"""Protokoll — Kennungen und Zustände, nie Token, Pfade oder Namen.

``message`` trägt Kennungen und Zustände (``captureId``, Rolle, Zustand,
Fehlercode, Bytegröße) — nie ein Token, eine signierte URL, einen Pfad oder
einen Projekt-, Ansichts- oder Dateinamen (``plugin-api-v1-security.md``,
Abschnitt 5). Das gilt auch mit Debug-Schalter.
"""

from __future__ import annotations

import os
import sys
import traceback

from . import CLIENT_DIR, _CURRENT

_DEBUG = {"enabled": False}


def set_debug(enabled: bool) -> None:
    _DEBUG["enabled"] = bool(enabled)


def debug_enabled() -> bool:
    return _DEBUG["enabled"]


def log(message: str) -> None:
    """Eine Zeile auf die Konsole des Hosts (``stderr``)."""
    print(f"rendertaxi: {message}", file=sys.stderr)


def _roots() -> tuple[str, ...]:
    host = _CURRENT["host"]
    extra = tuple(os.path.abspath(root) for root in (host.roots if host else ()))
    return (CLIENT_DIR, *extra)


def _inside(filename: str, roots: tuple[str, ...]) -> bool:
    for root in roots:
        try:
            if os.path.commonpath([root, filename]) == root:
                return True
        except ValueError:  # anderes Laufwerk (Windows)
            continue
    return False


def describe_exception(error: BaseException) -> str:
    """Eine Ausnahme ohne vertrauliche Werte: Typ, ``errno`` und Codestellen des Plugins.

    Der Ausnahmetext fehlt absichtlich — ein ``OSError`` trägt dort den
    absoluten Pfad, andere Ausnahmen Projekt- oder Dateinamen. Codestellen
    außerhalb des Plugins und dieses Clients (Standardbibliothek, Host, Skripte
    in einer Szene) erscheinen nur als ``…``, weil schon ihr Dateiname ein
    Kundenname sein kann. Innerhalb erscheint nur der Dateiname, nie der Pfad.
    """
    parts = [type(error).__name__]
    number = getattr(error, "errno", None)
    if isinstance(number, int):
        parts.append(f"errno {number}")
    roots = _roots()
    frames = []
    for frame in traceback.extract_tb(error.__traceback__):
        filename = os.path.abspath(frame.filename or "")
        if _inside(filename, roots):
            frames.append(f"{os.path.basename(filename)}:{frame.lineno} {frame.name}")
        elif not frames or frames[-1] != "…":
            frames.append("…")
    if frames:
        parts.append("bei " + " → ".join(frames))
    return ", ".join(parts)


def log_exception(context: str, error: BaseException) -> None:
    """Eine unerwartete Ausnahme protokollieren — nie mit Traceback oder Ausnahmetext.

    Ohne Debug-Schalter nur ``context`` und der Ausnahmetyp; mit Schalter dazu
    ``errno`` und die Codestellen im Plugin (``describe_exception``).
    """
    detail = describe_exception(error) if _DEBUG["enabled"] else type(error).__name__
    log(f"{context} ({detail})")
