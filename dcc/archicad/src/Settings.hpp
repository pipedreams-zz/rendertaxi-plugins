// Einstellungen des Add-Ons: ausschließlich die Serveradresse.
//
// Hier steht **kein** Geheimnis. Das Anmeldetoken liegt in der Keychain
// (`rtx::TokenStore`), der Zustand angefangener Übernahmen im
// `rtx::TransferStore`. Diese Datei darf ein Nutzer bedenkenlos weitergeben.
#pragma once

#include <string>

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

/** Pfad der Protokolldatei unter `~/Library/Logs/rendertaxi/`. */
std::string LogPath ();

} // namespace rtxaddon
