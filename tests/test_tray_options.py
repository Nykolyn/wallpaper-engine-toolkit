"""Tray customization: persisted values, independent fills and settings controls."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = tempfile.TemporaryDirectory(prefix="tray_options_")
os.environ["WALLPAPER_TOOLKIT_DATA"] = TMP.name

from PySide6.QtWidgets import QApplication
from app import settings as store, theme, tray_icon, tray_options
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
