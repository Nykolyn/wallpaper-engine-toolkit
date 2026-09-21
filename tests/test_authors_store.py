"""The authors database as a local SQLite file: reading, writing, the trail.

Run it directly — there is no test framework in this project:

    .venv\\Scripts\\python.exe tests\\test_authors_store.py

Everything happens in a temporary directory; nothing here touches the real
`data/authors.sqlite`.
"""
from __future__ import annotations

import gzip
import json
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.engines.authors_store as st  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="wallpaper_authors_store_test_"))

results: list[bool] = []


def check(label: str, condition: bool) -> None:
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label)


def raises(error, work) -> bool:
    try:
        work()
    except error:
        return True
    return False


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def fresh(name: str, mirror: Path | None = None) -> st.AuthorsStore:
    folder = TMP / name
    return st.AuthorsStore(folder / "authors.sqlite", mirror=mirror).open()


ALICE = "76561198000000001"
BOB = "76561198000000002"


# ---- Opening ---------------------------------------------------------------

db = fresh("basic")
check("the file is made on first use, with nothing to set up",
      db.path.exists() and db.count() == 0)
with sqlite3.connect(db.path) as raw:
    check("and carries its schema version",
          raw.execute("PRAGMA user_version").fetchone()[0] == st.SCHEMA_VERSION)
    columns = [r[1] for r in raw.execute("PRAGMA table_info(authors)")]
check("with exactly the four columns the review uses",
      columns == ["key", "name", "added", "visited"])
check("an empty table has nothing to back up", db.snapshot() is None)

# ---- Writing and reading back ----------------------------------------------

report = db.apply([db.plan_create("Alice", ALICE, visited=utc(2026, 9, 1, 12)),
                   db.plan_create("Vanity", "VanityOne", visited=None,
                                  added=utc(2020, 1, 1))])
check("a create lands", report["created"] == 2 and db.count() == 2)
check("a vanity name is found whatever its case",
      [a.name for a in db.lookup(["vanityone"])] == ["Vanity"]
      and [a.name for a in db.lookup(["VANITYONE"])] == ["Vanity"])
check("an account number is found as itself",
      db.lookup([ALICE])[0].visited == utc(2026, 9, 1, 12))
check("nobody is found for a key nobody is filed under", db.lookup([BOB]) == [])

both = db.lookup_many({"alice": [ALICE, "alice"], "vanity": ["vanityone", "VanityOne"],
                       "nobody": [BOB]})
check("one record answering to two keys is one record, not a duplicate",
      len(both["vanity"]) == 1 and len(both["alice"]) == 1 and both["nobody"] == [])

check("a second author under the same key in another case is refused",
      raises(st.DbError, lambda: db.apply([db.plan_create("Clash", "VANITYONE")])))

# Naive means local, as it does everywhere else in the suite. Read the other
# way round, a stored time is three hours out — silently.
alice = db.lookup([ALICE])[0]
local_noon = datetime(2026, 9, 10, 12, 0, 0)
change = db.plan_update(alice, visited=local_noon)
db.apply([change])
back = db.lookup([ALICE])[0].visited
check("a local time goes in and comes back as the same instant",
      back == local_noon.astimezone(timezone.utc))
with sqlite3.connect(db.path) as raw:
    stored = raw.execute("SELECT visited FROM authors WHERE key = ?", (ALICE,)).fetchone()[0]
check("and is stored as UTC with the zone written on it", stored.endswith("Z"))

alice = db.lookup([ALICE])[0]
check("an update that changes nothing is not planned at all",
      db.plan_update(alice, visited=back, name="Alice") is None)
check("a fraction of a second is not a change either",
      db.plan_update(alice, visited=back + timedelta(microseconds=300)) is None)

# ---- All or nothing --------------------------------------------------------

ghost = st.Author(steam_id="76561190000000000", name="Ghost")
before = db.count()
failed = raises(st.DbError, lambda: db.apply([
    db.plan_create("Bob", BOB),
    db.plan_update(ghost, name="Still a ghost"),
]))
check("a write that reaches nobody stops the batch", failed)
check("and the create before it in the same batch is rolled back",
      db.count() == before and db.lookup([BOB]) == [])

# ---- Merging duplicates ----------------------------------------------------

db.apply([db.plan_create("Oldname", "Oldname7", visited=utc(2026, 8, 1),
                         added=utc(2019, 5, 5)),
          db.plan_create("Newname", "76561198000000011", visited=utc(2026, 6, 1),
                         added=utc(2023, 1, 1))])
