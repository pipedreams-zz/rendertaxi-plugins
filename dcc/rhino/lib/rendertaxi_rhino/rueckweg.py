"""Rückweg ohne ``Rhino`` (RTX-RH-005, #627) — Kameras als benannte Ansichten, Achsen, Namen, Prüfung der Lage.

Ein Export der Plattform (RTX-M2-105) ist eine GLB im Exportraum (Meter, rechtshändig, +Y oben): je Instanz ein
Wurzelknoten mit ``extras.rendertaxi.instance``, die Kameras des Blickpunkts als glTF-Kameras mit
``extras.rendertaxi.camera`` (``integrations/_shared/python/rendertaxi_client/model_import.py``). Was ``importer``
damit in Rhino tut, rechnet dieser Teil vorher hostfrei:

* **Achsen.** Rhinos glTF-Import (``Import_glTF``, Quelle ``mcneel/glTF-IO``, ``GltfUtils.YupToZup``) bildet
  ``(x, y, z)`` auf ``(x, −z, y)`` ab — die Umkehrung von ``MapZToY`` beim Export (``passes.to_export``) — und
  skaliert von Metern auf die Dokumenteinheit. ``to_rhino`` ist dieselbe Abbildung für Kameras und Hüllen.
* **Kamera → benannte Ansicht** (``rhino_view``): Rhino kennt keine Kameraobjekte. Eine Ansicht ist ein
  Frustum in Modelleinheiten: Standort, Blickrichtung, Oben und links/rechts/unten/oben auf der Nahebene. Der
  Vertikalwinkel ist ``yfov`` der Datei, die Breite folgt aus ``aspectRatio``; der Shift verschiebt die Mitte
  um ``shift · längere Seite`` — genau die Umkehrung von ``camera.camera_block``, der beim Export aus dem
  Frustum Winkel und Shift liest (QR-07: nie aus ``Camera35mmLensLength`` oder ``GetCameraAngles``). Die
  Linsenlänge, die Rhino danach zeigt, ist die Kleinbild-Brennweite an der **kürzeren** Seite (24 mm,
  QR-07): ``lens_length``. Parallelkameras: ``xmag``/``ymag`` sind die halbe Ausdehnung des Frustums.
* **Ebenen.** „rendertaxi <Export> <Datum>“ oben, darunter je Instanz eine Ebene mit ihrem Namen
  (``layer_name``: Rhinos Namensregeln, gleiche Geschwister durchnummeriert).
* **Prüfen statt glauben** (``import_fit``): nach dem Import wird die Hülle jeder Instanz gegen die Hülle ihrer
  Punkte in der Datei gemessen (``glb.world_point_bounds``) — wie der Exportweg den Maßstab des Exporters misst
  (``model.exporter_scale``). Passt sie bis auf einen gleichförmigen Faktor, setzt der Faktor die Objekte auf
  die Dokumenteinheit; seitenverkehrt, mit Z statt Y oben, verschoben oder verzerrt heißt: nichts bleibt.
"""

from __future__ import annotations

import dataclasses
import math
import re
from dataclasses import dataclass

from . import camera as cam
from .rendertaxi_client import model_import as mi

# Wie der Exportweg (``model.BOUNDS_TOLERANCE``): relativ zur Diagonale, die GLB speichert float32.
BOUNDS_TOLERANCE = 1e-4
SCALE_TOLERANCE = 1e-6
# Rhino begrenzt Namen nicht wie Blender auf 63 Bytes; lang genug für jeden Exportnamen der Plattform.
NAME_MAX_BYTES = 255
# Ohne ``aspectRatio`` in der Datei: das Verhältnis der aktiven Ansicht, und ohne sie 3:2 (Kleinbild).
FALLBACK_ASPECT = 1.5
# Ohne ``zfar``: Rhino rechnet Nah- und Fernebene beim Zeichnen ohnehin aus der Szene (``camera.py``).
FAR_FALLBACK_M = 1000.0
NEAR_FALLBACK_M = 0.01
# Der Zielpunkt einer Ansicht (Drehpunkt beim Umsehen), wenn die Szene nicht vor der Kamera liegt.
TARGET_FALLBACK_M = 10.0
NAMED_VIEW_SEPARATOR = " – "


def to_rhino(point, units_per_meter: float = 1.0) -> tuple[float, float, float]:
    """Ein Punkt (oder eine Richtung mit ``units_per_meter = 1``) des Exportraums in Rhino-Achsen."""
    x, y, z = point
    return (x * units_per_meter + 0.0, -z * units_per_meter + 0.0, y * units_per_meter + 0.0)


