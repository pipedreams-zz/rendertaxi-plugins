#include "RendertaxiPalette.hpp"

#include <chrono>
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>

#if defined (WINDOWS)
#include <shellapi.h>
#include <vector>
#endif

#include "HostInfo.hpp"
#include "Settings.hpp"
#include "Version.hpp"
#include "ViewCapture.hpp"
#include "rtx/CaptureManifest.hpp"
#include "rtx/Ids.hpp"
#include "rtx/ImageCrop.hpp"
#include "rtx/ImageFile.hpp"
#include "rtx/Log.hpp"
#include "rtx/PaletteText.hpp"
#include "rtx/Platform.hpp"
#include "rtx/Sha256.hpp"

namespace rtxaddon {
namespace {

const GS::Guid paletteGuid ("{4B1D9F2E-6A07-4C55-9E3D-8F2A71C40D96}");

GS::UniString U (const std::string& text)
{
	return GS::UniString (text.c_str (), CC_UTF8);
}

std::string Utf8 (const GS::UniString& text)
{
	return std::string (text.ToCStr (0, MaxUSize, CC_UTF8).Get ());
}

/**
 * Öffnet eine Adresse im Systembrowser. Das DevKit kennt dafür keinen Aufruf.
 * Die Adresse wird **nicht** in eine Shell gegeben, sondern als Argument an
 * `/usr/bin/open` übergeben: so kann kein Zeichen darin einen Befehl werden.
 */
void OpenInSystemBrowser (const std::string& url)
{
	if (url.rfind ("https://", 0) != 0 && url.rfind ("http://", 0) != 0) return;
#if defined (WINDOWS)
	// Unter Windows dasselbe ohne Shell: `ShellExecuteW` übergibt die Adresse
	// dem registrierten Browser als Ganzes.
	const int length = MultiByteToWideChar (CP_UTF8, 0, url.c_str (), -1, nullptr, 0);
	if (length <= 0) return;
	std::vector<WCHAR> wide (static_cast<std::size_t> (length));
	MultiByteToWideChar (CP_UTF8, 0, url.c_str (), -1, wide.data (), length);
	ShellExecuteW (nullptr, L"open", wide.data (), nullptr, nullptr, SW_SHOWNORMAL);
#else
	const pid_t child = fork ();
	if (child == 0) {
		execl ("/usr/bin/open", "open", url.c_str (), static_cast<char*> (nullptr));
		_exit (127);
	}
#endif
}

GSErrCode NotificationHandler (API_NotifyEventID notifID, Int32)
{
	if (notifID == APINotify_Quit) RendertaxiPalette::DestroyInstance ();
	else RendertaxiPalette::NoteProjectChanged ();
	return NoError;
}

} // namespace

void SharedState::Set (const std::string& connection, const std::string& progress)
{
	std::lock_guard<std::mutex> guard (mutex);
	if (!connection.empty ()) connectionText = connection;
	progressText = progress;
	dirty = true;
}

void SharedState::SetProgress (const std::string& progress)
{
	std::lock_guard<std::mutex> guard (mutex);
	progressText = progress;
	dirty = true;
}

void SharedState::SetResult (const std::string& result, const std::string& url)
{
	std::lock_guard<std::mutex> guard (mutex);
	resultText = result;
	openUrl = url;
	dirty = true;
}

GS::Ref<RendertaxiPalette> RendertaxiPalette::instance;
std::atomic<bool> RendertaxiPalette::projectChanged {true};

void RendertaxiPalette::NoteProjectChanged ()
{
	projectChanged.store (true);
}

RendertaxiPalette::RendertaxiPalette () :
	DG::Palette (ACAPI_GetOwnResModule (), RtxPaletteResId, ACAPI_GetOwnResModule (), paletteGuid),
	connectionGroup (GetReference (), ConnectionGroupId),
	serverLabel (GetReference (), ServerLabelId),
	serverEdit (GetReference (), ServerEditId),
	connectionStatus (GetReference (), ConnectionStatusId),
	signInButton (GetReference (), SignInButtonId),
	signOutButton (GetReference (), SignOutButtonId),
	targetGroup (GetReference (), TargetGroupId),
	projectLabel (GetReference (), ProjectLabelId),
	projectPopUp (GetReference (), ProjectPopUpId),
	createRadio (GetReference (), CreateRadioId),
	nameLabel (GetReference (), NameLabelId),
	nameEdit (GetReference (), NameEditId),
	updateRadio (GetReference (), UpdateRadioId),
	viewpointLabel (GetReference (), ViewpointLabelId),
	viewpointPopUp (GetReference (), ViewpointPopUpId),
	sizeLabel (GetReference (), SizeLabelId),
	sizePopUp (GetReference (), SizePopUpId),
	fitFrameCheck (GetReference (), FitFrameCheckId),
	captureGroup (GetReference (), CaptureGroupId),
	sourceViewText (GetReference (), SourceViewTextId),
	captureFormatText (GetReference (), CaptureFormatTextId),
	targetFormatText (GetReference (), TargetFormatTextId),
	matchText (GetReference (), MatchTextId),
	memoryText (GetReference (), MemoryTextId),
	infoLine5 (GetReference (), InfoLine5Id),
	infoLine6 (GetReference (), InfoLine6Id),
	infoLine7 (GetReference (), InfoLine7Id),
	infoLine8 (GetReference (), InfoLine8Id),
	progressText2 (GetReference (), ProgressText2Id),
	resultText2 (GetReference (), ResultText2Id),
	resultText3 (GetReference (), ResultText3Id),
	progressText3 (GetReference (), ProgressText3Id),
	captureButton (GetReference (), CaptureButtonId),
	cancelButton (GetReference (), CancelButtonId),
	discardButton (GetReference (), DiscardButtonId),
	renderingButton (GetReference (), RenderingButtonId),
	progressText (GetReference (), ProgressTextId),
	resultGroup (GetReference (), ResultGroupId),
	resultText (GetReference (), ResultTextId),
	openButton (GetReference (), OpenButtonId)
{
	rtx::SetLogFile (LogPath ());

	http = rtx::MakeCurlHttpClient ();
	tokens = rtx::MakeSystemTokenStore ();
	store.reset (new rtx::TransferStore (rtx::TransferStore::DefaultPath ()));
	store->Load ();
	serverUrl = ServerUrl ();
	RebuildApi ();

	serverEdit.SetText (U (serverUrl));
	// Die zwei Werte aus §7.2, in der Reihenfolge des Vertrags: die Vorgabe
	// zuerst. Was hier steht, wird **nicht** übersetzt geraten — `capture`
	// geht nur dorthin, wo es auch wirkt.
	sizePopUp.AppendItem ();
	sizePopUp.SetItemText (1, U ("Canvas-Vorgabe (Standard)"));
	sizePopUp.AppendItem ();
	sizePopUp.SetItemText (2, U ("Render-Einstellung übernehmen"));
	sizePopUp.SelectItem (FrameSize () == "capture" ? 2 : 1);
	createRadio.Select ();
	cancelButton.Disable ();
	openButton.Disable ();

	// Projektwechsel machen Projektname und Projektschlüssel ungültig. Ohne
	// diese Benachrichtigung müsste der Leerlauf beides bei jedem Tick neu
	// erfragen — ein ACAPI-Aufruf je Mausbewegung.
	ACAPI_ProjectOperation_CatchProjectEvent (
		APINotify_New | APINotify_NewAndReset | APINotify_Open | APINotify_Close |
			APINotify_Save | APINotify_Quit,
		NotificationHandler);
	Attach (*this);
	signInButton.Attach (*this);
	signOutButton.Attach (*this);
	captureButton.Attach (*this);
	cancelButton.Attach (*this);
	discardButton.Attach (*this);
	renderingButton.Attach (*this);
	// Ohne diesen Beobachter merkte das Add-on nicht, dass jemand den Namen
	// selbst geschrieben hat — und überschriebe ihn beim nächsten
	// Ansichtswechsel.
	nameEdit.Attach (*this);
	openButton.Attach (*this);
	BeginEventProcessing ();
	EnableIdleEvent ();

	// Ein hinterlegtes Token bedeutet: wir waren angemeldet. Ob es noch gilt,
	// entscheidet erst der nächste Aufruf — ein Widerruf wirkt serverseitig.
	if (tokens != nullptr) {
		// Die Gerätekennung entsteht **einmal** je Installation und liegt neben
		// dem Token im Schlüsselspeicher (`plugin-api-v1.md`, Abschnitt 5.5).
		deviceId = rtx::LoadOrCreateDeviceId (*tokens, serverUrl);
		rtx::DeviceLogin login (*api, *tokens, serverUrl);
		const rtx::Result<rtx::StoredCredential> stored = login.Restore ();
		if (stored) {
			shared.signedIn = true;
			std::string line = "Angemeldet als " + stored.Value ().displayName;
			if (!stored.Value ().organizationName.empty ())
				line += " (" + stored.Value ().organizationName + ")";
			shared.Set (line, {});
		} else {
			// Festlegung 4 aus Issue #20: kein Passwortfeld im Plugin. Die
			// Anmeldung läuft über den Gerätefluss im Systembrowser; das
			// Add-On sieht nie Zugangsdaten, nur ein widerrufbares Token.
			shared.Set ("Nicht angemeldet.",
						"Die Anmeldung läuft im Browser — das Plugin speichert kein Passwort.");
		}
	} else {
		shared.Set ("Keine Keychain verfügbar — Anmeldung nicht möglich.", {});
	}
	RefreshSourceView ();
	RefreshFromState ();
}

RendertaxiPalette::~RendertaxiPalette ()
{
	cancel.Cancel ();
	JoinWorker ();
	EndEventProcessing ();
}

void RendertaxiPalette::RebuildApi ()
{
	api.reset (new rtx::PluginApiClient (*http, serverUrl));
}

void RendertaxiPalette::JoinWorker ()
{
	if (worker.joinable ()) worker.join ();
	workerRunning.store (false);
}

bool RendertaxiPalette::HasInstance ()
{
	return instance != nullptr;
}

RendertaxiPalette& RendertaxiPalette::GetInstance ()
{
	return *instance;
}

void RendertaxiPalette::EnsureShown ()
{
	if (instance == nullptr) instance = new RendertaxiPalette ();
	instance->Show ();
}

void RendertaxiPalette::DestroyInstance ()
{
	instance = nullptr;
}

void RendertaxiPalette::Show ()
{
	DG::Palette::Show ();
	ACAPI_KeepInMemory (true);
}

void RendertaxiPalette::Hide ()
{
	DG::Palette::Hide ();
}

void RendertaxiPalette::PanelCloseRequested (const DG::PanelCloseRequestEvent&, bool* accepted)
{
	*accepted = true;
	Hide ();
}

// --- Anzeige ----------------------------------------------------------------

/**
 * Vorschlag für den Namen eines neuen Blickpunkts.
 *
 * Archicad 28 füllt `API_WindowInfo::title` für das 3D- und das
 * Grundrissfenster nicht (gemessen; `open-questions.md`, Q-13). Der Vorschlag
 * entsteht deshalb aus Projektname und Fensterart. Er ist ausdrücklich nur ein
 * Vorschlag: der Nutzer kann ihn überschreiben, und er trägt keine Identität.
 */
std::string RendertaxiPalette::SuggestViewpointName (const SourceView& view) const
{
	// **Der Ausschnitt zuerst.** Was in der Ausschnittsmappe steht, ist der
	// Name, den der Architekt selbst vergeben hat; bis zum 24.09.2026 stand im
	// Vorschlag stattdessen die Fensterart („Grundriss"), weil Archicad den
	// offenen Ausschnitt nicht von sich aus nennt (siehe `HostInfo.hpp`).
	if (!view.viewMapName.empty ()) return view.viewMapName;
	if (!view.displayName.empty ()) return view.displayName;
	if (cachedProjectName.empty ()) return view.windowLabel;
	return cachedProjectName + " — " + view.windowLabel;
}

/** Holt Projektname und Projektschlüssel, aber nur wenn sie veraltet sind. */
void RendertaxiPalette::RefreshProjectCache ()
{
	if (projectCacheValid && !projectChanged.exchange (false)) return;
	projectChanged.store (false);
	cachedProjectName = ProjectDisplayName ();
	cachedLocalProjectKey = LocalProjectKey ();
	projectCacheValid = true;
	// Ein anderes Projekt heißt: anderer Vorschlag, andere gemerkte Zuordnung.
	lastSourceViewKey.clear ();
}

/** Ausgabeziel des in der Auswahl stehenden Blickpunkts; leer, wenn keiner. */
/**
 * Die Geräteangaben, die `auth/device` und der Handshake verlangen (§5.1).
 * Sie stammen aus Archicad und der Maschine — **kein** Rechner- oder
 * Benutzername, auch nicht als `displayName`.
 */
rtx::DeviceIdentity RendertaxiPalette::CurrentDevice () const
{
	const HostVersion host = ReadHostVersion ();
	const MachineInfo machine = ReadMachineInfo ();
	rtx::DeviceIdentity device;
	device.deviceId = deviceId;
	device.hostKey = "archicad";
	device.hostVersion = host.version;
	device.pluginVersion = RTX_ADDON_VERSION;
	device.os = machine.os;
	device.architecture = machine.architecture;
	return device;
}

rtx::DesiredOutput RendertaxiPalette::SelectedViewpointFormat () const
{
	const short index = viewpointPopUp.GetSelectedItem ();
	if (index < 1) return {};
	std::lock_guard<std::mutex> guard (shared.mutex);
	const std::size_t position = static_cast<std::size_t> (index - 1);
	if (position >= shared.viewpoints.size ()) return {};
	return shared.viewpoints[position].desired;
}

/**
 * Kürzt einen Text auf eine Länge, die in eine Palettenzeile passt.
 *
 * Die Zeilen sind 388 Punkte breit; darüber schneidet DG ab, und der Rest ist
 * nur noch im Tooltip zu sehen — genau der Befund vom 24.09.2026. Lieber ein
 * sichtbares „…" als eine Angabe, die es nur beim Überfahren gibt. Kürzen und
 * Umbrechen stehen im Kern (`rtx/PaletteText.hpp`), damit sie prüfbar sind.
 */
static std::string Shorten (const std::string& text, std::size_t maxChars)
{
	return rtx::ShortenText (text, maxChars);
}

/**
 * Ein Seitenverhältnis als gekürztes Paar — „1920 x 1080" wird „16:9".
 *
 * Gekürzt wird über den größten gemeinsamen Teiler. Bleibt danach eine Zahl
 * über 99 stehen (etwa 1001:750), ist das Paar keine Hilfe mehr; dann steht
 * dort die Dezimalzahl. **Es wird nichts gerundet, um hübscher auszusehen:**
 * ein angezeigtes 16:9, das in Wahrheit 1,777 nicht trifft, wäre genau die
 * stille Abweichung, um die es hier geht.
 */
static std::string AspectLabel (int width, int height)
{
	if (width <= 0 || height <= 0) return {};
	int a = width;
	int b = height;
	while (b != 0) {
		const int rest = a % b;
		a = b;
		b = rest;
	}
	const int shortWidth = width / a;
	const int shortHeight = height / a;
	if (shortWidth <= 99 && shortHeight <= 99)
		return std::to_string (shortWidth) + ":" + std::to_string (shortHeight);
	char buffer[32];
	std::snprintf (buffer, sizeof buffer, "%.3f",
				   static_cast<double> (width) / static_cast<double> (height));
	return std::string (buffer) + ":1";
}

void RendertaxiPalette::RefreshSourceView ()
{
	RefreshProjectCache ();
	const SourceView view = ReadCurrentView ();
	const std::string viewKey = view.key.empty () ? "archicad:view:unknown" : view.key;
	std::string text;
	if (!view.capturable) {
		text = "Quellansicht: " + view.windowLabel + " liefert kein Bild.";
	} else {
		text = "Quellansicht: " + SuggestViewpointName (view);
	}

	// Die zuletzt bestätigte Zuordnung wird **vorgeschlagen**, nie still
	// angewandt (Festlegung 5 des Auftrags): der Blickpunkt wird in der
	// Auswahl vorgemerkt, aber „Bestehenden aktualisieren" bleibt eine eigene,
	// ausdrückliche Handlung des Nutzers.
	// Die zuletzt bestätigte Zuordnung wird **vorgeschlagen**, nie still
	// angewandt (Festlegung 5 des Auftrags): der Blickpunkt wird in der
	// Auswahl vorgemerkt, aber „Bestehenden aktualisieren" bleibt eine eigene,
	// ausdrückliche Handlung des Nutzers.
	const rtx::LastAssignment remembered = store->FindAssignment (cachedLocalProjectKey, viewKey);
	std::string memory;
	if (!remembered.viewpointId.empty ())
		memory = "Aus dieser Ansicht zuletzt nach „" +
				 Shorten (remembered.viewpointName, 34) + "“";

	// Ein angefangener Vorgang ist keine Nebensache: die nächste Übernahme
	// setzt ihn fort und nimmt **nicht** neu auf. Das steht hier, bevor jemand
	// auf den Knopf drückt, und der Weg heraus steht als Knopf daneben.
	// Die Schlüssel kommen aus der **schon gelesenen** Ansicht; ein zweiter
	// ACAPI-Aufruf je Leerlauf wäre für dieselbe Auskunft zu viel.
	rtx::PendingTransfer open;
	for (const std::string& key : SourceKeysFor (view)) {
		const rtx::PendingTransfer found = store->FindPending (cachedLocalProjectKey, key);
		if (!found.IsEmpty ()) {
			open = found;
			break;
		}
	}
	const bool hasPending = !open.IsEmpty ();
	// Ein eigener Absatz, der immer ganz dasteht (F-02 an PR #152). Ohne
	// lokale Bild- oder Manifestdatei ist der Vorgang nicht fortsetzbar; die
	// nächste Übernahme verwirft ihn, bevor sie neu aufnimmt (Punkt 16).
	const std::string pendingLine =
		hasPending ? rtx::PendingText (open.createdAt, rtx::HasLocalMaterial (open))
				   : std::string ();
	if (hasPending != discardEnabled) {
		discardEnabled = hasPending;
		discardButton.SetStatus (hasPending);
	}

	// --- Was aufgenommen wird ------------------------------------------------
	//
	// Der Ausschnitt kommt aus der **Rendering-Szene**: sie ist die einzige
	// Stelle, an der ein Archicad-Nutzer ihn sieht, bevor er die Ansicht
	// festlegt — als „Render-Schutzbereich" im 3D-Fenster.
	//
	// Diese Angaben standen bis zum 24.09.2026 mit allem anderen in **einer**
	// Zeile. Die war zu lang für die Palette und nur noch im Tooltip lesbar —
	// für einen Abgleich **vor** der Übernahme wertlos. Jetzt hat jede Aussage
	// ihre eigene Zeile.
	const RenderScene scene = ReadCurrentRenderScene ();
	const std::string sceneSize =
		scene.known ? std::to_string (scene.width) + " x " + std::to_string (scene.height) +
						  " · " + AspectLabel (scene.width, scene.height)
					: std::string ();
	// **Zwei Wege, zwei Bilder.** „Aktuelle Ansicht übernehmen" sichert das
	// Fenster: es zeigt den 3D-Darstellungsmodus, den der Nutzer unter
	// „Ansicht ▸ 3D-Darstellungsmodus" gewählt hat, aber nur in der
	// Bildschirmauflösung des Fensters. „Rendern und übernehmen" rechnet die
	// eingestellte Größe, dafür mit der Maschine aus den
	// Photorealistik-Einstellungen. Beides steht hier, weil beide Knöpfe
	// darunter liegen und die Wahl sonst geraten wäre.
	const std::string style = view.is3D ? ReadCurrent3DStyle () : std::string ();
	const std::string sceneAspect =
		scene.known ? AspectLabel (scene.width, scene.height) : std::string ();
	std::string capture;
	std::string renderLine;
	if (!view.capturable) {
		capture = "Aufnahme: —";
	} else {
		// **Genau sagen, was geschieht.** Der Fensterweg liefert die
		// Bildschirmauflösung des Fensters und schneidet auf das **Format**
		// der Szene — er vergrößert nichts. Ist das Fenster kleiner als die
		// eingestellte Rendergröße, ist das Bild eben kleiner; die Zahl aus
		// den Photorealistik-Einstellungen gilt nur für den Renderweg.
		// Knapp halten: acht Zeilen hat die Palette, und darunter stehen noch
		// Zielrahmen, Abgleich, Zielgröße und die gemerkte Zuordnung.
		capture = "Ansicht: Fenstergröße";
		if (!style.empty ()) capture += ", „" + Shorten (style, 24) + "“";
		capture += scene.known ? ", Zuschnitt auf " + sceneAspect : ", ohne Zuschnitt";
		if (view.is3D) {
			renderLine = "Rendern: " +
						 (scene.known ? sceneSize : std::string ("Szenengröße")) +
						 " (Photorealistik-Einstellungen)";
		}
	}

	// --- Was den Blickpunkt erwartet, und ob beides zusammenpasst ------------
	//
	// **Kein Hinweis „Ausgabeziel weicht ab" mehr.** Die Formatentscheidung vom
	// 23.09.2026 klärt beide Richtungen: ein **neuer** Blickpunkt übernimmt
	// serverseitig das Seitenverhältnis der Bilddatei, und ein **Update**
	// behält Rahmen und Ausschnitt, bis der Nutzer ausdrücklich „Rahmen an
	// Aufnahme anpassen" wählt. Eine Abweichung ist damit keine Warnung,
	// sondern eine Wahl — und die steht als Häkchen daneben.
	// **Die Rahmengröße gehört in die Anzeige**, nicht nur in die Einstellung:
	// sie entscheidet über die Zielgröße des Blickpunkts, und sie zählt zum
	// Vorgangsschlüssel. Was gilt, hängt vom Modus ab — bei `update` wirkt sie
	// nur zusammen mit „Rahmen an Aufnahme anpassen" (§7.2).
	const bool sizeFromCapture = sizePopUp.GetSelectedItem () == 2;
	const bool frameFollowsCapture =
		!updateRadio.IsSelected () || fitFrameCheck.IsChecked ();
	const std::string sizeLine =
		rtx::TargetSizeText (sizeFromCapture, frameFollowsCapture, scene.known ? sceneSize : "");

	std::string targetLine;
	std::string matchLine;
	if (updateRadio.IsSelected ()) {
		const rtx::DesiredOutput desired = SelectedViewpointFormat ();
		if (!desired.known) {
			targetLine = "Zielrahmen: noch kein Ausgabeziel gespeichert";
			matchLine = "Abgleich: der Blickpunkt übernimmt das Format der Aufnahme";
		} else {
			targetLine = "Zielrahmen: " + Shorten (desired.label, 40);
			// ADR 0024: das bisherige Basisbild verliert seine Rolle und
			// **bleibt als Referenz stehen**. Das gehört gesagt, bevor jemand
			// ein Update auslöst.
			if (desired.exactWidth > 0 && desired.exactHeight > 0)
				targetLine += " · " + AspectLabel (desired.exactWidth, desired.exactHeight);
			else if (desired.aspectWidth > 0 && desired.aspectHeight > 0)
				targetLine += " · " + AspectLabel (desired.aspectWidth, desired.aspectHeight);

			if (!scene.known) {
				matchLine = "Abgleich: ohne Rendering-Szene nicht feststellbar";
			} else {
				const double sceneRatio =
					static_cast<double> (scene.width) / static_cast<double> (scene.height);
				const double abweichung =
					std::fabs (sceneRatio - desired.Ratio ()) / desired.Ratio ();
				if (abweichung <= 0.005) {
					matchLine = "Abgleich: Format stimmt überein";
				} else if (fitFrameCheck.IsChecked ()) {
					matchLine = "Abgleich: Format weicht ab (" +
								AspectLabel (scene.width, scene.height) + " statt " +
								AspectLabel (desired.exactWidth > 0 ? desired.exactWidth
																	: desired.aspectWidth,
											 desired.exactHeight > 0 ? desired.exactHeight
																	 : desired.aspectHeight) +
								") — Rahmen folgt der Aufnahme";
				} else {
					matchLine = "Abgleich: Format weicht ab (" +
								AspectLabel (scene.width, scene.height) + " statt " +
								AspectLabel (desired.exactWidth > 0 ? desired.exactWidth
																	: desired.aspectWidth,
											 desired.exactHeight > 0 ? desired.exactHeight
																	 : desired.aspectHeight) +
								") — Rahmen bleibt";
				}
			}
		}
	} else {
		targetLine = "Zielrahmen: entsteht neu";
		matchLine = "Abgleich: der neue Blickpunkt übernimmt das Format der Aufnahme";
	}
	if (updateRadio.IsSelected ())
		matchLine += "  ·  Das bisherige Basisbild bleibt als Referenz erhalten.";

	// **Der Namensvorschlag geht mit.**
	//
	// Er wurde bis zum 24.09.2026 nur gesetzt, wenn das Feld leer war. Wer im
	// Grundriss anfing und ins 3D wechselte, behielt deshalb „EG" als Namen
	// für eine 3D-Ansicht. Jetzt gilt: solange im Feld **der vorherige
	// Vorschlag** steht, wird er durch den neuen ersetzt. Ein selbst
	// getippter Name bleibt unangetastet — das Add-on überschreibt nie, was
	// jemand geschrieben hat.
	// **Ein angefangener Vorgang stellt sein eigenes Ziel wieder her.**
	//
	// Nach einem Absturz stand in der Auswahl wieder das erste Projekt der
	// Liste — der offene Vorgang gehörte aber zu einem anderen. Die
	// Wiederholung lief damit gegen ein fremdes Ziel und endete mit
	// `idempotency_conflict`: richtig gerechnet und trotzdem eine Sackgasse,
	// denn Projekt, Modus und Blickpunkt stehen im gemerkten Vorgang
	// (gemessen am 25.09.2026).
	//
	// Wiederhergestellt wird **einmal je Vorgang**: danach hat der Nutzer
	// wieder das Sagen, auch wenn er woandershin will.
	if (hasPending && restoredPendingKey != open.idempotencyKey) {
		std::vector<rtx::ProjectSummary> known;
		{
			std::lock_guard<std::mutex> guard (shared.mutex);
			known = shared.projects;
		}
		for (std::size_t i = 0; i < known.size (); ++i) {
			if (known[i].id != open.targetProjectId) continue;
			projectPopUp.SelectItem (static_cast<short> (i + 1));
			restoredPendingKey = open.idempotencyKey;
			if (open.targetMode == "update") {
				updateRadio.Select ();
				proposedViewpointId = open.targetViewpointId;
				proposedViewpointPending = !proposedViewpointId.empty ();
			} else {
				createRadio.Select ();
			}
			// Der Rahmen gehört zum Ziel und damit zur Wiederherstellung.
			fitFrameCheck.SetState (open.targetFrame == "fit-to-capture");
			sizePopUp.SelectItem (open.targetSize == "capture" ? 2 : 1);
			break;
		}
	}

	// **Ein angefangener Vorgang schlägt seinen eigenen Namen vor.**
	//
	// Nach einem Absturz mitten in einer Anlage steht der Name des Ziels im
	// gemerkten Vorgang — im Namensfeld aber wieder der Vorschlag aus der
	// Ansicht. Wer dann nicht zufällig dasselbe tippt, bekommt zu Recht
	// `idempotency_conflict`: anderes Ziel, anderer Vorgang. Das ist richtig
	// gerechnet und trotzdem eine Zumutung, denn die Angabe liegt vor.
	const std::string suggestion =
		hasPending && open.targetMode == "create" && !open.targetViewpointName.empty ()
			? open.targetViewpointName
			: (view.capturable ? SuggestViewpointName (view) : std::string ());
	if (!suggestion.empty () && suggestion != shownSuggestion) {
		// Der Vorschlag folgt der Ansicht, **solange niemand selbst getippt
		// hat**. Wer einen eigenen Namen schreibt, behält ihn; das Add-on
		// überschreibt nichts, was jemand geschrieben hat. Erkannt wird das
		// über den Beobachter am Namensfeld, nicht über einen Vergleich mit
		// dem letzten Vorschlag — der ging schief, sobald Archicad das Feld
		// selbst anfasste.
		if (!nameEditedByUser) {
			nameEdit.SetText (U (suggestion));
			// Die eigene Änderung ist keine Eingabe des Nutzers.
			nameEditedByUser = false;
		}
		shownSuggestion = suggestion;
	}

	if (viewKey != lastSourceViewKey) {
		lastSourceViewKey = viewKey;
		proposedViewpointId = remembered.viewpointId;
		proposedViewpointPending = !proposedViewpointId.empty ();
	}

	if (proposedViewpointPending) {
		std::lock_guard<std::mutex> guard (shared.mutex);
		for (std::size_t i = 0; i < shared.viewpoints.size (); ++i) {
			if (shared.viewpoints[i].id != proposedViewpointId) continue;
			if (viewpointPopUp.GetItemCount () >= static_cast<short> (i + 1))
				viewpointPopUp.SelectItem (static_cast<short> (i + 1));
			proposedViewpointPending = false;
			break;
		}
	}

	std::lock_guard<std::mutex> guard (shared.mutex);
	const auto put = [this] (std::string& slot, const std::string& value) {
		if (slot == value) return;
		slot = value;
		shared.dirty = true;
	};
	// Breiter als `kPaletteLineWidth` schneidet DG ab. Deshalb wird **umgebrochen**.
	put (shared.sourceViewText, Shorten (text, rtx::kPaletteLineWidth));

	// Jede Angabe ist ein eigener Absatz; der Renderweg etwa beginnt eine
	// **eigene** Zeile: zwei Verfahren, zwei Auflösungen, zwei Absätze.
	//
	// **Kein Absatz fällt weg, und Zielgröße und offener Vorgang stehen immer
	// ganz da** (Issue #88, Punkt 17; F-02 an PR #152). Bis zum 25.09.2026
	// wurde nach dem Umbruch auf sechs Zeilen gekürzt, danach ein Absatz, der
	// nicht mehr passte, ganz ausgelassen — beides konnte die Zielgröße
	// verschlucken. Jetzt verlieren bei Platzmangel die übrigen Absätze
	// Zeilen und enden auf „…"; die Regel und ihr ungünstigster Fall stehen
	// im Kern und sind dort geprüft.
	const std::vector<std::string> lines = rtx::LayoutInfoLines (
		{{capture, false},
		 {renderLine, false},
		 {targetLine, false},
		 {matchLine, false},
		 {sizeLine, true},
		 {memory, false},
		 {pendingLine, true}},
		rtx::kPaletteLineWidth, rtx::kPaletteInfoRows);
	if (shared.infoLines != lines) {
		shared.infoLines = lines;
		shared.dirty = true;
	}
}

void RendertaxiPalette::RefreshFromState ()
{
	std::string connection;
	std::string progress;
	std::string result;
	std::string source;
	std::vector<std::string> infoLines;
	bool resetNameSuggestion = false;
	std::string url;
	std::string browser;
	bool busy = false;
	bool signedIn = false;
	bool projectsChanged = false;
	bool viewpointsChanged = false;
	std::vector<rtx::ProjectSummary> projects;
	std::vector<rtx::ViewpointSummary> viewpoints;
	{
		std::lock_guard<std::mutex> guard (shared.mutex);
		if (!shared.dirty) return;
		shared.dirty = false;
		connection = shared.connectionText;
		progress = shared.progressText;
		result = shared.resultText;
		source = shared.sourceViewText;
		infoLines = shared.infoLines;
		resetNameSuggestion = shared.resetNameSuggestion;
		shared.resetNameSuggestion = false;
		url = shared.openUrl;
		busy = shared.busy;
		signedIn = shared.signedIn;
		browser = shared.browserToOpen;
		shared.browserToOpen.clear ();
		projectsChanged = shared.projectsChanged;
		viewpointsChanged = shared.viewpointsChanged;
		shared.projectsChanged = false;
		shared.viewpointsChanged = false;
		projects = shared.projects;
		viewpoints = shared.viewpoints;
	}

	if (connection != shownConnection) {
		connectionStatus.SetText (U (connection));
		shownConnection = connection;
	}
	// Umgebrochen statt abgeschnitten, über so viele Zeilen, wie der Bereich
	// hat: die letzte nimmt notfalls den Rest, aber „…" ist hier die Ausnahme
	// und nicht der Normalfall.
	const auto putLines = [] (const std::vector<DG::LeftText*>& slots, const std::string& text) {
		const std::vector<std::string> lines = rtx::WrapText (text, rtx::kPaletteLineWidth);
		for (std::size_t i = 0; i < slots.size (); ++i) {
			if (i + 1 < slots.size ()) {
				slots[i]->SetText (U (i < lines.size () ? lines[i] : std::string ()));
				continue;
			}
			// Letzter Platz: alles, was noch übrig ist.
			std::string rest;
			for (std::size_t j = i; j < lines.size (); ++j) {
				if (!rest.empty ()) rest += " ";
				rest += lines[j];
			}
			slots[i]->SetText (U (Shorten (rest, 66)));
		}
	};
	if (progress != shownProgress) {
		putLines ({&progressText, &progressText2, &progressText3}, progress);
		shownProgress = progress;
	}
	if (result != shownResult) {
		putLines ({&resultText, &resultText2, &resultText3}, result);
		shownResult = result;
	}
	if (source != shownSourceView) {
		sourceViewText.SetText (U (source));
		shownSourceView = source;
	}
	if (resetNameSuggestion) {
		nameEditedByUser = false;
		shownSuggestion.clear ();
	}
	if (infoLines != shownInfoLines) {
		DG::LeftText* const slots[] = {&captureFormatText, &targetFormatText, &matchText,
									   &memoryText,        &infoLine5,       &infoLine6,
									   &infoLine7,         &infoLine8};
		static_assert (sizeof slots / sizeof slots[0] == rtx::kPaletteInfoRows,
					   "Jede Infozeile braucht ihr Dialogelement.");
		for (std::size_t i = 0; i < rtx::kPaletteInfoRows; ++i) {
			const std::string value = i < infoLines.size () ? infoLines[i] : std::string ();
			slots[i]->SetText (U (value));
		}
		shownInfoLines = infoLines;
	}

	if (projectsChanged) {
		while (projectPopUp.GetItemCount () > 0) projectPopUp.DeleteItem (1);
		for (const rtx::ProjectSummary& project : projects) {
			projectPopUp.AppendItem ();
			projectPopUp.SetItemText (projectPopUp.GetItemCount (), U (project.name));
		}
		if (projectPopUp.GetItemCount () > 0) projectPopUp.SelectItem (1);
	}
	if (viewpointsChanged) {
		while (viewpointPopUp.GetItemCount () > 0) viewpointPopUp.DeleteItem (1);
		for (const rtx::ViewpointSummary& viewpoint : viewpoints) {
			viewpointPopUp.AppendItem ();
			viewpointPopUp.SetItemText (viewpointPopUp.GetItemCount (), U (viewpoint.name));
		}
		if (viewpointPopUp.GetItemCount () > 0) viewpointPopUp.SelectItem (1);
	}

	if (busy != shownBusy || signedIn != shownSignedIn) {
		shownBusy = busy;
		shownSignedIn = signedIn;
		signInButton.SetStatus (!busy && !signedIn);
		signOutButton.SetStatus (!busy && signedIn);
		captureButton.SetStatus (!busy && signedIn);
		renderingButton.SetStatus (!busy && signedIn);
		cancelButton.SetStatus (busy);
		// Während ein Faden läuft, wird nichts verworfen; danach entscheidet
		// wieder der Leerlauf, ob überhaupt etwas offen steht.
		if (busy) {
			discardEnabled = false;
			discardButton.SetStatus (false);
		}
		projectPopUp.SetStatus (!busy && signedIn);
		viewpointPopUp.SetStatus (!busy && signedIn);

		createRadio.SetStatus (!busy && signedIn);
		updateRadio.SetStatus (!busy && signedIn);
		nameEdit.SetStatus (!busy && signedIn);
		sizePopUp.SetStatus (!busy && signedIn);
		serverEdit.SetStatus (!busy && !signedIn);
	}
	openButton.SetStatus (!url.empty ());
	// Nur beim Update gibt es einen Rahmen, der angepasst werden könnte. Das
	// folgt dem Moduswechsel und nicht nur dem Anmeldezustand.
	const bool fitEnabled = !busy && signedIn && updateRadio.IsSelected ();
	if (fitEnabled != shownFitEnabled) {
		shownFitEnabled = fitEnabled;
		fitFrameCheck.SetStatus (fitEnabled);
		if (!fitEnabled) fitFrameCheck.SetState (false);
	}

	if (!browser.empty ()) OpenInSystemBrowser (browser);
}

void RendertaxiPalette::PanelIdle (const DG::PanelIdleEvent&)
{
	if (!workerRunning.load () && worker.joinable ()) JoinWorker ();

	// Das Leerlaufereignis kommt sehr oft. Die Anzeige aus dem gemeinsamen
	// Zustand zu schreiben ist billig — ein ACAPI-Aufruf je Mausbewegung wäre
	// es nicht. Die Quellansicht wird deshalb höchstens zweimal je Sekunde
	// erfragt.
	const auto now = std::chrono::steady_clock::now ();
	if (now - lastSourceViewCheck > std::chrono::milliseconds (500)) {
		lastSourceViewCheck = now;
		RefreshSourceView ();
	}
	RefreshProjectList ();
	RefreshViewpointList ();
	RefreshFromState ();
}

// --- Aktionen ---------------------------------------------------------------

void RendertaxiPalette::TextEditChanged (const DG::TextEditChangeEvent& ev)
{
	// Nur das Namensfeld, und nur die Eingabe eines Menschen: `SetText` aus dem
	// Leerlauf löst dieses Ereignis nicht aus.
	if (ev.GetSource () == &nameEdit) nameEditedByUser = true;
}

void RendertaxiPalette::ButtonClicked (const DG::ButtonClickEvent& ev)
{
	if (ev.GetSource () == &discardButton) StartDiscard ();
	if (ev.GetSource () == &renderingButton) StartCapture (CaptureSource::Rendering);
	if (ev.GetSource () == &signInButton) StartSignIn ();
	else if (ev.GetSource () == &signOutButton) StartSignOut ();
	else if (ev.GetSource () == &captureButton) StartCapture ();
	else if (ev.GetSource () == &cancelButton) CancelRunningJob ();
	else if (ev.GetSource () == &openButton) OpenResultInBrowser ();
}

void RendertaxiPalette::CancelRunningJob ()
{
	cancel.Cancel ();
	shared.SetProgress ("Abbruch angefordert…");
}

void RendertaxiPalette::OpenResultInBrowser ()
{
	std::lock_guard<std::mutex> guard (shared.mutex);
	if (!shared.openUrl.empty ()) {
		shared.browserToOpen = shared.openUrl;
		shared.dirty = true;
	}
}

void RendertaxiPalette::StartSignIn ()
{
	if (workerRunning.load ()) return;
	if (tokens == nullptr) {
		shared.Set ("Keine Keychain verfügbar — Anmeldung nicht möglich.", {});
		return;
	}

	const std::string typedUrl = Utf8 (serverEdit.GetText ());
	if (!SetServerUrl (typedUrl)) {
		shared.Set ("Die Serveradresse muss mit https:// oder http:// beginnen.", {});
		return;
	}
	// **F-01:** Ein Anmeldetoken verlässt den Prozess nur über `https://`.
	// Einzige Ausnahme ist die Schleife für den Scheinserver. Die Prüfung steht
	// hier, damit der Nutzer den Grund sieht, bevor ein Gerätelogin beginnt —
	// und nicht erst, wenn der erste angemeldete Aufruf abgelehnt wird.
	if (!rtx::PluginApiClient::IsTokenSafeBaseUrl (ServerUrl ())) {
		shared.Set ("Nicht angemeldet.",
					"Über eine unverschlüsselte Verbindung wird kein Anmeldetoken gesendet. "
					"Verwende https://; http:// gilt nur für 127.0.0.1 und localhost.");
		return;
	}
	serverUrl = ServerUrl ();
	RebuildApi ();

	cancel.Reset ();
	{
		std::lock_guard<std::mutex> guard (shared.mutex);
		shared.busy = true;
		shared.dirty = true;
	}
	shared.Set ("Anmeldung wird vorbereitet…", {});

	workerRunning.store (true);
	worker = std::thread ([this] () {
		// Erst fragen, ob der Server die Plugin API v1 überhaupt spricht. Ohne
		// diesen Schritt bekäme der Nutzer für einen fehlenden Endpunkt
		// dieselbe Meldung wie für ein falsches Kennwort.
		// **F-02:** Kein Zweig hier beendet den Prozess. Jeder Sonderfall ist
		// ein Fehlerwert und endet in einer Meldung in der Palette.
		const rtx::Result<rtx::HandshakeInfo> handshake =
			api->Handshake (CurrentDevice (), &cancel);
		if (!handshake) {
			shared.Set ("Nicht angemeldet.",
						handshake.GetError ().code == rtx::errc::EndpointMissing
							? "Dieser Server bietet die Plugin API v1 nicht an."
							: "Der Handshake ist fehlgeschlagen: " +
								  handshake.GetError ().message);
			std::lock_guard<std::mutex> guard (shared.mutex);
			shared.busy = false;
			shared.signedIn = false;
			shared.dirty = true;
			workerRunning.store (false);
			return;
		}
		if (handshake.Value ().BlocksTransfer ()) {
			std::string message = "Dieses Add-on ist zu alt für diesen Server.";
			if (!handshake.Value ().updateMessage.empty ())
				message += " " + handshake.Value ().updateMessage;
			shared.Set ("Nicht angemeldet.", message);
			std::lock_guard<std::mutex> guard (shared.mutex);
			shared.busy = false;
			shared.signedIn = false;
			shared.dirty = true;
			workerRunning.store (false);
			return;
		}

		rtx::DeviceLogin login (*api, *tokens, serverUrl);
		const rtx::Result<rtx::StoredCredential> credential = login.SignIn (
			CurrentDevice (), &cancel,
			[this] (const rtx::DeviceLoginPrompt& prompt) {
				std::lock_guard<std::mutex> guard (shared.mutex);
				shared.connectionText = "Code " + prompt.userCode + " im Browser bestätigen.";
				shared.browserToOpen = prompt.verificationUriComplete.empty ()
										   ? prompt.verificationUri
										   : prompt.verificationUriComplete;
				shared.dirty = true;
			},
			[] (int seconds) {
				for (int i = 0; i < seconds * 10; ++i)
					std::this_thread::sleep_for (std::chrono::milliseconds (100));
			});

		if (!credential) {
			const std::string code = credential.GetError ().code;
			const std::string reason =
				code == rtx::errc::EndpointMissing
					? "Dieser Server bietet den Gerätelogin nicht an."
				: code == rtx::errc::AccessDenied
					? "Die Anmeldung wurde im Browser abgelehnt."
				: code == "membership_missing"
					? "Dieses Konto gehört zu keiner Organisation."
				: code == "organization_selection_required"
					? "Dieses Konto gehört zu mehreren Organisationen; eine Auswahl für Plugins "
					  "gibt es noch nicht."
					: credential.GetError ().message;
			shared.Set ("Nicht angemeldet.", "Anmeldung fehlgeschlagen: " + reason);
			std::lock_guard<std::mutex> guard (shared.mutex);
			shared.busy = false;
			shared.signedIn = false;
			shared.dirty = true;
			workerRunning.store (false);
			return;
		}

		// **Weder Projekte noch Blickpunkte holt dieser Faden.** Beide Listen
		// hängen am angemeldeten Zustand und nicht an der Anmeldehandlung —
		// sonst gäbe es sie nur nach einem Klick auf „Anmelden" und nicht
		// nach einem Neustart von Archicad mit gültigem Token. Genau das war
		// der Befund vom 24.09.2026: angemeldet, aber kein Projekt in der
		// Auswahl. Der Leerlauf lädt sie, sobald etwas fehlt.
		shared.Set ("Angemeldet als " + credential.Value ().displayName, {});
		{
			std::lock_guard<std::mutex> guard (shared.mutex);
			shared.signedIn = true;
			shared.busy = false;
			shared.dirty = true;
		}
		workerRunning.store (false);
	});
}

void RendertaxiPalette::StartSignOut ()
{
	if (workerRunning.load () || tokens == nullptr) return;
	cancel.Reset ();
	{
		std::lock_guard<std::mutex> guard (shared.mutex);
		shared.busy = true;
		shared.dirty = true;
	}
	workerRunning.store (true);
	worker = std::thread ([this] () {
		rtx::DeviceLogin login (*api, *tokens, serverUrl);
		const rtx::Status status = login.SignOut (&cancel);
		std::lock_guard<std::mutex> guard (shared.mutex);
		shared.signedIn = false;
		shared.busy = false;
		shared.projects.clear ();
		shared.viewpoints.clear ();
		shared.viewpointsProjectId.clear ();
		projectLoadTried = false;
		shared.projectsChanged = true;
		shared.viewpointsChanged = true;
		shared.connectionText = status ? "Abgemeldet. Das Token ist gelöscht und widerrufen."
									   : "Abgemeldet. " + status.GetError ().message;
		shared.openUrl.clear ();
		shared.resultText.clear ();
		shared.dirty = true;
		workerRunning.store (false);
	});
}

/**
 * Sorgt dafür, dass es eine Projektliste gibt, solange jemand angemeldet ist.
 *
 * **Der Befund vom 24.09.2026:** nach einem Neustart von Archicad war das
 * Token noch gültig, die Palette zeigte „Angemeldet als …" — und die
 * Projektauswahl war leer, weil die Liste nur im Anmeldefaden geholt wurde.
 * Abmelden und neu anmelden half; das ist keine Lösung, sondern ein Umweg um
 * einen Zustand, den der Client selbst herstellen kann.
 *
 * Ein Fehlschlag wird gesagt und nach einer halben Minute noch einmal
 * versucht — ein Netz, das gerade nicht da ist, kommt meist wieder.
 */
void RendertaxiPalette::RefreshProjectList ()
{
	if (workerRunning.load () || api == nullptr) return;
	{
		std::lock_guard<std::mutex> guard (shared.mutex);
		if (!shared.signedIn || shared.busy || !shared.projects.empty ()) return;
	}
	const auto now = std::chrono::steady_clock::now ();
	if (projectLoadTried && now - lastProjectLoad < std::chrono::seconds (30)) return;
	projectLoadTried = true;
	lastProjectLoad = now;

	JoinWorker ();
	cancel.Reset ();
	{
		std::lock_guard<std::mutex> guard (shared.mutex);
		shared.progressText = "Projekte werden geladen…";
		shared.dirty = true;
	}
	workerRunning.store (true);
	worker = std::thread ([this] () {
		const rtx::Result<std::vector<rtx::ProjectSummary>> projects = api->ListProjects (&cancel);
		std::lock_guard<std::mutex> guard (shared.mutex);
		if (projects) {
			shared.projects = projects.Value ();
			shared.projectsChanged = true;
			shared.progressText =
				shared.projects.empty () ? "Keine Projekte in dieser Organisation." : "";
		} else {
			shared.progressText =
				"Projekte konnten nicht geladen werden: " + projects.GetError ().message;
			if (projects.GetError ().code == rtx::errc::Unauthorized) {
				// Ein widerrufenes Token sieht man erst am ersten Aufruf.
				shared.signedIn = false;
				shared.connectionText = "Die Verbindung wurde beendet. Bitte neu anmelden.";
			}
		}
		shared.dirty = true;
		workerRunning.store (false);
	});
}

/**
 * Hält die Blickpunktliste an dem Projekt, das in der Auswahl steht.
 *
 * **Der Befund aus dem Abnahmelauf am 24.09.2026.** Die Liste wurde einmal
 * beim Anmelden für `projects.front()` geladen und blieb danach stehen. Wer
 * das Projekt wechselte, sah die Blickpunkte des ersten Projekts, und ein
 * Update darauf endete mit `404` — „Der Blickpunkt existiert nicht oder ist
 * nicht sichtbar". Der Server hat richtig geantwortet; falsch war der Client.
 *
 * Die Auswahl ist ein Zustand der Anzeige, kein Ereignis: es gibt für die
 * Auswahlliste keinen Beobachter, der ohne `AttachToAllItems` verlässlich
 * hinge. Der Leerlauf vergleicht deshalb zweimal je Sekunde, wozu die Liste
 * gehört und was gewählt ist — und lädt nach, sobald beides auseinanderfällt.
 */
/**
 * Die Einstellung „Rahmengröße" als Vertragswert — und sie **bleibt**.
 *
 * Gelesen wird die Auswahl, geschrieben wird nur, wenn sie sich geändert hat:
 * eine Einstellung, die den Neustart nicht überlebt, ist keine.
 */
std::string RendertaxiPalette::SelectedFrameSize ()
{
	const std::string value = sizePopUp.GetSelectedItem () == 2 ? "capture" : "canvas-default";
	if (value != shownFrameSize) {
		shownFrameSize = value;
		// Ausdrücklich qualifiziert: `DG::Palette` führt selbst ein
		// `SetFrameSize` für die Fenstergröße.
		rtxaddon::SetFrameSize (value);
	}
	return value;
}

void RendertaxiPalette::RefreshViewpointList ()
{
	if (workerRunning.load ()) return;

	std::string wanted;
	bool signedIn = false;
	{
		std::lock_guard<std::mutex> guard (shared.mutex);
		signedIn = shared.signedIn;
		if (!signedIn || shared.busy || shared.projects.empty ()) return;
		const short index = projectPopUp.GetSelectedItem ();
		if (index < 1 || static_cast<std::size_t> (index) > shared.projects.size ()) return;
		const rtx::ProjectSummary& project =
			shared.projects[static_cast<std::size_t> (index - 1)];
		if (shared.viewpointsProjectId == project.id) return;
		wanted = project.id;

		// **Zuerst weg, dann laden.** Eine Liste, die zum gewählten Projekt
		// nicht mehr gehört, darf keine Sekunde länger auswählbar sein.
		if (!shared.viewpoints.empty ()) {
			shared.viewpoints.clear ();
			shared.viewpointsChanged = true;
			shared.dirty = true;
		}
	}
	StartLoadViewpoints (wanted);
}

void RendertaxiPalette::StartLoadViewpoints (const std::string& projectId)
{
	if (workerRunning.load () || api == nullptr || projectId.empty ()) return;
	JoinWorker ();
	cancel.Reset ();
	{
		std::lock_guard<std::mutex> guard (shared.mutex);
		shared.progressText = "Blickpunkte werden geladen…";
		shared.dirty = true;
	}
	workerRunning.store (true);
	worker = std::thread ([this, projectId] () {
		const rtx::Result<std::vector<rtx::ViewpointSummary>> viewpoints =
			api->ListViewpoints (projectId, &cancel);
		std::lock_guard<std::mutex> guard (shared.mutex);
		if (viewpoints) {
			shared.viewpoints = viewpoints.Value ();
			// Die Zuordnung entsteht **mit** der Liste, nie davor.
			shared.viewpointsProjectId = projectId;
			shared.progressText = shared.viewpoints.empty ()
									  ? "Dieses Projekt hat noch keinen Blickpunkt."
									  : "";
		} else {
			shared.viewpoints.clear ();
			shared.viewpointsProjectId.clear ();
			shared.progressText =
				"Blickpunkte konnten nicht geladen werden: " + viewpoints.GetError ().message;
		}
		shared.viewpointsChanged = true;
		shared.dirty = true;
		workerRunning.store (false);
	});
	// Der gemerkte Vorschlag gilt für die **neue** Liste erneut.
	proposedViewpointPending = !proposedViewpointId.empty ();
}

/**
 * Verwirft den angefangenen Vorgang dieser Ansicht.
 *
 * Ohne diesen Weg war ein Vorgang, dessen Ziel nicht mehr stimmt, eine
 * Sackgasse: der Client meldete zu Recht `idempotency_conflict` — „fortsetzen
 * oder verwerfen" —, und verwerfen ging nur über das Löschen einer Datei im
 * Benutzerordner. Der Abbruch geht **auch serverseitig**: eine liegengebliebene
 * Session soll nicht bis zum Ablauf ihrer TTL stehen.
 */
void RendertaxiPalette::StartDiscard ()
{
	if (workerRunning.load () || store == nullptr) return;
	RefreshProjectCache ();
	// **Alle** Schlüssel dieser Ansicht, aus derselben Stelle, die sie beim
	// Start vergibt: das Fensterbild **und** das Rendering (F-05).
	const std::vector<std::string> keys = SourceKeysForCurrentView ();
	const std::string projectKey = cachedLocalProjectKey;
	bool anyPending = false;
	for (const std::string& key : keys)
		anyPending = anyPending || !store->FindPending (projectKey, key).IsEmpty ();
	if (!anyPending) {
		shared.SetProgress ("Für diese Ansicht steht kein angefangener Vorgang offen.");
		return;
	}

	JoinWorker ();
	cancel.Reset ();
	{
		std::lock_guard<std::mutex> guard (shared.mutex);
		shared.busy = true;
		shared.progressText = "Angefangene Übernahme wird verworfen…";
		shared.dirty = true;
	}
	workerRunning.store (true);
	worker = std::thread ([this, keys, projectKey] () {
		rtx::CaptureTransfer transfer (*api, *store);
		const rtx::Status status =
			rtx::DiscardAll (transfer, *store, projectKey, keys, &cancel);
		std::lock_guard<std::mutex> guard (shared.mutex);
		shared.busy = false;
		shared.progressText =
			status ? "Angefangene Übernahme verworfen. Die nächste beginnt von vorn."
				   : "Verworfen, aber der Server hat den Abbruch nicht bestätigt: " +
						 status.GetError ().message;
		shared.resultText.clear ();
		shared.openUrl.clear ();
		shared.dirty = true;
		workerRunning.store (false);
	});
}

/**
 * Macht aus einer gelesenen Quellansicht die **Renderquelle** desselben
 * Blickwinkels.
 *
 * Freie Funktion, damit Start, Verwerfen und Anzeige dieselbe Ableitung
 * benutzen und nicht jede ihre eigene ACAPI-Lesung braucht.
 */
static SourceView AsRenderingSource (SourceView view)
{
	view.isRendering = true;
	view.is3D = false;
	view.capturable = true;
	view.windowKind = "rendering";
	view.windowLabel = "Rendering";
	if (!view.viewMapName.empty ()) {
		view.displayName = view.viewMapName + " — Rendering";
		view.key += ":rendering";
	} else {
		view.displayName.clear ();
		view.key = "archicad:window:rendering";
	}
	return view;
}

SourceView RendertaxiPalette::SourceViewFor (CaptureSource source) const
{
	SourceView view = ReadCurrentView ();
	if (view.key.empty ()) view.key = "archicad:view:unknown";
	return source == CaptureSource::Rendering ? AsRenderingSource (view) : view;
}

std::vector<std::string> RendertaxiPalette::SourceKeysFor (const SourceView& current)
{
	SourceView base = current;
	if (base.key.empty ()) base.key = "archicad:view:unknown";
	std::vector<std::string> keys {base.key};
	const std::string rendering = AsRenderingSource (base).key;
	if (rendering != base.key) keys.push_back (rendering);
	return keys;
}

std::vector<std::string> RendertaxiPalette::SourceKeysForCurrentView () const
{
	return SourceKeysFor (ReadCurrentView ());
}

void RendertaxiPalette::StartCapture (CaptureSource source)
{
	if (workerRunning.load ()) return;

	// --- 1. Alles, was Archicad braucht, im Hauptfaden erledigen -------------
	const bool fromRendering = source == CaptureSource::Rendering;
	const SourceView view = SourceViewFor (source);
	if (!view.capturable) {
		shared.SetProgress ("Aus diesem Fenster lässt sich kein Bild erzeugen.");
		return;
	}

	std::vector<rtx::ProjectSummary> projects;
	std::vector<rtx::ViewpointSummary> viewpoints;
	std::string viewpointsProjectId;
	{
		std::lock_guard<std::mutex> guard (shared.mutex);
		projects = shared.projects;
		viewpoints = shared.viewpoints;
		viewpointsProjectId = shared.viewpointsProjectId;
		if (!shared.signedIn) {
			shared.progressText = "Bitte zuerst anmelden.";
			shared.dirty = true;
			return;
		}
	}
	if (projects.empty ()) {
		shared.SetProgress ("Kein Projekt zur Auswahl.");
		return;
	}

	const short projectIndex = projectPopUp.GetSelectedItem ();
	if (projectIndex < 1 || static_cast<std::size_t> (projectIndex) > projects.size ()) {
		shared.SetProgress ("Bitte ein Projekt wählen.");
		return;
	}
	const rtx::ProjectSummary& project = projects[static_cast<std::size_t> (projectIndex - 1)];

	rtx::CaptureTarget target;
	target.projectId = project.id;
	target.viewpoint.present = true;
	// Die Rahmengröße gilt für beide Modi; ob sie mitreist, entscheidet
	// `SendsSize()` — bei `update` nur zusammen mit `fit-to-capture` (§7.2).
	target.viewpoint.size = SelectedFrameSize ();
	// `baseImageRole` steht **innerhalb** von `target.viewpoint` (§7.2).
	target.viewpoint.baseImageRole = "viewport";
	std::string viewpointDisplayName;
	if (updateRadio.IsSelected ()) {
		// **Die Liste muss zu diesem Projekt gehören.** Der Leerlauf hält das
		// nach; hier steht es noch einmal, weil ein Update an einen
		// projektfremden Blickpunkt kein Fehler ist, den der Server ausbaden
		// soll — und weil ein Klick schneller sein kann als ein Nachladen.
		if (viewpointsProjectId != project.id) {
			shared.SetProgress ("Die Blickpunkte dieses Projekts werden noch geladen.");
			return;
		}
		const short viewpointIndex = viewpointPopUp.GetSelectedItem ();
		if (viewpointIndex < 1 || static_cast<std::size_t> (viewpointIndex) > viewpoints.size ()) {
			shared.SetProgress ("Bitte einen bestehenden Blickpunkt wählen.");
			return;
		}
		const rtx::ViewpointSummary& viewpoint =
			viewpoints[static_cast<std::size_t> (viewpointIndex - 1)];
		target.viewpoint.mode = "update";
		target.viewpoint.viewpointId = viewpoint.id;
		// **Ausdrückliche Handlung**, kein Vorgabewert: ohne Häkchen behält der
		// Blickpunkt Rahmenformat und Ausschnitt (Nutzerentscheidung 23.09.2026).
		target.viewpoint.frame = fitFrameCheck.IsChecked () ? "fit-to-capture" : "keep";
		viewpointDisplayName = viewpoint.name;
	} else {
		target.viewpoint.mode = "create";
		std::string name = Utf8 (nameEdit.GetText ());
		if (name.empty ()) name = view.displayName;
		if (name.empty ()) name = "Archicad-Ansicht";
		if (name.size () > 120) name.resize (120);
		target.viewpoint.name = name;
		// Ein **neuer** Blickpunkt übernimmt das Seitenverhältnis der Bilddatei
		// serverseitig: `fit-to-capture` ist bei `create` implizit, `keep`
		// wäre dort ein `400` (§7.2). Der Client sendet das Feld hier nicht,
		// nennt den wirksamen Wert aber richtig.
		target.viewpoint.frame = "fit-to-capture";
		viewpointDisplayName = name;
	}

	const std::string workRoot = rtx::TransferStore::DefaultWorkDirectory ();
	const std::string localProjectKey = cachedLocalProjectKey;
	const std::string viewKey = view.key.empty () ? "archicad:view:unknown" : view.key;

	// **Ein Vorgang ohne lokalen Bestand wird verworfen, bevor neue
	// Aufnahmebytes entstehen** (Issue #88, Punkt 16). Fehlt seine Bild- oder
	// Manifestdatei, gibt es nichts fortzusetzen. Blieb er stehen, landete die
	// neue Aufnahme in seinem Verzeichnis, ergab ein anderes Manifest, und
	// der Client meldete `idempotency_conflict` gegen sich selbst.
	//
	// Lokal wird hier geräumt, im Hauptfaden und ohne Netz; die Session bricht
	// der Arbeitsfaden danach auch serverseitig ab.
	const rtx::Result<rtx::PendingTransfer> orphan =
		rtx::ReleaseOrphanedPending (*store, localProjectKey, viewKey);
	if (!orphan) {
		shared.SetProgress ("Der verwaiste Vorgang ließ sich nicht räumen: " +
							orphan.GetError ().message);
		return;
	}
	const rtx::PendingTransfer orphaned = orphan.Value ();

	// Läuft für diese Ansicht schon eine Übernahme, wird ihr Verzeichnis
	// weiterverwendet. Nur so bleibt der Idempotenzschlüssel gültig.
	const rtx::PendingTransfer pending = store->FindPending (localProjectKey, viewKey);
	const std::string directory =
		pending.IsEmpty () ? workRoot + "/" + rtx::RandomHex (8) : pending.directory;

	// **Eine Wiederaufnahme nimmt nichts neu auf.**
	//
	// Der erste Anlauf tat genau das: er belichtete das 3D-Fenster noch einmal
	// in dasselbe Verzeichnis und baute ein frisches Manifest mit neuem
	// `createdAt`. Beides ändert die Bytes, über die `manifestSha256` läuft —
	// der angefangene Vorgang wurde damit zu einem anderen Vorgang, und der
	// Client meldete `idempotency_conflict` gegen sich selbst. Ein Vorgang ist
	// über seinen **Inhalt** identifiziert; wer ihn fortsetzt, muss denselben
	// Inhalt schicken.
	//
	// Ein Vorgang, der hier noch steht, hat seinen lokalen Bestand: der
	// verwaiste ist oben schon geräumt.
	const std::string pendingImage = directory + "/viewport.png";
	const bool resuming = !pending.IsEmpty ();

	// Der Ausschnitt ist der Schutzbereich der Rendering-Szene — das, was der
	// Nutzer im 3D-Fenster gesehen hat. Gibt es keine Szene, wird nicht
	// zugeschnitten; dann folgt das Format dem Bild.
	const RenderScene scene = ReadCurrentRenderScene ();
	// **Ein Rendering trägt seinen Ausschnitt schon.** Archicad hat es mit
	// genau diesen Maßen und, wenn angehakt, mit angewandtem Schutzbereich
	// gerechnet. Ein zweiter Zuschnitt könnte nur schaden.
	const bool cropToScene = scene.known && !view.isRendering && !resuming;

	std::string imagePath = pendingImage;
	if (resuming) {
		shared.SetProgress ("Angefangene Übernahme wird fortgesetzt — dasselbe Bild, "
							"derselbe Vorgang.");
	} else {
		shared.SetProgress (
			fromRendering
				? "Archicad rendert in der eingestellten Auflösung — das kann dauern…"
				: (cropToScene ? "Ansicht wird gesichert und zugeschnitten…"
							   : "Ansicht wird als Bild gesichert…"));
		const rtx::Result<ViewCaptureResult> captured =
			fromRendering ? RenderCurrentViewAsPng (directory, "viewport.png")
						  : CaptureCurrentViewAsPng (directory, "viewport.png");
		if (!captured) {
			shared.SetProgress (captured.GetError ().message);
			return;
		}
		imagePath = captured.Value ().filePath;
	}
	if (cropToScene) {
		const std::string croppedPath = directory + "/viewport-cropped.png";
		const rtx::Result<rtx::CropResult> cropped =
			rtx::CropImageToAspect (imagePath, croppedPath, scene.width, scene.height);
		if (!cropped) {
			shared.SetProgress (cropped.GetError ().message);
			return;
		}
		if (cropped.Value ().cropped) {
			// Ersetzen statt erst löschen, dann umbenennen: scheitert es, bleibt
			// die Aufnahme erhalten (#164, F-01). Über UTF-8-Pfade, auch unter
			// Windows (`rtx/Platform.hpp`).
			if (!rtx::RenameReplacing (croppedPath, imagePath)) {
				shared.SetProgress ("Das zugeschnittene Bild ließ sich nicht ablegen.");
				return;
			}
		}
	}

	const rtx::Result<rtx::ImageInfo> image = rtx::ReadImageInfo (imagePath);
	if (!image) {
		shared.SetProgress (image.GetError ().message);
		return;
	}
	bool hashed = false;
	const std::string sha = rtx::Sha256OfFile (imagePath, &hashed);
	if (!hashed) {
		shared.SetProgress ("Das erzeugte Bild ließ sich nicht lesen.");
		return;
	}

	const HostVersion host = ReadHostVersion ();
	const MachineInfo machine = ReadMachineInfo ();
	const std::string projectKey = ReadOrCreateProjectKey ();

	rtx::CaptureManifest manifest;
	// Ein wiederaufgenommener Vorgang behält **die Kennung und den Zeitpunkt
	// seines Manifests** — beide gehen in die Bytes ein, über die
	// `manifestSha256` läuft. Nicht zu verwechseln mit `pending.captureId`:
	// das ist die Kennung der **Session**, die der Server vergibt (§7.6).
	manifest.captureId = resuming ? pending.manifestCaptureId : rtx::NewUuidV7 ();
	manifest.createdAt = resuming ? pending.manifestCreatedAt : rtx::NowTimestampUtc ();
	manifest.source.hostKey = "archicad";
	manifest.source.hostVersion = host.version;
	manifest.source.hostBuild = host.build;
	manifest.source.pluginIdentifier = RTX_ADDON_IDENTIFIER;
	manifest.source.pluginVersion = RTX_ADDON_VERSION;
	manifest.source.os = machine.os;
	manifest.source.osVersion = machine.osVersion;
	manifest.source.architecture = machine.architecture;
	manifest.platformProjectId = project.id;
	manifest.sourceProjectKey = projectKey;   // leer heißt im Manifest `null`
	manifest.projectDisplayName = cachedProjectName;
	manifest.sourceViewKey = view.key;
	manifest.viewDisplayName = view.displayName;

	rtx::CaptureAsset asset;
	asset.role = "viewport";
	asset.path = "viewport.png";
	asset.status = "present";
	asset.mediaType = image.Value ().mediaType;
	asset.byteSize = rtx::FileSize (imagePath);
	asset.sha256 = sha;
	asset.localPath = imagePath;
	asset.hasImage = true;
	asset.image.width = image.Value ().width;
	asset.image.height = image.Value ().height;
	asset.image.colorSpace = "srgb";
	asset.image.bitDepth = image.Value ().bitDepth;
	asset.image.sampleFormat = image.Value ().sampleFormat;
	asset.image.channels = image.Value ().channels;
	manifest.assets.push_back (asset);

	// `beauty` ist in Archicad 28 nicht ohne Umweg zu haben: PhotoRender kennt
	// kein PNG und startet einen vollständigen Renderlauf (capabilities.md,
	// Abschnitt 3). Der Vertrag verlangt dafür `planned`, nicht Weglassen.
	rtx::CaptureAsset beauty;
	beauty.role = "beauty";
	beauty.path = "beauty.png";
	beauty.status = "planned";
	// Gemessen am 20.09.2026: PhotoRender liefert kein PNG und rendert in der
	// Aufloesung der Rendering-Einstellungen (1024x768) statt in der der
	// Quellansicht (1071x905) — also einen anderen Bildausschnitt.
	beauty.note = "PhotoRender rendert in der Aufloesung der Rendering-Einstellungen und liefert "
				  "damit einen anderen Bildausschnitt als die Quellansicht.";
	manifest.assets.push_back (beauty);

	// --- 2. Lokal prüfen, bevor irgendetwas das Gerät verlässt --------------
	const rtx::Status valid = manifest.Validate ();
	if (!valid) {
		shared.SetProgress ("Das Manifest ist ungültig: " + valid.GetError ().message);
		rtx::LogLine ("Manifest ungültig: " + valid.GetError ().code + " " +
					  valid.GetError ().pointer);
		return;
	}
	const rtx::Result<std::string> manifestText = manifest.Serialize ();
	if (!manifestText) {
		shared.SetProgress (manifestText.GetError ().message);
		return;
	}
	if (!rtx::WriteTextFile (directory + "/capture-manifest.json", manifestText.Value ())) {
		shared.SetProgress ("Das Manifest ließ sich nicht schreiben.");
		return;
	}

	rtx::TransferRequest request;
	request.manifest = manifest;
	request.target = target;
	request.sourceProjectKey = localProjectKey;
	request.sourceViewKey = viewKey;
	request.directory = directory;
	request.serverUrl = serverUrl;
	request.projectDisplayName = project.name;
	request.viewpointDisplayName = viewpointDisplayName;

	// --- 3. Ab hier ohne Archicad: eigener Faden, Palette bleibt bedienbar ---
	// Das Ziel steht in der Palette, bevor die Übertragung beginnt: eine
	// Übernahme, die ihren Blickpunkt nicht nennt, wäre eine stille Zuordnung.
	std::string targetLine =
		target.viewpoint.mode == "update"
			? "Update von Blickpunkt „" + viewpointDisplayName + "“ in " + project.name
			: "Neuer Blickpunkt „" + viewpointDisplayName + "“ in " + project.name;
	// Die Aufnahmemaße sind **nicht** die Zielgröße der Ausgabe. Sie stehen
	// hier als Angabe über die Datei und nirgends als Ausgabeziel.
	targetLine += "  ·  Aufnahme " + std::to_string (image.Value ().width) + " x " +
				  std::to_string (image.Value ().height) + " Pixel";
	targetLine += target.viewpoint.EffectiveFrame () == "fit-to-capture"
					  ? ", Rahmen wird an die Aufnahme angepasst"
					  : ", Rahmen und Ausschnitt bleiben";

	cancel.Reset ();
	{
		std::lock_guard<std::mutex> guard (shared.mutex);
		shared.busy = true;
		shared.openUrl.clear ();
		shared.resultText = targetLine;
		shared.dirty = true;
	}

	workerRunning.store (true);
	worker = std::thread ([this, request, targetLine, orphaned] () {
		// **F-02:** Kein Zweig hier beendet den Prozess.
		const rtx::Result<rtx::HandshakeInfo> handshake =
			api->Handshake (CurrentDevice (), &cancel);
		const auto stop = [this] (const std::string& message) {
			shared.SetProgress (message);
			std::lock_guard<std::mutex> guard (shared.mutex);
			shared.busy = false;
			shared.dirty = true;
			workerRunning.store (false);
		};
		if (!handshake) {
			stop (handshake.GetError ().code == rtx::errc::EndpointMissing
					  ? "Dieser Server bietet die Plugin API v1 nicht an."
					  : "Der Handshake ist fehlgeschlagen: " + handshake.GetError ().message);
			return;
		}
		if (handshake.Value ().BlocksTransfer ()) {
			// `update_required`: das Plugin beginnt keine Übernahme (§4).
			std::string message = "Dieses Add-on ist zu alt für diesen Server.";
			if (!handshake.Value ().updateMessage.empty ())
				message += " " + handshake.Value ().updateMessage;
			if (!handshake.Value ().updateUrl.empty ())
				message += " " + handshake.Value ().updateUrl;
			stop (message);
			return;
		}
		const rtx::Status supported = handshake.Value ().RequireCaptureContract ();
		if (!supported) {
			stop (supported.GetError ().message);
			return;
		}

		rtx::CaptureTransfer transfer (*api, *store);
		// Der verwaiste Vorgang von oben: seine Session wird abgebrochen,
		// statt bis zum Ablauf ihrer TTL stehen zu bleiben. Scheitert das,
		// geht die Übernahme trotzdem weiter — lokal ist er längst geräumt.
		// `Discard` protokolliert einen gescheiterten Abbruch selbst.
		if (!orphaned.IsEmpty () && !orphaned.captureId.empty ())
			transfer.Discard (orphaned, &cancel);
		const rtx::Result<rtx::CaptureResult> result =
			transfer.Run (request, &cancel, [this] (const rtx::TransferProgress& progress) {
				shared.SetProgress (progress.message + " (" + std::to_string (progress.percent) +
									" %)");
			});

		std::lock_guard<std::mutex> guard (shared.mutex);
		shared.busy = false;
		if (result) {
			shared.progressText = "Fertig.";
			shared.resultText = targetLine + " — übernommen.";
			shared.openUrl = result.Value ().openUrl;
			// Nach einer Anlage gibt es einen Blickpunkt mehr. Die Zuordnung
			// fällt weg, und der Leerlauf liest die Liste neu — sonst wäre
			// genau der neue Blickpunkt der einzige, den man nicht
			// aktualisieren könnte.
			shared.viewpointsProjectId.clear ();
			// Der getippte Name ist verbraucht; ab jetzt schlägt wieder die
			// Ansicht vor. **Der Faden setzt nur die Marke** — angefasst wird
			// das Feld im Hauptfaden, wie jedes andere Element auch.
			shared.resetNameSuggestion = true;
		} else {
			shared.progressText = result.GetError ().message;
			if (result.GetError ().code == rtx::errc::Unauthorized) {
				shared.signedIn = false;
				shared.connectionText = "Die Verbindung wurde beendet. Bitte neu anmelden.";
			}
		}
		shared.dirty = true;
		workerRunning.store (false);
	});
}

// --- Paletteneinbindung ------------------------------------------------------

GSErrCode RendertaxiPalette::PaletteControlCallBack (Int32, API_PaletteMessageID messageID,
													 GS::IntPtr param)
{
	switch (messageID) {
		case APIPalMsg_OpenPalette: EnsureShown (); break;
		case APIPalMsg_ClosePalette:
			if (HasInstance ()) GetInstance ().Hide ();
			break;
		case APIPalMsg_HidePalette_Begin:
			if (HasInstance () && GetInstance ().IsVisible ()) GetInstance ().Hide ();
			break;
		case APIPalMsg_HidePalette_End:
			if (HasInstance () && !GetInstance ().IsVisible ()) GetInstance ().Show ();
			break;
		case APIPalMsg_DisableItems_Begin:
			if (HasInstance () && GetInstance ().IsVisible ()) GetInstance ().DisableItems ();
			break;
		case APIPalMsg_DisableItems_End:
			if (HasInstance () && GetInstance ().IsVisible ()) GetInstance ().EnableItems ();
			break;
		case APIPalMsg_IsPaletteVisible:
			*(reinterpret_cast<bool*> (param)) = HasInstance () && GetInstance ().IsVisible ();
			break;
		default: break;
	}
	return NoError;
}

GSErrCode RendertaxiPalette::RegisterPaletteControlCallBack ()
{
	return ACAPI_RegisterModelessWindow (
		GS::CalculateHashValue (paletteGuid), PaletteControlCallBack,
		API_PalEnabled_FloorPlan + API_PalEnabled_Section + API_PalEnabled_Elevation +
			API_PalEnabled_InteriorElevation + API_PalEnabled_3D + API_PalEnabled_Detail +
			API_PalEnabled_Worksheet + API_PalEnabled_Layout + API_PalEnabled_DocumentFrom3D,
		GSGuid2APIGuid (paletteGuid));
}

} // namespace rtxaddon
