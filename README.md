# rendertaxi.ai-Plugins

Plugins, die eine Ansicht aus dem 3D-Programm als Bild an
[rendertaxi.ai](https://dev.rendertaxi.ai) übergeben und einem Projekt und
Blickpunkt zuordnen. Generierung und Ergebnisbearbeitung bleiben in der
Webanwendung.

## Download

Fertige Zips liegen unter [Releases](../../releases/latest). Die
Webanwendung verlinkt dieselbe Seite unter **Verbundene Geräte › Plugins herunterladen** (`/geraete`).

| Plugin    | Ordner         | Voraussetzung                           | Stand           |
| --------- | -------------- | --------------------------------------- | --------------- |
| Archicad  | `dcc/archicad` | Archicad 28 (macOS universell, Win x64) | verfügbar       |
| Blender   | `dcc/blender`  | Blender 5.2 LTS (macOS, Win, Linux)     | Vorschau        |
| Cinema 4D | `dcc/cinema4d` | Cinema 4D 2026 (macOS, Win)             | Vorschau        |
| Rhino     | `dcc/rhino`    | —                                       | in Vorbereitung |

Jedes Plugin zählt seine Version eigenständig. Releases tragen ein Datum
(`2026.09.26`) und nennen in ihrer Notiz die enthaltenen Versionen.

## Anleitungen

- [Archicad](docs/dcc/archicad.md)
- [Blender](docs/dcc/blender.md)
- [Cinema 4D](docs/dcc/cinema4d.md)

## Herkunft

Dieses Repository ist ein automatischer Spiegel des Plugin-Teils der
rendertaxi.ai-Plattform; Änderungen entstehen dort und werden mit jedem
Release hierher übertragen. Pull Requests hier werden deshalb nicht
übernommen. Fehler und Wünsche bitte als Issue hier melden.

Ein Release baut die Windows-Fassung des Archicad-Add-ons selbst
(`.github/workflows/archicad-windows.yml`) und packt sie mit dem
macOS-Bundle aus `dcc/archicad/dist/macos/` (`scripts/pack.sh`). Die
Blender-Extension und das Cinema-4D-Plugin sind Python und werden ohne Build
aus `dcc/blender/` und `dcc/cinema4d/` gepackt; beide tragen denselben
eingebetteten Python-Client (`rendertaxi_client/`).

## Lizenz

MIT, siehe [LICENSE](LICENSE).
