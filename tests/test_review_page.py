"""Review page: what each state says, the flow end to end on a Steam made of
dictionaries and an authors database in a temporary folder, the plan shown
before anything is written, and the two dialogs.

Run it directly (needs Qt, but no windows on screen):

    .venv\\Scripts\\python.exe tests\\test_review_page.py

Nothing here asks Steam, reads Wallpaper Engine's config.json or this
machine's authors database: the page is given its own config file, its own
workshop folder and a `prepare` that hands it a Review over a fake client.
"""
from __future__ import annotations

import builtins
import io
import json
import os
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

TMP = Path(tempfile.mkdtemp(prefix="wallpaper_review_page_test_"))
# Before any app module: the data folder resolves when they are imported.
os.environ["WALLPAPER_TOOLKIT_DATA"] = str(TMP / "data")
(TMP / "data").mkdir(parents=True, exist_ok=True)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# The labels carry ≈, ·, — and →, which a Windows console's code page (cp1252
# on CI) cannot always print; a check must not fail for the way its name is shown.
sys.stdout.reconfigure(errors="replace")

from PySide6.QtCore import Qt                                             # noqa: E402
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget          # noqa: E402

app = QApplication(sys.argv)

from app import animations, secrets, services, theme                     # noqa: E402
from app.engines import review as rv                                      # noqa: E402
from app.engines import review_flow as rf                                 # noqa: E402
from app.engines.authors_store import Author, AuthorsStore, Change, StoreDamaged  # noqa: E402
from app.engines.library import Library                                   # noqa: E402
from app.engines.steam_api import (                                       # noqa: E402
    AuthorItems, ItemDetails, Profile, SteamUnreachable,
)
from app.pages import review as page_module                               # noqa: E402
from app.pages import review_settings as rs                               # noqa: E402
from app.pages.review import (                                            # noqa: E402
    AuthorList, ReviewPage, ScanProgress, author_row, done_subtitle, done_text, empty_subtitle,
    empty_text, finish_plan, found_row, gallery_subtitle, last_meta, list_foot, nav_state,
    reviewing_subtitle, scanning_subtitle, stopped_subtitle, stopped_text, written_blind,
    written_line,
)
from app.services import snapshot as snapshot_module                      # noqa: E402
from app.services.snapshot import REVIEW, ReviewState                     # noqa: E402
from app.settings import Settings                                         # noqa: E402
from app.ui import gallery as gallery_mod                                    # noqa: E402
from app.ui.kit import ConfirmDialog, NavState, format as fmt             # noqa: E402

theme.apply(app)
animations.ENABLED = True
snapshot_module.compute = lambda keys, data_dir: {}

NOW = datetime(2026, 10, 1, 14, 20)            # a Thursday
results: list[bool] = []


def check(label: str, condition: bool) -> None:
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label)


def wait_for(condition, ms: int = 20_000) -> bool:
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.005)
    return condition()


def at(clock: str, days_ago: int = 0) -> datetime:
    hour, minute = (int(p) for p in clock.split(":"))
    return (NOW - timedelta(days=days_ago)).replace(hour=hour, minute=minute)


# ==== the words ===================================================================================

print("-- the header, per state --")
finished_last = ReviewState.from_json({"scanned": at("09:10", 5).isoformat(),
                                       "finished": at("09:52", 5).isoformat(), "items": 89,
                                       "authors": [{"name": "a", "new": 1, "done": True}] * 12,
                                       "checked": 118})
open_last = ReviewState.from_json({"scanned": at("09:10", 1).isoformat(), "items": 89,
                                   "authors": [{"name": "a", "new": 1, "done": False}] * 12})
check("before any review there is none to name", empty_subtitle(None, NOW) == "No review yet")
check("after one, the day it was finished",
      empty_subtitle(finished_last, NOW) == "Last review 26 Sep")
check("a scan nobody finished says so",
      empty_subtitle(open_last, NOW) == "Last scan Wed 09:10 · not finished")
check("a scan names how many authors it is checking, once it knows",
      scanning_subtitle(118, "folder:new") == "Scanning 118 authors"
      and scanning_subtitle(None, "folder:new") == "Scanning folder “new”")
check("a stopped scan, when it stopped", stopped_subtitle(at("13:46")) == "Scan stopped at 13:46")

print("-- the empty state --")
title, body, meta = empty_text(None, "folder:new", NOW)
check("nothing scanned yet, and what a scan does: the authors of a folder, not "
      "\u201cauthors you follow\u201d (§7.1)",
      title == "Nothing scanned yet" and "folder “new”" in body and "follow" not in body
      and "changes nothing on disk" in body and meta == "")
title, body, meta = empty_text(finished_last, "folder:new", NOW, seconds=112)
check("after a finished review: nothing since, and the last scan (gate G4 A, no schedule)",
      title == "Nothing scanned since the last review"
      and meta == "last scan Sat 09:10 · 12 authors had new items"
      and "runs on its own" not in meta)
check("how long a scan takes comes from the last one, never made up",
      body.endswith("The last one took 2 min.") and "about two minutes" not in body)
title, body, _ = empty_text(open_last, "folder:new", NOW)
check("a scan left unfinished is not called nothing",
      title == "The last scan was not gone through" and "12 authors" in body)
