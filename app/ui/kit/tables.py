"""Tables and rows: Table, TableModel, RowDelegate, ListRow, RowList, Thumb.

A table here can hold the whole reserve, 33 000 rows on a machine whose
wallpapers live on a hard disk, so everything is built for that:

- **The model keeps indices, not rows.** `TableModel` holds the items the page
  gave it and a list of ints saying which item each row shows (or which group
  header). Sorting and filtering rebuild that list; no row becomes an object.
- **One delegate paints whole rows.** `Table` paints its own viewport: one
  Python call per visible row, not one per cell, and nothing at all for rows
  out of view. The ground (zebra, hover, selection), group headers, text,
  chips and thumbs are all drawn in that one call.
- **A row costs few calls into Qt.** PySide gives up the GIL on every call
  into Qt and takes it back after, so while another thread is busy in Python
  each call can wait. A thumb is drawn from a tile made once (well, picture,
  edge, rounded), chips from their cached pixmaps, text from elisions already
  worked out, grounds as plain fills: about fifteen calls a row, where
  drawing each part afresh took about sixty.
- **Hover is a row, tracked by hand.** Qt's own hover repaints a cell each
  time the pointer crosses a column; the table notes which row the pointer is
  on and repaints only the row it left and the row it entered.
- **Thumbnails load off the GUI thread, for the rows on screen only.** Once
  scrolling settles the table asks its `ThumbLoader` for the previews of the
  visible rows and drops what it had queued for the rest. A row whose
  preview is not in yet shows an empty well; one with no preview, the
  placeholder's cross.

The column spec (`Column`) says, per column: its title, a fixed content width
or a share of what is left, alignment, mono or sans, whether it sorts, and
optionally a thumb or a glyph before the text. The model's `cell()` returns a
string, a `Cell` (text in another tone) or a `ChipCell` for each one.

`ListRow` is the same row for list views (the author list, recent activity):
a dataclass of what a row says, `paint_list_row` to draw it in any state, and
`RowList`, a list view of them. `Thumb` is a preview on its own, for cards.
"""
from __future__ import annotations

import math
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Hashable, Iterable, Sequence

from PySide6.QtCore import (
    QAbstractTableModel, QEvent, QItemSelection, QItemSelectionModel, QModelIndex,
    QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal,
)
from PySide6.QtGui import QCursor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QListView, QSizePolicy, QStyle,
    QStyledItemDelegate, QTableView, QWidget,
)

from ... import animations, theme
from . import icons
from .base import label
from .chips import chip_pixmap, chip_size
from .thumbs import ThumbLoader, shared as shared_loader

ALIGNMENTS = {"left": Qt.AlignLeft, "right": Qt.AlignRight, "center": Qt.AlignHCenter}


# ---- Thumb -------------------------------------------------------------------------

THUMB_STATES = ("placeholder", "loading", "image")


def paint_thumb(painter: QPainter, rect: QRectF, pixmap: QPixmap | None, state: str,
                radius: float, *, shimmer: float | None = None) -> None:
    """A preview in its well: the picture fitted inside (letterboxed, never
    cropped, as the gallery draws it), or while it loads an empty well, or
    when there is none the placeholder's two diagonals. `shimmer` is a loading
    skeleton's opacity, for a Thumb that pulses while it waits."""
    rect = QRectF(rect)
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)
    painter.fillPath(path, theme.color("surface.well"))
    painter.setClipPath(path)
    if state == "image" and pixmap is not None and not pixmap.isNull():
        size = pixmap.deviceIndependentSize()
        scale = min(rect.width() / size.width(), rect.height() / size.height())
        w, h = size.width() * scale, size.height() * scale
        target = QRectF(rect.center().x() - w / 2, rect.center().y() - h / 2, w, h)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.drawPixmap(target, pixmap, QRectF(pixmap.rect()))
    elif state == "loading":
        if shimmer is not None:
            painter.fillPath(path, theme.color("surface.raised", shimmer))
    else:
        painter.setPen(QPen(theme.color("thumb.cross"), 1))
        painter.drawLine(rect.topLeft(), rect.bottomRight())
        painter.drawLine(rect.topRight(), rect.bottomLeft())
    painter.setClipping(False)
    painter.setPen(QPen(theme.color("border.hairline"), 1))
    painter.setBrush(Qt.NoBrush)
    painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius - 0.5, radius - 0.5)
    painter.restore()


_state_tiles: dict[tuple, QPixmap] = {}


def thumb_tile(pixmap: QPixmap | None, state: str, size: str, dpr: float) -> QPixmap:
    """A thumb drawn once into a pixmap of its own, for painting by copying:
    one call per row instead of the twenty that drawing it takes. The
    placeholder and loading tiles are shared; an image's is made per picture."""
    if state != "image":
        key = (state, size, round(dpr, 3))
        cached = _state_tiles.get(key)
        if cached is not None:
            return cached
    width, height, radius = thumb_box(size)
    tile = QPixmap(math.ceil(width * dpr), math.ceil(height * dpr))
    tile.setDevicePixelRatio(dpr)
    tile.fill(Qt.transparent)
    painter = QPainter(tile)
    paint_thumb(painter, QRectF(0, 0, width, height), pixmap, state, radius)
    painter.end()
    if state != "image":
        _state_tiles[key] = tile
    return tile


def thumb_box(size: str) -> tuple[int, int, int]:
    try:
        return theme.THUMB[size]
    except KeyError:
        raise KeyError(f"no Thumb size {size!r}; there are {', '.join(theme.THUMB)}") from None


