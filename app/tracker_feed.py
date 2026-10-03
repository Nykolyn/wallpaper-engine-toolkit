"""The tracker, looked at whenever Wallpaper Engine does something.

One feed runs in each process that shows the count: the tray has one, and the
toolkit window — a program of its own, see `window_instance` — builds another
for its Tracker tab. Two are cheap now that a look is taken only when Wallpaper
Engine writes; sharing one meant building the window inside the tray, where a
window that froze took the count down with it.

When to look is `PollSchedule`'s business (see engines/tracker.py): every time
Wallpaper Engine rewrites playliststate.bin or config.json, and on a slow
heartbeat otherwise.

**Every look is taken on a thread of the feed's own.** A look reads config.json
and probes the wallpapers of the playlist, on whatever disk they are on, and
the wallpaper disk here is a 12 TB HDD that sleeps: the tray's hang log caught
54 s inside `probe_current` while it spun up, with the tray and the window
frozen all that time. So the worker owns the `Tracker`, its `EngineFiles` and
its `PollSchedule`, and nothing else touches them. It stats the two files once
a second, looks when they say to, and hands copies back by queued signal: the
results, the error, whether there is a state file to follow, and what it read
of the two files. The countdowns (`WallpaperTimer`) follow that through
`files`, a copy on the window's thread that never reads the disk itself. A new
cycle, rebuilding from file times and a different config.json go through the
same worker, in the order they were asked for.
"""
from __future__ import annotations

import ctypes
import threading
import traceback
from collections import deque
from ctypes import wintypes
from typing import Callable

from PySide6.QtCore import QObject, Signal

from .engines.tracker import (
    DEFAULT_WE_CONFIG, HEARTBEAT_SECONDS, WATCH_SECONDS, PollSchedule, Progress, Tracker)
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


class _Requests:
    """What the window's thread has asked of the worker, and whether to stop.

    Apart from the feed, so that the feed's `destroyed` can still stop the
    worker once the feed itself is gone.
    """

    def __init__(self):
        self._lock = threading.Condition()
        self._queue: deque = deque()
        self._look = False
        self._stopping = False

    def put(self, request: tuple | None = None, *, look: bool = False) -> None:
        with self._lock:
            if request is not None:
                self._queue.append(request)
            # Asked for while one runs, any number of looks make one more.
            self._look = self._look or look
            self._lock.notify()

    def stop(self, *_args) -> None:
        with self._lock:
            self._stopping = True
            self._lock.notify()

    @property
    def stopping(self) -> bool:
        with self._lock:
            return self._stopping

    def take(self, timeout: float) -> tuple[list[tuple], bool] | None:
        """(requests, whether a look was asked for), after waiting up to
        `timeout` for either; None once told to stop."""
        with self._lock:
            if not self._queue and not self._look and not self._stopping:
                self._lock.wait(timeout)
            if self._stopping:
                return None
            requests, self._queue = list(self._queue), deque()
            look, self._look = self._look, False
            return requests, look


