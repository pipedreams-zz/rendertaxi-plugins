// Projekt anlegen und Listen neu laden (RTX-A-011, #281).
//
// Der Kerntest des Auftrags: Ein zweiter Versuch nach verlorener Antwort legt
// **kein zweites Projekt** an. Dazu das Wiederfinden der Auswahl nach dem
// Neuladen, die Sperre gegen zu häufiges Neuladen und die Build-Zeile.
#include "Testing.hpp"

#include <cstdlib>
#include <memory>
#include <string>

#include "FakePlatform.hpp"
#include "MockServer.hpp"
#include "rtx/DeviceLogin.hpp"
#include "rtx/Ids.hpp"
#include "rtx/PaletteText.hpp"
#include "rtx/PluginApi.hpp"
#include "rtx/ProjectList.hpp"
#include "rtx/TokenStore.hpp"

using namespace rtx;

namespace {

/** Ein angemeldeter Client gegen den Scheinserver — nur, was diese Tests brauchen. */
struct SignedIn {
	testing::FakePlatform platform;
	testing::MockServer server;
	std::unique_ptr<HttpClient> http;
	std::unique_ptr<PluginApiClient> api;
	std::unique_ptr<TokenStore> tokens;

	SignedIn () : server (platform.Handler ()), http (MakeCurlHttpClient ())
	{
		platform.SetBaseUrl (server.BaseUrl ());
		const char* transcript = std::getenv ("RTX_OPENAPI_TRANSCRIPT");
		if (transcript != nullptr) platform.SetTranscriptPath (transcript);
		api.reset (new PluginApiClient (*http, server.BaseUrl ()));
		tokens = MakeMemoryTokenStore ();

		DeviceIdentity device;
		device.deviceId = LoadOrCreateDeviceId (*tokens, server.BaseUrl ());
		device.hostKey = "archicad";
		device.hostVersion = "28.1";
		device.pluginVersion = "1.0.0";
		device.os = "macos";
		device.architecture = "arm64";
		DeviceLogin login (*api, *tokens, server.BaseUrl ());
		platform.ApproveDevice ();
		const Result<StoredCredential> credential = login.SignIn (device, nullptr, {}, [] (int) {});
		RTX_CHECK (credential.IsOk ());
	}

	std::vector<std::string> ProjectIds ()
	{
		std::vector<std::string> ids;
		const Result<std::vector<ProjectSummary>> projects = api->ListProjects (nullptr);
		RTX_CHECK (projects.IsOk ());
		for (const ProjectSummary& project : projects.Value ()) ids.push_back (project.id);
		return ids;
	}
};

} // namespace

RTX_TEST (ProjektanlageNachVerlorenerAntwortLegtKeinZweitesProjektAn)
{
	SignedIn client;
	NewProjectIntent intent;

	// Erster Versuch: der Server legt an, die Antwort geht verloren.
	client.platform.LoseNextProjectResponse ();
	const std::string key = intent.KeyFor (" Schule Weberberg ");
	RTX_CHECK (IsUuidV7 (key));
	const Result<ProjectSummary> lost =
		client.api->CreateProject (TrimProjectName (" Schule Weberberg "), key, nullptr);
	RTX_CHECK (!lost.IsOk ());
	RTX_CHECK_EQ (client.platform.ProjectsNamed ("Schule Weberberg"), 1);

	// Derselbe Name noch einmal: derselbe Schlüssel, dasselbe Projekt.
	const std::string again = intent.KeyFor ("Schule Weberberg");
	RTX_CHECK_EQ (again, key);
	const Result<ProjectSummary> created =
		client.api->CreateProject ("Schule Weberberg", again, nullptr);
	RTX_CHECK (created.IsOk ());
	RTX_CHECK_EQ (created.Value ().name, std::string ("Schule Weberberg"));
	RTX_CHECK_EQ (client.platform.ProjectsNamed ("Schule Weberberg"), 1);

	// Beide Versuche trugen genau diesen einen Schlüssel.
	const std::vector<std::string> keys = client.platform.ProjectCreateKeys ();
	RTX_CHECK_EQ (keys.size (), std::size_t (2));
	RTX_CHECK (keys[0] == key && keys[1] == key);

	// Das neue Projekt steht in der neu geladenen Liste und wird wiedergefunden.
	const ListReselection chosen = ReselectById (client.ProjectIds (), created.Value ().id);
	RTX_CHECK (chosen.kept);

	// Nach dem Erfolg ist die Absicht erledigt: derselbe Name ist eine neue Anlage.
	intent.Done ("Schule Weberberg");
	RTX_CHECK (intent.KeyFor ("Schule Weberberg") != key);
}

