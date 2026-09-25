"""Buttons: AccentButton, SecondaryButton, DangerButton, GhostButton, IconButton.

Each variant's five states are the design system's Buttons section, value for
value. Buttons are painted rather than left to the stylesheet for three things
a stylesheet cannot do: ease the fill between states (motion.base), draw the
shadow and the focus ring outside the box without moving it, and show that
ring only when the keyboard brought focus there.

- AccentButton: the one action that starts the work — one per screen.
- SecondaryButton: everything else of weight.
- DangerButton: destructive, inside a confirmation only. It is never a
  dialog's default button, so Enter cannot delete.
- GhostButton: inline and low weight; `outlined=True` is the flat, edged
  variant the page headers use ("Skip for now", "Run settings").
- IconButton: a glyph in a 30 px square (22 px in a Pagination). It says what
  it does in a tool tip, which it requires.

A text button can lead with an icon (`icon="folder"`) and end with a key cap
(`key="Ctrl+V"`). Sizes: "sm" for inline actions in a card header, "md" (the
default), "lg" for an empty state's one action. Every variant of a size has the
same box, so a row of them lines up.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QMargins, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QPushButton, QStyle, QToolButton, QWidget

from ... import theme
from . import icons
from .base import Caster, Fade, Interactive, elevation_margins, follow, token

# variant → state → (fill, edge, text). A colour is a token, `token/alpha`, or
# None. Only the fill moves between states; edge and text change at once.
LOOKS: dict[str, dict[str, tuple[str | None, str | None, str]]] = {
    "accent": {
        "default": ("accent", None, "text.onAccent"),
        "hover": ("accent.hover", None, "text.onAccent"),
        "pressed": ("accent.press", None, "text.onAccent"),
        "disabled": ("accent/.3", None, "text.onAccent/.5"),
    },
    "secondary": {
        "default": ("surface.wash", "border.control", "text.body"),
        "hover": ("surface.washHover", "border.strong", "text.body"),
        "pressed": ("surface.washPress", "border.strong", "text.body"),
        "disabled": ("surface.washDisabled", "border.control", "text.disabled"),
    },
    "danger": {
        "default": ("danger.solid", None, "text.onDanger"),
        "hover": ("danger.solidHover", None, "text.onDanger"),
        "pressed": ("danger.solidPress", None, "text.onDanger"),
        "disabled": ("danger.solid/.3", None, "text.onDanger/.5"),
    },
    "ghost": {
        "default": (None, None, "text.mid"),
        "hover": ("surface.raised", None, "text.hi"),
        "pressed": ("surface.press", None, "text.mid"),
        "disabled": (None, None, "text.disabled"),
    },
    # GhostButton(outlined=True): a SecondaryButton without sheen or shadow
    "outlined": {
        "default": ("surface.wash", "border.control", "text.body"),
        "hover": ("surface.washHover", "border.strong", "text.body"),
        "pressed": ("surface.washPress", "border.strong", "text.body"),
        "disabled": ("surface.washDisabled", "border.control", "text.disabled"),
    },
    # IconButton; disabled is the default look at 40 %
    "icon": {
        "default": (None, None, "text.mid"),
        "hover": ("surface.raised", None, "text.hi"),
        "pressed": ("surface.press", None, "text.mid"),
        "disabled": (None, None, "text.mid"),
    },
}
RAISED = ("accent", "secondary", "danger")      # sheen and elev.1 at rest
EDGED = ("secondary", "outlined")               # their edge turns border.focus with the ring


def _plain(text: str) -> str:
    """Button text as it reads: `&Save` is "Save", `R&&D` is "R&D"."""
    return text.replace("&&", "\0").replace("&", "").replace("\0", "&")


class _Painted(Interactive, Caster):
    """What every button shares: the state table, the easing fill, and the
    shadow and ring drawn on the surface behind."""

    _variant = "secondary"

    def _setup(self) -> None:
        self._fade = Fade(self, token(self._look()[0]))
        self.pressed.connect(self.state_changed)
        self.released.connect(self.state_changed)

    @property
    def variant(self) -> str:
        return self._variant

    def is_down(self) -> bool:
        return self.isDown()

    def _look(self) -> tuple[str | None, str | None, str]:
        return LOOKS[self._variant][self.visual_state()]

    def _radius(self) -> float:
        return theme.R_MD

    def state_changed(self) -> None:
        fade = getattr(self, "_fade", None)
        if fade is not None:
            # a forced state is a still picture: no easing into it
            fade.go(token(self._look()[0]), animate=self._kit_force is None)
        super().state_changed()

    # -- outside the box: the resting shadow, and the keyboard's ring

    def outside_margins(self) -> QMargins:
        return elevation_margins("elev.1" if self._variant in RAISED else "elev.0")

    def paint_outside(self, painter: QPainter) -> None:
        box = QRectF(self.rect())
        if self._variant in RAISED and self.visual_state() in ("default", "hover"):
            theme.paint_shadow(painter, box, "elev.1", self._radius())
        if self.focus_visible():
            theme.paint_focus_ring(painter, box, self._radius())

    # -- the box

    def _paint_box(self, painter: QPainter) -> str:
        """Fill, depth and edge. Returns the text colour for what goes on top."""
        state = self.visual_state()
        _, edge, text = self._look()
        box = QRectF(self.rect())
        r = self._radius()
        fill = self._fade.value()
        if fill.alpha():
            path = QPainterPath()
            path.addRoundedRect(box, r, r)
            painter.fillPath(path, fill)
        if state == "pressed":
            theme.paint_shadow(painter, box, "elev.inset", r)
        elif self._variant in RAISED and state != "disabled":
            theme.paint_sheen(painter, box, r)
        if self._variant in EDGED and self.focus_visible():
            edge = "border.focus"
        if edge:
            painter.setPen(QPen(token(edge), 1))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5), r - 0.5, r - 0.5)
        return text


class _TextButton(_Painted, QPushButton):
    """A button with words: an optional leading icon, the text, an optional key cap."""

    def __init__(self, text: str = "", parent: QWidget | None = None, *,
                 icon: str | None = None, key: str | None = None, size: str = "md"):
        QPushButton.__init__(self, text, parent)
        if size not in theme.BUTTON:
            raise KeyError(f"no button size {size!r}; there are {', '.join(theme.BUTTON)}")
        if icon is not None:
            icons.svg(icon)             # an unknown name fails here, not at paint time
        self._size = theme.BUTTON[size]
        self._icon = icon
        self._key = key
        if key:
            self.setAccessibleDescription(f"Shortcut: {key}")
        self._setup()

    # -- content

    def set_icon(self, name: str | None) -> None:
        if name is not None:
            icons.svg(name)
        self._icon = name
        self.updateGeometry()
        self.update()

    def set_key(self, key: str | None) -> None:
        self._key = key
        self.setAccessibleDescription(f"Shortcut: {key}" if key else "")
        self.updateGeometry()
        self.update()

    def _metrics(self) -> QFontMetricsF:
        return QFontMetricsF(theme.font(self._size.font))

    def _key_size(self) -> tuple[float, float]:
        """The key cap's width and height."""
        fm = QFontMetricsF(theme.font("type.monoSm"))
        return (fm.horizontalAdvance(self._key) + 2 * theme.KEY_CAP_PAD + 2, fm.height() + 2)

    def _content_width(self) -> float:
        s = self._size
        parts = []
        if self._icon:
            parts.append(s.icon)
        if self.text():
            parts.append(self._metrics().horizontalAdvance(_plain(self.text())))
        if self._key:
            parts.append(self._key_size()[0])
        return sum(parts) + s.gap * max(0, len(parts) - 1)

    def sizeHint(self) -> QSize:                # noqa: N802 - Qt's name
        width = math.ceil(self._content_width()) + 2 * (self._size.pad + 1)
        return QSize(width, self._size.height)

    def minimumSizeHint(self) -> QSize:         # noqa: N802 - Qt's name
        return self.sizeHint()

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        follow(self)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        colour = token(self._paint_box(painter))
        s = self._size
        x = (self.width() - self._content_width()) / 2
        middle = self.height() / 2
        if self._icon:
            glyph = icons.pixmap(self._icon, colour, s.icon, self.devicePixelRatioF())
            painter.drawPixmap(QPointF(x, middle - s.icon / 2), glyph)
            x += s.icon + s.gap
        if self.text():
            width = self._metrics().horizontalAdvance(_plain(self.text()))
            flags = Qt.AlignLeft | Qt.AlignVCenter
            flags |= (Qt.TextShowMnemonic if self.style().styleHint(QStyle.SH_UnderlineShortcut)
                      else Qt.TextHideMnemonic)
            painter.setFont(theme.font(s.font))
            painter.setPen(colour)
            painter.drawText(QRectF(x, 0, width + 1, self.height()), flags, self.text())
            x += width + s.gap
        if self._key:
            self._paint_key(painter, x, middle)

    def _paint_key(self, painter: QPainter, x: float, middle: float) -> None:
        """`Ctrl+V` in a small cap: mono, text.lo, a hairline edge, r.sm."""
        width, height = self._key_size()
        cap = QRectF(x, middle - height / 2, width, height)
        faded = not self.isEnabled()
        painter.setPen(QPen(token("border.control"), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(cap.adjusted(0.5, 0.5, -0.5, -0.5), theme.R_SM, theme.R_SM)
        painter.setFont(theme.font("type.monoSm"))
        painter.setPen(token("text.disabled" if faded else "text.lo"))
        painter.drawText(cap, Qt.AlignCenter, self._key)



class AccentButton(_TextButton):
    """The one action that starts the work. One per screen."""

    _variant = "accent"


class SecondaryButton(_TextButton):
    """An action of weight that is not the screen's main one."""

    _variant = "secondary"


class DangerButton(_TextButton):
    """A destructive action, inside a confirmation that names the count.

    It is never a dialog's default button: a dialog does not give it Enter on
    showing, it does not take Enter when it gets focus, and asking for either
    is an error.
    """

    _variant = "danger"

    def __init__(self, text: str = "", parent: QWidget | None = None, **kwargs):
        super().__init__(text, parent, **kwargs)
        QPushButton.setAutoDefault(self, False)

    def setDefault(self, on: bool) -> None:     # noqa: N802 - Qt's name
        if on:
            raise ValueError("a DangerButton is never the default button: "
                             "Enter must not destroy anything")
        QPushButton.setDefault(self, False)

    def setAutoDefault(self, on: bool) -> None:  # noqa: N802 - Qt's name
        if on:
            raise ValueError("a DangerButton never becomes the default button on focus")
        QPushButton.setAutoDefault(self, False)

    def event(self, event) -> bool:
        # QDialogButtonBox makes its first accept button the default from C++,
        # where the override above cannot see it. Undo that before anything
        # else happens to the button.
        if QPushButton.isDefault(self):
            QPushButton.setDefault(self, False)
        return super().event(event)


class GhostButton(_TextButton):
    """An inline, low-weight action ("Open folder"). `outlined=True` gives it
    the flat edged box the page headers use."""

    def __init__(self, text: str = "", parent: QWidget | None = None, *,
                 outlined: bool = False, **kwargs):
        self._variant = "outlined" if outlined else "ghost"
        super().__init__(text, parent, **kwargs)

    @property
    def outlined(self) -> bool:
        return self._variant == "outlined"


class IconButton(_Painted, QToolButton):
    """A glyph button. The tool tip says what it does, so it is required, and
    it is the button's accessible name too."""

    _variant = "icon"

    def __init__(self, icon: str, tooltip: str, parent: QWidget | None = None, *,
                 size: str = "md"):
        QToolButton.__init__(self, parent)
        if not tooltip or not tooltip.strip():
            raise ValueError("an IconButton needs a tool tip that says what it does")
        if size not in theme.ICON_BUTTON:
            raise KeyError(f"no IconButton size {size!r}; there are {', '.join(theme.ICON_BUTTON)}")
        icons.svg(icon)
        self._icon = icon
        self._side, self._glyph = theme.ICON_BUTTON[size]
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)
        self.setFixedSize(self._side, self._side)
        self._setup()

    def set_icon(self, name: str) -> None:
        icons.svg(name)
        self._icon = name
        self.update()

    def setToolTip(self, text: str) -> None:    # noqa: N802 - Qt's name
        if not text or not text.strip():
            raise ValueError("an IconButton needs a tool tip that says what it does")
        QToolButton.setToolTip(self, text)
        self.setAccessibleName(text)

    def _radius(self) -> float:
        return theme.R_MD if self._side >= theme.ICON_BUTTON["md"][0] else theme.R_SM

    def sizeHint(self) -> QSize:                # noqa: N802 - Qt's name
        return QSize(self._side, self._side)

    def minimumSizeHint(self) -> QSize:         # noqa: N802 - Qt's name
        return self.sizeHint()

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        follow(self)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if not self.isEnabled():
            painter.setOpacity(theme.DISABLED_OPACITY)
        colour = token(self._paint_box(painter))
        glyph = icons.pixmap(self._icon, colour, self._glyph, self.devicePixelRatioF())
        offset = (self._side - self._glyph) / 2
        painter.drawPixmap(QPointF(offset, offset), glyph)
