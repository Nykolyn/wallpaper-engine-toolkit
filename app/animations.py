"""Motion: three durations, one curve, and four loops.

Qt stylesheets have no transitions, so everything here is a real animation on a
property. Motion has one job: to say that something is happening, or that
something just changed. The frame, table rows, dialogs and numbers never move.

- `FAST` (90 ms): hover tint, icon colour.
- `BASE` (140 ms): button fill, toggle knob, the cross-fade between pages.
- `SLOW` (220 ms): progress width, a panel expanding.
- `ease()`: the design's cubic-bezier(.2,.7,.3,1), used by everything except
  the spinners.

The loops — a spinner, a pulsing dot, a skeleton's shimmer, the indeterminate
sweep — run from one shared clock per kind (`loop("spin")`), only while
something that shows them is on screen.

With Windows' own animations off (`reduced_motion()`), `ENABLED` is False: every
transition becomes an instant change and the loops stand still.
"""
from __future__ import annotations

import ctypes
import sys
import weakref
from contextlib import contextmanager

from PySide6.QtCore import (
    Property, QAbstractAnimation, QElapsedTimer, QEasingCurve, QEvent, QObject,
    QPointF, QPropertyAnimation, QTimer, QVariantAnimation, Qt,
)
from PySide6.QtWidgets import (
    QApplication, QGraphicsOpacityEffect, QProgressBar, QTabWidget, QWidget,
)

from . import theme

FAST = 90         # motion.fast
BASE = 140        # motion.base
SLOW = 220        # motion.slow

# A count that changed by itself holds its tint for four slow beats. It is the
# one motion the design does not name: the numbers themselves step without
# rolling, and this is how the eye learns that the number it was looking at is
# not the number it is looking at now. At SLOW alone it is over before a
# glance lands; at four it reads as "just changed" without nagging.
FLASH = 4 * SLOW

# Set from Windows' "Show animations in Windows" by theme.apply().
ENABLED = True


def ease() -> QEasingCurve:
    """ease.standard: cubic-bezier(.2,.7,.3,1) — quick off the mark, soft landing."""
    return _bezier((0.2, 0.7), (0.3, 1.0))


def _bezier(c1: tuple[float, float], c2: tuple[float, float]) -> QEasingCurve:
    curve = QEasingCurve(QEasingCurve.BezierSpline)
    curve.addCubicBezierSegment(QPointF(*c1), QPointF(*c2), QPointF(1.0, 1.0))
    return curve


SPI_GETCLIENTAREAANIMATION = 0x1042


def reduced_motion() -> bool:
    """True when Windows' animations are switched off.

    That is Settings → Accessibility → Visual effects → Animation effects, the
    switch Windows itself honours for its own windows. Anywhere that cannot be
    asked, motion stays on.
    """
    if sys.platform != "win32":
        return False
    enabled = ctypes.c_int(1)
    try:
        ok = ctypes.windll.user32.SystemParametersInfoW(
            SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(enabled), 0)
    except (AttributeError, OSError):
        return False
    return bool(ok) and not enabled.value


# ---- Progress that slides instead of jumping ------------------------------

