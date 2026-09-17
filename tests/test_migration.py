"""The identifier migration, checked on records that never leave this process.

Run it directly — there is no test framework in this project:

    .venv\\Scripts\\python.exe tests\\test_migration.py

Steam is replaced by a dictionary and the database by an object that plans
writes but has nowhere to send them, so nothing here reaches the network or the
collection. What is being checked is the judgement: which evidence wins, what
counts as a duplicate, which of a duplicate pair survives, and what has to be
carried across before the other one is deleted.
"""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.engines.migration as mig               # noqa: E402
from app.engines.authors_db import Author, AuthorsDb  # noqa: E402
from app.engines.steam_api import ItemDetails, Profile  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="wallpaper_migration_test_"))

results: list[bool] = []


def check(label: str, condition: bool) -> None:
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label)


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


LINK = "https://steamcommunity.com/sharedfiles/filedetails/?id="

ALICE = "76561198000000001"
BOB = "76561198000000002"
CAROL = "76561198000000003"


class FakeSteam:
    """Steam as two lookup tables."""

    def __init__(self, creators: dict[str, str], vanities: dict[str, str],
                 names: dict[str, str] | None = None):
        self.creators = creators
        self.vanities = vanities
        self.names = names or {}
        self.item_calls = 0
        self.name_calls = 0

    def details(self, ids, on_progress=None):
        self.item_calls += 1
        return {i: ItemDetails(id=i, ok=i in self.creators,
                               creator=self.creators.get(i)) for i in ids}

    def resolve_vanities(self, names, on_progress=None):
        self.name_calls += 1
        return {n: self.vanities.get(n) for n in names}

    def profiles(self, keys, on_progress=None):
        self.name_calls += 1
        out = {}
        for key in keys:
            if key in self.vanities:
                out[key] = Profile(id64=self.vanities[key], name=self.names.get(key, ""))
            elif key in self.names:
                out[key] = Profile(id64=key, name=self.names[key])
            else:
                out[key] = Profile(id64=None, exists=False)
        return out


def author(oid, name, steam_id, item=None, visited=None, added=None,
           favorite=False, reviewed=True, counted=0):
    return Author(id=oid, name=name, steam_id=steam_id,
                  added=added or utc(2024, 1, 1), visited=visited,
                  favorite=favorite, new_wallpapers=counted,
                  reference_link=(LINK + item) if item else None,
                  reviewed_favorites=reviewed)


def run(records, steam, on_disagreement=mig.PREFER_ITEM, stale_days=365, **kwargs):
    db = AuthorsDb(uri="mongodb://x/y", database="y", creator="owner")
    return mig.Migration(db, steam, on_disagreement=on_disagreement,
                         stale_days=stale_days).analyse(records=records, **kwargs)


# ---- Reading the old app's link --------------------------------------------

check("a referenceLink gives up the wallpaper it points at",
      mig.item_in(LINK + "2594375030") == "2594375030")
check("and anything else gives up nothing",
      mig.item_in(None) is None and mig.item_in("https://steamcommunity.com/id/x/") is None)


# ---- Which evidence decides ------------------------------------------------

steam = FakeSteam(creators={"100": ALICE, "200": BOB}, vanities={"alice": ALICE})
plan = run([
    author(1, "Alice", "alice", item="100"),          # a wallpaper only it claims
    author(2, "Bob", BOB, item="101"),                # an id whose link disagrees
    author(3, "Nameless", "ghost"),                   # nothing to go on
    author(4, "Carol", "carol-new"),                  # only the name resolves
], FakeSteam(creators={"100": ALICE, "101": ALICE},
             vanities={"alice": ALICE, "carol-new": CAROL}))

found = {i.record.name: i for i in plan.identities}
check("a vanity record is identified by the wallpaper it points at",
      found["Alice"].resolved == ALICE and found["Alice"].source == mig.FROM_ITEM)
check("a record already filed under an account id keeps it",
      found["Bob"].resolved == BOB and found["Bob"].source == mig.FROM_RECORD)
check("even when its link points at somebody else — but that is recorded",
      found["Bob"].conflict and found["Bob"].by_item == ALICE)
