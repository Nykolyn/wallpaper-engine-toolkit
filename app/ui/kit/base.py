"""What every kit widget is built on: the state model, and painting past its edges.

**The state model.** Every interactive kit widget has the design's five states:
default, hover (the pointer only), pressed, disabled, and a focus ring that
shows when the keyboard brought focus there — never after a click.
`Interactive` holds that logic once. Its `force_state` shows one state without
a pointer or a keyboard, for the kit preview and for snapshots; the app never
sets it.

**Painting outside.** CSS draws a box's shadow and its focus ring round the box
without either taking room in the layout. Qt clips every widget to its own
rectangle, so a kit widget that has to draw outside itself (a `Caster`) hands
that part to the *surface* behind it: the nearest GlassPanel, a scroll area's
viewport, or a widget `declare()`d one — the kit preview's page, and from the
shell step the window's backdrop. The surface draws those parts after its own
ground and before its children, which is where CSS puts them too.

A surface repaints only the strip round a widget whose outside changed, so
nothing here runs per frame.
"""
from __future__ import annotations

import weakref

import shiboken6
from PySide6.QtCore import QEvent, QMargins, QObject, QPoint, QRect, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QPainter, QRegion
from PySide6.QtWidgets import QAbstractScrollArea, QLabel, QWidget

from ... import animations, theme
from . import icons

STATES = (None, "hover", "pressed", "focus")

NBSP = "\u00a0"


def grouped(n: int) -> str:
    """`1 000`: thousands apart by a no-break space, as every count in the design."""
    return f"{n:,}".replace(",", NBSP)


_SURFACE = "kitSurface"             # the dynamic property that marks a surface

_KEYBOARD_REASONS = (Qt.TabFocusReason, Qt.BacktabFocusReason, Qt.ShortcutFocusReason)
# Focus that comes back with the window or from a closed popup keeps whatever
# reason brought it the first time.
_RETURNING_REASONS = (Qt.ActiveWindowFocusReason, Qt.PopupFocusReason)


def token(spec: str | None) -> QColor:
    """A colour as the kit's state tables write it: a token, a token at part of
    its opacity (`accent/.3`), or None for nothing at all."""
    if spec is None:
        return QColor(0, 0, 0, 0)
    name, _, alpha = spec.partition("/")
    return theme.color(name, float(alpha) if alpha else None)


def mix(a: QColor, b: QColor, t: float) -> QColor:
    """`a` turning into `b`, `t` of the way, the way CSS blends a transition:
    in premultiplied colour, so fading in from nothing does not pass through grey."""
    if t <= 0:
        return QColor(a)
    if t >= 1:
        return QColor(b)
    aa, ba = a.alphaF(), b.alphaF()
    alpha = aa + (ba - aa) * t
    if alpha <= 0:
        return QColor(0, 0, 0, 0)

    def channel(x: float, y: float) -> float:
        return min(1.0, max(0.0, (x * aa + (y * ba - x * aa) * t) / alpha))

    out = QColor()
    out.setRgbF(channel(a.redF(), b.redF()), channel(a.greenF(), b.greenF()),
                channel(a.blueF(), b.blueF()), alpha)
    return out


class Fade:
    """A colour easing to a new value over motion.base, or changing at once
    when motion is off or the widget is not on screen.

    A button's fill is the one thing that moves when its state changes; its
    edge, text and geometry change at once, as the design's CSS has it.
    """

    def __init__(self, widget: QWidget, start: QColor):
        self._widget = widget
        self._from = QColor(start)
        self._to = QColor(start)
        self._t = 1.0
        self._animation = QVariantAnimation(widget)
        self._animation.setDuration(animations.BASE)
        self._animation.setEasingCurve(animations.ease())
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.valueChanged.connect(self._step)

    def value(self) -> QColor:
        return mix(self._from, self._to, self._t)

    @property
    def running(self) -> bool:
        return self._animation.state() == QVariantAnimation.Running

    def go(self, target: QColor, animate: bool = True) -> None:
        if target == self._to:
            return
        self._from = self.value()
        self._to = QColor(target)
        self._animation.stop()
        if animate and animations.ENABLED and self._widget.isVisible():
            self._t = 0.0
            self._animation.start()
        else:
            self._t = 1.0
            self._widget.update()

    def _step(self, value) -> None:
        self._t = float(value)
        self._widget.update()


