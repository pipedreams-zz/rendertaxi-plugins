"""Was das Fenster tut — ohne ``Rhino`` und ohne ``Eto``: Zustand, Hintergrundarbeit, Übernahme.

Das Fenster (``panel.py``) zeichnet nur und leitet Klicks hierher weiter; alles Hostspezifische erreicht
dieser Teil über einen Adapter (``rhinohost.py`` für Rhino, in den Tests ein Stub). Der Aufbau folgt dem
Cinema-4D-Plugin (``rendertaxi_c4d/controller.py``) und dem Blender-Panel: Verbindung, Projekt, Blickpunkt,
Bild, Modell, Übernehmen.

**Faden-Regel.** Netzaufrufe laufen in einem Hintergrundfaden (``Job``), damit Rhino bedienbar bleibt. Der
Faden fasst ``Rhino`` nie an: Ergebnisse reicht er über ``Job.post`` weiter; ``pump`` spielt sie im
UI-Faden ein (das Fenster ruft es aus seinem ``UITimer``). Aufnahme und Rendern laufen im UI-Faden: Rhino
Render ist aus einem Skript modal (QR-08, gemessen) — die Oberfläche zeichnet weiter, der Befehl wartet.

**Bild- und Modellweg** (``ways``, Vertrag 1.6.0, RTX-RH-003): nur Bild, nur Modell oder beides. Nur mit Modell
wird nicht aufgenommen: es gehen die GLB-Datei und die Kamera der gewählten Ansicht mit ihrer Bildgröße. Die Wahl
der Wege und „Materialfarben" sind gemerkt; ein fremder Wert fällt auf die Vorgabe.

**Ein gemerkter Zustand darf das Laden nie verhindern** (Regel 3). ``restore_session`` fängt jeden Fehler und
endet in „nicht verbunden"; kaputte Einstellungen fallen auf die Vorgaben, eine ungültige Bittiefe wird 8 Bit,
eine kaputte Ansichtszuordnung wird leer, eine gemerkte Wahl „nur Modell" gegen einen alten Server wird
„Bild und Modell" (``ways.plan``).
"""

from __future__ import annotations

import os
import queue
import re
import shutil
import threading
from dataclasses import dataclass, field

from . import host  # noqa: F401 — setzt den Host des Clients, bevor ihn jemand benutzt
from .rendertaxi_client import auth, frame, glb, ways
from .rendertaxi_client import manifest as mf
from .rendertaxi_client.log import log, log_exception, set_debug
from .rendertaxi_client.store import (CredentialStore, SettingsStore, TransferStore, has_local_material, read_json,
                                      write_json_private)
from .rendertaxi_client.transport import (ApiClient, ApiError, Cancelled, NewProject, Transfer, Unauthorized,
                                          handshake_problem, normalize_server_url, prepare, project_choices,
                                          project_name_problem, supports_model)

DEFAULT_SERVER_URL = "https://dev.rendertaxi.ai"
# ``dataPassBitDepth``: Bittiefe der Datenpässe (8 oder 16), Standard wie in Blender und Cinema 4D.
# ``sendImage``, ``sendModel``, ``modelColors``: die Wahl der Wege und der Materialfarben (wie Cinema 4D,
# RTX-C4D-008) — gemerkt, nie Bedingung fürs Laden.
DEFAULT_SETTINGS = {"serverUrl": DEFAULT_SERVER_URL, "deviceName": "", "debugLogging": False,
                    "dataPassBitDepth": mf.DEFAULT_DATA_PASS_BIT_DEPTH, "sendImage": True, "sendModel": False,
                    "modelColors": False}

VIEWPORT = "viewport"
BEAUTY = "beauty"
RESOLUTION_DOCUMENT = "document"
RESOLUTION_VIEWPOINT = "viewpoint"
CURRENT_VIEW = ""  # Quelle der Aufnahme: die aktive Ansicht; sonst die Kennung einer benannten Ansicht
FRAME_OPTION = "Rahmen an Aufnahme anpassen"
SIZE_OPTION = "Blickpunkt-Rahmen"
NEW_PROJECT_PROMPT = "Name des neuen Projekts"
VIEWPOINT_NAME_MAX_LENGTH = 200
PASS_ROLES = ("depth", "normal", "albedo", "object-id", "material-id")
# Die Dreiecksgrenze des Servers aus dem gemeinsamen Client; hier gelesen, damit ein Test sie senken kann.
MAX_TRIANGLES = glb.MAX_TRIANGLES
NO_MODEL_SERVER = "Dieser Server nimmt noch keine Modelle an; das Bild lässt sich ohne Modell übernehmen."
VIEWS_FILE = "views.json"


