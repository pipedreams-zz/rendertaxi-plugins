// Messauftrag Archicad-Modellweg (RTX-A-010, #256). Siehe ModelGlbSpike.hpp.
//
// Ein Messweg je Frage (Stoppkriterium des Auftrags):
//
// - Geometrie: `ACAPI_Sight_GetSelectedSightModel` — das `ModelerAPI::Model`
//   der gewählten Sicht, ohne eigene Sicht und ohne Speichern-unter-Dialog.
// - Kameras: `ACAPI_View_Get3DProjectionSets` am 3D-Fenster, für gespeicherte
//   Ansichten nach `ACAPI_View_GoToView`. Die Navigatoransicht selbst führt
//   keine Kamera (`API_NavigatorView` hat kein Feld dafür).
//
// Die Abbildung Archicad → glTF ist (x, y, z) → (x, z, −y): Archicad ist
// rechtshändig mit Z oben, glTF rechtshändig mit Y oben. Die Abbildung ist
// eine Drehung um −90° um X (Determinante +1), Längen bleiben, wie sie sind.
#include "ModelGlbSpike.hpp"

#include <chrono>
#include <cmath>
#include <cstdio>
#include <fstream>
#include <map>
#include <string>
#include <vector>

#include "ObjectState.hpp"

#include "AttributeIndex.hpp"
#include "ConvexPolygon.hpp"
#include "Model.hpp"
#include "ModelElement.hpp"
#include "ModelMaterial.hpp"
#include "ModelMeshBody.hpp"
#include "Polygon.hpp"
#include "Vertex.hpp"

#include "../../src/HostInfo.hpp"
#include "GlbWriter.hpp"

