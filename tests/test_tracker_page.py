"""Tracker page: what each card, fact, sentence, note and row says, where the
words come from, and that nothing on the window's thread reads the disk.

Run it directly (needs Qt, but no windows on screen):

    .venv\\Scripts\\python.exe tests\\test_tracker_page.py

The mapping from the tracker's Progress (and its countdown, and what is known
of the wallpaper on screen) to words is tested through the page's plain
functions; the page itself is built on a feed that reads nothing, a
countdown that is made up, and a list read from a cycle held in memory.
Titles and authors come from files made here, in a temporary folder; so do
the folders Mark [protected] renames. Send to Copier is tested through the
window's own wiring (`build_pages`), so a Copier that stops taking folders
from the Tracker fails here.
"""
from __future__ import annotations

import builtins
import ctypes
import io
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
from ctypes import wintypes
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

TMP = Path(tempfile.mkdtemp(prefix="wallpaper_tracker_page_test_"))
# Before any app module: the data folder resolves when they are imported.
os.environ["WALLPAPER_TOOLKIT_DATA"] = str(TMP / "data")
(TMP / "data").mkdir(parents=True, exist_ok=True)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# The labels carry ≈, ~, · and —, which a Windows console's code page (cp1252
# on CI) cannot always print; a check must not fail for the way its name is shown.
sys.stdout.reconfigure(errors="replace")

from PySide6.QtCore import QObject, Signal                               # noqa: E402
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget         # noqa: E402

app = QApplication(sys.argv)

from app import animations, external, theme                             # noqa: E402
from app.engines import wallpaper_meta as wm                             # noqa: E402
from app.engines import wallpaper_timer as wt                            # noqa: E402
from app.engines.tracker import (                                        # noqa: E402
    ANCHOR_ENGINE, ANCHOR_FILE_TIMES, ANCHOR_NONE, ANCHOR_ROTATION, MIN_SAMPLE, TIME_FMT, Cycle,
    Progress,
)
from app.engines.wallpaper_meta import Described, MetaCache, WallpaperMeta   # noqa: E402
from app.engines.wallpaper_timer import Countdown                        # noqa: E402
from app.pages.tracker import (                                          # noqa: E402
    PlaylistModel, TrackerPage, empty_text, finish_at, finish_sentence,
    header_subtitle, monitor_view, nav_state, pace_figure, playlist_rows, provenance,
    queue_words, rebuild_sentence, reveal, row_matches, when_text,
)
from app.services import snapshot as snapshot_module                     # noqa: E402
from app.settings import Settings                                        # noqa: E402
from app.tracker_feed import TRAY_MUTEX, tray_running                    # noqa: E402
from app.ui.kit import Callout, MonitorCard, MonitorView, NavState, format as fmt   # noqa: E402

theme.apply(app)
animations.ENABLED = True
snapshot_module.compute = lambda keys, data_dir: {}

NB = fmt.NBSP
NOW = datetime(2026, 9, 19, 13, 44)             # a Saturday
results: list[bool] = []


def check(label: str, condition: bool) -> None:
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label)


def wait_for(condition, ms: int = 4000) -> bool:
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


def stamp(dt: datetime) -> str:
    return dt.strftime(TIME_FMT)


def progress(monitor="Monitor1", seen=4, total=201, **kw) -> Progress:
    values = dict(cycle_id="c1", monitor=monitor, playlist="custom", seen=seen, total=total,
                  changes=seen, repeats=0, order="random", delay=10,
                  started=stamp(NOW - timedelta(minutes=42 * seen)),
                  current=f"X:/wallpapers/{monitor}_now/scene.pkg",
                  current_title="Paper Lanterns at Dusk",
                  current_since=stamp(NOW - timedelta(minutes=14)), live=True, inferred=0,
                  anchor=ANCHOR_ROTATION, gone=0, from_rotation=True, from_engine=True)
    values.update(kw)
    return Progress(**values)


def countdown(remaining=26 * 60, *, approximate=False, paused=False, active=True, known=True):
    """A 30-minute playlist's timer with `remaining` seconds left."""
    return Countdown("Monitor1", 1800.0, running=max(1800.0 - (remaining or 0), 0.0), known=known,
                     approximate=approximate, paused=paused, active=active)


KNOWN = Described(WallpaperMeta("Paper Lanterns at Dusk", "scene", "1700000001"), "Marlow", True)


# ---- the header and the sidebar --------------------------------------------------------------

print("-- the header and the sidebar --")
check("two monitors, counted in the background while the tray runs",
      header_subtitle(2, True) == "2 monitors · counting in the background")
check("and only while the window is open when it does not",
      header_subtitle(1, False) == "1 monitor · counting while this window is open")
check("no monitor, no claim", header_subtitle(0, True) == "no playlist counted yet")
check("the sidebar counts the leading monitor: 4/201 and its bar",
      nav_state(progress()) == NavState.count(4, 201) and nav_state(progress()).tone == "lo")
check("green once the playlist is done", nav_state(progress(seen=201)).tone == "ok")
check("nothing counted, nothing shown", nav_state(None) == NavState())


# ---- a monitor's card -----------------------------------------------------------------------------

print("-- a monitor's card --")
v = monitor_view(progress(), leading=True, countdown=countdown(), engine=True, described=KNOWN,
                 resolution="2560×1440", now=NOW)
check("leading: its badge, its screen, its count", v.state == "leading"
      and v.badge() == ("Leading", "accent") and v.resolution == "2560×1440"
      and v.count_text() == "4 / 201")
check("the wallpaper's title and author, Known when the database has them",
      v.title == "Paper Lanterns at Dusk" and v.author == "Marlow" and v.author_chip == "Known")
check("SHOWN FOR is the time since it came up, REMAINING the countdown",
      v.facts(NOW)[:2] == [("Shown for", "14 min"), ("Remaining", "26 min")])
check("CYCLE STARTED is the cycle's start, seen: no mark",
      v.facts(NOW)[2] == ("Cycle started", "19 Sep 10:56"))
check("a card with no note while all is well", v.note == "")
v = monitor_view(progress(), leading=True, countdown=countdown(approximate=True), engine=True,
                 now=NOW)
check("an estimated countdown is marked ≈", v.remaining_parts() == ("≈26 min", "", "text.body"))
check("without the author known, no chip and no author",
      v.author == "" and v.author_chip is None and v.title == "Paper Lanterns at Dusk")
v = monitor_view(progress(), leading=True, countdown=countdown(21 * 60 + 10, paused=True),
                 engine=True, now=NOW)
check("paused: the time left in warn, and the badge stays LEADING",
      v.remaining_parts() == ("21 min", "paused", "warn") and v.badge() == ("Leading", "accent"))
v = monitor_view(progress(live=False), leading=True, countdown=countdown(), engine=False, now=NOW)
check("Wallpaper Engine not running: — disconnected, in danger",
      v.remaining_parts() == (fmt.DASH, "disconnected", "danger"))
check("and the card keeps the last known wallpaper and count, and says so",
      v.note == "last known · Wallpaper Engine is not running" and v.shown_for is None
      and v.count_text() == "4 / 201" and v.title == "Paper Lanterns at Dusk")
v = monitor_view(progress(live=False), leading=True, countdown=None, engine=None, now=NOW)
check("before the countdown has looked, the count's own word decides",
      v.remaining_parts()[1] == "disconnected")
v = monitor_view(progress(live=False, from_engine=False), leading=True, countdown=countdown(),
                 engine=True, now=NOW)
check("the file probe not finding the playlist on screen says that instead",
      v.note == "last known · nothing from the playlist on screen"
      and v.remaining_parts() == ("26 min", "", "text.body"))
