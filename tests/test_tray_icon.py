"""The tray: its ring, its words, its menu and its two balloons.

    .venv\\Scripts\\python.exe tests\\test_tray_icon.py

The tray is a separate process the logon task starts, so what is checked here
is what it can be checked on without one: the ring drawn per state and size
(the arc by sampling pixels along the circle), the taskbar's colour, the
copy with a number known and not, the menu's composition and what each row
asks the window for, and that none of it needs the kit. Run offscreen.
"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
TMP = Path(tempfile.mkdtemp(prefix="tray_icon_test_"))
os.environ["WALLPAPER_TOOLKIT_DATA"] = str(TMP / "data")     # before any app module
(TMP / "data").mkdir()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# CI's console is cp1252; a label with a character outside it must not crash print.
sys.stdout.reconfigure(errors="replace")

import math                                           # noqa: E402

from PySide6.QtCore import QPoint                     # noqa: E402
from PySide6.QtGui import QColor, QImage              # noqa: E402
from PySide6.QtWidgets import QApplication           # noqa: E402

app = QApplication.instance() or QApplication([])

from app import theme, tray_icon as ti, tray_menu as tm, tray_words as tw   # noqa: E402
from app.engines.tracker import TIME_FMT, Progress                          # noqa: E402
from app.engines.wallpaper_timer import Countdown                           # noqa: E402

theme.apply(app, styled=False)
results: list[bool] = []
NB = chr(0xA0)


def check(label: str, condition: bool) -> None:
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label)


def image(state, size, **kw) -> QImage:
    return ti.ring_pixmap(state, size, **kw).toImage().convertToFormat(QImage.Format_ARGB32)


def near(got: QColor, want: str, slack=40) -> bool:
    w = theme.color(want)
    return (got.alpha() > 200 and abs(got.red() - w.red()) <= slack
            and abs(got.green() - w.green()) <= slack and abs(got.blue() - w.blue()) <= slack)


def on_ring(img: QImage, size: int, degrees: float) -> QColor:
    """The pixel on the ring's centre line, `degrees` clockwise from twelve."""
    r, c = ti.ring_radius(size), size / 2
    a = math.radians(degrees)
    return img.pixelColor(int(c + r * math.sin(a)), int(c - r * math.cos(a)))


def lit(img: QImage, size: int, colour: str, step=5) -> float:
    """The share of the ring (sampled every `step` degrees) in a colour."""
    angles = [d + step / 2 for d in range(0, 360, step)]
    return sum(near(on_ring(img, size, d), colour) for d in angles) / len(angles)


def solid(img: QImage) -> int:
    return sum(img.pixelColor(x, y).alpha() > 0
               for x in range(img.width()) for y in range(img.height()))


print("-- geometry --")
check("the stroke is the design's: 2.5 at 16, 3 at 24, 3.5 at 32",
      [ti.stroke_width(s) for s in (16, 24, 32)] == [2.5, 3.0, 3.5])
check("and interpolated, half a pixel to eight: 20, 40 and 48",
      [ti.stroke_width(s) for s in (20, 40, 48)] == [2.75, 4.0, 4.5])
check("the ring's radius leaves half a pixel at the edge: 6.25, 10, 13.75",
      [ti.ring_radius(s) for s in (16, 24, 32)] == [6.25, 10.0, 13.75])
check("every size Windows may ask for is drawn", ti.SIZES == (16, 20, 24, 32, 40, 48))
check("a tick's stroke is the design's at 16, 24 and 32",
      [ti.tick_width(s) for s in (16, 24, 32)] == [1.9, 2.0, 2.4])
check("and grows with the size beyond it", ti.tick_width(48) > ti.tick_width(32) > ti.tick_width(20))
check("the ring is snapped to 2 degrees, clamped to 0..1",
      ti.snap(0.5004) == 0.5 and ti.snap(1.4) == 1.0 and ti.snap(-1) == 0.0
      and ti.snap(None) is None and ti.snap(1 / 180) == 1 / 180)

print("-- each state at each size --")
for state in ti.STATES:
    ok = True
    for size in ti.SIZES:
        pix = ti.ring_pixmap(state, size, fraction=0.62, number=62)
        img = pix.toImage().convertToFormat(QImage.Format_ARGB32)
        ok = ok and (pix.width(), pix.height()) == (size, size) and solid(img) > size \
            and img.pixelColor(0, 0).alpha() == 0 and img.pixelColor(size - 1, size - 1).alpha() == 0
    check(f"{state}: drawn at every size, the right size, with clear corners", ok)
