"""Review tab — the week's new authors, and what they have published since.

Left: one card per author behind the wallpapers put aside in Wallpaper
Engine's folder — their name, whether the authors database has heard of them,
and how many of their wallpapers are new since the last visit. Right: that
author's wallpapers, the ones not subscribed to, as a wall of previews.

**Why it loads in two steps.** Naming 453 authors takes four seconds. Working
out what each has published since costs a request apiece, and opening one
author's whole back catalogue costs a dozen. So the list appears first and
fills in behind itself, and a gallery is fetched when its card is clicked. The
engine does the arithmetic (``engines/review.py``); this is its face.

**Two ways to subscribe, because one of them is unsupported.** Wallpaper
Engine's own Subscribe button calls `ISteamUGC::SubscribeItem`, and so can this
tab — a click and the wallpaper is on its way, without leaving the window. That
path declares Wallpaper Engine's app id to Steam, which Valve does not sanction
and a Steam update could close, so the other one stays: open the wallpaper in
the Steam client and press Subscribe there. The choice is a switch, both work,
and the tab says which is in use.
"""
from __future__ import annotations

import atexit
import queue
import subprocess
import webbrowser
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import (
    QModelIndex, QRect, QSize, Qt, QThread, QTimer, Signal)
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QLabel, QLineEdit, QListView,
    QMessageBox, QPushButton, QSplitter, QStyle, QStyledItemDelegate,
    QToolButton, QVBoxLayout, QWidget)

from .. import animations, theme
from ..engines import review as rv
from ..engines.authors_store import AuthorsStore, StoreDamaged
from ..engines.library import Library
from ..engines.review import AuthorCard, Review, ReviewResult
from ..engines.steam_api import SteamAuthError, SteamClient, workshop_url
from ..engines.steam_ugc import SteamUgc, UgcError
from ..settings import Settings
from .. import secrets
from .authors_dialog import AuthorsDialog, mirror_folder, open_store
from .credentials import WITHOUT_KEY, CredentialsDialog, has_key
from .gallery import GalleryView

SECTION = "review"
KEYLESS_HIDDEN = "keyless_banner_hidden"

# How a wallpaper gets subscribed to.
BY_STEAM = "steam"        # ISteamUGC, from this window
BY_PAGE = "page"          # open Steam's own page and let the user press it

CARD = Qt.UserRole + 1

STATE_LABEL = {rv.NEW: "new author", rv.KNOWN: "known", rv.DUPLICATE: "duplicated",
               rv.UNKNOWN: "unidentified"}

SORTS = [("Known first, then new", rv.SORT_DEFAULT),
         ("Name", rv.SORT_NAME),
         ("Added to the folder", rv.SORT_APPEARED)]


def _short_date(when) -> str:
    """A date that fits a list row: the year only when it is not this one."""
    if when is None:
        return ""
    return when.strftime("%m-%d" if when.year == datetime.now().year else "%Y-%m-%d")


# ---- Work that must not happen on the GUI thread ---------------------------

class Task(QThread):
    """Any slow call, with its result or its failure delivered as a signal."""

    done = Signal(object)
    failed = Signal(str)
    step = Signal(str, int, int)

    def __init__(self, work, parent=None):
        super().__init__(parent)
        self._work = work

    def run(self) -> None:
        try:
            self.done.emit(self._work(self.step.emit))
        except Exception as err:  # noqa: BLE001 — a thread must report, not die
            self.failed.emit(str(err))


class SubscribeQueue(QThread):
    """Every subscription goes through one thread and one Steam connection.

    Each click used to start a thread of its own, and each of those opened the
    Steamworks API and shut it down again. `SteamAPI_Shutdown` is process-wide,
    so two clicks close together could have one thread close the connection
    the other was still subscribing through. One queue makes the calls
    sequential; it also means "subscribe to all" opens Steam once for thirty
    wallpapers rather than thirty times, and closes it again as soon as the
    queue has been idle for a moment — so Steam does not show Wallpaper Engine
    as running for longer than the work takes.
    """

    started_item = Signal(str)
    finished_item = Signal(str, str)      # id, what Steam now says about it
    failed_item = Signal(str, str)        # id, why not
    progress = Signal(int, int)           # done, asked for in this run

    IDLE_SECONDS = 1.5

    def __init__(self, parent=None):
        super().__init__(parent)
        self._queue: queue.Queue[str] = queue.Queue()
        self._asked = 0
        self._done = 0
        # Anything added in the instant the thread decided to stop is picked
        # up by starting it again.
        self.finished.connect(self._restart_if_needed)

    def add(self, item_ids) -> None:
        for item_id in item_ids:
            self._queue.put(str(item_id))
            self._asked += 1
        if not self.isRunning():
            self.start()

    def pending(self) -> int:
        return self._queue.qsize()

    def _restart_if_needed(self) -> None:
        if not self._queue.empty():
            self.start()
        else:
            self._asked = self._done = 0

    def run(self) -> None:
        ugc = None
        try:
            while True:
                try:
                    item_id = self._queue.get(timeout=self.IDLE_SECONDS)
                except queue.Empty:
                    return
                self.started_item.emit(item_id)
                try:
                    if ugc is None:
                        ugc = SteamUgc().connect()
                    state = ugc.subscribe(item_id, wait=5)
                    self.finished_item.emit(item_id, state.describe())
                except Exception as err:  # noqa: BLE001 — report it, keep going
                    self.failed_item.emit(item_id, str(err))
                    if isinstance(err, UgcError) and ugc is None:
                        # Steam is not there at all; the rest would fail the
                        # same way, one slow timeout each.
                        while True:
                            try:
                                rest = self._queue.get_nowait()
                            except queue.Empty:
                                break
                            self.failed_item.emit(rest, str(err))
                            self._done += 1
                self._done += 1
                self.progress.emit(self._done, self._asked)
        finally:
            if ugc is not None:
                ugc.close()


