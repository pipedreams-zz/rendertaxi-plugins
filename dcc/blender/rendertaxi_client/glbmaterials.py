"""Die eine Materialregel für jede exportierte GLB-Datei (RTX-P-021, #526).

Was ein glTF-Exporter eines Hosts schreibt, bringt ``apply_material_rule`` auf
dieselbe Form — Vertrag: ``integrations/_shared/docs/capture-manifest.md``,
Abschnitt 11.6:

* **Basisfarbe immer**, dazu höchstens eine **Diffustextur** (``baseColorTexture``,
  UV-Satz 0) und die **Transparenz als ``alphaMode``**. Keine weiteren Kanäle:
  Normal-, Occlusion-, Emissions- und Metallic-Roughness-Texturen, die
  ``KHR_materials_*``-Erweiterungen und Vertexfarben, Tangenten und weitere
  UV-Sätze fallen weg. Metallic und Roughness bleiben als Zahlen, damit eine
  Fläche nicht zum Metall wird (glTF setzt ``metallicFactor`` sonst auf 1).
* **Eine Textur höchstens 2 MB** (``MAX_TEXTURE_BYTES``). Eine größere wird
  verkleinert, nicht weggelassen — über ``scale_image`` des Hosts, denn die
  Standardbibliothek dekodiert kein PNG oder JPEG. Nur wenn auch das nicht
  gelingt, bleibt das Material mit seiner Farbe ohne Textur (``dropped``).
* **Zusammenführen** (``merge``): ``MERGE_BY_MATERIAL`` (der Standard) legt je
  Materialname einen Knoten mit einem Mesh an, benannt wie das Material, die
  Geometrie in Weltkoordinaten (ein Primitiv je Fassung des Materials); ``KEEP_OBJECTS`` lässt die Knoten des Hosts
  (und damit ihre Namen) stehen.
* ``materials=False`` ist der Schalter „ohne Materialien“: keine Materialien,
  keine Texturen, keine UV — nach Material zusammengeführt ein einziger Knoten.

Die Datei wird als Ganzes neu geschrieben (Binärteil neu gepackt, nur was ein
Verweis braucht) und über ``glb._write`` ersetzt. Kameras, ihre Knoten und
``extras`` bleiben, ebenso ihre Lage in der Hierarchie.
"""

from __future__ import annotations

import array
import math
import struct
import sys

from . import glb
from .glb import GlbError

MAX_TEXTURE_BYTES = 2 * 1024 * 1024
MERGE_BY_MATERIAL = "material"
KEEP_OBJECTS = "objects"
MERGE_MODES = (MERGE_BY_MATERIAL, KEEP_OBJECTS)
# Der Knoten der Geometrie ohne Material, wenn nach Material zusammengeführt wird.
NO_MATERIAL_NAME = "ohne Material"
# Ab dieser Entfernung vom Ursprung (in Einheiten der Datei) bekommt ein zusammengeführter Knoten den
# Mittelpunkt der Szene als Verschiebung — float32 hält in 1 km Entfernung noch etwa 0,06 mm.
_FAR = 1000.0
_IMAGE_TYPES = ("image/png", "image/jpeg")

_BIN = 0x004E4942
_COMPONENTS = {5120: ("b", 1), 5121: ("B", 1), 5122: ("h", 2), 5123: ("H", 2), 5125: ("I", 4), 5126: ("f", 4)}
_NORMALIZE = {5120: 127.0, 5121: 255.0, 5122: 32767.0, 5123: 65535.0}
_WIDTH = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}
_ARRAY_BUFFER = 34962
_ELEMENT_ARRAY_BUFFER = 34963
_TEXTURE_TRANSFORM = "KHR_texture_transform"


class MaterialReport:
    """Was ``apply_material_rule`` geschrieben hat — für den Hinweis im Plugin und die Tests."""

    def __init__(self) -> None:
        self.materials = 0
        self.textures = 0
        self.downscaled: list[tuple[int, int]] = []  # (Bytes vorher, Bytes nachher) je verkleinerter Textur
        self.dropped = 0  # Texturen, die auch verkleinert nicht passten oder nicht lesbar waren
        self.objects = 0  # Knoten mit Geometrie
        self.merged = False

    def as_dict(self) -> dict:
        return {"materials": self.materials, "textures": self.textures,
                "downscaled": [list(pair) for pair in self.downscaled], "dropped": self.dropped,
                "objects": self.objects, "merged": self.merged}


# --------------------------------------------------------------------------
# Lesen
# --------------------------------------------------------------------------


def _split(rest: bytes) -> bytes:
    """Der BIN-Chunk hinter dem JSON-Chunk — leer, wenn es keinen gibt."""
    if len(rest) < 8:
        return b""
    length, kind = struct.unpack("<II", rest[:8])
    if kind != _BIN:
        return b""
    return rest[8:8 + length]


def _flip_bytes(data: array.array) -> None:
    if sys.byteorder != "little" and data.itemsize > 1:
        data.byteswap()


