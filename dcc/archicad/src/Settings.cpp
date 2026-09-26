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

std::string LogPath ()
{
	const std::string directory = rtx::LogDirectory ();
	rtx::EnsureDirectory (directory);
	return directory + "/archicad-addon.log";
}

} // namespace rtxaddon
