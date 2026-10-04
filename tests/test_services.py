"""The window's services: jobs, the activity journal, the log files, the snapshot.

Run it directly (needs Qt, but no windows on screen):

    .venv\\Scripts\\python.exe tests\\test_services.py

It holds the rules the status line, the sidebar and Overview will rely on:
which running job leads, a time left that is only shown once it has been
measured, a journal that survives a damaged line and a field it does not
know and rotates at its size, log files named by tool and day and gone after
30 days, and numbers from the W: disk that are read on a worker thread —
never on the GUI thread — and keep their age. Then the tabs' thin wiring:
each tab's work reaches all three.

Everything is written under a temporary folder.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="wallpaper_services_test_"))
# Before any app module: settings and the Rotator's files resolve the data
# folder when they are imported, and it must not be this checkout's data\.
os.environ["WALLPAPER_TOOLKIT_DATA"] = str(TMP / "data")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# The labels carry ≈ and ·, which a Windows console's code page (cp1252 on CI)
# cannot always print; a check must not fail for the way its name is shown.
sys.stdout.reconfigure(errors="replace")

from PySide6.QtCore import QElapsedTimer, QObject, Signal                     # noqa: E402
from PySide6.QtTest import QTest                                              # noqa: E402
from PySide6.QtWidgets import QApplication                                    # noqa: E402

app = QApplication(sys.argv)

from app import services                                                      # noqa: E402
from app.engines.rotator import config as rc                                  # noqa: E402
from app.engines.rotator.config import History, RunRecord                     # noqa: E402
from app.engines.rotator.core import Rotator                                  # noqa: E402
from app.engines.tracker import ANCHOR_NONE, ANCHOR_ROTATION, Progress        # noqa: E402
from app.services import activity, jobs, logstore, runs, snapshot, textfile   # noqa: E402
from app.services.activity import ActivityJournal, Entry, PlaylistWatch       # noqa: E402
from app.services.jobs import JobCenter                                       # noqa: E402
from app.services.logstore import LogStore                                    # noqa: E402
from app.services.snapshot import Snapshot                                    # noqa: E402
from app.settings import Settings                                             # noqa: E402

results: list[bool] = []


def check(label: str, condition: bool) -> None:
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label)


def raises(fn, error=Exception) -> bool:
    try:
        fn()
    except error:
        return True
    return False


def wait_for(condition, ms: int = 5000) -> bool:
    clock = QElapsedTimer()
    clock.start()
    while clock.elapsed() < ms:
        if condition():
            return True
        QTest.qWait(10)
    return condition()


class Clock:
    """A monotonic clock moved by hand."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def folder(*parts: str) -> Path:
    path = TMP.joinpath(*parts)
    path.mkdir(parents=True, exist_ok=True)
    return path


# ==== JobCenter =====================================================================

print("-- JobCenter: a job's life --")
center = JobCenter(throttle=0)
changed: list = []
ended: list = []
center.changed.connect(changed.append)
center.finished.connect(ended.append)

job = center.start("creator", "Building 3 wallpapers", "creator")
check("start tells whoever listens, and the job is running",
      changed == [job] and job.running and center.current() is job)
check("a job knows its tool, title and page", (job.tool, job.title, job.page)
      == ("creator", "Building 3 wallpapers", "creator"))
extra = center.start("copier", "x")
check("a page defaults to the tool", extra.page == "copier")
extra.finish("clean")
changed.clear()
ended.clear()

job.update("building 3 wallpapers", 1, 3)
check("update sets the phase and the count", (job.phase_text, job.done, job.total)
      == ("building 3 wallpapers", 1, 3) and changed == [job])
job.update(done=2)
check("what update leaves out keeps its value",
      job.phase_text == "building 3 wallpapers" and job.total == 3 and job.done == 2)
check("fraction follows the count", abs(job.fraction() - 2 / 3) < 1e-9)
job.update(count_text="2 / 3 · 66%")
check("a count may be given in words", job.count_text == "2 / 3 · 66%")
job.update("reading", 0, 0)
check("a total of 0 is not counted: no fraction", job.fraction() is None)

check("an unknown result is refused", raises(lambda: job.finish("done"), ValueError))
check("finish ends it", job.finish("problems", "2 wallpapers created, 1 failed"))
check("… tells whoever listens, once", ended == [job] and changed[-1] is job)
check("… and it is the tool's last finished job, and no longer current",
      center.last_finished("creator") is job and center.current() is None
      and not job.running and job.result == "problems" and job.ended is not None)
check("a second ending is refused and the first stands",
      not job.fail("late") and job.result == "problems" and len(ended) == 1)
before = len(changed)
job.update("still going?", 5, 9)
check("updates after the end are ignored", job.done == 0 and len(changed) == before)

failing = center.start("review", "Scanning")
failing.fail("Steam refused the key")
check("fail is finish('failed', message)",
      failing.result == "failed" and failing.summary == "Steam refused the key")
check("last_finished() with no tool is the latest of any tool",
      center.last_finished() is failing and center.last_finished("copier") is not None)
check("a tool that never ran has no last job", center.last_finished("rotator") is None)

print("-- JobCenter: which job leads --")
center = JobCenter(throttle=0)
scan = center.start("review", "Scanning")
check("one running job is the current one", center.current() is scan)
copy = center.start("copier", "Copying")
check("a copy leads a Review scan", center.current() is copy)
rotation = center.start("rotator", "Run 39")
check("a rotation leads everything", center.current() is rotation)
build = center.start("creator", "Building")
check("a build started later does not take the lead from a rotation",
      center.current() is rotation)
check("running() lists them most important first",
      center.running() == [rotation, copy, build, scan])
rotation.finish("clean")
check("when the rotation ends the copy leads", center.current() is copy)
second = center.start("copier", "Copying again")
check("between two of one tool the one started last leads", center.current() is second)
check("is_running follows the tools", center.is_running("copier")
      and not center.is_running("rotator"))
