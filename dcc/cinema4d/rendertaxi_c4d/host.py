"""Cinema 4D als Host des gemeinsamen Clients (ADR 0035).

Hier — und nur hier — stehen die Kennungen des Plugins:

* ``PLUGIN_ID`` — die bei Maxon registrierte Plugin-ID (developers.maxon.net).
  Sie kennzeichnet auch den Untercontainer mit der Dokumentkennung (QC-10):
  eine neue ID heißt, dass vorhandene Dokumente ihre Kennung nicht mehr
  finden und bei der nächsten Übernahme eine neue bekommen.
* ``PLUGIN_VERSION`` — die eine Quelle der Pluginversion;
  ``scripts/pack.sh`` im öffentlichen Spiegel liest sie hier.
* ``TESTED_VERSIONS`` — die Cinema-4D-Fassungen, an denen das Plugin geprüft
  ist (RTX-P-023): 2026 gegen die Dokumentation (Python SDK 2026.0.0,
  Python 3.11.4) und am Host, 2027 am Host des Nutzers. Die eine Liste für
  README, Anleitung und die Seite „Plugins“ (``tests/test_plugin.py`` hält sie
  zusammen). Eine neuere, nicht genannte Fassung läuft mit dem Hinweis
  „ungeprüft“ (``untested_note``); eine ältere bekommt statt des Fensters eine
  klare Meldung (``supported``) — eine Kompatibilität mit 2025 ist nicht belegt.
* ``HOST_MAJOR`` — die älteste geprüfte Fassung, also die Mindestversion und
  der Name der Zip (``rendertaxi-cinema4d2026-…``); ``scripts/pack.sh`` liest
  sie hier. Immer ``min(TESTED_VERSIONS)``.

Kein ``c4d``-Import auf Modulebene außer für ``host_version``: der Client
wird beim Laden konfiguriert, bevor irgendetwas anderes geschieht.
"""

from __future__ import annotations

import os

from . import rendertaxi_client as client
from .rendertaxi_client import hostversion

# Registrierte Plugin-ID (vom Nutzer am 01.10.2026 genannt; die Entwicklungs-ID 1000001 entfällt).
PLUGIN_ID = 1070762
PLUGIN_VERSION = "0.8.2"
TESTED_VERSIONS = (2026, 2027)
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
    # Tiefe (QC-03, Positions-Pass), Normalen (QC-04) und Objekt-ID (QC-05, Dateiweg) sind seit RTX-C4D-005 am Host
    # gemessen. Die Material-ID liefern Standard und Physical nicht (QC-06) — Corona schon (RTX-C4D-009, Masken je
    # Material-ID, am Host gemessen): keine Rolle ist mehr „nie present“; unter Standard/Physical bleibt sie geplant.
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


def _found_major() -> int | None:
    try:
        return _version_number() // 1000
    except Exception:  # noqa: BLE001 — ein unbekanntes c4d darf das Laden nicht verhindern
        return None


def version_state() -> str:
    """``tested``, ``untested`` oder ``too_old`` (``hostversion.state``) — nie eine Ausnahme."""
    return hostversion.state(_found_major(), TESTED_VERSIONS)


def supported() -> bool:
    """Läuft eine Fassung ab ``HOST_MAJOR``? Geprüft oder neuer und ungeprüft — im Zweifel „nein"."""
    return version_state() != hostversion.TOO_OLD


def _found() -> str:
    try:
        return host_version()
    except Exception:  # noqa: BLE001
        return "unbekannt"


def untested_note() -> str | None:
    """„Cinema 4D 2028.0.0 ist ungeprüft (geprüft: 2026 und 2027).“ — oder ``None`` in einer geprüften Fassung."""
    if version_state() != hostversion.UNTESTED:
        return None
    return hostversion.untested_note(LABEL, _found(), TESTED_VERSIONS)


def unsupported_text() -> str:
    return hostversion.too_old_text("rendertaxi.ai", PLUGIN_VERSION, LABEL, _found(), TESTED_VERSIONS)
