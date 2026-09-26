#include "rtx/PluginApi.hpp"

#include "rtx/UrlHost.hpp"

#include <cctype>
#include <cstdlib>

#include "rtx/Ids.hpp"
#include "rtx/Log.hpp"

namespace rtx {
namespace {

std::string TrimTrailingSlash (std::string url)
{
	while (!url.empty () && url.back () == '/') url.pop_back ();
	return url;
}

std::string TextField (const JsonPtr& node, const char* key)
{
	if (node == nullptr) return {};
	const JsonPtr field = node->Get (key);
	if (field == nullptr || field->GetKind () != Json::Kind::String) return {};
	return field->StringOr ("");
}

std::int64_t IntField (const JsonPtr& node, const char* key, std::int64_t fallback)
{
	if (node == nullptr) return fallback;
	const JsonPtr field = node->Get (key);
	return field != nullptr ? field->IntOr (fallback) : fallback;
}

/** Prozentkodiert einen Abfragewert; die Werte sind ASCII, aber nicht alle sicher. */
std::string QueryEscape (const std::string& value)
{
	static const char* hex = "0123456789ABCDEF";
	std::string out;
	for (const char raw : value) {
		const unsigned char c = static_cast<unsigned char> (raw);
		const bool safe = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
						  (c >= '0' && c <= '9') || c == '-' || c == '.' || c == '_' || c == '~';
		if (safe) {
			out += raw;
		} else {
			out += '%';
			out += hex[c >> 4];
			out += hex[c & 0x0F];
		}
	}
	return out;
}

JsonPtr DeviceToJson (const DeviceIdentity& device)
{
	JsonPtr node = Json::MakeObject ();
	node->Set ("deviceId", Json::MakeString (device.deviceId));
	if (!device.displayName.empty ())
		node->Set ("displayName", Json::MakeString (device.displayName));
	node->Set ("hostKey", Json::MakeString (device.hostKey));
	node->Set ("hostVersion", Json::MakeString (device.hostVersion));
	node->Set ("pluginVersion", Json::MakeString (device.pluginVersion));
	node->Set ("os", Json::MakeString (device.os));
	node->Set ("architecture", Json::MakeString (device.architecture));
	return node;
}

JsonPtr TargetToJson (const CaptureTarget& target)
{
	JsonPtr node = Json::MakeObject ();
	node->Set ("projectId", Json::MakeString (target.projectId));
	if (!target.viewpoint.present) return node;

	JsonPtr viewpoint = Json::MakeObject ();
	viewpoint->Set ("mode", Json::MakeString (target.viewpoint.mode));
	if (target.viewpoint.mode == "create") {
		viewpoint->Set ("name", Json::MakeString (target.viewpoint.name));
	} else {
		viewpoint->Set ("viewpointId", Json::MakeString (target.viewpoint.viewpointId));
	}
	// `baseImageRole` steht **innerhalb** von `target.viewpoint` (§7.2).
	if (!target.viewpoint.baseImageRole.empty ())
		viewpoint->Set ("baseImageRole", Json::MakeString (target.viewpoint.baseImageRole));
	// `frame` gehört zum Vorgangsschlüssel (§7.2). Ein Update sendet ihn
	// deshalb **immer** ausdrücklich, auch den Vorgabewert `keep`; eine
	// Anlage sendet ihn nie — dort ist `fit-to-capture` implizit und `keep`
	// ein `400`.
	if (target.viewpoint.SendsFrame ())
		viewpoint->Set ("frame", Json::MakeString (target.viewpoint.frame));
	// `size` gehört wie `frame` zum Vorgangsschlüssel und reist nur mit, wo es
	// wirkt (§7.2). Die Vorgabe `canvas-default` bleibt ungesagt.
	if (target.viewpoint.SendsSize ())
		viewpoint->Set ("size", Json::MakeString (target.viewpoint.size));
	node->Set ("viewpoint", viewpoint);
	return node;
}

CaptureLimits ParseLimits (const JsonPtr& node)
{
	CaptureLimits limits;
	if (node == nullptr) return limits;
	limits.maxAssetBytes = IntField (node, "maxAssetBytes", 0);
	limits.maxTotalBytes = IntField (node, "maxTotalBytes", 0);
	limits.maxManifestBytes = IntField (node, "maxManifestBytes", 0);
	limits.maxAssetCount = static_cast<int> (IntField (node, "maxAssetCount", 0));
	limits.captureTtlSeconds = static_cast<int> (IntField (node, "captureTtlSeconds", 0));
	if (const JsonPtr media = node->Get ("allowedMediaTypes")) {
		for (const JsonPtr& item : media->Items ()) limits.allowedMediaTypes.push_back (item->StringOr (""));
	}
	return limits;
}

CaptureFileState ParseFile (const JsonPtr& item)
{
	CaptureFileState file;
	file.role = TextField (item, "role");
	file.path = TextField (item, "path");
	file.mediaType = TextField (item, "mediaType");
	file.byteSize = IntField (item, "byteSize", 0);
	file.sha256 = TextField (item, "sha256");
	file.assetId = TextField (item, "assetId");
	file.attempt = static_cast<int> (IntField (item, "attempt", 0));
	file.state = TextField (item, "state");
	if (const JsonPtr error = item->Get ("error")) {
		if (!error->IsNull ()) {
			file.errorCode = TextField (error, "code");
			file.errorMessage = TextField (error, "message");
		}
	}
	return file;
}

UploadTicket ParseTicket (const JsonPtr& node)
{
	UploadTicket ticket;
	// `ticket: null` heißt „keine Übertragung nötig" — kein Fehler (V-14).
	if (node == nullptr || node->IsNull ()) return ticket;
	ticket.url = TextField (node, "uploadUrl");
	const std::string method = TextField (node, "method");
	if (!method.empty ()) ticket.method = method;
	ticket.expiresAt = TextField (node, "expiresAt");
	if (const JsonPtr headers = node->Get ("requiredHeaders")) {
		for (const auto& field : headers->Fields ())
			ticket.requiredHeaders.emplace_back (field.first, field.second->StringOr (""));
	}
	ticket.present = !ticket.url.empty ();
	return ticket;
}

} // namespace

double DesiredOutput::Ratio () const
{
	if (exactWidth > 0 && exactHeight > 0)
		return static_cast<double> (exactWidth) / static_cast<double> (exactHeight);
	if (aspectWidth > 0 && aspectHeight > 0)
		return static_cast<double> (aspectWidth) / static_cast<double> (aspectHeight);
	return 0.0;
}

Status HandshakeInfo::RequireCaptureContract () const
{
	// **Der Server bewertet.** Der erste Durchgang rechnete selbst und meldete
	// deshalb immer `unsupported_contract_major` (V-01). `negotiation.result`
	// ist genau diese Antwort — und sie ist eine Zeichenkette, keine Zahl: hier
	// wird nichts geparst, was werfen könnte (F-02).
	if (!negotiationPresent) {
		return Status::Fail (errc::Transport,
							 "Der Server hat die Vertragsfassung nicht bewertet. Frage den "
							 "Handshake mit contract und contractVersion ab.");
	}
	if (negotiationResult == "supported") return Status::Ok ();
	if (negotiationResult == "unsupported_contract_minor") {
		return Status::Fail (errc::UnsupportedContractMinor,
							 "Dieser Server setzt das Capture-Manifest nur bis " +
								 (highestSupportedVersion.empty () ? std::string ("einer älteren Fassung")
																   : highestSupportedVersion) +
								 " um. Das Add-on braucht eine neuere Serverfassung.");
	}
	if (negotiationResult == "unsupported_contract_major") {
		return Status::Fail (errc::UnsupportedContractMajor,
							 "Dieser Server kennt die Hauptfassung des Capture-Manifests nicht, "
							 "die dieses Add-on erzeugt.");
	}
	if (negotiationResult == "unsupported_contract") {
		return Status::Fail (errc::UnsupportedContractMajor,
							 "Dieser Server nimmt das Capture-Manifest nicht an.");
	}
	return Status::Fail (errc::Transport,
						 "Unbekanntes Ergebnis der Versionsaushandlung: " + negotiationResult);
}

const CaptureFileState* CaptureSessionState::FindByPath (const std::string& path) const
{
	for (const CaptureFileState& file : files) {
		if (file.path == path) return &file;
	}
	return nullptr;
}

bool CaptureSessionState::IsClosed () const
{
	return state == "verified" || state == "rejected" || state == "aborted" || state == "expired";
}

DesiredOutput ParseDesiredOutput (const JsonPtr& node)
{
	DesiredOutput desired;
	if (node == nullptr || node->GetKind () != Json::Kind::Object) return desired;
	const std::string kind = TextField (node, "kind");
	if (kind == "exact") {
		desired.exactWidth = static_cast<int> (IntField (node, "width", 0));
		desired.exactHeight = static_cast<int> (IntField (node, "height", 0));
		if (desired.exactWidth <= 0 || desired.exactHeight <= 0) return desired;
		desired.known = true;
		int a = desired.exactWidth;
		int b = desired.exactHeight;
		while (b != 0) { const int rest = a % b; a = b; b = rest; }
		desired.aspectWidth = a > 1 ? desired.exactWidth / a : desired.exactWidth;
		desired.aspectHeight = a > 1 ? desired.exactHeight / a : desired.exactHeight;
		desired.label = std::to_string (desired.exactWidth) + " x " +
						std::to_string (desired.exactHeight);
		return desired;
	}
	if (kind != "aspect_ratio") return desired;
	const std::string value = TextField (node, "value");
	const std::size_t colon = value.find (':');
	if (colon == std::string::npos || colon == 0 || colon + 1 >= value.size ()) return desired;
	desired.aspectWidth = std::atoi (value.substr (0, colon).c_str ());
	desired.aspectHeight = std::atoi (value.substr (colon + 1).c_str ());
	if (desired.aspectWidth <= 0 || desired.aspectHeight <= 0) {
		desired.aspectWidth = 0;
		desired.aspectHeight = 0;
		return desired;
	}
	desired.known = true;
	desired.label = value;
	return desired;
}

Result<CaptureSessionState> ParseCaptureSession (const JsonPtr& node)
{
	if (node == nullptr || node->GetKind () != Json::Kind::Object)
		return Result<CaptureSessionState>::Fail (errc::Transport,
												  "Antwort der Capture-Session ist kein Objekt.");
	CaptureSessionState session;
	session.captureId = TextField (node, "captureId");
	session.state = TextField (node, "state");
	session.expiresAt = TextField (node, "expiresAt");
	session.limits = ParseLimits (node->Get ("limits"));

	if (const JsonPtr manifest = node->Get ("manifest"))
		session.manifestState = TextField (manifest, "state");
	if (const JsonPtr validation = node->Get ("validation")) {
		session.validationState = TextField (validation, "state");
		if (const JsonPtr errors = validation->Get ("errors")) {
			for (const JsonPtr& item : errors->Items ()) {
				session.validationErrors.emplace_back (TextField (item, "code"),
													   TextField (item, "message"),
													   TextField (item, "pointer"));
			}
		}
	}
	if (const JsonPtr files = node->Get ("files")) {
		for (const JsonPtr& item : files->Items ()) session.files.push_back (ParseFile (item));
	}
	if (const JsonPtr result = node->Get ("result")) {
		if (!result->IsNull ()) {
			session.result.kind = TextField (result, "kind");
			session.result.viewpointId = TextField (result, "viewpointId");
			session.result.openUrl = TextField (result, "openUrl");
			if (const JsonPtr assetIds = result->Get ("assetIds")) {
				for (const auto& field : assetIds->Fields ())
					session.result.assetIdsByRole.emplace_back (field.first,
																field.second->StringOr (""));
			}
		}
	}
	if (session.captureId.empty ())
		return Result<CaptureSessionState>::Fail (errc::Transport, "Antwort ohne captureId.");
	return Result<CaptureSessionState>::Ok (session);
}

JsonPtr CreateCaptureRequestToJson (const CreateCaptureRequest& request)
{
	JsonPtr node = Json::MakeObject ();
	node->Set ("contract", Json::MakeString (request.contract));
	node->Set ("contractVersion", Json::MakeString (request.contractVersion));
	node->Set ("manifestSha256", Json::MakeString (request.manifestSha256));

	JsonPtr files = Json::MakeArray ();
	for (const CaptureAsset& asset : request.files) {
		JsonPtr file = Json::MakeObject ();
		file->Set ("role", Json::MakeString (asset.role));
		file->Set ("path", Json::MakeString (asset.path));
		file->Set ("mediaType", Json::MakeString (asset.mediaType));
		file->Set ("byteSize", Json::MakeInt (asset.byteSize));
		file->Set ("sha256", Json::MakeString (asset.sha256));
		files->Append (file);
	}
	node->Set ("files", files);
	node->Set ("target", TargetToJson (request.target));
	// `georeference` fehlt im Bildweg: der Handshake nennt ihn `unsupported`,
	// und ein gesendeter Block antwortete `georeference_mismatch` (§7.9).
	return node;
}

PluginApiClient::PluginApiClient (HttpClient& httpClient, std::string url) :
	http (httpClient), baseUrl (TrimTrailingSlash (std::move (url)))
{
}

void PluginApiClient::SetAccessToken (std::string token)
{
	accessToken = std::move (token);
}

bool PluginApiClient::IsTokenSafeBaseUrl (const std::string& url)
{
	// **Die Adresse wird nicht mehr selbst zerlegt.**
	//
	// Der erste Anlauf schnitt den Host vor dem ersten `:` ab und hielt
	// `http://localhost:80@evil.example/` für die Schleife — dort ist
	// `localhost:80` aber die Benutzerangabe und `evil.example` der Host. Das
	// Token wäre im Klartext an einen fremden Rechner gegangen (F-01, dritte
	// Nachprüfung). Was die Autorität ist, beantwortet jetzt der Parser des
	// Systems (`rtx::ParseUrl`).
	const UrlParts parts = ParseUrl (url);
	if (!parts.valid) return false;

	// **Eine Benutzerangabe gehört nicht in eine Serveradresse.** Sie ist in
	// jeder Form abgelehnt — auch über `https://`, wo sie „nur" ein Geheimnis
	// in der Adresszeile wäre, und unabhängig davon, wie harmlos der Host
	// danach aussieht.
	if (parts.hasUserInfo) return false;

	if (parts.scheme == "https") return true;
	if (parts.scheme != "http") return false;

	// Einzige Ausnahme: die Schleife. Sie verlässt den Rechner nicht, und der
	// Scheinserver aus `core/tests` lebt dort. Der Host kommt ohne Port und
	// ohne Klammern aus dem Systemparser; verglichen wird auf Gleichheit, nie
	// auf einen Anfang oder ein Ende.
	return parts.host == "127.0.0.1" || parts.host == "localhost" || parts.host == "::1";
}

Error PluginApiClient::ErrorFromResponse (const HttpResponse& response) const
{
	// Ein Körper im einheitlichen Fehlerformat von `/api/v1` (§3):
	// `{ error: { code, message, requestId, details?, fieldErrors? } }`.
	const JsonPtr node = Json::Parse (response.body);
	std::string code;
	std::string message;
	std::string reason;
	std::string fieldSummary;

	if (node != nullptr && node->GetKind () == Json::Kind::Object) {
		if (const JsonPtr error = node->Get ("error")) {
			if (error->GetKind () == Json::Kind::Object) {
				code = TextField (error, "code");
				message = TextField (error, "message");
				if (const JsonPtr details = error->Get ("details"))
					reason = TextField (details, "reason");
				if (const JsonPtr fields = error->Get ("fieldErrors")) {
					for (const JsonPtr& item : fields->Items ()) {
						if (!fieldSummary.empty ()) fieldSummary += "; ";
						fieldSummary += TextField (item, "path") + ": " + TextField (item, "code");
					}
				}
			}
		}
	}

	if (response.status == 429) {
		// `Retry-After` in Sekunden an den vier unangemeldeten Operationen (§3).
		const std::string retryAfter = response.Header ("Retry-After");
		std::string text = message.empty () ? "Zu viele Anfragen." : message;
		if (!retryAfter.empty ()) text += " Nächster Versuch in " + retryAfter + " s.";
		Error error (errc::RateLimited, text);
		error.pointer = retryAfter;
		return error;
	}

	// **Der Grund steht in `details.reason`**, nicht im Code: der Katalog der
	// Fehlercodes wächst dafür nicht (§3). Für den Client ist der Grund die
	// unterscheidende Kennung.
	if (!reason.empty ()) {
		Error error (reason, message.empty () ? reason : message);
		return error;
	}

	if (code.empty ()) {
		if (response.status == 401) code = errc::Unauthorized;
		else if (response.status == 403) code = errc::Forbidden;
		else if (response.status == 409) code = errc::IdempotencyConflict;
		else code = errc::Transport;
	}
	if (message.empty ())
		message = "Der Server antwortete mit HTTP " + std::to_string (response.status) + ".";
	if (!fieldSummary.empty ()) message += " (" + fieldSummary + ")";
	return Error (code, message);
}

Result<HttpResponse> PluginApiClient::Call (const std::string& method, const std::string& path,
											const std::string& body, const HttpHeaders& extra,
											CancelToken* cancel, TokenInBody tokenInBody)
{
	// **Die Transportregel steht vor dem Aufruf, nicht vor der Kopfzeile.**
	//
	// Der erste Anlauf prüfte nur, ob ein Bearer gesetzt würde. `auth/revoke`
	// trägt das Token aber im **Körper** und leert `accessToken` vorher — der
	// Widerruf ging damit im Klartext an jeden externen Host. Maßgeblich ist
	// deshalb, ob dieser Aufruf ein Token **irgendwo** trägt.
	const bool carriesToken = !accessToken.empty () || tokenInBody == TokenInBody::Yes;
	if (carriesToken && !IsTokenSafeBaseUrl (baseUrl)) {
		// Vor `HttpClient::Send`: es verlässt kein Byte den Prozess.
		return Result<HttpResponse>::Fail (
			errc::InsecureTransport,
			"Über eine unverschlüsselte Verbindung wird kein Anmeldetoken gesendet — "
			"weder als Kopfzeile noch im Körper. Verwende https:// (Ausnahme: 127.0.0.1 "
			"für den Scheinserver).");
	}

	HttpRequest request;
	request.method = method;
	request.url = baseUrl + path;
	request.body = body;
	if (!body.empty ()) request.headers.emplace_back ("Content-Type", "application/json");
	request.headers.emplace_back ("Accept", "application/json");

	if (!accessToken.empty ()) request.headers.emplace_back ("Authorization", "Bearer " + accessToken);
	for (const auto& header : extra) request.headers.push_back (header);

	LogLine ("HTTP " + method + " " + SafeUrl (request.url));
	Result<HttpResponse> response = http.Send (request, cancel, {});
	if (response) {
		// Jede Antwort trägt `X-Request-Id` (§3). Sie ist der Schlüssel jeder
		// Supportanfrage und gehört deshalb ins Protokoll — sie ist kein
		// Geheimnis.
		lastRequestId = response.Value ().Header ("X-Request-Id");
		if (!lastRequestId.empty ())
			LogLine ("  requestId " + lastRequestId + " -> " +
					 std::to_string (response.Value ().status));
	}
	return response;
}

Result<HandshakeInfo> PluginApiClient::Handshake (const DeviceIdentity& device,
												  CancelToken* cancel)
{
	// Alle Parameter sind optional, aber **ohne `contract` und
	// `contractVersion` gibt es keine `negotiation`** — und ohne die kann der
	// Client die Fassung nicht prüfen (§4).
	std::string path = "/api/v1/plugin/handshake";
	path += "?contract=" + QueryEscape (kCaptureContract);
	path += "&contractVersion=" + QueryEscape (kCaptureContractVersion);
	path += "&hostKey=" + QueryEscape (device.hostKey);
	path += "&hostVersion=" + QueryEscape (device.hostVersion);
	path += "&pluginVersion=" + QueryEscape (device.pluginVersion);

	const Result<HttpResponse> response = Call ("GET", path, {}, {}, cancel);
	if (!response) return Result<HandshakeInfo>::Fail (response.GetError ());
	if (response.Value ().status == 404) {
		// „Dieser Server bietet die Plugin API v1 nicht an." Ein Körper wird
		// dabei **nicht** ausgewertet (§4).
		return Result<HandshakeInfo>::Fail (errc::EndpointMissing,
											"Dieser Server bietet die Plugin API v1 nicht an.");
	}
	if (response.Value ().status != 200)
		return Result<HandshakeInfo>::Fail (ErrorFromResponse (response.Value ()));

	const JsonPtr node = Json::Parse (response.Value ().body);
	if (node == nullptr)
		return Result<HandshakeInfo>::Fail (errc::Transport, "Handshake-Antwort ist kein JSON.");

	HandshakeInfo info;
	info.apiVersion = TextField (node, "apiVersion");
	info.profile = TextField (node, "profile");
	info.profileVersion = TextField (node, "profileVersion");
	info.serverTime = TextField (node, "serverTime");
	info.limits = ParseLimits (node->Get ("limits"));

	if (const JsonPtr contracts = node->Get ("contracts")) {
		for (const JsonPtr& item : contracts->Items ()) {
			ContractSupport support;
			support.contract = TextField (item, "contract");
			support.availability = TextField (item, "availability");
			support.finalization = TextField (item, "finalization");
			support.georeference = TextField (item, "georeference");
			if (const JsonPtr versions = item->Get ("versions")) {
				for (const JsonPtr& version : versions->Items ()) {
					support.versions.emplace_back (static_cast<int> (IntField (version, "major", 0)),
												   static_cast<int> (IntField (version, "maxMinor", 0)));
				}
			}
			info.contracts.push_back (support);
		}
	}
	if (const JsonPtr negotiation = node->Get ("negotiation")) {
		if (!negotiation->IsNull ()) {
			info.negotiationPresent = true;
			info.negotiationContract = TextField (negotiation, "contract");
			info.negotiationContractVersion = TextField (negotiation, "contractVersion");
			info.negotiationResult = TextField (negotiation, "result");
			info.highestSupportedVersion = TextField (negotiation, "highestSupportedVersion");
		}
	}
	if (const JsonPtr update = node->Get ("update")) {
		info.updateStatus = TextField (update, "status");
		info.updateMessage = TextField (update, "message");
		info.updateUrl = TextField (update, "url");
	}
	return Result<HandshakeInfo>::Ok (info);
}

Result<DeviceAuthorization> PluginApiClient::StartDeviceLogin (const DeviceIdentity& device,
															   CancelToken* cancel)
{
	JsonPtr request = Json::MakeObject ();
	request->Set ("client_id", Json::MakeString (kPluginClientId));
	request->Set ("device", DeviceToJson (device));

	const Result<HttpResponse> response =
		Call ("POST", "/api/v1/plugin/auth/device", request->Serialize (), {}, cancel);
	if (!response) return Result<DeviceAuthorization>::Fail (response.GetError ());
	if (response.Value ().status != 200)
		return Result<DeviceAuthorization>::Fail (ErrorFromResponse (response.Value ()));

	const JsonPtr node = Json::Parse (response.Value ().body);
	if (node == nullptr)
		return Result<DeviceAuthorization>::Fail (errc::Transport,
												  "Antwort des Geräteflusses ist kein JSON.");
	DeviceAuthorization authorization;
	authorization.deviceCode = TextField (node, "device_code");
	authorization.userCode = TextField (node, "user_code");
	authorization.verificationUri = TextField (node, "verification_uri");
	authorization.verificationUriComplete = TextField (node, "verification_uri_complete");
	authorization.expiresInSeconds = static_cast<int> (IntField (node, "expires_in", 600));
	authorization.intervalSeconds = static_cast<int> (IntField (node, "interval", 5));
	if (authorization.intervalSeconds < 1) authorization.intervalSeconds = 5;
	if (authorization.deviceCode.empty () || authorization.userCode.empty () ||
		authorization.verificationUri.empty ())
		return Result<DeviceAuthorization>::Fail (
			errc::Transport, "Der Gerätefluss lieferte keinen vollständigen Satz aus Gerätecode, "
							 "Benutzercode und Adresse.");
	return Result<DeviceAuthorization>::Ok (authorization);
}

Result<DeviceToken> PluginApiClient::PollDeviceToken (const std::string& deviceCode,
													  CancelToken* cancel)
{
	JsonPtr request = Json::MakeObject ();
	request->Set ("grant_type", Json::MakeString ("urn:ietf:params:oauth:grant-type:device_code"));
	request->Set ("device_code", Json::MakeString (deviceCode));
	request->Set ("client_id", Json::MakeString (kPluginClientId));

	const Result<HttpResponse> response =
		Call ("POST", "/api/v1/plugin/auth/device/token", request->Serialize (), {}, cancel);
	if (!response) return Result<DeviceToken>::Fail (response.GetError ());
	if (response.Value ().status != 200) {
		Error error = ErrorFromResponse (response.Value ());
		// `slow_down` nennt den **neuen** Abstand in `details.interval` (§5.1).
		if (error.code == errc::SlowDown) {
			const JsonPtr node = Json::Parse (response.Value ().body);
			const JsonPtr details =
				node != nullptr && node->Get ("error") ? node->Get ("error")->Get ("details") : nullptr;
			const std::int64_t interval = IntField (details, "interval", 0);
			if (interval > 0) error.pointer = std::to_string (interval);
		}
		return Result<DeviceToken>::Fail (error);
	}

	const JsonPtr node = Json::Parse (response.Value ().body);
	if (node == nullptr)
		return Result<DeviceToken>::Fail (errc::Transport, "Tokenantwort ist kein JSON.");
	DeviceToken token;
	token.accessToken = TextField (node, "access_token");
	token.expiresInSeconds = static_cast<int> (IntField (node, "expires_in", 0));
	token.scope = TextField (node, "scope");
	if (token.accessToken.empty ())
		return Result<DeviceToken>::Fail (errc::Transport, "Tokenantwort ohne Zugriffstoken.");
	return Result<DeviceToken>::Ok (token);
}

Status PluginApiClient::RevokeToken (const std::string& token, CancelToken* cancel)
{
	if (token.empty ()) return Status::Ok ();
	// RFC 7009: das zu widerrufende Token steht **im Körper** (§5.4). Der
	// Aufruf ist unangemeldet; ein Bearer-Kopf gehört nicht dazu.
	JsonPtr request = Json::MakeObject ();
	request->Set ("token", Json::MakeString (token));

	const std::string saved = accessToken;
	accessToken.clear ();
	const Result<HttpResponse> response =
		Call ("POST", "/api/v1/plugin/auth/revoke", request->Serialize (), {}, cancel,
			  TokenInBody::Yes);
	accessToken = saved;

	if (!response) return Status::Fail (response.GetError ());
	// Bekannt, unbekannt, bereits widerrufen — der Aufruf antwortet gleich.
	if (response.Value ().status == 200) return Status::Ok ();
	return Status::Fail (ErrorFromResponse (response.Value ()));
}

Result<AccountInfo> PluginApiClient::Me (CancelToken* cancel)
{
	const Result<HttpResponse> response = Call ("GET", "/api/v1/plugin/me", {}, {}, cancel);
	if (!response) return Result<AccountInfo>::Fail (response.GetError ());
	if (response.Value ().status != 200)
		return Result<AccountInfo>::Fail (ErrorFromResponse (response.Value ()));
	const JsonPtr node = Json::Parse (response.Value ().body);
	if (node == nullptr)
		return Result<AccountInfo>::Fail (errc::Transport, "Kontoantwort ist kein JSON.");

	AccountInfo info;
	info.userDisplayName = TextField (node->Get ("user"), "displayName");
	info.organizationId = TextField (node->Get ("organization"), "id");
	info.organizationName = TextField (node->Get ("organization"), "name");
	info.role = TextField (node, "role");
	info.tokenExpiresAt = TextField (node->Get ("token"), "expiresAt");
	info.tokenRenewableUntil = TextField (node->Get ("token"), "renewableUntil");
	return Result<AccountInfo>::Ok (info);
}

Result<std::vector<ProjectSummary>> PluginApiClient::ListProjects (CancelToken* cancel)
{
	std::vector<ProjectSummary> projects;
	std::string path = "/api/v1/projects?limit=100";
	for (int page = 0; page < 20; ++page) {
		const Result<HttpResponse> response = Call ("GET", path, {}, {}, cancel);
		if (!response) return Result<std::vector<ProjectSummary>>::Fail (response.GetError ());
		if (response.Value ().status != 200)
			return Result<std::vector<ProjectSummary>>::Fail (ErrorFromResponse (response.Value ()));
		const JsonPtr node = Json::Parse (response.Value ().body);
		if (node == nullptr)
			return Result<std::vector<ProjectSummary>>::Fail (errc::Transport,
															  "Projektliste ist kein JSON.");
		const JsonPtr items = node->Get ("items");
		if (items == nullptr) break;
		for (const JsonPtr& item : items->Items ()) {
			ProjectSummary summary;
			summary.id = TextField (item, "id");
			summary.name = TextField (item, "name");
			summary.viewpointCount = static_cast<int> (IntField (item, "viewpointCount", 0));
			if (!summary.id.empty ()) projects.push_back (summary);
		}
		const std::string cursor = TextField (node, "nextCursor");
		if (cursor.empty ()) break;
		path = "/api/v1/projects?limit=100&cursor=" + QueryEscape (cursor);
	}
	return Result<std::vector<ProjectSummary>>::Ok (projects);
}

Result<std::vector<ViewpointSummary>> PluginApiClient::ListViewpoints (const std::string& projectId,
																	   CancelToken* cancel)
{
	// Zwei vorhandene Lesewege, über die Blickpunkt-ID zusammengeführt — der in
	// §6 ausdrücklich vorgesehene Weg. Eine kompaktere Leseform entsteht nicht
	// (ADR 0024).
	const Result<HttpResponse> response =
		Call ("GET", "/api/v1/projects/" + projectId + "/workspace", {}, {}, cancel);
	if (!response) return Result<std::vector<ViewpointSummary>>::Fail (response.GetError ());
	if (response.Value ().status != 200)
		return Result<std::vector<ViewpointSummary>>::Fail (ErrorFromResponse (response.Value ()));
	const JsonPtr node = Json::Parse (response.Value ().body);
	if (node == nullptr)
		return Result<std::vector<ViewpointSummary>>::Fail (errc::Transport,
														   "Arbeitsfläche ist kein JSON.");
	std::vector<ViewpointSummary> viewpoints;
	if (const JsonPtr items = node->Get ("viewpoints")) {
		for (const JsonPtr& item : items->Items ()) {
			ViewpointSummary summary;
			summary.id = TextField (item, "id");
			summary.name = TextField (item, "name");
			if (!summary.id.empty ()) viewpoints.push_back (summary);
		}
	}

	// Schlägt der zweite Aufruf fehl, bleibt die Auswahl möglich; das Plugin
	// erfindet dann kein Ausgabeziel (§6).
	const Result<HttpResponse> overview =
		Call ("GET", "/api/v1/projects/" + projectId + "/viewpoint-overview", {}, {}, cancel);
	if (overview && overview.Value ().status == 200) {
		const JsonPtr overviewNode = Json::Parse (overview.Value ().body);
		const JsonPtr overviewItems =
			overviewNode != nullptr ? overviewNode->Get ("viewpoints") : nullptr;
		if (overviewItems != nullptr) {
			for (const JsonPtr& item : overviewItems->Items ()) {
				const std::string id = TextField (item, "viewpointId");
				const JsonPtr desired = item->Get ("desired");
				if (id.empty () || desired == nullptr || desired->IsNull ()) continue;
				for (ViewpointSummary& summary : viewpoints) {
					if (summary.id != id) continue;
					summary.desired = ParseDesiredOutput (desired);
					break;
				}
			}
		}
	}
	return Result<std::vector<ViewpointSummary>>::Ok (viewpoints);
}

Result<CaptureSessionState> PluginApiClient::CreateCapture (const std::string& idempotencyKey,
															const CreateCaptureRequest& request,
															CancelToken* cancel)
{
	const HttpHeaders extra {{"Idempotency-Key", idempotencyKey}};
	const Result<HttpResponse> response =
		Call ("POST", "/api/v1/plugin/captures", CreateCaptureRequestToJson (request)->Serialize (),
			  extra, cancel);
	if (!response) return Result<CaptureSessionState>::Fail (response.GetError ());
	const int status = response.Value ().status;
	if (status != 200 && status != 201)
		return Result<CaptureSessionState>::Fail (ErrorFromResponse (response.Value ()));
	return ParseCaptureSession (Json::Parse (response.Value ().body));
}

Result<CaptureSessionState> PluginApiClient::GetCapture (const std::string& captureId,
														 CancelToken* cancel)
{
	const Result<HttpResponse> response =
		Call ("GET", "/api/v1/plugin/captures/" + captureId, {}, {}, cancel);
	if (!response) return Result<CaptureSessionState>::Fail (response.GetError ());
	if (response.Value ().status != 200)
		return Result<CaptureSessionState>::Fail (ErrorFromResponse (response.Value ()));
	return ParseCaptureSession (Json::Parse (response.Value ().body));
}

Result<CaptureFileResponse> PluginApiClient::CallFiles (const std::string& captureId,
														const std::string& idempotencyKey,
														const std::string& action,
														const std::string& path, int attempt,
														CancelToken* cancel)
{
	JsonPtr request = Json::MakeObject ();
	request->Set ("action", Json::MakeString (action));
	request->Set ("path", Json::MakeString (path));
	request->Set ("attempt", Json::MakeInt (attempt));

	const HttpHeaders extra {{"Idempotency-Key", idempotencyKey}};
	const Result<HttpResponse> response = Call (
		"POST", "/api/v1/plugin/captures/" + captureId + "/files", request->Serialize (), extra,
		cancel);
	if (!response) return Result<CaptureFileResponse>::Fail (response.GetError ());
	if (response.Value ().status != 200)
		return Result<CaptureFileResponse>::Fail (ErrorFromResponse (response.Value ()));

	const JsonPtr node = Json::Parse (response.Value ().body);
	if (node == nullptr)
		return Result<CaptureFileResponse>::Fail (errc::Transport, "Dateiantwort ist kein JSON.");
	CaptureFileResponse result;
	result.sessionState = TextField (node, "state");
	if (const JsonPtr file = node->Get ("file")) result.file = ParseFile (file);
	result.ticket = ParseTicket (node->Get ("ticket"));
	if (result.file.path.empty ())
		return Result<CaptureFileResponse>::Fail (errc::Transport, "Dateiantwort ohne Datei.");
	return Result<CaptureFileResponse>::Ok (result);
}

Result<CaptureFileResponse> PluginApiClient::BeginFile (const std::string& captureId,
														const std::string& idempotencyKey,
														const std::string& path, int attempt,
														CancelToken* cancel)
{
	return CallFiles (captureId, idempotencyKey, "begin", path, attempt, cancel);
}

Result<CaptureFileResponse> PluginApiClient::CompleteFile (const std::string& captureId,
														   const std::string& idempotencyKey,
														   const std::string& path, int attempt,
														   CancelToken* cancel)
{
	return CallFiles (captureId, idempotencyKey, "complete", path, attempt, cancel);
}

Status PluginApiClient::PutFile (const UploadTicket& ticket, const CaptureAsset& asset,
								 CancelToken* cancel, const HttpProgress& progress)
{
	HttpRequest request;
	request.method = ticket.method;
	request.url = ticket.url;
	request.bodyFilePath = asset.localPath;
	request.timeoutSeconds = 600;
	// **Alle** Kopfzeilen aus `requiredHeaders`, unverändert — und keine
	// eigenen daneben. Der `PUT` geht direkt an den Objektspeicher, **ohne**
	// `Authorization` (§7.4).
	request.headers = ticket.requiredHeaders;

	LogLine ("PUT einer Capture-Datei: " + asset.path);
	const Result<HttpResponse> response = http.Send (request, cancel, progress);
	if (!response) return Status::Fail (response.GetError ());
	const int status = response.Value ().status;
	if (status >= 200 && status < 300) return Status::Ok ();
	if (status == 412) {
		// „Unter diesem Versuch liegt bereits ein Objekt." Weder Fehler noch
		// `verified`: `complete` desselben `attempt` entscheidet (§7.4).
		LogLine ("PUT meldete 412; complete desselben Versuchs entscheidet.");
		return Status::Ok ();
	}
	return Status::Fail (Error (errc::Transport,
								"Die Datei wurde nicht angenommen (HTTP " +
									std::to_string (status) + ")."));
}

Result<CaptureSessionState> PluginApiClient::SubmitManifest (const std::string& captureId,
															 const std::string& idempotencyKey,
															 const std::string& manifestText,
															 CancelToken* cancel)
{
	// Das Manifest geht als **Rohbytes**; `manifestSha256` läuft über genau
	// diese Bytes (§7.7, A-04 bestätigt).
	const HttpHeaders extra {{"Idempotency-Key", idempotencyKey}};
	const Result<HttpResponse> response = Call (
		"POST", "/api/v1/plugin/captures/" + captureId + "/manifest", manifestText, extra, cancel);
	if (!response) return Result<CaptureSessionState>::Fail (response.GetError ());
	if (response.Value ().status != 200)
		return Result<CaptureSessionState>::Fail (ErrorFromResponse (response.Value ()));
	return ParseCaptureSession (Json::Parse (response.Value ().body));
}

Result<CaptureSessionState> PluginApiClient::Finalize (const std::string& captureId,
													   const std::string& idempotencyKey,
													   CancelToken* cancel)
{
	// Leerer Körper: das Ziel steht seit der Anlage fest (§7.3).
	const HttpHeaders extra {{"Idempotency-Key", idempotencyKey}};
	const Result<HttpResponse> response =
		Call ("POST", "/api/v1/plugin/captures/" + captureId + "/finalize", "{}", extra, cancel);
	if (!response) return Result<CaptureSessionState>::Fail (response.GetError ());
	if (response.Value ().status != 200)
		return Result<CaptureSessionState>::Fail (ErrorFromResponse (response.Value ()));
	return ParseCaptureSession (Json::Parse (response.Value ().body));
}

Status PluginApiClient::Abort (const std::string& captureId, const std::string& idempotencyKey,
							   CancelToken* cancel)
{
	const HttpHeaders extra {{"Idempotency-Key", idempotencyKey}};
	const Result<HttpResponse> response =
		Call ("POST", "/api/v1/plugin/captures/" + captureId + "/abort", "{}", extra, cancel);
	if (!response) return Status::Fail (response.GetError ());
	const int status = response.Value ().status;
	// `404` beim Verwerfen: die Session gibt es nicht mehr — das Ziel ist
	// erreicht.
	if (status == 200 || status == 404) return Status::Ok ();
	return Status::Fail (ErrorFromResponse (response.Value ()));
}

} // namespace rtx
