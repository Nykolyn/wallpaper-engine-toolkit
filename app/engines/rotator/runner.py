"""A rotation, a retry and a playlist rebuild, start to finish, without Qt.

The workers in `worker.py` run these on a thread; tests run them directly.

Each goes through the steps of `core.STEPS` that it needs (`steps`), and
every event it hands on carries the index of its step in that list
(`ProgressEvent.step`) with the count within it (`current` / `total`). A
step's opening and closing lines are logged as `step N` (N from 1), the way
the design's log numbers them. The page draws its StepList from `steps` and
these indices; nothing here knows about the page.

    run = RotationRun(config, history, progress=emit, check_finished=when)
    run.stop_after_step()          # from any thread: stop at the next step boundary
    record = run.execute()         # run.result, run.meta, run.refresh afterwards

A run writes its side-file entry (`meta.RunMeta`) once it is in the history.
Its log file is `meta.run_log_name(run_id)`, written by whoever shows the
events (the page, through `services.begin(..., run_id=run.run_id)`), so the
file and the LogPanel hold the same lines.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable

from ..playlist_refresh import PlaylistRefresh, recent_batches
from .config import Config, History, RunRecord, new_run_id
from .core import (
    ProgressCallback, ProgressEvent, RetryResult, Rotator, Step, folder_problem, run_steps,
    step,
)
from .meta import RunMeta, RunMetaStore, now_text, record_meta, run_log_name

Clock = Callable[[], datetime]


def _noop(_e: ProgressEvent) -> None:
    pass


class StepTracker:
    """Tags a run's events with their step, and notes when each step ended."""

    def __init__(self, steps: tuple[Step, ...], progress: ProgressCallback, clock: Clock):
        self.steps = steps
        self.index: int | None = None
        self.times: dict[str, str] = {}
        self._progress = progress
        self._clock = clock

    def position(self, key: str) -> int | None:
        return next((i for i, s in enumerate(self.steps) if s.key == key), None)

    def enter(self, key: str) -> None:
        i = self.position(key)
        if i is None or i == self.index:
            return
        self._close()
        self.index = i

    def leave(self) -> None:
        """The last step has ended."""
        self._close()

    def _close(self) -> None:
        if self.index is not None:
            self.times[self.steps[self.index].key] = now_text(self._clock())

    def relay(self, e: ProgressEvent) -> None:
        if e.step is None:
            e.step = self.index
        if e.kind == "step" and e.step is not None:
            e.kind = f"step {e.step + 1}"
        self._progress(e)


