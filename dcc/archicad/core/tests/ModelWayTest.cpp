// Modellweg im Kern (RTX-A-012, #307): GLB-Schreiber, Kameraabbildung,
// Capture-Manifest bis 1.6.0 und die Wahl der Wege.
//
// Die Kamerawerte sind die **gemessenen** des Messauftrags RTX-A-010
// (`docs/measurements/2026-10-08-modellweg/testszene-bericht.json`): rohe
// Archicad-Projektion hinein, die glTF-Kamera heraus, die im Archicad-Bild
// und auf dev pixelgenau saß.
#include "Testing.hpp"

#include <algorithm>
#include <cmath>
#include <iterator>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

#include "rtx/ArchicadCamera.hpp"
#include "rtx/CaptureManifest.hpp"
#include "rtx/CaptureWays.hpp"
#include "rtx/Glb.hpp"
#include "rtx/Ids.hpp"
#include "rtx/Json.hpp"
#include "rtx/Numbers.hpp"
#include "rtx/PluginApi.hpp"
#include "rtx/Platform.hpp"
#include "rtx/Sha256.hpp"
#include "rtx/TransferStore.hpp"

using namespace rtx;

namespace {

constexpr double kPi = 3.14159265358979323846;

using Vec3 = GlbSceneBuilder::Vec3;

bool Near (double a, double b, double tolerance) { return std::fabs (a - b) <= tolerance; }

std::uint32_t U32 (const std::vector<std::uint8_t>& bytes, std::size_t at)
{
	return static_cast<std::uint32_t> (bytes[at]) | (static_cast<std::uint32_t> (bytes[at + 1]) << 8) |
		   (static_cast<std::uint32_t> (bytes[at + 2]) << 16) | (static_cast<std::uint32_t> (bytes[at + 3]) << 24);
}

/** Der JSON-Teil einer GLB-Datei — mit Prüfung von Kopf und Längen. */
JsonPtr GlbJson (const std::vector<std::uint8_t>& bytes)
{
	RTX_CHECK (bytes.size () >= 20);
	if (bytes.size () < 20) return nullptr;
	RTX_CHECK_EQ (U32 (bytes, 0), 0x46546C67u);
	RTX_CHECK_EQ (U32 (bytes, 4), 2u);
	RTX_CHECK_EQ (static_cast<std::size_t> (U32 (bytes, 8)), bytes.size ());
	const std::uint32_t length = U32 (bytes, 12);
	RTX_CHECK_EQ (U32 (bytes, 16), 0x4E4F534Au);
	RTX_CHECK_EQ (length % 4, 0u);
	const std::string text (reinterpret_cast<const char*> (bytes.data () + 20), length);
	JsonPtr json = Json::Parse (text);
	RTX_CHECK (json != nullptr);
	return json;
}

double At (const JsonPtr& array, std::size_t index)
{
	return array && index < array->Items ().size () ? array->Items ()[index]->NumberOr (NAN) : NAN;
}

/** Quader in Archicad-Koordinaten, Normalen nach außen, Umlauf gegen den Uhrzeigersinn von außen. */
void AddBox (GlbSceneBuilder& builder, std::uint32_t material, Vec3 lo, Vec3 hi)
{
	struct Face {
		Vec3 n;
		Vec3 c[4];
	};
	const Face faces[6] = {
		{{0, 0, -1}, {{lo.x, lo.y, lo.z}, {lo.x, hi.y, lo.z}, {hi.x, hi.y, lo.z}, {hi.x, lo.y, lo.z}}},
		{{0, 0, 1}, {{lo.x, lo.y, hi.z}, {hi.x, lo.y, hi.z}, {hi.x, hi.y, hi.z}, {lo.x, hi.y, hi.z}}},
		{{0, -1, 0}, {{lo.x, lo.y, lo.z}, {hi.x, lo.y, lo.z}, {hi.x, lo.y, hi.z}, {lo.x, lo.y, hi.z}}},
		{{0, 1, 0}, {{lo.x, hi.y, lo.z}, {lo.x, hi.y, hi.z}, {hi.x, hi.y, hi.z}, {hi.x, hi.y, lo.z}}},
		{{-1, 0, 0}, {{lo.x, lo.y, lo.z}, {lo.x, lo.y, hi.z}, {lo.x, hi.y, hi.z}, {lo.x, hi.y, lo.z}}},
		{{1, 0, 0}, {{hi.x, lo.y, lo.z}, {hi.x, hi.y, lo.z}, {hi.x, hi.y, hi.z}, {hi.x, lo.y, hi.z}}},
	};
	for (const Face& f : faces)
		builder.AddConvexPolygon (material, {f.c[0], f.c[1], f.c[2], f.c[3]}, {f.n, f.n, f.n, f.n}, f.n);
}

/** Die Testszene des Messauftrags: Quader A, Turm B, Würfel C (Meter, Archicad). */
GlbScene TestScene ()
{
	GlbSceneBuilder builder;
	const std::uint32_t grau = builder.Material (3, "Grau", 0.5, 0.5, 0.5, 0.0);
	const std::uint32_t glas = builder.Material (7, "Glass - Clear Fast", 0.6, 0.8, 0.9, 0.69);
	builder.BeginElement ("0F0A1B2C-0000-4000-8000-00000000000A");
	AddBox (builder, grau, {0, 0, 0}, {10, 4, 3});
	builder.BeginElement ("0F0A1B2C-0000-4000-8000-00000000000B");
	AddBox (builder, glas, {15, 10, 0}, {17, 12, 6});
	builder.BeginElement ("0F0A1B2C-0000-4000-8000-00000000000C");
	AddBox (builder, grau, {-5, 20, 0}, {-4, 21, 1});
	return builder.Scene ();
}

SceneBox TestSceneBox ()
{
	SceneBox box;
	box.known = true;
	const double lo[3] = {-5, 0, 0}, hi[3] = {17, 21, 6};
	for (int k = 0; k < 3; ++k) {
		box.min[k] = lo[k];
		box.max[k] = hi[k];
	}
	return box;
}

ArchicadProjection Perspective (double ex, double ey, double ez, double tx, double ty, double tz, double cone,
								double roll, bool twoPoint)
{
	ArchicadProjection p;
	p.perspective = true;
	p.eye[0] = ex;
	p.eye[1] = ey;
	p.eye[2] = ez;
	p.target[0] = tx;
	p.target[1] = ty;
	p.target[2] = tz;
	p.viewConeDegrees = cone;
	p.rollDegrees = roll;
	p.twoPoint = twoPoint;
	p.hSize = 1600;
	p.vSize = 600;
	return p;
}

/** „RTX Axonometrie" (freie Axonometrie, projMod 15), gemessen. */
ArchicadProjection Axonometry ()
{
	ArchicadProjection p;
	p.perspective = false;
	const double t[12] = {0.992267084, 0.37626493,  5.31607389e-17, 0, -0.156026446, 0.412278743,
						  0.868180751, 0,           0.305961195,    -0.806865586, 0.438147894,  0};
	std::memcpy (p.tranmat, t, sizeof t);
	p.projMod = 15;
	p.hSize = 1600;
	p.vSize = 600;
	p.zoomScaleX = 32.3290426;
	p.zoomScaleY = 32.3290426;
	p.zoomDispX = 800;
	p.zoomDispY = -300;
	return p;
}

/** Ein Punkt (Archicad) durch die glTF-Kamera in Fensterpixel (Ursprung oben links). */
void ProjectThrough (const GlbCamera& cam, const double p[3], int width, int height, double& px, double& py,
					 double& depth)
{
	const double* q = cam.rotation;  // x, y, z, w
	// Drehmatrix aus dem Quaternion; Spalten = rechts, oben, hinten im glTF-Raum.
	const double x = q[0], y = q[1], z = q[2], w = q[3];
	const double r[3] = {1 - 2 * (y * y + z * z), 2 * (x * y + z * w), 2 * (x * z - y * w)};
	const double u[3] = {2 * (x * y - z * w), 1 - 2 * (x * x + z * z), 2 * (y * z + x * w)};
	const double b[3] = {2 * (x * z + y * w), 2 * (y * z - x * w), 1 - 2 * (x * x + y * y)};
	const double g[3] = {p[0] - cam.translation[0], p[2] - cam.translation[1], -p[1] - cam.translation[2]};
	const double cx = g[0] * r[0] + g[1] * r[1] + g[2] * r[2];
	const double cy = g[0] * u[0] + g[1] * u[1] + g[2] * u[2];
	const double cz = g[0] * b[0] + g[1] * b[1] + g[2] * b[2];
	depth = -cz;
	double nx, ny;
	if (cam.perspective) {
		const double t = std::tan (cam.yfov / 2);
		nx = cx / (depth * t * cam.aspectRatio);
		ny = cy / (depth * t);
	} else {
		nx = cx / cam.xmag;
		ny = cy / cam.ymag;
	}
	px = (nx + 1) / 2 * width;
	py = (1 - ny) / 2 * height;
}

CaptureManifest BaseManifest (const std::string& version)
{
	CaptureManifest manifest;
	manifest.contractVersion = version;
	manifest.captureId = NewUuidV7 ();
	manifest.createdAt = NowTimestampUtc ();
	manifest.source.hostVersion = "28.1";
	manifest.source.hostBuild = "7006";
	manifest.source.pluginIdentifier = "ai.rendertaxi.plugin.archicad";
	manifest.source.pluginVersion = "1.2.0";
	manifest.sourceProjectKey = "archicad:project:test";
	manifest.sourceViewKey = "archicad:view:3d";
	manifest.viewDisplayName = "RTX Perspektive";
	return manifest;
}

CaptureAsset ModelAsset (const std::vector<std::uint8_t>& bytes)
{
	CaptureAsset asset;
	asset.role = kModelRole;
	asset.path = "model/scene.glb";
	asset.mediaType = kModelMediaType;
	asset.byteSize = static_cast<std::int64_t> (bytes.size ());
	asset.sha256 = Sha256::OfString (std::string (bytes.begin (), bytes.end ()));
	return asset;
}

CaptureAsset ImageAsset ()
{
	CaptureAsset asset;
	asset.role = "viewport";
	asset.path = "viewport.png";
	asset.mediaType = "image/png";
	asset.byteSize = 1234;
	asset.sha256 = Sha256::OfString ("bild");
	asset.hasImage = true;
	asset.image = {1600, 600, "srgb", 8, "uint", "rgba"};
	return asset;
}

/** Ein Manifest mit Modell und der Kamera „RTX Zweifluchtpunkt". */
CaptureManifest ModelManifest (const std::string& version, bool withImage)
{
	CaptureManifest manifest = BaseManifest (version);
	const std::vector<std::uint8_t> glb = WriteGlb (TestScene ());
	if (withImage) manifest.assets.push_back (ImageAsset ());
	manifest.assets.push_back (ModelAsset (glb));
	manifest.hasGeometry = true;
	manifest.geometry.assetPath = "model/scene.glb";
	const Result<MappedCamera> camera =
		MapArchicadCamera (Perspective (24, -12, 1.6, 8, 4, 6, 60, 0, true), TestSceneBox (), "RTX Zweifluchtpunkt",
						   "current", 0, 0, ContractMinor (version) >= 6);
	RTX_CHECK (camera.IsOk ());
	manifest.hasCamera = true;
	manifest.camera = camera.Value ().manifest;
	manifest.source.capabilities = {{"viewportCapture", "available"}, {"cameraExport", "available"},
									{"geometryExport", "available"}};
	if (ContractMinor (version) >= 6) manifest.source.capabilities.push_back ({"modelOnlyCapture", "available"});
	if (ContractMinor (version) >= 5) manifest.source.fileName = SourceFileName ("/Users/anna/Projekte/Wohnhaus Süd.pln");
	return manifest;
}

/** `RTX_MANIFEST_SAMPLE` mit einem Zusatz vor `.json` — die Geschwister, die `check-manifest.mjs` prüft. */
std::string SamplePath (const char* suffix)
{
	const char* sample = std::getenv ("RTX_MANIFEST_SAMPLE");
	if (sample == nullptr) return {};
	std::string path = sample;
	const std::size_t dot = path.rfind (".json");
	return path.substr (0, dot) + suffix + ".json";
}

} // namespace

