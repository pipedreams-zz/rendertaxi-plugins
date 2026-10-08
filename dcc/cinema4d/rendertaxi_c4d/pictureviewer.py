"""Rendern im Picture Viewer — sichtbar, ohne Blockade, das Ergebnis aus dem eigenen Bildspeicher (RTX-C4D-010, #290).

Nutzerwunsch aus der Hostprobe vom 07.10.2026: mit Corona blockierte „Bild übernehmen" Cinema 4D ohne
Fortschritt. Nur der **Viewport Renderer** rendert weiter direkt (schnell, unsichtbar, ``capture.render_beauty``);
**jeder andere** Renderer — auch ein unbekannter — rendert hier:

* **Sichtbar und ohne Blockade.** ``RenderDocument`` läuft in einem eigenen Faden (``C4DThread``) mit
  ``RENDERFLAGS_CREATE_PICTUREVIEWER | RENDERFLAGS_OPEN_PICTUREVIEWER``: der Picture Viewer zeigt das Rendern in einem
  **neuen** Eintrag (die Bilder des Nutzers darin bleiben), Cinema 4D bleibt bedienbar, der Fortschritt kommt über den
  Rückruf. Sein Fenster öffnet der Hauptfaden beim Start (``PICTURE_VIEWER_COMMAND``). In einem Faden ohne Hauptschleife (``c4dpy``) wartet der Picture Viewer auf den Hauptfaden — dort rendert
  die Messung mit ``show=False``.
* **Eine Kopie des Dokuments**, im Hauptfaden angelegt (``GetClone(COPYFLAGS_DOCUMENT)``), gerendert mit
  ``RENDERFLAGS_NODOCUMENTCLONE``: der Nutzer kann weiterarbeiten, ohne dass der Faden ein Dokument liest, das sich
  gerade ändert. Was der Render braucht (Größe, nur das aktuelle Bild, kein Speichern; Positions-Pass für die Tiefe),
  steht **nur in der Kopie** — am Dokument des Nutzers ändert sich nichts, auch nicht vorübergehend. Eine Kopie mit
  dem Viewport Renderer gibt es hier nie (``docs/measurements/2026-10-04-klon-absturz.md``).
* **Ein Durchgang für Beauty und Ebenen.** Gerendert wird in eine ``MultipassBitmap`` (``COLORMODE_RGBf``) mit
  ``RENDERFLAGS_OCIO_BAKE_RENDERING``: das Bild selbst ist gebacken wie die direkte Beauty, die Ebenen der Kanäle
  bleiben roh — mit ``…_RAW_RENDERING`` sind sie bitgleich (gemessen 08.10.2026, c4dpy, Standard; Protokoll
  ``docs/measurements/2026-10-08-picture-viewer.md``). Gelesen wird wie bisher: Ebene über ``MPBTYPE_USERID``,
  Floats über ``GetPixelCnt``, nie über ``layer.Save`` (die Falle aus #207).
* **Beauty wie die Datei des Renderers.** Gespeichert wird der Bildspeicher so, wie Cinema 4D das Rendering selbst
  speichert: mit Alphakanal (``SAVEBIT_ALPHA``), wenn die Rendervoreinstellung ihn hat, sonst RGB — beides pixelgleich
  zur Datei, die der Renderer im selben Render schreibt (gemessen 08.10.2026, c4dpy). Die direkte Beauty (24 Bit)
  weicht davon um höchstens 1 von 255 ab.
* **Objekt-ID** braucht Antialiasing „Keines" und den Dateiweg: ein zweiter, unsichtbarer Durchgang im selben Faden,
  auf einer zweiten Kopie, deren Voreinstellung ``capture.object_render_data`` baut.
* **Alterung.** Angeboten wird nur das jüngste Ergebnis, und nur solange Dokument, Renderer, Bildgröße und Kamera
  dieselben sind wie beim Start (``fingerprint``); sonst sagt ``stale_reason`` in einem Satz, was sich geändert hat.

``c4d`` wird nur im Hauptfaden angefasst — außer ``RenderDocument`` selbst im Faden. Der Fortschritt landet in einer
Zahl, die ``poll`` im Hauptfaden liest.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time

import c4d
from c4d.threading import C4DThread

from . import capture

CREATE_PICTUREVIEWER = getattr(c4d, "RENDERFLAGS_CREATE_PICTUREVIEWER", 0)
OPEN_PICTUREVIEWER = getattr(c4d, "RENDERFLAGS_OPEN_PICTUREVIEWER", 0)
NO_DOCUMENT_CLONE = getattr(c4d, "RENDERFLAGS_NODOCUMENTCLONE", 0)
# Der Befehl „Bild-Manager“ (Picture Viewer) — am Host 2026.3.1 nachgeschlagen (``FilterPluginList``, 08.10.2026). Aus dem
# Faden legen die Flags den Eintrag im Picture Viewer an, öffnen sein Fenster aber nicht (Hostprobe 08.10.2026): das tut
# der Hauptfaden beim Start, nur wenn es nicht schon offen ist — der Befehl schaltet um.
PICTURE_VIEWER_COMMAND = 430000700

RENDERING = "rendering"
READY = "ready"
FAILED = "failed"
CANCELLED = "cancelled"


def direct(doc, kind: str) -> bool:
    """Ob „Bild übernehmen" direkt rendert: die Ansicht, oder die Beauty mit dem Viewport Renderer."""
    if kind != "beauty":
        return True
    rd = doc.GetActiveRenderData()
    return rd is not None and capture.VIEWPORT_RENDERER is not None and rd[c4d.RDATA_RENDERENGINE] == capture.VIEWPORT_RENDERER


