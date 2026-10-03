"""Steam's folders, found in the background: what is said before the answer is
in, when it arrives, and what the Rotator's settings do with it.

Run it directly (no Qt needed):

    .venv\\Scripts\\python.exe tests\\test_steam_paths.py

Steam is made up in a temporary folder, and its registry entry is a stand-in
the test answers when it chooses. `test_startup.py` checks the same answer
arriving in a whole window and tray.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="wallpaper_steam_paths_test_"))
# Before any app module: the data folder resolves when they are imported.
os.environ["WALLPAPER_TOOLKIT_DATA"] = str(TMP / "data")
(TMP / "data").mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.engines import steam_paths                                      # noqa: E402
from app.engines.rotator import config as rconfig                        # noqa: E402
from app import settings as app_settings                                 # noqa: E402

results: list[bool] = []


def check(label: str, condition: bool) -> None:
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label)


STEAM = TMP / "Steam"
MYPROJECTS = STEAM / "steamapps" / "common" / "wallpaper_engine" / "projects" / "myprojects"
MYPROJECTS.mkdir(parents=True)
(MYPROJECTS.parent.parent / "config.json").write_text("{}", encoding="utf-8")
(STEAM / "steamapps" / "libraryfolders.vdf").write_text(
    '"path"\t\t"' + str(STEAM).replace("\\", "\\\\") + '"\n"apps"\n{\n"431960"\t"1"\n}\n',
    encoding="utf-8")

answer = threading.Event()
asked_on: list[str] = []
where = {"steam": str(STEAM)}


def registry() -> str:
    asked_on.append(threading.current_thread().name)
    answer.wait(10)
    return where["steam"]


steam_paths._from_registry = registry
rconfig.CONFIG_PATH = TMP / "rotator.json"

print("-- before the answer is in --")
check("nothing is known, and asking does not wait",
      steam_paths.known(steam_paths.myprojects_dir) is None and not steam_paths.found())
check("asking starts the finding, on a thread of its own",
      steam_paths._finding is not None and steam_paths._finding.is_alive())
heard: list[str] = []
steam_paths.when_found(lambda: heard.append(threading.current_thread().name))
check("the settings' defaults are empty meanwhile, as with no Steam",
      app_settings.default_copier_dest() == "" and app_settings.default_creator_target() == "")
first_run = rconfig.Config.load()
check("a first run's Rotator settings: myprojects empty, and not saved yet",
      first_run.destination == "" and not rconfig.CONFIG_PATH.exists())

print("-- when it arrives --")
answer.set()
check("the finding finishes", steam_paths.wait_found(10))
check("Steam was asked on the finding thread alone", set(asked_on) == {"steam folders"})
check("whoever asked to hear is told, from that thread", heard == ["steam folders"])
check("and the answers are there to read without waiting",
      steam_paths.known(steam_paths.myprojects_dir) == MYPROJECTS
      and app_settings.default_copier_dest() == str(MYPROJECTS))
now: list[bool] = []
steam_paths.when_found(lambda: now.append(True))
check("asking to hear once it is known is answered at once", now == [True])
check("the Rotator's waiting settings take myprojects, and are saved with it",
      first_run.take_found_folders() and first_run.destination == str(MYPROJECTS)
      and json.loads(rconfig.CONFIG_PATH.read_text(encoding="utf-8"))["destination"]
      == str(MYPROJECTS))
check("and a second time changes nothing", not first_run.take_found_folders())

print("-- what the Rotator keeps --")
rconfig.CONFIG_PATH.write_text(json.dumps({"source": "R:/reserve", "destination": "",
                                           "count": 5}), encoding="utf-8")
stored_empty = rconfig.Config.load()
check("an empty myprojects the file says is kept: it was written, not defaulted",
      not stored_empty.take_found_folders() and stored_empty.destination == "")
rconfig.CONFIG_PATH.write_text(json.dumps({"destination": "D:/mine"}), encoding="utf-8")
check("a chosen one is kept", not rconfig.Config.load().take_found_folders()
      and rconfig.Config.load().destination == "D:/mine")
rconfig.CONFIG_PATH.write_text(json.dumps({"source": "R:/reserve"}), encoding="utf-8")
older = rconfig.Config.load()
check("one the file never had is Steam's, known now", older.destination == str(MYPROJECTS))

print("-- no Steam at all --")
steam_paths.forget()
answer.clear()
where["steam"] = str(TMP / "no steam here")
steam_paths._FALLBACK_ROOTS = ()
rconfig.CONFIG_PATH.unlink()
waiting = rconfig.Config.load()
check("the first run waits for the answer before saving", not rconfig.CONFIG_PATH.exists())
answer.set()
steam_paths.wait_found(10)
check("which is that there is no Steam: myprojects stays empty",
      steam_paths.known(steam_paths.myprojects_dir) is None
      and not waiting.take_found_folders() and waiting.destination == "")
check("and the defaults are saved as they are",
      json.loads(rconfig.CONFIG_PATH.read_text(encoding="utf-8"))["destination"] == "")

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
