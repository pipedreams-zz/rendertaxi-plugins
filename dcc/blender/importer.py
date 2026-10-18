"""Rückweg (RTX-B-005, #625) — einen Export aus rendertaxi in die Szene importieren.

Ein Export der Plattform (RTX-M2-105) ist eine GLB: je Instanz ein Wurzelknoten mit ihrer Lage,
die Kameras des Blickpunkts als glTF-Kameras mit ``extras.rendertaxi.camera`` und — beim Bezug
„georeferenziert" — der Bezug des Rahmens in ``asset.extras.rendertaxi.origin``. Was hier gilt:

* **Import** mit Blenders glTF-Importer (``bpy.ops.import_scene.gltf``): Meter, Achsen glTF
  (+Y oben) → Blender (+Z oben) wie der Standardimporter, nichts wird verschmolzen — jeder
  Wurzelknoten einer Instanz bleibt ein eigenes Objekt (mit ``rendertaxi.instance`` in seinen
  Eigenschaften).
* **Sammlung** „rendertaxi <Export> <Datum>" mit einem Wurzelobjekt (Empty) gleichen Namens; alle
  obersten Objekte der Datei hängen daran. Die Sammlung trägt die Kennung des Assets: derselbe
  Export ersetzt sie nach Rückfrage, statt eine zweite anzulegen — die bisherige geht erst, wenn die
  neue vollständig steht; scheitert der Import, bleibt sie mit allen Bearbeitungen.
* **Kameras**: Objektiv, Sensor, Sensorbezug und Shift setzt die eine Rückrechnung
  ``rendertaxi_client.model_import.host_camera`` — der Importer kennt nur ``yfov`` (vertikal).
  Welche Definition zu welchem Kameraobjekt gehört, sagt eine Nummer, die vor dem Import in
  ``extras`` geschrieben und danach wieder entfernt wird (``mark_cameras``).
* **Lage**: Bezug „Nullpunkt des Rahmens" → das Wurzelobjekt bleibt am Ursprung. Bezug
  „georeferenziert" → das Wurzelobjekt trägt den Bezug als Transformation (Ost, Nord, Höhe, Drehung
  um Z um den negativen Nordwinkel, ``geo_root_transform``), und der Bezug steht als Text in seinen
  Eigenschaften (``rendertaxi_origin``). Die Geometrie bleibt in den kleinen Koordinaten des Rahmens.
  Der Bezug steht in Metern; das Wurzelobjekt bekommt ihn in Szeneneinheiten (÷ Unit Scale) wie die
  Geometrie, die der glTF-Importer ebenso umrechnet. Blender speichert die Lage eines Objekts in 32 Bit: bei Landeskoordinaten um 5 600 000 m liegt sie
  auf 0,5 m genau — der Text ist der genaue Wert.

Läuft im Hauptfaden (``bpy.ops``); den Download macht das Panel vorher im Hintergrund.
"""

from __future__ import annotations

import contextlib
import math

from .rendertaxi_client import glb
from .rendertaxi_client import model_import as mi

# Eigenschaften an Sammlung und Wurzelobjekt.
ASSET_KEY = "rendertaxi_asset_id"
ORIGIN_KEY = "rendertaxi_origin"
REFERENCE_KEY = "rendertaxi_reference"
EXPORT_KEY = "rendertaxi_export"
ASPECT_KEY = "rendertaxi_frame_aspect"

# Ohne ferne Ebene setzt der glTF-Importer 10¹² m — damit zeigt keine Ansicht durch die Kamera
# mehr Tiefe. 10 km wie die Parallelkamera des Exports (MODEL_EXPORT_ORTHOGRAPHIC_ZFAR).
DEFAULT_CLIP_END = 10_000.0


class ImportProblem(RuntimeError):
    """Ein Import, der nicht gelang — mit einem Satz für das Panel."""


def find_collection(asset_id: str):
    """Die Sammlung, in die dieser Export schon importiert wurde — oder ``None``."""
    import bpy

    for collection in bpy.data.collections:
        if collection.get(ASSET_KEY) == asset_id:
            return collection
    return None


def remove_collection(collection) -> None:
    """Sammlung, ihre Objekte (auch in Untersammlungen) und deren nun unbenutzte Daten entfernen."""
    import bpy

    def all_children(current):
        for child in current.children:
            yield from all_children(child)
            yield child

    objects = list(dict.fromkeys(_objects_of(collection)))
    # Verknüpfte Duplikate teilen ihre Daten: jeder Block nur einmal, sonst ist er beim zweiten Mal schon weg.
    data = list(dict.fromkeys(obj.data for obj in objects if obj.data is not None))
    for obj in objects:
        bpy.data.objects.remove(obj, do_unlink=True)
    for block in data:
        _remove_unused(block)
    for child in list(all_children(collection)):
        bpy.data.collections.remove(child)
    bpy.data.collections.remove(collection)


