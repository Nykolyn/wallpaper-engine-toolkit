"""Tags and age survive local/Steam reads, caches, table rows and Creator builds."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["WALLPAPER_TOOLKIT_DATA"] = tempfile.mkdtemp(prefix="wallpaper_metadata_")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from app.engines import creator, wallpaper_meta as meta
from app.engines.library_index import FolderInfo, LibraryIndex, describe
from app.engines.steam_api import ItemDetails
from app.engines.unavailable import Wallpaper
from app.pages.cleaner import WallpaperModel
from app.pages.creator import CreatorPage, age_cell
from app.pages.rotator import LibraryModel
from app.pages.tracker import PlaylistModel, PlaylistRow, QUEUE
from app.settings import Settings
from app.ui.gallery import GalleryListModel, GalleryModel
from app.engines.review import Wallpaper as Reviewed
from app.ui.kit import Cell, ChipCell, TagsCell, Table

app = QApplication.instance() or QApplication([])


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.folder = self.root / "clip"
        self.folder.mkdir()
        self.video = self.folder / "clip.mp4"
        self.video.write_bytes(b"video")
        self.project = creator.build_project_json("clip.mp4", "Clip", "preview.gif", ["Nature"], "Mature")
        (self.folder / "project.json").write_text(json.dumps(self.project), encoding="utf-8")
        self.addCleanup(self.tmp.cleanup)

    def test_local_metadata_and_cache(self):
        found = meta.read_meta(str(self.video))
        self.assertEqual((found.tags, found.age), (("Nature",), "Mature"))
        info = describe(self.folder, "clip", 1)
        self.assertEqual(FolderInfo.from_json("clip", info.to_json()), info)
        index = LibraryIndex(self.root / "index.json")
        index.path.write_text(json.dumps({"format": 1, "roots": {str(self.root): {
            "complete": True, "folders": {"clip": {
                "m": int(self.folder.stat().st_mtime), "t": "old", "s": 123}}}}}), encoding="utf-8")
        index.load()
        index.refresh([str(self.root)])
        self.assertEqual(index.get(self.root, "clip").age, "Mature")
        self.assertEqual(index.get(self.root, "clip").size, 123)
        self.assertFalse(FolderInfo.from_json("clip", {"m": 1}).metadata_read)
        self.assertEqual(meta.read_tags("Anime"), ())
        self.assertEqual(meta.read_tags([None, 1, {}, " Anime ", "Anime"]), ("Anime",))
        self.assertEqual(meta.content_rating(" mature "), "Mature")
        self.assertEqual(meta.content_rating("unknown"), "")

    def test_steam_metadata_and_cache(self):
        item = ItemDetails.from_steam({"publishedfileid": "123", "tags": [
            {"tag": "Video"}, {"tag": "Nature"}, {"tag": "1920x1080"}, {"tag": "Questionable"}]})
        self.assertEqual((item.kind, item.tags, item.age), ("Video", ("Nature",), "Questionable"))
        self.assertEqual(ItemDetails.from_json(item.to_json()), item)
        self.assertEqual(ItemDetails.from_json({"id": "1", "ok": True}).age, "")

    def test_all_table_columns(self):
        row = PlaylistRow(str(self.video), QUEUE, 1)
        row.describe(meta.Described(meta.read_meta(str(self.video))))
        tracker = PlaylistModel()
        rotator = LibraryModel()
        rotator.infos["clip"] = describe(self.folder, "clip", 1)
        cleaner = WallpaperModel(None)
        cleaner.page = SimpleNamespace(scanning=False, acting=False)
        local = Wallpaper(str(self.folder), "Clip", tags=("Nature",), age="Mature")
        review = GalleryListModel(GalleryModel())
        remote = Reviewed(ItemDetails("1", True, tags=("Nature",), age="Mature"))
        for model, item in ((tracker, row), (rotator, "clip"), (cleaner, local), (review, remote)):
            titles = [column.title for column in model.columns]
            self.assertEqual(model.cell(item, titles.index("Tags")), TagsCell(("Nature",)))
            self.assertEqual(model.cell(item, titles.index("Age")), "Mature")
            model.set_rows([item])
            model.sort(titles.index("Age"), Qt.AscendingOrder)
            table = Table()
            table.setModel(model)
            table.resize(600, 300)
            table.show()
            app.processEvents()
            title = next(i for i, col in enumerate(model.columns) if col.min_width)
            self.assertGreaterEqual(table.column_spans()[title][1], model.columns[title].min_width)
            self.assertGreater(table.horizontalScrollBar().maximum(), 0)
            table.resize(1600, 300)
            app.processEvents()
            self.assertEqual(table.horizontalScrollBar().maximum(), 0)
            table.close()

    def test_creator_choices_persist_and_override(self):
        settings = Settings({})
        with patch("app.settings.SETTINGS_PATH", self.root / "suite.json"):
            page = CreatorPage(settings)
            self.assertEqual(page.batch_age, "")
            self.assertFalse(page.age_note.isHidden())
            self.assertEqual([page.age.itemData(i) for i in range(1, page.age.count())], list(meta.WE_AGES))
            page.age.setCurrentIndex(page.age.findData("Questionable"))
            restored = CreatorPage(Settings.load())
            self.assertEqual(restored.batch_age, "Questionable")
            item = creator.VideoItem(str(self.video))
            self.assertEqual(age_cell(item, page.batch_age), Cell("Questionable", "text.lo"))
            item.age = "Mature"
            self.assertEqual(creator.resolve_age(item, page.batch_age), "Mature")
            item.age = ""
            self.assertEqual(age_cell(item, page.batch_age), ChipCell("NeedsAge"))
            page.items, page.selected = [item], {item.video_path}
            page._set_state("scanned")
            page.table.setCurrentIndex(page.source_model.index(0, 6))
            QTest.keyClick(page.table, Qt.Key_Space)
            page._age_editor.setCurrentIndex(page._age_editor.findData("Mature"))
            self.assertEqual(item.age, "Mature")
            page._age_editor.hidePopup()
            item.age = None
            self.assertEqual(creator.resolve_age(item, "Everyone"), "Everyone")
            for widget in (page, restored):
                widget.close()
                widget.deleteLater()

    def test_build_writes_rating_and_unset_omits_it(self):
        item = creator.VideoItem(str(self.video))
        item.age = "Mature"
        def preview(_ffmpeg, _video, output, _duration):
            Path(output).write_bytes(b"GIF89a")
            return True
        with patch.object(creator, "probe_duration", return_value=5), patch.object(creator, "make_gif", side_effect=preview):
            result = creator.BuildEngine()._process_item(item, str(self.root / "out"), False,
                                                         "fake", ["Nature"], "Everyone")
        self.assertEqual(result["status"], "ok")
        written = json.loads((self.root / "out" / result["folder"] / "project.json").read_text(encoding="utf-8"))
        self.assertEqual(written["contentrating"], "Mature")
        self.assertTrue(self.video.exists())
        for rating in meta.WE_AGES:
            self.assertEqual(creator.build_project_json("v", "v", "p", age=rating)["contentrating"], rating)
        self.assertNotIn("contentrating", creator.build_project_json("v", "v", "p"))


if __name__ == "__main__":
    unittest.main()
