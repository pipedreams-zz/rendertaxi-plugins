// Georeferenz aus dem Archicad-Projekt (RTX-A-013, #628; Q-04).
//
// Archicad führt seit 25 die **Geo Location** des Projekts
// (`ACAPI_GeoLocation_GetGeoLocation`, `API_GeoLocation`): den Ort
// (`placeInfo`: Länge, Breite, Projektnorden), die Lage des
// **Vermessungspunkts** im Projekt (`surveyPointPosition`) und die
// Georeferenz-Parameter (`API_GeoReferenceData`) — laut DevKit-Kopf „entsprechen
// `IfcCoordinateReferenceSystem` und `IfcMapConversion`“: Name und Teile des
// Bezugssystems als Text, Ost/Nord/Höhe des Vermessungspunkts in der Karte, die
// Richtung der lokalen x-Achse in Kartenachsen und ein Maßstab. Dazu liefert
// `ACAPI_SurveyPoint_GetSurveyPointTransformation` die Abbildung
// Projektursprung → Koordinatensystem des Vermessungspunkts; ihr linearer Teil
// ist dessen Drehung (der Nordwinkel des Vermessungspunkts, den „Projektnorden
// setzen“ mitführt).
//
// Dieser Baustein ist DevKit-frei: `src/ModelExport.cpp` kopiert die Rohwerte
// in `ArchicadGeoLocation`, hier entsteht der Block `geometry.georeference`
// nach Plugin-API §7.9 und `georeferenz.md` 2.2 — oder sein Fehlen mit Grund.
//
// Die Kette der Abbildung eines Projektpunkts p (Meter, Z oben) in die Karte:
//
//   q = L · (p − s)                 s Vermessungspunkt im Projekt, L Drehung des Vermessungspunkts
//   E = E₀ + k · (q.x · a − q.y · b)    (a, b) = x-Achse der Karte, normiert; k Maßstab
//   N = N₀ + k · (q.x · b + q.y · a)
//
// Übertragen wird nur k = 1: die Datei geht 1 : 1, und die Kette trägt eine Drehung (siehe unten).
//   H = H₀ + q.z
//
// Der Dateinullpunkt (p = 0) gibt `origin`, die Richtung der Planachse +Y den
// Datei-Nordwinkel α (Gitter-Nord → Plan-Nord, im Uhrzeigersinn positiv); die
// Kette ist eine Drehung um +Y des Exportraums um −α.
#pragma once

#include <string>

#include "rtx/CaptureManifest.hpp"

namespace rtx {

/** Die Rohwerte aus Archicad, Texte als UTF-8. */
struct ArchicadGeoLocation {
	/** `API_GeoReferenceData`: die Texte des Bezugssystems. */
	std::string name;
	std::string description;
	std::string geodeticDatum;
	std::string verticalDatum;
	std::string mapProjection;
	std::string mapZone;
	/** Lage des Vermessungspunkts in der Karte. */
	double eastings = 0.0;
	double northings = 0.0;
	double orthogonalHeight = 0.0;
	/** Richtung der lokalen x-Achse in Kartenachsen (Ost, Nord); (0, 0) gilt wie im IFC als (1, 0). */
	double xAxisAbscissa = 1.0;
	double xAxisOrdinate = 0.0;
	/** Maßstab; 0 oder nicht endlich gilt wie im IFC als „nicht angegeben“ = 1. */
	double scale = 1.0;
	/** `API_GeoLocation.surveyPointPosition`: Lage des Vermessungspunkts im Projekt, Meter. */
	double surveyPoint[3] = {0, 0, 0};
	/** `API_Tranmat` aus `ACAPI_SurveyPoint_GetSurveyPointTransformation`, zeilenweise 3 × 4. */
	bool hasSurveyTransform = false;
	double surveyTransform[12] = {1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0};
};

/**
 * Der EPSG-Code aus den Texten des Bezugssystems, 0 wenn keiner folgt.
 *
 * 1. Ausdrücklich: `EPSG:25832`, `EPSG::25832`, `urn:ogc:def:crs:EPSG::25832`,
 *    `EPSG 25832` in Name, Kartenprojektion, Zone oder Beschreibung (in dieser
 *    Reihenfolge; Graphisofts eigenes Beispiel schreibt `EPSG:28356` in Name und
 *    Kartenprojektion).
 * 2. Sonst UTM aus Projektion/Name und Zone (`UTM`, `32N`, `zone 32`): mit
 *    Datum ETRS89 `258zz` (Zonen 28–38, Nordhalbkugel), mit WGS 84 `326zz` /
 *    `327zz`. Ein anderes oder fehlendes Datum gibt 0 — geraten wird nicht.
 *
 * Geografische Systeme (4326, 4258, 4979, 4937) sind kein projiziertes System
 * nach §7.9 und geben 0.
 */
int EpsgOf (const ArchicadGeoLocation& location);

struct GeoreferenceReading {
	bool present = false;
	CaptureGeoreference block;
	/** Warum der Block fehlt, als Satz für Palette und Protokoll; leer, wenn das Projekt keine Georeferenz trägt. */
	std::string note;
};

/**
 * Der Block aus den Rohwerten. **Fehlen die Daten, fehlt der Block** (ohne
 * Satz): kein Bezugssystem und keine Kartenlage. Mit Satz, wenn etwas gesetzt,
 * aber nicht verwendbar ist — Bezugssystem ohne EPSG-Code, Kartenlage
 * (0, 0), Maßstab ≠ 1, Drehung des Vermessungspunkts nicht um die Hochachse.
 *
 * Maßstab ≠ 1 (auch eine Gitterverzerrung wie 0,99994): die GLB bleibt 1 : 1,
 * und die Kette trägt nur die Drehung. Ein auf den Ursprung gelegter Maßstab
 * ließe jeden Punkt außer dem Ursprung um (1 − k) · Abstand danebenliegen
 * (0,99994 auf 100 m: 6 mm) — darum kein Block, sondern ein Satz.
 */
GeoreferenceReading ReadArchicadGeoreference (const ArchicadGeoLocation& location);

} // namespace rtx
