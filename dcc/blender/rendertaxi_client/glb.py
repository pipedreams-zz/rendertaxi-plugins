"""GLB-Dateien lesen, zählen und vermessen — für jeden Modellweg (Capture-Manifest 1.2.0).

Die eine Stelle, an der ein Plugin eine exportierte GLB-Datei **vor** dem
Senden liest: Kopf und JSON-Chunk, Dreiecke wie der Server sie zählt
(``packages/modules/assets/src/domain/glb.ts``), Verweise nach außen, die
Grenzen der Szene im Exportraum. Blender (``integrations/blender/addon/export.py``)
und Cinema 4D (``integrations/cinema-4d/plugin/rendertaxi_c4d/export.py``)
benutzen genau diese Funktionen — eine zweite Zählung in einem Plugin wäre ein
zweiter Weg zu derselben Grenze.

Zwei Schreibzugriffe, beide nur auf den JSON-Chunk, der Binärteil bleibt
unverändert (``_write``): ``scale_scene`` hängt die Wurzeln der Szene unter
einen Knoten mit gleichförmigem Maßstab — das braucht Cinema 4D, dessen
glTF-Exporter seinen Maßstab nicht über die Python-API preisgibt (QC-02).
``set_camera_extras`` schreibt Objektiv und Shift je Kamera in
``cameras[i].extras.rendertaxi.camera`` (RTX-B-004) — glTF selbst kennt
beides nicht. Format: ``integrations/_shared/contracts/v1/gltf-camera-extras.schema.json``,
``capture-manifest.md`` Abschnitt 11.5.
"""

from __future__ import annotations

import json
import math
import struct

MODEL_PATH = "model/scene.glb"

# Die Grenze des Servers (``GLB_LIMITS.maxTriangles`` in
# ``packages/modules/assets/src/domain/glb.ts``). Der Handshake nennt keine
# Dreiecksgrenze, nur ``limits.maxGeometryBytes``.
MAX_TRIANGLES = 50_000_000

_MAGIC = b"glTF"
_JSON = 0x4E4F534A


class GlbError(RuntimeError):
    """Die Datei ist keine lesbare GLB nach glTF 2.0 — lesbar, ohne Pfad."""


def _read(path: str) -> tuple[dict, bytes]:
    """``(JSON-Dokument, alles hinter dem JSON-Chunk)``."""
    with open(path, "rb") as handle:
        head = handle.read(20)
        if len(head) < 20 or head[:4] != _MAGIC:
            raise GlbError("Der Export hat keine GLB-Datei erzeugt.")
        version, _total, chunk_length, chunk_type = struct.unpack("<IIII", head[4:20])
        if version != 2 or chunk_type != _JSON:
            raise GlbError("Der Export hat keine GLB-Datei nach glTF 2.0 erzeugt.")
        raw = handle.read(chunk_length)
        rest = handle.read()
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise GlbError("Der JSON-Teil der GLB-Datei ist nicht lesbar.") from None
    if not isinstance(document, dict):
        raise GlbError("Der JSON-Teil der GLB-Datei ist kein Objekt.")
    return document, rest


def read_glb_json(path: str) -> dict:
    """Den JSON-Chunk einer GLB-Datei — oder ``GlbError``."""
    return _read(path)[0]


def _roots(document: dict) -> list:
    scenes = document.get("scenes") or []
    if not scenes:
        return []
    index = document.get("scene", 0)
    scene = scenes[index] if isinstance(index, int) and 0 <= index < len(scenes) else scenes[0]
    return list((scene or {}).get("nodes", []))


def glb_triangles(document: dict) -> int:
    """Dreiecke über alle Instanzen der Szene — so, wie der Server sie zählt."""
    nodes = document.get("nodes") or []
    meshes = document.get("meshes") or []
    accessors = document.get("accessors") or []

    def primitive_triangles(primitive: dict) -> int:
        mode = primitive.get("mode", 4)
        source = primitive.get("indices")
        if source is None:
            source = (primitive.get("attributes") or {}).get("POSITION")
        count = accessors[source].get("count", 0) if isinstance(source, int) and source < len(accessors) else 0
        if mode == 4:
            return count // 3
        if mode in (5, 6):
            return max(count - 2, 0)
        return 0

    total = 0
    stack = _roots(document)
    while stack:
        node = nodes[stack.pop()]
        mesh = node.get("mesh")
        if isinstance(mesh, int) and mesh < len(meshes):
            total += sum(primitive_triangles(p) for p in meshes[mesh].get("primitives", []))
        stack.extend(node.get("children", []))
    return total


def external_references(document: dict) -> bool:
    """Verweist die Datei über ``uri`` nach außen? Der Server nimmt das nicht an."""
    return any("uri" in entry for key in ("buffers", "images") for entry in document.get(key) or [])


# --------------------------------------------------------------------------
# Grenzen der Szene
# --------------------------------------------------------------------------

_IDENTITY = (1.0, 0.0, 0.0, 0.0,  0.0, 1.0, 0.0, 0.0,  0.0, 0.0, 1.0, 0.0)