icon = ti.tray_icon(ti.RUNNING, 0.5, 50)
check("one QIcon holds them all, so Windows can pick at 150 percent",
      sorted(s.width() for s in icon.availableSizes()) == list(ti.SIZES))
check("and asking it for 24 gets the 24 that was drawn",
      icon.pixmap(24, 24).size().width() == 24 and not icon.pixmap(24, 24).isNull())
try:
    ti.ring_pixmap("sleepy", 16)
    check("a state that is none is refused", False)
except ValueError:
    check("a state that is none is refused", True)

print("-- the arc --")
quarter, half, three = (image(ti.RUNNING, 32, fraction=f) for f in (0.25, 0.5, 0.75))
check("a quarter ring covers about a quarter (the round caps add a little)",
      abs(lit(quarter, 32, "tray.dark.ring") - 0.25) < 0.08)
check("half covers half", abs(lit(half, 32, "tray.dark.ring") - 0.5) < 0.08)
check("three quarters cover three quarters", abs(lit(three, 32, "tray.dark.ring") - 0.75) < 0.08)
check("it runs clockwise from twelve: half is the right side, not the left",
      near(on_ring(half, 32, 90), "tray.dark.ring") and not near(on_ring(half, 32, 270), "tray.dark.ring"))
check("a quarter ends before three o'clock's far side",
      near(on_ring(quarter, 32, 45), "tray.dark.ring") and not near(on_ring(quarter, 32, 135), "tray.dark.ring"))
check("what is not filled is the track, dimmer",
      0 < on_ring(half, 32, 270).alpha() < 120)
check("an empty ring is track only", lit(image(ti.RUNNING, 32, fraction=0.0), 32, "tray.dark.ring") == 0)
check("a ring drawn at 16 covers its fraction too",
      abs(lit(image(ti.RUNNING, 16, fraction=0.5), 16, "tray.dark.ring") - 0.5) < 0.12)
check("the same step draws the same ring",
      image(ti.RUNNING, 24, fraction=0.5001) == image(ti.RUNNING, 24, fraction=0.5))
check("another step draws another",
      image(ti.RUNNING, 24, fraction=0.51) != image(ti.RUNNING, 24, fraction=0.5))
paused = image(ti.PAUSED, 32, fraction=0.5)
check("a paused ring is the same arc in grey",
      abs(lit(paused, 32, "tray.dark.paused") - 0.5) < 0.08 and lit(paused, 32, "tray.dark.ring") == 0)
finished = image(ti.FINISHED, 32, fraction=0.1)
check("a finished ring is full and green, whatever it was told",
      lit(finished, 32, "tray.dark.ok") > 0.97)
unknown = image(ti.UNKNOWN, 32)
dashes = [on_ring(unknown, 32, d).alpha() > 20 for d in range(0, 360, 3)]
check("an unknown ring is a dotted track: dashes and gaps, no fill",
      lit(unknown, 32, "tray.dark.ring") == 0 and 0.35 < sum(dashes) / len(dashes) < 0.65
      and sum(1 for a, b in zip(dashes, dashes[1:]) if a != b) >= 12)

