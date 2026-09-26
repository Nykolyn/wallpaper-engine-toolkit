"""authors_store.py — the authors database, as one file in the data folder.

Review's whole premise is remembering, per author, when you last looked at
their work. That lives here — `data/authors.sqlite`, created the first time it
is needed, with nothing to install, host or sign in to.

**Why SQLite rather than a JSON file.** Both are one file and neither needs
setting up. SQLite is the one that already knows how to survive a crash in the
middle of a write (its journal), how to write one changed row without
rewriting forty thousand, and how to find `O0P` when asked for `o0p` through an
index. JSON would need all three written by hand. The standard library ships
it, and the Steam cache beside it already uses it.

**The schema is what the Review actually uses and nothing else**::

    key      steamID64 — or, for the few authors nothing could identify, the
             vanity name they were filed under. Compared without case: a vanity
             name is case-blind on Steam, and `COLLATE NOCASE` folds ASCII,
             which is all a vanity name may contain.
    name     what the author was called when last seen
    added    when they were first found          (UTC, "2026-09-21T10:15:30Z")
    visited  how far through their work you are  (UTC; empty = never)

Times are stored as text with the zone written on them. UTC without the marker
is one misreading away from a silent three-hour error in the one date that
decides what is shown.

**Nothing is written without a trail.** A write is planned first
(:class:`Change`, shown to the user as a line of English), carried out in one
transaction — all of it or none of it — and followed by:

* a line in ``authors_backup/journal.jsonl`` holding every touched row as it
  was and as it became, and
* a full **snapshot**: the whole table as gzipped JSON, readable without this
  app, verified by reading it back. Snapshots are pruned to the last ten plus
  one a day for a week, one a week for a month and one a month for a year —
  about a megabyte each.

Snapshots are always written to ``data/authors_backup/``. If a second folder is
configured they are copied there as well — another disk, or a folder a cloud
client syncs — because a backup on the same disk as the thing it backs up
survives mistakes, not disk failures.
"""
from __future__ import annotations

import gzip
import json
import os
import re
import shutil
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Sequence

from ..settings import app_data_dir
from .steam_api import is_steam_id64

DB_NAME = "authors.sqlite"
BACKUP_DIRNAME = "authors_backup"
JOURNAL_NAME = "journal.jsonl"

# Bumped when the table changes shape; a file from a newer toolkit is refused
# rather than half-understood.
SCHEMA_VERSION = 1
SNAPSHOT_FORMAT = "wallpaper-engine-toolkit/authors"

# What survives pruning, newest first within each rule. A file kept by any rule
# is kept. Ten recent ones cover "the last few reviews"; the calendar rules
# cover "it went wrong a while ago and I only noticed now".
KEEP_RECENT = 10
KEEP_DAILY = 7
KEEP_WEEKLY = 4
KEEP_MONTHLY = 12

# authors-20260921-101530-38897.json.gz — when, and how many authors it holds,
# so a folder listing says what each file is without opening it.
_SNAPSHOT_NAME = re.compile(r"^authors-(\d{8})-(\d{6})-(\d+)\.json\.gz$")

_COLUMNS = {"key", "name", "added", "visited"}

# SQLite's limit on bound parameters is 999 on old builds; stay well inside it.
_CHUNK = 500

_SCHEMA = """
CREATE TABLE IF NOT EXISTS authors (
    key     TEXT PRIMARY KEY COLLATE NOCASE CHECK (length(key) > 0),
    name    TEXT NOT NULL DEFAULT '',
    added   TEXT,
    visited TEXT
) WITHOUT ROWID
"""


class DbError(RuntimeError):
    """The store refused what was asked of it, or could not be read."""


class StoreDamaged(DbError):
    """The database file exists but is not a healthy SQLite database.

    Said separately because the answer is different: not "try again" but
    "restore from a backup", and the window offers exactly that.
    """


# ---- One author ------------------------------------------------------------

