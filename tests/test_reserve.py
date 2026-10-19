"""Subscribed wallpapers missing from the reserve, using only fabricated libraries."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

scratch = tempfile.TemporaryDirectory(prefix="reserve-test-")
TMP = Path(scratch.name)
os.environ["WALLPAPER_TOOLKIT_DATA"] = str(TMP / "data")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from app import animations, services, theme
from app.engines import file_copy
from app.engines import we_memory
from app.engines import reserve as engine
from app.engines.rotator.config import Config
from app.engines.steam_api import ItemDetails, Profile, SteamError
from app.engines.steam_ugc import ItemState, SteamUgc, UgcError
from app.pages.reserve import ReservePage
from app.pages.cleaner import CleanerPage
from app.pages.rotator import RotatorPage
from app.pages.settings import SettingsPage
from app.settings import Settings
from app.ui.kit import StepList

qt = QApplication(sys.argv)
theme.apply(qt)
animations.ENABLED = False


def wallpaper(root, name, item=None):
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "project.json").write_text(json.dumps({
        "title": "Garden " + name, "type": "video", "file": "video.mp4",
        "workshopid": item or name,
    }), encoding="utf-8")
    (folder / "video.mp4").write_bytes(b"video" * 50)
    return folder


class Ugc:
    def __init__(self, states):
        self.flags = states
        self.asked = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def state(self, item):
        self.asked.append(item)
        return ItemState(item, self.flags.get(item, 0))


class Client:
    def details(self, ids, **_):
        return {i: ItemDetails(i, True, creator="76561198000000001") for i in ids}

    def profiles(self, ids, **_):
        return {i: Profile(i, name="Mira") for i in ids}


def wait_for(condition):
    until = time.monotonic() + 5
    while time.monotonic() < until:
        qt.processEvents()
        if condition():
            return True
        time.sleep(.005)
    return condition()


class ReserveTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(dir=TMP))
        self.workshop = self.root / "431960"
        self.workshop.mkdir()
        self.reserve = self.root / "reserve"
        self.reserve.mkdir()
        self.source = wallpaper(self.workshop, "1001")
        self.ugc = Ugc({"1001": 5})
        self.options = dict(workshop=self.workshop, ugc=self.ugc, client=Client(),
                            index_path=self.root / "index.json", cache_path=self.root / "cache.sqlite")

    def test_only_active_ready_valid_subscriptions_missing_from_this_reserve(self):
        for name in ("1002", "1003", "1004", "1005", "1006", "1007"):
            wallpaper(self.workshop, name)
        self.ugc.flags.update({"1002": 4, "1003": 21, "1004": 5, "1005": 5,
                               "1006": 5, "1007": 5})
        (self.workshop / "1004" / "project.json").write_text("[]")
        (self.workshop / "1005" / "video.mp4").unlink()
        (self.workshop / "1006" / "video.mp4").write_bytes(b"")
        wallpaper(self.reserve, "renamed-copy", "1007")
        (self.workshop / "1008").mkdir()  # shader leftovers
        rows = engine.scan(str(self.reserve), **self.options).rows
        self.assertEqual([Path(r.folder).name for r in rows], ["1001"])
        self.assertEqual((rows[0].author, rows[0].kind), ("Mira", "video"))
        self.assertEqual(rows[0].size, sum(p.stat().st_size for p in self.source.iterdir()))
        other = self.root / "another-reserve"
        other.mkdir()
        self.assertIn("1007", [Path(r.folder).name for r in engine.scan(str(other), **self.options).rows])

    def test_missing_reserve_and_steam_errors_never_become_an_empty_success(self):
        with self.assertRaises(ValueError):
            engine.scan("", **self.options)
        with self.assertRaises(OSError):
            engine.scan(str(self.root / "missing"), **self.options)
        with patch.object(self.ugc, "state", side_effect=UgcError("Steam offline")):
            with self.assertRaisesRegex(UgcError, "offline"):
                engine.scan(str(self.reserve), **self.options)
        with patch.object(Client, "details", side_effect=SteamError("offline")):
            result = engine.scan(str(self.reserve), **self.options)
        self.assertEqual(len(result.rows), 1)
        self.assertIn("author", result.warning.lower())

    def test_copy_retains_subscription_and_source_with_byte_progress_and_no_overwrite(self):
        before = {p.name: p.read_bytes() for p in self.source.iterdir()}
        progress = []
        target = engine.copy_to_reserve(str(self.source), str(self.reserve), ugc=self.ugc,
                                        on_progress=lambda done, total: progress.append((done, total)))
        self.assertEqual({p.name: p.read_bytes() for p in Path(target).iterdir()}, before)
        self.assertEqual({p.name: p.read_bytes() for p in self.source.iterdir()}, before)
        self.assertEqual(progress[-1], (sum(map(len, before.values())),) * 2)
        self.assertTrue(all(a[0] <= b[0] for a, b in zip(progress, progress[1:])))
        with self.assertRaises(FileExistsError):
            engine.copy_to_reserve(str(self.source), str(self.reserve), ugc=self.ugc)
        self.assertEqual(list(self.root.glob(".toolkit-reserve-*")), [])

    def test_revalidation_and_failure_cleanup(self):
        self.ugc.flags["1001"] = 4
        with self.assertRaises(ValueError):
            engine.copy_to_reserve(str(self.source), str(self.reserve), ugc=self.ugc)
        self.ugc.flags["1001"] = 5
        for options in (dict(cancelled=lambda: True), {}):
            with patch.object(file_copy, "verify_copy", side_effect=OSError("verify failed")):
                with self.assertRaises((OSError, InterruptedError)):
                    engine.copy_to_reserve(str(self.source), str(self.reserve), ugc=self.ugc, **options)
            self.assertTrue(self.source.exists())
            self.assertEqual(list(self.reserve.iterdir()), [])
            self.assertEqual(list(self.root.glob(".toolkit-reserve-*")), [])
        wallpaper(self.reserve, "renamed", "1001")
        with self.assertRaises(FileExistsError):
            engine.copy_to_reserve(str(self.source), str(self.reserve), ugc=self.ugc)

    def test_changed_project_type_is_rejected_before_copy(self):
        project = json.loads((self.source / "project.json").read_text())
        project["type"] = "screensaver"
        (self.source / "project.json").write_text(json.dumps(project))
        with self.assertRaisesRegex(ValueError, "wallpaper"):
            engine.copy_to_reserve(str(self.source), str(self.reserve), ugc=self.ugc)
        self.assertEqual(list(self.reserve.iterdir()), [])

    def test_stop_after_item_details_does_not_start_profile_requests(self):
        stopped = threading.Event()
        client = Client()
        def details(ids, **options):
            stopped.set()
            return Client.details(client, ids)
        with patch.object(client, "details", side_effect=details), \
             patch.object(client, "profiles") as profiles:
            with self.assertRaises(InterruptedError):
                engine.scan(str(self.reserve), **{**self.options, "client": client}, cancelled=stopped.is_set)
            profiles.assert_not_called()

    def test_stop_during_indexing_is_cancellation_instead_of_failure(self):
        stopped = threading.Event()
        def refresh(*args, **kwargs):
            stopped.set()
            return SimpleNamespace(missing=[], complete=False)
        with patch.object(engine.LibraryIndex, "refresh", side_effect=refresh):
            with self.assertRaises(InterruptedError):
                engine.reserve_ids(str(self.reserve), cancelled=stopped.is_set)

    def test_payload_escape_is_invalid(self):
        (self.root / "outside.mp4").write_bytes(b"not wallpaper content")
        for payload in ("../outside.mp4", str(self.root / "outside.mp4"), "", "video.mp4/../absent"):
            (self.source / "project.json").write_text(json.dumps({"file": payload, "type": "video"}))
            self.assertEqual(engine.scan(str(self.reserve), **self.options).rows, [])

    def test_stop_during_copy_and_publish_collision_leave_no_partial_output(self):
        stopped = threading.Event()
        def progress(done, total):
            if done:
                stopped.set()
        with self.assertRaises(InterruptedError):
            engine.copy_to_reserve(str(self.source), str(self.reserve), ugc=self.ugc,
                                   cancelled=stopped.is_set, on_progress=progress)
        self.assertTrue(self.source.exists())
        self.assertEqual(list(self.reserve.iterdir()), [])
        self.assertEqual(list(self.root.glob(".toolkit-reserve-*")), [])
        def collide(done, total):
            target = self.reserve / "1001"
            if not target.exists():
                target.mkdir()
                (target / "keep.txt").write_text("keep")
        with self.assertRaises(FileExistsError):
            engine.copy_to_reserve(str(self.source), str(self.reserve), ugc=self.ugc, on_progress=collide)
        self.assertEqual((self.reserve / "1001" / "keep.txt").read_text(), "keep")
        self.assertEqual(list(self.root.glob(".toolkit-reserve-*")), [])

    def test_steam_connections_cannot_shut_down_another_tools_connection(self):
        lib = SimpleNamespace(SteamAPI_InitFlat=Mock(return_value=0), SteamAPI_Shutdown=Mock(),
                              SteamAPI_SteamUGC_v020=Mock(return_value=123))
        with patch("app.engines.steam_ugc.find_steam_api", return_value=self.root / "steam_api64.dll"), \
             patch("app.engines.steam_ugc.ctypes.CDLL", return_value=lib), \
             patch.object(SteamUgc, "_bind"):
            first, second = SteamUgc(), SteamUgc()
            first.connect()
            entered, opened = threading.Event(), threading.Event()
            def connect():
                entered.set()
                with second:
                    opened.set()
            thread = threading.Thread(target=connect, daemon=True)
            thread.start()
            self.assertTrue(entered.wait(1))
            try:
                self.assertFalse(opened.wait(.1), "A second tool opened the process-wide Steam API")
                self.assertEqual(lib.SteamAPI_Shutdown.call_count, 0)
            finally:
                first.close()
                thread.join(2)
            self.assertTrue(opened.is_set())
            self.assertEqual(lib.SteamAPI_Shutdown.call_count, 2)
            lib.SteamAPI_InitFlat.side_effect = UgcError("offline")
            with self.assertRaises(UgcError):
                SteamUgc().connect()
            lib.SteamAPI_InitFlat.side_effect = None
            opened.clear()
            thread = threading.Thread(target=connect, daemon=True)
            thread.start()
            thread.join(2)
            self.assertTrue(opened.is_set(), "A failed connection kept the Steam lock")

    def test_stop_interrupts_wait_for_another_tools_steam_connection(self):
        lib = SimpleNamespace(SteamAPI_InitFlat=Mock(return_value=0), SteamAPI_Shutdown=Mock(),
                              SteamAPI_SteamUGC_v020=Mock(return_value=123))
        with patch("app.engines.steam_ugc.find_steam_api", return_value=self.root / "steam_api64.dll"), \
             patch("app.engines.steam_ugc.ctypes.CDLL", return_value=lib), \
             patch.object(SteamUgc, "_bind"):
            stopped = threading.Event()
            try:
                second = SteamUgc(cancelled=stopped.is_set)
            except TypeError:
                self.fail("Steam connections cannot yet cancel while waiting")
            errors = []
            def connect():
                try:
                    with second:
                        self.fail("Stopped connection must not initialize Steam")
                except InterruptedError as error:
                    errors.append(error)
            first = SteamUgc().connect()
            try:
                thread = threading.Thread(target=connect, daemon=True)
                thread.start()
                stopped.set()
                thread.join(1)
                self.assertFalse(thread.is_alive(), "Stop is waiting for the other tool to release Steam")
                self.assertEqual(len(errors), 1)
                self.assertEqual(lib.SteamAPI_InitFlat.call_count, 1)
            finally:
                first.close()

    def test_page_selection_totals_and_partial_batch_failure_on_a_worker(self):
        svc = services.Services(data_dir=self.root / "data")
        services.install(svc)
        page = ReservePage(Settings({}), svc, Config(source=str(self.reserve), destination=""))
        row = engine.Wallpaper(str(self.source), "Garden", author="Mira", kind="video", size=300)
        other = engine.Wallpaper(str(self.workshop / "1002"), "Other", size=200)
        page._scanned(engine.Scan([row, other], 2))
        self.assertIn("500 B", page.footer.text())
        page.select_all()
        self.assertEqual(len(page.selected), 2)
        self.assertTrue(page.copy_selected.isEnabled())
        self.assertEqual(page.model.cell(row, 2), "Mira")
        page.table.setCurrentIndex(page.model.index(0, 0))
        QTest.keyClick(page.table, Qt.Key_Space)
        self.assertEqual(len(page.selected), 1)
        QTest.keyClick(page.table, Qt.Key_A, Qt.ControlModifier)
        self.assertEqual(len(page.selected), 2)
        threads = []
        def copy(folder, reserve, **options):
            threads.append(threading.get_ident())
            options["on_progress"](100, 300)
            if folder == other.folder:
                raise OSError("disk full")
            return str(self.reserve / "1001")
        with patch.object(engine, "copy_to_reserve", side_effect=copy):
            self.assertTrue(page.copy_all())
            self.assertFalse(page.copy_all())
            self.assertTrue(wait_for(lambda: not page.acting))
        self.assertEqual(page.model.items(), [other])
        self.assertIn("disk full", other.note)
        self.assertTrue(all(t != threading.get_ident() for t in threads))
        self.assertEqual(svc.jobs.last_finished(page.key).result, "problems")
        page.deleteLater()
        services.install(None)

    def test_scan_is_lazy_and_failed_refresh_keeps_previous_rows(self):
        page = ReservePage(Settings({}), config=Config(source=str(self.reserve), destination=""))
        with patch.object(engine, "scan", return_value=engine.Scan([], 1)) as scan:
            qt.processEvents()
            scan.assert_not_called()
            page.on_shown()
            self.assertTrue(wait_for(lambda: not page.scanning))
            page.on_shown()
            self.assertEqual(scan.call_count, 1)
        row = engine.Wallpaper(str(self.source), "Garden", size=300)
        page._scanned(engine.Scan([row], 1))
        page._scanned(UgcError("Steam offline"))
        self.assertEqual(page.model.items(), [row])
        self.assertIn("offline", page.problem.body())
        page.deleteLater()

    def test_reserve_copies_exclude_other_reserve_mutations_and_lock_settings(self):
        svc = services.Services(data_dir=self.root / "data")
        page = ReservePage(Settings({}), svc, Config(source=str(self.reserve), destination=""))
        row = engine.Wallpaper(str(self.source), "Garden", size=300)
        page._scanned(engine.Scan([row], 1))
        for tool in ("rotator", "cleaner"):
            job = svc.jobs.start(tool, "Working")
            with patch.object(engine, "copy_to_reserve") as copy:
                self.assertFalse(page.copy_all())
                copy.assert_not_called()
            job.finish("clean", "Done")
        job = svc.jobs.start("reserve", "Copying")
        rotator = SimpleNamespace(state="idle", _starting=False, _services=svc)
        cleaner = SimpleNamespace(services=svc, window=lambda: SimpleNamespace(pages={}))
        settings = SimpleNamespace(_services=svc)
        self.assertTrue(RotatorPage._busy(rotator))
        self.assertTrue(CleanerPage._rotating(cleaner))
        self.assertTrue(SettingsPage._rotator_busy(settings))
        job.finish("clean", "Done")
        self.assertFalse(RotatorPage._busy(rotator))
        page.deleteLater()

    def test_progress_burst_keeps_only_the_latest_update_instead_of_queuing_every_chunk(self):
        calls = []
        original = ReservePage._copying_progress
        def display(page, *values):
            calls.append(values)
            return original(page, *values)
        with patch.object(ReservePage, "_copying_progress", display):
            page = ReservePage(Settings({}), config=Config(source=str(self.reserve), destination=""))
            row = engine.Wallpaper(str(self.source), "Garden", size=2000)
            page._scanned(engine.Scan([row], 1))
            def copy(folder, reserve, **options):
                for done in range(2000):
                    options["on_progress"](done, 2000)
                return str(self.reserve / "1001")
            with patch.object(engine, "copy_to_reserve", side_effect=copy):
                self.assertTrue(page.copy_all())
                self.assertTrue(wait_for(lambda: not page.acting))
        self.assertLessEqual(len(calls), 3, "Worker progress flooded the GUI event queue")
        self.assertEqual(calls[-1][-2:], (2000, 2000))
        page.deleteLater()

    def test_repeated_identical_plan_updates_do_not_recreate_widgets(self):
        steps = [("Keep", "Originals remain", "pending"), ("Copy", "Waiting", "pending")]
        panel = StepList(steps)
        originals = [row[0] for row in panel._rows]
        for _ in range(100):
            panel.set_steps(steps)
        self.assertEqual([row[0] for row in panel._rows], originals,
                         "Progress updates rebuilt a hidden page's unchanged plan")
        panel.set_step(0, caption="Changed")
        panel.set_steps(steps)
        self.assertEqual(panel.caption(0), "Originals remain")
        panel.deleteLater()

    def test_copy_yields_cpu_and_io_priority_and_restores_it_on_errors(self):
        with patch.object(we_memory, "background_mode", return_value=True) as priority, \
             patch.object(file_copy, "manifest", side_effect=OSError("unreadable")):
            with self.assertRaisesRegex(OSError, "unreadable"):
                file_copy.copy_to_reserve(str(self.source), str(self.reserve))
        self.assertEqual([c.args for c in priority.call_args_list], [(True,), (False,)])

    def test_copy_does_not_add_artificial_delays(self):
        (self.source / "video.mp4").write_bytes(b"x" * (2 * 1024 * 1024))
        now = [0.0]
        def sleep(seconds):
            now[0] += seconds
        with patch.object(file_copy.time, "monotonic", side_effect=lambda: now[0]), \
             patch.object(file_copy.time, "sleep", side_effect=sleep):
            target = file_copy.copy_to_reserve(str(self.source), str(self.reserve))
        self.assertEqual((Path(target) / "video.mp4").read_bytes(), b"x" * (2 * 1024 * 1024))
        self.assertEqual(now[0], 0, "Copying added a deliberate delay instead of using available throughput")

    def test_nested_background_work_does_not_restore_priority_too_early(self):
        self.assertTrue(hasattr(file_copy, "background_io"))
        background = [False]
        def mode(on):
            changed = on != background[0]
            background[0] = on
            return changed
        with patch.object(we_memory, "background_mode", side_effect=mode):
            with file_copy.background_io():
                self.assertTrue(background[0])
                with file_copy.background_io():
                    self.assertTrue(background[0])
                self.assertTrue(background[0], "An inner operation restored priority while copying continues")
            self.assertFalse(background[0])

    def test_plan_validation_is_not_skipped_after_invalid_input(self):
        panel = StepList()
        for _ in range(2):
            with self.assertRaises(ValueError):
                panel.set_steps([("Copy", "", "invalid")])
        panel.deleteLater()


if __name__ == "__main__":
    unittest.main()