check("a record with no link falls back to resolving its name",
      found["Carol"].resolved == CAROL and found["Carol"].source == mig.FROM_VANITY)
check("and one with neither is left alone rather than guessed at",
      not found["Nameless"].settled and found["Nameless"] in plan.unresolved)

# The case that made the wallpaper the primary evidence: a vanity name that was
# released and picked up by a different account.
handover = FakeSteam(creators={"100": ALICE}, vanities={"zenith22": BOB})
taken_over = run([author(1, "Zenith", "zenith22", item="100")], handover,
                 cross_check=True)
identity = taken_over.identities[0]
check("when the wallpaper and the name name different people, the wallpaper wins",
      identity.resolved == ALICE and identity.by_vanity == BOB)
check("and the disagreement is counted rather than buried",
      identity.conflict and identity in taken_over.conflicts)

careful = run([author(1, "Zenith", "zenith22", item="100")],
              FakeSteam(creators={"100": ALICE}, vanities={"zenith22": BOB}),
              on_disagreement=mig.SKIP, cross_check=True)
check("and a run told to be careful decides nothing instead",
      careful.identities[0].disputed and not careful.changes)
check("a record where both sources agree is migrated, and says so",
      run([author(1, "A", "alice", item="100")],
          FakeSteam(creators={"100": ALICE}, vanities={"alice": ALICE}),
          cross_check=True).identities[0].source == mig.FROM_BOTH)

cheap = FakeSteam(creators={"100": ALICE}, vanities={"zenith22": BOB})
quiet_run = run([author(1, "Zenith", "zenith22", item="100")], cheap)
check("but checking for that is opt-in, because it costs a request per record",
      cheap.name_calls == 0 and quiet_run.identities[0].resolved == ALICE)


# ---- A link several records claim is no link at all -------------------------

# Item 2428128941 is the referenceLink of five unrelated authors in the real
# collection. Believing it merges them into one record and deletes four.
crowded = FakeSteam(creators={"100": ALICE, "200": BOB}, vanities={"c": CAROL})
shared = run([author(1, "a", "a", item="100"), author(2, "b", "b", item="100"),
              author(3, "c", "c", item="100")], crowded)
check("a wallpaper claimed by several records identifies none of them",
      all(i.shared_item and not i.by_item for i in shared.identities))
check("so they are not merged into one author",
      shared.counts["merge_groups"] == 0)
check("and fall back to their names, where those still work",
      shared.counts["from_vanity"] == 1 and shared.counts["unresolved"] == 2)
check("a wallpaper only one record claims is still good evidence",
      run([author(1, "a", "a", item="100"), author(2, "b", "b", item="200")],
          FakeSteam(creators={"100": ALICE, "200": BOB}, vanities={})
          ).counts["from_item"] == 2)


# ---- Only paying for the names that need it --------------------------------

steam = FakeSteam(creators={"100": ALICE}, vanities={"ghost": CAROL})
run([author(1, "A", "alice", item="100"), author(2, "B", "ghost")], steam)
check("names are only resolved for records no wallpaper could identify",
      steam.name_calls == 1)

quiet = FakeSteam(creators={"100": ALICE}, vanities={})
run([author(1, "A", "alice", item="100")], quiet)
check("and not at all when every record was identified by a wallpaper",
      quiet.name_calls == 0)


# ---- Duplicates ------------------------------------------------------------

pair = run([
    # The copy that is about to be deleted is the one carrying a favourite
    # mark and an outstanding review; both have to survive it.
    author(1, "Oldname7", "Oldname7", item="100", visited=utc(2026, 8, 16),
           added=utc(2026, 8, 9), favorite=True, reviewed=False),
    author(2, "Oldname", ALICE, visited=utc(2026, 3, 15), added=utc(2025, 6, 1)),
], FakeSteam(creators={"100": ALICE}, vanities={}))

check("two records resolving to one author are one merge",
      pair.counts["merge_groups"] == 1 and pair.counts["merge_deletes"] == 1)

deletes = [c for c in pair.changes if c.kind == "delete"]
updates = [c for c in pair.changes if c.kind == "update"]
check("the record already holding the account id is the one that survives",
      deletes[0].author.steam_id == "Oldname7" and updates[0].author.steam_id == ALICE)
