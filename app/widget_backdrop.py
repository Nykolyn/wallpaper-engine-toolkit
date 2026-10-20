"""Windows acrylic for the widget's own HWND; Qt remains the portable fallback."""
from __future__ import annotations

import ctypes
import sys

from PySide6.QtWidgets import QApplication


class _Accent(ctypes.Structure):
    _fields_ = [("state", ctypes.c_int32), ("flags", ctypes.c_uint32),
                ("color", ctypes.c_uint32), ("animation", ctypes.c_int32)]


class _Composition(ctypes.Structure):
    _fields_ = [("attribute", ctypes.c_int32), ("data", ctypes.c_void_p),
                ("size", ctypes.c_size_t)]


def _apply(hwnd: int) -> bool:
    try:
        # shortcut: this accent API is undocumented; replace if layered-window support changes.
        compose = ctypes.WinDLL("user32", use_last_error=True).SetWindowCompositionAttribute
        compose.argtypes = [ctypes.c_void_p, ctypes.POINTER(_Composition)]
        compose.restype = ctypes.c_int32
        accent = _Accent(4, 0, 0x30281E18, 0)  # fixed smoke tint, ABGR
        data = _Composition(19, ctypes.addressof(accent), ctypes.sizeof(accent))
        return bool(compose(ctypes.c_void_p(hwnd), ctypes.byref(data)))
    except (AttributeError, OSError):
        return False


def apply_backdrop(window) -> bool:
    if sys.platform != "win32" or QApplication.platformName() != "windows":
        return False
    if sys.getwindowsversion().build < 17763:
        return False
    return _apply(int(window.winId()))