class RotationRun:
    """One rotation: close Wallpaper Engine, return, draw and move, rebuild the
    playlist, start Wallpaper Engine again, and write the run's side file.

    `check_finished`: when the check for broken folders that began this run
    finished (the check is a worker of its own, with a confirmation between it
    and the rest). With it the steps start with "check", and its time is in
    the side file. `started`: when the run began from the user's point of view
    (the Start button), if earlier than `execute()`.
    """

    def __init__(self, config: Config, history: History, *, run_id: str | None = None,
                 progress: ProgressCallback = _noop, check_finished: datetime | None = None,
                 started: datetime | None = None, clock: Clock = datetime.now,
                 meta_path: Path | None = None):
        self.config = config
        self.history = history
        self.run_id = run_id or new_run_id()
        self.steps = run_steps(check=check_finished is not None,
                               playlist=config.refresh_playlist)
        self.check_finished = check_finished
        self.started = started
        self.clock = clock
        self.meta_path = meta_path
        self.tracker = StepTracker(self.steps, progress, clock)
        self._stop = False
        self._cancel = False
        # Afterwards:
        self.record: RunRecord | None = None
        self.rotator: Rotator | None = None
        self.refresh: PlaylistRefresh | None = None
        self.meta: RunMeta | None = None
        self.result = ""
        self.error = ""
        self.meta_error = ""

    @property
    def log_name(self) -> str:
        return run_log_name(self.run_id)

    @property
    def playlist_summary(self) -> list[str]:
        return list(self.refresh.summary) if self.refresh is not None else []

    def stop_after_step(self) -> None:
        """Finish the step under way, then stop: Wallpaper Engine is started
        again and the playlist left as it was."""
        self._stop = True

    def cancel(self) -> None:
        """Stop before the next folder."""
        self._cancel = True

    def execute(self) -> RunRecord | None:
        """Run it. None when it could not start (`error` says why)."""
        relay = self.tracker.relay
        started = self.started or self.clock()
        rotator = self.rotator = Rotator(self.config, self.history)
        problem = rotator.validate()
        if problem:
            self.result, self.error = "failed", problem
            relay(ProgressEvent("error", problem, level="ERROR"))
            return None
        for notice in (self.history.notice, getattr(self.config, "notice", "")):
            if notice:
                relay(ProgressEvent("start", notice, level="WARN"))

        refresh = None
        if self.config.refresh_playlist:
            refresh = self.refresh = PlaylistRefresh(
                self.config.destination, relay, candidates=recent_batches(self.history.runs))
        record = None
        try:
            # Closing Wallpaper Engine is how the return begins.
            self.tracker.enter("return")
            if refresh is not None:
                refresh.prepare()
            record = rotator.run(relay, cancelled=lambda: self._cancel,
                                 stop_after_step=lambda: self._stop,
                                 record_id=self.run_id, on_step=self.tracker.enter)
        except Exception as e:  # noqa: BLE001 — reported as the run's result
            self.error = f"Unexpected error: {e}"
            relay(ProgressEvent("error", self.error, level="ERROR"))
            record = rotator.record
        finally:
            if refresh is not None:
                self.tracker.enter("playlist")
                refresh.finish(completed=rotator.completed and not rotator.stopped_after)
        self.tracker.leave()
        finished = self.clock()

        self.record = record
        self.result = self._result(rotator, refresh)
        times = {"check": now_text(self.check_finished)} if self.check_finished else {}
        times.update(self.tracker.times)
        self.meta = RunMeta(
            started=now_text(started), finished=now_text(finished),
            seconds=round(max(0.0, (finished - started).total_seconds()), 1),
            result=self.result, batch=rotator.batch, protected=rotator.protected,
            returned_failed=list(rotator.returned_failed),
            moved_failed=list(rotator.moved_failed),
            playlist=self.playlist_summary,
            playlist_rebuilt=refresh.rebuilt if refresh is not None else None,
            playlist_problem=refresh.problems[0] if refresh and refresh.problems else "",
            history_reset=bool(record and record.history_reset),
            stopped_after=rotator.stopped_after, log=self.log_name, step_times=times)
        if rotator.recorded:
            try:
                record_meta(self.run_id, self.meta, self.meta_path)
            except OSError as e:
                self.meta_error = f"run_meta.json could not be written: {e}"
                relay(ProgressEvent("done", self.meta_error, level="WARN"))
        return record

    def _result(self, rotator: Rotator, refresh: PlaylistRefresh | None) -> str:
        if self.error or rotator.error:
            return "failed"
        if rotator.cancelled or rotator.stopped_after:
            return "stopped"
        if (rotator.record is not None and rotator.record.failed) or (
                refresh is not None and refresh.problems):
            return "problems"
        return "clean"


