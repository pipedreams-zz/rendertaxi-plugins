#include "rtx/SavedViews.hpp"

namespace rtx {

const char* const kCurrentModelViewLabel = "Aktuelle Modellansicht";

namespace {

/** Wie viele führende Ordner alle Ansichten einer Mappe gemeinsam haben. */
std::size_t CommonFolderDepth (const std::vector<SavedView>& views, bool personal)
{
	const std::vector<std::string>* first = nullptr;
	std::size_t depth = 0;
	for (const SavedView& view : views) {
		if (view.personal != personal || view.guid.empty ()) continue;
		if (first == nullptr) {
			first = &view.folders;
			depth = first->size ();
			continue;
		}
		std::size_t same = 0;
		while (same < depth && same < view.folders.size () && view.folders[same] == (*first)[same]) ++same;
		depth = same;
	}
	return depth;
}

} // namespace

std::vector<ViewChoice> BuildViewChoices (const std::vector<SavedView>& views)
{
	std::vector<ViewChoice> choices;
	choices.push_back (ViewChoice {kCurrentModelViewLabel, std::string (), std::string ()});
	for (const bool personal : {false, true}) {
		// **Der Name zuerst.** Die Auswahl ist schmal, und am Host (05.10.2026)
		// begann jede Zeile mit „Ohne Titel / Beispielausschnitte / …" — der
		// Name der Ansicht fiel hinten weg. Was alle Ansichten der Mappe
		// gemeinsam haben, unterscheidet nichts und entfällt.
		const std::size_t common = CommonFolderDepth (views, personal);
		for (const SavedView& view : views) {
			if (view.personal != personal || view.guid.empty ()) continue;
			std::string label = personal ? "Eigene: " : "";
			label += view.name.empty () ? "(ohne Namen)" : view.name;
			std::string path;
			for (std::size_t i = common; i < view.folders.size (); ++i) {
				if (view.folders[i].empty ()) continue;
				if (!path.empty ()) path += " / ";
				path += view.folders[i];
			}
			if (!path.empty ()) label += " — " + path;
			choices.push_back (ViewChoice {label, view.guid, view.name});
		}
	}
	return choices;
}

Reselection Reselect (const std::vector<ViewChoice>& choices, const std::string& guid,
					  const std::string& lastName)
{
	Reselection result;
	if (guid.empty ()) return result;
	for (std::size_t i = 1; i < choices.size (); ++i) {
		if (choices[i].guid == guid) {
			result.index = i;
			return result;
		}
	}
	const std::string name = lastName.empty () ? std::string ("Die gewählte Ansicht") :
												 "Die Ansicht „" + lastName + "“";
	result.hint = name + " gibt es nicht mehr in der Ausschnittsmappe; gewählt ist wieder die aktuelle Modellansicht.";
	return result;
}

UpdateProposal ProposeUpdate (const std::string& assignedProjectId,
							  const std::string& assignedViewpointId,
							  const std::string& selectedProjectId,
							  const std::string& listProjectId,
							  const std::vector<std::string>& listViewpointIds)
{
	UpdateProposal result;
	if (assignedViewpointId.empty () || selectedProjectId.empty ()) return result;
	// Eine Zuordnung in ein anderes Projekt ist hier kein Ziel.
	if (assignedProjectId != selectedProjectId) return result;
	// Die Liste gehört noch nicht zu diesem Projekt: abwarten, nichts wählen.
	if (listProjectId != selectedProjectId) {
		result.action = UpdateProposal::Action::Wait;
		return result;
	}
	for (std::size_t i = 0; i < listViewpointIds.size (); ++i) {
		if (listViewpointIds[i] != assignedViewpointId) continue;
		result.action = UpdateProposal::Action::Select;
		result.index = i;
		return result;
	}
	// Gelöscht oder nicht sichtbar: die verbleibende Auswahl ist kein Ersatz.
	return result;
}

ProposalStep StepProposal (const std::string& active, const std::string& marker, const UpdateProposal& proposal)
{
	ProposalStep step;
	step.active = active;
	switch (proposal.action) {
		case UpdateProposal::Action::Select:
			// Schon gesetzt: nicht noch einmal — der Nutzer darf danach umschalten.
			if (marker == active) return step;
			step.mode = ProposalStep::Mode::Update;
			step.index = proposal.index;
			step.active = marker;
			return step;
		case UpdateProposal::Action::Wait:
			// Die Liste des Projekts lädt noch: weder setzen noch zurücknehmen.
			return step;
		case UpdateProposal::Action::None:
			if (active.empty ()) return step;
			step.mode = ProposalStep::Mode::Create;
			step.active.clear ();
			return step;
	}
	return step;
}

} // namespace rtx
