"""Chip: a small semantic label, in exactly fourteen variants.

The colour is the meaning, so the set is closed: eight variants for library
items (Review, Overview) and six for job and file state (Creator, Copier). A
screen that needs a new word reuses the nearest variant with its own text —
`Chip("Duplicated", "Already have")` — rather than inventing a colour; an
unknown variant is an error.

A chip is drawn once into a pixmap per variant, text, state and screen scale,
and copied after that: a table of 1 400 rows paints its chips from the same
few pixmaps (`chip_pixmap`, for delegates). `Chip` is the widget.
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QMargins, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QFontMetricsF, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from ... import theme
from . import icons
from .base import Caster, Interactive, follow, token


@dataclass(frozen=True)
class ChipSpec:
    label: str                  # the text when a screen gives none
    fill: str | None            # colour tokens
    edge: str | None
    text: str
    dashed: bool = False        # an edge in dashes: not known either way
    lead: str | None = None     # "dot", or an icon drawn before the text


VARIANTS: dict[str, ChipSpec] = {
    # library items
    "NewAuthor": ChipSpec("New author", "accent.soft", "accent.line", "accent.hover"),
    "Known": ChipSpec("Known", "surface.raised", None, "text.mid"),
    "Duplicated": ChipSpec("Duplicated", "warn.soft", "warn.line", "warn"),
    "Unidentified": ChipSpec("Unidentified", None, "border.control", "text.lo", dashed=True),
    "New": ChipSpec("New", "accent.soft", None, "accent.hover", lead="dot"),
    "Queued": ChipSpec("Queued", "surface.raised", None, "text.mid", lead="clock"),
    "WasYours": ChipSpec("Was yours", "ok.soft", "ok.line", "ok"),
    "Subscribed": ChipSpec("Subscribed", "info.soft", "info.line", "info"),
    # job and file state
    "Tagged": ChipSpec("Tagged", "ok.soft", "ok.line", "ok"),
    "NeedsTags": ChipSpec("Needs tags", "warn.soft", "warn.line", "warn"),
    "AutoTagged": ChipSpec("From folder name", "info.soft", "info.line", "info"),
    "Copying": ChipSpec("Copying", "accent.soft", "accent.line", "accent.hover"),
    "Done": ChipSpec("Done", "ok.soft", "ok.line", "ok"),
    "Failed": ChipSpec("Failed", "danger.soft", "danger.line", "danger"),
}

STATES = ("default", "hover", "selected", "disabled")


def spec(variant: str) -> ChipSpec:
    try:
        return VARIANTS[variant]
    except KeyError:
        raise KeyError(f"no Chip variant {variant!r}; there are exactly "
                       f"{len(VARIANTS)}: {', '.join(VARIANTS)}") from None


def _text(variant: str, text: str | None) -> str:
    return (text if text is not None else spec(variant).label).upper()


def chip_size(variant: str, text: str | None = None) -> QSize:
    """The chip's box, border included; the pixmap adds a focus ring's room round it."""
    s = spec(variant)
    width = QFontMetricsF(theme.font("type.chip")).horizontalAdvance(_text(variant, text))
    if s.lead == "dot":
        width += theme.CHIP_DOT + theme.CHIP_GAP
    elif s.lead:
        width += theme.CHIP_GLYPH + theme.CHIP_GAP
    return QSize(round(width + 0.5) + 2 * (theme.CHIP_PAD + 1), theme.CHIP_HEIGHT)


def paint_chip(painter: QPainter, rect: QRectF, variant: str, text: str | None = None,
               state: str = "default") -> None:
    """Draw a chip into `rect` (its box, without the ring's room)."""
    s = spec(variant)
    if state not in STATES:
        raise ValueError(f"no chip state {state!r}; there are {', '.join(STATES)}")
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)
    if state == "disabled":
        painter.setOpacity(theme.DISABLED_OPACITY)
    radius = rect.height() / 2
    fill = "surface.raisedHi" if state == "hover" else s.fill
    if fill:
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        painter.fillPath(path, token(fill))
    edge = "border.focus" if state == "selected" else s.edge
    if edge:
        pen = QPen(token(edge), 1)
        if s.dashed and state != "selected":
            pen.setDashPattern(list(theme.CHIP_DASH))
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius - 0.5, radius - 0.5)
    if state == "selected":
        theme.paint_focus_ring(painter, rect, radius)

    colour = token(s.text)
    x = rect.left() + 1 + theme.CHIP_PAD
    middle = rect.center().y()
    if s.lead == "dot":
        painter.setPen(Qt.NoPen)
        painter.setBrush(colour)
        dot = theme.CHIP_DOT
        painter.drawEllipse(QRectF(x, middle - dot / 2, dot, dot))
        x += dot + theme.CHIP_GAP
    elif s.lead:
        glyph = icons.pixmap(s.lead, colour, theme.CHIP_GLYPH,
                             painter.device().devicePixelRatioF())
        painter.drawPixmap(QPointF(x, middle - theme.CHIP_GLYPH / 2), glyph)
        x += theme.CHIP_GLYPH + theme.CHIP_GAP
    painter.setFont(theme.font("type.chip"))
    painter.setPen(colour)
    painter.drawText(QRectF(x, rect.top(), rect.right() - x, rect.height()),
                     Qt.AlignLeft | Qt.AlignVCenter, _text(variant, text))
    painter.restore()


