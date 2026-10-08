// Uploader, Gerätefluss und Zustandsautomat gegen einen Scheinserver, der die
// **Spezifikation** umsetzt (`docs/api/plugin-api-v1.md`, PR #139 `d35187e`).
//
// Jede Prüfung nennt den Befund, den sie festhält: V-01 bis V-20 aus dem
// Abgleichsbericht in PR #139 und F-01 bis F-03 aus dem Review an PR #141.
#include "Testing.hpp"

#include <cstdio>
#include <cstdlib>
#include <string>

#include "FakePlatform.hpp"
#include "MockServer.hpp"
#include "rtx/ArchicadCamera.hpp"
#include "rtx/CaptureTransfer.hpp"
#include "rtx/CaptureWays.hpp"
#include "rtx/Glb.hpp"
#include "rtx/DeviceLogin.hpp"
#include "rtx/Ids.hpp"
#include "rtx/Platform.hpp"
#include "rtx/Sha256.hpp"
#include "rtx/TokenStore.hpp"

using namespace rtx;

namespace {

constexpr const char* kProjectId = "0199a000-0000-7000-8000-00000000000a";
constexpr const char* kViewpointId = "0199a000-0000-7000-8000-00000000001a";

/**
 * Die Fassung, mit der die Tests auftreten. Sie liegt **nicht** fest im Text:
 * der Scheinserver meldet `minimumPluginVersion: "1.0.0"` wie der ausgelieferte
 * Server, und ein Add-On darunter dürfte nach §4 nichts übernehmen.
 */
#define RTX_TEST_PLUGIN_VERSION "1.0.0"

DeviceIdentity MakeDevice (const std::string& deviceId)
{
	DeviceIdentity device;
	device.deviceId = deviceId;
	device.hostKey = "archicad";
	device.hostVersion = "28.1";
	device.pluginVersion = RTX_TEST_PLUGIN_VERSION;
	device.os = "macos";
	device.architecture = "arm64";
	return device;
}

struct Harness {
	testing::FakePlatform platform;
	testing::MockServer server;
	std::unique_ptr<HttpClient> http;
	std::unique_ptr<PluginApiClient> api;
	std::unique_ptr<TokenStore> tokens;
	std::string root;
	std::unique_ptr<TransferStore> store;
	std::string deviceId;

	Harness () : server (platform.Handler ()), http (MakeCurlHttpClient ())
	{
		platform.SetBaseUrl (server.BaseUrl ());
		const char* transcript = std::getenv ("RTX_OPENAPI_TRANSCRIPT");
		if (transcript != nullptr) platform.SetTranscriptPath (transcript);
		api.reset (new PluginApiClient (*http, server.BaseUrl ()));
		tokens = MakeMemoryTokenStore ();
		root = TransferStore::DefaultWorkDirectory () + "/test-" + RandomHex (6);
		EnsureDirectory (root);
		store.reset (new TransferStore (root + "/transfers.json"));
		store->Load ();
		deviceId = LoadOrCreateDeviceId (*tokens, server.BaseUrl ());
	}

	~Harness () { RemoveDirectory (root); }

	std::string SignIn ()
	{
		DeviceLogin login (*api, *tokens, server.BaseUrl ());
		platform.ApproveDevice ();
		const Result<StoredCredential> credential =
			login.SignIn (MakeDevice (deviceId), nullptr, {}, [] (int) {});
		RTX_CHECK (credential.IsOk ());
		return credential ? credential.Value ().accessToken : std::string ();
	}

	TransferRequest MakeRequest (const std::string& pixelSeed, const std::string& mode,
								 const std::string& viewpointId = {},
								 const std::string& frame = "keep")
	{
		const std::string directory = root + "/capture-" + RandomHex (4);
		EnsureDirectory (directory);
		const std::string file = directory + "/viewport.png";

		std::string png ("\x89PNG\r\n\x1a\n", 8);
		const unsigned char ihdr[] = {0, 0, 0, 13, 'I', 'H', 'D', 'R', 0, 0, 0, 4,
									  0, 0, 0, 3,  8,   6,   0,   0,   0, 0, 0, 0, 0};
		png.append (reinterpret_cast<const char*> (ihdr), sizeof ihdr);
		png.append (pixelSeed);
		WriteTextFile (file, png);

		CaptureManifest manifest;
		manifest.captureId = NewUuidV7 ();
		manifest.createdAt = NowTimestampUtc ();
		manifest.source.hostKey = "archicad";
		manifest.source.hostVersion = "28.1";
		manifest.source.pluginIdentifier = kPluginClientId;
		manifest.source.pluginVersion = RTX_TEST_PLUGIN_VERSION;
		manifest.source.architecture = "arm64";
		manifest.sourceProjectKey = "archicad:project:test";
		manifest.sourceViewKey = "archicad:view:3d";
		manifest.platformProjectId = kProjectId;

		CaptureAsset asset;
		asset.role = "viewport";
		asset.path = "viewport.png";
		asset.status = "present";
		asset.mediaType = "image/png";
		asset.byteSize = static_cast<std::int64_t> (png.size ());
		asset.sha256 = Sha256::OfString (png);
		asset.localPath = file;
		asset.hasImage = true;
		asset.image.width = 4;
		asset.image.height = 3;
		asset.image.colorSpace = "srgb";
		asset.image.bitDepth = 8;
		asset.image.sampleFormat = "uint";
		asset.image.channels = "rgba";
		manifest.assets.push_back (asset);

		TransferRequest request;
		request.manifest = manifest;
		request.directory = directory;
		request.serverUrl = server.BaseUrl ();
		request.sourceProjectKey = manifest.sourceProjectKey;
		request.sourceViewKey = manifest.sourceViewKey;
		request.target.projectId = kProjectId;
		request.target.viewpoint.present = true;
		request.target.viewpoint.mode = mode;
		request.target.viewpoint.name = "Nordansicht";
		request.target.viewpoint.viewpointId = viewpointId;
		request.target.viewpoint.baseImageRole = "viewport";
		request.target.viewpoint.frame = frame;
		return request;
	}

	/**
	 * Hängt die Modelldatei (ein Würfel, eine Kamera) an — wie der Modellweg im Add-on
	 * (RTX-A-012): Fassung aus dem Handshake, `geometry`, Kamerablock mit Bildgröße.
	 */
	std::string AddModel (TransferRequest& request, bool keepImage, const std::string& version)
	{
		GlbSceneBuilder builder;
		builder.BeginElement ("0F0A1B2C-0000-4000-8000-00000000000A");
		const std::uint32_t m = builder.Material (1, "Grau", 0.5, 0.5, 0.5, 0);
		builder.AddConvexPolygon (m, {{0, 0, 0}, {1, 0, 0}, {1, 1, 0}}, {}, {0, 0, 1});
		ArchicadProjection view;
		view.eye[0] = 0.5;
		view.eye[1] = -5;
		view.eye[2] = 1.6;
		view.target[0] = 0.5;
		view.target[1] = 0.5;
		view.target[2] = 0;
		view.hSize = 1600;
		view.vSize = 900;
		SceneBox box;
		box.known = true;
		box.max[0] = box.max[1] = 1;
		const Result<MappedCamera> camera =
			MapArchicadCamera (view, box, "Aktuelle Ansicht", "current", 0, 0, ContractMinor (version) >= 6);
		RTX_CHECK (camera.IsOk ());
		builder.Scene ().cameras.push_back (camera.Value ().gltf);
		const Result<GlbFile> file = FinishGlb (builder.Scene (), 0);
		RTX_CHECK (file.IsOk ());
		const std::string bytes (file.Value ().bytes.begin (), file.Value ().bytes.end ());
		EnsureDirectory (request.directory + "/model");
		const std::string local = request.directory + "/model/scene.glb";
		WriteTextFile (local, bytes);

		CaptureManifest& manifest = request.manifest;
		manifest.contractVersion = version;
		if (!keepImage) manifest.assets.clear ();
		CaptureAsset model;
		model.role = kModelRole;
		model.path = "model/scene.glb";
		model.mediaType = kModelMediaType;
		model.byteSize = static_cast<std::int64_t> (bytes.size ());
		model.sha256 = Sha256::OfString (bytes);
		model.localPath = local;
		manifest.assets.push_back (model);
		manifest.hasGeometry = true;
		manifest.geometry.assetPath = model.path;
		manifest.hasCamera = true;
		manifest.camera = camera.Value ().manifest;
		return model.sha256;
	}
};

} // namespace

// --- V-01: Der Server bewertet die Vertragsfassung ---------------------------

