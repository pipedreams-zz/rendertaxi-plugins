"""N-Panel „rendertaxi" — Verbinden, Ziel wählen, Aufnehmen, Übernehmen.

Vier Bereiche, wie die Palette des Archicad-Add-ons: **Verbindung**,
**Projekt und Blickpunkt**, **Bild übernehmen** (mit „Modell mitsenden",
RTX-B-003), **Im Browser öffnen**. Generierung und Ergebnisbearbeitung
bleiben in der Webanwendung.

**Faden-Regel.** Netzaufrufe laufen in einem Hintergrundfaden (``Job``), damit
Blender bedienbar bleibt. Der Faden fasst ``bpy`` nie an: was er an
Ergebnissen hat, reicht er über ``Job.post`` an den Hauptfaden weiter, den ein
``bpy.app.timers``-Takt abarbeitet. Aufnahme und Rendering laufen im
Hauptfaden (``bpy.ops`` verlangt es).

**Ein gemerkter Zustand darf das Laden nie verhindern.** Die Wiederaufnahme
der Anmeldung beim Start fängt jeden Fehler und endet dann in „nicht
verbunden".
"""

from __future__ import annotations

import os
import queue
import shutil
import threading
import zlib

import bpy
from bpy.props import BoolProperty, EnumProperty, PointerProperty, StringProperty
from bpy.types import Operator, Panel, PropertyGroup

from . import capture, export, settings
from .rendertaxi_client import auth, frame
from .rendertaxi_client import manifest as mf
from .rendertaxi_client.transport import (ApiClient, ApiError, Cancelled, Transfer, Unauthorized, handshake_problem,
                                          normalize_server_url, prepare, supports_model)
from .settings import CredentialStore, TransferStore, log, log_exception

# --------------------------------------------------------------------------
# Zustand (nur Hauptfaden)
# --------------------------------------------------------------------------


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
        self.model_estimate: export.Estimate | None = None


STATE = State()


def host_version() -> str:
    return ".".join(str(part) for part in bpy.app.version)


def _redraw() -> None:
    try:
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()
    except (AttributeError, ReferenceError):
        pass


class Job:
    """Ein Hintergrundfaden mit Abbruch und einem Rückweg in den Hauptfaden."""

    def __init__(self, label: str, work):
        self.label = label
        self.cancel = threading.Event()
        self.inbox: queue.Queue = queue.Queue()
        self.done = False
        self._work = work
        self.thread = threading.Thread(target=self._run, name=f"rendertaxi-{label}", daemon=True)

    def post(self, callback) -> None:
        self.inbox.put(callback)

    def _run(self) -> None:
        try:
            self._work(self)
        except Cancelled:
            self.post(lambda: _set_message("Abgebrochen."))
        except Unauthorized:
            self.post(_lost_authorization)
        except ApiError as error:
            text = str(error)
            self.post(lambda: _set_error(text))
        except Exception as error:  # nie still: in die Konsole, und eine lesbare Zeile ins Panel
            log_exception("Unerwarteter Fehler im Hintergrund", error)
            self.post(lambda: _set_error("Unerwarteter Fehler — Details in der Konsole (Debug-Schalter)."))
        finally:
            self.post(self._finish)

    def _finish(self) -> None:
        self.done = True
        if STATE.job is self:
            STATE.job = None
            STATE.progress = None
            STATE.code = None
        deferred, STATE.deferred_viewpoints = STATE.deferred_viewpoints, None
        if deferred:
            _request_viewpoints(deferred)


def _pump():
    handled = False
    for current in list(_FINISHING):
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
        if current.done and current in _FINISHING:
            _FINISHING.remove(current)
    if handled:
        _redraw()
    if STATE.job is None and not _FINISHING:
        return None
    return 0.2


_FINISHING: list[Job] = []


def start_job(label: str, work) -> bool:
    if STATE.job is not None:
        _set_error("Es läuft schon ein Vorgang. Erst abwarten oder abbrechen.")
        return False
    job = Job(label, work)
    STATE.job = job
    STATE.error = ""
    _FINISHING.append(job)
    job.thread.start()
    if not bpy.app.timers.is_registered(_pump):
        bpy.app.timers.register(_pump, first_interval=0.1)
    _redraw()
    return True


def _set_message(text: str) -> None:
    STATE.message = text
    STATE.error = ""


def _set_error(text: str) -> None:
    STATE.error = text


def _lost_authorization() -> None:
    try:
        CredentialStore(settings.user_dir()).forget_token(STATE.server)
    except OSError:
        pass
    STATE.connected = False
    STATE.identity = None
    STATE.error = "Die Anmeldung gilt nicht mehr (abgelaufen oder abgemeldet). Bitte neu verbinden."


# --------------------------------------------------------------------------
# Einstellungen und Ablage
# --------------------------------------------------------------------------


