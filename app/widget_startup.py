"""The desktop widget's own Windows startup choice, separate from the tracker."""
from __future__ import annotations

import sys

from . import autostart

VALUE_NAME = "WallpaperEngineToolkitWidget"


def command() -> str:
    program, arguments = autostart.target()
    return f'"{program}" {arguments.removesuffix("--tracker")}--widget-startup'


def enabled() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, autostart.RUN_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, VALUE_NAME)
        return bool(value)
    except (ImportError, FileNotFoundError):
        return False


def set_enabled(on: bool) -> None:
    import winreg
    if on:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, autostart.RUN_KEY, 0,
                               winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command())
    else:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, autostart.RUN_KEY, 0,
                                winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, VALUE_NAME)
        except FileNotFoundError:
            pass


def release(exe: str | None = None, *, run_value=None, clear=None) -> str:
    """An uninstalled copy removes only the startup entry pointing to itself."""
    exe = exe or str(autostart.target()[0])
    run_value = run_value or autostart.run_key_value
    clear = clear or (lambda _name: set_enabled(False))
    line = run_value(VALUE_NAME)
    if not line:
        return "widget autostart: off, nothing to remove"
    program = autostart.program_of(line)
    owns_source = (not getattr(sys, "frozen", False)
                   and autostart._same_program(exe, str(autostart.target()[0])))
    if (not autostart._same_program(program, exe)
            or (owns_source and line.casefold() != command().casefold())):
        return f"widget autostart: left alone, as it starts another copy ({program})"
    clear(VALUE_NAME)
    return f"widget autostart: removed the Run entry {VALUE_NAME}"
