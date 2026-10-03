"""Draw the application icon: the Branding page's mark.

Kept as a script rather than a checked-in binary alone, so the mark can be
retuned without a paint program:

    .venv\\Scripts\\python.exe assets\\make_icon.py

Writes assets/icon.ico (every size Windows asks for), assets/icon.png (1024 px)
and assets/icon_preview.png (a contact sheet of the real sizes, to check the
small ones by eye).

The mark is the design's (`Branding`): two overlapping rounded rectangles, a
grey outline behind and an accent fill in front of it, on a dark rounded tile
that fades from `#34425F` to `#151A24`. It is drawn here with QPainter from the
same numbers as the title bar's `BrandMark` (`app/ui/kit/shell.py`), on the
design's 64-unit grid, and *each size is drawn on its own* rather than the big
one shrunk: at 16 px the outline is one pixel wide and a shrunk 1024 would
blur it. The tile's faint inner edge is only drawn from 64 px up, as in the
design, where the small sizes leave it out.

The colours come from `app/theme.py`, so the icon cannot drift from the app.
There is no PIL and no SVG library in it; the .ico is written by hand (a
header, a directory, and one PNG per size, which Windows has read since Vista).
"""
from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(OUT.parent))

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt    # noqa: E402
from PySide6.QtGui import (                                              # noqa: E402
    QBrush, QColor, QGuiApplication, QImage, QPainter, QPen)

from app import theme                                                    # noqa: E402

MASTER = 1024
# 20 and 40 are the 125 % and 250 % taskbar sizes.
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
SHEET_SIZES = (16, 24, 32, 48, 64, 128, 256)
EDGE_FROM = 64                   # the tile's inner edge appears from this size

# On the design's 64-unit grid (x, y, width, height, radius[, stroke]).
TILE = (2, 2, 60, 60, 14)
EDGE = (2.5, 2.5, 59, 59, 13.5, 1)
OUTLINE = (13, 15, 25, 20, 4, 4)
FILL = (24, 27, 27, 22, 4, 3)


def draw(size: int) -> QImage:
    """The mark at one size, `size` × `size` pixels with a clear background."""
    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    k = size / 64

    def box(spec) -> QRectF:
        x, y, w, h = spec[:4]
        return QRectF(x * k, y * k, w * k, h * k)

    tile = box(TILE)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(theme.gradient("brand.tile", tile)))
    painter.drawRoundedRect(tile, TILE[4] * k, TILE[4] * k)
    if size >= EDGE_FROM:
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(theme.color("sheen"), EDGE[5] * k))
        painter.drawRoundedRect(box(EDGE), EDGE[4] * k, EDGE[4] * k)
    painter.setBrush(Qt.NoBrush)
    painter.setPen(QPen(theme.color("text.lo"), OUTLINE[5] * k))
    painter.drawRoundedRect(box(OUTLINE), OUTLINE[4] * k, OUTLINE[4] * k)
    painter.setBrush(theme.color("accent"))
    painter.setPen(QPen(theme.color("brand.edge"), FILL[5] * k))
    painter.drawRoundedRect(box(FILL), FILL[4] * k, FILL[4] * k)
    painter.end()
    return image


def png_bytes(image: QImage) -> bytes:
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, "PNG")
    buffer.close()
    return bytes(data)


def ico_bytes(sizes=ICO_SIZES) -> bytes:
    """A multi-size .ico: ICONDIR, one ICONDIRENTRY per size, then the PNGs."""
    pictures = [(size, png_bytes(draw(size))) for size in sizes]
    header = struct.pack("<HHH", 0, 1, len(pictures))
    offset = len(header) + 16 * len(pictures)
    directory, body = b"", b""
    for size, data in pictures:
        side = 0 if size >= 256 else size           # 0 means 256
        directory += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(data), offset)
        body += data
        offset += len(data)
    return header + directory + body


def read_ico_sizes(data: bytes) -> list[int]:
    """The sizes an .ico holds, to check what was written."""
    reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    if (reserved, kind) != (0, 1):
        raise ValueError("not an icon file")
    return [struct.unpack_from("<BB", data, 6 + 16 * n)[0] or 256 for n in range(count)]


def sheet() -> QImage:
    ground = QColor("#16181D")
    width = sum(SHEET_SIZES) + 20 * (len(SHEET_SIZES) + 1)
    image = QImage(width, 280, QImage.Format_ARGB32)
    image.fill(ground)
    painter = QPainter(image)
    x = 20
    for size in SHEET_SIZES:
        painter.drawImage(x, 140 - size // 2, draw(size))
        x += size + 20
    painter.end()
    return image


def main() -> None:
    QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    draw(MASTER).save(str(OUT / "icon.png"))
    (OUT / "icon.ico").write_bytes(ico_bytes())
    sheet().save(str(OUT / "icon_preview.png"))
    print(f"wrote {OUT / 'icon.ico'}, {OUT / 'icon.png'}, {OUT / 'icon_preview.png'}")


if __name__ == "__main__":
    main()
