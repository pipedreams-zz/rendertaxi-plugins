"""Was der Dialog tut — ohne ``c4d``: Zustand, Hintergrundarbeit, Übernahme.

Der Dialog (``dialog.py``) zeichnet nur und leitet Klicks hierher weiter; alles
Hostspezifische erreicht dieser Teil über einen ``Adapter`` (``c4dhost.py``
für Cinema 4D, in den Tests ein Stub). So ist der ganze Ablauf auf der VPS ohne
Cinema 4D prüfbar.

**Faden-Regel.** Netzaufrufe laufen in einem Hintergrundfaden (``Job``), damit
Cinema 4D bedienbar bleibt. Der Faden fasst ``c4d`` nie an: Ergebnisse reicht
er über ``Job.post`` weiter; ``pump`` spielt sie im Hauptfaden ein (der
Dialog ruft es aus seinem Timer). Aufnahme und Rendern laufen im Hauptfaden
und blockieren die Oberfläche für ihre Dauer, mit Fortschritt in der
Statusleiste (QC-07) — außer der Beauty mit einem anderen Renderer als dem
Viewport Renderer: sie rendert sichtbar im Picture Viewer, in einem Faden des
Hosts (``Rendering``, RTX-C4D-010), und ``pump`` fragt nach, wie weit sie ist.

**Ein gemerkter Zustand darf das Laden nie verhindern.** ``restore_session``
fängt jeden Fehler und endet in „nicht verbunden"; kaputte Einstellungen
fallen auf die Vorgaben — auch eine ungültige gemerkte Bittiefe der
Datenpässe: sie wird zum Standard 8 Bit (``mf.data_pass_bit_depth``).
"""

from __future__ import annotations

import os
import queue
import shutil
import threading
from dataclasses import dataclass, field

from . import host  # noqa: F401 — setzt den Host des Clients, bevor ihn jemand benutzt
from .rendertaxi_client import auth, frame, ways
from .rendertaxi_client import manifest as mf
from .rendertaxi_client.log import log_exception, set_debug
from .rendertaxi_client.store import CredentialStore, SettingsStore, TransferStore, has_local_material
from .rendertaxi_client.glb import MAX_TRIANGLES
from .rendertaxi_client.transport import (ApiClient, ApiError, Cancelled, NewProject, Transfer, Unauthorized,
                                          handshake_problem, normalize_server_url, prepare, project_choices,
                                          project_name_problem, supports_model)

DEFAULT_SERVER_URL = "https://dev.rendertaxi.ai"
# ``dataPassBitDepth``: Bittiefe der Datenpässe (8 oder 16), Standard wie in Blender (RTX-P-012).
# ``lastTab``, ``sendImage``, ``sendModel``: der zuletzt gewählte Tab und die Wahl der Wege (RTX-C4D-008) —
# gemerkt, nie Bedingung fürs Laden: ein fremder Wert fällt auf die Vorgabe.
# ``sendExtraCameras`` und ``extraCameras`` (RTX-C4D-010): der Schalter „Zusätzliche Kameras mitsenden" und je
# Dokumentkennung die gewählten Kameras (Schlüssel aus ``export.scene_cameras``) — eine gemerkte Kamera, die es nicht
# mehr gibt, verliert ihr Häkchen, nie ein Fehler.
DEFAULT_SETTINGS = {"serverUrl": DEFAULT_SERVER_URL, "deviceName": "", "debugLogging": False,
                    "dataPassBitDepth": mf.DEFAULT_DATA_PASS_BIT_DEPTH, "lastTab": "connection",
                    "sendImage": True, "sendModel": False, "sendExtraCameras": False, "extraCameras": {}}
# Höchstens so viele gemerkte Dokumente mit Kamerawahl; das älteste fällt heraus.
REMEMBERED_DOCUMENTS = 50
# Die Tabs des Fensters, in ihrer Reihenfolge (Nutzerentscheidung vom 05.10.2026).
TABS = ("connection", "project", "viewpoint", "image", "model")

