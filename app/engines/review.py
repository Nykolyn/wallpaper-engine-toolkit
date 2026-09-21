"""review.py — the weekly walk through new authors, done by the app.

The manual version: open Wallpaper Engine, click each wallpaper added during
the week, look at who made it, search the authors database by hand, create the
author or read off the date it was last visited, open their workshop, find the
first wallpaper published after that date, and browse forward from there. Every
step of that is a lookup this module can do, and the numbers say how much of it
was work: 2 252 wallpapers in the folder, 453 distinct authors behind them.

**Two phases, because they cost different amounts.** Identifying the authors is
twelve seconds — 12 batched requests for the wallpapers, 5 for the profiles,
one query for all 453 database records. Working out what each of them has
published *since* is a request or two per author, and that is a minute or
three. So :meth:`Review.scan` answers "who" and returns immediately, and
:meth:`Review.fill` answers "how much is new" for one author at a time, in
whatever order the window wants them.

**What counts as unseen.** A wallpaper is offered if it is not subscribed right
now. That deliberately includes wallpapers that were subscribed once and
deleted — those come back around, and the user asked for them — but they are
marked, because half of a two-thousand-card list would otherwise be things
already rejected once. Two records answer "did you have this": the
`project.json` of every kept copy in the local libraries (see :mod:`library`),
and every id Wallpaper Engine's own folders still remember, which is the far
bigger of the two — 14 915 ids here that are no longer subscribed against 425
the copies knew about. The answer costs a parse, so it is worked out once by
:meth:`Review.owned_before` and applied per gallery by :meth:`Review.mark_owned`
rather than during the count.

**What gets reviewed.** A scope, not a hard-coded folder: one of Wallpaper
Engine's browser folders, everything in any of them, everything in none of
them, or the whole library. See :func:`scope_candidates`.

**Where the visit date lands.** After a review an author's `dateVisited` moves
to the creation time of the newest wallpaper the review covered — not to "now".
An author who publishes something while the window is open would otherwise be
skipped for ever, and the whole point of the date is that nothing is skipped.
"""
from __future__ import annotations

import itertools
import json
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Sequence

from .authors_store import Author, AuthorsStore, Change
from .library import Library
from .steam_api import AuthorItems, ItemDetails, Profile, SteamClient
from .tracker import find_we_config

# What a card says about the author behind it.
NEW = "new"                 # nobody has added them to the database yet
KNOWN = "known"             # exactly one record
DUPLICATE = "duplicate"     # more than one, which the migration could not fold
UNKNOWN = "unknown"         # Steam would not say who they are

DEFAULT_FOLDER = "new"

# What a review is asked to look at. A folder scope is the folder's title
# behind :data:`FOLDER`; the three beginning with "@" are the ones no folder
# can be called, because Wallpaper Engine will not name a folder after them.
FOLDER = "folder:"
SCOPE_FOLDERS = "@folders"          # everything in any folder
SCOPE_LOOSE = "@loose"              # everything in none of them
SCOPE_EVERYTHING = "@everything"    # both, which is the whole library
DEFAULT_SCOPE = FOLDER + DEFAULT_FOLDER

# How the author list can be ordered.
SORT_DEFAULT = "default"      # authors already in the database first, then new ones
SORT_NAME = "name"
SORT_APPEARED = "appeared"    # when their wallpapers reached the folder


