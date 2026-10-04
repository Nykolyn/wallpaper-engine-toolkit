"""Overview: the state of the loop at a glance.

Every number here comes from what the window already holds — the Snapshot's
readings, the JobCenter, the TrackerFeed's last look, the activity journal —
so nothing on this thread lists a folder on the W: disk (REDESIGN_PLAN §2.4):

- four StatCards: RESERVE, IN ROTATION, PLAYLIST and NEW SINCE LAST REVIEW. A
  shimmer until the first reading, the reason when there is none ("set the
  reserve folder in Settings", "no scan yet"), and a value read before but not
  now in text.lo, marked "last known". Each opens the page it comes from.
- "The loop": a tile each for the Rotator, the Tracker and Review — what each
  is doing, a thin bar, a line of numbers — the tile of a running job accented
  with a pulsing dot; and the sentence that says what the next run will draw.
- "Recent activity": the journal's newest entries.
- The monitors, from the TrackerFeed, the leading one first, and the log: the
  tail of the running job's log file, or of the one written last, followed
  while the page is on screen.

The words come from plain functions of those sources (`reserve_card`,
`rotator_tile`, `activity_row`, …), which tests call without building a
widget; the page puts their answers on screen. Nothing is invented: a number
that is not known shows its empty state, never 0 (§2.6).
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QModelIndex, Qt, QTimer
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QHBoxLayout, QStackedLayout, QVBoxLayout, QWidget

from .. import theme
from ..engines.tracker import pick_primary
from ..engines.wallpaper_meta import MetaCache
from ..engines.wallpaper_timer import monitor_resolutions
from ..services.activity import TOOLS
from ..services.logstore import LogTail
from ..services.snapshot import (
    KEYS, LAST_RUN, PLAYLIST, RESERVE, REVIEW, ROTATION, PlaylistProgress, Reading,
    ReserveCounts, ReviewState, RotationCounts, RunSummary, parse_estimate,
)
from ..ui.kit import (
    CardTitle, GhostButton, GlassPanel, ListRow, LogPanel, MonitorCard, MonitorView, RowList,
    Rule, StatCard, ToolTile, format as fmt, label,
)
from ..ui.kit.chips import VARIANTS as CHIP_VARIANTS
from ..ui.kit.tables import LIST_ROW_ROLE
from .base import Page
from .tracker import ListReader

ACTIVITY_ROWS = 8           # entries in "Recent activity"
LOG_LINES = 300             # the newest lines of a log the panel holds
_LOG_POLL_MS = 1_000        # how often a running job's log is looked at again
_MINUTE_MS = 60_000
_TOOL_ROLE = Qt.UserRole + 1


# ---- what the cards say ------------------------------------------------------------------

@dataclass(frozen=True)
class CardText:
    """A StatCard's words: a value (None while loading or when there is none),
    its caption, its tone, and the page a click opens. `link` writes the reason
    for an empty card as a link: the click goes where it is put right."""
    value: object = None
    caption: str = ""
    tone: str | None = None
    loading: bool = False
    link: bool = False
    target: str = ""

    @property
    def empty(self) -> bool:
        return self.value is None and not self.loading


def _loading(reading: Reading) -> bool:
    """Never read, and no failure yet: the first reading is on its way."""
    return reading.at is None and not reading.error


def _folder_empty(reading: Reading, folder: str, target: str) -> CardText:
    """A Rotator folder's count that is not there, and why."""
    if reading.reason == "unset":
        return CardText(caption=f"set the {folder} folder in Settings", link=True,
                        target="settings")
    if reading.reason == "missing":
        return CardText(caption=f"{folder} folder not found — check Settings", link=True,
                        target="settings")
    if reading.reason == "unreadable":
        return CardText(caption="the rotation history could not be read", target=target)
    return CardText(caption="could not be counted", target=target)


def reserve_card(reading: Reading) -> CardText:
    """RESERVE: folders in the reserve, and how many were never used."""
    counts: ReserveCounts | None = reading.value
    if counts is not None:
        caption = f"{fmt.count(counts.never_used)} never used"
        if reading.error:
            return CardText(counts.folders, f"{caption} · last known", "lo", target="rotator")
        return CardText(counts.folders, caption, target="rotator")
    if _loading(reading):
        return CardText(loading=True, target="rotator")
    return _folder_empty(reading, "reserve", "rotator")


