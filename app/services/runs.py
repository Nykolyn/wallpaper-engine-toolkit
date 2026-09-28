"""A page's piece of work, told to the JobCenter, its log and the journal at once.

    run = begin("creator", "Building 3 wallpapers", activity="build")
    run.update("building 3 wallpapers", 2, 3)
    run.log_text("[OK]    some-clip-a1b2c3")
    run.finish("clean", "3 wallpapers created", detail="in myprojects")

`finish` ends the job, writes the last log line, and — when the run has an
`activity` — a journal entry of kind `<activity>.<result>`. `note` adds any
other entry (`run.started`, `duplicates.set_aside`). With no services
installed every call does nothing, so a tab works the same on its own.
"""
from __future__ import annotations

# The log line a result ends with, in the LogPanel's kinds.
_END_KINDS = {"clean": "done", "problems": "warn", "stopped": "stop", "failed": "error"}


class Run:
    def __init__(self, services, tool: str, title: str, page: str | None = None, *,
                 activity: str | None = None, run_id: str | None = None):
        self.tool = tool
        self.title = title
        self.activity = activity
        self.result: str | None = None
        self._journal = services.journal if services is not None else None
        self.job = services.jobs.start(tool, title, page) if services is not None else None
        self._log = services.logs.open(tool, run_id) if services is not None else None
        if self.job is not None and self._log is not None:
            self.job.log_path = self._log.path
        self.log("start", title)

    @property
    def ended(self) -> bool:
        return self.result is not None

    def update(self, phase_text: str | None = None, done: int | None = None,
               total: int | None = None, count_text: str | None = None) -> None:
        if self.job is not None:
            self.job.update(phase_text, done, total, count_text)

    def log(self, kind: str, message: str) -> None:
        if self._log is not None and not self.ended:
            self._log.write(kind, message)

    def log_text(self, text: str) -> None:
        """Lines in a callback engine's own `[TAG]  message` form."""
        if self._log is not None and not self.ended:
            self._log.write_text(text)

    def note(self, kind: str, title: str, detail: str = "", chip: str | None = None,
             run: str | None = None) -> None:
        """A journal entry of this run's tool."""
        if self._journal is not None:
            self._journal.add(self.tool, kind, title, detail, chip, run)

    def finish(self, result: str, summary: str, *, title: str | None = None,
               detail: str = "", chip: str | None = None, run: str | None = None,
               journal: bool = True) -> bool:
        """End it. False if it had already ended: the first ending stands.

        `journal=False` leaves the journal out of an ending not worth a row of
        its own (a folder check that found nothing, before every rotation).
        """
        if self.ended:
            return False
        self.log(_END_KINDS.get(result, "info"), summary)
        self.result = result
        if self.job is not None:
            self.job.finish(result, summary)
        if self.activity and journal:
            self.note(f"{self.activity}.{result}", title or summary, detail, chip, run)
        if self._log is not None:
            self._log.close()
        return True

    def fail(self, message: str, *, title: str | None = None) -> bool:
        return self.finish("failed", message, title=title or f"{self.title} failed",
                           detail=message)


def begin(tool: str, title: str, page: str | None = None, *,
          activity: str | None = None, run_id: str | None = None) -> Run:
    """Start reporting a piece of work to the installed services (if any)."""
    from . import current
    return Run(current(), tool, title, page, activity=activity, run_id=run_id)
