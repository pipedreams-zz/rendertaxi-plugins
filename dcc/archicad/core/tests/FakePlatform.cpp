#include "FakePlatform.hpp"

#include <cstdio>
#include <sstream>

#include "rtx/Ids.hpp"
#include "rtx/Json.hpp"
#include "rtx/Sha256.hpp"

namespace testing {
namespace {

using rtx::Json;
using rtx::JsonPtr;

std::string Text (const JsonPtr& node, const char* key)
{
	if (node == nullptr) return {};
	const JsonPtr field = node->Get (key);
	return field != nullptr && field->GetKind () == Json::Kind::String ? field->StringOr ("")
																	   : std::string ();
}

long long IntOf (const JsonPtr& node, const char* key)
{
	if (node == nullptr) return 0;
	const JsonPtr field = node->Get (key);
	return field != nullptr ? field->IntOr (0) : 0;
}

MockResponse Json200 (int status, const std::string& body)
{
	MockResponse response;
	response.status = status;
	response.body = body;
	return response;
}

/** Das einheitliche Fehlerformat von `/api/v1` (§3). */
MockResponse ApiError (int status, const std::string& code, const std::string& message,
					   const std::string& reason = {}, const std::string& fieldPath = {},
					   const std::string& fieldCode = {}, long long intervalDetail = 0)
{
	JsonPtr error = Json::MakeObject ();
	error->Set ("code", Json::MakeString (code));
	error->Set ("message", Json::MakeString (message));
	error->Set ("requestId", Json::MakeString (rtx::NewUuidV7 ()));
	if (!reason.empty ()) {
		JsonPtr details = Json::MakeObject ();
		details->Set ("reason", Json::MakeString (reason));
		if (intervalDetail > 0) details->Set ("interval", Json::MakeInt (intervalDetail));
		error->Set ("details", details);
	}
	if (!fieldPath.empty ()) {
		JsonPtr item = Json::MakeObject ();
		item->Set ("path", Json::MakeString (fieldPath));
		item->Set ("code", Json::MakeString (fieldCode));
		item->Set ("message", Json::MakeString (message));
		JsonPtr list = Json::MakeArray ();
		list->Append (item);
		error->Set ("fieldErrors", list);
	}
	JsonPtr root = Json::MakeObject ();
	root->Set ("error", error);
	return Json200 (status, root->Serialize ());
}

std::string Segment (const std::string& path, std::size_t index)
{
	std::vector<std::string> parts;
	std::size_t start = 0;
	while (start < path.size ()) {
		const std::size_t slash = path.find ('/', start);
		if (slash == std::string::npos) { parts.push_back (path.substr (start)); break; }
		if (slash > start) parts.push_back (path.substr (start, slash - start));
		start = slash + 1;
	}
	return index < parts.size () ? parts[index] : std::string ();
}

/** `Idempotency-Key` muss eine UUIDv7 sein (§7.3). */
bool IsUuidV7Key (const std::string& value)
{
	return rtx::IsUuidV7 (value);
}

std::string Instant ()
{
	return rtx::NowTimestampUtc ();
}

} // namespace

FakePlatform::FakePlatform () = default;

void FakePlatform::SetBaseUrl (std::string url)
{
	std::lock_guard<std::mutex> guard (mutex);
	baseUrl = std::move (url);
}

void FakePlatform::SetTranscriptPath (std::string path)
{
	std::lock_guard<std::mutex> guard (mutex);
	// **Nicht** löschen: ein Testlauf legt viele Scheinserver an, und das
	// Protokoll soll den gesamten Lauf tragen. Geleert wird es von
	// `scripts/test.sh`, bevor der Lauf beginnt.
	transcriptPath = std::move (path);
}

void FakePlatform::ApproveDevice ()
{
	std::lock_guard<std::mutex> guard (mutex);
	deviceApproved = true;
}

void FakePlatform::DenyDevice ()
{
	std::lock_guard<std::mutex> guard (mutex);
	deviceDenied = true;
}

void FakePlatform::SetAutoApprove (bool value)
{
	std::lock_guard<std::mutex> guard (mutex);
	autoApprove = value;
}

void FakePlatform::RevokeAllTokens ()
{
	std::lock_guard<std::mutex> guard (mutex);
	validTokens.clear ();
}

void FakePlatform::FailNext (const std::string& path, int count)
{
	std::lock_guard<std::mutex> guard (mutex);
	failures[path] = count;
}

void FakePlatform::FailNextMatching (const std::string& needle, int count)
{
	std::lock_guard<std::mutex> guard (mutex);
	failuresByNeedle[needle] = count;
}

void FakePlatform::ExpireSessions ()
{
	std::lock_guard<std::mutex> guard (mutex);
	for (auto& entry : sessions) {
		if (!entry.second.finalized) { entry.second.state = "expired"; entry.second.expired = true; }
	}
}

void FakePlatform::SetAlwaysExpire (bool value)
{
	std::lock_guard<std::mutex> guard (mutex);
	alwaysExpire = value;
}

void FakePlatform::RejectNextFile ()
{
	std::lock_guard<std::mutex> guard (mutex);
	rejectNextFile = true;
}

void FakePlatform::SlowDownNext (int count)
{
	std::lock_guard<std::mutex> guard (mutex);
	slowDownRemaining = count;
}

void FakePlatform::RateLimitNext (int count)
{
	std::lock_guard<std::mutex> guard (mutex);
	rateLimitRemaining = count;
}

int FakePlatform::AssetsCreated () const
{
	std::lock_guard<std::mutex> guard (mutex);
	return assetsCreated;
}

int FakePlatform::ViewpointsCreated () const
{
	std::lock_guard<std::mutex> guard (mutex);
	return viewpointsCreated;
}

int FakePlatform::BaseImageVersions () const
{
	std::lock_guard<std::mutex> guard (mutex);
	return baseImageVersions;
}

std::string FakePlatform::LastOpenUrl () const
{
	std::lock_guard<std::mutex> guard (mutex);
	return lastOpenUrl;
}

int FakePlatform::PendingAuthorizationPolls () const
{
	std::lock_guard<std::mutex> guard (mutex);
	return pendingPolls;
}

int FakePlatform::RevocationCount () const
{
	std::lock_guard<std::mutex> guard (mutex);
	return revocations;
}

bool FakePlatform::IsTokenValid (const std::string& token) const
{
	std::lock_guard<std::mutex> guard (mutex);
	return validTokens.count (token) > 0;
}

MockHandler FakePlatform::Handler ()
{
	return [this] (const MockRequest& request) {
		MockResponse response = Dispatch (request);
		// **Jede** Antwort trägt eine serverseitig erzeugte `X-Request-Id`
		// (§3, ADR 0005). Ein vom Plugin gesendeter Wert wird nicht übernommen.
		if (request.path.rfind ("/api/v1/", 0) == 0)
			response.headers.emplace_back ("X-Request-Id", rtx::NewUuidV7 ());
		Record (request, response);
		return response;
	};
}

void FakePlatform::Record (const MockRequest& request, const MockResponse& response)
{
	std::lock_guard<std::mutex> guard (mutex);
	if (transcriptPath.empty ()) return;
	// Nur die Operationen des Profils; Browserseiten und Blobs sind nicht Teil
	// des OpenAPI-Dokuments.
	if (request.path.rfind ("/api/v1/plugin/", 0) != 0) return;

	JsonPtr line = Json::MakeObject ();
	line->Set ("method", Json::MakeString (request.method));
	line->Set ("path", Json::MakeString (request.path));
	line->Set ("query", Json::MakeString (request.query));
	line->Set ("status", Json::MakeInt (response.status));
	line->Set ("idempotencyKey", Json::MakeString (request.Header ("Idempotency-Key")));
	line->Set ("contentType", Json::MakeString (response.contentType));
	line->Set ("requestBody", Json::MakeString (request.body));
	line->Set ("responseBody", Json::MakeString (response.body));

	std::FILE* file = std::fopen (transcriptPath.c_str (), "ab");
	if (file == nullptr) return;
	const std::string text = line->Serialize () + "\n";
	std::fwrite (text.data (), 1, text.size (), file);
	std::fclose (file);
}

FakePlatform::Session* FakePlatform::FindSession (const std::string& captureId)
{
	const auto found = sessions.find (captureId);
	return found == sessions.end () ? nullptr : &found->second;
}

std::string FakePlatform::SessionJson (const Session& session) const
{
	JsonPtr root = Json::MakeObject ();
	root->Set ("captureId", Json::MakeString (session.captureId));
	root->Set ("contract", Json::MakeString (session.contract));
	root->Set ("contractVersion", Json::MakeString (session.contractVersion));
	root->Set ("state", Json::MakeString (session.state));

	JsonPtr target = Json::Parse (session.targetJson);
	root->Set ("target", target != nullptr ? target : Json::MakeObject ());
	root->Set ("georeference", Json::MakeNull ());

	JsonPtr files = Json::MakeArray ();
	for (const FileEntry& file : session.files) {
		JsonPtr node = Json::MakeObject ();
		node->Set ("role", Json::MakeString (file.role));
		node->Set ("path", Json::MakeString (file.path));
		node->Set ("mediaType", Json::MakeString (file.mediaType));
		node->Set ("byteSize", Json::MakeInt (file.byteSize));
		node->Set ("sha256", Json::MakeString (file.sha256));
		node->Set ("assetId",
				   file.assetId.empty () ? Json::MakeNull () : Json::MakeString (file.assetId));
		node->Set ("attempt", Json::MakeInt (file.attempt));
		node->Set ("state", Json::MakeString (file.state));
		if (file.state == "rejected") {
			JsonPtr error = Json::MakeObject ();
			error->Set ("code", Json::MakeString (file.errorCode));
			error->Set ("message", Json::MakeString ("Die Prüfung ist fehlgeschlagen."));
			node->Set ("error", error);
		} else {
			node->Set ("error", Json::MakeNull ());
		}
		files->Append (node);
	}
	root->Set ("files", files);

	JsonPtr manifest = Json::MakeObject ();
	manifest->Set ("sha256", Json::MakeString (session.manifestSha256));
	manifest->Set ("state", Json::MakeString (session.manifestState));
	root->Set ("manifest", manifest);

	JsonPtr validation = Json::MakeObject ();
	validation->Set ("state", Json::MakeString (session.validationState));
	JsonPtr errors = Json::MakeArray ();
	for (const auto& entry : session.validationErrors) {
		JsonPtr item = Json::MakeObject ();
		item->Set ("code", Json::MakeString (entry.first));
		item->Set ("message", Json::MakeString (entry.second));
		errors->Append (item);
	}
	validation->Set ("errors", errors);
	root->Set ("validation", validation);

	JsonPtr limits = Json::MakeObject ();
	limits->Set ("maxAssetBytes", Json::MakeInt (52428800));
	limits->Set ("maxGeometryBytes", Json::MakeInt (67108864));
	limits->Set ("maxTotalBytes", Json::MakeInt (209715200));
	limits->Set ("maxAssetCount", Json::MakeInt (16));
	limits->Set ("maxManifestBytes", Json::MakeInt (1048576));
	JsonPtr media = Json::MakeArray ();
	media->Append (Json::MakeString ("image/jpeg"));
	media->Append (Json::MakeString ("image/png"));
	media->Append (Json::MakeString ("image/webp"));
	limits->Set ("allowedMediaTypes", media);
	limits->Set ("captureTtlSeconds", Json::MakeInt (86400));
	root->Set ("limits", limits);

	if (session.finalized) {
		JsonPtr result = Json::MakeObject ();
		result->Set ("kind", Json::MakeString ("platform-assets"));
		JsonPtr assetIds = Json::MakeObject ();
		for (const auto& entry : session.assetIds)
			assetIds->Set (entry.first, Json::MakeString (entry.second));
		result->Set ("assetIds", assetIds);
		result->Set ("viewpointId", session.viewpointId.empty ()
										? Json::MakeNull ()
										: Json::MakeString (session.viewpointId));
		result->Set ("openUrl", Json::MakeString (session.openUrl));
		root->Set ("result", result);
	} else {
		root->Set ("result", Json::MakeNull ());
	}

	root->Set ("createdAt", Json::MakeString (Instant ()));
	root->Set ("expiresAt", Json::MakeString (Instant ()));
	return root->Serialize ();
}

MockResponse FakePlatform::CaptureFiles (Session& session, const MockRequest& request)
{
	const JsonPtr body = rtx::Json::Parse (request.body);
	if (body == nullptr) return ApiError (400, "validation_failed", "Kein JSON.");
	const std::string action = Text (body, "action");
	const std::string path = Text (body, "path");
	const long long attempt = IntOf (body, "attempt");

	FileEntry* file = nullptr;
	for (FileEntry& candidate : session.files) {
		if (candidate.path == path) { file = &candidate; break; }
	}
	if (file == nullptr)
		return ApiError (400, "validation_failed", "Unbekannter Pfad.", "asset_unexpected");

	if (action == "begin") {
		// Versuchsregel (§7.3): sie gilt **vor** der Wiederholungsregel.
		if (attempt == file->attempt && file->state == "uploading") {
			// Wiederholung desselben `begin`: derselbe Versuch, frisches Ticket.
		} else if (attempt == file->attempt + 1 &&
				   (file->state == "missing" || file->state == "rejected")) {
			file->attempt = static_cast<int> (attempt);
			file->state = "uploading";
			file->errorCode.clear ();
		} else if (file->state == "deduplicated" || file->state == "verified") {
			// Keine Übertragung nötig: Datei und `ticket: null`.
			JsonPtr root = rtx::Json::MakeObject ();
			root->Set ("state", rtx::Json::MakeString (session.state));
			const JsonPtr document = rtx::Json::Parse (SessionJson (session));
			for (const JsonPtr& item : document->Get ("files")->Items ()) {
				if (Text (item, "path") == path) root->Set ("file", item);
			}
			root->Set ("ticket", rtx::Json::MakeNull ());
			return Json200 (200, root->Serialize ());
		} else {
			return ApiError (400, "validation_failed", "Dieser Versuch passt nicht zum Zustand.",
							 "capture_state_conflict");
		}

		const std::string ticket = "ticket-" + std::to_string (nextId++);
		ticketToPath[ticket] = session.captureId + "|" + path;

		JsonPtr root = rtx::Json::MakeObject ();
		root->Set ("state", rtx::Json::MakeString (session.state));
		const JsonPtr document = rtx::Json::Parse (SessionJson (session));
		for (const JsonPtr& item : document->Get ("files")->Items ()) {
			if (Text (item, "path") == path) root->Set ("file", item);
		}
		JsonPtr ticketNode = rtx::Json::MakeObject ();
		ticketNode->Set ("uploadUrl", rtx::Json::MakeString (baseUrl + "/__blob/" + ticket));
		ticketNode->Set ("method", rtx::Json::MakeString ("PUT"));
		JsonPtr headers = rtx::Json::MakeObject ();
		headers->Set ("Content-Type", rtx::Json::MakeString (file->mediaType));
		headers->Set ("If-None-Match", rtx::Json::MakeString ("*"));
		ticketNode->Set ("requiredHeaders", headers);
		ticketNode->Set ("expiresAt", rtx::Json::MakeString (Instant ()));
		root->Set ("ticket", ticketNode);
		return Json200 (200, root->Serialize ());
	}

	if (action == "complete") {
		if (attempt != file->attempt)
			return ApiError (400, "validation_failed", "Das ist nicht der laufende Versuch.",
							 "capture_state_conflict");
		if (file->state == "uploading") {
			// Die serverseitige Prüfung: Existenz, Größe, SHA-256, Signatur.
			const std::string blob = blobBytes.count (file->sha256) > 0 ? file->sha256 : std::string ();
			if (rejectNextFile) {
				rejectNextFile = false;
				file->state = "rejected";
				file->errorCode = "asset_hash_mismatch";
			} else if (blob.empty ()) {
				file->state = "rejected";
				file->errorCode = "asset_missing";
			} else {
				file->state = "verified";
				if (file->assetId.empty ()) file->assetId = rtx::NewUuidV7 ();
			}
		}
		bool allSettled = true;
		for (const FileEntry& entry : session.files) {
			if (entry.state != "verified" && entry.state != "deduplicated") allSettled = false;
		}
		if (allSettled) session.state = "awaiting-manifest";

		JsonPtr root = rtx::Json::MakeObject ();
		root->Set ("state", rtx::Json::MakeString (session.state));
		const JsonPtr document = rtx::Json::Parse (SessionJson (session));
		for (const JsonPtr& item : document->Get ("files")->Items ()) {
			if (Text (item, "path") == path) root->Set ("file", item);
		}
		root->Set ("ticket", rtx::Json::MakeNull ());
		return Json200 (200, root->Serialize ());
	}

	return ApiError (400, "validation_failed", "Unbekannte Aktion.");
}

MockResponse FakePlatform::Capture (const MockRequest& request)
{
	const std::string captureId = Segment (request.path, 4);
	const std::string action = Segment (request.path, 5);
	const std::string key = request.Header ("Idempotency-Key");

	// `GET` ist der einzige Aufruf ohne Schlüssel (§7.3).
	const bool changesState = request.method == "POST";
	if (changesState && !IsUuidV7Key (key))
		return ApiError (400, "validation_failed", "Der Idempotency-Key ist keine UUIDv7.", {},
						 "Idempotency-Key", "invalid_idempotency_key");

	Session* session = FindSession (captureId);
	if (session == nullptr) return ApiError (404, "not_found", "Capture unbekannt.");
	if (session->idempotencyKey != key && changesState)
		return ApiError (409, "idempotency_conflict", "Anderer Schlüssel an bestehender Session.");

	if (action.empty () && request.method == "GET") return Json200 (200, SessionJson (*session));

	if (session->expired)
		return ApiError (400, "validation_failed", "Die Session ist abgelaufen.", "capture_expired");

	if (action == "files") return CaptureFiles (*session, request);

	if (action == "manifest") {
		for (const FileEntry& file : session->files) {
			if (file.state != "verified" && file.state != "deduplicated")
				return ApiError (400, "validation_failed",
								 "Das Manifest kommt erst, wenn alle Dateien geprüft sind.",
								 "capture_state_conflict");
		}
		const std::string digest = rtx::Sha256::OfString (request.body);
		if (digest != session->manifestSha256) {
			session->state = "rejected";
			session->manifestState = "rejected";
			session->validationState = "failed";
			session->validationErrors.emplace_back (
				"manifest_hash_mismatch", "Die Bytes passen nicht zu manifestSha256.");
			return Json200 (200, SessionJson (*session));
		}
		const JsonPtr manifest = rtx::Json::Parse (request.body);
		if (manifest == nullptr || Text (manifest, "contract") != session->contract) {
			session->state = "rejected";
			session->manifestState = "rejected";
			session->validationState = "failed";
			session->validationErrors.emplace_back ("unsupported_contract",
												   "Anderer contract als in der Anlage.");
			return Json200 (200, SessionJson (*session));
		}
		session->manifestState = "accepted";
		session->state = "validating";
		return Json200 (200, SessionJson (*session));
	}

	if (action == "finalize") {
		if (session->manifestState != "accepted")
			return ApiError (400, "validation_failed", "Ohne angenommenes Manifest kein Abschluss.",
							 "capture_state_conflict");
		if (!request.body.empty () && request.body != "{}") {
			const JsonPtr body = rtx::Json::Parse (request.body);
			if (body != nullptr && !body->Fields ().empty ())
				return ApiError (400, "validation_failed", "Der Körper von finalize ist leer.");
		}
		if (!session->finalized) {
			// Abschluss und Zuordnung sind **eine** Transaktion (§7.7).
			const std::string targetKey = session->targetProjectId + "|" + session->targetMode +
										  "|" + session->targetViewpointId + "|" +
										  session->targetViewpointName;
			if (session->targetMode == "update") {
				session->viewpointId = session->targetViewpointId;
			} else if (session->targetMode == "create") {
				const auto known = viewpointsByTarget.find (targetKey);
				if (known != viewpointsByTarget.end ()) {
					session->viewpointId = known->second;
				} else {
					session->viewpointId = rtx::NewUuidV7 ();
					viewpointsByTarget[targetKey] = session->viewpointId;
					++viewpointsCreated;
				}
			}
			if (!session->viewpointId.empty ()) {
				Viewpoint& viewpoint = viewpoints[session->viewpointId];
				viewpoint.id = session->viewpointId;
				if (viewpoint.name.empty ())
					viewpoint.name = session->targetViewpointName.empty () ? "Blickpunkt"
																		   : session->targetViewpointName;
				const std::string role = session->targetBaseImageRole.empty ()
											 ? std::string ("viewport")
											 : session->targetBaseImageRole;
				for (const FileEntry& file : session->files) {
					if (file.role == role) viewpoint.baseImageShas.push_back (file.sha256);
				}
				++baseImageVersions;
			}
			for (const FileEntry& file : session->files) session->assetIds[file.role] = file.assetId;
			session->openUrl = baseUrl + "/app/projects/" + session->targetProjectId +
							   (session->viewpointId.empty ()
									? std::string ()
									: "?viewpoint=" + session->viewpointId);
			session->state = "verified";
			session->validationState = "passed";
			session->finalized = true;
			lastOpenUrl = session->openUrl;
		}
		return Json200 (200, SessionJson (*session));
	}

	if (action == "abort") {
		if (!session->finalized) {
			session->state = "aborted";
			session->validationState = "pending";
		}
		return Json200 (200, SessionJson (*session));
	}

	return ApiError (404, "not_found", "Unbekannte Capture-Operation.");
}

MockResponse FakePlatform::Dispatch (const MockRequest& request)
{
	std::lock_guard<std::mutex> guard (mutex);

	const auto failure = failures.find (request.path);
	if (failure != failures.end () && failure->second > 0) {
		--failure->second;
		// `500 internal_error` ist der Code des Vertrags für interne Fehler
		// (§7.8); `503` kennt das Profil nicht.
		return ApiError (500, "internal_error", "Der Scheinserver simuliert einen Ausfall.");
	}
	for (auto& entry : failuresByNeedle) {
		if (entry.second > 0 && request.path.find (entry.first) != std::string::npos) {
			--entry.second;
			// `500 internal_error` ist der Code des Vertrags für interne Fehler
		// (§7.8); `503` kennt das Profil nicht.
		return ApiError (500, "internal_error", "Der Scheinserver simuliert einen Ausfall.");
		}
	}

	// --- Seiten für den Browser --------------------------------------------
	if (request.path == "/geraet" || request.path == "/geraet/approve")
		return ServeDevicePage (request);
	if (request.path.rfind ("/app/projects/", 0) == 0) {
		const std::size_t at = request.query.find ("viewpoint=");
		return ServeViewpointPage (at == std::string::npos ? std::string ()
														   : request.query.substr (at + 10));
	}
	if (request.path.rfind ("/blob/", 0) == 0) {
		const auto found = blobBytes.find (request.path.substr (6));
		if (found == blobBytes.end ()) return ApiError (404, "not_found", "Kein Bild.");
		MockResponse response;
		response.contentType = "image/png";
		response.body = found->second;
		return response;
	}

	// --- Objektspeicher ------------------------------------------------------
	if (request.path.rfind ("/__blob/", 0) == 0 && request.method == "PUT") {
		// Der `PUT` geht **ohne** Authorization; ein Bearer wäre ein Befund.
		if (!request.Header ("Authorization").empty ())
			return ApiError (400, "validation_failed",
							 "Der PUT an den Objektspeicher trägt keinen Authorization-Kopf.");
		const std::string ticket = request.path.substr (8);
		const auto found = ticketToPath.find (ticket);
		if (found == ticketToPath.end ()) return ApiError (404, "not_found", "Ticket unbekannt.");
		const std::string captureId = found->second.substr (0, found->second.find ('|'));
		const std::string path = found->second.substr (found->second.find ('|') + 1);
		Session* session = FindSession (captureId);
		if (session == nullptr) return ApiError (404, "not_found", "Session unbekannt.");

		const std::string digest = rtx::Sha256::OfString (request.body);
		for (FileEntry& file : session->files) {
			if (file.path != path) continue;
			// `If-None-Match: *` macht den PUT einmalig (§7.4).
			if (storedBlobs.count (digest) > 0 && blobBytes.count (digest) > 0 &&
				request.Header ("If-None-Match") == "*" && file.state == "uploading" &&
				blobBytes[digest] == request.body && file.assetId.empty () == false)
				return Json200 (412, "");
			if (storedBlobs.insert (digest).second) ++assetsCreated;
			blobBytes[digest] = request.body;
			if (file.assetId.empty ()) file.assetId = rtx::NewUuidV7 ();
		}
		return Json200 (200, "");
	}

	const std::string authorization = request.Header ("Authorization");
	const std::string bearer =
		authorization.rfind ("Bearer ", 0) == 0 ? authorization.substr (7) : std::string ();
	const bool needsToken = request.path.rfind ("/api/v1/plugin/captures", 0) == 0 ||
							request.path == "/api/v1/plugin/me" ||
							request.path.rfind ("/api/v1/projects", 0) == 0;
	if (needsToken && validTokens.find (bearer) == validTokens.end ())
		return ApiError (401, "unauthorized",
						 "Die Verbindung wurde beendet. Bitte neu anmelden.");

	// --- Handshake ------------------------------------------------------------
	if (request.path == "/api/v1/plugin/handshake") {
		JsonPtr root = rtx::Json::MakeObject ();
		root->Set ("apiVersion", rtx::Json::MakeString ("v1"));
		root->Set ("profile", rtx::Json::MakeString ("rendertaxi.plugin-api"));
		root->Set ("profileVersion", rtx::Json::MakeString ("1.0.0"));
		root->Set ("serverTime", rtx::Json::MakeString (Instant ()));

		JsonPtr contracts = rtx::Json::MakeArray ();
		JsonPtr capture = rtx::Json::MakeObject ();
		capture->Set ("contract", rtx::Json::MakeString ("rendertaxi.plugin.capture-manifest"));
		capture->Set ("finalization", rtx::Json::MakeString ("platform-assets"));
		capture->Set ("georeference", rtx::Json::MakeString ("unsupported"));
		capture->Set ("availability", rtx::Json::MakeString ("available"));
		JsonPtr versions = rtx::Json::MakeArray ();
		JsonPtr one = rtx::Json::MakeObject ();
		one->Set ("major", rtx::Json::MakeInt (1));
		one->Set ("maxMinor", rtx::Json::MakeInt (0));
		versions->Append (one);
		capture->Set ("versions", versions);
		contracts->Append (capture);
		JsonPtr model = rtx::Json::MakeObject ();
		model->Set ("contract", rtx::Json::MakeString ("rendertaxi.archicad.visual-snapshot"));
		model->Set ("finalization", rtx::Json::MakeString ("model-version"));
		model->Set ("georeference", rtx::Json::MakeString ("required"));
		model->Set ("availability", rtx::Json::MakeString ("planned"));
		model->Set ("versions", rtx::Json::MakeArray ());
		contracts->Append (model);
		root->Set ("contracts", contracts);

		// `negotiation` entsteht nur mit `contract` und `contractVersion` (§4).
		const bool asked = request.query.find ("contract=") != std::string::npos &&
						   request.query.find ("contractVersion=") != std::string::npos;
		if (asked) {
			JsonPtr negotiation = rtx::Json::MakeObject ();
			negotiation->Set ("contract",
							  rtx::Json::MakeString ("rendertaxi.plugin.capture-manifest"));
			negotiation->Set ("contractVersion", rtx::Json::MakeString ("1.0.0"));
			negotiation->Set ("result", rtx::Json::MakeString ("supported"));
			negotiation->Set ("highestSupportedVersion", rtx::Json::MakeString ("1.0.0"));
			root->Set ("negotiation", negotiation);
		} else {
			root->Set ("negotiation", rtx::Json::MakeNull ());
		}

		JsonPtr hosts = rtx::Json::MakeArray ();
		JsonPtr host = rtx::Json::MakeObject ();
		host->Set ("hostKey", rtx::Json::MakeString ("archicad"));
		// Wortgleich zum ausgelieferten Server auf dev.rendertaxi.ai
		// (Handshake vom 24.09.2026).
		host->Set ("minimumPluginVersion", rtx::Json::MakeString ("1.0.0"));
		host->Set ("latestPluginVersion", rtx::Json::MakeString ("1.0.0"));
		hosts->Append (host);
		root->Set ("hosts", hosts);

		JsonPtr limits = rtx::Json::MakeObject ();
		limits->Set ("maxAssetBytes", rtx::Json::MakeInt (52428800));
		limits->Set ("maxGeometryBytes", rtx::Json::MakeInt (67108864));
		limits->Set ("maxTotalBytes", rtx::Json::MakeInt (209715200));
		limits->Set ("maxAssetCount", rtx::Json::MakeInt (16));
		limits->Set ("maxManifestBytes", rtx::Json::MakeInt (1048576));
		JsonPtr media = rtx::Json::MakeArray ();
		media->Append (rtx::Json::MakeString ("image/jpeg"));
		media->Append (rtx::Json::MakeString ("image/png"));
		media->Append (rtx::Json::MakeString ("image/webp"));
		limits->Set ("allowedMediaTypes", media);
		limits->Set ("captureTtlSeconds", rtx::Json::MakeInt (86400));
		root->Set ("limits", limits);

		JsonPtr update = rtx::Json::MakeObject ();
		update->Set ("status", rtx::Json::MakeString ("current"));
		update->Set ("message", rtx::Json::MakeNull ());
		update->Set ("url", rtx::Json::MakeNull ());
		root->Set ("update", update);
		return Json200 (200, root->Serialize ());
	}

	// --- Gerätefluss ----------------------------------------------------------
	if (request.path == "/api/v1/plugin/auth/device") {
		const JsonPtr body = rtx::Json::Parse (request.body);
		if (body == nullptr || Text (body, "client_id").empty () || body->Get ("device") == nullptr)
			return ApiError (400, "validation_failed", "client_id und device sind Pflicht.");
		const JsonPtr device = body->Get ("device");
		if (Text (device, "deviceId").empty () || Text (device, "hostKey").empty () ||
			Text (device, "os").empty () || Text (device, "architecture").empty ())
			return ApiError (400, "validation_failed", "Das Gerät ist unvollständig beschrieben.");

		const std::string deviceCode = "dc-" + rtx::RandomHex (24);
		pendingDeviceCodes[deviceCode] = "WDJB-MJHT";
		JsonPtr root = rtx::Json::MakeObject ();
		root->Set ("device_code", rtx::Json::MakeString (deviceCode));
		root->Set ("user_code", rtx::Json::MakeString ("WDJB-MJHT"));
		root->Set ("verification_uri", rtx::Json::MakeString (baseUrl + "/geraet"));
		root->Set ("verification_uri_complete",
				   rtx::Json::MakeString (baseUrl + "/geraet?code=WDJB-MJHT"));
		root->Set ("expires_in", rtx::Json::MakeInt (600));
		root->Set ("interval", rtx::Json::MakeInt (1));
		return Json200 (200, root->Serialize ());
	}

	if (request.path == "/api/v1/plugin/auth/device/token") {
		const JsonPtr body = rtx::Json::Parse (request.body);
		if (body == nullptr || Text (body, "client_id").empty () ||
			Text (body, "grant_type").empty ())
			return ApiError (400, "validation_failed", "grant_type und client_id sind Pflicht.");
		const std::string deviceCode = Text (body, "device_code");
		if (pendingDeviceCodes.find (deviceCode) == pendingDeviceCodes.end ())
			return ApiError (400, "validation_failed", "Unbekannter Gerätecode.", "invalid_grant");
		if (rateLimitRemaining > 0) {
			// Ratenbegrenzung an einer der vier unangemeldeten Operationen (§3).
			--rateLimitRemaining;
			MockResponse limited =
				ApiError (429, "rate_limited", "Zu viele Anfragen auf diesen Code.");
			limited.headers.emplace_back ("Retry-After", "7");
			return limited;
		}
		if (slowDownRemaining > 0) {
			--slowDownRemaining;
			return ApiError (400, "validation_failed", "Zu schnell abgefragt.", "slow_down", {}, {},
							 10);
		}
		if (deviceDenied)
			return ApiError (400, "validation_failed", "Die Anmeldung wurde abgelehnt.",
							 "access_denied");
		if (autoApprove) deviceApproved = true;
		if (!deviceApproved) {
			++pendingPolls;
			return ApiError (400, "validation_failed", "Die Anmeldung wurde noch nicht bestätigt.",
							 "authorization_pending");
		}
		const std::string token = "tok-" + rtx::RandomHex (24);
		validTokens.insert (token);
		pendingDeviceCodes.erase (deviceCode);
		JsonPtr root = rtx::Json::MakeObject ();
		root->Set ("access_token", rtx::Json::MakeString (token));
		root->Set ("token_type", rtx::Json::MakeString ("Bearer"));
		root->Set ("expires_in", rtx::Json::MakeInt (2592000));
		root->Set ("scope", rtx::Json::MakeString ("plugin"));
		return Json200 (200, root->Serialize ());
	}

	if (request.path == "/api/v1/plugin/auth/revoke") {
		const JsonPtr body = rtx::Json::Parse (request.body);
		const std::string token = Text (body, "token");
		if (token.empty ())
			return ApiError (400, "validation_failed", "token ist Pflicht.");
		// Bekannt, unbekannt, bereits widerrufen — gleiche Antwort (§5.4).
		if (validTokens.erase (token) > 0) ++revocations;
		return Json200 (200, "{}");
	}

	if (request.path == "/api/v1/plugin/me") {
		JsonPtr root = rtx::Json::MakeObject ();
		JsonPtr user = rtx::Json::MakeObject ();
		user->Set ("id", rtx::Json::MakeString ("0199a000-0000-7000-8000-000000000001"));
		user->Set ("displayName", rtx::Json::MakeString ("Testkonto"));
		user->Set ("email", rtx::Json::MakeString ("test@example.test"));
		root->Set ("user", user);
		JsonPtr organization = rtx::Json::MakeObject ();
		organization->Set ("id", rtx::Json::MakeString ("0199a000-0000-7000-8000-000000000002"));
		organization->Set ("name", rtx::Json::MakeString ("Testorganisation"));
		root->Set ("organization", organization);
		root->Set ("role", rtx::Json::MakeString ("owner"));
		JsonPtr device = rtx::Json::MakeObject ();
		device->Set ("deviceId", rtx::Json::MakeString ("0199a000-0000-7000-8000-00000000000d"));
		device->Set ("hostKey", rtx::Json::MakeString ("archicad"));
		device->Set ("hostVersion", rtx::Json::MakeString ("28.1"));
		device->Set ("pluginVersion", rtx::Json::MakeString ("0.1.0"));
		device->Set ("os", rtx::Json::MakeString ("macos"));
		device->Set ("architecture", rtx::Json::MakeString ("arm64"));
		root->Set ("device", device);
		JsonPtr token = rtx::Json::MakeObject ();
		token->Set ("scope", rtx::Json::MakeString ("plugin"));
		token->Set ("issuedAt", rtx::Json::MakeString (Instant ()));
		token->Set ("expiresAt", rtx::Json::MakeString (Instant ()));
		token->Set ("renewableUntil", rtx::Json::MakeString (Instant ()));
		root->Set ("token", token);
		return Json200 (200, root->Serialize ());
	}

	// --- Lesewege --------------------------------------------------------------
	if (request.path == "/api/v1/projects")
		return Json200 (200,
						R"({"items":[{"id":"0199a000-0000-7000-8000-00000000000a","name":"Testprojekt",)"
						R"("status":"active","viewpointCount":2,"lastChangedAt":null,)"
						R"("createdAt":"2026-09-01T00:00:00.000Z","regionPolicy":"eu"}],"nextCursor":null})");
	if (request.path.rfind ("/api/v1/projects/", 0) == 0 &&
		request.path.find ("/workspace") != std::string::npos)
		return Json200 (200,
						R"({"viewpoints":[{"id":"0199a000-0000-7000-8000-00000000001a","name":"Nordansicht"},)"
						R"({"id":"0199a000-0000-7000-8000-00000000001b","name":"Innenraum"}]})");
	if (request.path.rfind ("/api/v1/projects/", 0) == 0 &&
		request.path.find ("/viewpoint-overview") != std::string::npos)
		return Json200 (200,
						R"({"viewpoints":[)"
						R"({"viewpointId":"0199a000-0000-7000-8000-00000000001a",)"
						R"("desired":{"kind":"aspect_ratio","value":"16:9"}},)"
						R"({"viewpointId":"0199a000-0000-7000-8000-00000000001b","desired":null}]})");

	// --- Capture ---------------------------------------------------------------
	if (request.path == "/api/v1/plugin/captures" && request.method == "POST") {
		const std::string key = request.Header ("Idempotency-Key");
		if (!IsUuidV7Key (key))
			return ApiError (400, "validation_failed", "Der Idempotency-Key ist keine UUIDv7.", {},
							 "Idempotency-Key", "invalid_idempotency_key");
		const JsonPtr body = rtx::Json::Parse (request.body);
		if (body == nullptr) return ApiError (400, "validation_failed", "Kein JSON.");
		if (body->Get ("target") == nullptr)
			return ApiError (400, "validation_failed", "target ist Pflicht.");
		if (body->Get ("georeference") != nullptr)
			return ApiError (400, "validation_failed",
							 "Der Bildweg nimmt keine Georeferenz an.", "georeference_mismatch");
		const std::string manifestSha256 = Text (body, "manifestSha256");
		if (manifestSha256.size () != 64)
			return ApiError (400, "validation_failed", "manifestSha256 ist Pflicht.");

		const std::string fingerprint = request.body;
		const auto existing = sessionByIdempotencyKey.find (key);
		if (existing != sessionByIdempotencyKey.end ()) {
			Session& known = sessions[existing->second];
			if (known.requestFingerprint != fingerprint)
				return ApiError (409, "idempotency_conflict",
								 "Gleicher Schlüssel, abweichender Anlagekörper.");
			return Json200 (201, SessionJson (known));
		}

		Session session;
		session.captureId = rtx::NewUuidV7 ();
		session.idempotencyKey = key;
		session.manifestSha256 = manifestSha256;
		session.requestFingerprint = fingerprint;
		session.contract = Text (body, "contract");
		session.contractVersion = Text (body, "contractVersion");

		const JsonPtr target = body->Get ("target");
		session.targetProjectId = Text (target, "projectId");
		session.targetJson = target->Serialize ();
		if (const JsonPtr viewpoint = target->Get ("viewpoint")) {
			session.targetMode = Text (viewpoint, "mode");
			session.targetViewpointId = Text (viewpoint, "viewpointId");
			session.targetViewpointName = Text (viewpoint, "name");
			session.targetBaseImageRole = Text (viewpoint, "baseImageRole");
		}

		if (const JsonPtr files = body->Get ("files")) {
			for (const JsonPtr& item : files->Items ()) {
				FileEntry file;
				file.role = Text (item, "role");
				file.path = Text (item, "path");
				file.sha256 = Text (item, "sha256");
				file.mediaType = Text (item, "mediaType");
				file.byteSize = IntOf (item, "byteSize");
				if (storedBlobs.count (file.sha256) > 0) {
					file.state = "deduplicated";
					file.assetId = rtx::NewUuidV7 ();
				}
				session.files.push_back (file);
			}
		}
		bool allSettled = !session.files.empty ();
		for (const FileEntry& file : session.files) {
			if (file.state != "verified" && file.state != "deduplicated") allSettled = false;
		}
		session.state = allSettled ? "awaiting-manifest" : "awaiting-assets";
		if (alwaysExpire) { session.state = "expired"; session.expired = true; }

		sessions[session.captureId] = session;
		sessionByIdempotencyKey[key] = session.captureId;
		return Json200 (201, SessionJson (sessions[session.captureId]));
	}

	if (request.path.rfind ("/api/v1/plugin/captures/", 0) == 0) return Capture (request);

	return ApiError (404, "not_found", "Unbekannter Pfad: " + request.path);
}

MockResponse FakePlatform::ServeDevicePage (const MockRequest& request)
{
	MockResponse response;
	response.contentType = "text/html; charset=utf-8";
	if (request.method == "POST" || request.path == "/geraet/approve") {
		deviceApproved = true;
		response.body =
			"<!doctype html><meta charset=utf-8><title>Geraet bestaetigt</title>"
			"<body style=\"font:16px system-ui;padding:3rem\">"
			"<h1>Geraet bestaetigt</h1><p>Du kannst zu Archicad zurueckwechseln.</p>";
		return response;
	}
	response.body =
		"<!doctype html><meta charset=utf-8><title>Geraet anmelden</title>"
		"<body style=\"font:16px system-ui;padding:3rem\">"
		"<h1>Geraet anmelden</h1>"
		"<p>Scheinserver des rendertaxi.ai-Add-ons. Bestaetige die Anmeldung:</p>"
		"<form method=post action=\"/geraet/approve\">"
		"<button style=\"font:16px system-ui;padding:.6rem 1.2rem\">Bestaetigen</button></form>";
	return response;
}

MockResponse FakePlatform::ServeViewpointPage (const std::string& viewpointId)
{
	MockResponse response;
	response.contentType = "text/html; charset=utf-8";
	const auto found = viewpoints.find (viewpointId);
	if (found == viewpoints.end ()) {
		response.body = "<!doctype html><meta charset=utf-8><body>Projekt ohne Blickpunkt.";
		return response;
	}
	const Viewpoint& viewpoint = found->second;
	std::ostringstream body;
	body << "<!doctype html><meta charset=utf-8><title>" << viewpoint.name << "</title>"
		 << "<body style=\"font:16px system-ui;padding:2rem;background:#111;color:#eee\">"
		 << "<h1>" << viewpoint.name << "</h1>"
		 << "<p>Blickpunkt <code>" << viewpoint.id << "</code> &middot; "
		 << viewpoint.baseImageShas.size () << " Basisbildfassung(en)</p>";
	for (std::size_t i = viewpoint.baseImageShas.size (); i > 0; --i) {
		const std::string& sha = viewpoint.baseImageShas[i - 1];
		body << "<figure style=\"margin:0 0 2rem\"><figcaption>Fassung " << i
			 << (i == viewpoint.baseImageShas.size () ? " (aktuell)" : " (historisch)")
			 << "</figcaption><img src=\"/blob/" << sha
			 << "\" style=\"max-width:100%;background:#fff\"></figure>";
	}
	response.body = body.str ();
	return response;
}

} // namespace testing
