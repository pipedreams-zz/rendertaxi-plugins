"""Die Brücke vom Controller zu Cinema 4D — Nutzerordner, Dokument, Aufnahme, Statusleiste.

Alles hier läuft im Hauptfaden. Die Anmeldung liegt im Einstellungsordner des
Nutzers (``c4d.storage.GeGetC4DPath(c4d.C4D_PATH_PREFS)``), Unterordner
``rendertaxi``, Dateien mit Modus 0600 — nie im Dokument (ADR 0035).
"""

from __future__ import annotations

import os
import webbrowser

import c4d

from . import capture, export, host
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

    def probe(self, model: bool = False) -> dict:
        """Die Laufzeitprobe; mit Modell auch ``geometryExport`` und ``cameraExport``."""
        doc = capture.active_document()
        capabilities = capture.probe(doc)
        if model:
            capabilities.update(export.probe(doc, capture.document_size(doc)))
        return capabilities

    def pass_rows(self):
        return capture.pass_rows(capture.active_document())

    def render(self, kind: str, directory: str, size, roles: list[str], allowed_media_types, progress,
               bit_depth: int):
        """``(files, planned, view_name)`` — Viewport oder Beauty mit Pässen (PNG, ``bit_depth`` 8 oder 16)."""
        doc = capture.active_document()
        if kind == BEAUTY:
            beauty, passes, planned = capture.render_beauty(doc, directory, size, roles, allowed_media_types, progress,
                                                            bit_depth)
            return [beauty, *passes], planned, capture.view_name(doc)
        return [capture.render_viewport(doc, directory, size, progress)], [], capture.view_name(doc)

    def model_problem(self) -> str | None:
        return export.model_problem(capture.active_document())

    def model_estimate(self, kind: str) -> export.Estimate:
        return export.estimate(capture.active_document(), kind)

    def camera_problem(self, size: tuple[int, int]) -> str | None:
        return export.camera_problem(capture.active_document(), size)

    def export_model(self, kind: str, directory: str, size: tuple[int, int], progress):
        """``(Datei, geometry, camera)`` — GLB der sichtbaren Objekte und die Kamera für ein Bild ``size``."""
        doc = capture.active_document()
        progress("Modell exportieren", 10)
        model = export.export_model(doc, kind, directory)
        progress("Modell exportieren", 100)
        return model.file, model.geometry, export.camera_block(doc, size, model.meters)

    def status(self, text: str, percent: int) -> None:
        """Fortschritt in der Statusleiste; ``percent < 0`` räumt sie."""
        if percent < 0:
            c4d.StatusClear()
            return
        c4d.StatusSetText(f"rendertaxi.ai: {text}")
        c4d.StatusSetBar(percent)
