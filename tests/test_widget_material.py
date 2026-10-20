"""Fixed glass appearance, native activation, and precise transparent silhouettes."""
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["WALLPAPER_TOOLKIT_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor
from app import desktop_widget as drawing, theme
from app.settings import Settings
from app.engines.wallpaper_timer import Countdown

app = QApplication.instance() or QApplication([])
theme.apply(app, styled=False)
widget = drawing.DesktopWidget(Settings())
progress = SimpleNamespace(monitor="Monitor1", playlist="custom", seen=12, total=197,
                           percent=6, current="", current_title="Wallpaper")
widget.render([progress], {"Monitor1": Countdown("Monitor1", 600, running=455, known=True)}, progress)
checks = []
def check(label, value):
    checks.append(bool(value))
    print(("PASS " if value else "FAIL ") + label)

for design in ("rectangles", "rings"):
    widget._set_option("design", design)
    before = widget.grab().toImage()
    widget._set_option("outer_color", "#FF0000")
    widget._set_option("inner_color", "#00FF00")
    widget._set_option("opacity", 40)
    check(design + " ignores obsolete custom colors and whole-window opacity",
          before == widget.grab().toImage() and widget.windowOpacity() == 1)
    labels = {action.text() for action in widget.build_menu().actions()}
    check("appearance customization is absent from menu", not labels & {"Opacity", "Rectangle colours", "Ring colours"})

# Mock only the compositor boundary; inspect real Qt painting and clipping.
with patch.object(drawing, "apply_backdrop", return_value=True, create=True) as native:
    widget.show()
    app.processEvents()
    for mode, design in (("compact", "rectangles"), ("compact", "rings"), ("expanded", "rings")):
        widget._set_option("mode", mode)
        widget._set_option("design", design)
        app.processEvents()
        image = widget.grab().toImage()
        point = QPoint(35, 50) if mode == "compact" else QPoint(190, 80)
        check(mode + " " + design + " native material transmits the blurred backdrop",
              0 < image.pixelColor(point).alpha() < 145)
        check(mode + " " + design + " native region excludes transparent corners",
              not widget.mask().isEmpty() and not widget.mask().contains(QPoint(0, 0)))
        if mode == "compact" and design == "rectangles":
            check("overlapping lenses keep their shared center visible",
                  widget.mask().contains(QPoint(69, 78)))
    check("show activates the native material", native.called)
    calls = native.call_count
    widget.render(*widget._readings)
    check("countdown refresh does not reapply the compositor", native.call_count == calls)
    widget.render([progress, SimpleNamespace(**{**vars(progress), "monitor": "Monitor2"})], {}, progress)
    check("new rows inherit the active material", all(row.native_glass for row in widget.rows))
    header = widget.grab().toImage().pixelColor(140, 22)
    background = theme.composite(header, QColor(Qt.white))
    check("native panel header title, buttons and icons remain readable over white",
          all(theme.contrast(theme.color(token), background) >= 4.5
              for token in ("text.body", "text.mid")))
with patch.object(drawing, "apply_backdrop", return_value=False, create=True):
    widget._update_backdrop(force=True)
    image = widget.grab().toImage()
    check("rejected native effect clears clipping and restores readable fallback",
          widget.mask().isEmpty() and image.pixelColor(190, 80).alpha() >= 210)
widget.close()
print(f"{sum(checks)}/{len(checks)} passed")
raise SystemExit(0 if all(checks) else 1)