v = monitor_view(progress(), leading=True, countdown=countdown(0), engine=True, now=NOW)
check("a timer run out: any moment", v.remaining_parts()[0] == "any moment")
v = monitor_view(progress(), leading=True, countdown=countdown(None, known=False), engine=True,
                 now=NOW)
check("a countdown not known yet is a dash, not a zero", v.remaining_parts()[0] == fmt.DASH)
v = monitor_view(progress(), leading=True, countdown=countdown(active=False), engine=True, now=NOW)
check("a playlist with no timer has no time left to show", v.remaining_parts()[0] == fmt.DASH)
v = monitor_view(progress(), leading=True, countdown=countdown(89), engine=True,
                 now=NOW)
check("the time left is kept as precisely as it is written",
      v.remaining == 60.0 and monitor_view(progress(), leading=True, countdown=countdown(42.4),
                                           engine=True, now=NOW).remaining == 42.0)
other = progress("Monitor2", 2, from_rotation=False, anchor=ANCHOR_NONE)
v = monitor_view(other, leading=False, countdown=countdown(), engine=True, now=NOW)
check("a second monitor on its own playlist: a summary, not counted for rotation",
      v.state == "summary" and v.badge() is None
      and v.note == "follows its own order · not counted for rotation")
check("a second monitor on a rotation's playlist has no such note",
      monitor_view(progress("Monitor2"), leading=False, engine=True, now=NOW).note == "")
v = monitor_view(progress(anchor=ANCHOR_ENGINE), leading=True, engine=True, now=NOW)
check("a cycle Wallpaper Engine started over is dated ~",
      v.facts(NOW)[2] == ("Cycle started", "~19 Sep 10:56"))
check("and so is one dated from file times",
      monitor_view(progress(anchor=ANCHOR_FILE_TIMES), leading=True, engine=True,
                   now=NOW).cycle_text(NOW).startswith("~"))
v = monitor_view(progress(seen=201), leading=True, engine=True, now=NOW)
check("the whole playlist shown: finished, its bar in ok", v.finished() and v.bar_tone() == "ok")
check("the preview is the wallpaper's folder", monitor_view(progress(), leading=True, now=NOW)
      .preview == str(Path("X:/wallpapers/Monitor1_now")))
check("a timer state the card does not know is refused",
      raises(lambda: MonitorView("M", timer="asleep"), ValueError))


# ---- the pace ------------------------------------------------------------------------------------

print("-- the pace and the finish --")
check("the average on screen: the cycle's time over what it has shown",
      pace_figure(progress(), NOW) == ("42 min", "average on screen"))
check("≈ when the cycle's start was worked out afterwards",
      pace_figure(progress(anchor=ANCHOR_ENGINE), NOW)[0] == "≈42 min")
check("nothing shown, nothing averaged", pace_figure(progress(seen=0), NOW)
      == (fmt.DASH, "nothing shown yet this cycle"))
forty = progress(seen=40)
when = finish_at(forty, NOW)
check("the finish: the pace times what is left", when == NOW + timedelta(minutes=42 * 161))
check("the engine's Progress says the same", forty.finish_at(NOW) == when
      and forty.pace_seconds(NOW) == 42 * 60 and forty.finish_estimate is not None)
plain, rich = finish_sentence(forty, NOW, 39)
check("worded for a rotation started by hand (§7.5)",
      plain == "At this pace the playlist empties ≈24 Sep, about 06:26 — time to rotate (run 39).")
check("the date stands out, its ≈ in text.lo", theme.css("text.hi") in rich
      and theme.css("text.lo") in rich and "≈" in rich)
check("with no run known, no run number",
      finish_sentence(forty, NOW)[0].endswith("— time to rotate."))
check(f"fewer than {MIN_SAMPLE} shown: no date yet, and how many to go",
      finish_sentence(progress(), NOW)[0]
      == f"The finish is estimated once {MIN_SAMPLE} have been shown — {MIN_SAMPLE - 4} to go.")
check("the engine agrees: no estimate below the sample",
      progress().finish_at(NOW) is None and progress().finish_estimate is None)
check("shown to the end: how long it took",
      finish_sentence(progress(seen=201), NOW)[0] == "Shown to the end: all 201 in 5 d 20 h.")
check("the start is reconstructed for these anchors only",
      progress(anchor=ANCHOR_ENGINE).reconstructed_start
      and progress(anchor=ANCHOR_FILE_TIMES).reconstructed_start
      and not progress(anchor=ANCHOR_ROTATION).reconstructed_start
      and not progress(anchor=ANCHOR_NONE).reconstructed_start)


# ---- what the count rests on ---------------------------------------------------------------------

print("-- the notes: never hidden --")
notes = provenance(progress(inferred=12), NOW)
check("followed from the engine: said, with its ~ times dated from their files",
      notes == [("neutral", "Read from Wallpaper Engine's own record of the pass. 12 marked ~ "
                            "came up unwatched; their times are from the files. Cycle dated by "
                            "the rotation that built the playlist.")])
notes = provenance(progress(inferred=5, from_engine=False, anchor=ANCHOR_NONE), NOW)
check("counted by the file probe: said, with its ~ restored from access times",
      notes == [("neutral", "Counted by watching which file Wallpaper Engine holds open. 5 marked "
                            "~ were restored from file access times, not watched. Cycle counted "
                            "from when tracking started.")])
restart = progress(anchor=ANCHOR_ENGINE, restarted_at=stamp(NOW.replace(hour=11, minute=2)),
                   restarted_from="81/195", inferred=21)
notes = provenance(restart, NOW)
check("started over: in warn, with what the count had reached",
      notes[0] == ("warn", "Wallpaper Engine started the playlist over at 11:02 — the previous "
                           "count had reached 81 / 195.")
      and "Wallpaper Engine's record of the new pass (~)" in notes[1][1])
notes = provenance(progress(restarted_at=stamp(NOW - timedelta(days=1)), restarted_from="195/195"),
                   NOW)
check("a pass run to its end is no warning: the next began after all were shown",
      notes[0][0] == "neutral" and notes[0][1].startswith(
          "Wallpaper Engine began the next pass at Fri 13:44, after all 195 of the last one"))
notes = provenance(progress(gone=14), NOW)
check("deleted wallpapers are not counted, and it says so in warn",
      notes[0] == ("warn", "14 wallpapers deleted since the playlist was built, not counted."))
check("a dating from file times is marked ~",
      provenance(progress(anchor=ANCHOR_FILE_TIMES), NOW)[-1][1].endswith(
          "Cycle dated from the break in the file times (~)."))


# ---- the playlist ----------------------------------------------------------------------------------

print("-- the playlist: grouping, order, filter, authors --")
ITEMS = [f"X:/myprojects/w{n:02d}/scene.pkg" for n in range(1, 11)]
cycle = Cycle(monitor="Monitor1", playlist="custom", started=stamp(NOW - timedelta(hours=3)),
              items=list(ITEMS),
              seen={ITEMS[4]: stamp(NOW - timedelta(minutes=100)),
                    ITEMS[1]: stamp(NOW - timedelta(minutes=58)),
                    ITEMS[7]: stamp(NOW - timedelta(minutes=14))},
              current=ITEMS[7], current_since=stamp(NOW - timedelta(minutes=14)), order="random",
              inferred=[ITEMS[4]], missing=[ITEMS[9]])
rows, in_order = playlist_rows(cycle, NOW)
shown = [r for r in rows if r.group == "shown"]
queue = [r for r in rows if r.group == "queue"]
check("shown newest first, the one on screen at the top",
      [r.item for r in shown] == [ITEMS[7], ITEMS[1], ITEMS[4]] and shown[0].on_screen
      and not any(r.on_screen for r in shown[1:]))
check("each shown for as long as it stayed: until the next came up",
      [r.shown_for for r in shown] == [14 * 60, 44 * 60, 42 * 60])
