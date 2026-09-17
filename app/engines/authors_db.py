"""authors_db.py — the authors database, read carefully and written to twice.

The collection is the one the old web app built up over five years: 39 047
authors in Mongo, and the only copy. Nothing here is a fresh start — the schema
is left exactly as `mongoose` wrote it, down to the `__v` field, so the old
front end goes on reading the same records. This module is a new way in, not a
new store.

**Matching is the hard part.** An author is filed under whatever the URL said
on the day they were added, and that is two different things: a steamID64 for
18 535 of them and a vanity name for the other 20 512. Steam hands us a numeric
id, so a lookup on that alone finds a little under half the database and
cheerfully creates a duplicate for the rest. Worse, duplicates are already
there — a vanity name and an account number can be one person, added twice, with
visit dates two months apart — so a lookup has to expect *more than one* answer
and say so rather than pick.

Every lookup therefore runs on both keys a profile answers to, matched without
regard to case, and returns a list. Deciding what a list of two means is the
review's business, not this module's.

**Writing.** Nothing here writes when it is asked to. `plan_*` returns a
:class:`Change` describing what would happen, field by field; only
:meth:`AuthorsDb.apply` touches the database, and it writes the previous state
of every document it is about to change into `data/authors_backup/` first. A
five-year-old collection with no second copy earns that much ceremony.
"""
from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Sequence

from ..settings import app_data_dir
from . import mongo_srv
from .steam_api import is_steam_id64

COLLECTION = "steamusers"
BACKUP_DIR = app_data_dir() / "authors_backup"

# The database stores a name for every author and nothing else is required.
FIELDS = ("name", "steamId", "dateAdded", "dateVisited", "favorite",
          "newWallpapers", "referenceLink", "reviewedFavorites", "creator")

# Matching ignores case because a vanity name does: steamcommunity.com/id/O0P
# and /id/o0p are the same profile, and the database holds whichever was typed.
_CASE_INSENSITIVE = {"locale": "en", "strength": 2}


class DbError(RuntimeError):
    """The database could not be reached, or refused what was asked of it."""


def explain(err: Exception) -> str:
    """What went wrong, in a line worth showing rather than the raw dump.

    A failed name lookup arrives as five lines of nameserver addresses repeated
    three times over, which tells a reader nothing they can act on. The two
    failures that actually happen — the lookup and the password — get said
    plainly; anything else is passed through, trimmed.
    """
    text = str(err)
    if "resolution lifetime expired" in text or "DNS operation timed out" in text:
        servers = sorted(set(re.findall(r"Do53:([0-9.]+)@", text)))
        where = ", ".join(servers) if servers else "the configured DNS servers"
        return (f"the name lookup timed out — {where} did not answer. "
                "That is usually a VPN, whose DNS replies to Windows but not to "
                "a program asking it directly.")
    if "Authentication failed" in text or "auth failed" in text.lower():
        return "the user name or password was refused."
    if "SSL handshake failed" in text or "certificate verify failed" in text:
        return f"the TLS handshake failed: {text.splitlines()[0][:200]}"
    return text.splitlines()[0][:300]


# ---- One author ------------------------------------------------------------

@dataclass
class Author:
    """One record, as the old app shaped it."""

    id: object                      # ObjectId
    name: str = ""
    steam_id: str = ""
    added: datetime | None = None
    visited: datetime | None = None
    favorite: bool = False
    new_wallpapers: int | None = None
    reference_link: str | None = None
    reviewed_favorites: bool = True

    @classmethod
    def from_doc(cls, doc: dict) -> "Author":
        return cls(
            id=doc.get("_id"),
            name=doc.get("name") or "",
            steam_id=str(doc.get("steamId") or ""),
            added=from_stored(doc.get("dateAdded")),
            visited=from_stored(doc.get("dateVisited")),
            favorite=bool(doc.get("favorite")),
            new_wallpapers=doc.get("newWallpapers"),
            reference_link=doc.get("referenceLink"),
            reviewed_favorites=bool(doc.get("reviewedFavorites", True)),
        )

    @property
    def numeric(self) -> bool:
        """Whether this record is filed under a steamID64 rather than a name.

        Not "does it look like a number": a vanity URL can be all digits, and
        this collection holds several that are.
        """
        return is_steam_id64(self.steam_id)

    def __str__(self) -> str:
        return f"{self.name} ({self.steam_id})"


