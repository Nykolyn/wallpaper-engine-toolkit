"""The Review page's gallery, checked without a window on screen.

Run it directly — there is no test framework in this project:

    .venv\\Scripts\\python.exe tests\\test_gallery.py

Delegates are where a page actually breaks. They run for every visible card, on
data that varies more than the happy path suggests — a wallpaper with no title,
an author Steam would not name, a card whose marks have not arrived yet — and
a mistake there is an exception per repaint rather than a wrong number. So each
one is painted here onto a pixmap, in every state it has, and the pixmap is
checked for having something on it.

The rest is the behaviour a mouse produces: a subscribed card must not offer
itself again, a click with Ctrl selects rather than subscribes, a preview must
be asked for once, and an animation must only be started for something that
is actually animated.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["WALLPAPER_TOOLKIT_DATA"] = tempfile.mkdtemp(prefix="gallery-test-")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import (  # noqa: E402
    QByteArray, QEvent, QPointF, QRect, QRectF, QSize, Qt)
from PySide6.QtGui import (  # noqa: E402
    QColor, QImage, QKeyEvent, QMouseEvent, QPainter, QPixmap)
from PySide6.QtWidgets import (                                              # noqa: E402
    QApplication, QStackedWidget, QStyleOptionViewItem)

app = QApplication.instance() or QApplication([])

from app import animations, theme                           # noqa: E402
theme.apply(app)

import app.ui.gallery as gal                                # noqa: E402
import app.ui.kit.thumbs as thumbs                          # noqa: E402
import app.pages.review as page_mod                         # noqa: E402
from app.engines import library as lib                      # noqa: E402
from app.engines.review_flow import SessionAuthor           # noqa: E402
from app.engines.steam_ugc import UgcError                  # noqa: E402
from app.ui.kit.tables import (                             # noqa: E402
    BusyCell, ButtonCell, Cell, ChipCell, DiscCell, paint_list_row)
import app.engines.review as rv                             # noqa: E402
from app.engines.authors_store import Author                # noqa: E402
from app.engines.steam_api import ItemDetails, Profile      # noqa: E402

results: list[bool] = []


def check(label: str, condition: bool) -> None:
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label)


def wallpaper(item_id="1", title="A wallpaper", kind="Scene",
              size=6 * 1024 ** 2, preview="", **flags) -> rv.Wallpaper:
    item = ItemDetails(id=item_id, ok=True, creator="76561198000000001",
                       title=title, created=1750000000, updated=1750000000,
                       preview=preview, kind=kind, file_size=size)
    return rv.Wallpaper(item=item, **flags)


def item_box(delegate) -> QSize:
    return delegate.item_size()


def painted(delegate, index, size: QSize | None = None) -> QImage:
    """Paint one item and hand back what landed on the canvas."""
    size = size or item_box(delegate)
    pixmap = QPixmap(size)
    pixmap.fill(QColor("#000000"))
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, size.width(), size.height())
    painter = QPainter(pixmap)
    try:
        delegate.paint(painter, option, index)
    finally:
        painter.end()
    return pixmap.toImage()


def has_ink(image: QImage) -> bool:
    """Whether anything was drawn — the canvas started black."""
    return thumbs._brightness(image) > 0.01


def wait_for(condition, seconds=6.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.02)
    return False


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
check("an arriving frame becomes a pixmap, its shape kept (the card crops it)",
      stored is not None and stored.size() == QSize(40, 25))
check("and the bytes are kept, because animation needs the original",
      model.raw("1") is not None)
huge = QImage(2000, 1000, QImage.Format_RGB32)
huge.fill(QColor("#224466"))
model.set_image("3", QByteArray(b"x"), huge)
check("a big one is kept no bigger than a card can use",
      model.image("3").width() == theme.GALLERY_STILL_MAX
      and model.image("3").height() == theme.GALLERY_STILL_MAX // 2)
model.set_image("2", QByteArray(), QImage())
check("an image that never arrived is a placeholder, not a wait for ever",
      model.data(model.index(1, 0), gal.IMAGE) is None and model.missing("2"))


# ---- What a card says: the mark, its chip and its edge -----------------------

marks = {
    "new to you": ({"owned_checked": True}, "New", None, "accent.line", False, True),
    "already had": ({"owned_checked": True, "in_library": True, "library_place": lib.RESERVE},
                    "Duplicated", "Already have", "warn.line", True, False),
    "yours before": ({"owned_checked": True, "once_had": True}, "WasYours", None, "ok.line",
                     False, True),
    "subscribed": ({"subscribed": True}, "Subscribed", None, "info.line", True, False),
    "in the folder": ({"owned_checked": True, "in_queue": True}, "Queued", None,
                      "border.hairline", False, True),
    "not asked yet": ({}, None, None, "border.hairline", False, True),
}
for label, (flags, chip, text, edge, dimmed, offer) in marks.items():
    mark = gal.card_mark(wallpaper(**flags))
    check(f"a card {label}: chip {chip}, edge {edge}, "
          f"{'set back' if dimmed else 'bright'}, {'on offer' if offer else 'not offered'}",
          (mark.chip, mark.text, mark.edge, mark.dimmed, mark.offer)
          == (chip, text, edge, dimmed, offer))
check("subscribed outranks everything else on the card",
      gal.card_mark(wallpaper(subscribed=True, in_library=True, once_had=True)) is gal.SUBSCRIBED)
check("and a copy kept outranks having had it once",
      gal.card_mark(wallpaper(owned_checked=True, in_library=True, once_had=True)) is gal.HAVE)

check("an already-had card says where the copy is, not when it was published",
      gal.card_line(wallpaper(in_library=True, library_place=lib.RESERVE))
      == "matches a folder in the reserve"
      and gal.card_line(wallpaper(in_library=True, library_place=lib.ROTATION))
      == "matches a folder in myprojects"
      and gal.card_line(wallpaper(in_library=True, library_place="?"))
      == "matches a folder in your library")
check("every other card, when it was published",
      gal.card_line(wallpaper()).startswith("added ")
      and gal.card_line(wallpaper(), now=datetime(2025, 6, 20)) == "added 15 Jun")
check("the plate says what it is and what it weighs, in the design's words",
      gal.card_meta(wallpaper(kind="Video", size=214 * 1024 ** 2)) == ("video", "214 MB")
      and gal.card_meta(wallpaper(kind="", size=0)) == ("", ""))
check("a card being worked on is not on offer, nor selectable",
      gal.offered(wallpaper(owned_checked=True))
      and not gal.offered(wallpaper(owned_checked=True), gal.WAITING))


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
check("nor one with a copy kept in the libraries",
      not wallpaper(owned_checked=True, in_library=True).unseen)
check("and neither is one you are subscribed to this minute",
      not wallpaper(owned_checked=True, subscribed=True).unseen)


# ---- The grid: how many across, and the cards' shape ---------------------------

check("a 1 280 px window's gallery holds three cards across",
      gal.columns_for(700) == 3)
check("a wider one four, and the widest five — never more",
      gal.columns_for(1020) == 4 and gal.columns_for(1980) == 5 and gal.columns_for(5000) == 5)
check("and never fewer than three, however narrow",
      gal.columns_for(400) == 3)
geometry = gal.card_geometry(700)
check("cards come out near the design's 230 px, gaps included",
      geometry.columns == 3 and 200 <= geometry.width <= 240
      and 3 * geometry.width + 4 * theme.GALLERY_GAP <= 700)
check("a card's preview is 16:9", abs(geometry.preview - geometry.width * 9 / 16) <= 0.5)

square = QSize(512, 512)
cut = gal.cover_source(square, 160, 90)
check("a square preview covering a 16:9 card loses its top and bottom",
      cut.width() == 512 and abs(cut.height() - 288) < 0.01 and abs(cut.top() - 112) < 0.01)
cut = gal.cover_source(QSize(800, 200), 160, 90)
check("and a very wide one its sides, centred",
      cut.height() == 200 and abs(cut.width() - 200 * 16 / 9) < 0.01
      and abs(cut.left() - (800 - 200 * 16 / 9) / 2) < 0.01)
check("a player's frames are scaled to just cover the card",
      gal.cover_size(square, 240, 135) == QSize(240, 240)
      and gal.cover_size(QSize(800, 200), 240, 135) == QSize(540, 135))


# ---- Painting a wallpaper card ---------------------------------------------

delegate = gal.GalleryDelegate()
size = delegate.sizeHint(QStyleOptionViewItem(), model.index(0, 0))
check("every card is the same size, which is what lets the grid virtualise",
      size == delegate.item_size() and size.width() > delegate.geometry.width)

states = {
    "plain": {},
    "new to you": {"owned_checked": True},
    "in the queue": {"in_queue": True},
    "owned once": {"once_had": True, "owned_checked": True},
    "already had": {"in_library": True, "owned_checked": True, "library_place": lib.RESERVE},
    "subscribed": {"subscribed": True},
    "everything at once": {"owned_checked": True, "in_queue": True, "in_library": True,
                           "once_had": True, "subscribed": True},
    "no title at all": {},
}
for label, flags in states.items():
    one = gal.GalleryModel()
    card = wallpaper(title="" if label == "no title at all" else "A wallpaper", **flags)
    one.set_items([card])
    check(f"a card paints when it is {label}", has_ink(painted(delegate, one.index(0, 0))))

for label, extra in {"a video of a gigabyte": {"kind": "Video", "size": 2 * 1024 ** 3},
                     "a web wallpaper": {"kind": "Web"},
                     "something Steam never tagged": {"kind": "", "size": 0},
                     "still loading its preview": {"preview": "https://example.net/p.jpg"}}.items():
    one = gal.GalleryModel()
    one.set_items([wallpaper(**extra)])
    check(f"and when it is {label}", has_ink(painted(delegate, one.index(0, 0))))

for state in (gal.WAITING, gal.SUBSCRIBING):
    one = gal.GalleryModel()
    one.set_items([wallpaper("b", owned_checked=True)])
    one.set_busy("b", state)
    check(f"and while it is {state}", has_ink(painted(delegate, one.index(0, 0))))

with_picture = gal.GalleryModel()
with_picture.set_items([wallpaper("p", owned_checked=True)])
picture = QImage(512, 512, QImage.Format_RGB32)
picture.fill(QColor("#cc3366"))
with_picture.set_image("p", QByteArray(b"x"), picture)
image = painted(delegate, with_picture.index(0, 0))
inside = image.pixelColor(gal.MARGIN + delegate.geometry.width // 2,
                          gal.MARGIN + delegate.geometry.preview // 2)
check("a preview is drawn covering its card, not letterboxed",
      inside.red() > 150 and inside.blue() > 60 and
      image.pixelColor(gal.MARGIN + delegate.geometry.width // 2, gal.MARGIN + 3).red() > 150)

# A subscribed card is set back where it stands, never taken away: a card
# vanishing under the cursor loses the place in a wall of four hundred.
bright, dim = gal.GalleryModel(), gal.GalleryModel()
bright.set_items([wallpaper("s", owned_checked=True)])
dim.set_items([wallpaper("s", subscribed=True)])
for m in (bright, dim):
    m.set_image("s", QByteArray(b"x"), picture)
centre = (gal.MARGIN + delegate.geometry.width // 2, gal.MARGIN + delegate.geometry.preview // 2)
check("a subscribed card is dimmed in place",
      painted(delegate, dim.index(0, 0)).pixelColor(*centre).red()
      < painted(delegate, bright.index(0, 0)).pixelColor(*centre).red() * 0.7)

# A selected card is marked by an accent edge and a check; its picture and its
# type stay as they were.
chosen = gal.GalleryModel()
chosen.set_items([wallpaper("c", owned_checked=True)])
chosen.set_image("c", QByteArray(b"x"), picture)
plain_card = painted(delegate, chosen.index(0, 0))
chosen.toggle("c")
selected_card = painted(delegate, chosen.index(0, 0))
edge_at = (gal.MARGIN + delegate.geometry.width // 2, gal.MARGIN + 1)
check("a selected card has the accent edge",
      selected_card.pixelColor(*edge_at).name() == theme.color("accent").name()
      and plain_card.pixelColor(*edge_at).name() != theme.color("accent").name())
check("and keeps its picture", selected_card.pixelColor(*centre) == plain_card.pixelColor(*centre))

empty = gal.GalleryModel()
empty.set_items([])
check("painting a row that is not there does nothing rather than raising",
      not has_ink(painted(delegate, empty.index(0, 0))))

big = gal.GalleryModel()
big.set_items([wallpaper("g", kind="Video", size=2 * 1024 ** 3)])
small = gal.GalleryModel()
small.set_items([wallpaper("g", kind="Video", size=200 * 1024 ** 2)])
check("a download of a gigabyte or more says its size in the warning colour",
      delegate.plate("video", "2.0 GB", True, 1.0).toImage()
      != delegate.plate("video", "2.0 GB", False, 1.0).toImage())
check("and a card with nothing to say about it has no plate at all",
      delegate.plate("", "", False, 1.0) is None)


# ---- Painting an author row -------------------------------------------------
#
# The list is the kit's ListRow (app/pages/review.py `author_row`): a row per
# author with new items, its chip, a tick once gone through.

known = Author(name="Alice", steam_id="76561198000000001",
               added=datetime(2024, 1, 1, tzinfo=timezone.utc),
               visited=datetime(2026, 1, 1, tzinfo=timezone.utc))


def painted_row(row, width=262, height=48) -> QImage:
    pixmap = QPixmap(width, height)
    pixmap.fill(QColor("#000000"))
    painter = QPainter(pixmap)
    try:
        paint_list_row(painter, QRectF(0, 0, width, height), row)
    finally:
        painter.end()
    return pixmap.toImage()


for label, state in (("a new author", rv.NEW), ("a known one", rv.KNOWN),
                     ("a duplicated one", rv.DUPLICATE), ("one Steam will not name", rv.UNKNOWN)):
    author = SessionAuthor(id="76561198000000001", name="Alice", new=3, state=state)
    check(f"an author row paints for {label}", has_ink(painted_row(page_mod.author_row(author))))
gone_through = SessionAuthor(id="76561198000000001", name="Alice", new=3, done=True)
card = rv.AuthorCard(id64="76561198000000001", profile=Profile(id64="1", name="Alice"),
                     records=[Author(name="Alicia", steam_id="76561198000000001")])
blank = QPixmap(262, 48)
blank.fill(QColor("#000000"))
check("a row gone through, with the name the database still has, paints (set back, so "
      "fainter than the rest)",
      painted_row(page_mod.author_row(gone_through, card)) != blank.toImage())
check("the author on screen says how many of theirs are selected",
      page_mod.author_row(gone_through, card, 3).meta == "3 new · 3 selected")


# ---- What the mouse does ----------------------------------------------------

view = gal.GalleryView()
view.resize(700, 500)
view.show()
offered = wallpaper("11", owned_checked=True)
taken = wallpaper("12", subscribed=True)
kept_copy = wallpaper("13", owned_checked=True, in_library=True, library_place=lib.RESERVE)
view.show_items([offered, taken, kept_copy])
app.processEvents()
check("the view shows what it was handed",
      [w.id for w in view.showing()] == ["11", "12", "13"])

asked: list[str] = []
view.subscribe_requested.connect(asked.append)


def release(target, point, button=Qt.LeftButton, modifiers=Qt.NoModifier):
    """A real mouse event: the view hands it on to Qt, so a stub will not do."""
    target.mouseReleaseEvent(QMouseEvent(QEvent.MouseButtonRelease, QPointF(point), button,
                                         button, modifiers))


def centre_of(row: int):
    return view.visualRect(view.model_.index(row, 0)).center()


release(view, centre_of(0))
check("clicking a card that is on offer asks for a subscription", asked == ["11"])
release(view, centre_of(1))
check("clicking one already subscribed asks for nothing", asked == ["11"])
release(view, centre_of(2))
check("nor does one you already have a copy of", asked == ["11"])

opened: list[str] = []
view.open_requested.connect(opened.append)
release(view, centre_of(1), Qt.RightButton)
check("but the right button still opens it in Steam", opened == ["12"])

view.mark_busy("11", gal.WAITING)
check("a card can wait in the queue", view.busy("11") == gal.WAITING)
view.mark_busy("11", True)
check("and be shown as being subscribed to", view.busy("11") == gal.SUBSCRIBING)
release(view, centre_of(0))
check("a card under way is not asked for twice", asked == ["11"])
view.mark_busy("11", None)
check("and told when it is over", view.busy("11") is None)


# ---- Selecting ----------------------------------------------------------------

picks = gal.GalleryView()
picks.resize(900, 700)
picks.show()
many = [wallpaper(f"s{i}", owned_checked=True) for i in range(40)]
many[3].subscribed = True
picks.show_items(many)
app.processEvents()
counts: list[int] = []
picks.selection_changed.connect(counts.append)
subscribed_to: list[str] = []
picks.subscribe_requested.connect(subscribed_to.append)


def pick_centre(row: int):
    return picks.visualRect(picks.model_.index(row, 0)).center()


release(picks, pick_centre(0), modifiers=Qt.ControlModifier)
check("Ctrl-click selects a card instead of subscribing to it",
      picks.selected_ids() == ["s0"] and not subscribed_to and counts[-1] == 1)
release(picks, pick_centre(1), modifiers=Qt.ControlModifier)
check("Ctrl-click adds to the selection", picks.selected_ids() == ["s0", "s1"])
release(picks, pick_centre(1), modifiers=Qt.ControlModifier)
check("and a second one takes it out again", picks.selected_ids() == ["s0"])
release(picks, pick_centre(5), modifiers=Qt.ShiftModifier)
check("Shift-click selects from the last card chosen up to this one, skipping the taken",
      picks.selected_ids() == ["s0", "s1", "s2", "s4", "s5"])
release(picks, pick_centre(3), modifiers=Qt.ControlModifier)
check("a subscribed card cannot be selected", "s3" not in picks.selected_ids())
picks.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
check("Esc lets go of the selection", picks.selected_ids() == [] and counts[-1] == 0)

check_box = picks.delegate.check_rect(picks.visualRect(picks.model_.index(6, 0)))
release(picks, check_box.center().toPoint())
check("the check offered on a card selects it, without a modifier",
      picks.selected_ids() == ["s6"] and not subscribed_to)
picks.set_page(2)
release(picks, picks.visualRect(picks.model_.index(0, 0)).center(), modifiers=Qt.ControlModifier)
check("the selection spans pages", picks.selected_ids() == ["s6", many[gal.PAGE_SIZE].id])
picks.mark_busy("s6", gal.WAITING)
check("a card handed to the queue leaves the selection", "s6" not in picks.selected_ids())
picks.set_page(1)
picks.clear_selection()
picks.select_page()
check("Ctrl+A selects every card on the page that is on offer",
      len(picks.selected_ids()) == gal.PAGE_SIZE - 2)
release(picks, pick_centre(7))
check("a plain click subscribes, as the card says, whatever is selected",
      subscribed_to == ["s7"] and len(picks.selected_ids()) == gal.PAGE_SIZE - 2)
picks.clear_selection()
picks.show_items([wallpaper("fresh")])
check("a new gallery starts with nothing selected", picks.selected_ids() == [])


# ---- The spinner of the card being subscribed to --------------------------------

spin = gal.GalleryView()
spin.resize(900, 700)
spin.show()
spin.show_items([wallpaper(f"w{i}", owned_checked=True) for i in range(12)])
app.processEvents()


class Viewport:
    """Stands in for the view's viewport, to count what gets repainted."""

    def __init__(self):
        self.rects = []

    def update(self, rect=None):
        self.rects.append(rect)


