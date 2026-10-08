"""Aufnahme in Rhino 8 — Ansicht (``ViewCapture``), Rendering (Rhino Render) und Datenpässe aus den Renderkanälen.

Nur dieses Modul und ``rhinohost.py`` fassen ``Rhino`` an; alles läuft im UI-Faden.

**Ansicht.** ``Rhino.Display.ViewCapture`` in der Zielgröße, im Anzeigemodus der Ansicht, ohne Gitter, Gitterachsen
und Weltachsen — das sind Eigenschaften des Capture-Objekts, die Ansicht selbst bleibt unberührt (gemessen:
Gitter vorher und nachher gleich). PNG 8 Bit RGBA mit ``sRGB``-Chunk (QR-02, gemessen).

**Rendering.** ``-_Render`` mit dem aktuellen Renderer; die Datenpässe nur mit Rhino Render. Was das Rendern
braucht — Kanalwahl, Bildgröße, Quelle „aktive Ansicht" —, setzt ``TransientRender`` nur für diesen Lauf in den
Rendereinstellungen des Dokuments und stellt danach die **Kopie der vorherigen Einstellungen** zurück (Regel 2:
die Einstellungen des Nutzers sind danach dieselben; zur Änderungsmarke siehe ``TransientRender``). Aus einem Skript
ist ``-_Render`` modal (QR-08, gemessen): der Aufruf kehrt nach dem fertigen Bild zurück, die Oberfläche
zeichnet währenddessen weiter.

**Kanäle.** Rhino speichert aus dem Renderfenster nur RGBA (auch als EXR, gemessen). Die Kanäle liest das Plugin
deshalb über ``RenderWindow.FromSessionId(…).OpenChannel(…)``. Die Kennung der Render-Sitzung gibt RhinoCommon
nur während des Renderns preis (die Pipeline steht dann in ``RenderPipeline.m_all_render_pipelines``); ein
Hintergrundfaden liest sie dort ab — **nicht öffentliche** API, deshalb mit Rückfall: fehlt sie, bleiben die
Pässe geplant mit Grund, das Rendering geht. ``Channel.GetValues`` erwartet den Zeilenabstand in **Bytes**
(Breite · ``PixelSize``), gemessen.
"""

from __future__ import annotations

import os
import struct
import threading
import time
import uuid
from contextlib import contextmanager

import Rhino
import System

from . import host, passes, pngwrite
from .camera import View
from .rendertaxi_client import manifest as mf
from .rendertaxi_client.log import log

PNG = mf.PNG_MEDIA_TYPE
RHINO_RENDER = System.Guid("4f793ad6-60ce-4aaf-8a7e-6e36c752486c")
DOCUMENT_SECTION = host.MARK
DOCUMENT_ENTRY = ".documentKey"  # führender Punkt: verborgener Dokument-User-Text (setdocumentusertext.htm)
DOCUMENT_KEY_PREFIX = f"{host.KEY}:"
NAMED_PREFIX = "named:"
SC = Rhino.Render.RenderWindow.StandardChannels
CO = Rhino.Render.ComponentOrders


class CaptureError(RuntimeError):
    """Ein Grund, warum nicht aufgenommen wurde — lesbar, ohne Pfad."""


class PassSpec:
    def __init__(self, role, capability, label, channel, color_space, channels, components):
        self.role = role
        self.capability = capability
        self.label = label
        self.channel = channel
        self.color_space = color_space
        self.channels = channels
        self.components = components


PASSES = (
    PassSpec("depth", "depthPass", "Tiefe", SC.DistanceFromCamera, "non-color", pngwrite.GRAY, 1),
    PassSpec("normal", "normalPass", "Normalen", SC.NormalXYZ, "non-color", pngwrite.RGB, 3),
    PassSpec("albedo", "albedoPass", "Albedo", SC.AlbedoRGB, "linear", pngwrite.RGB, 3),
    PassSpec("object-id", "objectIdPass", "Objekt-ID", SC.ObjectIds, "non-color", pngwrite.GRAY, 1),
    PassSpec("material-id", "materialIdPass", "Material-ID", SC.MaterialIds, "non-color", pngwrite.GRAY, 1),
)
PASS_BY_ROLE = {spec.role: spec for spec in PASSES}


