"""Motion, kept to the places where it says something.

Qt stylesheets have no transitions, so everything here is a real animation on a
property. The rule applied throughout: motion is only worth it when it makes a
*change of state* legible — a bar advancing, a page arriving, a number that just
went up. Nothing loops, nothing decorates, and nothing delays a click.

Durations are short on purpose. Anything past ~200ms on a click stops reading as
responsiveness and starts reading as lag.
"""
from __future__ import annotations

from contextlib import contextmanager

from PySide6.QtCore import (
    Property, QAbstractAnimation, QEasingCurve, QPropertyAnimation,
    QVariantAnimation, Qt,
)
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication, QGraphicsOpacityEffect, QProgressBar, QTabWidget, QWidget,
)

from . import theme

FAST = 120        # hover-scale feedback, things the eye should barely catch
NORMAL = 180      # a page arriving, a bar moving
SLOW = 900        # a highlight fading back out, which must not feel urgent

# Set False to render everything without motion (kept for slow remote sessions).
ENABLED = True


# ---- Progress that slides instead of jumping ------------------------------

class SmoothProgressBar(QProgressBar):
    """A progress bar that eases to new values.

    Qt animates a property through its WRITE method, so animating `value`
    directly would land back in `setValue` and recurse. It animates a private
    float property instead, which writes the real one.
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._animation = QPropertyAnimation(self, b"easedValue", self)
        self._animation.setDuration(NORMAL)
        self._animation.setEasingCurve(QEasingCurve.OutCubic)

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

def fade_in(widget: QWidget, duration: int = NORMAL) -> None:
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
    animation.setEasingCurve(QEasingCurve.OutCubic)
    animation.finished.connect(lambda: widget.setGraphicsEffect(None))
    animation.start(QAbstractAnimation.DeleteWhenStopped)


class FadingTabWidget(QTabWidget):
    """Tabs whose pages fade in, so a switch reads as a move, not a flicker."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.currentChanged.connect(self._fade_current)

    def _fade_current(self, index: int) -> None:
        page = self.widget(index)
        if page is not None:
            fade_in(page, FAST)


# ---- Something just changed ----------------------------------------------

def flash(widget: QWidget, kind: str = "accent", duration: int = SLOW,
          rest: str | None = None) -> None:
    """Tint a label in `kind`, then let it fade back to its resting colour.

    Used where a number changes on its own — the eye needs telling that the
    thing it was already looking at is not the thing it is looking at now.
    """
    if not ENABLED or not widget:
        return
    start = QColor(theme.status_color(kind) if kind in ("ok", "bad", "done")
                   else theme.C.get(kind, theme.C["accent"]))
    end = QColor(rest or theme.C["text"])

    animation = QVariantAnimation(widget)
    animation.setDuration(duration)
    animation.setStartValue(start)
    animation.setEndValue(end)
    animation.setEasingCurve(QEasingCurve.OutCubic)
    animation.valueChanged.connect(
        lambda colour: widget.setStyleSheet(
            f"color: {colour.name()}; background: transparent;"))
    animation.start(QAbstractAnimation.DeleteWhenStopped)


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
