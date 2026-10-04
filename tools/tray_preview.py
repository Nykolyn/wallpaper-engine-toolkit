"""Draw the tray's icon, menu and balloon copy, to look at.

    .venv\\Scripts\\python.exe tools\\tray_preview.py --grab icons  out.png
    .venv\\Scripts\\python.exe tools\\tray_preview.py --grab menu   out.png [--state running]
    .venv\\Scripts\\python.exe tools\\tray_preview.py --grab notes  out.png

`icons` is the design's sheet (`Tray: fill icon`): the four states at 16, 24
and 32 px (and the three between) on a dark and a light taskbar, each
drawn at its true pixel size and then magnified four times without smoothing,
so what the shell would show is what you see. `menu` is the tray menu over a
dark desktop; `notes` the two balloons' copy over both. Everything is made up
(a 4 of 201 playlist on "Monitor1"), and nothing is read from the data folder.

Sets itself up to run offscreen, which has no fonts unless told where they are.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QPoint, QRect, Qt                    # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QPainter        # noqa: E402
from PySide6.QtWidgets import QApplication                       # noqa: E402

from app import theme                                            # noqa: E402
from app.tray_icon import (FINISHED, PAUSED, RUNNING, SIZES, UNKNOWN,   # noqa: E402
                           mark_pixmap)
from app.tray_menu import TrayMenu, build_model                  # noqa: E402
from app import tray_words as words                              # noqa: E402

TASKBARS = (("dark", "#202020", "#FFFFFF"), ("light", "#F3F3F3", "#1A1A1A"))
CASES = (("Running 20%", RUNNING, 0.2), ("Running 75%", RUNNING, 0.75),
         ("Paused", PAUSED, 0.62), ("Unknown", UNKNOWN, None), ("Finished", FINISHED, 1.0))
ZOOM = 4
GAP = 8


def _label(painter: QPainter, x: int, y: int, text: str, colour: str, px: int = 11) -> None:
    font = QFont("Segoe UI")
    font.setPixelSize(px)
    painter.setFont(font)
    painter.setPen(QColor(colour))
    painter.drawText(x, y, text)


def icons_sheet() -> QImage:
    cell = max(SIZES) * ZOOM + 12
    group = sum(size * ZOOM + GAP for size in SIZES) + 24
    width = 24 + len(CASES) * group
    rows = len(TASKBARS)
    sheet = QImage(width, rows * (cell + 60) + 20, QImage.Format_ARGB32)
    sheet.fill(QColor("#141821"))
    painter = QPainter(sheet)
    y = 14
    for name, ground, ink in TASKBARS:
        painter.fillRect(QRect(12, y, width - 24, cell + 44), QColor(ground))
        _label(painter, 24, y + 16, f"{name.upper()} TASKBAR  {ground}", ink)
        x = 24
        for title, state, fill in CASES:
            _label(painter, x, y + 34, title, ink)
            for size in SIZES:
                pixmap = mark_pixmap(state, size, fill=fill)
                image = pixmap.toImage().scaled(size * ZOOM, size * ZOOM,
                                                Qt.IgnoreAspectRatio, Qt.FastTransformation)
                painter.drawImage(x, y + 44 + (max(SIZES) * ZOOM - size * ZOOM) // 2, image)
                _label(painter, x, y + 44 + max(SIZES) * ZOOM + 12, str(size), ink, 9)
                x += size * ZOOM + GAP
            x += 24
        y += cell + 60
    painter.end()
    return sheet


def menu_sheet(state: str) -> QImage:
    # The fill is the time to the next change; 4 of 201 shown is 2 percent.
    fractions = {RUNNING: (0.62, 2), PAUSED: (0.62, 2), UNKNOWN: (None, 2), FINISHED: (1.0, 100)}
    fraction, number = fractions[state]
    line = {RUNNING: "tracking · 4 of 201 shown", PAUSED: "paused · 4 of 201 shown",
            UNKNOWN: "timer unknown · 4 of 201 shown",
            FINISHED: "finished · all 201 shown"}[state]
    menu = TrayMenu()
    menu.set_model(build_model(state, fraction, number, line, run_hint=words.run_hint(39),
                               review_hint=words.review_hint(12)))
    menu.popup(QPoint(40, 40))
    QApplication.processEvents()
    QApplication.processEvents()
    grabbed = menu.grab().toImage()
    sheet = QImage(grabbed.width() + 80, grabbed.height() + 80, QImage.Format_ARGB32)
    sheet.fill(QColor("#202020"))
    painter = QPainter(sheet)
    painter.drawImage(40, 40, grabbed)
    painter.end()
    menu.hide()
    return sheet


def notes_sheet() -> QImage:
    title_a, body_a = words.finished_balloon("Monitor1", 201, 1000)
    title_b, body_b = words.restarted_balloon("Monitor1", 1, 201)
    title_c, body_c = words.finished_balloon("Monitor1", 201)
    sheet = QImage(760, 330, QImage.Format_ARGB32)
    sheet.fill(QColor("#202020"))
    painter = QPainter(sheet)
    y = 30
    for title, body in ((title_a, body_a), (title_c, body_c), (title_b, body_b)):
        _label(painter, 24, y, title, "#FFFFFF", 14)
        font = QFont("Segoe UI")
        font.setPixelSize(12)
        painter.setFont(font)
        painter.setPen(QColor("#C8C8C8"))
        painter.drawText(QRect(24, y + 8, 710, 60), Qt.TextWordWrap, body)
        y += 100
    painter.end()
    return sheet


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--grab", choices=("icons", "menu", "notes"), required=True)
    parser.add_argument("out")
    parser.add_argument("--state", choices=(RUNNING, PAUSED, UNKNOWN, FINISHED), default=RUNNING)
    args = parser.parse_args(argv)

    app = QApplication(sys.argv[:1])
    theme.apply(app, styled=False)
    image = {"icons": icons_sheet, "notes": notes_sheet,
             "menu": lambda: menu_sheet(args.state)}[args.grab]()
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    image.save(args.out)
    print(f"wrote {args.out}  ({image.width()}x{image.height()})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
