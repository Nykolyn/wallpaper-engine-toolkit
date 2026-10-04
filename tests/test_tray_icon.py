"""The tray: its icon, its words, its menu and its two balloons.

    .venv\\Scripts\\python.exe tests\\test_tray_icon.py

The tray is a separate process the logon task starts, so what is checked here
is what it can be checked on without one: the mark drawn per state and size
(its fill by sampling pixels of the two frames), the copy with a number
known and not, the menu's composition and what each row asks the window for, and that none of it needs the kit. Run offscreen.
"""
from __future__ import annotations

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
    return ti.mark_pixmap(state, size, **kw).toImage().convertToFormat(QImage.Format_ARGB32)


def near(got: QColor, want: str, slack=40) -> bool:
    w = theme.color(want)
    return (got.alpha() > 200 and abs(got.red() - w.red()) <= slack
            and abs(got.green() - w.green()) <= slack and abs(got.blue() - w.blue()) <= slack)


def solid(img: QImage) -> int:
    return sum(img.pixelColor(x, y).alpha() > 0
               for x in range(img.width()) for y in range(img.height()))


def painted(img: QImage, colour: str, box=None, slack=40) -> int:
    """How many pixels (in `box`, x0, y0, x1, y1) are in a colour."""
    x0, y0, x1, y1 = box or (0, 0, img.width(), img.height())
    return sum(near(img.pixelColor(x, y), colour, slack)
               for x in range(x0, x1) for y in range(y0, y1))


# At 32 px the mark spans the icon less half a pixel each side: the back
# frame's left stroke runs down x = 1, its top-right corner is clear of the
# front frame at (20, 8), the front frame's body spans x = 11..29 (its right
# side clear of the badge at y = 14), and the badge sits round (25, 25).
BACK_LEFT, BACK_RIGHT, FRONT_LEFT, FRONT_RIGHT = (1, 10), (20, 8), (13, 20), (27, 14)
FRONT_INSIDE = (12, 14, 29, 18)          # the front body inside its edge, above the badge
BADGE = (int(25 - 0.2 * 32), int(25 - 0.2 * 32), 32, 32)
FILL = "tray.fill.running"


def at(img: QImage, xy) -> QColor:
    return img.pixelColor(*xy)


print("-- geometry --")
check("every size Windows may ask for is drawn", ti.SIZES == (16, 20, 24, 32, 40, 48))
check("the mark spans the icon's width, as the tray's other icons do: a step a pixel",
      [ti.fill_steps(s) for s in ti.SIZES] == list(ti.SIZES) and ti.FILL_STEPS == 48)
check("and is centred top to bottom",
      all(abs(ti.origin(s)[1] + ti.TOP * ti.scale(s)
              - (s - ti.origin(s)[1] - ti.BOTTOM * ti.scale(s))) < 1e-9 for s in ti.SIZES))
check("the scale runs from the back frame's left edge to the front frame's right edge",
      (ti.FILL_FROM, ti.FILL_TO) == (11.0, 52.5))
check("a fill is snapped to its steps, clamped to 0..1",
      ti.snap(0.5004, 10) == 0.5 and ti.snap(1.4) == 1.0 and ti.snap(-1) == 0.0
      and ti.snap(None) is None)
check("the fill's edge is on a whole pixel",
      all(float(ti.fill_edge(s, f)).is_integer() for s in ti.SIZES for f in (0, 0.13, 0.5, 0.77, 1)))

print("-- each state at each size --")
for state in ti.STATES:
    ok = True
    for size in ti.SIZES:
        pix = ti.mark_pixmap(state, size, fill=0.62)
        img = pix.toImage().convertToFormat(QImage.Format_ARGB32)
        ok = ok and (pix.width(), pix.height()) == (size, size) and solid(img) > size \
            and img.pixelColor(0, 0).alpha() == 0 and img.pixelColor(size - 1, 0).alpha() == 0
    check(f"{state}: drawn at every size, the right size, with clear corners", ok)
icon = ti.tray_icon(ti.RUNNING, 0.5)
check("one QIcon holds them all, so Windows can pick at 150 percent",
      sorted(s.width() for s in icon.availableSizes()) == list(ti.SIZES))
check("and asking it for 24 gets the 24 that was drawn",
      icon.pixmap(24, 24).size().width() == 24 and not icon.pixmap(24, 24).isNull())
try:
    ti.mark_pixmap("sleepy", 16)
    check("a state that is none is refused", False)
except ValueError:
    check("a state that is none is refused", True)

print("-- the fill --")
empty, fifth, half, most, full = (image(ti.RUNNING, 32, fill=f) for f in (0.0, 0.2, 0.5, 0.75, 1.0))
check("empty: no colour, the frames grey and the front body dark",
      painted(empty, FILL) == 0 and near(at(empty, BACK_LEFT), "tray.frame")
      and near(at(empty, FRONT_RIGHT), "tray.body"))
