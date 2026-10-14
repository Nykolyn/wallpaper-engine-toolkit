"""Widget lifecycle shares the tray's feed and clock; no live tracker is started."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["WALLPAPER_TOOLKIT_DATA"] = tempfile.mkdtemp(prefix="toolkit_widget_lifecycle_")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QSystemTrayIcon
from app import tracker_tray
from app.engines.wallpaper_timer import Countdown
from app.settings import Settings

app = QApplication([])


class Feed(QObject):
    updated = Signal()
    config_changed = Signal()
    failed = Signal(str)

    def __init__(self, *args):
        super().__init__()
        self.results = []
        self.error = ""
        self.following = True
        self.config_path = "X:/WE/config.json"
        self.files = SimpleNamespace(config={"user": {"general": {"user": {
            "monitormap": {"device-path-0": {"location": 0},
                           "device-path-1": {"location": 1}}}}}})


class Shell(QObject):
    activated = Signal(object)
    messageClicked = Signal()
    Trigger, DoubleClick = QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick

    def __init__(self, *args):
        super().__init__()
        self.tip = ""

    def setToolTip(self, text):
        self.tip = text

    def toolTip(self):
        return self.tip

    def setIcon(self, *args):
        pass

    def setContextMenu(self, *args):
        pass

    def show(self):
        pass


class Listener(QObject):
    requested = Signal()
    widget_requested = Signal(bool)


class Widget(QObject):
    settings_requested = Signal()
    visibility_changed = Signal(bool)

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.visible = False
        self.reading = None
        self.synced = 0

    def render(self, *reading):
        self.reading = reading

    def sync_settings(self):
        self.synced += 1

    def show(self):
        self.visible = True

    def hide(self):
        self.visible = False


settings = Settings({"widget": {"enabled": True}, "tracker": {"primary": "Monitor1"}})
settings.save()
countdowns = {"Monitor1": Countdown("Monitor1", 600, 120, known=True)}
clock = SimpleNamespace(tick=lambda: countdowns)
with patch.object(tracker_tray, "TrackerFeed", Feed), \
        patch.object(tracker_tray, "WallpaperTimer", lambda *a, **k: clock), \
        patch.object(tracker_tray, "QSystemTrayIcon", Shell), \
        patch.dict(sys.modules, {"app.desktop_widget": SimpleNamespace(DesktopWidget=Widget)}):
    tray = tracker_tray.TrackerTray(app, Listener())
    tray.clock_timer.stop()
    assert tray.widget is None, "tracker startup does not enable widget startup implicitly"
    tray._tick_clock()
    assert tray.widget is None, "persisted visibility alone cannot bypass the separate startup switch"
    first = SimpleNamespace(monitor="Monitor0", seen=1, total=10, percent=10)
    chosen = SimpleNamespace(monitor="Monitor1", seen=2, total=10, percent=20)
    tray.results = [first, chosen]
    tray._set_widget_visible(True)
    assert tray.widget.visible and tray.widget.settings is tray.settings
    assert tray.widget.reading[0] is tray.results and tray.widget.reading[1] is countdowns
    assert tray.widget.reading[2] is chosen, "compact uses the tray's explicit lead monitor"
    assert set(tray.widget.reading[4]) == {"Monitor0", "Monitor1"}
    tray.widget.visibility_changed.emit(False)
    assert not tray.widget.visible and not Settings.load().get("widget", "enabled", True)
    assert tray.clock is clock, "hiding the widget leaves the tracker and clock running"
    external = Settings.load()
    external.set("widget", "enabled", True)
    external.save()
    tray._sync_widget()
    assert tray.widget.visible, "changes made by the Utilities page reach the widget"
    with patch.object(tray, "open_toolkit") as open_toolkit:
        tray.widget.settings_requested.emit()
        open_toolkit.assert_called_once_with("Widget")
    old_syncs = tray.widget.synced
    tray._tick_clock()
    assert tray.widget.synced > old_syncs
    assert tray.widget.reading[1] is countdowns

print("PASS widget startup, visibility, selected monitor and shared countdown lifecycle")
