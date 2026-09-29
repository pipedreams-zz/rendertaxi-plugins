"""OpenEXR ohne Drittpaket — den Kopf lesen, einen Pass aus einer Mehrschichtdatei lösen.

``describe`` liest nur den Kopf einer EXR-Datei (Maße, Kanäle, Pixeltyp) —
für jede Kompression, damit das Manifest die Werte aus der Datei nimmt statt
aus einer Einstellung (Cinema 4D schreibt Datenpässe selbst).

``extract_pass`` gibt es für Blender: Ein Render mit Pässen landet in Blender
5.2 im „Render Result". Python kommt an dessen Pixel nicht heran, und
``Image.save_render`` schreibt entweder nur das Beauty-Bild (Einzelbild) oder
**alle** Pässe in eine mehrteilige OpenEXR-Datei (``OPEN_EXR_MULTILAYER``,
gemessen am 27.09.2026: je Pass ein Teil namens ``<ViewLayer>.<Pass>``, etwa
``ViewLayer.Normal`` mit den Kanälen ``…Normal.X``, ``.Y``, ``.Z``). Der Vertrag
will je Rolle **eine** Datei. Der Compositor-Weg (File-Output-Knoten) hätte die
Compositing-Einstellungen des Nutzers verändert; dieses Modul lässt sie in
Ruhe und liest die Datei selbst.

Für ``parts`` und ``extract_pass`` unterstützt wird bewusst nur, was das
Blender-Add-on selbst schreibt: Scanline-Dateien **ohne Kompression**
(``exr_codec = 'NONE'``), einteilig oder mehrteilig, Pixeltypen UINT, HALF und
FLOAT. Alles andere ist ein ausdrücklicher Fehler. Kein Drittpaket (OpenEXR,
OpenImageIO) — die Python-Standardbibliothek reicht.
"""

from __future__ import annotations

import struct

MAGIC = 20000630
_MULTIPART = 0x1000
_TILED = 0x200
_DEEP = 0x800
_PIXEL_BYTES = {0: 4, 1: 2, 2: 4}


class ExrError(ValueError):
    pass


def _cstr(data: bytes, pos: int) -> tuple[str, int]:
    end = data.index(b"\0", pos)
    return data[pos:end].decode("utf-8"), end + 1


def _read_header(data: bytes, pos: int) -> tuple[dict, int]:
    header: dict = {}
    while True:
        name, pos = _cstr(data, pos)
        if not name:
            return header, pos
        kind, pos = _cstr(data, pos)
        (size,) = struct.unpack_from("<i", data, pos)
        pos += 4
        header[name] = (kind, data[pos:pos + size])
        pos += size


def describe(data: bytes) -> dict:
    """Maße und Kanäle einer einteiligen EXR-Datei aus ihrem Kopf — ``image`` des Manifests ohne Farbraum.

    Nur der Kopf wird gelesen; Kompression und Kachelung spielen keine Rolle.
    Kanäle ``R``/``G``/``B``/``A`` (auch mit Schichtpräfix) oder genau einer;
    alle mit demselben Pixeltyp.
    """
    if len(data) < 8 or struct.unpack_from("<i", data, 0)[0] != MAGIC:
        raise ExrError("Die Datei ist kein OpenEXR.")
    flags = struct.unpack_from("<I", data, 4)[0]
    if flags & (_MULTIPART | _DEEP):
        raise ExrError("Mehrteilige oder tiefe EXR-Dateien werden nicht beschrieben.")
    header, _pos = _read_header(data, 8)
    if "channels" not in header or "dataWindow" not in header:
        raise ExrError("Dem EXR-Kopf fehlen Kanäle oder Datenfenster.")
    x0, y0, x1, y1 = struct.unpack("<iiii", header["dataWindow"][1])
    chans = _channels(header["channels"][1])
    types = {c[1] for c in chans}
    if not chans or len(types) != 1:
        raise ExrError("Ein EXR mit gemischten Pixeltypen wird nicht beschrieben.")
    names = sorted(c[0].rsplit(".", 1)[-1] for c in chans)
    layout = {1: "gray", 3: "rgb", 4: "rgba"}.get(len(chans))
    if layout is None or (len(chans) > 1 and names not in (["B", "G", "R"], ["A", "B", "G", "R"])):
        raise ExrError(f"Unbekannte Kanalbelegung: {names}")
    pixel_type = types.pop()
    return {
        "width": x1 - x0 + 1,
        "height": y1 - y0 + 1,
        "channels": layout,
        "bitDepth": 16 if pixel_type == 1 else 32,
        "sampleFormat": "uint" if pixel_type == 0 else "float",
    }


