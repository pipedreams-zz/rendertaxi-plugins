// Die gespeicherten 3D-Ansichten als Auswahl (RTX-A-009, #255): Reihenfolge,
// Beschriftung, und was nach einem Auffrischen gewählt ist.
#include "Testing.hpp"

#include "rtx/SavedViews.hpp"

using namespace rtx;

namespace {

std::vector<SavedView> Sample ()
{
	return {
		{"g-eigen", "Mein Blick", {}, true},
		{"g-sued", "Süd", {"Außen"}, false},
		{"g-halle", "Halle", {"Innen", "EG"}, false},
		{"", "ohne Schlüssel", {}, false},
	};
}

} // namespace

RTX_TEST (SavedViewsStartWithTheCurrentModelViewThenPublicThenPersonal)
{
	const std::vector<ViewChoice> choices = BuildViewChoices (Sample ());
	RTX_CHECK (choices.size () == 4);
	RTX_CHECK (choices[0].label == "Aktuelle Modellansicht" && choices[0].guid.empty ());
	RTX_CHECK (choices[1].label == "Süd — Außen" && choices[1].guid == "g-sued" && choices[1].name == "Süd");
	RTX_CHECK (choices[2].label == "Halle — Innen / EG");
	RTX_CHECK (choices[3].label == "Eigene: Mein Blick");
}

RTX_TEST (FoldersEveryViewSharesAreLeftOutOfTheLabel)
{
	// Am Host lagen alle Ansichten unter „Ohne Titel / Beispielausschnitte".
	const std::vector<ViewChoice> choices = BuildViewChoices ({
		{"g-1", "Allgemeine Perspektive", {"Ohne Titel", "Beispiel", "Arbeit", "3D"}, false},
		{"g-2", "Nord", {"Ohne Titel", "Beispiel", "RTX-Test"}, false},
		{"g-3", "Oben", {"Ohne Titel", "Beispiel"}, false},
	});
	RTX_CHECK (choices.size () == 4);
	RTX_CHECK (choices[1].label == "Allgemeine Perspektive — Arbeit / 3D");
	RTX_CHECK (choices[2].label == "Nord — RTX-Test");
	RTX_CHECK (choices[3].label == "Oben");
}

RTX_TEST (SavedViewsWithoutAnyEntryStillOfferTheCurrentModelView)
{
	const std::vector<ViewChoice> choices = BuildViewChoices ({});
	RTX_CHECK (choices.size () == 1 && choices[0].label == "Aktuelle Modellansicht");
}

RTX_TEST (ARenamedViewStaysSelectedAndADeletedOneFallsBackWithAHint)
{
	std::vector<SavedView> views = Sample ();
	views[1].name = "Süd neu";
	const std::vector<ViewChoice> choices = BuildViewChoices (views);
	const Reselection renamed = Reselect (choices, "g-sued", "Süd");
	RTX_CHECK (renamed.index == 1 && renamed.hint.empty ());
	RTX_CHECK (choices[renamed.index].name == "Süd neu");

	const Reselection deleted = Reselect (choices, "g-weg", "Nord");
	RTX_CHECK (deleted.index == 0);
	RTX_CHECK (deleted.hint.find ("„Nord“ gibt es nicht mehr") != std::string::npos);

	const Reselection none = Reselect (choices, "", "");
	RTX_CHECK (none.index == 0 && none.hint.empty ());
}

RTX_TEST (AnAssignmentOnlyProposesAnUpdateForExactlyItsViewpointInTheSelectedProject)
{
	using A = UpdateProposal::Action;
	const std::vector<std::string> listB = {"vp-y", "vp-z"};
	// F-01 an PR #259: Zuordnung nach A/X, gewählt ist B mit Y — kein Update auf Y.
	RTX_CHECK (ProposeUpdate ("p-a", "vp-x", "p-b", "p-b", listB).action == A::None);
	// Liste noch vom vorigen Projekt: warten, nichts wählen.
	RTX_CHECK (ProposeUpdate ("p-b", "vp-z", "p-b", "p-a", {"vp-z"}).action == A::Wait);
	// Zugeordneter Blickpunkt gelöscht: die verbleibende Auswahl wird kein Ziel.
	RTX_CHECK (ProposeUpdate ("p-b", "vp-x", "p-b", "p-b", listB).action == A::None);
	// Gültige Zuordnung: genau dieser Blickpunkt.
	const UpdateProposal valid = ProposeUpdate ("p-b", "vp-z", "p-b", "p-b", listB);
	RTX_CHECK (valid.action == A::Select && valid.index == 1);
	// Keine Zuordnung, kein Projekt gewählt.
	RTX_CHECK (ProposeUpdate ("p-b", "", "p-b", "p-b", listB).action == A::None);
	RTX_CHECK (ProposeUpdate ("p-b", "vp-z", "", "", listB).action == A::None);
}