def _objects_of(collection):
    yield from collection.objects
    for child in collection.children:
        yield from _objects_of(child)


def _remove_unused(block) -> None:
    """Objektdaten (Netz, Kamera, Licht, Kurve) entfernen, wenn nichts mehr sie benutzt."""
    import bpy

    stores = {"Mesh": bpy.data.meshes, "Camera": bpy.data.cameras, "Light": bpy.data.lights,
              "Curve": bpy.data.curves}
    store = stores.get(block.bl_rna.identifier)
    if store is not None and block.users == 0:
        store.remove(block)


def apply_camera(data, camera: dict) -> None:
    """Eine Blender-Kamera nach ``host_camera`` einstellen — hier wird nichts gerechnet."""
    if camera["projection"] == "orthographic":
        data.type = "ORTHO"
        data.sensor_fit = "AUTO"
        data.ortho_scale = 2 * max(camera["halfWidth"], camera["halfHeight"])
    else:
        data.type = "PERSP"
        data.lens_unit = "MILLIMETERS"
        data.sensor_fit = camera["sensorFit"].upper()
        # Blender liest bei VERTICAL die Höhe, sonst die Breite; beide tragen denselben Wert.
        data.sensor_width = camera["sensorMm"]
        data.sensor_height = camera["sensorMm"]
        data.lens = camera["focalLengthMm"]
    data.shift_x, data.shift_y = camera["shift"]
    if camera["far"] is None:
        data.clip_end = DEFAULT_CLIP_END


def _gltf_options() -> dict:
    """Optionen des Importers, die es in dieser Blender-Fassung gibt — Kameras und Objekte bleiben einzeln."""
    import bpy

    wanted = {
        "merge_vertices": False,
        # Ab 5.x legt der Importer sonst eine eigene Sammlung je Szene an — die Sammlung ist unsere.
        "import_scene_as_collection": False,
        "import_select_created_objects": False,
    }
    known = bpy.ops.import_scene.gltf.get_rna_type().properties.keys()
    return {key: value for key, value in wanted.items() if key in known}


@contextlib.contextmanager
def _active_collection(context, collection):
    """Den Import in ``collection`` lenken — der Importer legt neue Objekte in die aktive Sammlung."""
    view_layer = context.view_layer
    previous = view_layer.active_layer_collection

    def find(layer):
        if layer.collection == collection:
            return layer
        for child in layer.children:
            found = find(child)
            if found is not None:
                return found
        return None

    target = find(view_layer.layer_collection)
    if target is not None:
        view_layer.active_layer_collection = target
    try:
        yield
    finally:
        with contextlib.suppress(ReferenceError, TypeError, AttributeError):
            view_layer.active_layer_collection = previous


def import_export(context, path: str, entry: dict, *, replace: bool = False) -> dict:
    """Die GLB unter ``path`` als Sammlung importieren — was entstand, für Panel und Prüfung.

    ``entry`` ist ein Eintrag aus ``model_import.export_entries``. Steht derselbe Export schon in der
    Szene, ersetzt ``replace`` ihn; sonst ``ImportProblem``. Gibt ``{"collection", "root", "objects",
    "instances", "cameras", "origin"}`` zurück (Namen und Zahlen).
    """
    import bpy

    existing = find_collection(entry["assetId"])
    if existing is not None and not replace:
        raise ImportProblem(f"Dieser Export steht schon in der Szene („{existing.name}“).")
    try:
        definitions = mi.mark_cameras(path)
        origin = mi.export_origin(glb.read_glb_json(path))
    except (glb.GlbError, OSError) as error:
        raise ImportProblem(f"Die Datei des Exports ist nicht lesbar: {error}") from None

    # Der Ersatz entsteht neben dem bisherigen Import; dieser geht erst, wenn der neue vollständig steht.
    # Bis dahin trägt die neue Sammlung keine Kennung, und der bisherige Import gibt seine Namen frei —
    # scheitert der neue, bekommt er sie zurück.
    aside = _set_aside(existing) if existing is not None else []
    name = mi.collection_name(entry)
    collection = bpy.data.collections.new(name)
    context.scene.collection.children.link(collection)
    before = set(bpy.data.objects)
    try:
        root, cameras = _build(context, path, collection, name, entry, origin, definitions, before)
    except BaseException:
        try:
            _discard(collection, [obj for obj in bpy.data.objects if obj not in before])
        finally:
            # Die bisherigen Namen kommen zurück, auch wenn die Rücknahme selbst scheitert.
            for block, own in aside:
                block.name = own
        raise
    imported = [obj for obj in collection.objects if obj is not root]
    collection[ASSET_KEY] = entry["assetId"]
    if existing is not None:
        remove_collection(existing)

    instances = sum(1 for obj in imported if _instance_of(obj) is not None)
    placed = mi.geo_root_transform(origin)
    return {
        "collection": collection.name,
        "root": root.name,
        "objects": len(imported),
        "instances": instances,
        "cameras": cameras,
        "origin": origin,
        "rotationDeg": math.degrees(placed["rotationZ"]),
    }


