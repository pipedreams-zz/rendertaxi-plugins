// Client der Plugin API v1.
//
// **Maßgeblicher Vertrag:** `docs/api/plugin-api-v1.md`,
// `docs/api/plugin-api-v1.openapi.json` und `packages/contracts/src/plugin.ts`
// auf `main` bei `aeb4908` (Merge von PR #139). Zur Wireform ist dieser Stand
// zeichengleich zum Head des PR (`d35187e`), gegen den der Client gebaut
// wurde. `addon/docs/plugin-api-client.md` hält beide Stände fest.
//
// Dieser Client **rät nicht mehr**. Die neun Annahmen A-01 bis A-09 des ersten
// Durchgangs sind aufgelöst: jede ist entweder durch die Spezifikation
// bestätigt — dann steht hier ein Verweis auf ihren Abschnitt — oder sie war
// falsch und ist korrigiert. Auch die letzte Stelle, die der Momentaufnahme
// vorauslief (`CaptureViewpointTarget::frame`), ist seit `aeb4908` Vertrag.
// Es gibt keine Abweichung mehr.
#pragma once

#include <cstdint>
#include <memory>
#include <string>
#include <vector>

#include "rtx/CaptureManifest.hpp"
#include "rtx/Http.hpp"
#include "rtx/Json.hpp"
#include "rtx/Result.hpp"

namespace rtx {

/** Kennung dieses Plugins in Reverse-DNS-Form; `client_id` und `source.plugin.identifier`. */
inline constexpr const char* kPluginClientId = "ai.rendertaxi.plugin.archicad";

/**
 * Grenzen des Servers (§4). Das Plugin übernimmt sie, verhandelt sie nicht und
 * prüft sie **lokal vor dem Upload**.
 */
struct CaptureLimits {
	std::int64_t maxAssetBytes = 0;
	/** Die eigene Grenze der Modelldatei (RTX-P-011); ohne Angabe gilt `maxAssetBytes`. */
	std::int64_t maxGeometryBytes = 0;
	std::int64_t maxTotalBytes = 0;
	std::int64_t maxManifestBytes = 0;
	int maxAssetCount = 0;
	int captureTtlSeconds = 0;
	std::vector<std::string> allowedMediaTypes;

	bool IsKnown () const { return maxAssetCount > 0; }
};

/** Ein Vertrag aus `contracts[]` des Handshakes (§4). */
struct ContractSupport {
	std::string contract;
	/** `available` oder `planned`. */
	std::string availability;
	std::string finalization;
	/** `required` oder `unsupported`. */
	std::string georeference;
	/** Die unterstützten MAJOR-Stände mit ihrem höchsten MINOR. */
	std::vector<std::pair<int, int>> versions;
};

/**
 * Das Ergebnis der Versionsaushandlung (§4). **Der Server bewertet, nicht der
 * Client**: `negotiation.result` beantwortet genau die Frage, die der erste
 * Durchgang selbst zu beantworten versuchte — und dabei immer
 * `unsupported_contract_major` meldete (V-01).
 */
/**
 * **Die Canvas-Vorgabe des Servers** (`canvasFrameDefault` im Handshake, seit
 * Capture-Manifest 1.7.0, RTX-P-015): Seitenverhältnis und lange Kante, mit
 * denen ein neuer Rahmen beginnt — die Einstellung aus dem Admin-Bereich, die
 * die Übernahme anwendet (`canvas-default` hält die lange Kante, `capture`
 * bleibt nie darunter). Das Add-on zeigt sie und ändert sie nicht.
 *
 * `known == false`: der Server nennt sie nicht (vor 1.7.0, oder er konnte sie
 * nicht lesen) — die Palette zeigt dann ihren Satz ohne Zahl.
 */
struct CanvasDefault {
	bool known = false;
	std::string aspectRatio;
	int longEdgePx = 0;
};

/** Liest `canvasFrameDefault`; fehlt das Feld oder ist es unbrauchbar, `known == false`. */
CanvasDefault ParseCanvasDefault (const JsonPtr& node);

struct HandshakeInfo {
	std::string apiVersion;
	std::string profile;
	std::string profileVersion;
	std::string serverTime;
	std::vector<ContractSupport> contracts;