print("-- the middle --")
for size in (24, 32, 40, 48):
    with_n = image(ti.RUNNING, size, fraction=0.5, number=62)
    check(f"{size} px carries the percentage",
          with_n != image(ti.RUNNING, size, fraction=0.5)
          and sum(near(with_n.pixelColor(x, y), "tray.dark.glyph", 60)
                  for x in range(size) for y in range(size)) > size // 2)
for size in (16, 20):
    plain = image(ti.RUNNING, size, fraction=0.5)
    check(f"{size} px has no room for it: the number is left out",
          image(ti.RUNNING, size, fraction=0.5, number=62) == plain)
    c = size // 2
    check(f"and a dot marks the centre at {size}",
          near(plain.pixelColor(c, c), "tray.dark.glyph", 30) or near(plain.pixelColor(c - 1, c - 1), "tray.dark.glyph", 30))
big = image(ti.RUNNING, 32, fraction=0.5, number=100)
inner = ti.ring_radius(32) - ti.stroke_width(32) / 2 + 1
check("three digits still fit inside the ring",
      any(near(big.pixelColor(x, y), "tray.dark.glyph", 60) for x in range(32) for y in range(32))
      and all(math.hypot(x + 0.5 - 16, y + 0.5 - 16) <= inner
              for x in range(32) for y in range(32) if near(big.pixelColor(x, y), "tray.dark.glyph", 60)))
check("a running ring with no count at 32 has the dot, not a blank",
      near(image(ti.RUNNING, 32, fraction=0.5).pixelColor(16, 16), "tray.dark.glyph", 30)
      or near(image(ti.RUNNING, 32, fraction=0.5).pixelColor(15, 15), "tray.dark.glyph", 30))
pause = image(ti.PAUSED, 32, fraction=0.5)
check("paused has two bars in the middle", near(pause.pixelColor(14, 16), "tray.dark.glyph", 30)
      and near(pause.pixelColor(18, 16), "tray.dark.glyph", 30) and pause.pixelColor(16, 16).alpha() < 20)
tick = image(ti.FINISHED, 32)
check("finished has a tick in the middle",
      near(tick.pixelColor(14, 21), "tray.dark.glyph", 60) or near(tick.pixelColor(14, 20), "tray.dark.glyph", 60))
check("an unknown ring with a count still says it; without one it asks",
      image(ti.UNKNOWN, 32, number=40) != image(ti.UNKNOWN, 32)
      and image(ti.UNKNOWN, 16, number=40) != image(ti.UNKNOWN, 16))

print("-- a light taskbar --")
light = image(ti.RUNNING, 32, fraction=0.5, light=True)
check("the ring is #2C6BD8", near(on_ring(light, 32, 90), "tray.light.ring", 10)
      and theme.color("tray.light.ring").name().upper() == "#2C6BD8")
check("and not the dark taskbar's", not near(on_ring(light, 32, 90), "tray.dark.ring", 10))
check("the glyphs are #1A1A1A", theme.color("tray.light.glyph").name().upper() == "#1A1A1A"
      and near(image(ti.FINISHED, 32, light=True).pixelColor(14, 21), "tray.light.glyph", 60)
      or near(image(ti.FINISHED, 32, light=True).pixelColor(14, 20), "tray.light.glyph", 60))
check("the track goes dark on light", on_ring(light, 32, 270).red() < 40
      and 0 < on_ring(light, 32, 270).alpha() < 120)
check("paused and finished have their light colours",
      near(on_ring(image(ti.PAUSED, 32, fraction=0.5, light=True), 32, 90), "tray.light.paused", 10)
      and near(on_ring(image(ti.FINISHED, 32, light=True), 32, 90), "tray.light.ok", 10))
check("a light icon is a different picture", light != image(ti.RUNNING, 32, fraction=0.5))
p = ti.palette(True)
check("the palette is the theme's", p.ring == theme.color("tray.light.ring")
      and ti.palette(False).ring == theme.color("tray.dark.ring"))


def reading(value):
    return lambda: value


check("SystemUsesLightTheme = 1 is a light taskbar", ti.taskbar_is_light(reading(1)))
check("0 is dark", not ti.taskbar_is_light(reading(0)))
check("a machine that cannot be asked is taken to be dark", not ti.taskbar_is_light(reading(None)))
check("the real switch reads without failing", ti.taskbar_is_light() in (True, False))

print("-- hearing Windows change it --")
state = {"value": 0}
watch = ti.TaskbarTheme(lambda: state["value"])
flips: list[bool] = []
watch.changed.connect(flips.append)
check("it starts as the switch says", watch.light is False)
check("reading it again unchanged says nothing", watch.refresh() is False and flips == [])
state["value"] = 1
check("a change is noticed and announced once", watch.refresh() and flips == [True] and watch.light)
check("and not again", not watch.refresh() and flips == [True])
state["value"] = 0
watch.poll(ti.RECHECK_SECONDS - 1)
check("polled before its time it does not read", watch.light is True)
watch.poll(1)
check("polled on time it does, so a message that never came is caught",
      watch.light is False and flips == [True, False])


def lparam(text):
    buffer = ctypes.create_unicode_buffer(text)
    lparam.keep = buffer
    return ctypes.addressof(buffer)


check("Windows' theme message is a colour message", ti.is_colour_message(ti.WM_THEMECHANGED, 0))
check("so is a settings change for ImmersiveColorSet",
      ti.is_colour_message(ti.WM_SETTINGCHANGE, lparam("ImmersiveColorSet")))
check("a settings change for anything else is not",
      not ti.is_colour_message(ti.WM_SETTINGCHANGE, lparam("intl"))
      and not ti.is_colour_message(ti.WM_SETTINGCHANGE, 0) and not ti.is_colour_message(0x0001, 0))
state["value"] = 1
watch.hear(ti.WM_SETTINGCHANGE, lparam("ImmersiveColorSet"))
app.processEvents()
check("hearing one refreshes", watch.light is True and flips[-1] is True)
state["value"] = 0
watch.hear(ti.WM_SETTINGCHANGE, lparam("intl"))
app.processEvents()
check("hearing another does not", watch.light is True)
watch.listen(app)
check("it listens through a window that exists and is never shown",
      watch._window is not None and not watch._window.isVisible())
if os.name == "nt":
    from ctypes import wintypes
    msg = wintypes.MSG()
    msg.message, msg.lParam = ti.WM_THEMECHANGED, 0
    handled = watch.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(msg))
    app.processEvents()
    check("Qt's native event filter passes a window message on and never swallows it",
          handled == (False, 0) and watch.light is False)

