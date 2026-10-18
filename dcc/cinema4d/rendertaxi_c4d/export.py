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
  Umlaufsinn um) und ohne UVs und Animation übernommen. So liegen Punkte der
  GLB und die Hülle, die das Plugin vorher ausrechnet, im selben Raum.
* **Normalen wie in Cinema 4D (RTX-C4D-011).** Mit geht nicht der Phong-Tag
  allein, sondern die Normalen, die Cinema 4D für das Objekt zeigt
  (``CreatePhongNormals``: Phong-Winkel, Phong-Brüche, Normal-Tag), in
  Weltkoordinaten gedreht, als Normal-Tag am gebackenen Objekt — dazu ein
  Phong-Tag, ohne den der glTF-Exporter keine Normalen schreibt. Gemessen
  (``docs/measurements/2026-10-14-glb-normalen.md``): bis 0.6.4 gingen
  Normal-Tag (CAD-Importe) und Phong-Brüche verloren, und die Flächen wurden
  in der GLB rund, wo Cinema 4D sie flach zeigt.
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
  Hilfe die horizontale Breite. Gegen einen 1.4-Server dazu ``lens`` und Film
  Offset als ``shift`` (RTX-C4D-007, am Host gemessen). Was der Vertrag nicht
  darstellen kann (Parallelprojektion, echtes Stereo, sphärisch,
  Linsenverzerrung, Pixelseitenverhältnis ≠ 1, Filmformat ≠ Bildformat, Film
  Offset vor 1.4.0), führt zu ``None`` mit einem Satz, der sagt, was zu tun
  ist — der Dialog zeigt ihn **vor** dem Senden.
* **Kameras in der Datei (QC-16, RTX-C4D-010).** Die Kamera der Renderansicht
  geht immer mit in die GLB, weitere Kameras der Szene nur, wenn der Nutzer sie
  im Tab „Modell“ gewählt hat („Zusätzliche Kameras mitsenden“); bis 0.4.0
  gingen alle sichtbaren. Kenntlich ist eine Kamera an ihrem Namen und, bei
  gleichen Namen, an ihrer Reihenfolge (``camera_keys``) — die GUID von Cinema 4D
  übersteht weder ``Polygonize`` noch Speichern und Laden (gemessen 08.10.2026).
  Welche Objekte Kameras sind, steht nur in ``CAMERA_TYPES``. Der
  glTF-Exporter schreibt sie mit „Flip Z“ richtig herum.
  Objektiv und Film Offset (als Shift) trägt das Plugin danach in
  ``cameras[i].extras.rendertaxi.camera`` ein (RTX-B-004, Vertrag 11.5) —
  gerechnet mit dem ``aspectRatio``, das der Exporter geschrieben hat, und nur
  für Kameras, deren Werte am Host belegt sind (``_lens_of``).

