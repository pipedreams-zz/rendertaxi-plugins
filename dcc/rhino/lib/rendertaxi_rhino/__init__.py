"""rdtx.ai für Rhino 8 — Einstieg des Befehls ``RdtxAI`` (``commands/RdtxAI.py``).

``main()`` öffnet die Palette als Rhino-Panel (``dock.py``, RTX-RH-004), holt sie nach vorn oder blendet sie
aus, wenn sie schon der sichtbare Reiter ist. Eine ``Session`` je Rhino-Prozess hält den ``Controller`` in
``scriptcontext.sticky``; jedes Panel, das Rhino erzeugt (je Dokumentfenster eines), zeichnet ihn
(``panel.RdtxView``). Nimmt Rhino das Panel nicht an, öffnet dieselbe Palette als schwebendes Fenster.

Protokoll: jede Zeile des Clients geht zusätzlich zur Konsole in die Datei ``rhino.log`` im Nutzerordner
(0600, ab 1 MB eine Vorgängerdatei) und auf die Rhino-Kommandozeile (``log.add_sink``) — Rhino tauscht
``stderr`` je Skriptlauf aus, ein Abzweig dort ginge verloren. Der Client
schreibt nie Token, Pfade oder Namen (``rendertaxi_client/log.py``); die Datei erbt das.

Regel 3: kein gemerkter Zustand verhindert das Öffnen — ein Fehler beim Aufbau wird eine Meldung auf der
Kommandozeile oder ein Satz im Panel, nie ein Absturz von Rhino.
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


class Session:
    """Der eine ``Controller`` je Rhino-Prozess und die Paletten, die ihn zeichnen.

    Eine Palette, die Rhino nur entlädt (Reiter ausgeblendet, Leiste zu), bleibt registriert und zeichnet nach dem
    nächsten ``Load`` wieder mit (Review F-01). Erst eine entsorgte Palette (``RdtxView.disposed``: Panel
    ``IsDisposed`` oder Fenster geschlossen) fällt heraus, ihr Zeitgeber steht.
    """

    def __init__(self, controller):
        self.controller = controller
        self._views = []
        self.window = None

    def add(self, view) -> None:
        if view not in self._views:
            self._views.append(view)

    def views(self) -> list:
        """Die geladenen Paletten; entsorgte werden dabei vergessen."""
        kept = []
        for view in self._views:
            if view.disposed():
                view.timer.Stop()
            else:
                kept.append(view)
        self._views = kept
        return [view for view in kept if view.alive]

    def build(self, container) -> None:
        """Inhalt eines Panels, das Rhino erzeugt — ein Fehler wird ein Satz im Panel (Regel 3)."""
        try:
            from . import panel

            self.add(panel.RdtxView(self.controller, container, self.views))
        except Exception as error:  # noqa: BLE001
            from .rendertaxi_client.log import log_exception

            log_exception("Palette ließ sich nicht aufbauen", error)
            try:
                import Eto.Forms as forms

                label = forms.Label()
                label.Text = f"Die Palette ließ sich nicht aufbauen ({type(error).__name__}). Einzelheiten im Protokoll."
                container.Content = label
            except Exception:  # noqa: BLE001
                pass

    def shutdown(self, *_args) -> None:
        try:
            self.controller.shutdown()
        except Exception:  # noqa: BLE001
            pass


def _session():
    """Die ``Session`` aus ``sticky`` oder eine neue — mit Protokoll, gemerkter Anmeldung und Abbruch beim Beenden."""
    import Rhino

    from . import rhinohost
    from .controller import Controller
    from .rendertaxi_client.log import log

    sticky = _sticky()
    session = sticky.get(STICKY)
    if isinstance(session, Session):
        return session
    try:
        install_log(rhinohost.user_dir())
    except OSError:
        pass  # ohne Nutzerordner bleibt die Konsole; die Palette sagt beim Verbinden, was fehlt
    session = Session(Controller(rhinohost.RhinoAdapter()))
    sticky[STICKY] = session
    try:
        Rhino.RhinoApp.Closing += session.shutdown
    except Exception:  # noqa: BLE001 — ohne das Ereignis bricht Rhino eine Übertragung beim Beenden selbst ab
        pass
    log(f"Palette bereit ({host.PLUGIN_VERSION}, {host.build_text()}, Rhino {host.host_version()})")
    session.controller.restore_session()
    return session


def _window(session) -> None:
    """Ausweg ohne Panel: die Palette als schwebendes Fenster; ein zweiter Befehl holt es nach vorn."""
    import Rhino

    from . import panel

    if session.window is not None:
        try:
            session.window.BringToFront()
            return
        except Exception:  # noqa: BLE001 — ein geschlossenes Fenster wird neu geöffnet
            session.window = None
    view = panel.RdtxView(session.controller, None, session.views)
    session.add(view)
    window = panel.RdtxWindow(view)
    window.Owner = Rhino.UI.RhinoEtoApp.MainWindow

    def closed(*_):
        view.close()
        session.window = None

    window.Closed += closed
    window.Show()
    session.window = window


def main() -> None:
    """Befehl ``RdtxAI``: das Panel öffnen, nach vorn holen oder ausblenden."""
    import Rhino

    try:
        if not host.supported():
            Rhino.RhinoApp.WriteLine(host.unsupported_text())
            return
        from . import dock, rhinohost
        from .rendertaxi_client.log import log, log_exception

        session = _session()
        try:
            dock.panel_type(session.build)
            shown = dock.show(rhinohost.user_dir())
        except Exception as error:  # noqa: BLE001 — Panel nicht angenommen: Fenster statt nichts
            log_exception("Panel ließ sich nicht registrieren", error)
            shown = "hidden"
        if shown == "hidden":
            log("Panel nicht sichtbar — Palette als Fenster")
            _window(session)
        for view in session.views():
            view.refresh()
    except Exception as error:  # noqa: BLE001 — nie ein Absturz von Rhino
        from .rendertaxi_client.log import log_exception

        log_exception("Palette ließ sich nicht öffnen", error)
        Rhino.RhinoApp.WriteLine(f"{host.MARK}: Die Palette ließ sich nicht öffnen ({type(error).__name__}).")
