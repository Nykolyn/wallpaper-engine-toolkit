# Development

## Architecture

The three original tools were each written against a different GUI toolkit
(tkinter, CustomTkinter, PySide6). This project unifies the **user interface**
onto one — PySide6/Qt with the *Fusion* style — so every tab shares the same
widgets, progress bars and dialogs, painted from one theme.

The **implementation is deliberately unchanged**. Each original feature's core
engine is the original source, reused as-is:

```
app/
├── run_app.py is the entry point (one level up)
├── main_window.py        the QMainWindow and the tab strip
├── theme.py              every colour, font and radius
├── animations.py         the motion, and the switch that turns it off
├── settings.py           data/suite.json
├── secrets.py            data/secrets.json, DPAPI-encrypted
├── workers.py            Qt signal bridges for the callback engines
├── tracker_tray.py       the tracker as a tray-only app (--tracker)
├── tracker_feed.py       one tracker, looked at when Wallpaper Engine writes
├── autostart.py          the logon task, and the rename migration
├── engines/
│   ├── steam_paths.py    where Steam, its libraries and Wallpaper Engine are
│   ├── copier.py         verbatim  wallpaper_copier/copier.py
│   ├── creator.py        projects from videos: ffmpeg preview.gif + project.json
│   ├── tracker.py        playlist progress, and when to look at it
│   ├── wallpaper_timer.py  the countdown, the PLPV0005 parser, the file watcher
│   ├── we_memory.py      reading wallpaper64.exe's timer, read-only
│   ├── engine_control.py closing Wallpaper Engine as its tray does, starting it again
│   ├── playlist_refresh.py the rotation's playlist: found by contents, refilled, restarted
│   ├── steam_api.py      the Steam Web API
│   ├── steam_ugc.py      Steamworks, for subscribing
│   ├── library.py        what is subscribed now, and what was owned once
│   ├── authors_store.py  the authors database: SQLite, snapshots, the journal
│   ├── authors_db.py     the old MongoDB collection  ┐ only the import tool
│   ├── mongo_srv.py      mongodb+srv through Windows ├ uses these; they go
│   ├── migration.py      re-keying that collection   ┘ in the next release
│   ├── review.py         the weekly walk itself
│   └── rotator/
│       ├── config.py     verbatim  wallpaper_rotator/app/config.py
│       ├── core.py       + the [protected] rule
│       └── worker.py     + closing and restarting Wallpaper Engine around a run
└── ui/
    ├── copier_tab.py, creator_tab.py
    ├── rotator_tab.py, cleanup_dialog.py
    ├── tracker_tab.py
    ├── review_tab.py, gallery.py
    ├── credentials.py    the optional Steam key
    ├── authors_dialog.py the authors database, its backups, restoring one
    └── widgets.py        verbatim  wallpaper_rotator/app/ui/widgets.py
```

Only the GUI layer is new. The callback-based Copier and Creator engines are
driven through small Qt signal bridges in `app/workers.py`, so their background
threads update the UI safely.

## Tests

There is **no test framework**. Each file is a script that runs its own checks
and exits non-zero if any fail. Nothing is installed to run them:

```
.venv\Scripts\python.exe tests\test_tracker.py
```

Run the lot:

```
for %f in (tests\test_*.py) do .venv\Scripts\python.exe %f
```

| File | Covers |
|---|---|
| `test_creator.py` | project.json, and which tags win between a batch and one clip |
| `test_tracker.py` | anchoring a cycle, rebuilding history, merging two writers, following the engine's deck, when to look |
| `test_wallpaper_timer.py` | the PLPV0005 parser, the file watcher, the countdown, pause rules |
| `test_playlist_refresh.py` | finding the rotation's playlist, refilling it, restarting one monitor's pass, the state file written back byte for byte |
| `test_rotator_cleanup.py` | the reserve check and what it offers to delete |
| `test_autostart.py` | the command line, the task XML, and the rename migration |
| `test_animations.py` | motion, by sampling real widgets over real time |
| `test_steam_api.py` | the Web API client and its cache |
| `test_authors_store.py` | the authors database: transactions, snapshots, pruning, the second folder, restoring, damaged files, the Mongo import |
| `test_authors_db.py` | the old Mongo collection, for the import tool |
| `test_migration.py` | the identifier re-keying |
| `test_mongo_srv.py` | SRV/TXT resolution and URI rewriting |
| `test_review.py` | the weekly walk |
| `test_gallery.py` | every delegate, painted in every state; memory and animation bounds |
| `test_hang_watch.py` | a stuck GUI thread leaves its stacks in the hang log |
| `test_window_instance.py` | one window, raised from the tray, in a process of its own |

