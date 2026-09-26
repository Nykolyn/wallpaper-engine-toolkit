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
"""
from __future__ import annotations

import json
import os
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


def _write_atomically(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


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
    """

    source: str = ""
    destination: str = field(
        default_factory=lambda: steam_paths.as_text(steam_paths.myprojects_dir()))
    duplicates: str = ""
    count: int = 1000
    # Close Wallpaper Engine for the move, refill the playlist built from the
    # previous rotation with the new set, and start it again on a fresh pass.
    refresh_playlist: bool = True

    @classmethod
    def load(cls) -> "Config":
        if CONFIG_PATH.exists():
            try:
                data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                return cls(**{k: data.get(k, getattr(cls(), k)) for k in cls().__dict__})
            except Exception:  # noqa: BLE001 — anything unreadable is set aside below
                if _set_aside(CONFIG_PATH) is None:
                    # Still there and still unreadable: use the defaults for
                    # now, and leave the file for a later start or a person.
                    return cls()
        c = cls()
        c.save()
        return c

    def save(self) -> None:
        _write_atomically(CONFIG_PATH,
                          json.dumps(asdict(self), indent=2, ensure_ascii=False))


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
        # the History tab and the confirmation before a rotation. Empty when
        # the file simply read.
        self.notice = notice
        # Set only when the history cannot be saved without destroying a file
        # that could not be read. A rotation will not start while it is.
        self.problem = problem

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
