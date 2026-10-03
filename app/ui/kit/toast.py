"""Toasts: Toast, and the ToastHost that stacks them in a page's corner.

A toast says that something finished, or went wrong, without stopping what
you are doing: an icon, a line or two, an optional action link ("Undo",
"Open log") and a close button, on surface.overlay at elev.3, its edge in
the variant's hue. Variants are ok, info, warn and danger.

- It fades in rising 8 px over 180 ms and fades out over 120 (anim.toastIn);
  with Windows' animations off it appears and goes at once.
- It goes by itself after 6 s — except danger, which stays until it is
  closed: a failure is not something to miss by looking away. The pointer
  resting on a toast holds it.
- ToastHost stacks a page's toasts bottom-right over its content, newest at
  the bottom, at most four; a fifth makes the oldest that may go leave first.
- In the app only. A job finishing while the window is hidden is the tray's
  to tell, as a Windows notification (step 15).

The host sits over the page and passes every click through to it; the
toasts are the page's own children, raised over the host, so only the toasts
themselves take the pointer. The host paints their shadows, which reach
100 px out: a host the size of its stack, shadows and all, would swallow the
clicks meant for whatever those shadows fall on.
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QEvent, QRectF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QPainter, QPainterPath, QPen, QRegion
from PySide6.QtWidgets import QGraphicsOpacityEffect, QHBoxLayout, QSizePolicy, QWidget

from ... import animations, theme
from . import base
from .base import Glyph, alive, label
from .buttons import IconButton, LinkButton

# variant → (icon, icon colour, edge)
# variant → (its glyph, the glyph's and the left edge's hue)
TOAST_VARIANTS: dict[str, tuple[str, str]] = {
    "ok": ("check", "ok"),
    "info": ("info", "info"),
    "warn": ("warn", "warn"),
    "danger": ("warn", "danger"),
}

_DEFAULT = object()


class Toast(QWidget):
    """One toast. Made by a ToastHost (`host.show_toast(...)`) rather than on
    its own: the host places it, raises it and paints its shadow.

    `timeout` is in ms; by default 6 000, and None for danger, which stays
    until closed. `closed` fires once it has gone, by itself or by a click.
    """

    closed = Signal()

    def __init__(self, text: str, variant: str = "info", parent: QWidget | None = None, *,
                 action: str | None = None, on_action: Callable[[], None] | None = None,
                 timeout=_DEFAULT):
        super().__init__(parent)
        if variant not in TOAST_VARIANTS:
            raise KeyError(f"no toast variant {variant!r}; there are {', '.join(TOAST_VARIANTS)}")
        self._variant = variant
        if timeout is _DEFAULT:
            timeout = None if variant == "danger" else theme.TOAST_TIMEOUT
        self._timeout: int | None = timeout
        self._on_action = on_action
        self._t = 0.0                   # 0 not there, 1 fully shown
        self._leaving = False
        self._gone = False
        self.setFixedWidth(theme.TOAST_WIDTH)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        base.declare(self)              # its link's and close button's rings go on it
        self.setAccessibleName(text)
        self.setAccessibleDescription(variant)

        row = QHBoxLayout(self)
        vertical, horizontal = theme.TOAST_PAD
        row.setContentsMargins(theme.TOAST_EDGE + horizontal, vertical, horizontal - theme.SP_4, vertical)
        row.setSpacing(theme.TOAST_GAP)
        icon, colour = TOAST_VARIANTS[variant]
        self._glyph = Glyph(icon, colour, theme.TOAST_ICON)
        row.addWidget(self._glyph, 0, Qt.AlignTop)
        self._text = label(text, "type.bodySm", "body")
        self._text.setWordWrap(True)
        self._text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        row.addWidget(self._text, 1, Qt.AlignVCenter)
        self._link: LinkButton | None = None
        if action:
            self._link = LinkButton(action)
            self._link.clicked.connect(self._act)
            row.addWidget(self._link, 0, Qt.AlignVCenter)
        self._close = IconButton("close", "Dismiss", size="sm")
        self._close.clicked.connect(self.dismiss)
        row.addWidget(self._close, 0, Qt.AlignTop)
        self.setFixedHeight(self._height())

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)
        self._left_ms: int | None = None
        self._animation = QVariantAnimation(self)
        self._animation.setEasingCurve(animations.ease())
        self._animation.valueChanged.connect(self._step)
        self._animation.finished.connect(self._arrived)
        self._effect: QGraphicsOpacityEffect | None = None

    def _height(self) -> int:
        layout = self.layout()
        return max(layout.totalHeightForWidth(theme.TOAST_WIDTH), layout.totalMinimumSize().height())

    # -- what it says

    def variant(self) -> str:
        return self._variant

    def text(self) -> str:
        return self._text.text()

    def action_text(self) -> str:
        return self._link.text() if self._link is not None else ""

    def timeout(self) -> int | None:
        return self._timeout

    def appearance(self) -> float:
        """0 not there, 1 fully shown; in between while it arrives or leaves."""
        return self._t

    def leaving(self) -> bool:
        return self._leaving

    def gone(self) -> bool:
        return self._gone

    def timer_running(self) -> bool:
        return self._timer.isActive()

    # -- arriving and leaving

    def arrive(self) -> None:
        """Show it, and start its clock. The host calls this."""
        self.show()
        self.raise_()
        if animations.ENABLED:
            self._fade(0.0, 1.0, animations.TOAST_IN)
        else:
            self._set(1.0)
            self._arrived()

    def dismiss(self) -> None:
        """Fade it out and let it go. Safe to call twice."""
        if self._leaving or self._gone:
            return
        self._leaving = True
        self._timer.stop()
        if animations.ENABLED and self.isVisible():
            self._fade(self._t, 0.0, animations.TOAST_OUT)
        else:
            self._set(0.0)
            self._arrived()

    def _fade(self, start: float, end: float, ms: int) -> None:
        if self._effect is None:
            self._effect = QGraphicsOpacityEffect(self)
            self.setGraphicsEffect(self._effect)
        self._animation.stop()
        self._animation.setDuration(ms)
        self._animation.setStartValue(start)
        self._animation.setEndValue(end)
        self._set(start)
        self._animation.start()

    def _step(self, value) -> None:
        self._set(float(value))

    def _set(self, t: float) -> None:
        self._t = t
        if self._effect is not None:
            self._effect.setOpacity(t)
        host = self._host()
        if host is not None:
            host.place()

    def _arrived(self) -> None:
        # The effect routes every repaint through an offscreen pixmap; it is
        # only there while the toast moves.
        if self._effect is not None:
            self.setGraphicsEffect(None)
            self._effect = None
        if self._leaving:
            self._gone = True
            self.hide()
            self.closed.emit()
            self.deleteLater()
        elif self._timeout is not None and not self.underMouse():
            self._timer.start(self._timeout)

    def _host(self) -> ToastHost | None:
        host = getattr(self, "_kit_host", None)
        return host if alive(host) else None

    # -- the pointer holds it

    def enterEvent(self, event) -> None:        # noqa: N802 - Qt's name
        super().enterEvent(event)
        if self._timer.isActive():
            self._left_ms = self._timer.remainingTime()
            self._timer.stop()

    def leaveEvent(self, event) -> None:        # noqa: N802 - Qt's name
        super().leaveEvent(event)
        if self._timeout is not None and not self._leaving and self._t >= 1.0:
            left = self._left_ms if self._left_ms is not None else self._timeout
            # at least a third of its time back, to read it once the pointer moves off
            self._timer.start(max(left, self._timeout // 3))
            self._left_ms = None

    def _act(self) -> None:
        if self._on_action is not None:
            self._on_action()
        self.dismiss()

    # -- painting

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        box = QRectF(self.rect())
        radius = theme.TOAST_RADIUS
        path = QPainterPath()
        path.addRoundedRect(box, radius, radius)
        painter.fillPath(path, theme.color("surface.overlay"))
        theme.paint_sheen(painter, box, radius, "elev.3")
        painter.setPen(QPen(theme.color("border.control"), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5), radius - 0.5, radius - 0.5)
        # ds-12's border-left: the hue down the left side, inside the rounding
        painter.save()
        painter.setClipPath(path)
        painter.fillRect(QRectF(0, 0, theme.TOAST_EDGE, box.height()),
                         theme.color(TOAST_VARIANTS[self._variant][1]))
        painter.restore()
        base.paint(painter, self, event.rect())


class ToastHost(QWidget):
    """Where a page's toasts appear. Give it the widget whose bottom-right
    corner they stack in — the window's content, not a page that scrolls:

        toasts = ToastHost(content)
        toasts.show_toast("37 wallpapers created.", "ok", action="Open folder",
                          on_action=open_folder)

    It covers that widget, lets every click through, and paints the toasts'
    shadows; the toasts are the widget's children, over it.
    """

    def __init__(self, page: QWidget):
        super().__init__(page)
        self._page = page
        self._toasts: list[Toast] = []
        self._drawn = QRegion()         # where the shadows were last painted
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.NoFocus)
        page.installEventFilter(self)
        self.setGeometry(page.rect())
        self.show()

    # -- toasts

    def show_toast(self, text: str, variant: str = "info", *, action: str | None = None,
                   on_action: Callable[[], None] | None = None, timeout=_DEFAULT) -> Toast:
        """Make a toast and show it at the bottom of the stack."""
        return self.add(Toast(text, variant, action=action, on_action=on_action,
                              timeout=timeout))

    def add(self, toast: Toast) -> Toast:
        toast.setParent(self._page)
        toast._kit_host = self
        toast.closed.connect(lambda t=toast: self._forget(t))
        self._toasts.append(toast)
        waiting = [t for t in self._toasts if not t.leaving()]
        while len(waiting) > theme.TOAST_LIMIT:
            oldest = next((t for t in waiting if t.timeout() is not None), None)
            if oldest is None:
                break           # all danger: they stay, as danger does
            oldest.dismiss()
            waiting.remove(oldest)
        self.raise_()
        self.place()
        toast.arrive()
        return toast

    def toasts(self) -> list[Toast]:
        """The toasts on the page, oldest first, leaving ones included."""
        return [t for t in self._toasts if alive(t) and not t.gone()]

    def clear(self) -> None:
        for toast in list(self._toasts):
            if alive(toast):
                toast.dismiss()

    def _forget(self, toast: Toast) -> None:
        self._toasts = [t for t in self._toasts if t is not toast and alive(t)]
        self.place()

    # -- where they go

    def place(self) -> None:
        """Stack the toasts up from the page's bottom-right corner, newest
        lowest; each one arriving or leaving sits lower by what it has yet to rise."""
        self.setGeometry(self._page.rect())
        x = self.width() - theme.TOAST_MARGIN - theme.TOAST_WIDTH
        bottom = self.height() - theme.TOAST_MARGIN
        left, top, right, below = theme.shadow_reach("elev.3")
        region = QRegion()
        for toast in reversed(self.toasts()):
            rise = round((1.0 - toast.appearance()) * theme.TOAST_RISE)
            toast.move(x, bottom - toast.height() + rise)
            toast.raise_()
            region += toast.geometry().adjusted(-left, -top, right, below)
            bottom -= toast.height() + theme.TOAST_STACK_GAP
        # Only round the stack, where the shadows were and are: the host is
        # transparent, so what it repaints the page under it repaints too, on
        # every frame of a toast's arrival.
        self.update(region + self._drawn)
        self._drawn = region

    def eventFilter(self, watched, event) -> bool:      # noqa: N802 - Qt's name
        if watched is self._page and event.type() in (QEvent.Resize, QEvent.Show):
            self.place()
        elif watched is self._page and event.type() == QEvent.ChildAdded:
            # a page that grows new children after a toast would lay them over it
            QTimer.singleShot(0, self._restack)
        return False

    def _restack(self) -> None:
        if alive(self) and self.toasts():
            self.raise_()
            for toast in self.toasts():
                toast.raise_()

    # -- the shadows

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        for toast in self.toasts():
            if not toast.isVisible() or toast.appearance() <= 0:
                continue
            painter.setOpacity(toast.appearance())
            box = QRectF(toast.geometry().translated(-self.pos()))
            theme.paint_shadow(painter, box, "elev.3", theme.TOAST_RADIUS)
