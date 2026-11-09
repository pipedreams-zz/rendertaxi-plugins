"""Geprüfte Fassungen eines Hosts — eine Liste statt einer Zahl (RTX-P-023).

Ein Plugin nennt in seinem ``host.py`` die Fassungen, an denen es geprüft ist
(``TESTED_VERSIONS``, etwa ``(2026, 2027)`` für Cinema 4D). Daraus folgt für die
gefundene Hauptversion genau eines von drei Ergebnissen:

* ``tested``   — eine der geprüften Fassungen: das Plugin läuft ohne Hinweis.
* ``untested`` — neuer als die älteste geprüfte, aber nicht in der Liste (etwa
  Cinema 4D 2028): das Plugin läuft, Fenster und Protokoll sagen „ungeprüft“.
* ``too_old``  — älter als die älteste geprüfte Fassung, oder die Fassung ist
  nicht lesbar: das Plugin lädt, zeigt aber statt des Fensters die Meldung.

Kein Hostmodul, nur Standardbibliothek — die Hosts lesen die gefundene
Hauptversion selbst und geben sie hier herein.
"""

from __future__ import annotations

TESTED = "tested"
UNTESTED = "untested"
TOO_OLD = "too_old"


def state(found_major: int | None, tested: tuple[int, ...]) -> str:
    """``tested``, ``untested`` oder ``too_old`` — nie eine Ausnahme; unlesbar zählt als zu alt."""
    if not tested:
        raise ValueError("Ein Host nennt mindestens eine geprüfte Fassung.")
    if found_major is None:
        return TOO_OLD
    if found_major in tested:
        return TESTED
    return UNTESTED if found_major > min(tested) else TOO_OLD


def listed(tested: tuple[int, ...]) -> str:
    """„2026 und 2027“ — die geprüften Fassungen in einer Zeile, aufsteigend."""
    names = [str(version) for version in sorted(tested)]
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} und {names[-1]}"


def untested_note(label: str, found: str, tested: tuple[int, ...]) -> str:
    """Der Hinweis einer neueren, ungeprüften Fassung — kurz genug für Fenstertitel und Fuß."""
    return f"{label} {found} ist ungeprüft (geprüft: {listed(tested)})."


def too_old_text(mark: str, plugin_version: str, label: str, found: str, tested: tuple[int, ...]) -> str:
    """Die Meldung einer zu alten Fassung."""
    return (f"{mark} {plugin_version} braucht {label} {min(tested)} oder neuer; geprüft sind "
            f"{listed(tested)} (gefunden: {found}). Bitte die passende Fassung des Plugins verwenden.")
