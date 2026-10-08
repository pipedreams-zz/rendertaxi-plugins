#include "rtx/Platform.hpp"

#include <cstdlib>
#include <system_error>

#if defined (_WIN32)
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <shlobj.h>
#endif

#if defined (__APPLE__)
#include <CoreFoundation/CoreFoundation.h>
#endif

namespace rtx {

#if defined (_WIN32)

std::wstring Widen (const std::string& utf8)
{
	if (utf8.empty ()) return {};
	const int length = MultiByteToWideChar (CP_UTF8, MB_ERR_INVALID_CHARS, utf8.data (),
											static_cast<int> (utf8.size ()), nullptr, 0);
	if (length <= 0) return {};
	std::wstring wide (static_cast<std::size_t> (length), L'\0');
	MultiByteToWideChar (CP_UTF8, MB_ERR_INVALID_CHARS, utf8.data (),
						 static_cast<int> (utf8.size ()), &wide[0], length);
	return wide;
}

std::string Narrow (const std::wstring& wide)
{
	if (wide.empty ()) return {};
	const int length = WideCharToMultiByte (CP_UTF8, 0, wide.data (), static_cast<int> (wide.size ()),
											nullptr, 0, nullptr, nullptr);
	if (length <= 0) return {};
	std::string utf8 (static_cast<std::size_t> (length), '\0');
	WideCharToMultiByte (CP_UTF8, 0, wide.data (), static_cast<int> (wide.size ()), &utf8[0], length,
						 nullptr, nullptr);
	return utf8;
}

namespace {

/** `%LOCALAPPDATA%` über den Known-Folder-Aufruf, nicht über die Umgebung. */
std::string LocalAppData ()
{
	PWSTR folder = nullptr;
	std::string out;
	if (SUCCEEDED (SHGetKnownFolderPath (FOLDERID_LocalAppData, 0, nullptr, &folder)) &&
		folder != nullptr)
		out = Narrow (folder);
	CoTaskMemFree (folder);
	if (out.empty ()) {
		// Ohne Profil (etwa ein Dienstkonto) bleibt die Umgebung; sonst der
		// Arbeitsordner, wie unter macOS ohne `HOME`.
		const wchar_t* env = _wgetenv (L"LOCALAPPDATA");
		out = env != nullptr ? Narrow (env) : std::string (".");
	}
	return out;
}

} // namespace

std::string AppDataDirectory ()
{
	return LocalAppData () + "\\rendertaxi\\archicad";
}

std::string LogDirectory ()
{
	return AppDataDirectory ();
}

std::filesystem::path FsPath (const std::string& path)
{
	return std::filesystem::path (Widen (path));
}

std::FILE* OpenFile (const std::string& path, const char* mode)
{
	const std::wstring widePath = Widen (path);
	const std::wstring wideMode = Widen (mode);
	if (widePath.empty ()) return nullptr;
	return _wfopen (widePath.c_str (), wideMode.c_str ());
}

bool RenameReplacing (const std::string& from, const std::string& to)
{
	const std::wstring wideFrom = Widen (from);
	const std::wstring wideTo = Widen (to);
	if (wideFrom.empty () || wideTo.empty ()) return false;
	return MoveFileExW (wideFrom.c_str (), wideTo.c_str (),
						MOVEFILE_REPLACE_EXISTING | MOVEFILE_COPY_ALLOWED) != 0;
}

std::string NativePath (const std::string& path)
{
	std::string out = path;
	for (char& c : out)
		if (c == '/') c = '\\';
	return out;
}

#else

namespace {

std::string Home ()
{
	const char* home = std::getenv ("HOME");
	return home != nullptr ? std::string (home) : std::string (".");
}

} // namespace

std::string AppDataDirectory ()
{
	return Home () + "/Library/Application Support/rendertaxi/archicad";
}

std::string LogDirectory ()
{
	return Home () + "/Library/Logs/rendertaxi";
}

std::filesystem::path FsPath (const std::string& path)
{
	return std::filesystem::path (path);
}

std::FILE* OpenFile (const std::string& path, const char* mode)
{
	return std::fopen (path.c_str (), mode);
}

bool RenameReplacing (const std::string& from, const std::string& to)
{
	return std::rename (from.c_str (), to.c_str ()) == 0;
}

std::string NativePath (const std::string& path)
{
	return path;
}

#endif

std::string NormalizeNfc (const std::string& utf8)
{
	if (utf8.empty ()) return utf8;
	bool ascii = true;
	for (const char c : utf8)
		if (static_cast<unsigned char> (c) >= 0x80) ascii = false;
	if (ascii) return utf8;  // ASCII ist in jeder Normalform gleich.
#if defined (__APPLE__)
	CFStringRef source = CFStringCreateWithBytes (kCFAllocatorDefault, reinterpret_cast<const UInt8*> (utf8.data ()),
												  static_cast<CFIndex> (utf8.size ()), kCFStringEncodingUTF8, false);
	if (source == nullptr) return utf8;
	CFMutableStringRef text = CFStringCreateMutableCopy (kCFAllocatorDefault, 0, source);
	CFRelease (source);
	if (text == nullptr) return utf8;
	CFStringNormalize (text, kCFStringNormalizationFormC);
	const CFIndex length = CFStringGetLength (text);
	CFIndex bytes = 0;
	CFStringGetBytes (text, CFRangeMake (0, length), kCFStringEncodingUTF8, 0, false, nullptr, 0, &bytes);
	std::string out (static_cast<std::size_t> (bytes), '\0');
	if (bytes > 0)
		CFStringGetBytes (text, CFRangeMake (0, length), kCFStringEncodingUTF8, 0, false,
						  reinterpret_cast<UInt8*> (&out[0]), bytes, nullptr);
	CFRelease (text);
	return out;
#elif defined (_WIN32)
	const std::wstring wide = Widen (utf8);
	if (wide.empty ()) return utf8;
	int length = NormalizeString (NormalizationC, wide.data (), static_cast<int> (wide.size ()), nullptr, 0);
	for (int attempt = 0; attempt < 3 && length > 0; ++attempt) {
		std::wstring out (static_cast<std::size_t> (length), L'\0');
		const int written =
			NormalizeString (NormalizationC, wide.data (), static_cast<int> (wide.size ()), &out[0], length);
		if (written > 0) {
			out.resize (static_cast<std::size_t> (written));
			return Narrow (out);
		}
		if (GetLastError () != ERROR_INSUFFICIENT_BUFFER) break;
		length = -written;
	}
	return utf8;
#else
	return utf8;
#endif
}

bool RemoveFile (const std::string& path)
{
	std::error_code code;
	return std::filesystem::remove (FsPath (path), code) && !code;
}

} // namespace rtx
