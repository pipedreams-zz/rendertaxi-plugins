#include "rtx/Glb.hpp"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <limits>

#include "rtx/Json.hpp"

namespace rtx {
namespace {

constexpr std::uint32_t kGlbMagic = 0x46546C67;  // "glTF"
constexpr std::uint32_t kChunkJson = 0x4E4F534A; // "JSON"
constexpr std::uint32_t kChunkBin = 0x004E4942;  // "BIN\0"
constexpr std::size_t kNone = static_cast<std::size_t> (-1);

void PutU32 (std::vector<std::uint8_t>& out, std::uint32_t value)
{
	for (int i = 0; i < 4; ++i) out.push_back (static_cast<std::uint8_t> ((value >> (8 * i)) & 0xFF));
}

template <typename T>
void Append (std::vector<std::uint8_t>& out, const T* values, std::size_t count)
{
	const std::size_t start = out.size ();
	out.resize (start + count * sizeof (T));
	if (count > 0) std::memcpy (out.data () + start, values, count * sizeof (T));
}

void Pad (std::vector<std::uint8_t>& out, std::uint8_t fill)
{
	while (out.size () % 4 != 0) out.push_back (fill);
}

/** Zahl für den JSON-Teil: `%.9g` gibt jedes float32 umkehrbar wieder; nie `nan` oder `inf`. */
std::string Number (double value)
{
	if (!std::isfinite (value)) return "0";
	if (value == 0.0) return "0";
	char buffer[40];
	std::snprintf (buffer, sizeof (buffer), "%.9g", value);
	return buffer;
}

std::string Megabytes (double bytes)
{
	char buffer[32];
	std::snprintf (buffer, sizeof (buffer), "%.1f", bytes / 1048576.0);
	return buffer;
}

std::string Thousands (std::uint64_t value)
{
	// Tausenderpunkt wie in den Meldungen der Palette: 334.394.
	const std::string digits = std::to_string (value);
	std::string out;
	for (std::size_t i = 0; i < digits.size (); ++i) {
		if (i > 0 && (digits.size () - i) % 3 == 0) out += '.';
		out += digits[i];
	}
	return out;
}

GlbSceneBuilder::Vec3 Sub (GlbSceneBuilder::Vec3 a, GlbSceneBuilder::Vec3 b)
{
	return {a.x - b.x, a.y - b.y, a.z - b.z};
}
GlbSceneBuilder::Vec3 Cross (GlbSceneBuilder::Vec3 a, GlbSceneBuilder::Vec3 b)
{
	return {a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x};
}
double Dot (GlbSceneBuilder::Vec3 a, GlbSceneBuilder::Vec3 b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
GlbSceneBuilder::Vec3 Norm (GlbSceneBuilder::Vec3 a)
{
	const double l = std::sqrt (Dot (a, a));
	if (!(l > 1e-12)) return {};
	return {a.x / l, a.y / l, a.z / l};
}

} // namespace

float SrgbToLinear (double c)
{
	c = std::min (1.0, std::max (0.0, c));
	if (c <= 0.04045) return static_cast<float> (c / 12.92);
	return static_cast<float> (std::pow ((c + 0.055) / 1.055, 2.4));
}

GlbCounts CountGlb (const GlbScene& scene)
{
	GlbCounts counts;
	for (const GlbMesh& mesh : scene.meshes) {
		bool used = false;
		for (const GlbPrimitive& prim : mesh.primitives) {
			if (prim.indices.empty () || prim.positions.empty ()) continue;
			used = true;
			++counts.primitives;
			counts.triangles += prim.Triangles ();
			counts.vertices += prim.positions.size () / 3;
		}
		if (used) ++counts.meshes;
	}
	counts.accessors = counts.primitives * 3;
	// Mesh-Knoten, der Wurzelknoten beim Versatz ist hier nicht gezählt (höchstens einer), dann die Kameras.
	counts.nodes = counts.meshes + scene.cameras.size ();
	return counts;
}

std::vector<std::uint8_t> WriteGlb (const GlbScene& scene, GlbStats* stats)
{
	struct Placed {
		std::size_t mesh;
		std::size_t primitive;
		std::size_t positionOffset;
		std::size_t indexOffset;
		std::size_t vertexCount;
		std::size_t indexCount;
		float min[3];
		float max[3];
	};

	// --- Bounding Box doppelt genau, Versatz bei großen Koordinaten (QA-02) -----
	double lo[3] = {std::numeric_limits<double>::max (), std::numeric_limits<double>::max (),
					std::numeric_limits<double>::max ()};
	double hi[3] = {std::numeric_limits<double>::lowest (), std::numeric_limits<double>::lowest (),
					std::numeric_limits<double>::lowest ()};
	bool any = false;
	for (const GlbMesh& mesh : scene.meshes)
		for (const GlbPrimitive& prim : mesh.primitives) {
			if (prim.indices.empty ()) continue;
			for (std::size_t v = 0; v + 2 < prim.positions.size (); v += 3)
				for (int k = 0; k < 3; ++k) {
					lo[k] = std::min (lo[k], prim.positions[v + k]);
					hi[k] = std::max (hi[k], prim.positions[v + k]);
					any = true;
				}
		}
	double offset[3] = {0, 0, 0};
	if (any) {
		double reach = 0;
		for (int k = 0; k < 3; ++k) reach = std::max ({reach, std::fabs (lo[k]), std::fabs (hi[k])});
		if (reach > kGlbRecenterDistance)
			for (int k = 0; k < 3; ++k) offset[k] = std::round ((lo[k] + hi[k]) / 2.0);
	}
	const bool recentred = offset[0] != 0 || offset[1] != 0 || offset[2] != 0;

	// --- Binärteil: alle Positionen, dann alle Normalen, dann alle Indizes ------
	std::vector<Placed> placed;
	std::size_t vertexTotal = 0;
	std::size_t indexTotal = 0;
	for (std::size_t m = 0; m < scene.meshes.size (); ++m) {
		const GlbMesh& mesh = scene.meshes[m];
		for (std::size_t p = 0; p < mesh.primitives.size (); ++p) {
			const GlbPrimitive& prim = mesh.primitives[p];
			if (prim.indices.empty () || prim.positions.empty ()) continue;
			Placed entry {m, p, vertexTotal * 12, indexTotal * 4, prim.positions.size () / 3, prim.indices.size (), {}, {}};
			vertexTotal += entry.vertexCount;
			indexTotal += entry.indexCount;
			placed.push_back (entry);
		}
	}

	std::vector<std::uint8_t> bin;
	bin.reserve (vertexTotal * 24 + indexTotal * 4);
	std::vector<float> scratch;
	for (Placed& entry : placed) {
		const GlbPrimitive& prim = scene.meshes[entry.mesh].primitives[entry.primitive];
		scratch.resize (entry.vertexCount * 3);
		for (int k = 0; k < 3; ++k) {
			entry.min[k] = std::numeric_limits<float>::max ();
			entry.max[k] = std::numeric_limits<float>::lowest ();
		}
		for (std::size_t v = 0; v < entry.vertexCount; ++v)
			for (int k = 0; k < 3; ++k) {
				const float value = static_cast<float> (prim.positions[v * 3 + k] - offset[k]);
				scratch[v * 3 + k] = value;
				entry.min[k] = std::min (entry.min[k], value);
				entry.max[k] = std::max (entry.max[k], value);
			}
		Append (bin, scratch.data (), scratch.size ());
	}
	const std::size_t normalsStart = bin.size ();
	for (const Placed& entry : placed) {
		const GlbPrimitive& prim = scene.meshes[entry.mesh].primitives[entry.primitive];
		// Fehlende Normalen schreiben eine Null nicht: die Lücke füllt +Y, damit die Accessoren gleich lang bleiben.
		scratch.assign (entry.vertexCount * 3, 0.0f);
		for (std::size_t v = 0; v < entry.vertexCount; ++v) {
			if (v * 3 + 2 < prim.normals.size ()) {
				for (int k = 0; k < 3; ++k) scratch[v * 3 + k] = prim.normals[v * 3 + k];
			} else {
				scratch[v * 3 + 1] = 1.0f;
			}
		}
		Append (bin, scratch.data (), scratch.size ());
	}
	const std::size_t indicesStart = bin.size ();
	for (const Placed& entry : placed) {
		const GlbPrimitive& prim = scene.meshes[entry.mesh].primitives[entry.primitive];
		Append (bin, prim.indices.data (), prim.indices.size ());
	}
	const std::size_t binLength = bin.size ();
	Pad (bin, 0);

	// --- JSON -----------------------------------------------------------------
	std::string j;
	j.reserve (256 + placed.size () * 420);
	j += "{\"asset\":{\"version\":\"2.0\"";
	if (!scene.generator.empty ()) j += ",\"generator\":" + JsonQuote (scene.generator);
	j += "},\"scene\":0";

	// Knoten: die Meshes (nur die mit Inhalt), beim Versatz unter einem Wurzelknoten, dann die Kameras.
	std::vector<std::size_t> meshIndexOf (scene.meshes.size (), kNone);
	std::size_t meshCount = 0;
	for (const Placed& entry : placed)
		if (meshIndexOf[entry.mesh] == kNone) meshIndexOf[entry.mesh] = meshCount++;

	std::string nodes;
	std::string sceneNodes;
	std::string children;
	std::size_t nodeCount = 0;
	auto addNode = [&] (const std::string& node) {
		if (nodeCount > 0) nodes += ",";
		nodes += node;
		return nodeCount++;
	};
	if (recentred && meshCount > 0) {
		const std::size_t root = addNode ("");  // Platzhalter, unten ersetzt
		sceneNodes += std::to_string (root);
	}
	for (std::size_t m = 0; m < scene.meshes.size (); ++m) {
		if (meshIndexOf[m] == kNone) continue;
		std::string node = "{\"mesh\":" + std::to_string (meshIndexOf[m]);
		if (!scene.meshes[m].nodeName.empty ()) node += ",\"name\":" + JsonQuote (scene.meshes[m].nodeName);
		node += "}";
		const std::size_t index = addNode (node);
		if (recentred) {
			children += (children.empty () ? "" : ",") + std::to_string (index);
		} else {
			sceneNodes += (sceneNodes.empty () ? "" : ",") + std::to_string (index);
		}
	}
	if (recentred && meshCount > 0) {
		const std::string root = "{\"name\":\"rendertaxi:origin\",\"translation\":[" + Number (offset[0]) + "," +
								 Number (offset[1]) + "," + Number (offset[2]) + "],\"children\":[" + children + "]}";
		// Der Platzhalter ist der erste Knoten und steht am Anfang von `nodes`.
		nodes.insert (0, root);
	}
	for (std::size_t c = 0; c < scene.cameras.size (); ++c) {
		const GlbCamera& cam = scene.cameras[c];
		std::string node = "{\"camera\":" + std::to_string (c) + ",\"name\":" + JsonQuote (cam.name);
		node += ",\"translation\":[" + Number (cam.translation[0]) + "," + Number (cam.translation[1]) + "," +
				Number (cam.translation[2]) + "]";
		node += ",\"rotation\":[" + Number (cam.rotation[0]) + "," + Number (cam.rotation[1]) + "," +
				Number (cam.rotation[2]) + "," + Number (cam.rotation[3]) + "]";
		if (!cam.nodeExtrasJson.empty ()) node += ",\"extras\":" + cam.nodeExtrasJson;
		node += "}";
		const std::size_t index = addNode (node);
		sceneNodes += (sceneNodes.empty () ? "" : ",") + std::to_string (index);
	}
	j += ",\"scenes\":[{\"nodes\":[" + sceneNodes + "]}]";
	if (nodeCount > 0) j += ",\"nodes\":[" + nodes + "]";

	// Meshes und Accessoren.
	std::string meshes;
	std::string accessors;
	std::size_t accessorCount = 0;
	std::size_t currentMesh = kNone;
	for (const Placed& entry : placed) {
		const GlbPrimitive& prim = scene.meshes[entry.mesh].primitives[entry.primitive];
		if (entry.mesh != currentMesh) {
			if (currentMesh != kNone) meshes += "]},";
			meshes += "{\"primitives\":[";
			currentMesh = entry.mesh;
		} else {
			meshes += ",";
		}
		const std::size_t pos = accessorCount++;
		const std::size_t nor = accessorCount++;
		const std::size_t idx = accessorCount++;
		meshes += "{\"attributes\":{\"POSITION\":" + std::to_string (pos) + ",\"NORMAL\":" + std::to_string (nor) +
				  "},\"indices\":" + std::to_string (idx);
		if (prim.material < scene.materials.size ()) meshes += ",\"material\":" + std::to_string (prim.material);
		meshes += "}";
		if (!accessors.empty ()) accessors += ",";
		accessors += "{\"bufferView\":0,\"byteOffset\":" + std::to_string (entry.positionOffset) +
					 ",\"componentType\":5126,\"count\":" + std::to_string (entry.vertexCount) + ",\"type\":\"VEC3\",\"min\":[" +
					 Number (entry.min[0]) + "," + Number (entry.min[1]) + "," + Number (entry.min[2]) + "],\"max\":[" +
					 Number (entry.max[0]) + "," + Number (entry.max[1]) + "," + Number (entry.max[2]) + "]}";
		accessors += ",{\"bufferView\":1,\"byteOffset\":" + std::to_string (entry.positionOffset) +
					 ",\"componentType\":5126,\"count\":" + std::to_string (entry.vertexCount) + ",\"type\":\"VEC3\"}";
		accessors += ",{\"bufferView\":2,\"byteOffset\":" + std::to_string (entry.indexOffset) +
					 ",\"componentType\":5125,\"count\":" + std::to_string (entry.indexCount) + ",\"type\":\"SCALAR\"}";
	}
	if (currentMesh != kNone) meshes += "]}";
	if (!meshes.empty ()) j += ",\"meshes\":[" + meshes + "]";
	if (!accessors.empty ()) j += ",\"accessors\":[" + accessors + "]";

	if (!scene.materials.empty ()) {
		j += ",\"materials\":[";
		for (std::size_t i = 0; i < scene.materials.size (); ++i) {
			const GlbMaterial& mat = scene.materials[i];
			if (i > 0) j += ",";
			j += "{\"name\":" + JsonQuote (mat.name) + ",\"pbrMetallicRoughness\":{\"baseColorFactor\":[" +
				 Number (mat.rgba[0]) + "," + Number (mat.rgba[1]) + "," + Number (mat.rgba[2]) + "," +
				 Number (mat.rgba[3]) + "],\"metallicFactor\":0,\"roughnessFactor\":0.8}";
			if (mat.blend) j += ",\"alphaMode\":\"BLEND\"";
			j += ",\"doubleSided\":true}";
		}
		j += "]";
	}

	if (!scene.cameras.empty ()) {
		j += ",\"cameras\":[";
		for (std::size_t c = 0; c < scene.cameras.size (); ++c) {
			const GlbCamera& cam = scene.cameras[c];
			if (c > 0) j += ",";
			j += "{\"name\":" + JsonQuote (cam.name);
			if (cam.perspective) {
				j += ",\"type\":\"perspective\",\"perspective\":{\"yfov\":" + Number (cam.yfov) +
					 ",\"znear\":" + Number (cam.znear);
				if (cam.aspectRatio > 0) j += ",\"aspectRatio\":" + Number (cam.aspectRatio);
				if (cam.zfar > cam.znear) j += ",\"zfar\":" + Number (cam.zfar);
				j += "}";
			} else {
				const double zfar = cam.zfar > cam.znear ? cam.zfar : cam.znear + 10000.0;
				j += ",\"type\":\"orthographic\",\"orthographic\":{\"xmag\":" + Number (cam.xmag) +
					 ",\"ymag\":" + Number (cam.ymag) + ",\"znear\":" + Number (cam.znear) +
					 ",\"zfar\":" + Number (zfar) + "}";
			}
			if (!cam.extrasJson.empty ()) j += ",\"extras\":" + cam.extrasJson;
			j += "}";
		}
		j += "]";
	}

	if (binLength > 0) {
		j += ",\"buffers\":[{\"byteLength\":" + std::to_string (binLength) + "}]";
		j += ",\"bufferViews\":[";
		j += "{\"buffer\":0,\"byteOffset\":0,\"byteLength\":" + std::to_string (normalsStart) +
			 ",\"byteStride\":12,\"target\":34962}";
		j += ",{\"buffer\":0,\"byteOffset\":" + std::to_string (normalsStart) +
			 ",\"byteLength\":" + std::to_string (indicesStart - normalsStart) + ",\"byteStride\":12,\"target\":34962}";
		j += ",{\"buffer\":0,\"byteOffset\":" + std::to_string (indicesStart) +
			 ",\"byteLength\":" + std::to_string (binLength - indicesStart) + ",\"target\":34963}";
		j += "]";
	}
	j += "}";

	std::vector<std::uint8_t> json (j.begin (), j.end ());
	Pad (json, ' ');

	std::vector<std::uint8_t> out;
	const std::size_t total = 12 + 8 + json.size () + (binLength > 0 ? 8 + bin.size () : 0);
	out.reserve (total);
	PutU32 (out, kGlbMagic);
	PutU32 (out, 2);
	PutU32 (out, static_cast<std::uint32_t> (total));
	PutU32 (out, static_cast<std::uint32_t> (json.size ()));
	PutU32 (out, kChunkJson);
	out.insert (out.end (), json.begin (), json.end ());
	if (binLength > 0) {
		PutU32 (out, static_cast<std::uint32_t> (bin.size ()));
		PutU32 (out, kChunkBin);
		out.insert (out.end (), bin.begin (), bin.end ());
	}

	if (stats != nullptr) {
		*stats = GlbStats {};
		stats->vertices = vertexTotal;
		stats->triangles = indexTotal / 3;
		stats->meshes = meshCount;
		stats->nodes = nodeCount;
		stats->jsonBytes = json.size ();
		stats->binBytes = bin.size ();
		for (int k = 0; k < 3; ++k) {
			stats->offset[k] = offset[k];
			stats->boundsMin[k] = any ? lo[k] : 0.0;
			stats->boundsMax[k] = any ? hi[k] : 0.0;
		}
	}
	return out;
}

GlbScene MergeByMaterial (const GlbScene& scene)
{
	GlbScene merged;
	merged.generator = scene.generator;
	merged.materials = scene.materials;
	merged.cameras = scene.cameras;
	GlbMesh mesh;
	std::map<std::uint32_t, std::size_t> primitiveOf;
	for (const GlbMesh& source : scene.meshes)
		for (const GlbPrimitive& prim : source.primitives) {
			if (prim.indices.empty () || prim.positions.empty ()) continue;
			auto found = primitiveOf.find (prim.material);
			if (found == primitiveOf.end ()) {
				found = primitiveOf.emplace (prim.material, mesh.primitives.size ()).first;
				mesh.primitives.push_back ({});
				mesh.primitives.back ().material = prim.material;
			}
			GlbPrimitive& target = mesh.primitives[found->second];
			const std::uint32_t base = static_cast<std::uint32_t> (target.positions.size () / 3);
			target.positions.insert (target.positions.end (), prim.positions.begin (), prim.positions.end ());
			target.normals.insert (target.normals.end (), prim.normals.begin (), prim.normals.end ());
			// Fehlende Normalen auffüllen, damit die Ecken der nächsten Quelle an ihrer Stelle bleiben.
			target.normals.resize (target.positions.size (), 0.0f);
			for (const std::uint32_t index : prim.indices) target.indices.push_back (base + index);
		}
	if (!mesh.primitives.empty ()) merged.meshes.push_back (std::move (mesh));
	return merged;
}

GlbLimitCheck CheckGlbStructure (const GlbCounts& counts, const GlbServerLimits& limits)
{
	GlbLimitCheck check;
	if (counts.triangles > limits.maxTriangles) {
		check.trianglesExceeded = true;
		check.message = "Das Modell hat " + Thousands (counts.triangles) + " Dreiecke; der Server nimmt höchstens " +
						Thousands (limits.maxTriangles) + " an. Bitte den 3D-Ausschnitt verkleinern.";
		return check;
	}
	// Ein Knoten mehr für den Wurzelknoten beim Versatz.
	if (counts.meshes > limits.maxMeshes || counts.nodes + 1 > limits.maxNodes ||
		counts.primitives > limits.maxPrimitives || counts.accessors > limits.maxAccessors) {
		check.structureExceeded = true;
		check.message = "Das Modell hat " + Thousands (counts.meshes) +
						" sichtbare Elemente; der Server nimmt höchstens " + Thousands (limits.maxMeshes) +
						" einzeln an.";
	}
	return check;
}

Status CheckGlbFile (const GlbStats& stats, std::uint64_t byteSize, std::int64_t maxGeometryBytes,
					 const GlbServerLimits& limits)
{
	if (stats.triangles > limits.maxTriangles)
		return Status::Fail (errc::LimitExceeded,
							 "Das Modell hat " + Thousands (stats.triangles) + " Dreiecke; der Server nimmt höchstens " +
								 Thousands (limits.maxTriangles) + " an. Bitte den 3D-Ausschnitt verkleinern.");
	if (stats.jsonBytes > limits.maxJsonBytes)
		return Status::Fail (errc::LimitExceeded,
							 "Die Gliederung des Modells ist zu groß für den Server. Bitte den 3D-Ausschnitt "
							 "verkleinern.");
	if (maxGeometryBytes > 0 && byteSize > static_cast<std::uint64_t> (maxGeometryBytes)) {
		const double over = static_cast<double> (byteSize) / static_cast<double> (maxGeometryBytes);
		char percent[16];
		std::snprintf (percent, sizeof (percent), "%.0f", std::ceil ((over - 1.0) * 100.0));
		return Status::Fail (errc::LimitExceeded,
							 "Das Modell ist " + Megabytes (static_cast<double> (byteSize)) +
								 " MB groß; der Server nimmt höchstens " +
								 Megabytes (static_cast<double> (maxGeometryBytes)) + " MB an (" + percent +
								 " % zu viel). Bitte den 3D-Ausschnitt verkleinern.");
	}
	return Status::Ok ();
}

Result<GlbFile> FinishGlb (const GlbScene& scene, std::int64_t maxGeometryBytes, const GlbServerLimits& limits)
{
	const GlbCounts counts = CountGlb (scene);
	if (counts.triangles == 0)
		return Result<GlbFile>::Fail (kModelEmpty, "Das 3D-Fenster zeigt keine Geometrie; es wird kein Modell gesendet.");
	const GlbLimitCheck structure = CheckGlbStructure (counts, limits);
	if (structure.trianglesExceeded) return Result<GlbFile>::Fail (errc::LimitExceeded, structure.message);

	GlbFile file;
	if (structure.structureExceeded) {
		file.bytes = WriteGlb (MergeByMaterial (scene), &file.stats);
		file.mergedByMaterial = true;
	} else {
		file.bytes = WriteGlb (scene, &file.stats);
		if (file.stats.jsonBytes > limits.maxJsonBytes) {
			file.bytes = WriteGlb (MergeByMaterial (scene), &file.stats);
			file.mergedByMaterial = true;
		}
	}
	const Status checked = CheckGlbFile (file.stats, file.bytes.size (), maxGeometryBytes, limits);
	if (!checked) return Result<GlbFile>::Fail (checked.GetError ());
	return Result<GlbFile>::Ok (std::move (file));
}

// --- Szenenaufbau ---------------------------------------------------------------

void GlbSceneBuilder::BeginElement (const std::string& guid)
{
	GlbMesh mesh;
	mesh.nodeName = guid;
	scene.meshes.push_back (std::move (mesh));
	primitiveOf.clear ();
}

std::uint32_t GlbSceneBuilder::Material (std::int64_t key, const std::string& name, double red, double green,
										 double blue, double transparency)
{
	const auto found = materialOf.find (key);
	if (found != materialOf.end ()) return found->second;
	GlbMaterial material;
	material.name = name.empty () ? "Oberfläche " + std::to_string (key) : name;
	material.rgba[0] = SrgbToLinear (red);
	material.rgba[1] = SrgbToLinear (green);
	material.rgba[2] = SrgbToLinear (blue);
	const double alpha = 1.0 - std::min (1.0, std::max (0.0, std::isfinite (transparency) ? transparency : 0.0));
	material.rgba[3] = static_cast<float> (alpha);
	material.blend = alpha < 0.999;
	const std::uint32_t index = static_cast<std::uint32_t> (scene.materials.size ());
	scene.materials.push_back (material);
	materialOf[key] = index;
	return index;
}

void GlbSceneBuilder::AddConvexPolygon (std::uint32_t material, const std::vector<Vec3>& corners,
										const std::vector<Vec3>& normals, Vec3 faceNormal)
{
	const std::size_t n = corners.size ();
	if (n < 3) return;
	if (scene.meshes.empty ()) BeginElement ("");

	GlbMesh& mesh = scene.meshes.back ();
	auto found = primitiveOf.find (material);
	if (found == primitiveOf.end ()) {
		found = primitiveOf.emplace (material, mesh.primitives.size ()).first;
		mesh.primitives.push_back ({});
		mesh.primitives.back ().material = material;
	}
	GlbPrimitive& prim = mesh.primitives[found->second];

	// Umlaufsinn gegen die Flächennormale prüfen; glTF will gegen den Uhrzeigersinn von außen.
	faceNormal = Norm (faceNormal);
	const bool faceKnown = Dot (faceNormal, faceNormal) > 0.25;
	const Vec3 geometric = Cross (Sub (corners[1], corners[0]), Sub (corners[2], corners[0]));
	const bool flip = faceKnown && Dot (geometric, faceNormal) < 0;
	if (flip) ++flipped;

	const std::uint32_t base = static_cast<std::uint32_t> (prim.positions.size () / 3);
	for (std::size_t i = 0; i < n; ++i) {
		const Vec3& p = corners[i];
		// Archicad (x, y, z) → glTF (x, z, −y).
		prim.positions.insert (prim.positions.end (), {p.x, p.z, -p.y});
		Vec3 normal = i < normals.size () ? Norm (normals[i]) : Vec3 {};
		if (Dot (normal, normal) < 0.25) {
			++zeroNormals;
			normal = faceKnown ? faceNormal : Norm (flip ? Vec3 {-geometric.x, -geometric.y, -geometric.z} : geometric);
		}
		prim.normals.insert (prim.normals.end (),
							 {static_cast<float> (normal.x), static_cast<float> (normal.z), static_cast<float> (-normal.y)});
	}
	for (std::size_t i = 1; i + 1 < n; ++i) {
		const std::uint32_t a = base + static_cast<std::uint32_t> (i);
		const std::uint32_t b = base + static_cast<std::uint32_t> (i + 1);
		if (flip)
			prim.indices.insert (prim.indices.end (), {base, b, a});
		else
			prim.indices.insert (prim.indices.end (), {base, a, b});
		++triangles;
	}
}

} // namespace rtx
