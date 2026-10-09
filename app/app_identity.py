"""Who Windows says the balloons are from: "Toolkit", not WallpaperEngineToolkit.exe.

Windows names a notification's sender from the process's AppUserModelID, and
finds the name and icon for that ID on a Start-menu shortcut that carries it.
Without either it falls back to the executable's file name, which is what the
tray's "Playlist finished" balloon used to say it came from.

So the built exe makes sure a shortcut `Toolkit.lnk` is in the user's Start
menu, pointing at itself and carrying `APP_ID`, and only then sets `APP_ID` as
the process's own; the window and the tray both do, the tray included when the
logon task starts the exe directly. The order matters: an ID that no shortcut
registers can make Windows drop a notification rather than rename it, so a
process that could not make the shortcut keeps the name it had.

Source runs do neither: a Start-menu entry pointing at a checkout's Python is
nobody's idea of an install. Where the installer has put a copy on the machine,
the shortcut is that copy's — the installer makes it — and a build run from
somewhere else takes the ID but leaves the shortcut pointing where it does.

The shortcut is made through the shell's own COM interfaces (IShellLinkW,
IPropertyStore, IPersistFile) with ctypes; nothing else in the app needs
pywin32, and this is not worth adding it for.
"""
from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from pathlib import Path
from typing import Callable

from .branding import DISPLAY_NAME, FULL_NAME

# Stable for good: a new one would orphan the shortcut and the notification
# settings Windows keeps for it.
APP_ID = "Nykolyn.WallpaperEngineToolkit"


def shortcut_path() -> Path | None:
    """`…\\Start Menu\\Programs\\Toolkit.lnk` for this user, or None off Windows."""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / f"{DISPLAY_NAME}.lnk"


def claim(*, frozen: bool, target: str, link: Path | None,
          read: Callable[[Path], tuple[str, str] | None],
          write: Callable[[Path, str], None],
          set_id: Callable[[str], None],
          installed: str | None = None) -> str:
    """Make the shortcut if it is missing or points elsewhere, then take the ID.
    Returns what was done, in words, for data/tracker.log.

    `read(link)` is (target, app id) of an existing shortcut or None, `write`
    makes one, `set_id` sets the process's ID: passed in so this can be checked
    away from Windows. `installed` is the installed copy's exe, if there is one
    (see install_info): a shortcut to it is that copy's, and a build run from
    elsewhere leaves it alone.
    """
    if not frozen:
        return "source run: no Start-menu shortcut, the sender stays as it was"
    if link is None:
        return "no Start menu found: the sender stays as it was"
    try:
        found = read(link) if link.exists() else None
        if found is not None and found[1] == APP_ID and installed \
                and os.path.normcase(found[0]) == os.path.normcase(installed) \
                and os.path.normcase(target) != os.path.normcase(installed):
            set_id(APP_ID)
            return (f"Start-menu shortcut left with the installed copy ({installed}); "
                    f"notifications come from {DISPLAY_NAME}")
        if found is None or os.path.normcase(found[0]) != os.path.normcase(target) \
                or found[1] != APP_ID:
            link.parent.mkdir(parents=True, exist_ok=True)
            write(link, target)
            done = f"Start-menu shortcut {'made' if found is None else 'made again'}: {link}"
        else:
            done = "Start-menu shortcut in place"
    except OSError as err:
        return f"could not make the Start-menu shortcut ({err}): the sender stays as it was"
    set_id(APP_ID)
    return f"{done}; notifications come from {DISPLAY_NAME}"


def claim_for_this_process() -> str:
    """`claim` with the real exe, Start menu and Windows calls."""
    from .install_info import installed_exe
    installed = installed_exe()
    return claim(frozen=bool(getattr(sys, "frozen", False)), target=sys.executable,
                 link=shortcut_path(), read=_read_shortcut, write=_write_shortcut,
                 set_id=_set_process_id, installed=str(installed) if installed else None)


# ---- Windows ------------------------------------------------------------------------

class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    def __init__(self, text: str):
        super().__init__()
        ole32 = ctypes.WinDLL("ole32")
        if ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(self)) != 0:
            raise ValueError(f"not a GUID: {text}")


class _PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", _GUID), ("pid", wintypes.DWORD)]


class _PROPVARIANT(ctypes.Structure):
    # vt, three reserved words, then the value: a string pointer here. The
    # union is 16 bytes on 64-bit Windows, hence the padding.
    _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort),
                ("r3", ctypes.c_ushort), ("value", ctypes.c_void_p), ("pad", ctypes.c_void_p)]


_CLSID_SHELL_LINK = "{00021401-0000-0000-C000-000000000046}"
_IID_SHELL_LINK_W = "{000214F9-0000-0000-C000-000000000046}"
_IID_PROPERTY_STORE = "{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}"
_IID_PERSIST_FILE = "{0000010B-0000-0000-C000-000000000046}"
_PKEY_APP_ID = ("{9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}", 5)
_VT_LPWSTR = 31
_CLSCTX_INPROC_SERVER = 1
_COINIT_APARTMENTTHREADED = 2
_STGM_READ = 0

