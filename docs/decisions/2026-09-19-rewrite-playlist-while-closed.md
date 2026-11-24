---
date: 2026-09-19
status: accepted
source: "#7"
tags: [engine, rotator]
---

# Playlists are rewritten while Wallpaper Engine is closed

**Decision.** A rotation rebuilds the `myprojects` playlist itself: close
Wallpaper Engine with its own tray **Quit** (`WM_COMMAND 40010` posted to its
hidden `WPEEventWindow`), rewrite the playlist in `config.json`, then start the
exact executable with the exact arguments that were running. `launcher.exe` is
never started directly. Only the rotated monitor's playlist is touched.

**Why.** No command-line control can carry new playlist contents
(`-control openPlaylist` takes only a name Wallpaper Engine already holds), and
there is no reload command. `WM_CLOSE` and a plain `taskkill` are ignored;
`launcher.exe` opened the library window. Measured: Wallpaper Engine started
with the rewritten playlist in our order and kept the other monitor's pass.

**Consequences.** Saving a playlist starts a new pass, so the Rotator marks its
own rewrites (`playlist-refresh/rewritten.txt`) so they are never "restored".
See [Rotator](../rotator.md#wallpaper-engines-playlist).
