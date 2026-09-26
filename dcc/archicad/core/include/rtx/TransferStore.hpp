// Was einen Neustart von Archicad überlebt.
//
// Festlegung 6 aus Issue #20: „ein Idempotenzschlüssel je Übernahme, der bei
// Fehler und Wiederholung derselbe bleibt, bis der Vorgang abgeschlossen oder
// vom Nutzer verworfen ist." Er liegt deshalb nicht im Speicher des Prozesses
// und nicht in der Archicad-Projektdatei, sondern in einer kleinen Datei unter
// `~/Library/Application Support/rendertaxi/archicad/`.
//
// **Kein Geheimnis in dieser Datei.** Sie führt Kennungen, Zustände und Pfade;
// das Anmeldetoken liegt in der Keychain (`TokenStore`), signierte Adressen
// werden nie gespeichert (`upload-protocol.md`, Abschnitt 9).
#pragma once

#include <string>
#include <vector>

#include "rtx/Result.hpp"

namespace rtx {

/** Ein angefangener, noch nicht abgeschlossener Übernahmevorgang. */
struct PendingTransfer {
	/** Fachlicher Schlüssel: Archicad-Projekt und Quellansicht. */
	std::string sourceProjectKey;
	std::string sourceViewKey;
	/**
	 * **Eine UUIDv7**, einmal je Vorgang erzeugt und vor dem ersten Aufruf
	 * gespeichert (`plugin-api-v1.md`, Abschnitt 7.1, Schritt 5). Nach
	 * `expired` und `aborted` tritt ein neuer an seine Stelle (§7.5).
	 */
	std::string idempotencyKey;
	/** Die **vom Server** vergebene Kennung der Session (§10, Entscheidung 7). */
	std::string captureId;
	/**
	 * Die `captureId` **des Manifests** — vom Client erzeugt, Teil des
	 * Manifestinhalts (§7.6: „`captureId` darin vergibt der Server; es ist
	 * nicht die `captureId` des Manifests").
	 *
	 * Sie steht hier, weil eine wiederaufgenommene Übernahme dieselben
	 * Manifestbytes braucht: eine neue Kennung ergäbe einen neuen
	 * `manifestSha256` und damit einen `idempotency_conflict` gegen den
	 * eigenen angefangenen Vorgang. Der erste Anlauf nahm dafür `captureId` —
	 * die des **Servers** —, und solange die Anlage nie geglückt war, war sie
	 * leer: das Manifest trug dann eine leere Kennung und wurde lokal als
	 * ungültig abgewiesen.
	 */
	std::string manifestCaptureId;
	/** `createdAt` des Manifests; aus demselben Grund gemerkt wie die Kennung. */
	std::string manifestCreatedAt;
	/** SHA-256 über genau die Manifestbytes, die an `manifest` gehen (§7.2). */
	std::string manifestSha256;
	/** Verzeichnis mit Bilddateien und Manifest; wird beim Abschluss geräumt. */
	std::string directory;
	std::string targetProjectId;
	std::string targetMode;
	std::string targetViewpointId;
	std::string targetViewpointName;
	/** `keep` oder `fit-to-capture`; Teil des Ziels und damit der Identität. */
	std::string targetFrame;
	/** `canvas-default` oder `capture`; ebenfalls Teil des Ziels (§7.2). */
	std::string targetSize;
	std::string serverUrl;
	std::string createdAt;

	bool IsEmpty () const { return idempotencyKey.empty (); }
};

/** Die zuletzt bestätigte Zuordnung — Grundlage für einen Vorschlag, nie mehr. */
struct LastAssignment {
	std::string sourceProjectKey;
	std::string sourceViewKey;
	std::string projectId;
	std::string projectName;
	std::string viewpointId;
	std::string viewpointName;
};

class TransferStore final {
public:
	/** Legt den Speicher an dem übergebenen Pfad an; das Verzeichnis entsteht bei Bedarf. */
	explicit TransferStore (std::string filePath);

	/** Standardpfad unter `~/Library/Application Support/rendertaxi/archicad/`. */
	static std::string DefaultPath ();
	/** Verzeichnis für Zwischenstände eines Captures. */
	static std::string DefaultWorkDirectory ();

	Status Load ();
	Status Save () const;

	PendingTransfer FindPending (const std::string& sourceProjectKey,
								 const std::string& sourceViewKey) const;
	void PutPending (const PendingTransfer& transfer);
	void RemovePending (const std::string& sourceProjectKey, const std::string& sourceViewKey);
	const std::vector<PendingTransfer>& Pending () const { return pending; }

	LastAssignment FindAssignment (const std::string& sourceProjectKey,
								   const std::string& sourceViewKey) const;
	void PutAssignment (const LastAssignment& assignment);

private:
	std::string path;
	std::vector<PendingTransfer> pending;
	std::vector<LastAssignment> assignments;
};

/** Legt ein Verzeichnis samt Elternverzeichnissen an. */
bool EnsureDirectory (const std::string& path);
/** Löscht ein Verzeichnis samt Inhalt; `false`, wenn etwas stehen blieb. */
bool RemoveDirectory (const std::string& path);
/** Schreibt eine Textdatei vollständig; `false` bei jedem Fehler. */
bool WriteTextFile (const std::string& path, const std::string& content);
/** Liest eine Textdatei vollständig. */
bool ReadTextFile (const std::string& path, std::string& content);
/** Dateigröße in Bytes; -1, wenn die Datei fehlt. */
long long FileSize (const std::string& path);

} // namespace rtx
