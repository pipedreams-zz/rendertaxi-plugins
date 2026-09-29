"""Modellweg (RTX-C4D-003, #187) — GLB und Kamera für Capture-Manifest 1.2.0.

Vertrag: ``integrations/_shared/docs/capture-manifest.md``, Abschnitt 11
(ADR 0032). Die Datei folgt glTF 2.0 — Meter, rechtshändig, +Y oben —, die
Kamera steht im selben **Exportraum**. Jede benutzte Funktion und Konstante ist
gegen die Dokumentation von Cinema 4D **2026** belegt (Tabelle „API-Belege
2026" in ``integrations/cinema-4d/README.md``); was am Host erst gemessen wird,
steht als QC-01, QC-02, QC-09 in ``integrations/cinema-4d/docs/open-questions.md``
und wird mit ``tools/measure_model_way.py`` am Mac gemessen.

* **Was exportiert wird.** ``BaseDocument.Polygonize`` wandelt eine Kopie des
  Dokuments in Polygonobjekte; das Dokument des Nutzers bleibt unberührt.
  Davon gehen nur die **sichtbaren** Polygonobjekte in ein neues, leeres
  Exportdokument — sichtbar heißt: der Punkt im Objekt-Manager (bei Viewport
  der Editor-, bei Beauty der Renderpunkt, ``MODE_UNDEF`` erbt vom
  Elternobjekt) und die Ebene lassen es zu. Jedes Objekt wird mit seiner
  Weltmatrix in die Punkte **gebacken** (gespiegelte Matrizen kehren den
  Umlaufsinn um) und ohne Materialien, UVs und Animation übernommen; nur ein
  Phong-Tag geht mit. So liegen Punkte der GLB und die Hülle, die das Plugin
  vorher ausrechnet, im selben Raum.
* **Wie exportiert wird.** Der glTF-Exporter von Cinema 4D
  (``FORMAT_GLTFEXPORT``) mit „GLB (Binary)" und **Flip Z** — laut Hilfe 2026
  „glTF uses the right-hand rule (Z axis points to the front), while Cinema 4D
  uses the left-hand rule (Z axis points to the rear). The difference is in
  the way scene objects are mirrored along the Z axis." Das ist ``MIRROR``
  aus ``tools/coordinates.mjs``. Die Einstellungen des Exporters sind die des
  Nutzers: das Plugin setzt sie für den einen Export und stellt sie danach
  wieder her.
* **Prüfen statt glauben.** Der Maßstab des Exporters („Scale") ist über die
  Python-API nicht erreichbar. Nach dem Export liest das Plugin deshalb die
  Hülle der GLB-Datei und vergleicht sie mit der vorher ausgerechneten Hülle
  der Szene: stimmt sie gespiegelt an Z bis auf einen gleichförmigen Faktor
  überein, ist dieser Faktor der Maßstab des Exporters, und der Plugin setzt
  die Szene über einen Wurzelknoten auf Meter (``glb.scale_scene``). Stimmt
  sie nicht überein — keine Spiegelung an Z, verschoben, verzerrt —, geht kein
  Modell mit (QC-01, QC-02).
* **Einheit.** ``DOCUMENT_DOCUNIT`` ist ein ``UnitScaleData`` („Project
  scale"); ``GetUnitScale()`` liefert ``(scale, unit)`` mit ``DOCUMENT_UNIT_*``.
  Eine Einheit des Dokuments ist ``scale`` × (Meter der Einheit) lang. Ohne
  lesbaren Projektmaßstab geht kein Modell mit.
* **Kamera.** Die Kamera der Renderansicht (``GetSceneCamera``), auch die
  Editor-Kamera. Nur perspektivisch; ``fieldOfView`` horizontal aus
  ``2 · atan(Sensorbreite / (2 · Brennweite))`` — die Sensorgröße ist laut
  Hilfe die horizontale Breite. Was der Vertrag nicht darstellen kann (Film
  Offset, Parallelprojektion, Stereo, sphärisch, Linsenverzerrung,
  Pixelseitenverhältnis ≠ 1, Filmformat ≠ Bildformat), führt zu ``None`` mit
  einem Grund, den der Dialog **vor** dem Senden zeigt.

Das Modul erfindet nichts: was nicht belegt oder nicht geprüft ist, geht nicht
mit.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass

import c4d

from . import capture
from .rendertaxi_client import glb
from .rendertaxi_client import manifest as mf

VIEWPORT = "viewport"
MODEL_PATH = glb.MODEL_PATH
# Die Grenze des Servers aus dem gemeinsamen Client; hier gelesen, damit ein
# Test sie für einen Lauf senken kann.
MAX_TRIANGLES = glb.MAX_TRIANGLES

# Meter je Einheit (UnitScaleData.SetUnitScale, Referenz 2026) — Definitionen, keine Messung.
_UNIT_METERS = {
    "DOCUMENT_UNIT_KM": 1000.0,
    "DOCUMENT_UNIT_M": 1.0,
    "DOCUMENT_UNIT_CM": 0.01,
    "DOCUMENT_UNIT_MM": 0.001,
    "DOCUMENT_UNIT_MICRO": 0.000001,
    "DOCUMENT_UNIT_MILE": 1609.344,
    "DOCUMENT_UNIT_YARD": 0.9144,
    "DOCUMENT_UNIT_FOOT": 0.3048,
    "DOCUMENT_UNIT_INCH": 0.0254,
}
UNIT_METERS = {getattr(c4d, name): meters for name, meters in _UNIT_METERS.items() if hasattr(c4d, name)}

# Toleranzen: Hüllenvergleich relativ zur Diagonale (die GLB speichert float32),
# Maßstab, Film Offset und Seitenverhältnisse absolut.
BOUNDS_TOLERANCE = 1e-4
SCALE_TOLERANCE = 1e-6
TOLERANCE = 1e-6
# Die kleinste Nahgrenze, wenn die Kamera nicht abschneidet: der Vertrag verlangt near > 0.
NO_NEAR_CLIP = 0.000001


class ExportError(RuntimeError):
    """Ein Grund, warum das Modell nicht gesendet wird — lesbar, ohne Pfad."""


# --------------------------------------------------------------------------
# Zahlen und Raum
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


def to_export_point(point, meters: float) -> list:
    """Ein Punkt aus Cinema 4D (Y oben, linkshändig) in den Exportraum: ``s · (x, y, −z)`` (MIRROR)."""
    x, y, z = (float(c) * meters for c in point)
    return [length(x), length(y), length(-z)]


def to_export_direction(vector) -> list:
    """Eine Richtung, gespiegelt wie die Punkte, normiert und auf neun Nachkommastellen."""
    x, y, z = (float(c) for c in vector)
    norm = math.sqrt(x * x + y * y + z * z)
    if norm == 0:
        raise ExportError("Die Kamera hat keine Blickrichtung.")
    return [_num(x / norm, 9), _num(y / norm, 9), _num(-z / norm, 9)]


def perspective_fov(aperture: float, focus: float) -> tuple[str, float]:
    """``(Achse, Winkel im Bogenmaß)``: die Sensorgröße ist die horizontale Breite (Hilfe 2026)."""
    return "horizontal", 2.0 * math.atan(float(aperture) / (2.0 * float(focus)))


def camera_dict(*, position, direction, up, fov: tuple[str, float], near: float | None, far: float | None,
                meters: float) -> dict:
    """Der Kamerablock des Vertrags (Abschnitt 11.3) aus Werten im Weltraum von Cinema 4D."""
    near_m = NO_NEAR_CLIP if near is None else max(length(near * meters), NO_NEAR_CLIP)
    far_m = None if far is None else length(far * meters)
    if far_m is not None and far_m <= near_m:
        far_m = None
    return {
        "space": "export",
        "projection": "perspective",
        "position": to_export_point(position, meters),
        "direction": to_export_direction(direction),
        "up": to_export_direction(up),
        "fieldOfView": {"axis": fov[0], "angle": angle(fov[1])},
        "clip": {"near": near_m, "far": far_m},
    }


def geometry_dict(meters: float) -> dict:
    """``geometry`` für die GLB-Datei: Weltraum von Cinema 4D, unverschoben exportiert."""
    return {
        "assetPath": MODEL_PATH,
        "units": {"sourceUnitScaleToMeter": _num(meters, 6)},
        "axes": {"handedness": "left", "upAxis": "y"},
        "origin": [0, 0, 0],
    }


def mirrored_bounds(low, high) -> tuple[list, list]:
    """Die Hülle der Szene nach ``MIRROR`` (Z negiert): Tiefe tauscht Anfang und Ende."""
    return [low[0], low[1], -high[2]], [high[0], high[1], -low[2]]


def _fits(expected_low, expected_high, factor, glb_low, glb_high, tolerance) -> bool:
    return all(abs(glb_low[a] - factor * expected_low[a]) <= tolerance
               and abs(glb_high[a] - factor * expected_high[a]) <= tolerance for a in range(3))


def exporter_scale(scene_low, scene_high, glb_low, glb_high) -> float:
    """Der Maßstab des Exporters (GLB-Einheiten je Einheit des Dokuments) — gemessen an der Hülle.

    Erwartet wird die Hülle der Szene, an Z gespiegelt, bis auf einen
    gleichförmigen Faktor. Alles andere ist ein ``ExportError``: eine
    fehlende Spiegelung nennt QC-01, jede andere Abweichung QC-01 und QC-02.
    """
    scene_diagonal = math.dist(scene_low, scene_high)
    glb_diagonal = math.dist(glb_low, glb_high)
    if scene_diagonal == 0 or glb_diagonal == 0:
        raise ExportError("Das Modell hat keine Ausdehnung.")
    factor = glb_diagonal / scene_diagonal
    tolerance = BOUNDS_TOLERANCE * glb_diagonal
    if _fits(*mirrored_bounds(scene_low, scene_high), factor, glb_low, glb_high, tolerance):
        return factor
    if _fits(scene_low, scene_high, factor, glb_low, glb_high, tolerance):
        raise ExportError("Der glTF-Export hat nicht an der Z-Achse gespiegelt („Flip Z“); die Datei wäre "
                          "seitenverkehrt (QC-01). Das Modell wird nicht gesendet.")
    raise ExportError("Die GLB-Datei liegt nicht dort, wo die Szene liegt (verschoben oder verzerrt); "
                      "Maßstab und Achsen des Exports sind nicht belegt (QC-01, QC-02).")


# --------------------------------------------------------------------------
# Einheit
# --------------------------------------------------------------------------


def meters_per_unit(doc) -> float:
    """So viele Meter ist eine Einheit des Dokuments lang — aus dem Projektmaßstab, oder ``ExportError``."""
    data = doc[c4d.DOCUMENT_DOCUNIT]
    try:
        scale, unit = data.GetUnitScale()
    except (AttributeError, TypeError, ValueError):
        raise ExportError("Der Projektmaßstab des Dokuments ist nicht lesbar (QC-02). "
                          "Das Modell wird nicht gesendet.") from None
    meters = UNIT_METERS.get(unit)
    if meters is None or not (float(scale) > 0 and math.isfinite(float(scale))):
        raise ExportError("Der Projektmaßstab hat keine Einheit mit Bezug zu Metern (QC-02). In "
                          "Projekteinstellungen › Projektmaßstab eine Längeneinheit wählen.")
    return float(scale) * meters


def unit_problem(doc) -> str | None:
    try:
        meters_per_unit(doc)
    except ExportError as error:
        return str(error)
    return None


def model_problem(doc) -> str | None:
    """Warum kein Modell mitgehen kann, bevor exportiert wird: Exporter oder Projektmaßstab."""
    if not gltf_available():
        return "Der glTF-Exporter ist in diesem Cinema 4D nicht verfügbar."
    return unit_problem(doc)


# --------------------------------------------------------------------------
# Szene vorbereiten
# --------------------------------------------------------------------------


def _walk(obj):
    while obj is not None:
        yield obj
        yield from _walk(obj.GetDown())
        obj = obj.GetNext()


def visible(obj, doc, kind: str) -> bool:
    """Sichtbar in der Aufnahme: Editor- (Viewport) oder Renderpunkt (Beauty) samt Erbe, und die Ebene.

    ``MODE_ON``/``MODE_OFF`` gelten „regardless of the state of any parent
    object"; ``MODE_UNDEF`` übernimmt den Zustand des Elternobjekts (Referenz 2026).
    """
    node = obj
    while node is not None:
        mode = node.GetEditorMode() if kind == VIEWPORT else node.GetRenderMode()
        if mode == c4d.MODE_OFF:
            return False
        if mode == c4d.MODE_ON:
            break
        node = node.GetUp()
    try:
        layer = obj.GetLayerData(doc)
    except (AttributeError, TypeError):
        layer = None
    if layer:
        return bool(layer.get("view" if kind == VIEWPORT else "render", True))
    return True


def _determinant(m) -> float:
    a, b, c = m.v1, m.v2, m.v3
    return (a.x * (b.y * c.z - b.z * c.y) - a.y * (b.x * c.z - b.z * c.x) + a.z * (b.x * c.y - b.y * c.x))


def _reversed(polygon):
    """Die Punktreihenfolge umkehren — die Normale kehrt sich um (Hilfe: „Reversing the Normals")."""
    if polygon.IsTriangle():
        return c4d.CPolygon(polygon.a, polygon.c, polygon.b)
    return c4d.CPolygon(polygon.a, polygon.d, polygon.c, polygon.b)


@dataclass
class Prepared:
    """Das Exportdokument und was das Plugin darüber vorher weiß (Einheiten des Dokuments)."""

    document: object
    objects: int
    triangles: int
    low: list | None
    high: list | None


def _baked(obj, mesh_index: int):
    """Ein neues Polygonobjekt mit Weltkoordinaten — oder ``None`` ohne Polygone."""
    points = obj.GetAllPoints()
    polygons = obj.GetAllPolygons()
    if not points or not polygons:
        return None, 0
    matrix = obj.GetMg()
    flip = _determinant(matrix) < 0
    mesh = c4d.PolygonObject(len(points), len(polygons))
    mesh.SetName(obj.GetName() or f"Objekt {mesh_index}")
    mesh.SetAllPoints([matrix * point for point in points])
    triangles = 0
    for index, polygon in enumerate(polygons):
        mesh.SetPolygon(index, _reversed(polygon) if flip else polygon)
        triangles += 1 if polygon.IsTriangle() else 2
    phong = obj.GetTag(c4d.Tphong)
    if phong is not None:
        mesh.InsertTag(phong.GetClone())
    mesh.Message(c4d.MSG_UPDATE)
    return mesh, triangles


def prepare(doc, kind: str) -> Prepared:
    """Die sichtbaren Polygone des Dokuments in einem eigenen Exportdokument — das Dokument bleibt unberührt."""
    source = doc.Polygonize(False)
    if source is None:
        raise ExportError("Cinema 4D konnte die Szene nicht in Polygone wandeln.")
    target = c4d.documents.BaseDocument()
    try:
        target[c4d.DOCUMENT_DOCUNIT] = doc[c4d.DOCUMENT_DOCUNIT]
        objects = triangles = 0
        low = [math.inf] * 3
        high = [-math.inf] * 3
        for obj in _walk(source.GetFirstObject()):
            if not isinstance(obj, c4d.PolygonObject) or not visible(obj, source, kind):
                continue
            mesh, count = _baked(obj, objects)
            if mesh is None:
                continue
            target.InsertObject(mesh)
            objects += 1
            triangles += count
            for point in mesh.GetAllPoints():
                for axis, value in enumerate((point.x, point.y, point.z)):
                    low[axis] = min(low[axis], value)
                    high[axis] = max(high[axis], value)
    except Exception:
        c4d.documents.KillDocument(target)
        raise
    finally:
        c4d.documents.KillDocument(source)
    if objects == 0:
        return Prepared(target, 0, 0, None, None)
    return Prepared(target, objects, triangles, low, high)


@dataclass
class Estimate:
    objects: int
    triangles: int


def estimate(doc, kind: str) -> Estimate:
    """Sichtbare Objekte und Dreiecke — der Hinweis im Dialog, **bevor** exportiert wird."""
    prepared = prepare(doc, kind)
    c4d.documents.KillDocument(prepared.document)
    return Estimate(prepared.objects, prepared.triangles)


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------

# Die Einstellungen des glTF-Exporters für den einen Export (Ressource fgltfexporter, 2026).
# FILEFORMAT und FLIPZ sind Pflicht: ohne sie wäre weder GLB noch die Spiegelung gesichert.
_REQUIRED = ("GLTFEXPORTER_FILEFORMAT", "GLTFEXPORTER_FLIPZ")
_SETTINGS = (
    ("GLTFEXPORTER_FILEFORMAT", "GLTFEXPORTER_FILEFORMAT_GLB"),
    ("GLTFEXPORTER_FLIPZ", True),
    ("GLTFEXPORTER_CURRENTFRAME", True),
    ("GLTFEXPORTER_PSRANIMATIONS", False),
    ("GLTFEXPORTER_MORPHANIMATIONS", False),
    ("GLTFEXPORTER_SKINANIMATIONS", False),
    ("GLTFEXPORTER_BAKEANIMATIONS", False),
    ("GLTFEXPORTER_TEXTURES", False),
    ("GLTFEXPORTER_CAMERAS", False),
    ("GLTFEXPORTER_INSTANCES", False),
    ("GLTFEXPORTER_NORMALS", True),
    ("GLTFEXPORTER_UVS", False),
    ("GLTFEXPORTER_DOUBLESIDED", False),
)


def exporter_settings() -> dict:
    """``{Parameter-ID: Wert}`` — nur, was dieses c4d-Modul kennt; fehlt ein Pflichtwert, ``ExportError``."""
    settings = {}
    for name, value in _SETTINGS:
        pid = getattr(c4d, name, None)
        if isinstance(value, str):
            value = getattr(c4d, value, None)
        if pid is None or value is None:
            if name in _REQUIRED:
                raise ExportError("Der glTF-Exporter dieses Cinema 4D kennt „GLB“ oder „Flip Z“ nicht.")
            continue
        settings[pid] = value
    return settings


def gltf_available() -> bool:
    fmt = getattr(c4d, "FORMAT_GLTFEXPORT", None)
    return fmt is not None and c4d.plugins.FindPlugin(fmt, c4d.PLUGINTYPE_SCENESAVER) is not None


def _exporter():
    fmt = getattr(c4d, "FORMAT_GLTFEXPORT", None)
    plug = c4d.plugins.FindPlugin(fmt, c4d.PLUGINTYPE_SCENESAVER) if fmt is not None else None
    if plug is None:
        raise ExportError("Der glTF-Exporter ist in diesem Cinema 4D nicht verfügbar.")
    data: dict = {}
    if not plug.Message(c4d.MSG_RETRIEVEPRIVATEDATA, data) or data.get("imexporter") is None:
        raise ExportError("Die Einstellungen des glTF-Exporters sind nicht erreichbar.")
    return fmt, data["imexporter"]


def _save(document, target: str) -> None:
    """``SaveDocument`` mit den Einstellungen des Plugins; die des Nutzers danach zurück.

    Zurück heißt: der ganze Container, wie er war — auch ohne die Parameter, die vorher fehlten.
    """
    fmt, exporter = _exporter()
    settings = exporter_settings()
    saved = exporter.GetDataInstance().GetClone(c4d.COPYFLAGS_NONE)
    try:
        for pid, value in settings.items():
            exporter[pid] = value
        ok = c4d.documents.SaveDocument(document, target, c4d.SAVEDOCUMENTFLAGS_DONTADDTORECENTLIST, fmt)
    finally:
        exporter.SetData(saved, False)  # ersetzen, nicht zusammenführen
    if not ok or not os.path.exists(target):
        raise ExportError("Cinema 4D hat den glTF-Export nicht ausgeführt.")


@dataclass
class Model:
    file: mf.CaptureFile
    geometry: dict
    meters: float
    triangles: int
    exporter_scale: float  # gemessen: GLB-Einheiten je Einheit des Dokuments, vor der Korrektur


def export_model(doc, kind: str, directory: str) -> Model:
    """Die sichtbaren Objekte als GLB nach ``directory/model/scene.glb`` — geprüft, in Metern.

    Die Datei bleibt im Capture-Verzeichnis (0700); Meldungen tragen keinen Pfad.
    """
    meters = meters_per_unit(doc)
    prepared = prepare(doc, kind)
    try:
        if prepared.objects == 0 or prepared.triangles == 0:
            raise ExportError("Keine sichtbare Geometrie: das Modell wäre leer.")
        if prepared.triangles > MAX_TRIANGLES:
            raise ExportError(f"Das Modell hat {prepared.triangles:,} Dreiecke; der Server nimmt höchstens "
                              f"{MAX_TRIANGLES:,} an.".replace(",", " "))
        target = os.path.join(directory, *MODEL_PATH.split("/"))
        os.makedirs(os.path.dirname(target), mode=0o700, exist_ok=True)
        _save(prepared.document, target)
    finally:
        c4d.documents.KillDocument(prepared.document)
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
    if triangles > MAX_TRIANGLES:
        raise ExportError(f"Das Modell hat {triangles:,} Dreiecke; der Server nimmt höchstens "
                          f"{MAX_TRIANGLES:,} an.".replace(",", " "))
    factor = exporter_scale(prepared.low, prepared.high, *bounds)
    correction = meters / factor
    if abs(correction - 1.0) > SCALE_TOLERANCE:
        glb.scale_scene(target, correction)
    sha, size = mf.sha256_file(target)
    scaled = (f"Maßstab des Exporters {factor:g} je Einheit gemessen, auf Meter gesetzt ({correction:g})"
              if abs(correction - 1.0) > SCALE_TOLERANCE else "Maßstab des Exporters in Metern gemessen")
    note = (f"glTF 2.0 binär aus dem glTF-Exporter von Cinema 4D (Flip Z): sichtbare Objekte polygonisiert, "
            f"Weltkoordinaten, ohne Animation, Materialien und UVs; {triangles} Dreiecke; {scaled}.")
    file = mf.CaptureFile(role="model", path=MODEL_PATH, media_type=mf.MODEL_MEDIA_TYPE, image=None,
                          note=note, byte_size=size, sha256=sha)
    return Model(file, geometry_dict(meters), meters, triangles, factor)


# --------------------------------------------------------------------------
# Kamera
# --------------------------------------------------------------------------


def _get(obj, name: str, default=None):
    pid = getattr(c4d, name, None)
    if pid is None:
        return default
    try:
        value = obj[pid]
    except (AttributeError, TypeError, KeyError):
        return default
    return default if value is None else value


def probe(doc, size: tuple[int, int]) -> dict:
    """``geometryExport`` und ``cameraExport`` aus dem laufenden Cinema 4D — nur mit Modell gemeldet."""
    return {
        "geometryExport": {"state": "available" if gltf_available() else "unavailable"},
        "cameraExport": {"state": "requires-user-action" if camera_problem(doc, size) else "available"},
    }


def render_camera(doc):
    """Die Kamera, aus der gerendert wird: Szenenkamera der Renderansicht oder die Editor-Kamera."""
    view = capture.render_view(doc)
    return view.GetSceneCamera(doc) if view is not None else None


def camera_problem(doc, size: tuple[int, int]) -> str | None:
    """Warum **keine** Kamera mitgeht — für den Dialog, bevor gesendet wird."""
    camera = render_camera(doc)
    if camera is None or not isinstance(camera, c4d.CameraObject):
        return "Die Renderansicht hat keine Kamera."
    if camera.GetProjection() != c4d.Pperspective:
        return ("Die Kamera ist nicht perspektivisch; wie weit eine Parallelkamera in Cinema 4D reicht, "
                "ist nicht belegt (QC-09).")
    if not (camera.GetFocus() > 0 and camera.GetAperture() > 0):
        return "Brennweite oder Sensorgröße der Kamera ist nicht positiv."
    if (abs(float(_get(camera, "CAMERAOBJECT_FILM_OFFSET_X", 0.0))) > TOLERANCE
            or abs(float(_get(camera, "CAMERAOBJECT_FILM_OFFSET_Y", 0.0))) > TOLERANCE):
        return "Die Kamera hat Film Offset; der Vertrag kennt keine Bildverschiebung."
    if _get(camera, "CAMERAOBJECT_STEREO_MODE", 0) != getattr(c4d, "CAMERAOBJECT_STEREO_MODE_MONO", 0):
        return "Die Kamera ist eine Stereokamera; der Vertrag kennt eine Ansicht."
    if _get(camera, "CAMERAOBJECT_SPC_ENABLE", False):
        return "Die Kamera ist sphärisch; der Vertrag kennt nur perspektivisch und parallel."
    if (abs(float(_get(camera, "CAMERAOBJECT_LENS_DISTORTION_QUAD", 0.0))) > TOLERANCE
            or abs(float(_get(camera, "CAMERAOBJECT_LENS_DISTORTION_CUBIC", 0.0))) > TOLERANCE):
        return "Die Kamera hat Linsenverzerrung; der Vertrag kennt sie nicht."
    rd = doc.GetActiveRenderData()
    pixel = float(_get(rd, "RDATA_PIXELASPECT", 1.0))
    if abs(pixel - 1.0) > TOLERANCE:
        return "Das Pixelseitenverhältnis ist nicht 1:1; das Sichtfeld wäre auf der zweiten Achse falsch."
    film = _get(rd, "RDATA_FILMASPECT", None)
    width, height = size
    if film is not None and abs(float(film) - width / height) > 1e-3:
        return (f"Das Filmformat ({float(film):.3f}) weicht vom Bild ({width} × {height}) ab; "
                "das Sichtfeld wäre auf der zweiten Achse falsch.")
    axis, value = perspective_fov(camera.GetAperture(), camera.GetFocus())
    reported = _get(camera, "CAMERAOBJECT_FOV", None)
    if isinstance(reported, (int, float)) and not (
            abs(float(reported) - value) <= TOLERANCE or abs(float(reported) - math.degrees(value)) <= 1e-4):
        return ("Das Sichtfeld von Cinema 4D (CAMERAOBJECT_FOV) weicht von 2·atan(Sensor / (2 · Brennweite)) "
                "ab (QC-09).")
    return None


def camera_block(doc, size: tuple[int, int], meters: float) -> dict | None:
    """Die Kamera der Aufnahme im Exportraum — ``None``, wenn ``camera_problem`` einen Grund nennt.

    ``size`` sind die Maße des aufgenommenen Bildes (nicht der Voreinstellung:
    „Blickpunkt-Rahmen" ändert sie).
    """
    if camera_problem(doc, size):
        return None
    camera = render_camera(doc)
    matrix = camera.GetMg()
    near = (float(_get(camera, "CAMERAOBJECT_NEAR_CLIPPING", 0.0))
            if _get(camera, "CAMERAOBJECT_NEAR_CLIPPING_ENABLE", False) else None)
    far = (float(_get(camera, "CAMERAOBJECT_FAR_CLIPPING", 0.0))
           if _get(camera, "CAMERAOBJECT_FAR_CLIPPING_ENABLE", False) else None)
    # Cinema 4D: die Kamera blickt entlang ihrer +Z-Achse, +Y ist oben (v3, v2 der Weltmatrix).
    position = (matrix.off.x, matrix.off.y, matrix.off.z)
    direction = (matrix.v3.x, matrix.v3.y, matrix.v3.z)
    up = (matrix.v2.x, matrix.v2.y, matrix.v2.z)
    return camera_dict(position=position, direction=direction, up=up,
                       fov=perspective_fov(camera.GetAperture(), camera.GetFocus()),
                       near=near, far=far, meters=meters)
