"""gallery.py — an author's unsubscribed wallpapers: a grid of cards, or a list.

The list of authors answers *who*; this answers *what*, and it has to do it for
one thousand two hundred wallpapers without the window noticing. Four things
make that possible and each is a deliberate choice rather than a default:

**Virtualisation.** The grid is a `QListView` in icon mode with uniform item
sizes, which only paints the cards it can see; the list is the kit's `Table`.
The alternative — a widget per wallpaper in a grid layout — is 1 200 widgets,
and it is not close.

**Previews arrive later than the grid.** Thumbnails are fetched on a thread
pool and cached on disk under `data/thumbs`, keyed by workshop id. A card draws
its frame, its title and its mark immediately and repaints once when its image
lands; nothing waits for the network.

**A card is a few copies, not a drawing.** PySide gives up the GIL on every
call into Qt and waits for it back, so while another thread is busy in Python
each call can be a wait. So what a card shows is made once and copied: the
preview cropped to 16:9 with its rounded corners and its edge (`tile`), the
title and its date (`words`), the "scene · 214 MB" plate, the chip, the
checks, the hover's "Click to subscribe". A still card is about six calls.

**Everything on screen animates, within a budget.** Steam serves an animated
wallpaper's preview as a real GIF, so `QMovie` plays it with no help, and
nearly half of a real gallery is animated. Thirty to a page keeps the number of
decoders fixed; at most `MAX_PLAYERS` play, the ones nearest the top; Qt scales
each frame itself, to the size that covers the card; and one clock twenty
times a second repaints the cards whose frame moved on. If that clock finds
itself running late the animation rests until the window keeps up again.

The marks on a card (`card_mark`) say what the machine knows about each
wallpaper: **New** since your last visit, **Already have** (a copy kept in the
Rotator's libraries, and where), **Was yours** (subscribed once and gone —
Wallpaper Engine's folders remember it), **Queued** (in the folder being
reviewed), **Subscribed**. A subscribed or already-had card is set back where
it stands, never removed, so your place in the grid is kept.

A click subscribes. Ctrl- or Shift-click, or the check offered on a card under
the pointer, select instead, and Esc clears the selection. The selection is
the gallery's, shared by the grid and the list, and it spans pages.
"""
from __future__ import annotations

import math
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Iterable

from PySide6.QtCore import (
    QAbstractListModel, QBuffer, QByteArray, QEvent, QIODevice, QModelIndex, QPointF, QRect,
    QRectF, QSize, Qt, QTimer, Signal,
)
from PySide6.QtGui import (
    QCursor, QFont, QFontMetricsF, QImage, QMovie, QPainter, QPainterPath, QPen, QPixmap,
)
from PySide6.QtWidgets import QAbstractItemView, QListView, QStyledItemDelegate

from .. import animations, theme
from ..engines.library import DUPLICATES, LIBRARY, RESERVE, ROTATION
from .kit import format as fmt, icons
from .kit.chips import chip_pixmap, chip_size
from .kit.tables import (
    BusyCell, ButtonCell, Cell, ChipCell, Column, DiscCell, Table, TableModel, disc_pixmap,
    paint_spinner,
)
from .kit.thumbs import ThumbLoader

# Wallpapers to a page. Fetching a thousand previews to look at the newest few
# is the wait this removes; it also caps how many decoders animation needs.
PAGE_SIZE = theme.GALLERY_PAGE

# How many previews may animate at the same time. Starting thirty decoders in
# the burst in which their downloads land is what once made the window stop
# answering at all; bounding them costs a few still cards at the bottom of a
# page. They are started on a clock rather than on arrival for the same reason.
MAX_PLAYERS = theme.GALLERY_PLAYERS

# The animation clock. GIF previews here run at 25 frames a second at most, so
# 20 repaints a second drops one frame in five and nothing the eye follows.
FRAME_MS = theme.GALLERY_FRAME_MS
# When the clock is this late twice running, the GUI thread is behind with
# something, and the animation stops adding to it...
LATE_SECONDS = theme.GALLERY_LATE
# ...until the clock has been on time for this long.
CALM_SECONDS = theme.GALLERY_CALM

# Roles the delegate reads.
WALLPAPER = Qt.UserRole + 1
IMAGE = Qt.UserRole + 2

# What a card being worked on says.
WAITING = "waiting"            # in the queue to be subscribed
SUBSCRIBING = "subscribing"    # Steam is being asked now

# Room round a card for its halo and focus ring, inside the item's rectangle.
MARGIN = theme.FOCUS_RING + 1


# ---- What a card says ---------------------------------------------------------

@dataclass(frozen=True)
class Mark:
    """What a card says about its wallpaper, and how it looks for it."""
    chip: str | None        # the Chip variant, None for none
    text: str | None        # the chip's words, where they are not the variant's own
    edge: str               # the colour of the card's edge
    dimmed: bool            # set back where it stands
    offer: bool             # a click subscribes to it


SUBSCRIBED = Mark("Subscribed", None, "info.line", True, False)
HAVE = Mark("Duplicated", "Already have", "warn.line", True, False)
QUEUED = Mark("Queued", None, "border.hairline", False, True)
YOURS = Mark("WasYours", None, "ok.line", False, True)
NEW = Mark("New", None, "accent.line", False, True)
UNKNOWN = Mark(None, None, "border.hairline", False, True)


def card_mark(wallpaper) -> Mark:
    """The one mark a card carries, the most telling first: subscribed now, a
    copy already kept, in the folder being reviewed, subscribed once and gone,
    new to you. Before anything has asked what you had, none."""
    if wallpaper.subscribed:
        return SUBSCRIBED
    if wallpaper.in_library:
        return HAVE
    if wallpaper.in_queue:
        return QUEUED
    if wallpaper.once_had:
        return YOURS
    if wallpaper.unseen:
        return NEW
    return UNKNOWN


PLACES = {ROTATION: "myprojects", RESERVE: "the reserve", DUPLICATES: "the duplicates folder",
          LIBRARY: "your library"}


def card_line(wallpaper, now=None) -> str:
    """The line under a card's title: where the copy you already have is, or
    when the wallpaper was published."""
    if wallpaper.in_library:
        return f"matches a folder in {PLACES.get(wallpaper.library_place, PLACES[LIBRARY])}"
    created = wallpaper.created
    return f"added {fmt.day(created, now)}" if created else ""


def card_meta(wallpaper) -> tuple[str, str]:
    """("scene", "214 MB"): what it is and what it weighs, for the plate."""
    item = wallpaper.item
    return (item.kind or "").lower(), fmt.size(item.file_size) if item.file_size else ""


def offered(wallpaper, busy: str | None = None) -> bool:
    """Whether a click can subscribe to it now (and so whether it can be
    selected): not taken, not already had, not under way."""
    return card_mark(wallpaper).offer and busy is None


# ---- The grid's geometry ---------------------------------------------------------

def columns_for(width: float) -> int:
    """How many cards across a grid this wide holds: as many cards of about
    `GALLERY_CARD` px as fit with their gaps, never fewer than three or more
    than five."""
    gap = theme.GALLERY_GAP
    low, high = theme.GALLERY_COLUMNS
    return max(low, min(high, round((width - gap) / (theme.GALLERY_CARD + gap))))


@dataclass(frozen=True)
class CardGeometry:
    columns: int
    width: int              # a card
    height: int
    preview: int            # its preview's height: 16:9 of the width
    title: int              # its title's line
    line: int               # and the line under it


