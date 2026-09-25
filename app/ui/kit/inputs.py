"""Fields: TextInput, SpinBox and Dropdown.

They are Qt's own QLineEdit, QSpinBox and QComboBox, drawn by the generated
stylesheet (a well, a hairline edge, border.strong under the pointer,
border.focus in focus, 45 % when disabled). The classes add what a
stylesheet cannot draw: the inset shade at the top of the well, the focus ring
outside the box, a TextInput's leading search glyph and its error line, and
the Dropdown's list, which is the design's rather than Qt's.

A field you type into shows its ring whenever it has focus, as a browser
does; a Dropdown, like a button, only when the keyboard brought focus there,
and while its list is open.
"""
from __future__ import annotations

from PySide6.QtCore import QMargins, QModelIndex, QPoint, QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import (
    QFontMetricsF, QGuiApplication, QIcon, QPainter, QPainterPath, QPen, QValidator,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QLineEdit, QListView, QSizePolicy, QSpinBox, QStyle,
    QStyledItemDelegate, QStyleOptionComboBox, QStylePainter, QVBoxLayout, QWidget,
)

from ... import theme
from . import icons
from .base import (
    NBSP, Caster, Interactive, elevation_margins, follow, grouped, repolish, token,
)

def _ring_margins() -> QMargins:
    ring = theme.FOCUS_RING
    return QMargins(ring, ring, ring, ring)


# ---- TextInput ---------------------------------------------------------------------

class TextInput(Interactive, Caster, QLineEdit):
    """A one-line text field. `search=True` leads with a search glyph (the
    table filters). `set_error("…")` turns its edge red and writes the message
    under it; `set_error(None)` clears both."""

    _kit_qss = True

    def __init__(self, text: str = "", parent: QWidget | None = None, *,
                 placeholder: str = "", search: bool = False):
        QLineEdit.__init__(self, text, parent)
        self._search = search
        self._error: str | None = None
        self.setProperty("error", False)
        if placeholder:
            self.setPlaceholderText(placeholder)
        if search:
            self.setTextMargins(theme.FIELD_ICON + theme.FIELD_ICON_GAP, 0, 0, 0)
        self._fit()

    def _fit(self) -> None:
        # The height is the design's, error line or not; a layout short of
        # room must not squash the box into the line under it.
        extra = theme.FIELD_ERROR_HEIGHT if self._error else 0
        self.setFixedHeight(theme.CONTROL_HEIGHT + extra)

    def setPlaceholderText(self, text: str) -> None:   # noqa: N802 - Qt's name
        QLineEdit.setPlaceholderText(self, text)
        if not self.accessibleName():
            self.setAccessibleName(text)

    # -- the error state

    def error(self) -> str | None:
        return self._error

    def set_error(self, message: str | None) -> None:
        self._error = message or None
        self.setProperty("error", self._error is not None)
        self.setAccessibleDescription(self._error or "")
        repolish(self)
        self._fit()

    # -- geometry

    def _box(self) -> QRectF:
        """The field itself, without the error line under it."""
        extra = theme.FIELD_ERROR_HEIGHT if self._error else 0
        return QRectF(0, 0, self.width(), self.height() - extra)

    def sizeHint(self) -> QSize:                # noqa: N802 - Qt's name
        extra = theme.FIELD_ERROR_HEIGHT if self._error else 0
        return QSize(QLineEdit.sizeHint(self).width(), theme.CONTROL_HEIGHT + extra)

    def minimumSizeHint(self) -> QSize:         # noqa: N802 - Qt's name
        extra = theme.FIELD_ERROR_HEIGHT if self._error else 0
        return QSize(QLineEdit.minimumSizeHint(self).width(), theme.CONTROL_HEIGHT + extra)

    # -- painting

    def focus_visible(self) -> bool:
        if self._kit_force is not None:
            return self._kit_force == "focus" and self.isEnabled()
        return self.isEnabled() and self.hasFocus()

    def outside_margins(self) -> QMargins:
        return _ring_margins()

    def paint_outside(self, painter: QPainter) -> None:
        if self.focus_visible():
            theme.paint_focus_ring(painter, self._box(), theme.R_MD)

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        follow(self)
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        box = self._box()
        if not self.isEnabled():
            painter.setOpacity(theme.DISABLED_FIELD_OPACITY)
        theme.paint_shadow(painter, box, "elev.inset", theme.R_MD)
        if self._search:
            glyph = icons.pixmap("search", "text.lo", theme.FIELD_ICON, self.devicePixelRatioF())
            painter.drawPixmap(QPointF(1 + theme.FIELD_PAD,
                                       box.center().y() - theme.FIELD_ICON / 2), glyph)
        painter.setOpacity(1.0)
        if self._error:
            self._paint_error(painter, box)

    def _paint_error(self, painter: QPainter, box: QRectF) -> None:
        """⚠ and the message in danger, under the field."""
        size = theme.FIELD_ICON
        top = box.bottom() + theme.FIELD_ERROR_GAP
        line = self.height() - top
        glyph = icons.pixmap("warn", "danger", size, self.devicePixelRatioF())
        painter.drawPixmap(QPointF(1, top + (line - size) / 2), glyph)
        painter.setFont(theme.font("type.label"))
        painter.setPen(theme.color("danger"))
        left = 1 + size + theme.SP_6
        text = QFontMetricsF(theme.font("type.label")).elidedText(
            self._error, Qt.ElideRight, self.width() - left)
        painter.drawText(QRectF(left, top, self.width() - left, line),
                         Qt.AlignLeft | Qt.AlignVCenter, text)