@dataclass
class Change:
    """A write that has not happened yet.

    ``fields`` is what would be set; ``before`` is what is there now, so the
    same object serves the confirmation dialog, the log line and the backup.
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
        return f"{self.fields.get('name', '?')} ({self.fields.get('steamId', '?')})"

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


# ---- The database ----------------------------------------------------------

class AuthorsDb:
    """Reads and (deliberately reluctantly) writes the authors collection."""

    def __init__(self, uri: str, database: str | None = None,
                 creator: object | None = None, timeout_ms: int = 20000):
        self.uri = uri
        self.database = database or _database_from(uri)
        self.creator = creator
        self.timeout_ms = timeout_ms
        self._client = None
        self._col = None
        self._collation_ok = True
        self._lock = threading.Lock()
        self.dialled = ""

    # -- connecting --------------------------------------------------------

    def dial(self) -> tuple[str, str]:
        """The URI actually handed to pymongo, and how it was arrived at.

        A `mongodb+srv://` string makes pymongo look the seed list up with
        dnspython, which queries the configured nameservers itself. Behind a VPN
        those answer Windows but not a library talking to them directly, and
        every lookup then burns its full timeout for nothing. Resolving through
        Windows first (see :mod:`mongo_srv`) costs a tenth of a second and keeps
        the whole app on one resolver. If that cannot be done — another
        platform, or no SRV record — the original string is handed over
        untouched and pymongo does it its own way.
        """
        if not mongo_srv.is_srv(self.uri) or not mongo_srv.usable():
            return self.uri, "as given"
        try:
            return mongo_srv.expand(self.uri), "seed list resolved by Windows"
        except mongo_srv.SrvError:
            return self.uri, "as given"

    def connect(self) -> "AuthorsDb":
        try:
            from pymongo import MongoClient
        except ImportError as err:  # pragma: no cover - dependency is declared
            raise DbError("pymongo is not installed") from err
        uri, self.dialled = self.dial()
        try:
            self._client = MongoClient(uri, serverSelectionTimeoutMS=self.timeout_ms)
            self._col = self._client[self.database][COLLECTION]
            self._col.database.client.admin.command("ping")
        except Exception as err:
            raise DbError(f"cannot reach the authors database: {explain(err)}") from err
        if self.creator is None:
            self.creator = self._only_creator()
        return self

    def _only_creator(self):
        """Every record in this collection belongs to one account; find it."""
        owners = self._col.distinct("creator")
        return owners[0] if len(owners) == 1 else None

    @property
    def connected(self) -> bool:
        return self._col is not None

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
        self._client = None
        self._col = None

    def __enter__(self) -> "AuthorsDb":
        return self.connect()

    def __exit__(self, *_exc) -> None:
        self.close()

    def count(self) -> int:
        return self._require().count_documents({})

    def _require(self):
        if self._col is None:
            raise DbError("not connected")
        return self._col

    # -- reading -----------------------------------------------------------

    def lookup(self, keys: Iterable[str]) -> list[Author]:
        """Every record filed under any of these keys, case ignored."""
        return self.lookup_many({"one": list(keys)}).get("one", [])

    def lookup_many(self, wanted: dict[str, Sequence[str]]) -> dict[str, list[Author]]:
        """Resolve many authors in one round trip.

        A weekly review asks about four hundred authors at once and the
        database is in Frankfurt: asking four hundred times costs a minute of
        latency and nothing else. One query with every key in it costs one.
        """
        self._require()
        keys: list[str] = []
        for group in wanted.values():
            keys.extend(str(k).strip() for k in group if str(k).strip())
        keys = list(dict.fromkeys(keys))
        if not keys:
            return {name: [] for name in wanted}

        docs = self._find_by_steam_ids(keys)
        by_key: dict[str, list[Author]] = {}
        for doc in docs:
            author = Author.from_doc(doc)
            by_key.setdefault(author.steam_id.lower(), []).append(author)

        out: dict[str, list[Author]] = {}
        for name, group in wanted.items():
            seen: dict[object, Author] = {}
            for key in group:
                for author in by_key.get(str(key).strip().lower(), []):
                    # One record can answer to both of an author's keys; a
                    # second *record* is the duplicate we are hunting for, so
                    # the two must not be confused.
                    seen[author.id] = author
            out[name] = list(seen.values())
        return out

    def _find_by_steam_ids(self, keys: list[str]) -> list[dict]:
        """`$in` on steamId, case-insensitively unless case cannot matter.

        The collection has a plain index on `steamId` and a case-insensitive
        query cannot use it — MongoDB only uses an index whose collation matches
        the query's — so asking for one costs a COLLSCAN of all 38 897
        documents: 46 ms on the server against 2 ms through the index.

        That is worth avoiding but not at any price. Splitting the keys into a
        plain query and a collated one was measured slower, not faster: the
        round trip to Frankfurt is 220 ms and the scan it saves is 46 ms, so two
        queries (369 ms) lose to one (273 ms). One query it stays. The split is
        taken only when it costs nothing — when every key is digits, which have
        no case, and the whole batch can go through the index.

        The proper fix is an index carrying this collation, which would make the
        one query indexed as well; that is a change to the database rather than
        to this app, so it is written up in the README instead of done here.
        """
        collection = self._require()
        if all(k.isdigit() for k in keys):
            return list(collection.find({"steamId": {"$in": keys}}))
        return self._find_cased(collection, keys)

    def _find_cased(self, collection, keys: list[str]) -> list[dict]:
        """A lookup where upper and lower case are two different things."""
        if self._collation_ok:
            try:
                return list(collection.find({"steamId": {"$in": keys}})
                            .collation(_CASE_INSENSITIVE))
            except Exception:
                # An older server, or an index that refuses the collation.
                self._collation_ok = False

        # Numbers do not have a case, so only the vanity names need the slow
        # path — and they are the minority of any one review.
        numeric = [k for k in keys if k.isdigit()]
        named = [k for k in keys if not k.isdigit()]
        docs: list[dict] = []
        if numeric:
            docs.extend(collection.find({"steamId": {"$in": numeric}}))
        for chunk in _chunks(named, 100):
            patterns = [{"steamId": {"$regex": f"^{re.escape(k)}$", "$options": "i"}}
                        for k in chunk]
            docs.extend(collection.find({"$or": patterns}))
        return docs

    def by_id(self, author_id) -> Author | None:
        doc = self._require().find_one({"_id": author_id})
        return Author.from_doc(doc) if doc else None

    def search(self, text: str, limit: int = 50) -> list[Author]:
        """Find authors by name or id, the way the old search box did."""
        needle = str(text).strip()
        if not needle:
            return []
        safe = re.escape(needle)
        query = {"$or": [{"name": {"$regex": safe, "$options": "i"}},
                         {"steamId": {"$regex": f"^{safe}$", "$options": "i"}}]}
        return [Author.from_doc(d) for d in self._require().find(query).limit(limit)]

    def stale(self, before: datetime, limit: int = 0) -> list[Author]:
        """Authors not visited since ``before`` — the backlog, oldest first."""
        cursor = self._require().find({"dateVisited": {"$lt": before}}) \
            .sort("dateVisited", 1)
        if limit:
            cursor = cursor.limit(limit)
        return [Author.from_doc(d) for d in cursor]

    # -- planning a write --------------------------------------------------

    def plan_create(self, name: str, steam_id: str, visited: datetime | None = None,
                    added: datetime | None = None, reference_link: str | None = None,
                    new_wallpapers: int | None = None) -> Change:
        """A new author, shaped exactly as the old app would have written one."""
        now = datetime.now(timezone.utc)
        fields = {
            "name": name,
            "steamId": str(steam_id),
            "dateAdded": to_stored(added) or now,
            "dateVisited": to_stored(visited) or to_stored(added) or now,
            "favorite": False,
            "newWallpapers": new_wallpapers if new_wallpapers is not None else 0,
            "referenceLink": reference_link,
            "reviewedFavorites": True,
            "creator": self.creator,
            # mongoose stamps a version key on everything it inserts; without
            # it these records would be the odd ones out in their own table.
            "__v": 0,
        }
        return Change(kind="create", fields=fields)

    def plan_update(self, author: Author, *, visited: datetime | None = None,
                    name: str | None = None, steam_id: str | None = None,
                    new_wallpapers: int | None = None,
                    reference_link: str | None = None,
                    favorite: bool | None = None,
                    reviewed_favorites: bool | None = None,
                    reason: str = "") -> Change | None:
        """What would change on this record — or None if nothing would.

        Returning None for a no-op matters: a review of four hundred authors
        should write to the handful that actually moved, and a confirmation
        list padded with untouched records is one nobody reads.
        """
        wanted = {
            "name": name,
            "steamId": str(steam_id) if steam_id is not None else None,
            "dateVisited": to_stored(visited),
            "newWallpapers": new_wallpapers,
            "referenceLink": reference_link,
            "favorite": favorite,
            "reviewedFavorites": reviewed_favorites,
        }
        current = {
            "name": author.name,
            "steamId": author.steam_id,
            "dateVisited": author.visited,
            "newWallpapers": author.new_wallpapers,
            "referenceLink": author.reference_link,
            "favorite": author.favorite,
            "reviewedFavorites": author.reviewed_favorites,
        }
        fields = {}
        before = {}
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

        The pair disagree on exactly the fields that matter: the earlier
        `dateAdded` is when this author was really found, the later
        `dateVisited` is how much of their work has really been seen. Taking
        the safe end of each means a merge can never make the toolkit re-show
        wallpapers or, worse, skip them.
        """
        # The deletion goes first. When the survivor is taking the identifier
        # the duplicate is still filed under, the unique index on `steamId`
        # rejects the update until that record is gone.
        changes: list[Change] = [
            self.plan_delete(drop, reason=f"duplicate of {keep}")]
        update = self.plan_update(
            keep,
            visited=_later(keep.visited, drop.visited),
            steam_id=keep.steam_id if keep.numeric else (
                drop.steam_id if drop.numeric else None),
            reference_link=keep.reference_link or drop.reference_link,
            reason=f"merged with duplicate {drop}",
        )
        earliest = _earlier(keep.added, drop.added)
        if earliest and keep.added and earliest < keep.added:
            # dateAdded is immutable to the old API but not to the database,
            # and losing the real discovery date would falsify the statistics
            # the web app draws from it.
            update = update or Change(kind="update", author=keep, fields={}, before={})
            update.fields["dateAdded"] = to_stored(earliest)
            (update.before or {})["dateAdded"] = keep.added
        if update:
            changes.append(update)
        return changes

    # -- writing -----------------------------------------------------------

    def apply(self, changes: Sequence[Change], backup: bool = True,
              bulk: bool = True, batch: int = 500,
              on_progress: Callable[[int, int], None] | None = None) -> dict:
        """Carry out planned changes, after writing down what they replace.

        Order is preserved, and it matters: a merge deletes the record holding
        an identifier before the update that takes it, or the unique index
        rejects the write. So batches are sent ordered, and a failure stops the
        run rather than skipping past it — half a merge is worse than none.

        A review moves a handful of records and sends them one at a time; a
        migration moves twenty thousand, and one round trip each to Frankfurt
        is twenty minutes of waiting for nothing.
        """
        collection = self._require()
        changes = [c for c in changes if c is not None]
        if not changes:
            return {"created": 0, "updated": 0, "deleted": 0, "backup": None}

        backup_path = self.write_backup(changes) if backup else None
        counts = {"created": 0, "updated": 0, "deleted": 0}
        with self._lock:
            if bulk and len(changes) > 1 and hasattr(collection, "bulk_write"):
                self._apply_bulk(collection, changes, counts, batch, on_progress)
            else:
                self._apply_singly(collection, changes, counts, on_progress)
        return {**counts, "backup": str(backup_path) if backup_path else None}

    def _apply_singly(self, collection, changes, counts, on_progress) -> None:
        for done, change in enumerate(changes, 1):
            if change.kind == "create":
                try:
                    collection.insert_one(dict(change.fields))
                except Exception as err:
                    # steamId is unique; someone else may have added this
                    # author between the plan and the write.
                    raise DbError(f"could not create {change.target}: {err}") from err
                counts["created"] += 1
            elif change.kind == "update":
                result = collection.update_one({"_id": change.author.id},
                                               {"$set": dict(change.fields)})
                _must_have_matched(result, "matched_count", change)
                counts["updated"] += 1
            elif change.kind == "delete":
                result = collection.delete_one({"_id": change.author.id})
                _must_have_matched(result, "deleted_count", change)
                counts["deleted"] += 1
            if on_progress:
                on_progress(done, len(changes))

    def _apply_bulk(self, collection, changes, counts, batch, on_progress) -> None:
        from pymongo import DeleteOne, InsertOne, UpdateOne

        done = 0
        for start in range(0, len(changes), batch):
            chunk = changes[start:start + batch]
            operations = []
            for change in chunk:
                if change.kind == "create":
                    operations.append(InsertOne(dict(change.fields)))
                    counts["created"] += 1
                elif change.kind == "update":
                    operations.append(UpdateOne({"_id": change.author.id},
                                                {"$set": dict(change.fields)}))
                    counts["updated"] += 1
                elif change.kind == "delete":
                    operations.append(DeleteOne({"_id": change.author.id}))
                    counts["deleted"] += 1
            try:
                result = collection.bulk_write(operations, ordered=True)
            except Exception as err:
                raise DbError(
                    f"stopped after {done} of {len(changes)} changes: {err}") from err
            _must_have_landed(result, chunk, done, len(changes))
            done += len(chunk)
            if on_progress:
                on_progress(done, len(changes))

    # -- the whole thing, on disk ------------------------------------------

    def dump(self, path: Path | str | None = None,
             on_progress: Callable[[int, int], None] | None = None) -> Path:
        """Write every record to a file, before anything is allowed to change it.

        The per-change backups above cover a review, where a handful of
        records move and the previous values are the whole story. A migration
        is not that: it rewrites identifiers across tens of thousands of rows
        and merges some of them away, and the only honest safety net for that
        is a copy of the collection as it stands. Five years of collecting live
        here and there is no second copy anywhere.

        Documents are written one per line inside a JSON array so a half-
        finished dump is still readable, and every value is stored as text —
        ObjectIds and dates included — so the file says exactly what it holds
        without a driver to interpret it.
        """
        collection = self._require()
        total = collection.count_documents({})
        target = Path(path) if path else (
            BACKUP_DIR / f"dump-{datetime.now():%Y%m%d-%H%M%S}.json")
        target.parent.mkdir(parents=True, exist_ok=True)

        written = 0
        with target.open("w", encoding="utf-8") as out:
            out.write('{\n"taken": "%s",\n"database": "%s",\n"collection": "%s",\n'
                      '"expected": %d,\n"documents": [\n'
                      % (datetime.now().isoformat(timespec="seconds"),
                         self.database, COLLECTION, total))
            for doc in collection.find({}):
                out.write(("" if written == 0 else ",\n")
                          + json.dumps(_plain(doc), ensure_ascii=False))
                written += 1
                if on_progress and written % 1000 == 0:
                    on_progress(written, total)
            out.write("\n],\n\"written\": %d\n}\n" % written)
        if on_progress:
            on_progress(written, total)
        return target

    @staticmethod
    def read_dump(path: Path | str) -> dict:
        """Load a dump and check it against its own record of itself."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        documents = data.get("documents") or []
        if data.get("written") != len(documents):
            raise DbError(f"{path} is truncated: {len(documents)} of "
                          f"{data.get('written')} documents")
        if data.get("expected") and data["expected"] != len(documents):
            raise DbError(f"{path} holds {len(documents)} documents but the "
                          f"collection had {data['expected']} when it was taken")
        return data

    def restore(self, path: Path | str, remove_unknown: bool = False) -> dict:
        """Put a dump back.

        Records are matched on `_id` and written whole, so a restore undoes
        edits and brings back anything that was deleted. It does *not* remove
        records added since the dump unless asked to: after a migration the
        usual want is "undo what I just did", not "erase the week".
        """
        from bson import ObjectId

        collection = self._require()
        data = self.read_dump(path)
        restored = 0
        seen: list = []
        for plain in data["documents"]:
            doc = revive_ids(plain)
            seen.append(doc["_id"])
            collection.update_one({"_id": doc["_id"]}, {"$set": doc}, upsert=True)
            restored += 1
        removed = 0
        if remove_unknown:
            for doc in list(collection.find({}, {"_id": 1})):
                if doc.get("_id") not in seen:
                    collection.delete_one({"_id": doc.get("_id")})
                    removed += 1
        return {"restored": restored, "removed": removed}

    def write_backup(self, changes: Sequence[Change],
                     directory: Path = BACKUP_DIR) -> Path | None:
        """Save the current state of every record a change would touch.

        Whole documents, not just the fields being written: the point of the
        file is that a mistake can be undone from it without knowing what the
        mistake was.
        """
        collection = self._require()
        targets = [c.author.id for c in changes if c.author is not None]
        if not targets and not any(c.kind == "create" for c in changes):
            return None
        directory.mkdir(parents=True, exist_ok=True)
        docs = list(collection.find({"_id": {"$in": targets}})) if targets else []
        path = directory / f"{datetime.now():%Y%m%d-%H%M%S}.json"
        path.write_text(json.dumps({
            "saved": datetime.now().isoformat(timespec="seconds"),
            "planned": [c.describe() for c in changes],
            "documents": [_plain(d) for d in docs],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        return path


# ---- Odds and ends ---------------------------------------------------------

# A write that matched nothing is not an error to Mongo — it is a successful
# write of nothing. That is how a whole migration once reported 19 573 updates
# and changed not one document: the plan had been built from a dump, where
# `_id` is text, so every filter addressed an id no document has. Silence is
# the failure mode worth spending two functions on.

def _must_have_matched(result, field: str, change: "Change") -> None:
    matched = getattr(result, field, None)
    if matched == 0:
        raise DbError(f"{change.describe()} matched no document — the record's "
                      f"id ({change.author.id!r}) is not one this collection has")


def _must_have_landed(result, chunk: Sequence["Change"], done: int, total: int) -> None:
    wanted = {kind: sum(1 for c in chunk if c.kind == kind)
              for kind in ("update", "delete", "create")}
    landed = {
        "update": getattr(result, "matched_count", None),
        "delete": getattr(result, "deleted_count", None),
        "create": getattr(result, "inserted_count", None),
    }
    for kind, expected in wanted.items():
        actual = landed[kind]
        if expected and actual is not None and actual < expected:
            raise DbError(
                f"after {done} of {total} changes, a batch of {expected} "
                f"{kind}s reached only {actual} documents — the plan is "
                f"addressing records this collection does not have")


def _database_from(uri: str) -> str:
    """The database name out of a connection string."""
    without_query = uri.split("?", 1)[0]
    name = without_query.rsplit("/", 1)[-1]
    if not name or "@" in name or ":" in name:
        raise DbError("the connection string does not name a database")
    return name


CLUSTER_ENV_VAR = "WET_DB_CLUSTER"


def uri_from_env_file(path: Path | str) -> str:
    """Build a connection string out of a web app's `.env`.

    This exists for one migration path: a Node/Python service that already
    talks to the same Atlas database keeps its credentials in a `.env`, and
    retyping them into this app is how they get mistyped. So the file is read
    instead.

    Its `DB_HOST` is the *username*, not a host — a misnaming worth keeping in
    one place rather than rediscovering.

    The cluster itself is deployment-specific and is deliberately not baked in
    here. It comes from `DB_CLUSTER` in the same file, or from the
    ``WET_DB_CLUSTER`` environment variable. A file that already has a whole
    `MONGODB_URI` skips all of this.
    """
    import os
    import urllib.parse

    values: dict[str, str] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    if values.get("MONGODB_URI"):
        return values["MONGODB_URI"]
    missing = [k for k in ("DB_HOST", "DB_PASS", "DB_NAME") if not values.get(k)]
    if missing:
        raise DbError(f"{path} is missing {', '.join(missing)}")
    cluster = values.get("DB_CLUSTER") or os.environ.get(CLUSTER_ENV_VAR, "")
    if not cluster:
        raise DbError(
            f"{path} does not say which cluster to connect to — add a "
            f"DB_CLUSTER line (for example "
            f"DB_CLUSTER=mycluster.ab12c.mongodb.net), set the "
            f"{CLUSTER_ENV_VAR} environment variable, or put a full "
            f"MONGODB_URI in the file")
    # `safe=""` on purpose: quote leaves "/" alone by default, and a slash in a
    # password would end the host part of the uri and take the database name
    # with it. The old server escapes with encodeURIComponent, which is this.
    user = urllib.parse.quote(values["DB_HOST"], safe="")
    password = urllib.parse.quote(values["DB_PASS"], safe="")
    return (f"mongodb+srv://{user}:{password}@{cluster.strip()}/"
            f"{values['DB_NAME']}?retryWrites=true&w=majority")


def _chunks(items: Sequence, size: int):
    for start in range(0, len(items), size):
        yield items[start:start + size]


# Two kinds of naive datetime meet in this module and they mean different
# things. Mongo hands back UTC with the tzinfo stripped off; the rest of the
# suite works in local time, because `datetime.fromtimestamp` and
# `datetime.now` do. Reading one as the other is a silent three-hour error in
# `dateVisited` on this machine — enough to re-show a morning's wallpapers, or
# to skip them. So each direction gets its own conversion and nothing else
# touches tzinfo.

def from_stored(when: datetime | None) -> datetime | None:
    """A stored datetime, made aware. Mongo keeps UTC and drops the marker."""
    if when is None:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def to_stored(when: datetime | None) -> datetime | None:
    """A datetime on its way into the database. Naive means local here."""
    if when is None:
        return None
    return when.astimezone(timezone.utc)


def _same(current, wanted) -> bool:
    """Whether a field is already what a write would make it."""
    if isinstance(current, datetime) and isinstance(wanted, datetime):
        return abs(from_stored(current) - to_stored(wanted)).total_seconds() < 1
    if isinstance(current, str) and isinstance(wanted, str):
        return current == wanted
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
        return f"{value:%Y-%m-%d}"
    return str(value)


# The two Mongo types this schema uses that JSON has no word for. Storing them
# as plain text keeps a dump readable without a driver; putting them back needs
# to know which fields they were, and in a fixed five-field schema that is a
# list rather than a guess.
_OBJECT_IDS = ("_id", "creator")
_DATES = ("dateAdded", "dateVisited")


def _plain(doc: dict) -> dict:
    """A document as JSON: ObjectIds and dates as text."""
    out = {}
    for key, value in doc.items():
        if isinstance(value, datetime):
            out[key] = value.isoformat()
        elif isinstance(value, (str, int, float, bool)) or value is None:
            out[key] = value
        else:
            out[key] = str(value)
    return out


def revive_ids(plain: dict) -> dict:
    """The inverse of :func:`_plain`, for putting a dump back."""
    from bson import ObjectId

    out = dict(plain)
    for key in _OBJECT_IDS:
        if isinstance(out.get(key), str):
            try:
                out[key] = ObjectId(out[key])
            except Exception as err:
                raise DbError(f"{key} in the dump is not an id: {out[key]}") from err
    for key in _DATES:
        if isinstance(out.get(key), str):
            try:
                out[key] = datetime.fromisoformat(out[key])
            except ValueError as err:
                raise DbError(f"{key} in the dump is not a date: {out[key]}") from err
    return out
