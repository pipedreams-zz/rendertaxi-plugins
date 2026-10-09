# rendertaxi.ai für Archicad 28

Das Add-on übergibt die **aktuelle Ansicht als Bild**, das **Modell mit der
Kamera der Ansicht** oder beides an rendertaxi.ai, ordnet es einem Projekt und
einem Blickpunkt zu — neu oder als ausdrückliches Update — und öffnet genau
diesen Blickpunkt im Browser. Generierung und
Ergebnisbearbeitung bleiben in der Webanwendung.

## Installation

Voraussetzung: Archicad 28. Die Zip `rendertaxi-archicad28-<Version>-<Release>.zip` (etwa
`rendertaxi-archicad28-1.2.0-2026.10.08.zip`) von der
[Release-Seite](https://github.com/pipedreams-zz/rendertaxi-plugins/releases/latest)
laden (in der Webanwendung unter **Verbundene Geräte › Plugins herunterladen**) und
entpacken. Sie enthält `macos/rendertaxi.bundle` und `win/rendertaxi.apx`.

### macOS

1. Archicad beenden.
2. `macos/rendertaxi.bundle` nach
   `/Applications/Graphisoft/Archicad 28/Add-Ons/` kopieren. Alternativ in
   Archicad unter **Optionen › Add-On-Manager** hinzufügen.
3. Archicad starten. Im Menü erscheint **rdtx.ai**.

Das Bundle ist ad hoc signiert, aber **nicht notarisiert**. Lehnt macOS das
Laden ab oder meldet Archicad, das Add-on sei beschädigt, einmal im Terminal
die Download-Sperre entfernen und Archicad neu starten:

```sh
xattr -dr com.apple.quarantine "/Applications/Graphisoft/Archicad 28/Add-Ons/rendertaxi.bundle"
```

### Windows (Vorschau)

1. Archicad beenden.
2. **Download freigeben:** Die `.apx` ist nicht signiert. Vor dem Entpacken
   Rechtsklick auf die Zip › **Eigenschaften** › unten **Zulassen** anhaken ›
   OK. Sonst trägt die entpackte Datei die Download-Markierung, und Windows
   (SmartScreen) oder Archicad können das Laden verweigern. Nachträglich geht
   dasselbe an der `.apx` selbst oder in PowerShell mit
   `Unblock-File -Path "…\rendertaxi.apx"`.
3. `win/rendertaxi.apx` nach `C:\Program Files\Graphisoft\Archicad 28\Add-Ons\`
   kopieren (Windows fragt nach Administratorrechten) oder die Datei an einem
   beliebigen Ort ablegen und unter **Optionen › Add-On-Manager** hinzufügen.
4. Archicad starten. Im Menü erscheint **rdtx.ai**.

Die Anmeldung läuft wie unter macOS über den Browser (siehe
[Verbinden](#verbinden)); das Token liegt unter Windows in der
**Anmeldeinformationsverwaltung** (Credential Manager) unter
„Windows-Anmeldeinformationen › Generische Anmeldeinformationen“ als
`rendertaxi/archicad/<Serveradresse>`.

**Die Windows-Fassung ist eine Vorschau.** Sie ist vollständig umgesetzt und
automatisch geprüft, der Lauf mit Archicad unter Windows steht aber noch aus.
Rückmeldungen bitte als Issue in diesem Repository.

### Aktualisieren und entfernen

Archicad beenden, die Datei ersetzen beziehungsweise löschen, Archicad
starten.

## Verbinden

1. Menü **rdtx.ai › Palette** öffnet die Palette. Sie lässt sich
   andocken.
2. Im Bereich **Verbindung** steht die Serveradresse (Vorgabe
   `https://dev.rendertaxi.ai`). Nur ändern, wenn euer Büro eine andere
   Adresse nennt.
3. **Anmelden…** öffnet den Browser mit einem Code. Dort mit dem
   rendertaxi.ai-Konto anmelden und das Gerät bestätigen. Die Palette zeigt
   danach „Angemeldet als …“.

Das Add-on hat **kein Passwortfeld** und sieht nie Zugangsdaten; es erhält nur
ein widerrufbares Token und legt es im Schlüsselspeicher des Systems ab (macOS:
Keychain, Windows: Anmeldeinformationsverwaltung). Angemeldete
Geräte lassen sich in der Webanwendung unter **Verbundene Geräte** (`/geraete`) einsehen und
abmelden.

## Bild, Modell oder beides übernehmen

Bild und Modell sind zwei Wege; sie laufen einzeln oder zusammen. Im Bereich
**Übernehmen** stehen dafür zwei Häkchen:

- **Bild** — die Ansicht als Bild, wie bisher.
- **Modell mit Kamera** — die sichtbare Geometrie des 3D-Fensters als
  Modelldatei (glTF) mit der Kamera der Ansicht. **Nur Modell** (Bild aus,
  Modell an) rendert nicht und nimmt kein Bild auf.

Was gesendet wird, steht als erste Zeile darunter, etwa „Gesendet wird: nur
Modell und Kamera — ohne Rendern.“ Die Wahl bleibt gemerkt. Ein Update nur mit
Modell lässt das Bild des Blickpunkts stehen, eines nur mit Bild das Modell.

1. Im Bereich **Projekt und Blickpunkt** das Projekt wählen.
2. Entweder **Neuer Blickpunkt** mit Namen oder **Bestehenden Blickpunkt
   aktualisieren** und den Blickpunkt wählen. Ein Update legt keinen zweiten
   Blickpunkt an.
3. Den Ausschnitt in Archicad einstellen. Maßgeblich ist die
   **Rendering-Szene**: ihre Bildgröße ist der Rahmen, den Archicad im
   3D-Fenster als Render-Schutzbereich zeigt, und genau auf diesen Ausschnitt
   schneidet das Add-on das Bild zu. Die Palette nennt ihn vor der Übernahme,
   etwa „Ausschnitt 1024 x 768“.
4. **Aktuelle Ansicht übernehmen** nimmt das 3D-Fenster auf (Bild) und liest
   das Modell (Modell).
   **Rendern und übernehmen** rendert stattdessen mit der aktuellen Szene und
   übernimmt das fertige Rendering.
5. Der Fortschritt steht in der Palette. **Abbrechen** hält eine laufende
   Übertragung an; eine unterbrochene Übernahme wird beim nächsten Mal
   fortgesetzt oder lässt sich mit **Angefangene Übernahme verwerfen**
   verwerfen.
6. **Blickpunkt im Browser öffnen** springt zum Ergebnis in der Webanwendung.

## Modell senden

- **Woher:** das Modell kommt aus dem **3D-Fenster** — genau das, was es
  zeigt: Ebenen, 3D-Ausschnitt und Ansichtseinstellungen wirken. Für eine
  gespeicherte 3D-Ansicht unter **Ansicht** wählen; das Add-on öffnet sie vor
  der Übernahme.
- **Wie:** Meter im Projektursprung, Oberflächen mit ihrer Farbe und
  Transparenz, ein Element je Knoten. Texturen gehen noch nicht mit.
- **Kamera:** die Kamera der Ansicht — Perspektive, Zweifluchtpunkt und
  Axonometrie — im Ausschnitt der Rendering-Szene. In der Webanwendung zeigt
  **Modell im Rahmen** das Modell aus genau dieser Kamera.
- **Zusätzliche Kameras:** mit **Zusätzliche Kameras mitsenden** und
  **Kameras…** gehen die Kameras weiterer gespeicherter 3D-Ansichten in die
  Modelldatei; im Blickpunkt lassen sie sich unter „Kameras im Modell“
  übernehmen. Die Wahl gilt je Projekt. Das Add-on öffnet dafür jede gewählte
  Ansicht kurz und öffnet danach wieder die Ansicht, von der die Aufnahme
  stammt. Deshalb gibt es zusätzliche Kameras nur, wenn in der Palette eine
  **gespeicherte Ansicht** gewählt ist; bei „Aktuelle Modellansicht“ bleibt
  das Fenster unangetastet, und die Palette sagt das.
- **Wechsel beim Warten:** wer während des Neuaufbaus eine andere Ansicht
  öffnet, das Projekt wechselt oder Ziel und Auswahl ändert, beendet die
  wartende Übernahme; gesendet wird dann nichts.
- **Neuaufbau:** nach einem Ansichtswechsel baut Archicad das 3D-Modell im
  Hintergrund neu auf. Die Palette sagt das, wartet und startet die Übernahme
  danach von selbst; **Abbrechen** beendet das Warten.
- **Grenzen:** vor dem Senden prüft das Add-on Größe und Dreiecke gegen den
  Server. Ist das Modell zu groß, sagt die Palette, um wie viel — den
  3D-Ausschnitt verkleinern und erneut übernehmen.

## Rahmengröße

Die Einstellung **Rahmengröße** gehört zum Ziel, nicht zur Aufnahme:

- **Canvas-Vorgabe (Standard):** der Blickpunkt behält die Größe der
  Webanwendung; übernommen wird nur das Seitenverhältnis der Aufnahme.
- **Render-Einstellung übernehmen:** der Rahmen bekommt die Maße der
  Aufnahme. Ist die lange Kante kürzer als die der Canvas-Vorgabe, bleibt
  diese. Die Canvas-Vorgabe (Seitenverhältnis und lange Kante) stellt der
  Betreiber der Webanwendung ein; das Plugin nennt sie mit Zahl, sobald der
  Server sie mitteilt.

Beim Aktualisieren eines bestehenden Blickpunkts ändert sich dessen Rahmen
nur, wenn **Rahmen an Aufnahme anpassen** angehakt ist; sonst bleibt er, wie
er ist. Weicht der Rahmen vom Seitenverhältnis der Szene ab, sagt die Palette
das, statt es still aufzulösen.

## Abmelden

**Abmelden** in der Palette löscht das Token auf diesem Rechner **und**
widerruft es beim Server. Ein Gerät lässt sich auch aus der Webanwendung
unter **Verbundene Geräte** abmelden, etwa wenn der Rechner nicht mehr zugänglich ist.

## Hinweise

- Einstellungen und angefangene Übernahmen liegen unter macOS in
  `~/Library/Application Support/rendertaxi/archicad/`, Meldungen des Add-ons
  in `~/Library/Logs/rendertaxi/`. Unter Windows liegt beides in
  `%LOCALAPPDATA%\rendertaxi\archicad\`. Keines davon enthält ein Token.
- Das Add-on ist für Archicad 28 gebaut; andere Hauptversionen laden es nicht.
