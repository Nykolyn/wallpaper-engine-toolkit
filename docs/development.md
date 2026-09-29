# Development

## Architecture

The three original tools were each written against a different GUI toolkit
(tkinter, CustomTkinter, PySide6). This project unifies the **user interface**
onto one — PySide6/Qt with the *Fusion* style — so every page shares the same
widgets, progress bars and dialogs, painted from one theme.

The **implementation is deliberately unchanged**. Each original feature's core
engine is the original source, reused as-is:

```
app/
├── run_app.py is the entry point (one level up)
├── main_window.py        the frame: title bar, sidebar, page header, the pages, the status line
├── window_frame.py       the title bar's Windows side: hit testing, snap, DWM
├── window_instance.py    one window; the requests a second launch or the tray send it
├── selfcheck.py          what a build can import and reach (--selfcheck, Settings)
├── pages/                the window's pages, in the order of the loop:
│   ├── base.py           Page, and LegacyPage (an old tab in the frame)
│   ├── overview.py       the loop at a glance
│   ├── settings.py       what is set once
│   └── legacy.py         what the old tabs' sidebar items say
├── theme.py              the design tokens: colour, type, space, radius, shadows, the stylesheet
├── animations.py         motion tokens, the easing curve, reduced motion, the shared loops
├── data_location.py      where the data folder is, and moving it out of the program folder
├── settings.py           data/suite.json
├── secrets.py            data/secrets.json, DPAPI-encrypted
├── workers.py            Qt signal bridges for the callback engines
├── tracker_tray.py       the tracker as a tray-only app (--tracker)
├── tracker_feed.py       one tracker, looked at when Wallpaper Engine writes
├── autostart.py          the logon task, and the rename migration
├── external.py           starting other programs without the toolkit's own DLLs
├── services/             what the pages report their work to, and read the loop from:
│   ├── jobs.py           JobCenter: what is running, which job leads, rate and time left
│   ├── activity.py       ActivityJournal: data/activity.jsonl; PlaylistWatch
│   ├── logstore.py       LogStore: data/logs/<tool>/, 30 days
│   ├── snapshot.py       Snapshot: the reserve, myprojects, the last run, read off the GUI thread
│   ├── runs.py           begin(): a page's work told to all of them at once
│   └── textfile.py       reading a file from its end
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
│   ├── review.py         the weekly walk itself
│   └── rotator/
│       ├── config.py     verbatim  wallpaper_rotator/app/config.py
│       ├── core.py       + the [protected] rule
│       └── worker.py     + closing and restarting Wallpaper Engine around a run
└── ui/
    ├── kit/              the redesign's components: icons, the controls
    │                       (base, buttons, inputs, selection, chips, panels),
    │                       the formats, data display (paths, tags, progress,
    │                       cards, tables, thumbs), feedback (log, toast,
    │                       statusline, dialogs), and the frame (shell)
    ├── copier_tab.py, creator_tab.py
    ├── rotator_tab.py, cleanup_dialog.py
    ├── tracker_tab.py
    ├── review_tab.py, gallery.py
    ├── credentials.py    the optional Steam key
    ├── authors_dialog.py the authors database, its backups, restoring one
    └── widgets.py        verbatim  wallpaper_rotator/app/ui/widgets.py
```

