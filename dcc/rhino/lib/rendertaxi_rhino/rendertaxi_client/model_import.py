"""Rückweg: ein Export aus rendertaxi zurück in den Host (RTX-B-005, P7 aus #593).

Die eine Stelle, an der ein Plugin einen **Export** der Plattform (RTX-M2-105, Herkunft
``model_export``) liest, bevor es ihn importiert — für alle Python-Hosts:

* **Liste** (``export_entries``): aus der Asset-Liste eines Projekts die Exporte mit Name,
  Zeitpunkt, Instanzen- und Kamerazahl; ``collection_name`` nennt die Sammlung im Host.
* **Kamera** (``host_camera``): die Rückrechnung einer glTF-Kamera des Exports in Objektiv, Sensor,
  Sensorbezug und Shift **auf der Seite des Sensorbezugs** — die Umkehrung von ``gltfCameraOf``
  (``packages/contracts/src/model-export.ts``) mit der Lesart von ``modelViewCameraFromGltf``
  (Objektiv nur, wenn es denselben Winkel beschreibt wie ``yfov``) und den Werten von
  ``blenderCameraOf`` (``packages/contracts/src/model-view.ts``). Die Form von Objektiv und Shift
  ist die des Capture-Manifests 1.4.0 (RTX-M2-026): Shift als Anteil der **längeren** Bildseite.
* **Lage** (``export_origin``, ``geo_root_transform``): der Bezug eines georeferenzierten Exports
  aus ``asset.extras.rendertaxi.origin`` und die Transformation, die ein Wurzelobjekt mit Z oben
  dafür trägt. Die Geometrie bleibt in den kleinen Koordinaten des Rahmens.
* **Zuordnung** (``mark_cameras``): jede Kameradefinition bekommt vor dem Import eine Nummer in
  ``extras``; der glTF-Import eines Hosts legt ``extras`` als Eigenschaft an der Kamera ab, so
  findet das Plugin zu jedem importierten Kameraobjekt seine Definition — unabhängig von Namen,
  die der Host beim Anlegen umbenennt.

Nur Standardbibliothek, kein Hostmodul.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from . import glb

# Die Herkunft eines Export-Assets (``assetOriginSchema``, ``kind: "model_export"``).
EXPORT_ORIGIN_KIND = "model_export"

# Die Vorgaben der Plattform (``packages/contracts/src/model-view.ts``).
DEFAULT_SENSOR_MM = 36.0  # MODEL_VIEW_DEFAULT_SENSOR_WIDTH_MM
LENS_TOLERANCE = 1e-5  # GLTF_CAMERA_LENS_TOLERANCE, Bogenmaß

# Der Schlüssel, unter dem ``mark_cameras`` die Nummer einer Definition ablegt.
CAMERA_MARK = "rendertaxiImportCamera"

# Blender begrenzt Namen auf 63 Bytes; die anderen Hosts vertragen mehr.
NAME_MAX_BYTES = 63


# --------------------------------------------------------------------------
# Liste der Exporte
# --------------------------------------------------------------------------


def _name_of(metadata) -> str | None:
    """Der Name eines Assets wie ``assetNameOf``: ``name`` vor ``fileName``, nur nichtleerer Text."""
    if not isinstance(metadata, dict):
        return None
    for key in ("name", "fileName"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def export_entry(asset) -> dict | None:
    """Ein Eintrag der Liste aus einem Asset — ``None``, wenn es kein Export ist.

    ``{"assetId", "name", "createdAt", "instances", "cameras", "reference", "hasFile", "byteSize"}``;
    ``hasFile`` ist falsch, solange das Asset nicht ``ready`` ist oder keine Datei hat (Regel 3: die
    Liste sagt es, statt beim Import zu scheitern).
    """
    if not isinstance(asset, dict) or asset.get("kind") != "model":
        return None
    origin = asset.get("origin")
    if not isinstance(origin, dict) or origin.get("kind") != EXPORT_ORIGIN_KIND:
        return None
    if asset.get("archivedAt"):
        return None
    instances = origin.get("instances")
    cameras = origin.get("cameras")
    blob = asset.get("blob")
    return {
        "assetId": str(asset.get("id") or ""),
        "name": _name_of(asset.get("metadata")) or "Export",
        "createdAt": str(asset.get("createdAt") or ""),
        "instances": len(instances) if isinstance(instances, list) else 0,
        "cameras": cameras if isinstance(cameras, int) and not isinstance(cameras, bool) else 0,
        "reference": "georeferenced" if origin.get("reference") == "georeferenced" else "frame",
        "byteSize": blob.get("byteSize") if isinstance(blob, dict) else None,
        "hasFile": asset.get("status") == "ready" and isinstance(blob, dict),
    }


def export_entries(assets) -> list[dict]:
    """Die Exporte einer Asset-Liste, der jüngste zuerst."""
    entries = [entry for entry in (export_entry(asset) for asset in assets or []) if entry and entry["assetId"]]
    return sorted(entries, key=lambda entry: entry["createdAt"], reverse=True)


def _local_time(instant: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(instant.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone()


def created_text(entry: dict) -> str:
    """Der Zeitpunkt eines Exports in Ortszeit, ``TT.MM.JJJJ HH:MM`` — leer, wenn unlesbar."""
    moment = _local_time(entry.get("createdAt") or "")
    return "" if moment is None else moment.strftime("%d.%m.%Y %H:%M")


def entry_summary(entry: dict) -> str:
    """Eine Zeile über den Export: Instanzen, Kameras und Bezug."""
    models = "1 Modell" if entry["instances"] == 1 else f"{entry['instances']} Modelle"
    cameras = "1 Kamera" if entry["cameras"] == 1 else f"{entry['cameras']} Kameras"
    reference = "georeferenziert" if entry["reference"] == "georeferenced" else "Nullpunkt des Rahmens"
    return f"{models}, {cameras}, {reference}"


def _clip_bytes(text: str, limit: int) -> str:
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text
    return raw[:limit].decode("utf-8", errors="ignore").rstrip()


def collection_name(entry: dict, max_bytes: int = NAME_MAX_BYTES) -> str:
    """„rendertaxi <Name> <Datum>“ — der Name des Exports wird gekürzt, nie das Datum."""
    created = created_text(entry)
    tail = f" {created}" if created else ""
    head = "rendertaxi "
    room = max_bytes - len(head.encode("utf-8")) - len(tail.encode("utf-8"))
    return f"{head}{_clip_bytes(entry.get('name') or 'Export', max(room, 1))}{tail}"


# --------------------------------------------------------------------------
# Kamera: glTF-Definition → Objektiv, Sensor, Shift
# --------------------------------------------------------------------------


def _number(value) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


def _lens_matches(lens: dict, yfov: float, aspect: float | None) -> bool:
    """Ob das Objektiv denselben vertikalen Winkel beschreibt wie ``yfov`` (``lensMatchesYfov``)."""
    if aspect is None and lens["sensorFit"] != "vertical":
        return False
    ratio = aspect if aspect is not None and aspect > 0 else 1.0
    tangent = lens["sensorWidthMm"] / (2 * lens["focalLengthMm"])
    horizontal = lens["sensorFit"] == "horizontal" or (lens["sensorFit"] == "auto" and ratio >= 1)
    vertical = tangent / ratio if horizontal else tangent
    return abs(2 * math.atan(vertical) - yfov) <= LENS_TOLERANCE


def fitted_axis(sensor_fit: str, aspect: float) -> str:
    """Die Bildseite, an der der Sensorbezug misst: ``horizontal`` oder ``vertical`` (``auto``: die längere)."""
    if sensor_fit in ("horizontal", "vertical"):
        return sensor_fit
    return "horizontal" if aspect >= 1 else "vertical"


def fitted_shift(shift: dict, sensor_fit: str, aspect: float) -> tuple[float, float]:
    """Der Shift des Vertrags (Anteil der **längeren** Seite) als Anteil der Seite des Sensorbezugs.

    Die Umkehrung von ``contract_shift`` der Blender-Extension: ``shift · längere / Seite des Bezugs``.
    """
    width, height = (aspect, 1.0) if aspect > 0 else (1.0, 1.0)
    longer = max(width, height)
    fitted = width if fitted_axis(sensor_fit, aspect) == "horizontal" else height
    return shift["x"] * longer / fitted, shift["y"] * longer / fitted


def host_camera(definition) -> dict | None:
    """Eine Kamera des Exports, wie ein Host sie einstellt — ``None``, wenn die Definition unbrauchbar ist.

    Perspektivisch::

        {"projection": "perspective", "aspect", "focalLengthMm", "sensorMm", "sensorFit",
         "shift": (x, y), "shiftLonger": {"x", "y"}, "near", "far"}

    ``sensorFit`` ist ``auto``/``horizontal``/``vertical``, ``sensorMm`` gilt auf dessen Seite, ``shift``
    ist ein Anteil dieser Seite (so rechnen Blender, Cinema 4D und Rhino), ``shiftLonger`` die Form des
    Vertrags. Ohne Objektiv in ``extras`` (oder mit einem, das nicht zu ``yfov`` passt): Kleinbildsensor
    auf der Höhe, die Brennweite aus ``yfov``; unter 1 mm bleibt die Brennweite 1 mm und der Sensor
    wird kleiner (dasselbe Verhältnis, wie ``blenderCameraOf``).

    Parallel::

        {"projection": "orthographic", "aspect", "halfWidth", "halfHeight", "sensorFit": "auto",
         "shift": (x, y), "shiftLonger", "near", "far"}

    ``aspect`` ist ``aspectRatio`` der Datei (das Verhältnis des Rahmens) beziehungsweise
    ``xmag/ymag``; ``None``, wenn die Datei keins nennt.
    """
    if not isinstance(definition, dict):
        return None
    extras = glb.read_camera_extras(definition) or {}
    shift = extras.get("shift") or {"x": 0.0, "y": 0.0}
    kind = definition.get("type")
    if kind == "orthographic":
        part = definition.get("orthographic") or {}
        xmag, ymag = _number(part.get("xmag")), _number(part.get("ymag"))
        if not xmag or not ymag:
            return None
        aspect = abs(xmag) / abs(ymag)
        return {
            "projection": "orthographic",
            "aspect": aspect,
            "halfWidth": abs(xmag),
            "halfHeight": abs(ymag),
            "sensorFit": "auto",
            "shift": fitted_shift(shift, "auto", aspect),
            "shiftLonger": dict(shift),
            "near": _number(part.get("znear")),
            "far": _number(part.get("zfar")),
        }
    if kind != "perspective":
        return None
    part = definition.get("perspective") or {}
    yfov = _number(part.get("yfov"))
    if yfov is None or not 0 < yfov < math.pi:
        return None
    aspect = _number(part.get("aspectRatio"))
    aspect = aspect if aspect is not None and aspect > 0 else None
    lens = extras.get("lens")
    if lens is not None and _lens_matches(lens, yfov, aspect):
        focal, sensor, fit = float(lens["focalLengthMm"]), float(lens["sensorWidthMm"]), lens["sensorFit"]
    else:
        tangent = math.tan(yfov / 2)
        fit, sensor = "vertical", DEFAULT_SENSOR_MM
        focal = sensor / (2 * tangent)
        if focal < 1:
            focal, sensor = 1.0, 2 * tangent
    return {
        "projection": "perspective",
        "aspect": aspect,
        "focalLengthMm": focal,
        "sensorMm": sensor,
        "sensorFit": fit,
        # Ohne Verhältnis nur ein vertikaler Bezug — dort ist der Shift unabhängig davon.
        "shift": fitted_shift(shift, fit, aspect if aspect is not None else 1.0),
        "shiftLonger": dict(shift),
        "near": _number(part.get("znear")),
        "far": _number(part.get("zfar")),
    }


# --------------------------------------------------------------------------
# Datei: Kameras zuordnen, Bezug lesen
# --------------------------------------------------------------------------


def mark_cameras(path: str) -> list[dict]:
    """Jede Kameradefinition in ``path`` mit ihrer Nummer markieren — die Definitionen **vor** dem Markieren.

    Die Nummer steht in ``cameras[i].extras[CAMERA_MARK]``; der Rest von ``extras`` bleibt. Die Datei
    wird nur im JSON-Teil verändert (``glb._write``) und ist danach die, die der Host importiert.
    """
    document, rest = glb._read(path)
    cameras = document.get("cameras")
    if not isinstance(cameras, list) or not cameras:
        return []
    original = [dict(definition) if isinstance(definition, dict) else {} for definition in cameras]
    for index, definition in enumerate(cameras):
        if not isinstance(definition, dict):
            continue
        extras = definition.get("extras")
        extras = dict(extras) if isinstance(extras, dict) else {}
        extras[CAMERA_MARK] = index
        definition["extras"] = extras
    glb._write(path, document, rest)
    return original


def export_origin(document: dict) -> dict | None:
    """``asset.extras.rendertaxi.origin`` eines Exports — ``None`` beim Bezug „Nullpunkt des Rahmens“.

    ``{"epsg", "east", "north", "height", "northAngleDeg"}`` in Metern und Grad, so wie
    ``gltfWithExportExtras`` es schreibt; eine fehlende Höhe oder ein fehlender Nordwinkel gilt als 0.
    """
    asset = document.get("asset") if isinstance(document, dict) else None
    extras = asset.get("extras") if isinstance(asset, dict) else None
    own = extras.get(glb.CAMERA_EXTRAS_KEY) if isinstance(extras, dict) else None
    origin = own.get("origin") if isinstance(own, dict) else None
    if not isinstance(origin, dict):
        return None
    east, north = _number(origin.get("east")), _number(origin.get("north"))
    epsg = origin.get("epsg")
    if east is None or north is None or not isinstance(epsg, int) or isinstance(epsg, bool):
        return None
    return {
        "epsg": epsg,
        "east": east,
        "north": north,
        "height": _number(origin.get("height")) or 0.0,
        "northAngleDeg": _number(origin.get("northAngleDeg")) or 0.0,
    }


def origin_text(origin: dict) -> str:
    """Der Bezug als Text, verlustfrei — die Zahlen in voller Genauigkeit."""
    return (f"EPSG:{origin['epsg']} Ost {origin['east']!r} m, Nord {origin['north']!r} m, "
            f"Höhe {origin['height']!r} m, Nordwinkel {origin['northAngleDeg']!r}°")


def geo_root_transform(origin: dict | None) -> dict:
    """Die Lage eines Wurzelobjekts in einem Host mit **Z oben** (x Ost, y Nord, z Höhe).

    Rahmenkoordinaten ``p`` (glTF, +Y oben, −Z Plan-Nord) liegen im Bezugssystem bei
    ``R + Ry(−θ) · p`` (``georeferenced-placement.ts``: ``Ry(+θ)`` dreht Gitterachsen in die
    Planachsen, θ = Nordwinkel, im Uhrzeigersinn von oben positiv). Nach dem Achsentausch des
    glTF-Imports ist ``Ry(φ)`` die Drehung um +Z um φ — also: verschieben um (Ost, Nord, Höhe),
    drehen um +Z um ``−θ``. Ohne Bezug: Ursprung, ohne Drehung.
    """
    if origin is None:
        return {"location": (0.0, 0.0, 0.0), "rotationZ": 0.0}
    return {
        "location": (origin["east"], origin["north"], origin["height"]),
        "rotationZ": -math.radians(origin["northAngleDeg"]),
    }


# --------------------------------------------------------------------------
# Instanzen und Kameraknoten (RTX-RH-005)
# --------------------------------------------------------------------------


def export_instances(document: dict) -> list[dict]:
    """Die Wurzelknoten der Instanzen eines Exports, in der Reihenfolge der Szene.

    ``{"node", "name", "instanceId", "role"}`` je Wurzelknoten mit ``extras.rendertaxi.instance``; ``name`` ist
    der Knotenname (leer, wenn er fehlt). Ein Wurzelknoten ohne diese Angabe ist keine Instanz (Kameras).
    """
    nodes = document.get("nodes") or [] if isinstance(document, dict) else []
    found = []
    for index in glb._roots(document):
        node = nodes[index] if isinstance(index, int) and 0 <= index < len(nodes) else None
        extras = node.get("extras") if isinstance(node, dict) else None
        own = extras.get(glb.CAMERA_EXTRAS_KEY) if isinstance(extras, dict) else None
        instance = own.get("instance") if isinstance(own, dict) else None
        if not isinstance(instance, dict):
            continue
        found.append({"node": index, "name": str(node.get("name") or ""),
                      "instanceId": str(instance.get("id") or ""), "role": str(instance.get("role") or "")})
    return found


def _normalized(vector) -> tuple[float, float, float] | None:
    length = math.sqrt(sum(c * c for c in vector))
    if not length or not math.isfinite(length):
        return None
    return tuple(c / length for c in vector)


def camera_poses(document: dict) -> list[dict]:
    """Jeder Kameraknoten der Szene mit seiner Weltlage im Exportraum (Meter, +Y oben).

    ``{"node", "camera", "name", "position", "direction", "up", "definition"}``: ``direction`` ist die −Z-Achse,
    ``up`` die +Y-Achse der Weltmatrix (glTF: die Kamera blickt entlang ihres −Z), beide normiert;
    ``camera`` der Index der Definition. Knoten mit unbrauchbarer Matrix oder Definition fehlen.
    """
    nodes = document.get("nodes") or [] if isinstance(document, dict) else []
    definitions = document.get("cameras") or [] if isinstance(document, dict) else []
    poses = []
    stack = [(index, glb._IDENTITY) for index in glb._roots(document)]
    while stack:
        index, parent = stack.pop()
        node = nodes[index] if isinstance(index, int) and 0 <= index < len(nodes) else None
        if not isinstance(node, dict):
            continue
        world = glb._compose(parent, glb._local(node))
        camera = node.get("camera")
        if isinstance(camera, int) and not isinstance(camera, bool) and 0 <= camera < len(definitions):
            direction = _normalized((-world[2], -world[6], -world[10]))
            up = _normalized((world[1], world[5], world[9]))
            position = (world[3], world[7], world[11])
            if direction is not None and up is not None and all(math.isfinite(c) for c in position):
                poses.append({"node": index, "camera": camera, "name": str(node.get("name") or ""),
                              "position": position, "direction": direction, "up": up,
                              "definition": definitions[camera]})
        stack.extend((child, world) for child in node.get("children", []))
    return sorted(poses, key=lambda pose: pose["node"])