spin.mark_busy("w2", gal.WAITING)
spin.mark_busy("w3", gal.WAITING)
check("cards waiting in the queue turn nothing", spin._ticker.running is None)
spin.mark_busy("w2", gal.SUBSCRIBING)
check("the one Steam is asked about turns the shared spinner",
      spin._ticker.running == "spin" or not animations.ENABLED)
spy = Viewport()
real = spin.viewport
spin.viewport = lambda: spy
spin._spin_tick()
spin.viewport = real
spinner = spin.delegate.spinner_rect(spin.visualRect(spin.model_.index(2, 0)))
check("and each tick repaints that spinner only, not its card or the page",
      len(spy.rects) == 1 and spy.rects[0].contains(spinner.toAlignedRect())
      and spy.rects[0].width() < spin.delegate.geometry.width / 2)
spin.mark_busy("w2", None)
spin.mark_busy("w3", None)
check("and stops when nothing is", spin._ticker.running is None)


# ---- Asking for previews once -----------------------------------------------

loader = gal.ThumbLoader(threads=1)
loader.stop()          # nothing is going to run; only the bookkeeping is tested
loader.request("42", "https://example.net/a.jpg")
loader.request("42", "https://example.net/a.jpg")
check("a preview is only ever asked for once", loader._asked == {"42"})
loader.request("43", None)
check("and a wallpaper with no preview is not asked for at all",
      "43" not in loader._asked)
