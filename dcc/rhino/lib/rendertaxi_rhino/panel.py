"""Das Fenster von rdtx.ai in Rhino — Eto.Forms, plattformübergreifend (Windows und macOS).

Ein nicht modales ``Eto.Forms.Form`` mit dem Hauptfenster von Rhino als Besitzer (wie das gisloader-Plugin,
das in Rhino 8 auf beiden Systemen läuft). Aufbau wie das Blender-Panel: Verbindung, Projekt, Blickpunkt,
Bild, Modell, dann fest „Übernehmen"; im Fuß Version und Build-Kennung.

Das Fenster zeichnet nur; jede Handlung geht an den ``Controller``. Listen wählen über Kennungen (``Key``),
nie über Positionen. Jeder Klick läuft über ``_guarded``: eine Ausnahme wird ein Satz im Fenster, kein
Traceback. pythonnet in Rhino 8 setzt keine Eigenschaften im Konstruktor — erst erzeugen, dann setzen.
"""

from __future__ import annotations

import Eto.Drawing as drawing
import Eto.Forms as forms

from . import host
from .controller import BEAUTY, CURRENT_VIEW, RESOLUTION_DOCUMENT, RESOLUTION_VIEWPOINT, VIEWPORT, Controller
from .rendertaxi_client import frame, ways
from .rendertaxi_client import manifest as mf
from .rendertaxi_client.log import log_exception

NO_PROJECT = "— Projekt wählen —"
NO_VIEWPOINT = "— Blickpunkt wählen —"
WIDTH = 380


def _label(text: str = "", wrap: bool = True):
    label = forms.Label()
    label.Text = text
    if wrap:
        label.Wrap = forms.WrapMode.Word
    return label


def _button(text: str, handler):
    button = forms.Button()
    button.Text = text
    button.Click += handler
    return button


def _check(text: str, handler):
    box = forms.CheckBox()
    box.Text = text
    box.CheckedChanged += handler
    return box


def _stack(*controls, horizontal: bool = False):
    stack = forms.StackLayout()
    stack.Orientation = forms.Orientation.Horizontal if horizontal else forms.Orientation.Vertical
    stack.Spacing = 6
    if not horizontal:
        stack.HorizontalContentAlignment = forms.HorizontalAlignment.Stretch
    for control in controls:
        stack.Items.Add(forms.StackLayoutItem(control, False))
    return stack


def _group(title: str, *controls):
    group = forms.GroupBox()
    group.Text = title
    group.Padding = drawing.Padding(8)
    group.Content = _stack(*controls)
    return group


def _fill(dropdown, items, selected) -> None:
    """Einträge ``(Kennung, Text)`` setzen und die Wahl über die Kennung halten."""
    current = [(item.Key, item.Text) for item in dropdown.Items]
    if current != list(items):
        dropdown.Items.Clear()
        for key, text in items:
            entry = forms.ListItem()
            entry.Key = key
            entry.Text = text
            dropdown.Items.Add(entry)
    if dropdown.SelectedKey != selected:
        dropdown.SelectedKey = selected


