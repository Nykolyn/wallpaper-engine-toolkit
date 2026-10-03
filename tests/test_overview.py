"""Overview: what each card, tile, row and monitor says, where a click goes,
and that building the page reads nothing from the disk.

Run it directly (needs Qt, but no windows on screen):

    .venv\\Scripts\\python.exe tests\\test_overview.py

The mapping from the sources (Snapshot readings, jobs, the journal, the
tracker's monitors) to words is tested through the page's plain functions;
the page itself is built on services of its own under a temporary folder,
with a feed that reads nothing and a Snapshot whose worker reads nothing.
"""
from __future__ import annotations

import builtins
import io
import os
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

TMP = Path(tempfile.mkdtemp(prefix="wallpaper_overview_test_"))
# Before any app module: the data folder resolves when they are imported.
os.environ["WALLPAPER_TOOLKIT_DATA"] = str(TMP / "data")
(TMP / "data").mkdir(parents=True, exist_ok=True)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# The labels carry ≈, · and —, which a Windows console's code page (cp1252 on
# CI) cannot always print; a check must not fail for the way its name is shown.
sys.stdout.reconfigure(errors="replace")

from PySide6.QtCore import QObject, QPoint, Qt, Signal                   # noqa: E402
from PySide6.QtTest import QTest                                         # noqa: E402
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget         # noqa: E402

app = QApplication(sys.argv)

from app import animations, external, services, theme                   # noqa: E402
from app.pages import overview as ov                                     # noqa: E402
from app.pages.overview import (                                         # noqa: E402
    CardText, OverviewPage, TileText, activity_row, job_tile, loop_sentence, loop_subtitle,
    monitor_view, monitor_views, playlist_card, reserve_card, review_card, review_tile,
    rotation_card, rotator_tile, tracker_tile,
)
from app.services import snapshot as snapshot_module                     # noqa: E402
from app.services.activity import Entry                                  # noqa: E402
from app.services.jobs import JobCenter                                  # noqa: E402
from app.services.logstore import LogTail                                # noqa: E402
from app.services.snapshot import (                                      # noqa: E402
    LAST_RUN, PLAYLIST, RESERVE, REVIEW, ROTATION, PlaylistProgress, Reading, ReserveCounts,
    ReviewState, RotationCounts, RunSummary,
)
from app.settings import Settings                                        # noqa: E402
from app.ui.kit import format as fmt                                     # noqa: E402

theme.apply(app)
animations.ENABLED = True
# A finished job asks the Snapshot to count the Rotator's folders again; with
# no config that would be this machine's own myprojects. Here it reads nothing.
snapshot_module.compute = lambda keys, data_dir: {}

NB = fmt.NBSP
NOW = datetime(2026, 9, 19, 13, 44)             # a Saturday
results: list[bool] = []


def check(label: str, condition: bool) -> None:
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label)


def wait(ms: int) -> None:
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def wait_for(condition, ms: int = 3000) -> bool:
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.005)
    return condition()


def raises(fn, kind) -> bool:
    try:
        fn()
    except kind:
        return True
    return False


def read(value=None, *, at=1.0, error="", reason="") -> Reading:
    return Reading(value, at, error, reason)


RESERVE_COUNTS = ReserveCounts(folders=33421, never_used=8204, will_reset=False, batch=1000,
                               path="X:\\reserve")
ROTATION_COUNTS = RotationCounts(folders=1000, protected=3, moved_today=0, path="X:\\myprojects")
PLAYLIST_LIVE = PlaylistProgress(monitor="Monitor1", seen=4, total=201, remaining=197, percent=2,
                                 started="2026-09-18 09:03:00", finish_estimate="21 Sep 09:10",
                                 live=True, from_engine=True)
LAST_RUN_CLEAN = RunSummary(number=38, id="a1b2c3d4", started=datetime(2026, 9, 19, 13, 21),
                            result="clean", moved=1000, returned=998, duplicates=3, failed=0,
                            finished=datetime(2026, 9, 19, 13, 38))