# ---- SpinBox -------------------------------------------------------------------------

class SpinBox(Interactive, Caster, QSpinBox):
    """A number: mono, so the digits are tabular; thousands apart by a no-break
    space (`1 000`); the arrows stacked in a column of their own."""

    _kit_qss = True

    def __init__(self, parent: QWidget | None = None, *, minimum: int = 0,
                 maximum: int = 99_999, value: int = 0, suffix: str = ""):
        QSpinBox.__init__(self, parent)
        self.setRange(minimum, maximum)
        self.setValue(value)
        if suffix:
            self.setSuffix(suffix)
        self.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.setAccelerated(True)

    # -- `1 000` both ways

    def textFromValue(self, value: int) -> str:     # noqa: N802 - Qt's name
        return grouped(value)

    def _digits(self, text: str) -> str:
        if self.prefix() and text.startswith(self.prefix()):
            text = text[len(self.prefix()):]
        if self.suffix() and text.endswith(self.suffix()):
            text = text[:-len(self.suffix())]
        return "".join(text.split()).replace(NBSP, "")

    def valueFromText(self, text: str) -> int:      # noqa: N802 - Qt's name
        try:
            return int(self._digits(text))
        except ValueError:
            return self.value()

    def validate(self, text: str, pos: int):
        digits = self._digits(text)
        if digits in ("", "-", "+"):
            return QValidator.Intermediate, text, pos
        try:
            value = int(digits)
        except ValueError:
            return QValidator.Invalid, text, pos
        if self.minimum() <= value <= self.maximum():
            return QValidator.Acceptable, text, pos
        return QValidator.Intermediate, text, pos

    # -- geometry and painting

    def sizeHint(self) -> QSize:                # noqa: N802 - Qt's name
        return QSize(QSpinBox.sizeHint(self).width(), theme.CONTROL_HEIGHT)

    def minimumSizeHint(self) -> QSize:         # noqa: N802 - Qt's name
        return QSize(QSpinBox.minimumSizeHint(self).width(), theme.CONTROL_HEIGHT)

    def focus_visible(self) -> bool:
        if self._kit_force is not None:
            return self._kit_force == "focus" and self.isEnabled()
        return self.isEnabled() and self.hasFocus()

    def outside_margins(self) -> QMargins:
        return _ring_margins()

    def paint_outside(self, painter: QPainter) -> None:
        if self.focus_visible():
            theme.paint_focus_ring(painter, QRectF(self.rect()), theme.R_MD)

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        follow(self)
        super().paintEvent(event)
        # under the digits, which the spin box's own line edit draws next
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if not self.isEnabled():
            painter.setOpacity(theme.DISABLED_FIELD_OPACITY)
        theme.paint_shadow(painter, QRectF(self.rect()), "elev.inset", theme.R_MD)


