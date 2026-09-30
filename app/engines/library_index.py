"""library_index.py — every folder of the reserve and myprojects, described.

The Rotator's Reserve and Current tables list 33 000 folders by title, type,
author, size and when each was last used. None of that is in a folder's name:
title, type, workshop id and the preview's file name are in its
`project.json`, the size is the sum of its files, the author is what Review
has asked Steam about the workshop id, and "last used" is the history's.
Reading 33 000 `project.json` files on the hard disk takes minutes, so the
answers are kept in `data/library_meta.json` and a refresh reads again only
the folders that are new or changed.

**A file of its own, beside `library.json`.** `library.json` is Review's
index of workshop ids, read by every build since 1.x as `{name: [mtime, id]}`;
leaving it exactly as it is means no older build ever meets a shape it did
not write, and Review's refresh and this one never write the same file.

    {"format": 1, "saved": "2026-09-30T13:02:11",
     "roots": {"W:\\\\reserve": {"complete": true, "folders": {
         "night-drive": {"m": 1727600000, "t": "Night Drive", "k": "video",
                         "w": "3001", "p": "preview.jpg", "s": 91234567},
         "no-manifest": {"m": 1727600100, "u": 1}}}}}

Per folder, keyed by name and modification time (`m`): `t` title, `k` type,
`w` workshop id, `p` the preview's file name, `s` size in bytes (absent until
measured), `u` 1 when there is no readable `project.json` (the Unidentified
chip). Empty values are left out. A folder whose time changed is read again
and measured again. A folder that moved between the two roots keeps its time,
so after a rotation its entry is carried across instead of read again.

- The walk is **resumable**: it saves what it has read as it goes (every
  `SAVE_EVERY_SECONDS`), a root is `complete` only once a walk got to its end,
  and a walk stopped half-way starts again from what it saved.
- **Sizes** are measured apart from the walk, on the same worker, a few
  folders at a time: the ones the table shows first (`request_sizes`), then
  the rest in the background.
- **Authors** come from Review's Steam cache for the workshop ids
  (`wallpaper_meta.author_names`); the walk never asks Steam. None known: "".
- The file is a cache. One that does not read is rebuilt, and said so.

Nothing here runs on the GUI thread: `LibraryIndexWorker` loads the file,
walks, measures, and hands the page copies through signals.
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from PySide6.QtCore import QThread, Signal

from ..settings import app_data_dir
from .rotator.config import _write_atomically
from .rotator.core import folder_size, folder_is_set

FILE_NAME = "library_meta.json"
FORMAT = 1
SAVE_EVERY_SECONDS = 15.0
# How many folders the worker measures between looks at what the page asked for.
SIZE_CHUNK = 8

Progress = Callable[[str, int, int], None]
Cancelled = Callable[[], bool]


def index_path() -> Path:
    return app_data_dir() / FILE_NAME


def root_key(root: str | Path) -> str:
    return os.path.normpath(str(root))


# ---- one folder ----------------------------------------------------------------------------

@dataclass(frozen=True)
class FolderInfo:
    name: str
    mtime: int = 0
    title: str = ""
    kind: str = ""                  # "scene", "video", "web", … as project.json says, lower case
    workshop_id: str = ""
    preview: str = ""               # the preview's file name, inside the folder
    size: int | None = None         # bytes; None until measured
    unidentified: bool = False      # no readable project.json

    def to_json(self) -> dict:
        data: dict = {"m": self.mtime}
        for key, value in (("t", self.title), ("k", self.kind), ("w", self.workshop_id),
                           ("p", self.preview)):
            if value:
                data[key] = value
        if self.size is not None:
            data["s"] = self.size
        if self.unidentified:
            data["u"] = 1
        return data

    @classmethod
    def from_json(cls, name: str, data) -> "FolderInfo | None":
        """An entry as stored, or None when it is not one (read the folder again)."""
        if not isinstance(data, dict) or type(data.get("m")) is not int:
            return None
        text = {k: data.get(k) for k in ("t", "k", "w", "p")}
        if any(v is not None and not isinstance(v, str) for v in text.values()):
            return None
        size = data.get("s")
        if size is not None and (type(size) is not int or size < 0):
            size = None
        return cls(name=name, mtime=data["m"], title=text["t"] or "", kind=text["k"] or "",
                   workshop_id=text["w"] or "", preview=text["p"] or "", size=size,
                   unidentified=bool(data.get("u")))


def describe(folder: str | Path, name: str, mtime: int) -> FolderInfo:
    """What a folder's project.json says about it. Reads the disk."""
    try:
        with open(os.path.join(folder, "project.json"), encoding="utf-8-sig",
                  errors="replace") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError("not an object")
    except (OSError, ValueError, RecursionError):
        return FolderInfo(name=name, mtime=mtime, unidentified=True)

    def text(key: str) -> str:
        value = data.get(key)
        return value.strip() if isinstance(value, str) else ""

    workshop = next((str(data[k]).strip() for k in ("workshopid", "workshopId")
                     if data.get(k) is not None and not isinstance(data.get(k), (dict, list))),
                    "")
    preview = Path(text("preview").replace("\\", "/")).name if text("preview") else ""
    return FolderInfo(name=name, mtime=mtime, title=text("title"), kind=text("type").lower(),
                      workshop_id=workshop if workshop.isdigit() else "", preview=preview)


