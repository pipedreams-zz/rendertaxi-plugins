"""rendertaxi.ai für Cinema 4D 2026 — Einstieg des Python-Plugins.

Cinema 4D lädt diese Datei aus dem Plugin-Ordner. Sie macht den Ordner
importierbar und meldet den Befehl **Erweiterungen › rendertaxi.ai** an; alles
Weitere steht im Paket ``rendertaxi_c4d``.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from rendertaxi_c4d import plugin  # noqa: E402


def PluginMessage(id, data):  # noqa: A002 — Name und Signatur gibt Cinema 4D vor
    return plugin.message(id, data)


if __name__ == "__main__":
    plugin.register()
