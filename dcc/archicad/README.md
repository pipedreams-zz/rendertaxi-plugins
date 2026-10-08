# rendertaxi.ai — Add-On für Archicad 28

Übergibt die **aktuelle Ansicht als Bild** an rendertaxi.ai, ordnet sie einem
Projekt und einem Blickpunkt zu — neu oder als ausdrückliches Update — und
öffnet genau diesen Blickpunkt im Browser.

Der Weg ist der host-neutrale Vertrag aus
[ADR 0009](../../../docs/adr/0009-host-neutraler-plugin-vertrag.md): derselbe,
den Blender, Cinema 4D und Rhino später benutzen. Der Modellweg (Geometrie,
GLB, Modellversion, Diff, Aktivierung) ist ausdrücklich **nicht** Teil dieses
Add-Ons und wird von ihm auch nicht vorbereitet.

## Bauen und installieren

```bash
integrations/archicad/addon/scripts/build.sh     # baut das .bundle
integrations/archicad/addon/scripts/install.sh   # installiert es in Archicad 28
integrations/archicad/addon/scripts/test.sh      # prüft den Kern ohne Archicad
```

`build.sh` braucht das Archicad 28 API Development Kit. Sein Pfad kommt aus
`AC_API_DEVKIT_DIR`; **das DevKit gehört nicht ins Repository**. Ohne DevKit
baut `build.sh` nur den DevKit-freien Kern und sagt das ausdrücklich.

`test.sh` braucht weder Archicad noch DevKit. Es baut den Kern und lässt alle
Prüfungen laufen — einschließlich des vollständigen Uploadwegs gegen einen
lokalen Scheinserver, mit Abbruch und Wiederholung an jeder Stufe.

Archicad lädt Add-Ons beim Start. Nach `install.sh` muss Archicad neu
gestartet werden.

## Windows und Auslieferung

