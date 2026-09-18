"""wallpaper_timer.py — how long the wallpaper on each monitor has left.

Wallpaper Engine does not publish its playlist timer. It has no query for it:
`-control` accepts pause, stop, play, mute, unmute, openWallpaper, openPlaylist,
openProfile, closeWallpaper, applyProperties, nextWallpaper, hideIcons,
showIcons, revealWallpaper and getWallpaper, and none of them reports time.
What it does have, found by reading its binary and watching it run, is two
files beside it in `bin/`, both in a small length-prefixed format of its own
(magic `PLPV0005`, every string a u32 length and its bytes):

* ``playliststate.bin`` — per monitor, the wallpaper on screen and the playlist
  entries this pass has not reached yet. It is rewritten **every time a
  wallpaper changes**, to the second: two changes on a 10-minute playlist were
  written at 14:53:46 and 15:03:46. That makes its modification time the one
  exact clock a program can read, and it is what every countdown starts from.
* ``playliststatetime.bin`` — per monitor, the seconds the timer had run, and
  the moment they were saved. It is only written as Wallpaper Engine exits, so
  it says nothing about a timer that is running. It is not used here.

Everything between two changes has to be worked out, and the rule turned out
to be simple and exact. The timer is the playlist's `delay` in seconds of
*playing* time. Unless the playlist has "update on pause" set, it stands still
whenever the wallpaper is paused — measured: a paused monitor did not change
once in eighteen minutes while the other changed on the dot. And the pausing is
decided **per monitor** by Wallpaper Engine's own playback settings: another
application focused, maximized or fullscreen *on that monitor* pauses that
monitor's wallpaper and no other. With Claude maximized on the primary screen
the primary wallpaper stood still and the second screen played on, confirmed
independently by the read position of each video file (one advancing about
12 MB a second, the other not at all).

So a countdown here is: the delay, less the seconds this monitor has spent
unpaused since its state-file stamp, with pausing judged from the windows on
that monitor through Wallpaper Engine's own rules. Each change resets it to the
exact second, so a judgement that was slightly off never outlives one
wallpaper. What cannot be known is said plainly rather than guessed: before the
first change the tracker has seen, the countdown is unknown, and just after
Wallpaper Engine starts it is marked approximate as a precaution. (A first
wallpaper once appeared to run 717 s on a 600 s delay after a start; it turned
out the second monitor had been plugged in two minutes after Wallpaper Engine
started, and that monitor's pass and timer began only then.)
"""
from __future__ import annotations

import ctypes
import json
import os
import struct
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..settings import app_data_dir
from . import we_memory

STATE_FILE = Path("bin") / "playliststate.bin"
MAGIC = "PLPV0005"
SECTION = "wallpaperconfig"          # the desktop; the other sections are empty

# A change seen this soon after Wallpaper Engine started is marked approximate.
# A precaution rather than a measurement: the one start that seemed to break the
# delay (717 s on 600) had a monitor plugged in two minutes after the start.
STARTUP_GRACE = 60.0
# Wallpaper Engine writes the state file this long after it has actually reset
# the timer and brought the next wallpaper up: measured 3.3 s and 3.1 s, the
# timer reading 2.989 and 2.991 at the moment of the write. A count started at
# the write is therefore that much behind, unless it is added back.
WRITE_LAG = 3.0
# Wallpaper Engine is at its busiest in the first half-minute after it starts,
# loading wallpapers and allocating as it goes, and that is the worst moment to
# read its memory. Searching waits this long after it appears; until then the
# estimate carries the countdown, which is what it is for.
SETTLE = 30.0
# How long to wait before searching the process again after a search that found
# nothing, five times longer after each one, so a layout that no longer matches
# (a Wallpaper Engine update, say) settles into costing nothing at all.
LOCATE_RETRY = 60.0
LOCATE_MAX = 1800.0
# Re-applying a playlist gives the pass a new number, and Wallpaper Engine writes
# it into the object where it stands. A found object whose number disagrees with
# the state file is given this long to agree again before it is looked for afresh.
INSTANCE_GRACE = 90.0
# Below this share of real time a timer that was read twice has not been running.
PAUSED_RATE = 0.25
# How often the running totals are written down, so a restarted tray resumes.
SAVE_EVERY = 15.0
SAVE_PATH = app_data_dir() / "wallpaper_timer.json"

# Values of playbackfocus / playbackmaximized / playbackfullscreen that stop the
# wallpaper, and so its timer. "mute" only silences it; "run" leaves it alone.
HALTING = {"pause", "stop"}


# ---- Wallpaper Engine's state file --------------------------------------------

@dataclass
class MonitorDeck:
    """One monitor, as playliststate.bin records it."""
    current: str
    waiting: list[str] = field(default_factory=list)
    # The u32 stored beside the monitor name. It changes when a playlist is applied
    # afresh, and the same number sits in the playlist object in memory.
    instance: int = 0


