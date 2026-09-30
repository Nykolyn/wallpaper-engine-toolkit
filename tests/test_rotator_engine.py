"""The Rotator's engine for the new page: steps, stop, retry, the side file, estimates,
the standalone playlist rebuild and the library index v2.

Run it directly — there is no test framework in this project:

    .venv\\Scripts\\python.exe tests\\test_rotator_engine.py

Everything happens under a temporary folder: made-up reserves, myprojects and
duplicates folders, a stand-in Wallpaper Engine install with its own
config.json and playliststate.bin, and a stand-in for closing and starting
the engine, so nothing on the machine is read, moved or closed. The names are
invented.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
TMP = Path(tempfile.mkdtemp(prefix="rotator_engine_test_"))
os.environ["WALLPAPER_TOOLKIT_DATA"] = str(TMP / "data")
(TMP / "data").mkdir()
try:
    sys.stdout.reconfigure(errors="replace")
except (AttributeError, ValueError):
    pass

from app.engines import engine_control, library as lib_mod               # noqa: E402
from app.engines import library_index as li                               # noqa: E402
from app.engines import playlist_refresh as pr, steam_paths               # noqa: E402
from app.engines import wallpaper_timer as wt                             # noqa: E402
from app.engines.rotator import config as rc, core, meta as rmeta         # noqa: E402
from app.engines.rotator import runner                                    # noqa: E402
from app.engines.rotator.config import Config, History, RunRecord         # noqa: E402
from app.services import snapshot                                         # noqa: E402

results: list[bool] = []


def check(label: str, condition: bool) -> None:
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label)


def compress(values) -> list:
    out = []
    for v in values:
        if not out or out[-1] != v:
            out.append(v)
    return out


# ---- a made-up world --------------------------------------------------------------------------

SETTINGS = {"delay": 10, "mode": "timer", "order": "random", "transition": "-2",
            "transitiontime": 1500, "updateonpause": False, "videosequence": False}


def slashed(path: Path) -> str:
    return str(path).replace("\\", "/")


def wallpaper(root: Path, name: str, *, title: str | None = None, kind: str = "video",
              size: int = 10, manifest: object = "default") -> Path:
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    if manifest == "default":
        manifest = {"file": "clip.mp4", "title": title or name.title(), "type": kind,
                    "preview": "preview.jpg"}
    if manifest is not None:
        text = manifest if isinstance(manifest, str) else json.dumps(manifest)
        (folder / "project.json").write_text(text, encoding="utf-8")
    (folder / "clip.mp4").write_bytes(b"v" * size)
    (folder / "preview.jpg").write_bytes(b"p")
    return folder


ENGINE = {"install": None, "running": True, "close": True, "calls": []}


def fake_running():
    ENGINE["calls"].append("running")
    if not ENGINE["running"]:
        return None
    return engine_control.Engine(pid=4242, exe=str(ENGINE["install"] / "wallpaper64.exe"),
                                 arguments=["-language", "english"])


def fake_close(engine, seconds=0):
    ENGINE["calls"].append("close")
    return ENGINE["close"]


def fake_start(engine, seconds=0):
    ENGINE["calls"].append("start")
    return True


engine_control.running, engine_control.close, engine_control.start = (
    fake_running, fake_close, fake_start)
pr.backup_dir = lambda: TMP / "backups"
steam_paths.we_config = lambda: ENGINE["install"] / "config.json"


def state_file(monitor: str, items: list[str]) -> bytes:
    desktop = wt.StateSection(wt.SECTION, [wt.StateMonitor(
        name=monitor, instance=7, current=items[0], waiting=items[1:],
        marks=[0] * len(items[1:]))])
    return wt.write_state_file([desktop, wt.StateSection("\0\0"), wt.StateSection("")])


def world(tag: str, *, reserve: int = 8, current: int = 4, protected: int = 1,
          count: int = 4, refresh: bool = True) -> SimpleNamespace:
    """A reserve r0…, a myprojects c0… put there by an earlier run, a playlist of
    them in Wallpaper Engine, and the history of that run."""
    base = TMP / tag
    w = SimpleNamespace(base=base, reserve=base / "reserve", myprojects=base / "myprojects",
                        dupes=base / "duplicates", install=base / "wallpaper_engine")
    for d in (w.reserve, w.myprojects, w.dupes, w.install / "bin"):
        d.mkdir(parents=True)
    for i in range(reserve):
        wallpaper(w.reserve, f"r{i}")
    for i in range(current):
        wallpaper(w.myprojects, f"c{i}")
    for i in range(protected):
        wallpaper(w.myprojects, f"[protected] keep{i}")
    rc.HISTORY_PATH = base / "history.json"
    rc.CONFIG_PATH = base / "config.json"
    ENGINE.update(install=w.install, running=True, close=True, calls=[])
    items = [slashed(w.myprojects / f"c{i}" / "clip.mp4") for i in range(current)]
    cfg = {"user": {"general": {
        "playlists": [{"name": "custom", "items": list(items), "settings": dict(SETTINGS)}],
        "wallpaperconfig": {"selectedwallpapers": {"Monitor1": {
            "file": items[0] if items else "",
            "playlist": {"name": "custom", "items": list(items), "settings": dict(SETTINGS)}}}}}}}
    (w.install / "config.json").write_bytes(pr.dump_config(cfg))
    if items:
        (w.install / "bin" / "playliststate.bin").write_bytes(state_file("Monitor1", items))
    w.cfg = Config(source=str(w.reserve), destination=str(w.myprojects),
                   duplicates=str(w.dupes), count=count, refresh_playlist=refresh)
    earlier = RunRecord(id="earlier1", timestamp="2026-09-01 10:00:00",
                        moved=[f"c{i}" for i in range(current)])
    w.history = History([earlier])
    w.history.save()
    return w


def names(folder: Path) -> set[str]:
    return {p.name for p in folder.iterdir() if p.is_dir()}


def playlist_folders(w) -> list[str]:
    cfg = json.loads((w.install / "config.json").read_text(encoding="utf-8"))
    items = cfg["user"]["general"]["wallpaperconfig"]["selectedwallpapers"]["Monitor1"][
        "playlist"]["items"]
    return sorted(Path(i).parent.name for i in items)


CHECKED = datetime(2026, 9, 30, 13, 0, 0)

# ==== the steps table ============================================================================

print("-- the steps --")
check("the steps are the engine's real order: check, return, move, playlist",
      core.STEP_KEYS == ("check", "return", "move", "playlist"))
check("a run without a check or a playlist rebuild has two",
      [s.key for s in core.run_steps(check=False, playlist=False)] == ["return", "move"])
check("an event not given a kind has one from its message",
      [core.ProgressEvent("move", "Moved a").kind,
       core.ProgressEvent("return", "DUPLICATE: a", level="WARN").kind,
       core.ProgressEvent("stopped", "x", level="WARN", kind="stop").kind]
      == ["moved", "dupe", "stop"])

# ==== a normal run ============================================================================

print("-- a normal run: check, return, move, rebuild --")
w = world("normal")
events: list = []
run = runner.RotationRun(w.cfg, w.history, progress=events.append, check_finished=CHECKED)
check("the run id exists before the run starts, for its log file",
      len(run.run_id) == 8 and run.log_name == f"rotator/run-{run.run_id}.log")
record = run.execute()
check("it ends clean", run.result == "clean" and record is not None and record.id == run.run_id)
check("its steps are the four, the check first",
      [s.key for s in run.steps] == ["check", "return", "move", "playlist"])
check("every event carries its step", all(e.step is not None for e in events))
check("the step index runs 1 → 2 → 3 (the check was its own worker, step 0)",
      compress(e.step for e in events) == [1, 2, 3])
check("each step opens with a numbered line, as the design's log does",
      any(e.kind == "step 2" and e.message.startswith("Returning") for e in events)
      and any(e.kind == "step 3" and e.message.startswith("Moving") for e in events))
check("and closes with what it did",
      any(e.kind == "step 2" and "4 folders returned to the reserve" in e.message
          for e in events))
check("one line per folder: returned, then moved",
      sum(e.kind == "returned" for e in events) == 4
      and sum(e.kind == "moved" for e in events) == 4)
playlist_events = [e for e in events if e.phase == "playlist"]
check("the playlist step counts its three stages",
      all(e.total == 3 for e in playlist_events) and playlist_events[-1].current == 3)
check("the protected folder stayed, and the new batch came in",
      names(w.myprojects) == {"[protected] keep0", *record.moved} and len(record.moved) == 4)
check("Wallpaper Engine was closed first and started again after",
      ENGINE["calls"] == ["running", "close", "start"])
check("the playlist now lists what is in myprojects",
      playlist_folders(w) == sorted(["[protected] keep0", *record.moved]))
stored = rmeta.read_meta()[record.id]
check("the side file has the run, beside history.json",
      rmeta.meta_path() == w.base / "run_meta.json" and stored.result == "clean")
check("with its times, batch, protected count and log file",
      set(stored.step_times) == {"check", "return", "move", "playlist"}
      and stored.step_times["check"] == "2026-09-30T13:00:00"
      and stored.batch == 4 and stored.protected == 1 and stored.seconds is not None
      and stored.log == run.log_name and stored.started_at and stored.finished_at)
check("and what happened to the playlist",
      stored.playlist_rebuilt is True and stored.playlist_problem == ""
      and any("Playlist rebuilt with 5 wallpapers" in line for line in stored.playlist))
summary = snapshot.last_run(rc.read_runs(), w.base)
check("the Snapshot reads the result and times from the side file",
      summary.from_side_file and summary.result == "clean" and summary.number == 2
      and summary.seconds == stored.seconds)
usage = w.history.usage()
check("last used: when a folder was last moved in; never used: not since the last reset",
      usage.last_used("c0") == datetime(2026, 9, 1, 10, 0) and not usage.never_used("c0")
      and usage.never_used("r7") == ("r7" not in record.moved)
      and usage.last_used("nothing") is None and usage.never_used("nothing"))

# ==== without the playlist =====================================================================

print("-- a run with the playlist rebuild off --")
w = world("no-playlist", refresh=False)
events = []
run = runner.RotationRun(w.cfg, w.history, progress=events.append)
record = run.execute()
check("two steps, 0 → 1", [s.key for s in run.steps] == ["return", "move"]
      and compress(e.step for e in events) == [0, 1])
check("Wallpaper Engine is left alone", ENGINE["calls"] == [] and run.result == "clean")
check("the side file says the playlist was not touched",
      rmeta.read_meta()[record.id].playlist_rebuilt is None
      and set(rmeta.read_meta()[record.id].step_times) == {"return", "move"})

# ==== stop after this step ===================================================================

print("-- stop after the return --")
w = world("stop-return")
before_all = names(w.reserve) | names(w.myprojects) | names(w.dupes)
config_before = (w.install / "config.json").read_bytes()
state_before = (w.install / "bin" / "playliststate.bin").read_bytes()
events = []


def stop_at_return(e):
    events.append(e)
    if e.phase == "return":
        run.stop_after_step()


run = runner.RotationRun(w.cfg, w.history, progress=stop_at_return, check_finished=CHECKED)
record = run.execute()
check("the return finishes, then the run stops", run.result == "stopped"
      and record.returned == 4 and record.moved == [] and run.rotator.stopped_after == "return")
check("the steps go 1 → 3: the move never ran, Wallpaper Engine was still started",
      compress(e.step for e in events) == [1, 3])
check("myprojects holds only the protected folder, the reserve everything else",
      names(w.myprojects) == {"[protected] keep0"} and names(w.reserve) == before_all - {
          "[protected] keep0"})
check("no folder went anywhere else", names(w.reserve) | names(w.myprojects) | names(w.dupes)
      == before_all)
check("the playlist and the pass were left as they were, read back from the files",
      (w.install / "config.json").read_bytes() == config_before
      and (w.install / "bin" / "playliststate.bin").read_bytes() == state_before
      and playlist_folders(w) == ["c0", "c1", "c2", "c3"])
check("and Wallpaper Engine was started again", ENGINE["calls"][-1] == "start")
check("the stopped run is in the history, so its returns are too",
      [r.id for r in rc.read_runs()][0] == record.id)
stored = rmeta.read_meta()[record.id]
check("the side file says stopped, after the return, and the playlist not rebuilt",
      stored.result == "stopped" and stored.stopped_after == "return"
      and stored.playlist_rebuilt is False and "move" not in stored.step_times)
summary = snapshot.last_run(rc.read_runs(), w.base)
check("the Snapshot shows it stopped", summary.result == "stopped" and summary.from_side_file)

print("-- stop after the move --")
w = world("stop-move")
config_before = (w.install / "config.json").read_bytes()
events = []


def stop_at_move(e):
    events.append(e)
    if e.phase == "move":
        run.stop_after_step()


run = runner.RotationRun(w.cfg, w.history, progress=stop_at_move)
record = run.execute()
check("the whole batch moves in, then it stops before the rebuild",
      run.result == "stopped" and len(record.moved) == 4
      and run.rotator.stopped_after == "move")
check("the playlist is left for 'Rebuild playlist now'",
      (w.install / "config.json").read_bytes() == config_before
      and rmeta.read_meta()[record.id].playlist_rebuilt is False)

print("-- a cancel before anything moved --")
w = world("cancel-first")
run = runner.RotationRun(w.cfg, w.history)
run.cancel()
record = run.execute()
check("stopped, nothing moved, nothing recorded, no side-file entry",
      run.result == "stopped" and record is not None and not run.rotator.recorded
      and [r.id for r in rc.read_runs()] == ["earlier1"] and record.id not in rmeta.read_meta())

# ==== failures, and retrying them =============================================================

print("-- a run with failures --")


class Flaky:
    """shutil for core, with some folders that will not move."""

    def __init__(self, stuck: set[str]):
        self.stuck = stuck
        self.rmtree = shutil.rmtree

    def move(self, src, dst):
        if Path(src).name in self.stuck:
            raise PermissionError(13, "in use by Wallpaper Engine", src)
        return shutil.move(src, dst)


w = world("retry", reserve=6, current=3, count=6)
core.shutil = Flaky({"c1", "r2"})
events = []
run = runner.RotationRun(w.cfg, w.history, progress=events.append)
record = run.execute()
core.shutil = shutil
stored = rmeta.read_meta()[record.id]
check("it ends with problems", run.result == "problems" and stored.result == "problems")
check("the side file says which step each failure was in",
      stored.returned_failed == ["c1"] and stored.moved_failed == ["r2"]
      and sorted(record.failed) == ["c1", "r2"])
check("each failure is one 'fail' line in the log",
      sum(e.kind == "fail" for e in events) == 2)
check("they are where they were", (w.myprojects / "c1").is_dir() and (w.reserve / "r2").is_dir())
returned_before = record.returned

print("-- retrying them --")
events = []
ENGINE["calls"] = []
retry = runner.RetryRun(w.cfg, w.history, record, progress=events.append)
check("a retry goes through return, move and the playlist",
      [s.key for s in retry.steps] == ["return", "move", "playlist"])
outcome = retry.execute()
check("both are fixed", outcome.fixed == 2 and outcome.still_failed == [] and record.failed == [])
check("c1 went back to the reserve, r2 into myprojects",
      (w.reserve / "c1").is_dir() and not (w.myprojects / "c1").exists()
      and (w.myprojects / "r2").is_dir() and not (w.reserve / "r2").exists())
check("the record counts them", record.returned == returned_before + 1 and "r2" in record.moved)
saved = next(r for r in rc.read_runs() if r.id == record.id)
check("and is saved to history.json", saved.failed == [] and "r2" in saved.moved)
stored = rmeta.read_meta()[record.id]
check("the side file: nothing left failing, clean now, the retry noted",
      stored.returned_failed == [] and stored.moved_failed == [] and stored.result == "clean"
      and len(stored.retries) == 1 and stored.retries[0]["fixed"] == 2
      and stored.retries[0]["result"] == "clean")
check("the retry's steps run 0 → 1 → 2", compress(e.step for e in events) == [0, 1, 2])
check("the playlist was rebuilt with r2 in it, Wallpaper Engine restarted",
      "r2" in playlist_folders(w) and ENGINE["calls"][-1] == "start")

print("-- a retry that still fails --")
w = world("retry-again", reserve=4, current=2, count=4)
core.shutil = Flaky({"c0"})
record = runner.RotationRun(w.cfg, w.history).execute()
retry = runner.RetryRun(w.cfg, w.history, record)
outcome = retry.execute()
core.shutil = shutil
stored = rmeta.read_meta()[record.id]
check("still failing, still problems, and said so",
      outcome.still_failed == ["c0"] and retry.result == "problems"
      and stored.result == "problems" and stored.returned_failed == ["c0"]
      and stored.retries[-1]["failed"] == 1)
check("nothing moved, so the playlist was left and Wallpaper Engine started again",
      retry.refresh is not None and not retry.refresh.rebuilt and retry.refresh.restarted)

print("-- a retry of a run from before the side file --")
w = world("retry-old", reserve=3, current=0, protected=0, count=1)
wallpaper(w.myprojects, "stuck-back")        # failed to return: still in myprojects
wallpaper(w.reserve, "by-hand")              # failed to return, then moved back by hand
old = RunRecord(id="old00001", timestamp="2026-09-12 09:00:00", moved=["r0"],
                failed=["stuck-back", "by-hand"])
w.history = History([old, RunRecord(id="older001", timestamp="2026-09-05 09:00:00")])
w.history.save()
listing = (names(w.reserve), names(w.myprojects))
ENGINE["calls"] = []
retry = runner.RetryRun(w.cfg, w.history, old)
outcome = retry.execute()
check("nothing recorded their steps, so nothing is guessed: no folder moves",
      retry.retryable == 0 and outcome.unknown == ["stuck-back", "by-hand"]
      and (names(w.reserve), names(w.myprojects)) == listing
      and old.failed == ["stuck-back", "by-hand"] and retry.result == "problems")
check("and Wallpaper Engine is not closed for it", ENGINE["calls"] == [] and retry.steps == ())

print("-- a failure moved by hand before the retry --")
w = world("retry-by-hand", reserve=3, current=2, count=3, refresh=False)
core.shutil = Flaky({"c0", "c1"})
record = runner.RotationRun(w.cfg, w.history).execute()
core.shutil = shutil
shutil.move(str(w.myprojects / "c0"), str(w.reserve / "c0"))     # put right by hand
shutil.rmtree(w.myprojects / "c1")                                # deleted by hand
outcome = runner.RetryRun(w.cfg, w.history, record).execute()
check("both are taken off the list, and neither counted as returned by the retry",
      record.failed == [] and outcome.resolved == 2 and outcome.fixed == 0
      and outcome.returned == 0 and (w.reserve / "c0").is_dir())

# ==== estimates =================================================================================

print("-- estimates --")
runs = [RunRecord(id=f"e{i}", timestamp="2026-09-01 10:00:00", moved=["x"] * 1000)
        for i in range(8)]
metas = {
    "e0": rmeta.RunMeta(result="clean", seconds=100.0, batch=1000),
    "e1": rmeta.RunMeta(result="problems", seconds=300.0, batch=1000),
    "e2": rmeta.RunMeta(result="stopped", seconds=5.0, batch=1000),
    "e3": rmeta.RunMeta(result="clean", seconds=200.0, batch=1000),
    "e4": rmeta.RunMeta(result="clean", seconds=9.0, batch=100),
    "e5": rmeta.RunMeta(result="failed", seconds=7.0, batch=1000),
}
check("the median of the recent completed runs of a similar size",
      rmeta.estimate_seconds(1000, runs, metas) == 200.0)
check("stopped, failed and much smaller runs are left out",
      rmeta.estimate_seconds(1400, runs, metas) == 200.0
      and rmeta.estimate_seconds(100, runs, metas) == 9.0)
check("nothing similar, or nothing at all: no estimate",
      rmeta.estimate_seconds(3000, runs, metas) is None
      and rmeta.estimate_seconds(1000, [], {}) is None
      and rmeta.estimate_seconds(0, runs, metas) is None)
check("only the newest few count",
      rmeta.estimate_seconds(1000, runs, metas, recent=1) == 100.0)
check("a run without a batch in the side file is sized by what it moved",
      rmeta.estimate_seconds(1000, runs[6:], {"e6": rmeta.RunMeta(result="clean", seconds=60.0)})
      == 60.0)

# ==== the standalone rebuild ===================================================================

print("-- Rebuild playlist now --")
w = world("rebuild", reserve=4, current=4, count=4, refresh=False)
record = runner.RotationRun(w.cfg, w.history).execute()     # moves without rebuilding
w.history = History.load()
check("after a run that did not rebuild, the playlist lists the batch before",
      playlist_folders(w) == ["c0", "c1", "c2", "c3"])
events = []
ENGINE["calls"] = []
rebuild = runner.PlaylistRebuild(str(w.myprojects), runs=w.history.runs, progress=events.append)
refresh = rebuild.execute()
check("it finds that playlist by the batch an earlier run moved in, and refills it",
      rebuild.result == "clean" and refresh.rebuilt
      and playlist_folders(w) == sorted(["[protected] keep0", *record.moved]))
check("reported as one step, three stages", compress(e.step for e in events) == [0]
      and events[-1].current == 3 and events[-1].total == 3
      and ENGINE["calls"] == ["running", "close", "start"])
w2 = world("rebuild-no-history", reserve=4, current=4, count=4, refresh=False)
runner.RotationRun(w2.cfg, w2.history).execute()
rebuild = runner.PlaylistRebuild(str(w2.myprojects))
rebuild.execute()
check("without the history it cannot tell, and says so rather than guess",
      rebuild.result == "problems" and not rebuild.refresh.rebuilt
      and any("No playlist" in p for p in rebuild.refresh.problems))
check("an unset myprojects is refused", runner.PlaylistRebuild("").execute() is None)

# ==== nothing written may be lost ==============================================================

print("-- the history read by 2.1.1 (a rollback) --")


@dataclass
class RunRecord211:
    """RunRecord as 2.1.1 had it, verbatim."""
    id: str
    timestamp: str
    moved: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    returned: int = 0
    failed: list[str] = field(default_factory=list)
    history_reset: bool = False


def load_211(path: Path) -> list[RunRecord211]:
    """2.1.1's History.load, verbatim but for the path: any error is no history."""
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return [RunRecord211(**r) for r in data.get("runs", [])]
        except Exception:
            pass
    return []


