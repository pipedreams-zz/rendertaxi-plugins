"""Modellweg in Rhino 8 (RTX-RH-003) — sichtbare Objekte als GLB ohne Dialog, benannte Ansichten als Kameras.

Vertrag: ``integrations/_shared/docs/capture-manifest.md``, Abschnitt 11 (ADR 0032, ADR 0034). Am Host gemessen
(Rhino 8.35, macOS arm64, 08.10.2026, ``docs/measurements/2026-10-08-modellweg.md``):

* **Weg.** ``Rhino.FileIO.FileGltf.Write(Pfad, Dokument, FileGltfWriteOptions)`` schreibt ohne Dialog. Es
  schreibt ein **ganzes** Dokument. Das Plugin legt deshalb ein kopfloses Dokument an
  (``RhinoDoc.CreateHeadless``), legt dort nur die vernetzten sichtbaren Objekte ab und exportiert es — das
  Dokument des Nutzers bleibt unberührt, keine Vernetzung wird darin gespeichert (Regel 2).
* **Sichtbar** heißt: ``ObjectEnumeratorSettings`` mit ``VisibleFilter`` und der aktiven Ansicht als
  ``ViewportFilter`` (ausgeblendete Objekte, ausgeschaltete Ebenen, Ebenen nur in dieser Ansicht aus). Blöcke
  werden mit ``InstanceObject.Explode`` bis in die Blätter aufgelöst; ein Teil auf einer ausgeschalteten
  Ebene bleibt weg. Gespiegelte Blöcke drehen den Umlaufsinn um (gemessen: Volumen −1); das Plugin dreht ihn
  zurück.
* **Vernetzung.** Das Render-Netz, das Rhino für das Objekt schon hat; sonst ``Mesh.CreateFromBrep`` mit den
  Render-Mesh-Einstellungen des Objekts (``GetRenderMeshParameters``: eigene oder die des Dokuments), SubD
  mit ``Mesh.CreateFromSubD`` in der Dichte des Exporters. Kurven, Punkte, Bemaßungen, Texte und Lichter
  haben keine Fläche und gehen nicht mit — das Fenster nennt ihre Zahl.
* **Schnittebenen** wirken nicht auf die Datei: das Modell geht ganz mit, das Fenster sagt es.
* **Kameras.** Der Exporter schreibt keine. Jede benannte Ansicht geht als Kamera mit (``camera.gltf_camera``,
  ``glb.add_cameras``), mit dem Seitenverhältnis der Aufnahme; Objektiv und Shift in ``extras``.
* **Materialien** (RTX-P-021, ``capture-manifest.md`` 11.6). Je Objekt sein Rendermaterial als einfaches Material im
  kopflosen Dokument — Diffusfarbe und Transparenz — und die Texturkoordinaten des Netzes nach dem Mapping des
  Objekts (Kanal 1). Bitmap und Deckkraft hängt das Plugin selbst an die Datei (``patch_materials``): mit
  Bitmaps stürzt der Exporter bei mehreren Objekten ab, die Transparenz schreibt er nicht. Danach die Materialregel auf der Datei: Bilder über 2 MB
  verkleinert (``scale_image``, Eto), nach Material zusammengeführt oder je Objekt.
"""

from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass, field

import Rhino
import System

from . import camera as cam
from . import model as md
from . import rhinocapture
from .rendertaxi_client import glb
from .rendertaxi_client import glbmaterials

# Die Dichte, mit der der glTF-Exporter von Rhino SubD vernetzt (``SubDSurfaceMeshingDensity``, Vorgabe 4).
SUBD_DENSITY = 4
ExportError = md.ExportError


@dataclass
class Prepared:
    """Die vernetzten sichtbaren Objekte — Weltkoordinaten in Einheiten des Dokuments."""

    meshes: list = field(default_factory=list)  # (Name, Mesh, Stellvertreter oder None)
    objects: int = 0
    triangles: int = 0
    skipped: int = 0  # sichtbar, aber ohne Fläche (Kurven, Punkte, Texte …)
    low: list | None = None
    high: list | None = None


