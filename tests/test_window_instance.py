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

from PySide6.QtWidgets import QApplication  # noqa: E402

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

commands: list[tuple[str, str]] = []
first.command_received.connect(lambda verb, argument: commands.append((verb, argument)))
check("a request in the command form reaches it too",
      wi.send(wi.command("show", "rotator"), wait=0, name=NAME)
      and wait_for(lambda: commands == [("show", "rotator")]))
check("and a show in that form is a show", wait_for(lambda: asked[-1:] == ["rotator"]))
wi.send("rotate:confirm", wait=0, name=NAME)
check("a verb the window may not know is still handed on",
      wait_for(lambda: commands[-1:] == [("rotate", "confirm")]) and asked[-1:] == ["rotator"])
wi.send("hello there", wait=0, name=NAME)
wi.ask_to_show("Review", wait=0, name=NAME)
check("a line in neither form is dropped, and the next one still read",
      wait_for(lambda: asked[-1:] == ["Review"]) and commands[-1] == ("show", "Review"))

del asked[:], commands[:]
check("a command can follow the request to show, in the same write",
      wi.ask_to_show("Rotator", wait=0, name=NAME, command=wi.command("rotate", "confirm"))
      and wait_for(lambda: commands == [("show", "Rotator"), ("rotate", "confirm")]))
check("the window is raised on the page first, then asked", asked == ["Rotator"])
try:
    wi.ask_to_show("Rotator", wait=0, name=NAME, command="Rotate Now")
    check("a command that is no command is refused, not sent", False)
except ValueError:
    check("a command that is no command is refused, not sent", True)

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
check("with a command, the window is told to carry it out once it is up",
      wi.window_command("Rotator", "rotate:confirm")[-4:]
      == ["--tab", "Rotator", "--command", "rotate:confirm"])
check("and with none, no --command", "--command" not in wi.window_command("Tracker"))

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
FakePopen.refuse_breakaway = False
wi.launch("Rotator", "rotate:confirm")
check("a started window is handed the command on its command line",
      launched[-1][0][-2:] == ["--command", "rotate:confirm"])
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
        self.opened: list[tuple] = []
        self.quit: list[bool] = []
        self.app = self
        self._balloon = None

    def open_toolkit(self, page="Tracker", command=None):
        self.opened.append((page, command))


def open_with(answers: bool, running: bool, page="Tracker", command=None) -> tuple[TrayStandIn, list]:
    started: list[tuple] = []
    asked_for: list[tuple] = []
    real = (wi.ask_to_show, wi.already_running, wi.launch)
    wi.ask_to_show = lambda tab, wait=0, command=None: asked_for.append((tab, command)) or answers
    wi.already_running = lambda: running
    wi.launch = lambda tab, command=None: started.append((tab, command)) or FakePopen
    try:
        tray = TrayStandIn()
        tray_mod.TrackerTray.open_toolkit(tray, page, command)
    finally:
        wi.ask_to_show, wi.already_running, wi.launch = real
    tray.asked = asked_for
    return tray, started


tray, started = open_with(answers=True, running=True)
check("a click with the window open raises it and starts nothing",
      started == [] and tray.icon.messages == [])
tray, started = open_with(answers=False, running=False)
check("a click with no window open starts one, on the Tracker tab",
      started == [("Tracker", None)])
tray, started = open_with(answers=False, running=True)
check("a window that exists but does not answer is not stacked with another",
      started == [] and len(tray.icon.messages) == 1)
tray, started = open_with(answers=True, running=True, page="Rotator", command="rotate:confirm")
check("Rotate now… asks an open window for the Rotator and its start question",
      tray.asked == [("Rotator", "rotate:confirm")] and started == [])
tray, started = open_with(answers=False, running=False, page="Rotator", command="rotate:confirm")
check("and a closed one is started with that command, not left to a second click",
      started == [("Rotator", "rotate:confirm")])

# What each menu row opens, and what a click on a balloon does.
from app import tray_menu as menu_mod  # noqa: E402

for key, want in ((menu_mod.OPEN, ("Tracker", None)), (menu_mod.REVIEW, ("Review", None)),
                  (menu_mod.SETTINGS, ("Settings", None)),
                  (menu_mod.ROTATE, ("Rotator", "rotate:confirm"))):
    tray = TrayStandIn()
    tray_mod.TrackerTray._on_chosen(tray, key)
    check(f"the {key} row opens {want[0]}" + (" with its command" if want[1] else ""),
          tray.opened == [want])
tray = TrayStandIn()
tray.quit = []
tray.app = type("App", (), {"quit": lambda self: tray.quit.append(True)})()
tray_mod.TrackerTray._on_chosen(tray, menu_mod.QUIT)
check("Quit quits, and opens nothing", tray.quit == [True] and tray.opened == [])

for kind, page in ((tray_mod.BALLOON_FINISHED, "Rotator"), (tray_mod.BALLOON_RESTARTED, "Tracker")):
    tray = TrayStandIn()
    tray._balloon = kind
    tray_mod.TrackerTray._on_message_clicked(tray)
    check(f"a click on the {kind} balloon opens the {page}, and only shows it",
          tray.opened == [(page, None)])
tray = TrayStandIn()
tray_mod.TrackerTray._on_message_clicked(tray)
check("a click on a balloon that was neither opens nothing", tray.opened == [])

# ---- The window's side ----------------------------------------------------------

from app.main_window import page_for  # noqa: E402

check("an old tab name finds its page, whatever its case",
      [page_for(n) for n in ("Tracker", "review", "COPIER")] == ["tracker", "review", "copier"])
check("and so does a page that was never a tab", page_for("settings") == "settings")
check("a name that is no page finds nothing, so nothing changes", page_for("Nowhere") is None)

print()
print("PASSED %d/%d" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