allowed = {f.name for f in fields(RunRecord211)}
for tag in ("normal", "stop-return", "stop-move", "retry", "retry-again", "retry-old",
            "rebuild"):
    path = TMP / tag / "history.json"
    new = rc._read_runs(path)
    raw = json.loads(path.read_text(encoding="utf-8"))["runs"]
    old = load_211(path)
    check(f"{tag}: 2.1.1 gets every run back, with the same contents",
          len(old) == len(new) >= 2 and [asdict(r) for r in old] == [
              {k: getattr(n, k) for k in allowed} for n in new]
          and all(set(r) == allowed for r in raw))

cfg_path = TMP / "normal" / "config.json"
rc.CONFIG_PATH = cfg_path
cfg_path.write_text(json.dumps({"source": "D:\\made-up\\reserve", "count": "many",
                                "refresh_playlist": 1, "from_a_newer_build": {"x": 1}}),
                    encoding="utf-8")
c = Config.load()
check("a setting of the wrong type falls back to its default, alone",
      c.count == 1000 and c.refresh_playlist is True and c.source == "D:\\made-up\\reserve")
c.save()
saved = json.loads(cfg_path.read_text(encoding="utf-8"))
check("a key from a newer build is carried through a save", saved["from_a_newer_build"] == {"x": 1})


