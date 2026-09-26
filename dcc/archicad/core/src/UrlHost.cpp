#include "rtx/UrlHost.hpp"

#include <algorithm>

#if defined (__APPLE__)

#include <CoreFoundation/CoreFoundation.h>

namespace rtx {
namespace {

/** Hält eine CoreFoundation-Referenz und gibt sie am Blockende frei. */
template <typename T>
class CFHolder final {
public:
	explicit CFHolder (T value) : held (value) {}
	~CFHolder () { if (held != nullptr) CFRelease (held); }
	T Get () const { return held; }
	explicit operator bool () const { return held != nullptr; }

	CFHolder (const CFHolder&) = delete;
	CFHolder& operator= (const CFHolder&) = delete;

private:
	T held;
};

std::string Utf8 (CFStringRef text)
{
	if (text == nullptr) return {};
	const CFIndex length = CFStringGetLength (text);
	const CFIndex capacity = CFStringGetMaximumSizeForEncoding (length, kCFStringEncodingUTF8) + 1;
	std::string out (static_cast<std::size_t> (capacity), '\0');
	if (!CFStringGetCString (text, &out[0], capacity, kCFStringEncodingUTF8)) return {};
	out.resize (std::char_traits<char>::length (out.c_str ()));
	return out;
}

std::string ToLower (std::string value)
{
	std::transform (value.begin (), value.end (), value.begin (),
					[] (unsigned char c) { return static_cast<char> (std::tolower (c)); });
	return value;
}

} // namespace

UrlParts ParseUrl (const std::string& url)
{
	UrlParts parts;
	if (url.empty ()) return parts;

	CFHolder<CFURLRef> parsed (CFURLCreateWithBytes (
		kCFAllocatorDefault, reinterpret_cast<const UInt8*> (url.data ()),
		static_cast<CFIndex> (url.size ()), kCFStringEncodingUTF8, nullptr));
	if (!parsed) return parts;
	// Eine relative Adresse hat keine Autorität und ist hier nie richtig.
	if (!CFURLCanBeDecomposed (parsed.Get ())) return parts;

	CFHolder<CFStringRef> scheme (CFURLCopyScheme (parsed.Get ()));
	if (!scheme) return parts;
	parts.scheme = ToLower (Utf8 (scheme.Get ()));

	CFHolder<CFStringRef> host (CFURLCopyHostName (parsed.Get ()));
	if (!host) return parts;
	parts.host = ToLower (Utf8 (host.Get ()));

	// **Benutzerangabe:** `CFURLCopyUserName` liefert sie, wenn eine da ist.
	// Ein Passwort ohne Benutzernamen gibt es nicht, geprüft wird trotzdem
	// beides.
	CFHolder<CFStringRef> user (CFURLCopyUserName (parsed.Get ()));
	CFHolder<CFStringRef> password (CFURLCopyPassword (parsed.Get ()));
	parts.hasUserInfo = user || password;

	parts.valid = !parts.scheme.empty () && !parts.host.empty ();
	return parts;
}

} // namespace rtx

#else

namespace rtx {

UrlParts ParseUrl (const std::string&)
{
	// Ohne Systemparser wird **nichts** angenommen. Lieber eine Adresse zu
	// viel abgelehnt als ein Token zu viel gesendet; Windows ist in Issue #20
	// ohnehin Nicht-Ziel.
	return UrlParts {};
}

} // namespace rtx

#endif
