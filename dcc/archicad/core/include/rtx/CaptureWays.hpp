// Bildweg und Modellweg — zwei Wege, einzeln oder zusammen (RTX-A-012, #307;
// `capture-manifest.md`, Abschnitt 14). Das C++-Gegenstück zu
// `rendertaxi_client.ways` und den Fassungsregeln aus `manifest.py`.
//
// Nutzervorgabe vom 05.10.2026: „Bild- und Modellweg sind als zwei
// verschiedene Wege zu sehen, beide können einzeln, aber auch kombiniert
// laufen." Drei gleichwertige Fälle: nur Bild, nur Modell (ohne Rendern, ab
// Capture-Manifest 1.6.0), Bild und Modell.
//
// **Ein gemerkter Zustand darf das Laden nie verhindern** (Regel 3): eine
// gemerkte Wahl „nur Modell" gegen einen Server unter 1.6.0 bricht nichts ab —
// sie fällt mit Hinweis auf „Bild und Modell" zurück; ein Modell gegen einen
// Server unter 1.2.0 fällt mit Hinweis weg.
#pragma once

#include <string>
#include <vector>

#include "rtx/PluginApi.hpp"

namespace rtx {

/** Die höchste MINOR, die der Server für das Capture-Manifest nennt (`negotiation.highestSupportedVersion`); −1 ohne Angabe. */
int HighestCaptureMinor (const HandshakeInfo& handshake);

/**
 * Fassung des Bildwegs: 1.6.0, 1.5.0 (Name der Ursprungsdatei) oder 1.3.0
 * (PNG), wenn der Server sie umsetzt, sonst 1.1.0; gegen einen Server mit
 * höchstens 1.0 und ohne Angabe 1.0.0 — so schrieb das Add-on bis 1.1.
 */
std::string ImageContractVersion (int highestMinor);

/**
 * Fassung mit Modell: die höchste, die der Server umsetzt und dieser Kern
 * schreibt — 1.6.0 bis 1.2.0. Leer, wenn der Server kein Modell annimmt
 * (unter 1.2.0 oder ohne Angabe).
 */
std::string ModelContractVersion (int highestMinor);

/** Was eine Übernahme sendet — und warum es anders ist als gewählt (`hint`), wenn es das ist. */
struct CapturePlan {
	bool image = false;
	bool model = false;
	std::string hint;

	bool ModelOnly () const { return model && !image; }
};

inline constexpr const char* kNothingChosen = "Bitte Bild, Modell oder beides zum Senden wählen.";
inline constexpr const char* kModelOnlyFallback =
	"Dieser Server nimmt eine Aufnahme nur mit Modell noch nicht an; gesendet werden Bild und Modell.";
inline constexpr const char* kModelUnsupported =
	"Dieser Server nimmt noch kein Modell an; gesendet wird nur das Bild.";

/**
 * Die Wahl der Palette gegen das, was der Server annimmt. `highestMinor` −1
 * heißt „noch nicht verbunden": dann gilt die Wahl, wie sie ist. Nichts
 * gewählt ist der einzige Fehler.
 */
Result<CapturePlan> PlanCapture (bool sendImage, bool sendModel, int highestMinor);

/** Die Fassung des Manifests für diesen Plan. */
std::string PlanContractVersion (const CapturePlan& plan, int highestMinor);

/** „Bild", „Modell" oder „Bild und Modell". */
std::string PlanLabel (const CapturePlan& plan);

/** Was gesendet wird, in einem Satz — über dem Knopf „Übernehmen". */
std::string PlanSummary (const CapturePlan& plan);

/** Die Schritte einer Übernahme in Nutzersprache, in der Reihenfolge, in der sie laufen. */
std::vector<std::string> PlanSteps (const CapturePlan& plan);

/**
 * Was angekommen ist — aus `result.assetIds` je Rolle. Bei einem Update sagt
 * der Satz zusätzlich, was am Blickpunkt stehen geblieben ist.
 */
std::string PlanResultText (const CaptureResult& result, bool update);

} // namespace rtx
