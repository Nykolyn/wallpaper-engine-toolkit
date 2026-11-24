---
date: 2026-11-08
status: accepted
source: "#94"
tags: [engine, tracker]
---

# Restore a pass Wallpaper Engine threw away

**Decision.** When Wallpaper Engine re-applies a playlist and starts a monitor's
pass over, the Toolkit can put the old pass back: close Wallpaper Engine with
its tray Quit, write the not-yet-shown rest of the pass as that monitor's deck
in `playliststate.bin`, start it again. By button on the **Restore** page, or
automatically from the tray 60 s after the reset, switched per monitor
(`restore/auto.MonitorN`, on by default for the Rotator's monitor). The Tracker
now records which wallpapers were shown when a pass is reset; older resets fall
back to access times. The Rotator's own rewrites are never restored.

**Why.** Attaching a monitor or saving a playlist silently threw away a
half-shown pass, and wallpapers already seen came round again.

**Consequences.** Sequential (non-random) playlists are not checked yet. Full
design: [spec](../superpowers/specs/2026-11-08-pass-restore-design.md). See
[Restore](../restore.md).