def _local(node: dict) -> tuple:
    """Die lokale Transformation als 3×4 zeilenweise: ``matrix`` (spaltenweise in glTF) oder ``T · R · S``."""
    matrix = node.get("matrix")
    if matrix is not None:
        m = [float(v) for v in matrix]
        return (m[0], m[4], m[8], m[12], m[1], m[5], m[9], m[13], m[2], m[6], m[10], m[14])
    tx, ty, tz = (float(v) for v in node.get("translation", (0, 0, 0)))
    qx, qy, qz, qw = (float(v) for v in node.get("rotation", (0, 0, 0, 1)))
    sx, sy, sz = (float(v) for v in node.get("scale", (1, 1, 1)))
    r = (
        1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy - qz * qw), 2 * (qx * qz + qy * qw),
        2 * (qx * qy + qz * qw), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz - qx * qw),
        2 * (qx * qz - qy * qw), 2 * (qy * qz + qx * qw), 1 - 2 * (qx * qx + qy * qy),
    )
    return (r[0] * sx, r[1] * sy, r[2] * sz, tx,
            r[3] * sx, r[4] * sy, r[5] * sz, ty,
            r[6] * sx, r[7] * sy, r[8] * sz, tz)


def _compose(a: tuple, b: tuple) -> tuple:
    """``a · b`` für zwei affine 3×4-Matrizen."""
    out = []
    for row in range(3):
        ar = a[row * 4:row * 4 + 4]
        for col in range(3):
            out.append(ar[0] * b[col] + ar[1] * b[4 + col] + ar[2] * b[8 + col])
        out.append(ar[0] * b[3] + ar[1] * b[7] + ar[2] * b[11] + ar[3])
    return tuple(out)


def _apply(m: tuple, p) -> tuple:
    return (m[0] * p[0] + m[1] * p[1] + m[2] * p[2] + m[3],
            m[4] * p[0] + m[5] * p[1] + m[6] * p[2] + m[7],
            m[8] * p[0] + m[9] * p[1] + m[10] * p[2] + m[11])


def world_bounds(document: dict) -> tuple[list, list] | None:
    """``(min, max)`` der Szene im Exportraum — oder ``None`` ohne Geometrie.

    Wie der Server: je Primitiv die acht Ecken aus ``min``/``max`` des
    ``POSITION``-Accessors (glTF 2.0 verlangt beide), mit der Weltmatrix des
    Knotens abgebildet. Für Knoten ohne Drehung ist das genau die Hülle der
    Punkte; mit Drehung eine Hülle um sie.
    """
    nodes = document.get("nodes") or []
    meshes = document.get("meshes") or []
    accessors = document.get("accessors") or []
    low = [math.inf] * 3
    high = [-math.inf] * 3
    stack = [(index, _IDENTITY) for index in _roots(document)]
    while stack:
        index, parent = stack.pop()
        node = nodes[index]
        world = _compose(parent, _local(node))
        mesh = node.get("mesh")
        if isinstance(mesh, int) and mesh < len(meshes):
            for primitive in meshes[mesh].get("primitives", []):
                source = (primitive.get("attributes") or {}).get("POSITION")
                if not isinstance(source, int) or source >= len(accessors):
                    continue
                accessor = accessors[source]
                lo, hi = accessor.get("min"), accessor.get("max")
                if not (isinstance(lo, list) and isinstance(hi, list) and len(lo) == 3 and len(hi) == 3):
                    raise GlbError("Ein POSITION-Accessor der GLB-Datei nennt kein min/max.")
                for corner in ((x, y, z) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])):
                    point = _apply(world, corner)
                    for axis in range(3):
                        low[axis] = min(low[axis], point[axis])
                        high[axis] = max(high[axis], point[axis])
        stack.extend((child, world) for child in node.get("children", []))
    if low[0] == math.inf:
        return None
    return low, high


def scale_scene(path: str, factor: float) -> None:
    """Die Szene gleichförmig um ``factor`` skalieren: ein neuer Wurzelknoten, der Binärteil bleibt.

    Nur der JSON-Chunk wird neu geschrieben (auf vier Bytes mit Leerzeichen
    aufgefüllt, wie glTF 2.0 es verlangt); Accessoren, Puffer und Dreiecke
    bleiben unverändert.
    """
    if not (factor > 0 and math.isfinite(factor)):
        raise GlbError("Der Maßstab des Modells ist keine positive Zahl.")
    document, rest = _read(path)
    scenes = document.get("scenes") or []
    if not scenes:
        raise GlbError("Die GLB-Datei hat keine Szene.")
    index = document.get("scene", 0)
    scene = scenes[index if isinstance(index, int) and 0 <= index < len(scenes) else 0]
    nodes = document.setdefault("nodes", [])
    nodes.append({"name": "rendertaxi-meter", "scale": [factor, factor, factor],
                  "children": list(scene.get("nodes", []))})
    scene["nodes"] = [len(nodes) - 1]
    _write(path, document, rest)


