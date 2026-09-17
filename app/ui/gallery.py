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

The marks on a card matter as much as the picture. A wallpaper here may be one
that was owned once and deleted — 327 of one author's 1 155, in the library
this was built against — and re-reviewing those from scratch every week is
exactly the work the tab exists to remove.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterable

from PySide6.QtCore import (
    QAbstractListModel, QBuffer, QByteArray, QIODevice, QModelIndex, QObject,
    QRect, QRunnable, QSize, Qt, QThreadPool, Signal, QTimer)
from PySide6.QtGui import (
    QColor, QFont, QImage, QImageReader, QMovie, QPainter, QPen, QPixmap)
from PySide6.QtWidgets import QListView, QStyle, QStyledItemDelegate

from .. import theme
from ..settings import app_data_dir

THUMB_DIR = app_data_dir() / "thumbs"

# Card geometry. Square, because the previews are: measured on an author's
# first page, 20 of 20 came back 1:1. A 16:10 frame looked like a wallpaper
# and threw away the top and bottom 38% of every picture, which is the
# "zoomed in, half the content missing" it was, next to Wallpaper Engine's own
# grid. The image is fitted inside rather than cropped, so an odd-shaped
# preview is letterboxed instead of cut.
THUMB_W, THUMB_H = 400, 400
CARD_W, CARD_H = THUMB_W + 16, THUMB_H + 60

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

# How hard to look for a frame worth showing, and what counts as one.
MAX_STILL_FRAMES = 24
STILL_MIN_BRIGHTNESS = 0.06

# Roles the delegate reads.
WALLPAPER = Qt.UserRole + 1
IMAGE = Qt.UserRole + 2


# ---- Fetching previews ------------------------------------------------------

