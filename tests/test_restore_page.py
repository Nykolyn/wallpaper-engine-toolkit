"""Restore page: what each monitor's panel says, its switch, and that nothing
happens without a yes. Run it directly (needs Qt, but no windows on screen):

    .venv\\Scripts\\python.exe tests\\test_restore_page.py

The page is shown its made-up state; nothing here reads this machine's data.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="wallpaper_restore_page_test_"))
# Before any app module: the data folder resolves when they are imported.
os.environ["WALLPAPER_TOOLKIT_DATA"] = str(TMP / "data")
(TMP / "data").mkdir(parents=True, exist_ok=True)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(errors="replace")

from PySide6.QtWidgets import QApplication                              # noqa: E402

app = QApplication.instance() or QApplication([])

from app.main_window import PAGE_ORDER, PAGE_SHORTCUTS                   # noqa: E402
from app.pages.restore import RestorePage                                # noqa: E402
from app.settings import Settings                                        # noqa: E402

results: list[bool] = []


def check(label: str, condition: bool) -> None:
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label)


settings = Settings()
page = RestorePage(settings)
page.load_fixture("idle")
texts = page.texts()
check("the made-up state shows two monitors", sorted(texts) == ["Monitor0", "Monitor1"])
one = texts["Monitor1"]
check("a restorable pass says what it was and what Restore brings",
      one["button_enabled"] and one["current"] == "Now: 2/194"
      and one["lost"] == "Thrown away 2026-11-08 18:34: 168/194 · Restore brings it to 170/194"
      and one["source"] == "list from file access times" and one["auto"])
zero = texts["Monitor0"]
check("one with nothing to restore says why, and its button is off",
      not zero["button_enabled"]
      and zero["reason"] == "Wallpaper Engine has not thrown a pass away on Monitor0."
      and not zero["auto"])

check("the switch is drawn where it stands",
      page.panels["Monitor1"].auto.knob_position == 1.0
      and page.panels["Monitor0"].auto.knob_position == 0.0)
check("an empty line takes no room", page.panels["Monitor0"].source.isHidden())

page.panels["Monitor0"].auto.setChecked(True)
check("the switch is saved per monitor", settings.get("restore", "auto.Monitor0", None) is True
      and Settings.load().get("restore", "auto.Monitor0", None) is True)

asked = []
page._answer = lambda dialog: asked.append(dialog) or False
page.restore("Monitor1")
check("Restore asks first, and a no starts nothing", len(asked) == 1 and page.worker is None)

check("the page has no number shortcut, and the old ones stay",
      "restore" in PAGE_ORDER and "restore" not in PAGE_SHORTCUTS
      and PAGE_SHORTCUTS["reserve"] == "Ctrl+0" and PAGE_SHORTCUTS["settings"] == "Ctrl+9")

print()
print("PASSED %d/%d" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
