// Selbsttest des GLB-Schreibers ohne Archicad (RTX-A-010, #256).
//
//   clang++ -std=c++17 -O1 GlbWriter.cpp glb_selftest.cpp -o glb_selftest && ./glb_selftest wuerfel.glb
//
// Schreibt einen Würfel von 1 × 2 × 3 m (Archicad x, y, z) mit seiner
// Unterkante auf z = 0 und eine Kamera. Die Datei geht danach durch den
// Khronos-Validator und `inspectGlb` (siehe README).
#include <cmath>
#include <cstdio>
#include <fstream>

#include "GlbWriter.hpp"

using namespace rtxspike;

int main (int argc, char** argv)
{
	const char* path = argc > 1 ? argv[1] : "wuerfel.glb";

	// Archicad (x, y, z) → glTF (x, z, −y).
	auto put = [] (GlbPrimitive& prim, double x, double y, double z, double nx, double ny, double nz) {
		prim.positions.insert (prim.positions.end (), {float (x), float (z), float (-y)});
		prim.normals.insert (prim.normals.end (), {float (nx), float (nz), float (-ny)});
	};
	const double sx = 1, sy = 2, sz = 3;
	GlbPrimitive prim;
	struct Face { double n[3]; double c[4][3]; };
	const Face faces[6] = {
		{{0, 0, -1}, {{0, 0, 0}, {0, sy, 0}, {sx, sy, 0}, {sx, 0, 0}}},
		{{0, 0, 1}, {{0, 0, sz}, {sx, 0, sz}, {sx, sy, sz}, {0, sy, sz}}},
		{{0, -1, 0}, {{0, 0, 0}, {sx, 0, 0}, {sx, 0, sz}, {0, 0, sz}}},
		{{0, 1, 0}, {{0, sy, 0}, {0, sy, sz}, {sx, sy, sz}, {sx, sy, 0}}},
		{{-1, 0, 0}, {{0, 0, 0}, {0, 0, sz}, {0, sy, sz}, {0, sy, 0}}},
		{{1, 0, 0}, {{sx, 0, 0}, {sx, sy, 0}, {sx, sy, sz}, {sx, 0, sz}}},
	};
	for (const Face& f : faces) {
		const std::uint32_t base = static_cast<std::uint32_t> (prim.positions.size () / 3);
		for (const auto& c : f.c) put (prim, c[0], c[1], c[2], f.n[0], f.n[1], f.n[2]);
		prim.indices.insert (prim.indices.end (), {base, base + 1, base + 2, base, base + 2, base + 3});
	}

	GlbScene scene;
	scene.materials.push_back ({"Grau", {0.5f, 0.5f, 0.5f, 1.0f}, false});
	scene.meshes.push_back ({"selftest-cube", {prim}});

	GlbCamera cam;
	cam.name = "Testkamera";
	cam.yfov = 0.7;
	cam.aspectRatio = 1.5;
	cam.translation[0] = 0.5;
	cam.translation[1] = 1.5;
	cam.translation[2] = 10;
	cam.extrasJson = "{\"rendertaxi\":{\"camera\":{\"shift\":{\"x\":0,\"y\":0.1}}}}";
	scene.cameras.push_back (cam);

	GlbStats stats;
	const auto bytes = WriteGlb (scene, &stats);
	std::ofstream out (path, std::ios::binary);
	out.write (reinterpret_cast<const char*> (bytes.data ()), static_cast<std::streamsize> (bytes.size ()));
	std::printf ("%s: %zu Bytes, %llu Dreiecke, Bounding Box [%g %g %g] – [%g %g %g]\n", path, bytes.size (),
				 static_cast<unsigned long long> (stats.triangles), stats.boundsMin[0], stats.boundsMin[1],
				 stats.boundsMin[2], stats.boundsMax[0], stats.boundsMax[1], stats.boundsMax[2]);
	return out ? 0 : 1;
}
