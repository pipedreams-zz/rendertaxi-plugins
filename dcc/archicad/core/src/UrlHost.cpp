#include "rtx/UrlHost.hpp"

#include <algorithm>
#include <cctype>

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

#elif defined (_WIN32)

// Unter Windows beantwortet `WinHttpCrackUrl` dieselbe Frage — der Parser, mit
// dem WinHTTP selbst Adressen zerlegt. Er kennt nur `http` und `https`; jedes
// andere Schema ist damit ungültig und wird abgelehnt, wie unter macOS über
// die Schemaprüfung in `IsTokenSafeBaseUrl`.

#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <winhttp.h>

#include "rtx/Platform.hpp"

namespace rtx {
namespace {

std::string ToLower (std::string value)
{
	std::transform (value.begin (), value.end (), value.begin (),
					[] (unsigned char c) { return static_cast<char> (std::tolower (c)); });
	return value;
}

/**
 * Zeichen, die in keiner gültigen URL stehen (RFC 3986): Leer- und
 * Steuerzeichen und die ausdrücklich ausgeschlossenen `\ " < > ^ ` { | }`.
 * CFURL lehnt eine solche Adresse ab; `WinHttpCrackUrl` ist nachsichtiger
 * (etwa bei `\`, das es als Pfadtrenner liest). Damit beide Plattformen
 * dieselbe Menge annehmen, wird hier vorher abgelehnt — das ist keine eigene
 * Zerlegung, sondern die Zeichenmenge, die der Parser gar nicht sehen soll.
 */
bool HasForbiddenCharacter (const std::string& url)
{
	for (const char raw : url) {
		const unsigned char c = static_cast<unsigned char> (raw);
		if (c <= 0x20 || c == 0x7F) return true;
		switch (c) {
			case '\\': case '"': case '<': case '>': case '^': case '`': case '{': case '|': case '}':
				return true;
			default:
				break;
		}
	}
	return false;
}

} // namespace

UrlParts ParseUrl (const std::string& url)
{
	UrlParts parts;
	if (url.empty () || HasForbiddenCharacter (url)) return parts;

	const std::wstring wide = Widen (url);
	if (wide.empty ()) return parts;

	// Längen `-1`: der Parser liefert Zeiger in die Eingabe, statt zu kopieren.
	URL_COMPONENTS components = {};
	components.dwStructSize = sizeof components;
	components.dwSchemeLength = static_cast<DWORD> (-1);
	components.dwHostNameLength = static_cast<DWORD> (-1);
	components.dwUserNameLength = static_cast<DWORD> (-1);
	components.dwPasswordLength = static_cast<DWORD> (-1);
	components.dwUrlPathLength = static_cast<DWORD> (-1);
	components.dwExtraInfoLength = static_cast<DWORD> (-1);
	if (!WinHttpCrackUrl (wide.c_str (), static_cast<DWORD> (wide.size ()), 0, &components))
		return parts;

	if (components.lpszScheme == nullptr || components.dwSchemeLength == 0) return parts;
	parts.scheme = ToLower (Narrow (std::wstring (components.lpszScheme, components.dwSchemeLength)));

	if (components.lpszHostName == nullptr || components.dwHostNameLength == 0) return parts;
	std::wstring host (components.lpszHostName, components.dwHostNameLength);
	// Eine IPv6-Adresse kommt mit Klammern; CFURL liefert sie ohne. Verglichen
	// wird ohne, auf Gleichheit.
	if (host.size () >= 2 && host.front () == L'[' && host.back () == L']')
		host = host.substr (1, host.size () - 2);
	parts.host = ToLower (Narrow (host));

	// **Benutzerangabe:** Name oder Passwort vom Parser — und zusätzlich jedes
	// `@` in der Autorität, wie der Parser sie abgegrenzt hat (zwischen `://`
	// und dem Pfad). Das fängt auch die leere Angabe `http://@host` ab.
	parts.hasUserInfo = components.dwUserNameLength > 0 || components.dwPasswordLength > 0;
	const std::size_t authorityStart = wide.find (L"://");
	if (authorityStart != std::wstring::npos) {
		const std::size_t from = authorityStart + 3;
		std::size_t to = wide.size ();
		if (components.lpszUrlPath != nullptr && components.dwUrlPathLength > 0)
			to = static_cast<std::size_t> (components.lpszUrlPath - wide.c_str ());
		else if (components.lpszExtraInfo != nullptr && components.dwExtraInfoLength > 0)
			to = static_cast<std::size_t> (components.lpszExtraInfo - wide.c_str ());
		if (to > from && wide.substr (from, to - from).find (L'@') != std::wstring::npos)
			parts.hasUserInfo = true;
	}

	parts.valid = !parts.scheme.empty () && !parts.host.empty ();
	return parts;
}

} // namespace rtx

#else

namespace rtx {

UrlParts ParseUrl (const std::string&)
{
	// Ohne Systemparser wird **nichts** angenommen. Lieber eine Adresse zu
	// viel abgelehnt als ein Token zu viel gesendet.
	return UrlParts {};
}

} // namespace rtx

#endif
