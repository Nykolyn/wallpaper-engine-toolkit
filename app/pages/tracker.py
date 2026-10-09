"""Tracker: how far Wallpaper Engine has got through each monitor's playlist.

The leading monitor in detail — the ring and "4 / 201", what is on screen,
who made it, how long it has been up, how long it has left and when the
cycle began — the other monitors as summaries, the pace with its caveats,
and the playlist itself as a table: what this cycle has shown, newest first,
then what is still to come.

Where the numbers come from:

- The window's `TrackerFeed` (its `results`, one `Progress` per monitor),
  which looks whenever Wallpaper Engine writes its files. The leading monitor
  is `pick_primary` with the lead chosen on the Settings page (or on a card's
  menu, "Show on the tray icon").
- The time left on a wallpaper: a `WallpaperTimer` of the page's own, ticking
  once a second while the page is on screen. It reads nothing from Wallpaper
  Engine's memory (the tray does that) and writes nothing: it starts from the
  tray's saved count (`data/wallpaper_timer.json`) and works the rest out
  from the state file's changes and Wallpaper Engine's own pause rules, the
  way the tray's estimate does. Estimated is marked `≈`; paused says so in
  warn; Wallpaper Engine not running is "— disconnected".
- The playlist's rows: `data/tracker.json` (the cycle: what was shown when,
  what is waiting), read off the GUI thread; titles, types and authors from
  each wallpaper's project.json, Review's Steam cache and the authors
  database (`engines/wallpaper_meta`), read off the GUI thread too, and kept.

Each row ends with three glyph buttons. **Send to Copier** puts the row's
folder on the Copier's list (`copier_requested`, wired to the Copier in
`pages.build_pages`). **Mark [protected]** renames a folder that sits directly
in the Rotator's myprojects to `[protected] <name>`, after asking, on a thread
of its own (the library is on a hard disk); the Rotator then leaves it there.
Wallpaper Engine's playlist is not touched: its entry keeps the old name and
stops working until the next rotation rebuilds the playlist, and the row says
so. Workshop folders are not offered it; a protected one shows a lock.
**Delete** sends the folder to the Recycle Bin after asking, a Workshop item
unsubscribed first (`engines/wallpaper_delete`, `pages/folder_actions`), and
the row leaves the table.

The `#` column is the playlist's own order: its title, clicked, undoes any
sort. Each row's SIZE is its folder's, measured on the reader's thread after
the titles and kept until "Read again".

The words come from plain functions (`monitor_view`, `pace_figure`,
`finish_sentence`, `provenance`, `playlist_rows`, `protect_state`, …), which
tests call without building a widget. Nothing is invented (plan §2.6): `~`
marks a reconstructed time, `≈` an estimate, "last known" what Wallpaper
Engine is no longer there to confirm, and a number that is not known is "—",
never 0.

Nothing here touches the disk on the window's thread. Marking a folder
[protected] renames it on a worker. A new cycle and rebuilding the counts from
file times (a stat of every wallpaper) are asked of the TrackerFeed, whose
worker owns the tracker and takes every look (app/tracker_feed.py); the page
hears back when the count they changed has landed.
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QMenu, QStackedLayout, QVBoxLayout, QWidget,
)

from .. import external, theme
from ..engines.rotator.core import PROTECTED_PREFIX, is_protected
from ..engines.tracker import (
    ANCHOR_ENGINE, ANCHOR_FILE_TIMES, ANCHOR_NONE, ANCHOR_ROTATION, MIN_SAMPLE, TIME_FMT, Cycle,
    Progress, TrackerState, pick_primary, queue_is_known, upcoming,
)
from ..engines.rotator.core import folder_size
from ..engines.wallpaper_delete import delete_wallpaper
from ..engines.wallpaper_meta import Described, MetaCache, fallback_title, folder_of
from ..services.snapshot import LAST_RUN, PLAYLIST
from ..settings import DEFAULT_COPIER_COUNT
from ..tracker_feed import tray_running
from ..ui.kit import (
    AccentButton, ButtonsCell, Callout, Cell, CellButton, Column, ConfirmDialog, EmptyState, FormDialog, GhostButton, Glyph, GlassPanel, Group, IconButton, LinkButton,
    MonitorCard, MonitorView, NavState, Overline, PathField, Rule, SecondaryButton,
    SegmentedControl, Table, TableBar, TableFooter, TableModel, TextInput, format as fmt, label,
)
from ..ui.kit.base import set_tone
from ..ui.kit.cards import qualified_html
from .base import Page, SideScroll
from .folder_actions import (
    DELETE, SEND, SEND_TIP, delete_dialog, delete_tip, deleted_words,
)

_MINUTE_MS = 60_000
REVEAL_AGAIN = 1.0             # seconds before the same row opens Explorer again
_TICK_MS = 1_000                # the countdown
_LIST_SETTLE_MS = 150           # the feed's looks come in twos at times: read the list once
META_CHUNK = 120                # project.json files read between two updates of the table
SIZE_CHUNK = 8                  # folders measured between two updates of the table
FOOTER_NOTE = "Wallpaper Engine decides the order — this is a read of its playlist"
SHOWN, QUEUE = "shown", "queue"


def _time(stamp) -> datetime | None:
    """A tracker timestamp ("2026-09-19 12:44:00") as a datetime, or None."""
    if isinstance(stamp, datetime):
        return stamp
    if not stamp:
        return None
    try:
        return datetime.strptime(str(stamp), TIME_FMT)
    except ValueError:
        try:
            return datetime.fromisoformat(str(stamp))
        except ValueError:
            return None


def from_rotation(p) -> bool:
    """Whether a rotation built this monitor's playlist: the one whose end is
    the cue to rotate again."""
    return p.anchor == ANCHOR_ROTATION or bool(getattr(p, "from_rotation", False))


# ---- the header -------------------------------------------------------------------------------

def header_subtitle(monitors: int, background: bool) -> str:
    """"2 monitors · counting in the background" — only while the tray is
    running; the window alone counts only while it is open."""
    if monitors <= 0:
        return "no playlist counted yet"
    how = "counting in the background" if background else "counting while this window is open"
    return f"{fmt.counted(monitors, 'monitor')} · {how}"


def nav_state(lead) -> NavState:
    """The sidebar: "4/201" and a mini bar, green once the playlist is done."""
    if lead is None or lead.total <= 0:
        return NavState()
    return NavState.count(lead.seen, lead.total)


# ---- a monitor ----------------------------------------------------------------------------------

def engine_off(p, engine: bool | None) -> bool:
    """Wallpaper Engine is not running: as the countdown found it, or, before
    it has looked, as the count says (followed from the engine's record, and
    not live)."""
    if engine is not None:
        return not engine
    return bool(getattr(p, "from_engine", False)) and not p.live


def last_known_note(p, engine: bool | None) -> str:
    if p.live:
        return ""
    if engine_off(p, engine):
        return "last known · Wallpaper Engine is not running"
    return "last known · nothing from the playlist on screen"


def _as_shown(seconds: float | None) -> float | None:
    """A time as precisely as the card writes it — whole minutes from a
    minute up, whole seconds below — so a view changes only when its words do."""
    if seconds is None:
        return None
    return float(round(seconds / 60) * 60) if seconds >= 60 else float(round(seconds))


def monitor_view(p, *, leading: bool, countdown=None, engine: bool | None = None,
                 described: Described | None = None, resolution: str = "",
                 now: datetime) -> MonitorView:
    """One monitor's card, from its Progress, its countdown and what is known
    of the wallpaper on screen.

    REMAINING: the countdown ("≈" when it is estimated), "paused" in warn, or
    "— disconnected" when Wallpaper Engine is not running. Not live, the card
    keeps the last known wallpaper and count, and says so."""
    since = _time(p.current_since) if p.live else None
    shown = _as_shown((now - since).total_seconds()) if since is not None and now >= since \
        else None
    remaining, approx, timer = None, False, ""
    if engine_off(p, engine):
        timer = "stopped"
    elif countdown is not None and getattr(countdown, "active", False):
        remaining = _as_shown(countdown.remaining)
        approx = bool(countdown.approximate)
        if countdown.paused:
            timer = "paused"
    note = last_known_note(p, engine)
    if not note and not leading and not from_rotation(p):
        note = "follows its own order · not counted for rotation"
    title = (described.meta.title if described is not None and described.meta.title
             else p.current_title or "")
    author = described.author if described is not None else ""
    chip = "Known" if described is not None and described.known and author else None
    folder = str(folder_of(p.current)) if p.current else None
    return MonitorView(
        p.monitor, "leading" if leading else "summary", resolution=resolution, title=title,
        author=author, author_chip=chip, position=p.seen, total=p.total, shown_for=shown,
        remaining=remaining, remaining_approx=approx, timer=timer,
        cycle_started=_time(p.started), reconstructed=p.anchor in (ANCHOR_ENGINE, ANCHOR_FILE_TIMES),
        preview=folder, note=note)


# ---- the pace ------------------------------------------------------------------------------------

def pace_seconds(p, now: datetime) -> float | None:
    """The cycle's real time per wallpaper shown, so far."""
    started = _time(p.started)
    if started is None or p.seen <= 0:
        return None
    elapsed = (now - started).total_seconds()
    return elapsed / p.seen if elapsed > 0 else None


def finish_at(p, now: datetime) -> datetime | None:
    """When the playlist should be done at that pace; None until MIN_SAMPLE
    are shown, or once none are left (the engine's rule)."""
    remaining = max(p.total - p.seen, 0)
    if p.seen < MIN_SAMPLE or remaining <= 0:
        return None
    pace = pace_seconds(p, now)
    return now + timedelta(seconds=pace * remaining) if pace is not None else None


def pace_figure(p, now: datetime) -> tuple[str, str]:
    """("42 min", "average on screen"): real time per wallpaper, nights and
    pauses included — `≈` when the cycle's start was worked out afterwards."""
    pace = pace_seconds(p, now)
    if pace is None:
        return fmt.DASH, "nothing shown yet this cycle"
    text = fmt.duration(pace, exact=False)
    if p.anchor in (ANCHOR_ENGINE, ANCHOR_FILE_TIMES):
        text = fmt.approx(text)
    return text, "average on screen"


