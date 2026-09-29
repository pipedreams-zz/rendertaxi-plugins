"""Was ein Plugin sich merkt — Dateien in seinem Nutzerordner, Modus 0600.

* **Anmeldung** (``credentials.json``): Token und ``deviceId`` je
  Serveradresse. **Nie** im Dokument des Hosts, nie in den Einstellungen,
  nie im Log. Abweichung von ``plugin-api-v1.md``, Abschnitt 5.5
  (Schlüsselspeicher des Systems) — begründet in ADR 0033, übernommen in
  ADR 0035.
* **Übernahmen** (``transfers.json`` und ``captures/<Schlüssel>/``): der
  Vorgangsschlüssel und die Dateien einer angefangenen Übernahme, damit sie
  einen Neustart überlebt; dazu der zuletzt gewählte Blickpunkt je Dokument.
* **Einstellungen** (``settings.json``) für Hosts ohne eigene
  Einstellungsablage (Cinema 4D): Serveradresse, Gerätename, Debug-Schalter.

**Ein gemerkter Zustand darf das Laden nie verhindern.** Jede Datei hier wird
so gelesen, dass eine kaputte, fremde oder fehlende Datei zum leeren Zustand
führt („nicht verbunden") statt zu einer Ausnahme beim Laden des Plugins.

Kein Modul des Hosts: der Ordner kommt als Argument.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time


def read_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, UnicodeDecodeError):
        return {}


def write_json_private(path: str, value: dict) -> None:
    """Atomar und nur für den Nutzer lesbar (0600) — auch in einem Zwischenschritt."""
    directory = os.path.dirname(path)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".tmp-", dir=directory)
    try:
        os.chmod(temporary, 0o600)
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(value, out, indent=2, sort_keys=True)
            out.write("\n")
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


class CredentialStore:
    """Token und ``deviceId`` je Serveradresse — Datei mit Modus 0600."""

    FILE = "credentials.json"

    def __init__(self, directory: str):
        self.path = os.path.join(directory, self.FILE)

    def _load(self) -> dict:
        data = read_json(self.path)
        servers = data.get("servers")
        return servers if isinstance(servers, dict) else {}

    def _save(self, servers: dict) -> None:
        write_json_private(self.path, {"version": 1, "servers": servers})

    def device_id(self, server: str) -> str | None:
        entry = self._load().get(server)
        value = entry.get("deviceId") if isinstance(entry, dict) else None
        return value if isinstance(value, str) and value else None

    def token(self, server: str) -> str | None:
        entry = self._load().get(server)
        value = entry.get("token") if isinstance(entry, dict) else None
        return value if isinstance(value, str) and value else None

    def remember_device(self, server: str, device_id: str) -> None:
        servers = self._load()
        entry = servers.get(server) if isinstance(servers.get(server), dict) else {}
        entry["deviceId"] = device_id
        servers[server] = entry
        self._save(servers)

    def store_token(self, server: str, token: str) -> None:
        servers = self._load()
        entry = servers.get(server) if isinstance(servers.get(server), dict) else {}
        entry["token"] = token
        servers[server] = entry
        self._save(servers)

    def forget_token(self, server: str) -> None:
        """Abmelden löscht das Token; die ``deviceId`` der Installation bleibt."""
        servers = self._load()
        entry = servers.get(server)
        if isinstance(entry, dict) and "token" in entry:
            del entry["token"]
            self._save(servers)
        elif not os.path.exists(self.path):
            return
        elif not servers:
            # Kaputte Datei: sie wird nicht länger gelesen, aber auch kein Token überleben.
            self._save({})


class TransferStore:
    """Angefangene Übernahmen und der zuletzt gewählte Blickpunkt je Dokument.

    Schlüssel ist der ``sourceProjectKey`` des Dokuments (``blender:<uuid>``,
    ``cinema-4d:<uuid>``): je Dokument höchstens eine angefangene Übernahme,
    wie im Archicad-Add-on je Ansicht.
    """

    FILE = "transfers.json"

    def __init__(self, directory: str):
        self.directory = directory
        self.path = os.path.join(directory, self.FILE)

    def _load(self) -> dict:
        data = read_json(self.path)
        for key in ("pending", "assignments"):
            if not isinstance(data.get(key), dict):
                data[key] = {}
        return data

    def _save(self, data: dict) -> None:
        write_json_private(self.path, {"version": 1, "pending": data["pending"], "assignments": data["assignments"]})

    def capture_dir(self, key: str) -> str:
        return os.path.join(self.directory, "captures", key)

    def pending(self, document_key: str) -> dict | None:
        entry = self._load()["pending"].get(document_key)
        if not isinstance(entry, dict):
            return None
        required = ("idempotencyKey", "directory", "manifestSha256", "target", "server")
        if not all(entry.get(name) for name in required):
            return None
        return entry

    def put_pending(self, document_key: str, entry: dict) -> None:
        data = self._load()
        data["pending"][document_key] = entry
        self._save(data)

    def remove_pending(self, document_key: str) -> None:
        data = self._load()
        entry = data["pending"].pop(document_key, None)
        self._save(data)
        directory = entry.get("directory") if isinstance(entry, dict) else None
        if directory and os.path.dirname(os.path.dirname(directory)) == self.directory:
            shutil.rmtree(directory, ignore_errors=True)

    def assignment(self, document_key: str) -> dict | None:
        entry = self._load()["assignments"].get(document_key)
        return entry if isinstance(entry, dict) else None

    def put_assignment(self, document_key: str, entry: dict) -> None:
        data = self._load()
        data["assignments"][document_key] = dict(entry, at=time.time())
        self._save(data)


class SettingsStore:
    """Einstellungen mit festen Vorgaben — ein fremder oder kaputter Wert fällt auf die Vorgabe.

    Nur für Hosts ohne eigene Einstellungsablage (Cinema 4D). Nimmt nur die
    Schlüssel der Vorgaben an, und nur mit deren Typ: ein Token hat hier
    keinen Platz.
    """

    FILE = "settings.json"

    def __init__(self, directory: str, defaults: dict):
        self.path = os.path.join(directory, self.FILE)
        self.defaults = dict(defaults)

    def load(self) -> dict:
        stored = read_json(self.path).get("settings")
        stored = stored if isinstance(stored, dict) else {}
        values = dict(self.defaults)
        for key, default in self.defaults.items():
            value = stored.get(key)
            if type(value) is type(default):
                values[key] = value
        return values

    def save(self, values: dict) -> dict:
        merged = self.load()
        for key, default in self.defaults.items():
            if key in values and type(values[key]) is type(default):
                merged[key] = values[key]
        write_json_private(self.path, {"version": 1, "settings": merged})
        return merged


def has_local_material(pending: dict) -> bool:
    """Liegen Manifest und Dateien einer angefangenen Übernahme noch vollständig vor?"""
    directory = pending.get("directory") or ""
    try:
        with open(os.path.join(directory, "capture-manifest.json"), "rb") as handle:
            raw = handle.read()
    except OSError:
        return False
    if hashlib.sha256(raw).hexdigest() != pending.get("manifestSha256"):
        return False
    try:
        manifest = json.loads(raw.decode("utf-8"))
        for asset in manifest["assets"]:
            if asset["status"] != "present":
                continue
            path = os.path.join(directory, *asset["path"].split("/"))
            if ".." in asset["path"].split("/") or os.path.getsize(path) != asset["byteSize"]:
                return False
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return True
