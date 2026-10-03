"""The window pages, built from shared settings, feed and services."""
from __future__ import annotations

from .base import Page

__all__ = ["Page", "build_pages"]


def build_pages(window) -> list[Page]:
    from ..engines.rotator.config import Config
    from .copier import CopierPage
    from .creator import CreatorPage
    from .overview import OverviewPage
    from .review import ReviewPage
    from .rotator import RotatorPage
    from .settings import SettingsPage
    from .tracker import TrackerPage

    settings, feed, services = window.settings, window.feed, window.services
    # One Config for the Rotator page and the Settings page: what one saves the
    # other has, and a run is never handed a config the page is changing
    # (the Settings page's Rotator fields are read-only while it runs). The
    # Tracker reads where myprojects is from it, for Mark [protected].
    config = Config.load()
    copier = CopierPage(settings, services, config)
    rotator_page = RotatorPage(config, services)
    creator_page = CreatorPage(settings, services, config, on_rebuild=rotator_page.rebuild_playlist)
    tracker_page = TrackerPage(feed, services, settings=settings, config=config)
    review_page = ReviewPage(settings, services)
    settings_page = SettingsPage(settings, config, feed=feed, services=services,
                                 on_review_settings=review_page.edit_settings,
                                 on_authors=review_page.edit_authors)

    def changed(what: str) -> None:
        if what == "rotator":
            rotator_page.config_changed()
        if what == "copier" and copier._on_screen:
            copier.on_shown()
        if what == "creator" and creator_page._on_screen:
            creator_page.on_shown()

    def to_copier(folders: list) -> None:
        tracker_page.copier_took(folders, copier.add_folders(folders))

    settings_page.changed.connect(changed)
    rotator_page.config_edited.connect(settings_page.show_rotator)
    # The Tracker queues folders without starting any disk writes.
    tracker_page.copier_requested.connect(to_copier)
    return [OverviewPage(services, feed, settings=settings), rotator_page, tracker_page,
            review_page, creator_page, copier, settings_page]