LABEL_TONES = ("hi", "body", "mid", "lo", "ok", "warn", "danger", "info", "accent")


def label(text: str = "", type_token: str = "type.body", tone: str = "body",
          parent: QWidget | None = None) -> QLabel:
    """A QLabel in a type style and a text tone. The colour comes from the
    stylesheet's `QLabel[tone=…]` rules, not a stylesheet of its own."""
    if tone not in LABEL_TONES:
        raise KeyError(f"no label tone {tone!r}; there are {', '.join(LABEL_TONES)}")
    widget = QLabel(text, parent)
    widget.setFont(theme.font(type_token))
    widget.setProperty("tone", tone)
    return widget


def set_tone(widget: QLabel, tone: str) -> None:
    if tone not in LABEL_TONES:
        raise KeyError(f"no label tone {tone!r}; there are {', '.join(LABEL_TONES)}")
    if widget.property("tone") != tone:
        widget.setProperty("tone", tone)
        repolish(widget)


class Glyph(QWidget):
    """One icon at a fixed size, drawn for the screen it is on."""

    def __init__(self, name: str, colour: str = "text.mid", size: int = 16,
                 parent: QWidget | None = None):
        super().__init__(parent)
        icons.svg(name)
        self._name, self._colour, self._size = name, colour, size
        self.setFixedSize(size, size)

    def set_icon(self, name: str, colour: str | None = None) -> None:
        icons.svg(name)
        self._name = name
        if colour is not None:
            self._colour = colour
        self.update()

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        painter = QPainter(self)
        if not self.isEnabled():
            painter.setOpacity(theme.DISABLED_OPACITY)
        painter.drawPixmap(0, 0, icons.pixmap(self._name, self._colour, self._size,
                                              self.devicePixelRatioF()))


def repolish(widget: QWidget) -> None:
    """Re-read the stylesheet after a property its rules select on has changed."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


def alive(widget) -> bool:
    return widget is not None and shiboken6.isValid(widget)


# ---- surfaces ------------------------------------------------------------------

class _Filter(QObject):
    """Makes a widget that paints nothing of its own a surface: its casters'
    outside parts are drawn when it paints, before anything else it draws."""

    def __init__(self, widget: QWidget):
        super().__init__(widget)
        self.casters: list[weakref.ref] = []

    def eventFilter(self, watched, event) -> bool:      # noqa: N802 - Qt's name
        if event.type() == QEvent.Paint and self.casters:
            painter = QPainter(watched)
            paint(painter, watched, event.rect())
            painter.end()
        return False


def declare(widget: QWidget) -> None:
    """Make `widget` a surface that paints its casters itself: its paintEvent
    calls `paint(painter, self, event.rect())` after drawing its own ground."""
    if not widget.property(_SURFACE):
        widget._kit_casters = []
        widget.setProperty(_SURFACE, True)


def install(widget: QWidget) -> None:
    """Make a widget that draws nothing of its own (a viewport, a plain
    container) a surface, without changing its class."""
    if not widget.property(_SURFACE):
        widget.installEventFilter(_Filter(widget))
        widget.setProperty(_SURFACE, True)


def _casters(surface: QWidget) -> list[weakref.ref] | None:
    """The surface's casters, held weakly: a control taken off the page is not
    kept alive by the page having drawn its shadow once."""
    own = getattr(surface, "_kit_casters", None)
    if own is not None:
        return own
    found = surface.findChild(_Filter, options=Qt.FindDirectChildrenOnly)
    return found.casters if found is not None else None


def _is_viewport(widget: QWidget) -> bool:
    area = widget.parentWidget()
    return (isinstance(area, QAbstractScrollArea)
            and shiboken6.getCppPointer(area.viewport())[0]
            == shiboken6.getCppPointer(widget)[0])


def _find_surface(caster: QWidget) -> QWidget | None:
    w = caster.parentWidget()
    while w is not None:
        if w.property(_SURFACE):
            return w
        if _is_viewport(w):
            install(w)
            return w
        if w.isWindow():
            break
        w = w.parentWidget()
    # No surface on the way up: the widget's own parent becomes one. If that
    # parent paints an opaque ground of its own, the outside parts hide under it.
    parent = caster.parentWidget()
    if parent is not None:
        install(parent)
    return parent


def _area(caster: QWidget, surface: QWidget) -> QRect:
    """Where the caster draws, itself and its outside, in the surface's terms."""
    origin = caster.mapTo(surface, QPoint(0, 0))
    return QRect(origin, caster.size()).marginsAdded(caster.outside_margins())


