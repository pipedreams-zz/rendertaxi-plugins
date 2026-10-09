"""Die Palette von rdtx.ai in Rhino — Eto.Forms, plattformübergreifend (Windows und macOS).

``RdtxView`` baut den Inhalt in ein ``Eto.Forms.Panel``: das Rhino-Panel, das ``dock.py`` registriert (Rhino
erzeugt es, je Dokumentfenster eines), oder — geht das nicht — ein schwebendes Fenster (``RdtxWindow``).
Aufbau wie Rhinos eigene Paletten („Eigenschaften"): Abschnitte mit Überschrift, darin Zeilen mit Beschriftung
links und Wert rechts, Bedienelemente in Rhinos Größe (macOS: 11 pt, Steuerelementgröße „Small", gemessen an
„Eigenschaften" in Rhino 8.35). Bereiche: Verbindung (Einstellungen aufklappbar), Projekt, Blickpunkt, Bild,
Modell, Übernehmen; im Fuß Version und Build-Kennung. Lange Sätze stehen im Tooltip, die Zeile ist kurz.

Die Palette zeichnet nur; jede Handlung geht an den ``Controller``. Listen wählen über Kennungen (``Key``),
nie über Positionen. Jeder Klick läuft über ``_guarded``: eine Ausnahme wird ein Satz in der Palette, kein
Traceback. pythonnet in Rhino 8 setzt keine Eigenschaften im Konstruktor — erst erzeugen, dann setzen.
"""

from __future__ import annotations

import sys

import Eto.Drawing as drawing
import Eto.Forms as forms

from . import host
from .controller import (BEAUTY, CURRENT_VIEW, MAX_TRIANGLES, RESOLUTION_DOCUMENT, RESOLUTION_VIEWPOINT, VIEWPORT,
                         Controller)
from .rendertaxi_client import frame, ways
from .rendertaxi_client import manifest as mf
from .rendertaxi_client.log import log_exception

NO_PROJECT = "— Projekt wählen —"
NO_VIEWPOINT = "— Blickpunkt wählen —"
WIDTH = 380
# Rhinos Paletten unter macOS: Schrift 11 pt, Steuerelemente „Small" (Dropdown 22 px, Textfeld 19 px). Unter
# Windows ist die Standardschrift von Eto schon die von Rhino; dort bleibt sie.
FONT_SIZE = 11 if sys.platform == "darwin" else None
LABEL_WIDTH = 82
ROW_SPACING = 4
SECTION_SPACING = 10
PADDING = 8
PASSES = (("depth", "Tiefe"), ("normal", "Normalen"), ("albedo", "Albedo"), ("object-id", "Objekt-ID"),
          ("material-id", "Material-ID"))
TARGETS = [(frame.CREATE, "Neuer Blickpunkt"), (frame.UPDATE, "Bestehenden aktualisieren")]
SIZES = [(frame.SIZE_CANVAS_DEFAULT, "Vorgabe der Leinwand"), (frame.SIZE_CAPTURE, "wie die Aufnahme")]
KINDS = [(VIEWPORT, "Ansicht (wie im Viewport)"), (BEAUTY, "Rendering (Rhino Render)")]
RESOLUTIONS = [(RESOLUTION_DOCUMENT, "Rendereinstellungen"), (RESOLUTION_VIEWPOINT, "Größe des Blickpunkt-Rahmens")]


def _font(control, bold: bool = False):
    """Schrift in Rhino-Größe; ein Fehler lässt die Standardschrift stehen (Regel 3)."""
    if FONT_SIZE is None and not bold:
        return control
    try:
        size = FONT_SIZE or drawing.SystemFonts.Default().Size
        control.Font = drawing.SystemFonts.Bold(size) if bold else drawing.SystemFonts.Default(size)
    except Exception:  # noqa: BLE001
        pass
    return control


# Jede Beschriftung mit ihrem Umbruch: Rhino setzt beim Laden eines Panels für ``Label`` rechtsbündig und
# Wortumbruch (gemessen in Rhino 8.35, macOS) — ``_align`` stellt beides nach dem Laden wieder her.
_LABELS: list = []


def _align(label, wrap: bool) -> None:
    # pythonnet nennt den Enum-Wert „None" ``NONE``.
    mode = forms.WrapMode.Word if wrap else getattr(forms.WrapMode, "NONE", None)
    if mode is not None and label.Wrap != mode:
        label.Wrap = mode
    if label.TextAlignment != forms.TextAlignment.Left:
        label.TextAlignment = forms.TextAlignment.Left