	bool negotiationPresent = false;
	std::string negotiationContract;
	std::string negotiationContractVersion;
	/** `supported`, `unsupported_contract`, `unsupported_contract_major`, `unsupported_contract_minor`. */
	std::string negotiationResult;
	std::string highestSupportedVersion;

	CaptureLimits limits;

	/** `unknown`, `current`, `update_available`, `update_required`. */
	std::string updateStatus;
	std::string updateMessage;
	std::string updateUrl;

	/** Seit 1.7.0 (RTX-P-015); ohne Feld `known == false`. */
	CanvasDefault canvasDefault;

	/**
	 * Prüft das ausgehandelte Paar. Wertet ausschließlich `negotiation.result`
	 * aus; fehlt der Block, bleibt die Prüfung ohne Befund statt zu raten.
	 * Wirft nie — ein unbrauchbarer Handshake ist ein Fehlerwert, kein Abbruch
	 * des Archicad-Prozesses (F-02).
	 */
	Status RequireCaptureContract () const;

	/** `update_required` verbietet jede Übernahme (§4). */
	bool BlocksTransfer () const { return updateStatus == "update_required"; }
};

/** Das Gerät, wie `auth/device` und `plugin/me` es führen (§5.1). */
struct DeviceIdentity {
	/** UUIDv7 der Installation, **einmal** erzeugt, neben dem Token im Schlüsselspeicher. */
	std::string deviceId;
	/** Optional und vom Nutzer gewählt; nie mit Rechner- oder Benutzernamen vorbelegt. */
	std::string displayName;
	std::string hostKey = "archicad";
	std::string hostVersion;
	std::string pluginVersion;
	std::string os = "macos";
	std::string architecture = "arm64";
};

struct DeviceAuthorization {
	std::string deviceCode;
	std::string userCode;
	std::string verificationUri;
	std::string verificationUriComplete;
	int expiresInSeconds = 600;
	int intervalSeconds = 5;
};

/**
 * Das Anmeldetoken (§5.3). **Es gibt kein `refresh_token`** — die Erneuerung
 * ist gleitend und braucht kein zweites Geheimnis (§10, Entscheidung 5).
 */
struct DeviceToken {
	std::string accessToken;
	int expiresInSeconds = 0;
	std::string scope;
};

/** `GET /plugin/me` (§5.3, `PluginIdentity`). */
struct AccountInfo {
	std::string userDisplayName;
	std::string organizationId;
	std::string organizationName;
	std::string role;
	std::string tokenExpiresAt;
	std::string tokenRenewableUntil;
};

struct ProjectSummary {
	std::string id;
	std::string name;
	int viewpointCount = 0;
};

/**
 * Das Ausgabeziel eines Blickpunkts, so wie `recipe.output.desired` es führt
 * (§6, `viewpoint-overview`).
 *
 * Es wird **gelesen, nicht gesetzt**: der Client zeigt es an und leitet daraus
 * keine Aufnahmegröße ab. Die Aufnahmemaße sind nicht die Zielgröße.
 */
struct DesiredOutput {
	bool known = false;
	int aspectWidth = 0;
	int aspectHeight = 0;
	int exactWidth = 0;
	int exactHeight = 0;
	std::string label;

	double Ratio () const;
};

struct ViewpointSummary {
	std::string id;
	std::string name;
	DesiredOutput desired;
};

/**
 * `target.viewpoint` (§7.2). `baseImageRole` steht **innerhalb** dieses Blocks,
 * bei `create` wie bei `update` (V-11).
 */
struct CaptureViewpointTarget {
	bool present = false;
	/** `create` oder `update`. */
	std::string mode;
	std::string name;
	std::string viewpointId;
	std::string baseImageRole = "viewport";

