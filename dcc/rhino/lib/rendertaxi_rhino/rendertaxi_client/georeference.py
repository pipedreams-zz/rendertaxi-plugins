"""Georeferenz aus einem Host: WGS84 → UTM und der Block ``geometry.georeference`` (RTX-RH-005, #627).

Die eine Stelle, an der ein Plugin einen Ort in Breite und Länge (WGS84) in ein metrisches Bezugssystem
bringt und daraus den Manifestblock ``geometry.georeference`` baut (Plugin-API §7.9, Schema
``capture-manifest.schema.json`` ``$defs/georeference``). Der erste Lieferant ist Rhino
(``EarthAnchorPoint``); der Block ist für jeden Host derselbe.

* **Bezugssystem** (``utm_epsg``): die UTM-Zone des Ortes, mit den Ausnahmen Südwestnorwegen (32V) und
  Spitzbergen (31X–37X). In den Zonen 28–38 innerhalb der Ausdehnung von ETRS89 das europäische System
  ETRS89 / UTM (EPSG 258xx) — so liegen auch die Umgebungsmodelle (gisloader, EPSG 25832) —, sonst
  WGS 84 / UTM (326xx Nord, 327xx Süd). Die Koordinaten des Hosts sind WGS84; der Unterschied zu ETRS89
  ist eine Drift der Kontinentalplatte (2026 etwa 0,9 m) und wird **nicht** umgerechnet: ein Ort aus einer
  Karte in Rhino ist nicht genauer. Außerhalb von 80° S … 84° N gibt es kein UTM und keinen Block.
* **Abbildung** (``to_utm``): die Krüger-Reihe sechster Ordnung (Karney 2011, „Transverse Mercator with an
  accuracy of a few nanometers“), GRS80 für 258xx, WGS84 sonst — dieselbe Ellipsoidwahl wie PROJ.
* **Meridiankonvergenz** (``grid_convergence_deg``): um wie viel Gitter-Nord im Uhrzeigersinn gegen
  geografisch Nord gedreht ist — westlich des Mittelmeridians negativ, wie ``gridConvergenceDeg`` im
  Vertrag (``georeferenced-placement.ts``). Gemessen an der Abbildung selbst (Richtung des Meridians im
  Gitter), nicht an der Näherungsformel.
* **Block** (``manifest_block``): ``origin`` ist die Lage des Nullpunkts des Rahmens ``model`` (des
  Exportraums der GLB) im Bezugssystem; die Kette ``model → project`` kennt keinen Projektbezugsrahmen
  (den setzt ein Plugin nie, §7.9) und ist deshalb der Gitterrahmen am Modellursprung: **eine** Drehung um
  die Hochachse des Exportraums (+Y) um ``−α`` mit α dem Datei-Nordwinkel (Winkel von Gitter-Nord zur
  Planachse „Nord“ der Datei, im Uhrzeigersinn von oben positiv). Die Ableitung auf dem Server ist
  ``α = θₖ − φ`` mit θₖ = 0 (``fileGeoreferenceFromManifest``, ``georeferenz.md`` 2.2).

Nur Standardbibliothek, kein Hostmodul.
"""

from __future__ import annotations

import math

# Ellipsoide: große Halbachse und Abplattung.
WGS84 = (6378137.0, 1 / 298.257223563)
GRS80 = (6378137.0, 1 / 298.257222101)

UTM_SCALE = 0.9996
UTM_FALSE_EASTING = 500000.0
UTM_FALSE_NORTHING_SOUTH = 10000000.0
UTM_LATITUDE_RANGE = (-80.0, 84.0)

# ETRS89 / UTM zone 28N … 38N (EPSG 25828 … 25838) und die Ausdehnung von ETRS89 laut EPSG-Register
# (Area of use 1298, „Europe — ETRS89“): Breite 32,88° N … 84,73° N, Länge 16,1° W … 40,18° O.
ETRS89_ZONES = (28, 38)
ETRS89_LATITUDE = (32.88, 84.73)
ETRS89_LONGITUDE = (-16.1, 40.18)

# Präzisionsklassen des Manifests (capture-manifest.md, Abschnitt 7): Länge sechs, Winkel neun Stellen.
LENGTH_DIGITS = 6
ANGLE_DIGITS = 9

# Schrittweite der Konvergenzmessung in Grad Breite (≈ 1 cm); der Fehler liegt unter 1e-9°.
_CONVERGENCE_STEP_DEG = 1e-7


def _finite(*values) -> bool:
    return all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values)


# --------------------------------------------------------------------------
# Bezugssystem
# --------------------------------------------------------------------------


