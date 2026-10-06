"""Das Werkzeugfenster „rendertaxi.ai" — ein ``GeDialog`` mit vier Bereichen wie in Blender und Archicad.

1. **Verbindung** — Verbinden (Code und Adresse, der Browser öffnet sich),
   „Angemeldet als …", Abmelden; darunter die Einstellungen (Serveradresse,
   Gerätename, ausführliches Protokoll).
2. **Projekt und Blickpunkt** — Projekt, „Neues Projekt …" (Name erfragen,
   anlegen, gleich wählen), „Aktualisieren" (auch umbenannte Projekte); neuer
   Blickpunkt mit Namen — der Name des Kameraobjekts steht als Vorschlag
   darin, bis der Nutzer ihn ändert — oder bestehenden aktualisieren; „Rahmen an Aufnahme anpassen"
   (``frame: fit-to-capture``, sonst ``keep``); Rahmengröße Canvas-Vorgabe
   oder Render-Einstellung (``size``).
3. **Bild übernehmen** — Viewport oder Beauty mit optionalen Pässen; Größe
   aus dem Dokument oder dem Blickpunkt-Rahmen; „Bittiefe der Datenpässe"
   (8 Bit Standard oder 16 Bit, sofort gemerkt, Wortlaut aus dem gemeinsamen
   Client wie in Blender); „Modell mitsenden" (GLB und Kamera, Capture-Manifest
   1.2.0 bzw. 1.3.0; Standard aus) mit Größe und Grenzen vor dem Senden;
   Fortsetzen oder Verwerfen einer angefangenen Übernahme.
4. **Im Browser öffnen** — ``result.openUrl``.

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
from .controller import BEAUTY, RESOLUTION_DOCUMENT, RESOLUTION_VIEWPOINT, VIEWPORT, Controller, wrap
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
ID_GRP_TARGET = 1030
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
ID_GRP_CAPTURE = 1050
ID_KIND = 1051
ID_RESOLUTION = 1052
ID_CUT = 1053
ID_PASS = {"depth": 1060, "normal": 1061, "albedo": 1062, "object-id": 1063, "material-id": 1064}
ID_PASS_HINT = {"depth": 1065, "normal": 1066, "albedo": 1067, "object-id": 1069, "material-id": 1074}
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
LINES = 4
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

    def CreateLayout(self) -> bool:
        self.SetTitle(f"rendertaxi.ai {host.PLUGIN_VERSION}")
        self.GroupBegin(ID_MAIN, c4d.BFH_SCALEFIT | c4d.BFV_TOP, 1, 0, "")
        self.GroupBorderSpace(8, 8, 8, 8)

        self.GroupBegin(ID_GRP_CONNECTION, c4d.BFH_SCALEFIT, 1, 0, "Verbindung")
        self.GroupBorder(c4d.BORDER_GROUP_IN)
        self.GroupBorderSpace(6, 6, 6, 6)
        self._text(ID_STATUS)
        self._text(ID_CODE)
        self._text(ID_SERVER_TEXT)
        self.GroupBegin(0, c4d.BFH_SCALEFIT, 4, 1, "")
        self._gadgets[ID_CONNECT] = self.AddButton(ID_CONNECT, c4d.BFH_SCALEFIT, 0, 0, "Verbinden")
        self._gadgets[ID_OPEN_LOGIN] = self.AddButton(ID_OPEN_LOGIN, c4d.BFH_SCALEFIT, 0, 0, "Im Browser öffnen")
        self._gadgets[ID_CANCEL] = self.AddButton(ID_CANCEL, c4d.BFH_SCALEFIT, 0, 0, "Abbrechen")
        self._gadgets[ID_SIGN_OUT] = self.AddButton(ID_SIGN_OUT, c4d.BFH_SCALEFIT, 0, 0, "Abmelden")
        self.GroupEnd()
        self.GroupBegin(ID_GRP_SETTINGS, c4d.BFH_SCALEFIT, 2, 0, "Einstellungen")
        self._text(0, "Serveradresse")
        self._gadgets[ID_SERVER] = self.AddEditText(ID_SERVER, c4d.BFH_SCALEFIT, 300, 0)
        self._text(0, "Gerätename")
        self._gadgets[ID_DEVICE] = self.AddEditText(ID_DEVICE, c4d.BFH_SCALEFIT, 300, 0)
        self._text(0, "")
        self._gadgets[ID_DEBUG] = self.AddCheckbox(ID_DEBUG, c4d.BFH_SCALEFIT, 0, 0, "Ausführliches Protokoll (nie Pfade oder Namen)")
        self._text(0, "")
        self._gadgets[ID_SAVE_SETTINGS] = self.AddButton(ID_SAVE_SETTINGS, c4d.BFH_LEFT, 0, 0, "Einstellungen sichern")
        self.GroupEnd()
        self.GroupEnd()

        self.GroupBegin(ID_GRP_TARGET, c4d.BFH_SCALEFIT, 1, 0, "Projekt und Blickpunkt")
        self.GroupBorder(c4d.BORDER_GROUP_IN)
        self.GroupBorderSpace(6, 6, 6, 6)
        self.GroupBegin(0, c4d.BFH_SCALEFIT, 3, 1, "")
        self._combo(ID_PROJECT)
        self._gadgets[ID_NEW_PROJECT] = self.AddButton(ID_NEW_PROJECT, c4d.BFH_RIGHT, 0, 0, "Neues Projekt …")
        self._gadgets[ID_REFRESH] = self.AddButton(ID_REFRESH, c4d.BFH_RIGHT, 0, 0, "Aktualisieren")
        self.GroupEnd()
        self._combo(ID_TARGET_MODE, MODE_LABELS)
        self._gadgets[ID_VP_NAME] = self.AddEditText(ID_VP_NAME, c4d.BFH_SCALEFIT, 300, 0)
        self._combo(ID_VIEWPOINT)
        self._gadgets[ID_FIT] = self.AddCheckbox(ID_FIT, c4d.BFH_SCALEFIT, 0, 0, "Rahmen an Aufnahme anpassen")
        self._text(0, "Rahmengröße")
        self._combo(ID_SIZE, SIZE_LABELS)
        self._text(ID_SIZE_TEXT)
        for line in range(LINES):
            self._text(ID_HINT + line)
        self.GroupEnd()

        self.GroupBegin(ID_GRP_CAPTURE, c4d.BFH_SCALEFIT, 1, 0, "Bild übernehmen")
        self.GroupBorder(c4d.BORDER_GROUP_IN)
        self.GroupBorderSpace(6, 6, 6, 6)
        self._combo(ID_KIND, KIND_LABELS)
        self._text(0, "Größe")
        self._combo(ID_RESOLUTION, RESOLUTION_LABELS)
        self._text(ID_CUT)
        self._text(0, "Pässe (optional, nur Beauty)")
        for spec_role, label in (("depth", "Tiefe (Depth)"), ("normal", "Normalen"), ("albedo", "Albedo"),
                                 ("object-id", "Objekt-ID"), ("material-id", "Material-ID")):
            self._gadgets[ID_PASS[spec_role]] = self.AddCheckbox(ID_PASS[spec_role], c4d.BFH_SCALEFIT, 0, 0, label)
            self._text(ID_PASS_HINT[spec_role])
        self._text(0, mf.DATA_PASS_BIT_DEPTH_LABEL)
        self._combo(ID_BIT_DEPTH, BIT_DEPTH_LABELS)
        self.GroupBegin(0, c4d.BFH_SCALEFIT, 2, 1, "")
        self._gadgets[ID_MODEL] = self.AddCheckbox(ID_MODEL, c4d.BFH_SCALEFIT, 0, 0,
                                                   "Modell mitsenden (GLB und Kamera)")
        self._gadgets[ID_MODEL_COUNT] = self.AddButton(ID_MODEL_COUNT, c4d.BFH_RIGHT, 0, 0, "Neu zählen")
        self.GroupEnd()
        for line in range(LINES):
            self._text(ID_MODEL_HINT + line)
        self._text(ID_PENDING)
        self.GroupBegin(0, c4d.BFH_SCALEFIT, 3, 1, "")
        self._gadgets[ID_CAPTURE] = self.AddButton(ID_CAPTURE, c4d.BFH_SCALEFIT, 0, 0, "Aufnehmen und übernehmen")
        self._gadgets[ID_RESUME] = self.AddButton(ID_RESUME, c4d.BFH_SCALEFIT, 0, 0, "Übernahme fortsetzen")
        self._gadgets[ID_DISCARD] = self.AddButton(ID_DISCARD, c4d.BFH_SCALEFIT, 0, 0, "Angefangene verwerfen")
        self.GroupEnd()
        self.GroupEnd()

        self._text(ID_PROGRESS)
        for line in range(LINES):
            self._text(ID_MESSAGE + line)
        self.GroupBegin(0, c4d.BFH_SCALEFIT, 2, 1, "")
        self._gadgets[ID_OPEN_RESULT] = self.AddButton(ID_OPEN_RESULT, c4d.BFH_LEFT, 0, 0, "Blickpunkt im Browser öffnen")
        self._text(ID_RESULT)
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
        self.SetTimer(250)
        self._guarded(self.refresh)
        return True

    # -- Anzeige ----------------------------------------------------------------

    def _lines(self, first_id: int, text: str) -> None:
        for offset, line in enumerate(wrap(text, WIDTH, LINES)):
            self.SetString(first_id + offset, line)

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

        visible = state.connected
        if self._layout_state != (visible,):
            self.HideElement(ID_GRP_TARGET, not visible)
            self.HideElement(ID_GRP_CAPTURE, not visible)
            self.LayoutChanged(ID_MAIN)
            self._layout_state = (visible,)

        if visible:
            self._refresh_target(form, state, busy)
            self._refresh_capture(form, busy)

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
        self.SetString(ID_RESULT, "Aufnahme übernommen." if has_result else "")

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
        for role, element_id in ID_PASS.items():
            state, hint, spec = rows.get(role, ("unknown", "", None))
            usable = form.capture_kind == BEAUTY and state == "available"
            self._enable(element_id, usable)
            self.SetBool(element_id, usable and role in form.passes)
            if spec is None:
                note = "In diesem Cinema 4D nicht erkannt."
            elif state != "available":
                note = hint
            elif spec.blocked_by:
                note = spec.blocked_note or "Nicht übertragbar."
            elif no_png:
                note = "Dieser Server nimmt noch keine Pässe an; übertragen wird das Bild."
            elif role == "depth":
                note = f"PNG {bit_depth} Bit, Tiefe von nah (0) bis fern (1)"
            elif role == "object-id":
                note = f"PNG {bit_depth} Bit, je Pixel die ID des Objektpuffers"
            else:
                note = f"PNG {bit_depth} Bit, linear"
            self.SetString(ID_PASS_HINT[role], note[:120])
        self.SetBool(ID_MODEL, form.send_model)
        self._enable(ID_MODEL, not busy)
        self._enable(ID_MODEL_COUNT, not busy and form.send_model)
        self._lines(ID_MODEL_HINT, controller.model_hint())
        pending = controller.pending()
        if pending:
            self.SetString(ID_PENDING, f"Offene Übernahme vom {pending.get('createdAt', '')[:16].replace('T', ' ')} UTC")
        else:
            self.SetString(ID_PENDING, "")
        self._enable(ID_CAPTURE, not busy and not pending)
        self._enable(ID_RESUME, not busy and bool(pending))
        self._enable(ID_DISCARD, not busy and bool(pending))

    # -- Ereignisse -----------------------------------------------------------

    def Timer(self, msg) -> None:
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
            ID_RESUME: controller.resume,
            ID_DISCARD: controller.discard,
            ID_OPEN_RESULT: controller.open_result,
            ID_MODEL_COUNT: controller.count_model,
        }
        if element_id in actions:
            actions[element_id]()
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
        elif element_id == ID_MODEL:
            controller.set_send_model(self.GetBool(ID_MODEL))
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
