"""Core file-system logic: scanning, rotation, duplicate handling."""
from __future__ import annotations

import os
import random
import shutil
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from .config import Config, History, RunRecord, new_run_id


# ---- Progress event types -------------------------------------------------

class ProgressEvent:
    """One thing a piece of work did or is doing.

    `phase` is the engine's (scan, delete, return, select, move, playlist,
    done, stopped, cancelled, error, …); `current` / `total` the count within
    it (0 / 0 when there is none). `kind` is the log line's kind, in the
    LogPanel's words (moved, returned, dupe, skip, fail, error, warn, step,
    done, deleted, info); it is worked out from the message when not given.
    `step` is set by a run (`runner.RotationRun`): the index, in the run's
    `steps`, of the step the event belongs to — None outside a run.
    """

    def __init__(self, phase: str, message: str, current: int = 0, total: int = 0,
                 level: str = "INFO", *, kind: str = "", step: int | None = None):
        self.phase = phase
        self.message = message
        self.current = current
        self.total = total
        self.level = level
        self.kind = kind or _kind_of(phase, message, level)
        self.step = step

    def __repr__(self) -> str:
        return (f"ProgressEvent({self.phase!r}, {self.message!r}, {self.current}/{self.total}, "
                f"{self.level}, kind={self.kind!r}, step={self.step})")


def _kind_of(phase: str, message: str, level: str) -> str:
    """The log kind of an event that did not name one: the older emitters'."""
    if message.startswith("DUPLICATE"):
        return "dupe"
    if message.startswith(("SKIP", "Skipping")):
        return "skip"
    if level == "ERROR":
        return "fail" if message.startswith("Failed") else "error"
    if level == "WARN":
        return "warn"
    if message.startswith("Moved"):
        return "moved"
    if message.startswith("Returned"):
        return "returned"
    if message.startswith("Deleted"):
        return "deleted"
    return {"done": "done", "select": "step", "stopped": "stop",
            "cancelled": "stop"}.get(phase, "info")


def log_kind(e: ProgressEvent) -> str:
    """The LogPanel kind of an event: what its line in the run log is."""
    return e.kind


ProgressCallback = Callable[[ProgressEvent], None]
CancelCheck = Callable[[], bool]


# ---- The steps of a rotation -------------------------------------------------
#
# In the order the engine really runs them (REDESIGN_PLAN §6.2.1), which is not
# the order the design drew: the check for broken folders comes first and is
# optional (it is its own worker, with a confirmation between it and the rest);
# duplicates are set aside *during* the return, not after the move; the
# playlist is rebuilt last, and only with `Config.refresh_playlist`.

@dataclass(frozen=True)
class Step:
    key: str
    title: str
    phases: tuple[str, ...]         # the ProgressEvent phases that belong to it


STEPS: tuple[Step, ...] = (
    Step("check", "Check the folders for a project.json", ("scan", "delete")),
    Step("return", "Return the previous batch to the reserve", ("return",)),
    Step("move", "Draw and move the new batch in", ("select", "move")),
    Step("playlist", "Rebuild the playlist in Wallpaper Engine", ("playlist",)),
)
STEP_KEYS = tuple(s.key for s in STEPS)


def step(key: str) -> Step:
    return next(s for s in STEPS if s.key == key)


def run_steps(*, check: bool = True, playlist: bool = True) -> tuple[Step, ...]:
    """The steps one run goes through, in order: the check when the run began
    with one, the playlist when it rebuilds it. A ProgressEvent's `step` is an
    index into this."""
    return tuple(s for s in STEPS
                 if (s.key != "check" or check) and (s.key != "playlist" or playlist))


# ---- Folders that are not set -----------------------------------------------
#
# An empty setting is not "no folder". Path("") is Path("."), the working
# directory, and for the built exe that is usually the install folder: data\
# (the authors database, the Steam key, the tracker's history) and _internal\.
# With the duplicates folder left empty, the Duplicates tab listed those two as
# duplicates and "Delete all" deleted them. A relative path is the
# same hazard with a name on it. So a folder the Rotator lists or acts on is a
# full path, or it is not set, and nothing is listed, moved or deleted under it.

def folder_is_set(path: str | Path | None) -> bool:
    """Whether `path` names a folder of its own: not empty, and not relative."""
    text = "" if path is None else str(path).strip()
    return bool(text) and Path(text).is_absolute()


def folder_problem(label: str, path: str | Path | None) -> str | None:
    """Why `path` cannot be used as the `label` folder, or None when it can."""
    if folder_is_set(path):
        return None
    text = "" if path is None else str(path).strip()
    if not text:
        return f"The {label} folder is not set."
    return f"The {label} folder is not a full path: {text}"


