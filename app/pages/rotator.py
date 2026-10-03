"""Rotator: a batch of folders swapped between the reserve and myprojects.

What the page shows, by state:

- **idle** — on the left, the next run: the two folders it works between
  (set on the Settings page, shown here), the batch, how it draws, whether it
  rebuilds Wallpaper Engine's playlist, and "What a run does": its steps, each
  with what it will do this time (`Rotator.preview()`, worked out on a
  thread), and the button that starts it. On the right, one table with three
  views: the reserve, what is in myprojects now ("Current"), and the history
  of runs.
- **running** — the run itself: which step of how many, the count and the
  time left from the live rate, the folder it last moved, "Stop after this
  step"; its steps as they go, with what each did; and its log beside them.
- **done** — how it ended (clean, with problems, stopped, failed), what it
  did, and the way on: retry the failures, rebuild the playlist now, open the
  log, set up the next run. The table shows the history, the run marked.

A run is the engine's steps, in the engine's order (REDESIGN_PLAN §6.2.1):
the check for folders Wallpaper Engine cannot show (a worker of its own, and
the broken-folders confirmation after it), then the start confirmation, then
the rotation — the return (duplicates set aside on the way), the draw and the
move, and the playlist when the run rebuilds it.

Nothing here touches the reserve or myprojects on the window's thread: they
are tens of thousands of folders on a hard disk. They are counted, checked,
listed and measured on threads (`_Offload`, the engine's workers, the library
index), and the tables show only what those hand over. The Rotator's own
small files in the data folder — the history, its side file, a run's log —
are read where the old tab read them, on the window's thread, and never in
the constructor.

The words come from plain functions (`plan_rows`, `confirmation`,
`broken_groups`, `history_rows`, `done_steps`, `RunTracker`, …) that tests
call without building a widget.
"""
from __future__ import annotations

import copy
import csv
import io
import json
import os
import threading
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time as day_time, timedelta
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QCoreApplication, QObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QAbstractItemView, QFileDialog, QHBoxLayout, QSizePolicy, QStackedWidget, QVBoxLayout,
    QWidget,
)

from .. import external, theme
from ..engines import playlist_refresh
from ..engines.library_index import FolderInfo, LibraryIndex, LibraryIndexWorker, root_key
from ..engines.rotator import meta as rmeta
from ..engines.rotator.config import Config, History, RunRecord, Usage
from ..engines.rotator.core import (
    MEDIA_SUFFIXES, PROTECTED_PREFIX, REASON_EMPTY, REASON_SHADERS, STEP_KEYS, BrokenFolder, DuplicateFolder,
    ProgressEvent, Rotator, Step, folder_is_set, folder_problem, is_protected, list_subfolders,
    run_steps, step,
)
from ..engines.rotator.meta import RunMeta
from ..engines.rotator.worker import (
    CleanupWorker, DuplicateActionWorker, DuplicatesListWorker, PlaylistRebuildWorker,
    ReserveScanWorker, RetryWorker, RotationWorker,
)
from ..services import begin
from ..services.logstore import KEEP_DAYS, LogTail
from ..services.snapshot import LAST_RUN, PLAYLIST, moved_on
from ..ui.kit import (
    AccentButton, ActivityLine, Callout, CardTitle, Cell, CheckGroup, CheckRow, ChipCell, Column,
    ConfirmDialog, DangerButton, GhostButton, GlassPanel, Glyph, IconButton, IconDisc, LinkButton,
    LiveDot, LogPanel, MetricStrip, NavState, Overline, OverlayDialog, PathField, ProgressBar,
    ProgressRing, SecondaryButton, SegmentedControl, SpinBox, StepList, Table, TableBar,
    TableFooter, TableModel, TableSummary, TextInput, Toggle, chip_size, format as fmt, label,
)
from ..ui.kit.base import Elided, set_tone
from .base import Page, SideScroll

VIEWS = ("reserve", "current", "history")
VIEW_TITLES = ("Reserve", "Current", "History")
BATCH_RANGE = (1, 100_000)          # as the Settings page takes it
RESULTS = rmeta.RESULTS
STEP_COUNT_WORDS = {1: "one", 2: "two", 3: "three", 4: "four"}
FACTS_DELAY_MS = 300                # a burst of edits reads the folders once
REPAINT_MS = 250                    # sizes and titles arriving, shown together
SIZES_DELAY_MS = 200                # the rows on screen once scrolling settles
RENDER_MS = 80                      # a run's events, drawn together
PATH_SHOWN = 44                     # characters of a path in a table's summary

REFRESH_TIP = ("Wallpaper Engine is closed for the move and started again afterwards — a "
               "few seconds without wallpapers. The playlist is the one made of what is in "
               "myprojects now, found by its contents whatever it is called.")
CHECK_TIP = ("Look for folders without a project.json — Wallpaper Engine cannot show "
             "those, and rotating one in loses a slot in the playlist. Every run does this "
             "first.")
HISTORY_NOTE = "one row per run · newest first"


def _s(n: int) -> str:
    return "" if n == 1 else "s"


