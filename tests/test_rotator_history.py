"""The Rotator's history: it is never lost quietly.

Run it directly — there is no test framework in this project:

    .venv\\Scripts\\python.exe tests\\test_rotator_history.py

On 2026-09-20 a build emptied the folder the history was in. Nothing said so,
and the next rotation, six days later, started a new history of one run. These
checks hold the history to the opposite: a missing or unreadable file is put
back from a snapshot and said out loud, an unreadable one is never written
over, and a key from a newer version is carried rather than fatal.

Everything is written under a temporary folder.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.engines.rotator import config as rc                  # noqa: E402
from app.engines.rotator.config import Config, History, RunRecord   # noqa: E402
from app.engines.rotator.core import Rotator                   # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="wallpaper_history_test_"))

results: list[bool] = []


def check(label: str, condition: bool) -> None:
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label)


def fresh(name: str) -> Path:
    """Point the history and config at an empty folder of their own."""
    folder = TMP / name
    folder.mkdir()
    rc.HISTORY_PATH = folder / "history.json"
    rc.CONFIG_PATH = folder / "config.json"
    return folder


def run(i: int, *names: str) -> RunRecord:
    return RunRecord(id=f"run{i:05d}", timestamp=f"2026-09-{10 + i:02d} 12:00:00",
                     moved=list(names) or [f"folder-{i}"], returned=i)


def stored_runs() -> list[str]:
    return [r["id"] for r in json.loads(rc.HISTORY_PATH.read_text(encoding="utf-8"))["runs"]]


# ---- saving -------------------------------------------------------------------

print("-- saving --")
folder = fresh("save")
h = History.load()
check("no file and no snapshots is a first start, said nothing about",
      h.runs == [] and h.notice == "" and h.problem == "")
h.add(run(1))
h.add(run(2))
check("each run is saved, newest first", stored_runs() == ["run00002", "run00001"])
check("no temporary file is left beside it",
      not rc.HISTORY_PATH.with_name("history.json.tmp").exists())
snaps = rc.snapshots()
check("a snapshot is kept after each save, named for its size",
      snaps and snaps[0].name.endswith("-0002runs.json")
      and json.loads(snaps[0].read_text(encoding="utf-8")) ==
      json.loads(rc.HISTORY_PATH.read_text(encoding="utf-8")))

# ---- the file goes missing -------------------------------------------------------

print("-- history.json goes missing (what happened on 2026-09-20) --")
rc.HISTORY_PATH.unlink()
h = History.load()
check("it is put back from the newest snapshot", [r.id for r in h.runs] == ["run00002", "run00001"])
check("and written back to disk at once", stored_runs() == ["run00002", "run00001"])
check("and the notice says what happened and from where",
      "missing" in h.notice and "2 runs were put back" in h.notice and h.problem == "")
h.add(run(3))
check("the next rotation adds to it instead of starting over",
      stored_runs() == ["run00003", "run00002", "run00001"])
check("and what it avoids repeating still includes the older runs",
      {"folder-1", "folder-2", "folder-3"} <= h.used_set())

# ---- the file cannot be read ------------------------------------------------------

print("-- history.json cannot be read --")
rc.HISTORY_PATH.write_text('{"runs": [{"id": "run0', encoding="utf-8")   # cut short
h = History.load()
kept = sorted(folder.glob("history.unreadable-*.json"))
check("the unreadable file is set aside, not overwritten",
      len(kept) == 1 and kept[0].read_text(encoding="utf-8") == '{"runs": [{"id": "run0')
check("the newest snapshot is put back", [r.id for r in h.runs][:1] == ["run00003"])
check("and the notice names the file it kept",
      "could not be read" in h.notice and kept[0].name in h.notice)

folder = fresh("unreadable-no-snapshot")
rc.HISTORY_PATH.write_text("not json at all", encoding="utf-8")
h = History.load()
kept = sorted(folder.glob("history.unreadable-*.json"))
check("with no snapshot either, the file is still kept, and the history starts again, said so",
      len(kept) == 1 and h.runs == [] and "no snapshot" in h.notice and h.problem == "")
h.add(run(1))
check("the next save writes a new file and leaves the kept one alone",
      stored_runs() == ["run00001"]
      and kept[0].read_text(encoding="utf-8") == "not json at all")

for bad, why in (('{"history": []}', "no list of runs"),
                 ('{"runs": [{"id": "x", "timestamp": "t", "moved": "a,b"}]}', "a list")):
    folder = fresh("bad-" + str(len(results)))
    rc.HISTORY_PATH.write_text(bad, encoding="utf-8")
    h = History.load()
    check(f"a file of the wrong shape is set aside too ({why})",
          h.runs == [] and why in h.notice
          and len(list(folder.glob("history.unreadable-*.json"))) == 1)

print("-- a file that can be neither read nor moved --")
folder = fresh("stuck")
rc.HISTORY_PATH.write_text("garbage", encoding="utf-8")
real_set_aside = rc._set_aside
rc._set_aside = lambda path: None
try:
    h = History.load()
finally:
    rc._set_aside = real_set_aside
check("the history says what is wrong and refuses to save",
      h.problem and "could not be" in h.problem)
try:
    h.add(run(1))
    refused = False
except RuntimeError:
    refused = True
check("saving raises rather than writing over it",
      refused and rc.HISTORY_PATH.read_text(encoding="utf-8") == "garbage")
reserve, myprojects, dupes = (folder / n for n in ("reserve", "myprojects", "dupes"))
for f in (reserve, myprojects, dupes):
    f.mkdir()
cfg = Config(source=str(reserve), destination=str(myprojects), duplicates=str(dupes))
check("and a rotation will not start", Rotator(cfg, h).validate() == h.problem)

# ---- a newer version's keys --------------------------------------------------------

print("-- keys this version does not know --")
folder = fresh("extra")
rc.HISTORY_PATH.write_text(json.dumps({"runs": [
    {"id": "new1", "timestamp": "2026-10-01 12:00:00", "moved": ["a"], "duplicates": [],
     "returned": 1, "failed": [], "history_reset": False,
     "playlist": {"monitor": "Monitor1", "items": 200}},
]}), encoding="utf-8")
h = History.load()
check("the run is read, not the whole history thrown away",
      [r.id for r in h.runs] == ["new1"] and h.notice == "")
h.add(run(2))
again = json.loads(rc.HISTORY_PATH.read_text(encoding="utf-8"))["runs"]
check("and the unknown key is written back as it was",
      again[1]["playlist"] == {"monitor": "Monitor1", "items": 200}
      and "playlist" not in again[0])

# ---- snapshots are kept, but not forever ---------------------------------------------

print("-- how many snapshots --")
folder = fresh("retention")
rc.snapshot_dir().mkdir()
for i in range(rc.KEEP_SNAPSHOTS + 5):
    (rc.snapshot_dir() / f"history-20260101-{i:06d}-1runs.json").write_text(
        '{"runs": []}', encoding="utf-8")
h = History.load()
h.add(run(1))
check(f"only the newest {rc.KEEP_SNAPSHOTS} are kept, the one just made among them",
      len(rc.snapshots()) == rc.KEEP_SNAPSHOTS
      and rc.snapshots()[0].name.endswith("-0001runs.json")
      and not rc.snapshots()[0].name.startswith("history-20260101"))
empty = History([])
before = len(rc.snapshots())
empty.save()
check("an empty history is saved but not snapshotted", len(rc.snapshots()) == before)

# ---- the tab says so ----------------------------------------------------------------------

print("-- the History tab --")
import os                                                   # noqa: E402
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication                  # noqa: E402
qt = QApplication.instance() or QApplication(sys.argv)
from app import theme                                       # noqa: E402
theme.apply(qt)
from app.ui import rotator_tab                              # noqa: E402

def tab_config(folder: Path) -> None:
    """Folders of its own, so the tab lists nothing on this machine."""
    for name in ("reserve", "myprojects", "dupes"):
        (folder / name).mkdir()
    Config(source=str(folder / "reserve"), destination=str(folder / "myprojects"),
           duplicates=str(folder / "dupes")).save()


folder = fresh("tab")
tab_config(folder)
History([]).add(run(1))
rc.HISTORY_PATH.unlink()
tab = rotator_tab.RotatorTab()
check("a history put back from a snapshot is shown with the notice above it",
      tab.history_summary.text() == "History: 1 runs"
      and not tab.history_notice.isHidden()
      and "missing" in tab.history_notice.text())
tab.deleteLater()

folder = fresh("tab-quiet")
tab_config(folder)
History([]).add(run(1))
tab = rotator_tab.RotatorTab()
check("a history that simply read shows no notice", tab.history_notice.isHidden())
tab.deleteLater()

# ---- the Rotator's settings -------------------------------------------------------------

print("-- config.json cannot be read --")
folder = fresh("config")
rc.CONFIG_PATH.write_text('{"source": "D:\\\\Wallpapers\\\\reserve", "cou', encoding="utf-8")
c = Config.load()
kept = sorted(folder.glob("config.unreadable-*.json"))
check("the defaults are used and saved, and the unreadable file is kept beside them",
      c.source == "" and rc.CONFIG_PATH.exists() and len(kept) == 1
      and "Wallpapers" in kept[0].read_text(encoding="utf-8"))

shutil.rmtree(TMP, ignore_errors=True)

print()
print("PASSED %d/%d" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
