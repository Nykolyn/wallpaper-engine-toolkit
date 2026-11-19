"""The player: Wallpaper Engine's -control command lines and the pause flag.

    .venv\\Scripts\\python.exe tests\\test_player.py

Nothing here starts Wallpaper Engine: the running engine and the process
runner are replaced by fakes, and the data folder is a temporary one.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="player_test_"))
os.environ["WALLPAPER_TOOLKIT_DATA"] = str(TMP / "data")     # before any app module
(TMP / "data").mkdir()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(errors="replace")

from app.engines import player                                  # noqa: E402
from app.engines.engine_control import Engine                   # noqa: E402

FAILED = []


def check(label: str, condition: bool) -> None:
    print(("PASS " if condition else "FAIL ") + label)
    if not condition:
        FAILED.append(label)


EXE = "C:/we/wallpaper64.exe"
ENGINE = Engine(pid=4242, exe=EXE, arguments=[])


class Runner:
    """Records what would have been run; raises `error` when given one."""

    def __init__(self, error: BaseException | None = None):
        self.calls, self.error = [], error

    def __call__(self, args, **kwargs):
        self.calls.append((list(args), kwargs))
        if self.error is not None:
            raise self.error
        return subprocess.CompletedProcess(args, 0, b"", b"")


# ---- the command lines

check("next names the monitor by -location",
      player.control_args(EXE, "nextWallpaper", "Monitor1")
      == [EXE, "-control", "nextWallpaper", "-location", "Monitor1"])
check("open names the file, then the monitor",
      player.control_args(EXE, "openWallpaper", "Monitor0", "D:/w/1/project.json")[-4:]
      == ["-file", "D:/w/1/project.json", "-location", "Monitor0"])
check("pause names no monitor", "-location" not in player.control_args(EXE, "pause"))

# ---- sending

check("no engine: says so", player.send("pause", find=lambda: None)
      == "Wallpaper Engine is not running.")
run = Runner()
check("sent: None", player.send("nextWallpaper", "Monitor1", find=lambda: ENGINE, run=run) is None)
args, kwargs = run.calls[0]
check("with a timeout of 5 s", kwargs.get("timeout") == 5)
check("from the engine's folder", Path(kwargs.get("cwd", "")) == Path(EXE).parent)
check("the command line", args == player.control_args(EXE, "nextWallpaper", "Monitor1"))
check("no answer: says so",
      player.send("pause", find=lambda: ENGINE,
                  run=Runner(subprocess.TimeoutExpired(EXE, 5)))
      == "Wallpaper Engine did not answer.")
check("could not start: the reason",
      "denied" in (player.send("pause", find=lambda: ENGINE, run=Runner(OSError("access denied")))
                   or ""))
run = Runner()
player.pick("Monitor0", "D:/w/1/clip one.mp4", find=lambda: ENGINE, run=run)
check("pick sends the entry's project.json, as a Windows path",
      run.calls[0][0][-4:] == ["-file", os.path.normpath("D:/w/1/project.json"),
                              "-location", "Monitor0"])

# ---- the pause flag

check("nothing recorded: playing", player.is_paused(ENGINE.pid) is False)
check("pause sent", player.pause(find=lambda: ENGINE, run=Runner()) is None)
check("then paused", player.is_paused(ENGINE.pid) is True)
check("another engine since: playing", player.is_paused(ENGINE.pid + 1) is False)
check("no engine: playing", player.is_paused(None) is False)
player.play(find=lambda: ENGINE, run=Runner())
check("play clears it", player.is_paused(ENGINE.pid) is False)
player.pause(find=lambda: ENGINE, run=Runner(OSError("no")))
check("a pause that failed is not recorded", player.is_paused(ENGINE.pid) is False)
(TMP / "data" / player.PLAYER_FILE).write_text("{not json", encoding="utf-8")
check("a broken file: playing", player.is_paused(ENGINE.pid) is False)

# ---- in the background

import threading                                                # noqa: E402
got, done = [], threading.Event()
player.in_background(lambda: "words", lambda r: (got.append(r), done.set()))
check("the result comes back", done.wait(5) and got == ["words"])

print(f"\n{len(FAILED)} failed")
sys.exit(1 if FAILED else 0)
