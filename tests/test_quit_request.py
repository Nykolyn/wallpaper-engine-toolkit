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
    send=lambda m, wait, name: sent.append((m, name)), sleep=clock.sleep, clock=clock)
check("each running part is asked on its own socket",
      sent == [(qr.QUIT, wi.server_name()), (qr.QUIT, qr.tracker_server_name())])
check("and once they are gone, that is said", gone and "nothing runs any more" in said)

# A busy window stays: the wait ends, and says what is still there.
clock = Clock()
gone, said = qr.ask_all(
    wait=5, running=lambda: ["the window"], send=lambda m, wait, name: True,
    sleep=clock.sleep, clock=clock)
check("what will not quit is waited for, then named",
      not gone and "the window" in said and 5.0 <= clock.now < 6.0)

# The tracker's end of it, over a real socket under a test name.
name = f"WallpaperEngineToolkit.tracker-test.{os.getpid()}"
listener = qr.QuitListener(name)
asked: list[bool] = []
listener.requested.connect(lambda: asked.append(True))
check("the tracker listens on its socket", listener.listening)
wi.send("show Tracker", wait=1.0, name=name)
wi.send(qr.QUIT, wait=1.0, name=name)
until = time.monotonic() + 3
while not asked and time.monotonic() < until:
    app.processEvents()
    time.sleep(0.02)
check("a quit request reaches it, and only that one does", asked == [True])

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

print()
print("PASSED %d/%d" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
