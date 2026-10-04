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
- **An animated preview plays, on screen only.** For the rows on screen whose
  preview is a GIF, the loader reads its bytes on a worker and a QMovie plays
  them from memory, `MAX_PLAYERS` at a time, stopped when the table is off
  screen or motion is off. The row stays a tile, its picture a still; each
  frame is drawn over it, so a frame costs two copies, not a row drawn anew.

The column spec (`Column`) says, per column: its title, a fixed content width
or a share of what is left, alignment, mono or sans, whether it sorts, and
optionally a thumb or a glyph before the text. The model's `cell()` returns a
string, a `Cell` (text in another tone) or a `ChipCell` for each one; or a cell
that acts: a `ButtonCell` (a text button), a `ButtonsCell` (glyph buttons side by
side, a row's actions), a `BusyCell` or a `DiscCell`.
`TagsCell`, `CheckCell` and `ProgressCell` describe Creator rows.

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
    QAbstractTableModel, QBuffer, QByteArray, QEvent, QIODevice, QItemSelection,
    QItemSelectionModel, QModelIndex, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal,
)
from PySide6.QtGui import (
    QCursor, QFont, QFontMetricsF, QMovie, QPainter, QPainterPath, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QListView, QSizePolicy, QStyle,
    QStyledItemDelegate, QTableView, QToolTip, QWidget,
)

from ... import animations, theme
from . import icons
from .base import label
from .buttons import button_pixmap, button_size, icon_button_pixmap
from .chips import chip_pixmap, chip_size
from .inputs import SpinBox
from .thumbs import PREVIEW_KEY, ThumbLoader, shared as shared_loader

ALIGNMENTS = {"left": Qt.AlignLeft, "right": Qt.AlignRight, "center": Qt.AlignHCenter}


# ---- Thumb -------------------------------------------------------------------------

THUMB_STATES = ("placeholder", "loading", "image")


def paint_thumb(painter: QPainter, rect: QRectF, pixmap: QPixmap | None, state: str,
                radius: float, *, shimmer: float | None = None, cover: bool = False) -> None:
    """A preview in its well: the picture fitted inside (letterboxed, never
    cropped, as the gallery draws it) — or, with `cover`, filling the well
    and cropped about its middle (a table's square thumbs) — or while it
    loads an empty well, or when there is none the placeholder's two
    diagonals. `shimmer` is a loading skeleton's opacity, for a Thumb that
    pulses while it waits."""
    rect = QRectF(rect)
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)
    painter.fillPath(path, theme.color("surface.well"))
    painter.setClipPath(path)
    if state == "image" and pixmap is not None and not pixmap.isNull():
        size = pixmap.deviceIndependentSize()
        fit = max if cover else min
        scale = fit(rect.width() / size.width(), rect.height() / size.height())
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
    paint_thumb(painter, QRectF(0, 0, width, height), pixmap, state, radius,
                cover=size in theme.THUMB_COVER)
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
        # The same folder again is already shown, on its way, or known to have
        # no preview: a card refreshed every second must not read it again.
        if path == self._source:
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
    - `natural`: the column is the order the rows were given in (a playlist's
      `#`): a click on its title undoes any sort rather than sorting, and its
      title is lit while no column sorts.
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
    natural: bool = False

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
    """A cell's text in a tone of its own ("on screen" in accent), or strong;
    `icon` is a glyph before it, in the same tone (a lock on a [protected]
    folder) or in `icon_tone`; `sub` is a second, quieter line under it (mono,
    `sub_tone`, text.lo by default): a wallpaper's date under its title."""
    text: str
    tone: str | None = None
    strong: bool = False
    icon: str | None = None
    sub: str = ""
    sub_tone: str | None = None
    icon_tone: str | None = None


@dataclass(frozen=True)
class ChipCell:
    """A cell that is a Chip (`ChipCell("New")`, `ChipCell("NeedsTags")`)."""
    variant: str
    text: str | None = None


@dataclass(frozen=True)
class TagsCell:
    """Chosen tag pills. A batch-following row uses tone="text.lo"."""
    tags: tuple[str, ...]
    tone: str = "text.body"

    def __post_init__(self) -> None:
        object.__setattr__(self, "tags", tuple(self.tags))


@dataclass(frozen=True)
class CheckCell:
    """A row selection checkbox, painted by the delegate; the page handles clicks."""
    checked: bool
    enabled: bool = True


@dataclass(frozen=True)
class ProgressCell:
    """A flat per-item bar; busy means work with no measurable percentage."""
    fraction: float | None = None
    tone: str = "accent"
    busy: bool = False
    caption: str = ""


@dataclass(frozen=True)
class SpinCell:
    """A count painted at rest; double-click or F2 opens one kit SpinBox."""
    value: int
    minimum: int = 1
    maximum: int = 99_999
    enabled: bool = True


@dataclass(frozen=True)
class ButtonCell:
    """A button drawn in the cell, `variant` at rest and `hot` on the row under
    the pointer (the gallery's Subscribe turns Accent there). A click on it is
    `Table.button_clicked(row, column)`, not a click on the row."""
    text: str
    variant: str = "secondary"
    hot: str | None = "accent"


@dataclass(frozen=True)
class CellButton:
    """One glyph button of a `ButtonsCell`: its `icon`, the `tip` that says what
    it does (required, as an IconButton's tool tip is) and the `key` a click on
    it reports. `enabled=False` draws it faded and takes no click. `mark=True`
    makes it a glyph that says something rather than does something (the lock
    on a [protected] folder): no box, no hover, no click, `tone` its colour,
    `tip` still shown under the pointer."""
    key: str
    icon: str
    tip: str
    enabled: bool = True
    mark: bool = False
    tone: str = "text.mid"

    def __post_init__(self) -> None:
        if not self.tip or not self.tip.strip():
            raise ValueError("a cell's button needs a tool tip that says what it does")
        icons.svg(self.icon)            # an unknown name fails here, not at paint time


@dataclass(frozen=True)
class ButtonsCell:
    """Several glyph buttons side by side in one cell, each drawn as an
    IconButton of `size` ("sm", 22 px, or "md"): a playlist row's Send to
    Copier and Mark [protected]. A slot that is None stays empty, so the
    buttons of every row line up. The glyphs rest in text.lo and come up on
    the row under the pointer; a click on one is
    `Table.action_clicked(row, column, key)`, not a click on the row."""
    buttons: tuple[CellButton | None, ...]
    size: str = "sm"

    def __post_init__(self) -> None:
        if self.size not in theme.ICON_BUTTON:
            raise KeyError(f"no IconButton size {self.size!r}; "
                           f"there are {', '.join(theme.ICON_BUTTON)}")

    def width(self) -> int:
        """The room its slots take, empty ones included."""
        return buttons_width(len(self.buttons), self.size)


def buttons_width(slots: int, size: str = "sm") -> int:
    """What a ButtonsCell of so many slots needs: a column's width for it."""
    side = theme.ICON_BUTTON[size][0]
    return slots * side + max(0, slots - 1) * theme.TABLE_BUTTONS_GAP


@dataclass(frozen=True)
class BusyCell:
    """Work under way on the row: a ring and its words ("Subscribing…"). The
    ring turns while the table is told the row spins (`Table.set_spinning`)."""
    text: str
    tone: str = "accent.hover"


