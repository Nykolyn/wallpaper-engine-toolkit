"""Review: what is new from the authors behind the wallpapers you put aside.

One flow (REDESIGN_PLAN §6.4): **scan → go through the authors → finish.**
What the page shows, by state:

- **empty** — nothing scanned since the last review: what a scan does (it
  reads the authors of what is in the chosen Wallpaper Engine folder, and
  changes nothing on disk), "Scan for new items", and the last scan from
  `review_last.json`. The keyless banner, while there is no Steam key.
- **scanning** — the scan as it goes (`engines/review_flow.ScanFlow` on a
  thread): which stage, "34 / 118 authors checked · ≈70 s left" from the live
  rate, the author being checked, "Cancel scan"; the authors with new items
  as they are found; skeleton rows where the author list will be.
- **stopped** — the scan stopped: Steam did not answer, the key was refused,
  the database would not open — said in plain words, with the last log lines.
  "Carry on from author 34" counts the authors not reached, keeping the rest
  (§6.4.2); "Start over" scans again. A cancel lands here too, quieter.
- **reviewing** — the authors with new items on the left (done ticks, "3 /
  12"), an author's gallery on the right (`app/ui/gallery.py`: a grid of
  cards or a list, Grid / List remembered), the bar under it ("3 selected",
  "Subscribe selected", "Subscribe page", the pages, "Done with <author> →"),
  and in the header "Skip for now" and "Finish review" — which writes the
  visit dates after showing the plan.
- **done** — the review written: how many authors went through, what was
  subscribed, the numbers, "Open review as a list" (every author's wallpapers
  under their names, in the gallery's list) and "Reopen review".

The session — the authors with new items, which are done, what was
subscribed — is `review_flow.Session`, written to `data/review_last.json`
when a scan finishes, when an author is done and at the finish. Overview,
the sidebar's badge and the next start of this page read it back. "Skip for
now" leaves the session in memory for later; galleries are not kept between
starts of the app.

Nothing here touches Steam, the libraries or Wallpaper Engine's folders on
the window's thread: the scan, a subscription, the look for subscriptions
made elsewhere, the folder counts for the settings and the write to the
authors database all run on threads. Only `review_last.json` and the secrets
file, both small and in the data folder, are read here — never in the
constructor.

The words come from plain functions (`empty_text`, `ScanProgress`,
`stopped_text`, `author_row`, `found_row`, `finish_plan`, `done_text`, the
subtitles and `nav_state`), which tests call without building a widget.
"""
from __future__ import annotations

import queue
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from PySide6.QtCore import (
    QAbstractListModel, QCoreApplication, QModelIndex, QObject, Qt, QThread, QTimer, Signal,
)
from PySide6.QtGui import QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from .. import external, secrets, theme
from ..engines import review as rv
from ..engines.authors_store import AuthorsStore, StoreDamaged
from ..engines.review_flow import (
    CANCELLED, DONE, STOPPED, FlowEvent, ScanFlow, ScanOutcome, Session, SessionAuthor,
    load_last, save_last,
)
from ..engines.steam_api import workshop_url
from ..engines.steam_ugc import SteamUgc, UgcError
from ..services import begin
from ..services.snapshot import REVIEW, ReviewState
from ..settings import Settings, app_data_dir
from ..ui.gallery import SUBSCRIBING, WAITING, GalleryList, GalleryView, offered
from ..ui.kit import (
    AccentButton, Callout, ConfirmDialog, ConsoleExcerpt, Dropdown, EmptyState, GhostButton,
    GlassPanel, Group, IconButton, ListRow, MetricStrip, NavState, Pagination, ProgressBar,
    RowList, Rule, SecondaryButton, SegmentedControl, SkeletonRows, Spinner, TextInput,
    format as fmt, label,
)
from ..ui.kit import base
from ..ui.kit.base import Elided, set_tone
from ..ui.kit.tables import LIST_ROW_ROLE
from .base import Page
from .review_settings import (
    BY_PAGE, BY_STEAM, KEYLESS_HIDDEN, SECTION, WITHOUT_KEY, AuthorsDialog, ReviewSettingsDialog,
    has_key, mirror_folder, open_store, read_settings, save_settings, steamworks_note,
    stored_key_words,
)

STATES = ("empty", "scanning", "stopped", "reviewing", "done")
SORTS = (("Known first", rv.SORT_DEFAULT), ("Name", rv.SORT_NAME),
         ("Added to the folder", rv.SORT_APPEARED))
# an author's state → the chip on their row
STATE_CHIPS = {rv.NEW: "NewAuthor", rv.KNOWN: "Known", rv.DUPLICATE: "Duplicated",
               rv.UNKNOWN: "Unidentified"}
EXCERPT_LINES = 3


# ---- the words ------------------------------------------------------------------------------

def last_state(data: dict | None) -> ReviewState | None:
    return ReviewState.from_json(data) if isinstance(data, dict) else None


def last_meta(last: ReviewState | None, now: datetime) -> str:
    """"last scan Fri 09:10 · 12 authors had new items": the empty and the
    finished states' line (gate G4 A: no scheduled scan, so no "next scan")."""
    if last is None or last.scanned is None:
        return ""
    words = f"last scan {fmt.date_activity(last.scanned, now)}"
    if last.authors:
        words += f" · {fmt.counted(last.authors, 'author')} had new items"
    elif last.items == 0:
        words += " · nothing new"
    return words


def empty_subtitle(last: ReviewState | None, now: datetime) -> str:
    if last is None or last.scanned is None:
        return "No review yet"
    if last.finished is not None:
        return f"Last review {fmt.day(last.finished, now)}"
    return f"Last scan {fmt.date_activity(last.scanned, now)} · not finished"


def empty_text(last: ReviewState | None, scope: str, now: datetime,
               seconds: float | None = None) -> tuple[str, str, str]:
    """(title, body, meta) of the empty state. What a scan does is said as it
    is (§7.1): it reads the authors of what is in a Wallpaper Engine folder,
    not "the authors you follow", and it changes nothing on disk."""
    what = (f"A scan reads who made each wallpaper in {rv.scope_label(scope)}, then asks "
            "Steam what each of those authors has published since your last visit. It "
            "changes nothing on disk.")
    if seconds:
        what += f" The last one took {fmt.duration(seconds, exact=False)}."
    if last is None or last.scanned is None:
        return "Nothing scanned yet", what, ""
    if last.finished is None and last.authors:
        return ("The last scan was not gone through",
                f"It found {fmt.counted(last.authors, 'author')} with new items, but the "
                "review was not finished, and galleries are not kept from one start of the "
                "toolkit to the next. Nothing was written: scan again to go through them.",
                last_meta(last, now))
    return "Nothing scanned since the last review", what, last_meta(last, now)


def scanning_subtitle(total: int | None, scope: str) -> str:
    if total:
        return f"Scanning {fmt.counted(total, 'author')}"
    return f"Scanning {rv.scope_label(scope)}"


def reviewing_subtitle(session: Session, now: datetime) -> str:
    authors = fmt.counted(len(session.authors), "author")
    if not session.authors:
        return f"Nothing new · {fmt.counted(session.checked, 'author')} checked"
    if session.since is not None:
        return f"New since {fmt.day(session.since, now)} · {authors} with new items"
    return f"{authors} with new items"


def stopped_subtitle(when: datetime) -> str:
    return f"Scan stopped at {fmt.clock(when)}"


def done_subtitle(session: Session) -> str:
    finished = session.finished or session.scanned
    return (f"Finished {fmt.clock(finished)} · "
            f"{fmt.counted(session.done_count, 'author')} gone through")


@dataclass
class ScanProgress:
    """What the scan panel says, from the flow's events: the stage, the
    numbers, the author being checked, and the authors with new items found
    so far (in the order they were found)."""

    scope: str
    started: datetime
    phase: str = "prepare"          # prepare, scan, count, owned
    words: str = "getting ready"
    stage: str = ""                 # items, authors (the scan's numbered stages)
    done: int = 0
    total: int = 0
    checked: int = 0
    authors: int | None = None
    current: str = ""
    found: list = field(default_factory=list)   # (id, card), with new items

    def event(self, e: FlowEvent) -> None:
        if e.kind == "step":
            self.words = e.text
            if self.phase == "prepare" and e.text.startswith("reading "):
                self.phase = "scan"
        elif e.kind == "progress":
            self.phase, self.stage, self.done, self.total = "scan", e.text, e.done, e.total
        elif e.kind == "found":
            self.phase, self.authors, self.checked = "count", e.total, e.done
        elif e.kind == "checking":
            self.current = e.text
        elif e.kind == "checked":
            self.checked = max(self.checked, e.done)
            card = e.card
            if card is not None and not card.error and card.badge and \
                    all(found_id != card.id64 for found_id, _ in self.found):
                self.found.append((card.id64, card))
        elif e.kind == "owned":
            self.phase, self.words = "owned", e.text

    def counting(self) -> bool:
        return self.phase in ("count", "owned") and self.authors is not None

    def figure(self, left: float | None = None) -> tuple[str, str, str]:
        """(count, "/ total", caption) — the panel's headline."""
        if self.counting():
            caption = "authors checked"
            if left is not None and self.checked < (self.authors or 0):
                caption += f" · {fmt.left(left)}"
            return fmt.count(self.checked), f"/ {fmt.count(self.authors)}", caption
        if self.stage and self.total:
            noun = "wallpapers described" if self.stage == "items" else "authors named"
            return fmt.count(self.done), f"/ {fmt.count(self.total)}", noun
        return fmt.DASH, "", self.words

    def bar(self) -> tuple[int, int] | None:
        """(done, total) for the bar; None while there is nothing to count."""
        if self.counting():
            return self.checked, max(1, self.authors or 0)
        if self.stage and self.total:
            return self.done, self.total
        return None

    def activity(self) -> str:
        if self.phase == "count":
            return f"checking {self.current}" if self.current else "counting what is new"
        return self.words

    def status_text(self) -> str:
        """What the status line says after "Review ·"."""
        if self.counting():
            return f"scanning {fmt.ratio(self.checked, self.authors or 0, 'prose')} authors"
        if self.stage == "items" and self.total:
            return f"describing {fmt.ratio(self.done, self.total, 'prose')} wallpapers"
        if self.stage == "authors" and self.total:
            return f"naming {fmt.ratio(self.done, self.total, 'prose')} authors"
        return self.words

    def count_text(self) -> str | None:
        if self.counting():
            return fmt.ratio(self.checked, self.authors or 0)
        return None

    def found_items(self) -> int:
        return sum(card.badge for _, card in self.found)


def found_row(card) -> ListRow:
    """A row of "Found so far": the author, NEW AUTHOR when they are, and
    how much is new."""
    new = f"{fmt.count(card.badge)} new"
    if card.state == rv.NEW:
        return ListRow(card.name, thumb=True, thumb_size=theme.REVIEW_AVATAR,
                       chips=(("NewAuthor", None),), trailing=f"{new} — first time seen")
    return ListRow(card.name, thumb=True, thumb_size=theme.REVIEW_AVATAR, trailing=new)


def author_meta(author: SessionAuthor, card=None, selected: int = 0) -> tuple[str, str]:
    """(words, tone) under an author's name: "14 new · 3 subscribed", and
    "3 selected" while some of their wallpapers are."""
    bits = [f"{fmt.count(author.new)} new"]
    if author.subscribed:
        bits.append(f"{fmt.count(len(author.subscribed))} subscribed")
    if selected:
        bits.append(f"{fmt.count(selected)} selected")
    if author.state == rv.NEW:
        bits.append("first time seen")
    if author.incomplete:
        bits.append("list incomplete")
        return " · ".join(bits), "warn"
    return " · ".join(bits), "text.lo"