class _Source:
    """Die Accessoren der gelesenen Datei, jeder höchstens einmal dekodiert."""

    def __init__(self, document: dict, binary: bytes) -> None:
        self.document = document
        self.binary = binary
        self.accessors = document.get("accessors") or []
        self.views = document.get("bufferViews") or []
        self._cache: dict[int, tuple[array.array, int]] = {}
        buffers = document.get("buffers") or []
        if any("uri" in entry for entry in buffers):
            raise GlbError("Die GLB-Datei verweist auf Daten außerhalb ihrer selbst.")
        if "KHR_draco_mesh_compression" in (document.get("extensionsUsed") or []):
            raise GlbError("Die GLB-Datei ist mit Draco komprimiert — der Export muss ohne Draco laufen.")

    def view_bytes(self, index: int) -> bytes:
        if not isinstance(index, int) or not 0 <= index < len(self.views):
            raise GlbError("Ein bufferView der GLB-Datei fehlt.")
        view = self.views[index]
        if view.get("buffer", 0) != 0:
            raise GlbError("Die GLB-Datei hat mehr als einen Puffer.")
        start = view.get("byteOffset", 0)
        end = start + view["byteLength"]
        if end > len(self.binary):
            raise GlbError("Ein bufferView der GLB-Datei reicht über den Binärteil hinaus.")
        return self.binary[start:end]

    def read(self, index) -> tuple[array.array, int]:
        """``(Werte, Breite)`` eines Accessors — Zahlen flach hintereinander, normierte Ganzzahlen als float."""
        if not isinstance(index, int) or not 0 <= index < len(self.accessors):
            raise GlbError("Ein Accessor der GLB-Datei fehlt.")
        if index in self._cache:
            return self._cache[index]
        accessor = self.accessors[index]
        kind = accessor.get("componentType")
        width = _WIDTH.get(accessor.get("type"))
        if kind not in _COMPONENTS or width is None:
            raise GlbError("Ein Accessor der GLB-Datei hat einen unbekannten Typ.")
        code, size = _COMPONENTS[kind]
        count = int(accessor.get("count", 0))
        element = size * width
        values = array.array(code)
        if "bufferView" in accessor:
            data = self.view_bytes(accessor["bufferView"])
            stride = self.views[accessor["bufferView"]].get("byteStride") or element
            base = accessor.get("byteOffset", 0)
            if base + (count - 1) * stride + element > len(data) and count:
                raise GlbError("Ein Accessor der GLB-Datei reicht über seinen bufferView hinaus.")
            if stride == element:
                values.frombytes(data[base:base + count * element])
            else:
                for item in range(count):
                    start = base + item * stride
                    values.frombytes(data[start:start + element])
            _flip_bytes(values)
        else:
            values.extend([0] * (count * width))
        sparse = accessor.get("sparse")
        if sparse:
            positions = array.array(_COMPONENTS[sparse["indices"]["componentType"]][0])
            positions.frombytes(self.view_bytes(sparse["indices"]["bufferView"])[
                sparse["indices"].get("byteOffset", 0):][:sparse["count"] * positions.itemsize])
            replacement = array.array(code)
            replacement.frombytes(self.view_bytes(sparse["values"]["bufferView"])[
                sparse["values"].get("byteOffset", 0):][:sparse["count"] * element])
            _flip_bytes(positions)
            _flip_bytes(replacement)
            for slot, target in enumerate(positions):
                values[target * width:(target + 1) * width] = replacement[slot * width:(slot + 1) * width]
        if code != "f" and accessor.get("normalized"):
            scale = _NORMALIZE[kind]
            values = array.array("f", (max(value / scale, -1.0) for value in values))
        self._cache[index] = (values, width)
        return values, width


def _triangles(source: _Source, primitive: dict, count: int) -> list | None:
    """Die Dreiecksindizes eines Primitivs (Listen, Streifen, Fächer) — ``None`` für Punkte und Linien."""
    mode = primitive.get("mode", 4)
    if mode not in (4, 5, 6):
        return None
    if "indices" in primitive:
        indices = list(source.read(primitive["indices"])[0])
    else:
        indices = list(range(count))
    if mode == 4:
        return indices[:len(indices) - len(indices) % 3]
    out = []
    for i in range(len(indices) - 2):
        if mode == 5:
            a, b, c = (indices[i], indices[i + 1], indices[i + 2]) if i % 2 == 0 else (
                indices[i + 1], indices[i], indices[i + 2])
        else:
            a, b, c = indices[0], indices[i + 1], indices[i + 2]
        out.extend((a, b, c))
    return out


# --------------------------------------------------------------------------
# Materialien
# --------------------------------------------------------------------------


