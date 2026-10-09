# Installing

Every release on the
[Releases page](https://github.com/Nykolyn/wallpaper-engine-toolkit/releases)
carries an installer, `WallpaperEngineToolkit-Setup-X.Y.Z.exe`. Download it and
run it. Nothing else is needed: no Python, no clone, no build. You need
**Wallpaper Engine**, installed through Steam, and optionally a
[Steam Web API key](review.md#what-a-steam-web-api-key-is-for) for the Review
page, which you enter in the app.

Windows 10 1809 or later, 64-bit.

## "Windows protected your PC"

The installer is not code-signed yet, so SmartScreen stops it the first time:
click **More info**, then **Run anyway**. Every release's installer is built by
GitHub from the source in the repository; the workflow that does it is
`.github/workflows/tests.yml`.

## What it does

The wizard asks three things, the first two only on a first install:

1. **Where to put the program.** `%LOCALAPPDATA%\Programs\WallpaperEngineToolkit`
   unless you choose otherwise. It installs for your Windows account only and
   asks for no administrator rights.
   You can choose a folder under Program Files by running the installer as
   administrator; every update and the uninstall then need the same, and say
   so before they change anything.
2. **Data from an earlier copy** — see [below](#coming-from-an-earlier-copy).
   Leave it empty on a first install.
3. **Start the tray tracker when I sign in to Windows** (ticked), and a
   desktop shortcut (not ticked).

It puts **Toolkit** in the Start menu, and an uninstaller in
**Settings > Apps**. The wizard speaks English, Russian or Ukrainian, after
Windows' own language.

## Your data

The data — settings, the Steam key, the authors database, the Rotator's
history, the Tracker's state — lives apart from the program, in
`%LOCALAPPDATA%\WallpaperEngineToolkit` (see
[Configuration](configuration.md#where-things-live)). The installer:

- **never deletes, moves or overwrites it** — not when installing, updating or
  uninstalling;
- **copies it aside before every update**, into `update_backup\<date time>
  before <version>\` inside it, before the new version does anything else.
  Every file is compared with its original, and `manifest.json` lists them
  with their checksums. Left out are the previews (`thumbs\`, a gigabyte on a
  big library, and fetched again when shown), the logs, and the backups the
  app keeps anyway (`authors_backup\`, `history_backup\`). The newest five
  copies are kept; older ones go to the Recycle Bin. If the copy cannot be
  made — a full disk — the new version is installed but not started, and says
  so;
- **leaves it in place when you uninstall**, and says where it is. Installing
  again picks it up as it was.

To go back to a copy: quit Toolkit (its tray icon, **Quit**), and copy the
files from the `update_backup\…` folder over the ones in the data folder.

## Updating

Run the new release's installer. It:

1. asks the running window and tray tracker to quit, and waits. A window in
   the middle of a run, a scan or a question does not quit — it comes forward,
   and the installer asks you to finish and close it, then **Retry**. Nothing
   is ended by force;
2. copies the data aside, as above;
3. replaces the program;
4. starts the tray tracker again, if it was running.

Autostart is left as you set it.

## Coming from an earlier copy

**A copy you built with `build.cmd`, version 3.0.0 or later**, already keeps
its data in `%LOCALAPPDATA%\WallpaperEngineToolkit`: the installed copy uses
the same data, and there is nothing to bring over. Tick **Start the tray
tracker…** and the logon task is pointed at the installed copy. The Start-menu
entry is the installed copy's from then on; your build stays where it is, and
still runs, on the same data.

**A copy run from source** (`git clone`, `run_app.py`) keeps its data in
`data\` beside `run_app.py`, and **a build from before 3.0.0** in `data\` beside
its exe. On a first install, the wizard's **Data from an earlier copy** page
asks for that copy's folder, filled in already when the copy's autostart entry
names it. Its data is copied into `%LOCALAPPDATA%\WallpaperEngineToolkit` and
checked file by file; **the original is not touched**, and the marker in the
new folder (`data-folder.json`) says where it came from. Data that is already
in `%LOCALAPPDATA%\WallpaperEngineToolkit` is never added to or replaced, so the
page is not shown once it is there.

If the copy fails, nothing is lost — the original is where it was — and the
program is installed but not started, so running the installer again tries
again from the same place.

## Uninstalling

**Settings > Apps > Wallpaper Engine Toolkit > Uninstall.** It asks the window
and the tray tracker to quit, removes the program, its Start-menu entry, and
the logon task **if the task starts the installed copy** — not one that starts
a build of your own. The data stays; delete
`%LOCALAPPDATA%\WallpaperEngineToolkit` yourself if you no longer want it.

## Installing without the wizard

The installer takes Inno Setup's usual switches, and one of its own:

```
WallpaperEngineToolkit-Setup-X.Y.Z.exe /VERYSILENT /SUPPRESSMSGBOXES /TASKS=autostart
WallpaperEngineToolkit-Setup-X.Y.Z.exe /VERYSILENT /EarlierCopy="D:\src\wallpaper-engine-toolkit"
WallpaperEngineToolkit-Setup-X.Y.Z.exe /VERYSILENT /MERGETASKS=!autostart
```

`/EarlierCopy` names the earlier copy's folder outright. `/LOG=<file>` writes
the installer's log there; without it the log is in `%TEMP%`, as `Setup Log
<date> #<n>.txt`, with a line for each step the app took and what it said.

## When something goes wrong

- **"Toolkit is still running and could not be closed for you."** It is busy,
  or a window is asking something. Finish, close the window, quit the tray
  icon, and click **Retry**. A copy from before 3.19.0 does not answer the
  request at all; close it the same way.
- **The tracker did not come back after an update.** `tracker.log` in the data
  folder has a line for every start and for the request to quit.
- The installer's own log, above, has every step and the app's answer to it.