check("it fills from the left: at 20 % the back frame's left edge, not the front frame",
      near(at(fifth, BACK_LEFT), FILL) and not near(at(fifth, FRONT_LEFT), FILL)
      and near(at(fifth, FRONT_RIGHT), "tray.body"))
check("at half it has reached into the front frame, not across it",
      near(at(half, FRONT_LEFT), FILL) and near(at(half, FRONT_RIGHT), "tray.body"))
check("more time gone is more colour",
      0 < painted(fifth, FILL) < painted(half, FILL) < painted(most, FILL) < painted(full, FILL))
check("both frames filled whole is 100 %: nothing grey or dark is left",
      near(at(full, BACK_LEFT), FILL) and near(at(full, BACK_RIGHT), FILL)
      and near(at(full, FRONT_LEFT), FILL) and near(at(full, FRONT_RIGHT), FILL)
      and painted(full, "tray.body", FRONT_INSIDE, slack=6) == 0)
check("anything short of it leaves some unfilled",
      painted(image(ti.RUNNING, 32, fill=0.8), "tray.body", FRONT_INSIDE, slack=6) > 0)
check("the same step draws the same icon",
      image(ti.RUNNING, 16, fill=0.501) == image(ti.RUNNING, 16, fill=0.5))
check("another step draws another",
      image(ti.RUNNING, 16, fill=0.6) != image(ti.RUNNING, 16, fill=0.5))
check("running has no badge", painted(half, "tray.fill.paused", BADGE) == 0
      and painted(half, "tray.fill.unknown", BADGE) == 0 and painted(half, "tray.fill.ok", BADGE) == 0)

print("-- the other states --")
paused = image(ti.PAUSED, 32, fill=0.5)
check("paused keeps the fill where it was, in cold grey",
      painted(paused, FILL) == 0 and near(at(paused, FRONT_LEFT), "tray.fill.paused")
      and near(at(paused, FRONT_RIGHT), "tray.body"))
check("with a grey pause badge", painted(paused, "tray.fill.paused", BADGE) > 20
      and painted(paused, "tray.badge.pause", BADGE, slack=60) > 4)
unknown = image(ti.UNKNOWN, 32, fill=0.5)
check("unknown has no fill, whatever it was told, and is muted",
      painted(unknown, FILL) == 0 and painted(unknown, "tray.fill.paused") == 0
      and 0 < at(unknown, BACK_LEFT).alpha() < 200)
check("with an amber ? badge", painted(unknown, "tray.fill.unknown", BADGE) > 20
      and painted(unknown, "tray.badge.glyph", BADGE, slack=60) > 2)
finished = image(ti.FINISHED, 32, fill=0.1)
check("finished is both frames green, whatever it was told",
      near(at(finished, BACK_LEFT), "tray.fill.ok") and near(at(finished, BACK_RIGHT), "tray.fill.ok")
      and near(at(finished, FRONT_LEFT), "tray.fill.ok"))
check("with a green tick badge", painted(finished, "tray.badge.glyph", BADGE, slack=60) > 2)
check("the badge reads at 16 px too", painted(image(ti.UNKNOWN, 16), "tray.fill.unknown") > 4
      and painted(image(ti.PAUSED, 16, fill=0.5), "tray.fill.paused") > 4)
check("the colours are the design's",
      [theme.color(f"tray.fill.{n}").name().upper() for n in ("running", "paused", "ok", "unknown")]
      == ["#4C8DFF", "#7C879C", "#3DD68C", "#E8A33D"])
check("the palette is the theme's", ti.palette().running == theme.color(FILL)
      and ti.palette().fill(ti.FINISHED) == theme.color("tray.fill.ok"))


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


check("a counting timer is running, filled with the share of the delay gone",
      tw.icon_state(progress(), countdown()) == (ti.RUNNING, 0.25))
check("empty just after a change, full the moment the next is due",
      tw.icon_state(progress(), countdown(running=0.0)) == (ti.RUNNING, 0.0)
      and tw.icon_state(progress(), countdown(running=600.0)) == (ti.RUNNING, 1.0))
check("a paused one is paused, the fill kept",
      tw.icon_state(progress(), countdown(paused=True)) == (ti.PAUSED, 0.25))
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
check("one line per monitor, the lead marked", len(lines) == 2 and lines[0].startswith("▸ 2%")
      and lines[1].startswith("  100%"))
check("the playlist's percentage first, then the count and when the next change is",
      lines[0] == "▸ 2% · Monitor1 · 4 of 201 shown · next in 2:45")
