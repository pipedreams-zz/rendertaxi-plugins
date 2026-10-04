"""Cinema 4D als Host des gemeinsamen Clients (ADR 0035).

Hier — und nur hier — stehen die Kennungen des Plugins:

* ``PLUGIN_ID`` — die bei Maxon registrierte Plugin-ID (developers.maxon.net).
  Sie kennzeichnet auch den Untercontainer mit der Dokumentkennung (QC-10):
  eine neue ID heißt, dass vorhandene Dokumente ihre Kennung nicht mehr
  finden und bei der nächsten Übernahme eine neue bekommen.
* ``PLUGIN_VERSION`` — die eine Quelle der Pluginversion;
  ``scripts/pack.sh`` im öffentlichen Spiegel liest sie hier.
* ``HOST_MAJOR`` — die Cinema-4D-Fassung, für die das Plugin gebaut und
  gegen deren Dokumentation (Python SDK 2026.0.0, Python 3.11.4) es belegt ist.
  Zugleich die Mindestversion: eine Kompatibilität mit 2025 belegt die
  2026-Dokumentation nicht. Eine ältere Fassung lädt das Plugin trotzdem —
  sie bekommt statt des Fensters eine klare Meldung (``supported``).

Kein ``c4d``-Import auf Modulebene außer für ``host_version``: der Client
wird beim Laden konfiguriert, bevor irgendetwas anderes geschieht.
"""

from __future__ import annotations

import os

from . import rendertaxi_client as client

# Registrierte Plugin-ID (vom Nutzer am 01.10.2026 genannt; die Entwicklungs-ID 1000001 entfällt).
PLUGIN_ID = 1070762
PLUGIN_VERSION = "0.2.0"
HOST_MAJOR = 2026

KEY = "cinema-4d"
CLIENT_ID = "ai.rendertaxi.plugin.cinema-4d"
LABEL = "Cinema 4D"
# Der Plugin-Ordner (mit rendertaxi.pyp) — nur seine Codestellen nennt das Protokoll.
PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

HOST = client.configure(client.Host(
    key=KEY,
    client_id=CLIENT_ID,
    label=LABEL,
    plugin_version=PLUGIN_VERSION,
    roots=(PLUGIN_DIR,),
    # Tiefe: Standard/Physical kodieren den Abstand zur Fokusebene, die Umrechnung in
    # normalized-linear (1.3.0) ist nicht belegt (QC-03). Normalen sind seit RTX-C4D-005 belegt (QC-04, gemessen).
    never_present={"depth": "QC-03"},
))


def _version_number() -> int:
    import c4d

    return int(c4d.GetC4DVersion())


def host_version() -> str:
    """``source.host.version`` aus ``c4d.GetC4DVersion()`` — erwartet etwa 2026300 → ``2026.3.0``.

    Die Referenz 2026 nennt nur das Beispiel „12016"; die Kodierung
    Jahr·1000 + Minor·100 + Patch ist angenommen und Teil der Abnahme am Mac.
    """
    number = _version_number()
    return f"{number // 1000}.{(number % 1000) // 100}.{number % 100}"


def supported() -> bool:
    """Läuft eine Fassung ab ``HOST_MAJOR``? Nie eine Ausnahme — im Zweifel „nein"."""
    try:
        return _version_number() // 1000 >= HOST_MAJOR
    except Exception:  # noqa: BLE001 — ein unbekanntes c4d darf das Laden nicht verhindern
        return False


def unsupported_text() -> str:
    try:
        found = host_version()
    except Exception:  # noqa: BLE001
        found = "unbekannt"
    return (f"rendertaxi.ai {PLUGIN_VERSION} braucht Cinema 4D {HOST_MAJOR} oder neuer "
            f"(gefunden: {found}). Bitte die passende Fassung des Plugins verwenden.")