own = center.start("tracker", "Rebuilding", priority=-1)
check("a priority can be given outright", center.current() is own)

print("-- JobCenter: a count arrives at most every throttle, and the last one always --")
center = JobCenter(throttle=0.1)
seen: list = []
center.changed.connect(lambda j: seen.append(j.done))
job = center.start("rotator", "Run 1")
job.update("moving folders into myprojects", 0, 1000)
seen.clear()
for i in range(1, 201):
    job.update(done=i)
check("200 counts in a burst are not 200 signals", len(seen) < 5)
check("… and the last one arrives", wait_for(lambda: seen and seen[-1] == 200, 1000))
seen.clear()
job.update("rebuilding the playlist", 0, 0)
check("a new phase is sent at once", seen == [0])
job.update(done=1)
job.finish("clean")
sent = len(seen)
QTest.qWait(250)
check("the end is sent at once, and nothing held is sent after it",
      sent == 2 and len(seen) == sent and not job.running)

print("-- JobCenter: rate and time left, only once measured --")
clock = Clock()
center = JobCenter(clock=clock, throttle=0)
job = center.start("rotator", "Run 1")
job.update("moving folders into myprojects", 0, 100)
for i in range(1, jobs.MIN_SAMPLES - 1):
    clock.now += 1
    job.update(done=i)
check(f"no rate before {jobs.MIN_SAMPLES} counts", job.rate() is None and job.eta() is None)
clock.now += 1
job.update(done=jobs.MIN_SAMPLES - 1)
check(f"a rate once there are {jobs.MIN_SAMPLES} counts over {jobs.MIN_SPAN:g} s",
      job.rate() is not None and abs(job.rate() - 1.0) < 1e-9)
check("time left is what is left at that rate",
      abs(job.eta() - (100 - (jobs.MIN_SAMPLES - 1))) < 1e-6)
clock.now += 8
check("a job that stalls shows its rate falling",
      job.rate() < 0.5 and job.eta() > 100 - jobs.MIN_SAMPLES)
job.update("rebuilding the playlist", 0, 10)
check("a new phase starts the rate over", job.rate() is None and job.eta() is None)
job.update("rebuilding the playlist", 0, 0)
for i in range(1, 10):
    clock.now += 1
    job.update(done=i)
check("an uncounted phase has no time left", job.eta() is None)

clock = Clock()
center = JobCenter(clock=clock, throttle=0)
fast = center.start("copier", "Copying")
fast.update("copying", 0, 1000)
for i in range(1, 8):
    clock.now += 0.1
    fast.update(done=i * 10)
check(f"many counts inside {jobs.MIN_SPAN:g} s are not yet a rate", fast.rate() is None)
same = center.start("review", "Scanning")
same.update("authors", 5, 100)
for _ in range(10):
    clock.now += 1
    same.update(done=5)
check("a count that does not move is no rate", same.rate() is None)
fast.finish("clean")
check("a finished job has no time left", fast.eta() is None and fast.elapsed() > 0)


# ==== ActivityJournal ===============================================================

print("-- ActivityJournal --")
journal_dir = folder("journal")
journal = ActivityJournal(journal_dir / activity.FILE_NAME)
heard: list = []
journal.appended.connect(heard.append)
first = journal.add("rotator", "run.started", "Run 7 started", "5 to return · 3 to move",
                    ts=datetime(2026, 9, 28, 13, 41, 5, 123456))
journal.add("rotator", "duplicates.set_aside", "2 duplicates set aside",
            "moved to a folder", chip="Duplicated", run="ab12cd34",
            ts=datetime(2026, 9, 28, 13, 46))
journal.add("rotator", "run.clean", "Run 7 finished", "3 moved in", run="ab12cd34",
            ts=datetime(2026, 9, 28, 13, 58))
check("each append is heard", len(heard) == 3 and heard[0] == first)
lines = (journal_dir / activity.FILE_NAME).read_text(encoding="utf-8").splitlines()
record = json.loads(lines[0])
check("one JSON object a line, with the seven fields",
      len(lines) == 3 and set(record) == {"ts", "tool", "kind", "title", "detail", "chip", "run"})
check("the time is local, with its offset, to the second",
      record["ts"].startswith("2026-09-28T13:41:05")
      and datetime.fromisoformat(record["ts"]).tzinfo is not None)
recent = journal.recent(2)
check("recent(n) is the newest n, newest first",
      [e.title for e in recent] == ["Run 7 finished", "2 duplicates set aside"])
check("an entry reads back as it was written",
      recent[1].chip == "Duplicated" and recent[1].run == "ab12cd34"
      and recent[1].ts == datetime(2026, 9, 28, 13, 46) and recent[1].outcome == "set_aside")
check("its time reads back naive and local, like the app's others",
      journal.recent(3)[-1].ts == datetime(2026, 9, 28, 13, 41, 5))

with open(journal_dir / activity.FILE_NAME, "a", encoding="utf-8") as f:
    f.write("this is not json\n")
    f.write('{"ts": "2026-09-28T14:00:00", "tool": "copier", "kind": "copy.clean"}\n')
    f.write("[1, 2, 3]\n")
    f.write('{"ts": "yesterday", "tool": "copier", "kind": "copy.clean", "title": "x"}\n')
    f.write("\n")
    f.write(json.dumps({"ts": "2026-09-28T14:05:00+00:00", "tool": "creator",
                        "kind": "build.clean", "title": "2 wallpapers created",
                        "detail": "in a folder", "chip": None, "run": None,
                        "added_by_a_newer_build": {"anything": [1, 2]}}) + "\n")
    f.write('{"ts": "2026-09-28T14:06:00", "tool": "copier", "kinD": "cut sh')
