"""Starting the background tracker with Windows.

The tracker is only useful while it is running, which is most of the time the
toolkit window is *not* open — so it needs to come up on its own at logon.

Two mechanisms, in order of preference:

* a **scheduled task** triggered at logon with a delay. Explorer launches Run
  entries during shell startup, when the notification area may not exist yet and
  a tray icon has nowhere to go; a delayed task starts after the desktop has
  settled and does not depend on Explorer processing the Run key at all.
* the **Run key** (HKCU, no admin rights, nothing system-wide) as a fallback for
  machines where creating a task is refused.

Exactly one of them is installed at a time, so the tracker never starts twice.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "WallpaperEngineToolkitTracker"
TASK_NAME = "WallpaperEngineToolkitTracker"

# What this app was called before it was renamed. An installation from then
# still has its logon task — or its Run entry — registered under the old
# name, and that entry keeps working: it points at a path that still exists
# and starts a tracker every morning. What it no longer does is answer to
# this module, so the Tracker tab would report autostart as off while the
# tracker demonstrably starts, and turning it "on" would install a second
# one beside the first. Hence :func:`migrate_legacy`, which is run once at
# start-up and is a no-op on every machine that never had the old name.
LEGACY_VALUE_NAME = "WallpaperSuiteTracker"
LEGACY_TASK_NAME = "WallpaperSuiteTracker"

# Long enough for the shell to finish bringing up the notification area.
LOGON_DELAY_SECONDS = 30

# Keep schtasks from flashing a console window on a windowed build.
_NO_WINDOW = 0x08000000


def target() -> tuple[Path, str]:
    """(program, arguments) that start the tray tracker on this machine."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable), "--tracker"
    script = Path(__file__).resolve().parent.parent / "run_app.py"
    exe = Path(sys.executable)
    # pythonw.exe keeps a console window from flashing at logon.
    windowed = exe.with_name("pythonw.exe")
    if windowed.exists():
        exe = windowed
    return exe, f'"{script}" --tracker'


def command() -> str:
    """The whole thing as one command line, for the Run key."""
    program, arguments = target()
    return f'"{program}" {arguments}'.strip()


# ---- scheduled task -------------------------------------------------------

def _schtasks(*args: str) -> tuple[int, str]:
    """Run schtasks and return (exit code, output).

    Output is captured as bytes and decoded leniently on purpose: schtasks
    speaks the console's OEM code page, which is not the one Python would pick
    for text mode, and on a localised Windows that decode throws.
    """
    try:
        done = subprocess.run(["schtasks", *args], capture_output=True,
                              creationflags=_NO_WINDOW)
    except OSError as e:
        return 1, str(e)
    output = (done.stdout or b"") + (done.stderr or b"")
    for codec in ("oem", "utf-8"):
        try:
            return done.returncode, output.decode(codec, errors="replace")
        except LookupError:
            continue
    return done.returncode, repr(output)


def _schtasks_raw(*args: str) -> bytes:
    """schtasks output as bytes, for the one call whose output is a document."""
    try:
        done = subprocess.run(["schtasks", *args], capture_output=True,
                              creationflags=_NO_WINDOW)
    except OSError:
        return b""
    return done.stdout or b"" if done.returncode == 0 else b""


def _decode_xml(raw: bytes) -> str:
    """Decode a task definition. schtasks emits UTF-16, sometimes with a BOM."""
    for codec in ("utf-16", "utf-16-le", "utf-8-sig", "utf-8"):
        try:
            text = raw.decode(codec)
        except (UnicodeDecodeError, LookupError):
            continue
        if "<Task" in text:
            return text
    return ""


def export_task(name: str) -> str:
    """A registered task's whole definition as XML, or "" if there is none."""
    return _decode_xml(_schtasks_raw("/query", "/tn", name, "/xml", "ONE"))


def task_exists(name: str = TASK_NAME) -> bool:
    return _schtasks("/query", "/tn", name)[0] == 0


def _current_user() -> str:
    domain = os.environ.get("USERDOMAIN") or os.environ.get("COMPUTERNAME") or ""
    user = os.environ.get("USERNAME") or ""
    return f"{domain}\\{user}" if domain else user


def _escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _task_xml() -> str:
    """The task definition.

    Written as XML rather than passed to `schtasks /tr`, which cannot be given a
    program path and arguments that contain quotes without ambiguity. It also
    lets the settings a long-lived tray process needs be stated outright — above
    all no execution time limit, since the default kills a running task after
    three days.
    """
    program, arguments = target()
    user = _escape(_current_user())
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Wallpaper Engine Toolkit playlist tracker (tray).</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{user}</UserId>
      <Delay>PT{LOGON_DELAY_SECONDS}S</Delay>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{user}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{_escape(str(program))}</Command>
      <Arguments>{_escape(arguments)}</Arguments>
    </Exec>
  </Actions>
