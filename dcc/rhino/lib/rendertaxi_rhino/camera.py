"""Die Kamera einer Rhino-Ansicht als ``camera``-Block des Manifests — ohne ``Rhino``, hostfrei prüfbar.

Am Host gemessen (QR-07, Rhino 8.35, 08.10.2026, ``docs/measurements/2026-10-08-kamera-und-kennungen.md``):

* ``Camera35mmLensLength`` bezieht sich auf die **kürzere** Bildseite mit 24 mm: bei 16:9, 3:2 und 1:1 bleibt
  der halbe Vertikalwinkel einer 50-mm-Ansicht 13,4957° (= atan(12 / 50)), bei 9:16 wird er zum
  Horizontalwinkel.
* ``GetCameraAngles`` liefert bei verschobenem (asymmetrischem) Frustum den Winkel zur **weiter entfernten**
  Kante (18,57° statt der Hälfte von 26,77°). Der Block rechnet deshalb nie mit ``GetCameraAngles`` oder der
  Brennweite, sondern aus dem Frustum der aufgenommenen Bildgröße: Winkel aus der Ausdehnung, Shift aus der
  Lage der Mitte.

Raum: Exportraum des Manifests (Meter, rechtshändig, +Y oben, Abschnitt 11.3) — Rhino-Achsen um −90° um X
gedreht wie Rhinos glTF-Export, keine Spiegelung (``passes.to_export``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .passes import to_export

SENSOR_SHORT_MM = 24.0  # QR-07: die Brennweite gilt für die kürzere Bildseite
FOCAL_RANGE_MM = (1.0, 1200.0)  # Vertrag 11.3: focalLengthMm 1–1200


@dataclass(frozen=True)
class View:
    """Was der Adapter aus ``ViewportInfo`` liest — in Modelleinheiten und Rhino-Achsen."""

    perspective: bool
    location: tuple[float, float, float]
    direction: tuple[float, float, float]
    up: tuple[float, float, float]
    left: float
    right: float
    bottom: float
    top: float
    near: float
    far: float


def _unit(vector) -> list[float]:
    x, y, z = vector
    length = math.sqrt(x * x + y * y + z * z)
    if not length:
        raise ValueError("Nullvektor")
    return [round(c, 9) + 0.0 for c in to_export(x / length, y / length, z / length)]


def _length(value: float) -> float:
    return round(value, 6) + 0.0


def camera_block(view: View, size: tuple[int, int], meters: float, lens_and_shift: bool) -> dict:
    """Der Kamerablock für ein Bild ``size`` aus dem Frustum der Ansicht in dieser Bildgröße.

    ``lens_and_shift``: der Server setzt Manifest 1.4.0 um — ``lens`` (Perspektive) und ``shift`` (wenn nicht 0).
    """
    width, height = size
    frame_w = view.right - view.left
    frame_h = view.top - view.bottom
    if frame_w <= 0 or frame_h <= 0 or view.near <= 0:
        raise ValueError("Frustum ohne Ausdehnung")
    block: dict = {
        "space": "export",
        "projection": "perspective" if view.perspective else "orthographic",
        "position": [_length(c * meters) for c in to_export(*view.location)],
        "direction": _unit(view.direction),
        "up": _unit(view.up),
    }
    vertical = width >= height  # die kürzere Bildseite — an ihr hängt das Objektiv
    if view.perspective:
        extent = frame_h if vertical else frame_w
        angle = 2.0 * math.atan(extent / 2.0 / view.near)
        block["fieldOfView"] = {"axis": "vertical" if vertical else "horizontal", "angle": round(angle, 9)}
        far = view.far * meters if view.far > view.near else None
        block["clip"] = {"near": _length(view.near * meters), "far": _length(far) if far else None}
        if lens_and_shift:
            focal = SENSOR_SHORT_MM / 2.0 / math.tan(angle / 2.0)
            if FOCAL_RANGE_MM[0] <= focal <= FOCAL_RANGE_MM[1]:
                block["lens"] = {"focalLengthMm": round(focal, 6), "sensorWidthMm": SENSOR_SHORT_MM,
                                 "sensorFit": "vertical" if vertical else "horizontal"}
    else:
        block["extent"] = {"halfWidth": _length(frame_w / 2.0 * meters), "halfHeight": _length(frame_h / 2.0 * meters)}
        block["clip"] = {"near": _length(max(view.near * meters, 0.000001)),
                         "far": _length(view.far * meters) if view.far > view.near else None}
    if lens_and_shift:
        longer = max(frame_w, frame_h)
        shift_x = round(((view.left + view.right) / 2.0) / longer, 6) + 0.0
        shift_y = round(((view.bottom + view.top) / 2.0) / longer, 6) + 0.0
        if shift_x or shift_y:
            block["shift"] = {"x": shift_x, "y": shift_y}
    return block
