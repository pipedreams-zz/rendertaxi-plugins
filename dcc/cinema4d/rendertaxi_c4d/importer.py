"""Rückweg (RTX-C4D-012, #626) — einen Export aus rendertaxi in das aktive Dokument importieren.

Ein Export der Plattform (RTX-M2-105) ist eine GLB: je Instanz ein Wurzelknoten mit ihrer Lage
(``extras.rendertaxi.instance``), die Kameras des Blickpunkts als glTF-Kameras mit
``extras.rendertaxi.camera`` und — beim Bezug „georeferenziert“ — der Bezug des Rahmens in
``asset.extras.rendertaxi.origin``. Was hier gilt (Prüfmatrix QC-17 … QC-20):

* **Geometrie und Materialien** über den glTF-Importer von Cinema 4D (``FORMAT_GLTFIMPORT``, seit 2023.200):
  ``c4d.documents.MergeDocument`` in das aktive Dokument. Die Python-Referenz 2026 nennt weder die
  Plugin-ID noch die Parameter; beide stehen im C++-SDK 2026 (``ge_prepass.h``: ``FORMAT_GLTFIMPORT
  1060210``; ``description/fgltfimporter.h``). Das Plugin nimmt den Namen aus ``c4d``, wenn es ihn kennt,
  sonst die Zahl. Die Einstellungen sind die des Nutzers: gesetzt für den einen Import (Flip Z an, Scale
  1 m, Kameras aus, keine Animation), danach als ganzer Container zurück — wie der Export (RTX-C4D-003).
* **Prüfen statt glauben.** Wie beim Export misst das Plugin nach dem Import die Hülle der neuen Objekte
  gegen die Hülle der Datei (``export.hull_fit``): an Z gespiegelt bis auf einen Faktor heißt richtig;
  der Faktor setzt die Objekte auf die Einheit des Dokuments. Seitenverkehrt, verschoben oder verzerrt
  heißt: nichts bleibt im Dokument, und ein Satz sagt es.
* **Kameras legt das Plugin selbst an** — der Importer kennt keine ``extras`` (seine Beschreibung hat
  dafür keinen Parameter) und damit weder Objektiv noch Shift. Ort und Richtung aus der Weltlage des
  Kameraknotens (gespiegelt wie die Geometrie), Brennweite, Sensor und Shift aus der einen Rückrechnung
  ``rendertaxi_client.model_import.host_camera``; Film Offset aus dem Shift des Vertrags mit
  ``export.film_offset_of`` (der Umkehrung des Exportwegs). Parallelkameras übernimmt das Plugin nicht:
  ihre Zoom-Bedeutung ist am Host nicht gemessen (der Export nimmt sie auch nicht).
* **Ein Null-Objekt** „rendertaxi <Export> <Datum>“ oben im Dokument; darunter je Instanz ihr Objekt
  (Wurzelknoten bleiben getrennt, nichts wird verschmolzen) und die Kameras. Lage: Bezug „Nullpunkt des
  Rahmens“ → das Null-Objekt am Ursprung; „georeferenziert“ → das Null-Objekt trägt Ost, Höhe, Nord und
  die Drehung um den Nordwinkel (``geo_root_transform``, in die Achsen von Cinema 4D gesetzt), der Bezug
  steht verlustfrei als Benutzerdatum. Cinema 4D rechnet Matrizen in 64 Bit: die Lage ist bei
  Landeskoordinaten genau (anders als in Blender).
* **Wiederholung:** das Null-Objekt trägt die Kennung des Assets (Untercontainer ``PLUGIN_ID``). Derselbe
  Export ersetzt es nach Rückfrage samt seiner Materialien — erst, wenn der neue Import gelungen ist.

Läuft im Hauptfaden (``MergeDocument``); den Download macht der Controller vorher im Hintergrund.
"""

from __future__ import annotations

import math
import os

import c4d

