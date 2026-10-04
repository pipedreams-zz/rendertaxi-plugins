"""Ein eigener PNG-Schreiber für Datenpässe — aus Float-Werten, ohne Anzeigetransformation (RTX-C4D-005, #208).

``BaseBitmap.Save(…, FILTER_PNG)`` wendet auf eine Float-Ebene der ``MultipassBitmap`` eine
kanalabhängige Anzeigetransformation an, auch mit ``RENDERFLAGS_OCIO_RAW_RENDERING``
(``docs/measurements/2026-10-01-standard-paesse.md``): ein Normalenwert (0,5 / 0,5 / 0) wird zu
(0,761 / 0,737 / 0). Für die Beauty ist das gewollt, für Datenpässe nicht. Dieser Schreiber nimmt die
rohen Floats (``GetPixelCnt``, ``COLORMODE_RGBf``) und schreibt sie so, wie der Vertrag 1.3.0 es verlangt:
Graustufen oder RGB, 8 oder 16 Bit je Kanal, ``uint``, ohne Alpha, ohne Interlace, ohne Farbprofil.

Nur Standardbibliothek (``struct``, ``zlib``). Ein Wert ``v`` in [0, 1] wird zur Ganzzahl
``round(v · (2^Bittiefe − 1))``, außerhalb geklemmt; ``nan`` wird 0. Zurückgelesen ergibt das den Wert
bis auf eine halbe Stufe — der Belegtest (``tests/test_pngwrite.py``) prüft Float → PNG → Float.
"""

from __future__ import annotations

import math
import os
import struct
import zlib
from collections.abc import Iterable, Sequence

GRAY = 1
RGB = 3
_COLOR_TYPE = {GRAY: 0, RGB: 2}
_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_SIDE = 65536  # Schema: image.width/height


def _chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)


def quantize(value: float, bit_depth: int) -> int:
    """Ein Wert in [0, 1] als Ganzzahl der Bittiefe — geklemmt, ``nan`` wird 0."""
    top = (1 << bit_depth) - 1
    if value != value:  # nan
        return 0
    if value <= 0.0:
        return 0
    if value >= 1.0:
        return top
    return int(math.floor(value * top + 0.5))


def write_png(path: str, width: int, height: int, channels: int, bit_depth: int,
              rows: Iterable[Sequence[float]]) -> None:
    """``rows``: je Bildzeile ``width · channels`` Werte in [0, 1] (bei RGB R, G, B hintereinander je Pixel).

    Genau ``height`` Zeilen; zu wenige oder zu viele Werte sind ein ``ValueError`` — es gibt keine Datei ohne
    vollständigen Inhalt (die Datei entsteht erst, wenn alles gelesen ist).
    """
    if channels not in _COLOR_TYPE:
        raise ValueError("Kanäle: 1 (Graustufen) oder 3 (RGB).")
    if bit_depth not in (8, 16):
        raise ValueError("Bittiefe: 8 oder 16.")
    if not (1 <= width <= MAX_SIDE and 1 <= height <= MAX_SIDE):
        raise ValueError("Bildgröße außerhalb 1 … 65536.")
    count = width * channels
    top = (1 << bit_depth) - 1
    pack = struct.Struct(f">{count}{'B' if bit_depth == 8 else 'H'}")
    compressor = zlib.compressobj(6)
    parts: list[bytes] = []
    written = 0
    for values in rows:
        if len(values) != count:
            raise ValueError(f"Zeile {written}: {len(values)} Werte statt {count}.")
        ints = [0 if v != v or v <= 0.0 else top if v >= 1.0 else int(v * top + 0.5) for v in values]
        parts.append(compressor.compress(b"\0" + pack.pack(*ints)))
        written += 1
    if written != height:
        raise ValueError(f"{written} Zeilen statt {height}.")
    parts.append(compressor.flush())
    header = struct.pack(">IIBBBBB", width, height, bit_depth, _COLOR_TYPE[channels], 0, 0, 0)
    payload = _SIGNATURE + _chunk(b"IHDR", header) + _chunk(b"IDAT", b"".join(parts)) + _chunk(b"IEND", b"")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    temporary = path + ".part"
    with open(temporary, "wb") as handle:
        handle.write(payload)
    os.replace(temporary, path)
