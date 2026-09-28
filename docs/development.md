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
├── main_window.py        the QMainWindow and the tab strip, on the app's gradient
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
    │                       cards, tables, thumbs), and feedback (log, toast,
    │                       statusline, dialogs)
    ├── copier_tab.py, creator_tab.py
    ├── rotator_tab.py, cleanup_dialog.py
    ├── tracker_tab.py
    ├── review_tab.py, gallery.py
    ├── credentials.py    the optional Steam key
    ├── authors_dialog.py the authors database, its backups, restoring one
    └── widgets.py        verbatim  wallpaper_rotator/app/ui/widgets.py
```

`tools/kit_preview.py` sits outside the app: a window that draws the design
system from the app's own code (see [Look and feel](#look-and-feel)).

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
| `test_window_instance.py` | one window, raised from the tray, in a process of its own |
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
The pages move onto them one step at a time; so far only the Review gallery
uses the kit, for its preview loader.

| Module | Classes |
|---|---|
| `buttons.py` | `AccentButton` (the one action that starts the work), `SecondaryButton`, `DangerButton` (never a dialog's default, so Enter cannot delete), `GhostButton` (`outlined=True` for page headers), `IconButton` (30 px, or 22 px as `size="sm"`; its tool tip is required), `LinkButton` (an action written as a link, for a toast, the status line or a list; never a dialog's default). Text buttons take a leading `icon=`, a trailing `key="Ctrl+V"` cap, and `size="sm"`/`"md"`/`"lg"`. |
| `inputs.py` | `TextInput` (`search=True`, `set_error(message)`), `SpinBox` (mono, `1 000` with a no-break space), `Dropdown` (`add_item(text, data, count=)`, `add_section`, `add_separator`, `prefix="SORT"`) and its list, `DropdownPopup` |
| `selection.py` | `Checkbox`, `Toggle` (`knob_position`), `SegmentedControl` (two or three segments, `changed`), `Pagination` (`page_changed`), `page_numbers(pages, current)` |
| `chips.py` | `Chip(variant, text=None)` in exactly fourteen variants; `chip_pixmap` and `chip_size` for delegates |
| `panels.py` | `GlassPanel` (`tone=`, `padding=`), `Overline`, `CardTitle`, `Callout` (`tone=`, `title=`, `add_action`), `MetricStrip` |
| `base.py` | the state model and the surfaces, below; `label(text, type, tone)`, `Glyph`, `Elided` (one line cut with an ellipsis, whole in its tool tip) and `LiveDot` (the pulse of a running job) |
| `format.py` | how every number is written: `count` (`33 421`), `size` (`1.1 GB`), `duration` (`4 min 12 s`), `left` (`≈6 min left`), `approx` / `reconstructed` (`≈`, `~`), `clock` (`13:47`, or `13:47:02` for a log line), `date_table`, `date_activity`, `date_long`, `day`, `ratio` (`4 / 201`, `4/201`, `412 of 1 000`), `percent`. Pages never format numbers themselves. |
| `paths.py` | `PathField`: empty (type, paste or Browse…), compact (path elided from the left, ✓, a folder button), invalid ("folder not found"), disabled; a drop target. `path_changed` for the user's choice, `validity_changed` when a worker has checked the folder. |
| `tags.py` | `TagSelect` (pills, `3 / 25`, a popup of the Creator's `WE_TAGS` in four columns); `per_file=True` adds the clip's three states — `value()` None follows the batch, a list is its own, `[]` is none. `TagPopup` is the open state. |
| `progress.py` | `ProgressBar` (3–8 px; determinate, eased over `motion.slow`; indeterminate; error; success; optional caption row) and `ProgressRing` (58, 52, 34 px; percentage, spin, done) |
| `cards.py` | `StatCard` (default, hover when `clickable`, `set_loading`, empty), `MonitorCard` (compact or `detail=True`) and `MonitorView`, the plain values a page fills it from |
| `tables.py` | `Table`, `TableModel`, `Column`, `Cell`, `ChipCell`, `Group`, `RowDelegate`, `TableHeader`, `TableBar`, `TableSummary`, `TableFooter`; `ListRow`, `paint_list_row`, `RowList`; `Thumb` and `paint_thumb` |
| `thumbs.py` | `ThumbLoader`: Steam previews for the gallery (`request`), and a wallpaper folder's own preview (`request_local`), read on a worker and kept in `data/thumbs/local/` |
| `log.py` | `LogPanel` (a job's log as a card: `append(time, kind, message)`, All / Problems, copy, live dot, "writing to rotator.log", closes to its header with a problem badge), `LogModel` (the last 5 000 lines in a ring), `ProblemsFilter`, `LogView` |
| `toast.py` | `Toast` (ok, info, warn, danger; an action link; close) and `ToastHost` (stacks a page's toasts bottom-right, at most four) |
| `statusline.py` | `StatusLine`: `set_running(text, done, total, count_text, on_show)`, `set_idle(text)`, `set_warn(text, action, callback)`, `set_error(...)` |
| `dialogs.py` | `ConfirmDialog` (neutral or destructive; numbered `steps`; a checklist of `CheckGroup`s of `CheckRow`s; a summary; returns a `ConfirmResult`), `FormDialog` (labelled rows, Save once valid), and their chrome, `OverlayDialog` |

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

**The status line.** `StatusLine` has a plain API for now; the JobCenter
drives it from step 05. Its words elide and its count never does.

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
kit adds their section here.

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
