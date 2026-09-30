"""The reserve check: what counts as an unusable folder, and what is offered.

Run it directly — there is no test framework in this project:

    .venv\\Scripts\\python.exe tests\\test_rotator_cleanup.py

Everything works on a temporary reserve built here, so nothing on the machine is
read or deleted. Two sections build real widgets (they need Qt, but never show a
window): the broken-folders confirmation, to check what it ticks by default —
that default is the whole safety story of the feature, so it is worth a test
rather than a glance — and the Rotator page with its folders unset, run from a
stand-in install folder, to check that nothing in it is listed or deleted.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.engines.rotator import config as rotator_config   # noqa: E402
from app.engines.rotator import core                       # noqa: E402
from app.engines.rotator.config import Config, History     # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="wallpaper_cleanup_test_"))
RESERVE = TMP / "reserve"
RESERVE.mkdir(parents=True)

results: list[bool] = []


def check(label: str, condition: bool) -> None:
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label)


def folder(name: str, files: dict[str, bytes] | None = None) -> Path:
    d = RESERVE / name
    d.mkdir(parents=True, exist_ok=True)
    for rel, data in (files or {}).items():
        target = d / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return d


# --------------------------------------------------------- what is a wallpaper

video = folder("video-ok", {"project.json": b'{"file":"a.mp4"}', "a.mp4": b"x" * 10})
check("a folder with a project.json is left alone",
      core.inspect_folder(video, "video-ok") is None)

# A scene wallpaper ships its scene.json packed inside scene.pkg, so the file
# the manifest names is not on disk. That is normal and must not read as broken.
scene = folder("scene-packed", {"project.json": b'{"file":"scene.json"}',
                                "scene.pkg": b"packed", "preview.jpg": b"p"})
check("a scene wallpaper packed into scene.pkg is not broken",
      core.inspect_folder(scene, "scene-packed") is None)

# ------------------------------------------------------------ what is not

shaders = folder("shader-cache", {"shaders/blobsSM40/aaa.dxs": b"blob",
                                  "shaders/blobsSM40/bbb.dxs": b"blob"})
found = core.inspect_folder(shaders, "shader-cache")
check("a folder holding only the shader cache is unusable",
      found is not None and found.reason == core.REASON_SHADERS)
check("and it is safe to delete", found.safe_to_delete)
check("its contents come along so they can be looked at",
      sorted(found.entries) == [str(Path("shaders/blobsSM40/aaa.dxs")),
                                str(Path("shaders/blobsSM40/bbb.dxs"))])
check("with the file count and size", (found.files, found.size) == (2, 8))

empty = folder("nothing-here")
found = core.inspect_folder(empty, "nothing-here")
check("an empty folder is unusable", found is not None and found.reason == core.REASON_EMPTY)
check("and safe to delete too", found.safe_to_delete)

orphan = folder("lost-manifest", {"5月26日.mp4": b"video-bytes"})
found = core.inspect_folder(orphan, "lost-manifest")
check("a folder whose manifest is gone but whose media is not is flagged apart",
      found is not None and found.reason == core.REASON_ORPHAN)
check("and it is NOT offered as safe to delete", not found.safe_to_delete)

junk = folder("just-junk", {"readme.txt": b"notes"})
found = core.inspect_folder(junk, "just-junk")
check("anything else without a manifest is simply unusable",
      found is not None and found.reason == core.REASON_NO_MANIFEST)

# The dialog only shows a slice of a big folder; the count must still be whole.
many = folder("many-files", {f"shaders/f{i}.dxs": b"x" for i in range(core.ENTRY_CAP + 25)})
found = core.inspect_folder(many, "many-files")
check("a long folder is listed only in part",
      len(found.entries) == core.ENTRY_CAP)
check("but counted in full", found.files == core.ENTRY_CAP + 25)

# ------------------------------------------------------------------ the scan

seen = core.scan_invalid(RESERVE)
names = sorted(b.name for b in seen)
check("the scan finds every unusable folder and no others",
      names == ["just-junk", "lost-manifest", "many-files", "nothing-here",
                "shader-cache"])
check("each one knows where it lives",
      all(Path(b.root) == RESERVE and Path(b.path) == RESERVE / b.name for b in seen))

events: list = []
core.scan_invalid(RESERVE, progress=events.append)
check("the scan reports its own size, so nothing has to list the folders twice",
      max(e.total for e in events) == 7)

stop = {"now": False}


def cancel_after_first(_e) -> None:
    stop["now"] = True


check("a cancelled scan gives back nothing to delete",
      core.scan_invalid(RESERVE, progress=cancel_after_first,
                        cancelled=lambda: stop["now"]) == [])

# --------------------------------------------------------------- the deleting

doomed = [str(RESERVE / "shader-cache"), str(RESERVE / "nothing-here")]
failed = core.delete_broken(doomed)
check("confirmed folders are deleted", failed == []
      and not (RESERVE / "shader-cache").exists()
      and not (RESERVE / "nothing-here").exists())
check("and the wallpapers next to them are untouched", (RESERVE / "video-ok").exists())
# A folder can vanish between the scan and the confirmation — deleted by hand,
# or carried off with a parent that was ticked on the same list. It is not there,
# which is what was asked for, so it must not come back as a failure.
check("a folder that is already gone is not reported as a failure",
      core.delete_broken([str(RESERVE / "shader-cache")]) == [])

# ------------------------------------------------- folders that are not set
#
# Path("") is the working directory, and for the built exe that is the install
# folder, with the user's data\ in it. An unset folder must list nothing and
# lose nothing. The working directory from here on is a stand-in install folder
# holding the same two folders, and the Rotator's own files are kept out of the
# source tree.

INSTALL = TMP / "install"
for inside in ("data", "_internal"):
    (INSTALL / inside).mkdir(parents=True)
    (INSTALL / inside / "keep.txt").write_bytes(b"live")
DUPES = TMP / "duplicates"
(DUPES / "dup-a").mkdir(parents=True)
MYPROJECTS = TMP / "myprojects"
MYPROJECTS.mkdir()
rotator_config.CONFIG_PATH = TMP / "rotator-config.json"
rotator_config.HISTORY_PATH = TMP / "rotator-history.json"
HOME = os.getcwd()
os.chdir(INSTALL)


def untouched() -> bool:
    """The stand-in install folder still holds what it held."""
    return (sorted(p.name for p in INSTALL.iterdir()) == ["_internal", "data"]
            and all((INSTALL / d / "keep.txt").exists() for d in ("data", "_internal")))


check("an empty folder setting is not set",
      not any(core.folder_is_set(p) for p in ("", "   ", None)))
check("nor is a relative one",
      not any(core.folder_is_set(p) for p in ("data", ".", "\\data", "C:data")))
check("a full path is", core.folder_is_set(str(DUPES)))

check("an unset folder lists nothing, not the working directory",
      core.list_subfolders("") == [] and core.list_subfolders(".") == [])
check("and a check of it finds nothing to delete", core.scan_invalid("") == [])

check("deleting from an unset duplicates folder deletes nothing",
      core.delete_folders("", ["data", "_internal"]) == ["data", "_internal"]
      and untouched())
check("nor from a relative one",
      core.delete_folders(".", ["data"]) == ["data"] and untouched())
check("moving back from an unset duplicates folder moves nothing",
      core.move_replace_to_reserve("", str(RESERVE), ["data"]) == ["data"]
      and untouched() and not (RESERVE / "data").exists())
check("nor into an unset reserve",
      core.move_replace_to_reserve(str(DUPES), "", ["dup-a"]) == ["dup-a"]
      and (DUPES / "dup-a").exists() and untouched())
# Replacing clears the target first, and with one folder for both the target
# is the folder about to be moved.
check("nor when the duplicates folder is the reserve",
      core.move_replace_to_reserve(str(DUPES), str(DUPES), ["dup-a"]) == ["dup-a"]
      and (DUPES / "dup-a").exists())
odd = ["", ".", "..", str(INSTALL / "data"), "dup-a\\..\\..", "C:data"]
check("a name that is not one folder name is refused, not joined",
      core.delete_folders(str(DUPES), odd) == odd
      and (DUPES / "dup-a").exists() and untouched())
check("the reserve check's delete refuses a relative path",
      core.delete_broken(["data"]) == ["data"] and untouched())

# ------------------------------------------------ listing the duplicates
#
# For the Duplicates dialog of the new Rotator page: what is there, with sizes.

check("listing an unset duplicates folder lists nothing, not the working directory",
      core.list_duplicates("") == [] and core.list_duplicates(".") == [] and untouched())
(DUPES / "dup-a" / "clip.mp4").write_bytes(b"x" * 300)
(DUPES / "dup-a" / "sub").mkdir()
(DUPES / "dup-a" / "sub" / "scene.pkg").write_bytes(b"y" * 20)
(DUPES / "dup-b").mkdir()
(RESERVE / "dup-a").mkdir()
listed = core.list_duplicates(str(DUPES), str(RESERVE))
check("each folder with its size and file count, by name",
      [(d.name, d.size, d.files) for d in listed] == [("dup-a", 320, 2), ("dup-b", 0, 0)])
check("and whether Move & replace would replace one in the reserve",
      [d.in_reserve for d in listed] == [True, False])
try:
    import _winapi
    _winapi.CreateJunction(str(INSTALL / "data"), str(DUPES / "dup-b" / "linked"))
    junction = True
except (ImportError, OSError, AttributeError):
    junction = False
if junction:
    check("a junction inside a duplicate is not followed, so nothing is counted twice",
          [d.size for d in core.list_duplicates(str(DUPES))] == [320, 0])
    os.rmdir(DUPES / "dup-b" / "linked")          # the link only, never what it points at
    check("and removing it left what it pointed at", untouched())
shutil.rmtree(DUPES / "dup-a" / "sub")
(DUPES / "dup-a" / "clip.mp4").unlink()
(DUPES / "dup-b").rmdir()
(RESERVE / "dup-a").rmdir()


def validate(**folders: str) -> str:
    cfg = Config(**{"source": str(RESERVE), "destination": str(MYPROJECTS),
                    "duplicates": str(DUPES), **folders})
    return core.Rotator(cfg, History([])).validate() or ""


check("a rotation with all three folders set may start", validate() == "")
check("not with the reserve unset",
      validate(source="") == "The reserve folder is not set.")
check("nor myprojects", validate(destination="") == "The myprojects folder is not set.")
check("nor the duplicates folder",
      validate(duplicates="") == "The duplicates folder is not set.")
check("nor with a relative folder, even one that is there",
      validate(source="data") == "The reserve folder is not a full path: data")
try:
    core.Rotator(Config(source="", destination="", duplicates="", count=1),
                 History([])).run()
    refused = False
except ValueError:
    refused = True
check("and a rotation run without validating refuses before moving anything",
      refused and untouched() and not rotator_config.HISTORY_PATH.exists())

check("sizes are shown in units a person reads",
      (core.human_size(512), core.human_size(2048), core.human_size(5 * 1024 ** 2))
      == ("512 B", "2.0 KB", "5.0 MB"))

# ------------------------------------------------------- what the dialog ticks
#
# Nothing is deleted without a tick, so the default matters more than anything
# else here: everything obviously dead is ticked, and a folder that still holds
# media is left for the user to decide about.

import time                                          # noqa: E402

from PySide6.QtWidgets import QApplication          # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)
from app import theme                               # noqa: E402
from app.pages import rotator as page_module        # noqa: E402

theme.apply(app)


def wait_for(condition, ms: int = 20_000) -> bool:
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.005)
    return condition()


broken = [
    core.BrokenFolder("shader-cache", core.REASON_SHADERS, str(RESERVE), files=2, size=8),
    core.BrokenFolder("nothing-here", core.REASON_EMPTY, str(RESERVE)),
    core.BrokenFolder("lost-manifest", core.REASON_ORPHAN, str(RESERVE),
                      files=1, size=11, holds_media=True),
]
dialog = page_module.broken_dialog(broken, "the reserve", None, scanned=7, embedded=True)


def ticked() -> list[str]:
    return sorted(Path(row.data).name for row in dialog.checked_rows())


check("by default the dialog ticks only what is certainly rubbish",
      ticked() == ["nothing-here", "shader-cache"])
check("and the running total is filled in from the start",
      dialog.summary_text().startswith("2 selected"))
dialog.set_group_checked(1, True)
check("ticking the media group reaches the ones holding media too", len(ticked()) == 3)
check("and the button says how many will go",
      dialog.confirm_button().text() == "Delete 3 permanently")
dialog.set_group_checked(0, False)
dialog.set_group_checked(1, False)
check("with nothing ticked there is nothing to delete", ticked() == [])
check("and the delete button cannot be pressed", not dialog.confirm_button().isEnabled())
dialog.set_group_checked(0, True)
check("the paths handed back are absolute, ready to delete",
      all(Path(row.data).is_absolute() for row in dialog.checked_rows()))
check("Cancel is where Enter goes: nothing is deleted by a stray key",
      dialog.is_destructive() and dialog.cancel_button().isDefault())
dialog.deleteLater()

# ------------------------------------------------ the Rotator page, unset
#
# The reported case end to end: no duplicates folder, and the working
# directory holding data\. The Duplicates tab listed data\ and _internal\ and
# "Delete all" would have deleted them. Every question is answered yes here, so
# only the guards stand between the click and the delete.

from app.engines.library_index import LibraryIndex  # noqa: E402

rotator_config.CONFIG_PATH.write_text(json.dumps(
    {"source": "", "destination": "", "duplicates": "", "count": 1,
     "refresh_playlist": False}), encoding="utf-8")
asked: list = []


def yes(dialog):
    """Every question answered yes; the Duplicates dialog with all of it chosen."""
    asked.append(dialog)
    if isinstance(dialog, page_module.DuplicatesDialog):
        wait_for(lambda: bool(dialog.model.items()))
        dialog.table.selectAll()
        dialog._act("delete")
        return dialog.result_value()
    if dialog.__class__.__name__ == "ConfirmDialog":
        dialog._answer(True)
        return dialog.result_value()
    return True


page = page_module.RotatorPage(Config.load(), index=LibraryIndex(TMP / "library_meta.json"))
page._answer = yes
page._ensure_loaded()
page._render()
check("with the folders unset, the tables list nothing and say why",
      page.models["reserve"].items() == [] and page.models["current"].items() == []
      and page.summary.items() == ["The reserve folder is not set"])
check("the next run says what is missing, and cannot start",
      page.texts()["next"]["problem"] == "The reserve folder is not set."
      and not page.plan.start.isEnabled())

page.messages.clear()
page.open_duplicates()
check("with no duplicates folder the Duplicates dialog does not open, and says why",
      asked == [] and page.messages[-1][0] == "warn"
      and "duplicates folder is not set" in page.messages[-1][1] and untouched())
for action in ("delete", "replace"):
    page.messages.clear()
    page._dup_action(action, ["data", "_internal"])
    check(f"'{action}' stops before starting, and says the folder is not set",
          page.dup_worker is None and "duplicates folder is not set" in page.messages[-1][1]
          and untouched())

page.messages.clear()
page.check_folders()
check("Check folders with nothing set says so and checks nothing",
      wait_for(lambda: page.messages) and page.scan_worker is None
      and page.messages[-1][0] == "danger" and "reserve folder is not set" in page.messages[-1][1])
page.messages.clear()
page.start_rotation()
check("and a rotation will not start",
      wait_for(lambda: page.messages) and page.rotation_worker is None
      and page.messages[-1] == ("danger", "Cannot start: The reserve folder is not set.")
      and untouched())

page.config.duplicates = str(DUPES)
page.messages.clear()
page.open_duplicates()
check("once the duplicates folder is set, what is in it is listed, and asked about",
      len(asked) == 2 and [d.name for d in asked[0].model.items()] == ["dup-a"]
      and str(DUPES) in asked[1].body() and asked[1].is_destructive())
check("the Move back button waits for the reserve", not asked[0].replace_button.isEnabled())
check("and Delete deletes it", wait_for(lambda: page.dup_worker is None)
      and not (DUPES / "dup-a").exists() and DUPES.exists() and untouched())
check("and says so when it is done", page.messages[-1] == ("ok", "1 duplicate deleted."))

# ------------------------------------------ a rotation from the page, reported
#
# The run's id is made before it starts, so its log is a file of its own —
# logs/rotator/run-<id>.log, the lines the page shows — and its side-file
# entry names that file.

from app import services                             # noqa: E402
from app.engines.rotator import meta as rotator_meta  # noqa: E402
from app.services import snapshot                    # noqa: E402

snapshot.compute = lambda keys, data_dir: {}         # count nothing on this machine
wired = services.Services(data_dir=TMP / "services")
services.install(wired)
for name in ("fresh-a", "fresh-b"):
    folder(name, {"project.json": b'{"file":"a.mp4"}', "a.mp4": b"x"})
(MYPROJECTS / "old-a").mkdir()
page.config.source, page.config.destination = str(RESERVE), str(MYPROJECTS)
page.config.duplicates, page.config.count, page.config.refresh_playlist = str(DUPES), 2, False
before = sorted(p.name for p in RESERVE.iterdir()) + ["old-a"]
asked.clear()
page.messages.clear()
page._confirm_and_rotate()          # the check is tested on its own reserve, in test_rotator_page
check("a rotation asks, then runs",
      wait_for(lambda: page.state == "done" or bool(page.messages)))
start = [d for d in asked if d.__class__.__name__ == "ConfirmDialog"]
check("the question names the run and what it will do",
      start and start[-1].title() == "Start run 1?"
      and start[-1].step_titles()[:2] == ["Return the previous batch to the reserve",
                                          "Move 2 new folders in"])
run_id = page.done_view.record.id if page.done_view else ""
log = TMP / "services" / "logs" / "rotator" / f"run-{run_id}.log"
lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
check("a rotation from the page logs to a file of its own, named by the run",
      len(lines) > 4 and lines[0].split("\t")[1] == "start"
      and sum(line.split("\t")[1] == "moved" for line in lines) == 2
      and any(line.split("\t")[1] == "returned" for line in lines))
entry = rotator_meta.read_meta(rotator_config.HISTORY_PATH.parent / "run_meta.json").get(run_id)
check("and its side-file entry names that file", entry is not None
      and entry.log == f"rotator/run-{run_id}.log" and entry.result == "clean")
kinds = [e.kind for e in wired.journal.recent(5)]
check("the journal has it started and finished, under that id",
      kinds[:2] == ["run.clean", "run.started"]
      and all(e.run == run_id for e in wired.journal.recent(2)))
check("every folder is still somewhere",
      sorted([p.name for p in RESERVE.iterdir()] + [p.name for p in MYPROJECTS.iterdir()])
      == sorted(before))
check("and the page shows it done, cleanly", page.done_view is not None
      and page.texts()["result"]["title"] == "Run 1 finished cleanly")

page.deleteLater()
app.processEvents()
os.chdir(HOME)
shutil.rmtree(TMP, ignore_errors=True)

print()
print("PASSED %d/%d" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
