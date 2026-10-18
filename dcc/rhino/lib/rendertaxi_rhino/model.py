"""Modellweg ohne ``Rhino`` — Einheit, Achsen, Maßstab an der Datei, ``geometry``-Block; hostfrei prüfbar.

Am Host gemessen (Rhino 8.35, 08.10.2026, ``docs/measurements/2026-10-08-modellweg.md``):

* ``FileGltf.Write`` mit ``MapZToY`` bildet einen Punkt ``(x, y, z)`` auf ``(x, z, −y)`` ab — Drehung um −90° um
  X, rechtshändig wie Rhino; der Ursprung bleibt.
* Der Exporter schreibt **Meter**, gleich ob das Dokument in mm, cm oder m ist. Das Plugin glaubt das nicht,
  sondern misst es an jeder Datei (``exporter_scale``): Hülle der GLB gegen die Hülle der vorher vernetzten
  Szene; ein gleichförmiger Faktor ist der Maßstab des Exporters, alles andere bricht ab.
* ``RhinoMath.UnitScale`` nennt für „Keine Einheit" 1 — wie Meter. Ein Dokument ohne Längeneinheit sendet
  deshalb kein Modell (wie Blender ohne Einheitensystem), ebenso eigene Einheiten.

**Georeferenz aus dem Erdanker** (RTX-RH-005, ``anchor_georeference``): Rhino führt je Dokument einen
``EarthAnchorPoint`` — Breite, Länge (WGS84) und Höhe des Punkts ``ModelBasePoint``, dazu ``ModelNorth`` und
``ModelEast``, die Himmelsrichtungen in Modellkoordinaten. Daraus wird ``geometry.georeference``
(``rendertaxi_client.georeference``: UTM-Zone des Ortes, Dateiursprung, Nordwinkel gegen Gitter-Nord). Nur ein
gesetzter Anker mit **Höhe über dem Meeresspiegel** (``MeanSeaLevel``, seit 8.5 auch ``EGM2008``) gibt einen
Block; ohne Anker, mit Höhe über Gelände oder Erdmittelpunkt, mit Nord senkrecht oder gespiegelten Richtungen
bleibt das Feld weg, und der Grund steht in der Notiz der Modelldatei (kein Erfinden).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .rendertaxi_client import georeference as geo
from .rendertaxi_client import glb
from .rendertaxi_client import manifest as mf

MODEL_PATH = glb.MODEL_PATH
# Toleranzen: Hüllenvergleich relativ zur Diagonale (die GLB speichert float32), Maßstab absolut.
BOUNDS_TOLERANCE = 1e-4
SCALE_TOLERANCE = 1e-6
# ``sourceUnitScaleToMeter`` hat sechs Nachkommastellen (Präzisionsklasse ``ratio``): kleinere Einheiten als
# Mikrometer wären 0.
SMALLEST_UNIT_METERS = 1e-6
# Einheiten, für die Rhino keine Länge kennt (``UnitSystem``-Namen).
NO_LENGTH_UNITS = ("None", "CustomUnits", "Unset")
NO_UNIT = ("Das Dokument hat keine Längeneinheit. Unter Dokumenteigenschaften › Einheiten eine Einheit wie "
           "Millimeter oder Meter wählen; das Bild lässt sich ohne Modell übernehmen.")
TOO_SMALL = "Die Einheit des Dokuments ist kleiner als ein Mikrometer; das Bild lässt sich ohne Modell übernehmen."


class ExportError(RuntimeError):
    """Ein Grund, warum das Modell nicht gesendet wird — lesbar, ohne Pfad."""


def _num(value: float, digits: int):
    rounded = round(float(value), digits)
    if rounded == 0:
        return 0
    return int(rounded) if rounded.is_integer() else rounded


def unit_problem(unit_name: str, meters: float) -> str | None:
    """Warum die Einheit des Dokuments kein Modell trägt — ``None``, wenn sie es tut."""
    if unit_name in NO_LENGTH_UNITS or not (meters > 0 and math.isfinite(meters)):
        return NO_UNIT
    if meters < SMALLEST_UNIT_METERS:
        return TOO_SMALL
    return None


def to_export_bounds(low, high) -> tuple[list, list]:
    """Die Hülle der Szene (Rhino-Achsen) im Exportraum: ``(x, y, z) → (x, z, −y)`` — −y tauscht Anfang und Ende."""
    return [low[0], low[2], -high[1]], [high[0], high[2], -low[1]]


def _fits(expected_low, expected_high, factor, glb_low, glb_high, tolerance) -> bool:
    return all(abs(glb_low[a] - factor * expected_low[a]) <= tolerance
               and abs(glb_high[a] - factor * expected_high[a]) <= tolerance for a in range(3))


def exporter_scale(scene_low, scene_high, glb_low, glb_high) -> float:
    """Der Maßstab des Exporters (GLB-Einheiten je Einheit des Dokuments) — gemessen an der Hülle.

    Erwartet wird die Hülle der Szene, gedreht wie ``MapZToY``, bis auf einen gleichförmigen Faktor. Ohne
    Drehung (Z oben in der Datei) oder verschoben oder verzerrt: ``ExportError``.
    """
    scene_diagonal = math.dist(scene_low, scene_high)
    glb_diagonal = math.dist(glb_low, glb_high)
    if scene_diagonal == 0 or glb_diagonal == 0:
        raise ExportError("Das Modell hat keine Ausdehnung.")
    factor = glb_diagonal / scene_diagonal
    tolerance = BOUNDS_TOLERANCE * glb_diagonal
    if _fits(*to_export_bounds(scene_low, scene_high), factor, glb_low, glb_high, tolerance):
        return factor
    if _fits(scene_low, scene_high, factor, glb_low, glb_high, tolerance):
        raise ExportError("Der glTF-Export liefert das Modell mit Z nach oben statt Y. "
                          "Das Bild lässt sich ohne Modell übernehmen.")
    raise ExportError("Der glTF-Export hat das Modell verschoben oder verzerrt. "
                      "Das Bild lässt sich ohne Modell übernehmen.")


def geometry_dict(meters: float, georeference: dict | None = None) -> dict:
    """``geometry`` für die GLB-Datei: Rhinos Weltraum (rechtshändig, Z oben), unverschoben exportiert; mit dem
    Block ``georeference`` aus ``anchor_georeference``, wenn der Erdanker einen ergibt."""
    geometry = {
        "assetPath": MODEL_PATH,
        "units": {"sourceUnitScaleToMeter": _num(meters, 6)},
        "axes": {"handedness": "right", "upAxis": "z"},
        "origin": [0, 0, 0],
    }
    if georeference is not None:
        geometry["georeference"] = georeference
    return geometry


# Höhenbezüge des Erdankers, die eine absolute Höhe ergeben (``EarthCoordinateSystem`` seit 8.5,
# ``BasepointZero`` davor): über dem mittleren Meeresspiegel bzw. dem Geoid EGM2008.
SEA_LEVEL_ZEROS = ("MeanSeaLevel", "EGM2008")
NO_ANCHOR = "ohne Georeferenz: Erdanker (EarthAnchorPoint) nicht gesetzt"
NO_SEA_LEVEL = ("ohne Georeferenz: die Höhe des Erdankers bezieht sich nicht auf den Meeresspiegel "
                "(EarthAnchorPoint › Höhe über dem Meeresspiegel angeben)")
NO_NORTH = "ohne Georeferenz: die Nordrichtung des Erdankers liegt nicht in der Grundrissebene"
MIRRORED = "ohne Georeferenz: Nord- und Ostrichtung des Erdankers sind gespiegelt"
NO_UTM = "ohne Georeferenz: der Ort des Erdankers liegt außerhalb von UTM (80° S … 84° N)"


@dataclass(frozen=True)
class EarthAnchor:
    """Was ``export`` aus ``RhinoDoc.EarthAnchorPoint`` liest — Grad, Meter, Modellkoordinaten in Dokumenteinheit."""

    is_set: bool
    latitude: float
    longitude: float
    elevation: float
    elevation_zero: str
    base_point: tuple[float, float, float]
    north: tuple[float, float, float]
    east: tuple[float, float, float]


def anchor_georeference(anchor: EarthAnchor | None, meters: float) -> tuple[dict | None, str]:
    """``(geometry.georeference, Notiz)`` aus dem Erdanker — ohne Block mit dem Grund als Notiz.

    Der Winkel von geografisch Nord zur Planachse „Nord“ (+Y) ist ``−δ`` mit δ dem Winkel von ``ModelNorth``
    gegen +Y im Uhrzeigersinn (``atan2(x, y)``); der Ankerpunkt geht in Metern ein.
    """
    if anchor is None or not anchor.is_set:
        return None, NO_ANCHOR
    if anchor.elevation_zero not in SEA_LEVEL_ZEROS:
        return None, NO_SEA_LEVEL
    nx, ny, _nz = anchor.north
    ex, ey, _ez = anchor.east
    plan = math.hypot(nx, ny)
    if not plan > 1e-9 or not math.isfinite(plan):
        return None, NO_NORTH
    if ex * ny - ey * nx <= 0:  # Ost × Nord zeigt nach unten: gespiegelt
        return None, MIRRORED
    delta = math.degrees(math.atan2(nx, ny))
    point = tuple(c * meters for c in anchor.base_point)
    found = geo.file_georeference(anchor.latitude, anchor.longitude, anchor.elevation, point, -delta)
    if found is None:
        return None, NO_UTM
    block = geo.manifest_block(found)
    return block, f"Georeferenz aus dem Erdanker: {geo.block_text(block)}"


def triangles_text(count: int) -> str:
    return f"{count:,}".replace(",", " ")


def too_many(triangles: int, limit: int) -> str | None:
    if triangles > limit:
        return (f"Das Modell hat {triangles_text(triangles)} Dreiecke; der Server nimmt höchstens "
                f"{triangles_text(limit)} an.")
    return None


def too_big(size: int, limits: dict | None) -> str | None:
    """Die Modelldatei gegen ``limits.maxGeometryBytes`` des Handshakes — vor dem Senden."""
    cap = (limits or {}).get("maxGeometryBytes")
    if isinstance(cap, int) and not isinstance(cap, bool) and cap > 0 and size > cap:
        return (f"Die Modelldatei ist {size / 1048576:.1f} MB groß; der Server nimmt höchstens "
                f"{cap / 1048576:.0f} MB an. Weniger sichtbar schalten oder gröber vernetzen.")
    return None


def note(triangles: int, cameras: int, factor: float, correction: float, materials: str,
         georeference: str = "") -> str:
    """Die Zeile ``note`` des Modell-Assets: was in der Datei steht und was gemessen wurde."""
    scaled = (f"Maßstab des Exporters {factor:g} je Einheit gemessen, auf Meter gesetzt ({correction:g})"
              if abs(correction - 1.0) > SCALE_TOLERANCE else "Maßstab des Exporters in Metern gemessen")
    named = {0: "ohne benannte Ansichten", 1: "1 benannte Ansicht als Kamera"}.get(
        cameras, f"{cameras} benannte Ansichten als Kameras")
    return (f"glTF 2.0 binär aus dem glTF-Exporter von Rhino (Z nach Y): sichtbare Objekte als Render-Netze, "
            f"Blöcke aufgelöst, Weltkoordinaten; {materials}; "
            f"{triangles} Dreiecke; {named}; {scaled}" + (f"; {georeference}." if georeference else "."))


def model_file(path: str, triangles: int, cameras: int, factor: float, correction: float,
               materials: str, georeference: str = "") -> mf.CaptureFile:
    sha, size = mf.sha256_file(path)
    return mf.CaptureFile(role=mf.MODEL_ROLE, path=MODEL_PATH, media_type=mf.MODEL_MEDIA_TYPE, image=None,
                          note=note(triangles, cameras, factor, correction, materials, georeference), byte_size=size,
                          sha256=sha)
