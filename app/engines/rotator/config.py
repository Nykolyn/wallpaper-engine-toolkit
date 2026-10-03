"""Configuration and persistent state management.

Both files are written whole, to a temporary name that then replaces the old
file, so a write cut short leaves the previous version rather than half of the
new one. Neither is ever overwritten because it could not be read: an
unreadable file is set aside under a name that says so, first.

The history also keeps a snapshot of itself after every save, in
``history_backup/``. A history that is missing or unreadable at start-up is
put back from the newest snapshot that reads, and the History tab says so. It
went missing once without anyone noticing — a build emptied the folder it was
in — and the next rotation simply began a new one.

Both files are read by older builds too, after a newer one wrote them, so
neither ever gains a key an older reader would choke on: `RunRecord` keeps its
shape, and what a run needs beyond it goes in ``run_meta.json`` (see
``meta.py``). Keys a newer build did add are carried through a save.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, asdict, field, fields
from datetime import datetime
from pathlib import Path

from .. import steam_paths
from ...settings import app_data_dir


CONFIG_PATH = app_data_dir() / "config.json"
HISTORY_PATH = app_data_dir() / "history.json"
# Snapshots of the history, newest last by name. At one save per rotation this
# reaches back months; each is a few kilobytes per run.
KEEP_SNAPSHOTS = 30
# Windows will not replace a file another thread has open at that moment — the
# Snapshot reads these on a thread of its own — so a replace is tried again a
# few times before the save is given up.
REPLACE_TRIES = 8
REPLACE_WAIT = 0.05


def _write_atomically(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    for attempt in range(REPLACE_TRIES):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == REPLACE_TRIES - 1:
                raise
            time.sleep(REPLACE_WAIT)


def new_run_id() -> str:
    """A run's id: what history.json, run_meta.json and its log file share."""
    return uuid.uuid4().hex[:8]


def _set_aside(path: Path) -> Path | None:
    """Rename a file that could not be read, so nothing writes over it.

    Returns the new name, or None if it could not be moved — in which case the
    caller must not write to ``path`` either.
    """
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    kept = path.with_name(f"{path.stem}.unreadable-{stamp}{path.suffix}")
    try:
        path.replace(kept)
    except OSError:
        return None
    return kept


@dataclass
class Config:
    """The three folders a rotation moves between, how many to keep, and
    whether Wallpaper Engine's playlist is brought along.

    Only ``destination`` can be worked out in advance — it is Wallpaper
    Engine's own myprojects folder, which Steam put somewhere specific. The
    reserve and the duplicates bin are folders you choose, so they start
    empty and the Rotator tab asks for them rather than inventing them.

    Steam's answer is found in the background (steam_paths.known), so a
    default destination can still be "" when this is made, as with no Steam
    at all; `take_found_folders` fills it in once the answer is there.
    """

    source: str = ""
    destination: str = field(
        default_factory=lambda: steam_paths.as_text(steam_paths.known(steam_paths.myprojects_dir)))
    duplicates: str = ""
    count: int = 1000
    # Close Wallpaper Engine for the move, refill the playlist built from the
    # previous rotation with the new set, and start it again on a fresh pass.
    refresh_playlist: bool = True

    def __post_init__(self) -> None:
        # Keys a newer build wrote, carried through a save; and why the file on
        # disk must not be saved over, when it must not (see `load`).
        self._extra: dict = {}
        self.problem = ""
        self.notice = ""
        # The fields that hold a default rather than what the file said, and
        # whether the defaults are still to be written (see `_save_defaults`).
        self._defaulted: set[str] = {f.name for f in fields(self)}
        self._unsaved = False

    @classmethod
    def load(cls) -> "Config":
        """The settings on disk, or the defaults — saved — when there are none.

        A value of the wrong type falls back to its default alone. A file that
        does not read is renamed ``config.unreadable-<time>.json`` before the
        defaults are written; one that can be neither read nor renamed is left
        as it is, and the defaults it gives meanwhile refuse to be saved over it.
        """
        if CONFIG_PATH.exists():
            try:
                data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("it does not hold an object")
                return cls.from_dict(data)
            except Exception as err:  # noqa: BLE001 — anything unreadable is set aside below
                kept = _set_aside(CONFIG_PATH)
                if kept is None:
                    # Still there and still unreadable: use the defaults for
                    # now, and leave the file for a later start or a person.
                    c = cls()
                    c.problem = (f"The Rotator's settings in {CONFIG_PATH} could not be "
                                 f"read ({err}), nor moved aside; they are left as they "
                                 f"are and not saved over.")
                    c.notice = c.problem
                    return c
                notice = (f"config.json could not be read ({err}); it is kept as "
                          f"{kept.name} and the defaults are used.")
                c = cls()
                c.notice = notice
                c._save_defaults()
                return c
        c = cls()
        c._save_defaults()
        return c

    def _save_defaults(self) -> None:
        """Write the defaults, unless Steam's answer is not in yet: written then,
        the destination it stands in for would be "" for good."""
        if steam_paths.found():
            self.save()
        else:
            self._unsaved = True

    def take_found_folders(self) -> bool:
        """Once Steam's folders are known (steam_paths.when_found): its myprojects
        for a destination that was only ever an empty default, and the save of the
        defaults that waited for it. True when the destination changed. Not
        saved over a file that could not be read (`problem`).

        Raises OSError when the save cannot be made.
        """
        changed = False
        if "destination" in self._defaulted and not self.destination:
            self.destination = steam_paths.as_text(steam_paths.known(steam_paths.myprojects_dir))
            changed = bool(self.destination)
        if (changed or self._unsaved) and not self.problem:
            self._unsaved = False
            self.save()
        return changed

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        """Settings from a stored object: unknown keys kept, a value of the wrong
        type replaced by its default."""
        defaults = cls()
        known = {f.name for f in fields(cls)}
        defaulted = set(known)
        values = {}
        for name in known:
            value, default = data.get(name, getattr(defaults, name)), getattr(defaults, name)
            # bool is an int to isinstance, and neither may pass for the other.
            if type(default) is bool:
                ok = type(value) is bool
            elif type(default) is int:
                ok = type(value) is int and value > 0
            else:
                ok = isinstance(value, type(default))
            values[name] = value if ok else default
            if ok and name in data:
                defaulted.discard(name)
        c = cls(**values)
        c._extra = {k: v for k, v in data.items() if k not in known}
        c._defaulted = defaulted
        return c

    def save(self) -> None:
        """Write the settings. Raises OSError, the file untouched, while the one
        on disk could not be read or moved aside (`problem`)."""
        if self.problem:
            raise PermissionError(self.problem)
        data = {**self._extra, **asdict(self)}
        _write_atomically(CONFIG_PATH, json.dumps(data, indent=2, ensure_ascii=False))