Die Windows-`.apx` baut `.github/workflows/archicad-windows.yml` auf GitHub
Actions (DevKit 28.4001, Visual Studio 2022 mit Toolset v142, libcurl aus
vcpkg) und lässt dort auch die Kerntests laufen. Die Plattformteile haben je
System eine Umsetzung (#161):

| Baustein                             | macOS                                                 | Windows                                            |
| ------------------------------------ | ----------------------------------------------------- | -------------------------------------------------- |
| Tokenablage (`MakeSystemTokenStore`) | Keychain                                              | Credential Manager, `rendertaxi/archicad/<Server>` |
| Adresszerlegung (`ParseUrl`)         | CFURL                                                 | `WinHttpCrackUrl`                                  |
| PNG und Zuschnitt                    | ImageIO                                               | WIC, Rechnung in `PlanCrop`                        |
| Ablage (`rtx/Platform.hpp`)          | `~/Library/Application Support/…`, `~/Library/Logs/…` | `%LOCALAPPDATA%\rendertaxi\archicad\`              |
| Browser                              | `/usr/bin/open`                                       | `ShellExecuteW`                                    |

Der Lauf mit Archicad unter Windows steht aus (Vorlage in
`../docs/first-run-protocol.md`, Abschnitt 9); bis dahin ist die Windows-Fassung
„Vorschau“.

```bash
integrations/archicad/addon/scripts/dist.sh macos   # Bundle nach dist/macos/ (eingecheckt)
integrations/archicad/addon/scripts/dist.sh win     # .apx des letzten Laufs nach dist/win/ (nicht eingecheckt)
```

Ausgeliefert wird über den öffentlichen Spiegel
[`pipedreams-zz/rendertaxi-plugins`](https://github.com/pipedreams-zz/rendertaxi-plugins):
`.github/workflows/plugins.yml` spiegelt dieses Verzeichnis nach
`dcc/archicad/` und die Vorlagen aus `integrations/public-repo/` an die
Wurzel; ein Tag `plugins-<Datum>` erzeugt dort ein Release. Die
Endnutzer-Anleitung steht in
[`../../public-repo/docs/dcc/archicad.md`](../../public-repo/docs/dcc/archicad.md).
Nach einer Änderung am Add-on gehört vor dem Release-Tag ein frisches
`dist.sh macos` dazu, sonst packt der Release das alte Bundle.
Das Bundle entsteht **nur** am Mac, der Release baut es nicht neu (nur die
Windows-.apx). `integrations/public-repo/scripts/check-archicad-bundle.sh`
weist ein Bundle ab, dessen Version nicht `src/Version.hpp` entspricht — im
Spiegel-Workflow und in `pack.sh`.

## Aufbau

```text
addon/
├── core/            DevKit-frei, auf jedem Mac baubar und testbar
│   ├── include/rtx/
│   ├── src/
│   └── tests/       eigener Testlauf + lokaler HTTP-Scheinserver
├── src/             der einzige Teil, der ACAPI_* und DG aufruft
├── RINT/            lokalisierbare Ressourcen: Menü, Palette
├── RFIX/            MDID
├── RFIX.mac/        Info.plist
├── cmake/Addon.cmake
└── scripts/
```

Diese Grenze ist keine Ordnungsliebe, sondern die Bedingung dafür, dass Stufe D
des Auftrags erfüllbar ist: „Automatisierte Tests für Uploader und
Zustandsautomat **ohne Archicad**". `core` hängt nur von der
C++17-Standardbibliothek, der libcurl des Systems und Security.framework ab —
keine neu aufgenommene Abhängigkeit im Sinne der Reiferichtlinie aus
[`AGENTS.md`](../../../AGENTS.md).

| Baustein          | Was er tut                                                                                                 |
| ----------------- | ---------------------------------------------------------------------------------------------------------- |
| `Canonical`       | Längenpräfigierte Kodierung und `contentHash` nach `architecture.md`, 8.2 und 8.6                          |
| `CaptureManifest` | Erzeugt und **prüft** das Manifest, bevor etwas das Gerät verlässt                                         |
| `PluginApiClient` | Die elf Endpunkte der Plugin API v1 und die beiden Lesewege                                                |
| `CaptureTransfer` | Der Zustandsautomat der Übernahme: anlegen, übertragen, Manifest, finalisieren, wiederaufnehmen            |
| `TransferStore`   | Was einen Neustart von Archicad überlebt — Idempotenzschlüssel, angefangene Vorgänge, der letzte Vorschlag |
| `TokenStore`      | macOS Keychain, Windows Credential Manager. Es gibt bewusst **keine** Dateifassung                         |
| `Log`             | Redigiert Bearer-Kopfzeilen, Geheimnisfelder und Abfrageteile von URLs, bevor eine Zeile entsteht          |
| `ViewCapture`     | Der Bildzugriff auf Archicad; Belege in [`../docs/capabilities.md`](../docs/capabilities.md)               |
| `CapabilityProbe` | Die Messung hinter dem Menüpunkt „Bildzugriff messen"                                                      |

## Die Palette

Vier Bereiche, genau die aus Festlegung 3 des Auftrags:

1. **Verbindung** — Serveradresse, Anmelden, Abmelden.
2. **Projekt und Blickpunkt** — Projekt wählen; neuer Blickpunkt mit Namen
   **oder** bestehenden ausdrücklich aktualisieren.
3. **Bild übernehmen** — Quellansicht, Übernahme, Abbrechen, Fortschritt.
4. **Im Browser öffnen** — die `openUrl` aus der Antwort.

Generierung und Ergebnisbearbeitung bleiben in der Webanwendung. Es gibt
deshalb kein Prompt-, Rezept- oder Auftragsfeld.

## Der Ausschnitt kommt aus der Rendering-Szene

Der Architekt rahmt das Bild, und der Rahmen muss sichtbar sein, **während** er
den Blickwinkel setzt. In Archicad ist dafür genau eine Stelle zuständig: die
**Rendering-Szene**. Ihre Bildgröße (`API_RendImage.hSize`/`vSize`) ist das,
was der „Render-Schutzbereich" ins 3D-Fenster zeichnet, was
`ACAPI_Rendering_PhotoRender` rendert, und was eine gespeicherte Ansicht über
`API_NavigatorView.renderingSceneName` mitführt.

Das Add-On liest die aktuelle Szene und schneidet das aufgenommene Bild mittig
darauf zu — das Ergebnis ist genau der Schutzbereich. Die Palette nennt ihn
vor der Aufnahme: `Ausschnitt 1024 x 768`. Gibt es keine Szene, wird nichts
zugeschnitten.

Weicht das Ausgabeziel des Zielblickpunkts vom Szenenverhältnis ab, sagt die
Palette das — sie löst es nicht still auf.

**Nicht über die Fenstergröße.** Der Versuch, das 3D-Fenster vor der Aufnahme
auf das Zielformat zu bringen, ist gemessen worden und falsch: er entdockt das
Fenster aus der Registerleiste, und eine breitere Fenstergröße erweitert das
Blickfeld nach rechts, statt oben und unten zu beschneiden. Belege in
[`../docs/capabilities.md`](../docs/capabilities.md), Abschnitt 2.3.1. Die
Fenstergröße wird gelesen und nie gesetzt.

**Maße kommen aus der Datei, nicht aus der Fenstergröße.** Gemessen: dasselbe
API meldete 995 × 905 und schrieb ein PNG mit 1990 × 1810. `hSize`/`vSize` sind
Punkte, der Export folgt der Anzeigeskalierung.

## Skriptbar über die JSON-Schnittstelle

Das Add-On registriert drei Befehle für `API.ExecuteAddOnCommand` (Port 19723):

| Befehl                   | Was er tut                                                                                                                                  |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------- |
| `rendertaxi.Info`        | Host, Add-On-Fassung, aktuelle Ansicht, Projektname — ohne Nebenwirkung                                                                     |
| `rendertaxi.CaptureView` | Nimmt die aktuelle Ansicht auf, optional auf `aspectWidth:aspectHeight` zugeschnitten, und liefert Pfad, Maße, Medientyp, SHA-256 und Dauer |
| `rendertaxi.Probe`       | Die Messung aus [`../docs/capabilities.md`](../docs/capabilities.md)                                                                        |

Damit lassen sich Bildzugriff und Zuschnitt aus einem Skript messen und
nachrechnen, statt sie von Hand nachzustellen.

**Die Übertragung ist bewusst nicht dabei.** Sie hängt an einem Anmeldetoken,
und ein von außen auslösbarer Upload wäre eine Angriffsfläche ohne Gegenwert.

## Anmeldung

**Es gibt kein Passwortfeld, und das ist Absicht** (Festlegung 4 des Auftrags).
Die Anmeldung ist ein Gerätelogin nach RFC 8628: das Add-On zeigt einen Code,
öffnet den Systembrowser, und der Nutzer bestätigt dort. Das Add-On sieht nie
Zugangsdaten, sondern nur ein Token — und das liegt in der **macOS Keychain**
(Windows: Credential Manager),
nie in einer Klartextdatei, nie in der Archicad-Projektdatei, nie im Protokoll.
„Abmelden" löscht es lokal **und** widerruft es serverseitig.

## Wiederholungssicherheit

Ein Übernahmevorgang trägt **einen** Idempotenzschlüssel, der Session, Dateien,
Manifest, Finalisierung und die Zuordnung zum Blickpunkt umfasst. Er wird
**vor** dem ersten Netzaufruf in
`~/Library/Application Support/rendertaxi/archicad/transfers.json` (Windows:
`%LOCALAPPDATA%\rendertaxi\archicad\transfers.json`) geschrieben
und überlebt damit einen Absturz oder Neustart von Archicad. Er bleibt
derselbe, bis der Vorgang abgeschlossen oder vom Nutzer verworfen ist.

Wird dieselbe Ansicht mit **anderem** Bild oder **anderem** Ziel noch einmal
übernommen, während ein Vorgang offen ist, meldet das Add-On
`idempotency_conflict` und bittet um eine Entscheidung — fortsetzen oder
verwerfen. Es überschreibt den angefangenen Vorgang nicht still.

Läuft eine Session serverseitig ab, legt der Client nach
`upload-protocol.md`, Abschnitt 5.2 eine neue mit **neuem** Schlüssel an;
bereits übertragene Dateien werden über ihren SHA-256 dedupliziert.

## Was hier nicht steht

Kein Anmeldetoken, kein Gerätecode, keine signierte Adresse, kein DevKit, kein
gebautes Bundle. `build/` ist ignoriert.

## Stand der Gegenstelle

Der Client ist gegen die **Spezifikation** gebaut, nicht gegen Annahmen:
`docs/api/plugin-api-v1.md`, `docs/api/plugin-api-v1.openapi.json` und
`packages/contracts/src/plugin.ts` auf `main` bei **`08dd482`** (25.09.2026) —
derselbe Stand, der auf `https://dev.rendertaxi.ai` läuft.

Die neun Annahmen des ersten Durchgangs (A-01 bis A-09) sind **aufgelöst** —
jede entweder durch die Spezifikation bestätigt oder korrigiert. Auch die
letzte Stelle, die dem Vertrag vorauslief (`target.viewpoint.frame`), steht
seit `aeb4908` in der Momentaufnahme, die Rahmengröße `size` seit `08dd482`;
die Liste der erklärten Erweiterungen im
Prüfer ist **leer**. Welche Annahme wie aufgelöst wurde, steht in
[`docs/plugin-api-client.md`](docs/plugin-api-client.md).

**Der Scheinserver trägt keine eigene Vertragsauslegung mehr.** Jeder Aufruf
zwischen Client und Scheinserver wird aufgezeichnet und gegen die
OpenAPI-Momentaufnahme gehalten (`tools/check-openapi.mjs`), und das erzeugte
Manifest gegen das geteilte Schema und die Golden Fixtures
(`tools/check-manifest.mjs`). Beides läuft in `scripts/test.sh` und ist nicht
optional.

## Sicherheit

**Ein Anmeldetoken verlässt den Prozess nur über `https://`.** Einzige Ausnahme
ist die Schleife (`127.0.0.1`, `localhost`, `[::1]`) für den Scheinserver. Die
Regel steht an einer Stelle (`PluginApiClient::IsTokenSafeBaseUrl`), wird
zweifach durchgesetzt — beim Anmelden in der Palette und in jedem Aufruf — und
ist gegen Umgehungsversuche geprüft.

**Ein Fehler im Handshake beendet Archicad nie.** Es gibt keinen Pfad, der
wirft oder abbricht; jeder Sonderfall ist ein Fehlerwert und endet als Satz in
der Palette.

**Ein dauerhaft ablaufender Server blockiert keinen Capture.** Nach
`expired`/`aborted` legt der Client mit neuem Schlüssel neu an — höchstens
dreimal, dann sagt er es und lässt den Vorgang verwerfen.