# ---- The author list -------------------------------------------------------

class AuthorDelegate(QStyledItemDelegate):
    """One row: who they are, whether we know them, and how much is new."""

    def sizeHint(self, option, index) -> QSize:
        return QSize(280, 54)

    def paint(self, painter: QPainter, option, index) -> None:
        card: AuthorCard = index.data(CARD)
        if card is None:
            return
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRect(option.rect).adjusted(4, 2, -4, -2)
        selected = bool(option.state & QStyle.State_Selected)
        hovered = bool(option.state & QStyle.State_MouseOver)

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme.C["raised"] if selected or hovered
                                else theme.C["surface"]))
        painter.drawRoundedRect(rect, 5, 5)

        name = QRect(rect.left() + 10, rect.top() + 6, rect.width() - 90, 18)
        font = QFont(painter.font())
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(theme.C["text"]))
        shown = painter.fontMetrics().elidedText(card.name, Qt.ElideRight, name.width())
        painter.drawText(name, Qt.AlignLeft | Qt.AlignVCenter, shown)

        # The name Steam uses now is the one on the row. While the database
        # still holds an older one, it sits beside it in grey — that is the
        # name the author was filed under, and the one a memory reaches for.
        if card.database_name:
            used = painter.fontMetrics().horizontalAdvance(shown)
            font.setBold(False)
            painter.setFont(font)
            painter.setPen(QColor(theme.C["faint"]))
            old = QRect(name.left() + used + 8, name.top(),
                        max(0, name.width() - used - 8), name.height())
            painter.drawText(old, Qt.AlignLeft | Qt.AlignVCenter,
                             painter.fontMetrics().elidedText(
                                 f"was {card.database_name}", Qt.ElideRight, old.width()))

        font.setBold(False)
        font.setPointSize(8)
        painter.setFont(font)
        colour = {rv.NEW: theme.C["ok"], rv.KNOWN: theme.C["muted"],
                  rv.DUPLICATE: theme.C["warn"],
                  rv.UNKNOWN: theme.C["danger"]}[card.state]
        chip_text = STATE_LABEL[card.state]
        width = painter.fontMetrics().horizontalAdvance(chip_text) + 12
        chip = QRect(rect.left() + 10, rect.bottom() - 22, width, 16)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(colour))
        painter.drawRoundedRect(chip, 3, 3)
        painter.setPen(QColor("#101216"))
        painter.drawText(chip, Qt.AlignCenter, chip_text)

        painter.setPen(QColor(theme.C["faint"]))
        detail = f"{len(card.queued)} queued"
        if card.appeared:
            detail += f"  ·  added {_short_date(card.appeared)}"
        if card.visited:
            detail += f"  ·  visited {_short_date(card.visited)}"
        detail_rect = QRect(chip.right() + 8, chip.top(),
                            rect.right() - chip.right() - 62, 16)
        painter.drawText(detail_rect, Qt.AlignLeft | Qt.AlignVCenter,
                         painter.fontMetrics().elidedText(
                             detail, Qt.ElideRight, detail_rect.width()))

        # The badge, right-aligned: the number the whole tab exists to produce.
        if card.filled:
            text = str(card.badge)
            badge_colour = theme.C["accent"] if card.badge else theme.C["raised"]
            badge = QRect(rect.right() - 54, rect.top() + 14, 44, 24)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(badge_colour))
            painter.drawRoundedRect(badge, 4, 4)
            painter.setPen(QColor("#101216" if card.badge else theme.C["faint"]))
            font.setPointSize(10)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(badge, Qt.AlignCenter, text)
        else:
            painter.setPen(QColor(theme.C["faint"]))
            painter.drawText(QRect(rect.right() - 54, rect.top() + 14, 44, 24),
                             Qt.AlignCenter, "…")
        painter.restore()


class AuthorList(QListView):
    """The first list. 453 rows, so it is a view with a delegate, not widgets."""

    chosen = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        from PySide6.QtCore import QAbstractListModel

        class Model(QAbstractListModel):
            def __init__(self, outer):
                super().__init__(outer)
                self.cards: list[AuthorCard] = []

            def rowCount(self, parent=QModelIndex()):
                return 0 if parent.isValid() else len(self.cards)

            def data(self, index, role=Qt.DisplayRole):
                if not index.isValid():
                    return None
                card = self.cards[index.row()]
                if role == CARD:
                    return card
                if role in (Qt.DisplayRole, Qt.ToolTipRole):
                    return card.name
                return None

            def set_cards(self, cards):
                self.beginResetModel()
                self.cards = list(cards)
                self.endResetModel()

            def touch(self, card):
                if card in self.cards:
                    row = self.cards.index(card)
                    self.dataChanged.emit(self.index(row, 0), self.index(row, 0))

        self.model_ = Model(self)
        self.setModel(self.model_)
        self.setItemDelegate(AuthorDelegate(self))
        self.setUniformItemSizes(True)
        self.setMouseTracking(True)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setStyleSheet("QListView { border: none; background: transparent; }")

        # A click opens an author at once. Moving with the arrow keys opens
        # them too, but only once the selection has come to rest: holding the
        # key down through twenty authors should not start twenty fetches.
        self._restoring = False
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.setInterval(220)
        self._settle.timeout.connect(self._choose_current)
        self.clicked.connect(self._clicked)
        self.selectionModel().currentChanged.connect(self._moved)

    def _clicked(self, index) -> None:
        self._settle.stop()
        self.chosen.emit(index.data(CARD))

    def _moved(self, current, _previous) -> None:
        if self._restoring or not current.isValid():
            return
        self._settle.start()

    def _choose_current(self) -> None:
        index = self.currentIndex()
        if index.isValid():
            self.chosen.emit(index.data(CARD))

    def select(self, card) -> None:
        """Put the highlight back on a card without opening it again."""
        if card not in self.model_.cards:
            return
        self._restoring = True
        try:
            self.setCurrentIndex(self.model_.index(self.model_.cards.index(card), 0))
        finally:
            self._restoring = False

    def set_cards(self, cards) -> None:
        self.model_.set_cards(cards)

    def touch(self, card) -> None:
        self.model_.touch(card)