_pixmaps: dict[tuple, QPixmap] = {}
_CACHE_LIMIT = 512          # distinct chips; a table repeats a handful


def chip_pixmap(variant: str, text: str | None = None, state: str = "default",
                dpr: float = 1.0) -> QPixmap:
    """A chip as a pixmap, made once per (variant, text, state, scale).

    It is `FOCUS_RING` px bigger than the chip on every side, the room its
    selected ring needs: draw it at the chip's position less that margin.
    """
    key = (variant, _text(variant, text), state, round(dpr, 3))
    cached = _pixmaps.get(key)
    if cached is not None:
        return cached
    size = chip_size(variant, text)
    ring = theme.FOCUS_RING
    logical = QSize(size.width() + 2 * ring, size.height() + 2 * ring)
    pix = QPixmap(round(logical.width() * dpr), round(logical.height() * dpr))
    pix.setDevicePixelRatio(dpr)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    paint_chip(painter, QRectF(ring, ring, size.width(), size.height()), variant, text, state)
    painter.end()
    if len(_pixmaps) >= _CACHE_LIMIT:
        _pixmaps.clear()
    _pixmaps[key] = pix
    return pix


class Chip(Interactive, Caster, QWidget):
    """A chip on a page. The text defaults to the variant's own and is
    independent of it. `clickable=True` gives it the hover state, keyboard
    focus and a `clicked` signal; `set_selected` draws it chosen."""

    clicked = Signal()

    def __init__(self, variant: str, text: str | None = None, parent: QWidget | None = None,
                 *, clickable: bool = False):
        spec(variant)
        QWidget.__init__(self, parent)
        self._variant = variant
        self._text = text
        self._selected = False
        self._clickable = clickable
        self.setFocusPolicy(Qt.StrongFocus if clickable else Qt.NoFocus)
        self._resize()

    # -- what it says

    @property
    def variant(self) -> str:
        return self._variant

    def set_variant(self, variant: str) -> None:
        spec(variant)
        self._variant = variant
        self._resize()

    def text(self) -> str:
        return _text(self._variant, self._text)

    def set_text(self, text: str | None) -> None:
        self._text = text
        self._resize()

    def selected(self) -> bool:
        return self._selected

    def set_selected(self, on: bool) -> None:
        self._selected = bool(on)
        self.state_changed()

    def _resize(self) -> None:
        self.setFixedSize(chip_size(self._variant, self._text))
        self.setAccessibleName(self.text().capitalize())
        self.state_changed()

    # -- state

    def chip_state(self) -> str:
        if not self.isEnabled():
            return "disabled"
        if self._selected:
            return "selected"
        if self._kit_force == "hover" or (self._clickable and self.visual_state() == "hover"):
            return "hover"
        return "default"

    def outside_margins(self) -> QMargins:
        ring = theme.FOCUS_RING
        return QMargins(ring, ring, ring, ring)

    def paint_outside(self, painter: QPainter) -> None:
        if self.chip_state() == "selected" or self.focus_visible():
            rect = QRectF(self.rect())
            theme.paint_focus_ring(painter, rect, rect.height() / 2)

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        follow(self)
        painter = QPainter(self)
        ring = theme.FOCUS_RING
        pix = chip_pixmap(self._variant, self._text, self.chip_state(), self.devicePixelRatioF())
        painter.drawPixmap(QPointF(-ring, -ring), pix)

    # -- clicks

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt's name
        if self._clickable and event.button() == Qt.LeftButton and self.rect().contains(
                event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:     # noqa: N802 - Qt's name
        if self._clickable and event.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            self.clicked.emit()
            event.accept()
            return
        super().keyPressEvent(event)