// --- GLB ------------------------------------------------------------------------------

RTX_TEST (GlbQuaderStehtRichtigHerumUndMasshaltig)
{
	GlbSceneBuilder builder;
	const std::uint32_t grau = builder.Material (3, "Grau", 0.5, 0.5, 0.5, 0.0);
	builder.BeginElement ("0F0A1B2C-0000-4000-8000-00000000000A");
	AddBox (builder, grau, {0, 0, 0}, {1, 2, 3});
	RTX_CHECK_EQ (builder.Triangles (), std::uint64_t (12));
	RTX_CHECK_EQ (builder.FlippedPolygons (), std::uint64_t (0));

	GlbStats stats;
	const std::vector<std::uint8_t> bytes = WriteGlb (builder.Scene (), &stats);
	const JsonPtr json = GlbJson (bytes);
	RTX_CHECK_EQ (stats.triangles, std::uint64_t (12));
	RTX_CHECK_EQ (json->Get ("asset")->Get ("version")->StringOr (""), std::string ("2.0"));

	// Archicad (x, y, z) → glTF (x, z, −y): 1 × 2 × 3 m mit z nach oben wird 1 breit, 3 hoch, 2 tief.
	const JsonPtr position = json->Get ("accessors")->Items ()[0];
	RTX_CHECK (Near (At (position->Get ("min"), 0), 0, 1e-9));
	RTX_CHECK (Near (At (position->Get ("min"), 1), 0, 1e-9));
	RTX_CHECK (Near (At (position->Get ("min"), 2), -2, 1e-9));
	RTX_CHECK (Near (At (position->Get ("max"), 0), 1, 1e-9));
	RTX_CHECK (Near (At (position->Get ("max"), 1), 3, 1e-9));
	RTX_CHECK (Near (At (position->Get ("max"), 2), 0, 1e-9));

	// Ein Knoten je Element, die GUID als Name (QA-06).
	const JsonPtr node = json->Get ("nodes")->Items ()[0];
	RTX_CHECK_EQ (node->Get ("name")->StringOr (""), std::string ("0F0A1B2C-0000-4000-8000-00000000000A"));
	RTX_CHECK_EQ (node->Get ("mesh")->IntOr (-1), std::int64_t (0));

	// Oberflächenfarbe sRGB 0,5 → linear 0,214 (QA-03); undurchsichtig ohne `alphaMode`.
	const JsonPtr material = json->Get ("materials")->Items ()[0];
	const JsonPtr color = material->Get ("pbrMetallicRoughness")->Get ("baseColorFactor");
	RTX_CHECK (Near (At (color, 0), 0.214041, 1e-5));
	RTX_CHECK (Near (At (color, 3), 1, 1e-9));
	RTX_CHECK (material->Get ("alphaMode") == nullptr);

	// Keine Verweise nach außen: ein Puffer ohne `uri`.
	RTX_CHECK_EQ (json->Get ("buffers")->Items ().size (), std::size_t (1));
	RTX_CHECK (json->Get ("buffers")->Items ()[0]->Get ("uri") == nullptr);
	RTX_CHECK_EQ (json->Get ("bufferViews")->Items ().size (), std::size_t (3));
}

