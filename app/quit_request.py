"""Asking every running copy of the toolkit to quit — what the installer does first.

An update replaces the program's files and a running copy holds them open; an
uninstall removes them. So before either, the installer runs
``WallpaperEngineToolkit.exe --quit``, which asks each part that is running to
quit the way you would, and waits until they have:

- **the window**, on the socket it already listens on (``window_instance``):
  ``quit:``. A window in the middle of something — a run moving folders, a
  scan, a question waiting for an answer — does not quit, and comes forward
  instead, so you can see why.
- **the tray tracker**, on a socket of its own: ``quit:`` is its menu's Quit.

Each holds a named mutex for as long as it runs, so "gone" is when the mutex is.
Copies from before 3.19.0 do not listen for this: a window of theirs comes
forward on the request and stays, and the installer ends a tracker of theirs
through its logon task and asks you to close what is left.

Nothing here reads the data folder, so asking costs nothing and changes nothing.
"""
from __future__ import annotations

import time

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from . import window_instance

QUIT = "quit:"
# The same as tracker_feed.TRAY_MUTEX; that module reads the data folder when
# it is imported, and this one must not.
TRAY_MUTEX = "Local\\WallpaperEngineToolkitTracker"
WAIT_SECONDS = 15.0


def tracker_server_name() -> str:
    """The tray tracker's socket, per user like the window's."""
    return window_instance.server_name().replace(".window.", ".tracker.")


class QuitListener(QObject):
    """The tray tracker's end of its socket: says when it is asked to quit."""

    requested = Signal()

    def __init__(self, name: str | None = None, parent: QObject | None = None):
        super().__init__(parent)
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._connected)
        name = name or tracker_server_name()
        QLocalServer.removeServer(name)   # a leftover from a crash, if any
        self.listening = self.server.listen(name)

    def _connected(self) -> None:
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            socket.readyRead.connect(lambda s=socket: self._read(s))
            socket.disconnected.connect(socket.deleteLater)
            if socket.bytesAvailable():
                self._read(socket)

    def _read(self, socket: QLocalSocket) -> None:
        while socket.canReadLine():
            line = bytes(socket.readLine()).decode("utf-8", "replace")
            if window_instance.parse_command(line) == ("quit", ""):
                self.requested.emit()


def running() -> list[str]:
    """What of the toolkit runs for this user now, in words."""
    found = []
    if window_instance.already_running():
        found.append("the window")
    if window_instance.already_running(TRAY_MUTEX):
        found.append("the tray tracker")
    return found


def ask_all(wait: float = WAIT_SECONDS, *, running=running, send=window_instance.send,
            sleep=time.sleep, clock=time.monotonic) -> tuple[bool, str]:
    """Ask the window and the tracker to quit and wait for them to be gone.

    Returns (nothing runs any more, what happened in words). The other
    arguments stand in for the real ones in the tests.
    """
    found = running()
    if not found:
        return True, "nothing of the toolkit was running"
    for what in found:
        name = (window_instance.server_name() if what == "the window"
                else tracker_server_name())
        send(QUIT, wait=2.0, name=name)
    deadline = clock() + wait
    left = running()
    while left and clock() < deadline:
        sleep(0.25)
        left = running()
    if not left:
        return True, f"asked {' and '.join(found)} to quit; nothing runs any more"
    return False, (f"still running after {wait:.0f} s: {' and '.join(left)} — busy with "
                   f"something, waiting for an answer, or a version from before 3.19.0")
