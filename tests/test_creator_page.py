"""Creator selection, tag states, safe GUI construction and fixture states."""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["WALLPAPER_TOOLKIT_DATA"] = tempfile.mkdtemp(prefix="creator_page_")

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from app import theme
from app.engines.creator import VideoItem
from app.engines.rotator.config import Config
from app.pages.creator import CreatorPage, build_items, tag_cell, playlist_available
from app.settings import Settings
from app.ui.kit import ChipCell, TagsCell, ProgressCell, ConfirmDialog
from app import services

app = QApplication.instance() or QApplication([])
theme.apply(app)
results = []

def check(name, condition):
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + name)

def item(name, tags=None):
    row = VideoItem.__new__(VideoItem)
    row.__dict__.update(video_path=name, basename=name, filename=name, size=100,
                        _exists=True, reason=None, tags=tags, width=1920, height=1080,
                        duration=20, metadata_read=True)
    return row

rows = [item("a"), item("b", ["Own"]), item("c", [])]
selected = {"a", "b", "c"}
check("skip on builds only resolved tagged rows", len(build_items(rows, selected, ["Batch"], True)) == 2)
check("skip off includes explicitly untagged rows", len(build_items(rows, selected, ["Batch"], False)) == 3)
check("empty batch needs tags only for followers and explicit none", len(build_items(rows, selected, [], True)) == 1)
check("selection restricts the subset", [r.basename for r in build_items(rows, {"b"}, ["Batch"])] == ["b"])
check("followers use low-tone batch pills", tag_cell(rows[0], ["Batch"]) == TagsCell(("Batch",), "text.lo"))
check("own tags use body-tone pills", tag_cell(rows[1], ["Batch"]) == TagsCell(("Own",), "text.body"))
check("explicit none displays NeedsTags", tag_cell(rows[2], ["Batch"]) == ChipCell("NeedsTags"))
check("an empty batch displays NeedsTags for its followers", tag_cell(rows[0], []) == ChipCell("NeedsTags"))
check("playlist button requires the Rotator destination", playlist_available("X:/Studio/myprojects", "x:/Studio/myprojects"))
check("playlist button excludes other targets", not playlist_available("X:/Exports", "X:/Studio/myprojects"))
check("unset targets do not match", not playlist_available("", ""))

with patch("os.path.getsize", side_effect=AssertionError("GUI stat")), \
        patch("os.scandir", side_effect=AssertionError("GUI scan")), \
        patch("app.engines.creator.find_ffmpeg", side_effect=AssertionError("GUI ffmpeg")):
    page = CreatorPage(Settings({}), config=Config(destination="X:/Studio/myprojects"))
check("page construction never probes videos or lists drives", page.state == "empty")
for state in page.FIXTURES:
    page.load_fixture(state)
    check(f"fixture {state} loads the real page", page.state == state)
page.load_fixture("scanned")
check("scanned build count is derived from the rows", page.build_button.text() == "Build 7 wallpapers")
page.skip.setChecked(False)
check("skip checkbox changes the build count", page.build_button.text() == "Build 10 wallpapers")
page.table.setCurrentIndex(page.source_model.index(0, 0))
QTest.keyClick(page.table, Qt.Key_Space)
check("Space changes the current row checkbox", len(page.selected) == 9 and page.build_button.text() == "Build 9 wallpapers")
QTest.keyClick(page.table, Qt.Key_A, Qt.ControlModifier)
check("Ctrl+A selects every visible usable row", len(page.selected) == 10)
page.load_fixture("done")
check("done shows rebuild for the rotation target", not page.rebuild_button.isHidden())
page.config.destination = "X:/Elsewhere"
page.load_fixture("done")
check("done hides rebuild for an unrelated target", page.rebuild_button.isHidden())
check("Done table includes created wallpaper ids", page.done_model.cell(page.done_model.items()[0], 0).sub == "amber-101")
check("busy progress is explicitly indeterminate", ProgressCell(None, busy=True).fraction is None)

# The real page runs a worker read, receives queued signals and builds only a subset.
source = Path(os.environ["WALLPAPER_TOOLKIT_DATA"]) / "source"
source.mkdir()
for name in ("a.mp4", "b.mp4", "bad.mp4", "notes.txt"):
    (source / name).write_bytes(b"video")
target = source.parent / "output"
svc = services.Services(data_dir=source.parent / "data")
live = CreatorPage(Settings({"creator": {"target": str(target), "mode": "Copy", "tags": ["Nature"]}}), svc)

def settle_until(predicate, timeout=5):
    until = time.monotonic() + timeout
    while not predicate() and time.monotonic() < until:
        app.processEvents()
        time.sleep(.01)
    app.processEvents()
    return predicate()

def probe(ffmpeg, path):
    if str(path).endswith("bad.mp4"):
        return dict(duration=None, width=None, height=None, video=False)
    return dict(duration=5, width=1920, height=1080, video=True)

def gif(ffmpeg, path, output, duration=None):
    Path(output).write_bytes(b"GIF89a")
    return True

with patch("app.engines.creator.find_ffmpeg", return_value="fake"), \
        patch("app.engines.creator.probe_metadata", side_effect=probe), \
        patch("app.engines.creator.make_gif", side_effect=gif):
    live.read_folder(str(source))
    check("worker read streams actual metadata to the page", settle_until(lambda: live.state == "scanned")
          and len(live.items) == 4 and live.items[0].resolution == "1920×1080")
    # b follows no tags explicitly; a is selected for creation. bad/notes remain reportable.
    live.items[1].tags = []
    live.selected = {row.video_path for row in live.items if row.valid}
    with patch.object(ConfirmDialog, "ask", return_value=True):
        live.start_build()
    check("page completes a subset through queued engine signals", settle_until(lambda: live.state == "done"))
    check("page totals match actual created and skipped work", live._total == 1 and len(list(target.iterdir())) == 1
          and sum(e["status"] == "ok" for e in live.report) == 1)
    check("Done preserves no-tag, damaged and unsupported reasons", len(live.report) == 4
          and any(e["reason"] == "skipped — no tags" for e in live.report)
          and any(e["reason"] == "could not be read — file may be damaged" for e in live.report))
    check("build appears in JobCenter and journal", svc.jobs.last_finished("creator").result == "problems"
          and svc.journal.recent(1)[0].kind == "build.problems")
    check("off-page completion raises a notification", bool(live.messages))
    live.prepare_retry()
    check("retry contains only remaining usable source files", live.state == "scanned" and len(live.selected) == 1)
    live.tags.close_popup()
    live.clear()
    check("clear prevents the old folder being read on next visit", live.settings.get("creator", "source", None) == "")

live.deleteLater()

page.deleteLater()
app.processEvents()
print(f"PASSED {sum(results)}/{len(results)}")
sys.exit(0 if all(results) else 1)
