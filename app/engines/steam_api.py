"""steam_api.py — everything this suite knows about a wallpaper's author.

Wallpaper Engine's own files say nothing about *who* made a wallpaper. A
`project.json` carries a title and a workshop id and stops there; the workshop
`.acf` and the folder timestamps are rewritten wholesale by a Steam validation
pass, so they cannot even date an addition. Authorship has to come from Steam,
and this module is the only place that talks to it.

**Why the Web API key is not optional.** The obvious way in is the public
workshop listing, and it is a trap. Measured against a real library, an
anonymous listing does not show an author's work — it shows a fraction of it:

    author A   listing: 0     owned: 174   unlisted: 174
    author B   listing: 191   owned: 58    unlisted: 24
    author C   listing: 278   owned: 49    unlisted: 47

Mature and questionable content is invisible to a signed-out client, and it is
43% of this library. The item page is no better: fetched anonymously, a mature
wallpaper's page comes back as an empty `Steam Workshop` shell with no item
markup at all. A review built on that would quietly recommend "nothing new"
for the authors it matters most for.

The Web API has no such gate — the keyless `GetPublishedFileDetails` describes
mature items perfectly well — so with a key everything is read through it:

* :meth:`SteamClient.details` — ``ISteamRemoteStorage/GetPublishedFileDetails``,
  **200 ids in one POST**, no key needed. Turns a folder of workshop ids into a
  list of authors, with each wallpaper's title, creation time and preview.
* :meth:`SteamClient.author_items` — ``IPublishedFileService/GetUserFiles``,
  100 per page *with* metadata. The complete list of what an author published,
  which is the whole point of the exercise.
* :meth:`SteamClient.profiles` — ``ISteamUser/GetPlayerSummaries``, **100
  authors in one request**, returning the display name and the profile URL that
  the vanity name is read out of. That pairing of numeric id and vanity name is
  what a database lookup runs on: the authors database holds some people under
  one form and some under the other — a few under both, as two records with
  different visit dates — so both keys have to travel together.

Without a key the same three answers are approximated from the community pages,
every result is flagged ``complete=False``, and the caller is expected to say so
out loud rather than pretend the list is whole.

**Being a guest.** None of this is a quota to spend, so the client is
deliberately timid: requests to each host are spaced by a minimum interval, run
a handful at a time, and back off — for every thread at once, not just the one
that was refused — when Steam answers 429. Answers are cached in a SQLite file
beside the other suite data, because the facts asked for most (an item's
creation time, an author's name) never change once published, and a review
interrupted halfway should resume rather than start over.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Sequence

from ..settings import app_data_dir

# Wallpaper Engine's Steam app id — every listing is filtered to it.
APP_ID = 431960

API = "https://api.steampowered.com"
COMMUNITY = "https://steamcommunity.com"

DETAILS_URL = f"{API}/ISteamRemoteStorage/GetPublishedFileDetails/v1/"
USER_FILES_URL = f"{API}/IPublishedFileService/GetUserFiles/v1/"
SUMMARIES_URL = f"{API}/ISteamUser/GetPlayerSummaries/v2/"

# GetPublishedFileDetails accepts far more than the nine ids the old scraper
# used; 200 is verified working and keeps a 2 000-item scan to a dozen requests.
DETAILS_BATCH = 200

# GetPlayerSummaries takes 100 steamids per call. So does GetUserFiles per page.
SUMMARY_BATCH = 100
USER_FILES_PER_PAGE = 100

# The community listing offers 9 / 18 / 30 and ignores anything larger.
PER_PAGE = 30

# An author with more than this many wallpapers is read up to the cap and
# reported as truncated rather than paged through for ever.
MAX_AUTHOR_PAGES = 40

# How long a cached answer is trusted. Creation times and titles are immutable,
# so items could be kept for ever; the ceiling is there for `time_updated` and
# for items that turn private. Names and vanity URLs change rarely but do.
# An author's item list is capped short enough that a weekly review re-reads it.
DETAILS_TTL = 30 * 24 * 3600
PROFILE_TTL = 14 * 24 * 3600
AUTHOR_TTL = 12 * 3600

# How many requests may be in flight. The per-host throttle below is what
# actually protects Steam — it paces api.steampowered.com to one request every
# 0.2 s however many threads are waiting — so this only decides how much of the
# latency overlaps. Filling fifty author cards, all of it round trips to
# Frankfurt: 44.2 s at one at a time, 16.4 s at four, 11.3 s at eight, and
# 11.1 s at twelve, where the throttle is the whole of the remaining time
# (52 requests x 0.2 s = 10.4 s). Eight is where the gain stops.
WORKERS = 8
LISTING_TTL = 3600

CACHE_PATH = app_data_dir() / "steam_cache.sqlite"

# Cache kinds for the answers whose shape has grown. Renaming the kind is how
# an entry written before a field existed stops being served: an old item has
# no type and no size, and would show that way for the rest of its thirty days.
ITEM_CACHE = "item.2"
AUTHOR_CACHE = "author.2"

# The tags Wallpaper Engine uses for what a wallpaper *is*. Everything else in
# the tag list is genre, resolution or rating.
WALLPAPER_KINDS = ("Scene", "Video", "Web", "Application", "Preset")

# Where a download stops being incidental. See `ItemDetails.large`.
LARGE_FILE = 1024 ** 3

# Steam serves the plain-Python user agent a different, thinner page.
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
}

# Waits between retries of a request that failed for a reason worth retrying.
BACKOFF = (2.0, 6.0, 15.0)


class SteamError(RuntimeError):
    """A request Steam refused in a way retrying will not fix."""


class SteamAuthError(SteamError):
    """The Web API key is missing, wrong, or not allowed to ask this."""


# ---- What Steam gives back -------------------------------------------------

@dataclass
class ItemDetails:
    """One workshop item.

    ``ok`` is false for an id Steam will not describe — deleted, hidden or
    friends-only. About one in two hundred of a real library, and they stay in
    the list rather than being dropped, or the reason an item disappeared from
    a review becomes invisible.
    """

    id: str
    ok: bool
    creator: str | None = None
    title: str = ""
    created: int = 0
    updated: int = 0
    preview: str | None = None
    subscriptions: int = 0
    favorited: int = 0
    file_size: int = 0          # bytes, as Steam reports the download
    kind: str = ""              # Scene, Video, Web, Application — or "" if untagged

    @property
    def large(self) -> bool:
        """Whether this is a download worth being warned about.

        A gigabyte is the line because that is where the decision changes: a
        6 MB scene is a click, and a 1.4 GB video is a minute of downloading
        and a permanent gigabyte, on a library where a week can add forty of
        them. Above it a card says so in the warning colour rather than in the
        same grey as the date.
        """
        return (self.file_size or 0) >= LARGE_FILE

    @property
    def size_text(self) -> str:
        """The download size the way a card has room for it: `6.0 MB`."""
        size = float(self.file_size or 0)
        if size <= 0:
            return ""
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024 or unit == "GB":
                return f"{size:.0f} {unit}" if unit in ("B", "KB") else f"{size:.1f} {unit}"
            size /= 1024
        return ""

    @property
    def created_at(self) -> datetime | None:
        """When it was published, as an *aware* local datetime.

        Aware on purpose. This value ends up in the authors database as a visit
        date, stored in UTC, while `fromtimestamp` returns local time — a
        naive one read the wrong way is three hours out on this machine, which
        is enough to hide a morning's wallpapers or show yesterday's twice.
        """
        return datetime.fromtimestamp(self.created).astimezone() if self.created else None

    @property
    def url(self) -> str:
        return f"{COMMUNITY}/sharedfiles/filedetails/?id={self.id}"

    @property
    def steam_url(self) -> str:
        """The same page inside the Steam client, where Subscribe is one click."""
        return f"steam://url/CommunityFilePage/{self.id}"

    def to_json(self) -> dict:
        return {
            "id": self.id, "ok": self.ok, "creator": self.creator,
            "title": self.title, "created": self.created, "updated": self.updated,
            "preview": self.preview, "subscriptions": self.subscriptions,
            "favorited": self.favorited, "file_size": self.file_size,
            "kind": self.kind,
        }

    @classmethod
    def from_json(cls, data: dict) -> "ItemDetails":
        return cls(**data)

    @classmethod
    def from_steam(cls, data: dict) -> "ItemDetails":
        """Read either API shape.

        ``GetPublishedFileDetails`` reports success in ``result``;
        ``GetUserFiles`` omits the field entirely for items it does return.
        """
        item_id = str(data.get("publishedfileid", ""))
        if data.get("result", 1) != 1:
            return cls(id=item_id, ok=False)
        return cls(
            id=item_id,
            ok=True,
            creator=str(data["creator"]) if data.get("creator") else None,
            title=data.get("title") or "",
            created=int(data.get("time_created") or 0),
            updated=int(data.get("time_updated") or 0),
            preview=data.get("preview_url"),
            subscriptions=int(data.get("subscriptions") or 0),
            favorited=int(data.get("favorited") or 0),
            file_size=int(data.get("file_size") or 0),
            kind=_kind_of(data.get("tags")),
        )


@dataclass
class Profile:
    """An author seen from both sides: the numeric id and the vanity name.

    ``keys`` is what a database lookup runs on. Someone stored years ago as
    a vanity name and again later as an account number is one person with two
    records, and only searching on both turns that up.
    """

    id64: str | None
    name: str = ""
    vanity: str | None = None
    private: bool = False
    avatar: str | None = None
    exists: bool = True

    @property
    def keys(self) -> list[str]:
        return [k for k in (self.id64, self.vanity) if k]

    @property
    def workshop_url(self) -> str | None:
        return workshop_url(self.id64) if self.id64 else None

    def to_json(self) -> dict:
        return {
            "id64": self.id64, "name": self.name, "vanity": self.vanity,
            "private": self.private, "avatar": self.avatar, "exists": self.exists,
        }

    @classmethod
    def from_json(cls, data: dict) -> "Profile":
        return cls(**data)


@dataclass
class AuthorItems:
    """What an author published for Wallpaper Engine, newest first.

    Three flags describe how much of that is really here, and they mean
    different things:

    * ``complete`` — the *source* could see everything. True through the Web
      API; false when scraped from the community pages, where mature content is
      invisible to a signed-out client and the list is known to be short.
    * ``partial`` — we stopped early on purpose, because a cutoff was given and
      the ordering proves nothing older can matter. Everything newer than the
      cutoff is present; ``total`` still says how many the author has.
    * ``truncated`` — we hit the page cap. Nobody asked for that, so it is the
      one flag that means "this answer may actually be missing something".
    """

    id64: str
    items: list[ItemDetails] = field(default_factory=list)
    total: int = 0
    complete: bool = True
    source: str = "webapi"
    truncated: bool = False
    partial: bool = False

    def since(self, when: datetime | int | None) -> list[ItemDetails]:
        """The items published strictly after ``when``, newest first."""
        cutoff = _stamp(when)
        if cutoff is None:
            return list(self.items)
        return [i for i in self.items if i.created > cutoff]

    def first_after(self, when: datetime | int | None) -> ItemDetails | None:
        """The *oldest* item newer than ``when`` — where a review starts.

        Browsing works forward from the earliest thing not yet seen, so the
        useful item is the last one in a newest-first list, not the first.
        """
        newer = self.since(when)
        return newer[-1] if newer else None

    def page_of(self, item: ItemDetails | str, per_page: int = PER_PAGE) -> int:
        """Roughly which community-listing page an item sits on.

        Approximate on purpose. The listing is ordered the same way this list
        is — newest first — but it does not *hold* the same items: mature
        content is missing from it for a signed-out reader, so an item's index
        here is only near its index there. Good enough to open a browser tab
        close to the right place, not good enough to navigate by.
        """
        item_id = item.id if isinstance(item, ItemDetails) else str(item)
        for index, found in enumerate(self.items):
            if found.id == item_id:
                return index // per_page + 1
        return 1

    def to_json(self) -> dict:
        return {
            "id64": self.id64, "total": self.total, "complete": self.complete,
            "source": self.source, "truncated": self.truncated,
            "partial": self.partial,
            "items": [i.to_json() for i in self.items],
        }

    @classmethod
    def from_json(cls, data: dict) -> "AuthorItems":
        payload = dict(data)
        payload["items"] = [ItemDetails.from_json(i) for i in payload.get("items", [])]
        return cls(**payload)


@dataclass
class WorkshopPage:
    """One page of the community listing — the keyless fallback's unit of work."""

    ids: list[str]
    total: int
    page: int
    per_page: int = PER_PAGE

    @property
    def pages(self) -> int:
        if self.total <= 0:
            return 0
        return (self.total + self.per_page - 1) // self.per_page

    @property
    def has_more(self) -> bool:
        return self.page < self.pages