@dataclass(frozen=True)
class DiscCell:
    """A glyph on a filled disc (the gallery's subscribed check); `label` is
    what it says to a screen reader."""
    icon: str = "check"
    tone: str = "info"
    label: str = ""


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
    if isinstance(value, (ButtonCell, BusyCell)):
        return value.text
    if isinstance(value, DiscCell):
        return value.label
    if isinstance(value, ButtonsCell):
        return " · ".join(b.tip for b in value.buttons if b is not None)
    if isinstance(value, TagsCell):
        return ", ".join(value.tags)
    if isinstance(value, CheckCell):
        return "Selected" if value.checked else "Not selected"
    if isinstance(value, SpinCell):
        return str(value.value)
    if isinstance(value, ProgressCell):
        return value.caption or ("working" if value.busy else f"{round((value.fraction or 0) * 100)}%")
    return str(value)


_discs: dict[tuple, QPixmap] = {}
_tag_tiles: OrderedDict[tuple, QPixmap] = OrderedDict()
_checks: dict[tuple, QPixmap] = {}


def tags_pixmap(tags: Sequence[str], tone: str, width: float, dpr: float) -> QPixmap:
    """Pills fitted to one cell, with +N for those left over, cached for scrolling."""
    width = max(1, math.floor(width))
    key = (tuple(tags), tone, width, round(dpr, 3))
    found = _tag_tiles.get(key)
    if found is not None:
        _tag_tiles.move_to_end(key)
        return found
    font = theme.font("type.caption")
    metrics = QFontMetricsF(font)
    pad = theme.TAG_PILL_PAD[1]
    gap = theme.TAG_PILL_GAP
    pills: list[tuple[str, float]] = []
    used = 0.0
    for i, tag in enumerate(tags):
        pill_width = metrics.horizontalAdvance(tag) + 2 * pad
        remaining = len(tags) - i - 1
        reserve = metrics.horizontalAdvance(f"+{remaining}") + 2 * pad + gap if remaining else 0
        if used + pill_width + reserve > width:
            more = f"+{len(tags) - i}"
            pill_width = min(metrics.horizontalAdvance(more) + 2 * pad, width - used)
            pills.append((more, pill_width))
            used += pill_width
            break
        pills.append((tag, pill_width))
        used += pill_width + (gap if remaining else 0)
    height = theme.CHIP_HEIGHT
    pixmap = QPixmap(max(1, math.ceil(used * dpr)), math.ceil(height * dpr))
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setFont(font)
    x = 0.0
    for text, pill_width in pills:
        rect = QRectF(x, 0, pill_width, height)
        path = QPainterPath()
        path.addRoundedRect(rect, theme.R_SM, theme.R_SM)
        painter.fillPath(path, theme.color("surface.raised"))
        painter.setPen(theme.color(tone))
        text_width = max(0, pill_width - 2 * pad)
        shown = metrics.elidedText(text, Qt.ElideRight, text_width)
        painter.drawText(rect, Qt.AlignCenter, shown)
        x += pill_width + gap
    painter.end()
    _tag_tiles[key] = pixmap
    if len(_tag_tiles) > RowDelegate.ELIDED_LIMIT:
        _tag_tiles.popitem(last=False)
    return pixmap


def check_pixmap(checked: bool, enabled: bool, dpr: float) -> QPixmap:
    """A table's selection box, shared by all rows in the same state and scale."""
    key = (checked, enabled, round(dpr, 3))
    found = _checks.get(key)
    if found is not None:
        return found
    side = theme.CHECK_BOX
    pixmap = QPixmap(math.ceil(side * dpr), math.ceil(side * dpr))
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    if not enabled:
        painter.setOpacity(theme.DISABLED_FIELD_OPACITY)
    box = QRectF(0.5, 0.5, side - 1, side - 1)
    painter.setPen(QPen(theme.color("accent" if checked else "border.strong"), 1))
    painter.setBrush(theme.color("accent" if checked else "surface.well"))
    painter.drawRoundedRect(box, theme.R_SM, theme.R_SM)
    if checked:
        tick = theme.POPUP_CHECK
        painter.drawPixmap(QPointF((side - tick) / 2, (side - tick) / 2),
                           icons.pixmap("check", "text.onAccent", tick, dpr))
    painter.end()
    _checks[key] = pixmap
    return pixmap


def disc_pixmap(icon: str, tone: str, size: int, dpr: float) -> QPixmap:
    """A glyph in text.onAccent on a disc of `tone`, made once per (icon, tone,
    size, scale): the subscribed check on a gallery card, a DiscCell."""
    key = (icon, tone, size, round(dpr, 3))
    found = _discs.get(key)
    if found is not None:
        return found
    pixmap = QPixmap(math.ceil(size * dpr), math.ceil(size * dpr))
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(theme.color(tone))
    painter.drawEllipse(QRectF(0, 0, size, size))
    glyph = round(size * theme.DISC_GLYPH)
    painter.drawPixmap(QPointF((size - glyph) / 2, (size - glyph) / 2),
                       icons.pixmap(icon, "text.onAccent", glyph, dpr))
    painter.end()
    _discs[key] = pixmap
    return pixmap


def paint_spinner(painter: QPainter, box: QRectF, angle: float) -> None:
    """The kit's spinner in `box`: half a ring in accent turning over the rest
    of it, at the "spin" loop's `angle` (a Spinner, a BusyCell, a gallery card
    being subscribed to)."""
    stroke = theme.ACTIVITY_SPINNER_STROKE
    box = QRectF(box).adjusted(stroke / 2, stroke / 2, -stroke / 2, -stroke / 2)
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(Qt.NoBrush)
    painter.setPen(QPen(theme.color("surface.raised"), stroke))
    painter.drawEllipse(box)
    painter.setPen(QPen(theme.color("accent"), stroke))
    # the CSS colours the ring's top and right sides: ten-thirty round to four-thirty
    painter.drawArc(box, round((135 - angle) * 16), -round(360 * theme.ACTIVITY_SPIN_ARC * 16))
    painter.restore()


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
    than the text, `thumb_source(item)` for a thumb column's folder,
    `row_dimmed(item)` for rows drawn faint, and `row_tone(item)` for a row
    marked in a hue ("ok": the run that just finished cleanly).

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
        """A cell's content: text, a cell dataclass or None."""
        return item[column]

    def sort_key(self, item, column: int):
        """What a column sorts by. The text, unless overridden with a number."""
        return _text_of(self.cell(item, column))

    def thumb_source(self, item) -> str | None:
        """The wallpaper folder (or preview file) a thumb column shows."""
        return None

    def row_dimmed(self, item) -> bool:
        return False

    def row_tone(self, item) -> str | None:
        """A hue the row is marked in, as a selected row is in accent: its soft
        ground and its edge down the left. "ok", "warn", "danger" or None."""
        return None

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

class RowTiles:
    """Rows drawn once into a pixmap and copied after, newest kept, up to a
    budget of pixels rather than a count: a row's tile is as wide as the view.

    Drawing a row is about forty calls into Qt, and PySide gives the GIL up on
    every one, so with other Python threads busy each call can wait for it:
    measured with two busy threads (tests/perf_pages.py), the Tracker's
    playlist scrolled at 40 ms a step and Review's authors at 47, against 6
    and 3 uncontended. A copy is one call. The key is everything that decides
    how the row looks, so a tile is never stale, only unused.
    """

    BUDGET = 24 * 1024 * 1024       # bytes: about three screens of rows at 2x

    def __init__(self, budget: int = BUDGET):
        self.budget = budget
        self._tiles: OrderedDict[Hashable, QPixmap] = OrderedDict()
        self._bytes = 0
        self.drawn = 0              # tiles made, for tests and measurements

    def get(self, key: Hashable, width: int, height: int, dpr: float,
            draw: Callable[[QPainter], None]) -> QPixmap:
        tile = self._tiles.get(key)
        if tile is not None:
            self._tiles.move_to_end(key)
            return tile
        tile = QPixmap(max(1, round(width * dpr)), max(1, round(height * dpr)))
        tile.setDevicePixelRatio(dpr)
        tile.fill(Qt.transparent)
        painter = QPainter(tile)
        try:
            draw(painter)
        finally:
            painter.end()
        self.drawn += 1
        self._tiles[key] = tile
        self._bytes += tile.width() * tile.height() * 4
        while self._bytes > self.budget and len(self._tiles) > 1:
            _, old = self._tiles.popitem(last=False)
            self._bytes -= old.width() * old.height() * 4
        return tile

    def clear(self) -> None:
        self._tiles.clear()
        self._bytes = 0

    def __len__(self) -> int:
        return len(self._tiles)


