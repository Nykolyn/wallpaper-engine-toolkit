"""Progress: ProgressBar and ProgressRing, both painted.

- ProgressBar is a track in the well with the inset shade, 3, 4, 5, 6 or 8 px
  tall. Determinate, its fill eases to each new width over motion.slow (a jump
  backwards, a hidden bar and motion off land at once); indeterminate, a soft
  sweep crosses it from the shared "indeterminate" clock; `error` fills it in
  danger and `success` in ok. An optional caption row under it says
  `412 / 1 000` on the left and `41%` on the right, in the bar's colour.
- ProgressRing is 58, 52 or 34 px, a 4 px stroke with round caps on a well
  track: the percentage in its centre while determinate, a quarter arc turning
  on the shared "spin" clock while indeterminate, and a full ring in ok with a
  tick when done.

Both write their numbers through `format`, so a ring never reads 100% while
work remains.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QVariantAnimation
from PySide6.QtGui import QBrush, QFontMetricsF, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ... import animations, theme
from . import format as fmt
from . import icons

BAR_STATES = ("determinate", "indeterminate", "error", "success")
# The fill's colour for a determinate bar: accent for the work that counts,
# muted for a monitor that follows its own order, warn for a paused one.
BAR_TONES = {"accent": "accent", "muted": "text.lo", "warn": "warn",
             "danger": "danger", "ok": "ok"}


def _share(done: float, total: float) -> float:
    if total <= 0:
        return 0.0
    return max(0.0, min(1.0, done / total))


class ProgressBar(QWidget):
    """A progress bar. `set_value(done, total)` or `set_fraction(f)`;
    `set_state("determinate" | "indeterminate" | "error" | "success")`;
    `caption=True` adds the row of numbers under it, which `set_caption`
    can override (`"stopped at 620 / 1 000"`)."""

    def __init__(self, parent: QWidget | None = None, *, height: int = 8,
                 caption: bool = False, tone: str = "accent"):
        super().__init__(parent)
        if height not in theme.PROGRESS_HEIGHTS:
            raise ValueError(f"a ProgressBar is {', '.join(map(str, theme.PROGRESS_HEIGHTS))} "
                             f"px tall, not {height}")
        self._bar = height
        self._caption = caption
        self._state = "determinate"
        self._tone = "accent"
        self.set_tone(tone)
        self._done, self._total = 0, 0
        self._target = 0.0
        self._shown = 0.0
        self._words: tuple[str, str] | None = None
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(animations.SLOW)
        self._animation.setEasingCurve(animations.ease())
        self._animation.valueChanged.connect(self._step)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(self._full_height())
        self.setAccessibleName("Progress")

    def _full_height(self) -> int:
        if not self._caption:
            return self._bar
        return self._bar + theme.PROGRESS_CAPTION_GAP + round(theme.line_height("type.monoSm"))

    def sizeHint(self) -> QSize:                # noqa: N802 - Qt's name
        return QSize(160, self._full_height())

    # -- what it shows

    def set_value(self, done: int, total: int) -> None:
        self._done, self._total = done, total
        self._go(_share(done, total))

    def set_fraction(self, fraction: float) -> None:
        self._done, self._total = 0, 0
        self._go(max(0.0, min(1.0, float(fraction))))

    def fraction(self) -> float:
        """Where the bar is going; `shown_fraction()` is where it is drawn now."""
        return self._target

    def shown_fraction(self) -> float:
        return self._shown

    def state(self) -> str:
        return self._state

    def set_state(self, state: str) -> None:
        if state not in BAR_STATES:
            raise ValueError(f"no ProgressBar state {state!r}; there are {', '.join(BAR_STATES)}")
        self._state = state
        driver = animations.loop("indeterminate")
        if state == "indeterminate":
            driver.subscribe(self)
        else:
            driver.unsubscribe(self)
        if state == "success":
            self._go(1.0)
        self._describe()
        self.update()

    def tone(self) -> str:
        return self._tone

    def set_tone(self, tone: str) -> None:
        if tone not in BAR_TONES:
            raise KeyError(f"no ProgressBar tone {tone!r}; there are {', '.join(BAR_TONES)}")
        self._tone = tone
        self.update()

    def colour_token(self) -> str:
        if self._state == "error":
            return "danger"
        if self._state == "success":
            return "ok"
        return BAR_TONES[self._tone]

    def set_caption(self, left: str | None, right: str | None = None) -> None:
        """The caption row's words; `None` goes back to the numbers."""
        self._words = None if left is None else (left, right or "")
        self._describe()
        self.update()

    def caption(self) -> tuple[str, str]:
        if self._words is not None:
            return self._words
        if self._state == "indeterminate" or not self._total:
            return ("", "")
        numbers = fmt.ratio(self._done, self._total)
        if self._state == "error":
            numbers = f"stopped at {numbers}"
        return (numbers, fmt.percent(self._done, self._total))

    # -- motion

    def _go(self, target: float) -> None:
        self._target = target
        self._animation.stop()
        # A jump backwards is a run starting over, and a hidden bar has nothing
        # to show: both land at once, as they do with motion off.
        if (not animations.ENABLED or not self.isVisible() or target < self._shown
                or abs(target - self._shown) < 1e-4):
            self._shown = target
            self.update()
        else:
            self._animation.setStartValue(self._shown)
            self._animation.setEndValue(target)
            self._animation.start()
        self._describe()

    def _step(self, value) -> None:
        self._shown = float(value)
        self.update()

    def _describe(self) -> None:
        left, right = self.caption()
        self.setAccessibleDescription(" ".join(w for w in (left, right) if w)
                                      or f"{round(self._target * 100)}%")

    # -- painting

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if not self.isEnabled():
            painter.setOpacity(theme.DISABLED_OPACITY)
        track = QRectF(0, 0, self.width(), self._bar)
        radius = self._bar / 2
        path = QPainterPath()
        path.addRoundedRect(track, radius, radius)
        painter.fillPath(path, theme.color("surface.well"))
        theme.paint_shadow(painter, track, "elev.inset", radius)
        painter.save()
        painter.setClipPath(path)
        colour = theme.color(self.colour_token())
        if self._state == "indeterminate":
            sweep = track.width() * theme.PROGRESS_SWEEP
            travel = animations.loop("indeterminate").value()
            x = track.left() + sweep * (-1.0 + theme.PROGRESS_SWEEP_TRAVEL * travel)
            grad = QLinearGradient(QPointF(x, 0), QPointF(x + sweep, 0))
            clear = theme.color("accent", 0.0)
            grad.setColorAt(0, clear)
            grad.setColorAt(0.5, theme.color("accent"))
            grad.setColorAt(1, clear)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(grad))
            painter.drawRoundedRect(QRectF(x, track.top(), sweep, track.height()), radius, radius)
        elif self._shown > 0:
            fill = QRectF(track.left(), track.top(), track.width() * self._shown, track.height())
            painter.setPen(Qt.NoPen)
            painter.setBrush(colour)
            painter.drawRoundedRect(fill, radius, radius)
        painter.restore()

        if self._caption:
            left, right = self.caption()
            top = self._bar + theme.PROGRESS_CAPTION_GAP
            line = QRectF(0, top, self.width(), self.height() - top)
            painter.setFont(theme.font("type.monoSm"))
            if right:
                painter.setPen(colour)
                painter.drawText(line, Qt.AlignRight | Qt.AlignVCenter, right)
                width = QFontMetricsF(painter.font()).horizontalAdvance(right) + theme.SP_8
            else:
                width = 0
            painter.setPen(theme.color("text.lo"))
            text = QFontMetricsF(painter.font()).elidedText(left, Qt.ElideRight,
                                                            max(0.0, line.width() - width))
            painter.drawText(line, Qt.AlignLeft | Qt.AlignVCenter, text)


