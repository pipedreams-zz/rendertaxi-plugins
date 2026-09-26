// Der Übernahmevorgang als Zustandsautomat — wiederholungssicher von Anfang bis
// Ende, nach `docs/api/plugin-api-v1.md`, Abschnitte 7.1 bis 7.5.
//
// Drei Dinge, die der erste Durchgang falsch hatte und die hier stehen, weil
// sie die Form des Automaten bestimmen:
//
// 1. **Der Schlüssel ist eine UUIDv7** und steht an **jedem** zustandsändernden
//    Aufruf — Anlage, `files`, `manifest`, `finalize`, `abort` (V-08, V-09).
// 2. **Eine Datei geht über `begin` → `PUT` → `complete`**, jeweils mit ihrem
//    `attempt`. Ohne `complete` bleibt sie auf `uploading` (V-13).
// 3. **Die Manifestbytes stehen vor der Anlage fest**, denn `manifestSha256`
//    läuft über genau sie (V-10).
//
// Der Automat kennt kein Archicad und kein DevKit. Er ist deshalb ohne
// Archicad gegen einen Scheinserver prüfbar.
#pragma once

#include <functional>
#include <string>
#include <vector>

#include "rtx/CaptureManifest.hpp"
#include "rtx/PluginApi.hpp"
#include "rtx/TransferStore.hpp"

namespace rtx {

enum class TransferStage {
	Preparing,
	CreatingSession,
	UploadingFiles,
	SubmittingManifest,
	Finalizing,
	Done
};

struct TransferProgress {
	TransferStage stage = TransferStage::Preparing;
	std::string message;
	int percent = 0;
};

using TransferReporter = std::function<void (const TransferProgress&)>;

struct TransferRequest {
	/** Vollständiges, bereits lokal geprüftes Manifest; Assets tragen `localPath`. */
	CaptureManifest manifest;
	CaptureTarget target;
	std::string sourceProjectKey;
	std::string sourceViewKey;
	/** Verzeichnis mit Bilddateien und `capture-manifest.json`. */
	std::string directory;
	std::string serverUrl;
	/** Anzeigenamen für den gemerkten Vorschlag; nie Identität. */
	std::string projectDisplayName;
	std::string viewpointDisplayName;
};

class CaptureTransfer final {
public:
	CaptureTransfer (PluginApiClient& api, TransferStore& store);

	/**
	 * Führt die Übernahme aus oder nimmt sie wieder auf. Der Aufruf ist für
	 * denselben `TransferRequest` beliebig oft wiederholbar.
	 */
	Result<CaptureResult> Run (const TransferRequest& request, CancelToken* cancel,
							   const TransferReporter& report);

	/** Verwirft einen angefangenen Vorgang: Session abbrechen, Zustand räumen. */
	Status Discard (const PendingTransfer& pending, CancelToken* cancel);

	/** Prüft die Grenzen des Servers, bevor eine Datei übertragen wird (§4). */
	static Status CheckLimits (const CaptureManifest& manifest, std::int64_t manifestBytes,
							   const CaptureLimits& limits);

	/**
	 * Wie oft ein abgelaufener oder abgebrochener Vorgang höchstens mit einem
	 * neuen Schlüssel wiederholt wird, bevor der Client aufgibt und es sagt.
	 *
	 * Ohne diese Grenze konnte ein Server, der jede Session sofort ablaufen
	 * lässt, einen Capture **dauerhaft** blockieren: der Client rotierte den
	 * Schlüssel, bekam wieder `expired` und rotierte erneut (F-03).
	 */
	static constexpr int kMaxKeyRotations = 3;

private:
	PluginApiClient& api;
	TransferStore& store;
};

/**
 * Verwirft **jeden** offenen Vorgang einer Ansicht.
 *
 * Eine Ansicht kann mehr als einen Schlüssel tragen: das Fensterbild und das
 * gerechnete Rendering sind zwei Quellen desselben Blickwinkels und zwei
 * Vorgänge. Der erste Anlauf des Verwerfens kannte nur den Schlüssel der
 * Fensteraufnahme — ein liegengebliebener Rendering-Vorgang ließ sich damit
 * nicht verwerfen und blockierte den nächsten mit `idempotency_conflict`
 * (F-05, dritte Nachprüfung).
 *
 * Die Schlüssel kommen von **einer** Stelle im Add-on; Start und Verwerfen
 * benutzen dieselbe. Zwei Wege zu einer Entscheidung wären genau der Fehler,
 * der hier steckte.
 *
 * Der Rückgabewert ist der erste Fehler, den ein Abbruch gemeldet hat; geräumt
 * wird lokal in jedem Fall und für jeden Schlüssel.
 */
Status DiscardAll (CaptureTransfer& transfer, TransferStore& store,
				   const std::string& sourceProjectKey,
				   const std::vector<std::string>& sourceViewKeys, CancelToken* cancel);

/**
 * Liegt der lokale Bestand eines angefangenen Vorgangs noch vollständig vor?
 *
 * Fortsetzen heißt **dieselben Bytes** schicken: das Manifest, über das
 * `manifestSha256` läuft, und jede Datei, die es als `present` führt. Fehlt
 * eines davon — jemand hat das Arbeitsverzeichnis geräumt, das Manifest wurde
 * nie geschrieben, oder der Eintrag stammt aus einer Fassung ohne
 * `manifestCaptureId` —, gibt es nichts fortzusetzen.
 *
 * Geprüft werden Vorhandensein und Größe, nicht der Inhalt der Bilddatei: die
 * Palette fragt das in jedem Leerlauf, und ein Bild zu hashen wäre dafür zu
 * teuer. Eine gleich große, aber andere Datei fällt spätestens beim Server
 * auf, der jede Datei gegen ihren `sha256` aus dem Manifest prüft.
 */
bool HasLocalMaterial (const PendingTransfer& pending);

/**
 * Räumt einen angefangenen Vorgang ohne lokalen Bestand, **bevor** neue
 * Aufnahmebytes entstehen (Issue #88, Punkt 16).
 *
 * Ohne diesen Schritt blieb der alte Eintrag stehen: die nächste Aufnahme
 * landete in seinem Verzeichnis, ergab ein anderes Manifest, und der Client
 * meldete `idempotency_conflict` gegen den eigenen verwaisten Vorgang.
 *
 * Geräumt wird **lokal und sofort** — Eintrag und Verzeichnis —, ohne Netz,
 * damit der Aufrufer es vor der Aufnahme im Hauptfaden tun kann. Zurück kommt
 * der geräumte Vorgang; trägt er eine `captureId`, bricht der Aufrufer die
 * Session mit `CaptureTransfer::Discard` auch serverseitig ab. Ein leerer
 * Rückgabewert heißt: nichts zu räumen.
 */
Result<PendingTransfer> ReleaseOrphanedPending (TransferStore& store,
												const std::string& sourceProjectKey,
												const std::string& sourceViewKey);

} // namespace rtx