def parse_playlist_state(data: bytes) -> dict[str, MonitorDeck]:
    """The desktop section of playliststate.bin, by monitor.

    The layout, confirmed by a parser that consumes the real file to its last
    byte: magic, a section count, then per section its name, a monitor count,
    and per monitor its name, a u32, the current wallpaper, a u32, a count, and
    that many (path, u32) entries still waiting in this pass. Raises ValueError
    on anything that does not fit.
    """
    offset = 0

    def u32() -> int:
        nonlocal offset
        if offset + 4 > len(data):
            raise ValueError("playliststate.bin ends early")
        value = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        return value

    def text() -> str:
        nonlocal offset
        n = u32()
        if n > 1 << 16 or offset + n > len(data):
            raise ValueError("playliststate.bin has an impossible string")
        value = data[offset:offset + n].decode("utf-8", "replace")
        offset += n
        return value

    if text() != MAGIC:
        raise ValueError("not a PLPV0005 playlist state")
    decks: dict[str, MonitorDeck] = {}
    for _ in range(u32()):
        section = text()
        for _ in range(u32()):
            monitor = text()
            instance = u32()
            deck = MonitorDeck(current=text(), instance=instance)
            u32()
            for _ in range(u32()):
                deck.waiting.append(text())
                u32()
            if section == SECTION:
                decks[monitor] = deck
    return decks


# ---- Following Wallpaper Engine's files ---------------------------------------

class EngineFiles:
    """config.json and playliststate.bin, each read again only once rewritten.

    Between them these two files are everything a program can see of Wallpaper
    Engine changing its mind: the state file is rewritten at every wallpaper
    change, to the second, and config.json when the engine starts, exits or
    saves a playlist. Each is looked at with a stat — about 20 µs — and read
    only when that says it changed, so following them every second costs
    next to nothing.

    The countdown and the tracker share one of these. Every read that succeeds
    bumps a version number, and each follower keeps the last version it acted
    on, so neither can swallow a change the other has not seen yet. A file
    caught half-written fails to parse, keeps its old version, and is read again
    once it changes; what was read before it stays in place meanwhile.

    config.json is parsed here, once per rewrite, for both followers. It is
    2.3 MB on this machine and took 15 ms to parse, which the tracker used to
    pay on every look and the countdown again on its own.
    """

    def __init__(self, config_path: str | Path):
        self.config_path = Path(config_path)
        self.state_path = self.config_path.parent / STATE_FILE
        self.config: dict | None = None
        self.config_error: str | None = None       # why the last read failed
        self.config_version = 0
        self.state_version = 0
        self.state_written: float | None = None     # when the state read was written
        self.decks: dict[str, MonitorDeck] = {}
        # Whether the last look found a state file that parses. Without one there
        # is nothing to follow, and the tracker goes back to polling.
        self.state_ok = False
        self._config_key: tuple | None = None
        self._config_broken: tuple | None = None
        self._state_key: tuple | None = None
        self._broken_key: tuple | None = None

    @staticmethod
    def _key(path: Path) -> tuple | None:
        # Size as well as time: two writes inside one tick of the file-time
        # clock would otherwise look like one.
        try:
            st = path.stat()
        except OSError:
            return None
        return st.st_mtime_ns, st.st_size

    def refresh(self) -> None:
        self._refresh_config()
        self._refresh_state()

    def _refresh_config(self) -> None:
        key = self._key(self.config_path)
        if key is None:
            if self.config is None:
                self.config_error = "it is not there"
            return
        if key in (self._config_key, self._config_broken):
            return
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict):
                raise ValueError("it is not a JSON object")
        except (OSError, ValueError) as e:
            self._config_broken = key
            self.config_error = str(e)
            return
        self._config_key = key
        self.config = data
        self.config_error = None
        self.config_version += 1

    def _refresh_state(self) -> None:
        key = self._key(self.state_path)
        if key is None:
            self.state_ok = False
            return
        if key in (self._state_key, self._broken_key):
            return
        try:
            decks = parse_playlist_state(self.state_path.read_bytes())
        except (OSError, ValueError):
            # Tried once per version of the file: one written half-way changes
            # again when it is finished, one this cannot parse at all is not
            # read again every second for nothing.
            self._broken_key = key
            self.state_ok = False
            return
        self._state_key = key
        self.state_version += 1
        self.state_written = key[0] / 1e9
        self.decks = decks
        self.state_ok = True


# ---- Settings -----------------------------------------------------------------