def _label(text: str = "", wrap: bool = True, bold: bool = False):
    label = forms.Label()
    label.Text = text
    label.VerticalAlignment = forms.VerticalAlignment.Center
    _align(label, wrap)
    _LABELS.append((label, wrap))
    return _font(label, bold)


def _button(text: str, handler):
    button = forms.Button()
    button.Text = text
    button.Click += handler
    return _font(button)


def _check(text: str, handler):
    box = forms.CheckBox()
    box.Text = text
    box.CheckedChanged += handler
    return _font(box)


def _dropdown(handler=None):
    dropdown = _font(forms.DropDown())
    if handler is not None:
        dropdown.SelectedIndexChanged += handler
    return dropdown


def _textbox(placeholder: str = ""):
    box = _font(forms.TextBox())
    box.PlaceholderText = placeholder
    return box


def _stack(*controls, horizontal: bool = False, spacing: int = ROW_SPACING):
    stack = forms.StackLayout()
    stack.Orientation = forms.Orientation.Horizontal if horizontal else forms.Orientation.Vertical
    stack.Spacing = spacing
    if not horizontal:
        stack.HorizontalContentAlignment = forms.HorizontalAlignment.Stretch
    else:
        stack.VerticalContentAlignment = forms.VerticalAlignment.Center
    for control in controls:
        stack.Items.Add(forms.StackLayoutItem(control, False))
    return stack


def _cell(control, scale: bool):
    cell = forms.TableCell()
    cell.Control = control
    cell.ScaleWidth = scale
    return cell


def _row(title: str, *controls):
    """Eine Zeile wie in „Eigenschaften": Beschriftung links (feste Breite), Werte rechts (füllen die Breite).

    Eine eigene ``TableLayout`` je Zeile: so verschwindet mit ``Visible = False`` die ganze Zeile.
    """
    caption = _label(title, wrap=False)
    caption.Width = LABEL_WIDTH
    row = forms.TableRow()
    row.Cells.Add(_cell(caption, False))
    for index, control in enumerate(controls):
        row.Cells.Add(_cell(control, index == 0))
    table = forms.TableLayout()
    table.Spacing = drawing.Size(6, 0)
    table.Rows.Add(row)
    return table


def _grid(controls, columns: int = 2):
    """Kontrollkästchen nebeneinander — die Pässe in zwei Spalten statt fünf Zeilen."""
    table = forms.TableLayout()
    table.Spacing = drawing.Size(8, 2)
    for start in range(0, len(controls), columns):
        row = forms.TableRow()
        for index in range(columns):
            control = controls[start + index] if start + index < len(controls) else forms.Label()
            row.Cells.Add(_cell(control, True))
        table.Rows.Add(row)
    return table


class Section:
    """Ein senkrechter Stapel, der nur seine sichtbaren Teile trägt — eine ausgeblendete Zeile hinterlässt so
    keinen Abstand (Eto rechnet den Abstand auch für unsichtbare Einträge, gemessen in Rhino 8.35)."""

    def __init__(self, controls, spacing: int = ROW_SPACING):
        self.controls = list(controls)
        self.stack = _stack(*self.controls, spacing=spacing)
        self._shown = list(self.controls)

    def relayout(self) -> None:
        shown = [control for control in self.controls if control.Visible]
        if shown == self._shown:
            return
        self.stack.Items.Clear()
        for control in shown:
            self.stack.Items.Add(forms.StackLayoutItem(control, False))
        self._shown = shown


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


def size_line(c: Controller) -> str:
    """Die Zielgröße in einer kurzen Zeile („Zielgröße 16:9, <lange Kante> px"); der ganze Satz steht im Tooltip."""
    form = c.form
    known = frame.canvas_default(c.state.handshake)
    if form.size == frame.SIZE_CAPTURE:
        if form.target_mode != frame.CREATE and not form.fit_to_capture:
            return "Zielgröße wie Aufnahme — nur mit Anpassen"
        return f"Zielgröße wie Aufnahme, ab {known[1]} px" if known else "Zielgröße wie Aufnahme"
    return f"Zielgröße {known[0]}, {known[1]} px" if known else "Zielgröße: Vorgabe der Leinwand"


def model_line(c: Controller) -> str:
    """Der Modellhinweis in einer Zeile — Dreiecke, Objekte, Dateigröße; Einzelheiten im Tooltip."""
    problem = c.model_problem()
    if problem:
        return problem
    estimate = c.model_estimate
    cap = ((c.state.handshake or {}).get("limits") or {}).get("maxGeometryBytes")
    limit = f", bis {cap / 1048576:.0f} MB" if cap else ""
    if estimate is None:
        return f"Noch nicht gezählt{limit}"
    triangles = f"{estimate.triangles:,}".replace(",", " ")
    text = f"≈ {triangles} Dreiecke, {estimate.objects} Objekte{limit}"
    return ("Zu groß: " if estimate.triangles > MAX_TRIANGLES else "") + text


