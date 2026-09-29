"""The frame every page sits in: the title bar, the sidebar and the page header.

The frame never moves. Pages change inside it (a cross-fade of the content
only); the sidebar and the status line stay put, and they are live:

- `TitleBar`: the window's own 32 px bar — the app's mark, its name, and the
  minimise, maximise and close buttons. Windows still does the window's work
  (dragging, snapping, resizing): see `app/window_frame.py`.
- `Sidebar`: 246 px of `nav.gradient` holding the pages in the order of the
  loop, in `NavSection`s, and "Next in the loop" above Settings at the foot.
  Below `theme.RAIL_BELOW` it folds into a 56 px rail of icons, their names in
  tool tips, their states as dots.
- `NavItem`: a page's icon and name, and what that page is doing, as one of
  the design's `NavState`s: a progress bar and a percentage, a count and a
  mini bar, a warn badge, or a status word.
- `NextInLoop`: one sentence about what comes next ("197 left on Monitor1 —
  rotate again ≈21 Sep").
- `PageHeader`: the page's title, a line under it, and its actions.
"""
from __future__ import annotations

import html
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QMargins, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractButton, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget,
)

from ... import theme
from . import base, icons
from . import format as fmt
from .base import Caster, Elided, Interactive, elevation_margins, follow, label

NAV_KINDS = ("none", "progress", "count", "badge", "status")
# A status word's tone → its colour. The quiet one is the sidebar's own grey.
NAV_TONES = {"lo": "nav.overline", "ok": "ok", "warn": "warn", "danger": "danger",
             "accent": "accent.hover"}


# ---- what a page says about itself in the sidebar ---------------------------------------

@dataclass(frozen=True)
class NavState:
    """What a nav item shows beside its page's name. Make one with a constructor:

        NavState.progress(412, 1000)       # a bar and "41%", under the name
        NavState.count(4, 201)             # "4/201" at the right, a mini bar under
        NavState.badge(12)                 # a warn badge at the right
        NavState.status("idle")            # a quiet word at the right
        NavState.status("2 problems", "warn", below=True)   # a word under the name
        NavState()                         # nothing
    """
    kind: str = "none"
    text: str = ""
    fraction: float = 0.0
    tone: str = "lo"
    below: bool = False

    def __post_init__(self):
        if self.kind not in NAV_KINDS:
            raise ValueError(f"no nav state {self.kind!r}; there are {', '.join(NAV_KINDS)}")
        if self.tone not in NAV_TONES:
            raise ValueError(f"no nav tone {self.tone!r}; there are {', '.join(NAV_TONES)}")

    @classmethod
    def progress(cls, done: float, total: float) -> "NavState":
        fraction = max(0.0, min(1.0, done / total)) if total > 0 else 0.0
        return cls("progress", fmt.percent(done, total), fraction, "accent", True)

    @classmethod
    def count(cls, done: int, total: int) -> "NavState":
        fraction = max(0.0, min(1.0, done / total)) if total > 0 else 0.0
        return cls("count", fmt.ratio(done, total, "nav"), fraction)

    @classmethod
    def badge(cls, value) -> "NavState":
        text = fmt.count(value) if isinstance(value, int) else str(value)
        return cls("badge", text, tone="warn")

    @classmethod
    def status(cls, text: str, tone: str = "lo", *, below: bool = False) -> "NavState":
        return cls("status", text, tone=tone, below=below)

    @property
    def two_lines(self) -> bool:
        """Whether the item grows a second line under its name."""
        return self.kind in ("progress", "count") or (self.kind == "status" and self.below)

    def dot(self) -> str | None:
        """The colour of the dot that stands for this state in the rail, if any."""
        if self.kind == "progress":
            return "accent"
        if self.kind == "badge":
            return "warn"
        if self.kind == "status" and self.tone in ("warn", "danger"):
            return self.tone
        return None