def _listing(root: str) -> list[tuple[str, int]]:
    """(name, mtime) of each folder in root. On Windows the times come with the
    listing: one directory read, no stat per folder."""
    found = []
    with os.scandir(root) as entries:
        for entry in entries:
            try:
                if entry.is_dir():
                    found.append((entry.name, int(entry.stat().st_mtime)))
            except OSError:
                continue
    return found


@dataclass
class RefreshReport:
    folders: int = 0
    read: int = 0                   # project.json opened
    carried: int = 0                # moved between roots, taken over
    reused: int = 0
    removed: int = 0
    seconds: float = 0.0
    complete: bool = True
    missing: list[str] = field(default_factory=list)   # roots not found


# ---- the index -----------------------------------------------------------------------------

class LibraryIndex:
    """The file in memory. Thread-safe: the worker writes, anything may read
    copies (`folders`, `get`)."""

    def __init__(self, path: str | Path | None = None, *, clock: Callable[[], float] = time.monotonic):
        self.path = Path(path) if path is not None else index_path()
        self._clock = clock
        self._lock = threading.Lock()
        self._roots: dict[str, dict[str, FolderInfo]] = {}
        self._complete: dict[str, bool] = {}
        self._dirty = False
        self.notice = ""
        self.loaded = False

    # -- the file

    def load(self) -> None:
        self.loaded = True
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return
        except OSError as err:
            self.notice = f"{self.path.name} could not be read ({err}); it is built again."
            return
        try:
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("roots"), dict):
                raise ValueError("there are no roots in it")
        except ValueError as err:
            self.notice = f"{self.path.name} could not be read ({err}); it is built again."
            return
        roots: dict[str, dict[str, FolderInfo]] = {}
        complete: dict[str, bool] = {}
        for key, entry in data["roots"].items():
            if not isinstance(entry, dict) or not isinstance(entry.get("folders"), dict):
                continue
            folders = {}
            for name, item in entry["folders"].items():
                info = FolderInfo.from_json(str(name), item)
                if info is not None:
                    folders[info.name] = info
            roots[root_key(key)] = folders
            complete[root_key(key)] = entry.get("complete") is True
        with self._lock:
            self._roots, self._complete = roots, complete

    def save(self) -> None:
        with self._lock:
            payload = {"format": FORMAT,
                       "saved": datetime.now().replace(microsecond=0).isoformat(),
                       "roots": {key: {"complete": self._complete.get(key, False),
                                       "folders": {n: i.to_json() for n, i in folders.items()}}
                                 for key, folders in self._roots.items()}}
            self._dirty = False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _write_atomically(self.path, json.dumps(payload, ensure_ascii=False,
                                                separators=(",", ":")))

    def save_if_changed(self) -> None:
        if self._dirty:
            self.save()

    # -- reading

    def roots(self) -> list[str]:
        with self._lock:
            return list(self._roots)

    def folders(self, root: str | Path) -> dict[str, FolderInfo]:
        """A copy of what is known about the root's folders, by name."""
        with self._lock:
            return dict(self._roots.get(root_key(root), {}))

    def get(self, root: str | Path, name: str) -> FolderInfo | None:
        with self._lock:
            return self._roots.get(root_key(root), {}).get(name)

    def complete(self, root: str | Path) -> bool:
        with self._lock:
            return self._complete.get(root_key(root), False)

    def unsized(self, root: str | Path) -> list[str]:
        with self._lock:
            return sorted((n for n, i in self._roots.get(root_key(root), {}).items()
                           if i.size is None), key=str.lower)

    def total_size(self, root: str | Path) -> tuple[int, int]:
        """(bytes measured, folders not measured yet)."""
        with self._lock:
            folders = self._roots.get(root_key(root), {}).values()
            return (sum(i.size for i in folders if i.size is not None),
                    sum(1 for i in folders if i.size is None))

    def workshop_ids(self, root: str | Path | None = None) -> set[str]:
        with self._lock:
            maps = ([self._roots.get(root_key(root), {})] if root is not None
                    else list(self._roots.values()))
            return {i.workshop_id for m in maps for i in m.values() if i.workshop_id}

    def authors(self, root: str | Path | None = None,
                cache_path: Path | None = None) -> dict[str, str]:
        """Workshop id → author name, as far as Review's Steam cache knows. Reads
        a local database, never Steam: off the GUI thread all the same."""
        from .wallpaper_meta import author_names
        return {wid: a.name for wid, a in author_names(self.workshop_ids(root),
                                                       cache_path).items()}

    # -- the walk

    def refresh(self, roots: Iterable[str | Path], *, progress: Progress | None = None,
                cancelled: Cancelled | None = None,
                published: Callable[[str, dict], None] | None = None) -> RefreshReport:
        """Walk each root and read the folders that are new or changed.

        Saves as it goes and at the end; a root is `complete` once its walk
        got to the end. `published(root, folders)` hands out a copy of what is
        known each time the walk saves. A root that is not set or not there is
        skipped (and reported), and what is known of it kept.
        """
        started = self._clock()
        report = RefreshReport()
        cancelled = cancelled or (lambda: False)
        wanted = [str(r) for r in roots if folder_is_set(r)]
        carry = self._carry_map()
        for root in wanted:
            key = root_key(root)
            if not os.path.isdir(root):
                report.missing.append(root)
                continue
            try:
                listing = _listing(root)
            except OSError:
                report.missing.append(root)
                continue
            if not self._walk(root, key, listing, carry, report, progress, cancelled,
                              published):
                report.complete = False
                break
        report.folders = sum(len(self.folders(r)) for r in wanted)
        report.seconds = round(self._clock() - started, 1)
        return report

    def _carry_map(self) -> dict[tuple[str, int], FolderInfo]:
        with self._lock:
            return {(i.name, i.mtime): i for m in self._roots.values() for i in m.values()}

    def _walk(self, root, key, listing, carry, report, progress, cancelled, published) -> bool:
        with self._lock:
            previous = self._roots.get(key, {})
        current = dict(previous)
        seen: set[str] = set()
        total = len(listing)
        last_save = self._clock()
        changed = False
        for done, (name, mtime) in enumerate(listing, 1):
            if cancelled():
                self._publish(key, current, complete=False, published=published, root=root)
                return False
            seen.add(name)
            known = previous.get(name)
            if known is not None and known.mtime == mtime:
                report.reused += 1
            elif (name, mtime) in carry:
                current[name] = carry[(name, mtime)]
                report.carried += 1
                changed = True
            else:
                current[name] = describe(os.path.join(root, name), name, mtime)
                report.read += 1
                changed = True
            if progress and (done % 250 == 0 or done == total):
                progress(root, done, total)
            if changed and self._clock() - last_save >= SAVE_EVERY_SECONDS:
                self._publish(key, current, complete=False, published=published, root=root)
                last_save = self._clock()
                changed = False
        gone = [n for n in current if n not in seen]
        for name in gone:
            del current[name]
        report.removed += len(gone)
        self._publish(key, current, complete=True, published=published, root=root)
        if progress and total == 0:
            progress(root, 0, 0)
        return True

    def _publish(self, key, current, *, complete, published, root) -> None:
        with self._lock:
            self._roots[key] = dict(current)
            # Part-way through a walk some entries may be stale: complete only
            # once a walk has got to the end.
            self._complete[key] = complete
            self._dirty = True
        try:
            self.save()
        except OSError as err:
            self.notice = f"{self.path.name} could not be saved: {err}"
        if published is not None:
            published(root, self.folders(key))

    # -- sizes

    def measure(self, root: str | Path, names: Iterable[str], *,
                cancelled: Cancelled | None = None) -> dict[str, int]:
        """Measure these folders of the root that have no size yet; {name: bytes}.
        Reads every file's size: off the GUI thread."""
        key = root_key(root)
        found: dict[str, int] = {}
        for name in names:
            if cancelled is not None and cancelled():
                break
            info = self.get(key, name)
            if info is None or info.size is not None:
                continue
            size = folder_size(os.path.join(str(root), name))
            with self._lock:
                folders = self._roots.get(key)
                now = folders.get(name) if folders is not None else None
                # Read again or gone meanwhile: that entry is not this size.
                if now is not None and now.mtime == info.mtime:
                    folders[name] = replace(now, size=size)
                    self._dirty = True
            found[name] = size
        return found


