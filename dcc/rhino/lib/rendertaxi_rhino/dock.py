"""Die Palette als Rhino-Panel — andocken, als Reiter führen, über den Befehl ein- und ausblenden (RTX-RH-004,
ADR 0045; ersetzt das Fenster aus ADR 0043, Entscheidung 4).

Rhino führt ein Panel über seinen .NET-Typ: ``Panels.RegisterPanel`` verlangt einen Typ mit ``GuidAttribute``,
und Rhino erzeugt die Instanz selbst (je Dokumentfenster eine). Eine in Python 3 abgeleitete Eto-Klasse trägt
kein ``GuidAttribute`` — Rhino 8.35 lehnt sie ab („type must have a GuidAttribute", gemessen am 09.10.2026).
Deshalb erzeugt ``panel_type`` einmal je Rhino-Prozess mit ``System.Reflection.Emit`` eine kleine Unterklasse
von ``Eto.Forms.Panel`` mit ``GuidAttribute(host.PANEL_GUID)``; ihr Konstruktor ruft ein statisches Feld
``Build`` auf, über das Python den Inhalt baut (``panel.RdtxView``). Ein Typ für macOS und Windows.

Rhino merkt sich Dockleiste, Reiter und Größe unter ``host.PANEL_GUID``. Das Paket, das ``rhinocode`` baut, führt
beim Laden kein Python aus; nach einem Neustart erscheint das Panel deshalb erst mit dem Befehl ``RdtxAI`` — dann
an seinem gemerkten Platz (Messprotokoll ``docs/measurements/2026-10-09-panel.md``).

Beim allerersten Öffnen legt ``show`` das Panel als Reiter neben „Eigenschaften" statt in eine schwebende
Leiste von 200 × 200 px. Dass es schon einmal platziert war, steht in ``panel.json`` im Nutzerordner; fehlt die
Datei oder ist sie kaputt, gilt das erste Öffnen (Regel 3: ein gemerkter Zustand verhindert das Öffnen nie).
"""

from __future__ import annotations

import json
import os

from . import host

STATE_FILE = "panel.json"
TYPE_NAME = "RdtxAi.RhinoPanel"
STICKY_TYPE = "rdtx.ai.rhino.panel-type"
ICON_SIZE = 32


def _sticky() -> dict:
    import scriptcontext

    return scriptcontext.sticky


def emit_panel_type(guid: str):
    """Eine Unterklasse von ``Eto.Forms.Panel`` mit ``GuidAttribute(guid)`` und statischem Feld ``Build``.

    Der Konstruktor ruft ``base()`` und dann ``Build(this)``, wenn ``Build`` gesetzt ist.
    """
    import System
    from System.Reflection import AssemblyName, CallingConventions, FieldAttributes, MethodAttributes, TypeAttributes
    from System.Reflection.Emit import AssemblyBuilder, AssemblyBuilderAccess, CustomAttributeBuilder, OpCodes

    base = System.Type.GetType("Eto.Forms.Panel, Eto")
    name = AssemblyName("RdtxAiRhinoPanel")
    module = AssemblyBuilder.DefineDynamicAssembly(name, AssemblyBuilderAccess.Run).DefineDynamicModule(name.Name)
    builder = module.DefineType(TYPE_NAME, TypeAttributes.Public | TypeAttributes.Class, base)
    guid_ctor = System.Type.GetType("System.Runtime.InteropServices.GuidAttribute").GetConstructor(
        System.Array[System.Type]([System.String]))
    builder.SetCustomAttribute(CustomAttributeBuilder(guid_ctor, System.Array[System.Object]([guid])))
    action = System.Type.GetType("System.Action`1").MakeGenericType(System.Array[System.Type]([System.Object]))
    build = builder.DefineField("Build", action, FieldAttributes.Public | FieldAttributes.Static)
    ctor = builder.DefineConstructor(MethodAttributes.Public, CallingConventions.Standard, System.Type.EmptyTypes)
    il = ctor.GetILGenerator()
    il.Emit(OpCodes.Ldarg_0)
    il.Emit(OpCodes.Call, base.GetConstructor(System.Type.EmptyTypes))
    done = il.DefineLabel()
    il.Emit(OpCodes.Ldsfld, build)
    il.Emit(OpCodes.Brfalse_S, done)
    il.Emit(OpCodes.Ldsfld, build)
    il.Emit(OpCodes.Ldarg_0)
    il.Emit(OpCodes.Callvirt, action.GetMethod("Invoke"))
    il.MarkLabel(done)
    il.Emit(OpCodes.Ret)
    return builder.CreateType()