# Slots in each interface's table of methods (IUnknown takes 0-2).
_QUERY_INTERFACE, _RELEASE = 0, 2
_LINK_GET_PATH, _LINK_SET_DESCRIPTION, _LINK_SET_ICON, _LINK_SET_PATH = 3, 7, 17, 20
_STORE_GET_VALUE, _STORE_SET_VALUE, _STORE_COMMIT = 5, 6, 7
_FILE_LOAD, _FILE_SAVE = 5, 6


def _method(obj: ctypes.c_void_p, slot: int, *argtypes):
    """A COM method of `obj`, by its slot in the object's table of methods."""
    table = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    prototype = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)
    function = prototype(table[slot])
    return lambda *args: function(obj, *args)


def _query(obj: ctypes.c_void_p, iid: str) -> ctypes.c_void_p:
    out = ctypes.c_void_p()
    _method(obj, _QUERY_INTERFACE, ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p))(
        ctypes.byref(_GUID(iid)), ctypes.byref(out))
    return out


def _release(*objs: ctypes.c_void_p) -> None:
    for obj in objs:
        if obj:
            table = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(table[_RELEASE])(obj)


class _Link:
    """A shell link with its property store and file interfaces, released after."""

    def __enter__(self):
        ole32 = ctypes.WinDLL("ole32")
        self._uninit = ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED) in (0, 1)
        self.link = self.store = self.file = ctypes.c_void_p()
        link = ctypes.c_void_p()
        failed = ole32.CoCreateInstance(ctypes.byref(_GUID(_CLSID_SHELL_LINK)), None,
                                        _CLSCTX_INPROC_SERVER,
                                        ctypes.byref(_GUID(_IID_SHELL_LINK_W)), ctypes.byref(link))
        if failed or not link:
            # Calling into a null object would take the process down, not raise.
            self.__exit__(None, None, None)
            raise OSError(f"no shell link object (0x{failed & 0xFFFFFFFF:08X})")
        self.link = link
        try:
            self.store = _query(self.link, _IID_PROPERTY_STORE)
            self.file = _query(self.link, _IID_PERSIST_FILE)
        except OSError:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *exc):
        _release(self.file, self.store, self.link)
        if self._uninit:
            ctypes.WinDLL("ole32").CoUninitialize()


def _key() -> _PROPERTYKEY:
    return _PROPERTYKEY(_GUID(_PKEY_APP_ID[0]), _PKEY_APP_ID[1])


def _read_shortcut(link: Path) -> tuple[str, str] | None:
    """(target, app id) of a shortcut, or None when it cannot be read."""
    try:
        with _Link() as s:
            _method(s.file, _FILE_LOAD, wintypes.LPCWSTR, wintypes.DWORD)(str(link), _STGM_READ)
            buffer = ctypes.create_unicode_buffer(32768)
            _method(s.link, _LINK_GET_PATH, wintypes.LPWSTR, ctypes.c_int, ctypes.c_void_p,
                    wintypes.DWORD)(buffer, len(buffer), None, 0)
            value = _PROPVARIANT()
            key = _key()
            _method(s.store, _STORE_GET_VALUE, ctypes.POINTER(_PROPERTYKEY),
                    ctypes.POINTER(_PROPVARIANT))(ctypes.byref(key), ctypes.byref(value))
            app_id = ctypes.wstring_at(value.value) if value.vt == _VT_LPWSTR and value.value else ""
            ctypes.WinDLL("ole32").PropVariantClear(ctypes.byref(value))
            return buffer.value, app_id
    except (OSError, ValueError):
        return None


def _write_shortcut(link: Path, target: str) -> None:
    """Make (or replace) `link`: `target`, its own icon, FULL_NAME, and APP_ID."""
    try:
        with _Link() as s:
            _method(s.link, _LINK_SET_PATH, wintypes.LPCWSTR)(target)
            _method(s.link, _LINK_SET_ICON, wintypes.LPCWSTR, ctypes.c_int)(target, 0)
            _method(s.link, _LINK_SET_DESCRIPTION, wintypes.LPCWSTR)(FULL_NAME)
            text = ctypes.create_unicode_buffer(APP_ID)
            value = _PROPVARIANT(vt=_VT_LPWSTR, value=ctypes.cast(text, ctypes.c_void_p).value)
            key = _key()
            _method(s.store, _STORE_SET_VALUE, ctypes.POINTER(_PROPERTYKEY),
                    ctypes.POINTER(_PROPVARIANT))(ctypes.byref(key), ctypes.byref(value))
            _method(s.store, _STORE_COMMIT)()
            _method(s.file, _FILE_SAVE, wintypes.LPCWSTR, wintypes.BOOL)(str(link), True)
    except ValueError as err:
        raise OSError(str(err)) from err


def _set_process_id(app_id: str) -> None:
    ctypes.WinDLL("shell32").SetCurrentProcessExplicitAppUserModelID(ctypes.c_wchar_p(app_id))