RTX_TEST (HandshakeWirdVomServerBewertetUndNichtVomClientGeraten)
{
	Harness harness;
	const Result<HandshakeInfo> handshake =
		harness.api->Handshake (MakeDevice (harness.deviceId), nullptr);
	RTX_CHECK (handshake.IsOk ());
	RTX_CHECK_EQ (handshake.Value ().profileVersion, std::string ("1.0.0"));
	RTX_CHECK (handshake.Value ().negotiationPresent);
	RTX_CHECK_EQ (handshake.Value ().negotiationResult, std::string ("supported"));
	// Der erste Durchgang meldete hier **immer** unsupported_contract_major.
	RTX_CHECK (handshake.Value ().RequireCaptureContract ().IsOk ());
	RTX_CHECK (!handshake.Value ().BlocksTransfer ());

	// Die Grenzen kommen vollständig vom Server (V-19).
	RTX_CHECK_EQ (handshake.Value ().limits.maxManifestBytes, std::int64_t (1048576));
	RTX_CHECK_EQ (handshake.Value ().limits.captureTtlSeconds, 86400);
	RTX_CHECK_EQ (handshake.Value ().contracts.size (), std::size_t (2));
	RTX_CHECK_EQ (handshake.Value ().contracts[1].availability, std::string ("planned"));
}

// --- RTX-P-015: die Canvas-Vorgabe kommt aus dem Handshake -------------------

RTX_TEST (CanvasVorgabeKommtAusDemHandshakeOderFehlt)
{
	// Ein Server vor 1.7.0 nennt sie nicht: alles andere wie bisher.
	{
		Harness harness;
		const Result<HandshakeInfo> handshake =
			harness.api->Handshake (MakeDevice (harness.deviceId), nullptr);
		RTX_CHECK (handshake.IsOk ());
		RTX_CHECK (!handshake.Value ().canvasDefault.known);
		RTX_CHECK (handshake.Value ().RequireCaptureContract ().IsOk ());
	}
	// Ab 1.7.0 nennt er sie — das Add-on übernimmt sie unverändert.
	{
		Harness harness;
		harness.platform.SetCanvasDefault ("3:2", 2048);
		const Result<HandshakeInfo> handshake =
			harness.api->Handshake (MakeDevice (harness.deviceId), nullptr);
		RTX_CHECK (handshake.IsOk ());
		RTX_CHECK (handshake.Value ().canvasDefault.known);
		RTX_CHECK_EQ (handshake.Value ().canvasDefault.aspectRatio, std::string ("3:2"));
		RTX_CHECK_EQ (handshake.Value ().canvasDefault.longEdgePx, 2048);
	}
}

RTX_TEST (UnbrauchbareCanvasVorgabeIstUnbekannt)
{
	// Regel 3: ein unlesbares Feld verhindert nichts — es ist nur keine Zahl.
	for (const char* raw :
		 {"null", "[]", "{}", R"({"aspectRatio":"3:2"})", R"({"longEdgePx":1536})",
		  R"({"aspectRatio":"breit","longEdgePx":1536})", R"({"aspectRatio":"3:2","longEdgePx":"1536"})",
		  R"({"aspectRatio":"3:2","longEdgePx":0})", R"({"aspectRatio":"3:2","longEdgePx":1536.5})",
		  R"({"aspectRatio":"0:2","longEdgePx":1536})", R"({"aspectRatio":"3:2:1","longEdgePx":1536})"}) {
		RTX_CHECK (!ParseCanvasDefault (Json::Parse (raw)).known);
	}
	RTX_CHECK (!ParseCanvasDefault (nullptr).known);
	const CanvasDefault good =
		ParseCanvasDefault (Json::Parse (R"({"aspectRatio":"21:9","longEdgePx":4096})"));
	RTX_CHECK (good.known);
	RTX_CHECK_EQ (good.aspectRatio, std::string ("21:9"));
	RTX_CHECK_EQ (good.longEdgePx, 4096);
}

// --- F-02: Ein fehlerhafter Handshake beendet Archicad nicht -----------------

RTX_TEST (FehlerhafterHandshakeBeendetDenProzessNicht)
{
	// Der erste Durchgang parste die Fassung selbst mit `std::stoi`. Eine
	// unerwartete Zeichenkette warf dort eine Ausnahme, die niemand fing — in
	// einem Add-on beendet das den Archicad-Prozess. Heute wird nichts mehr
	// geparst, und jeder Sonderfall ist ein Fehlerwert.
	HandshakeInfo ohneAushandlung;
	const Status keine = ohneAushandlung.RequireCaptureContract ();
	RTX_CHECK (!keine);
	RTX_CHECK_EQ (keine.GetError ().code, std::string (errc::Transport));

	HandshakeInfo unsinn;
	unsinn.negotiationPresent = true;
	unsinn.negotiationResult = "v1.2.3-beta+unsinn";
	RTX_CHECK (!unsinn.RequireCaptureContract ());

	HandshakeInfo zuAlt;
	zuAlt.negotiationPresent = true;
	zuAlt.negotiationResult = "unsupported_contract_minor";
	zuAlt.highestSupportedVersion = "1.0.0";
	const Status minor = zuAlt.RequireCaptureContract ();
	RTX_CHECK (!minor);
	RTX_CHECK_EQ (minor.GetError ().code, std::string (errc::UnsupportedContractMinor));

	HandshakeInfo blockiert;
	blockiert.updateStatus = "update_required";
	RTX_CHECK (blockiert.BlocksTransfer ());
}

// --- F-01: Kein Bearer über unverschlüsselte Verbindungen --------------------

RTX_TEST (BearerTokenNurUeberHttpsOderDieSchleife)
{
	RTX_CHECK (PluginApiClient::IsTokenSafeBaseUrl ("https://dev.rendertaxi.ai"));
	RTX_CHECK (PluginApiClient::IsTokenSafeBaseUrl ("HTTPS://DEV.RENDERTAXI.AI"));
	RTX_CHECK (PluginApiClient::IsTokenSafeBaseUrl ("http://127.0.0.1:8787"));
	RTX_CHECK (PluginApiClient::IsTokenSafeBaseUrl ("http://localhost:8787"));
	RTX_CHECK (PluginApiClient::IsTokenSafeBaseUrl ("http://[::1]:8787"));

	RTX_CHECK (!PluginApiClient::IsTokenSafeBaseUrl ("http://dev.rendertaxi.ai"));
	RTX_CHECK (!PluginApiClient::IsTokenSafeBaseUrl ("http://192.168.1.10:8787"));
	// Kein Durchschlüpfen über einen Namen, der mit der Schleife beginnt.
	RTX_CHECK (!PluginApiClient::IsTokenSafeBaseUrl ("http://127.0.0.1.example.test"));
	RTX_CHECK (!PluginApiClient::IsTokenSafeBaseUrl ("http://localhost.example.test"));
	RTX_CHECK (!PluginApiClient::IsTokenSafeBaseUrl ("ftp://example.test"));

	// **Die Benutzerangabe.** Hier ist die erste Fassung durchgefallen: sie
	// schnitt vor dem ersten `:` ab und las „localhost" als Host, obwohl der
	// Host `evil.example` heißt (F-01, dritte Nachprüfung). Der Parser des
	// Systems liest die Autorität nach RFC 3986, und eine Benutzerangabe wird
	// grundsätzlich abgelehnt.
	RTX_CHECK (!PluginApiClient::IsTokenSafeBaseUrl ("http://localhost:80@evil.example/"));
	RTX_CHECK (!PluginApiClient::IsTokenSafeBaseUrl ("http://127.0.0.1@evil.example/"));
	RTX_CHECK (!PluginApiClient::IsTokenSafeBaseUrl ("http://[::1]@evil.example/"));
	RTX_CHECK (!PluginApiClient::IsTokenSafeBaseUrl ("http://user@localhost:8787"));
	RTX_CHECK (!PluginApiClient::IsTokenSafeBaseUrl ("http://user:pass@127.0.0.1:8787"));
	// Auch über https: ein Geheimnis in der Adresszeile bleibt ein Geheimnis
	// in der Adresszeile.
	RTX_CHECK (!PluginApiClient::IsTokenSafeBaseUrl ("https://user:pass@dev.rendertaxi.ai"));
	RTX_CHECK (!PluginApiClient::IsTokenSafeBaseUrl ("https://user@dev.rendertaxi.ai"));
	// Und was kein Systemparser annimmt, bekommt auch kein Token.
	RTX_CHECK (!PluginApiClient::IsTokenSafeBaseUrl ("https://"));
	RTX_CHECK (!PluginApiClient::IsTokenSafeBaseUrl ("dev.rendertaxi.ai"));
	RTX_CHECK (!PluginApiClient::IsTokenSafeBaseUrl (" https://dev.rendertaxi.ai"));
	RTX_CHECK (!PluginApiClient::IsTokenSafeBaseUrl (""));

	// Und der Client sendet dann wirklich nichts.
	std::unique_ptr<HttpClient> http = MakeCurlHttpClient ();
	PluginApiClient unsicher (*http, "http://dev.rendertaxi.ai");
	unsicher.SetAccessToken ("geheim");
	const Result<AccountInfo> blocked = unsicher.Me (nullptr);
	RTX_CHECK (!blocked);
	RTX_CHECK_EQ (blocked.GetError ().code, std::string (errc::InsecureTransport));
}

