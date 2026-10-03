"""Copier filesystem guarantees. Every write stays in temporary test directories."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from collections import namedtuple
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.engines.copier import CopyEngine, CopyJob, inspect_queue, parse_line


class CopierTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dest = self.root / "output"
        self.dest.mkdir()
        self.a = self.source("a")
        self.b = self.source("b")

    def source(self, name):
        folder = self.root / name
        (folder / "empty").mkdir(parents=True)
        (folder / "nested").mkdir()
        (folder / "nested" / "one").write_bytes(b"one" * 20)
        (folder / "two").write_bytes(b"two" * 10)
        return folder

    def run_engine(self, jobs, *, engine=None, verify=True):
        engine = engine or CopyEngine()
        self.assertTrue(engine.start(jobs, str(self.dest), verify=verify))
        engine._thread.join(5)
        self.assertFalse(engine.is_running())
        self.assertFalse(engine._thread.is_alive())
        return engine

    def test_numbering_uses_highest_and_never_overwrites(self):
        (self.dest / "a_copy1").mkdir()
        (self.dest / "a_copy7").mkdir()
        marker = self.dest / "a_copy7" / "keep"
        marker.write_text("untouched")
        run = self.run_engine([CopyJob(self.a, 2)])
        self.assertEqual(run.jobs[0].state, "done")
        self.assertTrue((self.dest / "a_copy8" / "empty").is_dir())
        self.assertEqual((self.dest / "a_copy9" / "nested" / "one").read_bytes(), b"one" * 20)
        self.assertEqual(marker.read_text(), "untouched")
        self.assertFalse((self.dest / "a_copy2").exists())

    def test_queue_order_multiple_sources_and_destinations(self):
        order = []
        alternate = self.root / "alternate"
        run = CopyEngine(log=lambda line: order.append(line))
        jobs = [CopyJob([self.a, self.b], 2, str(alternate)), CopyJob(self.a, 1)]
        self.run_engine(jobs, engine=run)
        starts = [line for line in order if line.startswith("[START]")]
        self.assertIn("a + 1 folders", starts[0])
        self.assertEqual(len(list(alternate.iterdir())), 4)
        self.assertEqual(len(list(self.dest.iterdir())), 1)
        self.assertEqual([j.state for j in run.jobs], ["done", "done"])
        self.assertEqual(sum(j.done_bytes for j in run.jobs), 450)

    def test_pause_between_files_resume_and_stop_while_paused(self):
        arrived = threading.Event()
        run = CopyEngine()
        original = run._copy_small
        calls = []
        def copying(*args):
            original(*args)
            calls.append(args)
            if len(calls) == 1:
                run.pause()
                arrived.set()
        run._copy_small = copying
        run.start([CopyJob(self.a, 2)], str(self.dest))
        self.assertTrue(arrived.wait(2))
        time.sleep(.05)
        self.assertEqual(len(calls), 1)
        self.assertTrue(run.is_paused())
        run.resume()
        run._thread.join(3)
        self.assertEqual(run.jobs[0].state, "done")
        calls.clear()
        arrived.clear()
        run.start([CopyJob(self.b, 2)], str(self.dest))
        self.assertTrue(arrived.wait(2))
        run.cancel()
        run._thread.join(3)
        self.assertEqual(run.jobs[0].state, "stopped", run.jobs[0].reason)
        self.assertFalse((self.dest / "b_copy1").exists())
        self.assertEqual((self.b / "two").read_bytes(), b"two" * 10)

    def test_verification_detects_truncated_file(self):
        run = CopyEngine()
        original = run._copy_small
        def truncated(source, dest, rel):
            original(source, dest, rel)
            Path(dest, rel).write_bytes(b"x")
        run._copy_small = truncated
        self.run_engine([CopyJob(self.a, 2), CopyJob(self.b, 1)], engine=run)
        self.assertEqual([j.state for j in run.jobs], ["failed", "failed"])
        self.assertIn("size differs", run.jobs[0].reason)
        self.assertEqual(list(self.dest.iterdir()), [])
        self.assertEqual(run.jobs[0].done_bytes, 0)

    def test_failure_isolation_retry_only_missing_copies_and_skip(self):
        run = CopyEngine()
        original = run._copy_small
        def fail_second(source, dest, rel):
            if str(dest).endswith("a_copy2"):
                raise PermissionError("Access denied by test")
            original(source, dest, rel)
        run._copy_small = fail_second
        self.run_engine([CopyJob(self.a, 3), CopyJob(self.b, 1)], engine=run)
        self.assertEqual([j.state for j in run.jobs], ["failed", "done"])
        self.assertEqual(len(run.jobs[0].completed), 1)
        self.assertIn("Access denied", run.jobs[0].reason)
        self.assertTrue(run.skip(run.jobs[0]))
        self.assertEqual(run.jobs[0].state, "skipped")
        self.assertTrue(run.retry(run.jobs[0]))
        run._copy_small = original
        self.run_engine(run.jobs, engine=run)
        self.assertEqual([j.state for j in run.jobs], ["done", "done"])
        self.assertEqual(sorted(p.name for p in self.dest.iterdir()),
                         ["a_copy1", "a_copy2", "a_copy3", "b_copy1"])
        self.assertEqual(run.jobs[0].done_bytes, 270)

    def test_retry_during_run_goes_after_waiting_job(self):
        events = []
        run = CopyEngine()
        fail_once = [True]
        original = run._copy_small
        def copying(source, dest, rel):
            if str(source) == str(self.a) and fail_once[0]:
                fail_once[0] = False
                raise OSError("temporary failure")
            original(source, dest, rel)
        def changed(row):
            if row["state"] in ("failed", "done"):
                events.append((row["name"], row["state"]))
            if row["state"] == "failed":
                self.assertTrue(run.retry(row["id"]))
        run._copy_small = copying
        run._job_changed = changed
        self.run_engine([CopyJob(self.a, 1), CopyJob(self.b, 1)], engine=run)
        self.assertEqual(events, [("a", "failed"), ("b", "done"), ("a", "done")])

    def test_large_file_stop_cleans_only_its_new_folder(self):
        updates = []
        run = CopyEngine()
        def updated(data):
            updates.append(data)
            if data["done"] > 0:
                run.cancel()
        run._updated = updated
        with patch("app.engines.copier.LARGE_FILE_THRESHOLD", 1), \
                patch("app.engines.copier.CHUNK_SIZE", 4), \
                patch("app.engines.copier.REPORT_INTERVAL", 0):
            self.run_engine([CopyJob(self.a, 1)], engine=run)
        self.assertEqual(run.jobs[0].state, "stopped", run.jobs[0].reason)
        self.assertEqual(list(self.dest.iterdir()), [])
        self.assertTrue(self.a.exists())
        self.assertEqual(run.jobs[0].done_bytes, 0)
        self.assertTrue(any(0 < u["done"] < u["total"] for u in updates))

    def test_paste_format(self):
        self.assertEqual(parse_line(r"C:\My clips\folder 5"), (os.path.normpath(r"C:\My clips\folder"), 5))
        self.assertEqual(parse_line('"C:\\My clips\\folder" 2'), (os.path.normpath(r"C:\My clips\folder"), 2))
        self.assertEqual(parse_line('"C:\\My clips\\folder 5"'), (os.path.normpath(r"C:\My clips\folder 5"), 3))
        self.assertEqual(parse_line(r"C:\My clips\folder")[1], 3)
        self.assertIsNone(parse_line(" "))
        with self.assertRaises(ValueError):
            parse_line("folder 0")
        with self.assertRaises(ValueError):
            CopyJob(self.a, 0)

    def test_free_space_aggregates_destination_filesystem(self):
        usage = namedtuple("Usage", "total used free")(1000, 900, 100)
        with patch("app.engines.copier.shutil.disk_usage", return_value=usage):
            data = inspect_queue([CopyJob(self.a, 2), CopyJob(self.b, 1, str(self.dest / "other"))], str(self.dest))
            self.assertEqual(len(data["drives"]), 1)
            self.assertEqual(data["drives"][0]["needed"], 270)
            run = self.run_engine([CopyJob(self.a, 2), CopyJob(self.b, 1)])
        self.assertEqual([j.state for j in run.jobs], ["failed", "done"])
        self.assertIn("free space", run.jobs[0].reason)

    def test_missing_source_and_nested_destination_are_failures(self):
        run = self.run_engine([CopyJob(self.root / "missing", 1),
                               CopyJob(self.a, 1, str(self.a / "output")), CopyJob(self.b, 1)])
        self.assertEqual([j.state for j in run.jobs], ["failed", "failed", "done"])
        self.assertFalse((self.a / "output").exists())

    def test_zero_byte_and_empty_folders(self):
        empty = self.root / "empty-source"
        (empty / "nested-empty").mkdir(parents=True)
        run = self.run_engine([CopyJob(empty, 1)])
        self.assertEqual(run.jobs[0].state, "done")
        self.assertEqual(run.jobs[0].done_bytes, 0)
        self.assertTrue((self.dest / "empty-source_copy1" / "nested-empty").is_dir())


if __name__ == "__main__":
    unittest.main()