namespace rtxaddon {
namespace spike {
namespace {

using rtxspike::JsonNumber;
using rtxspike::JsonString;

constexpr const char* kCommandNamespace = "rendertaxi";

using Clock = std::chrono::steady_clock;

/** Nicht `M_PI`: MSVC kennt es nur mit `_USE_MATH_DEFINES` (Frage 7). */
constexpr double kPi = 3.14159265358979323846;

double Ms (Clock::time_point from, Clock::time_point to)
{
	return std::chrono::duration<double, std::milli> (to - from).count ();
}

std::string Utf8 (const GS::UniString& text)
{
	return std::string (text.ToCStr (0, MaxUSize, CC_UTF8).Get ());
}

std::string Param (const GS::ObjectState& parameters, const char* key, const char* fallback)
{
	GS::UniString value;
	if (!parameters.Get (key, value)) return fallback;
	return Utf8 (value);
}

bool BoolParam (const GS::ObjectState& parameters, const char* key, bool fallback)
{
	bool value = fallback;
	if (!parameters.Get (key, value)) return fallback;
	return value;
}

double NumParam (const GS::ObjectState& parameters, const char* key, double fallback)
{
	double value = fallback;
	if (!parameters.Get (key, value)) return fallback;
	return value;
}

void Fail (GS::ObjectState& response, const std::string& message)
{
	response.Add ("succeeded", false);
	response.Add ("error", GS::UniString (message.c_str (), CC_UTF8));
}

// --- Vektoren ----------------------------------------------------------------

struct V3 {
	double x = 0, y = 0, z = 0;
};

V3 Sub (V3 a, V3 b) { return {a.x - b.x, a.y - b.y, a.z - b.z}; }
V3 Cross (V3 a, V3 b) { return {a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x}; }
double Dot (V3 a, V3 b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
double Len (V3 a) { return std::sqrt (Dot (a, a)); }
V3 Scale (V3 a, double s) { return {a.x * s, a.y * s, a.z * s}; }
V3 Norm (V3 a)
{
	const double l = Len (a);
	return l > 1e-12 ? Scale (a, 1.0 / l) : V3 {};
}

/** Archicad (x, y, z) → glTF (x, z, −y). */
V3 ToGltf (V3 a) { return {a.x, a.z, -a.y}; }

/** Quaternion (x, y, z, w) aus einer Drehmatrix mit den Spalten r, u, b. */
void Quaternion (V3 r, V3 u, V3 b, double q[4])
{
	const double m00 = r.x, m01 = u.x, m02 = b.x;
	const double m10 = r.y, m11 = u.y, m12 = b.y;
	const double m20 = r.z, m21 = u.z, m22 = b.z;
	const double trace = m00 + m11 + m22;
	if (trace > 0) {
		const double s = 0.5 / std::sqrt (trace + 1.0);
		q[3] = 0.25 / s;
		q[0] = (m21 - m12) * s;
		q[1] = (m02 - m20) * s;
		q[2] = (m10 - m01) * s;
	} else if (m00 > m11 && m00 > m22) {
		const double s = 2.0 * std::sqrt (1.0 + m00 - m11 - m22);
		q[3] = (m21 - m12) / s;
		q[0] = 0.25 * s;
		q[1] = (m01 + m10) / s;
		q[2] = (m02 + m20) / s;
	} else if (m11 > m22) {
		const double s = 2.0 * std::sqrt (1.0 + m11 - m00 - m22);
		q[3] = (m02 - m20) / s;
		q[0] = (m01 + m10) / s;
		q[1] = 0.25 * s;
		q[2] = (m12 + m21) / s;
	} else {
		const double s = 2.0 * std::sqrt (1.0 + m22 - m00 - m11);
		q[3] = (m10 - m01) / s;
		q[0] = (m02 + m20) / s;
		q[1] = (m12 + m21) / s;
		q[2] = 0.25 * s;
	}
}

// --- Farben ------------------------------------------------------------------

/** Archicad-Oberflächenfarben sind Bildschirmfarben (sRGB); glTF will lineare Werte. */
float SrgbToLinear (double c)
{
	if (c <= 0.04045) return static_cast<float> (c / 12.92);
	return static_cast<float> (std::pow ((c + 0.055) / 1.055, 2.4));
}

// --- Kamera ------------------------------------------------------------------

/** Die Projektion des 3D-Fensters, roh und als glTF-Kamera. */
struct CameraReading {
	std::string name;
	std::string source; // "current" oder "view:<guid>"
	GSErrCode err = NoError;
	API_3DProjectionInfo proj {};
	API_3DWindowInfo window {};
	rtxspike::GlbCamera camera;
	std::string fidelity = "exact";
	std::string note;
};

std::string RawProjectionJson (const CameraReading& r)
{
	std::string j = "{\"isPersp\":" + std::string (r.proj.isPersp ? "true" : "false");
	if (r.proj.isPersp) {
		const API_PerspPars& p = r.proj.u.persp;
		j += ",\"persp\":{\"azimuth\":" + JsonNumber (p.azimuth) + ",\"viewCone\":" + JsonNumber (p.viewCone) +
			 ",\"rollAngle\":" + JsonNumber (p.rollAngle) + ",\"distance\":" + JsonNumber (p.distance) +
			 ",\"pos\":[" + JsonNumber (p.pos.x) + "," + JsonNumber (p.pos.y) + "," + JsonNumber (p.cameraZ) + "]" +
			 ",\"target\":[" + JsonNumber (p.target.x) + "," + JsonNumber (p.target.y) + "," + JsonNumber (p.targetZ) +
			 "],\"isTwoPointPersp\":" + (p.isTwoPointPersp ? "true" : "false") + "}";
	} else {
		const API_AxonoPars& a = r.proj.u.axono;
		j += ",\"axono\":{\"azimuth\":" + JsonNumber (a.azimuth) + ",\"projMod\":" + std::to_string (a.projMod) + ",\"tranmat\":[";
		for (int i = 0; i < 12; ++i) j += (i ? "," : "") + JsonNumber (a.tranmat.tmx[i]);
		j += "],\"invtranmat\":[";
		for (int i = 0; i < 12; ++i) j += (i ? "," : "") + JsonNumber (a.invtranmat.tmx[i]);
		j += "]}";
	}
	j += ",\"window\":{\"hSize\":" + std::to_string (r.window.hSize) + ",\"vSize\":" + std::to_string (r.window.vSize) +
		 ",\"zoomScaleX\":" + JsonNumber (r.window.zoomScaleX) + ",\"zoomScaleY\":" + JsonNumber (r.window.zoomScaleY) +
		 ",\"zoomDispX\":" + JsonNumber (r.window.zoomDispX) + ",\"zoomDispY\":" + JsonNumber (r.window.zoomDispY) + "}";
	return j + "}";
}

struct CameraOptions {
	/**
	 * `horizontal`, `vertical` oder `diagonal` — welche Achse `viewCone` meint.
	 * Gemessen (Q-01, QA-05): **waagerecht**; die anderen Werte sind die Gegenprobe.
	 */
	std::string fovAxis = "horizontal";
	/** Seitenverhältnis Breite / Höhe; 0 nimmt das des 3D-Fensters. */
	double aspect = 0.0;
	/** `viewCone` in Grad — gemessen (QA-05), die Dokumentation sagt es nicht. */
	bool viewConeDegrees = true;
	/** `rollAngle` in Grad — gemessen (QA-05). */
	bool rollDegrees = true;
	/** Halbe Höhe der Parallelkamera, nur wenn das 3D-Fenster keinen Ausschnitt nennt. */
	double orthoHalfHeight = 10.0;
};

void BuildCamera (CameraReading& r, const CameraOptions& options)
{
	rtxspike::GlbCamera& cam = r.camera;
	cam.name = r.name;
	double aspect = options.aspect;
	if (aspect <= 0 && r.window.vSize > 0) aspect = static_cast<double> (r.window.hSize) / r.window.vSize;
	if (aspect <= 0) aspect = 1.5;
	cam.aspectRatio = aspect;

	if (r.proj.isPersp) {
		const API_PerspPars& p = r.proj.u.persp;
		const V3 eye {p.pos.x, p.pos.y, p.cameraZ};
		const V3 target {p.target.x, p.target.y, p.targetZ};
		V3 dir = Norm (Sub (target, eye));
		if (Len (dir) < 0.5) dir = {0, 1, 0};

		// Zweifluchtpunkt: Bildebene senkrecht, also waagerechte Blickrichtung;
		// die Neigung wandert in einen senkrechten Shift (Q-09).
		double pitchTan = 0.0;
		if (p.isTwoPointPersp) {
			const double horizontal = std::sqrt (dir.x * dir.x + dir.y * dir.y);
			if (horizontal > 1e-9) {
				pitchTan = dir.z / horizontal;
				dir = Norm (V3 {dir.x, dir.y, 0});
			}
		}

		// Basis wie im DevKit-Beispiel `ModelAccess_Test` (CalcOrientation):
		// rechts = (d.y, −d.x, 0), oben = rechts × d, dann um den Rollwinkel gedreht.
		V3 right0 = Norm (V3 {dir.y, -dir.x, 0});
		if (Len (right0) < 0.5) right0 = {1, 0, 0};
		const V3 top0 = Cross (right0, dir);
		const double roll = p.isTwoPointPersp ? 0.0 : (options.rollDegrees ? p.rollAngle * kPi / 180.0 : p.rollAngle);
		const double s = std::sin (roll), c = std::cos (roll);
		const V3 right {right0.x * c + top0.x * s, right0.y * c + top0.y * s, right0.z * c + top0.z * s};
		const V3 top {top0.x * c - right0.x * s, top0.y * c - right0.y * s, top0.z * c - right0.z * s};

		const V3 gr = ToGltf (right), gu = ToGltf (top), gb = ToGltf (Scale (dir, -1.0));
		Quaternion (gr, gu, gb, cam.rotation);
		const V3 ge = ToGltf (eye);
		cam.translation[0] = ge.x;
		cam.translation[1] = ge.y;
		cam.translation[2] = ge.z;

		const double cone = options.viewConeDegrees ? p.viewCone * kPi / 180.0 : p.viewCone;
		double halfTanV = 0;
		if (options.fovAxis == "vertical") {
			halfTanV = std::tan (cone / 2);
		} else if (options.fovAxis == "diagonal") {
			halfTanV = std::tan (cone / 2) / std::sqrt (1 + aspect * aspect);
		} else {
			halfTanV = std::tan (cone / 2) / aspect;
		}
		cam.yfov = 2 * std::atan (halfTanV);
		cam.znear = 0.1;
		cam.perspective = true;

		if (p.isTwoPointPersp && std::fabs (pitchTan) > 1e-9) {
			// Lage des Ziels auf der senkrechten Bildebene, in NDC: tan(Neigung) / tan(yfov/2).
			// Shift ist der Anteil der längeren Bildseite (gltf-camera-extras).
			const double yNdc = pitchTan / halfTanV;
			const double shiftY = (yNdc / 2.0) * (aspect >= 1 ? 1.0 / aspect : 1.0);
			cam.extrasJson = "{\"rendertaxi\":{\"camera\":{\"shift\":{\"x\":0,\"y\":" + JsonNumber (shiftY) + "}}}}";
			r.note = "Zweifluchtpunkt als waagerechte Kamera mit senkrechtem Shift";
		}
	} else {
		// Parallelprojektion: `tranmat` bildet Modell- auf Projektionskoordinaten ab.
		const API_AxonoPars& a = r.proj.u.axono;
		const double* t = a.tranmat.tmx;
		const V3 rowX {t[0], t[1], t[2]};
		const V3 rowY {t[4], t[5], t[6]};
		const V3 right = Norm (rowX);
		const V3 top = Norm (rowY);
		if (Len (right) < 0.5 || Len (top) < 0.5) {
			r.fidelity = "unavailable";
			r.note = "tranmat ohne lesbare Achsen";
		}
		// Rücken der Kamera: rechts × oben (rechtshändig). Gemessen ist das
		// parallel zur dritten Zeile von tranmat.
		const V3 back = Norm (Cross (right, top));
		const V3 gr = ToGltf (right), gu = ToGltf (top), gb = ToGltf (back);
		Quaternion (gr, gu, gb, cam.rotation);
		cam.perspective = false;

		// Ausschnitt (gemessen am Bild, siehe Protokoll): Pixel des 3D-Fensters
		//   x = zoomDispX + zoomScaleX · u,   y = −(zoomDispY + zoomScaleY · v)
		// mit (u, v) = tranmat · p. Die Zeilen von tranmat sind nicht normiert —
		// ihre Längen sind die Verkürzung der Axonometrie je Bildachse.
		const double lenX = Len (rowX), lenY = Len (rowY);
		const API_3DWindowInfo& w = r.window;
		if (w.zoomScaleX > 0 && w.zoomScaleY > 0 && lenX > 1e-9 && lenY > 1e-9 && w.hSize > 0 && w.vSize > 0) {
			cam.xmag = (w.hSize / 2.0) / (w.zoomScaleX * lenX);
			cam.ymag = (w.vSize / 2.0) / (w.zoomScaleY * lenY);
			const double uc = (w.hSize / 2.0 - w.zoomDispX) / w.zoomScaleX - t[3];
			const double vc = (-w.vSize / 2.0 - w.zoomDispY) / w.zoomScaleY - t[7];
			const V3 centre = {right.x * uc / lenX + top.x * vc / lenY, right.y * uc / lenX + top.y * vc / lenY,
							   right.z * uc / lenX + top.z * vc / lenY};
			const double back_off = 1000.0;
			const V3 eye = ToGltf (V3 {centre.x + back.x * back_off, centre.y + back.y * back_off, centre.z + back.z * back_off});
			cam.translation[0] = eye.x;
			cam.translation[1] = eye.y;
			cam.translation[2] = eye.z;
			cam.znear = 0.0;
			cam.zfar = 2 * back_off;
		} else {
			cam.ymag = options.orthoHalfHeight;
			cam.xmag = options.orthoHalfHeight * aspect;
			cam.znear = 0.0;
			cam.zfar = 2000.0;
			r.fidelity = "approximated";
			r.note = "Parallelprojektion ohne Ausschnitt des 3D-Fensters";
		}
		// Schiefe Parallelprojektion (Kavalier, Frontal): Bildachsen nicht
		// senkrecht zueinander — glTF kennt nur die rechtwinklige.
		const double shear = std::fabs (Dot (Norm (rowX), Norm (rowY)));
		if (shear > 1e-3 && r.fidelity == "exact") {
			r.fidelity = "approximated";
			r.note = "Schiefe Parallelprojektion (Scherung " + JsonNumber (std::asin (shear) * 180 / kPi) +
					 "°), als rechtwinklige genähert";
		}
		cam.extrasJson.clear ();
	}
	cam.nodeExtrasJson = "{\"rendertaxi\":{\"source\":" + JsonString (r.source) + ",\"fidelity\":" +
						 JsonString (r.fidelity) + "}}";
}

CameraReading ReadCamera (const std::string& name, const std::string& source, const CameraOptions& options)
{
	CameraReading r;
	r.name = name;
	r.source = source;
	r.err = ACAPI_View_Get3DProjectionSets (&r.proj);
	ACAPI_View_Get3DWindowSets (&r.window);
	if (r.err == NoError) BuildCamera (r, options);
	return r;
}

// --- Geometrie ---------------------------------------------------------------

struct GeometryCounts {
	Int32 elements = 0;
	Int32 elementsWithBodies = 0;
	Int64 bodies = 0;
	Int64 wireBodies = 0;
	Int64 surfaceBodies = 0;
	Int64 solidBodies = 0;
	Int64 closedBodies = 0;
	Int64 smoothBodies = 0;
	Int64 polygons = 0;
	Int64 invisiblePolygons = 0;
	Int64 complexPolygons = 0;
	Int64 convexPolygons = 0;
	Int64 polygonsWithTexture = 0;
	Int64 flippedWinding = 0;
	Int64 zeroNormals = 0;
	Int64 polygonErrors = 0;
	Int64 edges = 0;
	Int32 textures = 0;
	double min[3] = {1e300, 1e300, 1e300};
	double max[3] = {-1e300, -1e300, -1e300};
};

struct MaterialInfo {
	Int32 index = 0;
	std::string name;
	double color[3] = {0, 0, 0};
	double transparency = 0;
	bool hasTexture = false;
	std::string textureName;
	std::uint32_t glbIndex = 0;
	Int64 triangles = 0;
};

class Converter {
public:
	Converter (const ModelerAPI::Model& model, bool guids, bool perMaterial)
		: model (model), guids (guids), perMaterial (perMaterial) {}

	void Run ()
	{
		counts.elements = model.GetElementCount ();
		counts.textures = model.GetTextureCount ();
		if (perMaterial) scene.meshes.push_back ({});
		for (Int32 e = 1; e <= counts.elements; ++e) {
			ModelerAPI::Element element;
			model.GetElement (e, &element);
			if (element.IsInvalid ()) continue;
			const Int32 bodyCount = element.GetTessellatedBodyCount ();
			if (bodyCount <= 0) continue;
			++counts.elementsWithBodies;
			if (!perMaterial) {
				rtxspike::GlbMesh mesh;
				if (guids) mesh.nodeName = Utf8 (element.GetElemGuid ().ToUniString ());
				scene.meshes.push_back (std::move (mesh));
				primitiveOf.clear ();
			}
			for (Int32 b = 1; b <= bodyCount; ++b) {
				ModelerAPI::MeshBody body;
				element.GetTessellatedBody (b, &body);
				Body (body);
			}
		}
	}

	rtxspike::GlbScene scene;
	GeometryCounts counts;
	std::map<Int32, MaterialInfo> materials;

private:
	const ModelerAPI::Model& model;
	bool guids;
	bool perMaterial;
	/** Primitiv je glTF-Material im aktuellen Mesh. */
	std::map<std::uint32_t, std::size_t> primitiveOf;

	std::uint32_t Material (const ModelerAPI::Polygon& polygon)
	{
		ModelerAPI::AttributeIndex iMat (ModelerAPI::AttributeIndex::MaterialIndex);
		polygon.GetMaterialIndex (iMat);
		const Int32 key = iMat.IsValid () ? iMat.GetIndex () : 0;
		auto found = materials.find (key);
		if (found != materials.end ()) return found->second.glbIndex;

		MaterialInfo info;
		info.index = key;
		rtxspike::GlbMaterial glb;
		if (iMat.IsValid ()) {
			ModelerAPI::Material material;
			model.GetMaterial (iMat, &material);
			info.name = Utf8 (material.GetName ());
			const ModelerAPI::Color color = material.GetSurfaceColor ();
			info.color[0] = color.red;
			info.color[1] = color.green;
			info.color[2] = color.blue;
			info.transparency = material.GetTransparency ();
			ModelerAPI::AttributeIndex iText (ModelerAPI::AttributeIndex::TextureIndex);
			material.GetTextureIndex (iText);
			info.hasTexture = iText.IsValid () && iText.GetIndex () > 1;
			if (info.hasTexture) info.textureName = Utf8 (material.GetTextureName ());
			glb.rgba[0] = SrgbToLinear (color.red);
			glb.rgba[1] = SrgbToLinear (color.green);
			glb.rgba[2] = SrgbToLinear (color.blue);
			const double alpha = 1.0 - std::min (1.0, std::max (0.0, info.transparency));
			glb.rgba[3] = static_cast<float> (alpha);
			glb.blend = alpha < 0.999;
		} else {
			info.name = "(ohne Oberfläche)";
		}
		glb.name = info.name.empty () ? "Oberfläche " + std::to_string (key) : info.name;
		info.glbIndex = static_cast<std::uint32_t> (scene.materials.size ());
		scene.materials.push_back (glb);
		materials[key] = info;
		return info.glbIndex;
	}

	rtxspike::GlbPrimitive& PrimitiveFor (std::uint32_t material)
	{
		rtxspike::GlbMesh& mesh = scene.meshes.back ();
		auto found = primitiveOf.find (material);
		if (found != primitiveOf.end ()) return mesh.primitives[found->second];
		primitiveOf[material] = mesh.primitives.size ();
		mesh.primitives.push_back ({});
		mesh.primitives.back ().material = material;
		return mesh.primitives.back ();
	}

	void Body (const ModelerAPI::MeshBody& body)
	{
		++counts.bodies;
		if (body.IsWireBody ()) {
			++counts.wireBodies;
			return;
		}
		if (body.IsSurfaceBody ()) ++counts.surfaceBodies;
		if (body.IsSolidBody ()) ++counts.solidBodies;
		if (body.IsClosed ()) ++counts.closedBodies;
		if (body.IsVisibleIfContour ()) ++counts.smoothBodies;
		counts.edges += body.GetEdgeCount ();

		const Int32 polygonCount = body.GetPolygonCount ();
		for (Int32 p = 1; p <= polygonCount; ++p) {
			++counts.polygons;
			ModelerAPI::Polygon polygon;
			body.GetPolygon (p, &polygon);
			if (polygon.IsInvisible ()) {
				++counts.invisiblePolygons;
				continue;
			}
			if (polygon.IsComplex ()) ++counts.complexPolygons;
			if (polygon.HasMaterialTexture () || polygon.HasPolygonTexture ()) ++counts.polygonsWithTexture;
			const std::uint32_t material = Material (polygon);

			ModelerAPI::Vector polygonNormal;
			body.GetVector (polygon.GetNormalVectorIndex (), &polygonNormal);
			const V3 faceNormal = Norm (V3 {polygonNormal.x, polygonNormal.y, polygonNormal.z});

			try {
				const Int32 convexCount = polygon.GetConvexPolygonCount ();
				for (Int32 c = 1; c <= convexCount; ++c) {
					ModelerAPI::ConvexPolygon convex;
					polygon.GetConvexPolygon (c, &convex);
					++counts.convexPolygons;
					Convex (body, convex, faceNormal, PrimitiveFor (material), material);
				}
			} catch (const GS::Exception&) {
				++counts.polygonErrors;
			}
		}
	}

	void Convex (const ModelerAPI::MeshBody& body, const ModelerAPI::ConvexPolygon& convex, V3 faceNormal,
				 rtxspike::GlbPrimitive& prim, std::uint32_t material)
	{
		const Int32 n = convex.GetVertexCount ();
		if (n < 3) return;
		std::vector<V3> corners;
		std::vector<V3> normals;
		corners.reserve (n);
		for (Int32 i = 1; i <= n; ++i) {
			ModelerAPI::Vertex v;
			body.GetVertex (convex.GetVertexIndex (i), &v);
			corners.push_back ({v.x, v.y, v.z});
			const ModelerAPI::Vector vn = convex.GetNormalVectorByVertex (i);
			V3 normal = Norm (V3 {vn.x, vn.y, vn.z});
			if (Len (normal) < 0.5) {
				++counts.zeroNormals;
				normal = faceNormal;
			}
			normals.push_back (normal);
			counts.min[0] = std::min (counts.min[0], v.x);
			counts.min[1] = std::min (counts.min[1], v.y);
			counts.min[2] = std::min (counts.min[2], v.z);
			counts.max[0] = std::max (counts.max[0], v.x);
			counts.max[1] = std::max (counts.max[1], v.y);
			counts.max[2] = std::max (counts.max[2], v.z);
		}
		// Umlaufsinn gegen die Flächennormale prüfen; glTF will gegen den Uhrzeigersinn von außen.
		const V3 geometric = Cross (Sub (corners[1], corners[0]), Sub (corners[2], corners[0]));
		const bool flip = Len (faceNormal) > 0.5 && Dot (geometric, faceNormal) < 0;
		if (flip) ++counts.flippedWinding;

		const std::uint32_t base = static_cast<std::uint32_t> (prim.positions.size () / 3);
		for (std::size_t i = 0; i < corners.size (); ++i) {
			const V3 p = ToGltf (corners[i]);
			const V3 q = ToGltf (normals[i]);
			prim.positions.insert (prim.positions.end (), {float (p.x), float (p.y), float (p.z)});
			prim.normals.insert (prim.normals.end (), {float (q.x), float (q.y), float (q.z)});
		}
		for (Int32 i = 1; i + 1 < n; ++i) {
			if (flip)
				prim.indices.insert (prim.indices.end (), {base, base + std::uint32_t (i + 1), base + std::uint32_t (i)});
			else
				prim.indices.insert (prim.indices.end (), {base, base + std::uint32_t (i), base + std::uint32_t (i + 1)});
		}
		materials[MaterialKeyOf (material)].triangles += n - 2;
	}

	Int32 MaterialKeyOf (std::uint32_t glbIndex) const
	{
		for (const auto& [key, info] : materials)
			if (info.glbIndex == glbIndex) return key;
		return 0;
	}
};

bool WriteFile (const std::string& path, const void* data, std::size_t size)
{
	std::ofstream out (path, std::ios::binary);
	out.write (static_cast<const char*> (data), static_cast<std::streamsize> (size));
	return static_cast<bool> (out);
}

CameraOptions ReadCameraOptions (const GS::ObjectState& parameters)
{
	CameraOptions options;
	options.fovAxis = Param (parameters, "fovAxis", "horizontal");
	options.aspect = NumParam (parameters, "aspect", 0.0);
	options.viewConeDegrees = BoolParam (parameters, "viewConeDegrees", true);
	options.rollDegrees = BoolParam (parameters, "rollDegrees", true);
	options.orthoHalfHeight = NumParam (parameters, "orthoHalfHeight", 10.0);
	return options;
}

// --- Befehle -----------------------------------------------------------------

class SpikeCommandBase : public API_AddOnCommand {
public:
	GS::String GetNamespace () const override { return kCommandNamespace; }
	API_AddOnCommandExecutionPolicy GetExecutionPolicy () const override
	{
		return API_AddOnCommandExecutionPolicy::ScheduleForExecutionOnMainThread;
	}
	bool IsProcessWindowVisible () const override { return false; }
	GS::Optional<GS::UniString> GetSchemaDefinitions () const override { return GS::NoValue; }
	GS::Optional<GS::UniString> GetInputParametersSchema () const override { return GS::NoValue; }
	GS::Optional<GS::UniString> GetResponseSchema () const override { return GS::NoValue; }
	void OnResponseValidationFailed (const GS::ObjectState&) const override {}
};

/**
 * `rendertaxi.SpikeModelGlb` — das 3D-Fenster als GLB.
 *
 * Parameter:
 *   `path`           Zieldatei (.glb); der Bericht liegt als `<path>.json` daneben
 *   `granularity`    `element` (ein Knoten je Element, Vorgabe) oder `material`
 *   `includeGuids`   Element-GUID als Knotenname (Vorgabe ja)
 *   `cameras`        `none`, `current` (Vorgabe) oder `saved` (aktuelle + alle gespeicherten 3D-Ansichten)
 *   `fovAxis`, `aspect`, `viewConeDegrees`, `rollDegrees`, `orthoHalfHeight` — siehe CameraOptions
 */
class SpikeModelGlbCommand : public SpikeCommandBase {
public:
	GS::String GetName () const override { return "SpikeModelGlb"; }

	GS::ObjectState Execute (const GS::ObjectState& parameters, GS::ProcessControl&) const override
	{
		GS::ObjectState response;
		const std::string path = Param (parameters, "path", "");
		if (path.empty ()) {
			Fail (response, "path fehlt");
			return response;
		}
		const bool perMaterial = Param (parameters, "granularity", "element") == "material";
		const bool guids = BoolParam (parameters, "includeGuids", true);
		const std::string cameraMode = Param (parameters, "cameras", "current");
		const CameraOptions cameraOptions = ReadCameraOptions (parameters);

		const Clock::time_point t0 = Clock::now ();
		// Welche Sicht gewählt ist, hängt vom Aufrufkontext ab; die des
		// 3D-Fensters wird ausdrücklich gewählt und danach zurückgesetzt.
		void* windowSight = nullptr;
		void* previousSight = nullptr;
		const GSErrCode sightErr = ACAPI_Sight_GetCurrentWindowSight (&windowSight);
		GSErrCode selectErr = APIERR_GENERAL;
		if (sightErr == NoError && windowSight != nullptr) selectErr = ACAPI_Sight_SelectSight (windowSight, &previousSight);
		Int32 bodyCount = -1;
		const GSErrCode numErr = ACAPI_ModelAccess_GetNum (API_BodyID, &bodyCount);
		ModelerAPI::Model model;
		const GSErrCode modelErr = ACAPI_Sight_GetSelectedSightModel (model);
		if (selectErr == NoError && previousSight != nullptr && previousSight != windowSight)
			ACAPI_Sight_SelectSight (previousSight, nullptr);
		const Clock::time_point t1 = Clock::now ();
		const std::string sightJson = "{\"currentWindowSightErr\":" + std::to_string (sightErr) +
									  ",\"selectErr\":" + std::to_string (selectErr) + ",\"sameAsPrevious\":" +
									  (previousSight == windowSight ? "true" : "false") + ",\"getNumErr\":" +
									  std::to_string (numErr) + ",\"bodyCount\":" + std::to_string (bodyCount) +
									  ",\"modelErr\":" + std::to_string (modelErr) + "}";
		if (modelErr != NoError) {
			Fail (response, "ACAPI_Sight_GetSelectedSightModel: " + std::to_string (modelErr));
			return response;
		}

		Converter converter (model, guids, perMaterial);
		converter.Run ();
		const Clock::time_point t2 = Clock::now ();

		// Kameras: zuerst das Fenster, wie es ist; dann je gespeicherter 3D-Ansicht.
		std::vector<CameraReading> cameras;
		API_3DProjectionInfo before {};
		const GSErrCode beforeErr = ACAPI_View_Get3DProjectionSets (&before);
		std::string viewsError;
		if (cameraMode != "none") cameras.push_back (ReadCamera ("Aktuelle Ansicht", "current", cameraOptions));
		if (cameraMode == "saved") {
			const std::string opened = OpenedViewGuid ();
			for (const rtx::SavedView& view : ListSaved3DViews ()) {
				const std::string err = OpenSavedView (view);
				if (!err.empty ()) {
					viewsError += err + "\n";
					continue;
				}
				cameras.push_back (ReadCamera (view.name, "view:" + view.guid, cameraOptions));
			}
			// Zurück: die vorher geöffnete Ansicht, sonst die Projektion von vorher.
			bool restored = false;
			if (!opened.empty ())
				for (const rtx::SavedView& view : ListSaved3DViews ())
					if (view.guid == opened) restored = OpenSavedView (view).empty ();
			if (!restored && beforeErr == NoError) ACAPI_View_Change3DProjectionSets (&before);
		}
		const Clock::time_point t3 = Clock::now ();
		for (const CameraReading& r : cameras)
			if (r.err == NoError) converter.scene.cameras.push_back (r.camera);

		rtxspike::GlbStats stats;
		const std::vector<std::uint8_t> glb = rtxspike::WriteGlb (converter.scene, &stats);
		const bool written = WriteFile (path, glb.data (), glb.size ());
		const Clock::time_point t4 = Clock::now ();

		// --- Bericht -----------------------------------------------------------
		const GeometryCounts& c = converter.counts;
		std::string j = "{\"glbPath\":" + JsonString (path) + ",\"byteSize\":" + std::to_string (glb.size ()) +
						",\"jsonBytes\":" + std::to_string (stats.jsonBytes) + ",\"binBytes\":" + std::to_string (stats.binBytes) +
						",\"triangles\":" + std::to_string (stats.triangles) + ",\"vertices\":" + std::to_string (stats.vertices) +
						",\"granularity\":" + JsonString (perMaterial ? "material" : "element") +
						",\"includeGuids\":" + (guids ? "true" : "false") + ",\"meshes\":" +
						std::to_string (converter.scene.meshes.size ()) + ",\"materials\":" +
						std::to_string (converter.scene.materials.size ());
		j += ",\"sight\":" + sightJson;
		j += ",\"timingsMs\":{\"getModel\":" + JsonNumber (Ms (t0, t1)) + ",\"convert\":" + JsonNumber (Ms (t1, t2)) +
			 ",\"cameras\":" + JsonNumber (Ms (t2, t3)) + ",\"write\":" + JsonNumber (Ms (t3, t4)) +
			 ",\"total\":" + JsonNumber (Ms (t0, t4)) + "}";
		j += ",\"archicadBounds\":{\"min\":[" + JsonNumber (c.min[0]) + "," + JsonNumber (c.min[1]) + "," +
			 JsonNumber (c.min[2]) + "],\"max\":[" + JsonNumber (c.max[0]) + "," + JsonNumber (c.max[1]) + "," +
			 JsonNumber (c.max[2]) + "]}";
		j += ",\"gltfBounds\":{\"min\":[" + JsonNumber (stats.boundsMin[0]) + "," + JsonNumber (stats.boundsMin[1]) + "," +
			 JsonNumber (stats.boundsMin[2]) + "],\"max\":[" + JsonNumber (stats.boundsMax[0]) + "," +
			 JsonNumber (stats.boundsMax[1]) + "," + JsonNumber (stats.boundsMax[2]) + "]}";
		j += ",\"counts\":{\"elements\":" + std::to_string (c.elements) + ",\"elementsWithBodies\":" +
			 std::to_string (c.elementsWithBodies) + ",\"bodies\":" + std::to_string (c.bodies) + ",\"wireBodies\":" +
			 std::to_string (c.wireBodies) + ",\"surfaceBodies\":" + std::to_string (c.surfaceBodies) +
			 ",\"solidBodies\":" + std::to_string (c.solidBodies) + ",\"closedBodies\":" + std::to_string (c.closedBodies) +
			 ",\"smoothBodies\":" + std::to_string (c.smoothBodies) + ",\"polygons\":" + std::to_string (c.polygons) +
			 ",\"invisiblePolygons\":" + std::to_string (c.invisiblePolygons) + ",\"complexPolygons\":" +
			 std::to_string (c.complexPolygons) + ",\"convexPolygons\":" + std::to_string (c.convexPolygons) +
			 ",\"polygonsWithTexture\":" + std::to_string (c.polygonsWithTexture) + ",\"flippedWinding\":" +
			 std::to_string (c.flippedWinding) + ",\"zeroNormals\":" + std::to_string (c.zeroNormals) +
			 ",\"polygonErrors\":" + std::to_string (c.polygonErrors) + ",\"edges\":" + std::to_string (c.edges) +
			 ",\"textures\":" + std::to_string (c.textures) + "}";
		j += ",\"materialList\":[";
		bool first = true;
		for (const auto& [key, info] : converter.materials) {
			j += std::string (first ? "" : ",") + "{\"index\":" + std::to_string (key) + ",\"name\":" + JsonString (info.name) +
				 ",\"color\":[" + JsonNumber (info.color[0]) + "," + JsonNumber (info.color[1]) + "," +
				 JsonNumber (info.color[2]) + "],\"transparency\":" + JsonNumber (info.transparency) +
				 ",\"hasTexture\":" + (info.hasTexture ? "true" : "false") + ",\"texture\":" +
				 JsonString (info.textureName) + ",\"triangles\":" + std::to_string (info.triangles) + "}";
			first = false;
		}
		j += "],\"cameras\":[";
		for (std::size_t i = 0; i < cameras.size (); ++i) {
			const CameraReading& r = cameras[i];
			j += std::string (i ? "," : "") + "{\"name\":" + JsonString (r.name) + ",\"source\":" + JsonString (r.source) +
				 ",\"err\":" + std::to_string (r.err) + ",\"fidelity\":" + JsonString (r.fidelity) + ",\"note\":" +
				 JsonString (r.note) + ",\"raw\":" + RawProjectionJson (r) + ",\"gltf\":{\"perspective\":" +
				 (r.camera.perspective ? "true" : "false") + ",\"yfov\":" + JsonNumber (r.camera.yfov) +
				 ",\"aspectRatio\":" + JsonNumber (r.camera.aspectRatio) + ",\"translation\":[" +
				 JsonNumber (r.camera.translation[0]) + "," + JsonNumber (r.camera.translation[1]) + "," +
				 JsonNumber (r.camera.translation[2]) + "],\"rotation\":[" + JsonNumber (r.camera.rotation[0]) + "," +
				 JsonNumber (r.camera.rotation[1]) + "," + JsonNumber (r.camera.rotation[2]) + "," +
				 JsonNumber (r.camera.rotation[3]) + "],\"extras\":" +
				 (r.camera.extrasJson.empty () ? std::string ("null") : r.camera.extrasJson) + "}}";
		}
		j += "],\"viewsError\":" + JsonString (viewsError) + "}";
		const std::string reportPath = path + ".json";
		WriteFile (reportPath, j.data (), j.size ());

		if (!written) {
			Fail (response, "GLB nicht schreibbar: " + path);
			return response;
		}
		response.Add ("succeeded", true);
		response.Add ("reportPath", GS::UniString (reportPath.c_str (), CC_UTF8));
		response.Add ("byteSize", static_cast<Int64> (glb.size ()));
		response.Add ("triangles", static_cast<Int64> (stats.triangles));
		response.Add ("cameras", static_cast<Int32> (converter.scene.cameras.size ()));
		response.Add ("totalMs", Ms (t0, t4));
		return response;
	}
};

/** `rendertaxi.SpikeReadCamera` — die rohe Projektion des 3D-Fensters und die abgeleitete glTF-Kamera. */
class SpikeReadCameraCommand : public SpikeCommandBase {
public:
	GS::String GetName () const override { return "SpikeReadCamera"; }

	GS::ObjectState Execute (const GS::ObjectState& parameters, GS::ProcessControl&) const override
	{
		GS::ObjectState response;
		const CameraReading r = ReadCamera ("Aktuelle Ansicht", "current", ReadCameraOptions (parameters));
		if (r.err != NoError) {
			Fail (response, "ACAPI_View_Get3DProjectionSets: " + std::to_string (r.err));
			return response;
		}
		response.Add ("succeeded", true);
		response.Add ("raw", GS::UniString (RawProjectionJson (r).c_str (), CC_UTF8));
		response.Add ("fidelity", GS::UniString (r.fidelity.c_str (), CC_UTF8));
		response.Add ("yfov", r.camera.yfov);
		response.Add ("aspectRatio", r.camera.aspectRatio);
		return response;
	}
};

/**
 * `rendertaxi.SpikeSetCamera` — setzt die Perspektive des 3D-Fensters für die Testszene.
 *
 * Parameter: `pos` und `target` (je x, y, z in Metern), `viewCone`, `rollAngle`
 * (in den Einheiten der API), `twoPoint` (bool). Alle anderen Felder bleiben,
 * wie sie sind.
 */
class SpikeSetCameraCommand : public SpikeCommandBase {
public:
	GS::String GetName () const override { return "SpikeSetCamera"; }

	GS::ObjectState Execute (const GS::ObjectState& parameters, GS::ProcessControl&) const override
	{
		GS::ObjectState response;
		API_3DProjectionInfo proj {};
		GSErrCode err = ACAPI_View_Get3DProjectionSets (&proj);
		if (err != NoError) {
			Fail (response, "Get3DProjectionSets: " + std::to_string (err));
			return response;
		}
		const API_3DProjectionInfo old = proj;
		proj.isPersp = true;
		if (!old.isPersp) proj.u.persp = API_PerspPars {};
		API_PerspPars& p = proj.u.persp;
		p.pos.x = NumParam (parameters, "posX", p.pos.x);
		p.pos.y = NumParam (parameters, "posY", p.pos.y);
		p.cameraZ = NumParam (parameters, "posZ", p.cameraZ);
		p.target.x = NumParam (parameters, "targetX", p.target.x);
		p.target.y = NumParam (parameters, "targetY", p.target.y);
		p.targetZ = NumParam (parameters, "targetZ", p.targetZ);
		p.viewCone = NumParam (parameters, "viewCone", p.viewCone);
		p.rollAngle = NumParam (parameters, "rollAngle", p.rollAngle);
		p.isTwoPointPersp = BoolParam (parameters, "twoPoint", p.isTwoPointPersp);
		const double dx = p.target.x - p.pos.x, dy = p.target.y - p.pos.y, dz = p.targetZ - p.cameraZ;
		p.distance = std::sqrt (dx * dx + dy * dy + dz * dz);
		p.azimuth = std::atan2 (dy, dx);
		err = ACAPI_View_Change3DProjectionSets (&proj);
		if (err != NoError) {
			Fail (response, "Change3DProjectionSets: " + std::to_string (err));
			return response;
		}
		// Optional die Fenstergröße: ein deutlich breites Fenster trennt die
		// Hypothesen „waagerecht" und „senkrecht" für viewCone (Q-01).
		const double hSize = NumParam (parameters, "hSize", 0), vSize = NumParam (parameters, "vSize", 0);
		if (hSize > 0 && vSize > 0) {
			API_3DWindowInfo window {};
			ACAPI_View_Get3DWindowSets (&window);
			window.setWindowSize = true;
			window.hSize = static_cast<short> (hSize);
			window.vSize = static_cast<short> (vSize);
			err = ACAPI_View_Change3DWindowSets (&window);
			if (err != NoError) {
				Fail (response, "Change3DWindowSets: " + std::to_string (err));
				return response;
			}
		}
		CameraReading r;
		ACAPI_View_Get3DProjectionSets (&r.proj);
		ACAPI_View_Get3DWindowSets (&r.window);
		response.Add ("succeeded", true);
		response.Add ("raw", GS::UniString (RawProjectionJson (r).c_str (), CC_UTF8));
		return response;
	}
};


/**
 * `rendertaxi.SpikeCutPlanes` — schaltet die 3D-Schnittebenen des 3D-Fensters ein oder aus
 * (`enable`). Die Ebenen selbst setzt die Messung vorher (Tapir `Set3DCutPlanes`); ohne
 * Einschalten definiert das nur die Ebenen, das Fenster schneidet nicht (gemessen).
 */
class SpikeCutPlanesCommand : public SpikeCommandBase {
public:
	GS::String GetName () const override { return "SpikeCutPlanes"; }

	GS::ObjectState Execute (const GS::ObjectState& parameters, GS::ProcessControl&) const override
	{
		GS::ObjectState response;
		API_3DCutPlanesInfo info {};
		GSErrCode err = ACAPI_View_Get3DCuttingPlanes (&info);
		if (err != NoError) {
			Fail (response, "Get3DCuttingPlanes: " + std::to_string (err));
			return response;
		}
		info.isCutPlanes = BoolParam (parameters, "enable", true);
		err = ACAPI_View_Change3DCuttingPlanes (&info);
		const short shapes = info.nShapes;
		if (info.shapes != nullptr) BMKillHandle (reinterpret_cast<GSHandle*> (&info.shapes));
		if (err != NoError) {
			Fail (response, "Change3DCuttingPlanes: " + std::to_string (err));
			return response;
		}
		response.Add ("succeeded", true);
		response.Add ("shapes", static_cast<Int32> (shapes));
		return response;
	}
};

} // namespace

GSErrCode InstallModelGlbSpikeCommands ()
{
	GSErrCode err = ACAPI_AddOnAddOnCommunication_InstallAddOnCommandHandler (GS::NewOwned<SpikeModelGlbCommand> ());
	if (err != NoError) return err;
	err = ACAPI_AddOnAddOnCommunication_InstallAddOnCommandHandler (GS::NewOwned<SpikeReadCameraCommand> ());
	if (err != NoError) return err;
	err = ACAPI_AddOnAddOnCommunication_InstallAddOnCommandHandler (GS::NewOwned<SpikeSetCameraCommand> ());
	if (err != NoError) return err;
	return ACAPI_AddOnAddOnCommunication_InstallAddOnCommandHandler (GS::NewOwned<SpikeCutPlanesCommand> ());
}

} // namespace spike
} // namespace rtxaddon
