"""Modellweg (RTX-B-003, #183) — GLB und Kamera für Capture-Manifest 1.2.0.

Vertrag: ``integrations/_shared/docs/capture-manifest.md``, Abschnitt 11
(ADR 0032). Die Datei folgt glTF 2.0 — Meter, rechtshändig, +Y oben —, die
Kamera steht im selben **Exportraum**. Was hier gilt, ist gemessen
(``integrations/blender/docs/open-questions.md``, QB-03, QB-04, QB-08; Tests
``tests/test_model.py`` und ``tests/test_modelview.py``):

* **Achsen (QB-08).** Blenders Weltraum ist rechtshändig mit +Z oben: in der
  Draufsicht zeigt +X nach rechts, +Y nach oben, +Z zum Betrachter. Der
  glTF-Exporter bildet mit „+Y Up" einen Punkt ``(x, y, z)`` auf
  ``(x, z, −y)`` ab — eine Drehung um −90° um X, die Händigkeit bleibt.
* **Einheiten (QB-03).** Der glTF-Exporter schreibt interne Einheiten
  unverändert, ``scale_length`` wirkt nicht; der USD-Exporter setzt
  ``metersPerUnit = 1`` ebenso unabhängig davon. Die Oberfläche zeigt dagegen
  ``intern × scale_length``. Beide Lesarten stimmen nur bei Unit Scale 1
  überein — nur dann ist „eine Einheit = ein Meter" belegt. Mit anderem Unit
  Scale oder Einheitensystem „None" sendet das Add-on **kein** Modell, statt
  einen Maßstab zu raten.
* **Sichtfeld (QB-04).** Gemessen am gerenderten Bild: Sensor Fit ``AUTO``
  legt ``sensor_width`` auf die **längere** Bildseite (Hochformat: senkrecht,
  **nicht** ``angle_y``), ``HORIZONTAL`` ``sensor_width`` auf die Breite,
  ``VERTICAL`` ``sensor_height`` auf die Höhe; der Winkel ist
  ``2·atan(sensor / (2·lens))``. Die 3D-Ansicht ohne Kamera rechnet mit 72 mm
  (36 mm Sensor, Zoomfaktor 2) auf der längeren Seite, parallel mit der halben
  Ausdehnung ``view_distance · 36 / lens``. ``shift_x``/``shift_y``
  verschieben das Bild um diesen Bruchteil der Seite, die der Sensor Fit
  nennt (bei AUTO der längeren).
* **Objektiv und Shift (Manifest 1.4.0, RTX-M2-026).** Setzt der Server 1.4.0
  um, trägt der Kamerablock einer Kamera der Szene zusätzlich ``lens``
  (Brennweite, Sensorgröße auf der Seite des Sensor Fit, Sensor Fit) und
  ``shift`` als Anteil der **längeren** Bildseite — umgerechnet
  ``shift · Seite des Sensor Fit / längere Seite``. Gegen einen älteren Server
  gilt weiter: eine Kamera mit Shift wird nicht gesendet.
* **Kameras in der Datei (RTX-M2-026).** Die GLB-Datei trägt die sichtbaren
  Kameras der Szene (glTF ``cameras[]``, Knotennamen = Objektnamen), keine
  Lichter. Die Plattform liest sie nur im Browser, als Liste zum Übernehmen.
* **Objektiv und Shift jeder Kamera (RTX-B-004).** glTF kennt beides nicht.
  Nach dem Export schreibt das Add-on je Kamera ``lens`` und ``shift`` in der
  Form des Manifests 1.4.0 nach ``cameras[i].extras.rendertaxi.camera``
  (``rendertaxi_client.glb.set_camera_extras``) — nur diesen einen Schlüssel,
  ``export_extras`` bleibt aus (keine Custom Properties). Der Shift wird mit dem
  Seitenverhältnis der Szene auf die längere Seite umgerechnet, demselben, mit
  dem der Exporter ``yfov`` und ``aspectRatio`` schreibt.

Das Modul erfindet nichts: was der Vertrag nicht darstellen kann, führt zu
``None`` mit einem Grund, den das Panel **vor** dem Senden zeigt.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass

from .rendertaxi_client import glb
from .rendertaxi_client import manifest as mf

MODEL_PATH = glb.MODEL_PATH
MODEL_MEDIA_TYPE = mf.MODEL_MEDIA_TYPE

# Die Grenze des Servers, aus dem gemeinsamen Client; hier gelesen, damit ein
# Test sie für diesen Lauf senken kann.
MAX_TRIANGLES = glb.MAX_TRIANGLES

# Die 3D-Ansicht ohne Kamera: 36 mm Sensor, Zoomfaktor 2 (gemessen, QB-04).
VIEWPORT_SENSOR_MM = 36.0
VIEWPORT_ZOOM = 2.0

GEOMETRY_TYPES = ("MESH", "CURVE", "SURFACE", "FONT", "META", "CURVES")
UNIT_TOLERANCE = 1e-9


class ExportError(RuntimeError):
    """Ein Grund, warum das Modell nicht gesendet wird — lesbar, ohne Pfad."""


# --------------------------------------------------------------------------
# Zahlen und Raum (ohne bpy)
# --------------------------------------------------------------------------


def _num(value: float, digits: int):
    """Auf die Präzisionsklasse gerundet; ganze Werte als Ganzzahl, nie −0."""
    rounded = round(float(value), digits)
    if rounded == 0:
        return 0
    return int(rounded) if rounded.is_integer() else rounded


def length(value: float):
    return _num(value, 6)


def angle(value: float):
    return _num(value, 9)


def to_export_point(point, meters_per_unit: float = 1.0) -> list:
    """Ein Punkt aus Blenders Weltraum (Z oben) in den Exportraum (Y oben, Meter)."""
    x, y, z = (float(c) * meters_per_unit for c in point)
    return [length(x), length(z), length(-y)]


def to_export_direction(vector) -> list:
    """Eine Richtung, normiert und auf neun Nachkommastellen, im Exportraum."""
    x, y, z = (float(c) for c in vector)
    norm = math.sqrt(x * x + y * y + z * z)
    if norm == 0:
        raise ExportError("Die Kamera hat keine Blickrichtung.")
    return [_num(x / norm, 9), _num(z / norm, 9), _num(-y / norm, 9)]


def fitted_axis(sensor_fit: str, width: int, height: int) -> str:
    """Auf welche Bildseite der Sensor gelegt wird (QB-04, gemessen)."""
    if sensor_fit == "AUTO":
        return "horizontal" if width >= height else "vertical"
    return "horizontal" if sensor_fit == "HORIZONTAL" else "vertical"


def perspective_fov(sensor_fit: str, sensor_width: float, sensor_height: float, lens: float,
                    width: int, height: int) -> tuple[str, float]:
    """``(Achse, Winkel im Bogenmaß)`` einer perspektivischen Kamera für ein Bild ``width × height``."""
    axis = fitted_axis(sensor_fit, width, height)
    sensor = sensor_height if sensor_fit == "VERTICAL" else sensor_width
    return axis, 2.0 * math.atan(sensor / (2.0 * lens))


def orthographic_extent(sensor_fit: str, full_fitted: float, width: int, height: int) -> tuple[float, float]:
    """``(halbe Breite, halbe Höhe)`` aus der vollen Ausdehnung auf der Sensorseite."""
    half = full_fitted / 2.0
    if fitted_axis(sensor_fit, width, height) == "horizontal":
        return half, half * height / width
    return half * width / height, half


def camera_dict(*, projection: str, position, direction, up, near: float, far: float | None,
                fov: tuple[str, float] | None = None, extent: tuple[float, float] | None = None,
                meters_per_unit: float = 1.0, lens: dict | None = None,
                shift: tuple[float, float] | None = None) -> dict:
    """Der Kamerablock des Vertrags (Abschnitt 11.3) aus Werten in Blenders Weltraum.

    ``lens`` und ``shift`` (seit 1.4.0) stehen nur, wenn sie genannt sind; ein
    Shift ``(0, 0)`` entfällt.
    """
    near_m = max(length(near * meters_per_unit), 0.000001)
    block: dict = {
        "space": "export",
        "projection": projection,
        "position": to_export_point(position, meters_per_unit),
        "direction": to_export_direction(direction),
        "up": to_export_direction(up),
    }
    if projection == "perspective":
        block["fieldOfView"] = {"axis": fov[0], "angle": angle(fov[1])}
    else:
        block["extent"] = {"halfWidth": length(extent[0] * meters_per_unit),
                           "halfHeight": length(extent[1] * meters_per_unit)}
    far_m = None if far is None else length(far * meters_per_unit)
    if far_m is not None and far_m <= near_m:
        far_m = None
    block["clip"] = {"near": near_m, "far": far_m}
    if lens is not None and projection == "perspective":
        block["lens"] = lens
    if shift is not None and (_num(shift[0], 6) != 0 or _num(shift[1], 6) != 0):
        block["shift"] = {"x": _num(shift[0], 6), "y": _num(shift[1], 6)}
    return block


# Die Grenzen des Objektivs im Vertrag (capture-manifest.md, Abschnitt 11.3).
FOCAL_LENGTH_MM = (1.0, 1200.0)
SENSOR_MM = (1.0, 300.0)
SHIFT_LIMIT = 2.0


def lens_dict(sensor_fit: str, sensor_width: float, sensor_height: float, lens: float) -> dict | None:
    """``camera.lens`` (1.4.0): Brennweite, Sensorgröße auf der Seite des Sensor Fit, Sensor Fit.

    ``None``, wenn ein Wert außerhalb des Vertrags liegt — dann bleibt es beim Winkel.
    """
    sensor = sensor_height if sensor_fit == "VERTICAL" else sensor_width
    if not (FOCAL_LENGTH_MM[0] <= lens <= FOCAL_LENGTH_MM[1] and SENSOR_MM[0] <= sensor <= SENSOR_MM[1]):
        return None
    return {"focalLengthMm": length(lens), "sensorWidthMm": length(sensor), "sensorFit": sensor_fit.lower()}


def contract_shift(sensor_fit: str, shift_x: float, shift_y: float, width: int, height: int) -> tuple[float, float]:
    """Blenders Shift (Anteil der Seite des Sensor Fit) als Anteil der **längeren** Bildseite (1.4.0)."""
    longer = max(width, height)
    fitted = {"HORIZONTAL": width, "VERTICAL": height}.get(sensor_fit, longer)
    return shift_x * fitted / longer, shift_y * fitted / longer


def geometry_dict(meters_per_unit: float = 1.0) -> dict:
    """``geometry`` für die GLB-Datei: Blenders Weltraum, unverschoben exportiert."""
    return {
        "assetPath": MODEL_PATH,
        "units": {"sourceUnitScaleToMeter": _num(meters_per_unit, 6)},
        "axes": {"handedness": "right", "upAxis": "z"},
        "origin": [0, 0, 0],
    }


# --------------------------------------------------------------------------
# GLB lesen — der gemeinsame Client (``rendertaxi_client.glb``), ein Weg für alle Plugins
# --------------------------------------------------------------------------


def read_glb_json(path: str) -> dict:
    """Den JSON-Chunk einer GLB-Datei — oder ``ExportError``."""
    try:
        return glb.read_glb_json(path)
    except glb.GlbError as error:
        raise ExportError(str(error)) from None


glb_triangles = glb.glb_triangles
external_references = glb.external_references


# --------------------------------------------------------------------------
# Blender
# --------------------------------------------------------------------------


def unit_problem(scene) -> str | None:
    """Warum der Maßstab nicht belegt ist (QB-03) — oder ``None`` bei einer Einheit = ein Meter."""
    units = scene.unit_settings
    if units.system == "NONE":
        return ("Einheitensystem „None“ hat keinen Bezug zu Metern (QB-03). Für das Modell "
                "Scene › Units › Unit System „Metric“ oder „Imperial“ wählen.")
    if abs(units.scale_length - 1.0) > UNIT_TOLERANCE:
        return (f"Unit Scale ist {units.scale_length:g}, nicht 1: der Maßstab des Modells ist dann nicht "
                "eindeutig (QB-03). Das Modell wird nicht gesendet.")
    return None


def meters_per_unit(scene) -> float:
    problem = unit_problem(scene)
    if problem:
        raise ExportError(problem)
    return 1.0


def _camera_problem(camera, scene, lens_and_shift: bool = False) -> str | None:
    data = camera.data
    if data.type not in ("PERSP", "ORTHO"):
        return f"Die Kamera ist vom Typ {data.type}; der Vertrag kennt nur perspektivisch und parallel."
    shifted = abs(data.shift_x) > UNIT_TOLERANCE or abs(data.shift_y) > UNIT_TOLERANCE
    if shifted and not lens_and_shift:
        return "Die Kamera hat Shift; der Server kennt die Bildverschiebung erst ab Manifest 1.4.0 (QB-04)."
    if max(abs(data.shift_x), abs(data.shift_y)) > SHIFT_LIMIT:
        return "Der Shift der Kamera liegt außerhalb von ±2; der Vertrag trägt ihn nicht."
    render = scene.render
    if abs(render.pixel_aspect_x - render.pixel_aspect_y) > UNIT_TOLERANCE:
        return "Pixel Aspect ist nicht 1:1; das Sichtfeld wäre auf der zweiten Achse falsch."
    return None


def camera_problem(context, kind: str, lens_and_shift: bool = False) -> str | None:
    """Warum **keine** Kamera mitgeht — für das Panel, bevor gesendet wird.

    ``lens_and_shift``: der Server setzt Manifest 1.4.0 um (``mf.camera_lens_allowed``).
    """
    scene = context.scene
    if kind == "BEAUTY" or _view_uses_camera(context):
        if scene.camera is None:
            return "Die Szene hat keine aktive Kamera."
        return _camera_problem(scene.camera, scene, lens_and_shift)
    if _region(context) is None:
        return "Keine 3D-Ansicht offen."
    if abs(scene.render.pixel_aspect_x - scene.render.pixel_aspect_y) > UNIT_TOLERANCE:
        return "Pixel Aspect ist nicht 1:1; das Sichtfeld wäre auf der zweiten Achse falsch."
    return None


def _region(context):
    from . import capture

    found = capture.find_view3d(context)
    if found is None:
        return None
    _window, area, region = found
    return area.spaces.active, region.data


def _view_uses_camera(context) -> bool:
    found = _region(context)
    return found is not None and found[1].view_perspective == "CAMERA"


def capture_camera_name(context, kind: str) -> str | None:
    """Der Name des Kameraobjekts, aus dem aufgenommen wird — ``None`` ohne Kameraobjekt.

    Beauty rendert aus der Szenenkamera; der Viewport nur dann, wenn die 3D-Ansicht durch sie schaut.
    Der Name ist der Vorschlag für einen neuen Blickpunkt (RTX-P-013).
    """
    scene = context.scene
    if scene is None or scene.camera is None:
        return None
    if kind != "BEAUTY" and not _view_uses_camera(context):
        return None
    return scene.camera.name.strip() or None


def camera_from_object(camera, scene, size: tuple[int, int], meters: float, lens_and_shift: bool = False) -> dict:
    from mathutils import Vector

    data = camera.data
    _location, rotation, _scale = camera.matrix_world.decompose()
    turn = rotation.to_matrix()
    direction = turn @ Vector((0.0, 0.0, -1.0))
    up = turn @ Vector((0.0, 1.0, 0.0))
    width, height = size
    common = {"position": camera.matrix_world.translation, "direction": direction, "up": up,
              "near": data.clip_start, "far": data.clip_end, "meters_per_unit": meters}
    if lens_and_shift:
        common["shift"] = contract_shift(data.sensor_fit, data.shift_x, data.shift_y, width, height)
        if data.type == "PERSP":
            common["lens"] = lens_dict(data.sensor_fit, data.sensor_width, data.sensor_height, data.lens)
    if data.type == "ORTHO":
        extent = orthographic_extent(data.sensor_fit, data.ortho_scale, width, height)
        return camera_dict(projection="orthographic", extent=extent, **common)
    fov = perspective_fov(data.sensor_fit, data.sensor_width, data.sensor_height, data.lens, width, height)
    return camera_dict(projection="perspective", fov=fov, **common)


def camera_from_view(space, region_3d, size: tuple[int, int], meters: float) -> dict:
    """Die 3D-Ansicht ohne Kamera, wie ``bpy.ops.render.opengl(view_context=True)`` sie rendert."""
    from mathutils import Vector

    inverse = region_3d.view_matrix.inverted()
    turn = inverse.to_3x3().normalized()
    direction = turn @ Vector((0.0, 0.0, -1.0))
    up = turn @ Vector((0.0, 1.0, 0.0))
    eye = inverse.translation
    width, height = size
    sensor = VIEWPORT_SENSOR_MM * VIEWPORT_ZOOM
    if region_3d.view_perspective == "ORTHO":
        # Parallel liegt das Auge in der Bildmitte, geschnitten wird ±clip_end/2
        # (gemessen, QB-04): der Vertrag verlangt near > 0, also zurückgesetzt.
        full = region_3d.view_distance * sensor / space.lens
        extent = orthographic_extent("AUTO", full, width, height)
        back = eye - direction.normalized() * (space.clip_end / 2.0)
        return camera_dict(projection="orthographic", position=back, direction=direction, up=up,
                           near=space.clip_start, far=space.clip_end, extent=extent, meters_per_unit=meters)
    fov = perspective_fov("AUTO", sensor, sensor, space.lens, width, height)
    return camera_dict(projection="perspective", position=eye, direction=direction, up=up,
                       near=space.clip_start, far=space.clip_end, fov=fov, meters_per_unit=meters)


def camera_block(context, kind: str, size: tuple[int, int], lens_and_shift: bool = False) -> dict | None:
    """Die Kamera der Aufnahme — ``None``, wenn ``camera_problem`` einen Grund nennt.

    ``kind`` ist ``VIEWPORT`` oder ``BEAUTY``; ``size`` die Maße des
    aufgenommenen Bildes (nicht der Szene: „Blickpunkt-Rahmen" ändert sie).
    ``lens_and_shift``: Objektiv und Shift mitsenden (Manifest 1.4.0).
    """
    scene = context.scene
    meters = meters_per_unit(scene)
    if camera_problem(context, kind, lens_and_shift):
        return None
    if kind == "BEAUTY" or _view_uses_camera(context):
        return camera_from_object(scene.camera, scene, size, meters, lens_and_shift)
    space, region_3d = _region(context)
    return camera_from_view(space, region_3d, size, meters)


def geometry_block(context, directory: str) -> dict | None:
    """``geometry`` für die Datei, die ``export_model`` in ``directory`` geschrieben hat."""
    if not os.path.exists(os.path.join(directory, *MODEL_PATH.split("/"))):
        return None
    return geometry_dict(meters_per_unit(context.scene))


@dataclass
class Estimate:
    objects: int
    triangles: int


def estimate(context) -> Estimate:
    """Sichtbare Objekte und Dreiecke — der Hinweis im Panel, **bevor** exportiert wird."""
    depsgraph = context.evaluated_depsgraph_get()
    view_layer = context.view_layer
    objects = 0
    triangles = 0
    for instance in depsgraph.object_instances:
        obj = instance.object
        if obj.type not in GEOMETRY_TYPES:
            continue
        owner = instance.parent if instance.is_instance else obj
        try:
            if not owner.original.visible_get(view_layer=view_layer):
                continue
        except (AttributeError, RuntimeError):
            continue
        mesh = obj.data if obj.type == "MESH" else None
        temporary = False
        if mesh is None:
            try:
                mesh = obj.to_mesh()
                temporary = True
            except RuntimeError:
                continue
        if mesh is not None:
            objects += 1
            triangles += sum(p.loop_total - 2 for p in mesh.polygons)
        if temporary:
            obj.to_mesh_clear()
    return Estimate(objects, triangles)


def export_model(context, directory: str, include_materials: bool = False) -> mf.CaptureFile:
    """Die sichtbaren Objekte als GLB nach ``directory/model/scene.glb``.

    Eingebauter glTF-Exporter (``bpy.ops.export_scene.gltf``), „+Y Up",
    Modifier angewendet, ohne Animation, Lichter und Custom Properties;
    **mit** den sichtbaren Kameras der Szene (Namen der Objekte, RTX-M2-026);
    Materialien und Texturen nur auf Wunsch (dann in die Datei eingebettet,
    nie daneben). Die Datei bleibt im Capture-Verzeichnis.
    """
    import bpy

    scene = context.scene
    meters_per_unit(scene)
    target = os.path.join(directory, *MODEL_PATH.split("/"))
    os.makedirs(os.path.dirname(target), mode=0o700, exist_ok=True)
    for obj in getattr(context, "objects_in_mode", None) or []:
        obj.update_from_editmode()
    try:
        result = bpy.ops.export_scene.gltf(
            filepath=target,
            check_existing=False,
            export_format="GLB",
            use_visible=True,
            use_active_scene=True,
            export_yup=True,
            export_apply=True,
            export_animations=False,
            export_skins=False,
            export_morph=False,
            export_cameras=True,
            export_lights=False,
            export_extras=False,
            export_copyright="",
            export_materials="EXPORT" if include_materials else "NONE",
            export_image_format="AUTO" if include_materials else "NONE",
            export_texcoords=include_materials,
            export_keep_originals=False,
            will_save_settings=False,
        )
    except RuntimeError:
        # Blenders Meldung trägt den absoluten Pfad: nicht weiterreichen.
        raise ExportError("Blender hat den glTF-Export abgelehnt.") from None
    if "FINISHED" not in result or not os.path.exists(target):
        raise ExportError("Blender hat den glTF-Export nicht ausgeführt.")
    _write_camera_extras(scene, target)
    document = read_glb_json(target)
    if external_references(document):
        raise ExportError("Die GLB-Datei verweist auf Dateien außerhalb ihrer selbst.")
    triangles = glb_triangles(document)
    if triangles == 0:
        raise ExportError("Keine sichtbare Geometrie: das Modell wäre leer.")
    if triangles > MAX_TRIANGLES:
        raise ExportError(f"Das Modell hat {triangles:,} Dreiecke; der Server nimmt höchstens "
                          f"{MAX_TRIANGLES:,} an.".replace(",", " "))
    sha, size = mf.sha256_file(target)
    note = (f"glTF 2.0 binär aus dem glTF-Exporter von Blender: sichtbare Objekte, Modifier angewendet, "
            f"+Y oben, ohne Animation; {'mit' if include_materials else 'ohne'} Materialien; {triangles} Dreiecke.")
    return mf.CaptureFile(role="model", path=MODEL_PATH, media_type=MODEL_MEDIA_TYPE, image=None,
                          note=note, byte_size=size, sha256=sha)


def camera_extras_of(data, width: int, height: int) -> dict | None:
    """``extras.rendertaxi.camera`` einer Blender-Kamera für ein Bild ``width × height`` — oder ``None``.

    Perspektivisch: Objektiv und Shift; parallel: nur Shift. Eine Kamera, die
    der Vertrag nicht trägt (Panorama, Shift über ±2), bleibt ohne Angabe —
    der Browser liest sie dann wie bisher mit Shift 0.
    """
    if data.type not in ("PERSP", "ORTHO") or max(abs(data.shift_x), abs(data.shift_y)) > SHIFT_LIMIT:
        return None
    sx, sy = contract_shift(data.sensor_fit, data.shift_x, data.shift_y, width, height)
    lens = lens_dict(data.sensor_fit, data.sensor_width, data.sensor_height, data.lens) \
        if data.type == "PERSP" else None
    return glb.camera_extras(lens, {"x": _num(sx, 6), "y": _num(sy, 6)})


def effective_size(render) -> tuple[float, float]:
    """Die Bildmaße, mit denen Blenders glTF-Exporter ``aspectRatio`` und ``yfov`` rechnet: Auflösung × Pixel Aspect.

    Bei nicht quadratischen Pixeln weicht das Seitenverhältnis von der Auflösung ab; Sensor Fit AUTO und der
    Shift beziehen sich in Blender ebenfalls auf diese Maße.
    """
    return render.resolution_x * render.pixel_aspect_x, render.resolution_y * render.pixel_aspect_y


def _write_camera_extras(scene, target: str) -> None:
    """Objektiv und Shift aller Kameras der Datei nachtragen — Knotenname = Objektname."""
    import bpy

    width, height = effective_size(scene.render)
    by_name = {}
    for obj in bpy.data.objects:
        if obj.type == "CAMERA" and obj.data is not None:
            block = camera_extras_of(obj.data, width, height)
            if block is not None:
                by_name[obj.name] = block
    if by_name:
        try:
            glb.set_camera_extras(target, by_name)
        except glb.GlbError as error:
            raise ExportError(str(error)) from None
