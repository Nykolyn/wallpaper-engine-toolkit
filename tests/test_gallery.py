"""The Review tab's drawing layer, checked without a window on screen.

Run it directly — there is no test framework in this project:

    .venv\\Scripts\\python.exe tests\\test_gallery.py

Delegates are where a tab actually breaks. They run for every visible card, on
data that varies more than the happy path suggests — a wallpaper with no title,
an author Steam would not name, a card whose counts have not arrived yet — and
a mistake there is an exception per repaint rather than a wrong number. So each
one is painted here onto a pixmap, in every state it has, and the pixmap is
checked for having something on it.

The rest is the behaviour a mouse produces: a subscribed card must not offer
itself again, a preview must be asked for once, and an animation must only be
started for something that is actually animated.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import (  # noqa: E402
    QByteArray, QBuffer, QEvent, QIODevice, QPointF, QRect, Qt)
from PySide6.QtGui import (  # noqa: E402
    QColor, QImage, QMouseEvent, QPainter, QPixmap)
from PySide6.QtWidgets import (                                              # noqa: E402
    QApplication, QStyle, QStyleOptionViewItem)

app = QApplication.instance() or QApplication([])

from app import theme                                       # noqa: E402
theme.apply(app)

import app.ui.gallery as gal                                # noqa: E402
import app.ui.review_tab as tab_mod                         # noqa: E402
import app.engines.review as rv                             # noqa: E402
from app.engines.authors_store import Author                # noqa: E402
from app.engines.steam_api import ItemDetails, Profile      # noqa: E402

results: list[bool] = []


def check(label: str, condition: bool) -> None:
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label)


def wallpaper(item_id="1", title="A wallpaper", kind="Scene",
              size=6 * 1024 ** 2, **flags) -> rv.Wallpaper:
    item = ItemDetails(id=item_id, ok=True, creator="76561198000000001",
                       title=title, created=1750000000, updated=1750000000,
                       preview="https://example/p.jpg", kind=kind, file_size=size)
    return rv.Wallpaper(item=item, **flags)


def painted(delegate, index, width=gal.CARD_W, height=gal.CARD_H,
            state=None) -> QImage:
    """Paint one item and hand back what landed on the canvas."""
    pixmap = QPixmap(width, height)
    pixmap.fill(QColor("#000000"))
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, width, height)
    if state is not None:
        option.state = state
    painter = QPainter(pixmap)
    try:
        delegate.paint(painter, option, index)
    finally:
        painter.end()
    return pixmap.toImage()


def has_ink(image: QImage) -> bool:
    """Whether anything was drawn — the canvas started black."""
    return gal._brightness(image) > 0.01


# ---- The gallery model ------------------------------------------------------

model = gal.GalleryModel()
check("an empty model has no rows", model.rowCount() == 0)

items = [wallpaper("1"), wallpaper("2", once_had=True), wallpaper("3", subscribed=True)]
model.set_items(items)
check("its rows are the wallpapers it was given", model.rowCount() == 3)
check("each row hands back the wallpaper itself",
      model.data(model.index(1, 0), gal.WALLPAPER) is items[1])
check("and its title, for the tooltip",
      model.data(model.index(0, 0), Qt.ToolTipRole) == "A wallpaper")
check("a row outside the list is nothing, not a crash",
      model.data(model.index(9, 0), gal.WALLPAPER) is None)
check("no image has arrived yet",
      model.data(model.index(0, 0), gal.IMAGE) is None)

frame = QImage(40, 25, QImage.Format_RGB32)
frame.fill(QColor("#4488ff"))
model.set_image("1", QByteArray(b"\xff\xd8fake"), frame)
stored = model.data(model.index(0, 0), gal.IMAGE)
check("an arriving frame becomes a pixmap scaled to a card",
      stored is not None and stored.width() >= gal.THUMB_W)
check("and the bytes are kept, because animation needs the original",
      model.raw("1") is not None)
model.set_image("2", QByteArray(), QImage())
check("an image that never arrived changes nothing",
      model.data(model.index(1, 0), gal.IMAGE) is None)


# ---- Painting a wallpaper card ---------------------------------------------

delegate = gal.GalleryDelegate()
size = delegate.sizeHint(QStyleOptionViewItem(), model.index(0, 0))
check("every card is the same size, which is what lets the grid virtualise",
      size.width() == gal.CARD_W and size.height() == gal.CARD_H)

states = {
    "plain": {},
    "new to you": {"owned_checked": True},
    "in the queue": {"in_queue": True},
    "owned once": {"once_had": True, "owned_checked": True},
    "subscribed": {"subscribed": True},
    "everything at once": {"owned_checked": True, "in_queue": True,
                           "once_had": True, "subscribed": True},
    "no title at all": {},
}
for label, flags in states.items():
    one = gal.GalleryModel()
    card = wallpaper(title="" if label == "no title at all" else "A wallpaper", **flags)
    one.set_items([card])
    image = painted(delegate, one.index(0, 0))
    check(f"a card paints when it is {label}", has_ink(image))

for label, extra in {"a video of a gigabyte": {"kind": "Video", "size": 2 * 1024 ** 3},
                     "a web wallpaper": {"kind": "Web"},
                     "an application": {"kind": "Application"},
                     "a preset": {"kind": "Preset"},
                     "something Steam never tagged": {"kind": "", "size": 0}}.items():
    one = gal.GalleryModel()
    one.set_items([wallpaper(**extra)])
    check(f"and when it is {label}", has_ink(painted(delegate, one.index(0, 0))))


# ---- What a card is allowed to claim ---------------------------------------
#
# "new" used to be painted from `new_since_visit`, which is true of every card
# in the gallery — so a wallpaper downloaded and deleted twice still called
# itself new. It is the answer to "has this machine ever had it", and until
# something has actually asked, a card says nothing.

check("a card claims nothing about being new until ownership is checked",
      not wallpaper().unseen)
check("once checked, one never subscribed and never copied is new to you",
      wallpaper(owned_checked=True).unseen)
check("one you had before is not new, however long ago it was",
      not wallpaper(owned_checked=True, once_had=True).unseen)
check("and neither is one you are subscribed to this minute",
      not wallpaper(owned_checked=True, subscribed=True).unseen)


# ---- What it is, and what it costs -----------------------------------------

check("a download under a gigabyte is stated plainly",
      not wallpaper(size=1023 * 1024 ** 2).item.large)
check("and one at a gigabyte or over is a warning",
      wallpaper(size=1024 ** 3).item.large)
check("every kind Wallpaper Engine publishes reads as its own colour",
      len({theme.kind_color(k) for k in gal.KIND_MARKS}) == len(gal.KIND_MARKS))
check("and an untagged wallpaper gets the neutral chip, not a gap",
      theme.kind_color("") == theme.C["raised"])
check("each kind has a glyph to be recognised by before the word is read",
      set(gal.KIND_MARKS) == {"Scene", "Video", "Web", "Application", "Preset"})


# ---- A selected card has to stay readable ----------------------------------
#
# Selection used to fill the whole card with the pressed accent colour while
# the title stayed `text` and the size and date stayed `faint`: on solid blue,
# the two facts the decision is made on were barely visible. It is a border
# now, and the panel under the type does not move.

chosen = gal.GalleryModel()
chosen.set_items([wallpaper()])
image = painted(delegate, chosen.index(0, 0), state=QStyle.State_Selected)
under_text = image.pixelColor(200, gal.CARD_H - 12)
check("a selected card keeps the panel its title is read on",
      under_text.name().lower() == theme.C["raised"].lower())
check("and is marked out by its border instead",
      image.pixelColor(gal.CARD_W // 2, 5).name().lower()
      == theme.C["accent"].lower())

one = gal.GalleryModel()
one.set_items([wallpaper()])
delegate.busy.add("1")
check("and while it is being subscribed to", has_ink(painted(delegate, one.index(0, 0))))
delegate.busy.discard("1")

empty = gal.GalleryModel()
empty.set_items([])
check("painting a row that is not there does nothing rather than raising",
      not has_ink(painted(delegate, empty.index(0, 0))))


# ---- Painting an author row -------------------------------------------------

author_delegate = tab_mod.AuthorDelegate()
known = Author(name="Alice", steam_id="76561198000000001",
               added=datetime(2024, 1, 1, tzinfo=timezone.utc),
               visited=datetime(2026, 1, 1, tzinfo=timezone.utc))


class FakeIndex:
    """Enough of a model index for a delegate: it only reads one role."""

    def __init__(self, card):
        self.card = card

    def data(self, role=Qt.DisplayRole):
        return self.card if role == tab_mod.CARD else None


cards = {
    "a new author": rv.AuthorCard(id64="76561198000000001",
                                  profile=Profile(id64="1", name="Bob")),
    "a known one": rv.AuthorCard(id64="76561198000000001",
                                 profile=Profile(id64="1", name="Alice"),
                                 records=[known], queued=["1", "2"]),
    "a duplicated one": rv.AuthorCard(id64="76561198000000001",
                                      profile=Profile(id64="1", name="Alice"),
                                      records=[known, known]),
    "one Steam will not name": rv.AuthorCard(id64="76561198000000001",
                                             profile=Profile(id64=None, exists=False)),
}
for label, card in cards.items():
    check(f"an author row paints for {label}",
          has_ink(painted(author_delegate, FakeIndex(card), 300, 54)))

filled = cards["a known one"]
filled.items = [wallpaper("9")]
filled.filled = True
check("a row with its count in paints the badge",
      has_ink(painted(author_delegate, FakeIndex(filled), 300, 54)))
check("and a row whose count has not arrived paints a placeholder",
      has_ink(painted(author_delegate, FakeIndex(cards["a new author"]), 300, 54)))
check("a row with no card at all is skipped",
      not has_ink(painted(author_delegate, FakeIndex(None), 300, 54)))


# ---- What the mouse does ----------------------------------------------------

view = gal.GalleryView()
view.resize(700, 500)
offered = wallpaper("11")
taken = wallpaper("12", subscribed=True)
view.show_items([offered, taken])
check("the view shows what it was handed",
      [w.id for w in view.showing()] == ["11", "12"])

asked: list[str] = []
view.subscribe_requested.connect(asked.append)


def Click(point, button):
    """A real mouse event: the view hands it on to Qt, so a stub will not do."""
    return QMouseEvent(QEvent.MouseButtonRelease, QPointF(point), button, button,
                       Qt.NoModifier)


rect_one = view.visualRect(view.model_.index(0, 0))
rect_two = view.visualRect(view.model_.index(1, 0))
view.mouseReleaseEvent(Click(rect_one.center(), Qt.LeftButton))
check("clicking a card that is on offer asks for a subscription", asked == ["11"])
view.mouseReleaseEvent(Click(rect_two.center(), Qt.LeftButton))
check("clicking one already subscribed asks for nothing", asked == ["11"])

opened: list[str] = []
view.open_requested.connect(opened.append)
view.mouseReleaseEvent(Click(rect_two.center(), Qt.RightButton))
check("but the right button still opens it in Steam", opened == ["12"])

view.mark_busy("11", True)
check("a card can be shown as in progress", "11" in view.delegate.busy)
view.mark_busy("11", False)
check("and told when it is over", "11" not in view.delegate.busy)


# ---- Asking for previews once -----------------------------------------------

loader = gal.ThumbLoader(threads=1)
loader.stop()          # nothing is going to run; only the bookkeeping is tested
loader.request("42", "https://example/a.jpg")
loader.request("42", "https://example/a.jpg")
check("a preview is only ever asked for once", loader._asked == {"42"})
loader.request("43", None)
check("and a wallpaper with no preview is not asked for at all",
      "43" not in loader._asked)
check("the cache path is keyed by the wallpaper's own id",
      loader.path_for("42").name.startswith("42"))


# ---- Animation is only for what is animated ---------------------------------

still_view = gal.GalleryView()
still_view.resize(900, 700)
still_view.show_items([wallpaper("50"), wallpaper("51")])
still_view.model_._raw["50"] = QByteArray(b"\xff\xd8\xff\xe0 jpeg bytes")
still_view._sync_players()
check("a still preview starts no animation", "50" not in still_view._players)

still_view.model_._raw["51"] = QByteArray(b"GIF89a" + b"\x00" * 20)
still_view.model_._images["51"] = QPixmap(gal.THUMB_W, gal.THUMB_H)
still_view._sync_players()
check("an animated one plays without waiting for the cursor",
      "51" in still_view._players)
still_view._stop_all()
check("and everything stops together when the gallery is replaced",
      not still_view._players and not still_view.delegate.movies)


# ---- Pages ------------------------------------------------------------------

# Fetching a thousand previews to look at the newest few is the wait this
# removes; it also fixes how many decoders the animation needs.
paged = gal.GalleryView()
paged.resize(900, 700)
seen_pages: list[tuple] = []
paged.page_changed.connect(lambda p, n, t: seen_pages.append((p, n, t)))
paged.show_items([wallpaper(str(i)) for i in range(70)])
check("a long gallery is cut into pages", paged.pages == 3 and paged.total == 70)
check("and only one page is in the model at a time",
      paged.model_.rowCount() == gal.PAGE_SIZE)
check("the first page starts at the newest wallpaper",
      paged.model_.item_at(0).id == "0")

paged.next_page()
check("the next page follows on",
      paged.page == 2 and paged.model_.item_at(0).id == str(gal.PAGE_SIZE))
paged.set_page(99)
check("asking past the end lands on the last page", paged.page == 3)
check("which holds the remainder", paged.model_.rowCount() == 70 - 2 * gal.PAGE_SIZE)
paged.set_page(-5)
check("and asking before the start lands on the first", paged.page == 1)
check("every move is announced, so the buttons know what to say",
      seen_pages[-1] == (1, 3, 70))
check("but the gallery still knows about every wallpaper, not just this page",
      len(paged.showing()) == 70)

paged.show_items([wallpaper("x")])
check("a short gallery is a single page", paged.pages == 1 and paged.page == 1)


# ---- How many previews may move at once ------------------------------------
#
# Steam animates its whole grid and so does Wallpaper Engine, so a wall that
# only moves under the cursor reads as broken — but a page holds thirty, and
# building thirty decoders on the GUI thread in the burst their downloads land
# in is what stopped the window answering at all. Measured on one author: the
# window froze outright, and short of that, twenty-one players came to 178
# frames a second and 334 ms of lag. Capped and started on a clock: 8 players,
# 72 frames a second, 76 ms.

def animated_gif() -> bytes:
    """The smallest thing Qt will accept as an animated GIF."""
    return (b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff"
            b"!\xff\x0bNETSCAPE2.0\x03\x01\x00\x00\x00"
            b"!\xf9\x04\x04\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00"
            b"\x00\x02\x02D\x01\x00"
            b"!\xf9\x04\x04\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00"
            b"\x00\x02\x02D\x01\x00;")


moving = gal.GalleryView()
moving.resize(1200, 900)
many = [wallpaper(str(900 + i)) for i in range(gal.PAGE_SIZE)]
moving.show_items(many)
check("a page holds more cards than may animate",
      moving.model_.rowCount() > gal.MAX_PLAYERS)

gif = QByteArray(animated_gif())
frame = QImage(4, 4, QImage.Format_RGB32)
frame.fill(0xFF808080)
for card in many:
    moving._image_arrived(card.id, gif, frame)

check("an arriving preview does not build its player there and then",
      moving._players == {})
check("it asks for the players to be worked out instead",
      moving._players_due.isActive())

moving._sync_players()
check("no more than the cap animate at once",
      len(moving._players) == gal.MAX_PLAYERS)
on_screen = moving._on_screen_in_order()
check("and they are the ones nearest the top, where the eye is",
      list(moving._players) == on_screen[:gal.MAX_PLAYERS])

# No Python runs per frame. Every call into Qt from Python gives up the GIL and
# waits to get it back, so a scale and a pixmap per frame, eight players at 25
# frames a second, was a queue of waits whenever another thread was busy.
from PySide6.QtCore import SIGNAL                                 # noqa: E402

first = on_screen[0]
movie = moving._players[first][0]
check("a player has nothing in Python listening to its frames",
      movie.receivers(SIGNAL("frameChanged(int)")) == 0
      and movie.receivers(SIGNAL("updated(QRect)")) == 0)
check("Qt scales its frames itself, to the size the still was fitted to",
      movie.scaledSize() == moving.model_.image(first).size())
check("the delegate paints the player's own frame",
      moving.delegate.movies[first] is movie)
check("and one clock repaints the page", moving._clock.isActive())


class Viewport:
    """Stands in for the view's viewport, to count what gets repainted."""

    def __init__(self):
        self.rects = []

    def update(self, rect):
        self.rects.append(rect)


