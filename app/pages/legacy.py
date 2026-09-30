"""What the old tabs' sidebar items say, until their own pages say it.

An old tab knows nothing of the sidebar, so its item is worked out here from
the services: a job of that tool running (a bar and a percentage, or
"working"), or the Rotator's last run. Each page step moves its tool's part
into its own page and deletes it from here (the Tracker's went in step 08).
"""
from __future__ import annotations

from PySide6.QtCore import QObject

from ..services.snapshot import LAST_RUN
from ..ui.kit import NavState

# Tools whose sidebar item shows a bar while they work; the others say "working".
_BARS = ("rotator", "review")


def nav_for(key: str, jobs, snapshot) -> NavState:
    """The sidebar state of the old tab `key`, from the JobCenter and the Snapshot."""
    running = [job for job in jobs.running() if job.tool == key]
    if running:
        job = running[0]
        if key in _BARS and job.total > 0:
            return NavState.progress(job.done, job.total)
        return NavState.status("working", below=key in _BARS)
    if key == "rotator":
        return rotator_idle(jobs, snapshot)
    if key in ("creator", "copier"):
        return NavState.status("idle")
    return NavState()


def rotator_idle(jobs, snapshot) -> NavState:
    """"ready · run 39"; "clean · run 39" after a clean run this session;
    "2 problems" or "run 39 failed" until the next run."""
    reading = snapshot[LAST_RUN]
    if reading.at is None:
        return NavState()                   # not read yet: say nothing rather than guess
    last = reading.value
    if last is None:
        return NavState.status("ready · run 1", below=True)
    if last.result == "problems":
        n = last.failed
        words = f"{n} problem{'s' * (n != 1)}" if n else "problems"
        return NavState.status(words, "warn", below=True)
    if last.result == "failed":
        return NavState.status(f"run {last.number} failed", "danger", below=True)
    finished = jobs.last_finished("rotator")
    if (last.result == "clean" and finished is not None and finished.result == "clean"
            and finished.title == f"Run {last.number}"):
        return NavState.status(f"clean · run {last.number}", "ok", below=True)
    return NavState.status(f"ready · run {last.number + 1}", below=True)


class LegacyNav(QObject):
    """Keeps the old tabs' sidebar items up to date as jobs and readings change."""

    def __init__(self, pages, services, parent: QObject | None = None):
        super().__init__(parent)
        self._pages = list(pages)
        self._services = services
        services.jobs.changed.connect(self.update)
        services.snapshot.refreshed.connect(self.update)
        self.update()

    def update(self, *_args) -> None:
        jobs, snapshot = self._services.jobs, self._services.snapshot
        for page in self._pages:
            page.set_nav_state(nav_for(page.key, jobs, snapshot))