_UNSET = object()


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
        self._buttons: dict[tuple, tuple[float, float]] = {}
        self._font = None
        self._dpr = 1.0
        self._row = -1
        self.tiles = RowTiles()

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
        """One whole row into `rect` (the Table's own paint goes to `paint_row_at`)."""
        self.paint_row_at(painter, self._table.model(), rect.x(), rect.y(), rect.width(),
                          rect.height(), row, selected=selected, hovered=hovered,
                          focused=focused, spans=spans,
                          spans_key=(tuple(spans), tuple(self._table.model().columns)),
                          enabled=self._table.isEnabled())

    def paint_row_at(self, painter: QPainter, model: "TableModel", x: int, y: int, width: int,
                     height: int, row: int, *, selected: bool, hovered: bool, focused: bool,
                     spans: Sequence[tuple[float, float]], spans_key: tuple,
                     enabled: bool) -> None:
        """One whole row, as a tile copied to (x, y). Everything here bar the copy
        is Python: under contention it is the calls into Qt that wait (`RowTiles`)."""
        item = model.item_at(row)
        if item is None:
            group, note = model.group_at(row), model.group_note(row)
            if group is None:
                return
            tile = self.tiles.get(("group", group, note, width, height, self._dpr), width,
                                  height, self._dpr, lambda p: self._paint_group_tile(
                                      p, width, height, group, note))
            painter.drawPixmap(x, y, tile)
            return
        self._row = row
        table = self._table
        tone = None if selected else model.row_tone(item)
        fill = ("accent.soft" if selected else f"{tone}.soft" if tone
                else "surface.raised" if hovered
                else "surface.zebra" if model.zebra(row) else None)
        edge = (tone or "accent") if selected or tone else None
        faded = (theme.DISABLED_FIELD_OPACITY if not enabled
                 else theme.DIM_OPACITY if model.row_dimmed(item) else None)
        columns = model.columns
        values = tuple(model.cell(item, c) for c in range(len(columns)))
        if table.spins(row):
            # A spinner or a sweeping bar moves every frame: no tile would last.
            self._paint_whole(painter, QRect(x, y, width, height), model, item, values, spans,
                              fill, edge, focused, faded)
            return
        looks = tuple(self._look(row, c, value) for c, value in enumerate(values)
                      if isinstance(value, (ButtonCell, ButtonsCell)))
        thumbs = tuple(table.thumb_key(model.thumb_source(item), table.thumb_size(column))
                       for column in columns if column.thumb)
        key = (values, looks, thumbs, spans_key, width, height, self._dpr, fill, edge,
               focused, faded)
        tile = self.tiles.get(key, width, height, self._dpr, lambda p: self._paint_whole(
            p, QRect(0, 0, width, height), model, item, values, spans, fill, edge, focused,
            faded))
        painter.drawPixmap(x, y, tile)
        if table.playing():
            self._paint_frames(painter, model, item, x, y, height, spans, faded)

    def _paint_frames(self, painter: QPainter, model: "TableModel", item, x: int, y: int,
                      height: int, spans, faded) -> None:
        """An animated preview's frame, over the still its row's tile holds."""
        table = self._table
        source = model.thumb_source(item)
        for c, column in enumerate(model.columns):
            if not column.thumb or spans[c][1] <= 0:
                continue
            size = table.thumb_size(column)
            frame = table.frame_tile(source, size)
            if frame is None:
                continue
            h = theme.THUMB[size][1]
            if faded is not None:
                painter.setOpacity(faded)
            painter.drawPixmap(QPointF(x + spans[c][0], y + height / 2 - h / 2), frame)
            if faded is not None:
                painter.setOpacity(1.0)

    def _look(self, row: int, c: int, value):
        """How a cell that answers the pointer looks on this row now: part of its key."""
        if isinstance(value, ButtonCell):
            return self._table.button_look(row, c, value)
        return tuple(self._table.action_look(row, c, button)
                     if button is not None and not button.mark else None
                     for button in value.buttons)

    def _paint_group_tile(self, painter: QPainter, width: int, height: int, group: Group,
                          note: str) -> None:
        self._font = None
        self.paint_group(painter, QRectF(0, 0, width, height), group, note)
        self._font = None

    def _paint_whole(self, painter: QPainter, rect: QRect, model: TableModel, item,
                     values: Sequence, spans, fill, edge, focused: bool, faded) -> None:
        """The row as it always was drawn: its ground, then each cell."""
        self._font = None               # a tile is a painter of its own
        if fill:
            painter.fillRect(rect, self.colour(fill))
        if edge:
            painter.fillRect(rect.x(), rect.y(), theme.TABLE_SELECT_EDGE, rect.height(),
                             self.colour(edge))
        if focused:
            paint_row_ground(painter, QRectF(rect), focused=True)
            self._font = None
        if faded is not None:
            painter.setOpacity(faded)
        top, height = rect.y(), rect.height()
        for c, column in enumerate(model.columns):
            left, width = spans[c]
            if width > 0:
                self.paint_cell(painter, left, top, width, height, model, item, c, column,
                                values[c])
        if faded is not None:
            painter.setOpacity(1.0)
        self._font = None

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

    def createEditor(self, parent, option, index):
        model = index.model()
        value = model.cell(model.item_at(index.row()), index.column())
        if isinstance(value, SpinCell) and value.enabled:
            editor = SpinBox(parent, minimum=value.minimum, maximum=value.maximum, value=value.value)
            editor.setAccessibleName(model.columns[index.column()].title)
            return editor
        return None

    def setEditorData(self, editor, index):
        editor.setValue(int(index.data(Qt.EditRole)))
        editor.selectAll()

    def setModelData(self, editor, model, index):
        editor.interpretText()
        model.setData(index, editor.value(), Qt.EditRole)

    def paint_cell(self, painter: QPainter, left: float, top: float, width: float,
                   height: float, model: TableModel, item, c: int, column: Column,
                   value=_UNSET) -> None:
        if value is _UNSET:
            value = model.cell(item, c)
        x, right = left, left + width
        middle = top + height / 2
        if column.thumb:
            size = self._table.thumb_size(column)
            w, h, _ = theme.THUMB[size]
            painter.drawPixmap(QPointF(x, middle - h / 2),
                               self._table.thumb_tile(model.thumb_source(item), size))
            x += w + theme.THUMB_GAP
        if column.icon:
            size = theme.TABLE_ICON
            painter.drawPixmap(QPointF(x, middle - size / 2),
                               icons.pixmap(column.icon, "text.lo", size, self._dpr))
            x += size + theme.TABLE_ICON_GAP
        if right <= x:
            return
        align = ALIGNMENTS[column.align]
        if isinstance(value, ProgressCell):
            caption_h = theme.line_height("type.monoXs") + theme.SP_4 if value.caption else 0
            bar = QRectF(x, middle - (theme.TABLE_PROGRESS_HEIGHT + caption_h) / 2,
                         right - x, theme.TABLE_PROGRESS_HEIGHT)
            painter.setPen(Qt.NoPen)
            painter.setBrush(self.colour("surface.well"))
            painter.drawRoundedRect(bar, theme.TABLE_PROGRESS_HEIGHT / 2, theme.TABLE_PROGRESS_HEIGHT / 2)
            if value.busy or value.fraction:
                fill = QRectF(bar)
                if value.busy:
                    travel = animations.loop("spin").phase() if self._table.spins(self._row) else 0
                    fill.setWidth(bar.width() * theme.TABLE_PROGRESS_SWEEP)
                    fill.moveLeft(x + (bar.width() - fill.width()) * travel)
                else:
                    fill.setWidth(bar.width() * max(0, min(1, value.fraction)))
                painter.setBrush(self.colour(value.tone))
                painter.drawRoundedRect(fill, theme.TABLE_PROGRESS_HEIGHT / 2, theme.TABLE_PROGRESS_HEIGHT / 2)
            if value.caption:
                self._text(painter, QRectF(x, bar.bottom() + theme.SP_4, right - x, caption_h),
                           value.caption, "type.monoXs", value.tone if value.tone == "danger" else "text.lo", Qt.AlignLeft)
            return
        if isinstance(value, SpinCell):
            box = QRectF(x, middle - theme.CONTROL_HEIGHT / 2, right - x, theme.CONTROL_HEIGHT)
            painter.setPen(QPen(self.colour("border.control"), 1))
            painter.setBrush(self.colour("surface.well"))
            painter.drawRoundedRect(box, theme.R_MD, theme.R_MD)
            self._text(painter, box.adjusted(theme.SP_8, 0, -theme.SP_16, 0), str(value.value),
                       "type.monoSm", "text.body" if value.enabled else "text.lo", Qt.AlignLeft)
            side = theme.TABLE_SORT_ICON
            for glyph, y in (("chevU", middle - side), ("chevD", middle)):
                painter.drawPixmap(QPointF(right - side - theme.SP_2, y),
                                   icons.pixmap(glyph, "text.lo", side, self._dpr))
            return
        if isinstance(value, (TagsCell, CheckCell)):
            tile = (tags_pixmap(value.tags, value.tone, right - x, self._dpr)
                    if isinstance(value, TagsCell)
                    else check_pixmap(value.checked, value.enabled, self._dpr))
            size = tile.deviceIndependentSize()
            cell_left = (right - size.width() if align == Qt.AlignRight
                         else (x + right - size.width()) / 2 if align == Qt.AlignHCenter else x)
            painter.drawPixmap(QPointF(cell_left, middle - size.height() / 2), tile)
            return
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
        if isinstance(value, ButtonCell):
            box = self.button_box(value, x, right, middle, align)
            painter.drawPixmap(box.topLeft(), button_pixmap(
                value.text, *self._table.button_look(self._row, c, value), dpr=self._dpr))
            return
        if isinstance(value, ButtonsCell):
            for button, box in self.buttons_boxes(value, x, right, middle, align):
                if button.mark:
                    glyph = theme.ICON_BUTTON[value.size][1]
                    painter.drawPixmap(
                        QPointF(box.center().x() - glyph / 2, box.center().y() - glyph / 2),
                        icons.pixmap(button.icon, button.tone, glyph, self._dpr))
                    continue
                state, ink = self._table.action_look(self._row, c, button)
                painter.drawPixmap(box.topLeft(), icon_button_pixmap(
                    button.icon, state, value.size, self._dpr, ink=ink))
            return
        if isinstance(value, DiscCell):
            size = theme.TABLE_DISC
            left = (right - size if align == Qt.AlignRight
                    else (x + right - size) / 2 if align == Qt.AlignHCenter else x)
            painter.drawPixmap(QPointF(left, middle - size / 2),
                               disc_pixmap(value.icon, value.tone, size, self._dpr))
            return
        box = QRectF(x, top, right - x, height)
        if isinstance(value, BusyCell):
            size = theme.TABLE_SPINNER
            font, metrics = self._fonts.get(column.type_token())
            width = size + theme.TABLE_ICON_GAP + metrics.horizontalAdvance(value.text)
            left = (right - width if align == Qt.AlignRight
                    else (x + right - width) / 2 if align == Qt.AlignHCenter else x)
            paint_spinner(painter, QRectF(left, middle - size / 2, size, size),
                          animations.loop("spin").value() if self._table.spins(self._row) else 0)
            self._font = None
            box.setLeft(left + size + theme.TABLE_ICON_GAP)
            self._text(painter, box, value.text, column.type_token(), value.tone, Qt.AlignLeft)
            return
        if isinstance(value, Cell):
            tone = value.tone or column.tone
            if value.icon:
                size = theme.TABLE_ICON
                painter.drawPixmap(QPointF(x, middle - size / 2),
                                   icons.pixmap(value.icon, value.icon_tone or tone, size, self._dpr))
                box.setLeft(x + size + theme.TABLE_ICON_GAP)
            if value.sub:
                token = column.type_token()
                main = self._fonts.get(token, value.strong)[1].height()
                sub = self._fonts.get("type.monoXs")[1].height()
                first = middle - (main + theme.TABLE_SUB_GAP + sub) / 2
                self._text(painter, QRectF(box.left(), first, box.width(), main), value.text,
                           token, tone, align, column.elide, value.strong)
                self._text(painter, QRectF(box.left(), first + main + theme.TABLE_SUB_GAP,
                                           box.width(), sub),
                           value.sub, "type.monoXs", value.sub_tone or "text.lo", align, "right")
                return
            self._text(painter, box, value.text, column.type_token(),
                       tone, align, column.elide, value.strong)
        elif value is not None:
            self._text(painter, box, str(value), column.type_token(), column.tone,
                       align, column.elide)

    def button_box(self, value: ButtonCell, left: float, right: float, middle: float,
                   align) -> QRectF:
        """Where a ButtonCell's button sits in a cell spanning left to right."""
        key = (value.text, "md")
        size = self._buttons.get(key)
        if size is None:
            box = button_size(value.text)
            size = self._buttons[key] = (box.width(), box.height())
        width, height = size
        x = (right - width if align == Qt.AlignRight
             else (left + right - width) / 2 if align == Qt.AlignHCenter else left)
        return QRectF(x, middle - height / 2, width, height)

    @staticmethod
    def buttons_boxes(value: ButtonsCell, left: float, right: float, middle: float,
                      align) -> list[tuple[CellButton, QRectF]]:
        """Where each of a ButtonsCell's buttons sits in a cell spanning left to
        right; empty slots keep their room and are left out."""
        side = theme.ICON_BUTTON[value.size][0]
        width = value.width()
        x = (right - width if align == Qt.AlignRight
             else (left + right - width) / 2 if align == Qt.AlignHCenter else left)
        out = []
        for button in value.buttons:
            if button is not None:
                out.append((button, QRectF(x, middle - side / 2, side, side)))
            x += side + theme.TABLE_BUTTONS_GAP
        return out

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
        self._row = index.row()
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
            lit = (c == sorted_by or (column.sortable and c == self._hot)
                   or (column.natural and sorted_by < 0))
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
    and again the other way; a `natural` column's title goes back to the
    order the rows were given in. The selection follows its items. Thumbnails are
    asked for once scrolling settles, for the rows on screen only.
    """

    THUMB_SETTLE = 90               # ms of stillness before thumbnails are asked for
    THUMB_CACHE = 400               # pixmaps kept; a screen shows a few dozen
    # Animated previews playing at once. A table of 200 px thumbs shows four or
    # five rows; the gallery, thirty to a page, found eight the most worth it.
    MAX_PLAYERS = 8
    ANIMATION_CACHE = 24            # GIFs' bytes kept, for rows scrolled back to

    sort_changed = Signal(int, object)      # column, Qt.SortOrder
    button_clicked = Signal(int, int)       # row, column: a ButtonCell was clicked
    action_clicked = Signal(int, int, str)  # row, column, key: a ButtonsCell's button

    def __init__(self, parent: QWidget | None = None, *, loader: ThumbLoader | None = None):
        super().__init__(parent)
        self._delegate = RowDelegate(self)
        self.setItemDelegate(self._delegate)
        self.setHorizontalHeader(TableHeader(self))
        self.horizontalHeader().setAccessibleName("Column titles")
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
        # Tab goes on to the next control; the arrows move inside. QTableView's
        # own Tab walks the cells, and a keyboard never got out of a table.
        self.setTabKeyNavigation(False)
        self.horizontalHeader().sectionClicked.connect(self._header_clicked)
        self.horizontalHeader().sectionResized.connect(self._spans_changed)
        self.horizontalHeader().geometriesChanged.connect(self._spans_changed)

        self._row_height: int | None = None
        # The size the stretched thumb column draws while too narrow for its own
        # (theme.THUMB_NARROWER); None draws its own.
        self._narrow: str | None = None
        self._hover = -1
        self._keyboard = False
        self._spans: list[tuple[float, float]] | None = None
        # the cell button under the pointer, and the one held: (row, column, key),
        # the key None for a ButtonCell
        self._hot_button: tuple[int, int, str | None] | None = None
        self._pressed_button: tuple[int, int, str | None] | None = None
        self._spinning: set[int] = set()
        self._ticker: animations.LoopTicker | None = None

        self._loader = loader or ThumbLoader(self)
        self._loader.local_done.connect(self._thumb_arrived)
        self._loader.animation_done.connect(self._animation_arrived)
        # key → the GIF's bytes, empty when its preview is not animated
        self._gifs: OrderedDict[str, QByteArray] = OrderedDict()
        # keys whose still came from a GIF: the only ones whose bytes are asked for
        self._animatable: set[str] = set()
        self._players: dict[str, tuple[QMovie, QBuffer]] = {}
        self._frames: dict[tuple[str, str], QPixmap] = {}    # (key, size) → frame tile
        self._frame_images: dict[str, QPixmap] = {}         # key → the frame playing
        self._pixmaps: OrderedDict[str, QPixmap | None] = OrderedDict()
        self._tiles: dict[tuple[str, str], QPixmap] = {}
        self._thumb_gen: dict[str, int] = {}    # pictures arrived per source: part of a row's key
        # Read on every row painted, so kept here rather than asked of Qt each time.
        self._enabled = True
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

    def refresh_columns(self) -> None:
        """The model's columns changed their widths (a chip column widened for
        a longer chip): lay the header out again."""
        self._apply_columns()
        self.horizontalHeader().viewport().update()
        self.viewport().update()

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
        self._check_narrow()
        self.viewport().update()

    def thumb_size(self, column: Column) -> str | None:
        """The thumb a column draws: its own, or the next one down while it is
        the stretched column and too narrow to leave its words
        `theme.TABLE_WORDS_MIN`. Pictures are still asked for at the column's
        own size, so stepping back up is never blurred."""
        if self._narrow and column.width is None and column.thumb in theme.THUMB_NARROWER:
            return self._narrow
        return column.thumb

    def narrow(self) -> bool:
        return self._narrow is not None

    def _check_narrow(self) -> None:
        """Step the stretched thumb column's thumb down (THUMB_NARROWER, as far
        as it goes) until its words keep `theme.TABLE_WORDS_MIN`."""
        model = self.model()
        if not isinstance(model, TableModel):
            return
        header = self.horizontalHeader()
        narrow = self._narrow
        # Counted as if the scroll bar were there: the shorter rows can take it
        # away, which must not make the rows tall again (and the bar come back).
        bar = self.verticalScrollBar()
        missing = 0 if bar.isVisible() else bar.sizeHint().width()
        for c, column in enumerate(model.columns):
            if column.width is None and column.thumb in theme.THUMB_NARROWER and c < header.count():
                left, right = self._pads(c, len(model.columns) - 1)
                room = header.sectionSize(c) - missing - left - right - theme.THUMB_GAP
                size = column.thumb
                while (room - theme.THUMB[size][0] < theme.TABLE_WORDS_MIN
                       and size in theme.THUMB_NARROWER):
                    size = theme.THUMB_NARROWER[size]
                narrow = None if size == column.thumb else size
                break
        if narrow != self._narrow:
            self._narrow = narrow
            self._resize_rows()

    def _model_reset(self) -> None:
        self._hover = -1
        self._hot_button = self._pressed_button = None
        self._rows_for_key = {}
        self._resize_rows()
        self.horizontalHeader().viewport().update()
        self._settle.start()

    def _resize_rows(self) -> None:
        header = self.verticalHeader()
        header.setDefaultSectionSize(self.row_height())
        model: TableModel = self.model()
        group = self.group_row_height()
        for row in model.group_rows():
            header.resizeSection(row, group)

    # -- sizes

    def row_height(self) -> int:
        if self._row_height is not None:
            return self._row_height
        model = self.model()
        tallest = [theme.CHIP_HEIGHT, math.ceil(theme.line_height("type.bodySm"))]
        pad = theme.TABLE_ROW_PAD_TEXT
        if isinstance(model, TableModel):
            thumbs = [theme.THUMB[self.thumb_size(c)][1] for c in model.columns if c.thumb]
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
        if model.columns[column].natural:
            # the order the rows came in: back to it, whatever was sorted
            if model.sort_column() >= 0:
                self.sort_by(-1)
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

    def thumb_key(self, source, size: str | None) -> tuple:
        """What decides how a row's thumb looks, for its tile's key: the source,
        the size, and the state of its picture with how many times it came. A
        playing preview's frames are drawn over the tile, never into it."""
        key = str(source) if source else None
        state = self.thumb_state(source)[0]
        return key, size, state, self._thumb_gen.get(key, 0) if state == "image" else 0

    # -- animated previews

    def playing(self) -> bool:
        """Whether any preview is playing (a row then draws its frame)."""
        return bool(self._frame_images)

    def players(self) -> list[str]:
        """The sources whose previews are playing now."""
        return list(self._players)

    def frame_tile(self, source, size: str) -> QPixmap | None:
        """The frame an animated preview is on, ready to copy; None when it is
        not playing (the still in the row's tile shows)."""
        key = str(source) if source else ""
        image = self._frame_images.get(key)
        if image is None:
            return None
        tile = self._frames.get((key, size))
        if tile is None:
            tile = self._frames[(key, size)] = thumb_tile(image, "image", size,
                                                          self.devicePixelRatioF())
        return tile

    def _animates(self) -> bool:
        window = self.window()
        return (animations.ENABLED and self.isVisible()
                and not (window is not None and window.isMinimized()))

    def _play_visible(self) -> None:
        """Play the animated previews of the rows on screen, up to MAX_PLAYERS;
        stop the rest. Asks for the bytes of those not read yet."""
        wanted: list[str] = []
        if self._animates():
            for key in self._rows_for_key:
                if key not in self._animatable:
                    continue
                data = self._gifs.get(key)
                if data is None:
                    self._loader.request_animation(key, key)
                elif not data.isEmpty():
                    wanted.append(key)
        wanted = wanted[:self.MAX_PLAYERS]
        for key in [k for k in self._players if k not in wanted]:
            self._stop_player(key)
        for key in wanted:
            if key not in self._players:
                self._start_player(key)

    def stop_players(self) -> None:
        for key in list(self._players):
            self._stop_player(key)

    def _animation_arrived(self, key: str, data) -> None:
        self._gifs[key] = data
        self._gifs.move_to_end(key)
        while len(self._gifs) > self.ANIMATION_CACHE:
            gone, _ = self._gifs.popitem(last=False)
            if gone in self._players:
                self._stop_player(gone)
        if key in self._rows_for_key and not data.isEmpty():
            self._play_visible()

    def _start_player(self, key: str) -> None:
        buffer = QBuffer(self)
        buffer.setData(self._gifs[key])
        buffer.open(QIODevice.ReadOnly)
        movie = QMovie(buffer, QByteArray(), self)
        movie.setCacheMode(QMovie.CacheNone)
        if not movie.isValid() or movie.frameCount() == 1 or not movie.jumpToFrame(0):
            # one frame is a still, and the still is shown already
            buffer.close()
            movie.deleteLater()
            buffer.deleteLater()
            self._gifs[key] = QByteArray()
            return
        model = self.model()
        column = next(c for c in model.columns if c.thumb)
        width, height, _ = theme.THUMB[column.thumb]
        dpr = self.devicePixelRatioF()
        box = QSize(math.ceil(width * dpr), math.ceil(height * dpr))
        first = movie.currentImage().size()
        if first.isValid() and not first.isEmpty():
            fit = (Qt.KeepAspectRatioByExpanding if column.thumb in theme.THUMB_COVER
                   else Qt.KeepAspectRatio)
            scaled = first.scaled(box, fit)
            if scaled.width() < first.width():
                movie.setScaledSize(scaled)     # decoded big, kept small
        movie.frameChanged.connect(lambda _n, k=key: self._next_frame(k))
        self._players[key] = (movie, buffer)
        movie.start()

    def _stop_player(self, key: str) -> None:
        movie, buffer = self._players.pop(key)
        movie.stop()
        movie.deleteLater()
        buffer.close()
        buffer.deleteLater()
        self._frame_images.pop(key, None)
        for stale in [k for k in self._frames if k[0] == key]:
            del self._frames[stale]
        for row in self._rows_for_key.get(key, ()):
            self._update_row(row)

    def _next_frame(self, key: str) -> None:
        player = self._players.get(key)
        if player is None:
            return
        image = player[0].currentImage()
        if image.isNull():
            return
        frame = QPixmap.fromImage(image)
        frame.setDevicePixelRatio(self.devicePixelRatioF())
        self._frame_images[key] = frame
        for stale in [k for k in self._frames if k[0] == key]:
            del self._frames[stale]
        for row in self._rows_for_key.get(key, ()):
            self._update_row(row)

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
        cover = column.thumb in theme.THUMB_COVER
        for key in wanted:
            if key in self._pixmaps:
                self._pixmaps.move_to_end(key)
            else:
                self._loader.request_local(key, key, box, cover)
        self._play_visible()

    def _thumb_arrived(self, key: str, image) -> None:
        pixmap = None
        if image.text(PREVIEW_KEY).lower().endswith(".gif"):
            self._animatable.add(key)
            if key in self._rows_for_key:
                self._play_visible()
        if not image.isNull():
            pixmap = QPixmap.fromImage(image)
            pixmap.setDevicePixelRatio(self.devicePixelRatioF())
        self._pixmaps[key] = pixmap
        self._pixmaps.move_to_end(key)
        self._thumb_gen[key] = self._thumb_gen.get(key, 0) + 1
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

    def showEvent(self, event) -> None:         # noqa: N802 - Qt's name
        super().showEvent(event)
        if self._gifs:
            self._settle.start()        # what is on screen plays again

    def hideEvent(self, event) -> None:         # noqa: N802 - Qt's name
        super().hideEvent(event)
        self.stop_players()

    # -- buttons in cells

    def _under(self, pos) -> tuple[int, int, object, QRectF] | None:
        """The button under a viewport point: (row, column, the ButtonCell or
        the ButtonsCell's CellButton, its box), or None."""
        model = self.model()
        point = QPointF(pos)
        row = self.rowAt(round(point.y()))
        item = model.item_at(row) if isinstance(model, TableModel) else None
        if item is None:
            return None
        middle = self.rowViewportPosition(row) + self.rowHeight(row) / 2
        for c, (left, width) in enumerate(self.column_spans()):
            if width <= 0:
                continue
            value = model.cell(item, c)
            align = ALIGNMENTS[model.columns[c].align]
            if isinstance(value, ButtonCell):
                box = self._delegate.button_box(value, left, left + width, middle, align)
                if box.contains(point):
                    return row, c, value, box
            elif isinstance(value, ButtonsCell):
                for button, box in self._delegate.buttons_boxes(value, left, left + width,
                                                                middle, align):
                    if box.contains(point):
                        return row, c, button, box
        return None

    def _spot_at(self, pos) -> tuple[int, int, str | None] | None:
        """What a click under a viewport point would press: a ButtonCell's
        (row, column, None), or a ButtonsCell button's (row, column, key) when
        it is enabled and not a mark."""
        found = self._under(pos)
        if found is None:
            return None
        row, column, value, _ = found
        if isinstance(value, ButtonCell):
            return row, column, None
        if value.enabled and not value.mark:
            return row, column, value.key
        return None

    def button_at(self, pos) -> tuple[int, int] | None:
        """The (row, column) of the ButtonCell under a viewport point, or None."""
        spot = self._spot_at(pos)
        return spot[:2] if spot is not None and spot[2] is None else None

    def action_at(self, pos) -> tuple[int, int, str] | None:
        """The (row, column, key) of the ButtonsCell button a click under a
        viewport point would press, or None: nothing there, an empty slot, a
        mark or a button that is off."""
        spot = self._spot_at(pos)
        return spot if spot is not None and spot[2] is not None else None

    def button_look(self, row: int, column: int, value: ButtonCell) -> tuple[str, str]:
        """(variant, state) a ButtonCell is drawn in: its hot variant on the row
        under the pointer, hover under the pointer, pressed while held."""
        variant = value.hot if value.hot and row == self._hover else value.variant
        if not self._enabled:
            return variant, "disabled"
        here = (row, column, None)
        if self._pressed_button == here:
            return variant, "pressed" if self._hot_button == here else "hover"
        return variant, "hover" if self._hot_button == here else "default"

    def action_look(self, row: int, column: int, button: CellButton) -> tuple[str, str | None]:
        """(state, ink) a ButtonsCell button is drawn in: its glyph in text.lo
        at rest, the IconButton's own look on the row under the pointer, hover
        under the pointer, pressed while held, faded when off."""
        if not self._enabled or not button.enabled:
            return "disabled", None
        here = (row, column, button.key)
        if self._pressed_button == here:
            return ("pressed" if self._hot_button == here else "hover"), None
        if self._hot_button == here:
            return "hover", None
        return "default", None if row == self._hover else "text.lo"

    def _set_hot_button(self, hot: tuple[int, int, str | None] | None) -> None:
        if hot != self._hot_button:
            old, self._hot_button = self._hot_button, hot
            for spot in (old, hot):
                if spot is not None:
                    self._update_row(spot[0])
            self.viewport().setCursor(Qt.PointingHandCursor if hot else Qt.ArrowCursor)

    # -- rows whose work turns a spinner

    def set_spinning(self, items: Iterable[int]) -> None:
        """The items (by index in the model) whose BusyCell turns. The shared
        "spin" clock repaints those rows only, and stops when there are none."""
        old, self._spinning = self._spinning, set(items)
        if self._spinning and self._ticker is None:
            self._ticker = animations.LoopTicker(self.viewport(), self._spin_tick)
        if self._ticker is not None:
            if self._spinning:
                self._ticker.start("spin")
            else:
                self._ticker.stop()
        model = self.model()
        if isinstance(model, TableModel):
            for index in old ^ self._spinning:
                self._update_row(model.row_of_item(index))

    def spins(self, row: int) -> bool:
        model = self.model()
        return bool(self._spinning) and isinstance(model, TableModel)             and model.item_index(row) in self._spinning

    def _spin_tick(self) -> None:
        model = self.model()
        if isinstance(model, TableModel):
            for index in self._spinning:
                self._update_row(model.row_of_item(index))

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
            self._set_hot_button(None)
        if kind == QEvent.ToolTip:
            found = self._under(event.pos())
            if found is not None and isinstance(found[2], CellButton):
                QToolTip.showText(event.globalPos(), found[2].tip, self.viewport(),
                                  found[3].toAlignedRect())
                return True
        return super().viewportEvent(event)

    def mouseMoveEvent(self, event) -> None:    # noqa: N802 - Qt's name
        self._hover_to(self.rowAt(event.position().toPoint().y()))
        self._set_hot_button(self._spot_at(event.position()))
        if event.buttons() != Qt.NoButton and self._pressed_button is None:
            super().mouseMoveEvent(event)       # a drag that extends the selection

    def mousePressEvent(self, event) -> None:   # noqa: N802 - Qt's name
        self._keyboard = False
        if self._press_button(event):
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:     # noqa: N802 - Qt's name
        # A double-click's second press on a cell's button presses it again, as
        # it would a QPushButton; it does not activate the row behind it.
        if self._press_button(event):
            return
        super().mouseDoubleClickEvent(event)

    def _press_button(self, event) -> bool:
        hit = self._spot_at(event.position()) if event.button() == Qt.LeftButton else None
        if hit is None:
            return False
        self._pressed_button = self._hot_button = hit
        self._update_row(hit[0])
        event.accept()
        return True

    def mouseReleaseEvent(self, event) -> None:     # noqa: N802 - Qt's name
        pressed, self._pressed_button = self._pressed_button, None
        if pressed is not None:
            self._update_row(pressed[0])
            if event.button() == Qt.LeftButton and self._spot_at(event.position()) == pressed:
                row, column, key = pressed
                if key is None:
                    self.button_clicked.emit(row, column)
                else:
                    self.action_clicked.emit(row, column, key)
            event.accept()
            return
        super().mouseReleaseEvent(event)

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

    def changeEvent(self, event) -> None:       # noqa: N802 - Qt's name
        if event.type() == QEvent.EnabledChange:
            self._enabled = self.isEnabled()
        super().changeEvent(event)

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
        # What every row needs from Qt, asked once: each call gives the GIL up,
        # and the rows are then a copy each (RowTiles).
        spans = self.column_spans()
        # Where each column is and what it is: with the cells, all a row's look.
        spans_key = (tuple(spans), tuple(model.columns))
        selected = {index.row() for index in self.selectionModel().selectedRows()}
        current = self.currentIndex().row() if self.focus_visible() else -1
        width = self.viewport().width()
        header = self.verticalHeader()
        normal, group = header.defaultSectionSize(), self.group_row_height()
        groups = set(model.group_rows())
        y = self.rowViewportPosition(first)
        delegate = self._delegate
        delegate.begin(self.devicePixelRatioF())
        enabled = self._enabled = self.isEnabled()
        hover = self._hover
        for row in range(first, last + 1):
            height = group if row in groups else normal
            delegate.paint_row_at(painter, model, 0, y, width, height, row,
                                  selected=row in selected, hovered=row == hover,
                                  focused=row == current, spans=spans, spans_key=spans_key,
                                  enabled=enabled)
            y += height


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
    room without drawing one, so a column of titles stays aligned.

    `title_note` follows the title in text.lo ("was Old Name"); `tick` ends
    the row with an ok check (an author gone through), and `dimmed` sets such
    a row back: its title in text.mid, the row at LIST_ROW_DIM. `meta_tone`
    colours the meta line (a warning). `thumb_size` is the thumb's side; 0 is
    LIST_ROW_THUMB. `chips_inline` draws the chips right after the title, on
    its line, instead of at the row's right: the meta line under them then
    has the row's whole width."""
    title: str
    meta: str = ""
    when: str = ""
    icon: str | None = None
    icon_tone: str = "text.mid"
    thumb: bool = False
    chips: tuple = ()
    trailing: str = ""
    title_note: str = ""
    tick: bool = False
    dimmed: bool = False
    meta_tone: str = "text.lo"
    thumb_size: int = 0
    chips_inline: bool = False


