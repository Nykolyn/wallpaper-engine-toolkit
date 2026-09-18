"""Playlist progress tracking for Wallpaper Engine.

Answers one question: how much of the playlist that is currently running has
already been shown — ``seen / total`` — so the next rotation can be timed to
the moment the list has been worked through.

Wallpaper Engine's ``config.json`` holds the playlists and their timer delay,
but it is only flushed when the app starts or exits: ``selectedwallpapers.
<Monitor>.file`` stays frozen at whatever was on screen at launch, so it cannot
say what is showing right now.

``bin/playliststate.bin`` can. Wallpaper Engine rewrites it at every change,
with, per monitor, the wallpaper on screen and the entries this pass has not
drawn yet — which for a random playlist is the count itself. The tracker looks
whenever that file is rewritten, and follows it wherever it describes the
playlist (`deck_describes`, `Tracker._follow_deck`).

Where it does not — a sorted playlist, or a state file that does not fit — the
tracker does what it did before it knew of that file, and watches the file
handle. The renderer keeps the wallpaper it is showing open, so opening a path
with no sharing flags gets ERROR_SHARING_VIOLATION for exactly that one file.
That probe sweeps the whole playlist every look, about 17 ms for 1300 items,
because Wallpaper Engine does not always release the wallpaper it has moved on
from.

The probe alone is not enough, and the two things it misses are what much of
the rest of this module is about:

* wallpapers that are **gone** — Wallpaper Engine goes on listing a deleted one,
  and an entry that can never come up again would stall the count one short of
  the end for good (`scan_missing`);
* wallpapers that are **never held open** — a web wallpaper's `index.html` is
  read once by a browser process and released, so nothing is ever locked for it.

Both are answered by the filesystem rather than by watching harder: access times
say what was shown while nothing could see it (`_catch_up`), and existence says
what can never be shown at all.
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
import time
import uuid
from ctypes import wintypes
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from . import steam_paths
from .wallpaper_timer import WRITE_LAG, EngineFiles, MonitorDeck, wallpaper_engine_process


# ---- Where things live ----------------------------------------------------

def app_data_dir() -> Path:
    """Directory where tracker state lives. Next to the exe, or project root in dev."""
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).resolve().parent.parent.parent
    d = base / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


STATE_PATH = app_data_dir() / "tracker.json"

# Where Steam says Wallpaper Engine is. "" when Steam is not installed here,
# in which case find_we_config() falls through to its own search.
DEFAULT_WE_CONFIG = steam_paths.as_text(steam_paths.we_config())

TIME_FMT = "%Y-%m-%d %H:%M:%S"


def _now() -> str:
    return datetime.now().strftime(TIME_FMT)


def _parse(stamp: str | None) -> datetime | None:
    if not stamp:
        return None
    try:
        return datetime.strptime(stamp, TIME_FMT)
    except ValueError:
        return None


# ---- Finding Wallpaper Engine's config.json -------------------------------

def _steam_roots() -> list[Path]:
    """Steam library roots, from the registry plus libraryfolders.vdf."""
    roots: list[Path] = []
    try:
        import winreg
    except ImportError:
        return roots

    for hive, key in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                      (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam")):
        try:
            with winreg.OpenKey(hive, key) as k:
                for value in ("SteamPath", "InstallPath"):
                    try:
                        roots.append(Path(winreg.QueryValueEx(k, value)[0]))
                    except OSError:
                        pass
        except OSError:
            pass

    # Extra libraries appear as `"path"  "D:\\SteamLibrary"` lines in the vdf.
    for root in list(roots):
        try:
            text = (root / "steamapps" / "libraryfolders.vdf").read_text(
                encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith('"path"'):
                parts = stripped.split('"')
                if len(parts) >= 4:
                    roots.append(Path(parts[3].replace("\\\\", "\\")))
    return roots


def find_we_config() -> str | None:
    """Best guess at Wallpaper Engine's config.json, or None if nothing matches."""
    candidates: list[Path] = [Path(DEFAULT_WE_CONFIG)] if DEFAULT_WE_CONFIG else []
    for root in _steam_roots():
        candidates.append(root / "steamapps" / "common" / "wallpaper_engine" / "config.json")
    for drive in "WCDEFGHIJ":
        candidates.append(Path(f"{drive}:/steam/steamapps/common/wallpaper_engine/config.json"))
        candidates.append(
            Path(f"{drive}:/SteamLibrary/steamapps/common/wallpaper_engine/config.json"))

    seen: set[str] = set()
    for c in candidates:
        key = str(c).lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            if c.is_file():
                return str(c)
        except OSError:
            pass
    return None


# ---- Reading the playlists ------------------------------------------------

@dataclass
class PlaylistView:
    """One monitor's active playlist, as config.json describes it."""
    monitor: str
    name: str
    items: list[str]
    delay: int          # minutes between changes ("timer" mode)
    order: str          # "random" | "linear" | ...
    mode: str           # "timer" | ...
    config_file: str    # what config.json thinks is on screen (stale while WE runs)

    @property
    def total(self) -> int:
        return len(self.items)


def read_playlists(config_path: str) -> list[PlaylistView]:
    """Active playlist per monitor. Raises OSError/ValueError on an unreadable config."""
    return playlists_in(json.loads(Path(config_path).read_text(encoding="utf-8-sig")))


def playlists_in(data: dict) -> list[PlaylistView]:
    """Active playlist per monitor, out of an already parsed config.json."""
    views: list[PlaylistView] = []
    for _user, section in data.items():
        if not isinstance(section, dict):
            continue  # e.g. the "?installdirectory" string entry
        general = section.get("general")
        if not isinstance(general, dict):
            continue
        selected = general.get("wallpaperconfig", {}).get("selectedwallpapers", {})
        for monitor, cfg in sorted(selected.items()):
            playlist = cfg.get("playlist") or {}
            items = [i for i in playlist.get("items", []) if isinstance(i, str)]
            if not items:
                continue
            settings = playlist.get("settings") or {}
            views.append(PlaylistView(
                monitor=monitor,
                name=playlist.get("name") or "(unnamed)",
                items=items,
                delay=int(settings.get("delay") or 10),
                order=str(settings.get("order", "?")),
                mode=str(settings.get("mode", "?")),
                config_file=str(cfg.get("file", "")),
            ))
    return views


# ---- Which wallpaper is on screen right now -------------------------------

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateFileW.restype = wintypes.HANDLE
_k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                             wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                             wintypes.HANDLE]
_k32.CloseHandle.argtypes = [wintypes.HANDLE]

_GENERIC_READ = 0x80000000
_OPEN_EXISTING = 3
_INVALID_HANDLE = ctypes.c_void_p(-1).value
ERROR_SHARING_VIOLATION = 32
ERROR_LOCK_VIOLATION = 33


def is_in_use(path: str) -> bool:
    """True if another process holds `path` open — i.e. it is the live wallpaper.

    On a sharing violation no handle is ever created, so the common case (the
    wallpaper already playing) never takes the file away from Wallpaper Engine,
    not even for an instant.
    """
    handle = _k32.CreateFileW(path.replace("/", "\\"), _GENERIC_READ, 0, None,
                              _OPEN_EXISTING, 0, None)
    if handle == _INVALID_HANDLE:
        return ctypes.get_last_error() in (ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION)
    _k32.CloseHandle(handle)
    return False


