"""Install, update, uninstall and reinstall the built installer, checking the data
at every step. CI runs it on a fresh Windows (.github/workflows/tests.yml):

    python tools\\check_installer.py [dist\\installer\\WallpaperEngineToolkit-Setup-X.Y.Z.exe]

What it walks through is what a person does, with what can go wrong for their
data made to happen:

1. **First install, from an earlier copy.** A made-up checkout's ``data\\`` is
   named with ``/EarlierCopy``; its files must arrive in the data folder, and
   the checkout must be left exactly as it was.
2. **Update over a running copy.** The window and the tray tracker are started,
   and the installer is run again: it must ask them to quit, copy the data
   aside first, and leave every file of it in place.
3. **Uninstall.** The program, its Start-menu entry and its logon task go; the
   data stays, every file of it.
4. **Install again.** The data is picked up, untouched, and copied aside again.

It installs programs, a logon task and a Start-menu entry, so it runs only on
CI, or with ``--yes-on-this-machine``; and it refuses outright where the data
folder already exists, so a real one is never touched.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.engines import engine_control

APP = "WallpaperEngineToolkit"
LOCAL = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
DATA = LOCAL / APP
INSTALL_DIR = LOCAL / "Programs" / APP
EXE = INSTALL_DIR / f"{APP}.exe"
SHORTCUT = (Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu"
            / "Programs" / "Toolkit.lnk")
TASK = "WallpaperEngineToolkitTracker"
UNINSTALL_KEY = ("Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\"
                 "{8E307879-3279-4E3D-9EA0-D1BB3A3D456B}_is1")
WINDOW_MUTEX = "Local\\WallpaperEngineToolkitWindow"
TRAY_MUTEX = "Local\\WallpaperEngineToolkitTracker"

results: list[bool] = []


def check(label: str, condition: bool) -> None:
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label, flush=True)


def mutex_held(name: str) -> bool:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenMutexW.restype = wintypes.HANDLE
    kernel32.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenMutexW(0x00100000, False, name)
    if not handle:
        return False
    kernel32.CloseHandle(handle)
    return True


def program_running() -> bool:
    """Include the bootloader and copies still starting, before they take a mutex."""
    return any(name.casefold() == f"{APP}.exe".casefold()
               for _, name in engine_control.processes())


def wait_for(condition, seconds: float) -> bool:
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        if condition():
            return True
        time.sleep(0.5)
    return condition()


def digests(folder: Path) -> dict[str, str]:
    return {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob("*")) if p.is_file()}


def task_program() -> str:
    """What the logon task starts, read the way the app reads it: schtasks hands
    its XML over in UTF-16 or not, with a BOM or not."""
    sys.path.insert(0, str(ROOT))
    from app.autostart import _task_command, export_task
    return _task_command(export_task(TASK))


def registered() -> bool:
    import winreg
    try:
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY).Close()
        return True
    except OSError:
        return False


def install(setup: Path, log: Path, *extra: str) -> int:
    print(f"-- {setup.name} {' '.join(extra)}", flush=True)
    done = subprocess.run([str(setup), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                           f"/LOG={log}", *extra], timeout=900)
    return done.returncode


def uninstall(log: Path) -> bool:
    """Run the uninstaller and wait for it: it hands itself over to a copy in
    %TEMP%. That copy deletes the original exe after its first phase exits."""
    uninstaller = INSTALL_DIR / "unins000.exe"
    print(f"-- {uninstaller}", flush=True)
    if not uninstaller.is_file():
        print("   (not installed)")
        return False
    subprocess.run([str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                    f"/LOG={log}"], timeout=300)
    return wait_for(lambda: not registered() and not uninstaller.exists(), 300)


def show_log(log: Path) -> None:
    if log.exists():
        print(f"---- {log.name} (the app's steps) ----")
        for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
            if any(word in line for word in ("--quit", "--adopt-data", "--backup-data",
                                             "--autostart", "still running", "Prepare",
                                             "rror", "Starting the updated window")):
                print("   ", line)


def main(argv: list[str]) -> int:
    if not os.environ.get("CI") and "--yes-on-this-machine" not in argv:
        print("This installs the toolkit, a logon task and a Start-menu entry. It runs on CI,\n"
              "or with --yes-on-this-machine.")
        return 2
    if DATA.exists():
        print(f"{DATA} exists. This check never runs where there is real data. Nothing was done.")
        return 2
    given = [a for a in argv if not a.startswith("--")]
    setups = [Path(given[0])] if given else sorted((ROOT / "dist" / "installer").glob(f"{APP}-Setup-*.exe"))
    if len(setups) != 1 or not setups[0].is_file():
        print(f"expected one installer, found: {setups}")
        return 2
    setup = setups[0].resolve()
    work = Path(tempfile.mkdtemp(prefix="check_installer_"))

    # An earlier copy, run from source: its data beside run_app.py.
    earlier = work / "wallpaper-engine-toolkit"
    (earlier / "data" / "thumbs").mkdir(parents=True)
    (earlier / "run_app.py").write_text("# a checkout\n", encoding="utf-8")
    (earlier / "data" / "secrets.json").write_text("{}", encoding="utf-8")
    (earlier / "data" / "notes-from-the-check.txt").write_text("keep me\n", encoding="utf-8")
    (earlier / "data" / "keepsakes").mkdir()
    (earlier / "data" / "keepsakes" / "blob.bin").write_bytes(os.urandom(20000))
    # A preview: copied in like the rest, but the app's own cache to manage
    # after that, and left out of the copies taken before an update.
    (earlier / "data" / "thumbs" / "1234567890.img").write_bytes(os.urandom(20000))
    earlier_files = digests(earlier / "data")
    # What must survive every step, byte for byte.
    kept = ["secrets.json", "notes-from-the-check.txt", "keepsakes/blob.bin"]

    def data_intact(label: str) -> None:
        now = digests(DATA) if DATA.exists() else {}
        check(f"{label}: every file of the data is there, unchanged",
              all(now.get(name) == earlier_files[name] for name in kept))

    # 1. First install, from the earlier copy.
    code = install(setup, work / "1-install.log", f"/EarlierCopy={earlier}", "/TASKS=autostart")
    show_log(work / "1-install.log")
    check("the first install finishes", code == 0)
    check("the program is in the user's own Programs folder", EXE.is_file())
    check("Windows lists it, for this user", registered())
    check("the Start-menu entry is there", SHORTCUT.is_file())
    check("the logon task starts the installed copy",
          os.path.normcase(task_program()) == os.path.normcase(str(EXE)))
    marker = json.loads((DATA / "data-folder.json").read_text(encoding="utf-8")) \
        if (DATA / "data-folder.json").exists() else {}
    check("the earlier copy's data was copied in, and the marker says from where",
          marker.get("copied_from") == str(earlier / "data"))
    data_intact("after the first install")
    check("the preview came over too", (DATA / "thumbs" / "1234567890.img").is_file())
    check("the earlier copy is exactly as it was", digests(earlier / "data") == earlier_files)

    # 2. Update over a running copy.
    (DATA / "suite.json").write_text(json.dumps({"updates": {"check_on_startup": False},
                                                "widget": {"enabled": True},
                                                "app": {"autostart_migrated": True}}), encoding="utf-8")
    import winreg
    from app import autostart, widget_startup
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, autostart.RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, widget_startup.VALUE_NAME, 0, winreg.REG_SZ, f'"{EXE}" --widget-startup')
    check("the widget has its own startup entry", widget_startup.enabled())
    environment = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    if not wait_for(lambda: mutex_held(TRAY_MUTEX), 15):
        subprocess.Popen([str(EXE), "--tracker"], env=environment, cwd=str(INSTALL_DIR))
    subprocess.Popen([str(EXE)], env=environment, cwd=str(INSTALL_DIR))
    check("the tray tracker runs", wait_for(lambda: mutex_held(TRAY_MUTEX), 60))
    check("and the window", wait_for(lambda: mutex_held(WINDOW_MUTEX), 60))
    widget_shown = wait_for(
        lambda: (DATA / "tracker.log").is_file() and "widget shown" in
        (DATA / "tracker.log").read_text(encoding="utf-8", errors="replace"), 60)
    check("the installed widget is shown by the running tracker", widget_shown)
    if not widget_shown:
        print("---- widget startup diagnostics ----", flush=True)
        for name in ("tracker.log", "window-hangs.log", "tracker-hangs.log"):
            path = DATA / name
            if path.is_file():
                print(f"{name}:\n{path.read_text(encoding='utf-8', errors='replace')[-16000:]}", flush=True)
        print([(pid, engine_control.process_arguments(pid)) for pid, name in engine_control.processes()
               if name.casefold() == f"{APP}.exe".casefold()], flush=True)
    time.sleep(5)                         # let the window make its pages
    code = install(setup, work / "2-update.log", "/UpdateFromApp=1")
    show_log(work / "2-update.log")
    check("the update finishes over a running copy", code == 0)
    check("updating keeps the widget visibility preference",
          json.loads((DATA / "suite.json").read_text(encoding="utf-8")).get("widget", {}).get("enabled") is True)
    check("in-app setup opens the updated window again", wait_for(lambda: mutex_held(WINDOW_MUTEX), 60))
    check("in-app setup starts the tracker again", wait_for(lambda: mutex_held(TRAY_MUTEX), 60))
    tracker_log = (DATA / "tracker.log").read_text(encoding="utf-8", errors="replace") \
        if (DATA / "tracker.log").exists() else ""
    check("the tracker was asked to quit, and said so", "asked to quit" in tracker_log)
    if "asked to quit" not in tracker_log:
        print("---- tracker.log (shutdown trace missing) ----", flush=True)
        print(tracker_log or "(no tracker log)", flush=True)
    backups = sorted((DATA / "update_backup").glob("*")) if (DATA / "update_backup").exists() else []
    check("the data was copied aside before the new version ran", len(backups) == 1)
    if backups:
        manifest = json.loads((backups[0] / "manifest.json").read_text(encoding="utf-8"))
        listed = {f["file"] for f in manifest["files"]}
        check("the copy holds what cannot be had again, and leaves the previews out",
              set(kept) <= listed
              and not any(name.startswith("thumbs/") for name in listed))
    data_intact("after the update")

    # A file held without sharing makes the data backup fail, after file replacement.
    # Setup must report that failure and must not start a version with unprotected data.
    blocked = DATA / "locked-during-backup.txt"
    blocked.write_text("keep me too", encoding="utf-8")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                   wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.CreateFileW(str(blocked), 0x80000000, 0, None, 3, 0x80, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        code = install(setup, work / "2-backup-failed.log", "/UpdateFromApp=1")
    finally:
        kernel32.CloseHandle(handle)
    show_log(work / "2-backup-failed.log")
    check("backup failure returns the custom setup error", code == 20)
    stopped = not program_running()
    check("failed in-app setup does not launch the window or tracker", stopped)
    if not stopped:
        print("---- processes after failed setup ----", flush=True)
        for pid, name in engine_control.processes():
            if name.casefold() == f"{APP}.exe".casefold():
                print(pid, engine_control.process_arguments(pid), flush=True)
    check("a failed backup leaves the original file intact", blocked.read_text(encoding="utf-8") == "keep me too")
    blocked.unlink()
    data_intact("after a failed update backup")
    code = install(setup, work / "2-retry.log", "/UpdateFromApp=1")
    check("retry installs successfully and opens the window", code == 0
          and wait_for(lambda: mutex_held(WINDOW_MUTEX), 60))
    data_intact("after retrying")

    # 3. Uninstall.
    gone = uninstall(work / "3-uninstall.log")
    show_log(work / "3-uninstall.log")
    check("the uninstall finishes", gone)
    if not gone:
        print(f"shutdown: window={mutex_held(WINDOW_MUTEX)}, tracker={mutex_held(TRAY_MUTEX)}", flush=True)
        tracker_log = DATA / "tracker.log"
        if tracker_log.is_file():
            print(tracker_log.read_text(encoding="utf-8", errors="replace")[-12000:], flush=True)
    check("nothing of the toolkit runs any more",
          not mutex_held(WINDOW_MUTEX) and not mutex_held(TRAY_MUTEX))
    check("the program is gone", not EXE.exists())
    check("so are its Start-menu entry and its logon task",
          not SHORTCUT.exists() and task_program() == "")
    check("uninstall removes this copy's widget startup entry", not widget_startup.enabled())
    data_intact("after uninstalling")
    check("the copies taken before the update are kept too",
          (DATA / "update_backup").exists() and len(list((DATA / "update_backup").iterdir())) == 2)

    # 4. Install again: the data is picked up, and copied aside first.
    code = install(setup, work / "4-reinstall.log", "/MERGETASKS=!autostart")
    show_log(work / "4-reinstall.log")
    check("installing again finishes", code == 0 and EXE.is_file())
    check("the reinstalled uninstaller is there", (INSTALL_DIR / "unins000.exe").is_file())
    check("without autostart, as asked", task_program() == "")
    data_intact("after installing again")
    check("and the data was copied aside again first",
          len(list((DATA / "update_backup").iterdir())) == 3)

    check("the final uninstall finishes", uninstall(work / "5-uninstall.log"))

    print()
    print("PASSED %d/%d" % (sum(results), len(results)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
