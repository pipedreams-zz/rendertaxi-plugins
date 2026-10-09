#include "rtx/ModelCapture.hpp"

#include <algorithm>
#include <limits>
#include <set>
#include <utility>

#include "rtx/Sha256.hpp"
#include "rtx/TransferStore.hpp"

namespace rtx {

SceneBox SceneBoxOf (const GlbScene& scene)
{
	SceneBox box;
	double lo[3] = {std::numeric_limits<double>::max (), std::numeric_limits<double>::max (),
					std::numeric_limits<double>::max ()};
	double hi[3] = {std::numeric_limits<double>::lowest (), std::numeric_limits<double>::lowest (),
					std::numeric_limits<double>::lowest ()};
	for (const GlbMesh& mesh : scene.meshes)
		for (const GlbPrimitive& prim : mesh.primitives)
			for (std::size_t v = 0; v + 2 < prim.positions.size (); v += 3) {
				// glTF (x, y, z) → Archicad (x, −z, y).
				const double p[3] = {prim.positions[v], -prim.positions[v + 2], prim.positions[v + 1]};
				for (int k = 0; k < 3; ++k) {
					lo[k] = std::min (lo[k], p[k]);
					hi[k] = std::max (hi[k], p[k]);
				}
				box.known = true;
			}
	if (box.known)
		for (int k = 0; k < 3; ++k) {
			box.min[k] = lo[k];
			box.max[k] = hi[k];
		}
	return box;
}

Result<ModelOutput> AssembleModel (const ModelInput& input, const std::string& directory, int minor,
								   std::int64_t maxGeometryBytes)
{
	ModelOutput out;
	GlbScene scene = input.scene;
	const SceneBox box = SceneBoxOf (scene);
	if (!box.known)
		return Result<ModelOutput>::Fail (kModelEmpty, "Das 3D-Fenster zeigt keine Geometrie; es wird kein Modell gesendet.");

	const Result<MappedCamera> current =
		MapArchicadCamera (input.current.projection, box, input.current.name, input.current.source, input.width,
						   input.height, minor >= 6);
	if (!current) return Result<ModelOutput>::Fail (current.GetError ());
	scene.cameras.push_back (current.Value ().gltf);
	if (current.Value ().fidelity != "exact") out.notes.push_back ("Kamera genähert: " + current.Value ().note + ".");
	out.camera = current.Value ().manifest;
	out.hasCamera = true;
	// Ein Shift steht im Kamerablock erst ab 1.4.0; ohne ihn zeigte die Kamera einen anderen Ausschnitt.
	if (out.camera.HasShift () && minor < 4) {
		out.hasCamera = false;
		out.notes.push_back ("Die Kamera des Zweifluchtpunkts steht nur in der Modelldatei.");
	}

	std::set<std::string> names {input.current.name};
	for (const NamedProjection& extra : input.extra) {
		const Result<MappedCamera> mapped =
			MapArchicadCamera (extra.projection, box, extra.name, extra.source, input.width, input.height, false);
		if (!mapped) {
			out.notes.push_back ("Kamera „" + extra.name + "“ ausgelassen: " + mapped.GetError ().message);
			continue;
		}
		GlbCamera camera = mapped.Value ().gltf;
		// Gleichnamige Ansichten bleiben unterscheidbar (wie `Name#n` in Cinema 4D).
		for (int n = 2; !names.insert (camera.name).second; ++n) camera.name = extra.name + " #" + std::to_string (n);
		scene.cameras.push_back (camera);
	}
	out.cameras = static_cast<int> (scene.cameras.size ());

	const Result<GlbFile> file = FinishGlb (scene, maxGeometryBytes);
	if (!file) return Result<ModelOutput>::Fail (file.GetError ());
	out.stats = file.Value ().stats;
	out.mergedByMaterial = file.Value ().mergedByMaterial;
	if (out.mergedByMaterial)
		out.notes.push_back ("Zu viele Elemente für einzelne Knoten: das Modell ist je Oberfläche zusammengefasst.");

	const std::string folder = directory + "/model";
	const std::string local = directory + "/" + kModelPath;
	const std::string bytes (file.Value ().bytes.begin (), file.Value ().bytes.end ());
	if (!EnsureDirectory (folder) || !WriteTextFile (local, bytes))
		return Result<ModelOutput>::Fail (errc::IoFailed, "Das Modell ließ sich nicht ablegen.");

	out.asset.role = kModelRole;
	out.asset.path = kModelPath;
	out.asset.status = "present";
	out.asset.mediaType = kModelMediaType;
	out.asset.byteSize = static_cast<std::int64_t> (bytes.size ());
	out.asset.sha256 = Sha256::OfString (bytes);
	out.asset.localPath = local;
	return Result<ModelOutput>::Ok (out);
}

std::vector<std::pair<std::string, std::string>> ArchicadCapabilities (int minor)
{
	if (minor < 1) return {};
	std::vector<std::pair<std::string, std::string>> capabilities = {
		{"viewportCapture", "available"},
		{"cameraExport", "available"},
		{"geometryExport", "available"},
	};
	if (minor >= 6) capabilities.emplace_back ("modelOnlyCapture", "available");
	return capabilities;
}

// --- Zusätzliche Kameras ------------------------------------------------------------------------

std::vector<CameraPick> BuildCameraPicks (const std::vector<SavedView>& views,
										  const std::vector<std::string>& rememberedGuids,
										  const std::string& excludeGuid)
{
	const std::set<std::string> remembered (rememberedGuids.begin (), rememberedGuids.end ());
	std::vector<CameraPick> picks;
	for (const ViewChoice& choice : BuildViewChoices (views)) {
		if (choice.guid.empty () || choice.guid == excludeGuid) continue;
		picks.push_back ({choice.guid, choice.label, remembered.count (choice.guid) > 0});
	}
	return picks;
}

std::vector<std::string> CheckedGuids (const std::vector<CameraPick>& picks)
{
	std::vector<std::string> guids;
	for (const CameraPick& pick : picks)
		if (pick.checked) guids.push_back (pick.guid);
	return guids;
}

std::string CameraPickLine (const CameraPick& pick)
{
	return std::string (pick.checked ? "\xE2\x98\x91 " : "\xE2\x98\x90 ") + pick.label;
}

// --- Neuaufbau ----------------------------------------------------------------------------------

ModelRebuildWait::ModelRebuildWait (std::chrono::seconds d, std::chrono::milliseconds i) : deadline (d), interval (i) {}

std::string ModelWaitChange (const ModelWaitIdentity& started, const ModelWaitIdentity& now)
{
	if (now.projectKey != started.projectKey) return "Projekt gewechselt";
	if (now.sourceKey != started.sourceKey || now.viewGuid != started.viewGuid ||
		now.openedViewGuid != started.openedViewGuid)
		return "Ansicht gewechselt";
	if (now.targetKey != started.targetKey) return "Ziel geändert";
	if (now.image != started.image || now.model != started.model) return "Auswahl Bild/Modell geändert";
	return {};
}

std::string ModelWaitAbandonedText (const std::string& reason)
{
	return "Übernahme abgebrochen (" + reason +
		   ", während Archicad das 3D-Modell neu aufbaute). Nichts wurde gesendet; bitte erneut übernehmen.";
}

void ModelRebuildWait::Start (Clock::time_point now, ModelWaitIdentity id)
{
	identity = std::move (id);
	active = true;
	started = now;
	nextPoll = now + interval;
}

void ModelRebuildWait::Stop ()
{
	active = false;
}

std::string ModelRebuildWait::Changed (const ModelWaitIdentity& current) const
{
	return active ? ModelWaitChange (identity, current) : std::string ();
}

bool ModelRebuildWait::Due (Clock::time_point now)
{
	if (!active || now < nextPoll) return false;
	nextPoll = now + interval;
	return true;
}

bool ModelRebuildWait::Expired (Clock::time_point now) const
{
	return active && now - started >= deadline;
}

std::string ModelRebuildWait::Text (Clock::time_point now) const
{
	const long long seconds = std::chrono::duration_cast<std::chrono::seconds> (now - started).count ();
	return "Archicad baut das 3D-Modell neu auf (" + std::to_string (seconds) +
		   " s). Die Übernahme startet, sobald es steht.";
}

} // namespace rtx