check("~ where its own time or the next one's is reconstructed",
      [r.shown_approx for r in shown] == [False, False, True] and shown[2].inferred)
check("a random playlist has no queue: its own order, unnumbered",
      [r.item for r in queue] == [ITEMS[i] for i in (0, 2, 3, 5, 6, 8)] and not in_order
      and all(r.queue is None for r in queue) and [r.number for r in queue] == [1, 3, 4, 6, 7, 9])
check("a deleted wallpaper is in neither group", all(r.item != ITEMS[9] for r in rows))
check("titles start from the path: a scene is its folder",
      [r.title for r in shown] == ["w08", "w02", "w05"])
rows_off, _ = playlist_rows(cycle, NOW, live=False)
check("not live: nothing is on screen, and the last one's time is not known",
      not any(r.on_screen for r in rows_off) and rows_off[0].item == ITEMS[7]
      and rows_off[0].shown_for is None)
sorted_cycle = Cycle(monitor="Monitor1", playlist="custom", started=cycle.started,
                     items=list(ITEMS), seen=dict(cycle.seen), current=ITEMS[7],
                     current_since=cycle.current_since, order="sorted")
rows_sorted, in_order = playlist_rows(sorted_cycle, NOW)
check("a sorted playlist: the queue in playing order from the one on screen, numbered from 1",
      in_order and [r.item for r in rows_sorted if r.group == "queue"]
      == [ITEMS[i] for i in (8, 9, 0, 2, 3, 5, 6)]
      and [r.queue for r in rows_sorted if r.group == "queue"] == list(range(1, 8)))
check("the queue's words: today's", queue_words(True) == "up next, in playing order"
      and queue_words(False) == "random order, any of these can be next")
check("no cycle, no rows", playlist_rows(None, NOW) == ([], False))

model = PlaylistModel()
model.set_playlist(rows, False, NOW)
heads = model.group_rows()
check("two groups, shown first", [model.group_at(r).key for r in heads] == ["shown", "queue"])
check("their headers count out of the whole playlist",
      model.group_note(heads[0]) == "3 of 9"
      and model.group_note(heads[1]) == "6 of 9 · random order, any of these can be next")
model_sorted = PlaylistModel()
model_sorted.set_playlist(rows_sorted, True, NOW)
check("a sorted queue says it is in playing order",
      model_sorted.group_note(model_sorted.group_rows()[1]).endswith("up next, in playing order"))
first_shown, first_queued = heads[0] + 1, heads[1] + 1


def said(row, column):
    return model.data(model.index(row, column))


check("#: a dash for what was shown, the place for what waits",
      said(first_shown, 0) == fmt.DASH and said(first_queued, 0) == "001")
check("STATE: on screen, the time it came up (~ when rebuilt), queued",
      said(first_shown, 5) == "on screen" and said(first_shown + 1, 5) == "12:46"
      and said(first_shown + 2, 5) == "~12:04" and said(first_queued, 5) == "queued")
check("SHOWN: how long, ~ when worked out from a rebuilt time",
      said(first_shown, 4) == "14 min" and said(first_shown + 2, 4) == "~42 min"
      and said(first_queued, 4) == fmt.DASH)
check("what was shown is drawn faint, bar the one on screen",
      not model.row_dimmed(shown[0]) and model.row_dimmed(shown[1]) and not model.row_dimmed(queue[0]))
check("the thumb is the wallpaper's folder", model.thumb_source(shown[0]) == str(Path(ITEMS[7]).parent))
check("a day further back is written as the day",
      when_text(NOW - timedelta(days=9), NOW) == "10 Sep" and when_text(None, NOW) == fmt.DASH
      and when_text(NOW - timedelta(days=1), NOW) == "Fri 13:44")

for row, (author, title) in zip(rows, [("Marlow", "Harbour Lights"), ("tidewright", "Paper Moth"),
                                       ("Marlow", "Salt Field"), ("", "Glass River"),
                                       ("Quill", "Harbour Fog")]):
    row.author, row.title = author, title
check("the filter looks in the title, without case, and not in the author",
      row_matches(rows[0], "harbour") and row_matches(rows[4], "FOG")
      and not row_matches(rows[1], "tide") and not row_matches(rows[2], "harbour")
      and row_matches(rows[2], ""))
found = Described(WallpaperMeta("Cedar Linen", "video"), "orbit_lab", True)
check("a row takes what was read, once", rows[5].describe(found) and not rows[5].describe(found)
      and (rows[5].title, rows[5].author, rows[5].kind, rows[5].known)
      == ("Cedar Linen", "orbit_lab", "video", True))


# ---- the empty states ------------------------------------------------------------------------------

print("-- the empty states --")
title, body, action, target = empty_text(
    "Cannot read Wallpaper Engine's config.json: it is not there", False, "X:/x/config.json")
check("config.json not found: where to choose it", title == "Wallpaper Engine's config.json was not found"
      and action == "Choose it in Settings" and target == "settings")
title, *_ = empty_text("Cannot read Wallpaper Engine's config.json: Expecting value", True,
                       "X:/x/config.json")
check("config.json unreadable: said as that", title == "Wallpaper Engine's config.json could not be read")
no_playlist = "No active playlist in config.json. Apply a playlist in Wallpaper Engine and restart it."
check("no playlist on any monitor", empty_text(no_playlist, True)[0] == "No playlist is running")
check("and with Wallpaper Engine not running, that first",
      empty_text(no_playlist, False)[0] == "Wallpaper Engine is not running")
check("nothing looked at yet", empty_text(None, None)[0] == "Looking at Wallpaper Engine…")


# ---- the kit's parts it needs ---------------------------------------------------------------------

print("-- the kit: a finished card, a flash, a title-only note, a thumb asked once --")


class Host(QWidget):
    def __init__(self, w=1280, h=860):
        super().__init__()
        self.resize(w, h)
        self.column = QVBoxLayout(self)
        self.move(300, 200)             # off the offscreen pointer at (0, 0)


card = MonitorCard(detail=True)
card.set_view(MonitorView("Monitor1", "leading", position=200, total=201, title="x"), NOW)
check("a card short of the end: no ok edge", card.tone() is None and not card.texts()["finished"])
card.set_view(MonitorView("Monitor1", "leading", position=201, total=201, title="x"), NOW)
check("the whole playlist shown: the ring closes in ok, the count and the edge go green",
      card.texts()["ring"] == "done" and card.tone() == "ok"
      and card._position.property("tone") == "ok" and card.texts()["finished"])
check("and the count that moved flashes",
      wait_for(lambda: "color:" in card._position.styleSheet(), 500))
card.set_view(MonitorView("Monitor1", "leading", position=1, total=201, title="x"), NOW)
check("a new cycle takes the green away again", card.tone() is None
      and card._position.property("tone") == "hi" and card.texts()["ring"] == "1%")
bare = Callout(tone="ok", title="Whole playlist shown")
check("a note with a title alone shows no empty body", bare._body.isHidden())
bare.set_body("with words")
check("and shows one once it has words", not bare._body.isHidden())


class CountingLoader(QObject):
    local_done = Signal(str, object)

    def __init__(self):
        super().__init__()
        self.asked = 0

    def forget_local(self, key):
        pass

    def request_local(self, key, path, box=None):
        self.asked += 1
        return True


from app.ui.kit import Thumb        # noqa: E402

loader = CountingLoader()
thumb = Thumb("wide", loader=loader)
thumb.set_source("X:/somewhere")
thumb.set_source("X:/somewhere")
check("a thumb given the same folder again does not read it again", loader.asked == 1)


# ---- the engine's parts ----------------------------------------------------------------------------