REVIEW_OPEN = {"scanned": "2026-09-18T09:10:00", "scope": "new", "since": "2026-09-13",
               "items": 89, "finished": None,
               "authors": [{"name": f"Author {n}", "new": 7, "done": n < 2} for n in range(12)]}


# ---- the cards -----------------------------------------------------------------------------

print("-- the cards --")
check("before its first reading a card shimmers", reserve_card(Reading()).loading
      and rotation_card(Reading()).loading and playlist_card(Reading()).loading
      and review_card(Reading(), NOW).loading)
text = reserve_card(read(RESERVE_COUNTS))
check("RESERVE: the folders and how many were never used, and a click opens the Rotator",
      text == CardText(33421, f"8{NB}204 never used", None, target="rotator"))
text = reserve_card(read(RESERVE_COUNTS, error="the disk went away", reason="missing"))
check("a count read before but not now is the last known, in text.lo",
      text.value == 33421 and text.tone == "lo" and text.caption.endswith("· last known"))
text = reserve_card(read(None, error="The reserve folder is not set", reason="unset"))
check("with the reserve unset, it says to set it in Settings, as a link that goes there",
      text.empty and text.caption == "set the reserve folder in Settings" and text.link
      and text.target == "settings")
text = reserve_card(read(None, at=None, error="The reserve folder is not set", reason="unset"))
check("and so it does when the very first reading failed (no shimmer for ever)",
      text.empty and not text.loading and text.link)
check("a reserve not found says so, and links to Settings too",
      reserve_card(read(None, error="x", reason="missing")).caption
      == "reserve folder not found — check Settings")
check("an unreadable history is said as such, and is not a link",
      reserve_card(read(None, error="x", reason="unreadable"))
      == CardText(caption="the rotation history could not be read", target="rotator"))
check("never a 0 for a count that is not known",
      all(reserve_card(read(None, error="x", reason=r)).value is None
          for r in ("unset", "missing", "unreadable", "error")))
check("IN ROTATION: folders in myprojects",
      rotation_card(read(ROTATION_COUNTS)) == CardText(1000, "in myprojects", target="rotator"))
check("and what today's runs swapped in, only when they did",
      rotation_card(read(RotationCounts(1000, 3, 412, "X:\\m"))).caption
      == "in myprojects · 412 swapped today")
check("myprojects unset links to Settings",
      rotation_card(read(None, error="x", reason="unset")).caption
      == "set the myprojects folder in Settings")
check("PLAYLIST: the leading monitor's count and what is left",
      playlist_card(read(PLAYLIST_LIVE))
      == CardText(f"4 / 201", "Monitor1 leading · 197 to go", None, target="tracker"))
off = PlaylistProgress(**{**PLAYLIST_LIVE.__dict__, "live": False})
check("with Wallpaper Engine not running, the last known count in text.lo",
      playlist_card(read(off)) == CardText("4 / 201", "Monitor1 leading · last known", "lo",
                                           target="tracker"))
done = PlaylistProgress(**{**PLAYLIST_LIVE.__dict__, "seen": 201, "remaining": 0})
check("a finished playlist turns green", playlist_card(read(done)).tone == "ok"
      and playlist_card(read(done)).caption == "Monitor1 leading · all shown")
check("no playlist counted yet is an empty card, not 0 / 0",
      playlist_card(read(None)) == CardText(caption="no playlist counted yet", target="tracker"))
text = review_card(read(REVIEW_OPEN), NOW)
check("NEW SINCE LAST REVIEW: in warn when there are some, from how many authors",
      text == CardText(89, "from 12 authors · not reviewed", "warn", target="review"))
check("with no scan yet, it says so", review_card(read(None), NOW)
      == CardText(caption="no scan yet", target="review"))
