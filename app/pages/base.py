"""A page of the window: what the frame needs to know about it.

The frame (app/main_window.py) holds one `Page` per entry in the sidebar. A
page says what its header shows, what its sidebar item says, and hears when
it comes on screen and goes off it:

- `key`, `title`, `icon`: which page, its name, its sidebar glyph;
- `subtitle()` and `subtitle_changed`: the line beside the title;
- `header_actions()`: the buttons at the header's right, made once;
- `nav_state()` and `nav_state_changed`: what its sidebar item shows
  (a `NavState`: progress, a count, a badge, a word);
- `navigate`: asks the window for another page, by key ("settings");
- `on_shown()` / `on_hidden()`;
- `FIXTURES` and `load_fixture(state)`: made-up states for
  `tools/ui_snapshot.py`, which never reads this machine's data;
  `frame_fixture(state)`: the frame's state that goes with one of them.

`SideScroll` is the 330 px column a page's left side is (the Tracker's
monitors, the Rotator's next run).
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget

from .. import theme
from ..ui.kit import NavState


class Page(QWidget):
    key = ""
    title = ""
    icon = ""
    # The states `load_fixture` knows; `tools/ui_snapshot.py --state` picks one.
    FIXTURES: tuple[str, ...] = ()

    subtitle_changed = Signal(str)
    nav_state_changed = Signal(object)      # NavState
    navigate = Signal(str)                  # a page key

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._subtitle = ""
        self._nav = NavState()
        self._actions: list[QWidget] | None = None
        if self.title:
            self.setAccessibleName(self.title)

    # -- the header

    def subtitle(self) -> str:
        return self._subtitle

    def set_subtitle(self, text: str) -> None:
        if text != self._subtitle:
            self._subtitle = text
            self.subtitle_changed.emit(text)

    def header_actions(self) -> list[QWidget]:
        """The header's buttons for this page, made on first asking and kept."""
        if self._actions is None:
            self._actions = list(self.make_header_actions())
        return self._actions

    def make_header_actions(self) -> list[QWidget]:
        """Override: the widgets for the header's right, in order."""
        return []

    # -- the sidebar

    def nav_state(self) -> NavState:
        return self._nav

    def set_nav_state(self, state: NavState) -> None:
        if state != self._nav:
            self._nav = state
            self.nav_state_changed.emit(state)

    def content_minimum(self, width: int | None = None) -> QSize:
        """The room the page's content needs to be shown whole — at `width`,
        when its height depends on it — if more than the frame's own minimum;
        the window opens at least this big. Invalid: nothing more."""
        return QSize()

    # -- on and off screen

    def on_shown(self) -> None:
        """The page has just become the one on screen."""

    def on_hidden(self) -> None:
        """Another page has just taken its place."""

    # -- snapshots

    def frame_fixture(self, state: str) -> dict | None:
        """For a state of this page's own: the frame's state that goes with it
        (`{"frame": "running"}`, a state of tests/fixtures/ui/shell.json), and
        what the page changes in it — its sidebar item under "nav", "Next in
        the loop" under "next" — so the frame agrees with the page. None:
        the frame's state of the same name, as it is."""
        return None

    def load_fixture(self, state: str) -> None:
        """Show a made-up state, for `tools/ui_snapshot.py`. A page with no
        state of that name says so."""
        raise KeyError(f"the {self.key or type(self).__name__} page has no fixture {state!r}"
                       + (f"; it has {', '.join(self.FIXTURES)}" if self.FIXTURES else ""))


class SideScroll(QScrollArea):
    """A page's left column, `width` px, scrolling when the window is too
    short for it. The scroll bar is added beside the column rather than taken
    out of it, so the panels keep the width they are drawn for."""

    def __init__(self, column: QWidget, width: int, parent: QWidget | None = None):
        super().__init__(parent)
        self._width = width
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setWidget(column)
        column.setFixedWidth(width)
        self.verticalScrollBar().rangeChanged.connect(self._fit)
        self._fit()

    def _fit(self, *_args) -> None:
        bar = self.verticalScrollBar()
        extra = bar.sizeHint().width() if bar.maximum() > 0 else 0
        self.setFixedWidth(self._width + extra)

    def scrolls(self) -> bool:
        return self.verticalScrollBar().maximum() > 0