def author_row(author: SessionAuthor, card=None, selected: int = 0) -> ListRow:
    """An author in the list: their name (and the name the database still
    has for them), the counts, their chip, and a tick once gone through."""
    words, tone = author_meta(author, card, selected)
    old = card.database_name if card is not None else None
    return ListRow(author.name, meta=words, meta_tone=tone, thumb=True,
                   thumb_size=theme.REVIEW_AVATAR, title_note=f"was {old}" if old else "",
                   chips=((STATE_CHIPS.get(author.state, "Known"), None),), chips_inline=True,
                   tick=author.done, dimmed=author.done)


def _short(name: str) -> str:
    """An author's name short enough for "Done with <name> →"."""
    limit = theme.REVIEW_DONE_NAME
    return name if len(name) <= limit else name[:limit - 1].rstrip() + "…"


def list_foot(session: Session) -> str:
    """"106 authors had nothing new" under the list."""
    bits = []
    if session.nothing_new:
        bits.append(f"{fmt.counted(session.nothing_new, 'author')} had nothing new")
    if session.unread:
        bits.append(f"{fmt.count(session.unread)} could not be read")
    return " · ".join(bits)


def gallery_subtitle(author: SessionAuthor, card, now: datetime) -> str:
    """Under an author's name over their gallery."""
    since = card.visited if card is not None and author.state in (rv.KNOWN, rv.DUPLICATE) else None
    bits = [f"{fmt.count(author.new)} new since {fmt.day(since, now)}" if since
            else f"{fmt.count(author.new)} new — first time seen" if author.state == rv.NEW
            else f"{fmt.count(author.new)} new"]
    if author.have:
        bits.append(f"{fmt.count(author.have)} already had")
    if author.yours:
        bits.append(f"{fmt.count(author.yours)} {'was' if author.yours == 1 else 'were'} "
                    "yours before")
    if author.subscribed:
        bits.append(f"{fmt.count(len(author.subscribed))} subscribed")
    if card is not None and card.total:
        bits.append(f"{fmt.count(card.total)} published in all")
    if author.incomplete:
        bits.append("list incomplete: read without a Steam key, mature wallpapers left out")
    return " · ".join(bits)


@dataclass(frozen=True)
class StoppedText:
    title: str
    body: str
    tone: str           # "danger" for an error, "neutral" for a cancel
    icon: str
    carry: str          # the carry-on button's words, "" when there is nothing to carry on
    restore: bool       # the database is damaged: offer the backups


def stopped_text(outcome: ScanOutcome) -> StoppedText:
    """What the stopped state says about how the scan ended."""
    error = outcome.state == STOPPED
    damaged = isinstance(outcome.error, StoreDamaged)
    if damaged:
        return StoppedText("The authors database is damaged",
                           f"{outcome.reason} Restore the newest backup to go on: the damaged "
                           "file is set aside, not deleted.", "danger", "warn", "", True)
    if outcome.can_carry_on:
        at = outcome.stopped_at + 1
        result = outcome.result
        with_new = sum(1 for c in result.cards if c.filled and not c.error and c.badge) \
            if result is not None else 0
        kept = (f"the {fmt.counted(outcome.checked, 'author')} already checked are kept"
                + (f" — {fmt.count(with_new)} of them with new items —" if with_new else "")
                + " and the scan can carry on from where it stopped.")
        title = (f"The scan stopped at author {fmt.count(at)} of {fmt.count(outcome.total)}"
                 if error else
                 f"The scan was stopped at author {fmt.count(at)} of {fmt.count(outcome.total)}")
        lead = outcome.reason if error else "Stopped on request."
        return StoppedText(title, f"{lead} Nothing was changed — {kept}",
                           "danger" if error else "neutral", "warn" if error else "stop",
                           f"Carry on from author {fmt.count(at)}", False)
    if not error:
        return StoppedText("The scan was stopped", "Stopped on request, before any author was "
                           "checked. Nothing was changed.", "neutral", "stop", "", False)
    where = ("before it found the authors" if outcome.phase in ("prepare", "scan")
             else "before it could count the authors")
    return StoppedText(f"The scan stopped {where}", f"{outcome.reason} Nothing was changed.",
                       "danger", "warn", "", False)


def finish_plan(changes, blind: int) -> tuple[str, str, list[str], tuple[str, str] | None]:
    """(title, body, lines, note) of the Finish review confirmation: the plan
    first (§6.4.5), and the keyless warning when a visit date would be moved
    from a list Steam cut short."""
    created = sum(1 for c in changes if c.kind == "create")
    renamed = sum(1 for c in changes if c.kind == "update" and "name" in c.fields)
    visited = sum(1 for c in changes if c.kind == "update" and "visited" in c.fields)
    body = (f"{fmt.counted(created, 'author')} to create, "
            f"{fmt.counted(visited, 'visit date')} to move, "
            f"{fmt.counted(renamed, 'name')} to bring up to date. It is written in one go — "
            "all of it or none — and backed up straight after.")
    note = None
    if blind:
        note = ("warn",
                f"{fmt.count(blind)} of these come from lists read without a Steam key. Steam "
                "leaves mature wallpapers out of those, and moving the visit date past them "
                "means they will not be offered later — not even after a key is added.")
    return "Write the review to the authors database?", body, \
        [c.describe() for c in changes], note


def written_blind(result: rv.ReviewResult | None, changes) -> int:
    """How many of these would set a visit date from an incomplete list."""
    if result is None:
        return 0
    blind = {c.id64 for c in result.incomplete}
    if not blind:
        return 0
    owner = {id(r): c.id64 for c in result.cards for r in c.records}
    count = 0
    for change in changes:
        if change.kind == "create":
            count += change.fields.get("key") in blind
        elif change.kind == "update" and "visited" in change.fields:
            count += owner.get(id(change.author)) in blind
    return count


def written_line(report: dict, mirror: bool) -> str:
    """"3 created, 23 updated. Backup: authors-….json.gz"."""
    line = f"{fmt.count(report.get('created', 0))} created, " \
           f"{fmt.count(report.get('updated', 0))} updated."
    backup = report.get("backup")
    if backup:
        line += f" Backup: {Path(backup).name}" + (" (and in the second folder)." if mirror else ".")
    return line


@dataclass(frozen=True)
class DoneText:
    sentence: str
    metrics: tuple          # MetricStrip items
    foot: str


def done_text(session: Session, now: datetime) -> DoneText:
    total, done = len(session.authors), session.done_count
    if not total:
        first = "No author had anything new."
    elif done == total:
        first = f"All {fmt.counted(total, 'author')} went through."
    else:
        first = f"{fmt.count(done)} of {fmt.counted(total, 'author')} went through."
    n = session.subscribed
    second = (f"{fmt.counted(n, 'wallpaper')} {'was' if n == 1 else 'were'} subscribed and "
              f"{'is' if n == 1 else 'are'} downloading in Steam." if n
              else "Nothing was subscribed.")
    written = session.written or {}
    if written.get("created") or written.get("updated"):
        third = (f"{fmt.counted(written.get('created', 0), 'author')} added to the authors "
                 f"database and {fmt.count(written.get('updated', 0))} brought up to date.")
    else:
        third = "Nothing needed writing to the authors database."
    metrics = ((session.items, "new items found"), (n, "subscribed", "info"),
               (session.have, "already had", "warn"), (session.yours, "were yours", "ok"))
    finished = session.finished or now
    foot = f"finished {fmt.date_activity(finished, now)}"
    if written.get("backup"):
        foot += f" · backup {Path(written['backup']).name}"
    return DoneText(f"{first} {second} {third}", metrics, foot)


def nav_state(state: str, *, session: Session | None = None, last: ReviewState | None = None,
              scan: ScanProgress | None = None, error: bool = False) -> NavState:
    """The sidebar's Review item: the scan's bar, the authors waiting as a
    badge, or a word."""
    if state == "scanning":
        if scan is not None and scan.counting():
            return NavState.progress(scan.checked, max(1, scan.authors or 0))
        return NavState.status("scanning", below=True)
    if state == "stopped":
        return NavState.status("stopped", "danger" if error else "warn", below=True)
    if state == "reviewing" and session is not None:
        return NavState.badge(session.waiting) if session.waiting else \
            NavState.status("gone through", "ok", below=True)
    if state == "done":
        return NavState.status("finished", "ok", below=True)
    if last is not None and last.finished is None and last.waiting:
        return NavState.badge(last.waiting)
    return NavState()


# ---- the author lists -----------------------------------------------------------------------

