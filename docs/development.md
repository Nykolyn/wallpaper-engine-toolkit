# Development

## Architecture

The three original tools were each written against a different GUI toolkit
(tkinter, CustomTkinter, PySide6). This project unifies the **user interface**
onto one — PySide6/Qt with the *Fusion* style — so every page shares the same
widgets, progress bars and dialogs, painted from one theme.

The engines remain independent of their pages. The redesign adds worker-based
metadata, sequential queues and verified writes while retaining their existing
entry points:

```
app/
├── run_app.py is the entry point (one level up)
├── main_window.py        the frame: title bar, sidebar, page header, the pages, the status line
├── window_frame.py       the title bar's Windows side: hit testing, snap, DWM
├── window_instance.py    one window; the requests a second launch or the tray send it
├── branding.py           the product's names: "Toolkit", "Toolkit for Wallpaper Engine"
├── selfcheck.py          what a build can import and reach (--selfcheck, Settings)
├── pages/                the window's pages, in the order of the loop:
│   ├── base.py           Page, SideScroll (a page's 330 px left column)
│   ├── overview.py       the loop at a glance
│   ├── rotator.py        the next run, a run under way, how it ended; reserve, current, history
│   ├── tracker.py        each monitor's playlist, counted down; its table
│   ├── review.py         scan, go through the authors, finish; review_settings.py its
│   │                       two dialogs, review_fixtures.py its made-up states
│   ├── settings.py       what is set once
│   ├── creator.py        video metadata, selection, builds and results
│   └── copier.py         sequential duplication queue, measurements and results
├── theme.py              the design tokens: colour, type, space, radius, shadows, the stylesheet
├── animations.py         motion tokens, the easing curve, reduced motion, the shared loops
├── data_location.py      where the data folder is, and moving it out of the program folder
├── settings.py           data/suite.json
├── secrets.py            data/secrets.json, DPAPI-encrypted
├── tracker_tray.py       the tracker as a tray-only app (--tracker)
├── tray_icon.py          the tray's ring: four states, drawn per size; the taskbar's colour
├── tray_menu.py          the tray's menu: a QMenu that paints itself
├── tray_words.py         the tray's tooltip, menu hints and balloon copy; three small readers
├── tracker_feed.py       one tracker, looked at on a worker when Wallpaper Engine writes
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
│   ├── steam_paths.py    where Steam, its libraries and Wallpaper Engine are, found in the background
│   ├── copier.py         sequential duplication queue, verification and retry
│   ├── creator.py        projects from videos: ffmpeg preview.gif + project.json
│   ├── tracker.py        playlist progress, and when to look at it
│   ├── wallpaper_timer.py  the countdown, the PLPV0005 parser, the file watcher
│   ├── wallpaper_meta.py a wallpaper's title and type, and its author from Review's cache
│   ├── we_memory.py      reading wallpaper64.exe's timer, read-only
│   ├── engine_control.py closing Wallpaper Engine as its tray does, starting it again
│   ├── playlist_refresh.py the rotation's playlist: found by contents, refilled, restarted
│   ├── steam_api.py      the Steam Web API
│   ├── steam_ugc.py      Steamworks, for subscribing
│   ├── library.py        what is subscribed now, and what was owned once
│   ├── library_index.py  every folder of the reserve and myprojects described: library_meta.json
│   ├── authors_store.py  the authors database: SQLite, snapshots, the journal
│   ├── review.py         the weekly walk itself
│   ├── review_flow.py    a scan as one flow (find, count, carry on); review_last.json
│   └── rotator/
│       ├── config.py     Config, RunRecord, History (+ never saved over unread), Usage
│       ├── core.py       the steps, the rotation, stop between steps, retry, the reserve check
│       ├── meta.py       run_meta.json: what a run leaves beside its record; estimates
│       ├── runner.py     a run, a retry, a playlist rebuild, start to finish, without Qt
│       └── worker.py     the same on threads, and the duplicates and the reserve check
└── ui/
    ├── kit/              the redesign's components: icons, the controls
    │                       (base, buttons, inputs, selection, chips, panels),
    │                       the formats, data display (paths, tags, progress,
    │                       cards, tables, thumbs), feedback (log, toast,
    │                       statusline, dialogs), and the frame (shell)
    └── gallery.py        Review's gallery: the grid of cards, the list, the marks
```