def _server_url() -> str:
    prefs = settings.preferences()
    return normalize_server_url(prefs.server_url if prefs else settings.DEFAULT_SERVER_URL)


def _online_problem() -> str | None:
    if not bpy.app.online_access:
        return ("Blender erlaubt keinen Online-Zugriff. Edit › Preferences › System › Network › "
                "„Allow Online Access“ einschalten.")
    return None


def _stores() -> tuple[CredentialStore, TransferStore]:
    directory = settings.user_dir()
    return CredentialStore(directory), TransferStore(directory)


def _api(token: str | None = None) -> ApiClient:
    return ApiClient(_server_url(), token)


# --------------------------------------------------------------------------
# Hintergrundarbeit
# --------------------------------------------------------------------------


def _load_targets(job: Job, api: ApiClient) -> None:
    projects = [(p["id"], p.get("name") or p["id"]) for p in api.projects() if p.get("id")]

    def apply():
        STATE.projects = projects
        _suggest_assignment()
        _request_viewpoints(_props().project)

    job.post(apply)


def _connected(job: Job, api: ApiClient, handshake: dict, identity: dict) -> None:
    def apply():
        STATE.server = api.server_url
        STATE.connected = True
        STATE.identity = identity
        STATE.handshake = handshake
        STATE.message = ""

    job.post(apply)
    _load_targets(job, api)


def _restore_work(server: str, token: str):
    def work(job: Job) -> None:
        api = ApiClient(server, token)
        handshake = api.handshake(host_version(), mf.plugin_version())
        problem = handshake_problem(handshake)
        if problem:
            raise ApiError(problem)
        _connected(job, api, handshake, auth.identity(api))

    return work


def restore_session() -> None:
    """Beim Start: vorhandenes Token prüfen. Jeder Fehler endet in „nicht verbunden"."""
    try:
        STATE.reset()
        server = _server_url()
        STATE.server = server
        if _online_problem():
            return None
        token = CredentialStore(settings.user_dir()).token(server)
        if token:
            start_job("restore", _restore_work(server, token))
    except Exception as error:
        log_exception("Gespeicherter Zustand nicht lesbar; nicht verbunden.", error)
        STATE.reset()
    return None


# --------------------------------------------------------------------------
# Eigenschaften
# --------------------------------------------------------------------------

_ENUM_CACHE: dict[str, list] = {}
# Platzhalter mit dem Wert 0 — dem Vorgabewert: ohne Wahl des Nutzers (oder
# einen Vorschlag aus der letzten Übernahme) ist nichts gewählt.
_CHOOSE_PROJECT = ("NONE", "— Projekt wählen —", "", "NONE", 0)
_CHOOSE_VIEWPOINT = ("NONE", "— Blickpunkt wählen —", "", "NONE", 0)


def _stable(identifier: str) -> int:
    """Fester Zahlenwert je Kennung.

    Blender merkt sich bei dynamischen Aufzählungen den **Zahlenwert** der
    Auswahl. Ohne festen Wert wäre das die Listenposition — ein neues Projekt
    vorn in der Liste verschöbe die Auswahl still auf ein anderes Projekt.
    """
    return 1 + (zlib.crc32(identifier.encode("utf-8")) & 0x3FFFFFFF)


def _project_items(_self, _context):
    items = [_CHOOSE_PROJECT] + [(pid, name[:60], "", "NONE", _stable(pid)) for pid, name in STATE.projects]
    _ENUM_CACHE["projects"] = items
    return items


def _viewpoint_items(self, _context):
    items = [_CHOOSE_VIEWPOINT] + [(vid, name[:60], "", "NONE", _stable(vid))
                                   for vid, name in STATE.viewpoints.get(self.project, [])]
    _ENUM_CACHE["viewpoints"] = items
    return items


def _project_changed(self, _context):
    _request_viewpoints(self.project)


def _request_viewpoints(project_id: str) -> None:
    """Blickpunkte und Ausgabeziele eines Projekts laden — nach dem laufenden Vorgang, falls einer läuft."""
    if not project_id or project_id == "NONE" or not STATE.connected:
        return
    if STATE.job is not None:
        STATE.deferred_viewpoints = project_id
        return
    server, token = STATE.server, CredentialStore(settings.user_dir()).token(STATE.server)

    def work(job: Job) -> None:
        api = ApiClient(server, token)
        viewpoints = [(v["id"], v.get("name") or v["id"]) for v in api.viewpoints(project_id) if v.get("id")]
        desired = api.desired_outputs(project_id)

        def apply():
            STATE.viewpoints[project_id] = viewpoints
            STATE.desired.update(desired)
            _suggest_viewpoint(project_id)

        job.post(apply)

    start_job("viewpoints", work)