def icon():
    """Das Logo (``icons/rdtxai-{dark,light}.svg``) als ``System.Drawing.Icon`` — ``None``, wenn es nicht geht.

    Dieselben SVGs wie Werkzeugleiste und Befehl (``rdtxai.rhproj``); ``pack.sh`` legt sie neben dieses Modul.
    Ohne sie (oder ohne Rhino-Hilfe für SVG) zeigt Rhino sein
    Standardsymbol, das Panel öffnet trotzdem (Regel 3).
    """
    try:
        import Rhino
        import System

        dark = bool(Rhino.Runtime.HostUtils.RunningInDarkMode)
        name = f"rdtxai-{'dark' if dark else 'light'}.svg"
        # Im Paket neben dem Modul (pack.sh), im Arbeitsbaum unter plugin/icons/.
        paths = [os.path.join(host.PLUGIN_DIR, "icons", name),
                 os.path.join(host.PLUGIN_DIR, os.pardir, os.pardir, "icons", name)]
        path = next(candidate for candidate in paths if os.path.isfile(candidate))
        with open(path, encoding="utf-8") as handle:
            svg = handle.read()
        bitmap = Rhino.UI.DrawingUtilities.BitmapFromSvg(svg, ICON_SIZE, ICON_SIZE, True)
        return System.Drawing.Icon.FromHandle(bitmap.GetHicon())
    except Exception:  # noqa: BLE001 — Standardsymbol statt Ladefehler
        return None


def panel_type(build):
    """Den Panel-Typ einmal je Rhino-Prozess erzeugen und registrieren; ``build(panel)`` baut jede Instanz.

    Ein zweiter Aufruf (Befehl erneut, Bibliothek neu geladen) setzt nur ``Build`` neu — Rhino kennt den Typ
    schon, ein zweites ``RegisterPanel`` unter derselben Kennung wäre ein anderer Typ.
    """
    import Rhino
    import System

    sticky = _sticky()
    panel = sticky.get(STICKY_TYPE)
    if panel is None:
        panel = emit_panel_type(host.PANEL_GUID)
        plugin = Rhino.PlugIns.PlugIn.Find(System.Guid(host.PLUGIN_GUID))
        Rhino.UI.Panels.RegisterPanel(plugin, panel, host.MARK, icon())
        sticky[STICKY_TYPE] = panel
    panel.GetField("Build").SetValue(None, System.Action[System.Object](build))
    return panel


def _state_path(directory: str) -> str:
    return os.path.join(directory, STATE_FILE)


def placed(directory: str) -> bool:
    """War das Panel schon einmal offen? Fehlende oder kaputte Datei: nein (Regel 3)."""
    try:
        with open(_state_path(directory), encoding="utf-8") as handle:
            return json.load(handle).get("placed") is True
    except (OSError, ValueError, AttributeError):
        return False


def remember_placed(directory: str) -> None:
    try:
        os.makedirs(directory, exist_ok=True)
        with open(_state_path(directory), "w", encoding="utf-8") as handle:
            json.dump({"placed": True}, handle)
    except OSError:
        pass  # beim nächsten Mal wieder neben „Eigenschaften" — kein Fehler


def show(directory: str) -> str:
    """Befehl ``RdtxAI``: Panel öffnen, nach vorn holen oder — ist es schon der sichtbare Reiter — ausblenden.

    Gibt ``"opened"``, ``"closed"`` oder ``"hidden"`` (Rhino zeigt es nicht) zurück.
    """
    import Rhino
    import System

    panels = Rhino.UI.Panels
    guid = System.Guid(host.PANEL_GUID)
    if panels.IsPanelVisible(guid, True):
        panels.ClosePanel(guid)
        return "closed"
    beside = panels.PanelDockBar(Rhino.UI.PanelIds.ObjectProperties)
    if not placed(directory) and beside != System.Guid.Empty:
        panels.OpenPanel(beside, guid, True)
    else:
        panels.OpenPanel(guid, True)
    if not panels.IsPanelVisible(guid):
        # Gemerkter Platz nicht mehr da (Leiste geschlossen, Datei der Fenster kaputt): neben „Eigenschaften".
        if beside != System.Guid.Empty:
            panels.OpenPanel(beside, guid, True)
    visible = bool(panels.IsPanelVisible(guid))
    if visible:
        remember_placed(directory)
    return "opened" if visible else "hidden"