# --------------------------------------------------------------------------
# Dokument und Ansicht
# --------------------------------------------------------------------------


def active_document():
    doc = Rhino.RhinoDoc.ActiveDoc
    if doc is None:
        raise CaptureError("Kein Rhino-Dokument geöffnet.")
    return doc


def meters_per_unit(doc) -> float:
    return float(Rhino.RhinoMath.UnitScale(doc.ModelUnitSystem, Rhino.UnitSystem.Meters))


def document_size(doc) -> tuple:
    """Die Ausgabegröße der Rendereinstellungen; „Größe der Ansicht" heißt: die aktive Ansicht."""
    settings = doc.RenderSettings
    if settings.UseViewportSize:
        view = doc.Views.ActiveView
        if view is not None:
            size = view.ActiveViewport.Size
            return max(int(size.Width), 1), max(int(size.Height), 1)
    size = settings.ImageSize
    return max(int(size.Width), 1), max(int(size.Height), 1)


def document_name(doc):
    name = doc.Name or ""
    return os.path.splitext(name)[0] or None


def document_file_name(doc):
    """Der Name der gespeicherten Datei ohne Ordner; ``None`` für ein nie gespeichertes Dokument."""
    path = doc.Path or ""
    return os.path.basename(path) or None


def _open_documents():
    try:
        return list(Rhino.RhinoDoc.OpenDocuments())
    except Exception:  # noqa: BLE001
        return []


def _read_key(doc):
    try:
        value = doc.Strings.GetValue(DOCUMENT_SECTION, DOCUMENT_ENTRY)
    except Exception:  # noqa: BLE001 — Regel 3: unlesbar heißt keine Kennung
        return None
    if not isinstance(value, str) or not value.startswith(DOCUMENT_KEY_PREFIX):
        return None
    try:
        uuid.UUID(value[len(DOCUMENT_KEY_PREFIX):])
    except ValueError:
        return None
    return value


def document_key(doc, create: bool):
    """``sourceProjectKey`` als ``rhino:<uuid4>`` im verborgenen Dokument-User-Text (QR-11, gemessen).

    Die Kennung übersteht Speichern und erneutes Lesen; „Speichern unter" und eine Dateikopie tragen sie mit,
    **und** ein Import in ein anderes Dokument bringt sie dorthin (gemessen). Sie ist deshalb ein Hinweis, keine
    Autorität (ADR 0009, Entscheidung 8): das Plugin **schlägt** den zuletzt gewählten Blickpunkt nur vor. Tragen
    zwei offene Dokumente dieselbe Kennung, bekommt das aufnehmende eine neue. Im Dokument steht nur diese
    Kennung — nie ein Token.
    """
    value = _read_key(doc)
    if not create:
        return value
    shared = value is not None and any(
        other.RuntimeSerialNumber != doc.RuntimeSerialNumber and _read_key(other) == value
        for other in _open_documents())
    if value is not None and not shared:
        return value
    value = f"{DOCUMENT_KEY_PREFIX}{uuid.uuid4()}"
    doc.Strings.SetString(DOCUMENT_SECTION, DOCUMENT_ENTRY, value)
    return value


def named_views(doc) -> list:
    """``(Kennung, Name)`` der benannten Ansichten — ``named:<NamedViewId>`` (QR-10: übersteht Umbenennen,
    Speichern und erneutes Lesen; gemessen). Der Name ist nie der Schlüssel."""
    found = []
    table = doc.NamedViews
    for index in range(table.Count):
        info = table[index]
        try:
            ident = info.NamedViewId
        except Exception:  # noqa: BLE001 — vor 7.28 gab es die Kennung nicht
            continue
        if ident == System.Guid.Empty:
            continue
        found.append((f"{NAMED_PREFIX}{ident}", info.Name or "Benannte Ansicht"))
    return found


def _named_index(doc, view_key: str):
    if not view_key.startswith(NAMED_PREFIX):
        return None
    wanted = view_key[len(NAMED_PREFIX):]
    table = doc.NamedViews
    for index in range(table.Count):
        if str(table[index].NamedViewId) == wanted:
            return index
    raise CaptureError("Die gewählte benannte Ansicht gibt es im Dokument nicht mehr.")