def _send_model_changed(_self, context) -> None:
    refresh_estimate(context)


class RTX_Props(PropertyGroup):
    project: EnumProperty(name="Projekt", items=_project_items, update=_project_changed)
    target_mode: EnumProperty(
        name="Ziel",
        items=[("CREATE", "Neuer Blickpunkt", "Legt einen neuen Blickpunkt an"),
               ("UPDATE", "Bestehenden aktualisieren", "Neue Basisbildfassung für einen bestehenden Blickpunkt")],
        default="CREATE",
    )
    viewpoint_name: StringProperty(name="Name", default="", maxlen=200)
    viewpoint: EnumProperty(name="Blickpunkt", items=_viewpoint_items)
    fit_to_capture: BoolProperty(
        name="Rahmen an Aufnahme anpassen",
        description="Das Ausgabeziel übernimmt das Seitenverhältnis der Aufnahme; sonst bleibt der Rahmen, wie er ist",
        default=False,
    )
    size_mode: EnumProperty(
        name="Rahmengröße",
        items=[("canvas-default", "Canvas-Vorgabe", "Nur das Seitenverhältnis der Aufnahme; die lange Kante bleibt"),
               ("capture", "Render-Einstellung übernehmen", "Die Maße der Aufnahme; unter 1536 Pixel langer Kante bleibt die Canvas-Vorgabe")],
        default="canvas-default",
    )
    capture_kind: EnumProperty(
        name="Aufnahme",
        items=[("VIEWPORT", "Viewport", "Die aktuelle 3D-Ansicht (bpy.ops.render.opengl)"),
               ("BEAUTY", "Beauty", "Rendern aus der aktiven Kamera (bpy.ops.render.render)")],
        default="VIEWPORT",
    )
    hide_overlays: BoolProperty(name="Overlays ausblenden", default=True,
                                description="Gitter, Gizmos und Auswahlumrisse nicht mit aufnehmen")
    resolution_source: EnumProperty(
        name="Größe",
        items=[("SCENE", "Szene", "Auflösung der Ausgabeeinstellungen"),
               ("VIEWPOINT", "Blickpunkt-Rahmen", "Die Größe des Rahmens des gewählten Blickpunkts")],
        default="SCENE",
    )
    pass_depth: BoolProperty(name="Tiefe (Depth)", default=False)
    pass_normal: BoolProperty(name="Normalen", default=False)
    pass_albedo: BoolProperty(name="Albedo", default=False)
    pass_object_id: BoolProperty(name="Objekt-ID", default=False)
    pass_material_id: BoolProperty(name="Material-ID", default=False)
    send_model: BoolProperty(
        name="Modell mitsenden",
        description="Die sichtbaren Objekte als GLB und die Kamera der Aufnahme mitsenden (Capture-Manifest 1.2.0)",
        default=False,
        update=_send_model_changed,
    )
    model_materials: BoolProperty(
        name="Materialien und Texturen",
        description="Materialien und Texturen in die GLB-Datei einbetten; die Datei wird größer",
        default=False,
    )


_PASS_PROPS = {
    "depth": "pass_depth",
    "normal": "pass_normal",
    "albedo": "pass_albedo",
    "object-id": "pass_object_id",
    "material-id": "pass_material_id",
}


def _props(context=None) -> RTX_Props:
    return (context or bpy.context).window_manager.rendertaxi


def refresh_estimate(context=None) -> None:
    """Dreiecke und Objekte neu zählen — beim Einschalten und auf Knopfdruck, nie beim Zeichnen."""
    try:
        STATE.model_estimate = export.estimate(context or bpy.context)
    except (AttributeError, RuntimeError, ReferenceError) as error:
        log_exception("Modellgröße nicht bestimmbar", error)
        STATE.model_estimate = None


def _suggest_assignment() -> None:
    """Den zuletzt gewählten Blickpunkt des Dokuments **vorschlagen** — nie still übernehmen."""
    try:
        key = capture.document_key(bpy.context.scene, create=False)
        if not key:
            return
        _, transfers = _stores()
        last = transfers.assignment(key)
        if not last or last.get("server") != STATE.server:
            return
        if any(pid == last.get("projectId") for pid, _ in STATE.projects):
            _props().project = last["projectId"]
    except (TypeError, AttributeError, OSError):
        pass


def _suggest_viewpoint(project_id: str) -> None:
    try:
        key = capture.document_key(bpy.context.scene, create=False)
        if not key:
            return
        _, transfers = _stores()
        last = transfers.assignment(key)
        if not last or last.get("projectId") != project_id:
            return
        if any(vid == last.get("viewpointId") for vid, _ in STATE.viewpoints.get(project_id, [])):
            props = _props()
            props.viewpoint = last["viewpointId"]
            props.target_mode = "UPDATE"
    except (TypeError, AttributeError, OSError):
        pass


