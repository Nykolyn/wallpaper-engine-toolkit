"""TagSelect: Wallpaper Engine's genre tags, chosen for new wallpapers.

**Closed** it is a field of the chosen tags as pills, the count out of the 25
(`3 / 25`) and a chevron. **Open**, a popup about 540 px wide says where the
tags go, lays the 25 out in four columns with a box each, and ends with
"N selected" and Clear. The tags are the Creator's own list
(`app.engines.creator.WE_TAGS`), not a copy of it; they keep the order they
were ticked in, as `clean_tags` keeps whatever order somebody chose.

**Per file** (`per_file=True`, for one clip in the Creator) a TagSelect has
three states, the ones the Creator's engine already reads from a clip:

- *follows the batch* (`value()` is None): the clip gets whatever the batch
  select holds, which `set_batch_tags` keeps it told of;
- *own tags* (a non-empty list): the clip's own, whatever the batch says;
- *none* (an empty list): the clip gets no tags at all.

Ticking a box while it follows the batch starts its own list from the batch's
tags; unticking the last one leaves it at none; Clear is none; "Follow batch"
goes back.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QMargins, QPoint, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QFontMetricsF, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from ... import theme
from ...engines.creator import WE_TAGS, clean_tags
from . import icons
from .base import Caster, Interactive, elevation_margins, follow, label
from .buttons import GhostButton
from .selection import SegmentedControl

TAG_MODES = ("batch", "own", "none")
MODE_LABELS = ("Follow batch", "Own tags", "None")
HINT = "Written to each project.json. Wallpaper Engine shows them in its own browser."


class TagSelect(Interactive, Caster, QWidget):
    """The tags for new wallpapers (or, `per_file=True`, for one clip).

    `value()` / `set_value()`: a list of tags (or None: follows the batch).
    `tags()` is what will be written. `changed` fires on every change the
    user makes; `refreshed` on any change at all, which is what its popups
    follow.
    """

    changed = Signal()
    refreshed = Signal()

    def __init__(self, parent: QWidget | None = None, *, per_file: bool = False,
                 options=WE_TAGS, placeholder: str = "No tags"):
        QWidget.__init__(self, parent)
        self._options = tuple(options)
        self._per_file = per_file
        self._own: list[str] | None = None if per_file else []
        self._batch: list[str] = []
        self._placeholder = placeholder
        self._popup: TagPopup | None = None
        self.setFocusPolicy(Qt.StrongFocus)
        self.setFixedHeight(theme.CONTROL_HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAccessibleName("Tags")
        self._describe()

    # -- the value

    def options(self) -> tuple[str, ...]:
        return self._options

    def per_file(self) -> bool:
        return self._per_file

    def value(self) -> list[str] | None:
        """The tags chosen here, or None while a per-file select follows the batch."""
        return None if self._own is None else list(self._own)

    def set_value(self, value) -> None:
        """A list of tags; None (per file only) to follow the batch."""
        if value is None and self._per_file:
            self._own = None
        else:
            self._own = clean_tags(value)
        self._refresh()

    def tags(self) -> list[str]:
        """What will be written: the batch's tags while following it."""
        return list(self._batch) if self._own is None else list(self._own)

    def mode(self) -> str:
        """"batch" (follows the batch), "own", or "none"."""
        if self._own is None:
            return "batch"
        return "own" if self._own else "none"

    def set_batch_tags(self, tags) -> None:
        """What following the batch means for a per-file select."""
        self._batch = clean_tags(tags)
        self._refresh()

    def is_checked(self, tag: str) -> bool:
        return tag in self.tags()

    # -- what the user does

    def toggle(self, tag: str) -> None:
        """Tick or untick one tag. A per-file select that followed the batch
        now has tags of its own, starting from the batch's."""
        current = self.tags()
        if tag in current:
            current.remove(tag)
        else:
            current.append(tag)
        self._own = current
        self._user_changed()

    def clear(self) -> None:
        """No tags at all (for one clip: none, not the batch's)."""
        self._own = []
        self._user_changed()

    def follow_batch(self) -> None:
        if self._per_file:
            self._own = None
            self._user_changed()

    def set_mode(self, mode: str) -> None:
        """What the per-file popup's three segments do."""
        if mode not in TAG_MODES:
            raise ValueError(f"no tag mode {mode!r}; there are {', '.join(TAG_MODES)}")
        if mode == self.mode():
            return
        if mode == "batch":
            self.follow_batch()
        elif mode == "none":
            self.clear()
        else:
            self._own = list(self.tags())
            self._user_changed()

    def _user_changed(self) -> None:
        self._refresh()
        self.changed.emit()

    def _refresh(self) -> None:
        self._describe()
        self.update()
        self.refreshed.emit()

    def _describe(self) -> None:
        tags = self.tags()
        self.setAccessibleDescription(", ".join(tags) if tags else self._placeholder)

    def count_text(self) -> str:
        """`3 / 25`; per file, "batch" while following it and "none" for none."""
        if self._per_file and self._own is None:
            return "batch"
        if self._per_file and not self._own:
            return "none"
        return f"{len(self.tags())} / {len(self._options)}"

    def selected_text(self) -> str:
        return f"{len(self.tags())} selected"

    # -- the popup

    def popup_open(self) -> bool:
        return self._popup is not None and self._popup.isVisible()

    def open_popup(self) -> None:
        if not self.isEnabled():
            return
        if self._popup is None:
            self._popup = TagPopup(self)
        self._popup.open_for(self)
        self.state_changed()

    def close_popup(self) -> None:
        if self.popup_open():
            self._popup.hide()
        self.state_changed()

    def mousePressEvent(self, event) -> None:   # noqa: N802 - Qt's name
        if event.button() == Qt.LeftButton:
            if self.popup_open():
                self.close_popup()
            else:
                self.open_popup()
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:     # noqa: N802 - Qt's name
        key = event.key()
        if key in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter, Qt.Key_F4) or (
                key == Qt.Key_Down and event.modifiers() & Qt.AltModifier):
            self.open_popup()
            event.accept()
            return
        super().keyPressEvent(event)

    # -- painting

    def focus_visible(self) -> bool:
        return super().focus_visible() or self.popup_open()

    def outside_margins(self) -> QMargins:
        ring = theme.FOCUS_RING
        return QMargins(ring, ring, ring, ring)

    def paint_outside(self, painter: QPainter) -> None:
        if self.focus_visible():
            theme.paint_focus_ring(painter, QRectF(self.rect()), theme.R_MD)

    def sizeHint(self) -> QSize:                # noqa: N802 - Qt's name
        return QSize(260, theme.CONTROL_HEIGHT)

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        follow(self)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if not self.isEnabled():
            painter.setOpacity(theme.DISABLED_FIELD_OPACITY)
        box = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(box, theme.R_MD, theme.R_MD)
        painter.fillPath(path, theme.color("surface.well"))
        theme.paint_shadow(painter, box, "elev.inset", theme.R_MD)
        edge = ("border.focus" if self.focus_visible()
                else "border.strong" if self.visual_state() == "hover" else "border.control")
        painter.setPen(QPen(theme.color(edge), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5), theme.R_MD - 0.5, theme.R_MD - 0.5)

        pad_h = theme.TAG_FIELD_PAD[1]
        middle = box.center().y()
        right = box.right() - 1 - pad_h
        chevron = theme.PATH_MARK
        painter.drawPixmap(QPointF(right - chevron, middle - chevron / 2),
                           icons.pixmap("chevD", "text.mid", chevron, self.devicePixelRatioF()))
        right -= chevron + theme.TAG_PILL_GAP
        mono = theme.font("type.monoXs")
        count = self.count_text()
        count_w = QFontMetricsF(mono).horizontalAdvance(count)
        painter.setFont(mono)
        painter.setPen(theme.color("text.lo"))
        painter.drawText(QRectF(right - count_w, box.top(), count_w + 1, box.height()),
                         Qt.AlignRight | Qt.AlignVCenter, count)
        right -= count_w + theme.TAG_PILL_GAP * 2
        self._paint_pills(painter, QRectF(1 + pad_h, box.top(), right - 1 - pad_h, box.height()))

    def _paint_pills(self, painter: QPainter, area: QRectF) -> None:
        tags = self.tags()
        font = theme.font("type.caption")
        metrics = QFontMetricsF(font)
        if not tags:
            painter.setFont(theme.font("type.bodySm"))
            painter.setPen(theme.color("text.lo"))
            painter.drawText(area, Qt.AlignLeft | Qt.AlignVCenter, self._placeholder)
            return
        painter.save()
        if self._per_file and self._own is None:
            painter.setOpacity(painter.opacity() * theme.DIM_OPACITY)
        pad = theme.TAG_PILL_PAD[1]
        height = theme.CHIP_HEIGHT
        top = area.center().y() - height / 2
        x = area.left()
        for i, tag in enumerate(tags):
            width = metrics.horizontalAdvance(tag) + 2 * pad
            rest = len(tags) - i - 1
            more = (metrics.horizontalAdvance(f"+{rest}") + 2 * pad + theme.TAG_PILL_GAP) if rest else 0
            if x + width + more > area.right():
                self._pill(painter, font, QRectF(x, top, metrics.horizontalAdvance(
                    f"+{len(tags) - i}") + 2 * pad, height), f"+{len(tags) - i}")
                break
            self._pill(painter, font, QRectF(x, top, width, height), tag)
            x += width + theme.TAG_PILL_GAP
        painter.restore()

    @staticmethod
    def _pill(painter: QPainter, font, rect: QRectF, text: str) -> None:
        path = QPainterPath()
        path.addRoundedRect(rect, theme.R_SM, theme.R_SM)
        painter.fillPath(path, theme.color("surface.raised"))
        painter.setFont(font)
        painter.setPen(theme.color("text.body"))
        painter.drawText(rect, Qt.AlignCenter, text)