def _unit_name(doc) -> str:
    return str(doc.ModelUnitSystem)


def meters_per_unit(doc) -> float:
    meters = rhinocapture.meters_per_unit(doc)
    problem = md.unit_problem(_unit_name(doc), meters)
    if problem:
        raise ExportError(problem)
    return meters


def unit_problem(doc) -> str | None:
    return md.unit_problem(_unit_name(doc), rhinocapture.meters_per_unit(doc))


def _visible_objects(doc):
    settings = Rhino.DocObjects.ObjectEnumeratorSettings()
    settings.NormalObjects = True
    settings.LockedObjects = True
    settings.HiddenObjects = False
    settings.IdefObjects = False
    settings.ReferenceObjects = True
    settings.IncludeLights = False
    settings.IncludeGrips = False
    settings.IncludePhantoms = False
    settings.VisibleFilter = True
    view = doc.Views.ActiveView
    if view is not None and not isinstance(view, Rhino.Display.RhinoPageView):
        settings.ViewportFilter = view.ActiveViewport
    return list(doc.Objects.GetObjectList(settings))


def _layer_visible(doc, attributes) -> bool:
    if attributes is None or not attributes.Visible:
        return False
    index = attributes.LayerIndex
    if 0 <= index < doc.Layers.Count:
        layer = doc.Layers[index]
        return layer is not None and layer.IsVisible
    return True


def _cached_render_mesh(obj):
    try:
        meshes = obj.GetMeshes(Rhino.Geometry.MeshType.Render)
    except Exception:  # noqa: BLE001 — Teile eines Blocks haben keinen Speicher für Netze
        return None
    meshes = [mesh for mesh in (meshes or []) if mesh is not None and mesh.Faces.Count]
    return meshes or None


def _meshes(doc, obj, geometry):
    """Die Dreiecksnetze eines Objekts (Kopien, das Objekt bleibt) — ``None`` ohne Fläche."""
    G = Rhino.Geometry
    if isinstance(geometry, G.Mesh):
        return [geometry.DuplicateMesh()]
    cached = _cached_render_mesh(obj) if obj is not None else None
    if cached:
        return [mesh.DuplicateMesh() for mesh in cached]
    parameters = None
    if obj is not None:
        try:
            parameters = obj.GetRenderMeshParameters()
        except Exception:  # noqa: BLE001
            parameters = None
    if parameters is None:
        parameters = doc.GetMeshingParameters(doc.MeshingParameterStyle)
    if isinstance(geometry, G.SubD):
        mesh = G.Mesh.CreateFromSubD(geometry, SUBD_DENSITY)
        return [mesh] if mesh is not None else None
    brep = None
    if isinstance(geometry, G.Brep):
        brep = geometry
    elif isinstance(geometry, G.Extrusion):
        brep = geometry.ToBrep()
    elif isinstance(geometry, G.Surface):
        brep = geometry.ToBrep()
    if brep is None:
        return None
    meshes = G.Mesh.CreateFromBrep(brep, parameters)
    return [mesh for mesh in meshes or [] if mesh is not None] or None


@dataclass(frozen=True)
class Substitute:
    """Was vom Rendermaterial eines Objekts in die Datei geht (RTX-P-021): Name, Diffusfarbe, Transparenz, Bitmap."""

    name: str
    color: tuple
    transparency: float
    texture: str | None