	/**
	 * Rahmenformat des Ziels (§7.2, ADR 0024 Entscheidung 8).
	 *
	 * Seit `aeb4908` steht das Feld **im Vertrag**; die Vorläuferfassung des
	 * Clients ist damit erledigt. Es gilt die Spezifikation:
	 *
	 * | Modus    | erlaubt                     | Vorgabe |
	 * | -------- | --------------------------- | ------- |
	 * | `create` | nur `fit-to-capture`        | implizit `fit-to-capture`; `keep` wird mit `400` abgelehnt |
	 * | `update` | `keep` \| `fit-to-capture`  | `keep`   |
	 *
	 * `frame` gehört zum Vorgangsschlüssel: derselbe Schlüssel mit einem
	 * anderen `frame` ist `409 idempotency_conflict`. Deshalb sendet ein
	 * Update den Wert **immer ausdrücklich** — auch `keep`. Was der Server
	 * annimmt, steht dann nicht in seiner Vorgabe, sondern im Aufruf.
	 */
	std::string frame = "keep";

	/**
	 * Die **Rahmengröße** (§7.2, RTX-P-010): `canvas-default` oder `capture`.
	 *
	 * Sie ist eine Einstellung des Plugins, keine Eigenschaft der Aufnahme:
	 *
	 * | Wert | Zielgröße |
	 * | --- | --- |
	 * | `canvas-default` | Vorgabe; nur das **Seitenverhältnis** der Aufnahme, die lange Kante des Ausgabeziels bleibt. |
	 * | `capture` | die **Pixelmaße** der Datei, die Basisbild wird (in Archicad der Render-Schutzbereich). Ist deren lange Kante kürzer als die Canvas-Voreinstellung, bleibt diese — der Rahmen wird nicht verkleinert. |
	 *
	 * `size` wirkt nur, wo der Rahmen angepasst wird: bei `create` immer, bei
	 * `update` **nur** zusammen mit `frame: "fit-to-capture"`. Neben `keep`
	 * antwortet der Server `400 validation_failed` — die Einstellung wird
	 * nicht still übergangen, und der Client sendet sie dort deshalb nicht.
	 */
	std::string size = "canvas-default";

	/**
	 * Was dieses Ziel tatsächlich mit dem Rahmen tut. Bei `create` ist das
	 * immer `fit-to-capture` — unabhängig davon, was in `frame` steht; die
	 * Palette zeigt denselben Satz an.
	 */
	std::string EffectiveFrame () const { return mode == "create" ? "fit-to-capture" : frame; }

	/**
	 * Wirkt die Rahmengröße hier überhaupt? Nur dann reist sie mit.
	 *
	 * Bei `update` mit `keep` wäre sie ein `400`; bei `canvas-default` ist sie
	 * die Vorgabe des Servers und muss nicht gesagt werden. Gesendet wird
	 * `capture`, und zwar nur dort, wo der Rahmen angepasst wird.
	 */
	bool SendsSize () const
	{
		return present && size == "capture" && EffectiveFrame () == "fit-to-capture";
	}

	/**
	 * `update` sendet `frame` immer, `create` nie: dort ist `fit-to-capture`
	 * implizit und `keep` ein `400`. Ein Client, der bei `create` nichts
	 * sendet, kann diesen Fehler nicht auslösen.
	 */
	bool SendsFrame () const { return present && mode == "update"; }
};

/** `target` der Anlage (§7.2). `projectId` ist Pflicht (§10, Entscheidung 1). */
struct CaptureTarget {
	std::string projectId;
	CaptureViewpointTarget viewpoint;
};

/** Zustand einer Datei in der Session (§7.4, §7.6). */
struct CaptureFileState {
	std::string role;
	std::string path;
	std::string mediaType;
	std::int64_t byteSize = 0;
	std::string sha256;
	std::string assetId;
	/** Der laufende Übertragungsversuch; `begin` eröffnet `attempt + 1` (§7.3). */
	int attempt = 0;
	std::string state;   // missing | uploading | received | verified | deduplicated | rejected
	std::string errorCode;
	std::string errorMessage;

	/**
	 * `rejected` gehört dazu: ein neues `begin` mit dem nächsten `attempt`
	 * überträgt neu (V-16). `received` ist ein Durchgangszustand — dort wird
	 * erneut gelesen, nicht übertragen.
	 */
	bool NeedsUpload () const
	{
		return state == "missing" || state == "uploading" || state == "rejected";
	}
	bool IsSettled () const { return state == "verified" || state == "deduplicated"; }
};

/** Ergebnis der Finalisierung (§7.7). */
struct CaptureResult {
	std::string kind;
	std::vector<std::pair<std::string, std::string>> assetIdsByRole;
	std::string viewpointId;
	std::string openUrl;
};

struct CaptureSessionState {
	std::string captureId;
	std::string state;
	std::string manifestState;
	std::string validationState;
	std::vector<CaptureFileState> files;
	std::vector<Error> validationErrors;
	CaptureResult result;
	CaptureLimits limits;
	std::string expiresAt;