def bounds_to_rhino(bounds, units_per_meter: float) -> tuple[list, list]:
    """Eine Hülle des Exportraums in Rhino-Achsen: −z tauscht Anfang und Ende."""
    (lx, ly, lz), (hx, hy, hz) = bounds
    k = units_per_meter
    return [lx * k, -hz * k, ly * k], [hx * k, -lz * k, hy * k]


# --------------------------------------------------------------------------
# Namen
# --------------------------------------------------------------------------

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _clip(text: str, limit: int) -> str:
    raw = text.encode("utf-8")
    return text if len(raw) <= limit else raw[:limit].decode("utf-8", errors="ignore").rstrip()


def layer_name(text: str, fallback: str = "Modell") -> str:
    """Ein Name, den Rhino für Ebenen und Ansichten annimmt: ohne Steuerzeichen und ohne ``::`` (Trenner der
    Ebenenpfade), nicht mit Klammer oder Leerraum am Anfang; leer heißt ``fallback``."""
    name = _CONTROL.sub(" ", str(text or "")).replace("::", ":").strip()
    name = name.lstrip("([{ ").strip()
    return _clip(name, NAME_MAX_BYTES) or fallback


def unique_name(name: str, taken) -> str:
    """``name`` oder ``name (2)``, ``name (3)`` … — der erste, der nicht in ``taken`` steht (Groß/klein egal)."""
    lowered = {str(other).casefold() for other in taken}
    if name.casefold() not in lowered:
        return name
    number = 2
    while f"{name} ({number})".casefold() in lowered:
        number += 1
    return f"{name} ({number})"


def root_name(entry: dict) -> str:
    """„rendertaxi <Export> <Datum>“ — der Name des Exports wird gekürzt, nie das Datum."""
    return layer_name(mi.collection_name(entry, NAME_MAX_BYTES), "rendertaxi")


def view_name(root: str, camera_name: str, index: int) -> str:
    """Der Name einer benannten Ansicht: der Name der obersten Ebene als Präfix, dann der Kameraname."""
    return layer_name(f"{root}{NAMED_VIEW_SEPARATOR}{camera_name or f'Kamera {index + 1}'}")


# --------------------------------------------------------------------------
# Kamera → Ansicht
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class NamedView:
    """Was ``importer`` in ``ViewportInfo`` setzt — Rhino-Achsen, Modelleinheiten; dazu Linsenlänge und Name."""

    name: str
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
    target: tuple[float, float, float]
    aspect: float
    lens_length: float | None

    def as_camera_view(self) -> cam.View:
        """Dieselbe Ansicht in der Form, die der Exportweg liest (``camera.View``) — für die Probe hin und zurück."""
        return cam.View(self.perspective, self.location, self.direction, self.up, self.left, self.right,
                        self.bottom, self.top, self.near, self.far)


def lens_length(view: "NamedView") -> float | None:
    """Die Linsenlänge, die Rhino zeigt: Kleinbild an der kürzeren Seite (24 mm, QR-07), aus der Ausdehnung."""
    if not view.perspective:
        return None
    shorter = min(view.right - view.left, view.top - view.bottom)
    return cam.SENSOR_SHORT_MM / 2.0 * view.near / (shorter / 2.0)


def _target(location, direction, scene_center, units_per_meter: float) -> tuple[float, float, float]:
    distance = TARGET_FALLBACK_M * units_per_meter
    if scene_center is not None:
        ahead = sum((c - l) * d for c, l, d in zip(scene_center, location, direction))
        if ahead > 0:
            distance = ahead
    return tuple(l + d * distance for l, d in zip(location, direction))


def rhino_view(pose: dict, name: str, units_per_meter: float, fallback_aspect: float = FALLBACK_ASPECT,
               scene_center=None) -> NamedView | None:
    """Eine Kamera des Exports (``model_import.camera_poses``) als benannte Ansicht — ``None``, wenn unbrauchbar.

    ``scene_center``: die Mitte der importierten Szene in Rhino-Achsen und Modelleinheiten; liegt sie vor der
    Kamera, ist ihr Abstand der Zielpunkt der Ansicht.
    """
    host = mi.host_camera(pose.get("definition"))
    if host is None:
        return None
    definition = pose["definition"]
    k = units_per_meter
    location = to_rhino(pose["position"], k)
    direction = to_rhino(pose["direction"])
    up = to_rhino(pose["up"])
    aspect = host["aspect"] if host["aspect"] else (fallback_aspect if fallback_aspect > 0 else FALLBACK_ASPECT)
    near_m = host["near"] if host["near"] and host["near"] > 0 else NEAR_FALLBACK_M
    far_m = host["far"] if host["far"] and host["far"] > near_m else max(FAR_FALLBACK_M, near_m * 1000.0)
    near = near_m * k
    if host["projection"] == "perspective":
        half_height = near * math.tan(definition["perspective"]["yfov"] / 2.0)
        half_width = half_height * aspect
    else:
        half_width, half_height = host["halfWidth"] * k, host["halfHeight"] * k
    longer = 2.0 * max(half_width, half_height)
    center_x = host["shiftLonger"]["x"] * longer
    center_y = host["shiftLonger"]["y"] * longer
    view = NamedView(
        name=name, perspective=host["projection"] == "perspective", location=location, direction=direction, up=up,
        left=center_x - half_width, right=center_x + half_width,
        bottom=center_y - half_height, top=center_y + half_height,
        near=near, far=far_m * k, target=_target(location, direction, scene_center, k), aspect=aspect,
        lens_length=None)
    return dataclasses.replace(view, lens_length=lens_length(view))


