"""run_meta.json — what history.json does not say about a run.

`history.json` is read by every build back to 2.1.1, and 2.1.1 reads a run
with `RunRecord(**r)`: one key it does not know and the whole history reads as
empty, which its next rotation then saves over the real one. So a `RunRecord`
keeps its shape for good, and everything else a run leaves behind is kept
here, beside it, keyed by the run's id:

    {"3f9a0c1e": {
        "started": "2026-09-30T13:41:00", "finished": "2026-09-30T13:58:12",
        "seconds": 1032.4, "result": "problems", "batch": 1000,
        "protected": 2, "returned_failed": ["a-folder"], "moved_failed": [],
        "playlist": ["Playlist rebuilt with 1000 wallpapers (custom)."],
        "playlist_rebuilt": true, "playlist_problem": "",
        "history_reset": false, "stopped_after": "",
        "log": "rotator/run-3f9a0c1e.log",
        "step_times": {"check": "…", "return": "…", "move": "…", "playlist": "…"},
        "retries": [{"at": "…", "retried": 2, "fixed": 2, "failed": 0, "log": "…"}]}}

- `result`: clean, problems (a folder failed, or the playlist step did not do
  its job), stopped (by the user: a stop between steps, or a cancel), failed
  (an error ended it).
- `returned_failed` / `moved_failed`: the names of `RunRecord.failed`, by the
  step they failed in, which is what a retry needs to know.
- `step_times`: when each step of the run finished, by the step keys of
  `core.STEPS`.
- `retries`: one entry per retry of the run's failures; a retry updates the
  lists and the result in place.

Times are local and naive, ISO to the second, as everywhere in the app. The
file is written whole and replaced, like the history. An unreadable one is
renamed `run_meta.unreadable-<time>.json` and a new one begun; one that can be
neither read nor renamed is never saved over. Readers ignore keys they do not
know, and an entry they cannot read is written back as it was.
"""
from __future__ import annotations

import json
import statistics
import threading
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path

from . import config as rconfig

FILE_NAME = "run_meta.json"
RESULTS = ("clean", "problems", "stopped", "failed")
# A run finished all the way; what an estimate may be taken from.
COMPLETED = ("clean", "problems")
# How many similar runs an estimate is the median of, and how similar: a batch
# within half the asked size either way.
ESTIMATE_RUNS = 5
ESTIMATE_SPREAD = 0.5

_lock = threading.Lock()


def meta_path() -> Path:
    """Beside history.json, wherever that is."""
    return rconfig.HISTORY_PATH.parent / FILE_NAME


def run_log_name(run_id: str) -> str:
    """The run's log file as the LogStore names it (`LogWriter.name`)."""
    return f"rotator/run-{run_id}.log"


def now_text(when: datetime | None = None) -> str:
    return (when or datetime.now()).replace(microsecond=0).isoformat()


def parse_time(text) -> datetime | None:
    try:
        return datetime.fromisoformat(str(text)).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


@dataclass
class RunMeta:
    started: str = ""
    finished: str = ""
    seconds: float | None = None
    result: str = ""
    batch: int = 0                      # folders the run was asked to move in
    protected: int = 0
    returned_failed: list[str] = field(default_factory=list)
    moved_failed: list[str] = field(default_factory=list)
    playlist: list[str] = field(default_factory=list)
    playlist_rebuilt: bool | None = None    # None: the run left the playlist alone
    playlist_problem: str = ""
    history_reset: bool = False
    stopped_after: str = ""             # the step a stop between steps came after
    log: str = ""
    step_times: dict[str, str] = field(default_factory=dict)
    retries: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._extra: dict = {}

    @classmethod
    def from_dict(cls, data: dict) -> "RunMeta":
        """One stored entry. Unknown keys are kept; a value of the wrong type is
        dropped for its default rather than failing the entry."""
        if not isinstance(data, dict):
            raise ValueError(f"an entry is {type(data).__name__}, not an object")
        meta = cls()
        known = {f.name for f in fields(cls)}
        for f in fields(cls):
            if f.name not in data:
                continue
            value, default = data[f.name], getattr(meta, f.name)
            if f.name == "seconds":
                ok = value is None or (type(value) in (int, float) and value >= 0)
            elif f.name == "playlist_rebuilt":
                ok = value is None or type(value) is bool
            elif type(default) is bool:
                ok = type(value) is bool
            elif type(default) is int:
                ok = type(value) is int
            elif isinstance(default, list):
                ok = isinstance(value, list)
            elif isinstance(default, dict):
                ok = isinstance(value, dict)
            else:
                ok = isinstance(value, str)
            if ok:
                setattr(meta, f.name, value)
        meta.returned_failed = [str(n) for n in meta.returned_failed]
        meta.moved_failed = [str(n) for n in meta.moved_failed]
        meta.playlist = [str(n) for n in meta.playlist]
        meta._extra = {k: v for k, v in data.items() if k not in known}
        return meta

    def to_dict(self) -> dict:
        return {**self._extra, **asdict(self)}

    @property
    def started_at(self) -> datetime | None:
        return parse_time(self.started)

    @property
    def finished_at(self) -> datetime | None:
        return parse_time(self.finished)

    def step_time(self, key: str) -> datetime | None:
        return parse_time(self.step_times.get(key))


