# rendertaxi.ai für Archicad 28

Das Add-on übergibt die **aktuelle Ansicht als Bild** an rendertaxi.ai,
ordnet sie einem Projekt und einem Blickpunkt zu — neu oder als ausdrückliches
Update — und öffnet genau diesen Blickpunkt im Browser. Generierung und
Ergebnisbearbeitung bleiben in der Webanwendung.

## Installation

Voraussetzung: Archicad 28. Die Zip `rendertaxi-archicad28-<Version>-<Release>.zip` (etwa
`rendertaxi-archicad28-1.0.0-2026.09.26.zip`) von der
[Release-Seite](https://github.com/pipedreams-zz/rendertaxi-plugins/releases/latest)
laden (in der Webanwendung unter **Verbundene Geräte › Plugins herunterladen**) und
entpacken. Sie enthält `macos/rendertaxi.bundle` und `win/rendertaxi.apx`.

### macOS

1. Archicad beenden.
2. `macos/rendertaxi.bundle` nach
   `/Applications/Graphisoft/Archicad 28/Add-Ons/` kopieren. Alternativ in
   Archicad unter **Optionen › Add-On-Manager** hinzufügen.
3. Archicad starten. Im Menü erscheint **rendertaxi.ai**.

Das Bundle ist ad hoc signiert, aber **nicht notarisiert**. Lehnt macOS das
Laden ab oder meldet Archicad, das Add-on sei beschädigt, einmal im Terminal
die Download-Sperre entfernen und Archicad neu starten:

```sh
xattr -dr com.apple.quarantine "/Applications/Graphisoft/Archicad 28/Add-Ons/rendertaxi.bundle"
```

### Windows (Vorschau)

1. Archicad beenden.
2. `win/rendertaxi.apx` nach `C:\Program Files\Graphisoft\Archicad 28\Add-Ons\`
   kopieren oder unter **Optionen › Add-On-Manager** hinzufügen.
3. Archicad starten.

**Die Windows-Fassung ist eine Vorschau.** Sie wird automatisch gebaut, ist
aber noch nicht mit Archicad unter Windows erprobt. Die Anmeldung ist unter
Windows noch nicht möglich, weil der sichere Schlüsselspeicher (unter macOS
die Keychain) für Windows noch fehlt; die Palette meldet dann
„Keine Keychain verfügbar — Anmeldung nicht möglich.“ Für die Arbeit mit
rendertaxi.ai bitte vorerst die macOS-Fassung benutzen.

### Aktualisieren und entfernen

Archicad beenden, die Datei ersetzen beziehungsweise löschen, Archicad
starten.

## Verbinden

1. Menü **rendertaxi.ai › Palette** öffnet die Palette. Sie lässt sich
   andocken.
2. Im Bereich **Verbindung** steht die Serveradresse (Vorgabe
   `https://dev.rendertaxi.ai`). Nur ändern, wenn euer Büro eine andere
   Adresse nennt.
3. **Anmelden…** öffnet den Browser mit einem Code. Dort mit dem
   rendertaxi.ai-Konto anmelden und das Gerät bestätigen. Die Palette zeigt
   danach „Angemeldet als …“.

Das Add-on hat **kein Passwortfeld** und sieht nie Zugangsdaten; es erhält nur
ein widerrufbares Token und legt es in der macOS-Keychain ab. Angemeldete
Geräte lassen sich in der Webanwendung unter **Verbundene Geräte** (`/geraete`) einsehen und
abmelden.

## Bild übernehmen

1. Im Bereich **Projekt und Blickpunkt** das Projekt wählen.
2. Entweder **Neuer Blickpunkt** mit Namen oder **Bestehenden Blickpunkt
   aktualisieren** und den Blickpunkt wählen. Ein Update legt keinen zweiten
   Blickpunkt an.
3. Den Ausschnitt in Archicad einstellen. Maßgeblich ist die
   **Rendering-Szene**: ihre Bildgröße ist der Rahmen, den Archicad im
   3D-Fenster als Render-Schutzbereich zeigt, und genau auf diesen Ausschnitt
   schneidet das Add-on das Bild zu. Die Palette nennt ihn vor der Übernahme,
   etwa „Ausschnitt 1024 x 768“.
4. **Aktuelle Ansicht übernehmen** nimmt das 3D-Fenster auf.
   **Rendern und übernehmen** rendert stattdessen mit der aktuellen Szene und
   übernimmt das fertige Rendering.
5. Der Fortschritt steht in der Palette. **Abbrechen** hält eine laufende
   Übertragung an; eine unterbrochene Übernahme wird beim nächsten Mal
   fortgesetzt oder lässt sich mit **Angefangene Übernahme verwerfen**
   verwerfen.
6. **Blickpunkt im Browser öffnen** springt zum Ergebnis in der Webanwendung.

## Rahmengröße

Die Einstellung **Rahmengröße** gehört zum Ziel, nicht zur Aufnahme:

- **Canvas-Vorgabe (Standard):** der Blickpunkt behält die Größe der
  Webanwendung; übernommen wird nur das Seitenverhältnis der Aufnahme.
- **Render-Einstellung übernehmen:** der Rahmen bekommt die Maße der
  Aufnahme. Ist die lange Kante kürzer als 1536 Pixel, bleibt die
  Canvas-Vorgabe.

Beim Aktualisieren eines bestehenden Blickpunkts ändert sich dessen Rahmen
nur, wenn **Rahmen an Aufnahme anpassen** angehakt ist; sonst bleibt er, wie
er ist. Weicht der Rahmen vom Seitenverhältnis der Szene ab, sagt die Palette
das, statt es still aufzulösen.

## Abmelden

**Abmelden** in der Palette löscht das Token auf diesem Rechner **und**
widerruft es beim Server. Ein Gerät lässt sich auch aus der Webanwendung
unter **Verbundene Geräte** abmelden, etwa wenn der Rechner nicht mehr zugänglich ist.

## Hinweise

- Einstellungen und angefangene Übernahmen liegen unter
  `~/Library/Application Support/rendertaxi/archicad/`, Meldungen des Add-ons
  unter `~/Library/Logs/rendertaxi/`. Beide enthalten kein Token.
- Das Add-on ist für Archicad 28 gebaut; andere Hauptversionen laden es nicht.