from . import export, host
from .controller import ImportProblem  # gehört dem Controller (ohne ``c4d``), der ihn fängt
from .rendertaxi_client import glb
from .rendertaxi_client import model_import as mi

# Der glTF-Importer: Plugin-ID und Parameter aus dem C++-SDK 2026, falls ``c4d`` die Namen nicht kennt.
FORMAT_GLTFIMPORT = 1060210
_IMPORTER_IDS = {
    "GLTFIMPORTER_UNITSCALE": 1002,
    "GLTFIMPORTER_CAMERAS": 1003,
    "GLTFIMPORTER_GEOMETRY": 1004,
    "GLTFIMPORTER_NORMALS": 1005,
    "GLTFIMPORTER_NORMALS_VERTEX": 1007,
    "GLTFIMPORTER_UVS": 1011,
    "GLTFIMPORTER_FLIPZ": 1012,
    "GLTFIMPORTER_MATERIALS": 1013,
    "GLTFIMPORTER_ANIMATION_TRS": 1014,
    "GLTFIMPORTER_ANIMATION_POSEMORPHS": 1017,
    "GLTFIMPORTER_GEOMETRY_SPLIT_MESHPRIMS": 1018,
}

# Kennzeichen im Untercontainer ``host.PLUGIN_ID`` an Null-Objekt, Instanzen, Kameras und Materialien.
MARK_ASSET = 1
MARK_PART = 2
MARK_INSTANCE = 3
ROOT, INSTANCE, CAMERA, MATERIAL = "root", "instance", "camera", "material"

# Benutzerdaten — sichtbar im Attribut-Manager, verlustfrei als Text.
USER_EXPORT = "rendertaxi Export"
USER_REFERENCE = "rendertaxi Bezug"
USER_ASPECT = "rendertaxi Rahmenverhältnis"
FRAME_REFERENCE = "Nullpunkt des Rahmens"

# Cinema 4D begrenzt Objektnamen nicht wie Blender; lang genug für jeden Exportnamen der Plattform.
NAME_MAX_BYTES = 255
SCALE_TOLERANCE = export.SCALE_TOLERANCE



# --------------------------------------------------------------------------
# Kennzeichen
# --------------------------------------------------------------------------


def _mark(item, asset_id: str, part: str, instance_id: str = "") -> None:
    bc = c4d.BaseContainer()
    bc.SetString(MARK_ASSET, asset_id)
    bc.SetString(MARK_PART, part)
    if instance_id:
        bc.SetString(MARK_INSTANCE, instance_id)
    item.GetDataInstance().SetContainer(host.PLUGIN_ID, bc)


def mark_of(item) -> tuple[str, str]:
    """``(Asset-Kennung, Teil)`` eines Elements dieses Rückwegs — leer, wenn es keins ist."""
    bc = item.GetDataInstance().GetContainer(host.PLUGIN_ID)
    return bc.GetString(MARK_ASSET), bc.GetString(MARK_PART)


def _top_objects(doc) -> list:
    found, obj = [], doc.GetFirstObject()
    while obj is not None:
        found.append(obj)
        obj = obj.GetNext()
    return found


def _materials(doc) -> list:
    found, material = [], doc.GetFirstMaterial()
    while material is not None:
        found.append(material)
        material = material.GetNext()
    return found


def _walk(obj):
    while obj is not None:
        yield obj
        yield from _walk(obj.GetDown())
        obj = obj.GetNext()


def _known(item, among: list) -> bool:
    """Ob ``item`` schon in ``among`` war — ``==`` vergleicht das Objekt hinter der Python-Hülle, nie ``id()``."""
    return any(item == other for other in among)


def find_import(doc, asset_id: str):
    """Das Null-Objekt, unter dem dieser Export schon im Dokument steht — oder ``None``."""
    if doc is None or not asset_id:
        return None
    return next((obj for obj in _top_objects(doc) if mark_of(obj) == (asset_id, ROOT)), None)


