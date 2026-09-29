"""Blender als Host des gemeinsamen Clients (ADR 0033, ADR 0035).

Setzt beim Laden der Extension den ``Host`` des eingebetteten
``rendertaxi_client``: Schlüssel ``blender``, ``client_id``
``ai.rendertaxi.plugin.blender``, die Version aus ``blender_manifest.toml``
(die eine Quelle; ``scripts/pack.sh`` liest sie ebenso) und ``depth`` nie
``present`` (QB-01).
"""

from __future__ import annotations

import os
import tomllib

from . import rendertaxi_client as client

ADDON_DIR = os.path.dirname(os.path.abspath(__file__))
KEY = "blender"
CLIENT_ID = "ai.rendertaxi.plugin.blender"


def _version() -> str:
    with open(os.path.join(ADDON_DIR, "blender_manifest.toml"), "rb") as handle:
        return str(tomllib.load(handle)["version"])


HOST = client.configure(client.Host(
    key=KEY,
    client_id=CLIENT_ID,
    label="Blender",
    plugin_version=_version(),
    roots=(ADDON_DIR,),
    never_present={"depth": "QB-01"},
))