def is_steam_id64(value: object) -> bool:
    """Whether this is a real steamID64 rather than a name that looks numeric.

    "Is it all digits" is the obvious test and it is wrong. A vanity URL may be
    nothing but digits — `steamcommunity.com/id/328786869` and `/id/97777779`
    are both real authors in this collection — and treating one as an account
    number sends every lookup for them to the wrong place, silently. An
    individual's steamID64 is seventeen digits built on a fixed base, so it
    always begins 7656119.
    """
    text = str(value)
    return len(text) == 17 and text.isdigit() and text.startswith("7656119")


def workshop_url(steam_id: str, page: int = 1, per_page: int = PER_PAGE) -> str:
    """The author's Wallpaper Engine listing — the page a browser tab opens on.

    ``/profiles/`` takes an account number and ``/id/`` a vanity name; sending
    one down the other's path returns a bare 404 page rather than an error.
    """
    kind = "profiles" if is_steam_id64(steam_id) else "id"
    return (f"{COMMUNITY}/{kind}/{steam_id}/myworkshopfiles/"
            f"?appid={APP_ID}&browsefilter=myfiles&view=imagewall"
            f"&p={page}&numperpage={per_page}")


def profile_url(steam_id: str) -> str:
    kind = "profiles" if is_steam_id64(steam_id) else "id"
    return f"{COMMUNITY}/{kind}/{steam_id}/"


