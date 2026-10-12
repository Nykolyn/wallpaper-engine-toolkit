"""Temporary-library migration checks; never access real wallpapers or settings."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
IMPORT_DATA = tempfile.TemporaryDirectory(prefix="multiplier-import-")
os.environ["WALLPAPER_TOOLKIT_DATA"] = IMPORT_DATA.name

from app.engines import multiplier_migration as migration
from app.engines.wallpaper_timer import (StateMonitor, StateSection, read_state_file,
                                        write_state_file, STATE_FILE)


class Store:
    """The public store contract, independent of its file-format implementation."""
    def __init__(self, path):
        self.path = Path(path)
        self.reload()

    def initialize(self, runs, current_names, multipliers):
        self.path.write_text(json.dumps({"multipliers": {**self.data.get("multipliers", {}), **multipliers},
                                        "current": list(current_names)}),
                             encoding="utf-8")
        self.reload()

    def reload(self):
        self.data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix="multiplier-migration-")
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.reserve, self.current, self.copies = [self.root / n for n in ("reserve", "current", "copies")]
        for root in (self.reserve, self.current, self.copies):
            root.mkdir()
        self.store = Store(self.root / "multipliers.json")
        self.settings = self.root / "suite.json"
        self.settings.write_bytes(b'{"copier":{"dest":"old"}}')

    def wallpaper(self, root, name, content=b"wallpaper"):
        path = root / name
        path.mkdir()
        (path / "project.json").write_bytes(b'{"file":"clip.mp4","title":"Test"}')
        (path / "clip.mp4").write_bytes(content)
        (path / "empty").mkdir()
        return path

    def family(self, count=3):
        original = self.wallpaper(self.reserve, "cloud")
        for n in range(1, count + 1):
            shutil.copytree(original, self.reserve / f"cloud_copy{n}")
        return original

    def plan(self):
        return migration.plan_migration([self.reserve, self.current, self.copies], self.current)

    def apply(self, plan=None, **kwargs):
        return migration.apply_migration(plan or self.plan(), self.store,
                                         settings_paths=[self.settings],
                                         backup_root=self.root / "backups", **kwargs)

    def manifest(self):
        return next((self.root / "backups").glob("*/manifest.json"))

    def test_original_and_three_copies_make_four_and_one_recovery_payload(self):
        self.family()
        plan = self.plan()
        self.assertEqual(plan.copy_count, 3)
        self.assertEqual(plan.groups[0].multiplier, 4)
        manifest = self.apply(plan)
        self.assertEqual(self.store.data["multipliers"], {"cloud": 4})
        self.assertEqual(sorted(p.name for p in self.reserve.iterdir()), ["cloud"])
        data = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "complete")
        self.assertEqual(sum(Path(e["quarantine"]).exists() for e in data["sources"]), 1)
        self.assertTrue((manifest.parent / "before-0").is_file())
        migration.resume_migration(manifest, self.store)
        self.assertEqual(self.store.data["multipliers"]["cloud"], 4)
        migration.rollback_migration(manifest, self.store)
        self.assertEqual(sorted(p.name for p in self.reserve.iterdir()),
                         ["cloud", "cloud_copy1", "cloud_copy2", "cloud_copy3"])
        self.assertFalse(self.store.path.exists())
        migration.rollback_migration(manifest, self.store)

    def test_holes_and_chained_copy_numbers_count_real_folders(self):
        original = self.family(0)
        for name in ("cloud_copy4", "cloud_copy91_copy3"):
            shutil.copytree(original, self.copies / name)
        self.assertEqual(self.plan().groups[0].multiplier, 3)

    def test_edited_missing_and_unplayable_copies_are_preserved(self):
        self.family(1)
        self.wallpaper(self.reserve, "cloud_copy9", b"edited")
        self.wallpaper(self.copies, "orphan_copy2")
        broken = self.wallpaper(self.copies, "cloud_copy7")
        (broken / "project.json").unlink()
        plan = self.plan()
        self.assertEqual(plan.groups[0].multiplier, 2)
        self.assertEqual(len(plan.preserved), 3)
        self.apply(plan)
        self.assertTrue((self.reserve / "cloud_copy9").is_dir())
        self.assertTrue((self.copies / "orphan_copy2").is_dir())
        self.assertTrue(broken.is_dir())

    def test_active_copy_retains_active_original_and_protection(self):
        original = self.family(0)
        active = self.current / "[protected] cloud_copy2"
        shutil.copytree(original, active)
        shutil.copytree(original, self.copies / "cloud_copy9")
        plan = self.plan()
        self.assertEqual(plan.groups[0].survivor, str(self.current / "[protected] cloud"))
        manifest = self.apply(plan)
        self.assertTrue((self.current / "[protected] cloud").is_dir())
        self.assertFalse(original.exists())
        self.assertEqual(self.store.data["current"], ["[protected] cloud"])
        self.assertEqual(self.store.data["multipliers"], {"[protected] cloud": 3})
        migration.rollback_migration(manifest, self.store)
        self.assertTrue(original.is_dir())
        self.assertTrue(active.is_dir())
        self.assertFalse((self.current / "[protected] cloud").exists())

    def test_config_and_binary_deck_are_remapped_and_deduplicated(self):
        original = self.family(0)
        active = self.current / "cloud_copy1"
        shutil.copytree(original, active)
        first = str(original / "clip.mp4").replace("\\", "/")
        copied = str(active / "clip.mp4").replace("\\", "/")
        unrelated = "W:/workshop/42/video.mp4"
        self.config = self.root / "engine" / "config.json"
        self.config.parent.mkdir()
        config = {"user": {"general": {"playlists": [{"name": "Fav", "items": [first, copied, unrelated]}],
                  "wallpaperconfig": {"selectedwallpapers": {"Monitor0": {"file": copied,
                    "playlist": {"items": [copied, first, unrelated]}}}}}}}
        self.config.write_text(json.dumps(config), encoding="utf-8")
        state_path = self.config.parent / STATE_FILE
        state_path.parent.mkdir()
        before = write_state_file([StateSection("wallpaperconfig", [
            StateMonitor("Monitor0", 17, copied, [first, copied, unrelated], 9, [1, 2, 3])])])
        state_path.write_bytes(before)
        old_config = self.config.read_bytes()
        manifest = self.apply(config_path=self.config)
        survivor = str(self.current / "cloud" / "clip.mp4").replace("\\", "/")
        general = json.loads(self.config.read_text(encoding="utf-8"))["user"]["general"]
        self.assertEqual(general["playlists"][0]["items"], [survivor, unrelated])
        self.assertEqual(general["wallpaperconfig"]["selectedwallpapers"]["Monitor0"]["file"], survivor)
        deck = read_state_file(state_path.read_bytes())[0].monitors[0]
        self.assertEqual((deck.current, deck.waiting, deck.instance, deck.mark, deck.marks),
                         (survivor, [unrelated], 17, 9, [3]))
        migration.rollback_migration(manifest, self.store)
        self.assertEqual(self.config.read_bytes(), old_config)
        self.assertEqual(state_path.read_bytes(), before)

    def test_cancellation_after_quarantine_rolls_back_without_loss(self):
        self.family(3)
        canceled = False
        save = migration._save
        def checkpoint(path, data):
            nonlocal canceled
            save(path, data)
            if data["status"] == "applying" and any(Path(e["quarantine"]).exists() for e in data["sources"]):
                canceled = True
        with patch.object(migration, "_save", checkpoint):
            with self.assertRaises(migration.MigrationCancelled):
                self.apply(cancelled=lambda: canceled)
        self.assertEqual(len(list(self.reserve.iterdir())), 4)
        self.assertFalse(self.store.path.exists())
        self.assertEqual(json.loads(self.manifest().read_text())["status"], "rolled-back")

    def test_failed_survivor_copy_preserves_source_and_resumes(self):
        original = self.family(0)
        active = self.current / "cloud_copy1"
        shutil.copytree(original, active)
        def fail(source, target):
            Path(target).mkdir()
            (Path(target) / "partial").write_bytes(b"partial")
            raise OSError("disk full")
        with patch.object(migration.shutil, "copytree", fail):
            with self.assertRaises(OSError):
                self.apply()
        self.assertTrue(original.is_dir())
        self.assertTrue(active.is_dir())
        self.assertFalse(self.store.path.exists())
        migration.resume_migration(self.manifest(), self.store)
        self.assertEqual(self.store.data["multipliers"], {"cloud": 2})
        self.assertTrue((self.current / "cloud").is_dir())

    def test_interrupted_publish_resumes_without_double_count(self):
        self.family(3)
        replace = migration._replace
        failed = False
        def publish(path, data):
            nonlocal failed
            replace(path, data)
            if path == self.store.path and not failed:
                failed = True
                raise OSError("crash just after publishing")
        with patch.object(migration, "_replace", publish):
            with self.assertRaises(OSError):
                self.apply()
        migration.resume_migration(self.manifest(), self.store)
        self.assertEqual(self.store.data["multipliers"], {"cloud": 4})
        self.assertEqual(len(list(self.reserve.iterdir())), 1)

    def test_interrupted_copy_removal_resumes_and_rollback_restores_all(self):
        self.family(3)
        remove = migration._remove
        failed = False
        def deleting(path, parent):
            nonlocal failed
            remove(path, parent)
            if not failed:
                failed = True
                raise OSError("crash just after removing redundant payload")
        with patch.object(migration, "_remove", deleting):
            with self.assertRaises(OSError):
                self.apply()
        manifest = self.manifest()
        self.assertEqual(json.loads(manifest.read_text())["status"], "finalizing")
        migration.resume_migration(manifest, self.store)
        migration.rollback_migration(manifest, self.store)
        self.assertEqual(len(list(self.reserve.iterdir())), 4)
        for path in self.reserve.iterdir():
            self.assertEqual((path / "clip.mp4").read_bytes(), b"wallpaper")

    def test_cancel_resumed_finalization_restores_pre_migration_folders(self):
        self.family(3)
        with patch.object(migration, "_remove", side_effect=OSError("interrupted finalize")):
            with self.assertRaises(OSError):
                self.apply()
        with self.assertRaises(migration.MigrationCancelled):
            migration.resume_migration(self.manifest(), self.store, cancelled=lambda: True)
        self.assertEqual(len(list(self.reserve.iterdir())), 4)
        self.assertFalse(self.store.path.exists())

    def test_edited_preview_or_preferences_never_overwrite_user_changes(self):
        self.family(1)
        plan = self.plan()
        (self.reserve / "cloud_copy1" / "clip.mp4").write_bytes(b"edit")
        with self.assertRaisesRegex(ValueError, "changed after preview"):
            self.apply(plan)
        self.assertFalse((self.root / "backups").exists())

    def test_invalid_binary_state_fails_before_quarantine(self):
        self.family(1)
        config = self.root / "engine" / "config.json"
        config.parent.mkdir()
        config.write_bytes(b'{}')
        state = config.parent / STATE_FILE
        state.parent.mkdir()
        state.write_bytes(b"broken")
        with self.assertRaises(ValueError):
            self.apply(config_path=config)
        self.assertEqual(len(list(self.reserve.iterdir())), 2)
        self.assertFalse(self.store.path.exists())
        with self.assertRaisesRegex(ValueError, "unfinished"):
            self.apply()
        migration.rollback_migration(self.manifest(), self.store)

    def test_rollback_refuses_edits_to_survivor(self):
        original = self.family(1)
        manifest = self.apply()
        (original / "clip.mp4").write_bytes(b"new user work")
        with self.assertRaisesRegex(ValueError, "discard edits"):
            migration.rollback_migration(manifest, self.store)
        self.assertEqual((original / "clip.mp4").read_bytes(), b"new user work")

    def test_unrelated_multipliers_survive_later_migration(self):
        self.store.initialize([], [], {"another": 20})
        self.family(2)
        self.apply()
        self.assertEqual(self.store.data["multipliers"], {"another": 20, "cloud": 3})
        with self.assertRaisesRegex(ValueError, "no verified copy groups"):
            self.apply()
        self.assertEqual(self.store.data["multipliers"], {"another": 20, "cloud": 3})

    def test_real_quota_store_counts_active_survivor_once_and_preserves_history(self):
        from app.engines.multipliers import MultiplierStore
        from app.engines.rotator.config import RunRecord
        self.store = MultiplierStore(self.store.path)
        self.store.set_multiplier("unrelated", 20)
        original = self.family(1)
        active = self.current / "cloud_copy2"
        shutil.copytree(original, active)
        runs = [RunRecord("previous", "2026-10-12 12:00:00", moved=["cloud_copy1", "cloud_copy2"])]
        manifest = self.apply(runs=runs)
        usage = self.store.usage(runs)
        self.assertEqual(usage.multiplier("cloud"), 3)
        self.assertEqual(usage.remaining("cloud"), 2)
        self.assertEqual(usage.multiplier("unrelated"), 20)
        migration.resume_migration(manifest, self.store)
        self.assertEqual(self.store.usage(runs).remaining("cloud"), 2)
        self.assertEqual(runs[0].moved, ["cloud_copy1", "cloud_copy2"])

    def test_interrupted_rollback_copy_uses_staging_and_can_retry(self):
        self.family(3)
        manifest = self.apply()
        def failed(source, target):
            Path(target).mkdir()
            (Path(target) / "partial").write_bytes(b"partial")
            raise OSError("crash during rollback")
        with patch.object(migration.shutil, "copytree", failed):
            with self.assertRaises(OSError):
                migration.rollback_migration(manifest, self.store)
        self.assertEqual(len(list(self.reserve.iterdir())), 1)
        migration.rollback_migration(manifest, self.store)
        self.assertEqual(len(list(self.reserve.iterdir())), 4)

    def test_project_or_playlist_traversal_is_rejected(self):
        self.family(1)
        original = self.reserve / "cloud"
        mapping = {migration._key(original): str(self.current / "cloud")}
        with self.assertRaisesRegex(ValueError, "escapes"):
            migration.remap_config({"items": [str(original / ".." / "other" / "media.mp4")]}, mapping)

    def test_corrupt_store_blocks_before_any_library_changes(self):
        self.family(1)
        self.store.path.write_bytes(b"broken json")
        with self.assertRaises(ValueError):
            self.apply()
        self.assertEqual(len(list(self.reserve.iterdir())), 2)
        self.assertFalse((self.root / "backups").exists())

    def test_linked_copy_is_preserved(self):
        original = self.family(0)
        link = self.reserve / "cloud_copy2"
        try:
            link.symlink_to(original, target_is_directory=True)
        except OSError:
            self.skipTest("Creating links requires Windows Developer Mode or privilege")
        plan = self.plan()
        self.assertFalse(plan.groups)
        self.assertTrue(any("Link" in reason or "Linked" in reason for reason in plan.preserved))
        self.assertTrue(link.is_symlink())

    def test_junction_detection_is_checked_without_following_it(self):
        self.family(1)
        linked = self.reserve / "cloud_copy1"
        original = migration._linked
        with patch.object(migration, "_linked", lambda p: p == linked or original(p)):
            plan = self.plan()
        self.assertFalse(plan.groups)
        self.assertTrue(any("Linked" in reason for reason in plan.preserved))
        self.assertTrue(linked.is_dir())


if __name__ == "__main__":
    unittest.main(verbosity=2)