Most need **PySide6** (they build real widgets); none need Wallpaper Engine,
windows on screen, or a network.

### Opt-in live checks

Several take `--live`, which is the only part that touches a network or this
machine's real state:

```
.venv\Scripts\python.exe tests\test_steam_api.py --live
.venv\Scripts\python.exe tests\test_authors_db.py --live
.venv\Scripts\python.exe tests\test_authors_db.py --live-write
.venv\Scripts\python.exe tests\test_review.py --live
.venv\Scripts\python.exe tests\test_wallpaper_timer.py --live
set WET_TEST_CLUSTER=mycluster.ab12c.mongodb.net
.venv\Scripts\python.exe tests\test_mongo_srv.py --live
```

`test_mongo_srv.py --live` skips itself unless `WET_TEST_CLUSTER` names a
cluster — no one's infrastructure is written into this repository.
`test_wallpaper_timer.py --live` skips itself if Wallpaper Engine is not
installed.

## The theme

Every colour, radius and font comes from `app/theme.py` and nothing else names
one. It holds a token table, a `QPalette` for what Qt draws itself, and one
stylesheet for the rest; `theme.apply(app)` paints the whole application before
any window is built.

The palette is dark by default. `PALETTES["light"]` is the same tokens the other
way round.

Widgets that stay hand-styled ask the theme for a fragment rather than a
literal: `console_style()` for the log panels (kept as terminals on purpose),
`card_style()` for the Creator's clip cards, `label_style()` for secondary text,
`level_color()` for log severities, `status_color()` for ready/skipped/built.
`make_accent(button)` marks the one button that starts the work, so Build and
Cancel are told apart at a glance rather than by reading them.

Spin arrows, checkmarks and radio dots are deliberately **not** restyled: they
are drawn by Fusion from the palette. Overriding the boxes without supplying the
glyphs is exactly how a themed app ends up with checkboxes that never show a
tick.

## Motion

Qt stylesheets have no transitions, so everything that moves is a real animation
on a property, in `app/animations.py`.

**The rule throughout: motion only earns its place when it makes a change of
state legible.** Nothing loops, nothing decorates, and nothing delays a click.
Durations are 120 ms for pointer feedback and 180 ms for a page or a bar,
because past about 200 ms on a click motion stops reading as responsiveness and
starts reading as lag.

- **Progress eases instead of jumping.** `SmoothProgressBar` replaces the plain
  bar everywhere. Qt animates a property through its WRITE method, so animating
  `value` directly would land back in `setValue` and recurse; it animates a
  private float property that writes the real one. A jump *backwards* is a run
  starting over, and a hidden bar has nothing to show, so both are applied at
  once rather than crawling.
- **Tab pages fade in.** The opacity effect is removed the moment the fade
  ends — leaving one attached routes every later repaint through an offscreen
  pixmap, a real cost on a tab holding hundreds of cards. Measured at 2–8 ms per
  switch.
- **The Tracker's count flashes** when it changes, and turns green rather than
  blue when the playlist is finished.
- **Cards light up under the cursor** and **buttons travel a pixel when
  pressed** — both pure stylesheet, applied instantly.
- **Scanning shows a busy cursor.** Reading a source folder takes about half a
  second: long enough that a frozen window looks broken, short enough that
  moving it to a thread would cost more than it returns.

`animations.ENABLED = False` turns all of it off in one go.

## Conventions

- **Comments explain the decision, not the syntax.** Most of what is unusual in
  this codebase is unusual because a simpler version was tried and measured
  worse; the comment is where that measurement lives.
- **Numbers in comments and docs are measured**, not estimated. If a figure
  appears, it came from a run.
- **Nothing writes without saying what it would write.** The database plans
  first; the reserve check confirms first; rotation states the move first.
- **Line endings** are normalised to LF by `.gitattributes`, except `.cmd`
  files, which cmd.exe wants as CRLF.
