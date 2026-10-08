"""Das Werkzeugfenster „rendertaxi.ai" — ein ``GeDialog`` mit Tabs und einem festen Bereich „Übernehmen".

Nutzerentscheidung vom 05.10.2026 (RTX-C4D-008): oben fünf Tabs, darunter — gleich welcher Tab vorn ist — der
Bereich „Übernehmen".

* **Verbindung** — Verbinden (Code und Adresse, der Browser öffnet sich), „Angemeldet als …", Abmelden;
  darunter die Einstellungen (Serveradresse, Gerätename, ausführliches Protokoll).
* **Projekt** — Projekt, „Neues Projekt …" (Name erfragen, anlegen, gleich wählen), „Aktualisieren" (auch
  umbenannte Projekte).
* **Blickpunkt** — neuer Blickpunkt mit Namen (der Name des Kameraobjekts steht als Vorschlag darin, bis der
  Nutzer ihn ändert) oder bestehenden aktualisieren; „Rahmen an Aufnahme anpassen" (``frame: fit-to-capture``,
  sonst ``keep``); Rahmengröße Canvas-Vorgabe oder Render-Einstellung (``size``).
* **Bild** — Viewport oder Beauty mit optionalen Pässen; Größe aus dem Dokument oder dem Blickpunkt-Rahmen;
  „Bittiefe der Datenpässe" (8 oder 16 Bit, sofort gemerkt, Wortlaut aus dem gemeinsamen Client).
* **Modell** — Größe zählen, Grenzen und Kamera der Renderansicht, bevor gesendet wird.
* **Übernehmen** (fest darunter) — die Wege „Bild" und „Modell" (einzeln oder zusammen, ``ways.plan``), der Satz,
  was gesendet wird, der Knopf, Fortsetzen oder Verwerfen einer angefangenen Übernahme, Fortschritt, Meldungen
  und „Blickpunkt im Browser öffnen" (``result.openUrl``).

Ohne Verbindung ist nur „Verbindung" bedienbar; die anderen Tabs sagen in einem Satz, was fehlt. Der zuletzt
gewählte Tab und die Wahl der Wege sind gemerkt — nie als Bedingung fürs Laden (Regel 3).

Der Dialog zeichnet nur; was geschieht, entscheidet ``controller.Controller``.
Listen werden über Kennungen ausgewählt, nie über Positionen: eine neue
Projektliste verschiebt die Auswahl nicht. Jeder Klick läuft durch
``_guarded``: eine unerwartete Ausnahme landet ohne Pfad im Protokoll und als
Satz im Dialog, nie als Traceback in der Konsole.
"""

from __future__ import annotations

import c4d
from c4d import gui

from . import host
from .controller import BEAUTY, RESOLUTION_DOCUMENT, RESOLUTION_VIEWPOINT, TAKE_LABEL, VIEWPORT, Controller, wrap
from .rendertaxi_client import frame
from .rendertaxi_client import manifest as mf
from .rendertaxi_client.log import log_exception

# -- Kennungen der Elemente ----------------------------------------------------
ID_MAIN = 1000
ID_GRP_CONNECTION = 1010
ID_STATUS = 1011
ID_CODE = 1012
ID_SERVER_TEXT = 1013
ID_CONNECT = 1014
ID_OPEN_LOGIN = 1015
ID_CANCEL = 1016
ID_SIGN_OUT = 1017
ID_GRP_SETTINGS = 1020
ID_SERVER = 1021
ID_DEVICE = 1022
ID_DEBUG = 1023
ID_SAVE_SETTINGS = 1024
ID_PROJECT = 1031
ID_REFRESH = 1032
ID_NEW_PROJECT = 1039
ID_TARGET_MODE = 1033
ID_VP_NAME = 1034
ID_VIEWPOINT = 1035
ID_FIT = 1036
ID_SIZE = 1037
ID_SIZE_TEXT = 1038
ID_HINT = 1040  # bis 1043: vier Zeilen
ID_KIND = 1051
ID_RESOLUTION = 1052
ID_CUT = 1053
ID_PASS = {"depth": 1060, "normal": 1061, "albedo": 1062, "object-id": 1063, "material-id": 1064}
# Die Hinweise zu den Pässen: ein Block statt einer Zeile je Pass — nichts doppelt, nichts abgeschnitten (Host, 07.10.2026).
ID_PASS_NOTE = 1110  # bis 1115: sechs Zeilen
PASS_NOTE_LINES = 6
PASS_LABELS = {"depth": "Tiefe", "normal": "Normalen", "albedo": "Albedo", "object-id": "Objekt-ID",
               "material-id": "Material-ID"}
