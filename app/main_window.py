"""Top-level window: the bundled tools as tabs under one roof."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QMainWindow, QVBoxLayout, QWidget

from . import __version__, services, theme
from .animations import FadingTabWidget
from .settings import Settings
from .tracker_feed import TrackerFeed, heartbeat_setting
from .ui.copier_tab import CopierTab
from .ui.creator_tab import CreatorTab
from .ui.rotator_tab import RotatorTab
from .ui.review_tab import ReviewTab
from .ui.tracker_tab import TrackerTab


class Backdrop(QWidget):
    """The window's ground: `bg.app`, painted once behind everything.

    Every widget above it is transparent, so the translucent panels of the
    design pick the gradient up through themselves. It repaints under any child
    that repaints, so the brush is built once per size, not once per paint.
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._brush = None

    def resizeEvent(self, event) -> None:       # noqa: N802 - Qt's name
        self._brush = theme.app_background(self.rect())
        super().resizeEvent(event)

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        if self._brush is None:
            self._brush = theme.app_background(self.rect())
        QPainter(self).fillRect(event.rect(), self._brush)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = Settings.load()

        self.setWindowTitle(f"Wallpaper Engine Toolkit {__version__}")
        self.resize(1040, 780)

        backdrop = Backdrop()
        frame = QVBoxLayout(backdrop)
        frame.setContentsMargins(theme.SP_12, theme.SP_4, theme.SP_12, theme.SP_12)
        self.tabs = FadingTabWidget()
        self.tabs.setDocumentMode(True)
        frame.addWidget(self.tabs)
        self.setCentralWidget(backdrop)

        # The window's own look at the tracker: the Tracker tab shows it, the
        # snapshot and the journal listen to it.
        self.feed = TrackerFeed(self.settings.get("tracker", "we_config", None),
                                heartbeat_setting(self.settings), parent=self)
        # What the tabs report their work to (see app/services). Installed
        # before the tabs, which find it when they start something.
        self.services = services.Services(self, settings=self.settings, feed=self.feed)
        services.install(self.services)

        # Shared Settings instance for the two settings-in-UI tabs; the Rotator
        # manages its own verbatim Config/History.
        self.tabs.addTab(CopierTab(self.settings), "Copier")
        self.tabs.addTab(CreatorTab(self.settings), "Creator")
        self.tabs.addTab(RotatorTab(), "Rotator")
        self.tabs.addTab(TrackerTab(self.settings, self.feed), "Tracker")
        self.tabs.addTab(ReviewTab(self.settings), "Review")
        self.services.start()

    def show_tab(self, name: str) -> bool:
        """Switch to the tab with this title. False if there is none."""
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i).casefold() == (name or "").casefold():
                self.tabs.setCurrentIndex(i)
                return True
        return False

    def bring_forward(self, tab: str = "") -> None:
        """Answer a click on the tray icon, or a second launch of the program."""
        if tab:
            self.show_tab(tab)
        self.show()
        self.setWindowState((self.windowState() & ~Qt.WindowMinimized) | Qt.WindowActive)
        self.raise_()
        self.activateWindow()
