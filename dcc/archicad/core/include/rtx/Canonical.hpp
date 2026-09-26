// Kanonisierung und `contentHash` nach `integrations/archicad/docs/architecture.md`,
// Abschnitte 8.2 und 8.6, bitgleich zu
// `integrations/_shared/tools/canonical-hash.mjs`.
//
// **Grenze mit Absicht.** Die Referenz normalisiert jedes Feld nach Unicode NFC.
// Eine NFC-Umsetzung in C++ ohne ICU wäre entweder unvollständig oder eine
// zweite Abhängigkeit. Der Bildweg braucht sie nicht: in die Assetzeile gehen
// ausschließlich `role` (geschlossene Aufzählung), `path`
// (`common.schema.json#/$defs/relativePath` erlaubt nur `[A-Za-z0-9._-]` und
// `/`) und `sha256` (Hexziffern) ein — allesamt ASCII, und für ASCII ist NFC
// die Identität. `EncodeField` lehnt deshalb Nicht-ASCII ausdrücklich mit
// `not_canonical` ab, statt still einen Hash zu bilden, der von der Referenz
// abweichen könnte. Ein Feld mit freiem Text geht in v1 in keinen Hash ein.
#pragma once

#include <string>
#include <vector>

#include "rtx/Result.hpp"

namespace rtx {

/** Längenpräfigierte Kodierung: `<Bytelänge> ":" <Wert>`; nur ASCII zulässig. */
Result<std::string> EncodeField (const std::string& value);

/** Kodierung eines fehlenden Wertes: `-1:`. */
std::string EncodeAbsent ();

/** Sortiert aufsteigend nach UTF-8-Bytes (nicht nach UTF-16). */
void SortByUtf8 (std::vector<std::string>& values);

struct ContentHashAsset {
	std::string role;
	std::string path;
	std::string sha256;
};

/**
 * `contentHash` eines Captures: SHA-256 über die nach UTF-8-Bytes sortierten,
 * je mit Zeilenvorschub abgeschlossenen Assetzeilen
 * `enc("asset") ⇥ enc(role) ⇥ enc(path) ⇥ enc(sha256)`. Es gehen ausschließlich
 * Assets mit `status: "present"` ein; der Aufrufer hat sie bereits gefiltert.
 */
Result<std::string> CaptureContentHash (const std::vector<ContentHashAsset>& present);

} // namespace rtx