@dataclass
class Config211:
    source: str = ""
    destination: str = ""
    duplicates: str = ""
    count: int = 1000
    refresh_playlist: bool = True


data = json.loads(cfg_path.read_text(encoding="utf-8"))
check("and 2.1.1's loader still reads the file",
      Config211(**{k: data.get(k, getattr(Config211(), k)) for k in Config211().__dict__}).source
      == "D:\\made-up\\reserve")

print("-- a history that does not read is never saved over --")
w = world("corrupt", reserve=3, current=1, count=1)
garbage = b'{"runs": [{"id": "a1", "timestamp": "2026-09-01 10:00:00", "moved": ["x"'
rc.HISTORY_PATH.write_bytes(garbage)
for snap in rc.snapshots():
    snap.unlink()
h = History.load()
kept = sorted(w.base.glob("history.unreadable-*.json"))
check("it is kept, renamed, byte for byte, and the history says so",
      len(kept) == 1 and kept[0].read_bytes() == garbage and "could not be read" in h.notice)
events = []
record = runner.RotationRun(w.cfg, h, progress=events.append).execute()
check("the next run's log opens with that notice",
      any(e.level == "WARN" and "could not be read" in e.message for e in events[:3]))
check("the run is saved to a new history.json; the kept file is untouched",
      [r.id for r in rc.read_runs()] == [record.id] and kept[0].read_bytes() == garbage)

