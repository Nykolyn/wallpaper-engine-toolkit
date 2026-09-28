"""The JobCenter: what is running now, and what finished last.

Every tool that starts a piece of work — a rotation, a build, a copy, a
Review scan — registers it here and reports its progress to it. The status
line, the sidebar, "Next in the loop" and Overview read this one place, so
none of them polls a page or knows how a tool does its work.

- `JobCenter.start(tool, title, page)` returns a `Job`.
- `Job.update(phase_text, done, total, count_text=None)` reports progress;
  any of them left out keeps its last value.
- `Job.finish(result, summary)` ends it: `clean`, `problems`, `stopped` or
  `failed`; `Job.fail(message)` is `finish("failed", message)`.
- `changed(job)` is emitted when a job starts, moves or ends, and
  `finished(job)` when it ends. A job's count may move a thousand times a
  second (a rotation moves a folder, a scan checks one), so `changed` for a
  count alone is sent at most every `throttle` seconds, and the last value
  always arrives; a new phase or the end is sent at once.

A job is started and reported from the GUI thread; a worker reports through
the page's own signals, as the tabs' workers already do.

Rate and time left come from the job's own progress, never from a guess:
only after `MIN_SAMPLES` counts within one phase, spread over `MIN_SPAN`
seconds, and only while the count grows. They are estimates, and the UI
writes them with `≈`.
"""
from __future__ import annotations

import time
from collections import deque
from datetime import datetime

from PySide6.QtCore import QObject, QTimer, Signal

RESULTS = ("clean", "problems", "stopped", "failed")
# Which running job the status line leads with: the one that is moving files
# of the loop first. A rotation closes Wallpaper Engine and moves the reserve;
# a copy or a build writes into myprojects; a Review scan only reads.
PRIORITY = {"rotator": 0, "copier": 1, "creator": 2, "review": 3, "tracker": 4}
OTHER_PRIORITY = 9

# A rate needs this many counts within one phase, over at least this long.
MIN_SAMPLES = 5
MIN_SPAN = 2.0
# The rate is measured over the last this many counts: recent enough to follow
# a rotation that slows down on a large folder, long enough not to jitter.
SAMPLE_WINDOW = 50
# At most this often a `changed` for a count alone, in seconds.
THROTTLE = 0.1


class Job:
    """One piece of work, as the JobCenter knows it. Made by `JobCenter.start`."""

    def __init__(self, center: "JobCenter", number: int, tool: str, title: str,
                 page: str | None, priority: int):
        self._center = center
        self.number = number                # 1, 2, 3 … in the order started
        self.tool = tool
        self.title = title
        self.page = page or tool            # where "Show" goes
        self.priority = priority
        self.phase_text = ""
        self.done = 0
        self.total = 0                      # 0: not counted (indeterminate)
        self.count_text: str | None = None  # None: the UI writes done / total
        self.result: str | None = None      # one of RESULTS once finished
        self.summary = ""
        self.started = center._wall()
        self.ended: datetime | None = None
        # The file this job's log lines go to, when it has one (LogStore).
        self.log_path = None
        self._t0 = center._clock()
        self._t_end: float | None = None
        self._samples: deque[tuple[float, int]] = deque(maxlen=SAMPLE_WINDOW)

    def __repr__(self) -> str:
        state = self.result or f"{self.done}/{self.total}"
        return f"<Job {self.number} {self.tool} {self.title!r} {state}>"

    # -- state

    @property
    def running(self) -> bool:
        return self.result is None

    def fraction(self) -> float | None:
        """How far along, 0–1, or None while it is not counted."""
        if self.total <= 0:
            return None
        return max(0.0, min(1.0, self.done / self.total))

    def elapsed(self) -> float:
        """Seconds since it started, up to its end once it has ended."""
        end = self._t_end if self._t_end is not None else self._center._clock()
        return max(0.0, end - self._t0)

    def rate(self) -> float | None:
        """Counts a second in this phase, measured; None until enough of them.

        Taken up to now rather than up to the last count, so a job that stalls
        on one large folder shows its rate falling instead of a stale one.
        """
        if not self.running or len(self._samples) < MIN_SAMPLES:
            return None
        t_first, d_first = self._samples[0]
        t_last, d_last = self._samples[-1]
        if t_last - t_first < MIN_SPAN or d_last <= d_first:
            return None
        span = max(self._center._clock(), t_last) - t_first
        return (d_last - d_first) / span

    def eta(self) -> float | None:
        """Seconds left at the measured rate, or None when there is none."""
        rate = self.rate()
        if rate is None or self.total <= 0:
            return None
        return max(0.0, (self.total - self.done) / rate)

    # -- reporting

    def update(self, phase_text: str | None = None, done: int | None = None,
               total: int | None = None, count_text: str | None = None) -> None:
        """Report progress. Anything left out keeps its last value.

        A new phase, or a new total, starts the rate over: a rate carried from
        returning folders says nothing about moving them.
        """
        if not self.running:
            return
        new_phase = phase_text is not None and phase_text != self.phase_text
        if new_phase:
            self.phase_text = phase_text
        if total is not None and total != self.total:
            self.total = max(0, int(total))
            new_phase = True
        if count_text is not None:
            self.count_text = count_text or None
        if new_phase:
            self._samples.clear()
        if done is not None:
            done = max(0, int(done))
            if done != self.done or not self._samples:
                self.done = done
                if self.total > 0:
                    self._samples.append((self._center._clock(), done))
        self._center._changed(self, urgent=new_phase)

    def finish(self, result: str, summary: str = "") -> bool:
        """End it. False if it had already ended (the first ending stands)."""
        if result not in RESULTS:
            raise ValueError(f"a job ends {', '.join(RESULTS)}, not {result!r}")
        if not self.running:
            return False
        self.result = result
        self.summary = summary
        self.ended = self._center._wall()
        self._t_end = self._center._clock()
        self._center._ended(self)
        return True

    def fail(self, message: str) -> bool:
        return self.finish("failed", message)


