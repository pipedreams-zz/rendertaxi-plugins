# Messszene „Materialien und Texturen“ (RTX-P-021)

Dieselbe Szene in jedem Host. Sie belegt die Materialregel aus
[`capture-manifest.md`, Abschnitt 11.6](../../docs/capture-manifest.md) am echten Exporter.
Die Texturen schreibt [`textures.py`](textures.py) (nur Standardbibliothek, deterministisch).
Im Repository liegen `holz.png` und `ziegel.png`. `ziegel-gross.png` entsteht erst bei der
Messung, weil es mit 2400 × 2400 Pixeln und etwa 6 MB über der Grenze liegt.

| Objekt      | Material                                            | Was es prüft                                                                                |
| ----------- | --------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| Tisch       | Holz: Diffustextur `holz.png` auf UV                | Textur bis 2 MB, eingebettet                                                                |
| Tisch Kopie | Holz                                                | Instanz desselben Objekts (Blender: verknüpftes Duplikat, Rhino: Block, Cinema 4D: Instanz) |
| Stuhl       | Holz                                                | ein zweites Objekt mit demselben Material                                                   |
| Wand        | Ziegel: Diffustextur `ziegel-gross.png` auf UV      | großes Bild: verkleinert, nicht weggelassen                                                 |
| Rohbau      | –                                                   | Objekt ohne Material                                                                        |
| Glas        | Glas: Basisfarbe mit 40 % Deckkraft                 | Transparenz als `alphaMode: BLEND`                                                          |
| Sockel      | Holz, aber ohne UV (Cinema 4D: kubische Projektion) | Textur ohne UV                                                                              |
| Versteckt   | Holz                                                | ausgeblendet (Rhino: auf einer ausgeschalteten Ebene) — geht nicht mit                      |

Erwartet, je Host im Messprotokoll belegt:

- **nach Material** (Vorgabe): je ein Objekt `Holz`, `Ziegel`, `Glas` und `ohne Material`, dazu die
  Kameras. `Holz` umfasst Tisch, Tisch Kopie, Stuhl und Sockel. Schreibt der Exporter für den Sockel
  eine eigene Fassung von „Holz“ ohne Textur, ist sie ein zweites Primitiv desselben Objekts.
- **Objekte behalten**: Tisch, Tisch Kopie, Stuhl, Wand, Rohbau, Glas, Sockel (Archicad: die
  Element-GUIDs).
- **ohne Materialien**: ein Objekt `ohne Material`, keine Materialien, keine Bilder, keine UV.
- Jedes Bild höchstens 2 MB. Die Wand trägt ein verkleinertes Ziegelbild, kein Material ohne Textur.
  Keine Kanäle außer Basisfarbe, Diffustextur und `alphaMode`.

Belegt wird mit `rendertaxi_client.glbmaterials.describe_glb`, mit `inspect` aus `@gltf-transform/functions`
(der Bibliothek hinter `gltf-transform inspect`, im Repository schon Abhängigkeit von
`packages/modules/model-creation`) und mit einem Bild der Datei im `GLTFLoader` von three.js, wie in der
Modellansicht.