def rotation_card(reading: Reading) -> CardText:
    """IN ROTATION: folders in myprojects, and what today's runs swapped in."""
    counts: RotationCounts | None = reading.value
    if counts is not None:
        caption = "in myprojects"
        if counts.moved_today > 0:
            caption += f" · {fmt.count(counts.moved_today)} swapped today"
        if reading.error:
            return CardText(counts.folders, f"{caption} · last known", "lo", target="rotator")
        return CardText(counts.folders, caption, target="rotator")
    if _loading(reading):
        return CardText(loading=True, target="rotator")
    return _folder_empty(reading, "myprojects", "rotator")


def playlist_card(reading: Reading) -> CardText:
    """PLAYLIST: the leading monitor's count. Not live, it is the last known."""
    p: PlaylistProgress | None = reading.value
    if p is None or p.total <= 0:
        if _loading(reading):
            return CardText(loading=True, target="tracker")
        return CardText(caption="no playlist counted yet", target="tracker")
    value = fmt.ratio(p.seen, p.total)
    if not p.live or reading.error:
        return CardText(value, f"{p.monitor} leading · last known", "lo", target="tracker")
    if p.remaining <= 0:
        return CardText(value, f"{p.monitor} leading · all shown", "ok", target="tracker")
    return CardText(value, f"{p.monitor} leading · {fmt.count(p.remaining)} to go",
                    target="tracker")


def review_card(reading: Reading, now: datetime) -> CardText:
    """NEW SINCE LAST REVIEW: what the last scan found, while it waits to be
    reviewed. Once the review is finished the count is not known again until
    the next scan, and the card says so rather than show 0."""
    if reading.value is None:
        if _loading(reading):
            return CardText(loading=True, target="review")
        if reading.error:
            return CardText(caption="the last review could not be read", target="review")
        return CardText(caption="no scan yet", target="review")
    state = ReviewState.from_json(reading.value)
    if state.finished is not None:
        return CardText(caption=f"reviewed {fmt.date_activity(state.finished, now)} · "
                                "no scan since", target="review")
    if state.items is None:
        return CardText(caption="the last review could not be read", target="review")
    stale = " · last known" if reading.error else ""
    if state.items == 0:
        scanned = f" · scanned {fmt.date_activity(state.scanned, now)}" if state.scanned else ""
        return CardText(0, f"nothing new{scanned}{stale}", "lo" if stale else None,
                        target="review")
    authors = f"from {fmt.counted(state.authors, 'author')} · " if state.authors else ""
    return CardText(state.items, f"{authors}not reviewed{stale}", "lo" if stale else "warn",
                    target="review")


# ---- what the loop's tiles say --------------------------------------------------------------

@dataclass(frozen=True)
class TileText:
    """A ToolTile's words: the status line and its tone, the bar (None: no
    bar), the mono meta line, and whether its job is the one running."""
    status: str
    tone: str = "mid"
    fraction: float | None = None
    indeterminate: bool = False
    bar_tone: str = "muted"
    meta: str = ""
    active: bool = False


def _sentence(text: str) -> str:
    return text[:1].upper() + text[1:]


def job_tile(job) -> TileText:
    """A tool whose job is running: its phase, its bar, its count and, once
    the job's own pace gives one, the time left."""
    parts = []
    if job.total > 0:
        parts.append(job.count_text or fmt.ratio(job.done, job.total))
    eta = job.eta()
    if eta is not None:
        parts.append(fmt.left(eta))
    return TileText(_sentence(job.phase_text or job.title), "accent", job.fraction(),
                    job.total <= 0, "accent", " · ".join(parts), True)


def rotator_tile(job, last_run: Reading, now: datetime) -> TileText:
    if job is not None:
        return job_tile(job)
    last: RunSummary | None = last_run.value
    if last is None:
        if _loading(last_run):
            return TileText(fmt.DASH, "lo")
        if last_run.error:
            return TileText("The rotation history could not be read", "warn")
        return TileText("No run yet", "lo")
    when = fmt.date_activity(last.finished or last.started, now) if (
        last.finished or last.started) else ""
    n = last.number
    if last.result == "problems":
        status = (f"Run {n} had {fmt.counted(last.failed, 'problem')}" if last.failed
                  else f"Run {n} finished with problems")
        return TileText(status, "warn", meta=when)
    if last.result == "failed":
        return TileText(f"Run {n} failed", "danger", meta=when)
    if last.result == "stopped":
        return TileText(f"Run {n} was stopped", "mid", meta=when)
    meta = f"run {n} clean" + (f" · {when}" if when else "")
    return TileText(f"Ready for run {n + 1}", "mid", meta=meta)


