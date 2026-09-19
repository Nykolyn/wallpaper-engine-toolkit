"""QThread workers that run filesystem operations off the UI thread."""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from ..playlist_refresh import PlaylistRefresh
from .config import Config, History, RunRecord
from .core import (
    Rotator, ProgressEvent, delete_folders, move_replace_to_reserve,
    scan_invalid, delete_broken,
)


class RotationWorker(QThread):
    progress = Signal(object)   # ProgressEvent
    finished_run = Signal(object)  # RunRecord
    error = Signal(str)

    def __init__(self, config: Config, history: History):
        super().__init__()
        self.config = config
        self.history = history
        self._cancel = False
        # What happened to Wallpaper Engine's playlist, for the summary.
        self.playlist_summary: list[str] = []

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            rotator = Rotator(self.config, self.history)
            err = rotator.validate()
            if err:
                self.error.emit(err)
                return
            relay = lambda e: self.progress.emit(e)  # noqa: E731
            refresh = None
            if self.config.refresh_playlist:
                refresh = PlaylistRefresh(self.config.destination, relay)
            try:
                if refresh is not None:
                    refresh.prepare()
                record = rotator.run(progress=relay, cancelled=lambda: self._cancel)
            finally:
                # Also what starts Wallpaper Engine again, so it runs whatever
                # became of the rotation.
                if refresh is not None:
                    refresh.finish(completed=rotator.completed)
                    self.playlist_summary = refresh.summary
            self.finished_run.emit(record)
        except Exception as e:  # noqa: BLE001
            self.error.emit(f"Unexpected error: {e}")


class DuplicateActionWorker(QThread):
    """Runs delete or move-replace on duplicates in the background."""
    progress = Signal(object)   # ProgressEvent
    finished_action = Signal(list)  # list of failures
    error = Signal(str)

    def __init__(self, action: str, config: Config, names: list[str]):
        super().__init__()
        self.action = action  # "delete" | "replace"
        self.config = config
        self.names = names

    def run(self):
        try:
            cb = lambda e: self.progress.emit(e)
            if self.action == "delete":
                failed = delete_folders(self.config.duplicates, self.names, cb)
            elif self.action == "replace":
                failed = move_replace_to_reserve(
                    self.config.duplicates, self.config.source, self.names, cb)
            else:
                self.error.emit(f"Unknown action: {self.action}")
                return
            self.finished_action.emit(failed)
        except Exception as e:  # noqa: BLE001
            self.error.emit(f"Unexpected error: {e}")


class ReserveScanWorker(QThread):
    """Looks through the reserve for folders Wallpaper Engine could never show.

    Read-only: it deletes nothing. What it finds goes to the confirmation
    dialog, and only what is ticked there is passed to CleanupWorker.
    """
    progress = Signal(object)          # ProgressEvent
    finished_scan = Signal(list, int)  # list[BrokenFolder], folders looked at
    error = Signal(str)

    def __init__(self, roots: list[str]):
        super().__init__()
        self.roots = roots
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            found, scanned = [], 0
            # The scan reports its own size in the progress events; catching it
            # here saves listing tens of thousands of folders a second time.
            last = {"total": 0}

            def relay(e):
                if e.total:
                    last["total"] = e.total
                self.progress.emit(e)

            for root in self.roots:
                last["total"] = 0
                found += scan_invalid(root, progress=relay,
                                      cancelled=lambda: self._cancel)
                scanned += last["total"]
                if self._cancel:
                    break
            if self._cancel:
                self.finished_scan.emit([], 0)
            else:
                self.finished_scan.emit(found, scanned)
        except Exception as e:  # noqa: BLE001
            self.error.emit(f"Unexpected error while checking the folders: {e}")


class CleanupWorker(QThread):
    """Deletes the folders confirmed in the cleanup dialog."""
    progress = Signal(object)          # ProgressEvent
    finished_action = Signal(list)     # list of failures
    error = Signal(str)

    def __init__(self, paths: list[str]):
        super().__init__()
        self.paths = paths

    def run(self):
        try:
            failed = delete_broken(self.paths, lambda e: self.progress.emit(e))
            self.finished_action.emit(failed)
        except Exception as e:  # noqa: BLE001
            self.error.emit(f"Unexpected error: {e}")