/**
 * Ein HTTP-Port, der jeden Versuch aufschreibt und **nie** sendet. Damit lässt
 * sich prüfen, was den Prozess verlassen *hätte* — und nicht nur, welchen
 * Fehler der Client zurückgibt.
 */
class ObservingHttpClient final : public HttpClient {
public:
	std::vector<HttpRequest> attempts;

	Result<HttpResponse> Send (const HttpRequest& request, CancelToken*,
							   const HttpProgress&) override
	{
		attempts.push_back (request);
		return Result<HttpResponse>::Fail (errc::Transport,
										   "Dieser Port sendet nichts; er zeichnet nur auf.");
	}

	/** Steht das Geheimnis irgendwo in einem Versuch — Kopfzeile oder Körper? */
	bool Leaked (const std::string& secret) const
	{
		for (const HttpRequest& request : attempts) {
			if (request.body.find (secret) != std::string::npos) return true;
			if (request.url.find (secret) != std::string::npos) return true;
			for (const auto& header : request.headers) {
				if (header.second.find (secret) != std::string::npos) return true;
			}
		}
		return false;
	}
};

RTX_TEST (AbgelehnterTransportSendetWederKopfzeileNochKoerper)
{
	// F-01, zweiter Anlauf. Der erste prüfte nur den Weg über die
	// `Authorization`-Kopfzeile. `auth/revoke` trägt das Token nach RFC 7009
	// im **Körper** und leert `accessToken` vorher — der Widerruf ging damit
	// im Klartext an jeden externen Host. Dieser Test belegt beide Wege.
	const std::string geheim = "rtx_geheimes_token_0815";

	ObservingHttpClient http;
	PluginApiClient unsicher (http, "http://dev.rendertaxi.ai");

	// 1. Über die Kopfzeile.
	unsicher.SetAccessToken (geheim);
	const Result<AccountInfo> me = unsicher.Me (nullptr);
	RTX_CHECK (!me);
	RTX_CHECK_EQ (me.GetError ().code, std::string (errc::InsecureTransport));

	// 2. Über den Körper.
	const Status widerruf = unsicher.RevokeToken (geheim, nullptr);
	RTX_CHECK (!widerruf.IsOk ());
	RTX_CHECK_EQ (widerruf.GetError ().code, std::string (errc::InsecureTransport));

	// **Der Beleg:** `HttpClient::Send` ist kein einziges Mal aufgerufen
	// worden. Es gibt keinen Versuch, in dem das Geheimnis stehen könnte.
	RTX_CHECK_EQ (http.attempts.size (), static_cast<std::size_t> (0));
	RTX_CHECK (!http.Leaked (geheim));

	// Gegenprobe: über https läuft derselbe Aufruf bis zum Port durch — der
	// Test prüft eine Transportregel, keine tote Stelle.
	ObservingHttpClient sicherer;
	PluginApiClient sicher (sicherer, "https://dev.rendertaxi.ai");
	sicher.SetAccessToken (geheim);
	RTX_CHECK (!sicher.Me (nullptr));
	const Status erlaubt = sicher.RevokeToken (geheim, nullptr);
	RTX_CHECK (!erlaubt.IsOk ());
	RTX_CHECK_EQ (erlaubt.GetError ().code, std::string (errc::Transport));
	RTX_CHECK_EQ (sicherer.attempts.size (), static_cast<std::size_t> (2));
	RTX_CHECK (sicherer.Leaked (geheim));
	// Und der Widerruf trägt das Token dort tatsächlich im Körper.
	RTX_CHECK (sicherer.attempts[1].body.find (geheim) != std::string::npos);
}

// --- V-02 bis V-07: Gerätefluss ---------------------------------------------

RTX_TEST (GeraeteloginSendetClientIdUndGeraet)
{
	Harness harness;
	DeviceLogin login (*harness.api, *harness.tokens, harness.server.BaseUrl ());

	std::string shownCode;
	int waits = 0;
	const Result<StoredCredential> credential = login.SignIn (
		MakeDevice (harness.deviceId), nullptr,
		[&] (const DeviceLoginPrompt& prompt) { shownCode = prompt.userCode; },
		[&] (int) {
			++waits;
			if (waits == 3) harness.platform.ApproveDevice ();
		});
	RTX_CHECK (credential.IsOk ());
	RTX_CHECK_EQ (shownCode, std::string ("WDJB-MJHT"));
	// V-04: `authorization_pending` steht in `details.reason`; die Schleife
	// bricht dort **nicht** ab.
	RTX_CHECK_EQ (harness.platform.PendingAuthorizationPolls (), 2);
	// V-06: die Angaben stehen geschachtelt unter `user` und `organization`.
	RTX_CHECK_EQ (credential.Value ().displayName, std::string ("Testkonto"));
	RTX_CHECK_EQ (credential.Value ().organizationName, std::string ("Testorganisation"));
	RTX_CHECK (credential.Value ().deviceId == harness.deviceId);
}

RTX_TEST (SlowDownErhoehtDenAbstandDauerhaft)
{
	// V-05: **dauerhaft** um den genannten Wert, nicht um eine Sekunde.
	Harness harness;
	DeviceLogin login (*harness.api, *harness.tokens, harness.server.BaseUrl ());
	harness.platform.SlowDownNext (2);
	harness.platform.SetAutoApprove (true);

	std::vector<int> abstaende;
	const Result<StoredCredential> credential =
		login.SignIn (MakeDevice (harness.deviceId), nullptr, {},
					  [&] (int seconds) { abstaende.push_back (seconds); });
	RTX_CHECK (credential.IsOk ());
	RTX_CHECK (abstaende.size () >= 3);
	RTX_CHECK_EQ (abstaende[0], 1);    // `interval` des Servers
	RTX_CHECK_EQ (abstaende[1], 10);   // `details.interval` aus der ersten Ablehnung
	RTX_CHECK_EQ (abstaende[2], 10);   // bleibt erhöht
}

RTX_TEST (RatenbegrenzungVerdoppeltDenAbstandUndGibtNichtAuf)
{
	// V-18: `429` wurde vorher zu `transport_failed` und beendete die
	// Warteschleife. Jetzt trägt der Fehler `Retry-After`, und die Schleife
	// verdoppelt den Abstand und fragt weiter (RFC 8628, Abschnitt 3.5).
	Harness harness;
	DeviceLogin login (*harness.api, *harness.tokens, harness.server.BaseUrl ());
	harness.platform.RateLimitNext (2);
	harness.platform.SetAutoApprove (true);

	std::vector<int> abstaende;
	const Result<StoredCredential> credential =
		login.SignIn (MakeDevice (harness.deviceId), nullptr, {},
					  [&] (int seconds) { abstaende.push_back (seconds); });
	RTX_CHECK (credential.IsOk ());
	RTX_CHECK (abstaende.size () >= 3);
	RTX_CHECK_EQ (abstaende[0], 1);
	RTX_CHECK_EQ (abstaende[1], 2);
	RTX_CHECK_EQ (abstaende[2], 4);
}

RTX_TEST (AbgelehnteAnmeldungHoertAuf)
{
	Harness harness;
	DeviceLogin login (*harness.api, *harness.tokens, harness.server.BaseUrl ());
	harness.platform.DenyDevice ();
	const Result<StoredCredential> credential =
		login.SignIn (MakeDevice (harness.deviceId), nullptr, {}, [] (int) {});
	RTX_CHECK (!credential);
	RTX_CHECK_EQ (credential.GetError ().code, std::string (errc::AccessDenied));
}

RTX_TEST (AbmeldenWiderruftDasTokenServerseitig)
{
	// V-07: der erste Durchgang sendete `{}` mit Bearer; der Server antwortete
	// `400`, und das Token blieb gültig.
	Harness harness;
	const std::string token = harness.SignIn ();
	RTX_CHECK (harness.platform.IsTokenValid (token));

	DeviceLogin login (*harness.api, *harness.tokens, harness.server.BaseUrl ());
	RTX_CHECK (login.SignOut (nullptr).IsOk ());
	RTX_CHECK_EQ (harness.platform.RevocationCount (), 1);
	RTX_CHECK (!harness.platform.IsTokenValid (token));
	RTX_CHECK (!login.Restore ());

	// Die Gerätekennung überlebt das Abmelden (§5.5).
	RTX_CHECK_EQ (LoadOrCreateDeviceId (*harness.tokens, harness.server.BaseUrl ()),
				  harness.deviceId);
}

// --- V-08 bis V-16: Übernahme ------------------------------------------------

