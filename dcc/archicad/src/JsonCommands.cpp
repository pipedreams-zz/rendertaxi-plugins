#include "JsonCommands.hpp"

#include <string>

#include "ObjectState.hpp"

#include "CapabilityProbe.hpp"
#include "HostInfo.hpp"
#include "Version.hpp"
#include "ViewCapture.hpp"
#include "rtx/ImageCrop.hpp"
#include "rtx/ImageFile.hpp"
#include "rtx/Json.hpp"
#include "rtx/Sha256.hpp"
#include "rtx/TransferStore.hpp"

#ifdef RTX_SPIKE_MODEL_GLB
#include "../spike/model-glb/ModelGlbSpike.hpp"
#endif

namespace rtxaddon {
namespace {

constexpr const char* kCommandNamespace = "rendertaxi";

std::string Utf8 (const GS::UniString& text)
{
	return std::string (text.ToCStr (0, MaxUSize, CC_UTF8).Get ());
}

/** Liest ein Zeichenkettenfeld aus den Parametern eines JSON-Befehls. */
std::string Param (const GS::ObjectState& parameters, const char* key)
{
	GS::UniString value;
	if (!parameters.Get (key, value)) return {};
	return Utf8 (value);
}

int IntParam (const GS::ObjectState& parameters, const char* key)
{
	Int32 value = 0;
	if (!parameters.Get (key, value)) return 0;
	return static_cast<int> (value);
}

void Fail (GS::ObjectState& response, const std::string& message)
{
	response.Add ("succeeded", false);
	response.Add ("error", GS::UniString (message.c_str (), CC_UTF8));
}

/**
 * `rendertaxi.CaptureView` — nimmt die aktuelle Ansicht auf und beschreibt sie.
 *
 * Parameter (alle optional):
 *   `directory`     Zielverzeichnis; ohne Angabe ein Ordner im Arbeitsbereich
 *   `aspectWidth`   Zielverhältnis, Zähler
 *   `aspectHeight`  Zielverhältnis, Nenner
 *   `window`        `3d` oder `floorplan`; ohne Angabe das aktuelle Fenster
 *   `useRenderScene` schneidet auf das Verhältnis der aktuellen Rendering-Szene
 *                   zu — also auf genau den Schutzbereich, den Archicad zeigt
 *
 * Antwort: Pfad, Maße, Medientyp, SHA-256, Dauer, und ob zugeschnitten wurde.
 */
class CaptureViewCommand : public API_AddOnCommand {
public:
	GS::String GetNamespace () const override { return kCommandNamespace; }
	GS::String GetName () const override { return "CaptureView"; }
	API_AddOnCommandExecutionPolicy GetExecutionPolicy () const override
	{
		// Der Bildexport ist eine vollständige Operation; er gehört in die
		// Hauptschleife und nicht in den Aufrufkontext des JSON-Servers.
		return API_AddOnCommandExecutionPolicy::ScheduleForExecutionOnMainThread;
	}
	bool IsProcessWindowVisible () const override { return false; }
	GS::Optional<GS::UniString> GetSchemaDefinitions () const override { return GS::NoValue; }
	GS::Optional<GS::UniString> GetInputParametersSchema () const override
	{
		return R"json({
			"type": "object",
			"properties": {
				"directory":    { "type": "string" },
				"aspectWidth":  { "type": "integer" },
				"aspectHeight": { "type": "integer" },
				"window":       { "type": "string", "enum": ["3d", "floorplan"] },
				"useRenderScene": { "type": "boolean" }
			},
			"additionalProperties": false
		})json";
	}
	GS::Optional<GS::UniString> GetResponseSchema () const override { return GS::NoValue; }
	void OnResponseValidationFailed (const GS::ObjectState&) const override {}