print("-- what the icon is, from the tracker's reading --")
_n = [0]


def progress(**kw) -> Progress:
    _n[0] += 1
    base = dict(cycle_id=f"c{_n[0]}", monitor="Monitor1", playlist="Rotation", seen=4, total=201,
                changes=4, repeats=0, order="random", delay=600, started="2026-09-19 09:10:00",
                current=None, current_title="", current_since=None, live=True, inferred=0,
                anchor="engine", gone=0)
    base.update(kw)
    return Progress(**base)


def countdown(**kw) -> Countdown:
    base = dict(monitor="Monitor1", delay=600.0, running=150.0, known=True, active=True)
    base.update(kw)
    return Countdown(**base)


check("a counting timer is running, with the share left",
      tw.icon_state(progress(), countdown()) == (ti.RUNNING, 0.75))
check("a paused one is paused, the share kept",
      tw.icon_state(progress(), countdown(paused=True)) == (ti.PAUSED, 0.75))
check("no timer reading is unknown",
      tw.icon_state(progress(), None) == (ti.UNKNOWN, None)
      and tw.icon_state(progress(), countdown(known=False)) == (ti.UNKNOWN, None)
      and tw.icon_state(progress(), countdown(active=False)) == (ti.UNKNOWN, None))
check("no playlist at all is unknown", tw.icon_state(None, countdown()) == (ti.UNKNOWN, None))
check("every wallpaper shown is finished, whatever the timer says",
      tw.icon_state(progress(seen=201), countdown(paused=True)) == (ti.FINISHED, 1.0)
      and tw.icon_state(progress(seen=201), None) == (ti.FINISHED, 1.0))
check("an empty playlist is not finished", tw.icon_state(progress(seen=0, total=0), None)[0] == ti.UNKNOWN)

print("-- the menu's words --")
p4 = progress()
check("tracking · 4 of 201 shown", tw.header_line(ti.RUNNING, p4) == "tracking · 4 of 201 shown")
check("paused says so", tw.header_line(ti.PAUSED, p4) == "paused · 4 of 201 shown")
check("an unread timer says that, and keeps the count",
      tw.header_line(ti.UNKNOWN, p4) == "timer unknown · 4 of 201 shown")
check("finished says all of them",
      tw.header_line(ti.FINISHED, progress(seen=201)) == "finished · all 201 shown")
check("no playlist: the tracker's own error, or that none was found",
      tw.header_line(ti.UNKNOWN, None, "config.json not found") == "config.json not found"
      and tw.header_line(ti.UNKNOWN, None) == "no playlist found")
check("thousands are grouped with a no-break space",
      tw.header_line(ti.RUNNING, progress(seen=1200, total=1400)) == f"tracking · 1{NB}200 of 1{NB}400 shown")
check("the hints: run 39, 12 waiting, nothing when unknown or none",
      tw.run_hint(39) == "run 39" and tw.run_hint(None) == "" and tw.review_hint(12) == "12 waiting"
      and tw.review_hint(0) == "" and tw.review_hint(None) == "")

print("-- the tooltip --")
a, b = progress(monitor="Monitor1"), progress(monitor="Monitor2", seen=201)
counts = {"Monitor1": countdown(running=435.0), "Monitor2": countdown(monitor="Monitor2")}
text = tw.tooltip([a, b], a, counts)
lines = text.split("\n")
check("one line per monitor, the lead marked", len(lines) == 2 and lines[0].startswith("▸ Monitor1")
      and lines[1].startswith("  Monitor2"))
check("with the count and when the next change is",
      lines[0] == "▸ Monitor1 · 4 of 201 shown · next in 2:45")
