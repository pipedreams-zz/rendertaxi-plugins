// Der Modellweg einer Übernahme zwischen Archicad und Übertragung (RTX-A-012,
// #307) — alles, was nach dem Lesen am Host DevKit-frei geschehen kann.
//
// Das Add-on liest im Hauptfaden die Geometrie des 3D-Fensters
// (`GlbSceneBuilder`) und die Projektionen der Kameras; **erst nach dem
// Handshake** steht fest, welche Fassung der Server annimmt und wie groß das
// Modell sein darf. Deshalb entstehen Datei, Kameras und Manifestteile hier,
// im Arbeitsfaden: `AssembleModel`.
//
// Dazu die zwei Dinge der Palette, die sich ohne Archicad prüfen lassen: die
// Auswahl zusätzlicher Kameras aus den gespeicherten 3D-Ansichten
// (`BuildCameraPicks`, wie RTX-C4D-010) und das Warten auf den Neuaufbau des
// 3D-Modells (`ModelRebuildWait`, QA-09).
#pragma once

#include <chrono>
#include <cstdint>
#include <string>
#include <vector>

#include "rtx/ArchicadCamera.hpp"
#include "rtx/CaptureManifest.hpp"
#include "rtx/Glb.hpp"
#include "rtx/SavedViews.hpp"

