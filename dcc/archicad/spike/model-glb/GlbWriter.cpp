#include "GlbWriter.hpp"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <limits>

namespace rtxspike {
namespace {

constexpr std::uint32_t kGlbMagic = 0x46546C67;  // "glTF"
constexpr std::uint32_t kChunkJson = 0x4E4F534A; // "JSON"
constexpr std::uint32_t kChunkBin = 0x004E4942;  // "BIN\0"

void PutU32 (std::vector<std::uint8_t>& out, std::uint32_t value)
{
	for (int i = 0; i < 4; ++i) out.push_back (static_cast<std::uint8_t> ((value >> (8 * i)) & 0xFF));
}

template <typename T>
void Append (std::vector<std::uint8_t>& out, const std::vector<T>& values)
{
	const std::size_t start = out.size ();
	out.resize (start + values.size () * sizeof (T));
	if (!values.empty ()) std::memcpy (out.data () + start, values.data (), values.size () * sizeof (T));
}

void Pad (std::vector<std::uint8_t>& out, std::uint8_t fill)
{
	while (out.size () % 4 != 0) out.push_back (fill);
}

} // namespace

std::string JsonString (const std::string& text)
{
	std::string out = "\"";
	for (const char c : text) {
		const unsigned char u = static_cast<unsigned char> (c);
		switch (c) {
			case '"': out += "\\\""; break;
			case '\\': out += "\\\\"; break;
			case '\n': out += "\\n"; break;
			case '\r': out += "\\r"; break;
			case '\t': out += "\\t"; break;
			default:
				if (u < 0x20) {
					char buffer[8];
					std::snprintf (buffer, sizeof (buffer), "\\u%04x", u);
					out += buffer;
				} else {
					out += c;
				}
		}
	}
	return out + "\"";
}

std::string JsonNumber (double value)
{
	if (!std::isfinite (value)) return "0";
	char buffer[40];
	std::snprintf (buffer, sizeof (buffer), "%.9g", value);
	return buffer;
}

std::vector<std::uint8_t> WriteGlb (const GlbScene& scene, GlbStats* stats)
{
	// --- Binärteil: alle Positionen, dann alle Normalen, dann alle Indizes ---
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
	std::vector<Placed> placed;
	std::size_t vertexTotal = 0;
	std::size_t indexTotal = 0;
	for (std::size_t m = 0; m < scene.meshes.size (); ++m) {
		const GlbMesh& mesh = scene.meshes[m];
		for (std::size_t p = 0; p < mesh.primitives.size (); ++p) {
			const GlbPrimitive& prim = mesh.primitives[p];
			if (prim.indices.empty () || prim.positions.empty ()) continue;
			Placed entry {m, p, vertexTotal * 12, indexTotal * 4, prim.positions.size () / 3, prim.indices.size (), {}, {}};
			for (int k = 0; k < 3; ++k) {
				entry.min[k] = std::numeric_limits<float>::max ();
				entry.max[k] = std::numeric_limits<float>::lowest ();
			}
			for (std::size_t v = 0; v < entry.vertexCount; ++v)
				for (int k = 0; k < 3; ++k) {
					entry.min[k] = std::min (entry.min[k], prim.positions[v * 3 + k]);
					entry.max[k] = std::max (entry.max[k], prim.positions[v * 3 + k]);
				}
			vertexTotal += entry.vertexCount;
			indexTotal += entry.indexCount;
			placed.push_back (entry);
		}
	}

	std::vector<std::uint8_t> bin;
	bin.reserve (vertexTotal * 24 + indexTotal * 4);
	for (const Placed& entry : placed) Append (bin, scene.meshes[entry.mesh].primitives[entry.primitive].positions);
	const std::size_t normalsStart = bin.size ();
	for (const Placed& entry : placed) Append (bin, scene.meshes[entry.mesh].primitives[entry.primitive].normals);
	const std::size_t indicesStart = bin.size ();
	for (const Placed& entry : placed) Append (bin, scene.meshes[entry.mesh].primitives[entry.primitive].indices);
	const std::size_t binLength = bin.size ();
	Pad (bin, 0);

	// --- JSON ---------------------------------------------------------------
	std::string j;
	j.reserve (256 + placed.size () * 400);
	j += "{\"asset\":{\"version\":\"2.0\",\"generator\":" + JsonString (scene.generator) + "}";
	j += ",\"scene\":0";

	// Knoten: erst die Meshes (nur die mit Inhalt), dann die Kameras.
	std::vector<std::size_t> meshIndexOf (scene.meshes.size (), static_cast<std::size_t> (-1));
	std::size_t meshCount = 0;
	for (const Placed& entry : placed)
		if (meshIndexOf[entry.mesh] == static_cast<std::size_t> (-1)) meshIndexOf[entry.mesh] = meshCount++;

	std::string nodes;
	std::string sceneNodes;
	std::size_t nodeCount = 0;
	for (std::size_t m = 0; m < scene.meshes.size (); ++m) {
		if (meshIndexOf[m] == static_cast<std::size_t> (-1)) continue;
		if (nodeCount > 0) nodes += ",";
		nodes += "{\"mesh\":" + std::to_string (meshIndexOf[m]);
		if (!scene.meshes[m].nodeName.empty ()) nodes += ",\"name\":" + JsonString (scene.meshes[m].nodeName);
		nodes += "}";
		sceneNodes += (nodeCount > 0 ? "," : "") + std::to_string (nodeCount);
		++nodeCount;
	}
	for (std::size_t c = 0; c < scene.cameras.size (); ++c) {
		const GlbCamera& cam = scene.cameras[c];
		if (nodeCount > 0) nodes += ",";
		nodes += "{\"camera\":" + std::to_string (c) + ",\"name\":" + JsonString (cam.name);
		nodes += ",\"translation\":[" + JsonNumber (cam.translation[0]) + "," + JsonNumber (cam.translation[1]) + "," +
				 JsonNumber (cam.translation[2]) + "]";
		nodes += ",\"rotation\":[" + JsonNumber (cam.rotation[0]) + "," + JsonNumber (cam.rotation[1]) + "," +
				 JsonNumber (cam.rotation[2]) + "," + JsonNumber (cam.rotation[3]) + "]";
		if (!cam.nodeExtrasJson.empty ()) nodes += ",\"extras\":" + cam.nodeExtrasJson;
		nodes += "}";
		sceneNodes += (nodeCount > 0 ? "," : "") + std::to_string (nodeCount);
		++nodeCount;
	}
	j += ",\"scenes\":[{\"nodes\":[" + sceneNodes + "]}]";
	if (nodeCount > 0) j += ",\"nodes\":[" + nodes + "]";

	// Meshes und Accessoren.
	std::string meshes;
	std::string accessors;
	std::size_t accessorCount = 0;
	std::size_t currentMesh = static_cast<std::size_t> (-1);
	for (const Placed& entry : placed) {
		const GlbPrimitive& prim = scene.meshes[entry.mesh].primitives[entry.primitive];
		if (entry.mesh != currentMesh) {
			if (currentMesh != static_cast<std::size_t> (-1)) meshes += "]},";
			meshes += "{\"primitives\":[";
			currentMesh = entry.mesh;
		} else {
			meshes += ",";
		}
		const std::size_t pos = accessorCount++;
		const std::size_t nor = accessorCount++;
		const std::size_t idx = accessorCount++;
		meshes += "{\"attributes\":{\"POSITION\":" + std::to_string (pos) + ",\"NORMAL\":" + std::to_string (nor) +
				  "},\"indices\":" + std::to_string (idx) + ",\"material\":" + std::to_string (prim.material) + "}";
		if (!accessors.empty ()) accessors += ",";
		accessors += "{\"bufferView\":0,\"byteOffset\":" + std::to_string (entry.positionOffset) +
					 ",\"componentType\":5126,\"count\":" + std::to_string (entry.vertexCount) + ",\"type\":\"VEC3\",\"min\":[" +
					 JsonNumber (entry.min[0]) + "," + JsonNumber (entry.min[1]) + "," + JsonNumber (entry.min[2]) +
					 "],\"max\":[" + JsonNumber (entry.max[0]) + "," + JsonNumber (entry.max[1]) + "," +
					 JsonNumber (entry.max[2]) + "]}";
		accessors += ",{\"bufferView\":1,\"byteOffset\":" + std::to_string (entry.positionOffset) +
					 ",\"componentType\":5126,\"count\":" + std::to_string (entry.vertexCount) + ",\"type\":\"VEC3\"}";
		accessors += ",{\"bufferView\":2,\"byteOffset\":" + std::to_string (entry.indexOffset) +
					 ",\"componentType\":5125,\"count\":" + std::to_string (entry.indexCount) + ",\"type\":\"SCALAR\"}";
	}
	if (currentMesh != static_cast<std::size_t> (-1)) meshes += "]}";
	if (!meshes.empty ()) j += ",\"meshes\":[" + meshes + "]";
	if (!accessors.empty ()) j += ",\"accessors\":[" + accessors + "]";

	if (!scene.materials.empty ()) {
		j += ",\"materials\":[";
		for (std::size_t i = 0; i < scene.materials.size (); ++i) {
			const GlbMaterial& mat = scene.materials[i];
			if (i > 0) j += ",";
			j += "{\"name\":" + JsonString (mat.name) + ",\"pbrMetallicRoughness\":{\"baseColorFactor\":[" +
				 JsonNumber (mat.rgba[0]) + "," + JsonNumber (mat.rgba[1]) + "," + JsonNumber (mat.rgba[2]) + "," +
				 JsonNumber (mat.rgba[3]) + "],\"metallicFactor\":0,\"roughnessFactor\":0.8}";
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
			if (cam.perspective) {
				j += "{\"type\":\"perspective\",\"perspective\":{\"yfov\":" + JsonNumber (cam.yfov) +
					 ",\"znear\":" + JsonNumber (cam.znear);
				if (cam.aspectRatio > 0) j += ",\"aspectRatio\":" + JsonNumber (cam.aspectRatio);
				if (cam.zfar > cam.znear) j += ",\"zfar\":" + JsonNumber (cam.zfar);
				j += "}";
			} else {
				const double zfar = cam.zfar > cam.znear ? cam.zfar : cam.znear + 10000.0;
				j += "{\"type\":\"orthographic\",\"orthographic\":{\"xmag\":" + JsonNumber (cam.xmag) +
					 ",\"ymag\":" + JsonNumber (cam.ymag) + ",\"znear\":" + JsonNumber (cam.znear) +
					 ",\"zfar\":" + JsonNumber (zfar) + "}";
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
		stats->jsonBytes = json.size ();
		stats->binBytes = bin.size ();
		bool first = true;
		for (const Placed& entry : placed)
			for (int k = 0; k < 3; ++k) {
				stats->boundsMin[k] = first ? entry.min[k] : std::min (stats->boundsMin[k], entry.min[k]);
				stats->boundsMax[k] = first ? entry.max[k] : std::max (stats->boundsMax[k], entry.max[k]);
				if (k == 2) first = false;
			}
	}
	return out;
}

} // namespace rtxspike
