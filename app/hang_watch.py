"""hang_watch.py — when the window stops answering, leave a record of where.

On 18 September the toolkit window froze during a Review and Windows ended it.
All that survived was an "Application Hang" event naming the process — no
stack, no dump, nothing that said which of a dozen threads was doing what. Four
attempts to make it happen again, each running the same scan, the same count and
the same clicking through authors, never once got the window more than 0.13 s
behind. A freeze that cannot be reproduced has to be caught where it happens.

So every process with a window keeps a watch on its own GUI thread. A timer on
that thread re-arms :func:`faulthandler.dump_traceback_later` once a second;
if the thread goes `HANG_SECONDS` without getting back to it, faulthandler —
which runs in C on a thread of its own and needs neither the GIL nor the event
loop — writes the stack of every Python thread to the log. When the GUI thread
comes back, a line saying how long it was gone follows the stacks. If it never
comes back, the stacks are still there.

`faulthandler.enable` is pointed at the same file, so a hard crash leaves its
stack there too rather than vanishing with a windowed build's missing console.
"""
from __future__ import annotations

import faulthandler
import os
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QTimer

# Windows marks a window "Not Responding" after five seconds without it asking
# for messages, which is the moment a user starts reaching for Task Manager.
HANG_SECONDS = 5.0
REARM_MS = 1000
# A log that grows past this is moved aside once, at start, and begun again.
MAX_LOG_BYTES = 2 << 20


class HangWatch(QObject):
    """Dumps every thread's stack if the thread this lives on stops answering."""

    def __init__(self, path: str | Path, name: str, seconds: float = HANG_SECONDS,
                 parent: QObject | None = None):
        super().__init__(parent)
        self.path = Path(path)
        self.name = name
        self.seconds = seconds
        self.stalls = 0
        self._file = None
        self._last = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(REARM_MS)
        self._timer.timeout.connect(self._rearm)

    def start(self) -> "HangWatch":
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists() and self.path.stat().st_size > MAX_LOG_BYTES:
                os.replace(self.path, self.path.with_suffix(self.path.suffix + ".old"))
            # faulthandler writes to the descriptor itself, so this handle has to
            # stay open for as long as the process runs.
            self._file = open(self.path, "a", encoding="utf-8", buffering=1)
        except OSError:
            return self          # nowhere to write: run without the watch
        self._write(f"{self.name} started, pid {os.getpid()}")
        faulthandler.enable(file=self._file, all_threads=True)
        self._last = time.monotonic()
        self._arm()
        self._timer.start()
        return self

    def stop(self) -> None:
        self._timer.stop()
        if self._file is not None:
            faulthandler.cancel_dump_traceback_later()

    def _arm(self) -> None:
        faulthandler.dump_traceback_later(self.seconds, repeat=False, file=self._file)

    def _rearm(self) -> None:
        now = time.monotonic()
        gone = now - self._last
        self._last = now
        if gone >= self.seconds:
            # faulthandler has already written the stacks, while it was stuck.
            self.stalls += 1
            self._write(f"{self.name} did not answer for {gone:.1f} s — the stacks "
                        "above were taken while it was stuck")
        self._arm()

    def _write(self, text: str) -> None:
        try:
            self._file.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {text}\n")
        except (OSError, ValueError, AttributeError):
            pass
