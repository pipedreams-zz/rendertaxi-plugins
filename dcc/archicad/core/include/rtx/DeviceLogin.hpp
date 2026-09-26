// Gerätelogin nach RFC 8628, wie `plugin-api-v1.md`, Abschnitt 5 ihn festlegt.
//
// Zwei bewusste Abweichungen von der RFC, beide aus der Spezifikation: die
// Anfragekörper sind JSON statt `application/x-www-form-urlencoded`, und Fehler
// folgen dem Fehlerformat von `/api/v1`. **Die flache RFC-Fehlerform gibt es
// nicht** — der Grund steht in `details.reason` (§5, Entscheidung 6). Der erste
// Durchgang las sie und brach deshalb bei der ersten Abfrage ab (V-04).
//
// Die Warteschleife bekommt ihre Zeitquelle von außen, damit die Prüfungen den
// gesamten Fluss ohne echte Wartezeit durchlaufen.
#pragma once

#include <functional>
#include <string>

#include "rtx/PluginApi.hpp"
#include "rtx/TokenStore.hpp"

namespace rtx {

/** Zuschlag je `slow_down`, wenn der Server keinen neuen Abstand nennt (§5.2). */
inline constexpr int kSlowDownIncrementSeconds = 5;

struct DeviceLoginPrompt {
	std::string userCode;
	std::string verificationUri;
	std::string verificationUriComplete;
	int expiresInSeconds = 600;
};

/** Zeigt Code und Adresse; das Add-On öffnet dabei den Systembrowser. */
using DeviceLoginPrompter = std::function<void (const DeviceLoginPrompt&)>;
/** Wartet die übergebene Zahl Sekunden; in Prüfungen eine leere Funktion. */
using Sleeper = std::function<void (int seconds)>;

class DeviceLogin final {
public:
	DeviceLogin (PluginApiClient& api, TokenStore& tokens, std::string serverUrl);

	/**
	 * Vollständiger Gerätefluss bis zum gespeicherten Token.
	 *
	 * `device.deviceId` muss gesetzt sein — die Kennung entsteht **einmal** je
	 * Installation und liegt neben dem Token im Schlüsselspeicher (§5.5).
	 */
	Result<StoredCredential> SignIn (const DeviceIdentity& device, CancelToken* cancel,
									 const DeviceLoginPrompter& prompt, const Sleeper& sleep);

	/** Liest ein gespeichertes Token und setzt es im Client. */
	Result<StoredCredential> Restore ();

	/**
	 * Widerruft **serverseitig** und löscht danach lokal (§5.4).
	 * Der erste Durchgang sendete `{}` mit Bearer; der Server antwortete `400`,
	 * und das Token blieb gültig (V-07).
	 */
	Status SignOut (CancelToken* cancel);

private:
	PluginApiClient& api;
	TokenStore& tokens;
	std::string serverUrl;
};

} // namespace rtx
