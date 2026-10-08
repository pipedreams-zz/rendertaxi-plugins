// Projekt anlegen und die Listen der Palette neu laden (RTX-A-011, #281).
//
// DevKit-frei, damit die Zusagen prüfbar sind, die die Palette selbst ohne
// Archicad nicht belegen kann:
//
// - Eine Projektanlage, deren Antwort verloren ging, legt beim zweiten Versuch
//   **kein zweites Projekt** an — derselbe Name trägt denselben
//   Idempotenzschlüssel, bis die Anlage gelungen ist (wie `NewProject` im
//   gemeinsamen Python-Client).
// - Nach dem Neuladen bleibt gewählt, was noch existiert; sonst gilt der erste
//   Eintrag (Regel 3 des Auftrags: ein gemerkter Zustand verhindert nie das
//   Laden).
// - Das Neuladen beim Fokuswechsel geschieht höchstens alle fünf Sekunden.
#pragma once

#include <chrono>
#include <cstddef>
#include <string>
#include <utility>
#include <vector>

#include "rtx/PluginApi.hpp"

namespace rtx {

/** Dieselbe Grenze wie die Plattform (`projectNameSchema`), in Zeichen gezählt. */
constexpr std::size_t kProjectNameMaxLength = 120;

/** Der Name ohne Leerraum an den Enden — so, wie er gesendet wird. */
std::string TrimProjectName (const std::string& name);

/**
 * Warum dieser Name kein Projektname ist, oder leer. Nur die beiden Grenzen,
 * die der Client ohne Server kennt (leer, zu lang); alles Weitere prüft der
 * Server, und die Palette zeigt seine Antwort.
 */
std::string ProjectNameProblem (const std::string& name);

/**
 * Eine Projektanlage aus der Palette, gegen doppelte Anlage gesichert.
 *
 * Der Idempotenzschlüssel gehört zur **Absicht**, nicht zum Klick: Wer nach
 * einem Fehlschlag (Netz weg, Antwort verloren) denselben Namen noch einmal
 * bestätigt, sendet denselben Schlüssel, und die Plattform liefert das
 * Projekt, das sie schon angelegt hat. Ein anderer Name ist eine neue Absicht
 * mit neuem Schlüssel. Nach dem Erfolg ist die Absicht erledigt.
 *
 * Der Schlüssel ist eine zufällige UUIDv7 und nie aus dem Namen abgeleitet.
 */
class NewProjectIntent final {
public:
	std::string KeyFor (const std::string& name);
	void Done (const std::string& name);

private:
	std::string pendingName;
	std::string pendingKey;
};

/** Wohin die Auswahl nach dem Neuladen einer Liste zeigt. */
struct ListReselection {
	/** 0-basiert; bei leerer Liste 0 und ohne Bedeutung. */
	std::size_t index = 0;
	/** Steht der gemerkte Eintrag noch in der Liste? */
	bool kept = false;
};

/**
 * Den zuvor gewählten Eintrag über seine Kennung wiederfinden — nie über den
 * Namen, denn ein umbenanntes Projekt ist dasselbe Projekt. Fehlt er, gilt der
 * erste Eintrag.
 */
ListReselection ReselectById (const std::vector<std::string>& ids, const std::string& wantedId);

/** Was das Übernehmen einer neu geladenen Liste für die Auswahl bedeutet. */
struct ListChange {
	/** DG-Stelle (1-basiert) des danach gewählten Eintrags; 0 bei leerer Liste. */
	short item = 0;
	/** Der zuvor gewählte Eintrag steht noch in der Liste. */
	bool kept = false;
	/**
	 * Der gewählte Eintrag ist **aus derselben Liste** verschwunden (entfernt,
	 * nicht umsortiert). Ein Update-Ziel darf dann nicht still auf den ersten
	 * Eintrag übergehen; die Palette geht auf „Neuer Blickpunkt" zurück (F-01
	 * an PR #292).
	 */
	bool lost = false;
};

/**
 * **Die Liste, wie sie in einer Auswahl der Palette steht** (F-01 an PR #292).
 *
 * Der Arbeitsfaden ersetzt die geladenen Listen, bevor der Hauptfaden die
 * Auswahl neu aufbaut. Wer dazwischen eine Stelle der Auswahl gegen die
 * **geladene** Liste liest, trifft nach einem Umsortieren einen anderen
 * Eintrag als den sichtbar gewählten. Deshalb liest jeder, der eine Stelle in
 * einen Eintrag übersetzt, nur diesen Stand — und er ändert sich ausschließlich
 * zusammen mit der Auswahl selbst.
 *
 * `owner` ist, wozu die Liste gehört (bei Blickpunkten das Projekt).
 */
template <typename Entry>
class ShownList final {
public:
	const std::vector<Entry>& Entries () const { return entries; }
	const std::string& Owner () const { return owner; }