recent = journal.recent(10)
check("damaged lines, and lines that are not entries, are passed over",
      [e.title for e in recent] == ["2 wallpapers created", "Run 7 finished",
                                    "2 duplicates set aside", "Run 7 started"])
check("a field it does not know is ignored, and the entry is kept",
      recent[0].kind == "build.clean" and not hasattr(recent[0], "added_by_a_newer_build"))
check("an entry in UTC reads back in local time",
      recent[0].ts == datetime(2026, 9, 28, 14, 5, tzinfo=timezone.utc)
      .astimezone().replace(tzinfo=None))
check("Entry.from_json refuses what is not an entry",
      Entry.from_json([1]) is None and Entry.from_json({"ts": "2026-01-01T00:00:00"}) is None)
check("recent(0) is nothing", journal.recent(0) == [])

print("-- ActivityJournal: rotation at its size --")
rotating_dir = folder("rotating")
small = ActivityJournal(rotating_dir / activity.FILE_NAME, max_bytes=1000)
for i in range(40):
    small.add("copier", "copy.clean", f"entry {i:02d}", "x" * 40,
              ts=datetime(2026, 9, 1) + timedelta(minutes=i))
current_size = (rotating_dir / activity.FILE_NAME).stat().st_size
check("past its size the journal starts a new file",
      (rotating_dir / activity.ROTATED_NAME).exists() and current_size <= 1000)
check("only one older file is kept", sorted(p.name for p in rotating_dir.iterdir())
      == [activity.ROTATED_NAME, activity.FILE_NAME])
in_current = len((rotating_dir / activity.FILE_NAME).read_text(encoding="utf-8").splitlines())
everything = [e.title for e in small.recent(1000)]
check("recent() reads on into the older file, newest first, with no gap",
      len(everything) > in_current
      and everything == [f"entry {i:02d}" for i in range(39, 39 - len(everything), -1)])
check("recent(n) stops at n across the two files",
      [e.title for e in small.recent(in_current + 1)] == everything[:in_current + 1])

print("-- ActivityJournal: an append is cheap enough for the GUI thread --")
timing = ActivityJournal(folder("timing") / activity.FILE_NAME)
costs = []
for i in range(300):
    start = time.perf_counter()
    timing.add("rotator", "run.clean", f"Run {i} finished", "1 000 moved in · 998 returned",
               run="ab12cd34")
    costs.append(time.perf_counter() - start)
costs.sort()
mean_ms = 1000 * sum(costs) / len(costs)
p95_ms = 1000 * costs[int(len(costs) * 0.95)]
print(f"     measured: an append takes {mean_ms:.3f} ms on average, {p95_ms:.3f} ms at p95")
check("an append takes well under a frame (p95 < 5 ms)", p95_ms < 5)
reads = []
for _ in range(21):
    start = time.perf_counter()
    timing.recent(8)
    reads.append(1000 * (time.perf_counter() - start))
first_ms, median_ms = reads[0], sorted(reads[1:])[10]
print(f"     measured: recent(8) of {len(costs)} entries takes {median_ms:.3f} ms "
      f"({first_ms:.3f} ms the first time, just after the writes)")
check("reading the newest 8 reads the end of the file only (median < 2 ms)", median_ms < 2)

print("-- the kinds written are the kinds documented --")
for kind in ("run.started", "run.clean", "run.problems", "run.stopped", "run.failed",
             "duplicates.set_aside", "check.problems", "cleanup.clean",
             "duplicates_delete.clean", "duplicates_return.problems",
             "playlist.advanced", "playlist.finished", "playlist.restarted",
             "scan.clean", "count.problems", "database.failed", "build.stopped",
             "copy.clean"):
    check(f"{kind} is a known kind", activity.known_kind(kind))
check("an undocumented kind is not", not activity.known_kind("run.done")
      and not activity.known_kind("unknown.clean"))
check("every activity names a tool the journal knows",
      all(tool in activity.TOOLS for tool, _ in activity.ACTIVITIES.values())
      and all(tool in activity.TOOLS for tool, _ in activity.EVENTS.values()))
APP = Path(__file__).resolve().parent.parent / "app"
source = "\n".join(p.read_text(encoding="utf-8") for p in
                   (APP / "pages").glob("*.py"))
used = set(re.findall(r'activity="(\w+)"', source))
used |= set(re.findall(r'_start_job\(f?"[^"]*",\s*"(\w+)"[,)]', source))
used |= set(re.findall(r'job=\(f?"[^"]*",\s*"(\w+)"\)', source))
check("the pages start every activity documented, and no other",
      used == set(activity.ACTIVITIES))
notes = set(re.findall(r'\.note\("([\w.]+)"', source))
check("every note a tab or page writes is documented",
      notes and notes <= set(activity.EVENTS))


# ==== the Tracker's events ==========================================================

print("-- the Tracker's events --")


def progress(monitor="Monitor1", cycle="c1", seen=3, total=10, current="f3",
             title="", restarted_at=None, restarted_from=None, rotation=True):
    return Progress(cycle_id=cycle, monitor=monitor, playlist="p", seen=seen, total=total,
                    changes=seen, repeats=0, order="random", delay=10,
                    started="2026-09-20 10:00", current=current, current_title=title,
                    current_since="2026-09-28 13:00", live=True, inferred=0,
                    anchor=ANCHOR_ROTATION if rotation else ANCHOR_NONE, gone=0,
                    from_rotation=rotation, restarted_at=restarted_at,
                    restarted_from=restarted_from)


before = activity._seen(progress())
check("a new wallpaper on the leading monitor is an advance",
      activity.playlist_events(before, progress(seen=4, current="f4", title="Harbour Dusk"),
                               leading=True)
      == [("playlist.advanced", "Playlist advanced to #4", "Harbour Dusk on Monitor1")])
check("without a title the folder names it",
      activity.playlist_events(before, progress(seen=4, current="f4"), leading=True)[0][2]
      == "f4 on Monitor1")
