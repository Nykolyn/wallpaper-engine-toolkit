"""Tracker logic, checked without Qt, Wallpaper Engine or the real filesystem state.

Run it directly — there is no test framework in this project:

    .venv\\Scripts\\python.exe tests\\test_tracker.py

Everything here works on a temporary config.json, a temporary rotation history
and real files whose access times are forged with os.utime, so it says nothing
about the machine it runs on beyond needing NTFS access times for the two checks
that say so.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.engines.rotator.config as rot_config     # noqa: E402
import app.engines.tracker as tr                    # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="wallpaper_tracker_test_"))
tr.STATE_PATH = TMP / "tracker.json"
CFG = TMP / "config.json"
HIST = TMP / "history.json"
rot_config.HISTORY_PATH = HIST
NOW = datetime.now()

results: list[bool] = []


def check(label: str, condition: bool) -> None:
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label)


def write_cfg(items: list[str], name: str = "custom", delay: int = 10,
              order: str = "random", monitor: str = "Monitor0") -> None:
    CFG.write_text(json.dumps({
        "?installdirectory": "x",
        "steamuser": {"general": {"wallpaperconfig": {"selectedwallpapers": {
            monitor: {"file": items[0], "playlist": {
                "items": items, "name": name,
                "settings": {"delay": delay, "order": order, "mode": "timer"}}}}}}},
    }), encoding="utf-8")


def make_item(folder: str, days_ago: float) -> str:
    """A wallpaper file whose last-access time is `days_ago` in the past."""
    d = TMP / "myprojects" / folder
    d.mkdir(parents=True, exist_ok=True)
    f = d / "wallpaper.mp4"
    f.write_bytes(b"x")
    when = (NOW - timedelta(days=days_ago)).timestamp()
    os.utime(f, (when, when))
    return str(f).replace("\\", "/")


# The probe is the one thing that must be faked: these paths are not open.
playing = {"now": None}
tr.is_in_use = lambda path: path == playing["now"]


# ---------------------------------------------------------------- playlists

write_cfg(["W:/a/1.mp4", "W:/b/2.mp4"], name="custom", delay=7, order="linear")
view = tr.read_playlists(str(CFG))[0]
check("a playlist is read out of config.json",
      (view.monitor, view.name, view.total) == ("Monitor0", "custom", 2))
check("its timer settings come with it", (view.delay, view.order) == (7, "linear"))

CFG.write_text(json.dumps({"?installdirectory": "x", "steamuser": {"general": {}}}),
               encoding="utf-8")
check("a config with no playlist yields nothing", tr.read_playlists(str(CFG)) == [])


# --------------------------------------------------------------- divergence

check("identical lists are not diverged", not tr.diverged(["a", "b"], ["a", "b"]))
check("one item swapped is an edit", not tr.diverged(list("abcdefghij"), list("abcdefghiZ")))
check("a fully new list is a rotation", tr.diverged(list("abcde"), list("vwxyz")))
check("a half-new list is a rotation", tr.diverged(list("abcd"), ["a", "x", "y", "z"]))
check("an empty new list is not a rotation", not tr.diverged(list("abc"), []))


# ---------------------------------------------------------------- estimates

check("with nothing repeating the estimate is remaining * delay",
      tr.estimate_minutes(50, 10, changes=150, seen=150) == 500)
check("a finished playlist needs no more time",
      tr.estimate_minutes(0, 10, changes=150, seen=100) == 0)
check("no history yet falls back to remaining * delay",
      tr.estimate_minutes(50, 10, changes=0, seen=0) == 500)

# The regression that prompted this: 192 distinct wallpapers out of 193 displays
# is a shuffled playthrough. Modelling it as drawing with replacement turned
# 2h40m into 4d21h, so one stray repeat must barely move the number.
one_stray = tr.estimate_minutes(16, 10, changes=193, seen=192)
check("a single repeat in a long cycle barely moves the estimate",
      160 <= one_stray <= 175)
check("and it is nowhere near the coupon-collector answer", one_stray < 500)

check("wallpapers that really do come round again stretch it",
      tr.estimate_minutes(50, 10, changes=300, seen=150) == 1000)
check("a ratio from a handful of displays is not extrapolated",
      tr.estimate_minutes(1122, 10, changes=4, seen=3) == 11220)
check("the estimate never shrinks below remaining * delay",
      tr.estimate_minutes(50, 10, changes=10, seen=150) == 500)
check("zero minutes formats as a dash", tr.format_minutes(0) == "—")
check("90 minutes formats as hours", tr.format_minutes(90) == "1h 30m")
check("3000 minutes formats as days", tr.format_minutes(3000) == "2d 2h")
check("under a minute ago is not a dash", tr.elapsed_since(tr._now()) == "≤1m")
check("an unparseable stamp is a dash", tr.elapsed_since("nonsense") == "—")



# ---------------------------------------- projecting a finish from real pace

def snapshot(**over) -> tr.Progress:
    fields = dict(cycle_id="x", monitor="Monitor0", playlist="custom", seen=100,
                  total=200, changes=100, repeats=0, order="random", delay=10,
                  started="2026-01-01 00:00:00", current=None, current_title="",
                  current_since=None, live=True, inferred=0, anchor=tr.ANCHOR_NONE,
                  gone=0)
    fields.update(over)
    return tr.Progress(**fields)


# Ten hours in with half the playlist shown: the other half should land ten
# hours out, whatever the wallpaper timer says — the machine is off sometimes.
ten_hours_ago = (NOW - timedelta(hours=10)).strftime(tr.TIME_FMT)
projected = snapshot(started=ten_hours_ago, seen=100, total=200).finish_estimate
check("a finish is projected from the pace actually kept", projected is not None)
if projected:
    check("and it matches the pace rather than the timer",
          projected == (NOW + timedelta(hours=10)).strftime("%d %b %H:%M"))
check("nothing is projected before anything has been seen",
      snapshot(seen=0).finish_estimate is None)
check("nothing is projected from a handful of displays either",
      snapshot(started=ten_hours_ago, seen=3).finish_estimate is None)
check("nothing is projected for a finished playlist",
      snapshot(seen=200, total=200).finish_estimate is None)
check("nothing is projected without a usable start",
      snapshot(started="nonsense").finish_estimate is None)


# ------------------------------------------------------- a cycle, poll by poll

items = [f"W:/w/{i}/v.mp4" for i in range(10)]
write_cfg(items)
tracker = tr.Tracker(str(CFG))
tracker.atime_ok = False        # this section is about live watching only

playing["now"] = items[3]
p = tracker.poll()[0]
check("the first poll counts what is on screen", (p.seen, p.total, p.changes) == (1, 10, 1))

p = tracker.poll()[0]
check("the same wallpaper is not counted twice", (p.seen, p.changes) == (1, 1))

playing["now"] = items[7]
p = tracker.poll()[0]
check("a change advances the count", (p.seen, p.changes) == (2, 2))

playing["now"] = items[3]
p = tracker.poll()[0]
check("a repeat raises changes but not seen", (p.seen, p.changes, p.repeats) == (2, 3, 1))

playing["now"] = None
p = tracker.poll()[0]
check("with nothing playing the count freezes and the last item is kept",
      (p.seen, p.live, p.current) == (2, False, items[3]))

shown, remaining = tracker.split_items("Monitor0")
check("shown and remaining add up", (len(shown), len(remaining)) == (2, 8))
check("live entries are not marked as reconstructed", not any(i for _, _, i in shown))

write_cfg(items[:9] + ["W:/w/new/v.mp4"])
playing["now"] = items[3]
p = tracker.poll()[0]
check("editing the playlist keeps the cycle", (p.seen, p.total) == (2, 10))

fresh = [f"W:/w/r{i}/v.mp4" for i in range(12)]
write_cfg(fresh)
playing["now"] = fresh[0]
p = tracker.poll()[0]
check("a rotation starts the count over", (p.seen, p.total, p.changes) == (1, 12, 1))
check("the finished cycle is archived",
      len(tracker.archive()) == 1 and tracker.archive()[0]["seen"] == 2)

playing["now"] = fresh[5]
tracker.poll()
tracker.reset("Monitor0")
cycle = tracker.cycle("Monitor0")
check("a manual reset zeroes the count",
      (cycle.seen_count, cycle.changes, cycle.total) == (0, 0, 12))
check("a manual reset archives too", len(tracker.archive()) == 2)


# ------------------------------------------------------------ two writers

state = tr.TrackerState.load()
other = tr.TrackerState.load()
state.cycles["Monitor0"].seen["W:/w/r1/v.mp4"] = "2026-01-01 00:00:00"
state.cycles["Monitor0"].changes = 5
other.cycles["Monitor0"].seen["W:/w/r2/v.mp4"] = "2026-01-01 00:00:01"
other.cycles["Monitor0"].changes = 3
state.save()
other.save()
merged = tr.TrackerState.load().cycles["Monitor0"]
check("both writers' items survive a merge", merged.seen_count == 2)
check("the higher change count wins a merge", merged.changes == 5)


# ------------------------------------------------------------- the probe

playing["now"] = items[2]
check("an item claimed by another monitor is skipped",
      tr.probe_current(items, prefer=None, exclude={items[2]}) is None)
check("the previously playing item is checked first",
      tr.probe_current(items, prefer=items[2]) == items[2])
check("nothing playing means nothing found",
      tr.probe_current(items, prefer=None, exclude={items[2]}) is None)


# ------------------------------------------------------- which monitor leads

def progress(monitor: str, anchor: str) -> tr.Progress:
    return tr.Progress(cycle_id="x", monitor=monitor, playlist=monitor.lower(), seen=1,
                       total=10, changes=1, repeats=0, order="random", delay=10,
                       started="2026-01-01 00:00:00", current=None, current_title="",
                       current_since=None, live=True, inferred=0, anchor=anchor,
                       gone=0)


subscribed = progress("Monitor0", tr.ANCHOR_NONE)
rotated = progress("Monitor1", tr.ANCHOR_ROTATION)
check("the rotation-built playlist leads by default",
      tr.pick_primary([subscribed, rotated]) is rotated)
check("an explicit choice beats the default",
      tr.pick_primary([subscribed, rotated], "Monitor0") is subscribed)
check("a choice naming a monitor that is gone falls back to the default",
      tr.pick_primary([subscribed, rotated], "Monitor7") is rotated)
check("with no rotation anywhere the first monitor leads",
      tr.pick_primary([subscribed, progress("Monitor1", tr.ANCHOR_FILE_TIMES)]) is subscribed)
check("no playlists means no leader", tr.pick_primary([]) is None)


# ------------------------------------- rebuilding history from file times

tr.STATE_PATH.unlink(missing_ok=True)
recent = [make_item(f"recent{i}", 0.1 + i * 0.2) for i in range(12)]
stale = [make_item(f"stale{i}", 60 + i * 20) for i in range(5)]
items = recent + stale
write_cfg(items)

atimes = tr.scan_atimes(items)
check("access times are read for every file", len(atimes) == 17)
check("a missing file is skipped rather than fatal",
      len(tr.scan_atimes(items + ["W:/nope/gone.mp4"])) == 17)

gap = tr.gap_anchor(atimes)
check("the break before the current run is found", gap is not None)
if gap:
    boundary = tr._parse(gap)
    after = sum(1 for v in atimes.values() if datetime.fromtimestamp(v) >= boundary)
    check("exactly the current run falls after the break", after == 12)
check("a list with no break is not guessed at",
      tr.gap_anchor({p: (NOW - timedelta(hours=i)).timestamp()
                     for i, p in enumerate(items)}) is None)

HIST.write_text(json.dumps({"runs": [
    {"id": "aaa", "timestamp": (NOW - timedelta(days=3)).strftime(tr.TIME_FMT),
     "moved": [f"recent{i}" for i in range(12)] + ["carried", "away"]},
    {"id": "bbb", "timestamp": (NOW - timedelta(days=90)).strftime(tr.TIME_FMT),
     "moved": ["nothing", "to", "do", "with", "this"]},
]}), encoding="utf-8")
anchor, how = tr.cycle_anchor(items, atimes)
check("the rotation that built the playlist dates it", how == tr.ANCHOR_ROTATION)
check("and it is the run that actually matches",
      anchor == (NOW - timedelta(days=3)).strftime(tr.TIME_FMT))

HIST.write_text(json.dumps({"runs": [
    {"id": "ccc", "timestamp": "2020-01-01 00:00:00", "moved": ["unrelated"]},
]}), encoding="utf-8")
check("an unrelated run is not used as an anchor",
      tr.cycle_anchor(items, atimes)[1] == tr.ANCHOR_FILE_TIMES)
HIST.unlink()

tracker = tr.Tracker(str(CFG))

# Whether this machine records access times is a fact about the machine, not
# about the tracker, and a CI runner commonly has them switched off. What has
# to hold is that the detector reaches a definite answer. The reconstruction
# checked below reads times this file forged with os.utime, so it is exercised
# either way once the gate is opened — and opening it by hand is the only way
# this section runs anywhere but on a desktop with the default setting.
check("NTFS access-time support is decided rather than assumed",
      tracker.atime_ok in (True, False))
if not tracker.atime_ok:
    print("NOTE  this machine has last-access updates off; the checks below "
          "open the gate by hand and read forged times")
    tracker.atime_ok = True
playing["now"] = recent[0]
p = tracker.poll()[0]
check("adopting a running playlist recovers its history", (p.seen, p.total) == (12, 17))
check("recovered items are marked as reconstructed", p.inferred == 12)
check("the anchor is reported to the UI", p.anchor == tr.ANCHOR_FILE_TIMES)
check("what is left is what was never shown", p.remaining == 5)
check("the wallpaper already on screen is not counted as a repeat", p.repeats == 0)

p = tracker.poll()[0]
check("a quiet poll changes nothing", (p.seen, p.changes) == (12, 12))

shown, _ = tracker.split_items("Monitor0")
check("reconstructed rows are flagged in the list",
      sum(1 for _, _, inferred in shown if inferred) == 12)


# ------------------------------------------- catching up after being away

state = tr.TrackerState.load()
# Both stamps go back together, which is what a tracker that was simply not
# running looks like — nothing updates either of them while it is away.
six_hours_ago = (NOW - timedelta(hours=6)).strftime(tr.TIME_FMT)
state.cycles["Monitor0"].last_poll = six_hours_ago
state.cycles["Monitor0"].last_reconcile = six_hours_ago
state.save()
when = (NOW - timedelta(hours=2)).timestamp()          # shown while nothing watched
os.utime(stale[0].replace("/", "\\"), (when, when))
p = tracker.poll()[0]
check("a hole in polling is filled from file times", p.seen == 13)
check("and the recovered item stops counting as remaining", p.remaining == 4)


# ------------------------------------------------ upgrading an older state

# Written as raw JSON: a file from the version before reconstruction existed has
# no inferred/anchor/last_poll keys, and save() would merge rather than replace.
tr.STATE_PATH.write_text(json.dumps({"cycles": {"Monitor0": {
    "id": "old12345", "monitor": "Monitor0", "playlist": "custom",
    "started": (NOW - timedelta(hours=1)).strftime(tr.TIME_FMT),
    "items": items, "seen": {recent[0]: tr._now()}, "changes": 1,
    "current": recent[0], "current_since": tr._now(), "delay": 10, "order": "random",
}}, "archive": []}, ensure_ascii=False), encoding="utf-8")

loaded = tr.TrackerState.load().cycles["Monitor0"]
check("an older state file still loads", (loaded.seen_count, loaded.last_poll) == (1, None))
p = tracker.poll()[0]
check("an upgraded state file is rebuilt", p.seen == 13)
check("the upgrade credits reconstruction, not live watching", p.inferred == 12)
check("the upgrade re-dates the cycle", p.anchor == tr.ANCHOR_FILE_TIMES)


# ------------------------------- a reset must not be undone by the rebuild

tracker.reset("Monitor0")
p = tracker.poll()[0]
check("a reset survives the next poll", (p.seen, p.inferred) == (1, 0))
p = tracker.poll()[0]
check("a reset still holds a poll later", (p.seen, p.inferred) == (1, 0))


# ------------------------------------------ no access times: do not invent

tracker.atime_ok = False
tr.STATE_PATH.unlink()
p = tracker.poll()[0]
check("without access times nothing is invented", (p.seen, p.inferred) == (1, 0))
check("and the card says the count starts from now", p.anchor == tr.ANCHOR_NONE)



# ============================================================================
# Everything below runs on its own state file and its own tracker, because each
# section polls repeatedly and would otherwise shift the archive counts the
# sections above check.
# ============================================================================

def fresh_tracker(items: list[str], atime_ok: bool = False) -> tr.Tracker:
    tr.STATE_PATH = TMP / f"tracker_{len(results)}.json"
    write_cfg(items)
    tracker = tr.Tracker(str(CFG))
    tracker.atime_ok = atime_ok
    return tracker


# ----------------------------- wallpapers that can never come up again
#
# Wallpaper Engine goes on listing a wallpaper after its folder is deleted. Left
# in the total, such an entry stalls the count short of the end for good and the
# "playlist finished" cue never fires — which is exactly what happened on a real
# playlist: 197 of 208 shown, and all 11 remaining were deleted folders.

gone_items = [make_item(f"deleted{i}", 1) for i in range(3)]
kept_items = [make_item(f"kept{i}", 1) for i in range(5)]
for path in gone_items:
    Path(path.replace("/", "\\")).unlink()

detected = tr.scan_missing(kept_items + gone_items)
check("a deleted wallpaper is spotted", sorted(detected) == sorted(gone_items))
check("wallpapers still on disk are left alone",
      not any(i in detected for i in kept_items))

# A whole library that has gone away is a different thing from files deleted out
# of it — an unmounted drive must not wipe the count.
absent = [f"{TMP.drive}/nowhere-at-all/{n}/wallpaper.mp4" for n in range(4)]
check("an unreachable library is not mistaken for deletions",
      tr.scan_missing(absent) == [])

tracker = fresh_tracker(kept_items + gone_items)
playing["now"] = kept_items[0]
p = tracker.poll()[0]
check("deleted entries are held out of the total", (p.total, p.gone) == (5, 3))
check("and out of what is left to show", p.remaining == 4)

for item in kept_items:
    playing["now"] = item
    tracker.poll()
p = tracker.poll()[0]
check("so a playlist whose only stragglers are deleted reads as finished",
      (p.seen, p.total, p.remaining, p.percent) == (5, 5, 0, 100))

_shown, left = tracker.split_items("Monitor0")
check("and nothing unopenable is offered in 'not yet shown'", left == [])

# Restoring a file puts it back in the reckoning.
Path(gone_items[0].replace("/", "\\")).write_bytes(b"x")
p = tracker.poll()[0]
check("a wallpaper put back counts again", (p.total, p.gone, p.remaining) == (6, 2, 1))


# ------------------------- a handle Wallpaper Engine has not let go of
#
# Scene packages stay open after the wallpaper has moved on. Trusting the first
# lock found pinned the tracker to one abandoned half an hour earlier, and every
# change after it went unseen.

stale_scene = make_item("stale-scene", 0.5)      # read half a day ago
fresh_video = make_item("fresh-video", 0.001)    # read moments ago
tr.is_in_use = lambda path: path in (stale_scene, fresh_video)
check("with two files held, the more recently read one is the one on screen",
      tr.probe_current([stale_scene, fresh_video], prefer=stale_scene) == fresh_video)
check("the order they appear in the playlist does not decide it",
      tr.probe_current([fresh_video, stale_scene], prefer=stale_scene) == fresh_video)

tr.is_in_use = lambda path: path == stale_scene
check("a single held file is taken as it is",
      tr.probe_current([stale_scene, fresh_video], prefer=None) == stale_scene)
tr.is_in_use = lambda path: path == playing["now"]


# ------------------- a wallpaper the handle probe can never catch
#
# A web wallpaper's index.html is read once by a browser process and never held
# open, so nothing is ever locked for it however faithfully the probe polls.
# Only the periodic re-read of access times can credit one.

web_folder = TMP / "myprojects" / "web-wallpaper"
web_folder.mkdir(parents=True, exist_ok=True)
(web_folder / "index.html").write_bytes(b"<html>")
web = str(web_folder / "index.html").replace("\\", "/")
long_ago = (NOW - timedelta(days=90)).timestamp()
os.utime(web_folder / "index.html", (long_ago, long_ago))

watchable = [make_item(f"seen-live{i}", 1) for i in range(3)]
tracker = fresh_tracker(watchable + [web], atime_ok=True)
playing["now"] = watchable[0]
tracker.poll()
check("a wallpaper the probe cannot see starts out uncounted",
      web not in tr.TrackerState.load().cycles["Monitor0"].seen)

shown_at = (NOW - timedelta(minutes=5)).timestamp()
os.utime(web_folder / "index.html", (shown_at, shown_at))
state = tr.TrackerState.load()
due = (NOW - timedelta(minutes=tr.RECONCILE_MINUTES + 1)).strftime(tr.TIME_FMT)
state.cycles["Monitor0"].last_reconcile = due
state.cycles["Monitor0"].last_poll = due
state.save()
tracker.poll()
check("but the periodic re-read of access times credits it",
      web in tr.TrackerState.load().cycles["Monitor0"].seen)

# A cycle carried over from before periodic reconciling has no sweep stamp at
# all. Falling back to last_poll there would wedge it for good, because that
# stamp is renewed by every poll and the interval could never elapse.
state = tr.TrackerState.load()
carried = state.cycles["Monitor0"]
carried.seen.pop(web, None)
carried.inferred = [i for i in carried.inferred if i != web]
carried.last_reconcile = None            # the field did not exist back then
carried.last_poll = tr._now()            # and polling never stopped
carried.started = (NOW - timedelta(days=1)).strftime(tr.TIME_FMT)
state.save()
tracker.poll()
after = tr.TrackerState.load().cycles["Monitor0"]
check("a cycle with no sweep stamp reconciles instead of wedging",
      web in after.seen)
check("and it is stamped so the next one keeps to the schedule",
      after.last_reconcile is not None)


# ----------------------------- a bulk read is not a hundred wallpaper changes
#
# Wallpaper Engine refreshing its library, a playlist being built, a rotation
# moving 200 folders in — each stamps the whole set within a second. Seen live:
# minutes after a rotation of 200, 178 files shared one access time and the
# tracker read 180/199 on a playlist that had shown one wallpaper.
#
# What tells that apart from viewing is the rate, and only the rate. The first
# attempt compared the count against the playlist's delay instead, and that was
# wrong in the other direction: it threw away two wallpapers skipped by hand
# 22 seconds apart, which then sat in "not yet shown" for ever on a playlist
# that had already come round. Both failures are checked here.

spaced = [(f"item{i}", (NOW - timedelta(minutes=10 * i)).timestamp())
          for i in range(12)]
clump_at = (NOW - timedelta(minutes=4)).timestamp()
clump = [(f"scanned{i}", clump_at + i * 0.01) for i in range(150)]

check("access times spaced like displays are kept",
      len(tr.plausible(spaced)) == len(spaced))
check("a hundred and fifty files read at once are not", tr.plausible(clump) == [])
check("and the real ones next to a bulk read survive it",
      sorted(k[0] for k in tr.plausible(spaced + clump))
      == sorted(s[0] for s in spaced))

# The regression that sent this back for a second look: pressing "next" twice.
skipped_at = (NOW - timedelta(minutes=6)).timestamp()
by_hand = [("skip1", skipped_at), ("skip2", skipped_at + 22)]
check("two wallpapers skipped by hand 22 seconds apart are still displays",
      len(tr.plausible(by_hand)) == 2)
flurry = [(f"skip{i}", skipped_at + i * 12) for i in range(8)]
check("and so is a flurry of eight of them, a wallpaper every twelve seconds",
      len(tr.plausible(flurry)) == 8)
check("but eight read inside a tenth of a second is a machine",
      tr.plausible([(f"scan{i}", skipped_at + i * 0.01) for i in range(8)]) == [])

# The line between the two, checked from both sides.
check("a handful at a hand's pace is not a bulk read",
      not tr.is_bulk_read(tr.BURST_ITEMS, tr.BURST_ITEMS * 2.0))
check("the same handful read at once is",
      tr.is_bulk_read(tr.BURST_ITEMS, 0.0))
check("and two files, however close, are too few to judge",
      not tr.is_bulk_read(2, 0.0))

# A reconstruction that spans days *should* credit most of the playlist; a rule
# capping how much one sweep may credit would refuse exactly that.
long_run = [(f"old{i}", (NOW - timedelta(hours=i)).timestamp()) for i in range(80)]
check("recovering a cycle that ran for days credits all of it",
      len(tr.plausible(long_run)) == 80)

# End to end: the rotation itself is what stamps the files, so adoption of the
# playlist it built is exactly where this used to go wrong.
rotated = [make_item(f"rotated{i}", 1) for i in range(40)]
just_now = (NOW - timedelta(minutes=6)).timestamp()
for path in rotated:                      # one bulk read, as a move leaves them
    os.utime(path.replace("/", "\\"), (just_now, just_now))
HIST.write_text(json.dumps({"runs": [
    {"id": "rot", "timestamp": (NOW - timedelta(minutes=7)).strftime(tr.TIME_FMT),
     "moved": [f"rotated{i}" for i in range(40)]},
]}), encoding="utf-8")

tracker = fresh_tracker(rotated, atime_ok=True)
playing["now"] = rotated[0]
p = tracker.poll()[0]
check("a freshly rotated playlist is dated by the rotation",
      p.anchor == tr.ANCHOR_ROTATION)
check("and it starts from the beginning, not from nearly finished", p.seen == 1)
check("so nothing is credited to reconstruction", p.inferred == 0)
HIST.unlink()



# ---- What comes next -------------------------------------------------------
#
# Wallpaper Engine has two playlist orders, "random" and "sorted". A sorted
# playlist walks its items in order and wraps round, so the rest of the list
# after the wallpaper on screen *is* the queue. A random one keeps its shuffle
# to itself, and the list must not pretend otherwise.

queue = ["q0", "q1", "q2", "q3", "q4", "q5"]
check("a sorted playlist has a queue", tr.queue_is_known("sorted"))
check("a random one does not", not tr.queue_is_known("random"))
check("and neither does an order nobody has seen", not tr.queue_is_known("?"))

check("in sorted order the queue starts just after the wallpaper on screen",
      tr.upcoming(queue, {"q1", "q4", "q5"}, "sorted", after="q3") == ["q4", "q5", "q1"])
check("and wraps round the end of the playlist",
      tr.upcoming(queue, {"q0", "q2"}, "sorted", after="q4") == ["q0", "q2"])
check("the wallpaper on screen would come round last, a whole lap away",
      tr.upcoming(queue, {"q2", "q3"}, "sorted", after="q2") == ["q3", "q2"])
check("with nothing to continue from, a sorted queue starts at the top",
      tr.upcoming(queue, {"q3", "q1"}, "sorted", after=None) == ["q1", "q3"])
check("a random playlist keeps the playlist's own order, because none is known",
      tr.upcoming(queue, {"q4", "q1"}, "random", after="q3") == ["q1", "q4"])

# End to end through the tracker: the head of the queue follows the screen.
ordered = [make_item(f"sorted{i}", 1) for i in range(6)]
tracker = fresh_tracker(ordered)
write_cfg(ordered, order="sorted")
playing["now"] = ordered[2]
tracker.poll()
_shown, remaining = tracker.split_items("Monitor0")
check("the tracker lists a sorted playlist in playing order",
      remaining == ordered[3:] + ordered[:2])
check("and says the order is a real queue", tracker.queue_known("Monitor0"))

playing["now"] = ordered[5]
tracker.poll()
_shown, remaining = tracker.split_items("Monitor0")
check("when the next one comes up the queue moves on with it",
      remaining == [ordered[0], ordered[1], ordered[3], ordered[4]])

# Nothing caught on screen: continue from the last one known to have shown.
state = tr.TrackerState.load()
state.cycles["Monitor0"].current = None
state.cycles["Monitor0"].seen[ordered[2]] = "2000-01-01 00:00:00"   # the older one
state.save()
_shown, remaining = tracker.split_items("Monitor0")
check("without a wallpaper on screen, the queue continues from the last shown",
      remaining[0] == ordered[0])

shuffled = [make_item(f"shuffled{i}", 1) for i in range(4)]
tracker = fresh_tracker(shuffled)
playing["now"] = shuffled[1]
tracker.poll()
check("a random playlist is not presented as a queue",
      not tracker.queue_known("Monitor0"))


# ---- Wallpaper Engine starting a playlist over --------------------------------
#
# Wallpaper Engine keeps its own record of each monitor's pass: the wallpapers it
# has not drawn yet, in bin/playliststate.bin. Everything the tracker counted as
# shown should be gone from it. Seen for real: started with one monitor and then
# given a second, it threw the pass away — 77 of the 83 wallpapers counted were
# waiting again — while the tracker went on counting the old pass.

import struct                                                      # noqa: E402
from app.engines.wallpaper_timer import MAGIC, SECTION, MonitorDeck   # noqa: E402


def engine_state(path: Path, monitors: dict) -> None:
    def text(value: str) -> bytes:
        raw = value.encode("utf-8")
        return struct.pack("<I", len(raw)) + raw

    out = text(MAGIC) + struct.pack("<I", 1) + text(SECTION) + struct.pack("<I", len(monitors))
    for name, (current, waiting) in monitors.items():
        out += text(name) + struct.pack("<I", 84) + text(current) + struct.pack("<I", 0)
        out += struct.pack("<I", len(waiting))
        for item in waiting:
            out += text(item) + struct.pack("<I", 0)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(out)


letters = [f"W:/deck/{c}.mp4" for c in "abcdefghij"]
pass_cycle = tr.Cycle(monitor="Monitor0", playlist="p", started=tr._now(), items=list(letters),
                      seen={i: tr._now() for i in letters[:4]})
check("a deck that has moved on without the counted wallpapers is the same pass",
      not tr.engine_restarted(pass_cycle, MonitorDeck(letters[4], letters[5:])))
check("most of the counted wallpapers back in the deck is a new pass",
      tr.engine_restarted(pass_cycle, MonitorDeck(letters[9], letters[:3] + letters[4:9])))
check("one of them back is a credit the tracker got wrong, not a restart",
      not tr.engine_restarted(pass_cycle, MonitorDeck(letters[9], letters[:1] + letters[4:9])))
check("a deck from some other playlist says nothing about this one",
      not tr.engine_restarted(pass_cycle, MonitorDeck("W:/x.mp4",
                                                      [f"W:/other/{n}.mp4" for n in range(9)])))
done = tr.Cycle(monitor="Monitor0", playlist="p", started=tr._now(), items=list(letters),
                seen={i: tr._now() for i in letters})
check("a pass that ran to its end and began again is a new pass too",
      tr.engine_restarted(done, MonitorDeck(letters[0], letters[1:])))
barely = tr.Cycle(monitor="Monitor0", playlist="p", started=tr._now(), items=list(letters),
                  seen={i: tr._now() for i in letters[:2]})
check("two counted wallpapers are too few to call it",
      not tr.engine_restarted(barely, MonitorDeck(letters[9], letters[:9])))

old = tr.Cycle(monitor="Monitor1", playlist="custom", started="2026-09-12 02:12:03",
               items=list(letters), seen={i: "2026-09-13 10:00:00" for i in letters[:6]},
               changes=7, current=letters[7], current_since="2026-09-15 15:40:00",
               anchor=tr.ANCHOR_ROTATION, delay=10)
deck = MonitorDeck(letters[7], letters[:6] + [letters[8]])     # drew g and j, then h
written = datetime(2026, 9, 15, 15, 44, 0).timestamp()
new = tr.restart_from_engine(old, deck, written,
                             engine_started=datetime(2026, 9, 15, 15, 30, 0).timestamp())
check("the new pass starts with what the engine has drawn in it",
      set(new.seen) == {letters[6], letters[7], letters[9]})
check("the wallpaper still on screen keeps the time it came up",
      new.seen[letters[7]] == "2026-09-15 15:40:00" and new.current == letters[7])
check("the rest is marked as taken from the engine's record, not watched",
      new.inferred == [letters[6], letters[9]])
check("the cycle says what the old count had reached", new.restarted_from == "6/10")
check("and is dated from the engine starting it over", new.anchor == tr.ANCHOR_ENGINE)
check("a playlist from a rotation is still known to be one",
      new.from_rotation and tr.pick_primary([tr.Progress(
          cycle_id="x", monitor="Monitor1", playlist="custom", seen=2, total=10, changes=2,
          repeats=0, order="random", delay=10, started=new.started, current=None,
          current_title="", current_since=None, live=False, inferred=1,
          anchor=new.anchor, gone=0, from_rotation=True)]).monitor == "Monitor1")
check("the pass is never dated before Wallpaper Engine itself started",
      new.started == "2026-09-15 15:30:00")
check("and the reconciler is kept out of the old pass",
      new.last_reconcile is not None and new.last_poll is not None)

# End to end through a poll.
tr.wallpaper_engine_process = lambda: None
items = [make_item(f"restart{i}", 5) for i in range(8)]
tracker = fresh_tracker(items)
state_file = CFG.parent / "bin" / "playliststate.bin"
for shown in items[:5]:
    playing["now"] = shown
    tracker.poll()
before = tr.TrackerState.load().cycles["Monitor0"]
engine_state(state_file, {"Monitor0": (items[4], items[5:8])})
check("a deck consistent with the count leaves the cycle alone",
      tracker.poll()[0].cycle_id == before.id)

engine_state(state_file, {"Monitor0": (items[4], items[:4] + items[5:8])})
p = tracker.poll()[0]
state = tr.TrackerState.load()
check("a deck that took the counted wallpapers back starts a new cycle", p.cycle_id != before.id)
check("which counts only what this pass has drawn", p.seen == 1)
check("and says what the previous count had reached", p.restarted_from == "5/8")
check("the old cycle is archived with the reason",
      state.archive and state.archive[0].get("reason") == "Wallpaper Engine started the playlist over")
check("the next poll does not start it over again", tracker.poll()[0].cycle_id == p.cycle_id)
state_file.unlink()

print()
print("PASSED %d/%d" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