class RunMetaStore:
    """The side file in memory: `get(run id)`, `put(run id, meta)`, `save()`."""

    def __init__(self, entries: dict[str, RunMeta] | None = None, *,
                 path: Path | None = None, notice: str = "", problem: str = ""):
        self.path = Path(path) if path is not None else meta_path()
        self.entries: dict[str, RunMeta] = dict(entries or {})
        # Entries that did not read, written back untouched.
        self._raw: dict[str, object] = {}
        self.notice = notice
        self.problem = problem

    @classmethod
    def load(cls, path: Path | None = None) -> "RunMetaStore":
        path = Path(path) if path is not None else meta_path()
        if not path.exists():
            return cls(path=path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("it does not hold an object")
        except Exception as err:  # noqa: BLE001 — every kind is handled alike
            kept = rconfig._set_aside(path)
            if kept is None:
                problem = (f"{path.name} could not be read ({err}), nor moved aside; "
                           f"it is left as it is and not saved over.")
                return cls(path=path, notice=problem, problem=problem)
            return cls(path=path, notice=f"{path.name} could not be read ({err}); "
                                         f"it is kept as {kept.name}.")
        store = cls(path=path)
        for run_id, entry in data.items():
            try:
                store.entries[str(run_id)] = RunMeta.from_dict(entry)
            except ValueError:
                store._raw[str(run_id)] = entry
        return store

    def get(self, run_id: str) -> RunMeta | None:
        return self.entries.get(run_id)

    def put(self, run_id: str, meta: RunMeta) -> None:
        self._raw.pop(run_id, None)
        self.entries[run_id] = meta

    def to_dict(self) -> dict:
        data: dict = dict(self._raw)
        data.update({run_id: meta.to_dict() for run_id, meta in self.entries.items()})
        return data

    def save(self) -> None:
        """Write the file whole. Raises OSError, the file untouched, while the one
        on disk could not be read or moved aside (`problem`)."""
        if self.problem:
            raise PermissionError(self.problem)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        rconfig._write_atomically(self.path, json.dumps(self.to_dict(), indent=1,
                                                        ensure_ascii=False))


def read_meta(path: Path | None = None) -> dict[str, RunMeta]:
    """Every entry that reads, by run id, for a reader: nothing is set aside or
    written, and a missing or unreadable file is simply empty."""
    path = Path(path) if path is not None else meta_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    found = {}
    for run_id, entry in data.items():
        try:
            found[str(run_id)] = RunMeta.from_dict(entry)
        except ValueError:
            continue
    return found


def record_meta(run_id: str, meta: RunMeta, path: Path | None = None) -> None:
    """Put one run's entry in the file on disk, read again just before, so an
    entry written meanwhile by anything else is kept. Raises OSError."""
    with _lock:
        store = RunMetaStore.load(path)
        store.put(run_id, meta)
        store.save()


def estimate_seconds(count: int, runs=None, metas: dict[str, RunMeta] | None = None,
                     *, recent: int = ESTIMATE_RUNS) -> float | None:
    """How long a run of `count` folders is likely to take: the median time of
    the newest `recent` completed runs whose batch was within half of `count`
    either way. None when there is no such run — an estimate is measured or
    not shown.

    `runs` is the history, newest first (read from disk when None); `metas`
    the side file's entries (likewise).
    """
    if count <= 0:
        return None
    if runs is None:
        try:
            runs = rconfig.read_runs()
        except Exception:  # noqa: BLE001 — no history, no estimate
            return None
    if metas is None:
        metas = read_meta()
    samples: list[float] = []
    for record in runs:
        meta = metas.get(record.id)
        if meta is None or meta.result not in COMPLETED or meta.seconds is None:
            continue
        batch = meta.batch or record.moved_count
        if batch and abs(batch - count) <= ESTIMATE_SPREAD * count:
            samples.append(float(meta.seconds))
            if len(samples) >= recent:
                break
    return statistics.median(samples) if samples else None