# ---- on a worker ---------------------------------------------------------------------------

class LibraryIndexWorker(QThread):
    """Loads the index, walks the roots, then measures folders for as long as
    it runs: first the ones asked for (`request_sizes`, the table's visible
    rows), then, with `fill_sizes`, all the rest.

    - `loaded(root, folders)`: what the file already knew, at once;
    - `progress(root, done, total)` through each walk;
    - `refreshed(root, folders)`: after a walk saves, and when it ends — a
      copy, `{name: FolderInfo}`;
    - `walked(RefreshReport)`, then `authors({workshop id: name})`;
    - `sized(root, {name: bytes})`, a few at a time.

    `stop()` ends it; what was read or measured is saved.
    """
    loaded = Signal(str, object)
    progress = Signal(str, int, int)
    refreshed = Signal(str, object)
    walked = Signal(object)
    authors = Signal(object)
    sized = Signal(str, object)

    def __init__(self, roots: Iterable[str], index: LibraryIndex | None = None, *,
                 fill_sizes: bool = True, cache_path: Path | None = None):
        super().__init__()
        self.index = index if index is not None else LibraryIndex()
        self.roots = [str(r) for r in roots if folder_is_set(r)]
        self.fill_sizes = fill_sizes
        self.cache_path = cache_path
        self._wake = threading.Condition()
        self._asked: deque[tuple[str, str]] = deque()
        # The rest, measured in the background: listed once, not per batch.
        self._fill: deque[tuple[str, str]] = deque()
        self._stopping = False

    def request_sizes(self, root: str, names: Iterable[str]) -> None:
        """Measure these next (any thread). The latest request goes first."""
        with self._wake:
            fresh = [(str(root), n) for n in names]
            wanted = set(fresh)
            kept = [item for item in self._asked if item not in wanted]
            self._asked = deque(fresh + kept)
            self._wake.notify_all()

    def stop(self) -> None:
        with self._wake:
            self._stopping = True
            self._wake.notify_all()

    def _stopped(self) -> bool:
        return self._stopping

    def run(self) -> None:
        self.setPriority(QThread.Priority.LowPriority)
        index = self.index
        if not index.loaded:
            index.load()
        for root in self.roots:
            self.loaded.emit(root, index.folders(root))
        report = index.refresh(self.roots, progress=self.progress.emit,
                               cancelled=self._stopped, published=self.refreshed.emit)
        self.walked.emit(report)
        if self._stopping:
            return
        try:
            self.authors.emit(index.authors(cache_path=self.cache_path))
        except Exception:  # noqa: BLE001 — no names is what the table shows anyway
            self.authors.emit({})
        self._measure_loop()
        try:
            index.save_if_changed()
        except OSError as err:
            index.notice = f"{index.path.name} could not be saved: {err}"

    def _next_batch(self) -> tuple[str, list[str]] | None:
        with self._wake:
            while not self._stopping:
                if self._asked:
                    root, name = self._asked.popleft()
                    batch = [name]
                    while self._asked and len(batch) < SIZE_CHUNK and self._asked[0][0] == root:
                        batch.append(self._asked.popleft()[1])
                    return root, batch
                if self.fill_sizes and not self._fill:
                    self._fill.extend((root, n) for root in self.roots
                                      for n in self.index.unsized(root))
                if self.fill_sizes and self._fill:
                    root, name = self._fill.popleft()
                    batch = [name]
                    while self._fill and len(batch) < SIZE_CHUNK and self._fill[0][0] == root:
                        batch.append(self._fill.popleft()[1])
                    return root, batch
                self._wake.wait(timeout=30.0)
            return None

    def _measure_loop(self) -> None:
        last_save = time.monotonic()
        while True:
            batch = self._next_batch()
            if batch is None:
                return
            root, names = batch
            found = self.index.measure(root, names, cancelled=self._stopped)
            if found:
                self.sized.emit(root, found)
            if time.monotonic() - last_save >= SAVE_EVERY_SECONDS:
                try:
                    self.index.save_if_changed()
                except OSError:
                    pass
                last_save = time.monotonic()
