"""Desktop widget controls; the tracker owns its window and countdown."""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget

from .. import theme, widget_control, widget_startup
from ..settings import Settings
from ..ui.kit import CardTitle, Dropdown, GlassPanel, Overline, Toggle, label
from ..ui.kit.base import set_tone
from .base import Page


class _Answer(QObject):
    done = Signal(str, object)


class WidgetPage(Page):
    key = "widget"
    title = "Widget"
    icon = "tracker"
    FIXTURES = ("idle", "expanded")

    def __init__(self, settings: Settings, *, live: bool = True, parent=None):
        super().__init__(parent)
        self.settings = settings
        self._live = live
        self._busy = False
        self._stopping = threading.Event()
        self._visible_worker = None
        self._syncing = False
        self._startup_busy = False
        self._startup_on = False
        self._answer = _Answer(self)
        self._answer.done.connect(self._answered)
        self.set_subtitle("Playlist progress and countdown on your desktop")

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
        panel = GlassPanel(padding="lg")
        fields = QVBoxLayout(panel)
        fields.setContentsMargins(0, 0, 0, 0)
        fields.setSpacing(theme.DIALOG_ROW_GAP)
        fields.addWidget(CardTitle("Desktop widget", "Progress, status and time until the next wallpaper"))

        self.visible = Toggle("Show widget")
        self.visible.toggled.connect(self._set_visible)
        fields.addWidget(self.visible)
        self.visible_note = label("", "type.caption", "lo")
        self.visible_note.setWordWrap(True)
        fields.addWidget(self.visible_note)

        self.mode = Dropdown()
        self.mode.setAccessibleName("Widget view")
        self.mode.add_item("Compact", "compact")
        self.mode.add_item("Expanded", "expanded")
        title = Overline("View")
        title.setBuddy(self.mode)
        fields.addWidget(title)
        fields.addWidget(self.mode)
        self.mode.currentIndexChanged.connect(self._set_mode)
        note = label("Compact follows the monitor selected in the tray. Expanded shows every monitor. "
                     "Drag the widget by its header to place it on any screen.", "type.caption", "lo")
        note.setWordWrap(True)
        fields.addWidget(note)

        self.startup_toggle = Toggle("Start widget with Windows")
        self.startup_toggle.setEnabled(not live)
        self.startup_toggle.toggled.connect(self._set_startup)
        fields.addWidget(self.startup_toggle)
        self.startup_note = label("Checking Windows startup…" if live else "Off", "type.caption", "lo")
        self.startup_note.setWordWrap(True)
        fields.addWidget(self.startup_note)
        column.addWidget(panel)
        column.addStretch(1)
        scroll.setWidget(body)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        self._poll = QTimer(self)
        self._poll.setInterval(1000)
        self._poll.timeout.connect(self._sync)
        self._sync()

    def _note(self, field, text: str, tone: str = "lo") -> None:
        field.setText(text)
        set_tone(field, tone)

    def _check(self, toggle: Toggle, on: bool) -> None:
        # Let the Toggle's own signal move its knob, without treating a refresh as a click.
        self._syncing = True
        toggle.setChecked(on)
        self._syncing = False

    def _sync(self) -> None:
        if not self._live or self._busy:
            return
        self.settings.reload_if_changed()
        shown = bool(self.settings.get("widget", "enabled", False))
        self._check(self.visible, shown)
        self.mode.blockSignals(True)
        self.mode.setCurrentIndex(1 if self.settings.get("widget", "mode", "compact") == "expanded" else 0)
        self.mode.blockSignals(False)
        if self.visible_note.property("tone") != "danger":
            self._note(self.visible_note, "The widget stays open when the toolkit window closes." if shown
                       else "Hidden. Choose Show widget to open it.")

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
        if self._live and not self._stopping.is_set():
            self.settings.reload_if_changed()
            self.settings.set("widget", "mode", self.mode.currentData())
            self.settings.save()

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
            self._note(self.startup_note, "Starts with Windows while Show widget is enabled. "
                       "The tray's startup setting is separate." if found
                       else "Off. The tray's startup setting is separate.")

    def frame_fixture(self, state: str) -> dict | None:
        return {"frame": "idle"} if state in self.FIXTURES else None

    def load_fixture(self, state: str) -> None:
        if state not in self.FIXTURES:
            super().load_fixture(state)
        self._live = False
        self._poll.stop()
        self._check(self.visible, state == "expanded")
        self.mode.blockSignals(True)
        self.mode.setCurrentIndex(1 if state == "expanded" else 0)
        self.mode.blockSignals(False)
        self.startup_toggle.setEnabled(True)
        self._note(self.visible_note, "The widget stays open when the toolkit window closes." if state == "expanded"
                   else "Hidden. Choose Show widget to open it.")
        self._answered("startup", state == "expanded")
