"""The whole window by keyboard, its names and its text, and the wallpaper disk
kept off the GUI thread — across every page, in every made-up state.

Run it directly (needs Qt, but no windows on screen):

    .venv\\Scripts\\python.exe tests\\test_keyboard.py

The window is the one tools/ui_snapshot.py builds: every real page, fabricated
fixtures on a made-up drive X:, services never started. `gui_guard` records any
file call the GUI thread makes for a path on W: (the wallpaper disk) or X:.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="wallpaper_keyboard_test_"))
os.environ["WALLPAPER_TOOLKIT_DATA"] = str(TMP / "data")
(TMP / "data").mkdir(parents=True, exist_ok=True)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(errors="replace")

import gui_guard  # noqa: E402

from PySide6.QtCore import Qt                                           # noqa: E402
from PySide6.QtGui import QFontInfo                                     # noqa: E402
from PySide6.QtTest import QTest                                        # noqa: E402
from PySide6.QtWidgets import QAbstractItemView, QApplication, QLabel, QWidget  # noqa: E402

app = QApplication(sys.argv)

import ui_snapshot  # noqa: E402
from app import animations, theme  # noqa: E402
from app.main_window import PAGE_ORDER, load_shell_fixture, page_for  # noqa: E402
from app.ui.kit import Elided, Table  # noqa: E402
from app.ui.kit.base import TONE_TOKENS, tab_stops  # noqa: E402
import app.pages, app.pages.copier, app.pages.creator, app.pages.overview  # noqa: E401,E402,F401
import app.pages.review, app.pages.rotator, app.pages.settings, app.pages.tracker  # noqa: E401,E402,F401

# On from here: every page made, every state shown. (Importing the app detects
# Steam's folders once, at start, on the main thread: a known follow-up, not a page's.)
gui_guard.install()

results: list[bool] = []


def check(label: str, condition: bool) -> None:
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label)


def settle(ms: int = 60) -> None:
    ui_snapshot.settle(ms)


STATES = [k for k in json.loads((ROOT / "tests/fixtures/ui/shell.json").read_text(encoding="utf-8"))
          if k != "//"]
NAMED = ("IconButton", "Chip", "NavItem", "CaptionButton")

window = ui_snapshot.build_window("overview")
window.resize(1280, 860)
window.show()


def activate() -> None:
    """Offscreen, a window is active only when told: a fixture's dialog shown
    and hidden leaves none active, and then nothing has focus."""
    window.activateWindow()
    settle(10)


activate()
settle(200)


def visible_widgets(root: QWidget) -> list[QWidget]:
    return [w for w in root.findChildren(QWidget) if w.isVisible()]


def describe(w: QWidget) -> str:
    return f"{type(w).__name__}[{w.accessibleName()}]"


def tab_round(start: QWidget, limit: int = 120) -> list[QWidget]:
    """Where focus goes, Tab after Tab from `start`, until it comes round."""
    start.setFocus(Qt.TabFocusReason)
    settle(5)
    order: list[QWidget] = []
    for _ in range(limit):
        w = QApplication.focusWidget()
        if w is None or w in order:
            break
        order.append(w)
        QTest.keyClick(w, Qt.Key_Tab)
    return order


# ---- every page, every state -------------------------------------------------------------

unnamed: list[str] = []
small: list[str] = []
orders_ok: list[str] = []
traps: list[str] = []
states_seen = 0
for key in PAGE_ORDER:
    page = window.pages[key]
    for state in page.FIXTURES or ("idle",):
        fixture = ui_snapshot.frame_fixture(page, state, STATES, load_shell_fixture)
        if fixture is None:
            continue
        window.show_page(key, animate=False)
        window.load_fixture(state, fixture)
        settle(120)
        states_seen += 1
        dialog = getattr(page, "fixture_dialog", None)
        if dialog is not None and dialog.isVisible():
            dialog.hide()
        activate()
        for w in visible_widgets(window):
            cls = type(w).__name__
            if (cls in NAMED or isinstance(w, QAbstractItemView)) and not w.accessibleName():
                unnamed.append(f"{key}/{state}: {describe(w)}")
            if isinstance(w, (QLabel, Elided)):
                font = w.font() if isinstance(w, QLabel) else w._font
                if 0 < QFontInfo(font).pixelSize() < 10:
                    small.append(f"{key}/{state}: {describe(w)} {QFontInfo(font).pixelSize()} px")
        # Tab: down the sidebar, the header's actions, the page, the status line's link
        nav = [window.sidebar.item(k) for k in window.sidebar.keys()]
        order = tab_round(nav[0])
        expected = (nav + [w for w in page.header_actions() if w.isVisible()]
                    + [w for w in tab_stops(page) if w.isVisible() and w.isEnabled()])
        link = window.status.link()
        if link.isVisible():
            expected.append(link)
        if order != expected:
            orders_ok.append(f"{key}/{state}: " + " → ".join(describe(w) for w in order[:30]))
        for w in order:
            if isinstance(w, Table) and order[-1] is not w and order[order.index(w) + 1] is w:
                traps.append(f"{key}/{state}")

check(f"{states_seen} page states were walked", states_seen >= 40)
check("every IconButton, chip, nav item and list has an accessible name"
      + (": " + "; ".join(unnamed[:6]) if unnamed else ""), not unnamed)
check("no text is under 10 px" + (": " + "; ".join(small[:6]) if small else ""), not small)
check("Tab goes down the sidebar, the header's actions, the page in reading order, then the "
      "status line, on every page in every state"
      + (": " + " | ".join(orders_ok[:2]) if orders_ok else ""), not orders_ok)
check("Tab always leaves a table (the arrows move inside it)", not traps)
check("nothing touched the wallpaper disk from the GUI thread, building or showing any page"
      + (": " + "; ".join(gui_guard.violations[:6]) if gui_guard.violations else ""),
      not gui_guard.violations)

# ---- shortcuts -------------------------------------------------------------------------------------

for number, key in enumerate(PAGE_ORDER, start=1):
    QTest.keyClick(window, getattr(Qt, f"Key_{number}"), Qt.ControlModifier)
    settle(5)
    if window.current_page() != key:
        break
check("Ctrl+1 … Ctrl+7 go to the seven pages in the sidebar's order", window.current_page() == PAGE_ORDER[-1])

found = {}
for key in ("tracker", "rotator", "review"):
    page = window.pages[key]
    window.show_page(key, animate=False)
    state = page.FIXTURES[0] if key != "review" else "reviewing"
    window.load_fixture(state, ui_snapshot.frame_fixture(page, state, STATES, load_shell_fixture))
    settle(80)
    activate()
    window.sidebar.item("overview").setFocus(Qt.TabFocusReason)
    field = page.filter_field()
    field.setText("old words")
    QTest.keyClick(window, Qt.Key_F, Qt.ControlModifier)
    settle(5)
    found[key] = QApplication.focusWidget() is field and field.selectedText() == "old words"
    field.clear()
check("Ctrl+F puts the cursor in the page's filter, its words selected to type over "
      "(Tracker, Rotator, Review)", all(found.values()))
window.show_page("copier", animate=False)
settle(30)
before = QApplication.focusWidget()
check("on a page with no filter Ctrl+F does nothing", not window.focus_filter()
      and QApplication.focusWidget() is before)

# ---- focus rings: the keyboard's only ------------------------------------------------------------------

window.show_page("rotator", animate=False)
window.load_fixture("idle")
settle(80)
activate()
item = window.sidebar.item("tracker")
QTest.mouseClick(item, Qt.LeftButton)
settle(20)
window.show_page("rotator", animate=False)
settle(20)
item.setFocus(Qt.MouseFocusReason)
check("a nav item clicked has focus but no ring", item.hasFocus() and not item.focus_visible())
window.sidebar.item("overview").setFocus(Qt.TabFocusReason)
QTest.keyClick(window.sidebar.item("overview"), Qt.Key_Tab)
settle(5)
check("reached by Tab, it has its ring", window.sidebar.item("rotator").focus_visible())

# ---- the type and the tones ----------------------------------------------------------------------

check("no type token is under 10 px", min(spec.px for spec in theme.TYPE.values()) >= 10)
ground = theme.panel_ground()
floor = theme.contrast(theme.color("text.lo"), ground)
NEUTRAL = ("hi", "body", "mid", "lo")
dim = [tone for tone in NEUTRAL
       if theme.contrast(theme.color(TONE_TOKENS[tone]), ground) < floor - 0.01]
check(f"no text tone is dimmer than text.lo ({floor:.1f}:1 on glass)" + (f": {dim}" if dim else ""),
      not dim)
# The status hues are the design's own colours; danger, the darkest, is 4.96:1.
# Each passes WCAG AA for text (4.5:1) on the glass it sits on.
hues = {tone: theme.contrast(theme.color(token), ground)
        for tone, token in TONE_TOKENS.items() if tone not in NEUTRAL}
check("every status hue reads at WCAG AA, 4.5:1, on glass: "
      + ", ".join(f"{t} {c:.2f}" for t, c in hues.items()), min(hues.values()) >= 4.5)

# ---- motion off -------------------------------------------------------------------------------------------

animations.ENABLED = False
window.show_page("rotator", animate=False)
window.load_fixture("running")
settle(120)
activity = window.pages["rotator"].findChildren(QWidget, options=Qt.FindChildrenRecursively)
lines = [w for w in activity if type(w).__name__ == "ActivityLine" and w.isVisible()]
check("with Windows' animations off a spinner rests and its line says \"working\"",
      bool(lines) and all(line.shown_text().startswith("working") for line in lines))
check("and no loop is running", not any(animations.loop(name).running
                                       for name in ("spin", "pulse", "shimmer", "indeterminate")))
animations.ENABLED = True

window.close()
print()
print(f"{'PASSED' if all(results) else 'FAILED'} {sum(results)}/{len(results)}")
sys.exit(0 if all(results) else 1)