def attach(caster: QWidget) -> None:
    """Find the caster's surface and register with it. Called on every show,
    so a widget that moved to another parent follows."""
    surface = _find_surface(caster)
    old = getattr(caster, "_kit_surface", None)
    if old is not None and surface is not None and alive(old) \
            and shiboken6.getCppPointer(old)[0] == shiboken6.getCppPointer(surface)[0]:
        return
    if alive(old):
        mine = _casters(old)
        if mine is not None:
            mine[:] = [ref for ref in mine if ref() is not caster]
    caster._kit_surface = surface
    caster._kit_area = None
    if surface is not None:
        casters = _casters(surface)
        if casters is not None and not any(ref() is caster for ref in casters):
            casters.append(weakref.ref(caster))


def refresh(caster: QWidget) -> None:
    """Repaint what the caster draws outside itself: where it was, and where it is."""
    surface = getattr(caster, "_kit_surface", None)
    if not alive(surface) or not alive(caster):
        return
    region = QRegion()
    last = getattr(caster, "_kit_area", None)
    if last is not None:
        region += last
    if surface.isAncestorOf(caster) and caster.isVisibleTo(surface):
        now = _area(caster, surface)
        caster._kit_area = now
        region += now
    if not region.isEmpty():
        surface.update(region)


def follow(caster: QWidget) -> None:
    """Call at the top of a caster's paintEvent. A widget does not hear about
    its parent moving; its own repaint is where that shows up."""
    surface = getattr(caster, "_kit_surface", None)
    if alive(surface) and surface.isAncestorOf(caster) \
            and getattr(caster, "_kit_area", None) != _area(caster, surface):
        refresh(caster)


def paint(painter: QPainter, surface: QWidget, rect=None) -> None:
    """Draw the outside parts of every caster on this surface that meets `rect`."""
    casters = _casters(surface)
    if not casters:
        return
    clip = QRect(rect) if rect is not None else surface.rect()
    live = [ref for ref in casters if alive(ref())]
    casters[:] = live
    for ref in live:
        caster = ref()
        if not surface.isAncestorOf(caster) or not caster.isVisibleTo(surface):
            continue
        area = _area(caster, surface)
        caster._kit_area = area
        if not area.intersects(clip):
            continue
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.translate(caster.mapTo(surface, QPoint(0, 0)))
        caster.paint_outside(painter)
        painter.restore()


def elevation_margins(elev: str) -> QMargins:
    """How far an elevation's shadow reaches past its box, focus ring included."""
    ring = theme.FOCUS_RING
    left, top, right, bottom = theme.shadow_reach(elev)
    return QMargins(max(left, ring), max(top, ring), max(right, ring), max(bottom, ring))


