// Die Plattformgrenze: Ablageorte, UTF-8-Pfade, Adresszerlegung, Zuschnitt
// und der Schlüsselspeicher des Systems (Issue #161).
//
// Die meisten Prüfungen laufen auf **beiden** Systemen und halten damit fest,
// dass macOS und Windows dieselben Regeln haben. Die Prüfung des Credential
// Managers läuft nur unter Windows; die Keychain wird im Testlauf bewusst nicht
// angefasst, weil er sonst den Schlüsselbund des Entwicklers beschriebe.
#include "Testing.hpp"

#include <cstdlib>
#include <filesystem>
#include <string>

#include "rtx/ImageCrop.hpp"
#include "rtx/ImageFile.hpp"
#include "rtx/Ids.hpp"
#include "rtx/Platform.hpp"
#include "rtx/PluginApi.hpp"
#include "rtx/Sha256.hpp"
#include "rtx/TokenStore.hpp"
#include "rtx/TransferStore.hpp"
#include "rtx/UrlHost.hpp"

#if defined (_WIN32)
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <wincred.h>
// `windows.h` macht aus `RemoveDirectory` ein Makro auf `RemoveDirectoryA`.
#undef RemoveDirectory
#endif

using namespace rtx;

namespace {

bool EndsWith (const std::string& text, const std::string& tail)
{
	return text.size () >= tail.size () && text.compare (text.size () - tail.size (), tail.size (), tail) == 0;
}

/** Ein gültiges PNG mit 2 x 2 Pixeln, 8 Bit RGBA (wie in `CoreTest.cpp`). */
std::string TinyPng ()
{
	static const unsigned char tiny[] = {
		0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0x00, 0x00, 0x00, 0x0D, 0x49, 0x48,
		0x44, 0x52, 0x00, 0x00, 0x00, 0x02, 0x00, 0x00, 0x00, 0x02, 0x08, 0x06, 0x00, 0x00,
		0x00, 0x72, 0xB6, 0x0D, 0x24, 0x00, 0x00, 0x00, 0x16, 0x49, 0x44, 0x41, 0x54, 0x78,
		0x01, 0x01, 0x0B, 0x00, 0xF4, 0xFF, 0x00, 0xFF, 0x00, 0x00, 0xFF, 0x00, 0xFF, 0x00,
		0x00, 0xFF, 0xFF, 0x0B, 0xF8, 0x02, 0xFE, 0x33, 0x6C, 0x6A, 0x2A, 0x00, 0x00, 0x00,
		0x00, 0x49, 0x45, 0x4E, 0x44, 0xAE, 0x42, 0x60, 0x82};
	return std::string (reinterpret_cast<const char*> (tiny), sizeof tiny);
}

#if defined (_WIN32)
/** Steht `needle` in irgendeiner Datei unter `directory`? */
bool AnyFileContains (const std::string& directory, const std::string& needle)
{
	std::error_code code;
	const std::filesystem::path root = FsPath (directory);
	if (!std::filesystem::exists (root, code)) return false;
	for (auto it = std::filesystem::recursive_directory_iterator (root, code);
		 !code && it != std::filesystem::recursive_directory_iterator (); it.increment (code)) {
		if (!it->is_regular_file (code)) continue;
		std::string content;
		const std::string file = Narrow (it->path ().wstring ());
		if (ReadTextFile (file, content) && content.find (needle) != std::string::npos) return true;
	}
	return false;
}
#endif

} // namespace

// --- Ablageorte --------------------------------------------------------------

RTX_TEST (AblageorteFolgenDerPlattform)
{
	const std::string appData = AppDataDirectory ();
	RTX_CHECK_EQ (TransferStore::DefaultPath (), appData + "/transfers.json");
	RTX_CHECK_EQ (TransferStore::DefaultWorkDirectory (), appData + "/captures");
#if defined (_WIN32)
	// `%LOCALAPPDATA%\rendertaxi\archicad\` für Zustand und Protokoll.
	RTX_CHECK (EndsWith (appData, "\\rendertaxi\\archicad"));
	const wchar_t* local = _wgetenv (L"LOCALAPPDATA");
	RTX_CHECK (local != nullptr);
	if (local != nullptr) RTX_CHECK_EQ (appData, Narrow (local) + "\\rendertaxi\\archicad");
	RTX_CHECK_EQ (LogDirectory (), appData);
	RTX_CHECK_EQ (NativePath ("C:/a/b.png"), std::string ("C:\\a\\b.png"));
#else
	RTX_CHECK (EndsWith (appData, "/Library/Application Support/rendertaxi/archicad"));
	RTX_CHECK (EndsWith (LogDirectory (), "/Library/Logs/rendertaxi"));
	RTX_CHECK_EQ (NativePath ("/a/b.png"), std::string ("/a/b.png"));
#endif
}