def we_folders(config_path: str | Path | None = None) -> dict[str, list[str]]:
    """Every folder in Wallpaper Engine's browser, title to workshop ids.

    Read fresh rather than remembered: the file is 2.35 MB and parses in 15 ms,
    and a folder the user filled this morning has to appear in the list this
    afternoon. Wallpaper Engine only flushes `config.json` when it starts and
    when it exits, so a wallpaper added minutes ago shows up after the next
    restart — which is why the tab says what the folder holds rather than
    pretending to be live.

    Subfolders are folded into their parent. Nothing here nests one today, but
    `config.json` has the key and a folder inside a folder is still a folder
    the wallpaper was put in.
    """
    path = Path(config_path) if config_path else None
    if path is None:
        found = find_we_config()
        path = Path(found) if found else None
    if path is None or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    found_folders: dict[str, list[str]] = {}

    def take(entry: dict) -> None:
        title = str(entry.get("title") or "")
        if title and title not in found_folders:
            found_folders[title] = [str(i) for i in (entry.get("items") or {})
                                    if str(i).isdigit()]
        for child in (entry.get("subfolders") or []):
            if isinstance(child, dict):
                take(child)

    for settings in data.values():
        if not isinstance(settings, dict):
            continue
        for entry in (settings.get("general", {}).get("browser", {}).get("folders") or []):
            if isinstance(entry, dict):
                take(entry)
    return found_folders


def folder_items(config_path: str | Path | None = None,
                 folder: str = DEFAULT_FOLDER) -> list[str]:
    """The workshop ids in one of Wallpaper Engine's own folders."""
    return we_folders(config_path).get(folder, [])


def folder_of(scope: str) -> str | None:
    """The folder a scope names, or None when it names more than one."""
    return scope[len(FOLDER):] if scope.startswith(FOLDER) else None


def scope_label(scope: str) -> str:
    """What to call a scope. A noun phrase, so it reads after "Reading" too."""
    if scope == SCOPE_FOLDERS:
        return "all folders"
    if scope == SCOPE_LOOSE:
        return "whatever is in no folder"
    if scope == SCOPE_EVERYTHING:
        return "your whole library"
    name = folder_of(scope)
    return f"folder “{name}”" if name else str(scope)


def scope_candidates(scope: str, folders: dict[str, list[str]],
                     here: set[str]) -> list[str]:
    """The ids a scope asks about, before anything is filtered out of them.

    ``here`` is what Wallpaper Engine can actually show — the only way to know
    that a wallpaper is in *no* folder is to know it is here at all, since a
    folder remembers ids nothing is left of.
    """
    foldered: list[str] = []
    seen: set[str] = set()
    for title, items in folders.items():
        if scope.startswith(FOLDER) and title != folder_of(scope):
            continue
        for item_id in items:
            if item_id not in seen:
                seen.add(item_id)
                foldered.append(item_id)
    if scope == SCOPE_LOOSE:
        return sorted(here - seen)
    if scope == SCOPE_EVERYTHING:
        return foldered + sorted(here - seen)
    return foldered


@dataclass
class Wallpaper:
    """One wallpaper on an author's page, and why it is being shown."""

    item: ItemDetails
    subscribed: bool = False
    once_had: bool = False
    in_queue: bool = False          # it is in the Wallpaper Engine folder
    new_since_visit: bool = True
    # Whether anybody has yet asked whether this machine has had it before.
    # Until they have, ``once_had`` is False because nothing is known, not
    # because the answer is no — and a card must not claim "new" on the
    # strength of a question it never asked.
    owned_checked: bool = False

    @property
    def unseen(self) -> bool:
        """Never subscribed, never owned, never copied — genuinely new to you."""
        return self.owned_checked and not self.subscribed and not self.once_had

    @property
    def id(self) -> str:
        return self.item.id

    @property
    def title(self) -> str:
        return self.item.title

    @property
    def created(self) -> datetime | None:
        return self.item.created_at

    @property
    def offer(self) -> bool:
        """Whether it belongs in the gallery at all."""
        return not self.subscribed


