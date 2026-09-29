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
* **Pässe**: nur Standard/Physical Multi-Pass, nur was der Nutzer
  eingeschaltet hat — das Plugin schaltet keinen Pass ein (QC-13). Die Ebene
  eines Kanals findet ``MPBTYPE_USERID`` („In the renderer this is
  VPBUFFER_xxx"). Tiefe (QC-03) und Normalen (QC-04) bleiben ``planned``;
  Albedo (``VPBUFFER_REFLECTANCE_ALBEDO`` — ein ``VPBUFFER_ALBEDO`` gibt es in
  2026 nicht) wird als OpenEXR 32 Bit (``SAVEBIT_USE32BITCHANNELS``)
  geschrieben, wenn der Server ``image/x-exr`` annimmt.
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

import os
import re
import uuid
from dataclasses import dataclass

import c4d

from . import host
from .rendertaxi_client import exr
from .rendertaxi_client import manifest as mf

PNG = "image/png"
EXR = "image/x-exr"

# Renderer-IDs (RDATA_RENDERENGINE). STANDARD und PREVIEWHARDWARE nennt die
# Python-Referenz 2026, PHYSICAL und REDSHIFT drendersettings.h (C++ 2026) — ohne Zahl.
STANDARD = getattr(c4d, "RDATA_RENDERENGINE_STANDARD", None)
PHYSICAL = getattr(c4d, "RDATA_RENDERENGINE_PHYSICAL", None)
VIEWPORT_RENDERER = getattr(c4d, "RDATA_RENDERENGINE_PREVIEWHARDWARE", None)
REDSHIFT = getattr(c4d, "RDATA_RENDERENGINE_REDSHIFT", None)
RENDERER_LABELS = {engine: label for engine, label in ((STANDARD, "Standard"), (PHYSICAL, "Physical"),
                                                       (VIEWPORT_RENDERER, "Viewport Renderer"),
                                                       (REDSHIFT, "Redshift")) if engine is not None}
MULTIPASS_RENDERERS = tuple(engine for engine in (STANDARD, PHYSICAL) if engine is not None)

# Renderflags (Referenz 2026.2): EXTERNAL lässt die gewählte Engine gelten; die
# OCIO-Flags gibt es erst ab 2026.2 — fehlen sie, bleibt es bei EXTERNAL.
EXTERNAL = getattr(c4d, "RENDERFLAGS_EXTERNAL", 0)
OCIO_BAKE = getattr(c4d, "RENDERFLAGS_OCIO_BAKE_RENDERING", None)
OCIO_RAW = getattr(c4d, "RENDERFLAGS_OCIO_RAW_RENDERING", None)

# Dokumentkennung: Untercontainer unter der Plugin-ID im Container des Dokuments (QC-10).
DOCUMENT_KEY_ID = 1
DOCUMENT_KEY_PREFIX = f"{host.KEY}:"
_DOCUMENT_KEY = re.compile(r"^cinema-4d:[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


class CaptureError(RuntimeError):
    """Ein Grund, warum nicht aufgenommen wurde — lesbar, ohne Pfad."""


@dataclass(frozen=True)
class PassSpec:
    role: str
    capability: str
    label: str
    buffer: int | None  # VPBUFFER_* des Multi-Pass-Kanals; None: im c4d-Modul nicht vorhanden
    channel: str  # wie der Kanal im Multi-Pass-Menü heißt
    color_space: str
    blocked_by: str | None = None
    blocked_note: str | None = None


PASSES: tuple[PassSpec, ...] = (
    PassSpec("depth", "depthPass", "Tiefe (Depth)", getattr(c4d, "VPBUFFER_DEPTH", None), "Depth", "non-color",
             blocked_by="QC-03",
             blocked_note="Tiefe wird nicht übertragen: Kodierung nicht belegt, Standard/Physical normiert zur "
                          "Fokusebene, nicht linear-metric (integrations/cinema-4d/docs/open-questions.md, QC-03)."),
    PassSpec("normal", "normalPass", "Normalen", getattr(c4d, "VPBUFFER_MAT_NORMAL", None), "Material Normals",
             "non-color", blocked_by="QC-04",
             blocked_note="Normalen werden nicht übertragen: Raum und Vorzeichenbereich nicht belegt "
                          "(integrations/cinema-4d/docs/open-questions.md, QC-04)."),
    # 2026 kennt kein VPBUFFER_ALBEDO; der Kanal „Albedo" ist VPBUFFER_REFLECTANCE_ALBEDO
    # („Reflectance Channel Diffuse Albedo", C++ 2026, group VPBUFFER).
    PassSpec("albedo", "albedoPass", "Albedo", getattr(c4d, "VPBUFFER_REFLECTANCE_ALBEDO", None), "Albedo",
             "linear"),
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


def render_view(doc):
    """Die Ansicht, aus der Cinema 4D rendert (Renderansicht); ohne sie die aktive."""
    getter = getattr(doc, "GetRenderBaseDraw", None)
    view = getter() if getter is not None else None
    return view if view is not None else doc.GetActiveBaseDraw()


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


def pass_status(spec: PassSpec, rd) -> tuple[str, str]:
    """Zustand eines Passes in **diesem** Dokument und was der Nutzer einstellen muss."""
    engine = rd[c4d.RDATA_RENDERENGINE]
    where = f"Rendervoreinstellungen › Multi-Pass › Kanal „{spec.channel}“"
    if not MULTIPASS_RENDERERS or engine not in MULTIPASS_RENDERERS:
        return ("requires-user-action",
                f"Renderer Standard oder Physical wählen (jetzt {renderer_label(engine)}), dann {where} hinzufügen.")
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
    (QC-03).
    """
    capabilities: dict = {}
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
        state, _hint = pass_status(spec, rd)
        capabilities[spec.capability] = {"state": state, "constraints": {"mediaTypes": [EXR]}}
    return capabilities


def pass_rows(doc) -> list[tuple[PassSpec, str, str]]:
    """Die Pässe für den Dialog: (Pass, Zustand, Hinweis) — ohne die, die das c4d-Modul nicht kennt."""
    rd = doc.GetActiveRenderData()
    if rd is None:
        return []
    return [(spec, *pass_status(spec, rd)) for spec in PASSES if spec.buffer is not None]


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


def color_note() -> str:
    if OCIO_BAKE is not None:
        return ("PNG 8 Bit, gemeldet als srgb: OCIO-Ansichtstransformation eingebacken "
                "(RENDERFLAGS_OCIO_BAKE_RENDERING) — nicht am Host gemessen (QC-12)")
    return ("PNG 8 Bit, gemeldet als srgb mit eingebackener Ansichtstransformation angenommen — "
            "vor 2026.2 ohne Flag dafür, nicht am Host gemessen (QC-12)")


def render_viewport(doc, root: str, size: tuple[int, int] | None, progress) -> mf.CaptureFile:
    """Die Renderansicht über den Viewport Renderer als PNG — so, wie sie im Editor zu sehen ist.

    Eine Kopie des Containers der aktiven Rendervoreinstellung mit
    ``RDATA_RENDERENGINE_PREVIEWHARDWARE`` und ``RENDERFLAGS_EXTERNAL``
    (``render_document_hardware_2026_2.py``). Das Dokument bleibt unberührt.
    """
    if render_view(doc) is None:
        raise CaptureError("Keine Ansicht — die Viewport-Aufnahme braucht eine.")
    if not viewport_renderer_available():
        raise CaptureError("Der Viewport Renderer ist in diesem Cinema 4D nicht verfügbar.")
    size = size or document_size(doc)
    images = os.path.join(root, "images")
    os.makedirs(images, exist_ok=True)
    path = os.path.join(images, "viewport.png")
    data = doc.GetActiveRenderData().GetDataInstance().GetClone(c4d.COPYFLAGS_NONE)
    data[c4d.RDATA_RENDERENGINE] = VIEWPORT_RENDERER
    _prepare_data(data, size)
    bitmap = _bitmap(size)
    _render(doc, data, bitmap, progress, "Viewport rendern")
    _save_png(bitmap, path)
    note = (f"Viewport Renderer (RenderDocument, RDATA_RENDERENGINE_PREVIEWHARDWARE, RENDERFLAGS_EXTERNAL, "
            f"Kopie der Rendervoreinstellung), {view_name(doc)}; {color_note()}.")
    return mf.capture_file(path, root, "viewport", PNG, mf.describe_png(path), note)


def _find_layer(bitmap, buffer: int):
    """Die Ebene eines Multi-Pass-Kanals in der MultipassBitmap (``MPBTYPE_USERID`` = ``VPBUFFER_*``)."""
    for index in range(bitmap.GetLayerCount()):
        layer = bitmap.GetLayerNum(index)
        if layer is not None and layer.GetParameter(c4d.MPBTYPE_USERID) == buffer:
            return layer
    return None


def render_beauty(doc, root: str, size: tuple[int, int] | None, roles: list[str],
                  allowed_media_types: list[str] | None, progress):
    """Beauty als PNG und die gewählten Pässe — ``(beauty, pass_files, planned)``.

    Gerendert wird mit dem aktiven Renderer auf einer Kopie des Containers der
    aktiven Rendervoreinstellung; das Dokument bleibt unverändert. Ein
    gewählter Pass wird ``planned`` mit Begründung, wenn eine offene Frage
    ``present`` verbietet (QC-03, QC-04), der Kanal nicht eingeschaltet ist
    oder der Server ``image/x-exr`` nicht annimmt — kein stiller Umweg über ein
    anderes Format. Nur wenn ein Pass wirklich als Datei mitgeht, rendert das
    Plugin ein zweites Mal in eine MultipassBitmap mit 32 Bit je Kanal.
    """
    rd = doc.GetActiveRenderData()
    if rd is None:
        raise CaptureError("Das Dokument hat keine aktive Rendervoreinstellung.")
    size = size or document_size(doc)
    images = os.path.join(root, "images")
    os.makedirs(images, exist_ok=True)

    planned: list[mf.PlannedRole] = []
    wanted: list[PassSpec] = []
    exr_allowed = allowed_media_types is None or EXR in allowed_media_types
    for role in roles:
        spec = PASS_BY_ROLE[role]
        path = f"images/{role}.exr"
        state, hint = pass_status(spec, rd) if spec.buffer is not None else ("unknown", "")
        if spec.blocked_by:
            planned.append(mf.PlannedRole(role, path, EXR, spec.blocked_note))
        elif state != "available":
            planned.append(mf.PlannedRole(role, path, EXR, f"Pass nicht eingeschaltet: {hint}".strip()))
        elif not exr_allowed:
            planned.append(mf.PlannedRole(role, path, EXR,
                                          "Der Server nimmt image/x-exr nicht an (limits.allowedMediaTypes); "
                                          "Datenpässe gibt es nur als OpenEXR."))
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
    if wanted:
        layered = c4d.bitmaps.MultipassBitmap(size[0], size[1], c4d.COLORMODE_RGBf)
        if layered is None:
            raise CaptureError("Cinema 4D konnte keine MultipassBitmap anlegen.")
        _render(doc, data, layered, progress, "Pässe rendern", bake=False)
        for spec in wanted:
            layer = _find_layer(layered, spec.buffer)
            path = os.path.join(images, f"{spec.role}.exr")
            if layer is None or layer.Save(path, c4d.FILTER_EXR, None, c4d.SAVEBIT_USE32BITCHANNELS) != c4d.IMAGERESULT_OK:
                planned.append(mf.PlannedRole(spec.role, f"images/{spec.role}.exr", EXR,
                                              f"Cinema 4D hat den Kanal {spec.channel} nicht geliefert."))
                continue
            with open(path, "rb") as handle:
                description = exr.describe(handle.read())
            image = dict(description, colorSpace=spec.color_space)
            space = ("im Renderraum (RENDERFLAGS_OCIO_RAW_RENDERING; bei OCIO ACEScg, QC-12)"
                     if OCIO_RAW is not None else "linear, Primärvalenzen nicht belegt (QC-12)")
            note = (f"Multi-Pass „{spec.channel}“ aus {engine}, OpenEXR {image['bitDepth']} Bit "
                    f"{image['sampleFormat']}, {space}, zweiter Renderdurchgang.")
            files.append(mf.capture_file(path, root, spec.role, EXR, image, note))

    beauty = mf.capture_file(beauty_path, root, "beauty", PNG, mf.describe_png(beauty_path),
                             f"Beauty (RenderDocument), {engine}, {view_name(doc)}; {color_note()}.")
    order = [spec.role for spec in PASSES]
    files.sort(key=lambda f: order.index(f.role))
    planned.sort(key=lambda p: order.index(p.role))
    return beauty, files, planned
