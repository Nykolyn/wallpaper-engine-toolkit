"""The window pages, built from shared settings, feed and services."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, Signal

from .base import Page

__all__ = ["Page", "build_pages", "on_steam_found"]


class _SteamFound(QObject):
    """Steam's folders found (steam_paths.when_found), said on the window's
    thread: the finding thread emits `_arrived`, which is queued here."""

    found = Signal()
    _arrived = Signal()

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._arrived.connect(self.found)


def on_steam_found(parent: QObject, handler: Callable[[], None]) -> QObject:
    """Call `handler` on the window's thread once Steam's folders are known:
    at once if they are, otherwise as the finding thread finishes."""
    from ..engines import steam_paths

    bridge = _SteamFound(parent)
    bridge.found.connect(handler)

    def arrived() -> None:
        try:
            bridge._arrived.emit()
        except RuntimeError:
            pass                    # the window went before the answer came

    steam_paths.when_found(arrived)
    return bridge


def build_pages(window) -> list[Page]:
    from ..engines.rotator.config import Config
    from ..services.snapshot import RESERVE, ROTATION
    from ..engines.multipliers import MultiplierStore
    from .cleaner import CleanerPage
    from .reserve import ReservePage
    from .creator import CreatorPage
    from .overview import OverviewPage
    from .review import ReviewPage
    from .rotator import RotatorPage
    from .settings import SettingsPage
    from .tracker import TrackerPage
    from .widget import WidgetPage
    from .tray import TrayPage

    settings, feed, services = window.settings, window.feed, window.services
    # One Config for the Rotator page and the Settings page: what one saves the
    # other has, and a run is never handed a config the page is changing
    # (the Settings page's Rotator fields are read-only while it runs). The
    # Tracker reads where myprojects is from it, for Mark [protected].
    config = Config.load()
    multipliers = MultiplierStore()
    cleaner = CleanerPage(settings, services, config)
    rotator_page = RotatorPage(config, services, multipliers=multipliers)
    creator_page = CreatorPage(settings, services, config, on_rebuild=rotator_page.rebuild_playlist)
    tracker_page = TrackerPage(feed, services, settings=settings, config=config, multipliers=multipliers)
    review_page = ReviewPage(settings, services)
    settings_page = SettingsPage(settings, config, feed=feed, services=services,
                                 on_review_settings=review_page.edit_settings,
                                 on_authors=review_page.edit_authors)

    def changed(what: str) -> None:
        if what == "rotator":
            rotator_page.config_changed()
        if what == "creator" and creator_page._on_screen:
            creator_page.on_shown()

    def steam_found() -> None:
        # Steam's folders were looked up in the background (steam_paths), and
        # the defaults that waited for them can be shown now, where nothing
        # else is chosen; the Rotator's are saved, if they were waiting to be.
        try:
            moved = config.take_found_folders()
        except OSError:
            moved = True            # in memory, if not on disk: the next save tries again
        if moved:
            rotator_page.config_changed()
        settings_page.show_found_folders()
        if not settings.get("creator", "target", None):
            changed("creator")
        snapshot = getattr(services, "snapshot", None)
        if snapshot is not None:
            snapshot.refresh([RESERVE, ROTATION])

    settings_page.changed.connect(changed)
    rotator_page.config_edited.connect(settings_page.show_rotator)
    tracker_page.multiplier_changed.connect(rotator_page.refresh_multipliers)
    rotator_page.multiplier_changed.connect(tracker_page.refresh_multipliers)
    services.jobs.finished.connect(tracker_page.refresh_multipliers)
    on_steam_found(settings_page, steam_found)
    return [OverviewPage(services, feed, settings=settings), rotator_page, tracker_page,
            review_page, WidgetPage(settings, feed=feed), TrayPage(settings), cleaner, creator_page,
            ReservePage(settings, services, config), settings_page]