	GS::ObjectState Execute (const GS::ObjectState& parameters,
							 GS::ProcessControl&) const override
	{
		GS::ObjectState response;

		std::string directory = Param (parameters, "directory");
		if (directory.empty ())
			directory = rtx::TransferStore::DefaultWorkDirectory () + "/json-capture";
		if (!rtx::EnsureDirectory (directory)) {
			Fail (response, "Verzeichnis ließ sich nicht anlegen: " + directory);
			return response;
		}

		// Optionaler Fensterwechsel. Er ist eine Prüfhilfe: ohne ihn misst der
		// Befehl nur, was gerade vorn ist, und der 3D- und der 2D-Weg lassen
		// sich nicht aus einem Skript heraus vergleichen.
		const std::string window = Param (parameters, "window");
		if (!window.empty ()) {
			API_WindowInfo target = {};
			target.typeID = window == "3d" ? APIWind_3DModelID : APIWind_FloorPlanID;
			const GSErrCode switched = ACAPI_Window_ChangeWindow (&target);
			if (switched != NoError) {
				Fail (response, "Fensterwechsel nach " + window + " fehlgeschlagen: " +
									ExplainArchicadError (switched));
				return response;
			}
		}

		const SourceView view = ReadCurrentView ();
		const rtx::Result<ViewCaptureResult> captured =
			CaptureCurrentViewAsPng (directory, "viewport.png");
		if (!captured) {
			Fail (response, captured.GetError ().message);
			return response;
		}

		std::string path = captured.Value ().filePath;
		bool cropped = false;
		int aspectWidth = IntParam (parameters, "aspectWidth");
		int aspectHeight = IntParam (parameters, "aspectHeight");

		const RenderScene scene = ReadCurrentRenderScene ();
		bool useScene = false;
		parameters.Get ("useRenderScene", useScene);
		if (useScene && scene.known) {
			aspectWidth = scene.width;
			aspectHeight = scene.height;
		}
		if (aspectWidth > 0 && aspectHeight > 0) {
			const std::string target = directory + "/viewport-cropped.png";
			const rtx::Result<rtx::CropResult> crop =
				rtx::CropImageToAspect (path, target, aspectWidth, aspectHeight);
			if (!crop) {
				Fail (response, crop.GetError ().message);
				return response;
			}
			if (crop.Value ().cropped) {
				path = target;
				cropped = true;
			}
		}

		const rtx::Result<rtx::ImageInfo> image = rtx::ReadImageInfo (path);
		if (!image) {
			Fail (response, image.GetError ().message);
			return response;
		}
		bool hashed = false;
		const std::string sha = rtx::Sha256OfFile (path, &hashed);

		response.Add ("succeeded", true);
		response.Add ("path", GS::UniString (path.c_str (), CC_UTF8));
		response.Add ("width", static_cast<Int32> (image.Value ().width));
		response.Add ("height", static_cast<Int32> (image.Value ().height));
		response.Add ("mediaType", GS::UniString (image.Value ().mediaType.c_str (), CC_UTF8));
		response.Add ("channels", GS::UniString (image.Value ().channels.c_str (), CC_UTF8));
		response.Add ("bitDepth", static_cast<Int32> (image.Value ().bitDepth));
		response.Add ("byteSize", static_cast<Int32> (rtx::FileSize (path)));
		response.Add ("sha256", GS::UniString (hashed ? sha.c_str () : "", CC_UTF8));
		response.Add ("cropped", cropped);
		response.Add ("exportMilliseconds",
					  static_cast<Int32> (captured.Value ().milliseconds));
		response.Add ("windowWidth", static_cast<Int32> (captured.Value ().windowWidth));
		response.Add ("windowHeight", static_cast<Int32> (captured.Value ().windowHeight));
		response.Add ("windowKind", GS::UniString (view.windowKind.c_str (), CC_UTF8));
		response.Add ("sourceViewKey", GS::UniString (view.key.c_str (), CC_UTF8));
		response.Add ("renderSceneWidth", static_cast<Int32> (scene.width));
		response.Add ("renderSceneHeight", static_cast<Int32> (scene.height));
		return response;
	}
};

/** `rendertaxi.Info` — Host, Add-On und aktuelle Ansicht, ohne Nebenwirkung. */
class InfoCommand : public API_AddOnCommand {
public:
	GS::String GetNamespace () const override { return kCommandNamespace; }
	GS::String GetName () const override { return "Info"; }
	API_AddOnCommandExecutionPolicy GetExecutionPolicy () const override
	{
		return API_AddOnCommandExecutionPolicy::ScheduleForExecutionOnMainThread;
	}
	bool IsProcessWindowVisible () const override { return false; }
	GS::Optional<GS::UniString> GetSchemaDefinitions () const override { return GS::NoValue; }
	GS::Optional<GS::UniString> GetInputParametersSchema () const override { return GS::NoValue; }
	GS::Optional<GS::UniString> GetResponseSchema () const override { return GS::NoValue; }
	void OnResponseValidationFailed (const GS::ObjectState&) const override {}