# ---- Dropdown --------------------------------------------------------------------------

KIND_ROLE = Qt.UserRole + 40         # "item", "section" or "separator"
COUNT_ROLE = Qt.UserRole + 41        # a right-aligned count, mono
ITEM, SECTION, SEPARATOR = "item", "section", "separator"


class Dropdown(Interactive, Caster, QComboBox):
    """A choice from a list. Rows can carry a count on the right (`3 366`), be
    grouped under section rows ("YOUR FOLDERS") and split by separators;
    section and separator rows can never be chosen. `prefix="SORT"` labels the
    closed box, and the chosen row's count shows there too.

    Build it with `add_item`, `add_section` and `add_separator`; everything
    else is QComboBox (`currentIndex`, `currentData`, `currentIndexChanged`,
    `activated`).
    """

    _kit_qss = True

    def __init__(self, parent: QWidget | None = None, *, prefix: str = ""):
        QComboBox.__init__(self, parent)
        self._prefix = prefix.upper()
        self._popup: DropdownPopup | None = None
        self._last = -1
        self.setProperty("focusVisible", False)
        self.setFocusPolicy(Qt.StrongFocus)
        self.currentIndexChanged.connect(self._guard)

    # -- building the list

    def _append(self, text: str, kind: str, data=None, count=None) -> int:
        QComboBox.addItem(self, text, data)
        row = self.count() - 1
        item = self.model().item(row)
        item.setData(kind, KIND_ROLE)
        if count is not None:
            item.setData(count, COUNT_ROLE)
        if kind != ITEM:
            item.setFlags(Qt.NoItemFlags)
        self._settle()
        return row

    def add_item(self, text: str, data=None, *, count: int | str | None = None) -> int:
        """A row that can be chosen; `count` shows on its right."""
        return self._append(text, ITEM, data, count)

    def add_section(self, title: str) -> int:
        """A heading row over the rows after it. Never chosen."""
        return self._append(title, SECTION)

    def add_separator(self) -> int:
        """A hairline between groups of rows. Never chosen."""
        return self._append("", SEPARATOR)

    def set_count(self, row: int, count: int | str | None) -> None:
        self.model().item(row).setData(count, COUNT_ROLE)
        self.update()

    def kind(self, row: int) -> str:
        return self.itemData(row, KIND_ROLE) or ITEM

    def count_text(self, row: int) -> str:
        count = self.itemData(row, COUNT_ROLE)
        if count is None:
            return ""
        return grouped(count) if isinstance(count, int) else str(count)

    def is_selectable(self, row: int) -> bool:
        if not 0 <= row < self.count():
            return False
        item = self.model().item(row)
        return self.kind(row) == ITEM and bool(item.flags() & Qt.ItemIsEnabled)

    def selectable_rows(self) -> list[int]:
        return [row for row in range(self.count()) if self.is_selectable(row)]

    # -- choosing

    def setCurrentIndex(self, row: int) -> None:    # noqa: N802 - Qt's name
        if row != -1 and not self.is_selectable(row):
            raise ValueError(f"row {row} of this Dropdown is a {self.kind(row)}, "
                             "which cannot be chosen")
        QComboBox.setCurrentIndex(self, row)

    def _settle(self) -> None:
        """QComboBox chooses row 0 the moment it has one, even a heading."""
        if not self.is_selectable(self.currentIndex()):
            rows = self.selectable_rows()
            QComboBox.setCurrentIndex(self, rows[0] if rows else -1)

    def _guard(self, row: int) -> None:
        # Qt's own keyboard and wheel handling skip rows that cannot be
        # chosen; anything else that lands on one is undone here.
        if row >= 0 and not self.is_selectable(row):
            QComboBox.setCurrentIndex(self, self._last if self.is_selectable(self._last) else -1)
            return
        self._last = row

    def choose(self, row: int) -> None:
        """What a click on a row does: choose it, say so, close the list."""
        if not self.is_selectable(row):
            return
        QComboBox.setCurrentIndex(self, row)
        self.activated.emit(row)
        self.textActivated.emit(self.itemText(row))
        self.hidePopup()

    # -- the list

    def showPopup(self) -> None:                # noqa: N802 - Qt's name
        if not self.selectable_rows():
            return
        if self._popup is None:
            self._popup = DropdownPopup(self)
        self._popup.open_for(self)
        self.state_changed()

    def hidePopup(self) -> None:                # noqa: N802 - Qt's name
        if self._popup is not None and self._popup.isVisible():
            self._popup.hide()
        self.state_changed()

    def popup_open(self) -> bool:
        return self._popup is not None and self._popup.isVisible()

    # -- state and painting

    def focus_visible(self) -> bool:
        return super().focus_visible() or self.popup_open()

    def state_changed(self) -> None:
        wanted = bool(self.isEnabled() and self._kit_force is None and self.focus_visible())
        if bool(self.property("focusVisible")) != wanted:
            self.setProperty("focusVisible", wanted)
            repolish(self)
        super().state_changed()

    def sizeHint(self) -> QSize:                # noqa: N802 - Qt's name
        return QSize(QComboBox.sizeHint(self).width() + self._extras_width(),
                     theme.CONTROL_HEIGHT)

    def minimumSizeHint(self) -> QSize:         # noqa: N802 - Qt's name
        return QSize(QComboBox.minimumSizeHint(self).width(), theme.CONTROL_HEIGHT)

    def _extras_width(self) -> int:
        """Room for the prefix and the widest count, which Qt does not know about."""
        width = 0.0
        if self._prefix:
            width += QFontMetricsF(theme.font("type.monoSm")).horizontalAdvance(self._prefix)
            width += theme.SP_8
        counts = [self.count_text(row) for row in range(self.count())]
        if any(counts):
            fm = QFontMetricsF(theme.font("type.monoSm"))
            width += max(fm.horizontalAdvance(c) for c in counts) + theme.SP_8
        return round(width)

    def outside_margins(self) -> QMargins:
        return _ring_margins()

    def paint_outside(self, painter: QPainter) -> None:
        if self.focus_visible():
            theme.paint_focus_ring(painter, QRectF(self.rect()), theme.R_MD)

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        follow(self)
        painter = QStylePainter(self)
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        option.currentText = ""
        option.currentIcon = QIcon()
        painter.drawComplexControl(QStyle.CC_ComboBox, option)
        field = QRectF(self.style().subControlRect(
            QStyle.CC_ComboBox, option, QStyle.SC_ComboBoxEditField, self))

        painter.setRenderHint(QPainter.Antialiasing)
        enabled = self.isEnabled()
        if not enabled:
            painter.setOpacity(theme.DISABLED_FIELD_OPACITY)
        theme.paint_shadow(painter, QRectF(self.rect()), "elev.inset", theme.R_MD)
        painter.setOpacity(1.0)

        faint = token("text.disabled" if not enabled else "text.lo")
        mono = theme.font("type.monoSm")
        left, right = field.left(), field.right()
        if self._prefix:
            width = QFontMetricsF(mono).horizontalAdvance(self._prefix)
            painter.setFont(mono)
            painter.setPen(faint)
            painter.drawText(QRectF(left, field.top(), width + 1, field.height()),
                             Qt.AlignLeft | Qt.AlignVCenter, self._prefix)
            left += width + theme.SP_8
        count = self.count_text(self.currentIndex())
        if count:
            width = QFontMetricsF(mono).horizontalAdvance(count)
            painter.setFont(mono)
            painter.setPen(faint)
            painter.drawText(QRectF(right - width, field.top(), width + 1, field.height()),
                             Qt.AlignRight | Qt.AlignVCenter, count)
            right -= width + theme.SP_8
        body = theme.font("type.bodySm")
        text = QFontMetricsF(body).elidedText(self.currentText(), Qt.ElideRight, right - left)
        painter.setFont(body)
        painter.setPen(token("text.body" if enabled else "text.disabled"))
        painter.drawText(QRectF(left, field.top(), right - left, field.height()),
                         Qt.AlignLeft | Qt.AlignVCenter, text)


