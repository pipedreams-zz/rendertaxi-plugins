"""Rückweg in Rhino 8 (RTX-RH-005, #627) — einen Export aus rendertaxi in das aktive Dokument importieren.

Belegt (Prüfmatrix QR-12 … QR-15, ``integrations/rhino/docs/capabilities.md``):

* **Geometrie und Materialien** über Rhinos eigenen glTF-Import: ``RhinoDoc.Import(Pfad)`` (RhinoCommon seit
  7.0; „any file format that Rhino can import“) mit dem Importer ``Import_glTF`` (.glb und .gltf, Rhino-8-Hilfe
  „GL Transmission Format (.glb, .gltf) Import/Export“). Sein Quelltext (``mcneel/glTF-IO``, Zweig
  ``rhino-8.x``, ``glTF-BinImporter``) sagt, was er tut: ``ReadFile`` ohne Dialog, Y oben → Z oben
  (``YupToZup``), Meter → Dokumenteinheit (``RhinoMath.UnitScale``), je Netzknoten ein Netzobjekt mit
  Rendermaterial, **ein benannter leerer Knoten wird eine Ebene** (verschachtelt wie die Knoten), Kameras und
  ``extras`` liest er nicht. Ob Rhino 8.35 genau diesen Stand ausliefert, misst ``tools/measure_import.py``
  am Host; das Plugin glaubt es nicht, sondern prüft die Hülle jeder Instanz (``rueckweg.import_fit``).
* **Ebenen.** Die Wurzelknoten der Instanzen heißen für den Import kurz ``rdtxai-<Kennung>-<n>`` (nur im
  JSON-Teil der heruntergeladenen Kopie): so wird jede genau eine neue Ebene, auch wenn das Dokument schon eine
  Ebene ihres Namens hat. Danach trägt die Ebene den Namen der Instanz und hängt unter der obersten Ebene
  „rendertaxi <Export> <Datum>“; Netze ohne Instanz landen auf ihr selbst (sie ist während des Imports die
  aktuelle Ebene). Die Geometrie bleibt in den Koordinaten des Rahmens — dort liegt das Hauptmodell, dessen
  Datei dieses Dokument ist.
* **Kameras** legt das Plugin selbst als **benannte Ansichten** an (``ViewInfo`` aus einem losgelösten
  ``RhinoViewport``, ``NamedViews.Add``; gemessen in 8.35, siehe ``_named_view``): Name „<oberste Ebene> – <Kamera>“, Frustum aus ``rueckweg.rhino_view``.
* **Bezug.** „georeferenziert“: Ost, Nord, Höhe und Nordwinkel des Rahmens als Benutzertext der obersten Ebene
  und als Dokument-Benutzertext „rendertaxi Projektursprung“, verlustfrei; die Geometrie wird nicht in
  Landeskoordinaten verschoben (Rhino rechnet in 64 Bit, zeichnet aber in 32 Bit — Koordinaten um 5 600 000 m
  zeichnen auf Zentimeter grob).
* **Wiederholung.** Die oberste Ebene trägt die Kennung des Assets. Derselbe Export ersetzt nach Rückfrage
  Ebenen, Objekte, benannte Ansichten und Materialien des alten Imports — erst, wenn der neue gelungen ist.
  Ein gescheiterter Import lässt nichts zurück (Regel 3).

Läuft im UI-Faden; den Download macht der Controller vorher im Hintergrund.
"""

from __future__ import annotations

import json
import uuid

import Rhino

from . import model as md
from . import rhinocapture
from . import rueckweg as rw
from .controller import ImportProblem  # gehört dem Controller (ohne ``Rhino``), der ihn fängt
from .rendertaxi_client import glb
from .rendertaxi_client import model_import as mi
from .rendertaxi_client.log import log

# Benutzertext (``Layer.SetUserString``) an der obersten Ebene und an den Ebenen der Instanzen.
KEY_ASSET = "rendertaxi Asset"
KEY_EXPORT = "rendertaxi Export"
KEY_REFERENCE = "rendertaxi Bezug"
KEY_VIEWS = "rendertaxi Ansichten"
KEY_MATERIALS = "rendertaxi Materialien"
KEY_INSTANCE = "rendertaxi Instanz"
KEY_ROLE = "rendertaxi Rolle"
# Dokument-Benutzertext (``doc.Strings``) — sichtbar unter Dokumenteigenschaften › Benutzertext.
DOCUMENT_ORIGIN = "rendertaxi Projektursprung"
UNDO = "rdtx.ai Import"


