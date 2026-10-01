"""The Snapshot: the few numbers Overview and the sidebar show, kept ready.

Counting the reserve means listing tens of thousands of folders on the W:
hard disk, which is never done on the GUI thread (REDESIGN_PLAN §2.4). So the
numbers are worked out on a thread of their own, kept in memory, and handed
out as they were last read:

- `RESERVE` — folders in the reserve, how many were never used (what the
  next run draws from), and whether the next run resets the history. This is
  `Rotator.preview()`, run on the worker.
- `ROTATION` — folders in myprojects, the `[protected]` ones apart, and how
  many rotations moved in today.
- `LAST_RUN` — the last rotation: its number, when, and how it ended, from
  the history and the `run_meta.json` beside it (see engines/rotator/meta.py).
- `PLAYLIST` — the leading monitor's count, from the window's `TrackerFeed`
  (already in memory: no worker needed).
- `REVIEW` — the last review's summary from `data/review_last.json`, once
  step 11 writes it; None until then. `ReviewState.from_json` reads it, and
  its doc is the file's shape.

Every value is a `Reading`: the value, when it was read, and what went wrong
if the last try failed — in which case the value is the one read before, so
the UI can say "last known" and how old it is rather than show a zero.

Refreshed when a job that changes them finishes (`JobCenter.finished`), when
the TrackerFeed looks, and on demand (`refresh()`, "Refresh now"). One
refresh runs at a time; asking during one queues the keys for straight after.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

from .jobs import RESULTS

RESERVE = "reserve"
ROTATION = "rotation"
LAST_RUN = "last_run"
PLAYLIST = "playlist"
REVIEW = "review"
KEYS = (RESERVE, ROTATION, LAST_RUN, PLAYLIST, REVIEW)
# What the worker reads, and what a finished job of each tool may have changed.
# The reserve and myprojects come from one listing, so they go together.
_FROM_DISK = (RESERVE, ROTATION, LAST_RUN, REVIEW)
AFTER_JOB = {
    "rotator": (RESERVE, ROTATION, LAST_RUN),
    "creator": (ROTATION,),
    "copier": (ROTATION,),
    "review": (REVIEW,),
}

RUN_META = "run_meta.json"          # the Rotator's side file, beside history.json
REVIEW_LAST = "review_last.json"    # step 11's summary of the last review


@dataclass(frozen=True)
class Reading:
    """A value as last read. `at` is a `time.time()`; None: never read yet.

    When the last try failed, `error` says why in words and `reason` in one
    of `REASONS`, for a page to pick its empty state by: `unset` (the folder
    is not chosen: "set it in Settings"), `missing` (chosen, not found),
    `unreadable` (a file that would not parse), `error` (anything else).
    """
    value: Any = None
    at: float | None = None
    error: str = ""
    reason: str = ""

    def age(self, now: float | None = None) -> float | None:
        """Seconds since it was read, or None if it never was."""
        if self.at is None:
            return None
        return max(0.0, (time.time() if now is None else now) - self.at)

    @property
    def known(self) -> bool:
        return self.value is not None


@dataclass(frozen=True)
class ReserveCounts:
    folders: int            # in the reserve now
    never_used: int         # what the next run may draw from, after the return
    will_reset: bool        # fewer than `batch` of them: the history starts over
    batch: int              # folders a run moves in
    path: str


@dataclass(frozen=True)
class RotationCounts:
    folders: int            # in myprojects, the [protected] ones not counted
    protected: int
    moved_today: int        # moved in by today's runs, from the history
    path: str


@dataclass(frozen=True)
class RunSummary:
    number: int             # the nth rotation in the history
    id: str
    started: datetime | None
    result: str             # clean / problems / stopped / failed
    moved: int
    returned: int
    duplicates: int
    failed: int
    finished: datetime | None = None
    seconds: float | None = None
    # True when the result and times came from run_meta.json; False when the
    # result is worked out from the record (a failure means problems).
    from_side_file: bool = False


@dataclass(frozen=True)
class PlaylistProgress:
    monitor: str
    seen: int
    total: int
    remaining: int
    percent: int
    started: str
    finish_estimate: str | None     # "21 Sep 09:10", an estimate
    live: bool                      # the playlist file was found open
    current: str | None = None
    title: str = ""
    # counted from Wallpaper Engine's own record of the pass: not live then
    # means Wallpaper Engine is not running, rather than nothing on screen
    from_engine: bool = False


def parse_estimate(estimate: str | None, now: datetime) -> datetime | None:
    """The tracker's finish estimate ("21 Sep 09:10", no year) as a moment:
    this year's, or next year's for an estimate past New Year."""
    if not estimate:
        return None
    try:
        when = datetime.strptime(estimate, "%d %b %H:%M").replace(year=now.year)
    except ValueError:
        return None
    if (now - when).days > 180:
        when = when.replace(year=now.year + 1)
    return when


@dataclass(frozen=True)
class ReviewState:
    """The last review, as `review_last.json` holds it. The Review page writes
    the file (`engines/review_flow.Session.to_json`):

        {"scanned": "2026-09-18T09:10:00",   when the scan finished
         "scope": "folder:new",               what it read (`engines/review` scopes)
         "since": "2026-09-13",               the oldest last visit among the authors
         "items": 89,                         new items found, every author together
         "authors": [{"name": "…", "new": 14, "done": false}, …],
                                              the authors with new items; done once
                                              gone through in the gallery
         "checked": 118,                      authors counted
         "finished": null}                    when "Finish review" wrote the database

    and a few more the page reads back itself. A field that is missing or of
    the wrong kind is not known (None), never 0; fields this build does not
    know are ignored.
    """
    scanned: datetime | None = None
    scope: str = ""
    since: date | None = None
    items: int | None = None
    authors: int | None = None      # with new items
    waiting: int | None = None      # of them, not gone through yet
    finished: datetime | None = None
    checked: int | None = None      # authors counted, with something new or not

    @classmethod
    def from_json(cls, data) -> "ReviewState":
        if not isinstance(data, dict):
            return cls()
        authors = data.get("authors")
        if isinstance(authors, list):
            rows = [a for a in authors if isinstance(a, dict)]
            count, waiting = len(rows), sum(1 for a in rows if a.get("done") is not True)
        else:
            count, waiting = _count(authors), None
        finished = _parse_time(data.get("finished")) if data.get("finished") else None
        since = _parse_time(data.get("since"))
        scope = data.get("scope")
        return cls(scanned=_parse_time(data.get("scanned")),
                   scope=scope if isinstance(scope, str) else "",
                   since=since.date() if since else None, items=_count(data.get("items")),
                   authors=count, waiting=0 if finished and waiting is None else waiting,
                   finished=finished, checked=_count(data.get("checked")))


def _count(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


class Snapshot(QObject):
    """Cached numbers for Overview and the sidebar. See the module doc."""

    refreshed = Signal(list)            # the keys that have a new Reading
    _computed = Signal(object, object)  # from the worker: {key: Reading}, keys asked

    def __init__(self, parent: QObject | None = None, *, data_dir: str | Path | None = None,
                 jobs=None, feed=None, settings=None):
        super().__init__(parent)
        if data_dir is None:
            from ..settings import app_data_dir
            data_dir = app_data_dir()
        self.data_dir = Path(data_dir)
        self._settings = settings
        self._feed = feed
        self._readings: dict[str, Reading] = {key: Reading() for key in KEYS}
        self._worker: threading.Thread | None = None
        self._queued: set[str] = set()
        self._computed.connect(self._take)
        if jobs is not None:
            jobs.finished.connect(self._job_finished)
        if feed is not None:
            feed.updated.connect(self._feed_updated)
            feed.config_changed.connect(self._feed_updated)

    # -- reading

    def get(self, key: str) -> Reading:
        return self._readings[key]

    def __getitem__(self, key: str) -> Reading:
        return self._readings[key]

    def refreshing(self) -> bool:
        return self._worker is not None

    def put(self, key: str, value, *, at: float | None = None, error: str = "",
            reason: str = "") -> None:
        """Set a reading as if it had just been read, and say so. For fixtures
        (`tools/ui_snapshot.py`) and tests; the app reads through `refresh()`."""
        if key not in KEYS:
            raise KeyError(f"no such snapshot value: {key}")
        self._readings[key] = Reading(value, time.time() if at is None else at, error, reason)
        self.refreshed.emit([key])

    # -- refreshing

    def refresh(self, keys=None) -> None:
        """Read these keys again (all with None). Returns at once.

        The playlist is read here, from the feed in memory; everything else on
        the worker thread.
        """
        wanted = set(KEYS if keys is None else keys)
        unknown = wanted - set(KEYS)
        if unknown:
            raise KeyError(f"no such snapshot value: {', '.join(sorted(unknown))}")
        if PLAYLIST in wanted:
            self._feed_updated()
        disk = wanted & set(_FROM_DISK)
        if not disk:
            return
        if self._worker is not None:
            self._queued |= disk
            return
        self._start(disk)

    def _start(self, keys: set[str]) -> None:
        thread = threading.Thread(target=self._work, args=(frozenset(keys),),
                                  daemon=True, name="snapshot")
        self._worker = thread
        thread.start()

    def _work(self, keys: frozenset) -> None:
        try:
            found = compute(keys, self.data_dir)
        except Exception as err:  # noqa: BLE001 — a worker must report, not die
            found = {key: err for key in keys}
        try:
            self._computed.emit(found, keys)
        except RuntimeError:
            pass                # the window went while the disk was being read

    def _take(self, found: dict, keys) -> None:
        self._worker = None
        changed = []
        for key, outcome in found.items():
            before = self._readings[key]
            if isinstance(outcome, Exception):
                # Keep what was known, and say it could not be read again.
                self._readings[key] = Reading(before.value, before.at, str(outcome),
                                              getattr(outcome, "reason", "error"))
            else:
                self._readings[key] = Reading(outcome, time.time())
            changed.append(key)
        if changed:
            self.refreshed.emit(changed)
        if self._queued:
            queued, self._queued = self._queued, set()
            self._start(queued)

    def _job_finished(self, job) -> None:
        keys = AFTER_JOB.get(job.tool)
        if keys:
            self.refresh(keys)

    def _feed_updated(self) -> None:
        if self._feed is None:
            return
        try:
            value = playlist_progress(self._feed.results, self._preferred())
        except Exception as err:  # noqa: BLE001 — as a worker would report it
            before = self._readings[PLAYLIST]
            self._readings[PLAYLIST] = Reading(before.value, before.at, str(err), "error")
        else:
            self._readings[PLAYLIST] = Reading(value, time.time())
        self.refreshed.emit([PLAYLIST])

    def _preferred(self) -> str | None:
        if self._settings is None:
            return None
        return self._settings.get("tracker", "primary", None)


# ---- what the worker reads ---------------------------------------------------
#
# Plain functions of the data folder, so tests call them directly. Nothing
# here writes: the Rotator's own Config.load() saves a default config.json when
# there is none, and History.load() restores a damaged history — both are the
# Rotator page's to do, not a counter's.

REASONS = ("unset", "missing", "unreadable", "error")


class Unavailable(Exception):
    """A value that cannot be read now: why in words, and as one of REASONS."""

    def __init__(self, message: str, reason: str = "error"):
        super().__init__(message)
        if reason not in REASONS:
            raise ValueError(f"a reason is one of {', '.join(REASONS)}, not {reason!r}")
        self.reason = reason


def compute(keys, data_dir: Path) -> dict[str, Any]:
    """{key: value or the Exception that stopped it} for each key asked."""
    found: dict[str, Any] = {}
    rotator = {RESERVE, ROTATION, LAST_RUN} & set(keys)
    if rotator:
        found.update(_rotator_values(rotator))
    if REVIEW in keys:
        try:
            found[REVIEW] = review_summary(data_dir)
        except Exception as err:  # noqa: BLE001 — reported, not raised
            found[REVIEW] = err
    return found


def _rotator_values(keys: set[str]) -> dict[str, Any]:
    from ..engines.rotator import config as rconfig
    from ..engines.rotator.core import Rotator

    found: dict[str, Any] = {}
    config = read_config()
    try:
        runs = rconfig.read_runs()
        history_error = None
    except FileNotFoundError:
        runs, history_error = [], None
    except Exception as err:  # noqa: BLE001 — any unreadable history
        runs, history_error = [], Unavailable(
            f"the rotation history could not be read: {err}", "unreadable")

    if LAST_RUN in keys:
        found[LAST_RUN] = history_error or last_run(runs, rconfig.HISTORY_PATH.parent)
    if not keys & {RESERVE, ROTATION}:
        return found

    reserve_problem = _folder_unavailable("reserve", config.source)
    rotation_problem = _folder_unavailable("myprojects", config.destination)
    if reserve_problem and rotation_problem:
        found[RESERVE] = reserve_problem
        found[ROTATION] = rotation_problem
        return found

    history = rconfig.History(runs)
    counts = Rotator(config, history).preview()
    if RESERVE in keys:
        if reserve_problem:
            found[RESERVE] = reserve_problem
        elif history_error is not None:
            # Without the history every folder looks never used.
            found[RESERVE] = history_error
        else:
            found[RESERVE] = ReserveCounts(
                folders=counts["reserve_now"], never_used=counts["available_unique"],
                will_reset=counts["will_reset"], batch=counts["count"], path=config.source)
    if ROTATION in keys:
        if rotation_problem:
            found[ROTATION] = rotation_problem
        else:
            found[ROTATION] = RotationCounts(
                folders=counts["in_dest"], protected=counts["protected"],
                moved_today=moved_on(runs, date.today()), path=config.destination)
    return found


def _folder_unavailable(label: str, path: str) -> Unavailable | None:
    """Why this folder of the Rotator's cannot be counted, or None if it can."""
    from ..engines.rotator.core import folder_problem
    problem = folder_problem(label, path)
    if problem:
        return Unavailable(problem, "unset")
    if not Path(path).is_dir():
        return Unavailable(f"The {label} folder was not found: {path}", "missing")
    return None


