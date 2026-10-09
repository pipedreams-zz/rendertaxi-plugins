#include "rtx/CaptureManifest.hpp"

#include <algorithm>
#include <cmath>
#include <regex>
#include <set>

#include "rtx/Canonical.hpp"
#include "rtx/Ids.hpp"
#include "rtx/Numbers.hpp"
#include "rtx/Platform.hpp"

namespace rtx {
namespace {

constexpr std::int64_t kMaxSafeInteger = 9007199254740991LL;

const char* const kRoles[] = {"viewport", "beauty",    "depth",         "normal",  "albedo",
							  "mask",     "object-id", "material-id",   "ambient-occlusion",
							  "cryptomatte", "model"};

/** Präzisionsklassen (`canonical-hash.mjs`, `CANONICAL_DECIMALS`). */
constexpr int kLength = 6;
constexpr int kRatio = 6;
constexpr int kAngle = 9;
constexpr int kDirection = 9;
constexpr double kPi = 3.14159265358979323846;
constexpr double kUnitTolerance = 1e-6;

/** Kanonischer Wert einer Präzisionsklasse: gerundet, ohne negative Null. */
double Canon (double value, int decimals)
{
	const double scale = std::pow (10.0, decimals);
	const double rounded = std::round (value * scale) / scale;
	return rounded == 0.0 ? 0.0 : rounded;
}

JsonPtr Decimal (double value, int decimals)
{
	return Json::MakeDecimal (Canon (value, decimals), decimals);
}

JsonPtr Vector (const double v[3], int decimals)
{
	JsonPtr array = Json::MakeArray ();
	for (int i = 0; i < 3; ++i) array->Append (Decimal (v[i], decimals));
	return array;
}

double Length3 (const double v[3])
{
	return std::sqrt (v[0] * v[0] + v[1] * v[1] + v[2] * v[2]);
}

bool AllFinite (const double v[3])
{
	return std::isfinite (v[0]) && std::isfinite (v[1]) && std::isfinite (v[2]);
}

/** Die Codepunkte eines UTF-8-Textes; leer bei ungültigem UTF-8. */
bool DecodeUtf8 (const std::string& text, std::vector<std::uint32_t>& out)
{
	out.clear ();
	for (std::size_t i = 0; i < text.size ();) {
		const unsigned char c = static_cast<unsigned char> (text[i]);
		std::uint32_t cp = 0;
		int extra = 0;
		if (c < 0x80) { cp = c; }
		else if ((c & 0xE0) == 0xC0) { cp = c & 0x1F; extra = 1; }
		else if ((c & 0xF0) == 0xE0) { cp = c & 0x0F; extra = 2; }
		else if ((c & 0xF8) == 0xF0) { cp = c & 0x07; extra = 3; }
		else return false;
		for (int k = 1; k <= extra; ++k) {
			if (i + k >= text.size ()) return false;
			const unsigned char next = static_cast<unsigned char> (text[i + k]);
			if ((next & 0xC0) != 0x80) return false;
			cp = (cp << 6) | (next & 0x3F);
		}
		out.push_back (cp);
		i += 1 + extra;
	}
	return true;
}

/** Die Regel des Schemas für `source.fileName` (`pattern`, Länge). */
bool MatchesFileName (const std::string& name)
{
	std::vector<std::uint32_t> points;
	if (!DecodeUtf8 (name, points)) return false;
	if (points.empty () || points.size () > 255) return false;
	if (name == "." || name == "..") return false;
	for (const std::uint32_t cp : points) {
		if (cp == '/' || cp == '\\' || cp == ':') return false;
		if (cp <= 0x1F || (cp >= 0x7F && cp <= 0x9F)) return false;
		if ((cp >= 0x200B && cp <= 0x200F) || (cp >= 0x2028 && cp <= 0x202E) || (cp >= 0x2060 && cp <= 0x206F) ||
			cp == 0xFEFF)
			return false;
	}
	return true;
}

/**
 * Dieselben Funde wie `FORBIDDEN_CONTENT` in `tools/validate.mjs` und im
 * Server, soweit sie einen Dateinamen treffen können (wie
 * `_FILE_NAME_FORBIDDEN` im Python-Client). `\b` arbeitet hier wie in
 * JavaScript ohne `u`: ein Byte über 0x7F ist kein Wortzeichen.
 */
bool HasForbiddenContent (const std::string& text)
{
	static const std::regex patterns[] = {
		std::regex ("[A-Za-z0-9._-]+@[A-Za-z0-9-]+(\\.[A-Za-z0-9-]+)*"),
		std::regex ("\\b[A-Za-z0-9-]+\\.(local|lan|internal|localdomain)\\b", std::regex::icase),
		std::regex ("\\b(secret|password|passwd|api[_-]?key|access[_-]?token|bearer)\\b\\s*[:=]", std::regex::icase),
		std::regex ("X-Amz-[A-Za-z-]+=|X-Goog-Signature=|[?&](Signature|sig)="),
		std::regex ("\\bBEGIN [A-Z ]*PRIVATE KEY\\b"),
	};
	for (const std::regex& pattern : patterns)
		if (std::regex_search (text, pattern)) return true;
	return false;
}

Status Invalid (const std::string& message, const std::string& pointer)
{
	return Status::Fail (Error (errc::SchemaInvalid, message, pointer));
}

bool MatchesStableKey (const std::string& value)
{
	if (value.size () < 3 || value.size () > 128) return false;
	const char first = value[0];
	const bool firstOk = (first >= 'a' && first <= 'z') || (first >= '0' && first <= '9');
	if (!firstOk) return false;
	for (const char c : value) {
		const bool ok = (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '.' || c == '_' ||
						c == ':' || c == '-';
		if (!ok) return false;
	}
	return true;
}

bool MatchesRelativePath (const std::string& value)
{
	if (value.empty () || value.size () > 512) return false;
	if (value.front () == '/' || value.back () == '/') return false;
	std::size_t start = 0;
	while (start <= value.size ()) {
		const std::size_t slash = value.find ('/', start);
		const std::string segment =
			value.substr (start, slash == std::string::npos ? std::string::npos : slash - start);
		if (segment.empty () || segment == "..") return false;
		for (const char c : segment) {
			const bool ok = (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
							(c >= '0' && c <= '9') || c == '.' || c == '_' || c == '-';
			if (!ok) return false;
		}
		if (slash == std::string::npos) break;
		start = slash + 1;
	}
	return true;
}

bool MatchesSha256 (const std::string& value)
{
	if (value.size () != 64) return false;
	for (const char c : value) {
		const bool ok = (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f');
		if (!ok) return false;
	}
	return true;
}

bool MatchesSemver (const std::string& value)
{
	int parts = 0;
	std::size_t digits = 0;
	for (const char c : value) {
		if (c == '.') {
			if (digits == 0) return false;
			++parts;
			digits = 0;
			continue;
		}
		if (c < '0' || c > '9') return false;
		++digits;
	}
	return parts == 2 && digits > 0;
}

bool MatchesHostVersion (const std::string& value)
{
	if (value.empty () || value.size () > 32) return false;
	std::size_t digits = 0;
	int dots = 0;
	for (const char c : value) {
		if (c == '.') {
			if (digits == 0) return false;
			++dots;
			digits = 0;
			continue;
		}
		if (c < '0' || c > '9') return false;
		++digits;
	}
	return digits > 0 && dots <= 3;
}

bool MatchesReverseDns (const std::string& value)
{
	if (value.empty () || value.size () > 128) return false;
	if (value.find ('.') == std::string::npos) return false;
	std::size_t start = 0;
	while (start <= value.size ()) {
		const std::size_t dot = value.find ('.', start);
		const std::string label =
			value.substr (start, dot == std::string::npos ? std::string::npos : dot - start);
		if (label.empty ()) return false;
		if (label.front () == '-' || label.back () == '-') return false;
		for (const char c : label) {
			const bool ok = (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '-';
			if (!ok) return false;
		}
		if (dot == std::string::npos) break;
		start = dot + 1;
	}
	return true;
}

bool IsMediaType (const std::string& value)
{
	const std::size_t slash = value.find ('/');
	if (slash == std::string::npos || slash == 0 || slash + 1 >= value.size ()) return false;
	for (const char c : value) {
		if (c >= 'A' && c <= 'Z') return false;
		if (c == ' ' || c == ';') return false;
	}
	return true;
}

} // namespace

bool IsKnownAssetRole (const std::string& role)
{
	for (const char* known : kRoles) {
		if (role == known) return true;
	}
	return false;
}

bool IsColorAssetRole (const std::string& role)
{
	return role == "viewport" || role == "beauty" || role == "albedo";
}

int ContractMinor (const std::string& contractVersion)
{
	// Ohne `std::stoi`: eine übergroße MINOR aus dem Handshake oder einem Manifest wäre sonst eine
	// Ausnahme statt eines Fehlerwerts (F-01 an PR #311). MAJOR und PATCH werden ebenso begrenzt gelesen.
	if (!MatchesSemver (contractVersion)) return -1;
	const std::size_t first = contractVersion.find ('.');
	const std::size_t second = contractVersion.find ('.', first + 1);
	int major = 0, minor = 0, patch = 0;
	if (!ParseBoundedInt (contractVersion.substr (0, first), major) ||
		!ParseBoundedInt (contractVersion.substr (first + 1, second - first - 1), minor) ||
		!ParseBoundedInt (contractVersion.substr (second + 1), patch))
		return -1;
	return major == 1 ? minor : -1;
}

std::string SourceFileName (const std::string& nameOrPath)
{
	std::string name = NormalizeNfc (nameOrPath);
	std::replace (name.begin (), name.end (), '\\', '/');
	const std::size_t slash = name.rfind ('/');
	if (slash != std::string::npos) name = name.substr (slash + 1);
	// Leerraum am Rand wie `str.strip ()` im Python-Client (ASCII genügt: der Name kommt aus dem Dateisystem).
	const char* const space = " \t\n\r\f\v";
	const std::size_t first = name.find_first_not_of (space);
	if (first == std::string::npos) return {};
	name = name.substr (first, name.find_last_not_of (space) - first + 1);
	if (!MatchesFileName (name) || HasForbiddenContent (name)) return {};
	return name;
}

bool CaptureManifest::HasPresentImage () const
{
	for (const CaptureAsset& asset : assets)
		if (asset.status == "present" && asset.role != kModelRole) return true;
	return false;
}

bool CaptureManifest::HasPresentModel () const
{
	for (const CaptureAsset& asset : assets)
		if (asset.status == "present" && asset.role == kModelRole) return true;
	return false;
}

Result<std::string> CaptureManifest::ContentHash () const
{
	std::vector<ContentHashAsset> present;
	for (const CaptureAsset& asset : assets) {
		if (asset.status != "present") continue;
		present.push_back ({asset.role, asset.path, asset.sha256});
	}
	return CaptureContentHash (present);
}

namespace {

/** Der Kamerablock nach `checkCamera` in `tools/validate.mjs` und dem Schema. */
Status ValidateCamera (const CaptureCamera& camera, int minor)
{
	const bool perspective = camera.projection == "perspective";
	if (!perspective && camera.projection != "orthographic")
		return Invalid ("camera.projection ist weder perspective noch orthographic.", "/camera/projection");
	if (!AllFinite (camera.position) || !AllFinite (camera.direction) || !AllFinite (camera.up))
		return Invalid ("camera enthält keine endliche Zahl.", "/camera");
	double direction[3], up[3];
	for (int i = 0; i < 3; ++i) {
		direction[i] = Canon (camera.direction[i], kDirection);
		up[i] = Canon (camera.up[i], kDirection);
	}
	if (std::fabs (Length3 (direction) - 1.0) > kUnitTolerance)
		return Invalid ("camera.direction ist kein normierter Vektor.", "/camera/direction");
	if (std::fabs (Length3 (up) - 1.0) > kUnitTolerance)
		return Invalid ("camera.up ist kein normierter Vektor.", "/camera/up");
	const double dot = direction[0] * up[0] + direction[1] * up[1] + direction[2] * up[2];
	if (std::fabs (dot) >= 1.0 - kUnitTolerance)
		return Invalid ("camera.up ist parallel zu direction.", "/camera/up");
	if (perspective) {
		if (camera.fovAxis != "horizontal" && camera.fovAxis != "vertical")
			return Invalid ("camera.fieldOfView.axis ist weder horizontal noch vertical.", "/camera/fieldOfView/axis");
		const double angle = Canon (camera.fovAngle, kAngle);
		if (!(angle > 0.0 && angle < kPi))
			return Invalid ("camera.fieldOfView.angle liegt nicht echt zwischen 0 und π.", "/camera/fieldOfView/angle");
	} else {
		if (!(Canon (camera.halfWidth, kLength) > 0.0) || !(Canon (camera.halfHeight, kLength) > 0.0))
			return Invalid ("camera.extent braucht halbe Breite und Höhe größer als 0.", "/camera/extent");
	}
	const double nearClip = Canon (camera.clipNear, kLength);
	if (!(nearClip > 0.0)) return Invalid ("camera.clip.near ist nicht größer als 0.", "/camera/clip/near");
	if (camera.clipFar != 0.0 && !(Canon (camera.clipFar, kLength) > nearClip))
		return Invalid ("camera.clip.far ist nicht größer als near.", "/camera/clip/far");
	if (camera.HasShift ()) {
		if (minor < 4) return Invalid ("camera.shift ist erst ab contractVersion 1.4.0 zulässig.", "/camera/shift");
		if (!(std::fabs (camera.shiftX) <= 2.0) || !(std::fabs (camera.shiftY) <= 2.0))
			return Invalid ("camera.shift liegt außerhalb von −2 … 2.", "/camera/shift");
	}
	if (camera.resolutionWidth != 0 || camera.resolutionHeight != 0) {
		if (minor < 6)
			return Invalid ("camera.resolution ist erst ab contractVersion 1.6.0 zulässig.", "/camera/resolution");
		if (camera.resolutionWidth < 1 || camera.resolutionWidth > 65536 || camera.resolutionHeight < 1 ||
			camera.resolutionHeight > 65536)
			return Invalid ("camera.resolution liegt außerhalb von 1 … 65536.", "/camera/resolution");
	}
	return Status::Ok ();
}

} // namespace

Status CaptureManifest::Validate () const
{
	const int minor = ContractMinor (contractVersion);
	if (minor < 0)
		return Invalid ("contractVersion ist keine Fassung 1.x.y.", "/contractVersion");
	if (minor > kCaptureHighestMinor)
		return Invalid ("contractVersion ist neuer, als dieser Kern schreibt.", "/contractVersion");
	if (!IsUuidV7 (captureId))
		return Invalid ("captureId ist keine UUIDv7 in Kleinschreibung.", "/captureId");
	if (!IsTimestampUtc (createdAt))
		return Invalid ("createdAt ist kein UTC-Zeitpunkt mit Millisekunden und Suffix Z.",
						"/createdAt");

	if (!MatchesStableKey (source.hostKey) || source.hostKey.find ('_') != std::string::npos ||
		source.hostKey.find (':') != std::string::npos ||
		source.hostKey.find ('.') != std::string::npos)
		return Invalid ("source.host.key ist kein kebab-case-Schlüssel.", "/source/host/key");
	if (!MatchesHostVersion (source.hostVersion))
		return Invalid ("source.host.version ist keine punktgetrennte Dezimalversion.",
						"/source/host/version");
	if (!MatchesReverseDns (source.pluginIdentifier))
		return Invalid ("source.plugin.identifier ist keine Reverse-DNS-Kennung.",
						"/source/plugin/identifier");
	if (!MatchesSemver (source.pluginVersion))
		return Invalid ("source.plugin.version ist keine semantische Version.",
						"/source/plugin/version");
	if (source.os != "macos" && source.os != "windows" && source.os != "linux")
		return Invalid ("source.machine.os ist unbekannt.", "/source/machine/os");
	if (source.architecture != "arm64" && source.architecture != "x64")
		return Invalid ("source.machine.architecture ist unbekannt.", "/source/machine/architecture");

	// Ein Feld einer MINOR-Version gehört nicht in ein Dokument, das eine ältere nennt.
	if (!source.capabilities.empty () && minor < 1)
		return Invalid ("source.host.capabilities ist erst ab contractVersion 1.1.0 zulässig.",
						"/source/host/capabilities");
	std::set<std::string> seenCapabilities;
	for (const auto& [key, state] : source.capabilities) {
		static const std::set<std::string> keys = {
			"viewportCapture", "beautyRender", "depthPass",       "normalPass",   "albedoPass",
			"objectIdPass",    "materialIdPass", "maskPass",      "cameraExport", "geometryExport",
			"modelOnlyCapture", "bimMetadata",  "backgroundRender", "resultReimport"};
		if (keys.count (key) == 0 || !seenCapabilities.insert (key).second)
			return Invalid ("Unbekannte oder doppelte Capability: " + key, "/source/host/capabilities");
		if (state != "available" && state != "planned" && state != "unavailable" && state != "unknown")
			return Invalid ("Unbekannter Capability-Zustand: " + state, "/source/host/capabilities/" + key);
		if (key == "modelOnlyCapture" && minor < 6)
			return Invalid ("modelOnlyCapture ist erst ab contractVersion 1.6.0 zulässig.",
							"/source/host/capabilities/modelOnlyCapture");
	}
	if (!source.fileName.empty ()) {
		if (minor < 5)
			return Invalid ("source.fileName ist erst ab contractVersion 1.5.0 zulässig.", "/source/fileName");
		if (!MatchesFileName (source.fileName) || HasForbiddenContent (source.fileName))
			return Invalid ("source.fileName ist kein zulässiger Dateiname ohne Pfad.", "/source/fileName");
	}

	if (!platformProjectId.empty () && !IsUuidV7 (platformProjectId))
		return Invalid ("project.platformProjectId ist keine UUIDv7.", "/project/platformProjectId");
	if (!sourceProjectKey.empty () && !MatchesStableKey (sourceProjectKey))
		return Invalid ("project.sourceProjectKey verletzt die Schlüsselgrammatik.",
						"/project/sourceProjectKey");
	if (!sourceViewKey.empty () && !MatchesStableKey (sourceViewKey))
		return Invalid ("view.sourceViewKey verletzt die Schlüsselgrammatik.", "/view/sourceViewKey");
	if (projectDisplayName.size () > 512)
		return Invalid ("project.displayName ist länger als 512 Zeichen.", "/project/displayName");
	if (viewDisplayName.size () > 512)
		return Invalid ("view.displayName ist länger als 512 Zeichen.", "/view/displayName");

	if (!intent.presetKey.empty () && !MatchesStableKey (intent.presetKey))
		return Invalid ("intent.presetKey verletzt die Schlüsselgrammatik.", "/intent/presetKey");
	if (!intent.recipeId.empty () && !IsUuidV7 (intent.recipeId))
		return Invalid ("intent.recipeId ist keine UUIDv7.", "/intent/recipeId");
	if (intent.promptText.size () > 4000)
		return Invalid ("intent.promptText ist länger als 4000 Zeichen.", "/intent/promptText");

	if ((hasCamera || hasGeometry) && minor < 2)
		return Invalid ("camera und geometry sind erst ab contractVersion 1.2.0 ein Objekt.", "/camera");
	if (hasCamera) {
		const Status cameraStatus = ValidateCamera (camera, minor);
		if (!cameraStatus) return cameraStatus;
	}

	if (assets.empty () || assets.size () > 10)
		return Invalid ("assets enthält weniger als 1 oder mehr als 10 Einträge.", "/assets");

	std::set<std::string> seenRoles;
	std::set<std::string> seenPaths;
	const CaptureAsset* model = nullptr;
	for (std::size_t i = 0; i < assets.size (); ++i) {
		const CaptureAsset& asset = assets[i];
		const std::string at = "/assets/" + std::to_string (i);
		if (!IsKnownAssetRole (asset.role))
			return Invalid ("Unbekannte Assetrolle: " + asset.role, at + "/role");
		const bool isModel = asset.role == kModelRole;
		if (isModel && minor < 2)
			return Invalid ("Die Rolle model ist erst ab contractVersion 1.2.0 zulässig.", at + "/role");
		if (!seenRoles.insert (asset.role).second)
			return Invalid ("Je Rolle ist höchstens ein Eintrag zulässig: " + asset.role,
							at + "/role");
		if (!MatchesRelativePath (asset.path))
			return Invalid ("assets[].path ist kein gültiger relativer Pfad.", at + "/path");
		if (!seenPaths.insert (asset.path).second)
			return Invalid ("Doppelter Pfad im Manifest: " + asset.path, at + "/path");
		if (asset.status != "present" && asset.status != "planned" && asset.status != "unavailable")
			return Invalid ("assets[].status ist unbekannt.", at + "/status");
		if (asset.note.size () > 512)
			return Invalid ("assets[].note ist länger als 512 Zeichen.", at + "/note");
		// Seit 1.3.0 ist PNG das einzige Bildformat — auch bei `planned`, wenn ein Medientyp genannt ist.
		if (!isModel && minor >= 3 && !asset.mediaType.empty () && asset.mediaType != "image/png")
			return Invalid ("Ab contractVersion 1.3.0 ist jedes Bild image/png.", at + "/mediaType");

		if (asset.status == "present") {
			if (!IsMediaType (asset.mediaType))
				return Invalid ("assets[].mediaType fehlt oder ist kein IANA-Medientyp.",
								at + "/mediaType");
			if (asset.byteSize < 0 || asset.byteSize > kMaxSafeInteger)
				return Invalid ("assets[].byteSize liegt außerhalb des sicheren Ganzzahlbereichs.",
								at + "/byteSize");
			if (!MatchesSha256 (asset.sha256))
				return Invalid ("assets[].sha256 sind nicht 64 Hexziffern in Kleinschreibung.",
								at + "/sha256");
			if (isModel) {
				if (asset.mediaType != kModelMediaType)
					return Invalid ("Die Modelldatei ist model/gltf-binary.", at + "/mediaType");
				if (asset.hasImage)
					return Invalid ("Die Modelldatei trägt keinen image-Block.", at + "/image");
				model = &asset;
				continue;
			}
			if (!asset.hasImage)
				return Invalid ("Ein vorhandenes Asset braucht einen image-Block.", at + "/image");

			const CaptureImage& image = asset.image;
			if (image.width < 1 || image.width > 65536 || image.height < 1 || image.height > 65536)
				return Invalid ("assets[].image-Maße liegen außerhalb von 1 … 65536.", at + "/image");
			if (image.bitDepth != 8 && image.bitDepth != 16 && image.bitDepth != 32)
				return Invalid ("assets[].image.bitDepth ist weder 8 noch 16 noch 32.",
								at + "/image/bitDepth");
			if (image.sampleFormat != "uint" && image.sampleFormat != "float")
				return Invalid ("assets[].image.sampleFormat ist weder uint noch float.",
								at + "/image/sampleFormat");
			if (image.sampleFormat == "float" && image.bitDepth == 8)
				return Invalid ("float verlangt 16 oder 32 Bit je Kanal.", at + "/image/bitDepth");
			if (minor >= 3 && (image.sampleFormat != "uint" || image.bitDepth == 32))
				return Invalid ("Ab contractVersion 1.3.0 hat ein Bild 8 oder 16 Bit uint.", at + "/image");
			if (image.channels != "gray" && image.channels != "gray-alpha" &&
				image.channels != "rgb" && image.channels != "rgba")
				return Invalid ("assets[].image.channels ist unbekannt.", at + "/image/channels");
			const bool colorRole = IsColorAssetRole (asset.role);
			if (colorRole && image.colorSpace != "srgb" && image.colorSpace != "linear")
				return Invalid ("Farbrollen verlangen colorSpace srgb oder linear.",
								at + "/image/colorSpace");
			if (!colorRole && image.colorSpace != "non-color")
				return Invalid ("Datenrollen verlangen colorSpace non-color.",
								at + "/image/colorSpace");
		} else {
			if (!asset.sha256.empty () || asset.byteSize != 0)
				return Invalid ("Ein Asset ohne Datei darf weder sha256 noch byteSize führen.", at);
		}
	}

	// Was ein Capture mindestens trägt (Abschnitt 14): bis 1.5.x ein Bild, seit 1.6.0 ein Bild oder das
	// Modell — ohne Bild dann mit Kamera samt Bildgröße. Ein leerer Capture ist in keiner Version zulässig.
	const bool image = HasPresentImage ();
	if (!image && model == nullptr)
		return Invalid ("Mindestens ein Asset muss status present tragen.", "/assets");
	if (!image && minor < 6)
		return Invalid ("Das Modell allein ist erst ab contractVersion 1.6.0 ein Capture.", "/assets");
	if (!image && (!hasCamera || !camera.HasResolution ()))
		return Invalid ("Ein Capture nur mit Modell verlangt die Kamera mit Bildgröße.", "/camera/resolution");

	// `geometry` steht genau dann, wenn die Modelldatei vorhanden ist, und meint genau sie.
	if (model != nullptr && !hasGeometry)
		return Invalid ("Die Modelldatei verlangt den Block geometry.", "/geometry");
	if (model == nullptr && hasGeometry)
		return Invalid ("Der Block geometry verlangt eine vorhandene Modelldatei.", "/geometry");
	if (hasGeometry) {
		if (geometry.assetPath != model->path)
			return Invalid ("geometry.assetPath zeigt nicht auf die Modelldatei.", "/geometry/assetPath");
		const double scale = Canon (geometry.sourceUnitScaleToMeter, kRatio);
		if (!(scale > 0.0) || scale > 1000000.0)
			return Invalid ("geometry.units.sourceUnitScaleToMeter liegt außerhalb von (0, 10^6].",
							"/geometry/units/sourceUnitScaleToMeter");
		if (geometry.handedness != "right" && geometry.handedness != "left")
			return Invalid ("geometry.axes.handedness ist unbekannt.", "/geometry/axes/handedness");
		if (geometry.upAxis != "y" && geometry.upAxis != "z")
			return Invalid ("geometry.axes.upAxis ist unbekannt.", "/geometry/axes/upAxis");
		if (!AllFinite (geometry.origin))
			return Invalid ("geometry.origin enthält keine endliche Zahl.", "/geometry/origin");
	}

	const Result<std::string> hash = ContentHash ();
	if (!hash) return Status::Fail (hash.GetError ());
	return Status::Ok ();
}

Result<JsonPtr> CaptureManifest::ToJson () const
{
	const Status valid = Validate ();
	if (!valid) return Result<JsonPtr>::Fail (valid.GetError ());
	const Result<std::string> hash = ContentHash ();
	if (!hash) return Result<JsonPtr>::Fail (hash.GetError ());

	JsonPtr root = Json::MakeObject ();
	root->Set ("contract", Json::MakeString (kCaptureContract));
	root->Set ("contractVersion", Json::MakeString (contractVersion));
	root->Set ("captureId", Json::MakeString (captureId));
	root->Set ("createdAt", Json::MakeString (createdAt));

	JsonPtr host = Json::MakeObject ();
	host->Set ("key", Json::MakeString (source.hostKey));
	host->Set ("version", Json::MakeString (source.hostVersion));
	if (!source.hostBuild.empty ()) host->Set ("build", Json::MakeString (source.hostBuild));
	if (!source.capabilities.empty ()) {
		JsonPtr capabilities = Json::MakeObject ();
		for (const auto& [key, state] : source.capabilities) {
			JsonPtr entry = Json::MakeObject ();
			entry->Set ("state", Json::MakeString (state));
			capabilities->Set (key, entry);
		}
		host->Set ("capabilities", capabilities);
	}

	JsonPtr plugin = Json::MakeObject ();
	plugin->Set ("identifier", Json::MakeString (source.pluginIdentifier));
	plugin->Set ("version", Json::MakeString (source.pluginVersion));

	JsonPtr machine = Json::MakeObject ();
	machine->Set ("os", Json::MakeString (source.os));
	if (!source.osVersion.empty ()) machine->Set ("osVersion", Json::MakeString (source.osVersion));
	machine->Set ("architecture", Json::MakeString (source.architecture));

	JsonPtr sourceNode = Json::MakeObject ();
	sourceNode->Set ("host", host);
	sourceNode->Set ("plugin", plugin);
	sourceNode->Set ("machine", machine);
	if (!source.fileName.empty ()) sourceNode->Set ("fileName", Json::MakeString (source.fileName));
	root->Set ("source", sourceNode);

	JsonPtr project = Json::MakeObject ();
	project->Set ("platformProjectId", platformProjectId.empty ()
										   ? Json::MakeNull ()
										   : Json::MakeString (platformProjectId));
	project->Set ("sourceProjectKey",
				  sourceProjectKey.empty () ? Json::MakeNull () : Json::MakeString (sourceProjectKey));
	if (!projectDisplayName.empty ())
		project->Set ("displayName", Json::MakeString (projectDisplayName));
	root->Set ("project", project);

	JsonPtr view = Json::MakeObject ();
	view->Set ("sourceViewKey",
			   sourceViewKey.empty () ? Json::MakeNull () : Json::MakeString (sourceViewKey));
	if (!viewDisplayName.empty ()) view->Set ("displayName", Json::MakeString (viewDisplayName));
	root->Set ("view", view);

	JsonPtr intentNode = Json::MakeObject ();
	if (!intent.presetKey.empty ()) intentNode->Set ("presetKey", Json::MakeString (intent.presetKey));
	if (!intent.recipeId.empty ()) intentNode->Set ("recipeId", Json::MakeString (intent.recipeId));
	if (!intent.promptText.empty ())
		intentNode->Set ("promptText", Json::MakeString (intent.promptText));
	root->Set ("intent", intentNode);

	if (hasCamera) {
		JsonPtr node = Json::MakeObject ();
		node->Set ("space", Json::MakeString ("export"));
		node->Set ("projection", Json::MakeString (camera.projection));
		node->Set ("position", Vector (camera.position, kLength));
		node->Set ("direction", Vector (camera.direction, kDirection));
		node->Set ("up", Vector (camera.up, kDirection));
		if (camera.projection == "perspective") {
			JsonPtr fov = Json::MakeObject ();
			fov->Set ("axis", Json::MakeString (camera.fovAxis));
			fov->Set ("angle", Decimal (camera.fovAngle, kAngle));
			node->Set ("fieldOfView", fov);
		} else {
			JsonPtr extent = Json::MakeObject ();
			extent->Set ("halfWidth", Decimal (camera.halfWidth, kLength));
			extent->Set ("halfHeight", Decimal (camera.halfHeight, kLength));
			node->Set ("extent", extent);
		}
		JsonPtr clip = Json::MakeObject ();
		clip->Set ("near", Decimal (camera.clipNear, kLength));
		clip->Set ("far", camera.clipFar == 0.0 ? Json::MakeNull () : Decimal (camera.clipFar, kLength));
		node->Set ("clip", clip);
		if (camera.HasShift ()) {
			JsonPtr shift = Json::MakeObject ();
			shift->Set ("x", Decimal (camera.shiftX, kRatio));
			shift->Set ("y", Decimal (camera.shiftY, kRatio));
			node->Set ("shift", shift);
		}
		if (camera.HasResolution ()) {
			JsonPtr resolution = Json::MakeObject ();
			resolution->Set ("width", Json::MakeInt (camera.resolutionWidth));
			resolution->Set ("height", Json::MakeInt (camera.resolutionHeight));
			node->Set ("resolution", resolution);
		}
		root->Set ("camera", node);
	} else {
		root->Set ("camera", Json::MakeNull ());
	}

	if (hasGeometry) {
		JsonPtr node = Json::MakeObject ();
		node->Set ("assetPath", Json::MakeString (geometry.assetPath));
		JsonPtr units = Json::MakeObject ();
		units->Set ("sourceUnitScaleToMeter", Decimal (geometry.sourceUnitScaleToMeter, kRatio));
		node->Set ("units", units);
		JsonPtr axes = Json::MakeObject ();
		axes->Set ("handedness", Json::MakeString (geometry.handedness));
		axes->Set ("upAxis", Json::MakeString (geometry.upAxis));
		node->Set ("axes", axes);
		node->Set ("origin", Vector (geometry.origin, kLength));
		root->Set ("geometry", node);
	} else {
		root->Set ("geometry", Json::MakeNull ());
	}
	root->Set ("contentHash", Json::MakeString (hash.Value ()));

	JsonPtr assetArray = Json::MakeArray ();
	for (const CaptureAsset& asset : assets) {
		JsonPtr node = Json::MakeObject ();
		node->Set ("role", Json::MakeString (asset.role));
		node->Set ("path", Json::MakeString (asset.path));
		node->Set ("status", Json::MakeString (asset.status));
		if (asset.status == "present") {
			node->Set ("mediaType", Json::MakeString (asset.mediaType));
			node->Set ("byteSize", Json::MakeInt (asset.byteSize));
			node->Set ("sha256", Json::MakeString (asset.sha256));
			if (asset.role != kModelRole) {
				JsonPtr image = Json::MakeObject ();
				image->Set ("width", Json::MakeInt (asset.image.width));
				image->Set ("height", Json::MakeInt (asset.image.height));
				image->Set ("colorSpace", Json::MakeString (asset.image.colorSpace));
				image->Set ("bitDepth", Json::MakeInt (asset.image.bitDepth));
				image->Set ("sampleFormat", Json::MakeString (asset.image.sampleFormat));
				image->Set ("channels", Json::MakeString (asset.image.channels));
				node->Set ("image", image);
			}
		}
		if (!asset.note.empty ()) node->Set ("note", Json::MakeString (asset.note));
		assetArray->Append (node);
	}
	root->Set ("assets", assetArray);
	return Result<JsonPtr>::Ok (root);
}

namespace {

std::string Text (const JsonPtr& node, const char* key)
{
	const JsonPtr field = node ? node->Get (key) : nullptr;
	return field != nullptr && field->GetKind () == Json::Kind::String ? field->StringOr ("") : std::string ();
}

bool Vector3 (const JsonPtr& node, double out[3])
{
	if (node == nullptr || node->GetKind () != Json::Kind::Array || node->Items ().size () != 3) return false;
	for (int i = 0; i < 3; ++i) {
		if (node->Items ()[i]->GetKind () != Json::Kind::Number) return false;
		out[i] = node->Items ()[i]->NumberOr (0);
	}
	return true;
}

double Number (const JsonPtr& node, const char* key, double fallback = 0.0)
{
	const JsonPtr field = node ? node->Get (key) : nullptr;
	return field != nullptr && field->GetKind () == Json::Kind::Number ? field->NumberOr (fallback) : fallback;
}

} // namespace

Result<CaptureManifest> CaptureManifest::Parse (const std::string& text, const std::string& directory)
{
	const JsonPtr root = Json::Parse (text);
	const auto fail = [] (const std::string& message) {
		return Result<CaptureManifest>::Fail (errc::SchemaInvalid, message);
	};
	if (root == nullptr || root->GetKind () != Json::Kind::Object) return fail ("Das gespeicherte Manifest ist kein JSON-Objekt.");
	if (Text (root, "contract") != kCaptureContract) return fail ("Das gespeicherte Manifest hat einen anderen contract.");

	CaptureManifest m;
	m.contractVersion = Text (root, "contractVersion");
	m.captureId = Text (root, "captureId");
	m.createdAt = Text (root, "createdAt");

	const JsonPtr source = root->Get ("source");
	const JsonPtr host = source ? source->Get ("host") : nullptr;
	const JsonPtr plugin = source ? source->Get ("plugin") : nullptr;
	const JsonPtr machine = source ? source->Get ("machine") : nullptr;
	m.source.hostKey = Text (host, "key");
	m.source.hostVersion = Text (host, "version");
	m.source.hostBuild = Text (host, "build");
	if (const JsonPtr capabilities = host ? host->Get ("capabilities") : nullptr)
		for (const auto& [key, entry] : capabilities->Fields ()) m.source.capabilities.emplace_back (key, Text (entry, "state"));
	m.source.pluginIdentifier = Text (plugin, "identifier");
	m.source.pluginVersion = Text (plugin, "version");
	m.source.os = Text (machine, "os");
	m.source.osVersion = Text (machine, "osVersion");
	m.source.architecture = Text (machine, "architecture");
	m.source.fileName = Text (source, "fileName");

	const JsonPtr project = root->Get ("project");
	m.platformProjectId = Text (project, "platformProjectId");
	m.sourceProjectKey = Text (project, "sourceProjectKey");
	m.projectDisplayName = Text (project, "displayName");
	const JsonPtr view = root->Get ("view");
	m.sourceViewKey = Text (view, "sourceViewKey");
	m.viewDisplayName = Text (view, "displayName");
	const JsonPtr intentNode = root->Get ("intent");
	m.intent.presetKey = Text (intentNode, "presetKey");
	m.intent.recipeId = Text (intentNode, "recipeId");
	m.intent.promptText = Text (intentNode, "promptText");

	const JsonPtr camera = root->Get ("camera");
	if (camera != nullptr && camera->GetKind () == Json::Kind::Object) {
		m.hasCamera = true;
		CaptureCamera& c = m.camera;
		c.projection = Text (camera, "projection");
		if (!Vector3 (camera->Get ("position"), c.position) || !Vector3 (camera->Get ("direction"), c.direction) ||
			!Vector3 (camera->Get ("up"), c.up))
			return fail ("Die Kamera des gespeicherten Manifests ist unvollständig.");
		const JsonPtr fov = camera->Get ("fieldOfView");
		c.fovAxis = fov ? Text (fov, "axis") : std::string ("horizontal");
		c.fovAngle = Number (fov, "angle");
		const JsonPtr extent = camera->Get ("extent");
		c.halfWidth = Number (extent, "halfWidth");
		c.halfHeight = Number (extent, "halfHeight");
		const JsonPtr clip = camera->Get ("clip");
		c.clipNear = Number (clip, "near");
		c.clipFar = Number (clip, "far");
		const JsonPtr shift = camera->Get ("shift");
		c.shiftX = Number (shift, "x");
		c.shiftY = Number (shift, "y");
		const JsonPtr resolution = camera->Get ("resolution");
		c.resolutionWidth = static_cast<int> (Number (resolution, "width"));
		c.resolutionHeight = static_cast<int> (Number (resolution, "height"));
	}
	const JsonPtr geometry = root->Get ("geometry");
	if (geometry != nullptr && geometry->GetKind () == Json::Kind::Object) {
		m.hasGeometry = true;
		m.geometry.assetPath = Text (geometry, "assetPath");
		m.geometry.sourceUnitScaleToMeter = Number (geometry->Get ("units"), "sourceUnitScaleToMeter", 1.0);
		m.geometry.handedness = Text (geometry->Get ("axes"), "handedness");
		m.geometry.upAxis = Text (geometry->Get ("axes"), "upAxis");
		if (!Vector3 (geometry->Get ("origin"), m.geometry.origin))
			return fail ("Die Geometrie des gespeicherten Manifests ist unvollständig.");
	}

	const JsonPtr assets = root->Get ("assets");
	if (assets == nullptr || assets->GetKind () != Json::Kind::Array) return fail ("Das gespeicherte Manifest hat keine Dateien.");
	for (const JsonPtr& item : assets->Items ()) {
		CaptureAsset asset;
		asset.role = Text (item, "role");
		asset.path = Text (item, "path");
		asset.status = Text (item, "status");
		asset.mediaType = Text (item, "mediaType");
		asset.byteSize = item->Get ("byteSize") ? item->Get ("byteSize")->IntOr (0) : 0;
		asset.sha256 = Text (item, "sha256");
		asset.note = Text (item, "note");
		if (const JsonPtr image = item->Get ("image")) {
			asset.hasImage = true;
			asset.image.width = static_cast<int> (Number (image, "width"));
			asset.image.height = static_cast<int> (Number (image, "height"));
			asset.image.colorSpace = Text (image, "colorSpace");
			asset.image.bitDepth = static_cast<int> (Number (image, "bitDepth"));
			asset.image.sampleFormat = Text (image, "sampleFormat");
			asset.image.channels = Text (image, "channels");
		}
		if (asset.status == "present" && !directory.empty ()) asset.localPath = directory + "/" + asset.path;
		m.assets.push_back (asset);
	}
	const Status valid = m.Validate ();
	if (!valid) return Result<CaptureManifest>::Fail (valid.GetError ());
	return Result<CaptureManifest>::Ok (m);
}

Result<std::string> CaptureManifest::Serialize () const
{
	const Result<JsonPtr> json = ToJson ();
	if (!json) return Result<std::string>::Fail (json.GetError ());
	return Result<std::string>::Ok (json.Value ()->SerializePretty ());
}

} // namespace rtx
