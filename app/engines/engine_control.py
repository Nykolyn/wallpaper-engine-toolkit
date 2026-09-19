"""engine_control.py — closing Wallpaper Engine properly, and starting it again.

A playlist can only be changed from outside while Wallpaper Engine is closed.
It reads its ``config.json`` as it starts and writes its own copy back as it
exits, so an edit made while it runs is overwritten; and its command line has
no way in — ``-control openPlaylist`` takes the *name* of a playlist it already
holds, and there is no command to quit or to reload.

**Closing.** Its tray menu is an ordinary menu resource inside
``wallpaper64.exe``, and the Quit item is command ``40010`` (2.8.42; pause is
40022, mute 40031). Posted to the hidden ``WPEEventWindow`` of the engine's own
process, it quits exactly as the tray does: measured, 0.2 s, with
``playliststatetime.bin`` written on the way out. Posted to ``WPETrayWindow``
instead it is ignored, and so is ``WM_CLOSE``. Nothing here ever kills the
process — a forced end skips what Wallpaper Engine saves as it exits.

**Starting.** With the same program and arguments it was running with, read
from the process before it is closed. ``launcher.exe`` is deliberately not
used: started by hand it chose the 32-bit engine and opened the library window.
A restart keeps each monitor's pass — both survived one here with the same
pass number and the same wallpapers still to come.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import time
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path

QUIT_COMMAND = 40010
EVENT_WINDOW = "WPEEventWindow"
ENGINES = ("wallpaper64.exe", "wallpaper32.exe")
# Its helpers go down with it, but not in the same instant. Starting it again
# while one is still there can meet its "already running" check.
HELPERS = ENGINES + ("wallpaperui.exe", "webwallpaper64.exe", "webwallpaper32.exe",
                     "edgewallpaper64.exe")
# The library window opened on start; a rotation has no reason to show it.
DROPPED_ARGUMENTS = {"-showbrowse"}

QUIT_SECONDS = 30.0
SETTLE_SECONDS = 15.0
START_SECONDS = 30.0

_WM_COMMAND = 0x0111
_SYNCHRONIZE = 0x00100000
_QUERY_LIMITED = 0x1000
_SET_INFORMATION = 0x0200
_WAIT_OBJECT_0 = 0
_NORMAL_PRIORITY_CLASS = 0x00000020
_CREATE_BREAKAWAY_FROM_JOB = 0x01000000
_ProcessCommandLineInformation = 60
_TH32CS_SNAPPROCESS = 0x2
_INVALID_HANDLE = wintypes.HANDLE(-1).value

if os.name == "nt":
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _u32 = ctypes.WinDLL("user32", use_last_error=True)
    _ntdll = ctypes.WinDLL("ntdll")
    _shell = ctypes.WinDLL("shell32")
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _k32.WaitForSingleObject.restype = wintypes.DWORD
    _k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                wintypes.LPWSTR,
                                                ctypes.POINTER(wintypes.DWORD)]
    _k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    _ntdll.NtQueryInformationProcess.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                 ctypes.c_void_p, wintypes.ULONG,
                                                 ctypes.POINTER(wintypes.ULONG)]
    _shell.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    _shell.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    _k32.LocalFree.argtypes = [wintypes.HLOCAL]
    _k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    _k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    _u32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                  wintypes.LPARAM]
    _u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                              ctypes.POINTER(wintypes.DWORD)]
    _u32.FindWindowExW.restype = wintypes.HWND
    _u32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR,
                                   wintypes.LPCWSTR]
else:                                                      # pragma: no cover
    _k32 = _u32 = _ntdll = _shell = None


class _UnicodeString(ctypes.Structure):
    _fields_ = [("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT),
                ("Buffer", ctypes.c_void_p)]


class _ProcessEntry(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]


@dataclass
class Engine:
    """The Wallpaper Engine that is running, and how it was started."""
    pid: int
    exe: str
    arguments: list[str] = field(default_factory=list)

    @property
    def install_dir(self) -> Path:
        return Path(self.exe).parent

    def restart_command(self) -> list[str]:
        return [self.exe] + restart_arguments(self.arguments)


def restart_arguments(arguments: list[str]) -> list[str]:
    """The arguments to start it again with — the same, less the library window."""
    return [a for a in arguments if a.lower() not in DROPPED_ARGUMENTS]


# ---- Looking ------------------------------------------------------------------

def _open(pid: int, access: int):
    return _k32.OpenProcess(access, False, pid) if _k32 is not None else None


def process_image(pid: int) -> str:
    handle = _open(pid, _QUERY_LIMITED)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if _k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        _k32.CloseHandle(handle)


def process_arguments(pid: int) -> list[str] | None:
    """The command line a process was started with, split as Windows splits it."""
    handle = _open(pid, _QUERY_LIMITED)
    if not handle:
        return None
    try:
        buf = ctypes.create_string_buffer(65536)
        needed = wintypes.ULONG()
        if _ntdll.NtQueryInformationProcess(handle, _ProcessCommandLineInformation, buf,
                                            len(buf), ctypes.byref(needed)) != 0:
            return None
        text = _UnicodeString.from_buffer(buf)
        line = ctypes.wstring_at(text.Buffer, text.Length // 2) if text.Buffer else ""
    finally:
        _k32.CloseHandle(handle)
    count = ctypes.c_int()
    argv = _shell.CommandLineToArgvW(line, ctypes.byref(count))
    if not argv:
        return None
    try:
        return [argv[i] for i in range(count.value)]
    finally:
        _k32.LocalFree(argv)


def processes() -> list[tuple[int, str]]:
    """(pid, executable name) of every process."""
    if _k32 is None:
        return []
    snap = _k32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if not snap or snap == _INVALID_HANDLE:
        return []
    found = []
    try:
        entry = _ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        ok = _k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            found.append((entry.th32ProcessID, entry.szExeFile))
            ok = _k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        _k32.CloseHandle(snap)
    return found


def _event_window(pid: int):
    """The engine's hidden event window. The UI process registers one too."""
    hwnd = None
    while True:
        hwnd = _u32.FindWindowExW(None, hwnd, EVENT_WINDOW, None)
        if not hwnd:
            return None
        owner = wintypes.DWORD()
        _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            return hwnd


