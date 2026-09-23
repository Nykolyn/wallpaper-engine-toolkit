"""The countdown to the next wallpaper, checked without Wallpaper Engine running.

Run it directly — there is no test framework in this project:

    .venv\\Scripts\\python.exe tests\\test_wallpaper_timer.py
    .venv\\Scripts\\python.exe tests\\test_wallpaper_timer.py --live

The default run builds its own config.json and playliststate.bin in a temporary
folder and drives the timer with a fake clock, fake windows and a fake Wallpaper
Engine process, so every second it counts is one this script decided. `--live`
additionally reads the real state file and the real desktop.
"""
from __future__ import annotations

import json
import os
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.engines.wallpaper_timer as wt     # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="wallpaper_timer_test_"))
results: list[bool] = []


def check(label: str, condition: bool) -> None:
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label)


def text(value: str) -> bytes:
    raw = value.encode("utf-8")
    return struct.pack("<I", len(raw)) + raw


def state_bytes(monitors: dict[str, tuple[str, list[str]]]) -> bytes:
    """playliststate.bin as Wallpaper Engine lays it out, with the two empty
    sections the real file carries after the desktop one."""
    out = text(wt.MAGIC) + struct.pack("<I", 3)
    out += text(wt.SECTION) + struct.pack("<I", len(monitors))
    for name, (current, waiting) in monitors.items():
        out += text(name) + struct.pack("<I", 84) + text(current) + struct.pack("<I", 0)
        out += struct.pack("<I", len(waiting))
        for path in waiting:
            out += text(path) + struct.pack("<I", 0)
    for _ in range(2):
        out += text("") + struct.pack("<I", 0)
    return out


# ---- Reading Wallpaper Engine's state file -------------------------------------

blob = state_bytes({"Monitor1": ("W:/a/current.mp4", ["W:/a/x.mp4", "W:/a/y.mp4"]),
                    "Monitor0": ("W:/b/other.pkg", [])})
decks = wt.parse_playlist_state(blob)
check("both monitors are read out of the state file", sorted(decks) == ["Monitor0", "Monitor1"])
check("with the wallpaper on screen", decks["Monitor1"].current == "W:/a/current.mp4")
check("and what this pass has still to show", decks["Monitor1"].waiting == ["W:/a/x.mp4", "W:/a/y.mp4"])
check("an empty deck is an empty list, not a failure", decks["Monitor0"].waiting == [])
check("non-ASCII paths survive, as the real library is full of them",
      wt.parse_playlist_state(state_bytes({"Monitor0": ("W:/超长版本.mp4", [])}))
      ["Monitor0"].current == "W:/超长版本.mp4")

