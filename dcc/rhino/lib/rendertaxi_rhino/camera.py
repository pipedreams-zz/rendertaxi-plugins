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
# Rhino rechnet Nah- und Fernebene einer Ansicht aus der Szene, wenn es sie zeichnet; was eine Ansicht speichert,
# ist der Stand von damals (gemessen: benannte Ansicht mit nah 4,95 m, fern 10,1 m vor einer 60 m großen Szene).
# Übernähme ein Betrachter das, schnitte er das Modell ab. Gesendet wird deshalb nah höchstens 1 cm und keine
# Fernebene (automatisch, Vertrag 11.3 und ``modelViewCameraFromGltf``).
AUTO_NEAR_M = 0.01
# Eine Parallelkamera sieht alles im Quader ihres Frustums — auch hinter ihrem Standort. glTF und der Betrachter
# schneiden aber vor ``znear`` (≥ 0) und hinter ``zfar`` ab. Die Ebenen kommen deshalb aus der Hülle der Szene
# entlang der Blickrichtung, nie aus der gespeicherten Fernebene (RTX-RH-003, Review F-01): Liegt ein Teil der
# Szene näher als ``AUTO_NEAR_M`` plus Sicherheitsabstand, rückt die Kamera entlang ihrer Blickrichtung zurück —
# das Bild einer Parallelprojektion ändert sich dadurch nicht.
DEPTH_MARGIN = 0.01  # Anteil der Tiefe der Szene, mindestens ``AUTO_NEAR_M``
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


def _corners(bounds):
    low, high = bounds
    return [(x, y, z) for x in (low[0], high[0]) for y in (low[1], high[1]) for z in (low[2], high[2])]


def scene_depths(view: View, bounds) -> tuple[float, float]:
    """``(nächste, fernste)`` Tiefe der Hüllenecken entlang der Blickrichtung ab dem Standort — Modelleinheiten."""
    dx, dy, dz = view.direction
    length = math.sqrt(dx * dx + dy * dy + dz * dz)
    if not length:
        raise ValueError("Nullvektor")
    lx, ly, lz = view.location
    depths = [((x - lx) * dx + (y - ly) * dy + (z - lz) * dz) / length for x, y, z in _corners(bounds)]
    return min(depths), max(depths)


def parallel_reach(view: View, bounds, meters: float) -> tuple[float, float, float]:
    """``(zurück, nah, fern)`` einer Parallelkamera — ``zurück`` in Modelleinheiten, ``nah`` und ``fern`` in Metern.

    Jede Ecke der Hülle liegt danach zwischen nah und fern, mit Sicherheitsabstand: nah ist ``AUTO_NEAR_M``, die
    nächste Ecke liegt mindestens nah plus Abstand vor der Kamera, die fernste mindestens den Abstand vor fern.
    """
    nearest, farthest = scene_depths(view, bounds)
    margin = max(AUTO_NEAR_M, DEPTH_MARGIN * (farthest - nearest) * meters)
    back = max(0.0, AUTO_NEAR_M + margin - nearest * meters)
    return back / meters, AUTO_NEAR_M, farthest * meters + back + margin


def _moved_back(view: View, distance: float) -> View:
    dx, dy, dz = view.direction
    length = math.sqrt(dx * dx + dy * dy + dz * dz)
    x, y, z = view.location
    return View(view.perspective, (x - dx / length * distance, y - dy / length * distance, z - dz / length * distance),
                view.direction, view.up, view.left, view.right, view.bottom, view.top, view.near, view.far)