def remove_import(doc, root) -> None:
    """Null-Objekt mit allem darunter und die Materialien desselben Imports entfernen."""
    asset_id, _part = mark_of(root)
    for material in _materials(doc):
        if mark_of(material) == (asset_id, MATERIAL):
            material.Remove()
    root.Remove()


def _set_user_text(obj, name: str, text: str) -> None:
    bc = c4d.GetCustomDataTypeDefault(c4d.DTYPE_STRING)
    bc[c4d.DESC_NAME] = name
    obj[obj.AddUserData(bc)] = text


# --------------------------------------------------------------------------
# Importer
# --------------------------------------------------------------------------


def _id(name: str) -> int:
    return getattr(c4d, name, None) or _IMPORTER_IDS[name]


def _format() -> int:
    return getattr(c4d, "FORMAT_GLTFIMPORT", None) or FORMAT_GLTFIMPORT


def gltf_import_available() -> bool:
    return c4d.plugins.FindPlugin(_format(), c4d.PLUGINTYPE_SCENELOADER) is not None


def importer_settings(doc) -> dict:
    """``{Parameter-ID: Wert}`` für den einen Import — Meter, gespiegelt an Z, Kameras aus, keine Animation.

    ``UnitScaleData`` hat laut Referenz 2026 nur den Kopierkonstruktor: der Maßstab ist eine Kopie des
    Projektmaßstabs, auf 1 m gesetzt.
    """
    meters = c4d.UnitScaleData(doc[c4d.DOCUMENT_DOCUNIT])
    meters.SetUnitScale(1.0, c4d.DOCUMENT_UNIT_M)
    return {
        _id("GLTFIMPORTER_FLIPZ"): True,
        _id("GLTFIMPORTER_UNITSCALE"): meters,
        _id("GLTFIMPORTER_GEOMETRY"): True,
        _id("GLTFIMPORTER_MATERIALS"): True,
        _id("GLTFIMPORTER_UVS"): True,
        # Die Normalen der Datei sind die, die Cinema 4D beim Export zeigte (RTX-C4D-011) — nicht neu glätten.
        _id("GLTFIMPORTER_NORMALS"): _id("GLTFIMPORTER_NORMALS_VERTEX"),
        _id("GLTFIMPORTER_CAMERAS"): False,
        _id("GLTFIMPORTER_ANIMATION_TRS"): False,
        _id("GLTFIMPORTER_ANIMATION_POSEMORPHS"): False,
        _id("GLTFIMPORTER_GEOMETRY_SPLIT_MESHPRIMS"): False,
    }


def _merge(doc, path: str) -> None:
    """``MergeDocument`` mit den Einstellungen des Plugins; die des Nutzers danach zurück (ganzer Container)."""
    plug = c4d.plugins.FindPlugin(_format(), c4d.PLUGINTYPE_SCENELOADER)
    if plug is None:
        raise ImportProblem("Der glTF-Importer ist in diesem Cinema 4D nicht verfügbar (ab Cinema 4D 2023.2).")
    data: dict = {}
    if not plug.Message(c4d.MSG_RETRIEVEPRIVATEDATA, data) or data.get("imexporter") is None:
        raise ImportProblem("Die Einstellungen des glTF-Importers sind nicht erreichbar.")
    importer = data["imexporter"]
    saved = importer.GetDataInstance().GetClone(c4d.COPYFLAGS_NONE)
    flags = c4d.SCENEFILTER_OBJECTS | c4d.SCENEFILTER_MATERIALS | getattr(c4d, "SCENEFILTER_MERGESCENE", 0)
    try:
        for pid, value in importer_settings(doc).items():
            importer[pid] = value
        ok = c4d.documents.MergeDocument(doc, path, flags, None)
    finally:
        importer.SetData(saved, False)  # ersetzen, nicht zusammenführen
    if not ok:
        raise ImportProblem("Cinema 4D konnte den Export nicht importieren.")


# --------------------------------------------------------------------------
# Raum: glTF (Meter, rechtshändig, +Y oben) → Cinema 4D (Einheit des Dokuments, linkshändig)
# --------------------------------------------------------------------------