Das Modul erfindet nichts: was nicht belegt oder nicht geprüft ist, geht nicht
mit.
"""

from __future__ import annotations

import math
import os
import shutil
import tempfile
from dataclasses import dataclass, field

import c4d

from . import capture
from .rendertaxi_client import glb
from .rendertaxi_client import glbmaterials
from .rendertaxi_client import manifest as mf
from .rendertaxi_client.log import log, log_exception

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
# Die Redshift-Kamera: in Python ein ``BaseObject``, kein ``c4d.CameraObject`` — ``Polygonize`` behält sie, mit
# Weltmatrix und lesbaren Kameraparametern (am Host gemessen, 07.10.2026, Cinema 4D 2026.3.1 mit Redshift).
REDSHIFT_CAMERA = 1057516
# Die Kameratypen der Szene — die eine Stelle dafür (RTX-C4D-010): Typ → wie der Tab „Modell“ ihn nennt. Ein weiterer
# Typ braucht hier eine Zeile und, wenn er kein ``c4d.CameraObject`` ist, seinen Weg in ``standard_camera``.
CAMERA_TYPES = {getattr(c4d, "Ocamera", 5103): "Kamera", REDSHIFT_CAMERA: "Redshift-Kamera"}
# Was von einer Redshift-Kamera in die Standardkamera des Exports geht — dieselben Parameter-IDs wie bei ``Ocamera``.
_CAMERA_PARAMETERS = ("CAMERA_PROJECTION", "CAMERA_FOCUS", "CAMERAOBJECT_APERTURE", "CAMERAOBJECT_FILM_OFFSET_X",
                      "CAMERAOBJECT_FILM_OFFSET_Y", "CAMERAOBJECT_NEAR_CLIPPING_ENABLE", "CAMERAOBJECT_NEAR_CLIPPING",
                      "CAMERAOBJECT_FAR_CLIPPING_ENABLE", "CAMERAOBJECT_FAR_CLIPPING")
# Die kleinste Nahgrenze, wenn die Kamera nicht abschneidet: der Vertrag verlangt near > 0.
NO_NEAR_CLIP = 0.000001
# Die Grenzen des Objektivs und des Shifts im Vertrag (capture-manifest.md, Abschnitt 11.3).
FOCAL_LENGTH_MM = (1.0, 1200.0)
SENSOR_MM = (1.0, 300.0)
SHIFT_LIMIT = 2.0


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


def lens_dict(focus: float, aperture: float) -> dict | None:
    """``camera.lens`` (1.4.0): Brennweite und Sensorbreite in mm, Sensorbezug horizontal.

    Cinema 4D speichert beide in mm, unabhängig von der Einheit des Dokuments, und die Sensorgröße ist
    die horizontale Breite (am Host belegt, ``docs/measurements/2026-10-05-kamera.md``). ``None``, wenn
    ein Wert außerhalb des Vertrags liegt — dann bleibt es beim Winkel.
    """
    focus, aperture = float(focus), float(aperture)
    if not (FOCAL_LENGTH_MM[0] <= focus <= FOCAL_LENGTH_MM[1] and SENSOR_MM[0] <= aperture <= SENSOR_MM[1]):
        return None
    return {"focalLengthMm": length(focus), "sensorWidthMm": length(aperture), "sensorFit": "horizontal"}


def contract_shift(offset_x: float, offset_y: float, width: int, height: int) -> tuple[float, float]:
    """Film Offset als ``shift`` des Vertrags: Anteil der **längeren** Bildseite, ``x`` rechts, ``y`` oben.

    Am Host gemessen (``docs/measurements/2026-10-05-kamera.md``): Film Offset X ist ein Anteil der
    Bildbreite und schiebt den Ausschnitt nach rechts, Film Offset Y ein Anteil der Bildhöhe und schiebt
    ihn nach **unten** — quer wie hoch. Die Spiegelung des Exports betrifft das Bild nicht.
    """
    longer = max(width, height)
    return float(offset_x) * width / longer, -float(offset_y) * height / longer


def film_offset_of(shift_x: float, shift_y: float, width: float, height: float) -> tuple[float, float]:
    """Die Umkehrung von ``contract_shift``: ``shift`` des Vertrags → Film Offset X und Y (Rückweg, RTX-C4D-012)."""
    longer = max(width, height)
    return float(shift_x) * longer / width, -float(shift_y) * longer / height


def camera_dict(*, position, direction, up, fov: tuple[str, float], near: float | None, far: float | None,
                meters: float, lens: dict | None = None, shift: tuple[float, float] | None = None) -> dict:
    """Der Kamerablock des Vertrags (Abschnitt 11.3) aus Werten im Weltraum von Cinema 4D.

    ``lens`` und ``shift`` (seit 1.4.0) stehen nur, wenn sie genannt sind; ein Shift ``(0, 0)`` entfällt.
    """
    near_m = NO_NEAR_CLIP if near is None else max(length(near * meters), NO_NEAR_CLIP)
    far_m = None if far is None else length(far * meters)
    if far_m is not None and far_m <= near_m:
        far_m = None
    block = {
        "space": "export",
        "projection": "perspective",
        "position": to_export_point(position, meters),
        "direction": to_export_direction(direction),
        "up": to_export_direction(up),
        "fieldOfView": {"axis": fov[0], "angle": angle(fov[1])},
        "clip": {"near": near_m, "far": far_m},
    }
    if lens is not None:
        block["lens"] = lens
    if shift is not None and (_num(shift[0], 6) != 0 or _num(shift[1], 6) != 0):
        block["shift"] = {"x": _num(shift[0], 6), "y": _num(shift[1], 6)}
    return block


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


MIRRORED, UNMIRRORED, DISTORTED = "mirrored", "unmirrored", "distorted"


def hull_fit(scene_low, scene_high, glb_low, glb_high) -> tuple[str, float]:
    """Wie die Hülle einer GLB zur Hülle der Szene in Cinema 4D passt — ``(Befund, Faktor)``.

    Der Faktor ist GLB-Einheiten je Einheit des Dokuments (Verhältnis der Diagonalen). ``MIRRORED``: an Z
    gespiegelt bis auf diesen Faktor, wie Flip Z es verlangt; ``UNMIRRORED``: dasselbe ohne Spiegelung;
    ``DISTORTED``: verschoben, verzerrt oder ohne Ausdehnung (Faktor 0). Die eine Regel für Export (RTX-C4D-003)
    und Import (RTX-C4D-012); was daraus folgt, sagt jeder Weg mit seinem Satz.
    """
    scene_diagonal = math.dist(scene_low, scene_high)
    glb_diagonal = math.dist(glb_low, glb_high)
    if scene_diagonal == 0 or glb_diagonal == 0:
        return DISTORTED, 0.0
    factor = glb_diagonal / scene_diagonal
    tolerance = BOUNDS_TOLERANCE * glb_diagonal
    if _fits(*mirrored_bounds(scene_low, scene_high), factor, glb_low, glb_high, tolerance):
        return MIRRORED, factor
    if _fits(scene_low, scene_high, factor, glb_low, glb_high, tolerance):
        return UNMIRRORED, factor
    return DISTORTED, factor


def exporter_scale(scene_low, scene_high, glb_low, glb_high) -> float:
    """Der Maßstab des Exporters (GLB-Einheiten je Einheit des Dokuments) — gemessen an der Hülle.

    Erwartet wird die Hülle der Szene, an Z gespiegelt, bis auf einen
    gleichförmigen Faktor. Alles andere ist ein ``ExportError``: eine
    fehlende Spiegelung nennt QC-01, jede andere Abweichung QC-01 und QC-02.
    """
    verdict, factor = hull_fit(scene_low, scene_high, glb_low, glb_high)
    if verdict == MIRRORED:
        return factor
    if factor == 0:
        raise ExportError("Das Modell hat keine Ausdehnung.")
    if verdict == UNMIRRORED:
        raise ExportError("Der glTF-Export liefert das Modell seitenverkehrt („Flip Z“ fehlt). "
                          "Das Bild lässt sich ohne Modell übernehmen.")
    raise ExportError("Der glTF-Export hat das Modell verschoben oder verzerrt. "
                      "Das Bild lässt sich ohne Modell übernehmen.")


# --------------------------------------------------------------------------
# Einheit
# --------------------------------------------------------------------------


def meters_per_unit(doc) -> float:
    """So viele Meter ist eine Einheit des Dokuments lang — aus dem Projektmaßstab, oder ``ExportError``."""
    data = doc[c4d.DOCUMENT_DOCUNIT]
    try:
        scale, unit = data.GetUnitScale()
    except (AttributeError, TypeError, ValueError):
        raise ExportError("Der Projektmaßstab des Dokuments ist nicht lesbar. In "
                          "Projekteinstellungen › Projektmaßstab eine Längeneinheit wählen.") from None
    meters = UNIT_METERS.get(unit)
    if meters is None or not (float(scale) > 0 and math.isfinite(float(scale))):
        raise ExportError("Der Projektmaßstab hat keine Längeneinheit. In "
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


def _instance_name(obj, instances: set) -> str | None:
    """Der Name der Instanz, aus der ``obj`` beim ``Polygonize`` wurde — ``None`` für ein gewöhnliches Objekt."""
    parent = obj.GetUp()
    if (parent is None or parent.GetType() != c4d.Onull or parent.GetName() not in instances
            or parent.GetDown() is None or parent.GetDown().GetNext() is not None):
        return None
    return parent.GetName()


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
    cameras: int = 0
    lenses: dict = field(default_factory=dict)  # Knotenname → (Brennweite, Sensor, Film Offset X, Y) oder None
    material_names: dict = field(default_factory=dict)  # ``MaterialSubstitutes.renamed``


def _baked(obj, mesh_index: int, materials: "MaterialSubstitutes | None" = None):
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
    normals = _world_normals(obj, matrix, polygons, flip)
    if normals is not None:
        # Die Normalen, die Cinema 4D zeigt, als Normal-Tag; der Phong-Tag sorgt nur dafür, dass der Exporter sie
        # schreibt (ohne Phong-Tag schreibt er keine), und schaltet sein eigenes Glätten ab.
        tag = mesh.MakeVariableTag(c4d.Tnormal, len(polygons))
        tag.SetAllHighlevelData(normals)
        smooth = phong.GetClone() if phong is not None else c4d.BaseTag(c4d.Tphong)
        smooth[c4d.PHONGTAG_PHONG_ANGLELIMIT] = False
        mesh.InsertTag(smooth)
    elif phong is not None:
        mesh.InsertTag(phong.GetClone())
    if materials is not None:
        materials.carry(obj, mesh, polygons, flip)
    mesh.Message(c4d.MSG_UPDATE)
    return mesh, triangles


# --------------------------------------------------------------------------
# Materialien (RTX-C4D-011)
# --------------------------------------------------------------------------

# Größte Textur in der Datei (``capture-manifest.md`` 11.6); eine größere verkleinert die Materialregel
# (``glbmaterials.apply_material_rule`` mit ``scale_image``), statt sie wegzulassen (RTX-P-021).
MAX_TEXTURE_BYTES = glbmaterials.MAX_TEXTURE_BYTES
MERGE_BY_MATERIAL = glbmaterials.MERGE_BY_MATERIAL
KEEP_OBJECTS = glbmaterials.KEEP_OBJECTS


class MaterialSubstitutes:
    """**Die Materialien der Szene als einfache Standardmaterialien im Exportdokument.**

    Je Material der Szene genau ein Standardmaterial mit der Grundfarbe (``GetAverageColor`` des Farbkanals — für
    Standard-, Corona- und Node-Materialien gleich gefragt, ohne Shader-Übersetzung) und, nur bei einem
    Standardmaterial mit Bitmap im Farbkanal und UVW-Projektion, dieser Textur — gleich wie groß: über 2 MB
    verkleinert sie die Materialregel nach dem Export (RTX-P-021). Daraus schreibt
    der glTF-Exporter ein PBR-Material (``baseColorFactor`` bzw. ``baseColorTexture``, Metall 0, Rauheit 0,5;
    gemessen 14.10.2026). Mit gehen Texturtag, Auswahl und UVW des Objekts; ohne Texturtag kein Material.
    """

    def __init__(self, target, source_document):
        self.target = target
        self.source_document = source_document
        self.by_key: dict = {}
        self.textured = 0
        # Name im Exportdokument → Name in der Szene: ``InsertMaterial`` hängt einer zweiten Fassung desselben
        # Namens „.1“ an (gemessen 14.10.2026); die Materialregel führt beide wieder unter einem Namen zusammen.
        self.renamed: dict[str, str] = {}

    def _key(self, material):
        """Wer ein Material ist — über alle Verwendungen gleich. ``BaseMaterial`` hat in Python kein ``GetGUID``
        (gemessen 14.10.2026, 2026.4); ``id()`` des Wrappers ist je Zugriff neu und gab jeder Verwendung einen
        eigenen Stellvertreter („Holz“, „Holz.1“ …). Die Unique-ID des Erzeugers bleibt beim ``Polygonize``."""
        find = getattr(material, "FindUniqueID", None)
        unique = find(c4d.MAXON_CREATOR_ID) if find is not None and hasattr(c4d, "MAXON_CREATOR_ID") else None
        if unique is not None:
            return bytes(unique)
        guid = getattr(material, "GetGUID", None)
        return guid() if guid is not None else id(material)

    def substitute(self, material, textured: bool = True):
        """Der Stellvertreter von ``material`` für **eine Verwendung**: mit Textur (``textured``, Projektion UVW)
        oder nur mit Grundfarbe. Beide sind eigene Materialien im Exportdokument — einmal zugewiesen, wird ein
        Stellvertreter nie mehr verändert (Review F-01 zu #520: dasselbe Bitmap-Material an einem UVW- und einem
        kubisch projizierten Objekt behält am UVW-Objekt seine Textur)."""
        key = (self._key(material), textured)
        if key in self.by_key:
            return self.by_key[key]
        copy = c4d.BaseMaterial(c4d.Mmaterial)
        copy.SetName(material.GetName() or "Material")
        colour = None
        try:
            colour = material.GetAverageColor(c4d.CHANNEL_COLOR)
        except Exception:  # noqa: BLE001 — ein Material ohne Farbkanal bleibt neutral grau
            colour = None
        copy[c4d.MATERIAL_COLOR_COLOR] = colour if colour is not None else c4d.Vector(0.5)
        copy[c4d.MATERIAL_USE_REFLECTION] = False
        # Transparenz eines Standardmaterials: der Exporter schreibt sie als Alpha 1 − Helligkeit mit ``alphaMode``
        # ``BLEND`` (gemessen 14.10.2026, RTX-P-021). Andere Materialien bleiben deckend.
        if material.GetType() == c4d.Mmaterial and material[c4d.MATERIAL_USE_TRANSPARENCY]:
            copy[c4d.MATERIAL_USE_TRANSPARENCY] = True
            copy[c4d.MATERIAL_TRANSPARENCY_BRIGHTNESS] = material[c4d.MATERIAL_TRANSPARENCY_BRIGHTNESS]
        texture = self._texture(material) if textured else None
        if texture is not None:
            shader = c4d.BaseShader(c4d.Xbitmap)
            shader[c4d.BITMAPSHADER_FILENAME] = texture
            copy[c4d.MATERIAL_COLOR_SHADER] = shader
            copy.InsertShader(shader)
            self.textured += 1
        self.target.InsertMaterial(copy)
        if copy.GetName() != (material.GetName() or "Material"):
            self.renamed[copy.GetName()] = material.GetName() or "Material"
        self.by_key[key] = copy
        return copy

    def _texture(self, material) -> str | None:
        """Die Bilddatei im Farbkanal eines Standardmaterials, wenn sie lesbar ist."""
        if material.GetType() != c4d.Mmaterial:
            return None
        shader = material[c4d.MATERIAL_COLOR_SHADER]
        if shader is None or shader.GetType() != c4d.Xbitmap:
            return None
        name = shader[c4d.BITMAPSHADER_FILENAME]
        if not name:
            return None
        path = name if os.path.isabs(name) else None
        if path is None:
            folder = self.source_document.GetDocumentPath() or ""
            path = c4d.GenerateTexturePath(folder, name, "") if hasattr(c4d, "GenerateTexturePath") else None
        if not path or not os.path.isfile(path):
            return None
        return path

    def carry(self, obj, mesh, polygons, flip: bool) -> None:
        """Texturtags, Polygonauswahlen und UVW von ``obj`` an ``mesh`` — die Materialien als Stellvertreter."""
        texture_tags = [tag for tag in obj.GetTags() if tag.GetType() == c4d.Ttexture]
        if not texture_tags:
            return
        for tag in obj.GetTags():
            if tag.GetType() == c4d.Tpolygonselection:
                mesh.InsertTag(tag.GetClone())
        uvw = obj.GetTag(c4d.Tuvw)
        if uvw is not None:
            copy = uvw.GetClone()
            if flip:  # dieselbe Eckreihenfolge wie _reversed
                for index, polygon in enumerate(polygons):
                    a, b, c, d = (copy.GetSlow(index)[key] for key in ("a", "b", "c", "d"))
                    if polygon.IsTriangle():
                        copy.SetSlow(index, a, c, b, b)
                    else:
                        copy.SetSlow(index, a, d, c, b)
            mesh.InsertTag(copy)
        for tag in texture_tags:
            material = tag[c4d.TEXTURETAG_MATERIAL]
            if material is None:
                continue
            copy = tag.GetClone()
            # Eine andere Projektion als UVW ergäbe in der GLB falsche Koordinaten: nur die Farbe.
            uvw_projection = copy[c4d.TEXTURETAG_PROJECTION] == c4d.TEXTURETAG_PROJECTION_UVW
            copy[c4d.TEXTURETAG_MATERIAL] = self.substitute(material, textured=uvw_projection)
            mesh.InsertTag(copy)


# Ein Normal-Tag speichert je Polygon vier Ecken × xyz als ganze Zahlen; 32000 ist die Länge eins
# (``NormalTag``, gemessen mit ``GetAllHighlevelData`` am Host: 12 Werte je Polygon).
_NORMAL_SCALE = 32000


def _world_normals(obj, matrix, polygons, flip: bool) -> list | None:
    """Die Normalen, die Cinema 4D für ``obj`` zeigt, in Weltkoordinaten — als Daten eines Normal-Tags.

    ``None`` ohne Phong- und Normal-Tag (Cinema 4D zeigt dann flach; der Exporter schreibt dann keine Normalen, und
    der Loader schattiert ebenso flach) oder wenn dieses Cinema 4D die Normalen nicht liefert. Gedreht wird mit der
    Kofaktormatrix (Normalen unter ungleichmäßiger Skalierung), das Vorzeichen der Determinante hält sie außen; die
    Ecken einer gespiegelten Fläche stehen in der Reihenfolge von ``_reversed``.
    """
    create = getattr(obj, "CreatePhongNormals", None)
    if create is None or getattr(c4d, "Tnormal", None) is None or not polygons:
        return None
    normals = create()
    if not normals or len(normals) < 4 * len(polygons):
        return None
    v1, v2, v3 = matrix.v1, matrix.v2, matrix.v3
    sign = -1.0 if flip else 1.0
    c1, c2, c3 = _cross(v2, v3), _cross(v3, v1), _cross(v1, v2)
    data: list = []
    for index, polygon in enumerate(polygons):
        corners = []
        for corner in range(4):
            n = normals[index * 4 + corner]
            x = sign * (c1[0] * n.x + c2[0] * n.y + c3[0] * n.z)
            y = sign * (c1[1] * n.x + c2[1] * n.y + c3[1] * n.z)
            z = sign * (c1[2] * n.x + c2[2] * n.y + c3[2] * n.z)
            length = math.sqrt(x * x + y * y + z * z)
            if not length > 1e-12:
                return None
            corners.append((x / length, y / length, z / length))
        if flip:  # dieselbe Reihenfolge wie _reversed: (a, c, b) bzw. (a, d, c, b)
            corners = [corners[0], corners[2], corners[1], corners[1]] if polygon.IsTriangle() else \
                [corners[0], corners[3], corners[2], corners[1]]
        for x, y, z in corners:
            data += [int(round(x * _NORMAL_SCALE)), int(round(y * _NORMAL_SCALE)), int(round(z * _NORMAL_SCALE))]
    return data


def _cross(a, b) -> tuple[float, float, float]:
    return (a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x)


def is_camera(obj) -> bool:
    """Eine Kamera der Szene: ein ``c4d.CameraObject`` oder ein Typ aus ``CAMERA_TYPES``."""
    return isinstance(obj, c4d.CameraObject) or (obj is not None and obj.GetType() in CAMERA_TYPES)


def camera_keys(names: list[str]) -> list[tuple[str, int]]:
    """Die Schlüssel der Kameras — **die eine Stelle** dafür; Liste, Renderkamera und Export lesen sie hier.

    ``names`` in der Reihenfolge des Objekt-Managers; je Kamera ``(Schlüssel, Nummer)``. Die erste Kamera eines Namens
    heißt wie er; jede weitere gleichen Namens ``Name#n`` mit dem kleinsten ``n ≥ 2``, das **kein** Name einer Kamera
    und kein schon vergebener Schlüssel ist — so bleiben auch Namen, die selbst wie ``A#2`` aussehen, eindeutig
    (Review F-02, #310: ``A``, ``A``, ``A#2`` → ``A``, ``A#3``, ``A#2``). ``Nummer`` zählt die gleichen Namen (für die
    Beschriftung „A (2)“).
    """
    taken = set(names)
    seen: dict = {}
    keys = []
    for name in names:
        seen[name] = seen.get(name, 0) + 1
        number = seen[name]
        if number == 1:
            keys.append((name, number))
            continue
        suffix = 2
        while f"{name}#{suffix}" in taken:
            suffix += 1
        key = f"{name}#{suffix}"
        taken.add(key)
        keys.append((key, number))
    return keys


def _keyed_cameras(first) -> list:
    """``(Schlüssel, Nummer, Kamera)`` für jede Kamera unter ``first``, in der Reihenfolge des Objekt-Managers."""
    cameras = [obj for obj in _walk(first) if is_camera(obj)]
    keys = camera_keys([camera.GetName() or "" for camera in cameras])
    return [(key, number, obj) for (key, number), obj in zip(keys, cameras)]


def scene_cameras(doc) -> list[dict]:
    """Die Kameras der Szene für den Tab „Modell“: ``key``, ``label``, ``kind`` und ob sie die der Renderansicht ist."""
    render = render_camera_key(doc)
    cameras = []
    for key, number, obj in _keyed_cameras(doc.GetFirstObject()):
        name = obj.GetName() or "Kamera"
        cameras.append({"key": key, "label": f"{name} ({number})" if number > 1 else name,
                        "kind": CAMERA_TYPES.get(obj.GetType(), "Kamera"), "render": key == render})
    return cameras


def render_camera_key(doc) -> str | None:
    """Der Schlüssel der Kamera der Renderansicht — ``None`` für die Editor-Kamera."""
    view = capture.render_view(doc)
    camera = view.GetSceneCamera(doc) if view is not None else None
    editor = view.GetEditorCamera() if view is not None else None
    if camera is None or (editor is not None and camera == editor):
        return None
    return next((key for key, _number, obj in _keyed_cameras(doc.GetFirstObject()) if obj == camera), None)


def standard_camera(obj):
    """``obj`` als Standardkamera: eine ``Ocamera`` bleibt, wie sie ist; aus einer Redshift-Kamera wird eine neue
    Standardkamera mit Name, Weltmatrix, Projektion, Brennweite, Sensorbreite, Film Offset und Clipping.

    Der glTF-Exporter und die Kamerafragen (Objektiv, Shift, Sichtfeld) kennen nur die Standardkamera; die Szene
    bleibt unberührt — die neue Kamera steht in keinem Dokument.
    """
    if obj is None or isinstance(obj, c4d.CameraObject) or obj.GetType() != REDSHIFT_CAMERA:
        return obj
    camera = c4d.CameraObject()
    camera.SetName(obj.GetName())
    for name in _CAMERA_PARAMETERS:
        value = _get(obj, name)
        if value is not None:
            camera[getattr(c4d, name)] = value
    camera.SetMg(obj.GetMg())
    return camera


def _camera(obj):
    """Eine Kopie der Kamera ohne Kinder und Tags (ein Ziel-Tag fände sein Ziel nicht), mit ihrer Weltmatrix.

    Eine Redshift-Kamera geht als Standardkamera mit (``standard_camera``)."""
    if not isinstance(obj, c4d.CameraObject):
        return standard_camera(obj)
    camera = obj.GetClone(c4d.COPYFLAGS_NO_HIERARCHY)
    for tag in list(camera.GetTags()):
        tag.Remove()
    camera.SetMg(obj.GetMg())
    return camera


def _lens_of(obj, doc):
    """``(Brennweite, Sensorbreite, Film Offset X, Film Offset Y)`` einer Kamera für ``extras`` — oder ``None``.

    Nur, was am Host belegt ist (``docs/measurements/2026-10-05-kamera.md``): perspektivisch, ohne echtes Stereo,
    nicht sphärisch, ohne Linsenverzerrung, Pixelseitenverhältnis 1:1. Eine Parallelkamera bleibt ohne Angabe: ihre
    Ausdehnung und ihr Film Offset sind nicht gemessen (QC-09).
    """
    if obj.GetProjection() != c4d.Pperspective or not (obj.GetFocus() > 0 and obj.GetAperture() > 0):
        return None
    if stereo(doc, obj) or _get(obj, "CAMERAOBJECT_SPC_ENABLE", False):
        return None
    if (abs(float(_get(obj, "CAMERAOBJECT_LENS_DISTORTION_QUAD", 0.0))) > TOLERANCE
            or abs(float(_get(obj, "CAMERAOBJECT_LENS_DISTORTION_CUBIC", 0.0))) > TOLERANCE):
        return None
    if abs(float(_get(doc.GetActiveRenderData(), "RDATA_PIXELASPECT", 1.0)) - 1.0) > TOLERANCE:
        return None
    offset = film_offset(obj)
    if not all(math.isfinite(value) for value in offset):
        return None
    return float(obj.GetFocus()), float(obj.GetAperture()), offset[0], offset[1]


def extras_block(lens, definition: dict) -> dict | None:
    """``extras.rendertaxi.camera`` für eine Kameradefinition der Datei — mit ihrem ``aspectRatio`` gerechnet.

    Shift wie im Kamerablock (``contract_shift``), die Bildmaße aus dem Seitenverhältnis, das der Exporter selbst
    geschrieben hat (Breite / Höhe; die längere Seite ist 1). ``lens`` nur, wenn es mit ``aspectRatio`` denselben
    vertikalen Winkel beschreibt wie ``yfov`` (Vertrag 11.5, 1e-5 rad).
    """
    if lens is None or not isinstance(definition, dict) or definition.get("type") != "perspective":
        return None
    perspective = definition.get("perspective")
    if not isinstance(perspective, dict):
        return None
    aspect, yfov = perspective.get("aspectRatio"), perspective.get("yfov")
    if not (isinstance(aspect, (int, float)) and aspect > 0 and math.isfinite(aspect)):
        return None
    focus, aperture, offset_x, offset_y = lens
    width, height = (1.0, 1.0 / aspect) if aspect >= 1.0 else (aspect, 1.0)
    shift = contract_shift(offset_x, offset_y, width, height)
    described = lens_dict(focus, aperture)
    vertical = 2.0 * math.atan(aperture / (2.0 * focus) / aspect)
    if not (isinstance(yfov, (int, float)) and abs(vertical - yfov) <= 1e-5):
        described = None
    return glb.camera_extras(described, {"x": _num(shift[0], 6), "y": _num(shift[1], 6)})


def write_camera_extras(target: str, lenses: dict) -> int:
    """Objektiv und Shift in die Kameradefinitionen der GLB-Datei (Knotenname = Kameraname) — die Zahl der beschriebenen."""
    document = glb.read_glb_json(target)
    cameras = document.get("cameras") if isinstance(document, dict) else None
    if not isinstance(cameras, list):
        return 0
    by_name = {}
    for node in document.get("nodes") or []:
        index = node.get("camera") if isinstance(node, dict) else None
        name = node.get("name") if isinstance(node, dict) else None
        if (not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(cameras)
                or not isinstance(name, str) or name not in lenses):
            continue
        block = extras_block(lenses[name], cameras[index])
        if block is not None:
            by_name[name] = block
    return glb.set_camera_extras(target, by_name) if by_name else 0


def _image_size(target, size: tuple[int, int] | None) -> None:
    """Die Bildmaße der Aufnahme im Exportdokument: der Exporter rechnet ``yfov`` und ``aspectRatio`` daraus."""
    if size is None:
        return
    rd = target.GetActiveRenderData()
    rd[c4d.RDATA_XRES], rd[c4d.RDATA_YRES] = float(size[0]), float(size[1])
    rd[c4d.RDATA_FILMASPECT] = float(size[0]) / float(size[1])
    rd[c4d.RDATA_PIXELASPECT] = 1.0


def prepare(doc, kind: str, size: tuple[int, int] | None = None, extra_cameras=(), materials: bool = True) -> Prepared:
    """Die sichtbaren Polygone und die gewählten Kameras in einem eigenen Exportdokument — das Dokument bleibt unberührt.

    Kameras (QC-16) gehen mit ihrer Weltmatrix mit; der glTF-Exporter schreibt sie mit „Flip Z“ richtig herum
    (Blick −Z und Oben +Y des Knotens gleich der gespiegelten Kamera, am Host gemessen,
    ``docs/measurements/2026-10-05-kamera.md``). Mit geht die Kamera der Renderansicht und jede, deren Schlüssel in
    ``extra_cameras`` steht — gewählt ist gewählt, auch eine im Objekt-Manager ausgeblendete. Ein Schlüssel ohne Kamera
    (gelöscht, umbenannt) zählt nicht. Kameras zählen nicht zu den Objekten der Größenschätzung. ``materials``
    ``False`` („ohne Materialien“): keine Stellvertreter, kein Texturtag.
    """
    wanted = set(extra_cameras)
    render = render_camera_key(doc)
    if render is not None:
        wanted.add(render)
    source = doc.Polygonize(False)
    if source is None:
        raise ExportError("Cinema 4D konnte die Szene nicht in Polygone wandeln.")
    target = c4d.documents.BaseDocument()
    try:
        target[c4d.DOCUMENT_DOCUNIT] = doc[c4d.DOCUMENT_DOCUNIT]
        _image_size(target, size)
        objects = triangles = cameras = 0
        lenses: dict = {}
        substitutes = MaterialSubstitutes(target, doc) if materials else None
        low = [math.inf] * 3
        high = [-math.inf] * 3
        # Dieselben Schlüssel wie in der Liste: ``Polygonize`` behält Namen und Reihenfolge der Kameras.
        keys = iter([key for key, _number, _obj in _keyed_cameras(source.GetFirstObject())])
        # „Objekte behalten“ (RTX-P-021): ``Polygonize`` macht aus einer Instanz ein Null mit ihrem Namen und hängt
        # die Kopie des Originals darunter — mit dem Namen des Originals (gemessen 14.10.2026).
        instances = {o.GetName() for o in _walk(doc.GetFirstObject()) if o.GetType() == c4d.Oinstance}
        for obj in _walk(source.GetFirstObject()):
            if is_camera(obj):
                if next(keys) not in wanted:
                    continue
                copy = _camera(obj)
                target.InsertObject(copy)
                cameras += 1
                name = obj.GetName()
                # Zwei Kameras gleichen Namens: der Knoten wäre nicht eindeutig — beide ohne extras.
                # Objektiv und Shift einer Redshift-Kamera aus ihrer Standardkamera, sonst wie bisher.
                lens_source = obj if isinstance(obj, c4d.CameraObject) else copy
                lenses[name] = None if name in lenses else _lens_of(lens_source, doc)
                continue
            if not isinstance(obj, c4d.PolygonObject) or not visible(obj, source, kind):
                continue
            mesh, count = _baked(obj, objects, substitutes)
            if mesh is None:
                continue
            instance = _instance_name(obj, instances)
            if instance:
                mesh.SetName(instance)
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
        return Prepared(target, 0, 0, None, None, cameras, lenses)
    renamed = dict(substitutes.renamed) if substitutes is not None else {}
    return Prepared(target, objects, triangles, low, high, cameras, lenses, renamed)


@dataclass
class Estimate:
    objects: int
    triangles: int
    # Größe der GLB-Datei in Bytes, gemessen an einem Probeexport (#289 Teil 2); ``None``, wenn nicht exportiert
    # wurde (leer, über der Dreiecksgrenze oder der Export schlug fehl).
    byte_size: int | None = None
    # Warum keine Größe da ist — der Tab sagt es, statt still nichts zu zeigen (Abnahme 0.6.3, F-03).
    size_problem: str | None = None


def estimate(doc, kind: str, materials: bool = True, merge: str = MERGE_BY_MATERIAL) -> Estimate:
    """Sichtbare Objekte, Dreiecke und die Dateigröße — der Hinweis im Dialog, **bevor** übernommen wird.

    Die Größe kommt aus einem Export derselben Objekte mit demselben Exporter in ein temporäres Verzeichnis, das danach
    gelöscht wird (Nutzerwunsch vom 10.10.2026: „nach Neu zählen Dateigröße anzeigen“). Ohne Kameras; die fehlen in
    der Größe (je Kamera einige hundert Bytes). Mit der Materialregel wie beim Senden: verkleinerte Texturen zählen
    mit ihrer neuen Größe.
    """
    prepared = prepare(doc, kind, materials=materials)
    byte_size = None
    problem = None
    folder = None
    try:
        if prepared.triangles == 0:
            problem = "keine sichtbare Geometrie"
        elif prepared.triangles > MAX_TRIANGLES:
            problem = "zu viele Dreiecke für einen Export"
        else:
            folder = tempfile.mkdtemp(prefix="rendertaxi-zaehlen-")
            target = os.path.join(folder, "scene.glb")
            try:
                _save(prepared.document, target)
                glbmaterials.apply_material_rule(target, materials=materials, merge=merge, scale_image=scale_image,
                                                 material_names=prepared.material_names)
                byte_size = os.path.getsize(target)
            except Exception as error:  # noqa: BLE001 — die Zählung gilt trotzdem, nur ohne Größe, mit Grund
                log_exception("Dateigröße des Modells nicht messbar", error)
                problem = "Probeexport fehlgeschlagen" if isinstance(error, (ExportError, OSError)) \
                    else f"Probeexport fehlgeschlagen ({type(error).__name__})"
    finally:
        c4d.documents.KillDocument(prepared.document)
        if folder is not None:
            shutil.rmtree(folder, ignore_errors=True)
    log(f"Modell gezählt: {prepared.objects} Objekte, {prepared.triangles} Dreiecke, "
        f"{'—' if byte_size is None else byte_size} Bytes{f' ({problem})' if problem else ''}")
    return Estimate(prepared.objects, prepared.triangles, byte_size, problem)


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
    ("GLTFEXPORTER_TEXTURES", True),  # die Bitmaps der Stellvertreter (RTX-C4D-011); über 2 MB verkleinert (RTX-P-021)
    ("GLTFEXPORTER_CAMERAS", True),
    ("GLTFEXPORTER_INSTANCES", False),
    ("GLTFEXPORTER_NORMALS", True),
    ("GLTFEXPORTER_UVS", True),  # für die Texturen; UVW trägt nur ein Objekt mit Texturtag (MaterialSubstitutes)
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


def export_model(doc, kind: str, directory: str, size: tuple[int, int] | None = None, extra_cameras=(),
                 materials: bool = True, merge: str = MERGE_BY_MATERIAL) -> Model:
    """Die sichtbaren Objekte, die Kamera der Renderansicht und die gewählten Kameras als GLB nach
    ``directory/model/scene.glb`` — geprüft, in Metern.

    ``size``: die Maße des aufgenommenen Bildes (Seitenverhältnis der Kameras in der Datei). ``extra_cameras``: die
    Schlüssel weiterer Kameras (``scene_cameras``). ``materials`` und ``merge``: die Materialregel
    (``capture-manifest.md`` 11.6) — „ohne Materialien“ und „nach Material“ oder „Objekte behalten“; sie läuft auf
    der exportierten Datei, nach Maßstab und Kameras. Die Datei bleibt im Capture-Verzeichnis (0700); Meldungen
    tragen keinen Pfad.
    """
    meters = meters_per_unit(doc)
    prepared = prepare(doc, kind, size, extra_cameras, materials)
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
    try:
        described = write_camera_extras(target, prepared.lenses) if prepared.cameras else 0
    except Exception:  # noqa: BLE001 — die Datei ist nicht vertrauenswürdig: ohne extras lädt sie wie bisher
        described = 0  # (Shift 0, Objektiv aus yfov); die Aufnahme bricht nie daran ab
    try:
        report = glbmaterials.apply_material_rule(target, materials=materials, merge=merge, scale_image=scale_image,
                                                  material_names=prepared.material_names)
    except glb.GlbError as error:
        raise ExportError(str(error)) from None
    sha, size = mf.sha256_file(target)
    scaled = (f"Maßstab des Exporters {factor:g} je Einheit gemessen, auf Meter gesetzt ({correction:g})"
              if abs(correction - 1.0) > SCALE_TOLERANCE else "Maßstab des Exporters in Metern gemessen")
    cameras = {0: "ohne Kameras", 1: "1 Kamera"}.get(prepared.cameras, f"{prepared.cameras} Kameras")
    if prepared.cameras:
        cameras += f", davon {described} mit Objektiv und Shift (extras)"
    note = (f"glTF 2.0 binär aus dem glTF-Exporter von Cinema 4D (Flip Z): sichtbare Objekte polygonisiert, "
            f"Weltkoordinaten, Normalen wie in Cinema 4D, ohne Animation; {glbmaterials.report_note(report, materials)}; "
            f"{triangles} Dreiecke; {cameras}; {scaled}.")
    file = mf.CaptureFile(role="model", path=MODEL_PATH, media_type=mf.MODEL_MEDIA_TYPE, image=None,
                          note=note, byte_size=size, sha256=sha)
    return Model(file, geometry_dict(meters), meters, triangles, factor)


def scale_image(data: bytes, mime: str, factor: float):
    """Ein Bild um ``factor`` je Seite verkleinert neu kodieren — mit ``BaseBitmap`` von Cinema 4D.

    Ein PNG mit Alphakanal bleibt PNG (der Kanal wird mitskaliert), alles andere wird JPEG (Qualität 90): ein
    Foto als PNG wäre bei gleicher Pixelzahl drei- bis fünfmal größer.
    """
    folder = tempfile.mkdtemp(prefix="rendertaxi-bild-")
    try:
        source = os.path.join(folder, "quelle.png" if mime == "image/png" else "quelle.jpg")
        with open(source, "wb") as handle:
            handle.write(data)
        bitmap = c4d.bitmaps.BaseBitmap()
        result = bitmap.InitWith(source)
        if (result[0] if isinstance(result, tuple) else result) != c4d.IMAGERESULT_OK:
            return None
        width, height = bitmap.GetBw(), bitmap.GetBh()
        if not width or not height:
            return None
        new_width, new_height = max(1, int(width * factor)), max(1, int(height * factor))
        alpha = bitmap.GetInternalChannel() if mime == "image/png" else None
        scaled = c4d.bitmaps.BaseBitmap()
        if scaled.Init(new_width, new_height, 24) != c4d.IMAGERESULT_OK:
            return None
        bitmap.ScaleBicubic(scaled, 0, 0, width - 1, height - 1, 0, 0, new_width - 1, new_height - 1)
        if alpha is not None:
            channel = scaled.AddChannel(True, False)
            for y in range(new_height):
                for x in range(new_width):
                    value = bitmap.GetAlphaPixel(alpha, min(width - 1, int(x / factor)), min(height - 1, int(y / factor)))
                    scaled.SetAlphaPixel(channel, x, y, value)
        target = os.path.join(folder, "klein.png" if alpha is not None else "klein.jpg")
        if alpha is not None:
            saved = scaled.Save(target, c4d.FILTER_PNG, c4d.BaseContainer(), c4d.SAVEBIT_ALPHA)
        else:
            settings = c4d.BaseContainer()
            settings[c4d.JPGSAVER_QUALITY] = 90
            saved = scaled.Save(target, c4d.FILTER_JPG, settings, c4d.SAVEBIT_NONE)
        if saved != c4d.IMAGERESULT_OK or not os.path.isfile(target):
            return None
        with open(target, "rb") as handle:
            return handle.read(), "image/png" if alpha is not None else "image/jpeg"
    finally:
        shutil.rmtree(folder, ignore_errors=True)


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
    # Eine Redshift-Kamera als Standardkamera — Kamerablock und Kamerafragen kennen nur diese.
    return standard_camera(view.GetSceneCamera(doc)) if view is not None else None


def stereo(doc, camera) -> bool:
    """Ob das Bild stereoskopisch wird: Stereomodus der Kamera nicht Mono **und** Stereoskopie in der Voreinstellung.

    Am Host gemessen (``docs/measurements/2026-10-05-kamera.md``): die Editor-Kamera steht auf „Symmetrisch“, eine
    neue Kamera auf „Mono“; das Bild ändert sich aber nur, wenn ``RDATA_STEREO`` eingeschaltet ist — sonst ist es
    bytegleich mit Mono, gleich welcher Modus.
    """
    mode = _get(camera, "CAMERAOBJECT_STEREO_MODE", None)
    mono = getattr(c4d, "CAMERAOBJECT_STEREO_MODE_MONO", 0)
    if mode is None or mode == mono:
        return False
    return bool(_get(doc.GetActiveRenderData(), "RDATA_STEREO", False))


def film_offset(camera) -> tuple[float, float]:
    """Film Offset X/Y der Kamera (Anteil der Bildbreite bzw. -höhe)."""
    return (float(_get(camera, "CAMERAOBJECT_FILM_OFFSET_X", 0.0)),
            float(_get(camera, "CAMERAOBJECT_FILM_OFFSET_Y", 0.0)))


def camera_problem(doc, size: tuple[int, int], lens_and_shift: bool = False) -> str | None:
    """Warum **keine** Kamera mitgeht — für den Dialog, bevor gesendet wird; jeder Satz sagt, was zu tun ist.

    ``lens_and_shift``: der Server setzt Manifest 1.4.0 um (``mf.camera_lens_allowed``) — dann geht Film Offset
    als ``shift`` mit.
    """
    camera = render_camera(doc)
    if camera is None or not isinstance(camera, c4d.CameraObject):
        return "Für die Kamera in der Renderansicht eine Kamera aktivieren."
    if camera.GetProjection() != c4d.Pperspective:
        return "Parallelprojektion wird nicht übertragen; dafür eine perspektivische Kamera verwenden."
    if not (camera.GetFocus() > 0 and camera.GetAperture() > 0):
        return "Brennweite und Sensorgröße der Kamera müssen größer als 0 sein."
    if stereo(doc, camera):
        return ("Stereoskopie ist eingeschaltet; für die Kamera in den Rendervoreinstellungen Stereoskopie "
                "ausschalten oder den Stereomodus der Kamera auf Mono stellen.")
    if _get(camera, "CAMERAOBJECT_SPC_ENABLE", False):
        return "Sphärische Kameras werden nicht übertragen; dafür eine perspektivische Kamera verwenden."
    if (abs(float(_get(camera, "CAMERAOBJECT_LENS_DISTORTION_QUAD", 0.0))) > TOLERANCE
            or abs(float(_get(camera, "CAMERAOBJECT_LENS_DISTORTION_CUBIC", 0.0))) > TOLERANCE):
        return "Für die Kamera die Linsenverzerrung auf 0 stellen."
    rd = doc.GetActiveRenderData()
    pixel = float(_get(rd, "RDATA_PIXELASPECT", 1.0))
    if abs(pixel - 1.0) > TOLERANCE:
        return "Für die Kamera das Pixelseitenverhältnis in den Rendervoreinstellungen auf 1:1 stellen."
    film = _get(rd, "RDATA_FILMASPECT", None)
    width, height = size
    if film is not None and abs(float(film) - width / height) > 1e-3:
        return f"Für die Kamera das Filmformat in den Rendervoreinstellungen an das Bild ({width} × {height}) anpassen."
    offset = film_offset(camera)
    if any(abs(value) > TOLERANCE for value in offset):
        if not lens_and_shift:
            return "Film Offset übernimmt der Server noch nicht; ohne Film Offset geht die Kamera mit."
        if any(abs(value) > SHIFT_LIMIT for value in contract_shift(*offset, width, height)):
            return "Für die Kamera den Film Offset auf höchstens 200 % stellen."
    axis, value = perspective_fov(camera.GetAperture(), camera.GetFocus())
    reported = _get(camera, "CAMERAOBJECT_FOV", None)
    if isinstance(reported, (int, float)) and not (
            abs(float(reported) - value) <= TOLERANCE or abs(float(reported) - math.degrees(value)) <= 1e-4):
        return "Das Sichtfeld der Kamera passt nicht zu Brennweite und Sensorgröße; beide prüfen."
    return None


def camera_block(doc, size: tuple[int, int], meters: float, lens_and_shift: bool = False) -> dict | None:
    """Die Kamera der Aufnahme im Exportraum — ``None``, wenn ``camera_problem`` einen Grund nennt.

    ``size`` sind die Maße des aufgenommenen Bildes (nicht der Voreinstellung:
    „Blickpunkt-Rahmen" ändert sie). Mit ``lens_and_shift`` (Manifest 1.4.0) auch Objektiv und Shift.
    """
    if camera_problem(doc, size, lens_and_shift):
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
    extra = {}
    if lens_and_shift:
        extra = {"lens": lens_dict(camera.GetFocus(), camera.GetAperture()),
                 "shift": contract_shift(*film_offset(camera), *size)}
    return camera_dict(position=position, direction=direction, up=up,
                       fov=perspective_fov(camera.GetAperture(), camera.GetFocus()),
                       near=near, far=far, meters=meters, **extra)
