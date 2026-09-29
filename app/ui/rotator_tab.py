"""Rotator tab — the standalone Wallpaper Rotator re-hosted as a QWidget.

This is the original MainWindow, adapted to a plain QWidget, and hosted as a
page of the window until the Rotator page (redesign step 10) replaces it. The
five inner tabs (Rotate / Reserve / Transferred / History / Duplicates) and
all behaviour are unchanged; the Rotator/Config/History/worker logic is used
verbatim. Its folders and batch size are set on the Settings page, which
shares this tab's `Config`; the tab shows them, and says where to change them.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTabWidget, QPushButton,
    QLabel, QPlainTextEdit, QFormLayout,
    QGroupBox, QMessageBox, QTreeWidget, QTreeWidgetItem, QCheckBox,
)

from .. import animations, theme
from ..engines import playlist_refresh
from ..engines.rotator.config import Config, History, RunRecord
from ..engines.rotator.core import (
    Rotator, list_subfolders, ProgressEvent, folder_is_set, folder_problem,
)
from ..engines.rotator.worker import (
    RotationWorker, DuplicateActionWorker, ReserveScanWorker, CleanupWorker,
)
from ..services import begin
from .cleanup_dialog import CleanupDialog
from .kit import LinkButton, PathField, format as fmt, icon
from .widgets import FolderListPanel

# What the status line says a rotation is doing, by the engine's phase.
_PHASE_TEXT = {
    "scan": "checking every folder for a project.json",
    "delete": "deleting the folders you confirmed",
    "return": "returning folders to the reserve",
    "select": "drawing folders at random",
    "move": "moving folders into myprojects",
    "playlist": "Wallpaper Engine's playlist",
    "replace": "moving duplicates into the reserve",
    "done": "finishing",
    "cancelled": "stopping",
}


def _log_kind(e: ProgressEvent) -> str:
    """The LogPanel kind of an engine event, for the log file."""
    message = e.message
    if message.startswith("DUPLICATE"):
        return "dupe"
    if message.startswith(("SKIP", "Skipping")):
        return "skip"
    if e.level == "ERROR":
        return "fail" if message.startswith("Failed") else "error"
    if e.level == "WARN":
        return "warn"
    if message.startswith("Moved"):
        return "moved"
    if message.startswith("Returned"):
        return "returned"
    if message.startswith("Deleted"):
        return "deleted"
    return {"done": "done", "select": "step"}.get(e.phase, "info")


class RotatorTab(QWidget):
    # "Change in Settings": the window goes to the Settings page.
    settings_requested = Signal()

    def __init__(self, config: Config | None = None):
        super().__init__()
        # The window hands in the Config the Settings page edits too.
        self.config = config if config is not None else Config.load()
        self.history = History.load()
        self.rotation_worker: RotationWorker | None = None
        self.dup_worker: DuplicateActionWorker | None = None
        self.scan_worker: ReserveScanWorker | None = None
        self.cleanup_worker: CleanupWorker | None = None
        self._rotate_after_check = False
        self._dup_busy = False
        # The check, clean-up or rotation under way, and the duplicates action,
        # as the status line and the log files know them (app/services).
        self._job = None
        self._job_phase = ""
        self._dup_job = None
        self._cancelled = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("rotatorTabs")      # the stylesheet's rules for them
        outer.addWidget(self.tabs)

        self._build_rotate_tab()
        self._build_reserve_tab()
        self._build_transferred_tab()
        self._build_history_tab()
        self._build_duplicates_tab()

        self.refresh_all()

    # ---------------------------------------------------------------- Rotate
    def _build_rotate_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)

        settings = QGroupBox("Next run")
        form = QFormLayout(settings)
        # Set on the Settings page; shown here, checked on a worker.
        self.show_source = PathField(self.config.source, editable=False,
                                     placeholder="Reserve not set")
        self.show_dest = PathField(self.config.destination, editable=False,
                                   placeholder="myprojects not set")
        self.show_dup = PathField(self.config.duplicates, editable=False,
                                  placeholder="Duplicates folder not set")
        self.count_label = QLabel()
        change = LinkButton("Change in Settings")
        change.clicked.connect(self.settings_requested.emit)
        count_row = QHBoxLayout()
        count_row.addWidget(self.count_label)
        count_row.addStretch()
        count_row.addWidget(change)
        form.addRow("Reserve:", self.show_source)
        form.addRow("myprojects:", self.show_dest)
        form.addRow("Duplicates:", self.show_dup)
        form.addRow("Folders per run:", count_row)
        self.in_refresh = QCheckBox(
            "Rebuild the playlist from the new set and start it over")
        self.in_refresh.setChecked(self.config.refresh_playlist)
        self.in_refresh.setToolTip(
            "Wallpaper Engine is closed for the move and started again afterwards —\n"
            "a few seconds without wallpapers. The playlist is the one made of what\n"
            "is in myprojects now, found by its contents whatever it is called.")
        self.in_refresh.toggled.connect(self._set_refresh)
        form.addRow("Wallpaper Engine:", self.in_refresh)
        layout.addWidget(settings)
        self._show_config()

        btn_row = QHBoxLayout()
        self.start_btn = QPushButton(icon("play", "text.onAccent"), "Start Rotation")
        self.start_btn.setMinimumHeight(44)
        theme.make_accent(self.start_btn)
        self.start_btn.clicked.connect(self.start_rotation)
        self.check_btn = QPushButton("Check folders")
        self.check_btn.setMinimumHeight(44)
        self.check_btn.setToolTip(
            "Look for folders without a project.json — Wallpaper Engine cannot "
            "show those, and rotating one in loses a slot in the playlist.\n"
            "Runs on its own here; it is also step 1 of every rotation.")
        self.check_btn.clicked.connect(self.check_folders)
        self.cancel_btn = QPushButton(icon("stop"), "Stop")
        self.cancel_btn.setMinimumHeight(44)
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel_rotation)
        btn_row.addWidget(self.start_btn, 3)
        btn_row.addWidget(self.check_btn, 2)
        btn_row.addWidget(self.cancel_btn, 1)
        layout.addLayout(btn_row)

        self.phase_label = QLabel("Idle")
        self.phase_label.setStyleSheet(theme.label_style("text", size=13, weight=600))
        layout.addWidget(self.phase_label)

        self.progress = animations.SmoothProgressBar()
        self.progress.setTextVisible(True)
        layout.addWidget(self.progress)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        self.log.setStyleSheet(theme.console_style())
        layout.addWidget(self.log, 1)

        self.tabs.addTab(w, "Rotate")

    def _show_config(self) -> None:
        """What the Settings page set, as this tab shows it."""
        self.show_source.set_path(self.config.source)
        self.show_dest.set_path(self.config.destination)
        self.show_dup.set_path(self.config.duplicates)
        self.count_label.setText(fmt.count(self.config.count))

    def _set_refresh(self, on: bool) -> None:
        if self.rotation_worker is not None and self.rotation_worker.isRunning():
            return      # the run has its answer already; the box is off meanwhile
        self.config.refresh_playlist = on
        self.config.save()

    # --------------------------------------------------------------- Reserve
    def _build_reserve_tab(self):
        self.reserve_panel = FolderListPanel("Reserve folders")
        self.reserve_panel.refresh_btn.clicked.connect(self.refresh_reserve)
        self.tabs.addTab(self.reserve_panel, "Reserve")

    def _build_transferred_tab(self):
        self.transferred_panel = FolderListPanel("In myprojects")
        self.transferred_panel.refresh_btn.clicked.connect(self.refresh_transferred)
        self.tabs.addTab(self.transferred_panel, "Transferred")

    # --------------------------------------------------------------- History
    def _build_history_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        header = QHBoxLayout()
        self.history_summary = QLabel("History: 0 runs")
        self.history_summary.setStyleSheet(theme.label_style("text", weight=600))
        header.addWidget(self.history_summary)
        header.addStretch()
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh_history)
        header.addWidget(refresh)
        layout.addLayout(header)

        self.history_notice = QLabel()
        self.history_notice.setWordWrap(True)
        self.history_notice.setStyleSheet(theme.label_style("warn", weight=600))
        self.history_notice.hide()
        layout.addWidget(self.history_notice)

        self.history_tree = QTreeWidget()
        self.history_tree.setHeaderLabels(["Run / Folder", "Details"])
        self.history_tree.setColumnWidth(0, 360)
        layout.addWidget(self.history_tree)
        self.tabs.addTab(w, "History")

    # ------------------------------------------------------------ Duplicates
    def _build_duplicates_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        self.dup_panel = FolderListPanel("Duplicates", selectable=True)
        self.dup_panel.refresh_btn.clicked.connect(self.refresh_duplicates)
        layout.addWidget(self.dup_panel, 1)

        actions = QHBoxLayout()
        self.del_sel_btn = QPushButton("Delete selected")
        self.del_all_btn = QPushButton("Delete all")
        self.mv_sel_btn = QPushButton("Move && replace selected → reserve")
        self.mv_all_btn = QPushButton("Move && replace all → reserve")
        self.del_sel_btn.clicked.connect(lambda: self._dup_action("delete", False))
        self.del_all_btn.clicked.connect(lambda: self._dup_action("delete", True))
        self.mv_sel_btn.clicked.connect(lambda: self._dup_action("replace", False))
        self.mv_all_btn.clicked.connect(lambda: self._dup_action("replace", True))
        for b in (self.del_sel_btn, self.del_all_btn, self.mv_sel_btn, self.mv_all_btn):
            actions.addWidget(b)
        layout.addLayout(actions)

        self.dup_progress = animations.SmoothProgressBar()
        self.dup_progress.setVisible(False)
        layout.addWidget(self.dup_progress)

        self.tabs.addTab(w, "Duplicates")

    # ------------------------------------------------------------- Refreshing
    def refresh_all(self):
        self._show_config()
        self.refresh_reserve()
        self.refresh_transferred()
        self.refresh_history()
        self.refresh_duplicates()

    def refresh_reserve(self):
        self._list_folder(self.reserve_panel, "Reserve folders", self.config.source)

    def refresh_transferred(self):
        self._list_folder(self.transferred_panel, "In myprojects", self.config.destination)

    def refresh_duplicates(self):
        self._list_folder(self.dup_panel, "Duplicates", self.config.duplicates)
        if not self._dup_busy:
            self._set_dup_buttons(True)

    @staticmethod
    def _list_folder(panel: FolderListPanel, title: str, path: str):
        # An unset folder lists nothing — list_subfolders sees to that — and
        # says why, rather than looking like an empty folder.
        panel.set_title(title if folder_is_set(path) else f"{title} (not set)")
        panel.set_names(list_subfolders(path))

    def refresh_history(self):
        self.history_tree.clear()
        runs = self.history.runs
        self.history_summary.setText(f"History: {len(runs)} runs")
        # What happened to the file on disk, if anything did: put back from a
        # snapshot, set aside unread. It stays up until the window is closed.
        self.history_notice.setText(self.history.notice)
        self.history_notice.setVisible(bool(self.history.notice))
        for r in runs:
            top = QTreeWidgetItem([
                f"{r.timestamp}   (run {r.id})",
                f"moved {r.moved_count} · dup {r.duplicate_count} · returned {r.returned} "
                f"· failed {len(r.failed)}" + ("  · HISTORY RESET" if r.history_reset else ""),
            ])
            top.setFirstColumnSpanned(False)
            moved_node = QTreeWidgetItem([f"Moved ({r.moved_count})", ""])
            for n in r.moved:
                moved_node.addChild(QTreeWidgetItem([n, ""]))
            top.addChild(moved_node)
            if r.duplicates:
                dup_node = QTreeWidgetItem([f"Duplicates ({len(r.duplicates)})", ""])
                for n in r.duplicates:
                    dup_node.addChild(QTreeWidgetItem([n, ""]))
                top.addChild(dup_node)
            if r.failed:
                fail_node = QTreeWidgetItem([f"Failed ({len(r.failed)})", ""])
                for n in r.failed:
                    fail_node.addChild(QTreeWidgetItem([n, ""]))
                top.addChild(fail_node)
            self.history_tree.addTopLevelItem(top)

    # ------------------------------------------------- Step 1: broken folders
    #
    # Wallpaper Engine only shows a folder that has a project.json. One without
    # is dead weight: rotated into myprojects it takes a slot and the playlist
    # comes out short with nothing to say why — which is how a run of 200 once
    # produced a playlist of 198. So every rotation looks first. Nothing is ever
    # deleted on its own: the scan is read-only, and the dialog it opens is
    # where the folders are inspected and ticked off by hand.

    def check_folders(self):
        """The check on its own, without rotating afterwards."""
        self._begin_check(then_rotate=False)

    def _begin_check(self, then_rotate: bool):
        # Both libraries, and each only once: with reserve and myprojects set to
        # the same folder every finding would be listed — and deleted — twice.
        # One that is not set is left out, not checked as the working directory.
        roots, seen, unset = [], set(), []
        for label, candidate in (("reserve", self.config.source),
                                 ("myprojects", self.config.destination)):
            problem = folder_problem(label, candidate)
            if problem:
                unset.append(problem)
                continue
            try:
                key = str(Path(candidate).resolve()).lower()
            except OSError:
                key = candidate.lower()
            if key not in seen:
                seen.add(key)
                roots.append(candidate)
        if not roots:
            QMessageBox.critical(self, "Cannot check", "\n".join(unset))
            return
        missing = [r for r in roots if not Path(r).exists()]
        if missing:
            QMessageBox.critical(self, "Cannot check",
                                 "Folder not found:\n" + "\n".join(missing))
            return
        self._rotate_after_check = then_rotate
        self._cancelled = False
        self._start_job("Checking folders", "check")
        self.log.clear()
        for problem in unset:
            event = ProgressEvent("scan", f"{problem} Not checked.", level="WARN")
            self._append_log(event)
            self._job_event(event)
        self._set_running(True)
        self.phase_label.setText("Step 1 — checking every folder for a project.json")
        self.scan_worker = ReserveScanWorker(roots)
        self.scan_worker.progress.connect(self._on_progress)
        self.scan_worker.finished_scan.connect(self._on_scan_finished)
        self.scan_worker.error.connect(self._on_error)
        self.scan_worker.start()

    def _on_scan_finished(self, broken: list, scanned: int):
        self.scan_worker = None
        self._set_running(False)
        self.phase_label.setText(
            f"Checked {scanned} folders — {len(broken)} unusable")
        if self._cancelled:
            self._end_job("stopped", "The check was stopped", journal=False)
        elif broken:
            count = len(broken)
            self._end_job("problems", f"{count} of {scanned} folders cannot be shown "
                                      "by Wallpaper Engine",
                          title=f"{count} folder{'s' * (count != 1)} Wallpaper Engine "
                                "cannot show",
                          detail=f"of {scanned} checked")
        else:
            self._end_job("clean", f"All {scanned} folders have a project.json",
                          journal=False)
        if not broken:
            if self._rotate_after_check:
                self._confirm_and_rotate()
            else:
                QMessageBox.information(
                    self, "Nothing to clean",
                    f"All {scanned} folders have a project.json.")
            return

        dialog = CleanupDialog(broken, scanned=scanned, parent=self)
        confirmed = dialog.paths() if dialog.exec() else []
        if not confirmed:
            self._append_log(ProgressEvent(
                "scan", "Cleanup skipped — nothing was deleted.", level="WARN"))
            if self._rotate_after_check:
                self._confirm_and_rotate()
            return
        self._set_running(True, cancellable=False)
        self.phase_label.setText(
            f"Step 2 — deleting {len(confirmed)} confirmed folder(s)")
        self._start_job(f"Deleting {len(confirmed)} folders", "cleanup")
        self.cleanup_worker = CleanupWorker(confirmed)
        self.cleanup_worker.progress.connect(self._on_progress)
        self.cleanup_worker.finished_action.connect(self._on_cleanup_finished)
        self.cleanup_worker.error.connect(self._on_error)
        self.cleanup_worker.start()

    def _on_cleanup_finished(self, failed: list):
        asked = len(self.cleanup_worker.paths) if self.cleanup_worker else len(failed)
        self.cleanup_worker = None
        self._set_running(False)
        deleted = asked - len(failed)
        self._end_job("problems" if failed else "clean", f"{deleted} of {asked} folders deleted",
                      title=f"{deleted} folder{'s' * (deleted != 1)} Wallpaper Engine "
                            "could not show deleted",
                      detail=f"{len(failed)} could not be deleted" if failed else "",
                      chip="Failed" if failed else None)
        self.refresh_all()
        if failed:
            QMessageBox.warning(
                self, "Deleted with errors",
                f"{len(failed)} folder(s) could not be deleted:\n"
                + "\n".join(Path(f).name for f in failed[:20]))
        if self._rotate_after_check:
            self._confirm_and_rotate()
        elif not failed:
            QMessageBox.information(self, "Done", "The folders were deleted.")

    # --------------------------------------------------------------- Rotation
    def start_rotation(self):
        rotator = Rotator(self.config, self.history)
        err = rotator.validate()
        if err:
            QMessageBox.critical(self, "Cannot start", err)
            return
        self._begin_check(then_rotate=True)

    def _confirm_and_rotate(self):
        self._rotate_after_check = False
        rotator = Rotator(self.config, self.history)
        p = rotator.preview()
        msg = (
            f"This run will:\n\n"
            f"• Return {p['returning']} folders from myprojects to reserve\n"
            f"• Leave {p['protected']} [protected] folders untouched\n"
            f"• Move {p['duplicates_expected']} duplicates to duplicated_wallpapers\n"
            f"• Then move {p['count']} random folders into myprojects\n"
            f"{self._playlist_line()}\n"
            f"Reserve after return: ~{p['projected_reserve']} folders\n"
            f"Unique not-yet-used available: {p['available_unique']}\n"
        )
        if p["will_reset"]:
            msg += "\n⚠ Not enough unique folders left — history will RESET and reuse all."
        if self.history.notice:
            msg += f"\n⚠ {self.history.notice}"
        if QMessageBox.question(self, "Start rotation?", msg) != QMessageBox.Yes:
            return

        self._set_running(True)
        self._cancelled = False
        number = len(self.history.runs) + 1
        job = self._start_job(f"Run {number}", "run")
        job.note("run.started", f"Run {number} started",
                 f"{p['returning']} to return · {p['count']} to move into myprojects")
        self.rotation_worker = RotationWorker(self.config, self.history)
        self.rotation_worker.progress.connect(self._on_progress)
        self.rotation_worker.finished_run.connect(self._on_finished)
        self.rotation_worker.error.connect(self._on_error)
        self.rotation_worker.start()

    def _playlist_line(self) -> str:
        if not self.config.refresh_playlist:
            return ""
        labels = playlist_refresh.preview(self.config.destination)
        if not labels:
            return ("• Wallpaper Engine: no playlist is made of what is in myprojects "
                    "now, so there is none to rebuild\n")
        return ("• Close Wallpaper Engine, rebuild " + ", ".join(labels)
                + " from the new set, and start it again on a fresh pass\n")

    def cancel_rotation(self):
        for worker in (self.rotation_worker, self.scan_worker):
            if worker is not None and worker.isRunning():
                event = ProgressEvent("cancel", "Stop requested — finishing current item...",
                                      level="WARN")
                self._append_log(event)
                self._job_event(event)
                self._cancelled = True
                worker.cancel()
        self.cancel_btn.setEnabled(False)

    # ------------------------------------------ reporting (app/services)

    def _start_job(self, title: str, activity: str):
        self._job = begin("rotator", title, activity=activity)
        self._job_phase = ""
        return self._job

    def _job_event(self, e: ProgressEvent) -> None:
        """Every event goes to the log file; the status line gets the phase
        and the count. A phase that starts without a count clears the last
        phase's, which would otherwise stand under the new name."""
        job = self._job
        if job is None:
            return
        job.log(_log_kind(e), e.message)
        text = _PHASE_TEXT.get(e.phase)
        if e.total:
            job.update(text, e.current, e.total)
        elif text is not None and e.phase != self._job_phase:
            job.update(text, 0, 0)
        if text is not None:
            self._job_phase = e.phase

    def _end_job(self, result: str, summary: str, **journal) -> None:
        job, self._job = self._job, None
        if job is not None:
            job.finish(result, summary, **journal)

    def _on_progress(self, e: ProgressEvent):
        self._job_event(e)
        phase_names = {
            "scan": "Step 1 — checking every folder for a project.json",
            "delete": "Step 2 — deleting the folders you confirmed",
            "return": "Phase 1/3 — Returning folders to reserve",
            "select": "Phase 2/3 — Selecting random folders",
            "move": "Phase 3/3 — Moving folders to myprojects",
            "playlist": "Wallpaper Engine's playlist",
            "done": "Complete",
            "cancelled": "Cancelled",
            "error": "Error",
        }
        if e.phase in phase_names:
            self.phase_label.setText(phase_names[e.phase])
        if e.total:
            self.progress.setMaximum(e.total)
            self.progress.setValue(e.current)
            self.progress.setFormat(f"%v / %m  ({e.phase})")
        if (e.level != "INFO" or e.current in (0,)
                or e.phase in ("select", "done", "scan", "delete", "playlist")):
            self._append_log(e)

    def _append_log(self, e: ProgressEvent):
        color = theme.level_color(e.level)
        self.log.appendHtml(
            f'<span style="color:{color}">[{e.level}] {e.message}</span>')
        self.log.moveCursor(QTextCursor.End)

    def _on_finished(self, record: RunRecord):
        self._set_running(False)
        self.phase_label.setText(
            f"Complete — moved {record.moved_count}, duplicates {record.duplicate_count}")
        self._report_run(record)
        self.refresh_all()
        summary = self.rotation_worker.playlist_summary if self.rotation_worker else []
        QMessageBox.information(
            self, "Rotation complete",
            f"Moved {record.moved_count} folders.\n"
            f"Returned {record.returned}.\n"
            f"Duplicates set aside: {record.duplicate_count}.\n"
            f"Failed: {len(record.failed)}."
            + "".join(f"\n\n{line}" for line in summary))

    def _report_run(self, record: RunRecord) -> None:
        """The run to the status line and the journal. A run stopped while
        returning folders is not in the history, so it has no number yet."""
        job = self._job
        if job is None:
            return
        runs = self.history.runs
        recorded = bool(runs) and runs[0] is record
        number = len(runs) if recorded else len(runs) + 1
        run_id = record.id if recorded else None
        failed, dups = len(record.failed), record.duplicate_count
        if self._cancelled:
            result = "stopped"
            title = f"Run {number} stopped" if recorded else "The rotation was stopped"
        elif failed:
            result = "problems"
            title = f"Run {number} finished with {failed} problem{'s' * (failed != 1)}"
        else:
            result, title = "clean", f"Run {number} finished"
        parts = [f"{record.moved_count} moved in", f"{record.returned} returned"]
        if dups:
            parts.append(f"{dups} duplicate{'s' * (dups != 1)}")
            job.note("duplicates.set_aside", f"{dups} duplicate{'s' * (dups != 1)} set aside",
                     f"moved to {self.config.duplicates}", chip="Duplicated", run=run_id)
        if failed:
            parts.append(f"{failed} failed")
        self._end_job(result, f"Moved {record.moved_count}, returned {record.returned}, "
                              f"duplicates {dups}, failed {failed}",
                      title=title, detail=" · ".join(parts),
                      chip="Failed" if failed else None, run=run_id)

    def _on_error(self, msg: str):
        job, self._job = self._job, None
        if job is not None:
            job.fail(msg)
        self._show_error(msg)

    def _show_error(self, msg: str):
        self._set_running(False)
        self.phase_label.setText("Error")
        QMessageBox.critical(self, "Rotation error", msg)

    def _set_running(self, running: bool, cancellable: bool = True):
        self.start_btn.setEnabled(not running)
        self.check_btn.setEnabled(not running)
        self.in_refresh.setEnabled(not running)
        self.cancel_btn.setEnabled(running and cancellable)
        if running:
            self.phase_label.setText("Starting...")
            self.progress.setMaximum(0)

    # ---------------------------------------------------- Duplicate actions
    def _dup_action(self, action: str, all_items: bool):
        # The buttons are off while a folder is unset, but the Settings page
        # can change a folder without this tab being refreshed in between.
        problem = folder_problem("duplicates", self.config.duplicates)
        if problem is None and action == "replace":
            problem = folder_problem("reserve", self.config.source)
        if problem:
            QMessageBox.warning(self, "Folder not set",
                                f"{problem}\nChoose it on the Rotate tab and save.")
            self.refresh_duplicates()
            return
        if all_items:
            names = list_subfolders(self.config.duplicates)
        else:
            names = self.dup_panel.selected_names()
        if not names:
            QMessageBox.information(self, "Nothing selected",
                                    "Select one or more folders first.")
            return
        if action == "delete":
            what = f"permanently delete {len(names)} folder(s) from\n{self.config.duplicates}"
        else:
            what = (f"move {len(names)} folder(s) from\n{self.config.duplicates}\n"
                    f"into the reserve\n{self.config.source}\n"
                    f"replacing any there with the same name")
        if QMessageBox.question(
            self, "Confirm", f"Are you sure you want to {what}?"
        ) != QMessageBox.Yes:
            return

        self.dup_progress.setVisible(True)
        self.dup_progress.setMaximum(len(names))
        self.dup_progress.setValue(0)
        self._dup_busy = True
        self._set_dup_buttons(False)

        count = len(names)
        if action == "delete":
            self._dup_job = begin("rotator", f"Deleting {count} duplicates",
                                  activity="duplicates_delete")
            self._dup_job.update("deleting folders in the duplicates folder", 0, count)
        else:
            self._dup_job = begin("rotator", f"Moving {count} duplicates into the reserve",
                                  activity="duplicates_return")
            self._dup_job.update("moving duplicates into the reserve", 0, count)
        self.dup_worker = DuplicateActionWorker(action, self.config, names)
        self.dup_worker.progress.connect(self._on_dup_progress)
        self.dup_worker.finished_action.connect(self._on_dup_finished)
        self.dup_worker.error.connect(self._on_dup_error)
        self.dup_worker.start()

    def _on_dup_progress(self, e: ProgressEvent):
        if e.total:
            self.dup_progress.setMaximum(e.total)
            self.dup_progress.setValue(e.current)
        if self._dup_job is not None:
            self._dup_job.log(_log_kind(e), e.message)
            if e.total:
                self._dup_job.update(None, e.current, e.total)

    def _on_dup_error(self, msg: str):
        job, self._dup_job = self._dup_job, None
        if job is not None:
            job.fail(msg)
        self._show_error(msg)

    def _on_dup_finished(self, failed: list):
        job, self._dup_job = self._dup_job, None
        if job is not None and self.dup_worker is not None:
            asked = len(self.dup_worker.names)
            done = asked - len(failed)
            what = ("deleted" if self.dup_worker.action == "delete"
                    else "moved back into the reserve")
            job.finish("problems" if failed else "clean", f"{done} of {asked} duplicates {what}",
                       title=f"{done} duplicate{'s' * (done != 1)} {what}",
                       detail=(f"{len(failed)} failed" if failed
                               else f"from {self.config.duplicates}"),
                       chip="Failed" if failed else None)
        self._dup_busy = False
        self._set_dup_buttons(True)
        self.dup_progress.setVisible(False)
        self.refresh_duplicates()
        self.refresh_reserve()
        if failed:
            QMessageBox.warning(self, "Done with errors",
                                f"{len(failed)} folder(s) failed:\n" + "\n".join(failed[:20]))
        else:
            QMessageBox.information(self, "Done", "Action completed successfully.")

    def _set_dup_buttons(self, enabled: bool):
        # Each action only while the folders it works on are set.
        dup_set = folder_is_set(self.config.duplicates)
        reserve_set = folder_is_set(self.config.source)
        for b in (self.del_sel_btn, self.del_all_btn):
            b.setEnabled(enabled and dup_set)
        for b in (self.mv_sel_btn, self.mv_all_btn):
            b.setEnabled(enabled and dup_set and reserve_set)