def _camera_state(doc):
    view = capture.render_view(doc)
    camera = view.GetSceneCamera(doc) if view is not None else None
    if camera is None:
        return None
    matrix = camera.GetMg()
    values = [matrix.off, matrix.v1, matrix.v2, matrix.v3]
    numbers = tuple(round(float(c), 6) for vector in values for c in (vector.x, vector.y, vector.z))
    lens = tuple(capture._get(camera, name) for name in ("CAMERA_FOCUS", "CAMERAOBJECT_APERTURE",
                                                         "CAMERAOBJECT_FILM_OFFSET_X", "CAMERAOBJECT_FILM_OFFSET_Y"))
    return camera.GetName(), numbers, lens


def fingerprint(doc, size: tuple[int, int], roles) -> dict:
    """Was ein Ergebnis an seine Einstellung bindet: Dokument, Renderer, Bildgröße, Kamera und die gewählten Pässe."""
    rd = doc.GetActiveRenderData()
    return {"document": doc, "engine": rd[c4d.RDATA_RENDERENGINE] if rd is not None else None,
            "size": (int(size[0]), int(size[1])), "camera": _camera_state(doc), "roles": set(roles)}


def stale_reason(then: dict, now: dict) -> str | None:
    """Warum ein Ergebnis nicht mehr zur Einstellung passt — ``None``, wenn es passt."""
    if not same_document(then["document"], now["document"]):
        return "Das gerenderte Bild gehört zu einem anderen Dokument. Bitte neu rendern."
    if then["engine"] != now["engine"]:
        return "Der Renderer ist seit dem Rendern ein anderer. Bitte neu rendern."
    if then["size"] != now["size"]:
        return "Die Bildgröße ist seit dem Rendern eine andere. Bitte neu rendern."
    if then["camera"] != now["camera"]:
        return "Die Kamera hat sich seit dem Rendern geändert. Bitte neu rendern."
    if not now["roles"] <= then["roles"]:
        return "Seit dem Rendern sind weitere Pässe gewählt. Bitte neu rendern."
    return None


def same_document(then, now) -> bool:
    """Dasselbe, noch offene Dokument — ``==`` vergleicht in Cinema 4D das Objekt hinter der Python-Hülle; ``id()`` nicht,
    ``GetActiveDocument`` liefert jedes Mal eine neue Hülle."""
    if then is None or now is None:
        return then is now
    alive = getattr(then, "IsAlive", None)
    if alive is not None and not alive():
        return False
    return then == now


class _Thread(C4DThread):
    """Der Faden: nacheinander die sichtbaren und die unsichtbaren Durchgänge; Ergebnis und Fortschritt als Zahlen."""

    def __init__(self, passes):
        super().__init__()
        self.passes = passes  # (Dokument, Container, Bitmap, Flags, Anteil am Fortschritt)
        self.results: list = []
        self.percent = 0
        self.error: str | None = None

    def Main(self):  # noqa: N802 — Cinema 4D ruft Main im Faden
        done = 0.0
        for doc, data, bitmap, flags, share in self.passes:
            if self.TestBreak():
                self.results.append(getattr(c4d, "RENDERRESULT_USERBREAK", -1))
                return

            def report(value, *_rest, done=done, share=share):
                try:
                    part = max(0.0, min(1.0, float(value)))
                except (TypeError, ValueError):
                    part = 0.0
                self.percent = int((done + share * part) * 100)

            try:
                result = c4d.documents.RenderDocument(doc, data, bitmap, flags, self.Get(), report)
            except Exception as error:  # noqa: BLE001 — im Faden nie werfen: der Hauptfaden liest den Grund
                self.error = type(error).__name__
                return
            self.results.append(result)
            if result != c4d.RENDERRESULT_OK:
                return
            done += share
        self.percent = 100