def _rotate(next_run: int | None) -> str:
    return f"time to rotate (run {next_run})" if next_run else "time to rotate"


def finish_sentence(p, now: datetime, next_run: int | None = None) -> tuple[str, str]:
    """(before, the date) … the rest: "At this pace the playlist empties
    ≈21 Sep, about 09:10 — time to rotate (run 39)." As (plain text, rich
    text), the date standing out. The rotation is started by hand (§7.5)."""
    if p.total > 0 and p.seen >= p.total:
        started = _time(p.started)
        took = (f" in {fmt.duration((now - started).total_seconds(), exact=False)}"
                if started is not None and now > started else "")
        text = f"Shown to the end: all {fmt.count(p.total)}{took}."
        return text, _html(text)
    when = finish_at(p, now)
    if when is None:
        if p.seen < MIN_SAMPLE:
            text = (f"The finish is estimated once {MIN_SAMPLE} have been shown — "
                    f"{MIN_SAMPLE - p.seen} to go.")
            return text, _html(text)
        text = "No finish can be worked out from this cycle yet."
        return text, _html(text)
    date = fmt.approx(f"{fmt.day(when, now)}, about {fmt.clock(when)}")
    before, after = "At this pace the playlist empties ", f" — {_rotate(next_run)}."
    return (before + date + after,
            _html(before) + qualified_html(date, "text.hi") + _html(after))


def _html(text: str) -> str:
    import html
    return html.escape(text)


def _when(stamp, now: datetime) -> str:
    moment = _time(stamp)
    return fmt.date_activity(moment, now) if moment is not None else fmt.DASH


def provenance(p, now: datetime) -> list[tuple[str, str]]:
    """What the count rests on, as (tone, sentence) — never hidden (§6.3.3).
    Warn: the count was thrown away, or wallpapers were deleted; neutral: how
    the cycle is counted and dated, and which times are reconstructed (~)."""
    warn: list[str] = []
    neutral: list[str] = []
    if p.restarted_from and getattr(p, "previous_finished", False):
        total = p.restarted_from.split("/")[-1]
        neutral.append(f"Wallpaper Engine began the next pass at {_when(p.restarted_at, now)}, "
                       f"after all {total} of the last one were shown.")
    elif p.restarted_from:
        warn.append(f"Wallpaper Engine started the playlist over at "
                    f"{_when(p.restarted_at, now)} — the previous count had reached "
                    f"{p.restarted_from.replace('/', ' / ')}.")
    if p.gone:
        warn.append(f"{fmt.counted(p.gone, 'wallpaper')} deleted since the playlist was built, "
                    f"not counted.")
    passed = getattr(p, "passed", 0)
    if passed:
        warn.append(f"{fmt.counted(passed, 'wallpaper')} dealt while Wallpaper Engine was "
                    f"closed and passed over as it started — never on screen, so not "
                    f"counted, and out of this pass.")
    if getattr(p, "from_engine", False):
        neutral.append("Read from Wallpaper Engine's own record of the pass.")
        if p.inferred:
            neutral.append(f"{fmt.count(p.inferred)} marked ~ came up unwatched; their times "
                           f"are from the files.")
    else:
        neutral.append("Counted by watching which file Wallpaper Engine holds open.")
        if p.inferred:
            neutral.append(f"{fmt.count(p.inferred)} marked ~ were restored from file access "
                           f"times, not watched.")
    neutral.append({
        ANCHOR_ROTATION: "Cycle dated by the rotation that built the playlist.",
        ANCHOR_ENGINE: "Cycle dated from Wallpaper Engine's record of the new pass (~).",
        ANCHOR_FILE_TIMES: "Cycle dated from the break in the file times (~).",
        ANCHOR_NONE: "Cycle counted from when tracking started.",
    }.get(p.anchor, f"Cycle dated from {p.anchor}."))
    out = [("warn", " ".join(warn))] if warn else []
    return out + [("neutral", " ".join(neutral))]


# ---- the playlist ------------------------------------------------------------------------------

@dataclass(eq=False)
class PlaylistRow:
    """One wallpaper of the playlist, as the table shows it. Its title,
    author, type and Known start from the path and fill in as they are read."""
    item: str
    group: str                          # SHOWN or QUEUE
    number: int                         # its place in the playlist, from 1
    queue: int | None = None            # its place in the queue, from 1, where there is one
    when: datetime | None = None        # when it came up
    inferred: bool = False              # that time is reconstructed (~)
    shown_for: float | None = None      # how long it was up, in seconds
    shown_approx: bool = False          # worked out from a reconstructed time
    on_screen: bool = False
    title: str = ""
    author: str = ""
    kind: str = ""
    known: bool = False
    folder: str = field(default="", repr=False)
    size: int | None = None             # the folder's, in bytes, once measured
    # Marked [protected] here: `folder` is its new name, and Wallpaper Engine's
    # entry (`item`) still names the old one until the next rotation.
    stale: bool = False

    def describe(self, found: Described | None) -> bool:
        """Take what was read; True when it changed anything."""
        if found is None:
            return False
        new = (found.meta.title or self.title, found.author, found.meta.kind, found.known)
        if new == (self.title, self.author, self.kind, self.known):
            return False
        self.title, self.author, self.kind, self.known = new
        return True


def playlist_rows(cycle: Cycle | None, now: datetime, *, live: bool = True,
                  described: Callable[[str], Described | None] | None = None,
                  ) -> tuple[list[PlaylistRow], bool]:
    """The cycle's wallpapers as table rows, and whether the queue is in
    playing order.

    Shown, newest first: the one on screen at the top (when Wallpaper Engine
    is showing it), each with when it came up and how long it stayed — until
    the next one came up, real time — `~` where either time is reconstructed.
    Then the queue: in playing order for a sorted playlist, numbered from 1;
    in the playlist's own order for a random one, which has no queue — any of
    them can be next. Deleted wallpapers are neither: they are not there.
    """
    if cycle is None:
        return [], False
    gone = set(cycle.missing)
    number = {item: n for n, item in enumerate(cycle.items, 1)}
    inferred = set(cycle.inferred)
    seen = {item: _time(when) for item, when in cycle.seen.items()
            if item in number and item not in gone}
    on_screen = cycle.current if live and cycle.current in seen else None

    rows: list[PlaylistRow] = []
    order = sorted(seen, key=lambda i: seen[i] or datetime.min)
    until: dict[str, tuple[datetime | None, bool]] = {}
    for k, item in enumerate(order):
        later = order[k + 1] if k + 1 < len(order) else None
        until[item] = (seen[later], later in inferred) if later is not None else (None, False)
    for item in sorted(seen, key=lambda i: (i == on_screen, seen[i] or datetime.min),
                       reverse=True):
        start = seen[item]
        if item == on_screen:
            start = _time(cycle.current_since) or start
            end, end_inferred = now, False
        else:
            end, end_inferred = until[item]
        took = (end - start).total_seconds() if start and end and end >= start else None
        rows.append(PlaylistRow(
            item, SHOWN, number[item], when=seen[item], inferred=item in inferred,
            shown_for=took, shown_approx=took is not None and (item in inferred or end_inferred),
            on_screen=item == on_screen))

    # Passed over as Wallpaper Engine started: not shown, and not to come
    # in this pass either.
    passed = cycle.passed_over
    waiting = {i for i in cycle.items if i not in seen and i not in gone and i not in passed}
    after = cycle.current
    if after not in number and seen:
        after = max(seen, key=lambda i: seen[i] or datetime.min)
    in_order = queue_is_known(cycle.order)
    for place, item in enumerate(upcoming(cycle.items, waiting, cycle.order, after), 1):
        rows.append(PlaylistRow(item, QUEUE, number[item], queue=place if in_order else None))
    for row in rows:
        row.folder = str(folder_of(row.item))
        row.title = fallback_title(row.item)
        if described is not None:
            row.describe(described(row.item))
    return rows, in_order


def queue_words(in_order: bool) -> str:
    """What the queue's header adds, in today's words."""
    return "up next, in playing order" if in_order else "random order, any of these can be next"


def when_text(moment: datetime | None, now: datetime) -> str:
    """When a wallpaper came up, short enough for its column: 12:02 today,
    Fri 09:10 this week, 19 Sep before."""
    if moment is None:
        return fmt.DASH
    days = (now.date() - moment.date()).days
    return fmt.date_activity(moment, now) if days <= 6 else fmt.day(moment, now)


def row_matches(row: PlaylistRow, text: str = "") -> bool:
    """The filter: the words in the title."""
    words = text.strip().casefold()
    return not words or words in row.title.casefold()


# ---- a row's actions: Send to Copier, Mark [protected], Delete ----------------------------------

PROTECT, MARKED = "protect", "protected"     # the buttons' keys, with SEND and DELETE
OFFER = "offer"                         # protect_state: Mark [protected] is offered
# under the title of a row marked [protected] here; the consequence first, for a narrow table
STALE_NOTE = "playlist entry broken until the next rotation · renamed [protected]"


def folder_key(folder: str) -> str:
    """A folder as two paths to it compare: separators and, on Windows, case."""
    return os.path.normcase(os.path.normpath(folder)) if folder else ""


def protect_state(folder: str, destination: str) -> str:
    """What Mark [protected] can do for a row's folder: MARKED when its name
    already says [protected] (in any case, as the Rotator reads it); OFFER
    when it sits directly in the Rotator's myprojects (`destination`); ""
    otherwise — a Workshop folder, or anything elsewhere. Paths only: nothing
    is read."""
    if not folder:
        return ""
    if is_protected(os.path.basename(os.path.normpath(folder))):
        return MARKED
    if destination and folder_key(os.path.dirname(os.path.normpath(folder))) \
            == folder_key(destination):
        return OFFER
    return ""


def protected_name(folder: str) -> str:
    """The name a folder is marked [protected] under: `[protected] <name>`."""
    return f"{PROTECTED_PREFIX} {os.path.basename(os.path.normpath(folder))}"