rc.HISTORY_PATH.write_bytes(garbage)
real_set_aside = rc._set_aside
rc._set_aside = lambda path: None
try:
    h = History.load()
    listing = (names(w.reserve), names(w.myprojects))
    run = runner.RotationRun(w.cfg, h)
    record = run.execute()
    check("one that cannot even be moved aside stops the run before anything moves",
          record is None and run.result == "failed" and "could not be read" in run.error
          and (names(w.reserve), names(w.myprojects)) == listing)
    try:
        h.save()
        refused = False
    except RuntimeError:
        refused = True
    check("and refuses to be saved over", refused and rc.HISTORY_PATH.read_bytes() == garbage)

    rc.CONFIG_PATH.write_bytes(b"{ not json")
    c = Config.load()
    try:
        c.save()
        refused = False
    except OSError:
        refused = True
    check("the same for config.json: defaults meanwhile, and never saved over",
          c.problem and refused and rc.CONFIG_PATH.read_bytes() == b"{ not json")

    path = rmeta.meta_path()
    path.write_bytes(b"[not the side file")
    try:
        rmeta.record_meta("abc", rmeta.RunMeta(result="clean"))
        refused = False
    except OSError:
        refused = True
    check("the same for run_meta.json", refused and path.read_bytes() == b"[not the side file")