VIEWPORT = "viewport"
BEAUTY = "beauty"
RESOLUTION_DOCUMENT = "document"
RESOLUTION_VIEWPOINT = "viewpoint"
FRAME_OPTION = "Rahmen an Aufnahme anpassen"
NEW_PROJECT_PROMPT = "Name des neuen Projekts"
VIEWPOINT_NAME_MAX_LENGTH = 200
SIZE_OPTION = "Blickpunkt-Rahmen"
# Die Beauty mit einem anderen Renderer als dem Viewport Renderer (RTX-C4D-010, Wortlaut des Nutzers vom 07.10.2026).
RENDER_LABEL = "Mit aktivem Renderer im Picture Viewer rendern"
TAKE_LABEL = "Gerenderte Bilder übernehmen"
CANCEL_RENDER_LABEL = "Rendern abbrechen"
RENDER_STEP = "Rendern im Picture Viewer"


@dataclass
class Form:
    """Die Wahl im Dialog — Kennungen, nie Listenpositionen."""

    project_id: str | None = None
    target_mode: str = frame.CREATE
    viewpoint_name: str = ""
    # Der zuletzt vorgeschlagene Name (Kameraname, RTX-P-013). Steht im Feld noch genau er, folgt das Feld
    # der Kamera; hat der Nutzer etwas anderes eingegeben — auch ein leeres Feld —, bleibt seine Eingabe.
    suggested_viewpoint_name: str = ""
    viewpoint_id: str | None = None
    fit_to_capture: bool = False
    size: str = frame.SIZE_CANVAS_DEFAULT
    capture_kind: str = VIEWPORT
    resolution: str = RESOLUTION_DOCUMENT
    passes: set[str] = field(default_factory=set)
    # Bildweg und Modellweg (RTX-P-014): einzeln oder zusammen; ``ways.plan`` entscheidet, was gesendet wird.
    send_image: bool = True
    send_model: bool = False
    # Kameras im GLB (RTX-C4D-010): die der Renderansicht immer; die gewählten nur mit dem Schalter.
    send_extra_cameras: bool = False
    extra_cameras: set[str] = field(default_factory=set)


class State:
    def __init__(self):
        self.reset()

    def reset(self):
        self.server = ""
        self.connected = False
        self.identity: dict | None = None
        self.handshake: dict | None = None
        self.code: dict | None = None
        self.projects: list[tuple[str, str]] = []
        self.viewpoints: dict[str, list[tuple[str, str]]] = {}
        self.desired: dict[str, dict | None] = {}
        self.message = ""
        self.error = ""
        self.progress: tuple[str, int] | None = None
        self.result: dict | None = None
        self.job: Job | None = None
        self.deferred_viewpoints: str | None = None