# ---- the app's mark ----------------------------------------------------------------------

class BrandMark(QWidget):
    """Two overlapping rounded rectangles, a grey outline and an accent fill, on a
    dark tile: the Branding page's mark, drawn at any size."""

    # On the design's 64-unit grid: the tile, the outline, the fill, and their
    # radii and strokes.
    _TILE = (2, 2, 60, 60, 14)
    _OUTLINE = (13, 15, 25, 20, 4, 4)
    _FILL = (24, 27, 27, 22, 4, 3)

    def __init__(self, size: int = theme.TITLE_MARK, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        k = self.width() / 64
        x, y, w, h, r = self._TILE
        tile = QRectF(x * k, y * k, w * k, h * k)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(theme.gradient("brand.tile", tile)))
        painter.drawRoundedRect(tile, r * k, r * k)
        x, y, w, h, r, stroke = self._OUTLINE
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(theme.color("text.lo"), stroke * k))
        painter.drawRoundedRect(QRectF(x * k, y * k, w * k, h * k), r * k, r * k)
        x, y, w, h, r, stroke = self._FILL
        painter.setBrush(theme.color("accent"))
        painter.setPen(QPen(theme.color("brand.edge"), stroke * k))
        painter.drawRoundedRect(QRectF(x * k, y * k, w * k, h * k), r * k, r * k)


# ---- the title bar ----------------------------------------------------------------------

CAPTION_KINDS = ("minimise", "maximise", "close")


class CaptionButton(Interactive, QAbstractButton):
    """Minimise, maximise (restore once maximised) or close: 42 × 32, a thin glyph.

    Over the maximise button the pointer belongs to Windows, which shows its
    snap layouts there; `set_native(hover=…, down=…)` is how the window passes
    that pointer's state on, since Qt never hears of it.
    """

    _TIPS = {"minimise": "Minimise", "maximise": "Maximise", "close": "Close"}

    def __init__(self, kind: str, parent: QWidget | None = None):
        QAbstractButton.__init__(self, parent)
        if kind not in CAPTION_KINDS:
            raise ValueError(f"no caption button {kind!r}; there are {', '.join(CAPTION_KINDS)}")
        self.kind = kind
        self._maximised = False
        self._native_hover = False
        self._native_down = False
        self.setFixedSize(*theme.CAPTION_BUTTON)
        self.setFocusPolicy(Qt.NoFocus)         # Windows' own are not tab stops either
        self.setAttribute(Qt.WA_Hover)
        self._describe()

    def is_down(self) -> bool:
        return self.isDown() or self._native_down

    def visual_state(self) -> str:
        state = super().visual_state()
        if state == "default" and self._native_hover:
            return "hover"
        return state

    def set_maximised(self, on: bool) -> None:
        if on != self._maximised:
            self._maximised = on
            self._describe()
            self.update()

    def maximised(self) -> bool:
        return self._maximised

    def set_native(self, *, hover: bool | None = None, down: bool | None = None) -> None:
        changed = False
        if hover is not None and hover != self._native_hover:
            self._native_hover, changed = hover, True
        if down is not None and down != self._native_down:
            self._native_down, changed = down, True
        if changed:
            self.update()

    def native_hover(self) -> bool:
        return self._native_hover

    def _describe(self) -> None:
        tip = "Restore" if self.kind == "maximise" and self._maximised else self._TIPS[self.kind]
        self.setToolTip(tip)
        self.setAccessibleName(tip)

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        painter = QPainter(self)
        state = self.visual_state()
        glyph = "text.lo"
        if self.kind == "close" and state in ("hover", "pressed"):
            painter.fillRect(self.rect(), theme.color(
                "danger.solidPress" if state == "pressed" else "danger.solid"))
            glyph = "text.onDanger"
        elif state in ("hover", "pressed"):
            painter.fillRect(self.rect(), theme.color(
                "surface.washPress" if state == "pressed" else "surface.wash"))
            glyph = "text.body"
        painter.setRenderHint(QPainter.Antialiasing, self.kind == "close")
        pen = QPen(theme.color(glyph), 1)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        side = theme.CAPTION_GLYPH
        box = QRectF((self.width() - side) / 2, (self.height() - side) / 2, side, side)
        if self.kind == "minimise":
            y = round(box.center().y()) + 0.5
            painter.drawLine(QPointF(box.left(), y), QPointF(box.right(), y))
        elif self.kind == "maximise" and not self._maximised:
            painter.drawRect(box.adjusted(1.5, 1.5, -0.5, -0.5))
        elif self.kind == "maximise":
            # restore: the front window, and the corner of the one behind it
            front = box.adjusted(0.5, 2.5, -2.5, -0.5)
            painter.drawRect(front)
            painter.drawPolyline([QPointF(front.left() + 2, front.top()),
                                  QPointF(front.left() + 2, box.top() + 0.5),
                                  QPointF(box.right() - 0.5, box.top() + 0.5),
                                  QPointF(box.right() - 0.5, front.bottom() - 2),
                                  QPointF(front.right(), front.bottom() - 2)])
        else:
            painter.drawLine(box.topLeft() + QPointF(0.5, 0.5), box.bottomRight() - QPointF(0.5, 0.5))
            painter.drawLine(QPointF(box.right() - 0.5, box.top() + 0.5),
                             QPointF(box.left() + 0.5, box.bottom() - 0.5))


