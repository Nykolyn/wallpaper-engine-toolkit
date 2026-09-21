"""The authors database: where it is, how it is backed up, and putting one back.

There is nothing to set up — the file is made the first time the Review tab
needs it — so this dialog is about the one thing a local file needs that a
hosted database did for you: copies somewhere else.

* **What is there**: how many authors, and the file they are in.
* **The second folder**: snapshots always go to ``data/authors_backup``; if a
  folder is chosen here, every one is copied there too. Empty means "no second
  copy", and the dialog says what that risks rather than insisting.
* **The backups**: every snapshot in both places, newest first, each with the
  number of authors it holds, and **Restore** for the selected one. A restore
  snapshots the current state first, so it is undone the same way it is done.

Restoring and backing up run on a thread: reading forty thousand rows and a
megabyte of gzip is a second or two, and the window must not stop for it.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QVBoxLayout)

from .. import theme
from ..engines.authors_store import (
    AuthorsStore, DbError, KEEP_DAILY, KEEP_MONTHLY, KEEP_RECENT, KEEP_WEEKLY,
    Snapshot, StoreDamaged)
from ..settings import Settings

SECTION = "review"
MIRROR_SETTING = "backup_mirror"

SNAPSHOT = Qt.UserRole + 1


def mirror_folder(settings: Settings) -> Path | None:
    """The second backup folder, or None when none is chosen."""
    chosen = str(settings.get(SECTION, MIRROR_SETTING, "") or "").strip()
    return Path(chosen) if chosen else None


def open_store(settings: Settings) -> AuthorsStore:
    """The store as the settings describe it, opened."""
    return AuthorsStore(mirror=mirror_folder(settings)).open()


def _n(count: int) -> str:
    """A count the way the rest of the window writes one: 38 897."""
    return f"{count:,}".replace(",", " ")


def _size(n: int) -> str:
    if n >= 1 << 20:
        return f"{n / (1 << 20):.1f} MB"
    return f"{max(1, n // 1024)} KB"


class _Job(QThread):
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, work, parent=None):
        super().__init__(parent)
        self._work = work

    def run(self) -> None:
        try:
            self.done.emit(self._work())
        except Exception as err:  # noqa: BLE001 — the message is the answer
            self.failed.emit(str(err))


class AuthorsDialog(QDialog):
    """The authors database and its backups."""

    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.restored = False            # the caller must drop what it read
        self._jobs: list[_Job] = []
        self.setWindowTitle("Authors database")
        self.setMinimumWidth(680)
        # Room for the wrapped explanations and a dozen backups without the
        # list being squeezed under its own buttons.
        self.resize(720, 660)
        self.store = AuthorsStore(mirror=mirror_folder(settings))

        layout = QVBoxLayout(self)

        head = QHBoxLayout()
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet(theme.label_style("text", size=13, weight=600))
        head.addWidget(self.summary, 1)
        folder = QPushButton("Open folder")
        folder.clicked.connect(lambda: self._reveal(self.store.path))
        head.addWidget(folder)
        layout.addLayout(head)
        self.where = QLabel("")
        self.where.setWordWrap(True)
        self.where.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.where.setStyleSheet(theme.label_style("faint"))
        layout.addWidget(self.where)

        layout.addSpacing(8)
        backups = QLabel("Backups")
        backups.setStyleSheet(theme.label_style("text", size=13, weight=600))
        layout.addWidget(backups)
        policy = QLabel(
            "After every change the whole database is saved as a snapshot "
            f"(gzipped JSON, readable without this app). Kept: the last "
            f"{KEEP_RECENT}, then one a day for {KEEP_DAILY} days, one a week "
            f"for {KEEP_WEEKLY} weeks and one a month for {KEEP_MONTHLY} months. "
            "Each change is also written to journal.jsonl, as it was and as it "
            "became.")
        policy.setWordWrap(True)
        policy.setStyleSheet(theme.label_style("muted"))
        layout.addWidget(policy)

        row = QHBoxLayout()
        row.addWidget(QLabel("Second copy in:"))
        self.mirror = QLineEdit()
        self.mirror.setReadOnly(True)
        self.mirror.setPlaceholderText("not set — snapshots stay in data\\authors_backup only")
        row.addWidget(self.mirror, 1)
        choose = QPushButton("Choose…")
        choose.clicked.connect(self.choose_mirror)
        row.addWidget(choose)
        self.clear_btn = QPushButton("Don't copy")
        self.clear_btn.clicked.connect(self.clear_mirror)
        row.addWidget(self.clear_btn)
        layout.addLayout(row)
        self.mirror_note = QLabel("")
        self.mirror_note.setWordWrap(True)
        layout.addWidget(self.mirror_note)

        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.setMinimumHeight(220)
        self.list.itemSelectionChanged.connect(self._selection)
        self.list.itemDoubleClicked.connect(lambda _item: self.restore_selected())
        layout.addWidget(self.list, 1)

        actions = QHBoxLayout()
        self.backup_btn = QPushButton("Back up now")
        self.backup_btn.clicked.connect(self.back_up_now)
        actions.addWidget(self.backup_btn)
        self.restore_btn = QPushButton("Restore selected…")
        self.restore_btn.setEnabled(False)
        self.restore_btn.clicked.connect(self.restore_selected)
        actions.addWidget(self.restore_btn)
        show = QPushButton("Show backup folder")
        show.clicked.connect(lambda: self._reveal(self.store.backup_dir, folder=True))
        actions.addWidget(show)
        actions.addStretch()
        layout.addLayout(actions)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet(theme.label_style("muted"))
        layout.addWidget(self.status)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._show_mirror()
        self.refresh()

    # -- what is there -----------------------------------------------------

    def refresh(self) -> None:
        """Re-read the count and the list of backups."""
        self.damaged = False
        try:
            with AuthorsStore(self.store.path, mirror=self.store.mirror) as store:
                count = store.count()
            self.summary.setStyleSheet(theme.label_style("text", size=13, weight=600))
            self.summary.setText(f"{_n(count)} authors")
        except StoreDamaged as err:
            self.damaged = True
            self.summary.setText("The database file is damaged")
            self.summary.setStyleSheet(theme.label_style("danger", size=13, weight=600))
            self._say(f"{err}. Pick the newest backup below and press Restore.", "danger")
        except DbError as err:
            self.summary.setText("The database cannot be read")
            self._say(str(err), "danger")
        size = self.store.path.stat().st_size if self.store.path.exists() else 0
        self.where.setText(f"{self.store.path}  ·  {_size(size)}" if size
                           else f"{self.store.path}  ·  not created yet")

        self.list.clear()
        snapshots = self.store.snapshots()
        for snap in snapshots:
            label = (f"{snap.taken:%Y-%m-%d  %H:%M}    "
                     f"{_n(snap.count)} authors    {_size(snap.size)}    "
                     f"{snap.where}")
            item = QListWidgetItem(label)
            item.setData(SNAPSHOT, snap)
            item.setToolTip(str(snap.path))
            self.list.addItem(item)
        if not snapshots:
            item = QListWidgetItem("No backups yet — the first is made with the "
                                   "first change, or by “Back up now”.")
            item.setFlags(Qt.NoItemFlags)
            self.list.addItem(item)
        self._selection()

    def _show_mirror(self) -> None:
        chosen = self.store.mirror
        self.mirror.setText(str(chosen) if chosen else "")
        self.clear_btn.setEnabled(chosen is not None)
        if chosen is None:
            self.mirror_note.setStyleSheet(theme.label_style("warn"))
            self.mirror_note.setText(
                "Backups are on the same disk as the database, which protects "
                "against mistakes but not against that disk failing. A folder "
                "on another drive, or one OneDrive or Dropbox syncs, fixes that.")
        elif not chosen.is_dir():
            self.mirror_note.setStyleSheet(theme.label_style("warn"))
            self.mirror_note.setText(
                "That folder cannot be reached right now — an unplugged drive? "
                "Changes are still saved and backed up in data\\authors_backup; "
                "the copy is made again with the next change once it is back.")
        else:
            self.mirror_note.setStyleSheet(theme.label_style("faint"))
            self.mirror_note.setText("Every snapshot is copied there as well, and "
                                     "pruned by the same rules.")

    def _selection(self) -> None:
        item = self.list.currentItem()
        self.restore_btn.setEnabled(bool(item and item.data(SNAPSHOT)))

    def _say(self, text: str, kind: str = "muted") -> None:
        self.status.setStyleSheet(theme.label_style(kind))
        self.status.setText(text)

    # -- the second folder -------------------------------------------------

    def choose_mirror(self) -> None:
        start = str(self.store.mirror or Path.home())
        chosen = QFileDialog.getExistingDirectory(
            self, "Where to keep a second copy of every backup", start)
        if not chosen:
            return
        target = Path(chosen)
        if target.resolve() == self.store.backup_dir.resolve():
            self._say("That is the backup folder itself — pick another one, "
                      "ideally on a different drive.", "warn")
            return
        self.settings.set(SECTION, MIRROR_SETTING, str(target))
        self.settings.save()
        self.store.mirror = target
        self._show_mirror()
        self._say("Saved. The next snapshot is copied there; “Back up now” makes "
                  "one straight away.")

    def clear_mirror(self) -> None:
        self.settings.set(SECTION, MIRROR_SETTING, "")
        self.settings.save()
        self.store.mirror = None
        self._show_mirror()
        self._say("No second copy. Files already in that folder are left where they are.")
        self.refresh()

    # -- backing up and restoring ------------------------------------------

    def _run(self, work, done, label: str) -> None:
        self._say(label)
        self.backup_btn.setEnabled(False)
        self.restore_btn.setEnabled(False)
        job = _Job(work, self)
        job.done.connect(done)
        job.failed.connect(lambda message: self._say(message, "danger"))
        job.finished.connect(self._job_finished)
        self._jobs.append(job)
        job.start()

    def _job_finished(self) -> None:
        self.backup_btn.setEnabled(True)
        self.refresh()

    def back_up_now(self) -> None:
        store_path, mirror = self.store.path, self.store.mirror

        def work():
            with AuthorsStore(store_path, mirror=mirror) as store:
                path = store.snapshot()
                return path, list(store.warnings)

        def done(result) -> None:
            path, warnings = result
            if path is None:
                self._say("The database is empty; there is nothing to back up yet.")
            elif warnings:
                self._say(f"Saved {Path(path).name}, but " + "; ".join(warnings), "warn")
            else:
                self._say(f"Saved {Path(path).name}"
                          + (" and copied it to the second folder." if mirror else "."),
                          "ok")

        self._run(work, done, "Backing up…")

    def restore_selected(self) -> None:
        item = self.list.currentItem()
        snap: Snapshot | None = item.data(SNAPSHOT) if item else None
        if snap is None:
            return
        try:
            with AuthorsStore(self.store.path) as store:
                now = f"the {_n(store.count())} authors in the database now"
        except DbError:
            now = "whatever is left of the damaged database"
        answer = QMessageBox.warning(
            self, "Restore a backup",
            f"Replace {now} with the {_n(snap.count)} in the backup of "
            f"{snap.taken:%d %B %Y, %H:%M}?\n\n"
            "Everything written since that backup — visit dates, new authors, "
            "renames — goes back to how it was then. What is there now is "
            "backed up first, so this can be undone the same way.",
            QMessageBox.Ok | QMessageBox.Cancel, QMessageBox.Cancel)
        if answer != QMessageBox.Ok:
            return
        store_path, mirror = self.store.path, self.store.mirror

        def work():
            if self.damaged:
                # A damaged file cannot be opened to be replaced; it is set
                # aside whole, never deleted, and a fresh one takes its place.
                # Its journal goes with it: SQLite would otherwise take a
                # leftover journal for the new file's and "roll it back" in.
                n = 1
                while store_path.with_name(f"{store_path.name}.damaged{n}").exists():
                    n += 1
                for suffix in ("", "-journal", "-wal", "-shm"):
                    part = store_path.with_name(store_path.name + suffix)
                    if part.exists():
                        os.replace(part, part.with_name(f"{part.name}.damaged{n}"))
            with AuthorsStore(store_path, mirror=mirror) as store:
                return store.restore(snap.path)

        def done(report: dict) -> None:
            self.restored = True
            self._say(f"Restored {_n(report['written'])} authors from "
                      f"{snap.path.name}.", "ok")

        self._run(work, done, f"Restoring {snap.path.name}…")

    # -- odds and ends -----------------------------------------------------

    def _reveal(self, path: Path, folder: bool = False) -> None:
        """Explorer at a folder, or at a file with the file selected."""
        try:
            if folder:
                path.mkdir(parents=True, exist_ok=True)
                os.startfile(str(path))  # noqa: S606
            elif path.exists():
                subprocess.Popen(f'explorer /select,"{path}"')
            else:
                os.startfile(str(path.parent))  # noqa: S606
        except OSError as err:
            self._say(f"Could not open {path}: {err}", "warn")

    def closeEvent(self, event) -> None:
        for job in self._jobs:
            job.wait(3000)
        super().closeEvent(event)

    def reject(self) -> None:
        for job in self._jobs:
            job.wait(3000)
        super().reject()