# ---- the grid of boxes ----------------------------------------------------------------------

class _TagGrid(QWidget):
    """The 25 tags in four columns, each a box and its name. The pointer and
    the arrow keys move one highlight; a click, Space or Enter ticks."""

    def __init__(self, select: TagSelect, width: int, parent: QWidget | None = None):
        super().__init__(parent)
        self._select = select
        self._hot = -1
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setAccessibleName("Tags")
        rows = math.ceil(len(select.options()) / theme.TAG_COLUMNS)
        self._row_h = 2 * theme.TAG_ROW_PAD[0] + max(theme.TAG_BOX,
                                                      math.ceil(theme.line_height("type.bodySm")))
        gap_v = theme.TAG_GRID_GAP[0]
        self.setFixedSize(width, rows * self._row_h + max(0, rows - 1) * gap_v)

    def cell_rect(self, i: int) -> QRectF:
        gap_v, gap_h = theme.TAG_GRID_GAP
        columns = theme.TAG_COLUMNS
        width = (self.width() - (columns - 1) * gap_h) / columns
        row, column = divmod(i, columns)
        return QRectF(column * (width + gap_h), row * (self._row_h + gap_v), width, self._row_h)

    def index_at(self, point) -> int:
        for i in range(len(self._select.options())):
            if self.cell_rect(i).contains(QPointF(point)):
                return i
        return -1

    def hot(self) -> int:
        return self._hot

    def set_hot(self, i: int) -> None:
        if i != self._hot:
            self._hot = i
            self.update()

    def mouseMoveEvent(self, event) -> None:    # noqa: N802 - Qt's name
        self.set_hot(self.index_at(event.position()))

    def leaveEvent(self, event) -> None:        # noqa: N802 - Qt's name
        self.set_hot(-1)
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt's name
        i = self.index_at(event.position())
        if event.button() == Qt.LeftButton and i >= 0:
            self._select.toggle(self._select.options()[i])
        event.accept()

    def keyPressEvent(self, event) -> None:     # noqa: N802 - Qt's name
        count = len(self._select.options())
        key, hot = event.key(), max(self._hot, 0)
        steps = {Qt.Key_Right: 1, Qt.Key_Left: -1, Qt.Key_Down: theme.TAG_COLUMNS,
                 Qt.Key_Up: -theme.TAG_COLUMNS}
        if key in steps:
            self.set_hot(min(count - 1, max(0, hot + steps[key])) if self._hot >= 0 else 0)
        elif key == Qt.Key_Home:
            self.set_hot(0)
        elif key == Qt.Key_End:
            self.set_hot(count - 1)
        elif key in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter) and self._hot >= 0:
            self._select.toggle(self._select.options()[self._hot])
        elif key in (Qt.Key_Escape, Qt.Key_F4):
            self._select.close_popup()
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        body = theme.font("type.bodySm")
        dpr = self.devicePixelRatioF()
        pad_h = theme.TAG_ROW_PAD[1]
        box_side = theme.TAG_BOX
        for i, tag in enumerate(self._select.options()):
            cell = self.cell_rect(i)
            if i == self._hot:
                path = QPainterPath()
                path.addRoundedRect(cell, theme.R_SM, theme.R_SM)
                painter.fillPath(path, theme.color("surface.rowHover"))
            checked = self._select.is_checked(tag)
            box = QRectF(cell.left() + pad_h, cell.center().y() - box_side / 2, box_side, box_side)
            if checked:
                path = QPainterPath()
                path.addRoundedRect(box, theme.R_SM, theme.R_SM)
                painter.fillPath(path, theme.color("accent"))
                tick = theme.TAG_TICK
                painter.drawPixmap(QPointF(box.center().x() - tick / 2, box.center().y() - tick / 2),
                                   icons.pixmap("check", "text.onAccent", tick, dpr))
            else:
                painter.setPen(QPen(theme.color("border.strong"), 1))
                painter.setBrush(Qt.NoBrush)
                painter.drawRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5),
                                        theme.R_SM - 0.5, theme.R_SM - 0.5)
            painter.setFont(body)
            painter.setPen(theme.color("text.hi" if checked else "text.body"))
            left = box.right() + theme.SP_8
            painter.drawText(QRectF(left, cell.top(), cell.right() - left, cell.height()),
                             Qt.AlignLeft | Qt.AlignVCenter, tag)