check("a finished playlist is 100 % and says so, with no next change",
      lines[1] == "  100% · Monitor2 · 201 of 201 shown · finished")
check("the design's example", tw.tooltip_line(
      progress(monitor="Monitor1", seen=81, total=192), countdown(running=600 - 269.0), lead=True)
      == "▸ 42% · Monitor1 · 81 of 192 shown · next in 4:29")
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
FRESH = {"never_used": 4210, "batch": 1000, "will_reset": False}
check("with a fresh count of the reserve, it says how many have never been used",
      tw.finished_balloon("Monitor1", 201, 1000, FRESH)[1]
      == f"All 201 wallpapers on Monitor1 have been shown. Rotate to swap in 1{NB}000 of the "
         f"4{NB}210 folders that have never been used.")
check("and when too few are left, that the run draws from the whole reserve again",
      tw.finished_balloon("Monitor1", 201, 1000, {**FRESH, "never_used": 640, "will_reset": True})[1]
      == f"All 201 wallpapers on Monitor1 have been shown. Rotate to swap in 1{NB}000: only 640 "
         f"folders were never used, so the run draws from the whole reserve again.")
check("a count made for another batch size is not said",
      tw.finished_balloon("Monitor1", 201, 750, FRESH)[1].endswith("750 folders from the reserve."))
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

import time                                                                  # noqa: E402

from app.services import snapshot as snap                                     # noqa: E402

print("-- the reserve's count, as the window left it --")
check("nothing left yet: not said", tw.never_used() is None)
now = time.time()
counted = snap.ReserveCounts(folders=9000, never_used=4210, will_reset=False, batch=1000, path="R:")
snap.remember_reserve(TMP / "data", counted, now=now - 60)
left = TMP / "data" / snap.RESERVE_COUNT_FILE
check("the window leaves it whole, with when it was counted",
      json.loads(left.read_text(encoding="utf-8"))["never_used"] == 4210
      and not list((TMP / "data").glob("*.tmp")))
check("a fresh count is read back", tw.never_used(now) == {"never_used": 4210, "batch": 1000,
                                                          "will_reset": False})
check("a day-old one is not", tw.never_used(now + tw.RESERVE_FRESH_SECONDS) is None)
real_history = rconfig.HISTORY_PATH
rconfig.HISTORY_PATH = TMP / "history_after.json"
rconfig.HISTORY_PATH.write_text("{}", encoding="utf-8")
check("nor one counted before the last rotation", tw.never_used(now) is None)
rconfig.HISTORY_PATH = real_history
left.write_text("{half", encoding="utf-8")
check("and a damaged file is not guessed at", tw.never_used(now) is None)
left.unlink()

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
    failed = Signal(str)

    def __init__(self, *args, **kwargs):
        super().__init__()
        self.results: list = []
        self.following = True
        self.files = None
        self.config_path = "X:/we/config.json"
        self.error = ""


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

lead = progress()
tray.feed.results = [lead]
tray._on_update()                       # the feed has looked: the tray takes the reading
check("before the timer is read the icon is unknown; the tooltip has the playlist",
      tray._icon_key == (ti.UNKNOWN, None)
      and shell.tip == "▸ 2% · Monitor1 · 4 of 201 shown")
drawn = len(shell.icons)
clock.now = {"Monitor1": countdown(running=150.0)}
tray._tick_clock()
check("a counting playlist fills the icon and says where it is",
      len(shell.icons) == drawn + 1 and tray._icon_key == (ti.RUNNING, round(0.25 * ti.FILL_STEPS))
      and shell.tip == "▸ 2% · Monitor1 · 4 of 201 shown · next in 7:30")
drawn = len(shell.icons)
clock.now = {"Monitor1": countdown(running=151.0)}
tray._tick_clock()
check("a second further on is the same step: no new icon", len(shell.icons) == drawn)
clock.now = {"Monitor1": countdown(running=170.0)}
tray._tick_clock()
check("a few seconds further on crosses a step: a new icon, and the tooltip moves",
      len(shell.icons) == drawn + 1 and shell.tip.endswith("next in 7:10"))
drawn = len(shell.icons)
clock.now = {"Monitor1": countdown(running=170.0, paused=True)}
tray._tick_clock()
check("pausing draws the grey fill", len(shell.icons) == drawn + 1
      and tray._icon_key[0] == ti.PAUSED and shell.tip.endswith("(paused)"))

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
check("when the last wallpaper is shown there is one balloon, with the icon as its picture",
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
tray.feed.error = "config.json not found"
tray._on_update()
tray._tick_clock()
check("with no playlist the tooltip is the tracker's error and the icon asks",
      shell.tip == "config.json not found" and tray._icon_key[0] == ti.UNKNOWN)
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