def probe_current(items: list[str], prefer: str | None = None,
                  exclude: set[str] | None = None) -> str | None:
    """The playlist item currently on screen, or None if nothing is playing.

    Every item is checked, not just the one found last time. Wallpaper Engine
    does not always let go of a wallpaper it has moved on from — scene packages
    in particular stay open — so returning the first lock found would pin the
    tracker to something that stopped being shown hours ago, and silently miss
    every change after it. Observed on a live playlist: two files held at once,
    one playing and one abandoned half an hour earlier.

    When more than one is held, the one whose file was read most recently is the
    one on screen; the rest are handles not yet released. A tie keeps whatever
    was already current, so the answer does not flicker.

    `exclude` holds items already claimed by another monitor in the same poll.
    The full sweep costs about 17 ms for 1300 items.
    """
    exclude = exclude or set()
    locked = [i for i in items if i not in exclude and is_in_use(i)]
    if not locked:
        return None
    if len(locked) == 1:
        return locked[0]

    def freshness(item: str) -> tuple[float, bool]:
        try:
            when = os.stat(item.replace("/", "\\")).st_atime
        except OSError:
            when = 0.0
        return when, item == prefer

    return max(locked, key=freshness)


# ---- Human-readable names -------------------------------------------------

_title_cache: dict[str, str] = {}


def title_for(item: str) -> str:
    """The wallpaper's title from its project.json, falling back to the file name."""
    cached = _title_cache.get(item)
    if cached is not None:
        return cached
    path = Path(item.replace("/", "\\"))
    title = ""
    try:
        meta = json.loads((path.parent / "project.json").read_text(encoding="utf-8"))
        title = str(meta.get("title") or "").strip()
    except (OSError, ValueError, TypeError):
        pass
    if not title:
        title = path.parent.name if path.stem.lower() == "scene" else path.stem
    _title_cache[item] = title
    return title


# ---- Cycles ---------------------------------------------------------------

# How a cycle's start was established, in decreasing order of confidence.
ANCHOR_ROTATION = "rotation history"
ANCHOR_ENGINE = "Wallpaper Engine starting the playlist over"
ANCHOR_FILE_TIMES = "file times"
ANCHOR_NONE = "tracked from now"

# Wallpaper Engine keeps its own record of each monitor's pass through a random
# playlist: the wallpapers it has not drawn yet (see wallpaper_timer). Whatever
# the tracker has counted as shown should be gone from that list. When most of it
# is back, Wallpaper Engine has begun the playlist again and the count starts
# over with it. Measured on a real restart: 77 of the 83 wallpapers counted were
# waiting again on one monitor, 196 of 200 on the other — while a handful of
# credits the tracker got wrong would put back a few, never most.
RESET_SHARE = 0.5
RESET_MIN = 3
# How far the engine's deck may stray from the tracked playlist and still be
# the same one: config.json and the state file are written at different times.
DECK_TOLERANCE = 0.02

# Fewer displays than this is too small a sample to extrapolate a pace or a
# repeat rate from; until then the plain playthrough assumption is used.
MIN_SAMPLE = 20

# A playlist sharing less than this fraction of its items with the tracked one
# is a different playlist: a rotation ran, so the count starts over.
NEW_CYCLE_OVERLAP = 0.5


@dataclass
class Cycle:
    """Progress through one playlist, from the moment it was first seen."""
    monitor: str
    playlist: str
    started: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    items: list[str] = field(default_factory=list)
    seen: dict[str, str] = field(default_factory=dict)   # item -> first shown
    changes: int = 0                                     # incl. repeats
    current: str | None = None
    current_since: str | None = None
    delay: int = 10
    order: str = "?"
    # Items credited from their file's access time rather than watched live.
    inferred: list[str] = field(default_factory=list)
    # When this cycle's start was worked out, and from what.
    anchor: str = ANCHOR_NONE
    # Last time a poll actually ran; a hole here is what triggers reconciling.
    last_poll: str | None = None
    # Playlist entries whose file has been deleted. They can never be shown, so
    # they are held out of the counts — see `scan_missing`.
    missing: list[str] = field(default_factory=list)
    # Last time the access times were re-read; see `Tracker._catch_up`.
    last_reconcile: str | None = None
    # The playlist came out of a rotation, whatever this cycle is dated from.
    from_rotation: bool = False
    # Set when Wallpaper Engine started the playlist over: when, what the
    # previous cycle had reached ("87/201"), and which cycle that was.
    restarted_at: str | None = None
    restarted_from: str | None = None
    previous_id: str | None = None
    # Drawn in Wallpaper Engine's pass before a manual reset. The engine's
    # record still has them as shown; this cycle, by request, does not.
    excluded: list[str] = field(default_factory=list)

    @classmethod
    def start(cls, view: PlaylistView) -> "Cycle":
        return cls(monitor=view.monitor, playlist=view.name, started=_now(),
                   items=list(view.items), delay=view.delay, order=view.order)

    def sync(self, view: PlaylistView) -> None:
        """Adopt small edits to the playlist without ending the cycle."""
        self.playlist = view.name
        self.delay = view.delay
        self.order = view.order
        if view.items != self.items:
            self.items = list(view.items)
            live = set(self.items)
            self.seen = {k: v for k, v in self.seen.items() if k in live}
            self.inferred = [i for i in self.inferred if i in live]
            self.missing = [i for i in self.missing if i in live]
            self.excluded = [i for i in self.excluded if i in live]

    # ----- what still counts -----------------------------------------------
    #
    # Wallpaper Engine goes on listing a wallpaper after its folder is deleted,
    # and such an entry can never be shown again. Left in the total it stalls
    # the count one short of the end for good, so everything below counts only
    # what is still on disk.

    @property
    def live_items(self) -> list[str]:
        gone = set(self.missing)
        return [i for i in self.items if i not in gone]

    @property
    def gone_count(self) -> int:
        live = set(self.items)
        return sum(1 for i in set(self.missing) if i in live)

    @property
    def total(self) -> int:
        return len(self.items) - self.gone_count

    @property
    def shown_count(self) -> int:
        """Everything ever recorded as shown, deleted or not."""
        return len(self.seen)

    @property
    def seen_count(self) -> int:
        gone = set(self.missing)
        return sum(1 for i in self.seen if i not in gone)

    @property
    def remaining(self) -> int:
        return max(self.total - self.seen_count, 0)

    @property
    def repeats(self) -> int:
        # Counted against everything observed, not just what survives on disk:
        # a wallpaper shown twice and then deleted was still shown twice.
        return max(self.changes - self.shown_count, 0)

    @property
    def inferred_count(self) -> int:
        gone = set(self.missing)
        return len((set(self.inferred) & set(self.seen)) - gone)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "monitor": self.monitor, "playlist": self.playlist, "started": self.started,
            "items": self.items, "seen": self.seen, "changes": self.changes,
            "current": self.current, "current_since": self.current_since,
            "delay": self.delay, "order": self.order,
            "inferred": self.inferred, "anchor": self.anchor,
            "last_poll": self.last_poll, "missing": self.missing,
            "last_reconcile": self.last_reconcile,
            "from_rotation": self.from_rotation,
            "restarted_at": self.restarted_at, "restarted_from": self.restarted_from,
            "previous_id": self.previous_id, "excluded": self.excluded,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Cycle":
        return cls(
            monitor=str(d.get("monitor", "")),
            playlist=str(d.get("playlist", "")),
            started=str(d.get("started") or _now()),
            id=str(d.get("id") or uuid.uuid4().hex[:8]),
            items=[str(i) for i in d.get("items", [])],
            seen={str(k): str(v) for k, v in (d.get("seen") or {}).items()},
            changes=int(d.get("changes", 0)),
            current=d.get("current"),
            current_since=d.get("current_since"),
            delay=int(d.get("delay") or 10),
            order=str(d.get("order", "?")),
            inferred=[str(i) for i in d.get("inferred", [])],
            anchor=str(d.get("anchor") or ANCHOR_NONE),
            last_poll=d.get("last_poll"),
            missing=[str(i) for i in d.get("missing", [])],
            last_reconcile=d.get("last_reconcile"),
            from_rotation=bool(d.get("from_rotation")),
            restarted_at=d.get("restarted_at"),
            restarted_from=d.get("restarted_from"),
            previous_id=d.get("previous_id"),
            excluded=[str(i) for i in d.get("excluded", [])],
        )


