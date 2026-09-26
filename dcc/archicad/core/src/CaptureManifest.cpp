#include "rtx/CaptureManifest.hpp"

#include <algorithm>
#include <set>

#include "rtx/Canonical.hpp"
#include "rtx/Ids.hpp"

namespace rtx {
namespace {

constexpr std::int64_t kMaxSafeInteger = 9007199254740991LL;

const char* const kRoles[] = {"viewport", "beauty",    "depth",         "normal",  "albedo",
							  "mask",     "object-id", "material-id",   "ambient-occlusion",
							  "cryptomatte"};

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

Result<std::string> CaptureManifest::ContentHash () const
{
	std::vector<ContentHashAsset> present;
	for (const CaptureAsset& asset : assets) {
		if (asset.status != "present") continue;
		present.push_back ({asset.role, asset.path, asset.sha256});
	}
	return CaptureContentHash (present);
}

Status CaptureManifest::Validate () const
{
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

	if (assets.empty () || assets.size () > 10)
		return Invalid ("assets enthält weniger als 1 oder mehr als 10 Einträge.", "/assets");

	std::set<std::string> seenRoles;
	std::set<std::string> seenPaths;
	int presentCount = 0;
	for (std::size_t i = 0; i < assets.size (); ++i) {
		const CaptureAsset& asset = assets[i];
		const std::string at = "/assets/" + std::to_string (i);
		if (!IsKnownAssetRole (asset.role))
			return Invalid ("Unbekannte Assetrolle: " + asset.role, at + "/role");
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

		if (asset.status == "present") {
			++presentCount;
			if (!IsMediaType (asset.mediaType))
				return Invalid ("assets[].mediaType fehlt oder ist kein IANA-Medientyp.",
								at + "/mediaType");
			if (asset.byteSize < 0 || asset.byteSize > kMaxSafeInteger)
				return Invalid ("assets[].byteSize liegt außerhalb des sicheren Ganzzahlbereichs.",
								at + "/byteSize");
			if (!MatchesSha256 (asset.sha256))
				return Invalid ("assets[].sha256 sind nicht 64 Hexziffern in Kleinschreibung.",
								at + "/sha256");
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
	if (presentCount == 0)
		return Invalid ("Mindestens ein Asset muss status present tragen.", "/assets");

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
	root->Set ("contractVersion", Json::MakeString (kCaptureContractVersion));
	root->Set ("captureId", Json::MakeString (captureId));
	root->Set ("createdAt", Json::MakeString (createdAt));

	JsonPtr host = Json::MakeObject ();
	host->Set ("key", Json::MakeString (source.hostKey));
	host->Set ("version", Json::MakeString (source.hostVersion));
	if (!source.hostBuild.empty ()) host->Set ("build", Json::MakeString (source.hostBuild));

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

	root->Set ("camera", Json::MakeNull ());
	root->Set ("geometry", Json::MakeNull ());
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
			JsonPtr image = Json::MakeObject ();
			image->Set ("width", Json::MakeInt (asset.image.width));
			image->Set ("height", Json::MakeInt (asset.image.height));
			image->Set ("colorSpace", Json::MakeString (asset.image.colorSpace));
			image->Set ("bitDepth", Json::MakeInt (asset.image.bitDepth));
			image->Set ("sampleFormat", Json::MakeString (asset.image.sampleFormat));
			image->Set ("channels", Json::MakeString (asset.image.channels));
			node->Set ("image", image);
		}
		if (!asset.note.empty ()) node->Set ("note", Json::MakeString (asset.note));
		assetArray->Append (node);
	}
	root->Set ("assets", assetArray);
	return Result<JsonPtr>::Ok (root);
}

Result<std::string> CaptureManifest::Serialize () const
{
	const Result<JsonPtr> json = ToJson ();
	if (!json) return Result<std::string>::Fail (json.GetError ());
	return Result<std::string>::Ok (json.Value ()->SerializePretty ());
}

} // namespace rtx
