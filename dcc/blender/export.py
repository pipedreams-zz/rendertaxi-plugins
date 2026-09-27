"""Modellweg — die vorgesehene Stelle für Ausbaustufe 2, heute ohne Inhalt.

Das Capture-Manifest v1 trägt ``camera`` und ``geometry`` als Pflichtfelder mit
dem Wert ``null`` (ADR 0009, Entscheidung 1). Kamera- und Geometrieblock kommen
als MINOR-Erweiterung mit #177 (RTX-P-011); dann liefern diese beiden
Funktionen die Blöcke, und ``manifest.build_manifest`` übernimmt sie. Bis dahin
geben sie ``None`` zurück — das Add-on erfindet keinen Block vorab.

Die Schnittstelle steht fest, damit Ausbaustufe 2 nur dieses Modul berührt:

* ``camera_block(context) -> dict | None`` — Kamera der Aufnahme. Braucht
  QB-04 (Sichtfeld mit Achse) und QB-08 (Händigkeit).
* ``geometry_block(context, directory) -> dict | None`` — Geometrie als Datei
  im Capture-Verzeichnis. Braucht QB-03 (Einheiten).
"""

from __future__ import annotations


def camera_block(_context) -> dict | None:
    return None


def geometry_block(_context, _directory: str) -> dict | None:
    return None