RTX_TEST (EinAndererNameIstEineNeueAbsichtMitNeuemSchluessel)
{
	NewProjectIntent intent;
	const std::string first = intent.KeyFor ("Haus A");
	const std::string second = intent.KeyFor ("Haus B");
	RTX_CHECK (first != second);
	// Zurück zum ersten Namen: auch das ist wieder eine neue Absicht.
	RTX_CHECK (intent.KeyFor ("Haus A") != first);
	// `Done` mit einem anderen Namen erledigt die laufende Absicht nicht.
	const std::string pending = intent.KeyFor ("Haus C");
	intent.Done ("Haus D");
	RTX_CHECK_EQ (intent.KeyFor ("Haus C"), pending);
}

RTX_TEST (DerselbeSchluesselMitAnderemNamenIstEinKonflikt)
{
	SignedIn client;
	const std::string key = NewUuidV7 ();
	RTX_CHECK (client.api->CreateProject ("Haus A", key, nullptr).IsOk ());
	const Result<ProjectSummary> other = client.api->CreateProject ("Haus B", key, nullptr);
	RTX_CHECK (!other.IsOk ());
	RTX_CHECK_EQ (other.GetError ().code, std::string (errc::IdempotencyConflict));
	RTX_CHECK_EQ (client.platform.ProjectsNamed ("Haus B"), 0);
}

RTX_TEST (EineRolleOhneAnlagerechtBekommtEinenSatzStattEinesStatuscodes)
{
	SignedIn client;
	client.platform.SetRole ("viewer");
	const Result<ProjectSummary> denied =
		client.api->CreateProject ("Schule Weberberg", NewUuidV7 (), nullptr);
	RTX_CHECK (!denied.IsOk ());
	RTX_CHECK_EQ (denied.GetError ().code, std::string (errc::Forbidden));
	RTX_CHECK (denied.GetError ().message.find ("Rolle") != std::string::npos);
	RTX_CHECK (denied.GetError ().message.find ("403") == std::string::npos);
	RTX_CHECK_EQ (client.platform.ProjectsNamed ("Schule Weberberg"), 0);
}

RTX_TEST (DerProjektnameWirdVorDemSendenGeprueft)
{
	RTX_CHECK (!ProjectNameProblem ("").empty ());
	RTX_CHECK (!ProjectNameProblem ("   ").empty ());
	RTX_CHECK (ProjectNameProblem ("Schule Weberberg").empty ());
	RTX_CHECK (ProjectNameProblem (std::string (120, 'x')).empty ());
	RTX_CHECK (!ProjectNameProblem (std::string (121, 'x')).empty ());
	// Gezählt werden Zeichen, nicht Bytes: 120 Umlaute sind ein gültiger Name.
	std::string umlauts;
	for (int i = 0; i < 120; ++i) umlauts += "ü";
	RTX_CHECK (ProjectNameProblem (umlauts).empty ());
	RTX_CHECK_EQ (TrimProjectName ("  Büro Nord \t"), std::string ("Büro Nord"));
}

RTX_TEST (EinImWebAngelegtesProjektErscheintNachDemNeuladenUndDieAuswahlBleibt)
{
	SignedIn client;
	const std::string chosen = "0199a000-0000-7000-8000-00000000000a";
	RTX_CHECK (ReselectById (client.ProjectIds (), chosen).kept);

	// Im Web angelegt, während die Palette offen ist: die neue Liste trägt es,
	// und das gewählte Projekt bleibt gewählt — auch an anderer Stelle.
	client.platform.AddProject ("0199a000-0000-7000-8000-0000000000aa", "Aus dem Web");
	const std::vector<std::string> ids = client.ProjectIds ();
	RTX_CHECK_EQ (ids.size (), std::size_t (2));
	const ListReselection kept = ReselectById (ids, chosen);
	RTX_CHECK (kept.kept);
	RTX_CHECK_EQ (ids[kept.index], chosen);
}

RTX_TEST (EinGeloeschtesGemerktesProjektVerhindertDasLadenNicht)
{
	// Regel 3: das gemerkte Projekt ist gelöscht — die Liste lädt trotzdem,
	// und der erste Eintrag ist gewählt.
	SignedIn client;
	client.platform.AddProject ("0199a000-0000-7000-8000-0000000000aa", "Aus dem Web");
	client.platform.RemoveProject ("0199a000-0000-7000-8000-00000000000a");
	const std::vector<std::string> ids = client.ProjectIds ();
	RTX_CHECK_EQ (ids.size (), std::size_t (1));
	const ListReselection fallback = ReselectById (ids, "0199a000-0000-7000-8000-00000000000a");
	RTX_CHECK (!fallback.kept);
	RTX_CHECK_EQ (fallback.index, std::size_t (0));
	// Nichts gemerkt, leere Liste: ebenfalls kein Fehler.
	RTX_CHECK (!ReselectById ({}, "").kept);
}

