"""rendertaxi.ai für Cinema 4D 2026 — Python-Plugin (RTX-C4D-002, #186).

Übergibt die aktuelle Ansicht (Viewport Renderer) oder das gerenderte Bild
(Beauty, optional mit Datenpässen als PNG 8 oder 16 Bit) als Capture-Manifest
1.3.0 (gegen einen älteren Server 1.1.0) an rendertaxi.ai
und ordnet sie einem Projekt und einem Blickpunkt zu — neu oder als
ausdrückliches Update. Derselbe Weg wie Archicad und Blender, derselbe Vertrag,
dieselbe Serverseite (ADR 0009, ADR 0035).

Module:

* ``host``        Plugin-ID, Version, Host des gemeinsamen Clients
* ``capture``     Viewport, Beauty, Pässe, Laufzeitprobe, Dokumentkennung (``c4d``)
* ``c4dhost``     Brücke zum Controller: Nutzerordner, Dokument, Statusleiste (``c4d``)
* ``controller``  Zustand, Hintergrundarbeit, Übernahme — ohne ``c4d``
* ``dialog``      das Werkzeugfenster (``c4d.gui.GeDialog``)
* ``plugin``      Registrierung als Befehl im Menü Erweiterungen
* ``rendertaxi_client`` der gemeinsame Python-Client (ADR 0035); liegt im
  Repository unter ``integrations/_shared/python`` und wird beim Bauen
  eingebettet.

Nur Python-Standardbibliothek und ``c4d`` — keine Drittpakete, kein Build.
"""