finally:
    rc._set_aside = real_set_aside

print("-- the side file --")
w = world("side-file")
path = rmeta.meta_path()
entry = rmeta.RunMeta(started="2026-09-30T13:41:00", finished="2026-09-30T13:58:12",
                      seconds=1032.4, result="problems", batch=1000, protected=2,
                      returned_failed=["a"], moved_failed=["b"], playlist=["line"],
                      playlist_rebuilt=True, history_reset=True, log="rotator/run-x.log",
                      step_times={"return": "2026-09-30T13:46:03"})
rmeta.record_meta("x0000001", entry)
back = rmeta.read_meta()["x0000001"]
check("an entry reads back as it was written", back.to_dict() == entry.to_dict())
data = json.loads(path.read_text(encoding="utf-8"))
data["x0000001"]["from_the_future"] = [1, 2]
data["x0000001"]["seconds"] = "a while"
data["garbled1"] = "not an entry"
path.write_text(json.dumps(data), encoding="utf-8")
rmeta.record_meta("x0000002", rmeta.RunMeta(result="clean"))
data = json.loads(path.read_text(encoding="utf-8"))
check("unknown keys, and entries that do not read, are written back as they were",
      data["x0000001"]["from_the_future"] == [1, 2] and data["garbled1"] == "not an entry"
      and set(data) == {"x0000001", "x0000002", "garbled1"})