RTX_TEST (NeuladenBeimFokusHoechstensAlleFuenfSekunden)
{
	using namespace std::chrono;
	RefreshGate gate (seconds (5));
	const steady_clock::time_point start = steady_clock::now ();
	RTX_CHECK (gate.Allow (start));
	RTX_CHECK (!gate.Allow (start + seconds (1)));
	RTX_CHECK (!gate.Allow (start + milliseconds (4999)));
	RTX_CHECK (gate.Allow (start + seconds (5)));
	// Ein Klick auf „Aktualisieren" setzt die Sperre neu.
	gate.Mark (start + seconds (20));
	RTX_CHECK (!gate.Allow (start + seconds (22)));
	RTX_CHECK (gate.Allow (start + seconds (25)));
}

RTX_TEST (LangeNamenEndenAufAuslassungAmEnde)
{
	RTX_CHECK_EQ (PopupLabel ("Testprojekt"), std::string ("Testprojekt"));
	const std::string longName = "Perspektive Süd-West mit Vordach — 3D-Ansichten / Wettbewerb";
	const std::string label = PopupLabel (longName);
	RTX_CHECK (label.size () <= kPopupLabelWidth + std::string ("…").size ());
	// Der Anfang bleibt lesbar, die Auslassung steht am Ende.
	RTX_CHECK_EQ (label.substr (0, 11), std::string ("Perspektive"));
	RTX_CHECK_EQ (label.substr (label.size () - 3), std::string ("…"));
}

RTX_TEST (BuildZeileNenntKurzhashUndDatum)
{
	RTX_CHECK_EQ (FormatCompileDate ("Oct  8 2026"), std::string ("08.10.2026"));
	RTX_CHECK_EQ (FormatCompileDate ("Dec 24 2026"), std::string ("24.12.2026"));
	RTX_CHECK_EQ (FormatCompileDate ("kaputt"), std::string ("kaputt"));
	RTX_CHECK_EQ (BuildLine ("7f9ca06e1d2c3b4a5f60718293a4b5c6d7e8f901", "Oct  8 2026"),
				  std::string ("Build 7f9ca06 vom 08.10.2026"));
	RTX_CHECK_EQ (BuildLine ("7f9ca06-dirty", "Oct  8 2026"),
				  std::string ("Build 7f9ca06 (geändert) vom 08.10.2026"));
	// Fehlt die Kennung oder ist sie kein Hash: ein Entwicklungsbuild.
	RTX_CHECK_EQ (BuildLine ("", "Oct  8 2026"),
				  std::string ("Entwicklungsbuild vom 08.10.2026"));
	RTX_CHECK_EQ (BuildLine ("main", "Oct  8 2026"),
				  std::string ("Entwicklungsbuild vom 08.10.2026"));
}

// --- F-01 an PR #292: das Ziel ist, was sichtbar gewählt ist -------------------

namespace {

ViewpointSummary Viewpoint (const std::string& id, const std::string& name)
{
	ViewpointSummary viewpoint;
	viewpoint.id = id;
	viewpoint.name = name;
	return viewpoint;
}

ProjectSummary Project (const std::string& id, const std::string& name)
{
	ProjectSummary project;
	project.id = id;
	project.name = name;
	return project;
}

constexpr const char* kP = "0199a000-0000-7000-8000-0000000000p1";

} // namespace

RTX_TEST (EinKlickZwischenNeuladenUndAnzeigeAktualisiertDenSichtbarGewaehltenBlickpunkt)
{
	// Sichtbar: [A, B], A an Stelle 1 gewählt, Modus „aktualisieren".
	ShownList<ProjectSummary> projects;
	ShownList<ViewpointSummary> viewpoints;
	projects.Replace ({Project (kP, "Haus")}, {}, "");
	viewpoints.Replace ({Viewpoint ("A", "Nord"), Viewpoint ("B", "Süd")}, kP, "");
	const short shownItem = 1;

	// Der Faden hat die Liste schon ersetzt — umsortiert [B, A] —, die
	// Auswahl ist noch nicht neu aufgebaut. Der Klick in diesem Fenster löst
	// gegen die **dargestellte** Liste auf: A, nicht B.
	const std::vector<ViewpointSummary> loaded {Viewpoint ("B", "Süd"), Viewpoint ("A", "Nord")};
	const ResolvedTarget during = ResolveTarget (projects, 1, viewpoints, shownItem, true);
	RTX_CHECK (during.problem.empty ());
	RTX_CHECK_EQ (during.viewpoint.id, std::string ("A"));

	// Die Anzeige übernimmt die Liste: A bleibt gewählt, jetzt an Stelle 2,
	// und derselbe Klick trifft weiter A.
	const ListChange change = viewpoints.Replace (loaded, kP, during.viewpoint.id);
	RTX_CHECK (change.kept && !change.lost);
	RTX_CHECK_EQ (change.item, short (2));
	RTX_CHECK_EQ (ResolveTarget (projects, 1, viewpoints, change.item, true).viewpoint.id,
				  std::string ("A"));
}