spy = Viewport()
real_viewport = moving.viewport
moving.viewport = lambda: spy
moving._painted.clear()
moving._last_tick = time.monotonic() - gal.FRAME_MS / 1000
moving._tick()
painted_first = len(spy.rects)
moving._last_tick = time.monotonic() - gal.FRAME_MS / 1000
moving._tick()
check("a tick repaints every card whose frame has moved on",
      painted_first == len(moving._players))
check("and none whose frame has not", len(spy.rects) == painted_first)
moving.viewport = real_viewport

# Late twice running means the GUI thread is behind with something; the
# animation stops adding to it until the clock has been on time for a while.
now = time.monotonic()
moving._pace(0.6, now)
check("one late tick is not enough to stop anything", not moving.resting)
moving._pace(0.6, now + 0.1)
check("two in a row stop the animation",
      moving.resting and all(m.state() == m.MovieState.Paused
                             for m, _b in moving._players.values()))
moving._pace(0.0, now + 0.2)
moving._pace(0.0, now + 0.2 + gal.CALM_SECONDS / 2)
check("it stays stopped while the window has only just caught up", moving.resting)
moving._pace(0.0, now + 0.3 + gal.CALM_SECONDS)
check("and starts again once it has kept up for a while",
      not moving.resting and all(m.state() == m.MovieState.Running
                                 for m, _b in moving._players.values()))

