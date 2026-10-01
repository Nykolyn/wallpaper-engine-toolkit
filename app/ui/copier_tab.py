"""Copier tab — Qt front-end for the verbatim CopyEngine.

Mirrors the behaviour of the standalone Wallpaper Engine Copier:
  * the destination folder, set on the Settings page and shown here
  * a table of "folder path" / "copy count" jobs
  * add / paste-from-clipboard / remove / clear, inline count editing
  * folders sent from other pages (`add_folders`: the Tracker's Send to Copier)
  * drag & drop folders straight into the table
  * background copy with live log, progress bar and MB/s speed readout
"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QPushButton,
    QLabel, QPlainTextEdit, QTableWidget, QTableWidgetItem,
    QFileDialog, QMessageBox, QHeaderView, QApplication,
)

from .. import animations, theme
from ..engines.copier import CopyEngine, CopyJob
from ..services import begin
from ..settings import (
    Settings, DEFAULT_COPIER_DEST, DEFAULT_COPIER_COUNT,
)
from ..workers import CopierBridge
from .kit import LinkButton, PathField, icon

SECTION = "copier"


class JobTable(QTableWidget):
    """Two-column (path, count) table that accepts dropped folders."""

    def __init__(self, on_folders_dropped):
        super().__init__(0, 2)
        self._on_folders_dropped = on_folders_dropped
        self.setHorizontalHeaderLabels(["Folder path", "Copies"])
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.setSelectionBehavior(QTableWidget.SelectRows)
        self.setEditTriggers(
            QTableWidget.DoubleClicked | QTableWidget.EditKeyPressed
        )
        self.setAcceptDrops(True)
        self.setDragDropMode(QTableWidget.DropOnly)

    # ----- drag & drop: accept only folders ----------------------------------
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        folders = []
        for url in event.mimeData().urls():
            p = os.path.normpath(url.toLocalFile())
            if os.path.isdir(p):
                folders.append(p)
        if folders:
            self._on_folders_dropped(folders)
            event.acceptProposedAction()


class CopierTab(QWidget):
    # "Change in Settings": the window goes to the Settings page.
    settings_requested = Signal()

    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
        # The copy under way, as the status line and the log files know it.
        self._job = None
        self._dest = ""
        self._cancelled = False

        # Bridge engine callbacks -> Qt signals (queued onto the UI thread).
        self.bridge = CopierBridge()
        self.bridge.log.connect(self._log)
        self.bridge.progress.connect(self._on_progress)
        self.bridge.speed.connect(self._on_speed)
        self.bridge.finished.connect(self._on_finished)

        self.engine = CopyEngine(
            log=self.bridge.log.emit,
            progress=self.bridge.progress.emit,
            speed=self.bridge.speed.emit,
            finished=self.bridge.finished.emit,
        )

        self._build_ui()

    # ----- UI -----------------------------------------------------------------
    def _build_ui(self):
        layout = QVBoxLayout(self)

        # --- Destination ---
        dest_box = QGroupBox("Destination folder")
        dest_row = QHBoxLayout(dest_box)
        # Set on the Settings page; shown here, checked on a worker.
        self.dest_field = PathField(self._destination(), editable=False,
                                    placeholder="No destination set")
        change = LinkButton("Change in Settings")
        change.clicked.connect(self.settings_requested.emit)
        dest_row.addWidget(self.dest_field, 1)
        dest_row.addWidget(change)
        layout.addWidget(dest_box)

        # --- List management buttons ---
        btn_row = QHBoxLayout()
        add_btn = QPushButton("+ Add folder")
        paste_btn = QPushButton("Paste from clipboard")
        remove_btn = QPushButton("Remove selected")
        clear_btn = QPushButton("Clear list")
        add_btn.clicked.connect(self._add_folder)
        paste_btn.clicked.connect(self._paste_clipboard)
        remove_btn.clicked.connect(self._remove_selected)
        clear_btn.clicked.connect(self._clear_list)
        for b in (add_btn, paste_btn, remove_btn, clear_btn):
            btn_row.addWidget(b)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # --- Job table ---
        table_box = QGroupBox("Folders to copy")
        table_layout = QVBoxLayout(table_box)
        self.table = JobTable(self._add_rows)
        table_layout.addWidget(self.table)
        layout.addWidget(table_box, 1)

        # --- Progress + speed ---
        prog_row = QHBoxLayout()
        self.progress = animations.SmoothProgressBar()
        self.speed_label = QLabel("")
        self.speed_label.setMinimumWidth(90)
        self.speed_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        prog_row.addWidget(self.progress, 1)
        prog_row.addWidget(self.speed_label)
        layout.addLayout(prog_row)

        # --- Start / Cancel ---
        action_row = QHBoxLayout()
        self.start_btn = QPushButton(icon("play", "text.onAccent"), "Start copying")
        self.start_btn.setMinimumHeight(40)
        theme.make_accent(self.start_btn)
        self.cancel_btn = QPushButton(icon("close"), "Cancel")
        self.cancel_btn.setMinimumHeight(40)
        self.cancel_btn.setEnabled(False)
        self.start_btn.clicked.connect(self._start)
        self.cancel_btn.clicked.connect(self._cancel)
        action_row.addWidget(self.start_btn, 3)
        action_row.addWidget(self.cancel_btn, 1)
        layout.addLayout(action_row)

        # --- Log ---
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        self.log.setStyleSheet(theme.console_style())
        layout.addWidget(self.log, 1)

    def _destination(self) -> str:
        return self.settings.get(SECTION, "dest", DEFAULT_COPIER_DEST)

    def on_shown(self) -> None:
        """The destination may have changed on the Settings page meanwhile."""
        if self.dest_field.path() != self._destination():
            self.dest_field.set_path(self._destination())

    # ----- list actions -------------------------------------------------------
    def _add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Choose folder to copy")
        if d:
            self._add_row(os.path.normpath(d), DEFAULT_COPIER_COUNT)

    def _add_row(self, path: str, count: int):
        row = self.table.rowCount()
        self.table.insertRow(row)
        path_item = QTableWidgetItem(path)
        path_item.setFlags(path_item.flags() & ~Qt.ItemIsEditable)
        count_item = QTableWidgetItem(str(count))
        count_item.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(row, 0, path_item)
        self.table.setItem(row, 1, count_item)

    def add_folders(self, paths, count: int = DEFAULT_COPIER_COUNT) -> int:
        """Add folders to the list, each for `count` copies, leaving out any
        already listed (or given twice); returns how many were added. Other
        pages send folders here (the Tracker's Send to Copier). Reads nothing:
        a folder that is not there is the copy's to report."""
        listed = {os.path.normcase(os.path.normpath(self.table.item(row, 0).text()))
                  for row in range(self.table.rowCount())}
        added = 0
        for path in paths:
            path = os.path.normpath(str(path))
            key = os.path.normcase(path)
            if key in listed:
                continue
            listed.add(key)
            self._add_row(path, count)
            added += 1
        if added:
            self._log(f"[INFO]  Added folders: {added}")
        return added

    def folders(self) -> list[tuple[str, str]]:
        """The list as it stands: (folder, copies as written) per row."""
        return [(self.table.item(row, 0).text(), self.table.item(row, 1).text())
                for row in range(self.table.rowCount())]

    def _add_rows(self, folders):
        for p in folders:
            self._add_row(p, DEFAULT_COPIER_COUNT)
        self._log(f"[INFO]  Added folders: {len(folders)}")

    def _paste_clipboard(self):
        data = QApplication.clipboard().text()
        if not data.strip():
            QMessageBox.warning(self, "Clipboard", "Clipboard is empty.")
            return
        added = 0
        for line in data.splitlines():
            line = line.strip().strip('"')
            if not line:
                continue
            path, count = self._parse_line(line)
            if path:
                self._add_row(path, count)
                added += 1
        self._log(f"[INFO]  Added from clipboard: {added}")

    @staticmethod
    def _parse_line(line: str):
        """'C:\\path\\folder 5' or 'C:\\path\\folder' -> (path, count)."""
        line = line.strip().strip('"')
        parts = line.rsplit(" ", 1)
        if len(parts) == 2 and parts[1].isdigit():
            return os.path.normpath(parts[0].strip().strip('"')), int(parts[1])
        return os.path.normpath(line), DEFAULT_COPIER_COUNT

    def _remove_selected(self):
        for idx in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(idx)

    def _clear_list(self):
        self.table.setRowCount(0)

    # ----- run / cancel -------------------------------------------------------
    def _collect_jobs(self):
        jobs = []
        for row in range(self.table.rowCount()):
            path = self.table.item(row, 0).text()
            raw = self.table.item(row, 1).text().strip()
            count = int(raw) if raw.isdigit() and int(raw) > 0 else DEFAULT_COPIER_COUNT
            jobs.append(CopyJob(path, count))
        return jobs

    def _start(self):
        if self.engine.is_running():
            return
        dest = self._destination().strip()
        if not dest:
            QMessageBox.warning(self, "Destination",
                                "Choose a destination folder on the Settings page first.")
            return
        jobs = self._collect_jobs()
        if not jobs:
            QMessageBox.warning(self, "Empty list", "Add at least one folder.")
            return

        try:
            os.makedirs(dest, exist_ok=True)
        except OSError as exc:
            QMessageBox.critical(self, "Error", f"Could not create destination:\n{exc}")
            return

        self.log.clear()
        self.progress.setValue(0)
        self.start_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        folders = len(jobs)
        self._dest, self._cancelled = dest, False
        self._job = begin("copier", f"Copying {folders} folder{'s' * (folders != 1)}",
                          activity="copy")
        self.engine.start(jobs, dest)

    def _cancel(self):
        self.engine.cancel()
        self._cancelled = True
        self._log("[CANCEL] Cancellation requested — finishing current file…")
        self.cancel_btn.setEnabled(False)

    # ----- engine signal handlers --------------------------------------------
    def _on_progress(self, done: int, total: int):
        self.progress.setMaximum(max(total, 1))
        self.progress.setValue(done)
        if self._job is not None:
            self._job.update(f"making {total} copies", done, total)

    def _on_speed(self, mbps: float):
        self.speed_label.setText(f"{mbps:.1f} MB/s" if mbps else "")

    def _on_finished(self, report):
        self.start_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.speed_label.setText("")
        total_ok = sum(e["ok"] for e in report)
        total_req = sum(e["requested"] for e in report)
        self._log(f"\n[FINISH] Done: {total_ok}/{total_req} copies.")
        self._report(total_ok, total_req, sum(e["failed"] for e in report))

    def _report(self, made: int, asked: int, failed: int) -> None:
        job, self._job = self._job, None
        if job is None:
            return
        if self._cancelled:
            result = "stopped"
        elif failed or made < asked:
            result = "failed" if made == 0 else "problems"
        else:
            result = "clean"
        summary = f"{made} of {asked} copies made"
        detail = f"in {self._dest}" + (f" · {failed} failed" if failed else "")
        job.finish(result, summary, detail=detail, chip="Failed" if failed else None)

    def _log(self, text: str):
        self.log.appendPlainText(text)
        if self._job is not None:
            self._job.log_text(text)
