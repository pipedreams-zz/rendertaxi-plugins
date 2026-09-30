# rendertaxi.ai für Blender 5.2 LTS

Die Extension übergibt die **aktuelle Ansicht** oder das **gerenderte Bild**
an rendertaxi.ai — auf Wunsch zusammen mit dem **Modell** und der Kamera —,
ordnet es einem Projekt und einem Blickpunkt zu — neu oder als ausdrückliches
Update — und öffnet genau diesen Blickpunkt im Browser. Generierung und
Ergebnisbearbeitung bleiben in der Webanwendung.

## Installation

Voraussetzung: Blender 5.2 LTS (macOS, Windows, Linux). Die Zip
`rendertaxi-blender5.2-<Version>-<Release>.zip` (etwa
`rendertaxi-blender5.2-0.3.0-2026.09.29.zip`) von der
[Release-Seite](https://github.com/pipedreams-zz/rendertaxi-plugins/releases/latest)
laden (in der Webanwendung unter **Verbundene Geräte › Plugins herunterladen**).
**Nicht entpacken** — die Zip ist die Extension.

1. Blender öffnen, **Edit › Preferences › Get Extensions** (Menünamen wie in
   Blenders englischer Oberfläche).
2. Oben rechts im Menü (Pfeil nach unten) **Install from Disk…** wählen und
   die Zip auswählen.
3. Die Extension **rendertaxi.ai** erscheint unter **Add-ons** und ist
   eingeschaltet.
4. **Edit › Preferences › System › Network › Allow Online Access** muss an
   sein; ohne diese Freigabe verbindet sich die Extension nicht.

Alternativ die Zip auf ein Blender-Fenster ziehen.

**Aktualisieren:** eine neuere Zip genauso installieren. **Entfernen:**
**Edit › Preferences › Add-ons › rendertaxi.ai** › Menü › **Uninstall**.

## Verbinden

1. In der 3D-Ansicht mit **N** die Seitenleiste öffnen, Reiter
   **rendertaxi**.
2. Unter **Verbindung** steht die Serveradresse (Vorgabe
   `https://dev.rendertaxi.ai`, änderbar unter **Edit › Preferences › Add-ons ›
   rendertaxi.ai** —
   nur ändern, wenn euer Büro eine andere Adresse nennt).
3. **Verbinden** zeigt einen Code und öffnet den Browser. Dort mit dem
   rendertaxi.ai-Konto anmelden und das Gerät bestätigen. Das Panel zeigt
   danach „Angemeldet als …".

Die Extension hat **kein Passwortfeld**. Sie erhält nur ein widerrufbares Token
und legt es in ihrem Nutzerordner ab, nur für dich lesbar — nie in der
`.blend`-Datei. Angemeldete Geräte stehen in der Webanwendung unter
**Verbundene Geräte** (`/geraete`) und lassen sich dort abmelden. Ein
Gerätename (dieselben Einstellungen) hilft, Blender dort wiederzuerkennen.

## Bild übernehmen

1. Unter **Projekt und Blickpunkt** das Projekt wählen.
2. Entweder **Neuer Blickpunkt** mit Namen oder **Bestehenden aktualisieren**
   und den Blickpunkt wählen. Ein Update legt keinen zweiten Blickpunkt an.
3. Unter **Bild übernehmen** die Aufnahmeart wählen:
   - **Viewport** — die aktuelle 3D-Ansicht, so wie sie zu sehen ist.
     **Overlays ausblenden** (Vorgabe) lässt Gitter, Gizmos und Auswahlumrisse
     weg.
   - **Beauty** — rendert aus der aktiven Kamera mit der eingestellten Engine.
     Die Szene braucht eine Kamera.

   Die Größe kommt aus **Output Properties › Format** (Auflösung × Prozent); das Panel
   nennt sie als „Ausschnitt". Beim Aktualisieren lässt sich stattdessen die
   Größe des Blickpunkt-Rahmens wählen.
4. **Aufnehmen und übernehmen.** Der Fortschritt steht im Panel;
   **Abbrechen** hält eine laufende Übertragung an. Eine unterbrochene
   Übernahme wird mit **Übernahme fortsetzen** zu Ende geführt oder mit
   **Angefangene Übernahme verwerfen** verworfen.
5. **Blickpunkt im Browser öffnen** springt zum Ergebnis.

### Pässe (Beauty)

Bei **Beauty** zeigt das Panel die Pässe, die Blender liefern kann: Tiefe,
Normalen, Albedo, Objekt-ID, Material-ID. Die Extension schaltet keinen Pass
selbst ein; ist einer nicht wählbar, steht darunter, was einzustellen ist
(etwa „View Layer › Passes › Data › Object Index (Cycles) einschalten").

Gewählte Pässe gehen als **PNG** mit — je Pass eine Datei, von Blender im
selben Rendering geschrieben wie das Bild. **Bittiefe der Datenpässe**:
**8 Bit (Standard)** oder **16 Bit**; die Wahl steht im Panel über den Pässen
und wird gemerkt (auch unter **Edit › Preferences › Add-ons ›
rendertaxi.ai**). 16 Bit ist genauer und doppelt so groß.

- **Tiefe** wird zwischen **Clip Start** und **Clip End** der Kamera
  gespeichert (Object Data Properties der Kamera): nah = dunkel, fern = hell,
  ohne Treffer weiß. Ein enger Clipbereich um das Modell ergibt eine feinere
  Tiefe. Tiefe geht nur mit **Unit System** „Metric" oder „Imperial" und
  **Unit Scale 1** mit.
- **Normalen** im Weltraum, **Albedo** linear, **Objekt-ID** und
  **Material-ID** als Pass-Index. Mit 8 Bit passen Indizes bis 255; ist einer
  größer, sagt das Panel es, und der Pass geht nicht mit — dann 16 Bit wählen.
- Die Extension ändert dafür nichts dauerhaft an der Szene: Compositing und
  Ausgabeeinstellungen sind nach der Aufnahme wie vorher. Blender meldet beim
  Schreiben jeder Pass-Datei selbst eine Zeile „Saved: …" in der
  Systemkonsole.

## Modell mitsenden

Unter **Bild übernehmen** schaltet **Modell mitsenden** (Standard aus) die
sichtbaren Objekte als eine GLB-Datei (glTF 2.0) und die Kamera der Aufnahme
dazu. In der Webanwendung erscheint das Modell als Asset am Blickpunkt; auf
einen Rahmen gezogen, zeigt **Modell im Rahmen** es aus genau dieser Kamera.

- **Was exportiert wird:** alle in der 3D-Ansicht sichtbaren Objekte der
  aktuellen Szene, Modifier angewendet, ohne Animation und Lichter. Die
  sichtbaren **Kameras** der Szene gehen mit ihren Namen mit (seit 0.4.0) —
  im Blickpunkt lassen sie sich unter „Kameras im Modell" übernehmen.
  **Materialien und Texturen** nur, wenn angehakt — die Datei wird dann
  größer.
- **Vor dem Senden** nennt das Panel die Zahl der Dreiecke und sichtbaren
  Objekte (**Modell neu zählen** zählt nach Änderungen neu) und die größte
  Modelldatei, die der Server annimmt. Liegt das Modell darüber, wird nichts
  gesendet.
- **Kamera:** bei **Beauty** die aktive Kamera, bei **Viewport** die
  3D-Ansicht (perspektivisch oder parallel; in der Kameraansicht die Kamera).
  Brennweite, Sensor und **Shift** gehen mit, wenn der Server
  Capture-Manifest 1.4 kennt; gegen einen älteren Server geht eine Kamera mit
  Shift nicht mit. Eine Panoramakamera oder ein Pixel Aspect ungleich 1:1
  lässt sich im Vertrag nicht beschreiben: das Modell geht dann ohne Kamera
  mit, und das Panel sagt es vorher.
- **Einheiten:** das Modell geht nur mit **Scene › Units › Unit System**
  „Metric" oder „Imperial" und **Unit Scale 1**. Blenders Exporter schreiben
  eine Einheit als einen Meter, unabhängig von Unit Scale; mit einem anderen
  Wert wäre der Maßstab nicht eindeutig. Das Panel nennt den Grund, statt zu
  raten.
- Ohne **Modell mitsenden** bleibt alles wie bisher: nur das Bild.

Der Server muss den Modellweg kennen; tut er es nicht, sagt das Panel es, und
übernommen wird nur ohne Modell.

## Rahmengröße

Die Einstellung **Rahmengröße** gehört zum Ziel, nicht zur Aufnahme:

- **Canvas-Vorgabe (Standard):** der Blickpunkt behält die Größe der
  Webanwendung; übernommen wird nur das Seitenverhältnis der Aufnahme.
- **Render-Einstellung übernehmen:** der Rahmen bekommt die Maße der
  Aufnahme. Ist die lange Kante kürzer als 1536 Pixel, bleibt die
  Canvas-Vorgabe.

Beim Aktualisieren eines bestehenden Blickpunkts ändert sich dessen Rahmen nur,
wenn **Rahmen an Aufnahme anpassen** angehakt ist; sonst bleibt er, wie er ist.
Weicht der Rahmen vom Seitenverhältnis der Aufnahme ab, sagt das Panel das,
statt es still aufzulösen.

## Abmelden

**Abmelden** im Panel widerruft das Token beim Server **und** löscht es auf
diesem Rechner. Ist der Server gerade nicht erreichbar, wird das Token trotzdem
gelöscht, und das Panel nennt den Weg über **Verbundene Geräte** in der
Webanwendung.

## Hinweise

- Anmeldung, Einstellungen und angefangene Übernahmen liegen im Nutzerordner
  der Extension (unter Blenders Konfigurationsordner,
  `extensions/.user/user_default/rendertaxi/`). Die `.blend`-Datei trägt nur
  eine zufällige Kennung der Szene, über die die Extension den zuletzt
  gewählten Blickpunkt vorschlägt.
- Meldungen erscheinen in der Systemkonsole (**Window › Toggle System
  Console** unter Windows, das Terminal unter macOS/Linux). „Ausführliches Protokoll" in den
  Einstellungen schreibt bei Fehlern zusätzlich die Codestellen im Add-on —
  nie Pfade, Projekt- oder Dateinamen. Der glTF-Exporter von Blender nennt
  beim Export die Namen der Objekte selbst in der Konsole.
- Das Rendern für **Beauty** und der Export des Modells blockieren Blender
  für ihre Dauer, wie ein gewöhnliches Rendering oder ein Export.
- Die Extension ist eine Vorschau: automatisch geprüft, die Bedienung am Mac
  mit echtem Display steht aus. Rückmeldungen bitte als Issue in diesem
  Repository.