check("the cache path is keyed by the wallpaper's own id",
      loader.path_for("42").name.startswith("42"))
loader.forget("42")
check("one dropped from memory can be asked for again, and only that one",
      loader._asked == set())


# ---- Animation is only for what is animated ---------------------------------

still_view = gal.GalleryView()
still_view.resize(900, 700)
still_view.show_items([wallpaper("50"), wallpaper("51")])
still_view.model_._raw["50"] = QByteArray(b"\xff\xd8\xff\xe0 jpeg bytes")
still_view._sync_players()
check("a still preview starts no animation", "50" not in still_view._players)

still_view.model_._raw["51"] = QByteArray(b"GIF89a" + b"\x00" * 20)
still_view.model_._images["51"] = QPixmap(400, 400)
kept_gif = still_view.loader.path_for("51")
kept_gif.parent.mkdir(parents=True, exist_ok=True)
kept_gif.write_bytes(b"GIF89a" + b"\x00" * 20)
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
check("every move is announced, so the bar knows what to say",
      seen_pages[-1] == (1, 3, 70))
check("but the gallery still knows about every wallpaper, not just this page",
      len(paged.showing()) == 70)

paged.show_items([wallpaper("x")])
check("a short gallery is a single page", paged.pages == 1 and paged.page == 1)
paged.show_items([wallpaper(str(i)) for i in range(70)], paged=False)
check("the review as a list is one page of everything",
      paged.pages == 1 and paged.model_.rowCount() == 70)


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
moving.show()
many = [wallpaper(str(900 + i)) for i in range(gal.PAGE_SIZE)]
moving.show_items(many)
app.processEvents()
check("a page holds more cards than may animate",
      moving.model_.rowCount() > gal.MAX_PLAYERS)