check("a finished playlist has no next change", lines[1] == "  Monitor2 · all 201 shown")
check("a paused timer says so", "(paused)" in tw.tooltip([a], a, {"Monitor1": countdown(paused=True)}))
check("it fits what Windows shows", len(text) <= tw.TOOLTIP_LIMIT)
many = [progress(monitor=f"Monitor{n}") for n in range(1, 9)]
long_text = tw.tooltip(many, many[0], {m.monitor: countdown(monitor=m.monitor) for m in many})
check("with many monitors whole lines are dropped, not cut in half",
      len(long_text) <= tw.TOOLTIP_LIMIT and all(l.strip().endswith(("shown", ")")) or "next" in l
                                                  for l in long_text.split("\n")))
check("no playlist shows the error, or says none was found",
      tw.tooltip([], None, {}, "no config") == "no config" and tw.tooltip([], None, {}) == "No playlist found")

print("-- the two balloons --")
check("Playlist finished: the design's copy with the real numbers",
      tw.finished_balloon("Monitor1", 201, 1000)
      == ("Playlist finished",
          f"All 201 wallpapers on Monitor1 have been shown. Rotate to swap in 1{NB}000 folders from the reserve."))
check("the clause goes when the batch is not known",
      tw.finished_balloon("Monitor1", 201) == ("Playlist finished", "All 201 wallpapers on Monitor1 have been shown.")
      and tw.finished_balloon("Monitor1", 201, 0)[1] == "All 201 wallpapers on Monitor1 have been shown.")
check("Playlist started over: the design's copy with the real numbers",
      tw.restarted_balloon("Monitor1", 1, 201)
      == ("Playlist started over",
          "Monitor1 is back at wallpaper 1 of 201 without a rotation — Wallpaper Engine "
          "restarted or the playlist was rebuilt. The count starts again."))
check("at nothing shown yet it says the start, not wallpaper 0",
      "back at the start of its 201 wallpapers" in tw.restarted_balloon("Monitor2", 0, 201)[1])

print("-- once per cycle, as ever --")
import app.tracker_tray as tray_mod                                         # noqa: E402


class Said(SimpleNamespace):
    def _say_finished(self, monitor, total):
        self.finished.append((monitor, total))

    def _say_restarted(self, p):
        self.restarted.append(p.monitor)


def announce(results_, said=None):
    said = said or Said(results=results_, completed=set(), restarts_told=set(),
                        finished=[], restarted=[])
    said.results = results_
    tray_mod.TrackerTray._announce_completions(said)
    return said


said = announce([progress(seen=201, cycle_id="x")])
check("a playlist shown to its end says so once", said.finished == [("Monitor1", 201)])
announce(said.results, said)
announce(said.results, said)
check("and not on the next look, nor the one after", said.finished == [("Monitor1", 201)])
said = announce([progress(seen=100, cycle_id="y")])
check("one still going says nothing", said.finished == [] and said.restarted == [])
now = datetime.now().strftime(TIME_FMT)
said = announce([progress(cycle_id="z", restarted_at=now, restarted_from="81/195", previous_id="old")])
check("a playlist Wallpaper Engine started over says so", said.restarted == ["Monitor1"] and said.finished == [])
announce(said.results, said)
check("once", said.restarted == ["Monitor1"])
said = announce([progress(cycle_id="z2", restarted_at=now, restarted_from="195/195", previous_id="old2")])
check("a pass that ran to its end before the restart is finished news, with that pass's total",
      said.finished == [("Monitor1", 195)] and said.restarted == [])
announce(said.results, said)
check("and told once", said.finished == [("Monitor1", 195)])
stale = (datetime.now() - timedelta(minutes=16)).strftime(TIME_FMT)
said = announce([progress(cycle_id="z3", restarted_at=stale, restarted_from="81/195", previous_id="o3")])
check("a restart from before the tray was running is not news", said.restarted == [] and said.finished == [])

print("-- what the menu reads off the disk --")
from app.engines.rotator import config as rconfig                           # noqa: E402

real_paths = (rconfig.CONFIG_PATH, rconfig.HISTORY_PATH)
rconfig.CONFIG_PATH = TMP / "config.json"
rconfig.HISTORY_PATH = TMP / "history.json"
check("no Rotator settings: the batch is not known (its defaults are not the user's choice)",
      tw.rotation_batch() is None)
rconfig.CONFIG_PATH.write_text(json.dumps({"count": 750}), encoding="utf-8")
check("the batch is what the settings say", tw.rotation_batch() == 750)
for bad in ('{"count": 0}', '{"count": true}', '{"count": "9"}', "[]", "{not json"):
    rconfig.CONFIG_PATH.write_text(bad, encoding="utf-8")
    check(f"{bad!r} is not a batch", tw.rotation_batch() is None)
