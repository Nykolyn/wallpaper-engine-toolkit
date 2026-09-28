"""The status line: one line at the foot of every frame saying what the
toolkit is doing now.

34 px of `chrome.statusbar` under a divider, in one of four states:

- running: a pulsing dot, the job in words, a 150 px bar, the count in mono
  (`412 / 1 000`) and a "Show" link to the page doing the work;
- idle: a still dot and a line of words ("Idle · next rotation Saturday");
- warn and error: the glyph in its hue, the words, and a link that does
  something about it ("Open log").

Short of room, the words give way — cut with an ellipsis, whole in the tool
tip — and the count never does: a count cut to `412 / 1 0…` would lie.

It has a plain API here; binding it to the JobCenter is steps 05 and 06.
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QWidget

from ... import theme
from . import base
from . import format as fmt
from .base import Elided, Glyph, LiveDot, label
from .buttons import LinkButton
from .progress import ProgressBar

STATUS_STATES = ("idle", "running", "warn", "error")
# state → (the words' tone, the lead: a dot's colour, or a glyph and its colour)
_LOOKS = {
    "idle": ("mid", ("dot", "text.lo")),
    "running": ("body", ("dot", "accent")),
    "warn": ("body", ("warn", "warn")),
    "error": ("body", ("warn", "danger")),
}


class StatusLine(QWidget):
    """The line at the foot of the window. Set a state with its words:

        status.set_running("Rotating · moving folders in", 412, 1000, on_show=open_rotator)
        status.set_idle("Idle · next rotation Saturday 26 September")
        status.set_warn("Finished with 2 problems", "Open log", open_log)
        status.set_error("The rotation stopped: access denied", "Open log", open_log)
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._state = "idle"
        self._on_link: Callable[[], None] | None = None
        self.setFixedHeight(theme.STATUS_HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAccessibleName("Status")
        base.declare(self)              # its link's focus ring goes over its ground

        row = QHBoxLayout(self)
        row.setContentsMargins(theme.STATUS_PAD, 1, theme.STATUS_PAD, 0)
        row.setSpacing(theme.STATUS_GAP)
        self._dot = LiveDot("text.lo", live=False)
        self._glyph = Glyph("warn", "warn", theme.STATUS_ICON)
        self._text = Elided("", "type.label", "mid")
        self._text.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._bar = ProgressBar(height=4)
        self._bar.setFixedWidth(theme.STATUS_BAR)
        self._count = label("", "type.monoSm", "mid")
        self._count.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._link = LinkButton("Show", font="type.label")
        self._link.clicked.connect(self._clicked)
        row.addWidget(self._dot, 0, Qt.AlignVCenter)
        row.addWidget(self._glyph, 0, Qt.AlignVCenter)
        row.addWidget(self._text, 1)
        row.addWidget(self._bar, 0, Qt.AlignVCenter)
        row.addWidget(self._count, 0, Qt.AlignVCenter)
        row.addWidget(self._link, 0, Qt.AlignVCenter)
        self.set_idle("")

    # -- the four states

    def set_running(self, text: str, done: int = 0, total: int = 0,
                    count_text: str | None = None,
                    on_show: Callable[[], None] | None = None) -> None:
        """A job at work: `done` of `total` fills the bar and makes the count
        (`412 / 1 000`) unless `count_text` says otherwise; with no total the
        bar sweeps. `on_show` is where "Show" goes."""
        self._show("running", text, "Show" if on_show else None, on_show)
        if total > 0:
            self._bar.set_state("determinate")
            self._bar.set_value(done, total)
        else:
            self._bar.set_state("indeterminate")
        self._bar.show()
        count = count_text if count_text is not None else (fmt.ratio(done, total) if total > 0 else "")
        self._count.setText(count)
        self._count.setVisible(bool(count))
        self._describe()

    def set_idle(self, text: str = "") -> None:
        self._show("idle", text, None, None)

    def set_warn(self, text: str, action_text: str | None = None,
                 on_action: Callable[[], None] | None = None) -> None:
        self._show("warn", text, action_text, on_action)

    def set_error(self, text: str, action_text: str | None = None,
                  on_action: Callable[[], None] | None = None) -> None:
        self._show("error", text, action_text, on_action)

    def _show(self, state: str, text: str, link: str | None,
              callback: Callable[[], None] | None) -> None:
        self._state = state
        tone, (lead, colour) = _LOOKS[state]
        self._text.set_text(text)
        self._text.set_tone(tone)
        if lead == "dot":
            self._dot.set_colour(colour)
            self._dot.set_live(state == "running")
            self._dot.show()
            self._glyph.hide()
        else:
            self._dot.set_live(False)
            self._dot.hide()
            self._glyph.set_icon(lead, colour)
            self._glyph.show()
        if state != "running":
            self._bar.set_state("determinate")
            self._bar.hide()
            self._count.setText("")
            self._count.hide()
        self._on_link = callback
        if link and callback is not None:
            self._link.setText(link)
            self._link.show()
        else:
            self._link.hide()
        self._describe()

    def _clicked(self) -> None:
        if self._on_link is not None:
            self._on_link()

    def _describe(self) -> None:
        words = [self._text.text(), self._count.text()]
        self.setAccessibleDescription(" · ".join(w for w in words if w))

    # -- reading it back

    def state(self) -> str:
        return self._state

    def text(self) -> str:
        return self._text.text()

    def shown_text(self) -> str:
        """The words as drawn, ellipsis and all."""
        return self._text.shown_text()

    def count_text(self) -> str:
        return self._count.text() if self._count.isVisibleTo(self) else ""

    def link_text(self) -> str:
        return self._link.text() if self._link.isVisibleTo(self) else ""

    def bar(self) -> ProgressBar:
        return self._bar

    def sizeHint(self) -> QSize:                # noqa: N802 - Qt's name
        return QSize(640, theme.STATUS_HEIGHT)

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        painter = QPainter(self)
        painter.fillRect(QRectF(self.rect()), theme.color("chrome.statusbar"))
        painter.fillRect(QRectF(0, 0, self.width(), 1), theme.color("chrome.divider"))
        base.paint(painter, self, event.rect())