print("-- the countdown as a reader, and the tray's mutex --")
saved = TMP / "saved_timer.json"
saved.write_text(json.dumps({"saved_at": 1.0, "engine_started": 5.0, "clocks": {}}), "utf-8")


class Files:
    def __init__(self):
        self.refreshed = 0
        self.config, self.config_version, self.state_version = {}, 0, 0
        self.state_ok, self.decks, self.state_written = False, {}, None

    def refresh(self):
        self.refreshed += 1


files = Files()
reader = wt.WallpaperTimer("X:/we/config.json", find_engine=lambda: None, files=files,
                           save_path=None, restore_path=saved, open_memory=None,
                           follow_files=False, find_displays=lambda m: {})
check("a reader starts from the tray's file and saves nowhere",
      reader._restored is not None and reader.save_path is None)
reader.tick()
check("and leaves the files to whoever follows them", files.refreshed == 0 and reader.engine is None)
writer = wt.WallpaperTimer("X:/we/config.json", find_engine=lambda: (4, 5.0), files=files,
                           save_path=TMP / "own.json", open_memory=None,
                           find_displays=lambda m: {}, measure_screens=lambda r, i: {})
writer.tick()
check("the tray's own timer still follows them, and restores from where it saves",
      files.refreshed == 1 and writer.restore_path == TMP / "own.json" and writer.engine == (4, 5.0))

check("the tray's mutex is its name", TRAY_MUTEX == "Local\\WallpaperEngineToolkitTracker")
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.CreateMutexW.restype = wintypes.HANDLE
k32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
k32.CloseHandle.argtypes = [wintypes.HANDLE]
handle = k32.CreateMutexW(None, False, TRAY_MUTEX)
check("a running tray is seen", tray_running() is True)
k32.CloseHandle(handle)
check("the answer is a yes or a no", isinstance(tray_running(), bool))


print("-- titles, types and authors, read from files --")
folder = TMP / "lib" / "1700000001"
folder.mkdir(parents=True)
(folder / "project.json").write_text(json.dumps(
    {"title": "Harbour Lights Loop", "type": "Scene", "workshopid": "1700000001"}), "utf-8-sig")
(folder / "scene.pkg").write_bytes(b"")
item = str(folder / "scene.pkg").replace("\\", "/")
meta = wm.read_meta(item)
check("project.json gives the title, the type and the workshop id",
      meta == WallpaperMeta("Harbour Lights Loop", "scene", "1700000001", True))
broken = TMP / "lib" / "handmade"
broken.mkdir()
(broken / "project.json").write_text("{not json", "utf-8")
check("an unreadable one falls back to the folder's name, and says so",
      wm.read_meta(str(broken / "scene.pkg")) == WallpaperMeta("handmade", readable=False))
check("a video's fallback is its file's name", wm.fallback_title("X:/a/b/clip_01.mp4") == "clip_01")

# Made-up accounts: shaped like steamID64s, but below the first real one
# (76561197960265728), so nobody's.
cache_db = TMP / "steam_cache.sqlite"
with sqlite3.connect(cache_db) as conn:
    conn.execute("CREATE TABLE cache (kind TEXT, key TEXT, fetched INTEGER, payload TEXT, "
                 "PRIMARY KEY (kind, key))")
    conn.execute("INSERT INTO cache VALUES ('item.2', '1700000001', 1, ?)",
                 (json.dumps({"id": "1700000001", "ok": True, "creator": "76561190000000001"}),))
    conn.execute("INSERT INTO cache VALUES ('item.2', '1700000002', 1, ?)",
                 (json.dumps({"id": "1700000002", "ok": True, "creator": "76561190000000002"}),))
    conn.execute("INSERT INTO cache VALUES ('profile', '76561190000000001', 1, ?)",
                 (json.dumps({"id64": "76561190000000001", "name": "Marlow",
                              "vanity": "marlowmakes"}),))
names = wm.author_names(["1700000001", "1700000002", "1700000003", "not-an-id"], cache_db)
check("the Steam cache names the author of what it has seen, whatever its age",
      names == {"1700000001": wm.AuthorName("Marlow", ("76561190000000001", "marlowmakes"))})
authors_db = TMP / "authors.sqlite"
with sqlite3.connect(authors_db) as conn:
    conn.execute("CREATE TABLE authors (key TEXT PRIMARY KEY COLLATE NOCASE, name TEXT, "
                 "added TEXT, visited TEXT)")
    conn.execute("INSERT INTO authors VALUES ('MarlowMakes', 'Marlow', NULL, NULL)")
check("the authors database knows them under either key, without case",
      wm.known_authors(["76561190000000001", "marlowmakes"], authors_db) == {"marlowmakes"})
check("no database, nobody known — and none is made",
      wm.known_authors(["x"], TMP / "none.sqlite") == set()
      and not (TMP / "none.sqlite").exists() and wm.author_names(["1"], TMP / "no.sqlite") == {})
before = authors_db.stat().st_mtime_ns
cache = MetaCache(cache_path=cache_db, db_path=authors_db)
told = cache.describe([item, str(broken / "scene.pkg")])
check("a session's cache describes each wallpaper: title, author, Known",
      told[item] == Described(meta, "Marlow", True)
      and told[str(broken / "scene.pkg")].author == "")
check("and never writes the authors database", authors_db.stat().st_mtime_ns == before)
check("what was read is kept", cache.cached(item) == told[item])
cache.forget()
check("until it is forgotten", cache.cached(item) is None)


print("-- reveal in Explorer --")
opened: list = []
real_popen = external.popen
external.popen = lambda args, **kw: opened.append(args)
try:
    check("a file that is there: Explorer with it selected",
          reveal(item) is None and opened[-1] == f'explorer /select,"{Path(item)}"')
    check("one that is gone: the nearest folder still there",
          reveal(str(folder / "gone" / "x.mp4")) is None and opened[-1] == f'explorer "{folder}"')
    check("nothing left at all: said, not opened",
          reveal("Q:/nowhere/at/all.mp4").startswith("Nothing of this path is left on disk")
          and len(opened) == 2)
finally:
    external.popen = real_popen


# ---- the page ------------------------------------------------------------------------------------

print("-- the page: no disk on the window's thread --")


class Feed(QObject):
    """A TrackerFeed that reads nothing and remembers being asked to look."""
    updated = Signal()
    config_changed = Signal()

    def __init__(self):
        super().__init__()
        self.results = []
        self.looked = 0
        self.config_path = "X:/we/config.json"
        self.error = None
        self.atime_ok = True
        self.files = Files()
        self.asked: list[tuple] = []        # (what, its done), answered by the test

    def refresh(self):
        self.looked += 1

    def reset(self, monitor, done=None):
        self.asked.append(("reset", monitor, done))

    def rebuild(self, done=None):
        self.asked.append(("rebuild", done))


class FakeTimer:
    engine = (4, 5.0)
    rects = {"Monitor1": (0, 0, 2560, 1440), "Monitor2": (2560, 0, 4480, 1080)}

    def tick(self):
        return {"Monitor1": countdown(), "Monitor2": countdown(7 * 60)}


LIST = [f"X:/myprojects/w{n:03d}/scene.pkg" for n in range(1, 202)]
LEAD_CYCLE = Cycle(monitor="Monitor1", playlist="custom", started=stamp(NOW - timedelta(hours=3)),
                   items=list(LIST), seen={LIST[k]: stamp(NOW - timedelta(minutes=42 * (3 - k)))
                                           for k in range(4)},
                   current=LIST[3], current_since=stamp(NOW - timedelta(minutes=14)),
                   order="random")
loaded: list[str] = []


def load(monitor):
    loaded.append(monitor)
    return LEAD_CYCLE


