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


print()
print("PASSED %d/%d" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
