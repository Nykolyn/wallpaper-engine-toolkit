"""TrackerFeed: every look on its worker, none on the window's thread.

Run it directly (needs Qt, but no windows on screen):

    .venv\\Scripts\\python.exe tests\\test_tracker_feed.py

A real Tracker reads a made-up config.json and playliststate.bin in a temporary
folder that `gui_guard` treats as the wallpaper disk, with its tracker.json in a
temporary data folder treated the same way. The probe is a stand-in that says
which thread asked: the real one opens each file through CreateFileW, where the
guard cannot see it. The rest (looks asked for while one runs, the answers to a
new cycle and a rebuild, a config.json changed under a look, a look that fails,
stopping) is driven through a stand-in tracker whose looks the test holds and
lets go.
"""
from __future__ import annotations

import json
import os
import struct
import sys
import tempfile
import threading
import time
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="wallpaper_tracker_feed_test_"))
# Before any app module: the data folder resolves when they are imported.
os.environ["WALLPAPER_TOOLKIT_DATA"] = str(TMP / "data")
(TMP / "data").mkdir(parents=True, exist_ok=True)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
sys.stdout.reconfigure(errors="replace")

from PySide6.QtCore import QObject                                       # noqa: E402
import shiboken6                                                         # noqa: E402
from PySide6.QtWidgets import QApplication                               # noqa: E402

qt_app = QApplication(sys.argv)

import gui_guard                                                         # noqa: E402
import app.engines.tracker as tr                                         # noqa: E402
from app.engines import wallpaper_timer as wt                            # noqa: E402
from app.engines.wallpaper_timer import MAGIC, SECTION, EngineFiles      # noqa: E402
from app.tracker_feed import TrackerFeed                                 # noqa: E402

results: list[bool] = []


def check(label: str, condition: bool) -> None:
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label)


def wait_for(condition, ms: int = 5000) -> bool:
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        qt_app.processEvents()
        if condition():
            return True
        time.sleep(0.005)
    return condition()


def settle(ms: int) -> None:
    wait_for(lambda: False, ms)


def off_thread(fn, *args) -> None:
    """Write a fixture as Wallpaper Engine would: not from the window's thread."""
    worker = threading.Thread(target=fn, args=args)
    worker.start()
    worker.join()


# The tracker makes every path a Windows one before it looks at the file, so
# anywhere else every wallpaper reads as deleted. The counts are checked on
# Windows, where the tests run; which thread did what is checked everywhere.
COUNTS_HERE = os.name == "nt"


# ---- a Wallpaper Engine on the wallpaper disk -----------------------------------------------

WE = TMP / "wallpaper_engine"
CFG = WE / "config.json"
STATE = WE / "bin" / "playliststate.bin"
LIBRARY = TMP / "myprojects"
tr.STATE_PATH = TMP / "data" / "tracker.json"
# The tracker asks for Wallpaper Engine's process when it follows the state file:
# one running for an hour, since nothing is on screen while it is closed.
tr.wallpaper_engine_process = lambda: (4242, time.time() - 3600)


def make_items(folder: Path, n: int) -> list[str]:
    items = []
    for i in range(n):
        d = folder / f"w{i}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "wallpaper.mp4").write_bytes(b"x")
        items.append(str(d / "wallpaper.mp4").replace("\\", "/"))
    return items


def write_config(path: Path, items: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"steamuser": {"general": {"wallpaperconfig": {
        "selectedwallpapers": {"Monitor1": {"file": items[0], "playlist": {
            "items": items, "name": "custom",
            "settings": {"delay": 10, "order": "random", "mode": "timer"}}}}}}}}),
        encoding="utf-8")


def write_state(current: str, waiting: list[str]) -> None:
    def text(value: str) -> bytes:
        raw = value.encode("utf-8")
        return struct.pack("<I", len(raw)) + raw

    out = text(MAGIC) + struct.pack("<I", 1) + text(SECTION) + struct.pack("<I", 1)
    out += text("Monitor1") + struct.pack("<I", 84) + text(current) + struct.pack("<I", 0)
    out += struct.pack("<I", len(waiting))
    for item in waiting:
        out += text(item) + struct.pack("<I", 0)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_bytes(out)


ITEMS = make_items(LIBRARY, 6)
write_config(CFG, ITEMS)

# The real probe opens files through CreateFileW, which the guard cannot see.
probed_on_gui: list[bool] = []


def probe(path: str) -> bool:
    probed_on_gui.append(threading.current_thread() is threading.main_thread())
    return False


tr.is_in_use = probe

