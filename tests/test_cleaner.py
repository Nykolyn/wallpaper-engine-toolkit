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
from PySide6.QtTest import QTest
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
        self.assertEqual(Path(target), (reserve / "1001").resolve())
        self.assertEqual(events, [str(self.folder.resolve())])
        self.assertFalse(self.folder.exists())
        self.assertEqual(list(self.root.glob(".toolkit-reserve-*")), [])

    def test_copy_failure_and_collision_preserve_source(self):
        reserve = self.root / "reserve"
        with patch.object(engine, "verify_copy", side_effect=OSError("size differs")):
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
        self.assertIn(str(self.folder), page.model.data(page.model.index(0, 1), Qt.ToolTipRole))
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
        self.assertEqual((page.nav_state().kind, page.nav_state().text), ("none", ""))
        self.assertEqual(page.empty.title(), "Nothing to process")
        page.deleteLater()
        services.install(None)

    def test_zero_results_hide_badge_in_live_page_and_fixtures(self):
        page = CleanerPage(Settings({}))
        self.assertEqual(page.nav_state().text, "waiting")
        page.scanning = True
        page._render()
        self.assertEqual(page.nav_state().text, "checking")
        page._scanned(engine.Scan([], {}, 10))
        self.assertEqual((page.nav_state().kind, page.nav_state().text), ("none", ""))
        for state in page.FIXTURES:
            page.load_fixture(state)
            self.assertEqual(page.nav_state().kind == "badge", state == "found")
            self.assertEqual(page.frame_fixture(state)["nav"][page.key]["kind"],
                             "badge" if state == "found" else "none")
        page.deleteLater()

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

    def test_selection_filter_sort_keyboard_and_batch_partial_failure(self):
        svc = services.Services(data_dir=TMP / "data")
        services.install(svc)
        page = CleanerPage(Settings({}), svc, Config(source=str(self.root / "reserve"), destination=""))
        rows = [engine.Wallpaper(str(self.folder), "Garden", size=300),
                engine.Wallpaper(str(self.folder.parent / "1002"), "Other", size=2),
                engine.Wallpaper(str(self.folder.parent / "1003"), "Third", size=20)]
        page._scanned(engine.Scan(rows, {"new": ["1001"]}, 3))
        page.show()
        qt.processEvents()
        with patch("app.pages.cleaner.open_in_explorer") as explorer:
            QTest.mouseClick(page.table.viewport(), Qt.LeftButton,
                             pos=page.table.visualRect(page.model.index(0, 0)).center())
            self.assertEqual(page.selected, {rows[0].folder})
            self.assertEqual(page.all_rows.checkState(), Qt.PartiallyChecked)
            explorer.assert_not_called()
        page.model.sort(4, Qt.AscendingOrder)
        self.assertTrue(page.model.cell(rows[0], 0).checked)
        QTest.keyClick(page.table, Qt.Key_A, Qt.ControlModifier)
        self.assertEqual(page.selected, {row.folder for row in rows})
        QTest.keyClick(page.table, Qt.Key_A, Qt.ControlModifier)
        self.assertFalse(page.selected)
        page.table.setCurrentIndex(page.model.index(0, 1))
        QTest.keyClick(page.table, Qt.Key_Space)
        self.assertEqual(page.selected, {rows[1].folder})
        page.all_rows.click()
        self.assertEqual(page.all_rows.checkState(), Qt.Checked)
        page.source.setCurrentIndex(page.source.findData("new"))
        self.assertEqual(page.selected, {rows[0].folder})
        page.source.setCurrentIndex(0)
        self.assertEqual(page.selected, {rows[0].folder})
        page.all_rows.click()
        with patch.object(page, "_answer", return_value=False) as answer, \
             patch("app.pages.cleaner.delete_wallpaper") as remove:
            self.assertFalse(page._selected_action("delete"))
            answer.assert_called_once()
            remove.assert_not_called()
        calls, threads = [], []
        def remove(folder):
            calls.append(folder)
            threads.append(threading.get_ident())
            if folder == rows[1].folder:
                raise OSError("in use")
            return Deleted(folder, Path(folder).name)
        with patch.object(page, "_answer", return_value=True) as answer, \
             patch("app.pages.cleaner.delete_wallpaper", side_effect=remove):
            self.assertTrue(page._selected_action("delete"))
            self.assertFalse(page.delete_selected.isEnabled())
            self.assertFalse(page.source.isEnabled())
            self.assertFalse(page._selected_action("delete"))
            self.assertTrue(wait_for(lambda: not page.acting))
            answer.assert_called_once()
        self.assertEqual(set(calls), {row.folder for row in rows})
        self.assertTrue(all(ident != threading.get_ident() for ident in threads))
        self.assertEqual(page.model.items(), [rows[1]])
        self.assertEqual(page.selected, {rows[1].folder})
        self.assertEqual(svc.jobs.last_finished("cleaner").result, "problems")
        self.assertTrue(page.delete_selected.isEnabled())
        # Retry uses the same verified reserve engine as individual actions.
        with patch.object(page, "_answer", return_value=True) as answer, \
             patch.object(engine, "move_to_reserve", return_value="verified reserve") as move:
            self.assertTrue(page._selected_action("reserve"))
            self.assertTrue(wait_for(lambda: not page.acting))
            answer.assert_called_once()
            move.assert_called_once_with(rows[1].folder, page.config.source)
        self.assertFalse(page.selected)
        self.assertFalse(page.reserve_selected.isEnabled())
        page.deleteLater()
        services.install(None)

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

    def test_batch_reserve_requires_destination_and_moves_all_once(self):
        page = CleanerPage(Settings({}))
        rows = [engine.Wallpaper(str(self.folder), "Garden"),
                engine.Wallpaper(str(self.folder.parent / "1002"), "Other")]
        page._scanned(engine.Scan(rows, {}, 2))
        page.select_all()
        with patch.object(page, "_answer") as answer:
            self.assertFalse(page._selected_action("reserve"))
            answer.assert_not_called()
        page.config.source = str(self.root / "reserve")
        with patch.object(page, "_answer", return_value=True) as answer, \
             patch.object(engine, "move_to_reserve", return_value="verified copy") as move:
            self.assertTrue(page._selected_action("reserve"))
            self.assertTrue(wait_for(lambda: not page.acting))
            answer.assert_called_once()
            self.assertEqual([call.args for call in move.call_args_list],
                             [(row.folder, page.config.source) for row in rows])
        self.assertEqual(page.model.rowCount(), 0)
        self.assertEqual((page.nav_state().kind, page.nav_state().text), ("none", ""))
        page.deleteLater()


if __name__ == "__main__":
    unittest.main()