RTX_TEST (UebernahmeLegtGenauEinAssetUndEinenBlickpunktAn)
{
	Harness harness;
	harness.SignIn ();
	CaptureTransfer transfer (*harness.api, *harness.store);

	const TransferRequest request = harness.MakeRequest ("bild-eins", "create");
	const Result<CaptureResult> result = transfer.Run (request, nullptr, {});
	RTX_CHECK (result.IsOk ());
	RTX_CHECK_EQ (harness.platform.AssetsCreated (), 1);
	RTX_CHECK_EQ (harness.platform.ViewpointsCreated (), 1);
	RTX_CHECK_EQ (result.Value ().kind, std::string ("platform-assets"));
	RTX_CHECK (!result.Value ().openUrl.empty ());
	RTX_CHECK (!result.Value ().viewpointId.empty ());
	RTX_CHECK (harness.store->FindPending (request.sourceProjectKey, request.sourceViewKey).IsEmpty ());

	const LastAssignment assignment =
		harness.store->FindAssignment (request.sourceProjectKey, request.sourceViewKey);
	RTX_CHECK_EQ (assignment.projectId, std::string (kProjectId));
	RTX_CHECK_EQ (assignment.viewpointId, result.Value ().viewpointId);
}

RTX_TEST (DerIdempotenzschluesselIstEineUuidV7)
{
	// V-08: `archicad-capture-<id>` wies der Server mit
	// `invalid_idempotency_key` ab.
	Harness harness;
	harness.SignIn ();
	CaptureTransfer transfer (*harness.api, *harness.store);
	const TransferRequest request = harness.MakeRequest ("bild-zwei", "create");

	harness.platform.FailNext ("/api/v1/plugin/captures", 1);
	RTX_CHECK (!transfer.Run (request, nullptr, {}));

	const PendingTransfer pending =
		harness.store->FindPending (request.sourceProjectKey, request.sourceViewKey);
	RTX_CHECK (!pending.IsEmpty ());
	RTX_CHECK (IsUuidV7 (pending.idempotencyKey));
	// Und er steht vor dem ersten Netzaufruf in der Ablage (§7.1, Schritt 5).
	RTX_CHECK (!pending.manifestSha256.empty ());
}

RTX_TEST (AbbruchAnJederStelleUndWiederholungErzeugtNichtsDoppelt)
{
	// Die Stufen aus §7.1: Anlage, Dateiübertragung, Manifest, Finalisierung.
	// Die `captureId` vergibt der Server, deshalb greift der Ausfall über ein
	// Pfadmuster.
	const char* stages[] = {"/api/v1/plugin/captures", "/files", "/manifest", "/finalize"};
	for (const char* stage : stages) {
		Harness harness;
		harness.SignIn ();
		CaptureTransfer transfer (*harness.api, *harness.store);
		const TransferRequest request = harness.MakeRequest ("bild-drei", "create");

		if (std::string (stage) == "/api/v1/plugin/captures")
			harness.platform.FailNext (stage, 1);
		else
			harness.platform.FailNextMatching (stage, 1);

		const Result<CaptureResult> broken = transfer.Run (request, nullptr, {});
		RTX_CHECK (!broken);
		// Der angefangene Vorgang bleibt mit seinem Schlüssel stehen.
		const PendingTransfer pending =
			harness.store->FindPending (request.sourceProjectKey, request.sourceViewKey);
		RTX_CHECK (!pending.IsEmpty ());
		RTX_CHECK (IsUuidV7 (pending.idempotencyKey));

		const Result<CaptureResult> retried = transfer.Run (request, nullptr, {});
		RTX_CHECK (retried.IsOk ());
		RTX_CHECK_EQ (harness.platform.AssetsCreated (), 1);
		RTX_CHECK_EQ (harness.platform.ViewpointsCreated (), 1);
		RTX_CHECK_EQ (harness.platform.BaseImageVersions (), 1);
	}
}

RTX_TEST (AbbruchNachDemAnlegenWirdFortgesetzt)
{
	Harness harness;
	harness.SignIn ();
	CaptureTransfer transfer (*harness.api, *harness.store);
	const TransferRequest request = harness.MakeRequest ("bild-vier", "create");

	// Die Anlage gelingt, die Dateiübertragung scheitert einmal.
	const std::string sha = request.manifest.assets[0].sha256;
	harness.platform.FailNext ("/api/v1/plugin/captures", 0);
	CancelToken cancel;
	// Ein Abbruch vor dem ersten `files`-Aufruf.
	cancel.Cancel ();
	const Result<CaptureResult> cancelled = transfer.Run (request, &cancel, {});
	RTX_CHECK (!cancelled);
	RTX_CHECK_EQ (cancelled.GetError ().code, std::string (errc::Cancelled));

	cancel.Reset ();
	const Result<CaptureResult> result = transfer.Run (request, &cancel, {});
	RTX_CHECK (result.IsOk ());
	RTX_CHECK_EQ (harness.platform.AssetsCreated (), 1);
	RTX_CHECK_EQ (harness.platform.ViewpointsCreated (), 1);
	RTX_CHECK (!sha.empty ());
}

RTX_TEST (AbgelehnteDateiWirdMitDemNaechstenVersuchNeuUebertragen)
{
	// V-16: der erste Durchgang beendete den Vorgang bei `rejected`.
	Harness harness;
	harness.SignIn ();
	CaptureTransfer transfer (*harness.api, *harness.store);
	const TransferRequest request = harness.MakeRequest ("bild-fuenf", "create");

	harness.platform.RejectNextFile ();
	const Result<CaptureResult> result = transfer.Run (request, nullptr, {});
	RTX_CHECK (result.IsOk ());
	RTX_CHECK_EQ (harness.platform.AssetsCreated (), 1);
	RTX_CHECK_EQ (harness.platform.ViewpointsCreated (), 1);
}

RTX_TEST (AbgelaufeneSessionFuehrtZuNeuemSchluessel)
{
	Harness harness;
	harness.SignIn ();
	CaptureTransfer transfer (*harness.api, *harness.store);
	const TransferRequest request = harness.MakeRequest ("bild-sechs", "create");

	harness.platform.FailNext ("/api/v1/plugin/captures", 1);
	RTX_CHECK (!transfer.Run (request, nullptr, {}));
	const std::string firstKey =
		harness.store->FindPending (request.sourceProjectKey, request.sourceViewKey).idempotencyKey;

	harness.platform.ExpireSessions ();
	const Result<CaptureResult> result = transfer.Run (request, nullptr, {});
	RTX_CHECK (result.IsOk ());
	const std::string secondKey = firstKey;
	RTX_CHECK (IsUuidV7 (secondKey));
	RTX_CHECK_EQ (harness.platform.AssetsCreated (), 1);
	RTX_CHECK_EQ (harness.platform.ViewpointsCreated (), 1);
}

RTX_TEST (DauerhafterAblaufBlockiertDenCaptureNicht)
{
	// F-03: ein Server, der jede Session sofort ablaufen lässt, blockierte den
	// Capture dauerhaft — der Client rotierte den Schlüssel endlos.
	Harness harness;
	harness.SignIn ();
	CaptureTransfer transfer (*harness.api, *harness.store);
	const TransferRequest request = harness.MakeRequest ("bild-sieben", "create");

	harness.platform.SetAlwaysExpire (true);
	const Result<CaptureResult> result = transfer.Run (request, nullptr, {});
	RTX_CHECK (!result);
	RTX_CHECK_EQ (result.GetError ().code, std::string (errc::CaptureExpired));
	// Der Vorgang bleibt lesbar und blockiert nichts: er lässt sich verwerfen.
	const PendingTransfer pending =
		harness.store->FindPending (request.sourceProjectKey, request.sourceViewKey);
	RTX_CHECK (!pending.IsEmpty ());
	RTX_CHECK (transfer.Discard (pending, nullptr).IsOk ());

	// Verwerfen räumt das Verzeichnis; die nächste Übernahme nimmt neu auf.
	harness.platform.SetAlwaysExpire (false);
	const TransferRequest frisch = harness.MakeRequest ("bild-sieben-neu", "create");
	const Result<CaptureResult> danach = transfer.Run (frisch, nullptr, {});
	RTX_CHECK (danach.IsOk ());
}

RTX_TEST (GleicherSchluesselMitAnderemInhaltWirdAbgelehnt)
{
	Harness harness;
	harness.SignIn ();
	CaptureTransfer transfer (*harness.api, *harness.store);

	const TransferRequest first = harness.MakeRequest ("bild-acht", "create");
	harness.platform.FailNext ("/api/v1/plugin/captures", 1);
	RTX_CHECK (!transfer.Run (first, nullptr, {}));

	const TransferRequest second = harness.MakeRequest ("bild-neun", "create");
	const Result<CaptureResult> conflict = transfer.Run (second, nullptr, {});
	RTX_CHECK (!conflict);
	RTX_CHECK_EQ (conflict.GetError ().code, std::string (errc::IdempotencyConflict));

	const PendingTransfer pending =
		harness.store->FindPending (first.sourceProjectKey, first.sourceViewKey);
	RTX_CHECK (transfer.Discard (pending, nullptr).IsOk ());
	const Result<CaptureResult> result = transfer.Run (second, nullptr, {});
	RTX_CHECK (result.IsOk ());
	RTX_CHECK_EQ (harness.platform.ViewpointsCreated (), 1);
}

