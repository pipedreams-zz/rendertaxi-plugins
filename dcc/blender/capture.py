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
  View Transform angewendet) geschrieben.

Datenpässe (RTX-P-012, gemessen am 29.09.2026): je Rolle ein PNG mit 8 oder
16 Bit, geschrieben von Blender selbst während desselben Renderings — ein
File-Output-Knoten je Rolle in einer **vorübergehenden Kopie** der
Compositing-Gruppe (ohne Gruppe: eine Gruppe, die das Bild nur durchreicht).
Die Knoten rechnen die Kodierung des Vertrags (Tiefe normalisiert, Normalen
``(n + 1) / 2``, Indizes als Ganzzahl) und schreiben mit der Ansicht „Raw",
also ohne Farbumrechnung. Kein EXR, auch nicht als Zwischenschritt.

Blender 4.5 LTS (RTX-B-004, gemessen am 05.10.2026 mit 4.5.14) kennt keine
Compositing-Gruppe an der Szene (``Scene.compositing_node_group`` kam mit 5.0),
sondern einen in die Szene eingebetteten Baum (``Scene.node_tree``,
``use_nodes``), den man nicht austauschen kann. Dort rendert das Add-on die
Pässe deshalb aus einer **vorübergehenden Kopie der Szene** (``Scene.copy``:
dieselben Objekte, Kamera, Welt und View Layer, ein eigener Baum), deren Baum
es ergänzt — der Baum des Nutzers bleibt auch hier unverändert. Welcher Weg
gilt, entscheidet die Laufzeit an der API (``COMPOSITING_GROUPS``), nicht an
der Versionsnummer. ``ImageFormatSettings.media_type`` gibt es ebenfalls erst
ab 5.0; ohne es ist ``file_format`` allein maßgeblich.