class RdtxView:
    """Der Inhalt der Palette in einem ``Eto.Forms.Panel`` — ``container`` ist das Panel von Rhino."""

    def __init__(self, controller: Controller, container=None, peers=None):
        self.controller = controller
        self.container = container if container is not None else forms.Panel()
        self._peers = peers or (lambda: [self])
        self._updating = False
        self._settings_shown = False
        self._connected_shown = None
        self._sections = []
        self._closed = False
        self.alive = True
        self.caption = host.MARK
        del _LABELS[:]
        self._build()
        self._labels = list(_LABELS)
        del _LABELS[:]
        self.timer = forms.UITimer()
        self.timer.Interval = 0.2
        self.timer.Elapsed += self._tick
        try:
            self.container.Load += self._load
            self.container.UnLoad += self._unload
        except Exception:  # noqa: BLE001 — ohne die Ereignisse läuft der Zeitgeber durch
            pass
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
        self.server = _textbox()
        self.device = _textbox("optional")
        self.debug = _check("Ausführliches Protokoll", lambda *_: None)
        self.save = _button("Einstellungen sichern", self._guarded(self._save_settings))
        self.settings = forms.Expander()
        self.settings.Header = _label("Einstellungen", wrap=False)
        self.settings.Content = _stack(_row("Server", self.server), _row("Gerät", self.device),
                                       _row("", self.debug), _row("", self.save))
        self.login_row = _stack(self.open_login, self.cancel_login, horizontal=True)
        self.connection = self._section("Verbindung", self.status, self.code, self.login_row,
                                        _stack(self.connect, self.sign_out, horizontal=True), self.settings)
        # Projekt
        self.project = _dropdown(self._on_project)
        self.refresh_button = _button("Aktualisieren", self._guarded(c.refresh))
        self.create_project = _button("Projekt anlegen …", self._guarded(c.new_project))
        self.project_group = self._section("Projekt", _row("", self.project),
                                      _row("", _stack(self.refresh_button, self.create_project, horizontal=True)))
        # Blickpunkt
        self.view = _dropdown(self._on_view)
        self.target_mode = _dropdown()
        _fill(self.target_mode, TARGETS, frame.CREATE)
        self.target_mode.SelectedIndexChanged += self._on_target_mode
        self.viewpoint_name = _textbox("Name des Blickpunkts")
        self.viewpoint_name.TextChanged += self._on_name
        self.viewpoint = _dropdown(self._on_viewpoint)
        self.fit = _check("Rahmen an Aufnahme anpassen", self._on_fit)
        self.size = _dropdown()
        _fill(self.size, SIZES, frame.SIZE_CANVAS_DEFAULT)
        self.size.SelectedIndexChanged += self._on_size
        self.size_text = _label("", wrap=False)
        self.aspect = _label("")
        self.name_row = _row("Name", self.viewpoint_name)
        self.viewpoint_row = _row("Blickpunkt", self.viewpoint)
        self.fit_row = _row("", self.fit)
        self.viewpoint_group = self._section("Blickpunkt", _row("Ansicht", self.view),
                                             _row("Ziel", self.target_mode), self.name_row, self.viewpoint_row,
                                             self.fit_row, _row("Rahmen", self.size), _row("", self.size_text),
                                             self.aspect)
        # Bild
        self.send_image = _check("Bild senden", self._on_send_image)
        self.kind = _dropdown()
        _fill(self.kind, KINDS, VIEWPORT)
        self.kind.SelectedIndexChanged += self._on_kind
        self.resolution = _dropdown()
        _fill(self.resolution, RESOLUTIONS, RESOLUTION_DOCUMENT)
        self.resolution.SelectedIndexChanged += self._on_resolution
        self.resolution_row = _row("Größe", self.resolution)
        self.frame_text = _label("", wrap=False)
        self.pass_checks = {}
        for role, title in PASSES:
            box = _check(title, self._on_pass)
            box.Tag = role
            self.pass_checks[role] = box
        self.pass_hint = _label("")
        self.bit_depth = _dropdown()
        _fill(self.bit_depth, [(str(value), text) for value, text in mf.DATA_PASS_BIT_DEPTH_OPTIONS],
              str(mf.DEFAULT_DATA_PASS_BIT_DEPTH))
        self.bit_depth.SelectedIndexChanged += self._on_bit_depth
        self.bit_depth.ToolTip = mf.DATA_PASS_BIT_DEPTH_LABEL
        self.pass_hint_row = _row("", self.pass_hint)
        self.passes = self._section(None, _row("Pässe", _grid([self.pass_checks[role] for role, _ in PASSES])),
                                    self.pass_hint_row, _row("Bittiefe", self.bit_depth))
        self.image_group = self._section("Bild", _row("", self.send_image), _row("Art", self.kind),
                                         self.resolution_row, _row("Ausschnitt", self.frame_text), self.passes)
        # Modell (RTX-RH-003)
        self.send_model = _check("Modell senden", self._on_send_model)
        self.model_colors = _check("Materialfarben mitsenden", self._on_model_colors)
        self.model_hint = _label("", wrap=False)
        self.count = _button("Neu zählen", self._guarded(c.count_model))
        self.model_row = _row("Größe", self.model_hint, self.count)
        self.colors_row = _row("", self.model_colors)
        self.model_group = self._section("Modell", _row("", self.send_model), self.colors_row, self.model_row)
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
        self.resume_row = _stack(self.resume, self.discard, horizontal=True)
        self.transfer_group = self._section("Übernehmen", self.summary, self.capture, self.pending_text,
                                            self.resume_row, self.progress, self.progress_text, self.cancel,
                                            self.message, self.open_result)
        # Fuß
        self.footer = _label(f"{host.MARK} {host.PLUGIN_VERSION} · {host.build_text()}", wrap=False)
        self.about = _button("Über …", self._guarded(self._show_about))

        body = self._section(None, self.connection, self.project_group, self.viewpoint_group, self.image_group,
                             self.model_group, self.transfer_group, _stack(self.footer, self.about, horizontal=True),
                             spacing=SECTION_SPACING)
        body.Padding = drawing.Padding(PADDING)
        scroll = forms.Scrollable()
        scroll.Border = getattr(forms.BorderType, "NONE", scroll.Border)
        scroll.ExpandContentWidth = True
        scroll.Content = body
        self.container.Content = scroll

    def _section(self, title, *controls, spacing: int = ROW_SPACING):
        """Ein Abschnitt mit Überschrift (wie „Viewport" in „Eigenschaften"); ``None``: ohne Überschrift."""
        head = [_label(title, wrap=False, bold=True)] if title else []
        section = Section(head + list(controls), spacing)
        self._sections.append(section)
        return section.stack

    # -- Ereignisse --------------------------------------------------------

    def _guarded(self, action):
        def handler(*_args):
            try:
                action()
            except Exception as error:  # noqa: BLE001 — nie ein Traceback in der Palette
                log_exception("Fehler in der Palette", error)
                self.controller._set_error(str(error) or "Unerwarteter Fehler — Einzelheiten stehen im Protokoll.")
            self._refresh_all()
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
        for view in self._peers():
            view._settings_shown = False

    def _show_about(self) -> None:
        c = self.controller
        try:
            rhino = c.adapter.host_version()
        except Exception:  # noqa: BLE001
            rhino = "unbekannt"
        lines = [f"{host.MARK} für Rhino", f"Fassung {host.PLUGIN_VERSION}", host.build_text(),
                 f"Kennung {host.CLIENT_ID}", f"Server {c.state.server or '—'}", f"Rhino {rhino}"]
        forms.MessageBox.Show(self.container, "\n".join(lines), f"Über {host.MARK}")

    def _tick(self, *_):
        try:
            if self.controller.pump():
                self._refresh_all()
            elif self.controller.state.job is not None:
                self.refresh()
        except Exception as error:  # noqa: BLE001
            log_exception("Fehler im Zeitgeber", error)

    def _load(self, *_):
        if self.disposed():
            return
        self.alive = True
        self.timer.Start()
        self.refresh()

    def _unload(self, *_):
        # Rhino nimmt das Panel aus der Leiste (Reiter geschlossen, Dokumentfenster zu); eine laufende
        # Übertragung läuft weiter und erscheint, sobald die Palette wieder offen ist.
        self.alive = False
        self.timer.Stop()

    def close(self) -> None:
        """Für immer zu (Fenster geschlossen): kein Zeitgeber, kein Zeichnen mehr."""
        self._closed = True
        self._unload()

    def disposed(self) -> bool:
        """Entsorgt — das Fenster ist zu, oder Rhino hat das Panel freigegeben (``IsDisposed``)."""
        try:
            return self._closed or bool(getattr(self.container, "IsDisposed", False))
        except Exception:  # noqa: BLE001 — im Zweifel weiter zeichnen
            return self._closed

    def _refresh_all(self) -> None:
        for view in self._peers():
            view.refresh()

    # -- Zeichnen ----------------------------------------------------------

    def refresh(self) -> None:
        self._updating = True
        try:
            self._refresh()
            for section in self._sections:
                section.relayout()
            for label, wrap in self._labels:
                _align(label, wrap)
        except Exception as error:  # noqa: BLE001 — Regel 3: ein Fehler beim Zeichnen schließt nichts
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
        self.code.Visible = bool(self.code.Text)
        self.login_row.Visible = self.open_login.Visible = self.cancel_login.Visible = bool(state.code)
        self.connect.Visible = not state.connected and not state.code
        self.connect.Enabled = not busy
        self.sign_out.Visible = state.connected
        self.sign_out.Enabled = not busy
        self.save.Enabled = not busy
        connected = state.connected
        if self._connected_shown != connected:
            # Einstellungen aufgeklappt, solange keine Verbindung steht; danach zu — der Nutzer kann sie öffnen.
            self.settings.Expanded = not connected
            self._connected_shown = connected

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
        _fill(self.target_mode, TARGETS, form.target_mode)
        creating = form.target_mode == frame.CREATE
        self.name_row.Visible = self.viewpoint_name.Visible = creating
        if creating and self.viewpoint_name.Text != form.viewpoint_name:
            self.viewpoint_name.Text = form.viewpoint_name
        viewpoints = state.viewpoints.get(form.project_id or "", [])
        _fill(self.viewpoint, [("", NO_VIEWPOINT)] + list(viewpoints), form.viewpoint_id or "")
        self.viewpoint_row.Visible = self.fit_row.Visible = not creating
        self.viewpoint.Visible = self.fit.Visible = not creating
        self.fit.Checked = form.fit_to_capture
        _fill(self.size, SIZES, form.size)
        self.size_text.Text = size_line(c)
        self.size_text.ToolTip = self.size.ToolTip = c.size_text()
        hint = c.aspect_hint()
        self.aspect.Text = hint or ""
        self.aspect.Visible = bool(hint)

        # Bild
        self.send_image.Checked = form.send_image
        _fill(self.kind, KINDS, form.capture_kind)
        self.resolution_row.Visible = self.resolution.Visible = form.target_mode == frame.UPDATE
        _fill(self.resolution, RESOLUTIONS, form.resolution)
        try:
            width, height = c.capture_size() or c.document_size()
            self.frame_text.Text = f"{width} × {height}"
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
                box.ToolTip = "" if pass_state == "available" else (pass_hint or "")
                if pass_state != "available" and pass_hint and pass_hint not in hints:
                    hints.append(pass_hint)
            allowed = ((state.handshake or {}).get("limits") or {}).get("allowedMediaTypes")
            if allowed is not None and mf.PNG_MEDIA_TYPE not in allowed:
                hints.append("Dieser Server nimmt noch keine Pässe an; übertragen wird das Bild.")
            self.pass_hint.Text = " ".join(hints)
            self.pass_hint_row.Visible = self.pass_hint.Visible = bool(hints)
            _fill(self.bit_depth, [(str(value), text) for value, text in mf.DATA_PASS_BIT_DEPTH_OPTIONS],
                  str(c.data_pass_bit_depth()))

        # Modell
        self.send_model.Checked = form.send_model
        self.model_colors.Checked = form.model_colors
        self.colors_row.Visible = self.model_colors.Visible = self.count.Visible = form.send_model
        self.model_row.Visible = form.send_model
        self.count.Enabled = not busy
        details = c.model_hint()
        self.model_hint.Text = model_line(c) if form.send_model else ""
        self.model_hint.ToolTip = details
        self.model_hint.Visible = bool(self.model_hint.Text)

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
        self.pending_text.Visible = self.resume_row.Visible = self.resume.Visible = self.discard.Visible = bool(pending)
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
        self.message.Visible = bool(self.message.Text)
        self.message.TextColor = drawing.Colors.Red if state.error else drawing.SystemColors.ControlText


class RdtxWindow(forms.Form):
    """Ausweg, wenn Rhino das Panel nicht annimmt: dieselbe Palette als schwebendes Fenster (wie bis 0.2.x)."""

    def __init__(self, view: RdtxView):
        super().__init__()
        self.view = view
        self.Title = f"{host.MARK} {host.PLUGIN_VERSION}"
        self.Resizable = True
        self.MinimumSize = drawing.Size(WIDTH, 420)
        self.Content = view.container
