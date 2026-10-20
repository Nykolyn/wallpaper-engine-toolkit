"""Glass material, transparent silhouettes and readable expanded widget content."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
os.environ["WALLPAPER_TOOLKIT_DATA"] = tempfile.mkdtemp(prefix="widget_glass_")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
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
progress = SimpleNamespace(monitor="Monitor1", playlist="custom", seen=12, total=197, percent=6,
                           current="X:/wallpaper.mp4", current_title="A long wallpaper title")
clock = Countdown("Monitor1", 600, running=455, known=True)
widget.render([progress], {"Monitor1": clock}, progress,
              metadata={progress.current: {"title": "A long wallpaper title", "size": 1572864}})

for design in ("rectangles", "rings"):
    widget._set_option("design", design)
    for edge in (112, 144, 176):
        widget._set_option("size", edge)
        image = widget.grab().toImage()
        points = ((.20, .35),) if design == "rectangles" else ((.3, .3), (.32, .32), (.3, .7), (.68, .7))
        faces = [image.pixelColor(round(edge * x), round(edge * y)) for x, y in points]
        check(f"{design} {edge}: glass transmits the desktop without becoming unreadably clear",
              any(160 <= colour.alpha() < 220 for colour in faces))
        check(f"{design} {edge}: outer corner is transparent", image.pixelColor(0, 0).alpha() == 0)
    widget._set_option("mode", "expanded")
    image = widget.grab().toImage()
    check(f"{design}: expanded panel keeps the glass material",
          210 <= image.pixelColor(widget.width() // 2, 4).alpha() < 240)
    check(f"{design}: rounded glass panel has transparent corners", image.pixelColor(4, 4).alpha() == 0)
    widget._set_option("mode", "compact")

# Rendering the real window over white catches washed-out glass text, including
# custom fills, rather than checking the configured foreground in isolation.
for design, paused in (("rectangles", False), ("rectangles", True), ("rings", False), ("rings", True)):
    widget._set_option("design", design)
    widget._set_option("mode", "expanded")
    clock.paused = paused
    widget.render([progress], {"Monitor1": clock}, progress,
                  metadata={progress.current: {"title": "A long wallpaper title", "size": 1572864}})
    status = "Paused" if paused else "Tracking"
    painted = []
    draw_text = drawing._text

    def record_text(painter, rect, value, token, colour="text.mid", *args, **kwargs):
        painted.append((value, rect, theme.color(colour) if isinstance(colour, str) else colour))
        return draw_text(painter, rect, value, token, colour, *args, **kwargs)

    with patch.object(drawing, "_text", record_text):
        image = widget.grab().toImage()
    check(f"{design} {status}: expanded preserves monitor, state, playlist and wallpaper metadata",
          {"Monitor1", status, "custom", "A long wallpaper title", "1.5 MB", "2:25"}.issubset(
              {value for value, _rect, _colour in painted}))
    check(f"{design}: all labels fit inside the expanded row",
          all(widget.rows[0].rect().contains(rect.toAlignedRect()) for _value, rect, _colour in painted))
    white = QImage(image.size(), QImage.Format_ARGB32_Premultiplied)
    white.fill(Qt.white)
    painter = QPainter(white)
    painter.drawImage(0, 0, image)
    painter.end()
    # The right gutter has the same material as the metadata line, without text.
    background = white.pixelColor(widget.width() - 8, drawing.HEADER_HEIGHT + 65)
    foregrounds = [colour for value, _rect, colour in painted
                   if value in (status, "custom", "A long wallpaper title", "1.5 MB")]
    check(f"{design} {status}: metadata and state stay readable over a white desktop",
          len(foregrounds) == 4 and all(theme.contrast(colour, background) >= 4.5 for colour in foregrounds))

widget.close()
check("glass rendering stays lightweight in the tracker", "app.ui" not in sys.modules)
print(f"\n{sum(checks)}/{len(checks)} passed")
raise SystemExit(0 if all(checks) else 1)