def _texture_info(info) -> dict | None:
    """``baseColorTexture`` mit UV-Satz 0 — andere UV-Sätze schreibt die Regel nicht."""
    if not isinstance(info, dict) or not isinstance(info.get("index"), int) or info.get("texCoord", 0) != 0:
        return None
    out = {"index": info["index"]}
    transform = (info.get("extensions") or {}).get(_TEXTURE_TRANSFORM)
    if isinstance(transform, dict):
        transform = {key: value for key, value in transform.items() if key != "texCoord"}
        out["extensions"] = {_TEXTURE_TRANSFORM: transform}
    return out


def _reduced(material: dict, number: int) -> tuple[dict, dict | None]:
    """``(Material nach der Regel ohne Textur, baseColorTexture oder None)``."""
    pbr = material.get("pbrMetallicRoughness") or {}
    factor = pbr.get("baseColorFactor")
    if not (isinstance(factor, list) and len(factor) == 4 and all(isinstance(v, (int, float)) for v in factor)):
        factor = [1.0, 1.0, 1.0, 1.0]
    out: dict = {"name": str(material.get("name") or f"Material {number}")}
    reduced_pbr: dict = {"baseColorFactor": [float(v) for v in factor]}
    for key in ("metallicFactor", "roughnessFactor"):
        if isinstance(pbr.get(key), (int, float)):
            reduced_pbr[key] = float(pbr[key])
    out["pbrMetallicRoughness"] = reduced_pbr
    mode = material.get("alphaMode")
    if mode not in ("OPAQUE", "MASK", "BLEND"):
        mode = "BLEND" if factor[3] < 0.999 else None
    if mode and mode != "OPAQUE":
        out["alphaMode"] = mode
        if mode == "MASK" and isinstance(material.get("alphaCutoff"), (int, float)):
            out["alphaCutoff"] = float(material["alphaCutoff"])
    if material.get("doubleSided"):
        out["doubleSided"] = True
    return out, _texture_info(pbr.get("baseColorTexture"))


def report_note(report: "MaterialReport", materials: bool) -> str:
    """Der Teil der Notiz ``note`` des Modell-Assets zu Materialien und Zusammenführung — ohne Namen aus der Szene."""
    if not materials:
        text = "ohne Materialien"
    else:
        text = f"Materialien: {report.materials}, {report.textures} mit Textur"
        if report.downscaled:
            text += f", {len(report.downscaled)} auf höchstens 2 MB verkleinert"
        if report.dropped:
            text += f", {report.dropped} Textur(en) nicht lesbar, nur die Farbe"
    merged = "nach Material zusammengeführt" if report.merged else "Objekte behalten"
    return f"{text}; {merged} ({report.objects} {'Objekt' if report.objects == 1 else 'Objekte'})"


def shrink_image(data: bytes, mime: str, scale_image, max_bytes: int = MAX_TEXTURE_BYTES):
    """``(Bytes, mimeType)`` höchstens ``max_bytes`` groß — oder ``None``, wenn es nicht gelingt.

    ``scale_image(data, mime, factor)`` ist der Teil des Hosts: das Bild um
    ``factor`` (< 1, je Seite) verkleinert neu kodieren, als ``(Bytes, mimeType)``
    oder ``None``. Der erste Versuch rechnet mit der Wurzel des Größenverhältnisses
    (die Bytes wachsen etwa mit der Fläche), jeder weitere verkleinert vom
    Original aus weiter, höchstens acht Versuche.
    """
    if scale_image is None:
        return None
    factor = 1.0
    size = len(data)
    for _attempt in range(8):
        factor *= min(0.95, max(0.25, math.sqrt(max_bytes / size) * 0.95))
        try:
            result = scale_image(data, mime, factor)
        except Exception:  # noqa: BLE001 — ein Bild, das der Host nicht lesen kann, bleibt weg
            return None
        if not result or not result[0]:
            return None
        if len(result[0]) <= max_bytes:
            return bytes(result[0]), str(result[1])
        size = len(result[0])
    return None


# --------------------------------------------------------------------------
# Schreiben
# --------------------------------------------------------------------------