def _child(base: Path, name: str) -> Path | None:
    """`base / name`, or None when `name` is not one folder name.

    A name with a drive or a separator in it would land outside `base` — an
    absolute one replaces it outright — and "", "." or ".." is `base` itself or
    its parent. None of those comes out of a folder listing.
    """
    if not name.strip(" .") or Path(name).name != name:
        return None
    return base / name


def _same_folder(a: str | Path, b: str | Path) -> bool:
    return (os.path.normcase(os.path.realpath(a))
            == os.path.normcase(os.path.realpath(b)))


# ---- Filesystem helpers ---------------------------------------------------

def list_subfolders(path: str | Path) -> list[str]:
    """Return names of immediate subdirectories. Empty list if the path is
    missing or not set — an unset folder is not the working directory."""
    if not folder_is_set(path):
        return []
    p = Path(path)
    if not p.exists():
        return []
    try:
        return sorted(
            (e.name for e in p.iterdir() if e.is_dir()),
            key=str.lower,
        )
    except OSError:
        return []


def folder_measure(path: str | Path) -> tuple[int, int]:
    """(bytes, files) under a folder, best-effort. Links and junctions are not
    followed. Reads the disk: off the GUI thread."""
    total = files = 0
    stack = [str(path)]
    while stack:
        try:
            entries = os.scandir(stack.pop())
        except OSError:
            continue
        with entries:
            for entry in entries:
                try:
                    if entry.is_symlink() or entry.is_junction():
                        continue            # never counted twice, never a loop
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
                    elif entry.is_file(follow_symlinks=False):
                        # On Windows the listing already holds the size.
                        total += entry.stat(follow_symlinks=False).st_size
                        files += 1
                except OSError:
                    pass
    return total, files


def folder_size(path: str | Path) -> int:
    """Total size in bytes (best-effort)."""
    return folder_measure(path)[0]


def _unique_target(folder: Path, name: str) -> Path:
    """A non-colliding path inside `folder` for `name`."""
    target = folder / name
    if not target.exists():
        return target
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return folder / f"{name}_{stamp}"


def _noop(_e: ProgressEvent) -> None:
    pass


def _never_cancel() -> bool:
    return False


# ---- Protected folders ------------------------------------------------------

# Folders in the destination whose name starts with this prefix are never
# touched by rotation: they are not returned to reserve, not treated as
# duplicates, and simply stay in myprojects across runs.
PROTECTED_PREFIX = "[protected]"


def is_protected(name: str) -> bool:
    return name.lower().startswith(PROTECTED_PREFIX)


# ---- Folders Wallpaper Engine can never show ------------------------------
#
# A wallpaper is identified by its project.json. Without one the folder cannot
# appear in the browser or in a playlist, so rotating it into myprojects only
# burns a slot — and silently: the playlist comes out short and nothing says
# why. Three shapes turn up in a real reserve. Folders holding nothing but
# Wallpaper Engine's own compiled shader cache, left behind when the wallpaper
# itself was removed. Folders left completely empty. And folders whose manifest
# is gone but whose media is still there — those are a wallpaper worth repairing
# rather than deleting, so they are reported apart from the other two and are
# not ticked by default in the confirmation.

MEDIA_SUFFIXES = {
    ".mp4", ".webm", ".mkv", ".avi", ".mov", ".m4v", ".wmv", ".flv",
    ".gif", ".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tga",
    ".pkg", ".html", ".exe", ".mp3", ".ogg", ".wav",
}
# How many entries of a broken folder to keep for the confirmation dialog.
ENTRY_CAP = 60
SHADER_DIR = "shaders"

REASON_EMPTY = "empty folder"
REASON_SHADERS = "only Wallpaper Engine's shader cache"
REASON_ORPHAN = "no project.json, but media inside"
REASON_NO_MANIFEST = "no project.json"


@dataclass
class BrokenFolder:
    """A folder Wallpaper Engine could never show, and why."""
    name: str
    reason: str
    root: str = ""                                     # which library it sits in
    entries: list[str] = field(default_factory=list)   # capped at ENTRY_CAP
    files: int = 0
    size: int = 0
    holds_media: bool = False

    @property
    def safe_to_delete(self) -> bool:
        """True when there is nothing inside that could still be a wallpaper."""
        return not self.holds_media

    @property
    def path(self) -> str:
        return str(Path(self.root) / self.name)

    @property
    def summary(self) -> str:
        return f"{self.files} file(s), {human_size(self.size)}"


