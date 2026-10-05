"""Plugin API v1 über HTTP — und der Zustandsautomat der Übernahme.

Kein Hostmodul; läuft im Hintergrundfaden des Plugins. Es tut, was ``docs/api/plugin-api-v1.md`` vorschreibt und was
``CaptureTransfer.cpp`` im Archicad-Add-on tut:

1. Die Manifestbytes stehen **vor** der Anlage fest und liegen auf der Platte.
2. Der Vorgangsschlüssel (UUIDv7) wird gespeichert, **bevor** der erste Aufruf
   hinausgeht.
3. Session anlegen — nach ``expired``/``aborted`` mit neuem Schlüssel.
4. Je Datei ``begin`` → ``PUT`` → ``complete``; ``412`` auf den ``PUT`` heißt
   „liegt schon da", ``complete`` entscheidet. Nach ``rejected`` der nächste
   ``attempt``.
5. Manifest, ``finalize``, ``result.openUrl``.

Ein Abbruch an jeder Stelle ist gefahrlos: dieselbe Übernahme mit demselben
Schlüssel holt genau den fehlenden Rest nach.

**Nie im Protokoll:** Token, ``Authorization``, ``device_code``, ``user_code``,
Upload-Tickets, signierte URLs, Objektschlüssel (Abschnitt 5.6).
"""

from __future__ import annotations

import json
import os
import ssl
import threading
import urllib.error
import urllib.parse
import urllib.request

from . import current
from . import manifest as mf
from .log import log
from .store import TransferStore