gif = QByteArray(animated_gif())
frame = QImage(4, 4, QImage.Format_RGB32)
frame.fill(0xFF808080)
for card in many:
    moving.loader.path_for(card.id).write_bytes(animated_gif())    # where _Fetch keeps it
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
movie = moving._players[first]
check("a player reads the file the download was kept in, not a QBuffer made in Python",
      movie.fileName() == str(moving.loader.path_for(first)) and movie.device() is not None
      and type(movie.device()).__name__ == "QFile")
check("a player has nothing in Python listening to its frames",
      movie.receivers(SIGNAL("frameChanged(int)")) == 0
      and movie.receivers(SIGNAL("updated(QRect)")) == 0)
g = moving.card()
dpr = moving.devicePixelRatioF()
check("Qt scales its frames itself, to just cover the card",
      movie.scaledSize() == gal.cover_size(moving.model_.image(first).size(),
                                           g.width * dpr, g.preview * dpr))
check("the delegate paints the player's own frame",
      moving.delegate.movies[first] is movie)
check("and one clock repaints the page", moving._clock.isActive())

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
check("and only the preview of each, not the words under it",
      all(r.height() <= moving.card().preview + 1 for r in spy.rects))
moving.viewport = real_viewport

# Late twice running means the GUI thread is behind with something; the
# animation stops adding to it until the clock has been on time for a while.
now = time.monotonic()
moving._pace(0.6, now)
check("one late tick is not enough to stop anything", not moving.resting)
moving._pace(0.6, now + 0.1)
check("two in a row stop the animation",
      moving.resting and all(m.state() == m.MovieState.Paused
                             for m in moving._players.values()))