class TrackerFeed(QObject):
    """One tracker, looked at on a worker when Wallpaper Engine writes, shared by its viewers."""

    updated = Signal()            # `results`, `error` and `following` are new
    config_changed = Signal()     # a different config.json: `config_path` and `files` are new
    failed = Signal(str)          # a look or a request raised: the traceback, once per kind

    # From the worker, each queued to the window's thread. All but the answers
    # carry the generation of the config.json they are about, so nothing from
    # before a `use_config` lands after it.
    _built = Signal(int, str, object)              # generation, config.json, atime_ok
    _files_read = Signal(int, object)              # generation, EngineFiles.reads()
    _looked = Signal(int, object, object, bool)    # generation, results, error, following
    _answered = Signal(int, object)                # request, its answer

    def __init__(self, config_path: str | None, heartbeat: int = HEARTBEAT_SECONDS,
                 parent: QObject | None = None, *,
                 make_tracker: Callable[[str | None], Tracker] = Tracker):
        super().__init__(parent)
        self.results: list[Progress] = []
        self.error: str | None = None
        self.following = False
        # Whether NTFS keeps access times, once the worker has asked Windows.
        self.atime_ok: bool | None = None
        self._heartbeat = heartbeat
        self._generation = 0
        self._waiting: dict[int, Callable[[object], None] | None] = {}
        self._asked = 0
        # Where the worker will look, until it says otherwise. It only differs
        # when nothing is chosen and Steam's own place has no config.json, and
        # finding one elsewhere means stat'ing paths on every drive.
        self._take_config(config_path or DEFAULT_WE_CONFIG or "")

        self._built.connect(self._on_built)
        self._files_read.connect(self._on_files_read)
        self._looked.connect(self._on_looked)
        self._answered.connect(self._on_answered)

        self._requests = _Requests()
        self.destroyed.connect(self._requests.stop)
        self._requests.put(("build", self._generation, config_path, heartbeat))
        self._thread = threading.Thread(target=self._work, args=(self._requests, make_tracker),
                                        daemon=True, name="tracker feed")
        self._thread.start()

    def _take_config(self, config_path: str) -> None:
        self.config_path = config_path
        # Filled in from the worker's reads; never refreshed here.
        self.files = EngineFiles(config_path)

    # ----- asked for on the window's thread --------------------------------

    def refresh(self) -> None:
        """Look now, whatever the schedule says. `updated` says when it has."""
        self._requests.put(look=True)

    def reset(self, monitor: str, done: Callable[[object], None] | None = None) -> None:
        """Start a monitor's count again (`Tracker.reset`), then look. `done`
        gets None, or the exception it raised, once that look has landed."""
        self._requests.put(("reset", self._wait(done), monitor))

    def rebuild(self, done: Callable[[object], None] | None = None) -> None:
        """Work the counts out again from file times (`Tracker.rebuild`), then
        look. `done` gets the number recovered, or the exception it raised,
        once that look has landed."""
        self._requests.put(("rebuild", self._wait(done)))

    def set_heartbeat(self, seconds: int) -> None:
        self._heartbeat = seconds
        self._requests.put(("heartbeat", seconds))

    def use_config(self, config_path: str) -> None:
        self._generation += 1
        self._take_config(config_path)
        self.results, self.error, self.following = [], None, False
        self._requests.put(("build", self._generation, config_path, self._heartbeat))
        self.config_changed.emit()

    def stop(self) -> None:
        """Let the worker go once its current look is done."""
        self._requests.stop()

    def _wait(self, done: Callable[[object], None] | None) -> int:
        self._asked += 1
        self._waiting[self._asked] = done
        return self._asked

    # ----- what comes back --------------------------------------------------

    def _on_built(self, generation: int, config_path: str, atime_ok) -> None:
        if generation != self._generation:
            return
        self.atime_ok = atime_ok
        if config_path != self.config_path:
            self._take_config(config_path)
            self.config_changed.emit()

    def _on_files_read(self, generation: int, reads) -> None:
        if generation == self._generation:
            self.files.take_reads(reads)

    def _on_looked(self, generation: int, results, error, following: bool) -> None:
        if generation != self._generation:
            return
        self.results, self.error, self.following = results, error, following
        self.updated.emit()

    def _on_answered(self, asked: int, answer) -> None:
        done = self._waiting.pop(asked, None)
        if done is not None:
            done(answer)

    # ----- the worker -------------------------------------------------------

    def _work(self, requests: _Requests, make_tracker: Callable[[str | None], Tracker]) -> None:
        """The only code that touches the Tracker, its files and its schedule."""
        tracker: Tracker | None = None
        schedule: PollSchedule | None = None
        generation = 0
        shared = None           # what the window's thread was last told of the files
        told: set[str] = set()  # failures already reported

        def send(signal, *args) -> None:
            try:
                signal.emit(*args)
            except RuntimeError:        # the feed went while this looked
                requests.stop()

        def report(what: str) -> None:
            text = f"tracker feed: {what} failed:\n{traceback.format_exc()}"
            traceback.print_exc()
            if text not in told:
                told.add(text)
                send(self.failed, text)

        def share_files() -> None:
            nonlocal shared
            files = tracker.files
            # The versions stand for config and decks, which are not compared
            # themselves: config.json is megabytes once parsed.
            key = (files.config_version, files.state_version, files.state_ok,
                   files.config_error)
            if key != shared:
                shared = key
                send(self._files_read, generation, files.reads())

        def look() -> None:
            try:
                schedule.looked()
                results = tracker.poll()
            except Exception:           # noqa: BLE001 — the next look tries again
                report("a look")
                return
            share_files()
            send(self._looked, generation, results, tracker.error, schedule.following)

        while True:
            taken = requests.take(WATCH_SECONDS)
            if taken is None:
                return
            asked, wanted = taken
            for request in asked:
                kind = request[0]
                if kind == "build":
                    _, generation, path, heartbeat = request
                    try:
                        tracker = make_tracker(path)
                    except Exception:   # noqa: BLE001
                        report("reading the settings")
                        tracker = schedule = None
                        continue
                    schedule = PollSchedule(tracker.files, heartbeat)
                    shared = None
                    send(self._built, generation, tracker.config_path, tracker.atime_ok)
                    wanted = True
                elif kind == "heartbeat":
                    if schedule is not None:
                        schedule.heartbeat = request[1]
                elif kind in ("reset", "rebuild"):
                    answer = None
                    if tracker is not None:
                        try:
                            answer = (tracker.reset(request[2]) if kind == "reset"
                                      else tracker.rebuild())
                        except Exception as err:    # noqa: BLE001 — the asker says so
                            report(f"a {kind}")
                            answer = err
                        # Looked at now rather than with the rest, so the answer
                        # lands after the count it changed.
                        look()
                    send(self._answered, request[1], answer)
            if tracker is None or requests.stopping:
                continue
            try:
                due = schedule.due()
            except Exception:           # noqa: BLE001
                report("watching the files")
                continue
            share_files()
            if wanted or due:
                look()
