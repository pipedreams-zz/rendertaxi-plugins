// Bildzugriff auf die aktuelle Ansicht.
//
// Stufe B des Auftrags: „Klären und belegen, welcher Bildzugriff in Archicad 28
// tatsächlich vorhanden ist". Die Belege stehen in
// `integrations/archicad/docs/capabilities.md`; hier steht der Weg, den das
// Add-On tatsächlich geht.
//
// Gewählt ist `ACAPI_ProjectOperation_Save` mit `APIFType_PNGFile` und
// `API_SavePars_Picture`: er ist der einzige dokumentierte Weg, der **PNG**
// schreibt, für 2D- und 3D-Fenster gilt und keine Rendering-Einstellungen
// verändert. `ACAPI_Rendering_PhotoRender` kann laut Dokumentation nur
// `APIFType_PictFile`, `BMPFile`, `TIFFFile`, `JPEGFile` und `GIFFile` — kein
// PNG — und stößt einen vollständigen Renderlauf an; er bleibt deshalb dem
// späteren `beauty`-Pass vorbehalten.
#pragma once

#include <string>

#include "rtx/Result.hpp"

namespace rtxaddon {

struct ViewCaptureResult {
	/** Vollständiger Pfad der geschriebenen PNG-Datei. */
	std::string filePath;
	/** Der benutzte Weg, so wie ihn `capabilities.md` benennt. */
	std::string method;
	/** Dauer des Aufrufs in Millisekunden — Grundlage der Messung in `capabilities.md`. */
	long long milliseconds = 0;
	/** Fenstergröße, sofern Archicad sie hergibt; 0, wenn nicht. */
	int windowWidth = 0;
	int windowHeight = 0;
};

/**
 * Schreibt die aktuelle Ansicht als PNG in `directory` und liefert Pfad und
 * Messwerte. Das Verzeichnis muss vorhanden sein.
 */
rtx::Result<ViewCaptureResult> CaptureCurrentViewAsPng (const std::string& directory,
														const std::string& fileName);

/**
 * **Rendert** die aktuelle 3D-Ansicht in der eingestellten Auflösung.
 *
 * Warum das nötig ist: `ACAPI_ProjectOperation_Save` sichert, was im Fenster
 * steht — also die **Bildschirmauflösung** des 3D-Fensters. Ein kleines
 * Fenster ergibt ein kleines Bild, unabhängig davon, was in den
 * Photorealistik-Einstellungen steht. Für ein Basisbild ist das zu wenig.
 *
 * `ACAPI_Rendering_PhotoRender` rechnet dagegen genau die eingestellte Größe
 * und mit der eingestellten Maschine — fotorealistisch mit Cineware oder
 * vereinfacht mit der Skizzen-Maschine, je nachdem, was in Archicad gewählt
 * ist. Das dauert; deshalb ist es eine **eigene Handlung** und nicht der
 * stille Normalweg.
 *
 * Der erste Anlauf versuchte stattdessen, das **fertige** Renderfenster mit
 * `ACAPI_Window_ChangeWindow` nach vorn zu holen und zu sichern. Das schlug
 * am 24.09.2026 fehl: der Wechsel wurde abgelehnt, obwohl ein Bildfenster
 * offen stand. Selbst rechnen ist ohnehin das ehrlichere Verfahren — es ist
 * unabhängig davon, ob und wann jemand zuletzt gerendert hat.
 *
 * PhotoRender kennt kein PNG (PICT, BMP, TIFF, JPEG, GIF). Gerendert wird
 * deshalb nach TIFF und verlustfrei nach PNG umgeschrieben.
 */
rtx::Result<ViewCaptureResult> RenderCurrentViewAsPng (const std::string& directory,
													   const std::string& fileName);

/** Übersetzt einen Archicad-Fehlercode in einen Satz, den ein Nutzer versteht. */
std::string ExplainArchicadError (long errorCode);

} // namespace rtxaddon
