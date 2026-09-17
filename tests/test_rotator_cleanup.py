"""The reserve check: what counts as an unusable folder, and what is offered.

Run it directly — there is no test framework in this project:

    .venv\\Scripts\\python.exe tests\\test_rotator_cleanup.py

Everything works on a temporary reserve built here, so nothing on the machine is
read or deleted. The last section builds the real confirmation dialog (it needs
Qt, but never shows a window) to check what it ticks by default — that default
is the whole safety story of the feature, so it is worth a test rather than a
glance.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.engines.rotator import core                  # noqa: E402

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
shutil.rmtree(TMP, ignore_errors=True)

print()
print("PASSED %d/%d" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