class AuthorModel(QAbstractListModel):
    """ListRows by author id, in the order given."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ids: list[str] = []
        self._rows: dict[str, ListRow] = {}

    def rowCount(self, parent=QModelIndex()) -> int:     # noqa: N802 - Qt's name
        return 0 if parent.isValid() else len(self._ids)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or index.row() >= len(self._ids):
            return None
        row = self._rows[self._ids[index.row()]]
        if role == LIST_ROW_ROLE:
            return row
        if role in (Qt.DisplayRole, Qt.AccessibleTextRole):
            return row.title
        if role == Qt.ToolTipRole:
            return " · ".join(bit for bit in (row.title, row.title_note, row.meta) if bit)
        return None

    def list_item(self, row: int) -> tuple:
        """What RowList paints for a row, without a call into Qt for it."""
        return self._rows[self._ids[row]], None, None

    def set_rows(self, rows: list[tuple[str, ListRow]]) -> None:
        self.beginResetModel()
        self._ids = [author_id for author_id, _ in rows]
        self._rows = dict(rows)
        self.endResetModel()

    def append(self, author_id: str, row: ListRow) -> None:
        if author_id in self._rows:
            self.update(author_id, row)
            return
        at = len(self._ids)
        self.beginInsertRows(QModelIndex(), at, at)
        self._ids.append(author_id)
        self._rows[author_id] = row
        self.endInsertRows()

    def update(self, author_id: str, row: ListRow) -> None:
        if author_id not in self._rows:
            return
        self._rows[author_id] = row
        at = self._ids.index(author_id)
        self.dataChanged.emit(self.index(at), self.index(at))

    def ids(self) -> list[str]:
        return list(self._ids)

    def row(self, author_id: str) -> ListRow | None:
        return self._rows.get(author_id)

    def id_at(self, row: int) -> str | None:
        return self._ids[row] if 0 <= row < len(self._ids) else None

    def row_of(self, author_id: str) -> int | None:
        return self._ids.index(author_id) if author_id in self._rows else None


class AuthorList(RowList):
    """The list of authors with new items. A click opens one at once; the
    arrow keys open one once the selection comes to rest, so holding a key
    down through twenty authors does not open twenty galleries."""

    chosen = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.model_ = AuthorModel(self)
        self.setModel(self.model_)
        self.setAccessibleName("Authors with new items")
        self._restoring = False
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.setInterval(theme.REVIEW_SETTLE_MS)
        self._settle.timeout.connect(self._choose_current)
        self.clicked.connect(self._clicked)
        self.selectionModel().currentChanged.connect(self._moved)

    def _clicked(self, index) -> None:
        self._settle.stop()
        author_id = self.model_.id_at(index.row())
        if author_id:
            self.chosen.emit(author_id)

    def _moved(self, current, _previous) -> None:
        if self._restoring or not current.isValid():
            return
        self._settle.start()

    def _choose_current(self) -> None:
        author_id = self.model_.id_at(self.currentIndex().row())
        if author_id:
            self.chosen.emit(author_id)

    def select(self, author_id: str | None) -> None:
        """Put the highlight on an author without opening them again."""
        row = self.model_.row_of(author_id) if author_id else None
        self._restoring = True
        try:
            if row is None:
                self.clearSelection()
            else:
                self.setCurrentIndex(self.model_.index(row))
        finally:
            self._restoring = False


# ---- the panels -------------------------------------------------------------------------------

def _column(widget: QWidget, margins=(0, 0, 0, 0), spacing: int = 0) -> QVBoxLayout:
    column = QVBoxLayout(widget)
    column.setContentsMargins(*margins)
    column.setSpacing(spacing)
    return column


class _Strip(QWidget):
    """A panel's head or foot strip on its ground (the footer's for a foot),
    a surface for the buttons on it."""

    def __init__(self, foot: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self._foot = foot
        base.declare(self)

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        painter = QPainter(self)
        if self._foot:
            painter.fillRect(self.rect(), theme.color("surface.footer"))
        base.paint(painter, self, event.rect())


class _AuthorPanel(GlassPanel):
    """The left panel: the authors, their progress, filter and sort."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, padding="none")
        self.setFixedWidth(theme.REVIEW_SIDE)
        column = _column(self)
        head = QWidget()
        pad_v, pad_h = theme.REVIEW_HEAD_PAD
        inner = _column(head, (pad_h, pad_v, pad_h, pad_v), theme.REVIEW_HEAD_GAP)
        top = QHBoxLayout()
        top.setSpacing(theme.SP_6)
        self.title = label("Authors", "type.body", "body")
        self.count = label("", "type.mono", "lo")
        top.addWidget(self.title)
        top.addStretch(1)
        top.addWidget(self.count)
        inner.addLayout(top)
        self.sub = Elided("", "type.monoXs", "lo")
        self.sub.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        inner.addWidget(self.sub)
        self.bar = ProgressBar(height=4)
        inner.addWidget(self.bar)
        self.filter = TextInput(placeholder="Filter by author…", search=True)
        self.filter.setAccessibleName("Filter the authors")
        inner.addWidget(self.filter)
        sort = QHBoxLayout()
        sort.setSpacing(theme.SP_6)
        self.sort = Dropdown(prefix="Sort")
        self.sort.setAccessibleName("Sort the authors")
        for text, key in SORTS:
            self.sort.add_item(text, key)
        self.direction = IconButton("sortUp", "Ascending — click for descending")
        sort.addWidget(self.sort, 1)
        sort.addWidget(self.direction)
        self.sort_row = QWidget()
        self.sort_row.setLayout(sort)
        sort.setContentsMargins(0, 0, 0, 0)
        inner.addWidget(self.sort_row)
        column.addWidget(head)
        column.addWidget(Rule())

        self.stack = QStackedWidget()
        words = QWidget()
        words_column = _column(words, (theme.SP_20, theme.SP_20, theme.SP_20, theme.SP_20))
        self.words = label("The list fills in\nonce a scan runs", "type.label", "lo")
        self.words.setAlignment(Qt.AlignCenter)
        self.words.setWordWrap(True)
        words_column.addStretch(1)
        words_column.addWidget(self.words)
        words_column.addStretch(1)
        self.skeleton = SkeletonRows(live=False)
        skeleton_holder = QWidget()
        _column(skeleton_holder, (theme.REVIEW_LIST_PAD + 2,) * 4).addWidget(self.skeleton)
        self.list = AuthorList()
        list_holder = QWidget()
        _column(list_holder, (theme.REVIEW_LIST_PAD,) * 4).addWidget(self.list)
        for page in (words, skeleton_holder, list_holder):
            self.stack.addWidget(page)
        column.addWidget(self.stack, 1)
        self.foot_rule = Rule()
        column.addWidget(self.foot_rule)
        self.foot = Elided("", "type.monoXs", "lo")
        self.foot.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        foot_holder = QWidget()
        pad_v, pad_h = theme.REVIEW_FOOT_PAD
        _column(foot_holder, (pad_h, pad_v, pad_h, pad_v)).addWidget(self.foot)
        self.foot_holder = foot_holder
        column.addWidget(foot_holder)

    def show_words(self, title: str, sub: str, words: str, foot: str = "") -> None:
        self._head(title, "", "", None)
        self.sub.set_text(sub)
        self.sub.setVisible(bool(sub))
        self.words.setText(words)
        self.stack.setCurrentIndex(0)
        self.skeleton.set_live(False)
        self.set_foot(foot)
        set_tone(self.title, "body")

    def show_skeleton(self, count: str, bar: tuple[int, int] | None, *, live: bool,
                      sub: str = "") -> None:
        """Placeholder rows: shimmering while a scan fills the list, still and
        set back once it stopped."""
        self._head("Authors", count, "accent", bar)
        self.sub.set_text(sub)
        self.sub.setVisible(bool(sub))
        self.skeleton.set_live(live)
        self.stack.setCurrentIndex(1)
        self.set_foot("")
        set_tone(self.title, "body" if live else "lo")

    def show_list(self, done: int, total: int, foot: str) -> None:
        tone = "ok" if total and done == total else "warn"
        self._head("Authors with new items", fmt.ratio(done, total), tone,
                   (done, max(1, total)))
        self.sub.hide()
        self.skeleton.set_live(False)
        self.stack.setCurrentIndex(2)
        self.set_foot(foot)
        set_tone(self.title, "body")

    def _head(self, title: str, count: str, tone: str, bar: tuple[int, int] | None) -> None:
        self.title.setText(title)
        self.count.setText(count)
        set_tone(self.count, {"ok": "ok", "warn": "warn"}.get(tone, "lo"))
        self.bar.setVisible(bar is not None)
        if bar is not None:
            self.bar.set_tone({"ok": "ok", "warn": "warn"}.get(tone, "accent"))
            self.bar.set_value(*bar)
        listing = tone in ("ok", "warn")
        self.filter.setVisible(listing)
        self.sort_row.setVisible(listing)

    def set_foot(self, text: str) -> None:
        self.foot.set_text(text)
        self.foot_rule.setVisible(bool(text))
        self.foot_holder.setVisible(bool(text))

    def set_bar(self, bar: tuple[int, int] | None, count: str) -> None:
        self.count.setText(count)
        if bar is None:
            self.bar.set_state("indeterminate")
        else:
            self.bar.set_state("determinate")
            self.bar.set_value(*bar)


class _ScanPanel(GlassPanel):
    """The scan under way: the stage, the count, the bar, the author being
    checked and "Cancel scan"."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, padding="none")
        pad_v, pad_h = theme.REVIEW_SCAN_PAD
        column = _column(self, (pad_h, pad_v, pad_h, pad_v), theme.REVIEW_SCAN_GAP)
        top = QHBoxLayout()
        top.setSpacing(theme.SP_10)
        top.addWidget(Spinner(size=theme.REVIEW_SPINNER))
        top.addWidget(label("Scanning the Workshop", "type.h3", "hi"), 1)
        self.started = label("", "type.monoSm", "lo")
        top.addWidget(self.started)
        column.addLayout(top)
        figure = QHBoxLayout()
        figure.setSpacing(theme.REVIEW_FIGURE_GAP)
        number = QHBoxLayout()
        number.setSpacing(theme.REVIEW_TOTAL_GAP)
        self.count = label("", "type.numeric", "hi")
        self.total = label("", "type.metric", "lo")
        self.caption = label("", "type.bodySm", "mid")
        number.addWidget(self.count, 0, Qt.AlignBottom)
        number.addWidget(self.total, 0, Qt.AlignBottom)
        figure.addLayout(number)
        figure.addWidget(self.caption, 1, Qt.AlignBottom)
        column.addLayout(figure)
        self.bar = ProgressBar(height=8)
        column.addWidget(self.bar)
        self.row = _Activity()
        column.addWidget(self.row)
        self.cancel = self.row.cancel

    def render(self, scan: ScanProgress, left: float | None) -> None:
        count, total, caption = scan.figure(left)
        self.started.setText(f"started {fmt.clock(scan.started)}")
        self.count.setText(count)
        self.total.setText(total)
        self.total.setVisible(bool(total))
        self.caption.setText(caption)
        bar = scan.bar()
        if bar is None:
            self.bar.set_state("indeterminate")
        else:
            self.bar.set_state("determinate")
            self.bar.set_value(*bar)
        self.row.text.set_text(scan.activity())

    def texts(self) -> dict:
        return {"count": self.count.text(), "total": self.total.text(),
                "caption": self.caption.text(), "activity": self.row.text.text(),
                "started": self.started.text()}


class _Activity(QWidget):
    """The author being checked, on a faint ground, with "Cancel scan"."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        base.declare(self)
        row = QHBoxLayout(self)
        pad_v, pad_h = theme.ACTIVITY_PAD
        row.setContentsMargins(pad_h, pad_v - 2, pad_h - 4, pad_v - 2)
        row.setSpacing(theme.ACTIVITY_GAP)
        self.text = Elided("", "type.monoSm", "mid")
        self.text.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.cancel = GhostButton("Cancel scan", size="sm")
        row.addWidget(self.text, 1)
        row.addWidget(self.cancel)

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(theme.color("surface.header"))
        painter.drawRoundedRect(self.rect(), theme.R_ROW, theme.R_ROW)
        base.paint(painter, self, event.rect())