def _write(path: str, document: dict, rest: bytes) -> None:
    """Den JSON-Chunk neu schreiben (auf vier Bytes mit Leerzeichen aufgefüllt), den Rest dahinter."""
    raw = json.dumps(document, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    raw += b" " * (-len(raw) % 4)
    total = 12 + 8 + len(raw) + len(rest)
    with open(path, "wb") as handle:
        handle.write(_MAGIC + struct.pack("<II", 2, total))
        handle.write(struct.pack("<II", len(raw), _JSON))
        handle.write(raw)
        handle.write(rest)


# --------------------------------------------------------------------------
# Objektiv und Shift je Kamera (RTX-B-004)
# --------------------------------------------------------------------------

# Der Schlüssel unter ``extras`` einer glTF-Kameradefinition und der Teil darin.
CAMERA_EXTRAS_KEY = "rendertaxi"
CAMERA_EXTRAS_PART = "camera"

# Dieselben Grenzen wie ``camera.lens`` und ``camera.shift`` im Capture-Manifest 1.4.0 (Abschnitt 11.3).
FOCAL_LENGTH_MM = (1.0, 1200.0)
SENSOR_MM = (1.0, 300.0)
SHIFT_LIMIT = 2.0
SENSOR_FITS = ("auto", "horizontal", "vertical")


def _finite_in(value, low: float, high: float) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
            and low <= value <= high)


def camera_extras(lens: dict | None, shift: dict | None) -> dict | None:
    """``{"lens": …, "shift": …}`` für ``extras.rendertaxi.camera`` — ``None``, wenn nichts zu sagen ist.

    ``lens`` und ``shift`` haben die Form des Manifests (1.4.0): ``lens`` mit
    ``focalLengthMm``, ``sensorWidthMm``, ``sensorFit``; ``shift`` mit ``x``,
    ``y`` als Anteil der längeren Bildseite. Ein Wert außerhalb der Grenzen
    lässt seinen Teil weg, statt eine Datei zu schreiben, die der Leser
    verwerfen müsste; ein Shift ``(0, 0)`` entfällt.
    """
    block: dict = {}
    if lens is not None and (_finite_in(lens.get("focalLengthMm"), *FOCAL_LENGTH_MM)
                             and _finite_in(lens.get("sensorWidthMm"), *SENSOR_MM)
                             and lens.get("sensorFit") in SENSOR_FITS):
        block["lens"] = {"focalLengthMm": lens["focalLengthMm"], "sensorWidthMm": lens["sensorWidthMm"],
                         "sensorFit": lens["sensorFit"]}
    if shift is not None and all(_finite_in(shift.get(axis), -SHIFT_LIMIT, SHIFT_LIMIT) for axis in "xy"):
        if shift["x"] != 0 or shift["y"] != 0:
            block["shift"] = {"x": shift["x"], "y": shift["y"]}
    return block or None


def set_camera_extras(path: str, by_node_name: dict[str, dict]) -> int:
    """Objektiv und Shift in die Kameradefinitionen der Knoten schreiben — die Zahl der beschriebenen.

    ``by_node_name`` ordnet dem Namen eines Knotens mit Kamera den Block aus
    ``camera_extras`` zu. Teilen sich mehrere Knoten eine Definition und
    nennen verschiedene Werte, bleibt sie ohne Angabe — lieber Shift 0 als der
    Shift einer anderen Kamera. Vorhandene ``extras`` anderer Schlüssel bleiben.
    """
    document, rest = _read(path)
    cameras = document.get("cameras")
    nodes = document.get("nodes")
    if not isinstance(cameras, list) or not isinstance(nodes, list):
        return 0
    wanted: dict[int, dict | None] = {}
    for node in nodes:
        if not isinstance(node, dict) or node.get("name") not in by_node_name:
            continue
        index = node.get("camera")
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(cameras):
            continue
        block = by_node_name[node["name"]]
        if index in wanted and wanted[index] != block:
            wanted[index] = None
        else:
            wanted[index] = block
    written = 0
    for index, block in sorted(wanted.items()):
        definition = cameras[index]
        if block is None or not isinstance(definition, dict):
            continue
        extras = definition.get("extras")
        extras = dict(extras) if isinstance(extras, dict) else {}
        extras[CAMERA_EXTRAS_KEY] = {CAMERA_EXTRAS_PART: block}
        definition["extras"] = extras
        written += 1
    if written:
        _write(path, document, rest)
    return written


def read_camera_extras(definition) -> dict | None:
    """``extras.rendertaxi.camera`` einer Kameradefinition, geprüft wie im Browser — ``None`` ohne."""
    if not isinstance(definition, dict):
        return None
    extras = definition.get("extras")
    part = extras.get(CAMERA_EXTRAS_KEY) if isinstance(extras, dict) else None
    block = part.get(CAMERA_EXTRAS_PART) if isinstance(part, dict) else None
    if not isinstance(block, dict):
        return None
    lens = block.get("lens")
    shift = block.get("shift")
    return camera_extras(lens if isinstance(lens, dict) else None, shift if isinstance(shift, dict) else None)