	const CaptureFileState* FindByPath (const std::string& path) const;
	/** `verified`, `rejected`, `aborted` oder `expired` — die Session nimmt nichts mehr an. */
	bool IsClosed () const;
	/** Nach `expired` und `aborted` ist ein **neuer** Schlüssel fällig (§7.5). */
	bool NeedsNewKey () const { return state == "expired" || state == "aborted"; }
};

/** Kurzlebiges Uploadticket (§7.4). Nur im Arbeitsspeicher, nie im Protokoll. */
struct UploadTicket {
	bool present = false;
	std::string url;
	std::string method = "PUT";
	/** **Alle** `requiredHeaders` werden unverändert gesetzt (V-14). */
	HttpHeaders requiredHeaders;
	std::string expiresAt;
};

/** Antwort von `POST …/files` (§7.4). */
struct CaptureFileResponse {
	std::string sessionState;
	CaptureFileState file;
	UploadTicket ticket;
};

/** Der Anlagekörper, so wie `CreatePluginCaptureRequest` ihn verlangt (§7.2). */
struct CreateCaptureRequest {
	std::string contract;
	std::string contractVersion;
	/** SHA-256 über **genau die Bytes**, die später an `manifest` gehen (V-10). */
	std::string manifestSha256;
	/** Genau die Manifestassets mit `status: "present"`. */
	std::vector<CaptureAsset> files;
	CaptureTarget target;
};

/**
 * Aufrufe der Plugin API v1. Die Klasse hält keinen Zustand außer Adresse und
 * Token; jede Methode ist ein Aufruf und für sich wiederholbar.
 *
 * **Sicherheit (F-01):** Ein `Authorization: Bearer` verlässt den Prozess nur
 * über `https://`. Einzige Ausnahme ist die Schleife (`127.0.0.1`, `[::1]`,
 * `localhost`) für den Scheinserver; sie ist in `IsTokenSafeBaseUrl`
 * dokumentiert und geprüft.
 */
class PluginApiClient final {
public:
	/**
	 * Trägt dieser Aufruf ein Token im **Körper**? `auth/revoke` tut das
	 * (RFC 7009). Die Transportregel aus F-01 muss auch dort greifen — sie
	 * hängt an „trägt ein Token", nicht an „setzt einen Bearer".
	 */
	enum class TokenInBody { No, Yes };

	PluginApiClient (HttpClient& http, std::string baseUrl);

	void SetAccessToken (std::string token);
	const std::string& BaseUrl () const { return baseUrl; }

	/** Die zuletzt gesehene `X-Request-Id` — der Schlüssel jeder Supportanfrage (§3). */
	const std::string& LastRequestId () const { return lastRequestId; }

	/**
	 * Darf über diese Adresse ein Bearer-Token gesendet werden? Nur über
	 * `https://`, oder über `http://` an die Schleife (F-01).
	 */
	static bool IsTokenSafeBaseUrl (const std::string& url);

	Result<HandshakeInfo> Handshake (const DeviceIdentity& device, CancelToken* cancel);

	Result<DeviceAuthorization> StartDeviceLogin (const DeviceIdentity& device,
												  CancelToken* cancel);
	/**
	 * Holt das Token. Die Zwischenstände stehen in `details.reason` und kommen
	 * als Fehlercode zurück: `authorization_pending`, `slow_down`,
	 * `expired_token`, `access_denied`, `invalid_grant` (V-04).
	 * `slow_down` trägt den neuen Abstand in `Error::pointer`.
	 */
	Result<DeviceToken> PollDeviceToken (const std::string& deviceCode, CancelToken* cancel);
	/** Widerruft **das genannte Token** serverseitig (§5.4, V-07). */
	Status RevokeToken (const std::string& token, CancelToken* cancel);
	Result<AccountInfo> Me (CancelToken* cancel);