namespace rtx {

/** Pfad der Modelldatei im Arbeitsverzeichnis und im Manifest. */
inline constexpr const char* kModelPath = "model/scene.glb";

/** Eine Kamera, wie Archicad sie liefert, mit Namen und Herkunft (`current` oder `view:<guid>`). */
struct NamedProjection {
	std::string name;
	std::string source;
	ArchicadProjection projection;
};

struct ModelInput {
	/** Die Geometrie des 3D-Fensters, Archicad bereits nach glTF abgebildet. */
	GlbScene scene;
	/** Die Kamera der übertragenen Ansicht — sie wird der Kamerablock des Manifests. */
	NamedProjection current;
	/** Zusätzliche Kameras aus gespeicherten 3D-Ansichten; nur in der Datei. */
	std::vector<NamedProjection> extra;
	/** Bildgröße der Kamera: die Rendering-Szene, sonst 0 für das 3D-Fenster. */
	int width = 0;
	int height = 0;
};

struct ModelOutput {
	/** Die Modelldatei, `present`, mit Größe, Hash und lokalem Pfad. */
	CaptureAsset asset;
	/** Der Kamerablock; fehlt, wenn die Fassung den nötigen Shift nicht kennt. */
	bool hasCamera = false;
	CaptureCamera camera;
	GlbStats stats;
	bool mergedByMaterial = false;
	int cameras = 0;
	/** Sätze für die Palette: Rückfall je Material, genäherte oder ausgelassene Kameras. */
	std::vector<std::string> notes;
};

/** Die Hülle der Szene in Archicad-Koordinaten (Meter, Z oben), aus den Positionen im glTF-Raum. */
SceneBox SceneBoxOf (const GlbScene& scene);

/**
 * Stellt das Modell zusammen und schreibt es nach `<directory>/model/scene.glb`:
 * Kameras abbilden (die aktuelle zuerst), Grenzen prüfen (`FinishGlb`),
 * Datei schreiben, Hash bilden. `minor` ist die MINOR des Manifests, das
 * gesendet wird; ab 1.6 trägt der Kamerablock die Bildgröße, unter 1.4 entfällt
 * er bei einem Shift. Fehler: leeres Modell (`model_empty`), Grenzen,
 * unbrauchbare Kamera der Ansicht, Datei nicht schreibbar.
 */
Result<ModelOutput> AssembleModel (const ModelInput& input, const std::string& directory, int minor,
								   std::int64_t maxGeometryBytes);

/**
 * Die Selbstauskunft des Add-ons im Manifest (`source.host.capabilities`, ab
 * 1.1.0) — dieselben Zustände wie in `integrations/_shared/capabilities/archicad.json`.
 */
std::vector<std::pair<std::string, std::string>> ArchicadCapabilities (int minor);

// --- Zusätzliche Kameras ------------------------------------------------------------------------

/** Eine Zeile der Kameraauswahl: eine gespeicherte 3D-Ansicht mit Häkchen. */
struct CameraPick {
	std::string guid;
	std::string label;
	bool checked = false;
};

/**
 * Die Zeilen der Auswahl aus den gespeicherten 3D-Ansichten (Beschriftung wie
 * „Ansicht", `BuildViewChoices`) und den gemerkten GUIDs. **Regel 3:** eine
 * gemerkte GUID, deren Ansicht es nicht mehr gibt, verliert still ihr
 * Häkchen — sie verhindert nichts. `excludeGuid` ist die Ansicht, die
 * ohnehin übertragen wird; sie steht nicht in der Liste.
 */
std::vector<CameraPick> BuildCameraPicks (const std::vector<SavedView>& views,
										  const std::vector<std::string>& rememberedGuids,
										  const std::string& excludeGuid = {});

/** Die angehakten GUIDs in der Reihenfolge der Liste. */
std::vector<std::string> CheckedGuids (const std::vector<CameraPick>& picks);

/** „☑ Name" oder „☐ Name" — die Zeile, wie die Liste sie zeigt. */
std::string CameraPickLine (const CameraPick& pick);

// --- Neuaufbau des 3D-Modells (QA-09) ----------------------------------------------------------

/**
 * Nach einem Ansichtswechsel baut Archicad das 3D-Modell im Hintergrund neu;
 * so lange liefert ModelAccess **0 Körper ohne Fehlercode** (gemessen, bei
 * AERO rund 90 s). Ein leeres Modell wird nie gesendet: die Palette wartet,
 * fragt alle zwei Sekunden nach und startet die Übernahme dann selbst — oder
 * gibt nach der Frist mit einem Satz auf.
 */
/**
 * Wofür gewartet wird (F-01 an PR #318). Der Neustart nach dem Neuaufbau
 * überträgt nur, was angefordert war: dasselbe Projekt, dieselbe Quelle,
 * dasselbe Ziel, dieselbe Wahl. Die Palette füllt die Felder an **einer**
 * Stelle, beim Beginn und bei jeder Abfrage gleich.
 */
struct ModelWaitIdentity {
	std::string projectKey;       ///< lokale Projektkennung (Datei), nie im Manifest
	std::string sourceKey;        ///< Fenster bzw. Renderquelle
	std::string viewGuid;         ///< gewählte gespeicherte Ansicht, leer = aktuelle Modellansicht
	std::string openedViewGuid;   ///< zuletzt in Archicad geöffneter Ausschnitt
	std::string targetKey;        ///< Plattformprojekt, Modus, Blickpunkt
	bool image = false;
	bool model = false;
};

/** Leer, wenn `now` dieselbe Übernahme beschreibt; sonst der Grund in einem Satzteil. */
std::string ModelWaitChange (const ModelWaitIdentity& started, const ModelWaitIdentity& now);

/** Der Satz für Palette und Protokoll, wenn der Wartevorgang deshalb endet. */
std::string ModelWaitAbandonedText (const std::string& reason);

class ModelRebuildWait final {
public:
	using Clock = std::chrono::steady_clock;

	explicit ModelRebuildWait (std::chrono::seconds deadline = std::chrono::seconds (300),
							   std::chrono::milliseconds interval = std::chrono::milliseconds (2000));

	void Start (Clock::time_point now, ModelWaitIdentity identity = {});
	void Stop ();
	bool Active () const { return active; }
	/** Leer, solange `current` die angeforderte Übernahme ist (siehe `ModelWaitChange`). */
	std::string Changed (const ModelWaitIdentity& current) const;
	/** Ist die nächste Abfrage fällig? Rückt den Takt weiter, wenn ja. */
	bool Due (Clock::time_point now);
	bool Expired (Clock::time_point now) const;
	/** Der Satz für die Palette, mit vergangenen Sekunden. */
	std::string Text (Clock::time_point now) const;

private:
	std::chrono::seconds deadline;
	std::chrono::milliseconds interval;
	bool active = false;
	ModelWaitIdentity identity;
	Clock::time_point started {};
	Clock::time_point nextPoll {};
};

inline constexpr const char* kModelRebuildGaveUp =
	"Das 3D-Fenster zeigt nach fünf Minuten noch keine Geometrie. Bitte die Ansicht prüfen und erneut übernehmen.";

} // namespace rtx
