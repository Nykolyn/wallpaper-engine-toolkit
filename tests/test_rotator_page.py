"""Rotator page: what each state says, where the words come from, the run's
flows end to end on folders made here, and that the window's thread never
reads the reserve.

Run it directly (needs Qt, but no windows on screen):

    .venv\\Scripts\\python.exe tests\\test_rotator_page.py

The words — the next run's steps, the start confirmation, the broken-folders
checklist, the history's rows, a finished run's steps — are tested through
the page's plain functions. The page itself runs on a data folder and a
reserve made in a temporary folder, with the playlist rebuild off or stood in
for: nothing here closes, starts or rewrites Wallpaper Engine.
"""
from __future__ import annotations

import builtins
import csv
import io
import os
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

TMP = Path(tempfile.mkdtemp(prefix="wallpaper_rotator_page_test_"))
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

from app import animations, services, theme                              # noqa: E402
from app.engines.library_index import FolderInfo, LibraryIndex            # noqa: E402
from app.engines.rotator import config as rc                              # noqa: E402
from app.engines.rotator import meta as rmeta                             # noqa: E402
from app.engines.rotator import runner                                    # noqa: E402
from app.engines.rotator.config import Config, History, RunRecord         # noqa: E402
from app.engines.rotator.core import (                                    # noqa: E402
    REASON_EMPTY, REASON_ORPHAN, REASON_SHADERS, BrokenFolder, DuplicateFolder, ProgressEvent,
    run_steps, step,
)
from app.engines.rotator.meta import RunMeta                              # noqa: E402
from app.pages import rotator as page_module                              # noqa: E402
from app.pages.rotator import (                                           # noqa: E402
    CSV_COLUMNS, DoneView, LibraryModel, RotatorPage, RunTracker, broken_body, broken_dialog,
    broken_groups, broken_reason, broken_title, confirmation, done_steps, done_subtitle,
    duplicates_confirmation, estimate_line, history_csv, history_rows, history_summary,
    idle_subtitle, move_caption, nav_state, plan_captions, plan_rows, playlist_caption,
    playlist_stale, protected_line, record_lines, result_sentence, result_title, result_words,
    return_caption, running_subtitle, selection_line, short_path, start_dialog, tracker_note,
)
from app.services import snapshot as snapshot_module                      # noqa: E402
from app.services.snapshot import LAST_RUN, PlaylistProgress, Reading, RunSummary  # noqa: E402
from app.ui.kit import NavState, format as fmt                            # noqa: E402

theme.apply(app)
animations.ENABLED = True
snapshot_module.compute = lambda keys, data_dir: {}

NB = fmt.NBSP
NOW = datetime(2026, 9, 19, 13, 44)             # a Saturday
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


def preview(**changes) -> dict:
    p = {"in_dest": 1000, "protected": 0, "duplicates_expected": 0, "returning": 1000,
         "reserve_now": 33421, "projected_reserve": 34421, "used": 25217,
         "available_unique": 8204, "will_reset": False, "count": 1000}
    p.update(changes)
    return p


# ==== the next run's words ============================================================

print("-- what a run does --")
p = preview()
captions = plan_captions(p, labels=None)
check("the check says how many folders it looks at first",
      captions["check"] == f"34{NB}421 folders looked at before anything moves")
check("the return: how many go back",
      captions["return"] == f"1{NB}000 folders go back")
check("the move: how many, drawn from how many never used",
      captions["move"] == f"1{NB}000 drawn from 8{NB}204 never used")
check("the playlist: Wallpaper Engine restarts once", captions["playlist"]
      == "Wallpaper Engine restarts once")
dupes = preview(returning=997, duplicates_expected=3)
check("duplicates are counted on the return, where the engine sets them aside",
      return_caption(dupes) == "997 folders go back · 3 duplicates set aside")
check("one folder goes back, in the singular",
      return_caption(preview(returning=1, in_dest=1)) == "1 folder goes back")
check("an empty myprojects has nothing to return",
      return_caption(preview(returning=0, in_dest=0)) == "myprojects is empty: nothing goes back")
reset = preview(available_unique=640, will_reset=True)
check("a reset draws from all of them, and says the history resets",
      move_caption(reset) == f"1{NB}000 drawn from all 34{NB}421 · the history resets")
check("the playlists the run rebuilds are named",
      playlist_caption(("custom", "custom_1")) == "custom, custom_1 · Wallpaper Engine restarts once")
check("and when none is made of myprojects, it says there is none to rebuild",
      playlist_caption(()) == "no playlist is made of myprojects now: none to rebuild")
rows = plan_rows(p, batch=1000, refresh=True, labels=("custom",))
check("the steps are the engine's, in its order: check, return, move, playlist",
      [r[0] for r in rows] == ["Check the folders for a project.json",
                               "Return the previous batch to the reserve",
                               f"Move 1{NB}000 new folders in",
                               "Rebuild the playlist in Wallpaper Engine"])
check("without the rebuild there are three", len(plan_rows(p, batch=1000, refresh=False)) == 3)
check("while the folders are being counted the captions are empty",
      all(c == "" for _, c in plan_rows(None, batch=5, refresh=True)))
check("one strategy, as a line (gate G9 A)",
      selection_line(p) == ("", f"Drawn at random from 8{NB}204 never used"))
check("and a warning when the history will reset",
      selection_line(reset) == ("warn", "Only 640 unused left — history resets and all folders "
                                        "are eligible"))
