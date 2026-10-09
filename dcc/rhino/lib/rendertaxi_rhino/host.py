"""Rhino 8 als Host des gemeinsamen Clients (ADR 0035, RTX-RH-002).

Hier — und nur hier — stehen die Kennungen des Plugins:

* ``PLUGIN_GUID`` — die feste Kennung des Rhino-Plugins (``rdtxai.rhproj``,
  Feld ``id``, und ``guid:`` im yak-Paket). Eine neue Kennung hieße für Rhino
  ein anderes Plugin; sie bleibt.
* ``PLUGIN_VERSION`` — die eine Quelle der Pluginversion;
  ``integrations/rhino/scripts/pack.sh`` und der öffentliche Spiegel lesen sie
  hier, ``rdtxai.rhproj`` trägt dieselbe Zahl (``tests/test_plugin.py`` prüft es).
* ``HOST_MAJOR`` — die Rhino-Fassung, für die das Plugin gebaut und an der es
  gemessen ist (Rhino 8.35, CPython 3.9.10). Zugleich die Mindestversion: eine
  ältere Fassung bekommt statt des Fensters eine klare Meldung (``supported``).

Kein ``Rhino``-Import auf Modulebene: der Client wird beim Laden konfiguriert,
bevor irgendetwas anderes geschieht, und die Tests laufen ohne Rhino.
"""

from __future__ import annotations

import os

from . import rendertaxi_client as client

PLUGIN_GUID = "b47606d7-0da9-4f5b-86c4-43f5b32f1865"
PLUGIN_VERSION = "0.2.1"
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


def supported() -> bool:
    """Läuft eine Fassung ab ``HOST_MAJOR``? Nie eine Ausnahme — im Zweifel „nein"."""
    try:
        return int(host_version().split(".")[0]) >= HOST_MAJOR
    except Exception:  # noqa: BLE001 — ein unbekanntes Rhino darf das Laden nicht verhindern
        return False


def unsupported_text() -> str:
    try:
        found = host_version()
    except Exception:  # noqa: BLE001
        found = "unbekannt"
    return (f"{MARK} {PLUGIN_VERSION} braucht Rhino {HOST_MAJOR} oder neuer "
            f"(gefunden: {found}). Bitte die passende Fassung des Plugins verwenden.")


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
