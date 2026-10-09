# Golden Fixtures v1

Zehn positive Verzeichnisse — `host-capabilities/` mit je einer Selbstauskunft
mehrerer Hosts — und zwei Sätze Negativfälle. Sie sind die Testgrundlage für
Plugins und Plattform und werden von `../../tools/validate.mjs` maschinell
geprüft.

| Verzeichnis         | Zweck                                                                                                |
| ------------------- | ---------------------------------------------------------------------------------------------------- |
| `viewport-only`     | kleinster gültiger Capture: ein `viewport`-Bild, `intent` leer                                       |
| `beauty-depth`      | `beauty` und `depth` als Gleitkomma-EXR, `linear-metric`                                             |
| `full-pass`         | alle zehn Rollen, gemischte Status                                                                   |
| `host-capabilities` | Vertrag 1.1.0 mit `source.host.capabilities`; `depthPass: available` ohne Tiefenbild                 |
| `model-camera`      | Vertrag 1.2.0: `viewport`, GLB-Datei (`model`), perspektivische Kamera, `geometry` mit Georeferenz   |
| `camera-only`       | Vertrag 1.2.0: parallele Kamera ohne Modell, `model` als `unavailable`                               |
| `png-passes`        | Vertrag 1.3.0: alle Datenpässe als PNG mit 8 und 16 Bit, Tiefe `normalized-linear`, IDs Graustufen   |
| `lens-shift`        | Vertrag 1.4.0: Kamera mit Objektiv (35 mm, Sensor 36 mm, `auto`) und Shift, GLB-Datei                |
| `source-file-name`  | Vertrag 1.5.0: Cinema-4D-Capture mit `source.fileName` (Name der Ursprungsdatei, ohne Pfad)          |
| `model-only`        | Vertrag 1.6.0: Blender-Capture nur mit Modell — GLB, Kamera mit `resolution`, kein Bild              |
| `negative`          | `cases.json` (Manifest) und `capability-matrix-cases.json` (Matrix) — Fälle, die fehlschlagen müssen |

Die Fixtures sind Vertragsbeispiele, **keine Aussagen über Hostfähigkeiten**.
Was Blender, Cinema 4D und Rhino tatsächlich erzeugen können, belegen erst die
Verifikationsspikes #22 bis #24; `full-pass` nennt deshalb keinen echten Host.

`beauty-depth`, `full-pass` und `host-capabilities` zeigen Dokumente vor 1.3.0
mit OpenEXR und `linear-metric`. Sie bleiben gültig — eine MINOR-Version macht
kein älteres Dokument ungültig — und tragen die Negativfälle der Regeln von
1.0.0 bis 1.2.0. Was ein Plugin heute schreibt, zeigt `png-passes`: seit 1.3.0
ist PNG das einzige Bildformat des Bildwegs (`capture-manifest.md`,
Abschnitt 12).

## `viewport-only`

