"""Ziel der Übernahme und Rahmen des Blickpunkts — ADR 0024, für jeden Host gleich.

* ``target`` baut aus der Wahl im Plugin das gespeicherte Ziel (Projekt,
  neuer oder bestehender Blickpunkt, ``frame``, ``size``). Was davon an den
  Server geht, entscheidet ``transport.target_body``.
* ``viewpoint_size`` rechnet das Ausgabeziel eines Blickpunkts
  (``desired`` aus ``viewpoint-overview``) in Pixel um.
* ``size_text`` und ``aspect_hint`` sind die Sätze, die das Plugin zur
  Rahmengröße zeigt: weicht bei „Rahmen behalten" das Seitenverhältnis ab, sagt
  das Plugin es, statt es still aufzulösen.
"""

from __future__ import annotations

CREATE = "create"
UPDATE = "update"
SIZE_CANVAS_DEFAULT = "canvas-default"
SIZE_CAPTURE = "capture"
ASPECT_TOLERANCE = 0.005


def target(project_id: str | None, mode: str, *, name: str = "", viewpoint_id: str | None = None,
           fit_to_capture: bool = False, size: str = SIZE_CANVAS_DEFAULT) -> dict:
    """Das Ziel einer Übernahme — oder ``ValueError`` mit dem, was noch fehlt."""
    if not project_id or project_id == "NONE":
        raise ValueError("Bitte ein Projekt wählen.")
    if size not in (SIZE_CANVAS_DEFAULT, SIZE_CAPTURE):
        raise ValueError(f"Unbekannte Rahmengröße: {size}")
    if mode == CREATE:
        name = (name or "").strip()
        if not name:
            raise ValueError("Bitte einen Namen für den neuen Blickpunkt eingeben.")
        viewpoint = {"mode": CREATE, "name": name, "size": size}
    elif mode == UPDATE:
        if not viewpoint_id or viewpoint_id == "NONE":
            raise ValueError("Bitte den Blickpunkt wählen, der aktualisiert wird.")
        viewpoint = {
            "mode": UPDATE,
            "viewpointId": viewpoint_id,
            "frame": "fit-to-capture" if fit_to_capture else "keep",
            "size": size,
        }
    else:
        raise ValueError(f"Unbekanntes Ziel: {mode}")
    return {"projectId": project_id, "viewpoint": viewpoint}


def viewpoint_size(desired: dict | None, current: tuple[int, int]) -> tuple[int, int] | None:
    """Die Größe des Blickpunkt-Rahmens — ``exact`` direkt, ``aspect_ratio`` mit der langen Kante der Szene."""
    if not isinstance(desired, dict):
        return None
    if desired.get("kind") == "exact":
        width, height = int(desired.get("width", 0)), int(desired.get("height", 0))
        return (width, height) if width > 0 and height > 0 else None
    if desired.get("kind") == "aspect_ratio":
        try:
            a, b = (int(x) for x in str(desired.get("value", "")).split(":"))
        except ValueError:
            return None
        if a <= 0 or b <= 0:
            return None
        long_edge = max(current)
        return (long_edge, max(1, round(long_edge * b / a))) if a >= b else (max(1, round(long_edge * a / b)), long_edge)
    return None


def size_text(creating: bool, fit_to_capture: bool, size: str) -> str:
    """Was die gewählte Rahmengröße bewirkt — ``size`` wirkt nur, wo ADR 0024 es vorsieht."""
    follows = creating or fit_to_capture
    if size == SIZE_CAPTURE and follows:
        return "Zielgröße: Aufnahmemaße (unter 1536 langer Kante bleibt die Canvas-Vorgabe)"
    if size == SIZE_CAPTURE:
        return "Zielgröße: Aufnahmemaße — wirkt erst mit „Rahmen an Aufnahme anpassen“"
    return "Zielgröße: Canvas-Vorgabe — nur das Seitenverhältnis der Aufnahme"


def aspect_hint(frame: tuple[int, int] | None, size: tuple[int, int], *, frame_option: str,
                size_option: str | None) -> str | None:
    """Der Hinweis bei „Rahmen behalten", wenn der Rahmen ein anderes Seitenverhältnis hat als die Aufnahme.

    ``frame_option`` und ``size_option`` sind die Beschriftungen der beiden
    Auswege im Plugin („Rahmen an Aufnahme anpassen", „Blickpunkt-Rahmen").
    ``size_option`` ``None``: es gibt nur den ersten — eine Aufnahme nur mit
    Modell hat kein Bild, das in Rahmengröße aufgenommen würde (RTX-P-014).
    """
    if frame is None:
        return None
    if abs(frame[0] / frame[1] - size[0] / size[1]) <= ASPECT_TOLERANCE:
        return None
    ways_out = f"„{frame_option}“ oder Größe „{size_option}“ wählen." if size_option else f"„{frame_option}“ wählen."
    return (f"Der Rahmen des Blickpunkts ({frame[0]} × {frame[1]}) hat ein anderes Seitenverhältnis "
            f"als die Aufnahme ({size[0]} × {size[1]}). {ways_out}")