TIMEOUT_SECONDS = 30
# Wie ``PROJECT_NAME_MAX_LENGTH`` der Plattform (``packages/contracts/src/project.ts``).
PROJECT_NAME_MAX_LENGTH = 120
UPLOAD_TIMEOUT_SECONDS = 300
MAX_KEY_ROTATIONS = 3
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class ApiError(Exception):
    """Ein Fehler, den der Nutzer lesen kann — mit Code für das Protokoll."""

    def __init__(self, message: str, *, status: int = 0, code: str = "", reason: str = "",
                 request_id: str = "", details: dict | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.reason = reason
        self.request_id = request_id
        self.details = details or {}


class Unauthorized(ApiError):
    """``401``: Token abgelaufen oder widerrufen — lokal löschen, neu verbinden."""


class Cancelled(Exception):
    pass


# --------------------------------------------------------------------------
# Serveradresse
# --------------------------------------------------------------------------


def normalize_server_url(raw: str) -> str:
    """Die Serveradresse, wie sie benutzt wird — oder ``ValueError`` mit Begründung.

    TLS ist Pflicht. Klartext nur für genau ``localhost``, ``127.0.0.1`` und
    ``::1`` (Entwicklung, Prüfstand). Keine Zugangsdaten, keine Abfrage, kein
    Fragment in der Adresse.
    """
    text = (raw or "").strip().rstrip("/")
    parsed = urllib.parse.urlsplit(text)
    if parsed.scheme not in ("https", "http") or not parsed.hostname:
        raise ValueError("Die Serveradresse muss mit https:// beginnen.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Die Serveradresse darf keine Zugangsdaten, Abfrage oder Anker enthalten.")
    if parsed.scheme == "http" and parsed.hostname not in _LOCAL_HOSTS:
        raise ValueError("Ohne https nur für localhost — die Verbindung wäre sonst unverschlüsselt.")
    return text


# Zertifikatslisten des Systems, falls der Python des Hosts keine eigene kennt
# (ein eingebettetes Python unter macOS findet die Schlüsselbundliste nicht).
_SYSTEM_CA_FILES = ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt")


def _ssl_context() -> ssl.SSLContext:
    """TLS mit Prüfung von Zertifikat und Name — nie abgeschaltet.

    Reihenfolge: ``certifi``, wenn der Host es mitbringt (Blender); sonst die
    Standardpfade des Pythons; kennt der keine Zertifizierungsstelle, die
    Liste des Systems (macOS ``/etc/ssl/cert.pem``).
    """
    context = ssl.create_default_context()
    try:
        import certifi

        context.load_verify_locations(certifi.where())
    except (ImportError, OSError):
        pass
    if not context.cert_store_stats().get("x509_ca"):
        for path in _SYSTEM_CA_FILES:
            if os.path.isfile(path):
                try:
                    context.load_verify_locations(path)
                    break
                except (OSError, ssl.SSLError):
                    continue
    return context


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------


class ApiClient:
    """Die Endpunkte der Plugin API v1 unter ``<server>/api/v1``."""

    def __init__(self, server_url: str, token: str | None = None, *, opener=None):
        self.server_url = normalize_server_url(server_url)
        self.token = token
        self._context = _ssl_context()
        self._opener = opener

    # -- Grundlagen -------------------------------------------------------

    def _open(self, request: urllib.request.Request, timeout: float):
        if self._opener is not None:
            return self._opener(request, timeout)
        return urllib.request.urlopen(request, timeout=timeout, context=self._context)

    def request(self, method: str, path: str, *, body=None, headers: dict | None = None,
                expected=(200, 201), auth: bool = True) -> dict:
        url = f"{self.server_url}/api/v1{path}"
        data = None
        all_headers = {"Accept": "application/json"}
        if body is not None:
            data = body if isinstance(body, (bytes, bytearray)) else json.dumps(body).encode("utf-8")
            all_headers["Content-Type"] = "application/json"
        if auth and self.token:
            all_headers["Authorization"] = f"Bearer {self.token}"
        all_headers.update(headers or {})
        request = urllib.request.Request(url, data=data, method=method, headers=all_headers)
        try:
            with self._open(request, TIMEOUT_SECONDS) as response:
                status = response.status
                raw = response.read()
                request_id = response.headers.get("x-request-id", "")
        except urllib.error.HTTPError as error:
            with error:  # die Antwort schließen, auch wenn sie ein Fehler ist
                status = error.code
                raw = error.read() or b""
                request_id = error.headers.get("x-request-id", "") if error.headers else ""
        except (urllib.error.URLError, OSError, TimeoutError) as error:
            raise ApiError("Der Server ist nicht erreichbar. Netzwerk und Serveradresse prüfen.",
                           code="network", reason=type(error).__name__) from None
        try:
            parsed = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError):
            parsed = {}
        if status in expected:
            return parsed if isinstance(parsed, dict) else {}
        error = parsed.get("error") if isinstance(parsed, dict) else None
        error = error if isinstance(error, dict) else {}
        details = error.get("details") if isinstance(error.get("details"), dict) else {}
        code = str(error.get("code") or "")
        reason = str(details.get("reason") or "")
        message = str(error.get("message") or f"Der Server antwortete {status}.")
        cls = Unauthorized if status == 401 else ApiError
        log(" ".join(part for part in (f"{method} {path.split('?')[0]} → {status}", code, reason,
                                         f"(X-Request-Id {request_id or '—'})") if part))
        raise cls(message, status=status, code=code, reason=reason, request_id=request_id, details=details)

    # -- Handshake und Anmeldung -----------------------------------------

    def handshake(self, host_version: str, plugin_version: str,
                  contract_version: str = mf.CONTRACT_VERSION) -> dict:
        query = urllib.parse.urlencode({
            "contract": mf.CONTRACT,
            "contractVersion": contract_version,
            "hostKey": current().key,
            "hostVersion": host_version,
            "pluginVersion": plugin_version,
        })
        return self.request("GET", f"/plugin/handshake?{query}", auth=False)

    def device_authorization(self, device: dict) -> dict:
        return self.request("POST", "/plugin/auth/device",
                            body={"client_id": current().client_id, "device": device}, auth=False)

    def device_token(self, device_code: str) -> dict:
        return self.request("POST", "/plugin/auth/device/token", auth=False, body={
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": device_code,
            "client_id": current().client_id,
        })

    def revoke(self, token: str) -> None:
        self.request("POST", "/plugin/auth/revoke", body={"token": token}, auth=False)

    def me(self) -> dict:
        return self.request("GET", "/plugin/me")

    # -- Ziel ---------------------------------------------------------------

    def projects(self) -> list[dict]:
        items: list[dict] = []
        cursor = None
        for _ in range(20):  # höchstens 2000 Projekte; mehr passt in keine Auswahlliste
            query = "limit=100" + (f"&cursor={urllib.parse.quote(cursor)}" if cursor else "")
            page = self.request("GET", f"/projects?{query}")
            items.extend(i for i in page.get("items", []) if isinstance(i, dict))
            cursor = page.get("nextCursor")
            if not cursor:
                break
        return items

    def create_project(self, name: str, key: str) -> dict:
        """``POST /projects`` — ein neues Projekt mit den Rechten des angemeldeten Nutzers (ADR 0022).

        ``key`` ist der Idempotenzschlüssel (UUIDv7): derselbe Schlüssel mit demselben Namen legt
        **ein** Projekt an, auch wenn die erste Antwort verloren ging (``NewProject``). Eine Rolle ohne
        Anlagerecht bekommt ``403``; der Satz sagt es, statt einen Statuscode zu nennen.
        """
        try:
            return self.request("POST", "/projects", body={"name": name}, headers={"Idempotency-Key": key})
        except Unauthorized:
            raise
        except ApiError as error:
            if error.status == 403:
                raise ApiError("Deine Rolle in diesem Büro erlaubt keine neuen Projekte. Ein Owner oder "
                               "Mitglied kann es anlegen.", status=403, code=error.code,
                               request_id=error.request_id) from None
            raise

    def viewpoints(self, project_id: str) -> list[dict]:
        workspace = self.request("GET", f"/projects/{urllib.parse.quote(project_id)}/workspace")
        return [v for v in workspace.get("viewpoints", []) if isinstance(v, dict)]

    def desired_outputs(self, project_id: str) -> dict[str, dict | None]:
        """Das Ausgabeziel je Blickpunkt — optional; scheitert es, bleibt die Auswahl möglich."""
        try:
            overview = self.request("GET", f"/projects/{urllib.parse.quote(project_id)}/viewpoint-overview")
        except ApiError:
            return {}
        return {
            v.get("viewpointId"): v.get("desired")
            for v in overview.get("viewpoints", [])
            if isinstance(v, dict) and v.get("viewpointId")
        }

    # -- Capture ------------------------------------------------------------

    def create_capture(self, key: str, body: dict) -> dict:
        return self.request("POST", "/plugin/captures", body=body, headers={"Idempotency-Key": key})

    def get_capture(self, capture_id: str) -> dict:
        return self.request("GET", f"/plugin/captures/{capture_id}")

    def file_action(self, capture_id: str, key: str, action: str, path: str, attempt: int) -> dict:
        return self.request("POST", f"/plugin/captures/{capture_id}/files",
                            body={"action": action, "path": path, "attempt": attempt},
                            headers={"Idempotency-Key": key})

    def submit_manifest(self, capture_id: str, key: str, data: bytes) -> dict:
        return self.request("POST", f"/plugin/captures/{capture_id}/manifest", body=bytes(data),
                            headers={"Idempotency-Key": key})

    def finalize(self, capture_id: str, key: str) -> dict:
        return self.request("POST", f"/plugin/captures/{capture_id}/finalize", body={},
                            headers={"Idempotency-Key": key})

    def abort(self, capture_id: str, key: str) -> dict:
        return self.request("POST", f"/plugin/captures/{capture_id}/abort", body={},
                            headers={"Idempotency-Key": key})

    def put_file(self, ticket: dict, path: str) -> int:
        """Der eine bedingte ``PUT`` an den Objektspeicher — ohne ``Authorization``."""
        with open(path, "rb") as handle:
            data = handle.read()
        headers = {str(k): str(v) for k, v in (ticket.get("requiredHeaders") or {}).items()}
        request = urllib.request.Request(ticket["uploadUrl"], data=data,
                                         method=ticket.get("method") or "PUT", headers=headers)
        try:
            with self._open(request, UPLOAD_TIMEOUT_SECONDS) as response:
                return response.status
        except urllib.error.HTTPError as error:
            error.close()
            if error.code == 412:
                return 412  # liegt schon da; ``complete`` prüft
            raise ApiError(f"Die Übertragung einer Datei scheiterte ({error.code}).",
                           status=error.code, code="upload_failed") from None
        except (urllib.error.URLError, OSError, TimeoutError) as error:
            raise ApiError("Die Übertragung einer Datei brach ab. Die Übernahme lässt sich fortsetzen.",
                           code="network", reason=type(error).__name__) from None


