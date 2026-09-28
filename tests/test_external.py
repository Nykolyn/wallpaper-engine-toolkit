"""Programs the toolkit starts do not get its DLLs.

    .venv\\Scripts\\python.exe tests\\test_external.py

On 26 September Wallpaper Engine, restarted by a rotation from the built
toolkit, was running on the build's own ``_internal\\VCRUNTIME140.dll``: the
bootloader's ``SetDllDirectoryW`` is handed down to every child, and so is a
PATH that run-time hooks have put the bundle at the front of. A frozen build
cannot be made here, so the bundle is a folder of this test's own, set up the
way the bootloader and the hooks set up ``_internal``, and a real child
process reports whether it can load a DLL that is only in that folder.
"""
from __future__ import annotations

import ctypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import external                                   # noqa: E402
from app.engines import engine_control                     # noqa: E402

results: list[bool] = []


def check(label: str, condition: bool) -> None:
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label)


k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.SetDllDirectoryW.argtypes = [wintypes.LPCWSTR]

TMP = Path(tempfile.mkdtemp(prefix="wetk_external_"))
BUNDLE = TMP / "WallpaperEngineToolkit" / "_internal"
(BUNDLE / "PySide6" / "plugins").mkdir(parents=True)
USER = TMP / "tools"
USER.mkdir()


def as_bundle(folder: Path | None) -> None:
    """Pretend this process is a build unpacked at `folder` (None: a source run)."""
    if folder is None:
        if hasattr(sys, "_MEIPASS"):
            del sys._MEIPASS
    else:
        sys._MEIPASS = str(folder)


# ---- The environment a child is given ---------------------------------------------

as_bundle(BUNDLE)
windows = os.environ.get("SystemRoot", r"C:\Windows")
base = {
    "PATH": os.pathsep.join([str(BUNDLE / "PySide6"), str(BUNDLE), rf"{windows}\system32",
                             "", str(USER), f'"{str(BUNDLE).upper()}"',
                             str(BUNDLE) + "-not-it"]),
    "QT_PLUGIN_PATH": str(BUNDLE / "PySide6" / "plugins"),
    "QML2_IMPORT_PATH": str(BUNDLE / "PySide6" / "qml"),
    "_PYI_APPLICATION_HOME_DIR": str(BUNDLE),
    "_PYI_PARENT_PROCESS_LEVEL": "1",
    "_MEIPASS2": str(BUNDLE),
    "NUMBER_OF_PROCESSORS": "28",
    "QT_SCALE_FACTOR": "1.25",
    "STEAM_LIBRARY": str(USER),
}
saved_cwd = os.getcwd()
os.chdir(BUNDLE)            # where a relative value would resolve into the bundle
try:
    clean = external.environment(base)
finally:
    os.chdir(saved_cwd)

check("PATH keeps its other entries, in order, and drops every one into the bundle",
      clean.get("PATH") == os.pathsep.join([rf"{windows}\system32", "", str(USER),
                                           str(BUNDLE) + "-not-it"]))
check("a variable that only pointed into the bundle is left out",
      "QT_PLUGIN_PATH" not in clean and "QML2_IMPORT_PATH" not in clean)
check("the bootloader's own variables are left out",
      not any(name.upper().startswith(("_PYI", "_MEI")) for name in clean))
check("everything else is passed on as it was, relative values included",
      clean.get("NUMBER_OF_PROCESSORS") == "28" and clean.get("QT_SCALE_FACTOR") == "1.25"
      and clean.get("STEAM_LIBRARY") == str(USER))
before = dict(os.environ)
external.environment()
check("making a child's environment leaves this process's alone", dict(os.environ) == before)

# A source run: no bundle, but PySide6 puts its own folder (vcruntime140.dll
# and all) at the front of PATH as it is imported.
as_bundle(None)
import PySide6                                             # noqa: E402

pyside = os.path.dirname(PySide6.__file__)
on_path = [p for p in os.environ["PATH"].split(os.pathsep) if p]
check("importing PySide6 put its folder on this process's PATH",
      any(os.path.normcase(os.path.realpath(p)) == os.path.normcase(os.path.realpath(pyside))
          for p in on_path))
kept = external.environment()["PATH"].split(os.pathsep)
check("and a child's PATH does not have it",
      not any(p and os.path.normcase(os.path.realpath(p))
              == os.path.normcase(os.path.realpath(pyside)) for p in kept))

# ---- The DLL directory ------------------------------------------------------------

k32.SetDllDirectoryW(str(BUNDLE))
check("the DLL directory reads back as set", external.dll_directory() == str(BUNDLE))
with external.clean_dll_search():
    inside = external.dll_directory()
check("inside clean_dll_search there is none", inside == "")
check("afterwards it is back", external.dll_directory() == str(BUNDLE))
try:
    with external.clean_dll_search():
        raise RuntimeError("the launch failed")
except RuntimeError:
    pass
check("it is put back when the launch fails, too", external.dll_directory() == str(BUNDLE))
clean_ok, said = external.report()
check("the self-check reports it cleared", clean_ok and "cleared" in said)
k32.SetDllDirectoryW(None)
check("with none set, there is nothing to clear and nothing is set after",
      external.report()[0] and external.dll_directory() == "")