def human_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} GB"


def has_manifest(folder: Path) -> bool:
    """Whether the folder holds a project.json.

    Read with scandir rather than a direct exists() on the file: the parent
    listing has just warmed the directory entries, which makes this about
    seventeen times faster over a reserve of tens of thousands of folders
    (measured: 0.9 s against 16.8 s for 33 423).
    """
    try:
        return any(e.name.lower() == "project.json" for e in os.scandir(folder))
    except OSError:
        return False


def inspect_folder(folder: Path, name: str) -> BrokenFolder | None:
    """Describe `folder` if it is unusable, or None when it is a real wallpaper."""
    if has_manifest(folder):
        return None
    entries: list[str] = []
    files = size = 0
    media = False
    only_shaders = True
    for root, _dirs, names in os.walk(folder):
        rel = Path(root).relative_to(folder)
        top = rel.parts[0] if rel.parts else None
        for n in names:
            files += 1
            if top != SHADER_DIR:
                only_shaders = False
            if Path(n).suffix.lower() in MEDIA_SUFFIXES:
                media = True
            try:
                size += (Path(root) / n).stat().st_size
            except OSError:
                pass
            if len(entries) < ENTRY_CAP:
                entries.append(n if top is None else str(rel / n))
    if files == 0:
        reason = REASON_EMPTY
    elif media:
        reason = REASON_ORPHAN
    elif only_shaders:
        reason = REASON_SHADERS
    else:
        reason = REASON_NO_MANIFEST
    return BrokenFolder(name=name, reason=reason, root=str(folder.parent),
                        entries=entries, files=files, size=size, holds_media=media)


def scan_invalid(root: str | Path, progress: ProgressCallback = _noop,
                 cancelled: CancelCheck = _never_cancel) -> list[BrokenFolder]:
    """Every folder under `root` that Wallpaper Engine could never show."""
    base = Path(root)
    names = list_subfolders(base)
    total = len(names)
    progress(ProgressEvent("scan", f"Checking {total} folders for a project.json...",
                           0, total))
    found: list[BrokenFolder] = []
    for i, name in enumerate(names, 1):
        if cancelled():
            progress(ProgressEvent("cancelled", "Cancelled — nothing was deleted.",
                                   i, total, level="WARN"))
            return found
        broken = inspect_folder(base / name, name)
        if broken is not None:
            found.append(broken)
            progress(ProgressEvent("scan", f"{name} — {broken.reason}", i, total,
                                   level="WARN"))
        elif i % 1000 == 0 or i == total:
            progress(ProgressEvent("scan", f"Checked {i} of {total}", i, total))
    progress(ProgressEvent("scan",
        f"{len(found)} of {total} folders cannot be shown by Wallpaper Engine.",
        total, total))
    return found


def delete_broken(paths: list[str], progress: ProgressCallback = _noop) -> list[str]:
    """Permanently delete the confirmed folders. Returns the paths that failed."""
    failed: list[str] = []
    total = len(paths)
    for i, path in enumerate(paths, 1):
        name = Path(path).name
        if not folder_is_set(path):
            # Only a scan of an unset library could produce one, and it would be
            # relative to the working directory.
            progress(ProgressEvent("delete", f"Not deleting {path}: not a full path",
                                   i, total, level="ERROR"))
            failed.append(path)
            continue
        if not Path(path).exists():
            # Already gone: removed by hand since the scan, or swept away with a
            # parent that was on the same list. The folder is not there, which
            # is what was asked for — not a failure to report.
            progress(ProgressEvent("delete", f"{name} was already gone", i, total))
            continue
        try:
            shutil.rmtree(path)
            progress(ProgressEvent("delete", f"Deleted {name}", i, total))
        except OSError as e:
            progress(ProgressEvent("delete", f"Failed to delete {name}: {e}", i, total,
                                   level="ERROR"))
            failed.append(path)
    return failed


# ---- The rotation ---------------------------------------------------------

@dataclass
class RetryResult:
    """What a retry of a run's failures came to."""
    retried: int = 0
    fixed: int = 0                  # moved where the failed step meant them to go
    resolved: int = 0               # no longer there to move: taken care of elsewhere
    returned: int = 0
    duplicates: list[str] = field(default_factory=list)
    moved: list[str] = field(default_factory=list)
    still_failed: list[str] = field(default_factory=list)
    # Failed in a step nothing recorded (a run from before run_meta.json):
    # left as they are rather than guessed at.
    unknown: list[str] = field(default_factory=list)
    cancelled: bool = False

    @property
    def changed(self) -> bool:
        """Whether anything moved, so myprojects is not what it was."""
        return bool(self.returned or self.duplicates or self.moved)


