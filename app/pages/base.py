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

`LegacyPage` puts one of the old tabs in the frame until its own page
replaces it.
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Signal
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


class LegacyPage(Page):
    """One of the old tabs, in the new frame, until its own page replaces it.

    The tab keeps its behaviour; the page adds a little room round it, a title
    and subtitle for the header, and the tab's `on_shown()`, if it has one.
    What its sidebar item says is set from outside (see `app/pages/legacy.py`).
    """

    def __init__(self, key: str, title: str, icon: str, tab: QWidget, subtitle: str = "",
                 parent: QWidget | None = None):
        self.key, self.title, self.icon = key, title, icon
        super().__init__(parent)
        self.tab = tab
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        # The old tabs were laid out for a window that grew to fit them (the
        # Tracker's needs 930 px); the frame's is 720 px at the least. Below a
        # tab's own minimum the page scrolls rather than squeezing it.
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        holder = QWidget()
        inner = QVBoxLayout(holder)
        # The tabs lay themselves out with the style's own margins inside this.
        inner.setContentsMargins(theme.SP_6, theme.SP_4, theme.SP_6, theme.SP_6)
        inner.addWidget(tab)
        self.scroll.setWidget(holder)
        column.addWidget(self.scroll)
        self.set_subtitle(subtitle)
        if hasattr(tab, "settings_requested"):
            tab.settings_requested.connect(lambda: self.navigate.emit("settings"))

    def content_minimum(self, width: int | None = None) -> QSize:
        # Measured once the stylesheet has reached the tab: before, its
        # widgets are smaller than they will be. A tab with wrapped lines
        # needs more height than its minimum says; the scroll area gives it
        # its height for the width it has, and so does this.
        holder = self.scroll.widget()
        holder.ensurePolished()
        for child in holder.findChildren(QWidget):
            child.ensurePolished()
        layout = holder.layout()
        layout.activate()
        need = holder.minimumSizeHint()
        if width is not None and layout.hasHeightForWidth():
            need.setHeight(max(need.height(), holder.heightForWidth(max(width, need.width()))))
        return need

    def on_shown(self) -> None:
        shown = getattr(self.tab, "on_shown", None)
        if shown is not None:
            shown()