check("a value of the wrong type is read as not known",
      rmeta.read_meta()["x0000001"].seconds is None)
path.write_bytes(b"\x00\x01 garbage")
store = rmeta.RunMetaStore.load()
kept = list(w.base.glob("run_meta.unreadable-*.json"))
check("an unreadable side file is kept aside and a new one begun",
      store.entries == {} and len(kept) == 1 and kept[0].read_bytes() == b"\x00\x01 garbage"
      and "could not be read" in store.notice)
check("a reader never writes: a missing or broken file is simply empty",
      rmeta.read_meta(w.base / "nothing.json") == {} and rmeta.read_meta(kept[0]) == {})

# ==== library index v2 =========================================================================

print("-- library index v2 --")
lib_root = TMP / "library"
L_RES, L_CUR = lib_root / "reserve", lib_root / "myprojects"
for d in (L_RES, L_CUR):
    d.mkdir(parents=True)
wallpaper(L_RES, "tidal-glass", title="Tidal Glass", kind="Video", size=1234)
wallpaper(L_RES, "copper-sky", manifest={"title": "  Copper Sky ", "type": "scene",
                                       "workshopid": 3000000001, "preview": "sub\\prev.gif"})
wallpaper(L_RES, "no-manifest", manifest=None)
wallpaper(L_RES, "not-json", manifest="{ nope")
wallpaper(L_RES, "a-list", manifest="[1, 2]")
wallpaper(L_RES, "odd-types", manifest={"title": 42, "type": ["x"], "workshopid": "12ab"})
bom = L_RES / "with-bom"
bom.mkdir()
(bom / "project.json").write_bytes(b"\xef\xbb\xbf" + json.dumps({"title": "Bom"}).encode())
for i in range(5):
    wallpaper(L_CUR, f"cur{i}")
library_json = TMP / "data" / "library.json"
old_library = lib_mod.Library(workshop=None, roots=[L_RES, L_CUR], index_path=library_json)
old_library.refresh()
library_before = library_json.read_bytes()

reads: list[str] = []
real_describe = li.describe


def counting_describe(folder, name, mtime):
    reads.append(name)
    return real_describe(folder, name, mtime)


li.describe = counting_describe
index_file = TMP / "data" / li.FILE_NAME
index = li.LibraryIndex(index_file)
index.load()
report = index.refresh([L_RES, L_CUR])
check("a cold walk reads every folder once", report.read == 12 and len(reads) == 12
      and report.complete and index.complete(L_RES) and index.complete(L_CUR))
f = index.folders(L_RES)
check("title, type, workshop id and preview come from project.json",
      f["tidal-glass"].title == "Tidal Glass" and f["tidal-glass"].kind == "video"
      and f["copper-sky"].title == "Copper Sky" and f["copper-sky"].workshop_id == "3000000001"
      and f["copper-sky"].preview == "prev.gif" and f["tidal-glass"].preview == "preview.jpg")
check("no readable project.json is Unidentified: missing, not JSON, not an object",
      all(f[n].unidentified for n in ("no-manifest", "not-json", "a-list"))
      and not f["tidal-glass"].unidentified)
check("values of the wrong type are left empty, not guessed",
      (f["odd-types"].title, f["odd-types"].kind, f["odd-types"].workshop_id) == ("", "", "")
      and not f["odd-types"].unidentified and f["with-bom"].title == "Bom")
check("sizes are not measured by the walk", all(i.size is None for i in f.values()))
check("library.json, Review's index, is left exactly as it was",
      library_json.read_bytes() == library_before)
stored = json.loads(index_file.read_text(encoding="utf-8"))
check("library_meta.json is its own file, in the documented shape",
      stored["format"] == 1 and stored["roots"][os.path.normpath(str(L_RES))]["complete"] is True
      and stored["roots"][os.path.normpath(str(L_RES))]["folders"]["no-manifest"]["u"] == 1)

reads.clear()
again = li.LibraryIndex(index_file)
again.load()
t = (L_RES / "copper-sky").stat().st_mtime + 100
os.utime(L_RES / "copper-sky", (t, t))
report = again.refresh([L_RES, L_CUR])
check("touch one folder: only it is read again", reads == ["copper-sky"] and report.reused == 11)

