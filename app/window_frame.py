"""The window's own title bar, with Windows still doing the window's work.

The design draws a 32 px title bar of its own (gate G1, answered B). Drawing
one is easy; what is hard is keeping everything Windows does for a title bar
it draws itself: dragging, Aero Snap, the snap layouts that open over the
maximise button on Windows 11, resizing from every edge, double-clicking to
maximise, the system menu (Alt+Space), and the minimise and maximise
animations. So the window keeps its caption and resizable frame as far as
Windows knows (`WS_CAPTION | WS_THICKFRAME`) and gives the whole of it to
Qt to draw:

- `WM_NCCALCSIZE` answers "no frame": the client area is the whole window.
- `WM_NCHITTEST` says what is under the pointer: an edge or corner (resize),
  the maximise button (`HTMAXBUTTON`, which is what opens the snap layouts),
  the title bar (`HTCAPTION`: drag, snap, double-click, right-click menu), or
  the client area (everything else, the minimise and close buttons included:
  Qt's own buttons handle those).
- Over the maximise button the pointer belongs to Windows, so its hover and
  press arrive as non-client messages and are handed to the button here, and
  its click maximises. Left to itself, Windows would draw an old-style button
  there on press.
- DWM is asked for a dark window, rounded corners, a border in the title
  bar's colour, and its shadow (the frame extended by a pixel).

Maximised, Windows puts a resizable window's frame off the edges of the
screen. With no frame, that is the window's own content, so the window pads
itself in by exactly that much (`maximised_margins`).

Anything here that fails leaves a working window: without the native part the
title bar moves the window itself (`TitleBar`'s fallback).
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from PySide6.QtCore import QMargins, QPoint

WM_NCCALCSIZE = 0x0083
WM_NCHITTEST = 0x0084
WM_NCMOUSEMOVE = 0x00A0
WM_NCLBUTTONDOWN = 0x00A1
WM_NCLBUTTONUP = 0x00A2
WM_NCLBUTTONDBLCLK = 0x00A3
WM_MOUSEMOVE = 0x0200
WM_NCMOUSELEAVE = 0x02A2

HTCLIENT = 1
HTCAPTION = 2
HTMAXBUTTON = 9
HTLEFT, HTRIGHT, HTTOP, HTTOPLEFT, HTTOPRIGHT = 10, 11, 12, 13, 14
HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT = 15, 16, 17

GWL_STYLE = -16
WS_MAXIMIZEBOX = 0x00010000
WS_MINIMIZEBOX = 0x00020000
WS_THICKFRAME = 0x00040000
WS_SYSMENU = 0x00080000
WS_CAPTION = 0x00C00000
FRAME_STYLE = WS_CAPTION | WS_THICKFRAME | WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX

SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE, SWP_FRAMECHANGED = 0x1, 0x2, 0x4, 0x10, 0x20
TME_LEAVE, TME_NONCLIENT = 0x2, 0x10
MONITOR_DEFAULTTONEAREST = 2

DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_ROUND = 2
DWMWA_BORDER_COLOR = 34


def edge_hit(x: float, y: float, width: float, height: float, border: float) -> int | None:
    """Which edge or corner of a window (x, y) is on, in the window's own
    logical coordinates; None if it is further in than `border`."""
    left, right = x < border, x >= width - border
    top, bottom = y < border, y >= height - border
    if top and left:
        return HTTOPLEFT
    if top and right:
        return HTTOPRIGHT
    if bottom and left:
        return HTBOTTOMLEFT
    if bottom and right:
        return HTBOTTOMRIGHT
    if left:
        return HTLEFT
    if right:
        return HTRIGHT
    if top:
        return HTTOP
    if bottom:
        return HTBOTTOM
    return None


def hit_test(window, title_bar, x: float, y: float, border: int) -> int:
    """What Windows should take a point of the window to be (logical, window
    coordinates): an edge to resize by, the maximise button, the caption, or
    the client area."""
    if not window.isMaximized() and not window.isFullScreen():
        edge = edge_hit(x, y, window.width(), window.height(), border)
        if edge is not None:
            return edge
    if title_bar is None or not title_bar.isVisible():
        return HTCLIENT
    local = title_bar.mapFrom(window, QPoint(int(x), int(y)))
    if not title_bar.rect().contains(local):
        return HTCLIENT
    area = title_bar.area(local)
    if area == "maximise":
        return HTMAXBUTTON
    if area == "button":
        return HTCLIENT
    return HTCAPTION


class _Margins(ctypes.Structure):
    _fields_ = [("left", ctypes.c_int), ("right", ctypes.c_int),
                ("top", ctypes.c_int), ("bottom", ctypes.c_int)]


class _TrackMouse(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("hwndTrack", wintypes.HWND), ("dwHoverTime", wintypes.DWORD)]


class _MonitorInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


def _api():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    dwm = ctypes.WinDLL("dwmapi")
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.TrackMouseEvent.argtypes = [ctypes.POINTER(_TrackMouse)]
    user32.MonitorFromWindow.restype = wintypes.HANDLE
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MonitorInfo)]
    dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p,
                                          wintypes.DWORD]
    dwm.DwmExtendFrameIntoClientArea.argtypes = [wintypes.HWND, ctypes.POINTER(_Margins)]
    return user32, dwm


def _colorref(colour) -> int:
    return colour.red() | (colour.green() << 8) | (colour.blue() << 16)


class NativeFrame:
    """The Windows side of a window with its own title bar. Made by the
    window once its native handle exists; it then answers `handle()` from the
    window's `nativeEvent`."""

    def __init__(self, window, title_bar, border: int):
        self.window = window
        self.title_bar = title_bar
        self.border = border
        self.hwnd = int(window.winId())
        self._user32, self._dwm = _api()
        self._install()

    @classmethod
    def attach(cls, window, title_bar, border: int) -> "NativeFrame | None":
        """The native frame, or None where there is none to have (another
        platform, offscreen, or Windows refusing)."""
        from PySide6.QtGui import QGuiApplication
        if sys.platform != "win32" or QGuiApplication.platformName() != "windows":
            return None
        try:
            return cls(window, title_bar, border)
        except (OSError, AttributeError):
            return None

    def _install(self) -> None:
        from . import theme
        style = self._user32.GetWindowLongPtrW(self.hwnd, GWL_STYLE)
        self._user32.SetWindowLongPtrW(self.hwnd, GWL_STYLE, style | FRAME_STYLE)
        for attribute, value in ((DWMWA_USE_IMMERSIVE_DARK_MODE, 1),
                                 (DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND),
                                 (DWMWA_BORDER_COLOR, _colorref(theme.color("bg.solid")))):
            data = ctypes.c_int(value)
            # Older Windows knows fewer of these; each may fail on its own.
            self._dwm.DwmSetWindowAttribute(self.hwnd, attribute, ctypes.byref(data),
                                            ctypes.sizeof(data))
        margins = _Margins(0, 0, 1, 0)      # a pixel of frame: DWM's shadow
        self._dwm.DwmExtendFrameIntoClientArea(self.hwnd, ctypes.byref(margins))
        # Have Windows take the new style and ask WM_NCCALCSIZE again.
        self._user32.SetWindowPos(self.hwnd, None, 0, 0, 0, 0,
                                  SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
                                  | SWP_FRAMECHANGED)

    # -- messages

    def handle(self, message: int) -> int | None:
        """The answer to a native message, or None to let Qt and Windows have it."""
        msg = wintypes.MSG.from_address(message)
        kind = msg.message
        if kind == WM_NCCALCSIZE:
            return 0                        # no frame: the client is the whole window
        if kind == WM_NCHITTEST:
            x, y = self._logical(msg.lParam)
            return hit_test(self.window, self.title_bar, x, y, self.border)
        button = self.title_bar.maximise_button
        if kind == WM_NCMOUSEMOVE:
            over = msg.wParam == HTMAXBUTTON
            button.set_native(hover=over)
            if over:
                self._track_leave()
            return None
        if kind in (WM_NCMOUSELEAVE, WM_MOUSEMOVE):
            if button.native_hover():
                button.set_native(hover=False, down=False)
            return None
        if msg.wParam == HTMAXBUTTON:
            if kind == WM_NCLBUTTONDOWN:
                button.set_native(down=True)
                return 0
            if kind == WM_NCLBUTTONUP:
                pressed = button.is_down()
                button.set_native(down=False)
                if pressed:
                    self.title_bar.toggle_maximised()
                return 0
            if kind == WM_NCLBUTTONDBLCLK:
                return 0
        return None

    def _logical(self, lparam: int) -> tuple[float, float]:
        """The point in a hit-test message, in the window's logical coordinates."""
        x = ctypes.c_short(lparam & 0xFFFF).value
        y = ctypes.c_short((lparam >> 16) & 0xFFFF).value
        rect = wintypes.RECT()
        self._user32.GetWindowRect(self.hwnd, ctypes.byref(rect))
        scale = self.window.devicePixelRatioF() or 1.0
        return (x - rect.left) / scale, (y - rect.top) / scale

    def _track_leave(self) -> None:
        track = _TrackMouse(ctypes.sizeof(_TrackMouse), TME_LEAVE | TME_NONCLIENT, self.hwnd, 0)
        self._user32.TrackMouseEvent(ctypes.byref(track))

    # -- maximised

    def maximised_margins(self) -> QMargins:
        """How far the maximised window reaches past the screen's work area on
        each side, in logical pixels: what the window must pad itself by."""
        if not self.window.isMaximized():
            return QMargins()
        rect = wintypes.RECT()
        self._user32.GetWindowRect(self.hwnd, ctypes.byref(rect))
        info = _MonitorInfo()
        info.cbSize = ctypes.sizeof(_MonitorInfo)
        monitor = self._user32.MonitorFromWindow(self.hwnd, MONITOR_DEFAULTTONEAREST)
        if not monitor or not self._user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return QMargins()
        work = info.rcWork
        scale = self.window.devicePixelRatioF() or 1.0

        def over(value: int) -> int:
            return max(0, round(value / scale))
        return QMargins(over(work.left - rect.left), over(work.top - rect.top),
                        over(rect.right - work.right), over(rect.bottom - work.bottom))