def read_config():
    """The Rotator's settings as saved, or its defaults. Writes nothing."""
    from ..engines.rotator import config as rconfig
    defaults = rconfig.Config()
    try:
        data = json.loads(rconfig.CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return defaults
    if not isinstance(data, dict):
        return defaults
    return rconfig.Config.from_dict(data)


def _parse_time(text) -> datetime | None:
    try:
        return datetime.fromisoformat(str(text)).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def moved_on(runs, day: date) -> int:
    """Folders moved in by the runs started on this day."""
    total = 0
    for record in runs:
        started = _parse_time(record.timestamp)
        if started is not None and started.date() == day:
            total += record.moved_count
    return total


def run_meta(folder: Path) -> dict:
    """The Rotator's side file, `{run id: {...}}`; empty when absent or unreadable."""
    try:
        data = json.loads((Path(folder) / RUN_META).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def last_run(runs, folder: Path) -> RunSummary | None:
    """The newest run in the history (newest first), or None before the first."""
    if not runs:
        return None
    record = runs[0]
    meta = run_meta(folder).get(record.id)
    meta = meta if isinstance(meta, dict) else {}
    result = meta.get("result")
    from_side = result in RESULTS
    if not from_side:
        result = "problems" if record.failed else "clean"
    seconds = meta.get("seconds")
    return RunSummary(
        number=len(runs), id=record.id,
        started=_parse_time(meta.get("started")) or _parse_time(record.timestamp),
        result=result, moved=record.moved_count, returned=record.returned,
        duplicates=record.duplicate_count, failed=len(record.failed),
        finished=_parse_time(meta.get("finished")),
        seconds=float(seconds) if isinstance(seconds, (int, float)) else None,
        from_side_file=from_side)


def review_summary(data_dir: Path) -> dict | None:
    """`review_last.json` as step 11 writes it, or None while there is none."""
    path = Path(data_dir) / REVIEW_LAST
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        data = json.loads(text)
    except ValueError as err:
        raise Unavailable(f"{REVIEW_LAST} could not be read: {err}", "unreadable") from err
    if not isinstance(data, dict):
        raise Unavailable(f"{REVIEW_LAST} does not hold a summary", "unreadable")
    return data


def playlist_progress(results, preferred: str | None = None) -> PlaylistProgress | None:
    """The leading monitor's count, from what the TrackerFeed last saw."""
    from ..engines.tracker import pick_primary
    lead = pick_primary(list(results or []), preferred)
    if lead is None:
        return None
    return PlaylistProgress(
        monitor=lead.monitor, seen=lead.seen, total=lead.total,
        remaining=lead.remaining, percent=lead.percent, started=lead.started,
        finish_estimate=lead.finish_estimate, live=lead.live,
        current=lead.current, title=lead.current_title,
        from_engine=bool(getattr(lead, "from_engine", False)))
