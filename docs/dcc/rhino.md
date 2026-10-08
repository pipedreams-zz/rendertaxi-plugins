# rdtx.ai für Rhino 8

Das Plugin übergibt die **aktuelle Ansicht** oder ein **Rendering mit
Datenpässen** und auf Wunsch das **Modell** (GLB mit den benannten Ansichten als
Kameras) an rendertaxi.ai, ordnet es einem Projekt und einem Blickpunkt zu —
neu oder als ausdrückliches Update — und öffnet genau diesen Blickpunkt im
Browser. Generierung und Ergebnisbearbeitung bleiben in der Webanwendung.

## Installation

Voraussetzung: **Rhino 8** (macOS oder Windows). Ältere Fassungen werden nicht
unterstützt — das Plugin lädt dort, sagt aber nur, dass es Rhino 8 braucht.
Das Paket `rdtx.ai-<Version>+<Build>-rh8-any.yak` (etwa
`rdtx.ai-0.2.0+28396-rh8-any.yak`) von der
[Release-Seite](https://github.com/pipedreams-zz/rendertaxi-plugins/releases/latest)
laden (in der Webanwendung unter **Verbundene Geräte › Plugins herunterladen**).

1. Die `.yak`-Datei in ein offenes Rhino-Fenster **ziehen** — oder
   **_PackageManager** öffnen, das Zahnrad oben rechts, **Datei wählen** und die
   `.yak`-Datei angeben.
2. Rhino neu starten.
3. Befehl **RdtxAI** eingeben. Die Werkzeugleiste **rdtx.ai** (ein Knopf)
   erscheint nach der Installation; sonst unter **Optionen › Werkzeugleisten ›
   rdtx.ai** einschalten.

**Aktualisieren:** das neue Paket genauso installieren und Rhino neu starten.
Anmeldung und Einstellungen bleiben erhalten (sie liegen nicht im Paket).
**Entfernen:** **_PackageManager › Installiert › rdtx.ai › Deinstallieren**,
Rhino neu starten. Anmeldung und Protokoll liegen in deinem Nutzerordner
(macOS `~/Library/Application Support/rdtx.ai/rhino`, Windows
`%APPDATA%\rdtx.ai\rhino`); wer sie ebenfalls entfernen will, löscht genau
diesen Ordner — vorher in der Webanwendung unter **Verbundene Geräte** abmelden
oder im Plugin **Abmelden**.

## Verbinden

1. **RdtxAI** öffnet das Fenster von rdtx.ai.
2. Unter **Verbindung** steht die Serveradresse (Vorgabe
   `https://dev.rendertaxi.ai`) — nur ändern, wenn euer Büro eine andere Adresse
   nennt, dann **Einstellungen sichern**. Ein **Gerätename** hilft, Rhino später
   wiederzuerkennen.
3. **Verbinden** zeigt einen Code und öffnet den Browser. Dort mit dem
   rendertaxi.ai-Konto anmelden und das Gerät bestätigen. Das Fenster zeigt
   danach „Angemeldet als …".

Das Plugin hat **kein Passwortfeld**. Es erhält nur ein widerrufbares Token und
legt es im Nutzerordner ab, nur für dich lesbar — **nie** in der `.3dm`-Datei.

## Bild übernehmen

1. **Projekt** wählen — oder mit **Projekt anlegen …** eines anlegen (mit den
   Rechten deiner Rolle im Büro); es ist danach gewählt. **Aktualisieren** liest
   Projekte und Blickpunkte neu.
2. Unter **Blickpunkt** die **Ansicht** wählen: die aktive Ansicht oder eine
   **benannte Ansicht** des Dokuments. Für eine benannte Ansicht merkt sich das
   Plugin, welchem Blickpunkt du sie zugeordnet hast, und schlägt ihn beim
   nächsten Mal vor. Die aktive Ansicht steht nach der Aufnahme wieder genau
   wie vorher.
3. **Neuer Blickpunkt** mit Namen (der Name der Ansicht steht schon da) oder
   **Bestehenden aktualisieren** und den Blickpunkt wählen.
4. Unter **Bild** die Aufnahmeart:
   - **Ansicht** — die Ansicht in ihrem Anzeigemodus, ohne Gitter und Achsen.
   - **Rendering** — mit dem aktuellen Renderer; die **Pässe** Tiefe, Normalen,
     Albedo, Objekt-ID und Material-ID liefert **Rhino Render**. Ist ein anderer
     Renderer gewählt, sagt das Fenster es bei den Pässen.

   Die Größe kommt aus den Rendereinstellungen des Dokuments (Ausgabegröße);
   beim Aktualisieren lässt sich stattdessen die Größe des
   **Blickpunkt-Rahmens** wählen. Für das Rendern setzt das Plugin Größe,
   Kanäle und Quelle nur für diesen einen Lauf; danach stehen deine
   Rendereinstellungen wieder genau wie vorher.
5. **Bild übernehmen.** Das Rendern hält den Befehl für seine Dauer an (Esc im
   Renderfenster bricht ab); das Fenster zeigt den Fortschritt. Danach läuft
   die Übertragung im Hintergrund; **Abbrechen** hält sie an, **Fortsetzen**
   führt eine unterbrochene Übernahme zu Ende, **Verwerfen** verwirft sie.
6. **Im Browser öffnen** springt zum Blickpunkt.

Datenpässe gehen als **PNG** an rendertaxi.ai, je Pass eine Datei, mit
**8 Bit (Standard)** oder **16 Bit** — die Wahl merkt sich das Plugin.

## Modell

Unter **Modell** schaltet **Modell senden** das Modell zu. Unter **Bild** schaltet
**Bild senden** das Bild ab oder zu. So gehen **nur Bild**, **nur Modell** oder
**beides**; der Knopf heißt danach „Bild übernehmen", „Modell übernehmen" oder
„Bild und Modell übernehmen".

- **Was mitgeht:** alles, was in der aktiven Ansicht sichtbar ist, als
  Dreiecksnetz — Polyflächen, Extrusionen, Flächen, SubD, Netze und Blöcke.
  Ausgeblendete Objekte und ausgeschaltete Ebenen gehen nicht mit. Kurven,
  Punkte und Texte haben keine Fläche; das Fenster nennt ihre Zahl.
- **Maße:** Das Modell kommt in Metern an, gleich ob die Datei in mm, cm oder m
  ist. Eine Datei ohne Längeneinheit sendet kein Modell; dann unter
  **Dokumenteigenschaften › Einheiten** eine Einheit wählen.
- **Kameras:** Jede **benannte Ansicht** geht als Kamera ins Modell und lässt
  sich in der Webanwendung als Blickpunkt übernehmen.
- **Nur Modell:** Es wird nicht gerendert. Die Kamera kommt aus der gewählten
  Ansicht, die Bildgröße aus den Rendereinstellungen oder dem Blickpunkt-Rahmen.
- **Materialfarben mitsenden:** gibt jedem Objekt die Farbe seines
  Rendermaterials mit; ohne die Option kommt das Modell einfarbig an.
- **Neu zählen:** zählt Objekte und Dreiecke. Grenzen (50 Mio. Dreiecke und die
  Dateigröße, die der Server nennt) prüft das Plugin **vor** dem Senden.
- **Schnittebenen** wirken nicht auf das Modell; es geht ganz mit.

Deine Datei bleibt dabei unverändert: Das Plugin vernetzt in einer eigenen
Kopie, speichert nichts im Dokument und löscht die Modelldatei nach der
Übertragung.

## Rahmengröße

- **Vorgabe der Leinwand (Standard):** der Blickpunkt behält die Größe der
  Webanwendung; übernommen wird nur das Seitenverhältnis der Aufnahme.
- **Wie die Aufnahme:** der Rahmen bekommt die Maße der Aufnahme.

Beim Aktualisieren ändert sich der Rahmen nur, wenn **Rahmen an Aufnahme
anpassen** angehakt ist. Weicht er vom Seitenverhältnis der Aufnahme ab, sagt
das Fenster das.

## Abmelden

**Abmelden** widerruft das Token beim Server **und** löscht es auf diesem
Rechner. Ist der Server nicht erreichbar, wird das Token trotzdem gelöscht, und
das Fenster nennt den Weg über **Verbundene Geräte** in der Webanwendung.

## Hinweise

- Die `.3dm`-Datei trägt nur eine zufällige Kennung des Dokuments (verborgener
  Dokument-Text), über die das Plugin den zuletzt gewählten Blickpunkt
  vorschlägt. „Speichern unter" und Import übernehmen diese Kennung — gewählt
  wird immer von dir.
- Meldungen stehen auf der Rhino-Kommandozeile und in `rhino.log` im
  Nutzerordner. „Ausführliches Protokoll" schreibt bei Fehlern zusätzlich die
  Codestellen im Plugin — nie Pfade, Projekt- oder Dateinamen, nie ein Token.
- Fuß und **Über …** nennen Fassung und Build-Kennung; bitte bei Rückmeldungen
  angeben.
- Das Plugin ist eine **Vorschau**: auf macOS in Rhino 8.35 geprüft (Bild- und
  Modellweg), die Windows-Prüfung steht aus. Rückmeldungen bitte als Issue in diesem Repository.