def _channels(raw: bytes) -> list[tuple[str, int, int, int]]:
    result = []
    pos = 0
    while raw[pos] != 0:
        name, pos = _cstr(raw, pos)
        pixel_type, _linear, x_sampling, y_sampling = struct.unpack_from("<iB3xii", raw, pos)
        pos += 16
        result.append((name, pixel_type, x_sampling, y_sampling))
    return result


def parts(data: bytes) -> list[dict]:
    """Die Teile einer EXR-Datei: Name, Kanäle, Datenfenster."""
    if len(data) < 8 or struct.unpack_from("<i", data, 0)[0] != MAGIC:
        raise ExrError("Die Datei ist kein OpenEXR.")
    flags = struct.unpack_from("<I", data, 4)[0]
    if flags & (_TILED | _DEEP):
        raise ExrError("Gekachelte oder tiefe EXR-Dateien werden nicht gelesen.")
    pos = 8
    headers = []
    while True:
        header, pos = _read_header(data, pos)
        if not header:
            break
        headers.append(header)
        if not flags & _MULTIPART:
            break
    result = []
    for header in headers:
        if header.get("compression", ("", b"\x00"))[1] != b"\x00":
            raise ExrError("Nur unkomprimierte EXR-Dateien werden gelesen (exr_codec NONE).")
        x0, y0, x1, y1 = struct.unpack("<iiii", header["dataWindow"][1])
        chans = _channels(header["channels"][1])
        if any(c[2] != 1 or c[3] != 1 for c in chans):
            raise ExrError("Unterabgetastete Kanäle werden nicht gelesen.")
        name = header.get("name", ("string", b""))[1].decode("utf-8")
        result.append({
            "name": name,
            "channels": chans,
            "window": (x0, y0, x1, y1),
            "header": header,
        })
    return _with_offsets(data, flags, result, pos)


def _with_offsets(data: bytes, flags: int, result: list[dict], pos: int) -> list[dict]:
    # Bei mehrteiligen Dateien hat ``parts`` den leeren Abschlusskopf schon gelesen.
    for part in result:
        _x0, y0, _x1, y1 = part["window"]
        count = y1 - y0 + 1
        if "chunkCount" in part["header"]:
            count = struct.unpack("<i", part["header"]["chunkCount"][1])[0]
        part["offsets"] = list(struct.unpack_from(f"<{count}Q", data, pos))
        pos += 8 * count
    part_numbered = bool(flags & _MULTIPART)
    for index, part in enumerate(result):
        part["index"] = index
        part["multipart"] = part_numbered
    return result


