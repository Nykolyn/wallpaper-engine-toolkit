# Wallpaper Engine Toolkit

A Windows desktop application for people whose Wallpaper Engine library has
outgrown Wallpaper Engine: tens of thousands of wallpapers, a reserve that has
to be cycled through `myprojects`, clips waiting to become wallpapers, and a
weekly pile of new items whose authors are the part actually worth tracking.

Six tools in one window, sharing one dark theme and one set of habits.

| Tab | What it does |
|---|---|
| **[Copier](docs/copier.md)** | Duplicates wallpaper folders N times each — the weighting Wallpaper Engine's playlists do not have. |
| **[Creator](docs/creator.md)** | Turns video clips plus your own previews into Wallpaper Engine projects, in bulk. |
| **[Auto Creator](docs/auto-creator.md)** | Turns video clips into projects with **no preview needed** — it renders one from the video. |
| **[Rotator](docs/rotator.md)** | Moves folders between a reserve and `myprojects`, with duplicate handling, protected folders and a dead-folder check. |
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

- **Windows.** Several features call Windows directly: DPAPI for stored
  secrets, `DnsQuery_W` for MongoDB SRV lookups, Task Scheduler for autostart,
  and a read-only process-memory probe behind the Tracker's countdown.
- **Python 3.11+**, or the standalone build.
- **Wallpaper Engine** via Steam. Steam's registry entry and
  `libraryfolders.vdf` are read to locate it, so folder settings arrive already
  filled in.
- **A Steam Web API key and a MongoDB database** — for the Review tab only.
  Every other tab works without them.

## What it will not do

Nothing here writes to Wallpaper Engine. The Tracker reads its `config.json` and
the two state files in `bin/`, and its memory probe is opened read-only; none of
them are ever modified. Rotation moves your folders between your own
directories. The one action that reaches outside the machine is subscribing to a
workshop item, which is a deliberate click.

Nothing writes to a database without showing you the change first, as a line of
English, with a backup taken.

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
  [Review](docs/review.md#why-it-needs-a-steam-web-api-key)
- Why MongoDB is resolved through Windows instead of dnspython —
  [Review](docs/review.md#reaching-mongodb-through-a-vpn)
- Why only eight previews animate at once —
  [Gallery](docs/gallery.md#it-animates-but-not-thirty-at-once)
- Why a vanity name is not an identity —
  [Authors database](docs/authors-database.md#the-identifier-problem)

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