# --------------------------------------------------------------------------
# Operatoren: Verbindung
# --------------------------------------------------------------------------


class RTX_OT_connect(Operator):
    bl_idname = "rendertaxi.connect"
    bl_label = "Verbinden"
    bl_description = "Blender über einen Code im Browser mit rendertaxi.ai verbinden"

    def execute(self, context):
        problem = _online_problem()
        if problem:
            _set_error(problem)
            return {"CANCELLED"}
        try:
            server = _server_url()
        except ValueError as error:
            _set_error(str(error))
            return {"CANCELLED"}
        credentials, _ = _stores()
        prefs = settings.preferences()
        name = prefs.device_name if prefs else ""
        version = mf.plugin_version()

        def work(job: Job) -> None:
            api = ApiClient(server)
            handshake = api.handshake(host_version(), version)
            problem = handshake_problem(handshake)
            if problem:
                raise ApiError(problem)
            description = auth.device(credentials, api.server_url, host_version(), version, name)

            def show(code: dict) -> None:
                def apply():
                    STATE.code = code
                    STATE.message = ""
                    try:
                        bpy.ops.wm.url_open(url=code["verificationUriComplete"])
                    except RuntimeError:
                        pass  # ohne Browser bleiben Code und Adresse im Panel

                job.post(apply)

            identity = auth.DeviceLogin(api, credentials, description, job.cancel).run(show)
            _connected(job, api, handshake, identity)

        STATE.server = server
        start_job("connect", work)
        return {"FINISHED"}


class RTX_OT_open_login(Operator):
    bl_idname = "rendertaxi.open_login"
    bl_label = "Im Browser öffnen"
    bl_description = "Die Bestätigungsseite im Standardbrowser öffnen"

    def execute(self, _context):
        if STATE.code:
            bpy.ops.wm.url_open(url=STATE.code["verificationUriComplete"])
        return {"FINISHED"}


class RTX_OT_cancel(Operator):
    bl_idname = "rendertaxi.cancel"
    bl_label = "Abbrechen"
    bl_description = "Den laufenden Vorgang abbrechen; eine Übernahme lässt sich später fortsetzen"

    def execute(self, _context):
        if STATE.job is not None:
            STATE.job.cancel.set()
        return {"FINISHED"}


class RTX_OT_sign_out(Operator):
    bl_idname = "rendertaxi.sign_out"
    bl_label = "Abmelden"
    bl_description = "Das Token beim Server widerrufen und auf diesem Rechner löschen"

    def execute(self, _context):
        credentials, _ = _stores()
        server = STATE.server or _server_url()
        token = credentials.token(server)

        def work(job: Job) -> None:
            api = ApiClient(server, token)
            warning = auth.sign_out(api, credentials)

            def apply():
                STATE.reset()
                STATE.server = server
                STATE.message = "Abgemeldet."
                if warning:
                    STATE.error = warning

            job.post(apply)

        start_job("sign-out", work)
        return {"FINISHED"}


class RTX_OT_refresh(Operator):
    bl_idname = "rendertaxi.refresh"
    bl_label = "Aktualisieren"
    bl_description = "Projekte und Blickpunkte neu laden"

    def execute(self, context):
        if not STATE.connected:
            return {"CANCELLED"}
        server = STATE.server
        token = CredentialStore(settings.user_dir()).token(server)
        project_id = _props(context).project

        def work(job: Job) -> None:
            api = ApiClient(server, token)
            _load_targets(job, api)
            if project_id and project_id != "NONE":
                viewpoints = [(v["id"], v.get("name") or v["id"]) for v in api.viewpoints(project_id)]
                desired = api.desired_outputs(project_id)

                def apply():
                    STATE.viewpoints[project_id] = viewpoints
                    STATE.desired.update(desired)

                job.post(apply)

        start_job("refresh", work)
        return {"FINISHED"}


# --------------------------------------------------------------------------
# Operatoren: Übernahme
# --------------------------------------------------------------------------


def _target(props: RTX_Props) -> dict:
    mode = frame.CREATE if props.target_mode == "CREATE" else frame.UPDATE
    return frame.target(props.project, mode, name=props.viewpoint_name, viewpoint_id=props.viewpoint,
                        fit_to_capture=props.fit_to_capture, size=props.size_mode)


def capture_size(context, props: RTX_Props) -> tuple[int, int] | None:
    """``None`` heißt: Auflösung der Szene."""
    if props.resolution_source != "VIEWPOINT" or props.target_mode != "UPDATE":
        return None
    return capture.viewpoint_size(STATE.desired.get(props.viewpoint), capture.scene_size(context.scene))


def selected_roles(props: RTX_Props) -> list[str]:
    if props.capture_kind != "BEAUTY":
        return []
    return [role for role, name in _PASS_PROPS.items() if getattr(props, name)]


