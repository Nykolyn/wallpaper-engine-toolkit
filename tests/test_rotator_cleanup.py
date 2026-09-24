"""The reserve check: what counts as an unusable folder, and what is offered.

Run it directly — there is no test framework in this project:

    .venv\\Scripts\\python.exe tests\\test_rotator_cleanup.py

Everything works on a temporary reserve built here, so nothing on the machine is
read or deleted. Two sections build real widgets (they need Qt, but never show a
window): the confirmation dialog, to check what it ticks by default — that
default is the whole safety story of the feature, so it is worth a test rather
than a glance — and the Rotator tab with its folders unset, run from a stand-in
install folder, to check that nothing in it is listed or deleted.
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

from PySide6.QtWidgets import QApplication          # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)
from app import theme                               # noqa: E402
from app.ui.cleanup_dialog import CleanupDialog     # noqa: E402

theme.apply(app)

broken = [
    core.BrokenFolder("shader-cache", core.REASON_SHADERS, str(RESERVE), files=2, size=8),
    core.BrokenFolder("nothing-here", core.REASON_EMPTY, str(RESERVE)),
    core.BrokenFolder("lost-manifest", core.REASON_ORPHAN, str(RESERVE),
                      files=1, size=11, holds_media=True),
]
dialog = CleanupDialog(broken, scanned=7)
check("by default the dialog ticks only what is certainly rubbish",
      sorted(Path(p).name for p in dialog.paths()) == ["nothing-here", "shader-cache"])
check("and the running total is filled in from the start",
      dialog.counts.text().startswith("2 folder(s) ticked"))

dialog._set_all(True)
check("selecting all reaches the ones holding media too", len(dialog.paths()) == 3)
check("and the button says how many will go",
      dialog.delete_btn.text() == "Delete 3 folder(s)")

dialog._set_all(False)
check("with nothing ticked there is nothing to delete", dialog.paths() == [])
check("and the delete button cannot be pressed", not dialog.delete_btn.isEnabled())

dialog._select_safe()
check("the paths handed back are absolute, ready to delete",
      all(Path(p).is_absolute() for p in dialog.paths()))

dialog.deleteLater()

# ------------------------------------------------ the Rotator tab, unset
#
# The reported case end to end: no duplicates folder, and the working
# directory holding data\. The Duplicates tab listed data\ and _internal\ and
# "Delete all" would have deleted them. Every question is answered yes here, so
# only the guards stand between the click and the delete.

from PySide6.QtWidgets import QMessageBox            # noqa: E402
from app.ui import rotator_tab                       # noqa: E402

shown: list[tuple[str, str]] = []


def _box(kind: str):
    def show(_parent, _title, text, *_rest):
        shown.append((kind, text))
        return QMessageBox.Yes
    return staticmethod(show)


class Boxes:
    Yes = QMessageBox.Yes
    question = _box("question")
    information = _box("information")
    warning = _box("warning")
    critical = _box("critical")


rotator_tab.QMessageBox = Boxes
rotator_config.CONFIG_PATH.write_text(json.dumps(
    {"source": "", "destination": "", "duplicates": "", "count": 1,
     "refresh_playlist": False}), encoding="utf-8")

tab = rotator_tab.RotatorTab()
dup_buttons = (tab.del_sel_btn, tab.del_all_btn, tab.mv_sel_btn, tab.mv_all_btn)
check("with no duplicates folder the Duplicates tab lists nothing",
      tab.dup_panel.model.rowCount() == 0)
check("and says the folder is not set",
      tab.dup_panel.count_label.text() == "Duplicates (not set): 0")
check("and none of its actions can be pressed",
      not any(b.isEnabled() for b in dup_buttons))
check("the reserve and myprojects lists are empty and say why too",
      tab.reserve_panel.model.rowCount() == tab.transferred_panel.model.rowCount() == 0
      and "(not set)" in tab.reserve_panel.count_label.text()
      and "(not set)" in tab.transferred_panel.count_label.text())

for action in ("delete", "replace"):
    shown.clear()
    tab._dup_action(action, True)
    check(f"'{action} all' stops before asking, and says the folder is not set",
          tab.dup_worker is None and [k for k, _ in shown] == ["warning"]
          and "duplicates folder is not set" in shown[0][1] and untouched())

shown.clear()
tab.check_folders()
check("Check folders with nothing set says so and checks nothing",
      tab.scan_worker is None and [k for k, _ in shown] == ["critical"]
      and "reserve folder is not set" in shown[0][1])
shown.clear()
tab.start_rotation()
check("and a rotation will not start",
      tab.rotation_worker is None and [k for k, _ in shown] == ["critical"]
      and shown[0][1] == "The reserve folder is not set." and untouched())

tab.config.duplicates = str(DUPES)
tab.refresh_duplicates()
check("once the duplicates folder is set, what is in it is listed",
      tab.dup_panel.model.rowCount() == 1
      and tab.dup_panel.count_label.text() == "Duplicates: 1")
check("deleting is offered, moving back waits for the reserve",
      tab.del_all_btn.isEnabled() and not tab.mv_all_btn.isEnabled())
shown.clear()
tab._dup_action("delete", True)
tab.dup_worker.wait(10_000)
app.processEvents()
check("and Delete all deletes it, having named the folder in the question",
      not (DUPES / "dup-a").exists() and DUPES.exists()
      and shown[0][0] == "question" and str(DUPES) in shown[0][1] and untouched())
check("the buttons come back when it is done", tab.del_all_btn.isEnabled())

tab.deleteLater()
os.chdir(HOME)
shutil.rmtree(TMP, ignore_errors=True)

print()
print("PASSED %d/%d" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