def _substitute(doc, obj, attributes) -> Substitute | None:
    """Das Rendermaterial eines Objekts (Vorderseite) als ``Substitute`` — ``None`` ohne Material.

    ``GetMaterial(True)`` ist das Material, wie Rhino es anzeigt (bei einem Rendermaterial dessen Simulation);
    ohne zugewiesenes Material (Vorgabe von Rhino, Index −1) gibt es keins: das Objekt steht „ohne Material“.
    """
    try:
        if attributes is not None and attributes.MaterialSource == Rhino.DocObjects.ObjectMaterialSource.MaterialFromLayer:
            index = doc.Layers[attributes.LayerIndex].RenderMaterialIndex
        else:
            index = attributes.MaterialIndex if attributes is not None else -1
        if index < 0:
            return None
        material = obj.GetMaterial(True) if obj is not None else None
        if material is None:
            material = doc.Materials[index] if 0 <= index < doc.Materials.Count else None
        if material is None:
            return None
        color = material.DiffuseColor
        texture = None
        bitmap = material.GetBitmapTexture()
        if bitmap is not None:
            path = bitmap.FileReference.FullPath if bitmap.FileReference is not None else bitmap.FileName
            texture = path if path and os.path.isfile(path) else None
        return Substitute(str(material.Name or "Material"), (color.R, color.G, color.B),
                          float(material.Transparency), texture)
    except Exception:  # noqa: BLE001 — ein Material, das Rhino nicht liefert, heißt: ohne Material
        return None


def _mapped(obj, meshes):
    """Die Texturkoordinaten nach dem Mapping des Objekts (Kanal 1) — ohne Mapping die des Netzes."""
    try:
        mapping, transform = obj.GetTextureMapping(1)
    except Exception:  # noqa: BLE001 — kein Mapping lesbar: die Koordinaten des Netzes
        return meshes
    if mapping is None:
        return meshes
    for mesh in meshes:
        try:
            mesh.SetTextureCoordinates(mapping, transform, False)
        except Exception:  # noqa: BLE001
            continue
    return meshes


def _triangles(mesh) -> int:
    return mesh.Faces.TriangleCount + 2 * mesh.Faces.QuadCount


def prepare(doc, materials: bool = False) -> Prepared:
    """Die sichtbaren Objekte vernetzt, Blöcke aufgelöst — das Dokument bleibt unberührt. ``materials``: je Netz
    der Stellvertreter seines Rendermaterials und die Texturkoordinaten nach dem Mapping des Objekts."""
    prepared = Prepared()
    low = [float("inf")] * 3
    high = [float("-inf")] * 3

    def add(name, meshes, substitute, transform=None) -> None:
        nonlocal low, high
        joined = Rhino.Geometry.Mesh()
        for mesh in meshes:
            joined.Append(mesh)
        if transform is not None:
            joined.Transform(transform)
            if transform.Determinant < 0:
                joined.Flip(True, True, True)  # gespiegelt: Umlaufsinn und Normalen zurück
        if joined.Faces.Count == 0:
            return
        box = joined.GetBoundingBox(True)
        for axis, (lo, hi) in enumerate(((box.Min.X, box.Max.X), (box.Min.Y, box.Max.Y), (box.Min.Z, box.Max.Z))):
            low[axis] = min(low[axis], lo)
            high[axis] = max(high[axis], hi)
        prepared.meshes.append((name, joined, substitute))
        prepared.objects += 1
        prepared.triangles += _triangles(joined)

    for obj in _visible_objects(doc):
        if isinstance(obj, Rhino.DocObjects.InstanceObject):
            try:
                pieces, attributes, transforms = obj.Explode(True)
            except Exception:  # noqa: BLE001 — ein kaputter Block zählt als übersprungen
                prepared.skipped += 1
                continue
            for piece, piece_attributes, transform in zip(pieces, attributes, transforms):
                if piece is None or not _layer_visible(doc, piece_attributes):
                    continue
                meshes = _meshes(doc, piece, piece.Geometry)
                if not meshes:
                    prepared.skipped += 1
                    continue
                if materials:
                    meshes = _mapped(piece, meshes)
                add(obj.Name or piece.Name, meshes, _substitute(doc, piece, piece_attributes) if materials else None,
                    transform)
            continue
        if isinstance(obj, Rhino.DocObjects.ClippingPlaneObject):
            continue
        meshes = _meshes(doc, obj, obj.Geometry)
        if not meshes:
            prepared.skipped += 1
            continue
        if materials:
            meshes = _mapped(obj, meshes)
        add(obj.Name, meshes, _substitute(doc, obj, obj.Attributes) if materials else None)
    if prepared.objects:
        prepared.low, prepared.high = low, high
    return prepared


