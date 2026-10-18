// Georeferenz aus der Geo Location (RTX-A-013, #628; Q-04): EPSG aus den
// Texten des Bezugssystems, Dateiursprung und Datei-Nordwinkel aus
// Vermessungspunkt, seiner Drehung und der x-Achse der Karte.
//
// Das bekannte Projekt (dieselben Zahlen wie `tests/contract/archicad-georeference.test.ts`
// und die Fixture `fixtures/v1/model-georef`): Vermessungspunkt bei (12; 8; 1,5) m
// im Projekt, in der Karte EPSG 25832 Ost 294 409,411675, Nord 5 628 838,965590,
// Höhe 174,5 (der Domhof Aachen wie in RTX-RH-005); der Vermessungspunkt um −22°
// gedreht (Plan-Nord 22° im Uhrzeigersinn neben seinem Nord), die x-Achse der
// Karte um +5° (gegen den Uhrzeiger). Plan-Nord liegt also 17° im Uhrzeigersinn
// neben Gitter-Nord: α = 17°.
#include "Testing.hpp"

#include <array>
#include <cmath>
#include <string>

#include "rtx/ArchicadGeoreference.hpp"

using namespace rtx;

namespace {

constexpr double kPi = 3.14159265358979323846;
constexpr double kDegree = kPi / 180.0;

bool Near (double a, double b, double tolerance) { return std::fabs (a - b) <= tolerance; }

/** Die Abbildung Projekt → Vermessungspunkt: q = R(γ) · (p − s), als `API_Tranmat`. */
void SurveyTransform (ArchicadGeoLocation& location, double gammaDegrees)
{
	const double c = std::cos (gammaDegrees * kDegree);
	const double s = std::sin (gammaDegrees * kDegree);
	const double* p = location.surveyPoint;
	const double t[12] = {c, -s, 0, -(c * p[0] - s * p[1]), s, c, 0, -(s * p[0] + c * p[1]), 0, 0, 1, -p[2]};
	location.hasSurveyTransform = true;
	for (int i = 0; i < 12; ++i) location.surveyTransform[i] = t[i];
}

} // namespace

/** Auch `ModelWayTest.cpp` (Manifest und Fixture) rechnet mit diesem Projekt. */
ArchicadGeoLocation KnownGeoProject ()
{
	ArchicadGeoLocation location;
	location.name = "ETRS89 / UTM zone 32N";
	location.geodeticDatum = "ETRS89";
	location.verticalDatum = "DHHN2016";
	location.mapProjection = "EPSG:25832";
	location.mapZone = "32N";
	location.eastings = 294409.411675;
	location.northings = 5628838.965590;
	location.orthogonalHeight = 174.5;
	location.xAxisAbscissa = std::cos (5 * kDegree);
	location.xAxisOrdinate = std::sin (5 * kDegree);
	location.scale = 1.0;
	location.surveyPoint[0] = 12;
	location.surveyPoint[1] = 8;
	location.surveyPoint[2] = 1.5;
	SurveyTransform (location, -22);
	return location;
}

namespace {

ArchicadGeoLocation KnownProject () { return KnownGeoProject (); }

/** Der Dateiursprung, unabhängig gerechnet: Vermessungspunkt minus Planvektor s, um α im Gitter gedreht. */
void ExpectedOrigin (const ArchicadGeoLocation& location, double alphaDegrees, double& east, double& north)
{
	const double c = std::cos (alphaDegrees * kDegree);
	const double s = std::sin (alphaDegrees * kDegree);
	const double* p = location.surveyPoint;
	east = location.eastings - (p[0] * c + p[1] * s);
	north = location.northings - (-p[0] * s + p[1] * c);
}

} // namespace

