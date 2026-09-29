"""Registrierung: ein Befehl im Menü **Erweiterungen**, der das Werkzeugfenster öffnet.

Beim Laden geschieht nichts, was an gemerktem Zustand scheitern kann: die
Anmeldung wird erst geprüft, wenn das Fenster zum ersten Mal aufgeht, und dann
so, dass jeder Fehler in „nicht verbunden" endet. Das Fenster ist andockbar
und kommt mit dem Layout zurück (``RestoreLayout``).

Älter als Cinema 4D ``host.HOST_MAJOR``: das Plugin lädt trotzdem, meldet im
Protokoll, dass die Fassung nicht passt, und der Befehl zeigt dieselbe Meldung
(``c4d.gui.MessageDialog``) statt des Fensters — kein Fehler beim Laden.
"""

from __future__ import annotations

import c4d

from . import host
from .rendertaxi_client.log import log, log_exception

TITLE = "rendertaxi.ai"
HELP = "Ansicht oder Rendering an rendertaxi.ai übergeben"


class UnsupportedCommand(c4d.plugins.CommandData):
    """Der Befehl in einer zu alten Fassung: nur die Meldung."""

    def Execute(self, doc) -> bool:
        c4d.gui.MessageDialog(host.unsupported_text())
        return True


class RendertaxiCommand(c4d.plugins.CommandData):
    def __init__(self):
        super().__init__()
        from .c4dhost import Cinema4DAdapter
        from .controller import Controller

        self.controller = Controller(Cinema4DAdapter())
        self.dialog = None
        self._restored = False

    def _dialog(self):
        if self.dialog is None:
            from .dialog import RendertaxiDialog

            self.dialog = RendertaxiDialog(self.controller)
        if not self._restored:
            self._restored = True
            self.controller.restore_session()
        return self.dialog

    def Execute(self, doc) -> bool:
        return self._dialog().Open(c4d.DLG_TYPE_ASYNC, pluginid=host.PLUGIN_ID, defaultw=420, defaulth=640)

    def RestoreLayout(self, sec_ref) -> bool:
        return self._dialog().Restore(pluginid=host.PLUGIN_ID, secret=sec_ref)


COMMAND: RendertaxiCommand | None = None


def register() -> bool:
    """Den Befehl anmelden. Scheitert nur an Cinema 4D selbst, nie an gemerktem Zustand oder alter Fassung."""
    global COMMAND
    if not host.supported():
        log(host.unsupported_text())
        return bool(c4d.plugins.RegisterCommandPlugin(id=host.PLUGIN_ID, str=TITLE, info=0, icon=None,
                                                       help=HELP, dat=UnsupportedCommand()))
    COMMAND = RendertaxiCommand()
    ok = c4d.plugins.RegisterCommandPlugin(id=host.PLUGIN_ID, str=TITLE, info=0, icon=None, help=HELP, dat=COMMAND)
    if not ok:
        log(f"Registrierung abgelehnt (Plugin-ID {host.PLUGIN_ID} schon vergeben?).")
    return bool(ok)


def message(message_id, data) -> bool:
    """``PluginMessage`` der .pyp: beim Beenden einen laufenden Vorgang abbrechen."""
    try:
        if message_id == c4d.C4DPL_ENDACTIVITY and COMMAND is not None:
            COMMAND.controller.shutdown()
    except Exception as error:
        log_exception("Beenden", error)
    return False