@dataclass
class AuthorCard:
    """One row of the first list, and the gallery behind it."""

    id64: str
    profile: Profile | None = None
    records: list[Author] = field(default_factory=list)
    queued: list[str] = field(default_factory=list)     # their items in the folder
    items: list[Wallpaper] = field(default_factory=list)
    total: int = 0
    filled: bool = False
    deep: bool = False          # the gallery has been fetched
    appeared: datetime | None = None   # when their first queued wallpaper arrived
    complete: bool = True
    owned_checked: bool = False        # the "was yours" pass has run over `items`
    error: str = ""

    @property
    def state(self) -> str:
        if self.profile is None or not self.profile.exists:
            return UNKNOWN
        if not self.records:
            return NEW
        return KNOWN if len(self.records) == 1 else DUPLICATE

    @property
    def record(self) -> Author | None:
        """The record a review would write to: the most recently visited one."""
        if not self.records:
            return None
        return max(self.records, key=lambda r: r.visited or datetime.min.replace(
            tzinfo=timezone.utc))

    @property
    def name(self) -> str:
        if self.profile and self.profile.name:
            return self.profile.name
        record = self.record
        return record.name if record else self.id64

    @property
    def names(self) -> list[str]:
        """Every name this author is known by, current one first.

        More than one is the normal case, not an edge. Authors rename, and the
        database keeps whatever they were called on the day they were added:
        an author can be one name in the database and another on Steam today
        today, and was "Banned Cleavage" in between. Searching has to find
        them by any of those.
        """
        found: list[str] = []
        for candidate in [self.profile.name if self.profile else ""] + \
                [r.name for r in self.records]:
            if candidate and candidate not in found:
                found.append(candidate)
        return found

    @property
    def database_name(self) -> str | None:
        """The name on the record, when it differs from what Steam says now."""
        record = self.record
        if record and record.name and record.name != self.name:
            return record.name
        return None

    @property
    def visited(self) -> datetime | None:
        record = self.record
        return record.visited if record else None

    @property
    def offered(self) -> list[Wallpaper]:
        """What the gallery shows, newest first.

        Not subscribed, and — for an author already in the database — published
        after the last visit. Everything older has been looked at once already;
        that is what the visit date means. A new author has no such date, so all
        of their work is on offer.
        """
        return [w for w in self.items if w.offer and w.new_since_visit]

    @property
    def badge(self) -> int:
        """The number on the card.

        For a known author, wallpapers published since the last visit that are
        not subscribed. For a new one there is no "since", so it is everything
        of theirs not subscribed — the same question asked of somebody whose
        work has never been looked at.

        The same set the gallery shows, so the number on the card is the number
        of cards behind it.
        """
        return len(self.offered)

    @property
    def returning(self) -> int:
        """How many of the offered ones were had before and deleted."""
        return sum(1 for w in self.offered if w.once_had)

    def newest_covered(self) -> datetime | None:
        """The creation time the visit date should move to."""
        stamps = [w.item.created for w in self.items if w.item.created]
        return datetime.fromtimestamp(max(stamps)).astimezone() if stamps else None


@dataclass
class ReviewResult:
    cards: list[AuthorCard] = field(default_factory=list)
    queue: list[str] = field(default_factory=list)
    remembered: int = 0          # ids the folder holds, including long-gone ones
    unresolved: list[str] = field(default_factory=list)
    counts: dict = field(default_factory=dict)
    scope: str = DEFAULT_SCOPE   # what was asked about

    def by_state(self, state: str) -> list[AuthorCard]:
        return [c for c in self.cards if c.state == state]

    @property
    def incomplete(self) -> list[AuthorCard]:
        """Counted authors whose lists Steam cut short — read without a key,
        so mature wallpapers are not in them."""
        return [c for c in self.cards if c.filled and not c.complete]

    def summary(self) -> str:
        c = self.counts
        gone = c.get("remembered", 0) - c.get("queued", 0)
        # Which folder, when there is only one; "remembered" alone when the
        # scope spans several and no single folder is the one doing it.
        whose = "the folder remembers" if folder_of(self.scope) else "remembered"
        tail = f" ({gone} more {whose}, long gone)" if gone > 0 else ""
        return (f"{scope_label(self.scope)}: {c.get('queued', 0)} wallpapers{tail}, "
                f"{c.get('authors', 0)} authors: {c.get('new', 0)} new, "
                f"{c.get('known', 0)} known, {c.get('duplicate', 0)} duplicated, "
                f"{c.get('unknown', 0)} unidentifiable")


