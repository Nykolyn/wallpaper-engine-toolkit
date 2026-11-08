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
LIVE = [f"p{i:03}" for i in range(194)]
GONE = [f"gone{i}" for i in range(7)]
ITEMS = LIVE[:100] + GONE + LIVE[100:]                # 201, as WE lists them
OLD = LIVE[:168]                                      # shown before the reset
SINCE = {"p168": "2026-11-08 18:34:33", "p169": "2026-11-08 18:45:18"}


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
                       changes=2, current="p169", missing=list(GONE),
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

deck = pr.plan_deck(ITEMS, r.shown, GONE, "p169")
check("plan keeps the current one out and drops shown and missing",
      deck == LIVE[170:] and len(deck) == 24 and "p169" not in deck)

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
        wt.StateMonitor("Monitor1", 168, "p169", list(LIVE), 0, [0] * len(LIVE)),
    ])])


before = wt.read_state_file(state_file())[0].monitors
after = wt.read_state_file(pr.rewrite_state(state_file(), "Monitor1", "p169", deck))[0].monitors
check("rewrite touches only that monitor",
      after[0] == before[0] and after[1].current == "p169" and after[1].waiting == deck
      and after[1].marks == [0] * 24 and after[1].instance == 168)
try:
    pr.rewrite_state(state_file(), "Monitor9", "x", [])
    check("rewrite refuses an unknown monitor", False)
except ValueError:
    check("rewrite refuses an unknown monitor", True)


print()
print("PASSED %d/%d" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
