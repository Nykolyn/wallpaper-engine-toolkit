"""Copier page state, worker measurements, editing and service integration."""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
scratch = tempfile.TemporaryDirectory(prefix="copier-page-")
os.environ["WALLPAPER_TOOLKIT_DATA"] = scratch.name

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from app import services, theme
from app.engines import copier as engine
from app.engines.rotator.config import Config
from app.pages.copier import CopierPage, totals
from app.settings import Settings
from app.ui.kit import ConfirmDialog, SpinBox, SpinCell

app = QApplication.instance() or QApplication([])
theme.apply(app)
results = []


def check(name, value):
    results.append(bool(value))
    print(("PASS " if value else "FAIL ") + name)


def settle(predicate, seconds=5):
    deadline = time.monotonic() + seconds
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    app.processEvents()
    return predicate()


# Catch filesystem work during construction and queue mutation on the GUI thread.
violations = []
real_scan, real_stat = os.scandir, os.stat
def guard(real):
    def guarded(*args, **kwargs):
        if threading.current_thread() is threading.main_thread():
            violations.append(str(args[0]))
            raise OSError("GUI disk access")
        return real(*args, **kwargs)
    return guarded

with patch("os.scandir", guard(real_scan)), patch("os.stat", guard(real_stat)):
    page = CopierPage(Settings({}), config=Config(source="", destination=""))
    page.add_jobs(["X:/Sources/First"], 3)
check("construction and queue additions never inspect the disk on the GUI thread", not violations)
page._scan_timer.stop()
for state in page.FIXTURES:
    page.load_fixture(state)
    check(f"fixture {state} uses the real page", page.state == ("queue" if state == "no-space" else state))
page.load_fixture("queue")
t = totals(page.rows.values())
check("totals include all requested copies and files", t["jobs"] == 4 and t["copies"] == 11 and t["files"] == 56)
page.resize(1000, 680)
page.show()
app.processEvents()
index = page.model.index(0, 1)
page.table.setCurrentIndex(index)
QTest.keyClick(page.table, Qt.Key_F2)
app.processEvents()
editor = page.table.findChild(SpinBox)
check("F2 opens a real kit SpinBox for copies", editor is not None)
if editor is not None:
    editor.setValue(5)
    QTest.keyClick(editor, Qt.Key_Return)
    app.processEvents()
check("editing copies changes the job and invalidates its size", page.jobs[0].count == 5 and page.rows[page.jobs[0].id]["size"] is None)
page.load_fixture("running")
check("copy counts cannot be edited during a run", not page.model.flags(page.model.index(0, 1)) & Qt.ItemIsEditable)
check("running contains individual progress and a failure", totals(page.rows.values())["failed"] == 1
      and not page.problem.isHidden() and not page.pause_button.isHidden())
page.load_fixture("done")
check("Done totals count only copied files", totals(page.rows.values())["done_files"] == 50)
check("failed job can be retried from Done", not page.retry_button.isHidden())
page.clear()
check("Clear finished clears terminal results without deleting outputs", page.state == "empty" and not page.rows)
page.hide()

source = Path(scratch.name) / "source"
source.mkdir()
(source / "one").write_bytes(b"abcdefghij")
(source / "two").write_bytes(b"12345")
destination = Path(scratch.name) / "output"
svc = services.Services(data_dir=Path(scratch.name) / "services")
live = CopierPage(Settings({"copier": {"dest": str(destination)}}), svc)
threads = []
original_inspect = engine.inspect_queue
def measured(*args, **kwargs):
    threads.append(threading.current_thread())
    return original_inspect(*args, **kwargs)

with patch("app.engines.copier.inspect_queue", measured):
    live.add_jobs([str(source)], 2)
    check("sizes and space arrive from a worker", settle(lambda: live._measured_revision == live._revision)
          and totals(live.rows.values())["size"] == 30 and bool(live.drives))
    check("every size/space read is off the GUI thread", threads and all(t is not threading.main_thread() for t in threads))
    live.verify.setChecked(True)
    with patch.object(ConfirmDialog, "ask", return_value=True):
        live.start_copying()
    check("page runs through to Done", settle(lambda: live.state == "done"))
    check("page totals agree with written files", totals(live.rows.values())["done_bytes"] == 30
          and totals(live.rows.values())["done_files"] == 4
          and len(list(destination.iterdir())) == 2)
    check("JobCenter uses byte totals and a real result", svc.jobs.last_finished("copier").result == "clean"
          and svc.jobs.last_finished("copier").total == 30)
    check("journal contains both per-job and run outcomes", svc.journal.recent(1)[0].kind == "copy.clean"
          and any(e.kind == "copy.job.done" for e in svc.journal.recent(10)))
    check("off-page completion notifies", bool(live.messages))
    live.clear()
    check("clear does not delete output folders", live.state == "empty" and len(list(destination.iterdir())) == 2)

# An error is retained; another job finishes; retry returns only failed work to the queue.
missing = Path(scratch.name) / "missing"
live.add_jobs([str(missing), str(source)], 1)
live._begin()
check("page isolates errors", settle(lambda: live.state == "done")
      and totals(live.rows.values())["failed"] == 1 and totals(live.rows.values())["done"] == 1)
missing.mkdir()
(missing / "fixed").write_text("now readable")
live.retry_failed()
check("retry keeps successful jobs done", live.state == "queue" and [j.state for j in live.jobs] == ["queued", "done"])
check("retry refreshes its free-space estimate", settle(lambda: live._measured_revision == live._revision))
with patch.object(ConfirmDialog, "ask", return_value=True):
    live.start_copying()
check("retried queue finishes cleanly", settle(lambda: live.state == "done") and totals(live.rows.values())["failed"] == 0)
check("retry did not repeat the successful job", len(list(destination.glob("source_copy*"))) == 3)

# A stale worker result must not restore an old count or destination.
live.clear()
entered, release = threading.Event(), threading.Event()
def slow(jobs, destination):
    entered.set()
    release.wait(3)
    return original_inspect(jobs, destination)
with patch("app.engines.copier.inspect_queue", slow):
    live.add_jobs([str(source)], 1)
    live._measure()
    check("background measurement starts", entered.wait(2))
    old_revision = live._revision
    live.set_copies(live.jobs[0].id, 4)
    release.set()
    check("stale metadata is superseded", settle(lambda: live._measured_revision == live._revision)
          and live._revision > old_revision and totals(live.rows.values())["size"] == 60)
live.clear()

# Clipboard retains spaces, counts, and deduplication; no disk inspection is needed.
live.show()
QApplication.clipboard().setText(f'"{source}" 2\n"{source}" 4')
live.paste()
check("paste keeps the first count and avoids duplicate sources", live.folders() == [(str(source), "2")])
check("queue editing has one visible Accent action", live.start_button.isVisible() and not live.retry_button.isVisible())
live.clear()
settle(lambda: live._measuring is None and not live._scan_timer.isActive())
page.deleteLater()
live.deleteLater()
app.processEvents()
print(f"PASSED {sum(results)}/{len(results)}")
sys.exit(0 if all(results) else 1)
