"""gallery.py — an author's unsubscribed wallpapers, as a wall of previews.

The list of authors answers *who*; this answers *what*, and it has to do it for
one thousand two hundred cards without the window noticing. Three things make
that possible and each is a deliberate choice rather than a default:

**Virtualisation.** A `QListView` in icon mode with uniform item sizes only
builds the delegates it can see. The alternative — a widget per wallpaper in a
grid layout — is 1 200 widgets, and it is not close.

**Previews arrive later than the grid.** Thumbnails are fetched on a thread
pool and cached on disk under `data/thumbs`, keyed by workshop id. A card draws
its frame, its title and its marks immediately and repaints once when its image
lands; nothing waits for the network.

**Everything on screen animates.** Steam serves an animated wallpaper's preview
as a real GIF — measured, `image/gif` against `image/jpeg` for the static ones —
so `QMovie` plays it with no help, and nearly half of a real gallery is
animated. A wall that only moves under the cursor reads as broken next to
Wallpaper Engine's own browser, so every animated card in view is playing. What
makes that affordable is the **pagination**: thirty wallpapers to a page, so the
number of decoders is fixed however much the author has published — and the
wait is thirty previews rather than a thousand.

**What the GUI thread may not do.** Every call from Python into Qt gives up
the GIL and takes it back, and while any other thread is busy in Python, taking
it back is a wait. So the animation runs no Python per frame: Qt decodes and
scales each frame itself, and one clock twenty times a second repaints the
cards whose frame moved on. If that clock ever finds itself running late, the
animation stops until the window is keeping up again — a still gallery is a
small price, and a window that does not answer is not. Memory is bounded the
same way: only the page on screen keeps its previews in memory, because the
bytes are on disk and come back in milliseconds.

The marks on a card matter as much as the picture. A wallpaper here may be one
that was owned once and deleted — 14 915 of the ids this machine's folders
remember are in that state — and re-reviewing those from scratch every week is
exactly the work the tab exists to remove. That answer arrives after the cards
do, on a thread, so a card says nothing about it until it is known rather than
guessing "new".
"""
from __future__ import annotations

import time
from typing import Iterable

from PySide6.QtCore import (
    QAbstractListModel, QBuffer, QByteArray, QIODevice, QModelIndex, QRect, QSize, Qt,
    Signal, QTimer)
from PySide6.QtGui import QColor, QFont, QImage, QMovie, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QListView, QStyle, QStyledItemDelegate

from .. import theme
# The loader lives in the kit now, where the tables' thumbnails use it too.
from .kit.thumbs import ThumbLoader

# Card geometry. Square, because the previews are: measured on an author's
# first page, 20 of 20 came back 1:1. A 16:10 frame looked like a wallpaper
# and threw away the top and bottom 38% of every picture, which is the
# "zoomed in, half the content missing" it was, next to Wallpaper Engine's own
# grid. The image is fitted inside rather than cropped, so an odd-shaped
# preview is letterboxed instead of cut.
THUMB_W, THUMB_H = 400, 400
# Two lines of type under the picture: the title, and a row of marks holding
# what the wallpaper is, what it weighs and when it was made.
CARD_W, CARD_H = THUMB_W + 16, THUMB_H + 68

# A glyph per kind, drawn before the word. Geometric shapes rather than emoji:
# these are in Segoe UI Variable, they take the chip's own colour, and they
# survive being 8 point.
KIND_MARKS = {"Scene": "◆", "Video": "▶", "Web": "◎",
              "Application": "▣", "Preset": "◇"}
UNKNOWN_KIND = "○"

# Chips. One height and one radius, so a row of them reads as a row.
CHIP_H = 17
CHIP_PAD = 11

# Wallpapers to a page. Fetching a thousand previews to look at the newest few
# is the wait this removes; it also caps how many decoders animation needs.
PAGE_SIZE = 30

# How many previews may animate at the same time. A page holds thirty, and
# starting thirty decoders in the burst in which their downloads land is what
# made the window stop answering at all: the players are built on the GUI
# thread, inside the handler for each arrival, while six download threads keep
# delivering. Bounding them costs a few still cards at the bottom of a page and
# buys a window that stays alive. They are started on a clock rather than on
# arrival for the same reason.
MAX_PLAYERS = 8