check("a scan that found nothing says nothing new",
      last_meta(ReviewState.from_json({"scanned": at("09:10").isoformat(), "items": 0,
                                       "authors": []}), NOW) == "last scan 09:10 · nothing new")

print("-- the scan, as it goes --")
scan = ScanProgress("folder:new", at("13:44"))
check("before anything is counted there is no number", scan.figure() == (fmt.DASH, "", "getting ready")
      and scan.bar() is None)
scan.event(rf.FlowEvent("step", "reading folder “new”"))
scan.event(rf.FlowEvent("progress", "items", 400, 3366))
check("while it describes the wallpapers it counts those",
      scan.figure()[1:] == (f"/ {fmt.count(3366)}", "wallpapers described")
      and scan.status_text() == f"describing 400 of {fmt.count(3366)} wallpapers")
scan.event(rf.FlowEvent("found", total=118))
for n in range(1, 35):
    card = rv.AuthorCard(id64=str(n))
    scan.event(rf.FlowEvent("checking", f"author {n}", n - 1, 118, card, n - 1))
    scan.event(rf.FlowEvent("checked", f"author {n}", n, 118, card, n - 1))
check("then the authors: the count, the total and the time left from the live rate",
      scan.figure(70) == ("34", "/ 118", "authors checked · ≈1 min left"))
check("with no rate yet, no time left", scan.figure()[2] == "authors checked")
check("the status line says it the design's way",
      scan.status_text() == "scanning 34 of 118 authors" and scan.count_text() == "34 / 118")
check("and the line under the bar names the author being checked",
      scan.activity() == "checking author 34")

print("-- the rows --")
ann = rv.AuthorCard(id64="76561198000000901", profile=Profile(id64="76561198000000901", name="Ann"),
                    records=[Author("76561198000000901", "Anna", visited=datetime(
                        2026, 9, 13, tzinfo=timezone.utc))])
ann.items = [rv.Wallpaper(item=ItemDetails(id=str(i), ok=True, created=1, title="t"))
             for i in range(3)]
ann.filled = True
newbie = rv.AuthorCard(id64="76561198000000902", profile=Profile(id64="76561198000000902", name="Bo"))
newbie.items, newbie.filled = list(ann.items), True
check("found so far: the count, and a new author marked and said",
      found_row(ann).trailing == "3 new" and not found_row(ann).chips
      and found_row(newbie).chips == (("NewAuthor", None),)
      and found_row(newbie).trailing == "3 new — first time seen")
person = rf.SessionAuthor(id=ann.id64, name="Ann", new=3, state=rv.KNOWN)
row = author_row(person, ann)
check("an author's row: the chip as today, the name the database still has, the count",
      row.chips == (("Known", None),) and row.title_note == "was Anna" and row.meta == "3 new"
      and not row.tick)
person.subscribed = {"1", "2"}
person.done = True
row = author_row(person, ann)
check("gone through: ticked and set back, with what was subscribed",
      row.tick and row.dimmed and row.meta == "3 new · 2 subscribed")
person.incomplete = True
check("a list read without a key says so, in warn",
      author_row(person, ann).meta.endswith("list incomplete")
      and author_row(person, ann).meta_tone == "warn")
check("over the gallery: since when, and how much in all",
      gallery_subtitle(rf.SessionAuthor(id=ann.id64, name="Ann", new=3), ann, NOW)
      .startswith("3 new since 13 Sep"))

print("-- stopped --")
cards4 = [rv.AuthorCard(id64=str(i), profile=Profile(id64=str(i), name=f"n{i}")) for i in range(4)]
for c in cards4[:2]:
    c.filled, c.items = True, list(ann.items)
unreachable = SteamUnreachable("x", host="steamcommunity.com", reason="timed out", attempts=3)
outcome = rf.ScanOutcome(rf.STOPPED, rv.ReviewResult(cards=cards4), "count", 2, 4, 2,
                         unreachable, rf.plain_reason(unreachable))
words = stopped_text(outcome)
check("an error mid-way says where, why in plain words, and what was kept",
      words.title == "The scan stopped at author 3 of 4"
      and words.body.startswith("Steam did not answer: steamcommunity.com — timed out after 3 tries.")
      and "the 2 authors already checked are kept — 2 of them with new items —" in words.body
      and words.carry == "Carry on from author 3" and words.tone == "danger")
outcome.state, outcome.error = rf.CANCELLED, None
words = stopped_text(outcome)
check("a cancel is said quietly, and can be carried on too",
      words.title == "The scan was stopped at author 3 of 4" and words.tone == "neutral"
      and words.body.startswith("Stopped on request.") and words.carry)
damaged = StoreDamaged("authors.sqlite is damaged: file is not a database")
words = stopped_text(rf.ScanOutcome(rf.STOPPED, None, "prepare", error=damaged,
                                    reason=rf.plain_reason(damaged)))
check("a damaged database offers the backups, not a carry-on",
      words.restore and not words.carry and "set aside, not deleted" in words.body)
words = stopped_text(rf.ScanOutcome(rf.STOPPED, None, "scan", error=unreachable,
                                    reason=rf.plain_reason(unreachable)))
check("a scan that never found the authors can only start over",
      words.title == "The scan stopped before it found the authors" and not words.carry)