	/** Der Eintrag an DG-Stelle `item` (1-basiert, wie DG zählt); `nullptr` außerhalb. */
	const Entry* At (short item) const
	{
		if (item < 1 || static_cast<std::size_t> (item) > entries.size ()) return nullptr;
		return &entries[static_cast<std::size_t> (item - 1)];
	}

	/** DG-Stelle des Eintrags mit dieser Kennung; 0, wenn er fehlt. */
	short Find (const std::string& id) const
	{
		for (std::size_t i = 0; i < entries.size (); ++i)
			if (entries[i].id == id) return static_cast<short> (i + 1);
		return 0;
	}

	/**
	 * Übernimmt eine neu geladene Liste. Gewählt bleibt `keepId`, wenn es ihn
	 * noch gibt, sonst der erste Eintrag (Regel 3).
	 */
	ListChange Replace (std::vector<Entry> next, std::string nextOwner, const std::string& keepId)
	{
		std::vector<std::string> ids;
		for (const Entry& entry : next) ids.push_back (entry.id);
		const ListReselection again = ReselectById (ids, keepId);
		ListChange change;
		change.kept = again.kept;
		change.item = next.empty () ? 0 : static_cast<short> (again.index + 1);
		change.lost = !keepId.empty () && !again.kept && nextOwner == owner && Find (keepId) != 0;
		entries = std::move (next);
		owner = std::move (nextOwner);
		return change;
	}

private:
	std::vector<Entry> entries;
	std::string owner;
};

/** Das Ziel einer Übernahme, aufgelöst aus der **sichtbaren** Auswahl. */
struct ResolvedTarget {
	/** Leer, oder ein Satz, warum keine Übernahme beginnt. */
	std::string problem;
	ProjectSummary project;
	/** Nur bei `update`. */
	ViewpointSummary viewpoint;
};

/**
 * Löst Projekt und — bei `update` — Blickpunkt aus den DG-Stellen der beiden
 * Auswahlen gegen die **dargestellten** Listen auf. Gehört die
 * Blickpunktliste nicht zum gewählten Projekt, beginnt nichts.
 */
ResolvedTarget ResolveTarget (const ShownList<ProjectSummary>& projects, short projectItem,
							  const ShownList<ViewpointSummary>& viewpoints, short viewpointItem,
							  bool update);

/**
 * Lässt ein Neuladen höchstens alle `interval` durch.
 *
 * Für das Neuladen beim Zurückkehren des Fokus: ein Fenster, das zwischen
 * Palette und Modell hin und her wechselt, soll den Server nicht mit jedem
 * Klick befragen. Ein Klick auf „Aktualisieren" geht an der Sperre vorbei und
 * setzt sie trotzdem.
 */
class RefreshGate final {
public:
	using Clock = std::chrono::steady_clock;

	explicit RefreshGate (Clock::duration interval) : interval (interval) {}

	/** Darf jetzt neu geladen werden? Wenn ja, zählt das als Laden. */
	bool Allow (Clock::time_point now);
	/** Ein Laden aus anderem Grund (Knopf, Öffnen) — die Sperre beginnt neu. */
	void Mark (Clock::time_point now);

private:
	Clock::duration interval;
	Clock::time_point last {};
	bool loaded = false;
};

/**
 * Die Beschriftung eines Eintrags in einer Auswahlliste der Palette.
 *
 * Lange Namen enden auf „…" — **am Ende**, nicht in der Mitte. DG kürzt einen
 * zu langen Eintrag unter Windows selbst, aber in der Mitte, und dann steht
 * vom Projektnamen weder der Anfang noch das Ende lesbar da (Abnahme vom
 * 07.10.2026). Den vollen Namen zeigt der Tooltip.
 */
std::string PopupLabel (const std::string& name);

/** Bytes, die ein Eintrag der Auswahllisten (212 Punkte breit) sicher trägt. */
constexpr std::size_t kPopupLabelWidth = 34;

} // namespace rtx
