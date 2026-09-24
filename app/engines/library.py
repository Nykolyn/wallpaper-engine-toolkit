"""library.py — what is on this machine already, and what once was.

A review is mostly a question about two sets. Both live on disk and neither is
expensive to be wrong about, so both are worth getting exactly right:

* **Subscribed now** — the folders under `steamapps/workshop/content/431960`,
  named by workshop id. 1 288 of them here. This is what decides whether a
  wallpaper appears in the gallery at all.
* **Ever had** — wallpapers that came from the workshop, were kept, and are no
  longer subscribed. There are 4 266 of those, and without marking them a list
  of two thousand cards is half things already rejected once.

The second set has to be dug out. A copied wallpaper keeps its `project.json`,
and a workshop one names itself there: `"workshopid": "3796616409"`. Walking
the rotator's three libraries finds 4 656 such folders — out of 33 882, because
the rest were built locally by the Creator tabs and never had a workshop id to
lose.

**Why this is cached.** That walk takes **254 seconds**: it is 33 882 folders
and it opens a JSON file in each. Doing it when a tab is opened is not an
option. So the answer is kept in `data/library.json` keyed by folder name and
modification time, and a refresh only re-reads folders that are new or have
changed — seconds instead of minutes, with the first run paid once.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from ..settings import app_data_dir
from .rotator.config import Config as RotatorConfig
from .rotator.core import folder_is_set
from . import steam_paths

INDEX_PATH = app_data_dir() / "library.json"

# Steam's own answer, asked for rather than assumed. "" when Steam is not
# installed, which the search below simply skips over.
DEFAULT_WORKSHOP = steam_paths.as_text(steam_paths.workshop_dir())

# The manifest names its workshop id under one of these; older Wallpaper Engine
# versions wrote the second spelling.
_ID_KEYS = ("workshopid", "workshopId")


def find_workshop_content(hint: str | Path | None = None) -> Path | None:
    """Where Steam puts subscribed Wallpaper Engine items."""
    candidates = [Path(hint)] if hint else []
    if DEFAULT_WORKSHOP:
        candidates.append(Path(DEFAULT_WORKSHOP))
    try:
        from .tracker import find_we_config
        config = find_we_config()
        if config:
            # …/steamapps/common/wallpaper_engine/config.json
            steamapps = Path(config).parents[2]
            candidates.append(steamapps / "workshop" / "content" / "431960")
    except Exception:  # noqa: BLE001 — a missing tracker must not break this
        pass
    for path in candidates:
        if path.is_dir():
            return path
    return None


@dataclass
class Scan:
    """One library root, as last read."""

    root: str
    folders: dict[str, list] = field(default_factory=dict)   # name -> [mtime, id|None]
    read: int = 0
    skipped: int = 0

    def ids(self) -> set[str]:
        return {entry[1] for entry in self.folders.values() if entry[1]}


class Library:
    """The two sets a review asks about, with the slow one kept on disk."""

    def __init__(self, workshop: str | Path | None = None,
                 roots: Iterable[str | Path] | None = None,
                 index_path: Path = INDEX_PATH,
                 on_log: Callable[[str], None] | None = None):
        self.workshop = find_workshop_content(workshop)
        self.roots = [Path(r) for r in roots] if roots is not None else _rotator_roots()
        self.index_path = Path(index_path)
        self._log = on_log or (lambda _message: None)
        self._scans: dict[str, Scan] = {}
        # Steam accepts a subscription long before the folder appears, and the
        # gallery must not offer the wallpaper again in the meantime.
        self._just_subscribed: set[str] = set()
        self._load()

    # -- subscribed now ----------------------------------------------------

    def subscribed(self) -> set[str]:
        """Workshop ids Steam has downloaded here. A directory listing, no more."""
        if not self.workshop:
            return set()
        try:
            found = {e.name for e in os.scandir(self.workshop)
                     if e.is_dir() and e.name.isdigit()}
        except OSError as err:
            self._log(f"cannot read {self.workshop}: {err}")
            found = set()
        return found | self._just_subscribed

    def listable(self) -> set[str]:
        """Subscribed wallpapers Wallpaper Engine can actually show.

        Three things narrow a folder's contents down to what appears on screen,
        and getting any of them wrong inflates a week's work into a year's:

        * Unsubscribing does not remove an id from a folder — it stays in
          `config.json` for ever. A folder used as a queue for years remembers
          thousands of wallpapers with nothing behind them: 2 155 ids here,
          28 with a folder still on disk.
        * A folder on disk is not a wallpaper. Steam leaves behind directories
          holding nothing but the compiled shader cache, and Wallpaper Engine
          identifies a wallpaper by its `project.json` — no manifest, never
          listed. Four of those 28.
        * A copy in `myprojects` is not a subscription. Five more.

        What is left is 19, which is what the browser shows.
        """
        found: set[str] = set()
        if not self.workshop:
            return found
        try:
            entries = list(os.scandir(self.workshop))
        except OSError as err:
            self._log(f"cannot read {self.workshop}: {err}")
            return found
        for entry in entries:
            if not entry.is_dir() or not entry.name.isdigit():
                continue
            if os.path.isfile(os.path.join(entry.path, "project.json")):
                found.add(entry.name)
        return found | self._just_subscribed

    def added_at(self, item_ids: Iterable[str]) -> dict[str, float]:
        """When each subscribed wallpaper arrived, as a timestamp.

        Wallpaper Engine records nothing about *when* something was put in a
        folder — the value in `config.json` is always 1. The closest honest
        clock is the one Steam starts when it creates the wallpaper's folder,
        and it agrees: of 119 queued wallpapers, 118 were downloaded on exactly
        the day the folder first held them (read off Wallpaper Engine's own
        daily config backups), and to the minute rather than the day. The one
        exception was re-subscribed after it was already queued.
        """
        out: dict[str, float] = {}
        if not self.workshop:
            return out
        for item_id in item_ids:
            try:
                out[str(item_id)] = os.stat(os.path.join(self.workshop, str(item_id))).st_ctime
            except OSError:
                continue
        return out

    def note_subscribed(self, item_id: str) -> None:
        """Remember a subscription Steam has taken but not yet downloaded."""
        self._just_subscribed.add(str(item_id))

    # -- ever had ----------------------------------------------------------

    def ever_had(self) -> set[str]:
        """Every workshop id that has passed through the local libraries."""
        found: set[str] = set()
        for scan in self._scans.values():
            found |= scan.ids()
        return found

    def once_had_but_gone(self) -> set[str]:
        """The ones worth marking: kept at some point, not subscribed now."""
        return self.ever_had() - self.subscribed()

    def refresh(self, force: bool = False,
                on_progress: Callable[[str, int, int], None] | None = None) -> dict:
        """Re-read the libraries, opening only what has changed since last time."""
        started = time.monotonic()
        opened = reused = 0
        for root in self.roots:
            if not root.is_dir():
                self._log(f"library root missing: {root}")
                continue
            scan = self._scans.get(str(root)) or Scan(root=str(root))
            previous = {} if force else scan.folders
            folders: dict[str, list] = {}
            entries = [e for e in os.scandir(root) if e.is_dir()]
            for done, entry in enumerate(entries, 1):
                try:
                    stamp = int(entry.stat().st_mtime)
                except OSError:
                    continue
                known = previous.get(entry.name)
                if known and known[0] == stamp:
                    folders[entry.name] = known
                    reused += 1
                else:
                    folders[entry.name] = [stamp, _workshop_id(entry.path)]
                    opened += 1
                if on_progress and done % 500 == 0:
                    on_progress(str(root), done, len(entries))
            scan.folders = folders
            scan.read = len(folders)
            self._scans[str(root)] = scan
            if on_progress:
                on_progress(str(root), len(entries), len(entries))

        self._save()
        report = {"folders": sum(s.read for s in self._scans.values()),
                  "opened": opened, "reused": reused,
                  "ids": len(self.ever_had()),
                  "seconds": round(time.monotonic() - started, 1)}
        self._log("library scan: {folders} folders, {opened} read, {reused} unchanged,"
                  " {ids} workshop ids, {seconds}s".format(**report))
        return report

    @property
    def scanned(self) -> bool:
        return bool(self._scans)

    # -- the index on disk -------------------------------------------------

    def _load(self) -> None:
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for root, entry in (data.get("roots") or {}).items():
            self._scans[root] = Scan(root=root, folders=entry.get("folders") or {},
                                     read=entry.get("read", 0))

    def _save(self) -> None:
        payload = {
            "saved": time.strftime("%Y-%m-%d %H:%M:%S"),
            "roots": {root: {"folders": scan.folders, "read": scan.read}
                      for root, scan in self._scans.items()},
        }
        try:
            self.index_path.write_text(json.dumps(payload), encoding="utf-8")
        except OSError as err:
            self._log(f"could not save the library index: {err}")


def _rotator_roots() -> list[Path]:
    """The three folders the Rotator already knows about — those that are set.

    An empty or relative one would walk the working directory instead.
    """
    config = RotatorConfig.load()
    return [Path(p) for p in (config.source, config.destination, config.duplicates)
            if folder_is_set(p)]


def _workshop_id(folder: str) -> str | None:
    """The workshop id a folder's manifest claims, if it has one.

    Most folders do not: a wallpaper built by the Creator tabs was never on the
    workshop. Those are the common case, so the miss has to be cheap — the file
    is read once and parsed, and anything unreadable is simply not an id.
    """
    try:
        with open(os.path.join(folder, "project.json"), encoding="utf-8",
                  errors="replace") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    for key in _ID_KEYS:
        value = data.get(key)
        if value and str(value).isdigit():
            return str(value)
    return None
