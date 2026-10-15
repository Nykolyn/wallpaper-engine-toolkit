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

    def __init__(self, settings, *, profile_key="default", screen=None):
        super().__init__()
        self.settings = settings
        self.visible = False
        self.reading = None
        self.synced = 0
        self.profile_key = profile_key
        self.initial_screen = screen

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
primary_screen = app.primaryScreen()
geometry, dpr = primary_screen.geometry(), primary_screen.devicePixelRatio()
native_rects = {"Monitor0": (geometry.x(), geometry.y(), geometry.x() + round(geometry.width() * dpr),
                              geometry.y() + round(geometry.height() * dpr))}
with patch.object(tracker_tray, "TrackerFeed", Feed), \
        patch.object(tracker_tray, "WallpaperTimer", lambda *a, **k: clock), \
        patch.object(tracker_tray, "display_rects", return_value=native_rects), \
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

    from app.widget_options import set_layout, set_option
    set_layout(tray.settings, "per_monitor")
    tray._sync_widget()
    assert tray.widget is not None and not tray.widget.visible
    assert tray.widgets["Monitor0"].visible and tray.widgets["Monitor1"].visible
    assert tray.widgets["Monitor0"].profile_key == "Monitor0"
    assert tray.widgets["Monitor0"].initial_screen is primary_screen, "initial placement cannot wait for the first clock tick"
    tray.widgets["Monitor0"].visibility_changed.emit(False)
    assert not tray.widgets["Monitor0"].visible and tray.widgets["Monitor1"].visible
    assert Settings.load().get("widget", "enabled", False), "hide affects only the chosen monitor"
    set_option(tray.settings, "Monitor0", "enabled", True)
    tray._sync_widget()
    assert tray.widgets["Monitor0"].visible
    tray._set_widget_visible(False)
    assert not any(widget.visible for widget in tray.widgets.values())
    tray._set_widget_visible(True)
    assert tray.widgets["Monitor0"].visible and tray.widgets["Monitor1"].visible
    with patch.object(tray, "open_toolkit") as open_toolkit:
        tray._on_chosen("widget")
        open_toolkit.assert_called_once_with("Widget")
    tray._on_chosen("widget_visible")
    assert not any(widget.visible for widget in tray.widgets.values())

print("PASS widget startup, visibility, selected monitor and shared countdown lifecycle")

from PySide6.QtCore import QRect
screens = [SimpleNamespace(geometry=lambda: QRect(0, 0, 2560, 1440), devicePixelRatio=lambda: 1.5),
           SimpleNamespace(geometry=lambda: QRect(-1920, 1065, 1920, 1080), devicePixelRatio=lambda: 1)]
assert tracker_tray.screen_for_monitor((0, 0, 3840, 2160), screens) is screens[0]
assert tracker_tray.screen_for_monitor((-1920, 1065, 0, 2145), screens) is screens[1]
assert tracker_tray.screen_for_monitor((0, 0, 1920, 1080), screens) is None
print("PASS physical WE rectangles map to mixed-DPI Qt screens, preserving negative origins")

# Use real desktop windows and the real metadata worker with fabricated tracker readings.
import time
item = Path(os.environ["WALLPAPER_TOOLKIT_DATA"]) / "sky.mp4"
item.write_bytes(b"sky" * 1024)
(item.parent / "project.json").write_text('{"title":"Night sky"}', encoding="utf-8")
settings = Settings({"tracker": {"primary": "Monitor0"}, "widget": {"enabled": True, "layout": "per_monitor", "profiles": {
    "Monitor0": {"mode": "expanded", "layer": "normal"}}}})
settings.save()
with patch.object(tracker_tray, "TrackerFeed", Feed), \
        patch.object(tracker_tray, "WallpaperTimer", lambda *a, **k: clock), \
        patch.object(tracker_tray, "display_rects", return_value=native_rects), \
        patch.object(tracker_tray, "QSystemTrayIcon", Shell):
    real_tray = tracker_tray.TrackerTray(app, Listener(), show_widget=True)
    real_tray.clock_timer.stop()
    real_tray.results = [SimpleNamespace(monitor="Monitor0", playlist="Sky", seen=2, total=10,
                                        percent=20, current=str(item), current_title="Fallback")]
    real_tray._render_widget()
    until = time.monotonic() + 3
    while str(item) not in real_tray._metadata.data and time.monotonic() < until:
        app.processEvents()
        time.sleep(.005)
    row = real_tray.widgets["Monitor0"].rows[0]
    assert row.wallpaper_title == "Night sky" and row.size_text == "3 KB"
    assert real_tray.widgets["Monitor1"].rows[0].monitor == "Monitor1"
    assert real_tray.widgets["Monitor1"].rows[0].state == "unknown"
    real_tray.widgets["Monitor0"].request_hide()
    assert not real_tray.widgets["Monitor0"].isVisible()
    assert real_tray.widgets["Monitor1"].isVisible()
    assert Settings.load().get("widget", "enabled", False)
    real_tray._metadata.stop()
    for window in real_tray.widgets.values():
        window.close()
print("PASS real per-monitor windows share metadata, expose missing playlists and hide independently")

listener = Listener()
application = SimpleNamespace(setApplicationName=lambda *a: None,
                              setQuitOnLastWindowClosed=lambda *a: None)
from unittest.mock import Mock
application.exec = Mock(side_effect=AssertionError("quit before exec was lost"))
watch = Mock()

def build_after_quit(app, listener, **kwargs):
    listener.requested.emit()
    return SimpleNamespace(icon=SimpleNamespace(isVisible=lambda: True))

with patch.object(tracker_tray, "_claim_single_instance", return_value=True), \
        patch.object(tracker_tray, "QApplication", return_value=application), \
        patch.object(tracker_tray.theme, "apply"), \
        patch.object(tracker_tray, "QuitListener", return_value=listener), \
        patch.object(tracker_tray, "HangWatch", return_value=SimpleNamespace(start=lambda: watch)), \
        patch.object(tracker_tray, "TrackerTray", side_effect=build_after_quit), \
        patch.object(tracker_tray, "log"):
    assert tracker_tray.run_tray(show_widget=True) == 0
    application.exec.assert_not_called()
    watch.stop.assert_called_once()

print("PASS a quit received while the widget is being built survives until event-loop entry")
