"""Rhino 8 als Host des gemeinsamen Clients (ADR 0035, RTX-RH-002).

Hier — und nur hier — stehen die Kennungen des Plugins:

* ``PLUGIN_GUID`` — die feste Kennung des Rhino-Plugins (``rdtxai.rhproj``,
  Feld ``id``, und ``guid:`` im yak-Paket). Eine neue Kennung hieße für Rhino
  ein anderes Plugin; sie bleibt.
* ``PANEL_GUID`` — die feste Kennung des Panels (RTX-RH-004). Unter ihr merkt
  sich Rhino, wo das Panel angedockt ist; eine neue Kennung hieße für Rhino
  ein anderes Panel.
* ``PLUGIN_VERSION`` — die eine Quelle der Pluginversion;
  ``integrations/rhino/scripts/pack.sh`` und der öffentliche Spiegel lesen sie
  hier, ``rdtxai.rhproj`` trägt dieselbe Zahl (``tests/test_plugin.py`` prüft es).
* ``TESTED_VERSIONS`` — die Rhino-Fassungen, an denen das Plugin geprüft ist
  (Rhino 8.35, CPython 3.9.10). Wie bei Cinema 4D (RTX-P-023): eine neuere,
  nicht genannte Fassung läuft mit dem Hinweis „ungeprüft“ (``untested_note``),
  eine ältere bekommt statt des Fensters eine klare Meldung (``supported``).
* ``HOST_MAJOR`` — die älteste geprüfte Fassung, also die Mindestversion und
  ``rh8`` im Paketnamen. Immer ``min(TESTED_VERSIONS)``.

Kein ``Rhino``-Import auf Modulebene: der Client wird beim Laden konfiguriert,
bevor irgendetwas anderes geschieht, und die Tests laufen ohne Rhino.
"""

from __future__ import annotations

import os

from . import rendertaxi_client as client
from .rendertaxi_client import hostversion

PLUGIN_GUID = "b47606d7-0da9-4f5b-86c4-43f5b32f1865"
PANEL_GUID = "5d0c8a3e-2f71-4b9a-9c6e-7a1f3b2d4e60"
PLUGIN_VERSION = "0.5.1"
TESTED_VERSIONS = (8,)
HOST_MAJOR = 8

KEY = "rhino"
CLIENT_ID = "ai.rendertaxi.plugin.rhino"
LABEL = "Rhino"
# Kurzmarke als Zeichen: Fenstertitel, Befehl, Paketname (docs/design/gui-shell/texte.md).
MARK = "rdtx.ai"
COMMAND = "RdtxAI"
# Der Bibliotheksordner des Plugins — nur seine Codestellen nennt das Protokoll.
PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))

HOST = client.configure(client.Host(
    key=KEY,
    client_id=CLIENT_ID,
    label=LABEL,
    plugin_version=PLUGIN_VERSION,
    roots=(PLUGIN_DIR,),
    # Tiefe, Normalen, Albedo, Objekt- und Material-ID aus den Kanälen von Rhino Render sind am Host gemessen
    # (docs/measurements/2026-10-08-render-kanaele.md). Eine Maske kennt Rhino nicht (QR-06).
    never_present={"mask": "QR-06"},
))


def host_version() -> str:
    """``source.host.version`` aus ``RhinoApp.Version`` — etwa ``8.35.26251.13002``."""
    import Rhino

    return str(Rhino.RhinoApp.Version)


def _found_major() -> int | None:
    try:
        return int(host_version().split(".")[0])
    except Exception:  # noqa: BLE001 — ein unbekanntes Rhino darf das Laden nicht verhindern
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
    """„Rhino 9.1.… ist ungeprüft (geprüft: 8).“ — oder ``None`` in einer geprüften Fassung."""
    if version_state() != hostversion.UNTESTED:
        return None
    return hostversion.untested_note(LABEL, _found(), TESTED_VERSIONS)


def unsupported_text() -> str:
    return hostversion.too_old_text(MARK, PLUGIN_VERSION, LABEL, _found(), TESTED_VERSIONS)


def build_text() -> str:
    """Die Build-Kennung für Fensterfuß und „Über" — ``Build <Kurzhash> vom <Datum>`` oder „Entwicklungsbuild".

    ``buildinfo.py`` (``COMMIT``, ``DATE``) schreibt ``integrations/rhino/scripts/pack.sh`` beim Bauen in diesen
    Ordner; im Repository fehlt es. Ein kaputtes Modul heißt ebenfalls „Entwicklungsbuild" (Regel 3).
    """
    try:
        from . import buildinfo  # type: ignore[attr-defined]

        commit, date = str(buildinfo.COMMIT)[:16], str(buildinfo.DATE)[:10]
        if commit and date:
            return f"Build {commit} vom {date}"
    except Exception:  # noqa: BLE001 — fehlt oder kaputt: Entwicklungsbuild
        pass
    return "Entwicklungsbuild"