check("[protected] folders are a fact, not an option",
      protected_line(preview(protected=2)) == "2 [protected] folders stay in myprojects"
      and protected_line(preview(protected=1)) == "1 [protected] folder stays in myprojects"
      and protected_line(p) == "")
check("the estimate is said when there is one, and left out when not",
      estimate_line(900) == "Takes about 15 min. You can keep using the app."
      and estimate_line(None) == "You can keep using the app.")

print("-- the start confirmation --")
c = confirmation(p, 39, refresh=True, labels=("custom",), duplicates_folder="X:\\dupes")
check("it names the run", c.title == "Start run 39?")
check("and says what goes back and what is drawn",
      c.body == f"The 1{NB}000 folders now in myprojects go back to the reserve first, then "
                f"1{NB}000 new ones are drawn at random from the 8{NB}204 never used.")
check("its steps follow the engine, the check being done already",
      [s[0] for s in c.steps] == ["Return the previous batch to the reserve",
                                  f"Move 1{NB}000 new folders in",
                                  "Rebuild the playlist in Wallpaper Engine"]
      and c.steps[2][1] == "custom · Wallpaper Engine restarts once")
check("a normal run has no notes", c.notes == ())
protected = confirmation(preview(protected=2), 39, refresh=False)
check("[protected] folders are said to stay, with the lock",
      protected.notes == (("neutral", "lock", "2 [protected] folders stay in myprojects, "
                                              "untouched."),)
      and len(protected.steps) == 2)
duplicated = confirmation(dupes, 39, refresh=False, duplicates_folder="X:\\dupes")
check("duplicates are said to go to the duplicates folder",
      duplicated.notes == (("neutral", "info", "3 of them are in the reserve already: they go "
                                               "to X:\\dupes, not back into the reserve."),))
resets = confirmation(reset, 39, refresh=False)
check("a reset draws from all, and says why, in warn",
      "drawn at random from all 34" in resets.body and "fewer than" in resets.body
      and resets.notes[-1][0] == "warn" and "history resets" in resets.notes[-1][2])
empty = confirmation(preview(in_dest=0, returning=0), 1, refresh=False)
check("with nothing to return, it says so",
      empty.body.startswith("myprojects holds nothing to return, so 1"))
noticed = confirmation(p, 39, refresh=False, notices=("history.json was missing.", ""))
check("the history's notice is repeated before a run", noticed.notes
      == (("warn", "warn", "history.json was missing."),))
dialog = start_dialog(c, None, embedded=True)
check("the dialog is neutral and says Start run",
      not dialog.is_destructive() and dialog.confirm_button().text() == "Start run"
      and dialog.step_titles()[0] == "Return the previous batch to the reserve")
dialog.deleteLater()

print("-- the broken folders --")
reserve_root = str(TMP / "broken-root")
found = [
    BrokenFolder("empty-one", REASON_EMPTY, reserve_root),
    BrokenFolder("shaders", REASON_SHADERS, reserve_root, files=4, size=20),
    BrokenFolder("clip", REASON_ORPHAN, reserve_root, entries=["a.mp4", "b.jpg", "c.png"],
                 files=3, size=2048, holds_media=True),
    BrokenFolder("many", REASON_ORPHAN, reserve_root, entries=["x.mp4"], files=90, size=1,
                 holds_media=True),
]
groups = broken_groups(found)
check("two groups: safe to delete, ticked; holds media, in warn, not ticked",
      [(g.title, g.tone, g.initially_checked, len(g.rows)) for g in groups]
      == [("Safe to delete", "ok", True, 2), ("Holds media", "warn", False, 2)])
check("a row says what is in the folder",
      [broken_reason(b) for b in found]
      == ["no files at all", "only Wallpaper Engine's shader cache",
          "no project.json · 1 video, 2 images inside",
          "no project.json · 90 files, media among them"])
check("and carries its path, to delete",
      groups[0].rows[0].data == str(Path(reserve_root) / "empty-one"))
check("the title counts them and says where",
      broken_title(found, "the reserve") == "4 broken folders found in the reserve")
check("the body says why they are listed and which to look at",
      broken_body(found, 10) == "Of the 10 folders checked, these have no project.json, so "
                                "Wallpaper Engine can never list or show them. 2 hold no "
                                "media and are safe to remove. 2 still hold video, images or "
                                "other media — look inside before deciding.")
dialog = broken_dialog(found, "the reserve", None, scanned=10, embedded=True)
check("the dialog ticks the safe ones only",
      [row.name for row in dialog.checked_rows()] == ["empty-one", "shaders"]
      and dialog.group_state(1) == Qt.Unchecked)
check("the button counts what is ticked, and deleting is danger",
      dialog.confirm_button().text() == "Delete 2 permanently" and dialog.is_destructive())
check("and it says deleting is permanent", any(
    w.accessibleName() == page_module.PERMANENT for w in dialog.findChildren(QWidget)))
dialog.deleteLater()

# ==== the history ======================================================================

print("-- the history --")
old = RunRecord(id="old00001", timestamp="2026-08-01 09:00:00", moved=["a", "b"],
                duplicates=["c"], returned=2, failed=["d"])
