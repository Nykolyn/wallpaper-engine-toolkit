"""One toolkit window, in a process of its own, raised from the tray.

    .venv\\Scripts\\python.exe tests\\test_window_instance.py

The window used to be built inside the tray tracker, and when it froze on 18
September ending it ended the count too. It is now a program of its own; what
has to keep working is the thing that in-process construction gave for free —
a click on the tray raises the one window instead of opening another.
"""
from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication, QLabel, QTabWidget  # noqa: E402

app = QApplication.instance() or QApplication([])

from app import window_instance as wi  # noqa: E402

results: list[bool] = []


def check(label: str, condition: bool) -> None:
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label)


def wait_for(condition, seconds=3.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.02)
    return False


# Names of this run's own, so a toolkit window open on this machine is left alone.
tag = uuid.uuid4().hex[:8]
MUTEX = f"Local\\WallpaperEngineToolkitWindowTest{tag}"
NAME = f"WallpaperEngineToolkit.window.test.{tag}"

check("nothing claims to be the window before anything has",
      not wi.already_running(MUTEX))
first = wi.WindowInstance.claim(MUTEX, NAME)
check("the first launch becomes the window", first is not None and first.listening)
check("and from then on a window is known to exist", wi.already_running(MUTEX))
check("a second launch is told there already is one",
      wi.WindowInstance.claim(MUTEX, NAME) is None)

asked: list[str] = []
first.show_requested.connect(asked.append)
check("asking the window to come forward reaches it",
      wi.ask_to_show("Tracker", wait=0, name=NAME))
check("with the tab it was asked for", wait_for(lambda: asked == ["Tracker"]))
wi.ask_to_show("", wait=0, name=NAME)
check("or with none, when it was launched without one",
      wait_for(lambda: asked == ["Tracker", ""]))

first.release()
check("once the window is gone, nothing answers",
      not wi.ask_to_show("Tracker", wait=0, name=NAME))
check("and nothing claims to be it", not wi.already_running(MUTEX))

# ---- How the window is started ------------------------------------------------

command = wi.window_command("Tracker")
check("from the source, the window is run_app.py",
      command[1].endswith("run_app.py") and command[-2:] == ["--tab", "Tracker"])
check("without a console flashing up when there is a windowed Python",
      Path(command[0]).name.lower() in ("pythonw.exe", "python.exe"))
check("and with no tab, no --tab", "--tab" not in wi.window_command(""))

launched: list[tuple] = []


class FakePopen:
    pid = 4242
    refuse_breakaway = False

    def __init__(self, command, cwd=None, creationflags=0, close_fds=True):
        if FakePopen.refuse_breakaway and creationflags & wi._CREATE_BREAKAWAY_FROM_JOB:
            raise PermissionError(5, "Access is denied")
        launched.append((command, creationflags, cwd))


real_popen = wi.subprocess.Popen
wi.subprocess.Popen = FakePopen
wi.launch("Tracker")
check("the window is started at normal priority, whatever the tray runs at",
      launched[-1][1] & wi._NORMAL_PRIORITY_CLASS)
check("and out of the tray's job, so ending the tray does not end it",
      launched[-1][1] & wi._CREATE_BREAKAWAY_FROM_JOB)
check("in the folder the program lives in", Path(launched[-1][2], "run_app.py").exists())
FakePopen.refuse_breakaway = True
wi.launch("Tracker")
check("where the job will not let it go, it is still started",
      len(launched) == 2 and not launched[-1][1] & wi._CREATE_BREAKAWAY_FROM_JOB
      and launched[-1][1] & wi._NORMAL_PRIORITY_CLASS)
wi.subprocess.Popen = real_popen

done = wi.make_normal_priority()
check("the window can put its own CPU priority back to normal", done["cpu"])
check("and its disk priority", done["io"])

# ---- The tray's side -----------------------------------------------------------

import app.tracker_tray as tray_mod  # noqa: E402


class Icon:
    def __init__(self):
        self.messages = []

    def showMessage(self, title, text, *rest):       # noqa: N802 - Qt's name
        self.messages.append(text)


class TrayStandIn:
    def __init__(self):
        self.icon = Icon()


def open_with(answers: bool, running: bool) -> tuple[TrayStandIn, list]:
    started: list[str] = []
    real = (wi.ask_to_show, wi.already_running, wi.launch)
    wi.ask_to_show = lambda tab, wait=0: answers
    wi.already_running = lambda: running
    wi.launch = lambda tab: started.append(tab) or FakePopen
    try:
        tray = TrayStandIn()
        tray_mod.TrackerTray.open_toolkit(tray)
    finally:
        wi.ask_to_show, wi.already_running, wi.launch = real
    return tray, started


tray, started = open_with(answers=True, running=True)
check("a click with the window open raises it and starts nothing",
      started == [] and tray.icon.messages == [])
tray, started = open_with(answers=False, running=False)
check("a click with no window open starts one, on the Tracker tab",
      started == ["Tracker"])
tray, started = open_with(answers=False, running=True)
check("a window that exists but does not answer is not stacked with another",
      started == [] and len(tray.icon.messages) == 1)

# ---- The window's side ----------------------------------------------------------

from app.main_window import MainWindow  # noqa: E402


class WindowStandIn:
    def __init__(self):
        self.tabs = QTabWidget()
        for name in ("Copier", "Tracker", "Review"):
            self.tabs.addTab(QLabel(name), name)


window = WindowStandIn()
check("a tab is found by its title", MainWindow.show_tab(window, "review")
      and window.tabs.currentIndex() == 2)
check("and a title that is not there changes nothing",
      not MainWindow.show_tab(window, "Nowhere") and window.tabs.currentIndex() == 2)

print()
print("PASSED %d/%d" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
