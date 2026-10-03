"""A guard for page tests: no filesystem work on the GUI thread for a path on the
wallpaper disk.

The library lives on W:, a 12 TB HDD that takes seconds to wake: a `stat` there
from the GUI thread is a frozen window (docs/development.md). The fixtures'
made-up folders are on X:. With the guard on, every `os` / `os.path` /
`pathlib` / `open` call on the GUI thread for a path on either drive is
recorded, with the line that made it; work on a thread is left alone, as is
everything on other drives (a test's own temp folders).

    import gui_guard
    gui_guard.install()
    ...build pages, load fixtures...
    check("nothing on the wallpaper disk from the GUI thread", not gui_guard.violations)

A test that needs real files there — a real Tracker reading a made-up
config.json — makes them in a temporary folder and calls `watch(folder)`, and
the guard treats that folder as the wallpaper disk too.

It only records: raising would make the page take a path it never takes for
real, and hide the next call.
"""
from __future__ import annotations

import builtins
import io
import os
import threading
import traceback

DRIVES = ("W:", "X:")
violations: list[str] = []
_installed = False
_watched: list[str] = []        # folders treated as the wallpaper disk, by watch()


def watch(folder) -> None:
    """Treat this folder, and everything in it, as the wallpaper disk."""
    _watched.append(os.path.normcase(os.path.abspath(os.fspath(folder))))


def _on_disk(arg) -> bool:
    try:
        text = os.fspath(arg)
    except TypeError:
        return False
    if isinstance(text, bytes):
        text = text.decode(errors="replace")
    if not isinstance(text, str):
        return False
    if text[:2].upper() in DRIVES:
        return True
    if _watched:
        path = os.path.normcase(os.path.abspath(text))
        return any(path == w or path.startswith(w.rstrip(os.sep) + os.sep) for w in _watched)
    return False


def _where() -> str:
    """The first frame of the app's own code that asked."""
    for frame in reversed(traceback.extract_stack()[:-2]):
        if os.sep + "app" + os.sep in frame.filename or "/app/" in frame.filename:
            return f"{os.path.basename(frame.filename)}:{frame.lineno} {frame.name}"
    return "?"


def _wrap(owner, name: str) -> None:
    real = getattr(owner, name)

    def guarded(*args, **kwargs):
        if (args and threading.current_thread() is threading.main_thread()
                and _on_disk(args[0])):
            violations.append(f"{name}({os.fspath(args[0])!r}) at {_where()}")
        return real(*args, **kwargs)

    guarded.__wrapped__ = real
    setattr(owner, name, guarded)


def install() -> None:
    """Patch once per process; later calls do nothing."""
    global _installed
    if _installed:
        return
    _installed = True
    for owner, name in ((os, "stat"), (os, "lstat"), (os, "scandir"), (os, "listdir"),
                        (os, "walk"), (os.path, "exists"), (os.path, "isdir"),
                        (os.path, "isfile"), (os.path, "getsize"), (os.path, "getmtime"),
                        (builtins, "open"), (io, "open")):
        _wrap(owner, name)


def clear() -> None:
    violations.clear()