check("the other monitors' advances are not journalled",
      activity.playlist_events(before, progress(seen=4, current="f4"), leading=False) == [])
check("a repeat, with no new wallpaper counted, is not an advance",
      activity.playlist_events(before, progress(seen=3, current="f9"), leading=True) == [])
check("reaching the end is a finish, said once instead of an advance",
      activity.playlist_events(before, progress(seen=10, current="f10"), leading=True)
      == [("playlist.finished", "Playlist finished on Monitor1",
           "all 10 shown · time to rotate")])
check("a monitor that is not leading finishes without the cue to rotate",
      activity.playlist_events(before, progress(seen=10), leading=False)[0][2] == "all 10 shown")
check("Wallpaper Engine starting over is a restart",
      activity.playlist_events(before, progress(cycle="c2", seen=1, restarted_at="14:02",
                                                restarted_from="81/195"), leading=True)
      == [("playlist.restarted", "Wallpaper Engine started the playlist over",
           "Monitor1 · the previous count had reached 81/195")])
check("a new cycle of its own (a rotation) is left to the rotation to say",
      activity.playlist_events(before, progress(cycle="c2", seen=1, current="g1"),
                               leading=True) == [])


class FakeFeed(QObject):
    updated = Signal()
    config_changed = Signal()

    def __init__(self):
        super().__init__()
        self.results: list = []


feed = FakeFeed()
watch_journal = ActivityJournal(folder("watch") / activity.FILE_NAME)
watch = PlaylistWatch(feed, watch_journal, Settings({}))
feed.results = [progress(seen=3), progress(monitor="Monitor2", seen=1, rotation=False)]
feed.updated.emit()
check("the first look at a monitor is where it starts from, not an event",
      watch_journal.recent(5) == [])
feed.results = [progress(seen=4, current="f4"),
                progress(monitor="Monitor2", seen=2, current="m2", rotation=False)]
feed.updated.emit()
entries = watch_journal.recent(5)
check("the next look journals the leading monitor's advance, as the tracker",
      [(e.tool, e.kind, e.detail) for e in entries] == [("tracker", "playlist.advanced",
                                                          "f4 on Monitor1")])
feed.config_changed.emit()
feed.results = [progress(seen=9, current="f9")]
feed.updated.emit()
check("a different config.json starts over from the next look",
      len(watch_journal.recent(5)) == 1)


# ==== LogStore ======================================================================

print("-- LogStore: files and lines --")
logs_root = folder("logs-test") / logstore.FOLDER_NAME
store = LogStore(logs_root)
writer = store.open("creator")
check("a tool's log is today's file in its own folder",
      writer.path == logs_root / "creator" / f"{date.today().isoformat()}.log"
      and writer.name == f"creator/{date.today().isoformat()}.log")
writer.write("start", "Building 2 wallpapers", when=datetime(2026, 9, 28, 13, 47, 2))
writer.write("done", "clip-one\nclip-two", when=datetime(2026, 9, 28, 13, 47, 9))
writer.write("step 2", "moving 1 000 folders", when=datetime(2026, 9, 28, 13, 48))
writer.close()
day_file = logs_root / "creator" / "2026-09-28.log"
check("a line goes to the file of its own day", day_file.exists())
check("a line is HH:MM:SS, kind and message, between tabs; a message of two lines is two",
      day_file.read_text(encoding="utf-8").splitlines()
      == ["13:47:02\tstart\tBuilding 2 wallpapers", "13:47:09\tdone\tclip-one",
          "13:47:09\tdone\tclip-two", "13:48:00\tstep 2\tmoving 1 000 folders"])
run_writer = store.open("rotator", "ab12cd34")
check("a rotation run has a file of its own", run_writer.path
      == logs_root / "rotator" / "run-ab12cd34.log" and run_writer.name == "rotator/run-ab12cd34.log")
run_writer.write("moved", "folder-1", when=datetime(2026, 9, 28, 23, 59, 59))
run_writer.write("moved", "folder-2", when=datetime(2026, 9, 29, 0, 0, 1))
run_writer.close()
check("… and keeps it past midnight",
      len((logs_root / "rotator" / "run-ab12cd34.log").read_text(encoding="utf-8").splitlines())
      == 2)
night = store.open("copier")
night.write("info", "before", when=datetime(2026, 9, 27, 23, 59, 59))
night.write("info", "after", when=datetime(2026, 9, 28, 0, 0, 1))
night.close()
check("a daily log moves to the next day's file at midnight",
      (logs_root / "copier" / "2026-09-27.log").exists()
      and (logs_root / "copier" / "2026-09-28.log").read_text(encoding="utf-8").strip()
      == "00:00:01\tinfo\tafter")
check("a tool is a plain word, never a path", raises(lambda: store.open("../data"), ValueError)
      and raises(lambda: store.open("a b"), ValueError))
check("so is a run id", raises(lambda: store.open("rotator", "..\\x"), ValueError))

bench = store.open("rotator", "bench")
costs = []
for i in range(2000):
    start = time.perf_counter()
    bench.write("moved", f"folder-{i:05d}")
    costs.append(time.perf_counter() - start)
bench.close()
costs.sort()
mean_ms = 1000 * sum(costs) / len(costs)
p95_ms = 1000 * costs[int(len(costs) * 0.95)]
print(f"     measured: a log line takes {mean_ms:.3f} ms on average, {p95_ms:.3f} ms at p95")
check("a line per folder moved is cheap enough for the GUI thread (p95 < 2 ms)", p95_ms < 2)
check("… and every one of them is in the file", len(
    (logs_root / "rotator" / "run-bench.log").read_text(encoding="utf-8").splitlines()) == 2000)

