// Die Belegung der Infozeilen im Bereich „Bild übernehmen".
//
// Issue #88, Punkt 17, und Befund F-02 an PR #152: Die Zeile „Zielgröße: …"
// wurde erst mitten im Satz abgeschnitten und konnte danach ganz wegfallen.
// Die Zusage lautet jetzt: **Zielgröße und offener Vorgang stehen immer
// vollständig da, und kein Absatz fällt ganz weg** — auch im ungünstigsten
// Fall, den die Palette erzeugen kann.
#include "Testing.hpp"

#include <string>
#include <vector>

#include "rtx/PaletteText.hpp"

using namespace rtx;

namespace {

/** Enthält `lines` die Zeilen `part` lückenlos und in dieser Reihenfolge? */
bool ContainsRun (const std::vector<std::string>& lines, const std::vector<std::string>& part)
{
	if (part.empty () || part.size () > lines.size ()) return false;
	for (std::size_t i = 0; i + part.size () <= lines.size (); ++i) {
		bool all = true;
		for (std::size_t j = 0; j < part.size () && all; ++j) all = lines[i + j] == part[j];
		if (all) return true;
	}
	return false;
}

bool SomeLineStartsWith (const std::vector<std::string>& lines, const std::string& prefix)
{
	for (const std::string& line : lines)
		if (line.compare (0, prefix.size (), prefix) == 0) return true;
	return false;
}

/**
 * Der ungünstigste Fall aus F-02, mit den längsten Texten, die die Palette
 * bilden kann: 3D-Update, langer Stilname (auf 24 Zeichen gekürzt, wie in
 * der Palette), Renderweg, gespeicherter und abweichender Zielrahmen samt
 * Hinweis auf das bisherige Basisbild, gemerkte Zuordnung mit langem Namen
 * und ein offener Vorgang.
 */
std::vector<InfoParagraph> WorstCase (const std::string& sizeLine, const std::string& pending)
{
	return {
		{"Ansicht: Fenstergröße, „" + ShortenText ("Schattiert mit Konturen und Schlagschatten", 24) +
			 "“, Zuschnitt auf 1.778:1",
		 false},
		{"Rendern: 10000 x 10000 · 1.778:1 (Photorealistik-Einstellungen)", false},
		{"Zielrahmen: " + ShortenText ("Benutzerdefiniert, 4096 x 2304 Pixel, Druckformat", 40) +
			 " · 1.778:1",
		 false},
		{"Abgleich: Format weicht ab (1.778:1 statt 1.333:1) — Rahmen bleibt  ·  Das bisherige "
		 "Basisbild bleibt als Referenz erhalten.",
		 false},
		{sizeLine, true},
		{"Aus dieser Ansicht zuletzt nach „" +
			 ShortenText ("Nordansicht Eingang mit Vordach und Außentreppe", 34) + "“",
		 false},
		{pending, true},
	};
}

const std::string kSceneSize = "10000 x 10000 · 1.778:1";
const std::string kCreatedAt = "2026-09-25T12:34:56.789Z";

/** Ein Server vor 1.7.0 und der breiteste Satz, den einer ab 1.7.0 hervorbringt (RTX-P-015). */
const CanvasDefault kNoCanvas {};
const CanvasDefault kWidestCanvas {true, "21:9", 4096};

std::vector<std::string> AllSizeLines ()
{
	std::vector<std::string> lines;
	for (const CanvasDefault& canvas : {kNoCanvas, kWidestCanvas}) {
		for (const std::string& line :
			 {TargetSizeText (true, true, kSceneSize, canvas), TargetSizeText (true, true, "", canvas),
			  TargetSizeText (true, false, kSceneSize, canvas),
			  TargetSizeText (false, true, kSceneSize, canvas), TargetSizeText (false, false, "", canvas)})
			lines.push_back (line);
	}
	return lines;
}

} // namespace

RTX_TEST (ZielgroesseUndVorgangPassenInDenReservierbarenPlatz)
{
	// Die Rechnung hinter der Zusage: fünf kürzbare Absätze brauchen
	// mindestens fünf Zeilen, also dürfen Zielgröße und Vorgang zusammen
	// höchstens drei belegen.
	for (const std::string& size : AllSizeLines ())
		RTX_CHECK (WrapText (size, kPaletteLineWidth).size () <= 2);
	RTX_CHECK_EQ (WrapText (PendingText (kCreatedAt, true), kPaletteLineWidth).size (),
				  std::size_t (1));
	RTX_CHECK_EQ (WrapText (PendingText (kCreatedAt, false), kPaletteLineWidth).size (),
				  std::size_t (1));
}

RTX_TEST (ZielgroesseStehtImUnguenstigstenFallVollstaendigDa)
{
	// F-02: zweizeilige Aufnahme, Renderweg, Zielrahmen, dreizeiliger Abgleich
	// — vorher fiel die Zielgröße dahinter ganz weg.
	for (const std::string& size : AllSizeLines ()) {
		for (const bool resumable : {true, false}) {
			const std::string pending = PendingText (kCreatedAt, resumable);
			const std::vector<std::string> lines = LayoutInfoLines (
				WorstCase (size, pending), kPaletteLineWidth, kPaletteInfoRows);

			RTX_CHECK (lines.size () <= kPaletteInfoRows);
			RTX_CHECK (ContainsRun (lines, WrapText (size, kPaletteLineWidth)));
			RTX_CHECK (ContainsRun (lines, {pending}));
			// Kein Absatz fällt ganz weg.
			for (const char* prefix :
				 {"Ansicht:", "Rendern:", "Zielrahmen:", "Abgleich:", "Aus dieser Ansicht"})
				RTX_CHECK (SomeLineStartsWith (lines, prefix));
			for (const std::string& line : lines) RTX_CHECK (line.size () <= kPaletteLineWidth);
		}
	}
}

