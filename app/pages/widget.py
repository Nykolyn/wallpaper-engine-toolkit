"""Desktop widget controls; the tracker owns its window and countdown."""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QColorDialog, QGridLayout, QHBoxLayout, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from .. import theme, widget_control, widget_options, widget_startup
from ..settings import Settings
from ..engines.wallpaper_timer import monitor_map
from ..ui.kit import CardTitle, Dropdown, GlassPanel, Overline, SecondaryButton, SpinBox, TextInput, Toggle, label
from ..ui.kit.base import set_tone
from .base import Page


class _Answer(QObject):
    done = Signal(str, object)


class WidgetPage(Page):
    key = "widget"
    title = "Widget"
    icon = "tracker"
    FIXTURES = ("idle", "expanded", "per-monitor")

    def __init__(self, settings: Settings, *, feed=None, live: bool = True, parent=None):
        super().__init__(parent)
        self.settings = settings
        self._feed = feed
        self._profile_key = "default"
        self._fixture_monitors = ()
        self._live = live
        self._busy = False
        self._stopping = threading.Event()
        self._visible_worker = None
        self._syncing = False
        self._startup_busy = False
        self._startup_on = False
        self._answer = _Answer(self)
        self._answer.done.connect(self._answered)
        self.set_subtitle("Choose a layout, then customize each widget")

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFocusPolicy(Qt.NoFocus)
        body = QWidget()
        column = QVBoxLayout(body)
        pad_v, pad_h = theme.BODY_PAD
        column.setContentsMargins(pad_h, pad_v, pad_h, pad_v)
        column.setSpacing(theme.PANEL_GAP)
        panel, fields = self._panel("Desktop widgets", "Playlist progress and time until the next wallpaper")
        global_grid = QHBoxLayout()
        global_grid.setContentsMargins(0, 0, 0, 0)
        global_grid.setSpacing(theme.DIALOG_ROW_GAP)
        self.visible = Toggle("Show widgets")
        self.visible.toggled.connect(self._set_visible)
        self.visible_note = label("", "type.caption", "lo")
        self.visible_note.setWordWrap(True)
        self.startup_toggle = Toggle("Start widget with Windows")
        self.startup_toggle.setEnabled(not live)
        self.startup_toggle.toggled.connect(self._set_startup)
        self.startup_note = label("Checking Windows startup…" if live else "Off", "type.caption", "lo")
        self.startup_note.setWordWrap(True)
        self.layout_mode = self._dropdown("Widget layout", (("One widget", "single"),
                                                           ("One widget per monitor", "per_monitor")))
        self.layout_mode.currentIndexChanged.connect(self._set_layout)
        self.profile_selector = Dropdown()
        self.profile_selector.setAccessibleName("Customize widget")
        self.profile_selector.currentIndexChanged.connect(self._select_profile)
        for toggle, note, title, field in ((self.visible, self.visible_note, "Layout", self.layout_mode),
                                           (self.startup_toggle, self.startup_note, "Customize widget", self.profile_selector)):
            holder = QWidget()
            holder.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            stack = QVBoxLayout(holder)
            stack.setContentsMargins(0, 0, 0, 0)
            stack.setSpacing(theme.SP_8)
            stack.addWidget(toggle, 0, Qt.AlignLeft)
            stack.addWidget(note)
            stack.addWidget(self._field(title, field))
            global_grid.addWidget(holder, 1)
        fields.addLayout(global_grid)
        column.addWidget(panel)

        cards = QHBoxLayout()
        cards.setSpacing(theme.PANEL_GAP)
        self.appearance_panel, appearance = self._panel("Appearance", "Choose a design and view for each widget")
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(theme.DIALOG_ROW_GAP)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        self.design = self._dropdown("Widget design", (("Rectangles", "rectangles"), ("Rings", "rings")))
        self.design.currentIndexChanged.connect(lambda _i: self._set_option("design", self.design.currentData()))
        self.mode = self._dropdown("Widget view", (("Compact", "compact"), ("Expanded", "expanded")))
        self.mode.currentIndexChanged.connect(self._set_mode)
        self.size_choice = self._dropdown("Compact size", (("Small", 112), ("Medium", 144), ("Large", 176)))
        self.size_choice.currentIndexChanged.connect(lambda _i: self._set_option("size", self.size_choice.currentData()))
        self.opacity = SpinBox(minimum=40, maximum=100, value=100, suffix=" %")
        self.opacity.setAccessibleName("Widget opacity")
        self.opacity.valueChanged.connect(lambda value: self._set_option("opacity", value))
        self.target = Dropdown()
        self.target.setAccessibleName("Widget monitor")
        self.target.currentIndexChanged.connect(lambda _i: self._set_option("monitor", self.target.currentData()))
        grid.addWidget(self._field("Design", self.design), 0, 0)
        grid.addWidget(self._field("View", self.mode), 0, 1)
        grid.addWidget(self._field("Compact size", self.size_choice), 1, 0)
        grid.addWidget(self._field("Opacity", self.opacity), 1, 1)
        grid.addWidget(self._field("Monitor", self.target), 2, 0, 1, 2)
        self.outer_color, outer = self._color_field("outer_color", "Rear rectangle color")
        self.inner_color, inner = self._color_field("inner_color", "Front rectangle color")
        self.outer_color_group = self._field("Rear rectangle · playlist progress", outer)
        self.inner_color_group = self._field("Front rectangle · wallpaper time", inner)
        grid.addWidget(self.outer_color_group, 3, 0, 1, 2)
        grid.addWidget(self.inner_color_group, 4, 0, 1, 2)
        appearance.addLayout(grid)
        appearance_note = label("Liquid Glass in compact view; Glass Panel in expanded view, with the wallpaper's title and file size.",
                                "type.caption", "lo")
        appearance_note.setWordWrap(True)
        appearance.addWidget(appearance_note)
        self.behavior_panel, behavior = self._panel("Placement & behavior", "Position is remembered for each widget")
        self.layer = self._dropdown("Widget layer", (("On desktop", "desktop"),
                                                     ("Normal", "normal"), ("Always on top", "top")))
        self.layer.setToolTip("On desktop: behind apps. Normal window: among apps. Always on top: above apps.")
        self.layer.currentIndexChanged.connect(lambda _i: self._set_option("layer", self.layer.currentData()))
        behavior.addWidget(self._field("Layer", self.layer))
        self.layer_note = label("", "type.caption", "lo")
        self.layer_note.setWordWrap(True)
        behavior.addWidget(self.layer_note)
        self.locked = Toggle("Lock position")
        self.locked.setToolTip("Prevents dragging; the widget's menu stays available.")
        self.locked.toggled.connect(lambda on: self._set_option("locked", on))
        behavior.addWidget(self.locked)
        self.profile_enabled = Toggle("Show this widget")
        self.profile_enabled.toggled.connect(lambda on: self._set_option("enabled", on))
        behavior.addWidget(self.profile_enabled)
        self.profile_note = label("", "type.caption", "lo")
        self.profile_note.setWordWrap(True)
        behavior.addWidget(self.profile_note)
        self.reset = SecondaryButton("Reset this widget")
        self.reset.clicked.connect(self._reset_profile)
        behavior.addWidget(self.reset, 0, Qt.AlignLeft)
        reset_note = label("Resets appearance and position. Keeps its monitor, visibility and Windows startup choice.",
                           "type.caption", "lo")
        reset_note.setWordWrap(True)
        behavior.addWidget(reset_note)
        behavior.addStretch(1)
        cards.addWidget(self.appearance_panel, 1)
        cards.addWidget(self.behavior_panel, 1)
        column.addLayout(cards)
        column.addStretch(1)
        scroll.setWidget(body)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        self._poll = QTimer(self)
        self._poll.setInterval(1000)
        self._poll.timeout.connect(self._sync)
        if feed is not None:
            feed.updated.connect(self._sync)
        self._refresh_controls()

    @staticmethod
    def _panel(title: str, subtitle: str):
        panel = GlassPanel(padding="lg")
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(theme.DIALOG_ROW_GAP)
        column.addWidget(CardTitle(title, subtitle))
        return panel, column

    @staticmethod
    def _dropdown(name: str, choices):
        field = Dropdown()
        field.setAccessibleName(name)
        for title, value in choices:
            field.add_item(title, value)
        return field

    @staticmethod
    def _field(title: str, field):
        holder = QWidget()
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(theme.SP_6)
        heading = Overline(title)
        heading.setBuddy(field)
        column.addWidget(heading)
        column.addWidget(field)
        return holder

    def _color_field(self, name: str, title: str):
        field = TextInput(placeholder="#RRGGBB")
        field.setAccessibleName(title)
        field.setMaxLength(7)
        field.editingFinished.connect(lambda: self._set_color(name, field))
        choose = SecondaryButton("Choose…")
        choose.setAccessibleName("Choose " + title.lower())
        choose.clicked.connect(lambda: self._choose_color(name, field))
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.SP_6)
        row.addWidget(field, 1)
        row.addWidget(choose)
        return field, holder

    def _note(self, field, text: str, tone: str = "lo") -> None:
        field.setText(text)
        set_tone(field, tone)

    def _check(self, toggle: Toggle, on: bool) -> None:
        # Let the Toggle's own signal move its knob, without treating a refresh as a click.
        previous = self._syncing
        self._syncing = True
        toggle.setChecked(on)
        self._syncing = previous

    def _monitor_names(self) -> list[str]:
        names = list(self._fixture_monitors)
        names.extend(p.monitor for p in (self._feed.results if self._feed is not None else []))
        config = getattr(getattr(self._feed, "files", None), "config", None)
        names.extend(f"Monitor{entry['location']}" for entry in monitor_map(config).values()
                     if isinstance(entry, dict) and type(entry.get("location")) is int)
        profiles = self.settings.get("widget", "profiles", {})
        if isinstance(profiles, dict):
            names.extend(key for key in profiles if key != "default")
        target = widget_options.profile(self.settings)["monitor"]
        if target != "tray":
            names.append(target)
        return list(dict.fromkeys(name for name in names if isinstance(name, str) and name))

    @staticmethod
    def _pick(field, value) -> None:
        index = field.findData(value)
        field.setCurrentIndex(index if index >= 0 else 0)

    def _refresh_controls(self) -> None:
        self._syncing = True
        layout = self.settings.get("widget", "layout", "single")
        per_monitor = layout == "per_monitor"
        self._pick(self.layout_mode, layout)
        monitors = self._monitor_names()
        keys = monitors if per_monitor else ["default"]
        choices = [(key, key) for key in keys] if per_monitor else [("Single widget", "default")]
        if not choices:
            choices = [("No monitors discovered yet", None)]
        if [(self.profile_selector.itemText(i), self.profile_selector.itemData(i))
                for i in range(self.profile_selector.count())] != choices:
            self.profile_selector.clear()
            for title, key in choices:
                self.profile_selector.add_item(title, key)
        if self._profile_key not in keys:
            self._profile_key = keys[0] if keys else "default"
        self._pick(self.profile_selector, self._profile_key)
        self.profile_selector.setEnabled(per_monitor and bool(monitors))
        for panel in (self.appearance_panel, self.behavior_panel):
            panel.setEnabled(not per_monitor or bool(monitors))
        options = widget_options.profile(self.settings, self._profile_key)
        self._check(self.visible, bool(self.settings.get("widget", "enabled", False)))
        for field, name in ((self.design, "design"), (self.mode, "mode"),
                            (self.size_choice, "size"), (self.layer, "layer")):
            self._pick(field, options[name])
        self.opacity.setValue(options["opacity"])
        follow = "All monitors" if options["mode"] == "expanded" else "Follow selected tray monitor"
        targets = ([(follow, "tray")] + [(name, name) for name in monitors]
                   if self._profile_key == "default" else [(self._profile_key, self._profile_key)])
        if [(self.target.itemText(i), self.target.itemData(i)) for i in range(self.target.count())] != targets:
            self.target.clear()
            for title, key in targets:
                self.target.add_item(title, key)
        self._pick(self.target, options["monitor"])
        self.target.setEnabled(self._profile_key == "default")
        color_titles = ({"outer_color": ("Outer ring · time remaining", "Outer ring color"),
                         "inner_color": ("Inner ring · playlist remaining", "Inner ring color")}
                        if options["design"] == "rings" else
                        {"outer_color": ("Rear rectangle · playlist progress", "Rear rectangle color"),
                         "inner_color": ("Front rectangle · wallpaper time", "Front rectangle color")})
        for name, (caption, title) in color_titles.items():
            field = getattr(self, name)
            group = getattr(self, name + "_group")
            group.findChild(Overline).setText(caption)
            field.setAccessibleName(title)
            group.findChild(SecondaryButton).setAccessibleName("Choose " + title.lower())
            if not field.hasFocus() and field.error() is None:
                field.setText(options[name])
                field.set_error(None)
        self._check(self.locked, options["locked"])
        self._check(self.profile_enabled, options["enabled"])
        self.layer_note.setText({"desktop": "On the desktop, behind your app windows.",
                                 "normal": "A normal window among your apps.",
                                 "top": "Stays above other app windows."}[options["layer"]])
        if per_monitor:
            note = "Each monitor keeps its own appearance and position."
        elif options["monitor"] != "tray":
            note = f"This widget follows {options['monitor']} independently of the tray selection."
        elif options["mode"] == "expanded":
            note = "Expanded shows all monitors. Choose a monitor here to show only one."
        else:
            note = "Compact follows the monitor selected in the tray."
        self._note(self.profile_note, note)
        self._syncing = False

    def _sync(self) -> None:
        if not self._live or self._busy:
            return
        self.settings.reload_if_changed()
        self._refresh_controls()
        shown = bool(self.settings.get("widget", "enabled", False))
        if self.visible_note.property("tone") != "danger":
            self._note(self.visible_note, "The widget stays open when the toolkit window closes." if shown
                       else "Hidden. Choose Show widgets to display them.")

    def startup(self) -> None:
        if self._live:
            widget_control.log(f"widget window startup: enabled={self.settings.get('widget', 'enabled', False)}")
        if self._live and self.settings.get("widget", "enabled", False):
            self._set_visible(True)

    def stop(self, timeout: float = 0) -> bool:
        """Cancel late widget launches and drain one already starting before an installer quit."""
        self._stopping.set()
        self._poll.stop()
        worker = self._visible_worker
        if worker is not None and worker.is_alive():
            worker.join(timeout)
            return not worker.is_alive()
        return True

    def on_shown(self) -> None:
        if not self._live or self._stopping.is_set():
            return
        self._sync()
        self._poll.start()
        if not self._startup_busy:
            self._startup_busy = True
            self.startup_toggle.setEnabled(False)
            self._ask("startup", widget_startup.enabled)

    def on_hidden(self) -> None:
        self._poll.stop()

    def _set_mode(self, _index: int) -> None:
        self._set_option("mode", self.mode.currentData())

    def _set_option(self, name: str, value) -> bool:
        if not self._live or self._syncing or self._stopping.is_set():
            return False
        widget_options.set_option(self.settings, self._profile_key, name, value)
        self._sync()
        return True

    def _set_layout(self, _index: int) -> None:
        if not self._live or self._syncing or self._stopping.is_set():
            return
        widget_options.set_layout(self.settings, self.layout_mode.currentData())
        self.outer_color.set_error(None)
        self.inner_color.set_error(None)
        self._sync()

    def _select_profile(self, _index: int) -> None:
        if self._syncing:
            return
        key = self.profile_selector.currentData()
        if key is not None:
            self._profile_key = key
            self.outer_color.set_error(None)
            self.inner_color.set_error(None)
            self._refresh_controls()

    def _set_color(self, name: str, field: TextInput) -> None:
        if not self._live or self._syncing or self._stopping.is_set():
            return
        try:
            widget_options.set_option(self.settings, self._profile_key, name, field.text())
        except ValueError:
            field.set_error("Use #RRGGBB")
            return
        field.set_error(None)
        field.setText(widget_options.profile(self.settings, self._profile_key)[name])

    def _choose_color(self, name: str, field: TextInput) -> None:
        if not self._live or self._stopping.is_set():
            return
        color = QColorDialog.getColor(QColor(widget_options.profile(self.settings, self._profile_key)[name]),
                                      self.window(), field.accessibleName())
        if color.isValid():
            field.setText(color.name().upper())
            self._set_color(name, field)

    def _reset_profile(self) -> None:
        if not self._live or self._stopping.is_set():
            return
        widget_options.reset_profile(self.settings, self._profile_key)
        self.outer_color.set_error(None)
        self.inner_color.set_error(None)
        self._sync()

    def _set_visible(self, on: bool) -> None:
        if not self._live or self._busy or self._syncing or self._stopping.is_set():
            return
        self._busy = True
        self.visible.setEnabled(False)
        self._note(self.visible_note, "Opening widget…" if on else "Hiding widget…")
        self._ask("visible", lambda: widget_control.set_visible(self.settings, on,
                                                                stopped=self._stopping.is_set))

    def _set_startup(self, on: bool) -> None:
        if not self._live or self._syncing or self._stopping.is_set():
            return
        self._startup_busy = True
        self.startup_toggle.setEnabled(False)
        self._note(self.startup_note, "Changing Windows startup…")

        def change():
            widget_startup.set_enabled(on)
            return widget_startup.enabled()

        self._ask("startup", change)

    def _ask(self, what: str, work) -> None:
        def run():
            try:
                found = work()
            except Exception as err:  # noqa: BLE001 — display worker failures in the page
                found = err
            try:
                self._answer.done.emit(what, found)
            except RuntimeError:
                pass
        worker = threading.Thread(target=run, daemon=True, name=f"widget-{what}")
        if what == "visible":
            self._visible_worker = worker
        worker.start()

    def _answered(self, what: str, found) -> None:
        if what == "visible":
            self._busy = False
            self.visible.setEnabled(True)
            self._sync()
            if isinstance(found, Exception):
                self._note(self.visible_note, f"Could not change widget visibility: {found}", "danger")
        elif what == "startup":
            self._startup_busy = False
            self.startup_toggle.setEnabled(True)
            if isinstance(found, Exception):
                self._check(self.startup_toggle, self._startup_on)
                self._note(self.startup_note, f"Could not check or change Windows startup: {found}", "danger")
                return
            self._startup_on = bool(found)
            self._check(self.startup_toggle, bool(found))
            self._note(self.startup_note, "Starts with Windows while Show widgets is enabled. "
                       "The tray's startup setting is separate." if found
                       else "Off. The tray's startup setting is separate.")

    def frame_fixture(self, state: str) -> dict | None:
        return {"frame": "idle"} if state in self.FIXTURES else None

    def load_fixture(self, state: str) -> None:
        if state not in self.FIXTURES:
            super().load_fixture(state)
        self._live = False
        self._poll.stop()
        shown = state != "idle"
        self._fixture_monitors = ("Monitor1", "Monitor2")
        self._profile_key = "Monitor2" if state == "per-monitor" else "default"
        self.settings = Settings({"widget": {
            "enabled": shown, "layout": "per_monitor" if state == "per-monitor" else "single",
            "profiles": {"default": {"mode": "expanded" if state == "expanded" else "compact"},
                         "Monitor2": {"mode": "expanded", "size": 176, "opacity": 80,
                                      "layer": "normal", "locked": True,
                                      "outer_color": "#B692FF", "inner_color": "#F0BB63"}}}})
        self._refresh_controls()
        self.startup_toggle.setEnabled(True)
        self._note(self.visible_note, "The widget stays open when the toolkit window closes." if shown
                   else "Hidden. Choose Show widgets to display them.")
        self._answered("startup", shown)