def _es(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def _now_text(when: datetime | None) -> str:
    return fmt.clock(when) if when is not None else ""


def _parse(text) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.strptime(str(text), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return rmeta.parse_time(text)


# ---- what the next run will do ------------------------------------------------------------

def plan_steps(refresh: bool) -> tuple[Step, ...]:
    """A run's steps in the engine's order: the check first, the playlist last
    when the run rebuilds it."""
    return run_steps(check=True, playlist=refresh)


def step_title(key: str, batch: int | None = None) -> str:
    """The step's name; the move's names the batch ("Move 1 000 new folders in")."""
    if key == "move" and batch:
        return f"Move {fmt.count(batch)} new folder{_s(batch)} in"
    return step(key).title


def drawn(p: dict) -> int:
    """How many folders the run draws: the batch, or all there are when fewer."""
    pool = p["projected_reserve"] if p["will_reset"] else p["available_unique"]
    return max(0, min(p["count"], pool))


def check_caption(p: dict) -> str:
    total = p["reserve_now"] + p["in_dest"] + p["protected"]
    return f"{fmt.counted(total, 'folder')} looked at before anything moves"


def return_caption(p: dict) -> str:
    going, dupes = p["returning"], p["duplicates_expected"]
    if not going and not dupes:
        return "myprojects is empty: nothing goes back"
    parts = []
    if going:
        parts.append(f"{fmt.counted(going, 'folder')} {_es(going, 'goes', 'go')} back")
    if dupes:
        parts.append(f"{fmt.count(dupes)} duplicate{_s(dupes)} set aside")
    return " · ".join(parts)


def move_caption(p: dict) -> str:
    n = drawn(p)
    if p["will_reset"]:
        return (f"{fmt.count(n)} drawn from all {fmt.count(p['projected_reserve'])} · "
                "the history resets")
    return f"{fmt.count(n)} drawn from {fmt.count(p['available_unique'])} never used"


def playlist_caption(labels) -> str:
    """What the playlist step will do: `labels` are the playlists it rebuilds
    (None: not looked at, [] none found)."""
    if labels is not None and not labels:
        return "no playlist is made of myprojects now: none to rebuild"
    restarts = "Wallpaper Engine restarts once"
    return f"{', '.join(labels)} · {restarts}" if labels else restarts


def plan_captions(p: dict | None, labels=None) -> dict[str, str]:
    """Each step's caption for the next run, from `Rotator.preview()`; "" for
    all while it is being counted."""
    if p is None:
        return {key: "" for key in STEP_KEYS}
    return {"check": check_caption(p), "return": return_caption(p), "move": move_caption(p),
            "playlist": playlist_caption(labels)}


def plan_rows(p: dict | None, *, batch: int, refresh: bool, labels=None) -> list[tuple[str, str]]:
    """"What a run does": (title, caption) for each of the next run's steps."""
    captions = plan_captions(p, labels)
    return [(step_title(s.key, batch), captions[s.key]) for s in plan_steps(refresh)]


def selection_line(p: dict) -> tuple[str, str]:
    """How the next run draws (one strategy, gate G9 A): ("", words), or
    ("warn", words) when it will start the history over."""
    if p["will_reset"]:
        return ("warn", f"Only {fmt.count(p['available_unique'])} unused left — history "
                        "resets and all folders are eligible")
    return ("", f"Drawn at random from {fmt.count(p['available_unique'])} never used")


def protected_line(p: dict) -> str:
    """"2 [protected] folders stay in myprojects"; "" when there are none."""
    n = p["protected"]
    if not n:
        return ""
    return f"{fmt.count(n)} [protected] folder{_s(n)} {_es(n, 'stays', 'stay')} in myprojects"


def estimate_line(seconds: float | None) -> str:
    """Under the Start button: the time runs like it took, when there were any."""
    lead = f"Takes about {fmt.duration(seconds, exact=False)}. " if seconds else ""
    return lead + "You can keep using the app."


@dataclass(frozen=True)
class NextRunFacts:
    """What the next run would do, read off the window's thread."""
    problem: str | None = None                  # why it cannot start (Rotator.validate)
    preview: dict | None = None                 # Rotator.preview()
    labels: tuple[str, ...] | None = None       # the playlists it rebuilds; None: not asked
    estimate: float | None = None               # seconds, from runs like it
    duplicates: int | None = None               # folders in the duplicates folder; None: unset
    error: str = ""


def read_facts(config: Config, history: History, *, labels: bool = True) -> NextRunFacts:
    """Lists the reserve and myprojects and reads Wallpaper Engine's config:
    a thread's work, never the window's."""
    try:
        rotator = Rotator(config, history)
        problem = rotator.validate()
        preview = rotator.preview()
        found = None
        if labels and config.refresh_playlist and folder_is_set(config.destination):
            found = tuple(playlist_refresh.preview(config.destination))
        estimate = rmeta.estimate_seconds(config.count, runs=list(history.runs))
        dupes = (len(list_subfolders(config.duplicates)) if folder_is_set(config.duplicates)
                 else None)
        return NextRunFacts(problem, preview, found, estimate, dupes)
    except Exception as err:  # noqa: BLE001 — shown, not raised
        return NextRunFacts(error=f"{type(err).__name__}: {err}")


# ---- the start confirmation (frame 08) ---------------------------------------------------------

@dataclass(frozen=True)
class Confirmation:
    title: str
    body: str
    steps: tuple[tuple[str, str], ...]
    notes: tuple[tuple[str, str, str], ...]     # (tone, icon, words)
    confirm: str = "Start run"


def confirmation(p: dict, number: int, *, refresh: bool, labels=None,
                 duplicates_folder: str = "", notices=()) -> Confirmation:
    """What "Start run N?" says, from `Rotator.preview()` taken after the check:
    the steps still to come in the engine's order, and the facts that are not
    options — the [protected] folders, the duplicates, a history reset."""
    batch, home, available = p["count"], p["in_dest"], p["available_unique"]
    n = drawn(p)
    source = (f"all {fmt.count(p['projected_reserve'])}, as fewer than {fmt.count(batch)} "
              "were never used" if p["will_reset"]
              else f"the {fmt.count(available)} never used")
    if home:
        body = (f"The {fmt.counted(home, 'folder')} now in myprojects "
                f"{_es(home, 'goes', 'go')} back to the reserve first, then {fmt.count(n)} "
                f"new one{_s(n)} {_es(n, 'is', 'are')} drawn at random from {source}.")
    else:
        body = (f"myprojects holds nothing to return, so {fmt.counted(n, 'folder')} "
                f"{_es(n, 'is', 'are')} drawn at random from {source}.")
    move = move_caption(p)
    move += f" · the reserve holds {fmt.count(p['projected_reserve'])} after the return"
    steps = [(step_title("return"), return_caption(p)), (step_title("move", batch), move)]
    if refresh:
        steps.append((step_title("playlist"), playlist_caption(labels)))
    notes = []
    if p["protected"]:
        notes.append(("neutral", "lock", f"{protected_line(p)}, untouched."))
    dupes = p["duplicates_expected"]
    if dupes:
        where = duplicates_folder or "the duplicates folder"
        notes.append(("neutral", "info",
                      f"{fmt.count(dupes)} of them {_es(dupes, 'is', 'are')} in the reserve "
                      f"already: {_es(dupes, 'it goes', 'they go')} to {where}, not back into "
                      "the reserve."))
    if p["will_reset"]:
        notes.append(("warn", "warn", f"Only {fmt.count(available)} unused left — the history "
                                      "resets and every folder can be drawn again."))
    for notice in notices:
        if notice:
            notes.append(("warn", "warn", notice))
    return Confirmation(f"Start run {number}?", body, tuple(steps), tuple(notes))


# ---- the broken folders (frame 09) ---------------------------------------------------------------

_VIDEO = {".mp4", ".webm", ".mkv", ".avi", ".mov", ".m4v", ".wmv", ".flv"}
_IMAGE = {".gif", ".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tga"}


def broken_reason(b: BrokenFolder) -> str:
    """Why a folder is listed, in a row's words: what is (or is not) in it."""
    if b.reason == REASON_EMPTY:
        return "no files at all"
    if b.reason == REASON_SHADERS:
        return "only Wallpaper Engine's shader cache"
    if not b.holds_media:
        return f"no project.json, no media · {fmt.counted(b.files, 'file')}"
    if b.files > len(b.entries):
        return f"no project.json · {fmt.counted(b.files, 'file')}, media among them"
    kinds = Counter()
    for entry in b.entries:
        suffix = Path(entry).suffix.lower()
        if suffix in _VIDEO:
            kinds["video"] += 1
        elif suffix in _IMAGE:
            kinds["image"] += 1
        elif suffix in MEDIA_SUFFIXES:
            kinds["other media file"] += 1
    what = ", ".join(f"{fmt.count(n)} {word}{_s(n)}" for word, n in kinds.items())
    return f"no project.json · {what} inside" if what else "no project.json"


def broken_groups(broken: list[BrokenFolder]) -> list[CheckGroup]:
    """The checklist: what is safe to delete, ticked; what still holds media,
    in warn and not ticked — those are wallpapers that lost their manifest
    rather than rubbish, and are looked at before they go."""
    def rows(found):
        return [CheckRow(b.name, broken_reason(b), b.size, b.path)
                for b in sorted(found, key=lambda b: b.name.casefold())]

    safe = [b for b in broken if b.safe_to_delete]
    media = [b for b in broken if not b.safe_to_delete]
    groups = []
    if safe:
        groups.append(CheckGroup("Safe to delete", rows(safe), tone="ok", note="no media inside"))
    if media:
        groups.append(CheckGroup("Holds media", rows(media), tone="warn",
                                 initially_checked=False, note="check before deleting"))
    return groups


def broken_title(broken: list[BrokenFolder], where: str) -> str:
    n = len(broken)
    return f"{fmt.count(n)} broken folder{_s(n)} found in {where}"


def broken_body(broken: list[BrokenFolder], scanned: int = 0) -> str:
    safe = sum(1 for b in broken if b.safe_to_delete)
    media = len(broken) - safe
    checked = f"Of the {fmt.counted(scanned, 'folder')} checked, these" if scanned else "These"
    text = (f"{checked} have no project.json, so Wallpaper Engine can never list or show "
            "them.")
    if safe:
        text += (f" {fmt.count(safe)} {_es(safe, 'holds', 'hold')} no media and "
                 f"{_es(safe, 'is', 'are')} safe to remove.")
    if media:
        text += (f" {fmt.count(media)} still {_es(media, 'holds', 'hold')} video, images or "
                 "other media — look inside before deciding.")
    return text


PERMANENT = "Deleting is permanent — these folders do not go to the Recycle Bin."


# ---- the history ------------------------------------------------------------------------------

@dataclass(frozen=True)
class HistoryRow:
    number: int
    id: str
    started: datetime | None
    finished: datetime | None
    seconds: float | None
    result: str                 # clean / problems / stopped / failed
    moved: int
    returned: int
    duplicates: int
    failed: int
    batch: int | None
    log: str                    # "rotator/run-<id>.log"; "" when the run kept none
    from_side_file: bool        # the result and times are recorded, not worked out
    record: RunRecord = field(compare=False, repr=False)


def history_rows(runs, metas: dict[str, RunMeta]) -> list[HistoryRow]:
    """One row per run, newest first, numbered from the oldest. A run from
    before run_meta.json has no times beyond its start and no log; its result
    is worked out from its record (a failure means problems)."""
    rows = []
    total = len(runs)
    for i, record in enumerate(runs):
        meta = metas.get(record.id)
        side = meta is not None and meta.result in RESULTS
        result = meta.result if side else ("problems" if record.failed else "clean")
        rows.append(HistoryRow(
            number=total - i, id=record.id,
            started=(meta.started_at if meta else None) or _parse(record.timestamp),
            finished=meta.finished_at if meta else None,
            seconds=meta.seconds if meta else None, result=result,
            moved=record.moved_count, returned=record.returned,
            duplicates=record.duplicate_count, failed=len(record.failed),
            batch=(meta.batch or None) if meta else None, log=meta.log if meta else "",
            from_side_file=side, record=record))
    return rows


def result_words(result: str, failed: int) -> tuple[str, str]:
    """RESULT's words and tone: clean in ok, "2 problems" in warn, stopped and
    failed in danger."""
    if result == "clean":
        return "clean", "ok"
    if result == "problems":
        return (f"{fmt.count(failed)} problem{_s(failed)}" if failed else "problems"), "warn"
    return ("stopped" if result == "stopped" else "failed"), "danger"


def history_summary(rows: list[HistoryRow]) -> list[str]:
    """"39 runs · 37 clean · 2 with problems · logs kept 30 days"."""
    counts = Counter(r.result for r in rows)
    items = [fmt.counted(len(rows), "run"), f"{fmt.count(counts['clean'])} clean"]
    if counts["problems"]:
        items.append(f"{fmt.count(counts['problems'])} with problems")
    for result in ("stopped", "failed"):
        if counts[result]:
            items.append(f"{fmt.count(counts[result])} {result}")
    items.append(f"logs kept {KEEP_DAYS} days")
    return items


CSV_COLUMNS = ("run", "id", "started", "finished", "took_seconds", "result", "moved",
               "returned", "duplicates", "failed", "batch", "log")


def history_csv(rows: list[HistoryRow]) -> str:
    """The history as CSV, a row per run, newest first; what is not known is
    left empty."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for r in rows:
        writer.writerow([
            r.number, r.id, r.started.isoformat(sep=" ") if r.started else "",
            r.finished.isoformat(sep=" ") if r.finished else "",
            "" if r.seconds is None else f"{r.seconds:g}", r.result, r.moved, r.returned,
            r.duplicates, r.failed, r.batch or "", r.log])
    return out.getvalue()


def record_lines(record: RunRecord) -> list[tuple[str, str, str]]:
    """A run whose log was not kept, as the history recorded it: the folders
    it moved in, set aside and could not move, as log lines without times."""
    lines = [("", "moved", name) for name in record.moved]
    lines += [("", "dupe", f"{name} — set aside as a duplicate") for name in record.duplicates]
    lines += [("", "fail", f"{name} — did not move") for name in record.failed]
    return lines


# ---- a finished run, step by step ------------------------------------------------------------------

StepRow = tuple[str, str, str, "str | None"]        # title, caption, state, caption tone


def _parts_with_time(parts: list[str], when: datetime | None) -> str:
    if when is not None:
        parts = parts + [fmt.clock(when)]
    return " · ".join(parts)


def check_parts(broken: int | None, deleted: int = 0) -> list[str]:
    if broken is None:
        return ["checked"]
    parts = [f"{fmt.count(broken)} broken" if broken else "none broken"]
    if deleted:
        parts.append(f"{fmt.count(deleted)} deleted")
    return parts


def return_parts(returned: int, dupes: int, left: int) -> list[str]:
    parts = [f"{fmt.count(returned)} returned"]
    if dupes:
        parts.append(f"{fmt.count(dupes)} set aside")
    if left:
        parts.append(f"{fmt.count(left)} left behind")
    return parts


def move_parts(moved: int, failed: int, reset: bool = False) -> list[str]:
    parts = [f"{fmt.count(moved)} moved"]
    if failed:
        parts.append(f"{fmt.count(failed)} failed")
    if reset:
        parts.append("history reset")
    return parts


def done_steps(record: RunRecord, meta: RunMeta | None, *, check: tuple[int, int] | None = None,
               rebuilt: bool = False) -> list[StepRow]:
    """A finished run's steps, with what each did and when it ended, from its
    record and its side-file entry. `check` is (broken, deleted) of the check
    that began it, when this window saw it; `rebuilt` that "Rebuild playlist
    now" has put the playlist right since."""
    times = meta.step_times if meta is not None else {}
    has_check = check is not None or "check" in times
    has_playlist = meta is not None and meta.playlist_rebuilt is not None
    batch = (meta.batch if meta is not None and meta.batch else None) or record.moved_count
    result = meta.result if meta is not None else ("problems" if record.failed else "clean")
    stopped_after = meta.stopped_after if meta is not None else ""
    failed_at = ""
    if result == "failed":
        failed_at = "move" if (record.returned or record.duplicates or record.moved) else "return"
    rows: list[StepRow] = []
    for s in run_steps(check=has_check, playlist=has_playlist):
        when = meta.step_time(s.key) if meta is not None else None
        title = step_title(s.key, batch)
        if s.key == "check":
            broken, deleted = check if check is not None else (None, 0)
            rows.append((title, _parts_with_time(check_parts(broken, deleted), when), "done", None))
        elif s.key == "return":
            left = len(meta.returned_failed) if meta is not None else 0
            state = "failed" if failed_at == "return" else "done"
            rows.append((title, _parts_with_time(return_parts(
                record.returned, record.duplicate_count, left), when), state,
                "warn" if left and state == "done" else None))
        elif s.key == "move":
            if stopped_after == "return" or failed_at == "return":
                why = "stopped after the return" if stopped_after else "not reached"
                rows.append((title, f"not run — {why}", "pending", None))
                continue
            failed = len(meta.moved_failed) if meta is not None else len(record.failed)
            state = "failed" if failed_at == "move" else "done"
            rows.append((title, _parts_with_time(move_parts(
                record.moved_count, failed, record.history_reset), when), state,
                "warn" if failed and state == "done" else None))
        else:
            if rebuilt:
                rows.append((title, "rebuilt since, with “Rebuild playlist now”", "done", None))
            elif meta.playlist_problem:
                rows.append((title, meta.playlist_problem, "failed", None))
            elif meta.playlist_rebuilt:
                rows.append((title, _parts_with_time(["rebuilt"], when), "done", None))
            else:
                rows.append((title, _parts_with_time(["left as it was"], when), "done",
                             "warn" if stopped_after == "move" else None))
    return rows


def playlist_stale(meta: RunMeta | None, rebuilt: bool = False) -> bool:
    """Whether Wallpaper Engine is still playing the playlist from before the
    run: the run was to rebuild it and did not (it failed to, or the run
    stopped or failed first), and nothing has rebuilt it since."""
    return meta is not None and not rebuilt and meta.playlist_rebuilt is False


def result_title(number: int, result: str, problems: int, stopped_after: str = "") -> str:
    if result == "clean":
        return f"Run {number} finished cleanly"
    if result == "problems":
        return (f"Run {number} finished with {fmt.count(problems)} problem{_s(problems)}"
                if problems else f"Run {number} finished with problems")
    if result == "stopped":
        return f"Run {number} was stopped" + (f" after the {stopped_after}" if stopped_after else "")
    return f"Run {number} failed"


def result_sentence(record: RunRecord, meta: RunMeta | None, *, error: str = "",
                    rebuilt: bool = False) -> str:
    """The result panel's sentence: what moved, what did not, what became of
    the playlist."""
    moved, returned = record.moved_count, record.returned
    failed = len(record.failed)
    stopped_after = meta.stopped_after if meta is not None else ""
    if error:
        head = error if error.endswith(".") else f"{error}."
        return (f"{head} By then {fmt.count(moved)} folder{_s(moved)} had moved in and "
                f"{fmt.count(returned)} gone back.")
    if stopped_after == "return":
        return (f"{fmt.counted(returned, 'folder')} went back to the reserve; nothing new was "
                "moved in, and the playlist was left as it was.")
    if moved and moved == returned and not failed:
        text = f"{fmt.counted(moved, 'folder')} swapped, nothing left behind."
    else:
        text = f"{fmt.counted(moved, 'folder')} moved in, {fmt.count(returned)} returned."
    if failed:
        text += (f" {fmt.count(failed)} folder{_s(failed)} could not be moved and "
                 f"stayed where {_es(failed, 'it was', 'they were')}.")
    if meta is None or meta.playlist_rebuilt is None:
        return text
    if rebuilt:
        return text + " The playlist has been rebuilt since."
    if meta.playlist_rebuilt and not meta.playlist_problem:
        when = meta.step_time("playlist")
        line = next((l for l in meta.playlist if l.startswith("Playlist rebuilt")), "")
        wallpapers = line.split(" wallpapers", 1)[0].rsplit(" ", 1)[-1] if line else ""
        at = f" at {fmt.clock(when)}" if when else ""
        count = f" with {fmt.count(int(wallpapers))} wallpapers" if wallpapers.isdigit() else ""
        return text + f" Wallpaper Engine's playlist was rebuilt{count}{at}."
    if stopped_after == "move":
        return text + " Stopped after the move, as asked: the playlist was not rebuilt."
    return text + " The playlist did not rebuild."


def tracker_note(playlist, now: datetime) -> str:
    """"The Tracker is counting again — 201 on the playlist, time to rotate
    ≈28 Sep." Nothing when the Tracker has not counted the new playlist."""
    if playlist is None or playlist.total <= 0:
        return ""
    from ..services.snapshot import parse_estimate
    text = f"The Tracker is counting again — {fmt.count(playlist.total)} on the playlist"
    when = parse_estimate(playlist.finish_estimate, now)
    if when is not None:
        text += f", time to rotate {fmt.estimate_day(when, now)}"
    return text + "."


# ---- the run under way -----------------------------------------------------------------------------

class RunTracker:
    """What the page shows of a piece of work while it runs, from its events.

    `kind` is "run" (a rotation, the check first), "check" (the check on its
    own), "retry" or "rebuild". `steps` are the engine's (`run_steps`, a
    worker's `steps`); an event's `step` is an index into them. `state` is
    "running" until `finish(result)` makes it the result.
    """

    def __init__(self, kind: str, steps, *, number: int = 0, batch: int = 0,
                 started: datetime | None = None):
        self.kind = kind
        self.steps: tuple[Step, ...] = tuple(steps)
        self.number = number
        self.batch = batch
        self.started = started
        self.index: int | None = None
        self.states = ["pending"] * len(self.steps)
        self.ended: dict[int, datetime] = {}
        self.done = self.total = 0
        self.phase = ""
        self.moved = self.returned = self.dupes = self.failed = 0
        self.fails = [0] * len(self.steps)
        self.broken: int | None = None
        self.deleted = 0
        self.activity = ""
        self.playlist_problems: list[str] = []
        self.rebuilt = False
        self.restarted = False
        self.stopping = False
        self.error = ""
        self.state = "running"

    # -- the events

    def event(self, e: ProgressEvent, now: datetime) -> None:
        if e.step is not None and 0 <= e.step < len(self.steps) and e.step != self.index:
            self.enter(e.step, now)
        if e.phase not in ("cancel", "cancelled", "stopped", "done", "start", "error"):
            self.phase = e.phase
        if e.total:
            self.done, self.total = e.current, e.total
        kind = e.kind
        if kind == "moved":
            self.moved += 1
            self.activity = f"{e.message.removeprefix('Moved ')} → myprojects"
        elif kind == "returned":
            self.returned += 1
            self.activity = f"{e.message.removeprefix('Returned ')} → reserve"
        elif kind == "dupe":
            self.dupes += 1
            name = e.message.removeprefix("DUPLICATE: ").split(" already", 1)[0]
            self.activity = f"{name} → duplicates"
        elif kind == "fail":
            self.failed += 1
            if self.index is not None:
                self.fails[self.index] += 1
            self.activity = e.message
        elif kind == "deleted":
            self.deleted += 1
            self.activity = e.message
        elif e.phase == "error":
            self.error = e.message
        elif e.phase in ("scan", "playlist", "delete") and e.message:
            self.activity = e.message
        if e.phase == "playlist":
            if e.level != "INFO":
                self.playlist_problems.append(e.message)
            if e.message.startswith("Playlist rewritten"):
                self.rebuilt = True
            elif e.message.startswith("Wallpaper Engine is running again"):
                self.restarted = True

    def enter(self, i: int, now: datetime) -> None:
        if self.index is not None and self.index != i and self.states[self.index] == "active":
            self.states[self.index] = "done"
            self.ended[self.index] = now
        self.index = i
        self.states[i] = "active"
        self.done = self.total = 0

    def finish(self, result: str, now: datetime) -> None:
        """The work ended: the step under way is done, or failed with it."""
        if self.index is not None and self.states[self.index] == "active":
            self.states[self.index] = "failed" if result == "failed" else "done"
            self.ended[self.index] = now
        self.state = result

    # -- what it says

    def key(self) -> str | None:
        return self.steps[self.index].key if self.index is not None else None

    def sentence(self) -> str:
        """What is going on, as the run panel says it."""
        key, total = self.key(), self.total
        if key == "check":
            if self.phase == "delete":
                return f"Deleting the {fmt.counted(total, 'folder')} you confirmed"
            return "Checking every folder for a project.json"
        if key == "return":
            return (f"Returning {fmt.counted(total, 'folder')} to the reserve" if total
                    else "Returning the previous batch to the reserve")
        if key == "move":
            if self.phase == "select" or not total:
                return "Drawing the new batch at random"
            return f"Moving {fmt.counted(total, 'folder')} into myprojects"
        if key == "playlist":
            return "Rebuilding the playlist in Wallpaper Engine"
        return "Starting…"

    def status_text(self) -> str:
        """The status line's words: "step 2 of 4 — moving 1 000 folders into myprojects"."""
        words = self.sentence()
        words = words[:1].lower() + words[1:]
        if self.index is not None and len(self.steps) > 1:
            return f"step {self.index + 1} of {len(self.steps)} — {words}"
        return words

    def title(self) -> str:
        if self.kind == "check":
            return "Checking folders"
        if self.kind == "rebuild":
            return "Rebuilding the playlist"
        lead = f"Run {self.number}" if self.number else "Run"
        if self.kind == "retry":
            return f"{lead} · retrying"
        if self.index is None:
            return lead
        return f"{lead} · step {self.index + 1} of {len(self.steps)}"

    def step_rows(self, left: str | None = None) -> list[StepRow]:
        rows: list[StepRow] = []
        for i, s in enumerate(self.steps):
            title = step_title(s.key, self.batch)
            state = self.states[i]
            if state == "pending":
                rows.append((title, "waiting", "pending", None))
                continue
            if state == "active":
                if self.total:
                    words = fmt.ratio(self.done, self.total)
                    rows.append((title, f"{words} · {left}" if left else words, "active", None))
                else:
                    rows.append((title, "working", "active", None))
                continue
            when = self.ended.get(i)
            tone = None
            if s.key == "check":
                parts = check_parts(self.broken, self.deleted)
            elif s.key == "return":
                parts = return_parts(self.returned, self.dupes, self.fails[i])
                tone = "warn" if self.fails[i] else None
            elif s.key == "move":
                parts = move_parts(self.moved, self.fails[i])
                tone = "warn" if self.fails[i] else None
            else:
                if self.playlist_problems:
                    rows.append((title, self.playlist_problems[0], "failed", None))
                    continue
                parts = [w for w, on in (("rebuilt", self.rebuilt), ("restarted", self.restarted))
                         if on] or ["left as it was"]
            if state == "failed":
                rows.append((title, self.error or "failed", "failed", None))
            else:
                rows.append((title, _parts_with_time(parts, when), state, tone))
        return rows

    def metrics(self) -> list[tuple]:
        return [(self.moved, "moved in"), (self.returned, "returned"),
                (self.dupes, "duplicates"),
                (self.failed, "problems", "warn" if self.failed else None)]


# ---- the sidebar and the header ----------------------------------------------------------------

def idle_nav(jobs, snapshot) -> NavState:
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
        words = f"{n} problem{_s(n)}" if n else "problems"
        return NavState.status(words, "warn", below=True)
    if last.result == "failed":
        return NavState.status(f"run {last.number} failed", "danger", below=True)
    finished = jobs.last_finished("rotator")
    if (last.result == "clean" and finished is not None and finished.result == "clean"
            and finished.title == f"Run {last.number}"):
        return NavState.status(f"clean · run {last.number}", "ok", below=True)
    return NavState.status(f"ready · run {last.number + 1}", below=True)


def nav_state(jobs, snapshot) -> NavState:
    """The Rotator's sidebar item: the job's bar and percentage while one runs
    ("41%"), "working" while it is uncounted, else what the last run left."""
    running = [job for job in jobs.running() if job.tool == "rotator"]
    if running:
        job = running[0]
        if job.total > 0:
            return NavState.progress(job.done, job.total)
        return NavState.status("working", below=True)
    return idle_nav(jobs, snapshot)


def idle_subtitle(when: datetime | None, result: str | None, now: datetime, *,
                  ended: bool = True) -> str:
    """"Nothing running · last run finished 13:58". `ended=False`: `when` is
    only when it started (a run from before run_meta.json)."""
    if when is None or result is None:
        return "Nothing running · no run yet"
    if not ended:
        return f"Nothing running · last run {fmt.date_activity(when, now)}"
    how = {"clean": "finished", "problems": "finished with problems", "stopped": "was stopped",
           "failed": "failed"}.get(result, "finished")
    return f"Nothing running · last run {how} {fmt.date_activity(when, now)}"


def running_subtitle(tracker: RunTracker) -> str:
    """"Run 38 · started 13:41 · step 2 of 4"."""
    parts = []
    if tracker.kind in ("run", "retry") and tracker.number:
        parts.append(f"Run {tracker.number}")
    else:
        parts.append(tracker.title())
    if tracker.started is not None:
        parts.append(f"started {fmt.clock(tracker.started)}")
    if tracker.kind == "retry":
        parts.append("retrying its failures")
    elif tracker.index is not None and len(tracker.steps) > 1:
        parts.append(f"step {tracker.index + 1} of {len(tracker.steps)}")
    return " · ".join(parts)


def done_subtitle(number: int, result: str, finished: datetime | None, problems: int,
                  now: datetime) -> str:
    """"Run 39 · finished 09:18 · clean"; "Run 38 · finished 13:58 with 2 problems"."""
    at = f" {fmt.date_activity(finished, now)}" if finished else ""
    if result == "clean":
        return f"Run {number} · finished{at} · clean"
    if result == "problems":
        with_ = (f" with {fmt.count(problems)} problem{_s(problems)}" if problems
                 else " with problems")
        return f"Run {number} · finished{at}{with_}"
    if result == "stopped":
        return f"Run {number} · stopped{at}"
    return f"Run {number} · failed{at}"


def short_path(path: str, limit: int = PATH_SHOWN) -> str:
    """A path's end, which is the part that tells: "…\\projects\\myprojects"."""
    if len(path) <= limit:
        return path
    tail = path[-(limit - 1):]
    cut = tail.find(os.sep)
    return "…" + (tail[cut:] if 0 <= cut < len(tail) - 1 else tail)


# ---- the reserve and myprojects, as tables -----------------------------------------------------------

def library_columns(wide_chips: bool = False) -> tuple[Column, ...]:
    """The reserve's columns. The chip column fits New, the chip nearly every
    never-used folder carries; `wide_chips` widens it for Unidentified, which
    only a folder without a readable project.json does — few, since the
    check deletes them."""
    widths = theme.ROTATOR_COLUMNS
    chip = chip_size("New").width()
    if wide_chips:
        chip = max(chip, chip_size("Unidentified").width())
    return (
        Column("Folder", None, thumb="row", font="type.folder", tone="text.body"),
        Column("Author", widths["author"], font="type.label", tone="text.mid"),
        Column("Type", widths["type"], font="type.monoXs", tone="text.lo"),
        Column("Size", widths["size"], "right", mono=True, tone="text.mid"),
        Column("Last used", widths["used"], "right", mono=True, tone="text.mid"),
        Column("", chip, sortable=False),
    )


class LibraryModel(TableModel):
    """The folders of one root, by name: rows as soon as the names are known,
    titles, types, authors and sizes filling in as the library index hands
    them over. Nothing here reads the disk; a row is its folder's name, and
    everything else is looked up when the row is painted."""

    def __init__(self, parent=None):
        super().__init__(library_columns(), (), parent)
        self.root = ""
        self.infos: dict[str, FolderInfo] = {}
        self.sizes: dict[str, int] = {}
        self.authors: dict[str, str] = {}
        self.usage = None              # Usage, or a fixture's stand-in
        self.now = datetime.now()
        self.show_thumbs = True
        self.wide_chips = False
        self.sort(0, Qt.AscendingOrder)

    def set_names(self, names) -> None:
        self.set_rows(list(names))

    def fit_chips(self) -> bool:
        """Widen the chip column when a folder listed is Unidentified, narrow it
        when none is; True when it changed (the table lays itself out again)."""
        wide = any(info.unidentified for info in self.infos.values())
        if wide == self.wide_chips:
            return False
        self.wide_chips = wide
        self.columns = library_columns(wide)
        return True

    def names(self) -> list[str]:
        return self.items()

    def size_of(self, name: str) -> int | None:
        size = self.sizes.get(name)
        if size is None:
            info = self.infos.get(name)
            size = info.size if info is not None else None
        return size

    def last_used(self, name: str) -> datetime | None:
        return self.usage.last_used(name) if self.usage is not None else None

    def never_used(self, name: str) -> bool:
        return self.usage.never_used(name) if self.usage is not None else False

    def total_size(self) -> tuple[int, int]:
        """(bytes measured, folders not measured yet)."""
        known = unknown = 0
        for name in self.items():
            size = self.size_of(name)
            if size is None:
                unknown += 1
            else:
                known += size
        return known, unknown

    def never_used_count(self) -> int:
        if self.usage is None:
            return 0
        return sum(1 for name in self.items()
                   if not is_protected(name) and self.usage.never_used(name))

    def cell(self, name: str, column: int):
        if column == 0:
            if is_protected(name):
                # the lock says what the prefix said
                return Cell(name[len(PROTECTED_PREFIX):].strip() or name, "text.lo", icon="lock")
            return name
        info = self.infos.get(name)
        if column == 1:
            if info is None:
                return ""
            return self.authors.get(info.workshop_id, "") or fmt.DASH
        if column == 2:
            return (info.kind or fmt.DASH) if info is not None else ""
        if column == 3:
            size = self.size_of(name)
            return fmt.size(size) if size is not None else ""
        if column == 4:
            when = self.last_used(name)
            return fmt.day(when, self.now) if when is not None else Cell("never", "text.lo")
        if info is not None and info.unidentified:
            return ChipCell("Unidentified")
        if not is_protected(name) and self.never_used(name):
            return ChipCell("New")
        return None

    def sort_key(self, name: str, column: int):
        if column == 0:
            return name
        if column == 3:
            return self.size_of(name)
        if column == 4:
            when = self.last_used(name)
            return when.timestamp() if when is not None else 0.0
        return super().sort_key(name, column)

    def thumb_source(self, name: str) -> str | None:
        # The folder, always: the loader finds its preview on a worker. A key
        # that became the preview's file once project.json was read would
        # leave the request made for the folder unanswered.
        if not self.show_thumbs or not self.root:
            return None
        return os.path.join(self.root, name)

    def matches(self, name: str, text: str) -> bool:
        if text in name.casefold():
            return True
        info = self.infos.get(name)
        if info is None:
            return False
        return (text in info.title.casefold()
                or text in self.authors.get(info.workshop_id, "").casefold())


def history_columns() -> tuple[Column, ...]:
    widths = theme.HISTORY_COLUMNS
    return (
        Column("Run", widths["run"], "right", mono=True, tone="text.mid"),
        Column("Started", widths["started"], mono=True, tone="text.body"),
        Column("Took", widths["took"], "right", mono=True, tone="text.mid"),
        Column("Moved", widths["moved"], "right", mono=True, tone="text.body"),
        Column("Returned", widths["returned"], "right", mono=True, tone="text.body"),
        Column("Dupes", widths["dupes"], "right", mono=True, tone="text.body"),
        Column("Result", None, "right", mono=True),
        Column("", widths["log"], "right", font="type.label", tone="text.mid", sortable=False),
    )


LOG_COLUMN = 7
_RESULT_ORDER = {"clean": 0, "problems": 1, "stopped": 2, "failed": 3}


class HistoryModel(TableModel):
    """The runs, newest first, and the run just finished marked in its hue."""

    def __init__(self, parent=None):
        super().__init__(history_columns(), (), parent)
        self.marked: tuple[str, str] | None = None      # (run id, tone)
        self.now = datetime.now()

    def cell(self, row: HistoryRow, column: int):
        if column == 0:
            return str(row.number)
        if column == 1:
            return fmt.date_table(row.started, self.now) if row.started else fmt.DASH
        if column == 2:
            return fmt.duration(row.seconds, exact=False) if row.seconds is not None else fmt.DASH
        if column == 3:
            return fmt.count(row.moved)
        if column == 4:
            return fmt.count(row.returned)
        if column == 5:
            return fmt.count(row.duplicates)
        if column == 6:
            words, tone = result_words(row.result, row.failed)
            return Cell(words, tone)
        return "Log"

    def sort_key(self, row: HistoryRow, column: int):
        if column == 0:
            return row.number
        if column == 1:
            return row.started.timestamp() if row.started else None
        if column == 2:
            return row.seconds
        if column in (3, 4, 5):
            return (row.moved, row.returned, row.duplicates)[column - 3]
        if column == 6:
            return _RESULT_ORDER.get(row.result, 9)
        return super().sort_key(row, column)

    def row_tone(self, row: HistoryRow) -> str | None:
        if self.marked is not None and row.id == self.marked[0]:
            return self.marked[1]
        return None

    def matches(self, row: HistoryRow, text: str) -> bool:
        words = " ".join([str(row.number), self.cell(row, 1), row.result,
                          result_words(row.result, row.failed)[0]])
        return text in words.casefold()


# ---- work off the window's thread ----------------------------------------------------------------

class _Offload(QObject):
    """Runs a function on a thread of its own and hands its result (or the
    exception it raised) to a callback on the window's thread."""

    _done = Signal(int, object)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._callbacks: dict[int, Callable] = {}
        self._next = 0
        self._done.connect(self._deliver)

    def run(self, work: Callable[[], object], then: Callable[[object], None]) -> int:
        self._next += 1
        token = self._next
        self._callbacks[token] = then
        threading.Thread(target=self._work, args=(token, work), daemon=True,
                         name="rotator page").start()
        return token

    def _work(self, token: int, work) -> None:
        try:
            result = work()
        except Exception as err:  # noqa: BLE001 — handed over, not raised on a thread
            result = err
        try:
            self._done.emit(token, result)
        except RuntimeError:
            pass                # the page went while the disk was answering

    def _deliver(self, token: int, result) -> None:
        then = self._callbacks.pop(token, None)
        if then is not None:
            then(result)

    def forget(self, token: int | None) -> None:
        if token is not None:
            self._callbacks.pop(token, None)

    def busy(self) -> bool:
        return bool(self._callbacks)


# Workers are kept here until they have finished, whatever becomes of the page:
# a QThread deleted while it runs takes the process with it.
_LIVE: set = set()


def _park(thread: QThread) -> QThread:
    _LIVE.add(thread)
    thread.finished.connect(lambda t=thread: _LIVE.discard(t))
    return thread


def check_roots(config: Config) -> tuple[list[str], list[str], list[str]]:
    """The folders a check looks through — the reserve and myprojects, each
    once — and what is wrong with them: (roots, not set, not found). Resolves
    and stats paths on the hard disk: a thread's work."""
    roots, seen, unset = [], set(), []
    for label_, candidate in (("reserve", config.source), ("myprojects", config.destination)):
        problem = folder_problem(label_, candidate)
        if problem:
            unset.append(problem)
            continue
        try:
            key = str(Path(candidate).resolve()).lower()
        except OSError:
            key = candidate.lower()
        if key not in seen:
            seen.add(key)
            roots.append(candidate)
    missing = [r for r in roots if not Path(r).exists()]
    return roots, unset, missing


# ---- the dialogs -------------------------------------------------------------------------------------

def _note_line(tone: str, icon: str, words: str) -> QWidget:
    """A fact under a dialog's body: a glyph and a line of words."""
    line = QWidget()
    row = QHBoxLayout(line)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(theme.SP_8)
    colour = {"warn": "warn", "danger": "danger"}.get(tone, "text.lo")
    row.addWidget(Glyph(icon, colour, theme.RUN_FACT_ICON), 0, Qt.AlignTop)
    words_ = label(words, "type.label", {"warn": "warn", "danger": "body"}.get(tone, "mid"))
    words_.setWordWrap(True)
    row.addWidget(words_, 1)
    line.setAccessibleName(words)
    return line


def start_dialog(c: Confirmation, parent: QWidget | None, *, embedded: bool = False) -> ConfirmDialog:
    dialog = ConfirmDialog(c.title, c.body, parent, icon="rotator", tone="accent", steps=c.steps,
                           confirm_text=c.confirm, embedded=embedded)
    for tone, icon, words in c.notes:
        dialog.body_column.addWidget(_note_line(tone, icon, words))
    return dialog


def broken_dialog(broken: list[BrokenFolder], where: str, parent: QWidget | None, *,
                  scanned: int = 0, open_folder: Callable[[], None] | None = None,
                  embedded: bool = False) -> ConfirmDialog:
    """Frame 09: the grouped checklist, safe folders ticked, the ones holding
    media not; Cancel deletes nothing (the run goes on without the clean-up)."""
    actions = [("Open folder", open_folder)] if open_folder is not None else []
    dialog = ConfirmDialog(
        broken_title(broken, where), broken_body(broken, scanned), parent, destructive=True,
        icon="warn", groups=broken_groups(broken),
        confirm_text=lambda rows: f"Delete {fmt.count(len(rows))} permanently",
        actions=actions, embedded=embedded)
    dialog.body_column.addWidget(_note_line("danger", "warn", PERMANENT))
    return dialog


class RunLogDialog(OverlayDialog):
    """A run's log read back: the lines of its file, or — for a run whose log
    was not kept — what its record says it moved."""

    def __init__(self, title: str, body: str, lines, *, file_name: str = "",
                 on_open_folder: Callable[[], None] | None = None,
                 parent: QWidget | None = None, embedded: bool = False):
        super().__init__(parent, width=theme.RUN_LOG_WIDTH, embedded=embedded)
        self._set_head(title, body, "clock", "neutral")
        self.log = LogPanel("Log", file=file_name, expanded=True, on_open_folder=on_open_folder)
        self.log.set_file(file_name, writing=False)
        self.log.extend(lines)
        self.body_column.addWidget(self.log)
        close = SecondaryButton("Close")
        close.clicked.connect(self.accept)
        self.footer.buttons.addWidget(close)
        self._initial = close

    def result_value(self) -> bool:
        return True


DUPLICATES_COLUMNS = ("Folder", "Size", "Files", "Modified", "")


class DuplicatesModel(TableModel):
    def __init__(self, parent=None):
        widths = theme.DUPLICATES_COLUMNS
        super().__init__((
            Column("Folder", None, font="type.folder"),
            Column("Size", widths["size"], "right", mono=True, tone="text.mid"),
            Column("Files", widths["files"], "right", mono=True, tone="text.mid"),
            Column("Modified", widths["modified"], "right", mono=True, tone="text.mid"),
            Column("", widths["chip"], "right", sortable=False),
        ), (), parent)
        self.now = datetime.now()

    def cell(self, d: DuplicateFolder, column: int):
        if column == 0:
            return d.name
        if column == 1:
            return fmt.size(d.size)
        if column == 2:
            return fmt.count(d.files)
        if column == 3:
            return fmt.day(d.modified, self.now) if d.modified else fmt.DASH
        return ChipCell("Duplicated", "in reserve") if d.in_reserve else None

    def sort_key(self, d: DuplicateFolder, column: int):
        if column in (1, 2, 3):
            return (d.size, d.files, d.modified)[column - 1]
        return super().sort_key(d, column)


def duplicates_confirmation(action: str, chosen: list[DuplicateFolder], folder: str,
                            reserve: str) -> tuple[str, str, bool, str]:
    """(title, body, destructive, button) for deleting or moving back the chosen
    duplicates: the count, the size, the folders, and what cannot be undone."""
    n = len(chosen)
    size = fmt.size(sum(d.size for d in chosen))
    if action == "delete":
        return (f"Delete {fmt.counted(n, 'duplicate')} permanently?",
                f"{fmt.count(n)} folder{_s(n)} ({size}) in {folder} {_es(n, 'is', 'are')} "
                "deleted. They do not go to the Recycle Bin.", True,
                f"Delete {fmt.count(n)} permanently")
    replaced = sum(1 for d in chosen if d.in_reserve)
    body = (f"{fmt.count(n)} folder{_s(n)} ({size}) {_es(n, 'moves', 'move')} from {folder} "
            f"into the reserve, {reserve}.")
    if replaced:
        body += (f" {fmt.count(replaced)} {_es(replaced, 'replaces', 'replace')} a folder of the "
                 "same name there, which is deleted.")
    return (f"Move {fmt.counted(n, 'folder')} back into the reserve?", body, bool(replaced),
            f"Move {fmt.count(n)} back")


class DuplicatesDialog(OverlayDialog):
    """Not in the design (plan §6.2.9): what is in the duplicates folder, with
    sizes, to move back over the reserve's copy or to delete. It asks again
    before either; `ask()` returns ("delete" | "replace", names), or None."""

    def __init__(self, config: Config, parent: QWidget | None = None, *,
                 answer: Callable | None = None, embedded: bool = False, listing=None):
        super().__init__(parent, width=theme.DUPLICATES_WIDTH, embedded=embedded)
        self.config = config
        self._answer_fn = answer or (lambda dialog: dialog.ask())
        self._choice = None
        self._worker: DuplicatesListWorker | None = None
        self._set_head("Duplicates",
                       f"Folders a run found already in the reserve, set aside in "
                       f"{config.duplicates}. Move them back over the reserve's copy, or "
                       "delete them.", "copier", "neutral")
        self.model = DuplicatesModel(self)
        self.table = Table()
        self.table.setModel(self.model)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setAccessibleName("Duplicates")
        self.table.setFixedHeight(theme.DUPLICATES_TABLE)
        self.table.selectionModel().selectionChanged.connect(self._selected)
        self.body_column.addWidget(self.table)
        self.reserve_note = label("", "type.caption", "warn")
        self.reserve_note.setWordWrap(True)
        self.reserve_note.hide()
        self.body_column.addWidget(self.reserve_note)

        select_all = GhostButton("Select all", size="sm")
        select_all.clicked.connect(self.table.selectAll)
        self.footer.actions.addWidget(select_all)
        self.replace_button = SecondaryButton("Move & replace → reserve")
        self.replace_button.clicked.connect(lambda: self._act("replace"))
        self.delete_button = DangerButton("Delete…")
        self.delete_button.clicked.connect(lambda: self._act("delete"))
        close = SecondaryButton("Close")
        close.clicked.connect(self.reject)
        for button in (self.replace_button, self.delete_button, close):
            self.footer.buttons.addWidget(button)
        self._initial = close
        problem = folder_problem("reserve", config.source)
        if problem:
            self.reserve_note.setText(f"{problem} Moving back waits until it is set.")
            self.reserve_note.show()
        self._reserve_ok = problem is None
        if listing is not None:
            self.take(listing)
        else:
            self.footer.summary.set_text("Measuring the folders…")
        self._selected()

    def start(self) -> None:
        """List and measure what is in the duplicates folder, on a worker."""
        worker = self._worker = _park(DuplicatesListWorker(self.config))
        worker.listed.connect(self.take)
        worker.progress.connect(self._progress)
        worker.error.connect(lambda message: self.footer.summary.set_text(message))
        worker.start()

    def _progress(self, e: ProgressEvent) -> None:
        if e.total and not self.model.items():
            self.footer.summary.set_text(f"Measuring {fmt.ratio(e.current, e.total, 'prose')} "
                                         "folders…")

    def take(self, found: list) -> None:
        self.model.set_rows(found)
        self._selected()

    def chosen(self) -> list[DuplicateFolder]:
        return self.table.selected_items()

    def _selected(self, *_args) -> None:
        chosen = self.chosen()
        if self.model.items():
            size = fmt.size(sum(d.size for d in chosen))
            self.footer.summary.set_text(f"{fmt.count(len(chosen))} selected · {size} · "
                                         f"{fmt.counted(len(self.model.items()), 'folder')}")
        self.delete_button.setEnabled(bool(chosen))
        self.replace_button.setEnabled(bool(chosen) and self._reserve_ok)

    def _act(self, action: str) -> None:
        chosen = self.chosen()
        if not chosen:
            return
        title, body, destructive, button = duplicates_confirmation(
            action, chosen, self.config.duplicates, self.config.source)
        confirm = ConfirmDialog(title, body, self, destructive=destructive,
                                icon="trash" if action == "delete" else "folder",
                                confirm_text=button)
        self.confirmation = confirm
        if self._answer_fn(confirm):
            self._choice = (action, [d.name for d in chosen])
            self.accept()

    def done(self, code: int) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
        super().done(code)

    def result_value(self):
        return self._choice


# ---- the panels ---------------------------------------------------------------------------------------

def _column(widget: QWidget, spacing: int) -> QVBoxLayout:
    column = QVBoxLayout(widget)
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(spacing)
    return column


class _NextRun(GlassPanel):
    """"Next run": the folders it works between, the batch, how it draws,
    whether it rebuilds the playlist, and the facts that are not options."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, padding="md")
        column = _column(self, theme.ROTATOR_PANEL_GAP)
        self.head = CardTitle("Next run")
        column.addWidget(self.head)

        folders = QVBoxLayout()
        folders.setSpacing(theme.ROTATOR_FIELDS_GAP)
        folders.addWidget(Overline("Folders"))
        self.reserve = PathField(editable=False, placeholder="The reserve is not set")
        self.reserve.setAccessibleName("The reserve")
        self.myprojects = PathField(editable=False, placeholder="myprojects is not set")
        self.myprojects.setAccessibleName("myprojects")
        folders.addWidget(self.reserve)
        folders.addWidget(self.myprojects)
        column.addLayout(folders)

        batch = QHBoxLayout()
        batch.setSpacing(theme.SP_10)
        batch.addWidget(Overline("Batch"))
        batch.addStretch(1)
        self.batch = SpinBox(minimum=BATCH_RANGE[0], maximum=BATCH_RANGE[1], value=1)
        self.batch.setAccessibleName("Folders per run")
        batch.addWidget(self.batch)
        column.addLayout(batch)

        self.selection = label("", "type.bodySm", "mid")
        self.selection.setWordWrap(True)
        column.addWidget(self.selection)
        self.reset = Callout(tone="warn")
        self.reset.hide()
        column.addWidget(self.reset)

        toggles = QVBoxLayout()
        toggles.setSpacing(theme.SP_4)
        self.refresh = Toggle("Rebuild the playlist in Wallpaper Engine")
        self.refresh.setToolTip(REFRESH_TIP)
        toggles.addWidget(self.refresh)
        self.refresh_note = label("It restarts once: a few seconds without wallpapers.",
                                  "type.caption", "lo")
        self.refresh_note.setContentsMargins(theme.TOGGLE_TRACK[0] + theme.TOGGLE_GAP, 0, 0, 0)
        toggles.addWidget(self.refresh_note)
        column.addLayout(toggles)

        self._fact = QWidget()
        fact = QHBoxLayout(self._fact)
        fact.setContentsMargins(0, 0, 0, 0)
        fact.setSpacing(theme.SP_8)
        fact.addWidget(Glyph("lock", "text.lo", theme.RUN_FACT_ICON), 0, Qt.AlignVCenter)
        self.protected = label("", "type.label", "lo")
        fact.addWidget(self.protected, 1)
        self._fact.hide()
        column.addWidget(self._fact)

        self.notice = Callout(tone="warn")
        self.notice.hide()
        column.addWidget(self.notice)
        self.problem = Callout(tone="danger")
        self.settings_link = LinkButton("Set it in Settings")
        self.problem.add_action(self.settings_link)
        self.problem.hide()
        column.addWidget(self.problem)

    def set_protected(self, words: str) -> None:
        self.protected.setText(words)
        self._fact.setVisible(bool(words))

    def texts(self) -> dict:
        return {"title": self.head.title(), "run": self.head.subtitle(),
                "selection": self.selection.text() if not self.selection.isHidden() else "",
                "reset": self.reset._body.text() if not self.reset.isHidden() else "",
                "protected": self.protected.text() if not self._fact.isHidden() else "",
                "notice": self.notice._body.text() if not self.notice.isHidden() else "",
                "problem": self.problem._body.text() if not self.problem.isHidden() else ""}


class _Plan(GlassPanel):
    """"What a run does": the steps, what each will do this time, and the
    button that starts it."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, padding="md")
        column = _column(self, theme.ROTATOR_PANEL_GAP)
        column.addWidget(Overline("What a run does"))
        self.steps = StepList()
        column.addWidget(self.steps)
        column.addStretch(1)
        self.start = AccentButton("Start rotation")
        self.start.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        column.addWidget(self.start)
        self.estimate = label(estimate_line(None), "type.caption", "lo")
        self.estimate.setAlignment(Qt.AlignHCenter)
        self.estimate.setWordWrap(True)
        column.addWidget(self.estimate)


class _RunPanel(GlassPanel):
    """The run under way: which step, what it is doing, how far, what it last
    moved, and the one way to stop it (gate G7 A: no Pause)."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, padding="md")
        column = _column(self, theme.RUN_PANEL_GAP)
        head = QHBoxLayout()
        head.setSpacing(theme.RUN_HEAD_GAP)
        self.dot = LiveDot("accent")
        self.title = label("", "type.h3", "hi")
        self.started = label("", "type.monoSm", "lo")
        head.addWidget(self.dot, 0, Qt.AlignVCenter)
        head.addWidget(self.title, 1)
        head.addWidget(self.started)
        column.addLayout(head)
        self.sentence = label("", "type.bodySm", "mid")
        self.sentence.setWordWrap(True)
        column.addWidget(self.sentence)

        figure = QHBoxLayout()
        figure.setSpacing(theme.RUN_FIGURE_GAP)
        self.ring = ProgressRing(58)
        figure.addWidget(self.ring)
        numbers = QVBoxLayout()
        numbers.setSpacing(theme.RUN_COUNT_GAP)
        count = QHBoxLayout()
        count.setSpacing(theme.SP_6)
        self.count = label("", "type.count", "hi")
        self.of = label("", "type.runCount", "lo")
        count.addWidget(self.count, 0, Qt.AlignBaseline)
        count.addWidget(self.of, 0, Qt.AlignBaseline)
        count.addStretch(1)
        numbers.addStretch(1)
        numbers.addLayout(count)
        self.left = label("", "type.label", "mid")
        numbers.addWidget(self.left)
        numbers.addStretch(1)
        figure.addLayout(numbers, 1)
        column.addLayout(figure)
        self.bar = ProgressBar(height=8)
        column.addWidget(self.bar)
        self.activity = ActivityLine("")
        column.addWidget(self.activity)
        buttons = QHBoxLayout()
        buttons.setSpacing(theme.SP_8)
        self.stop = SecondaryButton("Stop after this step", icon="stop")
        buttons.addWidget(self.stop)
        buttons.addStretch(1)
        column.addLayout(buttons)

    def show_tracker(self, t: RunTracker, left: str | None) -> None:
        self.title.setText(t.title())
        self.started.setText(f"started {fmt.clock(t.started)}" if t.started else "")
        self.sentence.setText(t.sentence())
        if t.total:
            self.ring.set_value(t.done, t.total)
            self.bar.set_state("determinate")
            self.bar.set_value(t.done, t.total)
            self.count.setText(fmt.count(t.done))
            self.of.setText(f"/ {fmt.count(t.total)}")
        else:
            self.ring.set_indeterminate(True)
            self.bar.set_state("indeterminate")
            self.count.setText(fmt.DASH)
            self.of.setText("")
        self.left.setText(left or "")
        self.left.setVisible(bool(left))
        self.activity.set_text(t.activity or t.sentence())
        stoppable = t.kind != "rebuild" and t.key() != "playlist" and t.phase != "delete"
        self.stop.setVisible(t.kind != "rebuild")
        self.stop.setEnabled(stoppable and not t.stopping)
        self.stop.setText("Stopping after this step…" if t.stopping else "Stop after this step")

    def texts(self) -> dict:
        return {"title": self.title.text(), "started": self.started.text(),
                "sentence": self.sentence.text(),
                "count": f"{self.count.text()} {self.of.text()}".strip(),
                "left": self.left.text() if not self.left.isHidden() else "",
                "activity": self.activity.text(), "stop": self.stop.text(),
                "stop_enabled": self.stop.isEnabled()}


class _Result(GlassPanel):
    """How a run ended, in its hue: what it did, and the ways on."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, padding="lg")
        column = _column(self, theme.RUN_PANEL_GAP)
        head = QHBoxLayout()
        head.setSpacing(theme.RUN_HEAD_GAP)
        self.disc = IconDisc("check", "ok")
        self.title = label("", "type.h3", "hi")
        self.took = label("", "type.monoSm", "lo")
        head.addWidget(self.disc, 0, Qt.AlignVCenter)
        head.addWidget(self.title, 1)
        head.addWidget(self.took)
        column.addLayout(head)
        self.sentence = label("", "type.bodySm", "mid")
        self.sentence.setWordWrap(True)
        column.addWidget(self.sentence)
        self.metrics = MetricStrip([(0, "moved in"), (0, "returned"), (0, "duplicates"),
                                    (0, "problems")])
        column.addWidget(self.metrics)
        actions = QHBoxLayout()
        actions.setSpacing(theme.SP_8)
        self.retry = AccentButton("")
        self.open_log = SecondaryButton("Open log")
        self.history = GhostButton("Run history")
        self.next = GhostButton("Next run")
        for button in (self.retry, self.open_log, self.history, self.next):
            actions.addWidget(button)
        actions.addStretch(1)
        column.addLayout(actions)

    def texts(self) -> dict:
        return {"title": self.title.text(), "took": self.took.text(),
                "sentence": self.sentence.text(), "tone": self.tone(),
                "metrics": [self.metrics.value(i) for i in range(len(self.metrics))],
                "retry": self.retry.text() if not self.retry.isHidden() else ""}


class _Steps(GlassPanel):
    """"The four steps": the run's steps as they go or went; under them, the
    counts while it runs, or the notes and the way on once it has ended."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, padding="md")
        column = _column(self, theme.ROTATOR_PANEL_GAP)
        self.overline = Overline("The four steps")
        column.addWidget(self.overline)
        self.steps = StepList()
        column.addWidget(self.steps)
        column.addStretch(1)
        self.metrics = MetricStrip([(0, "moved in"), (0, "returned"), (0, "duplicates"),
                                    (0, "problems")])
        column.addWidget(self.metrics)
        self.failure = Callout(tone="danger")
        self.failure.hide()
        column.addWidget(self.failure)
        self.note = Callout(tone="neutral", icon="clock")
        self.note.hide()
        column.addWidget(self.note)
        self.stale = Callout("Wallpaper Engine is still playing the old playlist. Rebuild it "
                             "from here, or in Wallpaper Engine.", tone="danger")
        self.stale.hide()
        column.addWidget(self.stale)
        self.rebuild = SecondaryButton("Rebuild playlist now")
        self.rebuild.hide()
        holder = QHBoxLayout()
        holder.addWidget(self.rebuild)
        holder.addStretch(1)
        column.addLayout(holder)

    def set_rows(self, rows: list[StepRow]) -> None:
        self.overline.setText(f"The {STEP_COUNT_WORDS.get(len(rows), fmt.count(len(rows)))} "
                              f"step{_s(len(rows))}")
        if len(self.steps) != len(rows) or any(self.steps.title(i) != r[0]
                                               for i, r in enumerate(rows)):
            self.steps.set_steps([(r[0], r[1], r[2], r[3]) for r in rows])
            return
        for i, (title, caption, state, tone) in enumerate(rows):
            self.steps.set_step(i, state=state, caption=caption, tone=tone or "")

    def rows(self) -> list[tuple[str, str, str]]:
        return [(self.steps.title(i), self.steps.caption(i), self.steps.step_state(i))
                for i in range(len(self.steps))]


# ---- the page -------------------------------------------------------------------------------------------

@dataclass
class DoneView:
    """A run that ended while the window watched, as the done state shows it."""
    record: RunRecord
    meta: RunMeta | None
    number: int
    check: tuple[int, int] | None = None     # (broken, deleted) of the check that began it
    error: str = ""
    rebuilt: bool = False                   # "Rebuild playlist now" put the playlist right

    @property
    def result(self) -> str:
        if self.meta is not None and self.meta.result in RESULTS:
            return self.meta.result
        return "problems" if self.record.failed else "clean"


class RotatorPage(Page):
    key = "rotator"
    title = "Rotator"
    icon = "rotator"
    FIXTURES = ("idle", "running", "done-clean", "done-problems", "current", "confirm",
                "broken", "history", "will-reset")

    # The page saved the batch or the playlist switch: the Settings page shows it.
    config_edited = Signal()

    def __init__(self, config: Config, services=None, parent: QWidget | None = None, *,
                 now: Callable[[], datetime] | None = None, index: LibraryIndex | None = None):
        super().__init__(parent)
        self.config = config
        self._services = services
        self._now = now or datetime.now
        self._index = index
        self._fixture: dict | None = None
        self.state = "idle"
        self.view = "reserve"
        self.history: History | None = None
        self._metas: dict[str, RunMeta] = {}
        self.facts: NextRunFacts | None = None
        self._facts_token: int | None = None
        self.tracker: RunTracker | None = None
        self.done_view: DoneView | None = None
        self.messages: list[tuple[str, str]] = []
        self.last_dialog: QWidget | None = None
        self._on_screen = False
        self._job = None
        self._job_words = ""
        self._check_finished: datetime | None = None
        self._check_found: tuple[int, int] | None = None
        self._rotate_after_check = False
        self._starting = False
        self._showing = False
        self._cancelled = False
        self._fixture_left: str | None = None
        self._library: LibraryIndexWorker | None = None
        self._library_roots: tuple[str, str] = ("", "")
        self.scan_worker: ReserveScanWorker | None = None
        self.cleanup_worker: CleanupWorker | None = None
        self.rotation_worker: RotationWorker | None = None
        self.retry_worker: RetryWorker | None = None
        self.rebuild_worker: PlaylistRebuildWorker | None = None
        self.dup_worker: DuplicateActionWorker | None = None
        self._dup_job = None
        self._header: dict[str, QWidget] = {}
        self._offload = _Offload(self)

        self._build()

        self._facts_timer = self._timer(FACTS_DELAY_MS, self._read_facts)
        self._repaint_timer = self._timer(REPAINT_MS, self._repaint_library)
        self._sizes_timer = self._timer(SIZES_DELAY_MS, self._request_sizes)
        self._render_timer = self._timer(RENDER_MS, self._render_running)
        self._filter_timer = self._timer(FACTS_DELAY_MS // 2, self._apply_filter)

        if services is not None:
            services.jobs.changed.connect(self._jobs_changed)
            services.snapshot.refreshed.connect(self._snapshot_refreshed)
        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._shutdown)
        self._show_config()
        self._set_state("idle")
        self._update_nav()

    def _timer(self, ms: int, slot) -> QTimer:
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(ms)
        timer.timeout.connect(slot)
        return timer

    # -- building

    def _build(self) -> None:
        body = QHBoxLayout(self)
        pad_v, pad_h = theme.BODY_PAD
        body.setContentsMargins(pad_h, pad_v, pad_h, pad_v)
        body.setSpacing(theme.PANEL_GAP)

        side = QWidget()
        column = _column(side, theme.PANEL_GAP)
        self.next_run = _NextRun()
        self.plan = _Plan()
        self.run_panel = _RunPanel()
        self.result = _Result()
        self.steps = _Steps()
        column.addWidget(self.next_run)
        column.addWidget(self.plan, 1)
        column.addWidget(self.run_panel)
        column.addWidget(self.result)
        column.addWidget(self.steps, 1)
        self.side_scroll = SideScroll(side, theme.ROTATOR_SIDE)
        body.addWidget(self.side_scroll)

        self.right = QStackedWidget()
        self.right.addWidget(self._build_tables())
        self.log = LogPanel("Log", fill=True, on_open_folder=self._open_log_folder)
        self.right.addWidget(self.log)
        body.addWidget(self.right, 1)

        n = self.next_run
        n.batch.valueChanged.connect(self._set_batch)
        n.refresh.toggled.connect(self._set_refresh)
        n.settings_link.clicked.connect(lambda: self.navigate.emit("settings"))
        n.reserve.validity_changed.connect(lambda _v: self._render_idle())
        n.myprojects.validity_changed.connect(lambda _v: self._render_idle())
        self.plan.start.clicked.connect(self.start_rotation)
        self.run_panel.stop.clicked.connect(self.stop_after_step)
        r = self.result
        r.retry.clicked.connect(self.retry_failures)
        r.open_log.clicked.connect(lambda: self.done_view and self.open_run_log(
            self.done_view.record.id))
        r.history.clicked.connect(lambda: self.show_view("history"))
        r.next.clicked.connect(self.next_run_view)
        self.steps.rebuild.clicked.connect(self.rebuild_playlist)

    def filter_field(self) -> QWidget | None:
        return self.filter

    def _build_tables(self) -> GlassPanel:
        card = GlassPanel(padding="none")
        column = _column(card, 0)
        bar = TableBar()
        self.views = SegmentedControl(VIEW_TITLES)
        self.views.setAccessibleName("Show")
        self.views.changed.connect(lambda i: self.show_view(VIEWS[i]))
        self.filter = TextInput(placeholder="Filter…", search=True)
        self.filter.setFixedWidth(theme.ROTATOR_FILTER)
        self.filter.setAccessibleName("Filter")
        self.filter.textChanged.connect(lambda _t: self._filter_timer.start())
        self.reload = IconButton("refresh", "Read the folders and the history again")
        self.reload.clicked.connect(self.refresh)
        bar.add(self.views)
        bar.add(self.filter)
        bar.add_stretch()
        bar.add(self.reload)
        column.addWidget(bar)
        self.summary = TableSummary()
        column.addWidget(self.summary)

        self.models = {"reserve": LibraryModel(self), "current": LibraryModel(self),
                       "history": HistoryModel(self)}
        self.tables: dict[str, Table] = {}
        self.table_stack = QStackedWidget()
        for view in VIEWS:
            # A loader each: one table asking for its rows drops what another
            # had queued (`retarget_local`).
            table = Table()
            table.setModel(self.models[view])
            table.setAccessibleName(VIEW_TITLES[VIEWS.index(view)])
            if view == "history":
                table.clicked.connect(self._history_clicked)
                table.doubleClicked.connect(lambda index: self._history_clicked(index, True))
            else:
                table.verticalScrollBar().valueChanged.connect(lambda _v: self._sizes_timer.start())
            self.tables[view] = table
            self.table_stack.addWidget(table)
        column.addWidget(self.table_stack, 1)

        self.footer = TableFooter("")
        self.check_button = GhostButton("Check folders", size="sm")
        self.check_button.setToolTip(CHECK_TIP)
        self.check_button.clicked.connect(self.check_folders)
        self.open_button = GhostButton("Open folder", icon="ext", size="sm")
        self.open_button.clicked.connect(self.open_folder)
        self.export_button = GhostButton("Export as CSV", size="sm")
        self.export_button.clicked.connect(self.export_history)
        for button in (self.check_button, self.open_button, self.export_button):
            self.footer.add_action(button)
        column.addWidget(self.footer)
        return card

    def make_header_actions(self) -> list[QWidget]:
        # Parented from the start: shown with no parent, a button is a window of
        # its own for a moment, and takes the window's focus (and kit buttons
        # must have a parent, see STATUS step 11). The header takes them over.
        duplicates = GhostButton("Duplicates", self, outlined=True)
        duplicates.clicked.connect(self.open_duplicates)
        settings = GhostButton("Run settings", self, outlined=True)
        settings.clicked.connect(lambda: self.navigate.emit("settings"))
        history = GhostButton("Run history", self, outlined=True)
        history.clicked.connect(lambda: self.show_view("history"))
        self._header = {"duplicates": duplicates, "settings": settings, "history": history}
        self._render_header()
        return [duplicates, settings, history]

    # -- the Rotator's own files, read when first needed

    def _ensure_loaded(self) -> None:
        if self.history is not None or self._fixture is not None:
            return
        self.history = History.load()
        self._metas = rmeta.read_meta()
        self._history_changed(read=False)

    def _history_changed(self, read: bool = True) -> None:
        """The history (or its side file) changed: the tables' LAST USED and New,
        the History view, the next run's number."""
        if self.history is None:
            return
        if read and self._fixture is None:
            self._metas = rmeta.read_meta()
        usage = self.history.usage()
        now = self._now()
        for view in ("reserve", "current"):
            model = self.models[view]
            model.usage, model.now = usage, now
        self._set_history_rows()
        self._repaint_library()

    def _set_history_rows(self) -> None:
        model = self.models["history"]
        model.now = self._now()
        rows = history_rows(self.history.runs if self.history is not None else [], self._metas)
        model.set_rows(rows)
        if self.view == "history":
            self._render_table_bars()

    # -- on and off screen

    def on_shown(self) -> None:
        self._on_screen = True
        if self._fixture is not None:
            return
        self._ensure_loaded()
        if self.state != "running":
            self._start_library()
            self._read_facts()
        self._render()

    def on_hidden(self) -> None:
        self._on_screen = False
        self._stop_library()

    def _shutdown(self) -> None:
        self._stop_library(wait=True)

    # -- the config, shared with the Settings page

    def _show_config(self) -> None:
        n, c = self.next_run, self.config
        n.reserve.set_path(c.source)
        n.myprojects.set_path(c.destination)
        # Shown, not chosen: the handlers leave the config alone meanwhile (the
        # toggle's knob moves on its own signal, so that one is not blocked).
        self._showing = True
        try:
            n.batch.setValue(max(BATCH_RANGE[0], min(BATCH_RANGE[1], c.count)))
            n.refresh.setChecked(bool(c.refresh_playlist))
        finally:
            self._showing = False
        n.refresh_note.setVisible(bool(c.refresh_playlist))

    def config_changed(self) -> None:
        """The Settings page saved the Rotator's settings."""
        self._show_config()
        roots = (self.config.source, self.config.destination)
        if roots != self._library_roots and self._library is not None:
            self._stop_library()
            if self._on_screen:
                self._start_library()
        self._set_roots()
        self._facts_timer.start()
        self._render()

    def _save_config(self) -> bool:
        try:
            self.config.save()
        except OSError as err:
            self._say("danger", f"The Rotator's settings could not be saved: {err}")
            return False
        self.config_edited.emit()
        return True

    def _set_batch(self, n: int) -> None:
        if self.state == "running" or self._showing:
            return
        self.config.count = int(n)
        if self._save_config():
            self._facts_timer.start()
            self._render_idle()

    def _set_refresh(self, on: bool) -> None:
        if self.state == "running" or self._showing:
            return      # the run has its answer already; or this is the config shown
        self.config.refresh_playlist = bool(on)
        self.next_run.refresh_note.setVisible(bool(on))
        if self._save_config():
            self._facts_timer.start()
            self._render_idle()

    # -- the next run's facts, read on a thread

    def _read_facts(self) -> None:
        if self._fixture is not None or self.history is None or self.state == "running":
            return
        self._offload.forget(self._facts_token)
        config, history = copy.copy(self.config), self.history
        self._facts_token = self._offload.run(lambda: read_facts(config, history),
                                              self._take_facts)

    def _take_facts(self, facts) -> None:
        self._facts_token = None
        if isinstance(facts, Exception):
            facts = NextRunFacts(error=str(facts))
        self.facts = facts
        self._render_idle()
        self._render_header()

    # -- the tables

    def _set_roots(self) -> None:
        self.models["reserve"].root = self.config.source
        self.models["current"].root = self.config.destination

    def _start_library(self) -> None:
        if self._fixture is not None or self._library is not None:
            return
        self._set_roots()
        roots = [r for r in (self.config.source, self.config.destination) if folder_is_set(r)]
        self._library_roots = (self.config.source, self.config.destination)
        if not roots:
            for view in ("reserve", "current"):
                self.models[view].set_names([])
            self._render_table_bars()
            return
        if self._index is None:
            self._index = LibraryIndex()
        worker = self._library = _park(LibraryIndexWorker(roots, self._index))
        worker.loaded.connect(self._library_loaded)
        worker.listed.connect(self._library_listed)
        worker.refreshed.connect(self._library_refreshed)
        worker.sized.connect(self._library_sized)
        worker.authors.connect(self._library_authors)
        worker.start()

    def _stop_library(self, wait: bool = False) -> None:
        worker, self._library = self._library, None
        if worker is not None:
            for signal in (worker.loaded, worker.listed, worker.refreshed, worker.sized,
                           worker.authors):
                try:
                    signal.disconnect()
                except (RuntimeError, TypeError):
                    pass
            worker.stop()
            if wait:
                worker.wait(3000)

    def _models_of(self, root: str) -> list[LibraryModel]:
        key = root_key(root)
        return [self.models[view] for view in ("reserve", "current")
                if self.models[view].root and root_key(self.models[view].root) == key]

    def _library_loaded(self, root: str, folders: dict) -> None:
        for model in self._models_of(root):
            model.infos = dict(folders)
            if not model.items() and folders:
                model.set_names(folders)
        self._library_changed()

    def _library_listed(self, root: str, names: list) -> None:
        for model in self._models_of(root):
            if sorted(model.items()) != sorted(names):
                model.set_names(names)
        self._library_changed()

    def _library_refreshed(self, root: str, folders: dict) -> None:
        for model in self._models_of(root):
            model.infos = dict(folders)
        self._repaint_timer.start()

    def _library_sized(self, root: str, sizes: dict) -> None:
        for model in self._models_of(root):
            model.sizes.update(sizes)
        if not self._repaint_timer.isActive():
            self._repaint_timer.start()

    def _library_authors(self, authors: dict) -> None:
        for view in ("reserve", "current"):
            self.models[view].authors = dict(authors)
        self._repaint_timer.start()

    def _fit_chips(self) -> None:
        for view in ("reserve", "current"):
            if self.models[view].fit_chips():
                self.tables[view].refresh_columns()

    def _library_changed(self) -> None:
        self._fit_chips()
        self._apply_filter()
        self._render_table_bars()
        self._sizes_timer.start()
        self._render_subtitle()

    def _repaint_library(self) -> None:
        self._fit_chips()
        for view in ("reserve", "current"):
            self.tables[view].viewport().update()
        self._render_table_bars()

    def _request_sizes(self) -> None:
        worker = self._library
        if worker is None or self.view not in ("reserve", "current"):
            return
        table, model = self.tables[self.view], self.models[self.view]
        names = [model.item_at(row) for row in table.visible_rows()]
        names = [n for n in names if n is not None and model.size_of(n) is None]
        if names and model.root:
            worker.request_sizes(model.root, names)

    def _apply_filter(self) -> None:
        text = self.filter.text().strip().casefold()
        model = self.models[self.view]
        if text:
            model.set_filter(lambda item, m=model: m.matches(item, text))
        elif model._filter is not None:
            model.set_filter(None)
        self._render_table_bars()

    def show_view(self, view: str) -> None:
        """Reserve, Current or History, in the table."""
        if view not in VIEWS:
            raise KeyError(f"no view {view!r}; there are {', '.join(VIEWS)}")
        self.view = view
        i = VIEWS.index(view)
        if self.views.current_index() != i:
            self.views.set_current_index(i)
        self.table_stack.setCurrentIndex(i)
        for other in VIEWS:
            if other != view and self.models[other]._filter is not None:
                self.models[other].set_filter(None)
        self._apply_filter()
        if self.state != "running":
            self.right.setCurrentIndex(0)
        self.tables[view].request_visible_thumbs()
        self._sizes_timer.start()
        self._render_subtitle()

    def _render_table_bars(self) -> None:
        view = self.view
        model = self.models[view]
        shown = model.item_rows()
        library = view in ("reserve", "current")
        self.check_button.setVisible(library)
        self.open_button.setVisible(library)
        self.export_button.setVisible(not library)
        self.check_button.setEnabled(self.state != "running")
        if not library:
            rows = model.items()
            self.summary.set_items(history_summary(rows))
            self.footer.set_text(HISTORY_NOTE)
            self.export_button.setEnabled(bool(rows))
            return
        root = self.config.source if view == "reserve" else self.config.destination
        total = len(model.items())
        items = [fmt.counted(total, "folder")]
        if view == "reserve":
            if model.usage is not None and total:
                items.append(f"{fmt.count(model.never_used_count())} never used")
        else:
            today = moved_on(self.history.runs, self._now().date()) if self.history else 0
            if today:
                items.append(f"{fmt.count(today)} swapped today")
        size, unmeasured = model.total_size()
        if total and not unmeasured:
            items.append(fmt.size(size))
        if root:
            items.append(short_path(root))
        else:
            items = [f"The {'reserve' if view == 'reserve' else 'myprojects'} folder is not set"]
        self.summary.set_items(items)
        footer = fmt.counted(total, "folder")
        if shown != total:
            footer = f"{fmt.count(shown)} of {footer}"
        self.footer.set_text(footer)
        self.open_button.setEnabled(bool(root))

    def refresh(self) -> None:
        """"Read again": the folders, the history and the next run's facts."""
        if self._fixture is not None:
            return
        if self.history is not None and self.state != "running":
            self.history = History.load()
            self._history_changed()
        self._stop_library()
        if self.state != "running":
            self._start_library()
            self._read_facts()

    def open_folder(self) -> None:
        root = self.config.source if self.view == "reserve" else self.config.destination
        if folder_is_set(root):
            self._explore(root)

    def _explore(self, path: str) -> None:
        try:
            external.popen(["explorer", str(path)])
        except OSError as err:
            self._say("warn", f"Explorer could not be opened: {err}")

    # -- the History view

    def _history_clicked(self, index, activated: bool = False) -> None:
        row = self.models["history"].item_at(index.row())
        if row is not None and (activated or index.column() == LOG_COLUMN):
            self.open_run_log(row.id)

    def open_run_log(self, run_id: str) -> None:
        """A run's log, read back in a dialog: its file, or — when none was
        kept — what the history recorded of it."""
        rows = {r.id: r for r in self.models["history"].items()}
        row = rows.get(run_id)
        record = row.record if row is not None else (
            self.history.find(run_id) if self.history is not None else None)
        if record is None:
            return
        number = row.number if row is not None else 0
        meta = self._metas.get(run_id)
        name = (meta.log if meta is not None and meta.log else "")
        lines, body = [], ""
        path = self._log_path(name) if name else None
        if path is not None and self._fixture is None:
            lines = LogTail(path, theme.LOG_CAP).read()
        if lines:
            body = f"{fmt.counted(len(lines), 'line')} from {name}."
        else:
            lines = record_lines(record)
            why = (f"its log is older than {KEEP_DAYS} days" if name
                   else "it ran before runs kept a log of their own")
            body = (f"The log was not kept: {why}. What the history recorded of it is "
                    "below.")
            name = ""
        title = f"Run {number}" if number else "A run"
        if row is not None and row.started is not None:
            title += f" · {fmt.date_table(row.started, self._now())}"
        dialog = RunLogDialog(title, body, lines, file_name=name,
                              on_open_folder=self._open_log_folder, parent=self._dialog_parent())
        self.last_dialog = dialog
        self._answer(dialog)

    def _log_path(self, name: str) -> Path | None:
        services = self._services
        if services is None:
            from ..settings import app_data_dir
            root = app_data_dir() / "logs"
        else:
            root = services.logs.root
        path = root / Path(name)
        return path if name else None

    def _open_log_folder(self) -> None:
        services = self._services
        if services is not None:
            try:
                services.logs.open_folder("rotator")
            except OSError as err:
                self._say("warn", f"The log folder could not be opened: {err}")

    def export_history(self) -> None:
        rows = self.models["history"].items()
        if not rows:
            return
        default = str(Path.home() / "rotator-history.csv")
        path, _ = QFileDialog.getSaveFileName(self, "Export the run history", default,
                                              "CSV files (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                f.write(history_csv(rows))
        except OSError as err:
            self._say("danger", f"The history could not be exported: {err}")
            return
        self._say("ok", f"{fmt.counted(len(rows), 'run')} exported to {Path(path).name}")

    # -- starting a run: the check first

    def start_rotation(self) -> None:
        """Start: the check, its confirmation if it finds anything, then "Start run N?"."""
        if self._busy():
            return
        self._begin_check(then_rotate=True)

    def check_folders(self) -> None:
        """"Check folders": the check on its own, without rotating after."""
        if self._busy():
            return
        self._begin_check(then_rotate=False)

    def _busy(self) -> bool:
        return self.state == "running" or self._starting

    def _begin_check(self, then_rotate: bool) -> None:
        self._ensure_loaded()
        self._starting = True
        config = copy.copy(self.config)
        history = self.history

        def look():
            roots, unset, missing = check_roots(config)
            problem = Rotator(config, history).validate() if then_rotate else None
            return roots, unset, missing, problem

        self._offload.run(look, lambda found: self._check_ready(found, then_rotate))

    def _check_ready(self, found, then_rotate: bool) -> None:
        self._starting = False
        if isinstance(found, Exception):
            self._say("danger", f"Cannot check: {found}")
            return
        roots, unset, missing, problem = found
        if problem:
            self._say("danger", f"Cannot start: {problem}")
            return
        if not roots:
            self._say("danger", "Cannot check: " + " ".join(unset))
            return
        if missing:
            self._say("danger", "Cannot check: folder not found: " + ", ".join(missing))
            return
        self._rotate_after_check = then_rotate
        self._check_finished = None
        self._check_found = None
        self._cancelled = False
        started = self._now()
        steps = plan_steps(self.config.refresh_playlist) if then_rotate else (step("check"),)
        number = len(self.history.runs) + 1 if self.history is not None else 0
        self.tracker = RunTracker("run" if then_rotate else "check", steps, number=number,
                                  batch=self.config.count, started=started)
        self._stop_library()
        self._job = begin("rotator", "Checking folders", activity="check")
        self._job_words = ""
        self.log.clear()
        self.log.set_file(self._job_log_name(), writing=True)
        for problem_ in unset:
            self._event(ProgressEvent("scan", f"{problem_} Not checked.", level="WARN", step=0))
        self._set_state("running")
        worker = self.scan_worker = _park(ReserveScanWorker(roots, step=0))
        worker.progress.connect(self._event)
        worker.finished_scan.connect(self._on_scan_finished)
        worker.error.connect(self._on_error)
        worker.start()

    def _job_log_name(self) -> str:
        job = self._job.job if self._job is not None else None
        path = getattr(job, "log_path", None)
        return f"rotator/{Path(path).name}" if path else ""

    def _where(self, broken: list[BrokenFolder]) -> str:
        roots = {root_key(b.root) for b in broken}
        names = []
        for label_, folder in (("the reserve", self.config.source),
                               ("myprojects", self.config.destination)):
            if folder_is_set(folder) and root_key(folder) in roots:
                names.append(label_)
        return " and ".join(names) or "the reserve"

    def _on_scan_finished(self, broken: list, scanned: int) -> None:
        self.scan_worker = None
        self._check_finished = self._now()
        self._check_found = (len(broken), 0)
        if self.tracker is not None:
            self.tracker.broken = len(broken)
        if self._cancelled:
            self._end_job("stopped", "The check was stopped", journal=False)
            self._back_to_idle("info", "The check was stopped — nothing was moved or deleted.")
            return
        count = len(broken)
        if broken:
            self._end_job("problems", f"{count} of {scanned} folders cannot be shown by "
                                      "Wallpaper Engine",
                          title=f"{count} folder{_s(count)} Wallpaper Engine cannot show",
                          detail=f"of {scanned} checked")
        else:
            self._end_job("clean", f"All {scanned} folders have a project.json", journal=False)
        self._render_running()
        if not broken:
            if self._rotate_after_check:
                self._confirm_and_rotate()
            else:
                self._back_to_idle("ok", f"Nothing to clean: all {fmt.count(scanned)} folders "
                                         "have a project.json.")
            return
        root = next((b.root for b in broken), "")
        dialog = broken_dialog(broken, self._where(broken), self._dialog_parent(),
                               scanned=scanned, open_folder=lambda: self._explore(root))
        self.last_dialog = dialog
        answer = self._answer(dialog)
        confirmed = [row.data for row in answer.checked] if answer else []
        if not confirmed:
            self._event(ProgressEvent("scan", "Cleanup skipped — nothing was deleted.",
                                      level="WARN", step=0))
            if self._rotate_after_check:
                self._confirm_and_rotate()
            else:
                self._back_to_idle("info", "Cleanup skipped — nothing was deleted.")
            return
        self._job = begin("rotator", f"Deleting {len(confirmed)} folders", activity="cleanup")
        self._job_words = ""
        worker = self.cleanup_worker = _park(CleanupWorker(confirmed, step=0))
        worker.progress.connect(self._event)
        worker.finished_action.connect(self._on_cleanup_finished)
        worker.error.connect(self._on_error)
        worker.start()

    def _on_cleanup_finished(self, failed: list) -> None:
        asked = len(self.cleanup_worker.paths) if self.cleanup_worker else len(failed)
        self.cleanup_worker = None
        self._check_finished = self._now()
        deleted = asked - len(failed)
        if self._check_found is not None:
            self._check_found = (self._check_found[0], deleted)
        self._end_job("problems" if failed else "clean", f"{deleted} of {asked} folders deleted",
                      title=f"{deleted} folder{_s(deleted)} Wallpaper Engine could not show "
                            "deleted",
                      detail=f"{len(failed)} could not be deleted" if failed else "",
                      chip="Failed" if failed else None)
        if failed:
            self._say("warn", f"{fmt.counted(len(failed), 'folder')} could not be deleted: "
                              + ", ".join(Path(f).name for f in failed[:20]))
        if self._rotate_after_check:
            self._confirm_and_rotate()
        else:
            if not failed:
                self._back_to_idle("ok", "The folders were deleted.")
            else:
                self._back_to_idle(None, "")

    # -- "Start run N?", then the run

    def _confirm_and_rotate(self) -> None:
        self._rotate_after_check = False
        config, history = copy.copy(self.config), self.history
        self._offload.run(lambda: read_facts(config, history), self._confirm_with)

    def _confirm_with(self, facts) -> None:
        if isinstance(facts, Exception) or facts.preview is None:
            why = facts if isinstance(facts, Exception) else (facts.error or facts.problem)
            self._back_to_idle("danger", f"Cannot start: {why}")
            return
        if facts.problem:
            self._back_to_idle("danger", f"Cannot start: {facts.problem}")
            return
        self.facts = facts
        number = len(self.history.runs) + 1
        notices = (self.history.notice, getattr(self.config, "notice", ""))
        c = confirmation(facts.preview, number, refresh=self.config.refresh_playlist,
                         labels=facts.labels, duplicates_folder=self.config.duplicates,
                         notices=notices)
        dialog = start_dialog(c, self._dialog_parent())
        self.last_dialog = dialog
        if not self._answer(dialog):
            self._back_to_idle(None, "")
            return
        self._rotate(facts.preview, number)

    def _rotate(self, p: dict, number: int) -> None:
        tracker = self.tracker
        started = tracker.started if tracker is not None else self._now()
        worker = RotationWorker(self.config, self.history, check_finished=self._check_finished,
                                started=started)
        self.rotation_worker = _park(worker)
        steps = worker.steps
        if tracker is None or tracker.steps != steps:
            old = tracker
            tracker = self.tracker = RunTracker("run", steps, number=number,
                                                batch=self.config.count, started=started)
            if old is not None and old.steps and steps and steps[0].key == "check":
                tracker.states[0], tracker.ended = "done", dict(old.ended)
                tracker.broken, tracker.deleted = old.broken, old.deleted
        tracker.number = number
        tracker.kind = "run"
        self._cancelled = False
        run_id = worker.run_id
        self._job = begin("rotator", f"Run {number}", activity="run", run_id=run_id)
        self._job_words = ""
        self._job.note("run.started", f"Run {number} started",
                       f"{p['returning']} to return · {p['count']} to move into myprojects",
                       run=run_id)
        self.log.set_file(self._job_log_name(), writing=True)
        worker.progress.connect(self._event)
        worker.finished_run.connect(self._on_finished)
        worker.error.connect(self._on_error)
        self._set_state("running")
        worker.start()

    def stop_after_step(self) -> None:
        """"Stop after this step": the check stops where it is (it only reads);
        a rotation finishes the step under way; a retry stops before its next
        folder."""
        tracker = self.tracker
        if tracker is None:
            return
        stopped = False
        for worker, how in ((self.scan_worker, "cancel"), (self.rotation_worker, "stop_after_step"),
                            (self.retry_worker, "cancel")):
            if worker is not None and worker.isRunning():
                getattr(worker, how)()
                stopped = True
        if not stopped:
            return
        self._cancelled = True
        tracker.stopping = True
        self._event(ProgressEvent("cancel", "Stop requested — the run stops once this step is "
                                            "done.", level="WARN", kind="stop"))
        self._render_running()

    # -- the events

    def _event(self, e: ProgressEvent) -> None:
        now = self._now()
        if self.tracker is not None:
            self.tracker.event(e, now)
        self.log.append(now, e.kind, e.message)
        run = self._job
        if run is not None:
            run.log(e.kind, e.message)
            words = self.tracker.status_text() if self.tracker is not None else ""
            if e.total:
                run.update(words, e.current, e.total)
            elif words != self._job_words:
                run.update(words, 0, 0)
            self._job_words = words
        if not self._render_timer.isActive():
            self._render_timer.start()

    def _end_job(self, result: str, summary: str, **journal) -> None:
        job, self._job = self._job, None
        if job is not None:
            job.finish(result, summary, **journal)

    def _on_finished(self, record: RunRecord) -> None:
        worker = self.rotation_worker
        self.rotation_worker = None
        result = worker.result if worker is not None and worker.result else (
            "stopped" if self._cancelled else "problems" if record.failed else "clean")
        recorded = self.history is not None and any(r is record for r in self.history.runs)
        self._report_run(record, result, recorded)
        if self.tracker is not None:
            self.tracker.finish(result, self._now())
        self._history_changed()
        if not recorded:
            self._back_to_idle("info", "The rotation was stopped before it moved anything.")
            return
        number = self.history.number(record)
        error = worker.job.error if worker is not None and result == "failed" else ""
        meta = worker.meta if worker is not None else self._metas.get(record.id)
        self.done_view = DoneView(record, meta, number, check=self._check_found, error=error)
        self._set_state("done")
        self._after_work()
        title = result_title(number, result, len(record.failed),
                             meta.stopped_after if meta else "")
        self._finished_toast(title, {"clean": "ok", "problems": "warn"}.get(result, "danger"))

    def _report_run(self, record: RunRecord, result: str, recorded: bool) -> None:
        """The run to the status line and the journal. A run stopped before it
        moved anything is not in the history, so it has no number."""
        job = self._job
        if job is None:
            return
        runs = self.history.runs if self.history is not None else []
        number = len(runs) if recorded else len(runs) + 1
        run_id = record.id if recorded else None
        failed, dups = len(record.failed), record.duplicate_count
        if result == "stopped":
            title = f"Run {number} stopped" if recorded else "The rotation was stopped"
        elif result == "failed":
            title = f"Run {number} failed"
        elif result == "problems":
            count = failed or 1
            title = f"Run {number} finished with {count} problem{_s(count)}"
        else:
            title = f"Run {number} finished"
        parts = [f"{record.moved_count} moved in", f"{record.returned} returned"]
        if dups:
            parts.append(f"{dups} duplicate{_s(dups)}")
            job.note("duplicates.set_aside", f"{dups} duplicate{_s(dups)} set aside",
                     f"moved to {self.config.duplicates}", chip="Duplicated", run=run_id)
        if failed:
            parts.append(f"{failed} failed")
        self._end_job(result, f"Moved {record.moved_count}, returned {record.returned}, "
                              f"duplicates {dups}, failed {failed}",
                      title=title, detail=" · ".join(parts),
                      chip="Failed" if failed else None, run=run_id)

    def _on_error(self, message: str) -> None:
        job, self._job = self._job, None
        if job is not None:
            job.fail(message)
        self.scan_worker = self.cleanup_worker = self.rotation_worker = None
        self.retry_worker = self.rebuild_worker = None
        self._back_to_idle("danger", f"Rotation error: {message}")

    def _after_work(self) -> None:
        """A piece of work has ended: the folders and the next run are read again."""
        if self._on_screen and self._fixture is None:
            self._start_library()
            self._read_facts()

    def _back_to_idle(self, tone: str | None, words: str) -> None:
        if tone and words:
            self._say(tone, words)
        self.tracker = None
        self._set_state("done" if self.done_view is not None else "idle")
        self._after_work()

    def next_run_view(self) -> None:
        """"Next run": from a finished run back to setting up the next."""
        self.done_view = None
        self.models["history"].marked = None
        self._set_state("idle")
        self.show_view("reserve")

    # -- retrying the failures, rebuilding the playlist

    def retry_failures(self) -> None:
        view = self.done_view
        if view is None or self._busy():
            return
        meta = view.meta
        returning, moving, _unknown = Rotator.split_failures(
            view.record, meta.returned_failed if meta else None,
            meta.moved_failed if meta else None)
        n = len(returning) + len(moving)
        if not n:
            return
        steps = []
        for names, what in ((returning, "back to the reserve"), (moving, "into myprojects")):
            if names:
                shown = ", ".join(names[:3]) + (f" and {len(names) - 3} more" if len(names) > 3 else "")
                steps.append((f"Move {fmt.counted(len(names), 'folder')} {what}", shown))
        if self.config.refresh_playlist:
            steps.append((step_title("playlist"), "Wallpaper Engine restarts once"))
        dialog = ConfirmDialog(
            f"Retry the {fmt.count(n)} failure{_s(n)}?",
            f"Each folder that did not move is tried again in the step it failed in. What "
            f"has been put right by hand since is left as it is.", self._dialog_parent(),
            icon="refresh", tone="accent", steps=steps, confirm_text="Retry")
        self.last_dialog = dialog
        if not self._answer(dialog):
            return
        worker = RetryWorker(self.config, self.history, view.record)
        if not worker.job.retryable:
            self._say("warn", "Nothing recorded which step these failed in; move them by hand.")
            return
        self.retry_worker = _park(worker)
        self.tracker = RunTracker("retry", worker.steps, number=view.number,
                                  batch=self.config.count, started=self._now())
        self._stop_library()
        self._job = begin("rotator", f"Retrying run {view.number}", activity="retry",
                          run_id=view.record.id)
        self._job_words = ""
        self.log.clear()
        self.log.set_file(self._job_log_name(), writing=True)
        worker.progress.connect(self._event)
        worker.finished_retry.connect(self._on_retried)
        worker.error.connect(self._on_error)
        self._set_state("running")
        worker.start()

    def _on_retried(self, outcome) -> None:
        worker, self.retry_worker = self.retry_worker, None
        view = self.done_view
        result = worker.result if worker is not None else "problems"
        if self.tracker is not None:
            self.tracker.finish(result, self._now())
        fixed, retried = outcome.fixed + outcome.resolved, outcome.retried
        left = len(outcome.still_failed)
        title = (f"Run {view.number}: {fmt.count(fixed)} of {fmt.count(retried)} failure"
                 f"{_s(retried)} put right" if view is not None else "Retry finished")
        self._end_job(result, f"{fixed} of {retried} put right, {left} still failing",
                      title=title, detail=f"{left} still failing" if left else "",
                      chip="Failed" if left else None,
                      run=view.record.id if view is not None else None)
        self._history_changed()
        if view is not None and worker is not None:
            view.meta = worker.meta
        self._set_state("done" if view is not None else "idle")
        self._after_work()
        self._finished_toast(title, "ok" if result == "clean" else "warn")

    def rebuild_playlist(self) -> None:
        """"Rebuild playlist now": the last step of a run, on its own."""
        if self._busy():
            return
        dialog = ConfirmDialog(
            "Rebuild the playlist now?",
            "Wallpaper Engine closes, the rotation's playlist is refilled with what is in "
            "myprojects, and Wallpaper Engine starts again on a fresh pass.",
            self._dialog_parent(), icon="refresh", tone="accent",
            confirm_text="Rebuild playlist")
        self.last_dialog = dialog
        if not self._answer(dialog):
            return
        runs = list(self.history.runs) if self.history is not None else []
        worker = self.rebuild_worker = _park(PlaylistRebuildWorker(self.config.destination,
                                                                   runs=runs))
        self.tracker = RunTracker("rebuild", worker.steps, started=self._now())
        self._job = begin("rotator", "Rebuilding the playlist", activity="rebuild")
        self._job_words = ""
        self.log.clear()
        self.log.set_file(self._job_log_name(), writing=True)
        worker.progress.connect(self._event)
        worker.finished_rebuild.connect(self._on_rebuilt)
        worker.error.connect(self._on_error)
        self._set_state("running")
        worker.start()

    def _on_rebuilt(self, refresh) -> None:
        worker, self.rebuild_worker = self.rebuild_worker, None
        result = worker.result if worker is not None else "problems"
        if self.tracker is not None:
            self.tracker.finish(result, self._now())
        summary = " ".join(refresh.summary) or "The playlist was rebuilt."
        title = ("The playlist was rebuilt" if result == "clean"
                 else "The playlist could not be rebuilt")
        self._end_job(result, summary, title=title,
                      detail=refresh.problems[0] if refresh.problems else "")
        if self.done_view is not None and result == "clean":
            self.done_view.rebuilt = True
        self._set_state("done" if self.done_view is not None else "idle")
        self._after_work()
        if result != "clean":
            self._say("warn", refresh.problems[0] if refresh.problems else title)
        else:
            self._finished_toast(title, "ok")

    # -- duplicates

    def open_duplicates(self) -> None:
        if self._busy():
            return
        problem = folder_problem("duplicates", self.config.duplicates)
        if problem:
            self._say("warn", f"{problem} Choose it in Settings.")
            return
        dialog = DuplicatesDialog(self.config, self._dialog_parent(), answer=self._answer)
        self.last_dialog = dialog
        if self._fixture is None:
            dialog.start()
        answer = self._answer(dialog)
        if answer:
            self._dup_action(*answer)

    def _dup_action(self, action: str, names: list[str]) -> None:
        # The dialog asked; the Settings page may have changed a folder since.
        problem = folder_problem("duplicates", self.config.duplicates)
        if problem is None and action == "replace":
            problem = folder_problem("reserve", self.config.source)
        if problem:
            self._say("warn", f"{problem} Choose it in Settings.")
            return
        if not names:
            return
        count = len(names)
        if action == "delete":
            self._dup_job = begin("rotator", f"Deleting {count} duplicates",
                                  activity="duplicates_delete")
            self._dup_job.update("deleting folders in the duplicates folder", 0, count)
        else:
            self._dup_job = begin("rotator", f"Moving {count} duplicates into the reserve",
                                  activity="duplicates_return")
            self._dup_job.update("moving duplicates into the reserve", 0, count)
        worker = self.dup_worker = _park(DuplicateActionWorker(action, self.config, names))
        worker.progress.connect(self._on_dup_progress)
        worker.finished_action.connect(self._on_dup_finished)
        worker.error.connect(self._on_dup_error)
        worker.start()
        self._render_header()

    def _on_dup_progress(self, e: ProgressEvent) -> None:
        if self._dup_job is not None:
            self._dup_job.log(e.kind, e.message)
            if e.total:
                self._dup_job.update(None, e.current, e.total)

    def _on_dup_error(self, message: str) -> None:
        job, self._dup_job = self._dup_job, None
        self.dup_worker = None
        if job is not None:
            job.fail(message)
        self._say("danger", f"Duplicates: {message}")
        self._render_header()

    def _on_dup_finished(self, failed: list) -> None:
        worker, self.dup_worker = self.dup_worker, None
        job, self._dup_job = self._dup_job, None
        asked = len(worker.names) if worker is not None else len(failed)
        done = asked - len(failed)
        what = ("deleted" if worker is not None and worker.action == "delete"
                else "moved back into the reserve")
        if job is not None:
            job.finish("problems" if failed else "clean", f"{done} of {asked} duplicates {what}",
                       title=f"{done} duplicate{_s(done)} {what}",
                       detail=(f"{len(failed)} failed" if failed
                               else f"from {self.config.duplicates}"),
                       chip="Failed" if failed else None)
        if failed:
            self._say("warn", f"{fmt.counted(len(failed), 'folder')} failed: "
                              + ", ".join(failed[:20]))
        else:
            self._say("ok", f"{fmt.counted(done, 'duplicate')} {what}.")
        self._after_work()
        self._render_header()

    # -- what the window's services say

    def _jobs_changed(self, *_args) -> None:
        self._update_nav()

    def _snapshot_refreshed(self, keys) -> None:
        if LAST_RUN in keys:
            self._update_nav()
            self._render_subtitle()
        if PLAYLIST in keys and self.state == "done":
            self._render_done()

    def _update_nav(self) -> None:
        if self._services is None:
            return
        self.set_nav_state(nav_state(self._services.jobs, self._services.snapshot))

    # -- putting it on screen

    def _set_state(self, state: str) -> None:
        self.state = state
        idle, running, done = state == "idle", state == "running", state == "done"
        self.next_run.setVisible(idle)
        self.plan.setVisible(idle)
        self.run_panel.setVisible(running)
        self.result.setVisible(done)
        self.steps.setVisible(running or done)
        self.steps.metrics.setVisible(running)
        self.right.setCurrentIndex(1 if running else 0)
        self.log.set_live(running)
        if running:
            self.log.set_expanded(True)
        if done:
            self.show_view("history")
        self._render()

    def _render(self) -> None:
        if self.state == "idle":
            self._render_idle()
        elif self.state == "running":
            self._render_running()
        else:
            self._render_done()
        self._render_header()
        self._render_subtitle()
        self._render_table_bars()

    def _render_idle(self) -> None:
        n, facts = self.next_run, self.facts
        number = len(self.history.runs) + 1 if self.history is not None else None
        n.head.set_subtitle(f"run {number}" if number else "")
        p = facts.preview if facts is not None else None
        if p is not None:
            tone, words = selection_line(p)
            n.selection.setVisible(tone != "warn")
            n.selection.setText(words)
            n.reset.set_body(words if tone == "warn" else "")
            n.reset.setVisible(tone == "warn")
            n.set_protected(protected_line(p))
        else:
            n.selection.setVisible(True)
            n.selection.setText("Counting the folders…" if self._offload.busy()
                                or self.history is None else "")
            n.reset.hide()
            n.set_protected("")
        notices = [x for x in ((self.history.notice if self.history is not None else ""),
                               getattr(self.config, "notice", "")) if x]
        n.notice.set_body(" ".join(notices))
        n.notice.setVisible(bool(notices))
        problem = (facts.problem or facts.error) if facts is not None else ""
        if not problem:
            unset = folder_problem("reserve", self.config.source) or folder_problem(
                "myprojects", self.config.destination) or folder_problem(
                "duplicates", self.config.duplicates)
            problem = unset or ""
        n.problem.set_body(problem or "")
        n.problem.setVisible(bool(problem))
        rows = plan_rows(p, batch=self.config.count, refresh=self.config.refresh_playlist,
                         labels=facts.labels if facts is not None else None)
        self.plan.steps.set_steps([(title, caption or "…") for title, caption in rows])
        self.plan.estimate.setText(estimate_line(facts.estimate if facts is not None else None))
        self.plan.start.setEnabled(not problem and self.state == "idle"
                                   and self.history is not None)

    def _left(self) -> str | None:
        if self._fixture_left is not None:
            return self._fixture_left
        job = self._job.job if self._job is not None else None
        eta = job.eta() if job is not None else None
        return fmt.left(eta) if eta else None

    def _render_running(self) -> None:
        t = self.tracker
        if t is None or self.state != "running":
            return
        left = self._left()
        self.run_panel.show_tracker(t, left)
        self.steps.set_rows(t.step_rows(left))
        for i, item in enumerate(t.metrics()):
            self.steps.metrics.set_value(i, item[0], item[2] if len(item) > 2 else None)
        self._render_subtitle()

    def _render_done(self) -> None:
        view = self.done_view
        if view is None:
            return
        record, meta = view.record, view.meta
        result = view.result
        tone = {"clean": "ok", "problems": "warn", "stopped": "warn"}.get(result, "danger")
        r = self.result
        r.set_tone(tone)
        r.disc.set({"clean": "check", "problems": "warn", "stopped": "stop"}.get(result, "warn"),
                   tone)
        r.title.setText(result_title(view.number, result, len(record.failed),
                                     meta.stopped_after if meta else ""))
        seconds = meta.seconds if meta is not None else None
        r.took.setText(fmt.duration(seconds, exact=False) if seconds else "")
        r.sentence.setText(result_sentence(record, meta, error=view.error, rebuilt=view.rebuilt))
        failed = len(record.failed)
        r.metrics.set_value(0, record.moved_count)
        r.metrics.set_value(1, record.returned)
        r.metrics.set_value(2, record.duplicate_count)
        r.metrics.set_value(3, failed, "danger" if failed else ("ok" if result == "clean" else None))
        r.metrics.set_caption(3, "failed" if failed else "problems")
        returning, moving, _ = Rotator.split_failures(
            record, meta.returned_failed if meta else None, meta.moved_failed if meta else None)
        retryable = len(returning) + len(moving)
        r.retry.setText(f"Retry the {fmt.count(retryable)} failure{_s(retryable)}")
        r.retry.setVisible(bool(retryable))
        r.open_log.setVisible(bool(meta is not None and meta.log))
        r.history.setVisible(not retryable)
        rows = done_steps(record, meta, check=view.check, rebuilt=view.rebuilt)
        self.steps.set_rows(rows)
        s = self.steps
        s.failure.set_body(view.error)
        s.failure.setVisible(bool(view.error))
        stale = playlist_stale(meta, view.rebuilt)
        s.stale.setVisible(stale)
        s.rebuild.setVisible(stale)
        note = ""
        if result == "clean" and meta is not None and meta.playlist_rebuilt and self._services:
            note = tracker_note(self._services.snapshot[PLAYLIST].value, self._now())
        s.note.set_body(note)
        s.note.setVisible(bool(note))
        self.models["history"].marked = (record.id, tone)
        self.tables["history"].viewport().update()

    def _render_header(self) -> None:
        if not self._header:
            return
        running = self.state == "running"
        problems = self.state == "done" and self.done_view is not None and \
            self.done_view.result != "clean"
        count = self.facts.duplicates if self.facts is not None else None
        busy = self.dup_worker is not None
        duplicates = self._header["duplicates"]
        duplicates.setText(f"Duplicates · {fmt.count(count)}" if count else "Duplicates")
        duplicates.setVisible(bool(count) and not running)
        duplicates.setEnabled(not busy)
        self._header["settings"].setVisible(running or problems)
        self._header["history"].setVisible(not running)

    def _render_subtitle(self) -> None:
        now = self._now()
        if self.state == "running" and self.tracker is not None:
            text = running_subtitle(self.tracker)
        elif self.state == "done" and self.done_view is not None:
            view = self.done_view
            meta = view.meta
            finished = meta.finished_at if meta is not None else None
            text = done_subtitle(view.number, view.result, finished, len(view.record.failed), now)
        elif self.view == "current" and self.models["current"].items():
            n = len([name for name in self.models["current"].items() if not is_protected(name)])
            text = f"{fmt.counted(n, 'folder')} in rotation"
        else:
            when, result, ended = self._last_ended()
            text = idle_subtitle(when, result, now, ended=ended)
        self.set_subtitle(text)

    def _last_ended(self) -> tuple[datetime | None, str | None, bool]:
        """When the last run ended, and how: this session's, else the
        history's; and whether that is its end or only its start."""
        services = self._services
        if services is not None:
            job = services.jobs.last_finished("rotator")
            if job is not None and job.title.startswith("Run ") and job.ended is not None:
                return job.ended, job.result, True
            last = services.snapshot[LAST_RUN].value
            if last is not None:
                return last.finished or last.started, last.result, last.finished is not None
        if self.history is not None and self.history.runs:
            row = history_rows(self.history.runs[:1], self._metas)[0]
            return row.finished or row.started, row.result, row.finished is not None
        return None, None, False

    # -- telling the user

    def _say(self, tone: str, words: str) -> None:
        """A message that used to be a message box: a toast, and a line kept
        for tests. Danger ones stay until closed."""
        self.messages.append((tone, words))
        toasts = getattr(self.window(), "toasts", None)
        if toasts is not None:
            toasts.show_toast(words, {"danger": "danger", "warn": "warn", "ok": "ok"}.get(
                tone, "info"))

    def _finished_toast(self, title: str, tone: str) -> None:
        """A run that ended while another page was on screen says so there."""
        if self._on_screen:
            return
        self.messages.append((tone, title))
        toasts = getattr(self.window(), "toasts", None)
        if toasts is not None:
            toasts.show_toast(title, tone, action="Show",
                              on_action=lambda: self.navigate.emit("rotator"))

    def _dialog_parent(self) -> QWidget:
        return self.window() if self.window() is not None else self

    def _answer(self, dialog):
        """Ask. Tests put their own answer here."""
        return dialog.ask()

    # -- for tests and snapshots

    def texts(self) -> dict:
        return {"state": self.state, "subtitle": self.subtitle(), "view": self.view,
                "next": self.next_run.texts(),
                "plan": [(self.plan.steps.title(i), self.plan.steps.caption(i))
                         for i in range(len(self.plan.steps))],
                "estimate": self.plan.estimate.text(), "start": self.plan.start.isEnabled(),
                "run": self.run_panel.texts(), "result": self.result.texts(),
                "steps": self.steps.rows(), "summary": self.summary.items(),
                "footer": self.footer.text()}

    def frame_fixture(self, state: str) -> dict | None:
        if state not in self.FIXTURES:
            return None
        spec = load_rotator_fixture()["states"][state]
        return {"frame": spec.get("frame", "idle")}

    def load_fixture(self, state: str) -> None:
        """A made-up state from tests/fixtures/ui/rotator.json: its folders on X:,
        its history, what the next run would do, and a run under way or ended."""
        if state not in self.FIXTURES:
            super().load_fixture(state)
        data = load_rotator_fixture()
        spec = data["states"][state]
        clock = datetime.strptime(data["now"], "%H:%M").time()
        now = datetime.combine(date.today(), clock)
        self._fixture = spec
        self._now = lambda: now
        self._stop_library()
        for timer in (self._facts_timer, self._sizes_timer):
            timer.stop()
        c = data["config"]
        self.config.source, self.config.destination = c["source"], c["destination"]
        self.config.duplicates, self.config.count = c["duplicates"], c["count"]
        self.config.refresh_playlist = c["refresh_playlist"]
        self._show_config()
        self._set_roots()
        fixture = build_fixture(data, spec, now)
        self.history = History(fixture.runs)
        self._metas = fixture.metas
        for view in ("reserve", "current"):
            model = self.models[view]
            model.show_thumbs = False
            model.usage, model.now = fixture.usage, now
            model.infos = fixture.infos[view]
            model.sizes = {}
            model.authors = fixture.authors
            model.set_names(fixture.names[view])
        self._fit_chips()
        self._set_history_rows()
        self.facts = fixture.facts
        self.done_view = None
        self.tracker = None
        self._fixture_left = None
        self.models["history"].marked = None
        if "running" in spec:
            self._load_running(data, spec["running"], now)
        elif "done" in spec:
            self._load_done(spec["done"], now)
        else:
            self._set_state("idle")
            self.show_view(spec.get("view", "reserve"))
        if spec.get("dialog") == "confirm":
            p = fixture.facts.preview
            c = confirmation(p, len(self.history.runs) + 1, refresh=self.config.refresh_playlist,
                             labels=fixture.facts.labels, duplicates_folder=self.config.duplicates)
            self._show_fixture_dialog(start_dialog(c, self._dialog_parent()))
        elif spec.get("dialog") == "broken":
            broken = fixture_broken(data, self.config.source)
            self._show_fixture_dialog(broken_dialog(broken, "the reserve", self._dialog_parent(),
                                                    scanned=len(fixture.names["reserve"]),
                                                    open_folder=lambda: None))
        self._render_header()

    def _show_fixture_dialog(self, dialog) -> None:
        self.fixture_dialog = dialog
        self.last_dialog = dialog
        dialog.show()

    def _load_running(self, data: dict, spec: dict, now: datetime) -> None:
        steps = plan_steps(self.config.refresh_playlist)
        started = datetime.combine(now.date(), datetime.strptime(spec["started"], "%H:%M").time())
        t = self.tracker = RunTracker("run", steps, number=len(self.history.runs) + 1,
                                      batch=self.config.count, started=started)
        index = spec["step"]
        for i in range(index):
            t.states[i] = "done"
            t.ended[i] = datetime.combine(now.date(), datetime.strptime(
                spec["ended"][i], "%H:%M").time())
        t.index = index
        t.states[index] = "active"
        t.phase = steps[index].phases[-1]
        t.done, t.total = spec["done"], spec["total"]
        t.moved, t.returned, t.dupes = spec["moved"], spec["returned"], spec["dupes"]
        t.fails = list(spec["fails"])
        t.failed = sum(t.fails)
        t.broken = spec.get("broken", 0)
        t.activity = spec["activity"]
        self._fixture_left = spec.get("left")
        self.log.clear()
        self.log.set_file(f"rotator/run-{spec.get('id', 'f1x7ur3s')}.log", writing=True)
        self.log.extend([(time, kind, message) for time, kind, message in spec["log"]])
        self._set_state("running")

    def _load_done(self, spec: dict, now: datetime) -> None:
        record = self.history.runs[0]
        meta = self._metas.get(record.id)
        check = tuple(spec["check"]) if "check" in spec else None
        self.done_view = DoneView(record, meta, len(self.history.runs), check=check)
        if self._services is not None and "tracker" in spec:
            from ..services.snapshot import PlaylistProgress
            total = spec["tracker"]["total"]
            finish = now + timedelta(days=spec["tracker"]["days"])
            self._services.snapshot.put(PLAYLIST, PlaylistProgress(
                "Monitor1", 0, total, total, 0, now.strftime("%Y-%m-%d %H:%M"),
                finish.strftime("%d %b 09:00"), True))
        self._set_state("done")


# ---- fixtures -------------------------------------------------------------------------------------------

FIXTURE_FILE = (Path(__file__).resolve().parent.parent.parent
                / "tests" / "fixtures" / "ui" / "rotator.json")


def load_rotator_fixture() -> dict:
    """tests/fixtures/ui/rotator.json, from a source checkout."""
    data = json.loads(FIXTURE_FILE.read_text(encoding="utf-8"))
    data.pop("//", None)
    return data


class _FixtureUsage:
    """LAST USED and New for made-up folders: the first `used` of the reserve's
    were moved in at some point, the rest never; myprojects' all were."""

    def __init__(self, used: set[str], last: dict[str, datetime]):
        self._used, self._last = used, last

    def last_used(self, name: str) -> datetime | None:
        return self._last.get(name)

    def never_used(self, name: str) -> bool:
        return name not in self._used


@dataclass
class _Fixture:
    names: dict[str, list[str]]
    infos: dict[str, dict[str, FolderInfo]]
    authors: dict[str, str]
    usage: _FixtureUsage
    runs: list[RunRecord]
    metas: dict[str, RunMeta]
    facts: NextRunFacts


def fixture_names(data: dict, count: int, offset: int = 0) -> list[str]:
    words = data["words"]
    names = []
    for k in range(offset, offset + count):
        a, b = words[(k * 7) % len(words)], words[(k * 13 + 3) % len(words)]
        style = k % 4
        if style == 0:
            names.append(f"{a.lower()}_{b.lower()}-{k:05d}")
        elif style == 1:
            names.append(f"{a} {b} {k}")
        elif style == 2:
            names.append(f"{a}{b}_{k:04d}")
        else:
            names.append(f"{a.lower()}-{b.lower()}-{k}")
    return names


def build_fixture(data: dict, spec: dict, now: datetime) -> _Fixture:
    """The made-up reserve, myprojects and history a state shows. Everything is
    invented: names from the word list, folders on X:."""
    reserve_spec, current_spec = data["reserve"], data["current"]
    keep = spec.get("never_used", reserve_spec["never_used"])
    reserve = fixture_names(data, reserve_spec["count"])
    current = fixture_names(data, current_spec["count"], offset=reserve_spec["count"])
    protected = [f"[protected] {data['words'][i]} keep" for i in range(current_spec["protected"])]
    kinds, authors = data["kinds"], data["authors"]
    by_id: dict[str, str] = {}
    infos: dict[str, dict[str, FolderInfo]] = {"reserve": {}, "current": {}}
    for view, names in (("reserve", reserve), ("current", current + protected)):
        for k, name in enumerate(names):
            if view == "current" and k % 97 == 41:
                infos[view][name] = FolderInfo(name, unidentified=True,
                                               size=(k % 7) * 1024)
                continue
            wid = f"{k:06d}" if k % 3 else ""
            if wid and k % 5:
                by_id[wid] = authors[k % len(authors)]
            size = ((k * 7919) % 900 + 12) * 1024 * 1024 + (k % 1000) * 1024
            infos[view][name] = FolderInfo(name, title=name, kind=kinds[k % len(kinds)],
                                           workshop_id=wid, size=size)
    # Never used: `keep` of the reserve, spread through it rather than bunched
    # (a stride through the list, so the same folders every time).
    stride = sorted(range(len(reserve)), key=lambda k: (k * 7919) % len(reserve))
    fresh = {reserve[k] for k in stride[:keep]}
    used_list = [name for name in reserve if name not in fresh]
    used = set(used_list) | set(current)
    history = data["history"]
    batch = history["batch"]
    count = spec.get("runs", history["runs"])
    first = now - timedelta(days=history["every_days"] * (count - 1))
    last_time = datetime.strptime(spec.get("last_started", history["time"]), "%H:%M").time()
    runs, metas, last = [], {}, {}
    special = {int(k): v for k, v in history["special"].items()}
    special.update({int(k): v for k, v in spec.get("special", {}).items()})
    pool = used_list
    for number in range(1, count + 1):
        day = (first + timedelta(days=history["every_days"] * (number - 1))).date()
        when = datetime.combine(day, last_time if number == count
                                else datetime.strptime(history["time"], "%H:%M").time())
        extra = special.get(number, {})
        moved_n = extra.get("moved", batch)
        moved = (current if number == count else
                 [pool[(number * 31 + j * 29) % len(pool)] for j in range(moved_n)])[:moved_n]
        for name in moved:
            last[name] = when
        failed = [f"{data['words'][j]} held" for j in range(extra.get("failed", 0))]
        record = RunRecord(id=f"f{number:07d}", timestamp=when.strftime("%Y-%m-%d %H:%M:%S"),
                           moved=list(moved), returned=extra.get("returned", batch),
                           duplicates=[f"{data['words'][j]} twin" for j in
                                       range(extra.get("dupes", 0))],
                           failed=failed)
        runs.insert(0, record)
        if number >= history["meta_from"]:
            seconds = extra.get("seconds", 900 + (number * 37) % 120)
            finished = when + timedelta(seconds=seconds)
            result = extra.get("result", "problems" if failed else "clean")
            times = {}
            for key, minutes in extra.get("step_times", {}).items():
                times[key] = (when + timedelta(minutes=minutes)).isoformat()
            metas[record.id] = RunMeta(
                started=when.isoformat(), finished=finished.isoformat(), seconds=seconds,
                result=result, batch=batch, protected=len(protected),
                returned_failed=failed if extra.get("failed_in") == "return" else [],
                moved_failed=failed if extra.get("failed_in") == "move" else [],
                playlist=extra.get("playlist", ["Playlist rebuilt with 1000 wallpapers (custom)."]),
                playlist_rebuilt=extra.get("playlist_rebuilt", True),
                playlist_problem=extra.get("playlist_problem", ""),
                stopped_after=extra.get("stopped_after", ""),
                log=f"rotator/run-{record.id}.log", step_times=times)
    for name in used_list:
        last.setdefault(name, first + timedelta(days=sum(map(ord, name)) % 200))
    p = dict(data["facts"]["preview"])
    p.update(spec.get("preview", {}))
    p["available_unique"] = keep
    p["reserve_now"] = len(reserve)
    p["in_dest"] = len(current)
    p["protected"] = len(protected)
    p["projected_reserve"] = len(reserve) + p["returning"]
    p["will_reset"] = keep < p["count"]
    facts = NextRunFacts(None, p, tuple(data["facts"]["labels"]), data["facts"]["estimate"],
                         data["facts"]["duplicates"])
    return _Fixture({"reserve": reserve, "current": current + protected}, infos, by_id,
                    _FixtureUsage(used, last), runs, metas, facts)


def fixture_broken(data: dict, root: str) -> list[BrokenFolder]:
    found = []
    for spec in data["broken"]:
        found.append(BrokenFolder(spec["name"], spec["reason"], root, entries=spec.get("entries", []),
                                  files=spec.get("files", 0), size=spec.get("size", 0),
                                  holds_media=spec.get("media", False)))
    return found
