"""ThumbGrid: a TableModel's rows as cards — the preview, the title and a line
under it — for a page that offers Grid beside its Table.

It reads the same model the table does (`thumb_source`, `cell`, `row_dimmed`,
`group_rows`), so a filter, a sort or a new list shows in both. Group headers
are rows in a table and nothing in a grid: they are hidden here. The cards are
as big as the table's thumbs (`theme.THUMB["row"]`), which Settings chooses.
"""
from __future__ import annotations

from collections import OrderedDict

from PySide6.QtCore import QModelIndex, QPoint, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFontMetricsF, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QAbstractItemView, QListView, QStyle, QStyledItemDelegate, QWidget

from ... import theme
from . import format as fmt
from .tables import Cell, TableModel, _text_of, paint_row_ground, thumb_tile
from .thumbs import ThumbLoader

SIZE = "row"
PAD = theme.SP_8            # a card's edge to its preview, and its words
TITLE_GAP = theme.SP_6      # the preview to the title
META_GAP = theme.SP_2       # the title to the line under it
TITLE_TYPE, META_TYPE = "type.bodySm", "type.monoXs"


def card_size() -> QSize:
    side = theme.THUMB[SIZE][0]
    words = (TITLE_GAP + theme.line_height(TITLE_TYPE) + META_GAP
             + theme.line_height(META_TYPE))
    return QSize(side + 2 * PAD, round(side + words) + 2 * PAD)


class _CardDelegate(QStyledItemDelegate):
    def __init__(self, grid: "ThumbGrid"):
        super().__init__(grid)
        self.grid = grid

    def sizeHint(self, option, index) -> QSize:   # noqa: N802 - Qt's name
        return card_size()

    def paint(self, painter: QPainter, option, index: QModelIndex) -> None:
        grid = self.grid
        model: TableModel = index.model()
        item = model.item_at(index.row())
        if item is None:
            return
        rect = QRectF(option.rect)
        selected = bool(option.state & QStyle.State_Selected)
        hovered = bool(option.state & QStyle.State_MouseOver)
        paint_row_ground(painter, rect, selected=selected, hovered=hovered,
                         radius=theme.RADIUS["r.lg"])
        painter.save()
        if model.row_dimmed(item) and not selected:
            painter.setOpacity(theme.DIM_OPACITY)
        side = theme.THUMB[SIZE][0]
        x, y = rect.left() + PAD, rect.top() + PAD
        state, pixmap = grid.thumb_state(model.thumb_source(item))
        painter.drawPixmap(round(x), round(y), thumb_tile(pixmap, state, SIZE, grid.devicePixelRatioF()))
        y += side + TITLE_GAP
        title = model.cell(item, grid.title_column)
        tone = title.tone if isinstance(title, Cell) and title.tone else "text.body"
        y = self._line(painter, QRectF(x, y, side, theme.line_height(TITLE_TYPE)),
                       _text_of(title), TITLE_TYPE, tone) + META_GAP
        meta = " · ".join(t for t in (_text_of(model.cell(item, c)) for c in grid.meta_columns)
                          if t and t != fmt.DASH)
        self._line(painter, QRectF(x, y, side, theme.line_height(META_TYPE)), meta, META_TYPE,
                   "text.lo")
        painter.restore()

    @staticmethod
    def _line(painter: QPainter, box: QRectF, text: str, token: str, tone: str) -> float:
        font = theme.font(token)
        painter.setFont(font)
        painter.setPen(theme.color(tone))
        words = QFontMetricsF(font).elidedText(text, Qt.ElideRight, box.width())
        painter.drawText(box, Qt.AlignLeft | Qt.AlignVCenter, words)
        return box.bottom()


