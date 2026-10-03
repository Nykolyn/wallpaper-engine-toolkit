"""A plain start of the window and of the tray: nothing on the wallpaper disk
from the GUI thread, from the first import on.

Run it directly (needs Qt, but no windows on screen):

    .venv\\Scripts\\python.exe tests\\test_startup.py

Steam is made up, in a temporary folder that `gui_guard` treats as the wallpaper
disk, and the guard is on before `app.settings` is imported: that import used to
look Steam up on the spot, before either program had drawn anything. Steam's
registry entry is a stand-in that does not answer until the test lets it, so the
pages are built while Steam's folders are not known yet, and the test watches
them arrive: in the Settings page's fields, the Copier's destination, the
Rotator's myprojects (saved only then, on a first run) and the Tracker's
config.json.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="wallpaper_startup_test_"))
# Before any app module: the data folder resolves when they are imported.
os.environ["WALLPAPER_TOOLKIT_DATA"] = str(TMP / "data")
(TMP / "data").mkdir(parents=True, exist_ok=True)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
sys.stdout.reconfigure(errors="replace")

from PySide6.QtWidgets import QApplication                               # noqa: E402

qt_app = QApplication(sys.argv)

import gui_guard                                                         # noqa: E402
from app.engines import steam_paths                                      # noqa: E402

results: list[bool] = []


def check(label: str, condition: bool) -> None:
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label)


def wait_for(condition, ms: int = 8000) -> bool:
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        qt_app.processEvents()
        if condition():
            return True
        time.sleep(0.005)
    return condition()


def settle(ms: int) -> None:
    wait_for(lambda: False, ms)


check("steam_paths imports none of the app's settings", "app.settings" not in sys.modules)

# ---- Steam, on the wallpaper disk ----------------------------------------------------------

STEAM = TMP / "Steam"
ENGINE = STEAM / "steamapps" / "common" / "wallpaper_engine"
MYPROJECTS = ENGINE / "projects" / "myprojects"
CONFIG = ENGINE / "config.json"
MYPROJECTS.mkdir(parents=True)
CONFIG.write_text(json.dumps({"steamuser": {"general": {"wallpaperconfig": {
    "selectedwallpapers": {}}}}}), encoding="utf-8")
library = str(STEAM).replace("\\", "\\\\")
(STEAM / "steamapps" / "libraryfolders.vdf").write_text(
    '"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"' + library + '"\n'
    '\t\t"apps"\n\t\t{\n\t\t\t"431960"\t\t"123"\n\t\t}\n\t}\n}\n', encoding="utf-8")

answer = threading.Event()          # Steam's registry entry answers once this is set


def registry() -> str:
    answer.wait(30)
    return str(STEAM)


steam_paths._from_registry = registry

gui_guard.install()
gui_guard.watch(STEAM)

print("-- the window --")
import app.settings                                                      # noqa: E402
check(f"importing the settings asks nothing of Steam's disk ({gui_guard.violations[:3]})",
      gui_guard.violations == [])
from app.engines.rotator import config as rconfig                        # noqa: E402
from app.main_window import MainWindow                                   # noqa: E402
from app.tracker_tray import TrackerTray                                 # noqa: E402
check(f"nor does importing the window and the tray ({gui_guard.violations[:3]})",
      gui_guard.violations == [])

window = MainWindow()
window.show()
settle(300)
check(f"the window starts with nothing on Steam's disk from its thread "
      f"({gui_guard.violations[:3]})", gui_guard.violations == [])
check("while Steam has not answered", not steam_paths.found())
settings_page = window.pages["settings"]
copier = window.pages["copier"]
config = window.pages["rotator"].config
check("its folders are not guessed meanwhile: empty, as with no Steam",
      settings_page.copier_dest.path() == "" and settings_page.creator_target.path() == ""
      and settings_page.myprojects.path() == "" and config.destination == "")
check("and a first run's defaults are not saved with an empty myprojects",
      not rconfig.CONFIG_PATH.exists())
check("nor can the Copier start: there is nowhere to copy to",
      copier.destination.path() == "" and not copier.start_button.isEnabled())

answer.set()
check("once Steam answers, the Settings page shows its folders",
      wait_for(lambda: settings_page.copier_dest.path() == str(MYPROJECTS))
      and settings_page.creator_target.path() == str(MYPROJECTS)
      and settings_page.myprojects.path() == str(MYPROJECTS))
saved = json.loads(rconfig.CONFIG_PATH.read_text(encoding="utf-8")) \
    if rconfig.CONFIG_PATH.exists() else {}
check("the Rotator has its myprojects, and the first run's defaults are saved with it",
      config.destination == str(MYPROJECTS) and saved.get("destination") == str(MYPROJECTS))
window.show_page("copier", animate=False)
check("the Copier shows it when it is next on screen",
      wait_for(lambda: copier.destination.path() == str(MYPROJECTS)))
check("the Tracker finds config.json where Steam has it",
      wait_for(lambda: window.feed.config_path == str(CONFIG)))
settle(300)
check(f"and none of it touched Steam's disk from the window's thread "
      f"({gui_guard.violations[:3]})", gui_guard.violations == [])
window.feed.stop()

print("-- a chosen folder is not replaced --")
steam_paths.forget()
answer.clear()
chosen = str(TMP / "chosen")
mine = app.settings.Settings({"copier": {"dest": chosen}})
window2 = MainWindow(settings=mine, start=False)
answer.set()
check("Steam's answer arrives",
      wait_for(lambda: window2.pages["settings"].creator_target.path() == str(MYPROJECTS)))
check("and leaves the Copier's chosen destination as it was",
      window2.pages["settings"].copier_dest.path() == chosen
      and window2.pages["copier"].destination.path() == chosen)
window2.feed.stop()

print("-- the tray --")
steam_paths.forget()
answer.clear()
gui_guard.clear()
tray = TrackerTray(qt_app)
tray.clock_timer.stop()
settle(300)
check(f"the tray starts with nothing on Steam's disk from its thread "
      f"({gui_guard.violations[:3]})", gui_guard.violations == [])
check("and, Steam not answering yet, no countdown to build", tray.clock is None)
answer.set()
check("once it answers, the tray's count looks where Steam has config.json",
      wait_for(lambda: tray.feed.config_path == str(CONFIG)))
check("and its countdown is built on it", tray.clock is not None)
tray._tick_clock()
settle(300)
check(f"none of it on Steam's disk from the tray's thread ({gui_guard.violations[:3]})",
      gui_guard.violations == [])
tray.feed.stop()

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