finished = {**REVIEW_OPEN, "finished": "2026-09-19T12:10:00"}
check("once reviewed, what is new is not known until the next scan: no number",
      review_card(read(finished), NOW).empty
      and review_card(read(finished), NOW).caption == "reviewed 12:10 · no scan since")
nothing = {**REVIEW_OPEN, "items": 0, "authors": []}
check("a scan that found nothing is a plain 0, and when it looked",
      review_card(read(nothing), NOW) == CardText(0, "nothing new · scanned Fri 09:10",
                                                  target="review"))
check("a summary that does not hold a count is said to be unreadable, not 0",
      review_card(read({"scope": "new"}), NOW).caption == "the last review could not be read"
      and review_card(read(None, error="bad json", reason="unreadable"), NOW).caption
      == "the last review could not be read")

print("-- review_last.json --")
state = ReviewState.from_json(REVIEW_OPEN)
check("the summary's authors, and those not gone through yet",
      (state.items, state.authors, state.waiting) == (89, 12, 10)
      and state.since.isoformat() == "2026-09-13" and state.scanned == datetime(2026, 9, 18, 9, 10))
check("a count of authors instead of a list is read too, waiting not known",
      ReviewState.from_json({"items": 3, "authors": 2}).authors == 2
      and ReviewState.from_json({"items": 3, "authors": 2}).waiting is None)
check("anything of the wrong kind is not known, never 0",
      ReviewState.from_json({"items": "89", "authors": True, "scanned": 5}) == ReviewState()
      and ReviewState.from_json(["not", "a", "dict"]) == ReviewState())


# ---- the loop -------------------------------------------------------------------------------

print("-- the loop --")
ticks = [0.0]
center = JobCenter(clock=lambda: ticks[0], wall=lambda: datetime(2026, 9, 19, 13, 41),
                   throttle=0)
job = center.start("rotator", "Run 39")
job.update("step 2 of 4 — moving 1 000 folders into myprojects", 412, 1000)
tile = rotator_tile(job, read(LAST_RUN_CLEAN), NOW)
check("a running rotation: its phase in accent, its bar, its count, accented",
      tile == TileText("Step 2 of 4 — moving 1 000 folders into myprojects", "accent", 0.412,
                       False, "accent", f"412 / 1{NB}000", True))
for n in range(1, 6):
    ticks[0] = float(n)
    job.update(done=412 + 10 * n)
check("and, once its own pace says, the time left",
      rotator_tile(job, read(LAST_RUN_CLEAN), NOW).meta.startswith(f"462 / 1{NB}000 · ≈")
      and rotator_tile(job, read(LAST_RUN_CLEAN), NOW).meta.endswith(" left"))
uncounted = center.start("rotator", "Deleting 3 duplicates")
check("a job not counted yet sweeps its bar", job_tile(uncounted).indeterminate
      and job_tile(uncounted).meta == "")
check("with none running, the next run is ready after a clean one",
      rotator_tile(None, read(LAST_RUN_CLEAN), NOW)
      == TileText("Ready for run 39", "mid", meta="run 38 clean · 13:38"))
problems = RunSummary(**{**LAST_RUN_CLEAN.__dict__, "result": "problems", "failed": 2})
check("a run with problems says how many, in warn",
      rotator_tile(None, read(problems), NOW) == TileText("Run 38 had 2 problems", "warn",
                                                          meta="13:38"))
check("a failed or stopped run says so",
      rotator_tile(None, read(RunSummary(**{**LAST_RUN_CLEAN.__dict__, "result": "failed"})),
                   NOW).tone == "danger"
      and rotator_tile(None, read(RunSummary(**{**LAST_RUN_CLEAN.__dict__, "result": "stopped"})),
                       NOW).status == "Run 38 was stopped")
check("before the first run: no run yet; before the history is read: a dash",
      rotator_tile(None, read(None), NOW).status == "No run yet"
      and rotator_tile(None, Reading(), NOW).status == fmt.DASH)
