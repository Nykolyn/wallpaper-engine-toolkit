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
        "gallery-grid": {"kind": "badge", "value": 10},
        "gallery-list": {"kind": "badge", "value": 10},
        "done": {"kind": "status", "text": "finished", "tone": "ok", "below": True},
        "review-list": {"kind": "status", "text": "finished", "tone": "ok", "below": True},
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


def preview_image(seed: int, width: int = 512, height: int = 512):
    """A made-up preview: a gradient in one of a few hues with soft shapes over
    it, drawn here, so no real wallpaper's preview is ever in a picture.
    Mostly square, as Workshop previews are; every fifth one wide."""
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QRadialGradient
    if seed % 5 == 4:
        height = width * 9 // 16
    hues = (212, 268, 188, 24, 330, 150, 46, 250)
    hue = hues[seed % len(hues)]
    image = QImage(width, height, QImage.Format_RGB32)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    ground = QLinearGradient(QPointF(0, 0), QPointF(width, height))
    ground.setColorAt(0, QColor.fromHsv(hue, 150, 120))
    ground.setColorAt(1, QColor.fromHsv((hue + 40) % 360, 190, 40))
    painter.fillRect(image.rect(), ground)
    painter.setPen(Qt.NoPen)
    for k in range(4):
        r = width * (0.12 + 0.07 * ((seed + k) % 4))
        x = width * ((seed * 37 + k * 53) % 100) / 100
        y = height * ((seed * 61 + k * 29) % 100) / 100
        glow = QRadialGradient(QPointF(x, y), r)
        glow.setColorAt(0, QColor.fromHsv((hue + 20 * k) % 360, 90, 255, 200))
        glow.setColorAt(1, QColor.fromHsv((hue + 20 * k) % 360, 90, 255, 0))
        painter.setBrush(glow)
        painter.drawEllipse(QRectF(x - r, y - r, 2 * r, 2 * r))
    painter.end()
    return image


def _gallery(page, data: dict, spec: dict, s: Session, found: rv.ReviewResult, order) -> None:
    """The gallery of frames 12 and 13: an author's fourteen cards in every
    mark, previews drawn here, one card under the pointer, one being
    subscribed to, three selected."""
    from PySide6.QtCore import QByteArray
    from ..engines.library import RESERVE
    from ..ui.gallery import SUBSCRIBING, WAITING
    author_id = spec["author"]
    card = page.cards[author_id]
    for i, wallpaper in enumerate(card.items):
        mark = spec["marks"][i % len(spec["marks"])]
        wallpaper.item.kind = spec["kinds"][i % len(spec["kinds"])].capitalize()
        wallpaper.item.file_size = spec["sizes_mb"][i % len(spec["sizes_mb"])] * 1024 ** 2
        wallpaper.once_had = mark == "yours"
        wallpaper.in_library = mark == "have"
        wallpaper.library_place = RESERVE if mark == "have" else ""
        wallpaper.subscribed = mark == "subscribed"
        wallpaper.item.preview = "fixture:"
        if wallpaper.subscribed:
            s.note_subscribed(author_id, wallpaper.id)
    author = s.find(author_id)
    author.yours = sum(1 for w in card.items if w.once_had)
    author.have = sum(1 for w in card.items if w.in_library)
    page.open_author(author_id)
    gallery = page.gallery
    gallery.loader.stopped = True           # nothing is fetched: the previews are drawn here
    shown = gallery.current_page()
    for i, wallpaper in enumerate(shown):
        gallery.model_.set_image(wallpaper.id, QByteArray(b"made-up"),
                                 preview_image(i + 14))
    gallery.mark_busy(shown[spec["subscribing"]].id, SUBSCRIBING)
    for i in spec["waiting"]:
        gallery.mark_busy(shown[i].id, WAITING)
    gallery.model_.select(shown[i].id for i in spec["selected"])
    gallery.force_hover(spec["hover"])
    page.gallery_list._hover_to(spec["hover"])


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

    if state in ("gallery-grid", "gallery-list"):
        spec = data["states"]["gallery"]
        for author_id in order[:spec["done"]]:
            s.mark_done(author_id)
        _subscribe(s, found, spec["subscribed"])
        page._set_state("reviewing")
        page.gallery_panel.set_mode("list" if state == "gallery-list" else "grid")
        page.settings.set("review", "view", "list" if state == "gallery-list" else "grid")
        _gallery(page, data, spec, s, found, order)
        return

    if state in ("done", "review-list"):
        spec = data["states"]["done"]
        for author in s.authors:
            author.done = True
        _subscribe(s, found, spec["subscribed"])
        s.finished = _at(now, spec["finished"])
        s.written = {"created": spec["created"], "updated": spec["updated"],
                     "backup": spec["backup"]}
        page._set_state("done")
        if state == "review-list":
            page.gallery.loader.stopped = True
            page.open_review_list()
            for i, wallpaper in enumerate(page.gallery.current_page()[:40]):
                from PySide6.QtCore import QByteArray
                page.gallery.model_.set_image(wallpaper.id, QByteArray(b"made-up"),
                                              preview_image(i))


def _backup(now: datetime, when: str, count: int, size: int, where: str) -> Backup:
    days, clock = when.split(" ")
    taken = _at(now, clock, int(days))
    name = f"authors-{taken:%Y%m%d-%H%M%S}-{count}.json.gz"
    return Backup(Path(r"X:\Toolkit\data\authors_backup") / name, taken, count, where, size)


def _show(page, dialog) -> None:
    page.fixture_dialog = dialog
    page.last_dialog = dialog
    dialog.show()