reads.clear()
shutil.move(str(L_CUR / "cur0"), str(L_RES / "cur0"))
shutil.move(str(L_RES / "tidal-glass"), str(L_CUR / "tidal-glass"))
report = again.refresh([L_RES, L_CUR])
check("folders moved between the two are carried across, not read",
      reads == [] and report.carried == 2 and again.get(L_CUR, "tidal-glass").title == "Tidal Glass"
      and again.get(L_RES, "tidal-glass") is None)
shutil.rmtree(L_CUR / "cur1")
report = again.refresh([L_RES, L_CUR])
check("a folder that is gone is dropped", report.removed == 1 and again.get(L_CUR, "cur1") is None)

sizes = again.measure(L_CUR, ["tidal-glass", "cur2", "not-there"])
check("measuring gives the bytes of every file, and keeps them",
      sizes == {"tidal-glass": 1234 + 1 + len(json.dumps({"file": "clip.mp4",
                "title": "Tidal Glass", "type": "Video", "preview": "preview.jpg"})),
                "cur2": 10 + 1 + len(json.dumps({"file": "clip.mp4", "title": "Cur2",
                "type": "video", "preview": "preview.jpg"}))}
      and again.get(L_CUR, "cur2").size == sizes["cur2"])
check("the total says what is not measured yet",
      again.total_size(L_CUR) == (sum(sizes.values()), 2))
t = (L_CUR / "cur2").stat().st_mtime + 100
os.utime(L_CUR / "cur2", (t, t))
again.refresh([L_CUR])
check("a folder read again is measured again", again.get(L_CUR, "cur2").size is None)
again.save()
check("sizes are saved with the rest",
      li.LibraryIndex(index_file).get(L_CUR, "tidal-glass") is None
      and (lambda x: (x.load(), x.get(L_CUR, "tidal-glass").size)[1])(li.LibraryIndex(index_file))
      == sizes["tidal-glass"])

print("-- library index v2: a walk stopped half-way, and garbage --")
reads.clear()
li.SAVE_EVERY_SECONDS = 0.0
fresh_file = TMP / "data" / "resume.json"
seen = {"n": 0}


def stop_after_three():
    seen["n"] += 1
    return seen["n"] > 3


partial = li.LibraryIndex(fresh_file)
report = partial.refresh([L_RES], cancelled=stop_after_three)
check("a walk stopped part-way saves what it read, as not complete",
      not report.complete and len(reads) == 3 and not partial.complete(L_RES)
      and fresh_file.exists())
reads.clear()
resumed = li.LibraryIndex(fresh_file)
resumed.load()
check("and a new start knows those", len(resumed.folders(L_RES)) == 3
      and not resumed.complete(L_RES))
report = resumed.refresh([L_RES])
check("it carries on from there: only the rest is read",
      report.complete and len(reads) == len(names(L_RES)) - 3 and report.reused == 3)
li.SAVE_EVERY_SECONDS = 15.0

fresh_file.write_text('{"format": 1, "roots": {"x": {"folders": {"a": {"m": "soon"}, '
                      '"b": 7, "c": {"m": 5, "t": ["no"]}}}, "y": 3}}', encoding="utf-8")
junk = li.LibraryIndex(fresh_file)
junk.load()
check("entries that are not entries are dropped, to be read again",
      junk.folders("x") == {} and junk.roots() == ["x"])
fresh_file.write_bytes(b"\xff\xfe not json")
junk = li.LibraryIndex(fresh_file)
junk.load()
report = junk.refresh([L_CUR])
check("an index that does not read is built again, and says so",
      "could not be read" in junk.notice and report.complete
      and len(junk.folders(L_CUR)) == len(names(L_CUR)))
check("an unset or missing root is skipped, never the working directory",
      junk.refresh(["", "relative", TMP / "missing"]).missing == [str(TMP / "missing")]
      and all(r not in (".", "", "relative") for r in junk.roots()))
li.describe = real_describe

print("-- library index v2: authors from the Steam cache --")
cache_db = TMP / "data" / "steam_cache.sqlite"
with sqlite3.connect(cache_db) as conn:
    conn.execute("CREATE TABLE cache (kind TEXT, key TEXT, fetched INTEGER, payload TEXT, "
                 "PRIMARY KEY (kind, key))")
    conn.execute("INSERT INTO cache VALUES ('item.2', '3000000001', 1, ?)",
                 (json.dumps({"id": "3000000001", "ok": True,
                              "creator": "76561190000000009"}),))
    conn.execute("INSERT INTO cache VALUES ('profile', '76561190000000009', 1, ?)",
                 (json.dumps({"id64": "76561190000000009", "name": "Pellworm"}),))
check("a workshop id's author is what Review already asked Steam, nothing more",
      again.authors(cache_path=cache_db) == {"3000000001": "Pellworm"}
      and again.authors(L_CUR, cache_path=cache_db) == {})

print("-- library.json keeps its old shape --")
old_library.refresh()
old_data = json.loads(library_json.read_text(encoding="utf-8"))
entries = old_data["roots"][str(L_RES)]["folders"]
check("Review's index is still {name: [mtime, id]}, written whole",
      all(isinstance(v, list) and len(v) == 2 for v in entries.values())
      and entries["copper-sky"][1] == "3000000001"
      and not library_json.with_name("library.json.tmp").exists())