print("-- the plan, before anything is written --")
store = AuthorsStore(TMP / "plan.sqlite")
changes = [store.plan_create("New One", f"7656119800000099{i}") for i in range(3)]
known = [Author(f"7656119800000098{i}", f"old{i}", visited=datetime(2026, 9, 1, tzinfo=timezone.utc))
         for i in range(21)]
changes += [store.plan_update(a, visited=datetime(2026, 9, 30, tzinfo=timezone.utc),
                              name=(f"new{i}" if i < 2 else None), reason="reviewed")
            for i, a in enumerate(known)]
title, body, lines, note = finish_plan(changes, 0)
check("the plan in numbers, the design's sentence",
      body.startswith("3 authors to create, 21 visit dates to move, 2 names to bring up to date."))
check("every change as a line, and no warning when every list was whole",
      len(lines) == 24 and lines[0].startswith("create New One") and note is None)
dialog = ConfirmDialog(title, body, None, lines=lines, note=note, embedded=True)
shown = dialog.listed_lines()
check("the confirmation lists the first 14, then how many more",
      len(shown) == 15 and shown[-1] == "… and 10 more" and not dialog.is_destructive()
      and not dialog.cancel_is_default())
title, body, lines, note = finish_plan(changes, 4)
dialog = ConfirmDialog(title, body, None, lines=lines, note=note, safe_default=bool(note),
                       embedded=True)
check("a visit date from a list read without a key is warned about, and Cancel is "
      "the default", note is not None and note[0] == "warn" and "4 of these" in note[1]
      and dialog.cancel_is_default() and dialog.initial_focus() is dialog.cancel_button())
check("after the write: what it did and where the backup is",
      written_line({"created": 3, "updated": 23, "backup": r"X:\b\authors-1.json.gz"}, False)
      == "3 created, 23 updated. Backup: authors-1.json.gz.")

print("-- finished --")
done_session = rf.Session(scope="folder:new", scanned=at("13:47"), authors=[
    rf.SessionAuthor(id=str(i), name=f"a{i}", new=10, yours=1 if i < 2 else 0, done=True)
    for i in range(12)], checked=118, nothing_new=106)
done_session.authors[0].subscribed = {str(n) for n in range(23)}
done_session.finished = at("14:12")
done_session.written = {"created": 3, "updated": 23, "backup": "authors-x.json.gz"}
words = done_text(done_session, NOW)
check("every author gone through, what was subscribed, and the database",
      words.sentence.startswith("All 12 authors went through. 23 wallpapers were subscribed "
                                "and are downloading in Steam.")
      and "they land in the reserve" not in words.sentence
      and words.sentence.endswith("3 authors added to the authors database and 23 brought up "
                                  "to date."))
check("the numbers: new items, subscribed (info), already had (warn), were yours (ok)",
      words.metrics == ((120, "new items found"), (23, "subscribed", "info"),
                        (0, "already had", "warn"), (2, "were yours", "ok")))
check("and when it finished, with the backup", words.foot == "finished 14:12 · backup authors-x.json.gz")
check("the header says the same", done_subtitle(done_session) == "Finished 14:12 · 12 authors gone through")
done_session.authors[5].done = False
check("an author not gone through is counted honestly",
      done_text(done_session, NOW).sentence.startswith("11 of 12 authors went through."))
check("the reviewing header: since when, and how many",
      reviewing_subtitle(rf.Session("folder:new", NOW, done_session.authors,
                                    since=datetime(2026, 9, 13).date()), NOW)
      == "New since 13 Sep · 12 authors with new items")
check("under the list, the authors with nothing new",
      list_foot(done_session) == "106 authors had nothing new")

print("-- the sidebar --")
check("scanning shows the bar once the authors are known",
      nav_state("scanning", scan=scan) == NavState.progress(34, 118))
check("reviewing shows the authors waiting as a badge",
      nav_state("reviewing", session=done_session) == NavState.badge(1))
check("a scan left unfinished keeps its badge after a restart",
      nav_state("empty", last=open_last) == NavState.badge(12))
check("a finished review has no badge", nav_state("empty", last=finished_last) == NavState())
check("a stopped scan is said in danger",
      nav_state("stopped", error=True).tone == "danger")


# ==== the page ===================================================================================

print("-- a machine in a temporary folder --")
WORKSHOP = TMP / "workshop" / "content" / "431960"
WORKSHOP.mkdir(parents=True)
AUTHORS = [f"7656119800000030{i}" for i in range(1, 6)]
NAMES = ["Ann", "Ben", "Cat", "Dan", "Eve"]
QUEUED = [f"810{i}" for i in range(1, 6)]
for item in QUEUED:
    (WORKSHOP / item).mkdir()
    (WORKSHOP / item / "project.json").write_text(json.dumps({"title": item}), encoding="utf-8")
WE_CONFIG = TMP / "config.json"
WE_CONFIG.write_text(json.dumps({"steamuser": {"general": {"browser": {"folders": [
    {"title": "new", "items": {i: 1 for i in QUEUED}, "subfolders": [], "type": 0}]}}}}),
    encoding="utf-8")
library = Library(workshop=WORKSHOP, roots=[], index_path=TMP / "library.json")