keep = db.lookup(["76561198000000011"])[0]
drop = db.lookup(["oldname7"])[0]
merge = db.plan_merge(keep, drop)
check("a merge deletes the duplicate first", merge[0].kind == "delete")
db.apply(merge)
merged = db.lookup(["76561198000000011", "Oldname7"])
check("one record is left", len(merged) == 1)
check("with the later visit and the earlier discovery",
      merged[0].visited == utc(2026, 8, 1) and merged[0].added == utc(2019, 5, 5))

backwards = db.plan_merge(db.lookup(["vanityone"])[0], db.lookup([ALICE])[0])
check("merging into a vanity record takes the account number over",
      backwards[-1].fields.get("key") == ALICE)

# ---- The trail -------------------------------------------------------------

journal = db.backup_dir / st.JOURNAL_NAME
lines = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
check("every write leaves a journal line, and a refused one none", len(lines) == 4)
last = lines[-1]["changes"]
check("which holds each touched row as it was and as it became",
      last[0]["kind"] == "delete" and last[0]["before"]["key"] == "Oldname7"
      and last[1]["after"]["visited"] == "2026-08-01T00:00:00Z")

snapshots = db.snapshots()
check("every write leaves a snapshot, named for its date and its count",
      len(snapshots) >= 1 and snapshots[0].count == db.count()
      and snapshots[0].where == "data")
data = st.read_snapshot(snapshots[0].path)
check("which reads back whole", data["count"] == db.count()
      and {r["key"] for r in data["authors"]} == {r["key"] for r in db.rows()})
with gzip.open(snapshots[0].path, "rt", encoding="utf-8") as src:
    raw_text = src.read()
check("and is plain JSON once unzipped, one author to a line",
      json.loads(raw_text)["format"] == st.SNAPSHOT_FORMAT
      and raw_text.count("\n") >= db.count())
check("no second folder is written to when none is set",
      db.mirror is None and not (TMP / "basic" / "mirror").exists())

truncated = TMP / "truncated.json.gz"
with gzip.open(truncated, "wt", encoding="utf-8") as out:
    json.dump({"format": st.SNAPSHOT_FORMAT, "version": 1, "count": 5,
               "authors": [{"key": "a"}]}, out)
check("a snapshot holding fewer authors than it says is refused",
      raises(st.DbError, lambda: st.read_snapshot(truncated)))
junk = TMP / "junk.json.gz"
junk.write_bytes(b"not gzip at all")
check("and so is one that is not a snapshot at all",
      raises(st.DbError, lambda: st.read_snapshot(junk)))

# ---- Restoring -------------------------------------------------------------

good = db.snapshots()[0]
kept = {r["key"]: r for r in db.rows()}
db.apply([db.plan_update(db.lookup([ALICE])[0], name="Wrecked"),
          db.plan_delete(db.lookup(["vanityone"])[0])])
check("the damage is there before the restore",
      db.lookup(["vanityone"]) == [] and db.lookup([ALICE])[0].name == "Wrecked")
report = db.restore(good.path)
check("a restore puts every row back as it was",
      {r["key"]: r for r in db.rows()} == kept)
check("after saving what it replaced, so it can itself be undone",
      report["previous"] is not None and Path(report["previous"]).exists()
      and st.read_snapshot(report["previous"])["count"] == len(kept) - 1)

# ---- A backup that cannot be written ---------------------------------------

# The write has happened by the time its backup is taken. Raising then would
# tell the window nothing changed, and the next Update would plan the same
# creations again.
jammed = TMP / "jammed"
jammed.mkdir()
(jammed / "not a folder").write_text("x", encoding="utf-8")
stuck = st.AuthorsStore(jammed / "authors.sqlite",
                        backup_dir=jammed / "not a folder" / "backups").open()
written = stuck.apply([stuck.plan_create("Alice", ALICE)])
check("a backup that fails after the write does not undo or deny the write",
      written["created"] == 1 and stuck.count() == 1 and written["backup"] is None)
check("it is reported as a warning instead",
      any("backup failed" in w for w in stuck.warnings))
stuck.close()

# ---- The second folder -----------------------------------------------------

mirror = TMP / "mirror"
mirrored = fresh("mirrored", mirror=mirror)
mirrored.apply([mirrored.plan_create("Alice", ALICE)])
copies = st.list_snapshots(mirror, "second folder")
check("with a second folder set, every snapshot is copied there too",
      len(copies) == 1 and copies[0].path.name == mirrored.snapshots()[0].path.name
      and st.read_snapshot(copies[0].path)["count"] == 1)
