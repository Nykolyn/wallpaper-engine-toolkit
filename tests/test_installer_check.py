"""Installer checks must observe starting processes and the final uninstall deletion."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.engines import engine_control
from tools import check_installer as ci

results: list[bool] = []


def check(label: str, condition: bool) -> None:
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label)


with patch.object(ci, "mutex_held", side_effect=AssertionError("process detection must not need a mutex")):
    for processes, expected, label in (
        ([], False, "no process means nothing is running"),
        ([(7, "WallpaperEngineToolkit.exe")], True, "a starting process is found before its mutex or socket"),
        ([(7, "WALLPAPERENGINETOOLKIT.EXE")], True, "Windows executable names are case insensitive"),
        ([(7, "WallpaperEngineToolkit-Setup-4.1.4.exe"), (8, "wallpaper64.exe")], False,
         "the installer and Wallpaper Engine are not the toolkit"),
    ):
        with patch.object(engine_control, "processes", return_value=processes):
            check(label, ci.program_running() is expected)


with tempfile.TemporaryDirectory(prefix="toolkit_installer_check_test_") as temporary:
    folder = Path(temporary)
    uninstaller = folder / "unins000.exe"
    log = folder / "uninstall.log"
    uninstaller.write_bytes(b"a fake uninstaller")

    def finish_uninstall(condition, seconds):
        check("removing the registry entry alone is not the end of uninstall", not condition())
        uninstaller.unlink()
        return condition()

    with patch.object(ci, "INSTALL_DIR", folder), patch.object(ci, "registered", return_value=False), \
            patch.object(ci.subprocess, "run") as run, patch.object(ci, "wait_for", finish_uninstall):
        check("uninstall finishes after its executable is deleted too", ci.uninstall(log))
        check("only the temporary fake uninstaller was invoked", run.call_args.args[0][0] == str(uninstaller))

    uninstaller.write_bytes(b"a fake uninstaller")
    with patch.object(ci, "INSTALL_DIR", folder), patch.object(ci, "registered", return_value=True), \
            patch.object(ci.subprocess, "run"), patch.object(ci, "wait_for", lambda condition, seconds: condition()):
        check("unfinished uninstall reports failure", not ci.uninstall(log))

    uninstaller.unlink()
    with patch.object(ci, "INSTALL_DIR", folder), patch.object(ci.subprocess, "run") as run:
        check("a missing uninstaller is reported without launching anything", not ci.uninstall(log) and not run.called)


print("\nPASSED %d/%d" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