moving._pace(0.0, now + 0.2)
moving._pace(0.0, now + 0.2 + gal.CALM_SECONDS / 2)
check("it stays stopped while the window has only just caught up", moving.resting)
moving._pace(0.0, now + 0.3 + gal.CALM_SECONDS)
check("and starts again once it has kept up for a while",
      not moving.resting and all(m.state() == m.MovieState.Running
                                 for m in moving._players.values()))

moving.resize(700, 900)
app.processEvents()
g = moving.card()
check("a narrower window gives smaller cards, and the players follow",
      g.columns == 3 and movie.scaledSize() == gal.cover_size(
          moving.model_.image(first).size(), g.width * dpr, g.preview * dpr))
moving.hide()
check("a hidden grid plays nothing (the list is shown instead)", not moving._players)
moving.show()
moving._sync_players()
moving._stop_one(on_screen[0])
check("a player that stops leaves nothing behind for a row it no longer feeds",
      on_screen[0] not in moving._players and on_screen[0] not in moving.delegate.movies
      and on_screen[0] not in moving._painted)
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

lost = gal.GalleryView()
lost.resize(1200, 900)
lost.show_items([wallpaper("700")])
lost.model_.set_image("700", QByteArray(animated_gif()), frame)
lost.loader.path_for("700").unlink(missing_ok=True)
lost._sync_players()
check("an animated preview whose file is gone stays still rather than playing from memory",
      lost._players == {})


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

