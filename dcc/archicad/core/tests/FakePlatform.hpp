// Gegenstelle für die Prüfungen: ein Server, der die **Spezifikation** umsetzt.
//
// Er trägt ausdrücklich **keine eigene Vertragsauslegung** mehr. Zwei Dinge
// halten ihn daran:
//
// 1. Jede Anfrage und jede Antwort wird in ein Protokoll geschrieben
//    (`SetTranscriptPath`). `tools/check-openapi.mjs` prüft dieses Protokoll
//    gegen `docs/api/plugin-api-v1.openapi.json` — Anfragekörper gegen das
//    `requestBody`-Schema, Antworten gegen das Schema ihres Status.
// 2. Die Antwortformen stammen aus den Beispielen der Spezifikation.
//
// Damit prüfen Client und Scheinserver nicht mehr nur gegeneinander.
#pragma once

#include <map>
#include <mutex>
#include <set>
#include <string>
#include <vector>

#include "MockServer.hpp"

namespace testing {

class FakePlatform final {
public:
	FakePlatform ();

	MockHandler Handler ();
	void SetBaseUrl (std::string url);

	/** Schreibt jede Anfrage und Antwort als JSON-Zeile für die OpenAPI-Prüfung. */
	void SetTranscriptPath (std::string path);

	// --- Steuerung für die Prüfungen ---------------------------------------
	void ApproveDevice ();
	void DenyDevice ();
	void SetAutoApprove (bool value);
	/** Der Server widerruft alle Tokens; der nächste Aufruf ist `401`. */
	void RevokeAllTokens ();
	/** Die nächsten `count` Aufrufe auf `path` antworten mit HTTP 503. */
	void FailNext (const std::string& path, int count);
	/**
	 * Wie `FailNext`, aber über ein Pfadmuster. Die `captureId` vergibt der
	 * Server (§10, Entscheidung 7) — ein Test kann den vollen Pfad deshalb
	 * nicht vorher kennen.
	 */
	void FailNextMatching (const std::string& needle, int count);
	/** Alle offenen Sessions laufen ab. */
	void ExpireSessions ();
	/** Jede neue Session ist sofort abgelaufen — der Dauerablauf aus F-03. */
	void SetAlwaysExpire (bool value);
	/**
	 * Der Handshake nennt die Canvas-Vorgabe (`canvasFrameDefault`, seit
	 * 1.7.0, RTX-P-015). Ohne Aufruf antwortet er wie ein älterer Server: ohne
	 * das Feld.
	 */
	void SetCanvasDefault (std::string aspectRatio, int longEdgePx);
	/** Die nächste Prüfung einer Datei lehnt sie ab (`asset_hash_mismatch`). */
	void RejectNextFile ();
	/** Die nächsten `count` Tokenabfragen antworten `slow_down`. */
	void SlowDownNext (int count);
	/** Die nächsten `count` Tokenabfragen antworten `429` mit `Retry-After`. */
	void RateLimitNext (int count);

	// --- Projekte (RTX-A-011) ------------------------------------------------
	/**
	 * Die nächste Projektanlage **wirkt**, aber ihre Antwort ist `500` — die
	 * verlorene Antwort aus dem Kerntest zur Idempotenz.
	 */
	void LoseNextProjectResponse ();
	/** `viewer` darf keine Projekte anlegen (`403`), wie auf der Plattform. */
	void SetRole (std::string role);
	/** Ein Projekt, das jemand anderswo anlegt — etwa im Web. */
	void AddProject (const std::string& id, const std::string& name);
	void RemoveProject (const std::string& id);
	/** Wie oft ein Projekt mit diesem Namen in der Liste steht. */
	int ProjectsNamed (const std::string& name) const;
	/** Die Idempotenzschlüssel aller `POST /projects`, in ihrer Reihenfolge. */
	std::vector<std::string> ProjectCreateKeys () const;

