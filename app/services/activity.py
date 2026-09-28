"""The ActivityJournal: what happened, one line per event, for Overview.

`data/activity.jsonl` holds one JSON object a line, appended and never
rewritten:

    {"ts": "2026-09-28T13:47:02+03:00", "tool": "rotator", "kind": "run.clean",
     "title": "Run 38 finished", "detail": "1 000 moved in · 998 returned",
     "chip": null, "run": "3f9a0c1e"}

- `ts` is local time with its offset, to the second.
- `tool` is the tool it happened in (`TOOLS`); Overview draws its icon.
- `kind` is `<activity>.<outcome>` for the end of a piece of work, or one of
  `EVENTS`: see `ACTIVITIES`. A reader that does not know a kind still shows
  the entry, from its title.
- `title` and `detail` are what a row says; `chip` is a kit Chip variant
  (`Duplicated`, `New`, `Failed`, …) or null; `run` is the Rotator's run id.

Readers ignore fields they do not know, and lines they cannot read, so a
newer build may add to an entry and an older one still reads the file. Past
`MAX_BYTES` the file is renamed to `activity.1.jsonl` (replacing the one
before) and a new one begun: the journal never holds much more than twice
that, and `recent()` reads across both.

Only the window writes it. The tray has no journal: the Tracker's events
come from the window's own `TrackerFeed` (`PlaylistWatch`), so they cover
the time the window is open, and Overview reads `tracker.json` for the rest.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from . import textfile
from .jobs import RESULTS

FILE_NAME = "activity.jsonl"
ROTATED_NAME = "activity.1.jsonl"
MAX_BYTES = 2_000_000

TOOLS = ("rotator", "tracker", "review", "creator", "copier")

# The kinds this build writes. A piece of work ends as `<activity>.<outcome>`,
# the outcome being the JobCenter's result (clean, problems, stopped, failed);
# a few things that are not the end of a job have kinds of their own.
OUTCOMES = RESULTS
ACTIVITIES = {
    "run": ("rotator", "a rotation"),
    "check": ("rotator", "the check for folders Wallpaper Engine cannot show "
                         "(journalled only when it finds some, or fails)"),
    "cleanup": ("rotator", "deleting the folders ticked after that check"),
    "duplicates_delete": ("rotator", "deleting folders in the duplicates folder"),
    "duplicates_return": ("rotator", "moving them back into the reserve"),
    "scan": ("review", "a Review scan of a Wallpaper Engine folder"),
    "count": ("review", "counting what each author has published since the last visit"),
    "database": ("review", "writing the review to the authors database"),
    "build": ("creator", "a Creator build"),
    "copy": ("copier", "a Copier run"),
}
EVENTS = {
    "run.started": ("rotator", "a rotation began"),
    "duplicates.set_aside": ("rotator", "a rotation found folders already in the reserve "
                                        "and moved them to the duplicates folder"),
    "playlist.advanced": ("tracker", "the leading monitor showed a wallpaper not yet "
                                     "shown in this cycle"),
    "playlist.finished": ("tracker", "a playlist has shown every wallpaper in it"),
    "playlist.restarted": ("tracker", "Wallpaper Engine started a playlist over"),
}


def known_kind(kind: str) -> bool:
    """Whether this build writes entries of this kind."""
    activity, _, outcome = kind.rpartition(".")
    return kind in EVENTS or (activity in ACTIVITIES and outcome in OUTCOMES)


@dataclass(frozen=True)
class Entry:
    ts: datetime            # local time, naive, like every other time in the app
    tool: str
    kind: str
    title: str
    detail: str = ""
    chip: str | None = None
    run: str | None = None

    @property
    def outcome(self) -> str:
        """The part of the kind after the dot: `clean`, `set_aside`, …"""
        return self.kind.rpartition(".")[2]

    def to_json(self) -> dict:
        return {"ts": self.ts.astimezone().isoformat(timespec="seconds"),
                "tool": self.tool, "kind": self.kind, "title": self.title,
                "detail": self.detail, "chip": self.chip, "run": self.run}

    @classmethod
    def from_json(cls, data) -> "Entry | None":
        """An entry from one parsed line; None when it is not one.

        Fields this build does not know are left out. A line without a time,
        a tool, a kind or a title is not an entry.
        """
        if not isinstance(data, dict):
            return None
        try:
            ts = datetime.fromisoformat(str(data["ts"]))
        except (KeyError, ValueError):
            return None
        if ts.tzinfo is not None:
            ts = ts.astimezone().replace(tzinfo=None)
        tool, kind, title = data.get("tool"), data.get("kind"), data.get("title")
        if not all(isinstance(v, str) and v for v in (tool, kind, title)):
            return None
        chip, run = data.get("chip"), data.get("run")
        return cls(ts, tool, kind, title, str(data.get("detail") or ""),
                   chip if isinstance(chip, str) and chip else None,
                   str(run) if run not in (None, "") else None)


class ActivityJournal(QObject):
    """`data/activity.jsonl`: append an entry, read the newest few.

    An append is one short line to a file on the local disk — measured at
    well under a millisecond, and a run makes a handful — so it is written
    where it happens, on the GUI thread. `appended(entry)` follows each one.
    A write that fails loses that line only; `error` says why.
    """

    appended = Signal(object)       # Entry

    def __init__(self, path: str | Path, parent: QObject | None = None, *,
                 max_bytes: int = MAX_BYTES):
        super().__init__(parent)
        self.path = Path(path)
        self.rotated_path = self.path.with_name(ROTATED_NAME)
        self.max_bytes = max_bytes
        self.error = ""
        self._lock = threading.Lock()

    def add(self, tool: str, kind: str, title: str, detail: str = "",
            chip: str | None = None, run: str | None = None,
            ts: datetime | None = None) -> Entry:
        entry = Entry((ts or datetime.now()).replace(microsecond=0), tool, kind,
                      title, detail or "", chip or None, run or None)
        line = json.dumps(entry.to_json(), ensure_ascii=False, separators=(",", ":")) + "\n"
        data = line.encode("utf-8")
        with self._lock:
            try:
                self._rotate_for(len(data))
                with open(self.path, "ab") as f:
                    f.write(data)
                self.error = ""
            except OSError as err:
                self.error = f"{self.path.name} could not be written: {err}"
        self.appended.emit(entry)
        return entry

    def _rotate_for(self, incoming: int) -> None:
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            return
        if size and size + incoming > self.max_bytes:
            os.replace(self.path, self.rotated_path)

    def recent(self, n: int) -> list[Entry]:
        """The newest `n` entries, newest first, across both files.

        Read from the end: only as much of the file as the entries take.
        """
        found: list[Entry] = []
        if n <= 0:
            return found
        with self._lock:
            for path in (self.path, self.rotated_path):
                for line in textfile.reverse_lines(path):
                    if not line.strip():
                        continue
                    try:
                        entry = Entry.from_json(json.loads(line))
                    except ValueError:
                        continue
                    if entry is not None:
                        found.append(entry)
                        if len(found) >= n:
                            return found
        return found


# ---- the Tracker's events --------------------------------------------------

@dataclass(frozen=True)
class _Seen:
    """What a monitor's playlist looked like at the last look."""
    cycle_id: str
    seen: int
    total: int
    current: str | None
    restarted_at: str | None