RTX_TEST (GlbNormalenUndUmlaufsinnZeigenNachAussen)
{
	// Ein Dreieck im Uhrzeigersinn von oben, die Fläche zeigt nach oben: der Schreiber dreht es um.
	GlbSceneBuilder builder;
	const std::uint32_t m = builder.Material (1, "", 1, 1, 1, 0);
	builder.BeginElement ("guid");
	builder.AddConvexPolygon (m, {{0, 0, 0}, {0, 1, 0}, {1, 0, 0}}, {}, {0, 0, 1});
	RTX_CHECK_EQ (builder.FlippedPolygons (), std::uint64_t (1));
	RTX_CHECK_EQ (builder.ZeroNormals (), std::uint64_t (3));
	const GlbPrimitive& prim = builder.Scene ().meshes[0].primitives[0];
	RTX_CHECK_EQ (prim.indices[0], 0u);
	RTX_CHECK_EQ (prim.indices[1], 2u);
	RTX_CHECK_EQ (prim.indices[2], 1u);
	// Die fehlende Normale ist die der Fläche, in glTF-Achsen: Archicad +Z ist glTF +Y.
	RTX_CHECK (Near (prim.normals[1], 1.0, 1e-9));
	RTX_CHECK (Near (prim.normals[0], 0.0, 1e-9));
	// Name fällt auf „Oberfläche <Schlüssel>" zurück.
	RTX_CHECK_EQ (builder.Scene ().materials[0].name, std::string ("Oberfläche 1"));
	// Gleicher Schlüssel, gleiches Material.
	RTX_CHECK_EQ (builder.Material (1, "anders", 0, 0, 0, 0), m);
}

RTX_TEST (GlbTransparenteOberflaecheWirdGemischt)
{
	GlbSceneBuilder builder;
	builder.Material (7, "Glass - Clear Fast", 0.6, 0.8, 0.9, 0.69);
	builder.BeginElement ("g");
	AddBox (builder, 0, {0, 0, 0}, {1, 1, 1});
	const JsonPtr json = GlbJson (WriteGlb (builder.Scene ()));
	const JsonPtr material = json->Get ("materials")->Items ()[0];
	RTX_CHECK_EQ (material->Get ("alphaMode")->StringOr (""), std::string ("BLEND"));
	RTX_CHECK (Near (At (material->Get ("pbrMetallicRoughness")->Get ("baseColorFactor"), 3), 0.31, 1e-6));
}

RTX_TEST (GlbFernVomUrsprungWirdUmDenMittelpunktVerschoben)
{
	// Ein Projekt 5 km vom Ursprung: float32 löst dort nur noch ~0,5 mm auf (QA-02).
	GlbSceneBuilder builder;
	builder.BeginElement ("weit");
	AddBox (builder, builder.Material (1, "", 1, 1, 1, 0), {5000, 2000, 0}, {5001.234, 2002, 3});
	GlbStats stats;
	const JsonPtr json = GlbJson (WriteGlb (builder.Scene (), &stats));
	RTX_CHECK (Near (stats.offset[0], 5001, 1e-9));
	RTX_CHECK (Near (stats.offset[2], -2001, 1e-9));
	// Die Grenzen bleiben die im Projektursprung.
	RTX_CHECK (Near (stats.boundsMin[0], 5000, 1e-9));
	RTX_CHECK (Near (stats.boundsMax[0], 5001.234, 1e-9));

	const JsonPtr root = json->Get ("nodes")->Items ()[0];
	RTX_CHECK_EQ (root->Get ("name")->StringOr (""), std::string ("rendertaxi:origin"));
	RTX_CHECK (Near (At (root->Get ("translation"), 0), 5001, 1e-9));
	RTX_CHECK_EQ (root->Get ("children")->Items ().size (), std::size_t (1));
	RTX_CHECK_EQ (json->Get ("scenes")->Items ()[0]->Get ("nodes")->Items ().size (), std::size_t (1));
	// In den Accessoren steht nur der Rest — auf einen Mikrometer genau.
	const JsonPtr position = json->Get ("accessors")->Items ()[0];
	RTX_CHECK (Near (At (position->Get ("min"), 0), -1, 1e-6));
	RTX_CHECK (Near (At (position->Get ("max"), 0), 0.234, 1e-6));
}

RTX_TEST (GlbLeeresModellWirdNieGesendet)
{
	GlbScene scene;
	scene.meshes.push_back ({"leer", {}});
	const Result<GlbFile> file = FinishGlb (scene, 0);
	RTX_CHECK (!file);
	RTX_CHECK_EQ (file.GetError ().code, std::string (kModelEmpty));
}

RTX_TEST (GlbZuVieleElementeFallenAufEinMeshJeMaterialZurueck)
{
	GlbServerLimits limits;
	limits.maxMeshes = 2;
	const GlbScene scene = TestScene ();
	RTX_CHECK_EQ (CountGlb (scene).meshes, std::uint64_t (3));
	const Result<GlbFile> file = FinishGlb (scene, 0, limits);
	RTX_CHECK (file.IsOk ());
	RTX_CHECK (file.Value ().mergedByMaterial);
	const JsonPtr json = GlbJson (file.Value ().bytes);
	RTX_CHECK_EQ (json->Get ("meshes")->Items ().size (), std::size_t (1));
	RTX_CHECK_EQ (json->Get ("meshes")->Items ()[0]->Get ("primitives")->Items ().size (), std::size_t (2));
	RTX_CHECK_EQ (file.Value ().stats.triangles, std::uint64_t (36));

	// Ohne Grenze bleibt es ein Knoten je Element.
	const Result<GlbFile> normal = FinishGlb (scene, 0);
	RTX_CHECK (normal.IsOk ());
	RTX_CHECK (!normal.Value ().mergedByMaterial);
	RTX_CHECK_EQ (normal.Value ().stats.meshes, std::uint64_t (3));
}

RTX_TEST (GlbGrenzenGreifenVorDemSendenUndNennenDieZahl)
{
	GlbServerLimits limits;
	limits.maxTriangles = 20;
	const Result<GlbFile> triangles = FinishGlb (TestScene (), 0, limits);
	RTX_CHECK (!triangles);
	RTX_CHECK_EQ (triangles.GetError ().code, std::string (errc::LimitExceeded));
	RTX_CHECK (triangles.GetError ().message.find ("36 Dreiecke") != std::string::npos);

	const Result<GlbFile> bytes = FinishGlb (TestScene (), 1024);
	RTX_CHECK (!bytes);
	RTX_CHECK (bytes.GetError ().message.find ("MB") != std::string::npos);
	RTX_CHECK (bytes.GetError ().message.find ("% zu viel") != std::string::npos);

	// Tausenderpunkte wie AERO: 334.394 Dreiecke.
	GlbCounts counts;
	counts.triangles = 334394;
	limits.maxTriangles = 1000;
	RTX_CHECK (CheckGlbStructure (counts, limits).message.find ("334.394") != std::string::npos);
}