`tools/kit_preview.py` and `tools/ui_snapshot.py` sit outside the app: a
window that draws the design system from the app's own code, and a picture of
the real window in a made-up state (see [Look and feel](#look-and-feel) and
[Snapshots](#snapshots)). `tools/tray_preview.py` draws the tray's icon, menu
and balloon copy the same way (`--grab icons|menu|notes <png>`).

**The tray process stays light.** It runs all day at below-normal CPU and low
disk priority, so `tracker_tray`, `tray_icon`, `tray_menu` and `tray_words`
import none of `app.ui`, `app.pages` or `app.main_window` (`run_app.py` imports
the window only when it is going to open one), and `theme.apply(app,
styled=False)` leaves the stylesheet out. The menu's four icons are embedded as
SVG strings because importing `app.ui.kit.icons` would load the whole kit;
`test_tray_icon.py` fails if one drifts from the kit's drawing, or if importing
the tray pulls the window in.

Copier's page owns CopySignals and a MeasureWorker. Creator's page owns a
`ReadWorker` for source metadata and `BuildSignals` for its callback engine;
all worker results reach widgets through queued Qt signals. `SourceModel` and
`ResultModel` use the kit's virtualized Table, including tag pills, checkboxes
and indeterminate per-item progress. Source metadata is cached on VideoItem,
so table painting never stats a video.

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
and `41%` under the name), `count(done, total)` (`4/201` and a mini bar, green once done),
`badge(n)` (warn), `status(text, tone, below=)` (`idle`; `2 problems` in warn
under the name), or nothing. In the rail each is an icon with a dot for the
bar, the badge and a warn or danger word.

`build_pages(window)` makes them all: one Rotator `Config` is
shared by the Rotator page and the Settings page, whose Rotator fields are
read-only while a Rotator job runs; each tells the other when it saved
(`SettingsPage.changed("rotator")` → `RotatorPage.config_changed()`,
`RotatorPage.config_edited` → `SettingsPage.show_rotator()`). The Tracker page
reads the same `Config` for where myprojects is. Its **Send to Copier** is
`TrackerPage.copier_requested(folders)` → `CopierPage.add_folders(folders)` →
`TrackerPage.copier_took(folders, added)`, wired in `build_pages`. `test_tracker_page.py` builds the window through `build_pages` and fails
if the Copier stops accepting folders from the Tracker.

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

**Tracker** (`app/pages/tracker.py`) shows the window's `TrackerFeed`: the
leading monitor (`pick_primary` with `tracker.primary`) in a detailed
`MonitorCard`, the others compact, the **Pace** panel, and the playlist in a
`Table`. Its words are plain functions — `monitor_view` (→ `MonitorView`),
`pace_figure`, `finish_sentence`, `provenance` (→ `(tone, sentence)` notes),
`playlist_rows` (a `Cycle` → `PlaylistRow`s and whether the queue is in
playing order), `row_matches`, `author_choices`, `header_subtitle`,
`nav_state`, `empty_text` — and `tests/test_tracker_page.py` calls them
directly. Nothing on the window's thread reads a file (the test patches the
file functions to raise there):

- `ListReader` does the reading on a thread of its own, a job at a time: a
  monitor's `Cycle` out of `data/tracker.json`, its rows at once, then
  `project.json` a chunk at a time and the authors, each chunk sent back
  through a queued signal. A newer list makes an older one stop.
- `engines/wallpaper_meta.py`: `read_meta(item)` (title, type, workshop id
  from `project.json`), `author_names(ids)` (Review's `steam_cache.sqlite`,
  answers of any age, no network), `known_authors(keys)` (`authors.sqlite`,
  opened read-only) and `MetaCache`, which keeps a session's answers. Measured
  here: a 196-wallpaper playlist 1.5 s cold, 1 756 in 14 s; 0.01 s and 0.1 s warm.
- A row's actions are a `ButtonsCell` in the last column (`action_cell`):
  Send to Copier, and Mark [protected] where `protect_state(folder,
  destination)` offers it — a folder directly in the Rotator's myprojects
  whose name is not `[protected] …` already (that one gets a lock mark).
  Paths only, compared with `os.path.normcase`. The rename is
  `protect_folder(folder)` on a thread of the page's own `_Offload` (the
  Rotator page's pattern), after `protect_dialog`; it raises `ProtectError`
  with the words for the danger toast. A renamed row gets `stale=True` and its
  new `folder`, remembered by old folder for the session (`_renamed`), since
  `tracker.json` keeps the old name until the next rotation. `_answer(dialog)`
  and `messages` are there for tests.
- `Countdowns` runs a `WallpaperTimer` of its own while the page is on screen,
  as a reader: `open_memory=None`, `save_path=None`, `restore_path` the tray's
  `wallpaper_timer.json`, `follow_files=False` (the feed's worker stats the
  files, and the timer reads the copy the feed keeps in `files`). It is made
  again each time the page is shown, so it starts from the tray's latest
  count; it also gives each monitor's resolution.
- A row's click opens Explorer through `reveal(item)` on a thread.

A new cycle and rebuilding from file times are asked of the feed
(`TrackerFeed.reset(monitor, done)` / `rebuild(done)`), whose worker owns the
`Tracker`; `done` runs on the window's thread once the look after it has
landed, with the number recovered or the exception raised, and the page says
what came of it. Its fixtures are
`tests/fixtures/ui/tracker.json` (`tracking`, `paused`, `disconnected`,
`finished`, `restarted`, `we-off`, `single-monitor`, `no-config`,
`no-playlist`); `Page.frame_fixture(state)` names the frame's state each goes
with, and its sidebar item and "Next in the loop" to match.

**Rotator** (`app/pages/rotator.py`) is the next run, a run under way, or how
one ended, on the left (`SideScroll`, 330 px), and a `Table` with three views
on the right — Reserve, Current, History — which gives way to the run's
`LogPanel` while it runs. `state` is `idle`, `running` or `done`. Its words are
plain functions, which `tests/test_rotator_page.py` calls directly:
`plan_rows` / `plan_captions` (the next run's steps from `Rotator.preview()`),
`selection_line`, `protected_line`, `estimate_line`, `confirmation` (→ the
start question: title, body, steps in the engine's order, notes),
`broken_groups` / `broken_reason` / `broken_title` / `broken_body` (the
checklist), `history_rows` (→ `HistoryRow`s; a run without a side-file entry
gets its result from its record), `history_summary`, `history_csv`,
`record_lines` (a run whose log was not kept), `done_steps`, `result_title`,
`result_sentence`, `playlist_stale`, `tracker_note`, `nav_state` /
`idle_nav`, the three subtitles, and `duplicates_confirmation`.
`RunTracker` is a run under way as the page draws it: fed each
`ProgressEvent`, it keeps the step, its count, what moved, returned, was set
aside and failed (per step), the folder last moved and the playlist's fate;
`finish(result)` ends it.

- **Nothing about the reserve is read on the window's thread.** `read_facts`
  (validate, preview, the playlists a rebuild would find, the estimate, the
  duplicates count) and the check's `check_roots` run through `_Offload`, a
  thread per piece handing its result to a callback; the tables are
  `LibraryModel`s fed by `LibraryIndexWorker` — `loaded` (the cache), `listed`
  (every name, so rows appear at once), `refreshed`, `authors`, `sized` —
  which runs while the page is on screen and stops when it leaves, or when a
  run starts. The visible rows' sizes are asked for once scrolling settles. The
  Rotator's own small files (`history.json`, `run_meta.json`, a run's log) are
  read on the window's thread when first needed, never in the constructor.
- **A `LibraryModel` row is a folder name.** Everything else is looked up
  when the row is painted — `FolderInfo`, the measured size, the author, the
  history's `Usage` — so 33 000 names build in about 15 ms and a scroll step
  paints in 2 ms at p95 (measured in the test, with every file call on the
  window's thread made to fail). Its chip column fits `New`, and widens for
  `Unidentified` only while a folder listed is one (`fit_chips`,
  `Table.refresh_columns`).
- **The flows** are the old tab's, one worker at a time and every question a
  `ConfirmDialog`: Start → `check_roots` → `ReserveScanWorker(step=0)` →
  `broken_dialog` → `CleanupWorker` → `read_facts` again → `start_dialog`
  ("Start run N?") → `RotationWorker(check_finished=, started=)`. Also
  `retry_failures` (`RetryWorker`), `rebuild_playlist`
  (`PlaylistRebuildWorker`), and `DuplicatesDialog` (`DuplicatesListWorker`,
  then `DuplicateActionWorker`). Each is a `begin("rotator", …, activity=…)`:
  check, cleanup, run, retry, rebuild, duplicates_delete, duplicates_return.
  Workers are kept in a module set until they finish (`_park`), so a page going
  never takes a running thread with it. What used to be a message box is
  `_say(tone, words)` — a toast, and a line in `page.messages` for tests; a
  run that ends while another page is shown says so in a toast with **Show**.
  `page._answer(dialog)` asks; tests put their own answer there.
- Fixtures: `tests/fixtures/ui/rotator.json` (`idle`, `running`,
  `done-clean`, `done-problems`, `current`, `confirm`, `broken`, `history`,
  `will-reset`) — an invented reserve of 33 421 names from a word list on `X:`,
  a history of a run a week, the next run's facts. `confirm` and `broken`
  leave their dialog open as `page.fixture_dialog`, which `ui_snapshot`
  draws over the window.

**Review** (`app/pages/review.py`) is one flow — scan, go through the authors,
finish — in five `state`s: `empty`, `scanning`, `stopped`, `reviewing`,
`done`. On the left the author panel (262 px): a placeholder before a scan,
`SkeletonRows` while one runs (still and set back once it stopped), then
`AuthorList` (a `RowList` of `author_row`s: the chip on the title's line, the
name the database still has, a tick once gone through) with filter, sort,
"3 / 12" and "106 authors had nothing new". On the right a stack: the empty
state (and the keyless `Callout`), the scan panel and "Found so far", the
stopped state (an `EmptyState` with a `ConsoleExcerpt` of the last log lines),
an author's gallery (`_GalleryPanel`: Grid / List, the cards or the table,
and the bar — "3 selected", **Subscribe selected**, **Subscribe page**, the
`Pagination`, **Done with <author> →**), and the finished state
(`MetricStrip`, **Open review as a list**, **Reopen review**). Its
words are plain functions — `empty_text`, the five subtitles, `ScanProgress`
(the scan's events → the panel's figure, bar, activity line, the status line's
words), `found_row`, `author_row`, `gallery_subtitle`, `list_foot`,
`stopped_text`, `finish_plan`, `written_blind`, `written_line`, `done_text`,
`nav_state` — which `tests/test_review_page.py` calls directly.

- **The scan is `engines/review_flow.ScanFlow`**, without Qt, on a thread:
  `prepare(step)` makes what it needs the first time (the authors database,
  the Steam client, the libraries — on the scan's thread), `Review.scan`
  finds the authors (`on_progress` for its stages), then `Review.fill` counts
  them through the client's pool, each said as it starts and ends
  (`FlowEvent`). A `SteamUnreachable` or `SteamAuthError` (`STOPS`) stops it
  at the author it was on — `Review.fill(stop_on=)` lets those through and
  leaves the card unfilled — and `resume()` counts the unfilled ones; any
  other failure is that author's `card.error`. At the end, `owned_before` is
  parsed once and every author with something new is marked. A
  `ScanOutcome` says how it ended (`done`, `stopped` with a `reason` in plain
  words — `plain_reason` — or `cancelled`) and where (`stopped_at`). The page
  starts it as `begin("review", …, activity="scan")`, a carry-on as
  `activity="count"`, and the finish as `activity="database"`.
- **The session** is `review_flow.Session`: the authors with new items in the
  scan's order (`SessionAuthor`: new, were yours, done, subscribed), counted
  and nothing new, when finished and what was written. `to_json()` is
  `data/review_last.json`, saved (`save_last`, whole or not at all) when a
  scan finishes, an author is done and at the finish; the snapshot's
  `ReviewState` reads it back for Overview, the page's own empty state and
  badge. Galleries are not kept between starts.
- **Finish review** plans with `Review.plan`, asks with a `ConfirmDialog`
  listing the changes (`lines=`), with the keyless warning as its `note` and
  Cancel the default (`safe_default=`) when a visit date would move from a
  list read without a key, then writes on a thread and absorbs the changes.
- **Settings and the database** are `app/pages/review_settings.py`:
  `ReviewSettingsDialog` (a `FormDialog`: the source from `we_folders()` read
  on a thread, Subscribe by, the key with Show and Test, the second backup
  folder, "Authors database…"), `save_settings` (the settings file, and the
  key through `secrets`), and `AuthorsDialog` (an `OverlayDialog` with a
  `Table` of backups; it reads on a thread, and restores after a destructive
  confirmation). The Settings page's **Review settings…** and **Authors
  database…** open the same dialogs.
- Nothing about Steam, the libraries or Wallpaper Engine's folders is read on
  the window's thread: the scan, the subscriptions (`SubscribeQueue`, one
  thread and one Steamworks connection), the look for subscriptions made
  elsewhere (every 4 s while an author is open), the folder counts and the
  key's test all run on threads. The constructor reads nothing (the test
  patches the file functions to raise there).
- Fixtures: `tests/fixtures/ui/review.json` (`empty`, `scanning`, `error`,
  `reviewing`, `gallery-grid`, `gallery-list`, `done`, `review-list`,
  `settings`, `authors`) — twelve invented authors and a week of counts;
  `app/pages/review_fixtures.py` builds the cards, the result and the session
  as a scan would have left them. The gallery states draw their previews
  (`preview_image`: gradients and soft shapes, nothing downloaded) and put one
  card of each mark, one under the pointer, one being subscribed to and three
  selected, as frames 12 and 13 show them.

**The gallery** (`app/ui/gallery.py`) is one page of an author's wallpapers,
shared by two views:

- `GalleryModel` holds the page on screen, the previews that arrived (a still
  no larger than 640 px, at most `GALLERY_IMAGES` in memory), which cards are
  being subscribed to (`busy`: `WAITING`, `SUBSCRIBING`), and the selection
  (ids in the order chosen, on any page, with an `anchor` for Shift).
- `GalleryView` is the grid: a `QListView` in icon mode whose card size follows
  its width (`card_geometry`, `columns_for`: cards of about 230 px, three to
  five across, the scroll bar always allowed for), paged thirty at a time
  (`show_items(items, paged=False)` for the review as a list). It owns the
  players (`MAX_PLAYERS`, the 50 ms clock, resting when late) and the clicks:
  a click subscribes (`subscribe_requested`), Ctrl or the card's check toggle
  (`click`, `toggle`), Shift selects a range, Ctrl+A the page, Esc clears.
- `GalleryDelegate` paints a card from pixmaps made once: `tile` (the preview
  cropped to 16:9 by `cover_source`, corners, the mark's edge, chip, plate and
  check), `overlay` (the same over a playing frame), `words`, `shade` (hover,
  waiting, subscribing). It skips cards outside the repaint's region
  (`view.dirty`), because the view repaints the region's bounding box.
- `GalleryList` is the kit `Table` over the same model
  (`GalleryListModel`: thumb, WALLPAPER with its line under it, TYPE, SIZE,
  MARK, a `ButtonCell` Subscribe / `BusyCell` / dash / `DiscCell`), with the
  previews cropped from the gallery's own, and `set_groups` for every author
  under a header.
- `card_mark(wallpaper)` → `Mark(chip, text, edge, dimmed, offer)`, the one
  place that decides what a card says; `card_line`, `card_meta`, `offered`.
  The marks come from `Review.mark_owned`: `in_library` and `library_place`
  from `Library.places()` (each root as the Rotator names it: `ROTATION`,
  `RESERVE`, `DUPLICATES`), `once_had` only for ids Wallpaper Engine's folders
  remember with no copy kept.
- `tests/perf_gallery.py` (not a test: run it by hand) measures a page of
  thirty made-up animated previews with threads busy in Python, before and
  after a change, and with `--items 1200` a whole review's gallery turned page
  by page; [Gallery → A card is a few copies](gallery.md#a-card-is-a-few-copies)
  has the numbers. `tests/perf_pages.py` does the same for the other long
  lists — the reserve's 33 000 folders, a 1 400-row playlist, 450 authors and
  a log at its cap while a job streams — see [Performance](#performance).

**Window requests** (`window_instance`): a second launch or the tray sends one
line — `show <page>`, which every version understands, or the command form
`<verb>:<argument>` (`show:rotator`; `rotate:confirm` is the tray's "Rotate
now…": the Rotator, and its start question). A command follows a `show <page>`
line in one write, so a window from before the command existed still comes
forward on the right page; a window that is not running yet is started with
`--command <verb:argument>` and carries it out once it is up.
`WindowInstance.command_received(verb, argument)` hands each to
`MainWindow.handle_command`; a verb this version does not know still brings
the window forward. `--tab <page>` takes the old tab names and the page keys,
in any case; anything else opens Overview.

## Services

`app/services/` is what the status line, the sidebar and Overview read, and
what every page reports its work to. The main window makes one `Services`
(with its own `TrackerFeed`, which it also hands the Tracker page) and
installs it; `services.current()` is that one, or None for a page built on its
own, as the tests build them.

**A page's work** goes through `begin(tool, title, page=None, activity=None,
run_id=None)`, which returns a `Run`: `update(phase_text, done, total,
count_text)`, `log(kind, message)`, `log_text("[WARN]  …")` for the callback
engines' own lines, `note(kind, title, detail, chip, run)` for a journal entry
on the way, and `finish(result, summary, title=, detail=, chip=, run=,
journal=True)` / `fail(message)`. `finish` ends the job, writes the last log
line, and journals `<activity>.<result>`. With no services installed every
call does nothing, so pages can be built independently in tests.

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
with the result and times from `run_meta.json` beside `history.json` when it
has the run, `from_side_file`), `PLAYLIST`
(`PlaylistProgress` of the leading monitor, from the feed; `from_engine` says
whether "not live" means Wallpaper Engine is not running) and `REVIEW` (the
dict in `review_last.json`, or None; `ReviewState.from_json` reads it, and
its docstring is the file's shape — `scanned`, `scope`, `since`, `items`,
`authors` as `[{name, new, done}]`, `checked`, `finished` — which the Review
page writes, with more it reads back itself).
`parse_estimate(text, now)` turns the tracker's "21 Sep 09:10" into a moment. `refresh(keys=None)` returns at once:
the playlist is read from the feed in memory, everything else on one worker
thread, one refresh at a time (asking during one queues the keys).
`refreshed(keys)` follows. It refreshes itself when a rotation, a copy, a
build or a Review job finishes, and when the feed looks. Nothing it reads
writes: it reads `config.json` without `Config.load()`'s default save, and
the history through `read_runs()`, never `History.load()`'s repairs. On the
real reserve (33 619 folders on the W: disk) the worker took 1.0 s cold and
0.26 s warm.

## The Rotator engine

Everything the Rotator page draws comes from the engine without the engine
knowing about the page (REDESIGN_PLAN §6.2).

**Steps.** `core.STEPS` is the order a run really goes in: `check` (the
reserve check, its own worker, with a confirmation before the rest),
`return` (duplicates are set aside here), `move` (draw, then move in),
`playlist` (only with `refresh_playlist`). `run_steps(check=, playlist=)` is
one run's plan. Every `ProgressEvent` a run hands on carries `step`, the index
of its step in that plan, and `current` / `total` within it, and `kind`, its
log line's kind; a step's first and last lines are `step N`. Closing Wallpaper
Engine is the start of `return`; starting it again the end of `playlist`,
three stages counted 0–3.

**Runs** (`runner.py`, and the same on a thread in `worker.py`):

- `RotationRun(config, history, run_id=, progress=, check_finished=,
  started=)` / `RotationWorker` — `run_id` exists before it starts, so the
  page opens `logs/rotator/run-<id>.log` with `begin(..., run_id=)` first.
  `stop_after_step()` finishes the step under way and stops: after `return`,
  nothing is drawn and the playlist is left as it was
  (`PlaylistRefresh.finish(completed=False)`); after `move`, the batch is in
  and the playlist is not rebuilt. `cancel()` stops before the next folder.
  `result` is `clean` / `problems` / `stopped` / `failed`; `meta` the side-file
  entry. A run is in the history once it moved anything, however it ended.
- `RetryRun(config, history, record)` / `RetryWorker` — `record.failed` again,
  each in the step the side file says it failed in (a run from before it is
  left alone), then the playlist rebuilt when anything moved. The record and
  its entry are updated in place; the entry gains a `retries` item.
- `PlaylistRebuild(destination, runs=)` / `PlaylistRebuildWorker` — "Rebuild
  playlist now": the playlist step on its own. `runs` lets it find a playlist
  still made of an earlier batch, which a failed rebuild leaves.
- `DuplicatesListWorker(config)` — `core.list_duplicates()`, with sizes.

**`run_meta.json`** (`meta.py`), beside `history.json`, keyed by run id:
`started`, `finished`, `seconds`, `result`, `batch`, `protected`,
`returned_failed`, `moved_failed`, `playlist` (the summary lines),
`playlist_rebuilt`, `playlist_problem`, `history_reset`, `stopped_after`,
`log`, `step_times` (when each step ended) and `retries`. `RunRecord` keeps
its shape for good: 2.1.1 reads `RunRecord(**r)`, and one unknown key made it
save an empty history over the real one (`test_rotator_engine.py` reads every
history the tests write with 2.1.1's loader). `read_meta()` for readers,
`record_meta(id, meta)` to write one entry, `estimate_seconds(count)` — the
median time of the newest five completed runs whose batch was within half of
`count`, or None.

**`History.usage()`** → `Usage`: `last_used(name)` (when a run last moved it
in) and `never_used(name)` (not since the history last started over — what
the next run draws from), both O(1) for 33 000 rows.

**Library index v2** (`library_index.py`, `data/library_meta.json`). Per
folder, keyed by name and mtime: `FolderInfo(title, kind, workshop_id,
preview, size, unidentified)`. `LibraryIndex.refresh(roots)` reads only new or
changed folders, carries a folder moved between the roots across without
reading it, saves as it goes (a stopped walk resumes) and marks a root
`complete` at the end; `measure(root, names)` adds sizes; `authors()` names
workshop ids from Review's Steam cache, never Steam. `LibraryIndexWorker`
does it all on a thread: `loaded` (what the file knew, at once), `listed`
(every folder's name, as soon as the root is listed), `progress`,
`refreshed`, `walked`, `authors`, then `sized` as it measures —
`request_sizes(root, names)` first (the visible rows), then with
`fill_sizes` the rest. 33 000 entries: 3.8 MB, saved in 40 ms, loaded in
90 ms, on the worker. `library.json` (Review's) is a separate file and keeps
its shape.

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
| `test_creator.py` | Move preserves sources when JSON, copy or verification fails; metadata parsing, a single probe per file, read cancellation, pause/resume, subset builds, report reasons and the three tag states |
| `test_creator_page.py` | skip/selection counts, tag cells, keyboard selection, playlist visibility, fixtures without drive I/O, a threaded read and subset build through JobCenter and the journal, notification and retry |
| `test_tracker.py` | anchoring a cycle, rebuilding history, merging two writers, following the engine's deck, when to look |
| `test_wallpaper_timer.py` | the PLPV0005 parser, the file watcher, the countdown, pause rules |
| `test_app_identity.py` | the balloons' sender: the shortcut made only by the built exe, again when it points elsewhere or carries another ID, the ID taken only once it is there; on Windows a real shortcut made and read back |
| `test_row_tiles.py` | rows as tiles: copied while nothing changes, drawn again on a changed cell, hover, selection or a table switched off, never for a spinning row, and pixel for pixel the row drawn directly; a list's rows in one pass through `list_item` or its roles; a panel's glass once per size and tone; a caster's shadow skipped only inside its box clear of the corners |
| `test_steam_paths.py` | Steam's folders found on a thread of their own: nothing known and nothing waited for before, the answer and `when_found` after; the Rotator's first-run settings saved only once it is in, an empty, chosen or missing destination each kept or filled as it should be; no Steam at all |
| `test_startup.py` | a plain start of the window and of the tray with `gui_guard` on before `app.settings` is imported and Steam made up on a guarded folder that answers late: no GUI-thread call on its disk, the fields empty meanwhile, then filled in (a chosen one left alone), the Rotator's defaults saved then, the Tracker's config.json found |
| `test_tracker_feed.py` | a real Tracker on a made-up config.json and state file, every look on the feed's worker and no file call on the window's thread (`gui_guard.watch`); the window's copy of the files and a countdown reading it; a new cycle and a rebuild answered after their look; another config.json under a look; looks asked for while one runs; a failing look said once; the worker stopping with its owner |
| `test_playlist_refresh.py` | finding the rotation's playlist, refilling it, restarting one monitor's pass, the state file written back byte for byte |
| `test_rotator_cleanup.py` | the reserve check and what it offers to delete, ticked by default; the duplicates listed with sizes, junctions not followed; the Rotator page with its folders unset listing, checking, rotating and deleting nothing; a rotation from the page logging to its own run file and side-file entry |
| `test_rotator_page.py` | the next run's steps and captions from `preview()`; the start question for a normal run, `[protected]` folders, duplicates and a reset; the broken-folders groups and their defaults; the history's rows (old runs without a side file), summary and CSV; a finished run's steps and sentences; the event → state machine; a run end to end on folders made here (check, clean-up, question, rotation, journal, log), stop after this step, retry, rebuild (Wallpaper Engine stood in for), a run's log read back, CSV export; 33 000 rows built, sorted and scrolled with every file call on the window's thread failing; every fixture |
| `test_rotator_engine.py` | the steps and their order in a run, with and without the playlist, stopped after the return (folders and Wallpaper Engine's files read back) and after the move; retrying failures by step, one that still fails, one put right by hand, one from before the side file; the side file's round trip and tolerance; estimates; Rebuild playlist now; every history read back by 2.1.1's loader; a history, config or side file that does not read never saved over; library index v2 incremental, resumed, carried across, tolerant, measured, named, on its worker |
| `test_rotator_history.py` | the history's snapshots, and a missing or unreadable one put back, and the Rotator page saying so |
| `test_autostart.py` | the command line, the task XML, and the rename migration |
| `test_theme.py` | every token parses, text stays legible on glass, fonts, shadows, the stylesheet fills in and ticks its check boxes |
| `test_icons.py` | every icon draws, in the colour and at the size asked; unknown names raise |
| `test_kit_controls.py` | every kit control in every state; the fourteen chips, the Pagination rule, the Toggle with motion off, Dropdown rows that cannot be chosen, a DangerButton that never takes Enter, the ring for the keyboard only |
| `test_kit_feedback.py` | the log's 5 000-line ring, newest first, and its Problems filter; the console following the newest line until you scroll down; the panel closing over `motion.slow`, the newest line in its header; toasts stacking, going after 6 s and danger staying; the status line's five states and a count that never elides; a destructive dialog defaulting to Cancel, its group boxes, summary and Danger text following the ticks, Esc cancelling; a form's Save waiting for valid fields |
| `test_keyboard.py` | every page in every made-up state: Tab down the sidebar, the header, the page in reading order and the status line, never stuck in a table; Ctrl+number and Ctrl+F; focus rings for the keyboard only; a name on every IconButton, chip, nav item and list; no text under 10 px or dimmer than `text.lo`; spinners at rest with motion off; nothing on the wallpaper disk from the GUI thread (`gui_guard.py`, on from the first import) |
| `test_kit_data.py` | the formats; a PathField checked on a worker; a per-clip TagSelect's three states; MonitorView to card; TableModel groups, sorting and zebra; 33 000 rows built under 100 ms and only visible rows painted; a ButtonsCell's slots, clicks, marks, tool tips and double-click; local previews cached by path and time; no file-system call on the GUI thread |
| `test_animations.py` | motion, by sampling real widgets over real time; the curve, the loops, reduced motion |
| `test_steam_api.py` | the Web API client and its cache |
| `test_authors_store.py` | the authors database: transactions, snapshots, pruning, the second folder, restoring, damaged files |
| `test_review.py` | the weekly walk; the scan as one flow — the authors said in order, a stop on Steam at the author it was on, carrying on from there asking nobody twice, a cancel, a database or a key that stops it first; the session and `review_last.json`, read back as Overview reads it, tolerant of torn files and unknown fields |
| `test_review_page.py` | every state's words (the subtitles, the empty and stopped states, the scan's figure, the rows, the plan and its keyless default, the finished state, the sidebar); the page end to end on a Steam of dictionaries: a scan, Done with, subscribing a page and noticing subscriptions made elsewhere, selecting and Subscribe selected, Grid / List remembered, Skip for now, Finish review writing the database, a keyless review warned about, Steam stopping and carrying on, a cancel, a restore during a scan; the review as a list and back; the author list's keys; Review settings and the authors dialog's restore; no file read in the constructor |
| `test_gallery.py` | the marks, their chips and edges; the column count by width and the 16:9 crop; every card painted in every state; the clicks (subscribe, Ctrl, Shift, the check, Esc) and the selection across pages; the spinner repainting only itself; the list view's cells, its button and its selection; memory and animation bounds; one Steamworks queue for every subscription |
| `test_hang_watch.py` | a stuck GUI thread leaves its stacks in the hang log |
| `test_tray_icon.py` | the tray's ring: each state at each size, the arc by sampling pixels along the circle, the number from 24 px and the dot below, the light taskbar's colours and hearing Windows change them; the icon's state from the tracker's reading; the menu's words, tooltip and the two balloons' copy with a number known and not; once per cycle; the menu's rows, what each opens, and a click on a balloon; the readers leaving a damaged file alone; the tray importing no window code |
| `test_window_instance.py` | one window, raised from the tray, in a process of its own; the plain request and the command form on the socket |
| `test_shell.py` | pages in the loop's order, the sidebar and Ctrl+number; `--tab` names; the window command parser; the rail below 1 200 px; the status line bound to the JobCenter (Show, problems until seen); the cross-fade, and none with motion off; the Settings page writing `config.json` and `suite.json`, read-only while the Rotator works; the window and the tray reading each other's `suite.json`; the title bar's hit testing; `ui_snapshot` at a size and scale |
| `test_tracker_page.py` | the tracker's Progress as a monitor card in every state (paused, Wallpaper Engine stopped, restarted, finished); the countdown's words (`≈`, paused, any moment); the pace and the finish sentence; the notes the count rests on; the playlist's groups, order, `~` times, filter and authors; titles and authors read from project.json, the Steam cache and the authors database (never written); the countdown as a reader of the tray's file; every fixture; no file read on the GUI thread; which rows offer Send to Copier and Mark [protected], the question's words, the rename on a worker against folders made here, a failed rename (name taken, gone, in use, denied) changing nothing, the row after it; Send to Copier through `build_pages` into the Copier's list with the default count, once, and the toast's Show |
| `test_services.py` | which running job leads; rate and time left only once measured; the journal's append, tail and rotation past a damaged line and an unknown field; log files by tool, day and run, their tail and the 30-day sweep; the snapshot read on a worker, never the GUI thread, keeping its age and its last value; each page's work reaching all three |
| `test_external.py` | a child cannot load a DLL from the bundle, through the DLL directory or PATH; a quoted URL survives cmd.exe; nothing in `app/` starts a program another way |

Most need **PySide6** (they build real widgets); none need Wallpaper Engine,
windows on screen, or a network.

### Performance

`tests/perf_gallery.py` and `tests/perf_pages.py` are measurements, not tests:
run by hand before and after a change to a long list, with their numbers in
the pull request (`--root <checkout>` measures the code before it). Each
spins Python threads beside the list with the switch interval the app sets
(0.5 ms), because PySide gives the GIL up on every Qt call: under contention a
paint costs about as many waits as it makes Qt calls. Measured for 3.12.0,
offscreen at 1 280 × 860, two busy threads:

| List | Build | Scroll step (repaint) p50 / p95 |
|---|---|---|
| Reserve, 33 421 folders | 13 ms; filter 24 ms, sort 17 ms | 7.6 / 12.5 ms |
| Tracker playlist, 1 400 rows | under 1 ms | 72 / 84 ms (2.9 uncontended) |
| Review's authors, 450 | 1.4 ms | 74 / 80 ms (2.1 uncontended) |
| Gallery, 1 200 wallpapers in 40 pages | 24 ms; a page turned in 2.3 ms | 12.9 / 15.7 ms a frame, eight previews playing |
| Log at its 5 000-line cap, 200 lines a second | 10 ms to fill | 9.9 / 13.1 ms a repaint; 1.7 ms a line |

**Since 3.13.0 rows are tiles.** The table's rows and the lists' rows are
drawn once into a pixmap (`RowTiles`, keyed by everything that decides a row's
look: its cells, the columns and where they sit, its ground, hover and button
looks, its thumb, the pixel ratio) and copied after; a row with a spinner is
drawn each time. The Table and `RowList` paint their visible rows in one pass,
asking Qt once per paint for what every row needs, so a cached row is one call.
A scroll step also repaints what is behind the list: the window's gradient and
a GlassPanel's glass are now pixmaps drawn once per size, and the kit skips a
caster's shadow when what repaints lies inside its box, clear of its corners.
Measured on a different machine from the table above (its own `main`,
offscreen, two busy threads), scroll steps p50 / p95:

| List | 3.12.2 | 3.13.0 |
|---|---|---|
| Tracker playlist, 1 400 rows | 40 / 53 ms | 10–12 / 15–17 ms |
| Review's authors, 450 | 47 / 68 ms | 7–9 / 13–15 ms |
| Reserve, 33 421 folders | 26 / 36 ms | 12 / 17 ms |
| Log, console repaints | 7.3 / 14 ms | 6.5 / 13.4 ms |

Copied tiles are the rows drawn directly, pixel for pixel, onto the same
ground; on the window, text edges in rows differ slightly from text drawn
straight onto the glass, as in the gallery and the console.

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

The window is built to a design made in Claude Design: dark "frosted glass",
translucent panels over a radial gradient. Everything visual comes from
`app/theme.py` and `app/animations.py`; nothing else names a colour, a size or
a duration.

### Working from the design

The design is a specification, not code: HTML pages drawn by the designer,
rebuilt here in Qt. It was handed over as those pages rendered 1:1 to pictures
(twenty-five frames of the window, the design system's twelve sections, the
tray, the motion specimens, the branding), with condensed outlines of each and
an implementation plan. That handoff stays on the maintainer's machine: its
pictures carry the designer's sample names, which never go into this public
repository.

What the repository keeps is how to compare against it:

- `tools/ui_snapshot.py` draws any page in any made-up state at any size —
  [Snapshots](#snapshots);
- `tools/kit_preview.py --grab <section>` draws the kit section by section, as
  the design system lays it out — [The kit preview](#the-kit-preview);
- `tools/tray_preview.py` draws the tray's icon, menu and balloons.

A change to a page is drawn with the first and set beside the design's frame
at 1 280 × 860; then at 1 040 × 720 (the rail), 2 560 × 1 440 and 150 %.
Pixels are not the aim (fonts rasterise differently), structure, spacing,
colour and states are. Two things to know when measuring:

- **The design's CSS sizes boxes by their content.** Nothing in it sets
  `box-sizing`, so a box's border and padding are outside the width or height
  it names: the sidebar's `width: 246px` with 10 px sides and a 1 px edge is
  267 px on screen, the header's `height: 52px` and its divider 53. The theme's
  metrics say which they hold (`SIDEBAR_ITEMS` and `SIDEBAR_WIDTH`).
- **Qt rounds a font to whole pixels.** The design's 11.5 and 12.5 px text
  renders at 12 and 13 (Qt rounds the pixel size of a point size it is given),
  so a line of it is about 4 % wider than the design's, and a sentence the
  design fits on one line can take two. Layouts leave room for it; words that
  must stay together (`≈21 Sep`) are joined with no-break spaces.

Where the app differs from the design on purpose — the engine's real step
order, a number it cannot measure left out, a choice the maintainer made — the
pull request that built the page says so and why.

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
boxes, lists and headers, scroll bars, menus, tool tips —
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
reads Windows' animation switch. All pages now use kit components; the
hand-styled helpers and legacy tab adapters have been removed.

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
- **Pages cross-fade** through CrossFade, a cached image of the outgoing
  page over the live incoming page. It disappears when the fade ends.
- **A count that changed by itself flashes** (`flash`), and turns green rather
  than blue when the playlist is finished.
- **Loops share one clock per kind.** `loop("spin")`, `"pulse"`, `"shimmer"`
  and `"indeterminate"` are drivers a widget `subscribe()`s to and reads
  `value()` from in its paint: a spinner's angle, a live dot's opacity, a
  skeleton row's opacity (with a `delay` for staggering), the sweep's travel.
  A driver's timer runs only while a subscriber is visible — a hidden page or
  a minimised window stops it. A driver repaints a subscriber whole; an item
  view whose one card or row turns a spinner subscribes a `LoopTicker`
  instead, whose tick repaints just that spinner (the gallery, and a `Table`'s
  `set_spinning(items)`).

What does not move: the frame, table rows and gallery cards (no enter
animations), dialogs, and numbers (they step, they do not roll).

**Reduced motion.** When Windows' "Animation effects" are off
(`SystemParametersInfoW(SPI_GETCLIENTAREAANIMATION)`), `theme.apply()` sets
`animations.ENABLED = False`: every transition becomes an instant change and
the loops stand on their resting frame.

### The kit

`app/ui/kit/` holds the components every page is built from. Each class is
named as in the design, and each has all of the design's states.

| Module | Classes |
|---|---|
| `buttons.py` | `AccentButton` (the one action that starts the work), `SecondaryButton`, `DangerButton` (never a dialog's default, so Enter cannot delete), `GhostButton` (`outlined=True` for page headers), `IconButton` (30 px, or 22 px as `size="sm"`; its tool tip is required), `LinkButton` (an action written as a link, for a toast, the status line or a list; never a dialog's default). Text buttons take a leading `icon=`, a trailing `key="Ctrl+V"` cap, and `size="sm"`/`"md"`/`"lg"`. `button_pixmap(text, variant, state)` and `button_size` draw a button's look for a delegate; `icon_button_pixmap(icon, state, size, ink=)` an IconButton's. |
| `inputs.py` | `TextInput` (`search=True`, `set_error(message)`), `SpinBox` (mono, `1 000` with a no-break space), `Dropdown` (`add_item(text, data, count=)`, `add_section`, `add_separator`, `prefix="SORT"`) and its list, `DropdownPopup` |
| `selection.py` | `Checkbox`, `Toggle` (`knob_position`), `SegmentedControl` (two or three segments, `changed`), `Pagination` (`page_changed`), `page_numbers(pages, current)` |
| `chips.py` | `Chip(variant, text=None)` in exactly fourteen variants; `chip_pixmap` and `chip_size` for delegates |
| `panels.py` | `GlassPanel` (`tone=`, `padding=`), `Overline`, `Rule` (a hairline between two parts of a panel), `IconDisc` (a glyph in a tinted circle before a panel's title: a clean run's tick), `CardTitle`, `Callout` (`tone=`, `title=`, `add_action`), `MetricStrip`, `EmptyState` (`width=`, `add_content` for a console excerpt or numbers between the words and the actions), `StepList`, `ActivityLine`, `Spinner` |
| `base.py` | the state model and the surfaces, below; `label(text, type, tone)`, `Glyph`, `Elided` (one line cut with an ellipsis, whole in its tool tip) and `LiveDot` (the pulse of a running job); `tab_stops(root)` and `chain_tabs(widgets)` for the Tab order (see [Keyboard](#keyboard-and-accessibility)) |
| `format.py` | how every number is written: `count` (`33 421`), `size` (`1.1 GB`), `duration` (`4 min 12 s`), `left` (`≈6 min left`), `approx` / `reconstructed` (`≈`, `~`), `clock` (`13:47`, or `13:47:02` for a log line), `date_table`, `date_activity`, `date_long`, `day`, `ratio` (`4 / 201`, `4/201`, `412 of 1 000`), `percent`. Pages never format numbers themselves. |
| `paths.py` | `PathField`: empty (type, paste or Browse…), compact (path elided from the left, ✓, a folder button), invalid ("folder not found"), disabled; a drop target. `path_changed` for the user's choice, `validity_changed` when a worker has checked the folder. `kind="file"` (with `file_filter=`) holds one file instead: Wallpaper Engine's `config.json`. |
| `tags.py` | `TagSelect` (pills, `3 / 25`, a popup of the Creator's `WE_TAGS` in four columns); `per_file=True` adds the clip's three states — `value()` None follows the batch, a list is its own, `[]` is none. `TagPopup` is the open state. |
| `progress.py` | `ProgressBar` (3–8 px; determinate, eased over `motion.slow`; indeterminate; error; success; optional caption row) and `ProgressRing` (58, 52, 34 px; percentage, spin, done) |
| `cards.py` | `StatCard` (default, hover when `clickable` — an empty one too, `set_loading`, empty, `set_empty(why, link=True)` for a reason written as a link, tone `lo` for the last known), `MonitorCard` (compact or `detail=True`; a playlist shown to its end closes the ring in ok, turns the count green with a flash and gives the card an ok edge) and `MonitorView`, the plain values a page fills it from (`remaining_approx` for `≈`; `timer` "paused" or "stopped" says so in REMAINING without changing the badge), `ToolTile` (a tool of the loop on the Overview: status line and tone, thin bar, mono meta; `set_active` accents the one whose job runs, with a pulsing dot; `clicked` on a click, Enter or Space) |
| `tables.py` | `Table`, `TableModel`, `Column`, `Cell` (`sub=` a second, quieter line), `ChipCell`, `ButtonCell` (a button drawn in the row, Accent on the row under the pointer; `Table.button_clicked(row, column)`), `ButtonsCell` of `CellButton`s (glyph buttons side by side, as small IconButtons: quiet in text.lo until the row is hovered, an empty slot keeps the rest in line, `mark=True` a glyph that says something and takes no click, `enabled=False` faded; each shows its `tip`; `Table.action_clicked(row, column, key)`; `buttons_width(n)` for the column), `BusyCell` (a ring and its words; `Table.set_spinning(items)` turns it), `DiscCell` (a glyph on a disc), `Group`, `RowDelegate`, `TableHeader`, `TableBar`, `TableSummary`, `TableFooter`; `disc_pixmap`, `paint_spinner` (the kit `Spinner`'s ring, for delegates); `ListRow` (`title_note`, `tick`, `dimmed`, `meta_tone`, `thumb_size`, `chips_inline`), `paint_list_row`, `RowList`; `SkeletonRows` (placeholder rows, shimmering or still); `Thumb` and `paint_thumb` |
| `thumbs.py` | `ThumbLoader`: Steam previews for the gallery (`request`), and a wallpaper folder's own preview (`request_local`), read on a worker and kept in `data/thumbs/local/` |
| `log.py` | `LogPanel` (a job's log as a card, newest line first: `append(time, kind, message)`, All / Problems, copy, live dot, "writing to rotator.log" — or a file only read back, `set_file(name, writing=False)` — closes to its header, which then shows the newest line and a problem count; `fill=True` takes its layout's height and keeps the console open while collapsed, the Overview's glance), `LogModel` (the last 5 000 lines in a ring, its rows newest first; `lines()` oldest first), `ProblemsFilter`, `LogView`, `ConsoleExcerpt` (a few lines quoted in a console well) |
| `toast.py` | `Toast` (ok, info, warn, danger: a neutral card with its hue down the left edge; an action link; close) and `ToastHost` (stacks a page's toasts bottom-right, at most four) |
| `statusline.py` | `StatusLine`: `set_running(text, done, total, count_text, on_show)`, `set_idle(text)`, `set_ok(text)`, `set_warn(text, action, callback)`, `set_error(...)`; `link()` |
| `dialogs.py` | `ConfirmDialog` (neutral or destructive; numbered `steps` in a well; a checklist of `CheckGroup`s of `CheckRow`s, each group a tinted band with an optional `note`; `lines` of a plan, the first 14 and "… and N more"; a `note` Callout; `safe_default` for Cancel first; a summary; returns a `ConfirmResult`), `FormDialog` (labelled rows, Save once valid; `add_widget`, `add_action`), and their chrome, `OverlayDialog` |
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
it sorts by (`sort_key`), where a row's preview is (`thumb_source`),
whether it is dimmed (`row_dimmed`) and whether it is marked in a hue
(`row_tone`: its soft ground and an edge down the left, as a selected row is
in accent — the Rotator's history marks the run just finished). A `Cell`'s
`icon` draws a glyph before its words in its tone, or in `icon_tone` (the
lock on a `[protected]` folder; the warning before a file the Creator left
out). `Table.refresh_columns()` lays the header out again
when a model's column widths change. `set_rows(items, groups=[Group(...)],
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

A stretched column with a thumb steps down to the next thumb (`row`'s 120 × 68
to `md`'s 64 × 36) while it would leave its words less than
`TABLE_WORDS_MIN` (80 px, about a dozen characters): at 1 200 px the Tracker's
playlist had 114 px for a 120 px picture and no title at all. The room is
counted as if the scroll bar were there, so the shorter rows taking the bar
away cannot make the rows tall again. Pictures are still loaded at the
column's own size. Tab leaves a table (`setTabKeyNavigation(False)`); the
arrows move inside it.

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
`LogModel` keeps the last 5 000 lines in a ring, and its rows are newest first,
as the design's console reads: a new line comes in at row 0 and, past the cap,
the oldest leaves the last row; `line(i)` and `lines()` still read them as they
were written. Kinds take the console's colours: moved, returned, deleted and
done ok, skip, dupe and stop warn, fail and error err, step, start and info
mid; any other kind (`step 2`) is written as it is, in mid. The console keeps
the newest line in view while it is at the top and stops the moment you scroll
down, keeping the lines you read where they are as new ones arrive above;
back at the top it follows again. Its grid is the design's (time 62 px, kind
52, then the message), wider only if a font needs it. `copy()` (the button, or
Ctrl+C) takes the selected lines, or every line shown, oldest first as the
file has them. `set_expanded(False)` closes it to its header over
`motion.slow`, chevron (› closed, ∨ open) and height together; the closed
header shows the newest line and a badge counting the problems (danger once an
error is among them). "Open log folder" is the `on_open_folder` callback.

A line never changes, so the console draws each once into a tile and copies it
after, and fetches a whole line in one `data()` call: a row costs its ground
and one copy. With a job streaming 200 lines a second and two threads busy in
Python (`tests/perf_pages.py`), the console's repaint went from 27 ms to 10, and
a line from 3.0 ms to 1.7 (the first newest-first version sorted in a proxy,
whose Python `lessThan` ran about thirteen times a line).

**Toasts.** `ToastHost(content)` covers the widget whose bottom-right corner
the toasts stack in — the window's content, not a page that scrolls — and
lets every click through; `show_toast(text, variant, action=, on_action=,
timeout=)` adds one at the bottom of the stack. A toast goes after 6 s, the
pointer resting on it holds it, and danger stays until closed. Four at most:
a fifth makes the oldest that may go leave. In the app only; a hidden window
leaves it to the tray.

**The status line.** `StatusLine` has a plain API; the window binds it to the
JobCenter (`StatusBinding`). Five states, each led by a dot in its hue: running
(the dot pulses; a bar, a count, **Show**), idle, ok (a job that just ended
well: "Run 39 finished cleanly · …"), warn and error. The binding says how the
last job ended until its page has been looked at since, then goes back to
"Nothing running · last run finished 13:58". The bar, count and link follow its
words; the words elide and the count never does.

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
  the numbered list of what will happen, in a well.
- `groups=[CheckGroup(title, rows, tone=, initially_checked=, noun=, plural=,
  limit=3, note=)]`: a checklist. Each group's header is a band tinted by its
  tone, with `note` at its right ("no media inside"); its tri-state box ticks
  or clears the whole group, rows not yet shown included; its title reads
  "SAFE TO DELETE — 9 FOLDERS · 0 B". A row is its name in mono over the reason
  it is listed. Past `limit` rows the rest fold under "N more like these". A
  size not measured (`None`) turns totals into "at least …".
- `summary(rows) -> str` writes the footer from the ticked rows;
  `confirm_text` is words or `fn(rows) -> str`, and with a checklist the
  button is off while nothing is ticked.

```python
form = FormDialog("Review settings", window)
form.add_row("Review source", source, note="The Wallpaper Engine folder a scan reads.")
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

### Keyboard and accessibility

- **Tab** goes as the frame reads: down the sidebar, the page header's
  buttons, the page in the order its layouts set it out (a column top to
  bottom, a row left to right), then the status line's link.
  `MainWindow._order_tabs` sets it on every page change and again on every Tab
  (a page's widgets made later, a monitor's card, join the chain at its end);
  it costs about 2 ms. A dialog does the same over its own panel, so its body
  comes before its buttons. Scroll areas are not stops; tables are left by Tab.
- **Shortcuts**: Ctrl+1 … Ctrl+7 go to the pages in the sidebar's order;
  Ctrl+F puts the cursor in the page's filter with its words selected
  (`Page.filter_field()`: the Tracker's, the Rotator's and Review's authors);
  Ctrl+V on the Copier pastes paths; Enter confirms a dialog and Esc cancels
  it, except a destructive one, where Cancel has the focus and Enter.
- **Focus rings** show when the keyboard brought focus there, never after a
  click; fields you type into show theirs whenever they have focus.
- **Names**: every control a screen reader can land on says what it is: an
  IconButton by its required tool tip, a chip by its words, a nav item by its
  page, a table or list by what it lists.
- **Text** is 10 px at the least, and no text is dimmer than `text.lo` on
  glass (5.3:1). The status hues are the design's; the darkest, danger, reads
  at 4.96:1, above WCAG AA's 4.5.

`tests/test_keyboard.py` walks every page in every made-up state and checks
all of it, with `tests/gui_guard.py` recording any file call the GUI thread
makes for a path on the wallpaper disk.

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
TrackerFeed and services that never start. Every tool uses its real page with
fabricated fixture data, without library or Steam scans. The Snapshot worker
reads nothing either: a fixture's
finished job would have it count the Rotator's folders, and with no config
that is the machine's own myprojects. The fixtures are in
`tests/fixtures/ui/`: `shell.json` holds the frame's states (`--list`: idle,
we-off, running, clean, problems, scanning, building, copying, failed,
empty) as jobs put in the JobCenter, a reading put in the Snapshot
(`Snapshot.put`), sidebar items pinned to a state and the line under Next in
the loop; `MainWindow.load_fixture(state)` applies one, then the page's own
`load_fixture` (its state of that name, or its first). A page with more to
make up keeps it in a file of its own: `overview.json`, `tracker.json`,
`rotator.json`, `review.json`, `creator.json`, `copier.json`. A
page's own states (`--list` prints them per page: the Tracker's `tracking`,
`paused`, `disconnected`, `finished`, …) are asked for the same way; the page's
`frame_fixture(state)` says which of the frame's states goes with each, and
what of it the page changes (its sidebar item, "Next in the loop", and jobs).
Creator exposes `empty`, `reading`, `scanned`, `building`, `done` and
`ffmpeg-missing`; its reading/building frame jobs use the page's own counts.
A page state that opens a dialog (the Rotator's `confirm` and `broken`, Review's
`settings` and `authors`) keeps it
as `page.fixture_dialog`; the tool draws its grab over the window's, scrim and
all. Folders in fixtures are on a drive `X:` the tool reports as present. `--scale
1.5` is the user's 150 %; the PNG is then 1.5 × the size. `--reduced-motion`
draws it as Windows does with its animations off: every loop at rest, a
spinner beside the word "working".

Offscreen, Qt's screen is 800 × 800 unless told otherwise, and a popup that
does not fit under its field opens above it; the tool gives Qt a screen
larger than any window it draws (a config file named relative to its scratch
folder, as the platform's options split on `:`). A fixture's popup or dialog
is placed again once the page has settled, where it would open on the page as
it stands.

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

## Copier queue

The engine preserves CopyJob(source, count) and CopyEngine.start(jobs, destination).
CopyJob also accepts a sequence of source folders, copies and a per-job
destination. Jobs run sequentially and callbacks emit detached snapshots.
Completed (source, copy ordinal) entries stay attached to a job for session
retry, so successful copies are not duplicated twice.

inspect_queue measures file manifests and aggregates disk_usage by st_dev on a
worker. The page debounces queue edits and rejects stale measurement generations.
The engine repeats preflight on Start and checks each destination before writing.
All directory traversal and thumbnail discovery stay off the GUI thread.

Each output is reserved with mkdir, after the highest existing _copyN.
Failure or Stop removes only that newly reserved partial directory, after
checking it still resolves directly under the requested destination.
Verification compares file names and sizes against the source manifest.
Pause waits between files; cancellation also checks between large-file chunks.
Per-job failures do not interrupt subsequent jobs.

CopierPage exposes add_jobs(paths, copies, destination), plus add_folders and
folders for Tracker integration. Additions received during a run wait for the
next explicit Start. Run reports byte totals to JobCenter and copy outcomes to
the journal; copy.job.done and copy.job.failed record individual jobs.
The optional copier.verify setting is additive and old builds ignore it.

SpinCell paints copy counts without per-row widgets. Its RowDelegate creates
a kit SpinBox only while a cell is edited; the model owns EditRole, editable
flags and validation. ProgressCell gains an optional caption for speed,
elapsed time or an incomplete result. Both are in the tables kit preview.
No new dependencies are needed.

Copier fixtures: empty, queue, running, done, no-space. Filesystem tests use
temporary directories; page tests cover worker-only measurements, stale
results, editing, retries, actual output totals and service integration.