def item(item_id: str, author: str, days_ago: float) -> ItemDetails:
    stamp = int((datetime.now(timezone.utc) - timedelta(days=days_ago)).timestamp())
    return ItemDetails(id=item_id, ok=True, creator=author, title=f"t{item_id}", created=stamp,
                       updated=stamp, preview="", kind="Scene", file_size=1024 ** 2)


class Steam:
    """Steam, as dictionaries: one author after another; `fail` holds the
    authors it does not answer for; `on_call` runs before each."""

    def __init__(self):
        self.items = {q: item(q, AUTHORS[n], 30) for n, q in enumerate(QUEUED)}
        # Ben has nothing but what is already here; the others 2–5 new each
        self.works = {a: [item(f"9{n}{k}", a, k + 1) for k in range(n + 1)] if n != 1 else []
                      for n, a in enumerate(AUTHORS)}
        self.has_key = True
        self.fail: set[str] = set()
        self.on_call = None
        self.calls: list[str] = []

    def details(self, ids, on_progress=None):
        return {i: self.items.get(i, ItemDetails(id=i, ok=False)) for i in ids}

    def profiles(self, keys, on_progress=None, refresh=False):
        return {k: Profile(id64=k, name=NAMES[AUTHORS.index(k)]) for k in keys}

    def run_each(self, func, jobs):
        return [func(job) for job in jobs]

    def author_items(self, id64, since=None, refresh=False, **kwargs):
        self.calls.append(id64)
        if self.on_call is not None:
            self.on_call(id64)
        if id64 in self.fail:
            raise SteamUnreachable("timed out", host="api.steampowered.com",
                                   reason="timed out", attempts=3)
        every = self.works.get(id64, [])
        if since is not None:
            every = [i for i in every if i.created > int(since.timestamp())]
        return AuthorItems(id64=id64, items=list(every), total=len(every) + 7,
                           complete=self.has_key)

    def close(self):
        pass


steam = Steam()
DB = TMP / "data" / "authors.sqlite"
with AuthorsStore(DB) as seed:
    # Dan is known already, under the name he had then, visited a month ago
    seed.apply([seed.plan_create("Daniel", AUTHORS[3], visited=datetime.now(timezone.utc)
                                 - timedelta(days=30))])
prepared: list = []


def prepare(step):
    step("opening the authors database")
    db = AuthorsStore(DB).open()
    prepared.append(threading.current_thread() is threading.main_thread())
    return rv.Review(db, steam, library)


GUARDED = [(builtins, "open"), (io, "open"), (os, "stat"), (os, "lstat"), (os, "scandir"),
           (os, "listdir"), (os.path, "isdir"), (os.path, "isfile"), (os.path, "exists"),
           (Path, "stat"), (Path, "exists"), (Path, "is_dir"), (Path, "is_file"),
           (Path, "iterdir"), (Path, "read_text"), (Path, "read_bytes"), (Path, "open")]
on_gui_thread: list = []


def guarded(fn):
    originals = {}
    for owner, name in GUARDED:
        real = getattr(owner, name)
        originals[(owner, name)] = real

        def stand_in(*args, _real=real, _name=name, **kwargs):
            if threading.current_thread() is threading.main_thread():
                on_gui_thread.append(_name)
                raise OSError(f"{_name} called on the GUI thread")
            return _real(*args, **kwargs)
        setattr(owner, name, stand_in)
    try:
        return fn()
    finally:
        for (owner, name), real in originals.items():
            setattr(owner, name, real)


svc = services.Services(data_dir=TMP / "data")
services.install(svc)
settings = Settings({})
page = guarded(lambda: ReviewPage(settings, svc, prepare=prepare, config_path=WE_CONFIG,
                                  data_dir=TMP / "data"))
check(f"building the page reads nothing from the disk ({on_gui_thread} were read)",
      on_gui_thread == [] and page.state == "empty")


class Host(QWidget):
    def __init__(self):
        super().__init__()
        self.column = QVBoxLayout(self)
        self.resize(1280, 860)


host = Host()
host.column.addWidget(page)
host.move(400, 400)
host.show()
asked: list = []
answers = {"confirm": True}


def answer(dialog):
    asked.append(dialog)
    if isinstance(dialog, ConfirmDialog):
        dialog._answer(answers["confirm"])
        return dialog.result_value()
    return True


page._answer = answer
page.library = library
page.on_shown()
check("with no key, the keyless banner shows in the empty state", page.texts()["keyless"])
check("and with no last review, the page says so",
      page.subtitle().endswith("No review yet")
      and page.texts()["empty"][0] == "Nothing scanned yet")
check("the header counts the selected source before a scan",
      wait_for(lambda: page.subtitle() == "5 wallpapers to scan · No review yet"))

print("-- source counts --")
original_config = WE_CONFIG.read_text(encoding="utf-8")
WE_CONFIG.write_text(json.dumps({"user": {"general": {"browser": {"folders": [
    {"title": "new", "items": {QUEUED[0]: 1, QUEUED[1]: 1, "999999": 1}},
    {"title": "other", "items": {QUEUED[1]: 1, QUEUED[2]: 1}},
    {"title": "empty", "items": {}}]}}}}), encoding="utf-8")