def clipping_planes(doc) -> int:
    """Wie viele sichtbare Schnittebenen die aktive Ansicht schneiden — sie wirken nicht auf die Datei."""
    view = doc.Views.ActiveView
    if view is None:
        return 0
    viewport_id = view.ActiveViewportID
    count = 0
    settings = Rhino.DocObjects.ObjectEnumeratorSettings()
    settings.VisibleFilter = True
    settings.ObjectTypeFilter = Rhino.DocObjects.ObjectType.ClipPlane
    for obj in doc.Objects.GetObjectList(settings):
        try:
            if viewport_id in list(obj.ClippingPlaneGeometry.ViewportIds()):
                count += 1
        except Exception:  # noqa: BLE001
            continue
    return count


@dataclass
class Estimate:
    objects: int
    triangles: int
    skipped: int
    clipping: int


def scene_bounds(doc):
    """Die Hülle der sichtbaren Objekte ``(min, max)`` in Modelleinheiten, ohne Vernetzen — ``None`` ohne Objekte.

    Für die Schnittebenen einer Parallelkamera im Manifest (``camera.camera_block``); eine Hülle um die Objekte
    reicht, sie darf größer sein als die Netze.
    """
    box = Rhino.Geometry.BoundingBox.Empty
    for obj in _visible_objects(doc):
        if isinstance(obj, Rhino.DocObjects.ClippingPlaneObject):
            continue
        try:
            box.Union(obj.Geometry.GetBoundingBox(True))
        except Exception:  # noqa: BLE001 — ein Objekt ohne Hülle zählt nicht
            continue
    if not box.IsValid:
        return None
    return [box.Min.X, box.Min.Y, box.Min.Z], [box.Max.X, box.Max.Y, box.Max.Z]


def estimate(doc) -> Estimate:
    """Sichtbare Objekte und Dreiecke — der Hinweis im Fenster, **bevor** exportiert wird."""
    prepared = prepare(doc)
    return Estimate(prepared.objects, prepared.triangles, prepared.skipped, clipping_planes(doc))


def _options(materials: bool):
    options = Rhino.FileIO.FileGltfWriteOptions()
    options.MapZToY = True
    options.ExportMaterials = bool(materials)
    # Ein Objekt ohne Material steht „ohne Material“ (11.6), nicht in seiner Anzeigefarbe.
    options.UseDisplayColorForUnsetMaterials = False
    options.ExportTextureCoordinates = bool(materials)
    options.ExportVertexNormals = True
    options.ExportOpenMeshes = True
    options.ExportVertexColors = False
    options.UseDracoCompression = False
    options.ExportLayers = False
    return options


# Name einer Fassung ohne Bild im kopflosen Dokument; die Regel führt sie wieder unter dem Materialnamen zusammen.
_WITHOUT_IMAGE = " (ohne Bild)"


