"""Selection controls: Checkbox, Toggle, SegmentedControl, Pagination.

- Checkbox is Qt's QCheckBox under the stylesheet (the tick is instant); the
  class adds the focus ring and the raised shadow of a ticked box.
- Toggle is painted: a 34 × 18 track and a 12 px knob that slide together
  over motion.base, or jump when motion is off. For a binary setting.
- SegmentedControl is two or three exclusive segments — never one (that is a
  Toggle) and never four or more (that is a Dropdown).
- Pagination shows at most four page numbers — the first, the current, the
  one after it, the last — with an ellipsis for each gap, and hides itself
  below two pages. `page_numbers()` is that rule on its own.
"""
from __future__ import annotations

from PySide6.QtCore import QMargins, QPointF, QRectF, QSize, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractButton, QCheckBox, QHBoxLayout, QSizePolicy, QStyle,
    QStyleOptionButton, QWidget,
)

from ... import animations, theme
from .base import (
    STATES, Caster, Interactive, elevation_margins, follow, label, mix, refresh, token,
)
from .buttons import IconButton


def _ring() -> QMargins:
    ring = theme.FOCUS_RING
    return QMargins(ring, ring, ring, ring)


# ---- Checkbox ----------------------------------------------------------------------

class Checkbox(Interactive, Caster, QCheckBox):
    """A check box with a label. `tristate=True` allows the indeterminate state
    (a dash), which a group header shows when some of its rows are ticked."""

    _kit_qss = True

    def __init__(self, text: str = "", parent: QWidget | None = None, *,
                 tristate: bool = False):
        QCheckBox.__init__(self, text, parent)
        self.setTristate(tristate)
        self.stateChanged.connect(lambda _state: refresh(self))

    def is_down(self) -> bool:
        return self.isDown()

    def indicator_rect(self) -> QRectF:
        option = QStyleOptionButton()
        self.initStyleOption(option)
        return QRectF(self.style().subElementRect(QStyle.SE_CheckBoxIndicator, option, self))

    def outside_margins(self) -> QMargins:
        return elevation_margins("elev.1")

    def paint_outside(self, painter: QPainter) -> None:
        box = self.indicator_rect()
        if self.isEnabled() and self.checkState() != Qt.Unchecked:
            theme.paint_shadow(painter, box, "elev.1", theme.R_SM)
        if self.focus_visible():
            theme.paint_focus_ring(painter, box, theme.R_SM)

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        follow(self)
        super().paintEvent(event)


# ---- Toggle -------------------------------------------------------------------------