def _seen(p) -> _Seen:
    return _Seen(p.cycle_id, p.seen, p.total, p.current, p.restarted_at)


def playlist_events(before: _Seen, p, *, leading: bool) -> list[tuple[str, str, str]]:
    """(kind, title, detail) for what changed on one monitor between two looks.

    - `playlist.restarted` when Wallpaper Engine started the playlist over;
    - `playlist.finished` when the count reached the end within one cycle;
    - `playlist.advanced` when a wallpaper not yet shown in this cycle came
      up, on the leading monitor only: it is the one whose end is the cue to
      rotate, and the others would bury everything else.
    """
    events: list[tuple[str, str, str]] = []
    monitor = p.monitor
    if p.restarted_at and p.restarted_at != before.restarted_at:
        reached = f" · the previous count had reached {p.restarted_from}" if p.restarted_from else ""
        events.append(("playlist.restarted",
                       "Wallpaper Engine started the playlist over", f"{monitor}{reached}"))
        return events
    if p.cycle_id != before.cycle_id:
        return events                   # a new cycle: a rotation says so itself
    if p.total > 0 and before.seen < before.total and p.seen >= p.total:
        cue = " · time to rotate" if leading else ""
        events.append(("playlist.finished", f"Playlist finished on {monitor}",
                       f"all {p.total} shown{cue}"))
    elif leading and p.seen > before.seen and p.current and p.current != before.current:
        name = p.current_title or p.current
        events.append(("playlist.advanced", f"Playlist advanced to #{p.seen}",
                       f"{name} on {monitor}"))
    return events


class PlaylistWatch(QObject):
    """Journal entries from what the window's TrackerFeed sees.

    Only changes seen while the window is open: the first look at a monitor
    is where it starts from, not an event.
    """

    def __init__(self, feed, journal: ActivityJournal, settings=None,
                 parent: QObject | None = None):
        super().__init__(parent)
        self._feed = feed
        self._journal = journal
        self._settings = settings
        self._last: dict[str, _Seen] = {}
        feed.updated.connect(self._looked)
        feed.config_changed.connect(self._last.clear)

    def _looked(self) -> None:
        from ..engines.tracker import pick_primary
        results = list(self._feed.results or [])
        preferred = self._settings.get("tracker", "primary", None) if self._settings else None
        lead = pick_primary(results, preferred)
        for p in results:
            before = self._last.get(p.monitor)
            self._last[p.monitor] = _seen(p)
            if before is None:
                continue
            for kind, title, detail in playlist_events(before, p, leading=p is lead):
                self._journal.add("tracker", kind, title, detail)
