"""A rotation bringing Wallpaper Engine's playlist along, checked without it.

Run it directly — there is no test framework in this project:

    .venv\\Scripts\\python.exe tests\\test_playlist_refresh.py

Everything happens in a temporary folder: a fake Wallpaper Engine install with
its own config.json and playliststate.bin, a fake myprojects and reserve, and a
stand-in for closing and starting the engine, so nothing on the machine is read
or touched and no real Wallpaper Engine is closed.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.engines import engine_control, playlist_refresh as pr       # noqa: E402
from app.engines import wallpaper_timer as wt                        # noqa: E402
from app.engines.rotator import config as rotator_config             # noqa: E402
from app.engines.rotator.config import Config, History, RunRecord    # noqa: E402
from app.engines.rotator.core import Rotator                         # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="playlist_refresh_test_"))
results: list[bool] = []


def check(label: str, condition: bool) -> None:
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label)


def wallpaper(root: Path, name: str, manifest: dict | None, files: dict[str, bytes]) -> Path:
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    if manifest is not None:
        (folder / "project.json").write_text(json.dumps(manifest), encoding="utf-8")
    for rel, data in files.items():
        (folder / rel).parent.mkdir(parents=True, exist_ok=True)
        (folder / rel).write_bytes(data)
    return folder


def slashed(path: Path) -> str:
    return str(path).replace("\\", "/")


# ---- What a playlist lists for a wallpaper --------------------------------------

LIB = TMP / "lib"
video = wallpaper(LIB, "video", {"file": "clip.mp4", "type": "video"}, {"clip.mp4": b"v"})
packed = wallpaper(LIB, "scene", {"file": "scene.json", "type": "scene"}, {"scene.pkg": b"p"})
loose = wallpaper(LIB, "loose-scene", {"file": "scene.json", "type": "scene"},
                  {"scene.json": b"{}"})
web = wallpaper(LIB, "web", {"file": "index.html", "type": "web"}, {"index.html": b"<p>"})
nested = wallpaper(LIB, "nested", {"file": "media/clip.webm"}, {"media/clip.webm": b"w"})
gone = wallpaper(LIB, "gone", {"file": "clip.mp4"}, {})
bare = wallpaper(LIB, "bare", None, {"clip.mp4": b"v"})

check("a video is listed by the file its project.json names, with forward slashes",
      pr.playlist_item(video) == slashed(video / "clip.mp4"))
check("a compiled scene is listed by its scene.pkg, as Wallpaper Engine lists it",
      pr.playlist_item(packed) == slashed(packed / "scene.pkg"))
check("a scene still in source form keeps its scene.json",
      pr.playlist_item(loose) == slashed(loose / "scene.json"))
check("a web wallpaper is listed by its page", pr.playlist_item(web) == slashed(web / "index.html"))
check("a file in a subfolder of the wallpaper is found",
      pr.playlist_item(nested) == slashed(nested / "media" / "clip.webm"))
check("a manifest naming a file that is not there gives nothing to list",
      pr.playlist_item(gone) is None)
check("a folder without a project.json gives nothing to list", pr.playlist_item(bare) is None)
check("build_items lists every usable wallpaper, in folder order",
      pr.build_items(str(LIB)) == [slashed(loose / "scene.json"),
                                   slashed(nested / "media" / "clip.webm"),
                                   slashed(packed / "scene.pkg"),
                                   slashed(video / "clip.mp4"), slashed(web / "index.html")])

# ---- Which folder of myprojects an item lives in --------------------------------

DEST = "W:\\steam\\steamapps\\common\\wallpaper_engine\\projects\\myprojects"
MY = DEST.replace("\\", "/")
check("an item is placed in its folder whatever the slashes and case",
      pr.folder_in("w:/Steam/steamapps/common/wallpaper_engine/projects/MyProjects/ABC/x.mp4",
                   DEST) == "abc")
check("even when its file sits deeper in the folder",
      pr.folder_in(f"{MY}/abc/media/x.mp4", DEST) == "abc")
check("a folder whose name only starts like myprojects is not inside it",
      pr.folder_in(f"{MY}2/abc/x.mp4", DEST) is None)
check("a workshop item is not in myprojects",
      pr.folder_in("W:/steam/steamapps/workshop/content/431960/1/x.mp4", DEST) is None)

# ---- Finding the rotation's playlist by what is in it ---------------------------

SETTINGS = {"delay": 10, "mode": "timer", "order": "random", "transition": "-2",
            "transitiontime": 1500, "updateonpause": False, "videosequence": False}
old = [f"old{i}" for i in range(10)]
rotation_items = ([f"{MY}/{name}/clip.mp4" for name in old]
                  + ["W:/steam/steamapps/common/wallpaper_engine/projects/defaultprojects/"
                     "deep_space/scene.json"])
workshop_items = [f"W:/steam/steamapps/workshop/content/431960/{i}/v.mp4" for i in range(30)]
favourites = [f"{MY}/{name}/clip.mp4" for name in old[:2]]


def make_config() -> dict:
    return {
        "?installdirectory": "W:/steam/steamapps/common/wallpaper_engine",
        "someone": {"general": {
            "browser": {"folders": {}},
            "playlists": [
                {"name": "renamed by hand", "items": list(rotation_items), "settings": dict(SETTINGS)},
                {"name": "subscribed", "items": list(workshop_items), "settings": dict(SETTINGS)},
                {"name": "twin", "items": list(rotation_items), "settings": dict(SETTINGS)},
                {"name": "favourites", "items": list(favourites), "settings": dict(SETTINGS)},
            ],
            "wallpaperconfig": {"selectedwallpapers": {
                "Monitor0": {"file": workshop_items[0],
                             "playlist": {"name": "subscribed", "items": list(workshop_items),
                                          "settings": dict(SETTINGS)}},
                "Monitor1": {"file": rotation_items[3], "local": True,
                             "playlist": {"name": "renamed by hand",
                                          "items": list(rotation_items),
                                          "settings": dict(SETTINGS)}},
            }},
        }},
    }


config = make_config()
found = pr.find_playlists(config, DEST, set(old))
labels = sorted(f.label for f in found)
check("the rotation's playlist is found by its contents, not its name",
      "saved 'renamed by hand'" in labels)
check("a saved twin of it is found too", "saved 'twin'" in labels)
check("and the monitor playing it", "'renamed by hand' on Monitor1" in labels)
check("a workshop playlist is never the rotation's",
      not any("subscribed" in label for label in labels))
check("nor a few favourites that happen to live in myprojects",
      not any("favourites" in label for label in labels))
check("with nothing to rotate, nothing belongs", pr.find_playlists(config, DEST, set()) == [])
check("a playlist of an older rotation, sharing almost nothing, is not taken for this one",
      pr.find_playlists(config, DEST, {f"new{i}" for i in range(10)}) == [])

# ---- Rewriting config.json ------------------------------------------------------

fresh = [f"{MY}/new{i}/clip.mp4" for i in range(12)]
restarted = pr.rewrite_config(config, found, DEST, fresh)
general = config["someone"]["general"]
saved = general["playlists"][0]
check("the saved playlist now lists the new set",
      [i for i in saved["items"] if "/myprojects/" in i] == fresh)
check("and keeps what it held from outside myprojects",
      rotation_items[-1] in saved["items"])
check("its name and settings are untouched",
      saved["name"] == "renamed by hand" and saved["settings"] == SETTINGS)
check("the twin is refilled the same way", general["playlists"][2]["items"] == saved["items"])
check("the workshop playlist is left exactly as it was",
      general["playlists"][1]["items"] == workshop_items
      and general["wallpaperconfig"]["selectedwallpapers"]["Monitor0"]["playlist"]["items"]
      == workshop_items)
check("the favourites are left exactly as they were", general["playlists"][3]["items"] == favourites)
monitor1 = general["wallpaperconfig"]["selectedwallpapers"]["Monitor1"]
check("the monitor playing it gets the new list", monitor1["playlist"]["items"] == saved["items"])
check("and opens on one of the new wallpapers", monitor1["file"] in monitor1["playlist"]["items"])
check("only that monitor starts over", list(restarted) == ["Monitor1"])

sorted_config = make_config()
sorted_config["someone"]["general"]["wallpaperconfig"]["selectedwallpapers"]["Monitor1"][
    "playlist"]["settings"]["order"] = "sorted"
pr.rewrite_config(sorted_config, pr.find_playlists(sorted_config, DEST, set(old)), DEST, fresh)
check("a sorted playlist starts from its first wallpaper",
      sorted_config["someone"]["general"]["wallpaperconfig"]["selectedwallpapers"]["Monitor1"]
      ["file"] == rotation_items[-1])

text = pr.dump_config(config)
check("config.json is written back as JSON that reads back the same",
      json.loads(text.decode("utf-8")) == config)
check("in Wallpaper Engine's own style — tabs, CRLF — and raw UTF-8",
      b"\r\n\t" in text and b"\\u" not in pr.dump_config({"k": "超长版本"}))

# ---- Rewriting playliststate.bin ------------------------------------------------


def state_file(monitors: list[tuple[str, int, str, list[str]]]) -> bytes:
    desktop = wt.StateSection(wt.SECTION, [
        wt.StateMonitor(name=n, instance=i, current=c, waiting=list(w), marks=[0] * len(w))
        for n, i, c, w in monitors])
    return wt.write_state_file([desktop, wt.StateSection("\0\0"), wt.StateSection("")])


state = state_file([("Monitor0", 168, workshop_items[5], workshop_items[6:]),
                    ("Monitor1", 123, rotation_items[4], rotation_items[5:])])
check("the state file survives a read and a write byte for byte",
      wt.write_state_file(wt.read_state_file(state)) == state)
odd = state.replace(b"old4", b"ol\xff4")
check("even with bytes in a path that are not UTF-8",
      wt.write_state_file(wt.read_state_file(odd)) == odd)

rewritten = pr.restart_passes(state, config, restarted)
decks = wt.parse_playlist_state(rewritten)
check("the restarted monitor opens on the wallpaper config.json names",
      decks["Monitor1"].current == monitor1["file"])
check("with every other wallpaper of the new list still to come",
      sorted(decks["Monitor1"].waiting + [decks["Monitor1"].current])
      == sorted(monitor1["playlist"]["items"]))
check("keeping its pass number", decks["Monitor1"].instance == 123)
before = wt.read_state_file(state)[0].monitors[0]
after = wt.read_state_file(rewritten)[0].monitors[0]
check("the other monitor's pass is not touched at all", before == after)
check("and neither are the other sections",
      [s.name for s in wt.read_state_file(rewritten)] == [wt.SECTION, "\0\0", ""])

# ---- Starting Wallpaper Engine again ---------------------------------------------

check("it is started again without the library window",
      engine_control.restart_arguments(["-showbrowse", "-language", "english", "-ShowBrowse",
                                        "-updateuicmd"])
      == ["-language", "english", "-updateuicmd"])
check("a rotation brings the playlist along unless told not to", Config().refresh_playlist)

# ---- Around a real rotation, with a stand-in engine ------------------------------

INSTALL = TMP / "wallpaper_engine"
(INSTALL / "bin").mkdir(parents=True)
MYPROJECTS = TMP / "myprojects"
RESERVE = TMP / "reserve"
DUPES = TMP / "duplicates"
for d in (MYPROJECTS, RESERVE, DUPES):
    d.mkdir()
for i in range(6):
    wallpaper(MYPROJECTS, f"old{i}", {"file": "clip.mp4"}, {"clip.mp4": b"v"})
    wallpaper(RESERVE, f"new{i}", {"file": "clip.mp4"}, {"clip.mp4": b"v"})
wallpaper(MYPROJECTS, "[protected] keep", {"file": "clip.mp4"}, {"clip.mp4": b"v"})

pr.backup_dir = lambda: TMP / "backups"
rotator_config.HISTORY_PATH = TMP / "history.json"
calls: list[str] = []


class FakeEngines:
    close_works = True

    def running(self):
        calls.append("running")
        return engine_control.Engine(pid=4242, exe=str(INSTALL / "wallpaper64.exe"),
                                     arguments=["-language", "english"])

    def close(self, engine, seconds=0):
        calls.append("close")
        return self.close_works

    def start(self, engine, seconds=0):
        calls.append("start " + " ".join(engine.restart_command()[1:]))
        return True


fake = FakeEngines()
engine_control.running, engine_control.close, engine_control.start = (
    fake.running, fake.close, fake.start)


def install_engine_files() -> None:
    items = [slashed(MYPROJECTS / f"old{i}" / "clip.mp4") for i in range(6)]
    cfg = {"user": {"general": {
        "playlists": [{"name": "custom", "items": items, "settings": dict(SETTINGS)}],
        "wallpaperconfig": {"selectedwallpapers": {
            "Monitor1": {"file": items[0], "playlist": {"name": "custom", "items": list(items),
                                                        "settings": dict(SETTINGS)}}}}}}}
    (INSTALL / "config.json").write_bytes(pr.dump_config(cfg))
    (INSTALL / "bin" / "playliststate.bin").write_bytes(
        state_file([("Monitor1", 7, items[0], items[1:])]))


def rotate(completed: bool = True) -> tuple[pr.PlaylistRefresh, list[str]]:
    events: list[str] = []
    cfg = Config(source=str(RESERVE), destination=str(MYPROJECTS), duplicates=str(DUPES),
                 count=6)
    refresh = pr.PlaylistRefresh(cfg.destination, lambda e: events.append(e.message))
    # The old set was moved in by an earlier run, so this one brings the new set.
    earlier = RunRecord(id="earlier", timestamp="2026-01-01 00:00:00",
                        moved=[f"old{i}" for i in range(6)])
    rotator = Rotator(cfg, History([earlier]))
    try:
        refresh.prepare()
        if completed:
            rotator.run()
    finally:
        refresh.finish(completed=rotator.completed)
    return refresh, events


install_engine_files()
refresh, events = rotate()
cfg = json.loads((INSTALL / "config.json").read_text(encoding="utf-8"))
items = cfg["user"]["general"]["playlists"][0]["items"]
check("after a rotation the playlist lists what is now in myprojects",
      sorted(Path(i).parent.name for i in items)
      == sorted(["[protected] keep"] + [f"new{i}" for i in range(6)]))
check("on the monitor too",
      cfg["user"]["general"]["wallpaperconfig"]["selectedwallpapers"]["Monitor1"]["playlist"]
      ["items"] == items)
deck = wt.parse_playlist_state((INSTALL / "bin" / "playliststate.bin").read_bytes())["Monitor1"]
check("and the monitor's pass starts over on the new set",
      sorted([deck.current] + deck.waiting) == sorted(items))
check("both files were copied aside before they were rewritten",
      (TMP / "backups" / "config.json").is_file()
      and (TMP / "backups" / "playliststate.bin").is_file())
check("Wallpaper Engine was closed first and started again after, as it was started",
      calls == ["running", "close", "start -language english"])
check("the summary says what was rebuilt", any("custom" in line for line in refresh.summary))

calls.clear()
before = (INSTALL / "config.json").read_bytes()
refresh, events = rotate(completed=False)
check("a rotation that did not finish leaves the playlist as it was",
      (INSTALL / "config.json").read_bytes() == before)
check("but still starts Wallpaper Engine again", calls[-1].startswith("start"))

calls.clear()
fake.close_works = False
refresh, events = rotate()
check("when Wallpaper Engine will not close, nothing of it is touched",
      (INSTALL / "config.json").read_bytes() == before and "start" not in " ".join(calls))
check("and the summary says to rebuild it by hand",
      any("by hand" in line for line in refresh.summary))
fake.close_works = True

calls.clear()
(INSTALL / "config.json").write_text("{ not json", encoding="utf-8")
refresh, events = rotate()
check("an unreadable config.json is reported, not guessed at",
      any("could not be read" in line for line in refresh.summary))
check("and Wallpaper Engine is started again all the same", calls[-1].startswith("start"))

failed = sum(1 for r in results if not r)
print()
print(f"{'FAILED' if failed else 'PASSED'} {len(results) - failed}/{len(results)}")
sys.exit(1 if failed else 0)
