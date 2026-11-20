# Wallpaper player — design

Date: 2026-11-19. Status: approved in chat (behaviour); visual design comes
later from a separate design pass and is folded into the plan.

## Why

The user wants to drive Wallpaper Engine's playlist from the Toolkit the way a
music player is driven: pause/play, skip to the next wallpaper, and put a
chosen wallpaper on screen straight from the Tracker's playlist table — the
same as clicking a wallpaper in Wallpaper Engine — without opening WE.

## What the user decided

- **Pause / Play** — stops and resumes playback.
- **Next** — skips to the next wallpaper of the playlist.
- **No Previous.** WE has no command for it; the only route (rewrite the deck
  and restart WE) was turned down for this feature.
- **Pick a row** — every row of the Tracker's playlist table gets a button that
  makes that wallpaper the active one, **like WE's click: instant**
  (`openWallpaper`), accepting that it takes the monitor out of its playlist.
- Actions apply to the **active monitor**:
  - Tracker page — the monitor whose playlist the table shows (`table_monitor()`);
  - widget — the widget's own monitor (for the single widget, the one it
    follows/binds);
  - tray — the monitor selected for the tray in Settings.
- Controls appear on: the Tracker page's monitor card (Pause/Play, Next), each
  playlist row (Pick), the tray menu (Pause/Play, Next), the widget (expanded:
  buttons on the monitor row; compact: in the right-click menu).

## What Wallpaper Engine allows (help.wallpaperengine.io, CLI page)

All commands run the WE exe already running (`<install>/wallpaper64.exe` or
32) with `-control <command>`; WE must be running.

| Action | Command | Monitor |
|---|---|---|
| Pause | `-control pause` | **all monitors** — no monitor argument exists |
| Play | `-control play` | all monitors |
| Next | `-control nextWallpaper -location MonitorN` | one monitor; skipping keeps the pass (observed 2026-09) |
| Pick | `-control openWallpaper -file <project.json path> -location MonitorN` | one monitor |

So Pause/Play is global whatever surface it is pressed on; the button says so
in its tooltip ("Pause all monitors").

## Consequences of Pick (user accepted)

Expected: `openWallpaper` takes the monitor out of its playlist. **Seen live
2026-11-19 (WE on this machine), it does not:** with `-file <folder>/project.json
-location Monitor0`, `config.json` showed the picked file with the playlist still
attached, `playliststate.bin` was not rewritten, WE's playlist timer for Monitor0
restarted from 0, and a Next afterwards moved on within the same deck (same
instance). So the pass carries on; the picked wallpaper stays in the queue, and
the tracker shows the old "on screen" until the next change. No auto-restore
guard is needed.

My calls (correctable):
- Pick acts on the click, with no confirmation (the user's call, 2026-11-20,
  after seeing that the monitor keeps its playlist).
- Pick must **not trigger auto-restore**. The builder checks what the tracker
  records when a monitor has no playlist and, if it reads as an engine reset,
  marks Toolkit-made picks the way `playlist-refresh/rewritten.txt` marks
  Rotator rewrites.

## Pause state

WE cannot be asked whether it is paused. My call: the Toolkit records its own
pause in a small shared file in the data folder (`player.json`:
`{paused, pid}`), written by whichever process pressed the button and read by
the page, tray and widget, so all three show the same Play/Pause glyph and the
page's estimated countdown freezes. It is ignored once the WE pid differs (WE
restarted → playing). A pause made inside WE itself is not seen — the tray's
memory timer still shows it frozen, the button may say Pause; accepted limit.

## Non-goals

- Previous, mute, stop, hotkeys, per-monitor pause (WE cannot do it).
- Bringing a monitor back into its playlist after a Pick.

## Left to the builder

- Where the `-control` call lives: next to `engine_control.py`
  (`running()` gives the exe), run off the GUI thread, errors as a toast.
- Verify live, with the user's OK: `-location MonitorN` addresses the right
  monitor (fall back to `-monitor N`), and what Pick does to the playlist,
  state file and tracker.
- Tests for the command lines built and the pause-file rules.

## For the designer (separate design pass)

Surfaces and states to design:
1. Tracker monitor card: Pause/Play toggle + Next; disabled when WE is not
   running; paused state.
2. Playlist row: a Pick glyph button beside Protect/Delete (table and grid
   view); disabled on the wallpaper already on screen; confirm dialog.
3. Tray menu: "Pause all monitors"/"Play", "Next wallpaper".
4. Widget: expanded — Pause/Play + Next per monitor row; compact — the same
   two items in the right-click menu.
