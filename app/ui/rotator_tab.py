"""Rotator tab — the standalone Wallpaper Rotator re-hosted as a QWidget.

This is the original MainWindow, adapted from a QMainWindow to a plain QWidget
so it can live inside the toolkit's top-level tab strip. The five inner tabs
(Rotate / Reserve / Transferred / History / Duplicates) and all behaviour are
unchanged; the Rotator/Config/History/worker logic is used verbatim.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTabWidget, QPushButton,
    QLabel, QPlainTextEdit, QFormLayout, QLineEdit, QSpinBox,
    QGroupBox, QMessageBox, QTreeWidget, QTreeWidgetItem, QFileDialog, QCheckBox,
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
from .cleanup_dialog import CleanupDialog
from .kit import icon
from .widgets import FolderListPanel


class RotatorTab(QWidget):
    def __init__(self):
        super().__init__()
        self.config = Config.load()
        self.history = History.load()
        self.rotation_worker: RotationWorker | None = None
        self.dup_worker: DuplicateActionWorker | None = None
        self.scan_worker: ReserveScanWorker | None = None
        self.cleanup_worker: CleanupWorker | None = None
        self._rotate_after_check = False
        self._dup_busy = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.tabs = animations.FadingTabWidget()
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

        settings = QGroupBox("Configuration")
        form = QFormLayout(settings)
        self.in_source = QLineEdit(self.config.source)
        self.in_dest = QLineEdit(self.config.destination)
        self.in_dup = QLineEdit(self.config.duplicates)
        self.in_count = QSpinBox()
        self.in_count.setRange(1, 100000)
        self.in_count.setValue(self.config.count)

        form.addRow("Reserve (source):", self._path_row(self.in_source))
        form.addRow("myprojects (dest):", self._path_row(self.in_dest))
        form.addRow("Duplicates folder:", self._path_row(self.in_dup))
        form.addRow("Folders per run:", self.in_count)
        self.in_refresh = QCheckBox(
            "Rebuild the playlist from the new set and start it over")
        self.in_refresh.setChecked(self.config.refresh_playlist)
        self.in_refresh.setToolTip(
            "Wallpaper Engine is closed for the move and started again afterwards —\n"
            "a few seconds without wallpapers. The playlist is the one made of what\n"
            "is in myprojects now, found by its contents whatever it is called.")
        form.addRow("Wallpaper Engine:", self.in_refresh)
        save_btn = QPushButton("Save settings")
        save_btn.clicked.connect(self._save_settings)
        form.addRow("", save_btn)
        layout.addWidget(settings)

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

    def _path_row(self, line_edit: QLineEdit) -> QWidget:
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(line_edit)
        browse = QPushButton("...")
        browse.setMaximumWidth(36)
        browse.clicked.connect(lambda: self._browse_into(line_edit))
        h.addWidget(browse)
        return row

    def _browse_into(self, line_edit: QLineEdit):
        d = QFileDialog.getExistingDirectory(self, "Choose folder", line_edit.text())
        if d:
            line_edit.setText(d.replace("/", "\\"))

    def _save_settings(self):
        self.config.source = self.in_source.text().strip()
        self.config.destination = self.in_dest.text().strip()
        self.config.duplicates = self.in_dup.text().strip()
        self.config.count = self.in_count.value()
        self.config.refresh_playlist = self.in_refresh.isChecked()
        self.config.save()
        self.refresh_all()
        QMessageBox.information(self, "Saved", "Settings saved.")

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
        self._save_settings_silent()
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
        self.log.clear()
        for problem in unset:
            self._append_log(ProgressEvent("scan", f"{problem} Not checked.",
                                           level="WARN"))
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
        self.cleanup_worker = CleanupWorker(confirmed)
        self.cleanup_worker.progress.connect(self._on_progress)
        self.cleanup_worker.finished_action.connect(self._on_cleanup_finished)
        self.cleanup_worker.error.connect(self._on_error)
        self.cleanup_worker.start()

    def _on_cleanup_finished(self, failed: list):
        self.cleanup_worker = None
        self._set_running(False)
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
        self._save_settings_silent()
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
        if QMessageBox.question(self, "Start rotation?", msg) != QMessageBox.Yes:
            return

        self._set_running(True)
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
                self._append_log(ProgressEvent(
                    "cancel", "Stop requested — finishing current item...",
                    level="WARN"))
                worker.cancel()
        self.cancel_btn.setEnabled(False)

    def _on_progress(self, e: ProgressEvent):
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
        self.refresh_all()
        summary = self.rotation_worker.playlist_summary if self.rotation_worker else []
        QMessageBox.information(
            self, "Rotation complete",
            f"Moved {record.moved_count} folders.\n"
            f"Returned {record.returned}.\n"
            f"Duplicates set aside: {record.duplicate_count}.\n"
            f"Failed: {len(record.failed)}."
            + "".join(f"\n\n{line}" for line in summary))

    def _on_error(self, msg: str):
        self._set_running(False)
        self.phase_label.setText("Error")
        QMessageBox.critical(self, "Rotation error", msg)

    def _set_running(self, running: bool, cancellable: bool = True):
        self.start_btn.setEnabled(not running)
        self.check_btn.setEnabled(not running)
        self.cancel_btn.setEnabled(running and cancellable)
        if running:
            self.phase_label.setText("Starting...")
            self.progress.setMaximum(0)

    def _save_settings_silent(self):
        self.config.source = self.in_source.text().strip()
        self.config.destination = self.in_dest.text().strip()
        self.config.duplicates = self.in_dup.text().strip()
        self.config.count = self.in_count.value()
        self.config.refresh_playlist = self.in_refresh.isChecked()
        self.config.save()

    # ---------------------------------------------------- Duplicate actions
    def _dup_action(self, action: str, all_items: bool):
        # The buttons are off while a folder is unset, but the settings can be
        # saved by Check folders or Start without this tab being refreshed.
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

        self.dup_worker = DuplicateActionWorker(action, self.config, names)
        self.dup_worker.progress.connect(self._on_dup_progress)
        self.dup_worker.finished_action.connect(self._on_dup_finished)
        self.dup_worker.error.connect(self._on_error)
        self.dup_worker.start()

    def _on_dup_progress(self, e: ProgressEvent):
        if e.total:
            self.dup_progress.setMaximum(e.total)
            self.dup_progress.setValue(e.current)

    def _on_dup_finished(self, failed: list):
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
