"""Copier queue: measured off-thread, copied sequentially, reported through services."""
from __future__ import annotations

import copy
import json
import os
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QFileDialog, QHBoxLayout, QListView,
    QTreeView, QVBoxLayout, QWidget,
)

from .. import external, theme
from ..engines import copier as engine
from ..services import Run
from ..settings import DEFAULT_COPIER_COUNT, DEFAULT_COPIER_DEST
from ..ui.kit import (
    AccentButton, ButtonsCell, Callout, CardTitle, Cell, CellButton, Checkbox, Elided, Glyph,
    ChipCell, Column, ConfirmDialog, EmptyState, FormDialog, GhostButton,
    GlassPanel, IconButton, LogPanel, MetricStrip, NavState, PathField, ProgressBar,
    ProgressCell, ProgressRing, SecondaryButton, SpinCell, Table, TableBar,
    TableFooter, TableModel, format as fmt, label,
)
from .base import Page


def totals(rows):
    rows = list(rows)
    known = all(r.get("size") is not None and r.get("files") is not None for r in rows)
    return dict(jobs=len(rows), copies=sum(r["requested"] for r in rows),
                size=sum(r.get("size") or 0 for r in rows),
                files=sum(r.get("files") or 0 for r in rows), known=known,
                done_bytes=sum(r.get("done_bytes", 0) for r in rows),
                done_files=sum(r.get("done_files", 0) for r in rows),
                ok=sum(r.get("ok", 0) for r in rows),
                done=sum(r["state"] == "done" for r in rows),
                failed=sum(r["state"] == "failed" for r in rows))


class MeasureWorker(QThread):
    measured = Signal(int, object)

    def __init__(self, revision, jobs, destination, parent=None):
        super().__init__(parent)
        self.revision, self.jobs, self.destination = revision, copy.deepcopy(jobs), destination

    def run(self):
        self.measured.emit(self.revision, engine.inspect_queue(self.jobs, self.destination))


class CopySignals(QObject):
    log = Signal(str)
    job = Signal(object)
    updated = Signal(object)
    finished = Signal(object)


class QueueModel(TableModel):
    def __init__(self, page):
        self.page = page
        widths = theme.COPIER_COLUMNS
        columns = [Column("JOB", widths[0], thumb="xs", sortable=False),
                   Column("COPIES", widths[1], sortable=False),
                   Column("SIZE", widths[2], mono=True, sortable=False),
                   Column("DESTINATION", widths[3], mono=True, elide="left", sortable=False),
                   Column("PROGRESS", widths[4], mono=True, sortable=False),
                   Column("STATE", widths[5], align="right", sortable=False),
                   Column("", widths[6], sortable=False)]
        super().__init__(columns, parent=page)

    def cell(self, row, column):
        state = row["state"]
        if column == 0:
            meta = ("…" if row["files"] is None else fmt.counted(row["files"], "file"))
            return Cell(row["name"], strong=True, sub=f"{fmt.counted(len(row['sources']), 'folder')} · {meta}")
        if column == 1:
            return SpinCell(row["copies"], enabled=state == "queued" and self.page.state != "running")
        if column == 2:
            return fmt.size(row["size"]) if row["size"] is not None else "…"
        if column == 3:
            return Cell(row["destination"] or self.page.destination.path() or "Not set", icon="folder")
        if column == 4:
            if state == "queued":
                return fmt.DASH
            if state in ("failed", "stopped", "skipped"):
                caption = f"{row['ok']} of {row['requested']} · {state}"
            elif state == "done":
                caption = f"took {fmt.duration(row['elapsed'])}"
            else:
                caption = fmt.size(row["speed"]) + "/s" if row["speed"] else "copying…"
            fraction = (row["done_bytes"] / row["size"] if row["size"]
                        else 1 if state == "done" else 0)
            return ProgressCell(fraction, "danger" if state == "failed" else "ok" if state == "done" else "accent",
                                caption=caption)
        if column == 5:
            variant = {"queued": "Queued", "copying": "Copying", "done": "Done", "failed": "Failed"}
            return ChipCell(variant[state]) if state in variant else Cell(state, "warn")
        editable = state == "queued" and self.page.state != "running"
        return ButtonsCell((CellButton("destination", "folder", "Edit destination", enabled=editable),
                            CellButton("remove", "trash", "Remove job", enabled=editable)))

    def thumb_source(self, row):
        return None if self.page._fixture else row["sources"][0]

    def row_tone(self, row):
        return "accent" if row["state"] == "copying" else None

    def flags(self, index):
        flags = super().flags(index)
        row = self.item_at(index.row())
        if row and index.column() == 1 and row["state"] == "queued" and self.page.state != "running":
            flags |= Qt.ItemIsEditable
        return flags

    def data(self, index, role=Qt.DisplayRole):
        row = self.item_at(index.row())
        if role == Qt.EditRole and row and index.column() == 1:
            return row["copies"]
        if role == Qt.ToolTipRole and row:
            return row["reason"] or "\n".join(row["sources"]) if index.column() == 0 else (
                row["destination"] or self.page.destination.path() if index.column() == 3 else
                "Double-click or press F2 to edit copies" if index.column() == 1 else row["reason"])
        return super().data(index, role)

    def setData(self, index, value, role=Qt.EditRole):
        if role != Qt.EditRole or not self.flags(index) & Qt.ItemIsEditable:
            return False
        try:
            value = int(value)
        except (ValueError, TypeError):
            return False
        if not 1 <= value <= 99_999:
            return False
        row = self.item_at(index.row())
        self.page.set_copies(row["id"], value)
        self.dataChanged.emit(index, index)
        return True


