"""Bilder aus Cinema 4D 2026 — Viewport, Beauty, Pässe — und die Laufzeitprobe.

Die einzige Schicht, die ``c4d`` für Aufnahme und Dokument anfasst. Jede
benutzte Funktion und Konstante ist gegen die Dokumentation von Cinema 4D
**2026** belegt (Tabelle „API-Belege 2026" in ``integrations/cinema-4d/README.md``);
am Host gemessen ist nichts — die Messung ist die Abnahme am Mac.

* **Viewport**: ``c4d.documents.RenderDocument`` auf einer **Kopie** des
  Containers der aktiven Rendervoreinstellung, Engine
  ``RDATA_RENDERENGINE_PREVIEWHARDWARE``, mit ``RENDERFLAGS_EXTERNAL`` — so
  zeigt es Maxons Beispiel ``render_document_hardware_2026_2.py``, und die
  Referenz 2026.2 sagt: ohne dieses Flag ignoriert Cinema 4D die Engine-Wahl
  und rendert mit dem Standardrenderer. Das Dokument wird nicht angefasst:
  keine eingefügte Voreinstellung, kein Videopost, nichts zurückzusetzen.
* **Beauty**: ``RenderDocument`` mit dem im Dokument aktiven Renderer auf
  einer Kopie seines Containers (Größe, nur das aktuelle Bild, kein
  Speichern). Das Dokument wird nicht verändert.
* **Pässe**: nur Standard/Physical Multi-Pass. Normalen und Albedo nur, wenn der Nutzer
  den Kanal eingeschaltet hat (QC-13); Tiefe und Objekt-ID schaltet das Plugin nur für
  den einen Render ein (``transient``) und baut alles danach zurück. Die Ebene
  eines Kanals findet ``MPBTYPE_USERID`` („In the renderer this is
  VPBUFFER_xxx"). Jeder Pass ist ein PNG ``images/<role>.png`` mit 8 oder 16
  Bit je Kanal nach Wahl des Nutzers (Capture-Manifest 1.3.0): die rohen Floats
  der Ebene der MultipassBitmap (``GetPixelCnt``, ``COLORMODE_RGBf``) gehen
  durch den eigenen PNG-Schreiber (``pngwrite.py``). **Nicht** über
  ``BaseBitmap.Save``: das wendet auf Float-Ebenen eine kanalabhängige
  Anzeigetransformation an, auch mit ``RENDERFLAGS_OCIO_RAW_RENDERING``
  (``docs/measurements/2026-10-01-standard-paesse.md``) — für Datenpässe
  unbrauchbar, für die Beauty gewollt. Maße, Bittiefe und Kanäle liest das
  Manifest aus der geschriebenen Datei (``describe_png``). **Normalen** (QC-04, am Host gemessen):
  die Float-Ebene ist ``(n + 1) / 2`` im Weltraum in Cinema-4D-Achsen, ein Pixel (0, 0, 0) ist „kein
  Treffer“ und wird in der Datei 0,5 (Vertrag 1.3.0); Manifest ``normal.space: world`` — der Exportraum
  des Manifests wie bei Kamera und Modell, also ist die Z-Komponente gespiegelt (``1 − b``). **Tiefe**
  (QC-03, gemessen 05.10.2026) aus dem Positions-Pass, transient eingeschaltet, planar ``normalized-linear``;
  **Objekt-ID** (QC-05) über den Dateiweg (Multi-Pass speichern aus einer markierten Kopie der Voreinstellung);
  **Material-ID** (QC-06) liefern Standard und Physical nicht. Albedo (``VPBUFFER_REFLECTANCE_ALBEDO`` — ein
  ``VPBUFFER_ALBEDO`` gibt es in 2026 nicht) geht als lineares PNG aus den Floats.
* **Corona** (RTX-C4D-004): die Engine ist der Videopost 1030480 (``CORONA``;
  das c4d-Modul hat kein Symbol dafür — die ID stammt aus der Corona-Installation
  und ist am Host gemessen, ``docs/measurements/*-corona.md``). **Beauty** geht
  über denselben Weg wie jede andere Engine: ``RenderDocument`` auf der Kopie
  der Rendervoreinstellung. **Datenpässe** gibt es mit Corona (noch) nicht: die
  Corona-Pässe liegen im Scene-Hook 1037467, kommen als Ebenen mit ``USERID``
  111 in die MultipassBitmap (nur über den Namen unterscheidbar), sind als
  sRGB-kodierte Floats gespeichert, ihre Tiefenspanne ist in Python nicht
  lesbar und ihre IDs sind Farbcodes — jede dieser Hürden ist ein eigener
  Bildweg. Die Probe meldet sie deshalb als ``requires-user-action`` mit
  Anleitung; sie ändert nichts am Dokument und ohne Corona fällt nur diese
  Aussage weg.
* **Farbe** (QC-12): Viewport und Beauty als PNG 8 Bit, gemeldet als
  ``srgb``. Ab 2026.2 fordert das Plugin das Einbacken der
  OCIO-Ansichtstransformation ausdrücklich an
  (``RENDERFLAGS_OCIO_BAKE_RENDERING``, „as if Cinema 4D would when saving the
  rendering to disk"); davor ist es eine Annahme. ``note`` sagt, was galt.
* **Synchron** (QC-07): gerendert wird im Hauptfaden, mit Fortschritt in der
  Statusleiste. Die Render Queue verlangt gespeicherte Szenen.

Konstanten werden mit ``getattr`` gelesen: fehlt eine in einer Fassung, fällt
nur die betroffene Fähigkeit weg — das Plugin lädt trotzdem. Zahlen, die die
Dokumentation nicht nennt, werden nicht erfunden.
"""

from __future__ import annotations

import math
import os
import re
import shutil
import struct
import tempfile
import uuid
from dataclasses import dataclass

import c4d

from . import host, pngwrite, tiffread, transient
from .rendertaxi_client import manifest as mf

PNG = mf.PNG_MEDIA_TYPE

# Renderer-IDs (RDATA_RENDERENGINE). STANDARD und PREVIEWHARDWARE nennt die
# Python-Referenz 2026, PHYSICAL und REDSHIFT drendersettings.h (C++ 2026) — ohne Zahl.
STANDARD = getattr(c4d, "RDATA_RENDERENGINE_STANDARD", None)
PHYSICAL = getattr(c4d, "RDATA_RENDERENGINE_PHYSICAL", None)
VIEWPORT_RENDERER = getattr(c4d, "RDATA_RENDERENGINE_PREVIEWHARDWARE", None)
REDSHIFT = getattr(c4d, "RDATA_RENDERENGINE_REDSHIFT", None)
# Corona (Chaos): Videopost-ID der Engine, kein Symbol im c4d-Modul — am Host gemessen (RTX-C4D-004).
CORONA = 1030480
RENDERER_LABELS = {engine: label for engine, label in ((STANDARD, "Standard"), (PHYSICAL, "Physical"),
                                                       (VIEWPORT_RENDERER, "Viewport Renderer"),
                                                       (REDSHIFT, "Redshift"), (CORONA, "Corona"))
                   if engine is not None}
MULTIPASS_RENDERERS = tuple(engine for engine in (STANDARD, PHYSICAL) if engine is not None)