@dataclass
class PlaybackRules:
    """Wallpaper Engine's "other application ..." settings."""
    focus: str = "run"
    maximized: str = "run"
    fullscreen: str = "run"

    @classmethod
    def from_user(cls, user: dict) -> "PlaybackRules":
        return cls(focus=str(user.get("playbackfocus") or "run").lower(),
                   maximized=str(user.get("playbackmaximized") or "run").lower(),
                   fullscreen=str(user.get("playbackfullscreen") or "run").lower())


@dataclass
class ScreenState:
    """What the other applications are doing on one monitor."""
    focused: bool = False
    maximized: bool = False
    fullscreen: bool = False


def is_paused(rules: PlaybackRules, screen: ScreenState) -> bool:
    """Whether Wallpaper Engine pauses this monitor's wallpaper."""
    return ((screen.focused and rules.focus in HALTING)
            or (screen.maximized and rules.maximized in HALTING)
            or (screen.fullscreen and rules.fullscreen in HALTING))


@dataclass
class TimerSettings:
    """One monitor's playlist, as far as its timer is concerned."""
    delay: float                 # seconds
    mode: str = "timer"
    update_on_pause: bool = False
    video_sequence: bool = False
    transition_ms: int = 0

    @classmethod
    def from_playlist(cls, playlist: dict) -> "TimerSettings":
        s = playlist.get("settings") or {}
        try:
            transition = int(s.get("transitiontime") or 0)
        except (TypeError, ValueError):
            transition = 0
        return cls(delay=float(s.get("delay") or 10) * 60,
                   mode=str(s.get("mode") or "timer").lower(),
                   update_on_pause=bool(s.get("updateonpause")),
                   video_sequence=bool(s.get("videosequence")),
                   transition_ms=transition)

    @property
    def counts_down(self) -> bool:
        """Only a timer playlist has a delay to count; the others change on a
        schedule or when a video ends, and have nothing to show here."""
        return self.mode == "timer" and not self.video_sequence


# ---- One monitor's clock --------------------------------------------------------

@dataclass
class Countdown:
    """Where one monitor's wallpaper is on its timer, as the tray draws it."""
    monitor: str
    delay: float
    running: float = 0.0          # seconds of playing time on this wallpaper
    known: bool = False           # a change has been seen to count from
    approximate: bool = False     # counted from a change right after a WE start
    paused: bool = False
    active: bool = True           # a timer playlist, with Wallpaper Engine up
    exact: bool = False           # read out of Wallpaper Engine rather than estimated

    @property
    def remaining(self) -> float | None:
        if not (self.known and self.active):
            return None
        return max(self.delay - self.running, 0.0)

    @property
    def fraction(self) -> float | None:
        """Share of the delay still to run, 1.0 just after a change."""
        left = self.remaining
        if left is None or self.delay <= 0:
            return None
        return left / self.delay

    @property
    def due(self) -> bool:
        """The time is up and the change has not been written yet."""
        return self.remaining == 0.0

    def describe(self) -> str:
        """Short enough for a tray tooltip."""
        if not self.active:
            return ""
        left = self.remaining
        if left is None:
            return "timer: waiting for the next change"
        if self.due:
            return "next any moment"
        minutes, seconds = divmod(int(round(left)), 60)
        stamp = f"{'≈' if self.approximate else ''}{minutes}:{seconds:02d}"
        return f"next in {stamp}" + (" (paused)" if self.paused else "")


@dataclass
class MonitorClock:
    """The running total for one monitor, advanced once a tick."""
    monitor: str
    wallpaper: str | None = None
    running: float = 0.0
    known: bool = False
    approximate: bool = False
    waiting: int | None = None     # size of the deck, which shrinks with every draw

    def observe(self, wallpaper: str, changed_at: float, now: float,
                we_started: float | None, waiting: int | None = None) -> None:
        """A state file naming `wallpaper` was written at `changed_at`.

        A change is a different wallpaper, or the same one drawn again — which a
        new pass can do, and which only shows as the deck changing size.
        """
        same_deck = waiting is None or self.waiting is None or waiting == self.waiting
        self.waiting = waiting
        if wallpaper == self.wallpaper and same_deck:
            return
        first_sight = self.wallpaper is None
        self.wallpaper = wallpaper
        if first_sight and not self.known:
            # The first state the tracker reads names what is already on screen;
            # when it came up is not in the file, so there is nothing to count.
            return
        self.running = max(now - changed_at, 0.0) + WRITE_LAG
        self.known = True
        self.approximate = (we_started is not None
                            and changed_at - we_started < STARTUP_GRACE)

    def advance(self, seconds: float, paused: bool) -> None:
        if seconds > 0 and not paused:
            self.running += seconds


# ---- Windows: which real display is Wallpaper Engine's MonitorN ----------------

_u32 = ctypes.WinDLL("user32", use_last_error=True) if os.name == "nt" else None
_k32 = ctypes.WinDLL("kernel32", use_last_error=True) if os.name == "nt" else None
_dwm = ctypes.WinDLL("dwmapi") if os.name == "nt" else None