class _Writer:
    """Accessoren, bufferViews und Bilder der neuen Datei in einem Binärteil."""

    def __init__(self) -> None:
        self.binary = bytearray()
        self.views: list[dict] = []
        self.accessors: list[dict] = []

    def view(self, data: bytes, target: int | None = None) -> int:
        self.binary.extend(b"\0" * (-len(self.binary) % 4))
        view = {"buffer": 0, "byteOffset": len(self.binary), "byteLength": len(data)}
        if target is not None:
            view["target"] = target
        self.binary.extend(data)
        self.views.append(view)
        return len(self.views) - 1

    def floats(self, values, width: int, bounds: bool = False) -> int:
        data = values if isinstance(values, array.array) and values.typecode == "f" else array.array("f", values)
        packed = array.array("f", data)
        _flip_bytes(packed)
        accessor = {"bufferView": self.view(packed.tobytes(), _ARRAY_BUFFER), "componentType": 5126,
                    "count": len(data) // width, "type": {2: "VEC2", 3: "VEC3", 4: "VEC4"}[width]}
        if bounds and len(data):
            accessor["min"] = [min(data[axis::width]) for axis in range(width)]
            accessor["max"] = [max(data[axis::width]) for axis in range(width)]
        self.accessors.append(accessor)
        return len(self.accessors) - 1

    def indices(self, values) -> int:
        code, kind = ("H", 5123) if (max(values) if values else 0) < 65535 else ("I", 5125)
        packed = array.array(code, values)
        _flip_bytes(packed)
        self.accessors.append({"bufferView": self.view(packed.tobytes(), _ELEMENT_ARRAY_BUFFER),
                               "componentType": kind, "count": len(values), "type": "SCALAR"})
        return len(self.accessors) - 1


def _normal_matrix(m: tuple) -> tuple:
    """Die Kofaktormatrix des linearen Teils mit dem Vorzeichen der Determinante — die Inverse-Transponierte bis
    auf einen positiven Faktor: sie bildet Normalen ab, auch bei ungleichem Maßstab und gespiegelt."""
    a, b, c, d, e, f, g, h, i = m[0], m[1], m[2], m[4], m[5], m[6], m[8], m[9], m[10]
    sign = -1.0 if _determinant(m) < 0 else 1.0
    return tuple(sign * value for value in (e * i - f * h, f * g - d * i, d * h - e * g,
            c * h - b * i, a * i - c * g, b * g - a * h,
            b * f - c * e, c * d - a * f, a * e - b * d))


def _determinant(m: tuple) -> float:
    return (m[0] * (m[5] * m[10] - m[6] * m[9]) - m[1] * (m[4] * m[10] - m[6] * m[8])
            + m[2] * (m[4] * m[9] - m[5] * m[8]))


def _vertex_normals(positions, triangles) -> list:
    """Normalen je Punkt aus den Flächen gemittelt — für ein Primitiv ohne ``NORMAL`` in einem gemeinsamen Mesh."""
    normals = [0.0] * len(positions)
    for t in range(0, len(triangles), 3):
        a, b, c = (triangles[t + k] * 3 for k in range(3))
        ux, uy, uz = (positions[b + k] - positions[a + k] for k in range(3))
        vx, vy, vz = (positions[c + k] - positions[a + k] for k in range(3))
        n = (uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx)
        for corner in (a, b, c):
            for k in range(3):
                normals[corner + k] += n[k]
    for v in range(0, len(normals), 3):
        length = math.sqrt(normals[v] ** 2 + normals[v + 1] ** 2 + normals[v + 2] ** 2) or 1.0
        normals[v], normals[v + 1], normals[v + 2] = (normals[v + k] / length for k in range(3))
    return normals


class _Group:
    """Die Geometrie eines Materials in Weltkoordinaten (minus ``offset``)."""

    def __init__(self) -> None:
        self.positions: list = []
        self.normals: list = []
        self.uvs: list = []
        self.triangles: list = []

    def add(self, source: _Source, primitive: dict, world: tuple, offset, textured: bool) -> bool:
        attributes = primitive.get("attributes") or {}
        if "POSITION" not in attributes:
            return False
        positions, width = source.read(attributes["POSITION"])
        count = len(positions) // width
        triangles = _triangles(source, primitive, count)
        if not triangles:
            return False
        if _determinant(world) < 0:
            for t in range(0, len(triangles), 3):
                triangles[t + 1], triangles[t + 2] = triangles[t + 2], triangles[t + 1]
        base = len(self.positions) // 3
        m = world
        ox, oy, oz = offset
        local = []
        for v in range(0, count * 3, 3):
            x, y, z = positions[v], positions[v + 1], positions[v + 2]
            local.extend((m[0] * x + m[1] * y + m[2] * z + m[3] - ox,
                          m[4] * x + m[5] * y + m[6] * z + m[7] - oy,
                          m[8] * x + m[9] * y + m[10] * z + m[11] - oz))
        if "NORMAL" in attributes:
            normals, _ = source.read(attributes["NORMAL"])
            n = _normal_matrix(world)
            out = []
            for v in range(0, count * 3, 3):
                x, y, z = normals[v], normals[v + 1], normals[v + 2]
                nx, ny, nz = n[0] * x + n[1] * y + n[2] * z, n[3] * x + n[4] * y + n[5] * z, n[6] * x + n[7] * y + n[8] * z
                length = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
                out.extend((nx / length, ny / length, nz / length))
        else:
            out = _vertex_normals(local, triangles)
        if textured:
            if "TEXCOORD_0" in attributes:
                uvs, uv_width = source.read(attributes["TEXCOORD_0"])
                self.uvs.extend(uvs[:count * 2] if uv_width == 2 else [0.0] * (count * 2))
            else:
                self.uvs.extend([0.0] * (count * 2))
        self.positions.extend(local)
        self.normals.extend(out)
        self.triangles.extend(index + base for index in triangles)
        return True


def patch_materials(path: str, *, textures: dict | None = None, alphas: dict | None = None,
                    unassigned: tuple = ()) -> int:
    """Nachtragen, was ein Exporter nicht schreibt — die Zahl der geänderten Materialien.

    Für Rhino 8.35 (gemessen 14.10.2026): mit Bitmaps im kopflosen Dokument stürzt sein glTF-Exporter bei mehreren
    Objekten ab, die Transparenz eines Materials schreibt er nicht, und ein Objekt ohne Material bekommt ein
    eigenes „Default Material“. Er schreibt deshalb nur Farbe und ``TEXCOORD_0``, und das Plugin trägt nach:

    * ``textures``: Materialname → ``(Bytes, mimeType)`` als ``baseColorTexture``, die Basisfarbe dann weiß
      (die Bitmap ersetzt die Farbe wie in Rhino, glTF multipliziert beide);
    * ``alphas``: Materialname → Deckkraft in [0, 1] als Alpha der Basisfarbe (``alphaMode`` setzt die Regel);
    * ``unassigned``: Namen von Materialien, die der Exporter selbst anlegt — ihre Primitive stehen danach
      ohne Material.

    Das Bild steht am Ende des Binärteils; ``apply_material_rule`` packt danach ohnehin neu.
    """
    textures = textures or {}
    alphas = alphas or {}
    document, rest = glb._read(path)
    binary = bytearray(_split(rest))
    changed = 0
    slots: dict = {}
    dropped = set()
    for index, material in enumerate(document.get("materials") or []):
        name = material.get("name") if isinstance(material, dict) else None
        if name in unassigned:
            dropped.add(index)
            continue
        pbr = material.setdefault("pbrMetallicRoughness", {}) if name in textures or name in alphas else None
        if pbr is None:
            continue
        factor = pbr.get("baseColorFactor")
        factor = [float(v) for v in factor] if isinstance(factor, list) and len(factor) == 4 else [1.0] * 4
        if name in textures and "baseColorTexture" not in pbr:
            if name not in slots:
                data, mime = textures[name]
                binary.extend(b"\0" * (-len(binary) % 4))
                views = document.setdefault("bufferViews", [])
                views.append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(data)})
                binary.extend(data)
                images = document.setdefault("images", [])
                images.append({"bufferView": len(views) - 1, "mimeType": mime})
                texture_list = document.setdefault("textures", [])
                texture_list.append({"source": len(images) - 1})
                slots[name] = len(texture_list) - 1
            pbr["baseColorTexture"] = {"index": slots[name]}
            factor[:3] = [1.0, 1.0, 1.0]
        if name in alphas:
            factor[3] = min(1.0, max(0.0, float(alphas[name])))
        pbr["baseColorFactor"] = factor
        changed += 1
    if dropped:
        for mesh in document.get("meshes") or []:
            for primitive in mesh.get("primitives", []):
                if primitive.get("material") in dropped:
                    del primitive["material"]
        changed += len(dropped)
    if changed:
        binary.extend(b"\0" * (-len(binary) % 4))
        if binary:
            buffers = document.setdefault("buffers", [{}])
            buffers[0] = {"byteLength": len(binary)}
        glb._write(path, document, struct.pack("<II", len(binary), _BIN) + bytes(binary) if binary else b"")
    return changed