@dataclass
class Author:
    """One row."""

    steam_id: str                   # the key: a steamID64, or a vanity name
    name: str = ""
    added: datetime | None = None
    visited: datetime | None = None

    @classmethod
    def from_row(cls, row: Sequence) -> "Author":
        key, name, added, visited = row
        return cls(steam_id=key, name=name or "", added=from_text(added),
                   visited=from_text(visited))

    def to_row(self) -> dict:
        return {"key": self.steam_id, "name": self.name,
                "added": to_text(self.added), "visited": to_text(self.visited)}

    @property
    def numeric(self) -> bool:
        """Whether this record is filed under a steamID64 rather than a name.

        Not "does it look like a number": a vanity URL can be all digits.
        """
        return is_steam_id64(self.steam_id)

    def __str__(self) -> str:
        return f"{self.name} ({self.steam_id})"


@dataclass
class Change:
    """A write that has not happened yet.

    ``fields`` is what would be set, by column name; ``before`` is what is
    there now, so the same object serves the confirmation dialog and the
    journal.
    """

    kind: str                        # "create" | "update" | "delete"
    fields: dict = field(default_factory=dict)
    author: Author | None = None
    before: dict | None = None
    reason: str = ""

    @property
    def target(self) -> str:
        if self.author:
            return str(self.author)
        return f"{self.fields.get('name', '?')} ({self.fields.get('key', '?')})"

    def describe(self) -> str:
        """One line saying what this does, in the words a person would use."""
        if self.kind == "create":
            return f"create {self.target}"
        if self.kind == "delete":
            return f"delete {self.target}" + (f" — {self.reason}" if self.reason else "")
        parts = []
        for key, value in self.fields.items():
            was = (self.before or {}).get(key)
            parts.append(f"{key}: {_short(was)} → {_short(value)}")
        return f"update {self.target}: " + ", ".join(parts)