everything = gal.GalleryView()
everything.show_items([wallpaper(f"e{i}") for i in range(300)], paged=False)
dropped: list[str] = []
everything.model_.image_dropped.connect(dropped.append)
for card in everything.current_page():
    everything.model_.set_image(card.id, QByteArray(b"x"), tile)
check("a list of every author keeps a bounded number of previews in memory",
      len(everything.model_._images) == theme.GALLERY_IMAGES
      and len(dropped) == 300 - theme.GALLERY_IMAGES and dropped[0] == "e0")

# And the download queue follows the page: nothing asked for a page already
# left is still waiting ahead of the page on screen.
queue_loader = gal.ThumbLoader(threads=1)
queue_loader.stopped = True           # nothing runs; only the bookkeeping is tested
for i in range(5):
    queue_loader.request(f"q{i}", "https://example.net/q.jpg")
queue_loader.retarget()
check("a new page drops the downloads queued for the old one",
      queue_loader._asked == set())
queue_loader.request("q1", "https://example.net/q.jpg")
check("so the new page can ask for any of them again", queue_loader._asked == {"q1"})
queue_loader.stop()


# ---- The list view --------------------------------------------------------------

list_source = gal.GalleryView()
listing = gal.GalleryList(list_source)
listing.resize(900, 600)
listing.show()
rows = [wallpaper("l0", owned_checked=True, kind="Scene", size=214 * 1024 ** 2),
        wallpaper("l1", owned_checked=True, once_had=True),
        wallpaper("l2", owned_checked=True, in_library=True, library_place=lib.RESERVE),
        wallpaper("l3", subscribed=True),
        wallpaper("l4", owned_checked=True, kind="Video", size=3 * 1024 ** 3)]
