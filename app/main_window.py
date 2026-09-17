"""Top-level window: the bundled tools as tabs under one roof."""
from __future__ import annotations

from PySide6.QtWidgets import QMainWindow

from .animations import FadingTabWidget
from .settings import Settings
from .ui.auto_creator_tab import AutoCreatorTab
from .ui.copier_tab import CopierTab
from .ui.creator_tab import CreatorTab
from .ui.rotator_tab import RotatorTab
from .ui.review_tab import ReviewTab
from .ui.tracker_tab import TrackerTab


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = Settings.load()

        self.setWindowTitle("Wallpaper Engine Toolkit")
        self.resize(1040, 780)

        self.tabs = FadingTabWidget()
        self.tabs.setDocumentMode(True)
        self.setCentralWidget(self.tabs)

        # Shared Settings instance for the two settings-in-UI tabs; the Rotator
        # manages its own verbatim Config/History.
        self.tabs.addTab(CopierTab(self.settings), "Copier")
        self.tabs.addTab(CreatorTab(self.settings), "Creator")
        self.tabs.addTab(AutoCreatorTab(self.settings), "Auto Creator")
        self.tabs.addTab(RotatorTab(), "Rotator")
        self.tabs.addTab(TrackerTab(self.settings), "Tracker")
        self.tabs.addTab(ReviewTab(self.settings), "Review")
