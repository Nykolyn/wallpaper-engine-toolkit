# Wallpaper Engine Toolkit

A Windows desktop application for people whose Wallpaper Engine library has
outgrown Wallpaper Engine: tens of thousands of wallpapers, a reserve that has
to be cycled through `myprojects`, clips waiting to become wallpapers, and a
weekly pile of new items whose authors are the part actually worth tracking.

Five tools in one window, sharing one dark theme and one set of habits.

| Tab | What it does |
|---|---|
| **[Copier](docs/copier.md)** | Duplicates wallpaper folders N times each — the weighting Wallpaper Engine's playlists do not have. |
| **[Creator](docs/creator.md)** | Turns video clips into Wallpaper Engine projects, rendering each preview from the video itself. |
| **[Rotator](docs/rotator.md)** | Moves folders between a reserve and `myprojects`, with duplicate handling, protected folders and a dead-folder check — and rebuilds Wallpaper Engine's playlist from the new set. |
| **[Tracker](docs/tracker.md)** | Reports how far Wallpaper Engine has got through the active playlist — `112/208` — so the next rotation is due when it reaches the end. |
| **[Review](docs/review.md)** | Groups the week's new wallpapers by author, says what each has published since you last looked, and opens a gallery to subscribe from. |

**[Full documentation →](docs/)**

## Install

```
git clone https://github.com/Nykolyn/wallpaper-engine-toolkit.git
cd wallpaper-engine-toolkit
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe run_app.py
```

…or double-click `run.cmd`. For a standalone `.exe`, see
**[Building](docs/building.md)**.

The background playlist tracker is the same entry point with a flag:

```
.venv\Scripts\python.exe run_app.py --tracker
```

It has no window — it lives in the notification area and clicking it opens the
main window on the Tracker tab.

**[Getting started →](docs/getting-started.md)**

## Requirements

- **Windows.** Several features call Windows directly: DPAPI for the stored
  Steam key, Task Scheduler for autostart, and a read-only process-memory probe
  behind the Tracker's countdown.
- **Python 3.11+**, or the standalone build.
- **Wallpaper Engine** via Steam. Steam's registry entry and
  `libraryfolders.vdf` are read to locate it, so folder settings arrive already
  filled in.
- **Optionally, a Steam Web API key** — for the Review tab, which works
  without one but sees less. Its authors database is a local file made on first
  use; there is nothing to install or host.

## What it will not do

One thing writes to Wallpaper Engine, and says so first: a rotation rebuilding
its playlist closes it, rewrites that playlist in `config.json` and the pass in
`bin/playliststate.bin`, and starts it again — with both files copied aside
beforehand, and a checkbox to turn it off. Nothing else does. The Tracker reads
`config.json` and the two state files in `bin/`, and its memory probe is opened
read-only. Rotation moves your folders between your own directories. The one action that reaches outside the machine is subscribing to a
workshop item, which is a deliberate click.

Nothing writes to the authors database without showing you the change first,
as a line of English, and backing the whole database up after.

## Design

The three original tools were each written against a different GUI toolkit
(tkinter, CustomTkinter, PySide6). This project unifies the **interface** onto
PySide6/Qt with the *Fusion* style, and leaves the **implementations alone** —
each original engine is reused as-is, with the GUI layer re-hosting it behind Qt
widgets.

Most of what is unusual in the codebase is unusual because a simpler version was
tried and measured worse. Those measurements are in the comments and in
[`docs/`](docs/), because they are the reason the code looks the way it does:

- Why the playlist probe sweeps every item instead of stopping at the first
  locked one — [Tracker](docs/tracker.md#where-the-numbers-come-from)
- Why access times are grouped by rate before being believed —
  [Tracker](docs/tracker.md#time-nobody-was-watching)
- Why reading another process 64 KB at a time matters —
  [Tracker](docs/tracker.md#the-countdown-ring)
- Why a signed-out workshop listing is not the listing —
  [Review](docs/review.md#what-a-steam-web-api-key-is-for)
- Why the authors database is a SQLite file and not JSON —
  [Authors database](docs/authors-database.md#where-it-is)
- Why only eight previews animate at once —
  [Gallery](docs/gallery.md#it-animates-but-not-thirty-at-once)
- Why a vanity name is not an identity —
  [Authors database](docs/authors-database.md#why-an-author-can-have-two-keys)

**[Architecture and tests →](docs/development.md)**

## Tests

There is no test framework. Each file is a script that runs its own checks and
exits non-zero if any fail:

```
.venv\Scripts\python.exe tests\test_tracker.py
```

Most need PySide6, because they build real widgets. None need Wallpaper Engine,
windows on screen, or a network — the parts that do are behind an opt-in
`--live` flag.

## Contributing

Every change goes through a pull request. See **[CONTRIBUTING.md](CONTRIBUTING.md)**.

## Licence

[MIT](LICENSE).

Wallpaper Engine is a product of Kristjan Skutta / Wallpaper Engine Team. This
project is not affiliated with, endorsed by, or connected to it or to Valve.
