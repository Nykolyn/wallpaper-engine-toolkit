# Building a standalone executable

```
build.cmd
```

Produces `dist\WallpaperEngineToolkit\WallpaperEngineToolkit.exe` — PyInstaller,
windowed, `--collect-submodules app`.

The same executable runs the tray tracker when started with `--tracker`, so
there is only ever one build:

```
WallpaperEngineToolkit.exe              the window
WallpaperEngineToolkit.exe --tracker    the tray tracker
WallpaperEngineToolkit.exe --selfcheck  what this build can actually import
```

## What the build script does around PyInstaller

`dist\WallpaperEngineToolkit\data` is where a built installation keeps
everything it has learned — the authors database and its backups, the tracker's
history, the encrypted Steam key. PyInstaller empties its output folder before
writing, so **it never writes there**:

1. It builds into `build\stage\WallpaperEngineToolkit`.
2. Only if that succeeded, `robocopy /MIR` copies the result into
   `dist\WallpaperEngineToolkit`, with that one `data` folder excluded — from
   the copy *and* from the purge, so nothing under it is deleted or
   overwritten. A package's own `data` directory inside `_internal` is still
   copied; the exclusion names the full path.

A failed build leaves `dist\` as the previous build, whole. The script refuses
to start while the toolkit is running, because a running copy holds its exe
open and the copy could not replace it.

(It used to stash `data\` in `%TEMP%` and copy it back. A build that died half
way through PyInstaller's wipe deleted `secrets.json` that way — which is what
this replaced.)

## Checking a build

A windowed build has no console, and a module imported lazily is exactly the
shape of dependency PyInstaller misses — the failure looks like a tab quietly
not working, hours after the build.

So run the selfcheck once after building:

```
dist\WallpaperEngineToolkit\WallpaperEngineToolkit.exe --selfcheck
```

It writes the report to `data\selfcheck.txt` as well as printing it. A good one
looks like this:

```
version: 2.0.0
frozen: True
ok      PySide6.QtWidgets  (the window)
ok      sqlite3  (the authors database and the Steam cache)
ok      app.engines.authors_store  (the authors database)
ok      app.ui.authors_dialog  (its backups and restoring one)
ok      app.engines.review  (the review itself)
ok      app.ui.review_tab  (the Review tab)
ok      app.engines.steam_ugc  (subscribing from the gallery)
ok      app.secrets  (the stored Steam key)
ok      PySide6.QtNetwork  (one window, raised from the tray)
ok      app.window_instance  (starting the window on its own)
ok      app.engines.playlist_refresh  (rebuilding Wallpaper Engine's playlist after a rotation)
ok      preview downloads (HTTP 404 from the host)
```

`version:` is the build you meant to make, and `frozen: True` confirms you are
testing the build and not the source tree. A 404 from the preview host is a
pass: any answer at all means the connection works.

## Updating an installed copy

The tray tracker runs from the build in `dist\`, and a running copy holds its
exe open — so `build.cmd` refuses to start while one is running. The whole
update, from the project folder in `cmd`:

```
schtasks /end /tn WallpaperEngineToolkitTracker
robocopy dist\WallpaperEngineToolkit dist\_previous-2.0.0 /E /XD "%CD%\dist\WallpaperEngineToolkit\data"
build.cmd
dist\WallpaperEngineToolkit\WallpaperEngineToolkit.exe --selfcheck
schtasks /run /tn WallpaperEngineToolkitTracker
```

1. **Stop the tracker** — the task, or **Quit** on its tray icon — and close the
   window if it is open. Ending the task lets it shut down properly:
   `data\tracker.log` says `stopped with code 0`.
2. **Keep the build you are replacing**, named for its version and without its
   `data`, so going back is a copy rather than a rebuild. `build.cmd` never
   touches `_previous-*` folders. (robocopy's exit codes below 8 all mean
   success; 1 is "files were copied".)
3. **Build.** `data\` is not touched — see
   [above](#what-the-build-script-does-around-pyinstaller).
4. **Selfcheck** the new exe, as above.
5. **Start the tracker again.** `data\tracker.log` gains a `running; tray icon
   visible: True` line.

Measured going from 1.2.1 to 2.0.0: 52 s for the build, and all 365 files in
`data\` identical afterwards except `tracker.log`, which had gained the
tracker's own "stopped" line.

**Going back** is the same with the copy the other way round — stop the
tracker, then

```
robocopy dist\_previous-2.0.0 dist\WallpaperEngineToolkit /MIR /XD "%CD%\dist\WallpaperEngineToolkit\data"
```

and start it again. The `/XD` is what keeps `/MIR` from deleting `data\`, which
the copy does not have. Rolling the exe back does not roll data back: a version
older than 2.0.0 does not read `authors.sqlite` at all.

## After renaming the executable

If you had autostart on under a **previous** build name, the scheduled task
still points at the old path. The app migrates the task's *name* on first run,
and rebuilds the entry from scratch when the program it pointed at no longer
exists — but the cleanest fix is one line:

```
dist\WallpaperEngineToolkit\WallpaperEngineToolkit.exe --autostart on
```

That re-registers the task against the executable that exists now. Check it
with `--autostart status`.

## The icon

`assets/icon.ico` is generated by `assets/make_icon.py`, so the mark can be
retuned without a paint program. It is a stack of wallpapers — two dim slivers
behind one bright card with a picture on it — built from three shapes and a
single gradient, because it has to survive 16x16 on the taskbar.

The script also writes `icon_preview.png`, a contact sheet of every real size,
to check the small ones by eye.

`build.cmd` passes the icon to PyInstaller **twice**: `--icon` for what Explorer
shows, and `--add-data` so `QIcon` can paint the title bar at runtime.

## The spec file

`WallpaperEngineToolkit.spec` is checked in. It collects submodules for `app`
and collects `imageio_ffmpeg` whole — that last one carries a binary, which is what the [Creator](creator.md) falls back to
when there is no ffmpeg on `PATH`.
