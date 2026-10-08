"""Die Brücke vom Controller zu Rhino — Nutzerordner, Dokument, Ansichten, Aufnahme, Statusleiste.

Alles hier läuft im UI-Faden. Die Anmeldung liegt im Nutzerordner (``user_dir``), Dateien mit Modus 0600 —
nie im Dokument und nie in den ``.3dm``-Nutzerdaten (Sicherheits-Checkliste RTX-RH-002).
"""

from __future__ import annotations

import os
import sys
import webbrowser

from . import host, rhinocapture
from .camera import camera_block
from .controller import BEAUTY, CURRENT_VIEW


def user_dir() -> str:
    """``%APPDATA%\\rdtx.ai\\rhino`` (Windows), ``~/Library/Application Support/rdtx.ai/rhino`` (macOS).

    Ein eigener Ordner je Host unter der Kurzmarke: die Deinstallation darf genau ihn löschen, nichts sonst.
    ``RDTXAI_RHINO_USER_DIR`` setzt ihn für Tests.
    """
    override = os.environ.get("RDTXAI_RHINO_USER_DIR")
    if override:
        base = override
    elif sys.platform == "win32":
        base = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), host.MARK, "rhino")
    elif sys.platform == "darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support", host.MARK, "rhino")
    else:
        base = os.path.join(os.path.expanduser("~"), ".config", host.MARK, "rhino")
    os.makedirs(base, mode=0o700, exist_ok=True)
    return base


class RhinoAdapter:
    """Was der Controller vom Host braucht, für das aktive Dokument."""

    def user_dir(self) -> str:
        return user_dir()

    def host_version(self) -> str:
        return host.host_version()

    def open_url(self, url: str) -> None:
        try:
            webbrowser.open(url)
        except webbrowser.Error:
            pass  # ohne Browser bleiben Code und Adresse im Fenster

    def document_key(self, create: bool):
        return rhinocapture.document_key(rhinocapture.active_document(), create)

    def document_size(self) -> tuple:
        return rhinocapture.document_size(rhinocapture.active_document())

    def document_name(self):
        return rhinocapture.document_name(rhinocapture.active_document())

    def document_file_name(self):
        return rhinocapture.document_file_name(rhinocapture.active_document())

    def views(self) -> list:
        doc = rhinocapture.active_document()
        return [(CURRENT_VIEW, "Aktive Ansicht")] + rhinocapture.named_views(doc)

    def view_name(self, view_key: str):
        return rhinocapture.view_name(rhinocapture.active_document(), view_key)

    def ask_text(self, title: str, preset: str = ""):
        """Eine Zeile Text vom Nutzer; ``None`` bei „Abbrechen"."""
        import Rhino

        result, text = Rhino.UI.Dialogs.ShowEditBox(host.MARK, title, preset, False)
        return text if result and isinstance(text, str) and text.strip() else None

    def probe(self) -> dict:
        return rhinocapture.probe(rhinocapture.active_document())

    def pass_rows(self):
        return rhinocapture.pass_rows()

    def render(self, kind: str, directory: str, size, roles, allowed_media_types, progress, bit_depth: int,
               view_key: str = CURRENT_VIEW):
        """``(files, planned, view_name, size)`` — Ansicht oder Rendering mit Pässen (PNG, 8 oder 16 Bit)."""
        doc = rhinocapture.active_document()
        size = size or rhinocapture.document_size(doc)
        if kind == BEAUTY:
            beauty, passes, planned, name = rhinocapture.render(doc, directory, size, roles, allowed_media_types,
                                                                progress, bit_depth, view_key)
            return [beauty, *passes], planned, name, size
        viewport, name = rhinocapture.capture_viewport(doc, directory, size, progress, view_key)
        return [viewport], [], name, size

    def camera(self, size, lens_and_shift: bool, view_key: str = CURRENT_VIEW):
        doc = rhinocapture.active_document()
        view = rhinocapture.camera_view(doc, size, view_key)
        if view is None:
            return None
        return camera_block(view, size, rhinocapture.meters_per_unit(doc), lens_and_shift)

    def status(self, text: str, percent: int) -> None:
        """Fortschritt in der Statusleiste von Rhino; ``percent < 0`` räumt sie."""
        try:
            import Rhino

            if percent < 0:
                Rhino.UI.StatusBar.HideProgressMeter()
                return
            Rhino.UI.StatusBar.HideProgressMeter()
            Rhino.UI.StatusBar.ShowProgressMeter(0, 100, f"{host.MARK}: {text}", True, True)
            Rhino.UI.StatusBar.UpdateProgressMeter(int(percent), True)
        except Exception:  # noqa: BLE001 — eine Statusleiste darf nie eine Aufnahme verhindern
            pass