older = RunRecord(id="old00000", timestamp="2026-07-25 09:00:00", moved=["e"], returned=1)
new = RunRecord(id="new00002", timestamp="2026-09-19 13:41:00", moved=["f", "g"], returned=2)
metas = {"new00002": RunMeta(started="2026-09-19T13:41:00", finished="2026-09-19T13:58:12",
                             seconds=1032.4, result="stopped", batch=2,
                             log="rotator/run-new00002.log", stopped_after="move")}
rows = history_rows([new, old, older], metas)
check("one row a run, newest first, numbered from the oldest",
      [(r.number, r.id) for r in rows] == [(3, "new00002"), (2, "old00001"), (1, "old00000")])
check("a run from before run_meta.json: its start from the record, no time taken, no log",
      (rows[1].started, rows[1].seconds, rows[1].log, rows[1].from_side_file)
      == (datetime(2026, 8, 1, 9, 0), None, "", False))
check("and its result worked out from the record: a failure means problems",
      (rows[1].result, rows[2].result) == ("problems", "clean"))
check("a recorded run has its times, result and log",
      rows[0].result == "stopped" and rows[0].seconds == 1032.4
      and rows[0].finished == datetime(2026, 9, 19, 13, 58, 12)
      and rows[0].log == "rotator/run-new00002.log" and rows[0].from_side_file)
check("RESULT's words and hues", [result_words(r, f) for r, f in (
    ("clean", 0), ("problems", 2), ("problems", 1), ("problems", 0), ("stopped", 0),
    ("failed", 0))] == [("clean", "ok"), ("2 problems", "warn"), ("1 problem", "warn"),
                        ("problems", "warn"), ("stopped", "danger"), ("failed", "danger")])
check("the summary strip counts them",
      history_summary(rows) == ["3 runs", "1 clean", "1 with problems", "1 stopped",
                                "logs kept 30 days"])
text = history_csv(rows)
table = list(csv.reader(io.StringIO(text)))
check("the CSV has a header and a row per run",
      table[0] == list(CSV_COLUMNS) and len(table) == 4)
check("a recorded run's row, as it is",
      table[1] == ["3", "new00002", "2026-09-19 13:41:00", "2026-09-19 13:58:12", "1032.4",
                   "stopped", "2", "2", "0", "0", "2", "rotator/run-new00002.log"])
check("an old run's unknowns are left empty",
      table[2] == ["2", "old00001", "2026-08-01 09:00:00", "", "", "problems", "2", "2", "1",
                   "1", "", ""])
check("a run without its log is read back from its record",
      record_lines(old) == [("", "moved", "a"), ("", "moved", "b"),
                            ("", "dupe", "c — set aside as a duplicate"),
                            ("", "fail", "d — did not move")])

# ==== a finished run ======================================================================

print("-- a finished run, step by step --")
record = RunRecord(id="r1", timestamp="2026-09-19 13:41:00", moved=["m"] * 1000,
                   duplicates=["d"] * 3, returned=998, failed=["x", "y"])
meta = RunMeta(started="2026-09-19T13:41:00", finished="2026-09-19T13:58:00", seconds=1020,
               result="problems", batch=1000, returned_failed=["x", "y"],
               playlist_rebuilt=False,
               playlist_problem="Wallpaper Engine did not come back — start it by hand.",
               step_times={"check": "2026-09-19T13:42:00", "return": "2026-09-19T13:46:00",
                           "move": "2026-09-19T13:58:00", "playlist": "2026-09-19T13:58:10"})
steps_ = done_steps(record, meta, check=(0, 0))
check("its steps, with what each did and when it ended",
      [(s[1], s[2]) for s in steps_] == [
          ("none broken · 13:42", "done"),
          (f"998 returned · 3 set aside · 2 left behind · 13:46", "done"),
          (f"1{NB}000 moved · 13:58", "done"),
          ("Wallpaper Engine did not come back — start it by hand.", "failed")])
check("a step with failures says so in warn", steps_[1][3] == "warn")
check("the playlist not rebuilt is stale; rebuilt since, it is not",
      playlist_stale(meta) and not playlist_stale(meta, rebuilt=True))
rebuilt = done_steps(record, meta, rebuilt=True)
check("rebuilt since, the step says so", rebuilt[-1][1:3]
      == ("rebuilt since, with “Rebuild playlist now”", "done"))
check("the title counts the problems",
      result_title(38, "problems", 2) == "Run 38 finished with 2 problems"
      and result_title(39, "clean", 0) == "Run 39 finished cleanly"
      and result_title(40, "stopped", 0, "return") == "Run 40 was stopped after the return"
      and result_title(41, "failed", 0) == "Run 41 failed")
check("the sentence says what moved and what did not",
      result_sentence(record, meta) == f"1{NB}000 folders moved in, 998 returned. 2 folders "
                                       "could not be moved and stayed where they were. The "
                                       "playlist did not rebuild.")
clean = RunRecord(id="r2", timestamp="2026-09-19 09:02:00", moved=["m"] * 1000, returned=1000)
clean_meta = RunMeta(result="clean", batch=1000, playlist_rebuilt=True,
                     playlist=["Playlist rebuilt with 1000 wallpapers (custom)."],
                     step_times={"playlist": "2026-09-19T09:18:00"})
check("a clean run is swapped, nothing left behind, and the playlist rebuilt at a time",
      result_sentence(clean, clean_meta) == f"1{NB}000 folders swapped, nothing left behind. "
                                            f"Wallpaper Engine's playlist was rebuilt with "
                                            f"1{NB}000 wallpapers at 09:18.")
