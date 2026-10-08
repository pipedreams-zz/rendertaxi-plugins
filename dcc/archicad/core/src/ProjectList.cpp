#include "rtx/ProjectList.hpp"

#include "rtx/Ids.hpp"
#include "rtx/PaletteText.hpp"

namespace rtx {
namespace {

bool IsSpace (char c)
{
	return c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\f' || c == '\v';
}

/** Zeichen statt Bytes: „Büro" ist vier Zeichen lang, wie auf der Plattform. */
std::size_t CodePoints (const std::string& text)
{
	std::size_t count = 0;
	for (const char c : text)
		if ((static_cast<unsigned char> (c) & 0xC0) != 0x80) ++count;
	return count;
}

} // namespace

std::string TrimProjectName (const std::string& name)
{
	std::size_t begin = 0;
	std::size_t end = name.size ();
	while (begin < end && IsSpace (name[begin])) ++begin;
	while (end > begin && IsSpace (name[end - 1])) --end;
	return name.substr (begin, end - begin);
}

std::string ProjectNameProblem (const std::string& name)
{
	const std::string text = TrimProjectName (name);
	if (text.empty ()) return "Bitte einen Namen für das neue Projekt eingeben.";
	if (CodePoints (text) > kProjectNameMaxLength)
		return "Ein Projektname hat höchstens " + std::to_string (kProjectNameMaxLength) +
			   " Zeichen.";
	return {};
}

std::string NewProjectIntent::KeyFor (const std::string& name)
{
	const std::string text = TrimProjectName (name);
	if (pendingKey.empty () || pendingName != text) {
		pendingName = text;
		pendingKey = NewUuidV7 ();
	}
	return pendingKey;
}

void NewProjectIntent::Done (const std::string& name)
{
	if (!pendingKey.empty () && pendingName == TrimProjectName (name)) {
		pendingName.clear ();
		pendingKey.clear ();
	}
}

ListReselection ReselectById (const std::vector<std::string>& ids, const std::string& wantedId)
{
	ListReselection result;
	if (wantedId.empty ()) return result;
	for (std::size_t i = 0; i < ids.size (); ++i) {
		if (ids[i] != wantedId) continue;
		result.index = i;
		result.kept = true;
		break;
	}
	return result;
}

bool RefreshGate::Allow (Clock::time_point now)
{
	if (loaded && now - last < interval) return false;
	Mark (now);
	return true;
}

void RefreshGate::Mark (Clock::time_point now)
{
	last = now;
	loaded = true;
}

ResolvedTarget ResolveTarget (const ShownList<ProjectSummary>& projects, short projectItem,
							  const ShownList<ViewpointSummary>& viewpoints, short viewpointItem,
							  bool update)
{
	ResolvedTarget target;
	if (projects.Entries ().empty ()) {
		target.problem = "Kein Projekt zur Auswahl.";
		return target;
	}
	const ProjectSummary* project = projects.At (projectItem);
	if (project == nullptr) {
		target.problem = "Bitte ein Projekt wählen.";
		return target;
	}
	target.project = *project;
	if (!update) return target;
	// **Die Liste muss zu diesem Projekt gehören.** Ein Update an einen
	// projektfremden Blickpunkt ist kein Fehler, den der Server ausbaden soll.
	if (viewpoints.Owner () != project->id) {
		target.problem = "Die Blickpunkte dieses Projekts werden noch geladen.";
		return target;
	}
	const ViewpointSummary* viewpoint = viewpoints.At (viewpointItem);
	if (viewpoint == nullptr) {
		target.problem = "Bitte einen bestehenden Blickpunkt wählen.";
		return target;
	}
	target.viewpoint = *viewpoint;
	return target;
}

std::string PopupLabel (const std::string& name)
{
	return ShortenText (name, kPopupLabelWidth);
}

} // namespace rtx
