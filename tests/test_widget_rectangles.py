"""Tray-shaped widget progress, labels and contrast without live wallpaper state."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
os.environ["WALLPAPER_TOOLKIT_DATA"] = tempfile.mkdtemp(prefix="widget_rectangles_")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(errors="replace")

from PySide6.QtGui import QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app import desktop_widget as drawing, theme  # noqa: E402
from app.desktop_widget import DesktopWidget  # noqa: E402
from app.engines.wallpaper_timer import Countdown  # noqa: E402
from app.settings import Settings  # noqa: E402

app = QApplication.instance() or QApplication([])
theme.apply(app, styled=False)
checks = []


def check(label, condition):
    checks.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label)


settings = Settings()
widget = DesktopWidget(settings)
p = SimpleNamespace(monitor="Monitor1", playlist="Space", seen=10, total=20, percent=50,
                    current="X:/wallpaper.mp4", current_title="Space wallpaper")
clock = Countdown("Monitor1", 600, running=150, known=True)
widget.render([p], {"Monitor1": clock}, p)
check("new widgets use the rectangle design", widget.options.get("design") == "rectangles")

draw_text = drawing._text
painted = []


def record_text(painter, rect, value, *args, **kwargs):
    painted.append((value, rect))
    draw_text(painter, rect, value, *args, **kwargs)


def snapshot():
    return widget.grab().toImage().scaled(widget.size())


for size in (112, 144, 176):
    widget._set_option("size", size)
    with patch.object(drawing, "_text", record_text):
        painted.clear()
        image = snapshot()
    check(f"{size}: playlist percentage and countdown are drawn", {"50%", "7:30"}.issubset(
        {value for value, _rect in painted}))
    check(f"{size}: transparent corners follow offset rectangles", image.pixelColor(
        round(size * .85), round(size * .15)).alpha() == 0)
    check(f"{size}: rear playlist outline fills independently to fifty percent",
          image.pixelColor(round(size * .17), round(size * .10)).name() == "#4c8dff"
          and image.pixelColor(round(size * .52), round(size * .10)).name() == "#4a5468")
    check(f"{size}: front wallpaper fills a quarter from the left",
          image.pixelColor(round(size * .42), round(size * .83)).name() == "#22c3b0"
          and image.pixelColor(round(size * .70), round(size * .83)).name() == "#2b3345")
    for value, rect in painted:
        if value in ("50%", "7:30"):
            check(f"{size}: {value} label stays inside its widget", widget.rect().contains(rect.toAlignedRect()))

actual_text = QPainter.drawText
drawn = []


def record_draw(painter, *args):
    if isinstance(args[-1], str):
        drawn.append(args[-1])
    return actual_text(painter, *args)


for mode, size in (("compact", 112), ("compact", 144), ("compact", 176), ("expanded", 144)):
    widget._set_option("mode", mode)
    widget._set_option("size", size)
    for delay, approximate, expected in ((600, False, "10:00"), (1050, True, "≈17:30"),
                                         (7200, False, "120:00")):
        widget.render([p], {"Monitor1": Countdown("Monitor1", delay, known=True, approximate=approximate)}, p)
        drawn.clear()
        with patch.object(QPainter, "drawText", record_draw):
            widget.grab()
        check(f"{mode} {size}: complete countdown {expected} reaches the painter", expected in drawn)
widget._set_option("mode", "compact")
widget._set_option("size", 144)
for fill in ("#FFFFFF", "#000000", "#4C8DFF", "#22C3B0"):
    widget._set_option("inner_color", fill)
    widget.render([p], {"Monitor1": Countdown("Monitor1", 600, running=300, known=True)}, p)
    painted.clear()
    with patch.object(drawing, "_text", record_text):
        image = snapshot()
    rects = [rect for value, rect in painted if value == "5:00"]
    foreground = set()
    if rects:
        rect = rects[0].toAlignedRect()
        foreground = {image.pixelColor(x, y).name() for x in range(rect.left(), rect.right())
                      for y in range(rect.top(), rect.bottom())}
    expected = "#000000" if fill != "#000000" else "#ffffff"
    check(f"timer text contrasts across empty and {fill} filled halves",
          expected in foreground and "#ffffff" in foreground)

widget._set_option("inner_color", "#22C3B0")
clock.paused = True
widget.render([p], {"Monitor1": clock}, p)
check("paused timer keeps its elapsed fill and countdown", widget.rows[0].timer_text == "7:30"
      and snapshot().pixelColor(60, 120).name() == "#7c879c")
clock.known = False
widget.render([p], {"Monitor1": clock}, p)
image = snapshot()
check("unknown timer leaves known playlist progress available", widget.rows[0].timer_text == "—:—"
      and image.pixelColor(25, 14).name() == "#4c8dff"
      and image.pixelColor(60, 120).name() == "#2b3345")
p.seen, p.percent = 20, 100
widget.render([p], {"Monitor1": clock}, p)
check("finished playlist uses a full successful fill", snapshot().pixelColor(
    100, 120).name() == "#3dd68c")

menu = widget.build_menu()
design_menu = next((a.menu() for a in menu.actions() if a.text() == "Design"), None)
check("context menu offers the design choice", design_menu is not None)
if design_menu:
    next(a for a in design_menu.actions() if a.data() == ("design", "rings")).trigger()
    check("context menu switches and persists rings", widget.options["design"] == "rings"
          and Settings.load().get("widget", "profiles", {})["default"]["design"] == "rings")
    check("ring semantics still show remaining fractions", widget.rows[0].playlist_fraction == 0)
    next(a for a in widget.build_menu().actions() if a.text() == "Design").menu().actions()[0].trigger()

widget._set_option("mode", "expanded")
with patch.object(drawing, "_text", record_text):
    painted.clear()
    widget.grab()
check("expanded rows keep the selected rectangles and metadata", {"100%", "—:—", "Space wallpaper"}.issubset(
    {value for value, _rect in painted}))
widget.close()
check("rectangle drawing keeps toolkit UI imports out of the tracker", "app.ui" not in sys.modules)
print(f"\n{sum(checks)}/{len(checks)} passed")
raise SystemExit(0 if all(checks) else 1)