RTX_TEST (GeoreferenzEpsgAusDenTextenDesBezugssystems)
{
	ArchicadGeoLocation location;
	RTX_CHECK_EQ (EpsgOf (location), 0);
	location.name = "EPSG:28356";  // Graphisofts Beispiel `Environment_Control`
	RTX_CHECK_EQ (EpsgOf (location), 28356);
	location.name = "Lageplan";
	location.mapProjection = "urn:ogc:def:crs:EPSG::25833";
	RTX_CHECK_EQ (EpsgOf (location), 25833);
	location.mapProjection = "epsg 31467";
	RTX_CHECK_EQ (EpsgOf (location), 31467);

	// UTM aus Projektion und Zone, mit dem Datum.
	ArchicadGeoLocation utm;
	utm.mapProjection = "UTM";
	utm.mapZone = "32N";
	utm.geodeticDatum = "ETRS89";
	RTX_CHECK_EQ (EpsgOf (utm), 25832);
	utm.mapZone = "zone 33";
	RTX_CHECK_EQ (EpsgOf (utm), 25833);
	utm.geodeticDatum = "WGS 84";
	RTX_CHECK_EQ (EpsgOf (utm), 0);  // WGS 84 ohne Halbkugel: nicht geraten
	utm.mapZone = "56S";
	RTX_CHECK_EQ (EpsgOf (utm), 32756);
	utm.mapZone = "";
	utm.name = "WGS 84 / UTM zone 17N";
	RTX_CHECK_EQ (EpsgOf (utm), 32617);
	utm.geodeticDatum = "GDA94";
	utm.name = "UTM 56S";
	RTX_CHECK_EQ (EpsgOf (utm), 0);  // ein anderes Datum: kein Code

	// Geografisch ist kein projiziertes System (§7.9).
	ArchicadGeoLocation geographic;
	geographic.name = "EPSG:4326";
	RTX_CHECK_EQ (EpsgOf (geographic), 0);
}

RTX_TEST (GeoreferenzDesBekanntenProjekts)
{
	const ArchicadGeoLocation location = KnownProject ();
	const GeoreferenceReading reading = ReadArchicadGeoreference (location);
	RTX_CHECK (reading.present);
	RTX_CHECK (reading.note.empty ());
	RTX_CHECK_EQ (reading.block.epsg, 25832);
	double east = 0, north = 0;
	ExpectedOrigin (location, 17, east, north);
	RTX_CHECK (Near (reading.block.easting, east, 1e-7));
	RTX_CHECK (Near (reading.block.northing, north, 1e-7));
	RTX_CHECK (Near (reading.block.easting, 294395.5970443, 1e-6));
	RTX_CHECK (Near (reading.block.northing, 5628834.8236124, 1e-6));
	RTX_CHECK (Near (reading.block.height, 173.0, 1e-12));
	RTX_CHECK (Near (reading.block.rotationRadians, -17 * kDegree, 1e-12));

	// Probe am Vermessungspunkt selbst: von (0, 0) um s im Plan, α im Gitter — er landet auf seiner Kartenlage.
	const double c = std::cos (17 * kDegree);
	const double s = std::sin (17 * kDegree);
	RTX_CHECK (Near (reading.block.easting + 12 * c + 8 * s, location.eastings, 1e-7));
	RTX_CHECK (Near (reading.block.northing - 12 * s + 8 * c, location.northings, 1e-7));
}

RTX_TEST (GeoreferenzOhneDrehungUndAmProjektursprung)
{
	// Vermessungspunkt am Ursprung, keine Drehung: der Dateiursprung ist die Kartenlage, die Kette dreht um 0.
	ArchicadGeoLocation location;
	location.mapProjection = "EPSG:25832";
	location.eastings = 356000.5;
	location.northings = 5645000.25;
	location.orthogonalHeight = 58.2;
	const GeoreferenceReading reading = ReadArchicadGeoreference (location);
	RTX_CHECK (reading.present);
	RTX_CHECK_EQ (reading.block.easting, 356000.5);
	RTX_CHECK_EQ (reading.block.northing, 5645000.25);
	RTX_CHECK_EQ (reading.block.height, 58.2);
	RTX_CHECK_EQ (reading.block.rotationRadians, 0.0);

	// Nur die x-Achse der Karte (wie das OSArch-Rezept): Plan-Nord 30° gegen den Uhrzeiger → α = −30°.
	location.xAxisAbscissa = std::cos (30 * kDegree);
	location.xAxisOrdinate = std::sin (30 * kDegree);
	RTX_CHECK (Near (ReadArchicadGeoreference (location).block.rotationRadians, 30 * kDegree, 1e-12));
	// Eine nicht normierte Achse ist dieselbe Richtung; (0, 0) gilt als (1, 0).
	location.xAxisAbscissa *= 7;
	location.xAxisOrdinate *= 7;
	RTX_CHECK (Near (ReadArchicadGeoreference (location).block.rotationRadians, 30 * kDegree, 1e-12));
	location.xAxisAbscissa = 0;
	location.xAxisOrdinate = 0;
	RTX_CHECK_EQ (ReadArchicadGeoreference (location).block.rotationRadians, 0.0);
	// Maßstab 0 heißt „nicht angegeben“.
	location.scale = 0;
	RTX_CHECK (ReadArchicadGeoreference (location).present);
}