def running() -> Engine | None:
    """The Wallpaper Engine that is running now, or None."""
    if _u32 is None:
        return None
    hwnd = None
    while True:
        hwnd = _u32.FindWindowExW(None, hwnd, EVENT_WINDOW, None)
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        exe = process_image(pid.value)
        if Path(exe).name.lower() in ENGINES:
            argv = process_arguments(pid.value) or [exe]
            return Engine(pid=pid.value, exe=exe, arguments=argv[1:])


# ---- Closing ------------------------------------------------------------------

def _wait_gone(pid: int, seconds: float) -> bool:
    handle = _open(pid, _SYNCHRONIZE)
    if not handle:
        return True                      # already gone
    try:
        return _k32.WaitForSingleObject(handle, int(seconds * 1000)) == _WAIT_OBJECT_0
    finally:
        _k32.CloseHandle(handle)


def helpers_running() -> list[str]:
    return sorted({name for _pid, name in processes() if name.lower() in HELPERS})


def close(engine: Engine, seconds: float = QUIT_SECONDS) -> bool:
    """Close it the way its tray menu does, and wait for it and its helpers to go.

    False when it is still running afterwards — nothing is forced.
    """
    hwnd = _event_window(engine.pid)
    if hwnd is None:
        return _wait_gone(engine.pid, 0)
    _u32.PostMessageW(hwnd, _WM_COMMAND, QUIT_COMMAND, 0)
    if not _wait_gone(engine.pid, seconds):
        return False
    deadline = time.monotonic() + SETTLE_SECONDS
    while helpers_running() and time.monotonic() < deadline:
        time.sleep(0.25)
    return True


# ---- Starting -----------------------------------------------------------------

def _clean_environment() -> dict[str, str]:
    """This process's environment without what the frozen toolkit adds for itself."""
    return {k: v for k, v in os.environ.items()
            if not k.upper().startswith(("_MEI", "_PYI", "QT_", "PYSIDE"))}


def start(engine: Engine, seconds: float = START_SECONDS) -> bool:
    """Start it again as it was started, at normal priority, and wait for it to settle.

    Out of this program's job where Windows allows it, so closing the toolkit
    never takes the wallpapers with it; and at normal CPU, disk and memory
    priority whatever this process was given — it streams video from disk.
    """
    from ..window_instance import make_normal_priority

    command = engine.restart_command()
    cwd = str(engine.install_dir)
    env = _clean_environment()
    try:
        child = subprocess.Popen(command, cwd=cwd, env=env, close_fds=True,
                                 creationflags=_NORMAL_PRIORITY_CLASS
                                 | _CREATE_BREAKAWAY_FROM_JOB)
    except OSError:
        child = subprocess.Popen(command, cwd=cwd, env=env, close_fds=True,
                                 creationflags=_NORMAL_PRIORITY_CLASS)
    handle = _open(child.pid, _SET_INFORMATION)
    if handle:
        try:
            make_normal_priority(handle)
        finally:
            _k32.CloseHandle(handle)
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if _event_window(child.pid) is not None:
            return True
        if child.poll() is not None:
            return False
        time.sleep(0.25)
    return child.poll() is None