class JobCenter(QObject):
    """The running jobs and the last finished one per tool.

    One lives in the main window (see `app.services`). `clock` and `wall` are
    for tests, which move time by hand.
    """

    changed = Signal(object)     # Job: started, moved, or ended
    finished = Signal(object)    # Job: ended

    def __init__(self, parent: QObject | None = None, *, clock=time.monotonic,
                 wall=datetime.now, throttle: float = THROTTLE):
        super().__init__(parent)
        self._clock = clock
        self._wall = wall
        self._throttle = throttle
        self._running: list[Job] = []
        self._last: dict[str, Job] = {}
        self._latest: Job | None = None
        self._count = 0
        self._sent: dict[int, float] = {}       # job number → when changed was sent
        self._held: dict[int, Job] = {}         # a count waiting for its turn
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._release)

    # -- starting

    def start(self, tool: str, title: str, page: str | None = None, *,
              priority: int | None = None) -> Job:
        self._count += 1
        if priority is None:
            priority = PRIORITY.get(tool, OTHER_PRIORITY)
        job = Job(self, self._count, tool, title, page, priority)
        self._running.append(job)
        self._changed(job, urgent=True)
        return job

    # -- reading

    def running(self) -> list[Job]:
        """The running jobs, the most important first."""
        return sorted(self._running, key=lambda j: (j.priority, -j.number))

    def current(self) -> Job | None:
        """The running job the status line leads with, or None.

        The lowest priority number wins (a rotation over a copy over a scan);
        between two of the same tool, the one started last.
        """
        jobs = self.running()
        return jobs[0] if jobs else None

    def is_running(self, tool: str) -> bool:
        return any(j.tool == tool for j in self._running)

    def last_finished(self, tool: str | None = None) -> Job | None:
        """The job of this tool that ended last; any tool's with None."""
        if tool is None:
            return self._latest
        return self._last.get(tool)

    # -- inside

    def _changed(self, job: Job, urgent: bool = False) -> None:
        now = self._clock()
        sent = self._sent.get(job.number)
        if urgent or self._throttle <= 0 or sent is None or now - sent >= self._throttle:
            self._held.pop(job.number, None)
            self._sent[job.number] = now
            self.changed.emit(job)
            return
        self._held[job.number] = job
        if not self._timer.isActive():
            wait = self._throttle - (now - sent)
            self._timer.start(max(1, int(wait * 1000)))

    def _release(self) -> None:
        held, self._held = self._held, {}
        now = self._clock()
        for job in held.values():
            if job.running:
                self._sent[job.number] = now
                self.changed.emit(job)

    def _ended(self, job: Job) -> None:
        if job in self._running:
            self._running.remove(job)
        self._held.pop(job.number, None)
        self._sent.pop(job.number, None)
        self._last[job.tool] = job
        self._latest = job
        self.changed.emit(job)
        self.finished.emit(job)
