// Der host-neutrale Inhaltsvertrag `rendertaxi.plugin.capture-manifest` v1
// (ADR 0009, Entscheidung 2; Schema und Fixtures liefert Issue #16).
//
// Dieser Kern **erzeugt und prüft** das Manifest, bevor irgendetwas übertragen
// wird: „Ein Manifest, das lokal nicht validiert, wird nicht hochgeladen. Der
// Nutzer sieht den Fehler in Archicad, nicht erst im Browser."
// (`upload-protocol.md`, Abschnitt 1.2 — nach ADR 0009 unverändert gültig.)
//
// Was hier bewusst **nicht** steht: `modelVersionId`,
// `predecessorModelVersionId`, Diff- oder Aktivierungsfelder. Der Bildweg nimmt
// den Modellweg nicht vorweg (ADR 0009, Entscheidung 3). `camera` und
// `geometry` sind Pflichtfelder und in v1 immer `null`.
#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "rtx/Json.hpp"
#include "rtx/Result.hpp"

namespace rtx {

inline constexpr const char* kCaptureContract = "rendertaxi.plugin.capture-manifest";
inline constexpr const char* kCaptureContractVersion = "1.0.0";

/** Rollenkatalog aus `common.schema.json#/$defs/assetRole`, in `kebab-case`. */
bool IsKnownAssetRole (const std::string& role);
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
};

struct CaptureIntent {
	std::string presetKey;
	std::string recipeId;
	std::string promptText;
};

struct CaptureManifest {
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
	std::vector<CaptureAsset> assets;

	/** Berechnet `contentHash` aus den Assets mit `status: "present"`. */
	Result<std::string> ContentHash () const;
	/** Vollständige Prüfung gegen die v1-Regeln; `contentHash` wird mitgeprüft. */
	Status Validate () const;
	/** JSON-Baum in der Feldreihenfolge des Schemas. */
	Result<JsonPtr> ToJson () const;
	/** Eingerückter Manifesttext mit abschließendem Zeilenvorschub. */
	Result<std::string> Serialize () const;
};

} // namespace rtx
