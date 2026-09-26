// Angaben über den Host, die das Manifest verlangt — gelesen, nicht geraten.
//
// `source.host`, `source.machine`, `project.sourceProjectKey` und
// `view.sourceViewKey` stammen aus dem Archicad-API. Wo Archicad 28 nichts
// Stabiles hergibt, trägt das Manifest `null`; der Vertrag verlangt das
// ausdrücklich, statt einen Schlüssel aus einem Anzeigenamen zu erfinden
// (`capture-manifest.md`, Abschnitt 4).
#pragma once

#include <string>
#include <vector>

namespace rtxaddon {

struct HostVersion {
	std::string version;   ///< etwa "28.0"
	std::string build;     ///< Buildnummer als Zeichenkette, etwa "7006"
	int mainVersion = 0;
};

HostVersion ReadHostVersion ();

/** `os`, `osVersion`, `architecture` für `source.machine`. */
struct MachineInfo {
	std::string os = "macos";
	std::string osVersion;
	std::string architecture;
};

MachineInfo ReadMachineInfo ();

/** Die aktuelle Quellansicht. */
struct SourceView {
	/** Stabiler Schlüssel oder leer, wenn Archicad 28 keinen hergibt. */
	std::string key;
	/** Anzeigename der Ansicht, für den Namensvorschlag des Blickpunkts. */
	std::string displayName;
	/** Name des Eintrags aus der **Ausschnittsmappe**, falls einer offen ist. */
	std::string viewMapName;
	/** Fenstertyp als lesbares Wort, etwa `3d` oder `section`. */
	std::string windowKind;
	/** Fenstertyp als deutscher Anzeigetext für den Namensvorschlag. */
	std::string windowLabel;
	/** Wahr, wenn dies das 3D-Fenster ist. */
	bool is3D = false;
	/** Wahr, wenn aus diesem Fenster überhaupt ein Bild entstehen kann. */
	bool capturable = false;
	/** Das Fenster der Photorealistik — ein **fertiges** Bild, kein Modellzeichnen. */
	bool isRendering = false;
};

SourceView ReadCurrentView ();

/**
 * Merkt sich den Ausschnitt, den Archicad gerade aus der **Ausschnittsmappe**
 * geöffnet hat.
 *
 * Archicad 28 kennt keinen Aufruf „welcher Ausschnitt steht gerade?" — deshalb
 * stand im Namensvorschlag bis zum 24.09.2026 immer die Fensterart
 * („Grundriss"), egal welchen Ausschnitt der Nutzer geöffnet hatte. Es gibt
 * aber eine **Benachrichtigung**: `ACAPI_Notification_CatchViewEvent` meldet
 * mit `APINotifyView_Opened` die Kennung des geöffneten Navigatoreintrags. Das
 * Add-on hört zu und merkt sich Name und Datenbank des Eintrags; der
 * Namensvorschlag nimmt ihn, solange er zur Datenbank des aktuellen Fensters
 * gehört.
 *
 * Der Aufruf gehört in `Initialize`; er meldet das Add-on beim Navigator an.
 */
long InstallViewTracking ();

/**
 * Die **Rendering-Szene**, und damit der Bildausschnitt, den Archicad im
 * 3D-Fenster als „Render-Schutzbereich" einblendet.
 *
 * Sie ist die einzige Stelle, an der ein Archicad-Nutzer den finalen Ausschnitt
 * **sieht, bevor** er die Ansicht festlegt: `API_RendImage.hSize`/`vSize` ist
 * die Bildgröße der Szene, der Schutzbereich zeichnet genau dieses Verhältnis
 * mittig ins 3D-Fenster, und `ACAPI_Rendering_PhotoRender` rendert genau diese
 * Größe. `API_NavigatorView.renderingSceneName` bindet eine Szene an eine
 * gespeicherte Ansicht — der Ausschnitt ist damit speicherbar und übertragbar.
 */
struct RenderScene {
	bool known = false;
	/** Name der aktuellen Szene; leer, wenn sie keinen trägt. */
	std::string name;
	int width = 0;
	int height = 0;
};

/** Liest die **aktuelle** Rendering-Szene — die, die der Schutzbereich zeigt. */
RenderScene ReadCurrentRenderScene ();

/**
 * Der **3D-Darstellungsmodus**, den Archicad unter „Ansicht ▸
 * 3D-Darstellungsmodus" führt — „Schattierung mit Schatten", „Weißmodell",
 * „Zeichnung (Vektor)" und so weiter.
 *
 * Er entscheidet, wie „Aktuelle Ansicht übernehmen" aussieht, denn dieser Weg
 * sichert das Fenster. Der Rendering-Weg kennt ihn **nicht**: dort rechnet die
 * Maschine aus den Photorealistik-Einstellungen. Beides steht in der Palette
 * nebeneinander, damit die Wahl zwischen den zwei Knöpfen eine informierte
 * ist. Leer, wenn Archicad keinen Namen hergibt.
 */
std::string ReadCurrent3DStyle ();

/** Namen aller benannten Rendering-Szenen des Projekts. */
std::vector<std::string> ReadRenderSceneNames ();

/**
 * Stabiler, im Archicad-Projekt persistierter Schlüssel.
 * Leer, wenn er sich nicht anlegen ließ — dann steht im Manifest `null`.
 */
std::string ReadOrCreateProjectKey ();

/** Nur lokal: Schlüssel für den Zustandsspeicher, auch ohne Add-On Object. */
std::string LocalProjectKey ();

/** Anzeigename des Archicad-Projekts; reine Anzeige. */
std::string ProjectDisplayName ();

} // namespace rtxaddon