class Review:
    """Turns a folder of wallpapers into a list of authors worth visiting."""

    def __init__(self, db: AuthorsStore, steam: SteamClient, library: Library,
                 on_log: Callable[[str], None] | None = None,
                 on_progress: Callable[[str, int, int], None] | None = None):
        self.db = db
        self.steam = steam
        self.library = library
        self._log = on_log or (lambda _message: None)
        self._progress = on_progress or (lambda _stage, _done, _total: None)
        # One fetch per author at a time. "Count what is new" and a click on an
        # author it has not reached yet used to fetch the same catalogue twice,
        # side by side, and write the same card from two threads.
        self._filling: dict[str, threading.Lock] = {}
        self._filling_guard = threading.Lock()
        # Worked out once, off the GUI thread, and shared by every gallery.
        self._owned: set[str] | None = None
        self._owned_lock = threading.Lock()
        # Whatever config.json the last scan read, so the pass that asks what
        # you used to own reads the same one.
        self._config_path: str | Path | None = None

    # -- phase one: who ----------------------------------------------------

    def scan(self, scope: str = DEFAULT_SCOPE,
             config_path: str | Path | None = None,
             only_present: bool = True) -> ReviewResult:
        """Identify every author behind the chosen wallpapers. Seconds, no more.

        ``scope`` is one folder of Wallpaper Engine's, or one of the three
        that cross folders: :data:`SCOPE_FOLDERS`, :data:`SCOPE_LOOSE` and
        :data:`SCOPE_EVERYTHING`. It used to be the `new` folder and nothing
        else, which is the weekly habit but not the only question worth
        asking — this library has 12 353 ids in one folder, 3 374 in another
        and 66 wallpapers in none at all, and those 66 were unreachable.

        ``only_present`` is what makes the count match what Wallpaper Engine
        shows: see :meth:`Library.listable`. On the folder this was built
        against, 2 155 ids are remembered and 19 are wallpapers you still have.
        Reviewing the other 2 136 would mean re-reviewing everything ever put
        aside and since deleted.
        """
        self._config_path = config_path
        folders = we_folders(config_path)
        here = self.library.listable()
        remembered = scope_candidates(scope, folders, here)
        queue = [i for i in remembered if i in here] if only_present else remembered
        self._log(f"{scope_label(scope)}: {len(queue)} wallpapers"
                  + (f" ({len(remembered) - len(queue)} more the folder remembers "
                     "but nothing is left of)" if len(queue) != len(remembered) else ""))
        result = ReviewResult(queue=queue, remembered=len(remembered), scope=scope)
        if not queue:
            return result

        described = self.steam.details(
            queue, on_progress=lambda d, n: self._progress("items", d, n))
        by_author: dict[str, list[str]] = {}
        for item_id in queue:
            found = described.get(item_id)
            if found is None or not found.ok or not found.creator:
                result.unresolved.append(item_id)
                continue
            by_author.setdefault(found.creator, []).append(item_id)
        self._log(f"{len(by_author)} authors, {len(result.unresolved)} wallpapers "
                  "Steam would not describe")

        # Names are fetched fresh every scan when that is cheap. A cached one
        # is how an author the user knows as "Baka" was listed as "Banned
        # Cleavage" — a name neither the database nor Steam still used — and
        # could not be found. With a key a hundred profiles are one request.
        # Without one each is a page of its own, 0.4 s apart: three minutes
        # for an ordinary week, every scan. So a keyless scan takes names from
        # the cache, which is at most two weeks old, and fetches only the new.
        profiles = self.steam.profiles(
            list(by_author), refresh=self.steam.has_key,
            on_progress=lambda d, n: self._progress("authors", d, n))
        found_records = self.db.lookup_many(
            {key: (profiles[key].keys if key in profiles else [key])
             for key in by_author})

        arrived = self.library.added_at(queue)
        for id64, queued in by_author.items():
            stamps = [arrived[i] for i in queued if i in arrived]
            result.cards.append(AuthorCard(
                id64=id64, profile=profiles.get(id64),
                records=found_records.get(id64, []), queued=queued,
                appeared=datetime.fromtimestamp(min(stamps)).astimezone()
                if stamps else None))

        result.cards = sort_cards(result.cards)
        result.counts = self._count(result)
        return result

    # -- phase two: how much ----------------------------------------------

    def fill(self, card: AuthorCard, full: bool = False,
             refresh: bool = False, subscribed: set[str] | None = None,
             owned: set[str] | None = None) -> AuthorCard:
        """Work out what this author has that is not here, and mark it up.

        For an author already in the database only what was published after
        the last visit is wanted, and the ordering of Steam's answer means that
        is the first page — one request for an author with twelve hundred
        wallpapers. A new author has no visit date, so their whole list is read.

        The badge and the gallery are the same set, so one fill serves both and
        ``full`` no longer changes anything; it is kept so callers need not.

        A second caller for an author already being filled waits for the first
        and takes its answer, rather than asking Steam again.

        **What this deliberately does not answer is whether you had it before.**
        That is :meth:`mark_owned`, and it is separate because counting a week
        is four hundred of these and nobody is looking at a gallery yet. Pass
        ``owned`` to have it done here anyway.
        """
        with self._filling_guard:
            lock = self._filling.setdefault(card.id64, threading.Lock())
        with lock:
            if card.filled and card.deep and not refresh:
                return card
            return self._fill(card, refresh, subscribed, owned)

    def _fill(self, card: AuthorCard, refresh: bool, subscribed: set[str] | None,
              owned: set[str] | None) -> AuthorCard:
        # Re-read from disk each time it is asked for, so a batch hands it in
        # once rather than rebuilding it per card.
        subscribed = self.library.subscribed() if subscribed is None else subscribed
        queued = set(card.queued)
        visited = card.visited if card.state in (KNOWN, DUPLICATE) else None
        since = visited
        try:
            found: AuthorItems = self.steam.author_items(
                card.id64, since=since, refresh=refresh)
        except Exception as err:  # noqa: BLE001 — one bad author must not stop a run
            card.error = str(err)
            card.filled = True
            return card

        cutoff = int(visited.timestamp()) if visited else None
        card.error = ""             # whatever went wrong last time did not now
        card.total = found.total
        card.complete = found.complete
        card.items = [
            Wallpaper(item=item,
                      subscribed=item.id in subscribed,
                      in_queue=item.id in queued,
                      new_since_visit=cutoff is None or item.created > cutoff)
            for item in found.items
            if cutoff is None or item.created > cutoff
        ]
        card.filled = True
        card.deep = True
        card.owned_checked = False
        if owned is not None:
            self.mark_owned(card, owned, subscribed)
        return card

    # -- phase three: have you had it before? ------------------------------

    def owned_before(self, config_path: str | Path | None = None) -> set[str]:
        """Every workshop id this machine has had at some point. Cached.

        Two sources, and the second was the missing one. The local libraries
        hold a `project.json` per kept copy naming its `workshopid` — 4 412
        here — but a wallpaper subscribed to and later unsubscribed without
        ever being copied leaves nothing behind. **Wallpaper Engine's own
        folders remember it.** An id put in a folder stays there for ever, and
        the three folders on this machine remember 16 563 ids of which 14 915
        are no longer subscribed; only 425 of those were in the copies index.
        So without the folders, fourteen thousand wallpapers already seen and
        dropped were being offered back unmarked.

        Every folder is read whatever the review is scoped to: the question
        "have I had this" is not about the folder being reviewed, and reading
        them all costs the same 15 ms.
        """
        with self._owned_lock:
            if self._owned is None:
                where = config_path if config_path is not None else self._config_path
                self._owned = (self.library.ever_had()
                               | {i for ids in we_folders(where).values()
                                  for i in ids})
            return self._owned

    def forget_owned(self) -> None:
        """Drop the cache, so the next ask re-reads the folders and the index."""
        with self._owned_lock:
            self._owned = None

    def mark_owned(self, card: AuthorCard, owned: set[str] | None = None,
                   subscribed: set[str] | None = None) -> AuthorCard:
        """Fill in "was yours" across a card's gallery.

        Cheap once :meth:`owned_before` has been asked once — a set lookup per
        wallpaper — and the slow part of it is the parse behind that set, which
        is why the tab does this on a thread when a gallery is opened rather
        than during the count.
        """
        owned = self.owned_before() if owned is None else owned
        subscribed = self.library.subscribed() if subscribed is None else subscribed
        for wallpaper in card.items:
            wallpaper.once_had = (wallpaper.id in owned
                                  and wallpaper.id not in subscribed)
            wallpaper.owned_checked = True
        card.owned_checked = True
        return card

    def fill_all(self, result: ReviewResult, refresh: bool = False,
                 full: bool = False,
                 only: Sequence[str] | None = None) -> ReviewResult:
        """Fill every card, reporting progress.

        One Steam request per author and almost nothing else: measured at 641 ms
        median, of which all but a rounding error is waiting for Frankfurt.
        Done one author at a time that was 44 s for fifty authors, with the
        machine idle throughout. They go through the client's pool instead,
        where the per-host throttle still paces the requests — so Steam is
        asked no faster than before, the waiting just overlaps.

        The library set every card needs is read once here rather than once per
        card, which is small (0.6 ms) until a week brings 453 authors and it is
        not. Whether a wallpaper was yours before is not asked at all — see
        :meth:`mark_owned`.
        """
        wanted = [c for c in result.cards
                  if (only is None or c.id64 in set(only))
                  and not (c.deep if full else c.filled)]
        if wanted:
            subscribed = self.library.subscribed()
            counter = itertools.count(1)
            lock = threading.Lock()

            def one(card: AuthorCard) -> None:
                self.fill(card, full=full, refresh=refresh,
                          subscribed=subscribed)
                with lock:
                    done = next(counter)
                # A Qt signal, which is why this is safe to call from a worker.
                self._progress("wallpapers", done, len(wanted))

            self.steam.run_each(one, wanted)
        result.counts = self._count(result)
        return result

    def recount(self, result: ReviewResult) -> dict:
        """Refresh the totals after cards were filled one at a time."""
        result.counts = self._count(result)
        return result.counts

    # -- writing back ------------------------------------------------------

    def plan(self, cards: Iterable[AuthorCard],
             visited: datetime | None = None) -> list[Change]:
        """What pressing Update would write: one change per author, at most.

        A reviewed author — one whose card was counted — gets their visit date
        moved to the newest wallpaper the review covered, so the gallery just
        looked at counts as looked at and next week starts after it. A new
        author is created the same way.

        Every known author also gets their **current name**, reviewed or not.
        The database keeps whatever an author was called the day they were
        added, and a stale name is what hid "Baka" behind two renames. Steam's
        name is already in hand from the scan, so writing it back costs a line
        in the confirmation and nothing else; an author not yet reviewed keeps
        their visit date untouched.
        """
        changes: list[Change] = []
        for card in cards:
            if card.state == UNKNOWN:
                continue
            current = card.profile.name if card.profile and card.profile.name else None
            # With nothing newer found, the visit date stays where it was rather
            # than jumping to "now" — which would skip anything published
            # between the fetch and the press of the button.
            when = (visited or card.newest_covered() or card.visited
                    or datetime.now().astimezone())
            if card.state == NEW:
                if card.filled:
                    changes.append(self.db.plan_create(
                        name=card.name, steam_id=card.id64, visited=when))
                continue
            record = card.record
            if record is None:
                continue
            if card.filled:
                change = self.db.plan_update(
                    record, visited=when, name=current, reason="reviewed")
            else:
                change = self.db.plan_update(record, name=current, reason="renamed")
            if change:
                changes.append(change)
        return changes

    def absorb(self, result: ReviewResult, changes: Iterable[Change]) -> None:
        """Make the cards say what was just written, without a rescan.

        A renamed author loses the grey "was …", a visited one its old date,
        and a created one stops being new. That last one mattered: a card left
        "new" after its author was created planned the same creation again on
        the next press of Update, which the database then refused.
        """
        by_id = {c.id64: c for c in result.cards}
        for change in changes:
            if change.kind == "create":
                card = by_id.get(str(change.fields.get("key")))
                if card is not None:
                    card.records = [Author(
                        steam_id=str(change.fields["key"]),
                        name=change.fields.get("name") or "",
                        added=change.fields.get("added"),
                        visited=change.fields.get("visited"))]
            elif change.kind == "update" and change.author is not None:
                for column, attribute in (("name", "name"), ("added", "added"),
                                          ("visited", "visited"),
                                          ("key", "steam_id")):
                    if column in change.fields:
                        setattr(change.author, attribute, change.fields[column])
        result.counts = self._count(result)

    # -- odds and ends -----------------------------------------------------

    def _count(self, result: ReviewResult) -> dict:
        counts = {
            "queued": len(result.queue),
            "remembered": result.remembered,
            "authors": len(result.cards),
            "unresolved": len(result.unresolved),
            "new": len(result.by_state(NEW)),
            "known": len(result.by_state(KNOWN)),
            "duplicate": len(result.by_state(DUPLICATE)),
            "unknown": len(result.by_state(UNKNOWN)),
        }
        filled = [c for c in result.cards if c.filled]
        if filled:
            counts["filled"] = len(filled)
            counts["to_review"] = sum(c.badge for c in filled)
            counts["returning"] = sum(c.returning for c in filled)
            counts["gallery"] = sum(len(c.offered) for c in filled)
        return counts