check("the Tracker: counting down, how many of how many, the day it runs out",
      tracker_tile(read(PLAYLIST_LIVE), NOW)
      == TileText("Counting the playlist down", "mid", 4 / 201, meta="4 of 201 shown · ≈21 Sep"))
check("not running: the last known count, said so",
      tracker_tile(read(off), NOW) == TileText("Wallpaper Engine is not running", "lo", 4 / 201,
                                               meta="4 of 201 shown · last known"))
not_open = PlaylistProgress(**{**off.__dict__, "from_engine": False})
check("counted by the probe, not live means nothing of it is on screen",
      tracker_tile(read(not_open), NOW).status == "Nothing from the playlist on screen")
check("a finished playlist: time to rotate, in ok",
      tracker_tile(read(done), NOW) == TileText("Playlist finished — time to rotate", "ok", 1.0,
                                                bar_tone="ok", meta="all 201 shown"))
check("no playlist: said, and no bar", tracker_tile(read(None), NOW)
      == TileText("No playlist counted yet", "lo"))
check("Review: the authors still waiting, in warn, and the items since the last visit",
      review_tile(None, read(REVIEW_OPEN), NOW)
      == TileText("10 authors waiting", "warn", meta="89 items since 13 Sep"))
check("reviewed: when, and the last scan",
      review_tile(None, read(finished), NOW)
      == TileText("Reviewed 12:10", "mid", meta="last scan Fri 09:10"))
check("nothing found, or no scan yet",
      review_tile(None, read(nothing), NOW).status == "Nothing new"
      and review_tile(None, read(None), NOW) == TileText("No scan yet", "lo"))
scan = center.start("review", "Scan")
scan.update("scanning 34 of 118 authors", 34, 118)
check("a Review scan running takes its tile", review_tile(scan, read(REVIEW_OPEN), NOW).active
      and review_tile(scan, read(REVIEW_OPEN), NOW).status == "Scanning 34 of 118 authors")
check("the loop's subtitle: the run and when it started",
      loop_subtitle(job, read(LAST_RUN_CLEAN), NOW) == "run 39 · started 13:41"
      and loop_subtitle(uncounted, read(None), NOW) == "deleting 3 duplicates · started 13:41")
check("with nothing running, the last run; or none yet; or nothing while reading",
      loop_subtitle(None, read(LAST_RUN_CLEAN), NOW) == "last run 38 · 13:21"
      and loop_subtitle(None, read(None), NOW) == "no run yet"
      and loop_subtitle(None, Reading(), NOW) == "")
check("the sentence: what the next run draws (manual: never 'when the playlist runs out')",
      loop_sentence(read(RESERVE_COUNTS))
      == f"The next run draws 1{NB}000 at random from the 8{NB}204 never used.")
resets = ReserveCounts(**{**RESERVE_COUNTS.__dict__, "never_used": 640, "will_reset": True})
check("and the history-reset wording when too few were never used",
      "resets on the next run" in loop_sentence(read(resets)) and "640" in loop_sentence(read(resets)))
check("unset, it says where to set the folders; while counting it says nothing",
      "Settings" in loop_sentence(read(None, error="x", reason="unset"))
      and loop_sentence(Reading()) == "")


# ---- rows and monitors -----------------------------------------------------------------------

print("-- rows and monitors --")
row = activity_row(Entry(datetime(2026, 9, 19, 13, 42), "rotator", "duplicates.set_aside",
                         "3 duplicates set aside", "moved to X:\\duplicates", "Duplicated"), NOW)
check("an entry today: its time, the tool's glyph, title over detail, its chip",
      (row.when, row.icon, row.title, row.meta, row.chips)
      == ("13:42", "rotator", "3 duplicates set aside", "moved to X:\\duplicates",
          (("Duplicated", None),)))
