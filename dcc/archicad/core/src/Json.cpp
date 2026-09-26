#include "rtx/Json.hpp"

#include <cmath>
#include <cstdio>
#include <cstdlib>

namespace rtx {
namespace {

void AppendCodepoint (std::string& out, unsigned int cp)
{
	if (cp < 0x80) {
		out += static_cast<char> (cp);
	} else if (cp < 0x800) {
		out += static_cast<char> (0xC0 | (cp >> 6));
		out += static_cast<char> (0x80 | (cp & 0x3F));
	} else if (cp < 0x10000) {
		out += static_cast<char> (0xE0 | (cp >> 12));
		out += static_cast<char> (0x80 | ((cp >> 6) & 0x3F));
		out += static_cast<char> (0x80 | (cp & 0x3F));
	} else {
		out += static_cast<char> (0xF0 | (cp >> 18));
		out += static_cast<char> (0x80 | ((cp >> 12) & 0x3F));
		out += static_cast<char> (0x80 | ((cp >> 6) & 0x3F));
		out += static_cast<char> (0x80 | (cp & 0x3F));
	}
}

class Parser final {
public:
	explicit Parser (const std::string& source) : text (source) {}

	JsonPtr ParseDocument ()
	{
		SkipSpace ();
		JsonPtr value = ParseValue ();
		if (value == nullptr) return nullptr;
		SkipSpace ();
		if (pos != text.size ()) return nullptr;
		return value;
	}

private:
	void SkipSpace ()
	{
		while (pos < text.size ()) {
			const char c = text[pos];
			if (c == ' ' || c == '\t' || c == '\n' || c == '\r') ++pos;
			else break;
		}
	}

	bool Literal (const char* word)
	{
		const std::size_t length = std::char_traits<char>::length (word);
		if (text.compare (pos, length, word) != 0) return false;
		pos += length;
		return true;
	}

	JsonPtr ParseValue ()
	{
		if (depth > 64) return nullptr;
		if (pos >= text.size ()) return nullptr;
		const char c = text[pos];
		if (c == '{') return ParseObject ();
		if (c == '[') return ParseArray ();
		if (c == '"') {
			std::string value;
			if (!ParseString (value)) return nullptr;
			return Json::MakeString (value);
		}
		if (Literal ("true")) return Json::MakeBool (true);
		if (Literal ("false")) return Json::MakeBool (false);
		if (Literal ("null")) return Json::MakeNull ();
		return ParseNumber ();
	}

	bool ParseString (std::string& out)
	{
		if (pos >= text.size () || text[pos] != '"') return false;
		++pos;
		out.clear ();
		while (pos < text.size ()) {
			const unsigned char c = static_cast<unsigned char> (text[pos]);
			if (c == '"') { ++pos; return true; }
			if (c == '\\') {
				++pos;
				if (pos >= text.size ()) return false;
				const char esc = text[pos++];
				switch (esc) {
					case '"': out += '"'; break;
					case '\\': out += '\\'; break;
					case '/': out += '/'; break;
					case 'b': out += '\b'; break;
					case 'f': out += '\f'; break;
					case 'n': out += '\n'; break;
					case 'r': out += '\r'; break;
					case 't': out += '\t'; break;
					case 'u': {
						unsigned int cp = 0;
						if (!ParseHex4 (cp)) return false;
						if (cp >= 0xD800 && cp <= 0xDBFF && pos + 1 < text.size () &&
							text[pos] == '\\' && text[pos + 1] == 'u') {
							pos += 2;
							unsigned int low = 0;
							if (!ParseHex4 (low)) return false;
							if (low >= 0xDC00 && low <= 0xDFFF)
								cp = 0x10000 + ((cp - 0xD800) << 10) + (low - 0xDC00);
							else
								AppendCodepoint (out, low);
						}
						AppendCodepoint (out, cp);
						break;
					}
					default: return false;
				}
				continue;
			}
			if (c < 0x20) return false;
			out += text[pos++];
		}
		return false;
	}

