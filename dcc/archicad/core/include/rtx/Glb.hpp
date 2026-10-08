// GLB-Schreiber des Modellwegs (RTX-A-012, #307) — aus dem Prototyp des
// Messauftrags RTX-A-010 (`spike/model-glb/GlbWriter.*`, PR #305) in den
// DevKit-freien Kern übernommen.
//
// Der Kern kennt **kein** ModelerAPI. Das Add-on gibt Polygone in Archicad-
// Koordinaten an `GlbSceneBuilder`; alles Weitere geschieht hier und ist ohne
// Archicad prüfbar:
//
// - Abbildung Archicad (x, y, z) → glTF (x, z, −y): Archicad ist rechtshändig
//   mit Z oben, glTF rechtshändig mit Y oben. Eine Drehung um −90° um X
//   (Determinante +1), Meter bleiben Meter (gemessen, QA-02).
// - Umlaufsinn gegen die Flächennormale (194 von 127.501 Polygonen bei AERO
//   widersprachen, QA-01).
// - Ein Knoten je Element mit der Element-GUID als Name (QA-06); bei zu vielen
//   Meshes der gemessene Rückfall „ein Mesh je Material" (QA-04).
// - Liegt die Szene weiter als 1 km vom Ursprung, wandert ihr Mittelpunkt in
//   die `translation` eines Wurzelknotens: die Accessoren bleiben float32-genau,
//   die Datei bleibt in Metern im Projektursprung (QA-02).
//
// Aufbau der Datei: ein Puffer, drei BufferViews (Positionen, Normalen,
// Indizes), je Primitiv drei Accessoren. Die Zahl der BufferViews hängt damit
// nicht von der Modellgröße ab — die Plattform begrenzt sie
// (`packages/modules/assets/src/domain/glb.ts`, `GLB_LIMITS`).
#pragma once

#include <cstdint>
#include <map>
#include <string>
#include <vector>

#include "rtx/Result.hpp"

namespace rtx {

struct GlbMaterial {
	std::string name;
	/** Basisfarbe linear, RGBA in [0, 1]. */
	float rgba[4] = {0.8f, 0.8f, 0.8f, 1.0f};
	/** `alphaMode: BLEND`, sonst `OPAQUE`. */
	bool blend = false;
};

struct GlbPrimitive {
	std::uint32_t material = 0;
	/** x, y, z je Ecke im glTF-Raum, in Metern — doppelt genau bis zum Schreiben. */
	std::vector<double> positions;
	/** Einheitsvektoren im glTF-Raum, gleich viele wie Positionen. */
	std::vector<float> normals;
	/** Drei je Dreieck. */
	std::vector<std::uint32_t> indices;