print("-- LogStore: the callback engines' own lines --")
check("[OK] is done, [WARN] warn, [ERROR] error, [START] start, [CANCEL] stop",
      logstore.parse_text("[OK]    a\n[WARN]  b\n[ERROR] c\n[START] d\n[CANCEL] e")
      == [("done", "a"), ("warn", "b"), ("error", "c"), ("start", "d"), ("stop", "e")])
check("rules and empty lines are dropped; a line with no tag is info",
      logstore.parse_text("\n" + "=" * 50 + "\nRESULT: created 2\n" + "-" * 50)
      == [("info", "RESULT: created 2")])
check("a copy marked ❌ is a fail, whatever its tag",
      logstore.parse_text("[DONE]  clip: 1/3 copies ❌") == [("fail", "clip: 1/3 copies ❌")])
check("an unknown tag is kept as the kind",
      logstore.parse_text("[GIF]   x\n[FOO] y") == [("step", "x"), ("foo", "y")])
check("a line read back splits into its three columns, tabs in the message kept",
      logstore.parse_line("13:47:02\tmoved\ta\tb") == ("13:47:02", "moved", "a\tb"))
check("a line not in that shape is kept whole",
      logstore.parse_line("written by hand") == ("", "info", "written by hand"))

print("-- LogStore: tail --")
tail_store = LogStore(folder("tail") / "logs")
old = tail_store.open("rotator")
for i in range(5):
    old.write("moved", f"old-{i}", when=datetime(2026, 9, 27, 10, 0, i))
old.close()
os.utime(old.path, (time.time() - 3600, time.time() - 3600))
new = tail_store.open("rotator", "run2")
for i in range(3):
    new.write("moved", f"new-{i}", when=datetime(2026, 9, 28, 11, 0, i))
new.write("fail", "folder-x — file in use", when=datetime(2026, 9, 28, 11, 0, 9))
new.close()
check("tail is the last lines of the file written last, oldest first",
      [m for _, _, m in tail_store.tail("rotator", 2)] == ["new-2", "folder-x — file in use"])
check("… as (time, kind, message), what LogPanel.extend takes",
      tail_store.tail("rotator", 1) == [("11:00:09", "fail", "folder-x — file in use")])
check("a short newest file is filled out from the one before",
      [m for _, _, m in tail_store.tail("rotator", 6)]
      == ["old-3", "old-4", "new-0", "new-1", "new-2", "folder-x — file in use"])
other = tail_store.open("copier")
other.write("done", "a copy")
other.close()
check("tail(None, n) is whichever tool wrote last", tail_store.tail(None, 1)[0][2] == "a copy")
check("a tool with no log has an empty tail", tail_store.tail("review", 5) == [])
check("folder(tool) is made on asking", tail_store.folder("review").is_dir()
      and tail_store.folder(None) == tail_store.root)
unended = TMP / "tail" / "unended.log"
unended.write_bytes("first\r\nsecond\nthird — no newline".encode("utf-8"))
check("textfile.tail reads a file with no newline at its end, and CRLF lines",
      textfile.tail(unended, 3) == ["first", "second", "third — no newline"])
check("reverse_lines crosses its blocks whole",
      list(textfile.reverse_lines(unended, block=4)) == ["third — no newline", "second", "first"])

print("-- LogStore: 30 days, then gone --")
sweep_store = LogStore(folder("sweep") / "logs")
now = datetime(2026, 9, 28, 13, 0)
made = {}
for tool, name in (("creator", "2026-08-27.log"), ("creator", "2026-08-28.log"),
                   ("creator", "2026-08-29.log"), ("creator", "2026-09-28.log"),
                   ("rotator", "run-old.log"), ("rotator", "run-new.log"),
                   ("rotator", "notes.txt"), ("rotator", "2026-01-01-copy.log")):
    path = sweep_store.root / tool / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("13:00:00\tinfo\tx\n", encoding="utf-8")
    made[name] = path
old_stamp = (now - timedelta(days=40)).timestamp()
for name in ("run-old.log", "notes.txt", "2026-01-01-copy.log"):
    os.utime(made[name], (old_stamp, old_stamp))
recent_stamp = (now - timedelta(days=2)).timestamp()
for name in ("run-new.log", "2026-08-27.log", "2026-08-28.log"):
    os.utime(made[name], (recent_stamp, recent_stamp))
thread = sweep_store.sweep_in_background(now=now)
thread.join(5)
left = sorted(p.name for p in sweep_store.root.rglob("*") if p.is_file())
check("the sweep runs on a thread of its own",
      thread.name == "log retention sweep" and thread is not threading.main_thread())
check("a daily file older than 30 days goes, by the day in its name",
      not made["2026-08-27.log"].exists() and not made["2026-08-28.log"].exists())
check("a daily file 30 days old stays, and today's", made["2026-08-29.log"].exists()
      and made["2026-09-28.log"].exists())
check("a run file goes by its last write", not made["run-old.log"].exists()
      and made["run-new.log"].exists())
check("a file not named as the store names its own is never touched",
      made["notes.txt"].exists() and made["2026-01-01-copy.log"].exists())
check("folders are left in place", (sweep_store.root / "creator").is_dir())


# ==== Snapshot ======================================================================

print("-- Snapshot: the numbers --")
snap_data = folder("snapshot")
reserve = folder("snapshot", "reserve")
myprojects = folder("snapshot", "myprojects")
duplicates = folder("snapshot", "duplicates")
for name in ("r1", "r2", "r3", "r4", "r5"):
    (reserve / name).mkdir()
for name in ("m1", "m2", "[protected] keep"):
    (myprojects / name).mkdir()
rc.CONFIG_PATH = snap_data / "config.json"
rc.HISTORY_PATH = snap_data / "history.json"
rc.CONFIG_PATH.write_text(json.dumps({
    "source": str(reserve), "destination": str(myprojects), "duplicates": str(duplicates),
    "count": 3, "refresh_playlist": False}), encoding="utf-8")
