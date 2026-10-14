"""Desktop widget readings and logical screen geometry, with no live WE state."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
TMP = Path(tempfile.mkdtemp(prefix="desktop_widget_test_"))
os.environ["WALLPAPER_TOOLKIT_DATA"] = str(TMP / "data")
(TMP / "data").mkdir()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(errors="replace")

from PySide6.QtCore import QPoint, QRect, QSize, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QToolButton  # noqa: E402

app = QApplication.instance() or QApplication([])
from app import theme  # noqa: E402
from app.desktop_widget import DesktopWidget, MonitorRow, countdown_text, normalized_position, screen_position  # noqa: E402
from app.engines.wallpaper_timer import Countdown  # noqa: E402
from app.settings import Settings  # noqa: E402

theme.apply(app, styled=False)
checks = []


def check(label, condition):
    checks.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label)


def progress(monitor, playlist, seen=4, total=20):
    return SimpleNamespace(monitor=monitor, playlist=playlist, seen=seen, total=total,
                           percent=round(100 * seen / total) if total else 0)


settings = Settings({"tracker": {"primary": "Monitor1"}})
settings.save()
widget = DesktopWidget(settings)
p0, p1 = progress("Monitor0", "Landscape"), progress("Monitor1", "Space")
clock0 = Countdown("Monitor0", 600, running=60, known=True)
clock1 = Countdown("Monitor1", 600, running=120, known=True)
widget.render([p0, p1], {"Monitor0": clock0, "Monitor1": clock1}, p1)
check("compact is default and follows selected tray monitor", widget.mode == "compact"
      and len(widget.rows) == 1 and widget.rows[0].monitor == "Monitor1")
row = widget.rows[0]
check("monitor and playlist are accessible", row.accessibleName() == "Monitor1 · Space")
check("numeric countdown and draining fraction use remaining time", row.timer_text == "8:00"
      and row.fraction == 0.8 and row.state == "running")
clock1.running = 240
widget.render([p0, p1], {"Monitor1": clock1}, p1)
check("ring drains as time passes", row.timer_text == "6:00" and row.fraction == 0.6)
clock1.paused = True
widget.render([p0, p1], {"Monitor1": clock1}, p1)
check("paused state shares tray semantics and keeps countdown", row.state == "paused"
      and "paused" in row.accessibleDescription() and row.timer_text == "6:00")
clock1.approximate = True
widget.render([p0, p1], {"Monitor1": clock1}, p1)
check("approximate timer is visibly marked", row.timer_text == "≈6:00")
clock1.known = False
widget.render([p0, p1], {"Monitor1": clock1}, p1)
check("unknown timer has no guessed number or ring", row.state == "unknown"
      and row.fraction is None and row.timer_text == "—:—")
clock1.known, clock1.active = True, False
widget.render([p0, p1], {"Monitor1": clock1}, p1)
check("inactive timer is unknown", row.state == "unknown" and row.fraction is None)
p1.seen, p1.percent = 20, 100
widget.render([p0, p1], {"Monitor1": clock1}, p1)
check("finished takes precedence over unknown as in tray", row.state == "finished")
clock1.active, clock1.paused = True, False
clock1.running = 600
widget.render([p0, p1], {"Monitor1": clock1}, p1)
check("finished retains separate numeric countdown", row.timer_text == "≈0:00" and row.fraction == 0)
check("fractional remaining seconds are rounded up", countdown_text(
    Countdown("Monitor0", 600, running=599.7, known=True)) == "0:01")

widget.mode_button.click()
widget.render([p0, p1], {"Monitor0": clock0}, p1, monitors=("Monitor0", "Monitor1", "Monitor2"))
check("expanded includes all monitors including missing playlists", widget.mode == "expanded"
      and [row.monitor for row in widget.rows] == ["Monitor0", "Monitor1", "Monitor2"])
check("missing playlist is clear and has no countdown", widget.rows[-1].progress is None
      and widget.rows[-1].timer_text == "—:—" and "No playlist" in widget.rows[-1].accessibleName())
check("mode button persists choice", Settings.load().get("widget", "mode", None) == "expanded")
widget.render([], {}, None, "Wallpaper Engine not found", monitors=("Monitor0", "Monitor1"))
check("feed error remains accessible", "Wallpaper Engine not found" in widget.rows[0].accessibleDescription())
external = Settings.load()
external.set("widget", "mode", "compact")
external.set("creator", "source", "keep this setting")
external.save()
widget.sync_settings()
check("external settings update mode and keep selected monitor", widget.mode == "compact"
      and widget.rows[0].monitor == "Monitor1")
widget.mode_button.click()
check("mode write preserves other process settings", Settings.load().get(
    "creator", "source", None) == "keep this setting")
requested = []
widget.settings_requested.connect(lambda: requested.append(True))
for button in widget.header.findChildren(QToolButton):
    if button.accessibleName() == "Widget settings":
        button.click()
check("settings button emits the settings request", requested == [True])

size = QSize(360, 180)
areas = (QRect(-1920, -120, 1920, 1040), QRect(0, 0, 2560, 1400), QRect(2560, -400, 1280, 680))
for index, area in enumerate(areas):
    point = screen_position(area, size, 0.75, 0.25)
    relative = normalized_position(area, size, point)
    check(f"logical geometry roundtrip on screen {index}", abs(relative[0] - .75) < .001
          and abs(relative[1] - .25) < .001 and area.contains(QRect(point, size)))
check("negative monitor origin remains negative", screen_position(areas[0], size, 0.5, 0.5).x() < 0)
check("out of range stored position clamps onto screen", screen_position(areas[0], size, -4, 9)
      == QPoint(areas[0].left(), areas[0].y() + areas[0].height() - size.height()))
check("oversized window anchor is reachable", screen_position(QRect(-20, 10, 200, 90), size, 1, 1)
      == QPoint(-20, 10))
before = screen_position(areas[1], size, .95, .8)
after_size = QSize(360, 440)
after = screen_position(areas[1], after_size, *normalized_position(areas[1], size, before))
check("expanded size retains relative placement and stays inside", areas[1].contains(QRect(after, after_size))
      and abs(normalized_position(areas[1], after_size, after)[1] - .8) < .001)


class Screen:
    def __init__(self, name, area, scale):
        self._name, self.area, self.scale = name, area, scale

    def name(self):
        return self._name

    def availableGeometry(self):
        return self.area


left, right = Screen("left", areas[0], 1.0), Screen("right", areas[2], 2.0)
settings.set("widget", "position", {"screen": "left", "x": .8, "y": .7})
with patch.object(QApplication, "screens", return_value=[left, right]), \
        patch.object(QApplication, "primaryScreen", return_value=right):
    widget._restore_position()
    check("restores saved screen by name at negative coordinates", left.area.contains(widget.geometry()))
    left.area = QRect(-1280, -100, 1280, 700)
    left.scale = 1.5
    widget._restore_position()
    check("resolution and DPI change restores inside logical available area", left.area.contains(widget.geometry())
          and widget.width() == 360)
with patch.object(QApplication, "screens", return_value=[right]), \
        patch.object(QApplication, "primaryScreen", return_value=right):
    widget._restore_position()
    check("removed screen recovers to primary", right.area.contains(widget.geometry()))
    settings.set("widget", "position", {"screen": "right", "x": float("nan"), "y": "bad"})
    widget._restore_position()
    check("invalid stored coordinates recover inside screen", right.area.contains(widget.geometry()))
    settings.set("widget", "position", [])
    widget._restore_position()
    check("invalid position record recovers", right.area.contains(widget.geometry()))
    right.area = QRect(10, -40, 320, 260)
    widget.render([progress(f"Monitor{i}", "List") for i in range(6)], {}, None)
    widget._fit_window(right)
    check("many monitors fit screen with scrollable contents", right.area.contains(widget.geometry())
          and widget.content.minimumSizeHint().height() > widget.height())

widget._restored = False
widget.show()
app.processEvents()
widget.render([p0, p1], {"Monitor0": clock0, "Monitor1": clock1}, p1)
app.processEvents()
check("widget uses a tool window and shows without activation", bool(widget.windowFlags() & Qt.Tool)
      and widget.testAttribute(Qt.WA_ShowWithoutActivating))
check("visible expanded rows draw", widget.grab().toImage().pixelColor(20, 80).alpha() > 0)
widget.save_position()
stored = Settings.load().get("widget", "position", {})
check("saved placement is screen name and normalized fractions", isinstance(stored.get("screen"), str)
      and 0 <= stored["x"] <= 1 and 0 <= stored["y"] <= 1)
hidden = []
widget.visibility_changed.connect(hidden.append)
widget.request_hide()
check("user hide emits visibility off", not widget.isVisible() and hidden == [False])
widget.show()
widget.close()
check("shutdown close leaves visibility preference unchanged", not widget.isVisible() and hidden == [False])
shutdown_data = TMP / "shutdown-data"
shutdown_data.mkdir()
environment = dict(os.environ, WALLPAPER_TOOLKIT_DATA=str(shutdown_data))
script = """
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from app.desktop_widget import DesktopWidget
from app.settings import Settings
app = QApplication([])
settings = Settings({'widget': {'enabled': True}})
settings.save()
widget = DesktopWidget(settings)
def visibility_changed(enabled):
    settings.set('widget', 'enabled', enabled)
    settings.save()
widget.visibility_changed.connect(visibility_changed)
widget.show()
QTimer.singleShot(50, app.quit)
app.exec()
assert Settings.load().get('widget', 'enabled', None) is True
print('visibility preserved')
"""
shutdown = subprocess.run([sys.executable, "-c", script], env=environment,
                          capture_output=True, text=True, timeout=15)
check("QApplication.quit preserves visibility for next launch", shutdown.returncode == 0
      and "visibility preserved" in shutdown.stdout)
check("widget does not import toolkit pages or kit", not any(name in sys.modules for name in
      ("app.ui", "app.pages", "app.main_window")))

print(f"\n{sum(checks)}/{len(checks)} passed")
raise SystemExit(0 if all(checks) else 1)