@dataclass
class RunRecord:
    """A single rotation run, stored in history."""
    id: str
    timestamp: str
    moved: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    returned: int = 0
    failed: list[str] = field(default_factory=list)
    history_reset: bool = False

    @property
    def moved_count(self) -> int:
        return len(self.moved)

    @property
    def duplicate_count(self) -> int:
        return len(self.duplicates)

    @classmethod
    def from_dict(cls, data: dict) -> "RunRecord":
        """Read one stored run. Keys this version does not know are kept.

        Before, an unknown key made the whole file unreadable, which made the
        history empty, which the next rotation then saved — so going back to an
        older version after a newer one had added a field lost every run.
        """
        if not isinstance(data, dict):
            raise ValueError(f"a run is {type(data).__name__}, not an object")
        known = {f.name for f in fields(cls)}
        record = cls(**{k: v for k, v in data.items() if k in known})
        for name in ("moved", "duplicates", "failed"):
            if not isinstance(getattr(record, name), list):
                raise ValueError(f"run {record.id}: '{name}' is not a list")
        record._extra = {k: v for k, v in data.items() if k not in known}
        return record

    def to_dict(self) -> dict:
        return {**asdict(self), **getattr(self, "_extra", {})}


def _read_runs(path: Path) -> list[RunRecord]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("runs"), list):
        raise ValueError("there is no list of runs in it")
    return [RunRecord.from_dict(r) for r in data["runs"]]


def read_runs(path: Path | None = None) -> list[RunRecord]:
    """The runs in the history file, newest first, for a reader that only counts.

    Unlike `History.load` it never writes: nothing is set aside, restored or
    saved, and anything wrong with the file is raised (FileNotFoundError when
    there is none yet). Putting a damaged history right is the Rotator's job,
    not a counter's that happens to look first.
    """
    return _read_runs(HISTORY_PATH if path is None else path)


def snapshot_dir() -> Path:
    return HISTORY_PATH.parent / "history_backup"


def snapshots() -> list[Path]:
    """Snapshots of the history, newest first."""
    folder = snapshot_dir()
    if not folder.is_dir():
        return []
    return sorted(folder.glob("history-*.json"), reverse=True)


