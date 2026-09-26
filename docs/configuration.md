# Configuration

Every setting, every file this app writes, and what is worth backing up.

## Where things live

A built toolkit keeps everything it writes in **one folder of its own:
`%LOCALAPPDATA%\WallpaperEngineToolkit`** — not beside the exe. The program
folder is what a build replaces, an update overwrites and an uninstall deletes;
the data is outside all three. A source run keeps its data in `data\` beside
`run_app.py` instead, so working on the code never touches the copy you use.
These pages call the folder `data/` either way. Nothing else is written
anywhere, apart from the one autostart entry.

`WALLPAPER_TOOLKIT_DATA`, if set, names a different folder outright — for a test
build that must not see the real data.

```
data/
├── data-folder.json        marks this as the data folder, and says where it was moved from
├── suite.json              Copier, Creator, Review and Tracker settings
├── config.json             the Rotator's five settings
├── history.json            one record per rotation run
├── history_backup/         the history as it was after each of the last 30 saves
├── secrets.json            the Steam API key, DPAPI-encrypted
├── authors.sqlite          the Review tab's authors database
├── tracker.json            the live cycle per monitor, plus 40 finished ones
├── wallpaper_timer.json    the countdown, saved every 15 seconds
├── tracker.log             every tray launch, and any crash
├── library.json            workshop ids found across the local libraries
├── steam_cache.sqlite      what Steam has already been asked
├── selfcheck.txt           the last --selfcheck report
├── thumbs/                 cached preview images
├── authors_backup/         a snapshot of the authors after every change, and journal.jsonl
└── playlist-refresh/       Wallpaper Engine's two files before the last rotation rewrote them
```

### Moved out of the program folder in 3.0.0

Until 3.0.0 a build kept this folder as `data\` beside the exe — inside the
folder a build rewrites. On 2026-09-20 a build run the wrong way emptied it. The
Steam key was missed and put back; the Rotator's history was not, and the next
rotation six days later began a new one.

The first start of 3.0.0 or later moves it, once. Every file is copied to a
staging folder and compared with its original — size and SHA-256 — and only
then does the copy become the data folder and the old `data\` go to the
Recycle Bin. If anything fails, nothing is deleted, the old folder stays in use,
and the next start tries again. `--selfcheck` and `tracker.log` say which
happened, in their `data:` line. A named mutex keeps the tray and the window
from moving it at the same time.

**Not from inside another app's sandbox.** A Store app's terminal — the Claude
desktop app's, for one — runs what it starts inside that app's sandbox, where
every file and folder it creates under `%LOCALAPPDATA%` lands in the app's
private copy (`%LOCALAPPDATA%\Packages\<app>\LocalCache\Local`) while reading as
if it had gone to the real folder; only files that already exist are changed in
place. Nothing started outside that app ever sees the copy. The
first start of 3.0.0 was a `--selfcheck` run from such a terminal: it moved the
data into Claude's copy, and the tray tracker, started by Task Scheduler, found
the real folder empty and began a new one. From 3.0.1 a start that finds itself
in a sandbox moves, creates and marks nothing, and its `data:` line says which
app's sandbox it is in — and warns if that app holds a copy of the data folder.
The move is left to the next start outside it: the tracker at logon, or
`schtasks /run /tn WallpaperEngineToolkitTracker`.

A build older than 3.0.0 does not look in `%LOCALAPPDATA%`: going back to one
means copying the folder back — see
[Building](building.md#updating-an-installed-copy).

> `suite.json` keeps its original filename. It is an internal data file, and
> renaming it would orphan settings for no visible benefit.

## Folder settings

| Setting | Tab | Default |
|---|---|---|
| Destination | [Copier](copier.md) | Wallpaper Engine's `myprojects`, detected |
| Source | [Creator](creator.md) | empty — pick it |
| Target | [Creator](creator.md) | `myprojects`, detected |
| Mode | [Creator](creator.md) | `Move` |
| Tags | [Creator](creator.md) | none — nothing is tagged unless you say so |
| Source (reserve) | [Rotator](rotator.md) | empty — pick it |
| Destination | [Rotator](rotator.md) | `myprojects`, detected |
| Duplicates | [Rotator](rotator.md) | empty — pick it |
| Count | [Rotator](rotator.md) | 1000 |
| Rebuild Wallpaper Engine's playlist | [Rotator](rotator.md#wallpaper-engines-playlist) | on |
| Safety check ("Also check every") | [Tracker](tracker.md) | 5 min — changes themselves are picked up as Wallpaper Engine writes them |
| `config.json` path | [Tracker](tracker.md) | Wallpaper Engine's, detected |

### How the detected ones are found

`app/engines/steam_paths.py` asks Steam rather than assuming:

1. **Steam itself** — `HKCU\Software\Valve\Steam\SteamPath`, falling back to the
   two `HKLM` keys and then to the usual `Program Files` locations.
2. **Its libraries** — `steamapps/libraryfolders.vdf`, which lists every drive
   games are spread across.
3. **Wallpaper Engine** — the library whose `apps` block lists app **431960**.
   A library with a stale manifest still counts if the folder is really there.

From there the layout is fixed by Steam:

```
<library>/steamapps/common/wallpaper_engine/
<library>/steamapps/common/wallpaper_engine/config.json
<library>/steamapps/common/wallpaper_engine/projects/myprojects/
<library>/steamapps/workshop/content/431960/
```

Nothing raises. No Steam, a stripped registry, a Steam that has never been
opened — each simply yields nothing, and the field arrives empty rather than
wrong.

Results are cached for the life of the process. `steam_paths.forget()` drops the
cache if Steam moves underneath a running app.

## Secrets

One value must not live in the source tree, in `suite.json`, or in a log line:
the **Steam Web API key**. It goes in `data/secrets.json`, encrypted with
**DPAPI** — Windows' own per-user data protection.

DPAPI is the right size of answer here. The key is derived from the logged-in
Windows account, so the file is unreadable by another user and useless if copied
off the machine, and nothing has to be typed at start-up to unlock it.

It is **not** protection against someone already running as you — nothing local
is. It is protection against the file leaking on its own, which is the failure
that actually happens: a backup, a synced folder, a screen-share of an editor.

There are no dependencies; `CryptProtectData` is called through `ctypes`. Where
DPAPI is unavailable the value is stored in the clear, **marked as such**, and
the dialog says "stored in the clear" rather than implying a safety it does not
have.

## What to back up

| | Why |
|---|---|
| `data/tracker.json` | the only record of what has been shown |
| `data/history.json` and `data/history_backup/` | what keeps a rotation from repeating the last ones, and what dates every cycle; it cannot be rebuilt. The snapshots are made for you — see [the Rotator](rotator.md#the-history-is-not-lost-quietly). |
| `data/authors.sqlite` and `data/authors_backup/` | every author you have visited, and when — years of review, and nowhere else. The backups are made for you; **Second copy in** under **Authors database…** puts them on another disk as well. See [Backups](authors-database.md#backups). |
| `data/suite.json`, `data/config.json` | your settings; small, easily lost |

**Not** worth backing up: `thumbs/` and `steam_cache.sqlite` are caches that
rebuild themselves, and `library.json` rebuilds in about four minutes.

`secrets.json` is **not** portable — DPAPI ties it to one Windows account, so a
restored copy on another machine decrypts to nothing. Keep the API key
wherever you keep your other credentials.

## What is written outside this folder

Exactly one thing: the autostart entry, when you turn it on.

- A scheduled task named `WallpaperEngineToolkitTracker`, or
- `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` under the same name.

Turning autostart off removes both, including anything left under the app's
former name. See [Starting with Windows](tracker.md#starting-with-windows).

Nothing is ever written into Wallpaper Engine's own files.