	std::uint64_t Triangles () const { return indices.size () / 3; }
};

struct GlbMesh {
	/** Knotenname, im Modellweg die Element-GUID; leer lässt den Namen weg. */
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
	/** 0 heißt unendlich (nur Perspektive; glTF verlangt `zfar` bei der Parallelkamera). */
	double zfar = 0.0;
	/** Lage des Kameraknotens im glTF-Raum; die Kamera blickt entlang −Z ihres Knotens. */
	double translation[3] = {0, 0, 0};
	/** Quaternion x, y, z, w. */
	double rotation[4] = {0, 0, 0, 1};
	/** Fertiges JSON-Objekt für `cameras[i].extras` oder leer (`gltf-camera-extras.schema.json`). */
	std::string extrasJson;
	/** Fertiges JSON-Objekt für `nodes[i].extras` oder leer (Herkunft der Kamera). */
	std::string nodeExtrasJson;
};

struct GlbScene {
	std::string generator;
	std::vector<GlbMaterial> materials;
	std::vector<GlbMesh> meshes;
	std::vector<GlbCamera> cameras;
};

/** Die Grenzen des Server-Prüfers (`GLB_LIMITS` in `glb.ts`) — keine Einstellung, sie schützen den Server. */
struct GlbServerLimits {
	std::uint64_t maxJsonBytes = 16ull * 1024 * 1024;
	std::uint64_t maxBufferViews = 500000;
	std::uint64_t maxAccessors = 500000;
	std::uint64_t maxNodes = 100000;
	std::uint64_t maxMeshes = 50000;
	std::uint64_t maxPrimitives = 200000;
	std::uint64_t maxTriangles = 50000000;
};

/** Was eine Szene als Datei würde — gezählt wie der Server zählt, ohne zu schreiben. */
struct GlbCounts {
	std::uint64_t meshes = 0;
	std::uint64_t nodes = 0;
	std::uint64_t primitives = 0;
	std::uint64_t accessors = 0;
	std::uint64_t triangles = 0;
	std::uint64_t vertices = 0;
};

GlbCounts CountGlb (const GlbScene& scene);

struct GlbStats {
	std::uint64_t triangles = 0;
	std::uint64_t vertices = 0;
	std::uint64_t meshes = 0;
	std::uint64_t nodes = 0;
	std::uint64_t jsonBytes = 0;
	std::uint64_t binBytes = 0;
	/** Bounding Box im glTF-Raum, in Metern im Projektursprung (mit Versatz). */
	double boundsMin[3] = {0, 0, 0};
	double boundsMax[3] = {0, 0, 0};
	/** Versatz des Wurzelknotens; (0, 0, 0), wenn die Szene nah am Ursprung liegt. */
	double offset[3] = {0, 0, 0};
};

/** Ab diesem Abstand der Bounding Box vom Ursprung verschiebt der Schreiber um ihren Mittelpunkt (QA-02). */
inline constexpr double kGlbRecenterDistance = 1000.0;

/** Schreibt die Szene als GLB (glTF 2.0, binär). Leere Primitive und leere Meshes fallen weg. */
std::vector<std::uint8_t> WriteGlb (const GlbScene& scene, GlbStats* stats = nullptr);

/**
 * Der Rückfall bei der Mesh- oder Knotengrenze (QA-04): ein Mesh, ein Primitiv
 * je Material. Gemessen rund 11 % kleiner; die Element-GUIDs gehen verloren.
 */
GlbScene MergeByMaterial (const GlbScene& scene);

/** Was gegen die Grenzen spricht, als Satz für den Nutzer — leer, wenn nichts. */
struct GlbLimitCheck {
	/** Grenzen der Gliederung (Meshes, Knoten, Primitive, Accessoren) — der Rückfall hilft. */
	bool structureExceeded = false;
	/** Dreiecke — kein Rückfall hilft, der Nutzer verkleinert den 3D-Ausschnitt. */
	bool trianglesExceeded = false;
	std::string message;
};

GlbLimitCheck CheckGlbStructure (const GlbCounts& counts, const GlbServerLimits& limits);

/**
 * Prüft die geschriebene Datei gegen die Grenzen **vor** dem Senden: die Bytes
 * gegen `maxGeometryBytes` des Handshakes (0: unbekannt, keine Prüfung), den
 * JSON-Teil gegen den Server-Prüfer. Der Satz sagt, um wie viel es zu groß ist.
 */
Status CheckGlbFile (const GlbStats& stats, std::uint64_t byteSize, std::int64_t maxGeometryBytes,
					 const GlbServerLimits& limits);

/** Die fertige Modelldatei samt Kennzahlen. */
struct GlbFile {
	std::vector<std::uint8_t> bytes;
	GlbStats stats;
	/** Der Rückfall „ein Mesh je Material" hat gegriffen; die GUIDs fehlen in der Datei. */
	bool mergedByMaterial = false;
};

/**
 * Schreibt die Szene als Modelldatei und hält dabei die Grenzen **vor** dem
 * Senden ein (#307, QA-04):
 *
 * 1. Kein Dreieck → Fehler `model_empty`. Ein leeres Modell wird nie gesendet.
 * 2. Zu viele Dreiecke → Fehler mit Zahl; kein Rückfall hilft.
 * 3. Zu viele Meshes, Knoten, Primitive oder Accessoren, oder ein zu großer
 *    JSON-Teil → Rückfall „ein Mesh je Material".
 * 4. Mehr Bytes als `maxGeometryBytes` des Handshakes → Fehler, um wie viel.
 */
Result<GlbFile> FinishGlb (const GlbScene& scene, std::int64_t maxGeometryBytes,
						   const GlbServerLimits& limits = GlbServerLimits {});

/** Fehlercode für ein Modell ohne ein einziges Dreieck. */
inline constexpr const char* kModelEmpty = "model_empty";

/**
 * Baut die Szene aus Polygonen in Archicad-Koordinaten — die Arbeit des
 * Konverters im Prototyp, ohne ModelerAPI.
 */
class GlbSceneBuilder final {
public:
	struct Vec3 {
		double x = 0, y = 0, z = 0;
	};

	/** Neues Element: ein Mesh mit der GUID als Knotenname. */
	void BeginElement (const std::string& guid);

	/**
	 * Oberfläche anlegen oder wiederfinden. `key` ist der Schlüssel des Hosts
	 * (Archicad: Index des Materialattributs); Farbe als sRGB in [0, 1],
	 * Transparenz in [0, 1] wie `ModelerAPI::Material`.
	 */
	std::uint32_t Material (std::int64_t key, const std::string& name, double red, double green,
							double blue, double transparency);

	/**
	 * Ein konvexes Polygon des aktuellen Elements in Archicad-Koordinaten
	 * (Meter, Z oben), als Fächer zerlegt. `normals` je Ecke oder leer;
	 * `faceNormal` entscheidet den Umlaufsinn. Weniger als drei Ecken fallen weg.
	 */
	void AddConvexPolygon (std::uint32_t material, const std::vector<Vec3>& corners,
						   const std::vector<Vec3>& normals, Vec3 faceNormal);

	GlbScene& Scene () { return scene; }
	std::uint64_t FlippedPolygons () const { return flipped; }
	std::uint64_t ZeroNormals () const { return zeroNormals; }
	std::uint64_t Triangles () const { return triangles; }

private:
	GlbScene scene;
	std::map<std::int64_t, std::uint32_t> materialOf;
	/** Primitiv je Material im aktuellen Mesh. */
	std::map<std::uint32_t, std::size_t> primitiveOf;
	std::uint64_t flipped = 0;
	std::uint64_t zeroNormals = 0;
	std::uint64_t triangles = 0;
};

/** Archicad-Oberflächenfarben sind Bildschirmfarben (sRGB); glTF will lineare Werte. */
float SrgbToLinear (double channel);

} // namespace rtx