# --------------------------------------------------------------------------
# Projekt anlegen
# --------------------------------------------------------------------------


def project_name_problem(name: str) -> str | None:
    """Warum dieser Name kein Projektname ist — oder ``None``. Dieselben Grenzen wie die Plattform."""
    text = (name or "").strip()
    if not text:
        return "Bitte einen Namen für das neue Projekt eingeben."
    if len(text) > PROJECT_NAME_MAX_LENGTH:
        return f"Ein Projektname hat höchstens {PROJECT_NAME_MAX_LENGTH} Zeichen."
    return None


class NewProject:
    """Eine Projektanlage aus dem Plugin, gegen doppelte Anlage gesichert.

    Der Idempotenzschlüssel gehört zur **Absicht**, nicht zum Klick: wer nach einem Fehlschlag
    (Netz weg, Antwort verloren) denselben Namen noch einmal bestätigt, sendet denselben Schlüssel,
    und die Plattform liefert das Projekt, das sie schon angelegt hat. Ein anderer Name ist eine neue
    Absicht mit neuem Schlüssel. Nach dem Erfolg ist die Absicht erledigt.
    """

    def __init__(self):
        self._pending: tuple[str, str] | None = None

    def key_for(self, name: str) -> str:
        text = (name or "").strip()
        if self._pending is None or self._pending[0] != text:
            self._pending = (text, mf.uuid_v7())
        return self._pending[1]

    def done(self, name: str) -> None:
        if self._pending is not None and self._pending[0] == (name or "").strip():
            self._pending = None


