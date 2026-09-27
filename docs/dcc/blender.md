# rendertaxi.ai für Blender 5.2 LTS

Die Extension übergibt die **aktuelle Ansicht** oder das **gerenderte Bild**
an rendertaxi.ai, ordnet es einem Projekt und einem Blickpunkt zu — neu oder
als ausdrückliches Update — und öffnet genau diesen Blickpunkt im Browser.
Generierung und Ergebnisbearbeitung bleiben in der Webanwendung.

## Installation

Voraussetzung: Blender 5.2 LTS (macOS, Windows, Linux). Die Zip
`rendertaxi-blender5.2-<Version>-<Release>.zip` (etwa
`rendertaxi-blender5.2-0.1.0-2026.09.27.zip`) von der
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

Datenpässe entstehen als OpenEXR. **rendertaxi.ai nimmt EXR-Dateien derzeit
nicht an**; gewählte Pässe werden deshalb nur im Manifest als „geplant"
vermerkt und nicht übertragen. Tiefe und Normalen bleiben unabhängig davon
„geplant", bis ihre Kodierung geklärt ist.

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
  Einstellungen schreibt zusätzlich Pfade und Namen — nur zur Fehlersuche.
- Das Rendern für **Beauty** blockiert Blender für seine Dauer, wie ein
  gewöhnliches Rendering.
- Die Extension ist eine Vorschau: automatisch geprüft, die Bedienung am Mac
  mit echtem Display steht aus. Rückmeldungen bitte als Issue in diesem
  Repository.
