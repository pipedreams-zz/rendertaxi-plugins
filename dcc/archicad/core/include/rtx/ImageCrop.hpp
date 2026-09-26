// Zuschnitt auf das Zielformat eines Blickpunkts.
//
// **Warum der Zuschnitt und nicht die Fenstergröße.** Der erste Versuch, das
// Zielformat über `ACAPI_View_Change3DWindowSets` zu erreichen, ist an
// Archicad 28 gescheitert (`capabilities.md`, Abschnitt 2.3): das 3D-Fenster
// wird dabei aus der Registerleiste **entdockt** und kommt nicht zurück, und
// eine breitere Fenstergröße erweitert das **Blickfeld nach rechts**, statt
// oben und unten zu beschneiden. Das Ergebnis hatte zwar das Zielverhältnis,
// zeigte aber mehr Szene als die Ansicht, aus der es stammte.
//
// Der Zuschnitt macht das, was der Nutzer sieht, zur Grundlage: dieselbe
// Breite, oben und unten mittig beschnitten. Er ist sichtbar — die Palette
// nennt das Zielformat, bevor aufgenommen wird — und er ist deterministisch:
// dieselbe Ansicht und dasselbe Ziel ergeben dieselben Pixel, also denselben
// SHA-256 und damit die Deduplizierung aus #19.
#pragma once

#include <string>

#include "rtx/Result.hpp"

namespace rtx {

struct CropResult {
	bool cropped = false;
	int width = 0;
	int height = 0;
};

/**
 * Schneidet `sourcePath` mittig auf das Verhältnis `aspectWidth:aspectHeight`
 * zu und schreibt das Ergebnis als PNG nach `targetPath`.
 *
 * Passt das Bild bereits (Abweichung unter einem halben Prozent), wird nichts
 * geschnitten und `cropped` bleibt `false`; der Aufrufer benutzt dann weiter
 * die Quelldatei. Ein unbekanntes Verhältnis (0) ist kein Fehler, sondern
 * dasselbe „nichts zu tun".
 */
/**
 * Schreibt ein Bild als **PNG** neu.
 *
 * Gebraucht wird das, weil `ACAPI_Rendering_PhotoRender` kein PNG kennt — die
 * Dokumentation nennt PICT, BMP, TIFF, JPEG und GIF. Der Weg über TIFF und
 * diese Umschreibung ist verlustfrei; JPEG wäre es nicht, und ein Basisbild
 * soll nicht schon vor der ersten Generierung Artefakte tragen.
 *
 * Quelle und Ziel dürfen nicht dieselbe Datei sein.
 */
Status ConvertImageToPng (const std::string& sourcePath, const std::string& targetPath);

Result<CropResult> CropImageToAspect (const std::string& sourcePath,
									  const std::string& targetPath, int aspectWidth,
									  int aspectHeight);

} // namespace rtx
