"""Ein TIFF-Leser für die Objektpuffer des Dateiwegs — 32-Bit-Float, ohne Fremdbibliothek (RTX-C4D-005, #208).

Cinema 4D 2026.3.1 schreibt die Multi-Pass-Dateien als TIFF mit einem Kanal, 32 Bit Float, PackBits-komprimiert
(am Host gemessen, ``docs/measurements/2026-10-05-paesse-dateiweg.md``). Dieser Leser kennt genau das und unkomprimierte
Streifen; alles andere ist ein ``TiffError`` — das Plugin meldet den Pass dann als ``planned``, statt zu raten.
Nur Standardbibliothek (``struct``).
"""

from __future__ import annotations

import struct

_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 11: 4, 12: 8}
_PACKBITS = 32773
_NONE = 1
_FLOAT = 3


class TiffError(ValueError):
    """Die Datei ist kein TIFF, das dieser Leser kennt."""


def _unpackbits(chunk: bytes) -> bytes:
    out = bytearray()
    i = 0
    while i < len(chunk):
        n = chunk[i]
        i += 1
        if n < 128:
            if i + n + 1 > len(chunk):
                raise TiffError("PackBits-Literal abgeschnitten")
            out += chunk[i:i + n + 1]
            i += n + 1
        elif n > 128:
            if i >= len(chunk):
                raise TiffError("PackBits-Wiederholung abgeschnitten")
            out += bytes([chunk[i]]) * (257 - n)
            i += 1
    return bytes(out)


def _unpack(data: bytes, fmt: str, start: int) -> tuple:
    """``struct.unpack`` an ``start`` — außerhalb der Datei ein ``TiffError`` statt ``struct.error``."""
    end = start + struct.calcsize(fmt)
    if start < 0 or end > len(data):
        raise TiffError("TIFF-Struktur abgeschnitten")
    return struct.unpack(fmt, data[start:end])


def read_float_gray(path: str) -> tuple[int, int, tuple[float, ...]]:
    """``(Breite, Höhe, Werte)`` eines einkanaligen 32-Bit-Float-TIFF, Zeile für Zeile von oben."""
    with open(path, "rb") as handle:
        data = handle.read()
    if data[:4] not in (b"II*\0", b"MM\0*"):
        raise TiffError("keine TIFF-Datei")
    order = "<" if data[:2] == b"II" else ">"
    offset = _unpack(data, order + "I", 4)[0]
    count = _unpack(data, order + "H", offset)[0]
    tags: dict[int, list[int]] = {}
    for index in range(count):
        start = offset + 2 + 12 * index
        tag, kind, number = _unpack(data, order + "HHI", start)
        if kind not in (3, 4):
            continue
        size = _SIZES[kind] * number
        if not number or size > len(data):
            raise TiffError(f"Eintrag {tag} ungültig")
        where = start + 8 if size <= 4 else _unpack(data, order + "I", start + 8)[0]
        tags[tag] = list(_unpack(data, order + ("H" if kind == 3 else "I") * number, where))
    try:
        width, height = tags[256][0], tags[257][0]
        offsets, lengths = tags[273], tags[279]
    except KeyError:
        raise TiffError("Maße oder Streifen fehlen") from None
    if tags.get(277, [1])[0] != 1 or tags.get(258, [0])[0] != 32 or tags.get(339, [1])[0] != _FLOAT:
        raise TiffError("nicht ein Kanal 32 Bit Float")
    compression = tags.get(259, [_NONE])[0]
    if compression not in (_NONE, _PACKBITS):
        raise TiffError(f"Kompression {compression}")
    if not width or not height or len(offsets) != len(lengths):
        raise TiffError("Maße oder Streifen ungültig")
    pixels = bytearray()
    for start, length in zip(offsets, lengths):
        if start + length > len(data):
            raise TiffError("Streifen abgeschnitten")
        chunk = data[start:start + length]
        pixels += _unpackbits(chunk) if compression == _PACKBITS else chunk
    if len(pixels) < 4 * width * height:
        raise TiffError("zu wenige Bildpunkte")
    return width, height, struct.unpack(order + f"{width * height}f", bytes(pixels[:4 * width * height]))


def write_float_gray(path: str, width: int, height: int, values) -> None:
    """Das Gegenstück für Tests und den Teststub: ein Streifen, PackBits wie Cinema 4D (Literal-Läufe)."""
    raw = struct.pack(f"<{width * height}f", *values)
    packed = bytearray()
    for start in range(0, len(raw), 128):
        block = raw[start:start + 128]
        packed += bytes([len(block) - 1]) + block
    entries = [(256, 4, width), (257, 4, height), (258, 3, 32), (259, 3, _PACKBITS), (262, 3, 1), (273, 4, 0),
               (277, 3, 1), (278, 4, height), (279, 4, len(packed)), (339, 3, _FLOAT)]
    header = 8
    ifd_size = 2 + 12 * len(entries) + 4
    data_offset = header + ifd_size
    ifd = struct.pack("<H", len(entries))
    for tag, kind, value in entries:
        if tag == 273:
            value = data_offset
        ifd += struct.pack("<HHI", tag, kind, 1) + (struct.pack("<HH", value, 0) if kind == 3 else struct.pack("<I", value))
    ifd += struct.pack("<I", 0)
    with open(path, "wb") as handle:
        handle.write(b"II*\0" + struct.pack("<I", header) + ifd + bytes(packed))
