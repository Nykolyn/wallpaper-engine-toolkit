"""Building projects from videos, and the tags that go into them.

Run it directly — there is no test framework in this project:

    .venv\\Scripts\\python.exe tests\\test_creator.py

Nothing here runs ffmpeg or writes a wallpaper. What is checked is the part
that decides *what gets written*: the shape of project.json, and which tags end
up in it when a batch says one thing and a single clip says another. That
resolution is the whole of the feature and is invisible until a wallpaper shows
up in Wallpaper Engine tagged wrongly.

The widget checks need PySide6 but no windows on screen.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.engines import creator as cr                    # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="wallpaper_creator_test_"))

results: list[bool] = []


def check(label: str, condition: bool) -> None:
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label)


# ---- The tag vocabulary ----------------------------------------------------

check("the offered tags are distinct",
      len(cr.WE_TAGS) == len(set(cr.WE_TAGS)))
check("and include the ones a real library actually uses",
      {"Anime", "Game", "Unspecified", "Landscape", "Nature"} <= set(cr.WE_TAGS))
check("and the ones only Wallpaper Engine's own UI names",
      {"Guys", "MMD", "Memes", "Sports"} <= set(cr.WE_TAGS))
check("nothing in the list is blank or padded",
      all(t and t == t.strip() for t in cr.WE_TAGS))


# ---- Cleaning what a user typed --------------------------------------------

check("order is kept, because somebody chose it",
      cr.clean_tags(["Game", "Anime"]) == ["Game", "Anime"])
check("padding is trimmed", cr.clean_tags(["  Anime  "]) == ["Anime"])
check("a repeat is dropped, since a doubled tag shows twice",
      cr.clean_tags(["Anime", "Anime"]) == ["Anime"])
check("blanks never become tags", cr.clean_tags(["", "   ", "Anime"]) == ["Anime"])
check("nothing at all is an empty list, not None",
      cr.clean_tags(None) == [] and cr.clean_tags([]) == [])

# str() is not applied blindly anywhere: str(None) is "None", and a wallpaper
# tagged "None" is the kind of thing nobody notices for months.
check("a None in the list does not become the word None",
      cr.clean_tags([None, "Anime"]) == ["Anime"])
check("and neither does a number", cr.clean_tags([0, 7, "Anime"]) == ["Anime"])

check("a tag Wallpaper Engine has never heard of is still a tag",
      cr.clean_tags(["My own thing"]) == ["My own thing"])


# ---- project.json ----------------------------------------------------------

made = cr.build_project_json("clip.mp4", "clip", "preview.gif", ["Anime", "Game"])
check("the file, title and preview are where Wallpaper Engine looks",
      made["file"] == "clip.mp4" and made["title"] == "clip"
      and made["preview"] == "preview.gif")
check("it is declared a video", made["type"] == "video")
check("the chosen tags are written", made["tags"] == ["Anime", "Game"])

bare = cr.build_project_json("clip.mp4", "clip", "preview.gif")
check("with no tags chosen, none are invented",
      bare["tags"] == [])
check("which is an empty list rather than a missing key",
      "tags" in bare and isinstance(bare["tags"], list))

# This is the regression that prompted the feature: the genre was once a
# literal in this function, so every wallpaper anyone built anywhere came out
# tagged with one library's taste.
check("no tag is hard-coded into the builder",
      cr.build_project_json("a.mp4", "a", "p.gif", [])["tags"] == [])

check("what goes in is cleaned on the way",
      cr.build_project_json("a.mp4", "a", "p.gif",
                            ["  Anime ", "Anime", ""])["tags"] == ["Anime"])


# ---- A clip may disagree with its batch ------------------------------------

clip = TMP / "clip.mp4"
clip.write_bytes(b"not really a video")
item = cr.VideoItem(str(clip))

check("a new clip has no opinion of its own", item.tags is None)
check("and reads its name and size off the disk",
      item.filename == "clip" and item.size == 18 and item.valid)


def resolved(own, batch):
    """What _process_item would hand to build_project_json."""
    return batch if own is None else own


check("a clip with no opinion takes the batch's",
      resolved(item.tags, ["Anime"]) == ["Anime"])

item.tags = ["Nature"]
check("a clip that was decided about keeps its own",
      resolved(item.tags, ["Anime"]) == ["Nature"])

# The distinction that makes `None` worth having: "no tags" is a decision, and
# it has to survive the batch being given some.
item.tags = []
check("a clip deliberately left untagged stays untagged",
      resolved(item.tags, ["Anime"]) == [])
check("which is not the same as having no opinion",
      item.tags is not None)


# ---- Scanning --------------------------------------------------------------

(TMP / "b.mp4").write_bytes(b"x")
(TMP / "a.mkv").write_bytes(b"x")
(TMP / "notes.txt").write_text("not a video", encoding="utf-8")
found = cr.scan_source(str(TMP))
check("every video extension is picked up, and nothing else",
      [i.basename for i in found] == ["a.mkv", "b.mp4", "clip.mp4"])
check("a folder that is not there is empty, not an error",
      cr.scan_source(str(TMP / "nowhere")) == [])
check("and each scanned clip starts out following the batch",
      all(i.tags is None for i in found))


# ---- Naming ----------------------------------------------------------------

suffixes = {cr.generate_suffix() for _ in range(200)}
check("suffixes made in the same second are still distinct",
      len(suffixes) == 200)
check("and are made only of digits, so any filesystem takes them",
      all(s.isdigit() for s in suffixes))


# ---- The widgets that carry all this ---------------------------------------

from PySide6.QtWidgets import QApplication                # noqa: E402

_app = QApplication.instance() or QApplication([])

from app.ui.creator_tab import TagDialog, VideoCard, describe_tags   # noqa: E402

check("a tag list reads as a sentence", describe_tags(["Anime", "Game"]) == "Anime, Game")
check("and an empty one says so rather than showing nothing",
      describe_tags([], "none") == "none")

dialog = TagDialog(["Anime", "Something else"])
check("a known tag comes back ticked", dialog.boxes["Anime"].isChecked())
check("an unknown one goes in the free-text field",
      dialog.extra.text() == "Something else")
check("and both survive the round trip",
      dialog.value() == ["Anime", "Something else"])

batch_dialog = TagDialog(None, batch=["Anime"])
check("a clip set to follow the batch says so", batch_dialog.follow.isChecked())
check("and answers None rather than a list", batch_dialog.value() is None)
check("with the boxes disabled, so the two cannot be set at once",
      not batch_dialog._grid_host.isEnabled())

batch_dialog.follow.setChecked(False)
check("un-following re-enables them", batch_dialog._grid_host.isEnabled())
check("and the answer becomes a list — an empty one, having ticked nothing",
      batch_dialog.value() == [])

plain = TagDialog(["Anime"])
check("the batch's own dialog has no follow checkbox to offer",
      plain.follow is None)

fresh = cr.VideoItem(str(clip))
card = VideoCard(fresh, ["Anime", "Game"])
check("a card following the batch shows the batch's tags",
      "Anime, Game" in card.tags_btn.text())
card.set_batch_tags(["Retro"])
check("and follows it when it changes", "Retro" in card.tags_btn.text())

fresh.tags = ["Nature"]
card._refresh_tags_button()
check("a card with its own tags shows those instead",
      "Nature" in card.tags_btn.text() and "Retro" not in card.tags_btn.text())

fresh.tags = []
card._refresh_tags_button()
check("and one deliberately untagged says none, not the batch's",
      "none" in card.tags_btn.text() and "Retro" not in card.tags_btn.text())


print()
print("PASSED %d/%d" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
