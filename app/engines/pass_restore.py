"""Putting back a pass Wallpaper Engine threw away.

Wallpaper Engine starts a monitor's pass over whenever the playlist is applied
to that monitor again: a monitor attached after it started, or the playlist
saved in it. Everything already shown then comes round again. The tracker
notices (`tracker.engine_restarted`) and archives the old pass with what it
had shown.

A pass is put back the way the Rotator rewrites a playlist: close Wallpaper
Engine, write that monitor's deck in ``playliststate.bin`` as the playlist less
everything shown, give the tracker its old cycle back, and start Wallpaper
Engine again — it resumes the deck it finds.
"""
from __future__ import annotations

import copy
import shutil
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable

from PySide6.QtCore import QThread, Signal

from . import engine_control
from ..settings import app_data_dir
from .playlist_refresh import _replace
from .tracker import (RESET_REASON, TIME_FMT, Cycle, TrackerState, last_access_enabled,
                      scan_atimes)
from .wallpaper_timer import SECTION, STATE_FILE, read_state_file, write_state_file

# A playlist is still the one the pass ran on when it keeps its name and at
# least this share of what the pass had shown.
SAME_PLAYLIST_SHARE = 0.9


@dataclass
class Restorable:
    monitor: str
    entry: dict                 # the archive entry of the thrown-away pass
    current: Cycle              # the cycle the reset began
    shown: dict[str, str]       # item -> when: the old pass and everything since
    source: str                 # "saved" | "access times"

    @property
    def total(self) -> int:
        gone = set(self.current.missing)
        return sum(1 for i in self.current.items if i not in gone)

    @property
    def seen(self) -> int:
        return len(self.shown)


def _stamp(when: float) -> str:
    return datetime.fromtimestamp(when).strftime(TIME_FMT)


def shown_from_atimes(items: list[str], atimes: dict[str, float],
                      started: datetime, ended: datetime) -> dict[str, str]:
    """The items whose file was last read while the pass ran."""
    lo, hi = started.timestamp(), ended.timestamp()
    return {i: _stamp(atimes[i]) for i in items if i in atimes and lo <= atimes[i] < hi}


def find_restorable(state: TrackerState, monitor: str,
                    atimes: Callable[[list[str]], dict[str, float]] = scan_atimes,
                    atime_ok: bool | None = True) -> tuple[Restorable | None, str]:
    """The pass Wallpaper Engine last threw away on `monitor`, if it can be put
    back — else None and why not, in words for the page."""
    current = state.cycles.get(monitor)
    if current is None:
        return None, f"Nothing is tracked on {monitor} yet."
    entry = next((e for e in state.archive if current.previous_id
                  and e.get("id") == current.previous_id), None)
    if entry is None or entry.get("reason") != RESET_REASON:
        return None, f"Wallpaper Engine has not thrown a pass away on {monitor}."
    if entry.get("restored"):
        return None, "That pass was already restored."
    items, gone = set(current.items), set(current.missing)
    if "shown" in entry:
        old, source = dict(entry["shown"]), "saved"
    elif atime_ok:
        started = datetime.strptime(entry["started"], TIME_FMT)
        ended = datetime.strptime(entry["ended"], TIME_FMT)
        old = shown_from_atimes(current.items, atimes(current.items), started, ended)
        source = "access times"
    else:
        return None, "No record of what that pass showed."
    if not old:
        return None, "No record of what that pass showed."
    if (entry.get("playlist") != current.playlist
            or len(old.keys() & items) < SAME_PLAYLIST_SHARE * len(old)):
        return None, "The playlist has changed since the reset."
    shown = {i: w for i, w in {**old, **current.seen}.items() if i in items and i not in gone}
    found = Restorable(monitor, entry, current, shown, source)
    if found.seen >= found.total:
        return None, "That pass had already been shown to the end."
    return found, ""


def plan_deck(items: list[str], shown: Iterable[str], missing: Iterable[str],
              current: str) -> list[str]:
    """What is still to come, in playlist order."""
    skip = set(shown) | set(missing) | {current}
    return [i for i in items if i not in skip]