class RdtxForm(forms.Form):
    def __init__(self, controller: Controller):
        super().__init__()
        self.controller = controller
        self._updating = False
        self._settings_shown = False
        self.Title = f"{host.MARK} {host.PLUGIN_VERSION}"
        self.Padding = drawing.Padding(10)
        self.Resizable = True
        self.MinimumSize = drawing.Size(WIDTH, 420)
        self._build()
        self.timer = forms.UITimer()
        self.timer.Interval = 0.2
        self.timer.Elapsed += self._tick
        self.Closed += self._closed
        self.refresh()
        self.timer.Start()

    # -- Aufbau -----------------------------------------------------------

    def _build(self) -> None:
        c = self.controller
        # Verbindung
        self.status = _label("Nicht verbunden")
        self.code = _label("")
        self.connect = _button("Verbinden", self._guarded(c.connect))
        self.open_login = _button("Anmeldeseite öffnen", self._guarded(c.open_login))
        self.cancel_login = _button("Abbrechen", self._guarded(c.cancel))
        self.sign_out = _button("Abmelden", self._guarded(c.sign_out))
        self.server = forms.TextBox()
        self.device = forms.TextBox()
        self.device.PlaceholderText = "Gerätename (optional)"
        self.debug = _check("Ausführliches Protokoll", lambda *_: None)
        self.save = _button("Einstellungen sichern", self._guarded(self._save_settings))
        self.connection = _group("Verbindung", self.status, self.code, _stack(self.open_login, self.cancel_login,
                                 horizontal=True), _stack(self.connect, self.sign_out, horizontal=True),
                                 _label("Server"), self.server, self.device, self.debug, self.save)
        # Projekt
        self.project = forms.DropDown()
        self.project.SelectedIndexChanged += self._on_project
        self.refresh_button = _button("Aktualisieren", self._guarded(c.refresh))
        self.create_project = _button("Projekt anlegen …", self._guarded(c.new_project))
        self.project_group = _group("Projekt", self.project, _stack(self.refresh_button, self.create_project,
                                    horizontal=True))
        # Blickpunkt
        self.view = forms.DropDown()
        self.view.SelectedIndexChanged += self._on_view
        self.target_mode = forms.DropDown()
        _fill(self.target_mode, [(frame.CREATE, "Neuer Blickpunkt"), (frame.UPDATE, "Bestehenden aktualisieren")],
              frame.CREATE)
        self.target_mode.SelectedIndexChanged += self._on_target_mode
        self.viewpoint_name = forms.TextBox()
        self.viewpoint_name.PlaceholderText = "Name des Blickpunkts"
        self.viewpoint_name.TextChanged += self._on_name
        self.viewpoint = forms.DropDown()
        self.viewpoint.SelectedIndexChanged += self._on_viewpoint
        self.fit = _check("Rahmen an Aufnahme anpassen", self._on_fit)
        self.size = forms.DropDown()
        _fill(self.size, [(frame.SIZE_CANVAS_DEFAULT, "Rahmengröße: Vorgabe der Leinwand"),
                          (frame.SIZE_CAPTURE, "Rahmengröße: wie die Aufnahme")], frame.SIZE_CANVAS_DEFAULT)
        self.size.SelectedIndexChanged += self._on_size
        self.size_text = _label("")
        self.aspect = _label("")
        self.viewpoint_group = _group("Blickpunkt", _label("Ansicht"), self.view, self.target_mode,
                                      self.viewpoint_name, self.viewpoint, self.fit, self.size, self.size_text,
                                      self.aspect)
        # Bild
        self.send_image = _check("Bild senden", self._on_send_image)
        self.kind = forms.DropDown()
        _fill(self.kind, [(VIEWPORT, "Ansicht (wie im Viewport)"), (BEAUTY, "Rendering (Rhino Render)")], VIEWPORT)
        self.kind.SelectedIndexChanged += self._on_kind
        self.resolution = forms.DropDown()
        _fill(self.resolution, [(RESOLUTION_DOCUMENT, "Größe aus den Rendereinstellungen"),
                                (RESOLUTION_VIEWPOINT, "Größe des Blickpunkt-Rahmens")], RESOLUTION_DOCUMENT)
        self.resolution.SelectedIndexChanged += self._on_resolution
        self.frame_text = _label("")
        self.pass_checks = {}
        pass_rows = []
        for role, title in (("depth", "Tiefe"), ("normal", "Normalen"), ("albedo", "Albedo"),
                            ("object-id", "Objekt-ID"), ("material-id", "Material-ID")):
            box = _check(title, self._on_pass)
            box.Tag = role
            self.pass_checks[role] = box
            pass_rows.append(box)
        self.pass_hint = _label("")
        self.bit_depth = forms.DropDown()
        _fill(self.bit_depth, [(str(value), text) for value, text in mf.DATA_PASS_BIT_DEPTH_OPTIONS],
              str(mf.DEFAULT_DATA_PASS_BIT_DEPTH))
        self.bit_depth.SelectedIndexChanged += self._on_bit_depth
        self.passes = _stack(_label("Pässe (optional)"), *pass_rows, self.pass_hint,
                             _label(mf.DATA_PASS_BIT_DEPTH_LABEL), self.bit_depth)
        self.image_group = _group("Bild", self.send_image, self.kind, self.resolution, self.frame_text, self.passes)
        # Modell (RTX-RH-003)
        self.send_model = _check("Modell senden", self._on_send_model)
        self.model_colors = _check("Materialfarben mitsenden", self._on_model_colors)
        self.model_hint = _label("")
        self.count = _button("Neu zählen", self._guarded(c.count_model))
        self.model_group = _group("Modell", self.send_model, self.model_colors, self.model_hint, self.count)
        # Übernehmen
        self.summary = _label("")
        self.capture = _button("Bild übernehmen", self._guarded(c.capture))
        self.resume = _button("Fortsetzen", self._guarded(c.resume))
        self.discard = _button("Verwerfen", self._guarded(c.discard))
        self.pending_text = _label("")
        self.progress = forms.ProgressBar()
        self.progress.MinValue = 0
        self.progress.MaxValue = 100
        self.progress_text = _label("")
        self.cancel = _button("Abbrechen", self._guarded(c.cancel))
        self.message = _label("")
        self.open_result = _button("Im Browser öffnen", self._guarded(c.open_result))
        self.transfer_group = _group("Übernehmen", self.summary, self.capture, self.pending_text,
                                     _stack(self.resume, self.discard, horizontal=True), self.progress,
                                     self.progress_text, self.cancel, self.message, self.open_result)
        # Fuß
        self.footer = _label(f"{host.MARK} {host.PLUGIN_VERSION} · {host.build_text()}")
        self.about = _button("Über …", self._guarded(self._show_about))

        body = _stack(self.connection, self.project_group, self.viewpoint_group, self.image_group,
                      self.model_group, self.transfer_group, _stack(self.footer, self.about))
        scroll = forms.Scrollable()
        scroll.Content = body
        self.Content = scroll

    # -- Ereignisse --------------------------------------------------------

    def _guarded(self, action):
        def handler(*_args):
            try:
                action()
            except Exception as error:  # noqa: BLE001 — nie ein Traceback im Fenster
                log_exception("Fehler im Fenster", error)
                self.controller._set_error(str(error) or "Unerwarteter Fehler — Einzelheiten stehen im Protokoll.")
            self.refresh()
        return handler

    def _changed(self, action):
        """Ein Listenwechsel des Nutzers — nicht der, den ``refresh`` beim Füllen auslöst."""
        if self._updating:
            return
        self._guarded(action)()

    def _on_project(self, *_):
        self._changed(lambda: self.controller.select_project(self.project.SelectedKey or None))

    def _on_view(self, *_):
        self._changed(lambda: self.controller.select_view(self.view.SelectedKey or CURRENT_VIEW))

    def _on_target_mode(self, *_):
        self._changed(lambda: setattr(self.controller.form, "target_mode", self.target_mode.SelectedKey))

    def _on_name(self, *_):
        if not self._updating:
            self.controller.form.viewpoint_name = self.viewpoint_name.Text or ""

    def _on_viewpoint(self, *_):
        self._changed(lambda: setattr(self.controller.form, "viewpoint_id", self.viewpoint.SelectedKey or None))

    def _on_fit(self, *_):
        self._changed(lambda: setattr(self.controller.form, "fit_to_capture", bool(self.fit.Checked)))

    def _on_size(self, *_):
        self._changed(lambda: setattr(self.controller.form, "size", self.size.SelectedKey))

    def _on_send_image(self, *_):
        self._changed(lambda: self.controller.set_send_image(bool(self.send_image.Checked)))

    def _on_send_model(self, *_):
        self._changed(lambda: self.controller.set_send_model(bool(self.send_model.Checked)))

    def _on_model_colors(self, *_):
        self._changed(lambda: self.controller.set_model_colors(bool(self.model_colors.Checked)))

    def _on_kind(self, *_):
        self._changed(lambda: setattr(self.controller.form, "capture_kind", self.kind.SelectedKey))

    def _on_resolution(self, *_):
        self._changed(lambda: setattr(self.controller.form, "resolution", self.resolution.SelectedKey))

    def _on_pass(self, sender, *_):
        def apply():
            role = sender.Tag
            if sender.Checked:
                self.controller.form.passes.add(role)
            else:
                self.controller.form.passes.discard(role)
        self._changed(apply)

    def _on_bit_depth(self, *_):
        self._changed(lambda: self.controller.set_data_pass_bit_depth(int(self.bit_depth.SelectedKey or 8)))

    def _save_settings(self) -> None:
        self.controller.save_settings(self.server.Text or "", self.device.Text or "", bool(self.debug.Checked))
        self._settings_shown = False

    def _show_about(self) -> None:
        c = self.controller
        try:
            rhino = c.adapter.host_version()
        except Exception:  # noqa: BLE001
            rhino = "unbekannt"
        lines = [f"{host.MARK} für Rhino", f"Fassung {host.PLUGIN_VERSION}", host.build_text(),
                 f"Kennung {host.CLIENT_ID}", f"Server {c.state.server or '—'}", f"Rhino {rhino}"]
        forms.MessageBox.Show(self, "\n".join(lines), f"Über {host.MARK}")

    def _tick(self, *_):
        try:
            if self.controller.pump():
                self.refresh()
            elif self.controller.state.job is not None:
                self.refresh()
        except Exception as error:  # noqa: BLE001
            log_exception("Fehler im Zeitgeber", error)

    def _closed(self, *_):
        try:
            self.timer.Stop()
            self.controller.shutdown()
        except Exception:  # noqa: BLE001
            pass

    # -- Zeichnen ----------------------------------------------------------

    def refresh(self) -> None:
        self._updating = True
        try:
            self._refresh()
        except Exception as error:  # noqa: BLE001 — Regel 3: ein Fehler beim Zeichnen schließt das Fenster nicht
            log_exception("Fehler beim Zeichnen", error)
        finally:
            self._updating = False

    def _refresh(self) -> None:
        c, state, form = self.controller, self.controller.state, self.controller.form
        busy = state.job is not None
        if not self._settings_shown:
            # Nur beim Öffnen und nach „Einstellungen sichern" — sonst überschriebe das Zeichnen eine Eingabe.
            settings = c.settings()
            self.server.Text = settings.get("serverUrl") or ""
            self.device.Text = settings.get("deviceName") or ""
            self.debug.Checked = bool(settings.get("debugLogging"))
            self._settings_shown = True

        # Verbindung
        if state.code:
            self.status.Text = "Anmeldung im Browser bestätigen"
            self.code.Text = f"Code {state.code.get('userCode', '')} — {state.code.get('verificationUri', '')}"
        elif state.connected and state.identity:
            who = state.identity.get("user") or state.identity.get("email") or ""
            self.status.Text = f"Angemeldet als {who}"
            self.code.Text = state.identity.get("organization") or ""
        else:
            self.status.Text = "Nicht verbunden"
            self.code.Text = state.server or ""
        self.open_login.Visible = self.cancel_login.Visible = bool(state.code)
        self.connect.Visible = not state.connected and not state.code
        self.connect.Enabled = not busy
        self.sign_out.Visible = state.connected
        self.sign_out.Enabled = not busy
        self.save.Enabled = not busy

        connected = state.connected
        for group in (self.project_group, self.viewpoint_group, self.image_group, self.model_group,
                      self.transfer_group):
            group.Visible = connected
        if not connected:
            self._messages(state)
            self.transfer_group.Visible = bool(state.message or state.error)
            return

        # Projekt
        _fill(self.project, [("", NO_PROJECT)] + list(state.projects), form.project_id or "")
        self.refresh_button.Enabled = self.create_project.Enabled = not busy

        # Blickpunkt
        _fill(self.view, c.views(), form.view_key)
        c.suggest_viewpoint_name()
        _fill(self.target_mode, [(frame.CREATE, "Neuer Blickpunkt"), (frame.UPDATE, "Bestehenden aktualisieren")],
              form.target_mode)
        creating = form.target_mode == frame.CREATE
        self.viewpoint_name.Visible = creating
        if creating and self.viewpoint_name.Text != form.viewpoint_name:
            self.viewpoint_name.Text = form.viewpoint_name
        viewpoints = state.viewpoints.get(form.project_id or "", [])
        _fill(self.viewpoint, [("", NO_VIEWPOINT)] + list(viewpoints), form.viewpoint_id or "")
        self.viewpoint.Visible = self.fit.Visible = not creating
        self.fit.Checked = form.fit_to_capture
        _fill(self.size, [(frame.SIZE_CANVAS_DEFAULT, "Rahmengröße: Vorgabe der Leinwand"),
                          (frame.SIZE_CAPTURE, "Rahmengröße: wie die Aufnahme")], form.size)
        self.size_text.Text = c.size_text()
        hint = c.aspect_hint()
        self.aspect.Text = hint or ""
        self.aspect.Visible = bool(hint)

        # Bild
        self.send_image.Checked = form.send_image
        _fill(self.kind, [(VIEWPORT, "Ansicht (wie im Viewport)"), (BEAUTY, "Rendering (Rhino Render)")],
              form.capture_kind)
        self.resolution.Visible = form.target_mode == frame.UPDATE
        _fill(self.resolution, [(RESOLUTION_DOCUMENT, "Größe aus den Rendereinstellungen"),
                                (RESOLUTION_VIEWPOINT, "Größe des Blickpunkt-Rahmens")], form.resolution)
        try:
            width, height = c.capture_size() or c.document_size()
            self.frame_text.Text = f"Ausschnitt {width} × {height}"
        except Exception:  # noqa: BLE001 — ohne Dokument keine Größe
            self.frame_text.Text = "Kein Rhino-Dokument geöffnet."
        beauty = form.capture_kind == BEAUTY
        self.passes.Visible = beauty
        if beauty:
            hints = []
            for spec, pass_state, pass_hint in c.adapter.pass_rows():
                box = self.pass_checks.get(spec.role)
                if box is None:
                    continue
                box.Enabled = pass_state == "available"
                box.Checked = spec.role in form.passes and pass_state == "available"
                if pass_state != "available" and pass_hint and pass_hint not in hints:
                    hints.append(pass_hint)
            allowed = ((state.handshake or {}).get("limits") or {}).get("allowedMediaTypes")
            if allowed is not None and mf.PNG_MEDIA_TYPE not in allowed:
                hints.append("Dieser Server nimmt noch keine Pässe an; übertragen wird das Bild.")
            self.pass_hint.Text = " ".join(hints)
            self.pass_hint.Visible = bool(hints)
            _fill(self.bit_depth, [(str(value), text) for value, text in mf.DATA_PASS_BIT_DEPTH_OPTIONS],
                  str(c.data_pass_bit_depth()))

        # Modell
        self.send_model.Checked = form.send_model
        self.model_colors.Checked = form.model_colors
        self.model_colors.Visible = self.count.Visible = form.send_model
        self.count.Enabled = not busy
        model_hint = c.model_hint()
        self.model_hint.Text = model_hint
        self.model_hint.Visible = bool(model_hint)

        # Übernehmen
        try:
            chosen = c.chosen()
            self.summary.Text = ways.summary(chosen) + (f" {chosen.hint}" if chosen.hint else "")
            self.capture.Text = f"{ways.label(chosen)} übernehmen"
            can_capture = True
        except ValueError as error:
            self.summary.Text = str(error)
            self.capture.Text = "Übernehmen"
            can_capture = False
        pending = c.pending()
        self.capture.Visible = not pending
        self.capture.Enabled = can_capture and not busy
        self.pending_text.Visible = self.resume.Visible = self.discard.Visible = bool(pending)
        if pending:
            created = (pending.get("createdAt") or "")[:16].replace("T", " ")
            self.pending_text.Text = f"Offene Übernahme vom {created} UTC"
            self.resume.Enabled = self.discard.Enabled = not busy
        self._messages(state)
        self.open_result.Visible = bool(state.result and state.result.get("openUrl"))

    def _messages(self, state) -> None:
        if state.progress:
            text, percent = state.progress
            self.progress.Visible = self.progress_text.Visible = True
            self.progress.Value = max(0, min(100, int(percent)))
            self.progress_text.Text = f"{text} ({percent} %)"
        else:
            self.progress.Visible = False
            self.progress_text.Visible = state.job is not None
            self.progress_text.Text = "Bitte warten …" if state.job is not None else ""
        self.cancel.Visible = state.job is not None and state.job.label in ("transfer", "connect")
        self.message.Text = state.error or state.message or ""
        self.message.TextColor = drawing.Colors.Red if state.error else drawing.SystemColors.ControlText
