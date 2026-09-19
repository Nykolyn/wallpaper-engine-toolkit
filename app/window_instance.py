"""window_instance.py — one toolkit window, in a process of its own.

**Why its own process.** The tray used to build the window inside the tracker.
On 18 September the window froze during a Review, and the "Application Hang"
Windows recorded names the tracker's pid — because the window *was* the tracker.
Ending the frozen window ended the count with it. Sharing the process also meant
the window ran at the priority Task Scheduler gives the logon task (below
normal), and shared a GUI thread with the tray, which reads Wallpaper Engine's
files on a spinning disk every second and every playlist file on every look.

So the tray now starts the window as an ordinary program at normal priority,
and afterwards only asks it to come forward. Whatever the window does, the tray
keeps counting; whatever the tray waits on, the window keeps answering.

**One window, still.** Building the window in the tray was how a second click
raised the same window instead of opening another. That is kept the way the
tracker keeps to one tray icon: a named mutex says whether a window exists, and
a local socket carries the request to show it — from the tray, or from a second
launch of the program, which passes the request on and exits.
"""
from __future__ import annotations

import ctypes
import getpass
import os
import re
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

MUTEX_NAME = "Local\\WallpaperEngineToolkitWindow"

# How long a second launch keeps trying to reach the first. The first may have
# only just started, with its socket not yet listening.
REACH_SECONDS = 5.0

_NORMAL_PRIORITY_CLASS = 0x00000020
_CREATE_BREAKAWAY_FROM_JOB = 0x01000000
_ERROR_ALREADY_EXISTS = 183
_SYNCHRONIZE = 0x00100000
_ASFW_ANY = 0xFFFFFFFF

if os.name == "nt":
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateMutexW.restype = wintypes.HANDLE
    _k32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    _k32.OpenMutexW.restype = wintypes.HANDLE
    _k32.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.GetCurrentProcess.restype = wintypes.HANDLE
    _k32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _u32 = ctypes.WinDLL("user32", use_last_error=True)
    _u32.AllowSetForegroundWindow.argtypes = [wintypes.DWORD]
else:                                                      # pragma: no cover
    _k32 = _u32 = None


def server_name() -> str:
    """The socket's name, per user: two people logged in get a window each."""
    user = re.sub(r"[^A-Za-z0-9_.-]", "_", getpass.getuser() or "user")
    return f"WallpaperEngineToolkit.window.{user}"


def already_running(mutex_name: str = MUTEX_NAME) -> bool:
    """Whether a toolkit window exists, without claiming to be it."""
    if _k32 is None:
        return False
    handle = _k32.OpenMutexW(_SYNCHRONIZE, False, mutex_name)
    if not handle:
        return False
    _k32.CloseHandle(handle)
    return True


class WindowInstance(QObject):
    """The running window's end of the socket: hears "show" and says so."""

    show_requested = Signal(str)          # the tab to show, or ""

    def __init__(self, name: str, mutex, parent: QObject | None = None):
        super().__init__(parent)
        self._mutex = mutex               # held for the life of the process
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._connected)
        QLocalServer.removeServer(name)   # a leftover from a crash, if any
        self.listening = self.server.listen(name)

    @classmethod
    def claim(cls, mutex_name: str = MUTEX_NAME, name: str | None = None,
              parent: QObject | None = None) -> "WindowInstance | None":
        """Become the one window, or None if there already is one."""
        mutex = None
        if _k32 is not None:
            mutex = _k32.CreateMutexW(None, False, mutex_name)
            if mutex and ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
                _k32.CloseHandle(mutex)
                return None
        return cls(name or server_name(), mutex, parent)

    def release(self) -> None:
        """Stop being the one window (tests; a process exit does this anyway)."""
        self.server.close()
        if self._mutex and _k32 is not None:
            _k32.CloseHandle(self._mutex)
            self._mutex = None

    def _connected(self) -> None:
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            socket.readyRead.connect(lambda s=socket: self._read(s))
            socket.disconnected.connect(socket.deleteLater)
            if socket.bytesAvailable():
                self._read(socket)

    def _read(self, socket: QLocalSocket) -> None:
        while socket.canReadLine():
            line = bytes(socket.readLine()).decode("utf-8", "replace").strip()
            verb, _, tab = line.partition(" ")
            if verb == "show":
                self.show_requested.emit(tab.strip())


