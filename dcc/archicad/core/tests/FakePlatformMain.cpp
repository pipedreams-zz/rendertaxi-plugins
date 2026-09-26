// Der Scheinserver als eigenständiges Programm.
//
// Er ist dasselbe Stück Code, gegen das die automatisierten Prüfungen laufen —
// nur mit Browserseiten und ohne Testlauf drumherum. Damit lässt sich der
// **gesamte** Weg aus Archicad 28 heraus vorführen, obwohl die Serverhälfte
// (#18, #19, #125, #126) noch nicht ausgeliefert ist: Gerätelogin im Browser,
// Projekt- und Blickpunktauswahl, Übernahme, „Im Browser öffnen".
//
// Er ist ausdrücklich **keine** Nachbildung der Plattform. Er setzt die
// Annahmen aus `docs/plugin-api-client.md` um, sonst nichts. Wer ihn für eine
// Referenz hält, verwechselt eine Attrappe mit einem Vertrag.
#include <atomic>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <thread>

#include "FakePlatform.hpp"
#include "MockServer.hpp"

namespace {

std::atomic<bool> running {true};

void OnSignal (int)
{
	running.store (false);
}

} // namespace

int main (int argc, char** argv)
{
	bool autoApprove = false;
	int port = 8787;
	for (int i = 1; i < argc; ++i) {
		const std::string argument = argv[i];
		if (argument == "--auto-approve") autoApprove = true;
		if (argument == "--port" && i + 1 < argc) port = std::atoi (argv[++i]);
		if (argument == "--help" || argument == "-h") {
			std::printf (
				"Scheinserver der Plugin API v1 für das rendertaxi.ai-Add-on.\n\n"
				"  --port <n>       Port (Vorgabe 8787; belegt: freier Port)\n"
				"  --auto-approve   Gerätecodes ohne Browserseite bestätigen\n\n"
				"Er hält keine Daten über das Programmende hinaus.\n");
			return 0;
		}
	}

	testing::FakePlatform platform;
	platform.SetAutoApprove (autoApprove);
	// Auch ein Lauf mit echtem Archicad wird mitgeschrieben, damit er sich
	// hinterher gegen die OpenAPI-Momentaufnahme halten lässt:
	//   node tools/check-openapi.mjs <pfad>
	if (const char* transcript = std::getenv ("RTX_OPENAPI_TRANSCRIPT")) {
		platform.SetTranscriptPath (transcript);
		std::printf ("Verkehr wird nach %s mitgeschrieben.\n", transcript);
	}
	testing::MockServer server (platform.Handler (), port);
	platform.SetBaseUrl (server.BaseUrl ());

	std::signal (SIGINT, OnSignal);
	std::signal (SIGTERM, OnSignal);

	std::printf ("Scheinserver läuft auf %s\n", server.BaseUrl ().c_str ());
	std::printf ("Diese Adresse in das Feld „Server\" der rendertaxi.ai-Palette eintragen.\n");
	if (!autoApprove)
		std::printf ("Die Anmeldung wird im Browser unter %s/geraet bestätigt.\n",
					 server.BaseUrl ().c_str ());
	std::printf ("Beenden mit Strg-C.\n\n");
	std::fflush (stdout);

	int lastCount = 0;
	while (running.load ()) {
		std::this_thread::sleep_for (std::chrono::milliseconds (250));
		const int count = server.RequestCount ();
		if (count != lastCount) {
			lastCount = count;
			std::printf ("Aufrufe: %d · Assets: %d · Blickpunkte: %d · Basisbildfassungen: %d\n",
						 count, platform.AssetsCreated (), platform.ViewpointsCreated (),
						 platform.BaseImageVersions ());
			if (!platform.LastOpenUrl ().empty ())
				std::printf ("  zuletzt: %s\n", platform.LastOpenUrl ().c_str ());
			std::fflush (stdout);
		}
	}
	std::printf ("\nBeendet.\n");
	return 0;
}
