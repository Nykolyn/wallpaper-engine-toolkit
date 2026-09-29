"""The window: a fixed frame, and the pages that change inside it.

    ┌ TitleBar ──────────────────────────────────────────────────┐
    │ Sidebar │ PageHeader: title · subtitle · actions            │
    │         ├───────────────────────────────────────────────────┤
    │         │ the page (a QStackedWidget, cross-faded)          │
    ├─────────┴───────────────────────────────────────────────────┤
    │ StatusLine: what is running, from any page, and "Show"      │
    └──────────────────────────────────────────────────────────────┘

The pages are in the order of the loop — Overview, Rotator, Tracker, Review,
then Creator and Copier — and Settings at the foot; Ctrl+1…7 goes to each.
The window opens on Overview. The old tabs are hosted as pages until each
page step replaces its own.

The sidebar and the status line are live and read one place, the services
(app/services): the JobCenter for what runs, the Snapshot for the numbers.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer
from PySide6.QtGui import QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import QHBoxLayout, QMainWindow, QStackedWidget, QVBoxLayout, QWidget

from . import animations, services, theme
from .pages.base import Page
from .services.snapshot import PLAYLIST
from .settings import Settings
from .tracker_feed import TrackerFeed, heartbeat_setting
from .ui.kit import NavState, PageHeader, Sidebar, StatusLine, TitleBar, ToastHost
from .ui.kit import base as kit_base
from .ui.kit import format as fmt
from .window_frame import NativeFrame

DISPLAY_NAME = "Toolkit"            # gate G10: the window, the tray, notifications
# The pages, in the order of the loop; Ctrl+1 is the first.
PAGE_ORDER = ("overview", "rotator", "tracker", "review", "creator", "copier", "settings")
DEFAULT_PAGE = "overview"
# The sidebar's groups: which pages open each.
SECTIONS = {"overview": "The loop", "creator": "Utilities"}
FOOTER = ("settings",)
TOOL_NAMES = {"rotator": "Rotator", "tracker": "Tracker", "review": "Review",
              "creator": "Creator", "copier": "Copier"}


def page_for(name: str | None) -> str | None:
    """The page an old tab name or a page key means (`--tab Tracker`,
    `show:rotator`), whatever its case; None for anything else."""
    key = (name or "").strip().casefold()
    return key if key in PAGE_ORDER else None


class Backdrop(QWidget):
    """The window's ground: `bg.app`, painted once behind everything.

    Every widget above it is transparent, so the translucent panels of the
    design pick the gradient up through themselves. It repaints under any child
    that repaints, so the brush is built once per size, not once per paint. It
    is also the surface the frame's controls draw their shadows and focus
    rings on.
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._brush = None
        kit_base.declare(self)

    def resizeEvent(self, event) -> None:       # noqa: N802 - Qt's name
        self._brush = theme.app_background(self.rect())
        super().resizeEvent(event)

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        if self._brush is None:
            self._brush = theme.app_background(self.rect())
        painter = QPainter(self)
        painter.fillRect(event.rect(), self._brush)
        kit_base.paint(painter, self, event.rect())


# ---- the status line, bound to the JobCenter --------------------------------------------

class StatusBinding(QObject):
    """Says in the status line what the JobCenter knows.

    - Something running: its tool, what it is doing, its bar and count, and
      "Show", which goes to its page.
    - Nothing running: "Nothing running · last run finished 13:58".
    - The last job ended with problems or failed: said in warn or danger, with
      a link to its page, until that page has been looked at.
    """

    def __init__(self, line: StatusLine, jobs, show, *, now=datetime.now,
                 parent: QObject | None = None):
        super().__init__(parent)
        self.line = line
        self.jobs = jobs
        self._show = show
        self._now = now
        self._seen: set[int] = set()        # finished jobs whose page was looked at since
        jobs.changed.connect(self.update)
        self.update()

    def acknowledge(self, page: str) -> None:
        """The user is on `page`: what ended badly there has been seen."""
        last = self.jobs.last_finished()
        if last is not None and last.page == page and last.result in ("problems", "failed"):
            self._seen.add(last.number)
            self.update()

    def update(self, *_args) -> None:
        job = self.jobs.current()
        if job is not None:
            text = f"{TOOL_NAMES.get(job.tool, job.tool.title())} · {job.phase_text or job.title}"
            others = len(self.jobs.running()) - 1
            if others > 0:
                text += f" · {others} more running"
            count = job.count_text
            if count is None and job.total > 0:
                count = f"{fmt.ratio(job.done, job.total)} · {fmt.percent(job.done, job.total)}"
            self.line.set_running(text, job.done, job.total, count,
                                  on_show=lambda page=job.page: self._show(page))
            return
        last = self.jobs.last_finished()
        if last is None:
            self.line.set_idle("Nothing running")
            return
        go = (lambda page=last.page: self._show(page))
        if last.result == "problems" and last.number not in self._seen:
            self.line.set_warn(f"{last.title} finished with problems · {last.summary}",
                               "See problems", go)
            return
        if last.result == "failed" and last.number not in self._seen:
            self.line.set_error(f"{last.title} failed · {last.summary}", "Show", go)
            return
        self.line.set_idle(f"Nothing running · {self._ended(last)}")

    def _ended(self, job) -> str:
        what = ("last run" if job.tool == "rotator" and job.title.startswith("Run ")
                else TOOL_NAMES.get(job.tool, job.tool.title()))
        how = {"clean": "finished", "problems": "finished with problems",
               "stopped": "was stopped", "failed": "failed"}.get(job.result, "finished")
        when = fmt.date_activity(job.ended, now=self._now()) if job.ended else ""
        return f"{what} {how} {when}".strip()