def camera_block(view: View, size: tuple[int, int], meters: float, lens_and_shift: bool, bounds=None) -> dict:
    """Der Kamerablock für ein Bild ``size`` aus dem Frustum der Ansicht in dieser Bildgröße.

    ``lens_and_shift``: der Server setzt Manifest 1.4.0 um — ``lens`` (Perspektive) und ``shift`` (wenn nicht 0).
    ``bounds``: die Hülle der sichtbaren Szene ``(min, max)`` in Modelleinheiten; eine Parallelkamera rückt
    danach so weit zurück, dass keine Ecke vor ihrer Nahebene liegt (``parallel_reach``).
    """
    if not view.perspective and bounds is not None:
        view = _moved_back(view, parallel_reach(view, bounds, meters)[0])
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
        block["clip"] = {"near": _length(min(view.near * meters, AUTO_NEAR_M)), "far": None}
        if lens_and_shift:
            focal = SENSOR_SHORT_MM / 2.0 / math.tan(angle / 2.0)
            if FOCAL_RANGE_MM[0] <= focal <= FOCAL_RANGE_MM[1]:
                block["lens"] = {"focalLengthMm": round(focal, 6), "sensorWidthMm": SENSOR_SHORT_MM,
                                 "sensorFit": "vertical" if vertical else "horizontal"}
    else:
        block["extent"] = {"halfWidth": _length(frame_w / 2.0 * meters), "halfHeight": _length(frame_h / 2.0 * meters)}
        near = AUTO_NEAR_M if bounds is not None else min(max(view.near * meters, 0.000001), AUTO_NEAR_M)
        block["clip"] = {"near": _length(near), "far": None}
    if lens_and_shift:
        longer = max(frame_w, frame_h)
        shift_x = round(((view.left + view.right) / 2.0) / longer, 6) + 0.0
        shift_y = round(((view.bottom + view.top) / 2.0) / longer, 6) + 0.0
        if shift_x or shift_y:
            block["shift"] = {"x": shift_x, "y": shift_y}
    return block


def gltf_camera(view: View, size: tuple[int, int], meters: float, name: str, bounds=None) -> dict:
    """Eine Kamera für ``glb.add_cameras``: Knoten im Exportraum, glTF-Definition, Objektiv und Shift in ``extras``.

    Dieselben Zahlen wie ``camera_block`` (ein Weg für Manifest und Datei): ``yfov`` aus der Höhe des Frustums,
    ``aspectRatio`` aus der Bildgröße; parallel ``xmag``/``ymag`` als halbe Ausdehnung. Objektiv (24 mm auf der
    kürzeren Seite, QR-07) und Shift gehen nach ``extras.rendertaxi.camera`` (Vertrag 11.5) — das Objektiv
    beschreibt mit ``aspectRatio`` denselben Vertikalwinkel wie ``yfov``.

    Schnittebenen: perspektivisch wie im Kamerablock (nah höchstens ``AUTO_NEAR_M``, ohne ``zfar``; was hinter
    einer perspektivischen Kamera liegt, sieht sie ohnehin nicht). glTF verlangt parallel ein ``zfar``: beide
    Ebenen kommen aus ``bounds``, der Hülle der Szene in Modelleinheiten (``parallel_reach``), die Kamera rückt
    wenn nötig zurück. Ohne ``bounds`` gibt es keine Parallelkamera (``ValueError``).
    """
    from .rendertaxi_client import glb

    block = camera_block(view, size, meters, lens_and_shift=True, bounds=bounds)
    width, height = size
    frame_h = view.top - view.bottom
    if view.perspective:
        definition = {"type": "perspective", "perspective": {
            "yfov": round(2.0 * math.atan(frame_h / 2.0 / view.near), 9), "aspectRatio": round(width / height, 9),
            "znear": block["clip"]["near"]}}
    else:
        if bounds is None:
            raise ValueError("Parallelkamera ohne Hülle der Szene")
        _back, near, far = parallel_reach(view, bounds, meters)
        extent = block["extent"]
        definition = {"type": "orthographic",
                      "orthographic": {"xmag": extent["halfWidth"], "ymag": extent["halfHeight"],
                                       "znear": _length(near), "zfar": _length(far)}}
    extras = glb.camera_extras(block.get("lens"), block.get("shift"))
    if extras:
        definition["extras"] = {glb.CAMERA_EXTRAS_KEY: {glb.CAMERA_EXTRAS_PART: extras}}
    return {"name": name, "position": block["position"], "direction": block["direction"], "up": block["up"],
            "definition": definition}