RTX_TEST (AusdruecklichesUpdateErzeugtEineNeueBasisbildfassung)
{
	Harness harness;
	harness.SignIn ();
	CaptureTransfer transfer (*harness.api, *harness.store);

	const TransferRequest created = harness.MakeRequest ("bild-zehn", "create");
	const Result<CaptureResult> first = transfer.Run (created, nullptr, {});
	RTX_CHECK (first.IsOk ());

	const TransferRequest updated =
		harness.MakeRequest ("bild-elf", "update", first.Value ().viewpointId);
	const Result<CaptureResult> second = transfer.Run (updated, nullptr, {});
	RTX_CHECK (second.IsOk ());
	RTX_CHECK_EQ (harness.platform.AssetsCreated (), 2);
	RTX_CHECK_EQ (harness.platform.BaseImageVersions (), 2);
	// Ein Update legt **keinen** zweiten Blickpunkt an.
	RTX_CHECK_EQ (harness.platform.ViewpointsCreated (), 1);
	RTX_CHECK_EQ (second.Value ().viewpointId, first.Value ().viewpointId);
}

// --- Formatanteil: `target.viewpoint.frame` ----------------------------------

RTX_TEST (RahmenAnAufnahmeAnpassenIstEineAusdrueckicheAktion)
{
	// §7.2: `frame` gehört zum Vorgangsschlüssel. Ein Update sendet den Wert
	// deshalb **immer** ausdrücklich — auch den Vorgabewert `keep`. Was der
	// Server annimmt, steht dann im Aufruf und nicht in seiner Vorgabe.
	CaptureTarget behalten;
	behalten.projectId = kProjectId;
	behalten.viewpoint.present = true;
	behalten.viewpoint.mode = "update";
	behalten.viewpoint.viewpointId = kViewpointId;
	RTX_CHECK (behalten.viewpoint.SendsFrame ());
	RTX_CHECK_EQ (behalten.viewpoint.EffectiveFrame (), std::string ("keep"));

	CreateCaptureRequest request;
	request.contract = kCaptureContract;
	request.contractVersion = kCaptureContractVersion;
	request.manifestSha256 = std::string (64, 'a');
	request.target = behalten;
	CaptureAsset asset;
	asset.role = "viewport";
	asset.path = "viewport.png";
	asset.status = "present";
	asset.mediaType = "image/png";
	asset.byteSize = 10;
	asset.sha256 = std::string (64, 'b');
	request.files.push_back (asset);
	RTX_CHECK (CreateCaptureRequestToJson (request)->Serialize ().find (
				   "\"frame\":\"keep\"") != std::string::npos);

	CaptureTarget anpassen = behalten;
	anpassen.viewpoint.frame = "fit-to-capture";
	RTX_CHECK (anpassen.viewpoint.SendsFrame ());
	request.target = anpassen;
	const std::string json = CreateCaptureRequestToJson (request)->Serialize ();
	RTX_CHECK (json.find ("\"frame\":\"fit-to-capture\"") != std::string::npos);

	// Bei `create` ist `fit-to-capture` implizit und `keep` ein `400` (§7.2).
	// Der Client sendet das Feld dort **nie** — und kann den Fehler damit
	// nicht auslösen, was immer im Feld steht.
	CaptureTarget neu = anpassen;
	neu.viewpoint.mode = "create";
	neu.viewpoint.name = "Nordansicht";
	RTX_CHECK (!neu.viewpoint.SendsFrame ());
	RTX_CHECK_EQ (neu.viewpoint.EffectiveFrame (), std::string ("fit-to-capture"));
	neu.viewpoint.frame = "keep";
	RTX_CHECK (!neu.viewpoint.SendsFrame ());
	RTX_CHECK_EQ (neu.viewpoint.EffectiveFrame (), std::string ("fit-to-capture"));
	request.target = neu;
	RTX_CHECK (CreateCaptureRequestToJson (request)->Serialize ().find ("frame") ==
			   std::string::npos);
}

RTX_TEST (RahmengroesseReistNurMitWoSieWirkt)
{
	// §7.2, RTX-P-010. `size` ist eine Einstellung des Plugins und wirkt nur,
	// wo der Rahmen angepasst wird: bei `create` immer, bei `update` nur mit
	// `frame: "fit-to-capture"`. Neben `keep` antwortet der Server `400`; der
	// Client sendet es dort deshalb nicht — und übergeht die Einstellung
	// trotzdem nicht still, weil die Palette sagt, was gilt.
	CreateCaptureRequest request;
	request.contract = kCaptureContract;
	request.contractVersion = kCaptureContractVersion;
	request.manifestSha256 = std::string (64, 'a');
	CaptureAsset asset;
	asset.role = "viewport";
	asset.path = "viewport.png";
	asset.status = "present";
	asset.mediaType = "image/png";
	asset.byteSize = 10;
	asset.sha256 = std::string (64, 'b');
	request.files.push_back (asset);

	// 1. Die Vorgabe reist nie mit: sie ist die Vorgabe des Servers.
	CaptureTarget vorgabe;
	vorgabe.projectId = kProjectId;
	vorgabe.viewpoint.present = true;
	vorgabe.viewpoint.mode = "create";
	vorgabe.viewpoint.name = "Nordansicht";
	RTX_CHECK (!vorgabe.viewpoint.SendsSize ());
	request.target = vorgabe;
	RTX_CHECK (CreateCaptureRequestToJson (request)->Serialize ().find ("size") ==
			   std::string::npos);

	// 2. Bei `create` wirkt sie immer — dort ist `fit-to-capture` implizit.
	CaptureTarget anlageMitAufnahmegroesse = vorgabe;
	anlageMitAufnahmegroesse.viewpoint.size = "capture";
	RTX_CHECK (anlageMitAufnahmegroesse.viewpoint.SendsSize ());
	request.target = anlageMitAufnahmegroesse;
	RTX_CHECK (CreateCaptureRequestToJson (request)->Serialize ().find (
				   "\"size\":\"capture\"") != std::string::npos);

	// 3. Bei `update` mit `keep` wirkt sie **nicht** und reist nicht mit.
	CaptureTarget updateBehalten;
	updateBehalten.projectId = kProjectId;
	updateBehalten.viewpoint.present = true;
	updateBehalten.viewpoint.mode = "update";
	updateBehalten.viewpoint.viewpointId = kViewpointId;
	updateBehalten.viewpoint.size = "capture";
	RTX_CHECK (!updateBehalten.viewpoint.SendsSize ());
	request.target = updateBehalten;
	RTX_CHECK (CreateCaptureRequestToJson (request)->Serialize ().find ("size") ==
			   std::string::npos);

	// 4. Bei `update` mit `fit-to-capture` wirkt sie.
	CaptureTarget updateAngepasst = updateBehalten;
	updateAngepasst.viewpoint.frame = "fit-to-capture";
	RTX_CHECK (updateAngepasst.viewpoint.SendsSize ());
	request.target = updateAngepasst;
	const std::string json = CreateCaptureRequestToJson (request)->Serialize ();
	RTX_CHECK (json.find ("\"size\":\"capture\"") != std::string::npos);
	RTX_CHECK (json.find ("\"frame\":\"fit-to-capture\"") != std::string::npos);
}

RTX_TEST (EineAndereRahmengroesseIstEinAndererVorgang)
{
	// `size` gehört zum Ziel und damit zum Vorgangsschlüssel (§7.2).
	Harness harness;
	harness.SignIn ();
	CaptureTransfer transfer (*harness.api, *harness.store);
	const TransferRequest vorgabe = harness.MakeRequest ("groesse", "create");
	TransferRequest aufnahmegroesse = vorgabe;
	aufnahmegroesse.target.viewpoint.size = "capture";

	harness.platform.FailNextMatching ("/manifest", 1);
	RTX_CHECK (!transfer.Run (vorgabe, nullptr, {}));

	const Result<CaptureResult> konflikt = transfer.Run (aufnahmegroesse, nullptr, {});
	RTX_CHECK (!konflikt);
	RTX_CHECK_EQ (konflikt.GetError ().code, std::string (errc::IdempotencyConflict));

	// Und der Lauf **mit** der Aufnahmegröße geht über die Leitung, sobald der
	// alte Vorgang verworfen ist — so sieht ihn auch der Vertragsprüfer.
	// Verwerfen räumt das Verzeichnis; die nächste Übernahme nimmt neu auf,
	// genau wie die Palette es täte.
	const std::vector<std::string> keys {vorgabe.sourceViewKey};
	RTX_CHECK (DiscardAll (transfer, *harness.store, vorgabe.sourceProjectKey, keys, nullptr)
				   .IsOk ());
	TransferRequest neueAufnahme = harness.MakeRequest ("groesse-neu", "create");
	neueAufnahme.target.viewpoint.size = "capture";
	RTX_CHECK (transfer.Run (neueAufnahme, nullptr, {}).IsOk ());
}