# After the app's imports: settings.py finds Steam's folders as it is imported,
# which is a matter of its own (and of a test of its own once it moves).
gui_guard.install()
gui_guard.clear()
for folder in (WE, LIBRARY, TMP / "data"):
    gui_guard.watch(folder)

print("-- a real tracker, looked at on the feed's worker --")
feed = TrackerFeed(str(CFG))
updates: list[list] = []
heard_on_gui: list[bool] = []


def heard() -> None:
    updates.append(list(feed.results))
    heard_on_gui.append(threading.current_thread() is threading.main_thread())


feed.updated.connect(heard)
check("built without a file call on the window's thread", gui_guard.violations == [])
check("it knows where it looks before it has looked", feed.config_path == str(CFG))
check("and its first look comes back by signal, to the window's thread",
      wait_for(lambda: len(updates) >= 1) and len(feed.results) == 1 and all(heard_on_gui))
first = feed.results[0] if feed.results else None
check("the playlist as config.json has it",
      first is not None and first.monitor == "Monitor1" and first.total + first.gone == 6
      and (first.gone == 0 or not COUNTS_HERE))
check("the probe ran on the worker, never here",
      not any(probed_on_gui) and (probed_on_gui or not COUNTS_HERE))
check("no state file: nothing to follow, and no error",
      feed.following is False and feed.error is None)
check("the window's copy of the files has what the worker read",
      feed.files.config_version >= 1 and isinstance(feed.files.config, dict)
      and feed.files.state_ok is False)

off_thread(write_state, ITEMS[0], ITEMS[1:])
check("a state file written is followed within a second or two",
      wait_for(lambda: feed.following and feed.results and feed.results[0].from_engine))
check("and counted from: the wallpaper on screen is the one the pass has drawn",
      feed.results and feed.results[0].current == ITEMS[0]
      and (feed.results[0].seen == 1 or not COUNTS_HERE))
check("the copy has the state file too",
      feed.files.state_ok and "Monitor1" in feed.files.decks and feed.files.state_version >= 1)

reader = wt.WallpaperTimer(feed.config_path, find_engine=lambda: None,
                           measure_screens=lambda rects, ignore: {},
                           find_displays=lambda monitors: {}, save_path=None,
                           restore_path=None, open_memory=None, files=feed.files,
                           follow_files=False)
ticked = reader.tick()
check("a countdown follows the copy, as the tray's and the page's do",
      "Monitor1" in ticked and ticked["Monitor1"].delay == 600)

seen = len(updates)
feed.refresh()
check("Refresh now: another look, and the window's thread only asked",
      wait_for(lambda: len(updates) > seen))

events: list[str] = []
feed.updated.connect(lambda: events.append("updated"))
before = feed.results[0].cycle_id
answers: list = []
answered_on_gui: list[bool] = []


def answered(answer) -> None:
    events.append("done")
    answers.append(answer)
    answered_on_gui.append(threading.current_thread() is threading.main_thread())


feed.reset("Monitor1", answered)
check("a new cycle is answered once the look after it has landed, on the window's thread",
      wait_for(lambda: answers) and answers == [None] and events[-2:] == ["updated", "done"]
      and answered_on_gui == [True])
check("and the count it shows is the new cycle's", feed.results[0].cycle_id != before)
del events[:]
feed.rebuild(answered)
check("a rebuild answers with what it recovered, after its look",
      wait_for(lambda: len(answers) == 2) and isinstance(answers[1], int)
      and events[-2:] == ["updated", "done"])
feed.set_heartbeat(120)

OTHER = TMP / "other_engine" / "config.json"
other_items = make_items(TMP / "other_library", 3)
write_config(OTHER, other_items)
gui_guard.watch(OTHER.parent)
gui_guard.watch(TMP / "other_library")
changed: list[tuple] = []
feed.config_changed.connect(lambda: changed.append((feed.config_path, feed.files,
                                                    list(feed.results))))
old_files = feed.files
feed.use_config(str(OTHER))
check("another config.json: said at once, with a fresh copy and no results yet",
      changed == [(str(OTHER), feed.files, [])] and feed.files is not old_files)
check("and its playlist counted on the worker",
      wait_for(lambda: feed.results and feed.results[0].total + feed.results[0].gone == 3))

check(f"no file call on the window's thread through all of it ({gui_guard.violations[:3]})",
      gui_guard.violations == [])
feed.stop()
feed._thread.join(5)
check("stopped, the worker goes", not feed._thread.is_alive())