class Thumb(QWidget):
    """A wallpaper's preview at one of the design's sizes (`theme.THUMB`).

    `set_source(folder)` reads the preview off the GUI thread through the
    shared loader and shows it when it lands; `set_pixmap` shows one the page
    already has. With neither it is the placeholder; while a source loads it
    is an empty well that shimmers. "grid" is 16:9 at whatever width the
    layout gives it."""

    def __init__(self, size: str = "row", parent: QWidget | None = None, *,
                 loader: ThumbLoader | None = None):
        super().__init__(parent)
        self._size = size
        width, height, self._radius = thumb_box(size)
        self._pixmap: QPixmap | None = None
        self._state = "placeholder"
        self._source: str | None = None
        self._loader = loader
        self._listening: ThumbLoader | None = None
        if size == "grid":
            policy = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            policy.setHeightForWidth(True)
            self.setSizePolicy(policy)
            self.setMinimumWidth(width)
        else:
            self.setFixedSize(width, height)

    def hasHeightForWidth(self) -> bool:        # noqa: N802 - Qt's name
        return self._size == "grid"

    def heightForWidth(self, width: int) -> int:    # noqa: N802 - Qt's name
        return round(width * 9 / 16) if self._size == "grid" else self.height()

    def sizeHint(self) -> QSize:                # noqa: N802 - Qt's name
        width, height, _ = thumb_box(self._size)
        return QSize(width, height)

    def state(self) -> str:
        return self._state

    def source(self) -> str | None:
        return self._source

    def pixmap(self) -> QPixmap | None:
        return self._pixmap

    def set_pixmap(self, pixmap: QPixmap | None) -> None:
        self._source = None
        self._pixmap = pixmap if pixmap is not None and not pixmap.isNull() else None
        self._set_state("image" if self._pixmap is not None else "placeholder")

    def set_loading(self) -> None:
        self._pixmap = None
        self._set_state("loading")

    def set_source(self, path: str | None) -> None:
        """Show the preview of a wallpaper folder (or an image file), read on a
        worker; None shows the placeholder."""
        if not path:
            self.set_pixmap(None)
            return
        path = str(path)
        if path == self._source and self._state != "placeholder":
            return
        self._source = path
        self._pixmap = None
        self._set_state("loading")
        loader = self._loader or shared_loader()
        if self._listening is not loader:
            loader.local_done.connect(self._arrived)
            self._listening = loader
        dpr = self.devicePixelRatioF()
        loader.forget_local(path)
        loader.request_local(path, path, QSize(math.ceil(self.width() * dpr),
                                               math.ceil(self.height() * dpr)))

    def _arrived(self, key: str, image) -> None:
        if key != self._source:
            return
        if image.isNull():
            self._pixmap = None
            self._set_state("placeholder")
        else:
            self._pixmap = QPixmap.fromImage(image)
            self._pixmap.setDevicePixelRatio(self.devicePixelRatioF())
            self._set_state("image")

    def _set_state(self, state: str) -> None:
        self._state = state
        driver = animations.loop("shimmer")
        if state == "loading":
            driver.subscribe(self)
        else:
            driver.unsubscribe(self)
        self.update()

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        painter = QPainter(self)
        if not self.isEnabled():
            painter.setOpacity(theme.DISABLED_OPACITY)
        shimmer = animations.loop("shimmer").value() if self._state == "loading" else None
        paint_thumb(painter, QRectF(self.rect()), self._pixmap, self._state, self._radius,
                    shimmer=shimmer)


# ---- the column spec and the cells --------------------------------------------------------

@dataclass(frozen=True)
class Column:
    """One column of a Table.

    - `title`: the header's words (drawn upper case).
    - `width`: the content's width in px, as the design's grid writes it; None
      shares out what the fixed columns leave.
    - `align`: "left", "right" or "center".
    - `mono`: Consolas at type.monoSm (counts, sizes, times, ids), else Segoe at
      type.bodySm. `font` names another type token outright.
    - `tone`: the text's colour token.
    - `sortable`: a click on its title sorts by it.
    - `thumb`: a `theme.THUMB` size drawn before the text; `icon`: a glyph.
    - `elide`: "right", or "left" for paths, whose end is the part that tells.
    """
    title: str
    width: int | None = None
    align: str = "left"
    mono: bool = False
    sortable: bool = True
    tone: str = "text.body"
    font: str | None = None
    thumb: str | None = None
    icon: str | None = None
    elide: str = "right"

    def __post_init__(self) -> None:
        if self.align not in ALIGNMENTS:
            raise ValueError(f"no alignment {self.align!r}; there are left, right and center")
        if self.thumb is not None:
            thumb_box(self.thumb)
        if self.elide not in ("left", "right"):
            raise ValueError(f"a column elides on the left or the right, not {self.elide!r}")

    def type_token(self) -> str:
        return self.font or ("type.monoSm" if self.mono else "type.bodySm")


@dataclass(frozen=True)
class Cell:
    """A cell's text in a tone of its own ("on screen" in accent), or strong."""
    text: str
    tone: str | None = None
    strong: bool = False


@dataclass(frozen=True)
class ChipCell:
    """A cell that is a Chip (`ChipCell("New")`, `ChipCell("NeedsTags")`)."""
    variant: str
    text: str | None = None


@dataclass(frozen=True)
class Group:
    """A run of rows under a header row: "ALREADY SHOWN THIS CYCLE · 4 of 201".
    `note` None writes the group's count out of every row the table holds."""
    key: Hashable
    title: str
    note: str | None = None
    show_empty: bool = False


def _text_of(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (Cell, ChipCell)):
        return value.text or ("" if isinstance(value, Cell) else value.variant)
    return str(value)


def _sortable(value):
    """Missing values sort last, whichever way; text sorts without case."""
    if isinstance(value, str):
        value = value.casefold()
    return (value is None, value)


_STRIPES = bytes([0, 1])


# ---- the model ---------------------------------------------------------------------------

