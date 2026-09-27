"""Bilder aus Blender — Viewport, Beauty, Pässe — und die Laufzeitprobe.

Belege: ``integrations/blender/docs/capabilities.md``. Gemessen am 27.09.2026
mit Blender 5.2.2 (Linux, Software-GL im virtuellen Display):

* ``bpy.ops.render.opengl(view_context=True)`` braucht ein Fenster mit einer
  3D-Ansicht; im Hintergrundbetrieb (``-b``) gibt es keines. Es rendert in der
  **Auflösung der Szene**, nicht in der Fenstergröße, und auch bei Cycles ohne
  Fehler. Overlays (Gitter, Gizmos, Auswahlumriss) landen im Bild (QB-05) —
  deshalb blendet das Add-on sie auf Wunsch für die Aufnahme aus.
* ``bpy.ops.render.render`` rendert aus der aktiven Kamera. Das Beauty-Bild
  wird mit ``Image.save_render`` als PNG (8 Bit, RGBA, „Save As Render":
  View Transform angewendet) geschrieben; die Pässe als unkomprimiertes
  Mehrschicht-EXR, aus dem ``exr.extract_pass`` je Rolle eine Datei macht.

Das Add-on **schaltet keinen Pass ein** und stellt keine Engine um. Welche
Pässe möglich sind, sagt die Laufzeitprobe; was fehlt, nennt der Hinweis. Jede
vorübergehende Änderung an der Szene (Auflösung, Ausgabeformat, Overlays)
wird im ``finally`` zurückgesetzt.
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from dataclasses import dataclass

import bpy

from . import exr
from . import manifest as mf

DOCUMENT_KEY_PROPERTY = "rendertaxi_document_key"
EXR = "image/x-exr"
PNG = "image/png"

ENGINE_LABELS = {
    "CYCLES": "Cycles",
    "BLENDER_EEVEE": "EEVEE",
    "BLENDER_EEVEE_NEXT": "EEVEE",
    "BLENDER_WORKBENCH": "Workbench",
}


@dataclass(frozen=True)
class PassSpec:
    role: str
    capability: str
    label: str
    engines: tuple[str, ...]
    part: str  # Name des Passes im Mehrschicht-EXR, ohne View-Layer
    color_space: str
    ui_path: str  # wo der Nutzer den Pass einschaltet
    blocked_by: str | None = None  # offene Frage, die ``present`` verbietet
    blocked_note: str | None = None
    extra_hint: str | None = None

    def enabled(self, view_layer) -> bool:
        if self.role == "albedo":
            cycles = getattr(view_layer, "cycles", None)
            return bool(getattr(cycles, "denoising_store_passes", False))
        attribute = {
            "depth": "use_pass_z",
            "normal": "use_pass_normal",
            "object-id": "use_pass_object_index",
            "material-id": "use_pass_material_index",
        }[self.role]
        return bool(getattr(view_layer, attribute, False))


# Zuordnung aus capabilities.md, Abschnitt 3 (Passes, Blender 5.2).
PASSES: tuple[PassSpec, ...] = (
    PassSpec("depth", "depthPass", "Tiefe (Depth)", ("CYCLES", "BLENDER_EEVEE", "BLENDER_EEVEE_NEXT", "BLENDER_WORKBENCH"),
             "Depth", "non-color", "View Layer › Passes › Data › Z",
             blocked_by="QB-01",
             blocked_note="Depth-Pass vorhanden, Kodierung nicht belegt (integrations/blender/docs/open-questions.md, QB-01)."),
    PassSpec("normal", "normalPass", "Normalen", ("CYCLES", "BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"),
             "Normal", "non-color", "View Layer › Passes › Data › Normal",
             blocked_by="QB-02",
             blocked_note="Weltraum belegt, Wertebereich nicht (integrations/blender/docs/open-questions.md, QB-02)."),
    PassSpec("albedo", "albedoPass", "Albedo", ("CYCLES",),
             "Denoising Albedo", "linear", "View Layer › Passes › Data › Denoising Data (Cycles)"),
    PassSpec("object-id", "objectIdPass", "Objekt-ID", ("CYCLES",),
             "Object Index", "non-color", "View Layer › Passes › Data › Object Index (Cycles)",
             extra_hint="Objekte brauchen einen Pass-Index (Objekt › Relations)."),
    PassSpec("material-id", "materialIdPass", "Material-ID", ("CYCLES",),
             "Material Index", "non-color", "View Layer › Passes › Data › Material Index (Cycles)",
             extra_hint="Materialien brauchen einen Pass-Index (Material › Settings)."),
)
PASS_BY_ROLE = {spec.role: spec for spec in PASSES}


# --------------------------------------------------------------------------
# Laufzeitprobe
# --------------------------------------------------------------------------


def find_view3d(context=None):
    """(Fenster, Bereich, Region) der 3D-Ansicht: die des Aufrufs, sonst die größte."""
    context = context or bpy.context
    area = getattr(context, "area", None)
    window = getattr(context, "window", None)
    if area is not None and area.type == "VIEW_3D" and window is not None:
        region = next((r for r in area.regions if r.type == "WINDOW"), None)
        if region is not None:
            return window, area, region
    best = None
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type != "VIEW_3D":
                continue
            region = next((r for r in area.regions if r.type == "WINDOW"), None)
            if region is not None and (best is None or area.width * area.height > best[1].width * best[1].height):
                best = (window, area, region)
    return best


def pass_status(spec: PassSpec, scene, view_layer) -> tuple[str, str | None]:
    """Zustand eines Passes in **dieser** Szene und ein Hinweis, was fehlt."""
    engine = scene.render.engine
    if engine not in spec.engines:
        names = " oder ".join(sorted({ENGINE_LABELS.get(e, e) for e in spec.engines}))
        return "requires-user-action", f"Render-Engine {names} wählen, dann {spec.ui_path} einschalten."
    if not spec.enabled(view_layer):
        hint = f"{spec.ui_path} einschalten."
        if spec.extra_hint:
            hint += " " + spec.extra_hint
        return "requires-user-action", hint
    return "available", spec.extra_hint


def probe(context=None) -> dict:
    """``source.host.capabilities`` aus dem laufenden Blender — nicht aus der Matrixdatei.

    Gemeldet wird nur, was diese Probe tatsächlich prüft. ``maskPass``,
    ``bimMetadata`` (offen, QB-09/QB-10), ``backgroundRender`` und
    ``resultReimport`` (vom Add-on nicht benutzt) fehlen und heißen damit
    ``unknown``. Ein Pass beschreibt den Pass, nicht seine Kodierung: auch ein
    eingeschalteter Depth-Pass bleibt als Asset ``planned`` (QB-01).
    """
    context = context or bpy.context
    scene = context.scene
    view_layer = context.view_layer
    capabilities: dict = {}

    if bpy.app.background:
        capabilities["viewportCapture"] = {"state": "unavailable"}
    elif find_view3d(context) is None:
        capabilities["viewportCapture"] = {"state": "requires-user-action", "constraints": {"mediaTypes": [PNG]}}
    else:
        capabilities["viewportCapture"] = {"state": "available", "constraints": {"mediaTypes": [PNG]}}

    has_camera = scene.camera is not None
    capabilities["beautyRender"] = {
        "state": "available" if has_camera else "requires-user-action",
        "constraints": {"mediaTypes": [PNG]},
    }
    for spec in PASSES:
        state, _hint = pass_status(spec, scene, view_layer)
        capabilities[spec.capability] = {"state": state, "constraints": {"mediaTypes": [EXR]}}
    capabilities["cameraExport"] = {"state": "available" if has_camera else "requires-user-action"}
    gltf = hasattr(bpy.ops, "export_scene") and hasattr(bpy.ops.export_scene, "gltf")
    capabilities["geometryExport"] = {"state": "available" if gltf else "unavailable"}
    return capabilities


# --------------------------------------------------------------------------
# Szene
# --------------------------------------------------------------------------


def document_key(scene, create: bool) -> str | None:
    """``sourceProjectKey`` als ``blender:<uuid>`` an der Szene.

    Gemessen am 27.09.2026 (QB-07): die Kennung wandert mit „Speichern unter",
    „Full Copy", „Link Copy", Append und Link; nur eine neue, leere Szene hat
    keine. Sie ist deshalb ein Hinweis, keine Autorität (ADR 0009,
    Entscheidung 8): das Add-on **schlägt** den zuletzt gewählten Blickpunkt nur
    vor. Eine doppelt vergebene Kennung **innerhalb** der Datei erkennt es und
    gibt der aufnehmenden Szene eine neue, statt sie still zu übernehmen;
    zwischen zwei Dateien ist eine Kopie nicht zu erkennen.
    """
    value = scene.get(DOCUMENT_KEY_PROPERTY)
    valid = isinstance(value, str) and value.startswith("blender:") and len(value) == 44
    if not create:
        return value if valid else None
    if scene.library is not None:
        raise CaptureError("Die Szene ist aus einer anderen Datei verknüpft (Link). Bitte in einer eigenen Szene aufnehmen.")
    shared = valid and any(
        other != scene and other.library is None and other.get(DOCUMENT_KEY_PROPERTY) == value
        for other in bpy.data.scenes
    )
    if valid and not shared:
        return value
    value = f"blender:{uuid.uuid4()}"
    scene[DOCUMENT_KEY_PROPERTY] = value
    return value


def scene_size(scene) -> tuple[int, int]:
    render = scene.render
    return (max(1, render.resolution_x * render.resolution_percentage // 100),
            max(1, render.resolution_y * render.resolution_percentage // 100))


def viewpoint_size(desired: dict | None, current: tuple[int, int]) -> tuple[int, int] | None:
    """Die Größe des Blickpunkt-Rahmens — ``exact`` direkt, ``aspect_ratio`` mit der langen Kante der Szene."""
    if not isinstance(desired, dict):
        return None
    if desired.get("kind") == "exact":
        width, height = int(desired.get("width", 0)), int(desired.get("height", 0))
        return (width, height) if width > 0 and height > 0 else None
    if desired.get("kind") == "aspect_ratio":
        try:
            a, b = (int(x) for x in str(desired.get("value", "")).split(":"))
        except ValueError:
            return None
        if a <= 0 or b <= 0:
            return None
        long_edge = max(current)
        return (long_edge, max(1, round(long_edge * b / a))) if a >= b else (max(1, round(long_edge * a / b)), long_edge)
    return None


def color_note(scene) -> str:
    view = scene.view_settings
    text = f"View Transform {view.view_transform}"
    if view.look and view.look != "None":
        text += f", Look {view.look}"
    working = getattr(getattr(bpy.data, "colorspace", None), "working_space", "Linear Rec.709")
    if working and working != "Linear Rec.709":
        text += f"; Arbeitsfarbraum {working}"
    return text


@contextmanager
def _resolution(scene, size: tuple[int, int] | None):
    if size is None:
        yield
        return
    render = scene.render
    saved = (render.resolution_x, render.resolution_y, render.resolution_percentage)
    try:
        render.resolution_x, render.resolution_y = size
        render.resolution_percentage = 100
        yield
    finally:
        render.resolution_x, render.resolution_y, render.resolution_percentage = saved


@contextmanager
def _image_settings(scene, media_type: str, file_format: str, color_mode: str, color_depth: str,
                    exr_codec: str | None = None):
    settings = scene.render.image_settings
    saved = {name: getattr(settings, name) for name in
             ("media_type", "file_format", "color_mode", "color_depth", "exr_codec", "compression")}
    try:
        settings.media_type = media_type
        settings.file_format = file_format
        settings.color_mode = color_mode
        settings.color_depth = color_depth
        if exr_codec is not None:
            settings.exr_codec = exr_codec
        yield
    finally:
        for name in ("media_type", "file_format", "color_mode", "color_depth", "exr_codec", "compression"):
            try:
                setattr(settings, name, saved[name])
            except (TypeError, ValueError):
                pass  # ein Wert, den das zurückgesetzte Format nicht kennt


@contextmanager
def _overlays_hidden(area, hide: bool):
    overlay = area.spaces.active.overlay
    saved = overlay.show_overlays
    try:
        if hide:
            overlay.show_overlays = False
        yield
    finally:
        overlay.show_overlays = saved


def _save_png(scene, path: str) -> None:
    image = bpy.data.images["Render Result"]
    with _image_settings(scene, "IMAGE", "PNG", "RGBA", "8"):
        image.save_render(path, scene=scene)


def _file(path: str, root: str, role: str, media_type: str, image: dict, note: str) -> mf.CaptureFile:
    sha, size = mf.sha256_file(path)
    relative = os.path.relpath(path, root).replace(os.sep, "/")
    return mf.CaptureFile(role=role, path=relative, media_type=media_type, image=image,
                          note=note, byte_size=size, sha256=sha)


class CaptureError(RuntimeError):
    pass


def capture_viewport(context, root: str, size: tuple[int, int] | None, hide_overlays: bool) -> mf.CaptureFile:
    """Die aktuelle 3D-Ansicht als PNG — ``bpy.ops.render.opengl`` mit ``view_context``."""
    if bpy.app.background:
        # Gemessen: auch -b hat ein Fenster mit 3D-Ansicht, aber keinen OpenGL-Kontext.
        raise CaptureError("Ohne Fenster (blender -b) gibt es keine 3D-Ansicht zum Aufnehmen.")
    found = find_view3d(context)
    if found is None:
        raise CaptureError("Keine 3D-Ansicht offen — die Viewport-Aufnahme braucht eine.")
    window, area, region = found
    scene = context.scene
    images = os.path.join(root, "images")
    os.makedirs(images, exist_ok=True)
    path = os.path.join(images, "viewport.png")
    shading = area.spaces.active.shading.type
    with _resolution(scene, size), _overlays_hidden(area, hide_overlays):
        try:
            with context.temp_override(window=window, area=area, region=region):
                result = bpy.ops.render.opengl(write_still=False, view_context=True)
        except RuntimeError as error:
            raise CaptureError(f"Blender hat die Viewport-Aufnahme abgelehnt: {error}") from None
        if "FINISHED" not in result:
            raise CaptureError("Blender hat die Viewport-Aufnahme nicht ausgeführt.")
        _save_png(scene, path)
    engine = ENGINE_LABELS.get(scene.render.engine, scene.render.engine)
    note = (f"Viewport Render (bpy.ops.render.opengl, view_context), {engine}, Shading {shading}; "
            f"Overlays {'aus' if hide_overlays else 'an'}; {color_note(scene)}.")
    return _file(path, root, "viewport", PNG, mf.describe_png(path), note)


def capture_beauty(context, root: str, size: tuple[int, int] | None, roles: list[str],
                   allowed_media_types: list[str] | None):
    """Beauty als PNG und die gewählten Pässe — ``(beauty, pass_files, planned)``.

    Ein gewählter Pass wird ``planned`` mit Begründung, wenn eine offene Frage
    ``present`` verbietet (Depth QB-01, Normalen QB-02) oder wenn der Server
    ``image/x-exr`` nicht annimmt (``limits.allowedMediaTypes`` des
    Handshakes) — kein stiller Umweg über ein anderes Format.
    """
    scene = context.scene
    if scene.camera is None:
        raise CaptureError("Die Szene hat keine aktive Kamera — Beauty rendert aus der Kamera.")
    view_layer = context.view_layer
    images = os.path.join(root, "images")
    os.makedirs(images, exist_ok=True)

    planned: list[mf.PlannedRole] = []
    wanted: list[PassSpec] = []
    exr_allowed = allowed_media_types is None or EXR in allowed_media_types
    for role in roles:
        spec = PASS_BY_ROLE[role]
        path = f"images/{role}.exr"
        state, hint = pass_status(spec, scene, view_layer)
        if spec.blocked_by:
            planned.append(mf.PlannedRole(role, path, EXR, spec.blocked_note))
        elif state != "available":
            planned.append(mf.PlannedRole(role, path, EXR, f"Pass nicht eingeschaltet: {hint}"))
        elif not exr_allowed:
            planned.append(mf.PlannedRole(role, path, EXR,
                                          "Der Server nimmt image/x-exr nicht an (limits.allowedMediaTypes); "
                                          "Datenpässe gibt es nur als OpenEXR (QB-06)."))
        else:
            wanted.append(spec)

    beauty_path = os.path.join(images, "beauty.png")
    files: list[mf.CaptureFile] = []
    with _resolution(scene, size):
        try:
            result = bpy.ops.render.render(write_still=False)
        except RuntimeError as error:
            raise CaptureError(f"Blender hat das Rendering abgelehnt: {error}") from None
        if "FINISHED" not in result:
            raise CaptureError("Blender hat das Rendering nicht ausgeführt.")
        _save_png(scene, beauty_path)
        if wanted:
            multilayer = os.path.join(root, "passes-multilayer.exr")
            with _image_settings(scene, "MULTI_LAYER_IMAGE", "OPEN_EXR_MULTILAYER", "RGBA", "32", "NONE"):
                bpy.data.images["Render Result"].save_render(multilayer, scene=scene)
            with open(multilayer, "rb") as handle:
                data = handle.read()
            os.unlink(multilayer)
            available = {p["name"] for p in exr.parts(data)}
            for spec in wanted:
                part = f"{view_layer.name}.{spec.part}"
                if part not in available:
                    planned.append(mf.PlannedRole(spec.role, f"images/{spec.role}.exr", EXR,
                                                  f"Blender hat den Pass {spec.part} nicht geliefert."))
                    continue
                payload, description = exr.extract_pass(data, part)
                path = os.path.join(images, f"{spec.role}.exr")
                with open(path, "wb") as handle:
                    handle.write(payload)
                image = {"width": description["width"], "height": description["height"],
                         "colorSpace": spec.color_space, "bitDepth": description["bitDepth"],
                         "sampleFormat": description["sampleFormat"], "channels": description["channels"]}
                engine = ENGINE_LABELS.get(scene.render.engine, scene.render.engine)
                note = f"{spec.part} aus {engine}, OpenEXR {image['bitDepth']} Bit {image['sampleFormat']}, unkomprimiert."
                files.append(_file(path, root, spec.role, EXR, image, note))

    engine = ENGINE_LABELS.get(scene.render.engine, scene.render.engine)
    beauty = _file(beauty_path, root, "beauty", PNG, mf.describe_png(beauty_path),
                   f"Beauty Render (bpy.ops.render.render), {engine}; {color_note(scene)}.")
    order = [spec.role for spec in PASSES]
    files.sort(key=lambda f: order.index(f.role))
    planned.sort(key=lambda p: order.index(p.role))
    return beauty, files, planned
