"""Bildweg und Modellweg — zwei Wege, einzeln oder zusammen (RTX-P-014, capture-manifest.md Abschnitt 14).

Nutzervorgabe vom 05.10.2026: „Bild- und Modellweg sind als zwei verschiedene Wege zu sehen, beide
können einzeln, aber auch kombiniert laufen." Drei gleichwertige Fälle:

* **nur Bild** — Ansicht oder Rendering mit Datenpässen; setzt am Blickpunkt Basisbild und Pässe.
* **nur Modell** — GLB und Kamera, **ohne Rendern**; setzt Modell und Kamera. Erst ab Capture-Manifest
  1.6.0; die Kamera nennt dann ihre Bildgröße (``camera.resolution``).
* **Bild und Modell** — beides in einer Übernahme.

Jeder Weg ändert am Blickpunkt nur das Seine: ein Update nur mit Modell lässt das Bild stehen, eines nur
mit Bild das Modell. Das entscheidet der Server; hier steht, **was gesendet wird** (``plan``), wie es im
Plugin heißt (``summary``, ``steps``, ``role_label``) und was angekommen ist (``result_text``).

**Ein gemerkter Zustand darf das Laden nie verhindern** (Regel 3): eine gemerkte Wahl „nur Modell" gegen
einen Server, der 1.6.0 nicht umsetzt, bricht nichts ab — sie fällt mit einem Hinweis auf „Bild und
Modell" zurück, und das läuft wie bisher.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import manifest as mf

IMAGE = "image"
MODEL = "model"

# Wie die Rollen im Plugin heißen — Nutzersprache statt Vertragsschlüssel.
ROLE_LABELS = {
    "viewport": "Ansicht",
    "beauty": "Rendering",
    "depth": "Tiefe",
    "normal": "Normalen",
    "albedo": "Albedo",
    "mask": "Maske",
    "object-id": "Objekt-ID",
    "material-id": "Material-ID",
    "ambient-occlusion": "Umgebungsverdeckung",
    "cryptomatte": "Cryptomatte",
    mf.MODEL_ROLE: "Modell",
}

NOTHING_CHOSEN = "Bitte Bild, Modell oder beides zum Senden wählen."
MODEL_ONLY_FALLBACK = "Dieser Server nimmt eine Aufnahme nur mit Modell noch nicht an; gesendet werden Bild und Modell."


@dataclass(frozen=True)
class Plan:
    """Was eine Übernahme sendet — und warum es anders ist als gewählt (``hint``), wenn es das ist."""

    image: bool
    model: bool
    hint: str | None = None

    @property
    def model_only(self) -> bool:
        return self.model and not self.image


def plan(send_image: bool, send_model: bool, handshake: dict | None) -> Plan:
    """Die Wahl im Plugin gegen das, was der Server annimmt — ``ValueError`` nur, wenn nichts gewählt ist.

    ``handshake`` ``None`` heißt „noch nicht verbunden": dann gilt die Wahl, wie sie ist. Ob der Server ein
    Modell überhaupt annimmt (ab 1.2.0), prüft der Host wie bisher an seiner Modellfrage; hier geht es nur
    um die Aufnahme **ohne** Bild.
    """
    if not send_image and not send_model:
        raise ValueError(NOTHING_CHOSEN)
    if send_model and not send_image and handshake is not None and not mf.model_only_allowed(handshake):
        return Plan(image=True, model=True, hint=MODEL_ONLY_FALLBACK)
    return Plan(image=bool(send_image), model=bool(send_model))


def label(chosen: Plan) -> str:
    """Die Wege einer Übernahme in zwei, drei Wörtern: „Bild", „Modell" oder „Bild und Modell"."""
    if chosen.model_only:
        return "Modell"
    return "Bild und Modell" if chosen.model else "Bild"


def summary(chosen: Plan) -> str:
    """Was gesendet wird, in einem Satz — der Bereich „Übernehmen" zeigt ihn über dem Knopf."""
    if chosen.model_only:
        return "Gesendet wird: nur Modell und Kamera — ohne Rendern."
    if chosen.model:
        return "Gesendet werden: Bild und Modell."
    return "Gesendet wird: nur Bild."


def steps(chosen: Plan) -> list[str]:
    """Die Schritte einer Übernahme in Nutzersprache — je Weg, in der Reihenfolge, in der sie laufen."""
    labels = []
    if chosen.image:
        labels.append("Bild aufnehmen")
    if chosen.model:
        labels += ["Modell exportieren", "Kamera lesen"]
    if chosen.image:
        labels.append("Bild übertragen")
    if chosen.model:
        labels.append("Modell übertragen")
    labels.append("Übernahme abschließen")
    return labels


def role_label(role: str) -> str:
    return ROLE_LABELS.get(role, role)


def transfer_step(role: str) -> str:
    """Der Fortschritt beim Übertragen einer Datei — der Weg und, für ein Bild, welches."""
    if role == mf.MODEL_ROLE:
        return "Modell übertragen"
    return f"Bild übertragen ({role_label(role)})"


def ways_of(roles) -> Plan:
    """Welche Wege eine Rollenliste trägt — ein Manifest (``present``) oder das Ergebnis (``assetIds``)."""
    roles = set(roles)
    return Plan(image=any(role != mf.MODEL_ROLE for role in roles), model=mf.MODEL_ROLE in roles)


def ways_of_manifest(manifest: dict) -> Plan:
    return ways_of(a.get("role") for a in manifest.get("assets", []) if a.get("status") == "present")


def result_text(result: dict | None, update: bool) -> str:
    """Was angekommen ist — aus dem Abschlussbeleg des Servers (``result.assetIds`` je Rolle), je Weg.

    Bei einem Update sagt der Satz zusätzlich, was am Blickpunkt **stehen geblieben** ist.
    """
    arrived = ways_of((result or {}).get("assetIds") or {})
    if arrived.model_only:
        return "Modell und Kamera übernommen." + (" Ein Bild am Blickpunkt bleibt, wie es ist." if update else "")
    if arrived.model:
        return "Bild und Modell übernommen."
    if arrived.image:
        return "Bild übernommen." + (" Ein Modell am Blickpunkt bleibt, wie es ist." if update else "")
    return "Übernahme abgeschlossen."
