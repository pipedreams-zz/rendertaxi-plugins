#include "ViewCapture.hpp"

#include "APIEnvir.h"
#include "ACAPinc.h"

#include <chrono>
#include <cstdio>

#include "HostInfo.hpp"
#include "rtx/ImageCrop.hpp"
#include "rtx/Log.hpp"
#include "rtx/Platform.hpp"
#include "rtx/TransferStore.hpp"

namespace rtxaddon {
namespace {

rtx::Result<ViewCaptureResult> Fail (const std::string& code, const std::string& message)
{
	return rtx::Result<ViewCaptureResult>::Fail (code, message);
}

} // namespace

std::string ExplainArchicadError (long errorCode)
{
	switch (errorCode) {
		case APIERR_REFUSEDCMD:
			// Die Dokumentation von `ACAPI_ProjectOperation_Save` nennt genau
			// diese drei Fälle: Aufruf aus einer Benachrichtigung, ein anderes
			// Add-On ist aktiv, oder Archicad läuft als Demo.
			return "Archicad hat den Bildexport abgelehnt. Das passiert, wenn gerade ein anderer "
				   "Befehl läuft oder Archicad im Demo-Modus gestartet wurde; der Bildexport ist "
				   "in der Demo-Version nicht verfügbar.";
		case APIERR_READONLY:
			return "Das Projekt ist schreibgeschützt geöffnet; Archicad verweigert den Export.";
		case APIERR_NOPLAN:
			return "Es ist kein Projekt geöffnet.";
		case APIERR_BADDATABASE:
			return "Diese Ansicht lässt sich nicht als Bild sichern. Wechsle in das 3D-Fenster "
				   "oder in eine 2D-Ansicht.";
		case APIERR_BADPARS:
			return "Archicad hat die Exportparameter zurückgewiesen.";
		default:
			return "Archicad meldete den Fehler " + std::to_string (errorCode) + ".";
	}
}

namespace {

/** Die gemeinsame Hälfte: Parameter setzen, sichern, prüfen. */
rtx::Result<ViewCaptureResult> SaveCurrentWindowAsPng (const std::string& directory,
													   const std::string& fileName,
													   bool imageWindow)
{
	if (!rtx::EnsureDirectory (directory))
		return Fail (rtx::errc::IoFailed, "Das Arbeitsverzeichnis ließ sich nicht anlegen.");

	ViewCaptureResult result;
	result.filePath = directory + "/" + fileName;
	result.method = "ACAPI_ProjectOperation_Save + API_SavePars_Picture (APIFType_PNGFile)";

	API_FileSavePars savePars = {};
	savePars.fileTypeID = APIFType_PNGFile;
	IO::Location target (GS::UniString (rtx::NativePath (result.filePath).c_str (), CC_UTF8));
	savePars.file = &target;

	API_SavePars_Picture picturePars = {};
	// `API_ColorDepthID` ist plattformabhängig deklariert: die True-Color-Werte
	// `APIColorDepth_TC24`/`_TC32` stehen in `APIdefs_Elements.h` hinter
	// `#ifdef WINDOWS`, macOS kennt stattdessen `APIColorDepth_MiC` (Millionen
	// Farben) und `_MiCP` (Millionen Farben +, mit Alphakanal).
#if defined (macintosh)
	picturePars.colorDepth = APIColorDepth_MiCP;
#else
	picturePars.colorDepth = APIColorDepth_TC32;
#endif
	picturePars.dithered = false;
	// `view2D` heißt „sichere das 2D-Zeichenbild". Ein Bildfenster — 3D wie
	// Rendering — trägt keines.
	picturePars.view2D = !imageWindow;
	picturePars.crop = true;                       // nur für 2D-Ansichten wirksam
	picturePars.keepSelectionHighlight = false;    // Auswahlrahmen gehören nicht ins Basisbild

	const auto started = std::chrono::steady_clock::now ();
	const GSErrCode err = ACAPI_ProjectOperation_Save (&savePars, &picturePars);
	const auto finished = std::chrono::steady_clock::now ();
	result.milliseconds =
		std::chrono::duration_cast<std::chrono::milliseconds> (finished - started).count ();

	if (err != NoError) {
		rtx::LogLine ("Bildexport fehlgeschlagen, Code " + std::to_string (err));
		return Fail (rtx::errc::IoFailed, ExplainArchicadError (err));
	}
	if (rtx::FileSize (result.filePath) <= 0)
		return Fail (rtx::errc::IoFailed,
					 "Archicad meldete Erfolg, hat aber keine Bilddatei geschrieben.");

	rtx::LogLine ("Bildexport erfolgreich (" + std::to_string (result.milliseconds) + " ms).");
	return rtx::Result<ViewCaptureResult>::Ok (result);
}

} // namespace

rtx::Result<ViewCaptureResult> CaptureCurrentViewAsPng (const std::string& directory,
														const std::string& fileName)
{
	const SourceView view = ReadCurrentView ();
	if (!view.capturable)
		return Fail (rtx::errc::IoFailed,
					 "Aus diesem Fenster lässt sich kein Bild erzeugen. Wechsle in das "
					 "3D-Fenster oder in eine 2D-Ansicht.");

	// Die Größe des 3D-Fensters bestimmt die Auflösung des gesicherten Bildes
	// (belegt in `capabilities.md`, Abschnitt 2.3). Sie wird **gelesen und
	// nicht gesetzt**.
	//
	// Der Versuch, sie für ein Zielformat zu setzen, ist am 21.09.2026 an
	// Archicad 28 gescheitert und wurde zurückgenommen:
	//
	// 1. `ACAPI_View_Change3DWindowSets` mit `setWindowSize` **entdockt das
	//    3D-Fenster** aus der Registerleiste. Das Zurückstellen der Größe
	//    dockt es nicht wieder an; das Fenster bleibt aus dem Layout
	//    herausgelöst. Eine Aufnahme darf die Arbeitsumgebung nicht
	//    beschädigen.
	// 2. Eine breitere Fenstergröße erweitert das **Blickfeld** nach rechts,
	//    statt oben und unten zu beschneiden. Das Ergebnis hat zwar das
	//    Zielverhältnis, zeigt aber mehr Szene als die Ansicht, aus der es
	//    stammt — genau die stille Abweichung, die vermieden werden soll.
	//
	// Der Zuschnitt auf das Ausgabeziel gehört deshalb nicht hierher. Siehe
	// `capabilities.md`, Abschnitt 2.3.
	int windowWidth = 0;
	int windowHeight = 0;
	if (view.is3D) {
		API_3DWindowInfo windowInfo = {};
		if (ACAPI_View_Get3DWindowSets (&windowInfo) == NoError) {
			windowWidth = windowInfo.hSize;
			windowHeight = windowInfo.vSize;
		}
	}

	const rtx::Result<ViewCaptureResult> saved =
		SaveCurrentWindowAsPng (directory, fileName, view.is3D || view.isRendering);
	if (!saved) return saved;
	ViewCaptureResult result = saved.Value ();
	result.windowWidth = windowWidth;
	result.windowHeight = windowHeight;
	return rtx::Result<ViewCaptureResult>::Ok (result);
}

rtx::Result<ViewCaptureResult> RenderCurrentViewAsPng (const std::string& directory,
													   const std::string& fileName)
{
	if (!rtx::EnsureDirectory (directory))
		return Fail (rtx::errc::IoFailed, "Das Arbeitsverzeichnis ließ sich nicht anlegen.");

	const SourceView view = ReadCurrentView ();
	if (!view.is3D)
		return Fail (rtx::errc::IoFailed,
					 "Gerendert wird aus dem 3D-Fenster. Bitte dorthin wechseln.");

	// Die Maße stehen in den Photorealistik-Einstellungen; sie werden gelesen
	// und **nicht** gesetzt. Was hier steht, ist exakt das, was Archicad
	// rechnen wird — und was die Palette vorher anzeigt.
	const RenderScene scene = ReadCurrentRenderScene ();

	ViewCaptureResult result;
	result.filePath = directory + "/" + fileName;
	result.method = "ACAPI_Rendering_PhotoRender (TIFF) + ImageIO (PNG)";
	result.windowWidth = scene.width;
	result.windowHeight = scene.height;

	// PhotoRender kennt kein PNG. TIFF ist verlustfrei; die Umwandlung danach
	// ist es auch.
	const std::string tiffPath = directory + "/render.tiff";
	rtx::RemoveFile (tiffPath);

	API_PhotoRenderPars renderPars = {};
	renderPars.fileTypeID = APIFType_TIFFFile;
	IO::Location target (GS::UniString (rtx::NativePath (tiffPath).c_str (), CC_UTF8));
	renderPars.file = &target;
#if defined (macintosh)
	renderPars.colorDepth = APIColorDepth_MiCP;
#else
	renderPars.colorDepth = APIColorDepth_TC32;
#endif
	renderPars.dithered = false;

	const auto started = std::chrono::steady_clock::now ();
	const GSErrCode err = ACAPI_Rendering_PhotoRender (&renderPars);
	const auto finished = std::chrono::steady_clock::now ();
	result.milliseconds =
		std::chrono::duration_cast<std::chrono::milliseconds> (finished - started).count ();

	if (err != NoError) {
		rtx::LogLine ("Rendern fehlgeschlagen, Code " + std::to_string (err));
		return Fail (rtx::errc::IoFailed, ExplainArchicadError (err));
	}
	if (rtx::FileSize (tiffPath) <= 0)
		return Fail (rtx::errc::IoFailed,
					 "Archicad meldete Erfolg, hat aber kein gerendertes Bild geschrieben.");

	const rtx::Status converted = rtx::ConvertImageToPng (tiffPath, result.filePath);
	rtx::RemoveFile (tiffPath);
	if (!converted) return Fail (converted.GetError ().code, converted.GetError ().message);

	rtx::LogLine ("Gerendert und umgeschrieben (" + std::to_string (result.milliseconds) + " ms).");
	return rtx::Result<ViewCaptureResult>::Ok (result);
}

} // namespace rtxaddon
