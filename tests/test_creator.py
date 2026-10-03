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


# ---- Move never consumes an input after a JSON write failure ----------------

from unittest.mock import patch


def fake_gif(ffmpeg, video, output, duration=None):
    Path(output).write_bytes(b"preview")
    return True


move_source = TMP / "move-source.mp4"
move_source.write_bytes(b"original video")
move_target = TMP / "move-target"
engine = cr.BuildEngine()
with patch.object(cr, "probe_duration", return_value=10.0), \
        patch.object(cr, "make_gif", side_effect=fake_gif), \
        patch.object(cr.json, "dump", side_effect=OSError("disk full")):
    failed = engine._process_item(cr.VideoItem(str(move_source)),
                                  str(move_target), True, "fake-ffmpeg")
check("Move preserves the source when project.json fails",
      failed["status"] == "failed" and move_source.read_bytes() == b"original video")
check("Move removes the incomplete output when project.json fails",
      not list(move_target.iterdir()))


import threading
import subprocess

banner = """Input #0, mov, from 'sample.mp4':
  Duration: 01:02:03.50, start: 0.000000, bitrate: 1500 kb/s
  Stream #0:0(und): Video: h264 (High) (avc1 / 0x31637661), yuv420p, 1920x1080 [SAR 1:1 DAR 16:9], 24 fps
  Stream #0:1: Audio: aac, 48000 Hz, stereo
"""
parsed = cr.parse_probe_output(banner)
check("one ffmpeg banner yields length and video dimensions",
      parsed == dict(duration=3723.5, width=1920, height=1080, video=True))
check("audio dimensions are never video dimensions",
      cr.parse_probe_output("Stream #0:0: Audio: pcm, 1920x1080")["width"] is None)
check("an unavailable duration remains unknown",
      cr.parse_probe_output("Duration: N/A\nStream #0:0: Video: vp9, yuv420p, 3840x2160, 30 fps")["duration"] is None)
check("codec hex identifiers are not dimensions", parsed["width"] == 1920)
check("a damaged input has no video stream", not cr.parse_probe_output("Invalid data found")["video"])

read_dir = TMP / "read"
read_dir.mkdir()
for name in ("a.mp4", "b.mkv", "notes.txt", "z.webm"):
    (read_dir / name).write_bytes(b"sample")
calls = []
def fake_run(args):
    calls.append(args)
    return subprocess.CompletedProcess(args, 1, "", banner)
with patch.object(cr, "_run", side_effect=fake_run):
    streamed = []
    read = cr.read_source(str(read_dir), lambda item, n, total: streamed.append((n, total)), ffmpeg="fake")
check("read streams sorted rows including unsupported reasons",
      [i.basename for i in read] == ["a.mp4", "b.mkv", "notes.txt", "z.webm"]
      and read[2].reason.startswith("unsupported") and streamed[-1] == (4, 4))
check("read probes every supported video once", len(calls) == 3)
check("read caches metadata without future filesystem calls",
      read[0].resolution == "1920×1080" and read[0].duration == 3723.5)
stop = threading.Event()
with patch.object(cr, "_run", side_effect=fake_run):
    partial = cr.read_source(str(read_dir), lambda *_: stop.set(), stop, ffmpeg="fake")
check("cancel keeps only the rows already checked", len(partial) == 1)
check("cancel before read touches nothing", cr.read_source("missing", cancelled=stop) == [])
with patch.object(cr, "_run", return_value=subprocess.CompletedProcess([], 1, "", "Invalid data")):
    broken = cr.read_source(str(read_dir), ffmpeg="fake")
check("damaged files have an actionable reason", broken[0].reason == "could not be read — file may be damaged")

own = cr.VideoItem(str(clip))
check("NeedsTags follows an empty batch", cr.needs_tags(own, []))
check("NeedsTags resolves a nonempty batch", not cr.needs_tags(own, ["Nature"]))
own.tags = []
check("explicit none stays NeedsTags with a tagged batch", cr.needs_tags(own, ["Nature"]))
own.tags = ["Custom"]
check("own tags override an empty batch", cr.resolve_tags(own, []) == ["Custom"])

copy_target = TMP / "copy-target"
with patch.object(cr, "probe_duration", return_value=10), patch.object(cr, "make_gif", side_effect=fake_gif):
    copied = engine._process_item(cr.VideoItem(str(clip)), str(copy_target), False, "fake")
check("Copy verifies a complete wallpaper and leaves input",
      copied["status"] == "ok" and clip.exists() and Path(copied["preview_path"]).exists())
with patch.object(cr, "probe_duration", return_value=10), patch.object(cr, "make_gif", side_effect=fake_gif), \
        patch.object(cr.BuildEngine, "_verify_project", side_effect=OSError("size mismatch")):
    failed_verify = engine._process_item(cr.VideoItem(str(move_source)), str(move_target), True, "fake")
check("Move preserves input after failed verification", move_source.exists() and "size mismatch" in failed_verify["reason"])
with patch.object(cr, "probe_duration", return_value=10), patch.object(cr, "make_gif", side_effect=fake_gif):
    moved = engine._process_item(cr.VideoItem(str(move_source)), str(move_target), True, "fake")
check("Move removes input only after verified output exists",
      moved["status"] == "ok" and not move_source.exists()
      and (move_target / moved["folder"] / "move-source.mp4").read_bytes() == b"original video")

reports, notifications = [], []
subset = cr.BuildEngine(finished=reports.append, item_done=lambda *args: notifications.append(args))
selected = read[:2]
selected[0].tags = ["Nature"]
selected[1].tags = []
subset_target = TMP / "subset"
with patch.object(cr, "find_ffmpeg", return_value="fake"), patch.object(cr, "make_gif", side_effect=fake_gif):
    subset._run(selected, str(subset_target), False, [], True)
check("subset builds only given tagged files and reports no-tag skips",
      len(list(subset_target.iterdir())) == 1 and len(reports[0]) == 2
      and reports[0][1]["reason"] == "skipped — no tags")
check("item_done carries the new id and preview path",
      len(notifications[0]) == 4 and notifications[0][2] and Path(notifications[0][3]).exists())

entered, proceed, second = threading.Event(), threading.Event(), threading.Event()
pause_engine = cr.BuildEngine()
def paused_run(args):
    if "-i" in args:
        if not entered.is_set():
            pause_engine.pause()
            entered.set()
            proceed.wait(5)
        else:
            second.set()
    return subprocess.CompletedProcess(args, 1, "", banner)
pause_items = [cr.VideoItem(str(read_dir / name)) for name in ("a.mp4", "b.mkv")]
with patch.object(cr, "find_ffmpeg", return_value="fake"), patch.object(cr, "_run", side_effect=paused_run), \
        patch.object(cr, "make_gif", side_effect=fake_gif):
    pause_engine.start(pause_items, str(TMP / "pause"), move=False)
    reached = entered.wait(5)
    proceed.set()
    check("pause waits between items", reached and not second.wait(.15))
    pause_engine.resume()
    pause_engine._thread.join(5)
    check("resume continues the next item", second.is_set() and not pause_engine.is_running())

print()
print("PASSED %d/%d" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