RTX_TEST (EinAnderesRahmenformatIstEinAndererVorgang)
{
	// §7.2: „`frame` gehört zum Ziel und damit zum Vorgangsschlüssel: derselbe
	// Schlüssel mit einem anderen `frame` ist `409 idempotency_conflict`."
	// Der Client setzt einen offenen Vorgang deshalb nicht stillschweigend mit
	// anderem Rahmen fort — er fragt vorher.
	Harness harness;
	harness.SignIn ();
	CaptureTransfer transfer (*harness.api, *harness.store);
	const TransferRequest behalten =
		harness.MakeRequest ("rahmen", "update", kViewpointId, "keep");
	// Dieselbe Datei, dasselbe Manifest, dasselbe Ziel — **nur** der Rahmen.
	TransferRequest anpassen = behalten;
	anpassen.target.viewpoint.frame = "fit-to-capture";

	harness.platform.FailNextMatching ("/manifest", 1);
	RTX_CHECK (!transfer.Run (behalten, nullptr, {}));

	const Result<CaptureResult> konflikt = transfer.Run (anpassen, nullptr, {});
	RTX_CHECK (!konflikt);
	RTX_CHECK_EQ (konflikt.GetError ().code, std::string (errc::IdempotencyConflict));

	// Mit demselben Rahmen läuft derselbe Vorgang weiter.
	RTX_CHECK (transfer.Run (behalten, nullptr, {}).IsOk ());
}

RTX_TEST (BeiCreateIstDasRahmenfeldKeinUnterschied)
{
	// Bei `create` ist `fit-to-capture` implizit; was im Feld steht, reist
	// nicht mit. Der Vorgangsschlüssel darf daran deshalb nicht hängen —
	// sonst meldete der Client einen Konflikt, den der Server nicht kennt.
	Harness harness;
	harness.SignIn ();
	CaptureTransfer transfer (*harness.api, *harness.store);
	const TransferRequest ersterVersuch = harness.MakeRequest ("create-rahmen", "create", {}, "keep");
	TransferRequest zweiterVersuch = ersterVersuch;
	zweiterVersuch.target.viewpoint.frame = "fit-to-capture";

	harness.platform.FailNextMatching ("/manifest", 1);
	RTX_CHECK (!transfer.Run (ersterVersuch, nullptr, {}));
	RTX_CHECK (transfer.Run (zweiterVersuch, nullptr, {}).IsOk ());
	RTX_CHECK_EQ (harness.platform.ViewpointsCreated (), 1);
}

RTX_TEST (EinAngefangenerVorgangMerktSichDieKennungSeinesManifests)
{
	// **Der Abnahmebefund vom 24.09.2026.** Die Palette baute das Manifest bei
	// jeder Übernahme neu und nahm als `captureId` die des angefangenen
	// Vorgangs — das ist aber die Kennung der **Session**, die der Server
	// vergibt (§7.6: „`captureId` darin vergibt der Server; es ist nicht die
	// `captureId` des Manifests"). Solange die Anlage nie geglückt war, war
	// sie leer, und das Manifest wurde lokal als ungültig abgewiesen:
	// „captureId ist keine UUIDv7 in Kleinschreibung."
	//
	// Der Vorgang merkt sich deshalb beides getrennt.
	Harness harness;
	harness.SignIn ();
	CaptureTransfer transfer (*harness.api, *harness.store);
	const TransferRequest request = harness.MakeRequest ("manifestkennung", "create");

	// Die Anlage schlägt fehl: es gibt einen offenen Vorgang **ohne**
	// Serverkennung — genau die Lage, in der der Fehler auftrat.
	harness.platform.FailNext ("/api/v1/plugin/captures", 1);
	RTX_CHECK (!transfer.Run (request, nullptr, {}));

	const PendingTransfer pending =
		harness.store->FindPending (request.sourceProjectKey, request.sourceViewKey);
	RTX_CHECK (!pending.IsEmpty ());
	RTX_CHECK (pending.captureId.empty ());
	RTX_CHECK_EQ (pending.manifestCaptureId, request.manifest.captureId);
	RTX_CHECK_EQ (pending.manifestCreatedAt, request.manifest.createdAt);
	RTX_CHECK (IsUuidV7 (pending.manifestCaptureId));

	// Und sie überleben einen Neustart: der Speicher liest sie wieder.
	TransferStore reloaded (harness.root + "/transfers.json");
	reloaded.Load ();
	const PendingTransfer afterRestart =
		reloaded.FindPending (request.sourceProjectKey, request.sourceViewKey);
	RTX_CHECK_EQ (afterRestart.manifestCaptureId, request.manifest.captureId);
	RTX_CHECK_EQ (afterRestart.manifestCreatedAt, request.manifest.createdAt);

	// Wiederaufnahme mit **denselben** Manifestbytes läuft durch.
	RTX_CHECK (transfer.Run (request, nullptr, {}).IsOk ());
	RTX_CHECK_EQ (harness.platform.ViewpointsCreated (), 1);
	RTX_CHECK_EQ (harness.platform.AssetsCreated (), 1);
}

RTX_TEST (VorgangOhneLokaleDateiWirdVorDerNeuenAufnahmeGeraeumt)
{
	// **Issue #88, Punkt 16.** Fehlte einem gespeicherten Vorgang die lokale
	// Bild- oder Manifestdatei, blieb der Eintrag stehen: die nächste
	// Aufnahme ergab ein anderes Manifest und scheiterte mit
	// `idempotency_conflict` am eigenen verwaisten Vorgang.
	Harness harness;
	harness.SignIn ();
	CaptureTransfer transfer (*harness.api, *harness.store);
	const TransferRequest request = harness.MakeRequest ("verwaist", "create");

	// Die Anlage gelingt, die Datei nicht: ein Vorgang mit `captureId` bleibt.
	harness.platform.FailNextMatching ("/files", 1);
	RTX_CHECK (!transfer.Run (request, nullptr, {}));
	const PendingTransfer pending =
		harness.store->FindPending (request.sourceProjectKey, request.sourceViewKey);
	RTX_CHECK (!pending.IsEmpty ());
	RTX_CHECK (!pending.captureId.empty ());

	// Solange alles da ist, ist er fortsetzbar und wird **nicht** geräumt.
	RTX_CHECK (HasLocalMaterial (pending));
	const Result<PendingTransfer> kept = ReleaseOrphanedPending (
		*harness.store, request.sourceProjectKey, request.sourceViewKey);
	RTX_CHECK (kept.IsOk ());
	RTX_CHECK (kept.Value ().IsEmpty ());
	RTX_CHECK (!harness.store->FindPending (request.sourceProjectKey, request.sourceViewKey)
					.IsEmpty ());

	// Fehlt das Manifest, ist er es nicht mehr — ebenso ohne Bilddatei.
	const std::string manifestPath = pending.directory + "/capture-manifest.json";
	std::string manifestText;
	RTX_CHECK (ReadTextFile (manifestPath, manifestText));
	RemoveFile (manifestPath);
	RTX_CHECK (!HasLocalMaterial (pending));
	RTX_CHECK (WriteTextFile (manifestPath, manifestText));
	RTX_CHECK (HasLocalMaterial (pending));
	// Ein anderes Manifest ist kein Bestand dieses Vorgangs.
	RTX_CHECK (WriteTextFile (manifestPath, manifestText + " "));
	RTX_CHECK (!HasLocalMaterial (pending));
	RTX_CHECK (WriteTextFile (manifestPath, manifestText));
	const std::string imagePath = pending.directory + "/viewport.png";
	RemoveFile (imagePath);
	RTX_CHECK (!HasLocalMaterial (pending));

	// Geräumt wird lokal, sofort und ohne Netz: Eintrag und Verzeichnis.
	const Result<PendingTransfer> released = ReleaseOrphanedPending (
		*harness.store, request.sourceProjectKey, request.sourceViewKey);
	RTX_CHECK (released.IsOk ());
	RTX_CHECK_EQ (released.Value ().idempotencyKey, pending.idempotencyKey);
	RTX_CHECK (harness.store->FindPending (request.sourceProjectKey, request.sourceViewKey)
				   .IsEmpty ());
	RTX_CHECK_EQ (FileSize (manifestPath), -1LL);
	// Auch nach einem Neustart ist er fort.
	TransferStore reloaded (harness.root + "/transfers.json");
	reloaded.Load ();
	RTX_CHECK (reloaded.FindPending (request.sourceProjectKey, request.sourceViewKey).IsEmpty ());

	// Die Session wird serverseitig abgebrochen, wie beim Verwerfen per Knopf.
	RTX_CHECK (transfer.Discard (released.Value (), nullptr).IsOk ());
	const Result<CaptureSessionState> session =
		harness.api->GetCapture (pending.captureId, nullptr);
	RTX_CHECK (session.IsOk ());
	RTX_CHECK_EQ (session.Value ().state, std::string ("aborted"));

	// Und die neue Aufnahme derselben Ansicht läuft ohne Konflikt durch —
	// mit einem **neuen** Schlüssel.
	const TransferRequest neu = harness.MakeRequest ("neu-aufgenommen", "create");
	RTX_CHECK (transfer.Run (neu, nullptr, {}).IsOk ());
	RTX_CHECK_EQ (harness.platform.ViewpointsCreated (), 1);
}

