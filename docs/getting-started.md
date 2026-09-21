# Getting started

## Requirements

- **Windows.** Several features call Windows directly: DPAPI for the stored
  Steam key, Task Scheduler for autostart, and the process-memory read behind
  the Tracker's countdown. The pure engines
  run anywhere; the app as a whole does not.
- **Python 3.11 or newer**, or the [standalone build](building.md).
- **Wallpaper Engine**, installed through Steam. It does not need to be running
  except where a page says so.

## Install

```bash
git clone https://github.com/Nykolyn/wallpaper-engine-toolkit.git
cd wallpaper-engine-toolkit
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Run

```bash
.venv\Scripts\python.exe run_app.py
```

…or double-click `run.cmd`, which uses a local `.venv` if there is one and the
system `python` otherwise.

The background playlist tracker is the same entry point with a flag:

```bash
.venv\Scripts\python.exe run_app.py --tracker
```

…or `run_tracker.cmd`. It has no window: it lives in the notification area, and
clicking its icon opens the main window on the Tracker tab.

## What the first run already knows

Folders are not guessed. Steam records its own location in the registry and its
library locations in `steamapps/libraryfolders.vdf`; both are read to find
Wallpaper Engine (app `431960`), and from there the layout is fixed:

| Setting | Filled in from |
|---|---|
| Copier destination | `…/steamapps/common/wallpaper_engine/projects/myprojects` |
| Creator target | the same folder |
| Rotator destination | the same folder |
| Workshop content (Review) | `…/steamapps/workshop/content/431960` |
| Wallpaper Engine `config.json` (Tracker) | `…/steamapps/common/wallpaper_engine/config.json` |

If Steam is not installed, or Wallpaper Engine is in a library Steam has not
recorded, these arrive empty and each tab offers a folder picker. Nothing is
filled in with a path that does not exist.

The folders that **cannot** be derived start empty on purpose: where you keep
video clips, where you keep previews, your rotation reserve, and the folder
duplicates are moved to. A blank field that asks is better than a filled one
that is wrong, in a tab whose next action is moving files.

## What needs setting up, and for which tab

Every tab works immediately. Two have something worth knowing:

| Tab | What | Where |
|---|---|---|
| Creator | ffmpeg — taken from `PATH`, otherwise the bundled `imageio-ffmpeg` | automatic |
| Review | a **Steam Web API key** — optional, but without it author lists leave out mature wallpapers | **Steam key…** in the tab |

A Steam Web API key is free from
[steamcommunity.com/dev/apikey](https://steamcommunity.com/dev/apikey). Without
one the Review tab says so in a banner, marks every author it counted without
it, and warns before writing a visit date from such a list — see
[what the key is for](review.md#what-a-steam-web-api-key-is-for). It is stored
DPAPI-encrypted in `data/secrets.json` — readable only by the Windows account
that wrote it, and useless if the file is copied off the machine. See
[Configuration](configuration.md#secrets).

The Review tab's authors database is a file, `data/authors.sqlite`, made the
first time it is needed and backed up after every change. Nothing to install or
host. See [Authors database](authors-database.md).

## Check the install

```bash
.venv\Scripts\python.exe run_app.py --selfcheck
```

This reports what the app can actually import and reach, and writes the same
report to `data/selfcheck.txt`. It exists because a windowed build has no
console, and the libraries the Review tab needs are imported lazily — exactly
the shape of dependency a packaged build drops silently.

## Next

- Cycling a large library: [Rotator](rotator.md), then [Tracker](tracker.md).
- Building wallpapers from clips: [Creator](creator.md).
- Weekly triage of new wallpapers: [Review](review.md).
