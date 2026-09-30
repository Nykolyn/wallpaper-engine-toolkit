"""wallpaper_meta.py — what a wallpaper is called, what it is, and who made it.

The Tracker's table names every wallpaper of a playlist — up to 1 400 of them —
by its title, its type and its author, and none of the three is in the
playlist itself:

- **Title and type** are in the wallpaper's own `project.json` ("title",
  "type": scene, video, web, application), beside its workshop id when it
  came from the workshop.
- **The author** is not in any file Wallpaper Engine keeps. Review asks Steam
  for it and keeps the answers in `data/steam_cache.sqlite`: an item's creator
  (a steamID64) and that account's name and vanity name. This reads those
  answers, whatever their age, and never asks Steam: a wallpaper Review has
  not looked up has no author here, and the table says "—".
- **Known** means the authors database (`data/authors.sqlite`) holds the
  author under either key. It is opened read-only; a missing database knows
  nobody.

Everything here reads files, the project.json ones on the disk the wallpapers
are on (a hard disk here), so it runs on a worker thread and never on the
window's. `MetaCache` keeps what was read for the rest of the session: a
playlist changes wholesale only at a rotation.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ..settings import app_data_dir

# Review's Steam cache: see steam_api (ITEM_CACHE, the "profile" kind).
STEAM_CACHE = "steam_cache.sqlite"
ITEM_KIND = "item.2"
PROFILE_KIND = "profile"
AUTHORS_DB = "authors.sqlite"

# SQLite's limit on bound parameters is 999 on old builds; stay well inside it.
_CHUNK = 500
_ID_KEYS = ("workshopid", "workshopId")


# ---- one wallpaper -------------------------------------------------------------------------

@dataclass(frozen=True)
class WallpaperMeta:
    """A wallpaper as its project.json describes it. `readable` is False when
    there was no project.json to read, or it was not JSON."""
    title: str = ""
    kind: str = ""                  # "scene", "video", "web", … as the file says, lower case
    workshop_id: str = ""
    readable: bool = True


def folder_of(item: str) -> Path:
    """The wallpaper folder a playlist entry is in (`…/myprojects/123/scene.pkg`)."""
    return Path(str(item).replace("/", "\\")).parent


def fallback_title(item: str) -> str:
    """What a wallpaper is called when its project.json says nothing: the
    folder for a scene (every scene's file is scene.json or scene.pkg), else
    the file's own name."""
    path = Path(str(item).replace("/", "\\"))
    return path.parent.name if path.stem.lower() == "scene" else path.stem


def read_meta(item: str) -> WallpaperMeta:
    """Title, type and workshop id from the project.json beside a playlist
    entry. Reads the disk: call it off the GUI thread."""
    try:
        data = json.loads((folder_of(item) / "project.json").read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("not an object")
    except (OSError, ValueError, TypeError):
        return WallpaperMeta(title=fallback_title(item), readable=False)
    title = str(data.get("title") or "").strip() or fallback_title(item)
    kind = str(data.get("type") or "").strip().lower()
    workshop = next((str(data[k]).strip() for k in _ID_KEYS if data.get(k)), "")
    return WallpaperMeta(title=title, kind=kind,
                         workshop_id=workshop if workshop.isdigit() else "")


# ---- its author ----------------------------------------------------------------------------

@dataclass(frozen=True)
class AuthorName:
    """An author as the Steam cache knows them: the name, and the keys the
    authors database files people under (the steamID64 and the vanity name)."""
    name: str
    keys: tuple[str, ...] = ()


def _connect(path: Path, mode: str) -> sqlite3.Connection | None:
    """A connection that never creates the file: `ro` for the authors
    database, which must not be written here; `rw` for the Steam cache, which
    is in WAL mode, where a read-only opening can fail for want of its -shm
    file. Only SELECTs are run on either. None when the file is not there."""
    if not path.is_file():
        return None
    try:
        return sqlite3.connect(f"{path.resolve().as_uri()}?mode={mode}", uri=True, timeout=5,
                               check_same_thread=False)
    except sqlite3.Error:
        return None


def _chunks(items: list, size: int = _CHUNK):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _payloads(conn: sqlite3.Connection, kind: str, keys: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for chunk in _chunks(keys):
        marks = ",".join("?" * len(chunk))
        rows = conn.execute(f"SELECT key, payload FROM cache WHERE kind=? AND key IN ({marks})",
                            [kind, *chunk])
        for key, payload in rows:
            try:
                value = json.loads(payload)
            except (TypeError, ValueError):
                continue
            if isinstance(value, dict):
                out[str(key)] = value
    return out


def author_names(workshop_ids: Iterable[str],
                 cache_path: Path | None = None) -> dict[str, AuthorName]:
    """The author of each workshop id the Steam cache can name, by id. An id
    it has no creator or no name for is left out."""
    ids = list(dict.fromkeys(str(i) for i in workshop_ids if str(i).isdigit()))
    if not ids:
        return {}
    conn = _connect(Path(cache_path) if cache_path else app_data_dir() / STEAM_CACHE, "rw")
    if conn is None:
        return {}
    try:
        items = _payloads(conn, ITEM_KIND, ids)
        creators = {wid: str(p["creator"]) for wid, p in items.items() if p.get("creator")}
        profiles = _payloads(conn, PROFILE_KIND,
                             sorted({c.lower() for c in creators.values()}))
    except sqlite3.Error:
        return {}
    finally:
        conn.close()
    out: dict[str, AuthorName] = {}
    for wid, creator in creators.items():
        profile = profiles.get(creator.lower())
        if not profile or not profile.get("name"):
            continue
        keys = tuple(k for k in (profile.get("id64") or creator, profile.get("vanity")) if k)
        out[wid] = AuthorName(str(profile["name"]), tuple(str(k) for k in keys))
    return out


def known_authors(keys: Iterable[str], db_path: Path | None = None) -> set[str]:
    """Of these keys, the ones the authors database has a record under,
    lower case. Read-only; nothing when there is no database to read."""
    wanted = list(dict.fromkeys(str(k).strip() for k in keys if str(k).strip()))
    if not wanted:
        return set()
    conn = _connect(Path(db_path) if db_path else app_data_dir() / AUTHORS_DB, "ro")
    if conn is None:
        return set()
    found: set[str] = set()
    try:
        for chunk in _chunks(wanted):
            marks = ",".join("?" * len(chunk))
            for (key,) in conn.execute(f"SELECT key FROM authors WHERE key IN ({marks})", chunk):
                found.add(str(key).lower())
    except sqlite3.Error:
        return set()
    finally:
        conn.close()
    return found


# ---- what a session has read ---------------------------------------------------------------

@dataclass(frozen=True)
class Described:
    """Everything the table says about one wallpaper."""
    meta: WallpaperMeta
    author: str = ""
    known: bool = False


class MetaCache:
    """What has been read this session, shared by the threads that read it.

    `describe(items)` reads what is not cached yet — project.json, then the
    authors of the new workshop ids in one pass over each database — and
    returns every item's `Described`. `forget()` drops it all, for a "read it
    again". Safe to call from any thread; the reading is done without the lock.
    """

    def __init__(self, *, cache_path: Path | None = None, db_path: Path | None = None):
        self._lock = threading.Lock()
        self._meta: dict[str, WallpaperMeta] = {}
        self._authors: dict[str, AuthorName | None] = {}      # workshop id → author, None = unknown
        self._known: dict[str, bool] = {}                     # key (lower) → in the database
        self._cache_path = cache_path
        self._db_path = db_path

    def forget(self) -> None:
        with self._lock:
            self._meta.clear()
            self._authors.clear()
            self._known.clear()

    def cached(self, item: str) -> Described | None:
        """What is known of an item without reading anything, or None."""
        with self._lock:
            meta = self._meta.get(item)
            if meta is None:
                return None
            return self._describe(meta)

    def _describe(self, meta: WallpaperMeta) -> Described:
        author = self._authors.get(meta.workshop_id) if meta.workshop_id else None
        if author is None:
            return Described(meta)
        known = any(self._known.get(k.lower(), False) for k in author.keys)
        return Described(meta, author.name, known)

    def read(self, items: Iterable[str]) -> None:
        """Read project.json for the items not read yet (the disk)."""
        with self._lock:
            todo = [i for i in dict.fromkeys(items) if i not in self._meta]
        for item in todo:
            meta = read_meta(item)
            with self._lock:
                self._meta[item] = meta

    def resolve_authors(self, items: Iterable[str]) -> None:
        """Name the authors of these items' workshop ids, and look them up in
        the authors database, where that has not been done yet."""
        with self._lock:
            ids = {m.workshop_id for i in items
                   if (m := self._meta.get(i)) is not None and m.workshop_id}
            ids = sorted(i for i in ids if i not in self._authors)
        if not ids:
            return
        names = author_names(ids, self._cache_path)
        keys = sorted({k for name in names.values() for k in name.keys})
        with self._lock:
            keys = [k for k in keys if k.lower() not in self._known]
        found = known_authors(keys, self._db_path) if keys else set()
        with self._lock:
            for wid in ids:
                self._authors[wid] = names.get(wid)
            for key in keys:
                self._known[key.lower()] = key.lower() in found

    def describe(self, items: Iterable[str]) -> dict[str, Described]:
        items = list(items)
        self.read(items)
        self.resolve_authors(items)
        with self._lock:
            return {i: self._describe(self._meta[i]) for i in items if i in self._meta}
