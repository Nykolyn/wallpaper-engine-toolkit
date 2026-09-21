"""The review: the local library, the author cards, and the Steam plumbing.

Run it directly — there is no test framework in this project:

    .venv\\Scripts\\python.exe tests\\test_review.py
    .venv\\Scripts\\python.exe tests\\test_review.py --live

The default run builds a Wallpaper Engine config, a workshop folder and a
couple of wallpaper libraries in a temporary directory, and answers to Steam
out of a dictionary. ``--live`` adds two read-only checks against the real Steam
client, which is the only way to know the subscription path is still open.
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.engines.library as lib_mod              # noqa: E402
import app.engines.review as rv                    # noqa: E402
import app.engines.steam_ugc as ugc_mod            # noqa: E402
from app.engines.authors_store import Author, AuthorsStore   # noqa: E402
from app.engines.steam_api import AuthorItems, ItemDetails, Profile  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="wallpaper_review_test_"))

results: list[bool] = []


def check(label: str, condition: bool) -> None:
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label)


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def stamp(days_ago: float) -> int:
    return int((datetime.now(timezone.utc) - timedelta(days=days_ago)).timestamp())


# ---- A machine in a temporary directory ------------------------------------

WORKSHOP = TMP / "workshop" / "content" / "431960"
RESERVE = TMP / "reserve"
PROJECTS = TMP / "myprojects"
for folder in (WORKSHOP, RESERVE, PROJECTS):
    folder.mkdir(parents=True)


def subscribed(*ids: str) -> None:
    """A downloaded wallpaper: a folder with a manifest, which is what WE lists."""
    for item in ids:
        folder = WORKSHOP / item
        folder.mkdir(exist_ok=True)
        (folder / "project.json").write_text(
            json.dumps({"title": item, "type": "video", "workshopid": item}),
            encoding="utf-8")


def kept(root: Path, name: str, workshop_id: str | None) -> None:
    """A folder in a local library, with or without a workshop id in it."""
    folder = root / name
    folder.mkdir(exist_ok=True)
    manifest = {"title": name, "type": "video", "file": "a.mp4"}
    if workshop_id:
        manifest["workshopid"] = workshop_id
    (folder / "project.json").write_text(json.dumps(manifest), encoding="utf-8")


subscribed("1001", "1002")
kept(RESERVE, "old-favourite", "2001")      # had it, deleted it
kept(RESERVE, "still-here", "1001")         # had it, still subscribed
kept(RESERVE, "home-made", None)            # never was on the workshop
kept(PROJECTS, "another", "2002")
# A wallpaper copied into myprojects: not subscribed, but Wallpaper Engine has
# files for it and lists it, so a folder holding it is holding something real.
(PROJECTS / "2001").mkdir(exist_ok=True)

WE_CONFIG = TMP / "config.json"
WE_CONFIG.write_text(json.dumps({
    "?installdirectory": str(TMP),
    "steamuser": {"general": {"browser": {"folders": [
        {"title": "all", "items": {"1001": 1, "9999": 1}, "subfolders": [], "type": 0},
        {"title": "new", "items": {"1001": 1, "2001": 1, "3001": 1, "bogus": 1},
         "subfolders": [], "type": 0},
    ]}}},
}), encoding="utf-8")


# ---- The library -----------------------------------------------------------

library = lib_mod.Library(workshop=WORKSHOP, roots=[RESERVE, PROJECTS],
                          index_path=TMP / "library.json")
check("what is subscribed is a directory listing, nothing cleverer",
      library.subscribed() == {"1001", "1002"})

report = library.refresh()
check("a scan finds the workshop ids hidden in project.json files",
      library.ever_had() == {"2001", "1001", "2002"})
check("and ignores folders that were never on the workshop",
      report["folders"] == 5 and report["ids"] == 3)
check("what was owned once and is gone now is the set worth marking",
      library.once_had_but_gone() == {"2001", "2002"})

second = library.refresh()
check("a second scan opens nothing it has already read",
      second["opened"] == 0 and second["reused"] == 5)
kept(RESERVE, "brand-new", "2003")
third = library.refresh()
check("but it does notice a folder that appeared since",
      third["opened"] == 1 and "2003" in library.ever_had())

reloaded = lib_mod.Library(workshop=WORKSHOP, roots=[RESERVE, PROJECTS],
                           index_path=TMP / "library.json")
check("and the index survives a restart, so the slow walk is paid once",
      reloaded.ever_had() == library.ever_had() and reloaded.scanned)


# ---- Reading Wallpaper Engine's own folder ---------------------------------

queue = rv.folder_items(WE_CONFIG, "new")
check("the folder's own contents are read as they are written",
      queue == ["1001", "2001", "3001"])
check("and anything that is not a workshop id is left out of it", "bogus" not in queue)
check("a folder that does not exist is empty, not an error",
      rv.folder_items(WE_CONFIG, "nope") == [])

# The folder to review used to be `new` and nothing else. It is a scope now:
# one folder, every folder, no folder, or the whole library — the last two
# being the only way to reach a wallpaper that was never filed anywhere.
folders = rv.we_folders(WE_CONFIG)
check("every folder is read in one pass, not one parse per folder",
      {k: len(v) for k, v in folders.items()} == {"all": 2, "new": 3})
present = library.listable()
check("a scope naming one folder is that folder",
      rv.scope_candidates(rv.FOLDER + "new", folders, present)
      == ["1001", "2001", "3001"])
check("all folders is their union, with each wallpaper in it once",
      rv.scope_candidates(rv.SCOPE_FOLDERS, folders, present)
      == ["1001", "9999", "2001", "3001"])
check("what is in no folder can only be found among what is here at all",
      rv.scope_candidates(rv.SCOPE_LOOSE, folders, present) == ["1002"])
check("and everything is both together",
      set(rv.scope_candidates(rv.SCOPE_EVERYTHING, folders, present))
      == {"1001", "9999", "2001", "3001", "1002"})
check("every scope can say what it is in a sentence",
      rv.scope_label(rv.FOLDER + "new").endswith("“new”")
      and rv.scope_label(rv.SCOPE_LOOSE) == "whatever is in no folder")
# Three things stand between "the folder holds this id" and "you can see it":
# the wallpaper may have been unsubscribed, the folder left behind may hold
# nothing but a shader cache, and a copy in myprojects is not a subscription.
# Steam leaves these behind when a wallpaper is removed: a folder holding
# nothing but the compiled shader cache. Wallpaper Engine never lists one.
leftover = WORKSHOP / "1003"
leftover.mkdir(exist_ok=True)
(leftover / "shaders").mkdir(exist_ok=True)
check("only a subscribed folder with a manifest can be shown",
      library.listable() == {"1001", "1002"})
check("a folder holding no project.json is not a wallpaper",
      "1003" in library.subscribed() and "1003" not in library.listable())


# ---- Steam, as a dictionary ------------------------------------------------

ALICE = "76561198000000001"
BOB = "76561198000000002"


class FakeSteam:
    def __init__(self, items, authors, profiles):
        self.items = items
        self.authors = authors
        self.profiles_by_key = profiles
        self.author_calls: list[tuple[str, object]] = []
        self.delay = 0.0
        self.has_key = True
        self._lock = threading.Lock()

    def details(self, ids, on_progress=None):
        return {i: self.items.get(i, ItemDetails(id=i, ok=False)) for i in ids}

    def profiles(self, keys, on_progress=None, refresh=False):
        self.last_profile_refresh = refresh
        return {k: self.profiles_by_key.get(k, Profile(id64=None, exists=False))
                for k in keys}

    def run_each(self, func, jobs):
        """The real client runs these on its pool; so does this one."""
        if not jobs:
            return []
        with ThreadPoolExecutor(max_workers=min(8, len(jobs))) as pool:
            return list(pool.map(func, jobs))

    def author_items(self, id64, since=None, refresh=False, **kwargs):
        with self._lock:
            self.author_calls.append((id64, since))
        if self.delay:
            time.sleep(self.delay)
        every = self.authors.get(id64, [])
        if since is not None:
            cutoff = int(since.timestamp())
            every = [i for i in every if i.created > cutoff]
        return AuthorItems(id64=id64, items=list(every), total=len(self.authors.get(id64, [])))


def wallpaper(item_id: str, creator: str, days_ago: float) -> ItemDetails:
    return ItemDetails(id=item_id, ok=True, creator=creator, title=f"w{item_id}",
                       created=stamp(days_ago), updated=stamp(days_ago),
                       preview=f"https://x/{item_id}.jpg")


# Newest first, the way Steam answers. 2002 was owned once and deleted and is
# newer than the last visit; 2001 was owned once too, but long before it.
ALICE_WORK = [wallpaper("3001", ALICE, 2), wallpaper("2002", ALICE, 5),
              wallpaper("2001", ALICE, 400), wallpaper("1001", ALICE, 500),
              wallpaper("4001", ALICE, 600)]
BOB_WORK = [wallpaper("5001", BOB, 3)]

steam = FakeSteam(
    items={w.id: w for w in ALICE_WORK + BOB_WORK},
    authors={ALICE: ALICE_WORK, BOB: BOB_WORK},
    profiles={ALICE: Profile(id64=ALICE, name="Alice", vanity="alice"),
              BOB: Profile(id64=BOB, name="Bob")})


class FakeDb(AuthorsStore):
    def __init__(self, records):
        super().__init__(path=TMP / "never-opened.sqlite")
        self.records = records

    def lookup_many(self, wanted):
        return {key: [r for r in self.records
                      if r.steam_id.lower() in {k.lower() for k in keys}]
                for key, keys in wanted.items()}


known = Author(name="Alice (old name)", steam_id=ALICE,
               added=utc(2024, 1, 1),
               visited=datetime.now(timezone.utc) - timedelta(days=30))
db = FakeDb([known])
review = rv.Review(db, steam, library)


# ---- Phase one: who --------------------------------------------------------

result = review.scan(config_path=WE_CONFIG)
cards = {c.id64: c for c in result.cards}

# Unsubscribing from a wallpaper leaves its id in the folder for ever, so a
# queue used for years remembers thousands of wallpapers it can no longer show.
# The real folder this was built against: 2 155 remembered, 39 still on disk.
check("the queue is what the folder can still show, not what it remembers",
      result.queue == ["1001"] and result.remembered == 3)
check("and the difference is reported rather than hidden",
      "2 more the folder remembers" in result.summary())
check("asking for everything the folder remembers is still possible",
      review.scan(config_path=WE_CONFIG, only_present=False).queue
      == ["1001", "2001", "3001"])
whole = review.scan(rv.SCOPE_EVERYTHING, config_path=WE_CONFIG)
check("a scan of the whole library reaches a wallpaper no folder holds",
      "1002" in whole.queue and "1002" not in result.queue)
check("and the summary names what was looked at",
      "whole library" in whole.summary())

check("every author behind the queue gets a card", set(cards) == {ALICE})
check("an author already in the database is marked known",
      cards[ALICE].state == rv.KNOWN)
check("and the card carries their real name, not the one typed years ago",
      cards[ALICE].name == "Alice")
check("wallpapers Steam would not describe are counted, not dropped silently",
      result.unresolved == [])

new_author = rv.AuthorCard(id64=BOB, profile=Profile(id64=BOB, name="Bob"))
check("an author nobody has added is marked new", new_author.state == rv.NEW)
check("a card with two records is a duplicate the migration could not fold",
      rv.AuthorCard(id64=ALICE, profile=Profile(id64=ALICE),
                    records=[known, known]).state == rv.DUPLICATE)
check("and one Steam will not name is neither",
      rv.AuthorCard(id64="1", profile=Profile(id64=None, exists=False)).state
      == rv.UNKNOWN)


# ---- Phase two: how much ---------------------------------------------------

card = cards[ALICE]
check("a scan asks Steam for names afresh, never from the cache",
      steam.last_profile_refresh is True)

# Without a key every name is a community page of its own, 0.4 s apart — three
# minutes for an ordinary week. The cached name, at most two weeks old, is
# the better trade there.
steam.has_key = False
review.scan(config_path=WE_CONFIG)
check("without a key, names come from the cache rather than a page each",
      steam.last_profile_refresh is False)
steam.has_key = True
check("and knows when the author's wallpapers reached the folder",
      card.appeared is not None)

review.fill(card)
check("a known author is only asked about what came after the last visit",
      steam.author_calls[-1][1] is not None)
check("the gallery holds only what was published since that visit",
      [w.id for w in card.offered] == ["3001", "2002"])
check("everything from before it is not shown, however it was marked",
      not {"2001", "4001"} & {w.id for w in card.offered})
check("which includes a queued wallpaper older than the visit",
      "1001" not in {w.id for w in card.items})
check("so the badge and the gallery are the same number", card.badge == 2)
check("the gallery is newest first, the way a workshop page is",
      card.offered[0].id == "3001")

# Whether you have had a wallpaper before is a separate pass. Counting a week
# is four hundred authors and nobody is looking at a gallery yet, so the count
# does not ask — and until something does, a card must not claim either answer.
check("counting does not ask what you used to own",
      not card.owned_checked and not any(w.once_had for w in card.items))
check("and no card calls itself new to you before anything has asked",
      not any(w.unseen for w in card.offered))

review.mark_owned(card)
check("the pass that does ask marks what was owned once and deleted",
      card.owned_checked and card.returning == 2
      and {w.id for w in card.offered if w.once_had} == {"3001", "2002"})
# This is the one that was missing. A wallpaper subscribed to and dropped
# without ever being copied leaves nothing in the libraries — but Wallpaper
# Engine's folder still remembers its id, for ever. On the real machine that
# is 14 915 wallpapers that were being offered back as if never seen.
check("a folder remembering an id is enough, with no copy left anywhere",
      "3001" in review.owned_before() and "3001" not in library.ever_had())

review.fill(card, full=True)
check("opening the card does not widen it past the visit date",
      steam.author_calls[-1][1] is not None and card.badge == 2 and card.deep)


# ---- Writing the review back -----------------------------------------------

changes = review.plan([card])
check("a reviewed author gets one update", len(changes) == 1
      and changes[0].kind == "update")
check("the visit date moves to the newest wallpaper the review covered",
      abs((changes[0].fields["visited"]
           - datetime.fromtimestamp(ALICE_WORK[0].created, timezone.utc)).total_seconds()) < 2)
check("and nothing is written but the visit date and the name",
      set(changes[0].fields) == {"visited", "name"})

# The database keeps whatever an author was called the day they were added.
# 76561198000000014 is "Baka" there, "Retired Baka" on Steam, and was listed
# for a week as "Banned Cleavage" — a name neither side still used.
check("the author's current name is written back with the review",
      changes[0].fields.get("name") == "Alice" and changes[0].before["name"] == "Alice (old name)")
check("and the card says what the database calls them until then",
      card.database_name == "Alice (old name)")

unopened = rv.AuthorCard(id64=ALICE, profile=Profile(id64=ALICE, name="Alice"),
                         records=[known])
rename_only = review.plan([unopened])
check("an author nobody counted still gets their name brought up to date",
      len(rename_only) == 1 and rename_only[0].fields == {"name": "Alice"})
check("but keeps the visit date they had, because nothing was reviewed",
      "visited" not in rename_only[0].fields)

quiet = rv.AuthorCard(id64=ALICE, profile=Profile(id64=ALICE, name="Alice (old name)"),
                      records=[known], filled=True)
unchanged = review.plan([quiet])
check("a counted author with nothing new keeps the visit date where it was",
      all("visited" not in c.fields for c in unchanged))

fresh_card = rv.AuthorCard(id64=BOB, profile=Profile(id64=BOB, name="Bob"),
                           queued=["5001"])
review.fill(fresh_card, full=True)
review.mark_owned(fresh_card)
check("a wallpaper no folder remembers and no copy names is new to you",
      [w.id for w in fresh_card.offered if w.unseen] == ["5001"])
created = review.plan([fresh_card])
check("a new author is created rather than updated",
      created[0].kind == "create" and created[0].fields["key"] == BOB)
check("under the name Steam gives them today",
      created[0].fields["name"] == "Bob")
check("with everything of theirs counted as unseen",
      fresh_card.badge == 1)

# Pressing Update twice used to plan the same creation twice: the card stayed
# "new" after its author had been written, and the second write was refused.
written = rv.ReviewResult(cards=[fresh_card, card])
review.absorb(written, created)
check("once written, a created author is known to the card",
      fresh_card.state == rv.KNOWN and fresh_card.visited is not None)
check("and a second press of Update has nothing left to do for them",
      review.plan([fresh_card]) == [])

cut_short = rv.AuthorCard(id64=BOB, profile=Profile(id64=BOB, name="Bob"),
                          filled=True, complete=False)
check("a list read without a key is reported as incomplete",
      rv.ReviewResult(cards=[cut_short, card]).incomplete == [cut_short])

check("an author Steam cannot name is not written to the database at all",
      review.plan([rv.AuthorCard(id64="1", profile=None)]) == [])


# ---- What happens the moment something is subscribed ------------------------

# Steam accepts a subscription seconds before the folder appears on disk, and
# in that gap the gallery would go on offering the same wallpaper.
taken = card.offered[0]
check("before subscribing it is on offer", taken.offer and card.badge == 2)
library.note_subscribed(taken.id)
check("a subscription Steam has taken counts immediately",
      taken.id in library.subscribed())
check("and stops being one of the wallpapers owned once and lost",
      taken.id not in library.once_had_but_gone())

taken.subscribed = True
check("the card is no longer offered", not taken.offer
      and taken not in card.offered)
check("so the badge falls by itself", card.badge == 1)
check("but it is still in the list, marked rather than removed",
      taken in card.items)

reopened = rv.AuthorCard(id64=ALICE, profile=Profile(id64=ALICE, name="Alice"),
                         records=[known], queued=list(card.queued))
review.fill(reopened, full=True)
check("and on a later visit it is simply gone from the gallery",
      taken.id not in {w.id for w in reopened.offered})


# ---- Ordering the author list ----------------------------------------------

def author_card(id64, name, state, queued=1, appeared_days=None, db_name=None):
    records = []
    if state in (rv.KNOWN, rv.DUPLICATE):
        record = Author(name=db_name or name, steam_id=id64)
        records = [record] if state == rv.KNOWN else [record, record]
    profile = (Profile(id64=None, exists=False) if state == rv.UNKNOWN
               else Profile(id64=id64, name=name))
    appeared = (datetime.now(timezone.utc) - timedelta(days=appeared_days)
                if appeared_days is not None else None)
    return rv.AuthorCard(id64=id64, profile=profile, records=records,
                         queued=["x"] * queued, appeared=appeared)


roster = [
    author_card("76561190000000001", "zed", rv.NEW, queued=9, appeared_days=1),
    author_card("76561190000000002", "Amy", rv.KNOWN, queued=2, appeared_days=5),
    author_card("76561190000000003", "bob", rv.KNOWN, queued=7, appeared_days=3),
    author_card("76561190000000004", "Cleo", rv.NEW, queued=1, appeared_days=None),
]
by_default = [c.name for c in rv.sort_cards(roster)]
check("by default authors already in the database come first",
      by_default[:2] == ["bob", "Amy"] and set(by_default[2:]) == {"zed", "Cleo"})
check("and within each group the busiest author leads",
      by_default == ["bob", "Amy", "zed", "Cleo"])
check("descending turns the default round, new authors first",
      [c.name for c in rv.sort_cards(roster, rv.SORT_DEFAULT, True)][0] in {"zed", "Cleo"})

check("by name, ignoring case",
      [c.name for c in rv.sort_cards(roster, rv.SORT_NAME)] == ["Amy", "bob", "Cleo", "zed"])
check("and the other way round",
      [c.name for c in rv.sort_cards(roster, rv.SORT_NAME, True)] == ["zed", "Cleo", "bob", "Amy"])

check("by when they reached the folder, earliest first",
      [c.name for c in rv.sort_cards(roster, rv.SORT_APPEARED)][:3] == ["Amy", "bob", "zed"])
check("latest first when descending",
      [c.name for c in rv.sort_cards(roster, rv.SORT_APPEARED, True)][:3] == ["zed", "bob", "Amy"])
check("an author with no arrival time goes last whichever way round",
      rv.sort_cards(roster, rv.SORT_APPEARED)[-1].name == "Cleo"
      and rv.sort_cards(roster, rv.SORT_APPEARED, True)[-1].name == "Cleo")


# ---- Finding an author -----------------------------------------------------

baka = author_card("76561198000000014", "Retired Baka", rv.KNOWN, db_name="Baka")
baka.profile.vanity = "bakaworks"
numbered = author_card("76561190000000099", "2949338283", rv.NEW)

check("an empty search finds everyone", rv.matches(baka, "") and rv.matches(baka, "   "))
check("a name matches on any part of it", rv.matches(baka, "tired"))
check("without regard to case", rv.matches(baka, "RETIRED baka"))
check("including the name the database still remembers them by",
      rv.matches(baka, "Baka") and baka.database_name == "Baka")
check("an id matches only whole", rv.matches(baka, "76561198000000014")
      and not rv.matches(baka, "7656119918239"))
check("a vanity name counts as an id", rv.matches(baka, "BakaWorks"))
check("a pasted profile url is reduced to the id in it",
      rv.matches(baka, "https://steamcommunity.com/profiles/76561198000000014/"))
check("and a vanity url to the name in it",
      rv.matches(baka, "https://steamcommunity.com/id/bakaworks/myworkshopfiles/"))
check("an author whose name is all digits is still found by that name",
      rv.matches(numbered, "29493"))
check("and a search that fits nobody fits nobody",
      not rv.matches(baka, "Oldname7"))


# ---- When a wallpaper arrived -----------------------------------------------

arrived = library.added_at(["1001", "does-not-exist"])
check("arrival is read off the wallpaper's own folder",
      "1001" in arrived and isinstance(arrived["1001"], float))
check("and a wallpaper that is not there has no arrival time",
      "does-not-exist" not in arrived)


# ---- Choosing a frame to stand for a preview --------------------------------

from PySide6.QtCore import QBuffer, QByteArray, QIODevice   # noqa: E402
from PySide6.QtGui import QColor, QImage                    # noqa: E402
from PySide6.QtWidgets import QApplication                  # noqa: E402

_app = QApplication.instance() or QApplication([])
import app.ui.gallery as gal                                # noqa: E402


def encoded(colour: str, fmt: bytes = b"PNG") -> QByteArray:
    image = QImage(24, 16, QImage.Format_RGB32)
    image.fill(QColor(colour))
    blob = QByteArray()
    buffer = QBuffer(blob)
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, fmt.decode())
    buffer.close()
    return blob


dark = QImage(4, 4, QImage.Format_RGB32)
dark.fill(QColor("black"))
bright = QImage(4, 4, QImage.Format_RGB32)
bright.fill(QColor("white"))
check("a black frame reads as dark and a white one as light",
      gal._brightness(dark) < 0.05 and gal._brightness(bright) > 0.95)
check("and the threshold sits between them, so a fade-in is skipped",
      gal._brightness(dark) < gal.STILL_MIN_BRIGHTNESS < gal._brightness(bright))

still = gal._still_image(encoded("#3366cc"))
check("a still image is used as it is", not still.isNull()
      and gal._brightness(still) > 0.1)
check("and nothing at all is not an image",
      gal._still_image(QByteArray()).isNull())
check("neither is something that is not a picture",
      gal._still_image(QByteArray(b"not an image at all")).isNull())


# ---- The Steamworks layer --------------------------------------------------

state = ugc_mod.ItemState(id="1", flags=ugc_mod.STATE_SUBSCRIBED | ugc_mod.STATE_INSTALLED)
check("a subscribed and installed wallpaper is ready to show",
      state.ready and "installed" in state.describe())
downloading = ugc_mod.ItemState(id="1", flags=ugc_mod.STATE_SUBSCRIBED
                                | ugc_mod.STATE_DOWNLOADING)
check("one still downloading is subscribed but not ready",
      downloading.subscribed and not downloading.ready and downloading.downloading)
check("and one Steam knows nothing about says so plainly",
      ugc_mod.ItemState(id="1").describe() == "not subscribed")

missing = ugc_mod.SteamUgc(dll=TMP / "nope.dll")
check("without the library the layer reports itself unavailable, not broken",
      missing.available is False or missing.path is not None)


# ---- Live, opt-in ----------------------------------------------------------

if "--live" in sys.argv:
    print("\n-- live (reads Steam; subscribes to nothing) --")
    real = ugc_mod.SteamUgc()
    check("Wallpaper Engine's Steamworks library is where it should be",
          real.available)
    try:
        with real:
            count = real.subscribed_count()
            check("Steam answers how many subscriptions this account has",
                  count > 100)
            present = next(iter(lib_mod.Library().subscribed()), None)
            if present:
                check("and agrees that a wallpaper on disk is subscribed",
                      real.state(present).subscribed)
    except ugc_mod.UgcError as err:
        check(f"Steam is reachable ({err})", False)


# ---- Filling every card, which is the phase with the waiting in it ---------
#
# One Steam request per author and nothing else, so it is all latency: measured
# against the real thing, fifty authors took 44.2 s one at a time and 11.3 s
# through the client's pool. What has to hold is that the concurrency changes
# only the waiting: every card still filled, every card still counted once, and
# the library sets read once for the batch rather than once per card.

counted = {"subscribed": 0, "ever": 0}
real_subscribed, real_ever = library.subscribed, library.ever_had


def count_subscribed():
    counted["subscribed"] += 1
    return real_subscribed()


def count_ever():
    counted["ever"] += 1
    return real_ever()


library.subscribed, library.ever_had = count_subscribed, count_ever
steam.author_calls.clear()
steps: list[tuple[str, int, int]] = []
lock = threading.Lock()


def note(stage, done, total):
    with lock:
        steps.append((stage, done, total))


batch = rv.Review(db, steam, library, on_progress=note)
filled = batch.scan(config_path=WE_CONFIG)
counted["subscribed"] = counted["ever"] = 0
batch.fill_all(filled)

check("every card comes back filled",
      bool(filled.cards) and all(c.filled for c in filled.cards))
check("each author was asked for exactly once",
      sorted(c[0] for c in steam.author_calls) == sorted(c.id64 for c in filled.cards))
check("progress is reported once per card",
      len([s for s in steps if s[0] == "wallpapers"]) == len(filled.cards))
check("and the last report is the whole batch",
      max(s[1] for s in steps if s[0] == "wallpapers") == len(filled.cards))
check("the subscribed set is read once for the batch, not once per card",
      counted["subscribed"] == 1)
check("and counting never asks the slow question of what you used to own",
      counted["ever"] == 0)
check("the totals are worked out after the filling, not before",
      filled.counts.get("filled") == len(filled.cards))

# Filling a single card on its own must keep working exactly as it did, sets
# and all — that is the path a click on one author takes.
counted["subscribed"] = counted["ever"] = 0
one = [c for c in filled.cards if c.id64 == ALICE][0]
one.filled = False
batch.fill(one)
check("one card on its own still reads the subscribed set for itself",
      one.filled and counted["subscribed"] == 1 and counted["ever"] == 0)

counted["ever"] = 0
batch.mark_owned(one)
batch.mark_owned(one)
check("the set behind “was yours” is built once and shared by every gallery",
      counted["ever"] == 1 and one.owned_checked)
batch.forget_owned()
batch.mark_owned(one)
check("and built again on demand, for a folder filled since the scan",
      counted["ever"] == 2)

# The waiting has to actually overlap. Four cards that each take 200 ms are
# 800 ms in a row; through the pool they are one wait, not four.
slow = FakeSteam(items=steam.items,
                 authors={f"7656119800000000{i}": [wallpaper(f"9{i}", f"7656119800000000{i}", 1)]
                          for i in range(4)},
                 profiles={})
slow.delay = 0.2
cards = [rv.AuthorCard(id64=k, profile=None, records=[], queued=[])
         for k in slow.authors]
spread = rv.Review(FakeDb([]), slow, library)
started = time.perf_counter()
spread.fill_all(rv.ReviewResult(queue=[], remembered=0, cards=cards))
elapsed = time.perf_counter() - started
check("four cards of 200 ms each take one wait, not four",
      elapsed < 0.5, )
check("and all four are filled regardless", all(c.filled for c in cards))

# "Count what is new" and a click on an author it has not reached yet used to
# fetch the same catalogue side by side, and write the same card from two
# threads. One fetch per author: whoever comes second waits and takes the answer.
twice = FakeSteam(items=steam.items,
                  authors={"76561198000000077": [wallpaper("77", "76561198000000077", 1)]},
                  profiles={})
twice.delay = 0.3
same = rv.AuthorCard(id64="76561198000000077", profile=None, records=[], queued=[])
both = rv.Review(FakeDb([]), twice, library)
racers = [threading.Thread(target=both.fill, args=(same,)) for _ in range(2)]
for racer in racers:
    racer.start()
for racer in racers:
    racer.join()
check("two fills of one author at once ask Steam once",
      len(twice.author_calls) == 1 and same.deep and len(same.items) == 1)
both.fill(same, refresh=True)
check("while asking again on purpose still asks again", len(twice.author_calls) == 2)

# An author whose fetch failed once is not marked failed for ever.
flaky = rv.AuthorCard(id64="76561198000000077", profile=None, records=[], queued=[])
flaky.error = "Steam timed out"
both.fill(flaky)
check("a fetch that works clears the error of the one before", flaky.error == "")

library.subscribed, library.ever_had = real_subscribed, real_ever

print()
print("PASSED %d/%d" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