check("and both are listed", {s.where for s in mirrored.snapshots()}
      == {"data", "second folder"})

blocked = TMP / "a file, not a folder"
blocked.write_text("x", encoding="utf-8")
unreachable = fresh("unreachable", mirror=blocked / "backups")
result = unreachable.apply([unreachable.plan_create("Alice", ALICE)])
check("a second folder that cannot be reached does not stop the write",
      result["created"] == 1 and unreachable.count() == 1)
check("it is reported instead", len(unreachable.warnings) == 1
      and "not copied" in unreachable.warnings[0])
check("and the first copy is still made", len(unreachable.snapshots()) == 1)

# ---- Pruning ---------------------------------------------------------------

now = datetime(2026, 9, 21, 12, 0)
history = [st.Snapshot(path=Path(f"s{h}"), taken=now - timedelta(hours=h), count=1,
                       where="data")
           for h in range(0, 24 * 400, 6)]           # four a day for 400 days
keep = st.keep_set(history)
days = {s.taken.date() for s in history if s.path in keep}
check("the newest is always kept", history[0].path in keep)
check("so are the last ten, whatever day they fell on",
      all(s.path in keep for s in history[:st.KEEP_RECENT]))
check("then one a day for a week",
      all((now - timedelta(days=d)).date() in days for d in range(st.KEEP_DAILY)))
check("then one a month for a year", len({(d.year, d.month) for d in days}) == st.KEEP_MONTHLY)
check("and nothing older than that", min(days) > (now - timedelta(days=366)).date())
check("which is a few dozen files, not sixteen hundred", len(keep) <= 40)

folder = TMP / "prune"
folder.mkdir()
for h in range(0, 24 * 30, 6):
    when = now - timedelta(hours=h)
    (folder / f"authors-{when:%Y%m%d-%H%M%S}-1.json.gz").write_bytes(b"")
(folder / "notes.txt").write_text("mine", encoding="utf-8")
(folder / st.JOURNAL_NAME).write_text("{}\n", encoding="utf-8")
removed = st.prune(folder)
check("pruning deletes what no rule keeps", len(removed) > 0)
check("and never a file it did not name",
      (folder / "notes.txt").exists() and (folder / st.JOURNAL_NAME).exists())

# ---- A file that is not what it should be ----------------------------------

broken = TMP / "broken" / "authors.sqlite"
broken.parent.mkdir()
broken.write_bytes(b"this is not a database, and it is long enough to be read " * 50)
check("a damaged file is reported as damaged, not as a connection problem",
      raises(st.StoreDamaged, lambda: st.AuthorsStore(broken).open()))

newer = TMP / "newer" / "authors.sqlite"
newer.parent.mkdir()
with sqlite3.connect(newer) as raw:
    raw.execute(st._SCHEMA)
    raw.execute(f"PRAGMA user_version={st.SCHEMA_VERSION + 1}")
check("a file from a newer toolkit is refused rather than half-read",
      raises(st.DbError, lambda: st.AuthorsStore(newer).open()))

first = fresh("first-open")
first.apply([first.plan_create("Alice", ALICE)])
for s in first.snapshots():
    s.path.unlink()
check("a database with no backup at all gets one when it is opened",
      first.ensure_snapshot() is not None and len(first.snapshots()) == 1)
check("and only then", first.ensure_snapshot() is None)

# ---- Replacing the whole table ---------------------------------------------

rows = [
    {"key": ALICE, "name": "Alice", "added": "2019-06-26T00:00:00Z",
     "visited": "2023-08-23T17:49:11Z"},
    {"key": "O0P", "name": "Vanity", "added": "2020-01-01T00:00:00Z",
     "visited": "2024-01-01T00:00:00Z"},
    {"key": "NeverSeen", "name": "Quiet", "added": "2020-01-01T00:00:00Z",
     "visited": None},
]
replaced = fresh("replaced")
report = replaced.replace_all(rows, reason="test")
check("a whole table goes in at once", report["written"] == 3 and replaced.count() == 3
      and report["previous"] is None and report["backup"] is not None)
check("and reads back as the review will see it",
      replaced.lookup(["o0p"])[0].visited == utc(2024, 1, 1)
      and replaced.lookup(["NeverSeen"])[0].visited is None)
check("two rows under one key, in any case, are refused",
      raises(st.DbError, lambda: replaced.replace_all(
          rows + [dict(rows[1], key="o0p")], "x")))
check("leaving what was there untouched", replaced.count() == 3)

for store in (db, mirrored, unreachable, first, replaced):
    store.close()

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