class ReadNothing(MetaCache):
    """Titles and authors made up in memory: the list's own reading is tested above."""

    def read(self, items):
        with self._lock:
            for i in items:
                self._meta.setdefault(i, WallpaperMeta(f"Title {i[-13:-10]}", "scene",
                                                       i[-13:-10]))

    def resolve_authors(self, items):
        with self._lock:
            for i in items:
                wid = self._meta[i].workshop_id
                n = int(wid) if wid.isdigit() else 0
                self._authors[wid] = wm.AuthorName("Marlow" if n % 2 else "Quill", ("k" + wid,))
                self._known["k" + wid] = n % 4 == 1


settings = Settings({})
feed = Feed()
GUARDED = [(builtins, "open"), (io, "open"), (os, "stat"), (os, "lstat"), (os, "scandir"),
           (os, "listdir"), (os.path, "isdir"), (os.path, "isfile"), (os.path, "exists"),
           (Path, "stat"), (Path, "exists"), (Path, "is_dir"), (Path, "is_file"),
           (Path, "iterdir"), (Path, "read_text"), (Path, "read_bytes"), (Path, "open")]
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


def unguard():
    for (owner, name), real in originals.items():
        setattr(owner, name, real)
    originals.clear()


host = Host()
for owner, name in GUARDED:
    guard(owner, name)
try:
    check("the guard does catch a GUI-thread call",
          raises(lambda: os.path.isdir(str(TMP)), OSError) and len(on_gui_thread) == 1)
    on_gui_thread.clear()
    measured: list[bool] = []

    def measure(folder: str) -> int:
        measured.append(threading.current_thread() is threading.main_thread())
        return 1_000_000 * (int(folder[-3:]) if folder[-3:].isdigit() else 1)

    page = TrackerPage(feed, None, settings=None, now=lambda: NOW,
                       make_timer=lambda f: FakeTimer(), meta=ReadNothing(), load=load,
                       measure=measure)
    host.column.addWidget(page)
    host.show()
    feed.results = [progress(current=LIST[3]),
                    progress("Monitor2", 2, from_rotation=False, anchor=ANCHOR_NONE,
                             current_title="Tram Window, Late Rain")]
    feed.updated.emit()
    page.countdowns.start()
    page._read_list()
    check("the list is read off the window's thread", wait_for(lambda: page.model.item_rows() == 201))
    check("and its titles and authors fill in", wait_for(
        lambda: page.model.item_at(page.model.group_rows()[0] + 1).author != ""))
    check("then each folder's size, measured on the reader's thread",
          wait_for(lambda: all(r.size is not None for r in page.model.items()))
          and measured and not any(measured))
    page.filter.setText("title 00")
    page.jump.set_current_index(1)
    page.countdowns.tick()
    feed.updated.emit()
    app.processEvents()
finally:
    unguard()
check(f"none of it read the disk on the window's thread ({on_gui_thread})", on_gui_thread == [])
page.filter.setText("")

print("-- the page says what the tracker says --")
said = page.texts()
check("the header: two monitors, counted while the window is open (no tray in this test)",
      said["subtitle"] == "2 monitors · counting while this window is open")
check("the lead card: LEADING, its screen, count, ring and facts",
      said["lead"]["badge"] == "LEADING" and said["lead"]["resolution"] == "2560×1440"
      and said["lead"]["count"] == "4 / 201" and said["lead"]["ring"] == "2%"
      and said["lead"]["facts"] == "14 min | 26 min | 19 Sep 10:56")
check("its title and author as read, Known in a chip",
      wait_for(lambda: page.texts()["lead"]["meta"] in ("Marlow", "Quill"))
      and page.texts()["lead"]["title"].startswith("Title"))
check("the second monitor as a summary, not counted for rotation",
      len(said["summaries"]) == 1 and said["summaries"][0]["name"] == "Monitor2"
      and said["summaries"][0]["note"] == "follows its own order · not counted for rotation"
      and said["summaries"][0]["count"] == "2 / 201")
check("the pace: 42 min on screen on average, no date before 20",
      said["pace"]["value"] == "42 min" and said["pace"]["sentence"].startswith(
          "The finish is estimated once 20"))
check("the footer counts the playlist", said["footer"] == "201 wallpapers")
check("the sidebar counts it too", page.nav_state() == NavState.count(4, 201))
check("the one on screen is the row selected",
      [r.on_screen for r in page.table.selected_items()] == [True])

rows_before = page.model.item_rows()
page.filter.setText("Title 00")
check("the filter narrows the rows", 0 < page.model.item_rows() < rows_before)
page.filter.setText("")
check("and empty again shows them all", page.model.item_rows() == rows_before)
check("no author column and no author list: # · WALLPAPER · TYPE · SIZE · SHOWN · STATE",
      [c.title for c in page.model.columns] == ["#", "Wallpaper", "Type", "Size", "Shown",
                                                "State", ""]
      and not hasattr(page, "author_pick"))

print("-- # is the playlist's own order --")
playlist_order = [r.item for r in page.model.items()]


def shown_order() -> list:
    return [page.model.item_at(r).item for r in range(page.model.rowCount())
            if page.model.item_at(r) is not None]


natural = shown_order()
page.table._header_clicked(1)                    # by title
check("sorted by title, the rows move", page.model.sort_column() == 1 and shown_order() != natural)
page.table._header_clicked(0)                    # #
check("#'s title brings the playlist's own order back",
      page.model.sort_column() == -1 and shown_order() == natural)
page.table._header_clicked(0)
check("and clicked again it stays there, rather than sorting the other way",
      page.model.sort_column() == -1 and shown_order() == natural)
check("its title is lit while nothing else sorts", page.model.columns[0].natural)
queued = [r for r in page.model.items() if r.group == "queue"]
check("SIZE says each folder's size", page.model.cell(queued[0], 3) == fmt.size(queued[0].size))
page.table._header_clicked(3)
sizes = [page.model.item_at(r).size for r in range(page.model.rowCount())
         if page.model.item_at(r) is not None and page.model.item_at(r).group == "queue"]
check("and sorts by it, smallest first", sizes == sorted(sizes) and len(set(sizes)) > 1)
page.table._header_clicked(0)
page.jump.set_current_index(1)
app.processEvents()
check("Queue brings the queue's header to the top",
      page.table.rowAt(0) == page.model.group_rows()[1])
page.table.verticalScrollBar().setValue(0)
app.processEvents()
check("scrolled back up, the switch says Shown", page.jump.current_index() == 0)

check("the lead card's menu: its list is below, it is on the tray icon, a new cycle",
      page.menu_actions("Monitor1") == [("Show its playlist below", True, False),
                                        ("Show on the tray icon", True, False),
                                        ("New cycle…", False, True)])
check("the second's offers its list and the tray icon",
      page.menu_actions("Monitor2")[:2] == [("Show its playlist below", False, True),
                                            ("Show on the tray icon", False, True)])
loaded.clear()
page.show_list("Monitor2")
check("its list is read, and the footer says whose",
      wait_for(lambda: loaded == ["Monitor2"]) and wait_for(
          lambda: page.texts()["footer"] == "201 wallpapers · Monitor2"))
page.show_list("Monitor1")
wait_for(lambda: page.model.item_rows() == 201)

import app.pages.tracker as tracker_page        # noqa: E402

revealed: list[str] = []
real_reveal = tracker_page.reveal
tracker_page.reveal = lambda item: revealed.append(item)
try:
    first = page.model.index(page.model.group_rows()[0] + 1, 1)
    page._row_clicked(first)
    page._row_clicked(first)         # the double-click's second half
    check("a click opens the wallpaper's folder once, off the window's thread",
          wait_for(lambda: revealed == [LIST[3]]) and len(revealed) == 1)
    page._revealed_last = ("", 0.0)
    page._row_clicked(page.model.index(page.model.group_rows()[0], 1))
    app.processEvents()
    check("a group's header opens nothing", len(revealed) == 1)