def model_problem(context, props: RTX_Props, handshake: dict | None) -> str | None:
    """Warum „Modell mitsenden" nicht geht — im Panel **vor** dem Senden, im Operator als Fehler."""
    if handshake is not None and not supports_model(handshake):
        return "Der Server nimmt Capture-Manifest 1.2.0 (mit Modell) noch nicht an."
    problem = export.unit_problem(context.scene)
    if problem:
        return problem
    return None


def build_capture(context, props: RTX_Props, directory: str, handshake: dict | None) -> bytes:
    """Aufnehmen, Manifest bauen und lokal prüfen — im Hauptfaden.

    Die Vertragsfassung kommt aus dem Handshake: 1.3.0, wenn der Server sie
    umsetzt (PNG-Datenpässe, RTX-P-012), sonst ohne Modell 1.1.0 und mit Modell
    1.2.0. Ohne Modell sind ``camera`` und ``geometry`` ``null``; mit Modell
    geht die GLB mit und — wenn darstellbar — die Kamera der Aufnahme. Die
    Datenpässe haben die Bittiefe aus den Einstellungen (``settings``).
    """
    scene = context.scene
    size = capture_size(context, props)
    limits = (handshake or {}).get("limits") or {}
    contract_version = mf.image_contract_version(handshake)
    if props.send_model:
        problem = model_problem(context, props, handshake)
        if problem:
            raise ValueError(problem)
    capabilities = capture.probe(context)
    if props.capture_kind == "VIEWPORT":
        files = [capture.capture_viewport(context, directory, size, props.hide_overlays)]
        planned: list[mf.PlannedRole] = []
        view_name = "3D-Ansicht"
    else:
        beauty, passes, planned = capture.capture_beauty(
            context, directory, size, selected_roles(props), limits.get("allowedMediaTypes"),
            settings.data_pass_bit_depth())
        files = [beauty, *passes]
        view_name = scene.camera.name if scene.camera else None
    camera = geometry = None
    if props.send_model:
        image = files[0].image
        wm = context.window_manager
        wm.progress_begin(0, 100)
        try:
            wm.progress_update(10)
            files.append(export.export_model(context, directory, props.model_materials))
            wm.progress_update(90)
        finally:
            wm.progress_end()
        camera = export.camera_block(context, props.capture_kind, (image["width"], image["height"]))
        geometry = export.geometry_block(context, directory)
        contract_version = mf.model_contract_version(handshake)
    stem = os.path.splitext(os.path.basename(bpy.data.filepath))[0] if bpy.data.filepath else None
    data = mf.ManifestInput(
        capture_id=mf.uuid_v7(),
        created_at=mf.timestamp_utc(),
        host_version=host_version(),
        plugin_version=mf.plugin_version(),
        machine=auth.machine(),
        capabilities=capabilities,
        platform_project_id=props.project,
        source_project_key=capture.document_key(scene, create=True),
        project_display_name=stem,
        view_display_name=view_name,
        files=files,
        planned=planned,
        camera=camera,
        geometry=geometry,
        contract_version=contract_version,
    )
    manifest = mf.build_manifest(data)
    problems = mf.validate_manifest(manifest)
    if problems:
        raise ValueError("Das Manifest ist ungültig und wird nicht hochgeladen: " + problems[0])
    raw = mf.serialize(manifest)
    problem = mf.check_limits(manifest, len(raw), limits)
    if problem:
        raise ValueError(problem)
    return raw


def _transfer_work(server: str, token: str, document_key: str, pending: dict):
    directory = settings.user_dir()  # im Hauptfaden: der Nutzerordner kommt aus bpy

    def work(job: Job) -> None:
        api = ApiClient(server, token)

        def progress(text: str, percent: int) -> None:
            job.post(lambda: setattr(STATE, "progress", (text, percent)))

        outcome = Transfer(api, TransferStore(directory), document_key, job.cancel, progress).run(pending)

        def apply():
            STATE.result = {
                "captureId": outcome["captureId"],
                "openUrl": outcome["result"].get("openUrl"),
                "viewpointId": outcome["result"].get("viewpointId"),
            }
            STATE.message = "Übernahme abgeschlossen."
            project_id = pending["target"]["projectId"]
            STATE.viewpoints.pop(project_id, None)

        job.post(apply)
        # Neuer Blickpunkt: die Liste neu lesen, damit er gleich wählbar ist.
        project_id = pending["target"]["projectId"]
        viewpoints = [(v["id"], v.get("name") or v["id"]) for v in api.viewpoints(project_id)]
        desired = api.desired_outputs(project_id)

        def refresh():
            STATE.viewpoints[project_id] = viewpoints
            STATE.desired.update(desired)

        job.post(refresh)

    return work