def _point(x: float, y: float, z: float, units: float):
    """Ein Punkt der Datei im Raum von Cinema 4D: an Z gespiegelt (Flip Z), in Einheiten des Dokuments."""
    return c4d.Vector(x / units, y / units, -z / units)


def _unit(vector) -> tuple[float, float, float]:
    x, y, z = vector
    norm = math.sqrt(x * x + y * y + z * z)
    if norm == 0:
        raise ImportProblem("Eine Kamera des Exports hat keine Richtung.")
    return x / norm, y / norm, z / norm


def _cross(a, b) -> tuple[float, float, float]:
    return a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]


def camera_matrix(world: tuple, units: float):
    """Die Lage einer Kamera in Cinema 4D aus der Weltlage ihres Knotens (3 × 4 zeilenweise, Meter).

    glTF blickt entlang −Z des Knotens, +Y ist oben; Cinema 4D blickt entlang +Z des Objekts. Gespiegelt an Z
    wie die Geometrie: Blick ``v3 = M · (−Z)``, oben ``v2 = M · Y``, rechts ``v1 = v2 × v3`` (die Umkehrung von
    ``export.to_export_direction``). Ein Maßstab am Knoten zählt nicht — die Achsen werden normiert.
    """
    up = _unit((world[1], world[5], -world[9]))
    view = _unit((-world[2], -world[6], world[10]))
    right = _unit(_cross(up, view))
    up = _cross(view, right)
    return c4d.Matrix(_point(world[3], world[7], world[11], units), c4d.Vector(*right), c4d.Vector(*up),
                      c4d.Vector(*view))


def camera_nodes(document: dict) -> list[tuple[str, dict, tuple]]:
    """``(Name, Kameradefinition, Weltlage)`` je Kameraknoten der Szene, in der Reihenfolge der Datei."""
    nodes = document.get("nodes") or []
    cameras = document.get("cameras") or []
    found = []
    stack = [(index, glb._IDENTITY) for index in reversed(glb._roots(document))]
    while stack:
        index, parent = stack.pop()
        if not isinstance(index, int) or not 0 <= index < len(nodes):
            continue
        node = nodes[index]
        world = glb._compose(parent, glb._local(node))
        camera = node.get("camera")
        if isinstance(camera, int) and 0 <= camera < len(cameras):
            found.append((str(node.get("name") or cameras[camera].get("name") or "Kamera"), cameras[camera], world))
        stack.extend((child, world) for child in reversed(node.get("children", [])))
    return found


def root_nodes(document: dict) -> list[tuple[str, dict | None, tuple]]:
    """``(Name, rendertaxi.instance, Ort in Metern)`` je Wurzelknoten der Szene ohne Kamera — die Instanzen."""
    nodes = document.get("nodes") or []
    found = []
    for index in glb._roots(document):
        if not isinstance(index, int) or not 0 <= index < len(nodes) or nodes[index].get("camera") is not None:
            continue
        node = nodes[index]
        extras = node.get("extras") if isinstance(node.get("extras"), dict) else {}
        own = extras.get(glb.CAMERA_EXTRAS_KEY) if isinstance(extras.get(glb.CAMERA_EXTRAS_KEY), dict) else {}
        instance = own.get("instance") if isinstance(own.get("instance"), dict) else None
        local = glb._local(node)
        found.append((str(node.get("name") or ""), instance, (local[3], local[7], local[11])))
    return found


def _match(objects: list, roots: list) -> list:
    """Je Wurzelknoten sein oberstes Objekt — über den Namen, sonst in der Reihenfolge der Datei; ``None`` ohne."""
    unmatched, found = list(objects), []
    for node_name, _instance, _place in roots:
        obj = next((o for o in unmatched if o.GetName() == node_name), None)
        if obj is None and unmatched:
            obj = unmatched[0]
        if obj is not None:
            unmatched = [o for o in unmatched if not (o == obj)]
        found.append(obj)
    return found


