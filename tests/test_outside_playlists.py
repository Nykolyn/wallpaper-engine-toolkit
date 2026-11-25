"""The Cleaner's outside-playlists scan, on a fabricated Workshop folder and config.json."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

scratch = tempfile.TemporaryDirectory(prefix="outside-test-")
TMP = Path(scratch.name)
os.environ["WALLPAPER_TOOLKIT_DATA"] = str(TMP / "data")
(TMP / "data").mkdir()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.engines import outside_playlists as engine


def workshop(root: Path, *ids: str) -> Path:
    content = root / "workshop" / "content" / "431960"
    for item in ids:
        (content / item).mkdir(parents=True)
        (content / item / "project.json").write_text(json.dumps({"title": f"Wall {item}",
                                                                  "type": "video"}), encoding="utf-8")
    return content


def config(root: Path, data: dict) -> Path:
    path = root / "config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def item(content: Path, wallpaper: str) -> str:
    return f"{content.as_posix()}/{wallpaper}/project.json"


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(dir=TMP))

    def test_scan_lists_only_unplaylisted_subscribed(self):
        content = workshop(self.root, "1", "2", "3", "4")
        path = config(self.root, {
            "?installdirectory": "X:/we",
            "alice": {"general": {
                "playlists": [{"name": "all", "items": [item(content, "1")]}],
                "browser": {"folders": [{"title": "all", "items": {"1": 1, "3": 1, "4": 1, "5": 1}}]},
                "wallpaperconfig": {"selectedwallpapers": {
                    "Monitor1": {"file": item(content, "3")}}}}},
            "bob": {"general": {"wallpaperconfig": {"selectedwallpapers": {
                "Monitor2": {"playlist": {"name": "x", "items": [item(content, "2")]}}}}}},
        })
        result = engine.scan(config_path=path, workshop=content, cache_path=self.root / "none.sqlite")
        self.assertEqual([Path(r.folder).name for r in result.rows], ["3", "4"])
        self.assertEqual(result.rows[0].title, "Wall 3")
        self.assertIn("5", result.folders["all"])
        self.assertIsNotNone(result.saved)

    def test_item_path_forms(self):
        path = config(self.root, {"u": {"general": {"playlists": [{"items": [
            "X:/a/431960/7/project.json", "X:\\a\\431960\\8\\scene.pkg", "myprojects/9/project.json"]}]}}})
        self.assertEqual(engine.playlisted_ids(path), {"7", "8"})

    def test_item_id_uses_the_workshop_segment_not_an_earlier_431960(self):
        path = config(self.root, {"u": {"general": {"playlists": [{"items": [
            "D:/431960/steamapps/workshop/content/431960/123/scene.pkg",
            "X:/431960/55/steamapps/workshop/content/431960/124/project.json"]}]}}})
        self.assertEqual(engine.playlisted_ids(path), {"123", "124"})

    def test_unreadable_config_raises(self):
        with self.assertRaises(OSError):
            engine.playlisted_ids(self.root / "missing.json")
        broken = self.root / "broken.json"
        broken.write_text("{", encoding="utf-8")
        with self.assertRaises(OSError):
            engine.playlisted_ids(broken)

    def test_default_folder(self):
        self.assertEqual(engine.default_folder("all", "folder:new", ["all", "new"]), "all")
        self.assertEqual(engine.default_folder("gone", "folder:new", ["new"]), "new")
        self.assertEqual(engine.default_folder(None, "@folders", ["new"]), "")
        self.assertEqual(engine.default_folder(None, "folder:x", []), "")
        self.assertEqual(engine.default_folder("", "folder:new", ["new"]), "")


if __name__ == "__main__":
    unittest.main()