stopped = RunMeta(result="stopped", stopped_after="return", playlist_rebuilt=False)
check("a run stopped after the return says nothing new came in",
      result_sentence(RunRecord("r3", "2026-09-19 09:00:00", returned=412), stopped)
      == "412 folders went back to the reserve; nothing new was moved in, and the playlist "
         "was left as it was.")
moved_only = done_steps(RunRecord("r3", "2026-09-19 09:00:00", returned=412), stopped)
check("and its move did not run", moved_only[1][1:3] == ("not run — stopped after the return",
                                                        "pending"))
check("a failed run says why first",
      result_sentence(RunRecord("r4", "2026-09-19 09:00:00", returned=3), RunMeta(result="failed"),
                      error="Unexpected error: disk full")
      .startswith("Unexpected error: disk full. By then 0 folders had moved in"))
playlist = PlaylistProgress("Monitor1", 0, 1000, 1000, 0, "", "26 Sep 09:00", True)
check("the Tracker's note: counting again, and when it is time to rotate",
      tracker_note(playlist, NOW) == f"The Tracker is counting again — 1{NB}000 on the playlist, "
                                     "time to rotate ≈26 Sep.")
check("and nothing before it has counted", tracker_note(None, NOW) == "")

print("-- the header and the sidebar --")
check("idle: nothing running, the last run and how it ended",
      idle_subtitle(datetime(2026, 9, 19, 13, 58), "clean", NOW)
      == "Nothing running · last run finished 13:58"
      and idle_subtitle(None, None, NOW) == "Nothing running · no run yet"
      and idle_subtitle(datetime(2026, 9, 18, 9, 0), "problems", NOW)
      == "Nothing running · last run finished with problems Fri 09:00"
      and idle_subtitle(datetime(2026, 9, 18, 9, 0), "clean", NOW, ended=False)
      == "Nothing running · last run Fri 09:00")
tracker = RunTracker("run", run_steps(check=True, playlist=True), number=38, batch=1000,
                     started=datetime(2026, 9, 19, 13, 41))
tracker.enter(2, NOW)
check("running: the run, when it started, which step",
      running_subtitle(tracker) == "Run 38 · started 13:41 · step 3 of 4")
check("done: the run, when, how",
      done_subtitle(39, "clean", datetime(2026, 9, 19, 9, 18), 0, NOW)
      == "Run 39 · finished 09:18 · clean"
      and done_subtitle(38, "problems", datetime(2026, 9, 19, 13, 58), 2, NOW)
      == "Run 38 · finished 13:58 with 2 problems")


class Snap(dict):
    def __getitem__(self, key):
        return self.get(key, Reading())


center = services.JobCenter()
snap = Snap()
check("a Rotator not read yet says nothing", nav_state(center, snap) == NavState())
snap[LAST_RUN] = Reading(RunSummary(38, "x", None, "clean", 1000, 998, 0, 0), 1.0)
check("after a run: ready for the next", nav_state(center, snap).text == "ready · run 39")
job = center.start("rotator", "Checking folders")
check("a job without a count yet: working", nav_state(center, snap)
      == NavState.status("working", below=True))
job.update("step 1 of 4 — checking", 10, 100)
check("with a count: its bar", nav_state(center, snap) == NavState.progress(10, 100))
job.finish("clean", "")
long_path = "X:\\a\\very\\long\\path\\to\\the\\wallpaper_engine\\projects\\myprojects"
check("a path's end is the part a summary shows",
      short_path(long_path, 30) == "…\\projects\\myprojects"
      and short_path("X:\\reserve") == "X:\\reserve")

# ==== the run under way: the state machine ================================================

print("-- events to states --")
track = RunTracker("run", run_steps(check=True, playlist=True), number=39, batch=3,
                   started=NOW)
check("before any event: the run, starting", track.title() == "Run 39"
      and track.sentence() == "Starting…" and track.states == ["pending"] * 4)
track.event(ProgressEvent("scan", "Checking 10 folders for a project.json...", 0, 10, step=0), NOW)
track.event(ProgressEvent("scan", "Checked 10 of 10", 10, 10, step=0), NOW)
check("the check is step 1, active, counted",
      track.states[0] == "active" and track.title() == "Run 39 · step 1 of 4"
      and track.step_rows()[0][1:3] == ("10 / 10", "active"))
track.broken = 0
later = NOW + timedelta(minutes=1)
track.event(ProgressEvent("return", "Returning 2 folders from myprojects...", 0, 2,
                          kind="step 2", step=1), later)
track.event(ProgressEvent("return", "Returned alpha", 1, 2, kind="returned", step=1), later)
track.event(ProgressEvent("return", "Failed to return beta: in use", 2, 2, level="ERROR",
                          kind="fail", step=1), later)
check("entering the return ends the check, with its time",
      track.states[:2] == ["done", "active"]
      and track.step_rows()[0][1] == "none broken · 13:45")
check("the return counts what it did, and what it left",
      (track.returned, track.failed, track.fails[1]) == (1, 1, 1)
      and track.activity == "Failed to return beta: in use")
check("the status line's words say which step",
      track.status_text() == "step 2 of 4 — returning 2 folders to the reserve")
