#include "Settings.hpp"

#include "Version.hpp"
#include "rtx/Json.hpp"
#include "rtx/Platform.hpp"
#include "rtx/TransferStore.hpp"

namespace rtxaddon {
namespace {

/** macOS: `~/Library/Application Support/…`, Windows: `%LOCALAPPDATA%\…` (`rtx/Platform.hpp`). */
std::string ConfigPath ()
{
	return rtx::AppDataDirectory () + "/settings.json";
}

std::string Trim (std::string url)
{
	while (!url.empty () && (url.back () == '/' || url.back () == ' ')) url.pop_back ();
	while (!url.empty () && url.front () == ' ') url.erase (url.begin ());
	return url;
}

/** Die Datei als Baum; ein leeres Objekt, wenn es sie noch nicht gibt. */
rtx::JsonPtr ReadSettings ()
{
	std::string content;
	if (rtx::ReadTextFile (ConfigPath (), content)) {
		rtx::JsonPtr node = rtx::Json::Parse (content);
		if (node != nullptr && node->GetKind () == rtx::Json::Kind::Object) return node;
	}
	return rtx::Json::MakeObject ();
}

/** Schreibt den Baum zurück — **ohne** die anderen Einstellungen zu verlieren. */
bool WriteSettings (const rtx::JsonPtr& node)
{
	const std::string path = ConfigPath ();
	if (!rtx::EnsureDirectory (path.substr (0, path.find_last_of ('/')))) return false;
	return rtx::WriteTextFile (path, node->SerializePretty ());
}

} // namespace

std::string ServerUrl ()
{
	const rtx::JsonPtr field = ReadSettings ()->Get ("server");
	if (field != nullptr) {
		const std::string url = Trim (field->StringOr (""));
		if (url.rfind ("https://", 0) == 0 || url.rfind ("http://", 0) == 0) return url;
	}
	return RTX_DEFAULT_SERVER;
}

bool SetServerUrl (const std::string& url)
{
	const std::string trimmed = Trim (url);
	if (trimmed.rfind ("https://", 0) != 0 && trimmed.rfind ("http://", 0) != 0) return false;
	rtx::JsonPtr node = ReadSettings ();
	node->Set ("server", rtx::Json::MakeString (trimmed));
	return WriteSettings (node);
}

std::string FrameSize ()
{
	const rtx::JsonPtr field = ReadSettings ()->Get ("frameSize");
	if (field != nullptr) {
		const std::string value = field->StringOr ("");
		// **Nur bekannte Werte.** Was hier steht, geht als `size` an den
		// Server; ein unbekannter Wert wäre ein `400` statt einer Vorgabe.
		if (value == "capture" || value == "canvas-default") return value;
	}
	return "canvas-default";
}

bool SetFrameSize (const std::string& value)
{
	if (value != "capture" && value != "canvas-default") return false;
	rtx::JsonPtr node = ReadSettings ();
	node->Set ("frameSize", rtx::Json::MakeString (value));
	return WriteSettings (node);
}

namespace {

bool Flag (const rtx::JsonPtr& node, const char* key, bool fallback)
{
	const rtx::JsonPtr field = node->Get (key);
	return field != nullptr && field->GetKind () == rtx::Json::Kind::Bool ? field->BoolOr (fallback) : fallback;
}

} // namespace

WayChoice Ways ()
{
	const rtx::JsonPtr node = ReadSettings ();
	WayChoice ways;
	ways.image = Flag (node, "sendImage", true);
	ways.model = Flag (node, "sendModel", false);
	if (!ways.image && !ways.model) ways.image = true;
	return ways;
}

bool SetWays (const WayChoice& ways)
{
	rtx::JsonPtr node = ReadSettings ();
	node->Set ("sendImage", rtx::Json::MakeBool (ways.image));
	node->Set ("sendModel", rtx::Json::MakeBool (ways.model));
	return WriteSettings (node);
}

bool ExtraCameras ()
{
	return Flag (ReadSettings (), "extraCameras", false);
}

bool SetExtraCameras (bool value)
{
	rtx::JsonPtr node = ReadSettings ();
	node->Set ("extraCameras", rtx::Json::MakeBool (value));
	return WriteSettings (node);
}

std::vector<std::string> CameraViews (const std::string& projectKey)
{
	std::vector<std::string> guids;
	const rtx::JsonPtr all = ReadSettings ()->Get ("cameraViews");
	const rtx::JsonPtr list = all != nullptr ? all->Get (projectKey) : nullptr;
	if (list == nullptr || list->GetKind () != rtx::Json::Kind::Array) return guids;
	for (const rtx::JsonPtr& item : list->Items ())
		if (item->GetKind () == rtx::Json::Kind::String && !item->StringOr ("").empty ())
			guids.push_back (item->StringOr (""));
	return guids;
}

bool SetCameraViews (const std::string& projectKey, const std::vector<std::string>& guids)
{
	if (projectKey.empty ()) return false;
	rtx::JsonPtr node = ReadSettings ();
	rtx::JsonPtr all = node->Get ("cameraViews");
	if (all == nullptr || all->GetKind () != rtx::Json::Kind::Object) all = rtx::Json::MakeObject ();
	rtx::JsonPtr list = rtx::Json::MakeArray ();
	for (const std::string& guid : guids) list->Append (rtx::Json::MakeString (guid));
	all->Set (projectKey, list);
	node->Set ("cameraViews", all);
	return WriteSettings (node);
}

std::string LogPath ()
{
	const std::string directory = rtx::LogDirectory ();
	rtx::EnsureDirectory (directory);
	return directory + "/archicad-addon.log";
}

} // namespace rtxaddon