def apply_material_rule(path: str, *, materials: bool = True, merge: str = MERGE_BY_MATERIAL,
                        scale_image=None, max_texture_bytes: int = MAX_TEXTURE_BYTES,
                        material_names: dict | None = None) -> MaterialReport:
    """Die Datei unter ``path`` nach der Materialregel neu schreiben (Abschnitt 11.6) — der Bericht.

    ``scale_image(data, mime, factor) -> (bytes, mime) | None`` verkleinert
    ein Bild im Host (``shrink_image``); ohne bleibt eine zu große Textur weg.
    ``material_names`` ordnet einem Namen in der Datei den Namen im Host zu —
    für einen Host, der eine zweite Fassung beim Export umbenennt (Cinema 4D:
    „Holz.1“ für „Holz“ ohne Textur).
    """
    if merge not in MERGE_MODES:
        raise ValueError(f"merge muss eines von {MERGE_MODES} sein")
    document, rest = glb._read(path)
    source = _Source(document, _split(rest))
    writer = _Writer()
    report = MaterialReport()

    # Materialien nach der Regel, Texturen höchstens max_texture_bytes groß.
    old_materials = document.get("materials") or [] if materials else []
    textures = document.get("textures") or []
    images = document.get("images") or []
    samplers = document.get("samplers") or []
    new_materials: list[dict] = []
    new_textures: list[dict] = []
    new_images: list[dict] = []
    new_samplers: list[dict] = []
    image_slots: dict[int, int | None] = {}
    sampler_slots: dict[int, int] = {}
    uses_transform = False

    def image_slot(index) -> int | None:
        if index in image_slots:
            return image_slots[index]
        slot = None
        image = images[index] if isinstance(index, int) and 0 <= index < len(images) else None
        if isinstance(image, dict) and "bufferView" in image and image.get("mimeType") in _IMAGE_TYPES:
            data, mime = source.view_bytes(image["bufferView"]), image["mimeType"]
            if len(data) > max_texture_bytes:
                before = len(data)
                shrunk = shrink_image(data, mime, scale_image, max_texture_bytes)
                data, mime = shrunk if shrunk else (None, None)
                if data is not None:
                    report.downscaled.append((before, len(data)))
            if data is not None:
                entry = {"bufferView": writer.view(data), "mimeType": mime}
                if image.get("name"):
                    entry["name"] = str(image["name"])
                new_images.append(entry)
                slot = len(new_images) - 1
        image_slots[index] = slot
        return slot

    for number, material in enumerate(old_materials, start=1):
        reduced, info = _reduced(material if isinstance(material, dict) else {}, number)
        if material_names and reduced["name"] in material_names:
            reduced["name"] = str(material_names[reduced["name"]])
        texture = textures[info["index"]] if info and 0 <= info["index"] < len(textures) else None
        if info is not None:
            slot = image_slot(texture.get("source")) if isinstance(texture, dict) else None
            if slot is None:
                report.dropped += 1
            else:
                entry: dict = {"source": slot}
                sampler = texture.get("sampler")
                if isinstance(sampler, int) and 0 <= sampler < len(samplers):
                    if sampler not in sampler_slots:
                        new_samplers.append(dict(samplers[sampler]))
                        sampler_slots[sampler] = len(new_samplers) - 1
                    entry["sampler"] = sampler_slots[sampler]
                new_textures.append(entry)
                info = dict(info, index=len(new_textures) - 1)
                reduced["pbrMetallicRoughness"]["baseColorTexture"] = info
                uses_transform = uses_transform or "extensions" in info
        new_materials.append(reduced)

    def textured(material) -> bool:
        return (isinstance(material, int) and 0 <= material < len(new_materials)
                and "baseColorTexture" in new_materials[material]["pbrMetallicRoughness"])

    def material_of(primitive: dict):
        index = primitive.get("material")
        return index if materials and isinstance(index, int) and 0 <= index < len(new_materials) else None

    nodes = document.get("nodes") or []
    meshes = document.get("meshes") or []
    roots = glb._roots(document)
    new_nodes: list[dict] = []
    new_meshes: list[dict] = []
    scene_roots: list[int] = []

    def copy_node(node: dict) -> dict:
        out = {key: node[key] for key in ("name", "camera", "matrix", "translation", "rotation", "scale", "extras")
               if key in node}
        return out

    if merge == MERGE_BY_MATERIAL:
        report.merged = True
        bounds = glb.world_bounds(document)
        offset = (0.0, 0.0, 0.0)
        if bounds and max(abs(v) for v in bounds[0] + bounds[1]) > _FAR:
            offset = tuple(round((bounds[0][k] + bounds[1][k]) / 2.0) for k in range(3))
        # Ein Knoten je Materialname: Exporter schreiben ein Material des Hosts mitunter in zwei Fassungen
        # (Blender ohne UV ohne Textur, Cinema 4D mit und ohne Bitmap) — dann ein Primitiv je Fassung.
        groups: dict = {}
        order: list = []
        stack = [(index, glb._IDENTITY) for index in reversed(roots)]
        while stack:
            index, parent = stack.pop()
            node = nodes[index]
            world = glb._compose(parent, glb._local(node))
            mesh = node.get("mesh")
            if isinstance(mesh, int) and 0 <= mesh < len(meshes):
                for primitive in meshes[mesh].get("primitives", []):
                    key = material_of(primitive)
                    name = new_materials[key]["name"] if key is not None else NO_MATERIAL_NAME
                    if name not in groups:
                        groups[name] = {}
                        order.append(name)
                    group = groups[name].get(key)
                    if group is None:
                        group = groups[name][key] = _Group()
                    group.add(source, primitive, world, offset, textured(key))
            stack.extend((child, world) for child in reversed(node.get("children", [])))
        for name in order:
            primitives = []
            for key, group in groups[name].items():
                if not group.triangles:
                    continue
                primitive = {"attributes": {"POSITION": writer.floats(group.positions, 3, bounds=True),
                                            "NORMAL": writer.floats(group.normals, 3)},
                             "indices": writer.indices(group.triangles)}
                if group.uvs:
                    primitive["attributes"]["TEXCOORD_0"] = writer.floats(group.uvs, 2)
                if key is not None:
                    primitive["material"] = key
                primitives.append(primitive)
            if not primitives:
                continue
            new_meshes.append({"name": name, "primitives": primitives})
            node = {"name": name, "mesh": len(new_meshes) - 1}
            if any(offset):
                node["translation"] = [float(v) for v in offset]
            new_nodes.append(node)
            scene_roots.append(len(new_nodes) - 1)
        report.objects = len(new_nodes)

        # Die Kameras mit ihren Vorfahren; alles andere war Geometrie und steht jetzt in den Materialknoten.
        def keep_cameras(index: int) -> int | None:
            node = nodes[index]
            children = [kept for kept in (keep_cameras(child) for child in node.get("children", []))
                        if kept is not None]
            if "camera" not in node and not children:
                return None
            out = copy_node(node)
            if children:
                out["children"] = children
            new_nodes.append(out)
            return len(new_nodes) - 1

        scene_roots.extend(kept for kept in (keep_cameras(index) for index in roots) if kept is not None)
    else:
        mesh_slots: dict[int, int | None] = {}
        written: dict = {}

        def accessor(index, kind: str):
            key = (index, kind)
            if key not in written:
                values, width = source.read(index)
                if kind == "POSITION":
                    written[key] = writer.floats(values, width, bounds=True)
                elif kind == "indices":
                    written[key] = writer.indices(values)
                else:
                    written[key] = writer.floats(values, width)
            return written[key]

        def mesh_slot(index: int) -> int | None:
            if index not in mesh_slots:
                primitives = []
                for primitive in meshes[index].get("primitives", []):
                    attributes = primitive.get("attributes") or {}
                    if "POSITION" not in attributes:
                        continue
                    key = material_of(primitive)
                    kept = {"POSITION": accessor(attributes["POSITION"], "POSITION")}
                    if "NORMAL" in attributes:
                        kept["NORMAL"] = accessor(attributes["NORMAL"], "NORMAL")
                    if textured(key):
                        if "TEXCOORD_0" in attributes:
                            kept["TEXCOORD_0"] = accessor(attributes["TEXCOORD_0"], "TEXCOORD_0")
                        else:  # Textur ohne UV: glTF verlangt den Satz, den die Textur nennt — alles auf (0, 0)
                            count = len(source.read(attributes["POSITION"])[0]) // 3
                            kept["TEXCOORD_0"] = writer.floats([0.0] * (count * 2), 2)
                    out = {"attributes": kept}
                    if "indices" in primitive:
                        out["indices"] = accessor(primitive["indices"], "indices")
                    if primitive.get("mode", 4) != 4:
                        out["mode"] = primitive["mode"]
                    if key is not None:
                        out["material"] = key
                    primitives.append(out)
                if primitives:
                    mesh = {"primitives": primitives}
                    if meshes[index].get("name"):
                        mesh["name"] = str(meshes[index]["name"])
                    new_meshes.append(mesh)
                    mesh_slots[index] = len(new_meshes) - 1
                else:
                    mesh_slots[index] = None
            return mesh_slots[index]

        slots: dict[int, int] = {}

        def keep_node(index: int) -> int:
            if index in slots:
                return slots[index]
            node = nodes[index]
            out = copy_node(node)
            mesh = node.get("mesh")
            if isinstance(mesh, int) and 0 <= mesh < len(meshes):
                slot = mesh_slot(mesh)
                if slot is not None:
                    out["mesh"] = slot
                    report.objects += 1
            new_nodes.append(out)
            slots[index] = len(new_nodes) - 1
            children = [keep_node(child) for child in node.get("children", [])]
            if children:
                new_nodes[slots[index]]["children"] = children
            return slots[index]

        scene_roots.extend(keep_node(index) for index in roots)

    # Nur Materialien, die ein Primitiv trägt (ein Exporter legt auch eigene an, Rhino „Default Material“).
    used = {primitive.get("material") for mesh in new_meshes for primitive in mesh["primitives"]}
    remap = {old: new for new, old in enumerate(index for index in range(len(new_materials)) if index in used)}
    if len(remap) != len(new_materials):
        for mesh in new_meshes:
            for primitive in mesh["primitives"]:
                if "material" in primitive:
                    primitive["material"] = remap[primitive["material"]]
        kept = [new_materials[index] for index in sorted(remap)]
        used = {remap[index] for index in used if index is not None} | ({None} if None in used else set())
        new_materials[:] = kept
    # Gezählt wird je Materialname wie die Knoten „nach Material“ — eine zweite Fassung ist kein zweites Material.
    report.materials = len({new_materials[index]["name"] for index in used if index is not None})
    report.textures = len({new_materials[index]["name"] for index in used if index is not None and textured(index)})

    out: dict = {"asset": dict(document.get("asset") or {"version": "2.0"})}
    out["asset"]["version"] = "2.0"
    if uses_transform:
        out["extensionsUsed"] = [_TEXTURE_TRANSFORM]
    out["scene"] = 0
    scene = dict((document.get("scenes") or [{}])[document.get("scene", 0) if isinstance(document.get("scene", 0), int)
                                                    and 0 <= document.get("scene", 0) < len(document.get("scenes") or [])
                                                    else 0] or {})
    scene = {key: value for key, value in scene.items() if key in ("name", "extras")}
    scene["nodes"] = scene_roots
    out["scenes"] = [scene]
    out["nodes"] = new_nodes
    for key, values in (("meshes", new_meshes), ("materials", new_materials), ("textures", new_textures),
                        ("images", new_images), ("samplers", new_samplers)):
        if values:
            out[key] = values
    if document.get("cameras"):
        out["cameras"] = document["cameras"]
    if writer.accessors:
        out["accessors"] = writer.accessors
    writer.binary.extend(b"\0" * (-len(writer.binary) % 4))
    if writer.views:
        out["bufferViews"] = writer.views
        out["buffers"] = [{"byteLength": len(writer.binary)}]
    if document.get("extras"):
        out["extras"] = document["extras"]
    tail = struct.pack("<II", len(writer.binary), _BIN) + bytes(writer.binary) if writer.binary else b""
    glb._write(path, out, tail)
    return report