class _Rule(QWidget):
    """The hairline over the popup's footer."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedHeight(1)

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        QPainter(self).fillRect(self.rect(), theme.color("border.hairline"))


class TagPopup(Caster, QWidget):
    """A TagSelect's open state: the hint, the grid and the footer, on
    surface.popup over elev.3. A window of its own, or with `embedded=True`
    an ordinary child (how the kit preview shows it open)."""

    def __init__(self, select: TagSelect, *, embedded: bool = False,
                 parent: QWidget | None = None):
        flags = (Qt.Widget if embedded
                 else Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        QWidget.__init__(self, parent if embedded else select, flags)
        self.select = select
        self._embedded = embedded
        if not embedded:
            self.setAttribute(Qt.WA_TranslucentBackground)
            self.setAttribute(Qt.WA_NoMouseReplay)
        column = QVBoxLayout(self)
        m = self._window_margins()
        pad_v, pad_h = theme.TAG_POPUP_PAD
        column.setContentsMargins(m.left() + 1 + pad_h, m.top() + 1 + pad_v,
                                  m.right() + 1 + pad_h, m.bottom() + 1 + pad_v)
        column.setSpacing(theme.TAG_POPUP_GAP)
        inner = theme.TAG_POPUP_WIDTH - 2 * (1 + pad_h)
        self.modes: SegmentedControl | None = None
        if select.per_file():
            self.modes = SegmentedControl(MODE_LABELS)
            self.modes.changed.connect(lambda i: select.set_mode(TAG_MODES[i]))
            column.addWidget(self.modes, 0, Qt.AlignLeft)
        hint = label(HINT, "type.label", "lo")
        hint.setWordWrap(True)
        hint.setFixedWidth(inner)
        column.addWidget(hint)
        self.grid = _TagGrid(select, inner)
        column.addWidget(self.grid)
        column.addWidget(_Rule())
        footer = QHBoxLayout()
        footer.setSpacing(theme.SP_10)
        self._selected = label("", "type.monoSm", "lo")
        footer.addWidget(self._selected)
        footer.addStretch(1)
        self.clear_button = GhostButton("Clear", size="sm")
        self.clear_button.clicked.connect(select.clear)
        footer.addWidget(self.clear_button)
        column.addLayout(footer)
        self.setFixedWidth(theme.TAG_POPUP_WIDTH + m.left() + m.right())
        select.refreshed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        self._selected.setText(self.select.selected_text())
        if self.modes is not None:
            index = TAG_MODES.index(self.select.mode())
            if self.modes.current_index() != index:
                self.modes.blockSignals(True)
                self.modes.set_current_index(index)
                self.modes.blockSignals(False)
        self.grid.update()

    def _window_margins(self) -> QMargins:
        if self._embedded:
            return QMargins()
        left, top, right, bottom = theme.shadow_reach("elev.3")
        return QMargins(left, top, right, bottom)

    def box(self) -> QRectF:
        m = self._window_margins()
        return QRectF(self.rect()).adjusted(m.left(), m.top(), -m.right(), -m.bottom())

    def open_for(self, select: TagSelect) -> None:
        """Under the field, its left edge on the field's, or above it when the
        screen ends first."""
        self.refresh()
        self.adjustSize()
        m = self._window_margins()
        box_h = self.height() - m.top() - m.bottom()
        below = select.mapToGlobal(QPoint(0, select.height() + theme.SP_6))
        screen = QGuiApplication.screenAt(below) or select.screen()
        top = below.y()
        if screen is not None and top + box_h > screen.availableGeometry().bottom():
            top = select.mapToGlobal(QPoint(0, 0)).y() - theme.SP_6 - box_h
        self.move(below.x() - m.left(), top - m.top())
        self.show()
        self.grid.setFocus(Qt.PopupFocusReason)

    def mousePressEvent(self, event) -> None:   # noqa: N802 - Qt's name
        if not self._embedded and not self.box().contains(event.position()):
            self.select.close_popup()
        event.accept()

    def keyPressEvent(self, event) -> None:     # noqa: N802 - Qt's name
        if event.key() in (Qt.Key_Escape, Qt.Key_F4):
            self.select.close_popup()
            event.accept()
            return
        super().keyPressEvent(event)

    def hideEvent(self, event) -> None:         # noqa: N802 - Qt's name
        super().hideEvent(event)
        if not self._embedded:
            self.select.state_changed()

    def outside_margins(self) -> QMargins:
        return elevation_margins("elev.3") if self._embedded else QMargins()

    def paint_outside(self, painter: QPainter) -> None:
        theme.paint_shadow(painter, QRectF(self.rect()), "elev.3", theme.R_ROW)

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        follow(self)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        box = self.box()
        if not self._embedded:
            theme.paint_shadow(painter, box, "elev.3", theme.R_ROW)
        path = QPainterPath()
        path.addRoundedRect(box, theme.R_ROW, theme.R_ROW)
        painter.fillPath(path, theme.color("surface.popup"))
        theme.paint_sheen(painter, box, theme.R_ROW, "elev.3")
        painter.setPen(QPen(theme.color("border.control"), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5), theme.R_ROW - 0.5, theme.R_ROW - 0.5)