list_source.show_items(rows)
app.processEvents()
lm = listing.model_
check("the list shows the gallery's page, one row per wallpaper",
      lm.rowCount() == 5 and lm.item_at(0) is rows[0])
title_cell = lm.cell(rows[2], 1)
check("WALLPAPER is the title over its date, or where the copy you have is",
      isinstance(title_cell, Cell) and title_cell.sub == "matches a folder in the reserve"
      and lm.cell(rows[0], 1).sub.startswith("added "))
check("TYPE and SIZE in the design's words; a gigabyte in the warning colour",
      lm.cell(rows[0], 2) == "scene" and lm.cell(rows[0], 3).text == "214 MB"
      and lm.cell(rows[4], 3).tone == "warn" and lm.cell(rows[0], 3).tone is None)
check("MARK is the card's chip",
      lm.cell(rows[0], 4) == ChipCell("New") and lm.cell(rows[1], 4) == ChipCell("WasYours")
      and lm.cell(rows[2], 4) == ChipCell("Duplicated", "Already have")
      and lm.cell(rows[3], 4) == ChipCell("Subscribed"))
check("the last column: Subscribe, a dash for one you have, a check for one subscribed",
      lm.cell(rows[0], 5) == ButtonCell("Subscribe")
      and lm.cell(rows[2], 5).text == "—" and isinstance(lm.cell(rows[3], 5), DiscCell))
check("and Open in Steam when that is what a click does",
      gal.action_cell(rows[0], None, opens_page=True) == ButtonCell("Open in Steam"))
list_source.mark_busy("l0", gal.SUBSCRIBING)
list_source.mark_busy("l1", gal.WAITING)
check("and the work under way: Subscribing… turning, Waiting… still",
      lm.cell(rows[0], 5) == BusyCell("Subscribing…") and lm.cell(rows[1], 5).text == "Waiting…"
      and listing.spins(0) == animations.ENABLED and not listing.spins(1))
list_source.mark_busy("l0", None)
list_source.mark_busy("l1", None)
check("a row you already have is set back; the others are not",
      lm.row_dimmed(rows[2]) and not lm.row_dimmed(rows[0]) and not lm.row_dimmed(rows[3]))
check("sorting by size sorts by bytes, not by the words",
      lm.sort_key(rows[4], 3) > lm.sort_key(rows[0], 3))

