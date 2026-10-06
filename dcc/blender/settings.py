"""Was das Add-on sich merkt — und wo.

Drei Dinge, drei Orte:

* **Add-on-Einstellungen** (``AddonPreferences``, in ``userpref.blend``):
  Serveradresse, Gerätename, Debug-Schalter, Bittiefe der Datenpässe
  (RTX-P-012). Kein Token.
* **Anmeldung** (``credentials.json`` im Nutzerordner der Extension,
  ``bpy.utils.extension_path_user``, Modus 0600): Token und ``deviceId`` je
  Serveradresse. **Nie** in der ``.blend``-Datei, nie in den Einstellungen,
  nie im Log. Abweichung von ``plugin-api-v1.md``, Abschnitt 5.5
  (Schlüsselspeicher des Systems) — begründet in ADR 0033.
* **Übernahmen** (``transfers.json`` und ``captures/<Schlüssel>/``): der
  Vorgangsschlüssel und die Dateien einer angefangenen Übernahme, damit sie
  einen Neustart überlebt; dazu der zuletzt gewählte Blickpunkt je Dokument.

Die Ablage selbst (``CredentialStore``, ``TransferStore``,
``has_local_material``) und das Protokoll gehören zum gemeinsamen Client
(``rendertaxi_client.store``, ``rendertaxi_client.log``); hier steht nur, was
Blender braucht: ``user_dir`` und ``Preferences``.

**Ein gemerkter Zustand darf das Laden nie verhindern.** Jede Datei der
Ablage wird so gelesen, dass eine kaputte, fremde oder fehlende Datei zum
leeren Zustand führt („nicht verbunden") statt zu einer Ausnahme beim Laden des
Add-ons. Ebenso die Bittiefe: ein ungültiger gemerkter Wert ist 8 Bit
(``data_pass_bit_depth``).
"""

from __future__ import annotations

from . import host  # noqa: F401 — setzt den Host des Clients, bevor ihn jemand benutzt
from .rendertaxi_client import manifest as mf
from .rendertaxi_client.log import log, log_exception, set_debug  # noqa: F401
from .rendertaxi_client.store import CredentialStore, TransferStore, has_local_material  # noqa: F401

DEFAULT_SERVER_URL = "https://dev.rendertaxi.ai"


# --------------------------------------------------------------------------
# Blender
# --------------------------------------------------------------------------


def user_dir() -> str:
    """Der Nutzerordner dieser Extension — außerhalb der ``.blend`` und des Paketordners."""
    import bpy

    return bpy.utils.extension_path_user(__package__, create=True)


def data_pass_bit_depth() -> int:
    """Die gewählte Bittiefe der Datenpässe — 8 oder 16; alles andere, auch ein Fehler, ist 8 Bit."""
    try:
        prefs = preferences()
        return mf.data_pass_bit_depth(prefs.data_pass_bit_depth if prefs is not None else None)
    except Exception:  # noqa: BLE001 — ein gemerkter Zustand darf nie stören (Regel 3)
        return mf.DEFAULT_DATA_PASS_BIT_DEPTH


try:
    import bpy
    from bpy.props import BoolProperty, EnumProperty, StringProperty
    from bpy.types import AddonPreferences

    def _debug_changed(self, _context):
        set_debug(self.debug_logging)

    class Preferences(AddonPreferences):
        bl_idname = __package__

        server_url: StringProperty(
            name="Serveradresse",
            description="Adresse der rendertaxi.ai-Webanwendung. https ist Pflicht, außer für localhost",
            default=DEFAULT_SERVER_URL,
        )
        device_name: StringProperty(
            name="Gerätename",
            description="Optional: so erscheint dieses Blender unter „Verbundene Geräte“",
            default="",
            maxlen=64,
        )
        debug_logging: BoolProperty(
            name="Ausführliches Protokoll",
            description="Schreibt bei Fehlern zusätzlich die Codestellen im Add-on in die Konsole — nie Pfade oder Namen",
            default=False,
            update=_debug_changed,
        )
        # Statische Liste: der gemerkte Wert ist die Zahl selbst (8 oder 16), nicht ein Index in
        # einer Liste, die sich ändern kann. Gelesen wird er nur über ``data_pass_bit_depth``.
        data_pass_bit_depth: EnumProperty(
            name=mf.DATA_PASS_BIT_DEPTH_LABEL,
            description="Bittiefe der PNG-Dateien für Tiefe, Normalen, Albedo und IDs",
            items=[(str(bits), label, "", bits) for bits, label in mf.DATA_PASS_BIT_DEPTH_OPTIONS],
            default=str(mf.DEFAULT_DATA_PASS_BIT_DEPTH),
        )

        def draw(self, _context):
            layout = self.layout
            layout.prop(self, "server_url")
            layout.prop(self, "device_name")
            layout.prop(self, "debug_logging")
            layout.prop(self, "data_pass_bit_depth")
            layout.label(text="Die Anmeldung liegt im Nutzerordner der Extension, nur für dich lesbar.")
            # Vor dem Deinstallieren (RTX-B-004): Blender löscht den Nutzerordner, widerruft aber kein Token.
            layout.separator()
            layout.operator("rendertaxi.forget_local", icon="TRASH")

    def preferences():
        """Die Einstellungen — oder ``None``, wenn sie (noch) nicht lesbar sind."""
        try:
            return bpy.context.preferences.addons[__package__].preferences
        except (KeyError, AttributeError):
            return None

except ImportError:  # außerhalb von Blender: nur die Ablage ist nutzbar
    Preferences = None

    def preferences():
        return None