# --------------------------------------------------------------------------
# Prüfung der Lage nach dem Import
# --------------------------------------------------------------------------


class ImportFitError(ValueError):
    """Rhino hat das Modell anders gelegt, als die Datei sagt — mit einem Satz für die Palette."""


def _fits(expected_low, expected_high, factor, low, high, tolerance) -> bool:
    return all(abs(low[a] - factor * expected_low[a]) <= tolerance
               and abs(high[a] - factor * expected_high[a]) <= tolerance for a in range(3))


def import_fit(expected, measured) -> float:
    """Der Faktor, um den Rhino die Hülle ``expected`` (Rhino-Achsen, Dokumenteinheit) gegen ``measured`` skaliert hat.

    1 heißt: richtig. Ein anderer gleichförmiger Faktor heißt: richtig gelegt, falsche Einheit — ``importer``
    setzt die Objekte mit ``1 / Faktor`` zurück. Sonst ``ImportFitError`` mit dem Grund.
    """
    (e_low, e_high), (m_low, m_high) = expected, measured
    expected_diagonal = math.dist(e_low, e_high)
    measured_diagonal = math.dist(m_low, m_high)
    if expected_diagonal == 0 or measured_diagonal == 0:
        raise ImportFitError("Der Export hat kein Modell mit Ausdehnung.")
    factor = measured_diagonal / expected_diagonal
    tolerance = BOUNDS_TOLERANCE * measured_diagonal
    if _fits(e_low, e_high, factor, m_low, m_high, tolerance):
        return 1.0 if abs(factor - 1.0) <= SCALE_TOLERANCE else factor
    # Z oben ohne Achsentausch: die Hülle der Datei unverändert (x, y, z) statt (x, −z, y).
    unrotated = ([e_low[0], e_low[2], -e_high[1]], [e_high[0], e_high[2], -e_low[1]])
    if _fits(*unrotated, factor, m_low, m_high, tolerance):
        raise ImportFitError("Der glTF-Import von Rhino hat das Modell mit Y statt Z nach oben gelegt. "
                             "Nichts importiert.")
    raise ImportFitError("Der glTF-Import von Rhino hat das Modell verschoben oder verzerrt. Nichts importiert.")


def merge_bounds(boxes):
    """Die gemeinsame Hülle mehrerer ``(min, max)`` — ``None`` ohne."""
    boxes = [box for box in boxes if box is not None]
    if not boxes:
        return None
    return ([min(box[0][a] for box in boxes) for a in range(3)], [max(box[1][a] for box in boxes) for a in range(3)])


def center(bounds):
    return None if bounds is None else tuple((bounds[0][a] + bounds[1][a]) / 2.0 for a in range(3))


# --------------------------------------------------------------------------
# Bezug und Ergebnis
# --------------------------------------------------------------------------

FRAME_REFERENCE = "Nullpunkt des Rahmens"


def reference_text(origin: dict | None) -> str:
    """Der Bezug als Benutzertext: verlustfrei bei „georeferenziert“, sonst „Nullpunkt des Rahmens“."""
    return FRAME_REFERENCE if origin is None else mi.origin_text(origin)


def import_summary(result: dict) -> str:
    """Ein Satz über einen gelungenen Import."""
    models = "1 Modell" if result["instances"] == 1 else f"{result['instances']} Modelle"
    views = "1 benannte Ansicht" if result["cameras"] == 1 else f"{result['cameras']} benannte Ansichten"
    where = "Bezug georeferenziert (Benutzertext)" if result["origin"] is not None else "am Nullpunkt des Rahmens"
    text = f"Importiert in „{result['root']}“: {models}, {views}, {where}."
    if result.get("skippedCameras"):
        text += f" {result['skippedCameras']} Kamera(s) nicht lesbar."
    if result.get("factor", 1.0) != 1.0:
        text += f" Maßstab des Imports {result['factor']:g} gemessen und korrigiert."
    return text