def _check_places(matched: list, roots: list, units: float, tolerance: float) -> None:
    """Liegen die Instanzen dort, wo die Datei sie hinlegt (an Z gespiegelt)? Die Hülle allein sieht eine Spiegelung
    nicht, wenn die Szene in Z symmetrisch ist — die Orte der Wurzelknoten schon."""
    for obj, (_name, _instance, (x, y, z)) in zip(matched, roots):
        if obj is None:
            continue
        at = obj.GetMl().off
        mirrored = math.dist((at.x, at.y, at.z), (x / units, y / units, -z / units))
        if mirrored <= tolerance:
            continue
        if math.dist((at.x, at.y, at.z), (x / units, y / units, z / units)) <= tolerance:
            raise ImportProblem("Der glTF-Import liefert das Modell seitenverkehrt („Flip Z“ wirkt nicht).")
        raise ImportProblem("Der glTF-Import hat das Modell verschoben oder verzerrt.")


def root_matrix(origin: dict | None, units: float):
    """Die Lage des Null-Objekts: ``geo_root_transform`` (Z oben) in den Achsen von Cinema 4D (Y oben).

    Dort ist x Ost, y Nord, z Höhe; hier x Ost, y Höhe, z Nord (glTF −Z ist Plan-Nord, gespiegelt +Z). Die
    Drehung um +Z um α wird zur Drehung, die x nach ``(cos α, 0, sin α)`` und z nach ``(−sin α, 0, cos α)``
    bringt — derselbe Nordwinkel, nur die Achsen getauscht.
    """
    placed = mi.geo_root_transform(origin)
    east, north, height = placed["location"]
    alpha = placed["rotationZ"]
    cos, sin = math.cos(alpha), math.sin(alpha)
    return c4d.Matrix(c4d.Vector(east / units, height / units, north / units), c4d.Vector(cos, 0.0, sin),
                      c4d.Vector(0.0, 1.0, 0.0), c4d.Vector(-sin, 0.0, cos))


def _bounds(objects: list) -> tuple[list, list] | None:
    """Die Hülle wie ``glb.world_bounds``: je Polygonobjekt die acht Ecken seiner Punkte, mit der Weltmatrix."""
    low, high = [math.inf] * 3, [-math.inf] * 3
    for top in objects:
        for obj in _walk_one(top):
            if not isinstance(obj, c4d.PolygonObject):
                continue
            points = obj.GetAllPoints()
            if not points:
                continue
            lo = [min(getattr(p, axis) for p in points) for axis in "xyz"]
            hi = [max(getattr(p, axis) for p in points) for axis in "xyz"]
            matrix = obj.GetMg()
            for corner in ((x, y, z) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])):
                point = matrix * c4d.Vector(*corner)
                for axis, value in enumerate((point.x, point.y, point.z)):
                    low[axis] = min(low[axis], value)
                    high[axis] = max(high[axis], value)
    return None if low[0] == math.inf else (low, high)


def _walk_one(obj):
    yield obj
    yield from _walk(obj.GetDown())


def _scale(obj, factor: float) -> None:
    """Ein oberstes Objekt um den Ursprung skalieren — Ort und Achsen, ohne Punkte anzufassen."""
    m = obj.GetMl()
    obj.SetMl(c4d.Matrix(m.off * factor, m.v1 * factor, m.v2 * factor, m.v3 * factor))


# --------------------------------------------------------------------------
# Kameras
# --------------------------------------------------------------------------


