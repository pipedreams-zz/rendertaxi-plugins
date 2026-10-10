"""Die Brücke vom Controller zu Cinema 4D — Nutzerordner, Dokument, Aufnahme, Statusleiste.

Alles hier läuft im Hauptfaden. Die Anmeldung liegt im Einstellungsordner des
Nutzers (``c4d.storage.GeGetC4DPath(c4d.C4D_PATH_PREFS)``), Unterordner
``rendertaxi``, Dateien mit Modus 0600 — nie im Dokument (ADR 0035).
"""

from __future__ import annotations

import os
import webbrowser

import c4d
from c4d import gui

from . import capture, corona, export, host, pictureviewer
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

    def document_file_name(self) -> str | None:
        """Der Name der gespeicherten Datei (``source.fileName``, 1.5.0); ``None`` ohne Speicherstand."""
        return capture.document_file_name(capture.active_document())

    def camera_name(self) -> str | None:
        """Der Name des Kameraobjekts der Renderansicht — Vorschlag für einen neuen Blickpunkt."""
        return capture.camera_name(capture.active_document())

    def ask_text(self, title: str, preset: str = "") -> str | None:
        """Eine Zeile Text vom Nutzer (``gui.InputDialog``); ``None`` bei „Abbrechen"."""
        answer = gui.InputDialog(title, preset)
        return answer if isinstance(answer, str) and answer.strip() else None

    def probe(self, model: bool = False) -> dict:
        """Die Laufzeitprobe; mit Modell auch ``geometryExport`` und ``cameraExport``."""
        doc = capture.active_document()
        capabilities = capture.probe(doc)
        if model:
            capabilities.update(export.probe(doc, capture.document_size(doc)))
        return capabilities

    def pass_rows(self):
        return capture.pass_rows(capture.active_document())

    def mask_rows(self) -> list[dict]:
        """Die aktivierten Object-Buffer-Masken (Corona) — ``number``, ``name``; ohne Corona leer."""
        doc = capture.active_document()
        if not any(spec.role == "mask" for spec, _state, _hint in capture.pass_rows(doc)):
            return []
        return [{"number": mask.number, "name": mask.name} for mask in corona.object_buffers(doc)]

    def render(self, kind: str, directory: str, size, roles: list[str], allowed_media_types, progress,
               bit_depth: int, rendered=None, masks=None):
        """``(files, planned, view_name)`` — Viewport oder Beauty mit Pässen (PNG, ``bit_depth`` 8 oder 16).

        ``rendered``: das Ergebnis von ``start_rendering`` — die Beauty kommt dann aus dessen Bildspeicher.
        """
        doc = capture.active_document()
        if kind == BEAUTY:
            beauty, passes, planned = capture.render_beauty(doc, directory, size, roles, allowed_media_types, progress,
                                                            bit_depth, rendered, masks)
            return [beauty, *passes], planned, capture.view_name(doc)
        return [capture.render_viewport(doc, directory, size, progress)], [], capture.view_name(doc)

    def direct_beauty(self, kind: str) -> bool:
        """Ob „Bild übernehmen“ direkt rendert (Ansicht oder Viewport Renderer) — sonst im Picture Viewer."""
        return pictureviewer.direct(capture.active_document(), kind)

    def renderer_label(self, engine) -> str:
        return capture.renderer_label(engine)

    def start_rendering(self, size: tuple[int, int], roles: list[str]):
        """Das Rendern im Picture Viewer starten — das Ergebnis fragt der Controller mit ``poll`` ab."""
        rendering = pictureviewer.Rendering(capture.active_document(), size, roles)
        rendering.start()
        return rendering

    def rendering_stale(self, rendering, size: tuple[int, int], roles: list[str]) -> str | None:
        return rendering.stale(capture.active_document(), size, roles)

    def document(self):
        """Das aktive Dokument als Identität — der Controller vergleicht es nur mit ``same_document``."""
        return capture.active_document()

    def same_document(self, then, now) -> bool:
        """Dasselbe, noch offene Dokument (``==`` auf dem Objekt hinter der Python-Hülle, nie ``id()`` oder Name)."""
        return pictureviewer.same_document(then, now)

    def scene_cameras(self) -> list[dict]:
        return export.scene_cameras(capture.active_document())

    def model_problem(self) -> str | None:
        return export.model_problem(capture.active_document())

    def model_estimate(self, kind: str) -> export.Estimate:
        return export.estimate(capture.active_document(), kind)

    def camera_problem(self, size: tuple[int, int], lens_and_shift: bool = False) -> str | None:
        return export.camera_problem(capture.active_document(), size, lens_and_shift)

    def export_model(self, kind: str, directory: str, size: tuple[int, int], progress, lens_and_shift: bool = False,
                     extra_cameras=()):
        """``(Datei, geometry, camera)`` — GLB der sichtbaren Objekte mit der Kamera der Renderansicht und den
        gewählten weiteren Kameras, und die Kamera für ein Bild ``size``.

        ``lens_and_shift``: der Server setzt Manifest 1.4.0 um — die Kamera trägt Objektiv und Shift.
        """
        doc = capture.active_document()
        progress("Modell exportieren", 10)
        model = export.export_model(doc, kind, directory, size, extra_cameras)
        progress("Modell exportieren", 100)
        return model.file, model.geometry, export.camera_block(doc, size, model.meters, lens_and_shift)

    def status(self, text: str, percent: int) -> None:
        """Fortschritt in der Statusleiste; ``percent < 0`` räumt sie."""
        if percent < 0:
            c4d.StatusClear()
            return
        c4d.StatusSetText(f"rendertaxi.ai: {text}")
        c4d.StatusSetBar(percent)
