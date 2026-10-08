#include "rtx/DeviceLogin.hpp"

#include <cstdlib>

#include "rtx/Ids.hpp"
#include "rtx/Log.hpp"
#include "rtx/Numbers.hpp"

namespace rtx {

DeviceLogin::DeviceLogin (PluginApiClient& apiClient, TokenStore& tokenStore, std::string url) :
	api (apiClient), tokens (tokenStore), serverUrl (std::move (url))
{
}

Result<StoredCredential> DeviceLogin::Restore ()
{
	Result<StoredCredential> stored = tokens.Load (serverUrl);
	if (!stored) return stored;
	api.SetAccessToken (stored.Value ().accessToken);
	return stored;
}

Result<StoredCredential> DeviceLogin::SignIn (const DeviceIdentity& device, CancelToken* cancel,
											  const DeviceLoginPrompter& prompt,
											  const Sleeper& sleep)
{
	if (device.deviceId.empty ())
		return Result<StoredCredential>::Fail (errc::IoFailed,
											   "Ohne Gerätekennung ist keine Anmeldung möglich.");

	api.SetAccessToken ({});
	const Result<DeviceAuthorization> authorization = api.StartDeviceLogin (device, cancel);
	if (!authorization) return Result<StoredCredential>::Fail (authorization.GetError ());

	// Weder `device_code` noch `user_code` erscheinen im Protokoll (§5.6).
	LogLine ("Gerätelogin begonnen, deviceId " + device.deviceId);

	if (prompt) {
		prompt (DeviceLoginPrompt {authorization.Value ().userCode,
								   authorization.Value ().verificationUri,
								   authorization.Value ().verificationUriComplete,
								   authorization.Value ().expiresInSeconds});
	}

	int interval = authorization.Value ().intervalSeconds;
	const std::uint64_t deadline =
		UnixMillis () + static_cast<std::uint64_t> (authorization.Value ().expiresInSeconds) * 1000;

	while (true) {
		if (cancel != nullptr && cancel->IsCancelled ())
			return Result<StoredCredential>::Fail (errc::Cancelled, "Anmeldung abgebrochen.");
		if (UnixMillis () > deadline)
			return Result<StoredCredential>::Fail (
				errc::ExpiredToken, "Der Anmeldecode ist abgelaufen. Bitte neu anmelden.");

		if (sleep) sleep (interval);

		const Result<DeviceToken> token = api.PollDeviceToken (authorization.Value ().deviceCode, cancel);
		if (token) {
			StoredCredential credential;
			credential.accessToken = token.Value ().accessToken;
			credential.deviceId = device.deviceId;
			credential.expiresAtMillis =
				token.Value ().expiresInSeconds > 0
					? UnixMillis () + static_cast<std::uint64_t> (token.Value ().expiresInSeconds) * 1000
					: 0;
			api.SetAccessToken (credential.accessToken);

			const Result<AccountInfo> account = api.Me (cancel);
			if (account) {
				credential.displayName = account.Value ().userDisplayName;
				credential.organizationId = account.Value ().organizationId;
				credential.organizationName = account.Value ().organizationName;
			} else if (account.GetError ().code == "membership_missing" ||
					   account.GetError ().code == "organization_selection_required") {
				// Der Gerätelogin gelingt in beiden Fällen; das Plugin erfährt
				// den Zustand über `GET /plugin/me` und zeigt ihn an (§5.3).
				credential.displayName = {};
				credential.membershipIssue = account.GetError ().code;
			}
			const Status saved = tokens.Save (serverUrl, credential);
			if (!saved) return Result<StoredCredential>::Fail (saved.GetError ());
			LogLine ("Gerätelogin abgeschlossen.");
			return Result<StoredCredential>::Ok (credential);
		}

		const std::string& code = token.GetError ().code;
		if (code == errc::AuthorizationPending) continue;
		if (code == errc::SlowDown) {
			// **Dauerhaft** um 5 s erhöhen; nennt der Server einen neuen
			// Abstand in `details.interval`, gilt dieser (§5.1).
			// Begrenzt gelesen (F-01 an #311): mehr als eine Stunde Abstand ist keine Angabe, sondern ein Fehler.
			int announced = 0;
			if (!ParseBoundedInt (token.GetError ().pointer, announced, 3600)) announced = 0;
			interval = announced > 0 ? announced : interval + kSlowDownIncrementSeconds;
			LogLine ("Gerätelogin: Abstand auf " + std::to_string (interval) + " s erhöht.");
			continue;
		}
		if (code == errc::Transport || code == errc::RateLimited) {
			// Netzwerkfehler, Zeitüberschreitung und Ratenbegrenzung:
			// Abstand verdoppeln und weiter abfragen (RFC 8628, Abschnitt 3.5).
			interval = interval > 0 ? interval * 2 : kSlowDownIncrementSeconds;
			if (interval > 60) interval = 60;
			LogLine ("Gerätelogin: Abstand nach Transportfehler auf " + std::to_string (interval) +
					 " s verdoppelt.");
			continue;
		}
		// `expired_token`, `access_denied`, `invalid_grant`, `cancelled`:
		// aufhören und anzeigen.
		return Result<StoredCredential>::Fail (token.GetError ());
	}
}

Status DeviceLogin::SignOut (CancelToken* cancel)
{
	// Erst serverseitig widerrufen, solange das Token noch da ist; dann lokal
	// löschen. Ein fehlgeschlagener Widerruf darf das lokale Löschen nicht
	// verhindern — sonst bliebe ein Token liegen, das der Nutzer loswerden
	// wollte.
	const Result<StoredCredential> stored = tokens.Load (serverUrl);
	const std::string token = stored ? stored.Value ().accessToken : std::string ();

	const Status revoked = api.RevokeToken (token, cancel);
	api.SetAccessToken ({});
	const Status erased = tokens.Erase (serverUrl);
	if (!erased) return erased;
	if (!revoked)
		return Status::Fail (revoked.GetError ().code,
							 "Das Token wurde lokal gelöscht, der serverseitige Widerruf ist "
							 "aber fehlgeschlagen: " +
								 revoked.GetError ().message);
	return Status::Ok ();
}

} // namespace rtx
