// Bildbeschreibung aus der Datei lesen statt sie zu behaupten.
//
// `capture-manifest.schema.json` verlangt für jedes vorhandene Asset einen
// `image`-Block mit Maßen, Farbraum, Bittiefe, Zahlenformat und Kanälen. Diese
// Angaben werden aus dem erzeugten Bild **gelesen**: was Archicad tatsächlich
// geschrieben hat, ist die einzige belastbare Quelle. Eine geratene Bittiefe
// wäre genau die Art Behauptung, die `architecture.md` dem Vertrag verbietet.
#pragma once

#include <string>

#include "rtx/Result.hpp"

namespace rtx {

struct ImageInfo {
	int width = 0;
	int height = 0;
	int bitDepth = 8;
	/** `gray`, `gray-alpha`, `rgb` oder `rgba`. */
	std::string channels;
	/** `uint` oder `float`. */
	std::string sampleFormat = "uint";
	/** IANA-Medientyp der gelesenen Datei. */
	std::string mediaType;
};

/**
 * Liest Maße und Kanalbelegung aus einer PNG- oder JPEG-Datei.
 * PNG-Palettenbilder werden als `rgb` beziehungsweise `rgba` gemeldet: nach dem
 * Dekodieren ist genau das ihre Kanalbelegung, und der Konsument sieht das
 * dekodierte Bild.
 */
Result<ImageInfo> ReadImageInfo (const std::string& path);

} // namespace rtx