def next_in_loop(playlist, now: datetime | None = None) -> str:
    """The sidebar's "Next in the loop", from the leading monitor's count:
    "197 left on Monitor1 — rotate again ≈21 Sep". The date is an estimate from
    the cycle's own pace, and there only once it has one."""
    if playlist is None or playlist.total <= 0:
        return "No playlist counted yet"
    if playlist.remaining <= 0:
        return f"{playlist.monitor}'s playlist is done — time to rotate"
    text = f"{fmt.count(playlist.remaining)} left on {playlist.monitor}"
    day = _estimate_day(playlist.finish_estimate, now or datetime.now())
    # "≈21 Sep" is one thing: it wraps whole, never as "≈21" and "Sep".
    day = day.replace(" ", fmt.NBSP)
    return f"{text} — rotate again {fmt.approx(day)}" if day else text


def _estimate_day(estimate: str | None, now: datetime) -> str:
    """The day in the tracker's estimate ("21 Sep 09:10"), as the design writes it."""
    if not estimate:
        return ""
    try:
        when = datetime.strptime(estimate, "%d %b %H:%M").replace(year=now.year)
    except ValueError:
        return ""
    if (now - when).days > 180:
        when = when.replace(year=now.year + 1)      # an estimate past New Year
    return fmt.day(when, now)


# ---- the window ----------------------------------------------------------------------