class Toggle(Interactive, Caster, QAbstractButton):
    """An on/off switch with a label. The knob and the track move together."""

    def __init__(self, text: str = "", parent: QWidget | None = None, *,
                 checked: bool = False):
        QAbstractButton.__init__(self, parent)
        self.setText(text)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._t = 1.0 if checked else 0.0
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(animations.BASE)
        self._animation.setEasingCurve(animations.ease())
        self._animation.valueChanged.connect(self._step)
        self.toggled.connect(self._slide)
        self.pressed.connect(self.state_changed)
        self.released.connect(self.state_changed)

    @property
    def knob_position(self) -> float:
        """0 is off, 1 is on; in between while it slides."""
        return self._t

    @property
    def sliding(self) -> bool:
        return self._animation.state() == QVariantAnimation.Running

    def is_down(self) -> bool:
        return self.isDown()

    def _slide(self, on: bool) -> None:
        target = 1.0 if on else 0.0
        self._animation.stop()
        if animations.ENABLED and self.isVisible() and self._kit_force is None:
            self._animation.setStartValue(self._t)
            self._animation.setEndValue(target)
            self._animation.start()
        else:
            self._t = target
            self.update()
        refresh(self)

    def _step(self, value) -> None:
        self._t = float(value)
        self.update()

    # -- geometry

    def _track(self) -> QRectF:
        width, height = theme.TOGGLE_TRACK
        return QRectF(0, (self.height() - height) / 2, width, height)

    def sizeHint(self) -> QSize:                # noqa: N802 - Qt's name
        width, height = theme.TOGGLE_TRACK
        if self.text():
            width += theme.TOGGLE_GAP + round(
                QFontMetricsF(theme.font("type.bodySm")).horizontalAdvance(self.text())) + 1
        return QSize(width, max(height, round(theme.line_height("type.bodySm"))))

    def minimumSizeHint(self) -> QSize:         # noqa: N802 - Qt's name
        return self.sizeHint()

    def hitButton(self, pos) -> bool:           # noqa: N802 - Qt's name
        return self.rect().contains(pos)

    # -- painting

    def outside_margins(self) -> QMargins:
        return elevation_margins("elev.1")

    def paint_outside(self, painter: QPainter) -> None:
        track = self._track()
        radius = track.height() / 2
        if self.isEnabled() and self.isChecked() and self.visual_state() != "pressed":
            theme.paint_shadow(painter, track, "elev.1", radius)
        if self.focus_visible():
            theme.paint_focus_ring(painter, track, radius)

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        follow(self)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        opacity = 1.0 if self.isEnabled() else theme.DISABLED_OPACITY
        painter.setOpacity(opacity)
        hover = self.visual_state() == "hover"
        t = self._t
        track = self._track()
        radius = track.height() / 2

        path = QPainterPath()
        path.addRoundedRect(track, radius, radius)
        painter.fillPath(path, mix(token("surface.well"),
                                   token("accent.hover" if hover else "accent"), t))
        if t < 1:                       # off: a well, shaded at the top
            painter.setOpacity(opacity * (1 - t))
            theme.paint_shadow(painter, track, "elev.inset", radius)
        if t > 0:                       # on: raised, lit at the top
            painter.setOpacity(opacity * t)
            theme.paint_sheen(painter, track, radius)
        painter.setOpacity(opacity)
        edge = token("border.strong" if hover else "border.control")
        clear = QColor(edge)
        clear.setAlpha(0)
        painter.setPen(QPen(mix(edge, clear, t), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(track.adjusted(0.5, 0.5, -0.5, -0.5), radius - 0.5, radius - 0.5)

        knob = theme.TOGGLE_KNOB
        inner = track.adjusted(1, 1, -1, -1)
        start = inner.left() + theme.TOGGLE_INSET
        end = inner.right() - theme.TOGGLE_INSET - knob
        centre = QPointF(start + (end - start) * t + knob / 2, inner.center().y())
        theme.paint_disc_shadow(painter, centre, knob / 2)
        painter.setPen(Qt.NoPen)
        painter.setBrush(mix(token("text.mid"), token("text.onAccent"), t))
        painter.drawEllipse(centre, knob / 2, knob / 2)

        if self.text():
            left = track.right() + theme.TOGGLE_GAP
            painter.setFont(theme.font("type.bodySm"))
            painter.setPen(token("text.body"))
            painter.drawText(QRectF(left, 0, self.width() - left, self.height()),
                             Qt.AlignLeft | Qt.AlignVCenter, self.text())


# ---- SegmentedControl ----------------------------------------------------------------

class SegmentedControl(Interactive, Caster, QWidget):
    """Two or three exclusive segments ("Queue / Shown", "All / Problems").

    The chosen one is surface.raised with text.hi, the others transparent with
    text.mid. Arrow keys move the choice; `changed` says where it went.
    """

    changed = Signal(int)

    def __init__(self, labels, parent: QWidget | None = None, *, current: int = 0):
        labels = [str(text) for text in labels]
        if not 2 <= len(labels) <= 3:
            raise ValueError(f"a SegmentedControl has two or three segments, not {len(labels)}: "
                             "for one on/off setting use a Toggle, for more a Dropdown")
        QWidget.__init__(self, parent)
        self._labels = labels
        self._current = max(0, min(current, len(labels) - 1))
        self._hover = -1
        self._down = -1
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setAccessibleName(" / ".join(labels))
        self.setAccessibleDescription(labels[self._current])

    # -- the choice

    def labels(self) -> list[str]:
        return list(self._labels)

    def current_index(self) -> int:
        return self._current

    def set_current_index(self, index: int) -> None:
        if not 0 <= index < len(self._labels):
            raise IndexError(f"no segment {index}")
        if index != self._current:
            self._current = index
            self.setAccessibleDescription(self._labels[index])
            self.update()
            self.changed.emit(index)

    # -- geometry

    def _segments(self) -> list[QRectF]:
        fm = QFontMetricsF(theme.font("type.label"))
        rects, x = [], 1.0
        for i, text in enumerate(self._labels):
            width = fm.horizontalAdvance(text) + 2 * theme.SEGMENT_PAD
            rects.append(QRectF(x, 1, width, self.height() - 2))
            x += width + 1              # the divider
        return rects

    def sizeHint(self) -> QSize:                # noqa: N802 - Qt's name
        fm = QFontMetricsF(theme.font("type.label"))
        width = sum(fm.horizontalAdvance(t) + 2 * theme.SEGMENT_PAD for t in self._labels)
        return QSize(round(width) + len(self._labels) + 1, theme.SEGMENT_HEIGHT)

    def minimumSizeHint(self) -> QSize:         # noqa: N802 - Qt's name
        return self.sizeHint()

    def _at(self, pos) -> int:
        for i, rect in enumerate(self._segments()):
            if rect.adjusted(-1, -1, 1, 1).contains(QPointF(pos)):
                return i
        return -1

    # -- state

    def is_down(self) -> bool:
        return self._down >= 0

    def _hovered(self) -> int:
        if self._kit_force == "hover":
            return next(i for i in range(len(self._labels)) if i != self._current)
        return self._hover if self._kit_force is None and self.isEnabled() else -1

    def _pressed(self) -> int:
        if self._kit_force == "pressed":
            return self._current
        return self._down if self._kit_force is None else -1

    # -- input

    def mouseMoveEvent(self, event) -> None:    # noqa: N802 - Qt's name
        index = self._at(event.position())
        if index != self._hover:
            self._hover = index
            self.update()

    def leaveEvent(self, event) -> None:        # noqa: N802 - Qt's name
        self._hover = -1
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:   # noqa: N802 - Qt's name
        if event.button() == Qt.LeftButton:
            self._down = self._at(event.position())
            self.state_changed()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt's name
        if event.button() == Qt.LeftButton and self._down >= 0:
            index = self._at(event.position())
            chosen = self._down
            self._down = -1
            if index == chosen:
                self.set_current_index(chosen)
            self.state_changed()

    def keyPressEvent(self, event) -> None:     # noqa: N802 - Qt's name
        last = len(self._labels) - 1
        moves = {Qt.Key_Left: self._current - 1, Qt.Key_Up: self._current - 1,
                 Qt.Key_Right: self._current + 1, Qt.Key_Down: self._current + 1,
                 Qt.Key_Home: 0, Qt.Key_End: last}
        if event.key() in moves:
            self.set_current_index(max(0, min(last, moves[event.key()])))
            event.accept()
        else:
            super().keyPressEvent(event)

    # -- painting

    def outside_margins(self) -> QMargins:
        return _ring()

    def paint_outside(self, painter: QPainter) -> None:
        if self.focus_visible():
            theme.paint_focus_ring(painter, QRectF(self.rect()), theme.R_MD)

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        follow(self)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if not self.isEnabled():
            painter.setOpacity(theme.DISABLED_OPACITY)
        box = QRectF(self.rect())
        r = theme.R_MD
        outline = QPainterPath()
        outline.addRoundedRect(box.adjusted(1, 1, -1, -1), r - 1, r - 1)
        segments = self._segments()
        hovered, pressed = self._hovered(), self._pressed()

        painter.save()
        painter.setClipPath(outline)
        for i, rect in enumerate(segments):
            if i == pressed:
                painter.fillRect(rect, theme.color("surface.wash"))
                theme.paint_shadow(painter, rect, "elev.inset", 0)
            elif i == self._current:
                painter.fillRect(rect, theme.color("surface.raised"))
            elif i == hovered:
                painter.fillRect(rect, theme.color("surface.wash"))
            if i:
                painter.fillRect(QRectF(rect.left() - 1, 1, 1, box.height() - 2),
                                 theme.color("border.control"))
        painter.restore()

        edge = ("border.focus" if self.focus_visible()
                else "border.strong" if self.visual_state() == "hover" or hovered >= 0
                else "border.control")
        painter.setPen(QPen(theme.color(edge), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5), r - 0.5, r - 0.5)

        painter.setFont(theme.font("type.label"))
        for i, rect in enumerate(segments):
            chosen = i == self._current or i == pressed
            painter.setPen(theme.color("text.hi" if chosen else "text.mid"))
            painter.drawText(rect, Qt.AlignCenter, self._labels[i])


# ---- Pagination ------------------------------------------------------------------------

def page_numbers(pages: int, current: int) -> list[int | None]:
    """The page numbers a Pagination shows, None standing for an ellipsis.

    At most four numbers: the first, the current, the one after it, the last.
    At the ends the pair turns inwards and the count is made up to four, so
    page 1 of 6 reads `1 2 3 … 6` and page 6 of 6 reads `1 … 4 5 6`. Nothing
    below two pages.
    """
    if pages < 2:
        return []
    current = max(1, min(current, pages))
    if pages <= 4:
        return list(range(1, pages + 1))
    low = current if current < pages else pages - 1
    high = low + 1
    shown = {1, pages, low, high}
    while len(shown) < 4:
        if high + 1 < pages:
            high += 1
            shown.add(high)
        elif low - 1 > 1:
            low -= 1
            shown.add(low)
        else:
            break
    numbers: list[int | None] = []
    previous = 0
    for n in sorted(shown):
        if n - previous > 1:
            numbers.append(None)
        numbers.append(n)
        previous = n
    return numbers


class _PageCell(Interactive, Caster, QAbstractButton):
    """One page number: the current page an accent fill, the rest ghost."""

    def __init__(self, page: int, current: bool, parent: QWidget | None = None):
        QAbstractButton.__init__(self, parent)
        self.page = page
        self.current = current
        self.setText(str(page))
        self.setAccessibleName(f"Page {page}" + (", current" if current else ""))
        self.setFocusPolicy(Qt.TabFocus if not current else Qt.NoFocus)
        self.pressed.connect(self.state_changed)
        self.released.connect(self.state_changed)

    def is_down(self) -> bool:
        return self.isDown()

    def sizeHint(self) -> QSize:                # noqa: N802 - Qt's name
        side = theme.ICON_BUTTON["sm"][0]
        width = QFontMetricsF(theme.font("type.mono")).horizontalAdvance(self.text())
        return QSize(max(side, round(width) + 2 * theme.PAGE_PAD), side)

    def minimumSizeHint(self) -> QSize:         # noqa: N802 - Qt's name
        return self.sizeHint()

    def outside_margins(self) -> QMargins:
        return _ring()

    def paint_outside(self, painter: QPainter) -> None:
        if self.focus_visible():
            theme.paint_focus_ring(painter, QRectF(self.rect()), theme.R_SM)

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        follow(self)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        box = QRectF(self.rect())
        state = self.visual_state()
        if self.current:
            fill, text = "accent", "text.onAccent"
        else:
            fill, text = {"hover": ("surface.raised", "text.hi"),
                          "pressed": ("surface.press", "text.mid")}.get(state, (None, "text.mid"))
        if not self.isEnabled():
            painter.setOpacity(theme.DISABLED_OPACITY)
        if fill:
            path = QPainterPath()
            path.addRoundedRect(box, theme.R_SM, theme.R_SM)
            painter.fillPath(path, theme.color(fill))
        if state == "pressed" and not self.current:
            theme.paint_shadow(painter, box, "elev.inset", theme.R_SM)
        painter.setFont(theme.font("type.mono"))
        painter.setPen(theme.color(text))
        painter.drawText(box, Qt.AlignCenter, self.text())


class Pagination(QWidget):
    """Page numbers between two 22 px chevrons, for gallery and table footers.

    The chevrons go disabled at the ends; below two pages the whole control is
    hidden, not disabled. `page_changed(page)` fires when the user moves to
    another page (pages count from 1).
    """

    page_changed = Signal(int)

    def __init__(self, parent: QWidget | None = None, *, pages: int = 1, current: int = 1):
        super().__init__(parent)
        self._pages = 1
        self._current = 1
        self._force: str | None = None
        self._hidden_for_count = False
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(theme.PAGE_GAP)
        self._previous = IconButton("chevL", "Previous page", size="sm")
        self._next = IconButton("chevR", "Next page", size="sm")
        self._previous.clicked.connect(lambda: self.set_current(self._current - 1, user=True))
        self._next.clicked.connect(lambda: self.set_current(self._current + 1, user=True))
        self._cells: list[QWidget] = []
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.set_pages(pages, current)

    # -- pages

    def pages(self) -> int:
        return self._pages

    def current(self) -> int:
        return self._current

    def numbers(self) -> list[int | None]:
        return page_numbers(self._pages, self._current)

    def set_pages(self, pages: int, current: int | None = None) -> None:
        self._pages = max(0, pages)
        self._current = max(1, min(current if current is not None else self._current,
                                   max(1, self._pages)))
        self._rebuild()
        if self._pages < 2:
            self._hidden_for_count = True
            self.hide()
        elif self._hidden_for_count:
            self._hidden_for_count = False
            if self.parentWidget() is not None:
                self.show()

    def set_current(self, page: int, *, user: bool = False) -> None:
        page = max(1, min(page, max(1, self._pages)))
        if page == self._current:
            return
        self._current = page
        self._rebuild()
        if user:
            self.page_changed.emit(page)

    # -- the debug-only override, passed to the page after the current one

    @property
    def force_state(self) -> str | None:
        return self._force

    @force_state.setter
    def force_state(self, state: str | None) -> None:
        if state not in STATES:
            raise ValueError(f"no state {state!r}; there are {STATES[1:]} and None")
        self._force = state
        self._rebuild()

    # -- building

    def _rebuild(self) -> None:
        while self._row.count():
            item = self._row.takeAt(0)
            widget = item.widget()
            if widget is not None and widget not in (self._previous, self._next):
                widget.deleteLater()
        self._cells = []
        self._row.addWidget(self._previous)
        forced = False
        for n in page_numbers(self._pages, self._current):
            if n is None:
                dots = label("…", "type.mono", "lo")
                dots.setContentsMargins(theme.SP_2, 0, theme.SP_2, 0)
                self._row.addWidget(dots)
                self._cells.append(dots)
                continue
            cell = _PageCell(n, n == self._current)
            cell.clicked.connect(lambda _=False, page=n: self.set_current(page, user=True))
            if self._force and not forced and n > self._current:
                cell.force_state = self._force
                forced = True
            self._row.addWidget(cell)
            self._cells.append(cell)
        self._row.addWidget(self._next)
        self._previous.setEnabled(self._current > 1)
        self._next.setEnabled(self._current < self._pages)