today = datetime.now().replace(microsecond=0)
history_runs = [
    RunRecord(id="run00002", timestamp=today.strftime("%Y-%m-%d %H:%M:%S"),
              moved=["m1", "m2"], returned=2, failed=["gone-2"]),
    RunRecord(id="run00001", timestamp="2026-09-12 10:00:00",
              moved=["r1", "gone-1"], returned=0),
]
rc.HISTORY_PATH.write_text(json.dumps({"runs": [r.to_dict() for r in history_runs]}),
                           encoding="utf-8")

# The engine fix first: never-used is counted by name, as a run draws.
counts = Rotator(rc.Config(source=str(reserve), destination=str(myprojects),
                           duplicates=str(duplicates), count=3),
                 History(history_runs)).preview()
check("preview counts the never used by name: a used folder deleted since is not "
      "subtracted", counts["available_unique"] == 4 and counts["used"] == 4)
check("… and the reset warning follows that count", counts["will_reset"] is False)

calls: list[str] = []
real_preview = Rotator.preview


def recording_preview(self):
    calls.append(threading.current_thread().name)
    return real_preview(self)


Rotator.preview = recording_preview
center = JobCenter(throttle=0)
snap_feed = FakeFeed()
snap = Snapshot(data_dir=snap_data, jobs=center, feed=snap_feed, settings=Settings({}))
news: list = []
snap.refreshed.connect(news.append)
check("nothing is read until asked", all(snap.get(k).at is None for k in snapshot.KEYS))
started = time.perf_counter()
snap.refresh()
returned_ms = 1000 * (time.perf_counter() - started)
check("refresh() returns at once", returned_ms < 50 and snap.refreshing())
check("… and the numbers arrive", wait_for(lambda: not snap.refreshing()))
check("Rotator.preview ran on a worker thread, not the GUI thread",
      calls and all(name != threading.main_thread().name for name in calls)
      and len(calls) == 1)
reserve_now = snap.get(snapshot.RESERVE)
check("RESERVE: folders, never used, the reset warning and the batch",
      reserve_now.value == snapshot.ReserveCounts(5, 4, False, 3, str(reserve)))
check("ROTATION: myprojects without the protected ones, and today's moves",
      snap.get(snapshot.ROTATION).value == snapshot.RotationCounts(2, 1, 2, str(myprojects)))
last = snap.get(snapshot.LAST_RUN).value
check("LAST_RUN: the newest run, numbered from the history",
      last.number == 2 and last.id == "run00002" and last.moved == 2 and last.failed == 1)
check("… its result worked out from the record without the side file",
      last.result == "problems" and not last.from_side_file and last.started == today)
check("REVIEW: None until step 11 writes its summary",
      snap.get(snapshot.REVIEW).value is None and snap.get(snapshot.REVIEW).at is not None)
check("each value carries its age", all(0 <= snap.get(k).age() < 5 for k in
                                        (snapshot.RESERVE, snapshot.ROTATION,
                                         snapshot.LAST_RUN, snapshot.REVIEW)))
check("refreshed names the keys it brought",
      news and set(news[-1]) == {snapshot.RESERVE, snapshot.ROTATION, snapshot.LAST_RUN,
                                 snapshot.REVIEW})
check("the counter wrote nothing of the Rotator's, only the count it leaves for the tray",
      sorted(p.name for p in snap_data.iterdir() if p.is_file())
      == ["config.json", "history.json", snapshot.RESERVE_COUNT_FILE]
      and json.loads((snap_data / snapshot.RESERVE_COUNT_FILE).read_text(encoding="utf-8"))
      ["never_used"] == snap.get(snapshot.RESERVE).value.never_used)

(snap_data / snapshot.RUN_META).write_text(json.dumps({
    "run00002": {"result": "stopped", "started": "2026-09-28T13:41:00",
                 "finished": "2026-09-28T13:58:30", "seconds": 1050,
                 "a_field_from_later": True},
    "run00001": "not an object"}), encoding="utf-8")
(snap_data / snapshot.REVIEW_LAST).write_text(json.dumps({"authors_new": 12, "items": 89}),
                                              encoding="utf-8")
snap.refresh([snapshot.LAST_RUN, snapshot.REVIEW])
wait_for(lambda: not snap.refreshing())
last = snap.get(snapshot.LAST_RUN).value
check("with step 09's side file the result and times come from it",
      last.result == "stopped" and last.from_side_file and last.seconds == 1050
      and last.started == datetime(2026, 9, 28, 13, 41)
      and last.finished == datetime(2026, 9, 28, 13, 58, 30))
check("REVIEW: the summary once there is one",
      snap.get(snapshot.REVIEW).value == {"authors_new": 12, "items": 89})
check("a refresh of some keys reads only those", len(calls) == 1)

print("-- Snapshot: last known, honestly --")
kept_at = snap.get(snapshot.RESERVE).at
shutil.rmtree(reserve)
time.sleep(0.05)
snap.refresh([snapshot.RESERVE, snapshot.ROTATION])
wait_for(lambda: not snap.refreshing())
missing = snap.get(snapshot.RESERVE)
check("a reserve that cannot be read keeps its last value, and says why",
      missing.value == reserve_now.value and missing.at == kept_at
      and "not found" in missing.error and missing.reason == "missing")
check("… and its age keeps growing", missing.age() >= 0.05)
check("myprojects is still read when only the reserve is gone",
      snap.get(snapshot.ROTATION).error == "" and snap.get(snapshot.ROTATION).reason == ""
      and snap.get(snapshot.ROTATION).at > kept_at)
check("a reason is one of the four", raises(lambda: snapshot.Unavailable("x", "gone"),
                                             ValueError))
(snap_data / snapshot.REVIEW_LAST).write_text("{broken", encoding="utf-8")
snap.refresh([snapshot.REVIEW])
wait_for(lambda: not snap.refreshing())
check("a damaged review summary keeps the last one and says so",
      snap.get(snapshot.REVIEW).value == {"authors_new": 12, "items": 89}
      and "could not be read" in snap.get(snapshot.REVIEW).error
      and snap.get(snapshot.REVIEW).reason == "unreadable")