RTX_TEST (GeoreferenzLegtJedenModellpunktAufSeineKartenlage)
{
	// Über den Block (Ursprung + Drehung um α) gegen die Kette von Archicad (q = L · (p − s), x-Achse der
	// Karte): der Vermessungspunkt und ein weiterer Modellpunkt.
	const ArchicadGeoLocation location = KnownProject ();
	const GeoreferenceReading reading = ReadArchicadGeoreference (location);
	RTX_CHECK (reading.present);
	const double c = std::cos (17 * kDegree);
	const double s = std::sin (17 * kDegree);
	const double* t = location.surveyTransform;
	const double a = location.xAxisAbscissa;
	const double b = location.xAxisOrdinate;
	for (const auto& p : {std::array<double, 3> {12, 8, 1.5}, std::array<double, 3> {30, 20, 0}}) {
		const double qx = t[0] * p[0] + t[1] * p[1] + t[3];
		const double qy = t[4] * p[0] + t[5] * p[1] + t[7];
		const double east = location.eastings + qx * a - qy * b;
		const double north = location.northings + qx * b + qy * a;
		RTX_CHECK (Near (reading.block.easting + p[0] * c + p[1] * s, east, 1e-7));
		RTX_CHECK (Near (reading.block.northing - p[0] * s + p[1] * c, north, 1e-7));
		RTX_CHECK (Near (reading.block.height + p[2], location.orthogonalHeight + p[2] - 1.5, 1e-12));
	}
}

RTX_TEST (GeoreferenzMitMasstabUngleichEinsFehltMitSatz)
{
	// Die Datei geht 1 : 1; ein Maßstab ≠ 1 ließe jeden Punkt außer dem Ursprung danebenliegen.
	for (const double scale : {0.999941534119322 /* Graphisofts Beispiel */, 1.1, 0.9, 1.000001, 0.3048}) {
		ArchicadGeoLocation location = KnownProject ();
		location.scale = scale;
		const GeoreferenceReading reading = ReadArchicadGeoreference (location);
		RTX_CHECK (!reading.present);
		RTX_CHECK (reading.note.find ("Maßstab") != std::string::npos);
		RTX_CHECK (reading.note.find ("Maßstab 1") != std::string::npos);
	}
	// Rundung um 1 ist Maßstab 1.
	ArchicadGeoLocation rounded = KnownProject ();
	rounded.scale = 1.0 + 1e-12;
	RTX_CHECK (ReadArchicadGeoreference (rounded).present);
}

RTX_TEST (GeoreferenzFehltOhneDatenUndNenntDenGrund)
{
	// Ein Projekt ohne Georeferenz: kein Block, kein Satz.
	const GeoreferenceReading none = ReadArchicadGeoreference (ArchicadGeoLocation {});
	RTX_CHECK (!none.present);
	RTX_CHECK (none.note.empty ());

	// Kartenlage ohne Code.
	ArchicadGeoLocation local = KnownProject ();
	local.name = "Lokales Netz";
	local.mapProjection = "";
	local.mapZone = "";
	local.geodeticDatum = "";
	const GeoreferenceReading noCode = ReadArchicadGeoreference (local);
	RTX_CHECK (!noCode.present);
	RTX_CHECK (noCode.note.find ("EPSG") != std::string::npos);

	// Code ohne Kartenlage.
	ArchicadGeoLocation unplaced;
	unplaced.mapProjection = "EPSG:25832";
	const GeoreferenceReading noPlace = ReadArchicadGeoreference (unplaced);
	RTX_CHECK (!noPlace.present);
	RTX_CHECK (noPlace.note.find ("Kartenlage") != std::string::npos);

	// Fuß gegen Meter ist eine Einheitenumrechnung.
	ArchicadGeoLocation feet = KnownProject ();
	feet.scale = 0.3048;
	const GeoreferenceReading foot = ReadArchicadGeoreference (feet);
	RTX_CHECK (!foot.present);
	RTX_CHECK (foot.note.find ("Maßstab") != std::string::npos);

	// Ein gekippter Vermessungspunkt passt in keine Drehung um die Hochachse.
	ArchicadGeoLocation tilted = KnownProject ();
	tilted.surveyTransform[10] = 0.5;
	const GeoreferenceReading tilt = ReadArchicadGeoreference (tilted);
	RTX_CHECK (!tilt.present);
	RTX_CHECK (tilt.note.find ("Hochachse") != std::string::npos);

	// Nicht endliche Werte.
	ArchicadGeoLocation broken = KnownProject ();
	broken.eastings = std::nan ("");
	RTX_CHECK (!ReadArchicadGeoreference (broken).present);
}