def _scanlines(data: bytes, part: dict) -> dict[int, bytes]:
    lines: dict[int, bytes] = {}
    x0, y0, x1, y1 = part["window"]
    width = x1 - x0 + 1
    line_bytes = sum(_PIXEL_BYTES[c[1]] for c in part["channels"]) * width
    for offset in part["offsets"]:
        pos = offset
        if part["multipart"]:
            (number,) = struct.unpack_from("<i", data, pos)
            if number != part["index"]:
                raise ExrError("Ein Block gehört zu einem anderen Teil der Datei.")
            pos += 4
        y, size = struct.unpack_from("<ii", data, pos)
        pos += 8
        if size % line_bytes:
            raise ExrError("Ein Block passt nicht zu den Kanälen (komprimiert?).")
        for row in range(size // line_bytes):
            lines[y + row] = data[pos + row * line_bytes:pos + (row + 1) * line_bytes]
    if sorted(lines) != list(range(y0, y1 + 1)):
        raise ExrError("Der Datei fehlen Zeilen.")
    return lines


def _attribute(name: str, kind: str, value: bytes) -> bytes:
    return name.encode() + b"\0" + kind.encode() + b"\0" + struct.pack("<i", len(value)) + value


# Umbenennung der Kanäle eines Passes auf die übliche Farbbenennung, damit
# jeder Betrachter die Datei ohne Kenntnis von Blenders Passnamen öffnet.
_RENAME = {
    1: {"X": "Y", "Z": "Y", "V": "Y", "R": "Y"},
    3: {"X": "R", "Y": "G", "Z": "B", "R": "R", "G": "G", "B": "B"},
    4: {"R": "R", "G": "G", "B": "B", "A": "A", "X": "R", "Y": "G", "Z": "B", "W": "A"},
}


def extract_pass(data: bytes, part_name: str) -> tuple[bytes, dict]:
    """Einen Teil (``ViewLayer.Normal``) als einteilige, unkomprimierte EXR-Datei.

    Rückgabe: die neuen Bytes und eine Beschreibung
    (``width``, ``height``, ``channels``, ``bitDepth``).
    """
    all_parts = parts(data)
    part = next((p for p in all_parts if p["name"] == part_name), None)
    if part is None:
        raise ExrError(f"Der Pass {part_name!r} steht nicht in der Datei.")
    chans = part["channels"]
    count = len(chans)
    if count not in _RENAME:
        raise ExrError(f"Ein Pass mit {count} Kanälen wird nicht übernommen.")
    types = {c[1] for c in chans}
    if len(types) != 1:
        raise ExrError("Ein Pass mit gemischten Pixeltypen wird nicht übernommen.")
    pixel_type = types.pop()
    suffix = [c[0].rsplit(".", 1)[-1] for c in chans]
    mapping = _RENAME[count]
    if any(s not in mapping for s in suffix):
        raise ExrError(f"Unbekannte Kanäle im Pass {part_name!r}: {suffix}")
    renamed = [mapping[s] for s in suffix]
    if len(set(renamed)) != count:
        raise ExrError(f"Die Kanäle des Passes {part_name!r} lassen sich nicht eindeutig benennen.")

    x0, y0, x1, y1 = part["window"]
    width = x1 - x0 + 1
    size = _PIXEL_BYTES[pixel_type]
    lines = _scanlines(data, part)
    # EXR verlangt die Kanalliste alphabetisch; die Daten einer Zeile folgen ihr.
    order = sorted(range(count), key=lambda i: renamed[i])

    channel_list = b"".join(
        renamed[i].encode() + b"\0" + struct.pack("<iB3xii", pixel_type, 0, 1, 1) for i in order
    ) + b"\0"
    window = struct.pack("<iiii", x0, y0, x1, y1)
    header = b"".join((
        _attribute("channels", "chlist", channel_list),
        _attribute("compression", "compression", b"\x00"),
        _attribute("dataWindow", "box2i", window),
        _attribute("displayWindow", "box2i", part["header"].get("displayWindow", ("", window))[1]),
        _attribute("lineOrder", "lineOrder", b"\x00"),
        _attribute("pixelAspectRatio", "float", struct.pack("<f", 1.0)),
        _attribute("screenWindowCenter", "v2f", struct.pack("<ff", 0.0, 0.0)),
        _attribute("screenWindowWidth", "float", struct.pack("<f", 1.0)),
    )) + b"\0"
    head = struct.pack("<iI", MAGIC, 2) + header
    rows = y1 - y0 + 1
    block_payload = width * size * count
    table_end = len(head) + 8 * rows
    offsets = [table_end + r * (8 + block_payload) for r in range(rows)]
    body = bytearray()
    for y in range(y0, y1 + 1):
        line = lines[y]
        stripes = [line[i * width * size:(i + 1) * width * size] for i in range(count)]
        body += struct.pack("<ii", y, block_payload)
        for i in order:
            body += stripes[i]
    out = head + struct.pack(f"<{rows}Q", *offsets) + bytes(body)
    description = {
        "width": width,
        "height": rows,
        "channels": {1: "gray", 3: "rgb", 4: "rgba"}[count],
        "bitDepth": 16 if pixel_type == 1 else 32,
        "sampleFormat": "uint" if pixel_type == 0 else "float",
    }
    return out, description