print("-- with nothing chosen, config.json is found on the worker --")
gui_guard.clear()
found = TrackerFeed(None)
looked: list[int] = []
found.updated.connect(lambda: looked.append(1))
check("it looks, wherever it finds one (or does not)", wait_for(lambda: looked))
check(f"and stat'ed no drive from the window's thread ({gui_guard.violations[:3]})",
      gui_guard.violations == [])
found.stop()


# ---- a stand-in tracker, for what a real one does too fast to catch -------------------------

print("-- looks asked for while one runs, answers, failures --")


class StandIn:
    """A tracker whose looks the test holds and lets go."""

    made: list["StandIn"] = []

    def __init__(self, config_path):
        self.config_path = config_path or "Q:/nowhere/config.json"
        self.files = EngineFiles(self.config_path)
        self.atime_ok = False
        self.error = None
        self.recheck_in = None
        self.looks = 0
        self.held = threading.Event()
        self.held.set()
        self.started = threading.Event()
        self.fail = ""                  # what a look raises, when it is not empty
        self.reset_fails = False
        StandIn.made.append(self)

    def poll(self):
        self.looks += 1
        self.started.set()
        self.held.wait(5)
        if self.fail:
            raise RuntimeError(self.fail)
        return [f"look {self.looks} at {self.config_path}"]

    def reset(self, monitor):
        if self.reset_fails:
            raise ValueError(f"no cycle for {monitor}")

    def rebuild(self):
        return 7


def hold(tracker: StandIn) -> None:
    tracker.held.clear()
    tracker.started.clear()


guessed = TrackerFeed(None, make_tracker=StandIn)
told: list[str] = []
guessed.config_changed.connect(lambda: told.append(guessed.config_path))
guessed_files = guessed.files
check("where the worker finds config.json, if not where it was guessed, is said",
      wait_for(lambda: told) and told == ["Q:/nowhere/config.json"]
      and guessed.files is not guessed_files)
guessed.stop()

stand = TrackerFeed("Q:/first/config.json", make_tracker=StandIn)
seen_results: list = []
stand.updated.connect(lambda: seen_results.append(list(stand.results)))
check("the stand-in's first look lands", wait_for(lambda: seen_results))
check("and says what the worker learnt as it began: whether access times are kept",
      stand.atime_ok is False)
first_tracker = StandIn.made[-1]
hold(first_tracker)
stand.refresh()
check("a look is running, held", first_tracker.started.wait(5))
for _ in range(5):
    stand.refresh()
first_tracker.held.set()
wait_for(lambda: first_tracker.looks >= 3)
settle(1500)                        # time for any more to start; none should
check(f"five asked while one ran make one more, not five ({first_tracker.looks} looks)",
      first_tracker.looks == 3)

hold(first_tracker)
stand.refresh()
first_tracker.started.wait(5)
after_change: list = []
stand.updated.connect(lambda: after_change.append(list(stand.results)))
stand.use_config("Q:/second/config.json")
check("changing config.json under a look clears the results at once", stand.results == [])
first_tracker.held.set()
check("the new config.json's look lands",
      wait_for(lambda: stand.results and "second" in stand.results[0]))
check("and the look begun before it never does",
      all("second" in r[0] for r in after_change if r))

second = StandIn.made[-1]
failures: list[str] = []
stand.failed.connect(failures.append)
second.fail = "the disk went away"
stand.refresh()
check("a look that fails says why, once", wait_for(lambda: failures)
      and "the disk went away" in failures[0])
stand.refresh()
settle(500)
check("the same failure again is not said again", len(failures) == 1)
second.fail = ""
landed = len(seen_results)
stand.refresh()
check("and the worker carries on", wait_for(lambda: len(seen_results) > landed))
second.reset_fails = True
answers = []
stand.reset("Monitor9", answers.append)
check("a new cycle that fails is answered with what it raised",
      wait_for(lambda: answers) and isinstance(answers[0], ValueError))
stand.rebuild(answers.append)
check("and a rebuild with what it recovered", wait_for(lambda: len(answers) == 2)
      and answers[1] == 7)
stand.stop()
stand._thread.join(5)
check("stopped, it goes", not stand._thread.is_alive())

owner = QObject()
owned = TrackerFeed("Q:/owned/config.json", parent=owner, make_tracker=StandIn)
check("an owned feed looks", wait_for(lambda: StandIn.made[-1].looks >= 1))
shiboken6.delete(owner)            # as the window's feed goes with the window
settle(200)
owned_thread = owned._thread
owned_thread.join(5)
check("and its worker goes with its owner", not owned_thread.is_alive())

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