class ProgressRing(QWidget):
    """A ring, 58, 52 or 34 px. `set_value(done, total)` shows the share and
    its percentage; `set_indeterminate(True)` turns a quarter arc; `set_done()`
    closes it in ok with a tick."""

    def __init__(self, size: int = 58, parent: QWidget | None = None):
        super().__init__(parent)
        if size not in theme.RING:
            raise ValueError(f"a ProgressRing is {', '.join(map(str, theme.RING))} px, not {size}")
        self._size = size
        self._fraction = 0.0
        self._done, self._total = 0, 0
        self._state = "determinate"
        self._label: str | None = None
        self.setFixedSize(size, size)
        self.setAccessibleName("Progress")

    def ring_size(self) -> int:
        return self._size

    def state(self) -> str:
        return self._state

    def set_value(self, done: int, total: int) -> None:
        self._done, self._total = done, total
        self._fraction = _share(done, total)
        self._set_state("determinate")

    def set_fraction(self, fraction: float) -> None:
        self._done, self._total = 0, 0
        self._fraction = max(0.0, min(1.0, float(fraction)))
        self._set_state("determinate")

    def fraction(self) -> float:
        return self._fraction

    def set_indeterminate(self, on: bool = True) -> None:
        self._set_state("indeterminate" if on else "determinate")

    def set_done(self, on: bool = True) -> None:
        if on:
            self._fraction = 1.0
        self._set_state("done" if on else "determinate")

    def set_label(self, text: str | None) -> None:
        """The words in the middle; None goes back to the percentage."""
        self._label = text
        self.update()

    def label(self) -> str:
        if self._state != "determinate":
            return ""
        if self._label is not None:
            return self._label
        if self._total:
            return fmt.percent(self._done, self._total)
        return f"{round(self._fraction * 100)}%"

    def _set_state(self, state: str) -> None:
        self._state = state
        driver = animations.loop("spin")
        if state == "indeterminate":
            driver.subscribe(self)
        else:
            driver.unsubscribe(self)
        self.setAccessibleDescription(self.label() or state)
        self.update()

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if not self.isEnabled():
            painter.setOpacity(theme.DISABLED_OPACITY)
        radius, type_token = theme.RING[self._size]
        centre = QPointF(self._size / 2, self._size / 2)
        box = QRectF(centre.x() - radius, centre.y() - radius, 2 * radius, 2 * radius)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(theme.color("surface.well"), theme.RING_STROKE))
        painter.drawEllipse(box)

        # Qt's arcs count 1/16 degrees anticlockwise from three o'clock; the
        # design's start at twelve and run clockwise.
        if self._state == "indeterminate":
            start = 90 - animations.loop("spin").value()
            span = 360 * theme.RING_SPIN_ARC
            colour = "accent"
        else:
            start, span = 90, 360 * self._fraction
            colour = "ok" if self._state == "done" else "accent"
        if span > 0:
            pen = QPen(theme.color(colour), theme.RING_STROKE, Qt.SolidLine, Qt.RoundCap)
            painter.setPen(pen)
            if span >= 360:
                painter.drawEllipse(box)
            else:
                painter.drawArc(box, round(start * 16), -round(span * 16))

        if self._state == "done":
            tick = dict(zip(sorted(theme.RING, reverse=True), theme.RING_TICK))[self._size]
            glyph = icons.pixmap("check", "ok", tick, self.devicePixelRatioF())
            painter.drawPixmap(QPointF(centre.x() - tick / 2, centre.y() - tick / 2), glyph)
            return
        text = self.label()
        if text:
            painter.setFont(theme.font(type_token))
            painter.setPen(theme.color("text.hi"))
            painter.drawText(QRectF(self.rect()), Qt.AlignCenter, text)