class _RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


class _MONITORINFOEX(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", _RECT), ("rcWork", _RECT),
                ("dwFlags", wintypes.DWORD), ("szDevice", wintypes.WCHAR * 32)]


class _DISPLAY_DEVICE(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("DeviceName", wintypes.WCHAR * 32),
                ("DeviceString", wintypes.WCHAR * 128), ("StateFlags", wintypes.DWORD),
                ("DeviceID", wintypes.WCHAR * 128), ("DeviceKey", wintypes.WCHAR * 128)]


Rect = tuple[int, int, int, int]

if _u32 is not None:
    _MONITORENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                                          ctypes.POINTER(_RECT), wintypes.LPARAM)
    _WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    _u32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.c_void_p, _MONITORENUMPROC,
                                         wintypes.LPARAM]
    _u32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.c_void_p]
    _u32.MonitorFromWindow.restype = wintypes.HMONITOR
    _u32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    _u32.EnumDisplayDevicesW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p,
                                         wintypes.DWORD]
    _u32.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
    _u32.GetForegroundWindow.restype = wintypes.HWND
    _u32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.c_void_p]
    _u32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _u32.IsWindowVisible.argtypes = [wintypes.HWND]
    _u32.IsIconic.argtypes = [wintypes.HWND]
    _u32.IsZoomed.argtypes = [wintypes.HWND]
    _u32.FindWindowW.restype = wintypes.HWND
    _u32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    _dwm.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p,
                                           wintypes.DWORD]
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]


def _norm(path: str) -> str:
    return path.replace("\\", "/").lower()


def display_rects(monitor_map: dict) -> dict[str, Rect]:
    """Wallpaper Engine's MonitorN mapped to each attached display's rectangle.

    config.json keeps `monitormap`, keyed by each display's device interface path
    with a `location` N for MonitorN. Windows gives that same path for each
    attached monitor through EnumDisplayDevices, so the two are matched exactly.
    A display the map does not know falls back to the plain order — the primary
    first — which is also what a fresh install uses.
    """
    if _u32 is None:
        return {}
    interfaces: dict[str, list[str]] = {}
    i = 0
    while True:
        adapter = _DISPLAY_DEVICE()
        adapter.cb = ctypes.sizeof(adapter)
        if not _u32.EnumDisplayDevicesW(None, i, ctypes.byref(adapter), 0):
            break
        j = 0
        while True:
            mon = _DISPLAY_DEVICE()
            mon.cb = ctypes.sizeof(mon)
            if not _u32.EnumDisplayDevicesW(adapter.DeviceName, j, ctypes.byref(mon), 1):
                break
            if mon.StateFlags & 1:
                interfaces.setdefault(adapter.DeviceName, []).append(mon.DeviceID)
            j += 1
        i += 1

    found: list[tuple[bool, str, Rect, list[str]]] = []

    def one(hmon, _hdc, _rect, _lp):
        info = _MONITORINFOEX()
        info.cbSize = ctypes.sizeof(info)
        _u32.GetMonitorInfoW(hmon, ctypes.byref(info))
        r = info.rcMonitor
        found.append((bool(info.dwFlags & 1), info.szDevice,
                      (r.left, r.top, r.right, r.bottom), interfaces.get(info.szDevice, [])))
        return True

    _u32.EnumDisplayMonitors(None, None, _MONITORENUMPROC(one), 0)
    locations = {_norm(k): v.get("location") for k, v in (monitor_map or {}).items()
                 if isinstance(v, dict)}
    rects: dict[str, Rect] = {}
    unplaced = []
    for primary, _device, rect, ifaces in sorted(found, key=lambda f: not f[0]):
        location = next((locations[_norm(x)] for x in ifaces if _norm(x) in locations), None)
        if isinstance(location, int) and f"Monitor{location}" not in rects:
            rects[f"Monitor{location}"] = rect
        else:
            unplaced.append(rect)
    n = 0
    for rect in unplaced:
        while f"Monitor{n}" in rects:
            n += 1
        rects[f"Monitor{n}"] = rect
    return rects


# Windows that are part of the desktop rather than "another application".
_SHELL_CLASSES = {
    "progman", "workerw", "shell_traywnd", "shell_secondarytraywnd",
    "notifyiconoverflowwindow", "toplevelwindowforoverflowxamlisland",
    "windows.ui.core.corewindow", "xamlexplorerhostislandwindow",
    "foregroundstaging", "multitaskingviewframe", "tasklistthumbnailwnd",
}


