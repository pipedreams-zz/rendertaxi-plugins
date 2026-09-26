// Die Messung, die Stufe B verlangt.
//
// „Klären und belegen, welcher Bildzugriff in Archicad 28 tatsächlich vorhanden
// ist — für die aktuelle Ansicht: Speichern der Ansicht als Bild,
// `ACAPI_Rendering_PhotoRender`, weitere dokumentierte Wege. Je Weg:
// erreichbare Auflösung, Reproduzierbarkeit, Abhängigkeit von
// Fenstergröße/Anzeigemodus, Dauer."
//
// Ein Beleg, den man von Hand zusammenträgt, ist beim zweiten Mal keiner mehr.
// Diese Messung läuft deshalb als Menüpunkt im Add-On und schreibt ihr Ergebnis
// als Textprotokoll; `capabilities.md` zitiert daraus.
#pragma once

#include <string>

namespace rtxaddon {

/**
 * Misst die Bildzugriffswege an der aktuellen Ansicht und schreibt ein
 * Protokoll. Gibt den Pfad des Protokolls zurück oder eine leere Zeichenkette.
 *
 * Die Messung verändert die 3D-Fenstergröße vorübergehend, um die Abhängigkeit
 * der Auflösung von ihr zu belegen, und stellt sie danach wieder her.
 */
std::string RunCapabilityProbe ();

} // namespace rtxaddon
