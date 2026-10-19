"""Keep copies of active Workshop subscriptions in the Rotator's reserve."""
from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtWidgets import QStackedLayout, QVBoxLayout

from .. import theme
from ..engines import reserve as engine
from ..engines.rotator.config import Config
from ..services import begin
from ..ui.kit import (
    AccentButton, ButtonsCell, Callout, Cell, CellButton, Checkbox, CheckCell, Column,
    Elided, EmptyState, GlassPanel, NavState, ProgressBar, SecondaryButton, Table,
    TableBar, TableFooter, TableModel, format as fmt,
)
from .base import Page
from .folder_actions import open_in_explorer
from .tracker import _Offload


class ReserveModel(TableModel):
    def __init__(self, page):
        self.page = page
        super().__init__((Column("", 32, sortable=False),
                          Column("Wallpaper", thumb="rowxs", min_width=220),
                          Column("Author", 180), Column("Type", 95),
                          Column("Size", 115, mono=True, align="right"),
                          Column("", 44, sortable=False)), parent=page)

    def cell(self, row, column):
        enabled = not self.page.scanning and not self.page.acting
        if column == 0:
            return CheckCell(row.folder in self.page.selected, enabled)
        if column == 1:
            return Cell(row.title, strong=True, sub=row.note or Path(row.folder).name)
        if column == 2:
            return row.author or fmt.DASH
        if column == 3:
            return row.kind.title() or fmt.DASH
        if column == 4:
            return fmt.size(row.size)
        return ButtonsCell((CellButton("reserve", "plus", "Copy to reserve", self.page._can_copy()),))

    def sort_key(self, row, column):
        return row.size if column == 4 else super().sort_key(row, column)

    def thumb_source(self, row):
        return row.folder

    def data(self, index, role=Qt.DisplayRole):
        if role == Qt.ToolTipRole and index.isValid() and index.column() == 1:
            row = self.item_at(index.row())
            return f"{row.folder}\n{row.note}".strip() if row else None
        return super().data(index, role)