class SmoothProgressBar(QProgressBar):
    """A progress bar that eases to new values over motion.slow.

    Qt animates a property through its WRITE method, so animating `value`
    directly would land back in `setValue` and recurse. It animates a private
    float property instead, which writes the real one.
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._animation = QPropertyAnimation(self, b"easedValue", self)
        self._animation.setDuration(SLOW)
        self._animation.setEasingCurve(ease())

    def _get_eased(self) -> float:
        return float(QProgressBar.value(self))

    def _set_eased(self, value: float) -> None:
        QProgressBar.setValue(self, int(round(value)))

    easedValue = Property(float, _get_eased, _set_eased)

    def setValue(self, value: int) -> None:      # noqa: N802 - Qt's name
        current = QProgressBar.value(self)
        # A jump backwards is a run starting over, and a hidden bar has nothing
        # to show — both should land immediately rather than crawl.
        if (not ENABLED or not self.isVisible() or value <= current
                or abs(value - current) <= 1):
            self._animation.stop()
            QProgressBar.setValue(self, value)
            return
        self._animation.stop()
        self._animation.setStartValue(float(current))
        self._animation.setEndValue(float(value))
        self._animation.start()


# ---- Arriving ------------------------------------------------------------

def fade_in(widget: QWidget, duration: int = BASE) -> None:
    """Bring a widget up from transparent, then get out of the way.

    The opacity effect is removed when the animation ends: leaving one attached
    routes every later repaint of that widget through an offscreen pixmap, which
    is a real cost on a tab holding hundreds of cards.
    """
    if not ENABLED or not widget:
        return
    effect = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(effect)

    animation = QPropertyAnimation(effect, b"opacity", widget)
    animation.setDuration(duration)
    animation.setStartValue(0.0)
    animation.setEndValue(1.0)
    animation.setEasingCurve(ease())
    animation.finished.connect(lambda: widget.setGraphicsEffect(None))
    animation.start(QAbstractAnimation.DeleteWhenStopped)


class FadingTabWidget(QTabWidget):
    """Tabs whose pages cross-fade in, so a switch reads as a move, not a flicker."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.currentChanged.connect(self._fade_current)

    def _fade_current(self, index: int) -> None:
        page = self.widget(index)
        if page is not None:
            fade_in(page, BASE)


# ---- Something just changed ----------------------------------------------

def flash(widget: QWidget, kind: str = "accent", duration: int = FLASH,
          rest: str | None = None) -> None:
    """Tint a label in `kind`, then let it fade back to its resting colour.

    `kind` and `rest` are colour tokens ("ok", "accent"); the resting colour is
    `text.body` unless said otherwise. Used where a number changes on its own.
    """
    if not ENABLED or not widget:
        return
    start = theme.color(kind)
    end = theme.color(rest or "text.body")

    animation = QVariantAnimation(widget)
    animation.setDuration(duration)
    animation.setStartValue(start)
    animation.setEndValue(end)
    animation.setEasingCurve(ease())
    animation.valueChanged.connect(
        lambda colour: widget.setStyleSheet(
            f"color: {colour.name()}; background: transparent;"))
    animation.start(QAbstractAnimation.DeleteWhenStopped)


# ---- Loops ------------------------------------------------------------------

class LoopDriver(QObject):
    """One clock for every widget showing the same kind of loop.

    A spinner in the status line and one on a page share a phase, so they turn
    together, and a page of skeleton rows costs one timer rather than one
    animation per row. Widgets `subscribe()`; each tick asks the visible ones
    to repaint, and they read `value()` in their paintEvent. The timer runs
    only while at least one subscriber is on screen: a hidden page, a minimised
    window or an empty list of subscribers stops it.
    """

    FRAME = 16        # ms between repaints: one per 60 Hz frame

    def __init__(self, name: str, period: int, curve: QEasingCurve | None = None,
                 parent: QObject | None = None):
        super().__init__(parent)
        self.name = name
        self.period = period
        self._curve = curve
        self._subscribers: list[weakref.ref] = []
        self._clock = QElapsedTimer()
        self._clock.start()
        self._timer = QTimer(self)
        self._timer.setInterval(self.FRAME)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.timeout.connect(self._tick)

    # -- the value a subscriber paints with

    def phase(self, delay: int = 0) -> float:
        """Where the loop is, 0 ≤ phase < 1, for a subscriber started `delay` ms later.

        Still at 0 with motion off, which is each loop's resting frame.
        """
        if not ENABLED:
            return 0.0
        return ((self._clock.elapsed() - delay) % self.period) / self.period

    def value(self, delay: int = 0) -> float:
        """The loop's current value: degrees for spin, opacity for pulse and
        shimmer, the sweep's 0–1 travel for indeterminate."""
        return VALUES[self.name](self, self.phase(delay))

    def eased(self, t: float) -> float:
        return self._curve.valueForProgress(t) if self._curve is not None else t

    # -- who is watching

    def subscribe(self, widget: QWidget) -> None:
        if any(ref() is widget for ref in self._subscribers):
            return
        self._subscribers.append(weakref.ref(widget))
        widget.installEventFilter(self)
        self._update_running()

    def unsubscribe(self, widget: QWidget) -> None:
        self._subscribers = [r for r in self._subscribers
                             if r() is not None and r() is not widget]
        try:
            widget.removeEventFilter(self)
        except RuntimeError:
            pass        # already gone
        self._update_running()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:   # noqa: N802
        if event.type() in (QEvent.Show, QEvent.Hide):
            self._update_running()
        return False

    @property
    def running(self) -> bool:
        return self._timer.isActive()

    def _visible(self) -> list[QWidget]:
        alive, shown = [], []
        for ref in self._subscribers:
            widget = ref()
            if widget is None:
                continue
            alive.append(ref)
            try:
                if widget.isVisible() and not widget.window().isMinimized():
                    shown.append(widget)
            except RuntimeError:
                alive.pop()     # the C++ side is gone before the Python side
        self._subscribers = alive
        return shown

    def _update_running(self) -> None:
        # A minimised window hides its children with a spontaneous hide event,
        # which arrives here too; its widgets still say isVisible().
        wanted = ENABLED and bool(self._visible())
        if wanted and not self._timer.isActive():
            self._timer.start()
        elif not wanted and self._timer.isActive():
            self._timer.stop()

    def _tick(self) -> None:
        shown = self._visible()
        if not shown or not ENABLED:
            self._timer.stop()
            return
        for widget in shown:
            widget.update()