@dataclass
class Form:
    """Die Wahl im Fenster — Kennungen, nie Listenpositionen."""

    project_id: str | None = None
    target_mode: str = frame.CREATE
    viewpoint_name: str = ""
    # Der zuletzt vorgeschlagene Name (Name der Ansicht). Steht im Feld noch genau er, folgt das Feld der
    # Ansicht; hat der Nutzer etwas anderes eingegeben — auch ein leeres Feld —, bleibt seine Eingabe.
    suggested_viewpoint_name: str = ""
    viewpoint_id: str | None = None
    fit_to_capture: bool = False
    size: str = frame.SIZE_CANVAS_DEFAULT
    view_key: str = CURRENT_VIEW
    send_image: bool = True
    capture_kind: str = VIEWPORT
    resolution: str = RESOLUTION_DOCUMENT
    passes: set = field(default_factory=set)
    send_model: bool = False
    model_colors: bool = False


class State:
    def __init__(self):
        self.reset()

    def reset(self):
        self.server = ""
        self.connected = False
        self.identity: dict | None = None
        self.handshake: dict | None = None
        self.code: dict | None = None
        self.projects: list = []
        self.viewpoints: dict = {}
        self.desired: dict = {}
        self.message = ""
        self.error = ""
        self.progress: tuple | None = None
        self.result: dict | None = None
        self.job: Job | None = None
        self.deferred_viewpoints: str | None = None


class Job:
    """Ein Hintergrundfaden mit Abbruch und einem Rückweg in den UI-Faden."""

    def __init__(self, controller: "Controller", label: str, work):
        self.controller = controller
        self.label = label
        self.cancel = threading.Event()
        self.inbox: queue.Queue = queue.Queue()
        self.done = False
        self._work = work
        self.thread = threading.Thread(target=self._run, name=f"rdtxai-{label}", daemon=True)

    def post(self, callback) -> None:
        # Nur einreihen: das Fenster holt es mit seinem Zeitgeber ab (``pump``). Ein Wecken per
        # ``RhinoApp.InvokeOnUiThread`` wartet auf den UI-Faden und blockierte den Hintergrundfaden, solange
        # der UI-Faden selbst wartet (gemessen 08.10.2026).
        self.inbox.put(callback)

    def _run(self) -> None:
        controller = self.controller
        try:
            self._work(self)
        except Cancelled:
            self.post(lambda: controller._set_message("Abgebrochen."))
        except Unauthorized:
            self.post(controller._lost_authorization)
        except ApiError as error:
            text = str(error)
            self.post(lambda: controller._set_error(text))
        except Exception as error:  # nie still: ins Protokoll, und eine lesbare Zeile ins Fenster
            log_exception("Unerwarteter Fehler im Hintergrund", error)
            self.post(lambda: controller._set_error("Unerwarteter Fehler — Einzelheiten stehen im Protokoll."))
        finally:
            self.post(self._finish)

    def _finish(self) -> None:
        self.done = True
        state = self.controller.state
        if state.job is self:
            state.job = None
            state.progress = None
            state.code = None
        deferred, state.deferred_viewpoints = state.deferred_viewpoints, None
        if deferred:
            self.controller._request_viewpoints(deferred)


