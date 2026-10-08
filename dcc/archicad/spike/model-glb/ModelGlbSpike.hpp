// Messauftrag Archicad-Modellweg (RTX-A-010, #256) — JSON-Befehle des Prototyps.
//
// **Nicht im veröffentlichten Add-on.** Nur mit `-DRTX_SPIKE_MODEL_GLB=ON`
// übersetzt; `scripts/build.sh`, CI und `dist.sh` setzen die Option nicht.
//
//   rendertaxi.SpikeModelGlb    3D-Fenster → GLB mit Kameras, dazu ein
//                               Messbericht als JSON neben der Datei
//   rendertaxi.SpikeReadCamera  rohe Projektionswerte des 3D-Fensters
//   rendertaxi.SpikeSetCamera   Perspektive des 3D-Fensters setzen (Testszene)
//   rendertaxi.SpikeCutPlanes   3D-Schnittebenen ein- oder ausschalten
#pragma once

#include "APIEnvir.h"
#include "ACAPinc.h"

namespace rtxaddon {
namespace spike {

GSErrCode InstallModelGlbSpikeCommands ();

} // namespace spike
} // namespace rtxaddon