def tracker_tile(playlist: Reading, now: datetime) -> TileText:
    p: PlaylistProgress | None = playlist.value
    if p is None or p.total <= 0:
        if _loading(playlist):
            return TileText(fmt.DASH, "lo")
        return TileText("No playlist counted yet", "lo")
    shown = f"{fmt.ratio(p.seen, p.total, 'prose')} shown"
    fraction = max(0.0, min(1.0, p.seen / p.total))
    if not p.live or playlist.error:
        status = ("Wallpaper Engine is not running" if p.from_engine
                  else "Nothing from the playlist on screen")
        return TileText(status, "lo", fraction, meta=f"{shown} · last known")
    if p.remaining <= 0:
        return TileText("Playlist finished — time to rotate", "ok", 1.0, bar_tone="ok",
                        meta=f"all {fmt.count(p.total)} shown")
    when = parse_estimate(p.finish_estimate, now)
    meta = f"{shown} · {fmt.estimate_day(when, now)}" if when is not None else shown
    return TileText("Counting the playlist down", "mid", fraction, meta=meta)


def review_tile(job, review: Reading, now: datetime) -> TileText:
    if job is not None:
        return job_tile(job)
    if review.value is None:
        if _loading(review):
            return TileText(fmt.DASH, "lo")
        if review.error:
            return TileText("The last review could not be read", "warn")
        return TileText("No scan yet", "lo")
    state = ReviewState.from_json(review.value)
    scanned = f"last scan {fmt.date_activity(state.scanned, now)}" if state.scanned else ""
    if state.finished is not None:
        return TileText(f"Reviewed {fmt.date_activity(state.finished, now)}", "mid", meta=scanned)
    if state.items is None:
        return TileText("The last review could not be read", "warn")
    if state.items == 0:
        return TileText("Nothing new", "mid", meta=scanned)
    items = (f"{fmt.counted(state.items, 'item')} since {fmt.day(state.since, now)}"
             if state.since else fmt.counted(state.items, "new item", "new items"))
    waiting = state.waiting if state.waiting is not None else state.authors
    if waiting:
        return TileText(f"{fmt.counted(waiting, 'author')} waiting", "warn", meta=items)
    return TileText("Every author gone through · not finished", "mid", meta=items)


def loop_subtitle(job, last_run: Reading, now: datetime) -> str:
    """"run 38 · started 13:41" while it runs; the last run otherwise."""
    if job is not None:
        match = re.fullmatch(r"Run (\d+)", job.title)
        what = f"run {match.group(1)}" if match else job.title[:1].lower() + job.title[1:]
        return f"{what} · started {fmt.date_activity(job.started, now)}"
    last: RunSummary | None = last_run.value
    if last is not None:
        when = f" · {fmt.date_activity(last.started, now)}" if last.started else ""
        return f"last run {last.number}{when}"
    if _loading(last_run) or last_run.error:
        return ""
    return "no run yet"


def loop_sentence(reserve: Reading) -> str:
    """What the next run will draw (§7.5): the rotation is started by hand."""
    counts: ReserveCounts | None = reserve.value
    if counts is None:
        if _loading(reserve):
            return ""
        if reserve.reason == "unset":
            return "Set the reserve and myprojects folders in Settings, and the Rotator can start."
        if reserve.reason == "missing":
            return "The reserve folder was not found, so what the next run draws is not known."
        return "The reserve could not be counted, so what the next run draws is not known."
    batch, never = fmt.count(counts.batch), fmt.count(counts.never_used)
    if counts.will_reset:
        return (f"The history resets on the next run: only {never} folders were never used, "
                f"fewer than the {batch} it moves, so it draws from the whole reserve again.")
    return f"The next run draws {batch} at random from the {never} never used."


# ---- what the rows and the monitors say ---------------------------------------------------

_TONES = {"failed": "danger", "problems": "warn"}