# ---- The tab ---------------------------------------------------------------

class ReviewTab(QWidget):
    # Progress from the engine, on its way to the status line. It belongs to
    # the tab because the tab is the thing that lasts: see `_progress_relay`.
    progressed = Signal(str, int, int)

    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.progressed.connect(self._step)
        self.settings = settings
        self.db: AuthorsStore | None = None
        self.steam: SteamClient | None = None
        self.library: Library | None = None
        self.review: Review | None = None
        self.result: ReviewResult | None = None
        self.current: AuthorCard | None = None
        self._task: Task | None = None
        self._tasks: set[Task] = set()          # author loads in flight
        self._loading: AuthorCard | None = None
        self._watching: Task | None = None      # a look at the workshop folder
        self._busy_count = 0
        self._owned_tasks: set[Task] = set()     # "was yours" passes in flight
        self._owned_pending: set[str] = set()    # the authors those are for
        self._status_text = ""
        self._status_kind = "muted"
        self._styled_kind = None                # the colour the label has now
        self._summary = ""                      # what the status returns to
        self._damaged = False                   # the last open found a bad file

        outer = QVBoxLayout(self)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("Look at:"))
        self.scope = QComboBox()
        self.scope.setMinimumWidth(210)
        self.scope.setToolTip(
            "Which of Wallpaper Engine's wallpapers to review. The folders are "
            "read from its own config.json, so a folder you made today is in "
            "this list today.")
        bar.addWidget(self.scope)
        self._load_scopes()
        self.scope.currentIndexChanged.connect(self._save_scope)

        self.scan_btn = QPushButton("Scan")
        theme.make_accent(self.scan_btn)
        self.scan_btn.clicked.connect(self.start_scan)
        bar.addWidget(self.scan_btn)

        self.fill_btn = QPushButton("Count what is new")
        self.fill_btn.setEnabled(False)
        self.fill_btn.clicked.connect(self.start_fill)
        bar.addWidget(self.fill_btn)

        bar.addSpacing(16)
        bar.addWidget(QLabel("Subscribe by:"))
        self.mode = QComboBox()
        self.mode.addItem("Steam directly (one click)", BY_STEAM)
        self.mode.addItem("Opening Steam's page", BY_PAGE)
        stored = self.settings.get(SECTION, "subscribe", BY_STEAM)
        self.mode.setCurrentIndex(0 if stored == BY_STEAM else 1)
        self.mode.currentIndexChanged.connect(self._save_mode)
        bar.addWidget(self.mode)

        bar.addStretch()
        self.key_btn = QPushButton("Steam key…")
        self.key_btn.setToolTip("The Steam Web API key — optional, but without it "
                                "author lists leave out mature wallpapers.")
        self.key_btn.clicked.connect(self.edit_credentials)
        bar.addWidget(self.key_btn)
        self.authors_btn = QPushButton("Authors database…")
        self.authors_btn.setToolTip("Where the authors are kept, the backups, "
                                    "and restoring one.")
        self.authors_btn.clicked.connect(self.edit_authors)
        bar.addWidget(self.authors_btn)
        self.update_btn = QPushButton("Update the database")
        self.update_btn.setEnabled(False)
        self.update_btn.clicked.connect(self.apply_review)
        bar.addWidget(self.update_btn)
        outer.addLayout(bar)

        # Going without a Steam key is allowed, and said once, up front, in
        # words — not discovered weeks later as authors who "never publish
        # anything". Hiding the banner keeps the warnings where they matter:
        # on each author counted without a key, and before anything is written.
        self.keyless = QWidget()
        self.keyless.setObjectName("keyless")
        self.keyless.setStyleSheet(
            f"#keyless {{ background: {theme.C['surface']}; "
            f"border: 1px solid {theme.C['warn']}; border-radius: 5px; }}")
        keyless_row = QHBoxLayout(self.keyless)
        keyless_row.setContentsMargins(10, 6, 8, 6)
        keyless_text = QLabel("⚠  No Steam Web API key.  " + WITHOUT_KEY)
        keyless_text.setWordWrap(True)
        keyless_text.setStyleSheet(theme.label_style("warn"))
        keyless_row.addWidget(keyless_text, 1)
        add_key = QPushButton("Add a key…")
        add_key.clicked.connect(self.edit_credentials)
        keyless_row.addWidget(add_key)
        hide_keyless = QPushButton("Hide")
        hide_keyless.setToolTip(
            "Stop showing this. Authors counted without a key still say so, and "
            "so does the confirmation before anything is written.")
        hide_keyless.clicked.connect(self._hide_keyless)
        keyless_row.addWidget(hide_keyless)
        outer.addWidget(self.keyless)
        self._show_keyless()

        # The bar and the status line keep their space whether or not they
        # have anything to show. Appearing and disappearing used to push the
        # whole tab down and pull it back up with every fetch.
        self.progress = animations.SmoothProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        keep = self.progress.sizePolicy()
        keep.setRetainSizeWhenHidden(True)
        self.progress.setSizePolicy(keep)
        self.progress.hide()
        outer.addWidget(self.progress)

        self.status = QLabel("")
        self.status.setWordWrap(False)
        self.status.setFixedHeight(self.status.fontMetrics().height() + 6)
        outer.addWidget(self.status)
        self._say("Choose what to look at, then press “Scan” to find out who "
                  "made what is in it.")

        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Find an author — part of a name, or an id")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._refresh_list)
        left_layout.addWidget(self.search)

        order = QHBoxLayout()
        order.addWidget(QLabel("Sort:"))
        self.sort = QComboBox()
        for label, key in SORTS:
            self.sort.addItem(label, key)
        stored_sort = self.settings.get(SECTION, "sort", rv.SORT_DEFAULT)
        self.sort.setCurrentIndex(max(0, [k for _, k in SORTS].index(stored_sort)
                                      if stored_sort in [k for _, k in SORTS] else 0))
        self.sort.currentIndexChanged.connect(self._sort_changed)
        order.addWidget(self.sort, 1)
        self.direction = QToolButton()
        self.direction.setCheckable(True)
        self.direction.setChecked(bool(self.settings.get(SECTION, "descending", False)))
        self.direction.toggled.connect(self._sort_changed)
        self._label_direction()
        order.addWidget(self.direction)
        left_layout.addLayout(order)

        self.authors = AuthorList()
        self.authors.chosen.connect(self.open_author)
        left_layout.addWidget(self.authors, 1)
        self.list_count = QLabel("")
        self.list_count.setStyleSheet(theme.label_style("faint"))
        left_layout.addWidget(self.list_count)
        split.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        self.heading = QLabel("")
        self.heading.setStyleSheet(theme.label_style("text", size=13, weight=600))
        head.addWidget(self.heading, 1)
        self.subscribe_all_btn = QPushButton("Subscribe to all on this page")
        self.subscribe_all_btn.clicked.connect(self.subscribe_page)
        self.subscribe_all_btn.setEnabled(False)
        head.addWidget(self.subscribe_all_btn)
        right_layout.addLayout(head)
        self.subheading = QLabel("")
        self.subheading.setStyleSheet(theme.label_style("faint"))
        self.subheading.setWordWrap(True)
        right_layout.addWidget(self.subheading)
        self.gallery = GalleryView()
        self.gallery.subscribe_requested.connect(self.subscribe)
        self.gallery.open_requested.connect(self.open_in_steam)
        self.gallery.page_changed.connect(self._page_changed)
        right_layout.addWidget(self.gallery, 1)

        # Paging is not only about scrolling: a page is thirty previews to
        # fetch instead of a thousand, and thirty decoders instead of however
        # many the author has published.
        pager = QHBoxLayout()
        self.prev_btn = QPushButton("‹  Previous")
        self.prev_btn.clicked.connect(self.gallery.previous_page)
        pager.addWidget(self.prev_btn)
        self.page_label = QLabel("")
        self.page_label.setStyleSheet(theme.label_style("muted"))
        self.page_label.setAlignment(Qt.AlignCenter)
        pager.addWidget(self.page_label, 1)
        self.next_btn = QPushButton("Next  ›")
        self.next_btn.clicked.connect(self.gallery.next_page)
        pager.addWidget(self.next_btn)
        right_layout.addLayout(pager)
        self._page_changed(1, 1, 0)
        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([440, 1100])
        outer.addWidget(split, 1)

        # Preview downloads must not outlive the process that started them.
        atexit.register(self.gallery.close_loader)

        # Subscribing outside this window — Steam's own page, or Wallpaper
        # Engine itself — is the other half of how wallpapers arrive, and the
        # gallery should notice either way. A directory listing of 1 300
        # entries every few seconds costs nothing.
        self._watch = QTimer(self)
        self._watch.setInterval(4000)
        self._watch.timeout.connect(self._notice_subscriptions)
        self._watch.start()

        self.subscriptions = SubscribeQueue(self)
        self.subscriptions.started_item.connect(
            lambda item_id: self.gallery.mark_busy(item_id, True))
        self.subscriptions.finished_item.connect(self._subscribed)
        self.subscriptions.failed_item.connect(self._subscribe_failed)
        self.subscriptions.progress.connect(self._subscribe_progress)

    # -- the status line ----------------------------------------------------

    def _say(self, text: str, kind: str = "muted") -> None:
        """Set the status line. Every message sets its colour too.

        Colour used to be set only when something failed, and nothing set it
        back — so after one failed subscription every later message, a
        successful subscription included, went on being printed in red from
        author to author.
        """
        self._status_text = text
        self._status_kind = kind
        # A style sheet is re-parsed on every set, and the count sets the
        # status once per author: only a change of colour is worth one.
        if kind != self._styled_kind:
            self._styled_kind = kind
            self.status.setStyleSheet(theme.label_style(kind))
        self._fit_status()

    def _fit_status(self) -> None:
        # One line, always: a message long enough to wrap would move the tab.
        metrics = self.status.fontMetrics()
        width = max(60, self.status.width())
        shown = metrics.elidedText(self._status_text, Qt.ElideRight, width)
        self.status.setText(shown)
        self.status.setToolTip(self._status_text if shown != self._status_text else "")

    def _settle_status(self) -> None:
        """Back to the summary of the scan, once a passing message is done."""
        self._say(self._summary)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_status()

    def _busy(self, on: bool) -> None:
        self._busy_count = max(0, self._busy_count + (1 if on else -1))
        if self._busy_count:
            self.progress.setRange(0, 0)
            self.progress.show()
        else:
            self.progress.hide()

    # -- the author list --------------------------------------------------

    def _label_direction(self) -> None:
        down = self.direction.isChecked()
        self.direction.setText("↓" if down else "↑")
        self.direction.setToolTip("Descending — click for ascending" if down
                                  else "Ascending — click for descending")

    def _sort_changed(self, *_args) -> None:
        self._label_direction()
        self.settings.set(SECTION, "sort", self.sort.currentData())
        self.settings.set(SECTION, "descending", self.direction.isChecked())
        self.settings.save()
        self._refresh_list()

    def _shown_cards(self) -> list[AuthorCard]:
        if self.result is None:
            return []
        query = self.search.text()
        found = [c for c in self.result.cards if rv.matches(c, query)]
        return rv.sort_cards(found, self.sort.currentData(), self.direction.isChecked())

    def _refresh_list(self, *_args) -> None:
        cards = self._shown_cards()
        self.authors.set_cards(cards)
        if self.current is not None:
            self.authors.select(self.current)
        total = len(self.result.cards) if self.result else 0
        if not total:
            self.list_count.setText("")
        elif len(cards) == total:
            self.list_count.setText(f"{total} authors")
        else:
            self.list_count.setText(f"{len(cards)} of {total} authors match")

    # -- what gets reviewed -------------------------------------------------

    def _load_scopes(self) -> None:
        """Fill the scope list from Wallpaper Engine's own folders.

        Read rather than remembered, when the tab is built and again after
        every scan: folders are made and emptied between sessions, and a list
        that has to be right is not worth caching 15 ms of parsing for. The
        three below the folders cross them — the last is the whole library,
        which is the only way to reach a wallpaper that is in no folder at all.
        """
        wanted = self.settings.get(SECTION, "scope", rv.DEFAULT_SCOPE)
        try:
            folders = rv.we_folders()
        except Exception:  # noqa: BLE001 — a tab with no list still scans
            folders = {}
        self.scope.blockSignals(True)
        self.scope.clear()
        for title, items in folders.items():
            self.scope.addItem(f"{title}  ({len(items)})", rv.FOLDER + title)
        if folders:
            self.scope.insertSeparator(self.scope.count())
        self.scope.addItem("All folders", rv.SCOPE_FOLDERS)
        self.scope.addItem("Not in any folder", rv.SCOPE_LOOSE)
        self.scope.addItem("Everything you have", rv.SCOPE_EVERYTHING)
        found = self.scope.findData(wanted)
        # The folder chosen last time may have been deleted since.
        self.scope.setCurrentIndex(found if found >= 0
                                   else self.scope.findData(rv.SCOPE_EVERYTHING))
        self.scope.blockSignals(False)

    def _save_scope(self) -> None:
        self.settings.set(SECTION, "scope", self.scope.currentData())
        self.settings.save()

    # -- settings ---------------------------------------------------------

    def _save_mode(self) -> None:
        self.settings.set(SECTION, "subscribe", self.mode.currentData())
        self.settings.save()
        if hasattr(self, "subscribe_all_btn"):
            self._update_subscribe_all()

    @property
    def by_steam(self) -> bool:
        return self.mode.currentData() == BY_STEAM

    def edit_credentials(self) -> None:
        """Set or remove the Steam key, and drop the client that used the old one."""
        if CredentialsDialog(self).exec() != CredentialsDialog.Accepted:
            return
        if self.steam is not None:
            self.steam.close()
        self.steam = self.review = None
        if has_key():
            # A key added after the banner was hidden: if it is removed again
            # one day, the banner comes back and says so.
            self.settings.set(SECTION, KEYLESS_HIDDEN, False)
            self.settings.save()
        self._show_keyless()
        self._say("Saved. Press “Scan” to use it.")

    def _show_keyless(self) -> None:
        self.keyless.setVisible(
            not has_key() and not self.settings.get(SECTION, KEYLESS_HIDDEN, False))

    def _hide_keyless(self) -> None:
        self.settings.set(SECTION, KEYLESS_HIDDEN, True)
        self.settings.save()
        self._show_keyless()

    def edit_authors(self) -> None:
        """The authors database and its backups."""
        dialog = AuthorsDialog(self.settings, self)
        dialog.exec()
        if self.db is not None:
            self.db.mirror = mirror_folder(self.settings)
        if dialog.restored:
            self._forget_review("The authors database was restored from a backup. "
                                "Press “Scan” to read it again.")
        elif self._damaged:
            self._damaged = False
            self._say("Press “Scan” to open the authors database again.")

    def _forget_review(self, message: str) -> None:
        """Drop everything read from the database: it is not what is there now."""
        if self.db is not None:
            self.db.close()
        self.db = self.review = self.result = self.current = None
        self._loading = None
        self.gallery.show_items([])
        self.heading.setText("")
        self.subheading.setText("")
        self._refresh_list()
        self.fill_btn.setEnabled(False)
        self.update_btn.setEnabled(False)
        self._summary = message
        self._say(message, "ok")

    # -- running work -----------------------------------------------------

    def _run(self, work, on_done, label: str) -> None:
        if self._task is not None and self._task.isRunning():
            return
        self._say(label)
        self._busy(True)
        self.scan_btn.setEnabled(False)
        task = Task(work, self)
        task.step.connect(self._step)
        task.done.connect(on_done)
        task.failed.connect(self._failed)
        task.finished.connect(self._finished)
        self._task = task
        task.start()

    def _progress_relay(self, stage: str, done: int, total: int) -> None:
        """What the engine reports progress through, for the tab's whole life.

        It used to be handed `task.step.emit` of whichever Task happened to be
        running when the `Review` was built — the first scan's. A `Review` is
        built once and kept, and a finished Task is deleted, so the second
        thing to report progress emitted from a deleted QObject and PySide
        raised **"Signal source has been deleted"** in red across the tab. It
        took one press of "Count what is new" after one scan.

        A signal on the tab has no such lifetime: the tab is what owns every
        Task in the first place. The `RuntimeError` guard is for the one case
        left — the tab itself going away while a fetch is still in flight, and
        nobody is reading the status line then either.
        """
        try:
            self.progressed.emit(stage, done, total)
        except RuntimeError:
            pass

    def _step(self, stage: str, done: int, total: int) -> None:
        if total:
            self.progress.setRange(0, total)
            self.progress.setValue(done)
        self._say(f"{stage}: {done}/{total}" if total else stage)

    def _finished(self) -> None:
        self._busy(False)
        self.scan_btn.setEnabled(True)
        # Every Task is a child of the tab, and a finished one used to stay one
        # for the rest of the session: a QThread object per author ever opened.
        if self._task is not None:
            self._task.deleteLater()
            self._task = None

    def _failed(self, message: str) -> None:
        self._say(message, "danger")
        if self._damaged:
            answer = QMessageBox.critical(
                self, "The authors database is damaged",
                f"{message}\n\nNothing will be written to it. Open the backups "
                "and restore the newest one? The damaged file is set aside, "
                "not deleted.",
                QMessageBox.Open | QMessageBox.Cancel, QMessageBox.Open)
            if answer == QMessageBox.Open:
                self.edit_authors()

    # -- phase one --------------------------------------------------------

    def start_scan(self) -> None:
        # Nothing to set up first: the database is a file made on first use,
        # and the Steam key is optional — the banner says what going without
        # one costs.
        self._damaged = False

        def work(step):
            if self.db is None:
                step("opening the authors database", 0, 0)
                try:
                    self.db = open_store(self.settings)
                except StoreDamaged:
                    self._damaged = True
                    raise
                # A database with no backup at all — the first run after an
                # import, or backups deleted by hand — gets one before anything.
                self.db.ensure_snapshot()
            if self.review is None:
                self.steam = SteamClient(
                    api_key=secrets.get(secrets.STEAM_API_KEY) or None)
                self.library = Library()
                if not self.library.scanned:
                    step("reading the local libraries", 0, 0)
                    self.library.refresh()
                self.review = Review(self.db, self.steam, self.library,
                                     on_progress=self._progress_relay)
            try:
                return self.review.scan(scope)
            except SteamAuthError as err:
                raise RuntimeError(
                    f"Steam refused the Web API key ({err}). Change or remove it "
                    "under “Steam key…” — the review also works without one.") from err

        scope = self.scope.currentData() or rv.DEFAULT_SCOPE
        self._save_scope()
        # A scan is also the moment to notice a folder filled since the tab
        # was opened — which is a wallpaper more that was yours before.
        if self.review is not None:
            self.review.forget_owned()
        self._run(work, self._scanned, f"Reading {rv.scope_label(scope)}…")

    def _scanned(self, result: ReviewResult) -> None:
        self.result = result
        self._owned_tasks.clear()
        self._owned_pending.clear()
        self._load_scopes()
        self._refresh_list()
        self.fill_btn.setEnabled(True)
        self.update_btn.setEnabled(True)
        self._summary = result.summary() + ".  Press “Count what is new”, or click an author."
        if self.steam is not None and not self.steam.has_key:
            self._summary += "  No Steam key: mature wallpapers are not counted."
        if self.db is not None and self.db.warnings:
            warnings = "; ".join(self.db.warnings)
            self.db.warnings.clear()
            self._say(f"{self._summary}  But {warnings}.", "warn")
            return
        self._say(self._summary)

    def start_fill(self) -> None:
        if self.result is None:
            return
        result = self.result

        def work(_step):
            # Through the engine's batch, not one card at a time. The loop
            # this replaced made one Steam request after another — 641 ms of
            # Frankfurt each, 54 s for the 85 authors of an ordinary week —
            # and re-read the workshop folder for every one of them. The batch
            # reads it once and lets the waiting overlap, which the client's
            # own throttle still paces. It reports its progress through the
            # hook the review was built with, which is this tab's status line.
            return self.review.fill_all(result)

        self._run(work, self._filled, "Asking Steam what each author has published…")

    def _filled(self, result: ReviewResult) -> None:
        self._refresh_list()
        counts = result.counts
        # "0 of them yours once" is not information — at this depth only
        # wallpapers newer than the last visit are counted, and none of those
        # can have been owned before. The clause appears when it means something.
        returning = counts.get("returning", 0)
        line = (f"{counts.get('to_review', 0)} wallpapers to look at across "
                f"{counts.get('authors', 0)} authors")
        if returning:
            line += f", {returning} of them yours once"
        self._summary = line + ".  Click an author, or move through them with the arrow keys."
        blind = len(result.incomplete)
        if blind:
            self._summary = (f"{line}.  {blind} authors were counted without a "
                             "Steam key — their mature wallpapers are not in it.")
            self._say(self._summary, "warn")
            return
        self._say(self._summary)

    # -- phase two --------------------------------------------------------

    def open_author(self, card: AuthorCard) -> None:
        """Show an author's gallery, fetching it first if it has not been.

        Not gated behind whatever else is running. The arrow keys make it
        normal to open a second author before the first has loaded, and a
        single shared "busy" flag used to drop the second request on the floor
        and leave its gallery empty. Each load runs on its own, and a result
        for an author no longer on screen only updates that author's row.
        """
        if card is None or self.review is None:
            return
        if card is self.current and (card.deep or self._loading is card):
            return
        self.current = card
        self._describe(card)
        if card.deep:
            self.gallery.show_items(card.offered)
            self._update_subscribe_all()
            self._check_owned(card)
            self._settle_status()
            return
        self.gallery.show_items([])
        self._update_subscribe_all()
        self._loading = card
        self._say(f"Reading what {card.name} has published"
                  + (f" since {card.visited:%Y-%m-%d}…" if card.visited else "…"))

        task = Task(lambda _step: self.review.fill(card, full=True), self)
        task.done.connect(self._author_ready)
        task.failed.connect(lambda message, c=card: self._author_failed(c, message))
        task.finished.connect(lambda t=task: self._task_done(t))
        self._tasks.add(task)
        self._busy(True)
        task.start()

    def _task_done(self, task: Task) -> None:
        self._tasks.discard(task)
        self._busy(False)
        task.deleteLater()

    def _author_ready(self, card: AuthorCard) -> None:
        if self._loading is card:
            self._loading = None
        self.authors.touch(card)
        if self.current is not card:
            return
        self._describe(card)
        self.gallery.show_items(card.offered)
        self._update_subscribe_all()
        self._check_owned(card)
        self._settle_status()

    # -- have you had these before? ----------------------------------------

    def _check_owned(self, card: AuthorCard) -> None:
        """Mark this gallery's wallpapers that you have had before, on a thread.

        Not during the count. Counting a week is four hundred authors and
        nobody is looking at a gallery yet, so the set this needs — every
        workshop id in the local libraries plus every id Wallpaper Engine's
        folders remember, 20 470 of them here — is built the first time a
        gallery is actually opened, and shared by every gallery after it.

        On a thread because that first build parses a 2.35 MB `config.json`
        and walks the library index, and the window must not stop for it. It
        stays off the busy counter: the progress bar answers “is the tab
        fetching”, and nobody asked for this or is waiting on it.
        """
        if card is None or card.owned_checked or self.review is None:
            return
        if card.id64 in self._owned_pending:
            # Clicking an author twice while the first pass is in flight.
            return
        self._owned_pending.add(card.id64)
        task = Task(lambda _step, c=card: (c, self.review.owned_before()), self)
        task.done.connect(self._owned_ready)
        task.failed.connect(lambda message, c=card: self._owned_failed(c, message))
        task.finished.connect(lambda t=task: self._owned_tasks.discard(t))
        self._owned_tasks.add(task)
        task.start()

    def _owned_ready(self, found) -> None:
        """The answer landed: mark the gallery and say what it came to."""
        card, owned = found
        self._owned_pending.discard(card.id64)
        if self.review is None:
            return
        self.review.mark_owned(card, owned)
        self.authors.touch(card)
        if self.current is card:
            self.gallery.refresh_page()
            self._describe(card)

    def _owned_failed(self, card: AuthorCard, message: str) -> None:
        """Say so once, quietly. The gallery is still usable without it — it
        simply goes on saying nothing about what you used to own."""
        self._owned_pending.discard(card.id64)
        if self.current is card:
            self._say(f"Could not work out what you have owned before: {message}",
                      "warn")

    def _author_failed(self, card: AuthorCard, message: str) -> None:
        if self._loading is card:
            self._loading = None
        if self.current is card:
            self._say(f"Could not read {card.name}'s workshop: {message}", "danger")

    def _describe(self, card: AuthorCard) -> None:
        self.heading.setText(card.name)
        bits = [STATE_LABEL[card.state]]
        if card.database_name:
            bits.append(f"in the database as “{card.database_name}”")
        bits.append(f"id {card.id64}")
        if card.visited:
            bits.append(f"last visited {card.visited:%Y-%m-%d}")
        if card.filled:
            # For a known author only what came after the visit is shown;
            # everything before it has been looked at once already.
            bits.append(f"{card.badge} new since then" if card.visited
                        else f"{card.badge} not subscribed")
            if card.owned_checked:
                # Said even at zero, because "none of these were ever yours"
                # is the answer the mark exists to give, and silence reads as
                # "not worked out yet" — which it was, a second ago.
                bits.append(f"{card.returning} were yours once"
                            if card.returning else "none were yours before")
            bits.append(f"{card.total} published in total")
        if card.filled and not card.complete:
            bits.append("list incomplete: read without a Steam key, mature "
                        "wallpapers left out")
        if card.error:
            bits.append(card.error)
        self.subheading.setText("  ·  ".join(bits))

    def _page_changed(self, page: int, pages: int, total: int) -> None:
        self.page_label.setText(
            f"page {page} of {pages}   ·   {total} wallpapers" if total
            else "nothing to show")
        self.prev_btn.setEnabled(page > 1)
        self.next_btn.setEnabled(page < pages)
        self._update_subscribe_all()

    # -- subscribing ------------------------------------------------------

    def subscribe(self, item_id: str) -> None:
        if not self.by_steam:
            self.open_in_steam(item_id)
            return
        if item_id in self.gallery.delegate.busy:
            return
        self.gallery.mark_busy(item_id, True)
        self.subscriptions.add([item_id])

    def subscribe_page(self) -> None:
        """Subscribe to every wallpaper on the page that is not already taken."""
        if not self.by_steam:
            return
        wanted = [w.id for w in self.gallery.current_page()
                  if w is not None and not w.subscribed
                  and w.id not in self.gallery.delegate.busy]
        if not wanted:
            return
        for item_id in wanted:
            self.gallery.mark_busy(item_id, True)
        self._say(f"Subscribing to {len(wanted)} wallpapers…")
        self.subscriptions.add(wanted)
        self._update_subscribe_all()

    def _update_subscribe_all(self) -> None:
        page = [w for w in self.gallery.current_page() if w is not None]
        open_ = [w for w in page if not w.subscribed
                 and w.id not in self.gallery.delegate.busy]
        self.subscribe_all_btn.setEnabled(self.by_steam and bool(open_))
        self.subscribe_all_btn.setText(
            f"Subscribe to all {len(open_)} on this page" if open_
            else "Subscribe to all on this page")
        self.subscribe_all_btn.setToolTip(
            "" if self.by_steam else
            "Needs “Steam directly”: opening Steam's page for every wallpaper "
            "would mean thirty windows.")

    def _title_of(self, item_id: str) -> str:
        for wallpaper in self.gallery.showing():
            if wallpaper is not None and wallpaper.id == item_id:
                return wallpaper.title or item_id
        return item_id

    def _subscribed(self, item_id: str, _state: str) -> None:
        self.gallery.mark_busy(item_id, False)
        self._mark_subscribed(item_id)
        if not self.subscriptions.pending():
            self._say(f"Subscribed to “{self._title_of(item_id)}”. "
                      "Steam downloads it in the background.", "ok")
        self._update_subscribe_all()

    def _subscribe_failed(self, item_id: str, message: str) -> None:
        self.gallery.mark_busy(item_id, False)
        self._say(f"Could not subscribe to “{self._title_of(item_id)}”: {message}. "
                  "Switch to “Opening Steam's page” if this keeps happening.", "danger")
        self._update_subscribe_all()

    def _subscribe_progress(self, done: int, asked: int) -> None:
        if asked > 1 and done < asked:
            self._say(f"Subscribing… {done} of {asked}")

    def _mark_subscribed(self, item_id: str) -> None:
        """Show a wallpaper as taken, and take it out of the count.

        The card is dimmed where it stands rather than removed: a tile
        vanishing under the cursor loses the reader's place in a wall of four
        hundred. The badge falls by itself, because it counts what is still on
        offer and this no longer is.
        """
        if self.library is not None:
            self.library.note_subscribed(item_id)
        for wallpaper in self.gallery.showing():
            if wallpaper is not None and wallpaper.id == item_id:
                wallpaper.subscribed = True
                self.gallery.refresh(item_id)
        if self.current is not None:
            self.authors.touch(self.current)
            self._describe(self.current)

    def _notice_subscriptions(self) -> None:
        """Catch wallpapers subscribed anywhere else, including Steam's page.

        The look itself is a listing of Steam's workshop folder, and it runs on
        a thread of its own. It used to run here, on the GUI thread, every four
        seconds — and that folder sits on whatever disk Steam does, which on
        this machine is a hard disk Wallpaper Engine is streaming video from,
        and which Steam writes every new subscription to.
        """
        if self.library is None or not self.isVisible() or self._watching is not None:
            return
        if not any(w and not w.subscribed for w in self.gallery.showing()):
            return
        library = self.library
        task = Task(lambda _step: library.subscribed(), self)
        task.done.connect(self._subscriptions_seen)
        task.finished.connect(lambda t=task: self._watch_done(t))
        self._watching = task
        task.start()

    def _subscriptions_seen(self, on_disk: set) -> None:
        for wallpaper in self.gallery.showing():
            if wallpaper is not None and not wallpaper.subscribed \
                    and wallpaper.id in on_disk:
                self._mark_subscribed(wallpaper.id)

    def _watch_done(self, task: Task) -> None:
        if self._watching is task:
            self._watching = None
        task.deleteLater()

    def open_in_steam(self, item_id: str) -> None:
        """Steam's own page for this wallpaper — the path that needs no SDK."""
        url = f"steam://url/CommunityFilePage/{item_id}"
        try:
            subprocess.Popen(["cmd", "/c", "start", "", url], shell=False)
        except OSError:
            webbrowser.open(
                f"https://steamcommunity.com/sharedfiles/filedetails/?id={item_id}")

    def open_workshop(self) -> None:
        if self.current:
            webbrowser.open(workshop_url(self.current.id64))

    # -- writing back -----------------------------------------------------

    def apply_review(self) -> None:
        if self.result is None or self.review is None:
            return
        # Every card, not only the counted ones: an author nobody opened this
        # week still gets their current name, just not a new visit date.
        changes = self.review.plan(self.result.cards)
        if not changes:
            QMessageBox.information(self, "Nothing to write",
                                    "No author's record would change.")
            return
        created = sum(1 for c in changes if c.kind == "create")
        renamed = sum(1 for c in changes if c.kind == "update" and "name" in c.fields)
        visited = sum(1 for c in changes if c.kind == "update" and "visited" in c.fields)
        preview = "\n".join(c.describe() for c in changes[:14])
        if len(changes) > 14:
            preview += f"\n… and {len(changes) - 14} more"
        text = (f"{created} authors to create, {visited} visit dates to move, "
                f"{renamed} names to bring up to date.\n\n{preview}")

        # A visit date moved from a list Steam cut short moves past wallpapers
        # nobody was shown. That is the one consequence of going without a key
        # that adding one later does not undo, so it is said here, where it
        # happens, and the safe button is the default.
        blind = self._written_blind(changes)
        if blind:
            text += (f"\n\n⚠  {blind} of these come from lists read without a "
                     "Steam key. Steam leaves mature wallpapers out of those, and "
                     "moving the visit date past them means they will not be "
                     "offered later — not even after a key is added.")
            answer = QMessageBox.warning(
                self, "Update the authors database", text,
                QMessageBox.Ok | QMessageBox.Cancel, QMessageBox.Cancel)
        else:
            answer = QMessageBox.question(
                self, "Update the authors database", text,
                QMessageBox.Ok | QMessageBox.Cancel)
        if answer != QMessageBox.Ok:
            return

        def work(step):
            report = self.db.apply(changes, on_progress=lambda d, n: step("writing", d, n))
            report["changes"] = changes
            report["warnings"] = list(self.db.warnings)
            self.db.warnings.clear()
            return report

        self._run(work, self._applied, "Writing to the authors database…")

    def _written_blind(self, changes) -> int:
        """How many of these would set a visit date from an incomplete list."""
        if self.result is None:
            return 0
        blind = {c.id64 for c in self.result.incomplete}
        if not blind:
            return 0
        owner = {id(r): c.id64 for c in self.result.cards for r in c.records}
        count = 0
        for change in changes:
            if change.kind == "create":
                count += change.fields.get("key") in blind
            elif change.kind == "update" and "visited" in change.fields:
                count += owner.get(id(change.author)) in blind
        return count

    def _applied(self, report: dict) -> None:
        # What was written is now what the cards should say, without a rescan.
        self.review.absorb(self.result, report.get("changes", []))
        self._refresh_list()
        if self.current is not None:
            self._describe(self.current)
        line = f"{report['created']} created, {report['updated']} updated."
        backup = report.get("backup")
        if backup:
            line += f"  Backed up as {Path(backup).name}"
            line += " here and in the second folder." if self.db and self.db.mirror else "."
        warnings = report.get("warnings") or []
        if warnings:
            self._say(line + "  But " + "; ".join(warnings), "warn")
        else:
            self._say(line, "ok")