guarded(page._refresh_source_counts)
check("counting reads no library or config on the GUI thread",
      wait_for(lambda: page._source_counts is not None) and on_gui_thread == [])
check("counts exclude deleted IDs, deduplicate folders, and include loose wallpapers",
      page._source_counts == {"folder:new": 2, "folder:other": 2, "folder:empty": 0,
                              rv.SCOPE_FOLDERS: 3, rv.SCOPE_LOOSE: 2,
                              rv.SCOPE_EVERYTHING: 5})

def choose_source(dialog):
    dialog.source.setCurrentIndex(dialog.source.findData(rv.SCOPE_EVERYTHING))
    return True

with patch("app.pages.review.steamworks_note", return_value="test"):
    page._answer = choose_source
    page.edit_settings()
    check("saving another Review source updates the header without scanning",
          wait_for(lambda: page.subtitle() == "5 wallpapers to scan · No review yet")
          and page.flow is None)
page._answer = answer
for scope, expected in (("folder:empty", 0), ("folder:gone", 0),
                        (rv.SCOPE_LOOSE, 2), (rv.SCOPE_FOLDERS, 3)):
    settings.set("review", "scope", scope)
    page._render()
    check(f"the header describes {scope}",
          page.subtitle().startswith(f"{expected} wallpapers to scan · "))
page._answered(f"source-counts:{page._source_request - 1}", {rv.SCOPE_FOLDERS: 999})
check("an older background result cannot replace a newer count",
      page.subtitle().startswith("3 wallpapers to scan · "))
page._answered(f"source-counts:{page._source_request}", OSError("test"))
check("a failed count is not displayed as zero",
      page.subtitle().startswith("Wallpaper count unavailable · "))
WE_CONFIG.write_text(original_config, encoding="utf-8")
settings.set("review", "scope", rv.DEFAULT_SCOPE)
page.on_hidden()
page.on_shown()
check("returning to Review rereads the source and recovers from a count failure",
      wait_for(lambda: page.subtitle() == "5 wallpapers to scan · No review yet"))

print("-- a scan, end to end --")
page.start_scan()
check("scanning: the state, a job in the status line", page.state == "scanning"
      and svc.jobs.is_running("review"))
check("the scan ends with the authors to go through",
      wait_for(lambda: page.state == "reviewing"))
check("what the scan needed was made on its thread, not the window's",
      prepared == [False])
session = page.session
check("the authors with new items are listed, the one with nothing new counted under them",
      [a.name for a in session.authors] == ["Dan", "Ann", "Cat", "Eve"]
      and page.texts()["foot"] == "1 author had nothing new")
check("the list says how many are done, in warn while some wait",
      page.texts()["authors_head"] == ("Authors with new items", "0 / 4"))
check("the first author waiting is opened", page.current == AUTHORS[3]
      and page.texts()["gallery"][2] == "Done with Dan →")
check("his gallery is what is new since his last visit",
      [w.id for w in page.gallery.showing()] == [w.id for w in page.cards[AUTHORS[3]].offered]
      and len(page.gallery.showing()) == 4)
check("the header: how many authors have new items",
      page.subtitle().endswith("4 authors with new items"))
check("the sidebar badge is the authors waiting", page.nav_state() == NavState.badge(4))
last_file = TMP / "data" / "review_last.json"
saved = json.loads(last_file.read_text(encoding="utf-8"))
state = ReviewState.from_json(saved)
check("review_last.json is written when the scan finishes",
      state.authors == 4 and state.waiting == 4 and state.checked == 5 and state.finished is None
      and saved["scope"] == "folder:new" and isinstance(saved["seconds"], float))
journal = svc.journal.recent(1)[0]
check("and the scan is in the activity journal",
      journal.kind == "scan.clean" and journal.title == "13 new items to look at")
check("the job is gone from the status line", not svc.jobs.is_running("review"))

page.done_with_author()
check("Done with Dan: ticked, the next opened, 1 / 4",
      session.find(AUTHORS[3]).done and page.current == AUTHORS[0]
      and page.texts()["authors_head"][1] == "1 / 4"
      and page.nav_state() == NavState.badge(3))
check("and written to review_last.json at once",
      ReviewState.from_json(json.loads(last_file.read_text(encoding="utf-8"))).waiting == 3)

print("-- subscribing --")
queued_for_steam: list = []
page.subscriptions.add = lambda ids: queued_for_steam.append(list(ids))
page._update_bar()
check("the bar offers what is left on the page",
      page.gallery_panel.subscribe_page.text() == "Subscribe page"
      and "the 1 wallpaper on this page" in page.gallery_panel.subscribe_page.toolTip()
      and page.texts()["bar"]["subscribe_page"])
page.subscribe_page()
check("pressing it subscribes to every wallpaper on the page not already taken",
      queued_for_steam == [[w.id for w in page.cards[AUTHORS[0]].offered]])
check("each waits on its card until Steam is asked for it",
      all(page.gallery.busy(i) == gallery_mod.WAITING for i in queued_for_steam[0]))
page.subscriptions.started_item.emit(queued_for_steam[0][0])
check("and turns its spinner while it is",
      page.gallery.busy(queued_for_steam[0][0]) == gallery_mod.SUBSCRIBING)