# ---- the Dropdown's list -----------------------------------------------------------------

class _RowDelegate(QStyledItemDelegate):
    """Draws a Dropdown's rows: chosen (accent.soft and a check), under the
    pointer or the keyboard (surface.raised), headings, separators, counts."""

    def __init__(self, popup: DropdownPopup):
        super().__init__(popup)
        self._popup = popup

    @staticmethod
    def row_height(kind: str) -> int:
        if kind == SEPARATOR:
            return 2 * theme.SP_4 + 1
        if kind == SECTION:
            return theme.POPUP_ROW[0] + theme.SP_4 + round(
                QFontMetricsF(theme.font("type.overline")).height())
        return 2 * theme.POPUP_ROW[0] + round(theme.line_height("type.bodySm"))

    def sizeHint(self, option, index) -> QSize:     # noqa: N802 - Qt's name
        return QSize(0, self.row_height(index.data(KIND_ROLE) or ITEM))

    def paint(self, painter: QPainter, option, index: QModelIndex) -> None:
        dropdown = self._popup.dropdown
        kind = index.data(KIND_ROLE) or ITEM
        rect = QRectF(option.rect)
        pad_v, pad_h = theme.POPUP_ROW
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        if kind == SEPARATOR:
            y = rect.center().y()
            painter.fillRect(QRectF(rect.left() + theme.SP_6, y, rect.width() - 2 * theme.SP_6, 1),
                             theme.color("border.hairline"))
        elif kind == SECTION:
            painter.setFont(theme.font("type.overline"))
            painter.setPen(theme.color("text.lo"))
            painter.drawText(rect.adjusted(pad_h, pad_v, -pad_h, -theme.SP_4),
                             Qt.AlignLeft | Qt.AlignVCenter, index.data(Qt.DisplayRole))
        else:
            chosen = index.row() == dropdown.currentIndex()
            hot = index.row() == self._popup.hot_row()
            ground = "accent.soft" if chosen else "surface.raised" if hot else None
            if ground:
                path = QPainterPath()
                path.addRoundedRect(rect, theme.R_SM, theme.R_SM)
                painter.fillPath(path, theme.color(ground))
            x = rect.left() + pad_h
            if chosen:
                glyph = icons.pixmap("check", "accent.hover", theme.POPUP_CHECK,
                                     painter.device().devicePixelRatioF())
                painter.drawPixmap(QPointF(x, rect.center().y() - theme.POPUP_CHECK / 2), glyph)
            x += theme.POPUP_CHECK + theme.SP_8
            right = rect.right() - pad_h
            count = dropdown.count_text(index.row())
            if count:
                mono = theme.font("type.monoSm")
                width = QFontMetricsF(mono).horizontalAdvance(count)
                painter.setFont(mono)
                painter.setPen(theme.color("text.lo"))
                painter.drawText(QRectF(right - width, rect.top(), width + 1, rect.height()),
                                 Qt.AlignRight | Qt.AlignVCenter, count)
                right -= width + theme.SP_8
            body = theme.font("type.bodySm")
            text = QFontMetricsF(body).elidedText(index.data(Qt.DisplayRole), Qt.ElideRight,
                                                  right - x)
            painter.setFont(body)
            painter.setPen(theme.color("text.hi" if chosen else "text.body"))
            painter.drawText(QRectF(x, rect.top(), right - x, rect.height()),
                             Qt.AlignLeft | Qt.AlignVCenter, text)
        painter.restore()