# Renderflags (Referenz 2026.2): EXTERNAL lässt die gewählte Engine gelten; die
# OCIO-Flags gibt es erst ab 2026.2 — fehlen sie, bleibt es bei EXTERNAL.
EXTERNAL = getattr(c4d, "RENDERFLAGS_EXTERNAL", 0)
OCIO_BAKE = getattr(c4d, "RENDERFLAGS_OCIO_BAKE_RENDERING", None)
OCIO_RAW = getattr(c4d, "RENDERFLAGS_OCIO_RAW_RENDERING", None)

# Viewport-Renderer-Videopost und seine Anzeigefilter (am Host belegt, c4dpy/GUI 2026.3.1,
# ``docs/measurements/2026-10-01-overlays.md``). Der Videopost gehört zum RenderData-Objekt, nicht zu dessen
# Container: die Filter wirken nur über die Voreinstellung des Dokuments selbst — transient, nur für die Dauer des
# Renders (``transient.TransientRenderSettings``; ein Dokumentklon stürzt beim nächsten Render ab).
# DISPLAYFILTER_SPLINE (1020, ``docs/measurements/2026-10-05-nur-geometrie.md``): Nutzerentscheidung 05.10.2026 —
# Splines werden ausgeblendet, nicht deaktiviert; die Spline-Objekte und was aus ihnen erzeugt wird, bleiben unberührt.
# Wirkung am Host noch nicht gemessen (Hostprobe: ``tools/measure_viewport_capture.py``).
HARDWARE_VIDEOPOST = 300001061
OVERLAY_FILTERS = ("DISPLAYFILTER_GRID", "DISPLAYFILTER_BASEGRID", "DISPLAYFILTER_WORLDAXIS", "DISPLAYFILTER_HORIZON",
                   "DISPLAYFILTER_HUD", "DISPLAYFILTER_GUIDELINES", "DISPLAYFILTER_OBJECTHANDLES",
                   "DISPLAYFILTER_CAMERA", "DISPLAYFILTER_LIGHT", "DISPLAYFILTER_NULL", "DISPLAYFILTER_OTHER",
                   "DISPLAYFILTER_SPLINE")

# Tiefe über den Positions-Pass (RTX-C4D-005, am Host gemessen: ``docs/measurements/2026-10-04-position-pass.md``,
# ``2026-10-05-paesse-dateiweg.md``): Videopost 1027117 und Kanal „Post-Effekte" liefern die Float-Ebene 1027751 mit
# der Weltposition je Pixel, ohne Treffer (0, 0, 0), auch mit Antialiasing ohne Mischwerte an Silhouetten — Standard
# und Physical, im selben Render wie Normalen und Albedo. Beides kommt nur transient in die Voreinstellung.
POSITION_VIDEOPOST = 1027117
POSITION_LAYER = 1027751
POST_EFFECTS = getattr(c4d, "VPBUFFER_ALLPOSTEFFECTS", None)
# Objekt-ID über den Dateiweg (gemessen 05.10.2026): ``RenderDocument`` legt Objektpuffer nicht als Ebene in die
# MultipassBitmap, schreibt sie aber mit „Multi-Pass speichern" als TIFF 32 Bit ``<Name>_object_<ID>.tif``; mit
# Antialiasing „Keines" exakt 0 oder 1. Grundlage sind die Compositing-Tags des Nutzers (Kanäle 1–12).
OBJECT_BUFFER = getattr(c4d, "VPBUFFER_OBJECTBUFFER", None)
COMPOSITING_TAG = getattr(c4d, "Tcompositing", None)
_CHANNELS = tuple((getattr(c4d, f"COMPOSITINGTAG_ENABLECHN{i}", None), getattr(c4d, f"COMPOSITINGTAG_IDCHN{i}", None))
                  for i in range(12))
COMPOSITING_CHANNELS = tuple(pair for pair in _CHANNELS if None not in pair)
PASS_FILE_PREFIX = "pass"

