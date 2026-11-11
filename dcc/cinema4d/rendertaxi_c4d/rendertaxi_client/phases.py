"""Benannte Phasen einer langen Arbeit — angekündigt, **bevor** sie beginnt, mit Laufzeit im Protokoll (RTX-C4D-013).

Ein Host, der eine Phase im Hauptfaden ausführen muss (Cinema 4D: ``Polygonize``, ``SaveDocument``), friert für ihre
Dauer ein. Dann soll der Nutzer wenigstens vorher lesen, was jetzt geschieht: ``Phases.begin`` meldet den Namen über
``report(text, percent)`` (Fenster und Statusleiste) und ruft ``redraw()`` — der Host zeichnet sein Fenster neu, bevor
die Phase beginnt. Das Ende einer Phase schreibt ihre Laufzeit ins **ausführliche** Protokoll, als Zeile
``Phase „<Name>“: <Sekunden> s`` — ohne Pfad, ohne Namen aus der Szene. ``phase_times`` liest diese Zeilen wieder
(Messung am Host: das Protokoll einfügen, die Zeiten je Phase herauslesen).

Kein Hostmodul, keine Annahme über Fäden: ``report`` und ``redraw`` entscheiden, wohin die Meldung geht.
"""

from __future__ import annotations

import re
import time

from .log import debug_enabled, log

# Die Wortwahl der Phasen — eine Stelle für Fenster, Statusleiste, Protokoll und Tests.
POLYGONIZE = "Polygonisieren"
WRITE_GLB = "GLB schreiben"
MATERIALS = "Zusammenführen und Texturen"
CHECKSUM = "Prüfsumme"
MANIFEST = "Aufnahme beschreiben"  # das Manifest — „Manifest“ ist kein Wort der Oberfläche
# Der Zusatz, wenn die Phase im Hauptfaden laufen muss und das Fenster für ihre Dauer nicht reagiert.
BLOCKING_NOTE = " — das Fenster reagiert kurz nicht"

_LINE = re.compile(r"Phase „(?P<name>[^“]+)“: (?P<seconds>[0-9]+(?:\.[0-9]+)?) s")


class Phases:
    """Eine Folge benannter Phasen: ``begin`` kündigt an, das nächste ``begin`` oder ``end`` misst die vorige.

    ``report(text, percent)``: wohin die Ankündigung geht. ``redraw()``: zeichnet das Fenster neu (darf fehlen).
    Ein Fehler in ``redraw`` hält keine Arbeit auf.
    """

    def __init__(self, report, redraw=None, clock=time.monotonic):
        self.report = report
        self.redraw = redraw
        self.clock = clock
        self.current: str | None = None
        self.started = 0.0
        self.times: list[tuple[str, float]] = []

    def begin(self, name: str, percent: int, *, blocking: bool = False) -> None:
        self.end()
        self.report(name + (BLOCKING_NOTE if blocking else ""), percent)
        if self.redraw is not None:
            try:
                self.redraw()
            except Exception:  # noqa: BLE001 — das Neuzeichnen ist eine Höflichkeit, keine Bedingung
                pass
        self.current = name
        self.started = self.clock()

    def end(self) -> None:
        if self.current is None:
            return
        seconds = max(0.0, self.clock() - self.started)
        self.times.append((self.current, seconds))
        if debug_enabled():
            log(f"Phase „{self.current}“: {seconds:.2f} s")
        self.current = None


def phase_times(lines) -> list[tuple[str, float]]:
    """Die Phasen und ihre Sekunden aus den Zeilen eines ausführlichen Protokolls — in ihrer Reihenfolge.

    Andere Zeilen zählen nicht; eine Zeile kann das Präfix ``rendertaxi:`` tragen oder nicht.
    """
    found = []
    for line in lines:
        match = _LINE.search(str(line))
        if match:
            found.append((match.group("name"), float(match.group("seconds"))))
    return found
