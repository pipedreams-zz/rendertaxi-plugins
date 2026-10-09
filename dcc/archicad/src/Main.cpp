// rendertaxi für Archicad 28 — Einsprungpunkte des Add-Ons.
//
// Vier Funktionen verlangt das DevKit: `CheckEnvironment`, `RegisterInterface`,
// `Initialize`, `FreeData`. Mehr macht diese Datei nicht; alles Weitere steht
// in der Palette und im DevKit-freien Kern.
#include "APIEnvir.h"
#include "ACAPinc.h"
#include "DG.h"

#include <string>

#include "CapabilityProbe.hpp"
#include "HostInfo.hpp"
#include "JsonCommands.hpp"
#include "RendertaxiPalette.hpp"
#include "Settings.hpp"
#include "Version.hpp"
#include "rtx/Log.hpp"
#include "rtx/PaletteText.hpp"

static void ShowOrHidePalette ()
{
	using rtxaddon::RendertaxiPalette;
	if (RendertaxiPalette::HasInstance () && RendertaxiPalette::GetInstance ().IsVisible ())
		RendertaxiPalette::GetInstance ().Hide ();
	else
		RendertaxiPalette::EnsureShown ();
}

/**
 * Welche der beiden „Palette“-Ressourcen gilt (RTX-P-019). Archicad 28 nimmt
 * das Menüsymbol nur aus dem Text der Ressource; ein Wechsel der Erscheinung
 * während der Sitzung erreicht es deshalb erst beim nächsten Start.
 */
static short paletteMenuResId = RtxMenuResId;

static GSErrCode MenuCommandHandler (const API_MenuParams* menuParams)
{
	// **Zwei Ressourcen, ein flaches Menü.** Jede trägt genau einen Befehl;
	// mehr als einer je Ressource erzwingt bei `MenuCode_UserDef` ein
	// Untermenü (siehe `RINT/rendertaxi.grc`). Die Kennung der Ressource sagt
	// deshalb, welcher Befehl gemeint ist, und der Index ist in beiden 1.
	if (menuParams->menuItemRef.menuResID == paletteMenuResId) {
		ShowOrHidePalette ();
	} else if (menuParams->menuItemRef.menuResID == RtxAboutMenuResId) {
		// **Was in einem Fehlerbericht stehen muss**: Fassung, Build, Kennung,
		// Server, Archicad. Die Fassung allein sagt zu wenig — zwischen zwei
		// Ständen mit derselben Nummer unterscheidet nur der Build, und der
		// nennt seit #281 den Git-Stand.
		//
		// **Ein Hinweis, keine Warnung.** `ACAPI_WriteReport` mit Meldefenster
		// erschien unter Windows als „Warnung!" mit Warnsymbol (Abnahme vom
		// 07.10.2026) — für eine Auskunft das falsche Signal.
		const rtxaddon::HostVersion host = rtxaddon::ReadHostVersion ();
		const std::string details =
			"Fassung " RTX_ADDON_VERSION "\n" +
			rtx::BuildLine (RTX_BUILD_COMMIT, RTX_ADDON_BUILD_DATE) +
			"\nKennung " RTX_ADDON_IDENTIFIER "\nServer " + rtxaddon::ServerUrl () +
			"\nArchicad " + host.version + " (Build " + host.build + ")";
		// Der Fenstertitel ist ein Etikett: `rdtx.ai` (texte.md, „Marke und Kurzmarke"); im Satz rendertaxi.ai.
		DGAlert (DG_INFORMATION, GS::UniString ("rdtx.ai", CC_UTF8),
				 GS::UniString ("rendertaxi.ai für Archicad", CC_UTF8),
				 GS::UniString (details.c_str (), CC_UTF8), GS::UniString ("OK", CC_UTF8));
	}
	return NoError;
}

API_AddonType CheckEnvironment (API_EnvirParams* envir)
{
	RSGetIndString (&envir->addOnInfo.name, 32000, 1, ACAPI_GetOwnResModule ());
	RSGetIndString (&envir->addOnInfo.description, 32000, 2, ACAPI_GetOwnResModule ());
	return APIAddon_Preload;
}

GSErrCode RegisterInterface (void)
{
	paletteMenuResId = rtxaddon::SystemAppearanceIsDark () ? RtxMenuDarkResId : RtxMenuResId;
	GSErrCode err = ACAPI_MenuItem_RegisterMenu (paletteMenuResId, 0, MenuCode_UserDef, MenuFlag_Default);
	if (err != NoError) return err;
	err = ACAPI_MenuItem_RegisterMenu (RtxAboutMenuResId, 0, MenuCode_UserDef, MenuFlag_Default);
	if (err != NoError) return err;
	// Ohne diesen Aufruf kann das Add-On keinen Projektschlüssel im
	// Archicad-Projekt ablegen; `project.sourceProjectKey` bliebe `null`.
	return ACAPI_AddOnObject_RegisterAddOnObjectHandler ();
}

GSErrCode Initialize (void)
{
	rtx::SetLogFile (rtxaddon::LogPath ());
	rtx::LogLine ("rendertaxi-Add-on geladen.");
	rtx::LogLine (std::string ("Menüsymbol: ") + (paletteMenuResId == RtxMenuDarkResId ? "dunkle" : "helle") + " Oberfläche.");
	GSErrCode err = ACAPI_MenuItem_InstallMenuHandler (paletteMenuResId, MenuCommandHandler);
	if (err != NoError) return err;
	err = ACAPI_MenuItem_InstallMenuHandler (RtxAboutMenuResId, MenuCommandHandler);
	if (err != NoError) return err;
	// Ohne diese Anmeldung weiß das Add-on nicht, welcher Ausschnitt offen ist,
	// und der Namensvorschlag fiele auf die Fensterart zurück.
	if (rtxaddon::InstallViewTracking () != NoError)
		rtx::LogLine ("Ausschnittsmappe konnte nicht beobachtet werden.");
	err = rtxaddon::InstallJsonCommands ();
	if (err != NoError) return err;
	return rtxaddon::RendertaxiPalette::RegisterPaletteControlCallBack ();
}

GSErrCode FreeData (void)
{
	rtxaddon::RendertaxiPalette::DestroyInstance ();
	return NoError;
}