finally:
    tracker_page.reveal = real_reveal

feed.results = [progress(seen=201, current=LIST[3]), feed.results[1]]
feed.updated.emit()
said = page.texts()
check("finished: the ring closes, the count and card go green, the sidebar too",
      said["lead"]["finished"] and said["lead"]["ring"] == "done"
      and page.nav_state().tone == "ok")
check("and a note says it is time, with the way to the Rotator",
      said["pace"]["notes"][0][:2] == ("ok", "Whole playlist shown — time to rotate")
      and page.pace.callouts[0]._actions.count() == 1)

feed.results = []
feed.error = "Cannot read Wallpaper Engine's config.json: it is not there"
feed.updated.emit()
check("no config.json: the empty state says where to choose it",
      page.texts()["empty"] == "Wallpaper Engine's config.json was not found"
      and page.nav_state() == NavState())
feed.error = None

print("-- the lead, and the page's dialogs --")
lead_settings = Settings({})
page2 = TrackerPage(feed, None, settings=lead_settings, now=lambda: NOW,
                    make_timer=lambda f: FakeTimer(), meta=ReadNothing(), load=load)
feed.results = [progress(), progress("Monitor2", 2, from_rotation=False, anchor=ANCHOR_NONE)]
feed.updated.emit()
page2.set_lead("Monitor2")
check("Show on the tray icon makes it the lead, here and for the tray",
      lead_settings.get("tracker", "primary", None) == "Monitor2"
      and page2.lead_card.view().name == "Monitor2")
form = page2.settings_dialog(embedded=True)
check("Playlist settings: where it reads, rebuild, a new cycle per monitor, and Done",
      [f.accessibleName() for f in form.fields()]
      == ["Where the count comes from", "Rebuild from file times", "New cycle"]
      and form.save_button().text() == "Done" and form.cancel_button().isHidden())
check("its facts point to Settings for what is set there",
      page2._settings_facts().startswith("Also checks every 5 min · lead monitor: Monitor2"))

print("-- a new cycle and a rebuild: asked of the feed, answered later --")
page2._answer = lambda dialog: False
asked_before = len(feed.asked)
check("cancelled, the feed is not asked",
      not page2.new_cycle("Monitor1") and not page2.rebuild() and len(feed.asked) == asked_before)
page2._answer = lambda dialog: True
followed: list[str] = []
check("New cycle… asks the feed, whose worker owns the tracker, and does not wait",
      page2.new_cycle("Monitor2", then=lambda: followed.append("Monitor2"))
      and feed.asked[-1][:2] == ("reset", "Monitor2") and followed == [])
feed.asked[-1][2](None)
check("what asked follows once the feed answers", followed == ["Monitor2"])
page2.new_cycle("Monitor1", then=lambda: followed.append("Monitor1"))
feed.asked[-1][2](OSError("tracker.json is read-only"))
check("one that failed says so, and nothing follows",
      page2.messages[-1] == ("danger", "Monitor1's count could not start again: "
                                       "tracker.json is read-only")
      and followed == ["Monitor2"])
said_back: list[str] = []
check("Rebuild asks the feed too", page2.rebuild(said_back.append)
      and feed.asked[-1][0] == "rebuild" and said_back == [])
feed.asked[-1][1](3)
check("and says what came of it in the words it always had",
      said_back == ["Recovered 3 wallpapers shown while nothing was watching."])
check("nothing to recover, or a failure, said as plainly",
      rebuild_sentence(0) == "Nothing to recover: the counts already match the file times."
      and rebuild_sentence(OSError("the disk is asleep"))
      == "The counts could not be rebuilt: the disk is asleep")


print("-- fixtures --")
fixture_page = TrackerPage(None, None, settings=Settings({}))
for state in TrackerPage.FIXTURES:
    fixture_page.load_fixture(state)
    frame = fixture_page.frame_fixture(state)
    check(f"the {state} fixture loads, with a frame state to go with it",
          frame is not None and frame["frame"] in
          ("running", "idle", "we-off", "empty"))
fixture_page.load_fixture("tracking")
said = fixture_page.texts()
check("tracking is frame 02: 4 / 201, 26 min left, a second monitor",
      said["lead"]["count"] == "4 / 201" and said["lead"]["facts"].split(" | ")[1] == "26 min"
      and said["summaries"][0]["name"] == "Monitor2" and said["lead"]["chip"] == "KNOWN")
check("its table: four shown, the rest queued", fixture_page.model.group_note(
    fixture_page.model.group_rows()[0]) == "4 of 201")
fixture_page.load_fixture("paused")
check("paused: the time left in warn", fixture_page.texts()["lead"]["facts"].split(" | ")[1]
      == "21 min paused")
fixture_page.load_fixture("disconnected")
check("disconnected: last known, — disconnected",
      fixture_page.texts()["lead"]["facts"].split(" | ")[1] == "— disconnected"
      and fixture_page.texts()["lead"]["note"] == "last known · Wallpaper Engine is not running"
      and fixture_page.table.selected_items() == [])
fixture_page.load_fixture("finished")
check("finished: green", fixture_page.texts()["lead"]["finished"]
      and fixture_page.frame_fixture("finished")["nav"]["tracker"] == {
          "kind": "count", "done": 201, "total": 201})
fixture_page.load_fixture("restarted")
check("restarted: the warning and the ~ start",
      fixture_page.texts()["pace"]["notes"][0][0] == "warn"
      and fixture_page.texts()["lead"]["facts"].endswith("~19 Sep 07:20"))
fixture_page.load_fixture("single-monitor")
check("one monitor, sorted: no summary, the queue numbered in playing order",
      fixture_page.texts()["summaries"] == [] and fixture_page.model.in_order
      and fixture_page.model.group_note(fixture_page.model.group_rows()[1]).endswith(
          "up next, in playing order"))
fixture_page.load_fixture("we-off")
check("we-off: Wallpaper Engine is not running",
      fixture_page.texts()["empty"] == "Wallpaper Engine is not running")
check("a state it does not have is refused", raises(lambda: fixture_page.load_fixture("nope"),
                                                     KeyError))


# ---- a row's actions: Send to Copier, Mark [protected] ------------------------------------------

print("-- a row's actions: which rows offer what --")
from app.engines.rotator.config import Config              # noqa: E402
from app.pages.tracker import (                             # noqa: E402
    ACTIONS, DELETE, MARKED, OFFER, PROTECT, SEND, STALE_NOTE, PlaylistRow, action_cell,
    protect_state, protected_name,
)
from app.settings import DEFAULT_COPIER_COUNT              # noqa: E402

DEST = "X:/Steam/steamapps/common/wallpaper_engine/projects/myprojects"
WORKSHOP = "X:/Steam/steamapps/workshop/content/431960"


def in_dest(name: str) -> str:
    return str(Path(DEST) / name)


check("a folder directly in myprojects is offered Mark [protected]",
      protect_state(in_dest("wallpaper_0012"), DEST) == OFFER
      and protect_state(in_dest("wallpaper_0012"), DEST + "/") == OFFER)
check("a Workshop folder never is", protect_state(str(Path(WORKSHOP) / "1700000005"), DEST) == "")
check("nor one further down, nor anything when myprojects is not set",
      protect_state(str(Path(DEST) / "sub" / "wallpaper_0012"), DEST) == ""
      and protect_state(in_dest("wallpaper_0012"), "") == "" and protect_state("", DEST) == "")
check("a folder already [protected], in whatever case, shows that it is",
      protect_state(in_dest("[protected] wallpaper_0012"), DEST) == MARKED
      and protect_state(in_dest("[PROTECTED] wallpaper_0012"), DEST) == MARKED)
