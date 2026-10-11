"""Concurrent window/tray writes keep the shutdown trace and the log's size limit."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = Path(sys.argv[2]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="tracker_log_test_"))
os.environ["WALLPAPER_TOOLKIT_DATA"] = str(TMP / "data")
from app import tracker_tray as tray  # noqa: E402


def writer(number: str) -> None:
    read = Path.read_text

    def slow_read(path, *args, **kwargs):
        text = read(path, *args, **kwargs)
        if path == tray.LOG_PATH:
            time.sleep(0.01)  # widen the read/write race on a slow CI runner
        return text

    (TMP / f"ready-{number}").touch()
    deadline = time.monotonic() + 20
    while not (TMP / "start").exists():
        if time.monotonic() > deadline:
            raise TimeoutError("parent did not start the writers")
        time.sleep(0.01)
    with patch.object(Path, "read_text", slow_read):
        for line in range(20):
            tray.log("asked to quit during concurrent writes" if number == "0" and line == 10
                     else f"writer-{number}-{line}")


def main() -> int:
    results = []

    def check(label, condition):
        results.append(bool(condition))
        print(("PASS " if condition else "FAIL ") + label, flush=True)

    tray.log("asked to quit (before both processes restart)")
    children = [subprocess.Popen([sys.executable, __file__, "--writer", str(TMP), str(n)])
                for n in range(4)]
    try:
        deadline = time.monotonic() + 20
        while not all((TMP / f"ready-{n}").exists() for n in range(4)):
            if time.monotonic() > deadline:
                raise TimeoutError("writers did not become ready")
            time.sleep(0.01)
        (TMP / "start").touch()
        check("all concurrent writers finish", all(child.wait(20) == 0 for child in children))
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait()
    text = tray.LOG_PATH.read_text(encoding="utf-8")
    messages = [line.split("  ", 1)[1] for line in text.splitlines()]
    expected = {f"writer-{n}-{line}" for n in range(4) for line in range(20)}
    expected.remove("writer-0-10")
    expected.add("asked to quit during concurrent writes")
    check("every process's line is preserved exactly once",
          len(messages) == 81 and set(messages[1:]) == expected)
    check("restarting the window and tracker preserves the shutdown trace",
          "asked to quit during concurrent writes" in text)

    tray.LOG_PATH.write_text("old line\n" * (tray.LOG_KEEP_LINES + 10), encoding="utf-8")
    tray.log("newest line")
    lines = tray.LOG_PATH.read_text(encoding="utf-8").splitlines()
    check("the log still keeps only its last 300 lines",
          len(lines) == tray.LOG_KEEP_LINES and lines[-1].endswith("newest line"))
    with patch.object(Path, "write_text", side_effect=OSError("unwritable log")):
        tray.log("cannot be written")
    tray.log("the next writer still works")
    check("a write error is harmless and releases the lock",
          tray.LOG_PATH.read_text(encoding="utf-8").endswith("the next writer still works\n"))
    print(f"\nPASSED {sum(results)}/{len(results)}")
    return 0 if all(results) else 1


if __name__ == "__main__":
    if len(sys.argv) > 1:
        writer(sys.argv[3])
    else:
        sys.exit(main())
