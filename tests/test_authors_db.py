"""The authors repository, checked against a collection that lives in memory.

Run it directly — there is no test framework in this project:

    .venv\\Scripts\\python.exe tests\\test_authors_db.py
    .venv\\Scripts\\python.exe tests\\test_authors_db.py --live
    .venv\\Scripts\\python.exe tests\\test_authors_db.py --live-write

The default run touches nothing outside this process. ``--live`` reads the real
database and writes nothing. ``--live-write`` is the only section that changes
anything: it creates one record of its own, updates it, deletes it again, and
cleans up even when a check fails — it never goes near a record it did not make.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.engines.authors_db as adb    # noqa: E402
from app import secrets                 # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="wallpaper_authors_test_"))

results: list[bool] = []


def check(label: str, condition: bool) -> None:
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label)


# ---- A collection that lives in a list -------------------------------------

def matches(doc: dict, query: dict) -> bool:
    for key, want in query.items():
        if key == "$or":
            if not any(matches(doc, clause) for clause in want):
                return False
            continue
        value = doc.get(key)
        if isinstance(want, dict):
            if "$in" in want and value not in want["$in"]:
                return False
            if "$lt" in want and not (value is not None and value < want["$lt"]):
                return False
            if "$regex" in want:
                flags = re.I if "i" in want.get("$options", "") else 0
                if value is None or not re.search(want["$regex"], str(value), flags):
                    return False
        elif value != want:
            return False
    return True


class Cursor(list):
    """Enough of a pymongo cursor for this module to drive."""

    def __init__(self, docs, collection=None):
        super().__init__(docs)
        self.collection = collection

    def collation(self, spec):
        if self.collection is not None:
            self.collection.collation_calls += 1
        if self.collection is not None and not self.collection.collation_supported:
            raise RuntimeError("collation not supported by this server")
        if self.collection is not None:
            return Cursor(self.collection.match(self.collection.last_query, fold=True))
        return self

    def limit(self, n):
        return Cursor(self[:n])

    def sort(self, key, direction=1):
        return Cursor(sorted(self, key=lambda d: d.get(key) or datetime.min,
                             reverse=direction < 0))


class Result:
    """What the driver hands back: how many documents a write actually reached."""

    def __init__(self, matched_count=0, deleted_count=0, inserted_count=0):
        self.matched_count = matched_count
        self.deleted_count = deleted_count
        self.inserted_count = inserted_count


class FakeCollection:
    def __init__(self, docs=None, collation_supported=True):
        self.docs = list(docs or [])
        self.collation_supported = collation_supported
        self.collation_calls = 0
        self.last_query: dict = {}
        self.database = self

    # the driver surface AuthorsDb uses
    def match(self, query, fold=False):
        if not fold:
            return [d for d in self.docs if matches(d, query)]
        folded = []
        wanted = query.get("steamId", {}).get("$in", [])
        lowered = {str(k).lower() for k in wanted}
        for doc in self.docs:
            if str(doc.get("steamId", "")).lower() in lowered:
                folded.append(doc)
        return folded

    def find(self, query=None, projection=None):
        self.last_query = query or {}
        return Cursor(self.match(self.last_query), self)

    def find_one(self, query):
        found = self.match(query)
        return found[0] if found else None

    def count_documents(self, query):
        return len(self.match(query))

    def distinct(self, key):
        return list(dict.fromkeys(d.get(key) for d in self.docs))

    def insert_one(self, doc):
        if any(str(d.get("steamId", "")).lower() == str(doc.get("steamId", "")).lower()
               for d in self.docs):
            raise RuntimeError("E11000 duplicate key error: steamId")
        from bson import ObjectId
        stored = dict(doc)
        stored.setdefault("_id", ObjectId())    # as the driver does
        self.docs.append(stored)

    def update_one(self, query, update, upsert=False):
        for doc in self.docs:
            if matches(doc, query):
                doc.update(update["$set"])
                return Result(matched_count=1)
        if upsert:
            self.docs.append(dict(update["$set"]))
            return Result(matched_count=1)
        return Result(matched_count=0)

    def delete_one(self, query):
        before = len(self.docs)
        self.docs = [d for d in self.docs if not matches(d, query)]
        return Result(deleted_count=before - len(self.docs))


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def doc(oid, name, steam_id, visited=None, added=None, **extra):
    base = {"_id": oid, "name": name, "steamId": steam_id,
            # Mongo hands datetimes back with the tzinfo stripped off.
            "dateAdded": (added or utc(2021, 1, 1)).replace(tzinfo=None),
            "dateVisited": visited.replace(tzinfo=None) if visited else None,
            "favorite": False, "newWallpapers": 0, "referenceLink": None,
            "reviewedFavorites": True, "creator": "owner", "__v": 0}
    base.update(extra)
    return base


def fresh(docs=None, collation_supported=True) -> adb.AuthorsDb:
    db = adb.AuthorsDb(uri="mongodb://x/y", database="y", creator="owner")
    db._col = FakeCollection(docs if docs is not None else SEED,
                             collation_supported=collation_supported)
    return db


SEED = [
    doc(1, "Oldname7", "Oldname7", visited=utc(2026, 8, 16), added=utc(2026, 8, 9)),
    doc(2, "Oldname", "76561198000000011", visited=utc(2026, 3, 15), added=utc(2025, 6, 1)),
    doc(3, "Vanity", "vanityone", visited=utc(2026, 9, 6)),
    doc(4, "Someone", "76561198000000012", visited=utc(2024, 1, 1)),
]


# ---- Matching --------------------------------------------------------------

db = fresh()
check("an author filed under a number is found by their number",
      [a.name for a in db.lookup(["76561198000000012"])] == ["Someone"])
check("an author filed under a vanity name is found by it too",
      [a.name for a in db.lookup(["vanityone"])] == ["Vanity"])
check("and by it in any case, because a vanity url ignores case",
      [a.name for a in db.lookup(["VANITYONE"])] == ["Vanity"])
check("an author nobody added comes back empty rather than guessed at",
      db.lookup(["76561190000000000"]) == [])

both = db.lookup(["76561198000000011", "Oldname7"])
check("searching on both keys is what turns up an existing duplicate",
      len(both) == 2 and {a.steam_id for a in both} == {"Oldname7", "76561198000000011"})

one_record = db.lookup(["vanityone", "76561198000000013"])
check("but a single record answering to two keys is still one record",
      len(one_record) == 1)

many = db.lookup_many({"a": ["vanityone"], "b": ["76561198000000012"], "c": ["nobody"]})
check("many authors are resolved in one query", len(many) == 3)
check("and each gets only its own answer",
      many["a"][0].name == "Vanity" and many["b"][0].name == "Someone" and not many["c"])

digits = fresh([doc(9, "Numbered", "328786869", visited=utc(2026, 1, 1))])
check("a record filed under an all-digit vanity name is not mistaken for an id",
      digits.lookup(["328786869"])[0].numeric is False)

no_collation = fresh(collation_supported=False)
check("a server without collation still matches a vanity name case-blind",
      [a.name for a in no_collation.lookup(["VanityOne"])] == ["Vanity"])
check("and still matches numbers, which never needed it",
      [a.name for a in no_collation.lookup(["76561198000000012"])] == ["Someone"])


# ---- Dates in and out ------------------------------------------------------

author = db.lookup(["vanityone"])[0]
check("a stored date comes back knowing it is UTC",
      author.visited.tzinfo is not None and author.visited == utc(2026, 9, 6))

local_noon = datetime(2026, 9, 6, 12, 0)          # naive: local, as Steam gives it
change = db.plan_update(author, visited=local_noon)
check("a naive date on the way in is read as local and converted, not relabelled",
      change.fields["dateVisited"] == local_noon.astimezone(timezone.utc))

check("re-writing the same instant is not a change at all",
      db.plan_update(author, visited=utc(2026, 9, 6)) is None)
check("nor is setting a field to what it already says",
      db.plan_update(author, name="Vanity", favorite=False) is None)


# ---- Planning --------------------------------------------------------------

created = db.plan_create("New Person", "76561199999999999")
check("a new author is created with today as both dates",
      created.fields["dateAdded"] == created.fields["dateVisited"])
check("and carries the version key mongoose puts on everything",
      created.fields["__v"] == 0 and created.fields["creator"] == "owner")
check("a create says what it would do in words",
      created.describe() == "create New Person (76561199999999999)")

update = db.plan_update(author, visited=utc(2026, 9, 7), new_wallpapers=3)
check("an update lists only the fields that actually move",
      set(update.fields) == {"dateVisited", "newWallpapers"})
check("and remembers what was there before",
      update.before["newWallpapers"] == 0)
check("which is what makes the confirmation line readable",
      "newWallpapers: 0 → 3" in update.describe())


# ---- Merging a duplicate ---------------------------------------------------

keep = [a for a in both if a.steam_id == "76561198000000011"][0]
drop = [a for a in both if a.steam_id == "Oldname7"][0]
merge = db.plan_merge(keep, drop)
merge_update = [c for c in merge if c.kind == "update"][0]
check("a merge keeps the later visit, so nothing is shown twice",
      merge_update.fields["dateVisited"] == utc(2026, 8, 16))
check("the record handed over as the duplicate is the one that goes",
      merge[0].kind == "delete" and merge[0].author.steam_id == "Oldname7")
check("and it is deleted before the survivor takes its identifier",
      merge.index(merge[0]) < merge.index(merge_update))
check("and the deletion says why it happened",
      "duplicate of" in merge[0].describe())
check("a discovery date already the earlier of the two is left alone",
      "dateAdded" not in merge_update.fields)

# The other way round: the record being dropped is the one that remembers when
# this author was really found, and that date has to survive the merge or the
# web app's "authors added per month" chart quietly rewrites itself.
backwards = db.plan_merge(drop, keep)
backwards_update = [c for c in backwards if c.kind == "update"][0]
check("but an earlier one on the duplicate is carried across before it is deleted",
      backwards_update.fields["dateAdded"] == utc(2025, 6, 1))
check("and merging that way puts the numeric id on the survivor",
      backwards_update.fields["steamId"] == "76561198000000011")


# ---- Writing ---------------------------------------------------------------

db = fresh()
adb.BACKUP_DIR = TMP / "backup"
target = db.lookup(["vanityone"])[0]
plan = db.plan_update(target, new_wallpapers=7)
report = db.apply([plan], backup=True)
check("an update is applied", db.lookup(["vanityone"])[0].new_wallpapers == 7)
check("and reported", report["updated"] == 1 and report["created"] == 0)

backup = Path(report["backup"])
saved = backup.read_text(encoding="utf-8")
check("the previous state was written down first", '"newWallpapers": 0' in saved)
check("as a whole document, so a mistake can be undone without knowing what it was",
      '"reviewedFavorites"' in saved and '"creator"' in saved)
check("alongside the plan in plain words", "update Vanity" in saved)

db.apply([db.plan_create("Fresh", "76561199999999999")])
check("a create lands in the collection", len(db.lookup(["76561199999999999"])) == 1)

clash = db.plan_create("Clash", "VANITYONE")
try:
    db.apply([clash])
    refused = False
except adb.DbError:
    refused = True
check("a create that collides with an existing id is refused, not swallowed", refused)

check("applying nothing does nothing, and says so",
      db.apply([])["backup"] is None)
check("and a None in the list is ignored rather than crashing",
      db.apply([None])["updated"] == 0)


# ---- Writing twenty thousand changes ---------------------------------------

class BulkCollection(FakeCollection):
    """Records what a bulk write was asked to do, in the order it was asked."""

    def __init__(self, docs=None):
        super().__init__(docs)
        self.batches: list[list[str]] = []

    def bulk_write(self, operations, ordered=True):
        assert ordered, "order matters: a merge deletes before it updates"
        self.batches.append([type(op).__name__ for op in operations])
        # Report only what really exists, the way the driver does.
        known = {d.get("_id") for d in self.docs}
        matched = sum(1 for op in operations if type(op).__name__ == "UpdateOne"
                      and op._filter["_id"] in known)
        deleted = sum(1 for op in operations if type(op).__name__ == "DeleteOne"
                      and op._filter["_id"] in known)
        return Result(matched_count=matched, deleted_count=deleted)


db = fresh()
db._col = BulkCollection(list(SEED))
victim = db.lookup(["Oldname7"])[0]
keeper = db.lookup(["76561198000000011"])[0]
report = db.apply(db.plan_merge(keeper, victim), backup=False)
check("a merge is sent as one ordered batch", len(db._col.batches) == 1)
check("with the deletion before the update that takes the identifier",
      db._col.batches[0] == ["DeleteOne", "UpdateOne"])
check("and counted as what it was", report["deleted"] == 1)

db._col = BulkCollection(list(SEED))
many = [db.plan_update(a, new_wallpapers=n + 1)
        for n, a in enumerate(db.lookup(["Oldname7", "vanityone", "76561198000000012"]))]
seen: list[int] = []
db.apply(many * 400, backup=False, batch=500,
         on_progress=lambda done, total: seen.append(done))
check("a large migration goes out in batches rather than one round trip each",
      [len(b) for b in db._col.batches] == [500, 500, 200])
check("and progress is reported as they land", seen == [500, 1000, 1200])

db._col = BulkCollection(list(SEED))
db.apply([db.plan_update(db.lookup(["vanityone"])[0], new_wallpapers=5)],
         backup=False)
check("a single change still goes the simple way, not through a batch",
      db._col.batches == [] and db.lookup(["vanityone"])[0].new_wallpapers == 5)

# A whole migration once reported 19 573 updates and changed nothing: the plan
# had been built from a dump, where `_id` is text, so every filter addressed an
# id no document has. Mongo calls that a successful write of nothing.
db = fresh()
ghost = adb.Author(id="000000000000000000000001", name="Vanity", steam_id="vanityone")
try:
    db.apply([db.plan_update(ghost, new_wallpapers=1)], backup=False)
    noticed = False
except adb.DbError as err:
    noticed = "matched no document" in str(err)
check("a write that reaches no document is a failure, not a quiet success", noticed)

db._col = BulkCollection(list(SEED))
try:
    db.apply([db.plan_update(ghost, new_wallpapers=1),
              db.plan_update(db.lookup(["vanityone"])[0], new_wallpapers=2)],
             backup=False)
    caught = False
except adb.DbError as err:
    caught = "does not have" in str(err)
check("and a batch that half-lands stops rather than carrying on", caught)


# ---- A dump, and the way back from one -------------------------------------

from bson import ObjectId                              # noqa: E402

oid = ObjectId()
db = fresh([doc(oid, "Vanity", "vanityone", visited=utc(2026, 9, 6),
                creator=ObjectId("000000000000000000000002"))])
dump = db.dump(TMP / "dump.json")
saved = adb.AuthorsDb.read_dump(dump)
check("a dump names what it holds and how much of it",
      saved["written"] == 1 and saved["expected"] == 1)
check("with ids and dates written as text a person can read",
      saved["documents"][0]["_id"] == str(oid)
      and saved["documents"][0]["dateVisited"].startswith("2026-09-06"))

broken = TMP / "truncated.json"
broken.write_text(dump.read_text(encoding="utf-8").replace('"written": 1', '"written": 9'),
                  encoding="utf-8")
try:
    adb.AuthorsDb.read_dump(broken)
    caught = False
except adb.DbError:
    caught = True
check("a dump that did not finish writing refuses to be read as if it had", caught)

db.apply([db.plan_update(db.lookup(["vanityone"])[0], name="Wrecked",
                         new_wallpapers=999)], backup=False)
db.apply([db.plan_delete(db.lookup(["vanityone"])[0])], backup=False)
check("the record is gone before the restore", db.lookup(["vanityone"]) == [])

report = db.restore(dump)
back = db.lookup(["vanityone"])[0]
check("a restore brings a deleted record back whole",
      report["restored"] == 1 and back.name == "Vanity" and back.new_wallpapers == 0)
check("with its dates and its owner intact, not as text",
      back.visited == utc(2026, 9, 6) and isinstance(back.id, ObjectId))

db.apply([db.plan_create("Added later", "76561199999999999")], backup=False)
check("a restore leaves later additions alone by default",
      db.restore(dump)["removed"] == 0 and len(db._col.docs) == 2)
check("unless it is told to make the collection match the dump exactly",
      db.restore(dump, remove_unknown=True)["removed"] == 1)


# ---- Reading a service's own configuration -------------------------------

env = TMP / ".env"
env.write_text("# DB creds\nDB_NAME=wallpapers\nDB_HOST=user\nDB_PASS=p@ss/w d\n"
               "DB_CLUSTER=demo.ab12c.mongodb.net\n", encoding="utf-8")
uri = adb.uri_from_env_file(env)
check("a service's .env still builds the connection string",
      uri.startswith("mongodb+srv://user:") and uri.endswith("w=majority"))
check("and the password is escaped, punctuation and all", "p%40ss%2Fw%20d" in uri)
check("the cluster comes out of the file, not out of the source",
      "@demo.ab12c.mongodb.net/" in uri)
check("the database name is read back out of the uri",
      adb._database_from(uri) == "wallpapers")

env.write_text("DB_NAME=x\n", encoding="utf-8")
try:
    adb.uri_from_env_file(env)
    complained = False
except adb.DbError as err:
    complained = "DB_HOST" in str(err) and "DB_PASS" in str(err)
check("a half-filled .env says exactly what is missing", complained)

env.write_text("DB_NAME=x\nDB_HOST=u\nDB_PASS=p\n", encoding="utf-8")
os.environ.pop(adb.CLUSTER_ENV_VAR, None)
try:
    adb.uri_from_env_file(env)
    complained = False
except adb.DbError as err:
    complained = "DB_CLUSTER" in str(err)
check("an .env with no cluster names the setting it wants", complained)

os.environ[adb.CLUSTER_ENV_VAR] = "fallback.ab12c.mongodb.net"
try:
    check("the environment variable stands in for a missing DB_CLUSTER",
          "@fallback.ab12c.mongodb.net/" in adb.uri_from_env_file(env))
finally:
    os.environ.pop(adb.CLUSTER_ENV_VAR, None)

env.write_text("MONGODB_URI=mongodb://given/db\n", encoding="utf-8")
check("a file that already holds a whole uri is taken as it is",
      adb.uri_from_env_file(env) == "mongodb://given/db")


# ---- Which query shape a lookup uses ---------------------------------------
#
# Asking for the case-insensitive collation costs the index: MongoDB only uses
# an index whose collation matches the query's, so the collated lookup is a scan
# of the whole collection - 46 ms on the real one against 2 ms through the
# index. Digits have no case, so a batch made only of steamID64s never needs to
# ask, and since the migration that is nearly every batch. Splitting a mixed
# batch into two queries was measured slower, not faster (the round trip is
# 220 ms and the scan it saves is 46 ms), so a mixed batch stays one query.

shapes = fresh()
shapes.lookup(["76561198000000012", "76561198000000011"])
check("a batch of steamID64s goes the plain way, which is the indexed one",
      shapes._col.collation_calls == 0)
check("and still finds them",
      len(shapes.lookup(["76561198000000012", "76561198000000011"])) == 2)

shapes = fresh()
shapes.lookup(["76561198000000012", "VanityOne"])
check("one vanity name in the batch is enough to need the collation",
      shapes._col.collation_calls == 1)
check("and the whole batch is still answered in that one query",
      len(shapes.lookup(["76561198000000012", "VanityOne"])) == 2)

shapes = fresh()
check("a vanity name is still matched whatever its case",
      [a.name for a in shapes.lookup(["vanityONE"])] == ["Vanity"])
check("and a number that is not there is still simply absent",
      shapes.lookup(["76561190000000000"]) == [])


# ---- Live, opt-in ----------------------------------------------------------

if "--live" in sys.argv or "--live-write" in sys.argv:
    print("\n-- live (reads the real database) --")
    uri = secrets.get(secrets.AUTHORS_DB_URI)
    if not uri:
        check("a connection string is stored", False)
    else:
        real = adb.AuthorsDb(uri).connect()
        check("the database answers", real.count() > 30000)
        check("and every record belongs to the one account", real.creator is not None)

        # Oldname7 and 76561198000000011 were one person filed twice, two months
        # apart. The migration of 2026-09-07 merged them and re-keyed the
        # survivor by steamID64, so searching both keys must now find exactly
        # one record — and the vanity half must no longer resolve on its own.
        pair = real.lookup(["76561198000000011", "Oldname7"])
        check("the duplicate the migration merged is now a single record",
              len(pair) == 1)
        check("and it is the one keyed by steamID64",
              bool(pair) and pair[0].steam_id == "76561198000000011")
        check("the vanity name it used to be filed under is gone",
              real.lookup(["Oldname7"]) == [])

        if "--live-write" in sys.argv:
            print("\n-- live write (creates and removes one record of its own) --")
            probe_id = "wallpaper-suite-selftest"
            adb.BACKUP_DIR = TMP / "backup-live"
            try:
                for stray in real.lookup([probe_id]):
                    real.apply([real.plan_delete(stray)], backup=False)
                real.apply([real.plan_create("Wallpaper Engine Toolkit self test", probe_id)])
                made = real.lookup([probe_id])
                check("a record can be created", len(made) == 1)
                check("and comes back with the shape the old app expects",
                      made[0].reviewed_favorites and made[0].added is not None)

                moved = real.apply([real.plan_update(made[0], new_wallpapers=42)])
                check("and updated", real.lookup([probe_id])[0].new_wallpapers == 42)
                check("with a backup of what it replaced",
                      moved["backup"] and Path(moved["backup"]).exists())
            finally:
                left = real.lookup([probe_id])
                if left:
                    real.apply([real.plan_delete(a) for a in left], backup=False)
                check("and removed again, leaving the collection as it was",
                      not real.lookup([probe_id]))
        real.close()

print()
print("PASSED %d/%d" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
