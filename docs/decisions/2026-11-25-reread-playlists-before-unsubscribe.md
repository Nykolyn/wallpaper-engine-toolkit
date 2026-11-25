---
date: 2026-11-25
status: accepted
source: "owner, in chat"
tags: [engine]
---

# Playlists are read again right before unsubscribing

**Decision.** The Cleaner's Outside playlists tab re-reads Wallpaper Engine's
`config.json` in the worker, immediately before a batch is unsubscribed, and
keeps every wallpaper it now finds in a playlist. An unreadable file stops the
whole batch. The confirmation shows when the file was last written. Wallpaper
Engine is not restarted to flush it.

**Why.** Wallpaper Engine writes `config.json` some time after a playlist
changes, so a list read earlier can call a wallpaper you just kept "outside".
The alternative — quitting and restarting Wallpaper Engine to force the write,
as the [Rotator does](2026-09-19-rewrite-playlist-while-closed.md) — is surer
but takes seconds and restarts it on every clean-up; the owner chose the
re-read.

**Consequences.** It cannot help when Wallpaper Engine has not written the
file at all; the save time in the confirmation is there for that. A playlist
is any saved one or any monitor's running copy; a monitor's single wallpaper
is not kept (owner). Design:
[spec](../superpowers/specs/2026-11-25-cleaner-outside-playlists-design.md). See
[Cleaner](../cleaner.md#outside-playlists).