library_json.write_text('["not", "an", "index"]', encoding="utf-8")
check("and a library.json that is not an index reads as none, rather than raising",
      not lib_mod.Library(workshop=None, roots=[], index_path=library_json).scanned)

# ==== the workers ===============================================================================

print("-- the workers --")
from PySide6.QtCore import QCoreApplication                     # noqa: E402
from app.engines.rotator import worker as rworker               # noqa: E402

app = QCoreApplication.instance() or QCoreApplication(sys.argv)


def wait_for(thread, seconds: float = 20.0) -> bool:
    finished = thread.wait(int(seconds * 1000))
    app.processEvents()
    return finished


w = world("workers", reserve=4, current=2, count=2)
got = {"events": [], "done": [], "errors": []}
rotation = rworker.RotationWorker(w.cfg, w.history, check_finished=CHECKED)
rotation.progress.connect(got["events"].append)
rotation.finished_run.connect(got["done"].append)
rotation.error.connect(got["errors"].append)
run_id = rotation.run_id
rotation.start()
check("RotationWorker runs a rotation on its thread",
      wait_for(rotation) and len(got["done"]) == 1 and got["done"][0].id == run_id
      and rotation.result == "clean" and not got["errors"])
check("its events carry their steps, its plan is known before it starts",
      [s.key for s in rotation.steps] == ["check", "return", "move", "playlist"]
      and compress(e.step for e in got["events"]) == [1, 2, 3])

bad = Config(source="", destination=str(w.myprojects), duplicates=str(w.dupes))
failing = rworker.RotationWorker(bad, w.history)
errors: list[str] = []
failing.error.connect(errors.append)
failing.start()
check("one that cannot start says why", wait_for(failing) and errors
      and "reserve folder is not set" in errors[0])

w = world("workers-retry", reserve=3, current=2, count=3)
core.shutil = Flaky({"c0"})
record = runner.RotationRun(w.cfg, w.history).execute()
core.shutil = shutil
retried: list = []
retry_worker = rworker.RetryWorker(w.cfg, w.history, record)
retry_worker.finished_retry.connect(retried.append)
retry_worker.start()
check("RetryWorker retries on its thread", wait_for(retry_worker) and retried
      and retried[0].fixed == 1 and retry_worker.result == "clean")

rebuilt: list = []
rebuild_worker = rworker.PlaylistRebuildWorker(str(w.myprojects), w.history.runs)
rebuild_worker.finished_rebuild.connect(rebuilt.append)
rebuild_worker.start()
check("PlaylistRebuildWorker rebuilds on its thread",
      wait_for(rebuild_worker) and rebuilt and rebuilt[0].rebuilt
      and rebuild_worker.result == "clean")

for name in ("dup-a", "dup-b"):
    wallpaper(w.dupes, name, size=50)
wallpaper(w.reserve, "dup-a")
listed: list = []
dup_worker = rworker.DuplicatesListWorker(w.cfg)
dup_worker.listed.connect(listed.append)
dup_worker.start()
check("DuplicatesListWorker lists the duplicates with their sizes",
      wait_for(dup_worker) and [d.name for d in listed[0]] == ["dup-a", "dup-b"]
      and listed[0][0].in_reserve and not listed[0][1].in_reserve
      and listed[0][1].size > 50 and listed[0][1].files == 3)

index_file.unlink()
got = {"loaded": [], "walked": [], "sized": {}, "authors": []}
index_worker = li.LibraryIndexWorker([L_RES, L_CUR], li.LibraryIndex(index_file),
                                     fill_sizes=False, cache_path=cache_db)
index_worker.loaded.connect(lambda root, folders: got["loaded"].append(root))
index_worker.walked.connect(got["walked"].append)
index_worker.authors.connect(got["authors"].append)
index_worker.sized.connect(lambda root, sizes: got["sized"].update(sizes))
index_worker.start()
deadline = time.monotonic() + 20
while time.monotonic() < deadline and not got["authors"]:
    app.processEvents()
    time.sleep(0.02)
index_worker.request_sizes(str(L_CUR), ["cur3", "cur4"])
while time.monotonic() < deadline and len(got["sized"]) < 2:
    app.processEvents()
    time.sleep(0.02)
check("LibraryIndexWorker walks, names the authors, then measures what it is asked",
      len(got["walked"]) == 1 and got["walked"][0].complete
      and got["authors"][0] == {"3000000001": "Pellworm"}
      and set(got["sized"]) == {"cur3", "cur4"})
index_worker.stop()
check("and stops when told, having saved", wait_for(index_worker, 10) and index_file.exists())

got = {"sized": {}}
filler = li.LibraryIndexWorker([L_CUR], li.LibraryIndex(index_file), fill_sizes=True)
filler.sized.connect(lambda root, sizes: got["sized"].update(sizes))
filler.start()
deadline = time.monotonic() + 20
while time.monotonic() < deadline and len(got["sized"]) < len(names(L_CUR)) - 2:
    app.processEvents()
    time.sleep(0.02)
filler.stop()
wait_for(filler, 10)
done = li.LibraryIndex(index_file)
done.load()
check("with fill_sizes the rest are measured in the background, and kept",
      done.total_size(L_CUR)[1] == 0)

shutil.rmtree(TMP, ignore_errors=True)
failed = sum(1 for r in results if not r)
print()
print(f"{'FAILED' if failed else 'PASSED'} {len(results) - failed}/{len(results)}")
sys.exit(1 if failed else 0)
