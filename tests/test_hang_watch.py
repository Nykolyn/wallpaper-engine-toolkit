"""The hang log: a GUI thread that stops answering leaves its stacks behind.

    .venv\\Scripts\\python.exe tests\\test_hang_watch.py

The freeze this exists for could not be made to happen again on purpose, so
the one thing that must work is the record of the next one. The GUI thread is
stuck here on purpose, in a function with a name worth looking for, and the log
has to say where it was.
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from app.hang_watch import HangWatch  # noqa: E402

results: list[bool] = []


def check(label: str, condition: bool) -> None:
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label)


def spin(seconds: float) -> None:
    """Let the event loop run, as a healthy window does."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)


def stuck_reading_a_slow_disk(seconds: float) -> None:
    """What a frozen window looks like from inside: busy, not in the event loop."""
    time.sleep(seconds)


folder = Path(tempfile.mkdtemp(prefix="wallpaper_hang_test_"))
log = folder / "window-hangs.log"
watch = HangWatch(log, "the test window", seconds=0.6)
watch._timer.setInterval(100)
watch.start()
check("starting says so, with the process it belongs to",
      "the test window started, pid" in log.read_text(encoding="utf-8"))

spin(1.2)
check("a window that keeps answering writes nothing more",
      "Timeout" not in log.read_text(encoding="utf-8") and watch.stalls == 0)

stuck_reading_a_slow_disk(1.5)
spin(0.5)
text = log.read_text(encoding="utf-8")
check("a stuck GUI thread gets its stacks written while it is stuck",
      "Timeout" in text)
check("and they name where it was stuck",
      "stuck_reading_a_slow_disk" in text)
check("once it answers again, the log says for how long it did not",
      "did not answer for" in text and watch.stalls == 1)

spin(1.2)
check("and it goes back to watching quietly",
      log.read_text(encoding="utf-8").count("Timeout") == 1)
watch.stop()

# A log that has grown too large is set aside, not appended to for ever.
big = folder / "big.log"
big.write_bytes(b"x" * (3 << 20))
HangWatch(big, "a second window", seconds=30).start().stop()
check("an oversized log is moved aside when a new run starts",
      (folder / "big.log.old").exists() and big.stat().st_size < 1024)

print()
print("PASSED %d/%d" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