def card_geometry(width: float) -> CardGeometry:
    """The cards of a grid `width` px across, gaps round it included."""
    gap = theme.GALLERY_GAP
    columns = columns_for(width)
    # A list view wraps a row once its items reach the viewport's edge, not
    # past it: leave it a pixel or two, or the last column wraps.
    card = max(1, int((width - gap * (columns + 1) - theme.GALLERY_SLACK) // columns))
    preview = round(card * 9 / 16)
    title = math.ceil(theme.line_height("type.bodySm"))
    line = math.ceil(theme.line_height("type.monoXs"))
    height = preview + theme.GALLERY_TEXT_GAP + title + theme.GALLERY_TEXT_GAP + line
    return CardGeometry(columns, card, height, preview, title, line)


def cover_source(size: QSize, width: float, height: float) -> QRectF:
    """The centred part of a picture of `size` with the shape of `width` ×
    `height`: what covering that box shows. WE previews are mostly square, the
    cards 16:9, so a square one loses its top and bottom fifth."""
    sw, sh = max(1, size.width()), max(1, size.height())
    ratio = width / max(1e-6, height)
    if sw / sh > ratio:
        cut = sh * ratio
        return QRectF((sw - cut) / 2, 0, cut, sh)
    cut = sw / ratio
    return QRectF(0, (sh - cut) / 2, sw, cut)


def cover_size(size: QSize, width: float, height: float) -> QSize:
    """A picture of `size` scaled so it just covers `width` × `height` px."""
    sw, sh = max(1, size.width()), max(1, size.height())
    scale = max(width / sw, height / sh)
    return QSize(max(1, math.ceil(sw * scale)), max(1, math.ceil(sh * scale)))


def _rounded(rect: QRectF, radius: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


def cover_pixmap(image: QPixmap, width: float, height: float, dpr: float) -> QPixmap:
    """The picture cropped to the box and scaled to it, at the screen's scale."""
    out = QPixmap(max(1, math.ceil(width * dpr)), max(1, math.ceil(height * dpr)))
    out.setDevicePixelRatio(dpr)
    out.fill(Qt.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    painter.drawPixmap(QRectF(0, 0, width, height), image,
                       cover_source(image.size(), width * dpr, height * dpr))
    painter.end()
    return out


# ---- The model ---------------------------------------------------------------------

class GalleryModel(QAbstractListModel):
    """The wallpapers of the page on screen, and what the gallery knows about
    them: the previews that have arrived, which are being subscribed to, and
    which are selected (on any page)."""

    image_ready = Signal(str)               # a preview arrived for a wallpaper shown
    image_dropped = Signal(str)             # one let go of, to keep memory bounded
    busy_changed = Signal(str)              # a wallpaper started or stopped being worked on
    selection_changed = Signal(int)         # how many are selected now

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: list = []
        self._rows: dict[str, int] = {}
        self._images: OrderedDict[str, QPixmap] = OrderedDict()
        self._raw: dict[str, QByteArray] = {}
        self._missing: set[str] = set()
        self.busy: dict[str, str] = {}               # id → WAITING or SUBSCRIBING
        self.selected: dict[str, None] = {}          # ids, in the order chosen
        self.anchor: str | None = None               # where a Shift-click counts from
        # A click opens the wallpaper's page in Steam rather than subscribing
        # (Review settings' "Steam's page"): the cards and buttons say so.
        self.opens_page = False

    # -- Qt's side

    def rowCount(self, parent=QModelIndex()) -> int:     # noqa: N802 - Qt's name
        return 0 if parent.isValid() else len(self._items)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or index.row() >= len(self._items):
            return None
        wallpaper = self._items[index.row()]
        if role == WALLPAPER:
            return wallpaper
        if role == IMAGE:
            return self._images.get(wallpaper.id)
        if role in (Qt.DisplayRole, Qt.ToolTipRole, Qt.AccessibleTextRole):
            return wallpaper.title or wallpaper.id
        return None

    # -- the page

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
        for gone in [k for k in self._images if k not in self._rows]:
            del self._images[gone]
        self._raw = {k: v for k, v in self._raw.items() if k in self._rows}
        self.endResetModel()

    def items(self) -> list:
        return list(self._items)

    def item_at(self, row: int):
        return self._items[row] if 0 <= row < len(self._items) else None

    def row_of(self, item_id: str) -> int | None:
        return self._rows.get(item_id)

    def find(self, item_id: str):
        row = self._rows.get(item_id)
        return self._items[row] if row is not None else None

    # -- previews

    def raw(self, item_id: str) -> QByteArray | None:
        return self._raw.get(item_id)

    def image(self, item_id: str) -> QPixmap | None:
        found = self._images.get(item_id)
        if found is not None:
            self._images.move_to_end(item_id)
        return found

    def missing(self, item_id: str) -> bool:
        """Its preview came back empty: there is none to show."""
        return item_id in self._missing

    def set_image(self, item_id: str, data: QByteArray, frame: QImage) -> None:
        row = self._rows.get(item_id)
        if row is None:
            # A preview for a page since left. Its bytes are on disk already.
            return
        if frame.isNull():
            self._missing.add(item_id)
        else:
            self._missing.discard(item_id)
            self._raw[item_id] = data
            biggest = theme.GALLERY_STILL_MAX
            if frame.width() > biggest or frame.height() > biggest:
                frame = frame.scaled(biggest, biggest, Qt.KeepAspectRatio,
                                     Qt.SmoothTransformation)
            self._images[item_id] = QPixmap.fromImage(frame)
            self._images.move_to_end(item_id)
            # A page is thirty; a list of every author can hold a thousand, and
            # the ones scrolled past are dropped (and asked for again if wanted).
            while len(self._images) > max(theme.GALLERY_IMAGES, PAGE_SIZE):
                gone, _ = self._images.popitem(last=False)
                self._raw.pop(gone, None)
                self.image_dropped.emit(gone)
        where = self.index(row, 0)
        self.dataChanged.emit(where, where, [IMAGE])
        self.image_ready.emit(item_id)

    def refresh_row(self, item_id: str) -> None:
        row = self._rows.get(item_id)
        if row is not None:
            where = self.index(row, 0)
            self.dataChanged.emit(where, where)

    def refresh_all(self) -> None:
        """Repaint the whole page — for something that changed every card."""
        if self._items:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self._items) - 1, 0))

    # -- work under way

    def set_busy(self, item_id: str, state: str | None) -> None:
        if state is None:
            if self.busy.pop(item_id, None) is None:
                return
        else:
            if self.busy.get(item_id) == state:
                return
            self.busy[item_id] = state
            self._deselect([item_id])
        self.refresh_row(item_id)
        self.busy_changed.emit(item_id)

    # -- the selection

    def is_selected(self, item_id: str) -> bool:
        return item_id in self.selected

    def toggle(self, item_id: str) -> bool:
        """Select it, or let it go; True when it is selected after."""
        if item_id in self.selected:
            del self.selected[item_id]
            chosen = False
        else:
            self.selected[item_id] = None
            chosen = True
        self.anchor = item_id
        self.refresh_row(item_id)
        self.selection_changed.emit(len(self.selected))
        return chosen

    def select(self, item_ids: Iterable[str]) -> None:
        """Add these to the selection."""
        added = [i for i in item_ids if i not in self.selected]
        if not added:
            return
        for item_id in added:
            self.selected[item_id] = None
            self.refresh_row(item_id)
        self.selection_changed.emit(len(self.selected))

    def clear_selection(self) -> bool:
        """Select nothing; False when nothing was."""
        if not self.selected:
            return False
        chosen = list(self.selected)
        self.selected.clear()
        self.anchor = None
        for item_id in chosen:
            self.refresh_row(item_id)
        self.selection_changed.emit(0)
        return True

    def _deselect(self, item_ids: Iterable[str]) -> None:
        dropped = [i for i in item_ids if self.selected.pop(i, False) is None]
        if dropped:
            self.selection_changed.emit(len(self.selected))


# ---- Drawing one card -------------------------------------------------------------

class GalleryDelegate(QStyledItemDelegate):
    """A card: the preview with its mark, its plate and its edge, the title,
    and the line under it — and what the pointer, a selection or a
    subscription under way add on top.

    Almost all of it is copied from pixmaps made once per card and size (see
    the module's docstring); what is painted afresh is the moving frame of an
    animated preview, the halo of the card under the pointer, and a spinner.
    """

    TILE_LIMIT = 3 * PAGE_SIZE

    def __init__(self, view: "GalleryView | None" = None):
        super().__init__(view)
        self.view = view
        # The player of every card that is animating; its current frame is read
        # at paint time, already scaled by Qt.
        self.movies: dict[str, QMovie] = {}
        self.geometry = card_geometry(theme.GALLERY_CARD * 3 + theme.GALLERY_GAP * 4)
        self._tiles: OrderedDict[tuple, QPixmap] = OrderedDict()
        self._words: OrderedDict[tuple, QPixmap] = OrderedDict()
        self._plates: dict[tuple, QPixmap] = {}
        self._shades: dict[tuple, QPixmap] = {}
        self._paths: dict[tuple, QPainterPath] = {}
        self._clips: dict[tuple, QPainterPath] = {}
        self._sources: dict[str, tuple] = {}

    # -- sizes

    def item_size(self) -> QSize:
        g = self.geometry
        return QSize(g.width + 2 * MARGIN, g.height + 2 * MARGIN)

    def sizeHint(self, option, index) -> QSize:        # noqa: N802 - Qt's name
        return self.item_size()

    def set_geometry(self, geometry: CardGeometry) -> bool:
        if geometry == self.geometry:
            return False
        self.geometry = geometry
        self._tiles.clear()
        self._words.clear()
        self._shades.clear()
        self._clips.clear()
        return True

    def card_rect(self, item_rect: QRect) -> QRectF:
        """The card inside its item's rectangle."""
        g = self.geometry
        return QRectF(item_rect.x() + MARGIN, item_rect.y() + MARGIN, g.width, g.height)

    def preview_rect(self, item_rect: QRect) -> QRectF:
        card = self.card_rect(item_rect)
        return QRectF(card.x(), card.y(), card.width(), self.geometry.preview)

    def check_rect(self, item_rect: QRect) -> QRectF:
        """The selection check, top right of the preview."""
        preview = self.preview_rect(item_rect)
        size, inset = theme.GALLERY_CHECK, theme.GALLERY_INSET
        return QRectF(preview.right() - inset - size, preview.top() + inset, size, size)

    def spinner_rect(self, item_rect: QRect) -> QRectF:
        preview = self.preview_rect(item_rect)
        width = self._shade_layout("subscribing")[0]
        size = theme.GALLERY_SPINNER
        return QRectF(preview.center().x() - width / 2, preview.center().y() - size / 2,
                      size, size)

    # -- what is made once

    def _path(self, width: float, height: float) -> QPainterPath:
        key = (width, height)
        path = self._paths.get(key)
        if path is None:
            path = self._paths[key] = _rounded(QRectF(0, 0, width, height), theme.R_ROW)
        return path

    def tile(self, wallpaper, image: QPixmap | None, state: str, mark: Mark,
             dpr: float) -> QPixmap:
        """The card's preview as one picture: the preview covering its well
        (or the empty well while it loads, the placeholder's cross when there
        is none), rounded, with the edge of its mark, the chip, the plate and
        a subscribed card's check — so a still card is one copy."""
        g = self.geometry
        key = (wallpaper.id, state, mark, g.width, g.preview, round(dpr, 3))
        found = self._tiles.get(key)
        if found is not None:
            self._tiles.move_to_end(key)
            return found
        width, height = g.width, g.preview
        tile = QPixmap(math.ceil(width * dpr), math.ceil(height * dpr))
        tile.setDevicePixelRatio(dpr)
        tile.fill(Qt.transparent)
        painter = QPainter(tile)
        painter.setRenderHint(QPainter.Antialiasing)
        path = self._path(width, height)
        painter.fillPath(path, theme.color("surface.well"))
        painter.setClipPath(path)
        if state == "image" and image is not None:
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            painter.drawPixmap(QRectF(0, 0, width, height), image,
                               cover_source(image.size(), width * dpr, height * dpr))
        elif state == "placeholder":
            painter.setPen(QPen(theme.color("thumb.cross"), 1))
            painter.drawLine(QPointF(0, 0), QPointF(width, height))
            painter.drawLine(QPointF(width, 0), QPointF(0, height))
        painter.setClipping(False)
        self._marks(painter, wallpaper, mark, dpr)
        painter.end()
        self._keep(self._tiles, key, tile)
        return tile

    def overlay(self, wallpaper, mark: Mark, dpr: float) -> QPixmap:
        """What a playing preview has over its frame — the edge, the chip, the
        plate, the check — on clear ground: one copy after the frame."""
        g = self.geometry
        key = (wallpaper.id, "overlay", mark, g.width, g.preview, round(dpr, 3))
        found = self._tiles.get(key)
        if found is not None:
            self._tiles.move_to_end(key)
            return found
        out = QPixmap(math.ceil(g.width * dpr), math.ceil(g.preview * dpr))
        out.setDevicePixelRatio(dpr)
        out.fill(Qt.transparent)
        painter = QPainter(out)
        painter.setRenderHint(QPainter.Antialiasing)
        self._marks(painter, wallpaper, mark, dpr)
        painter.end()
        self._keep(self._tiles, key, out)
        return out

    def _marks(self, painter: QPainter, wallpaper, mark: Mark, dpr: float) -> None:
        """The edge, chip, plate and check, at the preview's own origin."""
        g = self.geometry
        width, height = g.width, g.preview
        painter.setPen(QPen(theme.color(mark.edge), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(QRectF(0.5, 0.5, width - 1, height - 1),
                                theme.R_ROW - 0.5, theme.R_ROW - 0.5)
        inset, ring = theme.GALLERY_INSET, theme.FOCUS_RING
        if mark.chip:
            painter.drawPixmap(QPointF(inset - ring, inset - ring),
                               self.badge(mark.chip, mark.text, dpr))
        kind, size = card_meta(wallpaper)
        plate = self.plate(kind, size, bool(wallpaper.item.large), dpr)
        if plate is not None:
            plate_height = plate.deviceIndependentSize().height()
            painter.drawPixmap(QPointF(inset, height - inset - plate_height), plate)
        if mark is SUBSCRIBED:
            check = theme.GALLERY_CHECK
            painter.drawPixmap(QPointF(width - inset - check, height - inset - check),
                               disc_pixmap("check", "info", check, dpr))

    def _keep(self, cache: OrderedDict, key, pixmap: QPixmap) -> None:
        cache[key] = pixmap
        while len(cache) > self.TILE_LIMIT:
            cache.popitem(last=False)

    def words(self, wallpaper, dimmed: bool, dpr: float) -> QPixmap:
        """The title and the line under it, elided to the card."""
        g = self.geometry
        key = (wallpaper.id, dimmed, g.width, round(dpr, 3))
        found = self._words.get(key)
        if found is not None:
            return found
        title, line = wallpaper.title or wallpaper.id, card_line(wallpaper)
        height = g.title + theme.GALLERY_TEXT_GAP + g.line
        out = QPixmap(math.ceil(g.width * dpr), math.ceil(height * dpr))
        out.setDevicePixelRatio(dpr)
        out.fill(Qt.transparent)
        painter = QPainter(out)
        font = theme.font("type.bodySm")
        painter.setFont(font)
        painter.setPen(theme.color("text.mid" if dimmed else "text.body"))
        painter.drawText(QRectF(0, 0, g.width, g.title), Qt.AlignLeft | Qt.AlignVCenter,
                         QFontMetricsF(font).elidedText(title, Qt.ElideRight, g.width))
        if line:
            small = theme.font("type.monoXs")
            painter.setFont(small)
            painter.setPen(theme.color("text.lo"))
            painter.drawText(QRectF(0, g.title + theme.GALLERY_TEXT_GAP, g.width, g.line),
                             Qt.AlignLeft | Qt.AlignVCenter,
                             QFontMetricsF(small).elidedText(line, Qt.ElideRight, g.width))
        painter.end()
        self._keep(self._words, key, out)
        return out

    def plate(self, kind: str, size: str, large: bool, dpr: float) -> QPixmap | None:
        """"scene · 214 MB" on its dark plate; a download of a gigabyte or more
        says its size in the warning colour."""
        if not kind and not size:
            return None
        key = (kind, size, large, round(dpr, 3))
        found = self._plates.get(key)
        if found is not None:
            return found
        font = theme.font("type.monoXs")
        metrics = QFontMetricsF(font)
        lead = f"{kind} · " if kind and size else kind
        width_lead = metrics.horizontalAdvance(lead)
        width = width_lead + metrics.horizontalAdvance(size)
        pad_v, pad_h = theme.GALLERY_PLATE_PAD
        box = QRectF(0, 0, math.ceil(width + 2 * pad_h), math.ceil(metrics.height() + 2 * pad_v))
        out = QPixmap(math.ceil(box.width() * dpr), math.ceil(box.height() * dpr))
        out.setDevicePixelRatio(dpr)
        out.fill(Qt.transparent)
        painter = QPainter(out)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillPath(_rounded(box, theme.GALLERY_PLATE_RADIUS), theme.color("gallery.plate"))
        painter.setFont(font)
        painter.setPen(theme.color("text.mid"))
        text_box = box.adjusted(pad_h, 0, -pad_h, 0)
        painter.drawText(text_box, Qt.AlignLeft | Qt.AlignVCenter, lead)
        if size:
            painter.setPen(theme.color("warn" if large else "text.mid"))
            painter.drawText(text_box.adjusted(width_lead, 0, 0, 0),
                             Qt.AlignLeft | Qt.AlignVCenter, size)
        painter.end()
        self._plates[key] = out
        return out

    def badge(self, variant: str, text: str | None, dpr: float) -> QPixmap:
        """The mark's chip on a dark backing of its own shape: a chip's soft
        fill is made for the panel, and over a bright preview its words would
        all but vanish."""
        key = ("badge", variant, text, round(dpr, 3))
        found = self._plates.get(key)
        if found is not None:
            return found
        chip = chip_pixmap(variant, text, "default", dpr)
        out = QPixmap(chip.size())
        out.setDevicePixelRatio(dpr)
        out.fill(Qt.transparent)
        painter = QPainter(out)
        painter.setRenderHint(QPainter.Antialiasing)
        box = chip_size(variant, text)
        ring = theme.FOCUS_RING
        painter.fillPath(_rounded(QRectF(ring, ring, box.width(), box.height()), box.height() / 2),
                         theme.color("gallery.plate"))
        painter.drawPixmap(QPointF(0, 0), chip)
        painter.end()
        self._plates[key] = out
        return out

    def _shade_layout(self, kind: str) -> tuple[float, str, str | None]:
        """(content width, words, lead glyph) of a shade's centred line."""
        words, glyph, lead = {
            "hint": ("Click to subscribe", "plus", theme.GALLERY_HINT_ICON),
            "page": ("Open in Steam", "ext", theme.GALLERY_HINT_ICON),
            "subscribing": ("Subscribing…", None, theme.GALLERY_SPINNER),
            "waiting": ("Waiting…", "clock", theme.GALLERY_HINT_ICON),
        }[kind]
        token = "type.bodySm"
        font = theme.font(token)
        if kind in ("hint", "page"):
            font.setWeight(QFont.DemiBold)
        width = lead + theme.GALLERY_HINT_GAP + QFontMetricsF(font).horizontalAdvance(words)
        return width, words, glyph

    def shade(self, kind: str, dpr: float) -> QPixmap:
        """The preview darkened, with a centred line: "+ Click to subscribe",
        the room for a spinner and "Subscribing…", or "Waiting…"."""
        g = self.geometry
        key = (kind, g.width, g.preview, round(dpr, 3))
        found = self._shades.get(key)
        if found is not None:
            return found
        width, words, glyph = self._shade_layout(kind)
        out = QPixmap(math.ceil(g.width * dpr), math.ceil(g.preview * dpr))
        out.setDevicePixelRatio(dpr)
        out.fill(Qt.transparent)
        painter = QPainter(out)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillPath(self._path(g.width, g.preview), theme.color("gallery.shade"))
        x = (g.width - width) / 2
        middle = g.preview / 2
        lead = theme.GALLERY_SPINNER if kind == "subscribing" else theme.GALLERY_HINT_ICON
        bold = kind in ("hint", "page")
        if glyph:
            painter.drawPixmap(QPointF(x, middle - lead / 2),
                               icons.pixmap(glyph, "text.hi", lead, dpr))
        x += lead + theme.GALLERY_HINT_GAP
        font = theme.font("type.bodySm")
        if bold:
            font.setWeight(QFont.DemiBold)
        painter.setFont(font)
        painter.setPen(theme.color("text.hi"))
        painter.drawText(QRectF(x, 0, g.width - x, g.preview), Qt.AlignLeft | Qt.AlignVCenter,
                         words)
        painter.end()
        self._shades[key] = out
        return out

    def empty_check(self, dpr: float) -> QPixmap:
        """The check offered on a card under the pointer: an empty ring."""
        key = ("check", round(dpr, 3))
        found = self._shades.get(key)
        if found is not None:
            return found
        size = theme.GALLERY_CHECK
        out = QPixmap(math.ceil(size * dpr), math.ceil(size * dpr))
        out.setDevicePixelRatio(dpr)
        out.fill(Qt.transparent)
        painter = QPainter(out)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(theme.color("gallery.check"))
        painter.setPen(QPen(theme.color("text.hi"), 1.5))
        painter.drawEllipse(QRectF(0.75, 0.75, size - 1.5, size - 1.5))
        painter.end()
        self._shades[key] = out
        return out

    def forget(self, item_id: str) -> None:
        """Drop what was made for one card (its preview arrived, or went)."""
        for key in [k for k in self._tiles if k[0] == item_id]:
            del self._tiles[key]
        self._sources.pop(item_id, None)

    def frame_source(self, item_id: str, frame: QPixmap, dpr: float) -> QRectF:
        """The part of a player's frame that covers the card, worked out once
        per player and size (Qt scales its frames to that size already)."""
        g = self.geometry
        size = frame.size()
        key = (size.width(), size.height(), g.width, g.preview, round(dpr, 3))
        found = self._sources.get(item_id)
        if found is None or found[0] != key:
            found = (key, cover_source(size, g.width * dpr, g.preview * dpr))
            self._sources[item_id] = found
        return found[1]

    def _clip(self, x: int, y: int) -> QPainterPath:
        g = self.geometry
        key = (x, y, g.width, g.preview)
        path = self._clips.get(key)
        if path is None:
            if len(self._clips) > self.TILE_LIMIT:
                self._clips.clear()
            path = self._clips[key] = self._path(g.width, g.preview).translated(x, y)
        return path

    # -- painting

    def paint(self, painter: QPainter, option, index) -> None:
        """One card. Every call into Qt here can wait for the GIL while another
        thread is busy in Python, so the common cases are short: a still card
        is a copy of its tile and one of its words; a playing one is its frame
        through a rounded clip, then its overlay and its words."""
        view = self.view
        rect = option.rect
        if view is not None:
            # The view repaints every card in the dirty region's bounding box:
            # for eight playing previews spread over a page that is all of
            # them. A card the region itself misses is skipped.
            region = view.dirty
            if region is not None and not region.intersects(rect):
                return
            model = view.model_
            row = index.row()
            wallpaper = model.item_at(row)
            dpr = view.paint_dpr
            if view.fresh_pass:
                painter.setRenderHint(QPainter.Antialiasing)
                view.fresh_pass = False
        else:
            model = index.model()
            row = index.row()
            wallpaper = index.data(WALLPAPER)
            dpr = painter.device().devicePixelRatioF()
            painter.setRenderHint(QPainter.Antialiasing)
        if wallpaper is None:
            return
        item_id = wallpaper.id
        mark = card_mark(wallpaper)
        busy = model.busy.get(item_id)
        selected = item_id in model.selected
        hovered = view is not None and view.hovered_row() == row
        x, y = rect.x() + MARGIN, rect.y() + MARGIN
        g = self.geometry
        dimmed = mark.dimmed and not selected
        if dimmed:
            painter.setOpacity(theme.GALLERY_DIM)

        movie = self.movies.get(item_id)
        frame = movie.currentPixmap() if movie is not None else None
        if frame is not None and not frame.isNull():
            painter.setClipPath(self._clip(x, y))
            painter.drawPixmap(QRectF(x, y, g.width, g.preview), frame,
                               self.frame_source(item_id, frame, dpr))
            painter.setClipping(False)
            painter.drawPixmap(x, y, self.overlay(wallpaper, mark, dpr))
        else:
            image = model.image(item_id)
            if image is not None:
                state = "image"
            elif not wallpaper.item.preview or model.missing(item_id):
                state = "placeholder"
            else:
                state = "loading"
            painter.drawPixmap(x, y, self.tile(wallpaper, image, state, mark, dpr))

        if busy is not None or (hovered and mark.offer) or selected:
            self._paint_extras(painter, rect, busy, hovered, selected, mark, dpr)
        elif view is not None and view.focus_visible() and view.currentIndex().row() == row:
            theme.paint_focus_ring(painter, self.preview_rect(rect), theme.R_ROW)

        painter.drawPixmap(x, y + g.preview + theme.GALLERY_TEXT_GAP,
                           self.words(wallpaper, mark.dimmed, dpr))
        if dimmed:
            painter.setOpacity(1.0)

    def _paint_extras(self, painter: QPainter, rect: QRect, busy: str | None, hovered: bool,
                      selected: bool, mark: Mark, dpr: float) -> None:
        """What the pointer, a selection or a subscription under way add: a
        few cards at a time, so drawn as they come."""
        preview = self.preview_rect(rect)
        x, y = preview.x(), preview.y()
        ring = theme.FOCUS_RING
        if busy == SUBSCRIBING:
            painter.drawPixmap(QPointF(x, y), self.shade("subscribing", dpr))
            paint_spinner(painter, self.spinner_rect(rect), animations.loop("spin").value())
        elif busy == WAITING:
            painter.drawPixmap(QPointF(x, y), self.shade("waiting", dpr))
        elif hovered and mark.offer:
            hint = "page" if self.view is not None and self.view.model_.opens_page else "hint"
            painter.drawPixmap(QPointF(x, y), self.shade(hint, dpr))
        dimmed = mark.dimmed and not selected
        if dimmed:
            painter.setOpacity(1.0)
        offer = hovered and mark.offer and busy is None
        if offer or selected:
            painter.save()
            painter.setBrush(Qt.NoBrush)
            if offer and not selected:
                painter.setPen(QPen(theme.color("gallery.halo"), ring))
                painter.drawRoundedRect(preview.adjusted(-ring / 2, -ring / 2, ring / 2, ring / 2),
                                        theme.R_ROW + ring / 2, theme.R_ROW + ring / 2)
                painter.setPen(QPen(theme.color("accent.hover"), 1))
                painter.drawRoundedRect(preview.adjusted(0.5, 0.5, -0.5, -0.5),
                                        theme.R_ROW - 0.5, theme.R_ROW - 0.5)
            if selected:
                edge = theme.GALLERY_SELECT_EDGE
                painter.setPen(QPen(theme.color("accent"), edge))
                painter.drawRoundedRect(preview.adjusted(edge / 2, edge / 2, -edge / 2, -edge / 2),
                                        theme.R_ROW - edge / 2, theme.R_ROW - edge / 2)
            painter.restore()
        if selected:
            painter.drawPixmap(self.check_rect(rect).topLeft(),
                               disc_pixmap("check", "accent", theme.GALLERY_CHECK, dpr))
        elif offer:
            painter.drawPixmap(self.check_rect(rect).topLeft(), self.empty_check(dpr))
        if dimmed:
            painter.setOpacity(theme.GALLERY_DIM)


# ---- The grid ---------------------------------------------------------------------

class GalleryView(QListView):
    """The wall itself: virtualised, lazily illustrated, animated within a
    budget; paged; with the selection both views share."""

    subscribe_requested = Signal(str)
    open_requested = Signal(str)
    page_changed = Signal(int, int, int)      # page, pages, total
    selection_changed = Signal(int)           # how many are selected

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
        self.setSpacing(theme.GALLERY_GAP - 2 * MARGIN)
        self.setViewportMargins(MARGIN, MARGIN, MARGIN, 0)
        self.setMouseTracking(True)
        self.setSelectionMode(QAbstractItemView.NoSelection)
        self.setDragEnabled(False)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(theme.GALLERY_GAP * 3)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameShape(QListView.NoFrame)
        self.setAccessibleName("Wallpapers")
        self.model_.selection_changed.connect(self.selection_changed)
        self.model_.busy_changed.connect(self._busy_changed)
        self.model_.image_dropped.connect(self._image_dropped)

        self.loader = ThumbLoader(self)
        self.loader.done.connect(self._image_arrived)

        # One decoder per animated wallpaper on screen. A page holds thirty, so
        # this is bounded by the pagination rather than by the author's output.
        self._players: dict[str, tuple[QMovie, QBuffer]] = {}
        self._all: list = []
        self._page = 1
        self._paged = True
        self._hover = -1
        self._keyboard = False
        self._laid_for = -1
        self.dirty = None                       # the region being repainted, while it is
        self.paint_dpr = 1.0
        self.fresh_pass = False

        # Previews are only asked for once the grid stops moving, so a flick
        # through a page does not queue a download for every card twice.
        self._pending = QTimer(self)
        self._pending.setInterval(theme.GALLERY_SETTLE_MS)
        self._pending.setSingleShot(True)
        self._pending.timeout.connect(self._settled)
        self.verticalScrollBar().valueChanged.connect(self._scrolled)

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
        self._players_due.setInterval(theme.GALLERY_PLAYERS_MS)
        self._players_due.setSingleShot(True)
        self._players_due.timeout.connect(self._sync_players)

        # The card being subscribed to turns a spinner on the shared clock,
        # which repaints that spinner only.
        self._ticker = animations.LoopTicker(self.viewport(), self._spin_tick)
        self._lay_out()

    # -- contents ---------------------------------------------------------

    def close_loader(self) -> None:
        """Called when the window is going away, before Qt deletes the signal."""
        self._stop_all()
        self._ticker.stop()
        self.loader.stop()

    def show_items(self, items: Iterable, *, paged: bool = True) -> None:
        """Take the whole gallery, and show the first page of it — or all of it
        at once (`paged=False`: the list of every author's wallpapers)."""
        self._all = list(items)
        self._paged = paged
        self.model_.clear_selection()
        self.set_page(1)

    # -- pages ------------------------------------------------------------

    @property
    def page_size(self) -> int:
        return PAGE_SIZE if self._paged else max(1, len(self._all))

    @property
    def pages(self) -> int:
        size = self.page_size
        return max(1, (len(self._all) + size - 1) // size)

    @property
    def page(self) -> int:
        return self._page

    @property
    def total(self) -> int:
        return len(self._all)

    @property
    def paged(self) -> bool:
        return self._paged

    def set_page(self, page: int) -> None:
        self._page = max(1, min(page, self.pages))
        start = (self._page - 1) * self.page_size
        self._stop_all()
        self.loader.retarget()
        self.model_.set_items(self._all[start:start + self.page_size])
        self._hover = -1
        self.scrollToTop()
        self._lay_out()
        self._settled()
        self._busy_changed("")
        self.page_changed.emit(self._page, self.pages, len(self._all))

    def next_page(self) -> None:
        self.set_page(self._page + 1)

    def previous_page(self) -> None:
        self.set_page(self._page - 1)

    def mark_busy(self, item_id: str, state: str | bool | None = SUBSCRIBING) -> None:
        """Show a wallpaper as being worked on: WAITING in the queue,
        SUBSCRIBING now (True is SUBSCRIBING), or None/False for done."""
        if state is True:
            state = SUBSCRIBING
        self.model_.set_busy(item_id, state or None)

    def busy(self, item_id: str) -> str | None:
        return self.model_.busy.get(item_id)

    def refresh(self, item_id: str) -> None:
        self.model_.refresh_row(item_id)

    def refresh_page(self) -> None:
        self.model_.refresh_all()

    def current_page(self) -> list:
        """The wallpapers on the page that is on screen, in grid order."""
        return self.model_.items()

    def showing(self) -> list:
        """Every wallpaper in this gallery, not only the page on screen.

        Marking one as subscribed has to work whichever page it is on: the
        watch that notices a subscription made elsewhere runs while any page
        is open.
        """
        return list(self._all)

    def find(self, item_id: str):
        return next((w for w in self._all if w is not None and w.id == item_id), None)

    # -- the selection ----------------------------------------------------

    def selected_ids(self) -> list[str]:
        """What is selected, on any page, in the order it was chosen."""
        return list(self.model_.selected)

    def selectable(self, wallpaper) -> bool:
        return wallpaper is not None and offered(wallpaper, self.model_.busy.get(wallpaper.id))

    def toggle(self, item_id: str) -> None:
        wallpaper = self.find(item_id)
        if wallpaper is not None and (self.model_.is_selected(item_id)
                                      or self.selectable(wallpaper)):
            self.model_.toggle(item_id)

    def select_range(self, item_id: str) -> None:
        """Select from the last card chosen to this one, in the gallery's order
        (across pages), adding to what is selected."""
        order = [w.id for w in self._all if w is not None]
        anchor = self.model_.anchor if self.model_.anchor in order else None
        if anchor is None or item_id not in order:
            self.toggle(item_id)
            return
        a, b = sorted((order.index(anchor), order.index(item_id)))
        self.model_.select(w.id for w in self._all[a:b + 1] if self.selectable(w))
        self.model_.anchor = item_id

    def select_page(self) -> None:
        """Select every card on this page a click could subscribe to."""
        self.model_.select(w.id for w in self.current_page() if self.selectable(w))

    def clear_selection(self) -> bool:
        return self.model_.clear_selection()

    def click(self, item_id: str, modifiers=Qt.NoModifier, *, on_check: bool = False) -> None:
        """What a left click on a card does: Ctrl or the check toggle it, Shift
        selects up to it, and a plain click subscribes, as the card says."""
        wallpaper = self.find(item_id)
        if wallpaper is None:
            return
        if modifiers & Qt.ShiftModifier:
            self.select_range(item_id)
        elif on_check or modifiers & Qt.ControlModifier:
            self.toggle(item_id)
        elif self.selectable(wallpaper):
            self.subscribe_requested.emit(item_id)

    # -- geometry ---------------------------------------------------------

    def grid_width(self) -> int:
        """The width the cards are laid out in: the view's own, the scroll bar
        always allowed for, so a page that grows one does not change the grid."""
        bar = self.verticalScrollBar().sizeHint().width()
        return max(1, self.width() - 2 * self.frameWidth() - bar)

    def card(self) -> CardGeometry:
        return self.delegate.geometry

    def _lay_out(self) -> None:
        width = self.grid_width()
        if width == self._laid_for:
            return
        self._laid_for = width
        if self.delegate.set_geometry(card_geometry(width)):
            self._rescale_players()
            # uniform sizes are cached by the view: re-reading them takes a new layout
            self.setUniformItemSizes(False)
            self.setUniformItemSizes(True)
            self.scheduleDelayedItemsLayout()

    def resizeEvent(self, event) -> None:       # noqa: N802 - Qt's name
        self._lay_out()
        super().resizeEvent(event)
        self._pending.start()

    def visible_rows(self, margin: int = 0) -> range:
        """The rows of cards any part of which is on screen — and `margin`
        lines of cards either side — worked out from the grid's own geometry."""
        count = self.model_.rowCount()
        if count == 0:
            return range(0)
        g = self.card()
        pitch = g.height + theme.GALLERY_GAP
        top = self.verticalScrollBar().value()
        height = max(1, self.viewport().height())
        first = max(0, (top - theme.GALLERY_GAP) // pitch - margin)
        last = (top + height) // pitch + margin
        return range(min(count, first * g.columns), min(count, (last + 1) * g.columns))

    def item_rect(self, row: int) -> QRect:
        return self.visualRect(self.model_.index(row, 0))

    # -- previews ---------------------------------------------------------

    def _scrolled(self) -> None:
        self._pending.start()
        if self.underMouse():
            self._set_hover(self.indexAt(self.viewport().mapFromGlobal(QCursor.pos())).row())

    def _settled(self) -> None:
        """After the grid stops moving: fetch what is on screen, animate it."""
        window = self.window()
        if window is not self and not self.isVisibleTo(window):
            return              # the list is shown instead
        for row in self.visible_rows(margin=1):
            wallpaper = self.model_.item_at(row)
            if wallpaper is not None:
                self.loader.request(wallpaper.id, wallpaper.item.preview)
        self._sync_players()

    def _image_arrived(self, item_id: str, data: QByteArray, frame: QImage) -> None:
        """One preview has landed. Its player is worked out with the burst's.

        An arrival used to sweep the whole page and start a player on the spot:
        nine hundred model lookups to learn thirty things, and thirty decoders
        built on the GUI thread in whatever burst the six download threads
        delivered in. Now an arrival only says that the players are worth
        reconciling, and a clock does it once.
        """
        self.delegate.forget(item_id)
        self.model_.set_image(item_id, data, frame)
        if not self._players_due.isActive():
            self._players_due.start()

    def _image_dropped(self, item_id: str) -> None:
        self._stop_one(item_id)
        self.delegate.forget(item_id)
        self.loader.forget(item_id)

    # -- animation --------------------------------------------------------

    def _on_screen_in_order(self) -> list:
        """The ids of the cards a viewer can see, topmost first."""
        found = []
        for row in self.visible_rows():
            wallpaper = self.model_.item_at(row)
            if wallpaper is not None:
                found.append(wallpaper.id)
        return found

    def _on_screen(self) -> set:
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
        thread spends all its time on frames and the window stops answering.
        The ones nearest the top of the view win, because that is where the
        eye is, and the rest keep their still frame.
        """
        animated = [i for i in self._on_screen_in_order() if self._is_animated(i)]
        wanted = animated[:MAX_PLAYERS]
        for item_id in list(self._players):
            if item_id not in wanted:
                self._stop_one(item_id)
        for item_id in wanted:
            if item_id not in self._players:
                self._start_one(item_id)

    def _player_size(self, still: QPixmap) -> QSize:
        """The size Qt scales a player's frames to: just covering the card's
        preview, at the screen's scale."""
        g = self.card()
        dpr = self.devicePixelRatioF()
        return cover_size(still.size(), g.width * dpr, g.preview * dpr)

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
        # Qt scales each frame itself, to just cover the card, so no Python
        # runs per frame: a scale and a pixmap per frame, eight players at 25
        # frames a second, was a wait for the GIL whenever a download or a
        # Steam answer was being worked on.
        movie.setScaledSize(self._player_size(still))
        self._players[item_id] = (movie, buffer)
        self.delegate.movies[item_id] = movie
        movie.start()
        if self.resting:
            movie.setPaused(True)
        if not self._clock.isActive():
            self._last_tick = time.monotonic()
            self._clock.start()

    def _rescale_players(self) -> None:
        for item_id, (movie, _buffer) in self._players.items():
            still = self.model_.image(item_id)
            if still is not None:
                movie.setScaledSize(self._player_size(still))

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
                viewport.update(self.delegate.preview_rect(self.item_rect(row)).toAlignedRect())

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

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        self.dirty = event.region()
        self.paint_dpr = self.devicePixelRatioF()
        self.fresh_pass = True
        try:
            super().paintEvent(event)
        finally:
            self.dirty = None

    def hideEvent(self, event) -> None:         # noqa: N802 - Qt's name
        super().hideEvent(event)
        self._stop_all()

    def showEvent(self, event) -> None:         # noqa: N802 - Qt's name
        super().showEvent(event)
        self._lay_out()
        self._pending.start()

    # -- the spinner of the card being subscribed to ------------------------

    def _busy_changed(self, _item_id: str) -> None:
        turning = any(state == SUBSCRIBING and self.model_.row_of(i) is not None
                      for i, state in self.model_.busy.items())
        if turning and animations.ENABLED:
            self._ticker.start("spin")
        else:
            self._ticker.stop()

    def _spin_tick(self) -> None:
        viewport = self.viewport()
        for item_id, state in self.model_.busy.items():
            row = self.model_.row_of(item_id) if state == SUBSCRIBING else None
            if row is not None:
                box = self.delegate.spinner_rect(self.item_rect(row))
                viewport.update(box.adjusted(-1, -1, 1, 1).toAlignedRect())

    # -- hover, focus -------------------------------------------------------

    def hovered_row(self) -> int:
        return self._hover

    def _set_hover(self, row: int) -> None:
        wallpaper = self.model_.item_at(row)
        if wallpaper is None:
            row = -1
        if row == self._hover:
            return
        old, self._hover = self._hover, row
        viewport = self.viewport()
        for spot in (old, row):
            if spot >= 0:
                viewport.update(self.item_rect(spot))
        offer = wallpaper is not None and self.selectable(wallpaper)
        viewport.setCursor(Qt.PointingHandCursor if offer else Qt.ArrowCursor)

    def force_hover(self, row: int) -> None:
        """Draw a card as under the pointer (a fixture's picture)."""
        self._set_hover(row)

    def focus_visible(self) -> bool:
        return self.hasFocus() and self._keyboard

    def viewportEvent(self, event) -> bool:     # noqa: N802 - Qt's name
        kind = event.type()
        if kind in (QEvent.HoverEnter, QEvent.HoverMove, QEvent.HoverLeave):
            return False        # Qt's own hover repaints whole items; the view tracks a card
        if kind == QEvent.Leave:
            self._set_hover(-1)
        return super().viewportEvent(event)

    def mouseMoveEvent(self, event) -> None:    # noqa: N802 - Qt's name
        self._set_hover(self.indexAt(event.position().toPoint()).row())

    def mousePressEvent(self, event) -> None:   # noqa: N802 - Qt's name
        self._keyboard = False
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:     # noqa: N802 - Qt's name
        point = event.position().toPoint()
        index = self.indexAt(point)
        wallpaper = index.data(WALLPAPER) if index.isValid() else None
        if wallpaper is not None:
            if event.button() == Qt.RightButton:
                self.open_requested.emit(wallpaper.id)
            elif event.button() == Qt.LeftButton:
                check = self.delegate.check_rect(self.visualRect(index))
                hit = check.adjusted(*(d * (theme.GALLERY_CHECK_HIT - theme.GALLERY_CHECK) / 2
                                       for d in (-1, -1, 1, 1)))
                self.click(wallpaper.id, event.modifiers(),
                           on_check=hit.contains(QPointF(point)))
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:     # noqa: N802 - Qt's name
        key = event.key()
        current = self.model_.item_at(self.currentIndex().row())
        if key == Qt.Key_Escape and self.clear_selection():
            event.accept()
            return
        if key == Qt.Key_Space and current is not None:
            self.toggle(current.id)
            event.accept()
            return
        if key in (Qt.Key_Return, Qt.Key_Enter) and current is not None:
            self.click(current.id, event.modifiers())
            event.accept()
            return
        if key == Qt.Key_A and event.modifiers() & Qt.ControlModifier:
            self.select_page()
            event.accept()
            return
        self._keyboard = True
        previous = self.currentIndex().row()
        super().keyPressEvent(event)
        for row in (previous, self.currentIndex().row()):
            if row >= 0:
                self.viewport().update(self.item_rect(row))

    def focusInEvent(self, event) -> None:      # noqa: N802 - Qt's name
        if event.reason() in (Qt.TabFocusReason, Qt.BacktabFocusReason, Qt.ShortcutFocusReason):
            self._keyboard = True
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:     # noqa: N802 - Qt's name
        super().focusOutEvent(event)
        row = self.currentIndex().row()
        if row >= 0:
            self.viewport().update(self.item_rect(row))


# ---- The list -------------------------------------------------------------------

LIST_COLUMNS = (
    Column("", theme.THUMB["row"][0], thumb="row", sortable=False),
    Column("Wallpaper", None, font="type.body", tone="text.hi"),
    Column("Type", theme.GALLERY_LIST_COLUMNS["type"], mono=True, tone="text.mid"),
    Column("Size", theme.GALLERY_LIST_COLUMNS["size"], "right", mono=True, tone="text.mid"),
    Column("Mark", theme.GALLERY_LIST_COLUMNS["mark"]),
    Column("", theme.GALLERY_LIST_COLUMNS["action"], "right", sortable=False),
)
MARK_ORDER = (NEW, YOURS, QUEUED, UNKNOWN, HAVE, SUBSCRIBED)


def action_cell(wallpaper, busy: str | None, opens_page: bool = False):
    """The list's last column: Subscribe (Open in Steam, when that is what a
    click does), the work under way, a dash for one you already have, the
    check for one subscribed."""
    if busy == SUBSCRIBING:
        return BusyCell("Subscribing…")
    if busy == WAITING:
        return Cell("Waiting…", tone="text.mid", icon="clock")
    if wallpaper.subscribed:
        return DiscCell("check", "info", "Subscribed")
    if wallpaper.in_library:
        return Cell(fmt.DASH, tone="text.lo")
    return ButtonCell("Open in Steam" if opens_page else "Subscribe")


class GalleryListModel(TableModel):
    """The list view's rows: the gallery's page (or, for the review as a list,
    every author's wallpapers under their author)."""

    def __init__(self, gallery: GalleryModel, parent=None):
        super().__init__(LIST_COLUMNS, (), parent)
        self.gallery = gallery

    def cell(self, wallpaper, column: int):
        if column == 1:
            return Cell(wallpaper.title or wallpaper.id, sub=card_line(wallpaper))
        if column == 2:
            return (wallpaper.item.kind or "").lower() or None
        if column == 3:
            if not wallpaper.item.file_size:
                return None
            return Cell(fmt.size(wallpaper.item.file_size),
                        tone="warn" if wallpaper.item.large else None)
        if column == 4:
            mark = card_mark(wallpaper)
            return ChipCell(mark.chip, mark.text) if mark.chip else None
        if column == 5:
            return action_cell(wallpaper, self.gallery.busy.get(wallpaper.id),
                               self.gallery.opens_page)
        return None

    def sort_key(self, wallpaper, column: int):
        if column == 3:
            return wallpaper.item.file_size or 0
        if column == 4:
            return MARK_ORDER.index(card_mark(wallpaper))
        return super().sort_key(wallpaper, column)

    def thumb_source(self, wallpaper) -> str | None:
        return wallpaper.id

    def row_dimmed(self, wallpaper) -> bool:
        return wallpaper.in_library and not wallpaper.subscribed

    def row_tone(self, wallpaper) -> str | None:
        return "accent" if wallpaper.id in self.gallery.selected else None


class GalleryList(Table):
    """The gallery as a table: the same page, the same marks, the same
    selection. A row's Subscribe button turns Accent under the pointer; a
    click elsewhere on a row selects it (Shift: up to it); the right button
    opens the wallpaper's page in Steam."""

    subscribe_requested = Signal(str)
    open_requested = Signal(str)

    COVER_LIMIT = 4 * PAGE_SIZE

    def __init__(self, gallery: GalleryView, parent=None):
        super().__init__(parent, loader=gallery.loader)
        self.gallery = gallery
        self.model_ = GalleryListModel(gallery.model_, self)
        self.setModel(self.model_)
        self.setSelectionMode(QAbstractItemView.NoSelection)
        self.setAccessibleName("Wallpapers")
        self._covers: OrderedDict[str, QPixmap] = OrderedDict()
        self._index: dict[str, int] = {}
        self._groups: tuple | None = None
        self._group_of = None
        self.button_clicked.connect(self._button)
        source = gallery.model_
        source.modelReset.connect(self.refill)
        source.image_ready.connect(self.image_arrived)
        source.image_dropped.connect(self.image_arrived)
        source.dataChanged.connect(self._changed)
        source.busy_changed.connect(self._busy_changed)
        source.selection_changed.connect(lambda _n: self.viewport().update())
        self.refill()

    # -- rows

    def set_groups(self, groups, group_of) -> None:
        """Rows under group headers (every author's wallpapers under their
        name); None for the plain page."""
        self._groups = tuple(groups) if groups else None
        self._group_of = group_of
        self.refill()

    def refill(self) -> None:
        items = self.gallery.model_.items()
        self._index = {w.id: i for i, w in enumerate(items)}
        if self._groups:
            self.model_.set_rows(items, groups=self._groups, group_of=self._group_of)
        else:
            self.model_.set_rows(items, groups=(), group_of=None)
        self._busy_changed("")

    def row_of(self, item_id: str) -> int:
        index = self._index.get(item_id)
        return -1 if index is None else self.model_.row_of_item(index)

    def _changed(self, top, bottom, _roles=()) -> None:
        for row in range(top.row(), bottom.row() + 1):
            wallpaper = self.gallery.model_.item_at(row)
            if wallpaper is not None:
                self._update_row(self.row_of(wallpaper.id))

    def _busy_changed(self, _item_id: str) -> None:
        busy = self.gallery.model_.busy
        self.set_spinning(self._index[i] for i, state in busy.items()
                          if state == SUBSCRIBING and i in self._index and animations.ENABLED)
        self.viewport().update()

    # -- previews: the gallery's own, cropped to the row's thumb

    def thumb_state(self, source) -> tuple[str, QPixmap | None]:
        if not source:
            return "placeholder", None
        cover = self._covers.get(source)
        if cover is not None:
            return "image", cover
        image = self.gallery.model_.image(source)
        if image is not None:
            width, height, _ = theme.THUMB["row"]
            cover = cover_pixmap(image, width, height, self.devicePixelRatioF())
            self._covers[source] = cover
            while len(self._covers) > self.COVER_LIMIT:
                self._covers.popitem(last=False)
            return "image", cover
        wallpaper = self.gallery.model_.find(source)
        if wallpaper is None or not wallpaper.item.preview or self.gallery.model_.missing(source):
            return "placeholder", None
        return "loading", None

    def request_visible_thumbs(self) -> None:
        # The loader is the grid's too. A new author resets the model, which
        # settles this list 90 ms later even while the grid is the one shown:
        # its retarget dropped every download the grid had just queued, and
        # the cards not already downloading stayed empty until something made
        # the grid ask again. Hidden, the list asks for nothing; shown, it does.
        window = self.window()
        if window is not self and not self.isVisibleTo(window):
            return
        # What was queued for rows scrolled past is dropped (the list of every
        # author can be a thousand rows); what arrived is in the gallery's model.
        self.gallery.loader.retarget()
        for row in self.visible_rows():
            wallpaper = self.model_.item_at(row)
            if wallpaper is not None and self.gallery.model_.image(wallpaper.id) is None:
                self.gallery.loader.request(wallpaper.id, wallpaper.item.preview)

    def showEvent(self, event) -> None:         # noqa: N802 - Qt's name
        super().showEvent(event)
        self._settle.start()            # what it skipped while hidden

    def image_arrived(self, item_id: str) -> None:
        self._covers.pop(item_id, None)
        for key in [k for k in self._tiles if k[0] == item_id]:
            del self._tiles[key]
        self._update_row(self.row_of(item_id))

    # -- acting

    def _button(self, row: int, _column: int) -> None:
        wallpaper = self.model_.item_at(row)
        if wallpaper is not None and self.gallery.selectable(wallpaper):
            self.subscribe_requested.emit(wallpaper.id)

    def mouseReleaseEvent(self, event) -> None:     # noqa: N802 - Qt's name
        if self._pressed_button is None:
            wallpaper = self.model_.item_at(self.rowAt(round(event.position().y())))
            if wallpaper is not None:
                if event.button() == Qt.RightButton:
                    self.open_requested.emit(wallpaper.id)
                elif event.button() == Qt.LeftButton:
                    if event.modifiers() & Qt.ShiftModifier:
                        self.gallery.select_range(wallpaper.id)
                    else:
                        self.gallery.toggle(wallpaper.id)
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:     # noqa: N802 - Qt's name
        key = event.key()
        current = self.model_.item_at(self.currentIndex().row())
        if key == Qt.Key_Escape and self.gallery.clear_selection():
            event.accept()
            return
        if key == Qt.Key_Space and current is not None:
            self.gallery.toggle(current.id)
            event.accept()
            return
        if key in (Qt.Key_Return, Qt.Key_Enter) and current is not None:
            if self.gallery.selectable(current):
                self.subscribe_requested.emit(current.id)
            event.accept()
            return
        if key == Qt.Key_A and event.modifiers() & Qt.ControlModifier:
            self.gallery.select_page()
            event.accept()
            return
        super().keyPressEvent(event)


__all__ = [
    "GalleryModel", "GalleryDelegate", "GalleryView", "GalleryList", "GalleryListModel",
    "Mark", "card_mark", "card_line", "card_meta", "columns_for", "card_geometry",
    "cover_source", "cover_size", "cover_pixmap", "action_cell", "offered",
    "PAGE_SIZE", "MAX_PLAYERS", "FRAME_MS", "WAITING", "SUBSCRIBING",
]