# Extended styles of windows that sit over a screen without being an
# application on it: game and capture overlays (NVIDIA, SteelSeries, Discord,
# recorders) are transparent to the mouse, tool windows, or never take focus.
# Counting one of those as "fullscreen" would pause every wallpaper for good.
_WS_EX_TRANSPARENT = 0x00000020
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_NOACTIVATE = 0x08000000
_OVERLAY_STYLES = _WS_EX_TRANSPARENT | _WS_EX_TOOLWINDOW | _WS_EX_NOACTIVATE

if _u32 is not None:
    _u32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    _u32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    _k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]


def process_image(pid: int) -> str:
    """The executable path of a process, or "" when it cannot be read."""
    handle = _k32.OpenProcess(0x1000, False, pid)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if _k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        _k32.CloseHandle(handle)


def _cloaked(hwnd) -> bool:
    value = wintypes.DWORD()
    _dwm.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(value), ctypes.sizeof(value))
    return bool(value.value)


def screen_states(rects: dict[str, Rect], ignore: Callable[[int], bool]) -> dict[str, ScreenState]:
    """For each display, whether another application focuses, maximizes or covers it.

    `ignore(pid)` says which processes are Wallpaper Engine's own and so never
    "another application". This app's own window is *not* ignored: to Wallpaper
    Engine it is an application like any other.
    """
    states = {monitor: ScreenState() for monitor in rects}
    if _u32 is None or not rects:
        return states

    def monitor_of(hwnd) -> str | None:
        hmon = _u32.MonitorFromWindow(hwnd, 2)
        info = _MONITORINFOEX()
        info.cbSize = ctypes.sizeof(info)
        if not _u32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            return None
        r = info.rcMonitor
        box = (r.left, r.top, r.right, r.bottom)
        return next((m for m, rect in rects.items() if rect == box), None)

    def application(hwnd) -> bool:
        name = ctypes.create_unicode_buffer(96)
        _u32.GetClassNameW(hwnd, name, 96)
        if name.value.lower() in _SHELL_CLASSES:
            return False
        if _u32.GetWindowLongPtrW(hwnd, -20) & _OVERLAY_STYLES:     # GWL_EXSTYLE
            return False
        pid = wintypes.DWORD()
        _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return not ignore(pid.value)

    def one(hwnd, _lp):
        if not _u32.IsWindowVisible(hwnd) or _u32.IsIconic(hwnd) or _cloaked(hwnd):
            return True
        r = _RECT()
        _u32.GetWindowRect(hwnd, ctypes.byref(r))
        if r.right - r.left < 64 or r.bottom - r.top < 64 or not application(hwnd):
            return True
        monitor = monitor_of(hwnd)
        if monitor is None:
            return True
        left, top, right, bottom = rects[monitor]
        if _u32.IsZoomed(hwnd):
            states[monitor].maximized = True
        elif r.left <= left and r.top <= top and r.right >= right and r.bottom >= bottom:
            states[monitor].fullscreen = True
        return True

    _u32.EnumWindows(_WNDENUMPROC(one), 0)
    focus = _u32.GetForegroundWindow()
    if focus and application(focus):
        monitor = monitor_of(focus)
        if monitor is not None:
            states[monitor].focused = True
    return states


def wallpaper_engine_process() -> tuple[int, float] | None:
    """(pid, start time) of the running Wallpaper Engine, or None."""
    if _u32 is None:
        return None
    hwnd = _u32.FindWindowW("WPETrayWindow", None)
    if not hwnd:
        return None
    pid = wintypes.DWORD()
    _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = _k32.OpenProcess(0x1000, False, pid.value)     # QUERY_LIMITED_INFORMATION
    if not handle:
        return pid.value, 0.0
    try:
        created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if not _k32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                    ctypes.byref(kernel), ctypes.byref(user)):
            return pid.value, 0.0
        ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
        return pid.value, ticks / 10_000_000 - 11_644_473_600
    finally:
        _k32.CloseHandle(handle)


# ---- The timer ----------------------------------------------------------------

