"""player.py — driving Wallpaper Engine like a music player.

Wallpaper Engine takes commands from a second copy of itself started with
``-control <command>``; that copy hands the command to the running one and
exits. Four are used here:

* ``pause`` / ``play`` — every monitor at once: neither takes a monitor;
* ``nextWallpaper -location MonitorN`` — the next wallpaper of that monitor's
  playlist; the pass carries on and counts it as shown;
* ``openWallpaper -file <project.json> -location MonitorN`` — one wallpaper on
  that monitor, as a click in Wallpaper Engine does: the monitor leaves its
  playlist.

Wallpaper Engine cannot be asked whether it is paused, so a pause sent from
here is remembered in ``player.json`` in the data folder, with the pid of the
engine it was sent to: the window, the tray and the widget read the same
flag, and an engine started since is playing again.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Callable

from .. import external
from . import engine_control

PLAYER_FILE = "player.json"
TIMEOUT_S = 5
NOT_RUNNING = "Wallpaper Engine is not running."
NO_ANSWER = "Wallpaper Engine did not answer."


def control_args(exe: str, command: str, monitor: str | None = None,
                 file: str | None = None) -> list[str]:
    """The command line that hands `command` to the running engine."""
    args = [exe, "-control", command]
    if file:
        args += ["-file", file]
    if monitor:
        args += ["-location", monitor]
    return args


def send(command: str, monitor: str | None = None, file: str | None = None, *,
         find=engine_control.running, run=external.run) -> str | None:
    """Hand a command to Wallpaper Engine. None when it went, else why not, in words."""
    engine = find()
    if engine is None:
        return NOT_RUNNING
    try:
        run(control_args(engine.exe, command, monitor, file), cwd=str(engine.install_dir),
            timeout=TIMEOUT_S, capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return NO_ANSWER
    except OSError as error:
        return f"Could not reach Wallpaper Engine: {error}."
    return None


def _flag(command: str, paused: bool, **kw) -> str | None:
    find = kw.get("find", engine_control.running)
    engine = find()
    error = send(command, find=lambda: engine, **{k: v for k, v in kw.items() if k != "find"})
    if error is None:
        set_paused(paused, engine.pid)
    return error


def pause(**kw) -> str | None:
    """Pause every monitor, and remember it."""
    return _flag("pause", True, **kw)


def play(**kw) -> str | None:
    """Play every monitor again."""
    return _flag("play", False, **kw)


def next_wallpaper(monitor: str, **kw) -> str | None:
    return send("nextWallpaper", monitor, **kw)


def pick(monitor: str, item: str, **kw) -> str | None:
    """Put one wallpaper on `monitor`. `item` is a playlist entry — the
    wallpaper's file — and Wallpaper Engine is handed its folder's project.json.
    Seen live (2026-11-19): the monitor keeps its playlist and its timer
    starts again from 0."""
    project = os.path.join(os.path.dirname(os.path.normpath(item)), "project.json")
    return send("openWallpaper", monitor, project, **kw)


# ---- the pause flag ---------------------------------------------------------------

def _path(folder: Path | None) -> Path:
    if folder is None:
        from ..data_location import data_dir
        folder = data_dir()
    return Path(folder) / PLAYER_FILE


def is_paused(pid: int | None, folder: Path | None = None) -> bool:
    """A pause was sent from here to the engine running as `pid`."""
    if pid is None:
        return False
    try:
        saved = json.loads(_path(folder).read_text(encoding="utf-8"))
        return bool(saved.get("paused")) and saved.get("pid") == pid
    except (OSError, ValueError, AttributeError):
        return False


def set_paused(paused: bool, pid: int, folder: Path | None = None) -> None:
    try:
        _path(folder).write_text(json.dumps({"paused": paused, "pid": pid}), encoding="utf-8")
    except OSError:
        pass                    # the flag only picks a button's glyph


def in_background(work: Callable[[], str | None],
                  done: Callable[[str | None], None] | None = None) -> None:
    """Run `work` on a thread of its own; `done` gets its result on that thread."""
    def body():
        result = work()
        if done is not None:
            done(result)
    threading.Thread(target=body, name="player", daemon=True).start()