class _Fetch(QRunnable):
    """One preview, off the GUI thread."""

    def __init__(self, loader: "ThumbLoader", item_id: str, url: str):
        super().__init__()
        self.loader = loader
        self.item_id = item_id
        self.url = url

    def run(self) -> None:
        import urllib.request

        if self.loader.stopped:
            return
        path = self.loader.path_for(self.item_id)
        data = b""
        if path.exists():
            try:
                data = path.read_bytes()
            except OSError:
                data = b""
        if not data:
            try:
                request = urllib.request.Request(
                    self.url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(request, timeout=20) as response:
                    data = response.read()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            except Exception:  # noqa: BLE001 — a missing preview is not an error
                data = b""
        if self.loader.stopped:
            return
        blob = QByteArray(data)
        try:
            self.loader.done.emit(self.item_id, blob, _still_image(blob))
        except RuntimeError:
            # The gallery was closed while this was in flight. A preview
            # nobody is waiting for is not worth a traceback.
            pass


def _still_image(data: QByteArray) -> QImage:
    """One frame to stand for a preview — decoded here, off the GUI thread.

    Nearly half the previews in a real gallery are animated (46 of 90 in the
    first author looked at), and Steam serves those as GIF. Taking frame zero
    fills the grid with black tiles: these wallpapers commonly fade in, and
    measured on this library four in fourteen were still pure black twelve
    frames in. So frames are read in order until one is bright enough to be a
    picture, with a cap so a wallpaper that really is dark still gets a frame.

    A `QImage`, not a `QPixmap`: pixmaps may only be made on the GUI thread,
    and decoding twenty frames there for ninety cards is a stutter.
    """
    if data.isEmpty():
        return QImage()
    buffer = QBuffer()
    buffer.setData(data)
    buffer.open(QIODevice.ReadOnly)
    try:
        reader = QImageReader(buffer)
        reader.setDecideFormatFromContent(True)
        best = QImage()
        best_light = -1.0
        for _ in range(MAX_STILL_FRAMES):
            frame = reader.read()
            if frame.isNull():
                break
            light = _brightness(frame)
            if light > best_light:
                best, best_light = frame, light
            if light >= STILL_MIN_BRIGHTNESS:
                break
            if not reader.supportsAnimation():
                break
        return best
    finally:
        buffer.close()


def _brightness(frame: QImage) -> float:
    """Mean lightness of a frame, from an 8×8 thumbnail of it."""
    small = frame.scaled(8, 8, Qt.IgnoreAspectRatio, Qt.FastTransformation)
    if small.isNull():
        return 0.0
    total = sum(small.pixelColor(x, y).valueF()
                for x in range(small.width()) for y in range(small.height()))
    return total / max(1, small.width() * small.height())


class ThumbLoader(QObject):
    """Preview images, fetched once and kept on disk."""

    done = Signal(str, QByteArray, QImage)

    def __init__(self, parent=None, threads: int = 6):
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(threads)
        self._asked: set[str] = set()
        # Downloads outlive the widget that wanted them, so they have to be
        # told when nobody is listening any more.
        self.stopped = False

    def path_for(self, item_id: str) -> Path:
        return THUMB_DIR / f"{item_id}.img"

    def request(self, item_id: str, url: str | None) -> None:
        if not url or item_id in self._asked:
            return
        self._asked.add(item_id)
        self.pool.start(_Fetch(self, item_id, url))

    def forget(self) -> None:
        self._asked.clear()

    def stop(self) -> None:
        """Abandon everything in flight and wait for the threads to notice."""
        self.stopped = True
        self.pool.clear()
        self.pool.waitForDone(2000)


# ---- The model --------------------------------------------------------------

class GalleryModel(QAbstractListModel):
    """The wallpapers on offer, plus whatever images have arrived so far."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: list = []
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
        self.beginResetModel()
        self._items = list(items)
        self.endResetModel()

    def item_at(self, row: int):
        return self._items[row] if 0 <= row < len(self._items) else None

    def raw(self, item_id: str) -> QByteArray | None:
        return self._raw.get(item_id)

    def set_image(self, item_id: str, data: QByteArray, frame: QImage) -> None:
        if frame.isNull():
            return
        self._raw[item_id] = data
        self._images[item_id] = QPixmap.fromImage(
            frame.scaled(THUMB_W, THUMB_H, Qt.KeepAspectRatio,
                         Qt.SmoothTransformation))
        for row, wallpaper in enumerate(self._items):
            if wallpaper.id == item_id:
                where = self.index(row, 0)
                self.dataChanged.emit(where, where, [IMAGE])
                break

    def refresh_row(self, item_id: str) -> None:
        for row, wallpaper in enumerate(self._items):
            if wallpaper.id == item_id:
                where = self.index(row, 0)
                self.dataChanged.emit(where, where)
                return


# ---- Drawing one card -------------------------------------------------------

class GalleryDelegate(QStyledItemDelegate):
    """A preview, a title, a date, and the two or three things worth knowing."""

    def __init__(self, parent=None):
        super().__init__(parent)
        # The current animation frame of every card that is playing, which is
        # every animated one on screen.
        self.frames: dict[str, QPixmap] = {}
        self.busy: set[str] = set()              # ids being subscribed right now

    def sizeHint(self, option, index) -> QSize:
        return QSize(CARD_W, CARD_H)

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
        painter.setBrush(QColor(theme.C["surface"] if not selected
                                else theme.C["accent_pressed"]))
        painter.drawRoundedRect(card, 6, 6)

        image_rect = QRect(card.left() + 4, card.top() + 4, THUMB_W, THUMB_H)
        pixmap = self.frames.get(wallpaper.id) or index.data(IMAGE)
        if pixmap is not None and not pixmap.isNull():
            # The whole picture, centred; anything that is not square gets bars
            # in the page colour rather than losing its edges.
            painter.setBrush(QColor(theme.C["bg"]))
            painter.drawRect(image_rect)
            painter.drawPixmap(
                image_rect.left() + (THUMB_W - pixmap.width()) // 2,
                image_rect.top() + (THUMB_H - pixmap.height()) // 2, pixmap)
        else:
            painter.setBrush(QColor(theme.C["raised"]))
            painter.drawRect(image_rect)
            painter.setPen(QColor(theme.C["faint"]))
            painter.drawText(image_rect, Qt.AlignCenter, "…")

        # Marks, top-left over the image, so they read before the picture does.
        marks: list[tuple[str, str]] = []
        if wallpaper.subscribed:
            marks.append(("subscribed", theme.C["ok"]))
        if wallpaper.once_had:
            marks.append(("was yours", theme.C["warn"]))
        if wallpaper.in_queue:
            marks.append(("queued", theme.C["accent"]))
        if wallpaper.new_since_visit:
            marks.append(("new", theme.C["ok"]))
        x = image_rect.left() + 4
        for text, colour in marks:
            width = painter.fontMetrics().horizontalAdvance(text) + 10
            chip = QRect(x, image_rect.top() + 6, width, 18)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(colour))
            painter.drawRoundedRect(chip, 3, 3)
            painter.setPen(QColor("#101216"))
            painter.drawText(chip, Qt.AlignCenter, text)
            x += width + 4

        if wallpaper.subscribed:
            # Dim it rather than remove it: a card vanishing under the cursor
            # loses the place in a wall of four hundred.
            painter.setBrush(QColor(0, 0, 0, 110))
            painter.setPen(Qt.NoPen)
            painter.drawRect(image_rect)

        if wallpaper.id in self.busy:
            badge = QRect(image_rect.right() - 96, image_rect.bottom() - 26, 92, 22)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(theme.C["accent"]))
            painter.drawRoundedRect(badge, 3, 3)
            painter.setPen(QColor("#101216"))
            painter.drawText(badge, Qt.AlignCenter, "subscribing…")
        elif hovered and not wallpaper.subscribed:
            hint = QRect(image_rect.left(), image_rect.bottom() - 26,
                         image_rect.width(), 22)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(0, 0, 0, 150))
            painter.drawRect(hint)
            painter.setPen(QColor(theme.C["text"]))
            painter.drawText(hint, Qt.AlignCenter, "click to subscribe")

        title = wallpaper.title or wallpaper.id
        painter.setPen(QColor(theme.C["text"]))
        font = QFont(painter.font())
        font.setPointSize(10)
        painter.setFont(font)
        text_rect = QRect(card.left() + 6, image_rect.bottom() + 5,
                          card.width() - 12, 18)
        painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter,
                         painter.fontMetrics().elidedText(
                             title, Qt.ElideRight, text_rect.width()))

        # What it is and what it costs to download, before when it was made:
        # a 225 MB video and a 6 MB scene are different decisions.
        painter.setPen(QColor(theme.C["faint"]))
        when = wallpaper.created
        facts = [wallpaper.item.kind, wallpaper.item.size_text,
                 when.strftime("%Y-%m-%d") if when else ""]
        painter.drawText(QRect(card.left() + 6, text_rect.bottom() + 2,
                               card.width() - 12, 16),
                         Qt.AlignLeft | Qt.AlignVCenter,
                         "  ·  ".join(f for f in facts if f))

        if hovered or selected:
            painter.setPen(QPen(QColor(theme.C["accent"]), 1))
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

        # Rows whose player has a new frame, and the one clock that repaints
        # them. 50 ms is twenty repaints a second for the whole page, however
        # many players are running and whatever rate each of them wants.
        self._dirty: set[str] = set()
        self._repaint = QTimer(self)
        self._repaint.setInterval(50)
        self._repaint.setSingleShot(True)
        self._repaint.timeout.connect(self._repaint_dirty)

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
        if data is None:
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
        # Deliberately *not* setScaledSize: that stretches the frame to the
        # card and the picture visibly changes shape the moment it starts
        # moving. Frames are scaled the same way the still is — expanded to
        # cover, then cropped when painted — so nothing shifts.
        movie.frameChanged.connect(lambda _n, key=item_id: self._frame(key))
        self._players[item_id] = (movie, buffer)
        movie.start()

    def _frame(self, item_id: str) -> None:
        """A player has a new frame: scale it, and ask for one repaint soon.

        Every player runs at whatever rate its GIF asks for, and each frame
        used to repaint its row on the spot. Twenty-one players on a page came
        to 178 frames a second, each one a `dataChanged` the view had to answer
        — measured at 334 ms of lag on a GUI thread that should never be more
        than a frame behind. The scaling stays per frame, because that is what
        is being shown; the repainting is collected and done on one clock, so
        the cost is the page's, not the sum of every player's.
        """
        found = self._players.get(item_id)
        if found is None:
            return
        image = found[0].currentImage()
        if image.isNull():
            return
        self.delegate.frames[item_id] = QPixmap.fromImage(
            image.scaled(THUMB_W, THUMB_H, Qt.KeepAspectRatio,
                         Qt.SmoothTransformation))
        self._dirty.add(item_id)
        if not self._repaint.isActive():
            self._repaint.start()

    def _repaint_dirty(self) -> None:
        """Repaint every row that got a new frame since the last tick."""
        dirty, self._dirty = self._dirty, set()
        for item_id in dirty:
            self.model_.refresh_row(item_id)

    def _stop_one(self, item_id: str) -> None:
        found = self._players.pop(item_id, None)
        if found is None:
            return
        movie, buffer = found
        movie.stop()
        movie.deleteLater()
        buffer.close()
        buffer.deleteLater()
        self.delegate.frames.pop(item_id, None)
        self._dirty.discard(item_id)
        self.model_.refresh_row(item_id)

    def _stop_all(self) -> None:
        for item_id in list(self._players):
            self._stop_one(item_id)
        self.delegate.frames.clear()

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