class _FoundPanel(GlassPanel):
    """"Found so far": the authors with new items, as the scan finds them."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, padding="none")
        column = _column(self)
        head = QHBoxLayout()
        pad_v, pad_h = theme.REVIEW_FOUND_HEAD_PAD
        head.setContentsMargins(pad_h, pad_v, pad_h, pad_v)
        head.addWidget(label("Found so far", "type.h3", "hi"))
        head.addStretch(1)
        self.count = label("", "type.mono", "warn")
        head.addWidget(self.count)
        column.addLayout(head)
        column.addWidget(Rule())
        self.list = RowList()
        self.list.setAccessibleName("Authors with new items found so far")
        self.model = AuthorModel(self.list)
        self.list.setModel(self.model)
        self.list.setSelectionMode(QAbstractItemView.NoSelection)
        holder = QWidget()
        _column(holder, (theme.REVIEW_LIST_PAD + 2,) * 4).addWidget(self.list)
        column.addWidget(holder, 1)
        column.addWidget(Rule())
        foot = QWidget()
        pad_v, pad_h = theme.REVIEW_FOOT_PAD
        _column(foot, (pad_h + 1, pad_v, pad_h + 1, pad_v)).addWidget(
            label("the gallery opens when the scan finishes", "type.monoXs", "lo"))
        column.addWidget(foot)

    def render(self, scan: ScanProgress) -> None:
        have = set(self.model.ids())
        for author_id, card in scan.found:
            if author_id not in have:
                self.model.append(author_id, found_row(card))
        found = len(scan.found)
        self.count.setText(f"{fmt.counted(found, 'author')} · {fmt.counted(scan.found_items(), 'item')}"
                           if found else "")

    def clear(self) -> None:
        self.model.set_rows([])
        self.count.setText("")


VIEWS = ("grid", "list")


class _GalleryPanel(GlassPanel):
    """An author's gallery: who and what is new, Grid / List, the cards or the
    table, and the bar under them — what is selected and what to do with it,
    the pages, and "Done with <author> →"."""

    def __init__(self, gallery: GalleryView, listing: GalleryList,
                 parent: QWidget | None = None):
        super().__init__(parent, padding="none")
        column = _column(self)
        head = _Strip()
        pad_v, pad_h = theme.REVIEW_GALLERY_HEAD_PAD
        row = QHBoxLayout(head)
        row.setContentsMargins(pad_h, pad_v, pad_h, pad_v)
        row.setSpacing(theme.SP_12)
        words = QVBoxLayout()
        words.setSpacing(theme.SP_2)
        self.name = Elided("", "type.h3", "hi")
        self.name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.sub = Elided("", "type.monoXs", "lo")
        self.sub.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        words.addWidget(self.name)
        words.addWidget(self.sub)
        row.addLayout(words, 1)
        self.view = SegmentedControl(["Grid", "List"])
        self.view.setAccessibleName("Show the wallpapers as a grid or a list")
        row.addWidget(self.view)
        self.author_page = GhostButton("Open author page", icon="ext")
        row.addWidget(self.author_page)
        column.addWidget(head)
        column.addWidget(Rule())
        self.stack = QStackedWidget()
        self.stack.addWidget(gallery)
        holder = QWidget()
        pad_v, pad_h = theme.REVIEW_LIST_BODY_PAD
        _column(holder, (pad_h, pad_v, pad_h, pad_v)).addWidget(listing)
        self.stack.addWidget(holder)
        self.empty = EmptyState("", "", icon="review")
        self.stack.addWidget(self.empty)
        column.addWidget(self.stack, 1)
        column.addWidget(Rule())
        bar = _Strip()
        pad_v, pad_h = theme.REVIEW_BAR_PAD
        line = QHBoxLayout(bar)
        line.setContentsMargins(pad_h, pad_v, pad_h, pad_v)
        line.setSpacing(theme.SP_8)
        self.selected = label("", "type.bodySm", "body")
        line.addWidget(self.selected)
        self.subscribe_selected = SecondaryButton("Subscribe selected")
        line.addWidget(self.subscribe_selected)
        self.subscribe_page = SecondaryButton("Subscribe page")
        line.addWidget(self.subscribe_page)
        line.addStretch(1)
        self.pagination = Pagination()
        line.addWidget(self.pagination)
        self.back = SecondaryButton("Back to the summary")
        line.addWidget(self.back)
        self.done_with = AccentButton("Done →")
        line.addWidget(self.done_with)
        column.addWidget(bar)
        self.bar = bar
        self._mode = "grid"
        self._on = True

    def show_gallery(self, on: bool) -> None:
        self._on = on
        self.stack.setCurrentIndex(VIEWS.index(self._mode) if on else 2)
        self.bar.setVisible(on)
        self.author_page.setVisible(on)
        self.view.setVisible(on)

    def set_mode(self, mode: str) -> None:
        self._mode = mode if mode in VIEWS else "grid"
        if self.view.current_index() != VIEWS.index(self._mode):
            self.view.blockSignals(True)
            self.view.set_current_index(VIEWS.index(self._mode))
            self.view.blockSignals(False)
        if self._on:
            self.stack.setCurrentIndex(VIEWS.index(self._mode))

    def mode(self) -> str:
        return self._mode


class _Foot(_Strip):
    """The finished state's foot: when, and "Reopen review"."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(True, parent)
        row = QHBoxLayout(self)
        pad_v, pad_h = theme.REVIEW_BAR_PAD
        row.setContentsMargins(pad_h, pad_v, pad_h, pad_v)
        self.text = Elided("", "type.monoSm", "lo")
        self.text.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        row.addWidget(self.text, 1)
        self.reopen = GhostButton("Reopen review")
        row.addWidget(self.reopen)


# ---- work off the window's thread -----------------------------------------------------------------

class SubscribeQueue(QThread):
    """Every subscription goes through one thread and one Steam connection.

    `SteamAPI_Shutdown` is process-wide, so two clicks close together, each
    with a thread of its own, could have one close the connection the other
    was still subscribing through. One queue makes the calls sequential; it
    also opens Steam once for thirty wallpapers rather than thirty times, and
    closes it once the queue has been idle a moment — so Steam does not show
    Wallpaper Engine as running for longer than the work takes.
    """

    started_item = Signal(str)
    finished_item = Signal(str, str)      # id, what Steam now says about it
    failed_item = Signal(str, str)        # id, why not
    progress = Signal(int, int)           # done, asked for in this run

    IDLE_SECONDS = 1.5

    def __init__(self, parent=None, *, connect: Callable | None = None):
        super().__init__(parent)
        self._queue: queue.Queue[str] = queue.Queue()
        self._asked = 0
        self._done = 0
        self._connect = connect or (lambda: SteamUgc().connect())
        # Anything added in the instant the thread decided to stop is picked
        # up by starting it again.
        self.finished.connect(self._restart_if_needed)

    def add(self, item_ids) -> None:
        for item_id in item_ids:
            self._queue.put(str(item_id))
            self._asked += 1
        if not self.isRunning():
            self.start()

    def pending(self) -> int:
        return self._queue.qsize()

    def _restart_if_needed(self) -> None:
        if not self._queue.empty():
            self.start()
        else:
            self._asked = self._done = 0

    def run(self) -> None:
        ugc = None
        try:
            while True:
                try:
                    item_id = self._queue.get(timeout=self.IDLE_SECONDS)
                except queue.Empty:
                    return
                self.started_item.emit(item_id)
                try:
                    if ugc is None:
                        ugc = self._connect()
                    state = ugc.subscribe(item_id, wait=5)
                    self.finished_item.emit(item_id, state.describe())
                except Exception as err:  # noqa: BLE001 — report it, keep going
                    self.failed_item.emit(item_id, str(err))
                    if isinstance(err, UgcError) and ugc is None:
                        # Steam is not there at all; the rest would fail the
                        # same way, one slow timeout each.
                        while True:
                            try:
                                rest = self._queue.get_nowait()
                            except queue.Empty:
                                break
                            self.failed_item.emit(rest, str(err))
                            self._done += 1
                self._done += 1
                self.progress.emit(self._done, self._asked)
        finally:
            if ugc is not None:
                ugc.close()


class _Signals(QObject):
    """What threads hand back to the page."""
    flow_event = Signal(object)
    flow_ended = Signal(object)
    answer = Signal(str, object)
    progress = Signal(int, int)


def _thread(signals: _Signals, what: str, work: Callable[[], object]) -> None:
    """Run `work` on a thread; its result (or the exception it raised) comes
    back as `answer(what, result)`."""
    def run() -> None:
        try:
            found = work()
        except Exception as err:  # noqa: BLE001 — handed over, not raised on a thread
            found = err
        try:
            signals.answer.emit(what, found)
        except RuntimeError:
            pass                # the page went while the thread was working
    threading.Thread(target=run, daemon=True, name=f"review-{what}").start()


# ---- the page --------------------------------------------------------------------------------------

