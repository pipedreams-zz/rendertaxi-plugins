#include "CapabilityProbe.hpp"

#include "APIEnvir.h"
#include "ACAPinc.h"

#include <chrono>
#include <sstream>

#include "HostInfo.hpp"
#include "ViewCapture.hpp"
#include "rtx/Ids.hpp"
#include "rtx/ImageFile.hpp"
#include "rtx/TransferStore.hpp"

namespace rtxaddon {
namespace {

struct Measurement {
	bool ok = false;
	int width = 0;
	int height = 0;
	long long bytes = 0;
	long long milliseconds = 0;
	std::string note;
};

Measurement MeasurePngSave (const std::string& directory, const std::string& name)
{
	Measurement measurement;
	const rtx::Result<ViewCaptureResult> captured = CaptureCurrentViewAsPng (directory, name);
	if (!captured) {
		measurement.note = captured.GetError ().message;
		return measurement;
	}
	measurement.milliseconds = captured.Value ().milliseconds;
	measurement.bytes = rtx::FileSize (captured.Value ().filePath);
	const rtx::Result<rtx::ImageInfo> info = rtx::ReadImageInfo (captured.Value ().filePath);
	if (info) {
		measurement.width = info.Value ().width;
		measurement.height = info.Value ().height;
		measurement.note = "PNG, " + std::to_string (info.Value ().bitDepth) + " Bit, " +
						   info.Value ().channels;
	}
	measurement.ok = true;
	return measurement;
}

Measurement MeasurePhotoRender (const std::string& directory)
{
	Measurement measurement;
	// `ACAPI_Rendering_PhotoRender` nennt in der Dokumentation ausdrücklich
	// `APIFType_PictFile`, `BMPFile`, `TIFFFile`, `JPEGFile` und `GIFFile` —
	// kein PNG. Gemessen wird deshalb JPEG.
	const std::string path = directory + "/photorender.jpg";
	IO::Location target (GS::UniString (path.c_str (), CC_UTF8));

	API_PhotoRenderPars pars = {};
	pars.fileTypeID = APIFType_JPEGFile;
	pars.file = &target;
#if defined (macintosh)
	pars.colorDepth = APIColorDepth_MiC;
#else
	pars.colorDepth = APIColorDepth_TC24;
#endif
	pars.dithered = false;

	const auto started = std::chrono::steady_clock::now ();
	const GSErrCode err = ACAPI_Rendering_PhotoRender (&pars, nullptr);
	const auto finished = std::chrono::steady_clock::now ();
	measurement.milliseconds =
		std::chrono::duration_cast<std::chrono::milliseconds> (finished - started).count ();
	if (err != NoError) {
		measurement.note = "Fehler " + std::to_string (err) + ": " + ExplainArchicadError (err);
		return measurement;
	}
	measurement.bytes = rtx::FileSize (path);
	const rtx::Result<rtx::ImageInfo> info = rtx::ReadImageInfo (path);
	if (info) {
		measurement.width = info.Value ().width;
		measurement.height = info.Value ().height;
		measurement.note = "JPEG, " + std::to_string (info.Value ().bitDepth) + " Bit, " +
						   info.Value ().channels;
	}
	measurement.ok = true;
	return measurement;
}

void WriteRow (std::ostringstream& out, const std::string& label, const Measurement& measurement)
{
	out << "| " << label << " | " << (measurement.ok ? "ja" : "nein") << " | ";
	if (measurement.ok)
		out << measurement.width << " x " << measurement.height;
	else
		out << "—";
	out << " | " << measurement.milliseconds << " ms | " << measurement.bytes << " | "
		<< measurement.note << " |\n";
}

} // namespace

std::string RunCapabilityProbe ()
{
	const std::string directory =
		rtx::TransferStore::DefaultWorkDirectory () + "/capability-" + rtx::RandomHex (4);
	if (!rtx::EnsureDirectory (directory)) return {};

	const HostVersion host = ReadHostVersion ();
	const MachineInfo machine = ReadMachineInfo ();
	const SourceView view = ReadCurrentView ();

	std::ostringstream out;
	out << "# Bildzugriff in Archicad " << host.version << " (Build " << host.build << ")\n\n";
	out << "Erzeugt vom rendertaxi-Add-on, Menü „Bildzugriff messen“.\n\n";
	out << "- Zeitpunkt: " << rtx::NowTimestampUtc () << "\n";
	out << "- Maschine: " << machine.os << " " << machine.osVersion << ", "
		<< machine.architecture << "\n";
	out << "- Quellansicht: " << (view.displayName.empty () ? "(ohne Namen)" : view.displayName)
		<< " (" << view.windowKind << ")\n";
	out << "- Stabiler Schlüssel der Quellansicht: "
		<< (view.key.empty () ? "keiner (Manifest traegt null)" : view.key) << "\n";

	API_3DWindowInfo before = {};   // nur gelesen, nie gesetzt
	const bool have3DSets = view.is3D && ACAPI_View_Get3DWindowSets (&before) == NoError;
	if (have3DSets)
		out << "- 3D-Fenstergröße laut `ACAPI_View_Get3DWindowSets`: " << before.hSize << " x "
			<< before.vSize << " Pixel (die exportierte Bilddatei kann ein Vielfaches davon "
			<< "haben — siehe Tabelle)\n";
	const RenderScene currentScene = ReadCurrentRenderScene ();
	if (currentScene.known)
		out << "- Rendering-Szene (Schutzbereich): " << currentScene.width << " x "
			<< currentScene.height << " Pixel\n";
	out << "\n";

	out << "| Weg | erfolgreich | Auflösung | Dauer | Bytes | Anmerkung |\n";
	out << "| --- | --- | --- | --- | --- | --- |\n";

	// Dreimal derselbe Weg: Reproduzierbarkeit.
	for (int attempt = 1; attempt <= 3; ++attempt) {
		const Measurement measurement =
			MeasurePngSave (directory, "save-" + std::to_string (attempt) + ".png");
		WriteRow (out, "ProjectOperation_Save PNG, Lauf " + std::to_string (attempt), measurement);
	}

	// **Kein Fensterwechsel mehr.** Die Messung hat am 20.09.2026 einmal
	// `ACAPI_View_Change3DWindowSets` benutzt, um die Abhängigkeit der
	// Auflösung von der Fenstergröße zu belegen. Der Befund steht in
	// `capabilities.md`, Abschnitt 2.3 — und mit ihm der Grund, warum das hier
	// nicht wiederholt wird: der Aufruf **entdockt das 3D-Fenster** aus der
	// Registerleiste und dockt es nicht zurück. Eine Messung darf die
	// Arbeitsumgebung nicht beschädigen.
	const RenderScene scene = ReadCurrentRenderScene ();
	if (scene.known) {
		const Measurement measurement = MeasurePngSave (directory, "save-scene-crop.png");
		WriteRow (out,
				  "ProjectOperation_Save PNG, danach Zuschnitt auf die Szene " +
					  std::to_string (scene.width) + " x " + std::to_string (scene.height),
				  measurement);
	}

	if (view.is3D) {
		const Measurement measurement = MeasurePhotoRender (directory);
		WriteRow (out, "Rendering_PhotoRender JPEG", measurement);
	} else {
		out << "| Rendering_PhotoRender | nein | — | — | — | nur aus dem 3D-Fenster |\n";
	}

	out << "\nDateien dieser Messung: `" << directory << "`\n";

	const std::string reportPath = directory + "/capability-report.md";
	if (!rtx::WriteTextFile (reportPath, out.str ())) return {};
	return reportPath;
}

} // namespace rtxaddon