def list_row_height(row: ListRow | None = None) -> int:
    lines = theme.line_height("type.bodySm")
    if row is None or row.meta:
        lines += theme.SP_2 + theme.line_height("type.monoXs")
    side = (row.thumb_size or theme.LIST_ROW_THUMB) if row is not None else theme.LIST_ROW_THUMB
    inner = max(math.ceil(lines), side if row is None or row.thumb else 0)
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
    elif row.dimmed:
        painter.setOpacity(theme.LIST_ROW_DIM)
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
        side = row.thumb_size or theme.LIST_ROW_THUMB
        paint_thumb(painter, QRectF(x, middle - side / 2, side, side), pixmap,
                    "image" if pixmap is not None else "loading", theme.R_SM + 1)
        x += side + gap
    if row.tick:
        size = theme.LIST_ROW_TICK
        painter.drawPixmap(QPointF(right - size, middle - size / 2),
                           icons.pixmap("check", "ok", size, dpr))
        right -= size + gap
    if row.trailing:
        width = _draw_text(painter, _list_fonts, QRectF(x, rect.top(), right - x, rect.height()),
                           row.trailing, "type.monoSm", "text.lo", Qt.AlignRight)
        right -= width + gap
    chip_state = "disabled" if state == "disabled" else "default"
    ring = theme.FOCUS_RING
    if not row.chips_inline:
        for variant, text in reversed(row.chips):
            size = chip_size(variant, text)
            left = right - size.width()
            painter.drawPixmap(QPointF(left - ring, middle - size.height() / 2 - ring),
                               chip_pixmap(variant, text, chip_state, dpr))
            right = left - theme.SP_6
        right -= gap - theme.SP_6 if row.chips else 0
    title_h = theme.line_height("type.bodySm")
    meta_h = theme.line_height("type.monoXs") if row.meta else 0.0
    block = title_h + (theme.SP_2 + meta_h if row.meta else 0.0)
    top = middle - block / 2
    title_tone = "text.lo" if state == "disabled" else "text.mid" if row.dimmed else "text.body"
    inline = 0.0
    if row.chips_inline and row.chips:
        inline = sum(chip_size(v, t).width() + theme.SP_6 for v, t in row.chips)
    title_right = max(x, right - inline)
    used = _draw_text(painter, _list_fonts, QRectF(x, top, title_right - x, title_h), row.title,
                      "type.bodySm", title_tone, Qt.AlignLeft)
    after = x + used
    if row.chips_inline:
        line_middle = top + title_h / 2
        for variant, text in row.chips:
            size = chip_size(variant, text)
            left = after + theme.SP_6
            painter.drawPixmap(QPointF(left - ring, line_middle - size.height() / 2 - ring),
                               chip_pixmap(variant, text, chip_state, dpr))
            after = left + size.width()
    if row.title_note:
        left = after + theme.SP_6
        if right - left > theme.LIST_ROW_NOTE_MIN:
            _draw_text(painter, _list_fonts, QRectF(left, top, right - left, title_h),
                       row.title_note, "type.bodySm", "text.lo", Qt.AlignLeft)
    if row.meta:
        _draw_text(painter, _list_fonts, QRectF(x, top + title_h + theme.SP_2, right - x, meta_h),
                   row.meta, "type.monoXs", "text.lo" if state == "disabled" else row.meta_tone,
                   Qt.AlignLeft)
    painter.restore()