class ReviewPage(Page):
    key = "review"
    title = "Review"
    icon = "review"
    FIXTURES = ("empty", "scanning", "error", "reviewing", "gallery-grid", "gallery-list", "done",
                "review-list", "settings", "authors")

    def __init__(self, settings: Settings, services=None, parent: QWidget | None = None, *,
                 now: Callable[[], datetime] | None = None, data_dir: Path | str | None = None,
                 prepare: Callable | None = None, config_path: Path | str | None = None):
        super().__init__(parent)
        self.settings = settings
        self._services = services
        self._now = now or datetime.now
        self._data_dir = Path(data_dir) if data_dir else None
        self._prepare_fn = prepare
        self._config_path = config_path     # Wallpaper Engine's config.json; None finds it
        self.state = "empty"
        self.messages: list[tuple[str, str]] = []
        self.last_dialog: QWidget | None = None
        self.fixture_dialog: QWidget | None = None
        self._on_screen = False
        self._fixture = False
        self._fixture_left: float | None = None
        # what the engine needs, made on the scan's thread the first time
        self.db: AuthorsStore | None = None
        self.steam = None
        self.library = None
        self.review: rv.Review | None = None
        self._stale_client = False
        # the scan, the session, and where things stand
        self.flow: ScanFlow | None = None
        self.scan: ScanProgress | None = None
        self.outcome: ScanOutcome | None = None
        self.result: rv.ReviewResult | None = None
        self.session: Session | None = None
        self.cards: dict[str, rv.AuthorCard] = {}
        self.current: str | None = None
        self._owner: dict[str, str] = {}        # wallpaper id → author id
        self._last: ReviewState | None = None
        self._last_raw: dict | None = None
        self._stopped_at: datetime | None = None
        self._job = None
        self._job_text = ""
        self._log_tail: deque = deque(maxlen=EXCERPT_LINES)
        self._writing = False
        self._watching = False
        self._folders: dict[str, int] | None = None
        self._source_counts: dict[str, int] | None = None
        self._source_request = 0
        self._source_error = False
        self._status_subtitle = ""
        self._settings_dialog: ReviewSettingsDialog | None = None
        self.signals = _Signals(self)
        self.signals.flow_event.connect(self._flow_event)
        self.signals.flow_ended.connect(self._flow_ended)
        self.signals.answer.connect(self._answered)
        self.signals.progress.connect(self._write_progress)

        self.gallery = GalleryView()
        self.gallery_list = GalleryList(self.gallery)
        self._listing = False           # "Open review as a list": every author's wallpapers
        self._build()

        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(theme.REVIEW_RENDER_MS)
        self._render_timer.timeout.connect(self._render_scanning)
        self._watch = QTimer(self)
        self._watch.setInterval(theme.REVIEW_WATCH_MS)
        self._watch.timeout.connect(self._notice_subscriptions)

        self.subscriptions = SubscribeQueue(self)
        self.subscriptions.started_item.connect(lambda i: self.gallery.mark_busy(i, SUBSCRIBING))
        self.subscriptions.finished_item.connect(self._subscribed)
        self.subscriptions.failed_item.connect(self._subscribe_failed)

        if services is not None:
            services.snapshot.refreshed.connect(self._snapshot_refreshed)
        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._shutdown)
        self._set_state("empty")

    # -- building

    def _build(self) -> None:
        body = QHBoxLayout(self)
        pad_v, pad_h = theme.BODY_PAD
        body.setContentsMargins(pad_h, pad_v, pad_h, pad_v)
        body.setSpacing(theme.PANEL_GAP)
        self.authors = _AuthorPanel()
        body.addWidget(self.authors)
        self.right = QStackedWidget()
        body.addWidget(self.right, 1)

        # empty
        empty_page = QWidget()
        column = _column(empty_page, spacing=theme.PANEL_GAP)
        self.keyless = Callout(WITHOUT_KEY, tone="warn", title="No Steam Web API key")
        add_key = SecondaryButton("Add a key…", size="sm")
        add_key.clicked.connect(self.edit_settings)
        hide = GhostButton("Hide", size="sm")
        hide.setToolTip("Stop showing this. Authors counted without a key still say so, and "
                        "so does the confirmation before anything is written.")
        hide.clicked.connect(self._hide_keyless)
        self.keyless.add_action(add_key)
        self.keyless.add_action(hide)
        self.keyless.hide()
        column.addWidget(self.keyless)
        panel = GlassPanel(padding="none")
        self.empty = EmptyState("", "", icon="review")
        _column(panel, (theme.REVIEW_EMPTY_PAD,) * 4).addWidget(self.empty)
        self.scan_button = AccentButton("Scan for new items", size="lg")
        self.scan_button.clicked.connect(self.start_scan)
        self.empty.add_action(self.scan_button)
        column.addWidget(panel, 1)
        self.right.addWidget(empty_page)

        # scanning
        scanning = QWidget()
        column = _column(scanning, spacing=theme.PANEL_GAP)
        self.scan_panel = _ScanPanel()
        self.scan_panel.cancel.clicked.connect(self.cancel_scan)
        self.found = _FoundPanel()
        column.addWidget(self.scan_panel)
        column.addWidget(self.found, 1)
        self.right.addWidget(scanning)

        # stopped
        self.stopped_panel = GlassPanel(padding="none")
        self.stopped = EmptyState("", "", icon="warn", tone="danger",
                                  width=theme.REVIEW_STOPPED_WIDTH)
        self.excerpt = ConsoleExcerpt()
        self.stopped.add_content(self.excerpt)
        self.carry_button = AccentButton("Carry on")
        self.carry_button.clicked.connect(self.carry_on)
        self.restore_button = AccentButton("Open the backups…")
        self.restore_button.clicked.connect(self.edit_authors)
        self.again_button = AccentButton("Start over")
        self.again_button.clicked.connect(self.start_scan)
        self.over_button = SecondaryButton("Start over")
        self.over_button.clicked.connect(self.start_scan)
        self.log_button = GhostButton("Open log folder")
        self.log_button.clicked.connect(self.open_log_folder)
        for button in (self.carry_button, self.restore_button, self.again_button,
                       self.over_button, self.log_button):
            self.stopped.add_action(button)
        _column(self.stopped_panel, (theme.REVIEW_EMPTY_PAD,) * 4).addWidget(self.stopped)
        self.right.addWidget(self.stopped_panel)

        # reviewing
        self.gallery_panel = _GalleryPanel(self.gallery, self.gallery_list)
        g = self.gallery_panel
        g.author_page.clicked.connect(self.open_author_page)
        g.subscribe_page.clicked.connect(self.subscribe_page)
        g.subscribe_selected.clicked.connect(self.subscribe_selected)
        g.pagination.page_changed.connect(self.gallery.set_page)
        g.done_with.clicked.connect(self.done_with_author)
        g.back.clicked.connect(self.close_review_list)
        g.view.changed.connect(lambda i: self.set_view(VIEWS[i]))
        g.set_mode(self._stored_view())
        for view in (self.gallery, self.gallery_list):
            view.subscribe_requested.connect(self.subscribe)
            view.open_requested.connect(self.open_in_steam)
        self.gallery.page_changed.connect(self._page_changed)
        self.gallery.selection_changed.connect(self._selection_changed)
        # Esc lets go of the selection wherever the focus is in the panel
        clear = QShortcut(QKeySequence(Qt.Key_Escape), g)
        clear.setContext(Qt.WidgetWithChildrenShortcut)
        clear.activated.connect(self.gallery.clear_selection)
        self.right.addWidget(self.gallery_panel)

        # done
        self.done_panel = GlassPanel(tone="ok", padding="none")
        column = _column(self.done_panel)
        holder = QWidget()
        self.done = EmptyState("Review finished", "", icon="check", tone="ok",
                               width=theme.REVIEW_DONE_WIDTH)
        self.metrics = MetricStrip([(0, "new items found"), (0, "subscribed", "info"),
                                    (0, "already had", "warn"), (0, "were yours", "ok")])
        self.done.add_content(self.metrics)
        self.as_list = SecondaryButton("Open review as a list")
        self.as_list.setToolTip("Every author's new wallpapers in one list, under their names.")
        self.as_list.clicked.connect(self.open_review_list)
        self.done.add_action(self.as_list)
        _column(holder, (theme.REVIEW_DONE_PAD,) * 4).addWidget(self.done)
        column.addWidget(holder, 1)
        column.addWidget(Rule())
        self.done_foot = _Foot()
        self.done_foot.reopen.clicked.connect(self.reopen)
        column.addWidget(self.done_foot)
        self.right.addWidget(self.done_panel)

        a = self.authors
        a.list.chosen.connect(self.open_author)
        a.filter.textChanged.connect(lambda _t: self._fill_list())
        stored = self.settings.get(SECTION, "sort", rv.SORT_DEFAULT)
        keys = [key for _, key in SORTS]
        a.sort.setCurrentIndex(keys.index(stored) if stored in keys else 0)
        a.sort.activated.connect(lambda _row: self._sort_changed())
        self._descending = bool(self.settings.get(SECTION, "descending", False))
        a.direction.clicked.connect(self._flip_direction)
        self._show_direction()

    def make_header_actions(self) -> list[QWidget]:
        self.settings_button = GhostButton("Review settings", outlined=True)
        self.settings_button.clicked.connect(self.edit_settings)
        self.skip_button = SecondaryButton("Skip for now")
        self.skip_button.setToolTip("Leave the review as it is, without writing anything; "
                                    "it is here when you come back.")
        self.skip_button.clicked.connect(lambda: self.navigate.emit("overview"))
        self.finish_button = AccentButton("Finish review")
        self.finish_button.setToolTip("Write the visit dates to the authors database, after "
                                      "showing what will be written.")
        self.finish_button.clicked.connect(self.finish_review)
        self._render_header()
        return [self.settings_button, self.skip_button, self.finish_button]

    # -- on and off screen

    def on_shown(self) -> None:
        self._on_screen = True
        if self._fixture:
            return
        self.settings.reload_if_changed()
        self._refresh_source_counts()
        if self._last_raw is None:
            if self._services is not None:
                # what the snapshot already read, if it did before the page was made
                self._take_last(self._services.snapshot.get(REVIEW).value)
            else:
                self._take_last(load_last(self._last_path()))
        self._show_keyless()
        if self.state == "reviewing":
            self._watch.start()
        self._render()

    def on_hidden(self) -> None:
        self._on_screen = False
        self._watch.stop()

    def _shutdown(self) -> None:
        if self.flow is not None:
            self.flow.cancel()
        self._watch.stop()
        self.gallery.close_loader()

    # -- the last review

    def _last_path(self) -> Path:
        return (self._data_dir or app_data_dir()) / "review_last.json"

    def _snapshot_refreshed(self, keys) -> None:
        if REVIEW in keys and not self._fixture:
            self._take_last(self._services.snapshot.get(REVIEW).value)

    def _take_last(self, data) -> None:
        self._last_raw = data if isinstance(data, dict) else None
        self._last = last_state(self._last_raw)
        if self.state == "empty":
            self._render()
        else:
            self._update_nav()

    # -- states

    def _set_state(self, state: str) -> None:
        if state not in STATES:
            raise ValueError(f"no Review state {state!r}")
        if state != "done" and self._listing:
            self._end_listing()
        self.state = state
        if state == "done" and self._listing:
            self.right.setCurrentWidget(self.gallery_panel)
        else:
            self.right.setCurrentIndex(STATES.index(state))
        if state == "reviewing" and self._on_screen:
            self._watch.start()
        elif state != "reviewing":
            self._watch.stop()
        self._render()

    def _render(self) -> None:
        now = self._now()
        if self.state == "empty":
            self._render_empty(now)
        elif self.state == "scanning":
            self._render_scanning()
        elif self.state == "stopped":
            self._render_stopped()
        elif self.state == "reviewing":
            self._render_reviewing(now)
        elif self.state == "done":
            self._render_done(now)
        self._render_header()
        self._update_nav()

    def _render_header(self) -> None:
        if not hasattr(self, "finish_button"):
            return
        reviewing = self.state == "reviewing"
        self.skip_button.setVisible(reviewing)
        self.finish_button.setVisible(reviewing)
        self.finish_button.setEnabled(reviewing and not self._writing)

    def _update_nav(self) -> None:
        if self._fixture:
            return
        error = self.outcome is not None and self.outcome.state == STOPPED
        self.set_nav_state(nav_state(self.state, session=self.session, last=self._last,
                                     scan=self.scan, error=error))

    def _scope(self) -> str:
        return str(self.settings.get(SECTION, "scope", rv.DEFAULT_SCOPE) or rv.DEFAULT_SCOPE)

    def set_subtitle(self, text: str) -> None:
        self._status_subtitle = text
        self._render_source_count()

    def _render_source_count(self) -> None:
        if self._source_error:
            words = "Wallpaper count unavailable"
        elif self._source_counts is None:
            words = "Counting wallpapers…"
        else:
            count = self._source_counts.get(self._scope(), 0)
            words = f"{fmt.counted(count, 'wallpaper')} to scan"
        super().set_subtitle(f"{words} · {self._status_subtitle}"
                             if self._status_subtitle else words)

    def _refresh_source_counts(self) -> None:
        if self._fixture:
            return
        self._source_request += 1
        self._source_counts = None
        self._source_error = False
        self._render_source_count()
        config_path, library = self._config_path, self.library
        _thread(self.signals, f"source-counts:{self._source_request}",
                lambda: _source_counts(config_path, library))

    def _render_empty(self, now: datetime) -> None:
        seconds = (self._last_raw or {}).get("seconds")
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
            seconds = None
        title, body, meta = empty_text(self._last, self._scope(), now, seconds)
        self.empty.set_title(title)
        self.empty.set_body(body)
        self.empty.set_meta(meta)
        self.scan_button.setEnabled(True)
        last = self._last
        sub = ""
        if last is not None and last.scanned is not None and last.checked:
            sub = (f"{fmt.counted(last.checked, 'author')} · last checked "
                   f"{fmt.date_activity(last.scanned, now)}")
        self.authors.show_words("Authors", sub, "The list fills in\nonce a scan runs")
        self.set_subtitle(empty_subtitle(last, now))

    def _render_scanning(self) -> None:
        scan = self.scan
        if scan is None or self.state != "scanning":
            return
        left = self._left()
        self.scan_panel.render(scan, left)
        self.found.render(scan)
        bar = scan.bar() if scan.counting() else None
        self.authors.show_skeleton(scan.count_text() or "", bar, live=True)
        self.set_subtitle(scanning_subtitle(scan.authors, scan.scope))
        self._update_nav()

    def _left(self) -> float | None:
        """Seconds to go, from the live rate (the JobCenter's), or None."""
        if self._fixture_left is not None:
            return self._fixture_left
        job = getattr(self._job, "job", None) if self._job is not None else None
        try:
            return job.eta() if job is not None else None
        except Exception:  # noqa: BLE001 — an estimate is optional
            return None

    def _render_stopped(self) -> None:
        outcome = self.outcome
        if outcome is None:
            return
        words = stopped_text(outcome)
        self.stopped.set_title(words.title)
        self.stopped.set_body(words.body)
        self.stopped.set_tone("danger" if words.tone == "danger" else "neutral")
        self.stopped.set_icon(words.icon)
        self.stopped_panel.set_tone("danger" if words.tone == "danger" else None)
        self.excerpt.set_lines(list(self._log_tail))
        self.excerpt.setVisible(bool(self._log_tail))
        self.carry_button.setVisible(bool(words.carry))
        self.carry_button.setText(words.carry or "Carry on")
        self.restore_button.setVisible(words.restore)
        self.again_button.setVisible(not words.carry and not words.restore)
        self.over_button.setVisible(bool(words.carry) or words.restore)
        total = outcome.total if outcome.result is not None else 0
        self.authors.show_skeleton("", None, live=False,
                                   sub=fmt.counted(total, "author") if total else "")
        self.set_subtitle(stopped_subtitle(self._stopped_at or self._now()))

    def _render_reviewing(self, now: datetime) -> None:
        session = self.session
        if session is None:
            return
        self._fill_list()
        self.set_subtitle(reviewing_subtitle(session, now))
        if self.current is None or session.find(self.current) is None:
            self._show_no_author(now)
        else:
            self._describe(self.current)

    def _render_done(self, now: datetime) -> None:
        session = self.session
        if session is None:
            return
        self._fill_list()
        words = done_text(session, now)
        self.done.set_body(words.sentence)
        for i, item in enumerate(words.metrics):
            self.metrics.set_value(i, item[0], tone=item[2] if len(item) > 2 else None)
        last = last_state(session.to_json())
        self.done.set_meta(last_meta(last, now))
        self.done_foot.text.set_text(words.foot)
        self.set_subtitle(done_subtitle(session))
        self.as_list.setEnabled(bool(session.authors))
        if self._listing:
            self._describe_listing()

    # -- the author list

    def _ordered(self) -> list[str]:
        """The session's authors as the list shows them: sorted, filtered."""
        if self.session is None:
            return []
        cards = [self.cards[a.id] for a in self.session.authors if a.id in self.cards]
        query = self.authors.filter.text()
        found = [c for c in cards if rv.matches(c, query)]
        key = self.authors.sort.currentData() or rv.SORT_DEFAULT
        return [c.id64 for c in rv.sort_cards(found, key, self._descending)]

    def _selected_of(self, author_id: str) -> int:
        """How many of an author's wallpapers are selected (only the author
        on screen has any)."""
        if author_id != self.current or self._listing:
            return 0
        return len(self.gallery.selected_ids())

    def _fill_list(self) -> None:
        session = self.session
        if session is None:
            return
        rows = [(author_id, author_row(session.find(author_id), self.cards.get(author_id),
                                       self._selected_of(author_id)))
                for author_id in self._ordered()]
        self.authors.list.model_.set_rows(rows)
        self.authors.list.select(self.current)
        if session.authors:
            self.authors.show_list(session.done_count, len(session.authors), list_foot(session))
        else:
            self.authors.show_words("Authors with new items", "", "No author has anything new",
                                    list_foot(session))

    def _touch(self, author_id: str) -> None:
        author = self.session.find(author_id) if self.session else None
        if author is not None:
            self.authors.list.model_.update(author_id, author_row(
                author, self.cards.get(author_id), self._selected_of(author_id)))
            self.authors.show_list(self.session.done_count, len(self.session.authors),
                                   list_foot(self.session))

    def _sort_changed(self) -> None:
        self.settings.set(SECTION, "sort", self.authors.sort.currentData())
        self.settings.save()
        self._fill_list()

    def _flip_direction(self) -> None:
        self._descending = not self._descending
        self.settings.set(SECTION, "descending", self._descending)
        self.settings.save()
        self._show_direction()
        self._fill_list()

    def _show_direction(self) -> None:
        d = self.authors.direction
        d.set_icon("chevD" if self._descending else "sortUp")
        d.setToolTip("Descending — click for ascending" if self._descending
                     else "Ascending — click for descending")
        d.setAccessibleName(d.toolTip())

    # -- scanning

    def start_scan(self) -> None:
        """Scan for new items: the whole flow, from the start."""
        if self.state == "scanning":
            return
        scope = self._scope()
        flow = ScanFlow(self._prepare, scope, emit=self._relay, config_path=self._config_path)
        self._begin_flow(flow, resume=False,
                         title=f"Scanning {rv.scope_label(scope)}", activity="scan")

    def carry_on(self) -> None:
        """Count the authors the stopped scan did not reach."""
        if self.flow is None or self.outcome is None or not self.outcome.can_carry_on:
            return
        at = self.outcome.stopped_at + 1
        self._begin_flow(self.flow, resume=True,
                         title=f"Carrying on from author {fmt.count(at)}", activity="count")

    def _begin_flow(self, flow: ScanFlow, *, resume: bool, title: str, activity: str) -> None:
        self.flow = flow
        now = self._now()
        if not resume:
            self.result = None
            self.session = None
            self.cards = {}
            self.current = None
            self.gallery.show_items([])
            self.found.clear()
            self.scan = ScanProgress(flow.scope, now)
        else:
            self.scan.phase = "count"
            self.scan.started = now
        self.outcome = None
        self._log_tail.clear()
        self._job = begin("review", title, activity=activity)
        self._job_text = ""
        self._log("step", title)
        self.scan_panel.cancel.setEnabled(True)
        self.scan_panel.cancel.setText("Cancel scan")
        self._set_state("scanning")
        relay = self.signals

        def work() -> None:
            outcome = flow.resume() if resume else flow.run()
            try:
                relay.flow_ended.emit(outcome)
            except RuntimeError:
                pass
        threading.Thread(target=work, daemon=True, name="review-scan").start()

    def _relay(self, event: FlowEvent) -> None:
        """The flow's events, from its threads to the window's."""
        try:
            self.signals.flow_event.emit(event)
        except RuntimeError:
            pass

    def _prepare(self, step: Callable[[str], None]):
        """What the scan needs, made on its thread the first time: the authors
        database (made on first use, backed up if it has no backup at all), the
        Steam client, the libraries. Kept for the next scan."""
        if self._prepare_fn is not None:
            return self._prepare_fn(step)
        from ..engines.library import Library
        from ..engines.steam_api import SteamClient
        db = self.db
        if db is None:
            step("opening the authors database")
            db = open_store(self.settings)
            # A database with no backup at all — the first run after an
            # import, or backups deleted by hand — gets one before anything.
            db.ensure_snapshot()
            self.db = db
        if self.review is None or self._stale_client:
            if self.steam is not None:
                self.steam.close()
            self.steam = SteamClient(api_key=secrets.get(secrets.STEAM_API_KEY) or None)
            if self.library is None:
                library = Library()
                if not library.scanned:
                    step("reading the local libraries")
                    library.refresh()
                self.library = library
            self.review = rv.Review(db, self.steam, self.library)
            self._stale_client = False
        return self.review

    def cancel_scan(self) -> None:
        if self.flow is None or self.state != "scanning":
            return
        self.flow.cancel()
        self.scan_panel.cancel.setEnabled(False)
        self.scan_panel.cancel.setText("Stopping…")

    def _flow_event(self, e: FlowEvent) -> None:
        scan = self.scan
        if scan is None:
            return
        scan.event(e)
        if e.kind == "step":
            self._log("step", e.text[:1].upper() + e.text[1:])
        elif e.kind == "found":
            self._log("step", f"{fmt.counted(e.total, 'author')} to check"
                              + (f", {fmt.count(e.done)} checked before" if e.done else ""))
        elif e.kind == "checked" and e.card is not None:
            card = e.card
            if card.error:
                self._log("warn", f"{card.name}: {card.error}")
            elif card.badge:
                self._log("info", f"{card.name} — {fmt.count(card.badge)} new")
        elif e.kind == "owned":
            self._log("step", e.text[:1].upper() + e.text[1:])
        if self._job is not None:
            text = scan.status_text()
            bar = scan.bar()
            self._job.update(text, bar[0] if bar else 0, bar[1] if bar else 0,
                             scan.count_text())
        if not self._render_timer.isActive():
            self._render_timer.start()

    def _log(self, kind: str, message: str) -> None:
        self._log_tail.append((self._now(), kind, message))
        if self._job is not None:
            self._job.log(kind, message)

    def _flow_ended(self, outcome: ScanOutcome) -> None:
        if self.state != "scanning":
            return              # a scan dropped meanwhile: the database was restored
        self.outcome = outcome
        flow = self.flow
        if flow is not None and flow.review is not None:
            self.review = flow.review
        self.result = outcome.result
        if outcome.result is not None:
            self.cards = {c.id64: c for c in outcome.result.cards}
        job, self._job = self._job, None
        now = self._now()
        if outcome.state == DONE:
            self._scan_done(outcome, job, now)
            return
        self._stopped_at = now
        if outcome.state == CANCELLED:
            at = (outcome.stopped_at or 0) + 1
            self._log("stop", f"stopped at author {fmt.ratio(at, outcome.total, 'prose')}"
                      if outcome.result is not None else "stopped")
            if job is not None:
                job.finish("stopped", f"stopped at author {at} of {outcome.total}"
                           if outcome.result is not None else "stopped before the authors were found",
                           title="Review scan stopped")
            if outcome.result is None:
                self._set_state("empty")
                self._say("info", "The scan was stopped. Nothing was changed.")
                return
            self._set_state("stopped")
            return
        detail = outcome.error
        if outcome.result is not None and outcome.stopped_at is not None:
            name = outcome.result.cards[outcome.stopped_at].name
            self._log("error", str(detail) if detail is not None else outcome.reason)
            self._log("info", f"stopped at {name} ({fmt.ratio(outcome.stopped_at + 1, outcome.total)})")
        else:
            self._log("error", outcome.reason)
        if job is not None:
            job.fail(outcome.reason, title="Review scan stopped")
        self._set_state("stopped")
        if not self._on_screen:
            self._finished_toast("The Review scan stopped", "danger")

    def _scan_done(self, outcome: ScanOutcome, job, now: datetime) -> None:
        result = outcome.result
        session = Session.from_result(result, scanned=now)
        session.seconds = round(outcome.seconds, 1)
        self.session = session
        self._log("done", f"{fmt.counted(len(session.authors), 'author')} with new items · "
                          f"{fmt.counted(session.items, 'item')}")
        warned = []
        if self.db is not None and self.db.warnings:
            warned += list(self.db.warnings)
            self.db.warnings.clear()
        if outcome.owned_error:
            warned.append(f"what you had before could not be worked out ({outcome.owned_error})")
        incomplete = len(result.incomplete)
        self._save()
        if job is not None:
            items = session.items
            detail = (f"{fmt.counted(session.checked, 'author')} checked · "
                      f"{fmt.counted(len(session.authors), 'author')} with new items")
            if incomplete:
                detail += f" · {fmt.count(incomplete)} counted without a Steam key"
            if warned:
                detail += " · " + "; ".join(warned)
            job.finish("problems" if warned or incomplete else "clean",
                       f"{fmt.counted(items, 'new item')} from "
                       f"{fmt.counted(len(session.authors), 'author')}",
                       title=(f"{fmt.counted(items, 'new item')} to look at" if items
                              else "Nothing new to look at"),
                       detail=detail, chip="New" if items else None)
        for message in warned:
            self._say("warn", message[:1].upper() + message[1:] + ".")
        self.current = None
        self._owner = {w.id: c.id64 for c in result.cards for w in c.items}
        first = session.next_waiting(order=self._ordered_ids(session))
        self._set_state("reviewing")
        if first is not None:
            self.open_author(first)
        if not self._on_screen:
            self._finished_toast(f"The Review scan finished · "
                                 f"{fmt.counted(len(session.authors), 'author')} with new items",
                                 "ok")

    def _ordered_ids(self, session: Session) -> list[str]:
        cards = [self.cards[a.id] for a in session.authors if a.id in self.cards]
        key = self.authors.sort.currentData() or rv.SORT_DEFAULT
        return [c.id64 for c in rv.sort_cards(cards, key, self._descending)]

    def _save(self) -> None:
        """Write review_last.json, and let Overview and the badge know."""
        if self.session is None:
            return
        data = self.session.to_json()
        try:
            save_last(self._last_path(), data)
        except OSError as err:
            self._say("warn", f"Could not save the review's summary: {err}")
            return
        self._last_raw = data
        self._last = last_state(data)
        if self._services is not None:
            self._services.snapshot.refresh([REVIEW])

    # -- an author

    def open_author(self, author_id: str) -> None:
        """Show an author's gallery. Every author in the list was counted in
        full by the scan, so this fetches nothing. In the review as a list,
        it goes to the author's wallpapers in that list instead."""
        if self.session is None or self.session.find(author_id) is None:
            return
        if self._listing:
            self.authors.list.select(author_id)
            self._scroll_listing_to(author_id)
            return
        previous, self.current = self.current, author_id
        self.authors.list.select(author_id)
        self.gallery.show_items(self._gallery_items(author_id))
        if previous and previous != author_id:
            self._touch(previous)
        self._describe(author_id)

    def _gallery_items(self, author_id: str) -> list:
        """An author's wallpapers as the scan offered them, newest first — the
        ones subscribed since still among them, set back where they stood."""
        card = self.cards.get(author_id)
        author = self.session.find(author_id) if self.session is not None else None
        if card is None:
            return []
        taken = author.subscribed if author is not None else set()
        return [w for w in card.items if w.new_since_visit and (w.offer or w.id in taken)]

    def _describe(self, author_id: str) -> None:
        author = self.session.find(author_id)
        card = self.cards.get(author_id)
        g = self.gallery_panel
        g.show_gallery(True)
        g.set_mode(self._stored_view())
        g.view.show()
        g.back.hide()
        g.done_with.show()
        g.name.set_text(author.name)
        g.sub.set_text(gallery_subtitle(author, card, self._now()))
        if author.done:
            following = self.session.next_waiting(after=author_id, order=self._ordered())
            g.done_with.setText("Next author →")
            g.done_with.setEnabled(following is not None)
        else:
            g.done_with.setText(f"Done with {_short(author.name)} →")
            g.done_with.setEnabled(True)
        g.done_with.setToolTip(f"Mark {author.name} gone through and open the next author "
                               "waiting. Nothing is written until “Finish review”.")
        self._update_bar()

    def _show_no_author(self, now: datetime) -> None:
        g = self.gallery_panel
        session = self.session
        g.show_gallery(False)
        if session.authors:
            g.name.set_text("")
            g.sub.set_text("")
            g.empty.set_title("Pick an author")
            g.empty.set_body("Their wallpapers that are new since your last visit open here.")
        else:
            g.name.set_text("Nothing new")
            g.sub.set_text(f"{fmt.counted(session.checked, 'author')} checked")
            g.empty.set_title("Nothing new since the last review")
            g.empty.set_body(f"None of the {fmt.counted(session.checked, 'author')} behind "
                             f"{rv.scope_label(session.scope)} has published anything you have "
                             "not seen. “Finish review” still brings their names up to date and "
                             "adds the new ones to the authors database.")
        g.empty.set_meta("")

    def done_with_author(self) -> None:
        """Mark the author gone through, and open the next one waiting. Not
        written to the database until "Finish review" (§6.4.4)."""
        if self.session is None or self.current is None:
            return
        author_id = self.current
        if self.session.mark_done(author_id):
            self._touch(author_id)
            self._save()
            self._update_nav()
        following = self.session.next_waiting(after=author_id, order=self._ordered())
        if following is not None:
            self.open_author(following)
        else:
            self._describe(author_id)
            self._say("ok", "Every author gone through. “Finish review” writes the visit dates.")

    def open_author_page(self) -> None:
        if self.current:
            external.open_url(workshop_url(self.current))

    def _page_changed(self, page: int, pages: int, _total: int) -> None:
        self.gallery_panel.pagination.set_pages(pages, page)
        self._update_bar()

    # -- grid or list

    def _stored_view(self) -> str:
        view = self.settings.get(SECTION, "view", "grid")
        return view if view in VIEWS else "grid"

    def set_view(self, view: str) -> None:
        """Show the cards as a grid or a list, and remember which."""
        if view not in VIEWS:
            raise ValueError(f"no gallery view {view!r}")
        if not self._listing and view != self._stored_view():
            self.settings.set(SECTION, "view", view)
            self.settings.save()
        self.gallery_panel.set_mode(view)

    # -- every author as one list ("Open review as a list")

    def open_review_list(self) -> None:
        """The finished review's wallpapers, every author's under their name,
        in the list view: to look back over it, and subscribe from it."""
        if self.session is None:
            return
        order = self._ordered()
        groups, items, owner = [], [], {}
        for author_id in order:
            author = self.session.find(author_id)
            wallpapers = self._gallery_items(author_id)
            if author is None or not wallpapers:
                continue
            groups.append(Group(author_id, author.name, note=f"{fmt.count(len(wallpapers))} new"))
            for wallpaper in wallpapers:
                items.append(wallpaper)
                owner[wallpaper.id] = author_id
        self._listing = True
        self.gallery.show_items(items, paged=False)
        self.gallery_list.set_groups(groups, lambda w: owner.get(w.id))
        self.gallery_panel.set_mode("list")
        self._describe_listing()
        self.right.setCurrentWidget(self.gallery_panel)

    def _describe_listing(self) -> None:
        g = self.gallery_panel
        session = self.session
        g.show_gallery(True)
        g.set_mode("list")
        g.view.hide()
        g.author_page.hide()
        g.done_with.hide()
        g.back.show()
        g.name.set_text("Every author")
        g.sub.set_text(f"{fmt.counted(self.gallery.total, 'new item')} from "
                       f"{fmt.counted(len(session.authors), 'author')}")
        self._update_bar()

    def _scroll_listing_to(self, author_id: str) -> None:
        model = self.gallery_list.model_
        for row in model.group_rows():
            group = model.group_at(row)
            if group is not None and group.key == author_id:
                self.gallery_list.scrollTo(model.index(row, 0), QAbstractItemView.PositionAtTop)
                return

    def close_review_list(self) -> None:
        """Back to the finished review's summary."""
        if not self._listing:
            return
        self._end_listing()
        if self.state == "done":
            self.right.setCurrentIndex(STATES.index("done"))

    def _end_listing(self) -> None:
        self._listing = False
        self.gallery_list.set_groups(None, None)
        self.gallery_panel.set_mode(self._stored_view())
        self.gallery.show_items(self._gallery_items(self.current) if self.current else [])

    # -- the selection

    def _selection_changed(self, count: int) -> None:
        if self.current and self.session is not None:
            self._touch(self.current)
        self._update_bar()

    def subscribe_selected(self) -> None:
        """Subscribe to every selected wallpaper, on any page, in the order
        they were chosen; the selection is let go of."""
        if not self.by_steam:
            return
        wanted = [i for i in self.gallery.selected_ids()
                  if (w := self.gallery.find(i)) is not None and offered(w, self.gallery.busy(i))]
        self.gallery.clear_selection()
        self._queue(wanted)

    # -- subscribing

    @property
    def by_steam(self) -> bool:
        return self.settings.get(SECTION, "subscribe", BY_STEAM) != BY_PAGE

    def subscribe(self, item_id: str) -> None:
        if not self.by_steam:
            self.open_in_steam(item_id)
            return
        if self.gallery.busy(item_id) is not None:
            return
        self._queue([item_id])

    def subscribe_page(self) -> None:
        """Subscribe to every wallpaper on the page not already taken."""
        if not self.by_steam:
            return
        self._queue([w.id for w in self.gallery.current_page()
                     if w is not None and offered(w, self.gallery.busy(w.id))])

    def _queue(self, item_ids: list[str]) -> None:
        """Hand wallpapers to the subscription queue: each waits on its card
        until Steam is asked for it."""
        if not item_ids:
            return
        for item_id in item_ids:
            self.gallery.mark_busy(item_id, WAITING)
        self.subscriptions.add(item_ids)
        self._update_bar()

    def _update_bar(self) -> None:
        """The gallery's bar: what is selected and what can be subscribed to."""
        g = self.gallery_panel
        opens_page = not self.by_steam
        if self.gallery.model_.opens_page != opens_page:
            self.gallery.model_.opens_page = opens_page
            self.gallery.viewport().update()
            self.gallery_list.viewport().update()
        chosen = len(self.gallery.selected_ids())
        g.selected.setText(f"{fmt.count(chosen)} selected")
        g.selected.setVisible(bool(chosen))
        g.subscribe_selected.setVisible(bool(chosen))
        g.subscribe_selected.setEnabled(self.by_steam)
        page = [w for w in self.gallery.current_page() if w is not None]
        open_ = [w for w in page if offered(w, self.gallery.busy(w.id))]
        g.subscribe_page.setVisible(not self._listing)
        g.subscribe_page.setEnabled(self.by_steam and bool(open_))
        g.subscribe_page.setToolTip(
            f"Subscribe to the {fmt.counted(len(open_), 'wallpaper')} on this page not taken yet."
            if open_ else "Everything on this page is taken already.")
        if not self.by_steam:
            why = ("Needs “Steam directly” in Review settings: opening Steam's page for "
                   "every wallpaper would mean a window for each.")
            g.subscribe_page.setToolTip(why)
            g.subscribe_selected.setToolTip(why)
        else:
            g.subscribe_selected.setToolTip(
                f"Subscribe to the {fmt.counted(chosen, 'selected wallpaper')}. "
                "Esc lets go of the selection.")
        g.pagination.setVisible(not self._listing and self.gallery.pages > 1)

    def _title_of(self, item_id: str) -> str:
        wallpaper = self.gallery.find(item_id) or self._wallpaper(item_id)
        return (wallpaper.title or item_id) if wallpaper is not None else item_id

    def _wallpaper(self, item_id: str):
        """A wallpaper of the session by id, whichever author's it is."""
        card = self.cards.get(self._owner.get(item_id, ""))
        if card is None:
            return None
        return next((w for w in card.items if w.id == item_id), None)

    def _subscribed(self, item_id: str, _state: str) -> None:
        self.gallery.mark_busy(item_id, None)
        self._mark_subscribed(item_id)
        self._update_bar()

    def _subscribe_failed(self, item_id: str, message: str) -> None:
        self.gallery.mark_busy(item_id, None)
        self._say("danger", f"Could not subscribe to “{self._title_of(item_id)}”: {message}. "
                            "Choose “Steam's page” in Review settings if this keeps happening.")
        self._update_bar()

    def _mark_subscribed(self, item_id: str) -> None:
        """Show a wallpaper as taken where it stands — a tile vanishing under
        the cursor loses the reader's place — and count it for the session.
        The scan's own record of it is marked too, so its author's gallery says
        so when opened again."""
        if self.library is not None:
            self.library.note_subscribed(item_id)
        own = self._wallpaper(item_id)
        if own is not None:
            own.subscribed = True
        for wallpaper in self.gallery.showing():
            if wallpaper is not None and wallpaper.id == item_id:
                wallpaper.subscribed = True
                self.gallery.refresh(item_id)
        author_id = self._owner.get(item_id, self.current)
        if self.session is not None and author_id and \
                self.session.note_subscribed(author_id, item_id):
            self._touch(author_id)
            if author_id == self.current and not self._listing:
                self._describe(author_id)

    def _notice_subscriptions(self) -> None:
        """Catch wallpapers subscribed anywhere else — Steam's page, Wallpaper
        Engine itself. A listing of Steam's workshop folder, on a thread: that
        folder is on the disk Steam writes every new subscription to."""
        library = self.library
        if library is None or self._watching or self.state != "reviewing":
            return
        if not any(w and not w.subscribed for w in self.gallery.showing()):
            return
        self._watching = True
        _thread(self.signals, "subscribed", library.subscribed)

    def open_in_steam(self, item_id: str) -> None:
        """Steam's own page for this wallpaper — the path that needs no SDK."""
        external.open_url(f"steam://url/CommunityFilePage/{item_id}")

    # -- finishing

    def finish_review(self) -> None:
        """Write the review: the plan first, then the write, then the finished
        state (§6.4.5)."""
        if self.state != "reviewing" or self.session is None or self._writing:
            return
        if self.review is None or self.result is None:
            self._finished(None, [])
            return
        # Every card, not only the counted ones: an author nobody opened this
        # week still gets their current name, just not a new visit date.
        changes = self.review.plan(self.result.cards)
        if not changes:
            self._finished({"created": 0, "updated": 0, "backup": None}, [])
            self._say("info", "Nothing to write: no author's record would change.")
            return
        blind = written_blind(self.result, changes)
        title, body, lines, note = finish_plan(changes, blind)
        dialog = ConfirmDialog(title, body, self._dialog_parent(), icon="review",
                               lines=lines, note=note, safe_default=bool(blind),
                               confirm_text="Write to the database")
        self.last_dialog = dialog
        if not self._answer(dialog):
            return
        db = self.review.db
        self._writing = True
        self._render_header()
        self._job = begin("review", "Updating the authors database", activity="database")
        self._log("step", f"Writing {fmt.counted(len(changes), 'change')}")
        signals = self.signals

        def work():
            report = db.apply(changes, on_progress=lambda d, n: signals.progress.emit(d, n))
            report["warnings"] = list(db.warnings)
            db.warnings.clear()
            return report, changes

        _thread(self.signals, "write", work)

    def _write_progress(self, done: int, total: int) -> None:
        if self._job is not None:
            self._job.update("writing to the authors database", done, total)

    def _written(self, found) -> None:
        self._writing = False
        job, self._job = self._job, None
        if isinstance(found, Exception):
            message = str(found)
            if job is not None:
                job.fail(message, title="The authors database was not updated")
            damaged = isinstance(found, StoreDamaged)
            self._say("danger", f"Nothing was written: {message}"
                      + (" Open the backups from Review settings → Authors database." if damaged
                         else ""))
            self._render_header()
            return
        report, changes = found
        self.review.absorb(self.result, changes)
        line = written_line(report, bool(self.db is not None and self.db.mirror))
        warnings = report.get("warnings") or []
        if job is not None:
            backup = report.get("backup")
            job.finish("problems" if warnings else "clean", line,
                       title="Authors database updated",
                       detail=(f"{report['created']} created · {report['updated']} updated"
                               + (f" · backup {Path(backup).name}" if backup else "")
                               + (" · " + "; ".join(warnings) if warnings else "")))
        self._say("warn" if warnings else "ok",
                  line + (" But " + "; ".join(warnings) + "." if warnings else ""))
        self._finished(report, changes)

    def _finished(self, report: dict | None, _changes) -> None:
        session = self.session
        session.finished = self._now()
        if report is not None:
            session.written = {"created": report.get("created", 0),
                               "updated": report.get("updated", 0),
                               "backup": Path(report["backup"]).name if report.get("backup") else None}
        self._save()
        self._set_state("done")

    def reopen(self) -> None:
        """Back to the finished review's authors and galleries, to go on."""
        if self.session is None:
            return
        self.session.finished = None
        self._set_state("reviewing")
        if self.current is None:
            following = self.session.next_waiting(order=self._ordered())
            if following is not None:
                self.open_author(following)

    # -- settings and the database

    def edit_settings(self) -> None:
        """Review settings: the source, how a click subscribes, the key, the
        second backup folder."""
        before = read_settings(self.settings, secrets.get(secrets.STEAM_API_KEY))
        dialog = ReviewSettingsDialog(before, self._dialog_parent(), folders=self._folders,
                                      stored=stored_key_words(), on_authors=self.edit_authors)
        self.last_dialog = dialog
        self._settings_dialog = dialog
        self._refresh_source_counts()
        _thread(self.signals, "folders", lambda: _folder_counts(self._config_path))
        _thread(self.signals, "steamworks", steamworks_note)
        try:
            if not self._answer(dialog):
                return
        finally:
            self._settings_dialog = None
        changed = save_settings(self.settings, before, dialog.values())
        if "key" in changed:
            # the next scan asks Steam with the new key; this session keeps its lists
            self._stale_client = True
            self._show_keyless()
        if "mirror" in changed and self.db is not None:
            self.db.mirror = mirror_folder(self.settings)
        if "subscribe" in changed and self.state == "reviewing":
            self._update_bar()
        if "scope" in changed and self.state == "empty":
            self._render()
        self._render_source_count()

    def edit_authors(self) -> None:
        """The authors database and its backups."""
        dialog = AuthorsDialog(self.settings, self._dialog_parent())
        self.last_dialog = dialog
        dialog.start()
        self._answer(dialog)
        if self.db is not None:
            self.db.mirror = mirror_folder(self.settings)
        if dialog.restored:
            self._forget_review()

    def _forget_review(self) -> None:
        """Drop everything read from the database: it is not what is there now."""
        if self.state == "scanning" and self.flow is not None:
            self.flow.cancel()
            job, self._job = self._job, None
            if job is not None:
                job.finish("stopped", "the authors database was restored meanwhile",
                           title="Review scan stopped")
        if self.db is not None:
            self.db.close()
        self.db = self.review = None
        self.flow = self.result = self.session = self.outcome = None
        self.cards = {}
        self.current = None
        self.gallery.show_items([])
        self._set_state("empty")
        self._say("ok", "The authors database was restored from a backup. Scan again to read it.")

    def _show_keyless(self) -> None:
        self.keyless.setVisible(not has_key()
                                and not self.settings.get(SECTION, KEYLESS_HIDDEN, False))

    def _hide_keyless(self) -> None:
        self.settings.set(SECTION, KEYLESS_HIDDEN, True)
        self.settings.save()
        self._show_keyless()

    def open_log_folder(self) -> None:
        if self._services is not None:
            self._services.logs.open_folder("review")
        else:
            external.popen(["explorer", str(app_data_dir() / "logs")])

    # -- answers from threads

    def _answered(self, what: str, found) -> None:
        if what.startswith("source-counts:"):
            if what != f"source-counts:{self._source_request}":
                return                 # a newer read owns the header now
            self._source_counts = found if isinstance(found, dict) else None
            self._source_error = self._source_counts is None
            self._render_source_count()
        elif what == "write":
            self._written(found)
        elif what == "subscribed":
            self._watching = False
            if isinstance(found, set):
                for wallpaper in self.gallery.showing():
                    if wallpaper is not None and not wallpaper.subscribed and wallpaper.id in found:
                        self._mark_subscribed(wallpaper.id)
        elif what == "folders":
            if isinstance(found, dict):
                self._folders = found
                dialog = getattr(self, "_settings_dialog", None)
                if dialog is not None:
                    dialog.set_folders(found)
        elif what == "steamworks":
            dialog = getattr(self, "_settings_dialog", None)
            if dialog is not None and isinstance(found, str):
                dialog.set_steamworks(found)

    # -- telling

    def _say(self, tone: str, words: str) -> None:
        """A message: a toast, and a line kept for tests. Danger ones stay
        until closed."""
        self.messages.append((tone, words))
        toasts = getattr(self.window(), "toasts", None)
        if toasts is not None:
            toasts.show_toast(words, {"danger": "danger", "warn": "warn", "ok": "ok"}.get(
                tone, "info"))

    def _finished_toast(self, title: str, tone: str) -> None:
        """A scan that ended while another page was on screen says so there."""
        self.messages.append((tone, title))
        toasts = getattr(self.window(), "toasts", None)
        if toasts is not None:
            toasts.show_toast(title, tone, action="Show",
                              on_action=lambda: self.navigate.emit("review"))

    def _dialog_parent(self) -> QWidget:
        return self.window() if self.window() is not None else self

    def _answer(self, dialog):
        """Ask. Tests put their own answer here."""
        return dialog.ask()

    # -- for tests and snapshots

    def texts(self) -> dict:
        g = self.gallery_panel
        return {"state": self.state, "subtitle": self.subtitle(),
                "empty": (self.empty.title(), self.empty.body(), self.empty.meta()),
                "scan": self.scan_panel.texts(),
                "found": [self.found.model.row(i).title for i in self.found.model.ids()],
                "stopped": (self.stopped.title(), self.stopped.body()),
                "carry": self.carry_button.text() if not self.carry_button.isHidden() else "",
                "authors_head": (self.authors.title.text(), self.authors.count.text()),
                "list": [self.authors.list.model_.row(i).title
                         for i in self.authors.list.model_.ids()],
                "foot": self.authors.foot.text(),
                "gallery": (g.name.text(), g.sub.text(), g.done_with.text()),
                "view": g.mode(),
                "bar": {"selected": "" if g.selected.isHidden() else g.selected.text(),
                        "subscribe_selected": not g.subscribe_selected.isHidden(),
                        "subscribe_page": (not g.subscribe_page.isHidden()
                                           and g.subscribe_page.isEnabled()),
                        "done_with": not g.done_with.isHidden(), "back": not g.back.isHidden(),
                        "pages": not g.pagination.isHidden()},
                "done": (self.done.body(), self.done.meta(), self.done_foot.text.text()),
                "keyless": not self.keyless.isHidden()}

    def filter_field(self) -> QWidget | None:
        return self.authors.filter

    def frame_fixture(self, state: str) -> dict | None:
        if state not in self.FIXTURES:
            return None
        from .review_fixtures import frame_for
        return frame_for(state)

    def load_fixture(self, state: str) -> None:
        """A made-up state from tests/fixtures/ui/review.json: invented authors,
        no Steam, no disk."""
        if state not in self.FIXTURES:
            super().load_fixture(state)
        from .review_fixtures import load
        load(self, state)


