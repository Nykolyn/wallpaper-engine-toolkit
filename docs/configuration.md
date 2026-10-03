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
├── run_meta.json           what each run did beyond its record: result, times, failures by step
├── secrets.json            the Steam API key, DPAPI-encrypted
├── authors.sqlite          the Review page's authors database
├── review_last.json        the last Review scan: the authors with new items, which are done
├── tracker.json            the live cycle per monitor, plus 40 finished ones
├── wallpaper_timer.json    the countdown, saved every 15 seconds
├── tracker.log             every tray launch, and any crash
├── logs/                   each tool's log, a file a day, and a file per rotation; kept 30 days
├── activity.jsonl          a line for each piece of work as it ended
├── activity.1.jsonl        the lines before the journal last passed 2 MB
├── library.json            workshop ids found across the local libraries
├── library_meta.json       title, type, size of each folder in the reserve and myprojects
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
the real folder empty and began a new one. From 3.0.2 a start that finds itself
in a sandbox moves, creates and marks nothing, and its `data:` line says which
app's sandbox it is in — and warns if that app holds a copy of the data folder.
The move is left to the next start outside it: the tracker at logon, or
`schtasks /run /tn WallpaperEngineToolkitTracker`.

A build older than 3.0.0 does not look in `%LOCALAPPDATA%`: going back to one
means copying the folder back — see
[Building](building.md#updating-an-installed-copy).

> `suite.json` keeps its original filename. It is an internal data file, and
> renaming it would orphan settings for no visible benefit.

## The journal and the log files

Since 3.1.0 the window writes its work down as it goes.

**`logs/<tool>/YYYY-MM-DD.log`**, for `rotator`, `creator`, `copier` and
`review`: every line the tool logs, a file a day. A line is
`HH:MM:SS<TAB>kind<TAB>message` — the kind a word for what happened
(`moved`, `returned`, `dupe`, `skip`, `fail`, `error`, `done`, `step`,
`start`, `info`, …). A rotation writes a line for every folder. A line takes
about 7 µs to write, and each one is on the disk before the next, so a crash
loses nothing already logged. Once at every start of the window, on a thread
of its own, the files older than 30 days are deleted: a day's file by the day
in its name, any other by its last change. Only files named that way are
touched.

**`logs/rotator/run-<id>.log`**: one rotation's lines, in the same shape,
named by the run's id — every folder returned or moved, each step's first and
last line (`step 2`, `step 3`, …), and a retry of its failures added at the
end. Swept after 30 days like the rest.

**`activity.jsonl`**: a JSON object a line for each piece of work as it
ended, and for a few things that happened on the way — a rotation starting,
duplicates set aside, the leading monitor's playlist advancing, finishing or
starting over. Each has a time, the tool, a kind (`run.clean`,
`build.problems`, `playlist.finished`, …), a title and a line of detail, and
for a rotation its run id. Past 2 MB it becomes `activity.1.jsonl`, replacing
the one before, and a new file is begun. Only the window writes it, so the
playlist's events are the ones seen while the window is open.

Both are only ever added to. A reader skips a line it cannot read and a field
it does not know, so an older build and a newer one can share them. Neither
is worth backing up: nothing reads them to decide anything.

`tracker.log` is not part of this. The tray writes it as before.

## The Rotator's run facts and library index

Since 3.4.1.

**`run_meta.json`**, beside `history.json`: an entry per run, by its id —
when it started and finished, how it ended (`clean`, `problems`, `stopped`,
`failed`), the batch it was asked for, which folders failed in which step, what
happened to Wallpaper Engine's playlist, when each step ended, its log file, and
any retry. The history itself keeps the shape every older build reads; see
[the Rotator](rotator.md#what-a-run-leaves-behind). Written whole, like the
history; one that does not read is renamed `run_meta.unreadable-<time>.json`
and a new one begun.

**`library_meta.json`**: for each folder of the reserve and myprojects, its
title, type, workshop id and preview from its `project.json`, and its size
once measured — keyed by name and modification time, so a refresh reads only
what changed. A cache: deleted, it is built again, which on a hard disk takes
minutes the first time. It is a file of its own so that `library.json`, which
Review has read since 1.x, never changes shape.

## Folder settings

What is set once is set on the [Settings](settings.md) page; what belongs to
one run of a tool stays on its page. Two files hold them: the Rotator's own
`config.json`, and `suite.json` for everything else. The Settings page writes
each through its own code, and they stay two files.

| Setting | Set on | File (key) | Default |
|---|---|---|---|
| Reserve | [Settings](settings.md#folders) | `config.json` (`source`) | empty — pick it |
| myprojects | [Settings](settings.md#folders) | `config.json` (`destination`) | detected |
| Duplicates | [Settings](settings.md#folders) | `config.json` (`duplicates`) | empty — pick it |
| Folders per run | [Settings](settings.md#folders) | `config.json` (`count`) | 1000 |
| Rebuild Wallpaper Engine's playlist | [Rotator](rotator.md#wallpaper-engines-playlist) | `config.json` (`refresh_playlist`) | on |
| Copier destination | [Settings](settings.md#folders) | `suite.json` (`copier.dest`) | `myprojects`, detected |
| Creator source | [Settings](settings.md#folders) | `suite.json` (`creator.source`) | empty — pick it |
| Creator output | [Settings](settings.md#folders) | `suite.json` (`creator.target`) | `myprojects`, detected |
| Creator mode | [Creator](creator.md) | `suite.json` (`creator.mode`) | `Move` |
| Creator tags | [Creator](creator.md) | `suite.json` (`creator.tags`) | none — nothing is tagged unless you say so |
| `config.json` path | [Settings](settings.md#wallpaper-engine) | `suite.json` (`tracker.we_config`) | Wallpaper Engine's, detected |
| Safety check ("Also check every") | [Settings](settings.md#wallpaper-engine) | `suite.json` (`tracker.heartbeat`, seconds) | 5 min — changes themselves are picked up as Wallpaper Engine writes them |
| Lead monitor | [Settings](settings.md#tracker-and-tray), or a monitor's menu on the [Tracker](tracker.md#the-page) page | `suite.json` (`tracker.primary`) | automatic |
| Review source | [Review settings](review.md#what-gets-reviewed) | `suite.json` (`review.scope`) | the `new` folder |
| Subscribe by | [Review settings](review.md#how-a-click-subscribes) | `suite.json` (`review.subscribe`: `steam` or `page`) | Steam directly |
| Second copy of the authors backups | [Review settings](authors-database.md#a-second-copy-somewhere-else) | `suite.json` (`review.backup_mirror`) | none |
| The author list's order | [Review](review.md) | `suite.json` (`review.sort`, `review.descending`) | known authors first |
| The gallery as a grid or a list | [Review](gallery.md#grid-or-list) | `suite.json` (`review.view`: `grid` or `list`) | grid |

The window and the tray both keep `suite.json`. Each reads it again when the
other has written it, before it relies on or changes the lead monitor.

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

**It is found in the background.** Every step reads the disk Steam is on, which
can be a hard disk that has spun down: 0.3 ms awake, as long as the disk takes
to wake otherwise. The window and the tray start `steam_paths.find_in_background()`
as they start, and read the answers with `steam_paths.known(lookup)`, which is
`None` until they are in and never waits; their GUI threads never call a lookup
themselves. Until the answer arrives a detected default is empty, as with no
Steam at all, so nothing can start writing to it; when it arrives
(`steam_paths.when_found`) the fields that were waiting fill in, unless
something else was chosen meanwhile. A first run's `data/config.json` is
written only then, so its `destination` is never saved empty for want of an
answer that was a moment away.

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
| `data/authors.sqlite` and `data/authors_backup/` | every author you have visited, and when — years of review, and nowhere else. The backups are made for you; **Second copy of the authors backups** in Review settings puts them on another disk as well. See [Backups](authors-database.md#backups). |
| `data/suite.json`, `data/config.json` | your settings; small, easily lost |
| `data/run_meta.json` | each run's result, times and failures by step; estimates and Retry read it. Losing it loses those, never the history |

**Not** worth backing up: `thumbs/` and `steam_cache.sqlite` are caches that
rebuild themselves, and `library.json` and `library_meta.json` rebuild in a few
minutes.

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