	// --- Nachweise ---------------------------------------------------------
	int AssetsCreated () const;
	int ViewpointsCreated () const;
	int BaseImageVersions () const;
	std::string LastOpenUrl () const;
	int PendingAuthorizationPolls () const;
	/** Wie oft ein Token widerrufen wurde — der Nachweis für V-07. */
	int RevocationCount () const;
	/** Ist dieses Token noch gültig? */
	bool IsTokenValid (const std::string& token) const;

private:
	struct FileEntry {
		std::string role;
		std::string path;
		std::string sha256;
		std::string mediaType;
		long long byteSize = 0;
		std::string assetId;
		int attempt = 0;
		std::string state = "missing";
		std::string errorCode;
	};

	struct Project {
		std::string id;
		std::string name;
	};

	struct Viewpoint {
		std::string id;
		std::string name;
		std::vector<std::string> baseImageShas;
	};

	struct Session {
		std::string captureId;
		std::string idempotencyKey;
		std::string manifestSha256;
		std::string requestFingerprint;
		std::string contract;
		std::string contractVersion;
		std::string state = "awaiting-assets";
		std::string manifestState = "pending";
		std::string validationState = "pending";
		std::vector<FileEntry> files;
		std::vector<std::pair<std::string, std::string>> validationErrors;
		std::string targetProjectId;
		std::string targetMode;
		std::string targetViewpointId;
		std::string targetViewpointName;
		std::string targetBaseImageRole;
		std::string targetJson;
		std::string viewpointId;
		std::string openUrl;
		std::map<std::string, std::string> assetIds;
		bool finalized = false;
		bool expired = false;
	};

	MockResponse Dispatch (const MockRequest& request);
	MockResponse ServeDevicePage (const MockRequest& request);
	MockResponse ServeViewpointPage (const std::string& viewpointId);
	MockResponse Capture (const MockRequest& request);
	MockResponse CaptureFiles (Session& session, const MockRequest& request);
	std::string SessionJson (const Session& session) const;
	Session* FindSession (const std::string& captureId);
	void Record (const MockRequest& request, const MockResponse& response);

	mutable std::mutex mutex;
	std::map<std::string, std::string> pendingDeviceCodes;
	bool deviceApproved = false;
	bool deviceDenied = false;
	bool autoApprove = false;
	bool alwaysExpire = false;
	std::string canvasAspectRatio;
	int canvasLongEdgePx = 0;
	bool rejectNextFile = false;
	int slowDownRemaining = 0;
	int rateLimitRemaining = 0;
	int pendingPolls = 0;
	std::set<std::string> validTokens;
	std::map<std::string, Session> sessions;
	std::map<std::string, std::string> sessionByIdempotencyKey;
	std::set<std::string> storedBlobs;
	std::map<std::string, std::string> blobBytes;
	std::map<std::string, Viewpoint> viewpoints;
	std::map<std::string, std::string> ticketToPath;
	std::map<std::string, std::string> viewpointsByTarget;
	/** Replay-Identität → Antwortkörper, nach §7.3. */
	std::map<std::string, std::string> replayResponses;
	std::map<std::string, int> failures;
	std::map<std::string, int> failuresByNeedle;
	int assetsCreated = 0;
	int viewpointsCreated = 0;
	int baseImageVersions = 0;
	int revocations = 0;
	std::string lastOpenUrl;
	std::string baseUrl;
	std::string transcriptPath;
	int nextId = 1;
	/** Die Liste aus `GET /projects`, neueste zuerst. */
	std::vector<Project> projects {{"0199a000-0000-7000-8000-00000000000a", "Testprojekt"}};
	/** Schlüssel → (Name, Projektkennung) der Anlagen. */
	std::map<std::string, std::pair<std::string, std::string>> projectByKey;
	std::vector<std::string> projectCreateKeys;
	bool loseNextProjectResponse = false;
	std::string role = "owner";
};

} // namespace testing