def engine_restarted(cycle: "Cycle", deck: MonitorDeck) -> bool:
    """Whether Wallpaper Engine has started this playlist over behind the count.

    Within one pass each wallpaper is drawn once, and the engine's deck keeps
    only those still to come. So everything the tracker has counted as shown
    should be missing from the deck; when most of it is back, a new pass began.
    That covers both ways it happens — a pass that ran to its end, and one the
    engine threw away, as it did when started with one monitor and given a
    second. A deck that does not fit this playlist says nothing about it.
    """
    items = set(cycle.items)
    waiting = set(deck.waiting)
    if not waiting or len(waiting - items) > max(2, DECK_TOLERANCE * len(waiting)):
        return False
    gone = set(cycle.missing)
    counted = {i for i in cycle.seen if i in items and i not in gone}
    back = counted & waiting
    return len(back) >= RESET_MIN and len(back) >= RESET_SHARE * len(counted)


def restart_from_engine(old: "Cycle", deck: MonitorDeck, written: float | None,
                        engine_started: float | None = None) -> "Cycle":
    """The cycle Wallpaper Engine's new pass amounts to, as far as it has got.

    What the engine has drawn in this pass is everything not waiting in its deck.
    Those are credited straight away — from the engine's own record rather than
    watched, so they carry the reconstruction mark — and dated when the engine
    wrote that record. The start of the pass is not recorded anywhere; it is put
    one delay per drawn wallpaper before that, but never before the running
    Wallpaper Engine started — a pass cannot predate the process that began it,
    and a restart is exactly what a start with a monitor missing produces.
    """
    stamp = datetime.fromtimestamp(written) if written else datetime.now()
    waiting = set(deck.waiting)
    gone = set(old.missing)
    drawn = [i for i in old.items if i not in waiting and i not in gone]
    started = stamp - timedelta(minutes=old.delay * max(len(drawn) - 1, 0))
    if engine_started and datetime.fromtimestamp(engine_started) <= stamp:
        started = max(started, datetime.fromtimestamp(engine_started))
    when = stamp.strftime(TIME_FMT)
    cycle = Cycle(
        monitor=old.monitor, playlist=old.playlist, started=started.strftime(TIME_FMT),
        items=list(old.items), delay=old.delay, order=old.order,
        missing=list(old.missing), anchor=ANCHOR_ENGINE,
        from_rotation=old.from_rotation or old.anchor == ANCHOR_ROTATION,
        seen={i: when for i in drawn}, inferred=list(drawn), changes=len(drawn),
        # Stamped so the reconciler does not reach back into the old pass and
        # credit its displays to this one.
        last_poll=_now(), last_reconcile=_now(),
        restarted_at=_now(), restarted_from=f"{old.seen_count}/{old.total}",
        previous_id=old.id)
    if old.current and old.current == deck.current:
        cycle.current, cycle.current_since = old.current, old.current_since
        if old.current_since:
            cycle.seen[old.current] = old.current_since
            cycle.inferred = [i for i in cycle.inferred if i != old.current]
    return cycle


def diverged(old: list[str], new: list[str]) -> bool:
    """True when `new` is a different playlist rather than an edit of `old`."""
    a, b = set(old), set(new)
    if not b:
        return False
    if not a:
        return True
    return len(a & b) / len(b) < NEW_CYCLE_OVERLAP


def estimate_minutes(remaining: int, delay: int, changes: int, seen: int) -> int:
    """Wallpaper-time left until every item has been shown at least once.

    `remaining * delay`, stretched by what a newly seen wallpaper has actually
    cost so far: `changes / seen` displays each. On a playlist being walked
    through that ratio is 1 and the answer is the obvious one; where wallpapers
    do come round again it grows with the evidence.

    It deliberately does not model the order. An earlier version switched to the
    coupon-collector expectation for drawing with replacement as soon as a
    single repeat appeared, which turned a 2h40m estimate into 4d21h on one
    stray event — and the evidence was against that model anyway: 192 distinct
    wallpapers out of 193 displays is a shuffled playthrough, where drawing with
    replacement would have yielded about 125. Repeats are also only visible in
    the displays actually watched, never in the ones rebuilt from file times, so
    they are too weak a signal to pivot a model on.
    """
    if remaining <= 0:
        return 0
    # Below a handful of displays the ratio is noise — one repeat out of three
    # would inflate the answer by a third — so assume a clean playthrough until
    # there is enough of a cycle to measure.
    rate = changes / seen if seen >= MIN_SAMPLE else 1.0
    return int(round(remaining * max(rate, 1.0) * delay))