class Controller:
    """Zustand und Abläufe des Plugins; ``adapter`` ist die Brücke zu Rhino."""

    def __init__(self, adapter):
        self.adapter = adapter
        self.state = State()
        self.form = Form()
        self._jobs: list = []
        # „Projekt anlegen": derselbe Name nach einem Fehlschlag sendet denselben Idempotenzschlüssel.
        self.new_project_intent = NewProject()
        # Die letzte Zählung für „Modell senden" — nur ein Hinweis, gezählt auf Wunsch.
        self.model_estimate = None
        remembered = self.settings()
        self.form.send_image = remembered["sendImage"]
        self.form.send_model = remembered["sendModel"]
        self.form.model_colors = remembered["modelColors"]

    # -- Ablage und Einstellungen -------------------------------------------

    def _directory(self) -> str:
        return self.adapter.user_dir()

    def _stores(self) -> tuple:
        directory = self._directory()
        return CredentialStore(directory), TransferStore(directory)

    def settings(self) -> dict:
        """Die Einstellungen — kaputt oder unlesbar heißt: die Vorgaben."""
        try:
            return SettingsStore(self._directory(), DEFAULT_SETTINGS).load()
        except (OSError, ValueError):  # Ordner nicht anlegbar oder unzulässiger Pfad
            return dict(DEFAULT_SETTINGS)

    def save_settings(self, server_url: str, device_name: str, debug_logging: bool) -> bool:
        """Einstellungen sichern; eine unzulässige Adresse wird nicht gesichert, sondern erklärt."""
        try:
            server = normalize_server_url(server_url)
        except ValueError as error:
            self._set_error(str(error))
            return False
        changed_server = server != self.state.server
        SettingsStore(self._directory(), DEFAULT_SETTINGS).save(
            {"serverUrl": server, "deviceName": device_name.strip()[:64], "debugLogging": bool(debug_logging)})
        set_debug(debug_logging)
        if changed_server and self.state.job is None:
            self.restore_session()
            self._set_message("Einstellungen gesichert. Die Anmeldung gilt je Serveradresse.")
        else:
            self._set_message("Einstellungen gesichert.")
        return True

    def server_url(self) -> str:
        return normalize_server_url(self.settings()["serverUrl"])

    def data_pass_bit_depth(self) -> int:
        """Die gemerkte Bittiefe der Datenpässe — jeder ungültige Wert heißt 8 Bit (Regel 3)."""
        return mf.data_pass_bit_depth(self.settings().get("dataPassBitDepth"))

    def set_data_pass_bit_depth(self, value) -> int:
        bit_depth = mf.data_pass_bit_depth(value)
        try:
            SettingsStore(self._directory(), DEFAULT_SETTINGS).save({"dataPassBitDepth": bit_depth})
        except (OSError, ValueError):
            self._set_error("Die Bittiefe ließ sich nicht merken (Einstellungsordner nicht beschreibbar).")
        return bit_depth

    # -- Hintergrundarbeit --------------------------------------------------

    def busy(self) -> bool:
        return self.state.job is not None

    def start_job(self, label: str, work) -> bool:
        if self.state.job is not None:
            self._set_error("Es läuft schon ein Vorgang. Erst abwarten oder abbrechen.")
            return False
        job = Job(self, label, work)
        self.state.job = job
        self.state.error = ""
        self._jobs.append(job)
        job.thread.start()
        return True

    def pump(self) -> bool:
        """Ergebnisse der Hintergrundfäden im UI-Faden einspielen; ``True``, wenn sich etwas geändert hat."""
        handled = False
        for current in list(self._jobs):
            while True:
                try:
                    callback = current.inbox.get_nowait()
                except queue.Empty:
                    break
                handled = True
                try:
                    callback()
                except Exception as error:
                    log_exception("Fehler beim Übernehmen eines Ergebnisses", error)
            if current.done and current in self._jobs:
                self._jobs.remove(current)
        return handled

    def wait_idle(self, timeout: float = 30.0) -> None:
        """Nur für Tests und den Prüflauf: warten, bis kein Vorgang mehr läuft."""
        import time

        end = time.time() + timeout
        while self._jobs or self.state.job is not None:
            self.pump()
            if time.time() > end:
                raise TimeoutError("Vorgang läuft noch")
            time.sleep(0.02)

    def shutdown(self) -> None:
        if self.state.job is not None:
            self.state.job.cancel.set()

    def _set_message(self, text: str) -> None:
        self.state.message = text
        self.state.error = ""

    def _set_error(self, text: str) -> None:
        self.state.error = text

    def _lost_authorization(self) -> None:
        try:
            CredentialStore(self._directory()).forget_token(self.state.server)
        except OSError:
            pass
        self.state.connected = False
        self.state.identity = None
        self.state.error = "Die Anmeldung gilt nicht mehr (abgelaufen oder abgemeldet). Bitte neu verbinden."

    # -- Verbindung -----------------------------------------------------------

    def restore_session(self) -> None:
        """Beim Öffnen: vorhandenes Token prüfen. Jeder Fehler endet in „nicht verbunden"."""
        try:
            self.state.reset()
            settings = self.settings()
            set_debug(settings["debugLogging"])
            server = normalize_server_url(settings["serverUrl"])
            self.state.server = server
            token = CredentialStore(self._directory()).token(server)
            if token:
                self.start_job("restore", self._restore_work(server, token))
        except Exception as error:
            log_exception("Gespeicherter Zustand nicht lesbar; nicht verbunden.", error)
            self.state.reset()

    def _restore_work(self, server: str, token: str):
        host_version = self.adapter.host_version()

        def work(job: Job) -> None:
            api = ApiClient(server, token)
            handshake = api.handshake(host_version, mf.plugin_version())
            problem = handshake_problem(handshake)
            if problem:
                raise ApiError(problem)
            self._connected(job, api, handshake, auth.identity(api))

        return work

    def _connected(self, job: Job, api: ApiClient, handshake: dict, identity: dict) -> None:
        def apply():
            self.state.server = api.server_url
            self.state.connected = True
            self.state.identity = identity
            self.state.handshake = handshake
            self.state.message = ""

        job.post(apply)
        self._load_targets(job, api)

    def connect(self) -> bool:
        try:
            server = self.server_url()
        except ValueError as error:
            self._set_error(str(error))
            return False
        credentials, _ = self._stores()
        settings = self.settings()
        host_version = self.adapter.host_version()
        version = mf.plugin_version()

        def work(job: Job) -> None:
            api = ApiClient(server)
            handshake = api.handshake(host_version, version)
            problem = handshake_problem(handshake)
            if problem:
                raise ApiError(problem)
            description = auth.device(credentials, api.server_url, host_version, version, settings["deviceName"])

            def show(code: dict) -> None:
                def apply():
                    self.state.code = code
                    self.state.message = ""
                    self.adapter.open_url(code["verificationUriComplete"])

                job.post(apply)

            identity = auth.DeviceLogin(api, credentials, description, job.cancel).run(show)
            self._connected(job, api, handshake, identity)

        self.state.server = server
        return self.start_job("connect", work)

    def open_login(self) -> None:
        if self.state.code:
            self.adapter.open_url(self.state.code["verificationUriComplete"])

    def cancel(self) -> None:
        if self.state.job is not None:
            self.state.job.cancel.set()

    def sign_out(self) -> bool:
        """Abmelden mit Widerruf am Server; das Token wird lokal in jedem Fall gelöscht."""
        credentials, _ = self._stores()
        server = self.state.server or self.server_url()
        token = credentials.token(server)

        def work(job: Job) -> None:
            api = ApiClient(server, token)
            warning = auth.sign_out(api, credentials)

            def apply():
                self.state.reset()
                self.state.server = server
                self.state.message = "Abgemeldet."
                if warning:
                    self.state.error = warning

            job.post(apply)

        return self.start_job("sign-out", work)

    # -- Projekte und Blickpunkte -------------------------------------------

    def _load_targets(self, job: Job, api: ApiClient) -> None:
        projects = project_choices(api.projects())

        def apply():
            self.state.projects = projects
            if self.form.project_id and not any(pid == self.form.project_id for pid, _ in projects):
                # Ein verschwundenes Projekt wird nicht ersetzt — keine stille Zuordnung.
                self.form.project_id = None
            self._suggest_assignment()
            if self.form.project_id:
                self._request_viewpoints(self.form.project_id)

        job.post(apply)

    def refresh(self) -> bool:
        if not self.state.connected:
            return False
        server = self.state.server
        token = CredentialStore(self._directory()).token(server)
        project_id = self.form.project_id

        def work(job: Job) -> None:
            api = ApiClient(server, token)
            self._load_targets(job, api)
            if project_id:
                viewpoints = [(v["id"], v.get("name") or v["id"]) for v in api.viewpoints(project_id) if v.get("id")]
                desired = api.desired_outputs(project_id)
                job.post(lambda: self._apply_viewpoints(project_id, viewpoints, desired))

        return self.start_job("refresh", work)

    def new_project(self) -> bool:
        """„Projekt anlegen …": den Namen erfragen und das Projekt anlegen — „Abbrechen" tut nichts."""
        if self.state.job is not None:
            self._set_error("Es läuft schon ein Vorgang. Erst abwarten oder abbrechen.")
            return False
        if not self.state.connected:
            return False
        name = self.adapter.ask_text(NEW_PROJECT_PROMPT, "")
        return self.create_project(name) if name else False

    def create_project(self, name: str) -> bool:
        """Ein Projekt mit den Rechten des Nutzers anlegen und gleich als Ziel wählen.

        Neuer Blickpunkt ist dann das Ziel — im neuen Projekt gibt es noch keinen. Scheitert die Anlage, bleibt
        die bisherige Wahl; dieselbe Eingabe noch einmal legt kein zweites Projekt an.
        """
        problem = project_name_problem(name)
        if problem:
            self._set_error(problem)
            return False
        name = name.strip()
        key = self.new_project_intent.key_for(name)
        server = self.state.server
        token = CredentialStore(self._directory()).token(server)

        def work(job: Job) -> None:
            api = ApiClient(server, token)
            created = api.create_project(name, key)
            projects = project_choices(api.projects())
            project_id = created.get("id")
            if project_id and not any(pid == project_id for pid, _ in projects):
                projects.insert(0, (project_id, created.get("name") or name))

            def apply():
                self.new_project_intent.done(name)
                self.state.projects = projects
                if project_id:
                    self.form.project_id = project_id
                    self.form.viewpoint_id = None
                    self.form.target_mode = frame.CREATE
                    self.state.viewpoints[project_id] = []
                self._set_message(f"Projekt „{created.get('name') or name}“ angelegt und gewählt.")

            job.post(apply)

        return self.start_job("create-project", work)

    def select_project(self, project_id: str | None) -> None:
        self.form.project_id = project_id or None
        if self.form.viewpoint_id and not any(
                vid == self.form.viewpoint_id for vid, _ in self.state.viewpoints.get(project_id or "", [])):
            self.form.viewpoint_id = None
        if project_id:
            self._request_viewpoints(project_id)

    def _apply_viewpoints(self, project_id: str, viewpoints: list, desired: dict) -> None:
        self.state.viewpoints[project_id] = viewpoints
        self.state.desired.update(desired)
        if (self.form.project_id == project_id and self.form.viewpoint_id
                and not any(vid == self.form.viewpoint_id for vid, _ in viewpoints)):
            self.form.viewpoint_id = None

    def _request_viewpoints(self, project_id: str) -> None:
        """Blickpunkte und Ausgabeziele eines Projekts laden — nach dem laufenden Vorgang, falls einer läuft."""
        if not project_id or not self.state.connected:
            return
        if self.state.job is not None:
            self.state.deferred_viewpoints = project_id
            return
        server = self.state.server
        token = CredentialStore(self._directory()).token(server)

        def work(job: Job) -> None:
            api = ApiClient(server, token)
            viewpoints = [(v["id"], v.get("name") or v["id"]) for v in api.viewpoints(project_id) if v.get("id")]
            desired = api.desired_outputs(project_id)

            def apply():
                self._apply_viewpoints(project_id, viewpoints, desired)
                self._suggest_viewpoint(project_id)

            job.post(apply)

        self.start_job("viewpoints", work)

    # -- Ansicht als Quelle (QR-10) ------------------------------------------

    def views(self) -> list:
        """``(Kennung, Bezeichnung)`` — „Aktive Ansicht" und die benannten Ansichten; nie eine Ausnahme.

        Gibt es die gewählte benannte Ansicht nicht mehr (gelöscht, anderes Dokument), gilt wieder die aktive
        Ansicht — eine gemerkte Wahl verhindert keine Aufnahme (Regel 3).
        """
        try:
            found = list(self.adapter.views())
        except Exception as error:  # noqa: BLE001 — Regel 3: ohne Liste bleibt die aktive Ansicht
            log_exception("Ansichten nicht lesbar", error)
            found = [(CURRENT_VIEW, "Aktive Ansicht")]
        if self.form.view_key not in [key for key, _ in found]:
            self.form.view_key = CURRENT_VIEW
        return found

    def select_view(self, view_key: str) -> None:
        """Eine Ansicht als Quelle wählen; für eine benannte Ansicht wird ihr Blickpunkt vorgeschlagen."""
        keys = [key for key, _ in self.views()]
        self.form.view_key = view_key if view_key in keys else CURRENT_VIEW
        self.suggest_viewpoint_name()
        remembered = self._view_assignment(self.form.view_key)
        if not remembered:
            return
        if any(pid == remembered.get("projectId") for pid, _ in self.state.projects):
            if self.form.project_id != remembered["projectId"]:
                self.select_project(remembered["projectId"])
            if any(vid == remembered.get("viewpointId")
                   for vid, _ in self.state.viewpoints.get(remembered["projectId"], [])):
                self.form.viewpoint_id = remembered["viewpointId"]
                self.form.target_mode = frame.UPDATE

    def suggest_viewpoint_name(self) -> bool:
        """Den Namen der Ansicht als Namen eines neuen Blickpunkts vorschlagen — ``True``, wenn sich das Feld ändert.

        Was der Nutzer eingegeben hat, überschreibt der Vorschlag nie.
        """
        try:
            name = self.adapter.view_name(self.form.view_key)
        except Exception:  # noqa: BLE001 — ohne Dokument kein Vorschlag
            name = None
        suggestion = (name or "").strip()[:VIEWPOINT_NAME_MAX_LENGTH]
        form = self.form
        if form.viewpoint_name != form.suggested_viewpoint_name or suggestion == form.suggested_viewpoint_name:
            return False
        form.viewpoint_name = form.suggested_viewpoint_name = suggestion
        return True

    def _views_path(self) -> str:
        return os.path.join(self._directory(), VIEWS_FILE)

    def _view_assignment(self, view_key: str) -> dict | None:
        """Der zuletzt übernommene Blickpunkt einer benannten Ansicht dieses Dokuments — nur ein Vorschlag."""
        if not view_key:
            return None
        try:
            document = self.adapter.document_key(create=False)
            if not document:
                return None
            entry = read_json(self._views_path()).get(document, {}).get(view_key)
            if isinstance(entry, dict) and entry.get("server") == self.state.server:
                return entry
        except Exception:  # noqa: BLE001 — Regel 3: eine kaputte Zuordnung ist keine
            return None
        return None

    def _remember_view(self, document_key: str, view_key: str, project_id: str, viewpoint_id: str | None) -> None:
        if not view_key or not viewpoint_id:
            return
        try:
            data = read_json(self._views_path())
            views = data.get(document_key) if isinstance(data.get(document_key), dict) else {}
            views[view_key] = {"server": self.state.server, "projectId": project_id, "viewpointId": viewpoint_id}
            data[document_key] = views
            write_json_private(self._views_path(), data)
        except (OSError, ValueError) as error:
            log_exception("Ansichtszuordnung nicht gesichert", error)

    def _last_assignment(self) -> dict | None:
        try:
            key = self.adapter.document_key(create=False)
            if not key:
                return None
            _, transfers = self._stores()
            last = transfers.assignment(key)
            return last if last and last.get("server") == self.state.server else None
        except (OSError, TypeError, AttributeError, RuntimeError, ValueError):
            return None

    def _suggest_assignment(self) -> None:
        """Den zuletzt gewählten Blickpunkt des Dokuments **vorschlagen** — nie still übernehmen."""
        last = self._last_assignment()
        if last and not self.form.project_id and any(pid == last.get("projectId") for pid, _ in self.state.projects):
            self.form.project_id = last["projectId"]

    def _suggest_viewpoint(self, project_id: str) -> None:
        if self.form.viewpoint_id:
            return
        remembered = self._view_assignment(self.form.view_key) or self._last_assignment()
        if not remembered or remembered.get("projectId") != project_id:
            return
        if any(vid == remembered.get("viewpointId") for vid, _ in self.state.viewpoints.get(project_id, [])):
            self.form.viewpoint_id = remembered["viewpointId"]
            self.form.target_mode = frame.UPDATE

    # -- Anzeige ------------------------------------------------------------

    def document_size(self) -> tuple:
        return self.adapter.document_size()

    def capture_size(self) -> tuple | None:
        """``None`` heißt: Größe der Rendereinstellung des Dokuments."""
        if self.form.resolution != RESOLUTION_VIEWPOINT or self.form.target_mode != frame.UPDATE:
            return None
        return frame.viewpoint_size(self.state.desired.get(self.form.viewpoint_id or ""), self.document_size())

    def size_text(self) -> str:
        return frame.size_text(self.form.target_mode == frame.CREATE, self.form.fit_to_capture, self.form.size,
                               self.state.handshake)

    def aspect_hint(self) -> str | None:
        """Weicht bei „Rahmen behalten" das Ausgabeziel ab, sagt das Fenster es — es löst es nicht still auf."""
        if self.form.target_mode != frame.UPDATE or self.form.fit_to_capture:
            return None
        document = self.document_size()
        viewpoint_frame = frame.viewpoint_size(self.state.desired.get(self.form.viewpoint_id or ""), document)
        return frame.aspect_hint(viewpoint_frame, self.capture_size() or document,
                                 frame_option=FRAME_OPTION, size_option=SIZE_OPTION)

    def pending(self) -> dict | None:
        try:
            key = self.adapter.document_key(create=False)
            return TransferStore(self._directory()).pending(key) if key else None
        except (OSError, ValueError, RuntimeError):
            return None

    def selected_roles(self) -> list:
        if self.form.capture_kind != BEAUTY:
            return []
        return [role for role in PASS_ROLES if role in self.form.passes]

    def chosen(self) -> ways.Plan:
        """Was gesendet wird — ``ValueError`` (``ways.NOTHING_CHOSEN``), wenn nichts gewählt ist."""
        return ways.plan(self.form.send_image, self.form.send_model, self.state.handshake)

    # -- Modell --------------------------------------------------------------

    def _remember(self, values: dict) -> None:
        try:
            SettingsStore(self._directory(), DEFAULT_SETTINGS).save(values)
        except (OSError, ValueError):  # die Wahl gilt trotzdem, nur nicht über den Neustart hinaus
            pass

    def set_send_image(self, on: bool) -> None:
        self.form.send_image = bool(on)
        self._remember({"sendImage": self.form.send_image})

    def set_send_model(self, on: bool) -> None:
        self.form.send_model = bool(on)
        self._remember({"sendModel": self.form.send_model})
        if self.form.send_model:
            self.count_model()

    def set_model_colors(self, on: bool) -> None:
        self.form.model_colors = bool(on)
        self._remember({"modelColors": self.form.model_colors})

    def model_problem(self) -> str | None:
        """Warum das Modell nicht geht — im Fenster **vor** dem Senden, beim Übernehmen als Fehler."""
        if self.state.handshake is not None and not supports_model(self.state.handshake):
            return NO_MODEL_SERVER
        try:
            return self.adapter.model_problem()
        except Exception as error:  # noqa: BLE001 — kein Dokument: ein Satz, kein Absturz
            return str(error) or "Kein Rhino-Dokument geöffnet."

    def count_model(self) -> None:
        """Sichtbare Objekte und Dreiecke zählen — auf Wunsch, nicht bei jedem Zeichnen (Vernetzen kostet)."""
        self.model_estimate = None
        if self.model_problem():
            return
        try:
            self.model_estimate = self.adapter.model_estimate()
        except Exception as error:  # noqa: BLE001 — ohne Zählung bleibt der Hinweis „noch nicht gezählt"
            log_exception("Modellgröße nicht bestimmbar", error)

    def model_hint(self) -> str:
        """Größe, Grenzen und was nicht mitgeht — der Text im Bereich „Modell"."""
        if not self.form.send_model:
            return ""
        problem = self.model_problem()
        if problem:
            return problem
        parts = []
        estimate = self.model_estimate
        if estimate is None:
            parts.append("Größe noch nicht gezählt („Neu zählen“).")
        else:
            text = (f"≈ {estimate.triangles:,} Dreiecke in {estimate.objects} sichtbaren Objekten "
                    f"(höchstens {MAX_TRIANGLES:,}).").replace(",", " ")
            parts.append(("Zu groß: " if estimate.triangles > MAX_TRIANGLES else "") + text)
            if estimate.skipped:
                parts.append(f"{estimate.skipped} sichtbare Objekte ohne Fläche (Kurven, Punkte, Texte) gehen "
                             "nicht mit.")
            if estimate.clipping:
                parts.append("Schnittebenen wirken nicht auf das Modell; es geht ganz mit.")
        cap = ((self.state.handshake or {}).get("limits") or {}).get("maxGeometryBytes")
        if cap:
            parts.append(f"Modelldatei höchstens {cap / 1048576:.0f} MB.")
        named = [name for key, name in self.views() if key != CURRENT_VIEW]
        if named:
            parts.append(f"Kameras in der Datei: {len(named)} benannte Ansichten." if len(named) != 1
                         else "Kamera in der Datei: 1 benannte Ansicht.")
        return " ".join(parts)

    # -- Übernahme ------------------------------------------------------------

    def build_capture(self, directory: str) -> bytes:
        """Aufnehmen und/oder Modell exportieren, Manifest bauen und lokal prüfen — im UI-Faden.

        **Drei Fälle** (``ways.plan``): nur Bild, nur Modell, Bild und Modell. Nur mit Modell wird nicht
        aufgenommen: es gehen die GLB-Datei und die Kamera der gewählten Ansicht, deren Bildgröße die der
        Rendereinstellungen oder des Blickpunkt-Rahmens ist (``camera.resolution``, ab 1.6.0).

        Die Vertragsfassung kommt aus dem Handshake: ``mf.image_contract_version`` ohne, ``mf.model_contract_version``
        mit Modell. Ab 1.2.0 trägt das Manifest die Kamera der Ansicht (Exportraum), ab 1.4.0 mit Objektiv und
        Shift; ohne Modell ist ``geometry`` ``null``. Die Pässe tragen die gemerkte Bittiefe; ab 1.5.0 geht der
        Dateiname als ``source.fileName`` mit, nie ihr Ordner.
        """
        handshake = self.state.handshake or {}
        limits = handshake.get("limits") or {}
        chosen = self.chosen()  # nichts gewählt: ValueError mit dem Satz für das Fenster
        self.views()  # eine verschwundene benannte Ansicht wird zur aktiven
        size = self.capture_size()
        if chosen.model:
            problem = self.model_problem()
            if problem:
                raise ValueError(problem)
        contract_version = (mf.model_contract_version(handshake) if chosen.model
                            else mf.image_contract_version(handshake))
        capabilities = self.adapter.probe(model=chosen.model)
        files: list = []
        planned: list = []
        if chosen.image:
            files, planned, view_name, size_taken = self.adapter.render(
                self.form.capture_kind, directory, size, self.selected_roles(), limits.get("allowedMediaTypes"),
                self._progress_in_main, self.data_pass_bit_depth(), self.form.view_key)
        else:
            size_taken = size or self.document_size()
            view_name = self.adapter.view_name(self.form.view_key) or "Aktive Ansicht"
        camera = None
        if _camera_allowed(contract_version):
            try:
                camera = self.adapter.camera(size_taken, mf.camera_lens_allowed(contract_version), self.form.view_key)
            except Exception as error:  # noqa: BLE001 — ohne Kamera geht das Bild trotzdem
                log_exception("Kamera der Ansicht nicht lesbar; das Bild geht ohne Kamera", error)
        geometry = None
        if chosen.model:
            if chosen.model_only and camera is None:
                # Ohne Bild ist die Kamera die Aufnahme: ohne sie geht kein Modell allein (Pflicht ab 1.6.0).
                raise ValueError("Ohne Kamera geht das Modell nicht allein: bitte eine Modellansicht aktivieren "
                                 "(kein Layout) oder Bild und Modell senden.")
            self._progress_in_main("Modell exportieren", 70 if chosen.image else 10)
            model_file, geometry = self.adapter.export_model(directory, size_taken, limits, self.form.model_colors,
                                                             MAX_TRIANGLES)
            files.append(model_file)
            if camera is not None:
                # Die Bildgröße der Kamera; ``build_manifest`` schreibt sie erst ab 1.6.0.
                camera["resolution"] = {"width": int(size_taken[0]), "height": int(size_taken[1])}
        try:
            file_name = self.adapter.document_file_name()
        except Exception:  # noqa: BLE001 — ohne Dateinamen geht der Capture trotzdem
            file_name = None
        data = mf.ManifestInput(
            capture_id=mf.uuid_v7(),
            created_at=mf.timestamp_utc(),
            host_version=self.adapter.host_version(),
            plugin_version=mf.plugin_version(),
            machine=auth.machine(),
            capabilities=capabilities,
            platform_project_id=self.form.project_id,
            source_project_key=self.adapter.document_key(create=True),
            project_display_name=self.adapter.document_name(),
            view_display_name=view_name,
            files=files,
            planned=planned,
            camera=camera,
            geometry=geometry,
            source_file_name=file_name,
            contract_version=contract_version,
        )
        manifest = mf.build_manifest(data)
        problems = mf.validate_manifest(manifest)
        if problems:
            raise ValueError(mf.invalid_capture(problems))
        raw = mf.serialize(manifest)
        problem = mf.check_limits(manifest, len(raw), limits)
        if problem:
            raise ValueError(problem)
        return raw

    def _progress_in_main(self, text: str, percent: int) -> None:
        self.state.progress = (text, percent)
        self.adapter.status(text, percent)

    def capture(self) -> bool:
        """„Bild übernehmen": aufnehmen im UI-Faden, übertragen im Hintergrund."""
        if self.state.job is not None:
            self._set_error("Es läuft schon ein Vorgang.")
            return False
        if not self.state.connected:
            self._set_error("Nicht verbunden.")
            return False
        directory = None
        pending = None
        try:
            chosen = self.chosen()
            target = frame.target(self.form.project_id, self.form.target_mode, name=self.form.viewpoint_name,
                                  viewpoint_id=self.form.viewpoint_id, fit_to_capture=self.form.fit_to_capture,
                                  size=self.form.size)
            credentials, transfers = self._stores()
            key = self.adapter.document_key(create=True)
            if transfers.pending(key):
                raise ValueError("Hier läuft schon eine Übernahme. Fortsetzen oder verwerfen.")
            directory = transfers.capture_dir(mf.uuid_v7())
            os.makedirs(directory, mode=0o700, exist_ok=True)
            self.state.progress = (ways.steps(chosen)[0], 0)
            self.state.result = None
            raw = self.build_capture(directory)
            pending = prepare(transfers, key, self.state.server, target, raw, directory)
            if chosen.hint:
                self.state.message = chosen.hint
        except (ValueError, OSError, RuntimeError) as error:  # capture.CaptureError ist ein RuntimeError
            self.state.progress = None
            if pending is None and directory:
                shutil.rmtree(directory, ignore_errors=True)  # nichts angelegt: keine Reste
            self._set_error(str(error))
            return False
        finally:
            self.adapter.status("", -1)
        return self.start_job("transfer", self._transfer_work(self.state.server, credentials.token(self.state.server),
                                                              key, pending, self.form.view_key))

    def _transfer_work(self, server: str, token: str | None, document_key: str, pending: dict, view_key: str):
        directory = self._directory()  # im UI-Faden: der Nutzerordner kommt vom Adapter

        def work(job: Job) -> None:
            api = ApiClient(server, token)

            def progress(text: str, percent: int) -> None:
                job.post(lambda: setattr(self.state, "progress", (text, percent)))

            outcome = Transfer(api, TransferStore(directory), document_key, job.cancel, progress).run(pending)
            target = pending["target"]
            project_id = target["projectId"]
            update = (target.get("viewpoint") or {}).get("mode") == frame.UPDATE

            def apply():
                result = outcome.get("result") or {}
                self.state.result = {
                    "captureId": outcome["captureId"],
                    "openUrl": result.get("openUrl"),
                    "viewpointId": result.get("viewpointId"),
                }
                self.state.message = ways.result_text(result, update)
                self._remember_view(document_key, view_key, project_id, result.get("viewpointId"))

            job.post(apply)
            log(f"Übernahme {outcome['captureId']} abgeschlossen")
            # Neuer Blickpunkt: die Liste neu lesen, damit er gleich wählbar ist.
            viewpoints = [(v["id"], v.get("name") or v["id"]) for v in api.viewpoints(project_id) if v.get("id")]
            desired = api.desired_outputs(project_id)
            job.post(lambda: self._apply_viewpoints(project_id, viewpoints, desired))

        return work

    def resume(self) -> bool:
        key = self.adapter.document_key(create=False)
        credentials, transfers = self._stores()
        pending = transfers.pending(key) if key else None
        if not pending or not self.state.connected:
            return False
        if pending["server"] != self.state.server:
            self._set_error("Die angefangene Übernahme gehört zu einer anderen Serveradresse. Bitte verwerfen.")
            return False
        if not has_local_material(pending):
            self._set_error("Die Dateien der angefangenen Übernahme fehlen. Bitte verwerfen.")
            return False
        return self.start_job("transfer", self._transfer_work(self.state.server, credentials.token(self.state.server),
                                                              key, pending, CURRENT_VIEW))

    def discard(self) -> bool:
        key = self.adapter.document_key(create=False)
        credentials, transfers = self._stores()
        pending = transfers.pending(key) if key else None
        if not pending:
            return False
        server = pending["server"]
        token = credentials.token(server)

        def work(job: Job) -> None:
            Transfer(ApiClient(server, token), transfers, key).discard(pending)
            job.post(lambda: self._set_message("Angefangene Übernahme verworfen."))

        return self.start_job("discard", work)

    def open_result(self) -> None:
        if self.state.result and self.state.result.get("openUrl"):
            self.adapter.open_url(self.state.result["openUrl"])


def _camera_allowed(contract_version: str) -> bool:
    """``camera`` als Objekt erst ab 1.2.0 (ADR 0032); darunter ist der Block ``null``."""
    match = re.fullmatch(r"1\.([0-9]+)\.[0-9]+", str(contract_version))
    return match is not None and int(match.group(1)) >= 2
