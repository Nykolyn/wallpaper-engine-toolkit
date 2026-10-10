"""Cleaner detection, safe reserve moves and its asynchronous page, on fabricated data."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

scratch = tempfile.TemporaryDirectory(prefix="cleaner-test-")
TMP = Path(scratch.name)
os.environ["WALLPAPER_TOOLKIT_DATA"] = str(TMP / "data")
(TMP / "data").mkdir()
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from app import animations, services, theme
from app.engines import unavailable as engine
from app.engines.steam_api import ItemDetails, SteamClient, SteamError, ITEM_CACHE
from app.engines.rotator.config import Config
from app.engines.wallpaper_delete import Deleted
from app.main_window import MainWindow
from app.pages.cleaner import CleanerPage
from app.settings import Settings

qt = QApplication(sys.argv)
theme.apply(qt)
animations.ENABLED = False


def wait_for(condition, seconds=5):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        qt.processEvents()
        if condition():
            return True
        time.sleep(.005)
    return condition()


def wallpaper(root, item="1001", title="Garden"):
    folder = root / "431960" / item
    folder.mkdir(parents=True)
    (folder / "project.json").write_text(json.dumps({"title": title, "type": "Video", "file": "video.mp4"}),
                                       encoding="utf-8")
    (folder / "video.mp4").write_bytes(b"video" * 30)
    (folder / "preview.jpg").write_bytes(b"preview")
    return folder


class CleanerTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(dir=TMP))
        self.folder = wallpaper(self.root)

    def test_explicit_unavailable_results_and_legacy_cache(self):
        good = wallpaper(self.root, "1002")
        uncertain = wallpaper(self.root, "1003")
        (self.folder.parent / "1004").mkdir()       # shader leftovers, no manifest
        calls = []
        class Client:
            def details(_, ids, **options):
                calls.append((list(ids), options))
                if options.get("refresh"):
                    return {"1001": ItemDetails.from_steam({"publishedfileid": "1001", "result": 9})}
                return {"1001": ItemDetails("1001", False), "1002": ItemDetails("1002", True),
                        "1003": ItemDetails.from_steam({"publishedfileid": "1003", "result": 16})}
        with patch.object(engine, "find_workshop_content", return_value=self.folder.parent), \
             patch.object(engine, "we_folders", return_value={"new": ["1001", "1001", "9999"]}), \
             patch.object(engine, "author_names", return_value={"1001": SimpleNamespace(name="Mira")}), \
             patch.object(engine, "manifest", wraps=engine.manifest) as measured:
            result = engine.scan(client=Client())
        self.assertEqual([r.folder for r in result.rows], [str(self.folder)])
        row = result.rows[0]
        self.assertEqual((row.title, row.author, row.kind), ("Garden", "Mira", "video"))
        self.assertEqual(row.size, sum(p.stat().st_size for p in self.folder.iterdir()))
        self.assertAlmostEqual(row.created.timestamp(), self.folder.stat().st_ctime, places=4)
        self.assertEqual(measured.call_count, 1)    # no healthy wallpaper size/manifest reads
        self.assertEqual(calls[0][0], ["1001", "1002", "1003"])
        self.assertEqual(calls[0][1]["max_age"], 86400)
        self.assertEqual(calls[1][0], ["1001"])
        self.assertIn("1 wallpapers", result.warning)
        self.assertTrue(good.exists() and uncertain.exists())

    def test_network_and_missing_answers_are_not_cleanup_candidates(self):
        client = SteamClient(cache_path=None, retries=1)
        with patch.object(engine, "find_workshop_content", return_value=self.folder.parent), \
             patch.object(engine, "we_folders", return_value={}), \
             patch.object(client, "_fetch", return_value='{"response":{"publishedfiledetails":[]}}'):
            with self.assertRaises(SteamError):
                engine.scan(client=client)
        with patch.object(client, "details", side_effect=SteamError("offline")), \
             patch.object(engine, "find_workshop_content", return_value=self.folder.parent):
            with self.assertRaisesRegex(SteamError, "offline"):
                engine.scan(client=client)
        self.assertTrue(self.folder.exists())

    def test_cache_age_and_result_survive_existing_cache_format(self):
        with SteamClient(cache_path=self.root / "cache.sqlite") as client:
            client._cache.put(ITEM_CACHE, {"1001": ItemDetails("1001", False, result=9).to_json()})
            with patch.object(client, "_details_batch", side_effect=AssertionError("network")):
                self.assertEqual(client.details(["1001"], max_age=86400)["1001"].result, 9)
            client._cache._conn.execute("UPDATE cache SET fetched=fetched-86401")
            client._cache._conn.commit()
            with patch.object(client, "_details_batch", return_value={"1001": ItemDetails("1001", True)}) as fetch:
                self.assertTrue(client.details(["1001"], max_age=86400)["1001"].ok)
                self.assertEqual(fetch.call_count, 1)
        self.assertEqual(ItemDetails.from_json({"id": "1", "ok": False}).result, 0)
        with SteamClient(cache_path=self.root / "author.sqlite") as client:
            client._remember_items([ItemDetails("1001", True, creator="76561198000000001")])
            client._remember_items([ItemDetails("1001", False, result=9)])
            self.assertEqual(client.details(["1001"])["1001"].creator, "76561198000000001")

    def test_move_verifies_copy_before_source_removal(self):
        reserve = self.root / "reserve"
        events = []
        def remove(source):
            target = reserve / "1001"
            self.assertEqual((target / "video.mp4").read_bytes(), (self.folder / "video.mp4").read_bytes())
            self.assertTrue((target / "preview.jpg").is_file())
            events.append(source)
            shutil.rmtree(source)
        target = engine.move_to_reserve(str(self.folder), str(reserve), remove=remove)
        self.assertEqual(target, str(reserve / "1001"))
        self.assertEqual(events, [str(self.folder)])
        self.assertFalse(self.folder.exists())
        self.assertEqual(list(self.root.glob(".toolkit-reserve-*")), [])

    def test_copy_failure_and_collision_preserve_source(self):
        reserve = self.root / "reserve"
        with patch.object(engine.CopyEngine, "_verify", side_effect=OSError("size differs")):
            with self.assertRaisesRegex(OSError, "size differs"):
                engine.move_to_reserve(str(self.folder), str(reserve), remove=lambda _: self.fail("remove"))
        self.assertTrue(self.folder.exists())
        self.assertFalse((reserve / "1001").exists())
        self.assertEqual(list(self.root.glob(".toolkit-reserve-*")), [])
        (reserve / "1001").mkdir()
        (reserve / "1001" / "keep.txt").write_text("keep")
        with self.assertRaises(FileExistsError):
            engine.move_to_reserve(str(self.folder), str(reserve))
        self.assertEqual((reserve / "1001" / "keep.txt").read_text(), "keep")
        self.assertTrue(self.folder.exists())

    def test_unsubscribe_failure_keeps_verified_copy_and_source(self):
        reserve = self.root / "reserve"
        def fail(_):
            raise OSError("Steam unavailable")
        with self.assertRaisesRegex(OSError, "verified copy is kept"):
            engine.move_to_reserve(str(self.folder), str(reserve), remove=fail)
        self.assertTrue(self.folder.exists())
        self.assertEqual((reserve / "1001" / "video.mp4").read_bytes(), (self.folder / "video.mp4").read_bytes())

    def test_validation_prevents_bad_destination_or_empty_payload(self):
        for reserve in ("", str(self.folder), str(self.folder / "nested"), str(self.root)):
            with self.assertRaises(ValueError):
                engine.move_to_reserve(str(self.folder), reserve)
        with patch.object(engine, "destination_info", return_value={"free": 0}):
            with self.assertRaisesRegex(OSError, "free space"):
                engine.move_to_reserve(str(self.folder), str(self.root / "reserve"))
        (self.folder / "video.mp4").write_bytes(b"")
        with self.assertRaisesRegex(ValueError, "missing or empty"):
            engine.move_to_reserve(str(self.folder), str(self.root / "reserve"))
        self.assertTrue(self.folder.exists())

    def test_page_filters_badge_sorting_open_and_actions(self):
        svc = services.Services(data_dir=TMP / "data")
        services.install(svc)
        page = CleanerPage(Settings({}), svc, Config(source=str(self.root / "reserve"), destination=""))
        other = engine.Wallpaper(str(self.folder.parent / "1002"), "Other", size=2)
        rows = [engine.Wallpaper(str(self.folder), "Garden", size=300), other]
        page._scanned(engine.Scan(rows, {"new": ["1001", "1001", "9999"]}, 2))
        self.assertEqual((page.model.rowCount(), page.nav_state().text), (2, "2"))
        page.source.setCurrentIndex(page.source.findData("new"))
        self.assertEqual((page.model.rowCount(), page.nav_state().text), (1, "2"))
        self.assertIn(str(self.folder), page.model.data(page.model.index(0, 0), Qt.ToolTipRole))
        with patch("app.pages.cleaner.open_in_explorer") as explorer:
            page._open(page.model.index(0, 1))
            page._open(page.model.index(0, 1))
            self.assertEqual(explorer.call_count, 1)
            self.assertEqual(explorer.call_args.args[0], str(self.folder))
        page._scanned(SteamError("offline"))
        self.assertEqual((page.model.rowCount(), page.nav_state().text), (1, "2"))
        self.assertIn("previous results", page.problem.body())
        with patch.object(page, "_answer", return_value=False):
            self.assertFalse(page._action(0, 5, "reserve"))
        with patch.object(page, "_answer", return_value=True), \
             patch.object(engine, "move_to_reserve", return_value="fake reserve"):
            self.assertTrue(page._action(0, 5, "reserve"))
            self.assertFalse(page._action(0, 5, "reserve"))
            self.assertTrue(wait_for(lambda: not page.acting))
        self.assertEqual(page.nav_state().text, "1")
        self.assertEqual(page.model.rowCount(), 0)
        self.assertEqual(svc.jobs.last_finished("cleaner").result, "clean")
        self.assertEqual(svc.journal.recent(1)[0].kind, "reserve_move.clean")
        page.source.setCurrentIndex(0)
        with patch.object(page, "_answer", return_value=True), \
             patch("app.pages.cleaner.delete_wallpaper", side_effect=OSError("in use")):
            self.assertTrue(page._action(0, 5, "delete"))
            self.assertTrue(wait_for(lambda: not page.acting))
        self.assertEqual(page.nav_state().text, "1")
        with patch.object(page, "_answer", return_value=True), \
             patch("app.pages.cleaner.delete_wallpaper", return_value=Deleted(other.folder, "1002")):
            self.assertTrue(page._action(0, 5, "delete"))
            self.assertTrue(wait_for(lambda: not page.acting))
        self.assertEqual(page.nav_state().text, "0")
        self.assertEqual(page.empty.title(), "Nothing to process")
        page.deleteLater()
        services.install(None)

    def test_startup_is_background_once_and_disabled_for_fixtures(self):
        page = CleanerPage(Settings({}))
        entered, release = threading.Event(), threading.Event()
        threads = []
        def scan(**kwargs):
            threads.append(threading.get_ident())
            entered.set()
            release.wait(5)
            return engine.Scan([], {}, 0)
        with patch("app.pages.cleaner.on_steam_found", side_effect=lambda _, then: then()), \
             patch.object(engine, "scan", side_effect=scan):
            before = time.monotonic()
            page.startup()
            page.startup()
            self.assertLess(time.monotonic() - before, .2)
            self.assertTrue(entered.wait(2))
            self.assertTrue(page.scanning)
            release.set()
            self.assertTrue(wait_for(lambda: not page.scanning))
        self.assertEqual(len(threads), 1)
        self.assertNotEqual(threads[0], threading.get_ident())
        window = MainWindow(settings=Settings({}), pages=[page], start=False, initial="cleaner")
        with patch.object(page, "startup") as startup:
            qt.processEvents()
            self.assertEqual(startup.call_count, 0)
        window.feed.stop()
        window.deleteLater()
        fixture = CleanerPage(Settings({}))
        fixture.load_fixture("found")
        with patch.object(engine, "scan", side_effect=AssertionError("live scan")):
            fixture.startup()
            self.assertFalse(fixture.check())
            self.assertFalse(fixture._action(0, 5, "delete"))
        fixture.deleteLater()

    def test_cleaner_and_rotation_do_not_move_folders_together(self):
        from app.pages.rotator import RotatorPage
        svc = services.Services(data_dir=TMP / "data")
        services.install(svc)
        config = Config(source=str(self.root / "reserve"), destination="")
        page, rotator = CleanerPage(Settings({}), svc, config), RotatorPage(config, svc)
        window = MainWindow(settings=Settings({}), pages=[page, rotator], services_=svc,
                            start=False, initial="cleaner")
        page._scanned(engine.Scan([engine.Wallpaper(str(self.folder), "Garden")], {}, 1))
        job = svc.jobs.start("rotator", "Rotating")
        with patch.object(page, "_answer", side_effect=AssertionError("must wait")):
            self.assertFalse(page._action(0, 5, "reserve"))
        job.finish("clean", "Done")
        job = svc.jobs.start("cleaner", "Moving to reserve")
        with patch.object(rotator, "_begin_check", side_effect=AssertionError("must wait")):
            rotator.start_rotation()
        self.assertTrue(rotator._busy())
        job.finish("clean", "Done")
        window.feed.stop()
        window.deleteLater()
        services.install(None)


if __name__ == "__main__":
    unittest.main()