def activity_row(entry, now: datetime) -> ListRow:
    """A journal entry as a row: when, the tool's glyph, the title over its
    detail, and its chip. A chip or a tool this build does not know is left out."""
    chips = ((entry.chip, None),) if entry.chip in CHIP_VARIANTS else ()
    return ListRow(entry.title, entry.detail, fmt.date_activity(entry.ts, now),
                   icon=entry.tool if entry.tool in TOOLS else "",
                   icon_tone=_TONES.get(entry.outcome, "text.mid"), chips=chips)


def _time(text) -> datetime | None:
    try:
        return datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None


def monitor_view(p, leading: bool, now: datetime, *, author: str = "",
                 resolution: str = "") -> MonitorView:
    """One monitor's card, from the tracker's Progress, with its display's size
    and the wallpaper's author when they are known (left out while they are
    not). Not live, it shows the last known wallpaper and count, and says so."""
    since = _time(p.current_since) if p.live else None
    shown = (now - since).total_seconds() if since is not None and now >= since else None
    if p.live:
        note = ""
    elif getattr(p, "from_engine", False):
        note = "last known · Wallpaper Engine is not running"
    else:
        note = "last known · nothing from the playlist on screen"
    folder = os.path.dirname(p.current.replace("/", "\\")) if p.current else None
    return MonitorView(p.monitor, "leading" if leading else "summary",
                       resolution=resolution, title=p.current_title or "", author=author,
                       position=p.seen, total=p.total, shown_for=shown,
                       preview=folder or None, note=note)


def monitor_views(results, preferred: str | None, now: datetime, *,
                  authors: dict[str, str] | None = None,
                  resolutions: dict[str, str] | None = None) -> list[MonitorView]:
    """Every monitor the tracker counts, the leading one first. `authors` is by
    wallpaper (what is on screen), `resolutions` by monitor."""
    results = list(results or [])
    lead = pick_primary(results, preferred)
    if lead is None:
        return []
    authors, resolutions = authors or {}, resolutions or {}
    return [monitor_view(p, p is lead, now, author=authors.get(p.current or "", ""),
                         resolution=resolutions.get(p.monitor, ""))
            for p in [lead, *[r for r in results if r is not lead]]]


# ---- the page ---------------------------------------------------------------------------