def describe_glb(path: str) -> dict:
    """Was in der Datei steht — die Messprotokolle legen das neben ``gltf-transform inspect``.

    Knoten mit Geometrie (Name, Material, Dreiecke, UV ja/nein), Materialien
    (Basisfarbe, ``alphaMode``, Textur), Bilder (Typ, Bytes, Pixelmaß aus dem
    PNG- oder JPEG-Kopf) und die Gesamtgröße.
    """
    document, rest = glb._read(path)
    source = _Source(document, _split(rest))
    images = []
    for image in document.get("images") or []:
        data = source.view_bytes(image["bufferView"]) if "bufferView" in image else b""
        images.append({"mimeType": image.get("mimeType"), "bytes": len(data), "size": image_size(data)})
    materials = []
    for material in document.get("materials") or []:
        pbr = material.get("pbrMetallicRoughness") or {}
        texture = (pbr.get("baseColorTexture") or {}).get("index")
        channels = sorted(key for key in ("normalTexture", "occlusionTexture", "emissiveTexture") if key in material)
        if "metallicRoughnessTexture" in pbr:
            channels.append("metallicRoughnessTexture")
        materials.append({"name": material.get("name"), "baseColorFactor": pbr.get("baseColorFactor"),
                          "alphaMode": material.get("alphaMode", "OPAQUE"),
                          "baseColorImage": ((document.get("textures") or [])[texture].get("source")
                                             if isinstance(texture, int) else None),
                          "otherChannels": channels + sorted((material.get("extensions") or {}).keys())})
    meshes = document.get("meshes") or []
    all_materials = document.get("materials") or []

    def textured(primitive) -> bool:
        index = primitive.get("material")
        return (isinstance(index, int) and 0 <= index < len(all_materials)
                and "baseColorTexture" in (all_materials[index].get("pbrMetallicRoughness") or {}))

    objects = []
    for node in document.get("nodes") or []:
        mesh = node.get("mesh")
        if not isinstance(mesh, int) or not 0 <= mesh < len(meshes):
            continue
        primitives = meshes[mesh].get("primitives", [])
        objects.append({
            "name": node.get("name"),
            "materials": [primitive.get("material") for primitive in primitives],
            "triangles": glb.glb_triangles({"scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
                                            "meshes": [meshes[mesh]], "accessors": document.get("accessors")}),
            # Trägt jedes Primitiv mit Textur seinen UV-Satz? ``False`` auch ohne Textur: dann braucht es keinen.
            "uv": any(textured(primitive) for primitive in primitives) and all(
                "TEXCOORD_0" in (primitive.get("attributes") or {}) for primitive in primitives if textured(primitive)),
        })
    return {"bytes": len(rest) + 12, "objects": objects, "materials": materials, "images": images,
            "cameras": len(document.get("cameras") or []), "triangles": glb.glb_triangles(document)}


