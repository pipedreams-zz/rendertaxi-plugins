// Siehe ModelExport.hpp. Die Geometrie folgt dem Prototyp des Messauftrags
// (`spike/model-glb/ModelGlbSpike.cpp`, Klasse `Converter`); die Abbildung nach
// glTF und der Umlaufsinn stehen jetzt im Kern (`rtx::GlbSceneBuilder`).
#include "ModelExport.hpp"

#include <chrono>
#include <map>

#include "AttributeIndex.hpp"
#include "ConvexPolygon.hpp"
#include "Model.hpp"
#include "ModelElement.hpp"
#include "ModelMaterial.hpp"
#include "ModelMeshBody.hpp"
#include "Polygon.hpp"
#include "Vertex.hpp"

#include "HostInfo.hpp"
#include "rtx/Log.hpp"

namespace rtxaddon {
namespace {

using Vec3 = rtx::GlbSceneBuilder::Vec3;

std::string Utf8 (const GS::UniString& text)
{
	return std::string (text.ToCStr (0, MaxUSize, CC_UTF8).Get ());
}

/** Wählt die Sicht des 3D-Fensters und setzt die vorige beim Verlassen zurück. */
class WindowSight final {
public:
	WindowSight ()
	{
		sightErr = ACAPI_Sight_GetCurrentWindowSight (&window);
		if (sightErr == NoError && window != nullptr) selectErr = ACAPI_Sight_SelectSight (window, &previous);
	}
	~WindowSight ()
	{
		if (selectErr == NoError && previous != nullptr && previous != window) ACAPI_Sight_SelectSight (previous, nullptr);
	}
	bool Ok () const { return sightErr == NoError && window != nullptr && selectErr == NoError; }
	GSErrCode Error () const { return sightErr != NoError ? sightErr : selectErr; }

private:
	void* window = nullptr;
	void* previous = nullptr;
	GSErrCode sightErr = APIERR_GENERAL;
	GSErrCode selectErr = APIERR_GENERAL;
};

class Converter final {
public:
	Converter (const ModelerAPI::Model& model, rtx::GlbSceneBuilder& builder, ModelExtraction& counts) :
		model (model), builder (builder), counts (counts)
	{
	}

	void Run ()
	{
		counts.elements = model.GetElementCount ();
		for (Int32 e = 1; e <= counts.elements; ++e) {
			ModelerAPI::Element element;
			model.GetElement (e, &element);
			if (element.IsInvalid ()) continue;
			const Int32 bodyCount = element.GetTessellatedBodyCount ();
			if (bodyCount <= 0) continue;
			builder.BeginElement (Utf8 (element.GetElemGuid ().ToUniString ()));
			for (Int32 b = 1; b <= bodyCount; ++b) {
				ModelerAPI::MeshBody body;
				element.GetTessellatedBody (b, &body);
				Body (body);
			}
		}
	}

private:
	const ModelerAPI::Model& model;
	rtx::GlbSceneBuilder& builder;
	ModelExtraction& counts;

	std::uint32_t Material (const ModelerAPI::Polygon& polygon)
	{
		ModelerAPI::AttributeIndex iMat (ModelerAPI::AttributeIndex::MaterialIndex);
		polygon.GetMaterialIndex (iMat);
		const Int32 key = iMat.IsValid () ? iMat.GetIndex () : 0;
		if (!iMat.IsValid ()) return builder.Material (key, "", 0.8, 0.8, 0.8, 0.0);
		ModelerAPI::Material material;
		model.GetMaterial (iMat, &material);
		const ModelerAPI::Color color = material.GetSurfaceColor ();
		return builder.Material (key, Utf8 (material.GetName ()), color.red, color.green, color.blue,
								 material.GetTransparency ());
	}

