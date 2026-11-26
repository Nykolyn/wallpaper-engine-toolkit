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
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

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
from app.engines import file_copy
from app.engines import outside_playlists as outside
from app.engines.steam_api import ItemDetails, SteamClient, SteamError, ITEM_CACHE
from app.engines.rotator.config import Config
from app.engines.wallpaper_delete import Deleted
from app.main_window import MainWindow
from app.pages.cleaner import CleanerPage
from app.settings import Settings

qt = QApplication(sys.argv)


@contextmanager
def per_folder(target, attribute=None, **mock_args):
    """Stand in for a batch call (delete_wallpapers, move_many) with a Mock
    called once per folder, the way the page used to call it."""
    mock = MagicMock(**mock_args)
    def batch(folders, *rest, on_progress=None, **_):
        out = []
        for folder in folders:
            try:
                out.append(mock(folder, *rest))
            except Exception as err:  # noqa: BLE001 — the batch reports it per folder
                out.append(err)
            if on_progress:
                on_progress(len(out), len(folders))
        return out
    with (patch(target, side_effect=batch) if attribute is None
          else patch.object(target, attribute, side_effect=batch)):
        yield mock
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

    def steam(self, answer=True, asked=None):
        """A Steam that agrees (or not) to drop whatever it is asked to."""
        def unsubscribe_many(ids):
            if asked is not None:
                asked.append(list(ids))
            if isinstance(answer, Exception):
                raise answer
            return {item: answer for item in ids}
        return unsubscribe_many

    def other_drive(self):
        """The reserve on another volume than the Workshop folder: the copy path."""
        return patch.object(engine, "_volume", side_effect=lambda p: 1 if "431960" in str(p) else 2)

    def test_move_on_one_volume_renames_before_unsubscribing(self):
        reserve, video = self.root / "reserve", (self.folder / "video.mp4").read_bytes()
        def unsubscribe_many(ids):
            # Out of the Workshop folder, not yet in the reserve, when Steam is asked.
            self.assertFalse(self.folder.exists())
            self.assertFalse((reserve / "1001").exists())
            self.assertEqual(len(list(self.root.glob(".toolkit-reserve-*/1001"))), 1)
            return {item: True for item in ids}
        target = engine.move_to_reserve(str(self.folder), str(reserve), unsubscribe_many=unsubscribe_many,
                                        recycle=lambda _: self.fail("nothing to recycle"))
        self.assertEqual(Path(target), (reserve / "1001").resolve())
        self.assertEqual((reserve / "1001" / "video.mp4").read_bytes(), video)
        self.assertFalse(self.folder.exists())
        self.assertEqual(list(self.root.glob(".toolkit-reserve-*")), [])

    def test_move_puts_the_folder_back_when_steam_keeps_it(self):
        reserve = self.root / "reserve"
        for answer, words in ((False, "still lists it"), (OSError("Steam is not running"), "not running")):
            with self.assertRaisesRegex(OSError, f"{words}.*put back"):
                engine.move_to_reserve(str(self.folder), str(reserve), unsubscribe_many=self.steam(answer))
            self.assertTrue((self.folder / "video.mp4").is_file())
            self.assertFalse((reserve / "1001").exists())
            self.assertEqual(list(self.root.glob(".toolkit-reserve-*")), [])

    def test_folder_in_use_changes_nothing(self):
        asked = []
        with patch.object(engine.os, "rename", side_effect=PermissionError("in use")):
            with self.assertRaisesRegex(OSError, "Nothing was changed"):
                engine.move_to_reserve(str(self.folder), str(self.root / "reserve"),
                                       unsubscribe_many=self.steam(asked=asked))
        self.assertEqual(asked, [])
        self.assertTrue(self.folder.exists())
        self.assertEqual(list(self.root.glob(".toolkit-reserve-*")), [])

    def test_batch_move_asks_steam_once(self):
        second, asked = wallpaper(self.root, "1002"), []
        results = engine.move_many([str(self.folder), str(second), str(self.root / "431960" / "404")],
                                   str(self.root / "reserve"), unsubscribe_many=self.steam(asked=asked))
        self.assertEqual(asked, [["1001", "1002"]])
        self.assertEqual([Path(r).name for r in results[:2]], ["1001", "1002"])
        self.assertIsInstance(results[2], Exception)

    def test_move_to_another_drive_verifies_copy_before_source_removal(self):
        reserve = self.root / "reserve"
        events = []
        def recycle(source):
            target = reserve / "1001"
            self.assertEqual((target / "video.mp4").read_bytes(), (self.folder / "video.mp4").read_bytes())
            self.assertTrue((target / "preview.jpg").is_file())
            events.append(source)
            shutil.rmtree(source)
            return True
        with self.other_drive():
            target = engine.move_to_reserve(str(self.folder), str(reserve), unsubscribe_many=self.steam(),
                                            recycle=recycle)
        self.assertEqual(Path(target), (reserve / "1001").resolve())
        self.assertEqual(events, [self.folder.resolve()])
        self.assertFalse(self.folder.exists())
        self.assertEqual(list(self.root.glob(".toolkit-reserve-*")), [])

    def test_copy_failure_and_collision_preserve_source(self):
        reserve = self.root / "reserve"
        with self.other_drive(), patch.object(file_copy, "verify_copy", side_effect=OSError("size differs")):
            with self.assertRaisesRegex(OSError, "size differs"):
                engine.move_to_reserve(str(self.folder), str(reserve), unsubscribe_many=self.steam(),
                                       recycle=lambda _: self.fail("recycle"))
        self.assertTrue(self.folder.exists())
        self.assertFalse((reserve / "1001").exists())
        self.assertEqual(list(self.root.glob(".toolkit-reserve-*")), [])
        (reserve / "1001").mkdir()
        (reserve / "1001" / "keep.txt").write_text("keep")
        for drive in (self.other_drive(), patch.object(engine, "_volume", return_value=1)):
            with drive, self.assertRaises(FileExistsError):
                engine.move_to_reserve(str(self.folder), str(reserve), unsubscribe_many=self.steam())
            self.assertEqual((reserve / "1001" / "keep.txt").read_text(), "keep")
            self.assertTrue(self.folder.exists())

    def test_unsubscribe_failure_keeps_verified_copy_and_source(self):
        reserve = self.root / "reserve"
        with self.other_drive(), self.assertRaisesRegex(OSError, "verified copy is kept"):
            engine.move_to_reserve(str(self.folder), str(reserve),
                                   unsubscribe_many=self.steam(OSError("Steam unavailable")))
        self.assertTrue(self.folder.exists())
        self.assertEqual((reserve / "1001" / "video.mp4").read_bytes(), (self.folder / "video.mp4").read_bytes())

    def test_validation_prevents_bad_destination_or_empty_payload(self):
        for reserve in ("", str(self.folder), str(self.folder / "nested"), str(self.root)):
            with self.assertRaises(ValueError):
                engine.move_to_reserve(str(self.folder), reserve, unsubscribe_many=self.steam())
        self.assertFalse((self.folder / "nested").exists())
        with self.other_drive(), patch.object(file_copy, "destination_info", return_value={"free": 0}):
            with self.assertRaisesRegex(OSError, "free space"):
                engine.move_to_reserve(str(self.folder), str(self.root / "reserve"), unsubscribe_many=self.steam())
        (self.folder / "video.mp4").write_bytes(b"")
        with self.assertRaisesRegex(ValueError, "missing or empty"):
            engine.move_to_reserve(str(self.folder), str(self.root / "reserve"), unsubscribe_many=self.steam())
        self.assertTrue(self.folder.exists())

    def test_unsubscribe_many_asks_for_all_before_waiting(self):
        from app.engines.steam_ugc import STATE_INSTALLED, STATE_SUBSCRIBED, SteamUgc
        events, subscribed = [], {"1": True, "2": True}
        class Lib:
            def SteamAPI_ISteamUGC_UnsubscribeItem(self, _ugc, item):
                events.append(("ask", str(item)))
            def SteamAPI_ISteamUGC_GetItemState(self, _ugc, item):
                return STATE_INSTALLED | (STATE_SUBSCRIBED if subscribed[str(item)] else 0)
            def SteamAPI_RunCallbacks(self):
                events.append(("wait",))
                subscribed.update({k: False for k in subscribed})   # Steam catches up
        ugc = SteamUgc(dll="missing.dll")
        ugc._lib, ugc._ugc = Lib(), 1
        started = time.monotonic()
        states = ugc.unsubscribe_many(["1", "2"], wait=5)
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(events, [("ask", "1"), ("ask", "2"), ("wait",)])
        self.assertFalse(any(s.subscribed for s in states.values()))

    def test_unsubscribe_many_waits_while_steam_keeps_dropping(self):
        """Steam drops one item per tick: the whole selection takes longer than
        `wait`, yet every item is waited for (29 of 57 were given up on)."""
        from app.engines import steam_ugc
        from app.engines.steam_ugc import STATE_INSTALLED, STATE_SUBSCRIBED, SteamUgc
        subscribed = {str(i): True for i in range(6)}
        class Lib:
            def SteamAPI_ISteamUGC_UnsubscribeItem(self, _ugc, _item):
                pass
            def SteamAPI_ISteamUGC_GetItemState(self, _ugc, item):
                return STATE_INSTALLED | (STATE_SUBSCRIBED if subscribed[str(item)] else 0)
            def SteamAPI_RunCallbacks(self):
                left = [k for k, v in subscribed.items() if v]
                if left and not stalled:
                    subscribed[left[0]] = False
        clock = [0.0]
        def sleep(seconds):
            clock[0] += seconds
        ugc = SteamUgc(dll="missing.dll")
        ugc._lib, ugc._ugc = Lib(), 1
        with patch.object(steam_ugc.time, "monotonic", lambda: clock[0]), \
                patch.object(steam_ugc.time, "sleep", sleep):
            stalled = False
            states = ugc.unsubscribe_many(list(subscribed), wait=0.25)
            self.assertFalse(any(s.subscribed for s in states.values()))
            self.assertGreater(clock[0], 0.25)
            subscribed.update({"0": True})
            stalled, clock[0] = True, 0.0
            states = ugc.unsubscribe_many(list(subscribed), wait=0.25)
            self.assertTrue(states["0"].subscribed)
            self.assertLess(clock[0], 0.5)

    def test_batch_delete_asks_steam_once_and_reports_each(self):
        from app.engines.wallpaper_delete import DeleteError, delete_wallpapers
        second, asked, progress = wallpaper(self.root, "1002"), [], []
        mine = self.root / "myprojects" / "own"
        mine.mkdir(parents=True)
        results = delete_wallpapers([self.folder, second, mine, self.root / "gone"],
                                    unsubscribe_many=lambda ids: asked.append(ids) or {"1001": True, "1002": False},
                                    recycle=lambda p: shutil.rmtree(p) or True,
                                    on_progress=lambda d, t: progress.append((d, t)))
        self.assertEqual(asked, [["1001", "1002"]])
        self.assertEqual(results[0].workshop_id, "1001")
        self.assertIsInstance(results[1], DeleteError)
        self.assertTrue(second.exists())
        self.assertEqual(results[2].workshop_id, "")
        self.assertIsInstance(results[3], DeleteError)
        self.assertEqual(progress[-1], (4, 4))
        third = wallpaper(self.root, "1003")
        failed = delete_wallpapers([third], unsubscribe_many=self.steam(OSError("no Steam")),
                                   recycle=lambda _: self.fail("recycle"))
        self.assertRegex(str(failed[0]), "no Steam")
        self.assertTrue(third.exists())

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
             per_folder(engine, "move_many", return_value="fake reserve"):
            self.assertTrue(page._action(0, 5, "reserve"))
            self.assertFalse(page._action(0, 5, "reserve"))
            self.assertTrue(wait_for(lambda: not page.acting))
        self.assertEqual(page.nav_state().text, "1")
        self.assertEqual(page.model.rowCount(), 0)
        self.assertEqual(svc.jobs.last_finished("cleaner").result, "clean")
        self.assertEqual(svc.journal.recent(1)[0].kind, "reserve_move.clean")
        page.source.setCurrentIndex(0)
        with patch.object(page, "_answer", return_value=True), \
             per_folder("app.pages.cleaner.delete_wallpapers", side_effect=OSError("in use")):
            self.assertTrue(page._action(0, 5, "delete"))
            self.assertTrue(wait_for(lambda: not page.acting))
        self.assertEqual(page.nav_state().text, "1")
        with patch.object(page, "_answer", return_value=True), \
             per_folder("app.pages.cleaner.delete_wallpapers", return_value=Deleted(other.folder, "1002")):
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
             per_folder("app.pages.cleaner.delete_wallpapers") as remove:
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
             per_folder("app.pages.cleaner.delete_wallpapers", side_effect=remove):
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
             per_folder(engine, "move_many", return_value="verified reserve") as move:
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
             per_folder(engine, "move_many", return_value="verified copy") as move:
            self.assertTrue(page._selected_action("reserve"))
            self.assertTrue(wait_for(lambda: not page.acting))
            answer.assert_called_once()
            self.assertEqual([call.args for call in move.call_args_list],
                             [(row.folder, page.config.source) for row in rows])
        self.assertEqual(page.model.rowCount(), 0)
        self.assertEqual((page.nav_state().kind, page.nav_state().text), ("none", ""))
        page.deleteLater()


class OutsideTabTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(dir=TMP))
        self.rows = [engine.Wallpaper(str(wallpaper(self.root, "3")), "Three"),
                     engine.Wallpaper(str(wallpaper(self.root, "4")), "Four")]
        self.found = outside.OutsideScan(self.rows, {"all": ["3", "4"], "new": ["4"]}, None)

    def page(self, settings=None):
        # Review's own default is folder:new; most tests want every row.
        page = CleanerPage(Settings(settings or {"review": {"scope": "@everything"}}))
        with patch.object(outside, "scan", return_value=self.found) as scan:
            page.tabs.set_current_index(1)
            self.assertTrue(wait_for(lambda: not page.scanning))
        self.assertEqual(scan.call_count, 1)
        return page

    def test_outside_tab_scans_once_and_filters_by_remembered_folder(self):
        page = self.page({"cleaner": {"outside_folder": "all"}})
        self.assertEqual(page.mode, "outside")
        self.assertEqual((page.source.currentData(), page.model.rowCount()), ("all", 2))
        with patch.object(outside, "scan") as scan:
            page.source.setCurrentIndex(page.source.findData("new"))
            scan.assert_not_called()
        self.assertEqual(page.model.rowCount(), 1)
        self.assertEqual(page.settings.get("cleaner", "outside_folder", None), "new")
        page.tabs.set_current_index(0)
        self.assertEqual(page.source.currentData(), "")       # the Unavailable filter is its own
        page.deleteLater()

    def test_review_folder_is_the_default(self):
        page = self.page({"review": {"scope": "folder:new"}})
        self.assertEqual((page.source.currentData(), page.model.rowCount()), ("new", 1))
        page.deleteLater()

    def test_outside_rows_have_no_reserve_button(self):
        page = self.page()
        buttons = page.model.cell(page.model.item_at(0), 8).buttons
        self.assertEqual([b.key for b in buttons], ["delete"])
        self.assertTrue(page.reserve_selected.isHidden())
        page.deleteLater()

    def test_readded_to_playlist_is_skipped(self):
        page = self.page()
        page.select_all()
        with patch.object(page, "_answer", return_value=True), \
             patch.object(outside, "playlisted_ids", return_value={"3"}), \
             per_folder("app.pages.cleaner.delete_wallpapers",
                   side_effect=lambda folder: Deleted(folder, Path(folder).name)) as delete:
            self.assertTrue(page._selected_action("delete"))
            self.assertTrue(wait_for(lambda: not page.acting))
        self.assertEqual([c.args[0] for c in delete.call_args_list], [self.rows[1].folder])
        self.assertIn("1 now in a playlist", page.messages[-1][1])
        self.assertEqual(page.model.rowCount(), 0)
        page.deleteLater()

    def test_unreadable_config_deletes_nothing(self):
        page = self.page()
        with patch.object(page, "_answer", return_value=True), \
             patch.object(outside, "playlisted_ids", side_effect=OSError("config.json is gone")), \
             per_folder("app.pages.cleaner.delete_wallpapers") as delete:
            self.assertTrue(page._action(0, 8, "delete"))
            self.assertTrue(wait_for(lambda: not page.acting))
        delete.assert_not_called()
        self.assertEqual(page.messages[-1][0], "danger")
        self.assertEqual(page.model.rowCount(), 2)
        page.deleteLater()

    def test_tabs_locked_while_busy(self):
        page = CleanerPage(Settings({}))
        release = threading.Event()
        def slow(**_):
            release.wait(5)
            return self.found
        with patch.object(outside, "scan", side_effect=slow):
            page.tabs.set_current_index(1)
            self.assertFalse(page.tabs.isEnabled())
            release.set()
            self.assertTrue(wait_for(lambda: not page.scanning))
        self.assertTrue(page.tabs.isEnabled())
        page.deleteLater()

    def test_startup_check_is_the_unavailable_one(self):
        page = self.page()
        with patch.object(outside, "scan") as scan, patch.object(engine, "scan") as steam:
            page._startup_check()
            scan.assert_not_called()
            steam.assert_not_called()
        with patch.object(engine, "scan", return_value=engine.Scan([], {}, 5)) as steam:
            page.tabs.set_current_index(0)
            self.assertTrue(wait_for(lambda: not page.scanning))
            steam.assert_called_once()
        self.assertEqual(page.nav_state().kind, "none")
        page.deleteLater()

    def test_delete_on_one_tab_drops_the_row_from_the_other(self):
        page = CleanerPage(Settings({"review": {"scope": "@everything"}}))
        page._scanned(engine.Scan([engine.Wallpaper(self.rows[0].folder, "Three")], {}, 1))
        with patch.object(outside, "scan", return_value=self.found):
            page.tabs.set_current_index(1)
            self.assertTrue(wait_for(lambda: not page.scanning))
        with patch.object(page, "_answer", return_value=True), \
             patch.object(outside, "playlisted_ids", return_value=set()), \
             per_folder("app.pages.cleaner.delete_wallpapers",
                   side_effect=lambda folder: Deleted(folder, Path(folder).name)):
            self.assertTrue(page._action(0, 8, "delete"))
            self.assertTrue(wait_for(lambda: not page.acting))
        self.assertEqual(page.nav_state().kind, "none")
        page.deleteLater()

    def test_accessible_names_follow_the_tab(self):
        page = self.page()
        self.assertNotIn("navailable", page.table.accessibleName())
        self.assertNotIn("navailable", page.source.accessibleName())
        page.deleteLater()

    def test_badge_counts_unavailable_only(self):
        page = CleanerPage(Settings({}))
        page._scanned(engine.Scan([engine.Wallpaper(str(self.root / "431960" / "9"), "Gone")], {}, 1))
        with patch.object(outside, "scan", return_value=self.found):
            page.tabs.set_current_index(1)
            self.assertTrue(wait_for(lambda: not page.scanning))
        self.assertEqual(page.nav_state().text, "1")
        page.deleteLater()


if __name__ == "__main__":
    unittest.main()