# --------------------------------------------------------------------------
# Suchen und Entfernen
# --------------------------------------------------------------------------


def _layers(doc) -> list:
    return [layer for layer in (doc.Layers[i] for i in range(doc.Layers.Count))
            if layer is not None and not layer.IsDeleted]


def find_import(doc, asset_id: str):
    """Die oberste Ebene, unter der dieser Export schon im Dokument steht — oder ``None``."""
    if doc is None or not asset_id:
        return None
    empty = _empty_guid()
    return next((layer for layer in _layers(doc)
                 if layer.ParentLayerId == empty and layer.GetUserString(KEY_ASSET) == asset_id), None)


def _empty_guid():
    import System

    return System.Guid.Empty


def _below(doc, root) -> list:
    """``root`` und alle Ebenen darunter, die tiefsten zuerst."""
    layers = _layers(doc)
    by_parent: dict = {}
    for layer in layers:
        by_parent.setdefault(str(layer.ParentLayerId), []).append(layer)
    ordered, stack = [], [(root, 0)]
    while stack:
        layer, depth = stack.pop()
        ordered.append((depth, layer))
        stack.extend((child, depth + 1) for child in by_parent.get(str(layer.Id), []))
    return [layer for _depth, layer in sorted(ordered, key=lambda item: -item[0])]


def _depth(doc, layer) -> int:
    """Wie tief ``layer`` im Ebenenbaum steht (oben: 0)."""
    by_id = {str(item.Id): item for item in _layers(doc)}
    depth, at = 0, by_id.get(str(layer.ParentLayerId))
    while at is not None and depth < 64:
        depth, at = depth + 1, by_id.get(str(at.ParentLayerId))
    return depth


def _objects(doc) -> list:
    settings = Rhino.DocObjects.ObjectEnumeratorSettings()
    settings.HiddenObjects = True
    settings.LockedObjects = True
    settings.NormalObjects = True
    settings.IncludeLights = True
    return list(doc.Objects.GetObjectList(settings))


def _delete(doc, layers, views, materials) -> None:
    """Objekte auf ``layers``, dann die Ebenen (die tiefsten zuerst), benannte Ansichten und Materialien entfernen."""
    indices = {layer.Index for layer in layers}
    ids = [obj.Id for obj in _objects(doc) if obj.Attributes.LayerIndex in indices]
    if ids:
        doc.Objects.Delete(ids, True)
    current = doc.Layers.CurrentLayerIndex
    if current in indices:
        doc.Layers.SetCurrentLayerIndex(0 if 0 not in indices else _any_other_layer(doc, indices), True)
    for layer in layers:
        doc.Layers.Delete(layer.Index, True)
    for name in views:
        index = doc.NamedViews.FindByName(name)
        if index >= 0:
            doc.NamedViews.Delete(index)
    used = {str(obj.RenderMaterial.Id) for obj in _objects(doc) if obj.RenderMaterial is not None}
    for material in [m for m in doc.RenderMaterials if str(m.Id) in set(materials) and str(m.Id) not in used]:
        doc.RenderMaterials.Remove(material)


def _any_other_layer(doc, indices) -> int:
    for layer in _layers(doc):
        if layer.Index not in indices:
            return layer.Index
    return doc.Layers.Add(Rhino.DocObjects.Layer())


def _json_list(text) -> list:
    try:
        value = json.loads(text or "[]")
    except ValueError:
        return []
    return [str(item) for item in value] if isinstance(value, list) else []


def remove_import(doc, root) -> None:
    """Die oberste Ebene mit allem darunter, ihre benannten Ansichten und Materialien entfernen."""
    _delete(doc, _below(doc, root), _json_list(root.GetUserString(KEY_VIEWS)),
            _json_list(root.GetUserString(KEY_MATERIALS)))


# --------------------------------------------------------------------------
# Import
# --------------------------------------------------------------------------