def action_cell(row: PlaylistRow, state: str, renaming: bool = False,
                deleting: bool = False) -> ButtonsCell:
    """The row's last cell: Send to Copier, then Mark [protected] where it is
    offered (off while the rename runs), a lock where the folder is marked
    already (in warn, with what it means, when it was marked here), or an
    empty slot; then Delete (off while it runs; "Unsubscribe and delete" for
    a Workshop item)."""
    second = None
    if state == MARKED:
        second = (CellButton(MARKED, "lock", "Marked [protected] here. Wallpaper Engine's "
                             "playlist still lists it under its old name: that entry stops "
                             "working until the next rotation.", mark=True, tone="warn")
                  if row.stale else
                  CellButton(MARKED, "lock", "[protected]: the Rotator leaves it in myprojects",
                             mark=True, tone="accent.hover"))
    elif state == OFFER:
        second = (CellButton(PROTECT, "lock", "Renaming it [protected]…", enabled=False)
                  if renaming else CellButton(PROTECT, "lock", "Mark [protected]…"))
    delete = (CellButton(DELETE, "trash", "Deleting it…", enabled=False) if deleting
              else CellButton(DELETE, "trash", delete_tip(row.folder)))
    return ButtonsCell((CellButton(SEND, "copier", SEND_TIP), second, delete))


class ProtectError(Exception):
    """Why a folder could not be marked [protected], in words for a toast."""


def protect_folder(folder: str) -> str:
    """Rename a wallpaper folder to `[protected] <name>` beside it, and return
    its new path. Raises ProtectError saying why not; nothing is changed then.
    A folder already renamed (here, before the window was last closed) is
    found under its new name. Touches the disk: a worker's job."""
    source = Path(folder)
    target = source.with_name(protected_name(folder))
    if is_protected(source.name):
        raise ProtectError("it is marked [protected] already.")
    if not source.is_dir():
        if target.is_dir():
            return str(target)
        raise ProtectError("it is not in myprojects any more.")
    if target.exists():
        raise ProtectError(f"there is already a folder called {target.name} in myprojects.")
    try:
        source.rename(target)
    except FileExistsError:
        raise ProtectError(f"there is already a folder called {target.name} in myprojects.") \
            from None
    except PermissionError as err:
        if getattr(err, "winerror", None) == 32:         # ERROR_SHARING_VIOLATION
            raise ProtectError("it is in use. Wallpaper Engine may be showing it; try again "
                               "once it has moved on.") from None
        raise ProtectError("Windows denied access to it. It may be in use (Wallpaper Engine "
                           "may be showing it), or it is read-only.") from None
    except OSError as err:
        raise ProtectError(f"{err.strerror or err}.") from None
    return str(target)


def protect_dialog(old: str, new: str, parent: QWidget | None) -> ConfirmDialog:
    """Asking before Mark [protected]: the rename, what it is for, and what it
    does to Wallpaper Engine's playlist."""
    return ConfirmDialog(
        "Mark this folder [protected]?",
        "The Rotator leaves [protected] folders in myprojects: no run takes this one back "
        "to the reserve.",
        parent, icon="lock", lines=[f"{old}  →  {new}"],
        note=("warn", "Wallpaper Engine's playlist is not touched. Its entry for this wallpaper "
                      "keeps the old name and stops working until the next rotation rebuilds "
                      "the playlist."),
        confirm_text="Rename")


# `#` is the playlist's own order: a click on it undoes any sort (`natural`).
COLUMNS = (
    Column("#", theme.TRACKER_COLUMNS["number"], "right", mono=True, tone="text.lo",
           natural=True),
    Column("Wallpaper", None, thumb="row"),
    Column("Type", theme.TRACKER_COLUMNS["type"], font="type.monoXs", tone="text.lo"),
    Column("Size", theme.TRACKER_COLUMNS["size"], "right", mono=True, tone="text.mid"),
    Column("Shown", theme.TRACKER_COLUMNS["shown"], "right", mono=True, tone="text.mid"),
    Column("State", theme.TRACKER_COLUMNS["state"], "right", mono=True, tone="text.lo",
           sortable=False),
    Column("", theme.TRACKER_COLUMNS["actions"], "right", sortable=False),
)
TYPE, SIZE, SHOWN_FOR = 2, 3, 4
ACTIONS = len(COLUMNS) - 1
GROUPS = (Group(SHOWN, "Already shown this cycle"), Group(QUEUE, "Queue"))


class PlaylistModel(TableModel):
    """The playlist's rows under their two groups."""

    def __init__(self, parent=None):
        super().__init__(COLUMNS, (), parent, groups=GROUPS, group_of=lambda r: r.group)
        self.now = datetime.now()
        self.in_order = False
        self._digits = 3
        self.destination = ""               # the Rotator's myprojects
        self.renaming: set[str] = set()     # folder_key()s being marked [protected]
        self.deleting: set[str] = set()     # folder_key()s on their way to the Recycle Bin

    def set_playlist(self, rows: list[PlaylistRow], in_order: bool, now: datetime) -> None:
        self.now, self.in_order = now, in_order
        self._digits = max(3, len(str(len(rows))))
        self.set_rows(rows)

    def cell(self, row: PlaylistRow, column: int):
        if column == 0:
            if row.group == SHOWN:
                return Cell(fmt.DASH, "accent.hover" if row.on_screen else None)
            n = row.queue if row.queue is not None else row.number
            return f"{n:0{self._digits}d}"
        if column == 1:
            return Cell(row.title, "text.hi" if row.on_screen
                        else "text.mid" if row.group == SHOWN else None,
                        sub=STALE_NOTE if row.stale else "", sub_tone="warn")
        if column == ACTIONS:
            key = folder_key(row.folder)
            return action_cell(row, self.protect_state(row), key in self.renaming,
                               key in self.deleting)
        if column == TYPE:
            return row.kind
        if column == SIZE:
            return fmt.size(row.size) if row.size is not None else ""
        if column == SHOWN_FOR:
            if row.shown_for is None:
                return fmt.DASH
            text = fmt.duration(row.shown_for, exact=False)
            return Cell(fmt.reconstructed(text) if row.shown_approx else text,
                        "text.body" if row.on_screen else None, strong=row.on_screen)
        if row.on_screen:
            return Cell("on screen", "accent.hover", strong=True)
        if row.group == SHOWN:
            text = when_text(row.when, self.now)
            return fmt.reconstructed(text) if row.inferred and row.when else text
        return "queued"

    def sort_key(self, row: PlaylistRow, column: int):
        if column == 0:
            return row.queue if row.queue is not None else row.number
        if column == SIZE:
            return row.size
        if column == SHOWN_FOR:
            return row.shown_for
        return super().sort_key(row, column)

    def protect_state(self, row: PlaylistRow) -> str:
        return protect_state(row.folder, self.destination)

    def thumb_source(self, row: PlaylistRow) -> str | None:
        return row.folder or None

    def row_dimmed(self, row: PlaylistRow) -> bool:
        return row.group == SHOWN and not row.on_screen

    def group_note(self, row: int) -> str:
        note = super().group_note(row)
        group = self.group_at(row)
        if group is not None and group.key == QUEUE:
            return f"{note} · {queue_words(self.in_order)}"
        return note


# ---- reading off the GUI thread -----------------------------------------------------------------

def load_cycle(monitor: str) -> Cycle | None:
    return TrackerState.load().cycles.get(monitor)


def measure_folder(folder: str) -> int:
    """A wallpaper folder's size in bytes. OSError when it is not there: a
    playlist outlives its folders, and a gone one has no size, not 0 B."""
    if not os.path.isdir(folder):
        raise OSError(f"not a folder: {folder}")
    return folder_size(folder)


class ListReader(QObject):
    """Reads what the table and the cards need, one job at a time on a thread
    of its own: a monitor's list out of tracker.json, then its titles, types
    and authors, a chunk at a time, then each folder's size; or just the
    wallpapers the cards show. A newer list job makes an older one stop where
    it is. Sizes are kept, by folder, until `forget`."""

    rows_read = Signal(int, object)         # generation, (monitor, rows, in order)
    described = Signal(object)              # {item: Described}
    sized = Signal(object)                  # {folder_key: bytes}

    def __init__(self, meta: MetaCache, load: Callable[[str], Cycle | None] = load_cycle,
                 parent: QObject | None = None, *,
                 measure: Callable[[str], int] = measure_folder):
        super().__init__(parent)
        self.meta = meta
        self._load = load
        self._jobs: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._generation = 0
        self._thread: threading.Thread | None = None
        self._measure = measure
        self.sizes: dict[str, int] = {}     # folder_key → bytes; this thread writes it

    def read_list(self, monitor: str, now: datetime, live: bool) -> int:
        with self._lock:
            self._generation += 1
            generation = self._generation
        self._put(("list", generation, monitor, now, live))
        return generation

    def read_items(self, items) -> None:
        items = [i for i in items if i]
        if items:
            self._put(("items", items))

    def forget(self) -> None:
        """Read everything again from the next job on."""
        self._put(("forget",))

    def idle(self) -> bool:
        with self._lock:
            return self._jobs.empty() and (self._thread is None or not self._thread.is_alive())

    def _put(self, job) -> None:
        self._jobs.put(job)
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, daemon=True,
                                                name="tracker-reader")
                self._thread.start()

    def _run(self) -> None:
        while True:
            try:
                job = self._jobs.get(timeout=2)
            except queue.Empty:
                with self._lock:
                    if self._jobs.empty():
                        self._thread = None
                        return
                continue
            try:
                self._do(job)
            except Exception:           # noqa: BLE001 — the table stays as it was
                pass

    def _emit(self, signal, *args) -> None:
        try:
            signal.emit(*args)
        except RuntimeError:
            pass                        # the page went while this read

    def _stale(self, generation: int) -> bool:
        return generation != self._generation

    def _do(self, job) -> None:
        if job[0] == "forget":
            self.meta.forget()
            self.sizes = {}
            return
        if job[0] == "items":
            found = self.meta.describe(job[1])
            self._emit(self.described, found)
            return
        _, generation, monitor, now, live = job
        if self._stale(generation):
            return
        cycle = self._load(monitor)
        rows, in_order = playlist_rows(cycle, now, live=live, described=self.meta.cached)
        if self._stale(generation):
            return
        for row in rows:
            row.size = self.sizes.get(folder_key(row.folder))
        self._emit(self.rows_read, generation, (monitor, rows, in_order))
        items = [row.item for row in rows]
        for start in range(0, len(items), META_CHUNK):
            if self._stale(generation):
                return
            chunk = items[start:start + META_CHUNK]
            self.meta.read(chunk)
            self._emit(self.described, {i: d for i in chunk
                                        if (d := self.meta.cached(i)) is not None})
        if self._stale(generation):
            return
        self.meta.resolve_authors(items)
        self._emit(self.described, {i: d for i in items if (d := self.meta.cached(i)) is not None})
        # Then the sizes: a walk of each folder, the slowest read, so last.
        folders = [folder_key(row.folder) for row in rows if row.folder]
        waiting = [f for f in dict.fromkeys(folders) if f not in self.sizes]
        for start in range(0, len(waiting), SIZE_CHUNK):
            if self._stale(generation):
                return
            found = {}
            for folder in waiting[start:start + SIZE_CHUNK]:
                try:
                    found[folder] = int(self._measure(folder))
                except OSError:
                    continue
            self.sizes.update(found)
            if found:
                self._emit(self.sized, found)