def project_choices(projects: list[dict]) -> list[tuple[str, str]]:
    """``(id, Name)`` je Projekt für die Auswahlliste — ohne Namen steht die Kennung da."""
    return [(p["id"], p.get("name") or p["id"]) for p in projects if isinstance(p, dict) and p.get("id")]


# --------------------------------------------------------------------------
# Übernahme
# --------------------------------------------------------------------------


def target_body(target: dict) -> dict:
    """``target`` der Anlage aus dem gespeicherten Ziel.

    ``frame`` und ``size`` reisen nur, wo sie wirken (§7.2): bei ``create`` ist
    ``fit-to-capture`` implizit und wird nicht gesendet; ``size: capture`` bei
    ``update`` nur zusammen mit ``fit-to-capture``.
    """
    body: dict = {"projectId": target["projectId"]}
    viewpoint = target.get("viewpoint")
    if viewpoint:
        size = viewpoint.get("size", "canvas-default")
        if viewpoint["mode"] == "create":
            entry = {"mode": "create", "name": viewpoint["name"]}
            if size == "capture":
                entry["size"] = "capture"
        else:
            entry = {"mode": "update", "viewpointId": viewpoint["viewpointId"]}
            if viewpoint.get("frame") == "fit-to-capture":
                entry["frame"] = "fit-to-capture"
                if size == "capture":
                    entry["size"] = "capture"
        if viewpoint.get("baseImageRole"):
            entry["baseImageRole"] = viewpoint["baseImageRole"]
        body["viewpoint"] = entry
    return body


def _settled(file: dict) -> bool:
    return file.get("state") in ("verified", "deduplicated")


def _first_problem(session: dict) -> ApiError:
    for file in session.get("files", []):
        if file.get("state") == "rejected":
            error = file.get("error") or {}
            return ApiError(f"Der Server hat {file.get('path')} abgelehnt: {error.get('message', '')}",
                            code=str(error.get("code", "")))
    errors = (session.get("validation") or {}).get("errors") or []
    if errors:
        first = errors[0]
        pointer = f" ({first.get('pointer')})" if first.get("pointer") else ""
        return ApiError(f"{first.get('message', 'Abgelehnt')}{pointer}", code=str(first.get("code", "")))
    return ApiError(f"Die Übernahme steht auf „{session.get('state')}“.", code="capture_state")


def _needs_new_key(session: dict) -> bool:
    return session.get("state") in ("expired", "aborted")


def handshake_problem(handshake: dict) -> str | None:
    """Warum dieser Server nicht bedient wird — oder ``None``: Vertrag und Pluginfassung."""
    negotiation = handshake.get("negotiation") or {}
    if negotiation.get("result") not in (None, "supported"):
        return f"Der Server nimmt das Capture-Manifest {mf.CONTRACT_VERSION} nicht an ({negotiation.get('result')})."
    update = handshake.get("update") or {}
    if update.get("status") == "update_required":
        return update.get("message") or "Bitte das Plugin aktualisieren; diese Fassung nimmt der Server nicht mehr an."
    return None


