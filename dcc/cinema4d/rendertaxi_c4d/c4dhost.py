"""Die Brücke vom Controller zu Cinema 4D — Nutzerordner, Dokument, Aufnahme, Statusleiste.

Alles hier läuft im Hauptfaden. Die Anmeldung liegt im Einstellungsordner des
Nutzers (``c4d.storage.GeGetC4DPath(c4d.C4D_PATH_PREFS)``), Unterordner
``rendertaxi``, Dateien mit Modus 0600 — nie im Dokument (ADR 0035).
"""

from __future__ import annotations

import os
import webbrowser

import c4d

from . import capture, host
from .controller import BEAUTY


class Cinema4DAdapter:
    """Was der Controller vom Host braucht, für das aktive Dokument."""

    def user_dir(self) -> str:
        directory = os.path.join(c4d.storage.GeGetC4DPath(c4d.C4D_PATH_PREFS), "rendertaxi")
        os.makedirs(directory, mode=0o700, exist_ok=True)
        return directory

    def host_version(self) -> str:
        return host.host_version()

    def open_url(self, url: str) -> None:
        try:
            webbrowser.open(url)
        except webbrowser.Error:
            pass  # ohne Browser bleiben Code und Adresse im Dialog

    def document_key(self, create: bool) -> str | None:
        return capture.document_key(capture.active_document(), create)

    def document_size(self) -> tuple[int, int]:
        return capture.document_size(capture.active_document())

    def document_name(self) -> str | None:
        return capture.document_name(capture.active_document())

    def probe(self) -> dict:
        return capture.probe(capture.active_document())

    def pass_rows(self):
        return capture.pass_rows(capture.active_document())

    def render(self, kind: str, directory: str, size, roles: list[str], allowed_media_types, progress):
        """``(files, planned, view_name)`` — Viewport oder Beauty mit Pässen."""
        doc = capture.active_document()
        if kind == BEAUTY:
            beauty, passes, planned = capture.render_beauty(doc, directory, size, roles, allowed_media_types, progress)
            return [beauty, *passes], planned, capture.view_name(doc)
        return [capture.render_viewport(doc, directory, size, progress)], [], capture.view_name(doc)

    def status(self, text: str, percent: int) -> None:
        """Fortschritt in der Statusleiste; ``percent < 0`` räumt sie."""
        if percent < 0:
            c4d.StatusClear()
            return
        c4d.StatusSetText(f"rendertaxi.ai: {text}")
        c4d.StatusSetBar(percent)
