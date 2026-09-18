"""Top-level window: the bundled tools as tabs under one roof."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMainWindow

from . import __version__
from .animations import FadingTabWidget
from .settings import Settings
from .ui.copier_tab import CopierTab
from .ui.creator_tab import CreatorTab
from .ui.rotator_tab import RotatorTab
from .ui.review_tab import ReviewTab
from .ui.tracker_tab import TrackerTab


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = Settings.load()

        self.setWindowTitle(f"Wallpaper Engine Toolkit {__version__}")
        self.resize(1040, 780)

        self.tabs = FadingTabWidget()
        self.tabs.setDocumentMode(True)
        self.setCentralWidget(self.tabs)

        # Shared Settings instance for the two settings-in-UI tabs; the Rotator
        # manages its own verbatim Config/History.
        self.tabs.addTab(CopierTab(self.settings), "Copier")
        self.tabs.addTab(CreatorTab(self.settings), "Creator")
        self.tabs.addTab(RotatorTab(), "Rotator")
        self.tabs.addTab(TrackerTab(self.settings), "Tracker")
        self.tabs.addTab(ReviewTab(self.settings), "Review")

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
