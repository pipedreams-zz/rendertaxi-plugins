// Der host-neutrale Inhaltsvertrag `rendertaxi.plugin.capture-manifest` v1
// (ADR 0009, Entscheidung 2; Schema und Fixtures liefert Issue #16).
//
// Dieser Kern **erzeugt und prüft** das Manifest, bevor irgendetwas übertragen
// wird: „Ein Manifest, das lokal nicht validiert, wird nicht hochgeladen. Der
// Nutzer sieht den Fehler in Archicad, nicht erst im Browser."
// (`upload-protocol.md`, Abschnitt 1.2 — nach ADR 0009 unverändert gültig.)
//
// Was hier bewusst **nicht** steht: `modelVersionId`,
// `predecessorModelVersionId`, Diff- oder Aktivierungsfelder (ADR 0009,
// Entscheidung 3). „Modellweg" heißt in diesem Vertrag der Weg des
// Modell-Assets — eine GLB-Datei mit Kamera —, nicht der Modellversionsweg
// (`capture-manifest.md`, Abschnitt 14).
//
// **Fassungen** (`capture-manifest.md`, Abschnitt 3; RTX-A-012, #307): Das
// Manifest schreibt die Fassung, die `contractVersion` nennt, und **nur** die
// Felder, die sie kennt — 1.1.0 `source.host.capabilities`, 1.2.0 `camera`,
// `geometry` und die Rolle `model`, 1.3.0 das PNG-Profil, 1.4.0
// `camera.shift`, 1.5.0 `source.fileName`, 1.6.0 das Modell allein mit
// `camera.resolution` und `modelOnlyCapture`. Welche Fassung gilt, entscheidet
// der Handshake (`CaptureWays.hpp`). Bis 1.1.x sind `camera` und `geometry`
// immer `null`.
#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "rtx/Json.hpp"
#include "rtx/Result.hpp"

namespace rtx {

inline constexpr const char* kCaptureContract = "rendertaxi.plugin.capture-manifest";
/** Die Fassung, mit der der Handshake verhandelt und die ein Manifest ohne Angabe schreibt. */
inline constexpr const char* kCaptureContractVersion = "1.0.0";
/** Die höchste Fassung, die dieser Kern schreibt (1.7.0 bringt kein Manifestfeld). */
inline constexpr int kCaptureHighestMinor = 6;
/** Die Rolle der GLB-Datei; jede andere Rolle beschreibt ein Bild. */
inline constexpr const char* kModelRole = "model";
inline constexpr const char* kModelMediaType = "model/gltf-binary";

/** Rollenkatalog aus `common.schema.json#/$defs/assetRole`, in `kebab-case`, samt `model`. */
bool IsKnownAssetRole (const std::string& role);
/** MINOR einer Fassung `1.<minor>.<patch>`; −1, wenn sie keine 1.x ist. */
int ContractMinor (const std::string& contractVersion);

/**
 * `source.fileName` aus dem Namen oder Pfad der Projektdatei (Abschnitt 13):
 * der letzte Pfadteil, sonst nichts. Verletzt der Name eine Regel des
 * Vertrags — Pfadtrenner, Steuer- oder Formatzeichen, mehr als 255 Zeichen,
 * „." oder „..", ein Inhaltsverbot wie `name@host` —, kommt eine **leere**
 * Zeichenkette zurück und das Feld entfällt: ein Capture scheitert nie an
 * seinem Dateinamen (wie `manifest.source_file_name` im Python-Client).
 *
 * NFC wie jeder Text des Manifests, über `NormalizeNfc` (`Platform.hpp`):
 * macOS liefert Dateinamen zerlegt. Der Name steht nie im Protokoll.
 */
std::string SourceFileName (const std::string& nameOrPath);
/** Rollen, deren Bild Farbe trägt (`srgb`/`linear`); alle übrigen sind `non-color`. */
bool IsColorAssetRole (const std::string& role);

struct CaptureImage {
	int width = 0;
	int height = 0;
	std::string colorSpace;    // srgb | linear | non-color
	int bitDepth = 8;
	std::string sampleFormat;  // uint | float
	std::string channels;      // gray | gray-alpha | rgb | rgba
};

struct CaptureAsset {
	std::string role;
	std::string path;
	std::string status = "present";  // present | planned | unavailable
	std::string mediaType;
	std::int64_t byteSize = 0;
	std::string sha256;
	bool hasImage = false;
	CaptureImage image;
	std::string note;
	/** Lokaler Pfad der Datei; steht nie im Manifest, nur im Uploader. */
	std::string localPath;
};

struct CaptureSource {
	std::string hostKey = "archicad";
	std::string hostVersion;
	std::string hostBuild;
	std::string pluginIdentifier;
	std::string pluginVersion;
	std::string os = "macos";
	std::string osVersion;
	std::string architecture = "arm64";
	/** Seit 1.1.0: Selbstauskunft, Schlüssel → Zustand (`available`, …), in dieser Reihenfolge geschrieben. */
	std::vector<std::pair<std::string, std::string>> capabilities;
	/** Seit 1.5.0: Name der gespeicherten Projektdatei ohne Pfad; leer entfällt. */
	std::string fileName;
};

/**
 * Der Kamerablock (`capture-manifest.md`, Abschnitt 11.3) im **Exportraum**:
 * Meter, rechtshändig, +Y oben — derselbe Raum wie die GLB-Datei.
 */
struct CaptureCamera {
	/** `perspective` oder `orthographic`. */
	std::string projection = "perspective";
	double position[3] = {0, 0, 0};
	double direction[3] = {0, 0, -1};
	double up[3] = {0, 1, 0};
	/** Nur Perspektive: `horizontal` oder `vertical`, Winkel im Bogenmaß. */
	std::string fovAxis = "horizontal";
	double fovAngle = 0.0;
	/** Nur Parallel: halbe Breite und halbe Höhe in Metern. */
	double halfWidth = 0.0;
	double halfHeight = 0.0;
	double clipNear = 0.1;
	/** 0 heißt unendlich (`far: null`). */
	double clipFar = 0.0;
	/** Seit 1.4.0: Shift als Anteil der längeren Bildseite; (0, 0) entfällt. */
	double shiftX = 0.0;
	double shiftY = 0.0;
	/** Seit 1.6.0: Bildgröße der Kamera; 0 entfällt. */
	int resolutionWidth = 0;
	int resolutionHeight = 0;