RTX_TEST (PfadeSindUtf8AufBeidenSystemen)
{
	// Ein Benutzername mit Umlaut steht unter Windows mitten im Pfad. Unter
	// der ANSI-Codepage wäre das ein anderer Ordner als der, den Archicad
	// schreibt; über die Breitzeichenaufrufe ist es derselbe.
	const std::string directory = AppDataDirectory () + "/selftest-Pfäd ü-" + RandomHex (4);
	RTX_CHECK (EnsureDirectory (directory));
	const std::string file = directory + "/Bild ä.png";
	const std::string png = TinyPng ();
	RTX_CHECK (WriteTextFile (file, png));
	RTX_CHECK_EQ (FileSize (file), static_cast<long long> (png.size ()));

	std::string back;
	RTX_CHECK (ReadTextFile (file, back));
	RTX_CHECK (back == png);
	bool ok = false;
	RTX_CHECK_EQ (Sha256OfFile (file, &ok), Sha256::OfString (png));
	RTX_CHECK (ok);
	const Result<ImageInfo> info = ReadImageInfo (file);
	RTX_CHECK (info.IsOk ());

	std::FILE* handle = OpenFile (file, "rb");
	RTX_CHECK (handle != nullptr);
	if (handle != nullptr) std::fclose (handle);

#if defined (_WIN32)
	// Der Ordner heißt im Dateisystem wirklich so, nicht in Codepage-Zeichen.
	RTX_CHECK (std::filesystem::exists (std::filesystem::path (Widen (directory)) / L"Bild \u00e4.png"));
#endif

	// Ersetzen über Nicht-ASCII-Pfade, wie nach dem Zuschnitt in der Palette
	// (#164, F-01): das Ziel bekommt den neuen Inhalt, die Quelle ist weg.
	const std::string cropped = directory + "/zugeschnitten ö.png";
	RTX_CHECK (WriteTextFile (cropped, "neu"));
	RTX_CHECK (RenameReplacing (cropped, file));
	RTX_CHECK (ReadTextFile (file, back));
	RTX_CHECK_EQ (back, std::string ("neu"));
	RTX_CHECK_EQ (FileSize (cropped), -1LL);
	// Scheitert das Ersetzen, bleibt das Ziel unangetastet.
	RTX_CHECK (!RenameReplacing (directory + "/gibt es nicht ß.png", file));
	RTX_CHECK (ReadTextFile (file, back));
	RTX_CHECK_EQ (back, std::string ("neu"));

	RTX_CHECK (RemoveFile (file));
	RTX_CHECK_EQ (FileSize (file), -1LL);
	RTX_CHECK (RemoveDirectory (directory));
}

// --- Adresszerlegung: dieselben Regeln auf beiden Systemen -------------------

RTX_TEST (AdresszerlegungLiefertHostOhneKlammernUndKlein)
{
	const UrlParts loop = ParseUrl ("http://[::1]:8787/api");
	RTX_CHECK (loop.valid);
	RTX_CHECK_EQ (loop.scheme, std::string ("http"));
	RTX_CHECK_EQ (loop.host, std::string ("::1"));
	RTX_CHECK (!loop.hasUserInfo);

	const UrlParts upper = ParseUrl ("HTTPS://Dev.Rendertaxi.AI/x?y=1");
	RTX_CHECK (upper.valid);
	RTX_CHECK_EQ (upper.scheme, std::string ("https"));
	RTX_CHECK_EQ (upper.host, std::string ("dev.rendertaxi.ai"));

	const UrlParts evil = ParseUrl ("http://localhost:80@evil.example/");
	RTX_CHECK (!evil.valid || evil.hasUserInfo);
	RTX_CHECK (!evil.valid || evil.host == "evil.example");
}