class History:
    """Persistent history of runs + derived 'used' set for uniqueness."""

    def __init__(self, runs: list[RunRecord], notice: str = "", problem: str = ""):
        self.runs = runs
        # What was wrong with the file on disk and what was done about it, for
        # the History tab, the confirmation before a rotation and the first
        # lines of the next run's log. Empty when the file simply read.
        self.notice = notice
        # Set only when the history cannot be saved without destroying a file
        # that could not be read. A rotation will not start while it is, and
        # save() refuses.
        self.problem = problem

    def find(self, run_id: str) -> RunRecord | None:
        return next((r for r in self.runs if r.id == run_id), None)

    def number(self, record: RunRecord) -> int:
        """The run's number, counted from the oldest (1); 0 when not in the history."""
        for i, r in enumerate(self.runs):
            if r is record or r.id == record.id:
                return len(self.runs) - i
        return 0

    @classmethod
    def load(cls) -> "History":
        if HISTORY_PATH.exists():
            try:
                return cls(_read_runs(HISTORY_PATH))
            except Exception as err:  # noqa: BLE001 — every kind is handled alike
                kept = _set_aside(HISTORY_PATH)
                if kept is None:
                    problem = (f"The rotation history in {HISTORY_PATH} could not be "
                               f"read ({err}), nor moved aside. It is left as it is, "
                               f"and no rotation will run until it reads or is moved.")
                    return cls([], notice=problem, problem=problem)
                what = (f"history.json could not be read ({err}); "
                        f"it is kept as {kept.name}")
        elif snapshots():
            what = "history.json was missing"
        else:
            return cls([])                      # never saved: the first rotation

        for snapshot in snapshots():
            try:
                runs = _read_runs(snapshot)
            except Exception:  # noqa: BLE001 — try the next older one
                continue
            history = cls(runs, notice=(
                f"{what}. {len(runs)} runs were put back from the snapshot "
                f"{snapshot.name}."))
            history.save()
            return history
        return cls([], notice=f"{what}, and no snapshot could be read. "
                              f"The history starts again from the next rotation.")

    def save(self) -> None:
        if self.problem:
            raise RuntimeError(self.problem)
        text = json.dumps({"runs": [r.to_dict() for r in self.runs]},
                          indent=2, ensure_ascii=False)
        _write_atomically(HISTORY_PATH, text)
        self._snapshot(text)

    def _snapshot(self, text: str) -> None:
        """Keep a copy of what was just saved; drop all but the newest few."""
        if not self.runs:
            return
        folder = snapshot_dir()
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        _write_atomically(folder / f"history-{stamp}-{len(self.runs):04d}runs.json", text)
        for old in snapshots()[KEEP_SNAPSHOTS:]:
            try:
                old.unlink()
            except OSError:
                pass

    def add(self, record: RunRecord) -> None:
        self.runs.insert(0, record)  # newest first
        self.save()

    def usage(self) -> "Usage":
        """last_used / never_used for every folder, worked out once."""
        return Usage(self.runs)

    def last_used(self, name: str) -> datetime | None:
        return self.usage().last_used(name)

    def never_used(self, name: str) -> bool:
        return self.usage().never_used(name)

    def used_set(self) -> set[str]:
        """All folder names ever moved, lower-cased, since the last history reset."""
        used: set[str] = set()
        for r in self.runs:  # newest first
            if r.history_reset:
                # everything from this run onward (older) was before a reset boundary
                for name in r.moved:
                    used.add(name.lower())
                break
            for name in r.moved:
                used.add(name.lower())
        return used


def _stamp(text: str) -> datetime | None:
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        try:
            return datetime.fromisoformat(str(text)).replace(tzinfo=None)
        except (TypeError, ValueError):
            return None


class Usage:
    """When each folder was last moved into myprojects, and whether the next
    run may draw it — the LAST USED column and the New chip of the Rotator's
    tables. Worked out once from the runs, so a lookup costs nothing across
    33 000 rows.

    `never_used` is what the next run draws from: not moved in since the
    history last started over (`used_set`). A folder can have a last use from
    before that and still be never used in this sense; the page shows both.
    """

    def __init__(self, runs: list[RunRecord]):
        self._last: dict[str, datetime] = {}
        for record in runs:                       # newest first
            when = _stamp(record.timestamp)
            if when is None:
                continue
            for name in record.moved:
                self._last.setdefault(name.lower(), when)
        self._used = History(runs).used_set()

    def last_used(self, name: str) -> datetime | None:
        return self._last.get(name.lower())

    def never_used(self, name: str) -> bool:
        return name.lower() not in self._used