	Result<std::vector<ProjectSummary>> ListProjects (CancelToken* cancel);
	Result<std::vector<ViewpointSummary>> ListViewpoints (const std::string& projectId,
														  CancelToken* cancel);
	/**
	 * `POST /api/v1/projects` — ein neues Projekt mit den Rechten des
	 * angemeldeten Nutzers (RTX-A-011, #281; wie `create_project` im
	 * gemeinsamen Python-Client).
	 *
	 * `idempotencyKey` kommt aus `NewProjectIntent` (`rtx/ProjectList.hpp`):
	 * derselbe Schlüssel mit demselben Namen legt **ein** Projekt an, auch wenn
	 * die erste Antwort verloren ging. Eine Rolle ohne Anlagerecht bekommt
	 * `403`; die Meldung sagt das in einem Satz statt einen Statuscode zu nennen.
	 * Den Namen prüft der Server; der Client zeigt seine Antwort.
	 */
	Result<ProjectSummary> CreateProject (const std::string& name,
										  const std::string& idempotencyKey, CancelToken* cancel);

	Result<CaptureSessionState> CreateCapture (const std::string& idempotencyKey,
											   const CreateCaptureRequest& request,
											   CancelToken* cancel);
	Result<CaptureSessionState> GetCapture (const std::string& captureId, CancelToken* cancel);

	/** `{ action: "begin", path, attempt }` (§7.4). */
	Result<CaptureFileResponse> BeginFile (const std::string& captureId,
										   const std::string& idempotencyKey,
										   const std::string& path, int attempt,
										   CancelToken* cancel);
	/** `{ action: "complete", path, attempt }` (§7.4). */
	Result<CaptureFileResponse> CompleteFile (const std::string& captureId,
											  const std::string& idempotencyKey,
											  const std::string& path, int attempt,
											  CancelToken* cancel);

	/**
	 * Überträgt genau eine Datei als einziger bedingter `PUT` an den
	 * Objektspeicher — **ohne** `Authorization`-Kopf (§7.4).
	 * `412` ist weder Fehler noch Erfolg: es heißt „unter diesem Versuch liegt
	 * bereits ein Objekt", und `complete` desselben `attempt` entscheidet
	 * (V-15).
	 */
	Status PutFile (const UploadTicket& ticket, const CaptureAsset& asset, CancelToken* cancel,
					const HttpProgress& progress);

	Result<CaptureSessionState> SubmitManifest (const std::string& captureId,
												const std::string& idempotencyKey,
												const std::string& manifestText,
												CancelToken* cancel);
	/** Leerer Körper; das Ziel steht seit der Anlage fest (§7.3, V-12). */
	Result<CaptureSessionState> Finalize (const std::string& captureId,
										  const std::string& idempotencyKey,
										  CancelToken* cancel);
	Status Abort (const std::string& captureId, const std::string& idempotencyKey,
				  CancelToken* cancel);

private:
	Result<HttpResponse> Call (const std::string& method, const std::string& path,
							   const std::string& body, const HttpHeaders& extra,
							   CancelToken* cancel, TokenInBody tokenInBody = TokenInBody::No);
	Result<CaptureFileResponse> CallFiles (const std::string& captureId,
										   const std::string& idempotencyKey,
										   const std::string& action, const std::string& path,
										   int attempt, CancelToken* cancel);
	Error ErrorFromResponse (const HttpResponse& response) const;

	HttpClient& http;
	std::string baseUrl;
	std::string accessToken;
	std::string lastRequestId;
};

/** Liest `recipe.output.desired`; öffentlich, damit Tests es prüfen. */
DesiredOutput ParseDesiredOutput (const JsonPtr& node);

/** Liest ein `PluginCapture`; öffentlich, damit Tests es prüfen. */
Result<CaptureSessionState> ParseCaptureSession (const JsonPtr& node);

/** Baut den Anlagekörper; öffentlich, damit die Prüfung ihn gegen OpenAPI hält. */
JsonPtr CreateCaptureRequestToJson (const CreateCaptureRequest& request);

} // namespace rtx
