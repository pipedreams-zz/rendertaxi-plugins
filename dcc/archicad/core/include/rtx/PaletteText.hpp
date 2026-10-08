// Die Texte der Palette, soweit sie ohne Archicad feststehen: Kürzen,
// Umbrechen und die Belegung der Infozeilen im Bereich „Bild übernehmen".
//
// Sie stehen im Kern, weil ihre Zusage prüfbar sein muss (Issue #88, Punkt 17,
// Befund F-02 an PR #152): **Die Zielgröße und der offene Vorgang stehen immer
// vollständig da, und kein Absatz fällt ganz weg.** Die Palette selbst ist
// ohne Archicad nicht prüfbar; diese Regel schon.
#pragma once

#include <cstddef>
#include <string>
#include <vector>

#include "rtx/PluginApi.hpp"

namespace rtx {

/** Bytes, die eine Palettenzeile bei `SmallPlain` sicher trägt; darüber schneidet DG ab. */
constexpr std::size_t kPaletteLineWidth = 66;
/** Infozeilen im Bereich „Bild übernehmen" (`RINT/rendertaxi.grc`). */
constexpr std::size_t kPaletteInfoRows = 8;

/** Kürzt auf höchstens `maxBytes` Bytes an einer UTF-8-Grenze und hängt „…" an. */
std::string ShortenText (const std::string& text, std::size_t maxBytes);

/**
 * Bricht einen Satz an Wortgrenzen auf `width` Bytes um. Ein Wort, das allein
 * zu lang ist, wird hart getrennt.
 */
std::vector<std::string> WrapText (const std::string& text, std::size_t width);

/**
 * „Canvas-Vorgabe (3:2, lange Kante N px)" — mit der Angabe des Servers
 * (RTX-P-015); nennt er keine, nur „Canvas-Vorgabe". Das Add-on schreibt nie
 * eine eigene Zahl hin.
 */
std::string CanvasDefaultLabel (const CanvasDefault& canvas);

/**
 * Die Zeile „Zielgröße: …" — was die Rahmengröße für den Blickpunkt bewirkt
 * (§7.2). Die lange Kante der Canvas-Vorgabe kommt aus dem Handshake; ohne sie
 * steht der Satz ohne Zahl da.
 */
std::string TargetSizeText (bool sizeFromCapture, bool frameFollowsCapture,
							const std::string& sceneSize, const CanvasDefault& canvas);

/**
 * Der Hinweis auf einen offenen Vorgang. Er passt in **eine** Zeile, damit er
 * zusammen mit der Zielgröße nie mehr Platz beansprucht, als die übrigen
 * Absätze mindestens übrig lassen müssen.
 */
std::string PendingText (const std::string& createdAt, bool resumable);

struct InfoParagraph {
	std::string text;
	/** `true`: steht immer vollständig da — Zielgröße und offener Vorgang. */
	bool keepWhole = false;
};

/**
 * Belegt `rows` Zeilen mit den Absätzen in ihrer Reihenfolge.
 *
 * **Kein Absatz fällt weg.** Reicht der Platz nicht, verliert der längste
 * kürzbare Absatz eine Zeile, und seine letzte sichtbare Zeile endet auf „…";
 * das wiederholt sich, bis alles passt. Ein kürzbarer Absatz behält immer
 * mindestens eine Zeile, ein Absatz mit `keepWhole` immer alle.
 *
 * Passen selbst dann nicht alle, ist das ein Fehler des Aufrufers, der zu viel
 * Text in zu wenig Zeilen stellt; die Belegung endet dann nach `rows` Zeilen.
 * Die Palette ist so bemessen, dass es dazu nicht kommt — das hält ein Test.
 */
std::vector<std::string> LayoutInfoLines (const std::vector<InfoParagraph>& paragraphs,
										  std::size_t width, std::size_t rows);

/**
 * Die Build-Zeile für „Über" und den Fuß der Palette (RTX-A-011, #281):
 * „Build 7f9ca06 vom 08.10.2026".
 *
 * `commit` ist `RTX_BUILD_COMMIT`, den CI und `scripts/build.sh` per
 * CMake-Definition setzen — ein Git-Hash, wahlweise mit `-dirty` für einen
 * Stand mit ungesicherten Änderungen. Fehlt er oder ist er kein Hash, ist es
 * ein „Entwicklungsbuild". `compileDate` ist `__DATE__` („Oct  8 2026").
 */
std::string BuildLine (const std::string& commit, const std::string& compileDate);

/** `__DATE__` („Oct  8 2026") als „08.10.2026"; Unlesbares bleibt, wie es ist. */
std::string FormatCompileDate (const std::string& compileDate);

} // namespace rtx
