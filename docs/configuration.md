# Configuration

Every setting, every file this app writes, and what is worth backing up.

## Where things live

Everything is written **next to the application**: `data/` beside the executable
in a build, or beside `run_app.py` in a source run. Nothing goes into
`%APPDATA%`, the registry (except the one autostart entry) or anywhere else, so
a whole installation is one folder you can copy or delete.

```
data/
├── suite.json              Copier, Creator, Review and Tracker settings
├── secrets.json            Steam API key + database URI, DPAPI-encrypted
├── tracker.json            the live cycle per monitor, plus 40 finished ones
├── wallpaper_timer.json    the countdown, saved every 15 seconds
├── tracker.log             every tray launch, and any crash
├── library.json            workshop ids found across the local libraries
├── steam_cache.sqlite      what Steam has already been asked
├── selfcheck.txt           the last --selfcheck report
├── thumbs/                 cached preview images
└── authors_backup/         database plans and per-change backups

app/engines/data/
├── config.json             the Rotator's four settings
└── history.json            one record per rotation run
```

> The Rotator keeps its own two files beside its engine rather than in `data/`.
> That is how the standalone tool did it, and the [Tracker](tracker.md) reads
> `history.json` from there to date a cycle.

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
| Poll interval | [Tracker](tracker.md) | 30 s |
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

Two values must not live in the source tree, in `suite.json`, or in a log line:
the **Steam Web API key** and the **database connection string**. They go in
`data/secrets.json`, encrypted with **DPAPI** — Windows' own per-user data
protection.

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

## Environment variables

All optional.

| Variable | Used by | Meaning |
|---|---|---|
| `WET_DB_CLUSTER` | [Review](review.md) | cluster host, when a `.env` has no `DB_CLUSTER` |
| `WET_SERVER_ENV` | [Review](review.md) | pre-fills the `.env` picker |
| `WET_TEST_CLUSTER` | tests | cluster for `test_mongo_srv.py --live` |

## What to back up

| | Why |
|---|---|
| `data/tracker.json` | the only record of what has been shown |
| `app/engines/data/history.json` | dates every cycle, and cannot be rebuilt |
| `data/authors_backup/` | the only local copy of what the database looked like |
| `data/suite.json`, `app/engines/data/config.json` | your settings; small, easily lost |

**Not** worth backing up: `thumbs/` and `steam_cache.sqlite` are caches that
rebuild themselves, and `library.json` rebuilds in about four minutes.

`secrets.json` is **not** portable — DPAPI ties it to one Windows account, so a
restored copy on another machine decrypts to nothing. Keep the API key and the
connection string wherever you keep your other credentials.

## What is written outside this folder

Exactly one thing: the autostart entry, when you turn it on.

- A scheduled task named `WallpaperEngineToolkitTracker`, or
- `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` under the same name.

Turning autostart off removes both, including anything left under the app's
former name. See [Starting with Windows](tracker.md#starting-with-windows).

Nothing is ever written into Wallpaper Engine's own files.
