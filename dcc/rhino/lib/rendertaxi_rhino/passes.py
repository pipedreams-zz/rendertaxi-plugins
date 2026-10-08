"""Die Kanäle von Rhino Render als Datenpässe des Vertrags 1.3.0 — ohne ``Rhino``, hostfrei prüfbar.

Was die Kanäle liefern, ist am Host gemessen (Rhino 8.35, macOS arm64, 08.10.2026,
``docs/measurements/2026-10-08-render-kanaele.md``):

* **Distance** (QR-03): Float in Modelleinheiten, **planar** (Abstand entlang der Blickachse — eine Wand
  senkrecht zur Blickachse in 10 m trägt in der Mitte und in den Ecken 9,99993), ohne Treffer 1e10.
* **Normal** (QR-04): Weltnormalen in Rhino-Achsen, **über die Samples summiert** (Betrag = Zahl der Samples des
  Pixels, mit adaptivem Sampling 32 … 50), ohne Treffer (0, 0, 0). Die Richtung ist exakt; normiert wird hier.
* **Albedo** (QR-02): linear (Grau 50 % → 0,2195 = 0,502^2,2 je Sample), ebenfalls **summiert**. Die Samplezahl
  des Pixels ist der Betrag der Normalensumme; an Silhouetten (gemischte Normalen) ist sie zu klein geschätzt —
  dort liegt die Albedo um höchstens 11 % zu hoch (gemessen), auf Flächen ist sie exakt.
* **ObjectIds / MaterialIds** (QR-05): ein Wert je Pixel ohne Mischwerte an Kanten (drei Objekte → genau drei
  Werte), 0 ohne Treffer; die Werte selbst wechseln von Rendern zu Rendern. Sie werden deshalb je Aufnahme auf
  Indizes 1 … N abgebildet (aufsteigend nach Rohwert).

Die Zeilen kommen als flache Float-Listen (``W · H · Komponenten``) aus ``Channel.GetValues``, Zeile 0 oben.
Ausgabe sind Zeilen für ``pngwrite.write_png``.
"""

from __future__ import annotations

import math

# Distance ohne Treffer: gemessen 1e10. Alles ab 1e9 (und nicht endliche Werte) heißt „kein Treffer".
DEPTH_MISS = 1.0e9


def _hit(value: float) -> bool:
    return value == value and 0.0 < value < DEPTH_MISS


def depth_range(distances, meters: float) -> tuple[float, float] | None:
    """``(near, far)`` in Metern aus den Treffern — auf Millimeter nach außen gerundet; ``None`` ohne Treffer.

    Rhino hat keine feste Clipping-Ebene: ``FrustumNear``/``FrustumFar`` passt Rhino laufend an die Szene an.
    Deshalb gilt der Bereich der Treffer im Bild (wie das Cinema-4D-Plugin ohne Clipping); ``far > near`` immer.
    """
    hits = [d for d in distances if _hit(d)]
    if not hits:
        return None
    near = max(math.floor(min(hits) * meters * 1000.0) / 1000.0, 0.000001)
    far = math.ceil(max(hits) * meters * 1000.0) / 1000.0
    near, far = round(near, 6), round(far, 6)
    if far <= near:
        far = round(near + 0.001, 6)
    return near, far


def depth_rows(distances, width: int, height: int, meters: float, near: float, far: float):
    """``normalized-linear``: v = (d − near) / (far − near), geklemmt; kein Treffer 1 (Vertrag 1.3.0)."""
    span = far - near
    for y in range(height):
        row = distances[y * width:(y + 1) * width]
        yield [((d * meters - near) / span) if _hit(d) else 1.0 for d in row]


def to_export(x: float, y: float, z: float) -> tuple[float, float, float]:
    """Rhino-Weltachsen (Z oben, rechtshändig) → Exportraum des Manifests (Y oben, rechtshändig).

    Dieselbe Drehung um −90° um X wie Rhinos glTF-Export („rotates the Rhino model -90 degree around the
    x-axis"): (x, y, z) → (x, z, −y). Keine Spiegelung.
    """
    return x, z, -y


def normal_rows(sums, width: int, height: int):
    """Normalen im Raum ``world`` des Manifests (Exportraum), (n + 1) / 2 je Komponente, kein Treffer 0,5."""
    for y in range(height):
        row = []
        base = y * width * 3
        for x in range(width):
            i = base + 3 * x
            nx, ny, nz = sums[i], sums[i + 1], sums[i + 2]
            length = math.sqrt(nx * nx + ny * ny + nz * nz)
            if not length or length != length:
                row += [0.5, 0.5, 0.5]
                continue
            ex, ey, ez = to_export(nx / length, ny / length, nz / length)
            row += [(ex + 1.0) * 0.5, (ey + 1.0) * 0.5, (ez + 1.0) * 0.5]
        yield row


def albedo_rows(sums, normal_sums, width: int, height: int):
    """Albedo linear je Sample: Summe / Samplezahl, die Samplezahl aus dem Betrag der Normalensumme.

    Ohne Treffer (Normalensumme 0) ist die Albedo 0.
    """
    for y in range(height):
        row = []
        base = y * width * 3
        for x in range(width):
            i = base + 3 * x
            nx, ny, nz = normal_sums[i], normal_sums[i + 1], normal_sums[i + 2]
            count = math.sqrt(nx * nx + ny * ny + nz * nz)
            if not count or count != count:
                row += [0.0, 0.0, 0.0]
                continue
            row += [sums[i] / count, sums[i + 1] / count, sums[i + 2] / count]
        yield row


def id_index(values) -> dict:
    """Rohwert → Index 1 … N, aufsteigend nach Rohwert; 0 bleibt 0 (kein Treffer)."""
    found = sorted({v for v in values if v == v and v != 0.0})
    return {raw: index for index, raw in enumerate(found, start=1)}


def id_rows(values, width: int, height: int, index: dict, bit_depth: int):
    """Index als Grauwert: gespeichert wird der Index als Ganzzahl (v = Index / (2^Bittiefe − 1))."""
    top = float((1 << bit_depth) - 1)
    for y in range(height):
        yield [index.get(v, 0) / top for v in values[y * width:(y + 1) * width]]


def id_problem(index: dict, bit_depth: int) -> str | None:
    """Mehr verschiedene Werte, als die Bittiefe fasst, sind kein gültiges Indexbild (Vertrag 1.3.0)."""
    top = (1 << bit_depth) - 1
    if len(index) > top:
        return f"{len(index)} verschiedene Werte; {bit_depth} Bit fassen höchstens {top}. Mit 16 Bit erneut aufnehmen."
    return None
