// Die Kamera einer Archicad-Ansicht als glTF-Kamera und als Kamerablock des
// Capture-Manifests (RTX-A-012, #307) — die Abbildung aus dem Messauftrag
// RTX-A-010 (`docs/model-glb-spike.md`, Frage 5), hier ohne DevKit.
//
// Das Add-on liest `API_3DProjectionInfo` und `API_3DWindowInfo` und gibt die
// Zahlen unverändert in `ArchicadProjection`. Alles Weitere ist Rechnung und
// gegen die gemessenen Werte der Testszene geprüft:
//
// - **Perspektive:** `viewCone` ist der **waagerechte** Öffnungswinkel in Grad
//   (Q-01), `rollAngle` Grad. glTF bekommt
//   `yfov = 2·atan(tan(viewCone/2) / Seitenverhältnis)`, das Manifest den
//   Winkel unverändert mit `axis: horizontal`. Die Basis wie im DevKit-Beispiel
//   `ModelAccess_Test`: rechts = (d.y, −d.x, 0), oben = rechts × d, um den
//   Rollwinkel gedreht.
// - **Zweifluchtpunkt** (Q-09): waagerechte Kamera mit **senkrechtem Shift**,
//   `y = tan(Neigung) / tan(yfov/2) / 2 · (Höhe / längere Seite)`.
// - **Parallel:** `xmag`, `ymag` und die Mitte aus `tranmat`, `zoomScale` und
//   `zoomDisp`. Eine schiefe Parallelprojektion (Frontal-, Kavalier-
//   Axonometrie) kennt glTF nicht: rechtwinklig genähert, `approximated`.
// - **Nah und fern einer Parallelkamera aus der Szenenhülle** (die Falle aus
//   F-01 an PR #303): die Kamera rückt so weit zurück, dass jede Ecke der
//   Hülle zwischen nah und fern liegt, mit Sicherheitsabstand. Ein fester
//   Abstand schnitt bei Rhino ein entferntes Modell ab.
//
// Alle Ausgaben stehen im Exportraum: glTF-Achsen (x, z, −y), Meter im
// Projektursprung.
#pragma once

#include <string>

#include "rtx/CaptureManifest.hpp"
#include "rtx/Glb.hpp"
#include "rtx/Result.hpp"

namespace rtx {

/** Die Projektion des 3D-Fensters, Feld für Feld wie in `API_3DProjectionInfo` und `API_3DWindowInfo`. */
struct ArchicadProjection {
	bool perspective = true;

	// `API_PerspPars`: Standort (pos.x, pos.y, cameraZ), Ziel (target.x, target.y, targetZ) in Metern.
	double eye[3] = {0, 0, 0};
	double target[3] = {0, 1, 0};
	/** Waagerechter Öffnungswinkel in Grad (gemessen, QA-05). */
	double viewConeDegrees = 60.0;
	/** Rollwinkel in Grad (gemessen, QA-05); bei Zweifluchtpunkt ohne Wirkung. */
	double rollDegrees = 0.0;
	bool twoPoint = false;

	// `API_AxonoPars`: Modell → Projektion, Zeilen nicht normiert.
	double tranmat[12] = {1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0};
	int projMod = 0;

	// `API_3DWindowInfo`: Größe des 3D-Fensters in Pixeln und Ausschnitt der Parallelprojektion.
	int hSize = 0;
	int vSize = 0;
	double zoomScaleX = 0.0;
	double zoomScaleY = 0.0;
	double zoomDispX = 0.0;
	double zoomDispY = 0.0;
};

/** Die Hülle der sichtbaren Szene in Archicad-Koordinaten (Meter, Z oben). */
struct SceneBox {
	bool known = false;
	double min[3] = {0, 0, 0};
	double max[3] = {0, 0, 0};
};

struct MappedCamera {
	GlbCamera gltf;
	CaptureCamera manifest;
	/** `exact` oder `approximated` (schiefe Parallelprojektion, fehlender Ausschnitt). */
	std::string fidelity = "exact";
	std::string note;
};

/** Nahe Schnittebene der Perspektive in Metern (wie im Prototyp, pixelgenau auf dev). */
inline constexpr double kPerspectiveNear = 0.1;
/** Nahe Schnittebene der Parallelkamera und kleinster Sicherheitsabstand in Metern (wie Rhino, F-01). */
inline constexpr double kParallelNear = 0.01;
/** Sicherheitsabstand als Anteil der Szenentiefe (wie Rhino, F-01). */
inline constexpr double kDepthMargin = 0.01;

/**
 * Bildet die Projektion ab.
 *
 * `name` wird Name der glTF-Kamera, `source` steht in `nodes[i].extras`
 * (`current` oder `view:<guid>`). `width`/`height` sind die Bildgröße der
 * Kamera; 0 nimmt das 3D-Fenster. Eine andere Bildgröße gilt als **mittiger
 * Zuschnitt** des Fensters auf ihr Seitenverhältnis, so groß wie möglich — wie
 * das Bild des Bildwegs, das auf die Rendering-Szene zugeschnitten wird. Der
 * Winkel, `yfov`, der Shift und der Ausschnitt der Parallelkamera gelten dann
 * für den Zuschnitt. Die Bildgröße steht als `resolution` im Kamerablock, wenn
 * `withResolution` gesetzt ist (ab 1.6.0).
 *
 * Ein Shift (Zweifluchtpunkt) steht im Kamerablock erst ab 1.4.0. Gegen einen
 * älteren Server sendet der Aufrufer den Kamerablock dann gar nicht
 * (`plugin-api-v1.md`, Abschnitt 4) — eine Kamera ohne ihren Shift zeigte
 * einen anderen Ausschnitt. Die glTF-Kamera trägt den Shift immer.
 *
 * Fehler, wenn die Projektion keine Kamera ergibt (Standort gleich Ziel,
 * `tranmat` ohne Achsen, kein Seitenverhältnis).
 */
Result<MappedCamera> MapArchicadCamera (const ArchicadProjection& projection, const SceneBox& scene,
										const std::string& name, const std::string& source, int width = 0,
										int height = 0, bool withResolution = false);

} // namespace rtx