track.event(ProgressEvent("select", "Reserve: 9 | used: 0 | unique available: 9", step=2), later)
check("the draw is the move step, uncounted", track.sentence() == "Drawing the new batch at random"
      and track.step_rows(None)[2][1:3] == ("working", "active"))
track.event(ProgressEvent("move", "Moving 3 folders into myprojects...", 0, 3, kind="step 3",
                          step=2), later)
track.event(ProgressEvent("move", "Moved gamma", 1, 3, kind="moved", step=2), later)
check("moving: the count, the time left, the folder just moved",
      track.step_rows("≈1 min left")[2][1] == "1 / 3 · ≈1 min left"
      and track.activity == "gamma → myprojects"
      and track.sentence() == "Moving 3 folders into myprojects")
check("the return is done, in warn for what it left behind",
      track.step_rows()[1][1:] == ("1 returned · 1 left behind · 13:45", "done", "warn"))
track.event(ProgressEvent("playlist", "Playlist rewritten with 3 wallpapers: custom.", 2, 3,
                          step=3), later)
track.event(ProgressEvent("playlist", "Wallpaper Engine is running again.", 3, 3, step=3), later)
track.finish("clean", later)
check("finished: every step done, the playlist rebuilt and restarted",
      track.state == "clean" and track.states == ["done"] * 4
      and track.step_rows()[3][1] == "rebuilt · restarted · 13:45")
check("the metrics", track.metrics() == [(1, "moved in"), (1, "returned"), (0, "duplicates"),
                                         (1, "problems", "warn")])
failing = RunTracker("run", run_steps(check=False, playlist=False), number=2)
failing.event(ProgressEvent("move", "Moving 1 folders", 0, 1, step=1), NOW)
failing.event(ProgressEvent("error", "Unexpected error: disk full", level="ERROR"), NOW)
failing.finish("failed", NOW)
check("a failure fails the step under way, with the error",
      failing.states == ["pending", "failed"] and failing.step_rows()[1][1:3]
      == ("Unexpected error: disk full", "failed"))

# ==== the page ===============================================================================

print("-- no disk from the constructor --")
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


def guarded(fn):
    for owner, name in GUARDED:
        guard(owner, name)
    try:
        return fn()
    finally:
        for (owner, name), real in originals.items():
            setattr(owner, name, real)
        originals.clear()


svc = services.Services(data_dir=TMP / "data")
services.install(svc)
RESERVE, MYPROJECTS, DUPES = TMP / "reserve", TMP / "myprojects", TMP / "dupes"
for folder in (RESERVE, MYPROJECTS, DUPES):
    folder.mkdir()
config = Config(source=str(RESERVE), destination=str(MYPROJECTS), duplicates=str(DUPES),
                count=2, refresh_playlist=False)
config.save()
page = guarded(lambda: RotatorPage(config, svc, index=LibraryIndex(TMP / "library_meta.json")))
check(f"building the page reads nothing from the disk ({on_gui_thread} were read)",
      on_gui_thread == [])
check("and has not read the history yet", page.history is None and page.state == "idle")


class Host(QWidget):
    def __init__(self):
        super().__init__()
        self.column = QVBoxLayout(self)
        self.resize(1280, 860)


host = Host()
host.column.addWidget(page)
host.move(400, 400)
host.show()
answers: list = []


def answer(dialog):
    """Every question answered yes, the way a click on its button would."""
    answers.append(dialog)
    if dialog.__class__.__name__ == "ConfirmDialog":
        dialog._answer(True)
        return dialog.result_value()
    return True


page._answer = answer
page.on_shown()
check("shown, it reads the history, lists the folders and counts the next run",
      page.history is not None
      and wait_for(lambda: page.facts is not None and page.facts.preview is not None)
      and page.texts()["next"]["run"] == "run 1")
check("an empty reserve says so, with nothing to draw", wait_for(
    lambda: page.summary.items()[:1] == ["0 folders"]))

print("-- a run end to end: check, clean up, confirm, rotate --")


def make(root: Path, name: str, files: dict[str, bytes]) -> None:
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    for rel, data in files.items():
        (folder / rel).write_bytes(data)


for name in ("fresh-a", "fresh-b", "fresh-c"):
    make(RESERVE, name, {"project.json": b'{"title":"t","type":"video"}', "a.mp4": b"x" * 10})
make(RESERVE, "hollow", {})
make(RESERVE, "clip-only", {"clip.mp4": b"y" * 5})
make(MYPROJECTS, "old-a", {"project.json": b"{}"})
make(MYPROJECTS, "[protected] keep", {"project.json": b"{}"})
page.refresh()
check("the reserve is listed once the names are known",
      wait_for(lambda: len(page.models["reserve"].items()) == 5))
answers.clear()
page.messages.clear()
page.start_rotation()
check("Start runs the check first, as step 1 of 3",
      wait_for(lambda: page.state == "running") and page.tracker is not None
      and [s.key for s in page.tracker.steps] == ["check", "return", "move"])
check("the run ends", wait_for(lambda: page.state == "done", 30_000))
kinds = [type(d).__name__ for d in answers]
check("it asked about the broken folders, then to start the run",
      kinds == ["ConfirmDialog", "ConfirmDialog"] and answers[0].is_destructive()
      and answers[0].title() == "2 broken folders found in the reserve"
      and answers[1].title() == "Start run 1?")