ID_BIT_DEPTH = 1068
ID_MODEL = 1100
ID_MODEL_COUNT = 1101
ID_MODEL_HINT = 1102  # bis 1105: vier Zeilen
ID_CAPTURE = 1070
ID_RESUME = 1071
ID_DISCARD = 1072
ID_PENDING = 1073
ID_PROGRESS = 1080
ID_MESSAGE = 1081  # bis 1084: vier Zeilen
ID_OPEN_RESULT = 1090
ID_RESULT = 1091
# Tabs (RTX-C4D-008): die Tab-Gruppe, je Tab eine Gruppe, darin ein Satz ohne Verbindung und der Inhalt.
ID_TABS = 1200
ID_TAB = {"connection": 1201, "project": 1202, "viewpoint": 1203, "image": 1204, "model": 1205}
ID_TAB_NOTE = {"project": 1212, "viewpoint": 1213, "image": 1214, "model": 1215}
ID_TAB_BODY = {"project": 1222, "viewpoint": 1223, "image": 1224, "model": 1225}
ID_GRP_SEND = 1230
ID_SEND_IMAGE = 1231
ID_SEND_SUMMARY = 1232  # bis 1234: drei Zeilen (SUMMARY_LINES)
# RTX-C4D-010: Kameras im Modell und das Rendern im Picture Viewer.
ID_SEND_CAMERAS = 1236
ID_TAKE = 1074
ID_CAMERAS = 1240  # die Gruppe der Kameraliste, neu gefüllt, wenn sich die Kameras ändern
ID_CAMERA_NOTE = 1241
ID_CAMERA_FIRST = 1300  # bis 1300 + MAX_CAMERAS - 1
MAX_CAMERAS = 40
# Die Gruppen von früher: „Projekt und Blickpunkt" sind jetzt zwei Tabs, „Bild übernehmen" der Tab Bild.
ID_GRP_TARGET = ID_TAB_BODY["project"]
ID_GRP_CAPTURE = ID_TAB_BODY["image"]

MODES = (frame.CREATE, frame.UPDATE)
MODE_LABELS = ("Neuer Blickpunkt", "Bestehenden aktualisieren")
SIZES = (frame.SIZE_CANVAS_DEFAULT, frame.SIZE_CAPTURE)
SIZE_LABELS = ("Canvas-Vorgabe", "Render-Einstellung übernehmen")
KINDS = (VIEWPORT, BEAUTY)
KIND_LABELS = ("Viewport (Viewport Renderer)", "Beauty (aktiver Renderer)")
RESOLUTIONS = (RESOLUTION_DOCUMENT, RESOLUTION_VIEWPOINT)
RESOLUTION_LABELS = ("Rendervoreinstellung des Dokuments", "Blickpunkt-Rahmen")
# Bittiefe der Datenpässe: Wortlaut und Reihenfolge aus dem gemeinsamen Client (wie Blender).
BIT_DEPTHS = tuple(value for value, _label in mf.DATA_PASS_BIT_DEPTH_OPTIONS)
BIT_DEPTH_LABELS = tuple(label for _value, label in mf.DATA_PASS_BIT_DEPTH_OPTIONS)
TAB_TITLES = {"connection": "Verbindung", "project": "Projekt", "viewpoint": "Blickpunkt", "image": "Bild",
              "model": "Modell"}
NOT_CONNECTED = "Erst unter „Verbindung“ anmelden — dann lässt sich hier wählen."
LINES = 4
SUMMARY_LINES = 3
WIDTH = 64