RTX_TEST (EinEintragOhneManifestkennungIstNichtFortsetzbar)
{
	// Einträge aus der Fassung vor dem 24.09.2026 führen keine
	// `manifestCaptureId`. Ihre Manifestbytes lassen sich nicht wieder
	// erzeugen; sie sind verwaist, auch wenn die Dateien noch liegen.
	Harness harness;
	harness.SignIn ();
	CaptureTransfer transfer (*harness.api, *harness.store);
	const TransferRequest request = harness.MakeRequest ("altbestand", "create");
	harness.platform.FailNextMatching ("/files", 1);
	RTX_CHECK (!transfer.Run (request, nullptr, {}));

	PendingTransfer pending =
		harness.store->FindPending (request.sourceProjectKey, request.sourceViewKey);
	RTX_CHECK (HasLocalMaterial (pending));
	pending.manifestCaptureId.clear ();
	RTX_CHECK (!HasLocalMaterial (pending));
	RTX_CHECK (!HasLocalMaterial (PendingTransfer {}));
}

RTX_TEST (VerwerfenErreichtAlleSchluesselDerAnsicht)
{
	// **F-05, dritte Nachprüfung.** Eine Ansicht trägt zwei Schlüssel: das
	// Fensterbild und das gerechnete Rendering. Das Verwerfen kannte nur den
	// ersten — ein liegengebliebener Rendering-Vorgang blockierte damit jeden
	// weiteren mit `idempotency_conflict`, ohne Weg heraus.
	Harness harness;
	harness.SignIn ();
	CaptureTransfer transfer (*harness.api, *harness.store);

	TransferRequest fenster = harness.MakeRequest ("fensterbild", "create");
	TransferRequest rendering = harness.MakeRequest ("renderbild", "create");
	// Wie in der Palette: derselbe Blickwinkel, zwei Quellen.
	rendering.sourceViewKey = fenster.sourceViewKey + ":rendering";
	rendering.manifest.sourceViewKey = rendering.sourceViewKey;

	// Beide bleiben angefangen stehen.
	harness.platform.FailNextMatching ("/manifest", 1);
	RTX_CHECK (!transfer.Run (fenster, nullptr, {}));
	harness.platform.FailNextMatching ("/manifest", 1);
	RTX_CHECK (!transfer.Run (rendering, nullptr, {}));
	RTX_CHECK (!harness.store->FindPending (fenster.sourceProjectKey, fenster.sourceViewKey)
					.IsEmpty ());
	RTX_CHECK (!harness.store->FindPending (rendering.sourceProjectKey, rendering.sourceViewKey)
					.IsEmpty ());

	// Ein Aufruf, beide Schlüssel.
	const std::vector<std::string> keys {fenster.sourceViewKey, rendering.sourceViewKey};
	RTX_CHECK (DiscardAll (transfer, *harness.store, fenster.sourceProjectKey, keys, nullptr)
				   .IsOk ());
	RTX_CHECK (harness.store->FindPending (fenster.sourceProjectKey, fenster.sourceViewKey)
				   .IsEmpty ());
	RTX_CHECK (harness.store->FindPending (rendering.sourceProjectKey, rendering.sourceViewKey)
				   .IsEmpty ());

	// Und der nächste Rendering-Vorgang läuft ohne Konflikt durch.
	const TransferRequest neu = harness.MakeRequest ("renderbild-zwei", "create");
	TransferRequest zweiterLauf = neu;
	zweiterLauf.sourceViewKey = rendering.sourceViewKey;
	zweiterLauf.manifest.sourceViewKey = rendering.sourceViewKey;
	RTX_CHECK (transfer.Run (zweiterLauf, nullptr, {}).IsOk ());
}

RTX_TEST (BaseImageRoleStehtImBlickpunktZiel)
{
	// V-11: am `target` stehend wies der Server es mit `unrecognized_keys` ab.
	CreateCaptureRequest request;
	request.contract = kCaptureContract;
	request.contractVersion = kCaptureContractVersion;
	request.manifestSha256 = std::string (64, 'a');
	request.target.projectId = kProjectId;
	request.target.viewpoint.present = true;
	request.target.viewpoint.mode = "create";
	request.target.viewpoint.name = "Nordansicht";
	request.target.viewpoint.baseImageRole = "viewport";
	CaptureAsset asset;
	asset.role = "viewport";
	asset.path = "viewport.png";
	asset.status = "present";
	asset.mediaType = "image/png";
	asset.byteSize = 10;
	asset.sha256 = std::string (64, 'b');
	request.files.push_back (asset);

	const JsonPtr json = CreateCaptureRequestToJson (request);
	const JsonPtr target = json->Get ("target");
	RTX_CHECK (target->Get ("baseImageRole") == nullptr);
	RTX_CHECK (target->Get ("viewpoint")->Get ("baseImageRole") != nullptr);
	// V-10: `manifestSha256` statt `captureId`/`contentHash`.
	RTX_CHECK (json->Get ("manifestSha256") != nullptr);
	RTX_CHECK (json->Get ("captureId") == nullptr);
	RTX_CHECK (json->Get ("contentHash") == nullptr);
	// §7.9: der Bildweg sendet keine Georeferenz.
	RTX_CHECK (json->Get ("georeference") == nullptr);
}

// --- V-17, V-18: Fehlerkörper ------------------------------------------------

RTX_TEST (WiderrufenesTokenMeldetSichVerstaendlich)
{
	Harness harness;
	harness.SignIn ();
	CaptureTransfer transfer (*harness.api, *harness.store);
	const TransferRequest request = harness.MakeRequest ("bild-zwoelf", "create");

	harness.platform.RevokeAllTokens ();
	const Result<CaptureResult> result = transfer.Run (request, nullptr, {});
	RTX_CHECK (!result);
	RTX_CHECK_EQ (result.GetError ().code, std::string (errc::Unauthorized));
	RTX_CHECK (result.GetError ().message.find ("anmelden") != std::string::npos);
	// V-17: jede Antwort trägt eine `X-Request-Id`, die der Client mitführt.
	RTX_CHECK (!harness.api->LastRequestId ().empty ());
}

RTX_TEST (GrenzenWerdenVorDemUploadGeprueft)
{
	Harness harness;
	harness.SignIn ();
	const Result<HandshakeInfo> handshake =
		harness.api->Handshake (MakeDevice (harness.deviceId), nullptr);
	RTX_CHECK (handshake.IsOk ());

	TransferRequest request = harness.MakeRequest ("bild-dreizehn", "create");
	RTX_CHECK (CaptureTransfer::CheckLimits (request.manifest, 1000, handshake.Value ().limits)
				   .IsOk ());

	request.manifest.assets[0].byteSize = 999999999999LL;
	RTX_CHECK (!CaptureTransfer::CheckLimits (request.manifest, 1000, handshake.Value ().limits));

	request.manifest.assets[0].byteSize = 10;
	request.manifest.assets[0].mediaType = "image/tiff";
	RTX_CHECK (!CaptureTransfer::CheckLimits (request.manifest, 1000, handshake.Value ().limits));

	// V-19: auch das Manifest hat eine Grenze, und sie wird geprüft.
	request.manifest.assets[0].mediaType = "image/png";
	const Status zuGross =
		CaptureTransfer::CheckLimits (request.manifest, 2000000, handshake.Value ().limits);
	RTX_CHECK (!zuGross);
	RTX_CHECK_EQ (zuGross.GetError ().code, std::string (errc::LimitExceeded));
}

RTX_TEST (ListenLiefernProjekteUndBlickpunkte)
{
	Harness harness;
	harness.SignIn ();
	const Result<std::vector<ProjectSummary>> projects = harness.api->ListProjects (nullptr);
	RTX_CHECK (projects.IsOk ());
	RTX_CHECK_EQ (projects.Value ().size (), std::size_t (1));
	RTX_CHECK_EQ (projects.Value ().front ().name, std::string ("Testprojekt"));

	const Result<std::vector<ViewpointSummary>> viewpoints =
		harness.api->ListViewpoints (kProjectId, nullptr);
	RTX_CHECK (viewpoints.IsOk ());
	RTX_CHECK_EQ (viewpoints.Value ().size (), std::size_t (2));
	RTX_CHECK_EQ (viewpoints.Value ().front ().name, std::string ("Nordansicht"));
	RTX_CHECK (viewpoints.Value ().front ().desired.known);
	RTX_CHECK_EQ (viewpoints.Value ().front ().desired.label, std::string ("16:9"));
	RTX_CHECK (!viewpoints.Value ().back ().desired.known);
}