`tools/kit_preview.py` and `tools/ui_snapshot.py` sit outside the app: a
window that draws the design system from the app's own code, and a picture of
the real window in a made-up state (see [Look and feel](#look-and-feel) and
[Snapshots](#snapshots)).

Only the GUI layer is new. The callback-based Copier and Creator engines are
driven through small Qt signal bridges in `app/workers.py`, so their background
threads update the UI safely.

## The frame and its pages

`MainWindow` is a fixed frame: the `TitleBar`, the `Sidebar`, a `PageHeader`
over a `QStackedWidget` of pages, and the `StatusLine`. Pages are in the order
of the loop — `overview`, `rotator`, `tracker`, `review`, `creator`,
`copier`, `settings` (`main_window.PAGE_ORDER`) — and Ctrl+1…7 go to each.
Changing page cross-fades the content only (`animations.CrossFade`: a still
of the page that leaves, fading over the live one that arrives, over
`motion.base`; instant with motion off); the sidebar, title bar and status
line never move. Below `theme.RAIL_BELOW` (1 200 px) the sidebar is a 56 px
rail.

A page is an `app.pages.Page`:

| | |
|---|---|
| `key`, `title`, `icon` | which page, its name, its sidebar glyph |
| `subtitle()`, `set_subtitle()`, `subtitle_changed` | the line beside the title |
| `make_header_actions()` → `header_actions()` | the header's buttons, made once, kept |
| `nav_state()`, `set_nav_state()`, `nav_state_changed` | its sidebar item: a `NavState` |
| `navigate` | asks the window for another page, by key |
| `on_shown()`, `on_hidden()` | it came on screen, or went |
| `FIXTURES`, `load_fixture(state)` | made-up states for `tools/ui_snapshot.py` |

A `NavState` is one of the design's: `NavState.progress(done, total)` (a bar
and `41%` under the name), `count(done, total)` (`4/201` and a mini bar),
`badge(n)` (warn), `status(text, tone, below=)` (`idle`; `2 problems` in warn
under the name), or nothing. In the rail each is an icon with a dot for the
bar, the badge and a warn or danger word.

`LegacyPage(key, title, icon, tab, subtitle)` hosts one of the old tabs until
its page step replaces it, and `app/pages/legacy.py` works out its sidebar
item from the services (a job running, the Rotator's last run, the leading
monitor's count). A tab with a `settings_requested` signal gets its
**Change in Settings** wired to the Settings page. `build_pages(window)` makes
them all: one Rotator `Config` is shared by the Rotator tab and the Settings
page, whose Rotator fields are read-only while a Rotator job runs.

The status line reads the `JobCenter` (`StatusBinding`): the job that leads,
its phase, bar and `412 / 1 000 · 41%`, and **Show**, which goes to
`job.page`; otherwise "Nothing running · last run finished 13:58"; a job that
ended with problems or failed is said in warn or danger until its page has
been looked at. **Next in the loop** is the Snapshot's `PLAYLIST`.

**The title bar is the window's own** (gate G1): the window is frameless to
Qt and keeps its caption and resizable frame as far as Windows knows.
`app/window_frame.py` answers `WM_NCCALCSIZE` (no frame) and `WM_NCHITTEST`:
the edges resize, the title bar is the caption (drag, Aero Snap, double-click,
the system menu), the maximise button is `HTMAXBUTTON` (Windows 11's snap
layouts; its hover and click are handed back to the button), and the rest is
Qt's. Maximised, the content pads itself in by however far Windows puts the
frame off the screen. Offscreen there is no native frame, and the title bar
moves the window through `startSystemMove()`.

**Overview** (`app/pages/overview.py`) is built only from what the window
already holds — the Snapshot's readings, the JobCenter, the TrackerFeed's
`results`, the journal — and its constructor touches no file
(`tests/test_overview.py` guards it). The journal and the log are read once
the page is on screen: `journal.recent(8)` after the first `on_shown`, then
`appended`; the log panel follows the running job's `log_path` (or
`LogStore.files()[0]`) with a `LogTail`, a second at a time while visible.
The words are plain functions of those sources — `reserve_card`,
`rotation_card`, `playlist_card`, `review_card` (→ `CardText`),
`rotator_tile`, `tracker_tile`, `review_tile`, `job_tile` (→ `TileText`),
`loop_subtitle`, `loop_sentence`, `activity_row` (→ `ListRow`),
`monitor_view(s)` (→ `MonitorView`) — which the tests call directly. A
reading never read yet is a shimmer; one that failed with nothing before it
is an empty state picked by its `reason`; one read before but not now is
`lo` and "last known". Its fixtures are `tests/fixtures/ui/overview.json`
(`running`, `idle`, `empty`, `we-off`).

**Window requests** (`window_instance`): a second launch or the tray sends one
line — `show <page>`, which every version understands, or the command form
`<verb>:<argument>` (`show:rotator`; `rotate:confirm` is the tray's, to
come). `WindowInstance.command_received(verb, argument)` hands each to
`MainWindow.handle_command`; a verb this version does not know still brings
the window forward. `--tab <page>` takes the old tab names and the page keys,
in any case; anything else opens Overview.

## Services

`app/services/` is what the status line, the sidebar and Overview read, and
what every page reports its work to. The main window makes one `Services`
(with its own `TrackerFeed`, which it also hands the Tracker page) and
installs it; `services.current()` is that one, or None for a tab built on its
own, as the tests build them.

**A page's work** goes through `begin(tool, title, page=None, activity=None,
run_id=None)`, which returns a `Run`: `update(phase_text, done, total,
count_text)`, `log(kind, message)`, `log_text("[WARN]  …")` for the callback
engines' own lines, `note(kind, title, detail, chip, run)` for a journal entry
on the way, and `finish(result, summary, title=, detail=, chip=, run=,
journal=True)` / `fail(message)`. `finish` ends the job, writes the last log
line, and journals `<activity>.<result>`. With no services installed every
call does nothing. The old tabs are wired this way, thinly, until their pages
replace them.

**`JobCenter`** (`jobs.py`). `start(tool, title, page)` → `Job`;
`Job.update(phase_text, done, total, count_text=None)` (what is left out keeps
its value), `Job.finish(result, summary)` with result `clean`, `problems`,
`stopped` or `failed`, `Job.fail(message)`. Signals `changed(job)` and
`finished(job)`. `current()` is the job the status line leads with: the lowest
`PRIORITY` (rotator 0, copier 1, creator 2, review 3, tracker 4), then the one
started last. `running()`, `is_running(tool)`, `last_finished(tool=None)`.
`Job.rate()` and `Job.eta()` are None until `MIN_SAMPLES` (5) counts in one
phase over `MIN_SPAN` (2 s); a new phase or total starts them over, and a job
that stalls shows its rate falling. A count alone is sent at most every 0.1 s
(the last one always arrives); a new phase and the end at once. Nothing polls.

**`ActivityJournal`** (`activity.py`). `add(tool, kind, title, detail="",
chip=None, run=None)` appends a line to `data/activity.jsonl` — `{ts, tool,
kind, title, detail, chip, run}`, `ts` local with its offset — and emits
`appended(entry)`. `recent(n)` reads the newest `n` from the end of the file,
across `activity.1.jsonl`; a line it cannot read and a field it does not know
are skipped, and `Entry.ts` comes back naive and local. Past 2 MB the file is
renamed to `activity.1.jsonl`. An append measured 0.15 ms (p95 0.23 ms), and
`recent(8)` 0.1 ms, so both run on the GUI thread.

The kinds: a piece of work ends as `<activity>.<outcome>` (`ACTIVITIES`:
`run`, `check`, `cleanup`, `duplicates_delete`, `duplicates_return` for the
Rotator; `scan`, `count`, `database` for Review; `build`; `copy`). `EVENTS`
are the rest: `run.started`, `duplicates.set_aside`, and the Tracker's
`playlist.advanced` (the leading monitor only), `playlist.finished` and
`playlist.restarted`, which `PlaylistWatch` writes from the window's
`TrackerFeed` — changes seen while the window is open, never the first look.
`known_kind(kind)` says whether this build writes a kind.

**`LogStore`** (`logstore.py`). `open(tool, run_id=None)` → a `LogWriter` on
`data/logs/<tool>/YYYY-MM-DD.log` (it moves to the next day's file at
midnight), or `data/logs/rotator/run-<id>.log` for one rotation.
`write(kind, message)` adds `HH:MM:SS<TAB>kind<TAB>message`, a line per line
of the message, line-buffered (7 µs a line, measured); `write_text` turns
`[TAG]` lines into kinds (`TAG_KINDS`). `tail(tool, n)` gives (time, kind,
message) tuples for `LogPanel.extend` — from the file written last, reaching
into older ones when it is short; `tail(None, n)` is any tool's. `folder(tool)`
and `open_folder(tool)` for "Open log folder". `sweep()` deletes files older
than 30 days (a day's file by its name, a run's by its last write, nothing
named otherwise); `sweep_in_background()` is what the window runs at start-up.
`data/tracker.log` is the tray's and stays outside this. `LogTail(path, n)`
follows one file from its end: the first `read()` gives the last `n` lines,
each after that only the whole lines written since (a line cut short waits),
and a file that shrank is read from its end again with `restarted` set.

**`Snapshot`** (`snapshot.py`). `get(key)` → `Reading(value, at, error)`,
with `age()`; a read that fails keeps the value read before and sets `error`,
so a page can say "last known", and `reason` — `unset` (the folder is not
chosen), `missing`, `unreadable` or `error` — for it to pick its empty state
by. The keys: `RESERVE` (`ReserveCounts`:
folders, never used, `will_reset`, batch), `ROTATION` (`RotationCounts`:
folders in myprojects, protected, moved in today), `LAST_RUN` (`RunSummary`,
with the result and times from step 09's `run_meta.json` beside
`history.json` when it has the run, `from_side_file`), `PLAYLIST`
(`PlaylistProgress` of the leading monitor, from the feed; `from_engine` says
whether "not live" means Wallpaper Engine is not running) and `REVIEW` (the
dict in `review_last.json`, or None; `ReviewState.from_json` reads it, and
its docstring is the file's shape for step 11 to write: `scanned`, `scope`,
`since`, `items`, `authors` as `[{name, new, done}]`, `finished`).
`parse_estimate(text, now)` turns the tracker's "21 Sep 09:10" into a moment. `refresh(keys=None)` returns at once:
the playlist is read from the feed in memory, everything else on one worker
thread, one refresh at a time (asking during one queues the keys).
`refreshed(keys)` follows. It refreshes itself when a rotation, a copy, a
build or a Review job finishes, and when the feed looks. Nothing it reads
writes: it reads `config.json` without `Config.load()`'s default save, and
the history through `read_runs()`, never `History.load()`'s repairs. On the
real reserve (33 619 folders on the W: disk) the worker took 1.0 s cold and
0.26 s warm.

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
| `test_theme.py` | every token parses, text stays legible on glass, fonts, shadows, the stylesheet fills in and ticks its check boxes |
| `test_icons.py` | every icon draws, in the colour and at the size asked; unknown names raise |
| `test_kit_controls.py` | every kit control in every state; the fourteen chips, the Pagination rule, the Toggle with motion off, Dropdown rows that cannot be chosen, a DangerButton that never takes Enter, the ring for the keyboard only |
| `test_kit_feedback.py` | the log's 5 000-line ring and its Problems filter; the console following the newest line until you scroll up; the panel closing over `motion.slow`; toasts stacking, going after 6 s and danger staying; the status line's four states and a count that never elides; a destructive dialog defaulting to Cancel, its group boxes, summary and Danger text following the ticks, Esc cancelling; a form's Save waiting for valid fields |
| `test_kit_data.py` | the formats; a PathField checked on a worker; a per-clip TagSelect's three states; MonitorView to card; TableModel groups, sorting and zebra; 33 000 rows built under 100 ms and only visible rows painted; local previews cached by path and time; no file-system call on the GUI thread |
| `test_animations.py` | motion, by sampling real widgets over real time; the curve, the loops, reduced motion |
| `test_steam_api.py` | the Web API client and its cache |
| `test_authors_store.py` | the authors database: transactions, snapshots, pruning, the second folder, restoring, damaged files |
| `test_review.py` | the weekly walk |
| `test_gallery.py` | every delegate, painted in every state; memory and animation bounds |
| `test_hang_watch.py` | a stuck GUI thread leaves its stacks in the hang log |
| `test_window_instance.py` | one window, raised from the tray, in a process of its own; the plain request and the command form on the socket |
| `test_shell.py` | pages in the loop's order, the sidebar and Ctrl+number; `--tab` names; the window command parser; the rail below 1 200 px; the status line bound to the JobCenter (Show, problems until seen); the cross-fade, and none with motion off; the Settings page writing `config.json` and `suite.json`, read-only while the Rotator works; the window and the tray reading each other's `suite.json`; the title bar's hit testing; `ui_snapshot` at a size and scale |
| `test_services.py` | which running job leads; rate and time left only once measured; the journal's append, tail and rotation past a damaged line and an unknown field; log files by tool, day and run, their tail and the 30-day sweep; the snapshot read on a worker, never the GUI thread, keeping its age and its last value; each tab's work reaching all three |
| `test_external.py` | a child cannot load a DLL from the bundle, through the DLL directory or PATH; a quoted URL survives cmd.exe; nothing in `app/` starts a program another way |

Most need **PySide6** (they build real widgets); none need Wallpaper Engine,
windows on screen, or a network.

### Opt-in live checks

Several take `--live`, which is the only part that touches a network or this
machine's real state:

```
.venv\Scripts\python.exe tests\test_steam_api.py --live
.venv\Scripts\python.exe tests\test_review.py --live
.venv\Scripts\python.exe tests\test_wallpaper_timer.py --live
```

`test_wallpaper_timer.py --live` skips itself if Wallpaper Engine is not
installed.

## Look and feel

The app is being rebuilt to a design made in Claude Design: dark "frosted
glass", translucent panels over a radial gradient. Everything visual comes from
`app/theme.py` and `app/animations.py`; nothing else names a colour, a size or
a duration.

### Tokens

Colours are keyed by the design's dotted names, and hold the design's values
exactly — a hex colour or `rgba()` with a 0–1 alpha:

```python
theme.color("text.lo")          # QColor, a copy
theme.css("surface.well")       # "#80080A0E": QSS, QColor() and rich text all read it
theme.css("accent", 0.3)        # the accent at 30 %, for the design's disabled fills
theme.composite("surface.raised", "bg.solid")   # what a translucent token shows
```

Most surfaces are white at a few percent rather than a grey, so they pick up
the gradient under them. That also means a colour's legibility can only be
judged over what it sits on: `theme.panel_ground()` is a panel's ground as text
sees it, and `test_theme.py` holds `text.lo` to 4.5:1 on it.

Three surfaces are gradients: `bg.app` (the window, `paint_app_background()` or
`app_background(rect)` for a brush to keep), `surface.glass` and `nav.gradient`
(`theme.gradient(name, rect)`).

The design is dark only. The names say what a colour is for, not what it looks
like, so a light palette can be added later without touching the UI.

### Type

`theme.font("type.h3")` gives a QFont for a type token; `theme.qss_font()` the
same as stylesheet declarations. Sizes are CSS px, which are Qt logical px, and
go in as points (`px × 0.75`): 11.5 px has no integer pixel size, and Qt scales
by the display itself, so nothing multiplies by the device pixel ratio. Prose
is Segoe UI Variable Text (Segoe UI where that is missing); anything counted,
pathed or logged is Consolas, whose numerals are tabular. `line_height(token)`
gives the design's line height in px for layouts that stack lines by hand.

### Space, radius, elevation

`SPACING` (`sp.2` … `sp.32`, a 2 px grid) and `RADIUS` (`r.sm` 4 … `r.xl` 12,
`r.pill`) are dicts by name, and constants (`SP_12`, `R_LG`) for code.

`ELEVATION` holds the four shadows. `paint_shadow(painter, rect, "elev.2")`
draws one around a box from a pre-blurred nine-slice pixmap (`shadow(elev)`,
made once per elevation and screen scale in a few ms). Outer shadows are drawn
only outside the box, as CSS draws them — panels are translucent, and a shadow
under one would read as a darker panel. `elev.inset` darkens a well's top edge
inside the box.

### The stylesheet

Qt draws the standard controls — buttons, inputs, spin and combo boxes, check
boxes, lists and headers, scroll bars, menus, tool tips, the old tab strip —
from one stylesheet, generated from a template with `$(token)` placeholders.
`theme.unresolved(qss)` lists any left unfilled, and the test fails on them: Qt
silently drops a rule it cannot read.

Check marks and spin and combo arrows are icons drawn into small SVG files in
the temp folder (a stylesheet only takes a file), named by their contents so
two versions running side by side never share one. `--selfcheck` confirms the
build can read SVG; without it a check box would never show its tick.

The kit's fields and check box are Qt's own controls under the same
stylesheet; its rules select them by class name (`TextInput`, `Dropdown`) and
by the properties the classes set: `forceState`, `error`, `focusVisible`. Kit
labels name their colour with a `tone` property (`QLabel[tone="lo"]`) rather
than carrying a stylesheet each. Component sizes come from the metrics at the
end of `theme.py` (`BUTTON`, `CONTROL_HEIGHT`, `CHIP_HEIGHT`, …), as `$(px:…)`
in the template.

`theme.apply(app)` sets Fusion, the palette, the font and the stylesheet, and
reads Windows' animation switch. The old tabs' helpers (`label_style`,
`console_style`, `card_style`, `status_color`, `level_color`, `kind_color`,
`make_accent`) are mapped onto the tokens until the pages that call them are
replaced.

### Icons

`app/ui/kit/icons.py` holds the design's 26 icons, and the few glyphs the
screens draw outside that set, as SVG text — nothing to bundle, nothing read
from disk. `icon(name, colour, size)` gives a QIcon (with a pixmap per screen
scale, and a 40 % disabled state); `pixmap(name, colour, size, dpr)` a pixmap
for painting; `svg(name, colour, stroke=)` the text. Colours are tokens or
QColors. Everything is cached, so painting an icon never parses SVG.

### Motion

Three durations and one curve carry the whole app:

| Token | ms | Used for |
|---|---|---|
| `FAST` | 90 | hover tint, icon colour |
| `BASE` | 140 | button fill, toggle knob, the cross-fade between pages |
| `SLOW` | 220 | progress width, a panel expanding |
| `FLASH` | 880 | a count that changed by itself, fading back (four `SLOW` beats) |
| `TOAST_IN` / `TOAST_OUT` | 180 / 120 | a toast rising in, and fading out (`anim.toastIn`) |

`ease()` is the design's `cubic-bezier(.2,.7,.3,1)`, for everything except
spinners.

- **Progress eases instead of jumping.** `SmoothProgressBar` animates a private
  float property that writes the real value, because animating `value` itself
  would land back in `setValue` and recurse. A jump backwards is a run starting
  over, and a hidden bar has nothing to show, so both are applied at once.
- **Pages cross-fade** (`fade_in`, `FadingTabWidget`). The opacity effect is
  removed the moment the fade ends — leaving one attached routes every later
  repaint through an offscreen pixmap.
- **A count that changed by itself flashes** (`flash`), and turns green rather
  than blue when the playlist is finished.
- **Loops share one clock per kind.** `loop("spin")`, `"pulse"`, `"shimmer"`
  and `"indeterminate"` are drivers a widget `subscribe()`s to and reads
  `value()` from in its paint: a spinner's angle, a live dot's opacity, a
  skeleton row's opacity (with a `delay` for staggering), the sweep's travel.
  A driver's timer runs only while a subscriber is visible — a hidden page or
  a minimised window stops it.

What does not move: the frame, table rows and gallery cards (no enter
animations), dialogs, and numbers (they step, they do not roll).

**Reduced motion.** When Windows' "Animation effects" are off
(`SystemParametersInfoW(SPI_GETCLIENTAREAANIMATION)`), `theme.apply()` sets
`animations.ENABLED = False`: every transition becomes an instant change and
the loops stand on their resting frame.

### The kit

`app/ui/kit/` holds the components the redesigned pages are built from. Each
class is named as in the design, and each has all of the design's states.
The pages move onto them one step at a time: the frame, Settings and
Overview are built from it; the Review gallery uses its preview loader.

| Module | Classes |
|---|---|
| `buttons.py` | `AccentButton` (the one action that starts the work), `SecondaryButton`, `DangerButton` (never a dialog's default, so Enter cannot delete), `GhostButton` (`outlined=True` for page headers), `IconButton` (30 px, or 22 px as `size="sm"`; its tool tip is required), `LinkButton` (an action written as a link, for a toast, the status line or a list; never a dialog's default). Text buttons take a leading `icon=`, a trailing `key="Ctrl+V"` cap, and `size="sm"`/`"md"`/`"lg"`. |
| `inputs.py` | `TextInput` (`search=True`, `set_error(message)`), `SpinBox` (mono, `1 000` with a no-break space), `Dropdown` (`add_item(text, data, count=)`, `add_section`, `add_separator`, `prefix="SORT"`) and its list, `DropdownPopup` |
| `selection.py` | `Checkbox`, `Toggle` (`knob_position`), `SegmentedControl` (two or three segments, `changed`), `Pagination` (`page_changed`), `page_numbers(pages, current)` |
| `chips.py` | `Chip(variant, text=None)` in exactly fourteen variants; `chip_pixmap` and `chip_size` for delegates |
| `panels.py` | `GlassPanel` (`tone=`, `padding=`), `Overline`, `Rule` (a hairline between two parts of a panel), `CardTitle`, `Callout` (`tone=`, `title=`, `add_action`), `MetricStrip` |
| `base.py` | the state model and the surfaces, below; `label(text, type, tone)`, `Glyph`, `Elided` (one line cut with an ellipsis, whole in its tool tip) and `LiveDot` (the pulse of a running job) |
| `format.py` | how every number is written: `count` (`33 421`), `size` (`1.1 GB`), `duration` (`4 min 12 s`), `left` (`≈6 min left`), `approx` / `reconstructed` (`≈`, `~`), `clock` (`13:47`, or `13:47:02` for a log line), `date_table`, `date_activity`, `date_long`, `day`, `ratio` (`4 / 201`, `4/201`, `412 of 1 000`), `percent`. Pages never format numbers themselves. |
| `paths.py` | `PathField`: empty (type, paste or Browse…), compact (path elided from the left, ✓, a folder button), invalid ("folder not found"), disabled; a drop target. `path_changed` for the user's choice, `validity_changed` when a worker has checked the folder. `kind="file"` (with `file_filter=`) holds one file instead: Wallpaper Engine's `config.json`. |
| `tags.py` | `TagSelect` (pills, `3 / 25`, a popup of the Creator's `WE_TAGS` in four columns); `per_file=True` adds the clip's three states — `value()` None follows the batch, a list is its own, `[]` is none. `TagPopup` is the open state. |
| `progress.py` | `ProgressBar` (3–8 px; determinate, eased over `motion.slow`; indeterminate; error; success; optional caption row) and `ProgressRing` (58, 52, 34 px; percentage, spin, done) |
| `cards.py` | `StatCard` (default, hover when `clickable` — an empty one too, `set_loading`, empty, `set_empty(why, link=True)` for a reason written as a link, tone `lo` for the last known), `MonitorCard` (compact or `detail=True`) and `MonitorView`, the plain values a page fills it from, `ToolTile` (a tool of the loop on the Overview: status line and tone, thin bar, mono meta; `set_active` accents the one whose job runs, with a pulsing dot; `clicked` on a click, Enter or Space) |
| `tables.py` | `Table`, `TableModel`, `Column`, `Cell`, `ChipCell`, `Group`, `RowDelegate`, `TableHeader`, `TableBar`, `TableSummary`, `TableFooter`; `ListRow`, `paint_list_row`, `RowList`; `Thumb` and `paint_thumb` |
| `thumbs.py` | `ThumbLoader`: Steam previews for the gallery (`request`), and a wallpaper folder's own preview (`request_local`), read on a worker and kept in `data/thumbs/local/` |
| `log.py` | `LogPanel` (a job's log as a card: `append(time, kind, message)`, All / Problems, copy, live dot, "writing to rotator.log" — or a file only read back, `set_file(name, writing=False)` — closes to its header with a problem badge; `fill=True` takes its layout's height and keeps the console open while collapsed, the Overview's glance), `LogModel` (the last 5 000 lines in a ring), `ProblemsFilter`, `LogView` |
| `toast.py` | `Toast` (ok, info, warn, danger; an action link; close) and `ToastHost` (stacks a page's toasts bottom-right, at most four) |
| `statusline.py` | `StatusLine`: `set_running(text, done, total, count_text, on_show)`, `set_idle(text)`, `set_warn(text, action, callback)`, `set_error(...)` |
| `dialogs.py` | `ConfirmDialog` (neutral or destructive; numbered `steps`; a checklist of `CheckGroup`s of `CheckRow`s; a summary; returns a `ConfirmResult`), `FormDialog` (labelled rows, Save once valid), and their chrome, `OverlayDialog` |
| `shell.py` | the frame: `TitleBar` (the mark, the name, `CaptionButton`s), `Sidebar` (`add_section`, `add_item`, `set_current`, `set_state`, `set_rail`, `page_requested`), `NavSection`, `NavItem`, `NavState`, `NextInLoop`, `PageHeader` (`set_title`, `set_subtitle`, `set_actions`), `BrandMark` — see [The frame and its pages](#the-frame-and-its-pages) |

**States.** Every interactive control follows the design's five: default,
hover (the pointer only), pressed, disabled, and a focus ring that shows when
the keyboard brought focus there — never after a click. Fields you type into
are the exception: their ring shows whenever they have focus, as a browser's
do. `force_state = "hover" | "pressed" | "focus"` draws a state without a
pointer or a keyboard, for the kit preview and snapshots; the app never sets it.
A button's fill eases between states over `motion.base`; its edge, text and box
change at once, and with motion off so does the fill.

**Painting outside the box.** CSS draws a shadow and a focus ring round a box
without either taking room in the layout; Qt clips every widget to its own
rectangle. So a control that draws outside itself (a `base.Caster`) hands that
part to the *surface* behind it — the nearest `GlassPanel`, a scroll area's
viewport, or any widget passed to `base.declare()` whose paintEvent then calls
`base.paint(painter, self, event.rect())` after its own ground. The surface
draws the outside parts before its children, which is where CSS puts them,
and repaints only the strip round a control whose outside changed. Put kit
controls on a surface; on anything else their shadow and ring may be hidden by
the parent's own background.

**Tables.** A `TableModel` takes the page's items as they are and a
`Column` spec per column: title, a content `width` (or None to share what is
left), `align`, `mono` (or a `font` token), `tone`, `sortable`, and a `thumb`
size or `icon` drawn before the text. A subclass says what a cell shows
(`cell(item, column)`: a str, a `Cell` for another tone, a `ChipCell`), what
it sorts by (`sort_key`), where a row's preview is (`thumb_source`) and
whether it is dimmed (`row_dimmed`). `set_rows(items, groups=[Group(...)],
group_of=fn)` puts header rows over runs of items ("ALREADY SHOWN THIS CYCLE ·
4 of 201"); sorting stays inside each group, a filter is `set_filter(fn)`, and
the zebra restarts under each header. The model holds a list of ints, not a
row object per item: 33 000 rows build in about 4 ms and sort in under 30.

`Table` paints its own viewport, one whole row per call, and only the rows on
screen. It keeps the number of calls into Qt per row low (about fifteen:
thumbs are tiles drawn once, chips are cached pixmaps, elided text and colours
are remembered), because PySide gives up the GIL on each call and another
thread busy in Python can make every one of them wait. Measured on a
33 000-row table with a thread spinning in Python: a scroll step at p95
3.7 ms, where drawing each part afresh took 85. Hover is tracked per row and
repaints the two rows it moved between; a click on a sortable column's title
sorts, keeping the selection.

**Thumbnails.** A table asks its `ThumbLoader` for the previews of the rows
on screen once scrolling has settled for 90 ms, and drops whatever it had
queued for rows scrolled past. The loader lists the wallpaper folder on a
worker, takes one still (a GIF's first bright frame), fits it into 480 × 270
and keeps it as a JPEG under `data/thumbs/local/`, named by the preview's
path, modification time and size: an unchanged preview is never decoded
twice, an edited one misses its old still, which is then deleted. Nothing on
the GUI thread touches the disk; `test_kit_data.py` checks it by making every
file-system call from the GUI thread fail while a table of thumbs is shown.

**The log.** A `LogPanel` is fed a line at a time, `append(time, kind,
message)` (`time` a datetime, a timestamp, text, or None for now), or
`extend(lines)` for many at once, which the view hears as one insert. Its
`LogModel` keeps the last 5 000 lines in a ring: past the cap the oldest line
leaves the front and nothing behind it moves. Kinds take the console's
colours: moved and done ok, skip and dupe warn, fail and error err, step,
start and info mid; any other kind is written as it is, in mid. The console
follows the newest line while it is at the bottom and stops the moment you
scroll up, keeping the lines you read where they are even as the oldest leave
the ring; back at the bottom it follows again. `copy()` (the button, or
Ctrl+C) takes the selected lines, or every line shown. Measured offscreen:
30 000 lines appended one by one past the cap, about 80 µs each; 5 000 at
once, 21 ms; a repaint of the console, 3.7 ms. `set_expanded(False)` closes
it to its header over `motion.slow`, chevron and height together, where a
badge counts the problems (danger once an error is among them). "Open log
folder" is a callback (`on_open_folder`) until the LogStore of step 05.

**Toasts.** `ToastHost(content)` covers the widget whose bottom-right corner
the toasts stack in — the window's content, not a page that scrolls — and
lets every click through; `show_toast(text, variant, action=, on_action=,
timeout=)` adds one at the bottom of the stack. A toast goes after 6 s, the
pointer resting on it holds it, and danger stays until closed. Four at most:
a fifth makes the oldest that may go leave. In the app only; a hidden window
leaves it to the tray.

**The status line.** `StatusLine` has a plain API; the window binds it to the
JobCenter (`StatusBinding`). The bar, count and link follow its words; the
words elide and the count never does.

**Dialogs.** Both draw a `scrim` over the window they belong to and a
frameless panel on `surface.overlay` at elev.3, with no motion. `ask()` runs
one and returns the answer; `embedded=True` makes it an ordinary child, as
the kit preview shows them.

```python
result = ConfirmDialog(
    "Delete 9 unusable folders?", "Deleting is permanent.", window,
    destructive=True, icon="trash",
    groups=[CheckGroup("Safe to delete", [CheckRow(name, reason, size, data=folder), ...],
                       tone="ok"),
            CheckGroup("Hold media", [...], tone="warn", initially_checked=False)],
    summary=None,                                  # "9 selected · 0 B" by default
    confirm_text=lambda rows: f"Delete {len(rows)} permanently",
    actions=[("Open folder", open_reserve)],       # GhostButtons; they do not close it
).ask()
if result:                                         # ConfirmResult: True when confirmed
    delete(row.data for row in result.checked)     # empty when cancelled
```

- `destructive=True`: a DangerButton, the warn tile, and Cancel as the
  default button and first focus, so Enter cancels. Esc cancels either kind.
- `steps=["Close Wallpaper Engine", ("Move 1 000 new folders in", "caption"), ...]`:
  the numbered list of what will happen.
- `groups=[CheckGroup(title, rows, tone=, initially_checked=, noun=, plural=,
  limit=5)]`: a checklist. The header row's tri-state box ticks or clears the
  whole group, rows not yet shown included; its title reads "SAFE TO DELETE —
  9 FOLDERS · 0 B". Past `limit` rows the rest fold under "N more like these".
  A size not measured (`None`) turns totals into "at least …".
- `summary(rows) -> str` writes the footer from the ticked rows;
  `confirm_text` is words or `fn(rows) -> str`, and with a checklist the
  button is off while nothing is ticked.

```python
form = FormDialog("Review settings", window)
form.add_row("Scan", every, note="The next scan is due Saturday.")
form.add_row("Steam Web API key", TextInput(), required=True,
             check=lambda f: None if len(f.text()) == 32 else "a key is 32 characters")
form.set_check(lambda form: None)                  # the form as a whole, if need be
if form.ask():                                     # True when saved
    ...
```

A check returns None when the field is right, a message when it is not
(shown once the field has been touched; a TextInput shows it as its error),
or False for "not yet" without one. Save stays off until every row and the
form's own check pass.

### The kit preview

```
.venv\Scripts\python.exe tools\kit_preview.py
.venv\Scripts\python.exe tools\kit_preview.py --grab buttons buttons.png
.venv\Scripts\python.exe tools\kit_preview.py --grab all <folder>
.venv\Scripts\python.exe tools\kit_preview.py --grab dialogs <folder>
```

A development window that draws the design system from the app's own code:
Colour, Type, Space/Radius/Elevation, Motion (the loops, live), Icons, Qt's
standard controls, and the kit's Buttons, Inputs, Selection, Chips and Panels
with every state in a row; then Fields, Progress, Cards, Tables (33 000
made-up rows, to feel the scroll) and Empty states with steps; then Feedback:
the log (one live), toasts over a stand-in page, the status line in each state
and one cycling through them, and the dialogs, open on the page and a button
each to open them over the window. `--grab dialogs <folder>` saves each dialog
open over a made-up page. Its previews are painted into a temp folder, so a
grab never shows your library. It is not
bundled and nothing in `app/` imports it.
`--grab` renders a section offscreen at 100 %, which is how a change is
compared with the design's own pictures. Each step that adds components to the
kit adds their section here; `shell` shows the frame's: every nav item state,
the rail beside the full sidebar, the title bar and its buttons, the header.

### Snapshots

```
.venv\Scripts\python.exe tools\ui_snapshot.py --page rotator --state running --out rotator.png
.venv\Scripts\python.exe tools\ui_snapshot.py --page settings --size 1040x720 --scale 1.5 --out s.png
.venv\Scripts\python.exe tools\ui_snapshot.py --list
```

The real `MainWindow`, offscreen, in a made-up state, saved as a PNG — what a
page step compares with the design's `screen-NN` pictures. It never reads this
machine's data: it runs in a data folder of its own under `%TEMP%`, with no
TrackerFeed and services that never start, and the old tabs are not built
(building them reads the library, Steam and Wallpaper Engine); a stand-in says
where each goes. Its Snapshot's worker reads nothing either: a fixture's
finished job would have it count the Rotator's folders, and with no config
that is the machine's own myprojects. The fixtures are in
`tests/fixtures/ui/`: `shell.json` holds the frame's states (`--list`: idle,
we-off, running, clean, problems, scanning, building, copying, failed,
empty) as jobs put in the JobCenter, a reading put in the Snapshot
(`Snapshot.put`), sidebar items pinned to a state and the line under Next in
the loop; `MainWindow.load_fixture(state)` applies one, then the page's own
`load_fixture` (its state of that name, or its first). A page with more to
make up keeps it in a file of its own: `overview.json`.
Folders in fixtures are on a drive `X:` the tool reports as present. `--scale
1.5` is the user's 150 %; the PNG is then 1.5 × the size.

## Conventions

- **Comments explain the decision, not the syntax.** Most of what is unusual in
  this codebase is unusual because a simpler version was tried and measured
  worse; the comment is where that measurement lives.
- **Numbers in comments and docs are measured**, not estimated. If a figure
  appears, it came from a run.
- **Nothing writes without saying what it would write.** The database plans
  first; the reserve check confirms first; rotation states the move first.
- **Other programs are started through `app/external.py`.** Use `popen`, `run`
  or `open_url`, not `subprocess`, `os.startfile` or `webbrowser`. A build
  otherwise hands every child its `_internal` folder, both as the DLL directory,
  which Windows searches before System32, and at the front of `PATH`. Wallpaper
  Engine restarted by a rotation ran on the build's `VCRUNTIME140.dll` that way,
  and kept `build.cmd` from replacing it. `test_external.py` fails on any other
  kind of launch in `app/`. The one exception is `window_instance.launch`,
  which starts the toolkit itself.
- **Line endings** are normalised to LF by `.gitattributes`, except `.cmd`
  files, which cmd.exe wants as CRLF.