// --- Kameras ---------------------------------------------------------------------------

RTX_TEST (KameraPerspektiveWieGemessen)
{
	struct Case {
		const char* name;
		ArchicadProjection projection;
		double translation[3];
		double rotation[4];
		double yfov;
	};
	const Case cases[] = {
		{"RTX Perspektive", Perspective (5, -15, 1.6, 5, 2, 1.5, 60, 0, false), {5, 1.6, 15},
		 {-0.00294113831, 0, 0, 0.999995675}, 0.42643102},
		{"RTX Rollwinkel", Perspective (5, -15, 1.6, 5, 2, 1.5, 60, 10, false), {5, 1.6, 15},
		 {-0.00292994639, 0.000256337094, 0.0871553658, 0.996190389}, 0.42643102},
		{"RTX Weitwinkel", Perspective (-8, -6, 12, 8, 10, 0, 90, 0, false), {-8, 12, 6},
		 {-0.223024396, -0.371365851, -0.0923797296, 0.896556473}, 0.717541341},
		{"Physical Model",
		 Perspective (17.1097626, -22.6715493, 15.7680828, 10.6237395, 7.27755533, 0.247458987, 75, 0, false),
		 {17.1097626, 15.7680828, 22.6715493}, {-0.230953413, 0.103524363, 0.0247220252, 0.967125663}, 0.560357046},
		{"RTX Zweifluchtpunkt", Perspective (24, -12, 1.6, 8, 4, 6, 60, 0, true), {24, 1.6, 12},
		 {0, 0.382683432, 0, 0.923879533}, 0.42643102},
	};
	for (const Case& c : cases) {
		const Result<MappedCamera> mapped = MapArchicadCamera (c.projection, TestSceneBox (), c.name, "current");
		RTX_CHECK (mapped.IsOk ());
		if (!mapped) continue;
		const GlbCamera& cam = mapped.Value ().gltf;
		for (int k = 0; k < 3; ++k) RTX_CHECK (Near (cam.translation[k], c.translation[k], 1e-6));
		for (int k = 0; k < 4; ++k) RTX_CHECK (Near (cam.rotation[k], c.rotation[k], 1e-6));
		RTX_CHECK (Near (cam.yfov, c.yfov, 1e-6));
		RTX_CHECK (Near (cam.aspectRatio, 1600.0 / 600.0, 1e-9));
		RTX_CHECK_EQ (mapped.Value ().fidelity, std::string ("exact"));

		// Kamerablock: derselbe Standort, `viewCone` waagerecht unverändert (Q-01), Vektoren normiert.
		const CaptureCamera& block = mapped.Value ().manifest;
		RTX_CHECK_EQ (block.projection, std::string ("perspective"));
		RTX_CHECK_EQ (block.fovAxis, std::string ("horizontal"));
		RTX_CHECK (Near (block.fovAngle, c.projection.viewConeDegrees * kPi / 180, 1e-12));
		for (int k = 0; k < 3; ++k) RTX_CHECK (Near (block.position[k], c.translation[k], 1e-6));
		const double len = std::sqrt (block.direction[0] * block.direction[0] + block.direction[1] * block.direction[1] +
									  block.direction[2] * block.direction[2]);
		RTX_CHECK (Near (len, 1, 1e-12));
		RTX_CHECK (block.clipFar == 0.0);
	}
}

RTX_TEST (KameraZweifluchtpunktIstSenkrechterShift)
{
	const Result<MappedCamera> mapped =
		MapArchicadCamera (Perspective (24, -12, 1.6, 8, 4, 6, 60, 0, true), TestSceneBox (), "RTX Zweifluchtpunkt",
						   "view:F65D2432-5136-544E-AD6C-8D41AD852364");
	RTX_CHECK (mapped.IsOk ());
	// Gemessen 0,16840242; im Vertrag auf sechs Stellen (ratio).
	RTX_CHECK (Near (mapped.Value ().manifest.shiftY, 0.168402, 1e-12));
	RTX_CHECK (mapped.Value ().manifest.shiftX == 0.0);
	// Waagerechte Blickrichtung: kein Anteil nach oben (glTF +Y).
	RTX_CHECK (Near (mapped.Value ().manifest.direction[1], 0, 1e-12));

	GlbScene scene = TestScene ();
	scene.cameras.push_back (mapped.Value ().gltf);
	const JsonPtr json = GlbJson (WriteGlb (scene));
	const JsonPtr camera = json->Get ("cameras")->Items ()[0];
	RTX_CHECK_EQ (camera->Get ("type")->StringOr (""), std::string ("perspective"));
	const JsonPtr shift = camera->Get ("extras")->Get ("rendertaxi")->Get ("camera")->Get ("shift");
	RTX_CHECK (Near (shift->Get ("y")->NumberOr (0), 0.168402, 1e-12));
	// Nur `camera` unter `rendertaxi` (gltf-camera-extras.schema.json).
	RTX_CHECK_EQ (camera->Get ("extras")->Get ("rendertaxi")->Fields ().size (), std::size_t (1));
	// Die Herkunft steht am Knoten, nicht an der Kameradefinition.
	const JsonPtr node = json->Get ("nodes")->Items ()[3];
	RTX_CHECK_EQ (node->Get ("camera")->IntOr (-1), std::int64_t (0));
	RTX_CHECK_EQ (node->Get ("extras")->Get ("rendertaxi")->Get ("source")->StringOr (""),
				  std::string ("view:F65D2432-5136-544E-AD6C-8D41AD852364"));
}

RTX_TEST (KameraParallelTrifftDieArchicadPixel)
{
	const ArchicadProjection p = Axonometry ();
	const Result<MappedCamera> mapped = MapArchicadCamera (p, TestSceneBox (), "RTX Axonometrie", "current");
	RTX_CHECK (mapped.IsOk ());
	const GlbCamera& cam = mapped.Value ().gltf;
	RTX_CHECK (!cam.perspective);
	RTX_CHECK_EQ (mapped.Value ().fidelity, std::string ("exact"));
	// Gemessen auf dev: xmag 23,32, ymag 9,53 (QA-11).
	RTX_CHECK (Near (cam.xmag, 23.32, 0.01));
	RTX_CHECK (Near (cam.ymag, 9.53, 0.01));
	RTX_CHECK (Near (mapped.Value ().manifest.halfWidth, cam.xmag, 1e-12));

	// Jede Ecke der Szene landet dort, wo Archicad sie zeichnet (Fenster 1600 × 600 px):
	//   x = zoomDispX + zoomScaleX · u,   y = −(zoomDispY + zoomScaleY · v),   (u, v) = tranmat · p.
	const SceneBox box = TestSceneBox ();
	for (int i = 0; i < 8; ++i) {
		const double corner[3] = {(i & 1) ? box.max[0] : box.min[0], (i & 2) ? box.max[1] : box.min[1],
								  (i & 4) ? box.max[2] : box.min[2]};
		const double* t = p.tranmat;
		const double u = t[0] * corner[0] + t[1] * corner[1] + t[2] * corner[2] + t[3];
		const double v = t[4] * corner[0] + t[5] * corner[1] + t[6] * corner[2] + t[7];
		const double ax = p.zoomDispX + p.zoomScaleX * u;
		const double ay = -(p.zoomDispY + p.zoomScaleY * v);
		double px, py, depth;
		ProjectThrough (cam, corner, p.hSize, p.vSize, px, py, depth);
		RTX_CHECK (Near (px, ax, 1e-6));
		// Senkrecht ein Viertelpixel: die Bildachsen der freien Axonometrie stehen um 0,02° schief
		// (gemessen), eine glTF-Kamera ist rechtwinklig. Unter einem halben Pixel heißt „exakt" (QA-05).
		RTX_CHECK (Near (py, ay, 0.5));
		// Zwischen nah und fern, mit Abstand.
		RTX_CHECK (depth > cam.znear);
		RTX_CHECK (depth < cam.zfar);
	}
}

