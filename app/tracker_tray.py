"""Background tracker — the counter as a tray icon.

The playlist advances whether or not the toolkit window is open, so the count is
only trustworthy if something keeps polling. This is that something: a tray
icon drawn as a progress ring, a tooltip with the per-monitor `seen/total`, and
a balloon the moment a playlist has been shown end to end — which is the cue to
run the next rotation. Clicking the icon opens the toolkit window on its Tracker
tab; the tray outlives that window and keeps counting when it is closed.

Started with ``WallpaperEngineToolkit.exe --tracker`` (or ``run_tracker.cmd``), and by
Windows itself when autostart is on.
"""
from __future__ import annotations

import ctypes
import os
import sys
import time
import traceback
from ctypes import wintypes
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import (
    QAction, QActionGroup, QColor, QFont, QIcon, QPainter, QPen, QPixmap)
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import autostart, theme
from .engines.tracker import (
    FALLBACK_SECONDS, TIME_FMT, Progress, Tracker, app_data_dir, pick_primary)
from .engines.wallpaper_timer import Countdown, WallpaperTimer
from .settings import Settings
from .tracker_feed import TrackerFeed, heartbeat_setting


# A windowed build has no console, so a start that goes wrong leaves no trace.
# Every launch writes here instead — the file is the answer to "why was there no
# tray icon after I logged in?".
LOG_PATH = app_data_dir() / "tracker.log"
LOG_KEEP_LINES = 300

# The shell launches Run entries while it is still starting, so the notification
# area can briefly not exist. Wait for it rather than giving up on the icon.
TRAY_WAIT_SECONDS = 120
TRAY_POLL_SECONDS = 2

# The countdown ring moves once a second. The playlist count is looked at when
# Wallpaper Engine rewrites its files — see TrackerFeed.
CLOCK_TICK_MS = 1000
# A restart found later than this (the tray was not running) is not news any more.
RESTART_NOTICE_SECONDS = 15 * 60
# The ring is redrawn in 2-degree steps: on a 10-minute delay that is a new icon
# every few seconds rather than every second, which the eye cannot tell apart.
RING_STEPS = 180


def log(message: str) -> None:
    """Append one line to data/tracker.log, trimming it when it grows."""
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {message}\n"
    try:
        previous = ""
        if LOG_PATH.exists():
            previous = LOG_PATH.read_text(encoding="utf-8", errors="replace")
        lines = (previous + line).splitlines(keepends=True)[-LOG_KEEP_LINES:]
        LOG_PATH.write_text("".join(lines), encoding="utf-8")
    except OSError:
        pass


def _seconds_since(stamp: str) -> float:
    """Seconds since a tracker timestamp; very large when it cannot be read."""
    try:
        return (datetime.now() - datetime.strptime(stamp, TIME_FMT)).total_seconds()
    except (TypeError, ValueError):
        return float("inf")


def _ring_colors() -> tuple[QColor, QColor, QColor]:
    """Track, running fill and paused fill for the ring, from the theme."""
    return (QColor(theme.C["border_strong"]), QColor(theme.C["accent"]),
            QColor(theme.C["muted"]))


def tray_icon(number: int | None, ring: float | None, paused: bool = False,
              finished: bool = False) -> QIcon:
    """The tray icon: a ring filled to `ring` (0..1), and `number` in the middle.

    What the two stand for is the caller's business — today the ring is the
    time left on the wallpaper and the number is how much of the playlist has
    been shown, and a display setting can swap either without touching this.
    A ring of None draws the empty track only; `paused` greys the fill;
    `finished` turns the number the "done" colour.
    """
    size = 64
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)

    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)

    margin, width = 6, 8
    rect = pix.rect().adjusted(margin, margin, -margin, -margin)
    track, running, stopped = _ring_colors()

    painter.setPen(QPen(track, width, Qt.SolidLine, Qt.FlatCap))
    painter.drawEllipse(rect)

    if ring is not None and ring > 0:
        painter.setPen(QPen(stopped if paused else running, width,
                            Qt.SolidLine, Qt.RoundCap))
        # Qt angles are 1/16th of a degree, counter-clockwise from 3 o'clock.
        painter.drawArc(rect, 90 * 16, -int(360 * 16 * min(ring, 1.0)))

    if number is not None:
        font = QFont()
        font.setPixelSize(26 if number < 100 else 22)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(theme.C["ok"] if finished else theme.C["text"]))
        painter.drawText(pix.rect(), Qt.AlignCenter, str(number))
    painter.end()

    return QIcon(pix)


