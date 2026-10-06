#include "rtx/CaptureTransfer.hpp"

#include "rtx/Ids.hpp"
#include "rtx/Json.hpp"
#include "rtx/Log.hpp"
#include "rtx/Sha256.hpp"

namespace rtx {
namespace {

void Report (const TransferReporter& report, TransferStage stage, const std::string& message,
			 int percent)
{
	if (report) report (TransferProgress {stage, message, percent});
}

/**
 * Die Rahmengröße, **soweit sie wirkt**. Wo sie nicht mitreist, ist sie für
 * die Identität des Vorgangs auch nicht da — sonst wäre dieselbe Anfrage mit
 * einer anderen Einstellung ein anderer Vorgang, ohne dass sich am gesendeten
 * Körper etwas änderte.
 */
std::string EffectiveSize (const CaptureViewpointTarget& viewpoint)
{
	return viewpoint.SendsSize () ? viewpoint.size : std::string ("canvas-default");
}

bool SameTarget (const PendingTransfer& pending, const CaptureTarget& target)
{
	return pending.targetProjectId == target.projectId &&
		   pending.targetMode == (target.viewpoint.present ? target.viewpoint.mode : "") &&
		   pending.targetViewpointId == target.viewpoint.viewpointId &&
		   pending.targetViewpointName == target.viewpoint.name &&
		   // Der **wirksame** Rahmenwert, nicht das Feld: bei `create` ist
		   // `fit-to-capture` implizit, was immer im Feld steht (§7.2).
		   pending.targetFrame == target.viewpoint.EffectiveFrame () &&
		   // Dasselbe für die Rahmengröße: sie zählt, wo sie wirkt.
		   pending.targetSize == EffectiveSize (target.viewpoint);
}

bool AllSettled (const CaptureSessionState& session)
{
	if (session.files.empty ()) return false;
	for (const CaptureFileState& file : session.files) {
		if (!file.IsSettled ()) return false;
	}
	return true;
}

Error FirstProblem (const CaptureSessionState& session)
{
	for (const CaptureFileState& file : session.files) {
		if (file.state == "rejected")
			return Error (file.errorCode.empty () ? errc::SchemaInvalid : file.errorCode,
						  "Der Server hat " + file.path + " abgelehnt: " + file.errorMessage);
	}
	if (!session.validationErrors.empty ()) return session.validationErrors.front ();
	return Error (errc::Transport, "Die Session steht auf " + session.state + ".");
}

const CaptureAsset* FindAsset (const CaptureManifest& manifest, const std::string& path)
{
	for (const CaptureAsset& asset : manifest.assets) {
		if (asset.path == path && asset.status == "present") return &asset;
	}
	return nullptr;
}

} // namespace

CaptureTransfer::CaptureTransfer (PluginApiClient& apiClient, TransferStore& transferStore) :
	api (apiClient), store (transferStore)
{
}

Status CaptureTransfer::CheckLimits (const CaptureManifest& manifest, std::int64_t manifestBytes,
									 const CaptureLimits& limits)
{
	if (!limits.IsKnown ()) return Status::Ok ();
	std::int64_t total = 0;
	int count = 0;
	for (const CaptureAsset& asset : manifest.assets) {
		if (asset.status != "present") continue;
		++count;
		total += asset.byteSize;
		if (limits.maxAssetBytes > 0 && asset.byteSize > limits.maxAssetBytes)
			return Status::Fail (errc::LimitExceeded,
								 "Die Datei " + asset.path + " ist größer, als der Server annimmt.");
		if (!limits.allowedMediaTypes.empty ()) {
			bool allowed = false;
			for (const std::string& type : limits.allowedMediaTypes) {
				if (type == asset.mediaType) { allowed = true; break; }
			}
			if (!allowed)
				return Status::Fail (errc::LimitExceeded,
									 "Der Server nimmt den Medientyp " + asset.mediaType +
										 " nicht an.");
		}
	}
	if (limits.maxAssetCount > 0 && count > limits.maxAssetCount)
		return Status::Fail (errc::LimitExceeded, "Mehr Dateien, als der Server je Aufnahme annimmt.");
	if (limits.maxTotalBytes > 0 && total > limits.maxTotalBytes)
		return Status::Fail (errc::LimitExceeded, "Die Aufnahme ist insgesamt zu groß.");
	// V-19: auch die Manifestgröße kommt aus `limits` und wird lokal geprüft.
	if (limits.maxManifestBytes > 0 && manifestBytes > limits.maxManifestBytes)
		return Status::Fail (errc::LimitExceeded,
							 "Die Beschreibung der Aufnahme ist größer, als der Server annimmt.");
	return Status::Ok ();
}

Status DiscardAll (CaptureTransfer& transfer, TransferStore& store,
				   const std::string& sourceProjectKey,
				   const std::vector<std::string>& sourceViewKeys, CancelToken* cancel)
{
	Status firstError = Status::Ok ();
	for (const std::string& key : sourceViewKeys) {
		if (key.empty ()) continue;
		const PendingTransfer pending = store.FindPending (sourceProjectKey, key);
		if (pending.IsEmpty ()) continue;
		const Status status = transfer.Discard (pending, cancel);
		// **Weitermachen.** Ein Server, der den Abbruch nicht bestätigt, darf
		// den zweiten Vorgang nicht stehen lassen; gemeldet wird trotzdem.
		if (!status && firstError.IsOk ()) firstError = status;
	}
	return firstError;
}

bool HasLocalMaterial (const PendingTransfer& pending)
{
	if (pending.IsEmpty () || pending.directory.empty ()) return false;
	// Ohne Kennung und Zeitpunkt des Manifests lassen sich seine Bytes nicht
	// wieder erzeugen; ein solcher Eintrag ist nicht fortsetzbar.
	if (pending.manifestCaptureId.empty () || pending.manifestSha256.empty ()) return false;

	std::string text;
	if (!ReadTextFile (pending.directory + "/capture-manifest.json", text) || text.empty ())
		return false;
	if (Sha256::OfString (text) != pending.manifestSha256) return false;

	const JsonPtr root = Json::Parse (text);
	const JsonPtr assets = root ? root->Get ("assets") : nullptr;
	if (!assets) return false;
	bool anyPresent = false;
	for (const JsonPtr& asset : assets->Items ()) {
		const JsonPtr status = asset ? asset->Get ("status") : nullptr;
		if (!status || status->StringOr ("") != "present") continue;
		const JsonPtr path = asset->Get ("path");
		const JsonPtr size = asset->Get ("byteSize");
		const std::string relative = path ? path->StringOr ("") : std::string ();
		if (relative.empty () || relative.find ("..") != std::string::npos || !size) return false;
		if (FileSize (pending.directory + "/" + relative) != size->IntOr (-1)) return false;
		anyPresent = true;
	}
	return anyPresent;
}

Result<PendingTransfer> ReleaseOrphanedPending (TransferStore& store,
												const std::string& sourceProjectKey,
												const std::string& sourceViewKey)
{
	const PendingTransfer pending = store.FindPending (sourceProjectKey, sourceViewKey);
	if (pending.IsEmpty () || HasLocalMaterial (pending)) return Result<PendingTransfer>::Ok ({});

	// Der Schlüssel gehört ins Protokoll, aus demselben Grund wie in `Run`.
	LogLine ("Vorgang ohne lokalen Bestand verworfen, Schlüssel " + pending.idempotencyKey);
	if (!pending.directory.empty ()) RemoveDirectory (pending.directory);
	store.RemovePending (sourceProjectKey, sourceViewKey);
	const Status saved = store.Save ();
	if (!saved) return Result<PendingTransfer>::Fail (saved.GetError ());
	return Result<PendingTransfer>::Ok (pending);
}

Status CaptureTransfer::Discard (const PendingTransfer& pending, CancelToken* cancel)
{
	if (!pending.captureId.empty () && !pending.idempotencyKey.empty ()) {
		const Status aborted = api.Abort (pending.captureId, pending.idempotencyKey, cancel);
		if (!aborted) LogLine ("Abbruch der Session war nicht möglich: " + aborted.GetError ().code);
	}
	if (!pending.directory.empty ()) RemoveDirectory (pending.directory);
	store.RemovePending (pending.sourceProjectKey, pending.sourceViewKey);
	return store.Save ();
}

Result<CaptureResult> CaptureTransfer::Run (const TransferRequest& request, CancelToken* cancel,
											const TransferReporter& report)
{
	Report (report, TransferStage::Preparing, "Aufnahme prüfen", 0);

	const Status valid = request.manifest.Validate ();
	if (!valid) return Result<CaptureResult>::Fail (valid.GetError ());

	// --- 1. Die Manifestbytes stehen **vor** der Anlage fest -----------------
	//
	// `manifestSha256` läuft über genau diese Bytes (§7.2). Wird das Manifest
	// später anders serialisiert, scheitert der Schritt `manifest` mit
	// `manifest_hash_mismatch`. Deshalb wird es einmal erzeugt, auf die Platte
	// geschrieben und von dort wiederverwendet — auch nach einem Neustart.
	const std::string manifestPath = request.directory + "/capture-manifest.json";
	std::string manifestText;
	if (!ReadTextFile (manifestPath, manifestText) || manifestText.empty ()) {
		const Result<std::string> serialized = request.manifest.Serialize ();
		if (!serialized) return Result<CaptureResult>::Fail (serialized.GetError ());
		manifestText = serialized.Value ();
		if (!WriteTextFile (manifestPath, manifestText))
			return Result<CaptureResult>::Fail (errc::IoFailed,
												"Die Aufnahme ließ sich nicht ablegen.");
	}
	const std::string manifestSha256 = Sha256::OfString (manifestText);

	// --- 2. Idempotenzschlüssel: gemerkt oder neu ----------------------------
	PendingTransfer pending = store.FindPending (request.sourceProjectKey, request.sourceViewKey);
	const bool resumed = !pending.IsEmpty ();
	if (!pending.IsEmpty ()) {
		if (pending.manifestSha256 != manifestSha256 || !SameTarget (pending, request.target)) {
			return Result<CaptureResult>::Fail (
				errc::IdempotencyConflict,
				// Kurz genug für zwei Palettenzeilen: eine Meldung, die man nur
				// zur Hälfte liest, hilft niemandem.
				"Hier läuft schon eine Übernahme mit anderem Inhalt oder Ziel. "
				"Fortsetzen oder verwerfen.");
		}
	} else {
		pending = PendingTransfer {};
		pending.sourceProjectKey = request.sourceProjectKey;
		pending.sourceViewKey = request.sourceViewKey;
		pending.manifestSha256 = manifestSha256;
		// Die Kennung und der Zeitpunkt **des Manifests**: ohne sie kann eine
		// Wiederaufnahme die Bytes nicht noch einmal erzeugen.
		pending.manifestCaptureId = request.manifest.captureId;
		pending.manifestCreatedAt = request.manifest.createdAt;
		pending.directory = request.directory;
		pending.targetProjectId = request.target.projectId;
		pending.targetMode = request.target.viewpoint.present ? request.target.viewpoint.mode : "";
		pending.targetViewpointId = request.target.viewpoint.viewpointId;
		pending.targetViewpointName = request.target.viewpoint.name;
		pending.targetFrame = request.target.viewpoint.EffectiveFrame ();
		pending.targetSize = EffectiveSize (request.target.viewpoint);
		pending.serverUrl = request.serverUrl;
		pending.createdAt = NowTimestampUtc ();
		// **Eine UUIDv7** (§7.3). Der erste Durchgang baute eine eigene
		// Zeichenkette; der Server wies sie mit `invalid_idempotency_key` ab.
		pending.idempotencyKey = NewUuidV7 ();
		// Vor dem ersten Netzaufruf sichern (§7.1, Schritt 5).
		store.PutPending (pending);
		const Status saved = store.Save ();
		if (!saved) return Result<CaptureResult>::Fail (saved.GetError ());
	}

	CreateCaptureRequest create;
	create.contract = kCaptureContract;
	create.contractVersion = kCaptureContractVersion;
	create.manifestSha256 = manifestSha256;
	create.target = request.target;
	for (const CaptureAsset& asset : request.manifest.assets) {
		if (asset.status == "present") create.files.push_back (asset);
	}

	// --- 3. Session anlegen, notfalls mit neuem Schlüssel --------------------
	//
	// **Der Vorgangsschlüssel gehört ins Protokoll.** Er ist kein Geheimnis —
	// eine vom Client erzeugte UUIDv7 —, und ohne ihn lässt sich von außen
	// nicht nachlesen, ob eine Wiederholung denselben Vorgang fortgesetzt
	// oder einen zweiten begonnen hat. Genau diese Frage stellte der
	// Abnahmelauf am 25.09.2026, und die Antwort stand nur im Zustand auf der
	// Platte.
	LogLine ((resumed ? "Vorgang fortgesetzt, Schlüssel " : "Vorgang begonnen, Schlüssel ") +
			 pending.idempotencyKey);
	Report (report, TransferStage::CreatingSession, "Übernahme anmelden", 5);
	Result<CaptureSessionState> session =
		api.CreateCapture (pending.idempotencyKey, create, cancel);
	if (!session) return Result<CaptureResult>::Fail (session.GetError ());

	// §7.5: nach `expired` und `aborted` mit **neuem** Schlüssel neu anlegen.
	// Die Grenze verhindert, dass ein Server, der jede Session sofort ablaufen
	// lässt, den Capture dauerhaft blockiert (F-03).
	for (int rotation = 0; session.Value ().NeedsNewKey (); ++rotation) {
		if (rotation >= kMaxKeyRotations) {
			return Result<CaptureResult>::Fail (
				errc::CaptureExpired,
				"Der Server ließ " + std::to_string (kMaxKeyRotations + 1) +
					" Übernahmen hintereinander ablaufen oder abbrechen. Die Übernahme wird "
					"abgebrochen; versuche es später erneut.");
		}
		LogLine ("Session " + session.Value ().state + "; neuer Vorgangsschlüssel.");
		pending.idempotencyKey = NewUuidV7 ();
		pending.captureId.clear ();
		store.PutPending (pending);
		const Status saved = store.Save ();
		if (!saved) return Result<CaptureResult>::Fail (saved.GetError ());
		session = api.CreateCapture (pending.idempotencyKey, create, cancel);
		if (!session) return Result<CaptureResult>::Fail (session.GetError ());
	}

	if (pending.captureId != session.Value ().captureId) {
		// Die `captureId` vergibt der **Server** (§10, Entscheidung 7).
		pending.captureId = session.Value ().captureId;
		store.PutPending (pending);
		const Status saved = store.Save ();
		if (!saved) return Result<CaptureResult>::Fail (saved.GetError ());
	}
	const std::string captureId = pending.captureId;
	const std::string key = pending.idempotencyKey;

	const Status limits = CheckLimits (request.manifest,
									   static_cast<std::int64_t> (manifestText.size ()),
									   session.Value ().limits);
	if (!limits) return Result<CaptureResult>::Fail (limits.GetError ());

	// --- 4. Dateien übertragen: begin → PUT → complete -----------------------
	for (int round = 0; round < 4; ++round) {
		if (cancel != nullptr && cancel->IsCancelled ())
			return Result<CaptureResult>::Fail (errc::Cancelled, "Übernahme abgebrochen.");
		if (AllSettled (session.Value ())) break;

		int index = 0;
		const int outstanding =
			static_cast<int> (session.Value ().files.size () > 0 ? session.Value ().files.size () : 1);
		bool movedSomething = false;

		for (const CaptureFileState& file : session.Value ().files) {
			++index;
			if (file.IsSettled ()) continue;
			if (file.state == "received") continue;   // Durchgangszustand: erneut lesen
			if (!file.NeedsUpload ()) continue;

			const CaptureAsset* asset = FindAsset (request.manifest, file.path);
			if (asset == nullptr)
				return Result<CaptureResult>::Fail (
					errc::SchemaInvalid,
					"Der Server erwartet eine Datei, die diese Aufnahme nicht enthält: " + file.path);

			const int base = 10 + (70 * (index - 1)) / outstanding;
			Report (report, TransferStage::UploadingFiles, "Bild übertragen: " + asset->path, base);

			// `begin` eröffnet `file.attempt + 1`. Auf `uploading` ist das die
			// Wiederholung desselben Versuchs — dann bleibt der `attempt`
			// stehen und das Ticket wird frisch signiert (§7.3).
			const int attempt = file.state == "uploading" ? file.attempt : file.attempt + 1;
			const Result<CaptureFileResponse> begun =
				api.BeginFile (captureId, key, file.path, attempt, cancel);
			if (!begun) return Result<CaptureResult>::Fail (begun.GetError ());

			if (begun.Value ().file.IsSettled ()) {
				// Etwa `deduplicated`: `ticket` ist `null`, nichts zu übertragen.
				movedSomething = true;
				continue;
			}
			if (!begun.Value ().ticket.present) {
				// Kein Ticket und nicht erledigt: die Session nimmt nichts mehr
				// an (abgebrochen oder abgelaufen). Der Zustand entscheidet.
				break;
			}

			const Status put = api.PutFile (
				begun.Value ().ticket, *asset, cancel,
				[&] (std::uint64_t sent, std::uint64_t total) {
					if (total == 0) return;
					const int share = static_cast<int> ((70 * sent) / (total * outstanding));
					Report (report, TransferStage::UploadingFiles, "Bild übertragen: " + asset->path,
							base + share);
				});
			if (!put) return Result<CaptureResult>::Fail (put.GetError ());

			// **`complete` mit demselben `attempt`** — auch nach `412`. Ohne
			// ihn bliebe die Datei auf `uploading` (V-13, V-15).
			const Result<CaptureFileResponse> completed = api.CompleteFile (
				captureId, key, file.path, begun.Value ().file.attempt, cancel);
			if (!completed) return Result<CaptureResult>::Fail (completed.GetError ());
			movedSomething = true;
		}

		session = api.GetCapture (captureId, cancel);
		if (!session) return Result<CaptureResult>::Fail (session.GetError ());
		if (session.Value ().state == "rejected")
			return Result<CaptureResult>::Fail (FirstProblem (session.Value ()));
		if (session.Value ().IsClosed ()) break;
		if (!movedSomething) break;
	}

	if (!AllSettled (session.Value ()))
		return Result<CaptureResult>::Fail (FirstProblem (session.Value ()));

	// --- 5. Manifest übertragen ----------------------------------------------
	if (session.Value ().manifestState != "accepted") {
		Report (report, TransferStage::SubmittingManifest, "Aufnahme beschreiben", 85);
		session = api.SubmitManifest (captureId, key, manifestText, cancel);
		if (!session) return Result<CaptureResult>::Fail (session.GetError ());
		if (session.Value ().state == "rejected")
			return Result<CaptureResult>::Fail (FirstProblem (session.Value ()));
	}

	// --- 6. Finalisieren ------------------------------------------------------
	Report (report, TransferStage::Finalizing, "Übernahme abschließen", 92);
	session = api.Finalize (captureId, key, cancel);
	if (!session) return Result<CaptureResult>::Fail (session.GetError ());
	if (session.Value ().state != "verified")
		return Result<CaptureResult>::Fail (FirstProblem (session.Value ()));

	// --- 7. Zustand räumen und Vorschlag merken -------------------------------
	const CaptureResult result = session.Value ().result;
	store.RemovePending (request.sourceProjectKey, request.sourceViewKey);
	LastAssignment assignment;
	assignment.sourceProjectKey = request.sourceProjectKey;
	assignment.sourceViewKey = request.sourceViewKey;
	assignment.projectId = request.target.projectId;
	assignment.projectName = request.projectDisplayName;
	assignment.viewpointId = result.viewpointId.empty () ? request.target.viewpoint.viewpointId
														 : result.viewpointId;
	assignment.viewpointName = request.viewpointDisplayName.empty ()
								   ? request.target.viewpoint.name
								   : request.viewpointDisplayName;
	store.PutAssignment (assignment);
	const Status saved = store.Save ();
	if (!saved) return Result<CaptureResult>::Fail (saved.GetError ());
	if (!request.directory.empty ()) RemoveDirectory (request.directory);

	Report (report, TransferStage::Done, "Übernahme abgeschlossen", 100);
	return Result<CaptureResult>::Ok (result);
}

} // namespace rtx
