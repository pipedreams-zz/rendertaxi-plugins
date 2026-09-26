// Ablage des Anmeldetokens.
//
// Festlegung 4 aus Issue #20: „Kein Provider-Schlüssel und kein Passwort im
// Plugin. Anmeldung über Gerätelogin; das Anmeldetoken liegt in der macOS
// Keychain, nie in Klartextdateien, Projektdateien oder Logs, und ist
// serverseitig widerrufbar."
//
// Der Port hat zwei Umsetzungen: die Keychain für das Add-On und eine
// Speicherfassung für die Tests. Es gibt bewusst **keine** Dateifassung — sie
// wäre genau die Klartextablage, die die Festlegung verbietet.
#pragma once

#include <cstdint>
#include <memory>
#include <string>

#include "rtx/Result.hpp"

namespace rtx {

struct StoredCredential {
	std::string accessToken;
	/**
	 * Die **einmal** erzeugte UUIDv7 dieser Installation; sie liegt neben dem
	 * Token im Schlüsselspeicher (`plugin-api-v1.md`, Abschnitt 5.5).
	 */
	std::string deviceId;
	/** Unix-Zeit in Millisekunden, zu der `accessToken` abläuft; 0 heißt unbekannt. */
	std::uint64_t expiresAtMillis = 0;
	/** Anzeigename des angemeldeten Kontos — reine Anzeige, nie Identität. */
	std::string displayName;
	/** Organisation, die der Server aus der Mitgliedschaft aufgelöst hat. */
	std::string organizationId;
	std::string organizationName;
	/**
	 * `membership_missing` oder `organization_selection_required`, wenn der
	 * Gerätelogin gelang, aber `GET /plugin/me` die Mitgliedschaft ablehnt
	 * (§5.3). Leer heißt: nichts zu melden.
	 */
	std::string membershipIssue;
};


class TokenStore {
public:
	virtual ~TokenStore () = default;

	/** Liest das Token für eine Serveradresse; Fehlercode `unauthorized`, wenn keines da ist. */
	virtual Result<StoredCredential> Load (const std::string& serverUrl) = 0;
	/**
	 * Liest den Datensatz **ohne** die Bedingung, dass ein Token darin steht.
	 * Nach einem Abmelden bleibt genau ein solcher Datensatz stehen: er trägt
	 * nur noch die Gerätekennung.
	 */
	virtual Result<StoredCredential> LoadRaw (const std::string& serverUrl) = 0;
	virtual Status Save (const std::string& serverUrl, const StoredCredential& credential) = 0;
	/**
	 * Löscht das **Token**; der serverseitige Widerruf ist ein eigener Aufruf.
	 * Die Gerätekennung bleibt erhalten (siehe `LoadOrCreateDeviceId`).
	 */
	virtual Status Erase (const std::string& serverUrl) = 0;
};

/** macOS Keychain (`kSecClassGenericPassword`, Dienst `ai.rendertaxi.archicad`). */
std::unique_ptr<TokenStore> MakeKeychainTokenStore ();

/** Nur für Tests: hält das Token im Speicher des Prozesses. */
std::unique_ptr<TokenStore> MakeMemoryTokenStore ();

/**
 * Liest die Gerätekennung oder erzeugt sie **einmal**.
 *
 * Sie gehört zum **Gerät**, nicht zur Anmeldung, und bleibt über ein Abmelden
 * hinweg dieselbe: sonst bekäme die Liste „Verbundene Geräte" im Web bei jeder
 * Neuanmeldung einen zweiten Eintrag. Deshalb legt `Erase` sie **nicht**
 * mit ab, sondern behält einen Datensatz, der nur sie trägt.
 */
std::string LoadOrCreateDeviceId (TokenStore& tokens, const std::string& serverUrl);

/** Serialisiert einen Anmeldedatensatz; sichtbar für die Keychain und die Tests. */
std::string EncodeCredential (const StoredCredential& credential);
Result<StoredCredential> DecodeCredential (const std::string& encoded);
/** Wie `DecodeCredential`, aber ohne die Bedingung eines vorhandenen Tokens. */
Result<StoredCredential> DecodeRawCredential (const std::string& encoded);

} // namespace rtx
