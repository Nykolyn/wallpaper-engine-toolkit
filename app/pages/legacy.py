"""What the old tabs' sidebar items say, until their own pages say it.

An old tab knows nothing of the sidebar, so its item is worked out here from
the services: a job of that tool running ("working"), or "idle". Each page
step moves its tool's part into its own page and deletes it from here (the
Tracker's went in step 08, the Rotator's in 10, Review's in 11).
"""
from __future__ import annotations

from PySide6.QtCore import QObject

from ..ui.kit import NavState

def nav_for(key: str, jobs, snapshot=None) -> NavState:
    """The sidebar state of the old tab `key`, from the JobCenter."""
    if any(job.tool == key for job in jobs.running()):
        return NavState.status("working")
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