	GS::ObjectState Execute (const GS::ObjectState&, GS::ProcessControl&) const override
	{
		const HostVersion host = ReadHostVersion ();
		const MachineInfo machine = ReadMachineInfo ();
		const SourceView view = ReadCurrentView ();

		GS::ObjectState response;
		response.Add ("succeeded", true);
		response.Add ("addonVersion", GS::UniString (RTX_ADDON_VERSION, CC_UTF8));
		response.Add ("hostVersion", GS::UniString (host.version.c_str (), CC_UTF8));
		response.Add ("hostBuild", GS::UniString (host.build.c_str (), CC_UTF8));
		response.Add ("architecture", GS::UniString (machine.architecture.c_str (), CC_UTF8));
		response.Add ("windowKind", GS::UniString (view.windowKind.c_str (), CC_UTF8));
		response.Add ("sourceViewKey", GS::UniString (view.key.c_str (), CC_UTF8));
		response.Add ("capturable", view.capturable);
		response.Add ("projectName", GS::UniString (ProjectDisplayName ().c_str (), CC_UTF8));

		const RenderScene scene = ReadCurrentRenderScene ();
		response.Add ("renderSceneKnown", scene.known);
		response.Add ("renderSceneName", GS::UniString (scene.name.c_str (), CC_UTF8));
		response.Add ("renderSceneWidth", static_cast<Int32> (scene.width));
		response.Add ("renderSceneHeight", static_cast<Int32> (scene.height));

		GS::ObjectState scenes;
		for (const std::string& name : ReadRenderSceneNames ())
			scenes.Add (name.c_str (), true);
		response.Add ("renderScenes", scenes);
		return response;
	}
};

/** `rendertaxi.Probe` — die Messung aus Stufe B, als Befehl. */
class ProbeCommand : public API_AddOnCommand {
public:
	GS::String GetNamespace () const override { return kCommandNamespace; }
	GS::String GetName () const override { return "Probe"; }
	API_AddOnCommandExecutionPolicy GetExecutionPolicy () const override
	{
		return API_AddOnCommandExecutionPolicy::ScheduleForExecutionOnMainThread;
	}
	bool IsProcessWindowVisible () const override { return false; }
	GS::Optional<GS::UniString> GetSchemaDefinitions () const override { return GS::NoValue; }
	GS::Optional<GS::UniString> GetInputParametersSchema () const override { return GS::NoValue; }
	GS::Optional<GS::UniString> GetResponseSchema () const override { return GS::NoValue; }
	void OnResponseValidationFailed (const GS::ObjectState&) const override {}

	GS::ObjectState Execute (const GS::ObjectState&, GS::ProcessControl&) const override
	{
		GS::ObjectState response;
		const std::string report = RunCapabilityProbe ();
		if (report.empty ()) {
			Fail (response, "Die Messung ließ sich nicht schreiben.");
			return response;
		}
		response.Add ("succeeded", true);
		response.Add ("report", GS::UniString (report.c_str (), CC_UTF8));
		return response;
	}
};

} // namespace

GSErrCode InstallJsonCommands ()
{
	GSErrCode err = ACAPI_AddOnAddOnCommunication_InstallAddOnCommandHandler (
		GS::NewOwned<CaptureViewCommand> ());
	if (err != NoError) return err;
	err = ACAPI_AddOnAddOnCommunication_InstallAddOnCommandHandler (GS::NewOwned<InfoCommand> ());
	if (err != NoError) return err;
#ifdef RTX_SPIKE_MODEL_GLB
	// Messauftrag #256, nicht im veröffentlichten Add-on.
	err = spike::InstallModelGlbSpikeCommands ();
	if (err != NoError) return err;
#endif
	return ACAPI_AddOnAddOnCommunication_InstallAddOnCommandHandler (GS::NewOwned<ProbeCommand> ());
}

} // namespace rtxaddon
