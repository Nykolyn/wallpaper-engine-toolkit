"""Fixed glass appearance and anti-aliased, shadowed silhouettes without a native backdrop."""
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
os.environ["WALLPAPER_TOOLKIT_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PySide6.QtWidgets import QApplication
from app import desktop_widget as drawing, theme
from app.settings import Settings
from app.engines.wallpaper_timer import Countdown

app = QApplication.instance() or QApplication([])
theme.apply(app, styled=False)
widget = drawing.DesktopWidget(Settings())
widget.setGeometry(400, 400, 144, 144)
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

check("the native backdrop module is gone", not hasattr(drawing, "apply_backdrop"))
widget.show()
app.processEvents()
for mode, design in (("compact", "rectangles"), ("compact", "rings"), ("expanded", "rectangles")):
    widget._set_option("mode", mode)
    widget._set_option("design", design)
    app.processEvents()
    image = widget.grab().toImage()
    # A 1-bit window mask is what made a rectangle of blur and stepped corners.
    check(f"{mode} {design}: the window is never masked", widget.mask().isEmpty())
    check(f"{mode} {design}: outer corner is fully transparent", image.pixelColor(0, 0).alpha() == 0)
    # The panel's straight side is pixel-aligned; its rounded corner shows the smoothing.
    y = image.height() // 2 if mode == "compact" else 8
    row = [image.pixelColor(x, y).alpha() for x in range(image.width() // 2)]
    first = next((alpha for alpha in row if alpha), 0)
    check(f"{mode} {design}: the left silhouette edge is anti-aliased",
          0 < first < max(row) * .75 and max(row) >= 150)

widget._set_option("mode", "compact")
widget._set_option("design", "rectangles")
image = widget.grab().toImage()
_back, front = drawing._rectangle_rects(144)
below = [image.pixelColor(round(front.center().x()), round(front.bottom()) + offset) for offset in (2, 3, 4)]
check("the front lens casts a soft dark shadow below it",
      all(0 < colour.alpha() < 110 for colour in below) and all(colour.lightness() < 40 for colour in below))
check("the shadow fades away from the lens", below[0].alpha() > below[-1].alpha())
widget.close()
print(f"{sum(checks)}/{len(checks)} passed")
raise SystemExit(0 if all(checks) else 1)