check("only the safe broken folder was deleted; the one holding media was left (the draw "
      "may have moved it in)",
      not (RESERVE / "hollow").exists()
      and ((RESERVE / "clip-only").exists() or (MYPROJECTS / "clip-only").exists()))
view = page.done_view
check("the done state: the run, cleanly, with its steps and the check's findings",
      view is not None and view.result == "clean" and view.check == (2, 1)
      and page.texts()["result"]["title"] == "Run 1 finished cleanly"
      and [r[2] for r in page.texts()["steps"]] == ["done", "done", "done"]
      and page.texts()["steps"][0][1].startswith("2 broken · 1 deleted"))
check("the [protected] folder stayed in myprojects",
      (MYPROJECTS / "[protected] keep").exists() and page.done_view.meta.protected == 1)
check("the table shows the history, the run marked ok",
      page.view == "history" and page.models["history"].marked == (view.record.id, "ok")
      and page.models["history"].item_rows() == 1)
check("the subtitle says it finished cleanly", page.subtitle().startswith("Run 1 · finished")
      and page.subtitle().endswith("· clean"))
journal = [e.kind for e in svc.journal.recent(6)]
check("the journal has the check, the clean-up, the start and the end",
      journal[:4] == ["run.clean", "run.started", "cleanup.clean", "check.problems"])
check("the status line's job ended clean", svc.jobs.last_finished("rotator").result == "clean")
log = TMP / "data" / "logs" / "rotator" / f"run-{view.record.id}.log"
lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
check("its log is a file of its own", len(lines) > 3
      and sum(line.split("\t")[1] == "moved" for line in lines) == 2)
answers.clear()
page.open_run_log(view.record.id)
check("Log reads the run's file back", answers and answers[-1].log.model().lines()
      and "moved" in [line.kind for line in answers[-1].log.model().lines()])
page.next_run_view()
check("Next run goes back to setting up the next", page.state == "idle"
      and page.view == "reserve" and wait_for(lambda: page.texts()["next"]["run"] == "run 2"))

print("-- the job's words and counts --")
page.tracker = RunTracker("run", run_steps(check=True, playlist=False), number=7, batch=2,
                          started=NOW)
page._job = services.begin("rotator", "Run 7", activity="run")
page._job_words = ""
page._set_state("running")
page._event(ProgressEvent("return", "Returning 2 folders", 0, 2, step=1))
page._event(ProgressEvent("return", "Returned m1", 1, 2, kind="returned", step=1))
live = svc.jobs.current()
check("a run's step and count reach the status line",
      live.phase_text == "step 2 of 3 — returning 2 folders to the reserve"
      and (live.done, live.total) == (1, 2))
page._event(ProgressEvent("select", "Reserve: 7 | used: 4 | unique available: 4", step=2))
check("a step that starts uncounted clears the count before it",
      live.phase_text == "step 3 of 3 — drawing the new batch at random"
      and (live.done, live.total) == (0, 0))
page._event(ProgressEvent("cancel", "Stop requested", level="WARN", kind="stop"))
check("an event of no step keeps the words", live.phase_text.startswith("step 3 of 3"))
check("the running panel says so",
      wait_for(lambda: page.texts()["run"]["title"] == "Run 7 · step 3 of 3"))


class FakeWorker:
    """A rotation that has ended, as the page reads one."""

    def __init__(self, result, meta=None, error=""):
        self.result, self.meta = result, meta
        self.job = SimpleNamespace(error=error)
        self.stopped = False

    def isRunning(self):
        return True

    def stop_after_step(self):
        self.stopped = True


page.rotation_worker = worker = FakeWorker("stopped")
page.stop_after_step()
check("Stop after this step asks the run to stop between steps (gate G7 A)",
      worker.stopped and page.tracker.stopping
      and page.texts()["run"]["stop"] == "Stopping after this step…"
      and not page.texts()["run"]["stop_enabled"])
record = RunRecord(id="run00007", timestamp="2026-09-28 14:00:00", moved=["r2", "r3"],
                   duplicates=["m2"], returned=1, failed=["r4"])
page.history.runs.insert(0, record)
page.rotation_worker = FakeWorker("problems", RunMeta(result="problems", batch=2,
                                                      moved_failed=["r4"]))
page._on_finished(record)
entries = [(e.kind, e.title, e.detail, e.run) for e in svc.journal.recent(2)]
number = len(page.history.runs)
check("a run with a failure ends with problems, numbered from the history",
      svc.jobs.last_finished("rotator").result == "problems"
      and entries[0] == ("run.problems", f"Run {number} finished with 1 problem",
                         "2 moved in · 1 returned · 1 duplicate · 1 failed", "run00007"))
check("… and its duplicates set aside are an entry of their own",
      entries[1][:2] == ("duplicates.set_aside", "1 duplicate set aside")
      and entries[1][3] == "run00007")
check("and the page shows it done with problems, the failure retryable",
      page.state == "done" and page.texts()["result"]["retry"] == "Retry the 1 failure"
      and page.texts()["result"]["tone"] == "warn")
page._job = services.begin("rotator", "Run 9", activity="run")
page._cancelled = True
page.tracker = RunTracker("run", run_steps(check=False, playlist=False), number=9)
page.rotation_worker = FakeWorker("stopped")
unrecorded = RunRecord(id="run00009", timestamp="2026-09-28 15:00:00", returned=1)
page.messages.clear()
page._on_finished(unrecorded)
entry = svc.journal.recent(1)[0]
check("a run stopped before it was recorded has no number and no run id",
      (entry.kind, entry.title, entry.run) == ("run.stopped", "The rotation was stopped", None))