def _source_counts(config_path=None, library=None) -> dict[str, int]:
    """Count the same present, distinct IDs as Review.scan, off the UI thread."""
    if library is None:
        from ..engines.library import Library
        library = Library(roots=[])
    folders = rv.we_folders(config_path)
    here = library.listable()
    scopes = [rv.FOLDER + title for title in folders]
    scopes.extend((rv.SCOPE_FOLDERS, rv.SCOPE_LOOSE, rv.SCOPE_EVERYTHING))
    return {scope: sum(item in here for item in rv.scope_candidates(scope, folders, here))
            for scope in scopes}


def _folder_counts(config_path=None) -> dict[str, int]:
    """Wallpaper Engine's folders and how many wallpapers each holds. Reads
    its config.json, on Wallpaper Engine's disk: on a thread."""
    return {title: len(items) for title, items in rv.we_folders(config_path).items()}


__all__ = ["ReviewPage", "ScanProgress", "AuthorList", "AuthorModel", "SubscribeQueue",
           "author_row", "found_row", "empty_text", "stopped_text", "finish_plan", "done_text",
           "nav_state", "written_blind", "written_line", "gallery_subtitle", "list_foot",
           "empty_subtitle", "scanning_subtitle", "reviewing_subtitle", "stopped_subtitle",
           "done_subtitle", "last_meta"]