	bool ParseHex4 (unsigned int& out)
	{
		if (pos + 4 > text.size ()) return false;
		out = 0;
		for (int i = 0; i < 4; ++i) {
			const char c = text[pos++];
			out <<= 4;
			if (c >= '0' && c <= '9') out |= static_cast<unsigned int> (c - '0');
			else if (c >= 'a' && c <= 'f') out |= static_cast<unsigned int> (c - 'a' + 10);
			else if (c >= 'A' && c <= 'F') out |= static_cast<unsigned int> (c - 'A' + 10);
			else return false;
		}
		return true;
	}

	JsonPtr ParseNumber ()
	{
		const std::size_t start = pos;
		if (pos < text.size () && text[pos] == '-') ++pos;
		bool digits = false, fraction = false;
		while (pos < text.size ()) {
			const char c = text[pos];
			if (c >= '0' && c <= '9') { digits = true; ++pos; continue; }
			if (c == '.' || c == 'e' || c == 'E') { fraction = true; ++pos; continue; }
			if ((c == '-' || c == '+') && pos > start &&
				(text[pos - 1] == 'e' || text[pos - 1] == 'E')) { ++pos; continue; }
			break;
		}
		if (!digits) return nullptr;
		const std::string token = text.substr (start, pos - start);
		char* end = nullptr;
		if (!fraction) {
			const long long value = std::strtoll (token.c_str (), &end, 10);
			if (end != nullptr && *end == '\0') return Json::MakeInt (value);
		}
		end = nullptr;
		const double value = std::strtod (token.c_str (), &end);
		if (end == nullptr || *end != '\0') return nullptr;
		// Sechs Nachkommastellen: die einzige Gleitkommaklasse des Vertrags
		// (`length`) für `assets[].depth.near` und `.far`.
		return Json::MakeDecimal (value, 6);
	}

	JsonPtr ParseArray ()
	{
		++pos;
		++depth;
		JsonPtr node = Json::MakeArray ();
		SkipSpace ();
		if (pos < text.size () && text[pos] == ']') { ++pos; --depth; return node; }
		while (true) {
			SkipSpace ();
			JsonPtr item = ParseValue ();
			if (item == nullptr) return nullptr;
			node->Append (item);
			SkipSpace ();
			if (pos >= text.size ()) return nullptr;
			if (text[pos] == ',') { ++pos; continue; }
			if (text[pos] == ']') { ++pos; --depth; return node; }
			return nullptr;
		}
	}

	JsonPtr ParseObject ()
	{
		++pos;
		++depth;
		JsonPtr node = Json::MakeObject ();
		SkipSpace ();
		if (pos < text.size () && text[pos] == '}') { ++pos; --depth; return node; }
		while (true) {
			SkipSpace ();
			std::string key;
			if (!ParseString (key)) return nullptr;
			SkipSpace ();
			if (pos >= text.size () || text[pos] != ':') return nullptr;
			++pos;
			SkipSpace ();
			JsonPtr value = ParseValue ();
			if (value == nullptr) return nullptr;
			node->Set (key, value);
			SkipSpace ();
			if (pos >= text.size ()) return nullptr;
			if (text[pos] == ',') { ++pos; continue; }
			if (text[pos] == '}') { ++pos; --depth; return node; }
			return nullptr;
		}
	}