class TrackerTray:
    """Follows Wallpaper Engine in the background and renders the result into the tray."""

    def __init__(self, app: QApplication):
        self.app = app
        self.settings = Settings.load()
        self.results: list[Progress] = []
        self.completed: set[str] = set()
        self.restarts_told: set[str] = set()
        self.window = None          # the toolkit window, built the first time it is asked for
        self.countdowns: dict[str, Countdown] = {}
        self._icon_key = None
        self._clock_failed = False
        self._following: bool | None = None

        # The count and the countdown read the same two files through one
        # watcher. The count does not wait on the countdown's tick, though: that
        # tick reads other processes' windows and memory, and if it ever fails
        # the count must carry on regardless.
        self.feed = TrackerFeed(self.settings.get("tracker", "we_config", None),
                                heartbeat_setting(self.settings))
        self.feed.updated.connect(self._on_update)
        self.feed.config_changed.connect(self._build_clock)
        self._build_clock()

        self.icon = QSystemTrayIcon(tray_icon(0, None))
        self.icon.setToolTip("Wallpaper Tracker")
        self.icon.activated.connect(self._on_activated)

        self.menu = QMenu()
        # Rebuilt as it opens, so the countdown in it is never half a minute old.
        self.menu.aboutToShow.connect(self._rebuild_menu)
        self.icon.setContextMenu(self.menu)
        self.icon.show()

        self.clock_timer = QTimer()
        self.clock_timer.timeout.connect(self._tick_clock)
        self.clock_timer.start(CLOCK_TICK_MS)

    @property
    def tracker(self) -> Tracker:
        return self.feed.tracker

    def _build_clock(self):
        self.clock = WallpaperTimer(self.tracker.config_path, files=self.feed.files)
        self.clock.log = log            # whether Wallpaper Engine's own timer was found

    # ------------------------------------------------------------- polling
    def refresh(self):
        self.feed.refresh()

    def _on_update(self):
        self.results = self.feed.results
        if self.feed.following != self._following:
            self._following = self.feed.following
            log("tracker: following Wallpaper Engine's playliststate.bin"
                if self._following else
                f"tracker: no readable playliststate.bin — looking every "
                f"{FALLBACK_SECONDS} s instead")
        self._announce_completions()
        self._render()

    def _tick_clock(self):
        """Advance every monitor's countdown by a second and redraw if it shows."""
        try:
            self.countdowns = self.clock.tick()
        except Exception:                          # noqa: BLE001
            # The countdown reads other processes' windows through ctypes; if
            # that ever fails the playlist count must carry on regardless.
            if not self._clock_failed:
                self._clock_failed = True
                log("countdown failed, ring disabled:\n" + traceback.format_exc())
            self.countdowns = {}
        self._render_icon()

    def _announce_completions(self):
        """Balloon once per playlist, when the last unseen item has been shown,
        and once when Wallpaper Engine starts a playlist over under the count."""
        for p in self.results:
            if (p.restarted_at and p.cycle_id not in self.restarts_told
                    and _seconds_since(p.restarted_at) < RESTART_NOTICE_SECONDS):
                self.restarts_told.add(p.cycle_id)
                self.icon.showMessage(
                    "Playlist started over",
                    f"Wallpaper Engine began “{p.playlist}” on {p.monitor} again. "
                    f"The count starts over at {p.label} (it had reached {p.restarted_from}).",
                    QSystemTrayIcon.Information, 20000)
        for p in self.results:
            if p.total and p.seen >= p.total and p.cycle_id not in self.completed:
                self.completed.add(p.cycle_id)
                self.icon.showMessage(
                    "Playlist finished",
                    f"{p.monitor} · “{p.playlist}”: all {p.total} wallpapers shown "
                    f"in {p.changes} changes. Time to rotate.",
                    QSystemTrayIcon.Information, 20000)

    # ------------------------------------------------------------ rendering
    def _primary(self) -> Progress | None:
        return pick_primary(self.results, self.settings.get("tracker", "primary", None))

    def _render(self):
        self._render_icon()
        if not self.menu.isVisible():
            self._rebuild_menu()

    def _render_icon(self):
        """The icon and its tooltip: cheap enough to run every tick."""
        primary = self._primary()
        if primary is None:
            key = ("none", self.tracker.error)
            if key != self._icon_key:
                self._icon_key = key
                self.icon.setIcon(tray_icon(0, None))
                self.icon.setToolTip((self.tracker.error or "No playlist found")[:127])
            return

        countdown = self.countdowns.get(primary.monitor)
        ring = countdown.fraction if countdown else None
        paused = bool(countdown and countdown.paused)
        finished = primary.seen >= primary.total
        step = None if ring is None else round(ring * RING_STEPS)
        # Windows truncates a tray tooltip at 128 characters, so it gets one
        # short line per monitor and nothing else; the detail is in the menu
        # and in the window a click opens.
        lines = []
        for p in self.results:
            mark = "▸" if p is primary else " "
            left = "done" if not p.remaining else f"{p.remaining} left"
            timing = self.countdowns.get(p.monitor)
            when = f" · {timing.describe()}" if timing and timing.describe() else ""
            lines.append(f"{mark} “{p.playlist}” {p.label} · {left}{when}")
        tooltip = "\n".join(lines)[:127]

        key = (primary.percent, finished, step, paused)
        if key != self._icon_key:
            self._icon_key = key
            self.icon.setIcon(tray_icon(primary.percent, ring, paused, finished))
        if tooltip != self.icon.toolTip():
            self.icon.setToolTip(tooltip)

    def _rebuild_menu(self):
        self.menu.clear()

        if self.tracker.error:
            error = self.menu.addAction(self.tracker.error)
            error.setEnabled(False)

        for p in self.results:
            entry = self.menu.addAction(
                f"{p.monitor} “{p.playlist}” — {p.label}  ({p.percent}%)")
            entry.setEnabled(False)
            timing = self.countdowns.get(p.monitor)
            if timing and timing.describe():
                line = self.menu.addAction(f"      {timing.describe()}")
                line.setEnabled(False)
        if self.results:
            self.menu.addSeparator()

        self.menu.addAction("Open Wallpaper Engine Toolkit", self.open_toolkit)
        self.menu.addAction("Refresh now", self.refresh)

        if len(self.results) > 1:
            pick = self.menu.addMenu("Show on the icon")
            group = QActionGroup(self.menu)
            group.setExclusive(True)
            primary = self._primary()
            for p in self.results:
                action = pick.addAction(f"{p.monitor} “{p.playlist}”")
                action.setCheckable(True)
                action.setChecked(p is primary)
                action.triggered.connect(lambda _c=False, m=p.monitor: self._set_primary(m))
                group.addAction(action)

        if self.results:
            reset = self.menu.addMenu("New cycle")
            for p in self.results:
                reset.addAction(f"{p.monitor} “{p.playlist}”",
                                lambda _c=False, m=p.monitor: self._reset(m))

        self.menu.addSeparator()
        auto = QAction("Start with Windows", self.menu)
        auto.setCheckable(True)
        auto.setChecked(autostart.is_enabled())
        auto.toggled.connect(self._toggle_autostart)
        self.menu.addAction(auto)
        self.menu.addSeparator()
        self.menu.addAction("Quit", self.app.quit)

    # -------------------------------------------------------------- actions
    def _toggle_autostart(self, on: bool):
        try:
            autostart.set_enabled(on)
        except OSError as e:
            self.icon.showMessage("Autostart", f"Could not change it: {e}",
                                  QSystemTrayIcon.Warning, 10000)

    def _set_primary(self, monitor: str):
        self.settings.set("tracker", "primary", monitor)
        self.settings.save()
        self._render()

    def _reset(self, monitor: str):
        self.tracker.reset(monitor)
        self.refresh()

    def _on_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.open_toolkit()

    def open_toolkit(self):
        """Bring up the Wallpaper Engine Toolkit window, on the Tracker tab.

        Built here rather than launched as a second process, so a click always
        raises the same window instead of stacking up copies. The tray keeps
        running when it is closed (quitOnLastWindowClosed is off).
        """
        if self.window is None:
            from .main_window import MainWindow      # heavy — only on demand
            # The window's Tracker tab shows this tray's feed rather than
            # polling one of its own beside it.
            self.window = MainWindow(tracker_feed=self.feed)
            for i in range(self.window.tabs.count()):
                if self.window.tabs.tabText(i) == "Tracker":
                    self.window.tabs.setCurrentIndex(i)
                    break
        self.window.show()
        self.window.setWindowState(
            (self.window.windowState() & ~Qt.WindowMinimized) | Qt.WindowActive)
        self.window.raise_()
        self.window.activateWindow()