def _write(doc, prepared: Prepared, target: str, materials: bool) -> tuple[dict, dict, dict]:
    """Die Netze in ein kopfloses Dokument mit der Einheit des Nutzers — und von dort als GLB.

    Je ``Substitute`` ein Material im kopflosen Dokument mit Diffusfarbe und Transparenz, **ohne** Bitmap: mit
    Bitmaps stürzt der glTF-Exporter von Rhino 8.35 bei mehreren Objekten ab, nicht jedes Mal
    (gemessen 14./15.10.2026, ``docs/measurements/2026-10-15-materialien.md``). Die Bilder hängt
    ``glbmaterials.patch_materials`` danach an, ebenso die Deckkraft, die der Exporter nicht schreibt. Zurück kommen
    ``(Dateiname → Materialname, Dateiname → Bilddatei, Dateiname → Deckkraft)``.
    """
    headless = Rhino.RhinoDoc.CreateHeadless(None)
    names: dict = {}
    images: dict = {}
    alphas: dict = {}
    try:
        headless.ModelUnitSystem = doc.ModelUnitSystem
        slots: dict = {}
        for name, mesh, substitute in prepared.meshes:
            attributes = Rhino.DocObjects.ObjectAttributes()
            if name:
                attributes.Name = name
            if substitute is not None and substitute.texture and mesh.TextureCoordinates.Count != mesh.Vertices.Count:
                # Ein Netz ohne Texturkoordinaten bekommt die Fassung ohne Bild.
                substitute = dataclasses.replace(substitute, texture=None, name=substitute.name + _WITHOUT_IMAGE)
            if materials and substitute is not None:
                if substitute not in slots:
                    material = Rhino.DocObjects.Material()
                    material.Name = substitute.name
                    material.DiffuseColor = System.Drawing.Color.FromArgb(*substitute.color)
                    material.Transparency = substitute.transparency
                    slots[substitute] = headless.Materials.Add(material)
                    written = headless.Materials[slots[substitute]].Name
                    names[written] = substitute.name[:-len(_WITHOUT_IMAGE)] \
                        if substitute.name.endswith(_WITHOUT_IMAGE) else substitute.name
                    if substitute.texture and written not in images:
                        images[written] = substitute.texture
                    if substitute.transparency > 0:
                        alphas[written] = 1.0 - substitute.transparency
                attributes.MaterialIndex = slots[substitute]
                attributes.MaterialSource = Rhino.DocObjects.ObjectMaterialSource.MaterialFromObject
            headless.Objects.AddMesh(mesh, attributes)
        ok = Rhino.FileIO.FileGltf.Write(target, headless, _options(materials))
    finally:
        headless.Dispose()
    if not ok or not os.path.exists(target):
        raise ExportError("Rhino hat den glTF-Export nicht ausgeführt.")
    return names, images, alphas


def _image_file(path: str):
    """``(Bytes, mimeType)`` einer Bilddatei (PNG oder JPEG am Kopf erkannt) — ``None`` sonst."""
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError:
        return None
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return data, "image/png"
    if data[:2] == b"\xff\xd8":
        return data, "image/jpeg"
    return None


def scale_image(data: bytes, mime: str, factor: float):
    """Ein Bild um ``factor`` je Seite verkleinert neu kodieren — mit ``Eto.Drawing`` (Rhino 8, macOS und Windows).

    Ein PNG mit Alphakanal (laut Kopf) bleibt PNG, alles andere wird JPEG.
    """
    import Eto.Drawing as drawing
    import System

    stream = System.IO.MemoryStream(System.Array[System.Byte](bytearray(data)))
    source = drawing.Bitmap(stream)
    width, height = source.Width, source.Height
    if not width or not height:
        return None
    scaled = drawing.Bitmap(source, max(1, int(width * factor)), max(1, int(height * factor)),
                            drawing.ImageInterpolation.High)
    png = mime == "image/png" and glbmaterials.png_has_alpha(data)
    out = System.IO.MemoryStream()
    scaled.Save(out, drawing.ImageFormat.Png if png else drawing.ImageFormat.Jpeg)
    return bytes(bytearray(out.ToArray())), "image/png" if png else "image/jpeg"


def named_view_cameras(doc, size, bounds) -> list:
    """Jede benannte Ansicht als Kamera für die Datei — Seitenverhältnis der Aufnahme, Name der Ansicht.

    ``bounds``: die Hülle der exportierten Netze in Modelleinheiten; aus ihr kommen Nah- und Fernebene einer
    Parallelansicht (``camera.gltf_camera``), nie aus der gespeicherten Fernebene (Review F-01).
    """
    meters = rhinocapture.meters_per_unit(doc)
    cameras = []
    table = doc.NamedViews
    for index in range(table.Count):
        try:
            info = Rhino.DocObjects.ViewportInfo(table[index].Viewport)
            view = rhinocapture.view_of(info, size)
            cameras.append(cam.gltf_camera(view, size, meters, table[index].Name or f"Ansicht {index + 1}", bounds))
        except Exception:  # noqa: BLE001 — eine Ansicht ohne gültige Kamera bleibt weg
            continue
    return cameras


