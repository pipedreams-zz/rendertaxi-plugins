// Fassung des Add-Ons. Sie steht im Manifest unter `source.plugin.version` und
// ist deshalb eine semantische Version nach `common.schema.json`.
#pragma once

// **1.0.0, nicht 0.1.0.** Der ausgelieferte Server auf dev.rendertaxi.ai
// meldet im Handshake `hosts[0].minimumPluginVersion: "1.0.0"`; eine 0.x-Fassung
// bekommt `update: { status: "update_required" }` und darf nach §4 nichts
// übernehmen. Die Fassung ist eine Eigenschaft dieses Add-Ons, keine
// Vertragsauslegung — sie wird hier gehoben und im Bericht genannt, nicht
// stillschweigend.
#define RTX_ADDON_VERSION "1.0.0"

// **Wortgleich zu `client_id`** (§5.1: „`client_id` … wortgleich zu
// `source.plugin.identifier` des Manifests"). Vorher standen hier zwei
// verschiedene Kennungen; das war ein Vertragsverstoß (F-05).
#define RTX_ADDON_IDENTIFIER "ai.rendertaxi.plugin.archicad"

/**
 * Der Build: Datum und Uhrzeit der Übersetzung.
 *
 * Er steht neben der Fassung im „Über"-Fenster, weil zwischen zwei Ständen mit
 * derselben Fassung genau er den Unterschied macht — bei einem Fehlerbericht
 * ist „1.0.0" ohne Build keine Auskunft.
 */
#define RTX_ADDON_BUILD (__DATE__ " " __TIME__)

/** Vorgabe der Serveradresse; der Nutzer kann sie in der Palette ändern. */
#define RTX_DEFAULT_SERVER "https://dev.rendertaxi.ai"