row = activity_row(Entry(datetime(2026, 9, 18, 9, 10), "review", "count.failed", "Count failed",
                         chip="Sparkly"), NOW)
check("earlier in the week the day; a failure's glyph in danger; an unknown chip left out",
      row.when == "Fri 09:10" and row.icon_tone == "danger" and row.chips == ())
check("a tool this build does not know keeps the glyph's room, empty",
      activity_row(Entry(NOW, "somewhere", "x.y", "Something"), NOW).icon == "")


def progress(monitor, seen=4, total=201, live=True, *, title="Paper Lanterns at Dusk",
             since="2026-09-19 13:30:00", from_engine=True, anchor="", from_rotation=False):
    return SimpleNamespace(monitor=monitor, seen=seen, total=total, current_title=title,
                           current_since=since, live=live, from_engine=from_engine,
                           current=f"X:/projects/{monitor.lower()}-folder/scene.pkg",
                           anchor=anchor, from_rotation=from_rotation)


views = monitor_views([progress("Monitor2", 2), progress("Monitor1", from_rotation=True)], None, NOW)
check("the leading monitor first, marked, then the others as summaries",
      [(v.name, v.state) for v in views] == [("Monitor1", "leading"), ("Monitor2", "summary")])
check("an explicit choice of the leading monitor wins",
      monitor_views([progress("Monitor2", 2), progress("Monitor1", from_rotation=True)],
                    "Monitor2", NOW)[0].name == "Monitor2")
view = views[0]
check("a live monitor: its title, how long it has shown, its count, its folder's preview",
      (view.title, view.shown_for, view.position, view.total, view.preview)
      == ("Paper Lanterns at Dusk", 14 * 60.0, 4, 201, "X:\\projects\\monitor1-folder")
      and view.note == "")
view = monitor_view(progress("Monitor1", live=False), True, NOW)
check("Wallpaper Engine not running: the last known, and no time on screen made up",
      view.shown_for is None and view.note == "last known · Wallpaper Engine is not running"
      and view.title == "Paper Lanterns at Dusk")
check("nothing counted: no cards", monitor_views([], None, NOW) == [])


# ---- the log file, followed -----------------------------------------------------------------

print("-- LogTail --")
log_file = TMP / "tail.log"
log_file.write_bytes(b"".join(f"13:00:{n:02d}\tmoved\tf{n}\n".encode() for n in range(50)))
tail = LogTail(log_file, 10)
first = tail.read()
check("the first read is the last lines", [m for _, _, m in first] == [f"f{n}" for n in range(40, 50)]
      and first[0] == ("13:00:40", "moved", "f40") and not tail.restarted)
with open(log_file, "ab") as f:
    f.write(b"13:01:00\tfail\tlate one\n13:01:01\tmov")
check("then only what was written since, whole lines only", tail.read()
      == [("13:01:00", "fail", "late one")])
with open(log_file, "ab") as f:
    f.write(b"ed\tthe rest\n")
check("and a line cut short is taken once it is whole", tail.read()
      == [("13:01:01", "moved", "the rest")])
check("nothing new is nothing", tail.read() == [])
log_file.write_bytes(b"13:02:00\tstep\tbegun again\n")
check("a file begun again is read from its end again, and says so",
      tail.read() == [("13:02:00", "step", "begun again")] and tail.restarted)
check("a file that is not there has no lines", LogTail(TMP / "nothing.log", 5).read() == [])


# ---- the page ------------------------------------------------------------------------------

class Feed(QObject):
    """A TrackerFeed that reads nothing and remembers being asked to look."""
    updated = Signal()
    config_changed = Signal()

    def __init__(self):
        super().__init__()
        self.results = []
        self.looked = 0

    def refresh(self):
        self.looked += 1


class Host(QWidget):
    def __init__(self, w=1280, h=800):
        super().__init__()
        self.resize(w, h)
        self.column = QVBoxLayout(self)
        self.move(300, 200)             # off the offscreen pointer at (0, 0)