@dataclass
class Model:
    file: object
    geometry: dict
    meters: float
    triangles: int
    cameras: int
    exporter_scale: float


def export_model(doc, directory: str, size, limits: dict | None = None, materials: bool = True,
                 max_triangles: int = glb.MAX_TRIANGLES, merge: str = glbmaterials.MERGE_BY_MATERIAL) -> Model:
    """Die sichtbaren Objekte als GLB nach ``directory/model/scene.glb`` — geprüft, in Metern, mit Kameras.

    ``materials`` und ``merge``: die Materialregel (``capture-manifest.md`` 11.6) — „ohne Materialien“ und „nach
    Material“ oder „Objekte behalten“, auf der Datei nach Maßstab und Kameras.

    Grenzen (Dreiecke, ``maxGeometryBytes``, Einheit) greifen **vor** dem Senden. Die Datei liegt im
    Übernahmeordner unter dem Nutzerprofil (0700) und geht mit ihm, sobald die Übernahme abgeschlossen ist.
    """
    meters = meters_per_unit(doc)
    prepared = prepare(doc, materials)
    if prepared.objects == 0 or prepared.triangles == 0:
        raise ExportError("Keine sichtbare Geometrie: das Modell wäre leer.")
    problem = md.too_many(prepared.triangles, max_triangles)
    if problem:
        raise ExportError(problem)
    target = os.path.join(directory, *md.MODEL_PATH.split("/"))
    os.makedirs(os.path.dirname(target), mode=0o700, exist_ok=True)
    names, images, alphas = _write(doc, prepared, target, materials)
    try:
        document = glb.read_glb_json(target)
        triangles = glb.glb_triangles(document)
        bounds = glb.world_bounds(document)
    except glb.GlbError as error:
        raise ExportError(str(error)) from None
    if glb.external_references(document):
        raise ExportError("Die GLB-Datei verweist auf Dateien außerhalb ihrer selbst.")
    if triangles == 0 or bounds is None:
        raise ExportError("Keine sichtbare Geometrie: das Modell wäre leer.")
    problem = md.too_many(triangles, max_triangles)
    if problem:
        raise ExportError(problem)
    factor = md.exporter_scale(prepared.low, prepared.high, *bounds)
    correction = meters / factor
    if abs(correction - 1.0) > md.SCALE_TOLERANCE:
        glb.scale_scene(target, correction)
    try:
        cameras = glb.add_cameras(target, named_view_cameras(doc, size, (prepared.low, prepared.high)))
    except Exception:  # noqa: BLE001 — ohne Kameras bleibt die Datei, wie der Exporter sie schrieb
        cameras = 0
    try:
        if materials:
            files = {name: _image_file(path) for name, path in images.items()}
            # Ein Objekt ohne Material bekommt vom Exporter „Default Material“ (gemessen): es steht ohne Material.
            glbmaterials.patch_materials(target, textures={name: f for name, f in files.items() if f is not None},
                                         alphas=alphas, unassigned=() if "Default Material" in names else
                                         ("Default Material",))
        report = glbmaterials.apply_material_rule(target, materials=materials, merge=merge, scale_image=scale_image,
                                                  material_names=names)
    except glb.GlbError as error:
        raise ExportError(str(error)) from None
    problem = md.too_big(os.path.getsize(target), limits)
    if problem:
        raise ExportError(problem)
    file = md.model_file(target, triangles, cameras, factor, correction, glbmaterials.report_note(report, materials))
    return Model(file, md.geometry_dict(meters), meters, triangles, cameras, factor)


def probe(doc) -> dict:
    """``geometryExport`` und ``cameraExport`` aus dem laufenden Rhino — nur mit Modell gemeldet."""
    unit = unit_problem(doc)
    view = doc.Views.ActiveView
    return {
        "geometryExport": {"state": "requires-user-action" if unit else "available"},
        "cameraExport": {"state": "available" if view is not None and not isinstance(
            view, Rhino.Display.RhinoPageView) else "requires-user-action"},
    }
