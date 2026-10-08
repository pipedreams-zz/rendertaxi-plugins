# Prototyp: GLB aus dem 3D-Fenster (RTX-A-010, #256)

**Messauftrag, nicht im veröffentlichten Add-on.** Dieser Ordner wird nur mit
`-DRTX_SPIKE_MODEL_GLB=ON` übersetzt. `scripts/build.sh`, `scripts/dist.sh` und
der Windows-Workflow setzen die Option nicht. Das veröffentlichte Add-on bleibt
deshalb unverändert, so verlangt es Regel 2 des Auftrags.

Ergebnisse und Empfehlung stehen in
[`../../../docs/model-glb-spike.md`](../../../docs/model-glb-spike.md).

## Inhalt

| Datei                    | Zweck                                                                                 |
| ------------------------ | ------------------------------------------------------------------------------------- |
| `GlbWriter.hpp/.cpp`     | DevKit-freier GLB-Schreiber: Dreiecke im glTF-Raum, Materialien, Kameras mit `extras` |
| `glb_selftest.cpp`       | Würfel und Kamera ohne Archicad, für Validator und Server-Prüfer                      |
| `ModelGlbSpike.hpp/.cpp` | JSON-Befehle im Add-on (siehe unten)                                                  |
| `tools/ac.py`            | Aufruf der Archicad-JSON-Schnittstelle (Port 19723)                                   |
| `tools/rebuild.sh`       | Bauen, installieren, eigene Testinstanz neu starten, Testszene anlegen                |
| `tools/instance.sh`      | Nachweis der eigenen Testinstanz (PID und Startzeit), Entscheidung vor dem Neustart   |
| `tools/test_instance.sh` | Test der Prozessauswahl mit simulierten Prozessen                                     |
| `tools/scene.py`         | Testszene mit bekannten Maßen (Tapir-Befehle)                                         |
| `tools/cameras.py`       | Gespeicherte 3D-Ansichten mit bekannten Kameras                                       |
| `tools/export.py`        | `rendertaxi.SpikeModelGlb` aufrufen, Kernzahlen ausgeben                              |
| `tools/overlay.py`       | Ecken der Testszene mit der glTF-Kamera ins Archicad-Bild rechnen                     |
| `tools/visibility.py`    | Frage 1: Geometrie unter Ebene, Schnittebene, Ansichtseinstellungen                   |

## JSON-Befehle

Die Befehle laufen über `API.ExecuteAddOnCommand` im Namensraum `rendertaxi`:

- `SpikeModelGlb` mit `path`, `granularity` (`element` | `material`),
  `includeGuids` und `cameras` (`none` | `current` | `saved`). Schreibt die GLB
  und daneben `<path>.json` mit Zählungen, Zeiten, Bounding Box, Materialien und
  den rohen Projektionswerten jeder Kamera.
- `SpikeReadCamera` liefert die rohe Projektion des 3D-Fensters.
- `SpikeSetCamera` setzt die Perspektive des 3D-Fensters, wahlweise auch die
  Fenstergröße (`hSize`, `vSize`).
- `SpikeCutPlanes` schaltet die 3D-Schnittebenen ein oder aus.

## Wiederholen (macOS, Archicad 28, Tapir-Add-on)

```bash
integrations/archicad/addon/spike/model-glb/tools/rebuild.sh
```

```bash
python3 -I integrations/archicad/addon/spike/model-glb/tools/cameras.py
```

```bash
python3 -I integrations/archicad/addon/spike/model-glb/tools/export.py /tmp/szene.glb cameras=saved
```

Danach prüfen der Khronos-Validator und der Server-Prüfer die Datei:
`docs/verification/rtx-mod-001-modellerstellung/tools/gltf-validator.mts` und
`server-pruefung.mts`.

`rebuild.sh` beendet **nur die eigene Testinstanz** (F-01 an PR #305). Beim Start
schreibt es PID und Startzeit der neuen Instanz nach
`$RTX_BUILD_ROOT/testinstanz.pid`. Beim nächsten Lauf beendet es genau den Prozess,
der mit dieser PID **und** dieser Startzeit läuft. Seine ungesicherte Testszene ist
gewollt wegwerfbar.

Läuft eine andere Archicad-Instanz, etwa ein Kundenprojekt, eine Instanz aus einem
früheren Lauf ohne Startdatei oder eine wiederverwendete PID, dann bekommt sie
**kein Signal**. Das Skript bricht ab und bittet, Archicad selbst zu sichern und zu
beenden.

Vom Fehlerbericht `GSReport` beendet es nur den Bericht zur eigenen PID, damit
nichts gesendet wird. Die neue Testinstanz startet mit `-disablerecoverydialog`,
denn nach dem Abbruch hängt Archicad sonst modal in `Init::InitFileEnvir`.

Die Prozessauswahl prüft `tools/test_instance.sh` mit simulierten eigenen und
fremden Prozessen, ohne ein Signal zu senden:

```bash
integrations/archicad/addon/spike/model-glb/tools/test_instance.sh
```

## Aufräumen

`rebuild.sh` ersetzt das installierte rendertaxi-Add-on durch den Spike-Build.
Danach das veröffentlichte Add-on wieder installieren:

```bash
integrations/archicad/addon/scripts/build.sh
```

```bash
integrations/archicad/addon/scripts/install.sh
```
