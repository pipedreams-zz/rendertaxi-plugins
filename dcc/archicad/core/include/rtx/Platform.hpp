// Die Plattformgrenze für Ablageorte und Dateipfade.
//
// **Alle Pfade im Kern sind UTF-8.** Unter macOS ist das die Sprache des
// Systems; `fopen` und `std::filesystem::path` nehmen die Bytes, wie sie sind.
// Unter Windows lesen dieselben Aufrufe einen `std::string` dagegen in der
// ANSI-Codepage — ein Benutzername mit Umlaut in `%LOCALAPPDATA%` wäre dann
// ein anderer Pfad als der, den Archicad (`IO::Location` aus UTF-8) schreibt.
// Deshalb gehen Dateizugriffe des Kerns über diese Funktionen: unter macOS
// sind sie die bisherigen Aufrufe, unter Windows die Breitzeichenfassungen.
//
// Ablageorte (Issue #161, Punkt 4):
//
//   macOS    ~/Library/Application Support/rendertaxi/archicad/   Zustand, Einstellungen
//            ~/Library/Logs/rendertaxi/                          Protokoll
//   Windows  %LOCALAPPDATA%\rendertaxi\archicad\                  beides
#pragma once

#include <cstdio>
#include <filesystem>
#include <string>

namespace rtx {

/** Verzeichnis für `transfers.json`, `settings.json` und die Aufnahmen. */
std::string AppDataDirectory ();

/** Verzeichnis für das Protokoll des Add-Ons. */
std::string LogDirectory ();

/** Ein UTF-8-Pfad als `std::filesystem::path`. */
std::filesystem::path FsPath (const std::string& path);

/** `fopen` für einen UTF-8-Pfad. */
std::FILE* OpenFile (const std::string& path, const char* mode);

/** Löscht eine Datei; `false`, wenn es sie nicht gab oder sie blieb. */
bool RemoveFile (const std::string& path);

/**
 * Benennt `from` in `to` um und ersetzt eine vorhandene Datei `to` dabei.
 * Scheitert es, bleiben beide Dateien, wie sie waren — wer das Original erst
 * danach löschen wollte, verliert so nichts. Unter Windows `MoveFileExW` mit
 * `MOVEFILE_REPLACE_EXISTING`, sonst `rename`.
 */
bool RenameReplacing (const std::string& from, const std::string& to);

/**
 * Der Pfad in der Schreibweise des Systems: unter Windows mit `\`, unter macOS
 * unverändert. Gebraucht dort, wo ein Pfad das Add-On verlässt (Archicad).
 */
std::string NativePath (const std::string& path);

/**
 * Unicode-Normalform C eines UTF-8-Textes über das System — unter macOS
 * `CFStringNormalize`, unter Windows `NormalizeString`. macOS liefert
 * Dateinamen zerlegt (NFD), das Manifest steht in NFC (`source.fileName`,
 * `capture-manifest.md` Abschnitt 13). Ohne Systemhilfe (andere Plattformen,
 * ungültiges UTF-8) bleibt der Text, wie er ist.
 */
std::string NormalizeNfc (const std::string& utf8);

#if defined (_WIN32)
/** UTF-8 nach UTF-16; leer bei ungültigem UTF-8. */
std::wstring Widen (const std::string& utf8);
/** UTF-16 nach UTF-8. */
std::string Narrow (const std::wstring& wide);
#endif

} // namespace rtx