</Task>
"""


def create_task() -> tuple[bool, str]:
    """Register the logon task. Returns (worked, whatever schtasks said)."""
    handle, path = tempfile.mkstemp(suffix=".xml", prefix="wallpaper_tracker_task_")
    os.close(handle)
    try:
        # schtasks reads the file as UTF-16 when the BOM says so.
        Path(path).write_text(_task_xml(), encoding="utf-16")
        code, out = _schtasks("/create", "/tn", TASK_NAME, "/xml", path, "/f")
        return code == 0, out.strip()
    except OSError as e:
        return False, str(e)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def delete_task(name: str = TASK_NAME) -> None:
    _schtasks("/delete", "/tn", name, "/f")


# ---- Run key --------------------------------------------------------------

def run_key_set(name: str = VALUE_NAME) -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, name)
        return True
    except (ImportError, OSError):
        return False


def set_run_key() -> None:
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command())


def clear_run_key(name: str = VALUE_NAME) -> None:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, name)
    except (ImportError, OSError):
        pass


# ---- the rename ------------------------------------------------------------

def legacy_present() -> bool:
    """Whether autostart is still registered under the old name."""
    return task_exists(LEGACY_TASK_NAME) or run_key_set(LEGACY_VALUE_NAME)


def migrate_legacy() -> str:
    """Move an autostart entry from the old name to the current one.

    Returns what happened, for a log line: ``""`` when there was nothing to do,
    otherwise a short description. The new entry is installed *before* the old
    one is removed, so a failure half way through leaves the tracker still
    starting at logon rather than silently not.
    """
    had_task = task_exists(LEGACY_TASK_NAME)
    had_run_key = run_key_set(LEGACY_VALUE_NAME)
    if not (had_task or had_run_key):
        return ""
    if task_exists() or run_key_set():
        # Already migrated, and the old entry is a leftover that would start a
        # second tracker. Remove it and say so.
        delete_task(LEGACY_TASK_NAME)
        clear_run_key(LEGACY_VALUE_NAME)
        return "removed a leftover autostart entry under the old name"
    # A rename must not change what gets started. The old entry may well point
    # at a built .exe while this code is running from a checkout, and rebuilding
    # the entry from `target()` would quietly repoint autostart at the source
    # tree. So the existing definition is carried over verbatim wherever it can
    # be read, and only its name — and the stale product name in its
    # description — is changed.
    if had_task:
        moved, how = _rename_task()
    else:
        moved, how = _rename_run_key()
    if not moved:
        return f"could not move the old autostart entry ({how}) — left it alone"
    if had_task:
        delete_task(LEGACY_TASK_NAME)
    if had_run_key:
        clear_run_key(LEGACY_VALUE_NAME)
    return f"autostart renamed to {TASK_NAME} ({how})"


_COMMAND_TAG = re.compile(r"<Command>(.*?)</Command>", re.DOTALL)


def _task_command(definition: str) -> str:
    """The program a task definition runs, unescaped enough to check for."""
    found = _COMMAND_TAG.search(definition)
    if not found:
        return ""
    return (found.group(1).strip()
            .replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">"))


def _rename_task() -> tuple[bool, str]:
    """Re-register the legacy task under the new name, action and all.

    Carrying the definition over verbatim is right only while it still points
    at something. It does not, in one case that is easy to walk into: the old
    entry names an executable built under the old product name, and by the time
    this runs that build has been replaced by one under the new name. Copying
    then produces a correctly-named task that launches nothing. So the program
    is checked first, and a definition that has gone stale is rebuilt from
    scratch instead.
    """
    definition = export_task(LEGACY_TASK_NAME)
    program = _task_command(definition)
    stale = bool(program) and not Path(program).exists()
    if not definition or stale:
        # Unreadable or pointing at a program that is gone — better a rebuilt
        # entry than a faithful copy of a broken one.
        used = enable()
        why = "the old entry pointed at a program that is gone" if stale             else "the old entry could not be read"
        return (task_exists() or run_key_set()), f"rebuilt as a {used} — {why}"
    definition = definition.replace("Wallpaper Suite playlist tracker",
                                    "Wallpaper Engine Toolkit playlist tracker")
    handle, path = tempfile.mkstemp(suffix=".xml", prefix="wallpaper_tracker_migrate_")
    os.close(handle)
    try:
        Path(path).write_text(definition, encoding="utf-16")
        code, _ = _schtasks("/create", "/tn", TASK_NAME, "/xml", path, "/f")
    except OSError as err:
        return False, str(err)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    if code != 0 or not task_exists():
        return False, "schtasks refused the copied definition"
    return True, "scheduled task, unchanged"


def _rename_run_key() -> tuple[bool, str]:
    """Copy the legacy Run entry to the new name, command line and all."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            existing, kind = winreg.QueryValueEx(key, LEGACY_VALUE_NAME)
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, kind, existing)
    except (ImportError, OSError) as err:
        return False, str(err)
    return run_key_set(), "Run key, unchanged"


# ---- what the UI calls ----------------------------------------------------

def is_enabled() -> bool:
    return task_exists() or run_key_set()


def method() -> str:
    """Which mechanism is currently installed, for showing in the UI."""
    if task_exists():
        return f"scheduled task, {LOGON_DELAY_SECONDS}s after logon"
    if run_key_set():
        return "Run key"
    return "off"


def enable() -> str:
    """Install autostart, preferring the scheduled task. Returns what was used."""
    if create_task()[0]:
        clear_run_key()          # never both
        return "task"
    set_run_key()
    return "run key"


def disable() -> None:
    delete_task()
    clear_run_key()
    # Off means off, including anything left under the old name.
    delete_task(LEGACY_TASK_NAME)
    clear_run_key(LEGACY_VALUE_NAME)


def set_enabled(on: bool) -> None:
    if on:
        enable()
    else:
        disable()