def supports_model(handshake: dict | None) -> bool:
    """Setzt der Server Capture-Manifest 1.2.0 um? Aus ``highestSupportedVersion`` des Handshakes."""
    negotiation = (handshake or {}).get("negotiation") or {}
    highest = str(negotiation.get("highestSupportedVersion") or "")
    try:
        major, minor, _patch = (int(part) for part in highest.split("."))
    except ValueError:
        return False
    return major == 1 and minor >= int(mf.MODEL_CONTRACT_VERSION.split(".")[1])


class Transfer:
    """Führt eine vorbereitete Übernahme aus — oder setzt eine angefangene fort.

    ``pending`` ist der gespeicherte Vorgang (siehe ``prepare``); er liegt in
    ``TransferStore`` unter dem ``sourceProjectKey`` des Dokuments.
    """

    def __init__(self, api: ApiClient, store: TransferStore, document_key: str,
                 cancel: threading.Event | None = None, progress=None):
        self.api = api
        self.store = store
        self.document_key = document_key
        self.cancel = cancel or threading.Event()
        self.progress = progress or (lambda _text, _percent: None)

    def _check_cancel(self) -> None:
        if self.cancel.is_set():
            raise Cancelled()

    def _save(self, pending: dict) -> None:
        self.store.put_pending(self.document_key, pending)

    def run(self, pending: dict) -> dict:
        directory = pending["directory"]
        with open(os.path.join(directory, "capture-manifest.json"), "rb") as handle:
            manifest_bytes = handle.read()
        if mf.sha256_hex(manifest_bytes) != pending["manifestSha256"]:
            raise ApiError("Die gespeicherte Übernahme ist beschädigt. Bitte verwerfen.", code="local_state")
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        present = {a["path"]: a for a in manifest["assets"] if a["status"] == "present"}

        body = {
            "contract": manifest["contract"],
            "contractVersion": manifest["contractVersion"],
            "manifestSha256": pending["manifestSha256"],
            "files": [
                {k: a[k] for k in ("role", "path", "mediaType", "byteSize", "sha256")}
                for a in manifest["assets"] if a["status"] == "present"
            ],
            "target": target_body(pending["target"]),
        }

        version = manifest["contractVersion"]
        if version not in ("1.0.0", mf.CONTRACT_VERSION):
            # Ein MINOR-Feld erst schreiben, wenn die Gegenseite die MINOR umsetzt
            # (capture-manifest.md, §3, Regel 2) — vor der Anlage gefragt, auch beim Fortsetzen:
            # 1.2.0 (Modell) und 1.3.0 (PNG-Datenpässe).
            self.progress(f"Vertrag {version} prüfen", 2)
            source = manifest["source"]
            answer = self.api.handshake(source["host"]["version"], source["plugin"]["version"], version)
            result = (answer.get("negotiation") or {}).get("result")
            if result != "supported":
                raise ApiError(f"Der Server nimmt das Capture-Manifest {version} nicht an ({result}).",
                               code=str(result or "negotiation"))
        self.progress("Übernahme anmelden", 5)
        log(f"Vorgang {'fortgesetzt' if pending.get('captureId') else 'begonnen'}, Schlüssel {pending['idempotencyKey']}")
        session = self.api.create_capture(pending["idempotencyKey"], body)
        rotation = 0
        while _needs_new_key(session):
            if rotation >= MAX_KEY_ROTATIONS:
                raise ApiError("Der Server ließ die Übernahme mehrfach ablaufen. Bitte später erneut versuchen.",
                               code="capture_expired")
            rotation += 1
            log(f"Session {session.get('state')}; neuer Vorgangsschlüssel.")
            pending["idempotencyKey"] = mf.uuid_v7()
            pending["captureId"] = None
            self._save(pending)
            session = self.api.create_capture(pending["idempotencyKey"], body)

        capture_id = session["captureId"]
        if pending.get("captureId") != capture_id:
            pending["captureId"] = capture_id
            self._save(pending)
        key = pending["idempotencyKey"]
        log(f"Session {capture_id} · Zustand {session.get('state')}")

        problem = mf.check_limits(manifest, len(manifest_bytes), session.get("limits"))
        if problem:
            raise ApiError(problem, code="limit_exceeded")

        # -- Dateien -------------------------------------------------------
        for _round in range(4):
            self._check_cancel()
            files = session.get("files", [])
            if files and all(_settled(f) for f in files):
                break
            moved = False
            outstanding = max(len(files), 1)
            for index, file in enumerate(files):
                self._check_cancel()
                state = file.get("state")
                if _settled(file) or state == "received":
                    continue
                if state not in ("missing", "uploading", "rejected"):
                    continue
                asset = present.get(file.get("path"))
                if asset is None:
                    raise ApiError(f"Der Server erwartet eine Datei, die dieses Manifest nicht führt: {file.get('path')}",
                                   code="asset_unexpected")
                what = "Modell übertragen" if asset["role"] == mf.MODEL_ROLE else f"Bild übertragen: {asset['role']}"
                self.progress(what, 10 + (70 * index) // outstanding)
                attempt = file.get("attempt", 0) if state == "uploading" else file.get("attempt", 0) + 1
                begun = self.api.file_action(capture_id, key, "begin", asset["path"], attempt)
                if _settled(begun.get("file") or {}):
                    moved = True
                    continue
                ticket = begun.get("ticket")
                if not ticket:
                    break  # Session nimmt nichts mehr an; der Zustand entscheidet
                status = self.api.put_file(ticket, os.path.join(directory, *asset["path"].split("/")))
                log(f"{asset['role']}: PUT {status}, {asset['byteSize']} Bytes")
                completed = self.api.file_action(capture_id, key, "complete", asset["path"],
                                                 (begun.get("file") or {}).get("attempt", attempt))
                log(f"{asset['role']}: {(completed.get('file') or {}).get('state')}")
                moved = True
            session = self.api.get_capture(capture_id)
            if session.get("state") == "rejected":
                raise _first_problem(session)
            if session.get("state") in ("verified", "aborted", "expired"):
                break
            if not moved:
                break

        files = session.get("files", [])
        if not files or not all(_settled(f) for f in files):
            # Auch ein abgelehnter Versuch bleibt nicht hängen: die nächste
            # Übernahme beginnt diese Datei mit dem nächsten ``attempt``.
            raise _first_problem(session)

        # -- Manifest und Abschluss -----------------------------------------
        self._check_cancel()
        if (session.get("manifest") or {}).get("state") != "accepted":
            self.progress("Manifest übertragen", 85)
            session = self.api.submit_manifest(capture_id, key, manifest_bytes)
            if session.get("state") == "rejected":
                raise _first_problem(session)
        self._check_cancel()
        self.progress("Übernahme abschließen", 92)
        session = self.api.finalize(capture_id, key)
        if session.get("state") != "verified" or not session.get("result"):
            raise _first_problem(session)

        result = session["result"]
        target = pending["target"]
        viewpoint = target.get("viewpoint") or {}
        self.store.put_assignment(self.document_key, {
            "server": pending["server"],
            "projectId": target["projectId"],
            "viewpointId": result.get("viewpointId") or viewpoint.get("viewpointId"),
        })
        self.store.remove_pending(self.document_key)
        self.progress("Übernahme abgeschlossen", 100)
        log(f"Session {capture_id} · verified")
        return {"captureId": capture_id, "result": result}

    def discard(self, pending: dict) -> None:
        """Angefangene Übernahme verwerfen: ``abort`` beim Server, lokal löschen."""
        if pending.get("captureId") and pending.get("idempotencyKey"):
            try:
                self.api.abort(pending["captureId"], pending["idempotencyKey"])
            except ApiError as error:
                log(f"Abbruch der Session war nicht möglich: {error.code or error.status}")
        self.store.remove_pending(self.document_key)


def prepare(store: TransferStore, document_key: str, server: str, target: dict,
            manifest_bytes: bytes, directory: str) -> dict:
    """Den Vorgang anlegen und sichern — **bevor** ein Aufruf hinausgeht (§7.1, Schritt 5)."""
    path = os.path.join(directory, "capture-manifest.json")
    with open(path, "wb") as handle:
        handle.write(manifest_bytes)
    pending = {
        "idempotencyKey": mf.uuid_v7(),
        "captureId": None,
        "directory": directory,
        "manifestSha256": mf.sha256_hex(manifest_bytes),
        "target": target,
        "server": server,
        "createdAt": mf.timestamp_utc(),
    }
    store.put_pending(document_key, pending)
    return pending
