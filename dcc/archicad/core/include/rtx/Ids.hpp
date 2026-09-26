// Kennungen und Zeitstempel des Vertrags.
//
// `captureId` ist nach `capture-manifest.schema.json` eine UUIDv7 in
// kanonischer Kleinschreibung. UUIDv7 trägt die Unix-Zeit in Millisekunden in
// den ersten 48 Bit — dieselbe Identitätsentscheidung wie in ADR 0001 für die
// Plattform.
#pragma once

#include <cstdint>
#include <string>

namespace rtx {

/** Erzeugt eine UUIDv7 aus der aktuellen Zeit und kryptografischer Zufälligkeit. */
std::string NewUuidV7 ();

/** Wie `NewUuidV7`, aber mit vorgegebener Zeit und Zufallsquelle — für Tests. */
std::string MakeUuidV7 (std::uint64_t unixMillis, const std::uint8_t random[10]);

/** Prüft die Schreibweise von `common.schema.json#/$defs/uuidV7`. */
bool IsUuidV7 (const std::string& value);

/** Aktuelle UTC-Zeit als `YYYY-MM-DDThh:mm:ss.sssZ` (RFC 3339, Millisekunden). */
std::string NowTimestampUtc ();

/** Formatiert eine Unix-Zeit in Millisekunden als `timestampUtc`. */
std::string FormatTimestampUtc (std::uint64_t unixMillis);

/** Prüft die Schreibweise von `common.schema.json#/$defs/timestampUtc`. */
bool IsTimestampUtc (const std::string& value);

/** Unix-Zeit in Millisekunden. */
std::uint64_t UnixMillis ();

/** Füllt `length` Bytes mit kryptografischer Zufälligkeit. */
void FillRandom (std::uint8_t* out, std::size_t length);

/** Zufällige URL-sichere Zeichenkette aus `bytes` Zufallsbytes, hexkodiert. */
std::string RandomHex (std::size_t bytes);

} // namespace rtx