RTX_TEST (KameraParallelSchneidetEinEntferntesModellNichtAb)
{
	// F-01 an PR #303 (Rhino): eine feste Fernebene schnitt ein Modell 100 m vor der Kamera ab. Hier
	// liegt die Szene 5 km vor und 5 km hinter der Bildebene — beide Male zwischen nah und fern.
	const ArchicadProjection p = Axonometry ();
	const Result<MappedCamera> base = MapArchicadCamera (p, TestSceneBox (), "a", "current");
	RTX_CHECK (base.IsOk ());
	const double* d = base.Value ().manifest.direction;  // glTF
	const double archicadDir[3] = {d[0], -d[2], d[1]};
	for (const double distance : {5000.0, -5000.0}) {
		SceneBox box = TestSceneBox ();
		for (int k = 0; k < 3; ++k) {
			box.min[k] += archicadDir[k] * distance;
			box.max[k] += archicadDir[k] * distance;
		}
		const Result<MappedCamera> mapped = MapArchicadCamera (p, box, "a", "current");
		RTX_CHECK (mapped.IsOk ());
		const GlbCamera& cam = mapped.Value ().gltf;
		for (int i = 0; i < 8; ++i) {
			const double corner[3] = {(i & 1) ? box.max[0] : box.min[0], (i & 2) ? box.max[1] : box.min[1],
									  (i & 4) ? box.max[2] : box.min[2]};
			double px, py, depth;
			ProjectThrough (cam, corner, p.hSize, p.vSize, px, py, depth);
			RTX_CHECK (depth > cam.znear);
			RTX_CHECK (depth < cam.zfar);
		}
		RTX_CHECK (mapped.Value ().manifest.clipFar > mapped.Value ().manifest.clipNear);
	}
}

RTX_TEST (KameraFrontalAxonometrieWirdGenaehert)
{
	ArchicadProjection p;
	p.perspective = false;
	const double t[12] = {1, 0.3535534, 6.123234e-17, 0, -1.3962634e-16, 0.3535534, 1, 0,
						  0.282842717, -0.799999991, 0.282842717, 0};
	std::memcpy (p.tranmat, t, sizeof t);
	p.projMod = 4;
	p.hSize = 1600;
	p.vSize = 600;
	p.zoomScaleX = 26.4880502;
	p.zoomScaleY = 26.4880502;
	p.zoomDispX = 112.224577;
	p.zoomDispY = -565.52431;
	const Result<MappedCamera> mapped = MapArchicadCamera (p, TestSceneBox (), "Frontal", "current");
	RTX_CHECK (mapped.IsOk ());
	RTX_CHECK_EQ (mapped.Value ().fidelity, std::string ("approximated"));
	RTX_CHECK (mapped.Value ().note.find ("6.4") != std::string::npos);
	// Trotz Scherung eine echte Drehung: Quaternion der Länge 1, oben senkrecht zur Blickrichtung.
	const double* q = mapped.Value ().gltf.rotation;
	RTX_CHECK (Near (q[0] * q[0] + q[1] * q[1] + q[2] * q[2] + q[3] * q[3], 1, 1e-9));
	const CaptureCamera& block = mapped.Value ().manifest;
	RTX_CHECK (Near (block.direction[0] * block.up[0] + block.direction[1] * block.up[1] +
						 block.direction[2] * block.up[2],
					 0, 1e-9));
}

RTX_TEST (KameraOhneBrauchbareProjektionScheitertLesbar)
{
	RTX_CHECK (!MapArchicadCamera (Perspective (1, 1, 1, 1, 1, 1, 60, 0, false), {}, "a", "current"));
	ArchicadProjection noSize = Perspective (0, 0, 0, 0, 1, 0, 60, 0, false);
	noSize.hSize = 0;
	RTX_CHECK (!MapArchicadCamera (noSize, {}, "a", "current"));
	ArchicadProjection axo = Axonometry ();
	for (double& value : axo.tranmat) value = 0;
	RTX_CHECK (!MapArchicadCamera (axo, TestSceneBox (), "a", "current"));
	// Ohne Ausschnitt des Fensters, aber mit Szene: genähert, die ganze Szene im Bild.
	ArchicadProjection noZoom = Axonometry ();
	noZoom.zoomScaleX = 0;
	const Result<MappedCamera> approximated = MapArchicadCamera (noZoom, TestSceneBox (), "a", "current");
	RTX_CHECK (approximated.IsOk ());
	RTX_CHECK_EQ (approximated.Value ().fidelity, std::string ("approximated"));
	RTX_CHECK (!MapArchicadCamera (noZoom, {}, "a", "current"));
}

RTX_TEST (KameraNenntDieBildgroesseNurAufWunsch)
{
	const ArchicadProjection p = Perspective (5, -15, 1.6, 5, 2, 1.5, 60, 0, false);
	const Result<MappedCamera> without = MapArchicadCamera (p, TestSceneBox (), "a", "current");
	RTX_CHECK (!without.Value ().manifest.HasResolution ());
	const Result<MappedCamera> with = MapArchicadCamera (p, TestSceneBox (), "a", "current", 1920, 1080, true);
	RTX_CHECK_EQ (with.Value ().manifest.resolutionWidth, 1920);
	RTX_CHECK_EQ (with.Value ().manifest.resolutionHeight, 1080);
	RTX_CHECK (Near (with.Value ().gltf.aspectRatio, 1920.0 / 1080.0, 1e-12));
}

RTX_TEST (KameraFuerEinenZuschnittDesFensters)
{
	// Das Bild des Bildwegs ist ein mittiger Zuschnitt des Fensters auf die Rendering-Szene. Die Kamera
	// beschreibt denselben Zuschnitt: ein Punkt am Rand des Zuschnitts liegt am Rand des Bildes.
	const ArchicadProjection p = Perspective (5, -15, 1.6, 5, 2, 1.5, 60, 0, false);  // Fenster 1600 × 600
	const double windowHalfTanH = std::tan (kPi / 6);
	const double windowHalfTanV = windowHalfTanH / (1600.0 / 600.0);

	// Schmaler (4:3): die Höhe des Fensters bleibt, die Breite schrumpft auf 800 Pixel.
	const Result<MappedCamera> narrow = MapArchicadCamera (p, TestSceneBox (), "a", "current", 1024, 768, true);
	RTX_CHECK (Near (std::tan (narrow.Value ().gltf.yfov / 2), windowHalfTanV, 1e-12));
	RTX_CHECK (Near (std::tan (narrow.Value ().manifest.fovAngle / 2), windowHalfTanV * 1024.0 / 768.0, 1e-12));
	// Breiter (4:1): die Breite bleibt, viewCone unverändert.
	const Result<MappedCamera> wide = MapArchicadCamera (p, TestSceneBox (), "a", "current", 2400, 600, true);
	RTX_CHECK (Near (wide.Value ().manifest.fovAngle, kPi / 3, 1e-12));
	RTX_CHECK (Near (std::tan (wide.Value ().gltf.yfov / 2), windowHalfTanH / 4.0, 1e-12));

	// Parallel: dieselben Archicad-Pixel, nur innerhalb des Zuschnitts von 800 × 600 in der Mitte.
	const ArchicadProjection a = Axonometry ();
	const Result<MappedCamera> crop = MapArchicadCamera (a, TestSceneBox (), "a", "current", 800, 600, true);
	RTX_CHECK (crop.IsOk ());
	const SceneBox box = TestSceneBox ();
	for (int i = 0; i < 8; ++i) {
		const double corner[3] = {(i & 1) ? box.max[0] : box.min[0], (i & 2) ? box.max[1] : box.min[1],
								  (i & 4) ? box.max[2] : box.min[2]};
		const double* t = a.tranmat;
		const double u = t[0] * corner[0] + t[1] * corner[1] + t[2] * corner[2] + t[3];
		const double ax = a.zoomDispX + a.zoomScaleX * u - 400;  // Zuschnitt beginnt bei x = 400
		double px, py, depth;
		ProjectThrough (crop.Value ().gltf, corner, 800, 600, px, py, depth);
		RTX_CHECK (Near (px, ax, 1e-6));
	}
}