RTX_TEST (WirksameAufnahmemasseStehenVollstaendigDa)
{
	// Der zweite Fall aus F-02: die Aufnahmemaße wirken, und die Zeile nennt
	// sie samt Hinweis auf die Canvas-Vorgabe — zwei Zeilen, beide sichtbar.
	const std::string size = TargetSizeText (true, true, "1920 x 1080 · 16:9", kNoCanvas);
	const std::vector<std::string> wrapped = WrapText (size, kPaletteLineWidth);
	RTX_CHECK_EQ (wrapped.size (), std::size_t (2));
	const std::vector<std::string> lines =
		LayoutInfoLines (WorstCase (size, PendingText (kCreatedAt, true)), kPaletteLineWidth,
						 kPaletteInfoRows);
	// Beide Zeilen, direkt untereinander.
	RTX_CHECK (ContainsRun (lines, wrapped));
}

RTX_TEST (GekuerzteAbsaetzeEndenSichtbarAufEllipse)
{
	// Wer eine Zeile verliert, zeigt es: „…" am Ende, nicht ein stummer Schnitt.
	const std::vector<InfoParagraph> paragraphs =
		WorstCase (TargetSizeText (true, false, kSceneSize, kNoCanvas), PendingText (kCreatedAt, false));
	// Der Fall ist wirklich zu lang: ungekürzt bräuchte er mehr als acht Zeilen.
	std::size_t unshortened = 0;
	for (const InfoParagraph& paragraph : paragraphs)
		unshortened += WrapText (paragraph.text, kPaletteLineWidth).size ();
	RTX_CHECK (unshortened > kPaletteInfoRows);

	const std::vector<std::string> lines =
		LayoutInfoLines (paragraphs, kPaletteLineWidth, kPaletteInfoRows);
	bool anyEllipsis = false;
	for (const std::string& line : lines)
		if (line.size () >= 3 && line.compare (line.size () - 3, 3, "…") == 0) anyEllipsis = true;
	RTX_CHECK (anyEllipsis);
	RTX_CHECK_EQ (lines.size (), kPaletteInfoRows);
}

RTX_TEST (MitGenugPlatzBleibtAllesUngekuerzt)
{
	const std::vector<std::string> lines =
		LayoutInfoLines ({{"Ansicht: Fenstergröße, ohne Zuschnitt", false},
						  {"", false},
						  {"Zielrahmen: entsteht neu", false},
						  {TargetSizeText (false, true, "", kNoCanvas), true}},
						 kPaletteLineWidth, kPaletteInfoRows);
	// Eine, keine, eine und zwei Zeilen: der leere Absatz belegt nichts.
	RTX_CHECK_EQ (lines.size (), std::size_t (4));
	for (const std::string& line : lines)
		RTX_CHECK (line.size () < 3 || line.compare (line.size () - 3, 3, "…") != 0);
}

RTX_TEST (ZielgroesseNenntDieCanvasVorgabeDesServers)
{
	// RTX-P-015 (#291): die Zahl kommt aus dem Handshake; ohne sie steht der
	// Satz ohne Zahl da. Das Add-on schreibt keine eigene Zahl hin.
	const CanvasDefault canvas {true, "3:2", 2048};
	RTX_CHECK_EQ (CanvasDefaultLabel (canvas), std::string ("Canvas-Vorgabe (3:2, lange Kante 2048 px)"));
	RTX_CHECK_EQ (CanvasDefaultLabel (kNoCanvas), std::string ("Canvas-Vorgabe"));
	RTX_CHECK_EQ (TargetSizeText (false, true, "", canvas),
				  std::string ("Zielgröße: Canvas-Vorgabe (3:2, lange Kante 2048 px) — nur das "
							   "Seitenverhältnis der Aufnahme"));
	RTX_CHECK_EQ (TargetSizeText (true, true, "1920 x 1080 · 16:9", canvas),
				  std::string ("Zielgröße: Aufnahmemaße 1920 x 1080 · 16:9, mindestens 2048 px lange "
							   "Kante (Canvas-Vorgabe)"));
	RTX_CHECK_EQ (TargetSizeText (false, true, "", kNoCanvas),
				  std::string ("Zielgröße: Canvas-Vorgabe — nur das Seitenverhältnis der Aufnahme"));
	RTX_CHECK_EQ (TargetSizeText (true, true, "", kNoCanvas),
				  std::string ("Zielgröße: Aufnahmemaße, mindestens die lange Kante der Canvas-Vorgabe"));
	// Ohne Angabe des Servers keine Ziffer außer den Aufnahmemaßen.
	for (const bool fromCapture : {true, false})
		for (const bool follows : {true, false})
			RTX_CHECK (TargetSizeText (fromCapture, follows, "", kNoCanvas).find_first_of ("0123456789") ==
					   std::string::npos);
}
