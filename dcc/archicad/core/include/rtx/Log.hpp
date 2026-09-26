// Protokoll des Add-Ons — mit einer Sperre statt einer Bitte.
//
// `security-privacy.md` und ADR 0009 verlangen, dass weder Anmeldetokens noch
// Gerätecodes noch signierte URLs in Logs erscheinen. Eine Regel, die nur in
// der Dokumentation steht, hält genau bis zur ersten Fehlersuche. Deshalb
// schreibt dieser Logger nicht, was ihm übergeben wird, sondern was
// `Redact` übrig lässt: Abfrageteile von URLs, Bearer-Kopfzeilen und die
// bekannten Geheimnisfelder werden ersetzt, bevor eine Zeile entsteht.
#pragma once

#include <string>

namespace rtx {

/** Entfernt Geheimnisse aus einer Zeile; die Umsetzung ist testbar und geprüft. */
std::string Redact (const std::string& line);

/** Kürzt eine URL auf Schema, Host und Pfad — ohne Nutzerteil, Abfrage, Fragment. */
std::string SafeUrl (const std::string& url);

/** Zielpfad des Protokolls; leer schaltet das Schreiben ab. */
void SetLogFile (const std::string& path);
std::string LogFilePath ();

/** Schreibt eine redigierte Zeile mit UTC-Zeitstempel. */
void LogLine (const std::string& line);

} // namespace rtx
