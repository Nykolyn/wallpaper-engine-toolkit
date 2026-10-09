"""Whether this copy is the one the installer put on the machine.

The installer (``installer/WallpaperEngineToolkit.iss``) installs for the
current user only, and Windows keeps its record of that install — the entry in
Settings > Apps — under the installer's AppId in HKCU. Its ``InstallLocation``
is the folder the installed exe is in.

A copy built from source with ``build.cmd`` can sit beside an installed one.
Both use the same data folder (see ``data_location``), and they must not take
the Start-menu shortcut from each other every time one of them starts — so the
shortcut follows the installed copy whenever there is one (``app_identity``).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Stable for good, like app_identity.APP_ID: the installer is registered under
# it, and an update finds the copy it replaces by it. installer/*.iss has the
# same value, with the doubled brace Inno Setup wants.
INSTALLER_APP_ID = "{8E307879-3279-4E3D-9EA0-D1BB3A3D456B}"
UNINSTALL_KEY = ("Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\"
                 f"{INSTALLER_APP_ID}_is1")
EXE_NAME = "WallpaperEngineToolkit.exe"


def installed_exe() -> Path | None:
    """The installed copy's exe, if the installer has put one on this machine."""
    if sys.platform != "win32":
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            location, _ = winreg.QueryValueEx(key, "InstallLocation")
    except (ImportError, OSError):
        return None
    if not location:
        return None
    exe = Path(location) / EXE_NAME
    return exe if exe.is_file() else None


def same_file(a: str | os.PathLike, b: str | os.PathLike) -> bool:
    """Whether two paths name the same file, as Windows compares them."""
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def is_installed_copy() -> bool:
    """Whether the running exe is the installed copy."""
    exe = installed_exe()
    return (exe is not None and bool(getattr(sys, "frozen", False))
            and same_file(exe, sys.executable))