	void Body (const ModelerAPI::MeshBody& body)
	{
		++counts.bodies;
		// Drahtkörper tragen keine Flächen; Kanten werden nicht übertragen (QA-01).
		if (body.IsWireBody ()) return;
		const Int32 polygonCount = body.GetPolygonCount ();
		for (Int32 p = 1; p <= polygonCount; ++p) {
			++counts.polygons;
			ModelerAPI::Polygon polygon;
			body.GetPolygon (p, &polygon);
			if (polygon.IsInvisible ()) continue;
			const std::uint32_t material = Material (polygon);
			ModelerAPI::Vector faceNormal;
			body.GetVector (polygon.GetNormalVectorIndex (), &faceNormal);
			try {
				const Int32 convexCount = polygon.GetConvexPolygonCount ();
				for (Int32 c = 1; c <= convexCount; ++c) {
					ModelerAPI::ConvexPolygon convex;
					polygon.GetConvexPolygon (c, &convex);
					Convex (body, convex, {faceNormal.x, faceNormal.y, faceNormal.z}, material);
				}
			} catch (const GS::Exception&) {
				++counts.polygonErrors;
			}
		}
	}

	void Convex (const ModelerAPI::MeshBody& body, const ModelerAPI::ConvexPolygon& convex, Vec3 faceNormal,
				 std::uint32_t material)
	{
		const Int32 n = convex.GetVertexCount ();
		if (n < 3) return;
		std::vector<Vec3> corners;
		std::vector<Vec3> normals;
		corners.reserve (static_cast<std::size_t> (n));
		normals.reserve (static_cast<std::size_t> (n));
		for (Int32 i = 1; i <= n; ++i) {
			ModelerAPI::Vertex v;
			body.GetVertex (convex.GetVertexIndex (i), &v);
			corners.push_back ({v.x, v.y, v.z});
			const ModelerAPI::Vector normal = convex.GetNormalVectorByVertex (i);
			normals.push_back ({normal.x, normal.y, normal.z});
		}
		builder.AddConvexPolygon (material, corners, normals, faceNormal);
	}
};

} // namespace

ModelExtraction ExtractWindowModel (rtx::GlbSceneBuilder& builder)
{
	ModelExtraction result;
	const auto t0 = std::chrono::steady_clock::now ();
	ModelerAPI::Model model;
	{
		const WindowSight sight;
		if (!sight.Ok ()) {
			result.error = "Das Modell kommt aus dem 3D-Fenster. Bitte das 3D-Fenster öffnen oder eine gespeicherte "
						   "3D-Ansicht wählen.";
			rtx::LogLine ("Modell: Sicht des 3D-Fensters nicht wählbar, Fehler " + std::to_string (sight.Error ()));
			return result;
		}
		const GSErrCode err = ACAPI_Sight_GetSelectedSightModel (model);
		if (err != NoError) {
			result.error = "Archicad gab das 3D-Modell nicht heraus (" + std::to_string (err) + ").";
			return result;
		}
	}
	Converter converter (model, builder, result);
	converter.Run ();
	result.milliseconds =
		std::chrono::duration<double, std::milli> (std::chrono::steady_clock::now () - t0).count ();
	// 0 Körper ohne Fehlercode: das 3D-Modell wird gerade neu aufgebaut (QA-09). Nie leer senden.
	result.rebuilding = result.bodies == 0;
	rtx::LogLine ("Modell gelesen: " + std::to_string (result.elements) + " Elemente, " +
				  std::to_string (result.bodies) + " Körper, " + std::to_string (builder.Triangles ()) +
				  " Dreiecke, " + std::to_string (builder.FlippedPolygons ()) + " gedreht, " +
				  std::to_string (result.polygonErrors) + " Polygonfehler, " +
				  std::to_string (static_cast<long long> (result.milliseconds)) + " ms");
	return result;
}

Int32 CountWindowBodies ()
{
	const WindowSight sight;
	if (!sight.Ok ()) return -1;
	Int32 count = 0;
	if (ACAPI_ModelAccess_GetNum (API_BodyID, &count) != NoError) return -1;
	return count;
}

rtx::Result<rtx::ArchicadProjection> ReadWindowProjection ()
{
	API_3DProjectionInfo proj {};
	API_3DWindowInfo window {};
	const GSErrCode err = ACAPI_View_Get3DProjectionSets (&proj);
	if (err != NoError)
		return rtx::Result<rtx::ArchicadProjection>::Fail (rtx::errc::IoFailed,
														   "Archicad gab die Kamera des 3D-Fensters nicht heraus.");
	ACAPI_View_Get3DWindowSets (&window);
	rtx::ArchicadProjection p;
	p.perspective = proj.isPersp;
	if (proj.isPersp) {
		const API_PerspPars& persp = proj.u.persp;
		p.eye[0] = persp.pos.x;
		p.eye[1] = persp.pos.y;
		p.eye[2] = persp.cameraZ;
		p.target[0] = persp.target.x;
		p.target[1] = persp.target.y;
		p.target[2] = persp.targetZ;
		p.viewConeDegrees = persp.viewCone;
		p.rollDegrees = persp.rollAngle;
		p.twoPoint = persp.isTwoPointPersp;
	} else {
		for (int i = 0; i < 12; ++i) p.tranmat[i] = proj.u.axono.tranmat.tmx[i];
		p.projMod = proj.u.axono.projMod;
	}
	p.hSize = window.hSize;
	p.vSize = window.vSize;
	p.zoomScaleX = window.zoomScaleX;
	p.zoomScaleY = window.zoomScaleY;
	p.zoomDispX = window.zoomDispX;
	p.zoomDispY = window.zoomDispY;
	return rtx::Result<rtx::ArchicadProjection>::Ok (p);
}

// --- Ansichtsstand sichern und wiederherstellen (F-02 an #318) -----------------------------------

/**
 * Der Stand des 3D-Fensters vor einer Folge eigener `GoToView`-Aufrufe — **die eine Stelle**
 * für Sichern und Wiederherstellen.
 *
 * `GoToView` setzt mit der Kamera auch Ebenen, Modellansicht-Optionen, Grafische Überschreibung,
 * Renovierungsfilter, Strukturanzeige, 3D-Stil und Rendering-Szene. Archicad 28 hat keine API,
 * die all das einzeln liest und wieder setzt (Ebenen nur als Kombination, die Szene nur zum
 * Setzen), und ein vorübergehender Ausschnitt als Zwischenspeicher lässt sich nicht wieder
 * löschen: `ACAPI_Navigator_DeleteNavigatorView` und auch `API.DeleteNavigatorItems` melden
 * Erfolg, der Eintrag bleibt in der Mappe (Host, 09.10.2026, Build 7006).
 *
 * Wiederherstellbar ist der Stand deshalb nur, wenn er **eine gespeicherte Ansicht** ist: Die
 * Palette öffnet die gewählte Ansicht vor jeder Aufnahme neu, das Fenster zeigt dann genau sie,
 * und sie wird danach wieder geöffnet. Was ein Ausschnitt nicht sicher mitnimmt, wird zusätzlich
 * ausdrücklich gesichert: Projektion, Fenstergröße und Zoom, 3D-Schnittebenen, 3D-Filter. Eine
 * freie Ansicht wird nie verlassen.
 */
class ViewStateGuard final {
public:
	ViewStateGuard ()
	{
		mark = MarkOpenedView ();
		if (!mark.known || mark.guidText.empty ()) {
			error = "Sie werden nur zusammen mit einer gespeicherten Ansicht gelesen; eine freie Ansicht ließe sich "
					"danach nicht vollständig wiederherstellen.";
			return;
		}
		for (const rtx::SavedView& view : ListSaved3DViews ())
			if (view.guid == mark.guidText) origin = view;
		if (origin.guid.empty ()) {
			error = "Die geöffnete Ansicht steht nicht mehr in der Ausschnittsmappe.";
			return;
		}
		if (ACAPI_View_Get3DProjectionSets (&projection) != NoError) {
			error = "Kamera des 3D-Fensters nicht lesbar.";
			return;
		}
		hasWindow = ACAPI_View_Get3DWindowSets (&window) == NoError;
		hasCut = ACAPI_View_Get3DCuttingPlanes (&cut) == NoError;
		hasFilter = ACAPI_View_Get3DImageSets (&filter) == NoError;
		saved = true;
	}