def make_camera(name: str, definition: dict, world: tuple, units: float, fallback_aspect: float):
    """Ein ``CameraObject`` aus einer Kameradefinition — ``None`` für eine Parallelkamera oder eine unbrauchbare.

    Cinema 4D misst den Sensor an der Bildbreite (am Host belegt, RTX-C4D-007): ein Sensorbezug auf der Höhe wird
    mit dem Verhältnis des Rahmens auf die Breite gerechnet. Ohne ``aspectRatio`` in der Datei gilt das Verhältnis
    der Rendervoreinstellung.
    """
    camera = mi.host_camera(definition)
    if camera is None or camera["projection"] != "perspective":
        return None
    aspect = camera["aspect"] or fallback_aspect
    sensor = camera["sensorMm"]
    if mi.fitted_axis(camera["sensorFit"], aspect) == "vertical":
        sensor *= aspect
    obj = c4d.CameraObject()
    obj.SetName(name)
    obj[c4d.CAMERA_PROJECTION] = c4d.Pperspective
    obj[c4d.CAMERA_FOCUS] = camera["focalLengthMm"]
    obj[c4d.CAMERAOBJECT_APERTURE] = sensor
    offset_x, offset_y = export.film_offset_of(camera["shiftLonger"]["x"], camera["shiftLonger"]["y"], aspect, 1.0)
    obj[c4d.CAMERAOBJECT_FILM_OFFSET_X] = offset_x
    obj[c4d.CAMERAOBJECT_FILM_OFFSET_Y] = offset_y
    if camera["near"] is not None:
        obj[c4d.CAMERAOBJECT_NEAR_CLIPPING_ENABLE] = True
        obj[c4d.CAMERAOBJECT_NEAR_CLIPPING] = camera["near"] / units
    if camera["far"] is not None:
        obj[c4d.CAMERAOBJECT_FAR_CLIPPING_ENABLE] = True
        obj[c4d.CAMERAOBJECT_FAR_CLIPPING] = camera["far"] / units
    obj.SetMl(camera_matrix(world, units))
    if camera["aspect"] is not None:
        _set_user_text(obj, USER_ASPECT, repr(camera["aspect"]))
    return obj


# --------------------------------------------------------------------------
# Import
# --------------------------------------------------------------------------


def _render_aspect(doc) -> float:
    rd = doc.GetActiveRenderData()
    try:
        width, height = float(rd[c4d.RDATA_XRES]), float(rd[c4d.RDATA_YRES])
    except (TypeError, ValueError, AttributeError):
        return 16 / 9
    return width / height if width > 0 and height > 0 else 16 / 9


def _unwrap(doc, objects: list, wanted: set) -> list:
    """Hat der Importer die Szene in ein einziges Null-Objekt gelegt, zählen dessen Kinder als oberste Objekte."""
    while len(objects) == 1 and objects[0].GetName() not in wanted and objects[0].GetDown() is not None \
            and objects[0].GetType() == c4d.Onull:
        wrapper = objects[0]
        objects = list(_walk_siblings(wrapper.GetDown()))
        for obj in objects:
            matrix = obj.GetMg()
            obj.Remove()
            doc.InsertObject(obj)
            obj.SetMl(matrix)
        wrapper.Remove()
    return objects


def _walk_siblings(obj):
    while obj is not None:
        yield obj
        obj = obj.GetNext()


def _drop_cameras(objects: list, camera_names: set) -> list:
    """Was der Importer für Kameraknoten angelegt hat (Kamera oder leeres Null-Objekt), fällt weg."""
    for top in list(objects):
        for obj in list(_walk_one(top)):
            stand_in = obj.GetType() == c4d.Onull and obj.GetDown() is None and obj.GetName() in camera_names
            if isinstance(obj, c4d.CameraObject) or obj.GetType() == c4d.Ocamera or stand_in:
                obj.Remove()
                objects = [o for o in objects if not (o == obj)]
    return objects