class RTX_OT_capture(Operator):
    bl_idname = "rendertaxi.capture"
    bl_label = "Aufnehmen und übernehmen"
    bl_description = "Die Ansicht aufnehmen und an den gewählten Blickpunkt übergeben"

    def execute(self, context):
        if STATE.job is not None:
            self.report({"WARNING"}, "Es läuft schon ein Vorgang.")
            return {"CANCELLED"}
        if not STATE.connected:
            _set_error("Nicht verbunden.")
            return {"CANCELLED"}
        props = _props(context)
        try:
            target = _target(props)
            credentials, transfers = _stores()
            key = capture.document_key(context.scene, create=True)
            if transfers.pending(key):
                raise ValueError("Hier läuft schon eine Übernahme. Fortsetzen oder verwerfen.")
            directory = transfers.capture_dir(mf.uuid_v7())
            os.makedirs(directory, mode=0o700, exist_ok=True)
            STATE.progress = ("Aufnehmen …", 0)
            STATE.result = None
            raw = build_capture(context, props, directory, STATE.handshake)
            pending = prepare(transfers, key, STATE.server, target, raw, directory)
        except (ValueError, capture.CaptureError, export.ExportError, RuntimeError, OSError) as error:
            STATE.progress = None
            if "pending" not in locals() and "directory" in locals():
                shutil.rmtree(directory, ignore_errors=True)  # nichts angelegt: keine Reste
            _set_error(str(error))
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        token = credentials.token(STATE.server)
        start_job("transfer", _transfer_work(STATE.server, token, key, pending))
        return {"FINISHED"}


class RTX_OT_resume(Operator):
    bl_idname = "rendertaxi.resume"
    bl_label = "Übernahme fortsetzen"
    bl_description = "Die angefangene Übernahme mit demselben Vorgangsschlüssel zu Ende führen"

    def execute(self, context):
        key = capture.document_key(context.scene, create=False)
        credentials, transfers = _stores()
        pending = transfers.pending(key) if key else None
        if not pending or not STATE.connected:
            return {"CANCELLED"}
        if pending["server"] != STATE.server:
            _set_error("Die angefangene Übernahme gehört zu einer anderen Serveradresse. Bitte verwerfen.")
            return {"CANCELLED"}
        if not settings.has_local_material(pending):
            _set_error("Die Dateien der angefangenen Übernahme fehlen. Bitte verwerfen.")
            return {"CANCELLED"}
        start_job("transfer", _transfer_work(STATE.server, credentials.token(STATE.server), key, pending))
        return {"FINISHED"}


class RTX_OT_discard(Operator):
    bl_idname = "rendertaxi.discard"
    bl_label = "Angefangene Übernahme verwerfen"
    bl_description = "Die angefangene Übernahme beim Server abbrechen und lokal löschen"

    def execute(self, context):
        key = capture.document_key(context.scene, create=False)
        credentials, transfers = _stores()
        pending = transfers.pending(key) if key else None
        if not pending:
            return {"CANCELLED"}
        server = pending["server"]
        token = credentials.token(server)

        def work(job: Job) -> None:
            Transfer(ApiClient(server, token), transfers, key).discard(pending)
            job.post(lambda: _set_message("Angefangene Übernahme verworfen."))

        start_job("discard", work)
        return {"FINISHED"}


class RTX_OT_count_model(Operator):
    bl_idname = "rendertaxi.count_model"
    bl_label = "Modell neu zählen"
    bl_description = "Sichtbare Objekte und Dreiecke neu zählen"

    def execute(self, context):
        refresh_estimate(context)
        return {"FINISHED"}


class RTX_OT_open_result(Operator):
    bl_idname = "rendertaxi.open_result"
    bl_label = "Blickpunkt im Browser öffnen"
    bl_description = "Den Blickpunkt in der Webanwendung öffnen"

    def execute(self, _context):
        if STATE.result and STATE.result.get("openUrl"):
            bpy.ops.wm.url_open(url=STATE.result["openUrl"])
        return {"FINISHED"}


# --------------------------------------------------------------------------
# Panel
# --------------------------------------------------------------------------


_WRAP = {"width": 32}


def _measure(context) -> None:
    """Zeichen je Zeile aus der Breite der Seitenleiste — Blender bricht Beschriftungen nicht selbst um."""
    try:
        scale = context.preferences.system.ui_scale or 1.0
        _WRAP["width"] = max(18, int(context.region.width / (7.0 * scale)) - 6)
    except (AttributeError, TypeError, ZeroDivisionError):
        _WRAP["width"] = 32


def _wrapped(layout, text: str, icon: str = "NONE", width: int | None = None) -> None:
    width = width or _WRAP["width"]
    words, line, first = text.split(), "", True
    for word in words:
        if line and len(line) + 1 + len(word) > width:
            layout.label(text=line, icon=icon if first else "BLANK1")
            line, first = word, False
        else:
            line = f"{line} {word}".strip()
    if line:
        layout.label(text=line, icon=icon if first else "BLANK1")