def view_name(doc, view_key: str = ""):
    index = _named_index(doc, view_key) if view_key else None
    if index is not None:
        return doc.NamedViews[index].Name
    view = doc.Views.ActiveView
    if view is None:
        return None
    if isinstance(view, Rhino.Display.RhinoPageView):
        return view.PageName
    return view.ActiveViewport.Name


class ViewState:
    """Was ``NamedViews.Restore`` an einem Viewport ändert — gesichert und einzeln zurückgestellt (Regel 2).

    Gemessen (Rhino 8.35): ``PopViewProjection`` nach ``PushViewProjection`` liefert ``False`` und stellt nichts
    zurück; ``Restore`` benennt den Viewport nach der benannten Ansicht um; ``SetViewProjection`` mit einer
    ``ViewportInfo``-Kopie stellt Ort, Richtung und Linse her, aber nicht den Zielpunkt. Deshalb: Projektion,
    Zielpunkt, Name und Anzeigemodus je für sich.
    """

    def __init__(self, viewport):
        self.viewport = viewport
        self.info = Rhino.DocObjects.ViewportInfo(viewport)
        self.target = viewport.CameraTarget
        self.name = viewport.Name
        self.mode = viewport.DisplayMode.Id if viewport.DisplayMode is not None else None

    def restore(self) -> None:
        viewport = self.viewport
        viewport.SetViewProjection(self.info, True)
        viewport.SetCameraTarget(self.target, False)
        viewport.Name = self.name
        if self.mode is not None and (viewport.DisplayMode is None or viewport.DisplayMode.Id != self.mode):
            mode = Rhino.Display.DisplayModeDescription.GetDisplayMode(self.mode)
            if mode is not None:
                viewport.DisplayMode = mode


@contextmanager
def source_view(doc, view_key: str):
    """Die Ansicht der Aufnahme: die aktive — oder eine benannte, nur für die Dauer in der aktiven Ansicht.

    Danach steht die aktive Ansicht wieder wie vorher (``ViewState``; ``tests/host_check.py`` vergleicht Ort,
    Ziel, Linse, Name und Anzeigemodus vor und nach der Aufnahme).
    """
    view = doc.Views.ActiveView
    if view is None:
        raise CaptureError("Keine aktive Ansicht — bitte eine Ansicht anklicken.")
    index = _named_index(doc, view_key) if view_key else None
    if index is None:
        yield view
        return
    if isinstance(view, Rhino.Display.RhinoPageView):
        raise CaptureError("Eine benannte Ansicht lässt sich nur in einer Modellansicht aufnehmen, nicht im Layout.")
    saved = ViewState(view.ActiveViewport)
    try:
        doc.NamedViews.Restore(index, view.ActiveViewport)
        view.Redraw()
        yield view
    finally:
        saved.restore()
        view.Redraw()


# --------------------------------------------------------------------------
# Laufzeitprobe
# --------------------------------------------------------------------------


def renderer_is_rhino_render() -> bool:
    try:
        return Rhino.Render.Utilities.DefaultRenderPlugInId == RHINO_RENDER
    except Exception:  # noqa: BLE001
        return False


def channel_access() -> bool:
    """Ob die Kennung der Render-Sitzung erreichbar ist (nicht öffentliche RhinoCommon-Felder)."""
    return _pipeline_fields() is not None


def pass_status(spec) -> tuple:
    if not renderer_is_rhino_render():
        return "requires-user-action", "Die Pässe liefert Rhino Render: im Dokument als aktuellen Renderer wählen."
    if not channel_access():
        return "unavailable", "Diese Rhino-Fassung gibt die Renderkanäle nicht heraus."
    return "available", ""


