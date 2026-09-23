"""The icon set: every glyph draws, in the colour asked, at the size asked.

Run it directly (needs Qt, but no windows on screen):

    .venv\\Scripts\\python.exe tests\\test_icons.py

An SVG that fails to parse renders as nothing at all — no error, just a button
without its glyph. So each icon is rendered and its pixels are counted.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtGui import QColor, QIcon, QImage                     # noqa: E402
from PySide6.QtWidgets import QApplication                          # noqa: E402

app = QApplication(sys.argv)

from app import theme                                               # noqa: E402
from app.ui.kit import ICON_NAMES, icon, icons, pixmap, svg         # noqa: E402

results: list[bool] = []


def check(label: str, condition: bool) -> None:
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label)


def raises(fn) -> bool:
    try:
        fn()
    except KeyError:
        return True
    return False


def inked(image: QImage) -> list[QColor]:
    image = image.convertToFormat(QImage.Format_ARGB32)
    return [image.pixelColor(x, y) for x in range(image.width()) for y in range(image.height())
            if image.pixelColor(x, y).alpha() > 0]


DESIGN_SET = ("overview rotator tracker review creator copier settings folder search "
              "refresh play pause stop check close chevD chevR warn info ext trash lock "
              "monitor clock dots drag").split()
FROM_SCREENS = ("plus", "clipboard", "tag", "video", "folderPlus", "sortUp")

check("the design system's 26 icons are all there", all(n in ICON_NAMES for n in DESIGN_SET)
      and len(DESIGN_SET) == 26)
check("and the glyphs the screens draw outside the set",
      all(n in ICON_NAMES for n in FROM_SCREENS))
check("no two names", len(set(ICON_NAMES)) == len(ICON_NAMES))

accent = theme.color("accent")
blank, off_colour = [], []
for name in ICON_NAMES:
    for size in (16, 24):
        pix = pixmap(name, "accent", size, dpr=1.0)
        ink = inked(pix.toImage())
        if pix.width() != size or pix.height() != size or len(ink) < size // 2:
            blank.append(f"{name}@{size}")
            continue
        # The most solid pixels are the stroke itself, in exactly the colour. (A
        # 1.3 px line between two pixel rows never reaches full opacity.)
        strongest = max(c.alpha() for c in ink)
        solid = [c for c in ink if c.alpha() >= 0.8 * strongest]
        if not solid or any(abs(c.red() - accent.red()) > 2 or abs(c.green() - accent.green()) > 2
                            or abs(c.blue() - accent.blue()) > 2 for c in solid):
            off_colour.append(f"{name}@{size}")
check(f"every icon draws something at 16 and 24 px {blank or ''}", not blank)
check(f"in the colour it was asked for {off_colour or ''}", not off_colour)

hi = pixmap("check", "accent", 16, dpr=1.5)
check("at 150 % a 16 px icon is 24 device px, still 16 px on screen",
      hi.width() == 24 and hi.deviceIndependentSize().width() == 16)
check("a pixmap is drawn once and then reused",
      pixmap("folder", "text.mid", 16, 1.0) is pixmap("folder", "text.mid", 16, 1.0))
check("a colour can be a QColor as well as a token",
      pixmap("folder", QColor("#ff0000"), 16, 1.0).toImage().isNull() is False)

faint = inked(pixmap("stop", QColor(255, 255, 255, 102), 16, 1.0).toImage())
full = inked(pixmap("stop", QColor(255, 255, 255), 16, 1.0).toImage())
check("a translucent colour draws a fainter glyph",
      max(c.alpha() for c in faint) < max(c.alpha() for c in full))

glyph = icon("play", "text.onAccent")
check("icon() gives a QIcon with a pixmap", not glyph.isNull()
      and not glyph.pixmap(16, 16).isNull())
normal = inked(glyph.pixmap(16, 16, QIcon.Normal).toImage())
disabled = inked(glyph.pixmap(16, 16, QIcon.Disabled).toImage())
check("its disabled state is the same glyph at 40 %",
      disabled and max(c.alpha() for c in disabled) < 0.5 * max(c.alpha() for c in normal))
check("an icon is made once per name, colour and size", icon("play", "text.onAccent") is glyph)

drawing = svg("check", "text.onAccent", stroke=2.4)
check("the SVG text names a real colour, not currentColor", "currentColor" not in drawing
      and theme.color("text.onAccent").name().lower() in drawing.lower())
check("and can carry a heavier stroke for small sizes", 'stroke-width="2.4"' in drawing)
check("every icon is on the 16 px grid",
      all('viewBox="0 0 16 16"' in svg(n) for n in ICON_NAMES))

check("an unknown name raises from svg()", raises(lambda: svg("sparkles")))
check("from pixmap()", raises(lambda: pixmap("sparkles")))
check("and from icon()", raises(lambda: icon("sparkles")))
check("so does an unknown colour token", raises(lambda: pixmap("check", "text.nope")))
check("the module list and the kit's export agree", tuple(icons.NAMES) == tuple(ICON_NAMES))


print()
print("PASSED %d/%d" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
