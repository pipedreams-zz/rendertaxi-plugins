"""rendertaxi.ai für Blender 5.2 LTS — Extension (RTX-B-002, #176).

Übergibt die aktuelle Ansicht (Viewport) oder das gerenderte Bild (Beauty,
optional mit Datenpässen) als Capture-Manifest 1.1.0 an rendertaxi.ai und
ordnet sie einem Projekt und einem Blickpunkt zu — neu oder als ausdrückliches
Update. Derselbe Weg wie das Archicad-Add-on, derselbe Vertrag, dieselbe
Serverseite (ADR 0009, ADR 0033).

Module:

* ``auth``      Gerätelogin, Abmelden, „Angemeldet als …"
* ``capture``   Viewport, Beauty, Pässe, Laufzeitprobe (``bpy``)
* ``exr``       einen Pass aus Blenders Mehrschicht-EXR herauslösen
* ``export``    Modellweg — vorgesehene Stelle, heute ohne Inhalt
* ``manifest``  Manifest, ``contentHash``, Schemaprüfung
* ``settings``  Einstellungen, Anmeldungsablage (0600), angefangene Übernahmen
* ``transport`` Plugin API v1 über HTTP, Zustandsautomat der Übernahme
* ``ui``        N-Panel und Operatoren (``bpy``)

Nur Python-Standardbibliothek und ``bpy`` — keine Drittpakete.
"""

import bpy

from . import settings, ui


def register():
    bpy.utils.register_class(settings.Preferences)
    ui.register()


def unregister():
    ui.unregister()
    bpy.utils.unregister_class(settings.Preferences)