RTX_TEST (KeinTokenAnAdressenMitBenutzerangabeOderOhneHttps)
{
	// Ergänzt die Prüfung `BearerTokenNurUeberHttpsOderDieSchleife`: Formen,
	// bei denen ein nachsichtiger Parser anders läse als CFURL.
	RTX_CHECK (!PluginApiClient::IsTokenSafeBaseUrl ("http://@localhost:8787"));
	RTX_CHECK (!PluginApiClient::IsTokenSafeBaseUrl ("http://:@localhost:8787"));
	RTX_CHECK (!PluginApiClient::IsTokenSafeBaseUrl ("http://localhost\\@evil.example/"));
	RTX_CHECK (!PluginApiClient::IsTokenSafeBaseUrl ("http://local host:8787"));
#if defined (_WIN32)
	// Steuerzeichen lehnt die Windows-Umsetzung vor dem Parser ab. CFURL nimmt
	// einen angehängten Zeilenumbruch hin; das bleibt unter macOS unverändert
	// (#161: kein Eingriff in macOS) und ist ohne Folgen für die Regel: der
	// Host bleibt die Schleife.
	RTX_CHECK (!PluginApiClient::IsTokenSafeBaseUrl ("http://localhost:8787\n"));
#endif
	RTX_CHECK (!PluginApiClient::IsTokenSafeBaseUrl ("http://[::1]:8787@evil.example/"));
	RTX_CHECK (!PluginApiClient::IsTokenSafeBaseUrl ("http://127.0.0.1:8787@evil.example/"));
	RTX_CHECK (!PluginApiClient::IsTokenSafeBaseUrl ("http://0x7f.0.0.1:8787"));
	RTX_CHECK (!PluginApiClient::IsTokenSafeBaseUrl ("http://127.1:8787"));
	RTX_CHECK (!PluginApiClient::IsTokenSafeBaseUrl ("//localhost:8787"));
	RTX_CHECK (!PluginApiClient::IsTokenSafeBaseUrl ("localhost:8787"));
	RTX_CHECK (!PluginApiClient::IsTokenSafeBaseUrl ("file:///etc/passwd"));
	// Die erlaubten Formen bleiben erlaubt.
	RTX_CHECK (PluginApiClient::IsTokenSafeBaseUrl ("https://dev.rendertaxi.ai/"));
	RTX_CHECK (PluginApiClient::IsTokenSafeBaseUrl ("http://localhost:8787/"));
}

// --- Zuschnitt ---------------------------------------------------------------

RTX_TEST (ZuschnittsrechnungIstMittigUndGanzzahlig)
{
	// Die echte Aufnahme aus Archicad 28 auf 16:9: oben und unten beschnitten.
	const CropPlan wide = PlanCrop (1071, 905, 16, 9);
	RTX_CHECK (wide.needed);
	RTX_CHECK_EQ (wide.width, 1071);
	RTX_CHECK_EQ (wide.height, 602);
	RTX_CHECK_EQ (wide.x, 0);
	RTX_CHECK_EQ (wide.y, 151);

	// Zu breit: links und rechts.
	const CropPlan square = PlanCrop (2000, 1000, 1, 1);
	RTX_CHECK (square.needed);
	RTX_CHECK_EQ (square.width, 1000);
	RTX_CHECK_EQ (square.x, 500);
	RTX_CHECK_EQ (square.y, 0);

	// Passend (unter einem halben Prozent) und unbekannt: nichts zu tun.
	RTX_CHECK (!PlanCrop (1920, 1080, 16, 9).needed);
	RTX_CHECK (!PlanCrop (1921, 1080, 16, 9).needed);
	RTX_CHECK (!PlanCrop (1920, 1080, 0, 0).needed);
	RTX_CHECK (!PlanCrop (2, 2, 1, 1).needed);

	const CropPlan half = PlanCrop (2, 2, 2, 1);
	RTX_CHECK (half.needed);
	RTX_CHECK_EQ (half.width, 2);
	RTX_CHECK_EQ (half.height, 1);
}

