"""Modellweg ohne ``Rhino`` — Einheit, Achsen, Maßstab an der Datei, ``geometry``-Block; hostfrei prüfbar.

Am Host gemessen (Rhino 8.35, 08.10.2026, ``docs/measurements/2026-10-08-modellweg.md``):

* ``FileGltf.Write`` mit ``MapZToY`` bildet einen Punkt ``(x, y, z)`` auf ``(x, z, −y)`` ab — Drehung um −90° um
  X, rechtshändig wie Rhino; der Ursprung bleibt.
* Der Exporter schreibt **Meter**, gleich ob das Dokument in mm, cm oder m ist. Das Plugin glaubt das nicht,
  sondern misst es an jeder Datei (``exporter_scale``): Hülle der GLB gegen die Hülle der vorher vernetzten
  Szene; ein gleichförmiger Faktor ist der Maßstab des Exporters, alles andere bricht ab.
* ``RhinoMath.UnitScale`` nennt für „Keine Einheit" 1 — wie Meter. Ein Dokument ohne Längeneinheit sendet
  deshalb kein Modell (wie Blender ohne Einheitensystem), ebenso eigene Einheiten.
"""

from __future__ import annotations

import math

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


def geometry_dict(meters: float) -> dict:
    """``geometry`` für die GLB-Datei: Rhinos Weltraum (rechtshändig, Z oben), unverschoben exportiert."""
    return {
        "assetPath": MODEL_PATH,
        "units": {"sourceUnitScaleToMeter": _num(meters, 6)},
        "axes": {"handedness": "right", "upAxis": "z"},
        "origin": [0, 0, 0],
    }


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


def note(triangles: int, cameras: int, factor: float, correction: float, colors: bool) -> str:
    """Die Zeile ``note`` des Modell-Assets: was in der Datei steht und was gemessen wurde."""
    scaled = (f"Maßstab des Exporters {factor:g} je Einheit gemessen, auf Meter gesetzt ({correction:g})"
              if abs(correction - 1.0) > SCALE_TOLERANCE else "Maßstab des Exporters in Metern gemessen")
    named = {0: "ohne benannte Ansichten", 1: "1 benannte Ansicht als Kamera"}.get(
        cameras, f"{cameras} benannte Ansichten als Kameras")
    return (f"glTF 2.0 binär aus dem glTF-Exporter von Rhino (Z nach Y): sichtbare Objekte als Render-Netze, "
            f"Blöcke aufgelöst, Weltkoordinaten, {'mit Materialfarben' if colors else 'ohne Materialien'}; "
            f"{triangles} Dreiecke; {named}; {scaled}.")


def model_file(path: str, triangles: int, cameras: int, factor: float, correction: float,
               colors: bool) -> mf.CaptureFile:
    sha, size = mf.sha256_file(path)
    return mf.CaptureFile(role=mf.MODEL_ROLE, path=MODEL_PATH, media_type=mf.MODEL_MEDIA_TYPE, image=None,
                          note=note(triangles, cameras, factor, correction, colors), byte_size=size, sha256=sha)
