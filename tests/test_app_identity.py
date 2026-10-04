"""The balloons' sender: a Start-menu shortcut carrying the app's ID, made only
by the built exe, and the ID taken only once the shortcut is there.

Run it directly (no Qt needed):

    .venv\\Scripts\\python.exe tests\\test_app_identity.py

The decisions are checked with stand-ins for Windows. On Windows the real
shortcut is also made in a temporary folder, read back, and made again when it
points elsewhere; the process's own ID is not changed by the test.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import app_identity as ai                                        # noqa: E402

results: list[bool] = []


def check(label: str, condition: bool) -> None:
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label)


TMP = Path(tempfile.mkdtemp(prefix="app_identity_test_"))
EXE = r"C:\Apps\Toolkit\WallpaperEngineToolkit.exe"
link = TMP / "Programs" / "Toolkit.lnk"
made: list = []
taken: list[str] = []
shortcuts: dict = {}


def read(path):
    return shortcuts.get(path)


def write(path, target):
    made.append((path, target))
    shortcuts[path] = (target, ai.APP_ID)
    path.write_text("lnk")


def claim(frozen=True, target=EXE, where=link, writer=write):
    return ai.claim(frozen=frozen, target=target, link=where, read=read, write=writer,
                    set_id=taken.append)


said = claim(frozen=False)
check("a source run makes no shortcut and keeps its name",
      made == [] and taken == [] and said.startswith("source run"))
said = claim(where=None)
check("with no Start menu to put it in, nothing is taken either", made == [] and taken == [])
said = claim()
check("the built exe makes the shortcut, then takes the ID",
      made == [(link, EXE)] and taken == [ai.APP_ID] and "made" in said and "Toolkit" in said)
said = claim()
check("a shortcut already in place is left alone", len(made) == 1 and taken == [ai.APP_ID] * 2
      and "in place" in said)
said = claim(target=r"D:\Moved\WallpaperEngineToolkit.exe")
check("one pointing at an exe since moved is made again",
      made[-1][1] == r"D:\Moved\WallpaperEngineToolkit.exe" and "made again" in said)
shortcuts[link] = (r"D:\Moved\WallpaperEngineToolkit.exe", "Someone.Else")
claim(target=r"D:\Moved\WallpaperEngineToolkit.exe")
check("and one carrying another ID", shortcuts[link][1] == ai.APP_ID and len(made) == 3)


def refuse(path, target):
    raise OSError("access denied")


taken.clear()
link.unlink()
shortcuts.clear()
said = claim(writer=refuse)
check("a shortcut that cannot be made: the ID is not taken, and that is said",
      taken == [] and "could not make" in said)
check("the shortcut is named for the toolkit, in the user's Start menu",
      ai.shortcut_path() is None if not os.environ.get("APPDATA") else
      ai.shortcut_path().name == "Toolkit.lnk" and "Start Menu" in str(ai.shortcut_path()))

if sys.platform == "win32":
    real = TMP / "real" / "Toolkit.lnk"
    real.parent.mkdir()
    ai._write_shortcut(real, sys.executable)
    check("on Windows a real shortcut is made and reads back with the ID",
          ai._read_shortcut(real) is not None
          and os.path.normcase(ai._read_shortcut(real)[0]) == os.path.normcase(sys.executable)
          and ai._read_shortcut(real)[1] == ai.APP_ID)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