class CopierPage(Page):
    key, title, icon = "copier", "Copier", "copier"
    FIXTURES = ("empty", "queue", "running", "done", "no-space")

    def __init__(self, settings, services=None, config=None, parent=None):
        super().__init__(parent)
        self.settings, self.services, self.config = settings, services, config
        self.jobs, self.rows, self.drives = [], {}, []
        self.state, self._fixture, self._on_screen = "empty", None, False
        self._job = None
        self._revision = 0
        self._measuring = None
        self._scan_pending = False
        self._measured_revision = -1
        self._started = self._ended = None
        self._baseline_bytes = 0
        self._elapsed = self._speed = 0
        self._current = None
        self._cancelled = False
        self._run_ids = set()
        self.messages = []
        self.signals = CopySignals(self)
        self.engine = engine.CopyEngine(log=self.signals.log.emit, job_changed=self.signals.job.emit,
                                        updated=self.signals.updated.emit, finished=self.signals.finished.emit)
        self.signals.log.connect(self._log)
        self.signals.job.connect(self._job_changed)
        self.signals.updated.connect(self._updated)
        self.signals.finished.connect(self._finished)
        self._scan_timer = QTimer(self)
        self._scan_timer.setSingleShot(True)
        self._scan_timer.setInterval(theme.COPIER_SCAN_DEBOUNCE_MS)
        self._scan_timer.timeout.connect(self._measure)
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(theme.COPIER_RENDER_MS)
        self._render_timer.timeout.connect(self._render)
        self._build_ui()
        self._paste_shortcut = QShortcut(QKeySequence.Paste, self)
        self._paste_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self._paste_shortcut.activated.connect(self.paste)
        self.setAcceptDrops(True)
        self._render()

    def make_header_actions(self):
        button = GhostButton("Destination", outlined=True)
        button.clicked.connect(lambda: self.navigate.emit("settings"))
        return [button]

    def _build_ui(self):
        column = QVBoxLayout(self)
        column.setContentsMargins(theme.SP_16, theme.SP_14, theme.SP_16, theme.SP_12)
        column.setSpacing(theme.SP_12)
        self.empty_panel = GlassPanel(padding="lg")
        empty_layout = QVBoxLayout(self.empty_panel)
        self.empty = EmptyState("Drop folders here, or paste a path",
                                "Drag wallpaper folders from Explorer or paste their paths. "
                                "Each folder is duplicated the chosen number of times. "
                                "Jobs run one after another, so nothing competes for the disk.",
                                icon="copier", drop_zone=True)
        self.empty.dropped.connect(self.add_folders)
        choose = AccentButton("Choose folders")
        choose.clicked.connect(self.choose)
        paste = SecondaryButton("Paste path", icon="clipboard", key="Ctrl+V")
        paste.clicked.connect(self.paste)
        destination = SecondaryButton("Set destination", icon="folder")
        destination.clicked.connect(lambda: self.navigate.emit("settings"))
        for button in (choose, paste, destination):
            self.empty.add_action(button)
        places = QWidget()
        row = QHBoxLayout(places)
        row.setContentsMargins(0, theme.SP_4, 0, 0)
        row.setSpacing(theme.COPIER_PLACES_GAP)
        row.addStretch(1)
        self.known: dict[str, Elided] = {}
        for name in ("Reserve", "myprojects"):
            place = QHBoxLayout()
            place.setSpacing(theme.SP_6)
            place.addWidget(Glyph("folder", "text.lo", theme.COPIER_PLACE_ICON), 0, Qt.AlignVCenter)
            words = QVBoxLayout()
            words.setSpacing(0)
            words.addWidget(label(name, "type.caption", "mid"))
            self.known[name] = Elided("", "type.monoXs", "lo", mode=Qt.ElideLeft)
            self.known[name].setMaximumWidth(theme.DROP_BODY_WIDTH // 2 - theme.COPIER_PLACES_GAP)
            words.addWidget(self.known[name])
            place.addLayout(words)
            row.addLayout(place)
        row.addStretch(1)
        self.empty.add_below(places)
        empty_layout.addWidget(self.empty)
        column.addWidget(self.empty_panel, 1)

        self.run_panel = GlassPanel(padding="md")
        run_column = QVBoxLayout(self.run_panel)
        run_column.setContentsMargins(0, 0, 0, 0)
        run_column.setSpacing(theme.SP_8)
        head = QHBoxLayout()
        head.setSpacing(theme.SP_12)
        self.ring = ProgressRing(theme.COPIER_RING)
        head.addWidget(self.ring)
        self.run_title = CardTitle()
        head.addWidget(self.run_title, 1)
        self.pause_button = SecondaryButton("Pause", icon="pause")
        self.pause_button.clicked.connect(self.pause_resume)
        self.stop_button = SecondaryButton("Stop", icon="stop")
        self.stop_button.clicked.connect(self.stop)
        self.retry_button = AccentButton("Retry the failed job")
        self.retry_button.clicked.connect(self.retry_failed)
        self.open_top = SecondaryButton("Open destination", icon="ext")
        self.open_top.clicked.connect(self.open_destination)
        for button in (self.pause_button, self.stop_button, self.retry_button, self.open_top):
            head.addWidget(button)
        run_column.addLayout(head)
        self.run_bar = ProgressBar(height=theme.TABLE_PROGRESS_HEIGHT)
        run_column.addWidget(self.run_bar)
        self.run_meta = label("", "type.monoSm", "lo")
        run_column.addWidget(self.run_meta)
        self.metrics = MetricStrip([("0", "jobs done", "ok"), ("0", "failed", "danger"),
                                    ("—", "total time"), ("—", "average speed")])
        run_column.addWidget(self.metrics)
        column.addWidget(self.run_panel)

        self.card = GlassPanel(padding="none")
        table_column = QVBoxLayout(self.card)
        table_column.setContentsMargins(0, 0, 0, 0)
        table_column.setSpacing(0)
        self.toolbar = TableBar()
        self.toolbar.add(label("Copy to", "type.h3", "body"))
        self.destination = PathField(self.settings.get("copier", "dest", DEFAULT_COPIER_DEST),
                                     placeholder="Choose destination", dialog_title="Copy to")
        self.destination.setMaximumWidth(theme.COPIER_PATH_WIDTH)
        self.destination.path_changed.connect(self._destination_changed)
        self.toolbar.add(self.destination)
        self.toolbar.add_stretch()
        self.paste_button = GhostButton("Paste path", icon="clipboard", key="Ctrl+V")
        self.paste_button.clicked.connect(self.paste)
        self.add_button = GhostButton("Add folders…", icon="plus")
        self.add_button.clicked.connect(self.choose)
        self.refresh_button = IconButton("refresh", "Refresh sizes and free space")
        self.refresh_button.clicked.connect(self.queue_changed)
        for button in (self.paste_button, self.add_button, self.refresh_button):
            self.toolbar.add(button)
        table_column.addWidget(self.toolbar)
        self.model = QueueModel(self)
        self.table = Table()
        self.table.setAccessibleName("Copy queue; edit copies with F2")
        self.table.setModel(self.model)
        self.table.set_row_height(theme.COPIER_ROW_HEIGHT)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.table.action_clicked.connect(self._row_action)
        self.table.installEventFilter(self)
        self.table.viewport().installEventFilter(self)
        self.table.setAcceptDrops(True)
        table_column.addWidget(self.table, 1)
        self.problem = Callout(tone="danger")
        self.problem_retry = SecondaryButton("Retry")
        self.problem_retry.clicked.connect(self.retry_failed)
        self.problem_skip = GhostButton("Skip")
        self.problem_skip.clicked.connect(self.skip_failed)
        self.problem.add_action(self.problem_retry)
        self.problem.add_action(self.problem_skip)
        self.space_warning = Callout(tone="warn", title="Check the destination")
        self.notices = QWidget()
        notices = QVBoxLayout(self.notices)
        notices.setContentsMargins(theme.SP_12, theme.SP_8, theme.SP_12, theme.SP_8)
        notices.setSpacing(theme.SP_8)
        notices.addWidget(self.problem)
        notices.addWidget(self.space_warning)
        # Keep callouts directly below populated rows, leaving the remaining
        # card space clear, like the reference. The table itself fills when needed.
        table_column.addWidget(self.notices)
        table_column.addStretch()
        self.footer = TableFooter()
        self.clear_button = GhostButton("Clear queue")
        self.clear_button.clicked.connect(self.clear)
        self.verify = Checkbox("Verify each copy")
        self.verify.setChecked(bool(self.settings.get("copier", "verify", False)))
        self.verify.toggled.connect(self._verify_changed)
        self.start_button = AccentButton("Start copying")
        self.start_button.clicked.connect(self.start_copying)
        self.open_button = GhostButton("Open destination", icon="ext")
        self.open_button.clicked.connect(self.open_destination)
        for button in (self.clear_button, self.verify, self.start_button, self.open_button):
            self.footer.add_action(button)
        table_column.addWidget(self.footer)
        column.addWidget(self.card, 1)
        self.log = LogPanel()
        self.log.setMaximumHeight(theme.COPIER_LOG_HEIGHT)
        column.addWidget(self.log)

    def add_jobs(self, paths, copies=DEFAULT_COPIER_COUNT, destination=None) -> int:
        """Add one job per path, without touching the disk; deduplicate sources.

        Additions received while running wait for the next explicit Start.
        """
        listed = {os.path.normcase(p) for job in self.jobs for p in job.sources}
        added = 0
        for path in paths:
            if not str(path).strip():
                continue
            path = os.path.normpath(str(path))
            if os.path.normcase(path) in listed:
                continue
            job = engine.CopyJob(path, copies, destination)
            self.jobs.append(job)
            self.rows[job.id] = job.snapshot()
            listed.add(os.path.normcase(path))
            added += 1
        if added:
            if self.state != "running":
                self.state = "queue"
            self.queue_changed()
        return added

    def add_folders(self, paths, count=DEFAULT_COPIER_COUNT):
        return self.add_jobs(paths, count)

    def folders(self):
        return [(j.source, str(j.count)) for j in self.jobs]

    def choose(self):
        dialog = QFileDialog(self, "Choose folders to duplicate")
        dialog.setOption(QFileDialog.DontUseNativeDialog)
        dialog.setFileMode(QFileDialog.Directory)
        dialog.setOption(QFileDialog.ShowDirsOnly)
        for view in dialog.findChildren(QListView) + dialog.findChildren(QTreeView):
            view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        if dialog.exec():
            self.add_folders(dialog.selectedFiles())

    def paste(self):
        if isinstance(QApplication.focusWidget(), (QAbstractItemView,)) or self._on_screen or self.isVisible():
            text = QApplication.clipboard().text()
            try:
                entries = [entry for line in text.splitlines() if (entry := engine.parse_line(line))]
            except ValueError as exc:
                self._say("warn", str(exc))
                return
            if not entries:
                self._say("info", "Clipboard is empty. Copy a folder path first.")
            for path, copies in entries:
                self.add_jobs([path], copies)

    def queue_changed(self):
        self._revision += 1
        self._scan_pending = True
        self.drives = []
        if self._fixture is None and self.state != "running":
            self._scan_timer.start()
        self._render()

    def _measure(self):
        if self._fixture is not None or self.state == "running" or not self._scan_pending:
            return
        if self._measuring is not None:
            return
        self._scan_pending = False
        worker = MeasureWorker(self._revision, self.jobs, self.destination.path(), self)
        self._measuring = worker
        worker.measured.connect(self._measured)
        worker.finished.connect(self._measurement_done)
        worker.start()

    def _measured(self, revision, data):
        if revision != self._revision or self.state == "running" or self._fixture is not None:
            return
        self._measured_revision = revision
        self.drives = data["drives"]
        for job in self.jobs:
            row = data["jobs"].get(job.id)
            if row and job.state == "queued":
                job.files, job.size = row["files"], row["size"]
                self.rows[job.id].update(files=job.files, size=job.size, reason=row["reason"])
        self._render()

    def _measurement_done(self):
        worker, self._measuring = self._measuring, None
        if worker:
            worker.deleteLater()
        if self._scan_pending:
            self._scan_timer.start()

    def set_copies(self, key, copies):
        job = next(j for j in self.jobs if j.id == key)
        if self.state == "running" or job.state != "queued":
            return
        job.count = copies
        job.size = job.files = None
        self.rows[key] = job.snapshot()
        self.queue_changed()

    def _destination_changed(self, path):
        if self.state == "running":
            return
        self.settings.set("copier", "dest", path)
        if self._fixture is None:
            self.settings.save()
        self.queue_changed()

    def _verify_changed(self, value):
        if self._fixture is None:
            self.settings.set("copier", "verify", value)
            self.settings.save()
        self._render()

    def _row_action(self, row, column, action):
        entry = self.model.item_at(row)
        if not entry or self.state == "running" or entry["state"] != "queued":
            return
        job = next(j for j in self.jobs if j.id == entry["id"])
        if action == "remove":
            self.jobs.remove(job)
            self.rows.pop(job.id, None)
            if not self.jobs:
                self.state = "empty"
            self.queue_changed()
        elif action == "destination":
            form = FormDialog("Job destination", self.window(), body=job.name)
            field = PathField(job.destination or "", placeholder="Use the global destination")
            form.add_row("Copy to", field, "Leave empty to follow Copy to above the queue.")
            if form.ask():
                job.destination = field.path() or None
                self.rows[job.id]["destination"] = job.destination
                self.queue_changed()

    def start_copying(self):
        if self.state == "running" or self.engine.is_running() or self._fixture is not None:
            return
        queued = [j for j in self.jobs if j.state == "queued"]
        if not queued:
            return
        if any(not (j.destination or self.destination.path()).strip() for j in queued):
            self._say("warn", "Choose a destination for every job first.")
            return
        count = sum(j.requested - len(j.completed) for j in queued)
        destinations = list(dict.fromkeys(j.destination or self.destination.path() for j in queued))
        body = (f"Create {fmt.counted(count, 'copy', 'copies')} in "
                + "; ".join(destinations) + ". Existing folders and sources stay untouched.")
        dialog = ConfirmDialog(f"Start {fmt.counted(len(queued), 'job')}?", body,
                               self.window(), icon="copier", confirm_text="Start copying",
                               steps=[("Copy one job at a time", "New folders continue after the highest _copyN"),
                                      ("Verify each copy" if self.verify.isChecked() else "Verification is off",
                                       "Compare file names, counts and sizes" if self.verify.isChecked()
                                       else "Copies are reported after the write completes")])
        self.last_dialog = dialog
        if not dialog.ask():
            return
        self._begin()

    def _begin(self):
        self._scan_timer.stop()
        self._revision += 1
        self._run_ids = {j.id for j in self.jobs}
        self._baseline_bytes = sum(j.done_bytes for j in self.jobs)
        self._started, self._ended = datetime.now(), None
        self._clock = time.monotonic()
        self._elapsed = 0
        self._cancelled = False
        self._speed = 0
        self._job = Run(self.services, "copier", "Copying queued folders", activity="copy")
        self.log.clear()
        self.log.set_live(True)
        self.log.set_file(Path(self._job.job.log_path).name if self._job.job else "copier.log", writing=True)
        self.log.set_expanded(True)
        self.pause_button.setText("Pause")
        self.pause_button.set_icon("pause")
        self.stop_button.setEnabled(True)
        self.state = "running"
        self._render()
        self.engine.start(self.jobs, self.destination.path(), verify=self.verify.isChecked())

    def _job_changed(self, row):
        if row["id"] in self.rows:
            previous = self.rows[row["id"]]["state"]
            self.rows[row["id"]] = row
            if self._job and row["state"] in ("done", "failed") and previous != row["state"]:
                self._job.note("copy.job.failed" if row["state"] == "failed" else "copy.job.done", row["name"] + " · " + row["state"],
                               row["reason"] or f"{row['ok']} copies written",
                               chip="Failed" if row["state"] == "failed" else "Done")
        self._render_timer.start()

    def _updated(self, data):
        self._speed, self._current = data["speed"], data["current"]
        if self._job:
            number = self._job_number()
            phase = f"{number} of {len(self._run_ids)} jobs"
            phase += " · paused" if data["paused"] else f" · {fmt.size(self._speed)}/s" if self._speed else ""
            self._job.update(phase, data["done"], data["total"],
                             f"{fmt.size(data['done'])} / {fmt.size(data['total'])}")
        self._render_timer.start()

    def _finished(self, report):
        for row in report:
            self.rows[row["id"]] = row
        # The worker no longer mutates these. Keep completion identities for retry.
        finished = {j.id: copy.deepcopy(j) for j in self.engine.jobs}
        self.jobs = [finished.get(j.id, j) for j in self.jobs]
        self._ended = datetime.now()
        self._elapsed = time.monotonic() - self._clock
        self._speed = 0
        self.state = "done"
        t = totals(report)
        summary = f"{t['ok']} of {t['copies']} copies made"
        result = ("stopped" if self._cancelled else "problems" if t["ok"] and t["failed"]
                  else "failed" if t["failed"] else "problems" if t["ok"] < t["copies"] else "clean")
        if self._job:
            self._job.finish(result, summary, detail=f"{t['failed']} jobs failed",
                             chip="Failed" if t["failed"] else None)
            self._job = None
        self.log.set_live(False)
        self.log.set_file("copier.log", writing=False)
        self.log.set_expanded(False)
        self._render_timer.stop()
        self._render()
        if not self._on_screen:
            self._say("ok" if result == "clean" else "warn", summary)
        if any(j.state == "queued" for j in self.jobs):
            self.queue_changed()

    def _job_number(self):
        ids = [j.id for j in self.jobs if j.id in self._run_ids]
        return ids.index(self._current) + 1 if self._current in ids else max(1, sum(
            self.rows[k]["state"] in ("done", "failed", "skipped", "stopped") for k in ids))

    def pause_resume(self):
        if self.state != "running":
            return
        if self.engine.is_paused():
            self.engine.resume()
            self.pause_button.setText("Pause")
            self.pause_button.set_icon("pause")
        else:
            self.engine.pause()
            self.pause_button.setText("Resume")
            self.pause_button.set_icon("play")
        self._render()

    def stop(self):
        if self.state != "running":
            return
        self._cancelled = True
        self.engine.cancel()
        self.stop_button.setEnabled(False)
        self.pause_button.setEnabled(False)
        self._render()

    def retry_failed(self):
        keys = [r["id"] for r in self.rows.values() if r["state"] in ("failed", "stopped")]
        if self._fixture is not None:
            return
        if self.state == "running":
            for key in keys:
                self.engine.retry(key)
            return
        for job in self.jobs:
            if job.id in keys:
                job.state, job.reason = "queued", ""
                self.rows[job.id] = job.snapshot()
        self.state = "queue"
        self.queue_changed()

    def skip_failed(self):
        if self._fixture is not None:
            return
        for job in self.jobs:
            if self.rows[job.id]["state"] not in ("failed", "stopped"):
                continue
            if self.state == "running":
                self.engine.skip(job.id)
            else:
                job.state = "skipped"
                self.rows[job.id] = job.snapshot()
        self._render()

    def clear(self):
        if self.state == "running":
            return
        if self.state == "done":
            self.jobs = [j for j in self.jobs if j.state == "queued"]
        else:
            self.jobs = [j for j in self.jobs if j.state != "queued"]
        self.rows = {j.id: self.rows[j.id] for j in self.jobs}
        self.state = "empty" if not self.jobs else "queue" if any(j.state == "queued" for j in self.jobs) else "done"
        self.queue_changed()

    def _render(self):
        rows = [self.rows[j.id] for j in self.jobs]
        t = totals(r for r in rows if r["id"] in self._run_ids) if self.state == "running" else totals(rows)
        empty, running, done = not rows, self.state == "running", self.state == "done"
        if empty:
            self.state = "empty"
        self.empty_panel.setVisible(empty)
        self.card.setVisible(not empty)
        self.run_panel.setVisible(running or done)
        self.toolbar.setVisible(not running and not done)
        self.log.setVisible(running or done)
        self.destination.setEnabled(not running)
        self.verify.setEnabled(not running)
        self.verify.setVisible(not done and not running)
        self.start_button.setVisible(not running and any(r["state"] == "queued" for r in rows))
        self.start_button.setEnabled(bool(rows) and all(
            r["destination"] or self.destination.path() for r in rows if r["state"] == "queued")
                                     and (self._fixture is not None or self._measured_revision == self._revision))
        self.clear_button.setVisible(not running)
        self.clear_button.setText("Clear finished" if done else "Clear queue")
        self.open_button.setVisible(running or done)
        self.pause_button.setVisible(running)
        self.pause_button.setEnabled(not self._cancelled)
        self.stop_button.setVisible(running)
        self.retry_button.setVisible(done and any(r["state"] in ("failed", "stopped") for r in rows))
        self.retry_button.setText("Retry unfinished jobs" if self._cancelled else
                                  "Retry the failed job" if t["failed"] == 1 else "Retry failed jobs")
        self.open_top.setVisible(done)
        self.metrics.setVisible(done)
        self.run_bar.setVisible(running)
        self.run_meta.setVisible(running)
        self.run_panel.set_tone("warn" if done and t["failed"] else "ok" if done else None)
        self.ring.set_value(t["done_bytes"], max(1, t["size"]))
        self.ring.set_done(done and not self._cancelled)
        self.run_bar.set_value(t["done_bytes"], max(1, t["size"]))
        amount = fmt.size(t["size"]) if t["known"] else "…"
        if empty:
            subtitle = "Duplicates wallpaper folders so a playlist shows them more often"
        elif running:
            subtitle = f"{self._job_number()} of {len(self._run_ids)} jobs · started {fmt.clock(self._started)}"
        elif done:
            subtitle = f"{t['jobs']} jobs · {fmt.clock(self._started)} → {fmt.clock(self._ended)}"
        else:
            subtitle = f"{sum(r['state'] == 'queued' for r in rows)} jobs waiting · {amount}"
        self.set_subtitle(subtitle)
        self.set_nav_state(NavState.status("paused" if running and self.engine.is_paused() else "working" if running else "idle"))
        paused = running and self.engine.is_paused()
        if running:
            title = ("Stopping…" if self._cancelled else "Pause requested · after this file" if paused
                     else f"Copying {self._job_number()} of {len(self._run_ids)} jobs")
            detail = f"{fmt.size(t['done_bytes'])} of {amount}"
            if self._speed and not paused and not self._cancelled:
                detail += " · " + fmt.left(max(0, t["size"] - t["done_bytes"]) / self._speed)
            self.run_title.set_title(title)
            self.run_title.set_subtitle(detail)
            self.run_meta.setText(f"started {fmt.clock(self._started)} · "
                                  + (f"{fmt.size(self._speed)}/s · " if self._speed and not paused else "")
                                  + f"{t['done']} done · {t['failed']} failed")
        elif done:
            self.run_title.set_title("Copying stopped" if self._cancelled else "All jobs finished")
            self.run_title.set_subtitle(f"{t['done_files']} of {t['files']} files · "
                                        f"{fmt.size(t['done_bytes'])} of {amount}")
            for i, value in enumerate((t["done"], t["failed"], fmt.duration(self._elapsed),
                                       fmt.size(max(0, t["done_bytes"] - self._baseline_bytes) / self._elapsed) + "/s" if self._elapsed else fmt.DASH)):
                self.metrics.set_value(i, value)
        free = " · ".join(f"free on {d['drive'].rstrip(chr(92) + chr(47) + chr(58))}: {fmt.size(d['free'])}" for d in self.drives)
        if running or done:
            self.footer.set_text(f"{t['done_files']} of {t['files']} files copied")
            self.footer.set_note("verify each copy is on" if self.verify.isChecked() else "verification is off")
        else:
            self.footer.set_text(f"{t['jobs']} jobs · {t['files'] if t['known'] else '…'} files · {amount}")
            self.footer.set_note(free or "checking free space…")
        errors = [r for r in rows if r["state"] in ("failed", "stopped")]
        self.problem.setVisible(bool(errors))
        if errors:
            self.problem.set_title(f"{len(errors)} unfinished job" + ("s" if len(errors) != 1 else ""))
            self.problem.set_body("\n".join(f"{r['name']}: {r['reason']}" for r in errors[:2])
                                  + (f"\n{len(errors) - 2} more; select their rows for details." if len(errors) > 2 else ""))
        warnings = [f"{d['drive']} needs {fmt.size(d['needed'])}; {fmt.size(d['free'])} is free"
                    for d in self.drives if d["needed"] > d["free"]]
        warnings += [f"{r['name']}: {r['reason']}" for r in rows if r["state"] == "queued" and r["reason"]]
        self.space_warning.setVisible(bool(warnings) and not running)
        self.space_warning.set_body("\n".join(warnings[:2])
                                    + (f"\n{len(warnings) - 2} more; see row details." if len(warnings) > 2 else ""))
        self.notices.setVisible(bool(errors or warnings))
        self.model.columns = tuple(replace(c, title="RESULT" if done else "PROGRESS") if i == 4 else c
                                   for i, c in enumerate(self.model.columns))
        if [r["id"] for r in self.model.items()] != [r["id"] for r in rows]:
            self.model.set_rows(rows)
        else:
            self.model._items = rows
            if rows:
                self.model.dataChanged.emit(self.model.index(0, 0), self.model.index(len(rows) - 1, 6))
        self.table.setMaximumHeight(len(rows) * theme.COPIER_ROW_HEIGHT
                                    + self.table.horizontalHeader().sizeHint().height() + theme.SP_4)
        self.table.horizontalHeader().viewport().update()
        self.table.viewport().update()
        self.known["Reserve"].set_text(getattr(self.config, "source", "") or "Not set")
        self.known["myprojects"].set_text(getattr(self.config, "destination", "") or "Not set")

    def _log(self, text):
        if self._job:
            self._job.log_text(text)
        kind = "error" if "[ERROR]" in text else "done" if "[COPY]" in text else "info"
        self.log.append(datetime.now(), kind, text)

    def _say(self, tone, message):
        self.messages.append((tone, message))
        toasts = getattr(self.window(), "toasts", None)
        if toasts:
            toasts.show_toast(message, tone, action="Show", on_action=lambda: self.navigate.emit("copier"))

    def open_destination(self):
        row = self.model.item_at(self.table.currentIndex().row())
        path = (row["destination"] if row else None) or self.destination.path()
        if path and self._fixture is None:
            external.popen(["explorer", path])

    def on_shown(self):
        self._on_screen = True
        if self._fixture is not None or self.state == "running":
            return
        path = self.settings.get("copier", "dest", DEFAULT_COPIER_DEST)
        if path != self.destination.path():
            self.destination.set_path(path)
            self.queue_changed()
        else:
            self._render()

    def on_hidden(self):
        self._on_screen = False

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.DragEnter, QEvent.DragMove, QEvent.Drop):
            paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
            if paths:
                event.acceptProposedAction()
                if event.type() == QEvent.Drop:
                    self.add_folders(paths)
                return True
        if watched is self.table and event.type() == QEvent.KeyPress and event.key() == Qt.Key_Delete:
            self._row_action(self.table.currentIndex().row(), 6, "remove")
            return True
        return super().eventFilter(watched, event)

    def dragEnterEvent(self, event):
        if any(u.isLocalFile() for u in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        self.add_folders([u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()])
        event.acceptProposedAction()

    def frame_fixture(self, state):
        fixture = json.loads((Path(__file__).resolve().parents[2] / "tests/fixtures/ui/copier.json").read_text(encoding="utf-8"))
        jobs = []
        if state == "running":
            done = sum(row["running"].get("done_bytes", 0) for row in fixture["jobs"])
            size = sum(row["size"] for row in fixture["jobs"])
            jobs = [dict(tool="copier", title="Copier",
                         phase=f"2 of {len(fixture['jobs'])} jobs · {fmt.size(fixture['speed'])}/s",
                         done=done, total=size, count_text=f"{fmt.size(done)} / {fmt.size(size)}")]
        return {"frame": "idle", "jobs": jobs,
                "nav": {"copier": {"kind": "status", "text": "working" if state == "running" else "idle"}}}

    def load_fixture(self, state):
        if state not in self.FIXTURES:
            raise KeyError(state)
        fixture = json.loads((Path(__file__).resolve().parents[2] / "tests/fixtures/ui/copier.json").read_text(encoding="utf-8"))
        self._fixture = fixture
        self._scan_timer.stop()
        self._revision += 1
        self.jobs, self.rows = [], {}
        self.destination.set_path(fixture["destination"])
        if self.config:
            self.config.source = fixture["reserve"]
            self.config.destination = fixture["myprojects"]
        self.verify.setChecked(state in ("running", "done"))
        self.drives = [dict(drive="X:", free=fixture["free"], needed=0)]
        for spec in ([] if state == "empty" else fixture["jobs"]):
            job = engine.CopyJob(spec["sources"], spec["copies"], spec.get("destination"))
            self.jobs.append(job)
            row = job.snapshot()
            row.update(files=spec["files"], size=spec["size"])
            if state in ("running", "done"):
                row.update(spec[state])
            job.state = row["state"]
            self.rows[job.id] = row
        self._run_ids = {j.id for j in self.jobs}
        self._current = self.jobs[1].id if state == "running" else None
        self._started = datetime.fromisoformat(fixture["started"])
        self._ended = datetime.fromisoformat(fixture["ended"])
        self._elapsed = fixture["elapsed"]
        self._speed = fixture["speed"] if state == "running" else 0
        self.state = "queue" if state == "no-space" else state
        if state == "no-space":
            self.drives = [dict(drive="X:", free=1048576, needed=sum(r["size"] for r in self.rows.values()))]
        self.log.set_live(state == "running")
        self.log.set_expanded(False)
        self._render()
