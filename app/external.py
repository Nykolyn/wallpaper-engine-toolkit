"""external.py — starting programs that are not the toolkit, without its own DLLs.

A built toolkit is a PyInstaller folder, and it finds its own DLLs in two ways
that every process it starts inherits:

* **The DLL directory.** The bootloader calls ``SetDllDirectoryW`` with
  ``_internal``. Windows hands that directory to each process this one creates,
  and there it is searched *before* System32. On 26 September Wallpaper Engine,
  restarted at the end of a rotation, had ``_internal\\VCRUNTIME140.dll``
  loaded. For as long as it ran, build.cmd's robocopy could not replace that
  file or ``VCRUNTIME140_1.dll`` (ERROR 32).
* **The environment.** Run-time hooks put ``_internal`` (PyInstaller's, so Qt
  finds OpenSSL) and ``_internal\\PySide6`` (PySide6's own, for its plugins) at
  the front of ``PATH``, and point ``QT_PLUGIN_PATH`` and ``QML2_IMPORT_PATH``
  into the bundle. ``PATH`` comes after the system folders in a search, so
  only a DLL Windows does not have is found there (Qt's, OpenSSL's, Python's),
  but a child passes all of it on to its own children. A source run has the
  same leak in ``PATH``: PySide6's package folder is put first, and it ships its
  own ``vcruntime140.dll`` and ``msvcp140.dll``.

PyInstaller's advice is to clear the DLL directory before starting another
program and to take the bundle's paths out of ``PATH``. Everything the toolkit
starts that is not the toolkit goes through here. The one exception is
``window_instance.launch``: it starts the toolkit itself, which wants exactly
this setup, and its own bootloader sets it up again anyway.

The DLL directory is not cleared for good at start-up, although that would be
simpler. The toolkit's own imports can still depend on it after start-up, which
is why PyInstaller sets it in the first place. So it is cleared only while a
process is being created, under a lock, and then put back: 2 ms median, 7 ms
at most, measured over twenty starts each of cmd.exe and schtasks. For that
long, a DLL that another thread of this process loads is searched for without
``_internal``.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import threading
from contextlib import contextmanager
from ctypes import wintypes
from typing import Iterator, Mapping

# Set by the bootloader for itself (_MEIPASS2 before PyInstaller 6, _PYI_* after).
_BOOTLOADER_PREFIXES = ("_MEI", "_PYI")
# Packages that put their own folder on PATH when they are imported.
_PATH_PACKAGES = ("PySide6",)

_CREATE_NO_WINDOW = 0x08000000
_BUFFER = 32768

if os.name == "nt":
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.SetDllDirectoryW.argtypes = [wintypes.LPCWSTR]
    _k32.SetDllDirectoryW.restype = wintypes.BOOL
    _k32.GetDllDirectoryW.argtypes = [wintypes.DWORD, wintypes.LPWSTR]
    _k32.GetDllDirectoryW.restype = wintypes.DWORD
else:                                                      # pragma: no cover
    _k32 = None

# Re-entrant: a launch made inside clean_dll_search() just finds nothing to clear.
_lock = threading.RLock()


# ---- What a child must not be given ---------------------------------------------

def own_folders() -> list[str]:
    """The toolkit's own folders, as compared: the bundle, and PySide6's package."""
    found = []
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        found.append(bundle)
    for name in _PATH_PACKAGES:
        where = getattr(sys.modules.get(name), "__file__", None)
        if where:
            found.append(os.path.dirname(where))
    folders = set()
    for folder in found:
        # PySide6 puts its folder on PATH resolved, so compare both spellings.
        for spelling in (os.path.abspath(folder), os.path.realpath(folder)):
            folders.add(os.path.normcase(spelling).rstrip("\\/"))
    return sorted(folders)


def _inside(entry: str, folders: list[str]) -> bool:
    # Only an absolute path can point into the bundle. Judging relative ones
    # would resolve every "1" and "AMD64" against the current folder, and drop
    # them all whenever that folder happened to be _internal.
    entry = entry.strip().strip('"')
    if not os.path.isabs(entry):
        return False
    path = os.path.normcase(os.path.abspath(entry))
    return any(path == folder or path.startswith(folder + os.sep) for folder in folders)


def _without(value: str, folders: list[str]) -> str | None:
    """A path list without its entries inside `folders`; None if nothing is left."""
    entries = value.split(os.pathsep)
    kept = [e for e in entries if not _inside(e, folders)]
    if len(kept) == len(entries):
        return value
    return os.pathsep.join(kept) if any(e.strip() for e in kept) else None


def environment(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """This process's environment, less what the toolkit's runtime added for itself.

    The bootloader's own variables go, and so does every path into the
    toolkit's folders, whatever variable holds it: ``PATH`` loses those
    entries and keeps the rest in order, and a variable with nothing else in
    it, such as ``QT_PLUGIN_PATH``, is left out.
    """
    folders = own_folders()
    clean = {}
    for name, value in (os.environ if base is None else base).items():
        if name.upper().startswith(_BOOTLOADER_PREFIXES):
            continue
        if folders:
            value = _without(value, folders)
            if value is None:
                continue
        clean[name] = value
    return clean


def dll_directory() -> str:
    """The folder ``SetDllDirectoryW`` last set in this process, or ""."""
    if _k32 is None:
        return ""
    buf = ctypes.create_unicode_buffer(_BUFFER)
    return buf.value if _k32.GetDllDirectoryW(_BUFFER, buf) else ""


@contextmanager
def clean_dll_search() -> Iterator[None]:
    """No DLL directory while the block runs; whatever was set is put back after.

    ``SetDllDirectoryW(None)`` rather than ``""``: an empty string also takes
    the current folder out of the search, which is not how Windows starts a
    program.
    """
    with _lock:
        before = dll_directory()
        if before:
            _k32.SetDllDirectoryW(None)
        try:
            yield
        finally:
            if before:
                _k32.SetDllDirectoryW(before)


# ---- Starting ---------------------------------------------------------------------

def popen(args, **kwargs) -> subprocess.Popen:
    """``subprocess.Popen`` for a program that is not the toolkit.

    It gets :func:`environment` unless ``env`` is given, and is created with no
    DLL directory set.
    """
    kwargs.setdefault("env", environment())
    with clean_dll_search():
        return subprocess.Popen(args, **kwargs)


def run(args, **kwargs) -> subprocess.CompletedProcess:
    """``subprocess.run`` through :func:`popen`.

    Not ``subprocess.run`` inside :func:`clean_dll_search`: that would leave
    the DLL directory cleared for as long as the program runs, when only its
    creation needs it.
    """
    if kwargs.pop("capture_output", False):
        kwargs["stdout"] = kwargs["stderr"] = subprocess.PIPE
    with popen(args, **kwargs) as child:
        try:
            out, err = child.communicate()
        except BaseException:
            child.kill()
            raise
    return subprocess.CompletedProcess(child.args, child.returncode, out, err)


def url_command(url: str) -> str:
    """The command line that opens `url` with whatever Windows has for it.

    Written out rather than left to ``subprocess.list2cmdline``, which quotes
    only what contains a space: a workshop URL is full of ``&``, which cmd.exe
    reads as "and then run" unless it is inside quotes.
    """
    return f'cmd /c start "" "{url}"'


def open_url(url: str) -> None:
    """Open a web page or a ``steam://`` link in whatever program Windows has for it.

    Through ``cmd /c start`` in a clean child, and not through ``os.startfile``
    or ``webbrowser``. Those call ``ShellExecute`` inside this process, and the
    browser it may start then inherits this process's environment, which this
    process cannot clean. ``webbrowser`` remains only as a fallback, used if
    cmd.exe cannot be started.
    """
    try:
        popen(url_command(url), creationflags=_CREATE_NO_WINDOW)
    except OSError:
        import webbrowser
        with clean_dll_search():
            webbrowser.open(url)


# ---- For --selfcheck --------------------------------------------------------------

def report() -> tuple[bool, str]:
    """Whether a child would start clean, and what it is spared, in one line."""
    before = dll_directory()
    with clean_dll_search():
        during = dll_directory()
    after = dll_directory()
    ok = not during and after == before

    own = dict(os.environ)
    clean = environment(own)
    dropped = sorted(name for name in own if name not in clean)
    left_out = []
    for name in own:
        if name in clean and clean[name] != own[name]:
            gone = own[name].count(os.pathsep) - clean[name].count(os.pathsep)
            left_out.append(f"{gone} of {name}")
    left_out += dropped

    if not before:
        dll = "no DLL directory to clear"
    elif ok:
        dll = f"DLL directory ({os.path.basename(before)}) cleared while one starts"
    else:
        dll = f"DLL directory NOT cleared (still {during!r}, afterwards {after!r})"
    spared = ", ".join(left_out) if left_out else "nothing"
    return ok, f"{dll}; left out of their environment: {spared}"
