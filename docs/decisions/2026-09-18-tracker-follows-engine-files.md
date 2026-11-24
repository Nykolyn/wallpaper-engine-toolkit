---
date: 2026-09-18
status: accepted
source: "#4"
tags: [tracker, engine]
---

# The Tracker follows Wallpaper Engine's own files

**Decision.** The Tracker counts from what Wallpaper Engine writes, not from
polling: `bin/playliststate.bin` (the unplayed deck of each monitor's pass,
rewritten at every change) and `config.json`, watched for writes, plus a
five-minute heartbeat. The timer to the next change is read from Wallpaper
Engine's memory in 64 KB pieces, from a low-priority thread, never in the first
30 s after it starts, and a found object is kept until its instance disagrees
with the state file for 90 s. The Tracker never writes to Wallpaper Engine.

**Why.** The old 30-second poll stat'ed about 1 600 files on the GUI thread and
once hung the tray until Windows killed it. Reading a random playlist's deck is
exact (drawn = shown); access times were the only source of error. Large
memory reads (16 MB) made Wallpaper Engine stutter while it started.

**Consequences.** A playlist re-applied to a monitor (monitor attached, playlist
saved) starts a new pass; the Tracker detects it as a reset — see
[Restore a pass](2026-11-08-pass-restore.md). Offsets in memory can move with a
Wallpaper Engine update. See [Tracker](../tracker.md).