check("so nothing has to rewrite an identifier another record still holds",
      "steamId" not in updates[0].fields)
check("the deletion is planned before the update that depends on it",
      pair.changes.index(deletes[0]) < pair.changes.index(updates[0]))
check("the later visit is kept, so nothing already reviewed is shown again",
      updates[0].fields["dateVisited"] == utc(2026, 8, 16))
check("a discovery date already the earliest of the group is not rewritten",
      "dateAdded" not in updates[0].fields)
check("a favourite mark on either copy survives the merge",
      updates[0].fields["favorite"] is True)
check("and so does a review still outstanding on either copy",
      updates[0].fields["reviewedFavorites"] is False)
check("the merge is listed with the survivor first",
      pair.merges[0][0].steam_id == ALICE)

# The other way round: the copy about to be deleted is the one that remembers
# when this author was first found, and that date has to move across first.
older_duplicate = run([
    author(1, "Oldname7", "Oldname7", item="100", added=utc(2021, 3, 4),
           visited=utc(2026, 8, 16)),
    author(2, "Oldname", ALICE, added=utc(2025, 6, 1), visited=utc(2026, 3, 15)),
], FakeSteam(creators={"100": ALICE}, vanities={}))
carried = [c for c in older_duplicate.changes if c.kind == "update"][0]
check("an earlier discovery date on the doomed copy is carried across",
      carried.fields["dateAdded"] == utc(2021, 3, 4))

orphans = run([
    author(1, "One", "one", item="100", added=utc(2022, 5, 1), visited=utc(2025, 1, 1)),
    author(2, "Two", "two", item="200", added=utc(2023, 5, 1), visited=utc(2026, 1, 1)),
], FakeSteam(creators={"100": ALICE, "200": ALICE}, vanities={}))
survivor = [c for c in orphans.changes if c.kind == "update"][0]
check("a copy nobody has visited in a year is not the one that survives",
      survivor.author.name == "Two")
check("and it is the one that takes the account id",
      survivor.fields["steamId"] == ALICE)

# A copy nobody has opened in a year is dropped rather than merged: its visit
# date is older than the survivor's by definition, and reviving a favourite
# mark or an unfinished review from it would undo work already done.
stale = run([
    author(1, "current", ALICE, visited=utc(2026, 6, 1)),
    author(2, "abandoned", "old-name", item="100", visited=utc(2023, 1, 1),
           favorite=True, reviewed=False, counted=99),
], FakeSteam(creators={"100": ALICE}, vanities={}))
gone = [c for c in stale.changes if c.kind == "delete"][0]
kept = [c for c in stale.changes if c.kind == "update"]
check("a duplicate not visited for over a year is simply deleted",
      gone.author.name == "abandoned" and "stale duplicate" in gone.describe())
check("and nothing of it is carried onto the survivor", kept == [])

recent = run([
    author(1, "current", ALICE, visited=utc(2026, 6, 1)),
    author(2, "also current", "other-name", item="100", visited=utc(2026, 7, 1),
           favorite=True),
], FakeSteam(creators={"100": ALICE}, vanities={}))
merged = [c for c in recent.changes if c.kind == "update"][0]
check("but a duplicate still in use is merged, not thrown away",
      merged.fields["dateVisited"] == utc(2026, 7, 1)
      and merged.fields["favorite"] is True)

three = run([
    author(1, "a", "a", item="100", visited=utc(2024, 1, 1)),
    author(2, "b", "b", item="101", visited=utc(2026, 1, 1)),
    author(3, "c", ALICE, visited=utc(2025, 1, 1)),
], FakeSteam(creators={"100": ALICE, "101": ALICE}, vanities={}))
check("three records for one author collapse to one",
      three.counts["merge_deletes"] == 2 and three.counts["merge_groups"] == 1)
check("keeping the latest visit of all of them",
      [c for c in three.changes if c.kind == "update"][0]
      .fields["dateVisited"] == utc(2026, 1, 1))


# ---- Plain rewrites --------------------------------------------------------

