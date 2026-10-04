"""Rows drawn once into tiles: a tile is copied while the row looks the same,
drawn again the moment anything that decides its look changes, and copied to
exactly the pixels drawing the row directly gives. Also the two grounds behind
a scrolled table, the window's gradient and a glass panel, drawn once per size,
and the shadows the kit skips when what repaints is inside the box casting them.

Run it directly (needs Qt, but no windows on screen):

    .venv\\Scripts\\python.exe tests\\test_row_tiles.py

tests/perf_pages.py measures what this buys; this checks it never shows a stale row.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(errors="replace")

from PySide6.QtCore import QModelIndex, QRect, Qt                        # noqa: E402
from PySide6.QtGui import QImage, QPainter, QPixmap, QStandardItem, QStandardItemModel  # noqa: E402
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget         # noqa: E402

app = QApplication(sys.argv)

from app import theme                                                    # noqa: E402

theme.apply(app)

from app.ui.kit import (                                                 # noqa: E402
    ButtonsCell, Cell, CellButton, ChipCell, Column, GlassPanel, Table, TableModel,
)
from app.ui.kit import base                                              # noqa: E402
from app.ui.kit.tables import (                                          # noqa: E402
    LIST_ROW_ROLE, ListRow, RowList, RowTiles, BusyCell,
)

results: list[bool] = []


def check(label: str, condition: bool) -> None:
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label)


def grab(widget) -> QImage:
    return widget.grab().toImage().convertToFormat(QImage.Format_ARGB32)


print("-- the cache --")
tiles = RowTiles(budget=3 * 100 * 10 * 4)          # three 100 x 10 tiles at 1x
drawn: list[str] = []


def draw(name):
    return lambda painter: drawn.append(name)


first = tiles.get("a", 100, 10, 1.0, draw("a"))
check("a tile is drawn once and handed back after",
      tiles.get("a", 100, 10, 1.0, draw("a")) is first and drawn == ["a"] and tiles.drawn == 1)
check("it is as big as asked, at the pixel ratio asked",
      (first.width(), first.height(), first.devicePixelRatio()) == (100, 10, 1.0)
      and tiles.get("hi", 100, 10, 2.0, draw("hi")).width() == 200)
tiles.clear()
for name in "abc":
    tiles.get(name, 100, 10, 1.0, draw(name))
tiles.get("a", 100, 10, 1.0, draw("a"))            # used again: the newest
tiles.get("d", 100, 10, 1.0, draw("d"))
check("past its budget the tile used longest ago goes, not the one just used",
      len(tiles) == 3 and drawn[-1] == "d" and tiles.get("a", 100, 10, 1.0, draw("a"))
      and drawn[-1] == "d" and (tiles.get("b", 100, 10, 1.0, draw("b")), drawn[-1])[1] == "b")


print("-- a table's rows --")
COLUMNS = [Column("Wallpaper", None), Column("Author", 90), Column("State", 80, "right"),
           Column("", 70, "right", sortable=False)]


class Rows(TableModel):
    def cell(self, item, column):
        if column == 3:
            return ButtonsCell((CellButton("send", "copier", "Send to Copier"),
                                CellButton("lock", "lock", "Mark [protected]")))
        if column == 2 and item.get("busy"):
            return BusyCell("Working", "accent")
        return (Cell(item["title"]), item["author"],
                ChipCell(item.get("state", "Queued")), None)[column]


items = [{"title": f"Wallpaper {i}", "author": ("Marlow", "Quill")[i % 2]} for i in range(30)]
model = Rows(COLUMNS, items)
host = QWidget()
host.resize(640, 420)
QVBoxLayout(host).addWidget(table := Table())
table.setModel(model)
host.show()
app.processEvents()
tiled = table._delegate.tiles
before = grab(table.viewport())
made = tiled.drawn
again = grab(table.viewport())
check("a repaint with nothing changed copies every row and draws none",
      tiled.drawn == made and again == before and made >= len(table.visible_rows()))

items[2] = {**items[2], "title": "Renamed"}
model.set_rows(items)
app.processEvents()
changed = grab(table.viewport())
check("a cell that changes draws its row again, that row alone",
      tiled.drawn == made + 1 and changed != before)

made = tiled.drawn
table._hover_to(4)
app.processEvents()
hovered = grab(table.viewport())
check("the row under the pointer is drawn in its hover look (its buttons too)",
      tiled.drawn == made + 1 and hovered != changed)
table._hover_to(-1)
app.processEvents()
check("and back, the row as it was comes from its tile", grab(table.viewport()) == changed)

made = tiled.drawn
table.selectRow(5)
app.processEvents()
check("selecting a row draws it selected", tiled.drawn > made and grab(table.viewport()) != changed)
table.clearSelection()

made = tiled.drawn
host.setEnabled(False)
app.processEvents()
disabled = grab(table.viewport())
check("a table switched off draws every row faded",
      tiled.drawn >= made + len(table.visible_rows()) and disabled != changed)
host.setEnabled(True)
app.processEvents()
check("and switched on, its rows come back from their tiles", grab(table.viewport()) == changed)

items[1] = {**items[1], "busy": True}
model.set_rows(items)
table.set_spinning([1])
app.processEvents()
made = tiled.drawn
table.viewport().repaint()
table.viewport().repaint()
check("a row with a spinner is drawn each time, never kept as a tile", tiled.drawn == made)
table.set_spinning([])

# The tile, copied, is the row as drawn straight onto the same ground.
row = 6
width, height = table.viewport().width(), table.rowHeight(row)
spans = table.column_spans()
delegate = table._delegate
direct = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
direct.fill(Qt.transparent)
painter = QPainter(direct)
delegate.begin(1.0)
values = tuple(model.cell(model.item_at(row), c) for c in range(len(COLUMNS)))
delegate._paint_whole(painter, QRect(0, 0, width, height), model, model.item_at(row), values,
                      spans, "surface.zebra" if model.zebra(row) else None, None, False, None)
painter.end()
copied = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
copied.fill(Qt.transparent)
painter = QPainter(copied)
delegate.begin(1.0)
delegate.paint_row_at(painter, model, 0, 0, width, height, row, selected=False, hovered=False,
                      focused=False, spans=spans, spans_key=(tuple(spans), tuple(COLUMNS)),
                      enabled=True)
painter.end()
check("a copied tile is pixel for pixel the row drawn directly", copied == direct)


print("-- a list's rows --")


class Authors(QStandardItemModel):
    """Read through `list_item`, as Review's author list is."""

    def __init__(self, rows):
        super().__init__()
        self.rows, self.asked = rows, 0
        for r in rows:
            item = QStandardItem(r.title)
            item.setData(r, LIST_ROW_ROLE)
            self.appendRow(item)

    def list_item(self, row):
        self.asked += 1
        return self.rows[row], None, None


