"""Tray customization: persisted values, independent fills and settings controls."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = tempfile.TemporaryDirectory(prefix="tray_options_")
os.environ["WALLPAPER_TOOLKIT_DATA"] = TMP.name

from PySide6.QtWidgets import QApplication
from app import settings as store, theme, tray_icon, tray_options, tray_words
from app.engines.tracker import pick_primary
from app.engines.wallpaper_timer import Countdown
from app.main_window import PAGE_ORDER, SECTIONS
from app.pages.tray import TrayPage
from app.settings import Settings

app = QApplication.instance() or QApplication([])
theme.apply(app)


class TrayOptionsTest(unittest.TestCase):
    def setUp(self):
        self.settings = Settings({"widget": {"enabled": True}, "tray": {}})
        self.path = patch.object(store, "SETTINGS_PATH", Path(TMP.name) / "suite.json")
        self.path.start()

    def tearDown(self):
        self.path.stop()

    def test_validation_and_defaults(self):
        self.assertEqual(tray_options.read(self.settings), tray_options.DEFAULTS)
        for name, raw in (("front_color", "red"), ("back_color", []), ("progress", {})):
            with self.assertRaises(ValueError):
                tray_options.set_option(self.settings, name, raw)
            self.settings.set("tray", name, raw)
        self.assertEqual(tray_options.read(self.settings), tray_options.DEFAULTS)

    def test_persistence_preserves_other_process_changes(self):
        self.settings.save()
        other = Settings.load()
        other.set("widget", "mode", "expanded")
        other.save()
        tray_options.set_option(self.settings, "front_color", "#abcdef")
        loaded = Settings.load()
        self.assertEqual(loaded.get("tray", "front_color", None), "#ABCDEF")
        self.assertEqual(loaded.get("widget", "mode", None), "expanded")

    def test_independent_fills_and_swapping(self):
        def image(mode, fill, playlist, state=tray_icon.RUNNING):
            return tray_icon.mark_pixmap(state, 48, fill=fill, playlist_fill=playlist,
                options={"front_color": "#FF0000", "back_color": "#00FF00", "progress": mode}).toImage()

        def pixels(img, channel):
            return sum(img.pixelColor(x, y).getRgb()[channel] > 230
                       and img.pixelColor(x, y).getRgb()[1-channel] < 30
                       for x in range(48) for y in range(48))

        for mode, wallpaper_channel, playlist_channel in (("front_wallpaper", 0, 1), ("back_wallpaper", 1, 0)):
            low = image(mode, .2, .3)
            wallpaper = image(mode, 1., .3)
            playlist = image(mode, .2, 1.)
            self.assertGreater(pixels(wallpaper, wallpaper_channel), pixels(low, wallpaper_channel))
            self.assertEqual(pixels(wallpaper, playlist_channel), pixels(low, playlist_channel))
            self.assertGreater(pixels(playlist, playlist_channel), pixels(low, playlist_channel))
            self.assertEqual(pixels(playlist, wallpaper_channel), pixels(low, wallpaper_channel))
        self.assertEqual(image("combined", .4, .1), image("combined", .4, .9))
        for state in tray_icon.STATES:
            for size in tray_icon.SIZES:
                self.assertEqual(tray_icon.mark_pixmap(state, size, fill=.4).toImage(),
                                 tray_icon.mark_pixmap(state, size, fill=.4, options=tray_options.DEFAULTS).toImage())

    def test_front_fill_matches_visible_width_at_every_size(self):
        for size in tray_icon.SIZES:
            for mode in ("front_wallpaper", "back_wallpaper"):
                options = {"front_color": "#FF0000", "back_color": "#00FF00", "progress": mode}
                def image(share):
                    wallpaper, playlist = ((share, .42) if mode == "front_wallpaper" else (.42, share))
                    return tray_icon.mark_pixmap(tray_icon.RUNNING, size, fill=wallpaper,
                                                  playlist_fill=playlist, options=options).toImage()
                empty, full = image(0), image(1)
                rect = tray_icon._rect(tray_icon.FRONT, size)
                y = int(rect.center().y())
                span = rect.width() - tray_icon.FRONT[5] * tray_icon.scale(size)
                capacity = sum(full.pixelColor(x, y).red() - empty.pixelColor(x, y).red()
                               for x in range(size))
                for share in (0, .01, .1, .25, .255, .5, .75, .9, .99, .999, 1):
                    with self.subTest(size=size, mode=mode, share=share):
                        current = image(share)
                        actual = sum(current.pixelColor(x, y).red() - empty.pixelColor(x, y).red()
                                     for x in range(size)) / capacity
                        self.assertAlmostEqual(actual, share, delta=1.05 / span)
                        if 0 < share < 1:
                            self.assertNotEqual(current, full)

    def test_split_edges_and_states(self):
        for size in tray_icon.SIZES:
            for mode in ("front_wallpaper", "back_wallpaper"):
                options = {**tray_options.DEFAULTS, "progress": mode}
                def image(state, wallpaper, playlist):
                    return tray_icon.mark_pixmap(state, size, fill=wallpaper,
                                                 playlist_fill=playlist, options=options).toImage()
                for share in (None, -1, *(n / 100 for n in range(101)), .255, .999, 2):
                    back, front = tray_icon.fill_edges(size, share, share, mode)
                    for spec, edge in ((tray_icon.BACK, back), (tray_icon.FRONT, front)):
                        with self.subTest(size=size, mode=mode, share=share, rect=spec):
                            if share is None or share <= 0:
                                self.assertEqual(edge, 0)
                            elif share >= 1:
                                self.assertEqual(edge, size)
                            else:
                                rect = tray_icon._rect(spec, size)
                                stroke = spec[5] * tray_icon.scale(size) / 2
                                inset = -stroke if spec == tray_icon.BACK else stroke
                                ideal = rect.left() + inset + share * (rect.width() - 2 * inset)
                                self.assertLess(abs(edge - ideal), 1)
                                self.assertLess(edge, rect.right() - inset)
                for state in tray_icon.STATES:
                    self.assertEqual(image(state, -.2, -.2), image(state, 0, 0))
                    self.assertEqual(image(state, 2, 2), image(state, 1, 1))
                self.assertEqual(image(tray_icon.UNKNOWN, .255, .42), image(tray_icon.UNKNOWN, 0, 0))
                self.assertEqual(image(tray_icon.FINISHED, .255, .42), image(tray_icon.FINISHED, 1, 1))
                self.assertEqual(image(tray_icon.PAUSED, None, .42), image(tray_icon.PAUSED, 0, .42))
                reverse = "back_wallpaper" if mode == "front_wallpaper" else "front_wallpaper"
                for state in (tray_icon.RUNNING, tray_icon.PAUSED):
                    swapped = tray_icon.mark_pixmap(state, size, fill=.42, playlist_fill=.255,
                        options={**options, "progress": reverse}).toImage()
                    self.assertEqual(image(state, .255, .42), swapped)

    def test_screenshot_reading_and_selected_monitor(self):
        paused = SimpleNamespace(monitor="Monitor0", total=2059, seen=2)
        selected = SimpleNamespace(monitor="Monitor1", total=199, seen=84)
        primary = pick_primary([paused, selected], "Monitor1")
        clock = Countdown(monitor="Monitor1", delay=600, running=153, known=True, active=True)
        state, fill = tray_words.icon_state(primary, clock)
        self.assertEqual(state, tray_icon.RUNNING)
        self.assertAlmostEqual(fill, .255)
        self.assertAlmostEqual(primary.seen / primary.total, 84/199)
        clock.paused = True
        self.assertEqual(tray_words.icon_state(primary, clock), (tray_icon.PAUSED, fill))
        self.assertEqual(tray_words.icon_state(primary, None), (tray_icon.UNKNOWN, None))
        self.assertEqual(tray_words.icon_state(None, clock), (tray_icon.UNKNOWN, None))
        selected.seen = selected.total
        self.assertEqual(tray_words.icon_state(primary, None), (tray_icon.FINISHED, 1))

    def test_page_edits_validation_and_reset(self):
        page = TrayPage(self.settings)
        page.colors["front_color"].setText("#abcdef")
        page.colors["front_color"].editingFinished.emit()
        self.assertEqual(tray_options.read(Settings.load())["front_color"], "#ABCDEF")
        page.colors["back_color"].setText("#NOPE")
        page.colors["back_color"].editingFinished.emit()
        self.assertTrue(page.colors["back_color"].property("error"))
        page.mode.setCurrentIndex(page.mode.findData("back_wallpaper"))
        self.assertEqual(tray_options.read(Settings.load())["progress"], "back_wallpaper")
        page._reset()
        self.assertEqual(tray_options.read(Settings.load()), tray_options.DEFAULTS)
        self.assertTrue(self.settings.get("widget", "enabled", False))
        page.close()

    def test_sidebar_order(self):
        self.assertEqual(list(SECTIONS.values()), ["The loop", "Tools", "Utilities"])
        self.assertEqual(PAGE_ORDER[4:8], ("widget", "tray", "cleaner", "creator"))


if __name__ == "__main__":
    unittest.main()