def _token_names(path: str, instances: list, token: str) -> dict:
    """Die Wurzelknoten der Instanzen in der heruntergeladenen Kopie umbenennen — ``{Ersatzname: Instanz}``."""
    document, rest = glb._read(path)
    nodes = document.get("nodes") or []
    names = {}
    for number, instance in enumerate(instances):
        name = f"rdtxai-{token}-{number}"
        nodes[instance["node"]]["name"] = name
        names[name] = instance
    glb._write(path, document, rest)
    return names


def _add_layer(doc, name: str, parent=None, strings=None):
    layer = Rhino.DocObjects.Layer()
    layer.Name = name
    if parent is not None:
        layer.ParentLayerId = parent.Id
    for key, value in (strings or {}).items():
        layer.SetUserString(key, value)
    index = doc.Layers.Add(layer)
    if index < 0:
        raise ImportProblem(f"Rhino nimmt die Ebene „{name}“ nicht an.")
    return doc.Layers[index]


def _modify(doc, layer) -> None:
    if not doc.Layers.Modify(layer, layer.Index, True):
        raise ImportProblem(f"Rhino ändert die Ebene „{layer.Name}“ nicht.")


def _children_names(doc, parent) -> list:
    return [layer.Name for layer in _layers(doc) if layer.ParentLayerId == parent.Id]


def _bounds_of(objects):
    boxes = []
    for obj in objects:
        box = obj.Geometry.GetBoundingBox(True)
        if box.IsValid:
            boxes.append(([box.Min.X, box.Min.Y, box.Min.Z], [box.Max.X, box.Max.Y, box.Max.Z]))
    return rw.merge_bounds(boxes)


def _named_view(doc, view: rw.NamedView) -> int:
    """Eine benannte Ansicht aus ``rueckweg.NamedView`` — der Index in ``doc.NamedViews`` (−1: nicht angelegt).

    Gemessen in Rhino 8.35 (``docs/verification/rtx-rh-005-rhino-rueckweg/belege``): ``NamedViews.Add`` mit einer
    neuen ``ViewInfo()`` gibt −1, auch mit gültig gesetzter Kamera; eine ``ViewInfo`` des aktiven Viewports
    speichert dessen Kamera. Angenommen wird die Kamera über einen losgelösten ``RhinoViewport``
    (``SetViewProjection``), aus dem die ``ViewInfo`` entsteht — der aktive Viewport bleibt unberührt.
    """
    G = Rhino.Geometry
    projection = Rhino.DocObjects.ViewportInfo()
    if view.perspective:
        projection.ChangeToPerspectiveProjection(view.far / 2.0, False, view.lens_length or 50.0)
    else:
        projection.ChangeToParallelProjection(False)
    projection.SetCameraLocation(G.Point3d(*view.location))
    projection.SetCameraDirection(G.Vector3d(*view.direction))
    projection.SetCameraUp(G.Vector3d(*view.up))
    projection.UnlockFrustumSymmetry()
    projection.SetFrustum(view.left, view.right, view.bottom, view.top, view.near, view.far)
    projection.TargetPoint = G.Point3d(*view.target)
    viewport = Rhino.Display.RhinoViewport()
    try:
        if not viewport.SetViewProjection(projection, False):
            return -1
        info = Rhino.DocObjects.ViewInfo(viewport)
        info.Name = view.name
        return doc.NamedViews.Add(info)
    finally:
        viewport.Dispose()


def _units_per_meter(doc) -> float:
    meters = rhinocapture.meters_per_unit(doc)
    problem = md.unit_problem(str(doc.ModelUnitSystem), meters)
    if problem:
        raise ImportProblem(problem.replace("; das Bild lässt sich ohne Modell übernehmen.", ". Nichts importiert."))
    return 1.0 / meters


def _fallback_aspect(doc) -> float:
    try:
        view = doc.Views.ActiveView
        size = view.ActiveViewport.Size
        return size.Width / size.Height if size.Height else rw.FALLBACK_ASPECT
    except Exception:  # noqa: BLE001
        return rw.FALLBACK_ASPECT


