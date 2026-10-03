"""Background tracker — the counter as a tray icon.

The playlist advances whether or not the toolkit window is open, so the count is
only trustworthy if something keeps polling. This is that something: a tray
icon drawn as a ring (`tray_icon`), a tooltip with the per-monitor count, a menu
that says in words what the ring says (`tray_menu`, `tray_words`), and a balloon
the moment a playlist has been shown end to end — which is the cue to run the
next rotation. Clicking the icon opens the toolkit window on its Tracker page —
as a program of its own, so the tray keeps counting whatever the window does,
and when it is closed.

Started with ``WallpaperEngineToolkit.exe --tracker`` (or ``run_tracker.cmd``), and by
Windows itself when autostart is on.

**Kept light.** This process starts from a logon task at below-normal CPU and
low disk priority, and lives all day. It draws no stylesheet, imports none of
the kit, and its GUI thread never touches the wallpaper disks: the menu reads
two small files in the data folder when it opens, and nothing it does waits on
``schtasks`` (the old menu asked it for the autostart switch every time it was
rebuilt, and the hang log caught that taking 29 seconds).
"""
from __future__ import annotations

import ctypes
import os
import sys
import time
import traceback
from ctypes import wintypes
from datetime import datetime

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from . import theme, tray_words as words, window_instance
from .branding import DISPLAY_NAME
from .hang_watch import HangWatch
from .engines.tracker import (
    FALLBACK_SECONDS, TIME_FMT, Progress, Tracker, app_data_dir, pick_primary)
from .engines.wallpaper_timer import Countdown, WallpaperTimer
from .settings import Settings
from .tracker_feed import TRAY_MUTEX, TrackerFeed, heartbeat_setting
from .tray_icon import (
    FINISHED, RING_STEPS, RUNNING, TaskbarTheme, tray_icon, UNKNOWN)
from .tray_menu import OPEN, QUIT, REVIEW, ROTATE, SETTINGS, TrayMenu, build_model


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
# The ring is what ``tray_icon.RING_STEPS`` says: 2-degree steps. The design
# (gate G5 B) would redraw the icon once a minute, on a playlist change; gate G5
# A keeps the ring as the time left on the current wallpaper, which does move by
# the second, so the redraws are instead made only when a step is crossed: on a
# 10-minute delay that is a new icon every few seconds, never one a second.

# What a balloon was about, for what a click on it should open.
BALLOON_FINISHED, BALLOON_RESTARTED = "finished", "restarted"


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