def probe(doc) -> dict:
    """``source.host.capabilities`` aus dem laufenden Rhino — nicht aus der Matrixdatei.

    Gemeldet wird nur, was diese Probe prüft: Ansicht, Rendering und die Pässe nach Renderer und Zugang zu den
    Kanälen. Alles andere fehlt und heißt damit ``unknown``.
    """
    capabilities: dict = {}
    if doc.Views.ActiveView is None:
        capabilities["viewportCapture"] = {"state": "requires-user-action", "constraints": {"mediaTypes": [PNG]}}
    else:
        capabilities["viewportCapture"] = {"state": "available", "constraints": {"mediaTypes": [PNG]}}
    capabilities["beautyRender"] = {"state": "available", "constraints": {"mediaTypes": [PNG]}}
    for spec in PASSES:
        state, _hint = pass_status(spec)
        entry = {"state": state}
        if state != "unavailable":
            entry["constraints"] = {"mediaTypes": [PNG]}
        capabilities[spec.capability] = entry
    return capabilities


def pass_rows() -> list:
    """Die Pässe für das Fenster: (Pass, Zustand, Hinweis)."""
    return [(spec, *pass_status(spec)) for spec in PASSES]


# --------------------------------------------------------------------------
# Ansicht aufnehmen
# --------------------------------------------------------------------------


def _save_bitmap(bitmap, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    bitmap.Save(path, System.Drawing.Imaging.ImageFormat.Png)
    if not os.path.isfile(path):
        raise CaptureError("Rhino hat das Bild nicht gespeichert.")


def capture_viewport(doc, root: str, size, progress, view_key: str = ""):
    """Die Ansicht als PNG in ``size`` — ohne Gitter und Achsen, im Anzeigemodus der Ansicht."""
    width, height = size
    path = os.path.join(root, "images", "viewport.png")
    progress("Bild aufnehmen", 10)
    with source_view(doc, view_key) as view:
        capture = Rhino.Display.ViewCapture()
        capture.Width = int(width)
        capture.Height = int(height)
        capture.ScaleScreenItems = False
        capture.DrawAxes = False
        capture.DrawGrid = False
        capture.DrawGridAxes = False
        capture.TransparentBackground = False
        mode = view.ActiveViewport.DisplayMode.EnglishName if view.ActiveViewport.DisplayMode else "?"
        bitmap = capture.CaptureToBitmap(view)
        if bitmap is None:
            raise CaptureError("Rhino hat die Ansicht nicht aufgenommen.")
        _save_bitmap(bitmap, path)
        name = view_name(doc, view_key)
    progress("Bild aufnehmen", 100)
    note = (f"ViewCapture {width} × {height}, Anzeigemodus {mode}, ohne Gitter und Achsen; PNG 8 Bit RGBA mit "
            f"sRGB-Chunk, gemeldet als srgb (am Host gemessen).")
    return mf.capture_file(path, root, "viewport", PNG, mf.describe_png(path), note), name


# --------------------------------------------------------------------------
# Rendern
# --------------------------------------------------------------------------

_FIELDS: dict = {}


def _pipeline_fields():
    """``(Registry, Sitzungsfeld)`` der Render-Pipelines per Reflexion — ``None``, wenn es sie nicht gibt."""
    if "value" in _FIELDS:
        return _FIELDS["value"]
    value = None
    try:
        flags = System.Reflection.BindingFlags
        kind = System.Type.GetType("Rhino.Render.RenderPipeline, RhinoCommon")
        registry = kind.GetField("m_all_render_pipelines", flags.Static | flags.NonPublic)
        session = kind.GetField("m_session_id", flags.Instance | flags.NonPublic)
        if registry is not None and session is not None:
            value = (registry, session)
    except Exception:  # noqa: BLE001
        value = None
    _FIELDS["value"] = value
    return value


class _SessionWatch:
    """Liest während des Renderns die Kennung der Render-Sitzung ab (Hintergrundfaden, nur Lesen)."""

    def __init__(self):
        self.found: list = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="rdtxai-render-session", daemon=True)

    def _run(self):
        fields = _pipeline_fields()
        if fields is None:
            return
        registry, session = fields
        while not self._stop.is_set():
            try:
                for pair in registry.GetValue(None):
                    ident = str(session.GetValue(pair.Value))
                    if ident not in self.found:
                        self.found.append(ident)
            except Exception:  # noqa: BLE001 — die Registry ändert sich während des Lesens
                pass
            time.sleep(0.02)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(1.0)
        return False


