"""Made-up states of the Review page, for `tools/ui_snapshot.py` and the tests.

Everything comes from `tests/fixtures/ui/review.json` (a source checkout's):
invented authors, account numbers and titles, counts that look like a week.
No Steam, no authors database, no Wallpaper Engine: the cards, the result
and the session are built here as a scan would have left them, and the
gallery's wallpapers have no preview, so nothing is downloaded.
"""
from __future__ import annotations

import json
from datetime import date, datetime, time as day_time, timedelta, timezone
from pathlib import Path

from ..engines import review as rv
from ..engines.authors_store import Author
from ..engines.review_flow import STOPPED, ScanOutcome, Session, plain_reason
from ..engines.steam_api import ItemDetails, Profile, SteamUnreachable
from .review_settings import (
    BY_STEAM, AuthorsDialog, Backup, ReviewSettings, ReviewSettingsDialog, StoreFacts,
)

FIXTURE_FILE = (Path(__file__).resolve().parent.parent.parent
                / "tests" / "fixtures" / "ui" / "review.json")

KINDS = ("Scene", "Video", "Web")
WORDS = ("Harbour", "Lantern", "Signal", "Paper", "Static", "Orchard", "Ember", "Window",
         "Meadow", "Neon", "Quiet", "Copper", "Drift", "Hollow", "Tide", "Glass")
PLACES = ("Room", "Loop", "Rain", "Morning", "Street", "Field", "Garden", "Station")


def read() -> dict:
    data = json.loads(FIXTURE_FILE.read_text(encoding="utf-8"))
    data.pop("//", None)
    return data


def frame_for(state: str) -> dict:
    """The frame's state that goes with a page state, and the Review item in
    the sidebar as that state has it."""
    nav = {
        "empty": {"kind": "none"},
        "settings": {"kind": "none"},
        "authors": {"kind": "none"},
        "error": {"kind": "status", "text": "stopped", "tone": "danger", "below": True},
        "reviewing": {"kind": "badge", "value": 10},
        "done": {"kind": "status", "text": "finished", "tone": "ok", "below": True},
    }
    if state == "scanning":
        return {"frame": "scanning"}
    return {"frame": "idle", "nav": {"review": nav[state]}}


def _at(now: datetime, clock: str, days_ago: int = 0) -> datetime:
    hour, minute = (int(part) for part in clock.split(":"))
    return datetime.combine(now.date() - timedelta(days=days_ago), day_time(hour, minute))


def _stamp(now: datetime, days_ago: float) -> int:
    return int((now - timedelta(days=days_ago)).timestamp())


def cards(data: dict, now: datetime) -> list[rv.AuthorCard]:
    """The authors as a scan would have filled them: their wallpapers
    published since the last visit, the ones you had before marked."""
    made = []
    n = 0
    for a in data["authors"]:
        records = []
        if a["state"] in ("known", "duplicate"):
            visited = (now - timedelta(days=a["visited_days_ago"])).astimezone(timezone.utc)
            record = Author(steam_id=a["id"], name=a.get("was", a["name"]),
                            added=visited - timedelta(days=200), visited=visited)
            records = [record] if a["state"] == "known" else \
                [record, Author(steam_id=a["name"].lower().replace(" ", ""), name=a["name"],
                                added=visited - timedelta(days=400), visited=None)]
        card = rv.AuthorCard(id64=a["id"], profile=Profile(id64=a["id"], name=a["name"]),
                             records=records, queued=[f"{a['id'][-4:]}0"],
                             appeared=now - timedelta(days=3))
        items = []
        for i in range(a["new"]):
            n += 1
            title = f"{WORDS[n % len(WORDS)]} {PLACES[(n * 3) % len(PLACES)]}"
            item = ItemDetails(id=str(9_100_000_000 + n), ok=True, creator=a["id"],
                               title=title, created=_stamp(now, 1 + i * 0.7),
                               updated=_stamp(now, 1 + i * 0.7), preview="",
                               kind=KINDS[n % len(KINDS)],
                               file_size=(40 + (n * 37) % 380) * 1024 ** 2)
            wallpaper = rv.Wallpaper(item=item, once_had=i < a.get("yours", 0),
                                     owned_checked=True)
            items.append(wallpaper)
        card.items, card.total = items, a["new"] + 40 + len(a["name"]) * 3
        card.filled = card.deep = card.owned_checked = True
        card.complete = not a.get("incomplete", False)
        made.append(card)
    return made


def result(data: dict, now: datetime) -> rv.ReviewResult:
    made = cards(data, now)
    found = rv.ReviewResult(cards=made, queue=[c.queued[0] for c in made],
                            remembered=len(made), scope=data["scope"])
    found.counts = {"authors": len(made), "filled": len(made)}
    return found


def session(data: dict, now: datetime, found: rv.ReviewResult) -> Session:
    s = Session.from_result(found, scanned=_at(now, "13:47"), scope=data["scope"])
    s.checked = data["checked"]
    s.nothing_new = data["checked"] - len(s.authors)
    s.seconds = 112.0
    return s