class _RowList(QListView):
    """The rows. Chosen by a click or Enter; the pointer and the arrow keys
    move one highlight, and skip what cannot be chosen."""

    def __init__(self, popup: DropdownPopup):
        super().__init__(popup)
        self._popup = popup
        self.setObjectName("dropdownRows")
        self.setFrameShape(QListView.NoFrame)
        self.setMouseTracking(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setSelectionMode(QAbstractItemView.NoSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setUniformItemSizes(False)
        self.setFocusPolicy(Qt.StrongFocus)

    def mouseMoveEvent(self, event) -> None:    # noqa: N802 - Qt's name
        row = self.indexAt(event.position().toPoint()).row()
        self._popup.set_hot_row(row if self._popup.dropdown.is_selectable(row) else -1)

    def leaveEvent(self, event) -> None:        # noqa: N802 - Qt's name
        self._popup.set_hot_row(-1)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:   # noqa: N802 - Qt's name
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt's name
        if event.button() == Qt.LeftButton:
            self._popup.dropdown.choose(self.indexAt(event.position().toPoint()).row())
        event.accept()

    def keyPressEvent(self, event) -> None:     # noqa: N802 - Qt's name
        dropdown = self._popup.dropdown
        rows = dropdown.selectable_rows()
        hot = self._popup.hot_row()
        key = event.key()
        if key in (Qt.Key_Escape, Qt.Key_F4) or (key == Qt.Key_Up and event.modifiers() & Qt.AltModifier):
            dropdown.hidePopup()
        elif key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            dropdown.choose(hot)
        elif key == Qt.Key_Tab or key == Qt.Key_Backtab:
            dropdown.hidePopup()
        elif rows and key in (Qt.Key_Down, Qt.Key_Up, Qt.Key_Home, Qt.Key_End):
            if key == Qt.Key_Home:
                target = rows[0]
            elif key == Qt.Key_End:
                target = rows[-1]
            elif key == Qt.Key_Down:
                target = next((r for r in rows if r > hot), rows[-1])
            else:
                target = next((r for r in reversed(rows) if r < hot), rows[0])
            self._popup.set_hot_row(target)
        else:
            event.ignore()
            return
        event.accept()


class DropdownPopup(Caster, QWidget):
    """A Dropdown's open list: surface.popup over elev.3, a hairline edge and
    the sheen, 4 px inside, rows at r.sm.

    It is a window of its own, with room round it for its shadow, which it
    draws itself. `embedded=True` makes it an ordinary child instead, whose
    shadow its surface draws — how the kit preview shows it open.
    """

    def __init__(self, dropdown: Dropdown, *, embedded: bool = False,
                 parent: QWidget | None = None):
        flags = (Qt.Widget if embedded
                 else Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        QWidget.__init__(self, parent if embedded else dropdown, flags)
        self.dropdown = dropdown
        self._embedded = embedded
        self._hot = -1
        if not embedded:
            self.setAttribute(Qt.WA_TranslucentBackground)
            # a click outside closes the list, and is not replayed onto the
            # Dropdown, which would open it again
            self.setAttribute(Qt.WA_NoMouseReplay)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._rows = _RowList(self)
        self._rows.setModel(dropdown.model())
        self._rows.setItemDelegate(_RowDelegate(self))
        layout = QVBoxLayout(self)
        inset = 1 + theme.POPUP_PAD
        m = self._window_margins()
        layout.setContentsMargins(m.left() + inset, m.top() + inset,
                                  m.right() + inset, m.bottom() + inset)
        layout.addWidget(self._rows)
        self._rows.setFixedSize(self._rows_size())

    # -- the highlighted row

    def hot_row(self) -> int:
        return self._hot

    def set_hot_row(self, row: int) -> None:
        if row != self._hot:
            self._hot = row
            if row >= 0:
                self._rows.scrollTo(self.dropdown.model().index(row, 0))
            self._rows.viewport().update()

    # -- size and place

    def _window_margins(self) -> QMargins:
        if self._embedded:
            return QMargins()
        left, top, right, bottom = theme.shadow_reach("elev.3")
        return QMargins(left, top, right, bottom)

    def _rows_size(self) -> QSize:
        d = self.dropdown
        body, mono = QFontMetricsF(theme.font("type.bodySm")), QFontMetricsF(theme.font("type.monoSm"))
        widest, counts, heights = 0.0, 0.0, []
        for row in range(d.count()):
            kind = d.kind(row)
            heights.append(_RowDelegate.row_height(kind))
            if kind == ITEM:
                widest = max(widest, body.horizontalAdvance(d.itemText(row)))
                if d.count_text(row):
                    counts = max(counts, mono.horizontalAdvance(d.count_text(row)) + theme.SP_8)
        pad_h = theme.POPUP_ROW[1]
        width = pad_h + theme.POPUP_CHECK + theme.SP_8 + widest + counts + pad_h
        inner = d.width() - 2 * (1 + theme.POPUP_PAD)
        # at most maxVisibleItems rows of the tallest kind; more scroll
        limit = max(1, d.maxVisibleItems()) * _RowDelegate.row_height(ITEM)
        height = min(sum(heights), limit)
        scrolls = sum(heights) > limit
        if scrolls:
            width += self._rows.verticalScrollBar().sizeHint().width()
        return QSize(max(round(width) + 1, inner), max(height, 1))

    def box(self) -> QRectF:
        """The list's box inside the window, without the room for its shadow."""
        m = self._window_margins()
        return QRectF(self.rect()).adjusted(m.left(), m.top(), -m.right(), -m.bottom())

    def open_for(self, dropdown: Dropdown) -> None:
        """Place the list under the Dropdown (over it, if the screen ends
        first), with the chosen row highlighted, and show it."""
        self._rows.setFixedSize(self._rows_size())
        self.adjustSize()
        m = self._window_margins()
        box_h = self.height() - m.top() - m.bottom()
        below = dropdown.mapToGlobal(QPoint(0, dropdown.height() + theme.POPUP_GAP))
        screen = QGuiApplication.screenAt(below) or dropdown.screen()
        top = below.y()
        if screen is not None:
            room = screen.availableGeometry()
            if top + box_h > room.bottom():
                top = dropdown.mapToGlobal(QPoint(0, 0)).y() - theme.POPUP_GAP - box_h
        self.move(below.x() - m.left(), top - m.top())
        self._hot = dropdown.currentIndex()
        if self._hot >= 0:
            self._rows.scrollTo(dropdown.model().index(self._hot, 0))
        self.show()
        self._rows.setFocus(Qt.PopupFocusReason)

    # -- events

    def mousePressEvent(self, event) -> None:   # noqa: N802 - Qt's name
        # the window's transparent margin is not the list: a click there closes it
        if not self._embedded and not self.box().contains(event.position()):
            self.dropdown.hidePopup()
        event.accept()

    def hideEvent(self, event) -> None:         # noqa: N802 - Qt's name
        super().hideEvent(event)
        if not self._embedded:
            self.dropdown.state_changed()

    # -- painting

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
        painter.drawRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5),
                                theme.R_ROW - 0.5, theme.R_ROW - 0.5)