class WallpaperTimer:
    """Ticks once a second and keeps a Countdown per monitor.

    The Windows-facing parts are passed in so the logic can be driven without a
    desktop: `find_engine`, `measure_screens` and `clock` default to the real ones.
    """

    def __init__(self, config_path: str,
                 find_engine: Callable[[], tuple[int, float] | None] = wallpaper_engine_process,
                 measure_screens: Callable[[dict, set], dict] = screen_states,
                 find_displays: Callable[[dict], dict] = display_rects,
                 clock: Callable[[], float] = time.time,
                 save_path: Path | None = SAVE_PATH,
                 open_memory: Callable[[int], "we_memory.Memory"] | None = we_memory.ProcessMemory,
                 background: bool = True,
                 files: EngineFiles | None = None):
        self.config_path = Path(config_path)
        self.state_path = self.config_path.parent / STATE_FILE
        # Shared with the tracker when the tray runs both, so the two files are
        # looked at once a second rather than once per reader.
        self.files = files or EngineFiles(self.config_path)
        self.find_engine = find_engine
        self.measure_screens = measure_screens
        self.find_displays = find_displays
        self.clock = clock
        self.save_path = save_path

        self.settings: dict[str, TimerSettings] = {}
        self.rules = PlaybackRules()
        self.rects: dict[str, Rect] = {}
        self.clocks: dict[str, MonitorClock] = {}
        self.countdowns: dict[str, Countdown] = {}
        self._config_version = self._state_version = 0     # of `files`, last acted on
        self._last_tick: float | None = None
        self._last_save = 0.0
        self._engine: tuple[int, float] | None = None
        self._restored = self._load_saved()
        self._monitor_map: dict = {}
        self._rects_at: float | None = None
        self._engine_dir = _norm(str(self.config_path.parent)) + "/"
        self._pid_is_engine: dict[int, bool] = {}
        self._decks: dict[str, MonitorDeck] = {}

        # Reading the timer out of Wallpaper Engine, when it can be found.
        self.open_memory = open_memory
        self.background = background
        self._memory: "we_memory.Memory | None" = None
        self._memory_pid: int | None = None
        self._objects: dict[str, int] = {}            # monitor -> address of its object
        self._last_read: dict[str, tuple[float, float]] = {}   # monitor -> (value, when)
        self._mismatch: dict[str, float] = {}         # monitor -> when its number first differed
        self._probed: set[str] = set()                # monitors whose old address was tried
        self._hints: dict[str, "we_memory.Hint"] = self._saved_hints()
        self._search: threading.Thread | None = None
        self._search_result: dict[str, list[int]] | None = None
        self._search_for: dict[str, "we_memory.Expected"] = {}
        self._search_at = -1e18
        self._search_misses = 0
        self._search_cost = (0.0, 0.0)                # megabytes read, seconds taken
        self.log: Callable[[str], None] = lambda _message: None

    # -- reading Wallpaper Engine -------------------------------------------

    def _read_config(self) -> None:
        if self.files.config_version == self._config_version:
            return
        self._config_version = self.files.config_version
        data = self.files.config or {}
        for user in data.values():
            general = user.get("general") if isinstance(user, dict) else None
            if not isinstance(general, dict):
                continue
            prefs = general.get("user") or {}
            self.rules = PlaybackRules.from_user(prefs)
            self._monitor_map = prefs.get("monitormap") or {}
            self._rects_at = None
            selected = (general.get("wallpaperconfig") or {}).get("selectedwallpapers") or {}
            self.settings = {m: TimerSettings.from_playlist(cfg.get("playlist") or {})
                             for m, cfg in selected.items()
                             if isinstance(cfg, dict) and cfg.get("playlist")}
            return

    def _read_state(self, now: float) -> None:
        if self.files.state_version == self._state_version:
            return
        self._state_version = self.files.state_version
        mtime = self.files.state_written
        self._decks = decks = self.files.decks
        started = self._engine[1] if self._engine else None
        for monitor, deck in decks.items():
            clock = self.clocks.setdefault(monitor, MonitorClock(monitor))
            clock.observe(deck.current, mtime, now, started, len(deck.waiting))

    # -- the tick -----------------------------------------------------------

    def _is_engine(self, pid: int) -> bool:
        """Whether a process is one of Wallpaper Engine's (its UI, its renderers)."""
        if pid not in self._pid_is_engine:
            if len(self._pid_is_engine) > 512:
                self._pid_is_engine.clear()
            image = _norm(process_image(pid)) if _k32 is not None else ""
            self._pid_is_engine[pid] = bool(image) and image.startswith(self._engine_dir)
        return self._pid_is_engine[pid]

    # -- the timer, read out of Wallpaper Engine ---------------------------------

    def _expected(self, monitor: str) -> "we_memory.Expected | None":
        deck = self._decks.get(monitor)
        settings = self.settings.get(monitor)
        if deck is None or settings is None or not settings.transition_ms:
            return None
        return we_memory.Expected(monitor, deck.instance, settings.delay / 60,
                                  settings.transition_ms)

    def _forget_memory(self) -> None:
        if self._memory is not None:
            try:
                self._memory.close()
            except OSError:
                pass
        self._memory, self._memory_pid = None, None
        self._objects.clear()
        self._last_read.clear()
        self._mismatch.clear()
        self._probed.clear()
        self._search_misses = 0
        self._search_at = -1e18

    def _drop(self, monitor: str) -> None:
        """Stop trusting a monitor's object: it is no longer what it was."""
        self._objects.pop(monitor, None)
        self._last_read.pop(monitor, None)
        self._mismatch.pop(monitor, None)
        self._search_misses = 0

    def _remember(self, monitor: str, address: int) -> None:
        """Keep where an object was found, to look there first another time."""
        region_of = getattr(self._memory, "region_of", None)
        region = region_of(address) if region_of is not None else None
        if region is None:
            return
        base, size = region
        self._hints[monitor] = we_memory.Hint(monitor, address, size, address - base)
        self._probed.discard(monitor)

    def _saved_hints(self) -> dict[str, "we_memory.Hint"]:
        hints: dict[str, "we_memory.Hint"] = {}
        for monitor, spot in ((self._restored or {}).get("objects") or {}).items():
            try:
                hints[monitor] = we_memory.Hint(monitor, int(spot["address"]),
                                                int(spot["region_size"]), int(spot["offset"]))
            except (AttributeError, KeyError, TypeError, ValueError):
                continue
        return hints

    def _run_search(self, pid: int, wanted: list) -> None:
        memory = None
        we_memory.background_mode(True)
        started = time.monotonic()
        try:
            memory = self.open_memory(pid)
            self._search_result = we_memory.find_objects(
                memory, wanted, cancelled=lambda: self._memory_pid != pid,
                hints=[h for m, h in self._hints.items() if any(w.monitor == m for w in wanted)])
        except OSError:
            self._search_result = {}
        finally:
            self._search_cost = (getattr(memory, "read_bytes", 0) / 1e6,
                                 time.monotonic() - started)
            if memory is not None:
                memory.close()
            we_memory.background_mode(False)

    def _read_memory(self, now: float) -> dict[str, float]:
        """Every monitor's timer that can be read right now, by monitor."""
        engine = self._engine
        if self.open_memory is None or engine is None:
            return {}
        pid = engine[0]
        if self._memory_pid != pid:
            self._forget_memory()
            try:
                self._memory = self.open_memory(pid)
                self._memory_pid = pid
            except OSError:
                return {}

        # A search that has finished hands over what it found.
        if self._search is not None and not self._search.is_alive():
            self._search = None
            self._take_search_result()

        # Where an object was last time costs one read to try, so try it before
        # anything is searched for: within one run of Wallpaper Engine it is
        # still there, and the tray can start counting exactly the moment it starts.
        for monitor, hint in self._hints.items():
            want = self._expected(monitor)
            if monitor in self._objects or monitor in self._probed or want is None:
                continue
            self._probed.add(monitor)
            if we_memory.read_timer(self._memory, hint.address, want) is not None:
                self._objects[monitor] = hint.address
                self.log(f"countdown: {monitor}'s timer was still where it was last seen")

        values: dict[str, float] = {}
        for monitor in list(self._objects):
            want = self._expected(monitor)
            reading = (we_memory.read_timer(self._memory, self._objects[monitor], want,
                                            instance=False) if want is not None else None)
            if reading is None:
                # Wallpaper Engine restarted, a monitor went away: the object is gone.
                self._drop(monitor)
                continue
            if reading.instance == want.instance:
                self._mismatch.pop(monitor, None)
            else:
                # Either the state file has not caught up with a re-applied
                # playlist, or this is a freed copy of an object that has moved.
                # Waiting tells the two apart without a search for nothing.
                since = self._mismatch.setdefault(monitor, now)
                if now - since > INSTANCE_GRACE:
                    self._drop(monitor)
                    continue
            values[monitor] = reading.timer

        wanted = [w for m in self.settings if m not in self._objects
                  and (w := self._expected(m)) is not None]
        started = engine[1]
        settling = bool(started) and now - started < SETTLE
        wait = min(LOCATE_RETRY * 5 ** self._search_misses, LOCATE_MAX)
        if wanted and self._search is None and not settling and now - self._search_at >= wait:
            self._search_at = now
            self._search_for = {w.monitor: w for w in wanted}
            self._search_result = None
            if self.background:
                self._search = threading.Thread(target=self._run_search, args=(pid, wanted),
                                                 name="find-we-timer", daemon=True)
                self._search.start()
            else:
                self._run_search(pid, wanted)
                self._take_search_result()
                for monitor in self._search_for:
                    if monitor in self._objects:
                        reading = we_memory.read_timer(self._memory, self._objects[monitor],
                                                       self._search_for[monitor], instance=False)
                        if reading is not None:
                            values[monitor] = reading.timer
        return values

    def _take_search_result(self) -> None:
        result = self._search_result or {}
        for monitor, addresses in result.items():
            want = self._expected(monitor)
            if not addresses or want is None:
                continue
            # The playlist may have been re-applied while the search ran, which
            # renumbers the pass. The object is still the object, so check it
            # again rather than throw a whole search away over the number.
            if we_memory.read_timer(self._memory, addresses[0], want,
                                    instance=False) is not None:
                self._objects[monitor] = addresses[0]
                self._remember(monitor, addresses[0])
        missing = [m for m in self._search_for if m not in self._objects]
        self._search_misses = self._search_misses + 1 if missing else 0
        found = sorted(set(self._search_for) - set(missing))
        megabytes, seconds = getattr(self, "_search_cost", (0.0, 0.0))
        self.log(f"countdown: Wallpaper Engine's timer found for {found or 'no monitor'}"
                 + (f", not for {missing}" if missing else "")
                 + f" — read {megabytes:.0f} MB in {seconds:.1f} s")

    def tick(self) -> dict[str, Countdown]:
        now = self.clock()
        engine = self.find_engine()
        if engine != self._engine:
            if self._engine is not None and engine is None:
                # Wallpaper Engine closed: nothing is counting any more.
                for clock in self.clocks.values():
                    clock.known = False
                self._forget_memory()
            self._engine = engine
        self.files.refresh()
        self._read_config()
        if self._restored is not None:
            self._apply_saved(now)
        self._read_state(now)

        # Displays come and go without config.json noticing; look again now and then.
        if self._rects_at is None or now - self._rects_at > 30:
            self.rects = self.find_displays(self._monitor_map)
            self._rects_at = now
        elapsed = 0.0 if self._last_tick is None else min(now - self._last_tick, 5.0)
        self._last_tick = now
        read = self._read_memory(now)
        # Every window on screen is looked at only for monitors being estimated:
        # a timer read out of Wallpaper Engine already knows whether it is paused.
        estimated = [m for m in self.settings if m not in read]
        screens = (self.measure_screens(self.rects, self._is_engine)
                   if engine and estimated else {})

        out: dict[str, Countdown] = {}
        for monitor, settings in self.settings.items():
            clock = self.clocks.setdefault(monitor, MonitorClock(monitor))
            screen = screens.get(monitor, ScreenState())
            active = bool(engine) and settings.counts_down
            if monitor in read:
                # Wallpaper Engine's own number. Paused is simply "it did not move";
                # the outside estimate is kept in step so a lost object resumes from
                # an exact count rather than from wherever the estimate had drifted.
                value = read[monitor]
                last = self._last_read.get(monitor)
                paused = False
                if last is not None and now - last[1] >= 0.5 and value >= last[0]:
                    paused = (value - last[0]) < PAUSED_RATE * (now - last[1])
                self._last_read[monitor] = (value, now)
                clock.running, clock.known, clock.approximate = value, True, False
                out[monitor] = Countdown(monitor=monitor, delay=settings.delay, running=value,
                                         known=True, paused=paused, active=active, exact=True)
                continue
            paused = (not settings.update_on_pause) and is_paused(self.rules, screen)
            clock.advance(elapsed, paused)
            out[monitor] = Countdown(
                monitor=monitor, delay=settings.delay, running=clock.running,
                known=clock.known, approximate=clock.approximate, paused=paused,
                active=active)
        self.countdowns = out
        if now - self._last_save >= SAVE_EVERY:
            self._save(now)
        return out

    # -- surviving a restart of the tray --------------------------------------

    def _save(self, now: float) -> None:
        self._last_save = now
        if self.save_path is None or not self._engine:
            return
        payload = {
            "saved_at": now,
            "engine_started": self._engine[1],
            "clocks": {m: {"wallpaper": c.wallpaper, "running": c.running,
                           "known": c.known, "approximate": c.approximate}
                       for m, c in self.clocks.items()},
            # Where each timer was, so the next tray need not search for it.
            "objects": {m: {"address": h.address, "region_size": h.region_size,
                            "offset": h.offset} for m, h in self._hints.items()},
        }
        try:
            tmp = self.save_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.save_path)
        except OSError:
            pass

    def _load_saved(self) -> dict | None:
        if self.save_path is None:
            return None
        try:
            return json.loads(self.save_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _apply_saved(self, now: float) -> None:
        """Pick up where a previous tray left off, if nothing has moved since.

        Only within the same run of Wallpaper Engine, and only for a monitor
        still showing the same wallpaper. The seconds the tray was away are not
        known to have played or not, so a gap of more than a few makes the
        count approximate rather than silently wrong.
        """
        saved, self._restored = self._restored, None
        if not self._engine or abs(saved.get("engine_started", -1) - self._engine[1]) > 2:
            return
        if not self.files.state_ok:
            return
        decks = self.files.decks
        gap = now - float(saved.get("saved_at") or now)
        for monitor, entry in (saved.get("clocks") or {}).items():
            deck = decks.get(monitor)
            if deck is None or deck.current != entry.get("wallpaper") or not entry.get("known"):
                continue
            self.clocks[monitor] = MonitorClock(
                monitor, wallpaper=deck.current, running=float(entry.get("running") or 0),
                known=True, approximate=bool(entry.get("approximate")) or gap > 5,
                waiting=len(deck.waiting))