class TrackerTray:
    """Follows Wallpaper Engine in the background and renders the result into the tray."""

    def __init__(self, app: QApplication):
        self.app = app
        self.settings = Settings.load()
        self.results: list[Progress] = []
        self.completed: set[str] = set()
        self.restarts_told: set[str] = set()
        self.countdowns: dict[str, Countdown] = {}
        self._icon_key = None
        self._clock_failed = False
        self._following: bool | None = None
        self._balloon: str | None = None

        # The count and the countdown read the same two files through one
        # watcher. The count does not wait on the countdown's tick, though: that
        # tick reads other processes' windows and memory, and if it ever fails
        # the count must carry on regardless.
        self.feed = TrackerFeed(self.settings.get("tracker", "we_config", None),
                                heartbeat_setting(self.settings))
        self.feed.updated.connect(self._on_update)
        self.feed.config_changed.connect(self._build_clock)
        self._build_clock()

        # Which colours the icon wears depends on the taskbar's, which Windows
        # can change under a running tray (and does, at sunset, for some).
        self.taskbar = TaskbarTheme()
        self.taskbar.changed.connect(self._taskbar_changed)
        self.taskbar.listen(app)

        self.icon = QSystemTrayIcon(tray_icon(UNKNOWN, light=self.taskbar.light))
        self.icon.setToolTip(DISPLAY_NAME)
        self.icon.activated.connect(self._on_activated)
        self.icon.messageClicked.connect(self._on_message_clicked)

        self.menu = TrayMenu()
        # Built as it opens, so what it says is never half a minute old, and the
        # two files it reads are read only when somebody looks.
        self.menu.aboutToShow.connect(self._rebuild_menu)
        self.menu.chosen.connect(self._on_chosen)
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
    def _on_update(self):
        self.results = self.feed.results
        if self.feed.following != self._following:
            self._following = self.feed.following
            log("tracker: following Wallpaper Engine's playliststate.bin"
                if self._following else
                f"tracker: no readable playliststate.bin — looking every "
                f"{FALLBACK_SECONDS} s instead")
        self._announce_completions()
        self._render_icon()

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
        self.taskbar.poll()
        self._render_icon()

    def _taskbar_changed(self, _light: bool):
        self._icon_key = None
        self._render_icon()

    def _announce_completions(self):
        """Balloon once per playlist, when the last unseen item has been shown,
        and once when Wallpaper Engine starts a playlist over under the count."""
        for p in self.results:
            if (p.restarted_at and p.cycle_id not in self.restarts_told
                    and _seconds_since(p.restarted_at) < RESTART_NOTICE_SECONDS):
                self.restarts_told.add(p.cycle_id)
                if p.previous_finished:
                    # A pass that ran to its end. Wallpaper Engine may shuffle the
                    # next one as it draws the last wallpaper, so the finished
                    # cycle is never seen whole — this is then the only place
                    # the news can come from, and it must come once.
                    if p.previous_id not in self.completed:
                        self.completed.add(p.previous_id)
                        self._say_finished(p.monitor, int(p.restarted_from.split("/")[-1]))
                    continue
                self._say_restarted(p)
        for p in self.results:
            if p.total and p.seen >= p.total and p.cycle_id not in self.completed:
                self.completed.add(p.cycle_id)
                self._say_finished(p.monitor, p.total)

    def _say_finished(self, monitor: str, total: int):
        title, body = words.finished_balloon(monitor, total, words.rotation_batch())
        self._say(BALLOON_FINISHED, title, body, tray_icon(FINISHED, light=self.taskbar.light))

    def _say_restarted(self, p: Progress):
        title, body = words.restarted_balloon(p.monitor, p.seen, p.total)
        share = p.seen / p.total if p.total else None
        self._say(BALLOON_RESTARTED, title, body,
                  tray_icon(RUNNING, share, p.percent, light=self.taskbar.light))

    def _say(self, kind: str, title: str, body: str, icon):
        """One of the two balloons; a click on it opens the page it is about."""
        self._balloon = kind
        self.icon.showMessage(title, body, icon, 20000)

    # ------------------------------------------------------------ rendering
    def _primary(self) -> Progress | None:
        # The window's Settings page chooses it too.
        self.settings.reload_if_changed()
        return pick_primary(self.results, self.settings.get("tracker", "primary", None))

    def _reading(self) -> tuple[Progress | None, str, float | None, int | None]:
        """The lead monitor, and what the icon shows of it: state, ring, number."""
        primary = self._primary()
        countdown = self.countdowns.get(primary.monitor) if primary else None
        state, fraction = words.icon_state(primary, countdown)
        return primary, state, fraction, (primary.percent if primary else None)

    def _render_icon(self):
        """The icon and its tooltip: cheap enough to run every tick."""
        primary, state, fraction, number = self._reading()
        step = None if fraction is None else round(fraction * RING_STEPS)
        light = self.taskbar.light
        key = (state, step, number, light)
        if key != self._icon_key:
            self._icon_key = key
            self.icon.setIcon(tray_icon(state, fraction, number, light=light))
        # Windows truncates a tray tooltip at 128 characters, so it gets one
        # short line per monitor and nothing else; the detail is in the menu
        # and in the window a click opens.
        tooltip = words.tooltip(self.results, primary, self.countdowns,
                                self.tracker.error or "")
        if tooltip != self.icon.toolTip():
            self.icon.setToolTip(tooltip)

    def _rebuild_menu(self):
        primary, state, fraction, number = self._reading()
        line = words.header_line(state, primary, self.tracker.error or "")
        self.menu.set_model(build_model(
            state, fraction, number, line,
            run_hint=words.run_hint(words.next_run_number()),
            review_hint=words.review_hint(words.review_waiting())))

    # -------------------------------------------------------------- actions
    def _on_chosen(self, key: str):
        if key == OPEN:
            self.open_toolkit()
        elif key == ROTATE:
            # Only the question: the Rotator page checks the folders and asks
            # "Start run N?", and nothing moves until it is answered yes.
            self.open_toolkit("Rotator", window_instance.command("rotate", "confirm"))
        elif key == REVIEW:
            self.open_toolkit("Review")
        elif key == SETTINGS:
            self.open_toolkit("Settings")
        elif key == QUIT:
            self.app.quit()

    def _on_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.open_toolkit()

    def _on_message_clicked(self):
        """A click on a balloon: the Rotator after "Playlist finished" (the cue to
        rotate), the Tracker after "Playlist started over"."""
        if self._balloon == BALLOON_FINISHED:
            self.open_toolkit("Rotator")
        elif self._balloon == BALLOON_RESTARTED:
            self.open_toolkit("Tracker")

    def open_toolkit(self, page: str = "Tracker", command: str | None = None):
        """Bring up the Toolkit window, on a page (the Tracker, by default), and
        with a command for it to carry out (see `window_instance`).

        The window is a program of its own, not something built inside the
        tray: when it was, a window that froze took the count down with it, as
        it did on 18 September. An open window is asked to come forward;
        otherwise one is started.
        """
        if window_instance.ask_to_show(page, wait=0, command=command):
            return
        if window_instance.already_running():
            # A window exists and its socket did not answer: it is still
            # starting — give it a moment — or it has stopped answering. A second
            # one would only stack up behind it, so say so instead.
            if window_instance.ask_to_show(page, wait=3, command=command):
                return
            log("the toolkit window did not answer a request to show itself")
            self.icon.showMessage(
                DISPLAY_NAME,
                f"The {DISPLAY_NAME} window is not answering yet. If it stays that way, "
                "close it from Task Manager — the tracker keeps counting either way.",
                QSystemTrayIcon.Warning, 10000)
            return
        try:
            child = window_instance.launch(page, command)
            log(f"opened the toolkit window: pid {child.pid}")
        except OSError as err:
            log(f"could not open the toolkit window: {err}")
            self.icon.showMessage(DISPLAY_NAME,
                                  f"Could not open the window: {err}",
                                  QSystemTrayIcon.Warning, 10000)


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
    handle = kernel32.CreateMutexW(None, False, TRAY_MUTEX)
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
    # The tracker starts first at logon, so it is usually what moves the data
    # out of the program folder; this is the line that says it did.
    from .data_location import resolve
    log(f"data: {resolve().report}")
    try:
        if not _claim_single_instance():
            log("another tracker already has the tray — exiting")
            return 0

        app = QApplication(sys.argv)
        app.setApplicationName("Wallpaper Tracker")
        theme.apply(app, styled=False)
        app.setQuitOnLastWindowClosed(False)

        if not _wait_for_tray():
            # Not fatal: counting is the point, and the icon re-registers by
            # itself if the shell comes back later. Quitting here would lose the
            # count instead, and a dialog at logon would be worse than useless.
            log(f"no notification area after {TRAY_WAIT_SECONDS}s — "
                f"counting on without an icon")

        # The tray's own GUI thread reads Wallpaper Engine's files on whatever
        # disk they are on; if one of those reads ever holds it for seconds,
        # this says which.
        watch = HangWatch(app_data_dir() / "tracker-hangs.log", "the tray tracker").start()
        tray = TrackerTray(app)   # the local reference is what keeps the icon alive
        log(f"running; tray icon visible: {tray.icon.isVisible()}")
        exit_code = app.exec()
        watch.stop()
        log(f"stopped with code {exit_code}")
        del tray
        return exit_code
    except Exception:
        log("crashed:\n" + traceback.format_exc())
        raise