class ReservePage(Page):
    key, title, icon = "reserve", "To reserve", "copier"
    FIXTURES = ("found", "empty", "checking", "copying", "failed")
    _open_failed = Signal(str)

    def __init__(self, settings, services=None, config=None, parent=None):
        super().__init__(parent)
        self.settings, self.services = settings, services
        self.config = config if config is not None else Config(source="", destination="")
        self._offload = _Offload(self)
        self._cancel = threading.Event()
        self.scanning = self.acting = self._started = False
        self._fixture = None
        self._scanned_reserve = None
        self._rows, self.selected, self.messages = [], set(), []
        self._run = None
        self._pending_progress = None
        self._progress_lock = threading.Lock()
        self._progress_timer = QTimer(self)
        self._progress_timer.setInterval(200)
        self._progress_timer.timeout.connect(self._flush_progress)
        self._open_failed.connect(lambda words: self._say("danger", words))

        column = QVBoxLayout(self)
        v, h = theme.BODY_PAD
        column.setContentsMargins(h, v, h, v)
        column.setSpacing(theme.PANEL_GAP)
        column.addWidget(Callout("Keep copies of subscribed wallpapers that are missing from your reserve. "
                                 "The Workshop originals and subscriptions are kept.", tone="neutral"))
        self.destination = Elided("", "type.monoSm", "lo")
        column.addWidget(self.destination)
        self.problem = Callout(tone="warn")
        self.problem.hide()
        column.addWidget(self.problem)
        self.current = Elided()
        self.current.hide()
        column.addWidget(self.current)
        self.progress = ProgressBar(caption=True)
        self.progress.setAccessibleName("Reserve scan and copy progress")
        self.progress.hide()
        column.addWidget(self.progress)
        card = GlassPanel(padding="none")
        body = QVBoxLayout(card)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        toolbar = TableBar()
        self.all_rows = Checkbox("Select all", tristate=True)
        self.all_rows.setAccessibleName("Select all wallpapers missing from the reserve")
        self.all_rows.clicked.connect(self.select_all)
        toolbar.add(self.all_rows)
        self.copy_selected = SecondaryButton("Copy selected", icon="plus")
        self.copy_selected.clicked.connect(lambda: self._process(
            [r for r in self.model.items() if r.folder in self.selected]))
        toolbar.add(self.copy_selected)
        toolbar.add_stretch()
        self.copy_everything = AccentButton("Copy all to reserve", icon="plus")
        self.copy_everything.clicked.connect(self.copy_all)
        toolbar.add(self.copy_everything)
        body.addWidget(toolbar)
        self.model = ReserveModel(self)
        self.table = Table()
        self.table.setAccessibleName("Subscribed wallpapers missing from the reserve")
        self.table.setModel(self.model)
        self.table.clicked.connect(self._open)
        self.table.activated.connect(self._open)
        self.table.action_clicked.connect(self._action)
        self.table.installEventFilter(self)
        self.empty = EmptyState("Not checked yet", "Open this page to check your subscriptions.", icon="copier")
        self.content = QStackedLayout()
        body.addLayout(self.content, 1)
        self.content.addWidget(self.table)
        self.content.addWidget(self.empty)
        self.footer = TableFooter()
        body.addWidget(self.footer)
        column.addWidget(card, 1)
        if services is not None:
            services.jobs.changed.connect(self._render)
            services.jobs.finished.connect(self._render)
        self.header_actions()
        self._render()

    def make_header_actions(self):
        self.refresh = SecondaryButton("Refresh", icon="refresh")
        self.refresh.clicked.connect(self.check)
        self.stop = SecondaryButton("Stop", icon="stop")
        self.stop.clicked.connect(self._stop)
        return [self.stop, self.refresh]

    def on_shown(self):
        if (self._fixture is None and getattr(self.window(), "_start", True)
                and (not self._started or self._scanned_reserve != self.config.source)):
            self.check()

    def check(self, *_):
        if self.scanning or self.acting or self._fixture is not None:
            return False
        self._started, self.scanning = True, True
        self._cancel.clear()
        self._progress_timer.start()
        self.problem.hide()
        self.progress.set_state("indeterminate")
        self.progress.set_caption("Reading the reserve…")
        self.current.set_text("Checking your subscriptions")
        reserve = self.config.source
        cache = self.services.data_dir / "steam_cache.sqlite" if self.services else None
        self._render()
        self._offload.run(lambda: engine.scan(reserve, cache_path=cache,
                                              on_progress=lambda *args: self._report_progress("scan", *args),
                                              cancelled=self._cancel.is_set),
                          lambda result: self._scanned(result, reserve))
        return True

    def _scanning_progress(self, phase, done, total):
        if self.scanning:
            self.current.set_text(phase)
            self.progress.set_state("determinate" if total else "indeterminate")
            self.progress.set_value(done, total)
            self.progress.set_caption(f"{fmt.count(done)} / {fmt.count(total)}")

    def _report_progress(self, kind, *values):
        with self._progress_lock:
            self._pending_progress = kind, values

    def _flush_progress(self):
        with self._progress_lock:
            pending, self._pending_progress = self._pending_progress, None
        if pending is not None:
            kind, values = pending
            if kind == "scan":
                self._scanning_progress(*values)
            else:
                self._copying_progress(*values)

    def _scanned(self, result, reserve=None):
        self._progress_timer.stop()
        self._flush_progress()
        self.scanning = False
        if isinstance(result, InterruptedError):
            self._say("warn", "Scan stopped. Previous results are kept.")
        elif isinstance(result, Exception):
            self.problem.set_body(f"Check failed: {result} Previous results are kept.")
            self.problem.show()
        elif reserve is not None and reserve != self.config.source:
            self.problem.set_body("The reserve changed during the scan. Refresh to check the new folder.")
            self.problem.show()
        else:
            self._scanned_reserve = self.config.source
            self._rows = result.rows
            self.model.set_rows(self._rows)
            self.selected.intersection_update(r.folder for r in self._rows)
            words = result.warning
            if result.skipped:
                words += f" {fmt.count(result.skipped)} incomplete or invalid wallpapers were skipped."
            self.problem.set_body(words.strip())
            self.problem.setVisible(bool(words.strip()))
        self._render()

    def _blocked(self):
        jobs = getattr(self.services, "jobs", None)
        rotator = getattr(self.window(), "pages", {}).get("rotator")
        return ((jobs is not None and any(jobs.is_running(k) for k in ("rotator", "cleaner")))
                or (rotator is not None and (rotator.state == "running" or rotator._starting)))

    def _render(self, *_):
        rows = self.model.items()
        size = sum(r.size or 0 for r in rows)
        selected_size = sum(r.size or 0 for r in rows if r.folder in self.selected)
        busy = self.scanning or self.acting
        self.table.setEnabled(not busy)
        enabled = not busy
        can_copy = self._can_copy()
        self.destination.set_text(f"Reserve: {self.config.source or 'Choose a folder in Settings'}")
        self.refresh.setEnabled(enabled)
        self.stop.setVisible(busy)
        self.stop.setEnabled(busy and not self._cancel.is_set())
        self.all_rows.setEnabled(enabled and bool(rows))
        self.all_rows.setCheckState(Qt.Unchecked if not self.selected else
                                   Qt.Checked if len(self.selected) == len(rows) else Qt.PartiallyChecked)
        self.copy_selected.setEnabled(can_copy and bool(self.selected))
        self.copy_everything.setEnabled(can_copy and bool(rows))
        self.table.viewport().update()
        self.footer.set_text(f"{fmt.counted(len(rows), 'wallpaper')} · {fmt.size(size)} total"
                             + (f" · {fmt.count(len(self.selected))} selected · {fmt.size(selected_size)}"
                                if self.selected else ""))
        self.set_subtitle("Copying to the reserve…" if self.acting else "Checking subscriptions…"
                          if self.scanning else f"{fmt.counted(len(rows), 'wallpaper')} missing from reserve")
        if not busy:
            self.set_nav_state(NavState.badge(len(rows)) if rows else NavState())
        self.current.setVisible(busy)
        self.progress.setVisible(busy)
        self.content.setCurrentWidget(self.table if rows else self.empty)
        self.empty.set_title("Checking subscriptions" if self.scanning else "Reserve is up to date"
                             if self._scanned_reserve is not None else "Not checked yet")
        self.empty.set_body("Steam must be running. Only fully downloaded, valid wallpapers are listed."
                            if self._scanned_reserve is None or self.scanning else
                            "No downloaded subscriptions are missing from this reserve.")

    def _can_copy(self):
        return (not self.scanning and not self.acting and not self._blocked()
                and self._scanned_reserve == self.config.source)

    def select_all(self, *_):
        if self.scanning or self.acting:
            return
        visible = {r.folder for r in self.model.items()}
        self.selected = set() if visible <= self.selected else visible
        self._render()

    def _open(self, index):
        row = self.model.item_at(index.row())
        if row is None or self.scanning or self.acting:
            return
        if index.column() == 0:
            self.selected.symmetric_difference_update({row.folder})
            self._render()
        elif index.column() == 1 and self._fixture is None:
            open_in_explorer(row.folder, self._open_failed.emit)

    def eventFilter(self, watched, event):
        if watched is self.table and event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_A and event.modifiers() & Qt.ControlModifier:
                self.select_all()
                return True
            if event.key() == Qt.Key_Space:
                index = self.table.currentIndex()
                if index.isValid():
                    self._open(self.model.index(index.row(), 0))
                return True
        return super().eventFilter(watched, event)

    def _action(self, row, column, key):
        item = self.model.item_at(row)
        return self._process([item]) if item is not None and key == "reserve" else False

    def copy_all(self, *_):
        return self._process(list(self.model.items()))

    def _stop(self):
        self._cancel.set()
        self._render()

    def _process(self, items):
        if (not items or self.scanning or self.acting or self._fixture is not None
                or self._scanned_reserve != self.config.source):
            return False
        if self._blocked():
            self._say("warn", "Wait for the Rotator or Cleaner to finish before copying.")
            return False
        reserve = self.config.source
        self._cancel.clear()
        self._progress_timer.start()
        self.acting = True
        self.problem.hide()
        self._run = begin(self.key, f"Copying {fmt.counted(len(items), 'wallpaper')} to reserve",
                          activity="reserve_copy")
        self.progress.set_state("indeterminate")
        self.current.set_text("Checking the reserve before copying…")
        self.progress.set_caption("Preparing…")
        self._render()

        def work():
            kept = engine.reserve_ids(reserve, cancelled=self._cancel.is_set)
            results, copied, total = [], 0, sum(r.size for r in items)
            for item in items:
                if self._cancel.is_set():
                    break
                actual = item.size
                def progress(done, size):
                    nonlocal actual
                    actual = size
                    self._report_progress("copy", item.title, len(results), len(items), copied + done,
                                          total + size - item.size)
                try:
                    result = engine.copy_to_reserve(item.folder, reserve, kept=kept,
                                                    on_progress=progress, cancelled=self._cancel.is_set)
                    copied += actual
                    total += actual - item.size
                except InterruptedError:
                    break
                except Exception as err:
                    result = err
                results.append((item, result))
                self._report_progress("copy", item.title, len(results), len(items), copied, total)
            return results, self._cancel.is_set(), copied, total

        self._offload.run(work, self._acted)
        return True

    def _copying_progress(self, title, done, count, size, total):
        if not self.acting:
            return
        self.current.set_text(f"Copying: {title}")
        self.progress.set_state("determinate")
        self.progress.set_value(size, total)
        self.progress.set_caption(f"{fmt.size(size)} / {fmt.size(total)}", f"{done} / {count} wallpapers")
        self.set_nav_state(NavState.progress(size, total))
        if self._run:
            self._run.update(f"Copying {title}", size, total,
                             f"{done}/{count} wallpapers · {fmt.size(size)} / {fmt.size(total)}")

    def _acted(self, outcome):
        self._progress_timer.stop()
        self._flush_progress()
        self.acting = False
        if isinstance(outcome, Exception):
            self._run.finish("stopped" if isinstance(outcome, InterruptedError) else "failed", str(outcome))
            self._say("warn" if isinstance(outcome, InterruptedError) else "danger", str(outcome))
        else:
            results, stopped, copied, total = outcome
            succeeded, failed = set(), 0
            for item, result in results:
                if isinstance(result, Exception):
                    item.note = str(result)
                    failed += 1
                    self._run.log("error", f"{item.title}: {result}")
                else:
                    succeeded.add(item.folder)
                    self._run.log("info", f"Copied {item.title} to {result}")
            self._rows = [r for r in self._rows if r.folder not in succeeded]
            self.model.set_rows(self._rows)
            self.selected.difference_update(succeeded)
            words = (f"{len(succeeded)} wallpapers copied to reserve · {fmt.size(copied)}."
                     + (f" {failed} failed; see the row for details." if failed else "")
                     + (" Stopped; remaining wallpapers are kept in the list." if stopped else ""))
            result = "stopped" if stopped else "problems" if failed and succeeded else "failed" if failed else "clean"
            self._run.update(done=copied, total=total)
            self._run.finish(result, words)
            self._say("warn" if stopped or failed else "ok", words)
        self._run = None
        self._render()

    def _say(self, tone, words):
        self.messages.append((tone, words))
        toasts = getattr(self.window(), "toasts", None)
        if toasts is not None:
            toasts.show_toast(words, tone)
        if tone != "ok":
            self.problem.set_body(words)
            self.problem.show()

    def frame_fixture(self, state):
        fixture = {"frame": "idle"}
        if state == "copying":
            fixture["jobs"] = [{"tool": self.key, "title": "Copying to reserve", "phase": "Copying Night garden",
                                "done": 240_000_000, "total": 1_794_000_000,
                                "count_text": f"0/3 wallpapers · {fmt.size(240_000_000)} / {fmt.size(1_794_000_000)}"}]
        return fixture

    def load_fixture(self, state):
        if state not in self.FIXTURES:
            return super().load_fixture(state)
        self._fixture = state
        self.config.source = r"X:\Wallpapers\reserve"
        self._scanned_reserve = self.config.source
        self.scanning, self.acting = state == "checking", state == "copying"
        self.selected = set()
        self._rows = [engine.Wallpaper(fr"X:\Steam\workshop\content\431960\90000000{i}", title,
                                       author=author, kind=kind, size=size)
                      for i, (title, author, kind, size) in enumerate((
                          ("Night garden", "Mira", "video", 512_000_000),
                          ("Quiet orbit", "Evan", "scene", 82_000_000),
                          ("Rain on the window", "Mira", "video", 1_200_000_000)), 1)] if state != "empty" else []
        if state == "found":
            self.selected.add(self._rows[0].folder)
        self.model.set_rows(self._rows)
        self.problem.setVisible(state == "failed")
        if state == "failed":
            self.problem.set_body("Steam is not running. Start Steam and refresh. Previous results are kept.")
        self.current.set_text("Checking subscriptions" if self.scanning else "Copying: Night garden")
        self.progress.set_state("indeterminate" if self.scanning else "determinate")
        self.progress.set_value(240_000_000, 1_794_000_000)
        self.progress.set_caption(f"{fmt.size(240_000_000)} / {fmt.size(1_794_000_000)}", "0 / 3 wallpapers")
        self._render()