class RendertaxiDialog(gui.GeDialog):
    def __init__(self, controller: Controller):
        super().__init__()
        self.controller = controller
        self._lists: dict[int, list] = {}
        self._labels: dict[int, list[str]] = {}
        # Referenz 2026: Add* gibt ein C4DGadget zurück, Enable nimmt genau dieses.
        self._gadgets: dict[int, object] = {}
        self._layout_state: tuple | None = None
        # Wie viele Zeilen eines mehrzeiligen Textes sichtbar sind — leere Zeilen belegen keinen Platz.
        self._shown_lines: dict[int, int] = {}
        self._tab: str | None = None
        # Die Kameraliste, wie sie gerade im Tab „Modell“ steht: (Schlüssel, Beschriftung) je Häkchen.
        self._camera_rows: list[tuple[str, str]] | None = None

    # -- Aufbau ---------------------------------------------------------------

    def _text(self, element_id: int, text: str = "") -> None:
        self.AddStaticText(element_id, c4d.BFH_SCALEFIT, 0, 0, text, 0)

    def _enable(self, element_id: int, on: bool) -> None:
        """``GeDialog.Enable`` mit dem Gadget des Elements (Referenz 2026: ``gadget (C4DGadget)``)."""
        gadget = self._gadgets.get(element_id)
        if gadget is not None:
            self.Enable(gadget, bool(on))

    def _combo(self, element_id: int, labels=()) -> None:
        self._gadgets[element_id] = self.AddComboBox(element_id, c4d.BFH_SCALEFIT, 0, 0)
        for index, label in enumerate(labels):
            self.AddChild(element_id, index, label)

    def _tab_begin(self, tab: str) -> None:
        """Ein Tab: seine Gruppe, darin — außer bei „Verbindung" — der Satz ohne Verbindung und der Inhalt."""
        self.GroupBegin(ID_TAB[tab], c4d.BFH_SCALEFIT | c4d.BFV_TOP, 1, 0, TAB_TITLES[tab])
        self.GroupBorderSpace(6, 6, 6, 6)
        if tab in ID_TAB_BODY:
            self._text(ID_TAB_NOTE[tab], NOT_CONNECTED)
            self.GroupBegin(ID_TAB_BODY[tab], c4d.BFH_SCALEFIT | c4d.BFV_TOP, 1, 0, "")

    def _tab_end(self, tab: str) -> None:
        if tab in ID_TAB_BODY:
            self.GroupEnd()
        self.GroupEnd()

    def CreateLayout(self) -> bool:
        # **Jedes Mal frisch.** Cinema 4D ruft ``CreateLayout`` beim Wiederöffnen auf demselben Dialogobjekt
        # erneut auf; gemerkte Listen und Sichtbarkeit gehörten zum alten Fenster. Am Host (07.10.2026) blieb
        # sonst „Erst anmelden“ stehen und die Projektliste leer, obwohl die Verbindung bestand.
        self._lists, self._labels, self._gadgets = {}, {}, {}
        self._layout_state = None
        self._shown_lines = {}
        self._camera_rows = None
        self.SetTitle(f"rendertaxi.ai {host.PLUGIN_VERSION}")
        self.GroupBegin(ID_MAIN, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 1, 0, "")
        self.GroupBorderSpace(8, 8, 8, 8)
        # Die Tabs nehmen den Platz, der übrig ist; „Übernehmen“ darunter bleibt so klein wie sein Inhalt.
        self.TabGroupBegin(ID_TABS, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, c4d.TAB_TABS)

        self._tab_begin("connection")
        self._text(ID_STATUS)
        self._text(ID_CODE)
        self._text(ID_SERVER_TEXT)
        self.GroupBegin(0, c4d.BFH_SCALEFIT, 4, 1, "")
        self._gadgets[ID_CONNECT] = self.AddButton(ID_CONNECT, c4d.BFH_SCALEFIT, 0, 0, "Verbinden")
        self._gadgets[ID_OPEN_LOGIN] = self.AddButton(ID_OPEN_LOGIN, c4d.BFH_SCALEFIT, 0, 0, "Im Browser öffnen")
        self._gadgets[ID_CANCEL] = self.AddButton(ID_CANCEL, c4d.BFH_SCALEFIT, 0, 0, "Abbrechen")
        self._gadgets[ID_SIGN_OUT] = self.AddButton(ID_SIGN_OUT, c4d.BFH_SCALEFIT, 0, 0, "Abmelden")
        self.GroupEnd()
        # Beschriftung so breit wie nötig, das Feld nimmt den Rest der Zeile (Host, 07.10.2026).
        self.GroupBegin(ID_GRP_SETTINGS, c4d.BFH_SCALEFIT, 2, 0, "Einstellungen")
        self.AddStaticText(0, c4d.BFH_LEFT, 0, 0, "Serveradresse", 0)
        self._gadgets[ID_SERVER] = self.AddEditText(ID_SERVER, c4d.BFH_SCALEFIT, 0, 0)
        self.AddStaticText(0, c4d.BFH_LEFT, 0, 0, "Gerätename", 0)
        self._gadgets[ID_DEVICE] = self.AddEditText(ID_DEVICE, c4d.BFH_SCALEFIT, 0, 0)
        self.GroupEnd()
        self._gadgets[ID_DEBUG] = self.AddCheckbox(ID_DEBUG, c4d.BFH_SCALEFIT, 0, 0, "Ausführliches Protokoll (nie Pfade oder Namen)")
        self._gadgets[ID_SAVE_SETTINGS] = self.AddButton(ID_SAVE_SETTINGS, c4d.BFH_LEFT, 0, 0, "Einstellungen sichern")
        self._tab_end("connection")

        self._tab_begin("project")
        self.GroupBegin(0, c4d.BFH_SCALEFIT, 3, 1, "")
        self._combo(ID_PROJECT)
        self._gadgets[ID_NEW_PROJECT] = self.AddButton(ID_NEW_PROJECT, c4d.BFH_RIGHT, 0, 0, "Neues Projekt …")
        self._gadgets[ID_REFRESH] = self.AddButton(ID_REFRESH, c4d.BFH_RIGHT, 0, 0, "Aktualisieren")
        self.GroupEnd()
        self._tab_end("project")

        self._tab_begin("viewpoint")
        self._combo(ID_TARGET_MODE, MODE_LABELS)
        self._gadgets[ID_VP_NAME] = self.AddEditText(ID_VP_NAME, c4d.BFH_SCALEFIT, 300, 0)
        self._combo(ID_VIEWPOINT)
        self._gadgets[ID_FIT] = self.AddCheckbox(ID_FIT, c4d.BFH_SCALEFIT, 0, 0, "Rahmen an Aufnahme anpassen")
        self._text(0, "Rahmengröße")
        self._combo(ID_SIZE, SIZE_LABELS)
        self._text(ID_SIZE_TEXT)
        for line in range(LINES):
            self._text(ID_HINT + line)
        self._tab_end("viewpoint")

        self._tab_begin("image")
        self._combo(ID_KIND, KIND_LABELS)
        self._text(0, "Größe")
        self._combo(ID_RESOLUTION, RESOLUTION_LABELS)
        self._text(ID_CUT)
        self._text(0, "Pässe (optional, nur Beauty)")
        self.GroupBegin(0, c4d.BFH_SCALEFIT, 3, 0, "")
        for spec_role, label in (("depth", "Tiefe (Depth)"), ("normal", "Normalen"), ("albedo", "Albedo"),
                                 ("object-id", "Objekt-ID"), ("material-id", "Material-ID")):
            self._gadgets[ID_PASS[spec_role]] = self.AddCheckbox(ID_PASS[spec_role], c4d.BFH_SCALEFIT, 0, 0, label)
        self.GroupEnd()
        for line in range(PASS_NOTE_LINES):
            self._text(ID_PASS_NOTE + line)
        self._text(0, mf.DATA_PASS_BIT_DEPTH_LABEL)
        self._combo(ID_BIT_DEPTH, BIT_DEPTH_LABELS)
        self._tab_end("image")

        self._tab_begin("model")
        self._text(0, "GLB der sichtbaren Objekte mit der Kamera der Renderansicht.")
        self._gadgets[ID_MODEL_COUNT] = self.AddButton(ID_MODEL_COUNT, c4d.BFH_LEFT, 0, 0, "Neu zählen")
        for line in range(LINES):
            self._text(ID_MODEL_HINT + line)
        self._text(0, "Kameras der Szene")
        self.GroupBegin(ID_CAMERAS, c4d.BFH_SCALEFIT, 1, 0, "")
        self.GroupEnd()
        self._text(ID_CAMERA_NOTE)
        self._tab_end("model")
        self.GroupEnd()  # Tabs

        # Der feste Bereich: gleich welcher Tab vorn ist.
        self.GroupBegin(ID_GRP_SEND, c4d.BFH_SCALEFIT | c4d.BFV_BOTTOM, 1, 0, "Übernehmen")
        self.GroupBorder(c4d.BORDER_GROUP_IN)
        self.GroupBorderSpace(6, 6, 6, 6)
        self.GroupBegin(0, c4d.BFH_SCALEFIT, 2, 2, "")
        self._gadgets[ID_SEND_IMAGE] = self.AddCheckbox(ID_SEND_IMAGE, c4d.BFH_SCALEFIT, 0, 0, "Bild")
        self._gadgets[ID_MODEL] = self.AddCheckbox(ID_MODEL, c4d.BFH_SCALEFIT, 0, 0, "Modell")
        self.AddStaticText(0, c4d.BFH_SCALEFIT, 0, 0, "", 0)
        # Unter „Modell“ (Nutzerwunsch vom 07.10.2026).
        self._gadgets[ID_SEND_CAMERAS] = self.AddCheckbox(ID_SEND_CAMERAS, c4d.BFH_SCALEFIT, 0, 0,
                                                          "Zusätzliche Kameras mitsenden")
        self.GroupEnd()
        for line in range(SUMMARY_LINES):
            self._text(ID_SEND_SUMMARY + line)
        self._text(ID_PENDING)
        self.GroupBegin(0, c4d.BFH_SCALEFIT, 4, 1, "")
        self._gadgets[ID_CAPTURE] = self.AddButton(ID_CAPTURE, c4d.BFH_SCALEFIT, 0, 0, "Bild und Modell übernehmen")
        self._gadgets[ID_TAKE] = self.AddButton(ID_TAKE, c4d.BFH_SCALEFIT, 0, 0, TAKE_LABEL)
        self._gadgets[ID_RESUME] = self.AddButton(ID_RESUME, c4d.BFH_SCALEFIT, 0, 0, "Übernahme fortsetzen")
        self._gadgets[ID_DISCARD] = self.AddButton(ID_DISCARD, c4d.BFH_SCALEFIT, 0, 0, "Angefangene verwerfen")
        self.GroupEnd()
        self._text(ID_PROGRESS)
        for line in range(LINES):
            self._text(ID_MESSAGE + line)
        self.GroupBegin(0, c4d.BFH_SCALEFIT, 2, 1, "")
        self._gadgets[ID_OPEN_RESULT] = self.AddButton(ID_OPEN_RESULT, c4d.BFH_LEFT, 0, 0, "Blickpunkt im Browser öffnen")
        self._text(ID_RESULT)
        self.GroupEnd()
        self.GroupEnd()
        self.GroupEnd()
        return True

    def InitValues(self) -> bool:
        settings = self.controller.settings()
        self.SetString(ID_SERVER, settings["serverUrl"])
        self.SetString(ID_DEVICE, settings["deviceName"])
        self.SetBool(ID_DEBUG, settings["debugLogging"])
        self.SetString(ID_VP_NAME, self.controller.form.viewpoint_name)
        # Die gemerkte Bittiefe — ungültig heißt 8 Bit, nie ein Fehler (Regel 3).
        self.SetInt32(ID_BIT_DEPTH, BIT_DEPTHS.index(self.controller.data_pass_bit_depth()))
        # Der gemerkte Tab — ein unbekannter heißt „Verbindung", nie ein Fehler (Regel 3).
        self._tab = self.controller.last_tab()
        self.SetInt32(ID_TABS, ID_TAB[self._tab])
        self.SetTimer(250)
        self._guarded(self.refresh)
        return True

    # -- Anzeige ----------------------------------------------------------------

    def _lines(self, first_id: int, text: str, lines: int = LINES) -> None:
        """Text umgebrochen auf ``lines`` Zeilen; leere Zeilen sind verborgen und belegen keinen Platz."""
        wrapped = wrap(text, WIDTH, lines)
        for offset, line in enumerate(wrapped):
            self.SetString(first_id + offset, line)
        used = sum(1 for line in wrapped if line)
        if self._shown_lines.get(first_id) != used:
            for offset in range(lines):
                self.HideElement(first_id + offset, offset >= used)
            self._shown_lines[first_id] = used
            self.LayoutChanged(ID_MAIN)

    def _fill(self, element_id: int, placeholder: str, items: list[tuple[str, str]], selected: str | None) -> None:
        """Eine Auswahlliste mit Platzhalter; gewählt wird über die Kennung, nie über die Position.

        Neu aufgebaut wird, wenn sich Kennungen **oder Namen** ändern — ein umbenanntes Projekt steht
        nach „Aktualisieren" mit seinem neuen Namen da (RTX-P-013).
        """
        ids = [None] + [item_id for item_id, _ in items]
        labels = [placeholder] + [name[:60] for _item_id, name in items]
        if self._lists.get(element_id) != ids or self._labels.get(element_id) != labels:
            self.FreeChildren(element_id)
            for index, label in enumerate(labels):
                self.AddChild(element_id, index, label)
            self._lists[element_id] = ids
            self._labels[element_id] = labels
        self.SetInt32(element_id, ids.index(selected) if selected in ids else 0)

    def _chosen(self, element_id: int) -> str | None:
        ids = self._lists.get(element_id) or [None]
        index = self.GetInt32(element_id)
        return ids[index] if 0 <= index < len(ids) else None

    def _shown_tab(self) -> str | None:
        """Welcher Tab vorn ist — aus der Tab-Gruppe; ``None``, wenn der Host nichts Bekanntes meldet."""
        shown = self.GetInt32(ID_TABS)
        return next((tab for tab, tab_id in ID_TAB.items() if tab_id == shown), None)

    def _follow_tab(self) -> None:
        """Einen Tabwechsel merken — Cinema 4D meldet ihn als ``Command``; der Timer fragt zur Sicherheit nach."""
        tab = self._shown_tab()
        if tab is not None and tab != self._tab:
            self._tab = tab
            self.controller.remember_tab(tab)

    def refresh(self) -> None:
        controller, state, form = self.controller, self.controller.state, self.controller.form
        busy = controller.busy()

        # Verbindung
        if state.code:
            self.SetString(ID_STATUS, f"Code: {state.code['userCode']}")
            self.SetString(ID_CODE, f"Im Browser bestätigen: {state.code['verificationUri']}")
        elif state.connected and state.identity:
            who = state.identity.get("user") or state.identity.get("email") or ""
            org = state.identity.get("organization") or ""
            self.SetString(ID_STATUS, f"Angemeldet als {who}")
            self.SetString(ID_CODE, org)
        else:
            self.SetString(ID_STATUS, "Nicht verbunden")
            self.SetString(ID_CODE, "")
        self.SetString(ID_SERVER_TEXT, state.server)
        self._enable(ID_CONNECT, not busy and not state.connected)
        self._enable(ID_OPEN_LOGIN, bool(state.code))
        self._enable(ID_CANCEL, busy)
        self._enable(ID_SIGN_OUT, not busy and state.connected)
        self._enable(ID_SAVE_SETTINGS, not busy)

        # Ohne Verbindung sagen die anderen Tabs in einem Satz, was fehlt; ihr Inhalt ist verborgen.
        visible = state.connected
        if self._layout_state != (visible,):
            for tab, body_id in ID_TAB_BODY.items():
                self.HideElement(body_id, not visible)
                self.HideElement(ID_TAB_NOTE[tab], visible)
                # Am Host belegte ein verborgener Satz sonst weiter seine Zeile (07.10.2026): jede Tab-Gruppe neu.
                self.LayoutChanged(ID_TAB[tab])
            self.LayoutChanged(ID_MAIN)
            self._layout_state = (visible,)

        if visible:
            self._refresh_target(form, state, busy)
            self._refresh_capture(form, busy)
            self._refresh_model(busy)
        self._refresh_send(form, state, busy)

        # Status
        if state.progress:
            text, percent = state.progress
            self.SetString(ID_PROGRESS, f"{text} ({percent} %)")
        elif busy and state.job.label != "connect":
            self.SetString(ID_PROGRESS, "Bitte warten …")
        else:
            self.SetString(ID_PROGRESS, "")
        self._lines(ID_MESSAGE, state.error or state.message)
        has_result = bool(state.result and state.result.get("openUrl"))
        self._enable(ID_OPEN_RESULT, has_result)
        self.SetString(ID_RESULT, "Übernahme abgeschlossen." if has_result else "")

    def _refresh_target(self, form, state, busy: bool) -> None:
        self._fill(ID_PROJECT, "— Projekt wählen —", state.projects, form.project_id)
        self.SetInt32(ID_TARGET_MODE, MODES.index(form.target_mode))
        creating = form.target_mode == frame.CREATE
        # Der Kameraname als Vorschlag; was der Nutzer eingegeben hat, bleibt (Controller entscheidet).
        self.controller.suggest_viewpoint_name()
        if self.GetString(ID_VP_NAME) != form.viewpoint_name:
            self.SetString(ID_VP_NAME, form.viewpoint_name)
        self._enable(ID_VP_NAME, creating)
        self._enable(ID_VIEWPOINT, not creating)
        self._enable(ID_FIT, not creating)
        self._fill(ID_VIEWPOINT, "— Blickpunkt wählen —", state.viewpoints.get(form.project_id or "", []),
                   form.viewpoint_id)
        self.SetBool(ID_FIT, form.fit_to_capture)
        # „Render-Einstellung übernehmen“ nennt die Größe der Rendervoreinstellung (Host, 07.10.2026).
        try:
            width, height = self.controller.document_size()
            labels = [SIZE_LABELS[0], f"{SIZE_LABELS[1]} ({width} × {height})"]
        except RuntimeError:  # kein Dokument
            labels = list(SIZE_LABELS)
        if self._labels.get(ID_SIZE) != labels:
            self.FreeChildren(ID_SIZE)
            for index, label in enumerate(labels):
                self.AddChild(ID_SIZE, index, label)
            self._labels[ID_SIZE] = labels
        self.SetInt32(ID_SIZE, SIZES.index(form.size))
        self.SetString(ID_SIZE_TEXT, self.controller.size_text())
        self._lines(ID_HINT, self.controller.aspect_hint() or "")
        self._enable(ID_REFRESH, not busy)
        self._enable(ID_NEW_PROJECT, not busy)

    def _refresh_capture(self, form, busy: bool) -> None:
        controller = self.controller
        self.SetInt32(ID_KIND, KINDS.index(form.capture_kind))
        self.SetInt32(ID_RESOLUTION, RESOLUTIONS.index(form.resolution))
        self._enable(ID_RESOLUTION, form.target_mode == frame.UPDATE)
        try:
            size = controller.capture_size() or controller.document_size()
            self.SetString(ID_CUT, f"Ausschnitt {size[0]} × {size[1]}")
        except RuntimeError as error:  # kein Dokument
            self.SetString(ID_CUT, str(error))
        rows = {}
        try:
            rows = {spec.role: (state, hint, spec) for spec, state, hint in controller.adapter.pass_rows()}
        except RuntimeError:
            rows = {}
        allowed = ((controller.state.handshake or {}).get("limits") or {}).get("allowedMediaTypes")
        no_png = allowed is not None and mf.PNG_MEDIA_TYPE not in allowed
        bit_depth = controller.data_pass_bit_depth()
        self.SetInt32(ID_BIT_DEPTH, BIT_DEPTHS.index(bit_depth))
        self._enable(ID_BIT_DEPTH, not busy)
        notes: list[tuple[str, str]] = []
        offered = []
        for role, element_id in ID_PASS.items():
            state, hint, spec = rows.get(role, ("unknown", "", None))
            usable = form.capture_kind == BEAUTY and state == "available"
            self._enable(element_id, usable)
            self.SetBool(element_id, usable and role in form.passes)
            if spec is None:
                notes.append((role, "In diesem Cinema 4D nicht erkannt."))
            elif state != "available":
                notes.append((role, hint))
            elif spec.blocked_by:
                notes.append((role, spec.blocked_note or "Nicht übertragbar."))
            else:
                offered.append(role)
        if offered and no_png:
            notes.append(("", "Dieser Server nimmt noch keine Pässe an; übertragen wird das Bild."))
        elif offered:
            notes.append(("", f"Pässe als PNG {bit_depth} Bit: Tiefe von nah (0) bis fern (1), Objekt-ID je Pixel "
                              "die ID des Objektpuffers, sonst linear."))
        self._lines(ID_PASS_NOTE, pass_notes(notes), PASS_NOTE_LINES)

    def _refresh_model(self, busy: bool) -> None:
        controller = self.controller
        self._enable(ID_MODEL_COUNT, not busy and controller.form.send_model)
        hint = controller.model_hint() if controller.form.send_model else "Unter „Übernehmen“ „Modell“ wählen."
        self._lines(ID_MODEL_HINT, hint)
        self._refresh_cameras(busy)

    def _refresh_cameras(self, busy: bool) -> None:
        """Die Kameras der Szene mit Häkchen — neu aufgebaut, wenn sich Kameras oder Namen ändern.

        Die Kamera der Renderansicht geht immer mit: ihr Häkchen ist gesetzt und gesperrt. Die anderen gelten nur mit
        „Zusätzliche Kameras mitsenden“.
        """
        form = self.controller.form
        rows = self.controller.camera_rows()
        shown = rows[:MAX_CAMERAS]
        wanted = [(row["key"], self._camera_label(row)) for row in shown]
        if wanted != self._camera_rows:
            self.LayoutFlushGroup(ID_CAMERAS)
            for index, (_key, label) in enumerate(wanted):
                self._gadgets[ID_CAMERA_FIRST + index] = self.AddCheckbox(ID_CAMERA_FIRST + index, c4d.BFH_SCALEFIT,
                                                                          0, 0, label)
            self.LayoutChanged(ID_CAMERAS)
            self._camera_rows = wanted
        usable = form.send_model and form.send_extra_cameras and not busy
        for index, row in enumerate(shown):
            self.SetBool(ID_CAMERA_FIRST + index, row["checked"])
            self._enable(ID_CAMERA_FIRST + index, usable and not row["render"])
        if not rows:
            note = "Keine Kamera in der Szene; gesendet wird die Ansicht der Renderansicht."
        elif len(rows) > MAX_CAMERAS:
            note = f"Gezeigt sind die ersten {MAX_CAMERAS} von {len(rows)} Kameras."
        elif not form.send_extra_cameras:
            note = "Weitere Kameras wählen: unter „Übernehmen“ „Zusätzliche Kameras mitsenden“."
        else:
            note = ""
        self.SetString(ID_CAMERA_NOTE, note)

    @staticmethod
    def _camera_label(row: dict) -> str:
        label = row["label"][:60]
        if row["kind"] != "Kamera":
            label = f"{label} ({row['kind']})"
        return f"{label} — Renderansicht, geht immer mit" if row["render"] else label

    def _refresh_send(self, form, state, busy: bool) -> None:
        """Der feste Bereich: Wege, der Satz dazu, Knopf, Fortsetzen und Verwerfen."""
        controller = self.controller
        connected = state.connected
        self.SetBool(ID_SEND_IMAGE, form.send_image)
        self.SetBool(ID_MODEL, form.send_model)
        self._enable(ID_SEND_IMAGE, connected and not busy)
        self._enable(ID_MODEL, connected and not busy)
        self.SetBool(ID_SEND_CAMERAS, form.send_extra_cameras)
        self._enable(ID_SEND_CAMERAS, connected and not busy and form.send_model)
        self._lines(ID_SEND_SUMMARY, controller.send_summary(), SUMMARY_LINES)
        self.SetString(ID_CAPTURE, controller.send_label())
        # „Gerenderte Bilder übernehmen“ nur auf dem Weg über den Picture Viewer; aktiv, wenn ein passendes Ergebnis da ist.
        picture_viewer = controller.picture_viewer_way()
        if self._labels.get(ID_TAKE) != [str(picture_viewer)]:
            self.HideElement(ID_TAKE, not picture_viewer)
            self.LayoutChanged(ID_GRP_SEND)
            self._labels[ID_TAKE] = [str(picture_viewer)]
        pending = controller.pending() if connected else None
        if pending:
            self.SetString(ID_PENDING, f"Offene Übernahme vom {pending.get('createdAt', '')[:16].replace('T', ' ')} UTC")
        else:
            self.SetString(ID_PENDING, "")
        chosen = controller.chosen_ways()
        self._enable(ID_CAPTURE, connected and not busy and not pending and chosen is not None)
        self._enable(ID_TAKE, connected and not busy and not pending and controller.can_take_rendered())
        self._enable(ID_RESUME, connected and not busy and bool(pending))
        self._enable(ID_DISCARD, connected and not busy and bool(pending))

    # -- Ereignisse -----------------------------------------------------------

    def Timer(self, msg) -> None:
        self._guarded(self._follow_tab)
        if self._guarded(self.controller.pump):
            self.refresh()

    def CoreMessage(self, message_id, msg) -> bool:
        if message_id == c4d.EVMSG_CHANGE and self.controller.state.connected:
            self._guarded(self.refresh)
        return gui.GeDialog.CoreMessage(self, message_id, msg)

    def _guarded(self, action, *args):
        try:
            return action(*args)
        except Exception as error:  # nie als Traceback mit Pfaden in die Konsole
            log_exception("Unerwarteter Fehler im Dialog", error)
            self.controller.state.error = "Unerwarteter Fehler — Details in der Konsole (Debug-Schalter)."
            return None

    def Command(self, element_id, msg) -> bool:
        self._guarded(self._command, element_id)
        self._guarded(self.refresh)
        return True

    def _command(self, element_id: int) -> None:
        controller, form = self.controller, self.controller.form
        actions = {
            ID_CONNECT: controller.connect,
            ID_OPEN_LOGIN: controller.open_login,
            ID_CANCEL: controller.cancel,
            ID_SIGN_OUT: controller.sign_out,
            ID_REFRESH: controller.refresh,
            ID_NEW_PROJECT: controller.new_project,
            ID_CAPTURE: controller.capture,
            ID_TAKE: controller.take_rendered,
            ID_RESUME: controller.resume,
            ID_DISCARD: controller.discard,
            ID_OPEN_RESULT: controller.open_result,
            ID_MODEL_COUNT: controller.count_model,
        }
        if element_id in actions:
            actions[element_id]()
        elif element_id == ID_TABS:
            self._follow_tab()
        elif element_id == ID_SAVE_SETTINGS:
            controller.save_settings(self.GetString(ID_SERVER), self.GetString(ID_DEVICE), self.GetBool(ID_DEBUG))
        elif element_id == ID_PROJECT:
            controller.select_project(self._chosen(ID_PROJECT))
        elif element_id == ID_TARGET_MODE:
            form.target_mode = MODES[max(0, min(len(MODES) - 1, self.GetInt32(ID_TARGET_MODE)))]
        elif element_id == ID_VP_NAME:
            form.viewpoint_name = self.GetString(ID_VP_NAME)
        elif element_id == ID_VIEWPOINT:
            form.viewpoint_id = self._chosen(ID_VIEWPOINT)
        elif element_id == ID_FIT:
            form.fit_to_capture = self.GetBool(ID_FIT)
        elif element_id == ID_SIZE:
            form.size = SIZES[max(0, min(len(SIZES) - 1, self.GetInt32(ID_SIZE)))]
        elif element_id == ID_SEND_IMAGE:
            controller.set_send_image(self.GetBool(ID_SEND_IMAGE))
        elif element_id == ID_MODEL:
            controller.set_send_model(self.GetBool(ID_MODEL))
        elif element_id == ID_SEND_CAMERAS:
            controller.set_send_extra_cameras(self.GetBool(ID_SEND_CAMERAS))
        elif ID_CAMERA_FIRST <= element_id < ID_CAMERA_FIRST + MAX_CAMERAS:
            index = element_id - ID_CAMERA_FIRST
            if self._camera_rows and index < len(self._camera_rows):
                controller.set_extra_camera(self._camera_rows[index][0], self.GetBool(element_id))
        elif element_id == ID_KIND:
            form.capture_kind = KINDS[max(0, min(len(KINDS) - 1, self.GetInt32(ID_KIND)))]
        elif element_id == ID_BIT_DEPTH:
            index = self.GetInt32(ID_BIT_DEPTH)
            controller.set_data_pass_bit_depth(BIT_DEPTHS[index] if 0 <= index < len(BIT_DEPTHS) else None)
        elif element_id == ID_RESOLUTION:
            form.resolution = RESOLUTIONS[max(0, min(len(RESOLUTIONS) - 1, self.GetInt32(ID_RESOLUTION)))]
        else:
            for role, pass_id in ID_PASS.items():
                if element_id == pass_id:
                    if self.GetBool(pass_id):
                        form.passes.add(role)
                    else:
                        form.passes.discard(role)