def restored_cycle(r: Restorable) -> Cycle:
    """The old pass again, with everything shown since the reset counted in."""
    cycle = copy.deepcopy(r.current)
    cycle.id = r.entry["id"]
    cycle.started = r.entry["started"]
    cycle.seen = dict(r.shown)
    cycle.changes = int(r.entry.get("changes", 0)) + r.current.changes
    cycle.restarted_at = cycle.restarted_from = cycle.previous_id = None
    return cycle


def rewrite_state(data: bytes, monitor: str, current: str, waiting: list[str]) -> bytes:
    """playliststate.bin with `monitor` showing `current` and `waiting` to come."""
    sections = read_state_file(data)
    for section in sections:
        if section.name != SECTION:
            continue
        for record in section.monitors:
            if record.name == monitor:
                record.current = current
                record.waiting = list(waiting)
                record.mark = 0
                record.marks = [0] * len(waiting)
                return write_state_file(sections)
    raise ValueError(f"{monitor} is not in playliststate.bin")


# ---- Putting it back ---------------------------------------------------------

@dataclass
class RestoreResult:
    ok: bool
    message: str            # one sentence, for the page, the balloon and the journal
    seen: int = 0
    total: int = 0


def _on_screen(data: bytes, monitor: str) -> str | None:
    for section in read_state_file(data):
        if section.name == SECTION:
            for record in section.monitors:
                if record.name == monitor:
                    return record.current
    return None


def restore(monitor: str, say: Callable[[str], None] = lambda _m: None) -> RestoreResult:
    """Close Wallpaper Engine, put back the pass it threw away on `monitor`,
    start it again. Wallpaper Engine is started whenever it was closed."""
    atime_ok = last_access_enabled()
    found, why = find_restorable(TrackerState.load(), monitor, atime_ok=atime_ok)
    if found is None:
        return RestoreResult(False, why)
    engine = engine_control.running()
    if engine is None:
        return RestoreResult(False, "Wallpaper Engine is not running.")
    say("Closing Wallpaper Engine...")
    if not engine_control.close(engine):
        return RestoreResult(False, "Wallpaper Engine did not close, so nothing was changed.")
    backup = app_data_dir() / "pass-restore" / "playliststate.bin"
    try:
        path = engine.install_dir / STATE_FILE
        data = path.read_bytes()
        # Looked at again now that it is closed: it may have moved on meanwhile.
        state = TrackerState.load()
        found, why = find_restorable(state, monitor, atime_ok=atime_ok)
        if found is None:
            result = RestoreResult(False, why)
        else:
            current = _on_screen(data, monitor) or found.current.current or ""
            found.shown.setdefault(current, datetime.now().strftime(TIME_FMT))
            deck = plan_deck(found.current.items, found.shown, found.current.missing, current)
            say(f"Putting back the pass on {monitor}: {len(deck)} still to come...")
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup)
            _replace(path, rewrite_state(data, monitor, current, deck))
            cycle = restored_cycle(found)
            cycle.current = current
            state.cycles[monitor] = cycle
            found.entry["restored"] = datetime.now().strftime(TIME_FMT)
            state.save()
            result = RestoreResult(True, f"Pass restored on {monitor}: "
                                         f"{found.seen}/{found.total}.",
                                   found.seen, found.total)
    except Exception as e:  # noqa: BLE001 — Wallpaper Engine must come back regardless
        result = RestoreResult(False, f"Restoring failed: {e}")
    say("Starting Wallpaper Engine again...")
    if not engine_control.start(engine):
        result = RestoreResult(False, f"{result.message} Wallpaper Engine did not come back — "
                                      f"start it by hand; the old state is in {backup}.")
    return result


class RestoreWorker(QThread):
    """`restore` off the GUI thread. `finished_restore(RestoreResult)`."""
    finished_restore = Signal(object)

    def __init__(self, monitor: str):
        super().__init__()
        self.monitor = monitor

    def run(self):
        try:
            result = restore(self.monitor)
        except Exception as e:  # noqa: BLE001
            result = RestoreResult(False, f"Unexpected error: {e}")
        self.finished_restore.emit(result)
