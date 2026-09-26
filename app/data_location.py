"""Where the toolkit keeps what it has learned and what you told it.

A built toolkit keeps its data in ``%LOCALAPPDATA%\\WallpaperEngineToolkit``,
never beside the exe. The program folder is what a build replaces, an update
overwrites and an uninstall deletes, and until 3.0.0 the data lived inside it,
in ``data\\``. On 2026-09-20 a build run the wrong way emptied that folder; the
Steam key was noticed and put back, the Rotator's history was not, and the next
rotation six days later started a new one. Out here, nothing that happens to
the program folder can reach it.

A source run keeps using ``data\\`` beside ``run_app.py``, so working on the
code never touches the data of the copy you use. ``WALLPAPER_TOOLKIT_DATA``
names any other folder, for a test build that must not see the real one.

**The move** is done once, by whichever process asks for the folder first — the
tray tracker at logon, as a rule. Everything in the old ``data\\`` is copied to
a staging folder, every file is compared with its original byte for byte (size
and SHA-256), and only then does the staging folder become the data folder and
the old one go to the Recycle Bin. If any step fails, nothing is deleted and
the old folder stays in use; the next start tries again. A named mutex keeps
the tracker and the window from moving it at the same time.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import shutil
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

APP_FOLDER = "WallpaperEngineToolkit"
OVERRIDE_ENV = "WALLPAPER_TOOLKIT_DATA"
# Written into the data folder when it becomes the one in use, moved or new.
# Its presence is what "this folder is the data" means; a folder without it is
# not trusted with anything.
MARKER = "data-folder.json"
STAGING_SUFFIX = ".moving"
_MUTEX_NAME = "Local\\WallpaperEngineToolkit-data-folder"


@dataclass
class Resolved:
    folder: Path
    # One line on how it was decided, for the selfcheck: where the data is, and
    # whether anything was moved or left behind.
    report: str


_resolved: Resolved | None = None


def data_dir() -> Path:
    """The folder every data file lives in. Created if missing."""
    return resolve().folder


def resolve() -> Resolved:
    global _resolved
    if _resolved is None:
        _resolved = _decide()
        _resolved.folder.mkdir(parents=True, exist_ok=True)
    return _resolved


def _decide() -> Resolved:
    override = os.environ.get(OVERRIDE_ENV, "").strip()
    if override:
        return Resolved(Path(override), f"{override} ({OVERRIDE_ENV} names it)")
    if not getattr(sys, "frozen", False):
        source = Path(__file__).resolve().parent.parent / "data"
        return Resolved(source, f"{source} (a source run keeps its data beside run_app.py)")
    with _exclusive():
        return settle(legacy_dir(), installed_dir())


def installed_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    return (Path(base) if base else Path.home() / "AppData" / "Local") / APP_FOLDER


def legacy_dir() -> Path:
    """Where a build before 3.0.0 kept its data: ``data\\`` beside the exe."""
    return Path(sys.executable).resolve().parent / "data"


# ---- The move ---------------------------------------------------------------

def settle(old: Path, new: Path, recycle=None) -> Resolved:
    """Make ``new`` the data folder, carrying ``old`` over first if it has data.

    Returns the folder to use: ``new`` once it is safe to, ``old`` whenever the
    move could not be finished and verified. ``recycle`` disposes of the old
    folder after a verified move; the Recycle Bin unless a test says otherwise.
    """
    recycle = recycle or _to_recycle_bin
    marker = new / MARKER
    if marker.exists():
        if _has_files(old):
            # Recycling it failed last time, or an older build ran since and
            # wrote there. Either way it is not ours to delete unseen.
            return Resolved(new, f"{new} (an old data folder is still at {old}; "
                                 "it is not used — look through it and delete it)")
        return Resolved(new, str(new))

    if not _has_files(old):
        # Nothing to carry over: a first start, or a folder that is already the
        # data in all but the marker. The folder carries this app's name, and
        # the alternative is writing beside the exe again.
        adopted = _has_files(new)
        new.mkdir(parents=True, exist_ok=True)
        _write_marker(new, moved_from=None, files=0, size=0)
        return Resolved(new, f"{new} ({'adopted' if adopted else 'new'})")

    if _has_files(new):
        return Resolved(old, f"{old} — not moved: {new} already holds files that "
                             "are not marked as the data folder")
    try:
        files, size = _copy_verified(old, new)
    except Exception as err:  # noqa: BLE001 — any failure keeps the old folder in use
        return Resolved(old, f"{old} — the move to {new} failed and was undone: {err}")

    if recycle(old):
        left = "the old folder went to the Recycle Bin"
    else:
        left = f"the old folder could not be sent to the Recycle Bin and is still at {old}"
    return Resolved(new, f"{new} (moved from {old} just now: {files} files, "
                         f"{size / 1_048_576:.1f} MB, each one verified; {left})")


def _copy_verified(old: Path, new: Path) -> tuple[int, int]:
    """Copy ``old`` to ``new`` through a staging folder, checking every file."""
    staging = new.with_name(new.name + STAGING_SUFFIX)
    if staging.exists():
        shutil.rmtree(staging)          # a move that died half way; ours alone
    try:
        shutil.copytree(old, staging)
        files = size = 0
        for original in old.rglob("*"):
            if not original.is_file():
                continue
            copy = staging / original.relative_to(old)
            if not copy.is_file():
                raise OSError(f"{copy} was not written")
            if copy.stat().st_size != original.stat().st_size:
                raise OSError(f"{copy} differs in size from {original}")
            if _sha256(copy) != _sha256(original):
                raise OSError(f"{copy} differs in content from {original}")
            files += 1
            size += original.stat().st_size
        _write_marker(staging, moved_from=old, files=files, size=size)
        if new.exists():
            new.rmdir()                  # empty — checked by the caller
        staging.rename(new)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return files, size


def _has_files(folder: Path) -> bool:
    try:
        return folder.is_dir() and any(p.is_file() for p in folder.rglob("*"))
    except OSError:
        return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_marker(folder: Path, moved_from: Path | None, files: int, size: int) -> None:
    from . import __version__
    (folder / MARKER).write_text(json.dumps({
        "what": "Wallpaper Engine Toolkit keeps its data in this folder.",
        "since": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "version": __version__,
        "moved_from": str(moved_from) if moved_from else None,
        "files": files,
        "bytes": size,
    }, indent=2), encoding="utf-8")


# ---- Windows ----------------------------------------------------------------

@contextmanager
def _exclusive():
    """Hold a named mutex, so two processes starting at once move nothing twice.

    A process that dies holding it abandons it, and Windows hands it to the next
    waiter, so a crash half way cannot lock the others out.
    """
    if sys.platform != "win32":
        yield
        return
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.ReleaseMutex.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    if not handle:
        yield
        return
    kernel32.WaitForSingleObject(handle, 0xFFFFFFFF)
    try:
        yield
    finally:
        kernel32.ReleaseMutex(handle)
        kernel32.CloseHandle(handle)


def _to_recycle_bin(path: Path) -> bool:
    """Send a folder to the Recycle Bin, silently. True if it is gone."""
    if sys.platform != "win32":
        return False
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND),
                    ("wFunc", wintypes.UINT),
                    ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR),
                    ("fFlags", ctypes.c_ushort),
                    ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p),
                    ("lpszProgressTitle", wintypes.LPCWSTR)]

    fo_delete = 0x3
    flags = 0x0004 | 0x0010 | 0x0040 | 0x0400   # silent, no confirm, undo, no error UI
    # pFrom is a list ended by an empty entry: the path, then two NULs.
    operation = SHFILEOPSTRUCTW(None, fo_delete, str(path) + "\0", None, flags,
                                False, None, None)
    try:
        failed = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))
    except OSError:
        return False
    return not failed and not operation.fAnyOperationsAborted and not path.exists()