RTX_TEST (UmwandlungNachPngBehaeltDieMasse)
{
	const std::string directory = TransferStore::DefaultWorkDirectory () + "/convert-" + RandomHex (4);
	RTX_CHECK (EnsureDirectory (directory));
	const std::string source = directory + "/quelle.png";
	const std::string target = directory + "/ziel.png";
	RTX_CHECK (WriteTextFile (source, TinyPng ()));

	RTX_CHECK (ConvertImageToPng (source, target).IsOk ());
	const Result<ImageInfo> info = ReadImageInfo (target);
	RTX_CHECK (info.IsOk ());
	if (info.IsOk ()) {
		RTX_CHECK_EQ (info.Value ().width, 2);
		RTX_CHECK_EQ (info.Value ().height, 2);
		RTX_CHECK_EQ (info.Value ().mediaType, std::string ("image/png"));
		RTX_CHECK_EQ (info.Value ().bitDepth, 8);
	}
	// Eine unlesbare Quelle ist ein Fehler, kein leeres Bild.
	RTX_CHECK (WriteTextFile (source, "kein Bild"));
	RTX_CHECK (!ConvertImageToPng (source, target).IsOk ());
	RemoveDirectory (directory);
}

// --- Schlüsselspeicher des Systems -------------------------------------------

RTX_TEST (SchluesselspeicherDesSystemsIstVorhanden)
{
	// Unter macOS die Keychain, unter Windows der Credential Manager — nie
	// `nullptr` auf einer ausgelieferten Plattform.
	RTX_CHECK (MakeSystemTokenStore () != nullptr);
}

#if defined (_WIN32)

RTX_TEST (CredentialManagerHaeltTokenOhneKlartextdatei)
{
	std::unique_ptr<TokenStore> store = MakeSystemTokenStore ();
	RTX_CHECK (store != nullptr);
	if (store == nullptr) return;

	const std::string server = "https://selftest-" + RandomHex (6) + ".invalid";
	const std::string token = "rtx-selftest-" + RandomHex (16);
	StoredCredential credential;
	credential.accessToken = token;
	credential.deviceId = NewUuidV7 ();
	credential.displayName = "Prüfkonto Ä";
	credential.expiresAtMillis = 1234567890123ULL;

	const Status saved = store->Save (server, credential);
	RTX_CHECK (saved.IsOk ());
	if (!saved.IsOk ()) {
		std::cerr << "  " << saved.GetError ().message << "\n";
		return;
	}

	// Liegt im Credential Manager unter dem erwarteten Ziel.
	PCREDENTIALW entry = nullptr;
	RTX_CHECK (CredReadW (CredentialTarget (server).c_str (), CRED_TYPE_GENERIC, 0, &entry));
	if (entry != nullptr) {
		RTX_CHECK_EQ (static_cast<unsigned long> (entry->Persist),
					  static_cast<unsigned long> (CRED_PERSIST_LOCAL_MACHINE));
		CredFree (entry);
	}

	const Result<StoredCredential> loaded = store->Load (server);
	RTX_CHECK (loaded.IsOk ());
	if (loaded.IsOk ()) {
		RTX_CHECK_EQ (loaded.Value ().accessToken, token);
		RTX_CHECK_EQ (loaded.Value ().deviceId, credential.deviceId);
		RTX_CHECK_EQ (loaded.Value ().displayName, credential.displayName);
		RTX_CHECK_EQ (loaded.Value ().expiresAtMillis, credential.expiresAtMillis);
	}

	// **Keine Klartextablage:** das Token steht in keiner Datei des Add-Ons.
	RTX_CHECK (!AnyFileContains (AppDataDirectory (), token));

	// Zu groß wird ausdrücklich abgelehnt, nie gekürzt gespeichert.
	StoredCredential huge = credential;
	huge.displayName = std::string (3000, 'x');
	RTX_CHECK (!store->Save (server, huge).IsOk ());
	const Result<StoredCredential> stillThere = store->Load (server);
	RTX_CHECK (stillThere.IsOk () && stillThere.Value ().accessToken == token);

	// Abmelden: Token weg, Gerätekennung bleibt (§5.5).
	RTX_CHECK (store->Erase (server).IsOk ());
	RTX_CHECK (!store->Load (server).IsOk ());
	const Result<StoredCredential> raw = store->LoadRaw (server);
	RTX_CHECK (raw.IsOk ());
	if (raw.IsOk ()) {
		RTX_CHECK_EQ (raw.Value ().deviceId, credential.deviceId);
		RTX_CHECK (raw.Value ().accessToken.empty ());
	}
	RTX_CHECK_EQ (LoadOrCreateDeviceId (*store, server), credential.deviceId);

	// Aufräumen: den Prüfeintrag ganz entfernen.
	CredDeleteW (CredentialTarget (server).c_str (), CRED_TYPE_GENERIC, 0);
	RTX_CHECK (!store->LoadRaw (server).IsOk ());
}

#endif
