"""Restoring a pass Wallpaper Engine threw away, checked without Wallpaper Engine.

Run it directly — there is no test framework in this project:

    .venv\\Scripts\\python.exe tests\\test_pass_restore.py

Everything works on a temporary tracker.json, a made-up playliststate.bin and
fake engine controls; nothing here reads or changes this machine's data.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.engines.tracker as tr                    # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="wallpaper_restore_test_"))
tr.STATE_PATH = TMP / "tracker.json"

results: list[bool] = []


def check(label: str, condition: bool) -> None:
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label)


# ---- the archive keeps what a thrown-away pass had shown -----------------------------

T1, T2, T3 = "2026-11-01 02:00:00", "2026-11-02 02:00:00", "2026-11-03 02:00:00"


def archived(reason, keep_shown):
    cycle = tr.Cycle(monitor="Monitor1", playlist="custom", started="2026-11-01 01:46:10",
                     items=["a", "b", "c"], seen={"a": T1, "b": T2, "x": T3},
                     missing=["c"])
    state = tr.TrackerState()
    state.archive_cycle(cycle, reason, keep_shown=keep_shown)
    return state.archive[0]


entry = archived(tr.RESET_REASON, True)
check("archive keeps shown on an engine reset", entry.get("shown") == {"a": T1, "b": T2})
entry = archived("shown to the end; Wallpaper Engine began the next pass", False)
check("archive keeps no list otherwise", "shown" not in entry)


# ---- what is restorable, and the deck it gets ----------------------------------------

import app.engines.pass_restore as pr               # noqa: E402
import app.engines.wallpaper_timer as wt            # noqa: E402
from datetime import datetime, timedelta            # noqa: E402

S, E = "2026-11-01 01:46:10", "2026-11-08 18:34:34"
START, END = datetime.strptime(S, tr.TIME_FMT), datetime.strptime(E, tr.TIME_FMT)
# Real files: the tracker's own look (at the end) asks which ones exist.
WP = TMP / "myprojects"
WP.mkdir()
LIVE = [(WP / f"p{i:03}.mp4").as_posix() for i in range(194)]
for path in LIVE:
    Path(path).write_bytes(b"")
GONE = [(WP / f"gone{i}.mp4").as_posix() for i in range(7)]
P168, P169 = LIVE[168], LIVE[169]
ITEMS = LIVE[:100] + GONE + LIVE[100:]                # 201, as WE lists them
OLD = LIVE[:168]                                      # shown before the reset
SINCE = {P168: "2026-11-08 18:34:33", P169: "2026-11-08 18:45:18"}


def inside(i: int) -> float:
    return (START + timedelta(minutes=10 * (i + 1))).timestamp()


def atimes(items):
    out = {item: inside(n) for n, item in enumerate(OLD)}
    out.update({i: (END + timedelta(minutes=5)).timestamp() for i in SINCE})
    out.update({i: (START - timedelta(days=3)).timestamp() for i in LIVE[170:]})
    return out


def reset_state(*, playlist="custom", items=None, shown=None, restored=False,
                previous="270bd142", reason=None):
    entry = {"id": "270bd142", "monitor": "Monitor1", "playlist": "custom",
             "started": S, "ended": E, "seen": 168, "total": 194, "changes": 175,
             "reason": reason or tr.RESET_REASON}
    if shown is not None:
        entry["shown"] = shown
    if restored:
        entry["restored"] = "2026-11-08 20:00:00"
    current = tr.Cycle(monitor="Monitor1", playlist=playlist, started="2026-11-08 18:34:33",
                       id="a33bfae7", items=list(items or ITEMS), seen=dict(SINCE),
                       changes=2, current=P169, missing=list(GONE),
                       restarted_at=E, restarted_from="168/194", previous_id=previous,
                       from_rotation=True)
    return tr.TrackerState({"Monitor1": current}, [entry])


window = pr.shown_from_atimes(["a", "b", "c"], {
    "a": (START - timedelta(seconds=1)).timestamp(), "b": inside(0),
    "c": END.timestamp()}, START, END)
check("atimes pick only the pass window",
      window == {"b": datetime.fromtimestamp(inside(0)).strftime(tr.TIME_FMT)})

r, why = pr.find_restorable(reset_state(), "Monitor1", atimes)
check("today's pass is restorable from access times",
      r is not None and r.source == "access times" and r.seen == 170 and r.total == 194)

called = []
saved = {i: S for i in OLD}
r2, _ = pr.find_restorable(reset_state(shown=saved), "Monitor1",
                           lambda items: called.append(1) or {})
check("saved list wins over access times",
      r2 is not None and r2.source == "saved" and r2.seen == 170 and not called)

deck = pr.plan_deck(ITEMS, r.shown, GONE, P169)
check("plan keeps the current one out and drops shown and missing",
      deck == LIVE[170:] and len(deck) == 24 and P169 not in deck)

changed = "The playlist has changed since the reset."
check("refuses a renamed playlist",
      pr.find_restorable(reset_state(playlist="other"), "Monitor1", atimes) == (None, changed))
swapped = [f"new{i}" for i in range(40)] + ITEMS[40:]
check("refuses a playlist that lost a fifth of the shown",
      pr.find_restorable(reset_state(items=swapped, shown=saved), "Monitor1", atimes)
      == (None, changed))
check("refuses a restored pass",
      pr.find_restorable(reset_state(restored=True), "Monitor1", atimes)
      == (None, "That pass was already restored."))
check("refuses a complete pass",
      pr.find_restorable(reset_state(shown={i: S for i in LIVE}), "Monitor1", atimes)
      == (None, "That pass had already been shown to the end."))
check("refuses without a reset",
      pr.find_restorable(reset_state(previous=None), "Monitor1", atimes)
      == (None, "Wallpaper Engine has not thrown a pass away on Monitor1."))
check("refuses a pass that ran to its end",
      pr.find_restorable(reset_state(reason="shown to the end; Wallpaper Engine began the "
                                            "next pass"), "Monitor1", atimes)[0] is None)
check("refuses an untracked monitor",
      pr.find_restorable(reset_state(), "Monitor7", atimes)
      == (None, "Nothing is tracked on Monitor7 yet."))
check("refuses without access times or a saved list",
      pr.find_restorable(reset_state(), "Monitor1", atimes, atime_ok=False)
      == (None, "No record of what that pass showed."))

back = pr.restored_cycle(r)
check("restored cycle carries the old id",
      back.id == "270bd142" and back.started == S and back.previous_id is None
      and back.restarted_at is None and len(back.seen) == 170 and back.changes == 177)


def state_file():
    return wt.write_state_file([wt.StateSection("wallpaperconfig", [
        wt.StateMonitor("Monitor0", 84, "s0", ["s1", "s2"], 0, [0, 0]),
        wt.StateMonitor("Monitor1", 168, P169, [i for i in ITEMS if i != P169], 0,
                        [0] * (len(ITEMS) - 1)),
    ])])


before = wt.read_state_file(state_file())[0].monitors
after = wt.read_state_file(pr.rewrite_state(state_file(), "Monitor1", P169, deck))[0].monitors
check("rewrite touches only that monitor",
      after[0] == before[0] and after[1].current == P169 and after[1].waiting == deck
      and after[1].marks == [0] * 24 and after[1].instance == 168)
try:
    pr.rewrite_state(state_file(), "Monitor9", "x", [])
    check("rewrite refuses an unknown monitor", False)
except ValueError:
    check("rewrite refuses an unknown monitor", True)


# ---- running a restore ------------------------------------------------------------

import json                                         # noqa: E402
import app.engines.engine_control as ec             # noqa: E402

INSTALL = TMP / "wallpaper_engine"
(INSTALL / "bin").mkdir(parents=True)
STATE = INSTALL / "bin" / "playliststate.bin"
CFG = INSTALL / "config.json"
DATA = TMP / "data"
pr.app_data_dir = lambda: DATA
calls: dict[str, int] = {}
answers = {"close": True, "start": True}


def fake(name):
    def call(*_args, **_kwargs):
        calls[name] = calls.get(name, 0) + 1
        return answers[name]
    return call


ec.running = lambda: ec.Engine(pid=1, exe=str(INSTALL / "wallpaper64.exe"))
ec.close = fake("close")
ec.start = fake("start")


def setup(**changes):
    calls.clear()
    answers.update({"close": True, "start": True, **changes})
    STATE.write_bytes(state_file())
    reset_state(shown=saved).save()


setup()
result = pr.restore("Monitor1")
on_disk = wt.read_state_file(STATE.read_bytes())[0].monitors
loaded = tr.TrackerState.load()
check("a restore rewrites the deck and gives the tracker its pass back",
      result.ok and result.seen == 170 and result.total == 194
      and result.message == "Pass restored on Monitor1: 170/194."
      and on_disk[1].waiting == LIVE[170:] and on_disk[0].waiting == ["s1", "s2"]
      and loaded.cycles["Monitor1"].id == "270bd142"
      and loaded.archive[0].get("restored")
      and (DATA / "pass-restore" / "playliststate.bin").is_file()
      and calls == {"close": 1, "start": 1})

setup(close=False)
bytes_before, json_before = STATE.read_bytes(), tr.STATE_PATH.read_text(encoding="utf-8")
result = pr.restore("Monitor1")
check("WE that does not close changes nothing",
      not result.ok and result.message == "Wallpaper Engine did not close, so nothing was changed."
      and STATE.read_bytes() == bytes_before
      and tr.STATE_PATH.read_text(encoding="utf-8") == json_before and "start" not in calls)

setup()
json_before = tr.STATE_PATH.read_text(encoding="utf-8")
real_rewrite = pr.rewrite_state
pr.rewrite_state = lambda *a: (_ for _ in ()).throw(ValueError("broken"))
result = pr.restore("Monitor1")
pr.rewrite_state = real_rewrite
check("WE is started even when the rewrite fails",
      not result.ok and calls.get("start") == 1
      and tr.STATE_PATH.read_text(encoding="utf-8") == json_before)

setup(start=False)
result = pr.restore("Monitor1")
check("a failed start names the backup", not result.ok and "pass-restore" in result.message)

setup()
refused = pr.restore("Monitor7")
check("a refusal never touches Wallpaper Engine",
      not refused.ok and refused.message == "Nothing is tracked on Monitor7 yet." and not calls)

# Review focus: whatever the tray's tracker does next, it follows the deck.
setup()
pr.restore("Monitor1")
CFG.write_text(json.dumps({"?installdirectory": "x", "steamuser": {"general": {
    "wallpaperconfig": {"selectedwallpapers": {"Monitor1": {"file": P169, "playlist": {
        "items": ITEMS, "name": "custom",
        "settings": {"delay": 10, "order": "random", "mode": "timer"}}}}}}}}),
    encoding="utf-8")
looks = {p.monitor: p for p in tr.Tracker(str(CFG)).poll()}
check("the tracker follows the restored deck",
      "Monitor1" in looks and looks["Monitor1"].seen == 170
      and looks["Monitor1"].total == 194 and looks["Monitor1"].cycle_id == "270bd142")


# ---- the automatic switch and its loop guard ---------------------------------------

import app.settings as app_settings                 # noqa: E402

app_settings.SETTINGS_PATH = TMP / "suite.json"
prefs = app_settings.Settings()
rotation = tr.Cycle(monitor="Monitor1", playlist="custom", started=S, from_rotation=True)
plain = tr.Cycle(monitor="Monitor0", playlist="subscribed", started=S)
check("auto is on by default for the rotation's playlist",
      pr.auto_enabled(prefs, "Monitor1", rotation) and not pr.auto_enabled(prefs, "Monitor0", plain)
      and not pr.auto_enabled(prefs, "Monitor2", None))
pr.set_auto(prefs, "Monitor1", False)
check("an explicit off beats the rotation default",
      not pr.auto_enabled(app_settings.Settings.load(), "Monitor1", rotation))

guard = pr.AutoGuard()
first = guard.allowed("Monitor1", 1000.0)
guard.mark("Monitor1", 1000.0)
check("the guard holds a monitor for ten minutes",
      first and not guard.allowed("Monitor1", 1599.0) and guard.allowed("Monitor1", 1600.0)
      and guard.allowed("Monitor0", 1001.0))

from app.services.activity import known_kind        # noqa: E402

check("the journal knows restores", known_kind("restore.clean") and known_kind("restore.failed"))


print()
print("PASSED %d/%d" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