unset = Snapshot(data_dir=snap_data)
rc.CONFIG_PATH.write_text(json.dumps({"source": "", "destination": "relative\\path"}),
                          encoding="utf-8")
unset.refresh([snapshot.RESERVE, snapshot.ROTATION])
wait_for(lambda: not unset.refreshing())
check("an unset reserve is no count at all, not a zero",
      unset.get(snapshot.RESERVE).value is None
      and unset.get(snapshot.RESERVE).error == "The reserve folder is not set."
      and unset.get(snapshot.RESERVE).reason == "unset" and unset.get(snapshot.RESERVE).at is None)
check("nor is a myprojects that is not a full path",
      unset.get(snapshot.ROTATION).value is None
      and "not a full path" in unset.get(snapshot.ROTATION).error
      and unset.get(snapshot.ROTATION).reason == "unset")

rc.HISTORY_PATH.write_text("{not json", encoding="utf-8")
before_files = sorted(p.name for p in snap_data.iterdir())
check("read_runs raises on a damaged history, and moves or writes nothing",
      raises(rc.read_runs) and sorted(p.name for p in snap_data.iterdir()) == before_files)
reserve.mkdir()
before_files = sorted(p.name for p in snap_data.iterdir())
rc.CONFIG_PATH.write_text(json.dumps({"source": str(reserve), "destination": str(myprojects),
                                      "duplicates": str(duplicates), "count": 3}),
                          encoding="utf-8")
damaged = Snapshot(data_dir=snap_data)
damaged.refresh([snapshot.RESERVE, snapshot.LAST_RUN])
wait_for(lambda: not damaged.refreshing())
check("with the history unreadable, never used is not guessed",
      damaged.get(snapshot.RESERVE).value is None
      and "history could not be read" in damaged.get(snapshot.RESERVE).error
      and damaged.get(snapshot.RESERVE).reason == "unreadable")
check("… nor the last run", damaged.get(snapshot.LAST_RUN).value is None
      and damaged.get(snapshot.LAST_RUN).error)
check("… and the damaged file is left for the Rotator to deal with",
      rc.HISTORY_PATH.read_text(encoding="utf-8") == "{not json"
      and sorted(p.name for p in snap_data.iterdir()) == before_files)
rc.HISTORY_PATH.write_text(json.dumps({"runs": [r.to_dict() for r in history_runs]}),
                           encoding="utf-8")

print("-- Snapshot: refreshed by events --")
calls.clear()
job = center.start("review", "Scanning")
job.finish("clean")
wait_for(lambda: not snap.refreshing(), 2000)
check("a finished Review job reads the review summary, not the disk", calls == [])
job = center.start("rotator", "Run 3")
job.finish("clean")
check("a finished rotation reads the reserve again", wait_for(lambda: len(calls) == 1)
      and wait_for(lambda: not snap.refreshing()))
job = center.start("copier", "Copying")
job.finish("clean")
check("so does a finished copy (it writes into myprojects)",
      wait_for(lambda: len(calls) == 2) and wait_for(lambda: not snap.refreshing()))

gate = threading.Event()


def slow_preview(self):
    calls.append(threading.current_thread().name)
    gate.wait(5)
    return real_preview(self)


Rotator.preview = slow_preview
calls.clear()
snap.refresh([snapshot.RESERVE])
snap.refresh([snapshot.ROTATION])
snap.refresh([snapshot.RESERVE])
check("asking during a refresh queues it: one read at a time",
      wait_for(lambda: len(calls) == 1) and snap.refreshing() and len(calls) == 1)
gate.set()
check("… and the queued keys are read straight after, once",
      wait_for(lambda: len(calls) == 2 and not snap.refreshing()) and len(calls) == 2)
Rotator.preview = recording_preview

news.clear()
snap_feed.results = [progress(monitor="Monitor2", seen=2, total=201, rotation=False),
                     progress(monitor="Monitor1", seen=4, total=201, current="f4",
                              title="Harbour Dusk")]
snap_feed.updated.emit()
playlist = snap.get(snapshot.PLAYLIST)
check("PLAYLIST: the leading monitor, from the feed, at once",
      playlist.value.monitor == "Monitor1" and playlist.value.seen == 4
      and playlist.value.remaining == 197 and playlist.value.title == "Harbour Dusk"
      and news == [[snapshot.PLAYLIST]])
check("… with its age", playlist.age() < 1 and playlist.value.live)
preferring = Snapshot(data_dir=snap_data, feed=snap_feed,
                      settings=Settings({"tracker": {"primary": "Monitor2"}}))
preferring.refresh([snapshot.PLAYLIST])
check("a monitor chosen in the settings leads",
      preferring.get(snapshot.PLAYLIST).value.monitor == "Monitor2")
snap_feed.results = []
snap_feed.updated.emit()
check("no monitors: nothing to lead, read just now",
      snap.get(snapshot.PLAYLIST).value is None and snap.get(snapshot.PLAYLIST).at is not None)
check("an unknown key is refused", raises(lambda: snap.refresh(["reserve_size"]), KeyError))


# ==== the tabs' wiring ==============================================================

print("-- Run: one call, three services --")
check("with no services installed a run does nothing, and says nothing",
      services.current() is None and runs.begin("copier", "Copying").job is None)
wired_dir = folder("wired")
installed = services.Services(data_dir=wired_dir)
services.install(installed)
run = services.begin("creator", "Building 2 wallpapers", activity="build")
check("begin() starts a job in the installed JobCenter",
      installed.jobs.current() is run.job and run.job.log_path is not None)