def render_window(sessions, size, channels):
    """Das Renderfenster **dieses** Laufs unter den beobachteten Sitzungen — ``None``, wenn keines passt.

    Während des Renderns laufen auch andere Render-Pipelines (Vorschaubilder neuer Materialien rendert Rhino
    ebenfalls mit Rhino Render, gemessen). Gewählt wird deshalb nur ein Fenster in der Bildgröße der Aufnahme,
    das alle angeforderten Kanäle trägt — das zuletzt gestartete zuerst.
    """
    width, height = int(size[0]), int(size[1])
    for ident in reversed(sessions):
        try:
            window = Rhino.Render.RenderWindow.FromSessionId(System.Guid(ident))
            if window is None:
                continue
            got = window.Size()
            if got.Width != width or got.Height != height:
                continue
            requested = set(str(c) for c in window.GetRequestedRenderChannelsAsStandardChannels())
            if all(str(c) in requested for c in channels):
                return window
        except Exception:  # noqa: BLE001 — eine verschwundene Sitzung ist keine Aufnahme
            continue
    return None


class TransientRender:
    """Rendereinstellungen nur für einen Lauf — danach exakt die vorherigen (Regel 2).

    Gemessen (Rhino 8.35, 08.10.2026):

    * Jede Zuweisung an ``doc.RenderSettings`` markiert das Dokument als geändert. ``doc.Modified`` lässt sich
      unter macOS nicht setzen, auch nicht in einem Befehl oder einem zweiten Befehlsaufruf
      (``tools/measure_modified_mark.py``); Rhino für macOS speichert ein gespeichertes Dokument rund 15 s später
      selbst, dann ist die Marke weg. Unter Windows ist das Setzen nicht gemessen — der Versuch unten schadet nicht.
    * Am Objekt des Dokuments (``BeginChange`` … ``EndChange``) markiert ein Wechsel des **Kanalmodus** das
      Dokument nicht; eine geänderte **Kanalliste** markiert es in jedem Änderungskontext (Program, Ignore, UI).

    Deshalb: die Kanalliste nur ändern, wenn die gespeicherte nicht schon alle gebrauchten Kanäle enthält;
    Größe, „Größe der Ansicht" und Quelle nur dann über eine Zuweisung, wenn sie von dem abweichen, was das
    Rendern braucht. Danach sind die Einstellungen wieder dieselben. War das Dokument vorher unverändert und
    musste geändert werden, bleibt die Änderungsmarke gesetzt (``marked``) — Protokoll und Angaben zur
    Aufnahme nennen es; Rhino fragt dann beim Schließen nach dem Speichern.
    """

    def __init__(self, doc, size, channels):
        self.doc = doc
        self.size = (int(size[0]), int(size[1]))
        self.channels = channels
        self.saved = None
        self.saved_channels = None
        self.modified = None
        self.undo = None
        self.marked = False
        self.list_changed = False
        self.problems: list = []

    def _needs_settings(self) -> bool:
        settings = self.doc.RenderSettings
        source = settings.RenderSource == Rhino.Render.RenderSettings.RenderingSources.ActiveViewport
        return not source or document_size(self.doc) != self.size

    def _channels(self, mode, ids=None) -> None:
        """Modus und — nur wenn ``ids`` — die Kanalliste; schon das Zuweisen derselben Liste markiert (gemessen)."""
        channels = self.doc.RenderSettings.RenderChannels
        channels.BeginChange(Rhino.Render.RenderContent.ChangeContexts.Program)
        try:
            if str(channels.Mode) != str(mode):
                channels.Mode = mode
            if ids is not None:
                channels.CustomList = System.Array[System.Guid](list(ids))
        finally:
            channels.EndChange()

    def __enter__(self):
        doc = self.doc
        self.modified = doc.Modified
        self.undo = doc.UndoRecordingEnabled
        doc.UndoRecordingEnabled = False
        if self._needs_settings():
            self.saved = Rhino.Render.RenderSettings(doc.RenderSettings)
            settings = Rhino.Render.RenderSettings(doc.RenderSettings)
            settings.UseViewportSize = False
            settings.ImageSize = System.Drawing.Size(*self.size)
            settings.RenderSource = Rhino.Render.RenderSettings.RenderingSources.ActiveViewport
            doc.RenderSettings = settings
        if self.channels:
            current = doc.RenderSettings.RenderChannels
            self.saved_channels = (current.Mode, [guid for guid in current.CustomList])
            ids = [Rhino.Render.RenderWindow.ChannelId(channel) for channel in (SC.RGBA, *self.channels)]
            stored = set(str(guid) for guid in self.saved_channels[1])
            if all(str(guid) in stored for guid in ids):
                self._channels(Rhino.Render.RenderChannels.Modes.Custom)  # nur der Modus: markiert nicht
            else:
                self.list_changed = True
                self._channels(Rhino.Render.RenderChannels.Modes.Custom, ids)
            if str(doc.RenderSettings.RenderChannels.Mode) != "Custom":
                # Greift die Kanalwahl nicht, bleiben die Pässe geplant (render_window findet kein Fenster).
                self.problems.append("Kanalwahl nicht übernommen")
        return self

    def __exit__(self, *exc):
        doc = self.doc
        if self.saved_channels is not None:
            try:
                mode, ids = self.saved_channels
                self._channels(mode, ids if self.list_changed else None)
            except Exception as error:  # noqa: BLE001
                self.problems.append(f"Kanalwahl ({type(error).__name__})")
        if self.saved is not None:
            try:
                doc.RenderSettings = self.saved
            except Exception as error:  # noqa: BLE001
                self.problems.append(f"Rendereinstellungen ({type(error).__name__})")
            try:
                doc.Modified = self.modified
            except Exception:  # noqa: BLE001
                pass
        doc.UndoRecordingEnabled = self.undo
        # Nur eine Zuweisung kann die Marke stehen lassen; im selben Lauf liest Rhino sie auch sonst kurz als gesetzt.
        self.marked = (self.saved is not None or self.list_changed) and not self.modified
        return False