check("with no history the next run is the first", tw.next_run_number() == 1)
rconfig.HISTORY_PATH.write_text(json.dumps({"runs": [
    {"id": "b", "timestamp": "2026-09-20T09:00:00"}, {"id": "a", "timestamp": "2026-09-19T09:00:00"}]}),
    encoding="utf-8")
check("with two runs it is the third", tw.next_run_number() == 3)
before = rconfig.HISTORY_PATH.read_text(encoding="utf-8")
rconfig.HISTORY_PATH.write_text("{broken", encoding="utf-8")
check("a history that cannot be read is not guessed at", tw.next_run_number() is None)
check("and is left exactly as it was (a counter does not put it right)",
      rconfig.HISTORY_PATH.read_text(encoding="utf-8") == "{broken" and not list(TMP.glob("history.unreadable*")))
rconfig.CONFIG_PATH, rconfig.HISTORY_PATH = real_paths

review = TMP / "data" / "review_last.json"
check("no scan yet: nothing is waiting, as far as anyone knows", tw.review_waiting() is None)
review.write_text(json.dumps({"scanned": "2026-09-18T09:10:00", "items": 9, "authors": [
    {"name": "a", "new": 3, "done": True}, {"name": "b", "new": 3, "done": False},
    {"name": "c", "new": 3}]}), encoding="utf-8")
check("authors not gone through yet are waiting", tw.review_waiting() == 2)
review.write_text(json.dumps({"scanned": "2026-09-18T09:10:00", "finished": "2026-09-18T10:00:00",
                              "authors": [{"name": "a", "new": 3, "done": False}]}), encoding="utf-8")
check("a finished review has none waiting (as the sidebar's badge)", tw.review_waiting() is None)
review.write_text("nope", encoding="utf-8")
check("a file that does not read is not guessed at", tw.review_waiting() is None)

print("-- the menu --")
HINTS = dict(run_hint=tw.run_hint(39), review_hint=tw.review_hint(12))
model = tm.build_model(ti.RUNNING, 0.62, 62, "tracking · 4 of 201 shown", **HINTS)
check("five rows in three groups, in the design's order",
      model.keys() == ["open", "rotate", "review", "settings", "quit"]
      and [len(g) for g in model.groups] == [1, 2, 2])
check("the design's labels and hints",
      [(r.label, r.hint) for r in model.rows()]
      == [("Open Toolkit", "Enter"), ("Rotate now…", "run 39"), ("Review", "12 waiting"),
          ("Settings", ""), ("Quit", "")])
check("the header is the display name and the icon's words",
      (model.title, model.line) == ("Toolkit", "tracking · 4 of 201 shown"))
for state_, fraction_, number_, line_ in (
        (ti.RUNNING, 0.5, 2, "tracking · 4 of 201 shown"), (ti.PAUSED, 0.5, 2, "paused · 4 of 201 shown"),
        (ti.UNKNOWN, None, None, "no playlist found"), (ti.FINISHED, 1.0, 100, "finished · all 201 shown")):
    m = tm.build_model(state_, fraction_, number_, line_, **HINTS)
    check(f"{state_}: the same five rows; the header carries the state",
          m.keys() == model.keys() and m.state == state_ and m.line == line_)
check("with no history or scan read, the hints are bare",
      [r.hint for r in tm.build_model(ti.RUNNING, 0.5, 2, "x").rows()] == ["Enter", "", "", "", ""])

menu = tm.TrayMenu()
menu.set_model(model)
check("the menu is that, with a divider after the header and between the groups",
      menu.texts() == ["Toolkit  ·  tracking · 4 of 201 shown", "-", "Open Toolkit  ·  Enter", "-",
                       "Rotate now…  ·  run 39", "Review  ·  12 waiting", "-", "Settings", "Quit"])
chosen: list[str] = []
menu.chosen.connect(chosen.append)
by_data = {a.data(): a for a in menu.actions() if a.data()}
by_data["rotate"].trigger()
by_data["quit"].trigger()
by_data["header"].trigger()
check("a row says which it was; the header is no row", chosen == ["rotate", "quit"]
      and not by_data["header"].isEnabled())
menu.set_model(tm.build_model(ti.PAUSED, 0.5, 2, "paused · 4 of 201 shown"))
check("filling it again replaces the rows", menu.texts().count("-") == 3 and len(menu.actions()) == 9
      and menu.texts()[0].endswith("paused · 4 of 201 shown"))
menu.set_model(model)
menu.popup(QPoint(60, 60))
app.processEvents()
app.processEvents()
want_height = (2 * theme.TRAY_MENU_PAD + theme.TRAY_HEADER_HEIGHT + 3 * theme.TRAY_DIVIDER_HEIGHT
               + 5 * theme.TRAY_ROW_HEIGHT)