class Caster:
    """Mixin for a kit widget that draws outside its rectangle. Put it before
    the Qt class: `class GlassPanel(Caster, QFrame)`.

    Subclasses say how far out they draw (`outside_margins`, the most they
    ever need) and draw it (`paint_outside`, in their own coordinates, with
    the painter set up by the surface), call `follow(self)` first thing in
    their paintEvent, and `refresh(self)` when what they draw outside changes.
    """

    def outside_margins(self) -> QMargins:
        return QMargins()

    def paint_outside(self, painter: QPainter) -> None:
        pass

    def showEvent(self, event) -> None:         # noqa: N802 - Qt's name
        if not self.isWindow():         # a window draws its own outside, if any
            attach(self)
        super().showEvent(event)
        refresh(self)

    def hideEvent(self, event) -> None:         # noqa: N802 - Qt's name
        super().hideEvent(event)
        refresh(self)

    def moveEvent(self, event) -> None:         # noqa: N802 - Qt's name
        super().moveEvent(event)
        refresh(self)

    def resizeEvent(self, event) -> None:       # noqa: N802 - Qt's name
        super().resizeEvent(event)
        refresh(self)


# ---- the state model ------------------------------------------------------------

class Interactive:
    """Mixin: the design's five states, read one way by every kit control.

    `visual_state()` is "disabled", "pressed", "hover" or "default";
    `focus_visible()` says whether the ring shows. `_kit_qss` is True in
    classes the stylesheet draws, so that forcing a state reaches their rules
    through the `forceState` property. Subclasses react to any change in
    `state_changed()`.
    """

    _kit_force: str | None = None
    _kit_keyboard = False
    _kit_qss = False

    # -- the debug-only override

    @property
    def force_state(self) -> str | None:
        """Show one state regardless of pointer and keyboard: None, "hover",
        "pressed" or "focus". For the kit preview and snapshots only."""
        return self._kit_force

    @force_state.setter
    def force_state(self, state: str | None) -> None:
        if state not in STATES:
            raise ValueError(f"no state {state!r}; there are {STATES[1:]} and None")
        self._kit_force = state
        if self._kit_qss:
            self.setProperty("forceState", state or "")
            repolish(self)
        self.state_changed()

    # -- reading the state

    def is_down(self) -> bool:
        """The pointer is pressing it. Buttons answer with isDown()."""
        return False

    def visual_state(self) -> str:
        if not self.isEnabled():
            return "disabled"
        forced = self._kit_force
        if forced is not None:
            return forced if forced in ("hover", "pressed") else "default"
        if self.is_down():
            return "pressed"
        if self.underMouse():
            return "hover"
        return "default"

    def focus_visible(self) -> bool:
        if not self.isEnabled():
            return False
        if self._kit_force is not None:
            return self._kit_force == "focus"
        return self.hasFocus() and self._kit_keyboard

    def state_changed(self) -> None:
        self.update()
        if isinstance(self, Caster):
            refresh(self)

    # -- what changes it

    def focusInEvent(self, event) -> None:      # noqa: N802 - Qt's name
        reason = event.reason()
        if reason in _KEYBOARD_REASONS:
            self._kit_keyboard = True
        elif reason not in _RETURNING_REASONS:
            self._kit_keyboard = False
        super().focusInEvent(event)
        self.state_changed()

    def focusOutEvent(self, event) -> None:     # noqa: N802 - Qt's name
        super().focusOutEvent(event)
        self.state_changed()

    def enterEvent(self, event) -> None:        # noqa: N802 - Qt's name
        super().enterEvent(event)
        self.state_changed()

    def leaveEvent(self, event) -> None:        # noqa: N802 - Qt's name
        super().leaveEvent(event)
        self.state_changed()

    def changeEvent(self, event) -> None:       # noqa: N802 - Qt's name
        super().changeEvent(event)
        if event.type() == QEvent.EnabledChange:
            self.state_changed()
