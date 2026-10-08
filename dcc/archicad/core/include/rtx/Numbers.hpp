// Ganzzahlen aus fremdem Text — Versionsteile, Seitenverhältnisse, Abstände
// aus Antworten des Servers.
//
// **Nie werfen, nie überlaufen** (F-01 an PR #311): `std::stoi` wirft bei einer
// zu langen Zahl `std::out_of_range`, und `std::atoi` hat dort undefiniertes
// Verhalten. Beides darf ein Wert aus dem Netz nicht auslösen. Gelesen wird
// Ziffer für Ziffer mit Obergrenze; was sie überschreitet, ist kein Wert.
#pragma once

#include <string>

namespace rtx {

/** Größte Zahl, die `ParseBoundedInt` ohne Angabe annimmt (passt in jedes `int`). */
inline constexpr int kBoundedIntMax = 1000000000;

/**
 * Liest `text` als nicht negative Dezimalzahl ohne Vorzeichen, Leerraum oder
 * Rest. Leer, ein anderes Zeichen als eine Ziffer oder eine Zahl über `max`
 * → `false`, `value` bleibt unberührt.
 */
bool ParseBoundedInt (const std::string& text, int& value, int max = kBoundedIntMax);

} // namespace rtx