check("the name it is marked under: [protected] and the old name",
      protected_name(in_dest("wallpaper_0012")) == "[protected] wallpaper_0012")


def keys(cell) -> list:
    return [b.key if b is not None else None for b in cell.buttons]


plain_row = PlaylistRow("x", "queue", 1, folder=in_dest("wallpaper_0012"))
offered = action_cell(plain_row, OFFER)
check("a myprojects row: Send to Copier, Mark [protected]…, Delete…, each saying what it does",
      keys(offered) == [SEND, PROTECT, DELETE]
      and [b.tip for b in offered.buttons] == ["Send to Copier", "Mark [protected]…", "Delete…"]
      and all(b.enabled and not b.mark for b in offered.buttons))
check("a row not offered Mark: its slot empty so the buttons line up",
      keys(action_cell(plain_row, "")) == [SEND, None, DELETE])
workshop_row = PlaylistRow("w", "queue", 1, folder=str(Path(WORKSHOP) / "1700000005"))
check("a Workshop row's Delete says it unsubscribes",
      action_cell(workshop_row, "").buttons[2].tip == "Unsubscribe and delete…")
check("while Delete runs, it is off",
      not action_cell(plain_row, OFFER, deleting=True).buttons[2].enabled)
marked = action_cell(plain_row, MARKED).buttons[1]
check("an already protected row: a lock that is no button, and says what it means",
      marked.mark and marked.icon == "lock" and marked.tone == "accent.hover"
      and "the Rotator leaves it in myprojects" in marked.tip)
renaming = action_cell(plain_row, OFFER, renaming=True).buttons[1]
check("while the rename runs, Mark is off", renaming.key == PROTECT and not renaming.enabled)
stale_row = PlaylistRow("x", "queue", 1, folder=in_dest("[protected] wallpaper_0012"), stale=True)
stale = action_cell(stale_row, MARKED).buttons[1]
check("one marked here: the lock in warn, and its tip says the playlist entry is broken",
      stale.mark and stale.tone == "warn"
      and "stops working until the next rotation" in stale.tip)

actions_model = PlaylistModel()
actions_model.destination = DEST
actions_rows = [PlaylistRow("a", "queue", 1, folder=in_dest("wallpaper_0001"), title="Tide"),
                PlaylistRow("b", "queue", 2, folder=str(Path(WORKSHOP) / "1700000005"),
                            title="Moth"),
                PlaylistRow("c", "queue", 3, folder=in_dest("[protected] wallpaper_0003"),
                            title="Pine"),
                stale_row]
actions_model.set_playlist(actions_rows, False, NOW)
check("the table's last column holds them, with no title and no sorting",
      ACTIONS == len(actions_model.columns) - 1 and actions_model.columns[ACTIONS].title == ""
      and not actions_model.columns[ACTIONS].sortable)
check("each row as its folder says: offered, Workshop, protected, marked here",
      [keys(actions_model.cell(r, ACTIONS)) for r in actions_rows]
      == [[SEND, PROTECT, DELETE], [SEND, None, DELETE], [SEND, MARKED, DELETE],
          [SEND, MARKED, DELETE]])
check("the row marked here says so under its title, in warn",
      actions_model.cell(stale_row, 1).sub == STALE_NOTE
      and actions_model.cell(stale_row, 1).sub_tone == "warn"
      and actions_model.cell(actions_rows[0], 1).sub == ""
      and STALE_NOTE.startswith("playlist entry broken until the next rotation"))


print("-- Mark [protected]: asked first, renamed off the window's thread, said --")
MYP = TMP / "myprojects"
for name in ("w_keep", "w_taken", "[protected] w_taken", "[protected] w_old", "w_busy"):
    (MYP / name).mkdir(parents=True)
    (MYP / name / "scene.pkg").write_bytes(b"")
(TMP / "workshop" / "1700000099").mkdir(parents=True)


def entry(folder: Path) -> str:
    return str(folder / "scene.pkg")


MARK_ITEMS = [entry(MYP / "w_keep"), entry(MYP / "w_taken"), entry(MYP / "[protected] w_old"),
              entry(TMP / "workshop" / "1700000099"), entry(MYP / "w_busy"),
              entry(MYP / "w_gone")]
MARK_CYCLE = Cycle(monitor="Monitor1", playlist="custom", started=stamp(NOW - timedelta(hours=1)),
                   items=list(MARK_ITEMS), seen={MARK_ITEMS[0]: stamp(NOW - timedelta(minutes=14))},
                   current=MARK_ITEMS[0], current_since=stamp(NOW - timedelta(minutes=14)),
                   order="random")
mark_feed = Feed()
mark_config = Config(source="", destination=str(MYP), duplicates="", count=1000)
mark_page = TrackerPage(mark_feed, None, config=mark_config, now=lambda: NOW,
                        make_timer=lambda f: FakeTimer(), meta=ReadNothing(),
                        load=lambda monitor: MARK_CYCLE)
host.column.addWidget(mark_page)
mark_feed.results = [progress(current=MARK_ITEMS[0], total=len(MARK_ITEMS))]
mark_feed.updated.emit()
mark_page._read_list()
check("its list is read", wait_for(lambda: mark_page.model.item_rows() == len(MARK_ITEMS)))


def mark_row(name: str):
    return next(r for r in mark_page.model.items() if Path(r.folder).name == name)


check("the page knows myprojects from the Rotator's settings",
      [mark_page.model.protect_state(mark_row(n)) for n in
       ("w_keep", "[protected] w_old", "1700000099")] == [OFFER, MARKED, ""])
asked: list = []
answer_with = [False]


def answering(dialog):
    asked.append(dialog)
    return answer_with[0]


mark_page._answer = answering
renamed_on: list[bool] = []
real_protect_folder = tracker_page.protect_folder


def recording_protect(folder):
    renamed_on.append(threading.current_thread() is threading.main_thread())
    return real_protect_folder(folder)


tracker_page.protect_folder = recording_protect
check("cancelled, nothing is renamed and nothing is said",
      not mark_page.protect(mark_row("w_keep")) and len(asked) == 1 and renamed_on == []
      and (MYP / "w_keep").is_dir() and mark_page.messages == [])
dialog = asked[0]
check("the question: neutral, the rename, what the Rotator does, and Rename",
      dialog._title.text() == "Mark this folder [protected]?"
      and dialog._body.text() == "The Rotator leaves [protected] folders in myprojects: no run "
                                 "takes this one back to the reserve."
      and dialog.listed_lines() == ["w_keep  →  [protected] w_keep"]
      and not dialog.is_destructive() and dialog.confirm_button().text() == "Rename")
check("and, plainly, what it does to Wallpaper Engine's playlist",
      dialog.note_text() == "Wallpaper Engine's playlist is not touched. Its entry for this "
                            "wallpaper keeps the old name and stops working until the next "
                            "rotation rebuilds the playlist.")
check("a Workshop folder or a protected one is not asked about",
      not mark_page.protect(mark_row("1700000099"))
      and not mark_page.protect(mark_row("[protected] w_old")) and len(asked) == 1)

answer_with[0] = True
keep = mark_row("w_keep")
on_gui_thread.clear()
for owner, name in GUARDED:
    guard(owner, name)
try:
    started = mark_page.protect(keep)
    renaming_now = not mark_page.model.cell(keep, ACTIONS).buttons[1].enabled
    finished = wait_for(lambda: not mark_page._offload.busy())
finally:
    unguard()
check("confirmed, the rename runs, Mark is off meanwhile",
      started and renaming_now and finished)
check(f"on a worker: nothing on the window's thread touched the disk ({on_gui_thread})",
      renamed_on == [False] and on_gui_thread == [])
