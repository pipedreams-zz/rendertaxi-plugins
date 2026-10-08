"""rdtx.ai für Rhino 8 — Einstieg des Befehls ``RdtxAI`` (``commands/RdtxAI.py``).

``main()`` öffnet das Fenster (``panel.RdtxForm``) oder holt das offene nach vorn. Das Fenster lebt in
``scriptcontext.sticky``: ein erneuter Befehl öffnet kein zweites.

Protokoll: jede Zeile des Clients geht zusätzlich zur Konsole in die Datei ``rhino.log`` im Nutzerordner
(0600, ab 1 MB eine Vorgängerdatei) und auf die Rhino-Kommandozeile (``log.add_sink``) — Rhino tauscht
``stderr`` je Skriptlauf aus, ein Abzweig dort ginge verloren. Der Client
schreibt nie Token, Pfade oder Namen (``rendertaxi_client/log.py``); die Datei erbt das.

Regel 3: kein gemerkter Zustand verhindert das Öffnen — ein Fehler beim Aufbau wird eine Meldung auf der
Kommandozeile, nie ein Absturz von Rhino.
"""

from __future__ import annotations

import datetime
import os

from . import host  # noqa: F401 — konfiguriert den Client als Erstes

STICKY = "rdtx.ai.rhino"
LOG_FILE = "rhino.log"
LOG_LIMIT = 1024 * 1024


class FileLog:
    """Empfänger des Client-Protokolls (``log.add_sink``): Datei ``rhino.log`` (0600) und Kommandozeile."""

    def __init__(self, path: str):
        self.path = path

    def __call__(self, line: str) -> None:
        stamped = f"{datetime.datetime.now().isoformat(timespec='seconds')} {line}"
        try:
            if os.path.exists(self.path) and os.path.getsize(self.path) > LOG_LIMIT:
                os.replace(self.path, self.path + ".1")
            descriptor = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
                handle.write(stamped + "\n")
        except OSError:
            pass
        try:
            import Rhino

            Rhino.RhinoApp.WriteLine(f"{host.MARK}: {line[len('rendertaxi:'):].strip()}")
        except Exception:  # noqa: BLE001 — ohne Rhino (Tests) nur die Datei
            pass


_LOG: dict = {}


def install_log(directory: str) -> str:
    """Das Dateiprotokoll einmal je Prozess einhängen; gibt den Pfad der Protokolldatei zurück."""
    from .rendertaxi_client.log import add_sink

    path = os.path.join(directory, LOG_FILE)
    sink = _LOG.get("sink")
    if sink is None:
        sink = _LOG["sink"] = FileLog(path)
        add_sink(sink)
    sink.path = path
    return path


def _sticky() -> dict:
    import scriptcontext

    return scriptcontext.sticky


def main() -> None:
    """Befehl ``RdtxAI``: das Fenster öffnen oder nach vorn holen."""
    import Rhino

    try:
        if not host.supported():
            Rhino.RhinoApp.WriteLine(host.unsupported_text())
            return
        from . import panel, rhinohost
        from .controller import Controller
        from .rendertaxi_client.log import log

        sticky = _sticky()
        form = sticky.get(STICKY)
        if form is not None:
            try:
                form.BringToFront()
                return
            except Exception:  # noqa: BLE001 — ein geschlossenes Fenster wird neu geöffnet
                sticky[STICKY] = None
        try:
            install_log(rhinohost.user_dir())
        except OSError:
            pass  # ohne Nutzerordner bleibt die Konsole; das Fenster sagt beim Verbinden, was fehlt
        controller = Controller(rhinohost.RhinoAdapter())
        form = panel.RdtxForm(controller)
        form.Owner = Rhino.UI.RhinoEtoApp.MainWindow
        form.Closed += lambda *_: sticky.__setitem__(STICKY, None)
        form.Show()
        sticky[STICKY] = form
        log(f"Fenster geöffnet ({host.PLUGIN_VERSION}, {host.build_text()}, Rhino {host.host_version()})")
        controller.restore_session()
        form.refresh()
    except Exception as error:  # noqa: BLE001 — nie ein Absturz von Rhino
        from .rendertaxi_client.log import log_exception

        log_exception("Fenster ließ sich nicht öffnen", error)
        Rhino.RhinoApp.WriteLine(f"{host.MARK}: Das Fenster ließ sich nicht öffnen ({type(error).__name__}).")
