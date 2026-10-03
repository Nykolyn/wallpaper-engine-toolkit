"""The frame: pages, the sidebar, the status line, the Settings page, the title bar.

Run it directly (needs Qt, but no windows on screen):

    .venv\\Scripts\\python.exe tests\\test_shell.py

The window is built the way tools/ui_snapshot.py builds it: stand-ins for the
old tabs (building them reads the library), a feed that reads nothing, and
services that are never started. Everything is written under a temporary
folder.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

TMP = Path(tempfile.mkdtemp(prefix="wallpaper_shell_test_"))
# Before any app module: settings and the Rotator's files resolve the data
# folder when they are imported, and it must not be this checkout's data\.
os.environ["WALLPAPER_TOOLKIT_DATA"] = str(TMP / "data")
(TMP / "data").mkdir(parents=True, exist_ok=True)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# The labels carry ≈ and ·, which a Windows console's code page (cp1252 on CI)
# cannot always print; a check must not fail for the way its name is shown.
sys.stdout.reconfigure(errors="replace")

from PySide6.QtCore import QObject, QPoint, Qt, Signal                  # noqa: E402
from PySide6.QtGui import QImage                                        # noqa: E402
from PySide6.QtTest import QTest                                        # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

app = QApplication(sys.argv)

from app import animations, services, theme, window_frame as wf, window_instance as wi  # noqa: E402
from app.engines.rotator import config as rc                                          # noqa: E402
from app.engines.rotator.config import Config                                         # noqa: E402
from app.main_window import (                                                         # noqa: E402
    DEFAULT_PAGE, PAGE_ORDER, MainWindow, next_in_loop, page_for,
)
from app.pages.base import Page                                           # noqa: E402
from app.pages.copier import CopierPage                                                  # noqa: E402
from app.pages.rotator import nav_state as rotator_nav                             # noqa: E402
from app.pages.overview import OverviewPage                                           # noqa: E402
from app.pages.settings import SettingsPage                                           # noqa: E402
from app.services.jobs import JobCenter                                               # noqa: E402
from app.services import snapshot as snapshot_module                                  # noqa: E402
from app.services.snapshot import (                                                   # noqa: E402
    LAST_RUN, PLAYLIST, PlaylistProgress, Reading, RunSummary,
)
from app import settings as settings_module                                           # noqa: E402
from app.settings import Settings                                                     # noqa: E402
from app.ui.kit import NavState, PathField, format as fmt                             # noqa: E402

theme.apply(app)
animations.ENABLED = True
# A finished job asks the Snapshot to count the Rotator's folders again; with no
# config that would be this machine's own myprojects. Here its worker reads nothing.
snapshot_module.compute = lambda keys, data_dir: {}

results: list[bool] = []


def check(label: str, condition: bool) -> None:
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label)


def wait(ms: int) -> None:
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def wait_for(condition, ms: int = 3000) -> bool:
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return False


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def monitor(name: str) -> SimpleNamespace:
    """What the tracker says of a monitor, as much of it as the pages read."""
    return SimpleNamespace(monitor=name, seen=4, total=201, current=None, current_title="",
                           current_since=None, live=True, from_engine=True, anchor="",
                           from_rotation=False)


# ---- a window with nothing of this machine's in it ------------------------------------

class Feed(QObject):
    """A TrackerFeed that reads nothing and remembers what it was asked."""
    updated = Signal()
    config_changed = Signal()

    def __init__(self):
        super().__init__()
        self.results = []
        self.config_path = ""
        self.error = None
        self.asked = []

    def refresh(self):
        self.asked.append(("refresh",))

    def use_config(self, path):
        self.asked.append(("use_config", path))

    def set_heartbeat(self, seconds):
        self.asked.append(("heartbeat", seconds))


class StandIn(Page):
    """Where an old tab goes; counts its comings and goings."""

    def __init__(self, key, title, icon):
        self.key, self.title, self.icon = key, title, icon
        super().__init__()
        self.shown = self.hidden = 0

    def on_shown(self):
        self.shown += 1

    def on_hidden(self):
        self.hidden += 1


settings = Settings({})
config = Config(source="", destination="", duplicates="", count=1000)
feed = Feed()
svc = services.Services(data_dir=TMP / "data", settings=settings)


def make_window(initial=None) -> MainWindow:
    stand_ins = [StandIn(key, key.title(), key) for key in
                 ("rotator", "tracker", "review", "creator", "copier")]
    pages = [OverviewPage(svc, feed), *stand_ins,
             SettingsPage(settings, config, feed=feed, services=svc)]
    return MainWindow(settings=settings, feed=feed, services_=svc, pages=pages,
                      start=False, initial=initial)


w = make_window()
w.move(300, 200)         # off the offscreen pointer at (0, 0)

print("-- pages --")
check("the pages are in the order of the loop, Settings last",
      w.sidebar.keys() == list(PAGE_ORDER) and PAGE_ORDER[-1] == "settings")
check("the window opens on Overview", w.current_page() == DEFAULT_PAGE == "overview"
      and w.header.title() == "Overview" and w.sidebar.current() == "overview")
check("at 1280 × 860, no smaller than 1040 × 720",
      (w.width(), w.height()) == (1280, 860)
      and (w.minimumWidth(), w.minimumHeight()) == (1040, 720))
w.sidebar.item("tracker").click()
check("a click in the sidebar goes to its page",
      w.current_page() == "tracker" and w.header.title() == "Tracker"
      and w.sidebar.item("tracker").selected() and not w.sidebar.item("overview").selected())
check("the page that left heard it, and the one that came", w.pages["tracker"].shown == 1)
check("an old tab's name goes to its page, whatever its case",
      w.show_page("COPIER") and w.current_page() == "copier"
      and w.pages["tracker"].hidden == 1)
check("a name that is no page changes nothing",
      not w.show_page("nowhere") and w.current_page() == "copier")
w.pages["copier"].navigate.emit("settings")
check("a page may ask for another", w.current_page() == "settings")

w.show()
w.activateWindow()
wait_for(lambda: QApplication.activeWindow() is w, 1000)
for key, number in (("rotator", Qt.Key_2), ("settings", Qt.Key_7), ("overview", Qt.Key_1),
                    ("review", Qt.Key_4)):
    QTest.keyClick(w, number, Qt.ControlModifier)
    app.processEvents()
check("Ctrl+2, Ctrl+7, Ctrl+1 and Ctrl+4 go to Rotator, Settings, Overview, Review",
      w.current_page() == "review")
frame = (w.sidebar.geometry(), w.status.geometry(), w.title_bar.geometry())
w.show_page("tracker")
check("the frame does not move when the page changes",
      (w.sidebar.geometry(), w.status.geometry(), w.title_bar.geometry()) == frame)

print("-- --tab --")
check("--tab takes the old tab names and the page keys, in any case",
      [page_for(n) for n in ("Rotator", "tracker", "Review", "CREATOR", "copier",
                             "overview", "Settings")]
      == ["rotator", "tracker", "review", "creator", "copier", "overview", "settings"])
check("and nothing else", [page_for(n) for n in ("", None, "junk", "tab")] == [None] * 4)
check("a window started with --tab Review opens on Review",
      make_window("Review").current_page() == "review")
check("and with a name that is no page, on Overview",
      make_window("junk").current_page() == "overview")

print("-- window commands --")
check("the plain request still reads, with its tab", wi.parse_command("show Tracker")
      == ("show", "Tracker") and wi.parse_command("show") == ("show", "")
      and wi.parse_command("show \n") == ("show", ""))
check("the command form reads: show:<page>, and verbs to come",
      wi.parse_command("show:rotator") == ("show", "rotator")
      and wi.parse_command("rotate:confirm") == ("rotate", "confirm")
      and wi.parse_command("show:") == ("show", ""))
check("anything else is no request", [wi.parse_command(line) for line in (
    "", "hello", "Show:x", ":x", "a b:c", "SHOW Tracker")] == [None] * 6)
check("a command is written in its form", wi.command("show", "rotator") == "show:rotator")
try:
    wi.command("Show Me", "x")
    check("a verb that is no verb is refused", False)
except ValueError:
    check("a verb that is no verb is refused", True)
w.handle_command("show", "copier")
check("show:copier brings the window to Copier", w.current_page() == "copier" and w.isVisible())
w.handle_command("frobnicate", "now")
check("a verb this version does not know only brings the window forward",
      w.current_page() == "copier" and w.isVisible())
started: list[bool] = []
w.pages["rotator"].start_rotation = lambda: started.append(True)
w.handle_command("rotate", "other")
check("rotate with an argument it does not know starts nothing",
      started == [] and w.current_page() == "copier")
w.handle_command("rotate", "confirm")
check("rotate:confirm shows the Rotator and asks its start question, once",
      w.current_page() == "rotator" and started == [True])
del w.pages["rotator"].start_rotation
w.show_page("copier")

print("-- the rail --")
w.resize(1199, 800)
app.processEvents()
check("below 1200 px the sidebar is a 56 px rail",
      w.sidebar.rail() and w.sidebar.width() == theme.RAIL_WIDTH
      and not w.sidebar.next_in_loop.isVisible())
check("with each page's name in its tool tip",
      w.sidebar.item("rotator").toolTip().startswith("Rotator"))
w.resize(1200, 800)
app.processEvents()
check("from 1200 px it opens out again",
      not w.sidebar.rail() and w.sidebar.width() == theme.SIDEBAR_WIDTH
      and w.sidebar.item("rotator").toolTip() == "")
w.resize(1280, 860)
app.processEvents()

print("-- the status line and the JobCenter --")
jobs = svc.jobs
check("with nothing run yet it says so", w.status.state() == "idle"
      and w.status.text() == "Nothing running")
w.show_page("settings")
run = jobs.start("rotator", "Run 3")
run.update("step 2 of 4 — moving 1 000 folders into myprojects", 412, 1000)
wait(150)
check("a running job: its tool and what it is doing",
      w.status.state() == "running"
      and w.status.text() == "Rotator · step 2 of 4 — moving 1 000 folders into myprojects")
check("its count and percentage, and Show",
      w.status.count_text() == f"412 / 1{fmt.NBSP}000 · 41%" and w.status.link_text() == "Show")
w.status._link.click()
check("Show goes to the job's page", w.current_page() == "rotator")
copy = jobs.start("copier", "Copying 4 folders")
copy.update("2 of 4 jobs · 148 MB/s", 35, 97, "3.5 GB / 9.7 GB")
wait(150)
check("with two running, the one that matters leads, and the other is counted",
      w.status.text().startswith("Rotator ·") and w.status.text().endswith("· 1 more running"))
copy.finish("clean", "4 of 4 copied")
w.show_page("settings")
run.finish("problems", "2 folders could not be moved")
wait(50)
check("a job that ended with problems is said in warn, with the way to them",
      w.status.state() == "warn"
      and w.status.text() == "Run 3 finished with problems · 2 folders could not be moved"
      and w.status.link_text() == "See problems")
w.status._link.click()
check("See problems goes to its page, and once seen the line is quiet again",
      w.current_page() == "rotator" and w.status.state() == "idle"
      and w.status.text().startswith("Nothing running · last run finished with problems"))
failed = jobs.start("copier", "Copying 2 folders")
failed.fail("access denied")
wait(50)
check("a job that failed is said in danger, with Show",
      w.status.state() == "error" and w.status.text() == "Copying 2 folders failed · access denied"
      and w.status.link_text() == "Show")
w.show_page("copier")
check("until its page has been looked at", w.status.state() == "idle"
      and w.status.text().startswith("Nothing running · Copier failed"))
stopped = jobs.start("rotator", "Run 4")
stopped.finish("stopped", "stopped after the return")
wait(50)
when = fmt.date_activity(stopped.ended)
check("a stopped run is quiet: when it was stopped",
      w.status.text() == f"Nothing running · last run was stopped {when}")
w.show_page("overview")
clean = jobs.start("rotator", "Run 5")
clean.finish("clean", "Moved 1000, returned 1000")
wait(50)
check("a clean finish is said in ok, as frame 05 does, with no link",
      w.status.state() == "ok" and w.status.link_text() == ""
      and w.status.text() == "Run 5 finished cleanly · Moved 1000, returned 1000")
w.show_page("tracker")
check("another page looked at leaves it be", w.status.state() == "ok")
w.show_page("rotator")
check("its own page looked at, the line is quiet again",
      w.status.state() == "idle" and w.status.text().startswith("Nothing running · last run finished"))

print("-- the cross-fade --")
w.show_page("overview")
wait(300)
w.show_page("tracker")
check("with motion on, a page change fades the content", w.fade.isVisible() and w.fade.running()
      and w.fade.geometry().topLeft() == w.content.mapTo(w.backdrop, QPoint(0, 0)))
check("and only the content: not the sidebar, not the status line",
      w.fade.geometry().left() >= w.sidebar.geometry().right()
      and w.fade.geometry().bottom() < w.status.geometry().top())
wait(animations.BASE + 200)
check("which is gone when it has faded", not w.fade.isVisible())
animations.ENABLED = False
try:
    w.show_page("review")
    check("with motion off (ENABLED False) the change is instant",
          not w.fade.isVisible() and not w.fade.running() and w.header.title() == "Review"
          and w.stack.currentWidget() is w.pages["review"])
finally:
    animations.ENABLED = True

print("-- Settings writes through Config and Settings --")
page: SettingsPage = w.pages["settings"]
reserve = TMP / "reserve"
reserve.mkdir()
changed = []
page.changed.connect(changed.append)
page.reserve._chosen(str(reserve))
check("the reserve is the Rotator's, in data/config.json",
      read_json(rc.CONFIG_PATH)["source"] == str(reserve) and config.source == str(reserve)
      and changed[-1] == "rotator")
check("and the field checks it is there, on a worker",
      wait_for(lambda: page.reserve.valid() is True))
page.myprojects._chosen(str(TMP / "myprojects"))
page.duplicates._chosen(str(TMP / "dupes"))
page.batch.setValue(250)
saved = read_json(rc.CONFIG_PATH)
check("myprojects, the duplicates folder and the folders per run too",
      (saved["destination"], saved["duplicates"], saved["count"])
      == (str(TMP / "myprojects"), str(TMP / "dupes"), 250))
check("a folder that is not there says so", wait_for(lambda: page.myprojects.valid() is False))
page.copier_dest._chosen(str(TMP / "copies"))
page.creator_source._chosen(str(TMP / "clips"))
page.creator_target._chosen(str(TMP / "built"))
suite = read_json(settings_module.SETTINGS_PATH)
check("the Copier's and the Creator's folders are in data/suite.json",
      suite["copier"]["dest"] == str(TMP / "copies")
      and (suite["creator"]["source"], suite["creator"]["target"])
      == (str(TMP / "clips"), str(TMP / "built")))
check("and never in the Rotator's file", "copier" not in read_json(rc.CONFIG_PATH))
settings.section("tracker")["interval"] = 30
page.heartbeat.setValue(7)
suite = read_json(settings_module.SETTINGS_PATH)
check("\u201cAlso check every\u201d is saved in seconds, and the old interval dropped",
      suite["tracker"]["heartbeat"] == 420 and "interval" not in suite["tracker"]
      and ("heartbeat", 420) in feed.asked)
engine = TMP / "config.json"
engine.write_text("{}", encoding="utf-8")
page.we_config._chosen(str(engine))
check("Wallpaper Engine's config.json is saved, and the tracker told",
      read_json(settings_module.SETTINGS_PATH)["tracker"]["we_config"] == str(engine)
      and ("use_config", str(engine)) in feed.asked)
check("and its field holds a file, not a folder",
      page.we_config.kind() == "file" and wait_for(lambda: page.we_config.valid() is True))
feed.results = [monitor("Monitor1"), monitor("Monitor2")]
feed.updated.emit()
check("the lead monitor offers Automatic and each monitor the tracker sees",
      [page.lead.itemText(i) for i in range(page.lead.count())]
      == ["Automatic", "Monitor1", "Monitor2"])
page.lead.choose(2)
check("a monitor chosen is saved as the lead",
      read_json(settings_module.SETTINGS_PATH)["tracker"]["primary"] == "Monitor2")
page.lead.choose(0)
check("and Automatic forgets it", "primary" not in read_json(settings_module.SETTINGS_PATH)["tracker"])

busy = jobs.start("rotator", "Run 5")
wait(50)
check("while the Rotator works its folders are read-only, and it says why",
      not page.reserve.isEnabled() and not page.batch.isEnabled()
      and page.rotator_note.isVisibleTo(page) and page.copier_dest.isEnabled())
page._set_rotator("source", "Z:\\elsewhere")
check("and nothing reaches the Config meanwhile", config.source == str(reserve))
busy.finish("clean", "done")
wait(50)
check("once it is done they can be changed again",
      page.reserve.isEnabled() and not page.rotator_note.isVisibleTo(page))

print("-- the window and the tray share suite.json --")
other = Settings.load()                 # the tray's copy
other.set("tracker", "primary", "Monitor1")
time.sleep(0.02)
other.save()
check("a change the other process saved is read again",
      settings.reload_if_changed() and settings.get("tracker", "primary", None) == "Monitor1")
check("and only once", not settings.reload_if_changed())

print("-- the sidebar's states --")
p = NavState.progress(412, 1000)
check("progress: a bar and a percentage, under the name",
      (p.kind, p.text, p.two_lines, p.dot()) == ("progress", "41%", True, "accent"))
c = NavState.count(4, 201)
check("count: 4/201 and a mini bar", (c.text, c.two_lines, round(c.fraction, 3))
      == ("4/201", True, 0.02))
check("and both turn ok once all are done",
      c.tone == "lo" and NavState.count(201, 201).tone == "ok" and NavState.count(0, 0).tone == "lo")
check("badge: a warn badge, and a warn dot in the rail",
      (NavState.badge(12).text, NavState.badge(12).dot()) == ("12", "warn"))
check("status: a quiet word at the right, or a toned one under the name",
      not NavState.status("idle").two_lines and NavState.status("2 problems", "warn", below=True)
      .two_lines and NavState.status("idle").dot() is None)
for bad in (lambda: NavState("spinner"), lambda: NavState.status("x", "purple")):
    try:
        bad()
        check("a state or tone the design has not is refused", False)
    except ValueError:
        check("a state or tone the design has not is refused", True)
w.sidebar.set_state("review", NavState.badge(3))
check("the sidebar shows what it is given", w.sidebar.item("review").meta_text() == "3")

playlist = PlaylistProgress("Monitor1", 4, 201, 197, 2, "2026-09-19 13:41", "21 Sep 09:10", True)
now = datetime(2026, 9, 19, 13, 44)
check("Next in the loop: how many are left, and when to rotate, estimated",
      next_in_loop(playlist, now) == f"197 left on Monitor1 — rotate again ≈21{fmt.NBSP}Sep")
check("without an estimate, only what is known",
      next_in_loop(PlaylistProgress("Monitor1", 3, 201, 198, 1, "", None, True), now)
      == "198 left on Monitor1")
check("a finished playlist says it is time",
      next_in_loop(PlaylistProgress("Monitor1", 201, 201, 0, 100, "", None, True), now)
      == "Monitor1's playlist is done — time to rotate")
check("and before any count, it says so", next_in_loop(None, now) == "No playlist counted yet")
check("an estimate past New Year lands in the next year",
      next_in_loop(PlaylistProgress("M", 1, 9, 8, 11, "", "02 Jan 10:00", True),
                   datetime(2026, 12, 20)).endswith(f"≈2{fmt.NBSP}Jan{fmt.NBSP}2027"))


class Snap(dict):
    def __getitem__(self, key):
        return self.get(key, Reading())


center = JobCenter()
snap = Snap()
check("a Rotator not read yet says nothing", rotator_nav(center, snap) == NavState())
snap[LAST_RUN] = Reading(None, 1.0)
check("with no run yet: ready for run 1", rotator_nav(center, snap).text == "ready · run 1")
summary = RunSummary(38, "x", None, "clean", 1000, 998, 0, 0)
snap[LAST_RUN] = Reading(summary, 1.0)
check("after a run: ready for the next", rotator_nav(center, snap).text == "ready · run 39")
done = center.start("rotator", "Run 38")
done.finish("clean", "ok")
check("after a clean run this session: clean", rotator_nav(center, snap)
      == NavState.status("clean · run 38", "ok", below=True))
snap[LAST_RUN] = Reading(RunSummary(38, "x", None, "problems", 998, 998, 0, 2), 1.0)
check("after problems: how many, in warn", rotator_nav(center, snap)
      == NavState.status("2 problems", "warn", below=True))
going = center.start("rotator", "Run 39")
going.update("moving", 412, 1000)
check("while it runs: its bar", rotator_nav(center, snap) == NavState.progress(412, 1000))
copier_page = CopierPage(Settings({}))
check("Copier owns its idle nav", copier_page.nav_state().text == "idle")
copier_page.deleteLater()

print("-- the title bar and Windows --")
check("the offscreen window has no native frame to lean on",
      wf.NativeFrame.attach(w, w.title_bar, theme.RESIZE_BORDER) is None)
edges = [wf.edge_hit(x, y, 100, 80, 6) for x, y in (
    (1, 1), (50, 1), (98, 1), (1, 40), (98, 40), (1, 78), (50, 78), (98, 78), (50, 40))]
check("the edges and corners resize the window",
      edges == [wf.HTTOPLEFT, wf.HTTOP, wf.HTTOPRIGHT, wf.HTLEFT, wf.HTRIGHT,
                wf.HTBOTTOMLEFT, wf.HTBOTTOM, wf.HTBOTTOMRIGHT, None])
bar = w.title_bar


def point(widget, x, y):
    at = widget.mapTo(w, QPoint(x, y))
    return at.x(), at.y()


check("the title bar is the caption: it drags, snaps and double-clicks",
      wf.hit_test(w, bar, *point(bar, bar.width() // 2, 16), 6) == wf.HTCAPTION)
check("the maximise button is Windows' (its snap layouts)",
      wf.hit_test(w, bar, *point(bar.maximise_button, 20, 16), 6) == wf.HTMAXBUTTON)
check("minimise and close are Qt's own buttons",
      wf.hit_test(w, bar, *point(bar.close_button, 20, 16), 6) == wf.HTCLIENT
      and wf.hit_test(w, bar, *point(bar.minimise_button, 20, 16), 6) == wf.HTCLIENT)
check("the page is the page", wf.hit_test(w, bar, 600, 500, 6) == wf.HTCLIENT)
check("its left edge resizes", wf.hit_test(w, bar, 2, 400, 6) == wf.HTLEFT)
w.isMaximized = lambda: True
try:
    check("maximised, no edge resizes", wf.hit_test(w, bar, 2, 400, 6) == wf.HTCLIENT)
finally:
    del w.isMaximized
bar.maximise_button.set_native(hover=True)
check("Windows' hover over maximise reaches the button",
      bar.maximise_button.visual_state() == "hover")
bar.maximise_button.set_native(hover=False)
bar.maximise_button.set_maximised(True)
check("maximised, the button offers to restore", bar.maximise_button.toolTip() == "Restore")
bar.maximise_button.set_maximised(False)

print("-- a file in a PathField --")
field = PathField(str(engine), kind="file")
check("a file that is there is found", wait_for(lambda: field.valid() is True))
field.set_path(str(TMP))
check("a folder is not the file asked for", wait_for(lambda: field.valid() is False))
try:
    PathField(kind="drive")
    check("a kind that is neither is refused", False)
except ValueError:
    check("a kind that is neither is refused", True)

print("-- snapshots --")
s = make_window("rotator")
s.load_fixture("running")
wait(150)
check("a fixture puts its jobs in the JobCenter: the status line runs",
      s.status.state() == "running" and "step 3 of 4" in s.status.text())
check("and pins the sidebar", s.sidebar.state("rotator") == NavState.progress(412, 1000)
      and s.sidebar.state("review") == NavState.badge(12))
over = make_window("overview")
over.load_fixture("idle")
check("a page with a fixture of its own shows it", over.header.subtitle()
      == "Saturday 19 September, 13:44")
try:
    s.load_fixture("nonsense")
    check("a state with no fixture is refused", False)
except KeyError:
    check("a state with no fixture is refused", True)

out = TMP / "snap.png"
done = subprocess.run([sys.executable, str(ROOT / "tools" / "ui_snapshot.py"), "--page",
                       "settings", "--state", "running", "--size", "1040x720", "--scale", "1.5",
                       "--out", str(out)], capture_output=True, text=True, timeout=180)
image = QImage(str(out))
check("tools/ui_snapshot.py saves the window at the size and scale asked",
      done.returncode == 0 and (image.width(), image.height()) == (1560, 1080))

print()
print("PASSED %d/%d" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