def last(data: dict, now: datetime) -> dict:
    spec = data["last"]
    scanned = _at(now, spec["scanned"], spec["scanned_days_ago"])
    finished = _at(now, spec["finished"], spec["finished_days_ago"])
    return {"format": 1, "scanned": scanned.isoformat(timespec="seconds"),
            "scope": data["scope"], "since": (scanned.date() - timedelta(days=12)).isoformat(),
            "items": spec["items"],
            "authors": [{"name": f"author {i + 1}", "new": 1, "done": True}
                        for i in range(spec["authors"])],
            "checked": spec["checked"], "finished": finished.isoformat(timespec="seconds"),
            "seconds": spec["seconds"]}


def _subscribe(s: Session, found: rv.ReviewResult, counts: dict) -> None:
    by_id = {c.id64: c for c in found.cards}
    for author_id, count in counts.items():
        card = by_id[author_id]
        for wallpaper in card.items[:count]:
            wallpaper.subscribed = True
            s.note_subscribed(author_id, wallpaper.id)


def fixture_key() -> str:
    """A key-shaped value for the masked field, made here so no file holds
    one (the publish audit looks for 32 hex characters)."""
    return "".join("0123456789ABCDEF"[(i * 7) % 16] for i in range(32))


def load(page, state: str) -> None:
    """Put `page` in a made-up state."""
    from .review import ScanProgress, last_state
    data = read()
    now = datetime.combine(date.today(), datetime.strptime(data["now"], "%H:%M").time())
    page._fixture = True
    page._now = lambda: now
    page._watch.stop()
    page.flow = None
    page.settings.set("review", "scope", data["scope"])
    page._last_raw = last(data, now)
    page._last = last_state(page._last_raw)
    page.keyless.hide()
    page._folders = dict(data["folders"])
    found = result(data, now)
    page.cards = {c.id64: c for c in found.cards}
    page.result = found
    page.session = None
    page.current = None
    spec = data["states"].get(state, {})

    if state in ("empty", "settings", "authors"):
        page._set_state("empty")
        if state == "settings":
            dialog = ReviewSettingsDialog(
                ReviewSettings(scope=data["scope"], subscribe=BY_STEAM, key=fixture_key()),
                page._dialog_parent(), folders=data["folders"],
                stored="stored, encrypted (••••" + fixture_key()[-4:] + ")",
                on_authors=lambda: None, tester=lambda _value: "")
            dialog.set_steamworks("Steam directly subscribes from this window through Wallpaper "
                                  "Engine's own Steamworks library; Steam must be running.")
            _show(page, dialog)
        elif state == "authors":
            dialog = AuthorsDialog(page.settings, page._dialog_parent())
            dialog.take(StoreFacts(
                spec["count"], "", False, Path(r"X:\Toolkit\data\authors.sqlite"), spec["size"],
                tuple(_backup(now, *row) for row in spec["backups"])))
            _show(page, dialog)
        return

    if state == "scanning":
        scan = ScanProgress(data["scope"], _at(now, spec["started"]))
        scan.phase, scan.authors, scan.checked = "count", spec["total"], spec["checked"]
        scan.current = spec["current"]
        scan.found = [(author_id, page.cards[author_id]) for author_id in spec["found"]]
        page.scan = scan
        page._fixture_left = spec["left"]
        page.found.clear()
        page._set_state("scanning")
        return

    if state == "error":
        for card in found.cards:
            card.filled = card.id64 in spec["found"]
        error = SteamUnreachable("", host=spec["host"], reason=spec["reason"],
                                 attempts=spec["attempts"])
        page.outcome = ScanOutcome(STOPPED, found, "count", spec["stopped_at"], spec["total"],
                                   spec["stopped_at"], error, plain_reason(error))
        stopped = _at(now, spec["time"])
        page._stopped_at = stopped
        page._log_tail.clear()
        page._log_tail.extend([
            (stopped, "error", f"{spec['host']} — {spec['reason']} after {spec['attempts']} tries"),
            (stopped, "info", f"stopped at {spec['current']} "
                              f"({spec['stopped_at'] + 1} / {spec['total']})")])
        page._set_state("stopped")
        return

    s = session(data, now, found)
    page.session = s
    page._owner = {w.id: c.id64 for c in found.cards for w in c.items}
    order = page._ordered_ids(s)
    if state == "reviewing":
        for author_id in order[:spec["done"]]:
            s.mark_done(author_id)
        _subscribe(s, found, spec["subscribed"])
        page._set_state("reviewing")
        page.open_author(order[spec["current"]])
        return

    if state == "done":
        for author in s.authors:
            author.done = True
        _subscribe(s, found, spec["subscribed"])
        s.finished = _at(now, spec["finished"])
        s.written = {"created": spec["created"], "updated": spec["updated"],
                     "backup": spec["backup"]}
        page._set_state("done")


def _backup(now: datetime, when: str, count: int, size: int, where: str) -> Backup:
    days, clock = when.split(" ")
    taken = _at(now, clock, int(days))
    name = f"authors-{taken:%Y%m%d-%H%M%S}-{count}.json.gz"
    return Backup(Path(r"X:\Toolkit\data\authors_backup") / name, taken, count, where, size)


def _show(page, dialog) -> None:
    page.fixture_dialog = dialog
    page.last_dialog = dialog
    dialog.show()