check("the folder is renamed, and nothing else",
      (MYP / "[protected] w_keep").is_dir() and not (MYP / "w_keep").exists()
      and (MYP / "[protected] w_keep" / "scene.pkg").exists() and (MYP / "w_taken").is_dir())
keep = next(r for r in mark_page.model.items() if r.item == MARK_ITEMS[0])
check("the row follows: its new folder, the warn lock, the note under its title",
      Path(keep.folder) == MYP / "[protected] w_keep" and keep.stale
      and keys(mark_page.model.cell(keep, ACTIONS)) == [SEND, MARKED, DELETE]
      and mark_page.model.cell(keep, ACTIONS).buttons[1].tone == "warn"
      and mark_page.model.cell(keep, 1).sub == STALE_NOTE
      and mark_page.model.thumb_source(keep) == keep.folder)
check("and a toast says what changed, and that the playlist entry stops working",
      mark_page.messages[-1] == ("ok", "w_keep is now [protected] w_keep: the Rotator leaves it "
                                       "in myprojects. Wallpaper Engine's playlist still has "
                                       "the old name; that entry stops working until the next "
                                       "rotation."))
mark_page._read_list()
check("read again from tracker.json (which still has the old name), the row stays marked",
      wait_for(lambda: next(r for r in mark_page.model.items()
                            if r.item == MARK_ITEMS[0]).stale)
      and Path(next(r for r in mark_page.model.items()
                    if r.item == MARK_ITEMS[0]).folder) == MYP / "[protected] w_keep")


def failed_mark(name: str) -> tuple[str, str]:
    before = len(mark_page.messages)
    row = mark_row(name)
    mark_page.protect(row)
    wait_for(lambda: not mark_page._offload.busy() and len(mark_page.messages) > before)
    return mark_page.messages[-1] if len(mark_page.messages) > before else ("", "")


tone, words = failed_mark("w_taken")
check("the name already taken: a danger toast in plain words, nothing changed",
      tone == "danger" and words == "Could not mark w_taken [protected]: there is already a "
                                    "folder called [protected] w_taken in myprojects. Nothing "
                                    "was changed."
      and (MYP / "w_taken").is_dir() and (MYP / "[protected] w_taken").is_dir()
      and not mark_row("w_taken").stale
      and mark_page.model.cell(mark_row("w_taken"), ACTIONS).buttons[1].enabled)
tone, words = failed_mark("w_gone")
check("a folder no longer there: said so", tone == "danger"
      and words == "Could not mark w_gone [protected]: it is not in myprojects any more. "
                   "Nothing was changed.")


class InUse(PermissionError):
    winerror = 32                   # what Windows says of a folder another program holds


def refusing(error):
    def rename(self, target):
        raise error
    return rename


real_rename = Path.rename
try:
    Path.rename = refusing(InUse(13, "The process cannot access the file"))
    tone, words = failed_mark("w_busy")
    check("in use by Wallpaper Engine: said so, and the folder keeps its name",
          tone == "danger" and words.startswith("Could not mark w_busy [protected]: it is in use. "
                                                "Wallpaper Engine may be showing it")
          and words.endswith("Nothing was changed.") and (MYP / "w_busy").is_dir()
          and not mark_row("w_busy").stale)
    Path.rename = refusing(PermissionError(13, "Access is denied"))
    tone, words = failed_mark("w_busy")
    check("access denied: said so", tone == "danger"
          and "Windows denied access to it" in words and (MYP / "w_busy").is_dir())
finally:
    Path.rename = real_rename
tracker_page.protect_folder = real_protect_folder

asked.clear()
rotating = SimpleNamespace(is_running=lambda tool: tool == "rotator")
mark_page._services = SimpleNamespace(jobs=rotating)
try:
    check("while a rotation runs, not even asked: a warning says when",
          not mark_page.protect(mark_row("w_busy")) and asked == []
          and mark_page.messages[-1] == ("warn", "A rotation is running. Mark folders "
                                                 "[protected] once it has finished."))
finally:
    mark_page._services = None

copier_sent: list = []


def sent(folders):
    copier_sent.append(folders)


mark_page.copier_requested.connect(sent)
mark_page.send_to_copier(keep)
check("Send to Copier sends a renamed folder under its new name",
      copier_sent == [[keep.folder]])
check("with nobody to take it, it says so", mark_page.messages[-1]
      == ("warn", "The Copier is not there to take it."))
mark_page.copier_requested.disconnect(sent)
fixture_page.load_fixture("tracking")
before = len(fixture_page.messages)
check("a made-up state sends and renames nothing",
      not fixture_page.send_to_copier(fixture_page.model.items()[0])
      and not fixture_page.protect(fixture_page.model.items()[0])
      and len(fixture_page.messages) == before)

print("-- the marked fixture --")
fixture_page.load_fixture("marked")
fixture_rows = {r.number: r for r in fixture_page.model.items()}
check("wallpaper 2 was marked here: its row says so",
      fixture_rows[2].stale and Path(fixture_rows[2].folder).name == "[protected] wallpaper_0002"
      and fixture_page.model.cell(fixture_rows[2], ACTIONS).buttons[1].tone == "warn")
check("3 was protected already, 5 is a Workshop one, 4 is offered Mark",
      [keys(fixture_page.model.cell(fixture_rows[n], ACTIONS)) for n in (3, 5, 4)]
      == [[SEND, MARKED, DELETE], [SEND, None, DELETE], [SEND, PROTECT, DELETE]])


# ---- Send to Copier, through the window's own wiring --------------------------------------------

print("-- Send to Copier: the Copier's list, once, and Show goes there --")
from app import services as services_module                 # noqa: E402
from app.main_window import MainWindow                      # noqa: E402


class WindowFeed(Feed):
    def use_config(self, path):
        pass

    def set_heartbeat(self, seconds):
        pass


window_settings = Settings({})
window_services = services_module.Services(data_dir=TMP / "data", settings=window_settings)
window = MainWindow(settings=window_settings, feed=WindowFeed(), services_=window_services,
                    start=False, initial="tracker")
window.move(300, 200)
window.show()
app.processEvents()
tracker_in_window = window.pages["tracker"]
copier_page = window.pages["copier"]
sent_folder = str(MYP / "w_copy")
sent_row = PlaylistRow(entry(MYP / "w_copy"), "queue", 1, folder=sent_folder,
                       title="Harbour Lights")
check("the Copier takes a folder from the Tracker (build_pages wires it: whatever replaces "
      "the Copier tab must take these too)",
      tracker_in_window.send_to_copier(sent_row)
      and copier_page.folders() == [(os.path.normpath(sent_folder), str(DEFAULT_COPIER_COUNT))])
check("for the default copies, and the Tracker stays on screen",
      window.current_page() == "tracker"
      and tracker_in_window.messages[-1] == ("ok", f"“Harbour Lights” is on the Copier's list, "
                                                   f"for {DEFAULT_COPIER_COUNT} copies."))
toast = window.toasts.toasts()[-1]
check("its toast offers Show", toast.action_text() == "Show" and toast.variant() == "ok")
tracker_in_window.send_to_copier(sent_row)
check("sent again, it is not listed twice, and the toast says it is there already",
      len(copier_page.folders()) == 1
      and tracker_in_window.messages[-1] == ("info", "“Harbour Lights” is on the Copier's list "
                                                     "already."))
window.toasts.toasts()[-1]._act()
app.processEvents()
check("Show opens the Copier", window.current_page() == "copier")
check("the Copier's add_folders: one of each, its count, how many it added",
      copier_page.add_folders([sent_folder + os.sep, str(MYP / "w_two"), str(MYP / "w_two")],
                             count=5) == 1
      and copier_page.folders()[-1] == (os.path.normpath(str(MYP / "w_two")), "5"))
window.close()

host.close()
print()
print("PASSED %d/%d" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