settings = Settings({})
svc = services.Services(data_dir=TMP / "data", settings=settings)
services.install(svc)
feed = Feed()
feed.results = [progress("Monitor1", from_rotation=True), progress("Monitor2", 2)]
for n in range(3):
    svc.journal.add("rotator", "run.clean", f"Run {n + 1} finished", "1 000 moved in",
                    ts=datetime(2026, 9, 19, 10 + n, 0))

print("-- no disk from the constructor --")
GUARDED = [(builtins, "open"), (io, "open"), (os, "stat"), (os, "lstat"), (os, "scandir"),
           (os, "listdir"), (os.path, "isdir"), (os.path, "isfile"), (os.path, "exists"),
           (Path, "stat"), (Path, "exists"), (Path, "is_dir"), (Path, "is_file"),
           (Path, "iterdir"), (Path, "read_text"), (Path, "open")]
on_gui_thread: list = []
originals = {}


def guard(owner, name):
    real = getattr(owner, name)
    originals[(owner, name)] = real

    def guarded(*args, **kwargs):
        if threading.current_thread() is threading.main_thread():
            on_gui_thread.append(f"{getattr(owner, '__name__', owner)}.{name}")
            raise OSError(f"{name} called on the GUI thread")
        return real(*args, **kwargs)
    setattr(owner, name, guarded)


for owner, name in GUARDED:
    guard(owner, name)
try:
    check("the guard does catch a GUI-thread call",
          raises(lambda: os.path.isdir(str(TMP)), OSError)
          and raises(lambda: (TMP / "x").exists(), OSError) and len(on_gui_thread) == 2)
    on_gui_thread.clear()
    page = OverviewPage(svc, feed, settings=settings, now=lambda: NOW)
finally:
    for (owner, name), real in originals.items():
        setattr(owner, name, real)
check(f"building the page reads nothing from the disk ({on_gui_thread} were read)",
      on_gui_thread == [])
check("and it has not read the journal yet", page.activity_rows() == [])

host = Host()
host.column.addWidget(page)
host.show()
page.on_shown()
check("shown, it reads the journal: the newest first",
      wait_for(lambda: [r.title for r in page.activity_rows()]
               == ["Run 3 finished", "Run 2 finished", "Run 1 finished"]))

print("-- the page says what the services say --")
check("the header's subtitle is the long date", page.subtitle() == "Saturday 19 September, 13:44")
check("with nothing read yet, the cards shimmer",
      all(card.card_state() == "loading" for card in page.cards.values()))
svc.snapshot.put(RESERVE, RESERVE_COUNTS)
svc.snapshot.put(ROTATION, None, error="The myprojects folder is not set", reason="unset")
svc.snapshot.put(LAST_RUN, LAST_RUN_CLEAN)
svc.snapshot.put(PLAYLIST, PLAYLIST_LIVE)
svc.snapshot.put(REVIEW, REVIEW_OPEN)
reserve = page.cards[RESERVE]
check("RESERVE shows its count as read", reserve.card_state() in ("default", "hover")
      and reserve.value() == f"33{NB}421" and reserve.caption() == f"8{NB}204 never used")
rotation = page.cards[ROTATION]
check("IN ROTATION unset: a dash, and a link to Settings",
      rotation.value() == fmt.DASH and rotation.caption() == "set the myprojects folder in Settings"
      and rotation.caption_is_link())
check("NEW SINCE LAST REVIEW in warn", page.cards[REVIEW].value() == "89"
      and page.cards[REVIEW]._value.property("tone") == "warn")
check("the tiles, from the same readings",
      page.tiles["rotator"].texts()["status"] == "Ready for run 39"
      and page.tiles["tracker"].texts()["meta"] == "4 of 201 shown · ≈21 Sep"
      and page.tiles["review"].texts()["status"] == "10 authors waiting"
      and not any(t.active() for t in page.tiles.values()))
