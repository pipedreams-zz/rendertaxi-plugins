"""Die Texturen der Messszene (RTX-P-021) — nur Standardbibliothek, für jeden Host gleich.

    python3 integrations/_shared/fixtures/materials/textures.py <ordner>

schreibt ``holz.png`` (256 × 256, Maserung), ``ziegel.png`` (256 × 256,
Mauerwerk) und ``ziegel-gross.png`` (2400 × 2400, Mauerwerk mit Rauschen,
etwa 6 MB: über der Grenze von 2 MB, damit jede Messung das Verkleinern
zeigt). Die beiden kleinen liegen auch im Repository; das große entsteht
erst bei der Messung, denn es wäre die größte Datei des Repositorys.
Deterministisch: dieselben Bytes auf jedem Rechner.
"""

from __future__ import annotations

import os
import struct
import sys
import zlib


def png(width: int, height: int, pixel) -> bytes:
    """Ein RGB-PNG aus ``pixel(x, y) -> (r, g, b)``."""
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for x in range(width):
            rows.extend(pixel(x, y))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + chunk(b"IEND", b""))


def wood(x: int, y: int) -> tuple:
    ring = (x * 7 + (y * y) // 97 + (y // 9) * 3) % 32
    shade = 150 + ring * 2
    return shade, int(shade * 0.72), int(shade * 0.45)


def brick(x: int, y: int, noise: int = 0) -> tuple:
    row = y // 32
    joint = y % 32 < 3 or (x + (16 if row % 2 else 0) * 4) % 128 < 4
    if joint:
        base = (200, 196, 188)
    else:
        base = (168, 64 + (row * 13) % 20, 44)
    return tuple(max(0, min(255, channel + noise)) for channel in base)


def large_brick(side: int = 2400):
    seed = [0x2545F491]

    def pixel(x: int, y: int) -> tuple:
        seed[0] = (seed[0] * 1103515245 + 12345) & 0x7FFFFFFF
        return brick(x % 256, y % 256, (seed[0] >> 16) % 61 - 30)

    return png(side, side, pixel)


def write_all(folder: str, large: bool = True) -> dict:
    """Die Texturen nach ``folder`` — ``{Name: Pfad}``."""
    os.makedirs(folder, exist_ok=True)
    out = {}
    for name, data in (("holz.png", png(256, 256, wood)), ("ziegel.png", png(256, 256, brick))) + (
            (("ziegel-gross.png", large_brick()),) if large else ()):
        path = os.path.join(folder, name)
        with open(path, "wb") as handle:
            handle.write(data)
        out[name] = path
    return out


if __name__ == "__main__":
    for name, path in write_all(sys.argv[1] if len(sys.argv) > 1 else ".").items():
        print(name, os.path.getsize(path))
