"""Keep our top-level widget in the chosen Windows layer without reparenting it.

Explorer's desktop icon host moves forward for Win+D. Following its z-order
keeps the widget on that desktop while Qt retains its own per-monitor DPI and
movement. Only our HWND is changed; Wallpaper Engine's WorkerW is never used.
"""
from __future__ import annotations

import ctypes
import sys
import weakref
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QEvent, QObject, QTimer
from PySide6.QtWidgets import QApplication

HWND_TOP, HWND_BOTTOM, HWND_TOPMOST, HWND_NOTOPMOST = 0, 1, -1, -2
GW_HWNDPREV = 3
WM_WINDOWPOSCHANGING = 0x0046
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER = 0x0001, 0x0002, 0x0004
SWP_NOACTIVATE, SWP_NOOWNERZORDER, SWP_NOSENDCHANGING = 0x0010, 0x0200, 0x0400
_FLAGS = SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_NOOWNERZORDER | SWP_NOSENDCHANGING
_layers = weakref.WeakSet()


class _WindowPos(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("hwndInsertAfter", wintypes.HWND),
                ("x", ctypes.c_int), ("y", ctypes.c_int), ("cx", ctypes.c_int),
                ("cy", ctypes.c_int), ("flags", wintypes.UINT)]


def _api():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetShellWindow.restype = wintypes.HWND
    user32.GetWindow.restype = wintypes.HWND
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.FindWindowExW.restype = wintypes.HWND
    user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, wintypes.UINT]
    return user32


def _process(user32, hwnd) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _desktop_host(user32) -> int | None:
    shell = user32.GetShellWindow()
    if not shell:
        return None
    if user32.FindWindowExW(shell, None, "SHELLDLL_DefView", None):
        return shell
    # Older Explorer versions put the icon view in a sibling WorkerW.
    shell_pid = _process(user32, shell)
    worker = None
    while worker := user32.FindWindowExW(None, worker, "WorkerW", None):
        if (user32.IsWindowVisible(worker) and _process(user32, worker) == shell_pid
                and user32.FindWindowExW(worker, None, "SHELLDLL_DefView", None)):
            return worker
    return None


def _anchor(user32, hwnd: int, host: int | None) -> int:
    if not host or not user32.IsWindow(host):
        return HWND_BOTTOM
    previous = user32.GetWindow(host, GW_HWNDPREV)
    desktop_windows = {helper.hwnd for helper in _layers
                       if helper.layer == "desktop" and helper.widget.isVisible()}
    while previous in desktop_windows or previous == hwnd:
        if previous == hwnd:
            return user32.GetWindow(hwnd, GW_HWNDPREV) or HWND_TOP
        previous = user32.GetWindow(previous, GW_HWNDPREV)
    return previous or HWND_TOP


class _Filter(QAbstractNativeEventFilter):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def nativeEventFilter(self, event_type, message):  # noqa: N802
        owner = self.owner
        if owner is not None and owner.layer == "desktop" and owner.hwnd:
            msg = wintypes.MSG.from_address(int(message))
            if msg.hWnd == owner.hwnd and msg.message == WM_WINDOWPOSCHANGING and msg.lParam:
                position = _WindowPos.from_address(msg.lParam)
                if not position.flags & SWP_NOZORDER:
                    position.hwndInsertAfter = _anchor(owner.user32, owner.hwnd, owner.host)
                    position.flags |= SWP_NOACTIVATE | SWP_NOOWNERZORDER
        return False, 0


class _Layer(QObject):
    def __init__(self, widget, user32):
        super().__init__(widget)
        self.widget = widget
        self.user32 = user32
        self.layer = "normal"
        self.hwnd = 0
        self.host = None
        self._applying = False
        self.filter = _Filter(self)
        QApplication.instance().installNativeEventFilter(self.filter)
        widget.installEventFilter(self)
        widget.destroyed.connect(self._dispose)
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self._follow_desktop)
        _layers.add(self)

    def _dispose(self, *_args) -> None:
        _layers.discard(self)
        app = QApplication.instance()
        if app is not None:
            app.removeNativeEventFilter(self.filter)
        self.timer.stop()
        self.filter.owner = None

    def eventFilter(self, watched, event):  # noqa: N802
        if event.type() == QEvent.Hide:
            self.timer.stop()
        elif event.type() in (QEvent.Show, QEvent.WinIdChange) and watched.isVisible():
            self.apply(self.layer)
        return False

    def _position(self, after: int) -> bool:
        return bool(self.user32.SetWindowPos(self.hwnd, after, 0, 0, 0, 0, _FLAGS))

    def _follow_desktop(self) -> bool:
        self.host = _desktop_host(self.user32)
        applied = self._position(_anchor(self.user32, self.hwnd, self.host))
        if not applied:
            self._position(HWND_BOTTOM)
        return bool(self.host) and applied

    def apply(self, layer: str) -> bool:
        if self._applying:
            return False
        self._applying = True
        try:
            self.timer.stop()
            self.hwnd = int(self.widget.winId())
            self.layer = layer
            if layer == "desktop":
                self._position(HWND_NOTOPMOST)
                applied = self._follow_desktop()
                if self.widget.isVisible():
                    self.timer.start()
                return applied
            self.host = None
            return self._position(HWND_TOPMOST if layer == "top" else HWND_NOTOPMOST)
        finally:
            self._applying = False


def apply_layer(widget, layer: str = "desktop") -> bool:
    """Apply desktop/normal/top after show; False means the native layer was unavailable."""
    if layer not in ("desktop", "normal", "top"):
        raise ValueError(f"Unknown widget layer: {layer!r}")
    if sys.platform != "win32" or QApplication.platformName() != "windows":
        return False
    helper = getattr(widget, "_desktop_layer", None)
    if helper is None:
        helper = widget._desktop_layer = _Layer(widget, _api())
    return helper.apply(layer)