class RetryRun:
    """The failures of one recorded run, tried again in the steps they failed
    in, with the playlist rebuilt after when the run rebuilds it and anything
    moved. The run's history record and side-file entry are updated in place,
    and the retry is added to the entry's `retries`."""

    def __init__(self, config: Config, history: History, record: RunRecord, *,
                 progress: ProgressCallback = _noop, clock: Clock = datetime.now,
                 meta_path: Path | None = None):
        self.config = config
        self.history = history
        # The history's own record, so what the retry changes is what it saves.
        self.record = record = history.find(record.id) or record
        self.run_id = record.id
        self.clock = clock
        self.meta_path = meta_path
        self.rotator = Rotator(config, history)
        self.meta = RunMetaStore.load(meta_path).get(record.id) or RunMeta(
            log=run_log_name(record.id))
        returning, moving, self.unknown = self.rotator.split_failures(
            record, self.meta.returned_failed, self.meta.moved_failed)
        # What a retry can do: the names whose step the side file recorded.
        self.retryable = len(returning) + len(moving)
        self.steps = tuple(s for s, wanted in (
            (step("return"), bool(returning)), (step("move"), bool(moving)),
            (step("playlist"), config.refresh_playlist and bool(self.retryable))) if wanted)
        self.tracker = StepTracker(self.steps, progress, clock)
        self._cancel = False
        self.refresh: PlaylistRefresh | None = None
        self.outcome: RetryResult | None = None
        self.result = ""
        self.error = ""
        self.meta_error = ""

    @property
    def log_name(self) -> str:
        return run_log_name(self.run_id)

    def cancel(self) -> None:
        self._cancel = True

    def execute(self) -> RetryResult | None:
        relay = self.tracker.relay
        started = self.clock()
        problem = self.rotator.validate()
        if problem:
            self.result, self.error = "failed", problem
            relay(ProgressEvent("error", problem, level="ERROR"))
            return None
        if not self.record.failed:
            self.result = "clean"
            relay(ProgressEvent("done", "Nothing failed in this run — nothing to retry."))
            return RetryResult()
        if not self.retryable:
            # Nothing to move, so Wallpaper Engine is not closed for it either.
            self.result = "problems"
            relay(ProgressEvent("done", "Not retried: nothing recorded which step "
                                f"{', '.join(self.unknown)} failed in (a run from before "
                                "run_meta.json). Move them by hand.", level="WARN",
                                kind="skip"))
            return RetryResult(unknown=list(self.unknown), still_failed=list(self.record.failed))
        refresh = None
        if self.config.refresh_playlist:
            refresh = self.refresh = PlaylistRefresh(
                self.config.destination, relay, candidates=recent_batches(self.history.runs))
        outcome = None
        try:
            if self.steps:
                self.tracker.enter(self.steps[0].key)
            if refresh is not None:
                refresh.prepare()
            outcome = self.rotator.retry(
                self.record, self.meta.returned_failed, self.meta.moved_failed,
                relay, cancelled=lambda: self._cancel, on_step=self.tracker.enter)
        except Exception as e:  # noqa: BLE001 — reported as the retry's result
            self.error = f"Unexpected error: {e}"
            relay(ProgressEvent("error", self.error, level="ERROR"))
        finally:
            if refresh is not None:
                self.tracker.enter("playlist")
                refresh.finish(completed=outcome is not None and outcome.changed,
                               why="Nothing was moved, so the playlist was left as it was.")
        self.tracker.leave()
        finished = self.clock()
        self.outcome = outcome

        playlist_problem = bool(refresh is not None and refresh.problems)
        if self.error:
            self.result = "failed"
        elif outcome is not None and outcome.cancelled:
            self.result = "stopped"
        elif self.record.failed or playlist_problem:
            self.result = "problems"
        else:
            self.result = "clean"
        meta = self.meta
        if refresh is not None and refresh.rebuilt and not playlist_problem:
            meta.playlist = list(refresh.summary)
            meta.playlist_rebuilt = True
            meta.playlist_problem = ""
        elif playlist_problem:
            meta.playlist_problem = refresh.problems[0]
        # Nothing left of what made it a run with problems: it is clean now,
        # and `retries` says how it got there.
        if meta.result == "problems" and not self.record.failed and not meta.playlist_problem:
            meta.result = "clean"
        meta.log = meta.log or self.log_name
        meta.retries.append({
            "at": now_text(started), "seconds": round((finished - started).total_seconds(), 1),
            "retried": outcome.retried if outcome else 0,
            "fixed": outcome.fixed if outcome else 0,
            "resolved": outcome.resolved if outcome else 0,
            "failed": len(self.record.failed), "result": self.result})
        if self.history.find(self.record.id) is self.record:
            try:
                record_meta(self.record.id, meta, self.meta_path)
            except OSError as e:
                self.meta_error = f"run_meta.json could not be written: {e}"
                relay(ProgressEvent("done", self.meta_error, level="WARN"))
        return outcome


class PlaylistRebuild:
    """"Rebuild playlist now": close Wallpaper Engine, refill the rotation's
    playlist with what is in myprojects, start it again — the last step of a
    run, on its own. `runs` (the history, newest first) lets it find a playlist
    still made of an earlier batch, which is what a failed rebuild leaves."""

    def __init__(self, destination: str, *, runs=(), progress: ProgressCallback = _noop,
                 clock: Clock = datetime.now):
        self.destination = destination
        self.runs = list(runs)
        self.steps = (step("playlist"),)
        self.tracker = StepTracker(self.steps, progress, clock)
        self.refresh: PlaylistRefresh | None = None
        self.result = ""
        self.error = ""

    def execute(self) -> PlaylistRefresh | None:
        relay = self.tracker.relay
        problem = folder_problem("myprojects", self.destination)
        if problem is None and not Path(self.destination).is_dir():
            problem = f"Destination folder not found: {self.destination}"
        if problem:
            self.result, self.error = "failed", problem
            relay(ProgressEvent("error", problem, level="ERROR"))
            return None
        refresh = self.refresh = PlaylistRefresh(
            self.destination, relay, candidates=recent_batches(self.runs))
        self.tracker.enter("playlist")
        try:
            refresh.prepare()
        finally:
            refresh.finish(completed=True)
        self.tracker.leave()
        self.result = "problems" if refresh.problems else "clean"
        return refresh