check("the loop's subtitle and sentence", page.loop_title.subtitle() == "last run 38 · 13:21"
      and page.sentence.text() == f"The next run draws 1{NB}000 at random from the 8{NB}204 never used.")
check("a card per monitor, the leading one first",
      [c.texts()["name"] for c in page.monitor_cards] == ["Monitor1", "Monitor2"]
      and page.monitor_cards[0].texts()["badge"] == "LEADING"
      and page.monitor_cards[0].texts()["meta"] == "14 min in"
      and not page.no_monitors.isVisibleTo(page))
feed.results = []
feed.updated.emit()
check("no monitors: the column says no playlist is counted yet",
      page.monitor_cards == [] and page.no_monitors.isVisibleTo(page))
feed.results = [progress("Monitor1", from_rotation=True)]
feed.updated.emit()

print("-- a rotation runs --")
run = services.begin("rotator", "Run 39", activity="run")
run.update("step 2 of 4 — moving 1 000 folders into myprojects", 412, 1000)
for n in range(3):
    run.log("moved", f"123450{n:04d}")
run.log("fail", "Glass Orchard — file in use")
wait(60)
rotator_tile_widget = page.tiles["rotator"]
check("its tile is accented and its dot pulses", rotator_tile_widget.active()
      and rotator_tile_widget.texts()["dot"] and rotator_tile_widget.texts()["status_tone"] == "accent"
      and rotator_tile_widget.texts()["bar"] == 0.412)
check("the loop's subtitle names the run", page.loop_title.subtitle().startswith("run 39 · started "))
check("the log follows the run's own file: its lines, live, named by the tool",
      wait_for(lambda: [line.message for line in page.log.model().lines()][-2:]
               == ["1234500002", "Glass Orchard — file in use"])
      and page.log.live() and page.log.title() == "Log"
      and page.log.file_text().startswith("rotator/")
      and page.log.toolTip().startswith("writing to rotator/"))
check("collapsed, it fills its column and keeps the newest lines in view, with the problem badge",
      not page.log.expanded() and page.log.fills() and page.log.view.isVisibleTo(page)
      and page.log.badge_text() == "1" and not page.log.footer_shown()
      and page.log.view.indexAt(QPoint(4, 4)).data(Qt.DisplayRole).endswith("Glass Orchard — file in use"))
run.log("dupe", "Paper Crane — already in the reserve")
check("a line written later arrives within a second or so",
      wait_for(lambda: page.log.model().lines()[-1].message == "Paper Crane — already in the reserve",
               2500) and page.log.badge_text() == "2")
page.log.toggle()
check("opened, it shows the switch and the footer, in the same height",
      page.log.expanded() and page.log.footer_shown() and page.log.fills())
page.log.toggle()
run.finish("clean", "Moved 1000, returned 998", title="Run 39 finished",
           detail="1 000 moved in · 998 returned")
wait(60)
check("finished: the tile is not accented any more, the log not live",
      not rotator_tile_widget.active() and not page.log.live()
      and page.log.file_text().startswith("rotator/")
      and not page.log.toolTip().startswith("writing"))
check("and the journal's new entry is the first row",
      page.activity_rows()[0].title == "Run 39 finished"
      and page.activity_rows()[0].icon == "rotator" and len(page.activity_rows()) == 4)

print("-- where a click goes --")
went: list[str] = []
page.navigate.connect(went.append)
for key in ("rotator", "tracker", "review"):
    QTest.mouseClick(page.tiles[key], Qt.LeftButton)
check("each tile opens its tool's page", went == ["rotator", "tracker", "review"])
went.clear()
page.tiles["tracker"].setFocus(Qt.TabFocusReason)
QTest.keyClick(page.tiles["tracker"], Qt.Key_Return)
QTest.keyClick(page.tiles["review"], Qt.Key_Space)
check("and so do Enter and Space on it", went == ["tracker", "review"])
check("with the keyboard on it, it wears the focus ring", page.tiles["review"].focus_visible()
      or page.tiles["tracker"].focus_visible())
