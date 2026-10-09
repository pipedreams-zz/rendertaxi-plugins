// Der Modellweg am Host (RTX-A-012, #307): Geometrie des 3D-Fensters über
// ModelAccess und die Kameras der Ansichten — übernommen aus dem Messauftrag
// RTX-A-010 (`spike/model-glb/ModelGlbSpike.cpp`, `docs/model-glb-spike.md`).
//
// Hier steht nur, was ACAPI braucht. Abbildung nach glTF, Kameras, Grenzen und
// Datei entstehen im DevKit-freien Kern (`rtx/Glb.hpp`, `rtx/ArchicadCamera.hpp`,
// `rtx/ModelCapture.hpp`). Alles hier läuft im **Hauptfaden**.
#pragma once

#include "APIEnvir.h"
#include "ACAPinc.h"

#include <string>
#include <vector>

#include "rtx/ModelCapture.hpp"
#include "rtx/SavedViews.hpp"

namespace rtxaddon {

/** Was das Lesen der Geometrie ergab — Zählungen für Palette und Protokoll. */
struct ModelExtraction {
	/** ModelAccess lieferte 0 Körper ohne Fehlercode: Archicad baut das 3D-Modell neu auf (QA-09). */
	bool rebuilding = false;
	/** Leer bei Erfolg, sonst ein Satz für die Palette. */
	std::string error;
	Int32 elements = 0;
	Int64 bodies = 0;
	Int64 polygons = 0;
	Int64 polygonErrors = 0;
	double milliseconds = 0.0;
};

/**
 * Liest die sichtbare Geometrie des **3D-Fensters** in `builder`: ein Element je
 * Knoten mit GUID, Oberflächenfarbe und Transparenz, Umlaufsinn gegen die
 * Flächennormale. Die Sicht des 3D-Fensters wird dafür ausdrücklich gewählt und
 * danach zurückgesetzt — ohne gewählte Sicht liefert ModelAccess nichts
 * (gemessen, QA-01).
 */
ModelExtraction ExtractWindowModel (rtx::GlbSceneBuilder& builder);

/** Zahl der Körper des 3D-Fensters; −1 bei einem Fehler. Für das Warten auf den Neuaufbau. */
Int32 CountWindowBodies ();

/** Die Projektion des 3D-Fensters, Feld für Feld. Fehler, wenn Archicad sie nicht hergibt. */
rtx::Result<rtx::ArchicadProjection> ReadWindowProjection ();

/**
 * Die Kameras der genannten gespeicherten 3D-Ansichten: jede wird geöffnet
 * (`ACAPI_View_GoToView`), ihre Projektion gelesen. Vorher sichert
 * `ViewStateGuard` den Ansichtsstand, danach steht er wieder im Fenster
 * (F-02 an #318). Das geht nur, wenn das Fenster eine gespeicherte Ansicht
 * zeigt; eine freie Ansicht wird nie verlassen — dann wird keine Ansicht
 * geöffnet, und `warning` sagt es. Nicht mehr vorhandene Ansichten fallen weg;
 * `skipped` nennt sie.
 *
 * Nebenwirkung, gemessen (QA-09): jede Ansicht wechselt Ebenen und Ausschnitt
 * des Fensters und stößt einen Neuaufbau an. Deshalb wird die Geometrie
 * **vorher** gelesen.
 */
struct SavedViewCameras {
	std::vector<rtx::NamedProjection> cameras;
	std::vector<std::string> skipped;   ///< Ansichten, die es nicht mehr gibt oder die nicht lesbar waren
	std::string warning;                ///< für Palette und Protokoll: Stand nicht gesichert oder nicht zurück
};
SavedViewCameras ReadSavedViewCameras (const std::vector<rtx::SavedView>& views);

/** Die 3D-Schnittebenen des Fensters (QA-10): eingeschaltet und wie viele. */
struct CutPlanes {
	bool readable = false;
	bool enabled = false;
	int count = 0;
};
CutPlanes ReadCutPlanes ();

} // namespace rtxaddon