def import_export(doc, path: str, entry: dict, replace: bool = False) -> dict:
    """Die GLB ``path`` (ein Export aus rendertaxi) in ``doc`` importieren — ``{root, instances, cameras, …}``.

    Ersetzt bei ``replace`` den früheren Import desselben Assets, aber erst nach Erfolg. ``ImportProblem`` mit
    einem Satz, wenn etwas nicht geht; dann bleibt im Dokument nichts von diesem Import.
    """
    units_per_meter = _units_per_meter(doc)
    try:
        document = glb.read_glb_json(path)
    except glb.GlbError as error:
        raise ImportProblem(str(error)) from None
    instances = mi.export_instances(document)
    poses = mi.camera_poses(document)
    origin = mi.export_origin(document)
    expected = {i["node"]: glb.world_point_bounds(path, i["node"]) for i in instances}
    whole = glb.world_point_bounds(path)
    if whole is None:
        raise ImportProblem("Der Export enthält kein Modell. Nichts importiert.")
    names = _token_names(path, instances, uuid.uuid4().hex[:8])

    old = find_import(doc, entry["assetId"]) if replace else None
    root_text = rw.root_name(entry)
    taken = [layer.Name for layer in _layers(doc)
             if layer.ParentLayerId == _empty_guid() and (old is None or layer.Id != old.Id)]
    final = rw.unique_name(root_text, taken)
    temporary = final if old is None else rw.unique_name(f"{final} (neu)", taken + [old.Name])

    before_objects = {str(obj.Id) for obj in _objects(doc)}
    before_layers = {str(layer.Id) for layer in _layers(doc)}
    before_materials = {str(m.Id) for m in doc.RenderMaterials}
    current = doc.Layers.CurrentLayerIndex
    undo = doc.BeginUndoRecord(UNDO)
    root = None
    views: list[str] = []
    renames: list[tuple[str, str]] = []
    try:
        root = _add_layer(doc, temporary, strings={
            KEY_ASSET: entry["assetId"], KEY_EXPORT: f"{entry.get('name') or 'Export'} ({entry.get('createdAt') or ''})",
            KEY_REFERENCE: rw.reference_text(origin)})
        doc.Layers.SetCurrentLayerIndex(root.Index, True)
        try:
            imported = doc.Import(path)
        finally:
            doc.Layers.SetCurrentLayerIndex(current, True)
        if not imported:
            raise ImportProblem("Rhino hat den Export nicht importiert (glTF-Import ab Rhino 8). Nichts importiert.")
        new_objects = [obj for obj in _objects(doc) if str(obj.Id) not in before_objects]
        new_layers = [layer for layer in _layers(doc)
                      if str(layer.Id) not in before_layers and layer.Id != root.Id]
        if not new_objects:
            raise ImportProblem("Rhinos glTF-Import hat keine Objekte angelegt. Nichts importiert.")

        # Ebenen: je Instanz die Ebene ihres Ersatznamens, umbenannt und unter die oberste Ebene.
        by_name = {layer.Name: layer for layer in new_layers}
        instance_layers = {}
        for token, instance in names.items():
            layer = by_name.get(token)
            if layer is None:
                raise ImportProblem("Rhinos glTF-Import hat die Instanzen nicht getrennt. Nichts importiert.")
            layer.Name = rw.unique_name(rw.layer_name(instance["name"], "Instanz"), _children_names(doc, root))
            layer.ParentLayerId = root.Id
            layer.SetUserString(KEY_INSTANCE, instance["instanceId"])
            layer.SetUserString(KEY_ROLE, instance["role"])
            _modify(doc, layer)
            instance_layers[instance["node"]] = layer
        # Weitere neue Ebenen ohne neue Elternebene (der Importer legt sie oben an) gehören unter die oberste Ebene.
        new_ids = {str(layer.Id) for layer in new_layers}
        placed = {str(layer.Id) for layer in instance_layers.values()} | {str(root.Id)}
        for layer in _layers(doc):
            if (str(layer.Id) in new_ids and str(layer.Id) not in placed
                    and str(layer.ParentLayerId) not in new_ids | placed):
                layer.ParentLayerId = root.Id
                _modify(doc, layer)

        # Prüfen: jede Instanz und das Ganze gegen die Punkte der Datei, in Rhino-Achsen und Dokumenteinheit.
        factor = rw.import_fit(rw.bounds_to_rhino(whole, units_per_meter), _bounds_of(new_objects))
        for node, layer in instance_layers.items():
            below = {item.Index for item in _below(doc, layer)}
            measured = _bounds_of([obj for obj in new_objects if obj.Attributes.LayerIndex in below])
            if measured is None and expected[node] is None:
                continue  # eine Instanz ohne Netze: ihre Ebene bleibt leer
            if measured is None or expected[node] is None:
                raise ImportProblem(f"Die Instanz „{layer.Name}“ kam ohne Geometrie an. Nichts importiert.")
            fitted = rw.import_fit(rw.bounds_to_rhino(expected[node], units_per_meter), measured)
            if abs(fitted / factor - 1.0) > rw.SCALE_TOLERANCE:
                raise ImportProblem(f"Die Instanz „{layer.Name}“ liegt anders als in der Datei. Nichts importiert.")
        if factor != 1.0:
            scale = Rhino.Geometry.Transform.Scale(Rhino.Geometry.Point3d.Origin, 1.0 / factor)
            for obj in new_objects:
                doc.Objects.Transform(obj, scale, True)
            new_objects = [obj for obj in _objects(doc) if str(obj.Id) not in before_objects]

        # Benannte Ansichten. Beim Ersetzen tragen die des alten Imports noch ihre Namen: die neuen heißen bis
        # zum Entfernen „… (neu)“ und danach wie der alte (``renames``).
        scene = rw.center(rw.bounds_to_rhino(whole, units_per_meter))
        old_views = [] if old is None else _json_list(old.GetUserString(KEY_VIEWS))
        taken_views = [doc.NamedViews[i].Name for i in range(doc.NamedViews.Count)]
        others = [name for name in taken_views if name not in old_views]
        skipped = 0
        for number, pose in enumerate(poses):
            name = rw.unique_name(rw.view_name(final, pose["name"], number), others + [n for _t, n in renames])
            temporary_view = rw.unique_name(f"{name} (neu)", taken_views + views) if name in taken_views else name
            view = rw.rhino_view(pose, temporary_view, units_per_meter, _fallback_aspect(doc), scene)
            if view is None or _named_view(doc, view) < 0:
                skipped += 1
                continue
            views.append(temporary_view)
            renames.append((temporary_view, name))

        materials = [str(m.Id) for m in doc.RenderMaterials if str(m.Id) not in before_materials]
        root.SetUserString(KEY_VIEWS, json.dumps([name for _temporary, name in renames], ensure_ascii=False))
        root.SetUserString(KEY_MATERIALS, json.dumps(materials))
        _modify(doc, root)
    except BaseException as error:
        layers = sorted((layer for layer in _layers(doc) if str(layer.Id) not in before_layers),
                        key=lambda layer: -_depth(doc, layer))
        try:
            _delete(doc, layers, views, [str(m.Id) for m in doc.RenderMaterials if str(m.Id) not in before_materials])
            leftovers = [obj.Id for obj in _objects(doc) if str(obj.Id) not in before_objects]
            if leftovers:
                doc.Objects.Delete(leftovers, True)
        except Exception as cleanup:  # noqa: BLE001 — der erste Fehler zählt; der zweite geht ins Protokoll
            log(f"Rücknahme des Imports unvollständig: {type(cleanup).__name__}")
        if isinstance(error, rw.ImportFitError):
            raise ImportProblem(str(error)) from None
        raise
    else:
        # Gelungen: erst jetzt den alten Import entfernen und die Namen übernehmen — im selben Rückgängig-Schritt.
        if old is not None:
            remove_import(doc, old)
            root.Name = final
            _modify(doc, root)
        for temporary_view, name in renames:
            if temporary_view != name:
                doc.NamedViews.Rename(temporary_view, name)
        if origin is not None:
            doc.Strings.SetString(DOCUMENT_ORIGIN, mi.origin_text(origin))
    finally:
        doc.EndUndoRecord(undo)
    doc.Views.Redraw()
    return {"root": final, "instances": len(instance_layers), "cameras": len(views), "origin": origin,
            "skippedCameras": skipped, "factor": factor, "views": [name for _temporary, name in renames]}
