"""The window's pages, in the order of the loop.

- base: `Page`, what the frame needs from a page, and `LegacyPage`, an old
  tab in the new frame.
- overview: the loop at a glance (a placeholder until its own step).
- settings: what is set once.
- legacy: what the old tabs' sidebar items say, until their pages say it.

`build_pages(window)` makes them all for a window, from its settings, feed and
services. Each page step swaps one `LegacyPage` for its own page.
"""
from __future__ import annotations

from .base import LegacyPage, Page

__all__ = ["Page", "LegacyPage", "build_pages"]


def build_pages(window) -> list[Page]:
    from ..engines.rotator.config import Config
    from ..ui.copier_tab import CopierTab
    from ..ui.creator_tab import CreatorTab
    from ..ui.review_tab import ReviewTab
    from ..ui.rotator_tab import RotatorTab
    from ..ui.tracker_tab import TrackerTab
    from .legacy import LegacyNav
    from .overview import OverviewPage
    from .settings import SettingsPage

    settings, feed, services = window.settings, window.feed, window.services
    # One Config for the Rotator tab and the Settings page: what one saves the
    # other has, and a run is never handed a config the page is changing
    # (the page's Rotator fields are read-only while it runs).
    config = Config.load()
    rotator = RotatorTab(config)
    tracker = TrackerTab(settings, feed)
    review = ReviewTab(settings)
    creator = CreatorTab(settings)
    copier = CopierTab(settings)
    legacy = [
        LegacyPage("rotator", "Rotator", "rotator", rotator,
                   "Swaps a batch of folders between the reserve and myprojects"),
        LegacyPage("tracker", "Tracker", "tracker", tracker,
                   "Counts each monitor's playlist down"),
        LegacyPage("review", "Review", "review", review,
                   "What is new from the authors in your library"),
        LegacyPage("creator", "Creator", "creator", creator,
                   "Wallpapers made from video clips"),
        LegacyPage("copier", "Copier", "copier", copier,
                   "Copies of folders, so a playlist shows them more often"),
    ]
    settings_page = SettingsPage(settings, config, feed=feed, services=services,
                                 on_steam_key=review.edit_credentials,
                                 on_authors=review.edit_authors)

    def changed(what: str) -> None:
        if what == "rotator":
            rotator.refresh_all()

    settings_page.changed.connect(changed)
    window._legacy_nav = LegacyNav(legacy, services, window)
    return [OverviewPage(services, feed), *legacy, settings_page]
