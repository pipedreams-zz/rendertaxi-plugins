"""Transiente Rendereinstellung — ein Videopost oder Multi-Pass-Kanal nur für die Dauer eines synchronen Renders (RTX-C4D-006, #209).

Warum: Videoposts (und Multi-Pass-Kanäle) gehören zum ``RenderData``-**Objekt**, nicht zu dessen Container; die Kopie des
Containers, mit der das Plugin sonst rendert, trägt sie nicht. Ein Dokumentklon dafür ist keine Lösung: wird er nach dem Render
verworfen, stürzt Cinema 4D 2026.3.1 beim **nächsten** Render ab (``docs/measurements/2026-10-04-klon-absturz.md``, 12 Läufe).
Deshalb wird die aktive Rendervoreinstellung des Dokuments selbst kurz verändert und im ``finally`` **exakt** zurückgesetzt.

Zusagen:

* Vorher werden die Videoposts und die Multi-Pass-Kanäle der Voreinstellung festgehalten; zurückgesetzt wird ein eingefügter
  Videopost/Kanal durch ``Remove``, ein geänderter vorhandener Videopost durch den vorher geklonten Container (``SetData``).
* Kein Undo-Eintrag (kein ``StartUndo``), das Dokument wird **nicht** als geändert markiert (kein ``SetChanged``).
* Selbstheilung: eingefügte Objekte tragen den Namen ``rendertaxi:transient`` und einen privaten Marker im Container
  (``MARKER_KEY`` = Plugin-ID, Wert ``transient``). Bleibt nach einem Absturz mitten im Render einer in der Szene zurück,
  entfernt ``sweep`` ihn vor der nächsten Aufnahme, bei der Laufzeitprobe und beim Öffnen des Fensters; die Zahl steht in
  ``note``. Eigentum belegt nur Name **und** Marker: ein gleichnamiger Eintrag des Nutzers ohne diesen Marker (oder mit
  einem anderen Wert darunter) wird nie entfernt und wie jeder andere Nutzereintrag behandelt.
"""

from __future__ import annotations

import c4d

from . import host

MARKER = "rendertaxi:transient"
MARKER_KEY = host.PLUGIN_ID  # Schlüssel im Container des eingefügten Objekts
MARKER_VALUE = "transient"


def _is_marker(item) -> bool:
    """Nur vom Plugin eingefügt: Name und privater Container-Marker — der Name allein kann vom Nutzer stammen."""
    if item.GetName() != MARKER:
        return False
    data = item.GetDataInstance()
    return data is not None and data.GetString(MARKER_KEY) == MARKER_VALUE


def _mark(item) -> None:
    item.SetName(MARKER)
    item.GetDataInstance().SetString(MARKER_KEY, MARKER_VALUE)


def _items(first):
    item = first
    while item is not None:
        yield item
        item = item.GetNext()


def sweep(doc) -> int:
    """Entfernt verwaiste Marker-Einträge des Dokuments und liefert die Zahl: Videoposts und Kanäle in jeder
    Rendervoreinstellung, und markierte Kopien einer ganzen Rendervoreinstellung (Dateiweg der Objekt-ID,
    RTX-C4D-005). Eigentum nur über Name **und** privaten Marker. Ist eine verwaiste Kopie aktiv, wird zuerst eine
    unmarkierte Voreinstellung aktiv."""
    removed = 0
    rd = doc.GetFirstRenderData()
    orphans = []
    while rd is not None:
        if _is_marker(rd):
            orphans.append(rd)
        else:
            for first in (rd.GetFirstVideoPost(), rd.GetFirstMultipass()):
                for item in [i for i in _items(first) if _is_marker(i)]:
                    item.Remove()
                    removed += 1
        rd = rd.GetNext()
    if orphans:
        active = doc.GetActiveRenderData()
        if any(orphan is active or orphan == active for orphan in orphans):
            keep = next((item for item in _items(doc.GetFirstRenderData()) if not _is_marker(item)), None)
            if keep is not None:
                doc.SetActiveRenderData(keep)
        for orphan in orphans:
            orphan.Remove()
            removed += 1
    return removed


def sweep_open_documents() -> int:
    """``sweep`` für jedes geöffnete Dokument (beim Öffnen des Fensters, vor einer Aufnahme)."""
    removed = 0
    doc = c4d.documents.GetFirstDocument()
    while doc is not None:
        removed += sweep(doc)
        doc = doc.GetNext()
    return removed


class TransientRenderSettings:
    """``with TransientRenderSettings(doc) as t: t.videopost(id, {symbol: wert}); …render…`` — danach ist alles wie vorher."""

    def __init__(self, doc):
        self.doc = doc
        self.rd = doc.GetActiveRenderData()
        self.swept = 0
        self.problems: list[str] = []
        self._inserted: list = []
        self._changed: list = []  # (Videopost, vorher geklonter Container)

    def __enter__(self):
        self.swept = sweep(self.doc)
        return self

    def videopost(self, plugin_id: int, values: dict):
        """Der Videopost dieser ID mit diesen Werten: ein vorhandener wird geändert, sonst einer neu eingefügt."""
        post = next((p for p in _items(self.rd.GetFirstVideoPost()) if p.GetType() == plugin_id and not _is_marker(p)), None)
        if post is None:
            post = c4d.BaseList2D(plugin_id)
            if post is None:
                raise RuntimeError("Videopost nicht anlegbar")
            _mark(post)
            self.rd.InsertVideoPost(post)
            self._inserted.append(post)  # ab hier ist er in der Szene: Rückbau im finally
        else:
            self._changed.append((post, post.GetDataInstance().GetClone(c4d.COPYFLAGS_NONE)))
        for symbol, value in values.items():
            post[symbol] = value
        return post

    def multipass(self, buffer: int):
        """Ein Multi-Pass-Kanal dieses Typs: ein vorhandener bleibt, sonst wird einer eingefügt."""
        for channel in _items(self.rd.GetFirstMultipass()):
            if channel[c4d.MULTIPASSOBJECT_TYPE] == buffer and not _is_marker(channel):
                return channel
        channel = c4d.BaseList2D(c4d.Zmultipass)
        channel[c4d.MULTIPASSOBJECT_TYPE] = buffer
        _mark(channel)
        self.rd.InsertMultipass(channel)
        self._inserted.append(channel)
        return channel

    def __exit__(self, *_exc):
        for item in reversed(self._inserted):
            try:
                item.Remove()
            except Exception as error:  # noqa: BLE001 — weiter zurücksetzen, den Rest melden
                self.problems.append(f"Entfernen: {type(error).__name__}")
        for post, saved in reversed(self._changed):
            try:
                post.SetData(saved, False)
            except Exception as error:  # noqa: BLE001
                self.problems.append(f"Zurücksetzen: {type(error).__name__}")
        return False