def _spin(driver: LoopDriver, t: float) -> float:
    return 360.0 * t                                   # linear, degrees


def _pulse(driver: LoopDriver, t: float) -> float:
    # opacity 1 → .35 → 1, eased in and out on each half
    half = t * 2 if t < 0.5 else (t - 0.5) * 2
    k = driver.eased(half)
    return 1.0 - 0.65 * k if t < 0.5 else 0.35 + 0.65 * k


def _shimmer(driver: LoopDriver, t: float) -> float:
    # opacity .4 → .85 → .4; rows stagger by passing their own delay
    half = t * 2 if t < 0.5 else (t - 0.5) * 2
    k = driver.eased(half)
    return 0.4 + 0.45 * k if t < 0.5 else 0.85 - 0.45 * k


def _indeterminate(driver: LoopDriver, t: float) -> float:
    # the sweep's travel: 0 is a whole sweep-width left of the track, 1 is
    # 265 % of it to the right, as the design's keyframes put it
    return driver.eased(t)


VALUES = {"spin": _spin, "pulse": _pulse, "shimmer": _shimmer,
          "indeterminate": _indeterminate}

# name: (period in ms, curve factory or None for linear)
LOOPS = {
    "spin": (900, None),                                          # anim.spin
    "pulse": (1600, lambda: _bezier((0.42, 0.0), (0.58, 1.0))),   # anim.pulse
    "shimmer": (1600, lambda: _bezier((0.42, 0.0), (0.58, 1.0))), # anim.shimmer
    "indeterminate": (1400, lambda: _bezier((0.4, 0.0), (0.6, 1.0))),  # anim.indeterminate
}

_drivers: dict[str, LoopDriver] = {}


def loop(name: str) -> LoopDriver:
    """The shared driver for one kind of loop. Made on first use."""
    driver = _drivers.get(name)
    if driver is None:
        if name not in LOOPS:
            raise KeyError(f"no loop {name!r}")
        period, curve = LOOPS[name]
        driver = LoopDriver(name, period, curve() if curve else None,
                            QApplication.instance())
        _drivers[name] = driver
    return driver


# ---- Work that blocks the window -----------------------------------------

@contextmanager
def busy():
    """Say the window is thinking, for the half-second jobs done in place.

    Scanning a source folder and building its cards takes about half a second —
    long enough that a frozen window looks broken, short enough that moving it
    to a thread would cost more than it returns.
    """
    QApplication.setOverrideCursor(Qt.BusyCursor)
    QApplication.processEvents()
    try:
        yield
    finally:
        QApplication.restoreOverrideCursor()