def format_minutes(minutes: int) -> str:
    """'2h 15m' / '3d 4h' / '—'."""
    minutes = int(minutes)
    if minutes <= 0:
        return "—"
    days, rest = divmod(minutes, 60 * 24)
    hours, mins = divmod(rest, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {mins}m"
    return f"{mins}m"


def elapsed_since(stamp: str | None) -> str:
    """How long ago `stamp` was, as '2h 15m'. '—' only when there is no stamp."""
    started = _parse(stamp)
    if started is None:
        return "—"
    minutes = int((datetime.now() - started).total_seconds() // 60)
    return format_minutes(minutes) if minutes > 0 else "≤1m"


# ---- Rebuilding what was shown while nothing was watching -----------------
#
# The live probe only sees what happens while it is running, which would make
# the count a fragile private tally: close the app for a day and it silently
# falls behind. Wallpaper Engine itself is no help — its playlists are ordered
# "random" and it stores neither the shuffle order nor a position, so there is
# nothing to read back.
#
# NTFS is the help. With last-access updates on (the Windows default), showing a
# wallpaper stamps its file's access time, so the filesystem already holds the
# history the tracker missed. That turns the tracker into a reconciler: it
# reads those stamps whenever it adopts a playlist or notices a gap in its own
# polling, and fills in everything that was shown while it was away.

# Access-time granularity on NTFS is one hour, which is well under the delay
# between wallpapers, so nothing is lost by it.

# A playlist sharing this much with a rotation run came from that run.
ROTATION_OVERLAP = 0.5
# Fallback anchoring: inside a running cycle wallpapers are shown day after day,
# so only a break this long reads as "the previous playlist ended here".
MIN_GAP_DAYS = 4
# Polling that stopped for longer than this left a hole worth reconciling.
GAP_MINUTES = 5

# Even while polling steadily, the access times are re-read this often. Not
# every wallpaper can be caught by the handle probe: web wallpapers are loaded
# by a browser process that does not hold index.html open, so nothing is ever
# locked for them and they would sit in "not yet shown" for ever. Their access
# time still moves, so a periodic reconcile is the only thing that credits them.
RECONCILE_MINUTES = 10

# An access time is evidence of a display unless something read the files
# wholesale: Wallpaper Engine refreshing its browser or building a playlist, a
# rotation moving 200 folders in, a backup walking the tree. Crediting one of
# those runs the counter to the end of a playlist that has not started —
# measured on a real rotation, 178 files stamped inside 1.1 seconds and a
# playlist that had shown one wallpaper reporting 180 of 199 done.
#
# What separates the two is the **rate**, and the gap is four orders of
# magnitude: a machine reads at 160 files a second, while a person pressing
# "next" twice does it 22 seconds apart. Comparing the count against the
# playlist's delay instead — which is what this used to do — throws away real
# viewing, because nothing stops anyone skipping several wallpapers in a minute,
# and those wallpapers then sit in "not yet shown" for ever.
SCAN_RATE = 2.0        # files a second; above this nobody is watching them
BURST_ITEMS = 5        # and it takes a few to be a read rather than a coincidence
CLUSTER_GAP = 5.0      # seconds of quiet that end one cluster and begin the next



def last_access_enabled() -> bool | None:
    """Whether NTFS records access times. None when it cannot be determined."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SYSTEM\CurrentControlSet\Control\FileSystem") as key:
            value = int(winreg.QueryValueEx(key, "NtfsDisableLastAccessUpdate")[0])
    except (ImportError, OSError, ValueError, TypeError):
        return None
    # Low bits: 0/2 = updates on, 1/3 = off. Bit 31 only marks "system managed".
    return (value & 1) == 0


def scan_missing(items: list[str]) -> list[str]:
    """Playlist entries whose wallpaper has been deleted.

    Wallpaper Engine goes on listing a wallpaper after its folder is gone, and
    such an entry can never come up again — left in the total it stalls the
    count short of the end for good, and the "playlist finished" cue never
    fires.

    A file missing from a folder that has itself vanished is a different thing:
    an unmounted drive or a moved library, not a deletion. Those are left alone,
    because writing them off would wipe the whole count the moment a drive was
    slow to appear. Only entries whose containing project folder's parent is
    still there count as deleted.
    """
    missing: list[str] = []
    root_exists: dict[str, bool] = {}
    for item in items:
        path = Path(item.replace("/", "\\"))
        try:
            if path.exists():
                continue
        except OSError:
            continue
        root = path.parent.parent          # …/myprojects, …/431960
        key = str(root).lower()
        if key not in root_exists:
            try:
                root_exists[key] = root.is_dir()
            except OSError:
                root_exists[key] = False
        if root_exists[key]:
            missing.append(item)
    return missing


def scan_atimes(items: list[str]) -> dict[str, float]:
    """Last-access time per playlist item, skipping what no longer exists."""
    out: dict[str, float] = {}
    for item in items:
        try:
            out[item] = os.stat(item.replace("/", "\\")).st_atime
        except OSError:
            pass
    return out


def rotation_anchor(items: list[str]) -> str | None:
    """Timestamp of the Rotator run that put this playlist into myprojects.

    Exact rather than inferred: the run records the folder names it moved, so a
    playlist built out of them identifies its own starting moment.
    """
    try:
        from .rotator.config import HISTORY_PATH
        runs = json.loads(Path(HISTORY_PATH).read_text(encoding="utf-8")).get("runs", [])
    except (ImportError, OSError, ValueError):
        return None
    folders = {Path(i.replace("/", "\\")).parent.name.lower() for i in items}
    if not folders:
        return None
    for run in runs:                      # newest first
        moved = {str(m).lower() for m in run.get("moved", [])}
        if moved and len(folders & moved) / len(folders) >= ROTATION_OVERLAP:
            return str(run.get("timestamp") or "") or None
    return None


def gap_anchor(atimes: dict[str, float]) -> str | None:
    """Where the current run of access times begins, for playlists no rotation built."""
    stamps = sorted(atimes.values(), reverse=True)
    if len(stamps) < 4:
        return None
    limit = MIN_GAP_DAYS * 24 * 3600
    for newer, older in zip(stamps, stamps[1:]):
        if newer - older > limit:
            return datetime.fromtimestamp(newer).strftime(TIME_FMT)
    return None   # no break found — the whole list looks like one run, don't guess


def cycle_anchor(items: list[str], atimes: dict[str, float]) -> tuple[str | None, str]:
    """(when this playlist started, how that was worked out)."""
    stamp = rotation_anchor(items)
    if stamp:
        return stamp, ANCHOR_ROTATION
    stamp = gap_anchor(atimes)
    if stamp:
        return stamp, ANCHOR_FILE_TIMES
    return None, ANCHOR_NONE


def is_bulk_read(count: int, span: float) -> bool:
    """Whether this many access times, this close together, are a machine reading.

    Not "more than the playlist's cadence allows" — a person skipping wallpapers
    by hand breaks that rule constantly, and treating them as a scan is how six
    wallpapers ended up stuck in "not yet shown" on a playlist that had already
    come round. Only a rate no viewer could produce counts.
    """
    return count >= BURST_ITEMS and count / max(span, 0.1) >= SCAN_RATE


def plausible(fresh: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """Of these access times, the ones that can be wallpaper displays.

    Access times are grouped by the quiet between them: anything read within
    `CLUSTER_GAP` of the last one belongs to the same cluster, so a bulk read
    stays a single clump however long it runs, while wallpapers watched or
    skipped land in clusters of their own. A clump read faster than anyone could
    watch is dropped whole; everything else is kept.

    Deliberately nothing else. A rule of the form "no sweep may credit more than
    so much of the playlist" sounds prudent and is wrong: reconstructing a cycle
    that has been running for days *should* credit most of it at once, and that
    rule refuses exactly the case the reconstruction exists for.
    """
    kept: list[tuple[str, float]] = []
    group: list[tuple[str, float]] = []

    def take(g: list[tuple[str, float]]) -> None:
        if g and not is_bulk_read(len(g), g[-1][1] - g[0][1]):
            kept.extend(g)

    for entry in sorted(fresh, key=lambda pair: pair[1]):
        if group and entry[1] - group[-1][1] > CLUSTER_GAP:
            take(group)
            group = []
        group.append(entry)
    take(group)
    return kept


def backfill(cycle: "Cycle", atimes: dict[str, float], since: float) -> int:
    """Mark every item read since `since` as already shown. Returns how many.

    Only the access times that could plausibly be displays are credited — see
    `plausible`, which is what keeps a library scan out of the count.
    """
    fresh = [(item, when) for item, when in atimes.items()
             if when >= since and item not in cycle.seen]
    fresh = plausible(fresh)
    for item, when in fresh:
        cycle.seen[item] = datetime.fromtimestamp(when).strftime(TIME_FMT)
        cycle.inferred.append(item)
    if fresh:
        # Those were real changes, they just were not watched.
        cycle.changes = max(cycle.changes, len(cycle.seen))
    return len(fresh)


# ---- What comes next -------------------------------------------------------
#
# Wallpaper Engine offers a playlist two orders, and they are the values its UI
# writes into config.json: "random" and "sorted". Only one of them has a queue.
#
# Sorted walks the playlist in the order of its `items` and wraps round, so what
# is still to come is simply the rest of that list, starting after the wallpaper
# on screen. Random has no queue anywhere a program can read: config.json stores
# the order setting and nothing else, the log says nothing about playlists, and
# the order observed on this machine has no relation to the list at all — across
# 36 displays watched live, not one step to the next position, and a rank
# correlation of +0.07 between when a wallpaper came up and where it sits. What
# the data does show is that each pass is a shuffle, not a dice roll: 196
# changes on a 189-wallpaper playlist repeated one wallpaper, where drawing with
# replacement repeats about seventy. So in random order every wallpaper still
# waiting is exactly as likely to be next as any other, and any order put on
# them would be invented.

ORDER_SORTED = "sorted"


def queue_is_known(order: str) -> bool:
    """Whether Wallpaper Engine's order for this playlist can be predicted."""
    return (order or "").lower() == ORDER_SORTED


def upcoming(items: list[str], waiting: set[str], order: str,
             after: str | None) -> list[str]:
    """The wallpapers still waiting, in the order they will come if that is known.

    `after` is the wallpaper the queue continues from — the one on screen, or
    failing that the last one shown. In sorted order the list is read from just
    past it and round again; in random order there is no queue, and the
    playlist's own order is kept only because it is stable, not because it
    means anything.
    """
    if not queue_is_known(order) or after not in items:
        return [i for i in items if i in waiting]
    start = items.index(after) + 1
    return [i for i in items[start:] + items[:start] if i in waiting]


# ---- Wallpaper Engine's own record of the pass ------------------------------
#
# The handle probe and the access times were the only witnesses there were when
# the tracker was written. Wallpaper Engine turned out to keep the record itself:
# playliststate.bin names, per monitor, the wallpaper on screen and every entry
# this pass has not drawn yet (see wallpaper_timer). For a random playlist that
# *is* the count — what the pass has drawn has been shown, what is waiting has
# not — and it holds across the hours nothing was running to watch.
#
# Checked against the live tracker before switching: the deck named the same
# wallpaper on screen as the probe, on both monitors, and it disagreed with the
# count in three places, every one of them an access-time credit — 181 counted
# on a playlist the engine had drawn 180 of, 6 on one it had drawn 4 of (a
# project.json something had read, an mp4 read while the tray was starting).
# Each such credit makes "playlist finished" arrive a wallpaper early. The probe
# itself was never wrong, but it cannot see a web wallpaper at all — twenty on
# one playlist here — and it opens every file in the playlist on every look.
#
# So where the deck describes the playlist it is the source, and the probe and
# the access-time sweep run only where it does not: a sorted playlist, whose
# deck has not been checked against a real one, or a state file that does not
# fit what config.json says.

ORDER_RANDOM = "random"
# A write the tracker looked at this soon after it happened was watched as it
# happened; one found later came up while nothing was looking.
FRESH_WRITE_SECONDS = 60
# The longest a running tracker goes between looks: the longest heartbeat the
# tab offers, and a minute's grace.
MAX_LOOK_GAP_SECONDS = 3600 + 60


def deck_describes(cycle: "Cycle", deck: MonitorDeck | None) -> bool:
    """Whether the engine's deck can stand in for watching this cycle's playlist.

    Only a random playlist, the one order whose deck has been checked against a
    real playlist; only when the wallpaper on screen is in the playlist and no
    longer waiting, as the current wallpaper of a pass must be; and only when
    the deck is of this playlist, give or take what config.json and the state
    file disagree on between their writes.
    """
    if deck is None or (cycle.order or "").lower() != ORDER_RANDOM:
        return False
    items = set(cycle.items)
    waiting = set(deck.waiting)
    if deck.current not in items or deck.current in waiting:
        return False
    return len(waiting - items) <= max(2, DECK_TOLERANCE * len(waiting))


def pass_finished(cycle: "Cycle", deck: MonitorDeck) -> bool:
    """Whether a pass the engine has just replaced had been drawn to its end.

    Everything counted but the wallpaper now on screen: whether Wallpaper Engine
    writes out an empty deck before it shuffles the next one, or shuffles as it
    draws the last, the last is then the one showing.
    """
    unseen = {i for i in cycle.live_items if i not in cycle.seen}
    return bool(cycle.live_items) and unseen <= {deck.current}


# ---- The snapshot handed to the UI ----------------------------------------

@dataclass
class Progress:
    cycle_id: str
    monitor: str
    playlist: str
    seen: int
    total: int
    changes: int
    repeats: int
    order: str
    delay: int
    started: str
    current: str | None
    current_title: str
    current_since: str | None
    live: bool                # a playlist file was actually found open
    inferred: int             # of `seen`, how many came from file access times
    anchor: str               # how the cycle's start was established
    gone: int                 # playlist entries whose wallpaper was deleted
    from_rotation: bool = False
    restarted_at: str | None = None     # Wallpaper Engine started the playlist over
    restarted_from: str | None = None   # what the previous cycle had reached
    previous_id: str | None = None      # and which cycle that was
    from_engine: bool = False           # counted from Wallpaper Engine's own record

    @property
    def remaining(self) -> int:
        return max(self.total - self.seen, 0)

    @property
    def previous_finished(self) -> bool:
        """Whether the cycle before a restart had been shown to its end."""
        try:
            reached, total = (int(n) for n in (self.restarted_from or "").split("/"))
        except ValueError:
            return False
        return total > 0 and reached >= total

    @property
    def percent(self) -> int:
        return int(round(100 * self.seen / self.total)) if self.total else 0

    @property
    def eta_minutes(self) -> int:
        # Nothing is drawn twice within one of Wallpaper Engine's passes, so a
        # repeat rate carried over from the probe's count — 22 "repeats" on a
        # playlist of 189 here, most of them the probe losing track — says
        # nothing about what is left of a pass being followed.
        changes = self.seen if self.from_engine else self.changes
        return estimate_minutes(self.remaining, self.delay, changes, self.seen)

    @property
    def finish_estimate(self) -> str | None:
        """When the playlist should be done, at the pace this cycle has kept.

        `eta_minutes` counts wallpaper time, which only passes while Wallpaper
        Engine is running and unpaused — with the machine off overnight, or the
        wallpaper paused behind a fullscreen game, real time runs much slower.
        This projects from what the cycle has actually managed per hour instead,
        which is the number that answers "when can I rotate?".
        """
        started = _parse(self.started)
        if started is None or self.seen < MIN_SAMPLE or self.remaining <= 0:
            return None
        elapsed = (datetime.now() - started).total_seconds()
        if elapsed <= 0:
            return None
        seconds_left = elapsed / self.seen * self.remaining
        return (datetime.now() + timedelta(seconds=seconds_left)).strftime("%d %b %H:%M")

    @property
    def label(self) -> str:
        return f"{self.seen}/{self.total}"


def pick_primary(results: list["Progress"], preferred: str | None = None) -> "Progress | None":
    """The monitor the tray icon and the tab lead with.

    An explicit choice wins. Failing that it is the playlist a rotation built,
    because that is the one whose end is the cue to rotate again — a subscribed
    or hand-made playlist just runs forever and says nothing about timing.
    """
    if not results:
        return None
    for p in results:
        if p.monitor == preferred:
            return p
    for p in results:
        if p.anchor == ANCHOR_ROTATION or p.from_rotation:
            return p
    return results[0]


# ---- Persistent state -----------------------------------------------------

class TrackerState:
    """`data/tracker.json`: the live cycle per monitor plus the finished ones.

    A tray and a separately started toolkit window can both write this file, so
    a save re-reads what is on disk and merges first. The merge is three-way:
    what the other writer added since this state was loaded is kept, and what
    this one took away stays away. A plain union used to do, while every update
    only ever added a `seen` entry; it stopped doing once the engine's record
    could withdraw a credit, because the union put each one straight back.
    """

    MAX_ARCHIVE = 40

    def __init__(self, cycles: dict[str, Cycle] | None = None,
                 archive: list[dict] | None = None):
        self.cycles = cycles or {}
        self.archive = archive or []
        # Each cycle as it stood on disk when loaded: id, seen, inferred, changes.
        self._base: dict[str, tuple[str, set[str], set[str], int]] = {}
        self._remember()

    def _remember(self) -> None:
        self._base = {k: (c.id, set(c.seen), set(c.inferred), c.changes)
                      for k, c in self.cycles.items()}

    def fingerprint(self) -> str:
        """Everything a save would write, bar when each cycle was last looked at."""
        cycles = {k: {f: v for f, v in c.to_dict().items() if f != "last_poll"}
                  for k, c in self.cycles.items()}
        return json.dumps([cycles, len(self.archive), self.archive[:1]],
                          sort_keys=True, ensure_ascii=False)

    @classmethod
    def load(cls) -> "TrackerState":
        try:
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        cycles = {k: Cycle.from_dict(v) for k, v in (data.get("cycles") or {}).items()}
        return cls(cycles, list(data.get("archive") or []))

    def save(self) -> None:
        disk = TrackerState.load()
        for key, mine in self.cycles.items():
            theirs = disk.cycles.get(key)
            if theirs is None or theirs.id != mine.id:
                continue  # different cycle entirely — ours wins
            base_id, base_seen, base_inferred, base_changes = self._base.get(
                key, (None, set(), set(), None))
            if base_id != mine.id:
                # Not loaded as this cycle: everything on disk is theirs to keep.
                base_seen, base_inferred, base_changes = set(), set(), None
            for item, when in theirs.seen.items():
                if item not in base_seen:
                    mine.seen.setdefault(item, when)
            if theirs.changes != base_changes:
                mine.changes = max(mine.changes, theirs.changes)
            mine.inferred = sorted((set(mine.inferred) | (set(theirs.inferred) - base_inferred))
                                   & set(mine.seen))
        if len(disk.archive) > len(self.archive):
            self.archive = disk.archive

        payload = {
            "cycles": {k: c.to_dict() for k, c in self.cycles.items()},
            "archive": self.archive[:self.MAX_ARCHIVE],
        }
        tmp = STATE_PATH.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            os.replace(tmp, STATE_PATH)
        except OSError:
            pass
        self._remember()

    def archive_cycle(self, cycle: Cycle, reason: str | None = None) -> None:
        """Retire a cycle: keep the summary, drop the bulky item/seen lists."""
        entry = {
            "id": cycle.id,
            "monitor": cycle.monitor,
            "playlist": cycle.playlist,
            "started": cycle.started,
            "ended": _now(),
            "seen": cycle.seen_count,
            "total": cycle.total,
            "changes": cycle.changes,
        }
        if reason:
            entry["reason"] = reason
        self.archive.insert(0, entry)
        del self.archive[self.MAX_ARCHIVE:]


# ---- When to look ---------------------------------------------------------
#
# The tracker used to look every 30 seconds whether or not anything had
# happened. On a 10-minute playlist nineteen looks in twenty found nothing, and
# each still cost about 50 ms on the tray's GUI thread — config.json parsed,
# 1618 files opened and 1618 stat'ed on this machine — plus a rewrite of
# tracker.json: some 2900 looks and 570 MB of rewrites a day, for about 290
# changes. And a change still reached the count up to half a minute late, while
# the countdown ring beside it had already started over.
#
# Wallpaper Engine says when something happens. It rewrites playliststate.bin
# at every wallpaper change, a skip included, and config.json when it starts,
# exits or saves a playlist. So the tracker looks when either file is rewritten,
# within a second of it — every change, not one in thirty seconds, so wallpapers
# skipped in a hurry are not lost between two looks.
#
# A slow heartbeat stays for what no write announces: a wallpaper deleted from
# disk, the periodic access-time sweep, a change the files somehow failed to
# report. And where there is no state file to follow — a Wallpaper Engine that
# does not write one, or writes one this cannot parse — the tracker looks as
# often as it always did, because then nothing else will tell it.

HEARTBEAT_SECONDS = 300
FALLBACK_SECONDS = 30
# tracker.json is written when a look changed something, and otherwise only
# once the last-looked stamps in it are this old.
LAST_POLL_REFRESH = 600
# Deleted wallpapers are looked for on a timer of their own, and whenever
# config.json or the cycle changes, rather than on every look: a stat of every
# file in the playlist was most of what a look still cost — 12 ms of 15 for
# 1618 files here — and a deletion only matters to the count once a pass is
# nearly through, which takes days.
MISSING_EVERY = 300
# How often the two files are stat'ed. The state file is written about three
# seconds after the change it records, so a second more is lost in the noise.
WATCH_SECONDS = 1


class PollSchedule:
    """Says when the tracker should look again, from Wallpaper Engine's own writes."""

    def __init__(self, files: EngineFiles, heartbeat: float = HEARTBEAT_SECONDS,
                 clock: Callable[[], float] = time.monotonic):
        self.files = files
        self.heartbeat = heartbeat
        self.clock = clock
        self._seen: tuple[int, int] | None = None     # file versions last looked at
        self._last: float | None = None

    @property
    def following(self) -> bool:
        """Whether there is a state file to follow, rather than a timer to poll on."""
        return self.files.state_ok

    def due(self) -> str | None:
        """Why the tracker should look now, or None when it need not."""
        self.files.refresh()
        if self._last is None:
            return "first look"
        if (self.files.config_version, self.files.state_version) != self._seen:
            return "Wallpaper Engine wrote its files"
        every = self.heartbeat if self.following else FALLBACK_SECONDS
        if self.clock() - self._last >= every:
            return "heartbeat"
        return None

    def looked(self) -> None:
        """Call just before the tracker looks: what it is about to see counts as seen.

        Before rather than after, so a file rewritten while the look runs is
        still new to the next `due` — at worst one look too many, never one
        too few.
        """
        self._seen = (self.files.config_version, self.files.state_version)
        self._last = self.clock()


# ---- The tracker ----------------------------------------------------------

class Tracker:
    """Reads Wallpaper Engine and keeps `data/tracker.json` up to date."""

    def __init__(self, config_path: str | None = None, files: EngineFiles | None = None):
        self.config_path = config_path or find_we_config() or DEFAULT_WE_CONFIG or ""
        # Shared with the countdown in the tray, so each file is watched once.
        self.files = files or EngineFiles(self.config_path)
        self.error: str | None = None
        self.atime_ok = last_access_enabled()
        self.missing_every = MISSING_EVERY
        # monitor -> (when its deletions were last looked for, config version, cycle id)
        self._missing_at: dict[str, tuple[float, int, str]] = {}

    # ----- reconciling with the filesystem ---------------------------------

    def _adopt(self, cycle: Cycle) -> None:
        """A playlist the tracker has not been watching: recover its history."""
        if not self.atime_ok:
            return
        if cycle.anchor == ANCHOR_ENGINE:
            # Dated by Wallpaper Engine's own pass. Re-anchoring it to the
            # rotation would pull the previous pass's displays back in.
            started = _parse(cycle.started)
            if started is not None:
                backfill(cycle, scan_atimes(cycle.items), started.timestamp())
            return
        atimes = scan_atimes(cycle.items)
        stamp, how = cycle_anchor(cycle.items, atimes)
        cycle.anchor = how
        if stamp is None:
            return
        cycle.started = stamp
        cycle.last_reconcile = _now()
        started = _parse(stamp)
        if started is not None:
            backfill(cycle, atimes, started.timestamp())

    def _catch_up(self, cycle: Cycle) -> None:
        """Credit whatever the handle probe did not see.

        Two things end up here. Time when nothing was polling at all, and
        wallpapers the probe can never catch however faithfully it polls — a web
        wallpaper's `index.html` is read once by a browser process and never
        held, so it is only ever visible in its access time.
        """
        if not self.atime_ok:
            return
        if cycle.last_poll is None:
            # State written before the tracker could reconstruct history — the
            # cycle has never been reconciled, so treat it as newly adopted.
            # A reset stamps last_poll, so it never lands here.
            self._adopt(cycle)
            return
        # Reconciling from the last sweep rather than the last poll covers a gap
        # too: it is never later than last_poll.
        last_sweep = _parse(cycle.last_reconcile)
        if last_sweep is None:
            # A cycle from before there was periodic reconciling. Falling back
            # to last_poll here would wedge it for good — that stamp is renewed
            # every poll, so the interval below could never elapse. Reach back
            # to the start of the cycle once instead, then keep to the schedule.
            since = _parse(cycle.started) or _parse(cycle.last_poll)
        else:
            since = last_sweep
            idle = (datetime.now() - since).total_seconds()
            if idle < min(GAP_MINUTES, RECONCILE_MINUTES) * 60:
                return
        if since is None:
            return
        backfill(cycle, scan_atimes(cycle.items), since.timestamp())
        cycle.last_reconcile = _now()

    def rebuild(self) -> int:
        """Re-derive every cycle from file access times. Returns items recovered.

        A cycle that follows Wallpaper Engine's own record is left alone: that
        record is what access times were only ever a guess at.
        """
        state = TrackerState.load()
        self.files.refresh()
        decks = self.files.decks if self.files.state_ok else {}
        recovered = 0
        for monitor, cycle in state.cycles.items():
            if deck_describes(cycle, decks.get(monitor)):
                continue
            before = cycle.seen_count
            self._adopt(cycle)
            recovered += cycle.seen_count - before
        state.save()
        return recovered

    # ----- following the engine's record -------------------------------------

    def _drawn_at(self, item: str, written: float | None, now: datetime) -> str:
        """When a wallpaper the deck says was drawn came up, as near as can be told.

        Its access time where those are kept — the engine read the file to show
        it — unless that is later than the write that recorded the draw, which
        makes it some other read. Otherwise the write itself, which is no
        earlier than the draw.
        """
        if self.atime_ok:
            try:
                atime = os.stat(item.replace("/", "\\")).st_atime
            except OSError:
                atime = None
            # The file is read to show it a few seconds before the draw is written.
            if atime is not None and atime <= (written or now.timestamp()) + 5:
                return datetime.fromtimestamp(atime).strftime(TIME_FMT)
        if written is None:
            return now.strftime(TIME_FMT)
        return datetime.fromtimestamp(written - WRITE_LAG).strftime(TIME_FMT)

    def _follow_deck(self, cycle: Cycle, deck: MonitorDeck, written: float | None) -> None:
        """Bring the cycle into line with Wallpaper Engine's record of the pass.

        Whatever the pass has drawn is shown; whatever is still waiting is not,
        whatever the count said. The wallpaper on screen is seen, and when its
        change was looked at within moments of the engine writing it down it is
        dated to the second from that write. Anything else drawn since the last
        look came up unwatched — skipped through between two looks, or while
        nothing was running — and is marked so, dated as well as can be.
        """
        now = datetime.now()
        waiting = set(deck.waiting)
        # A manual reset set these aside. One back in the deck, or on screen
        # again, has been dealt again and counts as soon as it is drawn.
        cycle.excluded = [i for i in cycle.excluded
                          if i not in waiting and i != deck.current]
        withdrawn = [i for i in cycle.seen if i in waiting]
        for item in withdrawn:
            del cycle.seen[item]
        if withdrawn:
            gone_back = set(withdrawn)
            cycle.inferred = [i for i in cycle.inferred if i not in gone_back]
            cycle.changes = max(cycle.changes - len(withdrawn), 0)

        last = _parse(cycle.last_poll)
        watched = (written is not None and last is not None
                   and -1 <= written - last.timestamp() <= MAX_LOOK_GAP_SECONDS
                   and now.timestamp() - written <= FRESH_WRITE_SECONDS)
        if deck.current != cycle.current or cycle.current_since is None:
            cycle.current = deck.current
            cycle.current_since = (
                datetime.fromtimestamp(written - WRITE_LAG).strftime(TIME_FMT)
                if watched else self._drawn_at(deck.current, written, now))

        gone = set(cycle.missing)
        skip = set(cycle.excluded)
        fresh = [i for i in cycle.items
                 if i not in waiting and i not in gone and i not in skip and i not in cycle.seen]
        for item in fresh:
            if item == deck.current:
                cycle.seen[item] = cycle.current_since
            else:
                cycle.seen[item] = self._drawn_at(item, written, now)
                cycle.inferred.append(item)
        cycle.changes = max(cycle.changes + len(fresh), len(cycle.seen))

    def poll(self) -> list[Progress]:
        """One look: read the playlists, find what is playing, record it."""
        self.files.refresh()
        if self.files.config is None:
            self.error = f"Cannot read Wallpaper Engine's config.json: {self.files.config_error}"
            return []
        views = playlists_in(self.files.config)
        if not views:
            self.error = ("No active playlist in config.json. Apply a playlist in Wallpaper "
                          "Engine and restart it — the config is only written on exit.")
            return []
        self.error = None

        state = TrackerState.load()
        before = state.fingerprint()
        # Written back anyway once the stamps on disk grow old, so a gap in the
        # looking still reads as one — to the reconciler, and to a change that
        # has to tell whether it was watched.
        stale = any((_parse(c.last_poll) or datetime.min)
                    < datetime.now() - timedelta(seconds=LAST_POLL_REFRESH)
                    for c in state.cycles.values())
        claimed: set[str] = set()
        out: list[Progress] = []
        decks = self.files.decks if self.files.state_ok else {}
        written = self.files.state_written
        engine = wallpaper_engine_process() if decks else None

        for view in views:
            cycle = state.cycles.get(view.monitor)
            deck = decks.get(view.monitor)
            if cycle is None or diverged(cycle.items, view.items):
                if cycle is not None:
                    state.archive_cycle(cycle)
                cycle = Cycle.start(view)
                cycle.from_rotation = False
                state.cycles[view.monitor] = cycle
                self._adopt(cycle)
                cycle.from_rotation = cycle.anchor == ANCHOR_ROTATION
            else:
                cycle.sync(view)
                if deck is not None and engine_restarted(cycle, deck):
                    finished = pass_finished(cycle, deck)
                    if finished and deck.current not in cycle.seen:
                        # Shuffled as the last one was drawn: that one closed
                        # the old pass, whatever the new one goes on to do.
                        cycle.seen[deck.current] = self._drawn_at(
                            deck.current, written, datetime.now())
                        cycle.changes += 1
                    state.archive_cycle(
                        cycle, "shown to the end; Wallpaper Engine began the next pass"
                        if finished else "Wallpaper Engine started the playlist over")
                    cycle = restart_from_engine(cycle, deck, written,
                                                engine[1] if engine else None)
                    state.cycles[view.monitor] = cycle
                elif not deck_describes(cycle, deck):
                    self._catch_up(cycle)

            # Re-checked as the cycle goes rather than once: a wallpaper deleted
            # mid cycle has to stop counting, and one restored has to start
            # again. See MISSING_EVERY for why not on every look.
            checked = self._missing_at.get(view.monitor)
            if (checked is None or checked[1:] != (self.files.config_version, cycle.id)
                    or time.monotonic() - checked[0] >= self.missing_every):
                cycle.missing = scan_missing(cycle.items)
                self._missing_at[view.monitor] = (
                    time.monotonic(), self.files.config_version, cycle.id)

            from_engine = deck_describes(cycle, deck)
            if from_engine:
                self._follow_deck(cycle, deck, written)
                claimed.add(deck.current)
                live = engine is not None
            else:
                current = probe_current(cycle.live_items, prefer=cycle.current,
                                        exclude=claimed)
                if current:
                    claimed.add(current)
                    if current != cycle.current:
                        # First sight of a wallpaper the reconstruction already
                        # credited is not a transition — it is the one that was
                        # on screen all along, so it must not read as a repeat.
                        resync = cycle.current is None and current in cycle.seen
                        cycle.current = current
                        cycle.current_since = _now()
                        if not resync:
                            cycle.changes += 1
                            cycle.seen.setdefault(current, cycle.current_since)
                live = current is not None
            cycle.last_poll = _now()

            out.append(Progress(
                cycle_id=cycle.id,
                monitor=cycle.monitor,
                playlist=cycle.playlist,
                seen=cycle.seen_count,
                total=cycle.total,
                changes=cycle.changes,
                repeats=cycle.repeats,
                order=cycle.order,
                delay=cycle.delay,
                started=cycle.started,
                current=cycle.current,
                current_title=title_for(cycle.current) if cycle.current else "",
                current_since=cycle.current_since,
                live=live,
                inferred=cycle.inferred_count,
                anchor=cycle.anchor,
                gone=cycle.gone_count,
                from_rotation=cycle.from_rotation,
                restarted_at=cycle.restarted_at,
                restarted_from=cycle.restarted_from,
                previous_id=cycle.previous_id,
                from_engine=from_engine,
            ))

        # Most looks change nothing but the time of looking — a heartbeat, a
        # config.json rewritten with the same playlists — and rewriting 200 KB
        # for that was most of what tracker.json was ever written for.
        if stale or state.fingerprint() != before:
            state.save()
        return out

    # ----- queries used by the UI ------------------------------------------

    def cycle(self, monitor: str) -> Cycle | None:
        return TrackerState.load().cycles.get(monitor)

    def split_items(self, monitor: str) -> tuple[list[tuple[str, str, bool]], list[str]]:
        """(shown, remaining) for one monitor.

        Shown is newest first as (path, when, from_file_times). Remaining is in
        the order Wallpaper Engine will play it when the playlist is sorted; a
        random playlist has no such order — see :func:`upcoming`.
        """
        cycle = self.cycle(monitor)
        if cycle is None:
            return [], []
        inferred = set(cycle.inferred)
        gone = set(cycle.missing)
        shown = [(item, when, item in inferred)
                 for item, when in sorted(cycle.seen.items(),
                                          key=lambda kv: kv[1], reverse=True)
                 if item not in gone]
        # Deleted wallpapers are not waiting to be shown, they are simply not
        # there — listing them is what made this panel unopenable.
        waiting = {i for i in cycle.items if i not in cycle.seen and i not in gone}
        # The queue continues from what is on screen; if nothing was caught on
        # screen, from the last wallpaper known to have been shown.
        after = cycle.current
        if after not in cycle.items and cycle.seen:
            after = max(cycle.seen.items(), key=lambda kv: kv[1])[0]
        remaining = upcoming(cycle.items, waiting, cycle.order, after)
        return shown, remaining

    def queue_known(self, monitor: str) -> bool:
        """Whether `split_items` gives the remaining wallpapers in playing order."""
        cycle = self.cycle(monitor)
        return cycle is not None and queue_is_known(cycle.order)

    def reset(self, monitor: str) -> None:
        """Archive the monitor's cycle and start counting from zero."""
        state = TrackerState.load()
        cycle = state.cycles.get(monitor)
        if cycle is None:
            return
        state.archive_cycle(cycle)
        fresh = state.cycles[monitor] = Cycle(
            monitor=cycle.monitor, playlist=cycle.playlist, started=_now(),
            items=list(cycle.items), delay=cycle.delay, order=cycle.order,
            missing=list(cycle.missing),
            # Stamped so the reconciler reads this as a watched cycle starting
            # now, and does not immediately restore what was just cleared.
            last_poll=_now(), last_reconcile=_now())
        self.files.refresh()
        deck = self.files.decks.get(monitor) if self.files.state_ok else None
        if deck_describes(fresh, deck):
            # The engine's pass goes on regardless, and following it would
            # restore the whole count at the next look. What it has drawn so
            # far is set aside instead, bar the wallpaper on screen — the one
            # a reset has always started from.
            waiting = set(deck.waiting)
            fresh.excluded = [i for i in fresh.items
                              if i not in waiting and i != deck.current]
            fresh.current = deck.current
            fresh.current_since = (cycle.current_since if cycle.current == deck.current
                                   else _now())
            fresh.seen = {deck.current: fresh.current_since}
            fresh.changes = 1
        state.save()

    def archive(self) -> list[dict]:
        return TrackerState.load().archive