	ViewStateGuard (const ViewStateGuard&) = delete;
	ViewStateGuard& operator= (const ViewStateGuard&) = delete;

	~ViewStateGuard ()
	{
		if (saved && !restored) Restore ({});
		if (hasCut && cut.shapes != nullptr) BMKillHandle (reinterpret_cast<GSHandle*> (&cut.shapes));
	}

	bool Saved () const { return saved; }
	const std::string& Error () const { return error; }

	/** Leer bei Erfolg, sonst was nicht zurückkam. `openedMeanwhile` sind die eigenen `GoToView`-Ziele. */
	std::string Restore (const std::vector<std::string>& openedMeanwhile)
	{
		restored = true;
		std::vector<std::string> failed;
		const std::string back = OpenSavedView (origin);
		if (!back.empty ()) failed.push_back ("Ansicht „" + origin.name + "“");
		if (ACAPI_View_Change3DProjectionSets (&projection) != NoError) failed.push_back ("Kamera");
		if (hasWindow && ACAPI_View_Change3DWindowSets (&window) != NoError) failed.push_back ("Fenstergröße");
		if (hasCut && ACAPI_View_Change3DCuttingPlanes (&cut) != NoError) failed.push_back ("Schnittebenen");
		if (hasFilter && ACAPI_View_Change3DImageSets (&filter) != NoError) failed.push_back ("3D-Filter");
		RestoreOpenedView (mark, openedMeanwhile);
		if (failed.empty ()) return {};
		std::string text = "Der Ansichtsstand kam nicht vollständig zurück: ";
		for (std::size_t i = 0; i < failed.size (); ++i) text += (i > 0 ? ", " : "") + failed[i];
		return text + ". Bitte die Ansicht prüfen.";
	}

private:
	OpenedViewMark mark;
	rtx::SavedView origin;
	API_3DProjectionInfo projection {};
	API_3DWindowInfo window {};
	API_3DCutPlanesInfo cut {};
	API_3DFilterAndCutSettings filter {};
	bool hasWindow = false;
	bool hasCut = false;
	bool hasFilter = false;
	bool saved = false;
	bool restored = false;
	std::string error;
};

SavedViewCameras ReadSavedViewCameras (const std::vector<rtx::SavedView>& views)
{
	SavedViewCameras result;
	if (views.empty ()) return result;
	const auto t0 = std::chrono::steady_clock::now ();
	ViewStateGuard state;
	if (!state.Saved ()) {
		// Ohne gesicherten Stand wird keine Ansicht geöffnet: lieber ohne Zusatzkameras als ein
		// Fenster, das danach etwas anderes zeigt.
		result.warning = "Zusätzliche Kameras ausgelassen: " + state.Error ();
		rtx::LogLine ("Kameras: " + result.warning);
		return result;
	}
	std::vector<std::string> opened;
	for (const rtx::SavedView& view : views) {
		opened.push_back (view.guid);
		const std::string error = OpenSavedView (view);
		if (!error.empty ()) {
			result.skipped.push_back (view.name);
			continue;
		}
		const rtx::Result<rtx::ArchicadProjection> projection = ReadWindowProjection ();
		if (!projection) {
			result.skipped.push_back (view.name);
			continue;
		}
		result.cameras.push_back ({view.name, "view:" + view.guid, projection.Value ()});
	}
	result.warning = state.Restore (opened);
	if (!result.warning.empty ()) rtx::LogLine ("Kameras: " + result.warning);
	rtx::LogLine ("Kameras gelesen: " + std::to_string (result.cameras.size ()) + " von " +
				  std::to_string (views.size ()) + " Ansichten, " +
				  std::to_string (std::chrono::duration_cast<std::chrono::milliseconds> (
									  std::chrono::steady_clock::now () - t0)
									  .count ()) +
				  " ms");
	return result;
}

CutPlanes ReadCutPlanes ()
{
	CutPlanes planes;
	API_3DCutPlanesInfo info {};
	if (ACAPI_View_Get3DCuttingPlanes (&info) != NoError) return planes;
	planes.readable = true;
	planes.enabled = info.isCutPlanes;
	planes.count = info.nShapes;
	if (info.shapes != nullptr) BMKillHandle (reinterpret_cast<GSHandle*> (&info.shapes));
	return planes;
}

} // namespace rtxaddon