	bool HasShift () const { return shiftX != 0.0 || shiftY != 0.0; }
	bool HasResolution () const { return resolutionWidth > 0 && resolutionHeight > 0; }
};

/** Der Block `geometry` (Abschnitt 11.2): wie der Exportraum im Weltraum des Hosts liegt. */
struct CaptureGeometry {
	std::string assetPath;
	/** Archicad rechnet in Metern (QA-02). */
	double sourceUnitScaleToMeter = 1.0;
	std::string handedness = "right";
	std::string upAxis = "z";
	/** Ursprung des Exportraums in Metern, in den Achsen des Hosts; der Modellweg verschiebt nicht. */
	double origin[3] = {0, 0, 0};
};

struct CaptureIntent {
	std::string presetKey;
	std::string recipeId;
	std::string promptText;
};

struct CaptureManifest {
	/** Die geschriebene Fassung; `kCaptureContractVersion` für den Bildweg gegen einen Server ohne Angabe. */
	std::string contractVersion = kCaptureContractVersion;
	std::string captureId;
	std::string createdAt;
	CaptureSource source;
	/** Leer bedeutet `null` — vor der Zuordnung kennt das Plugin kein Projekt. */
	std::string platformProjectId;
	std::string sourceProjectKey;
	std::string projectDisplayName;
	std::string sourceViewKey;
	std::string viewDisplayName;
	CaptureIntent intent;
	/** Ab 1.2.0; ohne bleibt `camera` `null`. */
	bool hasCamera = false;
	CaptureCamera camera;
	/** Ab 1.2.0 und genau dann, wenn eine Datei der Rolle `model` vorhanden ist. */
	bool hasGeometry = false;
	CaptureGeometry geometry;
	std::vector<CaptureAsset> assets;

	/** Trägt der Capture ein vorhandenes Bild, ein vorhandenes Modell? */
	bool HasPresentImage () const;
	bool HasPresentModel () const;

	/** Berechnet `contentHash` aus den Assets mit `status: "present"`. */
	Result<std::string> ContentHash () const;
	/** Vollständige Prüfung gegen die Regeln der Fassung `contractVersion`; `contentHash` wird mitgeprüft. */
	Status Validate () const;
	/** JSON-Baum in der Feldreihenfolge des Schemas. */
	Result<JsonPtr> ToJson () const;
	/** Eingerückter Manifesttext mit abschließendem Zeilenvorschub. */
	Result<std::string> Serialize () const;

	/**
	 * Liest ein Manifest, das dieser Kern geschrieben hat, wieder ein — für die
	 * Wiederaufnahme eines angefangenen Vorgangs (RTX-A-012). Fortsetzen heißt
	 * **dieselben Bytes** senden; wer das Manifest neu zusammenstellte, müsste
	 * Bild, Modell und Kamera neu erzeugen und bekäme andere Bytes.
	 *
	 * `directory` wird jedem Asset als `localPath` vorangestellt. Das Ergebnis
	 * ist geprüft (`Validate`); ein fremdes oder kaputtes Dokument ist ein
	 * Fehler, keine Ausnahme.
	 */
	static Result<CaptureManifest> Parse (const std::string& text, const std::string& directory);
};

} // namespace rtx
