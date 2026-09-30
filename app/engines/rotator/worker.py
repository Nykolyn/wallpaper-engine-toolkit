"""QThread workers that run filesystem operations off the UI thread.

The rotation, the retry and the playlist rebuild are `runner` classes; the
workers only put them on a thread and turn their events into signals. Every
event carries its step (`ProgressEvent.step`, an index into the worker's
`steps`) and its log kind (`ProgressEvent.kind`).
"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QThread, Signal

from .config import Config, History, RunRecord
from .core import (
    ProgressEvent, delete_folders, move_replace_to_reserve, list_duplicates,
    scan_invalid, delete_broken,
)
from .runner import PlaylistRebuild, RetryRun, RotationRun


class RotationWorker(QThread):
    """A whole rotation (`runner.RotationRun`).

    `run_id` is made before the thread starts, so the page can open the run's
    log file (`services.begin(..., run_id=worker.run_id)`) first. `steps` is
    the run's plan. Afterwards `result` is clean / problems / stopped /
    failed, `meta` the side-file entry, `playlist_summary` what happened to
    Wallpaper Engine's playlist.

    `finished_run(record)` comes whenever the run got going, however it ended;
    `error(message)` when it could not start, or failed having moved nothing.
    """
    progress = Signal(object)   # ProgressEvent
    finished_run = Signal(object)  # RunRecord
    error = Signal(str)

    def __init__(self, config: Config, history: History, *, run_id: str | None = None,
                 check_finished: datetime | None = None, started: datetime | None = None):
        super().__init__()
        self.config = config
        self.history = history
        self.job = RotationRun(config, history, run_id=run_id, progress=self.progress.emit,
                               check_finished=check_finished, started=started)
        self.run_id = self.job.run_id
        self.steps = self.job.steps

    @property
    def result(self) -> str:
        return self.job.result

    @property
    def meta(self):
        return self.job.meta

    @property
    def playlist_summary(self) -> list[str]:
        return self.job.playlist_summary

    def cancel(self):
        """Stop before the next folder."""
        self.job.cancel()

    def stop_after_step(self):
        """Finish the step under way, then stop."""
        self.job.stop_after_step()

    def run(self):
        try:
            record = self.job.execute()
        except Exception as e:  # noqa: BLE001
            self.error.emit(f"Unexpected error: {e}")
            return
        rotator = self.job.rotator
        if record is None or (self.job.result == "failed"
                              and not (rotator is not None and rotator.recorded)):
            self.error.emit(self.job.error or "The rotation could not start.")
            return
        self.finished_run.emit(record)


class RetryWorker(QThread):
    """Retries a recorded run's failures (`runner.RetryRun`), with the playlist
    rebuilt after when the run rebuilds it. `finished_retry(RetryResult)`;
    `result` as for a run; `record` is the history's own record, updated."""
    progress = Signal(object)          # ProgressEvent
    finished_retry = Signal(object)    # RetryResult
    error = Signal(str)

    def __init__(self, config: Config, history: History, record: RunRecord):
        super().__init__()
        self.job = RetryRun(config, history, record, progress=self.progress.emit)
        self.record = self.job.record
        self.run_id = self.job.run_id
        self.steps = self.job.steps

    @property
    def result(self) -> str:
        return self.job.result

    @property
    def meta(self):
        return self.job.meta

    def cancel(self):
        self.job.cancel()

    def run(self):
        try:
            outcome = self.job.execute()
        except Exception as e:  # noqa: BLE001
            self.error.emit(f"Unexpected error: {e}")
            return
        if outcome is None:
            self.error.emit(self.job.error or "The retry could not start.")
            return
        self.finished_retry.emit(outcome)


class PlaylistRebuildWorker(QThread):
    """"Rebuild playlist now" (`runner.PlaylistRebuild`): close Wallpaper
    Engine, refill the rotation's playlist from myprojects, start it again.
    Its events are the playlist step of a run: step 0 of `steps`, done / total
    over the three stages. For the Rotator's problems state and the Creator's
    "Rebuild playlist". `finished_rebuild(PlaylistRefresh)`."""
    progress = Signal(object)          # ProgressEvent
    finished_rebuild = Signal(object)  # PlaylistRefresh
    error = Signal(str)

    def __init__(self, destination: str, runs=()):
        super().__init__()
        self.job = PlaylistRebuild(destination, runs=runs, progress=self.progress.emit)
        self.steps = self.job.steps

    @property
    def result(self) -> str:
        return self.job.result

    def run(self):
        try:
            refresh = self.job.execute()
        except Exception as e:  # noqa: BLE001
            self.error.emit(f"Unexpected error: {e}")
            return
        if refresh is None:
            self.error.emit(self.job.error)
            return
        self.finished_rebuild.emit(refresh)


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


class DuplicatesListWorker(QThread):
    """What is in the duplicates folder, with sizes (`core.list_duplicates`),
    for the Duplicates dialog. `listed(list[DuplicateFolder])`; nothing when
    cancelled."""
    progress = Signal(object)          # ProgressEvent
    listed = Signal(list)
    error = Signal(str)

    def __init__(self, config: Config):
        super().__init__()
        self.duplicates = config.duplicates
        self.reserve = config.source
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            found = list_duplicates(self.duplicates, self.reserve,
                                    lambda e: self.progress.emit(e),
                                    lambda: self._cancel)
            self.listed.emit([] if self._cancel else found)
        except Exception as e:  # noqa: BLE001
            self.error.emit(f"Unexpected error while listing the duplicates: {e}")


class ReserveScanWorker(QThread):
    """Looks through the reserve for folders Wallpaper Engine could never show.

    Read-only: it deletes nothing. What it finds goes to the confirmation
    dialog, and only what is ticked there is passed to CleanupWorker. As the
    first step of a run, `step=0` tags its events like the run's.
    """
    progress = Signal(object)          # ProgressEvent
    finished_scan = Signal(list, int)  # list[BrokenFolder], folders looked at
    error = Signal(str)

    def __init__(self, roots: list[str], *, step: int | None = None):
        super().__init__()
        self.roots = roots
        self.step = step
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
                e.step = self.step
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

    def __init__(self, paths: list[str], *, step: int | None = None):
        super().__init__()
        self.paths = paths
        self.step = step

    def run(self):
        try:
            def relay(e):
                e.step = self.step
                self.progress.emit(e)
            failed = delete_broken(self.paths, relay)
            self.finished_action.emit(failed)
        except Exception as e:  # noqa: BLE001
            self.error.emit(f"Unexpected error: {e}")