class Rotator:
    """One rotation: return myprojects to the reserve, then draw and move in.

    After `run()`:
    - `completed`: the move went through (even cut short by a cancel) and the
      run is recorded — what the playlist is rebuilt after.
    - `recorded`: the run is in the history. A run is recorded once it has
      moved anything, either way, whatever stopped it; one that touched
      nothing is not.
    - `stopped_after`: the step a stop between steps came after ("return" or
      "move"), or "".
    - `cancelled`: stopped part-way through a step (the per-folder cancel).
    - `error`: why it ended early, when something did.
    - `returned_failed` / `moved_failed`: the failures, by step, in the order
      of `record.failed`.
    """

    def __init__(self, config: Config, history: History):
        self.config = config
        self.history = history
        # Set once a run has gone all the way through and been recorded.
        self.completed = False
        self.recorded = False
        self.stopped_after = ""
        self.cancelled = False
        self.error = ""
        self.record: RunRecord | None = None
        self.protected = 0
        self.batch = 0
        self.returned_failed: list[str] = []
        self.moved_failed: list[str] = []

    def validate(self) -> Optional[str]:
        # All three, before anything else: an unset reserve or myprojects is the
        # working directory, whose folders would be carried off by the return
        # phase, and an unset duplicates folder is where duplicates would land.
        problem = (folder_problem("reserve", self.config.source)
                   or folder_problem("myprojects", self.config.destination)
                   or folder_problem("duplicates", self.config.duplicates))
        if problem:
            return problem
        # A history that could not be read and could not be moved aside would
        # be overwritten by this run's record.
        if self.history.problem:
            return self.history.problem
        if not Path(self.config.source).exists():
            return f"Source folder not found: {self.config.source}"
        if not Path(self.config.destination).exists():
            return f"Destination folder not found: {self.config.destination}"
        return None

    def preview(self) -> dict:
        """Return counts for a confirmation dialog without moving anything."""
        in_dest_all = list_subfolders(self.config.destination)
        protected = [n for n in in_dest_all if is_protected(n)]
        in_dest = [n for n in in_dest_all if not is_protected(n)]
        in_reserve = list_subfolders(self.config.source)
        reserve_names = {n.lower() for n in in_reserve}
        dup = [n for n in in_dest if n.lower() in reserve_names]
        used = self.history.used_set()
        # available after the return phase: reserve + the non-duplicate returns
        returning = [n for n in in_dest if n.lower() not in reserve_names]
        projected_reserve = len(in_reserve) + len(returning)
        # Counted by name, as run() draws: the history also remembers folders
        # used once and deleted since, and subtracting those from the total
        # made the count short, and could announce a reset that run() then
        # did not do.
        after_return = reserve_names | {n.lower() for n in returning}
        available_unique = len(after_return - used)
        return {
            "in_dest": len(in_dest),
            "protected": len(protected),
            "duplicates_expected": len(dup),
            "returning": len(returning),
            "reserve_now": len(in_reserve),
            "projected_reserve": projected_reserve,
            "used": len(used),
            "available_unique": max(available_unique, 0),
            "will_reset": available_unique < self.config.count,
            "count": self.config.count,
        }

    def run(self, progress: ProgressCallback = _noop,
            cancelled: CancelCheck = _never_cancel, *,
            stop_after_step: CancelCheck = _never_cancel,
            record_id: str | None = None,
            on_step: Callable[[str], None] | None = None) -> RunRecord:
        """Return, then draw and move in; record the run.

        `cancelled` is looked at before each folder, `stop_after_step` between
        the return and the draw, and after the move. `on_step(key)` is called
        as each step begins ("return", "move"). A run that had moved anything
        by the time it ended is recorded, however it ended — the folders it
        moved are where the history says, and the next run does not draw them
        again — and an exception is raised again after that.
        """
        # The worker validates first; this is for any caller that does not.
        problem = self.validate()
        if problem:
            raise ValueError(problem)
        step_to = on_step or (lambda _key: None)
        self.completed = self.recorded = self.cancelled = False
        self.stopped_after = self.error = ""
        self.returned_failed, self.moved_failed = [], []
        self.batch = self.config.count
        record = RunRecord(
            id=record_id or new_run_id(),
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        self.record = record
        try:
            Path(self.config.duplicates).mkdir(parents=True, exist_ok=True)
            step_to("return")
            if not self._return_phase(record, progress, cancelled):
                self._record_if_touched(record)
                return record
            if stop_after_step():
                self.stopped_after = "return"
                progress(ProgressEvent(
                    "stopped", "Stopped after the return, as asked — nothing new was "
                    "moved into myprojects.", level="WARN", kind="stop"))
                self._record_if_touched(record)
                return record
            step_to("move")
            if not self._move_phase(record, progress, cancelled):
                self._record_if_touched(record)
                return record
        except BaseException as err:
            self.error = self.error or f"{type(err).__name__}: {err}"
            try:
                self._record_if_touched(record)
            except Exception:  # noqa: BLE001 — the first error is the one to report
                pass
            raise

        progress(ProgressEvent("done",
            f"Done. Moved {record.moved_count}, returned {record.returned}, "
            f"duplicates {record.duplicate_count}, failed {len(record.failed)}."))
        self.history.add(record)
        self.recorded = self.completed = True
        if not self.cancelled and stop_after_step():
            self.stopped_after = "move"
            progress(ProgressEvent(
                "stopped", "Stopped after the move, as asked — the playlist was not "
                "rebuilt.", level="WARN", kind="stop"))
        return record

    def _record_if_touched(self, record: RunRecord) -> None:
        if any(r is record for r in self.history.runs):
            return          # added already; an error while saving it brought us back
        if record.moved or record.duplicates or record.returned or record.failed:
            self.history.add(record)
            self.recorded = True

    def _return_phase(self, record: RunRecord, progress: ProgressCallback,
                      cancelled: CancelCheck) -> bool:
        """Every folder in myprojects but the protected back to the reserve, or
        to the duplicates folder when the reserve has one of that name. False
        when cancelled part-way."""
        src, dst = Path(self.config.source), Path(self.config.destination)
        dup_dir = Path(self.config.duplicates)
        existing_all = list_subfolders(dst)
        protected = [n for n in existing_all if is_protected(n)]
        existing = [n for n in existing_all if not is_protected(n)]
        self.protected = len(protected)
        reserve_names = {n.lower() for n in list_subfolders(src)}
        total = len(existing)
        if protected:
            progress(ProgressEvent("return",
                f"Skipping {len(protected)} protected folder(s) "
                f"('{PROTECTED_PREFIX}' prefix) — left in myprojects.",
                0, total, level="WARN", kind="skip"))
        progress(ProgressEvent("return", f"Returning {total} folders from myprojects...",
                               0, total, kind="step"))
        for i, name in enumerate(existing, 1):
            if cancelled():
                self.cancelled = True
                progress(ProgressEvent("cancelled", "Cancelled during return phase.",
                                       level="WARN"))
                return False
            srcpath = dst / name
            if name.lower() in reserve_names:
                target = _unique_target(dup_dir, name)
                try:
                    shutil.move(str(srcpath), str(target))
                    record.duplicates.append(name)
                    progress(ProgressEvent("return",
                        f"DUPLICATE: {name} already in reserve -> duplicated_wallpapers",
                        i, total, level="WARN", kind="dupe"))
                except OSError as e:
                    self._failed(record, self.returned_failed, name)
                    progress(ProgressEvent("return", f"Failed to move duplicate {name}: {e}",
                                           i, total, level="ERROR", kind="fail"))
            else:
                try:
                    shutil.move(str(srcpath), str(src / name))
                    reserve_names.add(name.lower())
                    record.returned += 1
                    progress(ProgressEvent("return", f"Returned {name}", i, total,
                                           kind="returned"))
                except OSError as e:
                    self._failed(record, self.returned_failed, name)
                    progress(ProgressEvent("return", f"Failed to return {name}: {e}",
                                           i, total, level="ERROR", kind="fail"))
        left = len(self.returned_failed)
        parts = [f"{record.returned} folders returned to the reserve"]
        if record.duplicates:
            parts.append(f"{len(record.duplicates)} set aside as duplicates")
        if left:
            parts.append(f"{left} left behind")
        progress(ProgressEvent("return", ", ".join(parts), total, total, kind="step"))
        return True

    def _move_phase(self, record: RunRecord, progress: ProgressCallback,
                    cancelled: CancelCheck) -> bool:
        """Draw the batch and move it in. False when there was nothing to draw
        from; a cancel part-way ends the move but still counts as moved."""
        cfg = self.config
        src, dst = Path(cfg.source), Path(cfg.destination)
        all_folders = list_subfolders(src)
        if not all_folders:
            self.error = "No folders in reserve."
            progress(ProgressEvent("error", "No folders in reserve. Aborting.", level="ERROR"))
            return False

        used = self.history.used_set()
        available = [n for n in all_folders if n.lower() not in used]
        progress(ProgressEvent("select",
            f"Reserve: {len(all_folders)} | used: {len(used)} | unique available: {len(available)}"))

        if len(available) < cfg.count:
            progress(ProgressEvent("select",
                f"Only {len(available)} unique left (need {cfg.count}). Resetting history.",
                level="WARN"))
            record.history_reset = True
            available = all_folders

        pick = random.sample(available, min(cfg.count, len(available)))
        total = len(pick)
        progress(ProgressEvent("select", f"Selected {total} folders.", 0, total))
        progress(ProgressEvent("move", f"Moving {total} folders into myprojects...",
                               0, total, kind="step"))

        dest_names = {n.lower() for n in list_subfolders(dst)}
        for i, name in enumerate(pick, 1):
            if cancelled():
                self.cancelled = True
                progress(ProgressEvent("cancelled", "Cancelled during move phase.",
                                       level="WARN"))
                break
            if name.lower() in dest_names:
                self._failed(record, self.moved_failed, name)
                progress(ProgressEvent("move", f"SKIP: {name} already in myprojects.",
                                       i, total, level="WARN", kind="fail"))
                continue
            try:
                shutil.move(str(src / name), str(dst / name))
                record.moved.append(name)
                progress(ProgressEvent("move", f"Moved {name}", i, total, kind="moved"))
            except OSError as e:
                self._failed(record, self.moved_failed, name)
                progress(ProgressEvent("move", f"Failed to move {name}: {e}",
                                       i, total, level="ERROR", kind="fail"))
        parts = [f"{record.moved_count} folders moved into myprojects"]
        if self.moved_failed:
            parts.append(f"{len(self.moved_failed)} failed")
        progress(ProgressEvent("move", ", ".join(parts), total, total, kind="step"))
        return True

    @staticmethod
    def _failed(record: RunRecord, by_step: list[str], name: str) -> None:
        record.failed.append(name)
        by_step.append(name)

    # ---- retrying a run's failures -------------------------------------------

    def retry(self, record: RunRecord, returned_failed: list[str] | None = None,
              moved_failed: list[str] | None = None,
              progress: ProgressCallback = _noop, cancelled: CancelCheck = _never_cancel,
              on_step: Callable[[str], None] | None = None) -> RetryResult:
        """Try each name in `record.failed` again, in the step it failed in.

        A name that failed in the return goes back to the reserve (or to the
        duplicates folder, when the reserve has one of that name by now); one
        that failed in the move goes into myprojects. The side file says which
        (`returned_failed` / `moved_failed`). A name it does not place — a run
        from before it — is left alone: where the folder is now cannot tell a
        failed move from a failed return put right by hand, and guessing wrong
        would undo that. A name no longer where it failed — moved by hand, or
        deleted — is taken off the list, and the log says so.

        The record is updated in place — `failed` shrinks; `moved`,
        `duplicates` and `returned` grow — and the history is saved, in the
        same shape as ever. The two lists passed in become what still fails.
        """
        problem = self.validate()
        if problem:
            raise ValueError(problem)
        step_to = on_step or (lambda _key: None)
        returning, moving, unknown = self.split_failures(record, returned_failed,
                                                         moved_failed)
        result = RetryResult(retried=len(returning) + len(moving), unknown=unknown)
        if unknown:
            progress(ProgressEvent(
                "return" if returning or not moving else "move",
                f"Not retried, as nothing recorded which step they failed in: "
                f"{', '.join(unknown)}.", level="WARN", kind="skip"))
        cleared: list[tuple[str, str]] = []       # (step, name) no longer failing
        try:
            if returning:
                step_to("return")
                Path(self.config.duplicates).mkdir(parents=True, exist_ok=True)
                self._retry_returns(record, returning, cleared, result, progress, cancelled)
            if moving and not result.cancelled:
                step_to("move")
                self._retry_moves(record, moving, cleared, result, progress, cancelled)
        finally:
            left = Counter(cleared)
            still_returning = _without(returning, "return", left)
            still_moving = _without(moving, "move", left)
            still = Counter(still_returning) + Counter(still_moving) + Counter(unknown)
            failed = []
            for name in record.failed:
                if still[name] > 0:
                    still[name] -= 1
                    failed.append(name)
            record.failed = failed
            result.still_failed = list(failed)
            if returned_failed is not None:
                returned_failed[:] = still_returning
            if moved_failed is not None:
                moved_failed[:] = still_moving
            if result.retried and self.history.find(record.id) is record:
                self.history.save()
        return result

    @staticmethod
    def split_failures(record: RunRecord, returned_failed: list[str] | None,
                       moved_failed: list[str] | None
                       ) -> tuple[list[str], list[str], list[str]]:
        """record.failed, as (names to return, names to move in, names whose
        step nothing recorded)."""
        left = Counter(record.failed)
        returning: list[str] = []
        moving: list[str] = []
        for names, into in ((returned_failed or [], returning), (moved_failed or [], moving)):
            for name in names:
                if left[name] > 0:
                    left[name] -= 1
                    into.append(name)
        unknown = []
        for name in record.failed:
            if left[name] > 0:
                left[name] -= 1
                unknown.append(name)
        return returning, moving, unknown

    def _retry_returns(self, record, names, cleared, result, progress, cancelled) -> None:
        src, dst = Path(self.config.source), Path(self.config.destination)
        dup_dir = Path(self.config.duplicates)
        total = len(names)
        progress(ProgressEvent("return", f"Returning {total} folders that did not go back...",
                               0, total, kind="step"))
        for i, name in enumerate(names, 1):
            if cancelled():
                result.cancelled = True
                progress(ProgressEvent("cancelled", "Cancelled during the retry.", level="WARN"))
                return
            srcpath, home = _child(dst, name), _child(src, name)
            if srcpath is None or home is None:
                progress(ProgressEvent("return", f"Not a folder name: {name!r}", i, total,
                                       level="ERROR", kind="fail"))
                continue
            if not srcpath.exists():
                cleared.append(("return", name))
                result.resolved += 1
                where = "is back in the reserve" if home.exists() else "is no longer anywhere"
                progress(ProgressEvent("return", f"{name} {where} — nothing to retry.",
                                       i, total, level="WARN", kind="skip"))
                continue
            try:
                if home.exists():
                    shutil.move(str(srcpath), str(_unique_target(dup_dir, name)))
                    record.duplicates.append(name)
                    result.duplicates.append(name)
                    message, level, kind = (f"DUPLICATE: {name} already in reserve -> "
                                            "duplicated_wallpapers", "WARN", "dupe")
                else:
                    shutil.move(str(srcpath), str(home))
                    record.returned += 1
                    result.returned += 1
                    message, level, kind = f"Returned {name}", "INFO", "returned"
            except OSError as e:
                progress(ProgressEvent("return", f"Failed to return {name}: {e}", i, total,
                                       level="ERROR", kind="fail"))
                continue
            cleared.append(("return", name))
            result.fixed += 1
            progress(ProgressEvent("return", message, i, total, level=level, kind=kind))

    def _retry_moves(self, record, names, cleared, result, progress, cancelled) -> None:
        src, dst = Path(self.config.source), Path(self.config.destination)
        total = len(names)
        progress(ProgressEvent("move", f"Moving {total} folders that did not go in...",
                               0, total, kind="step"))
        for i, name in enumerate(names, 1):
            if cancelled():
                result.cancelled = True
                progress(ProgressEvent("cancelled", "Cancelled during the retry.", level="WARN"))
                return
            srcpath, target = _child(src, name), _child(dst, name)
            if srcpath is None or target is None:
                progress(ProgressEvent("move", f"Not a folder name: {name!r}", i, total,
                                       level="ERROR", kind="fail"))
                continue
            if target.exists():
                if srcpath.exists():
                    progress(ProgressEvent("move", f"SKIP: {name} already in myprojects.",
                                           i, total, level="WARN", kind="fail"))
                    continue
                # Moved in by hand since: it is where the run meant it to be.
                cleared.append(("move", name))
                result.resolved += 1
                record.moved.append(name)
                result.moved.append(name)
                progress(ProgressEvent("move", f"{name} is in myprojects already — "
                                       "counted as moved.", i, total, kind="skip"))
                continue
            if not srcpath.exists():
                cleared.append(("move", name))
                result.resolved += 1
                progress(ProgressEvent("move", f"{name} is no longer in the reserve — "
                                       "nothing to retry.", i, total, level="WARN", kind="skip"))
                continue
            try:
                shutil.move(str(srcpath), str(target))
            except OSError as e:
                progress(ProgressEvent("move", f"Failed to move {name}: {e}", i, total,
                                       level="ERROR", kind="fail"))
                continue
            cleared.append(("move", name))
            record.moved.append(name)
            result.moved.append(name)
            result.fixed += 1
            progress(ProgressEvent("move", f"Moved {name}", i, total, kind="moved"))


def _without(names: list[str], step_key: str, cleared: Counter) -> list[str]:
    """`names` less the ones cleared in this step, a name at a time."""
    still = []
    for name in names:
        if cleared[(step_key, name)] > 0:
            cleared[(step_key, name)] -= 1
        else:
            still.append(name)
    return still


# ---- Duplicate management (used by the Duplicates tab) --------------------

def delete_folders(duplicates_dir: str, names: list[str],
                   progress: ProgressCallback = _noop) -> list[str]:
    """Permanently delete the given folders from duplicates_dir. Returns failures.

    With duplicates_dir not set, nothing is touched and every name is a failure.
    """
    problem = folder_problem("duplicates", duplicates_dir)
    if problem:
        progress(ProgressEvent("delete", f"{problem} Nothing was deleted.",
                               level="ERROR"))
        return list(names)
    base = Path(duplicates_dir)
    failed = []
    total = len(names)
    for i, name in enumerate(names, 1):
        path = _child(base, name)
        if path is None:
            progress(ProgressEvent("delete", f"Not deleting {name!r}: not a folder name",
                                   i, total, level="ERROR"))
            failed.append(name)
            continue
        try:
            shutil.rmtree(path)
            progress(ProgressEvent("delete", f"Deleted {name}", i, total))
        except OSError as e:
            progress(ProgressEvent("delete", f"Failed to delete {name}: {e}", i, total,
                                   level="ERROR"))
            failed.append(name)
    return failed


def move_replace_to_reserve(duplicates_dir: str, reserve_dir: str, names: list[str],
                            progress: ProgressCallback = _noop) -> list[str]:
    """Move folders from duplicates back to reserve, replacing any existing. Returns failures.

    With either folder not set, or both the same folder — where "replacing"
    would delete the folder about to be moved — nothing is touched and every
    name is a failure.
    """
    problem = (folder_problem("duplicates", duplicates_dir)
               or folder_problem("reserve", reserve_dir))
    if problem is None and _same_folder(duplicates_dir, reserve_dir):
        problem = "The duplicates folder is the reserve itself."
    if problem:
        progress(ProgressEvent("replace", f"{problem} Nothing was moved.",
                               level="ERROR"))
        return list(names)
    dup = Path(duplicates_dir)
    reserve = Path(reserve_dir)
    failed = []
    total = len(names)
    for i, name in enumerate(names, 1):
        srcpath = _child(dup, name)
        target = _child(reserve, name)
        if srcpath is None or target is None:
            progress(ProgressEvent("replace", f"Not moving {name!r}: not a folder name",
                                   i, total, level="ERROR"))
            failed.append(name)
            continue
        try:
            if target.exists():
                shutil.rmtree(target)
            shutil.move(str(srcpath), str(target))
            progress(ProgressEvent("replace", f"Moved & replaced {name} -> reserve", i, total))
        except OSError as e:
            progress(ProgressEvent("replace", f"Failed for {name}: {e}", i, total, level="ERROR"))
            failed.append(name)
    return failed


@dataclass
class DuplicateFolder:
    """A folder in the duplicates folder, for the Duplicates dialog."""
    name: str
    size: int = 0
    files: int = 0
    modified: float = 0.0           # the folder's mtime, a timestamp
    in_reserve: bool = False        # a folder of this name is in the reserve now


def list_duplicates(duplicates_dir: str, reserve_dir: str = "",
                    progress: ProgressCallback = _noop,
                    cancelled: CancelCheck = _never_cancel) -> list[DuplicateFolder]:
    """What is in the duplicates folder, each with its size, sorted by name.

    Reads every file's size, on the disk the folders are on: run it on a
    worker. Nothing when the folder is not set — an unset folder is not the
    working directory. `in_reserve` says whether "Move & replace" would
    replace a folder in the reserve (only looked for when `reserve_dir` is set).
    """
    if not folder_is_set(duplicates_dir):
        return []
    base = Path(duplicates_dir)
    names = list_subfolders(base)
    reserve = ({n.lower() for n in list_subfolders(reserve_dir)}
               if folder_is_set(reserve_dir) else set())
    total = len(names)
    progress(ProgressEvent("list", f"Measuring {total} folders in the duplicates folder...",
                           0, total, kind="step"))
    found: list[DuplicateFolder] = []
    for i, name in enumerate(names, 1):
        if cancelled():
            progress(ProgressEvent("cancelled", "Stopped measuring.", i, total, level="WARN"))
            break
        path = base / name
        size, files = folder_measure(path)
        try:
            modified = path.stat().st_mtime
        except OSError:
            modified = 0.0
        found.append(DuplicateFolder(name, size, files, modified, name.lower() in reserve))
        progress(ProgressEvent("list", name, i, total))
    return found