@dataclass
class Snapshot:
    """One backup file, described from its name."""

    path: Path
    taken: datetime                  # local time, as in the name
    count: int
    where: str                       # "data" or "second folder"

    @property
    def size(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0


# ---- The store -------------------------------------------------------------

class AuthorsStore:
    """Reads and (deliberately reluctantly) writes the authors table."""

    def __init__(self, path: Path | str | None = None,
                 backup_dir: Path | str | None = None,
                 mirror: Path | str | None = None):
        self.path = Path(path) if path else app_data_dir() / DB_NAME
        self.backup_dir = Path(backup_dir) if backup_dir else \
            self.path.parent / BACKUP_DIRNAME
        self.mirror = Path(mirror) if mirror else None
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.RLock()
        # Things that went wrong without stopping the write — the second folder
        # being unplugged, say. The window reads and clears these.
        self.warnings: list[str] = []

    # -- opening -----------------------------------------------------------

    def open(self) -> "AuthorsStore":
        """Open the file, creating it on first use, and check it is sound."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existed = self.path.exists() and self.path.stat().st_size > 0
        try:
            conn = sqlite3.connect(str(self.path), timeout=15,
                                   check_same_thread=False, isolation_level=None)
        except sqlite3.Error as err:
            raise DbError(f"cannot open {self.path}: {err}") from err
        try:
            if existed:
                verdict = [r[0] for r in conn.execute("PRAGMA quick_check")]
                if verdict != ["ok"]:
                    raise StoreDamaged(
                        f"{self.path.name} is damaged: {'; '.join(verdict[:3])}")
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise DbError(
                    f"{self.path.name} was written by a newer version of the "
                    f"toolkit (schema {version}; this one reads {SCHEMA_VERSION})")
            # FULL: a write that has returned is on the disk, not in a cache.
            # Writes here are a few a week; the cost is nothing.
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute(_SCHEMA)
            if version < SCHEMA_VERSION:
                conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        except sqlite3.DatabaseError as err:
            conn.close()
            # "file is not a database", "database disk image is malformed"
            raise StoreDamaged(f"{self.path.name} is damaged: {err}") from err
        except Exception:
            conn.close()
            raise
        self._conn = conn
        return self

    @property
    def connected(self) -> bool:
        return self._conn is not None

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
            self._conn = None

    def __enter__(self) -> "AuthorsStore":
        return self.open()

    def __exit__(self, *_exc) -> None:
        self.close()

    def _require(self) -> sqlite3.Connection:
        if self._conn is None:
            raise DbError("the authors database is not open")
        return self._conn

    def count(self) -> int:
        with self._lock:
            return self._require().execute("SELECT count(*) FROM authors").fetchone()[0]

    # -- reading -----------------------------------------------------------

    def lookup(self, keys: Iterable[str]) -> list[Author]:
        """Every record filed under any of these keys, case ignored."""
        return self.lookup_many({"one": list(keys)}).get("one", [])

    def lookup_many(self, wanted: dict[str, Sequence[str]]) -> dict[str, list[Author]]:
        """Resolve many authors at once.

        An author answers to two keys — the account number and the vanity name
        — and the few records nothing could identify are filed under the
        latter, so both are asked about and a card can come back with two
        records. That is a duplicate, and saying so is the caller's business.
        """
        keys: list[str] = []
        for group in wanted.values():
            keys.extend(str(k).strip() for k in group if str(k).strip())
        keys = list(dict.fromkeys(keys))

        by_key: dict[str, Author] = {}
        with self._lock:
            conn = self._require()
            for chunk in _chunks(keys, _CHUNK):
                marks = ",".join("?" * len(chunk))
                for row in conn.execute(
                        "SELECT key, name, added, visited FROM authors "
                        f"WHERE key IN ({marks})", chunk):
                    by_key[row[0].lower()] = Author.from_row(row)

        out: dict[str, list[Author]] = {}
        for name, group in wanted.items():
            seen: dict[str, Author] = {}
            for key in group:
                found = by_key.get(str(key).strip().lower())
                if found is not None:
                    # One record can answer to both of an author's keys; only
                    # a second *record* is a duplicate.
                    seen[found.steam_id.lower()] = found
            out[name] = list(seen.values())
        return out

    def by_key(self, key: str) -> Author | None:
        found = self.lookup([key])
        return found[0] if found else None

    def rows(self) -> list[dict]:
        """The whole table, in key order, as a snapshot holds it."""
        with self._lock:
            return [{"key": k, "name": n, "added": a, "visited": v}
                    for k, n, a, v in self._require().execute(
                        "SELECT key, name, added, visited FROM authors ORDER BY key")]

    # -- planning a write --------------------------------------------------

    def plan_create(self, name: str, steam_id: str, visited: datetime | None = None,
                    added: datetime | None = None) -> Change:
        now = datetime.now(timezone.utc)
        return Change(kind="create", fields={
            "key": str(steam_id).strip(),
            "name": name or "",
            "added": added or now,
            "visited": visited or added or now,
        })

    def plan_update(self, author: Author, *, visited: datetime | None = None,
                    name: str | None = None, steam_id: str | None = None,
                    added: datetime | None = None, reason: str = "") -> Change | None:
        """What would change on this record — or None if nothing would.

        Returning None for a no-op matters: a review of four hundred authors
        should write to the handful that actually moved, and a confirmation
        list padded with untouched records is one nobody reads.
        """
        wanted = {"key": str(steam_id).strip() if steam_id is not None else None,
                  "name": name, "added": added, "visited": visited}
        current = {"key": author.steam_id, "name": author.name,
                   "added": author.added, "visited": author.visited}
        fields, before = {}, {}
        for key, value in wanted.items():
            if value is None or _same(current[key], value):
                continue
            fields[key] = value
            before[key] = current[key]
        if not fields:
            return None
        return Change(kind="update", author=author, fields=fields,
                      before=before, reason=reason)

    def plan_delete(self, author: Author, reason: str = "") -> Change:
        return Change(kind="delete", author=author, reason=reason)

    def plan_merge(self, keep: Author, drop: Author) -> list[Change]:
        """Fold one duplicate into the other, losing nothing that was known.

        The earlier `added` is when this author was really found, the later
        `visited` is how much of their work has really been seen. Taking the
        safe end of each means a merge can never make the toolkit re-show
        wallpapers or, worse, skip them. The deletion goes first, so the
        survivor can take the account number the duplicate was filed under.
        """
        changes: list[Change] = [self.plan_delete(drop, reason=f"duplicate of {keep}")]
        update = self.plan_update(
            keep,
            visited=_later(keep.visited, drop.visited),
            steam_id=keep.steam_id if keep.numeric else (
                drop.steam_id if drop.numeric else None),
            added=_earlier(keep.added, drop.added),
            reason=f"merged with duplicate {drop}")
        if update:
            changes.append(update)
        return changes

    # -- writing -----------------------------------------------------------

    def apply(self, changes: Sequence[Change | None],
              on_progress: Callable[[int, int], None] | None = None) -> dict:
        """Carry out planned changes: all of them, or — on any failure — none.

        Every update and delete must reach exactly the row it was planned
        against. A write that matches nothing is not an error to a database —
        it is a successful write of nothing, which is how a batch can report
        thousands of updates and change none. Here it stops the transaction.
        """
        changes = [c for c in changes if c is not None]
        if not changes:
            return {"created": 0, "updated": 0, "deleted": 0,
                    "backup": None, "journal": None}
        counts = {"created": 0, "updated": 0, "deleted": 0}
        entries: list[dict] = []
        with self._lock:
            conn = self._require()
            conn.execute("BEGIN IMMEDIATE")
            try:
                for done, change in enumerate(changes, 1):
                    entries.append(self._write_one(conn, change, counts))
                    if on_progress:
                        on_progress(done, len(changes))
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        journal = self._journal({"changes": entries})
        backup = self._snapshot_after("the change was written")
        return {**counts, "backup": str(backup) if backup else None,
                "journal": str(journal) if journal else None}

    def _snapshot_after(self, what: str) -> Path | None:
        """A snapshot of a write that has already been committed.

        It must not raise. The write happened; reporting it as failed because
        its backup did — a full disk, say — would leave the window believing
        nothing changed, and the next press of Update would plan the same
        creations again. So it becomes a warning the window shows instead.
        """
        try:
            return self.snapshot()
        except (OSError, DbError) as err:
            self.warnings.append(f"{what}, but its backup failed: {err}")
            return None

    def _write_one(self, conn: sqlite3.Connection, change: Change, counts: dict) -> dict:
        if change.kind == "create":
            row = _row_of(change.fields)
            try:
                conn.execute("INSERT INTO authors (key, name, added, visited) "
                             "VALUES (:key, :name, :added, :visited)", row)
            except sqlite3.IntegrityError as err:
                raise DbError(f"could not create {change.target}: an author is "
                              f"already filed under {row['key']}") from err
            counts["created"] += 1
            return {"kind": "create", "key": row["key"], "before": None,
                    "after": row, "reason": change.reason}

        key = change.author.steam_id if change.author else ""
        before = _fetch(conn, key)
        if before is None:
            raise DbError(f"{change.describe()} — no author is filed under {key!r} "
                          "any more; nothing was written")
        if change.kind == "delete":
            conn.execute("DELETE FROM authors WHERE key = ?", (key,))
            counts["deleted"] += 1
            return {"kind": "delete", "key": key, "before": before, "after": None,
                    "reason": change.reason}

        values = {k: _text_of(k, v) for k, v in change.fields.items()}
        unknown = set(values) - _COLUMNS
        if unknown:
            raise DbError(f"{change.describe()} names no such column: {sorted(unknown)}")
        assignments = ", ".join(f"{column} = :{column}" for column in values)
        try:
            cursor = conn.execute(f"UPDATE authors SET {assignments} WHERE key = :_old",
                                  {**values, "_old": key})
        except sqlite3.IntegrityError as err:
            raise DbError(f"{change.describe()} — another author is already "
                          f"filed under {values.get('key')}") from err
        if cursor.rowcount != 1:
            raise DbError(f"{change.describe()} reached {cursor.rowcount} rows, not one")
        counts["updated"] += 1
        return {"kind": "update", "key": key, "before": before,
                "after": _fetch(conn, values.get("key", key)), "reason": change.reason}

    def replace_all(self, rows: Sequence[dict], reason: str) -> dict:
        """Make the table exactly ``rows``, in one transaction.

        What a restore is. The state being replaced is snapshotted first,
        whatever it is, so this is undoable by itself.
        """
        cleaned = [_row_of(r) for r in rows]
        keys = [r["key"].lower() for r in cleaned]
        if len(set(keys)) != len(keys):
            raise DbError("the rows to write file two authors under one key")
        before = self.snapshot(force=True) if self.count() else None
        with self._lock:
            conn = self._require()
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute("DELETE FROM authors")
                conn.executemany("INSERT INTO authors (key, name, added, visited) "
                                 "VALUES (:key, :name, :added, :visited)", cleaned)
                written = conn.execute("SELECT count(*) FROM authors").fetchone()[0]
                if written != len(cleaned):
                    raise DbError(f"wrote {written} authors of {len(cleaned)}")
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        self._journal({"replaced": len(cleaned), "reason": reason,
                       "previous": str(before) if before else None})
        after = self._snapshot_after(f"{len(cleaned)} authors were written")
        return {"written": len(cleaned), "previous": str(before) if before else None,
                "backup": str(after) if after else None}

    def restore(self, path: Path | str) -> dict:
        """Put a snapshot back. The current state is snapshotted first."""
        data = read_snapshot(path)
        return self.replace_all(data["authors"], reason=f"restored {Path(path).name}")

    # -- the trail ---------------------------------------------------------

    def _journal(self, entry: dict) -> Path | None:
        """Append one line to the journal. A failure here is a warning only:
        the write it describes has already happened and been snapshotted."""
        path = self.backup_dir / JOURNAL_NAME
        line = json.dumps({"at": to_text(datetime.now(timezone.utc)), **entry},
                          ensure_ascii=False)
        try:
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8", newline="\n") as out:
                out.write(line + "\n")
                out.flush()
                os.fsync(out.fileno())
        except OSError as err:
            self.warnings.append(f"could not write the journal: {err}")
            return None
        return path

    def snapshot(self, force: bool = False) -> Path | None:
        """Write the whole table to a verified backup, and prune the old ones.

        Returns None for an empty table unless ``force``: an empty snapshot
        would count as the newest and push a real one out of the rotation.
        """
        rows = self.rows()
        if not rows and not force:
            return None
        taken = datetime.now()
        target = _free_name(self.backup_dir, taken, len(rows))
        write_snapshot(target, rows, taken)
        prune(self.backup_dir)
        self._copy_to_mirror(target)
        return target

    def _copy_to_mirror(self, source: Path) -> None:
        """Copy a snapshot to the second folder, if one is set. Never raises:
        the write it backs up has happened, and an unplugged drive must not
        make it look as if it had not."""
        if self.mirror is None:
            return
        try:
            self.mirror.mkdir(parents=True, exist_ok=True)
            target = self.mirror / source.name
            temp = target.with_name(target.name + ".tmp")
            shutil.copyfile(source, temp)
            with temp.open("rb+") as out:
                os.fsync(out.fileno())
            os.replace(temp, target)
            read_snapshot(target)
            prune(self.mirror)
        except (OSError, DbError) as err:
            self.warnings.append(f"the backup was not copied to {self.mirror}: {err}")

    def snapshots(self) -> list[Snapshot]:
        """Every backup there is, newest first, from both folders."""
        found = list_snapshots(self.backup_dir, "data")
        if self.mirror is not None:
            found += list_snapshots(self.mirror, "second folder")
        return sorted(found, key=lambda s: s.taken, reverse=True)

    def ensure_snapshot(self) -> Path | None:
        """Take a snapshot if there is none at all — a table with no backup
        is the one state this module exists to prevent."""
        if list_snapshots(self.backup_dir, "data"):
            return None
        try:
            return self.snapshot()
        except (OSError, DbError) as err:
            # Reading is still safe; the window says the backup is missing.
            self.warnings.append(f"there is no backup of the authors database, "
                                 f"and one could not be made: {err}")
            return None


# ---- Snapshots on disk -----------------------------------------------------

def write_snapshot(target: Path, rows: Sequence[dict], taken: datetime | None = None) -> Path:
    """Write rows as gzipped JSON, flushed to disk, then read back and checked.

    Through a temporary name and an atomic rename, so a crash leaves either
    the previous file or the whole new one — never half of one.
    """
    taken = taken or datetime.now()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "format": SNAPSHOT_FORMAT,
        "version": SCHEMA_VERSION,
        "taken": to_text(taken.astimezone()),
        "count": len(rows),
        "authors": [dict(r) for r in rows],
    }
    temp = target.with_name(target.name + ".tmp")
    with temp.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", filename="authors.json") as out:
            # One author per line, so the unzipped file reads as a list.
            text = json.dumps({k: v for k, v in payload.items() if k != "authors"},
                              ensure_ascii=False)[:-1]
            out.write(text.encode("utf-8"))
            out.write(b', "authors": [\n')
            for i, row in enumerate(payload["authors"]):
                out.write(((",\n" if i else "")
                           + json.dumps(row, ensure_ascii=False)).encode("utf-8"))
            out.write(b"\n]}\n")
        raw.flush()
        os.fsync(raw.fileno())
    os.replace(temp, target)
    back = read_snapshot(target)
    if back["count"] != len(rows):
        raise DbError(f"{target.name} reads back {back['count']} authors, not {len(rows)}")
    return target


def read_snapshot(path: Path | str) -> dict:
    """Load a snapshot and check it against its own record of itself."""
    path = Path(path)
    try:
        with gzip.open(path, "rt", encoding="utf-8") as src:
            data = json.load(src)
    except (OSError, EOFError, json.JSONDecodeError) as err:
        raise DbError(f"{path.name} cannot be read: {err}") from err
    if data.get("format") != SNAPSHOT_FORMAT:
        raise DbError(f"{path.name} is not an authors backup")
    if int(data.get("version") or 0) > SCHEMA_VERSION:
        raise DbError(f"{path.name} was written by a newer version of the toolkit")
    authors = data.get("authors")
    if not isinstance(authors, list) or data.get("count") != len(authors):
        raise DbError(f"{path.name} is incomplete: it says {data.get('count')} "
                      f"authors and holds {len(authors or [])}")
    for row in authors:
        if not isinstance(row, dict) or not str(row.get("key") or "").strip():
            raise DbError(f"{path.name} holds a record with no key")
    return data


def list_snapshots(folder: Path | None, where: str) -> list[Snapshot]:
    if folder is None or not folder.is_dir():
        return []
    found = []
    for path in folder.iterdir():
        match = _SNAPSHOT_NAME.match(path.name)
        if not match:
            continue
        try:
            taken = datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S")
        except ValueError:
            continue
        found.append(Snapshot(path=path, taken=taken, count=int(match.group(3)),
                              where=where))
    return sorted(found, key=lambda s: s.taken, reverse=True)


def keep_set(snapshots: Sequence[Snapshot]) -> set[Path]:
    """Which snapshots survive pruning: see the module's KEEP_* rules."""
    ordered = sorted(snapshots, key=lambda s: s.taken, reverse=True)
    keep = {s.path for s in ordered[:KEEP_RECENT]}

    def newest_per(bucket: Callable[[datetime], object], limit: int) -> None:
        seen: list[object] = []
        for s in ordered:
            b = bucket(s.taken)
            if b in seen:
                continue
            if len(seen) >= limit:
                break
            seen.append(b)
            keep.add(s.path)

    newest_per(lambda t: t.date(), KEEP_DAILY)
    newest_per(lambda t: t.isocalendar()[:2], KEEP_WEEKLY)
    newest_per(lambda t: (t.year, t.month), KEEP_MONTHLY)
    return keep


def prune(folder: Path) -> list[Path]:
    """Delete the snapshots in ``folder`` that no rule keeps. Only files this
    module named are ever considered; anything else there is left alone."""
    found = list_snapshots(folder, "")
    keep = keep_set(found)
    removed = []
    for s in found:
        if s.path not in keep:
            try:
                s.path.unlink()
                removed.append(s.path)
            except OSError:
                pass
    return removed


def _free_name(folder: Path, taken: datetime, count: int) -> Path:
    """A snapshot name that sorts after every one already there.

    Two writes in one second are normal — a restore snapshots before and after
    — and the order of the files is the order of the states, which is what a
    restore relies on. So a name never repeats or precedes the newest one's
    time: it takes the next second instead. The name is a label; the order is
    what matters.
    """
    when = taken.replace(microsecond=0)
    existing = list_snapshots(folder, "")
    if existing and existing[0].taken >= when:
        when = existing[0].taken + timedelta(seconds=1)
    return folder / f"authors-{when:%Y%m%d-%H%M%S}-{count}.json.gz"


# ---- Times -----------------------------------------------------------------

# One format, second precision, zone written on it. Dropping the fraction only
# ever moves a visit date *earlier*, which can re-show a wallpaper published in
# that same second but never skip one.

_TEXT_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def to_text(when: datetime | None) -> str | None:
    """A datetime as stored. Naive means local time, as everywhere else here."""
    if when is None:
        return None
    return when.astimezone(timezone.utc).strftime(_TEXT_FORMAT)


def from_text(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.strptime(text, _TEXT_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        # Anything ISO with an offset is still a date worth reading.
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _text_of(column: str, value):
    if column in ("added", "visited"):
        return to_text(value) if isinstance(value, datetime) else value
    return str(value) if value is not None else value


def _row_of(fields: dict) -> dict:
    key = str(fields.get("key") or "").strip()
    if not key:
        raise DbError("an author needs a key")
    return {"key": key, "name": str(fields.get("name") or ""),
            "added": _text_of("added", fields.get("added")),
            "visited": _text_of("visited", fields.get("visited"))}


def _fetch(conn: sqlite3.Connection, key: str) -> dict | None:
    row = conn.execute("SELECT key, name, added, visited FROM authors WHERE key = ?",
                       (key,)).fetchone()
    if row is None:
        return None
    return {"key": row[0], "name": row[1], "added": row[2], "visited": row[3]}


def _same(current, wanted) -> bool:
    """Whether a field is already what a write would make it."""
    if isinstance(current, datetime) and isinstance(wanted, datetime):
        return to_text(current) == to_text(wanted)
    if current is None or wanted is None:
        return current is wanted
    return current == wanted


def _later(a: datetime | None, b: datetime | None) -> datetime | None:
    if a is None or b is None:
        return a or b
    return max(a, b)


def _earlier(a: datetime | None, b: datetime | None) -> datetime | None:
    if a is None or b is None:
        return a or b
    return min(a, b)


def _short(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, datetime):
        return f"{value.astimezone():%Y-%m-%d}"
    return str(value)


def _chunks(items: Sequence, size: int):
    for start in range(0, len(items), size):
        yield items[start:start + size]