class TitleBar(QWidget):
    """The window's title bar: the mark, the name, and the three buttons.

    Where there is no native frame to lean on (offscreen, in tests and
    snapshots) it moves the window itself, and a double click maximises.
    """

    def __init__(self, title: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedHeight(theme.TITLE_BAR_HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        row = QHBoxLayout(self)
        left, right = theme.TITLE_BAR_PAD
        row.setContentsMargins(left, 0, right, 1)
        row.setSpacing(theme.TITLE_BAR_GAP)
        self.mark = BrandMark(theme.TITLE_MARK)
        self._name = label(title, "type.bodySm", "mid")
        self._name.setAttribute(Qt.WA_TransparentForMouseEvents)
        row.addWidget(self.mark, 0, Qt.AlignVCenter)
        row.addWidget(self._name, 0, Qt.AlignVCenter)
        row.addStretch(1)
        buttons = QHBoxLayout()
        buttons.setSpacing(0)
        buttons.setContentsMargins(0, 0, 0, 0)
        self.minimise_button = CaptionButton("minimise")
        self.maximise_button = CaptionButton("maximise")
        self.close_button = CaptionButton("close")
        for button in (self.minimise_button, self.maximise_button, self.close_button):
            buttons.addWidget(button, 0, Qt.AlignTop)
        row.addLayout(buttons)
        self.minimise_button.clicked.connect(lambda: self.window().showMinimized())
        self.maximise_button.clicked.connect(self.toggle_maximised)
        self.close_button.clicked.connect(lambda: self.window().close())
        self._watched = None

    def title(self) -> str:
        return self._name.text()

    def set_title(self, text: str) -> None:
        self._name.setText(text)

    def toggle_maximised(self) -> None:
        window = self.window()
        if window.isMaximized():
            window.showNormal()
        else:
            window.showMaximized()

    def area(self, point) -> str:
        """What is under a point of the bar (its own coordinates): "maximise",
        "button" (minimise or close), or "caption" (the bar itself, for dragging)."""
        for button in (self.minimise_button, self.maximise_button, self.close_button):
            if button.isVisible() and button.geometry().contains(point):
                return "maximise" if button is self.maximise_button else "button"
        return "caption"

    # -- the window it belongs to: maximised or not, active or not

    def showEvent(self, event) -> None:         # noqa: N802 - Qt's name
        window = self.window()
        if window is not self and window is not self._watched:
            window.installEventFilter(self)
            self._watched = window
        self._sync()
        super().showEvent(event)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt's name
        if event.type() in (QEvent.WindowStateChange, QEvent.ActivationChange):
            self._sync()
        return False

    def _sync(self) -> None:
        window = self.window()
        self.maximise_button.set_maximised(window.isMaximized())
        base.set_tone(self._name, "mid" if window.isActiveWindow() else "lo")

    # -- the fallback: moving the window without a native frame

    def mousePressEvent(self, event) -> None:   # noqa: N802 - Qt's name
        handle = self.window().windowHandle()
        if event.button() == Qt.LeftButton and handle is not None:
            handle.startSystemMove()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:     # noqa: N802 - Qt's name
        if event.button() == Qt.LeftButton:
            self.toggle_maximised()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        painter = QPainter(self)
        painter.fillRect(self.rect(), theme.color("chrome.titlebar"))
        painter.fillRect(QRectF(0, self.height() - 1, self.width(), 1), theme.color("chrome.divider"))


# ---- the sidebar ----------------------------------------------------------------------

def _line(token: str) -> float:
    return theme.line_height(token)


def _text(token: str) -> float:
    """A line of small mono text as the design sets it: the font's own height
    (CSS `line-height: normal`), not the type scale's airier line."""
    return round(QFontMetricsF(theme.font(token)).height())


class NavItem(Interactive, Caster, QAbstractButton):
    """A page in the sidebar: its icon, its name, and its `NavState`."""

    def __init__(self, key: str, text: str, icon: str, parent: QWidget | None = None):
        QAbstractButton.__init__(self, parent)
        self.key = key
        self.icon_name = icon
        self._state = NavState()
        self._selected = False
        self._rail = False
        self.setText(text)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.TabFocus)
        self.setAttribute(Qt.WA_Hover)
        self.setAccessibleName(text)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._resize()

    # -- what it shows

    def state(self) -> NavState:
        return self._state

    def set_state(self, state: NavState) -> None:
        if state == self._state:
            return
        grew = state.two_lines != self._state.two_lines
        self._state = state
        self._describe()
        if grew:
            self._resize()
        self.update()

    def selected(self) -> bool:
        return self._selected

    def set_selected(self, on: bool) -> None:
        if on != self._selected:
            self._selected = on
            self.setAccessibleDescription(self._description())
            self.state_changed()

    def rail(self) -> bool:
        return self._rail

    def set_rail(self, on: bool) -> None:
        if on != self._rail:
            self._rail = on
            self._describe()
            self._resize()
            self.update()

    def meta_text(self) -> str:
        """The words beside or under the name ("41%", "4/201", "12", "idle")."""
        return self._state.text

    def _description(self) -> str:
        parts = [self._state.text] if self._state.text else []
        if self._selected:
            parts.append("current page")
        return " · ".join(parts)

    def _describe(self) -> None:
        self.setAccessibleDescription(self._description())
        if self._rail:
            tip = self.text() + (f" · {self._state.text}" if self._state.text else "")
            self.setToolTip(tip)
        else:
            self.setToolTip("")

    # -- size

    def _height(self) -> int:
        if self._rail:
            return theme.RAIL_ITEM
        if not self._state.two_lines:
            return round(2 * theme.NAV_PAD[0] + _line("type.nav"))
        second = theme.NAV_BAR if self._state.kind == "count" else _text("type.monoXs")
        return round(2 * theme.NAV_PAD_TWO_LINES + _line("type.nav") + theme.NAV_LINE_GAP + second)

    def _resize(self) -> None:
        height = self._height()
        if self._rail:
            self.setFixedSize(theme.RAIL_ITEM, height)
        else:
            self.setMinimumWidth(0)
            self.setMaximumWidth(16_777_215)
            self.setFixedHeight(height)
        self.updateGeometry()
        base.refresh(self)

    def sizeHint(self) -> QSize:                # noqa: N802 - Qt's name
        if self._rail:
            return QSize(theme.RAIL_ITEM, theme.RAIL_ITEM)
        return QSize(theme.SIDEBAR_WIDTH - 2 * theme.SIDEBAR_PAD[1], self._height())

    # -- keys

    def keyPressEvent(self, event) -> None:     # noqa: N802 - Qt's name
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.click()
            event.accept()
            return
        super().keyPressEvent(event)

    # -- painting

    def outside_margins(self) -> QMargins:
        return elevation_margins("elev.1")

    def paint_outside(self, painter: QPainter) -> None:
        box = QRectF(self.rect())
        if self._selected:
            theme.paint_shadow(painter, box, "elev.1", theme.R_ROW)
        if self.focus_visible():
            theme.paint_focus_ring(painter, box, theme.R_ROW)

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        follow(self)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        box = QRectF(self.rect())
        shape = QPainterPath()
        shape.addRoundedRect(box, theme.R_ROW, theme.R_ROW)
        state = self.visual_state()
        if self._selected:
            painter.fillPath(shape, theme.color("nav.selected"))
            painter.save()
            painter.setClipPath(shape)
            painter.fillRect(QRectF(box.left(), box.top(), box.width(), 1.0),
                             theme.color("nav.selectedSheen"))
            painter.restore()
        elif state == "pressed":
            painter.fillPath(shape, theme.color("surface.washPress"))
        elif state == "hover":
            painter.fillPath(shape, theme.color("surface.wash"))

        dpr = self.devicePixelRatioF()
        glyph = "text.hi" if self._selected else "text.mid"
        size = theme.NAV_ICON
        if self._rail:
            corner = QPointF((self.width() - size) / 2, (self.height() - size) / 2)
            painter.drawPixmap(corner, icons.pixmap(self.icon_name, glyph, size, dpr))
            dot = self._state.dot()
            if dot is not None:
                d = theme.RAIL_DOT
                painter.setPen(Qt.NoPen)
                painter.setBrush(theme.color(dot))
                painter.drawEllipse(QRectF(corner.x() + size - d / 2, corner.y() - d / 2, d, d))
            return

        pad_h = theme.NAV_PAD[1]
        painter.drawPixmap(QPointF(pad_h, (self.height() - size) / 2),
                           icons.pixmap(self.icon_name, glyph, size, dpr))
        left = pad_h + size + theme.NAV_GAP
        right = self.width() - pad_h
        s = self._state
        top = theme.NAV_PAD_TWO_LINES if s.two_lines else theme.NAV_PAD[0]
        name_h = _line("type.nav")
        name_right = right

        # what sits at the right of the name: a count, a badge, a quiet word
        if s.kind == "badge" and s.text:
            font = theme.font("type.badge")
            text_w = QFontMetricsF(font).horizontalAdvance(s.text)
            pad_v, pad_x = theme.NAV_BADGE_PAD
            badge_h = _line("type.badge") + 2 * pad_v
            pill = QRectF(right - text_w - 2 * pad_x, top + (name_h - badge_h) / 2,
                          text_w + 2 * pad_x, badge_h)
            painter.setPen(Qt.NoPen)
            painter.setBrush(theme.color("warn"))
            radius = min(theme.NAV_BADGE_RADIUS, badge_h / 2)
            painter.drawRoundedRect(pill, radius, radius)
            painter.setFont(font)
            painter.setPen(theme.color("text.onAccent"))
            painter.drawText(pill, Qt.AlignCenter, s.text)
            name_right = pill.left() - theme.NAV_META_GAP
        elif (s.kind == "count" or (s.kind == "status" and not s.below)) and s.text:
            font = theme.font("type.monoXs")
            text_w = QFontMetricsF(font).horizontalAdvance(s.text)
            painter.setFont(font)
            painter.setPen(theme.color("text.mid" if s.kind == "count" else NAV_TONES[s.tone]))
            meta = QRectF(right - text_w, top, text_w, name_h)
            painter.drawText(meta, Qt.AlignRight | Qt.AlignVCenter, s.text)
            name_right = meta.left() - theme.NAV_META_GAP

        font = theme.font("type.nav")
        painter.setFont(font)
        painter.setPen(theme.color("text.hi" if self._selected else "text.body"))
        name = QFontMetricsF(font).elidedText(self.text(), Qt.ElideRight, max(0.0, name_right - left))
        painter.drawText(QRectF(left, top, max(0.0, name_right - left), name_h),
                         Qt.AlignLeft | Qt.AlignVCenter, name)

        if not s.two_lines:
            return
        second = top + name_h + theme.NAV_LINE_GAP
        if s.kind == "status":
            painter.setFont(theme.font("type.monoXs"))
            painter.setPen(theme.color(NAV_TONES[s.tone]))
            line = QRectF(left, second, right - left, _text("type.monoXs"))
            words = QFontMetricsF(painter.font()).elidedText(s.text, Qt.ElideRight, line.width())
            painter.drawText(line, Qt.AlignLeft | Qt.AlignVCenter, words)
            return
        bar_right = right
        bar_mid = second + theme.NAV_BAR / 2
        fill = "text.mid"
        if s.kind == "progress":
            fill = "accent.hover"
            font = theme.font("type.monoXs")
            text_w = QFontMetricsF(font).horizontalAdvance(s.text)
            line_h = _text("type.monoXs")
            painter.setFont(font)
            painter.setPen(theme.color(fill))
            painter.drawText(QRectF(right - text_w, second, text_w, line_h),
                             Qt.AlignRight | Qt.AlignVCenter, s.text)
            bar_right = right - text_w - theme.NAV_META_GAP
            bar_mid = second + line_h / 2
        track = QRectF(left, bar_mid - theme.NAV_BAR / 2, max(0.0, bar_right - left), theme.NAV_BAR)
        radius = theme.NAV_BAR / 2
        painter.setPen(Qt.NoPen)
        painter.setBrush(theme.color("surface.well"))
        painter.drawRoundedRect(track, radius, radius)
        if s.fraction > 0:
            painter.save()
            clip = QPainterPath()
            clip.addRoundedRect(track, radius, radius)
            painter.setClipPath(clip)
            painter.fillRect(QRectF(track.left(), track.top(),
                                    max(theme.NAV_BAR, track.width() * s.fraction), track.height()),
                             theme.color(fill))
            painter.restore()


class NavSection(QWidget):
    """A group's overline in the sidebar ("THE LOOP", "UTILITIES"). In the rail
    the words give way to a short rule, or to nothing for the first group."""

    def __init__(self, title: str, *, first: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self._title = title
        self._first = first
        self._rail = False
        self.setAccessibleName(title.title())
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._resize()

    def title(self) -> str:
        return self._title

    def set_rail(self, on: bool) -> None:
        if on != self._rail:
            self._rail = on
            self._resize()
            self.update()

    def _resize(self) -> None:
        above = 0 if self._first else theme.NAV_SECTION_GAP
        if self._rail:
            self.setFixedHeight(above if self._first else above + 1 + theme.NAV_SECTION_PAD[1])
        else:
            self.setFixedHeight(round(above + _text("type.overline") + theme.NAV_SECTION_PAD[1]))

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        painter = QPainter(self)
        above = 0 if self._first else theme.NAV_SECTION_GAP
        if self._rail:
            if not self._first:
                inset = theme.NAV_PAD[1]
                painter.fillRect(QRectF(inset, above / 2, self.width() - 2 * inset, 1),
                                 theme.color("chrome.divider"))
            return
        font = theme.font("type.overline")
        painter.setFont(font)
        painter.setPen(theme.color("nav.overline"))
        side = theme.NAV_SECTION_PAD[0]
        painter.drawText(QRectF(side, above, self.width() - 2 * side, _text("type.overline")),
                         Qt.AlignLeft | Qt.AlignVCenter, self._title.upper())


class NextInLoop(QWidget):
    """"NEXT IN THE LOOP" and a sentence about it. A `≈` or `~` in the sentence is
    set in the quieter grey, as the design writes its qualifiers."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        top, side, bottom = theme.NEXT_PAD
        column = QVBoxLayout(self)
        column.setContentsMargins(side, top, side, bottom)
        column.setSpacing(theme.NEXT_GAP)
        self._overline = QLabel("NEXT IN THE LOOP")
        self._overline.setFont(theme.font("type.overline"))
        self._overline.setStyleSheet(f"color: {theme.css('nav.overline')};")
        self._text = label("", "type.bodySm", "mid")
        self._text.setWordWrap(True)
        self._text.setTextFormat(Qt.RichText)
        column.addWidget(self._overline)
        column.addWidget(self._text)
        self._plain = ""
        self.setAccessibleName("Next in the loop")

    def text(self) -> str:
        return self._plain

    def set_text(self, text: str) -> None:
        self._plain = text
        quiet = theme.css("nav.overline")
        marked = html.escape(text)
        for mark in (fmt.APPROX, fmt.RECONSTRUCTED):
            marked = marked.replace(mark, f'<span style="color:{quiet}">{mark}</span>')
        self._text.setText(marked)
        self.setAccessibleDescription(text)


class Sidebar(QWidget):
    """The pages, in the order of the loop, and what each is doing.

        sidebar.add_section("The loop", first=True)
        sidebar.add_item("overview", "Overview", "overview")
        ...
        sidebar.add_item("settings", "Settings", "settings", footer=True)
        sidebar.page_requested.connect(window.show_page)
        sidebar.set_state("rotator", NavState.progress(412, 1000))
    """

    page_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        base.declare(self)          # the selected item's shadow and focus rings
        self._items: dict[str, NavItem] = {}
        self._sections: list[NavSection] = []
        self._current = ""
        self._rail = False
        self.setFixedWidth(theme.SIDEBAR_WIDTH)
        self.setAccessibleName("Pages")
        pad_v, pad_h = theme.SIDEBAR_PAD
        column = QVBoxLayout(self)
        column.setContentsMargins(pad_h, pad_v, pad_h, pad_v)
        column.setSpacing(0)
        self._top = QVBoxLayout()
        self._top.setSpacing(0)
        column.addLayout(self._top)
        column.addStretch(1)
        self._rule = QWidget()
        self._rule.setFixedHeight(1)
        self._rule.setAttribute(Qt.WA_StyledBackground)
        self._rule.setStyleSheet(f"background: {theme.css('chrome.divider')};")
        column.addWidget(self._rule)
        column.addSpacing(theme.NAV_FOOTER_PAD)
        self.next_in_loop = NextInLoop()
        column.addWidget(self.next_in_loop)
        self._bottom = QVBoxLayout()
        self._bottom.setSpacing(0)
        column.addLayout(self._bottom)

    # -- building it

    def add_section(self, title: str) -> NavSection:
        section = NavSection(title, first=not self._sections)
        self._sections.append(section)
        self._top.addWidget(section)
        section.set_rail(self._rail)
        return section

    def add_item(self, key: str, text: str, icon: str, *, footer: bool = False) -> NavItem:
        if key in self._items:
            raise ValueError(f"there is already a nav item {key!r}")
        item = NavItem(key, text, icon)
        item.set_rail(self._rail)
        item.clicked.connect(lambda _checked=False, k=key: self.page_requested.emit(k))
        self._items[key] = item
        layout = self._bottom if footer else self._top
        last = layout.itemAt(layout.count() - 1) if layout.count() else None
        if last is not None and isinstance(last.widget(), NavItem):
            layout.addSpacing(theme.NAV_SPACING)       # items of a group, 2 px apart
        layout.addWidget(item, 0, Qt.AlignHCenter if self._rail else Qt.AlignmentFlag(0))
        return item

    # -- reading and changing it

    def item(self, key: str) -> NavItem:
        return self._items[key]

    def keys(self) -> list[str]:
        return list(self._items)

    def current(self) -> str:
        return self._current

    def set_current(self, key: str) -> None:
        self._current = key
        for k, item in self._items.items():
            item.set_selected(k == key)

    def set_state(self, key: str, state: NavState) -> None:
        self._items[key].set_state(state)

    def state(self, key: str) -> NavState:
        return self._items[key].state()

    def rail(self) -> bool:
        return self._rail

    def set_rail(self, on: bool) -> None:
        """Fold into icons only (56 px), or open out again (246 px). Instant: the
        frame does not move."""
        if on == self._rail:
            return
        self._rail = on
        self.setFixedWidth(theme.RAIL_WIDTH if on else theme.SIDEBAR_WIDTH)
        for section in self._sections:
            section.set_rail(on)
        for layout in (self._top, self._bottom):
            for i in range(layout.count()):
                widget = layout.itemAt(i).widget()
                if isinstance(widget, NavItem):
                    widget.set_rail(on)
                    layout.setAlignment(widget, Qt.AlignHCenter if on else Qt.AlignmentFlag(0))
        self.next_in_loop.setVisible(not on)

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        painter = QPainter(self)
        box = QRectF(self.rect())
        painter.fillRect(box, QBrush(theme.gradient("nav.gradient", box)))
        painter.fillRect(QRectF(box.right() - 1, 0, 1, box.height()), theme.color("nav.edge"))
        painter.fillRect(QRectF(box.right() - 2, 0, 1, box.height()), theme.color("nav.edgeInner"))
        base.paint(painter, self, event.rect())


# ---- the page header ----------------------------------------------------------------

class PageHeader(QWidget):
    """The page's title, the line beside it, and the page's actions at the right."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        base.declare(self)          # the actions' focus rings
        self.setFixedHeight(theme.HEADER_HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        row = QHBoxLayout(self)
        row.setContentsMargins(theme.HEADER_PAD, 0, theme.HEADER_PAD, 1)
        row.setSpacing(theme.HEADER_GAP)
        self._title = label("", "type.h2", "hi")
        self._subtitle = Elided("", "type.label", "lo")
        self._subtitle.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._actions_row = QHBoxLayout()
        self._actions_row.setSpacing(theme.HEADER_ACTION_GAP)
        self._actions_row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self._title, 0, Qt.AlignVCenter)
        row.addWidget(self._subtitle, 1, Qt.AlignVCenter)
        row.addLayout(self._actions_row)
        self._actions: list[QWidget] = []

    def title(self) -> str:
        return self._title.text()

    def set_title(self, text: str) -> None:
        self._title.setText(text)
        self.setAccessibleName(text)

    def subtitle(self) -> str:
        return self._subtitle.text()

    def set_subtitle(self, text: str) -> None:
        self._subtitle.set_text(text)

    def actions(self) -> list[QWidget]:
        return list(self._actions)

    def set_actions(self, widgets) -> None:
        """Show these widgets at the right, in order; the ones shown before are
        hidden, not destroyed (they belong to their page)."""
        widgets = list(widgets)
        for widget in self._actions:
            if widget not in widgets:
                self._actions_row.removeWidget(widget)
                widget.hide()
        for widget in widgets:
            self._actions_row.addWidget(widget, 0, Qt.AlignVCenter)
            widget.show()
        self._actions = widgets

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        painter = QPainter(self)
        painter.fillRect(QRectF(0, self.height() - 1, self.width(), 1), theme.color("chrome.divider"))
        base.paint(painter, self, event.rect())