class Job:
    """Ein Hintergrundfaden mit Abbruch und einem Rückweg in den Hauptfaden."""

    def __init__(self, controller: "Controller", label: str, work):
        self.controller = controller
        self.label = label
        self.cancel = threading.Event()
        self.inbox: queue.Queue = queue.Queue()
        self.done = False
        self._work = work
        self.thread = threading.Thread(target=self._run, name=f"rendertaxi-{label}", daemon=True)

    def post(self, callback) -> None:
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
        except Exception as error:  # nie still: in die Konsole, und eine lesbare Zeile in den Dialog
            log_exception("Unerwarteter Fehler im Hintergrund", error)
            self.post(lambda: controller._set_error("Unerwarteter Fehler — Details in der Konsole (Debug-Schalter)."))
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
    """Zustand und Abläufe des Plugins; ``adapter`` ist die Brücke zum Host."""

    def __init__(self, adapter):
        self.adapter = adapter
        self.state = State()
        self.form = Form()
        self._jobs: list[Job] = []
        # Die letzte Zählung für „Modell mitsenden": (Aufnahmeart, Objekte, Dreiecke) — nur ein Hinweis.
        self.model_estimate: tuple[str, int, int] | None = None
        # „Neues Projekt …": derselbe Name nach einem Fehlschlag sendet denselben Idempotenzschlüssel.
        self.new_project_intent = NewProject()
        # Die gemerkte Wahl der Wege; ``settings`` fällt bei jedem Fehler auf die Vorgaben (Regel 3).
        remembered = self.settings()
        self.form.send_image = remembered["sendImage"]
        self.form.send_model = remembered["sendModel"]
        self.form.send_extra_cameras = remembered["sendExtraCameras"]
        # Das Rendern im Picture Viewer (``Rendering`` des Hosts): nur das jüngste, solange es zur Einstellung passt.
        self.rendering = None
        # Wem die Kamerawahl in ``form.extra_cameras`` gehört: das Dokument selbst und seine Kennung (F-01, #310).
        self._camera_document: str | None = None
        self._camera_owner = None
        # Die Wahl in Dokumenten ohne Kennung, solange sie offen sind: (Dokument, Schlüssel) — nur im Speicher.
        self._unkeyed_cameras: list = []

    # -- Ablage und Einstellungen -------------------------------------------

    def _directory(self) -> str:
        return self.adapter.user_dir()

    def _stores(self) -> tuple[CredentialStore, TransferStore]:
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
        """Die Wahl im Dialog sofort merken; ein ungültiger Wert wird zum Standard 8 Bit."""
        bit_depth = mf.data_pass_bit_depth(value)
        try:
            SettingsStore(self._directory(), DEFAULT_SETTINGS).save({"dataPassBitDepth": bit_depth})
        except (OSError, ValueError):  # nicht merkbar: es bleibt bei der gemerkten Bittiefe, der Dialog sagt es
            self._set_error("Die Bittiefe ließ sich nicht merken (Einstellungsordner nicht beschreibbar).")
        return bit_depth

    def last_tab(self) -> str:
        """Der zuletzt gewählte Tab — ein unbekannter Wert heißt „Verbindung" (Regel 3)."""
        tab = self.settings().get("lastTab")
        return tab if tab in TABS else TABS[0]

    def remember_tab(self, tab: str) -> None:
        """Den gewählten Tab merken; nicht merkbar ist kein Fehler — es bleibt beim Merken davor."""
        if tab not in TABS:
            return
        try:
            SettingsStore(self._directory(), DEFAULT_SETTINGS).save({"lastTab": tab})
        except (OSError, ValueError):
            pass

    def _remember_ways(self) -> None:
        try:
            SettingsStore(self._directory(), DEFAULT_SETTINGS).save(
                {"sendImage": bool(self.form.send_image), "sendModel": bool(self.form.send_model)})
        except (OSError, ValueError):  # die Wahl gilt trotzdem, nur nicht über den Neustart hinaus
            pass

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
        """Ergebnisse der Hintergrundfäden im Hauptfaden einspielen; ``True``, wenn sich etwas geändert hat."""
        handled = self._poll_rendering()
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
        self._drop_rendering()

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
        if self.rendering_running():
            self.rendering.cancel()

    def sign_out(self) -> bool:
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

                def apply():
                    self._apply_viewpoints(project_id, viewpoints, desired)

                job.post(apply)

        return self.start_job("refresh", work)

    def new_project(self) -> bool:
        """„Neues Projekt …": den Namen erfragen und das Projekt anlegen — „Abbrechen" tut nichts."""
        if self.state.job is not None:
            self._set_error("Es läuft schon ein Vorgang. Erst abwarten oder abbrechen.")
            return False
        if not self.state.connected:
            return False
        name = self.adapter.ask_text(NEW_PROJECT_PROMPT, "")
        return self.create_project(name) if name else False

    def create_project(self, name: str) -> bool:
        """Ein Projekt mit den Rechten des Nutzers anlegen und gleich als Ziel wählen (RTX-P-013).

        Neuer Blickpunkt ist dann das Ziel — im neuen Projekt gibt es noch keinen. Scheitert die
        Anlage, bleibt die bisherige Wahl; dieselbe Eingabe noch einmal legt kein zweites Projekt an.
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

    def suggest_viewpoint_name(self) -> bool:
        """Den Kameranamen als Namen eines neuen Blickpunkts vorschlagen — ``True``, wenn sich das Feld ändert.

        Ohne Kameraobjekt (Editor-Kamera, kein Dokument) gibt es keinen Vorschlag: das Feld bleibt, wie es
        heute ist. Was der Nutzer eingegeben hat, überschreibt der Vorschlag nie.
        """
        try:
            camera = self.adapter.camera_name()
        except (RuntimeError, AttributeError, TypeError):
            camera = None
        suggestion = (camera or "").strip()[:VIEWPOINT_NAME_MAX_LENGTH]
        form = self.form
        if form.viewpoint_name != form.suggested_viewpoint_name or suggestion == form.suggested_viewpoint_name:
            return False
        form.viewpoint_name = form.suggested_viewpoint_name = suggestion
        return True

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

    def _last_assignment(self) -> dict | None:
        try:
            key = self.adapter.document_key(create=False)
            if not key:
                return None
            _, transfers = self._stores()
            last = transfers.assignment(key)
            return last if last and last.get("server") == self.state.server else None
        except (OSError, TypeError, AttributeError, RuntimeError):
            return None

    def _suggest_assignment(self) -> None:
        """Den zuletzt gewählten Blickpunkt des Dokuments **vorschlagen** — nie still übernehmen."""
        last = self._last_assignment()
        if last and not self.form.project_id and any(pid == last.get("projectId") for pid, _ in self.state.projects):
            self.form.project_id = last["projectId"]

    def _suggest_viewpoint(self, project_id: str) -> None:
        last = self._last_assignment()
        if not last or last.get("projectId") != project_id or self.form.viewpoint_id:
            return
        if any(vid == last.get("viewpointId") for vid, _ in self.state.viewpoints.get(project_id, [])):
            self.form.viewpoint_id = last["viewpointId"]
            self.form.target_mode = frame.UPDATE

    # -- Anzeige ------------------------------------------------------------

    def document_size(self) -> tuple[int, int]:
        return self.adapter.document_size()

    def capture_size(self) -> tuple[int, int] | None:
        """``None`` heißt: Größe der Rendervoreinstellung des Dokuments."""
        if self.form.resolution != RESOLUTION_VIEWPOINT or self.form.target_mode != frame.UPDATE:
            return None
        return frame.viewpoint_size(self.state.desired.get(self.form.viewpoint_id or ""), self.document_size())

    def size_text(self) -> str:
        return frame.size_text(self.form.target_mode == frame.CREATE, self.form.fit_to_capture, self.form.size,
                               self.state.handshake)

    def aspect_hint(self) -> str | None:
        """Weicht bei „Rahmen behalten" das Ausgabeziel ab, sagt der Dialog es — er löst es nicht still auf."""
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

    def selected_roles(self) -> list[str]:
        if self.form.capture_kind != BEAUTY:
            return []
        return [role for role in ("depth", "normal", "albedo", "object-id", "material-id") if role in self.form.passes]

    # -- Modell --------------------------------------------------------------

    def model_problem(self) -> str | None:
        """Warum das Modell nicht geht — im Fenster **vor** dem Senden, beim Übernehmen als Fehler."""
        if self.state.handshake is not None and not supports_model(self.state.handshake):
            return "Dieser Server nimmt noch keine Modelle an; das Bild lässt sich ohne Modell übernehmen."
        return self.adapter.model_problem()

    def set_send_image(self, on: bool) -> None:
        self.form.send_image = bool(on)
        self._remember_ways()

    def set_send_model(self, on: bool) -> None:
        self.form.send_model = bool(on)
        self._remember_ways()
        if self.form.send_model:
            self.count_model()

    def chosen_ways(self) -> ways.Plan | None:
        """Was gesendet wird (``ways.plan``) — ``None``, wenn weder Bild noch Modell gewählt ist."""
        try:
            return ways.plan(self.form.send_image, self.form.send_model, self.state.handshake, self.selected_roles())
        except ValueError:
            return None

    def send_summary(self) -> str:
        """Der Satz über dem Knopf: was gesendet wird, warum anders als gewählt, oder warum nichts geht."""
        if not self.state.connected:
            return "Erst unter „Verbindung“ anmelden; dann lässt sich übernehmen."
        chosen = self.chosen_ways()
        if chosen is None:
            return ways.NOTHING_CHOSEN
        return " ".join(part for part in (ways.summary(chosen), chosen.hint, self.rendering_text()) if part)

    def send_label(self) -> str:
        """Die Beschriftung des Knopfs: „Bild übernehmen", „Modell übernehmen", „Bild und Modell übernehmen" —
        oder, wenn die Beauty im Picture Viewer rendert, „Mit aktivem Renderer im Picture Viewer rendern"."""
        chosen = self.chosen_ways()
        if chosen is None:
            return "Übernehmen"
        if self.picture_viewer_way():
            return CANCEL_RENDER_LABEL if self.rendering_running() else RENDER_LABEL
        return f"{ways.label(chosen)} übernehmen"

    # -- Rendern im Picture Viewer (RTX-C4D-010) -------------------------------

    def picture_viewer_way(self) -> bool:
        """Ob „Übernehmen" erst im Picture Viewer rendert: ein Bild mit der Beauty eines anderen Renderers als des
        Viewport Renderers — auch eines unbekannten (Regel 3). Ohne Dokument gilt der direkte Weg; er sagt dann, was fehlt."""
        chosen = self.chosen_ways()
        if chosen is None or not chosen.image or self.form.capture_kind != BEAUTY:
            return False
        try:
            return not self.adapter.direct_beauty(self.form.capture_kind)
        except (RuntimeError, AttributeError, TypeError):
            return False

    def rendering_running(self) -> bool:
        return self.rendering is not None and self.rendering.state == "rendering"

    def _drop_rendering(self) -> None:
        if self.rendering is not None:
            try:
                self.rendering.cancel()
                self.rendering.release()
            except Exception as error:  # noqa: BLE001 — freigeben darf nie das Fenster stören
                log_exception("Rendering nicht freigegeben", error)
            self.rendering = None

    def _poll_rendering(self) -> bool:
        """Den Fortschritt des Renderns im Hauptfaden lesen; ``True``, wenn sich etwas geändert hat."""
        if not self.rendering_running():
            return False
        before = self.state.progress
        state, percent = self.rendering.poll()
        if state == "rendering":
            self.state.progress = (RENDER_STEP, percent)
            self.adapter.status(RENDER_STEP, percent)
            return self.state.progress != before
        self.state.progress = None
        self.adapter.status("", -1)
        if state == "ready":
            self._set_message(f"Gerendert. Jetzt „{TAKE_LABEL}“.")
        else:
            self._set_error(self.rendering.problem or "Rendern abgebrochen.")
        return True

    def rendered_problem(self) -> str | None:
        """Warum das Gerenderte nicht (mehr) übernommen werden kann — ``None``, wenn es geht."""
        rendering = self.rendering
        if rendering is None or rendering.state != "ready":
            return f"Erst „{RENDER_LABEL}“."
        try:
            size = self.capture_size() or self.document_size()
            return self.adapter.rendering_stale(rendering, size, self.selected_roles())
        except RuntimeError as error:  # kein Dokument
            return str(error)

    def can_take_rendered(self) -> bool:
        return (self.state.connected and self.state.job is None and self.picture_viewer_way()
                and self.rendered_problem() is None)

    def rendering_text(self) -> str:
        """Der Satz zum Rendern im Picture Viewer im Bereich „Übernehmen“ — leer auf dem direkten Weg."""
        if not self.picture_viewer_way():
            return ""
        rendering = self.rendering
        if rendering is not None and rendering.state == "rendering":
            return "Rendert im Picture Viewer; Cinema 4D bleibt bedienbar."
        if rendering is not None and rendering.state == "ready":
            problem = self.rendered_problem()
            if problem:
                return problem
            width, height = rendering.size
            return f"Gerendert ({self.adapter.renderer_label(rendering.engine)}, {width} × {height}) — bereit zum Übernehmen."
        return f"Erst im Picture Viewer rendern, dann „{TAKE_LABEL}“."

    def start_rendering(self) -> bool:
        """„Mit aktivem Renderer im Picture Viewer rendern“: sichtbar, im Faden; ersetzt ein älteres Ergebnis."""
        if self.rendering_running():
            self.rendering.cancel()
            return True
        if self.state.job is not None:
            self._set_error("Es läuft schon ein Vorgang.")
            return False
        if not self.state.connected:
            self._set_error("Nicht verbunden.")
            return False
        self._drop_rendering()
        try:
            size = self.capture_size() or self.document_size()
            self.rendering = self.adapter.start_rendering(size, self.selected_roles())
        except (ValueError, OSError, RuntimeError) as error:  # capture.CaptureError ist ein RuntimeError
            self.rendering = None
            self._set_error(str(error))
            return False
        self.state.progress = (RENDER_STEP, 0)
        self._set_message("Rendert im Picture Viewer …")
        return True

    # -- Kameras im Modell (RTX-C4D-010) ---------------------------------------

    def _remembered_cameras(self, key: str | None) -> set[str]:
        stored = self.settings().get("extraCameras") or {}
        values = stored.get(key) if key else None
        return {value for value in values if isinstance(value, str)} if isinstance(values, list) else set()

    def camera_rows(self) -> list[dict]:
        """Die Kameras der Szene für den Tab „Modell“: ``key``, ``label``, ``kind``, ``render``, ``checked``.

        Die gemerkte Wahl gilt je Dokument; was es nicht mehr gibt, fällt still heraus (Regel 3).
        """
        try:
            rows = self.adapter.scene_cameras()
            self._follow_camera_document()
        except (RuntimeError, AttributeError, TypeError):
            return []
        present = {row["key"] for row in rows}
        self.form.extra_cameras &= present
        return [dict(row, checked=row["render"] or row["key"] in self.form.extra_cameras) for row in rows]

    def _follow_camera_document(self) -> None:
        """**Die eine Stelle** dafür, wem die Kamerawahl gehört: dem aktiven Dokument — Objekt **und** Kennung.

        Ein anderes Dokument bringt seine eigene Wahl mit, auch wenn beide noch keine Kennung haben: ohne Kennung steht
        sie nur im Speicher, je offenem Dokument (``_unkeyed_cameras``). Bekommt **dasselbe** Dokument eine Kennung
        (erste Übernahme, neue Kennung für eine Kopie), bleibt die Wahl, die der Nutzer sieht, und wird unter der
        Kennung gemerkt (Review F-01, #310).
        """
        document = self.adapter.document()
        key = self.adapter.document_key(create=False)
        if self.adapter.same_document(self._camera_owner, document):
            if key != self._camera_document:
                self._camera_document = key
                self._remember_cameras()
            return
        same = self.adapter.same_document
        if self._camera_owner is not None and self._camera_document is None:
            self._unkeyed_cameras.append((self._camera_owner, set(self.form.extra_cameras)))  # bis zur Rückkehr
        held = next((chosen for doc, chosen in self._unkeyed_cameras if same(doc, document)), None)
        # Was zum neuen Dokument gehört, ist jetzt in ``form``; geschlossene Dokumente fallen heraus (``same`` → False).
        self._unkeyed_cameras = [(doc, chosen) for doc, chosen in self._unkeyed_cameras
                                 if not same(doc, document) and same(doc, doc)]
        self._camera_owner = document
        self._camera_document = key
        if key is None:
            self.form.extra_cameras = set(held or ())
        else:
            self.form.extra_cameras = self._remembered_cameras(key)

    def set_extra_camera(self, key: str, on: bool) -> None:
        try:
            self._follow_camera_document()
        except (RuntimeError, AttributeError, TypeError):  # kein Dokument: die Wahl gilt nur hier
            pass
        if on:
            self.form.extra_cameras.add(key)
        else:
            self.form.extra_cameras.discard(key)
        self._remember_cameras()

    def _remember_cameras(self) -> None:
        """Die Wahl unter der Kennung des Dokuments merken — ohne Kennung gilt sie bis zur ersten Übernahme."""
        try:
            document = self._camera_document
            if not document:
                return
            store = SettingsStore(self._directory(), DEFAULT_SETTINGS)
            remembered = dict(store.load().get("extraCameras") or {})
            remembered.pop(document, None)
            remembered[document] = sorted(self.form.extra_cameras)
            while len(remembered) > REMEMBERED_DOCUMENTS:
                remembered.pop(next(iter(remembered)))
            store.save({"extraCameras": remembered})
        except (OSError, ValueError, RuntimeError):  # die Wahl gilt trotzdem, nur nicht über den Neustart hinaus
            pass

    def set_send_extra_cameras(self, on: bool) -> None:
        self.form.send_extra_cameras = bool(on)
        try:
            SettingsStore(self._directory(), DEFAULT_SETTINGS).save({"sendExtraCameras": bool(on)})
        except (OSError, ValueError):
            pass

    def chosen_cameras(self) -> list[str]:
        """Die Schlüssel der zusätzlichen Kameras, die mitgehen — leer ohne den Schalter."""
        if not self.form.send_extra_cameras:
            return []
        rows = self.camera_rows()
        return [row["key"] for row in rows if row["checked"] and not row["render"]]

    def count_model(self) -> None:
        """Sichtbare Objekte und Dreiecke zählen — auf Wunsch, nicht bei jedem Zeichnen (Polygonize kostet)."""
        self.model_estimate = None
        if self.model_problem():
            return
        try:
            counted = self.adapter.model_estimate(self.form.capture_kind)
        except (RuntimeError, OSError) as error:  # export.ExportError ist ein RuntimeError
            log_exception("Modellgröße nicht bestimmbar", error)
            return
        self.model_estimate = (self.form.capture_kind, counted.objects, counted.triangles)

    def model_hint(self) -> str:
        """Größe, Grenzen und was nicht mitgeht — der Text im Tab „Modell"."""
        if not self.form.send_model:
            return ""
        problem = self.model_problem()
        if problem:
            return problem
        parts = []
        estimate = self.model_estimate
        if estimate is None or estimate[0] != self.form.capture_kind:
            parts.append("Größe noch nicht gezählt („Neu zählen“).")
        else:
            _kind, objects, triangles = estimate
            text = f"≈ {triangles:,} Dreiecke in {objects} sichtbaren Objekten (höchstens {MAX_TRIANGLES:,})."
            parts.append(("Zu groß: " if triangles > MAX_TRIANGLES else "") + text.replace(",", " "))
        cap = ((self.state.handshake or {}).get("limits") or {}).get("maxGeometryBytes")
        if cap:
            parts.append(f"Modelldatei höchstens {cap / 1048576:.0f} MB.")
        try:
            why = self.adapter.camera_problem(self.capture_size() or self.document_size(),
                                              mf.camera_lens_allowed(mf.model_contract_version(self.state.handshake)))
        except RuntimeError as error:  # kein Dokument
            why = str(error)
        if why:
            parts.append(f"Ohne Kamera: {why}")
        return " ".join(parts)

    # -- Übernahme ------------------------------------------------------------

    def build_capture(self, directory: str, rendered=None) -> bytes:
        """Aufnehmen, Manifest bauen und lokal prüfen — im Hauptfaden.

        ``rendered``: das Ergebnis des Renderns im Picture Viewer — dann wird nicht noch einmal gerendert.

        **Drei Fälle** (RTX-P-014, ``ways.plan``): nur Bild, nur Modell, Bild und Modell. Nur mit Modell wird
        **nicht gerendert**: es gehen die GLB und die Kamera der Renderansicht, deren Bildgröße die der
        Rendervoreinstellung ist (``camera.resolution``, ab Capture-Manifest 1.6.0). Gegen einen Server unter
        1.6.0 fällt „nur Modell" mit Hinweis auf „Bild und Modell" zurück.

        Die Vertragsfassung kommt aus dem Handshake (``mf.image_contract_version`` bzw.
        ``mf.model_contract_version``); ohne Modell sind ``camera`` und ``geometry`` ``null``. Die Pässe tragen die
        gemerkte Bittiefe (8 oder 16 Bit). Gegen einen Server mit 1.5.0 geht der Name der gespeicherten Datei als
        ``source.fileName`` mit (RTX-P-013), nie ihr Ordner.
        """
        handshake = self.state.handshake or {}
        limits = handshake.get("limits") or {}
        chosen = ways.plan(self.form.send_image, self.form.send_model, self.state.handshake, self.selected_roles())
        size = self.capture_size()
        contract_version = mf.image_contract_version(handshake)
        if chosen.model:
            problem = self.model_problem()
            if problem:
                raise ValueError(problem)
        if chosen.model_only:
            # Ohne Bild ist die Kamera die Aufnahme: ohne sie geht kein Modell allein (Pflicht ab 1.6.0).
            why = self.adapter.camera_problem(self.document_size(),
                                              mf.camera_lens_allowed(mf.model_contract_version(handshake)))
            if why:
                raise ValueError(f"Ohne Kamera geht das Modell nicht allein: {why}")
        capabilities = self.adapter.probe(model=chosen.model)
        files: list = []
        planned: list = []
        view_name = None
        if chosen.image:
            files, planned, view_name = self.adapter.render(
                self.form.capture_kind, directory, size, self.selected_roles(), limits.get("allowedMediaTypes"),
                self._progress_in_main, self.data_pass_bit_depth(), rendered)
        camera = geometry = None
        if chosen.model:
            if chosen.image:
                width, height = files[0].image["width"], files[0].image["height"]
            else:
                width, height = self.document_size()
            contract_version = mf.model_contract_version(handshake)
            model, geometry, camera = self.adapter.export_model(
                self.form.capture_kind, directory, (width, height), self._progress_in_main,
                mf.camera_lens_allowed(contract_version), self.chosen_cameras())
            if camera is not None:
                # Die Bildgröße der Kamera; ``build_manifest`` schreibt sie erst ab 1.6.0.
                camera["resolution"] = {"width": int(width), "height": int(height)}
            files.append(model)
            if chosen.model_only:
                view_name = self.adapter.camera_name() or "Renderansicht"
        try:
            file_name = self.adapter.document_file_name()
        except (RuntimeError, AttributeError, TypeError):  # ohne Dateinamen geht der Capture trotzdem
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
        """„Übernehmen": Bild und/oder Modell im Hauptfaden, übertragen im Hintergrund.

        Rendert die Beauty im Picture Viewer, startet der Knopf dieses Rendern (``start_rendering``); übernommen wird
        dann mit „Gerenderte Bilder übernehmen“ (``take_rendered``).
        """
        if self.picture_viewer_way():
            return self.start_rendering()
        return self._capture(None)

    def take_rendered(self) -> bool:
        """„Gerenderte Bilder übernehmen“: Beauty und Ebenen aus dem Bildspeicher des Renderns, sonst wie „Übernehmen“."""
        problem = self.rendered_problem()
        if problem:
            self._set_error(problem)
            return False
        return self._capture(self.rendering)

    def _capture(self, rendered) -> bool:
        if self.state.job is not None:
            self._set_error("Es läuft schon ein Vorgang.")
            return False
        if not self.state.connected:
            self._set_error("Nicht verbunden.")
            return False
        directory = None
        pending = None
        try:
            target = frame.target(self.form.project_id, self.form.target_mode, name=self.form.viewpoint_name,
                                  viewpoint_id=self.form.viewpoint_id, fit_to_capture=self.form.fit_to_capture,
                                  size=self.form.size)
            credentials, transfers = self._stores()
            key = self.adapter.document_key(create=True)
            self._follow_camera_document()
            if transfers.pending(key):
                raise ValueError("Hier läuft schon eine Übernahme. Fortsetzen oder verwerfen.")
            chosen = ways.plan(self.form.send_image, self.form.send_model, self.state.handshake, self.selected_roles())
            directory = transfers.capture_dir(mf.uuid_v7())
            os.makedirs(directory, mode=0o700, exist_ok=True)
            self.state.progress = (f"{ways.steps(chosen)[0]} …", 0)
            self.state.result = None
            raw = self.build_capture(directory, rendered)
            pending = prepare(transfers, key, self.state.server, target, raw, directory)
        except (ValueError, OSError, RuntimeError) as error:  # capture.CaptureError ist ein RuntimeError
            self.state.progress = None
            if pending is None and directory:
                shutil.rmtree(directory, ignore_errors=True)  # nichts angelegt: keine Reste
            self._set_error(str(error))
            return False
        finally:
            self.adapter.status("", -1)
        self.state.message = chosen.hint or ""
        return self.start_job("transfer", self._transfer_work(self.state.server, credentials.token(self.state.server),
                                                              key, pending))

    def _transfer_work(self, server: str, token: str | None, document_key: str, pending: dict):
        directory = self._directory()  # im Hauptfaden: der Nutzerordner kommt aus c4d

        def work(job: Job) -> None:
            api = ApiClient(server, token)

            def progress(text: str, percent: int) -> None:
                job.post(lambda: setattr(self.state, "progress", (text, percent)))

            outcome = Transfer(api, TransferStore(directory), document_key, job.cancel, progress).run(pending)
            project_id = pending["target"]["projectId"]

            def apply():
                self.state.result = {
                    "captureId": outcome["captureId"],
                    "openUrl": outcome["result"].get("openUrl"),
                    "viewpointId": outcome["result"].get("viewpointId"),
                }
                # Was angekommen ist, aus dem Abschlussbeleg je Weg — und was am Blickpunkt stehen blieb.
                update = (pending["target"].get("viewpoint") or {}).get("mode") == frame.UPDATE
                self.state.message = ways.result_text(outcome["result"], update)

            job.post(apply)
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
                                                              key, pending))

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


def wrap(text: str, width: int = 60, lines: int = 4) -> list[str]:
    """Text in höchstens ``lines`` Zeilen zu ``width`` Zeichen — statische Texte brechen nicht selbst um."""
    words, result, line = (text or "").split(), [], ""
    for word in words:
        if line and len(line) + 1 + len(word) > width:
            result.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        result.append(line)
    if len(result) > lines:
        result = result[:lines]
        result[-1] = result[-1][: max(0, width - 1)] + "…"
    return result + [""] * (lines - len(result))