moving._stop_one(first)
check("a player that stops leaves nothing behind for a row it no longer feeds",
      first not in moving._players and first not in moving.delegate.movies
      and first not in moving._painted)
moving._stop_all()
check("and with no players the clock stops too", not moving._clock.isActive())

# A still preview is never given a decoder, however many there are.
stills = gal.GalleryView()
stills.resize(1200, 900)
plain = [wallpaper(str(800 + i)) for i in range(10)]
stills.show_items(plain)
for card in plain:
    stills._image_arrived(card.id, QByteArray(b"\x89PNG\r\n\x1a\n"), frame)
stills._sync_players()
check("a still preview gets no decoder at all", stills._players == {})


# ---- Whole pictures, not cropped ones ---------------------------------------

from app.settings import Settings                                 # noqa: E402

# Every preview measured on an author's first page was square. A 16:10 frame
# cut the top and bottom 38% off each of them.
check("the preview area is square, because the previews are",
      gal.THUMB_W == gal.THUMB_H)

squared = gal.GalleryModel()
squared.set_items([wallpaper("sq"), wallpaper("wide")])
square = QImage(512, 512, QImage.Format_RGB32)
square.fill(QColor("#3366cc"))
squared.set_image("sq", QByteArray(b"x"), square)
wide = QImage(800, 400, QImage.Format_RGB32)
wide.fill(QColor("#cc6633"))
squared.set_image("wide", QByteArray(b"x"), wide)
kept = squared.data(squared.index(0, 0), gal.IMAGE)
fitted = squared.data(squared.index(1, 0), gal.IMAGE)
check("a square preview fills the tile exactly",
      kept.width() == gal.THUMB_W and kept.height() == gal.THUMB_H)