Der Fall des ersten Durchgangs (#20): Archicad 28 übergibt die aktuelle Ansicht
als 8-Bit-PNG. `sourceViewKey` ist `null` — ob Archicad einen stabilen
Ansichtsschlüssel hergibt, ist noch nicht belegt. `camera` und `geometry` sind
`null`.

## `beauty-depth`

Projekt noch nicht zugeordnet (`platformProjectId: null`). `beauty` linear,
32 Bit Gleitkomma, RGBA; `depth` als `non-color`, ein Kanal, `linear-metric`
ohne `near`/`far`. `view.displayName` enthält ein Zeichen jenseits der BMP,
`intent` einen `presetKey` und Prompttext mit Nicht-ASCII.

## `full-pass`

| Rolle               | Status        | Besonderheit                                                         |
| ------------------- | ------------- | -------------------------------------------------------------------- |
| `viewport`          | `present`     | 8 Bit, sRGB, RGBA                                                    |
| `beauty`            | `present`     | 16 Bit Gleitkomma, linear                                            |
| `depth`             | `present`     | 16 Bit Ganzzahl, `normalized-inverse` mit `near` 0.1 und `far` 250.5 |
| `normal`            | `present`     | `space: camera`                                                      |
| `albedo`            | `planned`     |                                                                      |
| `mask`              | `unavailable` | mit `note`                                                           |
| `object-id`         | `present`     | 8 Bit, `non-color`                                                   |
| `material-id`       | `planned`     |                                                                      |
| `ambient-occlusion` | `planned`     | mit vorgesehenem `image`, ohne Hash                                  |
| `cryptomatte`       | `unavailable` |                                                                      |

## Golden-Fixture-Regel

Es gibt keine Bilddateien. Für jedes Asset mit `status: present` gilt:

```text
stand    = "rendertaxi-golden-fixture:" + captureId + ":" + path
sha256   = SHA-256(stand)
byteSize = Bytelänge von stand in UTF-8
```

`contentHash` folgt `../../docs/capture-manifest.md`, Abschnitt 7.

## `model-camera`

Ausbaustufe 2 (ADR 0032): ein Blender-Capture mit Bild **und** GLB-Datei.
`geometry.assetPath` zeigt auf genau die Datei der Rolle `model`; der
Weltraum des Hosts ist Z-oben und rechtshändig, der Ursprung verschoben.
`camera` steht im Exportraum (Meter, +Y oben) mit horizontalem Sichtfeld.
`georeference` hat die Form des Anlageblocks: EPSG 25832, Ursprung, eine
Drehung als einziger Schritt der Kette.

## `camera-only`

Eine Draufsicht aus Rhino: parallele Projektion mit `extent`, ferne
Schnittebene `null` (unendlich). Die Rolle `model` ist `unavailable`, deshalb
ist `geometry` `null`.

## `negative/cases.json`

Jeder Fall nennt eine Fixture als `base`, wendet `operations` (`set` oder
`remove` über einen JSON Pointer) auf eine Kopie an und muss einen Befund
auslösen, der `expect` enthält. Ein Fall, der stillschweigend durchgeht, lässt
die Prüfung fehlschlagen. Die Fälle sind nach den neun Festlegungen aus
Issue #16 gruppiert, gefolgt von Hashregeln, Identität und verbotenen Inhalten.

## `host-capabilities`

Das `viewport-only`-Bild unter Vertrag 1.1.0, dazu eine Selbstauskunft über den
Host, die mehr verspricht, als angekommen ist: `depthPass: available`, aber
kein Tiefenbild. Das ist gültig — der Server glaubt der Selbstauskunft nicht
und plant nur nach den angekommenen Dateien
(`../../docs/host-capabilities.md`).

## `negative/capability-matrix-cases.json`

Dieselbe Form wie `cases.json`, aber `base` zeigt auf eine Hostspalte unter
`../../capabilities/`. Jeder Fall muss einen Befund der Matrixprüfung auslösen —
allen voran `Zelle ohne Belegstufe`.

## `lens-shift`

Vertrag 1.4.0 (#197). Ein Blender-Capture mit Bild und GLB-Datei; die Kamera
nennt neben `fieldOfView` (horizontal, 2·atan(36/70)) ihr Objektiv — 35 mm,
Sensor 36 mm auf der längeren Seite — und einen Shift von 0,1 nach rechts
und 0,05 nach unten. Die Negativfälle `camera-lens-*` und `camera-shift-*`
bauen darauf auf: ein Objektiv mit anderem Winkel oder anderer Achse, ein
Objektiv an einer Parallelkamera, ein Shift außerhalb ±2, und beide Felder
in einem Dokument unter 1.4.0.

## `png-passes`

Vertrag 1.3.0 (#190). `viewport` und `beauty` 8 Bit sRGB RGBA; `depth` 16 Bit
Graustufen, `normalized-linear` mit `near` 0.1 und `far` 100; `normal` 8 Bit
`rgb` im Weltraum; `albedo` 16 Bit linear; `object-id` 8 Bit und
`material-id` 16 Bit Graustufen; `ambient-occlusion` vorgesehen als
`image/png`; `mask` und `cryptomatte` `unavailable`. Die Negativfälle
`png-profile-*` bauen darauf auf.

## `source-file-name`

Vertrag 1.5.0 (#226). Ein Cinema-4D-Capture mit `viewport` und `beauty` als
PNG; `source.fileName` nennt die gespeicherte Szene `SWH-Schule-Weberberg.c4d`
— den Namen, keinen Ordner. Die Negativfälle `source-file-name-*` bauen darauf
auf: das Feld in einem 1.4.0-Dokument, Pfadtrenner (`/`, `\`, `:`), Steuer-
und Formatzeichen, `..`, ein leerer und ein zu langer Name.

## `negative/source-file-names.json`

Keine Manifeste, sondern Dateinamen, wie ein Host sie liefert (`raw`), und was
der Python-Client daraus in `source.fileName` schreibt (`expected`; `null`
heißt: das Feld entfällt). `integrations/_shared/python/tests/test_client.py`
prüft den Client daran, `tests/contract/capture-manifest-validation.test.ts`
den Server: jeder erwartete Name wird angenommen, jeder verworfene Name ohne
Pfad abgelehnt.

## `model-only`

Vertrag 1.6.0 (RTX-P-014, `capture-manifest.md`, Abschnitt 14): eine Aufnahme
**nur mit Modell**. Kein Bild, nur die GLB-Datei; `camera` ist Pflicht und
nennt mit `resolution` (1920 × 1080) die Bildgröße der Kamera, dazu Objektiv
(35 mm). Die Selbstauskunft meldet `modelOnlyCapture: available`, die Quelle
nennt ihren Dateinamen. Unter 1.6.0 ist dasselbe Dokument ungültig
(Negativfall `model-only-before-1-6`).
