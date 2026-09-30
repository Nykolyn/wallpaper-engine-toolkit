"""The tracker, looked at whenever Wallpaper Engine does something.

One feed runs in each process that shows the count: the tray has one, and the
toolkit window — a program of its own, see `window_instance` — builds another
for its Tracker tab. Two are cheap now that a look is taken only when Wallpaper
Engine writes; sharing one meant building the window inside the tray, where a
window that froze took the count down with it.

When to look is `PollSchedule`'s business (see engines/tracker.py): every time
Wallpaper Engine rewrites playliststate.bin or config.json, and on a slow
heartbeat otherwise. This only drives it from the Qt event loop and tells
whoever is listening.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QObject, QTimer, Signal

from .engines.tracker import (
    HEARTBEAT_SECONDS, WATCH_SECONDS, PollSchedule, Progress, Tracker)
from .engines.wallpaper_timer import EngineFiles

# The tray tracker holds this mutex for as long as it runs (tracker_tray), so
# a second one does not start, and the window can tell it is counting.
TRAY_MUTEX = "Local\\WallpaperEngineToolkitTracker"
_SYNCHRONIZE = 0x00100000


def tray_running() -> bool:
    """Whether the tray tracker is running for this user — whether the count
    goes on with the window closed."""
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except (AttributeError, OSError):
        return False
    kernel32.OpenMutexW.restype = wintypes.HANDLE
    kernel32.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenMutexW(_SYNCHRONIZE, False, TRAY_MUTEX)
    if not handle:
        return False
    kernel32.CloseHandle(handle)
    return True


def heartbeat_setting(settings) -> int:
    """The heartbeat in seconds, from `data/suite.json`."""
    try:
        return max(60, int(settings.get("tracker", "heartbeat", HEARTBEAT_SECONDS)))
    except (TypeError, ValueError):
        return HEARTBEAT_SECONDS


class TrackerFeed(QObject):
    """One tracker, looked at when Wallpaper Engine writes, shared by its viewers."""

    updated = Signal()            # `results` and `tracker.error` are new
    config_changed = Signal()     # a different config.json: `files` is a new object

    def __init__(self, config_path: str | None, heartbeat: int = HEARTBEAT_SECONDS,
                 parent: QObject | None = None):
        super().__init__(parent)
        self.results: list[Progress] = []
        self._build(config_path, heartbeat)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._watch)
        self._timer.start(WATCH_SECONDS * 1000)
        # The first look waits for the event loop rather than holding up
        # whoever is being built — on a cold disk it can take a while.
        QTimer.singleShot(0, self._watch)

    def _build(self, config_path: str | None, heartbeat: int) -> None:
        self.tracker = Tracker(config_path)
        self.schedule = PollSchedule(self.tracker.files, heartbeat)

    @property
    def files(self) -> EngineFiles:
        return self.tracker.files

    @property
    def following(self) -> bool:
        return self.schedule.following

    def _watch(self) -> None:
        if self.schedule.due():
            self.refresh()

    def refresh(self) -> None:
        """Look now, whatever the schedule says."""
        self.schedule.looked()
        self.results = self.tracker.poll()
        self.updated.emit()

    def set_heartbeat(self, seconds: int) -> None:
        self.schedule.heartbeat = seconds

    def use_config(self, config_path: str) -> None:
        self._build(config_path, self.schedule.heartbeat)
        self.results = []
        self.config_changed.emit()
        self.refresh()