def png_has_alpha(data: bytes) -> bool:
    """Trägt ein PNG laut Kopf einen Alphakanal (Farbtyp 4 oder 6, oder ein ``tRNS``-Chunk)? JPEG nie.

    Das entscheidet beim Verkleinern über das Format: mit Alpha bleibt es PNG, sonst JPEG — Blender und Rhino
    laden jedes PNG mit vier Kanälen und sagen es selbst nicht.
    """
    if data[:8] != b"\x89PNG\r\n\x1a\n" or len(data) < 26:
        return False
    if data[25] in (4, 6):
        return True
    at = 8
    while at + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[at:at + 8])
        if kind == b"tRNS":
            return True
        if kind == b"IDAT":
            return False
        at += 12 + length
    return False


def image_size(data: bytes) -> list | None:
    """``[Breite, Höhe]`` aus dem Kopf eines PNG oder JPEG — ``None``, wenn er nicht lesbar ist."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return list(struct.unpack(">II", data[16:24]))
    if data[:2] == b"\xff\xd8":
        at = 2
        while at + 9 < len(data):
            if data[at] != 0xFF:
                return None
            marker = data[at + 1]
            length = struct.unpack(">H", data[at + 2:at + 4])[0]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                height, width = struct.unpack(">HH", data[at + 5:at + 9])
                return [width, height]
            at += 2 + length
    return None
