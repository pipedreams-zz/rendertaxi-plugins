// Prototyp eines GLB-Schreibers für den Archicad-Modellweg (RTX-A-010, #256).
//
// **Messauftrag, nicht im veröffentlichten Add-on.** Diese Datei wird nur mit
// `-DRTX_SPIKE_MODEL_GLB=ON` übersetzt (siehe `spike/model-glb/README.md`).
//
// DevKit-frei: Der Schreiber kennt nur Dreiecke im glTF-Raum (Meter,
// rechtshändig, +Y oben), Materialien und Kameras. Die Abbildung aus Archicad
// (Z oben) geschieht vorher, in `ModelGlbSpike.cpp`. So lässt sich der
// Schreiber ohne Archicad gegen den Khronos-Validator prüfen
// (`glb_selftest.cpp`).
//
// Aufbau der Datei: ein Puffer im BIN-Chunk, drei BufferViews (Positionen,
// Normalen, Indizes) und je Primitiv drei Accessoren in diese Views. Damit
// bleibt die Zahl der BufferViews unabhängig von der Modellgröße — die
// Plattform begrenzt BufferViews, Accessoren, Knoten und Meshes
// (`packages/modules/assets/src/domain/glb.ts`).
#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace rtxspike {

struct GlbMaterial {
	std::string name;
	/** Basisfarbe linear, RGBA in [0, 1]. */
	float rgba[4] = {0.8f, 0.8f, 0.8f, 1.0f};
	/** `alphaMode: BLEND`, sonst `OPAQUE`. */
	bool blend = false;
};

struct GlbPrimitive {
	std::uint32_t material = 0;
	/** x, y, z je Ecke, im glTF-Raum. */
	std::vector<float> positions;
	/** Einheitsvektoren, gleich viele wie Positionen. */
	std::vector<float> normals;
	/** Drei je Dreieck. */
	std::vector<std::uint32_t> indices;
};

struct GlbMesh {
	/** Knotenname, z. B. die Element-GUID; leer lässt den Namen weg. */
	std::string nodeName;
	std::vector<GlbPrimitive> primitives;
};

struct GlbCamera {
	std::string name;
	bool perspective = true;
	/** Perspektive: vertikaler Öffnungswinkel in Radiant und Seitenverhältnis Breite / Höhe. */
	double yfov = 0.8;
	double aspectRatio = 0.0;
	/** Parallel: halbe Breite und halbe Höhe in Metern. */
	double xmag = 1.0;
	double ymag = 1.0;
	double znear = 0.1;
	double zfar = 0.0;
	/** Lage des Kameraknotens im glTF-Raum; die Kamera blickt entlang −Z ihres Knotens. */
	double translation[3] = {0, 0, 0};
	/** Quaternion x, y, z, w. */
	double rotation[4] = {0, 0, 0, 1};
	/** Fertiges JSON-Objekt für `cameras[i].extras` oder leer. */
	std::string extrasJson;
	/** Fertiges JSON-Objekt für `nodes[i].extras` oder leer (Herkunft der Kamera). */
	std::string nodeExtrasJson;
};

struct GlbScene {
	std::string generator = "rendertaxi Archicad model spike";
	std::vector<GlbMaterial> materials;
	std::vector<GlbMesh> meshes;
	std::vector<GlbCamera> cameras;
};

struct GlbStats {
	std::uint64_t triangles = 0;
	std::uint64_t vertices = 0;
	std::uint64_t jsonBytes = 0;
	std::uint64_t binBytes = 0;
	float boundsMin[3] = {0, 0, 0};
	float boundsMax[3] = {0, 0, 0};
};

/** Schreibt die Szene als GLB (glTF 2.0, binär). Leere Primitive fallen weg. */
std::vector<std::uint8_t> WriteGlb (const GlbScene& scene, GlbStats* stats = nullptr);

/** JSON-Zeichenkette mit Anführungszeichen und Escapes. */
std::string JsonString (const std::string& text);

/** Zahl ohne Exponentenschreibweise bis 1e-9, wie JSON sie annimmt. */
std::string JsonNumber (double value);

} // namespace rtxspike
