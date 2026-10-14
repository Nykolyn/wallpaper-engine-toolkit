"""Asking the running window and tray tracker to quit — the installer's first step.

Run it directly — there is no test framework in this project:

    .venv\\Scripts\\python.exe tests\\test_quit_request.py

The waiting is checked with stand-ins for the mutexes and the clock. The
tracker's socket is real, under a name of its own, so a toolkit running on this
machine is never asked anything.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# tracker_feed finds the data folder when it is imported; not the real one.
os.environ["WALLPAPER_TOOLKIT_DATA"] = tempfile.mkdtemp(prefix="wallpaper_quit_test_")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QCoreApplication                              # noqa: E402

from app import quit_request as qr, window_instance as wi                # noqa: E402
from app.tracker_feed import TRAY_MUTEX                                  # noqa: E402

results: list[bool] = []


def check(label: str, condition: bool) -> None:
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label)


app = QCoreApplication.instance() or QCoreApplication(sys.argv)

check("the tray's mutex is the one the tracker holds", qr.TRAY_MUTEX == TRAY_MUTEX)
check("the tracker's socket is named like the window's, per user",
      qr.tracker_server_name() == wi.server_name().replace(".window.", ".tracker."))
check("the request is a window command", wi.parse_command(qr.QUIT) == ("quit", ""))


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


# Nothing running: nothing is asked.
sent: list[tuple[str, str]] = []
gone, said = qr.ask_all(running=lambda: [], send=lambda m, wait, name: sent.append((m, name)))
check("with nothing running nothing is asked", gone and sent == [] and "nothing" in said)

# Both running; both go after a second.
clock = Clock()
sent.clear()
gone, said = qr.ask_all(
    running=lambda: [] if clock.now >= 1.0 else ["the window", "the tray tracker"],
    send=lambda m, wait, name: sent.append((m, name)) or True, sleep=clock.sleep, clock=clock)
check("each running part is asked on its own socket",
      sent == [(qr.QUIT, wi.server_name()), (qr.QUIT, qr.tracker_server_name())])
check("and once they are gone, that is said", gone and "nothing runs any more" in said)

# A busy window stays: the wait ends, and says what is still there.
clock = Clock()
sent.clear()
gone, said = qr.ask_all(
    wait=5, running=lambda: ["the window"], send=lambda m, wait, name: sent.append((m, name)) or True,
    sleep=clock.sleep, clock=clock)
check("what will not quit is waited for, then named",
      not gone and "the window" in said and 5.0 <= clock.now < 6.0 and 1 < len(sent) <= 6)

clock = Clock()
closed = []
sent.clear()

def idle_later(message, wait, name):
    sent.append(clock.now)
    if clock.now >= 0.5:
        closed.append(True)
    return True

gone, said = qr.ask_all(wait=3, running=lambda: [] if closed else ["the window"],
                        send=idle_later, sleep=clock.sleep, clock=clock)
check("a window that becomes idle after the first request is asked again and exits",
      gone and len(sent) == 2 and sent[1] >= 1.0)

# A process holds its mutex before Qt's socket is ready on a slow machine.
clock = Clock()
delivered = []
attempts = []


def starting_send(message, wait, name):
    attempts.append(clock.now)
    if clock.now < 3:
        return False
    delivered.append(name)
    return True


gone, said = qr.ask_all(
    wait=6, running=lambda: [] if delivered else ["the tray tracker"], send=starting_send,
    sleep=clock.sleep, clock=clock)
check("a slow-starting tracker is asked again when its socket becomes ready",
      gone and len(attempts) > 1 and delivered == [qr.tracker_server_name()])
clock = Clock()
attempts.clear()
gone, said = qr.ask_all(
    wait=1, running=lambda: ["the tray tracker"],
    send=lambda message, wait, name: attempts.append(clock.now) or False,
    sleep=clock.sleep, clock=clock)
check("a socket that never starts has a bounded wait and reports failure",
      not gone and 1 <= clock.now < 1.5 and len(attempts) > 1)

# The real socket also starts later than the old two-second delivery attempt.
import subprocess                                                        # noqa: E402

late_name = f"WallpaperEngineToolkit.tracker-late-test.{os.getpid()}"
late = subprocess.Popen([
    sys.executable, "-c",
    "import sys,time; sys.path.insert(0,sys.argv[1]);"
    "from PySide6.QtCore import QCoreApplication;"
    "from app.quit_request import QuitListener;"
    "a=QCoreApplication([]); time.sleep(3); listener=QuitListener(sys.argv[2]);"
    "listener.requested.connect(a.quit); sys.exit(a.exec())",
    str(Path(__file__).resolve().parent.parent), late_name])
try:
    gone, said = qr.ask_all(
        wait=10, running=lambda: ["the tray tracker"] if late.poll() is None else [],
        send=lambda message, wait, name: wi.send(message, wait=wait, name=late_name))
    check("a real tracker socket created after startup still receives quit",
          gone and late.wait(5) == 0)
finally:
    if late.poll() is None:
        late.kill()
        late.wait()

# The tracker's end of it, over a real socket under a test name.
name = f"WallpaperEngineToolkit.tracker-test.{os.getpid()}"
listener = qr.QuitListener(name)
asked: list[bool] = []
widgets: list[bool] = []
listener.requested.connect(lambda: asked.append(True))
listener.widget_requested.connect(widgets.append)
check("the tracker listens on its socket", listener.listening)
wi.send("show Tracker", wait=1.0, name=name)
wi.send("widget:show", wait=1.0, name=name)
wi.send("widget:invalid", wait=1.0, name=name)
wi.send("widget:hide", wait=1.0, name=name)
wi.send(qr.QUIT, wait=1.0, name=name)
until = time.monotonic() + 3
while not asked and time.monotonic() < until:
    app.processEvents()
    time.sleep(0.02)
check("a quit request reaches it, and only that one does", asked == [True])
check("widget visibility requests reach the tracker without quitting it", widgets == [True, False])

# A tracker started before the notification area exists waits for it, up to
# two minutes, holding its mutex all along: it must hear the request then too.
# (CI's Windows has no notification area, and that is how this was found.)
from types import SimpleNamespace                                        # noqa: E402
from app import tracker_tray                                             # noqa: E402

real_tray_icon = tracker_tray.QSystemTrayIcon
tracker_tray.QSystemTrayIcon = SimpleNamespace(isSystemTrayAvailable=lambda: False)
name = f"WallpaperEngineToolkit.tracker-wait-test.{os.getpid()}"
# Its own listener: the one above stays alive, as a tracker's does.
waiting_listener = qr.QuitListener(name)
heard: list[bool] = []
waiting_listener.requested.connect(lambda: heard.append(True))
# The request, from a second process as the installer's would come.
asker = __import__("subprocess").Popen(
    [sys.executable, "-c",
     "import sys, time; sys.path.insert(0, sys.argv[1]); time.sleep(0.5);"
     "from PySide6.QtCore import QCoreApplication; a = QCoreApplication([]);"
     "from app import window_instance as wi; wi.send('quit:', wait=5, name=sys.argv[2])",
     str(Path(__file__).resolve().parent.parent), name])
started = time.monotonic()
found = tracker_tray._wait_for_tray(app, lambda: bool(heard))
asker.wait(10)
tracker_tray.QSystemTrayIcon = real_tray_icon
check("a tracker still waiting for the notification area hears the request, and stops waiting",
      not found and heard == [True] and time.monotonic() - started < 10)

from PySide6.QtCore import QTimer                                          # noqa: E402
from unittest.mock import patch                                           # noqa: E402

available = iter((False, True))
stop = []
QTimer.singleShot(0, lambda: stop.append(True))
with patch.object(tracker_tray, "QSystemTrayIcon",
                  SimpleNamespace(isSystemTrayAvailable=lambda: next(available))), \
        patch.object(tracker_tray, "TRAY_POLL_SECONDS", 0):
    found = tracker_tray._wait_for_tray(app, lambda: bool(stop))
check("quit wins when the notification area appears at the same time", not found and stop == [True])

print()
print("PASSED %d/%d" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