# ---- Parsing ---------------------------------------------------------------

_XML_TAG = r"<{0}>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{0}>"

# A profile URL is the only place GetPlayerSummaries reveals the vanity name.
_VANITY_IN_URL = re.compile(r"/id/([^/]+)/?$")


def parse_profile_xml(xml: str) -> Profile:
    """Read a community ``?xml=1`` document into a Profile.

    A vanity name that no longer resolves — the author renamed themselves,
    which is exactly what the old app's "obsolete" scan hunted for — comes back
    as an ``<error>`` document with no id in it.
    """
    def tag(name: str) -> str | None:
        found = re.search(_XML_TAG.format(name), xml, re.S)
        return found.group(1).strip() if found else None

    id64 = tag("steamID64")
    if not id64:
        return Profile(id64=None, exists=False)
    return Profile(
        id64=id64,
        name=tag("steamID") or "",
        vanity=tag("customURL") or None,
        private=(tag("privacyState") or "public").lower() != "public",
        avatar=tag("avatarMedium"),
    )


def parse_summary(player: dict) -> Profile:
    """Read one GetPlayerSummaries entry."""
    url = player.get("profileurl") or ""
    vanity = _VANITY_IN_URL.search(url.rstrip("/") + "/")
    return Profile(
        id64=str(player.get("steamid") or "") or None,
        name=player.get("personaname") or "",
        vanity=vanity.group(1) if vanity else None,
        # 3 is "public"; anything lower hides the profile from strangers.
        private=int(player.get("communityvisibilitystate") or 3) < 3,
        avatar=player.get("avatarmedium"),
    )