// --- Capture-Manifest bis 1.6.0 -----------------------------------------------------------

RTX_TEST (ManifestNurModellAbFassung16)
{
	CaptureManifest manifest = ModelManifest ("1.6.0", false);
	const Status valid = manifest.Validate ();
	RTX_CHECK (valid.IsOk ());
	if (!valid) std::cerr << valid.GetError ().message << " " << valid.GetError ().pointer << "\n";
	const Result<std::string> text = manifest.Serialize ();
	RTX_CHECK (text.IsOk ());
	if (!text) return;
	RTX_CHECK (text.Value ().find ("\"contractVersion\": \"1.6.0\"") != std::string::npos);
	RTX_CHECK (text.Value ().find ("\"fileName\": \"Wohnhaus Süd.pln\"") != std::string::npos);
	RTX_CHECK (text.Value ().find ("\"resolution\"") != std::string::npos);
	RTX_CHECK (text.Value ().find ("\"y\": 0.168402") != std::string::npos);
	RTX_CHECK (text.Value ().find ("\"upAxis\": \"z\"") != std::string::npos);
	// Die Modelldatei trägt keinen image-Block.
	RTX_CHECK (text.Value ().find ("\"image\"") == std::string::npos);
	const std::string sample = SamplePath ("-model-only");
	if (!sample.empty ()) RTX_CHECK (WriteTextFile (sample, text.Value ()));

	// Ohne Bildgröße sagt nichts, welche Form der Rahmen hat.
	manifest.camera.resolutionWidth = 0;
	manifest.camera.resolutionHeight = 0;
	RTX_CHECK (!manifest.Validate ());
	manifest.hasCamera = false;
	RTX_CHECK (!manifest.Validate ());
}

RTX_TEST (ManifestBildUndModell)
{
	const CaptureManifest manifest = ModelManifest ("1.6.0", true);
	const Result<std::string> text = manifest.Serialize ();
	RTX_CHECK (text.IsOk ());
	if (!text) return;
	const std::string sample = SamplePath ("-image-model");
	if (!sample.empty ()) RTX_CHECK (WriteTextFile (sample, text.Value ()));

	// Gegen einen Server mit 1.4: kein Dateiname, keine Bildgröße, aber Shift.
	const CaptureManifest older = ModelManifest ("1.4.0", true);
	RTX_CHECK (older.Validate ().IsOk ());
	const std::string olderText = older.Serialize ().Value ();
	RTX_CHECK (olderText.find ("fileName") == std::string::npos);
	RTX_CHECK (olderText.find ("resolution") == std::string::npos);
	RTX_CHECK (olderText.find ("\"shift\"") != std::string::npos);
	const std::string olderSample = SamplePath ("-image-model-1.4");
	if (!olderSample.empty ()) RTX_CHECK (WriteTextFile (olderSample, olderText));
}

RTX_TEST (ManifestHaeltJedeFassungAnIhreFelder)
{
	// Das Modell allein ist erst ab 1.6.0 ein Capture.
	RTX_CHECK (!ModelManifest ("1.5.0", false).Validate ());
	// Shift erst ab 1.4.0.
	RTX_CHECK (!ModelManifest ("1.3.0", true).Validate ());
	// Modell erst ab 1.2.0.
	{
		CaptureManifest manifest = ModelManifest ("1.4.0", true);
		manifest.contractVersion = "1.1.0";
		manifest.source.fileName.clear ();
		RTX_CHECK (!manifest.Validate ());
	}
	// Dateiname erst ab 1.5.0, Capabilities erst ab 1.1.0, modelOnlyCapture erst ab 1.6.0.
	{
		CaptureManifest manifest = BaseManifest ("1.4.0");
		manifest.assets.push_back (ImageAsset ());
		RTX_CHECK (manifest.Validate ().IsOk ());
		manifest.source.fileName = "Haus.pln";
		RTX_CHECK (!manifest.Validate ());
		manifest.source.fileName.clear ();
		manifest.source.capabilities = {{"modelOnlyCapture", "available"}};
		RTX_CHECK (!manifest.Validate ());
		manifest.source.capabilities = {{"viewportCapture", "available"}};
		RTX_CHECK (manifest.Validate ().IsOk ());
		manifest.contractVersion = "1.0.0";
		RTX_CHECK (!manifest.Validate ());
		manifest.source.capabilities.clear ();
		RTX_CHECK (manifest.Validate ().IsOk ());
		// Ab 1.3.0 ist jedes Bild PNG.
		manifest.contractVersion = "1.3.0";
		manifest.assets[0].mediaType = "image/jpeg";
		RTX_CHECK (!manifest.Validate ());
		// Neuer, als der Kern schreibt.
		manifest.assets[0].mediaType = "image/png";
		manifest.contractVersion = "1.7.0";
		RTX_CHECK (!manifest.Validate ());
	}
	// `geometry` steht genau dann, wenn das Modell da ist, und zeigt auf es.
	{
		CaptureManifest manifest = ModelManifest ("1.6.0", true);
		manifest.geometry.assetPath = "model/anders.glb";
		RTX_CHECK (!manifest.Validate ());
		manifest.hasGeometry = false;
		RTX_CHECK (!manifest.Validate ());
	}
	// Die Modelldatei ist model/gltf-binary und trägt kein Bild.
	{
		CaptureManifest manifest = ModelManifest ("1.6.0", true);
		manifest.assets[1].mediaType = "application/octet-stream";
		RTX_CHECK (!manifest.Validate ());
	}
	// Kamera: up nicht parallel, Winkel echt zwischen 0 und π.
	{
		CaptureManifest manifest = ModelManifest ("1.6.0", true);
		for (int k = 0; k < 3; ++k) manifest.camera.up[k] = manifest.camera.direction[k];
		RTX_CHECK (!manifest.Validate ());
		manifest = ModelManifest ("1.6.0", true);
		manifest.camera.fovAngle = kPi;
		RTX_CHECK (!manifest.Validate ());
		manifest = ModelManifest ("1.6.0", true);
		manifest.camera.shiftY = 2.5;
		RTX_CHECK (!manifest.Validate ());
	}
}