class ListRowDelegate(QStyledItemDelegate):
    """Draws a list view's rows from `LIST_ROW_ROLE` (a ListRow) and
    `LIST_PIXMAP_ROLE` (its thumb). Hover, selection and keyboard focus come
    from the view. Each row is drawn once into a tile (`RowTiles`) and copied
    after, keyed by the row, its state, its size and its thumb."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tiles = RowTiles()

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
        rect = option.rect
        self.paint_row_at(painter, rect.x(), rect.y(), rect.width(), rect.height(), row, state,
                          pixmap if isinstance(pixmap, QPixmap) else None,
                          painter.device().devicePixelRatioF())

    def paint_row_at(self, painter: QPainter, x: int, y: int, width: int, height: int,
                     row: ListRow, state: str, pixmap: QPixmap | None, dpr: float) -> None:
        """One row, as a tile (`RowTiles`) copied to (x, y)."""
        key = (row, state, width, height, dpr, pixmap.cacheKey() if pixmap is not None else 0)
        tile = self.tiles.get(key, width, height, dpr, lambda p: paint_list_row(
            p, QRectF(0, 0, width, height), row, state, pixmap=pixmap, dpr=dpr))
        painter.drawPixmap(x, y, tile)


class SkeletonRows(QWidget):
    """Rows standing where a list will be: a well and two bars each, as many
    as fit. Live, they shimmer (`anim.shimmer`, each row a little after the
    one above) while the list is being filled; `live=False` draws them still,
    faint and set back — a list that stopped filling. Painted, one shared
    clock: no animation per row (plan §2.4)."""

    def __init__(self, parent: QWidget | None = None, *, live: bool = True):
        super().__init__(parent)
        self._live = False
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setAccessibleName("Loading")
        self.set_live(live)

    def is_live(self) -> bool:
        return self._live

    def set_live(self, on: bool) -> None:
        self._live = bool(on)
        driver = animations.loop("shimmer")
        if self._live:
            driver.subscribe(self)
        else:
            driver.unsubscribe(self)
        self.update()

    @staticmethod
    def row_height() -> int:
        pad = theme.SKELETON_ROW_PAD[0]
        return theme.SKELETON_WELL + 2 * pad

    def rows(self) -> int:
        """How many rows the widget's height holds."""
        return max(0, self.height() // self.row_height())

    def sizeHint(self) -> QSize:                # noqa: N802 - Qt's name
        return QSize(theme.SKELETON_WELL * 6, self.row_height() * 4)

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        driver = animations.loop("shimmer")
        if not self._live:
            painter.setOpacity(theme.SKELETON_STILL_OPACITY)
        pad_v, pad_h = theme.SKELETON_ROW_PAD
        well = theme.SKELETON_WELL
        widths = theme.SKELETON_WIDTHS
        text_w = max(0.0, self.width() - 2 * pad_h - well - theme.SKELETON_GAP)
        block = sum(h for h, _ in theme.SKELETON_BARS) + theme.SKELETON_BAR_GAP
        for i in range(self.rows()):
            top = i * self.row_height() + pad_v
            delay = i * theme.SKELETON_ROW_STAGGER
            box = QRectF(pad_h, top, well, well)
            if self._live:
                painter.setBrush(theme.color("surface.raised", driver.value(delay)))
                painter.drawRoundedRect(box, theme.R_SM + 1, theme.R_SM + 1)
            else:
                paint_thumb(painter, box, None, "loading", theme.R_SM + 1)
                painter.setPen(Qt.NoPen)
            x = box.right() + theme.SKELETON_GAP
            bars = theme.SKELETON_BARS if self._live else theme.SKELETON_BARS[:1]
            y = top + (well - (block if self._live else bars[0][0])) / 2
            for b, (height, radius) in enumerate(bars):
                width = min(1.0, widths[i % len(widths)] if b == 0 else theme.SKELETON_SECOND)
                if self._live:
                    tone = theme.color("surface.raised",
                                       driver.value(delay + b * theme.SKELETON_BAR_STAGGER))
                else:
                    tone = theme.color("surface.still")
                painter.setBrush(tone)
                painter.drawRoundedRect(QRectF(x, y, text_w * width, height), radius, radius)
                y += height + theme.SKELETON_BAR_GAP


class RowList(QListView):
    """A list of ListRows: the author list, recent activity. The rows paint
    their own ground on the card the list sits on; hover is the pointer's
    row, and the focus ring shows when the keyboard moved it.

    It paints its visible rows itself, in one pass, rather than having Qt call
    the delegate once a row: every call from Qt into Python has to win the GIL
    back, and with other threads busy that wait was most of a scroll step. A
    model can hand its rows over without a call into Qt each by having
    `list_item(row) -> (ListRow | None, QPixmap | None, forced state | None)`;
    any other model is read through its roles."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._delegate = ListRowDelegate(self)
        self.setItemDelegate(self._delegate)
        self._hover = -1
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

    # -- painting the rows in one pass

    def hovered_row(self) -> int:
        return self._hover

    def _hover_to(self, row: int) -> None:
        if row == self._hover:
            return
        old, self._hover = self._hover, row
        model = self.model()
        for r in (old, row):
            if model is not None and 0 <= r < model.rowCount():
                self.viewport().update(self.visualRect(model.index(r, 0)))

    def viewportEvent(self, event) -> bool:     # noqa: N802 - Qt's name
        kind = event.type()
        if kind in (QEvent.HoverEnter, QEvent.HoverMove):
            self._hover_to(self.indexAt(event.position().toPoint()).row())
        elif kind in (QEvent.HoverLeave, QEvent.Leave):
            self._hover_to(-1)
        return super().viewportEvent(event)

    def _item(self, model, row: int) -> tuple:
        fast = getattr(model, "list_item", None)
        if fast is not None:
            return fast(row)
        index = model.index(row, 0)
        found, pixmap, forced = (index.data(LIST_ROW_ROLE), index.data(LIST_PIXMAP_ROLE),
                                 index.data(LIST_STATE_ROLE))
        return (found if isinstance(found, ListRow) else None,
                pixmap if isinstance(pixmap, QPixmap) else None, forced)

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        model = self.model()
        if model is None:
            return
        count = model.rowCount()
        if count == 0:
            return
        area = event.rect()
        first = self.indexAt(QPoint(0, area.top())).row()
        if first < 0:
            # Not on a row: above the first one, or past the last.
            first = 0 if area.top() < self.visualRect(model.index(0, 0)).top() else count
        if first >= count:
            return
        box = self.visualRect(model.index(first, 0))
        x, y, width, height = box.x(), box.y(), box.width(), box.height()
        bottom = area.bottom()
        selected = {index.row() for index in self.selectionModel().selectedIndexes()}
        enabled = self.isEnabled()
        current = self.currentIndex().row() if self.focus_visible() else -1
        dpr = self.devicePixelRatioF()
        painter = QPainter(self.viewport())
        delegate = self._delegate
        for r in range(first, count):
            if y > bottom:
                break
            row, pixmap, forced = self._item(model, r)
            if row is not None:
                state = ("disabled" if not enabled else "selected" if r in selected
                         else "focus" if r == current else "hover" if r == self._hover
                         else "default")
                if forced in LIST_ROW_STATES:
                    state = forced
                delegate.paint_row_at(painter, x, y, width, height, row, state, pixmap, dpr)
            y += height