check("and the page says nothing moved", page.messages[-1]
      == ("info", "The rotation was stopped before it moved anything."))

print("-- retry and rebuild --")
page.next_run_view()
for name in ("stuck-a",):
    make(RESERVE, name, {"project.json": b"{}"})
failed_run = RunRecord(id="run00010", timestamp="2026-09-29 10:00:00", moved=["fresh-a"],
                       returned=0, failed=["stuck-a"])
page.history.add(failed_run)
failed_meta = RunMeta(result="problems", batch=2, moved_failed=["stuck-a"],
                      log=rmeta.run_log_name("run00010"))
rmeta.record_meta("run00010", failed_meta)
page.done_view = DoneView(failed_run, failed_meta, len(page.history.runs))
page._set_state("done")
answers.clear()
page.retry_failures()
check("Retry asks first, naming what it moves",
      answers and answers[0].title() == "Retry the 1 failure?"
      and answers[0].step_titles() == ["Move 1 folder into myprojects"])
check("and runs, and the run is clean after", wait_for(lambda: page.state == "done"
                                                      and page.retry_worker is None, 30_000)
      and failed_run.failed == [] and (MYPROJECTS / "stuck-a").exists()
      and page.done_view.result == "clean")
check("the retry is journalled", svc.journal.recent(1)[0].kind == "retry.clean")


class FakeRefresh:
    """Wallpaper Engine's side of a rebuild, stood in for: nothing is closed."""

    def __init__(self, destination, progress, candidates=None):
        self.progress, self.problems, self.summary = progress, [], []
        self.rebuilt, self.restarted = False, True

    def prepare(self):
        self.progress(ProgressEvent("playlist", "Closing Wallpaper Engine...", 0, 3))

    def finish(self, completed, why=None):
        self.rebuilt = True
        self.summary.append("Playlist rebuilt with 3 wallpapers (custom).")
        self.progress(ProgressEvent("playlist", "Playlist rewritten with 3 wallpapers: custom.",
                                    3, 3))


real_refresh = runner.PlaylistRefresh
runner.PlaylistRefresh = FakeRefresh
try:
    stale_meta = RunMeta(result="problems", playlist_rebuilt=False,
                         playlist_problem="Wallpaper Engine did not come back — start it by hand.")
    page.done_view = DoneView(failed_run, stale_meta, len(page.history.runs))
    page._set_state("done")
    check("a playlist that did not rebuild offers to rebuild it now",
          page.steps.stale.isVisibleTo(page) and page.steps.rebuild.isVisibleTo(page))
    answers.clear()
    page.rebuild_playlist()
    check("Rebuild playlist now asks, then rebuilds",
          answers and answers[0].title() == "Rebuild the playlist now?"
          and wait_for(lambda: page.rebuild_worker is None and page.state == "done"))
    check("and the note goes: the playlist is right now",
          page.done_view.rebuilt and not page.steps.stale.isVisibleTo(page))
    check("the rebuild is journalled", svc.journal.recent(1)[0].kind == "rebuild.clean")
finally:
    runner.PlaylistRefresh = real_refresh

print("-- the history view and its log --")
page.next_run_view()
page.show_view("history")
answers.clear()
page.open_run_log("run00010")
check("a run's Log opens its file, or says it was not kept",
      answers and ("not kept" in answers[-1].body() or answers[-1].log.model().lines()))
old_record = RunRecord(id="ancient1", timestamp="2025-01-01 09:00:00", moved=["q"], returned=1)
page.history.runs.append(old_record)
page._history_changed()
answers.clear()
page.open_run_log("ancient1")
check("an old run's log was not kept: its record is shown instead",
      "ran before runs kept a log" in answers[-1].body()
      and [line.message for line in answers[-1].log.model().lines()] == ["q"])
out = TMP / "export.csv"
page_module.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(out), "CSV"))
page.messages.clear()
page.export_history()
exported = list(csv.reader(out.read_text(encoding="utf-8-sig").splitlines()))
check("Export as CSV writes the rows it shows",
      exported[0] == list(CSV_COLUMNS) and len(exported) == len(page.history.runs) + 1
      and page.messages[-1][0] == "ok")

print("-- duplicates --")
chosen = [DuplicateFolder("dup-a", 2048, 3, 0.0, in_reserve=True),
          DuplicateFolder("dup-b", 1024, 1, 0.0, in_reserve=False)]
title, body, destructive, button = duplicates_confirmation("delete", chosen, "X:\\dupes",
                                                           "X:\\reserve")
check("deleting names the count and the size, and is permanent",
      (title, destructive, button) == ("Delete 2 duplicates permanently?", True,
                                       "Delete 2 permanently")
      and "3 KB" in body and "Recycle Bin" in body)
title, body, destructive, button = duplicates_confirmation("replace", chosen, "X:\\dupes",
                                                           "X:\\reserve")
check("moving back says which replace a folder in the reserve, which is deleted",
      destructive and "1 replaces a folder of the same name there" in body
      and button == "Move 2 back")
title, body, destructive, button = duplicates_confirmation("replace", chosen[1:], "X:\\dupes",
                                                           "X:\\reserve")
check("and with nothing to replace, it is not a destructive question", not destructive)