went.clear()
for key in (RESERVE, ROTATION, PLAYLIST, REVIEW):
    QTest.mouseClick(page.cards[key], Qt.LeftButton)
check("a card opens where its number comes from, an unset folder opens Settings",
      went == ["rotator", "settings", "tracker", "review"])
went.clear()
open_rotator = [b for b in page.findChildren(ov.GhostButton) if b.text() == "Open Rotator →"][0]
open_rotator.click()
index = page.activity_model.index(1, 0)
page.activity.clicked.emit(index)
check("'Open Rotator →' and a row of the journal go to their pages",
      went == ["rotator", "rotator"])

opened: list = []
real_popen = external.popen
external.popen = lambda args, **kw: opened.append(list(args))
try:
    [b for b in page.findChildren(ov.GhostButton) if b.text() == "Open log folder"][0].click()
finally:
    external.popen = real_popen
check("'Open log folder' opens the logs folder in Explorer",
      opened == [["explorer", str(svc.logs.root)]])

refreshed: list = []
svc.snapshot.refresh = lambda keys=None: refreshed.append(keys)
page.header_actions()[0].click()
check("'Refresh now' reads the snapshot again and has the tracker look",
      refreshed == [None] and feed.looked == 1 and page.header_actions()[0].text() == "Refresh now")
del svc.snapshot.refresh

print("-- the minute --")
clock = [NOW]
minute_page = OverviewPage(svc, feed, settings=settings, now=lambda: clock[0])
clock[0] = NOW + timedelta(minutes=1)
minute_page._tick()
check("each minute the date moves on", minute_page.subtitle() == "Saturday 19 September, 13:45")
check("and the next tick waits for the next minute", 0 < minute_page._minute.remainingTime() <= 60_000)

print("-- empty journal --")
empty_svc = services.Services(data_dir=TMP / "empty-data", settings=settings)
bare = OverviewPage(empty_svc, None, settings=settings, now=lambda: NOW)
host.column.addWidget(bare)
bare.on_shown()
check("with nothing in the journal it says nothing has happened yet",
      wait_for(lambda: bare._activity_stack.currentWidget() is bare.activity_empty)
      and bare.activity_empty.text() == "Nothing has happened yet"
      and bare.activity_empty.property("tone") == "lo")
check("no log yet: the console says nothing is written", len(bare.log.model()) == 0
      and bare.log.file_text() == "")

print("-- fixtures --")
fixture_page = OverviewPage(services.Services(data_dir=TMP / "fixture-data", settings=settings),
                            None, settings=settings)
for state in OverviewPage.FIXTURES:
    fixture_page.load_fixture(state)
    check(f"the {state} fixture loads", fixture_page.subtitle() == "Saturday 19 September, 13:44")
fixture_page.load_fixture("empty")
check("the empty fixture is a fresh install: links to Settings, no scan, no rows",
      fixture_page.cards[RESERVE].caption() == "set the reserve folder in Settings"
      and fixture_page.cards[REVIEW].caption() == "no scan yet"
      and fixture_page.activity_rows() == [] and fixture_page.monitor_cards == [])
fixture_page.load_fixture("we-off")
check("we-off: the playlist's last known count in text.lo",
      fixture_page.cards[PLAYLIST].caption() == "Monitor1 leading · last known"
      and fixture_page.cards[PLAYLIST]._value.property("tone") == "lo"
      and fixture_page.monitor_cards[0].texts()["note"]
      == "last known · Wallpaper Engine is not running")
check("a state it does not have is refused", raises(lambda: fixture_page.load_fixture("nope"),
                                                     KeyError))

host.close()
print()
print("PASSED %d/%d" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
