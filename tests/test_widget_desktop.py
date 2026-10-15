"""Native layer decisions without touching Explorer or Wallpaper Engine windows."""
from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QWidget

from app import widget_desktop as desktop

app = QApplication.instance() or QApplication([])


class Windows:
    def __init__(self):
        self.shell = 100
        self.modern = False
        self.calls = []
        self.success = True

    def GetShellWindow(self):
        return self.shell

    def FindWindowExW(self, parent, previous, name, title):
        if parent is None:
            # Another program's WorkerW must never become our desktop host.
            return {None: 300, 300: 200}.get(previous)
        return 777 if parent == 200 or parent == 300 or (parent == 100 and self.modern) else None

    def GetWindowThreadProcessId(self, hwnd, pid):
        ctypes.cast(pid, ctypes.POINTER(wintypes.DWORD)).contents.value = 999 if hwnd == 300 else 42
        return 1

    def IsWindowVisible(self, hwnd):
        return True

    def IsWindow(self, hwnd):
        return hwnd in (100, 200)

    def GetWindow(self, hwnd, kind):
        assert kind == desktop.GW_HWNDPREV
        return 800

    def SetWindowPos(self, hwnd, after, x, y, width, height, flags):
        self.calls.append((hwnd, after, x, y, width, height, flags))
        return self.success


windows = Windows()
assert desktop._desktop_host(windows) == 200, "only Explorer's icon WorkerW is used"
windows.modern = True
assert desktop._desktop_host(windows) == 100, "new Explorer's Progman icon host is supported"
windows.shell = None
assert desktop._desktop_host(windows) is None
windows.shell = 100
other = SimpleNamespace(layer="desktop", hwnd=22, widget=SimpleNamespace(isVisible=lambda: True))
with patch.object(desktop, "_layers", [other]), \
        patch.object(windows, "GetWindow", side_effect=lambda hwnd, _: {100: 22, 22: 11, 11: 800}[hwnd]):
    assert desktop._anchor(windows, 11, 100) == 800
    assert desktop._anchor(windows, 22, 100) == 11, "multiple desktop widgets keep their relative order"

widget = QWidget()
widget.setGeometry(-1200, 1100, 176, 176)
widget.show()
handle, geometry = int(widget.winId()), widget.geometry()

with patch.object(QApplication, "platformName", return_value="windows"), \
        patch.object(desktop.sys, "platform", "win32"), \
        patch.object(desktop, "_api", return_value=windows):
    assert desktop.apply_layer(widget), "desktop is the default layer"
    helper = widget._desktop_layer
    assert helper.layer == "desktop" and helper.timer.isActive()
    assert [call[1] for call in windows.calls] == [desktop.HWND_NOTOPMOST, 800]
    assert all(call[0] == handle and call[2:6] == (0, 0, 0, 0) for call in windows.calls)
    assert all(call[-1] & desktop.SWP_NOACTIVATE for call in windows.calls)
    assert widget.geometry() == geometry and int(widget.winId()) == handle, "Qt owns coordinates and DPI"

    assert desktop.apply_layer(widget, "top")
    assert widget._desktop_layer is helper, "one native filter and timer per widget"
    assert windows.calls[-1][1] == desktop.HWND_TOPMOST and not helper.timer.isActive()
    assert desktop.apply_layer(widget, "normal")
    assert windows.calls[-1][1] == desktop.HWND_NOTOPMOST and not helper.timer.isActive()

    windows.shell = None
    assert not desktop.apply_layer(widget, "desktop")
    assert windows.calls[-1][1] == desktop.HWND_BOTTOM, "missing Explorer stays below applications"
    windows.shell = 100
    assert helper._follow_desktop(), "Explorer restart is rediscovered by the same helper"

    position = desktop._WindowPos(handle, desktop.HWND_TOPMOST, -1920, 1065, 264, 264, 0)
    message = wintypes.MSG()
    message.hWnd, message.message = handle, desktop.WM_WINDOWPOSCHANGING
    message.lParam = ctypes.addressof(position)
    assert helper.filter.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(message)) == (False, 0)
    assert position.hwndInsertAfter == 800, "clicking or dragging cannot raise a desktop widget over apps"
    assert position.flags & desktop.SWP_NOACTIVATE
    assert (position.x, position.y, position.cx, position.cy) == (-1920, 1065, 264, 264)
    position.hwndInsertAfter, position.flags = desktop.HWND_BOTTOM, desktop.SWP_NOZORDER
    helper.filter.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(message))
    assert position.hwndInsertAfter == desktop.HWND_BOTTOM, "Qt's position-only changes stay untouched"

    widget.hide()
    assert not helper.timer.isActive()
    widget.show()
    assert helper.timer.isActive(), "showing restarts desktop tracking"
    windows.success = False
    assert not desktop.apply_layer(widget), "Windows failures are reported without changing foreign windows"
    windows.success = True

assert not desktop.apply_layer(widget), "offscreen platforms do not call Win32 with Qt's fake handle"
try:
    desktop.apply_layer(widget, "invalid")
    raise AssertionError("invalid layer accepted")
except ValueError:
    pass

widget.deleteLater()
QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
assert helper.filter.owner is None, "destroying a widget removes its native event filter"
print("PASS desktop host ownership, layers, nonactivation, DPI geometry and helper lifetime")