people = [ListRow(f"Author {i}", f"{i} new") for i in range(40)]
authors = Authors(people)
listing = RowList()
listing.setModel(authors)
listing.resize(260, 300)
listing.show()
app.processEvents()
list_tiles = listing._delegate.tiles
first_look = grab(listing.viewport())
made = list_tiles.drawn
check("the list paints its rows in one pass, from the model's own list_item",
      authors.asked > 0 and made > 3 and grab(listing.viewport()) == first_look
      and list_tiles.drawn == made)
listing._hover_to(1)
app.processEvents()
check("the row under the pointer is drawn hovered",
      list_tiles.drawn == made + 1 and grab(listing.viewport()) != first_look)
listing._hover_to(-1)
listing.setCurrentIndex(authors.index(2, 0))
app.processEvents()
check("a chosen row is drawn selected", grab(listing.viewport()) != first_look)

plain = QStandardItemModel()
for r in people[:5]:
    item = QStandardItem(r.title)
    item.setData(r, LIST_ROW_ROLE)
    plain.appendRow(item)
other = RowList()
other.setModel(plain)
other.resize(260, 300)
other.show()
app.processEvents()
check("a model without list_item is read through its roles",
      other._delegate.tiles.drawn == 5 and grab(other.viewport()) != QImage())
check("and an empty list paints nothing", (other.setModel(QStandardItemModel()),
                                           other.viewport().repaint(), True)[2])


print("-- what is behind a table --")
panel = GlassPanel(padding="none")
panel.resize(300, 200)
panel.show()
app.processEvents()
ground = panel._ground()
check("a panel's glass is drawn once for its size", panel._ground() is ground)
panel.set_tone("warn")
check("and again in another tone", panel._ground() is not ground)
panel.resize(320, 200)
check("or at another size", panel._ground().width() == 320)


class Caster(QWidget):
    """A widget with an outside, as the kit's controls have."""

    def outside_margins(self):
        return base.elevation_margins("elev.2")


caster = Caster()
caster.resize(300, 200)
area = QRect(0, 0, 300, 200).marginsAdded(caster.outside_margins())
base._keep(caster, area)
check("a repaint well inside a caster's box leaves its outside alone",
      base._misses(caster, base._edges(QRect(0, 40, 300, 120))))
check("one far from it too", base._misses(caster, base._edges(QRect(900, 900, 10, 10))))
check("but not one reaching a corner, where a rounded box leaves room for its shadow",
      not base._misses(caster, base._edges(QRect(0, 0, 40, 40))))
check("nor one past its edge", not base._misses(caster, base._edges(QRect(-20, 50, 40, 40))))
check("and one not seen yet is always drawn", not base._misses(Caster(), base._edges(QRect(0, 0, 5, 5))))

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