class MainWindow(QMainWindow):
    """The frame and its pages.

    Everything it would otherwise make for itself can be handed in — the
    settings, the TrackerFeed, the services and the pages — which is how tests
    and `tools/ui_snapshot.py` build it without this machine's data.
    `start=False` leaves the services unstarted (no thread reads the disk).
    """

    def __init__(self, *, settings: Settings | None = None, feed=None, services_=None,
                 pages=None, start: bool = True, initial: str | None = None):
        super().__init__()
        self.settings = settings if settings is not None else Settings.load()
        self.setWindowTitle(DISPLAY_NAME)
        self.setWindowFlags(self.windowFlags() | Qt.FramelessWindowHint)
        self.setMinimumSize(*theme.WINDOW_MIN)
        self.resize(*theme.WINDOW_SIZE)

        self.backdrop = Backdrop()
        frame = QVBoxLayout(self.backdrop)
        frame.setContentsMargins(0, 0, 0, 0)
        frame.setSpacing(0)
        self.title_bar = TitleBar(DISPLAY_NAME)
        frame.addWidget(self.title_bar)
        body = QHBoxLayout()
        body.setSpacing(0)
        self.sidebar = Sidebar()
        body.addWidget(self.sidebar)
        self.content = QWidget()
        column = QVBoxLayout(self.content)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        self.header = PageHeader()
        self.stack = QStackedWidget()
        column.addWidget(self.header)
        column.addWidget(self.stack, 1)
        body.addWidget(self.content, 1)
        frame.addLayout(body, 1)
        self.status = StatusLine()
        frame.addWidget(self.status)
        self.setCentralWidget(self.backdrop)
        self.fade = animations.CrossFade(self.backdrop, cover=self.content)
        self.toasts = ToastHost(self.content)

        # The window's own look at the tracker: the Tracker page shows it, the
        # snapshot and the journal listen to it.
        self.feed = feed if feed is not None else TrackerFeed(
            self.settings.get("tracker", "we_config", None),
            heartbeat_setting(self.settings), parent=self)
        # What the pages report their work to (see app/services). Installed
        # before the pages, which find it when they start something.
        self.services = services_ if services_ is not None else services.Services(
            self, settings=self.settings, feed=self.feed)
        services.install(self.services)

        self.pages: dict[str, Page] = {}
        self._native: NativeFrame | None = None
        self._shown: Page | None = None         # the page the header and sidebar show
        # Sidebar parts a snapshot fixture has pinned, which the live ones leave alone.
        self._fixture_nav: dict[str, NavState] = {}
        if pages is None:
            from .pages import build_pages
            pages = build_pages(self)
        for page in pages:
            self.add_page(page)
        self.sidebar.page_requested.connect(self.show_page)

        self.status_binding = StatusBinding(self.status, self.services.jobs, self.show_page,
                                            parent=self)
        self.services.snapshot.refreshed.connect(self._snapshot_refreshed)
        self._snapshot_refreshed([PLAYLIST])
        for number, key in enumerate(PAGE_ORDER, start=1):
            shortcut = QShortcut(QKeySequence(f"Ctrl+{number}"), self)
            shortcut.activated.connect(lambda k=key: self.show_page(k))

        self.show_page(page_for(initial) or DEFAULT_PAGE, animate=False)
        self.resize(self.opening_size())
        self._place_rail()
        if start:
            self.services.start()

    # -- pages

    def add_page(self, page: Page) -> None:
        if page.key in self.pages:
            raise ValueError(f"there is already a page {page.key!r}")
        if page.key in SECTIONS:
            self.sidebar.add_section(SECTIONS[page.key])
        self.pages[page.key] = page
        self.stack.addWidget(page)
        self.sidebar.add_item(page.key, page.title, page.icon, footer=page.key in FOOTER)
        self.sidebar.set_state(page.key, page.nav_state())
        page.nav_state_changed.connect(lambda state, k=page.key: self._nav_changed(k, state))
        page.subtitle_changed.connect(lambda text, p=page: self._subtitle_changed(p, text))
        page.navigate.connect(self.show_page)

    def current_page(self) -> str:
        return self._shown.key if self._shown is not None else ""

    def show_page(self, key: str, animate: bool = True) -> bool:
        """Go to a page by its key (or an old tab's name). False, and nothing
        changes, if there is no such page."""
        key = page_for(key)
        page = self.pages.get(key) if key else None
        if page is None:
            return False
        before = self._shown
        if before is page:
            self._acknowledge(key)
            return True
        self._shown = page

        def change() -> None:
            self.stack.setCurrentWidget(page)
            self.header.set_title(page.title)
            self.header.set_subtitle(page.subtitle())
            self.header.set_actions(page.header_actions())
            self.sidebar.set_current(page.key)

        if animate and before is not None:
            self.fade.switch(change)
        else:
            change()
        if before is not None:
            before.on_hidden()
        page.on_shown()
        self._acknowledge(key)
        return True

    def _acknowledge(self, key: str) -> None:
        binding = getattr(self, "status_binding", None)
        if binding is not None:
            binding.acknowledge(key)

    def _subtitle_changed(self, page: Page, text: str) -> None:
        if self._shown is page:
            self.header.set_subtitle(text)

    def _nav_changed(self, key: str, state: NavState) -> None:
        if key not in self._fixture_nav:
            self.sidebar.set_state(key, state)

    def _snapshot_refreshed(self, keys) -> None:
        if PLAYLIST in keys and "next" not in self._fixture_nav:
            self.sidebar.next_in_loop.set_text(
                next_in_loop(self.services.snapshot[PLAYLIST].value))

    # -- asked from outside: the tray, a second launch

    def show_tab(self, name: str) -> bool:
        """What `--tab` and the old "show <tab>" message ask for."""
        return self.show_page(name)

    def bring_forward(self, page: str = "") -> None:
        """Answer a click on the tray icon, or a second launch of the program."""
        if page:
            self.show_page(page)
        self.show()
        self.setWindowState((self.windowState() & ~Qt.WindowMinimized) | Qt.WindowActive)
        self.raise_()
        self.activateWindow()

    def handle_command(self, verb: str, argument: str) -> None:
        """A window command (see window_instance): `show:<page>` now. A command
        this version does not know still brings the window forward."""
        self.bring_forward(argument if verb == "show" else "")

    # -- the frame's own behaviour

    def opening_size(self) -> QSize:
        """1280 × 860, or more where a page needs it to be shown whole — an old
        tab laid out for a window that grew to fit it — but never more than the
        screen has room for. Smaller than that, a page scrolls."""
        base_w, base_h = theme.WINDOW_SIZE
        width, height = base_w, base_h
        frame_w = theme.SIDEBAR_WIDTH
        frame_h = theme.TITLE_BAR_HEIGHT + theme.HEADER_HEIGHT + theme.STATUS_HEIGHT
        for page in self.pages.values():
            need = page.content_minimum()
            if need.isValid():
                width = max(width, frame_w + need.width())
        room = self.screen().availableGeometry() if self.screen() is not None else None
        if room is not None:
            width = max(base_w, min(width, room.width()))
        for page in self.pages.values():
            need = page.content_minimum(width - frame_w)
            if need.isValid():
                height = max(height, frame_h + need.height())
        if room is not None:
            height = max(base_h, min(height, room.height()))
        return QSize(width, height)

    def _place_rail(self) -> None:
        self.sidebar.set_rail(self.width() < theme.RAIL_BELOW)

    def resizeEvent(self, event) -> None:       # noqa: N802 - Qt's name
        super().resizeEvent(event)
        self._place_rail()
        self._fit_maximised()

    def showEvent(self, event) -> None:         # noqa: N802 - Qt's name
        if self._native is None:
            self._native = NativeFrame.attach(self, self.title_bar, theme.RESIZE_BORDER)
        super().showEvent(event)

    def changeEvent(self, event) -> None:       # noqa: N802 - Qt's name
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange:
            QTimer.singleShot(0, self._fit_maximised)

    def _fit_maximised(self) -> None:
        """Maximised, Windows puts the frame's width off each edge of the screen;
        with no frame, pad the content in by that much."""
        margins = self._native.maximised_margins() if self._native is not None else None
        if margins is not None:
            self.backdrop.layout().setContentsMargins(margins)

    def nativeEvent(self, event_type, message):  # noqa: N802 - Qt's name
        if self._native is not None and bytes(event_type) == b"windows_generic_MSG":
            answer = self._native.handle(int(message))
            if answer is not None:
                return True, answer
        return super().nativeEvent(event_type, message)

    # -- snapshots

    def load_fixture(self, state: str, fixture: dict | None = None) -> None:
        """Show the frame in a made-up state (`tools/ui_snapshot.py`): jobs in
        the JobCenter, the leading monitor's count, sidebar items pinned to a
        state, and the current page's own fixture. `fixture` is that state's
        entry in tests/fixtures/ui/shell.json."""
        fixture = fixture if fixture is not None else load_shell_fixture(state)
        jobs = self.services.jobs
        for spec in fixture.get("jobs", []):
            job = jobs.start(spec["tool"], spec.get("title", TOOL_NAMES.get(spec["tool"], "")),
                             spec.get("page"))
            job.update(spec.get("phase"), spec.get("done"), spec.get("total"),
                       spec.get("count_text"))
            if "result" in spec:
                job.finish(spec["result"], spec.get("summary", ""))
                if "ended" in spec:
                    job.ended = _fixture_time(spec["ended"])
        playlist = fixture.get("playlist")
        if playlist is not None:
            from .services.snapshot import PlaylistProgress
            self.services.snapshot.put(PLAYLIST, PlaylistProgress(**playlist))
        if "next" in fixture:
            self._fixture_nav["next"] = NavState()
            self.sidebar.next_in_loop.set_text(fixture["next"])
        for key, spec in fixture.get("nav", {}).items():
            nav = _nav_state(spec)
            self._fixture_nav[key] = nav
            self.sidebar.set_state(key, nav)
        self.status_binding.update()
        page = self.pages.get(self.current_page())
        if page is not None and page.FIXTURES:
            page.load_fixture(state if state in page.FIXTURES else page.FIXTURES[0])
            self.header.set_subtitle(page.subtitle())


def _fixture_time(text: str) -> datetime:
    """"13:58" is today at 13:58; anything longer is an ISO date and time."""
    if len(text) == 5:
        hour, minute = (int(part) for part in text.split(":"))
        return datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)
    return datetime.fromisoformat(text)


def _nav_state(spec: dict) -> NavState:
    kind = spec.get("kind", "none")
    if kind == "progress":
        return NavState.progress(spec["done"], spec["total"])
    if kind == "count":
        return NavState.count(spec["done"], spec["total"])
    if kind == "badge":
        return NavState.badge(spec["value"])
    if kind == "status":
        return NavState.status(spec["text"], spec.get("tone", "lo"), below=spec.get("below", False))
    return NavState()


FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "ui"


def load_shell_fixture(state: str) -> dict:
    """A state of the frame from tests/fixtures/ui/shell.json (a source checkout's)."""
    states = json.loads((FIXTURES / "shell.json").read_text(encoding="utf-8"))
    states.pop("//", None)
    if state not in states:
        raise KeyError(f"no shell fixture {state!r}; there are {', '.join(states)}")
    return states[state]