def pass_notes(notes: list[tuple[str, str]]) -> str:
    """Die Hinweise zu den Pässen als ein Text — **gleiche Sätze nur einmal**.

    ``notes`` sind ``(Rolle, Satz)``; eine leere Rolle ist ein allgemeiner Satz. Gleiche Sätze werden zu
    „Normalen, Albedo: …“ zusammengefasst. Hinweise mit demselben ersten Satz teilen ihren Anfang bis zum letzten
    gemeinsamen Satz- oder Teilsatzende (etwa „Mit Corona nicht übertragbar. Für Datenpässe Renderer Standard oder
    Physical wählen.“) — er steht einmal. Was danach mit „dann …“ folgt, ist der nächste Schritt nach dem Wechsel
    und entfällt hier; nach dem Wechsel nennt der Tab ihn je Pass.
    """
    grouped: dict[str, list[str]] = {}
    for role, text in notes:
        if text:
            grouped.setdefault(text, []).append(PASS_LABELS.get(role, ""))
    clusters: dict[str, list[str]] = {}
    for text in grouped:
        clusters.setdefault(text.split(". ", 1)[0], []).append(text)
    parts: list[str] = []
    for texts in clusters.values():
        shared = _shared_start(texts)
        if shared:
            parts.append(shared)
        for text in texts:
            labels = ", ".join(label for label in grouped[text] if label)
            rest = text
            if shared:
                rest = text[len(shared) - 1:].lstrip(" .,")
                if not rest or rest.startswith("dann "):
                    continue
            parts.append(f"{labels}: {rest}" if labels else rest)
    return " ".join(parts)


def _shared_start(texts: list[str]) -> str:
    """Der gemeinsame Anfang mehrerer Sätze bis zum letzten gemeinsamen Satz- oder Teilsatzende — sonst leer.

    Das Ergebnis endet mit einem Punkt; ohne ihn (``[:-1]``) ist es ein Anfang jedes der Sätze.
    """
    if len(texts) < 2:
        return ""
    prefix = texts[0]
    for text in texts[1:]:
        while not text.startswith(prefix):
            prefix = prefix[:-1]
    # Endet der Anfang in jedem Satz an einem Satz- oder Teilsatzende („wählen." / „wählen, dann …"), gilt er ganz.
    if all(len(text) > len(prefix) and text[len(prefix)] in ".," for text in texts):
        cut = len(prefix)
    else:
        cut = max(prefix.rfind("."), prefix.rfind(","))
    if cut <= 0:
        return ""
    return prefix[:cut].rstrip() + "."