def utm_zone(lat: float, lon: float) -> int | None:
    """Die UTM-Zone eines Ortes (1–60) mit den Ausnahmen 32V und 31X–37X — ``None`` außerhalb von UTM."""
    if not _finite(lat, lon) or not UTM_LATITUDE_RANGE[0] <= lat <= UTM_LATITUDE_RANGE[1]:
        return None
    lon = ((lon + 180.0) % 360.0) - 180.0
    zone = int((lon + 180.0) // 6.0) + 1
    zone = min(max(zone, 1), 60)
    if 56.0 <= lat < 64.0 and 3.0 <= lon < 12.0:
        return 32
    if 72.0 <= lat <= 84.0 and 0.0 <= lon < 42.0:
        for limit, svalbard in ((9.0, 31), (21.0, 33), (33.0, 35), (42.0, 37)):
            if lon < limit:
                return svalbard
    return zone


def utm_epsg(lat: float, lon: float) -> int | None:
    """Der EPSG-Code des metrischen Bezugssystems am Ort: ETRS89 / UTM in Europa, sonst WGS 84 / UTM."""
    zone = utm_zone(lat, lon)
    if zone is None:
        return None
    if (ETRS89_ZONES[0] <= zone <= ETRS89_ZONES[1] and ETRS89_LATITUDE[0] <= lat <= ETRS89_LATITUDE[1]
            and ETRS89_LONGITUDE[0] <= lon <= ETRS89_LONGITUDE[1]):
        return 25800 + zone
    return (32600 if lat >= 0 else 32700) + zone


def _system(epsg: int) -> tuple[int, bool, tuple[float, float]] | None:
    """``(Zone, Süd, Ellipsoid)`` eines UTM-Codes — ``None`` für jeden anderen Code."""
    if 25828 <= epsg <= 25838:
        return epsg - 25800, False, GRS80
    if 32601 <= epsg <= 32660:
        return epsg - 32600, False, WGS84
    if 32701 <= epsg <= 32760:
        return epsg - 32700, True, WGS84
    return None


# --------------------------------------------------------------------------
# Abbildung (Krüger-Reihe, Karney 2011)
# --------------------------------------------------------------------------


def _series(f: float) -> tuple[float, tuple[float, ...]]:
    n = f / (2 - f)
    n2, n3 = n * n, n * n * n
    n4, n5, n6 = n3 * n, n3 * n2, n3 * n3
    rectifying = (1 + n2 / 4 + n4 / 64 + n6 / 256) / (1 + n)
    alpha = (
        n / 2 - 2 * n2 / 3 + 5 * n3 / 16 + 41 * n4 / 180 - 127 * n5 / 288 + 7891 * n6 / 37800,
        13 * n2 / 48 - 3 * n3 / 5 + 557 * n4 / 1440 + 281 * n5 / 630 - 1983433 * n6 / 1935360,
        61 * n3 / 240 - 103 * n4 / 140 + 15061 * n5 / 26880 + 167603 * n6 / 181440,
        49561 * n4 / 161280 - 179 * n5 / 168 + 6601661 * n6 / 7257600,
        34729 * n5 / 80640 - 3418889 * n6 / 1995840,
        212378941 * n6 / 319334400,
    )
    return rectifying, alpha


def to_utm(lat: float, lon: float, epsg: int) -> tuple[float, float]:
    """Ost und Nord eines WGS84-Ortes in ``epsg`` (nur UTM, ``ValueError`` sonst)."""
    system = _system(epsg)
    if system is None or not _finite(lat, lon):
        raise ValueError(f"kein UTM-Bezugssystem: {epsg}")
    zone, south, (a, f) = system
    rectifying, alpha = _series(f)
    e = math.sqrt(f * (2 - f))
    phi = math.radians(lat)
    lam = math.radians(((lon - (zone * 6 - 183) + 180.0) % 360.0) - 180.0)
    sin_phi = math.sin(phi)
    tau = math.sinh(math.atanh(sin_phi) - e * math.atanh(e * sin_phi))
    xi_p = math.atan2(tau, math.cos(lam))
    eta_p = math.asinh(math.sin(lam) / math.hypot(tau, math.cos(lam)))
    xi, eta = xi_p, eta_p
    for j, a_j in enumerate(alpha, start=1):
        xi += a_j * math.sin(2 * j * xi_p) * math.cosh(2 * j * eta_p)
        eta += a_j * math.cos(2 * j * xi_p) * math.sinh(2 * j * eta_p)
    scale = UTM_SCALE * a * rectifying
    return (UTM_FALSE_EASTING + scale * eta,
            (UTM_FALSE_NORTHING_SOUTH if south else 0.0) + scale * xi)


def grid_convergence_deg(lat: float, lon: float, epsg: int) -> float:
    """Die Meridiankonvergenz γ in Grad: Gitter-Nord gegen geografisch Nord, im Uhrzeigersinn positiv.

    Die Richtung des Meridians im Gitter, gemessen an ``to_utm`` selbst (zentrale Differenz entlang der
    Breite): geografisch Nord zeigt im Gitter um ``atan2(dE, dN)`` im Uhrzeigersinn neben Gitter-Nord,
    also liegt Gitter-Nord um γ = −atan2(dE, dN) daneben.
    """
    step = _CONVERGENCE_STEP_DEG
    east_low, north_low = to_utm(lat - step, lon, epsg)
    east_high, north_high = to_utm(lat + step, lon, epsg)
    return -math.degrees(math.atan2(east_high - east_low, north_high - north_low))


# --------------------------------------------------------------------------
# Dateiursprung und Manifestblock
# --------------------------------------------------------------------------


def _round(value: float, digits: int) -> float:
    return round(float(value), digits) + 0.0  # + 0.0: keine negative Null


def plan_to_grid(x: float, y: float, north_angle_deg: float) -> tuple[float, float]:
    """Ein Planvektor (x Plan-Ost, y Plan-Nord) im Gitter, wenn Plan-Nord um α im Uhrzeigersinn neben
    Gitter-Nord liegt: ``(x cos α + y sin α, −x sin α + y cos α)``."""
    angle = math.radians(north_angle_deg)
    return (x * math.cos(angle) + y * math.sin(angle), -x * math.sin(angle) + y * math.cos(angle))


def file_georeference(lat: float, lon: float, height: float, anchor_point, plan_north_geo_deg: float) -> dict | None:
    """Die absolute Georeferenz des Dateinullpunkts aus einem Ankerpunkt — ``None`` außerhalb von UTM.

    ``lat``, ``lon`` (WGS84, Grad) und ``height`` (Meter) beschreiben den Punkt ``anchor_point`` der Datei
    (x Plan-Ost, y Plan-Nord, z oben, **Meter**). ``plan_north_geo_deg`` ist der Winkel von geografisch
    Nord zur Planachse „Nord“ der Datei, im Uhrzeigersinn von oben positiv. Ergebnis wie
    ``FileGeoreference`` im Vertrag::

        {"epsg", "east", "north", "height", "northAngleDeg", "convergenceDeg"}

    mit ``northAngleDeg`` = α gegen **Gitter**-Nord (``gridNorthAngleOf``: α = β − γ). Der Ursprung liegt
    um den Planvektor ``−anchor_point`` neben dem Ankerpunkt, in Gitterrichtung gedreht; der Maßstab der
    Abbildung (≈ 0,9996 … 1,0004) bleibt außen vor, damit der Ankerpunkt selbst genau sitzt.
    """
    if not _finite(lat, lon, height, plan_north_geo_deg, *anchor_point):
        return None
    epsg = utm_epsg(lat, lon)
    if epsg is None:
        return None
    east, north = to_utm(lat, lon, epsg)
    convergence = grid_convergence_deg(lat, lon, epsg)
    alpha = plan_north_geo_deg - convergence
    x, y, z = anchor_point
    offset_east, offset_north = plan_to_grid(-x, -y, alpha)
    return {
        "epsg": epsg,
        "east": east + offset_east,
        "north": north + offset_north,
        "height": height - z,
        "northAngleDeg": alpha,
        "convergenceDeg": convergence,
    }


def manifest_block(georeference: dict) -> dict:
    """``geometry.georeference`` (§7.9) aus ``file_georeference``: Exportraum, Meter, Kette mit einer Drehung.

    Die Drehung steht immer da, auch bei α = 0: die Kette sagt dann ausdrücklich, dass die Datei gitternord
    liegt. ``angleRadians`` ist −α (Drehung um +Y, rechtshändig, von oben gegen den Uhrzeiger positiv).
    """
    return {
        "epsg": int(georeference["epsg"]),
        "origin": {
            "easting": _round(georeference["east"], LENGTH_DIGITS),
            "northing": _round(georeference["north"], LENGTH_DIGITS),
            "height": _round(georeference["height"], LENGTH_DIGITS),
        },
        "axisConvention": {"handedness": "right", "upAxis": "y"},
        "unit": "meter",
        "transformChain": [{
            "order": 1,
            "sourceFrame": "model",
            "targetFrame": "project",
            "kind": "rotation",
            "parameters": {"axis": "y",
                           "angleRadians": _round(-math.radians(georeference["northAngleDeg"]), ANGLE_DIGITS)},
            "provenance": "plugin-reported",
        }],
    }


def block_text(block: dict) -> str:
    """Der Block in einer Zeile für Hinweise und Notizen: EPSG, Ost, Nord, Höhe, Nordwinkel in Grad."""
    origin = block["origin"]
    angle = -math.degrees(block["transformChain"][0]["parameters"]["angleRadians"])
    return (f"EPSG:{block['epsg']} Ost {origin['easting']:.3f} m, Nord {origin['northing']:.3f} m, "
            f"Höhe {origin['height']:.3f} m, Nordwinkel {angle:.4f}°")