	const std::string& text;
	std::size_t pos = 0;
	int depth = 0;
};

} // namespace

std::string JsonQuote (const std::string& value)
{
	std::string out;
	out.reserve (value.size () + 2);
	out += '"';
	for (const char raw : value) {
		const unsigned char c = static_cast<unsigned char> (raw);
		switch (c) {
			case '"': out += "\\\""; break;
			case '\\': out += "\\\\"; break;
			case '\b': out += "\\b"; break;
			case '\f': out += "\\f"; break;
			case '\n': out += "\\n"; break;
			case '\r': out += "\\r"; break;
			case '\t': out += "\\t"; break;
			default:
				if (c < 0x20) {
					char escape[7];
					std::snprintf (escape, sizeof escape, "\\u%04x", c);
					out += escape;
				} else {
					out += raw;
				}
		}
	}
	out += '"';
	return out;
}

JsonPtr Json::MakeNull ()
{
	JsonPtr node = std::make_shared<Json> ();
	node->kind = Kind::Null;
	return node;
}

JsonPtr Json::MakeBool (bool value)
{
	JsonPtr node = std::make_shared<Json> ();
	node->kind = Kind::Bool;
	node->boolean = value;
	return node;
}

JsonPtr Json::MakeInt (std::int64_t value)
{
	JsonPtr node = std::make_shared<Json> ();
	node->kind = Kind::Number;
	node->integer = value;
	node->number = static_cast<double> (value);
	node->isInteger = true;
	return node;
}

JsonPtr Json::MakeDecimal (double value, int places)
{
	JsonPtr node = std::make_shared<Json> ();
	node->kind = Kind::Number;
	node->number = value;
	node->isInteger = false;
	node->decimals = places;
	return node;
}

JsonPtr Json::MakeString (std::string value)
{
	JsonPtr node = std::make_shared<Json> ();
	node->kind = Kind::String;
	node->text = std::move (value);
	return node;
}

JsonPtr Json::MakeArray ()
{
	JsonPtr node = std::make_shared<Json> ();
	node->kind = Kind::Array;
	return node;
}

JsonPtr Json::MakeObject ()
{
	JsonPtr node = std::make_shared<Json> ();
	node->kind = Kind::Object;
	return node;
}

void Json::Set (const std::string& key, JsonPtr value)
{
	if (kind != Kind::Object) return;
	for (auto& field : object) {
		if (field.first == key) { field.second = std::move (value); return; }
	}
	object.emplace_back (key, std::move (value));
}

void Json::Append (JsonPtr value)
{
	if (kind != Kind::Array) return;
	array.push_back (std::move (value));
}

JsonPtr Json::Get (const std::string& key) const
{
	if (kind != Kind::Object) return nullptr;
	for (const auto& field : object) {
		if (field.first == key) return field.second;
	}
	return nullptr;
}

bool Json::BoolOr (bool fallback) const
{
	return kind == Kind::Bool ? boolean : fallback;
}

std::int64_t Json::IntOr (std::int64_t fallback) const
{
	if (kind != Kind::Number) return fallback;
	return isInteger ? integer : static_cast<std::int64_t> (number);
}

double Json::NumberOr (double fallback) const
{
	return kind == Kind::Number ? number : fallback;
}

std::string Json::StringOr (const std::string& fallback) const
{
	return kind == Kind::String ? text : fallback;
}

void Json::Write (std::string& out, int indent, int depth) const
{
	const std::string pad = indent > 0 ? std::string (static_cast<std::size_t> (indent * (depth + 1)), ' ') : std::string ();
	const std::string padEnd = indent > 0 ? std::string (static_cast<std::size_t> (indent * depth), ' ') : std::string ();
	const char* newline = indent > 0 ? "\n" : "";
	const char* colon = indent > 0 ? ": " : ":";

	switch (kind) {
		case Kind::Null: out += "null"; break;
		case Kind::Bool: out += boolean ? "true" : "false"; break;
		case Kind::Number: {
			char buffer[64];
			if (isInteger) {
				std::snprintf (buffer, sizeof buffer, "%lld", static_cast<long long> (integer));
			} else {
				std::snprintf (buffer, sizeof buffer, "%.*f", decimals, number);
			}
			out += buffer;
			break;
		}
		case Kind::String: out += JsonQuote (text); break;
		case Kind::Array: {
			if (array.empty ()) { out += "[]"; break; }
			out += '[';
			out += newline;
			for (std::size_t i = 0; i < array.size (); ++i) {
				out += pad;
				array[i]->Write (out, indent, depth + 1);
				if (i + 1 < array.size ()) out += ',';
				out += newline;
			}
			out += padEnd;
			out += ']';
			break;
		}
		case Kind::Object: {
			if (object.empty ()) { out += "{}"; break; }
			out += '{';
			out += newline;
			for (std::size_t i = 0; i < object.size (); ++i) {
				out += pad;
				out += JsonQuote (object[i].first);
				out += colon;
				object[i].second->Write (out, indent, depth + 1);
				if (i + 1 < object.size ()) out += ',';
				out += newline;
			}
			out += padEnd;
			out += '}';
			break;
		}
	}
}

std::string Json::Serialize () const
{
	std::string out;
	Write (out, 0, 0);
	return out;
}

std::string Json::SerializePretty () const
{
	std::string out;
	Write (out, 2, 0);
	out += '\n';
	return out;
}

JsonPtr Json::Parse (const std::string& text)
{
	Parser parser (text);
	return parser.ParseDocument ();
}

} // namespace rtx
