"""Overview: the loop at a glance — for now, a page that says it is coming.

The frame opens on it, so its place and its header are real: today's date
under the title, and "Refresh now", which reads the snapshot's numbers again
(the sidebar shows them). The page itself is built in the next step; until
then it says so, rather than showing a dashboard with nothing behind it.
"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QVBoxLayout, QWidget

from .. import theme
from ..ui.kit import GhostButton, format as fmt, label
from .base import Page

# How often the date under the title is looked at again.
_CLOCK_MS = 15_000


class OverviewPage(Page):
    key = "overview"
    title = "Overview"
    icon = "overview"
    FIXTURES = ("default",)

    def __init__(self, services=None, feed=None, parent: QWidget | None = None, *, now=None):
        super().__init__(parent)
        self._services = services
        self._feed = feed
        self._now = now or datetime.now
        column = QVBoxLayout(self)
        column.setContentsMargins(theme.BODY_PAD[1], theme.BODY_PAD[0],
                                  theme.BODY_PAD[1], theme.BODY_PAD[0])
        self.placeholder = label("The overview of the loop is coming in the next step.",
                                 "type.body", "lo")
        self.placeholder.setAlignment(Qt.AlignCenter)
        column.addWidget(self.placeholder, 1)
        self._clock = QTimer(self)
        self._clock.setInterval(_CLOCK_MS)
        self._clock.timeout.connect(self._tick)
        self._clock.start()
        self._tick()

    def _tick(self) -> None:
        self.set_subtitle(fmt.date_long(self._now()))

    def make_header_actions(self) -> list[QWidget]:
        refresh = GhostButton("Refresh now", outlined=True)
        refresh.clicked.connect(self.refresh)
        return [refresh]

    def refresh(self) -> None:
        """Read the numbers again: the snapshot, and the tracker's own look."""
        if self._feed is not None:
            self._feed.refresh()
        if self._services is not None:
            self._services.snapshot.refresh()

    def load_fixture(self, state: str) -> None:
        if state not in self.FIXTURES:
            super().load_fixture(state)
        # A made-up moment, so a snapshot does not carry the day it was taken.
        self._clock.stop()
        self._now = lambda: datetime(2026, 9, 19, 13, 44)
        self._tick()