class TableModel(QAbstractTableModel):
    """The rows of a Table. Give it the items and the columns; override
    `cell(item, column)` to say what each cell shows (the default indexes the
    item like a tuple), `sort_key(item, column)` to sort by something other
    than the text, `thumb_source(item)` for a thumb column's folder, and
    `row_dimmed(item)` for rows drawn faint.

    Groups: `set_rows(items, groups=[Group(...)], group_of=fn)` puts each item
    under the group whose key `group_of(item)` returns, in the order the
    groups are given; a group header is a row of its own that is never
    selected. Zebra stripes restart under each header.
    """

    def __init__(self, columns: Sequence[Column], items: Iterable = (), parent=None, *,
                 groups: Sequence[Group] = (), group_of: Callable | None = None):
        super().__init__(parent)
        if not columns:
            raise ValueError("a table has at least one column")
        self.columns: tuple[Column, ...] = tuple(columns)
        self._items: list = []
        self._groups: tuple[Group, ...] = ()
        self._group_of: Callable | None = None
        self._filter: Callable | None = None
        self._sort_column = -1
        self._sort_order = Qt.AscendingOrder
        self._display: list[int] = []       # item index, or -(group index + 1)
        self._group_rows: list[int] = []
        self._stripe = bytearray()
        self._counts: list[int] = []        # rows under each group
        self._reverse: dict[int, int] | None = None
        self.set_rows(items, groups=groups, group_of=group_of)

    # -- what subclasses say

    def cell(self, item, column: int):
        """A cell's content: a str, a Cell, a ChipCell or None."""
        return item[column]

    def sort_key(self, item, column: int):
        """What a column sorts by. The text, unless overridden with a number."""
        return _text_of(self.cell(item, column))

    def thumb_source(self, item) -> str | None:
        """The wallpaper folder (or preview file) a thumb column shows."""
        return None

    def row_dimmed(self, item) -> bool:
        return False

    # -- the rows

    def set_rows(self, items: Iterable, *, groups: Sequence[Group] | None = None,
                 group_of: Callable | None = None) -> None:
        self.beginResetModel()
        self._items = list(items)
        if groups is not None:
            self._groups = tuple(groups)
            self._group_of = group_of
        self._rebuild()
        self.endResetModel()

    def set_filter(self, predicate: Callable | None) -> None:
        """Show only the items `predicate(item)` is true of; None shows all."""
        self.beginResetModel()
        self._filter = predicate
        self._rebuild()
        self.endResetModel()

    def _rebuild(self) -> None:
        items = self._items
        keep = self._filter
        order = (list(range(len(items))) if keep is None
                 else [i for i, item in enumerate(items) if keep(item)])
        if self._groups:
            buckets = {group.key: [] for group in self._groups}
            spill: list[int] = []
            of = self._group_of
            for i in order:
                bucket = buckets.get(of(items[i]) if of else None)
                (bucket if bucket is not None else spill).append(i)
            runs = [(g, buckets[group.key]) for g, group in enumerate(self._groups)]
            if spill:
                runs.append((None, spill))
        else:
            runs = [(None, order)]
        if self._sort_column >= 0:
            column = self._sort_column
            key = self.sort_key

            def by(i: int):
                return _sortable(key(items[i], column))

            reverse = self._sort_order == Qt.DescendingOrder
            for _, rows in runs:
                rows.sort(key=by, reverse=reverse)
        display: list[int] = []
        stripe = bytearray()
        self._counts = [0] * len(self._groups)
        for g, rows in runs:
            if g is not None:
                self._counts[g] = len(rows)
                if not rows and not self._groups[g].show_empty:
                    continue
                display.append(-(g + 1))
                stripe.append(0)
            display.extend(rows)
            stripe.extend((_STRIPES * (len(rows) // 2 + 1))[:len(rows)])
        self._display = display
        self._stripe = stripe
        self._group_rows = [row for row, i in enumerate(display) if i < 0] if self._groups else []
        self._reverse = None

    def items(self) -> list:
        return self._items

    def item_at(self, row: int):
        """The item a row shows; None for a group header."""
        if 0 <= row < len(self._display):
            i = self._display[row]
            if i >= 0:
                return self._items[i]
        return None

    def item_index(self, row: int) -> int:
        """Which of the items a row shows; -1 for a header or no row."""
        if 0 <= row < len(self._display):
            return self._display[row]
        return -1

    def row_of_item(self, index: int) -> int:
        """The row showing item `index`, or -1 when it is filtered out."""
        if self._reverse is None:
            self._reverse = {i: row for row, i in enumerate(self._display) if i >= 0}
        return self._reverse.get(index, -1)

    def is_group_row(self, row: int) -> bool:
        return 0 <= row < len(self._display) and self._display[row] < 0

    def group_rows(self) -> list[int]:
        """The rows that are group headers."""
        return list(self._group_rows)

    def group_at(self, row: int) -> Group | None:
        if self.is_group_row(row):
            return self._groups[-self._display[row] - 1]
        return None

    def group_note(self, row: int) -> str:
        group = self.group_at(row)
        if group is None:
            return ""
        if group.note is not None:
            return group.note
        from .format import ratio
        return ratio(self._counts[-self._display[row] - 1], len(self._items), "prose")

    def group_count(self, key) -> int:
        for g, group in enumerate(self._groups):
            if group.key == key:
                return self._counts[g]
        raise KeyError(key)

    def zebra(self, row: int) -> bool:
        """Whether a row carries the zebra stripe: every second row of a run,
        counting again after each group header."""
        return 0 <= row < len(self._stripe) and bool(self._stripe[row])

    def item_rows(self) -> int:
        """Rows that show an item, headers not counted."""
        return len(self._display) - len(self._group_rows)

    def has_thumbs(self) -> bool:
        return any(column.thumb for column in self.columns)

    # -- sorting

    def sort_column(self) -> int:
        return self._sort_column

    def sort_order(self):
        return self._sort_order

    def sort(self, column: int, order=Qt.AscendingOrder) -> None:     # noqa: A003 - Qt's name
        """Sort within each group; -1 goes back to the order given. A column
        that does not sort is ignored."""
        if column >= 0 and not self.columns[column].sortable:
            return
        self.beginResetModel()
        self._sort_column = column
        self._sort_order = order
        self._rebuild()
        self.endResetModel()

    # -- Qt's model API

    def rowCount(self, parent=QModelIndex()) -> int:     # noqa: N802 - Qt's name
        return 0 if parent.isValid() else len(self._display)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802 - Qt's name
        return 0 if parent.isValid() else len(self.columns)

    def flags(self, index):
        if not index.isValid() or self.is_group_row(index.row()):
            return Qt.NoItemFlags
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row, column = index.row(), index.column()
        if role in (Qt.DisplayRole, Qt.AccessibleTextRole):
            group = self.group_at(row)
            if group is not None:
                return f"{group.title} · {self.group_note(row)}" if column == 0 else None
            item = self.item_at(row)
            return None if item is None else _text_of(self.cell(item, column))
        if role == Qt.TextAlignmentRole:
            return int(ALIGNMENTS[self.columns[column].align] | Qt.AlignVCenter)
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):  # noqa: N802 - Qt's name
        if orientation == Qt.Horizontal and 0 <= section < len(self.columns):
            if role == Qt.DisplayRole:
                return self.columns[section].title
            if role == Qt.TextAlignmentRole:
                return int(ALIGNMENTS[self.columns[section].align] | Qt.AlignVCenter)
        return None


# ---- painting a row -----------------------------------------------------------------------------

class _Fonts:
    """Fonts and their metrics, made once per type token."""

    def __init__(self):
        self._cache: dict[tuple[str, bool], tuple[QFont, QFontMetricsF]] = {}

    def get(self, token: str, strong: bool = False) -> tuple[QFont, QFontMetricsF]:
        key = (token, strong)
        found = self._cache.get(key)
        if found is None:
            font = theme.font(token)
            if strong:
                font.setWeight(QFont.DemiBold)
            found = (font, QFontMetricsF(font))
            self._cache[key] = found
        return found


def _draw_text(painter: QPainter, fonts: _Fonts, rect: QRectF, text: str, token: str,
               colour: str, align, elide: str = "right", strong: bool = False) -> float:
    """Draw one line, elided to fit; returns the width it took."""
    if not text or rect.width() <= 0:
        return 0.0
    font, metrics = fonts.get(token, strong)
    mode = Qt.ElideLeft if elide == "left" else Qt.ElideRight
    shown = metrics.elidedText(text, mode, rect.width())
    painter.setFont(font)
    painter.setPen(theme.color(colour))
    painter.drawText(rect, align | Qt.AlignVCenter, shown)
    return metrics.horizontalAdvance(shown)


def paint_row_ground(painter: QPainter, rect: QRectF, *, selected: bool = False,
                     hovered: bool = False, zebra: bool = False, focused: bool = False,
                     radius: float = 0.0) -> None:
    """A row's ground: selected (accent.soft and the accent edge down the left),
    else under the pointer (surface.raised), else the zebra stripe; and the
    focus ring inside it when the keyboard is on it."""
    rect = QRectF(rect)
    fill = ("accent.soft" if selected else "surface.raised" if hovered
            else "surface.zebra" if zebra else None)
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    if fill:
        painter.fillPath(path, theme.color(fill))
    if selected:
        painter.save()
        painter.setClipPath(path)
        painter.fillRect(QRectF(rect.left(), rect.top(), theme.TABLE_SELECT_EDGE, rect.height()),
                         theme.color("accent"))
        painter.restore()
    if focused:
        ring = theme.FOCUS_RING
        painter.setPen(QPen(theme.color("focus.ring"), ring))
        painter.setBrush(Qt.NoBrush)
        inner = rect.adjusted(ring / 2, ring / 2, -ring / 2, -ring / 2)
        painter.drawRoundedRect(inner, max(0.0, radius - ring / 2), max(0.0, radius - ring / 2))
    painter.restore()


class RowDelegate(QStyledItemDelegate):
    """Paints a Table's rows, a whole row per call (`paint_row`): the ground,
    a group header, or each cell in turn — text, a chip, a thumb, a glyph.

    It keeps what it has worked out once — colours, fonts, elided text, chip
    sizes — so painting a row is mostly copying. `begin(dpr)` starts a pass
    with a fresh painter.

    Installed on another view, its `paint` draws the one cell it is given,
    so a model can be shown elsewhere without a second delegate."""

    ELIDED_LIMIT = 8000             # remembered elisions; a screen needs a few hundred

    def __init__(self, table: "Table"):
        super().__init__(table)
        self._table = table
        self._fonts = _Fonts()
        self._colours: dict[str, object] = {}
        self._elided: dict[tuple, str] = {}
        self._chips: dict[tuple, tuple[float, float]] = {}
        self._font = None
        self._dpr = 1.0

    def begin(self, dpr: float) -> None:
        """A new pass, with a new painter: forget which font it holds."""
        self._font = None
        self._dpr = dpr

    def colour(self, token: str):
        found = self._colours.get(token)
        if found is None:
            found = self._colours[token] = theme.color(token)
        return found

    def _text(self, painter: QPainter, rect: QRectF, text: str, token: str, colour: str,
              align, elide: str = "right", strong: bool = False) -> None:
        width = rect.width()
        if not text or width <= 0:
            return
        key = (text, token, strong, int(width), elide)
        shown = self._elided.get(key)
        if shown is None:
            metrics = self._fonts.get(token, strong)[1]
            shown = metrics.elidedText(text, Qt.ElideLeft if elide == "left" else Qt.ElideRight,
                                       width)
            if len(self._elided) >= self.ELIDED_LIMIT:
                self._elided.clear()
            self._elided[key] = shown
        font = (token, strong)
        if font != self._font:
            painter.setFont(self._fonts.get(token, strong)[0])
            self._font = font
        painter.setPen(self.colour(colour))
        painter.drawText(rect, align | Qt.AlignVCenter, shown)

    # -- a whole row

    def paint_row(self, painter: QPainter, rect: QRect, row: int, *, selected: bool,
                  hovered: bool, focused: bool, spans: Sequence[tuple[float, float]]) -> None:
        model: TableModel = self._table.model()
        item = model.item_at(row)
        if item is None:
            self.paint_group(painter, QRectF(rect), model.group_at(row), model.group_note(row))
            return
        fill = ("accent.soft" if selected else "surface.raised" if hovered
                else "surface.zebra" if model.zebra(row) else None)
        if fill:
            painter.fillRect(rect, self.colour(fill))
        if selected:
            painter.fillRect(rect.x(), rect.y(), theme.TABLE_SELECT_EDGE, rect.height(),
                             self.colour("accent"))
        if focused:
            paint_row_ground(painter, QRectF(rect), focused=True)
            self._font = None
        faded = (theme.DISABLED_FIELD_OPACITY if not self._table.isEnabled()
                 else theme.DIM_OPACITY if model.row_dimmed(item) else None)
        if faded is not None:
            painter.setOpacity(faded)
        top, height = rect.y(), rect.height()
        for c, column in enumerate(model.columns):
            left, width = spans[c]
            if width > 0:
                self.paint_cell(painter, left, top, width, height, model, item, c, column)
        if faded is not None:
            painter.setOpacity(1.0)

    def paint_group(self, painter: QPainter, rect: QRectF, group: Group | None, note: str) -> None:
        if group is None:
            return
        pad = theme.TABLE_PAD
        box = rect.adjusted(pad, 0, -pad, -1)
        title = group.title.upper()
        self._text(painter, box, title, "type.overline", "text.lo", Qt.AlignLeft)
        width = self._fonts.get("type.overline")[1].horizontalAdvance(title)
        box.setLeft(box.left() + width + theme.TABLE_GROUP_GAP)
        self._text(painter, box, note, "type.monoSm", "text.lo", Qt.AlignLeft)
        painter.fillRect(QRectF(rect.left(), rect.top() + rect.height() - 1, rect.width(), 1),
                         self.colour("border.hairline"))

    def paint_cell(self, painter: QPainter, left: float, top: float, width: float,
                   height: float, model: TableModel, item, c: int, column: Column) -> None:
        value = model.cell(item, c)
        x, right = left, left + width
        middle = top + height / 2
        if column.thumb:
            w, h, _ = theme.THUMB[column.thumb]
            painter.drawPixmap(QPointF(x, middle - h / 2),
                               self._table.thumb_tile(model.thumb_source(item), column.thumb))
            x += w + theme.THUMB_GAP
        if column.icon:
            size = theme.TABLE_ICON
            painter.drawPixmap(QPointF(x, middle - size / 2),
                               icons.pixmap(column.icon, "text.lo", size, self._dpr))
            x += size + theme.TABLE_ICON_GAP
        if right <= x:
            return
        align = ALIGNMENTS[column.align]
        if isinstance(value, ChipCell):
            key = (value.variant, value.text)
            size = self._chips.get(key)
            if size is None:
                box = chip_size(value.variant, value.text)
                size = self._chips[key] = (box.width(), box.height())
            if align == Qt.AlignRight:
                chip_left = right - size[0]
            elif align == Qt.AlignHCenter:
                chip_left = (x + right - size[0]) / 2
            else:
                chip_left = x
            ring = theme.FOCUS_RING
            painter.drawPixmap(QPointF(chip_left - ring, middle - size[1] / 2 - ring),
                               chip_pixmap(value.variant, value.text, "default", self._dpr))
            return
        box = QRectF(x, top, right - x, height)
        if isinstance(value, Cell):
            self._text(painter, box, value.text, column.type_token(),
                       value.tone or column.tone, align, column.elide, value.strong)
        elif value is not None:
            self._text(painter, box, str(value), column.type_token(), column.tone,
                       align, column.elide)

    # -- as an ordinary delegate

    def paint(self, painter: QPainter, option, index: QModelIndex) -> None:
        model = index.model()
        if not isinstance(model, TableModel):
            super().paint(painter, option, index)
            return
        item = model.item_at(index.row())
        if item is None:
            return
        self.begin(painter.device().devicePixelRatioF())
        column = model.columns[index.column()]
        rect = QRectF(option.rect).adjusted(theme.TABLE_GAP / 2, 0, -theme.TABLE_GAP / 2, 0)
        self.paint_cell(painter, rect.left(), rect.top(), rect.width(), rect.height(),
                        model, item, index.column(), column)

    def sizeHint(self, option, index) -> QSize:     # noqa: N802 - Qt's name
        return QSize(0, self._table.row_height())


# ---- the header ------------------------------------------------------------------------

class TableHeader(QHeaderView):
    """The column titles on surface.header, mono and tracked; the sorted
    column's title in text.body with the accent chevron after it."""

    def __init__(self, table: "Table"):
        super().__init__(Qt.Horizontal, table)
        self._table = table
        self._fonts = _Fonts()
        self._hot = -1
        self.setSectionsClickable(True)
        self.setHighlightSections(False)
        self.setSortIndicatorShown(False)     # the model holds the sort; this draws it
        self.setMouseTracking(True)
        self.setMinimumSectionSize(1)
        self.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)

    def sizeHint(self) -> QSize:                # noqa: N802 - Qt's name
        height = 2 * theme.TABLE_HEAD_PAD + math.ceil(theme.line_height("type.tableHead")) + 1
        return QSize(super().sizeHint().width(), height)

    def mouseMoveEvent(self, event) -> None:    # noqa: N802 - Qt's name
        super().mouseMoveEvent(event)
        hot = self.logicalIndexAt(event.position().toPoint())
        if hot != self._hot:
            self._hot = hot
            self.viewport().update()

    def leaveEvent(self, event) -> None:        # noqa: N802 - Qt's name
        super().leaveEvent(event)
        self._hot = -1
        self.viewport().update()

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        painter = QPainter(self.viewport())
        rect = QRectF(self.viewport().rect())
        painter.fillRect(rect, theme.color("surface.header"))
        painter.fillRect(QRectF(rect.left(), rect.height() - 1, rect.width(), 1),
                         theme.color("border.hairline"))
        model = self._table.model()
        if not isinstance(model, TableModel):
            return
        sorted_by, order = model.sort_column(), model.sort_order()
        font, metrics = self._fonts.get("type.tableHead")
        for c, (left, width) in enumerate(self._table.column_spans()):
            column = model.columns[c]
            if width <= 0:
                continue
            box = QRectF(left, 0, width, rect.height() - 1)
            title = column.title.upper()
            lit = c == sorted_by or (column.sortable and c == self._hot)
            icon = theme.TABLE_SORT_ICON + theme.SP_4 if c == sorted_by else 0
            shown = metrics.elidedText(title, Qt.ElideRight, max(0.0, box.width() - icon))
            text_w = metrics.horizontalAdvance(shown)
            align = ALIGNMENTS[column.align]
            if align == Qt.AlignRight:
                x = box.right() - text_w - icon
            elif align == Qt.AlignHCenter:
                x = box.center().x() - (text_w + icon) / 2
            else:
                x = box.left()
            painter.setFont(font)
            painter.setPen(theme.color("text.body" if lit else "text.lo"))
            painter.drawText(QRectF(x, box.top(), text_w + 1, box.height()),
                             Qt.AlignLeft | Qt.AlignVCenter, shown)
            if c == sorted_by:
                glyph = "sortUp" if order == Qt.AscendingOrder else "chevD"
                size = theme.TABLE_SORT_ICON
                painter.drawPixmap(
                    QPointF(x + text_w + theme.SP_4, box.center().y() - size / 2),
                    icons.pixmap(glyph, "accent.hover", size, self.devicePixelRatioF()))


# ---- the table -----------------------------------------------------------------------------

class Table(QTableView):
    """A table of a `TableModel`, painted one whole row at a time.

    Rows are a fixed height (`row_height()`: the thumb column's height and
    its padding, or one line of text and a chip); group headers are shorter.
    Selection is by row. A click on a sortable column's title sorts by it,
    and again the other way; the selection follows its items. Thumbnails are
    asked for once scrolling settles, for the rows on screen only.
    """

    THUMB_SETTLE = 90               # ms of stillness before thumbnails are asked for
    THUMB_CACHE = 400               # pixmaps kept; a screen shows a few dozen

    sort_changed = Signal(int, object)      # column, Qt.SortOrder

    def __init__(self, parent: QWidget | None = None, *, loader: ThumbLoader | None = None):
        super().__init__(parent)
        self._delegate = RowDelegate(self)
        self.setItemDelegate(self._delegate)
        self.setHorizontalHeader(TableHeader(self))
        self.verticalHeader().hide()
        self.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setFrameShape(QTableView.NoFrame)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setMouseTracking(True)
        self.setCornerButtonEnabled(False)
        self.horizontalHeader().sectionClicked.connect(self._header_clicked)
        self.horizontalHeader().sectionResized.connect(self._spans_changed)
        self.horizontalHeader().geometriesChanged.connect(self._spans_changed)

        self._row_height: int | None = None
        self._hover = -1
        self._keyboard = False
        self._spans: list[tuple[float, float]] | None = None

        self._loader = loader or ThumbLoader(self)
        self._loader.local_done.connect(self._thumb_arrived)
        self._pixmaps: OrderedDict[str, QPixmap | None] = OrderedDict()
        self._tiles: dict[tuple[str, str], QPixmap] = {}
        self._rows_for_key: dict[str, list[int]] = {}
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.setInterval(self.THUMB_SETTLE)
        self._settle.timeout.connect(self.request_visible_thumbs)
        self.verticalScrollBar().valueChanged.connect(self._scrolled)

    # -- the model

    def setModel(self, model) -> None:          # noqa: N802 - Qt's name
        if not isinstance(model, TableModel):
            raise TypeError("a Table shows a TableModel")
        old = self.model()
        if isinstance(old, TableModel):
            old.modelReset.disconnect(self._model_reset)
        super().setModel(model)
        model.modelReset.connect(self._model_reset)
        self._apply_columns()
        self._model_reset()

    def _apply_columns(self) -> None:
        header = self.horizontalHeader()
        model: TableModel = self.model()
        last = len(model.columns) - 1
        for c, column in enumerate(model.columns):
            left, right = self._pads(c, last)
            if column.width is None:
                header.setSectionResizeMode(c, QHeaderView.Stretch)
            else:
                header.setSectionResizeMode(c, QHeaderView.Fixed)
                header.resizeSection(c, column.width + left + right)
        self._spans = None

    @staticmethod
    def _pads(c: int, last: int) -> tuple[int, int]:
        """Room each side of a column's content: the row's side padding at the
        ends, half the column gap between two columns."""
        half = theme.TABLE_GAP // 2
        left = theme.TABLE_PAD if c == 0 else theme.TABLE_GAP - half
        right = theme.TABLE_PAD if c == last else half
        return left, right

    def column_spans(self) -> list[tuple[float, float]]:
        """Each column's content, as (left, width) in viewport coordinates."""
        if self._spans is None:
            model = self.model()
            header = self.horizontalHeader()
            last = len(model.columns) - 1 if isinstance(model, TableModel) else 0
            spans = []
            for c in range(header.count()):
                left, right = self._pads(c, last)
                x = header.sectionViewportPosition(c)
                spans.append((float(x + left), float(header.sectionSize(c) - left - right)))
            self._spans = spans
        return self._spans

    def _spans_changed(self, *args) -> None:
        self._spans = None
        self.viewport().update()

    def _model_reset(self) -> None:
        self._hover = -1
        self._rows_for_key = {}
        header = self.verticalHeader()
        header.setDefaultSectionSize(self.row_height())
        model: TableModel = self.model()
        group = self.group_row_height()
        for row in model.group_rows():
            header.resizeSection(row, group)
        self.horizontalHeader().viewport().update()
        self._settle.start()

    # -- sizes

    def row_height(self) -> int:
        if self._row_height is not None:
            return self._row_height
        model = self.model()
        tallest = [theme.CHIP_HEIGHT, math.ceil(theme.line_height("type.bodySm"))]
        pad = theme.TABLE_ROW_PAD_TEXT
        if isinstance(model, TableModel):
            thumbs = [theme.THUMB[c.thumb][1] for c in model.columns if c.thumb]
            if thumbs:
                tallest += thumbs
                pad = theme.TABLE_ROW_PAD
        return max(tallest) + 2 * pad

    def set_row_height(self, height: int | None) -> None:
        self._row_height = height
        if isinstance(self.model(), TableModel):
            self._model_reset()

    @staticmethod
    def group_row_height() -> int:
        return 2 * theme.TABLE_GROUP_PAD + math.ceil(theme.line_height("type.overline")) + 1

    def visible_rows(self) -> range:
        """The rows any part of which is on screen."""
        model = self.model()
        if model is None or model.rowCount() == 0:
            return range(0)
        first = self.rowAt(0)
        if first < 0:
            return range(0)
        last = self.rowAt(self.viewport().height() - 1)
        if last < 0:
            last = model.rowCount() - 1
        return range(first, last + 1)

    # -- sorting

    def _header_clicked(self, column: int) -> None:
        model: TableModel = self.model()
        if not isinstance(model, TableModel) or not model.columns[column].sortable:
            return
        if model.sort_column() == column:
            order = (Qt.DescendingOrder if model.sort_order() == Qt.AscendingOrder
                     else Qt.AscendingOrder)
        else:
            order = Qt.AscendingOrder
        self.sort_by(column, order)

    def sort_by(self, column: int, order=Qt.AscendingOrder) -> None:
        """Sort, keeping the same items selected and the current one current."""
        model: TableModel = self.model()
        chosen = [model.item_index(i.row()) for i in self.selectionModel().selectedRows()]
        current = model.item_index(self.currentIndex().row())
        model.sort(column, order)
        self.select_items(chosen)
        if current >= 0:
            row = model.row_of_item(current)
            if row >= 0:
                self.selectionModel().setCurrentIndex(model.index(row, 0),
                                                      QItemSelectionModel.NoUpdate)
        self.sort_changed.emit(column, order)

    def select_items(self, indices: Iterable[int]) -> None:
        """Select the rows showing these items (by their index in the model)."""
        model: TableModel = self.model()
        rows = sorted(r for r in (model.row_of_item(i) for i in indices) if r >= 0)
        selection = QItemSelection()
        last = model.columnCount() - 1
        start = prev = None
        for row in rows + [None]:
            if start is not None and (row is None or row != prev + 1):
                selection.select(model.index(start, 0), model.index(prev, last))
                start = None
            if row is not None and start is None:
                start = row
            prev = row
        self.selectionModel().select(selection, QItemSelectionModel.ClearAndSelect)

    def selected_items(self) -> list:
        model: TableModel = self.model()
        return [model.item_at(i.row()) for i in self.selectionModel().selectedRows()
                if model.item_at(i.row()) is not None]

    # -- thumbnails

    def thumb_state(self, source) -> tuple[str, QPixmap | None]:
        if not source:
            return "placeholder", None
        key = str(source)
        if key in self._pixmaps:
            pixmap = self._pixmaps[key]
            return ("image", pixmap) if pixmap is not None else ("placeholder", None)
        return "loading", None

    def thumb_tile(self, source, size: str) -> QPixmap:
        """The row's thumb as a tile ready to copy: its picture, or the empty
        well while it loads, or the placeholder when it has none."""
        dpr = self.devicePixelRatioF()
        state, pixmap = self.thumb_state(source)
        if state != "image":
            return thumb_tile(None, state, size, dpr)
        key = (str(source), size)
        tile = self._tiles.get(key)
        if tile is None:
            tile = self._tiles[key] = thumb_tile(pixmap, "image", size, dpr)
        return tile

    def loader(self) -> ThumbLoader:
        return self._loader

    def request_visible_thumbs(self) -> None:
        """Ask for the previews of the rows on screen, and only those."""
        model = self.model()
        if not isinstance(model, TableModel) or not model.has_thumbs():
            return
        wanted: dict[str, list[int]] = {}
        for row in self.visible_rows():
            item = model.item_at(row)
            if item is None:
                continue
            source = model.thumb_source(item)
            if source:
                wanted.setdefault(str(source), []).append(row)
        self._rows_for_key = wanted
        self._loader.retarget_local()
        column = next(c for c in model.columns if c.thumb)
        width, height, _ = theme.THUMB[column.thumb]
        dpr = self.devicePixelRatioF()
        box = QSize(math.ceil(width * dpr), math.ceil(height * dpr))
        for key in wanted:
            if key in self._pixmaps:
                self._pixmaps.move_to_end(key)
            else:
                self._loader.request_local(key, key, box)

    def _thumb_arrived(self, key: str, image) -> None:
        pixmap = None
        if not image.isNull():
            pixmap = QPixmap.fromImage(image)
            pixmap.setDevicePixelRatio(self.devicePixelRatioF())
        self._pixmaps[key] = pixmap
        self._pixmaps.move_to_end(key)
        for stale in [k for k in self._tiles if k[0] == key]:
            del self._tiles[stale]
        while len(self._pixmaps) > self.THUMB_CACHE:
            gone, _ = self._pixmaps.popitem(last=False)
            for stale in [k for k in self._tiles if k[0] == gone]:
                del self._tiles[stale]
        for row in self._rows_for_key.get(key, ()):
            self._update_row(row)

    def _scrolled(self) -> None:
        self._settle.start()
        if self.underMouse():
            self._hover_to(self.rowAt(self.viewport().mapFromGlobal(QCursor.pos()).y()))

    def resizeEvent(self, event) -> None:       # noqa: N802 - Qt's name
        super().resizeEvent(event)
        self._spans = None
        self._settle.start()

    # -- hover, focus, current row

    def _row_rect(self, row: int) -> QRect:
        return QRect(0, self.rowViewportPosition(row), self.viewport().width(),
                     self.rowHeight(row))

    def _update_row(self, row: int) -> None:
        if row >= 0:
            self.viewport().update(self._row_rect(row))

    def hovered_row(self) -> int:
        return self._hover

    def _hover_to(self, row: int) -> None:
        model = self.model()
        if isinstance(model, TableModel) and model.is_group_row(row):
            row = -1
        if row != self._hover:
            old, self._hover = self._hover, row
            self._update_row(old)
            self._update_row(row)

    def viewportEvent(self, event) -> bool:     # noqa: N802 - Qt's name
        kind = event.type()
        if kind in (QEvent.HoverEnter, QEvent.HoverMove, QEvent.HoverLeave):
            return False        # Qt's own hover repaints cells; the table tracks rows
        if kind == QEvent.Leave:
            self._hover_to(-1)
        return super().viewportEvent(event)

    def mouseMoveEvent(self, event) -> None:    # noqa: N802 - Qt's name
        self._hover_to(self.rowAt(event.position().toPoint().y()))
        if event.buttons() != Qt.NoButton:
            super().mouseMoveEvent(event)       # a drag that extends the selection

    def mousePressEvent(self, event) -> None:   # noqa: N802 - Qt's name
        self._keyboard = False
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:     # noqa: N802 - Qt's name
        self._keyboard = True
        super().keyPressEvent(event)
        self._update_row(self.currentIndex().row())

    def focusInEvent(self, event) -> None:      # noqa: N802 - Qt's name
        if event.reason() in (Qt.TabFocusReason, Qt.BacktabFocusReason, Qt.ShortcutFocusReason):
            self._keyboard = True
        elif event.reason() not in (Qt.ActiveWindowFocusReason, Qt.PopupFocusReason):
            self._keyboard = False
        super().focusInEvent(event)
        self._update_row(self.currentIndex().row())

    def focusOutEvent(self, event) -> None:     # noqa: N802 - Qt's name
        super().focusOutEvent(event)
        self._update_row(self.currentIndex().row())

    def currentChanged(self, current, previous) -> None:    # noqa: N802 - Qt's name
        super().currentChanged(current, previous)
        self._update_row(previous.row())
        self._update_row(current.row())

    def focus_visible(self) -> bool:
        return self.hasFocus() and self._keyboard

    # -- painting

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        model = self.model()
        if not isinstance(model, TableModel):
            return
        painter = QPainter(self.viewport())
        area = event.rect()
        first = self.rowAt(area.top())
        if first < 0:
            return
        last = self.rowAt(area.bottom())
        if last < 0:
            last = model.rowCount() - 1
        spans = self.column_spans()
        selection = self.selectionModel()
        root = QModelIndex()
        current = self.currentIndex().row() if self.focus_visible() else -1
        width = self.viewport().width()
        self._delegate.begin(self.devicePixelRatioF())
        for row in range(first, last + 1):
            rect = QRect(0, self.rowViewportPosition(row), width, self.rowHeight(row))
            self._delegate.paint_row(
                painter, rect, row, selected=selection.isRowSelected(row, root),
                hovered=row == self._hover, focused=row == current, spans=spans)


# ---- the bars round a table -------------------------------------------------------------

class _Bar(QWidget):
    """A strip across a table card: a hairline above or below, a ground."""

    def __init__(self, parent: QWidget | None, *, ground: str | None, line: str,
                 pad: tuple[int, int], rounded_bottom: bool = False):
        super().__init__(parent)
        self._ground, self._line, self._rounded = ground, line, rounded_bottom
        self.row = QHBoxLayout(self)
        vertical, horizontal = pad
        self.row.setContentsMargins(horizontal, vertical, horizontal, vertical)
        self.row.setSpacing(theme.SP_10)

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        painter = QPainter(self)
        self.paint_ground(painter)

    def paint_ground(self, painter: QPainter) -> None:
        painter.setRenderHint(QPainter.Antialiasing)
        box = QRectF(self.rect())
        if self._ground:
            path = QPainterPath()
            if self._rounded:
                # the card's bottom corners, inside its 1 px edge
                radius = theme.R_LG - 1
                path.addRoundedRect(box.adjusted(0, -radius, 0, 0), radius, radius)
                clip = QPainterPath()
                clip.addRect(box)
                path = path.intersected(clip)
            else:
                path.addRect(box)
            painter.fillPath(path, theme.color(self._ground))
        y = 0 if self._line == "top" else box.height() - 1
        painter.fillRect(QRectF(0, y, box.width(), 1), theme.color("border.hairline"))


class TableBar(_Bar):
    """The toolbar over a table: a filter, a Dropdown, a SegmentedControl, and
    an IconButton at the far end (`add`, `add_stretch`)."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, ground=None, line="bottom",
                         pad=(theme.SP_8 + 1, theme.TABLE_PAD))
        self.row.setSpacing(theme.SP_8 + 1)

    def add(self, widget: QWidget) -> QWidget:
        self.row.addWidget(widget)
        return widget

    def add_stretch(self) -> None:
        self.row.addStretch(1)


class TableSummary(_Bar):
    """A line of facts over a table, divided by hairlines:
    `33 421 folders | 8 204 never used | 4.2 TB | W:\\wallpaper_reserve`.
    The first is the headline, in text.body; the rest are text.lo."""

    def __init__(self, items: Sequence[str] = (), parent: QWidget | None = None):
        super().__init__(parent, ground=None, line="bottom", pad=(theme.SP_8, theme.TABLE_PAD))
        self.row.setSpacing(0)
        self._labels: list[QLabel] = []
        self.row.addStretch(1)
        self.set_items(items)

    def set_items(self, items: Sequence[str]) -> None:
        items = list(items)
        while len(self._labels) < len(items):
            words = label("", "type.monoSm", "lo")
            words.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
            self.row.insertWidget(len(self._labels), words)
            self._labels.append(words)
        for i, words in enumerate(self._labels):
            shown = i < len(items)
            words.setVisible(shown)
            if shown:
                words.setText(items[i])
                words.setProperty("tone", "body" if i == 0 else "lo")
                words.style().unpolish(words)
                words.style().polish(words)
                left = 0 if i == 0 else theme.SP_12
                words.setContentsMargins(left, 0, theme.SP_12, 0)
        self.update()

    def items(self) -> list[str]:
        return [w.text() for w in self._labels if not w.isHidden()]

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        painter = QPainter(self)
        self.paint_ground(painter)
        line = theme.color("border.hairline")
        for words in self._labels[1:]:
            if not words.isHidden():
                g = words.geometry()
                painter.fillRect(QRectF(g.left(), g.top(), 1, g.height()), line)


class TableFooter(_Bar):
    """The bar under a table: a count on the left (`virtualised · 33 421
    rows`), actions or a note on the right. It rounds the card's bottom
    corners."""

    def __init__(self, text: str = "", parent: QWidget | None = None):
        vertical, horizontal = theme.TABLE_FOOTER_PAD
        super().__init__(parent, ground="surface.footer", line="top",
                         pad=(vertical, horizontal), rounded_bottom=True)
        self._text = label(text, "type.monoSm", "lo")
        self.row.addWidget(self._text)
        self.row.addStretch(1)
        self._note = label("", "type.monoSm", "lo")
        self._note.hide()
        self.row.addWidget(self._note)
        self._actions = QHBoxLayout()
        self._actions.setSpacing(theme.SP_6)
        self.row.addLayout(self._actions)

    def text(self) -> str:
        return self._text.text()

    def set_text(self, text: str) -> None:
        self._text.setText(text)

    def set_note(self, text: str) -> None:
        """Words on the right, for a footer that says rather than does."""
        self._note.setText(text)
        self._note.setVisible(bool(text))

    def add_action(self, widget: QWidget) -> QWidget:
        self._actions.addWidget(widget)
        return widget


# ---- ListRow ----------------------------------------------------------------------------------

LIST_ROW_STATES = ("default", "hover", "selected", "focus", "disabled")
LIST_ROW_ROLE = Qt.UserRole + 60        # a ListRow
LIST_PIXMAP_ROLE = Qt.UserRole + 61     # its thumb, a QPixmap
LIST_STATE_ROLE = Qt.UserRole + 62      # a state to draw regardless (the kit preview)


@dataclass(frozen=True)
class ListRow:
    """What one row of a list says: a title over a mono meta line, and around
    them an optional time column (`when`, recent activity), a glyph, a square
    thumb (the author list), chips, and a trailing mono note (`#1234567890`).
    `chips` is a tuple of (variant, text or None). `icon=""` keeps the glyph's
    room without drawing one, so a column of titles stays aligned."""
    title: str
    meta: str = ""
    when: str = ""
    icon: str | None = None
    icon_tone: str = "text.mid"
    thumb: bool = False
    chips: tuple = ()
    trailing: str = ""


def list_row_height(row: ListRow | None = None) -> int:
    lines = theme.line_height("type.bodySm")
    if row is None or row.meta:
        lines += theme.SP_2 + theme.line_height("type.monoXs")
    inner = max(math.ceil(lines), theme.LIST_ROW_THUMB if row is None or row.thumb else 0)
    return inner + 2 * theme.LIST_ROW_PAD[0]


_list_fonts = _Fonts()


def paint_list_row(painter: QPainter, rect: QRectF, row: ListRow, state: str = "default", *,
                   pixmap: QPixmap | None = None, dpr: float | None = None) -> None:
    """Draw one list row in one of LIST_ROW_STATES."""
    if state not in LIST_ROW_STATES:
        raise ValueError(f"no ListRow state {state!r}; there are {', '.join(LIST_ROW_STATES)}")
    rect = QRectF(rect)
    dpr = dpr or painter.device().devicePixelRatioF()
    paint_row_ground(painter, rect, selected=state == "selected", hovered=state == "hover",
                     focused=state == "focus", radius=theme.R_ROW)
    painter.save()
    if state == "disabled":
        painter.setOpacity(theme.DISABLED_FIELD_OPACITY)
    pad_v, pad_h = theme.LIST_ROW_PAD
    x, right = rect.left() + pad_h, rect.right() - pad_h
    middle = rect.center().y()
    gap = theme.LIST_ROW_GAP
    if row.when:
        _draw_text(painter, _list_fonts, QRectF(x, rect.top(), theme.LIST_ROW_TIME, rect.height()),
                   row.when, "type.monoSm", "text.lo", Qt.AlignLeft)
        x += theme.LIST_ROW_TIME + gap
    if row.icon is not None:
        size = theme.CALLOUT_ICON
        if row.icon:
            painter.drawPixmap(QPointF(x, middle - size / 2),
                               icons.pixmap(row.icon, row.icon_tone, size, dpr))
        x += size + theme.SP_2 + gap
    if row.thumb:
        side = theme.LIST_ROW_THUMB
        paint_thumb(painter, QRectF(x, middle - side / 2, side, side), pixmap,
                    "image" if pixmap is not None else "loading", theme.R_SM + 1)
        x += side + gap
    if row.trailing:
        width = _draw_text(painter, _list_fonts, QRectF(x, rect.top(), right - x, rect.height()),
                           row.trailing, "type.monoSm", "text.lo", Qt.AlignRight)
        right -= width + gap
    for variant, text in reversed(row.chips):
        size = chip_size(variant, text)
        ring = theme.FOCUS_RING
        left = right - size.width()
        painter.drawPixmap(QPointF(left - ring, middle - size.height() / 2 - ring),
                           chip_pixmap(variant, text, "disabled" if state == "disabled"
                                       else "default", dpr))
        right = left - theme.SP_6
    right -= gap - theme.SP_6 if row.chips else 0
    title_h = theme.line_height("type.bodySm")
    meta_h = theme.line_height("type.monoXs") if row.meta else 0.0
    block = title_h + (theme.SP_2 + meta_h if row.meta else 0.0)
    top = middle - block / 2
    title_tone = "text.lo" if state == "disabled" else "text.body"
    _draw_text(painter, _list_fonts, QRectF(x, top, right - x, title_h), row.title,
               "type.bodySm", title_tone, Qt.AlignLeft)
    if row.meta:
        _draw_text(painter, _list_fonts, QRectF(x, top + title_h + theme.SP_2, right - x, meta_h),
                   row.meta, "type.monoXs", "text.lo", Qt.AlignLeft)
    painter.restore()


class ListRowDelegate(QStyledItemDelegate):
    """Draws a list view's rows from `LIST_ROW_ROLE` (a ListRow) and
    `LIST_PIXMAP_ROLE` (its thumb). Hover, selection and keyboard focus come
    from the view."""

    def sizeHint(self, option, index) -> QSize:     # noqa: N802 - Qt's name
        row = index.data(LIST_ROW_ROLE)
        return QSize(0, list_row_height(row if isinstance(row, ListRow) else None))

    def paint(self, painter: QPainter, option, index: QModelIndex) -> None:
        row = index.data(LIST_ROW_ROLE)
        if not isinstance(row, ListRow):
            return
        flags = option.state
        view = option.widget
        if not flags & QStyle.State_Enabled:
            state = "disabled"
        elif flags & QStyle.State_Selected:
            state = "selected"
        elif (flags & QStyle.State_HasFocus and view is not None and view.hasFocus()
              and getattr(view, "focus_visible", lambda: False)()):
            state = "focus"
        elif flags & QStyle.State_MouseOver:
            state = "hover"
        else:
            state = "default"
        forced = index.data(LIST_STATE_ROLE)
        if forced in LIST_ROW_STATES:
            state = forced
        pixmap = index.data(LIST_PIXMAP_ROLE)
        paint_list_row(painter, QRectF(option.rect), row, state,
                       pixmap=pixmap if isinstance(pixmap, QPixmap) else None)


class RowList(QListView):
    """A list of ListRows: the author list, recent activity. The rows paint
    their own ground on the card the list sits on; hover is the pointer's
    row, and the focus ring shows when the keyboard moved it."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setItemDelegate(ListRowDelegate(self))
        self.setFrameShape(QListView.NoFrame)
        self.setMouseTracking(True)
        self.setUniformItemSizes(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._keyboard = False

    def focus_visible(self) -> bool:
        return self.hasFocus() and self._keyboard

    def keyPressEvent(self, event) -> None:     # noqa: N802 - Qt's name
        self._keyboard = True
        super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:   # noqa: N802 - Qt's name
        self._keyboard = False
        super().mousePressEvent(event)

    def focusInEvent(self, event) -> None:      # noqa: N802 - Qt's name
        if event.reason() in (Qt.TabFocusReason, Qt.BacktabFocusReason):
            self._keyboard = True
        super().focusInEvent(event)