def _aspect_hint(context, props: RTX_Props) -> str | None:
    """Weicht bei „Rahmen behalten" das Ausgabeziel ab, sagt das Panel es — es löst es nicht still auf."""
    if props.target_mode != "UPDATE" or props.fit_to_capture:
        return None
    viewpoint_frame = capture.viewpoint_size(STATE.desired.get(props.viewpoint), capture.scene_size(context.scene))
    size = capture_size(context, props) or capture.scene_size(context.scene)
    return frame.aspect_hint(viewpoint_frame, size, frame_option="Rahmen an Aufnahme anpassen",
                             size_option="Blickpunkt-Rahmen")


def _size_text(props: RTX_Props) -> str:
    return frame.size_text(props.target_mode == "CREATE", props.fit_to_capture, props.size_mode)


class RTX_PT_panel(Panel):
    bl_label = "rendertaxi.ai"
    bl_idname = "RTX_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "rendertaxi"

    def draw(self, context):
        layout = self.layout
        props = _props(context)
        busy = STATE.job is not None
        _measure(context)

        # -- Verbindung ------------------------------------------------------
        box = layout.box()
        box.label(text="Verbindung", icon="URL")
        problem = _online_problem()
        if problem:
            _wrapped(box, problem, "ERROR")
        if STATE.code:
            box.label(text=f"Code: {STATE.code['userCode']}", icon="KEY_HLT")
            _wrapped(box, f"Im Browser bestätigen: {STATE.code['verificationUri']}")
            row = box.row()
            row.operator(RTX_OT_open_login.bl_idname, icon="URL")
            row.operator(RTX_OT_cancel.bl_idname, icon="CANCEL")
        elif STATE.connected and STATE.identity:
            who = STATE.identity.get("user") or STATE.identity.get("email")
            org = STATE.identity.get("organization")
            _wrapped(box, f"Angemeldet als {who}", "CHECKMARK")
            if org:
                _wrapped(box, org, "COMMUNITY")
            row = box.row()
            row.enabled = not busy
            row.operator(RTX_OT_sign_out.bl_idname, icon="UNLINKED")
        else:
            box.label(text="Nicht verbunden", icon="UNLINKED")
            row = box.row()
            row.enabled = not busy and problem is None
            row.operator(RTX_OT_connect.bl_idname, icon="LINKED")
        server = STATE.server
        if server:
            box.label(text=server, icon="WORLD")

        if not STATE.connected:
            self._status(layout)
            return

        # -- Projekt und Blickpunkt -----------------------------------------
        box = layout.box()
        row = box.row()
        row.label(text="Projekt und Blickpunkt", icon="OUTLINER_COLLECTION")
        row.operator(RTX_OT_refresh.bl_idname, text="", icon="FILE_REFRESH")
        box.prop(props, "project", text="")
        box.prop(props, "target_mode", expand=True)
        if props.target_mode == "CREATE":
            box.prop(props, "viewpoint_name")
        else:
            box.prop(props, "viewpoint", text="")
            box.prop(props, "fit_to_capture")
        box.label(text="Rahmengröße")
        box.prop(props, "size_mode", text="")
        _wrapped(box, _size_text(props), "INFO")
        hint = _aspect_hint(context, props)
        if hint:
            _wrapped(box, hint, "ERROR")

        # -- Bild übernehmen ------------------------------------------------
        box = layout.box()
        box.label(text="Bild übernehmen", icon="IMAGE_DATA")
        box.prop(props, "capture_kind", expand=True)
        if props.capture_kind == "VIEWPORT":
            box.prop(props, "hide_overlays")
        if props.target_mode == "UPDATE":
            box.prop(props, "resolution_source")
        size = capture_size(context, props) or capture.scene_size(context.scene)
        box.label(text=f"Ausschnitt {size[0]} × {size[1]}")
        if props.capture_kind == "BEAUTY":
            if context.scene.camera is None:
                _wrapped(box, "Die Szene hat keine aktive Kamera.", "ERROR")
            self._passes(box.box(), context, props)
        self._model(box.box(), context, props)

        key = capture.document_key(context.scene, create=False)
        pending = None
        try:
            pending = TransferStore(settings.user_dir()).pending(key) if key else None
        except OSError:
            pending = None
        if pending:
            _wrapped(box, f"Offene Übernahme vom {pending.get('createdAt', '')[:16].replace('T', ' ')} UTC", "TIME")
            row = box.row()
            row.enabled = not busy
            row.operator(RTX_OT_resume.bl_idname, icon="PLAY")
            row = box.row()
            row.enabled = not busy
            row.operator(RTX_OT_discard.bl_idname, icon="TRASH")
        else:
            row = box.row()
            row.scale_y = 1.4
            row.enabled = not busy
            row.operator(RTX_OT_capture.bl_idname, icon="RENDER_STILL")

        self._status(layout)

        # -- Im Browser öffnen ------------------------------------------------
        if STATE.result and STATE.result.get("openUrl"):
            box = layout.box()
            box.operator(RTX_OT_open_result.bl_idname, icon="URL")
            box.label(text=f"Capture {STATE.result['captureId']}")

    def _passes(self, layout, context, props):
        layout.label(text="Pässe (optional)")
        allowed = ((STATE.handshake or {}).get("limits") or {}).get("allowedMediaTypes")
        if allowed is not None and capture.PNG not in allowed:
            _wrapped(layout, "Der Server nimmt derzeit kein PNG an: gewählte Pässe werden nur als „geplant“ vermerkt.", "INFO")
        prefs = settings.preferences()
        if prefs is not None:
            layout.prop(prefs, "data_pass_bit_depth")
        bit_depth = settings.data_pass_bit_depth()
        scene, view_layer = context.scene, context.view_layer
        for spec in capture.PASSES:
            state, hint = capture.pass_status(spec, scene, view_layer)
            if state not in ("available", "requires-user-action"):
                continue
            row = layout.row()
            row.enabled = state == "available"
            row.prop(props, _PASS_PROPS[spec.role], text=spec.label)
            if state != "available":
                _wrapped(layout, hint or "", "INFO")
            elif getattr(props, _PASS_PROPS[spec.role]) and scene.camera is not None:
                # Nur für gewählte Pässe: die Prüfung geht über alle Objekte der Szene.
                problem = capture.pass_problem(spec, scene, bit_depth)
                if problem:
                    _wrapped(layout, f"wird als „geplant“ gemeldet: {problem}", "INFO")

    def _model(self, layout, context, props):
        """„Modell mitsenden": Größe und Grenzen vor dem Senden, und was nicht mitgeht."""
        row = layout.row()
        row.prop(props, "send_model")
        if props.send_model:
            row.operator(RTX_OT_count_model.bl_idname, text="", icon="FILE_REFRESH")
        if not props.send_model:
            return
        problem = model_problem(context, props, STATE.handshake)
        if problem:
            _wrapped(layout, problem, "ERROR")
            return
        layout.prop(props, "model_materials")
        estimate = STATE.model_estimate
        if estimate is not None:
            text = (f"≈ {estimate.triangles:,} Dreiecke in {estimate.objects} sichtbaren Objekten "
                    f"(höchstens {export.MAX_TRIANGLES:,})").replace(",", " ")
            _wrapped(layout, text, "ERROR" if estimate.triangles > export.MAX_TRIANGLES else "MESH_DATA")
        cap = ((STATE.handshake or {}).get("limits") or {}).get("maxGeometryBytes")
        if cap:
            _wrapped(layout, f"Modelldatei höchstens {cap / 1048576:.0f} MB", "INFO")
        why = export.camera_problem(context, props.capture_kind)
        if why:
            _wrapped(layout, f"Ohne Kamera: {why}", "INFO")

    def _status(self, layout):
        if STATE.progress:
            text, percent = STATE.progress
            row = layout.row()
            row.label(text=f"{text} ({percent} %)", icon="SORTTIME")
            if STATE.job is not None and STATE.job.label == "transfer":
                row.operator(RTX_OT_cancel.bl_idname, text="", icon="CANCEL")
        elif STATE.job is not None and STATE.job.label not in ("connect",):
            layout.label(text="Bitte warten …", icon="SORTTIME")
        if STATE.error:
            _wrapped(layout, STATE.error, "ERROR")
        elif STATE.message:
            _wrapped(layout, STATE.message, "INFO")


CLASSES = (
    RTX_Props,
    RTX_OT_connect,
    RTX_OT_open_login,
    RTX_OT_cancel,
    RTX_OT_sign_out,
    RTX_OT_refresh,
    RTX_OT_capture,
    RTX_OT_resume,
    RTX_OT_discard,
    RTX_OT_count_model,
    RTX_OT_open_result,
    RTX_PT_panel,
)


def register() -> None:
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.rendertaxi = PointerProperty(type=RTX_Props)
    prefs = settings.preferences()
    settings.set_debug(bool(prefs and prefs.debug_logging))
    bpy.app.timers.register(restore_session, first_interval=0.5)


def unregister() -> None:
    if STATE.job is not None:
        STATE.job.cancel.set()
    for timer in (_pump, restore_session):
        if bpy.app.timers.is_registered(timer):
            bpy.app.timers.unregister(timer)
    del bpy.types.WindowManager.rendertaxi
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    STATE.reset()