def ask_to_show(tab: str = "", wait: float = REACH_SECONDS,
                name: str | None = None) -> bool:
    """Ask a running window to come forward. False if none could be reached."""
    deadline = time.monotonic() + max(0.0, wait)
    while True:
        socket = QLocalSocket()
        socket.connectToServer(name or server_name())
        if socket.waitForConnected(500):
            # Only the process the user just clicked may hand over the
            # foreground; without this Windows flashes the taskbar instead.
            if _u32 is not None:
                _u32.AllowSetForegroundWindow(_ASFW_ANY)
            socket.write(f"show {tab}\n".encode("utf-8"))
            # On Windows the write only reaches the pipe from the event loop, and
            # waitForBytesWritten alone comes back with all of it still queued.
            # Dropping the socket then drops the request with it — and a second
            # launch exits straight after asking. So the loop is run until the
            # bytes are out, with a limit.
            until = time.monotonic() + 2.0
            while socket.bytesToWrite() and time.monotonic() < until:
                socket.waitForBytesWritten(50)
                QCoreApplication.processEvents()
            sent = not socket.bytesToWrite()
            socket.disconnectFromServer()
            while socket.state() != QLocalSocket.UnconnectedState \
                    and time.monotonic() < until:
                socket.waitForDisconnected(50)
                QCoreApplication.processEvents()
            return sent
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.2)


def window_command(tab: str = "") -> list[str]:
    """How to start the window on this machine: the built exe, or the source."""
    if getattr(sys, "frozen", False):
        command = [sys.executable]
    else:
        exe = Path(sys.executable)
        windowed = exe.with_name("pythonw.exe")
        script = Path(__file__).resolve().parent.parent / "run_app.py"
        command = [str(windowed if windowed.exists() else exe), str(script)]
    return command + (["--tab", tab] if tab else [])


def launch(tab: str = "") -> subprocess.Popen:
    """Start the window as a program of its own, at normal priority.

    A child inherits a below-normal priority class unless it is told otherwise,
    and the tray runs at the one Task Scheduler gives it. It is also started out
    of the task's job, where Windows allows that, so ending the task — or the
    tray — does not take an open window down with it.
    """
    command = window_command(tab)
    cwd = str(Path(sys.executable).resolve().parent if getattr(sys, "frozen", False)
              else Path(__file__).resolve().parent.parent)
    flags = _NORMAL_PRIORITY_CLASS | _CREATE_BREAKAWAY_FROM_JOB
    try:
        return subprocess.Popen(command, cwd=cwd, creationflags=flags, close_fds=True)
    except OSError:
        # The job does not allow breaking away; a window inside it is still a
        # window of its own.
        return subprocess.Popen(command, cwd=cwd, creationflags=_NORMAL_PRIORITY_CLASS,
                                close_fds=True)


# ---- Priority ----------------------------------------------------------------

_ProcessIoPriority = 33
_IO_PRIORITY_NORMAL = 2
_ProcessMemoryPriority = 0
_MEMORY_PRIORITY_NORMAL = 5


class _MemoryPriority(ctypes.Structure):
    _fields_ = [("MemoryPriority", wintypes.ULONG)]


def make_normal_priority(process=None) -> dict[str, bool]:
    """Undo anything below normal this process inherited, as far as allowed.

    CPU, disk and memory priority are three separate things, and a process
    started from a low one can inherit all three. Disk matters most here: the
    wallpapers live on a hard disk Wallpaper Engine is streaming video from,
    and a read at low priority waits behind every one of its reads.

    `process` is a handle to another process to do the same for — Wallpaper
    Engine, when the Rotator starts it again.
    """
    done = {"cpu": False, "io": False, "memory": False}
    if _k32 is None:
        return done
    me = process or _k32.GetCurrentProcess()
    done["cpu"] = bool(_k32.SetPriorityClass(me, _NORMAL_PRIORITY_CLASS))
    try:
        ntdll = ctypes.WinDLL("ntdll")
        ntdll.NtSetInformationProcess.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                  ctypes.c_void_p, wintypes.ULONG]
        value = wintypes.ULONG(_IO_PRIORITY_NORMAL)
        done["io"] = ntdll.NtSetInformationProcess(
            me, _ProcessIoPriority, ctypes.byref(value), ctypes.sizeof(value)) == 0
    except (OSError, AttributeError):
        pass
    try:
        _k32.SetProcessInformation.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                               ctypes.c_void_p, wintypes.DWORD]
        info = _MemoryPriority(_MEMORY_PRIORITY_NORMAL)
        done["memory"] = bool(_k32.SetProcessInformation(
            me, _ProcessMemoryPriority, ctypes.byref(info), ctypes.sizeof(info)))
    except (OSError, AttributeError):
        pass
    return done
