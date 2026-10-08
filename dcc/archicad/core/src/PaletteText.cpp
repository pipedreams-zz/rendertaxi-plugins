#include "rtx/PaletteText.hpp"

namespace rtx {
namespace {

std::size_t Utf8Boundary (const std::string& text, std::size_t cut)
{
	while (cut > 0 && cut < text.size () &&
		   (static_cast<unsigned char> (text[cut]) & 0xC0) == 0x80)
		--cut;
	return cut;
}

} // namespace

std::string ShortenText (const std::string& text, std::size_t maxBytes)
{
	if (text.size () <= maxBytes) return text;
	return text.substr (0, Utf8Boundary (text, maxBytes)) + "…";
}

std::vector<std::string> WrapText (const std::string& text, std::size_t width)
{
	std::vector<std::string> lines;
	if (text.empty ()) return lines;
	std::string current;
	std::size_t start = 0;
	while (start <= text.size ()) {
		std::size_t space = text.find (' ', start);
		const std::string word =
			text.substr (start, space == std::string::npos ? std::string::npos : space - start);
		if (!word.empty ()) {
			if (current.empty ()) {
				current = word;
			} else if (current.size () + 1 + word.size () <= width) {
				current += " " + word;
			} else {
				lines.push_back (current);
				current = word;
			}
			while (current.size () > width) {
				lines.push_back (ShortenText (current, width));
				// Der harte Schnitt: der Rest kommt in die nächste Zeile.
				current = current.substr (Utf8Boundary (current, width));
			}
		}
		if (space == std::string::npos) break;
		start = space + 1;
	}
	if (!current.empty ()) lines.push_back (current);
	return lines;
}

std::string TargetSizeText (bool sizeFromCapture, bool frameFollowsCapture,
							const std::string& sceneSize)
{
	if (sizeFromCapture && frameFollowsCapture) {
		std::string line = sceneSize.empty () ? "Zielgröße: Aufnahmemaße"
											  : "Zielgröße: Aufnahmemaße " + sceneSize;
		return line + " (kürzer als 1536 lange Kante: Canvas-Vorgabe bleibt)";
	}
	if (sizeFromCapture)
		return "Zielgröße: Aufnahmemaße eingestellt — wirkt erst mit „Rahmen an "
			   "Aufnahme anpassen“";
	return "Zielgröße: Canvas-Vorgabe — nur das Seitenverhältnis der Aufnahme";
}

std::string PendingText (const std::string& createdAt, bool resumable)
{
	// Minutengenau reicht, und so bleibt es bei einer Zeile.
	const std::string when = createdAt.substr (0, 16);
	return resumable ? "Offener Vorgang " + when + " — wird fortgesetzt"
					 : "Vorgang " + when + " ohne lokale Dateien — wird verworfen";
}

std::vector<std::string> LayoutInfoLines (const std::vector<InfoParagraph>& paragraphs,
										  std::size_t width, std::size_t rows)
{
	std::vector<std::vector<std::string>> wrapped;
	std::vector<std::size_t> shown;
	std::size_t total = 0;
	for (const InfoParagraph& paragraph : paragraphs) {
		wrapped.push_back (WrapText (paragraph.text, width));
		shown.push_back (wrapped.back ().size ());
		total += wrapped.back ().size ();
	}

	// Der längste kürzbare Absatz gibt eine Zeile ab; bei Gleichstand der
	// spätere, weil die ersten Absätze die Aufnahme selbst beschreiben.
	while (total > rows) {
		std::size_t victim = paragraphs.size ();
		for (std::size_t i = 0; i < paragraphs.size (); ++i) {
			if (paragraphs[i].keepWhole || shown[i] <= 1) continue;
			if (victim == paragraphs.size () || shown[i] >= shown[victim]) victim = i;
		}
		if (victim == paragraphs.size ()) break;
		--shown[victim];
		--total;
	}

	std::vector<std::string> lines;
	for (std::size_t i = 0; i < paragraphs.size (); ++i) {
		for (std::size_t j = 0; j < shown[i]; ++j) {
			std::string line = wrapped[i][j];
			const bool cut = j + 1 == shown[i] && shown[i] < wrapped[i].size ();
			// „…" braucht drei Bytes; die Zeile darf dadurch nicht breiter werden.
			if (cut) line = line.size () + 3 <= width ? line + "…" : ShortenText (line, width - 3);
			lines.push_back (line);
		}
	}
	if (lines.size () > rows) lines.resize (rows);
	return lines;
}

std::string FormatCompileDate (const std::string& compileDate)
{
	static const char* const months[] = {"Jan", "Feb", "Mar", "Apr", "May", "Jun",
										 "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"};
	if (compileDate.size () != 11) return compileDate;
	int month = 0;
	for (int i = 0; i < 12; ++i)
		if (compileDate.compare (0, 3, months[i]) == 0) month = i + 1;
	const char tens = compileDate[4] == ' ' ? '0' : compileDate[4];
	const char ones = compileDate[5];
	if (month == 0 || tens < '0' || tens > '3' || ones < '0' || ones > '9') return compileDate;
	std::string text;
	text += tens;
	text += ones;
	text += month < 10 ? ".0" : ".";
	text += std::to_string (month) + "." + compileDate.substr (7, 4);
	return text;
}

std::string BuildLine (const std::string& commit, const std::string& compileDate)
{
	const std::string date = FormatCompileDate (compileDate);
	const std::string suffix = "-dirty";
	const bool dirty = commit.size () > suffix.size () &&
					   commit.compare (commit.size () - suffix.size (), suffix.size (), suffix) == 0;
	const std::string hash = dirty ? commit.substr (0, commit.size () - suffix.size ()) : commit;
	bool isHash = hash.size () >= 7 && hash.size () <= 40;
	for (const char c : hash)
		isHash = isHash && ((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'));
	if (!isHash) return "Entwicklungsbuild vom " + date;
	return "Build " + hash.substr (0, 7) + (dirty ? " (geändert)" : "") + " vom " + date;
}

} // namespace rtx