def reveal(item: str) -> str | None:
    """Open Explorer on a playlist entry's file, or on the nearest folder of
    its path that is still there — a playlist outlives its files: a later
    rotation carries folders back to the reserve and Wallpaper Engine goes on
    listing them. Returns what went wrong, or None. Reads the disk: call it
    off the GUI thread."""
    target = Path(str(item).replace("/", "\\"))
    try:
        if target.exists():
            external.popen(f'explorer /select,"{target}"')
            return None
        folder = next((p for p in target.parents if p.is_dir()), None)
        if folder is not None:
            external.popen(f'explorer "{folder}"')
            return None
        return f"Nothing of this path is left on disk: {target}"
    except OSError as err:
        return f"Could not open Explorer: {err}"


class _Revealed(QObject):
    failed = Signal(str)


class _Offload(QObject):
    """Runs a function on a thread of its own and hands its result (or the
    exception it raised) to a callback on the window's thread — as the
    Rotator page's does."""

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
                         name="tracker page").start()
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

    def busy(self) -> bool:
        return bool(self._callbacks)


# ---- the countdown -------------------------------------------------------------------------------

def page_timer(feed):
    """The page's own WallpaperTimer: a reader that starts from the tray's
    count and never writes (see the module's docstring)."""
    from ..engines import wallpaper_timer
    config = getattr(feed, "config_path", "") or ""
    files = getattr(feed, "files", None)
    if not config or files is None:
        return None
    return wallpaper_timer.WallpaperTimer(
        config, files=files, open_memory=None, save_path=None,
        restore_path=wallpaper_timer.SAVE_PATH, follow_files=False)


class Countdowns(QObject):
    """The time left on each monitor's wallpaper, ticking once a second
    while the page is on screen. Made afresh each time the page comes on
    screen, so it starts from the tray's latest count again rather than
    from where it stopped counting."""

    ticked = Signal()

    def __init__(self, feed, parent: QObject | None = None, *,
                 make_timer: Callable = page_timer):
        super().__init__(parent)
        self._feed = feed
        self._make = make_timer
        self._timer = None
        self.values: dict = {}
        self.engine: bool | None = None         # Wallpaper Engine running, as last found
        self.failed = False
        self._qt = QTimer(self)
        self._qt.setInterval(_TICK_MS)
        self._qt.timeout.connect(self.tick)

    def running(self) -> bool:
        return self._qt.isActive()

    def start(self) -> None:
        if self.failed:
            return
        try:
            self._timer = self._make(self._feed)
        except Exception:               # noqa: BLE001 — the count must go on without it
            self._timer, self.failed = None, True
        self.tick()
        if self._timer is not None:
            self._qt.start()

    def stop(self) -> None:
        self._qt.stop()

    def tick(self) -> None:
        if self._timer is None:
            return
        try:
            self.values = dict(self._timer.tick())
            self.engine = getattr(self._timer, "engine", None) is not None
        except Exception:               # noqa: BLE001 — it reads other processes' windows
            self._timer, self.failed = None, True
            self.values, self.engine = {}, None
            self._qt.stop()
        self.ticked.emit()

    def get(self, monitor: str):
        return self.values.get(monitor)

    def resolution(self, monitor: str) -> str:
        """"2560×1440", when Windows says which display is MonitorN."""
        from ..engines.wallpaper_timer import resolution_of
        rects = getattr(self._timer, "rects", None) or {}
        return resolution_of(rects.get(monitor))


# ---- the page ---------------------------------------------------------------------------------------

class _Pace(GlassPanel):
    """PACE: the average time on screen, the finish sentence, and the notes
    the count rests on — the finished one first, with its way on."""

    open_rotator = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, padding="none")
        vertical, horizontal = theme.PACE_PAD
        self.setContentsMargins(horizontal, vertical, horizontal, vertical)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(theme.PACE_GAP)
        column.addWidget(Overline("Pace"))
        figure = QHBoxLayout()
        figure.setSpacing(theme.PACE_FIGURE_GAP)
        self.value = label(fmt.DASH, "type.metricLg", "hi")
        self.value.setTextFormat(Qt.RichText)
        self.caption = label("", "type.label", "mid")
        figure.addWidget(self.value, 0, Qt.AlignBaseline)
        figure.addWidget(self.caption, 0, Qt.AlignBaseline)
        figure.addStretch(1)
        column.addLayout(figure)
        column.addWidget(Rule())
        line = QHBoxLayout()
        line.setSpacing(theme.SP_8)
        line.addWidget(Glyph("clock", "text.mid", theme.PACE_ICON), 0, Qt.AlignTop)
        self.sentence = label("", "type.bodySm", "mid")
        self.sentence.setTextFormat(Qt.RichText)
        self.sentence.setWordWrap(True)
        line.addWidget(self.sentence, 1)
        column.addLayout(line)
        column.addStretch(1)
        self.notes = QVBoxLayout()
        self.notes.setSpacing(theme.SP_8)
        column.addLayout(self.notes)
        self._said: tuple = ()
        self.callouts: list[Callout] = []

    def set_pace(self, value: str, caption: str, sentence: tuple[str, str],
                 notes: list[tuple[str, str]], finished: str | None) -> None:
        self.value.setText(qualified_html(value, "text.hi"))
        self.caption.setText(caption)
        self.sentence.setText(sentence[1])
        self.sentence.setAccessibleName(sentence[0])
        said = (tuple(notes), finished)
        if said == self._said:
            return
        self._said = said
        for callout in self.callouts:
            self.notes.removeWidget(callout)
            callout.deleteLater()
        self.callouts = []
        if finished:
            done = Callout(finished[1], tone="ok", title=finished[0], icon="check")
            go = GhostButton("Open Rotator", size="sm")
            go.clicked.connect(self.open_rotator.emit)
            done.add_action(go)
            self.callouts.append(done)
        for tone, text in notes:
            self.callouts.append(Callout(text, tone=tone))
        for callout in self.callouts:
            self.notes.addWidget(callout)

    def texts(self) -> dict:
        return {"value": _plain(self.value.text()),
                "caption": self.caption.text(), "sentence": self.sentence.accessibleName(),
                "notes": [(c.tone(), c._title.text(), c._body.text()) for c in self.callouts]}


def _plain(rich: str) -> str:
    from PySide6.QtGui import QTextDocumentFragment
    return QTextDocumentFragment.fromHtml(rich).toPlainText()


