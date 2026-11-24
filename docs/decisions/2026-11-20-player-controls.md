---
date: 2026-11-20
status: accepted
source: "#97, #98"
tags: [tracker, engine]
---

# Player controls act on the click

**Decision.** The Toolkit drives Wallpaper Engine's playlist like a music
player: Pause/Play, Next, and **Put on screen** on a Tracker playlist row — the
same as clicking a wallpaper in Wallpaper Engine. Put on screen switches at
once, without a confirmation.

**Why.** The owner wanted to steer the playlist without opening Wallpaper
Engine. The confirmation was dropped (#98) after live testing showed the
monitor keeps its playlist and its pass, so the warning had no reason to exist.

**Consequences.** Full design:
[spec](../superpowers/specs/2026-11-19-wallpaper-player-design.md). See
[Tracker](../tracker.md).