page.subscribe_page()
check("and not twice for the same wallpapers", len(queued_for_steam) == 1)
taken = queued_for_steam[0][0]
page._subscribed(taken, "subscribed")
check("a subscription is counted for its author, and shown on the row",
      session.find(AUTHORS[0]).subscribed == {taken}
      and "1 subscribed" in page.authors.list.model_.row(AUTHORS[0]).meta)
check("and its card is no longer worked on", page.gallery.busy(taken) is None)
settings.set("review", "subscribe", rs.BY_PAGE)
page._update_bar()
check("subscribing a page needs Steam directly",
      not page.gallery_panel.subscribe_page.isEnabled())
on_offer = next(w for c in page.cards.values() for w in c.offered
                if page.gallery.busy(w.id) is None and not w.in_library)
check("and the cards and the list say a click opens Steam's page",
      page.gallery.model_.opens_page
      and page.gallery_list.model_.cell(on_offer, 5).text == "Open in Steam")
settings.set("review", "subscribe", rs.BY_STEAM)
page._update_bar()


class WatchedLibrary:
    def __init__(self, found):
        self.found, self.threads = found, []

    def subscribed(self):
        self.threads.append(threading.current_thread() is threading.main_thread())
        return set(self.found)

    def note_subscribed(self, item_id):
        pass


page.open_author(AUTHORS[4])
other = page.cards[AUTHORS[4]].offered[1].id
real_library, page.library = page.library, WatchedLibrary({other})
page._notice_subscriptions()
page._notice_subscriptions()            # while the first look is still out
check("a wallpaper subscribed elsewhere is noticed, for the author it is by",
      wait_for(lambda: other in session.find(AUTHORS[4]).subscribed))
check("by looking at the folder off the GUI thread, once while a look is out",
      page.library.threads == [False])
page.library = real_library

print("-- the selection, and the bar --")
busiest = max(session.authors, key=lambda a: a.new).id
page.open_author(busiest)
open_ids = [w.id for w in page.gallery.current_page() if page.gallery.selectable(w)]
page.gallery.click(open_ids[0], Qt.ControlModifier)
page.gallery.click(open_ids[1], Qt.ControlModifier)
bar = page.texts()["bar"]
check("selecting cards says how many in the bar, and offers Subscribe selected",
      bar["selected"] == "2 selected" and bar["subscribe_selected"])
check("and on the author's row", "2 selected" in page.authors.list.model_.row(busiest).meta)
queued_for_steam.clear()
page.subscribe_selected()
check("Subscribe selected queues them in the order chosen and lets go of the selection",
      queued_for_steam == [open_ids[:2]] and page.gallery.selected_ids() == []
      and not page.texts()["bar"]["subscribe_selected"]
      and "selected" not in page.authors.list.model_.row(busiest).meta)
for item_id in open_ids[:2]:
    page.gallery.mark_busy(item_id, None)
page.gallery.click(open_ids[2] if len(open_ids) > 2 else open_ids[0], Qt.ControlModifier)
page.open_author(AUTHORS[0] if busiest != AUTHORS[0] else AUTHORS[1])
check("another author's gallery starts with nothing selected",
      page.gallery.selected_ids() == [] and page.texts()["bar"]["selected"] == "")

page.gallery_panel.view.set_current_index(1)
check("List shows the table, and the choice is remembered",
      page.texts()["view"] == "list" and settings.get("review", "view", None) == "list"
      and page.gallery_panel.stack.currentIndex() == 1)
page.open_author(busiest)
check("for the next author too", page.texts()["view"] == "list")
page.gallery_list.subscribe_requested.emit(open_ids[-1])
check("the list's Subscribe goes through the same queue",
      queued_for_steam[-1] == [open_ids[-1]])
page.gallery.mark_busy(open_ids[-1], None)
page.set_view("grid")
check("and Grid back again", page.texts()["view"] == "grid" and
      settings.get("review", "view", None) == "grid" and page.gallery_panel.stack.currentIndex() == 0)

print("-- skip, then finish --")
went: list = []
page.navigate.connect(went.append)
# The frame asks once and parents what it gets; a kit button left without a
# parent sits in a reference cycle with its fade, and the collector freeing it
# while a scan's thread runs Python is a crash.
host.column.insertWidget(0, header := QWidget())
row = QVBoxLayout(header)
for action in page.header_actions():
    row.addWidget(action)
page.skip_button.click()
check("Skip for now leaves for the Overview and keeps the review as it is",
      went == ["overview"] and page.state == "reviewing" and page.session is session)

page.finish_review()
plan = asked[-1]
check("Finish review shows the plan first",
      isinstance(plan, ConfirmDialog) and plan.title() == "Write the review to the authors database?"
      and plan.body().startswith("4 authors to create, 1 visit date to move, 1 name to bring up to date."))
check("an author with nothing new is created too, so next week knows them",
      any(line.startswith("create Ben") for line in plan.listed_lines()))
check("with every change listed", any(line.startswith("update Daniel") for line in plan.listed_lines())
      and not plan.cancel_is_default())
check("then writes, and the review is finished", wait_for(lambda: page.state == "done"))
with AuthorsStore(DB) as written:
    check("the authors database has the new authors", written.count() == 5)
    dan = written.by_key(AUTHORS[3])