list_asked: list[str] = []
listing.subscribe_requested.connect(list_asked.append)
button_at = None
for y in range(listing.viewport().height()):
    hit = listing.button_at(QPointF(listing.viewport().width() - 40, y))
    if hit is not None and hit[0] == 0:
        button_at = QPointF(listing.viewport().width() - 40, y)
        break
check("a row's Subscribe button can be found where it is drawn", button_at is not None)
listing._hover_to(0)
check("it is Accent on the row under the pointer, Secondary on the others",
      listing.button_look(0, 5, ButtonCell("Subscribe"))[0] == "accent"
      and listing.button_look(1, 5, ButtonCell("Subscribe"))[0] == "secondary")
press = QMouseEvent(QEvent.MouseButtonPress, button_at, Qt.LeftButton, Qt.LeftButton,
                    Qt.NoModifier)
listing.mousePressEvent(press)
release(listing, button_at)
check("clicking it subscribes, and does not select the row",
      list_asked == ["l0"] and list_source.selected_ids() == [])
row_point = QPointF(300, listing.rowViewportPosition(1) + listing.rowHeight(1) / 2)
release(listing, row_point)
check("a click elsewhere on a row selects it, in the gallery's one selection",
      list_source.selected_ids() == ["l1"] and lm.row_tone(rows[1]) == "accent")
release(listing, QPointF(300, listing.rowViewportPosition(3) + 5))
check("a subscribed row cannot be selected", list_source.selected_ids() == ["l1"])
listing.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
check("Esc lets go of it there too", list_source.selected_ids() == [])
listing.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_A, Qt.ControlModifier))
check("and Ctrl+A selects every row on offer", list_source.selected_ids() == ["l0", "l1", "l4"])
list_source.clear_selection()
check("its previews are the gallery's, cropped to the row",
      listing.thumb_state("l0")[0] == "placeholder")
list_source.model_.set_image("l0", QByteArray(b"x"), picture)
state, cover = listing.thumb_state("l0")
check("and once one arrives it covers the row's thumb exactly",
      state == "image" and cover.deviceIndependentSize().toSize()
      == QSize(*theme.THUMB["row"][:2]))

# The grid and the list share one loader, and only one of them is on screen.
# The next author resets the model under both; the hidden list used to settle
# 90 ms later and drop every download the grid had just queued, so most cards
# stayed empty until the grid happened to ask again.
shared_grid = gal.GalleryView()
shared_list = gal.GalleryList(shared_grid)
views = QStackedWidget()
views.addWidget(shared_grid)
views.addWidget(shared_list)
views.resize(1400, 900)
views.show()
shared_grid.loader.stopped = True     # nothing downloads; only the asking is tested
shared_grid.show_items([wallpaper(f"s{i}", preview="https://example.net/s.jpg")
                        for i in range(gal.PAGE_SIZE)])
app.processEvents()
shared_grid._pending.timeout.disconnect()   # the grid does not get to ask twice
shared_grid.show_items([wallpaper(f"t{i}", preview="https://example.net/t.jpg")
                        for i in range(gal.PAGE_SIZE)])
asked_by_grid = set(shared_grid.loader._asked)
wait_for(lambda: False, seconds=0.4)
check("the next author's previews stay asked for while the list is hidden",
      len(asked_by_grid) > 0 and asked_by_grid <= shared_grid.loader._asked)
views.setCurrentWidget(shared_list)
check("and the list asks for its own rows once it is shown",
      wait_for(lambda: shared_grid.loader._asked
               and shared_grid.loader._asked < asked_by_grid, seconds=1.0))
views.close()
shared_grid.close_loader()


# ---- One queue for every subscription ------------------------------------------

class FakeUgc:
    """Steamworks, counting how often it was opened and what it was asked."""

    opened = 0
    asked: list[str] = []
    fail_connect = False

    def connect(self):
        if FakeUgc.fail_connect:
            raise UgcError("Steam is not running")
        FakeUgc.opened += 1
        return self

    def subscribe(self, item_id, wait=0):
        FakeUgc.asked.append(item_id)
        return type("State", (), {"describe": lambda self: "subscribed"})()

    def close(self):
        pass


done_ids: list[str] = []
failed_ids: list[str] = []
line = page_mod.SubscribeQueue(connect=lambda: FakeUgc().connect())
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

print()
print("PASSED %d/%d" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