grab = menu.grab().toImage()
check("the popup is 268 px wide and as tall as its rows",
      (menu.width(), menu.height()) == (theme.TRAY_MENU_WIDTH, want_height))
check("it paints: the panel on the overlay colour, clear corners",
      grab.pixelColor(grab.width() // 2, 4).alpha() > 150 and grab.pixelColor(0, 0).alpha() < 60)
check("Open Toolkit starts highlighted, for Enter", menu.activeAction() is not None
      and menu.activeAction().data() == "open")
menu.hide()
from app.ui.kit import icons as kit_icons                                  # noqa: E402

check("the four drawings the menu embeds are the kit's, word for word",
      all(tm.ICON_BODIES[n] == kit_icons._BODIES[n] for n in tm.ICON_BODIES)
      and set(tm.ICON_BODIES) == {r.icon for r in model.rows()})
check("on the same frame", tm._SVG.split("{}")[0] == kit_icons._HEAD)
shot = tm.icon_pixmap("close", theme.color("text.mid"), 16, 1.0).toImage()
check("an icon renders in its colour", solid(shot.convertToFormat(QImage.Format_ARGB32)) > 10)
check("and is cached", tm.icon_pixmap("close", theme.color("text.mid"), 16, 1.0)
      is tm.icon_pixmap("close", theme.color("text.mid"), 16, 1.0))
check("the tray runs without the stylesheet", app.styleSheet() == "")

print("-- the tray itself, with the feed, the clock and the shell's icon stood in --")
from PySide6.QtCore import QObject, Signal                                  # noqa: E402
from PySide6.QtWidgets import QSystemTrayIcon                               # noqa: E402
from app import window_instance                                             # noqa: E402


class FakeFeed(QObject):
    updated = Signal()
    config_changed = Signal()

    def __init__(self, *args, **kwargs):
        super().__init__()
        self.results: list = []
        self.following = True
        self.files = None
        self.tracker = SimpleNamespace(config_path=None, error="")


class FakeClock:
    def __init__(self):
        self.now: dict = {}
        self.log = None

    def tick(self):
        return self.now


class FakeTrayIcon(QObject):
    activated = Signal(object)
    messageClicked = Signal()
    Trigger, DoubleClick, Warning = (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick,
                                     QSystemTrayIcon.Warning)

    def __init__(self, icon=None):
        super().__init__()
        self.icons = [icon]
        self.tip = ""
        self.messages: list[tuple] = []

    def setIcon(self, icon):                                          # noqa: N802 - Qt's name
        self.icons.append(icon)

    def setToolTip(self, tip):                                        # noqa: N802
        self.tip = tip

    def toolTip(self):                                                # noqa: N802
        return self.tip

    def setContextMenu(self, menu):                                   # noqa: N802
        self.menu = menu

    def show(self):
        pass

    def showMessage(self, title, body, icon=None, msecs=0):           # noqa: N802
        self.messages.append((title, body, icon))


clock = FakeClock()
real = (tray_mod.TrackerFeed, tray_mod.WallpaperTimer, tray_mod.QSystemTrayIcon,
        tw.rotation_batch, tw.next_run_number, tw.review_waiting)
tray_mod.TrackerFeed = FakeFeed
tray_mod.WallpaperTimer = lambda *a, **k: clock
tray_mod.QSystemTrayIcon = FakeTrayIcon
tw.rotation_batch, tw.next_run_number, tw.review_waiting = (lambda: 1000), (lambda: 39), (lambda: 12)
try:
    tray = tray_mod.TrackerTray(app)
finally:
    (tray_mod.TrackerFeed, tray_mod.WallpaperTimer, tray_mod.QSystemTrayIcon) = real[:3]
tray.clock_timer.stop()
shell = tray.icon
check("it starts as unknown, in the display name", len(shell.icons) == 1 and shell.tip == "Toolkit")
# The real switch is whatever this machine says (a CI runner may well be light):
# the rest of this section starts from a dark taskbar of its own.
tray.taskbar._read = lambda: 0
tray.taskbar.refresh()

lead = progress()
tray.feed.results = [lead]
tray._on_update()                       # the feed has looked: the tray takes the reading
check("before the timer is read the ring is unknown, with the count in it",
      tray._icon_key[0] == ti.UNKNOWN and tray._icon_key[2] == 2
      and shell.tip == "▸ Monitor1 · 4 of 201 shown")
drawn = len(shell.icons)
clock.now = {"Monitor1": countdown(running=150.0)}
tray._tick_clock()
check("a counting playlist draws the ring and says where it is",
      len(shell.icons) == drawn + 1 and tray._icon_key[0] == ti.RUNNING
      and shell.tip == "▸ Monitor1 · 4 of 201 shown · next in 7:30")
drawn = len(shell.icons)
clock.now = {"Monitor1": countdown(running=151.0)}
tray._tick_clock()
check("a second further on is the same 2-degree step: no new icon", len(shell.icons) == drawn)
clock.now = {"Monitor1": countdown(running=170.0)}
tray._tick_clock()
check("a few seconds further on crosses a step: a new icon, and the tooltip moves",
      len(shell.icons) == drawn + 1 and shell.tip.endswith("next in 7:10"))
drawn = len(shell.icons)
clock.now = {"Monitor1": countdown(running=170.0, paused=True)}
tray._tick_clock()
check("pausing draws the grey ring", len(shell.icons) == drawn + 1
      and tray._icon_key[0] == ti.PAUSED and shell.tip.endswith("(paused)"))
drawn = len(shell.icons)
tray.taskbar._read = lambda: 1
tray.taskbar.refresh()
check("Windows going light redraws the icon in its light colours",
      len(shell.icons) == drawn + 1 and tray._icon_key[-1] is True)
tray.taskbar._read = lambda: 0
tray.taskbar.refresh()

tray._rebuild_menu()
check("the menu is built when it opens, with the numbers read then",
      tray.menu.texts() == ["Toolkit  ·  paused · 4 of 201 shown", "-", "Open Toolkit  ·  Enter", "-",
                            "Rotate now…  ·  run 39", "Review  ·  12 waiting", "-", "Settings", "Quit"])
check("the tray builds no menu until it is asked to",
      tray_mod.TrackerTray._rebuild_menu.__code__.co_names.count("autostart") == 0)

opened: list[tuple] = []
real_show = window_instance.ask_to_show
window_instance.ask_to_show = lambda tab, wait=0, command=None: opened.append((tab, command)) or True
tray.menu.chosen.emit("rotate")
check("choosing Rotate now… asks the window for the Rotator's question",
      opened == [("Rotator", "rotate:confirm")])

lead.seen = 201
tray.feed.results = [lead]
tray._on_update()
check("when the last wallpaper is shown there is one balloon, with the ring as its picture",
      len(shell.messages) == 1 and shell.messages[0][0] == "Playlist finished"
      and shell.messages[0][1] == f"All 201 wallpapers on Monitor1 have been shown. "
                                  f"Rotate to swap in 1{NB}000 folders from the reserve."
      and shell.messages[0][2] is not None and not shell.messages[0][2].isNull())
check("and the icon is the finished one", tray._icon_key[0] == ti.FINISHED)
tray._on_update()
check("which is not said again", len(shell.messages) == 1)
del opened[:]
shell.messageClicked.emit()
check("a click on it opens the Rotator", opened == [("Rotator", None)])

restart = progress(cycle_id="again", seen=1, restarted_at=datetime.now().strftime(TIME_FMT),
                   restarted_from="81/195", previous_id="before")
tray.feed.results = [restart]
tray._on_update()
check("Wallpaper Engine starting a playlist over is the other balloon",
      len(shell.messages) == 2 and shell.messages[1][0] == "Playlist started over"
      and "back at wallpaper 1 of 201" in shell.messages[1][1])
del opened[:]
shell.messageClicked.emit()
check("and a click on that opens the Tracker", opened == [("Tracker", None)])
window_instance.ask_to_show = real_show
tray.feed.results = []
tray.feed.tracker.error = "config.json not found"
tray._on_update()
tray._tick_clock()
check("with no playlist the tooltip is the tracker's error and the icon asks",
      shell.tip == "config.json not found" and tray._icon_key[0] == ti.UNKNOWN)
tray.taskbar.changed.disconnect()
tw.rotation_batch, tw.next_run_number, tw.review_waiting = real[3:]

print("-- the tray stays light --")
code = ("import sys, run_app, app.tracker_tray, app.tray_menu; "
        "bad = sorted(m for m in sys.modules if m.startswith(('app.ui', 'app.pages', 'app.main_window'))); "
        "print(bad)")
done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                      cwd=str(Path(__file__).resolve().parent.parent),
                      env={**os.environ, "WALLPAPER_TOOLKIT_DATA": str(TMP / "data2")})
check("importing the tray loads no kit, no pages and no window",
      done.returncode == 0 and done.stdout.strip() == "[]")

print()
print("PASSED %d/%d" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
