"""What the old tabs' sidebar items say, until their own pages say it.

An old tab knows nothing of the sidebar, so its item is worked out here from
the services: a job of that tool running (a bar and a percentage, or
"working"). Each page step moves its tool's part into its own page and
deletes it from here (the Tracker's went in step 08, the Rotator's in 10).
"""
from __future__ import annotations

from PySide6.QtCore import QObject

from ..ui.kit import NavState

# Tools whose sidebar item shows a bar while they work; the others say "working".
_BARS = ("review",)


def nav_for(key: str, jobs, snapshot=None) -> NavState:
    """The sidebar state of the old tab `key`, from the JobCenter."""
    running = [job for job in jobs.running() if job.tool == key]
    if running:
        job = running[0]
        if key in _BARS and job.total > 0:
            return NavState.progress(job.done, job.total)
        return NavState.status("working", below=key in _BARS)
    if key in ("creator", "copier"):
        return NavState.status("idle")
    return NavState()


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
