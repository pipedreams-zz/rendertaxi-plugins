# rendertaxi.ai für Cinema 4D 2026

Das Plugin übergibt die **aktuelle Ansicht** oder das **gerenderte Bild** an
rendertaxi.ai — auf Wunsch zusammen mit dem **Modell** und der Kamera —, ordnet es einem Projekt und einem Blickpunkt zu — neu oder als
ausdrückliches Update — und öffnet genau diesen Blickpunkt im Browser.
Generierung und Ergebnisbearbeitung bleiben in der Webanwendung.

## Installation

Voraussetzung: Cinema 4D **2026** (macOS oder Windows); ältere Fassungen
werden nicht unterstützt — das Plugin lädt dort, zeigt aber nur einen Hinweis.
Die Zip `rendertaxi-cinema4d2026-<Version>-<Release>.zip` (etwa
`rendertaxi-cinema4d2026-0.2.0-2026.09.30.zip`) von der
[Release-Seite](https://github.com/pipedreams-zz/rendertaxi-plugins/releases/latest)
laden (in der Webanwendung unter **Verbundene Geräte › Plugins herunterladen**).
Das Plugin ist Python — kein Installationsprogramm, kein Build.

1. Cinema 4D beenden und die Zip entpacken. Sie enthält einen Ordner
   `rendertaxi` (darin `rendertaxi.pyp`).
2. Den Plugin-Ordner öffnen: in Cinema 4D **Edit › Preferences** (macOS:
   **Cinema 4D › Settings**), unten **Open Preferences Folder…**, darin den
   Ordner `plugins` (fehlt er, anlegen).
3. Den Ordner `rendertaxi` dorthin kopieren — **als Ganzes**, nicht nur die
   `.pyp`-Datei.
4. Cinema 4D starten. Das Plugin steht unter **Extensions › rendertaxi.ai**
   (deutsche Oberfläche: **Erweiterungen**).

Alternativ unter **Preferences › Plugins** den übergeordneten Ordner als
zusätzlichen Suchpfad eintragen.

**Aktualisieren:** Cinema 4D beenden, den Ordner `rendertaxi` durch den neuen
ersetzen. Anmeldung und Einstellungen bleiben erhalten (sie liegen nicht im
Plugin-Ordner). **Entfernen:** den Ordner `rendertaxi` löschen.

Das Fenster lässt sich andocken und kommt mit dem Layout zurück.

## Verbinden

1. **Extensions › rendertaxi.ai** öffnen.
2. Unter **Einstellungen** steht die Serveradresse (Vorgabe
   `https://dev.rendertaxi.ai`) — nur ändern, wenn euer Büro eine andere Adresse
   nennt, dann **Einstellungen sichern**. Ein **Gerätename** hilft, Cinema 4D
   später wiederzuerkennen.
3. **Verbinden** zeigt einen Code und öffnet den Browser. Dort mit dem
   rendertaxi.ai-Konto anmelden und das Gerät bestätigen. Das Fenster zeigt
   danach „Angemeldet als …".

Das Plugin hat **kein Passwortfeld**. Es erhält nur ein widerrufbares Token und
legt es in deinem Cinema-4D-Voreinstellungsordner ab (Unterordner
`rendertaxi`), nur für dich lesbar — **nie** in der `.c4d`-Datei. Angemeldete
Geräte stehen in der Webanwendung unter **Verbundene Geräte** (`/geraete`) und
lassen sich dort abmelden.

## Bild übernehmen

1. Unter **Projekt und Blickpunkt** das Projekt wählen — oder mit **Neues
   Projekt …** eines anlegen (mit den Rechten deiner Rolle im Büro); es ist
   danach gewählt. **Aktualisieren** zeigt umbenannte Projekte mit ihrem
   neuen Namen.
2. **Neuer Blickpunkt** mit Namen oder **Bestehenden aktualisieren** und den
   Blickpunkt wählen. Ein Update legt keinen zweiten Blickpunkt an. Als Name
   steht der Name der Kamera schon da; du kannst ihn ändern.
3. Unter **Bild übernehmen** die Aufnahmeart wählen:
   - **Viewport** — die Ansicht, gerendert mit dem **Viewport Renderer** (so,
     wie sie im Editor zu sehen ist; bei mehreren Ansichten die Renderansicht). Das Plugin
     rendert mit einer Kopie deiner Rendervoreinstellungen; diese selbst
     bleiben, wie sie sind.
   - **Beauty** — rendert mit dem Renderer, der in deinen Rendervoreinstellungen
     aktiv ist, aus der Kamera der Renderansicht — wie **Render to Picture
     Viewer**.

   Die Größe kommt aus **Render Settings › Output** (Breite × Höhe); das Fenster
   nennt sie als „Ausschnitt". Beim Aktualisieren lässt sich stattdessen die
   Größe des **Blickpunkt-Rahmens** wählen.
4. **Aufnehmen und übernehmen.** Das Rendern blockiert Cinema 4D für seine
   Dauer, der Fortschritt steht in der Statusleiste. Danach läuft die
   Übertragung im Hintergrund; **Abbrechen** hält sie an. Eine unterbrochene
   Übernahme wird mit **Übernahme fortsetzen** zu Ende geführt oder mit
   **Angefangene verwerfen** verworfen.
5. **Blickpunkt im Browser öffnen** springt zum Ergebnis.

### Pässe (Beauty)

Bei **Beauty** zeigt das Fenster die Pässe Tiefe, Normalen und Albedo. Das
Plugin schaltet keinen Pass selbst ein; ist einer nicht wählbar, steht darunter,
was einzustellen ist — mit dem Renderer **Standard** oder **Physical**: **Render
Settings › Multi-Pass** einschalten und den Kanal hinzufügen (etwa „Albedo").

Datenpässe gehen als **PNG** an rendertaxi.ai, je Pass eine Datei. Die
Einstellung **Bittiefe der Datenpässe** darunter wählt **8 Bit (Standard)**
oder **16 Bit**; das Plugin merkt sich die Wahl. 16 Bit lohnt sich für feine
Verläufe, die Dateien sind etwa doppelt so groß. Albedo wird übertragen, sobald
der Kanal eingeschaltet ist. Tiefe und Normalen bleiben „geplant" (im Manifest
vermerkt, nicht übertragen), bis gemessen ist, wie Cinema 4D sie kodiert — das
Fenster sagt es beim jeweiligen Pass.

## Modell mitsenden

Unter **Bild übernehmen** schaltet **Modell mitsenden (GLB und Kamera)**
(Standard aus) die sichtbaren Objekte als eine GLB-Datei (glTF 2.0) und die
Kamera der Aufnahme dazu. In der Webanwendung erscheint das Modell als Asset am
Blickpunkt; auf einen Rahmen gezogen, zeigt **Modell im Rahmen** es aus genau
dieser Kamera.

- **Was exportiert wird:** alle Objekte, die in der Aufnahme sichtbar sind —
  bei **Viewport** nach dem Editor-Punkt im Objekt-Manager, bei **Beauty** nach
  dem Render-Punkt, jeweils samt Ebene. Generatoren und Deformer werden in Polygone
  gewandelt, **ohne** Animation,
  Materialien, Texturen, Kameras und Lichter. Deine Szene wird dabei nicht
  verändert: das Plugin arbeitet auf einer Kopie und nutzt den glTF-Exporter
  von Cinema 4D; dessen Einstellungen stellt es danach wieder her.
- **Vor dem Senden** nennt das Fenster die Zahl der Dreiecke und sichtbaren
  Objekte (**Neu zählen** zählt nach Änderungen neu) und die größte
  Modelldatei, die der Server annimmt. Liegt das Modell darüber, wird nichts
  gesendet.
- **Maßstab:** aus **Projekteinstellungen › Projektmaßstab** (etwa 1
  Zentimeter). Das Plugin prüft an der fertigen Datei, dass sie in Metern und
  richtig gespiegelt vorliegt — Cinema 4D rechnet linkshändig, glTF
  rechtshändig. Passt etwas nicht, geht **kein** Modell mit, und das Fenster
  sagt, warum.
- **Kamera:** die Kamera der Renderansicht (ohne Szenenkamera die
  Editor-Kamera), perspektivisch. Eine **Parallelkamera**, **Film Offset**,
  Stereo, sphärische Kameras, Linsenverzerrung, ein Pixelseitenverhältnis
  ungleich 1:1 oder ein Film Aspect, der nicht zum Bild passt, lassen sich im
  Vertrag nicht beschreiben: das Modell geht dann ohne Kamera mit, und das
  Fenster sagt es vorher („Ohne Kamera: …").
- Ohne **Modell mitsenden** bleibt alles wie bisher: nur das Bild.

Der Server muss den Modellweg kennen; tut er es nicht, sagt das Fenster es, und
übernommen wird nur ohne Modell.

## Rahmengröße

Die Einstellung **Rahmengröße** gehört zum Ziel, nicht zur Aufnahme:

- **Canvas-Vorgabe (Standard):** der Blickpunkt behält die Größe der
  Webanwendung; übernommen wird nur das Seitenverhältnis der Aufnahme.
- **Render-Einstellung übernehmen:** der Rahmen bekommt die Maße der
  Aufnahme. Ist die lange Kante kürzer als die der Canvas-Vorgabe, bleibt
  diese. Die Canvas-Vorgabe (Seitenverhältnis und lange Kante) stellt der
  Betreiber der Webanwendung ein; das Plugin nennt sie mit Zahl, sobald der
  Server sie mitteilt.

Beim Aktualisieren eines bestehenden Blickpunkts ändert sich dessen Rahmen nur,
wenn **Rahmen an Aufnahme anpassen** angehakt ist; sonst bleibt er, wie er ist.
Weicht der Rahmen vom Seitenverhältnis der Aufnahme ab, sagt das Fenster das,
statt es still aufzulösen.

## Abmelden

**Abmelden** widerruft das Token beim Server **und** löscht es auf diesem
Rechner. Ist der Server gerade nicht erreichbar, wird das Token trotzdem
gelöscht, und das Fenster nennt den Weg über **Verbundene Geräte** in der
Webanwendung.

## Hinweise

- Die `.c4d`-Datei trägt nur eine zufällige Kennung des Dokuments, über die das
  Plugin den zuletzt gewählten Blickpunkt vorschlägt. „Speichern unter"
  übernimmt diese Kennung; das Plugin schlägt dann für beide Dateien denselben
  Blickpunkt vor — gewählt wird immer von dir.
- Farbe: Viewport und Beauty gehen als PNG mit 8 Bit und eingebackener
  Ansichtstransformation (OCIO) an rendertaxi.ai; Datenpässe als lineares PNG
  mit 8 oder 16 Bit.
- Meldungen erscheinen in der Konsole (**Extensions › Console**).
  „Ausführliches Protokoll" in den Einstellungen schreibt bei Fehlern
  zusätzlich die Codestellen im Plugin — nie Pfade, Projekt- oder Dateinamen.
- Das Rendern und der Export des Modells blockieren Cinema 4D für ihre Dauer.
- Das Plugin ist eine **Vorschau**: automatisch geprüft ohne Cinema 4D, die
  Bedienung in Cinema 4D selbst steht aus — ebenso die Messung, ob das
  Sichtfeld der gesendeten Kamera auf den Pixel genau zum Bild passt. Seine Plugin-ID ist noch eine
  Entwicklungs-ID von Maxon; sie kann mit einem anderen Entwicklungs-Plugin
  kollidieren. Rückmeldungen bitte als Issue in diesem Repository.
