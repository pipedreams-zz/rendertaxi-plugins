// Skriptbare Befehle über die Archicad-JSON-Schnittstelle.
//
// `ACAPI_AddOnAddOnCommunication_InstallAddOnCommandHandler` macht einen Befehl
// über `API.ExecuteAddOnCommand` von außen aufrufbar. Das Add-On bietet damit
// genau die Schritte an, die **Archicad** braucht — Fenster lesen, Ansicht
// aufnehmen, auf ein Zielformat zuschneiden —, ohne dass jemand die Palette
// bedienen muss.
//
// Zweck ist die Prüfbarkeit: der Bildzugriff und der Zuschnitt lassen sich so
// aus einem Skript heraus messen und nachrechnen, statt sie von Hand
// nachzustellen. Die Übertragung ist bewusst **nicht** dabei — sie hängt an
// einem Anmeldetoken, und ein von außen auslösbarer Upload wäre eine
// Angriffsfläche ohne Gegenwert.
#pragma once

#include "APIEnvir.h"
#include "ACAPinc.h"

namespace rtxaddon {

/** Installiert die Befehle; wird aus `Initialize` gerufen. */
GSErrCode InstallJsonCommands ();

} // namespace rtxaddon