RTX_TEST (EingefuegteUndEntfernteEintraegeAendernDasZielNieStill)
{
	ShownList<ViewpointSummary> viewpoints;
	viewpoints.Replace ({Viewpoint ("A", "Nord"), Viewpoint ("B", "Süd")}, kP, "");

	// Eingefügt vor der Wahl: B bleibt gewählt, die Stelle wandert mit.
	ListChange change =
		viewpoints.Replace ({Viewpoint ("C", "Neu"), Viewpoint ("A", "Nord"), Viewpoint ("B", "Süd")},
							kP, "B");
	RTX_CHECK (change.kept && !change.lost);
	RTX_CHECK_EQ (viewpoints.At (change.item)->id, std::string ("B"));

	// Entfernt: die Wahl ist **verloren**, nicht still ersetzt. Die Palette
	// geht dann auf „Neuer Blickpunkt" zurück, statt C zu aktualisieren.
	change = viewpoints.Replace ({Viewpoint ("C", "Neu"), Viewpoint ("A", "Nord")}, kP, "B");
	RTX_CHECK (!change.kept);
	RTX_CHECK (change.lost);

	// Eine Liste eines **anderen** Projekts verliert nichts — das ist ein
	// Projektwechsel, kein verschwundenes Ziel.
	change = viewpoints.Replace ({Viewpoint ("X", "Fremd")}, "anderes-projekt", "A");
	RTX_CHECK (!change.lost);

	// Eine gemerkte Kennung, die nie dastand, verliert ebenfalls nichts.
	change = viewpoints.Replace ({Viewpoint ("Y", "Zweites")}, "anderes-projekt", "unbekannt");
	RTX_CHECK (!change.lost);
}

RTX_TEST (EineBlickpunktlisteEinesAnderenProjektsIstKeinUpdateZiel)
{
	ShownList<ProjectSummary> projects;
	ShownList<ViewpointSummary> viewpoints;
	projects.Replace ({Project ("P1", "Eins"), Project ("P2", "Zwei")}, {}, "");
	viewpoints.Replace ({Viewpoint ("A", "Nord")}, "P1", "");
	// P2 sichtbar gewählt, die Liste gehört noch zu P1: nichts beginnt.
	const ResolvedTarget target = ResolveTarget (projects, 2, viewpoints, 1, true);
	RTX_CHECK (!target.problem.empty ());
	// Ein neuer Blickpunkt braucht keine Blickpunktliste.
	RTX_CHECK (ResolveTarget (projects, 2, viewpoints, 1, false).problem.empty ());
	RTX_CHECK_EQ (ResolveTarget (projects, 2, viewpoints, 1, false).project.id, std::string ("P2"));
	// Außerhalb der Liste: kein Ziel.
	RTX_CHECK (!ResolveTarget (projects, 3, viewpoints, 1, false).problem.empty ());
	RTX_CHECK (!ResolveTarget ({}, 1, viewpoints, 1, false).problem.empty ());
}

RTX_TEST (EinImWebAngelegtesProjektVerschiebtDieAuswahlNichtAufEinAnderesProjekt)
{
	// Gegen den Scheinserver: sichtbar steht „Testprojekt" an Stelle 1.
	SignedIn client;
	ShownList<ProjectSummary> projects;
	ShownList<ViewpointSummary> viewpoints;
	const Result<std::vector<ProjectSummary>> first = client.api->ListProjects (nullptr);
	RTX_CHECK (first.IsOk ());
	projects.Replace (first.Value (), {}, "");
	const std::string chosen = projects.At (1)->id;

	// Im Web entsteht ein Projekt; die neue Liste stellt es **vor** die Wahl.
	client.platform.AddProject ("0199a000-0000-7000-8000-0000000000aa", "Aus dem Web");
	const Result<std::vector<ProjectSummary>> loaded = client.api->ListProjects (nullptr);
	RTX_CHECK (loaded.IsOk ());
	RTX_CHECK_EQ (loaded.Value ().front ().name, std::string ("Aus dem Web"));

	// Klick vor dem Neuaufbau: Stelle 1 der **dargestellten** Liste.
	RTX_CHECK_EQ (ResolveTarget (projects, 1, viewpoints, 0, false).project.id, chosen);
	// Nach dem Neuaufbau steht die Wahl an Stelle 2 — und ist dasselbe Projekt.
	const ListChange change = projects.Replace (loaded.Value (), {}, chosen);
	RTX_CHECK (change.kept);
	RTX_CHECK_EQ (change.item, short (2));
	RTX_CHECK_EQ (ResolveTarget (projects, change.item, viewpoints, 0, false).project.id, chosen);
}