class Rendering:
    """Ein Rendern mit dem aktiven Renderer im Picture Viewer — ``start``, dann ``poll`` aus dem Timer, dann lesen.

    Was dabei herauskommt, liest ``capture.render_beauty(..., rendered=self)``: ``bitmap`` (Beauty und Ebenen),
    ``position`` (Positions-Pass in der Kopie), ``object_folder`` und ``object_ids`` (Dateiweg der Objekt-ID).
    """

    def __init__(self, doc, size: tuple[int, int], roles: list[str], show: bool = True):
        rd = doc.GetActiveRenderData()
        if rd is None:
            raise capture.CaptureError("Das Dokument hat keine aktive Rendervoreinstellung.")
        self.size = (int(size[0]), int(size[1]))
        self.roles = list(roles)
        self.engine = rd[c4d.RDATA_RENDERENGINE]
        # Mit Alphakanal gerendert: die Beauty geht dann als PNG mit Alpha, wie Cinema 4D sie selbst speichert.
        self.alpha = bool(capture._get(rd, "RDATA_ALPHACHANNEL", False))
        self.fingerprint = fingerprint(doc, self.size, self.roles)
        self.started = time.time()
        self.state = RENDERING
        self.show = show
        self.problem: str | None = None
        self.position = False
        self.object_folder: str | None = None
        self._ids: list[int] = []
        self._id_problem: str | None = None
        self._documents: list = []
        self.bitmap = c4d.bitmaps.MultipassBitmap(self.size[0], self.size[1], c4d.COLORMODE_RGBf)
        if self.bitmap is None:
            raise capture.CaptureError("Cinema 4D konnte keine MultipassBitmap anlegen.")
        passes = []
        try:
            passes.append(self._visible_pass(doc, show))
            if "object-id" in self.roles and self.engine in capture.MULTIPASS_RENDERERS:
                extra = self._object_pass(doc)
                if extra is not None:
                    passes.append(extra)
        except Exception:
            self._release_documents()
            raise
        shares = (1.0,) if len(passes) == 1 else (0.8, 0.2)
        self._thread = _Thread([(*entry, share) for entry, share in zip(passes, shares)])

    # -- Vorbereitung im Hauptfaden -------------------------------------------

    def _clone(self, doc):
        twin = doc.GetClone(c4d.COPYFLAGS_DOCUMENT)
        if twin is None:
            raise capture.CaptureError("Cinema 4D konnte das Dokument für das Rendern nicht kopieren.")
        self._documents.append(twin)
        return twin

    def _visible_pass(self, doc, show: bool):
        twin = self._clone(doc)
        rd = twin.GetActiveRenderData()
        data = rd.GetDataInstance()
        capture._prepare_data(data, self.size)
        if "depth" in self.roles and self.engine in capture.MULTIPASS_RENDERERS and capture.position_pass_available():
            try:
                self._add_position_pass(rd)
                data[c4d.RDATA_MULTIPASS_ENABLE] = True
                self.position = True
            except Exception:  # noqa: BLE001 — Regel 3: ohne Positions-Pass bleibt die Tiefe geplant
                self.position = False
        flags = capture.render_flags(True) | NO_DOCUMENT_CLONE
        if show:
            flags |= CREATE_PICTUREVIEWER | OPEN_PICTUREVIEWER
        return twin, data, self.bitmap, flags

    @staticmethod
    def _add_position_pass(rd) -> None:
        """Positions-Videopost und Kanal „Post-Effekte“ — in der Voreinstellung der **Kopie**, wie ``transient`` es
        sonst für die Dauer eines Renders im Dokument tut."""
        if not any(post.GetType() == capture.POSITION_VIDEOPOST for post in _items(rd.GetFirstVideoPost())):
            post = c4d.BaseList2D(capture.POSITION_VIDEOPOST)
            if post is None:
                raise RuntimeError("Videopost nicht anlegbar")
            rd.InsertVideoPost(post)
        if not any(channel[c4d.MULTIPASSOBJECT_TYPE] == capture.POST_EFFECTS for channel in _items(rd.GetFirstMultipass())):
            channel = c4d.BaseList2D(c4d.Zmultipass)
            channel[c4d.MULTIPASSOBJECT_TYPE] = capture.POST_EFFECTS
            rd.InsertMultipass(channel)

    def _object_pass(self, doc):
        """Der zweite Durchgang für die Objekt-ID: eine zweite Kopie, deren aktive Voreinstellung den Dateiweg trägt."""
        self._ids = capture.object_buffer_ids(doc)
        if not self._ids:
            self._id_problem = capture.object_id_problem(self._ids, 16)
            return None
        twin = self._clone(doc)
        rd = twin.GetActiveRenderData()
        self.object_folder = tempfile.mkdtemp(prefix="rendertaxi-objektpuffer-")
        try:
            copy = capture.object_render_data(rd, self._ids, self.object_folder, self.size)
        except capture.PassMissing as reason:
            self._id_problem = str(reason)
            return None
        twin.InsertRenderData(copy)
        twin.SetActiveRenderData(copy)
        bitmap = c4d.bitmaps.MultipassBitmap(self.size[0], self.size[1], c4d.COLORMODE_RGBf)
        return twin, copy.GetDataInstance(), bitmap, capture.render_flags(False) | NO_DOCUMENT_CLONE

    # -- Ablauf ---------------------------------------------------------------

    def start(self) -> None:
        if self.show:
            open_picture_viewer()
        if not self._thread.Start():
            self._finish(FAILED, "Cinema 4D konnte das Rendern nicht starten.")

    def poll(self) -> tuple[str, int]:
        """Im Hauptfaden (Timer): ``(Zustand, Prozent)``; beim ersten Blick nach dem Ende werden die Kopien verworfen."""
        if self.state != RENDERING:
            return self.state, 100 if self.state == READY else self._thread.percent
        if self._thread.IsRunning():
            return RENDERING, self._thread.percent
        thread = self._thread
        if thread.error:
            self._finish(FAILED, f"Cinema 4D hat das Rendern abgebrochen ({thread.error}).")
        elif not thread.results or any(result != c4d.RENDERRESULT_OK for result in thread.results):
            result = next((r for r in thread.results if r != c4d.RENDERRESULT_OK), None)
            if result == getattr(c4d, "RENDERRESULT_USERBREAK", object()):
                self._finish(CANCELLED, "Rendern abgebrochen.")
            else:
                self._finish(FAILED, capture._result_text(result))
        else:
            self._finish(READY, None)
        return self.state, thread.percent

    def cancel(self) -> None:
        if self.state == RENDERING:
            self._thread.End(False)

    def _finish(self, state: str, problem: str | None) -> None:
        self.state = state
        self.problem = problem
        self._release_documents()
        if state != READY:
            self.release()

    def _release_documents(self) -> None:
        for twin in self._documents:
            c4d.documents.KillDocument(twin)
        self._documents = []

    def release(self) -> None:
        """Bildspeicher und Objektpuffer-Dateien freigeben — das Ergebnis ist danach nicht mehr lesbar."""
        if self._thread.IsRunning():
            self._thread.End(True)
        self._release_documents()
        if self.object_folder:
            shutil.rmtree(self.object_folder, ignore_errors=True)
            self.object_folder = None
        self.bitmap = None
        if self.state == READY:
            self.state = CANCELLED

    # -- Lesen ----------------------------------------------------------------

    def object_ids(self, bit_depth: int) -> list[int]:
        """Die IDs des zweiten Durchgangs — ``PassMissing``, wenn er fehlt oder die Bittiefe sie nicht fasst."""
        if self._id_problem:
            raise capture.PassMissing(self._id_problem)
        if not self.object_folder or not os.path.isdir(self.object_folder):
            raise capture.PassMissing("Die Objekt-ID war beim Rendern nicht gewählt. Bitte neu rendern.")
        problem = capture.object_id_problem(self._ids, bit_depth)
        if problem:
            raise capture.PassMissing(problem)
        return list(self._ids)

    def stale(self, doc, size: tuple[int, int], roles) -> str | None:
        """Warum dieses Ergebnis nicht mehr zur jetzigen Einstellung passt — ``None``, wenn es passt."""
        return stale_reason(self.fingerprint, fingerprint(doc, size, roles))


def open_picture_viewer() -> None:
    """Das Fenster des Picture Viewers öffnen, wenn es zu ist — ein Fehler hier hält das Rendern nie auf (Regel 3)."""
    try:
        if not c4d.IsCommandChecked(PICTURE_VIEWER_COMMAND):
            c4d.CallCommand(PICTURE_VIEWER_COMMAND)
    except Exception:  # noqa: BLE001 — ohne Fenster rendert es trotzdem, der Fortschritt steht im Plugin
        pass


def _items(first):
    item = first
    while item is not None:
        yield item
        item = item.GetNext()
