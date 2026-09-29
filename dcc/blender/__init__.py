"""rendertaxi.ai für Blender 5.2 LTS — Extension (RTX-B-002, #176).

Übergibt die aktuelle Ansicht (Viewport) oder das gerenderte Bild (Beauty,
optional mit Datenpässen) als Capture-Manifest 1.1.0 an rendertaxi.ai und
ordnet sie einem Projekt und einem Blickpunkt zu — neu oder als ausdrückliches
Update. Derselbe Weg wie das Archicad-Add-on, derselbe Vertrag, dieselbe
Serverseite (ADR 0009, ADR 0033).

Module:

* ``host``      bindet den gemeinsamen Client an Blender (Schlüssel, Version)
* ``capture``   Viewport, Beauty, Pässe, Laufzeitprobe (``bpy``)
* ``export``    Modellweg: GLB, Kamera, Einheiten (RTX-B-003)
* ``settings``  Einstellungen und Nutzerordner (``bpy``)
* ``ui``        N-Panel und Operatoren (``bpy``)
* ``rendertaxi_client`` der gemeinsame Python-Client aller Python-Hosts —
  Gerätelogin, Transport, Manifest, Ablage, Protokoll, EXR (ADR 0035). Er
  liegt im Repository unter ``integrations/_shared/python`` und wird beim
  Bauen eingebettet.

Nur Python-Standardbibliothek und ``bpy`` — keine Drittpakete.
"""

import bpy

from . import host, settings, ui  # noqa: F401 — host zuerst: setzt den Host des Clients


def register():
    bpy.utils.register_class(settings.Preferences)
    ui.register()


def unregister():
    ui.unregister()
    bpy.utils.unregister_class(settings.Preferences)