run.update("building 2 wallpapers", 1, 2)
run.log_text("[OK]    clip-a-1700000001\n[ERROR] clip-b: could not be read")
check("finish ends the job, once", run.finish("problems", "1 wallpaper created",
                                              detail="in a folder · 1 failed", chip="Failed")
      and not run.finish("clean", "again") and run.job.result == "problems")
entry = installed.journal.recent(1)[0]
check("… and journals <activity>.<result>",
      (entry.tool, entry.kind, entry.title, entry.detail, entry.chip)
      == ("creator", "build.problems", "1 wallpaper created", "in a folder · 1 failed", "Failed"))
check("… and the log file has it all, the end last",
      [(k, m) for _, k, m in installed.logs.tail("creator", 10)]
      == [("start", "Building 2 wallpapers"), ("done", "clip-a-1700000001"),
          ("error", "clip-b: could not be read"), ("warn", "1 wallpaper created")])
quiet = services.begin("rotator", "Checking folders", activity="check")
quiet.finish("clean", "All 5 folders have a project.json", journal=False)
check("an ending may leave the journal out", installed.journal.recent(1)[0].kind
      == "build.problems")
broke = services.begin("review", "Scanning folder “new”", activity="scan")
broke.fail("Steam refused the key")
entry = installed.journal.recent(1)[0]
check("fail journals <activity>.failed, titled from the run",
      (entry.kind, entry.title, entry.detail)
      == ("scan.failed", "Scanning folder “new” failed", "Steam refused the key"))

print("-- the Copier tab, end to end --")
from app.pages.copier import CopierPage                                       # noqa: E402

source = folder("wired", "copy-source", "wallpaper-a")
(source / "project.json").write_text("{}", encoding="utf-8")
(source / "preview.gif").write_bytes(b"GIF89a")
dest = folder("wired", "copy-dest")
copier = CopierPage(Settings({"copier": {"dest": str(dest)}}), installed)
copier.add_jobs([str(source)], 2)
copier._begin()
check("Start makes a Copier job", installed.jobs.is_running("copier")
      or installed.jobs.last_finished("copier") is not None)
check("… which ends when the copies are made",
      wait_for(lambda: installed.jobs.last_finished("copier") is not None, 10000))
done = installed.jobs.last_finished("copier")
check("… clean, with the count in its summary",
      done.result == "clean" and done.summary == "2 of 2 copies made" and done.total == 16)
entry = installed.journal.recent(1)[0]
check("… journalled as copy.clean", entry.kind == "copy.clean" and entry.tool == "copier")
copier_lines = [(k, m) for _, k, m in installed.logs.tail("copier", 50)]
check("… and its log lines are in the copier's file, tags turned into kinds",
      copier_lines[0][0] == "start" and ("done", "2 of 2 copies made") == copier_lines[-1]
      and any(k == "done" and "wallpaper-a_copy1" in m for k, m in copier_lines))

print("-- the Creator page's report --")
from app.pages.creator import CreatorPage                                     # noqa: E402

creator = CreatorPage(Settings({}), installed)
creator._target = str(dest)
creator._job = services.begin("creator", "Building 3 wallpapers", activity="build")
creator._on_finished([{"name": "a", "status": "ok", "preview": "preview.gif"},
                      {"name": "b", "status": "failed", "preview": None},
                      {"name": "c", "status": "ok", "preview": "preview.jpg"}])
done = installed.jobs.last_finished("creator")
check("a build with a failure ends with problems",
      done.result == "problems" and done.summary == "2 wallpapers created")
creator._job = services.begin("creator", "Building 3 wallpapers", activity="build")
creator._cancelled = True
creator._on_finished([])
check("a stopped build ends stopped", installed.jobs.last_finished("creator").result
      == "stopped")
creator._cancelled = False
creator._job = services.begin("creator", "Building 3 wallpapers", activity="build")
creator._on_finished([])
check("a build that built nothing, unasked, failed",
      installed.jobs.last_finished("creator").result == "failed")

print("-- the Review page's work --")
from app.engines import review as review_engine                               # noqa: E402
from app.pages.review import ReviewPage                                       # noqa: E402


class EmptyLibrary:
    """No workshop folder, nothing subscribed: a scan finds nothing to ask about."""

    def listable(self):
        return set()

    def subscribed(self):
        return set()

    def added_at(self, ids):
        return {}


class NoSteam:
    has_key = True

    def run_each(self, func, jobs):
        return [func(job) for job in jobs]


empty_config = TMP / "we-config.json"
empty_config.write_text('{"steamuser": {"general": {"browser": {"folders": []}}}}',
                        encoding="utf-8")
review = ReviewPage(Settings({}), installed, config_path=empty_config, data_dir=wired_dir,
                    prepare=lambda step: review_engine.Review(None, NoSteam(), EmptyLibrary()))
review.start_scan()
check("a Review scan is a job while it runs, and ends with its result",
      wait_for(lambda: review.state == "reviewing")
      and installed.jobs.last_finished("review").result == "clean"
      and installed.journal.recent(1)[0].kind == "scan.clean"
      and installed.journal.recent(1)[0].title == "Nothing new to look at")


def locked(step):
    raise RuntimeError("the authors database is locked")


review._prepare_fn = locked
review.start_scan()
check("a Review scan that fails ends failed, and is journalled so, with why",
      wait_for(lambda: review.state == "stopped")
      and installed.jobs.last_finished("review").result == "failed"
      and installed.journal.recent(1)[0].kind == "scan.failed"
      and "the authors database is locked" in installed.journal.recent(1)[0].detail)

print("-- the window's services --")
installed.start()
check("start() queues the first snapshot, and sweeps the logs",
      wait_for(lambda: installed.snapshot.get(snapshot.RESERVE).at is not None))
installed.deleteLater()
QTest.qWait(50)
check("services that are gone are no longer the installed ones", services.current() is None)

print()
print("PASSED %d/%d" % (sum(results), len(results)))
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(0 if all(results) else 1)
