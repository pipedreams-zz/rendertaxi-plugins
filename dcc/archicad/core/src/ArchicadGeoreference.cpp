#include "rtx/ArchicadGeoreference.hpp"

#include <cctype>
#include <cmath>
#include <iomanip>
#include <regex>
#include <sstream>

namespace rtx {
namespace {

constexpr double kTolerance = 1e-6;
/** Maßstab 1 bis auf Rundung: 1e-9 sind 1 µm auf 1 km. */
constexpr double kScaleTolerance = 1e-9;

bool Contains (const std::string& text, const std::regex& pattern) { return std::regex_search (text, pattern); }

/** Die erste Zahl einer ausdrücklichen EPSG-Angabe in `text`, 0 ohne. */
int ExplicitEpsg (const std::string& text)
{
	static const std::regex pattern ("EPSG\\s*:{0,2}\\s*([0-9]{4,5})\\b", std::regex::icase);
	std::smatch match;
	if (!std::regex_search (text, match, pattern)) return 0;
	return std::stoi (match[1].str ());
}

/** UTM-Zone und Halbkugel ('N', 'S' oder 0 ohne Angabe) aus Zone oder Namen. */
bool UtmZone (const ArchicadGeoLocation& location, int& zone, char& hemisphere)
{
	static const std::regex zoneField ("^\\s*(?:zone\\s*)?([0-9]{1,2})\\s*([NS])?\\s*$", std::regex::icase);
	static const std::regex inName ("(?:zone\\s*|UTM\\s*)([0-9]{1,2})\\s*([NS])?\\b", std::regex::icase);
	std::smatch match;
	bool found = std::regex_match (location.mapZone, match, zoneField);
	if (!found) {
		for (const std::string* text : {&location.mapProjection, &location.name, &location.description})
			if (std::regex_search (*text, match, inName)) {
				found = true;
				break;
			}
	}
	if (!found) return false;
	zone = std::stoi (match[1].str ());
	hemisphere = match[2].matched ? static_cast<char> (std::toupper (match[2].str ()[0])) : 0;
	return zone >= 1 && zone <= 60;
}

int UtmEpsg (const ArchicadGeoLocation& location)
{
	static const std::regex utm ("UTM", std::regex::icase);
	static const std::regex etrs ("ETRS\\s*-?\\s*89|ETRF", std::regex::icase);
	static const std::regex wgs ("WGS\\s*-?\\s*84", std::regex::icase);
	const std::string projection = location.mapProjection + " " + location.name + " " + location.description;
	if (!Contains (projection, utm)) return 0;
	int zone = 0;
	char hemisphere = 0;
	if (!UtmZone (location, zone, hemisphere)) return 0;
	const std::string datum = location.geodeticDatum + " " + location.name + " " + location.description;
	// ETRS89 gilt nur in Europa, also nur auf der Nordhalbkugel (EPSG 25828 … 25838).
	if (Contains (datum, etrs)) return hemisphere != 'S' && zone >= 28 && zone <= 38 ? 25800 + zone : 0;
	if (Contains (datum, wgs)) {
		if (hemisphere == 'N') return 32600 + zone;
		if (hemisphere == 'S') return 32700 + zone;
	}
	return 0;
}

bool IsGeographic (int epsg) { return epsg == 4326 || epsg == 4258 || epsg == 4979 || epsg == 4937; }

bool Near (double a, double b) { return std::fabs (a - b) <= kTolerance; }

} // namespace

int EpsgOf (const ArchicadGeoLocation& location)
{
	int epsg = 0;
	for (const std::string* text :
		 {&location.name, &location.mapProjection, &location.mapZone, &location.description})
		if ((epsg = ExplicitEpsg (*text)) != 0) break;
	if (epsg == 0) epsg = UtmEpsg (location);
	if (epsg < 1024 || epsg > 32767 || IsGeographic (epsg)) return 0;
	return epsg;
}

GeoreferenceReading ReadArchicadGeoreference (const ArchicadGeoLocation& location)
{
	GeoreferenceReading reading;
	const auto refuse = [&reading] (const std::string& why) {
		reading.note = "Georeferenz nicht übertragen: " + why;
		return reading;
	};
	const bool texts = !location.name.empty () || !location.description.empty () || !location.mapProjection.empty () ||
					   !location.mapZone.empty () || !location.geodeticDatum.empty ();
	const bool placed = location.eastings != 0.0 || location.northings != 0.0;
	// Nichts gesetzt: das Projekt trägt keine Georeferenz, und der Block fehlt ohne Satz.
	if (!texts && !placed) return reading;

	const int epsg = EpsgOf (location);
	if (epsg == 0)
		return refuse ("das Bezugssystem nennt keinen EPSG-Code eines projizierten Systems "
					   "(etwa „EPSG:25832“ als Name in den Georeferenz-Parametern).");
	if (!std::isfinite (location.eastings) || !std::isfinite (location.northings) ||
		!std::isfinite (location.orthogonalHeight))
		return refuse ("die Kartenlage des Vermessungspunkts ist keine Zahl.");
	if (!placed) return refuse ("der Vermessungspunkt hat keine Kartenlage (Ost und Nord sind 0).");

	// Ein Maßstab ≠ 1 verzerrt jede Strecke ab dem Vermessungspunkt; die Datei geht 1 : 1, und die Kette trägt
	// nur eine Drehung. Ihn allein auf den Ursprung zu legen, gäbe eine scheinbar exakte Lage, die um Millimeter
	// (0,99994 auf 100 m) bis Meter danebenliegt — also kein Block.
	double scale = location.scale;
	if (!std::isfinite (scale) || scale == 0.0) scale = 1.0;
	if (std::fabs (scale - 1.0) > kScaleTolerance) {
		std::ostringstream text;
		text << std::setprecision (12) << scale;
		return refuse ("der Maßstab der Georeferenz ist " + text.str () +
					   ", rendertaxi überträgt das Modell nur im Maßstab 1. Die Lage wäre sonst ungenau; "
					   "für die Georeferenz in den Georeferenz-Parametern den Maßstab 1 setzen.");
	}

	// Die Drehung des Vermessungspunkts: linearer Teil der Abbildung, nur um die Hochachse.
	const double* t = location.surveyTransform;
	double l00 = 1, l01 = 0, l10 = 0, l11 = 1;
	if (location.hasSurveyTransform) {
		for (int i = 0; i < 12; ++i)
			if (!std::isfinite (t[i])) return refuse ("die Abbildung des Vermessungspunkts ist keine Zahl.");
		l00 = t[0];
		l01 = t[1];
		l10 = t[4];
		l11 = t[5];
		const bool upright = Near (t[2], 0) && Near (t[6], 0) && Near (t[8], 0) && Near (t[9], 0) && Near (t[10], 1);
		const bool rigid = Near (l00 * l00 + l10 * l10, 1) && Near (l01 * l01 + l11 * l11, 1) &&
						   Near (l00 * l01 + l10 * l11, 0) && Near (l00 * l11 - l01 * l10, 1);
		if (!upright || !rigid)
			return refuse ("der Vermessungspunkt ist nicht nur um die Hochachse gedreht.");
	}

	double a = location.xAxisAbscissa;
	double b = location.xAxisOrdinate;
	if (!std::isfinite (a) || !std::isfinite (b)) return refuse ("die x-Achse der Karte ist keine Zahl.");
	const double length = std::hypot (a, b);
	if (length < 1e-12) {
		a = 1.0;
		b = 0.0;
	} else {
		a /= length;
		b /= length;
	}

	// Dateinullpunkt p = 0: q = L · (−s).
	const double* s = location.surveyPoint;
	const double qx = -(l00 * s[0] + l01 * s[1]);
	const double qy = -(l10 * s[0] + l11 * s[1]);
	const double qz = -s[2];
	// Planachse +Y: L · (0, 1, 0), in Kartenachsen.
	const double vx = l01;
	const double vy = l11;
	const double east = vx * a - vy * b;
	const double north = vx * b + vy * a;

	reading.present = true;
	reading.block.epsg = epsg;
	reading.block.easting = location.eastings + (qx * a - qy * b);
	reading.block.northing = location.northings + (qx * b + qy * a);
	reading.block.height = location.orthogonalHeight + qz;
	reading.block.rotationRadians = -std::atan2 (east, north);
	return reading;
}

} // namespace rtx
