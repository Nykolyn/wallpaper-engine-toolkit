"""Restore: put back a pass Wallpaper Engine threw away (see engines/pass_restore).

One panel per tracked monitor: where its pass stands now, the pass Wallpaper
Engine last threw away there, a Restore button, and the switch that has the
tray do it unasked. What each panel says is worked out off the GUI thread —
it reads tracker.json and, for an old archive entry, access times on W:.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QApplication, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from .. import theme
from ..engines import pass_restore
from ..engines.tracker import Cycle, TrackerState
from ..services.runs import begin
from ..ui.kit import AccentButton, CardTitle, ConfirmDialog, GlassPanel, Toggle, label
from .base import Page

SOURCES = {"saved": "list saved by the tracker", "access times": "list from file access times"}


@dataclass
class Look:
    """One monitor, as the page shows it."""
    monitor: str
    cycle: Cycle
    found: pass_restore.Restorable | None
    reason: str


def look() -> list[Look]:
    """Every tracked monitor and whether its last thrown-away pass can come back."""
    state = TrackerState.load()
    out = []
    for monitor in sorted(state.cycles):
        found, reason = pass_restore.find_restorable(state, monitor)
        out.append(Look(monitor, state.cycles[monitor], found, reason))
    return out


class _MonitorPanel(GlassPanel):
    restore_clicked = Signal(str)
    auto_toggled = Signal(str, bool)

    def __init__(self, monitor: str):
        super().__init__(padding="lg")
        self.monitor = monitor
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(theme.DIALOG_ROW_GAP)
        self.title = CardTitle(monitor, "")
        column.addWidget(self.title)
        self.current = label("", "type.body", "body")
        self.lost = label("", "type.body", "body")
        self.lost.setWordWrap(True)
        self.source = label("", "type.caption", "lo")
        for widget in (self.current, self.lost, self.source):
            column.addWidget(widget)
        row = QHBoxLayout()
        self.button = AccentButton("Restore")
        self.button.setAccessibleName(f"Restore the pass on {monitor}")
        self.button.clicked.connect(lambda: self.restore_clicked.emit(self.monitor))
        self.auto = Toggle("Restore automatically")
        self.auto.setAccessibleName(f"Restore automatically on {monitor}")
        self._showing = False           # the page setting the switch, not the user
        self.auto.toggled.connect(self._toggled)
        row.addWidget(self.button)
        row.addStretch(1)
        row.addWidget(self.auto)
        column.addLayout(row)

    def _toggled(self, on: bool) -> None:
        if not self._showing:
            self.auto_toggled.emit(self.monitor, on)

    def show_look(self, item: Look, auto: bool, busy: bool) -> None:
        cycle, found = item.cycle, item.found
        self.title.set_subtitle(cycle.playlist)
        self.current.setText(f"Now: {cycle.seen_count}/{cycle.total}")
        if found is not None:
            entry = found.entry
            self.lost.setText(f"Thrown away {entry['ended'][:16]}: {entry['seen']}/"
                              f"{entry['total']} · Restore brings it to "
                              f"{found.seen}/{found.total}")
            self.source.setText(SOURCES[found.source])
        else:
            self.lost.setText(item.reason)
            self.source.setText("")
        self.source.setVisible(bool(self.source.text()))
        self.button.setEnabled(found is not None and not busy)
        self.button.setText("Restoring…" if busy else "Restore")
        # Not blockSignals: the knob slides on `toggled`, and would stay put.
        self._showing = True
        self.auto.setChecked(auto)
        self._showing = False


class RestorePage(Page):
    key = "restore"
    title = "Restore"
    icon = "refresh"
    FIXTURES = ("idle",)

    _looked = Signal(object)        # list[Look], from the thread that looked

    def __init__(self, settings, *, parent: QWidget | None = None):
        super().__init__(parent)
        self.settings = settings
        self.panels: dict[str, _MonitorPanel] = {}
        self.looks: dict[str, Look] = {}
        self.worker: pass_restore.RestoreWorker | None = None
        self._busy: str | None = None
        self._fixture = False           # a made-up state is never looked over
        self.set_subtitle("Put back a pass Wallpaper Engine threw away")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        self.column = QVBoxLayout(body)
        pad_v, pad_h = theme.BODY_PAD
        self.column.setContentsMargins(pad_h, pad_v, pad_h, pad_v)
        self.column.setSpacing(theme.PANEL_GAP)
        self.empty = label("Checking…", "type.body", "mid")
        self.column.addWidget(self.empty)
        self.column.addStretch(1)
        scroll.setWidget(body)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        self._looked.connect(self._show)
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._shutdown)

    def _shutdown(self) -> None:
        """A restore under way gets to start Wallpaper Engine again before the
        window goes."""
        if self.worker is not None and self.worker.isRunning():
            self.worker.wait()

    # -- looking

    def on_shown(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        if self._fixture:
            return
        threading.Thread(target=lambda: self._looked.emit(look()), daemon=True,
                         name="restore-look").start()

    def _show(self, looks: list[Look]) -> None:
        self.looks = {item.monitor: item for item in looks}
        self.empty.setText("" if looks else "Nothing is tracked yet.")
        self.empty.setVisible(not looks)
        for monitor in list(self.panels):
            if monitor not in self.looks:
                self.panels.pop(monitor).deleteLater()
        for item in looks:
            panel = self.panels.get(item.monitor)
            if panel is None:
                panel = self.panels[item.monitor] = _MonitorPanel(item.monitor)
                panel.restore_clicked.connect(self.restore)
                panel.auto_toggled.connect(self._set_auto)
                self.column.insertWidget(self.column.count() - 1, panel)
            panel.show_look(item, pass_restore.auto_enabled(self.settings, item.monitor,
                                                            item.cycle),
                            self._busy == item.monitor)

    def _set_auto(self, monitor: str, on: bool) -> None:
        pass_restore.set_auto(self.settings, monitor, on)

    # -- restoring

    def _answer(self, dialog):
        """Ask. Tests put their own answer here."""
        return dialog.ask()

    def restore(self, monitor: str) -> None:
        if self.worker is not None:
            return
        dialog = ConfirmDialog(
            f"Restore the pass on {monitor}?",
            "Wallpaper Engine closes for a moment, the pass it threw away is put back, "
            "and it starts again.",
            self.window() if self.window() is not None else self,
            icon="refresh", tone="accent", confirm_text="Restore")
        if not self._answer(dialog):
            return
        self._busy = monitor
        self._run = begin("tracker", f"Restoring the pass on {monitor}", page="restore",
                          activity="restore")
        worker = self.worker = pass_restore.RestoreWorker(monitor)
        worker.finished_restore.connect(self._restored)
        worker.start()
        if monitor in self.looks:
            self._show(list(self.looks.values()))

    def _restored(self, result) -> None:
        worker, self.worker, self._busy = self.worker, None, None
        if worker is not None:
            worker.wait()
        if result.ok:
            self._run.finish("clean", result.message)
        else:
            self._run.fail(result.message)
        self.refresh()

    # -- for tests and snapshots

    def texts(self) -> dict:
        out = {}
        for monitor, panel in self.panels.items():
            item = self.looks[monitor]
            out[monitor] = {"current": panel.current.text(),
                            "lost": panel.lost.text() if item.found else "",
                            "source": panel.source.text(),
                            "button_enabled": panel.button.isEnabled(),
                            "reason": item.reason, "auto": panel.auto.isChecked()}
        return out

    def load_fixture(self, state: str) -> None:
        if state != "idle":
            super().load_fixture(state)
        self._fixture = True
        live = [f"W:/myprojects/{i}/scene.pkg" for i in range(194)]
        lost = Cycle(monitor="Monitor1", playlist="custom", started="2026-11-08 18:34:33",
                     items=live, seen={live[168]: "2026-11-08 18:34:33",
                                       live[169]: "2026-11-08 18:45:18"},
                     from_rotation=True, previous_id="270bd142")
        entry = {"id": "270bd142", "playlist": "custom", "started": "2026-11-01 01:46:10",
                 "ended": "2026-11-08 18:34:34", "seen": 168, "total": 194, "changes": 175}
        shown = {i: "2026-11-02 00:00:00" for i in live[:170]}
        found = pass_restore.Restorable("Monitor1", entry, lost, shown, "access times")
        other = Cycle(monitor="Monitor0", playlist="subscribed", started="2026-11-08 15:30:41",
                      items=live[:40], seen={live[0]: "2026-11-08 15:30:41"})
        self._show([Look("Monitor0", other, None,
                         "Wallpaper Engine has not thrown a pass away on Monitor0."),
                    Look("Monitor1", lost, found, "")])