# The animation clock. GIF previews here run at 25 frames a second at most
# (470 measured), so 20 repaints a second drops one frame in five and nothing
# the eye follows.
FRAME_MS = 50
# When the clock is this late twice running, the GUI thread is behind with
# something, and the animation stops adding to it...
LATE_SECONDS = 0.25
# ...until the clock has been on time for this long.
CALM_SECONDS = 2.0

# Roles the delegate reads.
WALLPAPER = Qt.UserRole + 1
IMAGE = Qt.UserRole + 2


# ---- The model --------------------------------------------------------------

class GalleryModel(QAbstractListModel):
    """The wallpapers on offer, plus whatever images have arrived so far."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: list = []
        self._rows: dict[str, int] = {}
        self._images: dict[str, QPixmap] = {}
        self._raw: dict[str, QByteArray] = {}

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._items)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or index.row() >= len(self._items):
            return None
        wallpaper = self._items[index.row()]
        if role == WALLPAPER:
            return wallpaper
        if role == IMAGE:
            return self._images.get(wallpaper.id)
        if role in (Qt.DisplayRole, Qt.ToolTipRole):
            return wallpaper.title or wallpaper.id
        return None

    def set_items(self, items: Iterable) -> None:
        """Show these wallpapers, and let go of every other page's previews.

        They used to be kept for every page ever shown: after clicking through
        ninety authors that was 662 previews and 945 MB of memory, and the
        window only ever showed thirty of them. The bytes are in `data/thumbs`
        and come back from disk faster than the network ever delivered them.
        """
        self.beginResetModel()
        self._items = list(items)
        self._rows = {w.id: row for row, w in enumerate(self._items)}
        self._images = {k: v for k, v in self._images.items() if k in self._rows}
        self._raw = {k: v for k, v in self._raw.items() if k in self._rows}
        self.endResetModel()

    def item_at(self, row: int):
        return self._items[row] if 0 <= row < len(self._items) else None

    def raw(self, item_id: str) -> QByteArray | None:
        return self._raw.get(item_id)

    def image(self, item_id: str) -> QPixmap | None:
        return self._images.get(item_id)

    def row_of(self, item_id: str) -> int | None:
        return self._rows.get(item_id)

    def set_image(self, item_id: str, data: QByteArray, frame: QImage) -> None:
        if frame.isNull():
            return
        row = self._rows.get(item_id)
        if row is None:
            # A preview for a page since left. Its bytes are on disk already.
            return
        self._raw[item_id] = data
        self._images[item_id] = QPixmap.fromImage(
            frame.scaled(THUMB_W, THUMB_H, Qt.KeepAspectRatio,
                         Qt.SmoothTransformation))
        where = self.index(row, 0)
        self.dataChanged.emit(where, where, [IMAGE])

    def refresh_row(self, item_id: str) -> None:
        row = self._rows.get(item_id)
        if row is not None:
            where = self.index(row, 0)
            self.dataChanged.emit(where, where)

    def refresh_all(self) -> None:
        """Repaint the whole page — for something that changed every card.

        The "was yours" pass is worked out on a thread and lands for all
        thirty at once; thirty separate row signals for one answer is thirty
        repaints of the same page.
        """
        if self._items:
            self.dataChanged.emit(self.index(0, 0),
                                  self.index(len(self._items) - 1, 0))


# ---- Drawing one card -------------------------------------------------------

class GalleryDelegate(QStyledItemDelegate):
    """A preview, a title, and the four or five things worth knowing.

    **The card does not turn into the selection colour.** It used to: a
    selected card was filled with `accent_pressed` and its title and facts
    went on being drawn in the ordinary text and faint greys, which on a solid
    blue field were barely there — the size and the date in particular, which
    are the two facts a decision is made on. Selection is a border now and the
    panel behind the type never moves far from what the type was chosen for.

    **What a wallpaper is, and what it costs, are marks rather than prose.**
    "Video  ·  1.4 GB  ·  2026-08-31" in one grey line is read last, after the
    picture and the title, and by then the decision is usually made. The kind
    carries its own colour, and a download of a gigabyte or more carries the
    warning colour, so both land in the same glance as the picture.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        # The player of every card that is animating. Its current frame is
        # read at paint time, already scaled by Qt.
        self.movies: dict[str, QMovie] = {}
        self.busy: set[str] = set()              # ids being subscribed right now

    def sizeHint(self, option, index) -> QSize:
        return QSize(CARD_W, CARD_H)

    # -- one chip ---------------------------------------------------------

    def _chip(self, painter: QPainter, x: int, y: int, text: str,
              fill: str | None, ink: str, height: int = CHIP_H) -> int:
        """Draw one mark and return where the next one starts.

        ``fill`` of None draws the text alone, which is what the date wants:
        a row where everything is a filled chip has nothing left to emphasise.
        """
        width = painter.fontMetrics().horizontalAdvance(text) + (
            CHIP_PAD if fill else 0)
        rect = QRect(x, y, width, height)
        if fill:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(fill))
            painter.drawRoundedRect(rect, 3, 3)
        painter.setPen(QColor(ink))
        painter.drawText(rect, Qt.AlignCenter, text)
        return x + width + 5

    def paint(self, painter: QPainter, option, index) -> None:
        wallpaper = index.data(WALLPAPER)
        if wallpaper is None:
            return
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)

        card = QRect(option.rect).adjusted(4, 4, -4, -4)
        hovered = bool(option.state & QStyle.State_MouseOver)
        selected = bool(option.state & QStyle.State_Selected)

        painter.setPen(Qt.NoPen)
        painter.setBrush(theme.color("surface.raised" if selected or hovered
                                else "surface.wash"))
        painter.drawRoundedRect(card, 6, 6)

        image_rect = QRect(card.left() + 4, card.top() + 4, THUMB_W, THUMB_H)
        movie = self.movies.get(wallpaper.id)
        pixmap = movie.currentPixmap() if movie is not None else None
        if pixmap is None or pixmap.isNull():
            pixmap = index.data(IMAGE)
        if pixmap is not None and not pixmap.isNull():
            # The whole picture, centred; anything that is not square gets bars
            # in the page colour rather than losing its edges.
            painter.setBrush(theme.color("surface.well"))
            painter.drawRect(image_rect)
            painter.drawPixmap(
                image_rect.left() + (THUMB_W - pixmap.width()) // 2,
                image_rect.top() + (THUMB_H - pixmap.height()) // 2, pixmap)
        else:
            painter.setBrush(theme.color("surface.raised"))
            painter.drawRect(image_rect)
            painter.setPen(theme.color("text.lo"))
            painter.drawText(image_rect, Qt.AlignCenter, "…")

        if wallpaper.subscribed:
            # Dim it rather than remove it: a card vanishing under the cursor
            # loses the place in a wall of four hundred. Under the marks, not
            # over them — dimming the word "subscribed" along with the picture
            # is dimming the reason the picture is dim.
            painter.setBrush(QColor(0, 0, 0, 110))
            painter.setPen(Qt.NoPen)
            painter.drawRect(image_rect)

        # Marks, top-left over the image, so they read before the picture does.
        # Each is a different colour because each answers a different question;
        # "subscribed" and "new" were both green, which made the pair of them
        # one green blur.
        marks: list[tuple[str, str]] = []
        if wallpaper.subscribed:
            marks.append(("subscribed", theme.css("ok")))
        if wallpaper.once_had:
            marks.append(("was yours", theme.css("warn")))
        if wallpaper.in_queue:
            marks.append(("queued", theme.css("info")))
        if wallpaper.unseen:
            # Only once the machine has actually been asked whether it has had
            # this before. The mark used to be painted from `new_since_visit`,
            # which is true of every card in the gallery — so "new" sat on
            # wallpapers that had been downloaded and deleted twice.
            marks.append(("new to you", theme.css("accent")))
        font = QFont(painter.font())
        font.setPointSize(8)
        painter.setFont(font)
        x = image_rect.left() + 4
        for text, colour in marks:
            x = self._chip(painter, x, image_rect.top() + 6, text, colour,
                           theme.css("text.onAccent"), 18)

        if wallpaper.id in self.busy:
            badge = QRect(image_rect.right() - 96, image_rect.bottom() - 26, 92, 22)
            painter.setPen(Qt.NoPen)
            painter.setBrush(theme.color("accent"))
            painter.drawRoundedRect(badge, 3, 3)
            painter.setPen(theme.color("text.onAccent"))
            painter.drawText(badge, Qt.AlignCenter, "subscribing…")
        elif hovered and not wallpaper.subscribed:
            hint = QRect(image_rect.left(), image_rect.bottom() - 26,
                         image_rect.width(), 22)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(0, 0, 0, 150))
            painter.drawRect(hint)
            painter.setPen(theme.color("text.body"))
            painter.drawText(hint, Qt.AlignCenter, "click to subscribe")

        title = wallpaper.title or wallpaper.id
        painter.setPen(theme.color("text.body"))
        font.setPointSize(10)
        painter.setFont(font)
        text_rect = QRect(card.left() + 6, image_rect.bottom() + 5,
                          card.width() - 12, 18)
        painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter,
                         painter.fontMetrics().elidedText(
                             title, Qt.ElideRight, text_rect.width()))

        # What it is, what it costs to download, and when it was made. The
        # first two are the decision; the date is context, so it is the one
        # thing on the row left as plain type.
        font.setPointSize(8)
        painter.setFont(font)
        y = text_rect.bottom() + 4
        x = card.left() + 6
        item = wallpaper.item
        kind = item.kind
        if kind:
            x = self._chip(painter, x, y, f"{KIND_MARKS.get(kind, UNKNOWN_KIND)} {kind}",
                           theme.kind_color(kind), theme.css("text.onAccent"))
        if item.size_text:
            if item.large:
                x = self._chip(painter, x, y, f"⚠ {item.size_text}",
                               theme.css("warn"), theme.css("text.onAccent"))
            else:
                x = self._chip(painter, x, y, item.size_text, None, theme.css("text.mid"))
        when = wallpaper.created
        if when:
            self._chip(painter, x, y, when.strftime("%Y-%m-%d"), None,
                       theme.css("text.lo"))

        if selected:
            painter.setPen(QPen(theme.color("accent"), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(QRect(card).adjusted(1, 1, -1, -1), 6, 6)
        elif hovered:
            painter.setPen(QPen(theme.color("border.strong"), 1))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(card, 6, 6)
        painter.restore()


# ---- The view ---------------------------------------------------------------

class GalleryView(QListView):
    """The wall itself: virtualised, lazily illustrated, animated on hover."""

    subscribe_requested = Signal(str)
    open_requested = Signal(str)
    page_changed = Signal(int, int, int)      # page, pages, total

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model_ = GalleryModel(self)
        self.delegate = GalleryDelegate(self)
        self.setModel(self.model_)
        self.setItemDelegate(self.delegate)
        self.setViewMode(QListView.IconMode)
        self.setResizeMode(QListView.Adjust)
        self.setMovement(QListView.Static)
        self.setUniformItemSizes(True)         # the whole reason this scrolls
        self.setSpacing(2)
        self.setMouseTracking(True)
        self.setSelectionMode(QListView.SingleSelection)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setStyleSheet("QListView { border: none; background: transparent; }")

        self.loader = ThumbLoader(self)
        self.loader.done.connect(self._image_arrived)

        # One decoder per animated wallpaper on screen. A page holds thirty, so
        # this is bounded by the pagination rather than by the author's output.
        self._players: dict[str, tuple[QMovie, QBuffer]] = {}
        self._all: list = []
        self._page = 1

        # Previews are only asked for once the grid stops moving, so a flick
        # through a page does not queue a download for every card twice.
        self._pending = QTimer(self)
        self._pending.setInterval(120)
        self._pending.setSingleShot(True)
        self._pending.timeout.connect(self._settled)
        self.verticalScrollBar().valueChanged.connect(self._pending.start)

        # The one clock that repaints animated cards: twenty times a second for
        # the whole page, however many players are running and whatever rate
        # each of them wants. It is also how the gallery notices it is behind.
        self._painted: dict[str, int] = {}      # frame number last painted
        self._clock = QTimer(self)
        self._clock.setInterval(FRAME_MS)
        self._clock.timeout.connect(self._tick)
        self._last_tick = 0.0
        self._late_ticks = 0
        self._calm_since: float | None = None
        self.resting = False                    # animation stopped to catch up

        # Previews land in bursts of six; deciding who animates is done once a
        # burst rather than once an arrival.
        self._players_due = QTimer(self)
        self._players_due.setInterval(150)
        self._players_due.setSingleShot(True)
        self._players_due.timeout.connect(self._sync_players)

    # -- contents ---------------------------------------------------------

    def close_loader(self) -> None:
        """Called when the window is going away, before Qt deletes the signal."""
        self._stop_all()
        self.loader.stop()

    def show_items(self, items: Iterable) -> None:
        """Take the whole gallery, and show the first page of it."""
        self._all = list(items)
        self.set_page(1)

    # -- pages ------------------------------------------------------------

    @property
    def pages(self) -> int:
        return max(1, (len(self._all) + PAGE_SIZE - 1) // PAGE_SIZE)

    @property
    def page(self) -> int:
        return self._page

    @property
    def total(self) -> int:
        return len(self._all)

    def set_page(self, page: int) -> None:
        self._page = max(1, min(page, self.pages))
        start = (self._page - 1) * PAGE_SIZE
        self._stop_all()
        self.loader.retarget()
        self.model_.set_items(self._all[start:start + PAGE_SIZE])
        self.scrollToTop()
        self._settled()
        self.page_changed.emit(self._page, self.pages, len(self._all))

    def next_page(self) -> None:
        self.set_page(self._page + 1)

    def previous_page(self) -> None:
        self.set_page(self._page - 1)

    def mark_busy(self, item_id: str, busy: bool = True) -> None:
        if busy:
            self.delegate.busy.add(item_id)
        else:
            self.delegate.busy.discard(item_id)
        self.model_.refresh_row(item_id)

    def refresh(self, item_id: str) -> None:
        self.model_.refresh_row(item_id)

    def refresh_page(self) -> None:
        self.model_.refresh_all()

    def current_page(self) -> list:
        """The wallpapers on the page that is on screen, in grid order."""
        return [self.model_.item_at(row) for row in range(self.model_.rowCount())]

    def showing(self) -> list:
        """Every wallpaper in this gallery, not only the page on screen.

        Marking one as subscribed has to work whichever page it is on: the
        watch that notices a subscription made elsewhere runs while any page
        is open.
        """
        return list(self._all)

    # -- previews ---------------------------------------------------------

    def _visible_rows(self) -> range:
        top = self.indexAt(self.rect().topLeft())
        bottom = self.indexAt(self.rect().bottomLeft())
        first = max(0, (top.row() if top.isValid() else 0) - 3)
        last = (bottom.row() + 4) if bottom.isValid() else self.model_.rowCount()
        return range(first, min(last, self.model_.rowCount()))

    def _settled(self) -> None:
        """After the grid stops moving: fetch what is on screen, animate it."""
        for row in self._visible_rows():
            wallpaper = self.model_.item_at(row)
            if wallpaper is not None:
                self.loader.request(wallpaper.id, wallpaper.item.preview)
        self._sync_players()

    def _image_arrived(self, item_id: str, data: QByteArray, frame: QImage) -> None:
        """One preview has landed. Start its player, and only its player.

        This used to sweep the whole page and start a player on the spot, for
        each of thirty arrivals: nine hundred model lookups to learn thirty
        things, and thirty decoders built on the GUI thread in whatever burst
        the six download threads delivered in. Now an arrival only says that
        the players are worth reconciling, and a clock does it once.
        """
        self.model_.set_image(item_id, data, frame)
        if not self._players_due.isActive():
            self._players_due.start()

    # -- animation --------------------------------------------------------

    def _on_screen_in_order(self) -> list:
        """The ids of the cards a viewer can see, topmost first."""
        found = []
        for row in self._visible_rows():
            wallpaper = self.model_.item_at(row)
            if wallpaper is not None:
                found.append(wallpaper.id)
        return found

    def _on_screen(self) -> set:
        """The ids of the cards a viewer can currently see."""
        return set(self._on_screen_in_order())

    def _is_animated(self, item_id: str) -> bool:
        """Only an animated preview is worth a decoder; Steam sends GIF."""
        data = self.model_.raw(item_id)
        return data is not None and bytes(data[:3]) == b"GIF"

    def _sync_players(self) -> None:
        """Play the animated previews on screen, up to `MAX_PLAYERS` of them.

        Steam's own browser animates the whole grid, and so does Wallpaper
        Engine, so a wall that only moves under the cursor reads as broken.
        What it does not do is decode thirty at once: past a handful the GUI
        thread spends all its time scaling frames and the window stops
        answering. The ones nearest the top of the view win, because that is
        where the eye is, and the rest keep their still frame.
        """
        on_screen = self._on_screen_in_order()
        animated = [i for i in on_screen if self._is_animated(i)]
        wanted = set(animated[:MAX_PLAYERS])
        for item_id in list(self._players):
            if item_id not in wanted:
                self._stop_one(item_id)
        for item_id in animated[:MAX_PLAYERS]:
            if item_id not in self._players:
                self._start_one(item_id)

    def _start_one(self, item_id: str) -> None:
        data = self.model_.raw(item_id)
        still = self.model_.image(item_id)
        if data is None or still is None:
            return
        buffer = QBuffer(self)
        buffer.setData(data)
        buffer.open(QIODevice.ReadOnly)
        movie = QMovie(self)
        movie.setDevice(buffer)
        # CacheAll keeps every decoded frame of every player alive at once:
        # thirty previews of fifty frames is a lot of memory to hold for a
        # thumbnail. The frames are cheap to decode again as they come round.
        movie.setCacheMode(QMovie.CacheNone)
        # Qt scales each frame itself, to exactly the size the still was fitted
        # to, so the picture does not change shape when it starts to move. This
        # used to be done in Python for every frame — a scale and a pixmap per
        # frame, eight players at 25 frames a second, each call a wait for the
        # GIL whenever a download or a Steam answer was being worked on.
        movie.setScaledSize(still.size())
        self._players[item_id] = (movie, buffer)
        self.delegate.movies[item_id] = movie
        movie.start()
        if self.resting:
            movie.setPaused(True)
        if not self._clock.isActive():
            self._last_tick = time.monotonic()
            self._clock.start()

    def _tick(self) -> None:
        """Repaint the cards whose frame has moved on — or, when behind, rest."""
        now = time.monotonic()
        late = now - self._last_tick - FRAME_MS / 1000
        self._last_tick = now
        if not self._players:
            self._clock.stop()
            return
        self._pace(late, now)
        if self.resting:
            return
        viewport = self.viewport()
        for item_id, (movie, _buffer) in self._players.items():
            number = movie.currentFrameNumber()
            if self._painted.get(item_id) == number:
                continue
            self._painted[item_id] = number
            row = self.model_.row_of(item_id)
            if row is not None:
                viewport.update(self.visualRect(self.model_.index(row, 0)))

    def _pace(self, late: float, now: float) -> None:
        """Stop the animation while the GUI thread is behind; resume once it is not.

        Whatever else holds the window up — a burst of previews, a thread busy
        in Python, a disk that takes its time — the animation must not be the
        thing that tips it from slow into not answering.
        """
        if late > LATE_SECONDS:
            self._late_ticks += 1
            self._calm_since = None
        else:
            self._late_ticks = 0
            if self._calm_since is None:
                self._calm_since = now
        if not self.resting and self._late_ticks >= 2:
            self._rest(True)
        elif self.resting and self._calm_since is not None \
                and now - self._calm_since >= CALM_SECONDS:
            self._rest(False)

    def _rest(self, on: bool) -> None:
        self.resting = on
        for movie, _buffer in self._players.values():
            movie.setPaused(on)

    def _stop_one(self, item_id: str) -> None:
        found = self._players.pop(item_id, None)
        if found is None:
            return
        movie, buffer = found
        movie.stop()
        movie.deleteLater()
        buffer.close()
        buffer.deleteLater()
        self.delegate.movies.pop(item_id, None)
        self._painted.pop(item_id, None)
        self.model_.refresh_row(item_id)

    def _stop_all(self) -> None:
        for item_id in list(self._players):
            self._stop_one(item_id)
        self.delegate.movies.clear()
        self._clock.stop()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._pending.start()

    # -- acting -----------------------------------------------------------

    def mouseReleaseEvent(self, event) -> None:
        index = self.indexAt(event.position().toPoint())
        wallpaper = index.data(WALLPAPER) if index.isValid() else None
        if wallpaper is not None:
            if event.button() == Qt.RightButton:
                self.open_requested.emit(wallpaper.id)
            elif event.button() == Qt.LeftButton and not wallpaper.subscribed:
                self.subscribe_requested.emit(wallpaper.id)
        super().mouseReleaseEvent(event)