simple = run([author(1, "Alice", "alice", item="100"),
              author(2, "Bob", BOB, item="200")],
             FakeSteam(creators={"100": ALICE, "200": BOB}, vanities={}))
check("a lone vanity record is simply refiled under its account id",
      simple.counts["rewrites"] == 1 and simple.counts["merge_groups"] == 0)
check("and a record that is already right is not written to at all",
      len(simple.changes) == 1)
check("the change says what it does in words",
      "steamId: alice → " + ALICE in simple.changes[0].describe())


# ---- Refreshing names ------------------------------------------------------

named = run([author(1, "old name", "alice", item="100")],
            FakeSteam(creators={"100": ALICE}, vanities={},
                      names={ALICE: "Current Name"}),
            refresh_names=True)
check("names are only refreshed when asked for",
      named.counts["renames"] == 1
      and named.changes[0].fields["name"] == "Current Name")

unnamed = run([author(1, "old name", "alice", item="100")],
              FakeSteam(creators={"100": ALICE}, vanities={},
                        names={ALICE: "Current Name"}))
check("and left alone otherwise", unnamed.counts["renames"] == 0)


# ---- Nothing may collide with the unique index ------------------------------

check("a plan that refiles records under account ids collides with nothing",
      simple.collisions() == [] and pair.collisions() == [] and three.collisions() == [])

# Hand-built: a record taking an identifier a surviving record still holds. The
# analysis cannot produce this, which is exactly why it is worth checking for.
bad = run([author(1, "a", "a", item="100")],
          FakeSteam(creators={"100": ALICE}, vanities={}))
bad.identities.append(mig.Identity(record=author(9, "squatter", ALICE)))
check("but one that did would be caught before a single write went out",
      len(bad.collisions()) == 1 and "cannot take" in bad.collisions()[0])


# ---- The written plan ------------------------------------------------------

report = pair.write(TMP / "plan.json")
text = report.read_text(encoding="utf-8")
check("the plan is written out before anything is applied",
      '"counts"' in text and "delete Oldname7" in text)
check("with the disagreements a person should look at",
      '"conflicts"' in text and '"unresolved"' in text)
check("and a summary that reads as sentences",
      "duplicate groups to merge 1" in pair.summary())


# ---- Reading a dump --------------------------------------------------------

from bson import ObjectId                                # noqa: E402

dump = TMP / "dump.json"
first_id = ObjectId()
dump.write_text('{"expected": 1, "written": 1, "documents": ['
                '{"_id": "' + str(first_id) + '", "name": "A", "steamId": "alice",'
                ' "dateAdded": "2024-01-01T00:00:00", "dateVisited": null,'
                ' "referenceLink": "' + LINK + '100"}]}', encoding="utf-8")
from_dump = mig.Migration(AuthorsDb(uri="mongodb://x/y", database="y"),
                          FakeSteam(creators={"100": ALICE}, vanities={})
                          ).analyse(dump=dump)
check("an analysis can run off a dump instead of the live collection",
      from_dump.counts["records"] == 1 and from_dump.identities[0].resolved == ALICE)
check("and dates in a dump come back as dates, not text",
      from_dump.identities[0].record.added == utc(2024, 1, 1))

# The id is what every update and delete in the plan is addressed to. Left as
# the text the dump stores it as, the whole migration matches no documents at
# all — and reports success, because Mongo does not call that an error.
real_id = ObjectId()
dump.write_text('{"expected": 1, "written": 1, "documents": ['
                '{"_id": "' + str(real_id) + '", "name": "A", "steamId": "alice",'
                ' "dateAdded": "2024-01-01T00:00:00", "dateVisited": null,'
                ' "referenceLink": "' + LINK + '100"}]}', encoding="utf-8")
ids = mig.Migration(AuthorsDb(uri="mongodb://x/y", database="y"),
                    FakeSteam(creators={"100": ALICE}, vanities={})
                    ).analyse(dump=dump)
check("and an id in a dump comes back as an id the database can be addressed by",
      ids.identities[0].record.id == real_id
      and isinstance(ids.identities[0].record.id, ObjectId))
check("so the changes built from it point at real documents",
      ids.changes[0].author.id == real_id)

print()
print("PASSED %d/%d" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
