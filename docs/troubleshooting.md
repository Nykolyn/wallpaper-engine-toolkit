# Troubleshooting

Each failure below is one that actually happened, with the file that answers it.

## There was no tray icon after I logged in

**Read `data/tracker.log`.** Every launch appends to it: when it started,
whether the notification area was there, and any crash. A windowed build has no
console, so that file is the only account.

The tracker waits up to two minutes for a notification area and then keeps
counting without an icon rather than quitting — so "no icon" and "not running"
are different states, and the log tells them apart.

Check the entry itself:

```
python run_app.py --autostart status
```

If it says `off`, nothing is registered. If it names a scheduled task but the
tracker never starts, the task may point at an executable that has been renamed
or rebuilt elsewhere — re-register it:

```
python run_app.py --autostart on
```

## Two tray icons

Autostart and a manual `run_tracker.cmd` both started one. A named mutex is
meant to prevent this; if you see it anyway, close one — they write the same
state file and merge on save, so nothing is lost either way.

## The Review tab says it cannot reach the database, but the database is fine

Almost always a VPN. A `mongodb+srv://` string needs an SRV lookup, and the
nameservers a VPN installs answer the Windows DNS Client but not a program
querying them directly — so every lookup burns its full 20-second timeout.

This app already works around it by resolving through `dnsapi.dll`. If it is
still failing, check that the workaround is available:

```
python run_app.py --selfcheck
```

Look for `ok      the Windows resolver (dnsapi.dll)` and
`ok      app.engines.mongo_srv`.

Then press **Test** in the credentials dialog. It reports the actual error — a
refused password reads differently from a name that did not resolve.

## An author's list looks far too short

You have no Steam Web API key, or it is wrong. A signed-out listing hides mature
and questionable content, which on one real library was **43%** of it — and for
some authors the public listing shows *nothing at all*.

Set the key in the Review tab's credentials dialog and press **Test**. See
[why](review.md#why-it-needs-a-steam-web-api-key).

## Subscribing from the gallery does nothing

- **Steam must be running.** The subscribe call goes into the local Steam
  client, not over the web.
- If the direct route stopped working after a Steam or Wallpaper Engine update,
  switch the toolbar to **Opening Steam's page**. That route is one more click
  and uses nothing unsupported. See [Subscribing](gallery.md#subscribing).

## The playlist count is stuck one or two short of the end

This is usually **deleted wallpapers**: Wallpaper Engine goes on listing a
wallpaper after its folder is gone, and it can never come up again. The tracker
looks for these every five minutes and holds them out of the total, so the
count can reach the end.

If it is still stuck on a **sorted** playlist, the remainder are likely **web
wallpapers** (`.html`), which are never held open and can only be credited from
access times — that sweep runs every ten minutes. A random playlist is counted
from Wallpaper Engine's own record of the pass, which has no such blind spot.

## The count jumped by a hundred after a rotation

A rotation moving 200 folders stamps their access times all at once, and access
times are how the tracker rebuilds displays it did not witness. It discards a
group of at least five files arriving faster than two a second for exactly this
reason — but a slow rotation on a slow disk can fall under that rate.

**New cycle** resets it.

## The count reset itself and the tray said "Playlist started over"

Wallpaper Engine restarted the pass. The two known causes are a **monitor that
appeared after Wallpaper Engine started** and **saving the playlist**.

To avoid it: switch on every monitor before starting Wallpaper Engine, and add
subscriptions in batches when the pass is done anyway. See
[the full account](tracker.md#when-wallpaper-engine-starts-a-playlist-over).

## There is no countdown ring

Several harmless reasons:

- The tray has not seen a change yet — the state file does not say when the
  current wallpaper came up, so nothing is invented.
- The playlist changes on a schedule or when a video ends, so there is no delay
  to count.
- The timer object could not be found in `wallpaper64.exe` — a 32-bit build, or
  an update that moved the fields. `data/tracker.log` says so, and the tray
  falls back to its estimate.

## The rotation did not rebuild the playlist

The log says which of three it was:

- **No playlist is made of what is in myprojects.** The playlist is found by
  its contents: at least half of it must be folders the rotation takes back,
  and it must hold at least half of them. Build it once in Wallpaper Engine
  from what is in `myprojects`; every rotation after that keeps it current.
- **Wallpaper Engine did not close.** A dialog of its own open at the time
  can hold it. The rotation went ahead without touching it; rebuild the
  playlist by hand that once.
- **`config.json` could not be read.** Wallpaper Engine was started again
  unchanged.

What was in both files before the last rewrite is in `data/playlist-refresh/`.
To put it back, quit Wallpaper Engine from its tray, copy the two files over
`config.json` and `bin/playliststate.bin`, and start it.

## Wallpaper Engine did not come back after a rotation

The summary says so. Start it as usual; the playlist is already rewritten and
it opens on the new set.

## A rotation produced a playlist smaller than the count I asked for

Folders without a `project.json` can never be listed by Wallpaper Engine. Run
**Check folders** — on one real library, 389 of 33 423 folders were in this
state. See [the reserve check](rotator.md#the-reserve-check).

## The Creator skipped clips

A clip is skipped only when its preview could not be produced at all — neither
the GIF nor the still fallback. Its half-built folder is removed and the video
is left where it was, so nothing is lost; the log says which clip and why.

The usual cause is a file that is not really a video, or one ffmpeg cannot
decode. Check that ffmpeg is found at all — the tab reports which one it is
using.

## The Creator's previews are black

The clip fades in from black for longer than the one second the render skips.
Replace `preview.gif` in that project folder by hand; nothing else about the
project depends on how the preview was made.

## Folder fields are empty on a new machine

Steam was not found, or Wallpaper Engine is in a library Steam has not recorded.
Check what was detected:

```
python -c "from app.engines import steam_paths as s; print(s.steam_root(), s.wallpaper_engine_dir())"
```

Both `None` means the registry said nothing. Pick the folders by hand — they are
remembered after the first time.

## A rebuild lost my settings

It should not: `build.cmd` copies `data/` out before PyInstaller wipes `dist/`
and restores it afterwards. If it happened anyway, the backup is at
`%TEMP%\WallpaperEngineToolkit_data_backup`.

## The window stopped answering

It should not — see [Gallery](gallery.md#and-never-at-the-windows-expense) for
what made it freeze before and what changed. If it happens anyway, the record is
already written:

- `data/window-hangs.log` — the toolkit window. When its GUI thread goes five
  seconds without answering, the stack of every thread is written here while it
  is still stuck, followed by how long it was gone once it comes back. A hard
  crash leaves its stack here too.
- `data/tracker-hangs.log` — the same for the tray tracker.

That file is what to look at, or to attach to an issue: it says which line every
thread was on. Closing a frozen window from Task Manager no longer stops the
tracker — they are separate programs.

## Nothing above

`data/tracker.log` and `data/selfcheck.txt` are the two files worth reading
before anything else. Together they answer "is it running?" and "can it reach
what it needs?" — which is most failures.