RTX_TEST (ManifestOhneMinusNullUndMitKanonischenZahlen)
{
	CaptureManifest manifest = ModelManifest ("1.6.0", true);
	manifest.camera.position[0] = -1e-9;
	manifest.camera.direction[0] = 1;
	manifest.camera.direction[1] = 0;
	manifest.camera.direction[2] = -0.0;
	manifest.camera.up[0] = 0;
	manifest.camera.up[1] = 1;
	manifest.camera.up[2] = 0;
	const std::string text = manifest.Serialize ().Value ();
	RTX_CHECK (text.find ("-0.0") == std::string::npos);
}

RTX_TEST (DateinameFolgtDerGemeinsamenFixture)
{
	// `fixtures/v1/negative/source-file-names.json` hält Python-Client und Server zusammen — und nun
	// auch den C++-Kern: was er schreibt, nimmt der Server an; was er weglässt, hätte er abgelehnt.
	std::ifstream in (RTX_SHARED_FIXTURES "/negative/source-file-names.json", std::ios::binary);
	RTX_CHECK (in.good ());
	std::stringstream buffer;
	buffer << in.rdbuf ();
	const JsonPtr root = Json::Parse (buffer.str ());
	RTX_CHECK (root != nullptr);
	if (!root) return;
	int cases = 0;
	for (const JsonPtr& item : root->Get ("cases")->Items ()) {
		const std::string raw = item->Get ("raw")->StringOr ("");
		const JsonPtr expected = item->Get ("expected");
		const std::string want = expected->IsNull () ? std::string () : expected->StringOr ("");
		const std::string got = SourceFileName (raw);
		if (got != want) std::cerr << "  Fall: " << item->Get ("why")->StringOr ("") << "\n";
		RTX_CHECK_EQ (got, want);
		++cases;
	}
	RTX_CHECK (cases >= 16);
}

// --- Wege -----------------------------------------------------------------------------

RTX_TEST (WegeFallenGegenAeltereServerZurueckStattAbzubrechen)
{
	// Noch nicht verbunden: die Wahl gilt, wie sie ist.
	RTX_CHECK (PlanCapture (false, true, -1).Value ().ModelOnly ());
	// Nichts gewählt ist der einzige Fehler.
	RTX_CHECK (!PlanCapture (false, false, 7));
	RTX_CHECK_EQ (PlanCapture (false, false, 7).GetError ().message, std::string (kNothingChosen));

	// Ab 1.6: nur Modell.
	const CapturePlan modelOnly = PlanCapture (false, true, 7).Value ();
	RTX_CHECK (modelOnly.ModelOnly ());
	RTX_CHECK (modelOnly.hint.empty ());
	RTX_CHECK_EQ (PlanContractVersion (modelOnly, 7), std::string ("1.6.0"));
	RTX_CHECK_EQ (PlanSummary (modelOnly), std::string ("Gesendet wird: nur Modell und Kamera — ohne Rendern."));

	// Regel 3: gemerktes „nur Modell" gegen 1.5 → Bild und Modell, mit Hinweis.
	const CapturePlan fallback = PlanCapture (false, true, 5).Value ();
	RTX_CHECK (fallback.image && fallback.model);
	RTX_CHECK_EQ (fallback.hint, std::string (kModelOnlyFallback));
	RTX_CHECK_EQ (PlanContractVersion (fallback, 5), std::string ("1.5.0"));

	// Gegen einen Server ohne Modell (unter 1.2): nur Bild, mit Hinweis.
	const CapturePlan noModel = PlanCapture (true, true, 1).Value ();
	RTX_CHECK (noModel.image && !noModel.model);
	RTX_CHECK_EQ (noModel.hint, std::string (kModelUnsupported));
	RTX_CHECK_EQ (PlanContractVersion (noModel, 1), std::string ("1.1.0"));

	// Der Bildweg schreibt gegen einen Server mit 1.0 weiter 1.0.0 — so wie das Add-on bis 1.1.
	RTX_CHECK_EQ (PlanContractVersion (PlanCapture (true, false, 0).Value (), 0), std::string ("1.0.0"));
	RTX_CHECK_EQ (ImageContractVersion (-1), std::string ("1.0.0"));
	RTX_CHECK_EQ (ImageContractVersion (4), std::string ("1.3.0"));
	RTX_CHECK_EQ (ModelContractVersion (4), std::string ("1.4.0"));
	RTX_CHECK_EQ (ModelContractVersion (1), std::string ());

	const std::vector<std::string> steps = PlanSteps (PlanCapture (true, true, 7).Value ());
	RTX_CHECK_EQ (steps.size (), std::size_t (6));
	RTX_CHECK_EQ (steps.front (), std::string ("Bild aufnehmen"));
	RTX_CHECK_EQ (PlanLabel (modelOnly), std::string ("Modell"));

	CaptureResult result;
	result.assetIdsByRole = {{"model", "a"}};
	RTX_CHECK_EQ (PlanResultText (result, true),
				  std::string ("Modell und Kamera übernommen. Ein Bild am Blickpunkt bleibt, wie es ist."));
	result.assetIdsByRole = {{"viewport", "b"}, {"model", "a"}};
	RTX_CHECK_EQ (PlanResultText (result, true), std::string ("Bild und Modell übernommen."));
}

RTX_TEST (HoechsteFassungKommtAusDerAushandlung)
{
	HandshakeInfo handshake;
	RTX_CHECK_EQ (HighestCaptureMinor (handshake), -1);
	handshake.negotiationContract = kCaptureContract;
	handshake.highestSupportedVersion = "1.7.0";
	RTX_CHECK_EQ (HighestCaptureMinor (handshake), 7);
	handshake.highestSupportedVersion = "2.0.0";
	RTX_CHECK_EQ (HighestCaptureMinor (handshake), -1);
	handshake.highestSupportedVersion = "Unsinn";
	RTX_CHECK_EQ (HighestCaptureMinor (handshake), -1);
}

// --- Golden-Datei ---------------------------------------------------------------------------

namespace {

/** Gleiche JSON-Bäume, Zahlen auf `tolerance` genau — die Golden-Datei soll auch unter MSVC halten. */
bool SameJson (const JsonPtr& a, const JsonPtr& b, double tolerance, const std::string& at)
{
	if (a == nullptr || b == nullptr) return a == b;
	if (a->GetKind () != b->GetKind ()) {
		std::cerr << "  Abweichung bei " << at << "\n";
		return false;
	}
	switch (a->GetKind ()) {
		case Json::Kind::Number:
			if (!Near (a->NumberOr (0), b->NumberOr (0), tolerance * std::max (1.0, std::fabs (b->NumberOr (0))))) {
				std::cerr << "  Abweichung bei " << at << ": " << a->NumberOr (0) << " statt " << b->NumberOr (0) << "\n";
				return false;
			}
			return true;
		case Json::Kind::Array:
			if (a->Items ().size () != b->Items ().size ()) return false;
			for (std::size_t i = 0; i < a->Items ().size (); ++i)
				if (!SameJson (a->Items ()[i], b->Items ()[i], tolerance, at + "/" + std::to_string (i))) return false;
			return true;
		case Json::Kind::Object:
			if (a->Fields ().size () != b->Fields ().size ()) return false;
			for (std::size_t i = 0; i < a->Fields ().size (); ++i) {
				if (a->Fields ()[i].first != b->Fields ()[i].first) return false;
				if (!SameJson (a->Fields ()[i].second, b->Fields ()[i].second, tolerance, at + "/" + a->Fields ()[i].first))
					return false;
			}
			return true;
		default:
			return a->Serialize () == b->Serialize ();
	}
}

} // namespace