class OverviewPage(Page):
    key = "overview"
    title = "Overview"
    icon = "overview"
    FIXTURES = ("running", "idle", "empty", "we-off")

    def __init__(self, services=None, feed=None, parent: QWidget | None = None, *,
                 settings=None, now=None):
        super().__init__(parent)
        self._services = services
        self._feed = feed
        self._settings = settings
        self._now = now or datetime.now
        self._fixture: dict | None = None
        self._entries: list = []
        self._entries_loaded = False
        self._log_path: Path | None = None
        self._log_tail: LogTail | None = None
        self._running_jobs: frozenset = frozenset()
        # What is known of the wallpapers on screen, read on a thread of the
        # reader's own (the Tracker page's ListReader and MetaCache).
        self._described: dict = {}
        self._asked: set[str] = set()
        self._reader = ListReader(MetaCache(), parent=self)
        self._reader.described.connect(self._items_described)
        self._resolution_key = None
        self._resolution_cache: dict[str, str] = {}

        body = QVBoxLayout(self)
        body.setContentsMargins(theme.BODY_PAD[1], theme.BODY_PAD[0],
                                theme.BODY_PAD[1], theme.BODY_PAD[0])
        body.setSpacing(theme.PANEL_GAP)
        body.addLayout(self._build_cards())
        lower = QHBoxLayout()
        lower.setSpacing(theme.PANEL_GAP)
        left = QVBoxLayout()
        left.setSpacing(theme.PANEL_GAP)
        left.addWidget(self._build_loop())
        left.addWidget(self._build_activity(), 1)
        lower.addLayout(left, 1)
        lower.addWidget(self._build_side())
        body.addLayout(lower, 1)

        # The date under the title, and every "13:47" / "14 min in", move on
        # with the minute: the first tick lands on the next one.
        self._minute = QTimer(self)
        self._minute.setSingleShot(True)
        self._minute.timeout.connect(self._tick)
        self._log_timer = QTimer(self)
        self._log_timer.setInterval(_LOG_POLL_MS)
        self._log_timer.timeout.connect(self._read_log)

        if services is not None:
            services.snapshot.refreshed.connect(self._snapshot_refreshed)
            services.jobs.changed.connect(self._job_changed)
            services.journal.appended.connect(self._appended)
        if feed is not None:
            feed.updated.connect(self._render_monitors)
            feed.config_changed.connect(self._render_monitors)
        self._tick()
        self._render()

    # -- building

    def _build_cards(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(theme.PANEL_GAP)
        self.cards: dict[str, StatCard] = {}
        for key, overline in ((RESERVE, "Reserve"), (ROTATION, "In rotation"),
                              (PLAYLIST, "Playlist"), (REVIEW, "New since last review")):
            card = StatCard(overline, clickable=True)
            vertical, horizontal = theme.OVERVIEW_STAT_PAD
            card.setContentsMargins(horizontal, vertical, horizontal, vertical)
            card.clicked.connect(lambda k=key: self._card_clicked(k))
            self.cards[key] = card
            row.addWidget(card, 1)
        self._targets = {key: "" for key in self.cards}
        return row

    def _build_loop(self) -> GlassPanel:
        panel = GlassPanel(padding="none")
        vertical, horizontal = theme.LOOP_PAD
        panel.setContentsMargins(horizontal, vertical, horizontal, vertical)
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(theme.LOOP_GAP)
        self.loop_title = CardTitle("The loop")
        open_rotator = GhostButton("Open Rotator →", size="sm")
        open_rotator.clicked.connect(lambda: self.navigate.emit("rotator"))
        self.loop_title.add_action(open_rotator)
        column.addWidget(self.loop_title)
        tiles = QHBoxLayout()
        tiles.setSpacing(theme.TILE_SPACING)
        self.tiles: dict[str, ToolTile] = {}
        for key, name in (("rotator", "Rotator"), ("tracker", "Tracker"), ("review", "Review")):
            tile = ToolTile(name, key)
            tile.clicked.connect(lambda k=key: self.navigate.emit(k))
            self.tiles[key] = tile
            tiles.addWidget(tile, 1)
        column.addLayout(tiles)
        foot = QVBoxLayout()
        foot.setSpacing(theme.LOOP_RULE_PAD)
        self._rule = Rule()
        self.sentence = label("", "type.label", "mid")
        self.sentence.setWordWrap(True)
        foot.addWidget(self._rule)
        foot.addWidget(self.sentence)
        column.addLayout(foot)
        return panel

    def _build_activity(self) -> GlassPanel:
        panel = GlassPanel(padding="none")
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        top, sides, bottom = theme.ACTIVITY_HEAD_PAD
        head = CardTitle("Recent activity")
        head.setContentsMargins(sides, top, sides, bottom)
        folder = GhostButton("Open log folder", size="sm")
        folder.clicked.connect(self._open_logs)
        head.add_action(folder)
        column.addWidget(head)
        holder = QWidget()
        pad = theme.ACTIVITY_LIST_PAD
        holder_layout = QStackedLayout(holder)
        holder_layout.setContentsMargins(pad, 0, pad, pad)
        self.activity_model = QStandardItemModel(self)
        self.activity = RowList()
        self.activity.setModel(self.activity_model)
        self.activity.setAccessibleName("Recent activity")
        self.activity.clicked.connect(self._row_clicked)
        self.activity_empty = label("Nothing has happened yet", "type.body", "lo")
        self.activity_empty.setAlignment(Qt.AlignCenter)
        holder_layout.addWidget(self.activity)
        holder_layout.addWidget(self.activity_empty)
        self._activity_stack = holder_layout
        column.addWidget(holder, 1)
        return panel

    def _build_side(self) -> QWidget:
        side = QWidget()
        side.setFixedWidth(theme.OVERVIEW_SIDE)
        column = QVBoxLayout(side)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(theme.PANEL_GAP)
        self._monitor_column = QVBoxLayout()
        self._monitor_column.setSpacing(theme.PANEL_GAP)
        column.addLayout(self._monitor_column)
        self.monitor_cards: list[MonitorCard] = []
        self.no_monitors = GlassPanel(padding="sm")
        note = QVBoxLayout(self.no_monitors)
        note.setContentsMargins(0, 0, 0, 0)
        note.addWidget(label("No playlist counted yet", "type.bodySm", "lo"))
        self._monitor_column.addWidget(self.no_monitors)
        self.log = LogPanel("Log", expanded=False, fill=True, on_open_folder=self._open_log_folder)
        column.addWidget(self.log, 1)
        return side

    # -- the header

    def make_header_actions(self) -> list[QWidget]:
        refresh = GhostButton("Refresh now", outlined=True)
        refresh.clicked.connect(self.refresh)
        return [refresh]

    def refresh(self) -> None:
        """Read the numbers again: the snapshot, the tracker's own look, the
        journal and the log."""
        if self._fixture is not None:
            return
        if self._feed is not None:
            self._feed.refresh()
        if self._services is not None:
            self._services.snapshot.refresh()
        if self.isVisible():
            self._load_activity()
            self._pick_log()

    def _tick(self) -> None:
        now = self._now()
        self.set_subtitle(fmt.date_long(now))
        if self._fixture is None:
            wait = _MINUTE_MS - (now.second * 1000 + now.microsecond // 1000)
            self._minute.start(max(1, wait))
        if self._entries_loaded:
            self._render_activity()
        self._render_monitors()
        self._render_loop()

    # -- on and off screen

    def on_shown(self) -> None:
        if self._fixture is not None:
            return
        # after the window is up, never inside anyone's constructor
        QTimer.singleShot(0, self._arrive)

    def _arrive(self) -> None:
        if self._fixture is not None or not self.isVisibleTo(self.window()):
            return
        if not self._entries_loaded:
            self._load_activity()
        self._pick_log()

    def on_hidden(self) -> None:
        self._log_timer.stop()

    # -- reading what the window holds

    def _reading(self, key: str) -> Reading:
        if self._services is None:
            return Reading()
        return self._services.snapshot[key]

    def _running(self, tool: str):
        if self._services is None:
            return None
        return next((j for j in self._services.jobs.running() if j.tool == tool), None)

    def _preferred(self) -> str | None:
        if self._settings is None:
            return None
        return self._settings.get("tracker", "primary", None)

    def _snapshot_refreshed(self, keys) -> None:
        self._render_cards()
        self._render_loop()

    def _job_changed(self, job) -> None:
        self._render_loop()
        if self._fixture is not None or self._services is None:
            return
        # A job that starts or ends changes which log is followed, and so does
        # its log file, which is named just after the job starts; a count
        # moving (ten times a second) does not.
        running = self._services.jobs.running()
        numbers = frozenset(j.number for j in running)
        logged = next((Path(j.log_path) for j in running if j.log_path), None)
        if numbers != self._running_jobs or (logged is not None and logged != self._log_path):
            self._running_jobs = numbers
            if self.isVisible():
                self._pick_log()

    # -- putting it on screen

    def _render(self) -> None:
        self._render_cards()
        self._render_loop()
        self._render_monitors()

    def _render_cards(self) -> None:
        now = self._now()
        texts = {RESERVE: reserve_card(self._reading(RESERVE)),
                 ROTATION: rotation_card(self._reading(ROTATION)),
                 PLAYLIST: playlist_card(self._reading(PLAYLIST)),
                 REVIEW: review_card(self._reading(REVIEW), now)}
        for key, text in texts.items():
            card = self.cards[key]
            self._targets[key] = text.target
            if text.loading:
                if card.card_state() != "loading":
                    card.set_loading(True)
                continue
            if card.card_state() == "loading":
                card.set_loading(False)
            if text.value is None:
                card.set_empty(text.caption, link=text.link)
            else:
                card.set_value(text.value, text.tone)
                card.set_caption(text.caption)

    def card_text(self, key: str) -> CardText:
        """What a card says now, as the page worked it out (for tests)."""
        return {RESERVE: lambda: reserve_card(self._reading(RESERVE)),
                ROTATION: lambda: rotation_card(self._reading(ROTATION)),
                PLAYLIST: lambda: playlist_card(self._reading(PLAYLIST)),
                REVIEW: lambda: review_card(self._reading(REVIEW), self._now())}[key]()

    def _render_loop(self) -> None:
        now = self._now()
        rotator = self._running("rotator")
        texts = {"rotator": rotator_tile(rotator, self._reading(LAST_RUN), now),
                 "tracker": tracker_tile(self._reading(PLAYLIST), now),
                 "review": review_tile(self._running("review"), self._reading(REVIEW), now)}
        for key, text in texts.items():
            tile = self.tiles[key]
            tile.set_status(text.status, text.tone)
            tile.set_progress(text.fraction, text.bar_tone, indeterminate=text.indeterminate)
            tile.set_meta(text.meta)
            tile.set_active(text.active)
        self.loop_title.set_subtitle(loop_subtitle(rotator, self._reading(LAST_RUN), now))
        sentence = loop_sentence(self._reading(RESERVE))
        self.sentence.setText(sentence)
        self.sentence.setVisible(bool(sentence))
        self._rule.setVisible(bool(sentence))

    def _render_monitors(self) -> None:
        now = self._now()
        if self._fixture is not None:
            results = [_FixtureProgress(**m) for m in self._fixture.get("monitors", [])]
            authors = {p.current or "": p.author for p in results}
            resolutions = {p.monitor: p.resolution for p in results}
        else:
            results = list(getattr(self._feed, "results", None) or [])
            authors, resolutions = self._authors(results), self._resolutions()
        views = monitor_views(results, self._preferred(), now, authors=authors,
                              resolutions=resolutions)
        while len(self.monitor_cards) > len(views):
            card = self.monitor_cards.pop()
            self._monitor_column.removeWidget(card)
            card.deleteLater()
        while len(self.monitor_cards) < len(views):
            card = MonitorCard()
            self._monitor_column.insertWidget(len(self.monitor_cards), card)
            self.monitor_cards.append(card)
        for card, view in zip(self.monitor_cards, views):
            card.set_view(view, now)
        self.no_monitors.setVisible(not views)

    def _authors(self, results) -> dict[str, str]:
        """The authors known of what is on screen; the rest asked for on the
        reader's thread (project.json and Review's caches are on disk), and
        the cards drawn again when they come."""
        wanted = [p.current for p in results
                  if p.current and p.current not in self._described and p.current not in self._asked]
        if wanted:
            self._asked.update(wanted)
            self._reader.read_items(wanted)
        return {item: d.author for item, d in self._described.items() if d.author}

    def _items_described(self, found: dict) -> None:
        self._described.update(found)
        self._render_monitors()

    def _resolutions(self) -> dict[str, str]:
        """Each monitor's display size, worked out again only for another
        config.json or after the feed has looked again (displays come and go)."""
        config = getattr(getattr(self._feed, "files", None), "config", None)
        # Each look hands over a new results list: that is "after a look".
        key = (id(config), id(getattr(self._feed, "results", None)))
        if key != self._resolution_key:
            self._resolution_key = key
            try:
                self._resolution_cache = monitor_resolutions(config)
            except Exception:           # noqa: BLE001 — Windows' list of displays; left out
                self._resolution_cache = {}
        return self._resolution_cache

    # -- recent activity

    def _load_activity(self) -> None:
        if self._services is not None:
            self._entries = self._services.journal.recent(ACTIVITY_ROWS)
        self._entries_loaded = True
        self._render_activity()

    def _appended(self, entry) -> None:
        if not self._entries_loaded or self._fixture is not None:
            return
        self._entries = [entry, *self._entries][:ACTIVITY_ROWS]
        self._render_activity()

    def _render_activity(self) -> None:
        now = self._now()
        self.activity_model.clear()
        for entry in self._entries:
            item = QStandardItem()
            item.setData(activity_row(entry, now), LIST_ROW_ROLE)
            item.setData(entry.tool, _TOOL_ROLE)
            item.setEditable(False)
            self.activity_model.appendRow(item)
        self._activity_stack.setCurrentWidget(
            self.activity if self._entries else self.activity_empty)

    def _row_clicked(self, index: QModelIndex) -> None:
        tool = index.data(_TOOL_ROLE)
        if tool in TOOLS:
            self.navigate.emit(tool)

    def activity_rows(self) -> list[ListRow]:
        return [self.activity_model.item(r).data(LIST_ROW_ROLE)
                for r in range(self.activity_model.rowCount())]

    def _open_logs(self) -> None:
        if self._services is not None and self._fixture is None:
            self._services.logs.open_folder()

    # -- the log

    def _pick_log(self) -> None:
        """Follow the running job's log, or the file written last."""
        if self._services is None:
            return
        job = next((j for j in self._services.jobs.running() if j.log_path), None)
        if job is not None:
            path, live = Path(job.log_path), True
        else:
            files = self._services.logs.files(None)
            path, live = (files[0] if files else None), False
        if path != self._log_path:
            self._log_path = path
            self._log_tail = LogTail(path, LOG_LINES) if path is not None else None
            self.log.clear()
        self._name_log(f"{path.parent.name}/{path.name}" if path else None, live)
        self._read_log()
        if live and self.isVisible():
            self._log_timer.start()
        else:
            self._log_timer.stop()

    def _name_log(self, name: str | None, live: bool) -> None:
        """"Log", its dot pulsing while the job writes it, and the file
        ("rotator/2026-09-19.log") beside it, cut short in the narrow column
        and whole in the tool tip."""
        self.log.set_live(live)
        self.log.set_file(name, writing=False)
        self.log.setToolTip(("writing to " if live else "") + name if name else "")

    def _read_log(self) -> None:
        if self._log_tail is None:
            return
        lines = self._log_tail.read()
        if self._log_tail.restarted:
            self.log.clear()
        if lines:
            self.log.extend(lines)

    def _open_log_folder(self) -> None:
        if self._services is None or self._fixture is not None:
            return
        tool = self._log_path.parent.name if self._log_path is not None else None
        self._services.logs.open_folder(tool if tool in TOOLS else None)

    def _card_clicked(self, key: str) -> None:
        target = self._targets.get(key)
        if target:
            self.navigate.emit(target)

    # -- snapshots

    def load_fixture(self, state: str) -> None:
        """A made-up state from tests/fixtures/ui/overview.json: the snapshot's
        readings, the monitors, the journal's rows and the log's lines."""
        if state not in self.FIXTURES:
            super().load_fixture(state)
        data = load_overview_fixture()
        fixture = data["states"][state]
        now = datetime.fromisoformat(fixture.get("now", data["now"]))
        self._fixture = fixture
        self._minute.stop()
        self._log_timer.stop()
        self._now = lambda: now
        if self._services is not None:
            started = fixture.get("job_started")
            for job in self._services.jobs.running():
                if started:
                    job.started = datetime.fromisoformat(started)
            snapshot = self._services.snapshot
            for key in KEYS:
                if key in fixture:
                    snapshot.put(key, _fixture_value(key, fixture[key]))
                elif key in fixture.get("unset", {}):
                    snapshot.put(key, None, error=fixture["unset"][key],
                                 reason="unset")
        self._entries = [_fixture_entry(e, now) for e in fixture.get("activity", [])]
        self._entries_loaded = True
        log = fixture.get("log") or {}
        self.log.clear()
        self.log.extend(tuple(line) for line in log.get("lines", []))
        self._name_log(log.get("name"), bool(log.get("live")))
        self._tick()
        self._render()


# ---- fixtures ------------------------------------------------------------------------------

FIXTURE_FILE = (Path(__file__).resolve().parent.parent.parent
                / "tests" / "fixtures" / "ui" / "overview.json")


def load_overview_fixture() -> dict:
    """tests/fixtures/ui/overview.json, from a source checkout."""
    data = json.loads(FIXTURE_FILE.read_text(encoding="utf-8"))
    data.pop("//", None)
    return data


@dataclass(frozen=True)
class _FixtureProgress:
    """The fields of the tracker's Progress a monitor card reads."""
    monitor: str
    seen: int
    total: int
    current_title: str = ""
    current_since: str | None = None
    current: str | None = None
    live: bool = True
    from_engine: bool = True
    anchor: str = ""
    from_rotation: bool = False
    resolution: str = ""                # what the live page reads from Windows
    author: str = ""                    # and from the wallpaper's metadata


def _fixture_value(key: str, value):
    if value is None:
        return None
    if key == RESERVE:
        return ReserveCounts(**value)
    if key == ROTATION:
        return RotationCounts(**value)
    if key == LAST_RUN:
        value = dict(value)
        for field in ("started", "finished"):
            if value.get(field):
                value[field] = datetime.fromisoformat(value[field])
        return RunSummary(**value)
    if key == PLAYLIST:
        return PlaylistProgress(**value)
    return value


def _fixture_entry(spec: dict, now: datetime):
    from ..services.activity import Entry
    return Entry(now - timedelta(minutes=spec["minutes_ago"]), spec["tool"], spec["kind"],
                 spec["title"], spec.get("detail", ""), spec.get("chip"), spec.get("run"))