check("and Dan's current name and a new visit date", dan.name == "Dan"
      and dan.visited > datetime.now(timezone.utc) - timedelta(days=5))
check("the result is said, with the backup",
      any(tone == "ok" and words.startswith("4 created, 1 updated. Backup: authors-")
          for tone, words in page.messages))
final = ReviewState.from_json(json.loads(last_file.read_text(encoding="utf-8")))
check("review_last.json says the review is finished", final.finished is not None
      and json.loads(last_file.read_text(encoding="utf-8"))["written"]["created"] == 4)
check("the finished state: who went through, the numbers, the last scan",
      page.texts()["done"][0].startswith("1 of 4 authors went through. 2 wallpapers were subscribed")
      and page.texts()["done"][1].startswith("last scan ")
      and page.subtitle().startswith("5 wallpapers to scan · Finished "))
check("and the database write is journalled", svc.journal.recent(1)[0].kind == "database.clean")
page.reopen()
check("Reopen review goes back to the authors", page.state == "reviewing"
      and page.session is session)

print("-- the review as a list --")
page._finished(None, [])
page.open_review_list()
listed = page.gallery_list.model_
groups = [listed.group_at(r).title for r in listed.group_rows()]
expected = [i for i in page._ordered() if page._gallery_items(i)]
check("Open review as a list: every author's wallpapers under their name, as a list",
      page.state == "done" and page.right.currentWidget() is page.gallery_panel
      and page.texts()["view"] == "list"
      and groups == [session.find(i).name for i in expected]
      and listed.item_rows() == sum(len(page._gallery_items(i)) for i in expected))
bar = page.texts()["bar"]
check("its bar has no pages and no Done with, but a way back to the summary",
      bar["back"] and not bar["done_with"] and not bar["pages"]
      and page.texts()["gallery"][0] == "Every author")
page.open_author(expected[-1])
check("an author picked on the left is found in the list, not opened on their own",
      page._listing and page.gallery.total == listed.item_rows())
check("the remembered view is left as it was", settings.get("review", "view", None) == "grid")
page.close_review_list()
check("Back to the summary", page.right.currentIndex() == page_module.STATES.index("done")
      and not page._listing and page.texts()["view"] == "grid")
page.open_review_list()
page.reopen()
check("Reopen review from the list goes back to the authors' galleries",
      page.state == "reviewing" and not page._listing
      and page.right.currentWidget() is page.gallery_panel and page.gallery.paged)

print("-- a review without a key --")
# a week on: each of them has published one more
for n, author in enumerate(AUTHORS):
    steam.works[author].insert(0, item(f"95{n}", author, 0.01))
steam.has_key = False
page.start_scan()
check("a keyless scan finishes too", wait_for(lambda: page.state == "reviewing"))
check("its authors say their lists are incomplete",
      all(page.authors.list.model_.row(i).meta.endswith("list incomplete")
          for i in page.authors.list.model_.ids()))
answers["confirm"] = False
before = len(asked)
page.finish_review()
keyless = asked[before]
check("Finish review warns before moving a visit date past what Steam left out, "
      "and Cancel is the default",
      keyless.cancel_is_default() and "without a Steam key" in keyless.note_text())
check("cancelled, nothing is written and the review stays open", page.state == "reviewing"
      and not page._writing)
answers["confirm"] = True
steam.has_key = True

print("-- Steam stops answering, and carrying on --")
steam.fail = {AUTHORS[2]}            # Cat, the third in the scan's order (Dan, Ann, Cat…)
steam.calls.clear()
page.start_scan()
check("the scan stops", wait_for(lambda: page.state == "stopped"))
texts = page.texts()
check("at the author it was on, and says so",
      texts["stopped"][0].startswith("The scan stopped at author ") and texts["carry"]
      .startswith("Carry on from author "))
check("with the reason in plain words", texts["stopped"][1].startswith(
    "Steam did not answer: api.steampowered.com — timed out after 3 tries."))
check("and the last log lines", page.excerpt.lines()[-1].message.startswith("stopped at Cat")
      and page.excerpt.lines()[-2].kind == "error")
check("the status line and the journal say it failed",
      svc.journal.recent(1)[0].kind == "scan.failed"
      and page.subtitle().startswith("5 wallpapers to scan · Scan stopped at"))
check("the sidebar says stopped", page.nav_state().text == "stopped")
asked_before = list(steam.calls)
steam.fail.clear()
page.carry_on()
check("carrying on finishes the scan", wait_for(lambda: page.state == "reviewing"))
check("starting at the author it stopped at, asking nobody twice",
      steam.calls[len(asked_before):][0] == AUTHORS[2]
      and len(steam.calls[len(asked_before):]) == 5 - asked_before.index(AUTHORS[2]))
check("and is journalled as the count it carried on",
      svc.journal.recent(1)[0].kind in ("count.clean", "count.problems"))

print("-- a cancel --")
steam.on_call = lambda id64: page.flow.cancel() if id64 == AUTHORS[0] else None
page.start_scan()
check("a cancelled scan stops quietly, and can carry on",
      wait_for(lambda: page.state == "stopped") and page.texts()["stopped"][0].startswith(
          "The scan was stopped at author") and page.texts()["carry"]
      and svc.journal.recent(1)[0].kind == "scan.stopped")
steam.on_call = None