for label, broken in (("a file that is not a playlist state", text("NOTMAGIC") + b"\0" * 8),
                      ("a truncated file", blob[:len(blob) // 2]),
                      ("an impossible string length", text(wt.MAGIC) + struct.pack("<I", 1)
                       + struct.pack("<I", 1 << 30))):
    try:
        wt.parse_playlist_state(broken)
        refused = False
    except ValueError:
        refused = True
    check(f"{label} is refused rather than half-read", refused)


# ---- Following the two files ---------------------------------------------------
#
# The countdown and the tracker share one watcher, which reads each file again
# only when a stat says it was rewritten, and numbers every read that succeeded.

watched = TMP / "watched"
(watched / "bin").mkdir(parents=True)
files = wt.EngineFiles(watched / "config.json")
files.refresh()
check("with neither file there, there is nothing to follow",
      (files.config_version, files.state_version, files.state_ok) == (0, 0, False))

(watched / "config.json").write_text('{"u": {"general": {}}}', encoding="utf-8")
files.refresh()
parsed = files.config
files.refresh()
check("a config.json that appears is one new version, however often it is looked at",
      files.config_version == 1)
check("and is parsed once, for everyone following it", files.config is parsed and "u" in parsed)

(watched / "config.json").write_text('{"u": {"gen', encoding="utf-8")
os.utime(watched / "config.json", (4_000, 4_000))
files.refresh()
check("a config.json caught half-written is not a new version",
      files.config_version == 1 and files.config_error)
check("and what was read before stays in use meanwhile", files.config is parsed)
(watched / "config.json").write_text('{"u": {"general": {"user": {}}}}', encoding="utf-8")
os.utime(watched / "config.json", (4_100, 4_100))
files.refresh()
check("once written in full it is read",
      files.config_version == 2 and files.config_error is None
      and files.config["u"]["general"] == {"user": {}})

parses = {"n": 0}
real_parse = wt.parse_playlist_state


def counted_parse(data):
    parses["n"] += 1
    return real_parse(data)


wt.parse_playlist_state = counted_parse
state_file = watched / "bin" / "playliststate.bin"
state_file.write_bytes(state_bytes({"Monitor0": ("W:/a.mp4", ["W:/b.mp4"])}))
os.utime(state_file, (5_000, 5_000))
files.refresh()
check("a state file is read and numbered",
      files.state_ok and files.state_version == 1 and files.decks["Monitor0"].current == "W:/a.mp4")
check("and dated by when it was written, not when it was read", files.state_written == 5_000)
files.refresh()
check("an unchanged state file is not read again", parses["n"] == 1 and files.state_version == 1)

state_file.write_bytes(state_bytes({"Monitor0": ("W:/b.mp4", [])})[:20])
files.refresh()
check("one caught half-written is not taken, and is not a new version",
      not files.state_ok and files.state_version == 1)
check("while what was read before is kept", files.decks["Monitor0"].current == "W:/a.mp4")
files.refresh()
check("nor is the same broken file parsed again every second", parses["n"] == 2)

state_file.write_bytes(state_bytes({"Monitor0": ("W:/b.mp4", [])}))
os.utime(state_file, (5_600, 5_600))
files.refresh()
check("once written in full it is the next version",
      files.state_ok and files.state_version == 2 and files.decks["Monitor0"].current == "W:/b.mp4")

state_file.unlink()
files.refresh()
check("a state file that goes away leaves nothing to follow", not files.state_ok)
wt.parse_playlist_state = real_parse


# ---- Wallpaper Engine's pause rules ----------------------------------------------

rules = wt.PlaybackRules.from_user({"playbackfocus": "pause", "playbackmaximized": "stop",
                                    "playbackfullscreen": "mute"})
check("the rules are read from the user settings",
      (rules.focus, rules.maximized, rules.fullscreen) == ("pause", "stop", "mute"))
check("a focused application pauses the wallpaper when set to pause",
      wt.is_paused(rules, wt.ScreenState(focused=True)))
check("a maximized one does when set to stop",
      wt.is_paused(rules, wt.ScreenState(maximized=True)))
check("but a fullscreen one set only to mute leaves it running",
      not wt.is_paused(rules, wt.ScreenState(fullscreen=True)))
check("and a monitor with nothing on it is never paused",
      not wt.is_paused(wt.PlaybackRules("pause", "pause", "pause"), wt.ScreenState()))
check("settings that are missing mean keep running",
      wt.PlaybackRules.from_user({}) == wt.PlaybackRules("run", "run", "run"))

timer_settings = wt.TimerSettings.from_playlist({"settings": {"delay": 10, "mode": "timer"}})
check("the delay is read in minutes and kept in seconds", timer_settings.delay == 600)
check("a timer playlist counts down", timer_settings.counts_down)
check("one that changes when a video ends does not",
      not wt.TimerSettings.from_playlist({"settings": {"mode": "timer", "videosequence": True}}).counts_down)
check("nor does one on a schedule",
      not wt.TimerSettings.from_playlist({"settings": {"mode": "daytime"}}).counts_down)


# ---- One monitor's clock ---------------------------------------------------------

clock = wt.MonitorClock("Monitor1")
clock.observe("W:/a.mp4", changed_at=100, now=100, we_started=0, waiting=10)
check("what is on screen when the tracker first looks has no known start", not clock.known)
clock.observe("W:/b.mp4", changed_at=1000, now=1003, we_started=0, waiting=9)
check("a change starts the count", clock.known)
check("from the second the state file was written, not from when it was read",
      abs(clock.running - (3 + wt.WRITE_LAG)) < 1e-9)
check("plus the seconds Wallpaper Engine takes to write it after the real change",
      wt.WRITE_LAG == 3.0)
check("a change long after Wallpaper Engine started is exact", not clock.approximate)

clock.advance(10, paused=False)
check("playing time adds up", abs(clock.running - (13 + wt.WRITE_LAG)) < 1e-9)
clock.advance(10, paused=True)
check("paused time does not", abs(clock.running - (13 + wt.WRITE_LAG)) < 1e-9)

clock.observe("W:/b.mp4", changed_at=1020, now=1020, we_started=0, waiting=9)
check("the file rewritten for another monitor does not reset this one",
      abs(clock.running - (13 + wt.WRITE_LAG)) < 1e-9)
clock.observe("W:/b.mp4", changed_at=1030, now=1030, we_started=0, waiting=8)
check("the same wallpaper drawn again, seen as the deck shrinking, does", clock.running == wt.WRITE_LAG)

fresh = wt.MonitorClock("Monitor0", wallpaper="W:/x.mp4", waiting=5)
fresh.observe("W:/y.mp4", changed_at=530, now=530, we_started=500, waiting=4)
check("a change in the first minute after Wallpaper Engine starts is only approximate",
      fresh.known and fresh.approximate)


# ---- The countdown the tray draws ---------------------------------------------

cd = wt.Countdown("Monitor1", delay=600, running=150, known=True)
check("what is left is the delay less the playing time", cd.remaining == 450)
check("and as a share of the ring", abs(cd.fraction - 0.75) < 1e-9)
check("it reads as minutes and seconds", cd.describe() == "next in 7:30")
check("paused says so", wt.Countdown("M", 600, 150, known=True, paused=True).describe()
      == "next in 7:30 (paused)")
check("an approximate count is marked", wt.Countdown("M", 600, 150, known=True, approximate=True)
      .describe().startswith("next in ≈"))
over = wt.Countdown("M", 600, 700, known=True)
check("past its time it stops at zero rather than going negative", over.remaining == 0 and over.due)
check("and says the change is due", over.describe() == "next any moment")
unknown = wt.Countdown("M", 600)
check("before any change there is no number to show", unknown.remaining is None and unknown.fraction is None)
check("and it says what it is waiting for", "waiting" in unknown.describe())
check("a playlist with no timer shows nothing at all",
      wt.Countdown("M", 600, 0, known=True, active=False).describe() == "")


# ---- The whole timer, driven second by second ------------------------------------

root = TMP / "wallpaper_engine"
(root / "bin").mkdir(parents=True)
config = root / "config.json"


def write_config(update_on_pause=False, mode="timer"):
    config.write_text(json.dumps({"?installdirectory": "x", "steamuser": {"general": {
        "user": {"playbackfocus": "pause", "playbackmaximized": "pause",
                 "playbackfullscreen": "pause", "monitormap": {}},
        "wallpaperconfig": {"selectedwallpapers": {
            m: {"file": "x", "playlist": {"items": ["a"], "settings": {
                "delay": 10, "mode": mode, "updateonpause": update_on_pause,
                "transitiontime": 1500}}}
            for m in ("Monitor0", "Monitor1")}}}}}), encoding="utf-8")


def write_state(monitor1: str, monitor0: str, at: float, deck1: int = 5, deck0: int = 5):
    path = root / "bin" / "playliststate.bin"
    path.write_bytes(state_bytes({"Monitor1": (monitor1, ["w"] * deck1),
                                  "Monitor0": (monitor0, ["w"] * deck0)}))
    os.utime(path, (at, at))


now = {"t": 10_000.0}
engine = {"proc": (4242, 1_000.0)}
screens = {"Monitor0": wt.ScreenState(), "Monitor1": wt.ScreenState()}


def make_timer(save=None):
    return wt.WallpaperTimer(str(config), find_engine=lambda: engine["proc"], open_memory=None,
                             measure_screens=lambda rects, ignore: screens,
                             find_displays=lambda mm: {"Monitor0": (0, 0, 1, 1), "Monitor1": (1, 0, 2, 1)},
                             clock=lambda: now["t"], save_path=save)


def run(seconds: int):
    for _ in range(seconds):
        now["t"] += 1
        timer.tick()


write_config()
write_state("W:/one.mp4", "W:/zero.mp4", at=now["t"] - 50)
timer = make_timer()
out = timer.tick()
check("a timer that has just started knows the playlists but no times",
      out["Monitor1"].active and out["Monitor1"].remaining is None)

write_state("W:/two.mp4", "W:/zero.mp4", at=now["t"] + 1, deck1=4)
run(61)
check("after a change and a minute of playing, nine minutes are left",
      abs(timer.countdowns["Monitor1"].remaining - (540 - wt.WRITE_LAG)) < 1.5)
check("the other monitor, which did not change, is still unknown",
      timer.countdowns["Monitor0"].remaining is None)

screens["Monitor1"] = wt.ScreenState(maximized=True)
before = timer.countdowns["Monitor1"].remaining
run(120)
check("two minutes behind a maximized window do not count",
      abs(timer.countdowns["Monitor1"].remaining - before) < 1e-6)
check("and the countdown says it is paused", timer.countdowns["Monitor1"].paused)

screens["Monitor0"] = wt.ScreenState(focused=True)
screens["Monitor1"] = wt.ScreenState()
run(60)
check("an application focused on the other monitor does not pause this one",
      abs(timer.countdowns["Monitor1"].remaining - (before - 60)) < 1.5)

write_state("W:/two.mp4", "W:/zero2.mp4", at=now["t"] + 1, deck1=4, deck0=4)
run(3)
check("a change on the other monitor leaves this countdown where it was",
      abs(timer.countdowns["Monitor1"].remaining - (before - 63)) < 1.5)
check("while that monitor now has one of its own", timer.countdowns["Monitor0"].remaining is not None)

write_state("W:/three.mp4", "W:/zero2.mp4", at=now["t"] + 1, deck1=3, deck0=4)
run(2)
check("the next change on this monitor starts it again from the full delay",
      timer.countdowns["Monitor1"].remaining > 597 - wt.WRITE_LAG)

run(700)
check("a wallpaper that outstays its delay reads as due, not negative",
      timer.countdowns["Monitor1"].due)

# Stalls of the tray itself are not counted as playing time.
last = timer.countdowns["Monitor0"].running
now["t"] += 3600
timer.tick()
check("an hour the tray was frozen for is not an hour of wallpaper",
      timer.countdowns["Monitor0"].running - last <= 5.0)

write_config(update_on_pause=True)
os.utime(config, (now["t"] + 5, now["t"] + 5))
write_state("W:/four.mp4", "W:/zero2.mp4", at=now["t"] + 1, deck1=2, deck0=4)
screens["Monitor1"] = wt.ScreenState(fullscreen=True)
run(30)
check("with update-on-pause set, the timer runs through a pause",
      29 + wt.WRITE_LAG <= timer.countdowns["Monitor1"].running <= 31 + wt.WRITE_LAG)
check("and is not shown as paused", not timer.countdowns["Monitor1"].paused)

engine["proc"] = None
run(2)
check("when Wallpaper Engine closes there is nothing to count",
      timer.countdowns["Monitor1"].remaining is None and not timer.countdowns["Monitor1"].active)


# ---- Surviving a restart of the tray ------------------------------------------

save = TMP / "wallpaper_timer.json"
engine["proc"] = (4242, 2_000.0)
screens = {"Monitor0": wt.ScreenState(), "Monitor1": wt.ScreenState()}
write_config()
os.utime(config, (now["t"] + 10, now["t"] + 10))
write_state("W:/five.mp4", "W:/zero3.mp4", at=now["t"] + 1, deck1=5, deck0=5)
timer = make_timer(save)
run(1)
write_state("W:/six.mp4", "W:/zero3.mp4", at=now["t"] + 1, deck1=4, deck0=5)
run(40)
timer._save(now["t"])
said = timer.countdowns["Monitor1"].remaining

now["t"] += 2
again = make_timer(save)
again.tick()
check("a restarted tray picks the count up where it left off",
      again.countdowns["Monitor1"].remaining is not None
      and abs(again.countdowns["Monitor1"].remaining - said) < 1.5)
check("a two-second gap is not enough to call it approximate",
      not again.countdowns["Monitor1"].approximate)

now["t"] += 60
later = make_timer(save)
later.tick()
check("after a longer gap it resumes but admits it is approximate",
      later.countdowns["Monitor1"].approximate)

write_state("W:/seven.mp4", "W:/zero3.mp4", at=now["t"], deck1=3, deck0=5)
now["t"] += 2
moved = make_timer(save)
moved.tick()
check("if the wallpaper changed while the tray was away, the old count is not reused",
      moved.countdowns["Monitor1"].remaining is None)

engine["proc"] = (4343, 9_000.0)
write_state("W:/six.mp4", "W:/zero3.mp4", at=now["t"], deck1=4, deck0=5)
restarted = make_timer(save)
restarted.tick()
check("nor if Wallpaper Engine itself was restarted in between",
      restarted.countdowns["Monitor1"].remaining is None)


# ---- Reading Wallpaper Engine's own timer ---------------------------------------
#
# The playlist object in wallpaper64.exe, as found on the real process: the
# monitor's name as a short std::string, the delay in minutes at +88, the
# playlist instance (the u32 playliststate.bin stores) at +104, the timer at
# +108, the transition in ms at +116. A fake process holds such objects, decoys
# that share the name but not the rest, and noise.

import app.engines.we_memory as wm        # noqa: E402


def playlist_object(monitor, instance=84, delay=10.0, timer=123.5, transition=1500):
    block = bytearray(wm.BLOCK)
    block[0:32] = wm.signature(monitor)
    struct.pack_into("<f", block, wm.DELAY_OFF, delay)
    struct.pack_into("<I", block, wm.INSTANCE_OFF, instance)
    struct.pack_into("<f", block, wm.TIMER_OFF, timer)
    struct.pack_into("<i", block, wm.TRANSITION_OFF, transition)
    return bytes(block)


class FakeMemory:
    """A process as a few regions of bytes, some of them editable in place."""

    def __init__(self, regions):
        self.regions_ = {base: bytearray(data) for base, data in regions.items()}
        self.reads = 0
        self.read_bytes = 0

    def regions(self):
        return [(base, len(data)) for base, data in sorted(self.regions_.items())]

    def region_of(self, address):
        for base, data in self.regions_.items():
            if base <= address < base + len(data):
                return base, len(data)
        return None

    def read(self, address, size):
        self.reads += 1
        for base, data in self.regions_.items():
            if base <= address < base + len(data):
                start = address - base
                out = bytes(data[start:start + size])
                self.read_bytes += len(out)
                return out
        return b""

    def poke(self, name_address, offset, fmt, value):
        for base, data in self.regions_.items():
            if base <= name_address < base + len(data):
                struct.pack_into(fmt, data, name_address - base + offset, value)

    def poke_timer(self, name_address, value):
        self.poke(name_address, wm.TIMER_OFF, "<f", value)

    def close(self):
        pass


want1 = wm.Expected("Monitor1", instance=84, delay_minutes=10.0, transition_ms=1500)
want0 = wm.Expected("Monitor0", instance=467, delay_minutes=10.0, transition_ms=1500)
check("a matching object gives its timer",
      wm.check_block(playlist_object("Monitor1"), want1).timer == 123.5)
check("and says which pass it belongs to",
      wm.check_block(playlist_object("Monitor1"), want1).instance == 84)
check("a renumbered pass is the same object still, when the number is not the question",
      wm.check_block(playlist_object("Monitor1", instance=85), want1, instance=False).instance == 85)
check("but a wrong name never is",
      wm.check_block(playlist_object("Monitor0"), want1, instance=False) is None)
for label, block in (("another monitor's name", playlist_object("Monitor0")),
                     ("a different playlist instance", playlist_object("Monitor1", instance=85)),
                     ("a different delay", playlist_object("Monitor1", delay=5.0)),
                     ("a different transition", playlist_object("Monitor1", transition=1000)),
                     ("a timer past any possible delay", playlist_object("Monitor1", timer=5000.0)),
                     ("a negative timer", playlist_object("Monitor1", timer=-1.0)),
                     ("a block cut short", playlist_object("Monitor1")[:100])):
    check(f"{label} is not taken for the object", wm.check_block(block, want1) is None)

noise = bytes(range(256)) * 64
decoy = playlist_object("Monitor1", instance=999)          # right name, wrong object
region_a = noise + decoy + noise + playlist_object("Monitor0", instance=467, timer=40.0) + noise
region_b = noise[:1000] + playlist_object("Monitor1", timer=321.0) + noise
fake = FakeMemory({0x10000: region_a, 0x900000: region_b})
found = wm.find_objects(fake, [want1, want0])
check("each monitor's object is found, and only the real one",
      found == {"Monitor0": [0x10000 + len(noise) * 2 + len(decoy)],
                "Monitor1": [0x900000 + 1000]})
check("the timer is read back through the found address",
      wm.read_timer(fake, found["Monitor1"][0], want1).timer == 321.0)

# Where an object was last time is worth a read before anything is searched for.
spot = found["Monitor1"][0]
quick = FakeMemory({0x10000: region_a, 0x900000: region_b})
again = wm.find_objects(quick, [want1],
                        hints=[wm.Hint("Monitor1", spot, len(region_b), spot - 0x900000)])
check("a remembered place is found without searching anything",
      again["Monitor1"] == [spot] and quick.reads <= 2)

shifted = FakeMemory({0x770000: region_b})          # the same region, moved wholesale
cheap = wm.find_objects(shifted, [want1],
                        hints=[wm.Hint("Monitor1", 0x900000 + 1000, len(region_b), 1000)])
check("and so is the same spot in a region shaped like the one it was in",
      cheap["Monitor1"] == [0x770000 + 1000] and shifted.reads <= 3)

check("the smallest regions are swept first when nothing is remembered",
      wm.rank_regions([(0x1000, 900), (0x9000, 20), (0x5000, 400)])
      == [(0x9000, 20), (0x5000, 400), (0x1000, 900)])
check("and the regions nearest a remembered object when there is one",
      wm.rank_regions([(0x1000, 900), (0x9000, 20), (0x5000, 400)],
                      [wm.Hint("Monitor1", 0x5100, 400, 0x100)])
      == [(0x5000, 400), (0x9000, 20), (0x1000, 900)])
check("one read is small enough not to hold the other process up",
      wm.CHUNK <= 64 << 10)

# An object lying across the edge between two pieces of a read must still be found.
saved_chunk = wm.CHUNK
wm.CHUNK = 4096
edge = bytes(4096 - 40) + playlist_object("Monitor1", timer=7.0) + bytes(5000)
found = wm.find_objects(FakeMemory({0x20000: edge}), [want1])
wm.CHUNK = saved_chunk
check("an object split between two pieces of memory is found exactly once",
      found["Monitor1"] == [0x20000 + 4096 - 40])

moved = FakeMemory({0x900000: bytes(len(region_b))})
check("an address whose object has gone reads as nothing, not as a timer",
      wm.read_timer(moved, 0x900000 + 1000, want1) is None)

# Through the timer: exact when the object is there, the estimate when it is not.
now["t"] = 50_000.0
engine["proc"] = (7777, 49_000.0)
screens = {"Monitor0": wt.ScreenState(), "Monitor1": wt.ScreenState()}
write_config()
os.utime(config, (now["t"], now["t"]))
state_path = root / "bin" / "playliststate.bin"
state_path.write_bytes(state_bytes({"Monitor1": ("W:/m1.mp4", ["w"] * 5),
                                    "Monitor0": ("W:/m0.mp4", ["w"] * 5)}))
os.utime(state_path, (now["t"] - 100, now["t"] - 100))
process = FakeMemory({0x500000: noise + playlist_object("Monitor1", instance=84, timer=100.0) + noise})
name_at = 0x500000 + len(noise)
exact = wt.WallpaperTimer(str(config), find_engine=lambda: engine["proc"],
                          measure_screens=lambda rects, ignore: screens,
                          find_displays=lambda mm: {"Monitor0": (0, 0, 1, 1), "Monitor1": (1, 0, 2, 1)},
                          clock=lambda: now["t"], save_path=None,
                          open_memory=lambda pid: process, background=False)
out = exact.tick()
check("the countdown is read straight out of Wallpaper Engine when it can be",
      out["Monitor1"].exact and out["Monitor1"].remaining == 500.0)
check("and needs no change to have been seen first", out["Monitor1"].known)
check("a monitor whose object is not there is still estimated",
      not out["Monitor0"].exact and out["Monitor0"].remaining is None)

for second in range(1, 4):
    now["t"] += 1
    process.poke_timer(name_at, 100.0 + second)
    out = exact.tick()
check("the number follows Wallpaper Engine's to the fraction",
      abs(out["Monitor1"].remaining - 497.0) < 1e-4)
check("a timer that moves is not paused", not out["Monitor1"].paused)
for _ in range(3):
    now["t"] += 1
    out = exact.tick()
check("a timer that stands still is shown as paused, with no window rules involved",
      out["Monitor1"].paused and abs(out["Monitor1"].remaining - 497.0) < 1e-4)
check("and it is never marked approximate", "≈" not in out["Monitor1"].describe())

process.regions_[0x500000][:] = bytes(len(process.regions_[0x500000]))   # object gone
now["t"] += 1
out = exact.tick()
check("when the object goes away the countdown falls back to the estimate",
      not out["Monitor1"].exact)
check("carrying on from the exact count it last had rather than starting unknown",
      out["Monitor1"].known and abs(out["Monitor1"].remaining - 497.0) < 2.5)

# Re-applying a playlist renumbers the pass, and Wallpaper Engine writes the new
# number into the object where it stands. Searching all over again for an object
# that has not moved is what used to make Wallpaper Engine stutter, repeatedly.
now["t"] = 70_000.0
engine["proc"] = (7779, 60_000.0)
write_state("W:/m1.mp4", "W:/m0.mp4", now["t"] - 100)
held = FakeMemory({0x600000: noise + playlist_object("Monitor1", instance=84, timer=200.0) + noise})
held_at = 0x600000 + len(noise)
searches = {"n": 0}
real_find = wm.find_objects


def counted_find(*args, **kwargs):
    searches["n"] += 1
    return real_find(*args, **kwargs)


wm.find_objects = counted_find
renumber = wt.WallpaperTimer(str(config), find_engine=lambda: engine["proc"],
                             measure_screens=lambda rects, ignore: screens,
                             find_displays=lambda mm: {"Monitor0": (0, 0, 1, 1), "Monitor1": (1, 0, 2, 1)},
                             clock=lambda: now["t"], save_path=None,
                             open_memory=lambda pid: held, background=False)
check("the object is found by searching, once",
      renumber.tick()["Monitor1"].exact and searches["n"] == 1)
held.poke(held_at, wm.INSTANCE_OFF, "<I", 85)
now["t"] += 1
check("a renumbered pass does not lose the timer", renumber.tick()["Monitor1"].exact)
check("and does not start another search", searches["n"] == 1)
for _ in range(3):
    now["t"] += 1
    renumber.tick()
check("a number that disagrees with the state file is given time to agree",
      renumber.countdowns["Monitor1"].exact)
now["t"] += wt.INSTANCE_GRACE + 1
check("but an object that never agrees is given up on in the end",
      not renumber.tick()["Monitor1"].exact)
wm.find_objects = real_find

# Nothing is read out of Wallpaper Engine while it is still starting: that is when
# it is busiest, and reading its memory then is what the stutter was made of.
now["t"] = 90_000.0
engine["proc"] = (7780, now["t"] - 5)
write_state("W:/m1.mp4", "W:/m0.mp4", now["t"] - 100)
both = FakeMemory({0x700000: (noise + playlist_object("Monitor1", instance=84, timer=100.0)
                              + noise + playlist_object("Monitor0", instance=84, timer=30.0)
                              + noise)})
looks = {"screens": 0}


def count_screens(rects, ignore):
    looks["screens"] += 1
    return screens


young = wt.WallpaperTimer(str(config), find_engine=lambda: engine["proc"],
                          measure_screens=count_screens,
                          find_displays=lambda mm: {"Monitor0": (0, 0, 1, 1), "Monitor1": (1, 0, 2, 1)},
                          clock=lambda: now["t"], save_path=None,
                          open_memory=lambda pid: both, background=False)
young.tick()
check("a Wallpaper Engine that has only just started is left alone",
      both.read_bytes == 0 and not young.countdowns["Monitor1"].exact)
now["t"] += wt.SETTLE
out = young.tick()
check("and read once it has settled", out["Monitor1"].exact and out["Monitor0"].exact)
check("after which no window on screen needs looking at at all", looks["screens"] == 1)


# ---- The tray icon ---------------------------------------------------------------

from PySide6.QtGui import QColor, QImage        # noqa: E402
from PySide6.QtWidgets import QApplication      # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)
from app import theme                          # noqa: E402
from app.tracker_tray import tray_icon         # noqa: E402

theme.apply(app)


def pixels(icon) -> QImage:
    return icon.pixmap(64, 64).toImage().convertToFormat(QImage.Format_ARGB32)


def near(image: QImage, x: int, y: int, colour: str) -> bool:
    want = theme.color(colour)
    got = image.pixelColor(x, y)
    return (abs(got.red() - want.red()) < 40 and abs(got.green() - want.green()) < 40
            and abs(got.blue() - want.blue()) < 40 and got.alpha() > 200)


full = pixels(tray_icon(42, 1.0))
check("a full ring is drawn in the running colour at the top", near(full, 32, 6, "accent"))
half = pixels(tray_icon(42, 0.5))
check("half a ring covers the right side", near(half, 54, 32, "accent"))
check("and leaves the left side as bare track", not near(half, 10, 32, "accent"))
check("no ring at all when the time is unknown", not near(pixels(tray_icon(42, None)), 32, 6,
                                                          "accent"))
check("a paused ring is greyed rather than blue", near(pixels(tray_icon(42, 1.0, paused=True)),
                                                       32, 6, "text.lo"))
check("the icon still draws with nothing in the middle", not tray_icon(None, 0.3).isNull())


# ---- Live, opt-in ---------------------------------------------------------------

if "--live" in sys.argv:
    print("\n-- live (reads Wallpaper Engine's files and this desktop) --")
    from app.engines import steam_paths
    real = steam_paths.wallpaper_engine_dir()
    if real is None:
        print("   skipped: Steam or Wallpaper Engine was not found on this machine")
        print()
        print("PASSED %d/%d" % (sum(results), len(results)))
        sys.exit(0 if all(results) else 1)
    print("   found at %s" % real)
    state = real / wt.STATE_FILE
    if state.exists():
        decks = wt.parse_playlist_state(state.read_bytes())
        check("the real playliststate.bin parses", bool(decks))
        check("and names a wallpaper that exists for every monitor",
              all(Path(d.current.replace("/", os.sep)).exists() for d in decks.values()))
    live = wt.WallpaperTimer(str(real / "config.json"), save_path=None)
    out = live.tick()
    check("every monitor with a playlist gets a countdown", bool(out))
    check("and every one of them lands on an attached display",
          set(out) <= set(live.rects) or not live._engine)
    if live._engine:
        finder = wt.WallpaperTimer(str(real / "config.json"), save_path=None, background=False)
        import time as _time
        started = _time.perf_counter()
        first = finder.tick()
        taken = _time.perf_counter() - started
        _time.sleep(1.2)
        second = finder.tick()
        read = [m for m, c in second.items() if c.exact]
        check("Wallpaper Engine's own timer is found in the running process", bool(read))
        check("and reads as a sensible count for every monitor it was found for",
              all(0 <= second[m].running <= second[m].delay + 30 for m in read))
        check(f"and the search read only what it had to ({finder._search_cost[0]:.0f} MB "
              f"in {taken:.0f} s)", taken < 180)

print()
print("PASSED %d/%d" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
