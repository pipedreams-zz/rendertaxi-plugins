// Die Palette: Verbindung · Projekt/Blickpunkt · Bild übernehmen · im Browser
// öffnen. Genau diese vier Bereiche und keinen fünften (Festlegung 3 aus
// Issue #20); Generierung und Ergebnisbearbeitung bleiben in der Webanwendung.
//
// **Archicad blockiert nicht.** Jeder Netzaufruf läuft in einem eigenen Faden;
// die Palette liest seinen Zustand im Leerlaufereignis und schreibt ihn in die
// Anzeige. Der Faden fasst kein DG-Objekt an und ruft kein ACAPI auf — beides
// gehört dem Hauptfaden.
#pragma once

#include "APIEnvir.h"
#include "ACAPinc.h"
#include "DGModule.hpp"

#include <atomic>
#include <chrono>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "HostInfo.hpp"
#include "rtx/CaptureTransfer.hpp"
#include "rtx/DeviceLogin.hpp"
#include "rtx/Http.hpp"
#include "rtx/PluginApi.hpp"
#include "rtx/ProjectList.hpp"
#include "rtx/TokenStore.hpp"
#include "rtx/TransferStore.hpp"

#define RtxPaletteResId 32500
/** Der Dialog „Neues Projekt" (`RINT/rendertaxi.grc`, #281). */
#define RtxNewProjectDialogResId 32510
// Zwei Menüressourcen mit je **einem** Befehl; zusammen ergeben sie ein
// flaches Hauptmenü „rendertaxi.ai" (siehe `RINT/rendertaxi.grc`).
#define RtxMenuResId 32500
#define RtxAboutMenuResId 32501

namespace rtxaddon {

/** Was der Arbeitsfaden dem Hauptfaden mitteilt. */
struct SharedState {
	std::mutex mutex;
	std::string connectionText;
	std::string progressText;
	std::string resultText;
	std::string sourceViewText;
	/** Die umgebrochenen Angaben zur Aufnahme, eine Zeile je Eintrag. */
	std::vector<std::string> infoLines;
	/** Nach einer Übernahme darf der Namensvorschlag wieder greifen. */
	bool resetNameSuggestion = false;
	std::string openUrl;
	std::string browserToOpen;
	std::vector<rtx::ProjectSummary> projects;
	std::vector<rtx::ViewpointSummary> viewpoints;
	/**
	 * **Zu welchem Projekt die Blickpunktliste gehört.** Ohne dieses Feld war
	 * die Liste eine Behauptung: sie wurde einmal für das erste Projekt
	 * geladen und blieb beim Projektwechsel stehen — ein Update ging dann an
	 * einen Blickpunkt eines fremden Projekts, und der Server antwortete
	 * `404 viewpoint_not_found`. Leer heißt „für dieses Projekt ist nichts
	 * gelesen".
	 */
	std::string viewpointsProjectId;
	/**
	 * Die Canvas-Vorgabe aus dem jüngsten Handshake (RTX-P-015). Bis zum
	 * ersten Handshake, und gegen einen Server vor 1.7.0, unbekannt: die
	 * Zielgröße steht dann ohne Zahl da.
	 */
	rtx::CanvasDefault canvasDefault;
	bool projectsChanged = false;
	bool viewpointsChanged = false;
	/**
	 * Nach einer Projektanlage: dieses Projekt soll gewählt werden, und der
	 * Name ist als erledigte Absicht zu melden (#281). Der Faden setzt beides,
	 * der Hauptfaden löst es ein.
	 */
	std::string createdProjectId;
	std::string createdProjectName;
	bool signedIn = false;
	bool busy = false;
	bool dirty = false;