RTX_TEST (GlbGoldenDateiDerTestszene)
{
	// Die Testszene des Messauftrags mit fünf Kameras — die Datei, die Validator und Server-Prüfer in
	// `tests/contract/gltf-camera-extras.test.ts` lesen. `RTX_GLB_GOLDEN_WRITE=1` schreibt sie neu.
	GlbScene scene = TestScene ();
	scene.generator = "rdtx.ai Archicad add-on (core test)";
	const SceneBox box = TestSceneBox ();
	ArchicadProjection frontal;
	frontal.perspective = false;
	const double t[12] = {1, 0.3535534, 6.123234e-17, 0, -1.3962634e-16, 0.3535534, 1, 0,
						  0.282842717, -0.799999991, 0.282842717, 0};
	std::memcpy (frontal.tranmat, t, sizeof t);
	frontal.hSize = 1600;
	frontal.vSize = 600;
	frontal.zoomScaleX = 26.4880502;
	frontal.zoomScaleY = 26.4880502;
	frontal.zoomDispX = 112.224577;
	frontal.zoomDispY = -565.52431;
	const struct {
		const char* name;
		ArchicadProjection projection;
	} views[] = {
		{"RTX Perspektive", Perspective (5, -15, 1.6, 5, 2, 1.5, 60, 0, false)},
		{"RTX Rollwinkel", Perspective (5, -15, 1.6, 5, 2, 1.5, 60, 10, false)},
		{"RTX Zweifluchtpunkt", Perspective (24, -12, 1.6, 8, 4, 6, 60, 0, true)},
		{"RTX Axonometrie", Axonometry ()},
		{"Frontal-Axonometrie", frontal},
	};
	for (const auto& view : views) {
		const Result<MappedCamera> mapped = MapArchicadCamera (view.projection, box, view.name, "current");
		RTX_CHECK (mapped.IsOk ());
		if (mapped) scene.cameras.push_back (mapped.Value ().gltf);
	}
	const Result<GlbFile> file = FinishGlb (scene, 209715200);
	RTX_CHECK (file.IsOk ());
	if (!file) return;
	const std::vector<std::uint8_t>& bytes = file.Value ().bytes;

	const std::string golden = RTX_SHARED_FIXTURES "/../gltf-camera-extras/archicad-cameras.glb";
	if (std::getenv ("RTX_GLB_GOLDEN_WRITE") != nullptr) {
		std::ofstream out (golden, std::ios::binary);
		out.write (reinterpret_cast<const char*> (bytes.data ()), static_cast<std::streamsize> (bytes.size ()));
		RTX_CHECK (out.good ());
		return;
	}
	std::ifstream in (golden, std::ios::binary);
	RTX_CHECK (in.good ());
	const std::vector<std::uint8_t> stored ((std::istreambuf_iterator<char> (in)), std::istreambuf_iterator<char> ());
	RTX_CHECK_EQ (stored.size (), bytes.size ());
	if (stored.size () != bytes.size () || stored.size () < 20) return;
	// Der Binärteil ist bitgleich, der JSON-Teil auf 1e-7 genau.
	const std::uint32_t jsonLength = U32 (bytes, 12);
	RTX_CHECK (std::equal (bytes.begin () + 20 + jsonLength, bytes.end (), stored.begin () + 20 + jsonLength));
	RTX_CHECK (SameJson (GlbJson (bytes), GlbJson (stored), 1e-7, ""));
}

// --- F-01 an PR #311: Zahlen aus fremdem Text ohne Ausnahme und ohne Überlauf ---------------

RTX_TEST (FassungMitUebergrosserZahlIstKeineFassung)
{
	bool threw = false;
	try {
		for (const char* text : {"1.999999999999999999999.0", "1.2147483648.0", "99999999999999999999.6.0",
								 "1.6.99999999999999999999", "1.1000000001.0"})
			RTX_CHECK_EQ (ContractMinor (text), -1);
		// Gültige Fassungen unverändert.
		RTX_CHECK_EQ (ContractMinor ("1.0.0"), 0);
		RTX_CHECK_EQ (ContractMinor ("1.6.0"), 6);
		RTX_CHECK_EQ (ContractMinor ("1.7.12"), 7);
		RTX_CHECK_EQ (ContractMinor ("1.1000000000.0"), 1000000000);
		RTX_CHECK_EQ (ContractMinor ("2.0.0"), -1);
		RTX_CHECK_EQ (ContractMinor ("1.6"), -1);
		RTX_CHECK_EQ (ContractMinor (""), -1);

		HandshakeInfo handshake;
		handshake.negotiationContract = kCaptureContract;
		handshake.highestSupportedVersion = "1.999999999999999999999.0";
		RTX_CHECK_EQ (HighestCaptureMinor (handshake), -1);

		// Ein Manifest mit solcher Fassung ist ein Prüffehler, keine Ausnahme.
		CaptureManifest manifest = BaseManifest ("1.999999999999999999999.0");
		manifest.assets.push_back (ImageAsset ());
		const Status valid = manifest.Validate ();
		RTX_CHECK (!valid);
		RTX_CHECK_EQ (valid.GetError ().code, std::string (errc::SchemaInvalid));
		RTX_CHECK_EQ (valid.GetError ().pointer, std::string ("/contractVersion"));
		RTX_CHECK (!manifest.Serialize ());
		// Dieselbe Fassung mit kleiner Zahl bleibt gültig.
		manifest.contractVersion = "1.6.0";
		RTX_CHECK (manifest.Validate ().IsOk ());
	} catch (...) {
		threw = true;
	}
	RTX_CHECK (!threw);
}

RTX_TEST (BegrenzteZahlenAusFremdemText)
{
	int value = 7;
	RTX_CHECK (ParseBoundedInt ("0", value));
	RTX_CHECK_EQ (value, 0);
	RTX_CHECK (ParseBoundedInt ("1000000000", value));
	RTX_CHECK_EQ (value, 1000000000);
	value = 7;
	for (const char* bad : {"", "1000000001", "99999999999999999999999", "-1", "+1", " 1", "1 ", "1a", "0x10", "1.5"}) {
		RTX_CHECK (!ParseBoundedInt (bad, value));
		RTX_CHECK_EQ (value, 7);
	}
	RTX_CHECK (!ParseBoundedInt ("3601", value, 3600));
	RTX_CHECK (ParseBoundedInt ("3600", value, 3600));

	// Das Ausgabeziel aus dem Rezept: eine übergroße Zahl ist kein Seitenverhältnis und keine Größe.
	RTX_CHECK (!ParseDesiredOutput (Json::Parse (R"({"kind":"aspect_ratio","value":"99999999999:1"})")).known);
	RTX_CHECK (!ParseDesiredOutput (Json::Parse (R"({"kind":"aspect_ratio","value":"16:99999999999999999999"})")).known);
	RTX_CHECK (!ParseDesiredOutput (Json::Parse (R"({"kind":"exact","width":99999999999,"height":1080})")).known);
	const DesiredOutput good = ParseDesiredOutput (Json::Parse (R"({"kind":"aspect_ratio","value":"16:9"})"));
	RTX_CHECK (good.known);
	RTX_CHECK_EQ (good.aspectWidth, 16);
	RTX_CHECK_EQ (good.aspectHeight, 9);
	const DesiredOutput exact = ParseDesiredOutput (Json::Parse (R"({"kind":"exact","width":1920,"height":1080})"));
	RTX_CHECK (exact.known);
	RTX_CHECK_EQ (exact.aspectWidth, 16);
}
