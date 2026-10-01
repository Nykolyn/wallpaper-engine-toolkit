"""review_flow.py — a Review as one flow: scan, go through the authors, finish.

The page used to be two buttons, "Scan" (who made what is in the folder) and
"Count what is new" (what each of them has published since), with the user in
between. They are one thing now (REDESIGN_PLAN §6.4.1): :class:`ScanFlow`
identifies the authors with :meth:`Review.scan`, then counts each author with
:meth:`Review.fill`, and says as it goes what it is doing, which author it is
on and which it has finished — so the page can show the authors as they are
found, and the status line "34 of 118".

**It stops on Steam, not on an author.** One author Steam has no answer for
(a private profile, a 404) is that author's problem, written on its card, and
the count goes on. Steam not answering at all (:class:`SteamUnreachable`), or
refusing the key, would fail every author left the same way, one slow
timeout each, so the flow stops there instead: what was counted is kept, and
:meth:`ScanFlow.resume` carries on from the first author not counted (§6.4.2).
A cancel stops the same way, at the next author.

**The session** is what one scan found and what has been done with it since:
the authors with new items, in order, each marked done once gone through, the
wallpapers subscribed, and when the review was finished. :meth:`Session.to_json`
is ``data/review_last.json`` (§6.4.3), which Overview, the sidebar's badge and
the next start of the page read back through ``snapshot.ReviewState``. That
reader ignores what it does not know, and this writer only adds keys, so an
older build reads a newer file.

No Qt here: the page runs a flow on a thread and hands ``emit`` a signal.
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Sequence

from .authors_store import DbError, StoreDamaged
from .review import DUPLICATE, KNOWN, NEW, AuthorCard, ReviewResult, scope_label
from .steam_api import SteamAuthError, SteamError, SteamUnreachable

FILE_NAME = "review_last.json"
FORMAT = 1

# What stops a count, rather than being written on one author's card.
STOPS: tuple[type[BaseException], ...] = (SteamUnreachable, SteamAuthError)

# The flow's states at its end.
DONE, STOPPED, CANCELLED = "done", "stopped", "cancelled"


# ---- what a flow says as it goes ------------------------------------------------------

@dataclass(frozen=True)
class FlowEvent:
    """One thing a flow reports, from whichever thread it is on.

    - ``step``: what it is doing, in words (``text``): opening the authors
      database, reading the local libraries, reading the folder.
    - ``progress``: a stage of the scan with numbers — ``text`` is "items"
      (wallpapers described) or "authors" (authors named).
    - ``found``: the authors are known: ``total`` of them, ``done`` already
      counted (a carry-on starts past the ones a stopped scan counted).
    - ``checking`` / ``checked``: an author (``card``, at ``index`` in the scan's
      order) being counted, and counted; ``done`` is how many are, of ``total``.
    - ``owned``: working out which of the new wallpapers you had before.
    """

    kind: str
    text: str = ""
    done: int = 0
    total: int = 0
    card: AuthorCard | None = None
    index: int = -1


@dataclass
class ScanOutcome:
    """How a flow ended. ``state`` is done, stopped (``error`` says why, in
    plain words in ``reason``) or cancelled; ``phase`` is where: "prepare"
    (opening the database, the libraries), "scan" (finding the authors) or
    "count". ``stopped_at`` is the scan-order index of the first author not
    counted, which is where a carry-on starts."""

    state: str
    result: ReviewResult | None = None
    phase: str = "count"
    checked: int = 0
    total: int = 0
    stopped_at: int | None = None
    error: BaseException | None = None
    reason: str = ""
    owned_error: str = ""
    seconds: float = 0.0

    @property
    def can_carry_on(self) -> bool:
        """Whether there is a count to carry on: the authors are known, and
        some are not counted yet."""
        return self.result is not None and self.stopped_at is not None


def plain_reason(err: BaseException) -> str:
    """Why a scan stopped, as a sentence a person reads."""
    if isinstance(err, SteamAuthError):
        return ("Steam refused the Web API key. Change or remove it in Review settings "
                "— the review also works without one.")
    if isinstance(err, SteamUnreachable):
        where = err.host or "Steam"
        tries = f" after {err.attempts} tries" if err.attempts > 1 else ""
        return f"Steam did not answer: {where} — {err.reason or 'no answer'}{tries}."
    if isinstance(err, StoreDamaged):
        return f"The authors database is damaged ({err}). Nothing was written to it."
    if isinstance(err, DbError):
        return f"The authors database could not be opened: {err}."
    if isinstance(err, SteamError):
        return f"Steam answered with an error: {err}."
    if isinstance(err, OSError):
        return f"A file could not be read: {err}."
    words = str(err).strip()
    return f"{type(err).__name__}: {words}" if words else type(err).__name__


# ---- the flow ----------------------------------------------------------------------------

class ScanFlow:
    """Scan for new items: who made what is in the source, then what each of
    them has published since the last visit. See the module's docstring.

        flow = ScanFlow(prepare, "folder:new", emit=signal.emit)
        outcome = flow.run()             # on a thread
        if outcome.can_carry_on:
            outcome = flow.resume()      # the authors not counted yet

    ``prepare(step)`` returns the :class:`Review` to use, saying what it does
    through ``step(text)``; it runs inside :meth:`run`, so a database that
    will not open ends the flow like any other stop. ``cancel()`` may be
    called from any thread.
    """

    def __init__(self, prepare: Callable[[Callable[[str], None]], object], scope: str, *,
                 emit: Callable[[FlowEvent], None] | None = None,
                 config_path: str | Path | None = None):
        self._prepare = prepare
        self.scope = scope
        self._emit = emit or (lambda _event: None)
        self._config_path = config_path
        self._stop = threading.Event()
        self._cancelled = False
        self.review = None
        self.result: ReviewResult | None = None

    # -- control

    def cancel(self) -> None:
        self._cancelled = True
        self._stop.set()

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    # -- running

    def run(self) -> ScanOutcome:
        """The whole flow: open what it needs, find the authors, count them."""
        started = time.monotonic()
        self._reset()
        try:
            self.review = self._prepare(self._step)
        except Exception as err:  # noqa: BLE001 — a flow reports, it does not raise
            return self._ended(ScanOutcome(STOPPED, phase="prepare", error=err,
                                           reason=plain_reason(err)), started)
        if self._cancelled:
            return self._ended(ScanOutcome(CANCELLED, phase="prepare"), started)
        # A new scan is the moment to notice a folder filled since the last one,
        # which is one more wallpaper that was yours before.
        self.review.forget_owned()
        self._step(f"reading {scope_label(self.scope)}")
        try:
            self.result = self.review.scan(self.scope, config_path=self._config_path,
                                           on_progress=self._progress)
        except Exception as err:  # noqa: BLE001
            return self._ended(ScanOutcome(STOPPED, phase="scan", error=err,
                                           reason=plain_reason(err)), started)
        return self._ended(self._count(), started)

    def resume(self) -> ScanOutcome:
        """Count the authors a stopped or cancelled run did not reach. The ones
        it counted are kept as they are."""
        if self.review is None or self.result is None:
            raise RuntimeError("there is no scan to carry on: run() it first")
        started = time.monotonic()
        self._reset()
        return self._ended(self._count(), started)

    def _reset(self) -> None:
        self._stop.clear()
        self._cancelled = False

    def _ended(self, outcome: ScanOutcome, started: float) -> ScanOutcome:
        outcome.seconds = time.monotonic() - started
        return outcome

    def _step(self, text: str) -> None:
        self._emit(FlowEvent("step", text))

    def _progress(self, stage: str, done: int, total: int) -> None:
        self._emit(FlowEvent("progress", stage, done, total))

    def _count(self) -> ScanOutcome:
        result, review = self.result, self.review
        cards = result.cards
        total = len(cards)
        checked = sum(1 for c in cards if c.filled)
        self._emit(FlowEvent("found", done=checked, total=total))
        wanted = [(i, c) for i, c in enumerate(cards) if not c.filled]
        lock = threading.Lock()
        state = {"checked": checked, "error": None}
        subscribed = review.library.subscribed() if wanted else set()

        def one(job: tuple[int, AuthorCard]) -> None:
            index, card = job
            if self._stop.is_set():
                return
            with lock:
                done = state["checked"]
            self._emit(FlowEvent("checking", card.name, done, total, card, index))
            try:
                review.fill(card, subscribed=subscribed, stop_on=STOPS)
            except STOPS as err:
                with lock:
                    if state["error"] is None:
                        state["error"] = err
                self._stop.set()
                return
            with lock:
                state["checked"] += 1
                done = state["checked"]
            self._emit(FlowEvent("checked", card.name, done, total, card, index))

        if wanted:
            review.steam.run_each(one, wanted)
        checked = sum(1 for c in cards if c.filled)
        first = next((i for i, c in enumerate(cards) if not c.filled), None)
        error = state["error"]
        review.recount(result)
        if error is not None:
            return ScanOutcome(STOPPED, result, "count", checked, total, first, error,
                               plain_reason(error))
        if self._cancelled and first is not None:
            return ScanOutcome(CANCELLED, result, "count", checked, total, first)
        outcome = ScanOutcome(DONE, result, "count", checked, total)
        outcome.owned_error = self._owned(cards, subscribed)
        return outcome

    def _owned(self, cards: Sequence[AuthorCard], subscribed: set[str]) -> str:
        """Mark what you had before across every author with something new. One
        parse for the set (shared with every gallery after), then a lookup per
        wallpaper — done here, at the end, so the counts the review finishes
        with are whole, not only the galleries someone happened to open."""
        offered = [c for c in cards if c.filled and c.badge]
        if not offered:
            return ""
        self._emit(FlowEvent("owned", "working out what you had before"))
        try:
            owned = self.review.owned_before(self._config_path)
            for card in offered:
                self.review.mark_owned(card, owned, subscribed)
        except Exception as err:  # noqa: BLE001 — the review is usable without it
            return plain_reason(err)
        return ""


# ---- the session ---------------------------------------------------------------------------

@dataclass
class SessionAuthor:
    """An author with new items: who, how many new at the scan, how many of
    those you had before, whether they have been gone through, and the
    wallpapers of theirs subscribed in this session."""

    id: str
    name: str
    new: int
    yours: int = 0
    state: str = KNOWN
    done: bool = False
    incomplete: bool = False
    subscribed: set[str] = field(default_factory=set)


@dataclass
class Session:
    """One scan and what has been done with it. See the module's docstring."""

    scope: str
    scanned: datetime
    authors: list[SessionAuthor] = field(default_factory=list)
    since: date | None = None
    checked: int = 0            # authors counted
    nothing_new: int = 0        # of them, with nothing new
    unread: int = 0             # authors Steam had no answer for
    finished: datetime | None = None
    written: dict | None = None  # what Finish review wrote: created, updated, backup
    seconds: float | None = None  # how long the scan took

    @classmethod
    def from_result(cls, result: ReviewResult, *, scanned: datetime,
                    scope: str | None = None) -> "Session":
        """The authors with something new, in the scan's order."""
        listed, visits = [], []
        unread = nothing = 0
        for card in result.cards:
            if not card.filled:
                continue
            if card.error:
                unread += 1
                continue
            if not card.badge:
                nothing += 1
                continue
            listed.append(SessionAuthor(id=card.id64, name=card.name, new=card.badge,
                                        yours=card.returning, state=card.state,
                                        incomplete=not card.complete))
            if card.visited is not None and card.state in (KNOWN, DUPLICATE):
                visits.append(card.visited)
        since = min(visits).astimezone().date() if visits else None
        return cls(scope=scope or result.scope, scanned=scanned, authors=listed, since=since,
                   checked=sum(1 for c in result.cards if c.filled), nothing_new=nothing,
                   unread=unread)

    # -- reading it

    @property
    def items(self) -> int:
        return sum(a.new for a in self.authors)

    @property
    def yours(self) -> int:
        return sum(a.yours for a in self.authors)

    @property
    def subscribed(self) -> int:
        return sum(len(a.subscribed) for a in self.authors)

    @property
    def done_count(self) -> int:
        return sum(1 for a in self.authors if a.done)

    @property
    def waiting(self) -> int:
        return len(self.authors) - self.done_count

    @property
    def new_authors(self) -> int:
        return sum(1 for a in self.authors if a.state == NEW)

    def find(self, author_id: str) -> SessionAuthor | None:
        return next((a for a in self.authors if a.id == author_id), None)

    # -- changing it

    def mark_done(self, author_id: str, done: bool = True) -> bool:
        """Mark an author gone through; False when they are not in the session
        or already were."""
        author = self.find(author_id)
        if author is None or author.done == done:
            return False
        author.done = done
        return True

    def note_subscribed(self, author_id: str, item_id: str) -> bool:
        author = self.find(author_id)
        if author is None or item_id in author.subscribed:
            return False
        author.subscribed.add(item_id)
        return True

    def next_waiting(self, after: str | None = None,
                     order: Sequence[str] | None = None) -> str | None:
        """The next author not gone through, after ``after`` in ``order`` (the
        list as it is shown; the scan's order without one), round to the start;
        None once every author is done."""
        ids = list(order) if order is not None else [a.id for a in self.authors]
        waiting = {a.id for a in self.authors if not a.done}
        if not waiting:
            return None
        start = ids.index(after) + 1 if after in ids else 0
        for author_id in ids[start:] + ids[:start]:
            if author_id in waiting:
                return author_id
        return next(a.id for a in self.authors if not a.done)

    # -- the file

    def to_json(self) -> dict:
        """``review_last.json``: the keys `snapshot.ReviewState` reads (scanned,
        scope, since, items, authors with name/new/done, finished), and more."""
        return {
            "format": FORMAT,
            "scanned": _iso(self.scanned),
            "scope": self.scope,
            "since": self.since.isoformat() if self.since else None,
            "items": self.items,
            "authors": [{"id": a.id, "name": a.name, "new": a.new, "yours": a.yours,
                         "new_author": a.state == NEW, "done": a.done,
                         "subscribed": len(a.subscribed)} for a in self.authors],
            "checked": self.checked,
            "nothing_new": self.nothing_new,
            "unread": self.unread,
            "subscribed": self.subscribed,
            "finished": _iso(self.finished) if self.finished else None,
            "written": self.written,
            "seconds": self.seconds,
        }


def _iso(when: datetime) -> str:
    """A local time as the file keeps it: `2026-09-18T09:10:00`, no zone."""
    if when.tzinfo is not None:
        when = when.astimezone().replace(tzinfo=None)
    return when.isoformat(timespec="seconds")


def save_last(path: str | Path, data: dict) -> None:
    """Write the summary whole or not at all: to a temporary file beside it,
    flushed, then put in its place. A reader never sees half of one."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as out:
        json.dump(data, out, ensure_ascii=False, indent=1)
        out.flush()
        os.fsync(out.fileno())
    os.replace(temp, path)


def load_last(path: str | Path) -> dict | None:
    """The summary as written, or None when there is none or it cannot be
    read. Never raises: a page with no last review still opens."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


__all__ = ["CANCELLED", "DONE", "FILE_NAME", "FlowEvent", "STOPPED", "STOPS", "ScanFlow",
           "ScanOutcome", "Session", "SessionAuthor", "load_last", "plain_reason", "save_last"]
