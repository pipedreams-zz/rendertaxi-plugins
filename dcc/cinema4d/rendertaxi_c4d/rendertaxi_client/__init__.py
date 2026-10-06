"""rendertaxi_client — der eine Python-Client der Plugin API v1 für alle Python-Hosts.

Blender (``integrations/blender/addon/``) und Cinema 4D
(``integrations/cinema-4d/plugin/``) benutzen genau dieses Paket; es wird beim
Bauen in ihre Zip eingebettet (``integrations/_shared/python/vendor.py``) und
liegt im Repository **nur hier**. Entscheidung und Begründung: ADR 0035.

Module:

* ``auth``      Gerätelogin nach RFC 8628, Abmelden, „Angemeldet als …"
* ``transport`` Plugin API v1 über HTTP, Zustandsautomat der Übernahme
* ``manifest``  Capture-Manifest, ``contentHash``, Schemaprüfung, Grenzen, PNG-Kopf
* ``frame``     Ziel der Übernahme, Rahmengröße und Seitenverhältnis (ADR 0024)
* ``ways``      Bildweg und Modellweg: was gesendet wird, Rückfall, Schritte und Ergebnis je Weg
* ``store``     Anmeldung (0600), angefangene Übernahmen, Einstellungen
* ``log``       Protokoll ohne Token, Pfade und Namen
* ``glb``       exportierte GLB lesen: Dreiecke wie der Server, Hülle, Maßstab auf Meter setzen
* ``schema/``   Kopie der drei Schemas aus ``integrations/_shared/contracts/v1``

Nur Python-Standardbibliothek, Python ≥ 3.11, kein Hostmodul (``bpy``,
``c4d``). Was je Host verschieden ist — Schlüssel, ``client_id``, Version,
welche Rolle nie ``present`` sein darf —, steht in einem ``Host``, den das
Plugin beim Laden **einmal** mit ``configure`` setzt. Weil jedes Plugin seine
eigene eingebettete Kopie lädt (``bl_ext.….rendertaxi.rendertaxi_client``,
``rendertaxi_c4d.rendertaxi_client``), teilen sich zwei Plugins nie diesen
Zustand.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Host:
    """Was den Client an einen Host bindet.

    ``roots`` sind die Ordner, deren Codestellen das Protokoll mit Debug-Schalter
    nennen darf (``log.describe_exception``) — der Ordner des Plugins; der
    Client selbst gehört immer dazu. ``never_present`` nennt je Rolle die offene
    Frage, die ``status: present`` verbietet (Blender: ``depth`` wegen QB-01).
    """

    key: str
    client_id: str
    label: str
    plugin_version: str
    roots: tuple[str, ...] = ()
    never_present: dict[str, str] = field(default_factory=dict)


CLIENT_DIR = os.path.dirname(os.path.abspath(__file__))
_CURRENT: dict[str, Host | None] = {"host": None}


def configure(host: Host) -> Host:
    """Den Host setzen — einmal beim Laden des Plugins (die Tests setzen ihn je Lauf)."""
    _CURRENT["host"] = host
    return host


def current() -> Host:
    host = _CURRENT["host"]
    if host is None:
        raise RuntimeError("rendertaxi_client ist nicht konfiguriert (configure(Host(...)) beim Laden des Plugins).")
    return host