Das Add-on **schaltet keinen Pass ein** und stellt keine Engine um. Welche
Pässe möglich sind, sagt die Laufzeitprobe; was fehlt, nennt der Hinweis. Jede
vorübergehende Änderung an der Szene (Auflösung, Ausgabeformat, Overlays,
Compositing-Gruppe) wird im ``finally`` zurückgesetzt; die Gruppe des Nutzers
selbst wird nie verändert.
"""

from __future__ import annotations

import os
import shutil
import uuid
from contextlib import contextmanager
from dataclasses import dataclass

import bpy

from . import export
from . import host  # noqa: F401 — Host des Clients
from .rendertaxi_client import manifest as mf
from .rendertaxi_client.frame import viewpoint_size  # noqa: F401 — Teil der Schnittstelle dieses Moduls

DOCUMENT_KEY_PROPERTY = "rendertaxi_document_key"
PNG = "image/png"
# Die Ansicht der Farbverwaltung, die nichts umrechnet (Blender-Konfiguration 4.5 bis 5.2).
RAW_VIEW = "Raw"
# Ab Blender 5.0: Compositing als austauschbare Gruppe an der Szene; 4.5: eingebetteter Baum.
COMPOSITING_GROUPS = "compositing_node_group" in bpy.types.Scene.bl_rna.properties
# Ab Blender 5.0: ``media_type`` vor ``file_format``; 4.5 kennt nur ``file_format``.
IMAGE_SETTINGS = tuple(name for name in
                       ("media_type", "file_format", "color_mode", "color_depth", "exr_codec", "compression")
                       if name in bpy.types.ImageFormatSettings.bl_rna.properties)

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
    socket: str  # Ausgang des Render-Layers-Knotens
    channels: str  # Kanäle im PNG: gray oder rgb
    color_space: str
    ui_path: str  # wo der Nutzer den Pass einschaltet
    extra_hint: str | None = None
    socket_4_5: str | None = None  # Name des Ausgangs in Blender 4.5, wenn er anders heißt (gemessen 4.5.14)

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


# Zuordnung aus capabilities.md, Abschnitt 3 (Passes, Blender 5.2); Kodierung Abschnitt 11.
PASSES: tuple[PassSpec, ...] = (
    PassSpec("depth", "depthPass", "Tiefe (Depth)", ("CYCLES", "BLENDER_EEVEE", "BLENDER_EEVEE_NEXT", "BLENDER_WORKBENCH"),
             "Depth", "gray", "non-color", "View Layer › Passes › Data › Z"),
    PassSpec("normal", "normalPass", "Normalen", ("CYCLES", "BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"),
             "Normal", "rgb", "non-color", "View Layer › Passes › Data › Normal"),
    PassSpec("albedo", "albedoPass", "Albedo", ("CYCLES",),
             "Denoising Albedo", "rgb", "linear", "View Layer › Passes › Data › Denoising Data (Cycles)"),
    PassSpec("object-id", "objectIdPass", "Objekt-ID", ("CYCLES",),
             "Object Index", "gray", "non-color", "View Layer › Passes › Data › Object Index (Cycles)",
             extra_hint="Objekte brauchen einen Pass-Index (Objekt › Relations).", socket_4_5="IndexOB"),
    PassSpec("material-id", "materialIdPass", "Material-ID", ("CYCLES",),
             "Material Index", "gray", "non-color", "View Layer › Passes › Data › Material Index (Cycles)",
             extra_hint="Materialien brauchen einen Pass-Index (Material › Settings).", socket_4_5="IndexMA"),
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
    ``unknown``. Datenpässe schreibt das Add-on nur als PNG (RTX-P-012).
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
        capabilities[spec.capability] = {"state": state, "constraints": {"mediaTypes": [PNG]}}
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
def _image_settings(scene, media_type: str, file_format: str, color_mode: str, color_depth: str):
    settings = scene.render.image_settings
    saved = {name: getattr(settings, name) for name in IMAGE_SETTINGS}
    try:
        if "media_type" in saved:
            settings.media_type = media_type
        settings.file_format = file_format
        settings.color_mode = color_mode
        settings.color_depth = color_depth
        yield
    finally:
        for name in IMAGE_SETTINGS:
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
    return mf.capture_file(path, root, "viewport", PNG, mf.describe_png(path), note)


def depth_range(scene) -> tuple[float, float] | None:
    """``near``/``far`` der Tiefe: Clipbereich der Kamera, Präzisionsklasse ``length`` (6 Stellen)."""
    camera = scene.camera.data
    near, far = round(camera.clip_start, 6), round(camera.clip_end, 6)
    return (near, far) if far > near >= 0 else None


def _highest_index(spec: PassSpec, scene) -> int:
    """Der höchste vergebene Pass-Index der Rolle — über alle Objekte der Szene, die rendern.

    Bewusst die Szene, nicht der View Layer: dessen Objektliste folgt erst nach der
    nächsten Auswertung, und ein Objekt zu viel macht die Prüfung nur strenger.
    """
    objects = [o for o in scene.objects if not o.hide_render]
    if spec.role == "object-id":
        return max((o.pass_index for o in objects), default=0)
    return max((slot.material.pass_index for o in objects for slot in o.material_slots if slot.material),
               default=0)


def pass_problem(spec: PassSpec, scene, bit_depth: int) -> str | None:
    """Warum ein eingeschalteter Pass **nicht** ``present`` werden darf — oder ``None``."""
    if spec.role == "depth":
        if scene.camera.data.type not in ("PERSP", "ORTHO"):
            return (f"Die Kamera ist vom Typ {scene.camera.data.type}; Tiefe entlang der Blickachse gibt es "
                    "nur für perspektivische und parallele Kameras.")
        if export.unit_problem(scene) is not None:
            return ("Für die Tiefe Scene › Units › Unit System „Metric“ oder „Imperial“ und Unit Scale 1 "
                    "wählen.")
        if depth_range(scene) is None:
            return "Der Clipbereich der Kamera ist leer (Clip End nicht größer als Clip Start)."
    if spec.role in ("object-id", "material-id"):
        top = (1 << bit_depth) - 1
        highest = _highest_index(spec, scene)
        if highest > top:
            return (f"Ein Pass-Index ist {highest}, mit {bit_depth} Bit passen höchstens {top}. "
                    f"{mf.DATA_PASS_BIT_DEPTH_LABEL}: 16 Bit wählen.")
    return None


def _math(tree, operation: str, first, second=None, clamp: bool = False):
    node = tree.nodes.new("ShaderNodeMath")
    node.operation = operation
    node.use_clamp = clamp
    for index, value in enumerate((first, second)):
        if isinstance(value, (int, float)):
            node.inputs[index].default_value = value
        elif value is not None:
            tree.links.new(value, node.inputs[index])
    return node.outputs[0]


def _vector_math(tree, operation: str, first, *constants: float):
    node = tree.nodes.new("ShaderNodeVectorMath")
    node.operation = operation
    tree.links.new(first, node.inputs[0])
    for index, value in enumerate(constants, start=1):
        node.inputs[index].default_value = (value, value, value)
    return node.outputs[0]


def _encoded(tree, spec: PassSpec, source, bit_depth: int, depth: tuple[float, float] | None):
    """Der Pass in der Kodierung des Vertrags (capture-manifest.md, Abschnitt 12)."""
    if spec.role == "depth":
        near, far = depth
        # Blender liefert die Tiefe entlang der Blickachse (gemessen, QB-01); kein Treffer ist 1e10 → 1.
        return _math(tree, "DIVIDE", _math(tree, "SUBTRACT", source, near), far - near, clamp=True)
    if spec.role == "normal":
        # Weltraum (QB-02); normiert, weil Cycles an Kanten gemittelte, kürzere Vektoren liefert.
        return _vector_math(tree, "MULTIPLY_ADD", _vector_math(tree, "NORMALIZE", source), 0.5, 0.5)
    if spec.role in ("object-id", "material-id"):
        return _math(tree, "DIVIDE", _math(tree, "ROUND", source), float((1 << bit_depth) - 1))
    return source  # albedo: linear, wie Blender ihn rechnet


def _file_output(tree, spec: PassSpec, directory: str, bit_depth: int):
    """Ein File-Output-Knoten: PNG, Ansicht „Raw" — Blender quantisiert, ohne umzurechnen.

    5.x schreibt ``<directory>/<role>.png``; 4.5 hängt an den Pfad des Slots die
    Bildnummer an (``<role>0001.png``) — ``_written`` findet beide.
    """
    node = tree.nodes.new("CompositorNodeOutputFile")
    image_format = node.format
    if COMPOSITING_GROUPS:
        node.directory = directory
        node.file_name = ""
        image_format.media_type = "IMAGE"
    else:
        node.base_path = directory
    image_format.file_format = "PNG"
    image_format.color_mode = "BW" if spec.channels == "gray" else "RGB"
    image_format.color_depth = str(bit_depth)
    # Gemessen: ohne „Save As Render" rechnet Blender 8-Bit-RGB trotz „Non-Color" nach sRGB um;
    # mit der Ansicht „Raw" steht jeder Wert unverändert im PNG (capabilities.md, Abschnitt 11).
    node.save_as_render = True
    image_format.color_management = "OVERRIDE"
    view = image_format.view_settings
    view.view_transform = RAW_VIEW  # TypeError, wenn die Farbverwaltung „Raw" nicht kennt
    view.look = "None"
    view.exposure = 0.0
    view.gamma = 1.0
    view.use_curve_mapping = False
    if not COMPOSITING_GROUPS:
        # 4.5: ein Slot mit Farbeingang; Blender wandelt Zahl und Vektor beim Verbinden selbst.
        node.file_slots.clear()
        node.file_slots.new(spec.role)
        return node.inputs[0]
    kind = "FLOAT" if spec.channels == "gray" else ("VECTOR" if spec.role == "normal" else "RGBA")
    item = node.file_output_items.new(kind, spec.role)
    return node.inputs[item.name]


def _written(directory: str, role: str) -> str | None:
    """Die Datei, die der File-Output-Knoten der Rolle geschrieben hat — ``None``, wenn keine."""
    exact = os.path.join(directory, f"{role}.png")
    if os.path.isfile(exact):
        return exact
    numbered = [name for name in (os.listdir(directory) if os.path.isdir(directory) else ())
                if name.startswith(role) and name.endswith(".png") and name[len(role):-4].isdigit()]
    return os.path.join(directory, numbered[0]) if len(numbered) == 1 else None


def _pass_tree(scene, view_layer, specs: list[PassSpec], directory: str, bit_depth: int):
    """Die vorübergehende Compositing-Gruppe: die des Nutzers als Kopie, dazu je Rolle ein File Output.

    Gemessen am 29.09.2026: die Kopie liefert dasselbe Beauty-Bild wie die Gruppe
    des Nutzers, die durchreichende Gruppe dasselbe wie „ohne Compositing".
    """
    base = scene.compositing_node_group if scene.render.use_compositing else None
    if base is not None:
        tree = base.copy()
    else:
        tree = bpy.data.node_groups.new("rendertaxi Datenpässe", "CompositorNodeTree")
    try:
        layers = tree.nodes.new("CompositorNodeRLayers")
        layers.scene = scene
        layers.layer = view_layer.name
        if base is None:
            tree.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
            output = tree.nodes.new("NodeGroupOutput")
            tree.links.new(layers.outputs["Image"], output.inputs[0])
        _connect_passes(tree, layers, scene, specs, directory, bit_depth)
    except Exception:
        bpy.data.node_groups.remove(tree)
        raise
    return tree


def _connect_passes(tree, layers, scene, specs: list[PassSpec], directory: str, bit_depth: int) -> None:
    depth = depth_range(scene) if scene.camera else None
    for spec in specs:
        source = layers.outputs.get(spec.socket)
        if source is None and spec.socket_4_5:
            source = layers.outputs.get(spec.socket_4_5)
        if source is None or not source.enabled:
            continue  # fehlt nach dem Rendern und wird dann „geplant"
        tree.links.new(_encoded(tree, spec, source, bit_depth, depth), _file_output(tree, spec, directory, bit_depth))


def _pass_scene(scene, view_layer, specs: list[PassSpec], directory: str, bit_depth: int):
    """Blender 4.5: eine vorübergehende Kopie der Szene, deren eigener Baum die File Outputs trägt.

    ``Scene.copy`` verknüpft Objekte, Sammlungen, Kamera und Welt und kopiert
    Render-Einstellungen, View Layer und den eingebetteten Baum. Ist das
    Compositing des Nutzers aus (``use_nodes`` oder ``use_compositing``), reicht
    der Baum der Kopie das Bild nur durch — wie ohne Compositing.
    """
    copy = scene.copy()
    try:
        copy.pop(DOCUMENT_KEY_PROPERTY, None)  # die Kennung gehört der Szene des Nutzers
        active = scene.use_nodes and scene.render.use_compositing and scene.node_tree is not None
        copy.use_nodes = True
        copy.render.use_compositing = True
        tree = copy.node_tree
        if not active:
            tree.nodes.clear()
        layers = None
        for node in tree.nodes:
            if node.bl_idname == "CompositorNodeRLayers" and node.scene in (scene, copy):
                node.scene = copy  # die Kopie rendert sich selbst, nicht die Szene des Nutzers ein zweites Mal
                if layers is None and node.layer == view_layer.name:
                    layers = node
        if layers is None:
            layers = tree.nodes.new("CompositorNodeRLayers")
            layers.scene = copy
            layers.layer = view_layer.name
        if not active:
            composite = tree.nodes.new("CompositorNodeComposite")
            tree.links.new(layers.outputs["Image"], composite.inputs[0])
        _connect_passes(tree, layers, scene, specs, directory, bit_depth)
    except Exception:
        bpy.data.scenes.remove(copy)
        raise
    return copy


def _prepare_passes(scene, view_layer, specs: list[PassSpec], directory: str, bit_depth: int):
    """Das Vorübergehende für die Pässe: Gruppe (5.x) oder Szenenkopie (4.5); ``None`` ohne Pässe."""
    if not specs:
        return None
    if COMPOSITING_GROUPS:
        return _pass_tree(scene, view_layer, specs, directory, bit_depth)
    return _pass_scene(scene, view_layer, specs, directory, bit_depth)


@contextmanager
def _compositing(scene, prepared):
    """Die Szene, die rendert — danach ist die des Nutzers, wie sie war, und das Vorübergehende weg.

    5.x: die Gruppe nur für dieses Rendering an der Szene des Nutzers einsetzen.
    4.5: die Kopie rendern; die Szene des Nutzers wird nicht angefasst.
    """
    if prepared is None:
        yield scene
        return
    if isinstance(prepared, bpy.types.Scene):
        try:
            yield prepared
        finally:
            bpy.data.scenes.remove(prepared)
        return
    saved = (scene.compositing_node_group, scene.render.use_compositing)
    try:
        scene.compositing_node_group = prepared
        scene.render.use_compositing = True
        yield scene
    finally:
        scene.compositing_node_group, scene.render.use_compositing = saved
        bpy.data.node_groups.remove(prepared)


def _note(spec: PassSpec, engine: str, bit_depth: int, depth: tuple[float, float] | None) -> str:
    what = {
        "depth": f"Tiefe entlang der Blickachse, normalized-linear zwischen near {depth[0] if depth else 0:g} m "
                 f"und far {depth[1] if depth else 0:g} m (Clipbereich der Kamera), kein Treffer = 1",
        "normal": "Weltraum, normiert, (n + 1) / 2 je Kanal, kein Treffer = 0,5",
        "albedo": "linear, ohne View Transform",
        "object-id": "Pass-Index des Objekts als Ganzzahl, ohne Kantenglättung",
        "material-id": "Pass-Index des Materials als Ganzzahl, ohne Kantenglättung",
    }[spec.role]
    return f"{spec.socket} aus {engine}, PNG {bit_depth} Bit; {what}."


def capture_beauty(context, root: str, size: tuple[int, int] | None, roles: list[str],
                   allowed_media_types: list[str] | None, bit_depth: int = mf.DEFAULT_DATA_PASS_BIT_DEPTH):
    """Beauty als PNG und die gewählten Pässe als PNG — ``(beauty, pass_files, planned)``.

    Ein gewählter Pass wird ``planned`` mit Begründung, wenn er nicht
    eingeschaltet ist, wenn seine Kodierung in dieser Szene nicht belegt ist
    (``pass_problem``) oder wenn der Server ``image/png`` nicht annimmt — kein
    stiller Umweg über ein anderes Format. ``bit_depth`` ist die Wahl des
    Nutzers (8 oder 16); ein ungültiger Wert wird zu 8 Bit.
    """
    scene = context.scene
    if scene.camera is None:
        raise CaptureError("Die Szene hat keine aktive Kamera — Beauty rendert aus der Kamera.")
    bit_depth = mf.data_pass_bit_depth(bit_depth)
    view_layer = context.view_layer
    images = os.path.join(root, "images")
    os.makedirs(images, exist_ok=True)

    planned: list[mf.PlannedRole] = []
    wanted: list[PassSpec] = []
    png_allowed = allowed_media_types is None or PNG in allowed_media_types
    for role in roles:
        spec = PASS_BY_ROLE[role]
        path = f"images/{role}.png"
        state, hint = pass_status(spec, scene, view_layer)
        problem = pass_problem(spec, scene, bit_depth) if state == "available" else None
        if state != "available":
            planned.append(mf.PlannedRole(role, path, PNG, f"Pass nicht eingeschaltet: {hint}"))
        elif problem:
            planned.append(mf.PlannedRole(role, path, PNG, problem))
        elif not png_allowed:
            planned.append(mf.PlannedRole(role, path, PNG,
                                          "Der Server nimmt image/png nicht an (limits.allowedMediaTypes)."))
        else:
            wanted.append(spec)

    beauty_path = os.path.join(images, "beauty.png")
    staging = os.path.join(root, "passes-staging")
    files: list[mf.CaptureFile] = []
    engine = ENGINE_LABELS.get(scene.render.engine, scene.render.engine)
    try:
        with _resolution(scene, size):
            # Nach der Auflösung: die Szenenkopie (4.5) übernimmt sie.
            prepared = None
            try:
                prepared = _prepare_passes(scene, view_layer, wanted, staging, bit_depth)
            except (TypeError, ValueError, RuntimeError):
                # Etwa eine eigene OCIO-Konfiguration ohne die Ansicht „Raw": dann kein Pass, aber das Bild.
                for spec in wanted:
                    planned.append(mf.PlannedRole(spec.role, f"images/{spec.role}.png", PNG,
                                                  "Blender kann den Pass hier nicht ohne Farbumrechnung schreiben "
                                                  f"(Farbverwaltung ohne Ansicht „{RAW_VIEW}“)."))
                wanted = []
            with _compositing(scene, prepared) as rendering:
                try:
                    if rendering is scene:
                        result = bpy.ops.render.render(write_still=False)
                    else:
                        result = bpy.ops.render.render(write_still=False, scene=rendering.name,
                                                       layer=view_layer.name)
                except RuntimeError as error:
                    raise CaptureError(f"Blender hat das Rendering abgelehnt: {error}") from None
                if "FINISHED" not in result:
                    raise CaptureError("Blender hat das Rendering nicht ausgeführt.")
                # Noch in der Klammer und mit der Szene, die gerendert hat: das Render Result gehört
                # ihr, und mit der Szenenkopie (4.5) verschwindet es (gemessen).
                _save_png(rendering, beauty_path)
        depth = depth_range(scene)
        for spec in wanted:
            written = _written(staging, spec.role)
            path = os.path.join(images, f"{spec.role}.png")
            image = None
            if written is not None:
                os.replace(written, path)
                image = dict(mf.describe_png(path), colorSpace=spec.color_space)
            if image is None or image["channels"] != spec.channels or image["bitDepth"] != bit_depth:
                planned.append(mf.PlannedRole(spec.role, f"images/{spec.role}.png", PNG,
                                              f"Blender hat den Pass {spec.socket} nicht geliefert."))
                if os.path.exists(path):
                    os.unlink(path)
                continue
            files.append(mf.capture_file(
                path, root, spec.role, PNG, image, _note(spec, engine, bit_depth, depth),
                depth={"encoding": "normalized-linear", "near": depth[0], "far": depth[1]}
                if spec.role == "depth" else None,
                normal={"space": "world"} if spec.role == "normal" else None))
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    beauty = mf.capture_file(beauty_path, root, "beauty", PNG, mf.describe_png(beauty_path),
                   f"Beauty Render (bpy.ops.render.render), {engine}; {color_note(scene)}.")
    order = [spec.role for spec in PASSES]
    files.sort(key=lambda f: order.index(f.role))
    planned.sort(key=lambda p: order.index(p.role))
    return beauty, files, planned