RTX_TEST (VerwerfenBrichtDieSessionServerseitigAb)
{
	Harness harness;
	harness.SignIn ();
	CaptureTransfer transfer (*harness.api, *harness.store);
	const TransferRequest request = harness.MakeRequest ("bild-vierzehn", "create");

	// Die Anlage gelingt, die Dateiübertragung scheitert — so bleibt ein
	// Vorgang mit bekannter `captureId` stehen, den `Discard` abbrechen kann.
	harness.platform.FailNextMatching ("/files", 1);
	RTX_CHECK (!transfer.Run (request, nullptr, {}));

	const PendingTransfer pending =
		harness.store->FindPending (request.sourceProjectKey, request.sourceViewKey);
	RTX_CHECK (!pending.IsEmpty ());
	RTX_CHECK (!pending.captureId.empty ());
	RTX_CHECK (transfer.Discard (pending, nullptr).IsOk ());

	// Die Session nimmt danach nichts mehr an; es entsteht kein Ergebnis.
	const Result<CaptureSessionState> session =
		harness.api->GetCapture (pending.captureId, nullptr);
	RTX_CHECK (session.IsOk ());
	RTX_CHECK_EQ (session.Value ().state, std::string ("aborted"));
	RTX_CHECK (session.Value ().result.openUrl.empty ());
	RTX_CHECK_EQ (harness.platform.ViewpointsCreated (), 0);
}

RTX_TEST (RahmenAnAufnahmeAnpassenGehtUeberDieLeitung)
{
	// Die erklärte Erweiterung wird auch wirklich gesendet — sonst prüfte
	// `check-openapi.mjs` eine Zeile, die nie vorkommt.
	Harness harness;
	harness.SignIn ();
	CaptureTransfer transfer (*harness.api, *harness.store);

	const TransferRequest created = harness.MakeRequest ("bild-fuenfzehn", "create");
	const Result<CaptureResult> first = transfer.Run (created, nullptr, {});
	RTX_CHECK (first.IsOk ());

	const TransferRequest angepasst = harness.MakeRequest (
		"bild-sechzehn", "update", first.Value ().viewpointId, "fit-to-capture");
	const Result<CaptureResult> second = transfer.Run (angepasst, nullptr, {});
	RTX_CHECK (second.IsOk ());
	RTX_CHECK_EQ (second.Value ().viewpointId, first.Value ().viewpointId);
	RTX_CHECK_EQ (harness.platform.BaseImageVersions (), 2);
}

// --- RTX-A-012: der Modellweg durch denselben Zustandsautomaten ---------------

RTX_TEST (ModellAlleinLaeuftDurchDenZustandsautomaten)
{
	Harness harness;
	harness.platform.SetCaptureMaxMinor (7);
	harness.SignIn ();
	const Result<HandshakeInfo> handshake = harness.api->Handshake (MakeDevice (harness.deviceId), nullptr);
	RTX_CHECK (handshake.IsOk ());
	const int minor = HighestCaptureMinor (handshake.Value ());
	RTX_CHECK_EQ (minor, 7);
	RTX_CHECK_EQ (handshake.Value ().limits.maxGeometryBytes, std::int64_t (67108864));
	const CapturePlan plan = PlanCapture (false, true, minor).Value ();
	RTX_CHECK (plan.ModelOnly ());

	TransferRequest request = harness.MakeRequest ("modell-allein", "create");
	const std::string sha = harness.AddModel (request, false, PlanContractVersion (plan, minor));
	std::string local;
	RTX_CHECK (ReadTextFile (request.manifest.assets[0].localPath, local));
	CaptureTransfer transfer (*harness.api, *harness.store);
	const Result<CaptureResult> result = transfer.Run (request, nullptr, {});
	RTX_CHECK (result.IsOk ());
	if (!result) {
		std::cerr << "  " << result.GetError ().code << ": " << result.GetError ().message << "\n";
		return;
	}
	RTX_CHECK_EQ (harness.platform.LastContractVersion (), std::string ("1.6.0"));
	// Ohne Bild kein Basisbild: `baseImageRole` reist nicht mit (§7.2).
	RTX_CHECK_EQ (harness.platform.LastBaseImageRole (), std::string ());
	RTX_CHECK_EQ (harness.platform.LastFileRoles ().size (), std::size_t (1));
	RTX_CHECK_EQ (harness.platform.LastFileRoles ().front (), std::string ("model"));
	// Dieselben Bytes kommen an — und die temporäre GLB ist danach gelöscht (Sicherheits-Checkliste #307).
	RTX_CHECK (!local.empty ());
	RTX_CHECK (harness.platform.BlobBytes (sha) == local);
	RTX_CHECK_EQ (FileSize (request.manifest.assets[0].localPath), -1LL);
	RTX_CHECK_EQ (PlanResultText (result.Value (), false), std::string ("Modell und Kamera übernommen."));
}

RTX_TEST (BildUndModellLaufenGemeinsam)
{
	Harness harness;
	harness.platform.SetCaptureMaxMinor (7);
	harness.SignIn ();
	TransferRequest request = harness.MakeRequest ("bild-und-modell", "create");
	harness.AddModel (request, true, "1.6.0");
	CaptureTransfer transfer (*harness.api, *harness.store);
	const Result<CaptureResult> result = transfer.Run (request, nullptr, {});
	RTX_CHECK (result.IsOk ());
	RTX_CHECK_EQ (harness.platform.LastBaseImageRole (), std::string ("viewport"));
	RTX_CHECK_EQ (harness.platform.LastFileRoles ().size (), std::size_t (2));
	RTX_CHECK_EQ (PlanResultText (result.Value (), true), std::string ("Bild und Modell übernommen."));
}

RTX_TEST (ModellHatSeineEigeneGrenze)
{
	Harness harness;
	harness.platform.SetCaptureMaxMinor (7);
	harness.SignIn ();
	const Result<HandshakeInfo> handshake = harness.api->Handshake (MakeDevice (harness.deviceId), nullptr);
	TransferRequest request = harness.MakeRequest ("modell-grenze", "create");
	harness.AddModel (request, true, "1.6.0");
	CaptureAsset& model = request.manifest.assets.back ();
	// Größer als ein Bild sein darf (`maxAssetBytes`), aber unter `maxGeometryBytes`: angenommen.
	model.byteSize = handshake.Value ().limits.maxAssetBytes + 1;
	RTX_CHECK (CaptureTransfer::CheckLimits (request.manifest, 1000, handshake.Value ().limits).IsOk ());
	model.byteSize = handshake.Value ().limits.maxGeometryBytes + 1;
	const Status tooLarge = CaptureTransfer::CheckLimits (request.manifest, 1000, handshake.Value ().limits);
	RTX_CHECK (!tooLarge);
	RTX_CHECK_EQ (tooLarge.GetError ().code, std::string (errc::LimitExceeded));
	// Ein Server ohne Modell nennt `model/gltf-binary` nicht: abgelehnt, bevor etwas übertragen wird.
	Harness older;
	older.SignIn ();
	const Result<HandshakeInfo> oldHandshake = older.api->Handshake (MakeDevice (older.deviceId), nullptr);
	model.byteSize = 10;
	RTX_CHECK (!CaptureTransfer::CheckLimits (request.manifest, 1000, oldHandshake.Value ().limits));
}

// --- F-01 an PR #311: eine übergroße Fassung vom Server ist ein Fehlerwert, keine Ausnahme ---

RTX_TEST (UebergrosseFassungImHandshakeWirftNicht)
{
	for (const char* text : {"1.999999999999999999999.0", "99999999999999999999.6.0", "1.6.99999999999999999999"}) {
		Harness harness;
		harness.platform.SetCaptureMaxMinor (7);
		harness.platform.SetHighestSupportedVersionText (text);
		bool threw = false;
		int minor = 0;
		try {
			const Result<HandshakeInfo> handshake = harness.api->Handshake (MakeDevice (harness.deviceId), nullptr);
			RTX_CHECK (handshake.IsOk ());
			RTX_CHECK_EQ (handshake.Value ().highestSupportedVersion, std::string (text));
			minor = HighestCaptureMinor (handshake.Value ());
			// Unbrauchbar heißt „unbekannt": die Wahl bleibt, das Manifest prüft dann selbst.
			const Result<CapturePlan> plan = PlanCapture (true, true, minor);
			RTX_CHECK (plan.IsOk ());
			RTX_CHECK (!PlanContractVersion (plan.Value (), minor).empty ());
		} catch (...) {
			threw = true;
		}
		RTX_CHECK (!threw);
		RTX_CHECK_EQ (minor, -1);
	}
}
