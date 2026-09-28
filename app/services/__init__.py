"""The window's shared services: jobs, the activity journal, logs, the snapshot.

- `jobs.JobCenter` — what is running now; the status line, the sidebar and
  Overview read it.
- `activity.ActivityJournal` — `data/activity.jsonl`, what happened.
- `logstore.LogStore` — `data/logs/<tool>/…`, each tool's log lines.
- `snapshot.Snapshot` — the numbers Overview and the sidebar show, read off
  the GUI thread and kept.

The main window makes one `Services` and installs it; a page reports its
work through `begin()` (see `runs.py`), which finds the installed one. A tab
built without a window — in a test — reports to nothing.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QTimer

from .activity import FILE_NAME as JOURNAL_FILE, ActivityJournal, PlaylistWatch
from .jobs import Job, JobCenter
from .logstore import FOLDER_NAME as LOGS_FOLDER, LogStore
from .runs import Run, begin
from .snapshot import Snapshot

__all__ = ["Services", "install", "current", "begin", "Run", "Job", "JobCenter",
           "ActivityJournal", "LogStore", "Snapshot", "PlaylistWatch"]

_current: "Services | None" = None


class Services(QObject):
    """The four services, as one window owns them.

    `feed` is the window's TrackerFeed: the Snapshot reads the leading
    monitor from it, and `PlaylistWatch` journals what it sees.
    """

    def __init__(self, parent: QObject | None = None, *, data_dir: str | Path | None = None,
                 settings=None, feed=None):
        super().__init__(parent)
        if data_dir is None:
            from ..settings import app_data_dir
            data_dir = app_data_dir()
        self.data_dir = Path(data_dir)
        self.jobs = JobCenter(self)
        self.journal = ActivityJournal(self.data_dir / JOURNAL_FILE, self)
        self.logs = LogStore(self.data_dir / LOGS_FOLDER)
        self.snapshot = Snapshot(self, data_dir=self.data_dir, jobs=self.jobs,
                                 feed=feed, settings=settings)
        self.playlist_watch = (PlaylistWatch(feed, self.journal, settings, self)
                               if feed is not None else None)

    def start(self) -> None:
        """What the window does once it is up: the 30-day sweep of the logs, and
        the first snapshot. Both on threads; neither holds the window up."""
        self.logs.sweep_in_background()
        QTimer.singleShot(0, self.snapshot.refresh)


def install(services: Services | None) -> None:
    """Make these the services `begin()` reports to (None: none)."""
    global _current
    _current = services
    if services is not None:
        services.destroyed.connect(_forget)


def _forget(*_args) -> None:
    global _current
    _current = None


def current() -> Services | None:
    return _current