print("-- a restore while a scan runs --")
gate = threading.Event()
steam.calls.clear()
steam.on_call = lambda id64: gate.wait(5)
page.start_scan()
wait_for(lambda: steam.calls)
page._forget_review()
gate.set()
check("the scan is dropped: its job ends stopped, and its late result is ignored",
      wait_for(lambda: not svc.jobs.is_running("review"))
      and wait_for(lambda: page.flow is None and page.state == "empty", 3000)
      and svc.jobs.last_finished("review").result == "stopped")
time.sleep(0.3)
app.processEvents()
check("so the page stays empty, asking for a new scan", page.state == "empty"
      and page.session is None)
steam.on_call = None

print("-- the author list's keys --")
picked: list = []
names = AuthorList()
names.chosen.connect(picked.append)
names.model_.set_rows([(str(i), author_row(rf.SessionAuthor(id=str(i), name=f"a{i}", new=1)))
                       for i in range(3)])
names.resize(260, 300)
names.show()
names.setCurrentIndex(names.model_.index(1))
check("moving to an author with the keys opens them, as a click does",
      wait_for(lambda: picked == ["1"], 2000))
picked.clear()
for r in (2, 0, 1, 2):
    names.setCurrentIndex(names.model_.index(r))
check("but only where the selection comes to rest",
      wait_for(lambda: picked == ["2"], 2000) and picked == ["2"])
picked.clear()
names.select("0")
app.processEvents()
time.sleep(0.35)
app.processEvents()
check("putting the highlight back does not open anyone again", picked == [])
names.close()

print("-- Review settings --")
rows = rs.scope_rows({"all": 12547, "new": 3366}, "folder:gone")
check("the source list: Wallpaper Engine's folders with their counts, then the three across them",
      [r[1] for r in rows] == ["Your folders", "all", "new", "gone (not found)", "",
                               "All folders", "Not in any folder", "Everything you have"]
      and rows[1][3] == 12547)
check("before the folders are read, the current choice stands in for them",
      [r[1] for r in rs.scope_rows(None, "folder:new")][:2] == ["Your folders", "new"])
check("a key is 32 of 0–9 and A–F; empty is fine",
      rs.key_problem("") is None and rs.key_problem("0123456789abcdef" * 2) is None
      and rs.key_problem("not-a-key") is not None)
form = rs.ReviewSettingsDialog(rs.read_settings(settings), None, folders={"new": 3366},
                               tester=lambda value: "Steam accepts the key", embedded=True)
form.key.edit.setText("not-a-key")
check("a key that cannot be one keeps Save off", not form.is_valid())
form.key.edit.setText("")
form.mirror.set_path(str(TMP / "data" / "authors_backup"))
form.revalidate()
check("the backup folder itself cannot be its own second copy",
      form.problem(form.mirror) is not None and not form.is_valid())
form.mirror.set_path(str(TMP / "second"))
form.mode.set_current_index(1)
form.revalidate()
before = rs.read_settings(settings)
changed = rs.save_settings(settings, before, form.values())
check("saving writes what changed, and only that",
      changed == {"subscribe", "mirror"} and settings.get("review", "subscribe", None) == rs.BY_PAGE
      and settings.get("review", "backup_mirror", None) == str(TMP / "second"))
key = "".join("0123456789ABCDEF"[(i * 5) % 16] for i in range(32))
changed = rs.save_settings(settings, rs.read_settings(settings),
                           rs.ReviewSettings("folder:new", rs.BY_PAGE, key, str(TMP / "second")))
check("a key is saved through the secrets store, not the settings file",
      changed == {"key"} and secrets.get(secrets.STEAM_API_KEY) == key
      and key not in json.dumps(settings._data))
form.key.edit.setText(key)
form.test()
check("Test asks off the window's thread and says what came back",
      wait_for(lambda: form.key.verdict.text() == "Steam accepts the key"))
secrets.put(secrets.STEAM_API_KEY, "")
settings.set("review", "subscribe", rs.BY_STEAM)
settings.set("review", "backup_mirror", "")

print("-- the authors database --")
with AuthorsStore(DB) as now_store:
    now_store.snapshot(force=True)
    count_then = now_store.count()
    now_store.apply([now_store.plan_create("Late", "76561198000000999")])
dialog = rs.AuthorsDialog(settings, None, embedded=True, answer=answer)
facts = rs.read_store(dialog.store)
dialog.take(facts)
check("the dialog counts the authors and lists the backups, newest first",
      facts.count == count_then + 1 and len(facts.backups) >= 2
      and facts.backups[0].taken >= facts.backups[-1].taken)
oldest = len(facts.backups) - 1
dialog.table.selectRow(oldest)
before = len(asked)
dialog.restore_selected()
confirm = asked[before]
check("restoring asks first, as a destructive confirmation naming the count",
      confirm.is_destructive() and confirm.confirm_button().text()
      == f"Restore {fmt.count(facts.backups[oldest].count)} authors")
check("and is done on a thread, after which the page knows to read it again",
      wait_for(lambda: dialog.restored))
with AuthorsStore(DB) as back:
    check("the database is the backup again", back.count() == facts.backups[oldest].count)
page.edit_authors = lambda: None

print()
host.close()
print("PASSED %d/%d" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