	void Set (const std::string& connection, const std::string& progress);
	void SetProgress (const std::string& progress);
	void SetResult (const std::string& result, const std::string& url);
};

class RendertaxiPalette final : public DG::Palette,
								public DG::PanelObserver,
								public DG::ButtonItemObserver,
								public DG::TextEditBaseObserver,
								public DG::PopUpObserver {
public:
	static bool HasInstance ();
	static RendertaxiPalette& GetInstance ();
	static void EnsureShown ();
	static void DestroyInstance ();
	static GSErrCode RegisterPaletteControlCallBack ();

	void Show ();
	void Hide ();

	~RendertaxiPalette () override;

private:
	enum {
		ConnectionGroupId = 1,
		ServerLabelId = 2,
		ServerEditId = 3,
		ConnectionStatusId = 4,
		SignInButtonId = 5,
		SignOutButtonId = 6,
		TargetGroupId = 7,
		ProjectLabelId = 8,
		ProjectPopUpId = 9,
		CreateRadioId = 10,
		NameLabelId = 11,
		NameEditId = 12,
		UpdateRadioId = 13,
		ViewpointLabelId = 14,
		ViewpointPopUpId = 15,
		FitFrameCheckId = 16,
		CaptureGroupId = 17,
		SourceViewTextId = 18,
		CaptureButtonId = 19,
		CancelButtonId = 20,
		ProgressTextId = 21,
		ResultGroupId = 22,
		ResultTextId = 23,
		OpenButtonId = 24,
		DiscardButtonId = 25,
		CaptureFormatTextId = 26,
		TargetFormatTextId = 27,
		MatchTextId = 28,
		MemoryTextId = 29,
		RenderingButtonId = 30,
		InfoLine5Id = 31,
		InfoLine6Id = 32,
		ProgressText2Id = 33,
		ResultText2Id = 34,
		SizeLabelId = 35,
		SizePopUpId = 36,
		ResultText3Id = 37,
		ProgressText3Id = 38,
		InfoLine7Id = 39,
		InfoLine8Id = 40,
		ViewLabelId = 41,
		ViewPopUpId = 42,
		ViewRefreshButtonId = 43,
		ProjectRefreshButtonId = 44,
		NewProjectButtonId = 45,
		BuildTextId = 46
	};

	RendertaxiPalette ();

	void ButtonClicked (const DG::ButtonClickEvent& ev) override;
	void TextEditChanged (const DG::TextEditChangeEvent& ev) override;
	void PanelIdle (const DG::PanelIdleEvent& ev) override;
	void PanelCloseRequested (const DG::PanelCloseRequestEvent& ev, bool* accepted) override;
	/** Der Fokus kehrt auf die Palette zurück: Listen neu laden, höchstens alle 5 s. */
	void PanelActivated (const DG::PanelActivateEvent& ev) override;
	void PanelTopStatusGained (const DG::PanelTopStatusEvent& ev) override;
	/** Der volle Name des gewählten Eintrags — die Liste selbst endet auf „…". */
	void ItemToolTipRequested (const DG::ItemHelpEvent& ev, GS::UniString* toolTipText) override;

	void StartSignIn ();
	void StartSignOut ();
	/** Bricht einen angefangenen Vorgang serverseitig ab und räumt ihn lokal. */
	void StartDiscard ();
	/** Lädt die Projekte, sobald jemand angemeldet ist und keine Liste steht. */
	void RefreshProjectList ();
	/**
	 * Lädt Projekt- **und** Blickpunktliste neu, ohne die Auswahl zu
	 * verlieren (#281): gewählt bleibt, was noch existiert, sonst der erste
	 * Eintrag. Wege: Knopf „Aktualisieren", Öffnen der Palette, Rückkehr des
	 * Fokus. Läuft gerade ein Vorgang, holt der Leerlauf es danach nach.
	 */
	void StartRefreshLists ();
	/** „Neues Projekt…": Name erfragen, anlegen, Liste neu laden, Projekt wählen. */
	void StartCreateProject ();
	/** Lädt die Blickpunkte **dieses** Projekts; alles andere bleibt stehen. */
	void StartLoadViewpoints (const std::string& projectId);
	/** Merkt im Leerlauf, dass die Liste nicht mehr zum gewählten Projekt passt. */
	void RefreshViewpointList ();
	/**
	 * Liest die gespeicherten 3D-Ansichten der Ausschnittsmappe neu und baut
	 * die Auswahl „Ansicht" auf (RTX-A-009). Die gewählte Ansicht wird über
	 * ihre GUID wiedergefunden; fehlt sie, gilt wieder die aktuelle
	 * Modellansicht, und die Palette sagt das. Mit `listEntries` steht jeder
	 * Eintrag der Mappe im Protokoll (Q-13) — nur auf „Aktualisieren", sonst
	 * wüchse das Protokoll mit jeder Änderung der Mappe um Hunderte Zeilen.
	 */
	void RefreshSavedViews (bool listEntries = false);
	/** Reagiert auf eine Wahl in „Ansicht" — vom Nutzer oder vom Wiederfinden. */
	void OnViewChosen (std::size_t index);
	/** Die gewählte gespeicherte Ansicht, oder `nullptr` für die aktuelle Modellansicht. */
	const rtx::SavedView* SelectedSavedView () const;
	/**
	 * Steht zur gewählten Ansicht eine bestätigte Zuordnung, wechselt die
	 * Palette sichtbar auf „Bestehenden aktualisieren" — einmal je Ansicht und
	 * Blickpunkt, damit eine eigene Wahl des Nutzers stehen bleibt.
	 */
	void ProposeUpdateForSelectedView ();
	/** Woher das Bild kommt: aus dem aktiven Fenster oder aus dem Rendering. */
	enum class CaptureSource { CurrentWindow, Rendering };
	void StartCapture (CaptureSource source = CaptureSource::CurrentWindow);

	/**
	 * Die Quellansicht für eine der beiden Quellen — **die einzige Stelle**,
	 * an der ein Quellschlüssel entsteht.
	 *
	 * Start und Verwerfen lesen daraus; zwei Wege zu einer Entscheidung waren
	 * genau der Fehler hinter F-05 (ein Rendering-Vorgang ließ sich nicht
	 * verwerfen, weil das Verwerfen einen anderen Schlüssel suchte als der
	 * Start geschrieben hatte).
	 */
	SourceView SourceViewFor (CaptureSource source) const;
	/** Alle Schlüssel dieser Ansicht — aus einer bereits gelesenen Quellansicht. */
	static std::vector<std::string> SourceKeysFor (const SourceView& current);
	/** Dasselbe, wenn gerade keine Quellansicht zur Hand ist. */
	std::vector<std::string> SourceKeysForCurrentView () const;
	void CancelRunningJob ();
	void OpenResultInBrowser ();
	void RefreshFromState ();
	void RefreshSourceView ();
	void RefreshProjectCache ();
	rtx::DesiredOutput SelectedViewpointFormat () const;
	/** Liest die Einstellung „Rahmengröße" und sichert eine Änderung. */
	std::string SelectedFrameSize ();
	rtx::DeviceIdentity CurrentDevice () const;
	std::string SuggestViewpointName (const SourceView& view) const;
	void JoinWorker ();
	void RebuildApi ();

	static GSErrCode PaletteControlCallBack (Int32 paletteId, API_PaletteMessageID messageID,
											 GS::IntPtr param);

public:
	/** Merkt einen Projektwechsel vor; der Leerlauf liest ihn. */
	static void NoteProjectChanged ();

private:

	DG::GroupBox connectionGroup;
	DG::LeftText serverLabel;
	DG::TextEdit serverEdit;
	DG::LeftText connectionStatus;
	DG::Button signInButton;
	DG::Button signOutButton;

	DG::GroupBox targetGroup;
	DG::LeftText projectLabel;
	DG::PopUp projectPopUp;
	DG::RadioButton createRadio;
	DG::LeftText nameLabel;
	DG::TextEdit nameEdit;
	DG::RadioButton updateRadio;
	/** „Rahmen an Aufnahme anpassen" — ausdrückliche Handlung, nie Vorgabe. */
	DG::CheckBox fitFrameCheck;
	DG::LeftText sizeLabel;
	/**
	 * „Rahmengröße": Canvas-Vorgabe oder Render-Einstellung (§7.2, `size`).
	 *
	 * Eine **Einstellung**, keine Eigenschaft der Aufnahme: sie überlebt den
	 * Neustart in `settings.json` und gilt, bis jemand sie ändert.
	 */
	DG::PopUp sizePopUp;
	DG::LeftText viewpointLabel;
	DG::PopUp viewpointPopUp;

	DG::GroupBox captureGroup;
	DG::LeftText sourceViewText;
	/**
	 * Acht Zeilen für die Angaben zur Aufnahme (bis zum 25.09.2026 sechs).
	 *
	 * Sie sind **kein** Satz je Zeile: der Text wird auf die Breite der
	 * Palette umgebrochen und füllt so viele davon, wie er braucht. Der erste
	 * Anlauf kürzte stattdessen mit „…" — auch das war eine Angabe, die man
	 * nicht lesen konnte.
	 */
	DG::LeftText captureFormatText;
	DG::LeftText targetFormatText;
	DG::LeftText matchText;
	DG::LeftText memoryText;
	DG::LeftText infoLine5;
	DG::LeftText infoLine6;
	DG::LeftText infoLine7;
	DG::LeftText infoLine8;
	DG::Button captureButton;
	DG::Button cancelButton;
	/** Nur bedienbar, solange für diese Ansicht ein Vorgang offen steht. */
	DG::Button discardButton;
	/** Holt das fertige Rendering, ohne dass sein Fenster vorn stehen muss. */
	DG::Button renderingButton;
	DG::LeftText progressText;
	/** Zweite Zeile; Meldungen werden umgebrochen, nicht abgeschnitten. */
	DG::LeftText progressText2;
	DG::LeftText progressText3;

	DG::GroupBox resultGroup;
	DG::LeftText resultText;
	DG::LeftText resultText2;
	DG::LeftText resultText3;
	DG::Button openButton;

	/** „Ansicht": aktuelle Modellansicht oder eine gespeicherte 3D-Ansicht. */
	DG::LeftText viewLabel;
	DG::PopUp viewPopUp;
	DG::Button viewRefreshButton;

	/** „Aktualisieren" an der Projektliste und „Neues Projekt…" (#281). */
	DG::Button projectRefreshButton;
	DG::Button newProjectButton;
	/** „Build … vom …" im Fuß, damit jeder Screenshot den Stand zeigt. */
	DG::LeftText buildText;

	std::unique_ptr<rtx::HttpClient> http;
	std::unique_ptr<rtx::TokenStore> tokens;
	std::unique_ptr<rtx::PluginApiClient> api;
	std::unique_ptr<rtx::TransferStore> store;
	std::string serverUrl;
	/** Die **einmal** erzeugte Kennung dieser Installation (§5.5). */
	std::string deviceId;

	mutable SharedState shared;
	rtx::CancelToken cancel;
	std::thread worker;
	std::atomic<bool> workerRunning {false};

	/** Zuletzt angezeigte Werte — nur Änderungen werden in die Anzeige geschrieben. */
	std::string shownConnection;
	std::string shownProgress;
	std::string shownResult;
	std::string shownSourceView;
	std::vector<std::string> shownInfoLines;
	/** Der zuletzt selbst gesetzte Namensvorschlag. */
	std::string shownSuggestion;
	/** Hat der Nutzer den Namen selbst getippt? Dann bleibt er stehen. */
	bool nameEditedByUser = false;
	/** Zuletzt gesicherte Rahmengröße; nur Änderungen werden geschrieben. */
	std::string shownFrameSize;
	/** Schlüssel des Vorgangs, dessen Ziel schon wiederhergestellt wurde. */
	std::string restoredPendingKey;
	bool shownBusy = false;
	/** Spiegel des Knopfzustands; der Leerlauf schreibt nur Änderungen. */
	bool discardEnabled = true;
	/** Wurde schon einmal versucht, die Projekte zu laden? */
	bool projectLoadTried = false;
	/**
	 * **Die Listen, wie sie in den Auswahlen stehen** (F-01 an PR #292). Nur
	 * `RefreshFromState` ändert sie, im selben Schritt wie die Auswahl; jeder,
	 * der eine Stelle der Auswahl in einen Eintrag übersetzt, liest sie — nie
	 * `shared.projects` oder `shared.viewpoints`, die der Arbeitsfaden vorher
	 * ersetzt.
	 */
	rtx::ShownList<rtx::ProjectSummary> shownProjects;
	rtx::ShownList<rtx::ViewpointSummary> shownViewpoints;
	/** Zuletzt gewählte Einträge — sie überleben das Neuladen, wenn es sie noch gibt. */
	std::string lastProjectId;
	std::string lastViewpointId;
	/** Ein Neuladen wartet, bis kein Vorgang mehr läuft (Knopf, Öffnen). */
	bool refreshPending = false;
	/** Der Fokus kam zurück; der Leerlauf lädt, wenn die Sperre es erlaubt. */
	bool focusRefreshWanted = false;
	rtx::RefreshGate refreshGate {std::chrono::seconds (5)};
	/** Die laufende Projektanlage; derselbe Name trägt denselben Schlüssel. */
	rtx::NewProjectIntent newProject;
	/** Der zuletzt bestätigte Name, solange seine Anlage nicht gelungen ist. */
	std::string pendingProjectName;
	/** Nur der Knopf meldet das Neuladen; Öffnen und Fokus laden still. */
	bool refreshAnnounce = false;
	std::chrono::steady_clock::time_point lastProjectLoad {};
	bool shownSignedIn = false;
	bool shownFitEnabled = true;

	/** Zuletzt gesehene Quellansicht; ein Wechsel setzt den Namensvorschlag neu. */
	std::string lastSourceViewKey;
	/** Zwischengespeicherte Projektangaben; sie kosten je einen ACAPI-Aufruf. */
	std::string cachedProjectName;
	std::string cachedLocalProjectKey;
	bool projectCacheValid = false;
	/** Drosselt die Abfrage der Quellansicht im Leerlauf. */
	std::chrono::steady_clock::time_point lastSourceViewCheck {};
	/** Gemerkter Blickpunkt, der in der Auswahl vorgemerkt werden soll. */
	std::string proposedViewpointId;
	bool proposedViewpointPending = false;

	/** Die zuletzt gelesenen gespeicherten 3D-Ansichten und ihre Zeilen in „Ansicht". */
	std::vector<rtx::SavedView> savedViews;
	std::vector<rtx::ViewChoice> viewChoices;
	/** Gewählte gespeicherte Ansicht; leer heißt „Aktuelle Modellansicht". */
	std::string selectedViewGuid;
	std::string selectedViewName;
	/** Zuletzt in „Ansicht" gesehene Zeile (1-basiert, wie DG zählt). */
	short shownViewIndex = 1;
	/** Ansicht und Blickpunkt, für die der Wechsel auf „aktualisieren" schon geschah. */
	std::string proposedUpdateFor;

	static GS::Ref<RendertaxiPalette> instance;
	/** Setzt die Projektbenachrichtigung; der Leerlauf liest und löscht sie. */
	static std::atomic<bool> projectChanged;
	/** Ein Projektwechsel macht die Liste der gespeicherten Ansichten ungültig. */
	static std::atomic<bool> savedViewsStale;
};

} // namespace rtxaddon