def _read_channel(window, spec, width: int, height: int):
    channel = window.OpenChannel(spec.channel)
    if channel is None:
        return None
    try:
        if channel.Width != width or channel.Height != height:
            return None
        pixel = int(channel.PixelSize())
        count = width * height * (pixel // 4)
        buffer = System.Array.CreateInstance(System.Single, count)
        order = CO.Irrelevant if spec.components == 1 else CO.RGB
        result = channel.GetValues(System.Drawing.Rectangle(0, 0, width, height), width * pixel, order, buffer)
        values = result[-1] if isinstance(result, tuple) else (result if result is not None else buffer)
        return floats_of(values, count)
    finally:
        channel.Dispose()


def floats_of(values, count: int) -> list:
    """Ein .NET-``Single[]`` als Python-Liste — über einen Byte-Block statt Element für Element.

    ``Buffer.BlockCopy`` kopiert die Floats in ein ``Byte[]``, das pythonnet als Puffer herausgibt; Element für
    Element über pythonnet wäre bei Millionen Werten um Größenordnungen langsamer. Ohne Puffer gilt der
    langsame Weg.
    """
    try:
        block = System.Array.CreateInstance(System.Byte, count * 4)
        System.Buffer.BlockCopy(values, 0, block, 0, count * 4)
        return unpack_floats(bytes(memoryview(block)))
    except (TypeError, ValueError, BufferError):
        return [float(v) for v in values]


def render(doc, root: str, size, roles, allowed_media_types, progress, bit_depth: int, view_key: str = ""):
    """Rendering als PNG und die gewählten Pässe als PNG mit ``bit_depth`` — ``(beauty, pass_files, planned, name)``.

    Ein gewählter Pass wird ``planned`` mit Begründung, wenn Rhino Render nicht der aktuelle Renderer ist, die
    Kanäle nicht erreichbar sind oder der Server ``image/png`` nicht annimmt — kein stiller Umweg.
    """
    bit_depth = mf.data_pass_bit_depth(bit_depth)
    width, height = size
    images = os.path.join(root, "images")
    os.makedirs(images, exist_ok=True)
    view = doc.Views.ActiveView
    if view is None:
        raise CaptureError("Keine aktive Ansicht — bitte eine Ansicht anklicken.")
    if isinstance(view, Rhino.Display.RhinoPageView) and not view_key:
        raise CaptureError("Gerendert wird eine Modellansicht; bitte statt des Layouts eine Modellansicht aktivieren.")

    planned: list = []
    wanted: list = []
    png_allowed = allowed_media_types is None or PNG in allowed_media_types
    for role in roles:
        spec = PASS_BY_ROLE[role]
        path = f"images/{role}.png"
        state, hint = pass_status(spec)
        if state != "available":
            planned.append(mf.PlannedRole(role, path, PNG, f"Pass nicht verfügbar: {hint}".strip()))
        elif not png_allowed:
            planned.append(mf.PlannedRole(role, path, PNG, "Der Server nimmt derzeit kein PNG an."))
        else:
            wanted.append(spec)
    channels = [spec.channel for spec in wanted]
    if any(spec.role == "albedo" for spec in wanted) and SC.NormalXYZ not in channels:
        channels.append(SC.NormalXYZ)  # die Samplezahl der Albedo steckt im Betrag der Normalensumme

    beauty_path = os.path.join(images, "beauty.png")
    renderer = "Rhino Render" if renderer_is_rhino_render() else "aktueller Renderer"
    progress("Bild aufnehmen", 5)
    with source_view(doc, view_key):
        name = view_name(doc, view_key)
        with TransientRender(doc, size, channels) as transient, _SessionWatch() as watch:
            started = time.time()
            ok = Rhino.RhinoApp.RunScript("-_Render", False)
            seconds = time.time() - started
            if not ok:
                raise CaptureError("Rhino hat das Rendern nicht abgeschlossen (abgebrochen?).")
            progress("Bild aufnehmen", 60)
            saved = Rhino.RhinoApp.RunScript(f'-_SaveRenderWindowAs "{beauty_path}" _Enter', False)
            if not saved or not os.path.isfile(beauty_path):
                raise CaptureError("Rhino hat das gerenderte Bild nicht gespeichert.")
            window = render_window(watch.found, size, channels)
    problems = list(transient.problems)
    if transient.marked:
        problems.append("Rhino markiert das Dokument als geändert (Kanalliste, Größe oder Quelle wichen ab), "
                        "Einstellungen wieder gleich")
        log("Rendern: Dokument als geändert markiert, Rendereinstellungen zurückgestellt")

    files: list = []

    def missing(spec, reason: str) -> None:
        target = os.path.join(images, f"{spec.role}.png")
        if os.path.exists(target):
            os.remove(target)
        planned.append(mf.PlannedRole(spec.role, f"images/{spec.role}.png", PNG, reason))

    def describe(spec, path: str, note: str, **extra) -> None:
        image = dict(mf.describe_png(path), colorSpace=spec.color_space)
        files.append(mf.capture_file(path, root, spec.role, PNG, image, note, **extra))

    raw: dict = {}
    if wanted:
        if window is None:
            for spec in wanted:
                missing(spec, "Die Renderkanäle waren nach dem Rendern nicht erreichbar.")
            wanted = []
        else:
            progress("Bild aufnehmen", 70)
            for channel_spec in {spec.channel: spec for spec in wanted}.values():
                raw[channel_spec.role] = _read_channel(window, channel_spec, width, height)
            if any(spec.role == "albedo" for spec in wanted) and "normal" not in raw:
                raw["normal"] = _read_channel(window, PASS_BY_ROLE["normal"], width, height)

    meters = meters_per_unit(doc)
    written = f"PNG {bit_depth} Bit"
    for spec in wanted:
        path = os.path.join(images, f"{spec.role}.png")
        values = raw.get(spec.role)
        if values is None:
            missing(spec, f"Rhino Render hat den Kanal {spec.label} nicht geliefert.")
            continue
        if spec.role == "depth":
            found = passes.depth_range(values, meters)
            if found is None:
                missing(spec, "Im Bild ist keine Geometrie; die Tiefe bleibt leer.")
                continue
            near, far = found
            pngwrite.write_png(path, width, height, pngwrite.GRAY, bit_depth,
                               passes.depth_rows(values, width, height, meters, near, far))
            depth = {"encoding": "normalized-linear", "near": near, "far": far}
            describe(spec, path, (f"Tiefe entlang der Blickachse (planar) aus dem Kanal Distance von Rhino Render, "
                                  f"{written}, normalized-linear zwischen near {near:g} m und far {far:g} m (aus den "
                                  f"Treffern im Bild), kein Treffer = 1 (QR-03, am Host gemessen)."), depth=depth)
        elif spec.role == "normal":
            pngwrite.write_png(path, width, height, pngwrite.RGB, bit_depth, passes.normal_rows(values, width, height))
            describe(spec, path, (f"Kanal Normal von Rhino Render (Weltnormalen, über die Samples summiert), "
                                  f"normiert und in den Exportraum des Manifests gedreht (x, z, −y); {written}, "
                                  f"(n + 1) / 2 je Komponente, kein Treffer 0,5 (QR-04, am Host gemessen)."),
                     normal={"space": "world"})
        elif spec.role == "albedo":
            normals = raw.get("normal")
            if normals is None:
                missing(spec, "Für die Albedo fehlt der Normalenkanal (Samplezahl).")
                continue
            pngwrite.write_png(path, width, height, pngwrite.RGB, bit_depth,
                               passes.albedo_rows(values, normals, width, height))
            describe(spec, path, (f"Kanal Albedo von Rhino Render, linear, Summe der Samples geteilt durch die "
                                  f"Samplezahl (Betrag der Normalensumme; an Silhouetten bis 11 % zu hell), {written} "
                                  f"(QR-02, am Host gemessen)."))
        else:
            index = passes.id_index(values)
            problem = passes.id_problem(index, bit_depth)
            if problem:
                missing(spec, problem)
                continue
            pngwrite.write_png(path, width, height, pngwrite.GRAY, bit_depth,
                               passes.id_rows(values, width, height, index, bit_depth))
            describe(spec, path, (f"Kanal {spec.label} von Rhino Render: {len(index)} Werte je Aufnahme auf Indizes "
                                  f"1 … {len(index)} abgebildet (aufsteigend nach Rohwert), 0 = kein Treffer, ohne "
                                  f"Mischwerte an Kanten; {written} (QR-05, am Host gemessen)."))

    tail = f"; Zurücksetzen meldete: {', '.join(problems)}" if problems else ""
    beauty = mf.capture_file(
        beauty_path, root, "beauty", PNG, mf.describe_png(beauty_path),
        (f"-_Render mit {renderer}, {width} × {height}, {seconds:.1f} s, Rendereinstellungen nur für diesen Lauf "
         f"gesetzt und zurückgestellt{tail}; gespeichert über -_SaveRenderWindowAs: PNG 8 Bit RGBA, Gamma 2,2 "
         f"eingebacken, gemeldet als srgb (QR-02, am Host gemessen)."))
    order = [spec.role for spec in PASSES]
    files.sort(key=lambda f: order.index(f.role))
    planned.sort(key=lambda p: order.index(p.role))
    return beauty, files, planned, name


# --------------------------------------------------------------------------
# Kamera
# --------------------------------------------------------------------------


def camera_view(doc, size, view_key: str = ""):
    """Die Kamera der Ansicht für ein Bild ``size`` — ``None`` für ein Layout (keine Kamera)."""
    with source_view(doc, view_key) as view:
        if isinstance(view, Rhino.Display.RhinoPageView):
            return None
        return view_of(Rhino.DocObjects.ViewportInfo(view.ActiveViewport), size)


def view_of(info, size) -> View:
    """Eine ``ViewportInfo`` (eine Kopie: sie wird verändert) im Seitenverhältnis ``size`` als ``View``."""
    info.FrustumAspect = float(size[0]) / float(size[1])
    location, direction, up = info.CameraLocation, info.CameraDirection, info.CameraUp
    return View(
        perspective=bool(info.IsPerspectiveProjection),
        location=(location.X, location.Y, location.Z),
        direction=(direction.X, direction.Y, direction.Z),
        up=(up.X, up.Y, up.Z),
        left=info.FrustumLeft, right=info.FrustumRight, bottom=info.FrustumBottom, top=info.FrustumTop,
        near=info.FrustumNear, far=info.FrustumFar,
    )


def unpack_floats(data: bytes) -> list:
    return list(struct.unpack(f"<{len(data) // 4}f", data))