check("a wide one is fitted inside it, whole, instead of cropped to fill",
      fitted.width() == gal.THUMB_W and fitted.height() == gal.THUMB_H // 2)


# ---- Memory: only the page on screen -----------------------------------------
#
# Every preview of every page ever shown used to stay in memory: after clicking
# through ninety authors, 662 previews and 945 MB. A page is thirty.

kept_view = gal.GalleryView()
kept_view.resize(900, 700)
kept_view.show_items([wallpaper(f"m{i}") for i in range(70)])
tile = QImage(8, 8, QImage.Format_RGB32)
tile.fill(QColor("#447799"))
for card in kept_view.current_page():
    kept_view.model_.set_image(card.id, QByteArray(b"x" * 10), tile)
check("the page on screen keeps its previews",
      len(kept_view.model_._images) == gal.PAGE_SIZE)
first_page = [w.id for w in kept_view.current_page()]
kept_view.next_page()
check("turning the page lets go of the last page's previews",
      not set(first_page) & (set(kept_view.model_._images) | set(kept_view.model_._raw)))
kept_view.model_.set_image(first_page[0], QByteArray(b"late"), tile)
check("and a download for a page already left is not kept when it lands",
      first_page[0] not in kept_view.model_._images)
kept_view.set_page(2)
check("showing the same page again keeps what it already had",
      len(kept_view.model_._images) == 0 or all(
          i in {w.id for w in kept_view.current_page()} for i in kept_view.model_._images))

# And the download queue follows the page: nothing asked for a page already
# left is still waiting ahead of the page on screen.
queue_loader = gal.ThumbLoader(threads=1)
queue_loader.stopped = True           # nothing runs; only the bookkeeping is tested
for i in range(5):
    queue_loader.request(f"q{i}", "https://example/q.jpg")
queue_loader.retarget()
check("a new page drops the downloads queued for the old one",
      queue_loader._asked == set())
queue_loader.request("q1", "https://example/q.jpg")
check("so the new page can ask for any of them again", queue_loader._asked == {"q1"})
queue_loader.stop()


# ---- What a card says about a wallpaper -------------------------------------

sized = wallpaper("z")
sized.item.kind = "Video"
sized.item.file_size = 225_107_075
info_model = gal.GalleryModel()
info_model.set_items([sized])
with_facts = painted(delegate, info_model.index(0, 0))
sized.item.kind, sized.item.file_size = "", 0
without_facts = painted(delegate, info_model.index(0, 0))
check("type and size are drawn on the card",
      with_facts != without_facts and sized.item.size_text == "")


# ---- The tab: one queue for every subscription -------------------------------

class FakeUgc:
    """Steamworks, counting how often it was opened and what it was asked."""

    opened = 0
    asked: list[str] = []
    fail_connect = False

    def connect(self):
        if FakeUgc.fail_connect:
            raise tab_mod.UgcError("Steam is not running")
        FakeUgc.opened += 1
        return self

    def subscribe(self, item_id, wait=0):
        FakeUgc.asked.append(item_id)
        return type("State", (), {"describe": lambda self: "subscribed"})()

    def close(self):
        pass


tab_mod.SteamUgc = FakeUgc


def wait_for(condition, seconds=6.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.02)
    return False


done_ids: list[str] = []
failed_ids: list[str] = []
line = tab_mod.SubscribeQueue()
line.IDLE_SECONDS = 0.2
line.finished_item.connect(lambda item_id, _state: done_ids.append(item_id))
line.failed_item.connect(lambda item_id, _msg: failed_ids.append(item_id))
line.add(["a", "b", "c"])
line.add(["d"])
check("everything added is subscribed to, in the order it was asked for",
      wait_for(lambda: len(done_ids) == 4) and FakeUgc.asked == ["a", "b", "c", "d"])
check("through a single Steam connection, not one per wallpaper",
      FakeUgc.opened == 1)
check("which is closed again once the queue has gone quiet",
      wait_for(lambda: not line.isRunning()))

FakeUgc.fail_connect = True
line.add(["e", "f", "g"])
check("when Steam is not there every waiting wallpaper is told so at once",
      wait_for(lambda: failed_ids == ["e", "f", "g"]))
FakeUgc.fail_connect = False
wait_for(lambda: not line.isRunning())


# ---- The tab: status, space, keys, and subscribing a page -------------------

# The tab saves its choices as they change. Pointed at the real file, this test
# once overwrote data/suite.json with nothing but a subscribe mode.
import tempfile                                                    # noqa: E402
import app.settings as settings_mod                                # noqa: E402
settings_mod.SETTINGS_PATH = Path(tempfile.mkdtemp(prefix="wallpaper_gallery_test_")) / "suite.json"
real_settings = Path(__file__).resolve().parent.parent / "data" / "suite.json"
before_test = real_settings.read_bytes() if real_settings.exists() else None

tab = tab_mod.ReviewTab(Settings({}))
tab.resize(1400, 900)
tab.show()
app.processEvents()

tab._say("Could not subscribe", "danger")
red = tab.status.styleSheet()
tab._say("Subscribed to “something”", "ok")
check("an error colour does not outlive the error",
      tab.status.styleSheet() != red and "Subscribed" in tab.status.text())

height = tab.status.height()
tab._say("x " * 400)
app.processEvents()
check("a message too long for the line is cut short, not wrapped onto a second",
      tab.status.height() == height and tab.status.text().endswith("…"))
check("and the whole of it is still there to read on hover",
      tab.status.toolTip().startswith("x x"))

top_of_split = tab.authors.mapTo(tab, tab.authors.rect().topLeft()).y()
tab._busy(True)
app.processEvents()
moved_on_show = tab.authors.mapTo(tab, tab.authors.rect().topLeft()).y()
tab._busy(False)
app.processEvents()
moved_on_hide = tab.authors.mapTo(tab, tab.authors.rect().topLeft()).y()
check("the loading bar coming and going moves nothing below it",
      top_of_split == moved_on_show == moved_on_hide)

opened: list[str] = []
tab.open_author = lambda card: opened.append(card.name)
tab.authors.chosen.disconnect()
tab.authors.chosen.connect(tab.open_author)
people = [author_card_name for author_card_name in ("first", "second", "third")]
listed = [rv.AuthorCard(id64=f"7656119000000000{i}", profile=Profile(id64=str(i), name=n))
          for i, n in enumerate(people)]
tab.authors.set_cards(listed)
tab.authors.setCurrentIndex(tab.authors.model_.index(1, 0))
check("moving to an author with the keys opens them, as a click does",
      wait_for(lambda: opened == ["second"], 2.0))
opened.clear()
for row in (2, 0, 1, 2):
    tab.authors.setCurrentIndex(tab.authors.model_.index(row, 0))
check("but only where the selection comes to rest, not every row passed on the way",
      wait_for(lambda: opened == ["third"], 2.0) and opened == ["third"])
opened.clear()
tab.authors.select(listed[0])
app.processEvents()
time.sleep(0.35)
app.processEvents()
check("putting the highlight back after a refresh does not reopen anyone",
      opened == [])

queued_for_steam: list[list[str]] = []
tab.subscriptions.add = lambda ids: queued_for_steam.append(list(ids))
page = [wallpaper("p1"), wallpaper("p2", subscribed=True), wallpaper("p3")]
tab.gallery.show_items(page)
tab.mode.setCurrentIndex(0)
tab._update_subscribe_all()
check("the button offers what is left on the page",
      tab.subscribe_all_btn.isEnabled() and "2" in tab.subscribe_all_btn.text())
tab.subscribe_page()
check("pressing it subscribes to every wallpaper on the page not already taken",
      queued_for_steam == [["p1", "p3"]])
check("and marks each one as on its way", {"p1", "p3"} <= tab.gallery.delegate.busy)
tab.subscribe_page()
check("pressing it again does not ask twice for the same wallpapers",
      queued_for_steam == [["p1", "p3"]])

tab.mode.setCurrentIndex(1)
check("it is switched off when subscribing means opening Steam's page for each",
      not tab.subscribe_all_btn.isEnabled())

# Noticing a subscription made elsewhere is a listing of Steam's workshop
# folder, on a hard disk Wallpaper Engine streams from. It used to run on the
# GUI thread every four seconds.
import threading                                                   # noqa: E402


class WatchedLibrary:
    def __init__(self):
        self.threads = []

    def subscribed(self):
        self.threads.append(threading.current_thread() is threading.main_thread())
        return {"w2"}

    def note_subscribed(self, item_id):
        pass


tab.library = WatchedLibrary()
tab.gallery.show_items([wallpaper("w1"), wallpaper("w2")])
tab._notice_subscriptions()
tab._notice_subscriptions()           # while the first look is still out
check("a wallpaper subscribed elsewhere is noticed",
      wait_for(lambda: tab.gallery.showing()[1].subscribed))
check("by looking at the folder off the GUI thread", tab.library.threads == [False])
check("and only once while a look is already under way", len(tab.library.threads) == 1)
check("the other stays on offer", not tab.gallery.showing()[0].subscribed)

# ---- Progress has to outlive the task that first reported it ---------------
#
# The `Review` is built once and kept for the tab's life; a finished `Task` is
# deleted. So handing the engine `task.step.emit` of whichever task happened to
# be running when the Review was built — the first scan's — left the engine
# reporting through a deleted QObject. The second thing to report progress,
# which is "Count what is new" one press after a scan, died on it: PySide
# raised "Signal source has been deleted", the worker turned that into a
# failure, and the tab printed it in red instead of counting anything.

from PySide6.QtCore import QCoreApplication, QEvent                # noqa: E402

reported: list[tuple] = []
tab.progressed.connect(lambda *args: reported.append(args))

spent = tab_mod.Task(lambda step: step("items", 1, 2), tab)
tab._task = spent
spent.finished.connect(tab._finished)
spent.start()
wait_for(lambda: tab._task is None)
spent.wait(2000)
QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
check("a task that has finished is deleted, as it should be", tab._task is None)

tab._progress_relay("counting", 7, 9)
app.processEvents()
check("progress still reaches the tab after that task is gone",
      ("counting", 7, 9) in reported)
check("and lands on the status line rather than in red",
      "7/9" in tab.status.text())

# Finished work does not stay behind as a child of the tab.


def tasks_alive():
    return len([c for c in tab.children() if isinstance(c, tab_mod.Task)])


wait_for(lambda: tab._watching is None)
QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
before_tasks = tasks_alive()
tab._notice_subscriptions()
wait_for(lambda: tab._watching is None)
QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
check("a finished look leaves no thread object behind", tasks_alive() == before_tasks)
tab.gallery.close_loader()
tab.close()
check("and nothing it did touched the settings file in the project",
      (real_settings.read_bytes() if real_settings.exists() else None) == before_test)

print()
print("PASSED %d/%d" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
