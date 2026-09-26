#include "rtx/Log.hpp"

#include <cstdio>
#include <mutex>
#include <vector>

#include "rtx/Ids.hpp"

namespace rtx {
namespace {

std::mutex& LogMutex ()
{
	static std::mutex mutex;
	return mutex;
}

std::string& LogPath ()
{
	static std::string path;
	return path;
}

/** Feldnamen, deren Wert niemals in eine Zeile gehört. */
const char* const kSecretKeys[] = {"access_token", "refresh_token", "device_code", "user_code",
								   "id_token",     "client_secret", "Authorization"};

void ReplaceAll (std::string& text, const std::string& needle, const std::string& replacement)
{
	if (needle.empty ()) return;
	std::size_t at = 0;
	while ((at = text.find (needle, at)) != std::string::npos) {
		text.replace (at, needle.size (), replacement);
		at += replacement.size ();
	}
}

} // namespace

std::string SafeUrl (const std::string& url)
{
	const std::size_t scheme = url.find ("://");
	if (scheme == std::string::npos) return "[redigiert]";
	const std::size_t hostStart = scheme + 3;
	std::size_t end = url.size ();
	for (std::size_t i = hostStart; i < url.size (); ++i) {
		if (url[i] == '?' || url[i] == '#') { end = i; break; }
	}
	std::string trimmed = url.substr (0, end);
	// Nutzerteil `user:pass@host` entfernen.
	const std::size_t at = trimmed.find ('@', hostStart);
	if (at != std::string::npos) trimmed = trimmed.substr (0, hostStart) + trimmed.substr (at + 1);
	if (end < url.size ()) trimmed += "?[redigiert]";
	return trimmed;
}

std::string Redact (const std::string& line)
{
	std::string out = line;

	// 1. Bearer-Kopfzeilen.
	std::size_t at = 0;
	while ((at = out.find ("Bearer ", at)) != std::string::npos) {
		std::size_t end = at + 7;
		while (end < out.size () && out[end] != ' ' && out[end] != '"' && out[end] != '\n') ++end;
		out.replace (at + 7, end - (at + 7), "[redigiert]");
		at += 7;
	}

	// 2. Bekannte Geheimnisfelder in JSON- oder Schlüssel-Wert-Schreibweise.
	for (const char* key : kSecretKeys) {
		const std::string name (key);
		std::size_t pos = 0;
		while ((pos = out.find (name, pos)) != std::string::npos) {
			std::size_t cursor = pos + name.size ();
			while (cursor < out.size () && (out[cursor] == '"' || out[cursor] == ' ' ||
											out[cursor] == ':' || out[cursor] == '=')) ++cursor;
			std::size_t end = cursor;
			while (end < out.size () && out[end] != '"' && out[end] != ',' && out[end] != ' ' &&
				   out[end] != '}' && out[end] != '\n') ++end;
			if (end > cursor) out.replace (cursor, end - cursor, "[redigiert]");
			pos = cursor + 11;
			if (pos > out.size ()) break;
		}
	}

	// 3. URLs mit Abfrageteil — jede signierte Adresse fällt darunter.
	for (const char* scheme : {"https://", "http://"}) {
		std::size_t pos = 0;
		while ((pos = out.find (scheme, pos)) != std::string::npos) {
			std::size_t end = pos;
			while (end < out.size () && out[end] != ' ' && out[end] != '"' && out[end] != '\n') ++end;
			const std::string url = out.substr (pos, end - pos);
			const std::string safe = SafeUrl (url);
			out.replace (pos, url.size (), safe);
			pos += safe.size ();
		}
	}

	ReplaceAll (out, "\n", " ");
	ReplaceAll (out, "\r", " ");
	return out;
}

void SetLogFile (const std::string& path)
{
	std::lock_guard<std::mutex> guard (LogMutex ());
	LogPath () = path;
}

std::string LogFilePath ()
{
	std::lock_guard<std::mutex> guard (LogMutex ());
	return LogPath ();
}

void LogLine (const std::string& line)
{
	const std::string safe = Redact (line);
	std::lock_guard<std::mutex> guard (LogMutex ());
	if (LogPath ().empty ()) return;
	std::FILE* file = std::fopen (LogPath ().c_str (), "ab");
	if (file == nullptr) return;
	std::fprintf (file, "%s %s\n", NowTimestampUtc ().c_str (), safe.c_str ());
	std::fclose (file);
}

} // namespace rtx