# ---- Ordering and finding authors ------------------------------------------

_STATE_ORDER = {KNOWN: 0, DUPLICATE: 1, NEW: 2, UNKNOWN: 3}
_FOREVER = datetime.max.replace(tzinfo=timezone.utc)


def sort_cards(cards: Iterable[AuthorCard], by: str = SORT_DEFAULT,
               descending: bool = False) -> list[AuthorCard]:
    """The author list in the order asked for.

    The default puts authors already in the database first and new ones after
    them, busiest first within each: a known author is a visit date and a short
    list, a new one is a whole back catalogue, and the short lists are the ones
    to clear first. Descending reverses whichever order is chosen.
    """
    cards = list(cards)
    if by == SORT_NAME:
        key = lambda c: (c.name.casefold(), c.id64)
    elif by == SORT_APPEARED:
        # An author with no arrival time sorts last whichever way round.
        key = lambda c: (c.appeared is None, c.appeared or _FOREVER, c.name.casefold())
    else:
        key = lambda c: (_STATE_ORDER.get(c.state, 9), -len(c.queued), c.name.casefold())
    ordered = sorted(cards, key=key)
    if descending:
        undated = [c for c in ordered if by == SORT_APPEARED and c.appeared is None]
        dated = [c for c in ordered if c not in undated]
        ordered = list(reversed(dated)) + undated
    return ordered


_PROFILE_URL = re.compile(r"steamcommunity\.com/(profiles|id)/([^/?#\s]+)", re.I)


def matches(card: AuthorCard, query: str) -> bool:
    """Whether a card answers to what was typed in the search box.

    One box, two rules, both always tried: a name matches if it *contains* the
    text, an id matches only if it *is* the text. Trying both saves guessing
    which was meant — "2949338283" is an author's name here, not an id. Names
    include the ones the database remembers, and a pasted profile URL is
    reduced to the id or vanity name in it, the way the old app's search did.
    """
    text = (query or "").strip()
    if not text:
        return True
    found = _PROFILE_URL.search(text)
    if found:
        text = found.group(2)
    folded = text.casefold()

    ids = {card.id64.casefold()}
    ids.update(r.steam_id.casefold() for r in card.records if r.steam_id)
    if card.profile and card.profile.vanity:
        ids.add(card.profile.vanity.casefold())
    if folded in ids:
        return True
    return any(folded in name.casefold() for name in card.names)
