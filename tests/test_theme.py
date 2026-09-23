"""The theme: tokens, type, shadows and the generated stylesheet.

Run it directly (needs Qt, but no Wallpaper Engine and no windows on screen):

    .venv\\Scripts\\python.exe tests\\test_theme.py

A token misspelt in the stylesheet does not raise anything — Qt drops the rule
and a control quietly loses its colour. So every value is parsed here, the
stylesheet is checked for anything left unfilled, and the controls whose looks
depend on drawn images (the check box's tick) are painted and looked at.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QRectF, Qt                               # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QPainter             # noqa: E402
from PySide6.QtWidgets import (                                      # noqa: E402
    QApplication, QCheckBox, QTreeWidget, QTreeWidgetItem, QWidget)

app = QApplication(sys.argv)

from app import animations, theme                                     # noqa: E402

results: list[bool] = []


def check(label: str, condition: bool) -> None:
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label)


def raises(fn, error=KeyError) -> bool:
    try:
        fn()
    except error:
        return True
    return False


def close(a: QColor, b: QColor, within: int = 3) -> bool:
    return all(abs(x - y) <= within for x, y in
               ((a.red(), b.red()), (a.green(), b.green()), (a.blue(), b.blue())))


# ---- every token is a colour ----------------------------------------------------

bad = []
for name, value in theme.TOKENS.items():
    try:
        colour = theme.parse(value)
        if not colour.isValid():
            bad.append(name)
    except ValueError:
        bad.append(name)
check(f"all {len(theme.TOKENS)} colour tokens parse", not bad)
bad = [name for name, stops in theme.GRADIENTS.items()
       if not all(theme.parse(value).isValid() for _, value in stops)]
check("and every gradient stop does", not bad)

well = theme.parse("rgba(8,10,14,.5)")
check("rgba() with a fractional alpha keeps the colour and the alpha",
      (well.red(), well.green(), well.blue()) == (8, 10, 14) and abs(well.alphaF() - 0.5) < 0.01)
check("a hex colour is opaque", theme.color("accent").alpha() == 255)
check("a misspelt token raises instead of painting black",
      raises(lambda: theme.color("text.low")))
check("a colour handed out is a copy, not the cached one",
      theme.color("accent") is not theme.color("accent"))
mutated = theme.color("accent")
mutated.setAlpha(0)
check("so changing it changes nothing for the next caller", theme.color("accent").alpha() == 255)

round_trip = [name for name in theme.TOKENS
              if QColor(theme.css(name)).rgba() != theme.color(name).rgba()]
check("css() text reads back as the same colour in QColor", not round_trip)
check("translucent tokens are written #AARRGGBB, which QSS and QColor share",
      theme.css("surface.raised") == "#1FFFFFFF")
check("and css() can scale a token's opacity",
      abs(QColor(theme.css("accent", 0.3)).alphaF() - 0.3) < 0.01)


# ---- legibility on glass ------------------------------------------------------------

ground = theme.panel_ground()
check(f"the panel ground is the design's #262C3A, near enough (got {ground.name()})",
      close(ground, QColor("#262C3A"), within=3))
ratio = theme.contrast(theme.color("text.lo"), ground)
check(f"text.lo on the panel ground is at least 4.5:1 (got {ratio:.2f})", ratio >= 4.5)
for token in ("text.hi", "text.body", "text.mid"):
    check(f"{token} on the panel ground clears it too",
          theme.contrast(theme.color(token), ground) >= 4.5)
check("text.onAccent on the accent fill is legible",
      theme.contrast(theme.color("text.onAccent"), theme.color("accent")) >= 4.5)


# ---- type ---------------------------------------------------------------------------

check("fractional sizes survive: type.bodySm is 11.5 px = 8.625 pt",
      theme.font("type.bodySm").pointSizeF() == 8.625)
check("type.display is 38 px = 28.5 pt", theme.font("type.display").pointSizeF() == 28.5)
check("every type token builds a font",
      all(theme.font(t).pointSizeF() == theme.TYPE[t].px * 0.75 for t in theme.TYPE))
check("numbers are mono, Consolas first",
      theme.font("type.numeric").families()[0] == "Consolas")
check("prose is Segoe UI Variable Text, falling back to Segoe UI",
      theme.font("type.body").families()[0] == "Segoe UI Variable Text"
      and theme.font("type.body").families()[-1] == "Segoe UI")
check("a card title is semibold", theme.font("type.h3").weight() == QFont.DemiBold)
overline = theme.font("type.overline")
check("an overline is upper case and tracked out",
      overline.capitalization() == QFont.AllUppercase and overline.letterSpacing() > 0)
check("line heights come with the token", theme.line_height("type.mono") == 11 * 1.85)
check("an unknown type token raises", raises(lambda: theme.font("type.huge")))
check("the stylesheet form writes pt, not px",
      "font-size: 8.625pt" in theme.qss_font("type.bodySm"))


# ---- the stylesheet -----------------------------------------------------------------

qss = theme.stylesheet()
check("the stylesheet has no unfilled placeholders", not theme.unresolved(qss))
check("the unresolved-placeholder check itself finds one",
      theme.unresolved("a { color: $(text.nope); }") == ["text.nope"])
images = [line.split('url("', 1)[1].split('")', 1)[0] for line in qss.splitlines()
          if 'url("' in line]
check("the stylesheet draws the tick and the arrows from files", len(images) >= 6)
check("and every one of them is there, as SVG",
      all(Path(p).is_file() and Path(p).read_text(encoding="utf-8").startswith("<svg")
          for p in images))
check("the light palette is gone, and so is the old token dict",
      not hasattr(theme, "LIGHT") and not hasattr(theme, "C"))


# ---- applying it --------------------------------------------------------------------

real_reduced = animations.reduced_motion
try:
    animations.reduced_motion = lambda: True
    theme.apply(app)
    check("with Windows animations off, the app's motion is off",
          animations.ENABLED is False)
    animations.reduced_motion = lambda: False
    theme.apply(app)
    check("and on again when they are on", animations.ENABLED is True)
finally:
    animations.reduced_motion = real_reduced

app.setStyleSheet("")      # the stylesheet style wraps Fusion and hides its name
check("apply() uses Fusion", app.style().name().lower() == "fusion")
theme.apply(app)
check("the window ground is bg.solid", app.palette().window().color() == theme.color("bg.solid"))
check("the app font is type.body", app.font().pointSizeF() == 9.375)
check("the stylesheet is set", app.styleSheet() == theme.stylesheet())
check("reduced_motion() answers without raising", isinstance(animations.reduced_motion(), bool))

box = QCheckBox("Verify after move")
box.setChecked(True)
box.resize(200, 24)
box.show()
app.processEvents()
tick = box.grab().toImage().convertToFormat(QImage.Format_ARGB32)
# Offscreen, the pointer rests on the box, so it may be drawn hovered.
accent, hover = theme.color("accent"), theme.color("accent.hover")
ink = theme.color("text.onAccent")
area = [tick.pixelColor(x, y) for x in range(0, 24) for y in range(0, 24)]
filled = sum(close(c, accent, 8) or close(c, hover, 8) for c in area)
check(f"a checked box is filled with the accent, 17 px square ({filled} px)",
      200 <= filled <= 17 * 17)
check("and carries a dark tick on it", sum(close(c, ink, 40) for c in area) > 6)
box.setChecked(False)
app.processEvents()
empty = box.grab().toImage().convertToFormat(QImage.Format_ARGB32)
check("an unchecked box has no accent fill",
      not any(close(empty.pixelColor(x, y), accent, 8) or close(empty.pixelColor(x, y), hover, 8)
              for x in range(24) for y in range(24)))


tree = QTreeWidget()
tree.setHeaderHidden(True)
tree.setGeometry(400, 400, 240, 80)     # away from the pointer, so nothing is hovered
for state in (Qt.Unchecked, Qt.Checked):
    row = QTreeWidgetItem([""])           # no text: only the box can show
    row.setCheckState(0, state)
    tree.addTopLevelItem(row)
tree.show()
app.processEvents()
tree.setCurrentItem(None)
app.processEvents()
# Rendered over the dialog's own ground: the list is translucent, and its box
# is only visible against what is under it.
rows = QImage(tree.viewport().size(), QImage.Format_ARGB32)
rows.fill(theme.color("bg.solid"))
tree.viewport().render(rows)
first = tree.visualItemRect(tree.topLevelItem(0))
second = tree.visualItemRect(tree.topLevelItem(1))
ground = rows.pixelColor(first.right() - 4, first.center().y())


def box_pixels(rect) -> list[QColor]:
    return [rows.pixelColor(x, y) for x in range(rect.left(), rect.left() + 40)
            for y in range(rect.top(), rect.bottom() + 1)]


check("an unticked row in a list still shows its box",
      sum(c.lightness() - ground.lightness() > 15 for c in box_pixels(first)) > 10)
check("and a ticked one is the accent box",
      sum(close(c, accent, 8) or close(c, hover, 8) for c in box_pixels(second)) > 40)


# ---- background and shadows -----------------------------------------------------------

canvas = QImage(1280, 860, QImage.Format_ARGB32)
canvas.fill(Qt.black)
p = QPainter(canvas)
theme.paint_app_background(p, canvas.rect())
p.end()
glow = canvas.pixelColor(int(1280 * 0.16), 0)
corner = canvas.pixelColor(1279, 859)
check(f"the app ground is brightest near its centre above the top-left ({glow.name()})",
      glow.lightness() > corner.lightness())
check(f"and reaches the outer stop in the far corner ({corner.name()})",
      close(corner, QColor("#141821"), within=4))

for elev in ("elev.1", "elev.2", "elev.3"):
    pix = theme.shadow(elev)
    e = theme.ELEVATION[elev]
    m = theme._margin(e)
    image = pix.toImage()
    middle = image.pixelColor(pix.width() // 2, pix.height() // 2)
    check(f"{elev}: a nine-slice source 4m+1 px square", pix.width() == 4 * m + 1)
    check(f"{elev}: full strength in the middle", abs(middle.alphaF() - e.alpha) < 0.02)
    check(f"{elev}: gone at the edge", image.pixelColor(0, 0).alpha() == 0)
check("a shadow is made once and kept", theme.shadow("elev.2") is theme.shadow("elev.2"))
check("the inset has no outer shadow to cut", raises(lambda: theme.shadow("elev.inset"), ValueError))
check("an unknown elevation raises", raises(lambda: theme.shadow("elev.9")))

canvas = QImage(300, 300, QImage.Format_ARGB32_Premultiplied)
canvas.fill(Qt.transparent)
p = QPainter(canvas)
box_rect = QRectF(80, 60, 140, 100)
theme.paint_shadow(p, box_rect, "elev.2")
p.end()
check("a panel's shadow is not painted under the panel, which is glass",
      canvas.pixelColor(150, 110).alpha() == 0)
check("it falls below the panel", canvas.pixelColor(150, 172).alpha() > 40)
check("and fades out further down", canvas.pixelColor(150, 172).alpha()
      > canvas.pixelColor(150, 215).alpha())

canvas.fill(Qt.transparent)
p = QPainter(canvas)
theme.paint_shadow(p, box_rect, "elev.inset", theme.R_MD)
p.end()
check("an inset shadow darkens the top inside edge of a well",
      canvas.pixelColor(150, 61).alpha() > canvas.pixelColor(150, 70).alpha() >= 0)
check("and stays inside it", canvas.pixelColor(150, 50).alpha() == 0)


# ---- the helpers the old tabs still call ----------------------------------------------

check("status colours map onto the tokens",
      theme.status_color("bad") == theme.css("danger")
      and theme.status_color("done") == theme.css("accent"))
check("log levels map onto the console tokens",
      theme.level_color("ERROR") == theme.css("console.err")
      and theme.level_color("WARN") == theme.css("console.warn"))
check("a secondary line is text.mid, a faint one text.lo",
      theme.css("text.mid") in theme.label_style("muted")
      and theme.css("text.lo") in theme.label_style("faint"))
check("label sizes are still given in px and written in pt",
      "font-size: 9.75pt" in theme.label_style("text", size=13))
check("the console is the console surface in mono",
      theme.css("surface.console") in theme.console_style()
      and "Consolas" in theme.console_style())
button = QWidget()
theme.make_accent(button)
check("make_accent marks the button for the accent rule", button.property("accent") is True)


print()
print("PASSED %d/%d" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
