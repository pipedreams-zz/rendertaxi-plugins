"""Was das Add-on sich merkt — und wo.

Drei Dinge, drei Orte:

* **Add-on-Einstellungen** (``AddonPreferences``, in ``userpref.blend``):
  Serveradresse, Gerätename, Debug-Schalter. Kein Token.
* **Anmeldung** (``credentials.json`` im Nutzerordner der Extension,
  ``bpy.utils.extension_path_user``, Modus 0600): Token und ``deviceId`` je
  Serveradresse. **Nie** in der ``.blend``-Datei, nie in den Einstellungen,
  nie im Log. Abweichung von ``plugin-api-v1.md``, Abschnitt 5.5
  (Schlüsselspeicher des Systems) — begründet in ADR 0033.
* **Übernahmen** (``transfers.json`` und ``captures/<Schlüssel>/``): der
  Vorgangsschlüssel und die Dateien einer angefangenen Übernahme, damit sie
  einen Neustart überlebt; dazu der zuletzt gewählte Blickpunkt je Dokument.

**Ein gemerkter Zustand darf das Laden nie verhindern.** Jede Datei hier wird
so gelesen, dass eine kaputte, fremde oder fehlende Datei zum leeren Zustand
führt („nicht verbunden") statt zu einer Ausnahme beim Laden des Add-ons.

Die Klassen ``CredentialStore`` und ``TransferStore`` kennen ``bpy`` nicht und
nehmen ihren Ordner als Argument; nur ``user_dir`` und ``Preferences`` brauchen
Blender.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import traceback

DEFAULT_SERVER_URL = "https://dev.rendertaxi.ai"

# --------------------------------------------------------------------------
# Protokoll
# --------------------------------------------------------------------------

_DEBUG = {"enabled": False}


def set_debug(enabled: bool) -> None:
    _DEBUG["enabled"] = bool(enabled)


def log(message: str) -> None:
    """Eine Zeile auf die Konsole.

    ``message`` trägt Kennungen und Zustände (``captureId``, Rolle, Zustand,
    Fehlercode, Bytegröße) — nie ein Token, eine signierte URL, einen Pfad oder
    einen Projekt-, Ansichts- oder Dateinamen (``plugin-api-v1-security.md``,
    Abschnitt 5). Das gilt auch mit Debug-Schalter.
    """
    print(f"rendertaxi: {message}", file=sys.stderr)


_ADDON_DIR = os.path.dirname(os.path.abspath(__file__))


def describe_exception(error: BaseException) -> str:
    """Eine Ausnahme ohne vertrauliche Werte: Typ, ``errno`` und Codestellen des Add-ons.

    Der Ausnahmetext fehlt absichtlich — ein ``OSError`` trägt dort den
    absoluten Pfad, andere Ausnahmen Projekt- oder Dateinamen. Codestellen
    außerhalb des Add-ons (Standardbibliothek, Blender, Textblöcke einer
    ``.blend``) erscheinen nur als ``…``, weil schon ihr Dateiname ein
    Kundenname sein kann.
    """
    parts = [type(error).__name__]
    number = getattr(error, "errno", None)
    if isinstance(number, int):
        parts.append(f"errno {number}")
    frames = []
    for frame in traceback.extract_tb(error.__traceback__):
        filename = os.path.abspath(frame.filename or "")
        if os.path.dirname(filename) == _ADDON_DIR:
            frames.append(f"{os.path.basename(filename)}:{frame.lineno} {frame.name}")
        elif not frames or frames[-1] != "…":
            frames.append("…")
    if frames:
        parts.append("bei " + " → ".join(frames))
    return ", ".join(parts)


def log_exception(context: str, error: BaseException) -> None:
    """Eine unerwartete Ausnahme protokollieren — nie mit Traceback oder Ausnahmetext.

    Ohne Debug-Schalter nur ``context`` und der Ausnahmetyp; mit Schalter dazu
    ``errno`` und die Codestellen im Add-on (``describe_exception``).
    """
    detail = describe_exception(error) if _DEBUG["enabled"] else type(error).__name__
    log(f"{context} ({detail})")


# --------------------------------------------------------------------------
# Dateien, die einen Absturz überstehen
# --------------------------------------------------------------------------


def _read_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, UnicodeDecodeError):
        return {}


def _write_json_private(path: str, value: dict) -> None:
    """Atomar und nur für den Nutzer lesbar (0600) — auch in einem Zwischenschritt."""
    directory = os.path.dirname(path)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".tmp-", dir=directory)
    try:
        os.chmod(temporary, 0o600)
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(value, out, indent=2, sort_keys=True)
            out.write("\n")
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


class CredentialStore:
    """Token und ``deviceId`` je Serveradresse — Datei mit Modus 0600."""

    FILE = "credentials.json"

    def __init__(self, directory: str):
        self.path = os.path.join(directory, self.FILE)

    def _load(self) -> dict:
        data = _read_json(self.path)
        servers = data.get("servers")
        return servers if isinstance(servers, dict) else {}

    def _save(self, servers: dict) -> None:
        _write_json_private(self.path, {"version": 1, "servers": servers})

    def device_id(self, server: str) -> str | None:
        entry = self._load().get(server)
        value = entry.get("deviceId") if isinstance(entry, dict) else None
        return value if isinstance(value, str) and value else None

    def token(self, server: str) -> str | None:
        entry = self._load().get(server)
        value = entry.get("token") if isinstance(entry, dict) else None
        return value if isinstance(value, str) and value else None

    def remember_device(self, server: str, device_id: str) -> None:
        servers = self._load()
        entry = servers.get(server) if isinstance(servers.get(server), dict) else {}
        entry["deviceId"] = device_id
        servers[server] = entry
        self._save(servers)

    def store_token(self, server: str, token: str) -> None:
        servers = self._load()
        entry = servers.get(server) if isinstance(servers.get(server), dict) else {}
        entry["token"] = token
        servers[server] = entry
        self._save(servers)

    def forget_token(self, server: str) -> None:
        """Abmelden löscht das Token; die ``deviceId`` der Installation bleibt."""
        servers = self._load()
        entry = servers.get(server)
        if isinstance(entry, dict) and "token" in entry:
            del entry["token"]
            self._save(servers)
        elif not os.path.exists(self.path):
            return
        elif not servers:
            # Kaputte Datei: sie wird nicht länger gelesen, aber auch kein Token überleben.
            self._save({})


class TransferStore:
    """Angefangene Übernahmen und der zuletzt gewählte Blickpunkt je Dokument.

    Schlüssel ist der ``sourceProjectKey`` des Dokuments (``blender:<uuid>``):
    je Dokument höchstens eine angefangene Übernahme, wie im Archicad-Add-on je
    Ansicht.
    """

    FILE = "transfers.json"

    def __init__(self, directory: str):
        self.directory = directory
        self.path = os.path.join(directory, self.FILE)

    def _load(self) -> dict:
        data = _read_json(self.path)
        for key in ("pending", "assignments"):
            if not isinstance(data.get(key), dict):
                data[key] = {}
        return data

    def _save(self, data: dict) -> None:
        _write_json_private(self.path, {"version": 1, "pending": data["pending"], "assignments": data["assignments"]})

    def capture_dir(self, key: str) -> str:
        return os.path.join(self.directory, "captures", key)

    def pending(self, document_key: str) -> dict | None:
        entry = self._load()["pending"].get(document_key)
        if not isinstance(entry, dict):
            return None
        required = ("idempotencyKey", "directory", "manifestSha256", "target", "server")
        if not all(entry.get(name) for name in required):
            return None
        return entry

    def put_pending(self, document_key: str, entry: dict) -> None:
        data = self._load()
        data["pending"][document_key] = entry
        self._save(data)

    def remove_pending(self, document_key: str) -> None:
        data = self._load()
        entry = data["pending"].pop(document_key, None)
        self._save(data)
        directory = entry.get("directory") if isinstance(entry, dict) else None
        if directory and os.path.dirname(os.path.dirname(directory)) == self.directory:
            shutil.rmtree(directory, ignore_errors=True)

    def assignment(self, document_key: str) -> dict | None:
        entry = self._load()["assignments"].get(document_key)
        return entry if isinstance(entry, dict) else None

    def put_assignment(self, document_key: str, entry: dict) -> None:
        data = self._load()
        data["assignments"][document_key] = dict(entry, at=time.time())
        self._save(data)


def has_local_material(pending: dict) -> bool:
    """Liegen Manifest und Dateien einer angefangenen Übernahme noch vollständig vor?"""
    import hashlib

    directory = pending.get("directory") or ""
    try:
        with open(os.path.join(directory, "capture-manifest.json"), "rb") as handle:
            raw = handle.read()
    except OSError:
        return False
    if hashlib.sha256(raw).hexdigest() != pending.get("manifestSha256"):
        return False
    try:
        manifest = json.loads(raw.decode("utf-8"))
        for asset in manifest["assets"]:
            if asset["status"] != "present":
                continue
            path = os.path.join(directory, *asset["path"].split("/"))
            if ".." in asset["path"].split("/") or os.path.getsize(path) != asset["byteSize"]:
                return False
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return True


# --------------------------------------------------------------------------
# Blender
# --------------------------------------------------------------------------


def user_dir() -> str:
    """Der Nutzerordner dieser Extension — außerhalb der ``.blend`` und des Paketordners."""
    import bpy

    return bpy.utils.extension_path_user(__package__, create=True)


try:
    import bpy
    from bpy.props import BoolProperty, StringProperty
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

        def draw(self, _context):
            layout = self.layout
            layout.prop(self, "server_url")
            layout.prop(self, "device_name")
            layout.prop(self, "debug_logging")
            layout.label(text="Die Anmeldung liegt nicht hier, sondern im Nutzerordner der Extension (nur für dich lesbar).")

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
