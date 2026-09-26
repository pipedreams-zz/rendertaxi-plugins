// Sehr kleiner JSON-Baum: genug, um ein Capture-Manifest zu schreiben und die
// Antworten der Plugin API zu lesen. Bewusst keine fremde Bibliothek — die
// Reiferichtlinie aus `AGENTS.md` gilt für jede neu aufgenommene Abhängigkeit,
// und ein Add-On, das in einem fremden Prozess läuft, trägt lieber wenig Code
// als einen weiteren Fremdkörper.
//
// Objekte behalten ihre Einfügereihenfolge. Das ist keine Bequemlichkeit,
// sondern Bedingung: `manifestHash` geht nach `architecture.md`, Abschnitt 8.7
// über die **Rohbytes** der Manifestdatei, also muss dieselbe Struktur immer
// dieselben Bytes ergeben.
#pragma once

#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace rtx {

class Json;
using JsonPtr = std::shared_ptr<Json>;

class Json final {
public:
	enum class Kind { Null, Bool, Number, String, Array, Object };

	static JsonPtr MakeNull ();
	static JsonPtr MakeBool (bool value);
	/** Ganzzahl; sie wird ohne Nachkommastellen geschrieben. */
	static JsonPtr MakeInt (std::int64_t value);
	/** Gleitkommazahl; sie wird mit der Stellenzahl ihrer Präzisionsklasse geschrieben. */
	static JsonPtr MakeDecimal (double value, int decimals);
	static JsonPtr MakeString (std::string value);
	static JsonPtr MakeArray ();
	static JsonPtr MakeObject ();

	Kind GetKind () const { return kind; }
	bool IsNull () const { return kind == Kind::Null; }

	/** Objektfeld setzen oder ersetzen; die Reihenfolge bleibt die des ersten Setzens. */
	void Set (const std::string& key, JsonPtr value);
	void Append (JsonPtr value);

	/** Objektfeld lesen; `nullptr`, wenn es fehlt oder dies kein Objekt ist. */
	JsonPtr Get (const std::string& key) const;
	const std::vector<JsonPtr>& Items () const { return array; }
	const std::vector<std::pair<std::string, JsonPtr>>& Fields () const { return object; }

	bool BoolOr (bool fallback) const;
	std::int64_t IntOr (std::int64_t fallback) const;
	double NumberOr (double fallback) const;
	std::string StringOr (const std::string& fallback) const;

	/** Kompakte Schreibweise ohne Leerzeichen. */
	std::string Serialize () const;
	/** Eingerückte Schreibweise mit zwei Leerzeichen je Ebene und Zeilenende. */
	std::string SerializePretty () const;

	/** Liefert `nullptr`, wenn der Text kein gültiges JSON ist. */
	static JsonPtr Parse (const std::string& text);

private:
	void Write (std::string& out, int indent, int depth) const;

	Kind kind = Kind::Null;
	bool boolean = false;
	double number = 0.0;
	std::int64_t integer = 0;
	bool isInteger = false;
	int decimals = 0;
	std::string text;
	std::vector<JsonPtr> array;
	std::vector<std::pair<std::string, JsonPtr>> object;
};

/** Maskiert eine Zeichenkette als JSON-String samt Anführungszeichen. */
std::string JsonQuote (const std::string& value);

} // namespace rtx