def _claim_single_instance() -> bool:
    """False if a tray tracker is already running for this user.

    Autostart plus a manual run_tracker.cmd would otherwise put two icons in the
    tray, both polling and both writing the state file.
    """
    ERROR_ALREADY_EXISTS = 183
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # A HANDLE is 64-bit here; without this the return value is truncated to int.
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    handle = kernel32.CreateMutexW(None, False, "Local\\WallpaperEngineToolkitTracker")
    if not handle:
        return True   # cannot tell — better to run than to refuse
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        return False
    _claim_single_instance.handle = handle   # held for the process's lifetime
    return True


def _wait_for_tray() -> bool:
    """Give the notification area time to appear. True if it ever did."""
    deadline = time.monotonic() + TRAY_WAIT_SECONDS
    if QSystemTrayIcon.isSystemTrayAvailable():
        return True
    log("notification area not ready yet — waiting for it")
    while time.monotonic() < deadline:
        time.sleep(TRAY_POLL_SECONDS)
        if QSystemTrayIcon.isSystemTrayAvailable():
            log(f"notification area appeared after "
                f"{int(TRAY_WAIT_SECONDS - (deadline - time.monotonic()))}s")
            return True
    return False


def run_tray() -> int:
    """Entry point for `--tracker`: a tray-only application with no window."""
    log(f"starting: pid {os.getpid()}, {sys.executable}")
    try:
        if not _claim_single_instance():
            log("another tracker already has the tray — exiting")
            return 0

        app = QApplication(sys.argv)
        app.setApplicationName("Wallpaper Tracker")
        theme.apply(app)
        app.setQuitOnLastWindowClosed(False)

        if not _wait_for_tray():
            # Not fatal: counting is the point, and the icon re-registers by
            # itself if the shell comes back later. Quitting here would lose the
            # count instead, and a dialog at logon would be worse than useless.
            log(f"no notification area after {TRAY_WAIT_SECONDS}s — "
                f"counting on without an icon")

        tray = TrackerTray(app)   # the local reference is what keeps the icon alive
        log(f"running; tray icon visible: {tray.icon.isVisible()}")
        exit_code = app.exec()
        log(f"stopped with code {exit_code}")
        del tray
        return exit_code
    except Exception:
        log("crashed:\n" + traceback.format_exc())
        raise
