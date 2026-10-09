// Einstellungen des Add-Ons: ausschließlich die Serveradresse.
//
// Hier steht **kein** Geheimnis. Das Anmeldetoken liegt in der Keychain
// (`rtx::TokenStore`), der Zustand angefangener Übernahmen im
// `rtx::TransferStore`. Diese Datei darf ein Nutzer bedenkenlos weitergeben.
#pragma once

#include <string>
#include <vector>

namespace rtxaddon {

/** Serveradresse ohne abschließenden Schrägstrich. */
std::string ServerUrl ();
bool SetServerUrl (const std::string& url);

/**
 * Die Einstellung „Rahmengröße" (§7.2, `target.viewpoint.size`):
 * `canvas-default` oder `capture`. Sie überlebt den Neustart, weil sie eine
 * Einstellung ist und keine Eigenschaft einer einzelnen Aufnahme.
 */
std::string FrameSize ();
bool SetFrameSize (const std::string& value);

/**
 * Was eine Übernahme sendet (RTX-A-012): Bild, Modell oder beides. Vorgabe
 * „nur Bild", wie bis 1.1. **Regel 3:** ein unlesbarer oder leerer Wert ist
 * die Vorgabe — nie ein Grund, nicht zu laden. Beides aus wird beim Lesen zu
 * „nur Bild".
 */
struct WayChoice {
	bool image = true;
	bool model = false;
};
WayChoice Ways ();
bool SetWays (const WayChoice& ways);

/** „Zusätzliche Kameras mitsenden" (wie Cinema 4D, RTX-C4D-010); Vorgabe aus. */
bool ExtraCameras ();
bool SetExtraCameras (bool value);

/**
 * Die angehakten gespeicherten 3D-Ansichten **je Projekt** (Schlüssel aus
 * `LocalProjectKey`). Gelöschte Ansichten verlieren ihr Häkchen still beim
 * Aufbau der Liste (`rtx::BuildCameraPicks`).
 */
std::vector<std::string> CameraViews (const std::string& projectKey);
bool SetCameraViews (const std::string& projectKey, const std::vector<std::string>& guids);

/** Pfad der Protokolldatei unter `~/Library/Logs/rendertaxi/`. */
std::string LogPath ();

} // namespace rtxaddon