def import_export(doc, path: str, entry: dict, *, replace: bool = False) -> dict:
    """Die GLB unter ``path`` unter ein Null-Objekt importieren — was entstand, für Dialog und Prüfung.

    ``entry`` ist ein Eintrag aus ``model_import.export_entries``. Steht derselbe Export schon im Dokument,
    ersetzt ``replace`` ihn; sonst ``ImportProblem``. Zurück: ``{"root", "objects", "instances", "cameras",
    "skippedCameras", "origin", "scale"}``.
    """
    asset_id = entry["assetId"]
    existing = find_import(doc, asset_id)
    if existing is not None and not replace:
        raise ImportProblem(f"Dieser Export steht schon im Dokument („{existing.GetName()}“).")
    try:
        units = export.meters_per_unit(doc)
    except export.ExportError as error:
        raise ImportProblem(str(error)) from None
    if not os.path.isfile(path):
        raise ImportProblem("Die Datei des Exports fehlt.")
    try:
        document = glb.read_glb_json(path)
        bounds = glb.world_bounds(document)
    except (glb.GlbError, OSError, ValueError) as error:
        raise ImportProblem(f"Die Datei des Exports ist nicht lesbar: {error}") from None
    origin = mi.export_origin(document)
    cameras = camera_nodes(document)
    roots = root_nodes(document)

    objects_before, materials_before = _top_objects(doc), _materials(doc)
    _merge(doc, path)
    imported = [obj for obj in _top_objects(doc) if not _known(obj, objects_before)]
    materials = [m for m in _materials(doc) if not _known(m, materials_before)]
    try:
        imported = _drop_cameras(_unwrap(doc, imported, {name for name, _, _ in roots}),
                                 {name for name, _, _ in cameras})
        factor = 1.0
        if bounds is not None:
            found = _bounds(imported)
            if found is None:
                raise ImportProblem("Der Import hat keine Geometrie angelegt.")
            verdict, measured = export.hull_fit(*found, *bounds)
            if verdict == export.UNMIRRORED:
                raise ImportProblem("Der glTF-Import liefert das Modell seitenverkehrt („Flip Z“ wirkt nicht).")
            if verdict != export.MIRRORED:
                raise ImportProblem("Der glTF-Import hat das Modell verschoben oder verzerrt.")
            # ``measured``: Meter der Datei je Einheit des Imports; die Objekte sollen ``units`` Meter je Einheit haben.
            factor = measured / units
            if abs(factor - 1.0) > SCALE_TOLERANCE:
                for obj in imported:
                    _scale(obj, factor)
            diagonal = math.dist(bounds[0], bounds[1]) / units
            matched = _match(imported, roots)
            _check_places(matched, roots, units, export.BOUNDS_TOLERANCE * diagonal)
        else:
            matched = _match(imported, roots)
    except BaseException:
        for obj in imported:
            obj.Remove()
        for material in materials:
            material.Remove()
        raise

    if existing is not None:
        remove_import(doc, existing)
    name = mi.collection_name(entry, NAME_MAX_BYTES)
    root = c4d.BaseObject(c4d.Onull)
    root.SetName(name)
    _mark(root, asset_id, ROOT)
    _set_user_text(root, USER_EXPORT, entry.get("name") or "")
    _set_user_text(root, USER_REFERENCE, mi.origin_text(origin) if origin is not None else FRAME_REFERENCE)
    doc.InsertObject(root)

    instances = 0
    for obj, (_name, instance, _place) in zip(matched, roots):
        if obj is not None and instance is not None:
            _mark(obj, asset_id, INSTANCE, str(instance.get("id") or ""))
            instances += 1
    for obj in imported:
        matrix = obj.GetMl()
        obj.Remove()
        obj.InsertUnderLast(root)
        obj.SetMl(matrix)
    for material in materials:
        _mark(material, asset_id, MATERIAL)

    made, skipped = 0, 0
    aspect = _render_aspect(doc)
    for camera_name, definition, world in cameras:
        camera = make_camera(camera_name, definition, world, units, aspect)
        if camera is None:
            skipped += 1
            continue
        _mark(camera, asset_id, CAMERA)
        camera.InsertUnderLast(root)
        made += 1

    root.SetMl(root_matrix(origin, units))
    c4d.EventAdd()
    return {"root": name, "objects": len(imported), "instances": instances, "cameras": made,
            "skippedCameras": skipped, "origin": origin, "scale": factor}