# ---- A real child: the leak, and the fix ------------------------------------------

# A DLL only the bundle has, under a name nothing else on this machine uses.
probe = f"wetk_probe_{uuid.uuid4().hex[:8]}.dll"
source = Path(sys.base_prefix) / "DLLs" / "sqlite3.dll"
if not source.exists():
    source = Path(sys.base_prefix) / "python3.dll"
shutil.copy(source, BUNDLE / probe)

# The interpreter itself, not a venv's launcher: that would start it as a
# grandchild, which is not what is being tested.
python = getattr(sys, "_base_executable", sys.executable)
CHILD = f'''
import ctypes
from ctypes import wintypes
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.LoadLibraryW.restype = wintypes.HMODULE
k32.LoadLibraryW.argtypes = [wintypes.LPCWSTR]
k32.GetModuleFileNameW.argtypes = [wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
handle = k32.LoadLibraryW("{probe}")
path = ctypes.create_unicode_buffer(1024)
if handle:
    k32.GetModuleFileNameW(handle, path, 1024)
print(path.value if handle else "not found")
'''


def loads(launcher) -> str:
    """Where a child started by `launcher` found the probe DLL, or 'not found'."""
    done = launcher([python, "-I", "-S", "-c", CHILD], capture_output=True, text=True)
    return (done.stdout or done.stderr).strip()


def from_bundle(where: str) -> bool:
    return os.path.normcase(where).startswith(os.path.normcase(str(BUNDLE)))


as_bundle(BUNDLE)
saved_path = os.environ["PATH"]
try:
    k32.SetDllDirectoryW(str(BUNDLE))
    check("a child started the plain way loads a DLL from the parent's DLL directory",
          from_bundle(loads(subprocess.run)))
    check("a child started through external does not",
          loads(external.run) == "not found")
    check("and the parent still has its DLL directory afterwards",
          external.dll_directory() == str(BUNDLE))
    k32.SetDllDirectoryW(None)

    os.environ["PATH"] = str(BUNDLE) + os.pathsep + saved_path
    check("a child started the plain way loads a DLL from the bundle through PATH",
          from_bundle(loads(subprocess.run)))
    check("a child started through external does not", loads(external.run) == "not found")
finally:
    os.environ["PATH"] = saved_path
    k32.SetDllDirectoryW(None)
    as_bundle(None)

# ---- Opening a URL ----------------------------------------------------------------

url = "https://steamcommunity.com/id/someone/myworkshopfiles/?appid=431960&p=2&numperpage=30"
command = external.url_command(url)
check("a URL is handed to start as one quoted argument, after an empty title",
      command == f'cmd /c start "" "{url}"')
# The same command line with echo in place of start: cmd must keep every "&"
# inside the quotes instead of running what follows each one as a command.
echoed = external.run(command.replace('start ""', "echo"), capture_output=True, text=True)
check("cmd.exe reads the quoted URL whole, & and all",
      echoed.stdout.strip() == f'"{url}"')

# ---- Everything goes through it ---------------------------------------------------

# The call that started this: Wallpaper Engine, restarted after a rotation.
started = []


class FakeChild:
    pid = 0

    def poll(self):
        return None


def fake_popen(args, **kwargs):
    started.append((args, kwargs))
    return FakeChild()


real_popen = external.popen
external.popen = fake_popen
try:
    install = TMP / "wallpaper_engine"
    engine = engine_control.Engine(pid=1, exe=str(install / "wallpaper64.exe"),
                                   arguments=["-language", "english", "-showbrowse"])
    engine_control.start(engine, seconds=0)
finally:
    external.popen = real_popen
args, kwargs = started[0] if started else ([], {})
check("Wallpaper Engine is started through external.popen",
      args == [str(install / "wallpaper64.exe"), "-language", "english"])
check("from its own folder, at normal priority, with external's environment",
      kwargs.get("cwd") == str(install) and kwargs.get("creationflags", 0) & 0x20
      and "env" not in kwargs)

# Nothing in the app starts a program any other way. window_instance starts
# the toolkit itself, which is meant to have its setup; external is the way.
ALLOWED = {"external.py", "window_instance.py"}
LAUNCH = re.compile(r"\bsubprocess\.(Popen|run|call|check_call|check_output)\s*\("
                    r"|\bos\.(startfile|system|spawn\w*|exec\w*|popen)\s*\("
                    r"|\bwebbrowser\.open\w*\s*\(|\bQProcess\b|\bQDesktopServices\.openUrl"
                    r"|\bShellExecute\w*\s*\(")
offenders = []
for path in sorted((ROOT / "app").rglob("*.py")):
    if path.name in ALLOWED:
        continue
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        code = line.split("#", 1)[0]
        if LAUNCH.search(code):
            offenders.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
for offender in offenders:
    print("      " + offender)
check("no other module starts a program without external", not offenders)

shutil.rmtree(TMP, ignore_errors=True)
print()
print("PASSED %d/%d" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