# The listing repeats each item as an image link and a title link, and the page
# frame carries unrelated ids of its own; both are why item ids are read out of
# the browse container rather than off the whole document.
_ITEMS_BLOCK = re.compile(r'workshopBrowseItems.*?(?=workshopBrowsePaging|</body>)', re.S)
_ITEM_ID = re.compile(r'sharedfiles/filedetails/\?id=(\d+)')
_TOTAL = re.compile(r'of\s+([\d,\.\s]+?)\s+entries', re.I)


def parse_workshop_page(html: str, page: int, per_page: int = PER_PAGE) -> WorkshopPage:
    """Item ids in listing order (newest first) plus the author's stated total."""
    block = _ITEMS_BLOCK.search(html)
    ids = list(dict.fromkeys(_ITEM_ID.findall(block.group(0) if block else html)))

    total = 0
    found = _TOTAL.search(html)
    if found:
        digits = re.sub(r"[^\d]", "", found.group(1))
        total = int(digits) if digits else 0
    # A single-page listing does not always print a paging line at all.
    total = max(total, len(ids) + (page - 1) * per_page)
    return WorkshopPage(ids=ids, total=total, page=page, per_page=per_page)


# ---- Being a polite guest --------------------------------------------------

class _Throttle:
    """A minimum spacing between requests to one host, shared by every thread.

    :meth:`penalise` moves the gate for everyone, not only for the caller that
    was refused: a 429 is a statement about the host, and letting the other
    threads walk into the same wall turns one refusal into four.
    """

    def __init__(self, interval: float):
        self.interval = interval
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next - now)
            self._next = max(now, self._next) + self.interval
        if delay:
            time.sleep(delay)

    def penalise(self, seconds: float) -> None:
        with self._lock:
            self._next = max(self._next, time.monotonic() + seconds)


