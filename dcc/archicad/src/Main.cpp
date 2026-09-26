// rendertaxi für Archicad 28 — Einsprungpunkte des Add-Ons.
//
// Vier Funktionen verlangt das DevKit: `CheckEnvironment`, `RegisterInterface`,
// `Initialize`, `FreeData`. Mehr macht diese Datei nicht; alles Weitere steht
// in der Palette und im DevKit-freien Kern.
#include "APIEnvir.h"
#include "ACAPinc.h"

#include <string>

#include "CapabilityProbe.hpp"
#include "HostInfo.hpp"
#include "JsonCommands.hpp"
#include "RendertaxiPalette.hpp"
#include "Settings.hpp"
#include "Version.hpp"
#include "rtx/Log.hpp"

static void ShowOrHidePalette ()
{
	using rtxaddon::RendertaxiPalette;
	if (RendertaxiPalette::HasInstance () && RendertaxiPalette::GetInstance ().IsVisible ())
		RendertaxiPalette::GetInstance ().Hide ();
	else
		RendertaxiPalette::EnsureShown ();
}

static GSErrCode MenuCommandHandler (const API_MenuParams* menuParams)
{
	// **Zwei Ressourcen, ein flaches Menü.** Jede trägt genau einen Befehl;
	// mehr als einer je Ressource erzwingt bei `MenuCode_UserDef` ein
	// Untermenü (siehe `RINT/rendertaxi.grc`). Die Kennung der Ressource sagt
	// deshalb, welcher Befehl gemeint ist, und der Index ist in beiden 1.
	if (menuParams->menuItemRef.menuResID == RtxMenuResId) {
		ShowOrHidePalette ();
	} else if (menuParams->menuItemRef.menuResID == RtxAboutMenuResId) {
		// **Was in einem Fehlerbericht stehen muss**, in einem Satz: Fassung,
		// Build, Kennung, Server. Die Fassung allein sagt zu wenig — zwischen
		// zwei Ständen mit derselben Nummer unterscheidet nur der Build.
		const rtxaddon::HostVersion host = rtxaddon::ReadHostVersion ();
		ACAPI_WriteReport ("rendertaxi.ai für Archicad\n\n"
						   "Fassung:   %s\n"
						   "Build:     %s\n"
						   "Kennung:   %s\n"
						   "Server:    %s\n"
						   "Archicad:  %s (Build %s)",
						   true, RTX_ADDON_VERSION, RTX_ADDON_BUILD, RTX_ADDON_IDENTIFIER,
						   rtxaddon::ServerUrl ().c_str (), host.version.c_str (),
						   host.build.c_str ());
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
	GSErrCode err = ACAPI_MenuItem_RegisterMenu (RtxMenuResId, 0, MenuCode_UserDef, MenuFlag_Default);
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
	GSErrCode err = ACAPI_MenuItem_InstallMenuHandler (RtxMenuResId, MenuCommandHandler);
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