def _build(context, path: str, collection, name: str, entry: dict, origin, definitions, before):
    """Import in ``collection``, Wurzelobjekt, Kameras, Lage — ``(Wurzel, Zahl der Kameras)``."""
    import bpy
    from mathutils import Euler

    failure = ""
    try:
        with _active_collection(context, collection):
            result = bpy.ops.import_scene.gltf(filepath=path, **_gltf_options())
    except RuntimeError as error:
        # Der Importer meldet Fehler als RuntimeError mit dem Text seines Berichts; die letzte Zeile sagt es.
        result, failure = set(), (str(error).strip().splitlines() or [""])[-1]
    if "FINISHED" not in result:
        raise ImportProblem(f"Blender konnte den Export nicht importieren. {failure}".strip())
    imported = [obj for obj in bpy.data.objects if obj not in before]
    # Was der Importer außerhalb unserer Sammlung abgelegt hat, kommt hinein.
    for obj in imported:
        if collection not in obj.users_collection:
            for other in list(obj.users_collection):
                other.objects.unlink(obj)
            collection.objects.link(obj)

    root = bpy.data.objects.new(name, None)
    root.empty_display_type = "PLAIN_AXES"
    collection.objects.link(root)
    root[ASSET_KEY] = entry["assetId"]
    root[EXPORT_KEY] = entry.get("name") or ""
    root[REFERENCE_KEY] = "georeferenced" if origin is not None else "frame"
    if origin is not None:
        root[ORIGIN_KEY] = mi.origin_text(origin)
    for obj in imported:
        if obj.parent is None:
            basis = obj.matrix_basis.copy()
            obj.parent = root
            obj.matrix_parent_inverse.identity()
            obj.matrix_basis = basis

    cameras = 0
    for obj in imported:
        if obj.type != "CAMERA" or obj.data is None:
            continue
        index = obj.data.get(mi.CAMERA_MARK)
        if index is not None:
            del obj.data[mi.CAMERA_MARK]
        camera = mi.host_camera(definitions[index]) if isinstance(index, int) and 0 <= index < len(definitions) \
            else None
        if camera is not None:
            apply_camera(obj.data, camera)
            if camera["aspect"] is not None:
                obj[ASPECT_KEY] = camera["aspect"]
        cameras += 1

    # Der Importer rechnet Meter in Szeneneinheiten um (1 / Unit Scale); der Bezug steht in Metern und
    # kommt in dieselben Einheiten — sonst lägen Geometrie und Versatz in zwei Maßstäben.
    placed = mi.geo_root_transform(origin)
    units = 1.0 / context.scene.unit_settings.scale_length
    root.location = tuple(value * units for value in placed["location"])
    root.rotation_euler = Euler((0.0, 0.0, placed["rotationZ"]), "XYZ")
    context.view_layer.update()
    return root, cameras


def _set_aside(collection) -> list:
    """Sammlung, ihre Objekte und deren Daten vorläufig umbenennen — ``[(Block, bisheriger Name)]``."""
    blocks = [collection, *dict.fromkeys(_objects_of(collection))]
    blocks += [obj.data for obj in blocks[1:] if obj.data is not None]
    aside = []
    for number, block in enumerate(dict.fromkeys(blocks)):
        aside.append((block, block.name))
        block.name = f"rendertaxi-ersetzt-{number}"
    return aside


def _discard(collection, created) -> None:
    """Einen gescheiterten Import zurücknehmen — nur, was er selbst angelegt hat."""
    import bpy

    remove_collection(collection)
    for obj in created:
        with contextlib.suppress(ReferenceError):  # schon mit der Sammlung entfernt
            data = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if data is not None:
                _remove_unused(data)


def _instance_of(obj) -> dict | None:
    """``rendertaxi.instance`` eines Wurzelknotens (Eigenschaft aus ``extras``) — ``None`` ohne."""
    own = obj.get("rendertaxi")
    if own is None:
        return None
    try:
        own = own.to_dict()
    except AttributeError:
        pass
    instance = own.get("instance") if isinstance(own, dict) else None
    return instance if isinstance(instance, dict) else None