class ThumbGrid(QListView):
    """The rows of a TableModel as cards. `title_column` names the card;
    `meta_columns` make the line under it, joined with " · "."""

    SETTLE = 90         # ms of stillness before previews are asked for
    CACHE = 400         # previews kept

    item_clicked = Signal(QModelIndex)

    def __init__(self, title_column: int, meta_columns: tuple[int, ...] = (),
                 parent: QWidget | None = None, *, loader: ThumbLoader | None = None):
        super().__init__(parent)
        self.title_column = title_column
        self.meta_columns = meta_columns
        self.setViewMode(QListView.IconMode)
        self.setMovement(QListView.Static)
        self.setResizeMode(QListView.Adjust)
        self.setUniformItemSizes(True)
        self.setSpacing(theme.SP_4)
        self.setFrameShape(QListView.NoFrame)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_Hover)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.viewport().setAutoFillBackground(False)
        self.setItemDelegate(_CardDelegate(self))
        self._pixmaps: OrderedDict[str, QPixmap | None] = OrderedDict()
        self._loader = loader or ThumbLoader(self)
        self._loader.local_done.connect(self._arrived)
        self._settle = QTimer(self, singleShot=True, interval=self.SETTLE)
        self._settle.timeout.connect(self.request_visible_thumbs)
        self.verticalScrollBar().valueChanged.connect(self._settle.start)
        self.clicked.connect(self.item_clicked)
        self.activated.connect(self.item_clicked)

    def setModel(self, model: TableModel) -> None:   # noqa: N802 - Qt's name
        super().setModel(model)
        self.setModelColumn(self.title_column)
        for name in ("modelReset", "layoutChanged", "rowsInserted", "rowsRemoved"):
            getattr(model, name).connect(self._rows_changed)
        self._rows_changed()

    def _rows_changed(self, *_args) -> None:
        model = self.model()
        groups = set(model.group_rows())
        for row in range(model.rowCount()):
            self.setRowHidden(row, row in groups)
        self._settle.start()

    def resizeEvent(self, event) -> None:       # noqa: N802 - Qt's name
        super().resizeEvent(event)
        self._settle.start()

    def showEvent(self, event) -> None:         # noqa: N802 - Qt's name
        super().showEvent(event)
        self._settle.start()

    # -- previews

    def thumb_state(self, source) -> tuple[str, QPixmap | None]:
        if not source:
            return "placeholder", None
        key = str(source)
        if key in self._pixmaps:
            pixmap = self._pixmaps[key]
            return ("image", pixmap) if pixmap is not None else ("placeholder", None)
        return "loading", None

    def visible_rows(self) -> list[int]:
        model = self.model()
        if model is None:
            return []
        area = self.viewport().rect()
        return [r for r in range(model.rowCount()) if not self.isRowHidden(r)
                and self.visualRect(model.index(r, self.title_column)).intersects(area)]

    def request_visible_thumbs(self) -> None:
        model = self.model()
        if model is None or not self.isVisible():
            return
        self._loader.retarget_local()
        dpr = self.devicePixelRatioF()
        side = round(theme.THUMB[SIZE][0] * dpr)
        for row in self.visible_rows():
            source = model.thumb_source(model.item_at(row))
            if not source:
                continue
            key = str(source)
            if key in self._pixmaps:
                self._pixmaps.move_to_end(key)
            else:
                self._loader.request_local(key, key, QSize(side, side), True)

    def _arrived(self, key: str, image: QImage) -> None:
        pixmap = None
        if not image.isNull():
            pixmap = QPixmap.fromImage(image)
            pixmap.setDevicePixelRatio(self.devicePixelRatioF())
        self._pixmaps[key] = pixmap
        while len(self._pixmaps) > self.CACHE:
            self._pixmaps.popitem(last=False)
        self.viewport().update()

    def thumbs_resized(self) -> None:
        """`theme.THUMB["row"]` changed (Settings): new cards, previews read again."""
        self._pixmaps.clear()
        self.scheduleDelayedItemsLayout()
        self.viewport().update()
        self._settle.start()

    # -- following the table

    def scroll_to_row(self, row: int) -> None:
        model = self.model()
        if model is not None and 0 <= row < model.rowCount():
            self.scrollTo(model.index(row, self.title_column), QAbstractItemView.PositionAtTop)

    def first_visible_row(self) -> int:
        """The row of the card at the top left, -1 when there is none."""
        return self.indexAt(QPoint(PAD, PAD)).row()