class _Cache:
    """Disk cache for answers that do not change, keyed by kind and id."""

    def __init__(self, path: Path):
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS cache ("
                " kind TEXT NOT NULL, key TEXT NOT NULL,"
                " fetched INTEGER NOT NULL, payload TEXT NOT NULL,"
                " PRIMARY KEY (kind, key))")
            self._conn.commit()

    def get(self, kind: str, key: str, ttl: float) -> dict | None:
        if ttl <= 0:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT fetched, payload FROM cache WHERE kind=? AND key=?",
                (kind, key)).fetchone()
        if not row or time.time() - row[0] > ttl:
            return None
        try:
            return json.loads(row[1])
        except json.JSONDecodeError:
            return None

    def put(self, kind: str, entries: dict[str, dict]) -> None:
        if not entries:
            return
        now = int(time.time())
        rows = [(kind, key, now, json.dumps(value, ensure_ascii=False))
                for key, value in entries.items()]
        with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO cache (kind, key, fetched, payload)"
                " VALUES (?, ?, ?, ?)", rows)
            self._conn.commit()

    def forget(self, kind: str, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._conn.execute("DELETE FROM cache WHERE kind=?", (kind,))
            else:
                self._conn.execute("DELETE FROM cache WHERE kind=? AND key=?",
                                   (kind, key))
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()


# ---- The client ------------------------------------------------------------

class SteamClient:
    """Reads authors and their wallpapers off Steam, gently and with a cache."""

    def __init__(self, api_key: str | None = None,
                 cache_path: Path | str | None = CACHE_PATH,
                 workers: int = WORKERS, timeout: float = 20.0, retries: int = 3,
                 api_interval: float = 0.2, web_interval: float = 0.4,
                 on_log: Callable[[str], None] | None = None):
        self.api_key = (api_key or "").strip() or None
        self.workers = max(1, workers)
        self.timeout = timeout
        self.retries = max(1, retries)
        self._log = on_log or (lambda _message: None)
        self._cache = _Cache(Path(cache_path)) if cache_path else None
        self._throttles = {
            "api.steampowered.com": _Throttle(api_interval),
            "steamcommunity.com": _Throttle(web_interval),
        }
        self._fallback_throttle = _Throttle(web_interval)
        self._stats_lock = threading.Lock()
        self.requests = 0
        self.cache_hits = 0
        self.failures = 0

    @property
    def has_key(self) -> bool:
        return bool(self.api_key)

    # -- plumbing ----------------------------------------------------------

    def _throttle(self, url: str) -> _Throttle:
        host = urllib.parse.urlsplit(url).hostname or ""
        return self._throttles.get(host, self._fallback_throttle)

    def _count(self, name: str) -> None:
        with self._stats_lock:
            setattr(self, name, getattr(self, name) + 1)

    def _fetch(self, url: str, data: bytes | None = None) -> str:
        """One request, spaced, retried and backed off.

        The url is never logged as given: a Web API call carries the key in its
        query string, and a log file is exactly where it should not end up.
        """
        throttle = self._throttle(url)
        last: Exception | None = None
        pause = BACKOFF[0]
        for attempt in range(self.retries):
            throttle.wait()
            try:
                request = urllib.request.Request(url, data=data, headers=HEADERS)
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    body = response.read()
                self._count("requests")
                return body.decode("utf-8", "replace")
            except urllib.error.HTTPError as err:
                last = err
                if err.code in (401, 403) and "key=" in url:
                    self._count("failures")
                    raise SteamAuthError(
                        "Steam refused the Web API key "
                        f"(HTTP {err.code}) for {_safe(url)}") from err
                if err.code in (400, 401, 403, 404):
                    self._count("failures")
                    raise SteamError(f"HTTP {err.code} from {_safe(url)}") from err
                pause = _retry_after(err) or BACKOFF[min(attempt, len(BACKOFF) - 1)]
                if err.code == 429:
                    throttle.penalise(pause)
                    self._log(f"Steam asked us to slow down; waiting {pause:.0f}s")
            except (urllib.error.URLError, TimeoutError, OSError) as err:
                last = err
                pause = BACKOFF[min(attempt, len(BACKOFF) - 1)]
            if attempt + 1 < self.retries:
                time.sleep(pause)
        self._count("failures")
        raise SteamError(f"{_safe(url)} failed after {self.retries} attempts: {last}")

    def _api(self, url: str, params: dict) -> dict:
        """A Web API GET returning its ``response`` object."""
        if not self.api_key:
            raise SteamAuthError("this call needs a Steam Web API key")
        query = dict(params)
        query["key"] = self.api_key
        body = self._fetch(f"{url}?{urllib.parse.urlencode(query, doseq=True)}")
        try:
            return json.loads(body).get("response", {})
        except json.JSONDecodeError as err:
            raise SteamError(f"unreadable answer from {_safe(url)}: {err}") from err

    def _parallel(self, func: Callable, jobs: Sequence) -> list:
        if not jobs:
            return []
        if len(jobs) == 1 or self.workers == 1:
            return [func(job) for job in jobs]
        with ThreadPoolExecutor(max_workers=min(self.workers, len(jobs))) as pool:
            return list(pool.map(func, jobs))

    def run_each(self, func: Callable, jobs: Sequence) -> list:
        """Run `func` over `jobs` on this client's own pool of workers.

        Callers with their own per-item work — the review filling one card per
        author — get the same concurrency and the same per-host pacing as the
        batched calls below, instead of inventing a second pool with a second
        idea of how hard Steam may be asked.
        """
        return self._parallel(func, jobs)

    # -- item details (no key needed) --------------------------------------

    def details(self, ids: Iterable[str], refresh: bool = False,
                on_progress: Callable[[int, int], None] | None = None,
                ) -> dict[str, ItemDetails]:
        """Describe every id, in batches of 200, cache first.

        The answer holds an entry for every id asked about, including the ones
        Steam would not describe, so a caller never has to guess whether it
        asked or whether the answer went missing.
        """
        wanted = list(dict.fromkeys(str(i) for i in ids if str(i).isdigit()))
        found: dict[str, ItemDetails] = {}
        missing: list[str] = []

        for item_id in wanted:
            cached = None if refresh or not self._cache else \
                self._cache.get(ITEM_CACHE, item_id, DETAILS_TTL)
            if cached:
                self._count("cache_hits")
                found[item_id] = ItemDetails.from_json(cached)
            else:
                missing.append(item_id)

        if on_progress:
            on_progress(len(found), len(wanted))
        if not missing:
            return {i: found[i] for i in wanted if i in found}

        batches = [missing[i:i + DETAILS_BATCH]
                   for i in range(0, len(missing), DETAILS_BATCH)]
        done = len(found)
        progress_lock = threading.Lock()

        def run(batch: list[str]) -> dict[str, ItemDetails]:
            nonlocal done
            result = self._details_batch(batch)
            self._remember_items(result.values())
            with progress_lock:
                done += len(batch)
                if on_progress:
                    on_progress(min(done, len(wanted)), len(wanted))
            return result

        for chunk in self._parallel(run, batches):
            found.update(chunk)

        return {i: found.get(i, ItemDetails(id=i, ok=False)) for i in wanted}

    def _details_batch(self, ids: list[str]) -> dict[str, ItemDetails]:
        form: dict[str, str] = {"itemcount": str(len(ids))}
        for index, item_id in enumerate(ids):
            form[f"publishedfileids[{index}]"] = item_id
        body = self._fetch(DETAILS_URL, urllib.parse.urlencode(form).encode())
        try:
            payload = json.loads(body)["response"].get("publishedfiledetails") or []
        except (json.JSONDecodeError, KeyError, TypeError) as err:
            raise SteamError(f"unreadable details response: {err}") from err
        out: dict[str, ItemDetails] = {}
        for entry in payload:
            item = ItemDetails.from_steam(entry)
            if item.id:
                out[item.id] = item
        return out

    def _remember_items(self, items: Iterable[ItemDetails]) -> None:
        if self._cache:
            self._cache.put(ITEM_CACHE, {i.id: i.to_json() for i in items if i.id})

    # -- authors -----------------------------------------------------------

    def profiles(self, steam_ids: Iterable[str], refresh: bool = False,
                 on_progress: Callable[[int, int], None] | None = None,
                 ) -> dict[str, Profile]:
        """Look up many authors at once, keyed by what was asked for.

        With a key this is one request per hundred authors; without one it is a
        community page each, which is the single biggest reason a first scan of
        several hundred authors is worth a key.
        """
        wanted = list(dict.fromkeys(str(i).strip() for i in steam_ids if str(i).strip()))
        found: dict[str, Profile] = {}
        missing: list[str] = []

        for steam_id in wanted:
            cached = None if refresh or not self._cache else \
                self._cache.get("profile", steam_id.lower(), PROFILE_TTL)
            if cached:
                self._count("cache_hits")
                found[steam_id] = Profile.from_json(cached)
            else:
                missing.append(steam_id)

        if on_progress:
            on_progress(len(found), len(wanted))
        if not missing:
            return found

        numeric = [i for i in missing if is_steam_id64(i)]
        named = [i for i in missing if not is_steam_id64(i)]
        done = len(found)
        progress_lock = threading.Lock()

        def advance(count: int) -> None:
            nonlocal done
            with progress_lock:
                done += count
                if on_progress:
                    on_progress(min(done, len(wanted)), len(wanted))

        if self.has_key and numeric:
            batches = [numeric[i:i + SUMMARY_BATCH]
                       for i in range(0, len(numeric), SUMMARY_BATCH)]

            def summaries(batch: list[str]) -> dict[str, Profile]:
                result = self._summaries_batch(batch)
                advance(len(batch))
                return result

            for chunk in self._parallel(summaries, batches):
                found.update(chunk)
            # Anything the summaries endpoint did not answer for is a deleted
            # account; record it so it is not asked about again next week.
            for steam_id in numeric:
                found.setdefault(steam_id, Profile(id64=None, exists=False))
                self._remember_profile(steam_id, found[steam_id])
        else:
            named = missing

        def one(steam_id: str) -> tuple[str, Profile]:
            result = self._profile_by_page(steam_id)
            advance(1)
            return steam_id, result

        found.update(dict(self._parallel(one, named)))
        return found

    def profile(self, steam_id: str, refresh: bool = False) -> Profile:
        """The author behind an id or a vanity name, from either direction."""
        key = str(steam_id).strip()
        if not key:
            return Profile(id64=None, exists=False)
        return self.profiles([key], refresh=refresh).get(
            key, Profile(id64=None, exists=False))

    def _summaries_batch(self, ids: list[str]) -> dict[str, Profile]:
        response = self._api(SUMMARIES_URL, {"steamids": ",".join(ids)})
        out: dict[str, Profile] = {}
        for player in response.get("players") or []:
            found = parse_summary(player)
            if found.id64:
                out[found.id64] = found
        return out

    def _profile_by_page(self, steam_id: str) -> Profile:
        """The keyless route: the community profile's own XML view."""
        try:
            found = parse_profile_xml(self._fetch(profile_url(steam_id) + "?xml=1"))
        except SteamError:
            # A dead vanity name is an answer, not a failure: it is how an
            # author who renamed themselves shows up.
            found = Profile(id64=None, exists=False)
        self._remember_profile(steam_id, found)
        return found

    def _remember_profile(self, asked: str, found: Profile) -> None:
        """Cache a profile under every name it answers to."""
        if not self._cache:
            return
        entries = {asked.lower(): found.to_json()}
        for key in found.keys:
            entries[key.lower()] = found.to_json()
        self._cache.put("profile", entries)

    # -- an author's wallpapers --------------------------------------------

    def author_items(self, id64: str, since: datetime | int | None = None,
                     refresh: bool = False, max_pages: int = MAX_AUTHOR_PAGES,
                     on_progress: Callable[[int, int], None] | None = None,
                     ) -> AuthorItems:
        """What an author published for Wallpaper Engine, newest first.

        With a key this is the real list. Without one it is whatever the
        community pages will show a stranger, and the result says so through
        ``complete``.

        ``since`` is the last visit to this author, and it is what keeps a
        prolific one cheap: see :meth:`_author_items_api` for why stopping
        early is safe. Everything newer than the cutoff is still returned in
        full — the saving is in the pages nobody needed to read.
        """
        key = str(id64).strip()
        if not key:
            return AuthorItems(id64=key, complete=self.has_key)
        cutoff = _stamp(since)

        if self._cache and not refresh:
            cached = self._cache.get(AUTHOR_CACHE, key.lower(), AUTHOR_TTL)
            # Two cached lists are unusable however fresh they are: one
            # gathered without a key, now that we have one — the incomplete
            # answer this module exists to avoid — and a partial one, which
            # was cut at somebody else's cutoff.
            if cached and not cached.get("partial") \
                    and (cached.get("complete") or not self.has_key):
                self._count("cache_hits")
                return AuthorItems.from_json(cached)

        if self.has_key:
            found = self._author_items_api(key, cutoff, max_pages, on_progress)
        else:
            found = self._author_items_scraped(key, max_pages, on_progress)

        found.items.sort(key=lambda i: (i.created, i.id), reverse=True)
        self._remember_items(found.items)
        # Only a whole list is worth keeping; a partial one would be served
        # later to a caller asking about a different date.
        if self._cache and not found.partial:
            self._cache.put(AUTHOR_CACHE, {key.lower(): found.to_json()})
        return found

    def _author_items_api(self, id64: str, cutoff: int | None, max_pages: int,
                          on_progress: Callable[[int, int], None] | None,
                          ) -> AuthorItems:
        """Page through GetUserFiles, stopping as soon as it cannot matter.

        The endpoint returns items ordered by ``time_updated``, newest first —
        measured, not documented, and checked again by the test script. Since
        an item is never updated before it was created, anything published
        after the cutoff also has ``time_updated`` after it, and therefore
        appears before any page whose whole contents were last touched
        earlier. So the first such page is the end of the search: for an author
        with 1 204 wallpapers, one request instead of thirteen.

        Ordering by update also means a re-uploaded old wallpaper surfaces at
        the top. That is not new work, and filtering on ``time_created`` keeps
        it out of a review by itself.

        **Which is why the paging is sequential only while there is a cutoff.**
        Then each page decides whether the next is worth asking for, and that
        decision is the saving. A full fetch — opening an author's gallery — has
        no such decision to make: every page will be read, and the first
        response already says how many there are, so the rest are asked for
        together. For the author with 1 271 wallpapers that is 17.1 s of pages
        one after another against 3.6 s of them overlapping.
        """
        found = AuthorItems(id64=id64, complete=True, source="webapi")
        lock = threading.Lock()

        def ask(page: int) -> dict:
            return self._api(USER_FILES_URL, {
                "steamid": id64,
                "appid": APP_ID,
                "page": page,
                "numperpage": USER_FILES_PER_PAGE,
                "return_metadata": 1,
                "return_previews": 1,
                # The type of a wallpaper is a tag, not a field.
                "return_tags": 1,
                "return_short_description": 0,
            })

        def take(response: dict) -> tuple[int, int | None]:
            """Absorb one page. Returns its size and the oldest touch in it."""
            batch = response.get("publishedfiledetails") or []
            oldest_touch = None
            items: list[ItemDetails] = []
            for entry in batch:
                item = ItemDetails.from_steam(entry)
                if not item.id:
                    continue
                item.creator = item.creator or id64
                items.append(item)
                touched = item.updated or item.created
                oldest_touch = touched if oldest_touch is None else min(oldest_touch, touched)
            with lock:
                found.items.extend(items)
                found.total = max(found.total, int(response.get("total") or 0))
                if on_progress:
                    on_progress(len(found.items), found.total or len(found.items))
            return len(batch), oldest_touch

        size, oldest_touch = take(ask(1))
        done = size < USER_FILES_PER_PAGE or (0 < found.total <= len(found.items))

        if not done and cutoff is None and found.total:
            # Nothing can cut this search short, and the total is known: ask for
            # every remaining page at once. The per-host throttle still paces
            # them, so Steam sees the same rate, only the waiting overlaps.
            last = -(-found.total // USER_FILES_PER_PAGE)
            rest = list(range(2, min(last, max_pages) + 1))
            for response in self._parallel(ask, rest):
                take(response)
            if last > max_pages:
                found.truncated = True
        elif not done:
            page = 2
            while page <= max_pages:
                if cutoff is not None and oldest_touch is not None and oldest_touch <= cutoff:
                    found.partial = True
                    break
                size, oldest_touch = take(ask(page))
                if size < USER_FILES_PER_PAGE or (0 < found.total <= len(found.items)):
                    break
                page += 1
            else:
                found.truncated = True
        found.total = max(found.total, len(found.items))
        return found

    def _author_items_scraped(self, steam_id: str, max_pages: int,
                              on_progress: Callable[[int, int], None] | None,
                              ) -> AuthorItems:
        found = AuthorItems(id64=steam_id, complete=False, source="listing")
        ids: list[str] = []
        page = 1
        while page <= max_pages:
            listing = self.workshop_page(steam_id, page)
            fresh = [i for i in listing.ids if i not in ids]
            if not fresh:
                break
            ids.extend(fresh)
            found.total = max(found.total, listing.total)
            if on_progress:
                on_progress(len(ids), found.total or len(ids))
            if not listing.has_more:
                break
            page += 1
        else:
            found.truncated = True
        described = self.details(ids)
        found.items = [described[i] for i in ids if i in described]
        found.total = max(found.total, len(found.items))
        return found

    def workshop_page(self, steam_id: str, page: int = 1,
                      per_page: int = PER_PAGE, refresh: bool = False) -> WorkshopPage:
        """One page of the community listing. Incomplete by nature — see above."""
        cache_key = f"{steam_id}:{page}:{per_page}".lower()
        if self._cache and not refresh:
            cached = self._cache.get("listing", cache_key, LISTING_TTL)
            if cached:
                self._count("cache_hits")
                return WorkshopPage(**cached)
        try:
            html = self._fetch(workshop_url(str(steam_id), page, per_page))
        except SteamError as err:
            self._log(f"listing unavailable for {steam_id}: {err}")
            return WorkshopPage(ids=[], total=0, page=page, per_page=per_page)
        found = parse_workshop_page(html, page, per_page)
        if self._cache:
            self._cache.put("listing", {cache_key: {
                "ids": found.ids, "total": found.total,
                "page": found.page, "per_page": found.per_page}})
        return found

    # -- housekeeping ------------------------------------------------------

    def check_key(self) -> bool:
        """Ask Steam whether the configured key works. Raises if it does not."""
        if not self.has_key:
            return False
        self._api(SUMMARIES_URL, {"steamids": "76561197960435530"})
        return True

    @property
    def stats(self) -> dict[str, int]:
        return {"requests": self.requests, "cache_hits": self.cache_hits,
                "failures": self.failures}

    def forget(self, kind: str, key: str | None = None) -> None:
        if self._cache:
            self._cache.forget(kind, key)

    def close(self) -> None:
        if self._cache:
            self._cache.close()

    def __enter__(self) -> "SteamClient":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


def _kind_of(tags) -> str:
    """Scene, Video, Web or Application, read out of an item's tag list."""
    for tag in tags or []:
        name = tag.get("tag") if isinstance(tag, dict) else tag
        if name in WALLPAPER_KINDS:
            return name
    return ""


def _retry_after(err: urllib.error.HTTPError) -> float | None:
    """Steam's own Retry-After, when it sends one."""
    value = err.headers.get("Retry-After") if err.headers else None
    if not value:
        return None
    try:
        return max(1.0, float(value))
    except ValueError:
        return None


def _stamp(when: datetime | int | None) -> int | None:
    """A cutoff as a unix timestamp, however it was handed over."""
    if when is None:
        return None
    if isinstance(when, datetime):
        return int(when.timestamp())
    return int(when)


def _safe(url: str) -> str:
    """A url fit for a log line: the Web API key taken back out of it."""
    return re.sub(r"([?&]key=)[^&]*", r"\1<hidden>", url)
