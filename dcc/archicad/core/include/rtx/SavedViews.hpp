#pragma once

// Die gespeicherten 3D-Ansichten der Ausschnittsmappe als Auswahl der Palette
// (RTX-A-009, #255). DevKit-frei: Archicad liefert die Einträge
// (`HostInfo.cpp`, `ListSaved3DViews`), hier entstehen daraus die Zeilen der
// Auswahl und das Wiederfinden der gewählten Ansicht nach einem Auffrischen.
//
// Nutzerentscheidung vom 05.10.2026: die gespeicherten 3D-Ansichten stehen zur
// Wahl, **zuoberst** steht die aktuelle freie Modellansicht.

#include <cstddef>
#include <string>
#include <vector>

namespace rtx {

/** Ein Eintrag der Ausschnittsmappe, der das 3D-Fenster zeigt. */
struct SavedView {
	/** GUID des Navigatoreintrags als Text — der stabile Schlüssel. */
	std::string guid;
	/** Name, wie er in der Ausschnittsmappe steht. */
	std::string name;
	/** Ordner von der Mappe bis zum Eintrag, ohne die Mappe selbst. */
	std::vector<std::string> folders;
	/** Aus der eigenen Mappe („Meine Ausschnitte") statt der öffentlichen. */
	bool personal = false;
};

/** Eine Zeile der Auswahl. Zeile 0 ist immer die aktuelle Modellansicht (leere GUID). */
struct ViewChoice {
	std::string label;
	std::string guid;
	std::string name;
};

extern const char* const kCurrentModelViewLabel;

/**
 * Die Zeilen der Auswahl: „Aktuelle Modellansicht", dann die Ansichten in der
 * Reihenfolge der Mappe — erst die öffentliche, dann die eigene. Die Beschriftung
 * nennt den Namen zuerst und danach den Ordnerpfad („Süd — Außen"), damit
 * gleichnamige Ansichten in verschiedenen Ordnern unterscheidbar bleiben.
 * Ordner, die alle Ansichten einer Mappe gemeinsam haben, entfallen; die
 * eigene Mappe ist markiert.
 */
std::vector<ViewChoice> BuildViewChoices (const std::vector<SavedView>& views);

/** Wohin die Auswahl nach einem Auffrischen zeigt, und was der Nutzer dazu wissen muss. */
struct Reselection {
	std::size_t index = 0;
	/** Leer, oder ein Satz, warum wieder die aktuelle Modellansicht gewählt ist. */
	std::string hint;
};

/**
 * Die zuvor gewählte Ansicht in der neuen Liste wiederfinden — über die GUID,
 * nie über den Namen (umbenannt bleibt gewählt). Gibt es sie nicht mehr, ist
 * wieder die aktuelle Modellansicht gewählt, mit Hinweis (Regel 3: ein
 * gemerkter Zustand verhindert nichts).
 */
Reselection Reselect (const std::vector<ViewChoice>& choices, const std::string& guid,
					  const std::string& lastName);

/**
 * Ob die gemerkte Zuordnung einer gewählten Ansicht den Update-Modus setzen
 * darf (F-01 an PR #259). Nur wenn die Zuordnung zum gewählten Projekt gehört,
 * die geladene Liste zu diesem Projekt gehört und genau der zugeordnete
 * Blickpunkt darin steht. Sonst wird nichts automatisch zum Update-Ziel.
 */
struct UpdateProposal {
	enum class Action { None, Wait, Select };
	Action action = Action::None;
	/** Index des zugeordneten Blickpunkts in der Liste, nur bei `Select`. */
	std::size_t index = 0;
};

UpdateProposal ProposeUpdate (const std::string& assignedProjectId,
							  const std::string& assignedViewpointId,
							  const std::string& selectedProjectId,
							  const std::string& listProjectId,
							  const std::vector<std::string>& listViewpointIds);

/**
 * Was die Palette mit dem Modus tun soll, nachdem `ProposeUpdate` entschieden
 * hat — **auch zurücknehmen** (F-01, Hostprobe am 07.10.2026).
 *
 * `active` ist der Merker des Vorschlags, den die Palette selbst gesetzt hat
 * (leer, wenn keiner gilt), `marker` der für Ansicht, Projekt und Blickpunkt
 * von jetzt. Galt ein eigener Vorschlag und passt er nicht mehr — anderes
 * Projekt, Blickpunkt gelöscht, keine Ansicht mehr gewählt —, geht die
 * Palette zurück auf „Neuer Blickpunkt". Sonst bliebe „aktualisieren" stehen,
 * und die neu gefüllte Liste machte ihren ersten Eintrag still zum Ziel.
 *
 * Eine eigene Wahl des Nutzers bleibt unberührt: Hat er nach dem Vorschlag
 * selbst umgeschaltet, gilt derselbe Merker weiter (`Keep`); hat die Palette
 * keinen Vorschlag gesetzt, nimmt sie auch nichts zurück.
 */
struct ProposalStep {
	enum class Mode { Keep, Update, Create };
	Mode mode = Mode::Keep;
	/** Index des Blickpunkts in der Liste, nur bei `Update`. */
	std::size_t index = 0;
	/** Der Merker danach; leer, wenn kein eigener Vorschlag mehr gilt. */
	std::string active;
};

ProposalStep StepProposal (const std::string& active, const std::string& marker, const UpdateProposal& proposal);

} // namespace rtx
