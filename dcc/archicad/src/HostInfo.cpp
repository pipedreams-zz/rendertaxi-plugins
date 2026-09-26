#include "HostInfo.hpp"

#include "APIEnvir.h"
#include "ACAPinc.h"

#if !defined (WINDOWS)
#include <sys/utsname.h>
#endif

#include "rtx/Ids.hpp"
#include "rtx/Sha256.hpp"

namespace rtxaddon {
namespace {

std::string Utf8 (const GS::UniString& text)
{
	return std::string (text.ToCStr (0, MaxUSize, CC_UTF8).Get ());
}

std::string Utf8 (const GS::uchar_t* text)
{
	return Utf8 (GS::UniString (text));
}

/** Name des Unique AddOnObject, unter dem der Projektschlüssel liegt. */
const char* const kProjectKeyObjectName = "rendertaxi.projectKey";

std::string SanitizeKey (const std::string& value)
{
	// `common.schema.json#/$defs/stableKey`: `^[a-z0-9][a-z0-9._:-]*$`.
	std::string out;
	for (const char raw : value) {
		const char c = static_cast<char> (raw >= 'A' && raw <= 'Z' ? raw + 32 : raw);
		const bool ok = (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '.' || c == '_' ||
						c == ':' || c == '-';
		out += ok ? c : '-';
	}
	return out;
}

} // namespace

HostVersion ReadHostVersion ()
{
	API_ServerApplicationInfo info;
	ACAPI_GetReleaseNumber (&info);
	HostVersion version;
	version.mainVersion = static_cast<int> (info.mainVersion);
	version.version =
		std::to_string (info.mainVersion) + "." + std::to_string (info.releaseVersion);
	version.build = std::to_string (info.buildNum);
	return version;
}

MachineInfo ReadMachineInfo ()
{
	MachineInfo machine;
#if defined (WINDOWS)
	// Der Windows-Build ist ein reiner x64-Build (Archicad 28 gibt es unter
	// Windows nur so); die Betriebssystemfassung ist im Manifest optional.
	machine.os = "windows";
	machine.architecture = "x64";
	return machine;
#else
	machine.os = "macos";
	utsname system {};
	if (uname (&system) == 0) {
		machine.osVersion = system.release;
		const std::string arch = system.machine;
		machine.architecture = arch == "x86_64" ? "x64" : "arm64";
	} else {
		machine.architecture = "arm64";
	}
	return machine;
#endif
}

namespace {

/** Der zuletzt aus der Ausschnittsmappe geöffnete Eintrag. */
struct OpenedView {
	std::string name;
	std::string guidText;
	API_Guid database = APINULLGuid;
	bool known = false;
};

OpenedView& LastOpenedView ()
{
	static OpenedView value;
	return value;
}

/**
 * Archicad meldet hier jeden Ausschnitt, den jemand öffnet. Gespeichert wird
 * **Name und Datenbank**; die Datenbank entscheidet später, ob der Eintrag
 * überhaupt zum aktuellen Fenster gehört.
 */
GSErrCode ViewEventHandler (const API_NotifyViewEventType* viewEvent)
{
	if (viewEvent == nullptr) return NoError;
	if (viewEvent->notifID != APINotifyView_Opened) return NoError;

	API_NavigatorItem item = {};
	item.guid = viewEvent->itemGuid;
	item.mapId = viewEvent->mapId;
	if (ACAPI_Navigator_GetNavigatorItem (&viewEvent->itemGuid, &item) != NoError) return NoError;

	OpenedView& last = LastOpenedView ();
	last.name = Utf8 (item.uName);
	if (last.name.empty ()) last.name = Utf8 (item.uAutoTextedName);
	last.guidText = Utf8 (APIGuid2GSGuid (item.guid).ToUniString ());
	last.database = item.db.databaseUnId.elemSetId;
	last.known = !last.name.empty ();
	return NoError;
}

} // namespace

long InstallViewTracking ()
{
	// Nur „geöffnet": eingefügte, geänderte oder gelöschte Einträge sagen
	// nichts darüber, was gerade zu sehen ist.
	//
	// **Beide Ausschnittsmappen.** Archicad führt die geteilte
	// (`API_PublicViewMap`) und die persönliche (`API_MyViewMap`); ein
	// Ausschnitt kann aus jeder von beiden geöffnet werden.
	const GSErrCode publicMap =
		ACAPI_Notification_CatchViewEvent (APINotifyView_Opened, API_PublicViewMap,
										   ViewEventHandler);
	const GSErrCode myMap =
		ACAPI_Notification_CatchViewEvent (APINotifyView_Opened, API_MyViewMap, ViewEventHandler);
	return publicMap != NoError ? publicMap : myMap;
}

SourceView ReadCurrentView ()
{
	SourceView view;
	API_WindowInfo window = {};
	if (ACAPI_Window_GetCurrentWindow (&window) != NoError) return view;

	// Gemessen am 20.09.2026 (Archicad 28, Build 7006): `title` und `name` sind
	// für das 3D- und das Grundrissfenster **leer**. Die Dokumentation von
	// `ACAPI_Window_GetCurrentWindow` sagt dazu, dass nur die Felder gefüllt
	// werden, „which are necessary to identify the window type and the
	// database behind the windows". Es wird deshalb nichts erfunden: der
	// Anzeigename bleibt leer, und der Namensvorschlag entsteht andernorts aus
	// Projektname und Fensterart. Siehe `open-questions.md`, Q-13.
	view.displayName = Utf8 (window.title);
	if (view.displayName.empty ()) view.displayName = Utf8 (window.name);

	switch (window.typeID) {
		case APIWind_3DModelID:
			view.windowKind = "3d";
			view.windowLabel = "3D-Ansicht";
			view.is3D = true;
			view.capturable = true;
			// Das 3D-Fenster ist genau eines je Projekt. Es gibt in Archicad 28
			// keinen dokumentierten Weg, den gerade dargestellten Navigatoreintrag
			// zu lesen — siehe `open-questions.md`, Q-13. Der Schlüssel benennt
			// deshalb das Fenster, nicht die Navigatoransicht.
			view.key = "archicad:window:3d";
			break;
		case APIWind_FloorPlanID:
			view.windowKind = "floorplan";
			view.windowLabel = "Grundriss";
			view.capturable = true;
			view.key = "archicad:window:floorplan";
			break;
		case APIWind_SectionID:
			view.windowKind = "section";
			view.windowLabel = "Schnitt";
			view.capturable = true;
			break;
		case APIWind_ElevationID:
			view.windowKind = "elevation";
			view.windowLabel = "Ansicht";
			view.capturable = true;
			break;
		case APIWind_InteriorElevationID:
			view.windowKind = "interior-elevation";
			view.windowLabel = "Innenansicht";
			view.capturable = true;
			break;
		case APIWind_DetailID:
			view.windowKind = "detail";
			view.windowLabel = "Detail";
			view.capturable = true;
			break;
		case APIWind_WorksheetID:
			view.windowKind = "worksheet";
			view.windowLabel = "Arbeitsblatt";
			view.capturable = true;
			break;
		case APIWind_DocumentFrom3DID:
			view.windowKind = "document-from-3d";
			view.windowLabel = "3D-Dokument";
			view.capturable = true;
			break;
		case APIWind_LayoutID:
			view.windowKind = "layout";
			view.windowLabel = "Layout";
			view.capturable = true;
			break;
		case APIWind_RenderingID:
			// **Das fertige Rendering.** Nutzerwunsch vom 24.09.2026: neben dem
			// 3D-Fenster soll auch das Ergebnis der Photorealistik übernommen
			// werden können. Es wird **nicht neu gerendert** — übertragen wird
			// das Bild, das im Fenster steht; `ACAPI_Rendering_PhotoRender`
			// würde einen neuen Lauf starten und das Fenster danach schließen.
			//
			// Es gibt genau ein Renderfenster je Projekt, und sein Inhalt
			// gehört zu der 3D-Ansicht, aus der er entstand. Der Schlüssel
			// benennt deshalb das Fenster.
			view.windowKind = "rendering";
			view.windowLabel = "Rendering";
			view.isRendering = true;
			view.capturable = true;
			view.key = "archicad:window:rendering";
			break;
		default:
			view.windowKind = "unsupported";
			view.windowLabel = "dieses Fenster";
			view.capturable = false;
			break;
	}

	// **Der Ausschnitt aus der Ausschnittsmappe hat Vorrang** — er ist das,
	// was der Nutzer sieht und benennt. Er gilt nur, wenn er zur Datenbank
	// dieses Fensters gehört; sonst stünde nach einem Fensterwechsel der Name
	// eines Ausschnitts da, der gar nicht mehr offen ist.
	const OpenedView& opened = LastOpenedView ();
	if (opened.known && view.capturable &&
		opened.database == window.databaseUnId.elemSetId) {
		view.displayName = opened.name;
		view.viewMapName = opened.name;
		if (!opened.guidText.empty ())
			view.key = "archicad:view:" + SanitizeKey (opened.guidText);
	}

	if (view.key.empty () && view.capturable) {
		// Die übrigen Fenster hängen an einer Datenbank mit eigener GUID; die
		// ist der stabile Schlüssel der Quellansicht.
		const GS::Guid guid = APIGuid2GSGuid (window.databaseUnId.elemSetId);
		if (guid != GS::NULLGuid)
			view.key = "archicad:db:" + SanitizeKey (Utf8 (guid.ToUniString ()));
	}
	return view;
}

RenderScene ReadCurrentRenderScene ()
{
	RenderScene scene;

	// **Achtung, umgekehrte Speicherregel.** `API_ProjectInfo` gibt seine
	// Zeiger im eigenen Destruktor frei — ein zusätzliches `delete` dort hat
	// Archicad abstürzen lassen. `API_RendImage` hat **keinen** Destruktor,
	// und die Dokumentation von `ACAPI_Rendering_GetRenderingSets` sagt
	// ausdrücklich: „don't forget to free the allocated memory:
	// delete rendInfo.bkgPictFile;". Beide Regeln stehen hier nebeneinander,
	// damit niemand die eine für die andere hält.
	API_RendImage image = {};
	const GSErrCode err = ACAPI_Rendering_GetRenderingSets (&image, APIRendSet_ImageID, nullptr);
	delete image.bkgPictFile;
	image.bkgPictFile = nullptr;
	if (err != NoError) return scene;
	if (image.hSize <= 0 || image.vSize <= 0) return scene;

	scene.known = true;
	scene.width = image.hSize;
	scene.height = image.vSize;

	// Welche Szene die aktuelle ist, sagt das API nicht direkt; der Name wird
	// deshalb nur geführt, wenn es genau eine benannte Szene gibt. Sonst bleibt
	// er leer — ein geratener Name wäre schlechter als keiner.
	const std::vector<std::string> names = ReadRenderSceneNames ();
	if (names.size () == 1) scene.name = names.front ();
	return scene;
}

std::string ReadCurrent3DStyle ()
{
	GS::Array<GS::UniString> styles;
	GS::UniString current;
	if (ACAPI_View_Get3DStyleList (&styles, &current) != NoError) return {};
	return Utf8 (current);
}

std::vector<std::string> ReadRenderSceneNames ()
{
	GS::Array<GS::UniString> names;
	std::vector<std::string> result;
	if (ACAPI_Rendering_GetRenderingSceneNames (&names) != NoError) return result;
	for (const GS::UniString& name : names) result.push_back (Utf8 (name));
	return result;
}

std::string ProjectDisplayName ()
{
	// **Kein eigenes `delete`.** `API_ProjectInfo` hat einen Destruktor, der
	// `location`, `location_team`, `projectPath` und `projectName` selbst
	// freigibt (`APIdefs_Environment.h`). Ein zusätzliches `delete` ist ein
	// Double-Free und hat Archicad beim Öffnen der Palette abstürzen lassen.
	API_ProjectInfo info;
	if (ACAPI_ProjectOperation_Project (&info) != NoError) return {};
	if (info.projectName == nullptr) return {};
	return Utf8 (*info.projectName);
}

std::string LocalProjectKey ()
{
	// Nur für den lokalen Zustandsspeicher. Er darf an den Dateipfad gebunden
	// sein, weil er das Gerät nie verlässt; das Manifest bekommt ihn nicht.
	// Wie oben: der Destruktor räumt auf, dieser Code nicht.
	API_ProjectInfo info;
	if (ACAPI_ProjectOperation_Project (&info) != NoError) return "archicad:local:unknown";
	if (info.untitled || info.projectPath == nullptr) return "archicad:local:untitled";
	const std::string path = Utf8 (*info.projectPath);
	if (path.empty ()) return "archicad:local:untitled";
	return "archicad:local:" + rtx::Sha256::OfString (path).substr (0, 32);
}

std::string ReadOrCreateProjectKey ()
{
	API_Guid objectGuid = APINULLGuid;
	GSErrCode err = ACAPI_AddOnObject_GetUniqueObjectGuidFromName (
		GS::UniString (kProjectKeyObjectName), &objectGuid);

	if (err == NoError) {
		GS::UniString name;
		GSHandle content = nullptr;
		err = ACAPI_AddOnObject_GetObjectContent (objectGuid, &name, &content);
		std::string stored;
		if (err == NoError && content != nullptr) {
			stored.assign (*content, BMGetHandleSize (content));
			BMKillHandle (&content);
		}
		if (!stored.empty ()) return stored;
	}

	// Noch keiner da: einen anlegen. Das ändert das Archicad-Projekt und
	// verlangt deshalb eine undoable Klammer.
	const std::string key = "archicad:project:" + rtx::NewUuidV7 ();
	GSErrCode created = NoError;
	const GSErrCode outer = ACAPI_CallUndoableCommand ("rendertaxi-Projektschlüssel anlegen", [&] () -> GSErrCode {
		API_Guid guid = APINULLGuid;
		GSErrCode inner = ACAPI_AddOnObject_CreateUniqueObject (GS::UniString (kProjectKeyObjectName), &guid);
		if (inner == APIERR_NAMEALREADYUSED)
			inner = ACAPI_AddOnObject_GetUniqueObjectGuidFromName (
				GS::UniString (kProjectKeyObjectName), &guid);
		if (inner != NoError) { created = inner; return inner; }

		GSHandle content = BMAllocateHandle (static_cast<GSSize> (key.size ()), ALLOCATE_CLEAR, 0);
		if (content == nullptr) { created = APIERR_MEMFULL; return APIERR_MEMFULL; }
		BNCopyMemory (*content, key.data (), static_cast<GSSize> (key.size ()));
		inner = ACAPI_AddOnObject_ModifyObject (guid, nullptr, &content);
		BMKillHandle (&content);
		created = inner;
		return inner;
	});

	if (outer != NoError || created != NoError) return {};
	return key;
}

} // namespace rtxaddon