# Dokumentkennung: Untercontainer unter der Plugin-ID im Container des Dokuments (QC-10).
DOCUMENT_KEY_ID = 1
DOCUMENT_KEY_PREFIX = f"{host.KEY}:"
_DOCUMENT_KEY = re.compile(r"^cinema-4d:[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


class CaptureError(RuntimeError):
    """Ein Grund, warum nicht aufgenommen wurde — lesbar, ohne Pfad."""


# Woher ein Pass kommt.
LAYER = "layer"  # Ebene eines Multi-Pass-Kanals, den der Nutzer eingeschaltet hat
POSITION = "position"  # Positions-Pass, transient (Tiefe)
FILES = "files"  # Dateiweg: Multi-Pass speichern aus einer transienten Kopie der Voreinstellung (Objekt-ID)
NOWHERE = "nowhere"  # Cinema 4D liefert den Pass mit Standard/Physical nicht


@dataclass(frozen=True)
class PassSpec:
    role: str
    capability: str
    label: str
    buffer: int | None  # VPBUFFER_* des Multi-Pass-Kanals (Tiefe: die Positions-Ebene); None: im c4d-Modul nicht vorhanden
    channel: str  # wie der Kanal im Multi-Pass-Menü heißt
    color_space: str
    channels: int = pngwrite.RGB  # Kanäle der PNG-Datei: RGB (Albedo, Normalen) oder Graustufen (Tiefe, IDs)
    miss: float | None = None  # ein Pixel exakt (0, 0, 0) ist „kein Treffer“ und wird in der Datei dieser Wert
    normal_space: str | None = None  # Bezugsraum eines Normalenbildes (Manifest ``normal.space``)
    mirror_z: bool = False  # Z-Komponente spiegeln: Cinema 4D (linkshändig) → Exportraum des Manifests (x, y, −z)
    blocked_by: str | None = None
    blocked_note: str | None = None
    source: str = LAYER


PASSES: tuple[PassSpec, ...] = (
    # QC-03 (05.10.2026): planare Tiefe aus dem Positions-Pass, normalized-linear zwischen near und far.
    PassSpec("depth", "depthPass", "Tiefe (Depth)", POSITION_LAYER if POST_EFFECTS is not None else None,
             "Positions-Pass", "non-color", channels=pngwrite.GRAY, source=POSITION),
    # QC-04 am Host gemessen (01.10.2026, docs/measurements/2026-10-01-standard-paesse.md): die Float-Ebene ist exakt
    # (n + 1) / 2, linear, im Weltraum in Cinema-4D-Achsen; ohne Treffer steht (0, 0, 0) — der Vertrag verlangt dort 0,5.
    # ``normal.space: world`` ist der Raum des Manifests, in dem auch Kamera und Modell geliefert werden (Exportraum,
    # glTF-Konvention): die Z-Komponente wird gespiegelt, im Float 1 − b (Entscheidung RTX-C4D-005).
    PassSpec("normal", "normalPass", "Normalen", getattr(c4d, "VPBUFFER_MAT_NORMAL", None), "Material Normals",
             "non-color", miss=0.5, normal_space="world", mirror_z=True),
    # 2026 kennt kein VPBUFFER_ALBEDO; der Kanal „Albedo" ist VPBUFFER_REFLECTANCE_ALBEDO
    # („Reflectance Channel Diffuse Albedo", C++ 2026, group VPBUFFER).
    PassSpec("albedo", "albedoPass", "Albedo", getattr(c4d, "VPBUFFER_REFLECTANCE_ALBEDO", None), "Albedo",
             "linear"),
    # QC-05 (05.10.2026): Objektpuffer der Compositing-Tags über den Dateiweg, als Index ohne Antialiasing.
    PassSpec("object-id", "objectIdPass", "Objekt-ID", OBJECT_BUFFER if COMPOSITING_CHANNELS else None, "Objektpuffer",
             "non-color", channels=pngwrite.GRAY, source=FILES),
    # QC-06: Standard und Physical haben keinen Kanal für eine Material-ID (gemessen 05.10.2026); offen als
    # Produktentscheidung (transientes Compositing-Tag je Material oder Redshift).
    PassSpec("material-id", "materialIdPass", "Material-ID", 0, "Material-ID", "non-color", channels=pngwrite.GRAY,
             blocked_by="QC-06", blocked_note="Die Material-ID liefert Cinema 4D mit Standard oder Physical nicht.",
             source=NOWHERE),
)
PASS_BY_ROLE = {spec.role: spec for spec in PASSES}


# --------------------------------------------------------------------------
# Dokument
# --------------------------------------------------------------------------


def active_document():
    doc = c4d.documents.GetActiveDocument()
    if doc is None:
        raise CaptureError("Kein Dokument geöffnet.")
    return doc


def renderer_label(engine) -> str:
    return RENDERER_LABELS.get(engine, f"Renderer {engine}")


def document_size(doc) -> tuple[int, int]:
    """Die Ausgabegröße der aktiven Rendervoreinstellung (Breite × Höhe in Pixeln)."""
    rd = doc.GetActiveRenderData()
    return (max(1, int(round(float(rd[c4d.RDATA_XRES])))), max(1, int(round(float(rd[c4d.RDATA_YRES])))))


def document_name(doc) -> str | None:
    """Der Name des Dokuments ohne Endung — nur für ``project.displayName``, nie im Protokoll."""
    name = doc.GetDocumentName() or ""
    stem = os.path.splitext(name)[0].strip()
    return stem or None


def document_file_name(doc) -> str | None:
    """Der Name der gespeicherten Datei mit Endung — ``source.fileName`` (1.5.0); ``None``, solange nie gespeichert.

    Nur der Name, nie der Ordner (``GetDocumentPath`` sagt allein, **ob** gespeichert ist). Bereinigt
    wird im Client (``manifest.source_file_name``); der Name steht nie im Protokoll.
    """
    getter = getattr(doc, "GetDocumentPath", None)
    if getter is None or not (getter() or ""):
        return None
    return doc.GetDocumentName() or None


def render_view(doc):
    """Die Ansicht, aus der Cinema 4D rendert (Renderansicht); ohne sie die aktive."""
    getter = getattr(doc, "GetRenderBaseDraw", None)
    view = getter() if getter is not None else None
    return view if view is not None else doc.GetActiveBaseDraw()


def camera_name(doc) -> str | None:
    """Der Name des Kameraobjekts, aus dem gerendert wird — ``None`` für die Editor-Kamera.

    Er ist der Vorschlag für den Namen eines neuen Blickpunkts (RTX-P-013); ohne Kameraobjekt gibt es
    keinen Vorschlag.
    """
    bd = render_view(doc)
    camera = bd.GetSceneCamera(doc) if bd is not None else None
    editor = bd.GetEditorCamera() if bd is not None else None
    if camera is None or (editor is not None and camera == editor):
        return None
    return (camera.GetName() or "").strip() or None


def view_name(doc) -> str:
    """Die Kamera, aus der gerendert wird: die Szenenkamera der Renderansicht oder die Editor-Kamera."""
    bd = render_view(doc)
    camera = bd.GetSceneCamera(doc) if bd is not None else None
    editor = bd.GetEditorCamera() if bd is not None else None
    if camera is None or (editor is not None and camera == editor):
        return "Editor-Kamera"
    return camera.GetName() or "Kamera"


def _open_documents():
    doc = c4d.documents.GetFirstDocument()
    while doc is not None:
        yield doc
        doc = doc.GetNext()


def _read_key(doc) -> str | None:
    sub = doc.GetDataInstance().GetContainer(host.PLUGIN_ID)
    value = sub.GetString(DOCUMENT_KEY_ID) if sub is not None else ""
    return value if isinstance(value, str) and _DOCUMENT_KEY.match(value) else None


def document_key(doc, create: bool) -> str | None:
    """``sourceProjectKey`` als ``cinema-4d:<uuid4>`` im Container des Dokuments.

    Ein Untercontainer unter der Plugin-ID, gespeichert mit dem Dokument
    (``capabilities.md``, Abschnitt 7). Vorbehalt aus QC-10: „Speichern unter"
    kopiert die Kennung. Sie ist deshalb ein Hinweis, keine Autorität (ADR 0009,
    Entscheidung 8): das Plugin **schlägt** den zuletzt gewählten Blickpunkt nur
    vor. Tragen zwei **offene** Dokumente dieselbe Kennung, bekommt das
    aufnehmende eine neue; eine Kopie auf der Platte ist nicht zu erkennen.
    Im Dokument steht nur diese Kennung — nie ein Token.
    """
    value = _read_key(doc)
    if not create:
        return value
    shared = value is not None and any(other != doc and _read_key(other) == value for other in _open_documents())
    if value is not None and not shared:
        return value
    value = f"{DOCUMENT_KEY_PREFIX}{uuid.uuid4()}"
    data = doc.GetDataInstance()
    sub = data.GetContainer(host.PLUGIN_ID) or c4d.BaseContainer()
    sub.SetString(DOCUMENT_KEY_ID, value)
    data.SetContainer(host.PLUGIN_ID, sub)
    doc.SetChanged()  # die Kennung soll mit dem Dokument gespeichert werden
    return value


# --------------------------------------------------------------------------
# Laufzeitprobe
# --------------------------------------------------------------------------


def _multipass_channels(rd) -> set:
    """Die eingeschalteten Multi-Pass-Kanäle der Rendervoreinstellung."""
    found = set()
    passes = rd.GetFirstMultipass()
    disabled = getattr(c4d, "BIT_VPDISABLED", None)
    while passes is not None:
        if disabled is None or not passes.GetBit(disabled):
            found.add(passes[c4d.MULTIPASSOBJECT_TYPE])
        passes = passes.GetNext()
    return found


def _objects(obj):
    while obj is not None:
        yield obj
        yield from _objects(obj.GetDown())
        obj = obj.GetNext()


def object_buffer_ids(doc) -> list[int]:
    """Die Objektpuffer-IDs, die Compositing-Tags im Dokument vergeben (eingeschaltete Kanäle), aufsteigend.

    Nur gelesen: das Plugin legt keine Tags an und ändert keine (Entscheidung RTX-C4D-005).
    """
    if COMPOSITING_TAG is None:
        return []
    found = set()
    for obj in _objects(doc.GetFirstObject()):
        for tag in obj.GetTags():
            if tag.GetType() != COMPOSITING_TAG:
                continue
            for enable, number in COMPOSITING_CHANNELS:
                if tag[enable]:
                    value = tag[number]
                    if isinstance(value, int) and value > 0:
                        found.add(value)
    return sorted(found)


def position_pass_available() -> bool:
    return POST_EFFECTS is not None and c4d.plugins.FindPlugin(POSITION_VIDEOPOST, c4d.PLUGINTYPE_VIDEOPOST) is not None


def pass_status(spec: PassSpec, doc) -> tuple[str, str]:
    """Zustand eines Passes in **diesem** Dokument und was der Nutzer einstellen muss."""
    if spec.source == NOWHERE:
        return "unavailable", spec.blocked_note or ""
    rd = doc.GetActiveRenderData()
    engine = rd[c4d.RDATA_RENDERENGINE]
    where = f"Rendervoreinstellungen › Multi-Pass › Kanal „{spec.channel}“"
    if spec.source == LAYER:
        then = f", dann {where} hinzufügen"
    elif spec.source == FILES:
        then = ", dann an Objekten ein Compositing-Tag mit Objektpuffer vergeben"
    else:
        then = ""
    if engine == CORONA:
        return ("requires-user-action",
                f"Mit Corona nicht übertragbar. Für Datenpässe Renderer Standard oder Physical wählen{then}.")
    if not MULTIPASS_RENDERERS or engine not in MULTIPASS_RENDERERS:
        return ("requires-user-action", f"Renderer Standard oder Physical wählen (jetzt {renderer_label(engine)}){then}.")
    if spec.source == POSITION:
        if not position_pass_available():
            return "unavailable", "Der Positions-Pass fehlt in diesem Cinema 4D."
        return "available", ""
    if spec.source == FILES:
        if not object_buffer_ids(doc):
            return ("requires-user-action",
                    "An Objekten ein Compositing-Tag mit Objektpuffer vergeben (Tag › Objektpuffer, ID ab 1).")
        return "available", ""
    if not rd[c4d.RDATA_MULTIPASS_ENABLE]:
        return "requires-user-action", f"Multi-Pass einschalten und {where} hinzufügen."
    if spec.buffer not in _multipass_channels(rd):
        return "requires-user-action", f"{where} hinzufügen."
    return "available", ""


def viewport_renderer_available() -> bool:
    if VIEWPORT_RENDERER is None:
        return False
    return c4d.plugins.FindPlugin(VIEWPORT_RENDERER, c4d.PLUGINTYPE_VIDEOPOST) is not None


def probe(doc) -> dict:
    """``source.host.capabilities`` aus dem laufenden Cinema 4D — nicht aus der Matrixdatei.

    Gemeldet wird nur, was diese Probe prüft: Viewport (Renderansicht und
    Viewport Renderer vorhanden), Beauty (aktive Rendervoreinstellung) und die
    drei Pässe nach Renderer und Multi-Pass-Kanälen. Alles andere fehlt und
    heißt damit ``unknown`` — auch ein Pass, dessen Konstante das c4d-Modul
    nicht kennt. Ein Pass beschreibt den Pass, nicht seine Kodierung: ein
    eingeschalteter Depth-Kanal ist ``available``, das Asset bleibt ``planned``
    (QC-03). Jeder Pass geht als PNG (Capture-Manifest 1.3.0).
    """
    capabilities: dict = {}
    transient.sweep(doc)  # verwaiste Einträge einer abgestürzten Aufnahme (RTX-C4D-006)
    if render_view(doc) is None:
        capabilities["viewportCapture"] = {"state": "requires-user-action", "constraints": {"mediaTypes": [PNG]}}
    elif not viewport_renderer_available():
        capabilities["viewportCapture"] = {"state": "unavailable"}
    else:
        capabilities["viewportCapture"] = {"state": "available", "constraints": {"mediaTypes": [PNG]}}
    rd = doc.GetActiveRenderData()
    if rd is None:
        capabilities["beautyRender"] = {"state": "unavailable"}
        return capabilities
    capabilities["beautyRender"] = {"state": "available", "constraints": {"mediaTypes": [PNG]}}
    for spec in PASSES:
        if spec.buffer is None:
            continue
        state, _hint = pass_status(spec, doc)
        entry = {"state": state}
        if state != "unavailable":
            entry["constraints"] = {"mediaTypes": [PNG]}
        capabilities[spec.capability] = entry
    return capabilities


def pass_rows(doc) -> list[tuple[PassSpec, str, str]]:
    """Die Pässe für den Dialog: (Pass, Zustand, Hinweis) — ohne die, die das c4d-Modul nicht kennt."""
    rd = doc.GetActiveRenderData()
    if rd is None:
        return []
    return [(spec, *pass_status(spec, doc)) for spec in PASSES if spec.buffer is not None]


# --------------------------------------------------------------------------
# Rendern
# --------------------------------------------------------------------------

_RESULTS = {
    "RENDERRESULT_OUTOFMEMORY": "Nicht genug Arbeitsspeicher zum Rendern.",
    "RENDERRESULT_ASSETMISSING": "Dem Dokument fehlen Texturen oder Assets.",
    "RENDERRESULT_SAVINGFAILED": "Cinema 4D konnte das Bild nicht speichern.",
    "RENDERRESULT_USERBREAK": "Rendern abgebrochen.",
    "RENDERRESULT_GICACHEMISSING": "Dem Dokument fehlt der GI-Cache.",
    "RENDERRESULT_NOMACHINE": "Kein Rechner für das Rendern verfügbar.",
    "RENDERRESULT_PROJECTNOTFOUND": "Das Projekt wurde nicht gefunden.",
    "RENDERRESULT_ERRORLOADINGPROJECT": "Das Projekt ließ sich nicht laden.",
    "RENDERRESULT_NOOUTPUTSPECIFIED": "Keine Ausgabe festgelegt.",
}


def _result_text(result) -> str:
    for name, text in _RESULTS.items():
        if getattr(c4d, name, object()) == result:
            return text
    return f"Cinema 4D hat das Rendern nicht abgeschlossen (Ergebnis {result})."


def render_flags(bake: bool) -> int:
    """``RENDERFLAGS_EXTERNAL`` und — wo es sie gibt — das OCIO-Flag: gebacken fürs PNG, roh für Datenpässe."""
    extra = OCIO_BAKE if bake else OCIO_RAW
    return EXTERNAL | (extra or 0)


def _render(doc, data, bitmap, progress, label: str, bake: bool = True) -> None:
    def report(*args):
        try:
            value = float(args[0]) if args else 0.0
        except (TypeError, ValueError):
            value = 0.0
        progress(label, int(max(0.0, min(1.0, value)) * 100))

    progress(label, 0)
    result = c4d.documents.RenderDocument(doc, data, bitmap, render_flags(bake), None, report)
    if result != c4d.RENDERRESULT_OK:
        raise CaptureError(_result_text(result))


def _prepare_data(data, size: tuple[int, int]) -> None:
    """Größe, nur das aktuelle Bild, kein Speichern — nur auf der Kopie des Containers.

    ``RenderDocument`` „will always honor the 'Save' options" und rendert sonst
    eine ganze Animation (Beispiel ``render_document_simple_2026_2.py``).
    """
    data[c4d.RDATA_XRES] = float(size[0])
    data[c4d.RDATA_YRES] = float(size[1])
    data[c4d.RDATA_GLOBALSAVE] = False
    current = getattr(c4d, "RDATA_FRAMESEQUENCE_CURRENTFRAME", None)
    if current is not None:
        data[c4d.RDATA_FRAMESEQUENCE] = current


def _bitmap(size: tuple[int, int]):
    bitmap = c4d.bitmaps.BaseBitmap()
    if bitmap.Init(size[0], size[1], 24) != c4d.IMAGERESULT_OK:
        raise CaptureError("Cinema 4D konnte das Bild nicht anlegen (Größe oder Arbeitsspeicher).")
    return bitmap


def _save_png(bitmap, path: str) -> None:
    if bitmap.Save(path, c4d.FILTER_PNG) != c4d.IMAGERESULT_OK:
        raise CaptureError("Cinema 4D konnte das Bild nicht als PNG schreiben.")


def beauty_engine_note(engine) -> str:
    """Vorbehalt je Engine für die Beauty: bei Corona gilt das Tone-Mapping der Rendervoreinstellung."""
    if engine == CORONA:
        return ("Corona: Tone-Mapping und Farbe laut Corona-Einstellungen der Rendervoreinstellung "
                "(VFB-Nachbearbeitung wird nicht übernommen), nicht am Host gemessen (QC-12)")
    return ""


def color_note() -> str:
    if OCIO_BAKE is not None:
        return ("PNG 8 Bit, gemeldet als srgb: OCIO-Ansichtstransformation eingebacken "
                "(RENDERFLAGS_OCIO_BAKE_RENDERING) — nicht am Host gemessen (QC-12)")
    return ("PNG 8 Bit, gemeldet als srgb mit eingebackener Ansichtstransformation angenommen — "
            "vor 2026.2 ohne Flag dafür, nicht am Host gemessen (QC-12)")


def render_viewport(doc, root: str, size: tuple[int, int] | None, progress) -> mf.CaptureFile:
    """Die Renderansicht über den Viewport Renderer als PNG — **ohne Editor-Overlays** (RTX-C4D-006, #209).

    ``RenderDocument`` auf der Kopie des Containers der aktiven Rendervoreinstellung mit
    ``RDATA_RENDERENGINE_PREVIEWHARDWARE`` und ``RENDERFLAGS_EXTERNAL`` (``render_document_hardware_2026_2.py``). Die
    Anzeigefilter des Videoposts „Viewport Renderer“ (``VP_PREVIEWHARDWARE_DISPLAYFILTER_*``) gehören zum RenderData-
    **Objekt**: sie werden mit ``transient.TransientRenderSettings`` nur für die Dauer des synchronen Renders im Dokument
    auf Aus gesetzt und im ``finally`` exakt zurückgesetzt (kein Dokumentklon: das Verwerfen eines Klons stürzt den
    nächsten Viewport-Render ab, ``docs/measurements/2026-10-04-klon-absturz.md``). Fehlen die Filter oder schlägt das
    Setzen fehl, bleibt es beim bisherigen Weg; ``note`` sagt dann „Overlays möglich“ mit Grund.
    """
    if render_view(doc) is None:
        raise CaptureError("Keine Ansicht — die Viewport-Aufnahme braucht eine.")
    if not viewport_renderer_available():
        raise CaptureError("Der Viewport Renderer ist in diesem Cinema 4D nicht verfügbar.")
    size = size or document_size(doc)
    images = os.path.join(root, "images")
    os.makedirs(images, exist_ok=True)
    path = os.path.join(images, "viewport.png")
    values = {symbol: False for symbol in (getattr(c4d, "VP_PREVIEWHARDWARE_" + name, None) for name in OVERLAY_FILTERS)
              if symbol is not None}
    data = doc.GetActiveRenderData().GetDataInstance().GetClone(c4d.COPYFLAGS_NONE)
    data[c4d.RDATA_RENDERENGINE] = VIEWPORT_RENDERER
    _prepare_data(data, size)
    bitmap = _bitmap(size)
    swept, problem, restore_problems = 0, None, []
    with transient.TransientRenderSettings(doc) as settings:
        swept = settings.swept
        if not values:
            problem = "Anzeigefilter des Viewport Renderers fehlen in diesem Cinema 4D"
        else:
            try:
                settings.videopost(HARDWARE_VIDEOPOST, values)
            except Exception as error:  # noqa: BLE001 — Regel 3: die Aufnahme läuft dann wie bisher
                problem = f"Anzeigefilter nicht setzbar ({type(error).__name__})"
        _render(doc, data, bitmap, progress, "Viewport rendern")
    restore_problems = settings.problems
    _save_png(bitmap, path)
    overlays = ("Overlays aus (Anzeigefilter des Viewport Renderers nur für den Render abgeschaltet und zurückgesetzt)"
                if problem is None else f"Overlays möglich ({problem})")
    extra = f"; {swept} verwaiste rendertaxi-Einträge aus der Rendervoreinstellung entfernt" if swept else ""
    extra += f"; Zurücksetzen meldete: {', '.join(restore_problems)}" if restore_problems else ""
    note = (f"Viewport Renderer (RenderDocument, RDATA_RENDERENGINE_PREVIEWHARDWARE, RENDERFLAGS_EXTERNAL, "
            f"Kopie der Rendervoreinstellung), {view_name(doc)}; {overlays}{extra}; {color_note()}.")
    return mf.capture_file(path, root, "viewport", PNG, mf.describe_png(path), note)


def _find_layer(bitmap, buffer: int):
    """Die Ebene eines Multi-Pass-Kanals in der MultipassBitmap (``MPBTYPE_USERID`` = ``VPBUFFER_*``)."""
    for index in range(bitmap.GetLayerCount()):
        layer = bitmap.GetLayerNum(index)
        if layer is not None and layer.GetParameter(c4d.MPBTYPE_USERID) == buffer:
            return layer
    return None


def _layer_rows(layer, size: tuple[int, int], spec: PassSpec):
    """Die Float-Werte der Ebene Zeile für Zeile (RGB je Pixel; bei Graustufen der erste Kanal).

    ``GetPixelCnt`` mit ``COLORMODE_RGBf`` und 12 Byte je Pixel liefert die rohen Werte der
    ``MultipassBitmap`` ohne Anzeigetransformation (am Host gemessen, 01.10.2026). ``spec.miss``: ein Pixel
    exakt (0, 0, 0) ist „kein Treffer“ (nie ein Einheitsvektor als (n + 1) / 2) und wird in der Datei dieser
    Wert; ``spec.mirror_z``: ``(n_z + 1) / 2`` wird ``1 − (n_z + 1) / 2`` — die Spiegelung nach dem Ersetzen,
    ein Fehltreffer 0,5 bleibt 0,5.
    """
    width, height = size
    buffer = bytearray(12 * width)
    unpack = struct.Struct(f"<{3 * width}f").unpack
    for y in range(height):
        # Nur ein ausdrückliches ``False`` ist ein Fehlschlag: Cinema 4D 2026.3.1 gibt bei Erfolg ``None`` zurück,
        # nicht ``True`` (am Host beobachtet, Mac-Sitzung 01.–04.10.2026). Bei ``False`` bliebe der Puffer der
        # vorigen Zeile (oder Nullen, bei Normalen dann 0,5) — der ganze Pass wird verworfen.
        if layer.GetPixelCnt(0, y, width, buffer, 12, c4d.COLORMODE_RGBf, c4d.PIXELCNT_0) is False:
            raise CaptureError(f"GetPixelCnt meldet für Zeile {y} einen Lesefehler.")
        values = unpack(bytes(buffer))
        if spec.miss is not None or spec.mirror_z:
            red, green, blue = values[0::3], values[1::3], values[2::3]
            if spec.miss is not None and 0.0 in red:
                red, green, blue = zip(*[(spec.miss,) * 3 if (r == 0.0 and g == 0.0 and b == 0.0) else (r, g, b)
                                         for r, g, b in zip(red, green, blue)])
            if spec.mirror_z:
                blue = tuple(1.0 - b for b in blue)
            values = tuple(v for pixel in zip(red, green, blue) for v in pixel)
        yield values if spec.channels == pngwrite.RGB else values[0::3]


def _save_pass(layer, path: str, size: tuple[int, int], spec: PassSpec, bit_depth: int) -> bool:
    """Eine Ebene der MultipassBitmap als PNG mit 8 oder 16 Bit je Kanal — aus den Floats, ohne ``layer.Save``.

    Was geschrieben wird, liest danach ``describe_png`` aus dem IHDR — nicht aus der Wahl. ``False``, wenn die
    Werte nicht zu lesen waren (auch ``GetPixelCnt`` → ``False`` in einer Zeile); die Rolle wird dann ``planned`` mit
    Grund, und eine vorhandene Datei unter ``path`` wird entfernt.
    """
    try:
        pngwrite.write_png(path, size[0], size[1], spec.channels, bit_depth,
                           _layer_rows(layer, size, spec))
    except Exception:  # noqa: BLE001 — ein nicht lesbarer Pass wird planned, die Aufnahme läuft weiter (Regel 3)
        for stale in (path, path + ".part"):  # keine alte oder halbe Datei als Ergebnis
            try:
                os.remove(stale)
            except FileNotFoundError:
                pass
        return False
    return True


class PassMissing(Exception):
    """Ein gewählter Pass geht nicht mit — der Text sagt in Nutzersprache, warum; die Aufnahme läuft weiter."""


def _get(obj, name: str, default=None):
    pid = getattr(c4d, name, None)
    if pid is None:
        return default
    try:
        value = obj[pid]
    except (AttributeError, TypeError, KeyError):
        return default
    return default if value is None else value


def _depth_range(camera, hits: list, meters: float) -> tuple[float, float, str]:
    """``(near, far, Herkunft)`` in Metern: Clipping der Kamera, wo eingeschaltet, sonst die Treffer im Bild.

    Ohne Clipping wäre ``far`` unendlich; dann gilt die kleinste und größte Tiefe der Treffer, auf Millimeter nach
    außen gerundet. ``far`` liegt immer über ``near``.
    """
    near = far = None
    clipped, measured = [], []
    if _get(camera, "CAMERAOBJECT_NEAR_CLIPPING_ENABLE", False):
        near = float(_get(camera, "CAMERAOBJECT_NEAR_CLIPPING", 0.0)) * meters
        clipped.append("near")
    if _get(camera, "CAMERAOBJECT_FAR_CLIPPING_ENABLE", False):
        far = float(_get(camera, "CAMERAOBJECT_FAR_CLIPPING", 0.0)) * meters
        clipped.append("far")
    if near is None:
        near = math.floor(min(hits) * meters * 1000.0) / 1000.0
        measured.append("near")
    if far is None:
        far = math.ceil(max(hits) * meters * 1000.0) / 1000.0
        measured.append("far")
    sources = ([f"{' und '.join(clipped)} aus dem Clipping der Kamera"] if clipped else []) + \
              ([f"{' und '.join(measured)} aus den Treffern im Bild"] if measured else [])
    near = max(round(near, 6), 0.000001)
    far = round(far, 6)
    if far <= near:
        far = round(near + 0.001, 6)
    return near, far, ", ".join(sources)


def _write_depth(doc, layer, path: str, size: tuple[int, int], bit_depth: int) -> tuple[dict, str]:
    """Planare Tiefe aus der Positions-Ebene als Grau-PNG ``normalized-linear`` — ``(depth, Herkunft von near/far)``.

    ``d = (P − Kamera) · Blickrichtung`` in Einheiten des Dokuments, in Meter umgerechnet wie das Modell; kein Treffer
    (0, 0, 0) wird 1 (Vertrag 1.3.0).
    """
    from . import export  # export importiert capture

    view = render_view(doc)
    camera = view.GetSceneCamera(doc) if view is not None else None
    if camera is None:
        raise PassMissing("Für die Tiefe in der Renderansicht eine Kamera aktivieren.")
    try:
        meters = export.meters_per_unit(doc)
    except export.ExportError as error:
        raise PassMissing(str(error)) from None
    matrix = camera.GetMg()
    axis = matrix.v3
    norm = math.sqrt(axis.x * axis.x + axis.y * axis.y + axis.z * axis.z) or 1.0
    ox, oy, oz = matrix.off.x, matrix.off.y, matrix.off.z
    ax, ay, az = axis.x / norm, axis.y / norm, axis.z / norm
    width, height = size
    buffer = bytearray(12 * width)
    unpack = struct.Struct(f"<{3 * width}f").unpack
    depths: list = []
    for y in range(height):
        if layer.GetPixelCnt(0, y, width, buffer, 12, c4d.COLORMODE_RGBf, c4d.PIXELCNT_0) is False:
            raise PassMissing("Cinema 4D hat den Positions-Pass nicht lesbar geliefert.")
        values = unpack(bytes(buffer))
        for x in range(width):
            px, py, pz = values[3 * x], values[3 * x + 1], values[3 * x + 2]
            depths.append(None if (px == 0.0 and py == 0.0 and pz == 0.0)
                          else (px - ox) * ax + (py - oy) * ay + (pz - oz) * az)
    hits = [d for d in depths if d is not None]
    if not hits:
        raise PassMissing("Im Bild ist keine Geometrie; die Tiefe bleibt leer.")
    near, far, origin = _depth_range(camera, hits, meters)
    span = far - near

    def rows():
        for y in range(height):
            yield [1.0 if d is None else (d * meters - near) / span for d in depths[y * width:(y + 1) * width]]

    pngwrite.write_png(path, width, height, pngwrite.GRAY, bit_depth, rows())
    return {"encoding": "normalized-linear", "near": near, "far": far}, origin


def _object_files(doc, rd, ids: list[int], folder: str, size: tuple[int, int], progress) -> None:
    """Der Dateiweg: eine markierte Kopie der Voreinstellung (Antialiasing „Keines", je ID ein Objektpuffer-Kanal,
    Multi-Pass speichern als TIFF 32 Bit nach ``folder``) nur für diesen Render aktiv, danach entfernt."""
    names = ("RDATA_SAVEIMAGE", "RDATA_MULTIPASS_SAVEIMAGE", "RDATA_MULTIPASS_SAVEONEFILE", "RDATA_MULTIPASS_FILENAME",
             "RDATA_MULTIPASS_SAVEFORMAT", "RDATA_MULTIPASS_SAVEDEPTH", "RDATA_MULTIPASS_SAVEDEPTH_32",
             "RDATA_ANTIALIASING", "RDATA_ANTIALIASING_NONE", "FILTER_TIF", "MULTIPASSOBJECT_OBJECTBUFFER")
    if any(getattr(c4d, name, None) is None for name in names):
        raise PassMissing("Dieses Cinema 4D kann die Objektpuffer nicht speichern.")
    copy = rd.GetClone(c4d.COPYFLAGS_NONE)
    transient._mark(copy)
    channel = copy.GetFirstMultipass()
    while channel is not None:  # nur die Objektpuffer: nichts sonst landet als Datei
        following = channel.GetNext()
        channel.Remove()
        channel = following
    for number in ids:
        buffer = c4d.BaseList2D(c4d.Zmultipass)
        buffer[c4d.MULTIPASSOBJECT_TYPE] = OBJECT_BUFFER
        buffer[c4d.MULTIPASSOBJECT_OBJECTBUFFER] = number
        copy.InsertMultipass(buffer)
    _prepare_data(copy.GetDataInstance(), size)
    # Über die Parameter des Objekts, nicht den Container: der Dateiname ist ein ``Filename`` — als Text in den
    # Container geschrieben meldet Cinema 4D 2026.3.1 „CRITICAL: Stop“ und speichert nichts (am Host beobachtet).
    copy[c4d.RDATA_GLOBALSAVE] = True
    copy[c4d.RDATA_SAVEIMAGE] = False
    copy[c4d.RDATA_MULTIPASS_ENABLE] = True
    copy[c4d.RDATA_MULTIPASS_SAVEIMAGE] = True
    copy[c4d.RDATA_MULTIPASS_SAVEONEFILE] = False
    copy[c4d.RDATA_MULTIPASS_FILENAME] = os.path.join(folder, PASS_FILE_PREFIX)
    copy[c4d.RDATA_MULTIPASS_SAVEFORMAT] = c4d.FILTER_TIF
    copy[c4d.RDATA_MULTIPASS_SAVEDEPTH] = c4d.RDATA_MULTIPASS_SAVEDEPTH_32
    copy[c4d.RDATA_ANTIALIASING] = c4d.RDATA_ANTIALIASING_NONE
    bitmap = c4d.bitmaps.MultipassBitmap(size[0], size[1], c4d.COLORMODE_RGBf)
    if bitmap is None:
        raise CaptureError("Cinema 4D konnte keine MultipassBitmap anlegen.")
    doc.InsertRenderData(copy)
    try:
        doc.SetActiveRenderData(copy)
        _render(doc, copy.GetDataInstance(), bitmap, progress, "Objekt-ID rendern", bake=False)
    finally:
        doc.SetActiveRenderData(rd)
        copy.Remove()


def _write_object_ids(doc, rd, path: str, size: tuple[int, int], bit_depth: int, progress) -> list[int]:
    """Die Objekt-ID als Grau-PNG: je Pixel die ID des Objektpuffers, 0 ohne — geprüft, sonst ``PassMissing``."""
    ids = object_buffer_ids(doc)
    if not ids:
        raise PassMissing("An Objekten ein Compositing-Tag mit Objektpuffer vergeben (Tag › Objektpuffer, ID ab 1).")
    top = (1 << bit_depth) - 1
    if ids[-1] > top:
        if bit_depth == 8 and ids[-1] <= 65535:
            raise PassMissing(f"Objektpuffer-ID {ids[-1]} passt nicht in 8 Bit; „16 Bit“ wählen oder IDs bis {top} vergeben.")
        raise PassMissing(f"Objektpuffer-IDs bis {top} vergeben (gefunden: {ids[-1]}).")
    width, height = size
    folder = tempfile.mkdtemp(prefix="rendertaxi-objektpuffer-")
    try:
        _object_files(doc, rd, ids, folder, size, progress)
        index = [0] * (width * height)
        for number in ids:
            name = os.path.join(folder, f"{PASS_FILE_PREFIX}_object_{number}.tif")
            if not os.path.isfile(name):
                raise PassMissing(f"Cinema 4D hat den Objektpuffer {number} nicht geliefert.")
            try:
                w, h, values = tiffread.read_float_gray(name)
            except (OSError, tiffread.TiffError):
                raise PassMissing(f"Der Objektpuffer {number} ist nicht lesbar.") from None
            if (w, h) != (width, height):
                raise PassMissing(f"Der Objektpuffer {number} hat nicht die Bildgröße.")
            for i, value in enumerate(values):
                if value == 0.0:
                    continue
                if abs(value - 1.0) > 1e-6:
                    raise PassMissing("Die Objektpuffer sind nicht eindeutig (Kantenglättung); die Objekt-ID bleibt geplant.")
                if index[i]:
                    raise PassMissing("Ein Bildpunkt liegt in mehreren Objektpuffern; je Objekt nur einen Objektpuffer vergeben.")
                index[i] = number
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    pngwrite.write_png(path, width, height, pngwrite.GRAY, bit_depth,
                       ([value / top for value in index[y * width:(y + 1) * width]] for y in range(height)))
    return ids


def _remove(path: str) -> None:
    for stale in (path, path + ".part"):
        try:
            os.remove(stale)
        except FileNotFoundError:
            pass


def render_beauty(doc, root: str, size: tuple[int, int] | None, roles: list[str],
                  allowed_media_types: list[str] | None, progress, bit_depth: int = mf.DEFAULT_DATA_PASS_BIT_DEPTH):
    """Beauty als PNG und die gewählten Pässe als PNG mit ``bit_depth`` — ``(beauty, pass_files, planned)``.

    Gerendert wird mit dem aktiven Renderer auf einer Kopie des Containers der aktiven Rendervoreinstellung. Ein
    gewählter Pass wird ``planned`` mit Begründung, wenn Cinema 4D ihn nicht liefert (Material-ID), der Kanal nicht
    eingeschaltet ist oder der Server ``image/png`` nicht annimmt — kein stiller Umweg über ein anderes Format.

    * **Normalen, Albedo:** zweiter Render in eine MultipassBitmap (``COLORMODE_RGBf``, roh im Renderraum), die Ebene
      als PNG aus den Floats.
    * **Tiefe:** im selben zweiten Render der Positions-Pass — Videopost und Kanal „Post-Effekte" nur für diesen
      Render in der Voreinstellung (``transient``, exakter Rückbau); planare Tiefe ``normalized-linear``.
    * **Objekt-ID:** dritter Render über den Dateiweg (``_object_files``), Index-PNG; das temporäre Verzeichnis
      wird immer entfernt.

    Eine ungültige Bittiefe wird zum Standard 8 Bit (``mf.data_pass_bit_depth``).
    """
    bit_depth = mf.data_pass_bit_depth(bit_depth)
    rd = doc.GetActiveRenderData()
    if rd is None:
        raise CaptureError("Das Dokument hat keine aktive Rendervoreinstellung.")
    size = size or document_size(doc)
    images = os.path.join(root, "images")
    os.makedirs(images, exist_ok=True)

    planned: list[mf.PlannedRole] = []
    wanted: list[PassSpec] = []
    png_allowed = allowed_media_types is None or PNG in allowed_media_types
    for role in roles:
        spec = PASS_BY_ROLE[role]
        path = f"images/{role}.png"
        state, hint = pass_status(spec, doc) if spec.buffer is not None else ("unknown", "")
        if spec.blocked_by:
            planned.append(mf.PlannedRole(role, path, PNG, spec.blocked_note))
        elif state != "available":
            planned.append(mf.PlannedRole(role, path, PNG, f"Pass nicht verfügbar: {hint}".strip()))
        elif not png_allowed:
            planned.append(mf.PlannedRole(role, path, PNG, "Der Server nimmt derzeit kein PNG an."))
        else:
            wanted.append(spec)

    engine = renderer_label(rd[c4d.RDATA_RENDERENGINE])
    data = rd.GetDataInstance().GetClone(c4d.COPYFLAGS_NONE)
    _prepare_data(data, size)
    beauty_path = os.path.join(images, "beauty.png")
    bitmap = _bitmap(size)
    _render(doc, data, bitmap, progress, "Beauty rendern")
    _save_png(bitmap, beauty_path)

    files: list[mf.CaptureFile] = []

    def missing(spec: PassSpec, reason: str) -> None:
        _remove(os.path.join(images, f"{spec.role}.png"))
        planned.append(mf.PlannedRole(spec.role, f"images/{spec.role}.png", PNG, reason))

    def describe(spec: PassSpec, path: str, note, **extra) -> None:
        """``note(written)`` bekommt „PNG <Bit> Bit <Kanäle>“ aus der geschriebenen Datei."""
        image = dict(mf.describe_png(path), colorSpace=spec.color_space)
        written = f"PNG {image['bitDepth']} Bit {image['channels']}"
        if image["bitDepth"] != bit_depth:
            written += f" (gewählt: {bit_depth} Bit; die Datei entscheidet)"
        files.append(mf.capture_file(path, root, spec.role, PNG, image, note(written), **extra))

    layered_specs = [spec for spec in wanted if spec.source in (LAYER, POSITION)]
    if layered_specs:
        layered = c4d.bitmaps.MultipassBitmap(size[0], size[1], c4d.COLORMODE_RGBf)
        if layered is None:
            raise CaptureError("Cinema 4D konnte keine MultipassBitmap anlegen.")
        depth_spec = next((spec for spec in layered_specs if spec.source == POSITION), None)
        layer_data = data.GetClone(c4d.COPYFLAGS_NONE)
        problems: list[str] = []
        swept = 0
        if depth_spec is None:
            _render(doc, layer_data, layered, progress, "Pässe rendern", bake=False)
        else:
            layer_data[c4d.RDATA_MULTIPASS_ENABLE] = True
            with transient.TransientRenderSettings(doc) as settings:
                swept = settings.swept
                try:
                    settings.videopost(POSITION_VIDEOPOST, {})
                    settings.multipass(POST_EFFECTS)
                except Exception:  # noqa: BLE001 — Regel 3: ohne Positions-Pass bleibt die Tiefe geplant
                    layered_specs.remove(depth_spec)
                    missing(depth_spec, "Der Positions-Pass ließ sich nicht einschalten; die Tiefe bleibt geplant.")
                    depth_spec = None
                _render(doc, layer_data, layered, progress, "Pässe rendern", bake=False)
            problems = settings.problems
        space = ("Float der Ebene im Renderraum ohne Anzeigetransformation (RENDERFLAGS_OCIO_RAW_RENDERING; "
                 "bei OCIO ACEScg, QC-12)" if OCIO_RAW is not None
                 else "Float der Ebene, linear, Primärvalenzen nicht belegt (QC-12)")
        for spec in layered_specs:
            path = os.path.join(images, f"{spec.role}.png")
            layer = _find_layer(layered, spec.buffer)
            if spec.source == POSITION:
                if layer is None:
                    missing(spec, "Cinema 4D hat den Positions-Pass nicht geliefert; die Tiefe bleibt geplant.")
                    continue
                try:
                    depth, origin = _write_depth(doc, layer, path, size, bit_depth)
                except PassMissing as reason:
                    missing(spec, str(reason))
                    continue
                tail = f"; {swept} verwaiste rendertaxi-Einträge aus der Rendervoreinstellung entfernt" if swept else ""
                tail += f"; Zurücksetzen meldete: {', '.join(problems)}" if problems else ""
                describe(spec, path, lambda written, depth=depth, origin=origin, tail=tail: (
                    f"Tiefe entlang der Blickachse (planar) aus dem Positions-Pass von {engine} (Videopost 1027117, nur "
                    f"für den Render eingeschaltet und zurückgesetzt), {written}, normalized-linear zwischen near "
                    f"{depth['near']:g} m und far {depth['far']:g} m ({origin}), kein Treffer = 1, zweiter "
                    f"Renderdurchgang (QC-03, am Host gemessen){tail}."), depth=depth)
                continue
            if layer is None or not _save_pass(layer, path, size, spec, bit_depth):
                missing(spec, f"Cinema 4D hat den Kanal {spec.channel} nicht geliefert.")
                continue
            extra = (" Normalen im Exportraum des Manifests (wie Kamera und Modell), aus den rohen Cinema-4D-Weltnormalen "
                     "gespiegelt (z → −z); (n + 1) / 2 je Komponente, kein Treffer 0,5 (am Host gemessen, QC-04)."
                     if spec.normal_space else "")
            normal = {"space": spec.normal_space} if spec.normal_space else None
            describe(spec, path, lambda written, spec=spec, extra=extra: (
                f"Multi-Pass „{spec.channel}“ aus {engine}, {written}, {space}, eigener PNG-Schreiber aus den Floats "
                f"(GetPixelCnt), zweiter Renderdurchgang.{extra}"), normal=normal)

    for spec in [spec for spec in wanted if spec.source == FILES]:
        path = os.path.join(images, f"{spec.role}.png")
        try:
            ids = _write_object_ids(doc, rd, path, size, bit_depth, progress)
        except PassMissing as reason:
            missing(spec, str(reason))
            continue
        describe(spec, path, lambda written, ids=ids: (
            f"Objekt-ID aus den Objektpuffern der Compositing-Tags (IDs {', '.join(map(str, ids))}), {engine}, "
            f"{written}, Wert = ID, 0 = kein Objektpuffer; Multi-Pass als TIFF 32 Bit in ein temporäres Verzeichnis, "
            f"Antialiasing „Keines“ nur in einer Kopie der Rendervoreinstellung, dritter Renderdurchgang (QC-05, am "
            f"Host gemessen)."))

    extra = beauty_engine_note(rd[c4d.RDATA_RENDERENGINE])
    beauty = mf.capture_file(beauty_path, root, "beauty", PNG, mf.describe_png(beauty_path),
                             f"Beauty (RenderDocument), {engine}, {view_name(doc)}; {color_note()}"
                             f"{'; ' + extra if extra else ''}.")
    order = [spec.role for spec in PASSES]
    files.sort(key=lambda f: order.index(f.role))
    planned.sort(key=lambda p: order.index(p.role))
    return beauty, files, planned
