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

bool RemoveFile (const std::string& path)
{
	std::error_code code;
	return std::filesystem::remove (FsPath (path), code) && !code;
}

} // namespace rtx