# ==== 33 000 rows ==============================================================================

print("-- the reserve, 33 000 rows --")
data = page_module.load_rotator_fixture()
names = page_module.fixture_names(data, 33_000)
runs = [RunRecord(id=f"x{i}", timestamp=f"2026-0{1 + i % 9}-10 09:00:00",
                  moved=names[i * 1000:(i + 1) * 1000]) for i in range(30)]
usage = History(runs).usage()
infos = {name: FolderInfo(name, title=name, kind="scene", workshop_id=str(k),
                          size=k * 1000) for k, name in enumerate(names)}


def build():
    model = LibraryModel()
    model.root = str(TMP / "not-there")
    model.usage, model.infos, model.sizes = usage, infos, {}
    model.authors = {str(k): "Marlow" for k in range(0, 33_000, 3)}
    started = time.perf_counter()
    model.set_names(names)
    return model, time.perf_counter() - started


model, took = guarded(build)
check(f"the model takes 33 000 names in {took * 1000:.0f} ms (under 400), reading nothing",
      took < 0.4 and on_gui_thread == [] and model.item_rows() == 33_000)
check("sorted by name from the start", model.item_at(0) == sorted(names, key=str.casefold)[0])
check("a row says what the index and the history know",
      model.cell(names[3], 1) == "Marlow" and model.cell(names[3], 2) == "scene"
      and model.cell(names[3], 3) == fmt.size(3000))
check("New is never used; a used one has its day",
      model.cell(names[32_999], 5).variant == "New" and model.cell(names[0], 5) is None
      and model.cell(names[0], 4) not in ("", "never"))
host2 = Host()
table = page_module.Table()
table.setModel(model)
host2.column.addWidget(table)
host2.move(400, 400)
host2.show()
wait_for(lambda: False, 100)


def scroll():
    bar = table.verticalScrollBar()
    steps = []
    for k in range(60):
        started = time.perf_counter()
        bar.setValue((k * 997) % max(1, bar.maximum()))
        table.viewport().repaint()
        steps.append(time.perf_counter() - started)
    table.request_visible_thumbs()
    return sorted(steps)[int(len(steps) * 0.95) - 1]


p95 = guarded(scroll)
check(f"scrolling 33 000 rows: a step takes {p95 * 1000:.1f} ms at p95 (under 30), "
      "with nothing read on the window's thread", p95 < 0.030 and on_gui_thread == [])
started = time.perf_counter()
model.sort(3, Qt.DescendingOrder)
check(f"sorting by size takes {1000 * (time.perf_counter() - started):.0f} ms (under 400)",
      time.perf_counter() - started < 0.4 and model.item_at(0) == names[32_999])
model.set_filter(lambda name: model.matches(name, "1000"))
check("the filter narrows by name", 0 < model.item_rows() < 100)
model.infos[names[5]] = FolderInfo(names[5], unidentified=True)
check("an Unidentified folder widens the chip column to fit its chip",
      model.fit_chips() and model.columns[5].width > page_module.chip_size("New").width())
host2.close()

# ==== the fixtures =============================================================================

print("-- every fixture loads --")
fixture_page = RotatorPage(Config(source="", destination="", duplicates=""), svc)
holder = Host()
holder.column.addWidget(fixture_page)
holder.show()
fixture_page.make_header_actions()
said = {}
for state in RotatorPage.FIXTURES:
    fixture_page.load_fixture(state)
    said[state] = fixture_page.texts()
    dialog = getattr(fixture_page, "fixture_dialog", None)
    if dialog is not None:
        dialog.close()
        fixture_page.fixture_dialog = None
check("idle: the next run, its steps and the reserve",
      said["idle"]["state"] == "idle" and said["idle"]["next"]["run"] == "run 39"
      and said["idle"]["summary"][:2] == [f"33{NB}421 folders", f"8{NB}204 never used"])
check("running: the run, its count and its time left",
      said["running"]["run"]["count"] == f"412 / 1{NB}000"
      and said["running"]["run"]["left"] == "≈6 min left"
      and said["running"]["subtitle"] == "Run 39 · started 13:41 · step 3 of 4")
check("done-clean: finished cleanly, the Tracker counting again",
      said["done-clean"]["result"]["title"] == "Run 39 finished cleanly"
      and said["done-clean"]["result"]["tone"] == "ok")
check("done-problems: two problems, a retry, the playlist to rebuild",
      said["done-problems"]["result"]["title"] == "Run 38 finished with 2 problems"
      and said["done-problems"]["result"]["retry"] == "Retry the 2 failures")
check("current: what is in rotation", said["current"]["subtitle"] == f"1{NB}000 folders in rotation")
check("history: the runs", said["history"]["summary"][0] == "38 runs")
check("will-reset: the warning", said["will-reset"]["next"]["reset"].startswith("Only 640 unused"))
check("each names the frame's state it goes with",
      [fixture_page.frame_fixture(s)["frame"] for s in ("idle", "running", "done-clean",
                                                        "done-problems")]
      == ["idle", "running", "clean", "problems"] and fixture_page.frame_fixture("nope") is None)
try:
    fixture_page.load_fixture("nonsense")
    check("a state it does not have is refused", False)
except KeyError:
    check("a state it does not have is refused", True)
holder.close()

page.on_hidden()
page._stop_library(wait=True)
host.close()
app.processEvents()
print()
print("PASSED %d/%d" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