class TrackerPage(Page):
    key = "tracker"
    title = "Tracker"
    icon = "tracker"
    FIXTURES = ("tracking", "paused", "disconnected", "finished", "restarted", "we-off",
                "single-monitor", "no-config", "no-playlist", "marked")

    # Send to Copier: these folders, for the Copier's list. `build_pages` hands
    # them to the Copier and its answer back to `copier_took`.
    copier_requested = Signal(list)

    def __init__(self, feed=None, services=None, parent: QWidget | None = None, *,
                 settings=None, config=None, now=None, make_timer: Callable = page_timer,
                 meta: MetaCache | None = None, load: Callable = load_cycle,
                 measure: Callable[[str], int] = measure_folder):
        super().__init__(parent)
        self._feed = feed
        self._services = services
        self._settings = settings
        self._config = config               # the Rotator's: where myprojects is
        self._now = now or datetime.now
        self._fixture: dict | None = None
        self._results: list = []
        self._error: str | None = None
        self._engine_fixed: bool | None = None        # a fixture's answer to "running?"
        self._background = False
        self._lead = None
        self._table_monitor: str | None = None      # None: the leading monitor's
        self._list_generation = 0
        self._list_monitor: str | None = None
        self._list_dirty = True
        self._described: dict[str, Described] = {}
        self._views: dict[str, MonitorView] = {}
        self._next_run: int | None = None
        self._on_screen = False
        self._revealed_last: tuple[str, float] = ("", 0.0)
        self._renamed: dict[str, str] = {}      # folder_key(old) → new folder, this session
        self._deleted: set[str] = set()         # folder_key()s sent to the Recycle Bin here
        self._copier_answer: int | None = None
        self._sending = ""                      # the title of the row sent to the Copier
        self.messages: list[tuple[str, str]] = []   # (tone, words) of each toast, for tests

        self.reader = ListReader(meta or MetaCache(), load, self, measure=measure)
        self.reader.rows_read.connect(self._rows_read)
        self.reader.described.connect(self._items_described)
        self.reader.sized.connect(self._items_sized)
        self.countdowns = Countdowns(feed, self, make_timer=make_timer)
        self.countdowns.ticked.connect(self._render_cards)
        self._revealed = _Revealed(self)
        self._revealed.failed.connect(self._reveal_failed)
        self._offload = _Offload(self)

        self._build()

        self._minute = QTimer(self)
        self._minute.setSingleShot(True)
        self._minute.timeout.connect(self._tick)
        self._list_timer = QTimer(self)
        self._list_timer.setSingleShot(True)
        self._list_timer.setInterval(_LIST_SETTLE_MS)
        self._list_timer.timeout.connect(self._read_list)

        if feed is not None:
            feed.updated.connect(self._feed_updated)
            feed.config_changed.connect(self._config_changed)
        if services is not None:
            services.snapshot.refreshed.connect(self._snapshot_refreshed)
        self._take_feed()
        self._render()

    # -- building

    def _build(self) -> None:
        self._stack = QStackedLayout(self)

        self.content = QWidget()
        body = QHBoxLayout(self.content)
        pad_v, pad_h = theme.BODY_PAD
        body.setContentsMargins(pad_h, pad_v, pad_h, pad_v)
        body.setSpacing(theme.PANEL_GAP)
        side = QWidget()
        self._side = QVBoxLayout(side)
        self._side.setContentsMargins(0, 0, 0, 0)
        self._side.setSpacing(theme.PANEL_GAP)
        # Too short for the monitors and every note (a window 720 high), the
        # column scrolls, and the table beside it keeps its whole height.
        self.side_scroll = SideScroll(side, theme.TRACKER_SIDE)
        self.lead_card = MonitorCard(detail=True)
        self._lead_menu = QMenu(self)
        self.lead_card.set_menu(self._lead_menu)
        self._side.addWidget(self.lead_card)
        self.summary_cards: list[MonitorCard] = []
        self._summary_menus: list[QMenu] = []
        self.pace = _Pace()
        self.pace.open_rotator.connect(lambda: self.navigate.emit("rotator"))
        self._side.addWidget(self.pace, 1)
        body.addWidget(self.side_scroll)
        body.addWidget(self._build_table(), 1)
        self._stack.addWidget(self.content)

        self.empty = EmptyState("", "", icon="tracker")
        self.empty_action = SecondaryButton("")
        self.empty_action.clicked.connect(self._empty_clicked)
        self.empty.add_action(self.empty_action)
        empty_holder = QWidget()
        column = QVBoxLayout(empty_holder)
        column.setContentsMargins(pad_h, pad_v, pad_h, pad_v)
        column.addWidget(self.empty)
        self._stack.addWidget(empty_holder)
        self._empty_holder = empty_holder

    def filter_field(self) -> QWidget | None:
        return self.filter

    def _build_table(self) -> GlassPanel:
        card = GlassPanel(padding="none")
        column = QVBoxLayout(card)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        bar = TableBar()
        self.filter = TextInput(placeholder="Filter by title…", search=True)
        self.filter.setFixedWidth(theme.TRACKER_FILTER)
        self.filter.setAccessibleName("Filter by title")
        self.filter.textChanged.connect(self._apply_filter)
        self.jump = SegmentedControl(("Shown", "Queue"))
        self.jump.setAccessibleName("Go to")
        self.jump.changed.connect(self._jump_to)
        self.reload = IconButton("refresh", "Read the titles and authors again")
        self.reload.clicked.connect(self.read_again)
        for widget in (self.filter, self.jump):
            bar.add(widget)
        bar.add_stretch()
        bar.add(self.reload)
        column.addWidget(bar)
        self.model = PlaylistModel(self)
        self.table = Table()
        self.table.setModel(self.model)
        self.table.setAccessibleName("The playlist")
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.clicked.connect(self._row_clicked)
        self.table.activated.connect(self._row_clicked)
        self.table.action_clicked.connect(self._action_clicked)
        self.table.verticalScrollBar().valueChanged.connect(self._follow_scroll)
        column.addWidget(self.table, 1)
        self.footer = TableFooter("")
        self.footer.set_note(FOOTER_NOTE)
        column.addWidget(self.footer)
        return card

    def make_header_actions(self) -> list[QWidget]:
        settings = GhostButton("Playlist settings", outlined=True)
        settings.clicked.connect(self.open_settings)
        refresh = AccentButton("Refresh now")
        refresh.clicked.connect(self.refresh)
        return [settings, refresh]

    # -- reading what the window holds

    def _preferred(self) -> str | None:
        if self._settings is None:
            return None
        return self._settings.get("tracker", "primary", None)

    def _take_feed(self) -> None:
        if self._fixture is not None or self._feed is None:
            return
        self._results = list(getattr(self._feed, "results", None) or [])
        self._error = getattr(self._feed, "error", None)

    def _engine(self) -> bool | None:
        if self._fixture is not None:
            return self._engine_fixed
        return self.countdowns.engine

    def _feed_updated(self) -> None:
        self._take_feed()
        self._render()
        self._list_dirty = True
        if self._on_screen:
            self._list_timer.start()

    def _config_changed(self) -> None:
        self._results, self._described = [], {}
        self._table_monitor = self._list_monitor = None
        self.model.set_playlist([], False, self._now())
        if self.countdowns.running():
            self.countdowns.stop()
            self.countdowns.start()

    def _snapshot_refreshed(self, keys) -> None:
        if LAST_RUN in keys:
            self._render_pace()

    def _next(self) -> int | None:
        if self._fixture is not None:
            return self._next_run
        if self._services is None:
            return None
        last = self._services.snapshot[LAST_RUN].value
        return last.number + 1 if last is not None else None

    # -- on and off screen

    def on_shown(self) -> None:
        if self._fixture is not None:
            return
        self._on_screen = True
        if self._settings is not None:
            self._settings.reload_if_changed()
        self._follow_destination()
        self.countdowns.start()
        self._tick()
        if self._list_dirty:
            self._list_timer.start()
        self.reader.read_items([p.current for p in self._results])

    def on_hidden(self) -> None:
        self._on_screen = False
        self.countdowns.stop()
        self._minute.stop()

    def _tick(self) -> None:
        """Each minute while on screen: the header, and every "14 min"."""
        now = self._now()
        if self._fixture is None:
            self._background = tray_running()
            wait = _MINUTE_MS - (now.second * 1000 + now.microsecond // 1000)
            self._minute.start(max(1, wait))
        self._render()
        for row in self.model.items():
            if row.on_screen:
                start = _time(self._current_since(self._list_monitor)) or row.when
                if start is not None:
                    row.shown_for = max((now - start).total_seconds(), 0.0)
        self.model.now = now
        self.table.viewport().update()

    def _current_since(self, monitor: str | None):
        p = next((r for r in self._results if r.monitor == monitor), None)
        return p.current_since if p is not None else None

    # -- putting it on screen

    def _render(self) -> None:
        results = self._results
        lead = pick_primary(results, self._preferred())
        self._lead = lead
        self.set_nav_state(nav_state(lead))
        self.set_subtitle(header_subtitle(len(results), self._background))
        if lead is None:
            self._show_empty()
            return
        self._stack.setCurrentWidget(self.content)
        self._render_cards()
        self._render_pace()
        self._follow_monitors(results, lead)
        self._count_footer()

    def table_monitor(self) -> str | None:
        """Whose playlist the table shows: the leading monitor's, unless a
        card's menu asked for another's."""
        if self._lead is None:
            return None
        return self._table_monitor or self._lead.monitor

    def _count_footer(self) -> None:
        """"201 wallpapers": the playlist the table shows, deleted ones not
        counted — and whose, when it is not the leading monitor's."""
        monitor = self._list_monitor or self.table_monitor()
        p = next((r for r in self._results if r.monitor == monitor), None)
        if p is None:
            return
        text = fmt.counted(p.total, "wallpaper")
        if self._lead is not None and monitor != self._lead.monitor:
            text += f" · {monitor}"
        self.footer.set_text(text)

    def _others(self) -> list:
        return [p for p in self._results if p is not self._lead]

    def _render_cards(self) -> None:
        lead = self._lead
        if lead is None:
            return
        now = self._now()
        engine = self._engine()
        view = self._view(lead, True, engine, now)
        if self._views.get(lead.monitor) != view or self.lead_card.view() is None \
                or self.lead_card.view().name != view.name:
            self.lead_card.set_view(view, now)
            self._views[lead.monitor] = view
        self._fill_menu(self._lead_menu, lead.monitor, True)
        others = self._others()
        while len(self.summary_cards) > len(others):
            card = self.summary_cards.pop()
            self._summary_menus.pop()
            self._side.removeWidget(card)
            card.deleteLater()
        while len(self.summary_cards) < len(others):
            card = MonitorCard()
            menu = QMenu(self)
            card.set_menu(menu)
            self._side.insertWidget(1 + len(self.summary_cards), card)
            self.summary_cards.append(card)
            self._summary_menus.append(menu)
        for card, menu, p in zip(self.summary_cards, self._summary_menus, others):
            view = self._view(p, False, engine, now)
            if card.view() != view:
                card.set_view(view, now)
            self._fill_menu(menu, p.monitor, False)

    def _view(self, p, leading: bool, engine, now) -> MonitorView:
        if self._fixture is not None:
            countdown = self._fixture_countdowns.get(p.monitor)
            resolution = self._fixture_resolutions.get(p.monitor, "")
        else:
            countdown = self.countdowns.get(p.monitor)
            resolution = self.countdowns.resolution(p.monitor)
        return monitor_view(p, leading=leading, countdown=countdown, engine=engine,
                            described=self._described.get(p.current) if p.current else None,
                            resolution=resolution, now=now)

    def _render_pace(self) -> None:
        lead = self._lead
        if lead is None:
            return
        now = self._now()
        value, caption = pace_figure(lead, now)
        finished = None
        if lead.total > 0 and lead.seen >= lead.total:
            finished = (f"Whole playlist shown — {_rotate(self._next())}", "")
        self.pace.set_pace(value, caption, finish_sentence(lead, now, self._next()),
                           provenance(lead, now), finished)

    def _fill_menu(self, menu: QMenu, monitor: str, leading: bool) -> None:
        """A card's menu: its playlist in the table, the tray icon, a new cycle."""
        listed = monitor == self.table_monitor()
        key = (monitor, leading, listed, len(self._results))
        if menu.property("for") == repr(key):
            return
        menu.setProperty("for", repr(key))
        menu.clear()
        if len(self._results) > 1:
            table = menu.addAction("Show its playlist below")
            table.setCheckable(True)
            table.setChecked(listed)
            table.setEnabled(not listed)
            table.triggered.connect(lambda _c=False, m=monitor: self.show_list(m))
        show = menu.addAction("Show on the tray icon")
        show.setCheckable(True)
        show.setChecked(leading)
        show.setEnabled(not leading)
        show.triggered.connect(lambda _c=False, m=monitor: self.set_lead(m))
        menu.addSeparator()
        new = menu.addAction("New cycle…")
        new.triggered.connect(lambda _c=False, m=monitor: self.new_cycle(m))

    def menu_actions(self, monitor: str) -> list[tuple[str, bool, bool]]:
        """(text, checked, enabled) of a card's menu, for tests."""
        menus = [self._lead_menu, *self._summary_menus]
        cards = [self.lead_card, *self.summary_cards]
        for card, menu in zip(cards, menus):
            if card.view() is not None and card.view().name == monitor:
                return [(a.text(), a.isChecked(), a.isEnabled()) for a in menu.actions()
                        if not a.isSeparator()]
        return []

    def _follow_monitors(self, results, lead) -> None:
        names = [p.monitor for p in results]
        if self._table_monitor not in names:
            self._table_monitor = None
        shown = self.table_monitor()
        if shown != self._list_monitor:
            self._list_dirty = True
            if self._on_screen and self._fixture is None:
                self._list_timer.start()

    def _show_empty(self) -> None:
        title, body, action, target = empty_text(self._error, self._engine(),
                                                 getattr(self._feed, "config_path", ""))
        self.empty.set_title(title)
        self.empty.set_body(body)
        self.empty_action.setText(action)
        self.empty_action.setVisible(bool(action))
        self._empty_target = target
        self._stack.setCurrentWidget(self._empty_holder)

    def _empty_clicked(self) -> None:
        if getattr(self, "_empty_target", ""):
            self.navigate.emit(self._empty_target)

    # -- the table

    def _read_list(self) -> None:
        lead = self._lead
        if lead is None or self._fixture is not None:
            return
        monitor = self._table_monitor or lead.monitor
        p = next((r for r in self._results if r.monitor == monitor), lead)
        self._list_dirty = False
        self._list_generation = self.reader.read_list(monitor, self._now(), bool(p.live))

    def _rows_read(self, generation: int, payload) -> None:
        if generation != self._list_generation:
            return
        monitor, rows, in_order = payload
        for row in rows:
            row.describe(self._described.get(row.item))
        self._show_rows(monitor, rows, in_order, self._now())

    def _show_rows(self, monitor: str, rows: list[PlaylistRow], in_order: bool,
                   now: datetime) -> None:
        same = monitor == self._list_monitor
        scroll = self.table.verticalScrollBar().value() if same else 0
        self._list_monitor = monitor
        if self._deleted:
            rows = [row for row in rows if folder_key(row.folder) not in self._deleted]
        for row in rows:
            renamed = self._renamed.get(folder_key(row.folder))
            if renamed:
                row.folder, row.stale = renamed, True
        self.model.destination = self._destination()
        self.model.set_playlist(rows, in_order, now)
        self._apply_filter()
        self.table.verticalScrollBar().setValue(scroll)
        self._count_footer()
        self._follow_scroll()

    def _items_described(self, found: dict) -> None:
        if not found:
            return
        self._described.update(found)
        if any(p.current in found for p in self._results if p.current):
            self._render_cards()
        changed = False
        for row in self.model.items():
            changed |= row.describe(found.get(row.item))
        if changed:
            if self._filtering() or self.model.sort_column() >= 0:
                self._apply_filter()
            else:
                self.table.viewport().update()

    def _items_sized(self, found: dict) -> None:
        changed = False
        for row in self.model.items():
            size = found.get(folder_key(row.folder))
            if size is not None and size != row.size:
                row.size = size
                changed = True
        if changed:
            if self.model.sort_column() == SIZE:
                self._apply_filter()
            else:
                self.table.viewport().update()

    def _filtering(self) -> bool:
        return bool(self.filter.text().strip())

    def _apply_filter(self, *_args) -> None:
        text = self.filter.text()
        scroll = self.table.verticalScrollBar().value()
        if text.strip():
            self.model.set_filter(lambda row: row_matches(row, text))
        else:
            self.model.set_filter(None)
        self._select_on_screen()
        self.table.verticalScrollBar().setValue(scroll)

    def _select_on_screen(self) -> None:
        index = next((i for i, row in enumerate(self.model.items()) if row.on_screen), -1)
        if index >= 0 and self.model.row_of_item(index) >= 0:
            self.table.select_items([index])
        else:
            self.table.clearSelection()

    def _jump_to(self, segment: int) -> None:
        """Shown / Queue: bring that group's header to the top."""
        rows = self.model.group_rows()
        keys = [self.model.group_at(r).key for r in rows]
        key = (SHOWN, QUEUE)[segment]
        if key in keys:
            self.table.scrollTo(self.model.index(rows[keys.index(key)], 0),
                                QAbstractItemView.PositionAtTop)

    def _follow_scroll(self, *_args) -> None:
        """The segment says which group is at the top of the table."""
        top = self.table.rowAt(0)
        rows = self.model.group_rows()
        group = None
        for r in rows:
            if r <= max(top, 0):
                group = self.model.group_at(r).key
        if group is None and rows:
            group = self.model.group_at(rows[0]).key
        index = 1 if group == QUEUE else 0
        if self.jump.current_index() != index:
            self.jump.blockSignals(True)
            self.jump.set_current_index(index)
            self.jump.blockSignals(False)

    def _row_clicked(self, index) -> None:
        """A click, a double-click or Enter opens the wallpaper's folder —
        once: a double-click is also a click."""
        row = self.model.item_at(index.row())
        if row is None or self._fixture is not None:
            return
        # a folder marked [protected] here is under its new name; the entry, the old
        item = row.folder if row.stale else row.item
        last, when = self._revealed_last
        if item == last and time.monotonic() - when < REVEAL_AGAIN:
            return
        self._revealed_last = (item, time.monotonic())

        def run() -> None:
            problem = reveal(item)
            if problem:
                try:
                    self._revealed.failed.emit(problem)
                except RuntimeError:
                    pass
        threading.Thread(target=run, daemon=True, name="tracker-reveal").start()

    def _reveal_failed(self, problem: str) -> None:
        self._say("warn", problem)

    # -- a row's actions

    def _destination(self) -> str:
        """The Rotator's myprojects, as its settings say (a fixture's: its root)."""
        if self._fixture is not None:
            return self._fixture_data.get("root", "")
        return getattr(self._config, "destination", "") or ""

    def _follow_destination(self) -> None:
        """myprojects may have moved on the Settings page meanwhile."""
        destination = self._destination()
        if destination != self.model.destination:
            self.model.destination = destination
            self.table.viewport().update()

    def _action_clicked(self, row: int, _column: int, key: str) -> None:
        item = self.model.item_at(row)
        if item is None:
            return
        if key == SEND:
            self.send_to_copier(item)
        elif key == PROTECT:
            self.protect(item)
        elif key == DELETE:
            self.delete(item)

    def send_to_copier(self, row: PlaylistRow) -> bool:
        """Put the row's folder on the Copier's list, for the default copies,
        and stay here: a toast says so, with the way to the Copier."""
        if self._fixture is not None or not row.folder:
            return False
        self._copier_answer = None
        self._sending = row.title
        self.copier_requested.emit([row.folder])
        self._sending = ""
        if self._copier_answer is None:
            self._say("warn", "The Copier is not there to take it.")
            return False
        return True

    def copier_took(self, folders: list, added: int) -> None:
        """The Copier's answer to `copier_requested`: how many it added."""
        self._copier_answer = added
        names = (f"“{self._sending}”" if self._sending and len(folders) == 1
                 else ", ".join(os.path.basename(os.path.normpath(f)) for f in folders))
        if added:
            self._say("ok", f"{names} is on the Copier's list, for "
                            f"{fmt.counted(DEFAULT_COPIER_COUNT, 'copy', 'copies')}.",
                      action="Show", on_action=lambda: self.navigate.emit("copier"))
        else:
            self._say("info", f"{names} is on the Copier's list already.",
                      action="Show", on_action=lambda: self.navigate.emit("copier"))

    def protect(self, row: PlaylistRow) -> bool:
        """Mark [protected]: ask, then rename the row's folder to `[protected]
        <name>` on a worker. False when it was not offered, not wanted or not
        possible now."""
        if self._fixture is not None or self.model.protect_state(row) != OFFER:
            return False
        folder = row.folder
        if folder_key(folder) in self.model.renaming:
            return False
        if self._rotating():
            self._say("warn", "A rotation is running. Mark folders [protected] once it has "
                              "finished.")
            return False
        old, new = os.path.basename(os.path.normpath(folder)), protected_name(folder)
        if not self._answer(protect_dialog(old, new, self._dialog_parent())):
            return False
        if self._rotating():
            self._say("warn", "A rotation started meanwhile. Mark folders [protected] once it "
                              "has finished.")
            return False
        self.model.renaming.add(folder_key(folder))
        self.table.viewport().update()
        self._offload.run(lambda: protect_folder(folder),
                          lambda result: self._protected(folder, result))
        return True

    def _protected(self, folder: str, result) -> None:
        """The rename is done, or could not be: the row, and a toast."""
        self.model.renaming.discard(folder_key(folder))
        old, new = os.path.basename(os.path.normpath(folder)), protected_name(folder)
        if isinstance(result, Exception):
            reason = (str(result) if isinstance(result, ProtectError)
                      else f"{result}.")
            self.table.viewport().update()
            self._say("danger", f"Could not mark {old} [protected]: {reason} "
                                f"Nothing was changed.")
            return
        self._renamed[folder_key(folder)] = result
        for row in self.model.items():
            if folder_key(row.folder) == folder_key(folder):
                row.folder, row.stale = result, True
        self.table.viewport().update()
        self.table.request_visible_thumbs()
        self._say("ok", f"{old} is now {new}: the Rotator leaves it in myprojects. Wallpaper "
                        f"Engine's playlist still has the old name; that entry stops working "
                        f"until the next rotation.")

    def delete(self, row: PlaylistRow) -> bool:
        """Delete: ask, then send the row's folder to the Recycle Bin on a
        worker, a Workshop item unsubscribed first. False when it was not
        wanted or not possible now."""
        if self._fixture is not None or not row.folder:
            return False
        folder = row.folder
        if folder_key(folder) in self.model.deleting:
            return False
        if self._rotating():
            self._say("warn", "A rotation is running. Delete folders once it has finished.")
            return False
        name = row.title or os.path.basename(os.path.normpath(folder))
        if not self._answer(delete_dialog(name, folder, self._dialog_parent(),
                                          on_screen=row.on_screen)):
            return False
        if self._rotating():
            self._say("warn", "A rotation started meanwhile. Delete folders once it has "
                              "finished.")
            return False
        self.model.deleting.add(folder_key(folder))
        self.table.viewport().update()
        self._offload.run(lambda: self._delete_folder(folder),
                          lambda result: self._deleted_folder(folder, name, result))
        return True

    def _delete_folder(self, folder: str):
        """The work itself, on the worker. Tests put their own here."""
        return delete_wallpaper(folder)

    def _deleted_folder(self, folder: str, name: str, result) -> None:
        """Delete is done, or could not be: the row goes, and a toast."""
        key = folder_key(folder)
        self.model.deleting.discard(key)
        tone, words = deleted_words(name, result)
        if not isinstance(result, Exception):
            self._deleted.add(key)
            rows = [row for row in self.model.items() if folder_key(row.folder) != key]
            self._show_rows(self._list_monitor or "", rows, self.model.in_order, self._now())
            if self._feed is not None:
                self._feed.refresh()        # the count, without it
        else:
            self.table.viewport().update()
        self._say(tone, words)

    def _rotating(self) -> bool:
        jobs = getattr(self._services, "jobs", None)
        return jobs is not None and jobs.is_running("rotator")

    def _dialog_parent(self) -> QWidget:
        return self.window() if self.window() is not None else self

    def _answer(self, dialog):
        """Ask. Tests put their own answer here."""
        return dialog.ask()

    def _say(self, tone: str, words: str, *, action: str | None = None,
             on_action: Callable[[], None] | None = None) -> None:
        """A toast on the window, and a line kept for tests."""
        self.messages.append((tone, words))
        toasts = getattr(self.window(), "toasts", None)
        if toasts is not None:
            toasts.show_toast(words, tone, action=action, on_action=on_action)

    def show_list(self, monitor: str) -> None:
        """Show this monitor's playlist in the table (the leading one's is the
        usual); here only — the tray icon keeps its lead."""
        lead = self._lead
        self._table_monitor = None if lead is not None and monitor == lead.monitor else monitor
        self._render_cards()
        if self._fixture is not None:
            self._fixture_rows(monitor)
            return
        self._list_dirty = True
        self._read_list()

    # -- what the buttons do

    def refresh(self) -> None:
        """Look at Wallpaper Engine now, and read the list again."""
        if self._fixture is not None:
            return
        if self._feed is not None:
            self._feed.refresh()
        self._list_dirty = True
        self._read_list()

    def read_again(self) -> None:
        """Forget the titles and authors read so far, and read them again."""
        if self._fixture is not None:
            return
        self._described = {}
        self.reader.forget()
        self._list_dirty = True
        self._read_list()
        self.reader.read_items([p.current for p in self._results])

    def set_lead(self, monitor: str) -> None:
        """Lead with this monitor: here, on the tray icon and in "Next in the loop"."""
        if self._settings is not None:
            self._settings.reload_if_changed()      # not to write back what the tray changed
            self._settings.set("tracker", "primary", monitor)
            self._settings.save()
        if self._services is not None:
            self._services.snapshot.refresh([PLAYLIST])
        self._table_monitor = None
        self._render()

    def new_cycle(self, monitor: str, then: Callable[[], None] | None = None) -> bool:
        """Start a monitor's count again, after asking. `then` runs once the
        count it changed has landed; False when it was not done."""
        p = next((r for r in self._results if r.monitor == monitor), None)
        reached = f" (it has reached {fmt.ratio(p.seen, p.total)})" if p is not None else ""
        answer = self._answer(ConfirmDialog(
            f"Start {monitor}'s count again?",
            f"The count starts again from the wallpaper on screen, and the current "
            f"cycle{reached} is kept in the archive. Rarely needed: a playlist a rotation "
            f"swapped in is noticed by itself.",
            self.window(), icon="refresh", confirm_text="Start a new cycle"))
        if not answer or self._feed is None or self._fixture is not None:
            return False
        # On the feed's worker, like every look: tracker.json is read and
        # written there, and the look after it can wait on the wallpaper disk.
        self._feed.reset(monitor, lambda result: self._cycle_started(monitor, result, then))
        return True

    def _cycle_started(self, monitor: str, result, then: Callable[[], None] | None) -> None:
        if isinstance(result, Exception):
            self._say("danger", f"{monitor}'s count could not start again: {result}")
            return
        self._list_dirty = True
        self._read_list()
        if then is not None:
            then()

    def rebuild(self, then: Callable[[str], None] | None = None) -> bool:
        """Work the counts out again from the wallpapers' file times, after
        asking. `then` gets what came of it, in a sentence, once it has; False
        when it was not done."""
        answer = self._answer(ConfirmDialog(
            "Rebuild the counts from file times?",
            "Every count that does not follow Wallpaper Engine's own record is worked out "
            "again from the wallpapers' last-access times. It runs by itself when a playlist "
            "is adopted or the looking has been away, so this only forces it.",
            self.window(), icon="refresh", confirm_text="Rebuild"))
        if not answer or self._feed is None or self._fixture is not None:
            return False
        self._feed.rebuild(lambda result: self._rebuilt(result, then))
        return True

    def _rebuilt(self, result, then: Callable[[str], None] | None) -> None:
        self._list_dirty = True
        self._read_list()
        if then is not None:
            then(rebuild_sentence(result))

    def open_settings(self) -> None:
        """Playlist settings: where the count comes from, and the two ways to
        make it start over."""
        form = self.settings_dialog()
        form.ask()

    def settings_dialog(self, *, embedded: bool = False) -> FormDialog:
        form = FormDialog("Playlist settings", None if embedded else self.window(),
                          body="How the Tracker counts Wallpaper Engine's playlists.",
                          icon="tracker", save_text="Done", embedded=embedded)
        form.cancel_button().hide()

        where = QWidget()
        column = QVBoxLayout(where)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(theme.SP_6)
        config = PathField(getattr(self._feed, "config_path", "") or "", editable=False,
                           kind="file", placeholder="config.json not found")
        column.addWidget(config)
        facts = label(self._settings_facts(), "type.caption", "lo")
        facts.setWordWrap(True)
        column.addWidget(facts)
        change = LinkButton("Change these in Settings")
        change.clicked.connect(lambda: (form.reject(), self.navigate.emit("settings")))
        line = QHBoxLayout()
        line.addWidget(change)
        line.addStretch(1)
        column.addLayout(line)
        form.add_row("Where the count comes from", where)

        again = QWidget()
        column = QVBoxLayout(again)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(theme.SP_6)
        rebuild = GhostButton("Rebuild from file times", outlined=True)
        result = label("", "type.caption", "lo")
        result.setWordWrap(True)
        result.hide()
        line = QHBoxLayout()
        line.addWidget(rebuild)
        line.addStretch(1)
        column.addLayout(line)
        column.addWidget(result)
        if getattr(self._feed, "atime_ok", None) is False:
            rebuild.setEnabled(False)
            result.setText("NTFS last-access updates are off on this machine, so time when "
                           "nothing was looking cannot be recovered — keep the tray running.")
            set_tone(result, "warn")
            result.show()

        def say(sentence: str, done: bool = True) -> None:
            try:
                result.setText(sentence)
                set_tone(result, "lo")
                result.show()
                rebuild.setEnabled(done)
            except RuntimeError:
                pass            # the dialog closed before the feed answered

        def rebuilt() -> None:
            if self.rebuild(say):
                say("Rebuilding from file times…", done=False)
        rebuild.clicked.connect(rebuilt)
        form.add_row("Rebuild from file times", again,
                     "Works each count out again from the wallpapers' last-access times.")

        cycles = QWidget()
        column = QVBoxLayout(cycles)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(theme.SP_6)
        for p in self._results:
            line = QHBoxLayout()
            line.setSpacing(theme.SP_8)
            words = label(f"{p.monitor} · “{p.playlist}” · {fmt.ratio(p.seen, p.total)}",
                          "type.bodySm", "body")
            button = GhostButton("New cycle…", outlined=True, size="sm")
            button.clicked.connect(lambda _c=False, m=p.monitor, w=words: self._cycle_from_form(m, w))
            line.addWidget(words, 1)
            line.addWidget(button)
            column.addLayout(line)
        if not self._results:
            column.addWidget(label("No playlist is counted yet.", "type.bodySm", "lo"))
        form.add_row("New cycle", cycles,
                     "Starts a monitor's count again; the cycle it had is kept in the archive.")
        return form

    def _cycle_from_form(self, monitor: str, words) -> None:
        def said() -> None:
            p = next((r for r in self._results if r.monitor == monitor), None)
            if p is None:
                return
            try:
                words.setText(f"{p.monitor} · “{p.playlist}” · {fmt.ratio(p.seen, p.total)}")
            except RuntimeError:
                pass            # the dialog closed before the feed answered
        self.new_cycle(monitor, then=said)

    def _settings_facts(self) -> str:
        heartbeat = None
        if self._settings is not None:
            from ..tracker_feed import heartbeat_setting
            heartbeat = heartbeat_setting(self._settings) // 60
        lead = self._preferred()
        parts = []
        if heartbeat:
            parts.append(f"also checks every {heartbeat} min")
        parts.append(f"lead monitor: {lead}" if lead else
                     "lead monitor: automatic" + (f" ({self._lead.monitor})" if self._lead else ""))
        parts.append("counting in the background" if self._background
                     else "counting only while this window is open")
        text = " · ".join(parts)
        return text[:1].upper() + text[1:] + "."

    # -- for tests and snapshots

    def texts(self) -> dict:
        return {"subtitle": self.subtitle(), "lead": self.lead_card.texts(),
                "summaries": [c.texts() for c in self.summary_cards], "pace": self.pace.texts(),
                "footer": self.footer.text(),
                "empty": (self.empty.title() if self._stack.currentWidget() is self._empty_holder
                          else "")}

    def frame_fixture(self, state: str) -> dict | None:
        """What the frame shows with this page's fixture: which of its states,
        and this page's sidebar item and "Next in the loop" to match."""
        if state not in self.FIXTURES:
            return None
        data = load_tracker_fixture()
        spec = data["states"][state]
        now = datetime.fromisoformat(spec.get("now", data["now"]))
        results = [_fixture_progress(m["progress"]) for m in spec.get("monitors", [])]
        lead = pick_primary(results, spec.get("primary"))
        out = {"frame": spec.get("frame", "idle")}
        if lead is None:
            out["nav"] = {"tracker": {"kind": "none"}}
            out["next"] = "No playlist counted yet"
            return out
        out["nav"] = {"tracker": {"kind": "count", "done": lead.seen, "total": lead.total}}
        from ..main_window import next_in_loop
        when = finish_at(lead, now)
        estimate = when.strftime("%d %b %H:%M") if when is not None else None
        from ..services.snapshot import PlaylistProgress
        out["next"] = next_in_loop(PlaylistProgress(
            lead.monitor, lead.seen, lead.total, max(lead.total - lead.seen, 0), lead.percent,
            lead.started, estimate, lead.live), now)
        return out

    def load_fixture(self, state: str) -> None:
        """A made-up state from tests/fixtures/ui/tracker.json: the monitors,
        their countdowns, what is on screen, and the lead monitor's list."""
        if state not in self.FIXTURES:
            super().load_fixture(state)
        data = load_tracker_fixture()
        spec = data["states"][state]
        now = datetime.fromisoformat(spec.get("now", data["now"]))
        self._fixture = spec
        self._fixture_data = data
        self._minute.stop()
        self._list_timer.stop()
        self.countdowns.stop()
        self._now = lambda: now
        self._engine_fixed = spec.get("engine")
        self._background = bool(spec.get("background"))
        self._next_run = spec.get("next_run")
        self._error = spec.get("error")
        self._results = []
        self._fixture_countdowns = {}
        self._fixture_resolutions = {}
        self._fixture_cycles = {}
        self._described = {}
        self._renamed = {}
        for n in spec.get("renamed", []):
            folder = str(folder_of(_fixture_item(data, n)))
            self._renamed[folder_key(folder)] = os.path.join(os.path.dirname(folder),
                                                             protected_name(folder))
        for m in spec.get("monitors", []):
            p = _fixture_progress(m["progress"])
            cycle = _fixture_cycle(data, m, p, now)
            p.current = cycle.current
            self._results.append(p)
            self._fixture_cycles[p.monitor] = cycle
            if m.get("countdown") is not None:
                self._fixture_countdowns[p.monitor] = _FixtureCountdown(**m["countdown"])
            self._fixture_resolutions[p.monitor] = m.get("resolution", "")
            if p.current:
                self._described[p.current] = _fixture_described(m.get("wallpaper", {}))
        if self._settings is not None and "primary" in spec:
            self._settings.set("tracker", "primary", spec["primary"])
        self._views = {}
        self._render()
        lead = self._lead
        if lead is not None:
            self._fixture_rows(lead.monitor)

    def _fixture_rows(self, monitor: str) -> None:
        cycle = self._fixture_cycles.get(monitor)
        if cycle is None:
            return
        p = next(r for r in self._results if r.monitor == monitor)
        data, now = self._fixture_data, self._now()
        rows, in_order = playlist_rows(
            cycle, now, live=bool(p.live),
            described=lambda item: self._described.get(item) or _fixture_row_described(data, item))
        for row in rows:
            # made up, as the rest: between 40 MB and 940 MB
            row.size = ((_fixture_number(row.item) * 37) % 900 + 40) * 1_000_000
        self._show_rows(monitor, rows, in_order, now)


# ---- what came of a rebuild ------------------------------------------------------------------------

def rebuild_sentence(result) -> str:
    """Rebuilding from file times, said once it is done: `result` is the
    number of wallpapers it recovered, or the exception it raised."""
    if isinstance(result, Exception):
        return f"The counts could not be rebuilt: {result}"
    if result:
        return (f"Recovered {fmt.counted(result, 'wallpaper')} shown while nothing "
                f"was watching.")
    return "Nothing to recover: the counts already match the file times."


# ---- the empty states ------------------------------------------------------------------------------

def empty_text(error: str | None, engine: bool | None,
               config_path: str = "") -> tuple[str, str, str, str]:
    """(title, body, action, the page the action goes to) when no monitor can
    be shown."""
    error = error or ""
    if error.startswith("Cannot read"):
        missing = "it is not there" in error or not config_path
        if missing:
            return ("Wallpaper Engine's config.json was not found",
                    "The Tracker reads each monitor's playlist from it. It is found by itself "
                    "where Steam keeps Wallpaper Engine; if yours is elsewhere, choose it in "
                    "Settings.",
                    "Choose it in Settings", "settings")
        detail = error.split(": ", 1)[-1]
        return ("Wallpaper Engine's config.json could not be read",
                f"{detail}. Wallpaper Engine may be writing it this moment: the Tracker looks "
                f"again as soon as it changes.", "Check it in Settings", "settings")
    if error:
        if engine is False:
            return ("Wallpaper Engine is not running",
                    "Start it and apply a playlist: the Tracker counts the playlist each "
                    "monitor plays, from the moment Wallpaper Engine writes it down.", "", "")
        return ("No playlist is running",
                "Every monitor shows a single wallpaper, or Wallpaper Engine has not written "
                "its playlists down yet — it does when it starts, exits or saves a playlist. "
                "Apply a playlist in Wallpaper Engine and this fills in.", "", "")
    if engine is False:
        return ("Wallpaper Engine is not running",
                "Nothing has been counted yet. Start Wallpaper Engine with a playlist, and the "
                "Tracker follows it.", "", "")
    return ("Looking at Wallpaper Engine…", "The playlists are read in a moment.", "", "")


# ---- fixtures ----------------------------------------------------------------------------------------

FIXTURE_FILE = (Path(__file__).resolve().parent.parent.parent
                / "tests" / "fixtures" / "ui" / "tracker.json")


def load_tracker_fixture() -> dict:
    """tests/fixtures/ui/tracker.json, from a source checkout."""
    data = json.loads(FIXTURE_FILE.read_text(encoding="utf-8"))
    data.pop("//", None)
    return data


@dataclass(frozen=True)
class _FixtureCountdown:
    remaining: float | None = None
    approximate: bool = False
    paused: bool = False
    active: bool = True


def _fixture_progress(spec: dict) -> Progress:
    values = {"cycle_id": "fixture", "playlist": "custom", "changes": spec.get("seen", 0),
              "repeats": 0, "order": "random", "delay": 10, "current": None, "current_title": "",
              "current_since": None, "live": True, "inferred": 0, "anchor": ANCHOR_ROTATION,
              "gone": 0}
    values.update(spec)
    return Progress(**values)


WORKSHOP_ID = 1_700_000_000         # a made-up Workshop folder is this plus its number


def _fixture_item(data: dict, n: int) -> str:
    """Wallpaper n of a fixture's playlists: in myprojects (`root`) — under
    `[protected] …` when listed in `protected` — or every `workshop_every`-th
    a subscribed one, in the Workshop's folder."""
    every = data.get("workshop_every")
    if every and n % every == 0 and data.get("workshop_root"):
        return f"{data['workshop_root']}/{WORKSHOP_ID + n}/scene.pkg"
    name = f"wallpaper_{n:04d}"
    if n in data.get("protected", ()):
        name = f"{PROTECTED_PREFIX} {name}"
    return f"{data['root']}/{name}/scene.pkg"


def _fixture_number(item: str) -> int:
    """Which of the fixture's wallpapers an entry is (see `_fixture_item`)."""
    name = item.replace("\\", "/").rstrip("/").split("/")[-2]
    if name.isdigit():
        return int(name) - WORKSHOP_ID
    return int(name.rsplit("wallpaper_", 1)[1])


def _fixture_cycle(data: dict, entry: dict, p: Progress, now: datetime) -> Cycle:
    """A cycle that fits the fixture's count: `total` wallpapers (and the
    deleted ones), the first `seen` of a shuffle shown, one a pace apart."""
    playlist = entry.get("playlist", {})
    count = p.total + p.gone
    items = [_fixture_item(data, n) for n in range(1, count + 1)]
    step = playlist.get("stride", 37)
    order = [items[(k * step) % count] for k in range(count)]
    gone = order[-p.gone:] if p.gone else []
    live_order = [i for i in order if i not in gone]
    shown = live_order[:p.seen]
    minutes = playlist.get("minutes", 42)
    current = p.current or (shown[-1] if shown else None)
    since = _time(p.current_since) or now
    seen = {}
    for k, item in enumerate(reversed(shown)):
        seen[item] = (since - timedelta(minutes=minutes * k)).strftime(TIME_FMT)
    inferred = shown[:p.inferred] if p.inferred else []
    if current and current not in seen and shown:
        current = shown[-1]
    return Cycle(monitor=p.monitor, playlist=p.playlist, started=p.started, items=items,
                 seen=seen, current=current, current_since=p.current_since, order=p.order,
                 inferred=list(inferred), missing=list(gone), anchor=p.anchor)


def _fixture_described(spec: dict) -> Described:
    from ..engines.wallpaper_meta import WallpaperMeta
    return Described(WallpaperMeta(title=spec.get("title", ""), kind=spec.get("type", "scene")),
                     spec.get("author", ""), bool(spec.get("known")))


def _fixture_row_described(data: dict, item: str) -> Described:
    from ..engines.wallpaper_meta import WallpaperMeta
    n = _fixture_number(item)
    words, authors, kinds = data["title_words"], data["authors"], data["kinds"]
    first, second = (n * 7) % len(words), (n * 3 + 5) % len(words)
    if second == first:
        second = (second + 1) % len(words)
    title = f"{words[first]} {words[second]}"
    author = authors[n % len(authors)] if n % 11 else ""
    return Described(WallpaperMeta(title=title, kind=kinds[n % len(kinds)]), author,
                     known=bool(author) and n % 4 == 0)
