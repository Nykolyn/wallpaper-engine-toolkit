# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [3.9.0] - 2026-10-03

### Added

- Creator reads video dimensions, length and size on a worker, streams its table,
  and supports stopping the read. Select files, use batch or individual tags
  (including custom tags), skip untagged files, and pause or stop a build.
- Creator's completion report shows new previews and folder ids, explains every
  skipped or failed file, and offers a tagged subset retry. Rebuild the playlist
  when the output is the Rotator destination. Jobs, logs, the journal and
  off-page completion notifications follow each build.

### Changed

- Creator uses the redesigned page with source/output panels, state-specific
  progress, tables and a completion list. Removed its old card-based tab and
  unused theme helpers.

### Fixed

- **Move could permanently delete an original video when writing project.json
  failed.** Creator now copies the video, writes and reads back project.json,
  checks the copied video size and preview, then removes the source. Failed or
  cancelled builds remove only incomplete output and keep their source videos.

## [3.8.0] - 2026-10-01

Two things to do from a row of the Tracker's playlist: send the wallpaper to
the Copier, or mark its folder [protected]. See [Tracker](docs/tracker.md#a-rows-actions).

### Added

- **Send to Copier.** The Tracker's playlist ends each row with two small
  buttons, quiet until the pointer is on the row. The first puts the
  wallpaper's folder on the Copier's list for the default 3 copies; you stay
  on the Tracker, and the toast's **Show** goes to the Copier. A folder
  already on the list is not added twice.
- **Mark [protected].** The second, for a folder directly in the Rotator's
  myprojects, renames it to `[protected] <name>` after asking, so rotations
  leave it there. Workshop folders are not offered it; a folder protected
  already shows a lock. The rename runs off the window's thread; if Windows
  refuses (the folder in use, the name taken, access denied) a red toast says
  why and nothing changes. While a rotation runs, it asks you to wait.
- Wallpaper Engine's playlist is left alone: its entry for a folder marked
  this way keeps the old name and stops working until the next rotation
  rebuilds the playlist. The question says so, the toast says so, and the row
  keeps saying so (an amber lock, and *playlist entry broken until the next
  rotation* under the title).
- The Copier takes folders from other pages (`CopierTab.add_folders`), and a
  test fails if it stops taking them from the Tracker.
- Kit: `ButtonsCell` of `CellButton`s (glyph buttons side by side in a table
  cell, an empty slot keeping the rest in line, a mark that takes no click,
  each with its tool tip; `Table.action_clicked(row, column, key)`,
  `Table.action_at`, `buttons_width`), `icon_button_pixmap`; shown in the kit
  preview's tables section.

### Changed

- The two buttons take 59 px from the playlist's WALLPAPER column: beside its
  thumbnail a title has 25 px at a 1 040 px window (84 before), so it shows a
  letter or two, and 85 px at 1 280 px (144 before). Wider windows have room
  to spare.
- Kit: a double-click on a table cell's button presses it again, as on a
  button, instead of also activating the row behind it.

## [3.7.0] - 2026-10-01

The twelfth step of the redesign: Review's gallery, as a grid of cards or a
list, with the design's marks, several cards chosen at once, and the review as
one list when it is finished. See [Gallery](docs/gallery.md).

### Added

- **Grid or List.** Over an author's gallery, **Grid / List**: the cards, or a
  table of the same page — the preview, the title over its date, TYPE, SIZE,
  MARK and a **Subscribe** button that turns blue on the row under the
  pointer. The choice is remembered.
- **Choosing several.** Ctrl-click a card, or click the check that appears on
  the card under the pointer; Shift-click selects up to it; Ctrl+A selects the
  page; Esc lets go. The bar says *3 selected* and offers **Subscribe
  selected** next to **Subscribe page**; the author's row says *14 new · 3
  selected*. A plain click still subscribes.
- **Open review as a list.** A finished review's wallpapers in one list, every
  author's under their name, to look back over or subscribe from; **Back to
  the summary** returns.
- **"Already have"** is a mark of its own: a copy is kept in the Rotator's
  folders, and the card says where (*matches a folder in the reserve*). It
  used to be counted as *was yours*, which now means only "subscribed once
  and dropped". The finished review counts them apart: *already had* and
  *were yours*.
- Kit: `ButtonCell`, `BusyCell` and `DiscCell` for tables, a second line in a
  `Cell` (`sub=`), `Table.button_clicked` and `Table.set_spinning`;
  `button_pixmap` / `button_size`; `disc_pixmap` and `paint_spinner`;
  `animations.LoopTicker`, a loop subscriber that repaints only what turns;
  `ThumbLoader.forget(id)`.

### Changed

- **The cards are the design's.** A 16:9 preview cropped to fill the card
  (Workshop previews are mostly square, and used to be letterboxed in 400 px
  squares), one mark top left — New, Already have, Was yours, Queued,
  Subscribed — *scene · 214 MB* on the picture, the title and its date under
  it, and an edge in the mark's colour. Three to five cards across, as the
  window allows; one used to fit at 1 280 px.
- Under the pointer a card darkens and says **Click to subscribe**. A card
  being subscribed to shows a turning ring and *Subscribing…*; those queued
  behind it *Waiting…*. Subscribed and already-had cards are set back where
  they stand, and stay when you come back to the author.
- **Subscribe to all on this page** is **Subscribe page**, in the bar beside
  the selection's actions.
- The gallery repaints less for the same animation: with eight previews
  playing and two threads busy in Python, a frame takes 11 ms at a 1 280 px
  window (18 ms before, with a fifth of the cards on screen) and 12 ms at
  2 560 px (71 ms before). `tests/perf_gallery.py` measures it.
- `review_last.json` keeps how many each author's new wallpapers you already
  have (`have`), beside `yours`. Older builds ignore it.

### Removed

- The kind colours (`theme.kind_color`, `kind.*` tokens): a card writes its
  type as plain mono text, as the design does.

## [3.6.0] - 2026-10-01

The eleventh step of the redesign: Review as one flow — scan, go through the
authors, finish. The toolbar of buttons is gone; the page says at each point
what it is doing and what comes next. See [Review](docs/review.md). The
gallery itself is unchanged until the next step.

### Changed

- **Review is one flow.** **Scan for new items** finds the authors behind the
  wallpapers in the chosen folder and counts what each has published since
  your last visit, in one go — *34 / 118 authors checked · ≈1 min left*, the
  author it is on, and the authors with something new listed as they are
  found. "Scan" and "Count what is new" were two buttons.
- **The gallery opens when the scan finishes**, on the first author with
  something new. The list on the left holds only those authors — *106 authors
  had nothing new* under it — with *3 / 12* gone through above it.
  **Done with <author> →** ticks one and opens the next.
- **Finish review** writes the visit dates after showing the plan: *3 authors
  to create, 21 visit dates to move, 2 names to bring up to date*, then the
  changes themselves. It replaces **Update the database**. When a visit date
  would move past wallpapers a keyless list left out, it says so and Cancel is
  the default, as before. **Skip for now** leaves without writing anything.
- **When a scan stops it says why**, in plain words, with the last lines of
  its log: Steam not answering, a key Steam refuses, a database that will not
  open. **Carry on from author 34** counts the authors it had not reached,
  keeping the ones it had and asking nobody twice; **Start over** scans again.
  One author Steam has no answer for no longer stands for all the rest: it is
  counted and said, and the scan goes on.
- **A finished review says how it went**: how many authors went through, what
  was subscribed, the new items found and how many were yours before, the
  last scan — and **Reopen review**. No scan runs on its own (gate G4), so
  the page shows the last one rather than a next one.
- **Review settings** replaces **Steam key…**: what a scan reads (Wallpaper
  Engine's folders with how many wallpapers each holds, or the three across
  them), how a click subscribes (Steam directly or Steam's page — it was the
  toolbar's switch), the Steam Web API key with **Show**, **Test** and **Get a
  key**, the second folder for the authors database's backups (it was in
  **Authors database…**), and **Authors database…**. On the Settings page,
  **Review settings…** opens the same dialog.
- **Authors database…** lists the backups in a table, reads them without
  holding the window, and restores one after a confirmation that names the
  count — *Restore 38 897 authors* — with Cancel the default.
- What you had before is worked out once, at the end of the scan, for every
  author with something new, rather than when a gallery is first opened; the
  counts the review finishes with are whole.
- The messages of the old status line are toasts; the scan, a carry-on and
  the write are jobs in the status line and the activity journal, with their
  logs in `logs/review/`.

### Added

- `data/review_last.json`: the last scan — when, what it read, the authors
  with new items and which are done, what was subscribed, when the review was
  finished. Overview's **New since last review**, the sidebar's badge (the
  authors waiting) and the page's own empty state read it. Written whole or
  not at all; a newer file's keys are ignored by an older build.
- In the kit, for later pages: `SkeletonRows`, `ConsoleExcerpt`, `Spinner`;
  `EmptyState.add_content` and `width=`; `ListRow`'s `title_note`, `tick`,
  `dimmed`, `meta_tone`, `thumb_size` and `chips_inline`; `ConfirmDialog`'s
  `lines`, `note` and `safe_default`; `FormDialog.add_widget` and
  `add_action`; `Callout.body()`. In the engine: `ScanFlow`, `Session`,
  `SteamUnreachable`, and `Review.fill(stop_on=)`.

### Removed

- The Review tab and its toolbar (Look at, Scan, Count what is new, Subscribe
  by, Steam key…, Authors database…, Update the database), its status line and
  keyless banner as they were, the Steam key dialog and the old authors
  database dialog.

## [3.5.0] - 2026-10-01

The tenth step of the redesign: the Rotator's page. Its five inner tabs are
gone; the page is the next run on the left and the reserve, what is in
myprojects and the history in one table on the right, and a run in each of its
states. See [the Rotator](docs/rotator.md#the-page).

### Changed

- **The Rotator is a page of its own.** Before a run, **Next run** shows the
  two folders, the batch (changeable here as on the Settings page), how the
  batch is drawn — *Drawn at random from 8 204 never used*, or a warning when
  the history is about to start over — the playlist switch, and how many
  `[protected]` folders stay; **What a run does** lists the run's steps in the
  engine's order, each with what it will do this time, and *Takes about 15
  min* once earlier runs say so.
- **A run shows itself as it goes**: which step of how many, the count and a
  ring, *≈6 min left* from the live rate, the folder last moved, each step's
  result and time as it ends, and the counts — with the run's log beside it,
  All or Problems, written to its own file. The sidebar shows the run's bar
  and percentage, then *clean · run 39* or *2 problems*.
- **A finished run says how it ended**, in its colour — cleanly, with
  problems, stopped, or failed — with what moved and what did not, its steps
  and their times, and the history beside it with the run marked. After a
  clean run with the playlist rebuilt, it says the Tracker is counting again
  and when it will be time to rotate.
- **The broken-folders check asks in one grouped question**: *Safe to
  delete*, ticked, and *Holds media*, in warn and not ticked, each row saying
  what is in the folder and its size; the button counts what is ticked
  (**Delete 9 permanently**) and Enter goes to Cancel. The list of each
  folder's files and the per-folder Explorer button are gone: a row says what
  kinds of file it holds, and **Open folder** opens the library.
- **"Start run 39?"** replaces the old question: what goes back and what is
  drawn, the steps still to come, and the `[protected]` folders, the
  duplicates, a history reset and anything wrong with the history as notes.
- **The Reserve and Current views** list every folder with its preview,
  author, type, size and when a run last moved it in, **New** for the ones the
  next run may draw and **Unidentified** for one without a readable
  `project.json`, and a lock on `[protected]` ones. Rows come as soon as the
  folders are listed; the rest fills in from a cache
  (`data/library_meta.json`), measured on a thread while the page is open,
  the rows on screen first.
- **Stop is "Stop after this step"** (gate G7): the step under way finishes
  and the run stops there. The check still stops at once.
- **The messages that were message boxes are toasts**, and a run that ends
  while another page is shown says so there, with **Show**.
- The LogPanel's kind column fits `returned`, `deleted` and `step 2`, and
  colours `returned` and `deleted` as done, `stop` as a warning.

### Added

- **Retry the N failures**: the folders a run could not move are tried again
  in the step they failed in, after a question naming them.
- **Rebuild playlist now**, when a run did not rebuild Wallpaper Engine's
  playlist — it failed to, or the run stopped or failed first.
- **The History view**: a row per run with when it started, how long it took,
  what it moved, returned and set aside, and how it ended; **Log** reads the
  run's log back — or, for a run from before run logs, what its record says it
  moved — and **Export as CSV** saves the lot.
- **Duplicates · N** in the header, when the duplicates folder holds any: each
  folder with its size, file count and whether the reserve has one of that
  name; **Move & replace → reserve** and **Delete…**, each after a question
  naming the count and the size.
- **Run settings** in the header while a run goes or after one with problems.
- The journal's `retry.*` and `rebuild.*` entries.
- For later pages, in the kit: `IconDisc`; a `Cell`'s `icon`; a
  `TableModel`'s `row_tone`; `Table.refresh_columns()`. In the engine: the
  library index's `listed` signal. `SideScroll` is shared by the Tracker and
  Rotator pages.

### Removed

- The Rotator tab and its five inner tabs (Rotate, Reserve, Transferred,
  History, Duplicates), the old cleanup dialog, `app/ui/widgets.py`, the
  tabs' stylesheet rules and `theme.level_color`.

## [3.4.1] - 2026-09-30

The ninth step of the redesign: what the new Rotator page needs from the
engine, built and tested before the page. Little of it shows yet; what does
is below.

### Changed

- **Every rotation has a log file of its own**,
  `logs/rotator/run-<id>.log`, named by the run's id, instead of sharing the
  day's file. Each step's first and last lines are numbered (`step 2`), and
  each folder gets exactly one line — `returned`, `dupe`, `moved` or `fail`.
  See [the Rotator](docs/rotator.md#how-a-run-goes).
- **What each run did beyond its record goes in `run_meta.json`**, beside
  `history.json`: when it started and finished, how it ended (clean, with
  problems, stopped, failed), which folders failed in the return and which in
  the move, what became of the playlist, when each step ended. Overview and
  the sidebar read a run's result and time from it. `history.json` keeps the
  shape every version since 1.0.0 reads — a version before 3.0.0 read a record
  with a key it did not know as no history at all. See
  [What a run leaves behind](docs/rotator.md#what-a-run-leaves-behind).
- **A stopped rotation is in the history.** Stopped while returning folders,
  it used to be recorded nowhere: the duplicates it had set aside and the
  folders it had returned were in no record. Now any run that moved something
  is recorded — stopped, or ended by an error — so what it moved in is never
  drawn again by mistake.
- The rotation's playlist is found, when nothing matches what is in myprojects
  now, by the batches the last runs moved in: a rebuild that failed leaves the
  playlist listing the batch before, and the next rotation now brings it back
  instead of saying there is none.

### Added

For the new Rotator page (next release), in the engine and its tests:
the steps of a run as events (check → return → move → rebuild), **Stop after
this step**, **Retry** of a run's failures in the step each failed in,
**Rebuild playlist now** on its own, estimates from the times of earlier
runs, the duplicates listed with their sizes, and `library_meta.json` — the
title, type, workshop id, preview and size of each folder in the reserve and
myprojects, read on a worker a folder at a time and only again when it
changes. See [Configuration](docs/configuration.md#the-rotators-run-facts-and-library-index).

### Fixed

- **Nothing the Rotator keeps is saved over when it cannot be read.**
  `history.json` has been set aside rather than overwritten since 3.0.0;
  `config.json` was not, quite: one that could be neither read nor renamed
  gave the defaults, and the next setting you changed saved them over it. It
  now refuses, and the Settings page says the setting was not saved.
  `run_meta.json` is kept the same way.
- A setting of the wrong type in `config.json` (`"count": "many"`) was used as
  it was; it falls back to its default now. A setting written by a newer
  version is kept through a save instead of dropped.
- The rotation log said "Returned" and "Moved" also for folders that had just
  failed to move.
- Saving the history or the settings could fail if the Overview's counts were
  reading the same file at that instant; the save is tried again.
- `library.json` is written whole: a save cut short left half a file, which read
  as no index and meant the four-minute walk again. One that is not an index
  reads as none instead of stopping Review.

## [3.4.0] - 2026-09-30

The eighth step of the redesign: the Tracker page, where the count is read at
a glance.

### Changed

- **Tracker page — the leading monitor in detail, the others as summaries, the
  pace, and the playlist as a table.** See [Tracker](docs/tracker.md#the-page).
  - The leading monitor's card: the ring and `4 / 201` shown this cycle, a
    preview of the wallpaper on screen, its title, its author with a **Known**
    chip when your authors database has them, and **SHOWN FOR**,
    **REMAINING** and **CYCLE STARTED**. REMAINING is the countdown: `≈` when
    it is estimated, "paused" in amber while Wallpaper Engine has paused that
    monitor, "— disconnected" in red while Wallpaper Engine is not running,
    when the card keeps the last known wallpaper and says so. A cycle start
    worked out afterwards is marked `~`. The card's **…** menu: *Show its
    playlist below*, *Show on the tray icon*, *New cycle…*.
  - The other monitors as smaller cards; one whose playlist no rotation built
    says it follows its own order and is not counted for rotation.
  - **Pace**: the average time on screen this cycle, and when the playlist
    runs out at that pace — "At this pace the playlist empties ≈21 Sep, about
    09:10 — time to rotate (run 39)" — once 20 have been shown. Under it, what
    the count rests on, never hidden: how it is counted, how many times are
    reconstructed, how the cycle is dated; in amber, a pass Wallpaper Engine
    started over and wallpapers deleted from disk.
  - **The whole playlist shown** is unmissable: the ring closes in green, the
    count turns green, the card's edge goes green, and a note says "Whole
    playlist shown — time to rotate" with **Open Rotator**. The sidebar's count
    turns green with it.
  - **The playlist** as a table with previews: what this cycle has shown,
    newest first, the one on screen selected, how long each stayed and when it
    came up (`~` when rebuilt); then the queue, "up next, in playing order" or
    "random order, any of these can be next". Filter by title or author, pick
    an author, jump to Shown or Queue; a click opens the wallpaper in Explorer.
    Titles and types come from each wallpaper's project.json, authors from
    what Review has already asked Steam, all read in the background, the rows
    filling in as they come.
  - **Playlist settings** says where the count comes from and links to
    Settings for it, and holds **Rebuild from file times** (now asking first)
    and a **New cycle…** per monitor. The header says whether the count goes
    on with the window closed: "counting in the background" while the tray
    runs, "counting while this window is open" when it does not.
  - With nothing to show the page says why: `config.json` not found, no
    playlist on any monitor, or Wallpaper Engine not running.
  - The old Tracker tab is gone.

### Fixed

- A card's preview with no picture was read from disk again on every refresh
  of the card; it is read once.

## [3.3.0] - 2026-09-30

The seventh step of the redesign: the Overview, the page the window opens on.

### Added

- **Overview — the state of the loop at a glance.** See
  [Overview](docs/overview.md).
  - Four numbers: **Reserve** (folders, and how many were never used),
    **In rotation** (folders in myprojects, and how many today's runs swapped
    in), **Playlist** (the leading monitor's `4 / 201` and what is left; green
    once every wallpaper has been shown) and **New since last review** (what
    the last Review scan found, in amber while it waits). Each opens the page
    it comes from. A count on its way shimmers; one that is not known says why
    — *set the reserve folder in Settings*, as a link there, or *no scan yet* —
    instead of showing 0; one read before but not now is greyed and says
    **last known** (the playlist while Wallpaper Engine is not running).
  - **The loop**: a tile each for the Rotator, the Tracker and Review — what
    each is doing, a thin bar, and its numbers (`412 / 1 000 · ≈6 min left`,
    `4 of 201 shown · ≈21 Sep`, `12 authors waiting`). The tile of the job
    that runs is outlined in blue with a pulsing dot, and a tile opens its
    page. Under them one sentence says what the next run draws: *The next run
    draws 1 000 at random from the 8 204 never used*, or that the history
    resets when too few were never used.
  - **Recent activity**: the journal's eight newest entries, with the time,
    the tool, what happened and its chip; a row opens its tool's page, and
    **Open log folder** opens `data/logs/`.
  - On the right, a card per monitor, the leading one first, and the log: the
    newest lines of the running job's own log file, followed as they are
    written, or of the file written last, with a badge counting its warnings
    and errors. Open it for All / Problems, copy and the log folder.
  - **Refresh now** counts the folders again and has the Tracker look. The
    date beside the title moves on with the minute.
  - The page reads nothing from the disk while it is built; the numbers come
    from the counts the window already keeps, made on a worker.

### Fixed

- `tools/ui_snapshot.py` no longer counts this machine's own myprojects
  folder: a made-up finished job had its Snapshot read the Rotator's folders,
  with the default config. Its Snapshot now reads nothing; so do the frame's
  tests.

## [3.2.0] - 2026-09-29

The sixth step of the redesign, and the first you can see all at once: the
window's new frame. The tools themselves still look as they did, inside it.

### Changed

- **Navigation moves to a sidebar ordered by the loop; a status line shows
  running work from any page; settings live on one Settings page.**
  - The sidebar lists the pages in the order of the loop: Overview, Rotator,
    Tracker, Review, then the Creator and the Copier, and Settings at the
    foot. **Ctrl+1 … Ctrl+7** go to each. It is live: the Rotator's progress
    while it runs (`41%`) or `ready · run 39`, the leading monitor's count
    (`4/201`), `idle` or `working`, and **Next in the loop** under them
    (`197 left on Monitor1 — rotate again ≈21 Sep`). Below 1 200 px wide it
    folds into a rail of icons, their names in the tool tips.
  - The status line at the foot says what is running from any page — the
    tool, what it is doing, a bar and `412 / 1 000 · 41%` — with **Show** to go
    to it; afterwards "Nothing running · last run finished 13:58". A job that
    ends with problems or fails says so in warn or danger, with the way to it,
    until its page has been looked at.
  - The **Settings** page holds what is set once: the Rotator's reserve,
    myprojects, duplicates folder and folders per run; the Copier's
    destination; the Creator's source and output; Wallpaper Engine's
    `config.json` and "Also check every"; counting in the background; the
    monitor that leads; the Steam key and the authors database; and the data
    folder, the log folder and the selfcheck. Each change is saved as it is
    made, into the same files as before (`config.json` for the Rotator,
    `suite.json` for the rest). The Rotator's fields are read-only while it
    runs. See [Settings](docs/settings.md).
  - The Rotator, Copier, Creator and Tracker show their folders instead of
    editing them, with **Change in Settings** beside them. The Rotator's
    "Save settings" box is gone; its "Rebuild the playlist" switch stays on
    the Rotator and saves when changed. The Creator reads a new source as soon
    as you come back to it.
  - The old tabs sit in the frame as they were. Where the window is smaller
    than one of them needs, its page scrolls rather than squeezing it, and
    the window opens wide enough for the widest (the Review), within the
    screen.
  - The window opens on **Overview**, which says it is coming in the next
    release; the sidebar carries its numbers meanwhile. `--tab` takes the page
    names as well as the old tab names, in any case; a name that is no page
    opens Overview.
  - Switching pages cross-fades the page only (140 ms), and is instant with
    Windows' animations off. The sidebar and the status line never move.
- **The window draws its own title bar** (32 px, the app's mark and
  "Toolkit"), and Windows still does the window's work: dragging, Aero Snap,
  the snap layouts over the maximise button, resizing from every edge,
  double-clicking to maximise, Alt+Space. Maximised, it fills the screen's
  work area exactly. It opens at 1280 × 860, and goes no smaller than
  1040 × 720.
- **The window, the tray and its notifications say "Toolkit"**; About says
  "Toolkit for Wallpaper Engine". The program, the logon task and the data
  folder keep their names.
- The window and the tray read `suite.json` again when the other has written
  it, before they rely on or change the monitor that leads, so the Settings
  page's choice reaches the tray, and the tray's **Show on the icon** does not
  write back what the window changed.
- A second launch or the tray can send the window a command (`show:<page>`)
  besides the plain request every version understands.

### Removed

- The tab bar.

### Added

- `tools/ui_snapshot.py`: the real window, offscreen, in a made-up state from
  `tests/fixtures/ui/`, saved as a PNG at a size and scale — for comparing
  with the design. It reads nothing of this machine's. See
  [Snapshots](docs/development.md#snapshots).
- **Run selfcheck** on the Settings page: `--selfcheck` from the window.
- `tests/test_shell.py`.

## [3.1.0] - 2026-09-28

The fifth step of the redesign: the services behind the new frame. The tabs
look as they did, but what they do is now written down as it happens.

### Added

- **An activity journal, and per-tool log files in `data/logs/`, kept for 30
  days.** Every rotation, folder check, clean-up of broken folders, action on
  the duplicates, Creator build, Copier run, Review scan, count of what is new
  and authors database update writes its log to
  `data/logs/<tool>/YYYY-MM-DD.log` while it runs: a line per event (a line
  per folder a rotation moves), as time, kind and message. When it ends, one
  line in `data/activity.jsonl` says how: the rotation's run number, what was
  moved, returned and set aside, what failed. With the window open, the
  leading monitor's playlist advancing to a new wallpaper, reaching its end or
  starting over goes in the journal too. Log files older than 30 days are
  deleted once at start-up, on a thread of their own, and the journal starts a
  new file past 2 MB, keeping one before it. `data/tracker.log` stays where it
  was. See [The journal and the log files](docs/configuration.md#the-journal-and-the-log-files) and
  [Troubleshooting](docs/troubleshooting.md#where-the-logs-are).
- `app/services/`, what the status line, the sidebar and Overview of the next
  steps will read. Nothing shows these yet:
  - `JobCenter` knows what is running, and which job leads (a rotation, then a
    copy or a build, then a Review scan). It gives a rate and time left only
    once the job's own progress has been measured.
  - `ActivityJournal` writes the journal and reads its newest entries from
    the end of the file.
  - `LogStore` writes the log files, reads their tail and sweeps them.
  - `Snapshot` keeps the counts: the reserve and what in it was never used,
    myprojects, the last run, the leading monitor, and the last review. They
    are read on a thread of their own, never on the window's, and each keeps
    its age, so a page can say "last known".
- `tests/test_services.py`: 183 checks. `--selfcheck` reports `app.services`.

### Changed

- The window makes its own look at the tracker and hands it to the Tracker
  tab, so the snapshot and the journal see the same one.

### Fixed

- **The count of never-used folders before a rotation is no longer short.**
  "Unique not-yet-used available" subtracted every folder the history
  remembers, including the ones used once and deleted since. So it came out
  short, and could warn that the history would reset when the rotation then
  did not. It is now counted by name, the way the rotation draws.

## [3.0.3] - 2026-09-28

The fourth step of the redesign: the kit's feedback — what reports work while
it runs, and what asks before anything is written. **Nothing you can see
changes**; the pages that come next are built from these.

### Added

- **`LogPanel`**, a job's log as a card: a line per event in three mono
  columns (time · kind · message), coloured by kind as the console is (moved
  and done ok, skip and dupe warn, fail and error err, the rest mid). It keeps
  the last 5 000 lines in a ring, filters to the problems (All / Problems),
  copies the selected lines or all of them, and follows the newest line only
  while you are at the bottom: scroll up to read and the lines stay put, even
  as the oldest leave. It closes to its header over 220 ms with the count of
  problems in a badge, and shows a live dot while its job runs.
- **`Toast`** and **`ToastHost`**: ok, info, warn and danger, with an action
  link and a close button, stacked in the page's bottom-right corner, at most
  four. A toast rises in over 180 ms and goes after 6 s; a danger toast stays
  until it is closed, and the pointer resting on any toast holds it.
- **`StatusLine`**, the line at the foot of every frame: running (a pulsing
  dot, a 150 px bar, the count and "Show"), idle, warn and error (with a link
  that does something about it). Short of room its words elide; its count
  never does.
- **`ConfirmDialog`** and **`FormDialog`**, over a scrim that covers the
  window, with no motion. A confirmation lists the steps it will take, or the
  very rows it will act on, grouped under headers whose box ticks or clears
  the whole group ("SAFE TO DELETE — 9 FOLDERS · 0 B"), long groups folded
  under "N more like these". Its footer counts what is ticked, the Danger
  button says the same number ("Delete 9 permanently") and is off with none,
  and a size that was not measured makes the total "at least …". In a
  destructive one Cancel is the default and has focus when it opens, so
  Enter cancels; Esc cancels either kind, and a cancelled one returns no rows.
  A form's Save stays off until every field is right, and a field says what
  is wrong once it has been touched. See
  [the kit](docs/development.md#the-kit).
- `LinkButton`, `Elided` (one line cut with an ellipsis) and `LiveDot`, which
  these share; `format.clock(..., seconds=True)` for a log line's time.
- The kit preview gains Feedback, and `--grab dialogs <folder>` saves each
  dialog open over a made-up page.
- `tests/test_kit_feedback.py`: 114 checks.

### Changed

- `theme.py` gains a 14 px type style for dialog titles and the component
  sizes of the log, toasts, the status line and dialogs; `animations.py`
  gains the toast's 180 / 120 ms.

## [3.0.2] - 2026-09-28

### Fixed

- **A start inside another app's sandbox no longer moves the data there.** A
  Store app's terminal — the Claude desktop app's, for one — runs what it
  starts inside that app's sandbox, where files and folders it creates under
  `%LOCALAPPDATA%` land in the app's private copy while reading as if they had
  gone to the real folder.
  The first start of 3.0.0 was a `--selfcheck` from such a terminal. It moved
  the data into Claude's copy, and the tray tracker, started by Task Scheduler,
  found the real folder empty, began a new one, and ran on it for three
  minutes; a window opened in that time showed the Rotator unset and no
  history. The data was copied to the real folder, verified file by file, and
  nothing was lost. Now a start that finds what it creates redirected — it makes
  a folder and looks for it among the apps' private copies — moves, creates and
  marks nothing, names the app in its `data:` line, and warns if that app holds
  a copy of the data folder. The move is left to the next start outside it.
  See [Where things live](docs/configuration.md#moved-out-of-the-program-folder-in-300).

## [3.0.1] - 2026-09-26

Programs the toolkit starts no longer run on its DLLs.

### Fixed

- **Wallpaper Engine no longer loads the build's Visual C++ runtime when a
  rotation restarts it.** In a PyInstaller build, the bootloader sets
  `_internal` as the process's DLL directory, and run-time hooks put `_internal`
  and `_internal\PySide6` at the front of `PATH`. Windows passes both on to
  every process the toolkit starts, and the DLL directory is searched before
  System32. On 26 September, Wallpaper Engine restarted after a rotation was
  running on `_internal\VCRUNTIME140.dll` and `VCRUNTIME140_1.dll`, and
  `build.cmd` could not replace them while it ran (robocopy ERROR 32). Every
  program the toolkit starts now goes through `app/external.py`: Wallpaper
  Engine, schtasks, ffmpeg, Explorer, and the browser or Steam for links. The
  DLL directory is cleared while each process is created, then put back
  (2 ms median). Every path into the bundle is left out of the child's
  environment. Checked on a build: the old code's restart loaded both DLLs from
  the build's `_internal`. After the fix both come from System32, and the
  engine's `PATH` begins with `C:\WINDOWS\system32`. `--selfcheck` reports this
  as `other programs`.
- **Opening a folder or a link no longer goes through the toolkit's own
  process.** `os.startfile` and `webbrowser` call `ShellExecute` in-process, so
  a program they start inherits an environment that cannot be cleaned. The
  authors dialog now opens folders with Explorer. The Review tab opens Steam
  and workshop pages with `cmd /c start`, in a clean child. The URL is quoted
  so that cmd.exe passes the `&`s in a workshop URL through.

## [3.0.0] - 2026-09-26

The data moves out of the program folder, and the Rotator's history can no
longer disappear without a word.

On 2026-09-20 a build run the wrong way emptied
`dist\WallpaperEngineToolkit\data`. The Steam key was noticed and put back; the
Rotator's history, with the runs of 12 and 19 September, was not. Nothing said
so, and the rotation of 26 September began a new history: "History: 1 runs".

### Changed

- **A built toolkit keeps its data in `%LOCALAPPDATA%\WallpaperEngineToolkit`**,
  not in `data\` beside the exe. The program folder is what a build replaces, an
  update overwrites and an uninstall deletes; nothing that happens to it can
  reach the data now. The first start moves the folder, once: every file is
  copied and compared with its original by size and SHA-256, and only then does
  the old `data\` go to the Recycle Bin. If anything fails, nothing is deleted,
  the old folder stays in use, and the next start tries again. `--selfcheck` and
  `tracker.log` say where the data is and whether it was just moved. A source
  run keeps `data\` beside `run_app.py`; `WALLPAPER_TOOLKIT_DATA` names any
  other folder. See [Where things live](docs/configuration.md#where-things-live).
- **Breaking:** a build older than 3.0.0 does not look there. Going back to one
  means copying the folder back first — see
  [Building](docs/building.md#updating-an-installed-copy).
- A source run keeps the Rotator's `config.json` and `history.json` in `data\`
  with everything else, no longer in `app/engines/data/`.

### Fixed

- **The Rotator's history is never lost quietly.** Each save writes the whole
  file under a temporary name that then replaces the old one, and keeps a
  snapshot in `history_backup/` (the newest 30). A missing `history.json` is
  put back from the newest snapshot; an unreadable one is renamed
  `history.unreadable-<time>.json`, never written over, and put back the same
  way. Before, either read as an empty history, and the next rotation saved it
  over the old one. The History tab and the question before a rotation say
  what happened, and a file that can be neither read nor renamed stops
  rotations until it can. See
  [the Rotator](docs/rotator.md#the-history-is-not-lost-quietly).
- A run with a key this version does not know no longer makes the whole history
  unreadable: the run is read and the key written back. Going back to an older
  version after a newer one had added a field lost every run that way.
- An unreadable `config.json` is renamed `config.unreadable-<time>.json` before
  the Rotator's defaults are written, rather than overwritten by them.

## [2.2.3] - 2026-09-25

The third step of the redesign: the kit's data display — the fields, bars,
rings, cards, tables and empty states the pages are made of. **Nothing you
can see changes**; the Review gallery's preview loader moved into the kit and
works as before.

### Added

- **Formatting** in `app/ui/kit/format.py`, so every page writes numbers one
  way: counts with a no-break thousands space (`33 421`), sizes (`214 MB`,
  `1.1 GB`), durations (`4 min 12 s`, `14 min`, `≈6 min left`), the design's
  date styles (`19 Sep 12:44`, `Fri 09:10`, `Saturday 19 September, 13:44`),
  ratios (`4 / 201`, `4/201`, `412 of 1 000`), and the `≈` (estimated) and
  `~` (reconstructed) marks.
- **Kit components**, named as in the design, each with its states:
  `PathField` (empty, compact with the path elided from the left, invalid,
  disabled; a drop target; the folder checked on a worker thread),
  `TagSelect` (the Creator's 25 tags as pills and a four-column popup, and a
  per-clip mode: follows the batch, own tags, or none), `ProgressBar` and
  `ProgressRing`, `StatCard`, `MonitorCard` (compact and detail, filled from a
  `MonitorView`), `Table` with `TableModel`, group rows, sorting and its
  toolbar, summary and footer bars, `ListRow` and `RowList`, `Thumb`,
  `EmptyState` (with a drop-zone variant), `StepList` and `ActivityLine`. See
  [the kit](docs/development.md#the-kit).
- **Tables hold 33 000 rows**: the model keeps indices rather than rows (it
  builds in about 4 ms), and the table paints only the rows on screen, a
  whole row per call, with about fifteen calls into Qt per row, so another
  thread busy in Python does not stall scrolling.
- **Local previews** (`preview.gif`, `.jpg`, `.png` in a wallpaper folder) are
  read off the GUI thread, for the rows on screen only, and kept as small
  stills in `data/thumbs/local/`, keyed by the preview's path, time and size.
- The kit preview (`tools\kit_preview.py`) gains Fields, Progress, Cards,
  Tables (33 000 made-up rows to scroll) and Empty states; its previews are
  painted, never read from your library.
- `tests/test_kit_data.py`: 169 checks, among them that nothing touches the
  file system on the GUI thread while a table of thumbnails is on screen.

### Changed

- The Review gallery's `ThumbLoader` lives in `app/ui/kit/thumbs.py` now; the
  gallery imports it from there. The self-check also reports the kit.
- `theme.py` gains the tokens, type styles and component sizes these use.

## [2.2.2] - 2026-09-24

The second step of the redesign: the kit's controls exist, with every state,
and nothing in the app uses them yet. **Nothing you can see changes**; this is
for the pages that come next.

### Added

- **Kit controls** in `app/ui/kit/`, named as in the design: `AccentButton`,
  `SecondaryButton`, `DangerButton`, `GhostButton` (and its outlined header
  variant), `IconButton`; `TextInput`, `SpinBox`, `Dropdown`; `Checkbox`,
  `Toggle`, `SegmentedControl`, `Pagination`; `Chip` in its fourteen variants;
  `GlassPanel`, `Overline`, `CardTitle`, `Callout`, `MetricStrip`. Each has the
  design's five states. The focus ring shows only when the keyboard brought
  focus there, a button's fill eases over 140 ms (and changes at once with
  Windows' animations off), and shadows and rings are drawn outside the box
  without moving it. A `DangerButton` is never a dialog's default button, so
  Enter cannot delete. See [the kit](docs/development.md#the-kit).
- The kit preview (`tools\kit_preview.py`) shows them all, laid out like the
  design system's Buttons, Inputs, Selection and Chips pages, and Panels.
- `tests/test_kit_controls.py`: 95 checks.

### Changed

- `theme.py` gains the tokens the controls use (the tinted edges of chips,
  callouts and panels, a neutral callout ground, two type styles) and the
  component sizes, so no kit module holds a number of its own.

## [2.2.1] - 2026-09-23

### Fixed

- **An unset Rotator folder no longer means the folder the app was started
  from.** With the duplicates folder left empty, its default, the Duplicates
  tab listed the working directory's subfolders. For the built exe that is
  usually the install folder: `data\` (the authors database, the Steam key,
  the tracker's history) and `_internal\`. **Delete all** deleted them after a
  plain "delete 2 folder(s)?". An empty reserve was as bad: the rotation
  started, and with more to pick than the reserve held it carried `data\` and
  `_internal\` into `myprojects`. Both were reproduced on 2.1.1 in a stand-in
  install folder, and 2.2.0 left that code as it was. Now an empty or relative
  folder is *not set*. Its list says so and shows nothing, the Duplicates
  buttons stay off, and a rotation will not start until all three folders are
  set. The functions that delete and move refuse on their own as well. See [Duplicates](docs/rotator.md#duplicates).
- **Move & replace → reserve** refuses when the duplicates folder is the
  reserve itself. Replacing used to delete the very folder it was about to move.
- The Duplicates confirmation now names the folder it deletes from, and for a
  move, the reserve it replaces into.

## [2.2.0] - 2026-09-23

The first step of the redesign: the design system becomes code. No screen is
restructured yet; the tabs keep their layout and take on the new look.

### Changed

- **The whole app moves to the new dark "glass" palette.** A radial gradient
  behind the window, translucent panels over it, and every standard control —
  buttons, fields, spin and combo boxes, check boxes, lists, headers, scroll
  bars, menus and tool tips — drawn from one stylesheet generated from the
  design's tokens. Secondary text is lighter than before (the design holds
  captions to 4.5:1 on a panel), and the default text is the design's 12.5 px.
- **Buttons carry drawn icons instead of Unicode glyphs** (Start, Build,
  Cancel, Stop, Rescan), in the colour of their text, and greyed with it when
  disabled.
- Drop-down lists open below their box, as the design's do, rather than over
  it; and ticked rows in lists (the cleanup dialog's folders) use the same
  check box as everywhere else.
- **Motion is retimed to the design's three durations** — 90, 140 and 220 ms —
  on its one curve. Progress bars ease over 220 ms, pages cross-fade over 140.
- **Windows' own "Animation effects" switch is honoured.** With it off, the
  app's transitions become instant changes.

### Added

- The design's 26 icons, and the six glyphs its screens draw outside the set,
  kept in the code as SVG: nothing new to bundle.
- `tools/kit_preview.py`, a development window that draws the design system —
  colours, type, spacing, radii, shadows, icons and the live loops — from the
  app's own code, with `--grab` to save a section as a picture. See
  [Look and feel](docs/development.md#look-and-feel).
- `--selfcheck` reports whether the build can draw SVG, which the check boxes'
  ticks and the spin arrows now depend on.

### Removed

- **The unused light palette.** The design is dark only; its token names say
  what a colour is for, so a light one can be added back without touching the
  UI.

## [2.1.1] - 2026-09-22

### Removed

- **`WallpaperEngineToolkit.spec`.** Nothing read it — `build.cmd` passes its
  arguments to PyInstaller directly — and PyInstaller rewrote it in the project
  root on every build, which left the checkout showing a change after each
  one. It was also the one way to build around `build.cmd`'s protection of
  `dist\...\data`: `pyinstaller WallpaperEngineToolkit.spec` builds straight
  into `dist\` and empties it first. See
  [Why there is no spec file](docs/building.md#why-there-is-no-spec-file).

### Changed

- `build.cmd` has PyInstaller write its spec into `build\` (`--specpath`), and
  hands it absolute paths, so a build leaves the source tree untouched. Checked
  with a full build: the icon, the version resource and the bundled ffmpeg all
  land where they did.

## [2.1.0] - 2026-09-21

### Removed

- **Everything MongoDB.** The authors have lived in `data/authors.sqlite` since
  2.0.0 and the move is done, so what carried it is gone:
  `tools/import_authors_from_mongo.py`, `app/engines/authors_db.py`,
  `mongo_srv.py` (resolving `mongodb+srv` through Windows), `migration.py` (the
  identifier re-keying), their three test files, and `pymongo` and `dnspython`
  from `requirements.txt`. The app stopped using any of it in 2.0.0; this is the
  code catching up. Coming from a version before 2.0.0 means going through
  [2.0.1](https://github.com/Nykolyn/wallpaper-engine-toolkit/releases/tag/v2.0.1)
  first — its import tool is the way across.
- `SteamClient.resolve_vanity` and `resolve_vanities`, which only the re-keying
  called.
- The `WET_TEST_CLUSTER` variable, and with it the "Environment variables"
  section of [Configuration](docs/configuration.md), which held nothing else.

### Fixed

- The link from [Rotator](docs/rotator.md) to the Tracker's
  [What neither can see](docs/tracker.md#what-neither-can-see) pointed at the
  section's old name and went nowhere.

## [2.0.1] - 2026-09-21

### Added

- **How to update an installed copy**, in
  [Building](docs/building.md#updating-an-installed-copy): stop the tracker,
  keep the build being replaced, build, selfcheck, start the tracker — and how
  to go back. Written from the 1.2.1 → 2.0.0 update of a live install, where
  all 365 files in `data\` came through the build untouched.

### Fixed

- The selfcheck example in [Building](docs/building.md#checking-a-build) now
  matches what a 2.0.0 build prints, version line and all, and says why a 404
  from the preview host is a pass.
- `app/secrets.py` no longer describes the MongoDB connection string as
  something the app uses; only the import tool reads it now.

## [2.0.0] - 2026-09-21

### Changed

- **The authors database is a file now, not a MongoDB server.** The Review tab
  keeps its authors in `data/authors.sqlite`, made the first time it is needed.
  There is nothing to set up: no Atlas account, no cluster, no connection
  string, no VPN workaround — press **Scan** on a fresh install and it works.
  Opening it takes 5 ms where reaching the server took 1.3 s, and looking up a
  week's 616 authors takes 11 ms. See
  [Authors database](docs/authors-database.md).
- **Only what the review uses is kept**: the key (steamID64, or the vanity name
  for the few authors nothing could identify), the name, when they were found
  and when you last visited. `favorite`, `reviewedFavorites`, `newWallpapers`,
  `referenceLink`, `creator`, `_id` and `__v` belonged to the old web app and
  are gone. Times are stored with their zone written on them, which retires the
  three-hour trap of Mongo's zoneless UTC.
- **A write is all or nothing.** Every change the review plans goes in one
  transaction; if any part of it fails — an author filed under a key someone
  else already has, a record that is no longer there — none of it is written.
- **The Steam Web API key is optional.** Without one the tab works and says,
  plainly, what it cannot see: a banner under the toolbar (with **Add a key…**,
  and **Hide**), *list incomplete* on every author counted without a key, and a
  warning — with **Cancel** as the default — before a visit date is written from
  such a list, because that moves the date past the mature wallpapers Steam
  left out. Keyless scans take author names from the cache rather than fetching
  a page per author, which would be three minutes a week. See
  [What a Steam Web API key is for](docs/review.md#what-a-steam-web-api-key-is-for).
- **The toolbar's "Steam and database…" is two buttons**: **Steam key…** and
  **Authors database…**.
- **`build.cmd` never lets PyInstaller near `dist\...\data`.** It builds into
  `build\stage` and mirrors the result into `dist\` with the live `data`
  folder excluded from both the copy and the purge, so a failed build cannot
  delete anything in it. It also refuses to start while the toolkit is running.
  See [Building](docs/building.md#what-the-build-script-does-around-pyinstaller).
- `pymongo` and `dnspython` are no longer in the built app. They stay in
  `requirements.txt` for one release, for the import tool below.

### Added

- **Backups after every change.** A snapshot of the whole database — gzipped
  JSON, about 1 MB, readable without this app — is written, read back and
  counted after each write, and a line in `journal.jsonl` records every row
  the change touched, as it was and as it became. Snapshots are pruned to the
  last ten, one a day for a week, one a week for a month and one a month for a
  year. See [Backups](docs/authors-database.md#backups).
- **A second copy somewhere else.** **Authors database…** can name a second
  folder — another drive, or one a cloud client syncs — and every snapshot is
  copied there too. Off until chosen; an unreachable folder never stops a write,
  and the tab says the copy was not made.
- **Restoring a backup** from the same dialog, with both counts shown before
  anything is replaced and the current state snapshotted first. A damaged
  database file is detected when it is opened, set aside rather than deleted,
  and the tab offers the backups straight away.
- `tools/import_authors_from_mongo.py` — the one-time move from the MongoDB
  collection: a fresh dump, kept beside the backups; converted; written in one
  transaction; and checked row by row against the dump. The collection is only
  read. See [Coming from the MongoDB version](https://github.com/Nykolyn/wallpaper-engine-toolkit/blob/v2.0.0/docs/authors-database.md#coming-from-the-mongodb-version).

### Fixed

- **Pressing Update twice tried to create the same authors again.** A card
  stayed "new" after its author had been written, so the next plan created them
  a second time. Written changes are now applied to the cards themselves.

### Removed

- The MongoDB connection string field, its **Test** button and the **Take the
  connection string from a .env file…** button, with the `WET_DB_CLUSTER` and
  `WET_SERVER_ENV` variables they read.

## [1.2.1] - 2026-09-20

### Fixed

- **"Count what is new" failed with "Signal source has been deleted".** One
  press after a scan and the tab printed that in red instead of counting
  anything. The engine was given the scan's own worker thread to report
  progress through, and a worker is deleted the moment it finishes — so the
  next thing to report progress was reporting to something that no longer
  existed. Progress goes through the tab now, which is what owns the workers
  and outlives all of them. Introduced in 1.2.0.

## [1.2.0] - 2026-09-20

### Added

- **Choose what to review.** The `new` folder was written into the code; it is
  a box in the toolbar now, filled from Wallpaper Engine's own `config.json`
  every time the tab opens, so a folder made this morning is in the list this
  afternoon. Besides the folders by name there are three that cross them: **all
  folders**, **not in any folder**, and **everything you have**. The middle one
  exists because a folder is not the library — a wallpaper subscribed to last
  night and not yet filed was unreachable before. The choice is remembered. See
  [What gets reviewed](docs/review.md#what-gets-reviewed).
- **A card says what a wallpaper is and what it costs.** Scene, Video, Web,
  Application and Preset each have their own colour and glyph, and a download of
  **a gigabyte or more is amber**. All three facts used to be one grey line of
  prose under the title, read last if at all — and a 6 MB scene and a 1.4 GB
  video are not the same decision. See
  [What it is, and what it costs](docs/gallery.md#what-it-is-and-what-it-costs).

### Changed

- **A selected card is marked by its border, not by its fill.** It used to turn
  solid blue while its title and its facts kept the greys they were chosen for,
  which left the size and the date all but invisible on the one card you were
  looking at. The marks over a subscribed card's picture also stay bright
  through the dimming, and "new" and "subscribed" are no longer both green.
- **Counting what is new is faster, and asks less.** The requests go out
  through the Steam client's own pool instead of one after another — 17.9 s for
  the 85 authors of an ordinary week — the workshop folder is read once for the
  whole count rather than once per author, and what you used to own is not
  asked at all. That question is worked out on a thread when you **open** an
  author, where the answer is actually looked at: 0.02 s for the first gallery
  and nothing for every one after it.

### Fixed

- **"new" no longer sits on wallpapers you have had before.** The mark was
  painted from "published since your last visit", which is true of every card
  in a gallery — so a wallpaper downloaded, kept and deleted twice still called
  itself new. It now means what it says: this machine has never had it. The
  record that was missing is **Wallpaper Engine's own folders**, which remember
  an id for ever. A wallpaper dropped without ever being copied leaves nothing
  in the local libraries, and 14 915 ids on the machine this was built against
  are exactly that — against 425 the copies index knew about. Of one author's
  29 offered wallpapers, 11 turn out to be ones already seen and dropped. Until
  the question has been asked, a card claims neither answer. See
  [Whether you have had a wallpaper before](docs/review.md#whether-you-have-had-a-wallpaper-before-is-asked-separately).

## [1.1.0] - 2026-09-19

### Added

- **A rotation rebuilds Wallpaper Engine's playlist and starts it over.** No
  more opening the playlist after a rotation to clear out what went back to the
  reserve and add what came in. The playlist is found by what is in it, never
  by its name — any playlist, saved or running on a monitor, made mostly of the
  folders the rotation takes back — so renaming it or keeping a saved twin
  breaks nothing. It is refilled with everything now in `myprojects`, keeping
  its name, settings and anything it holds from elsewhere, and the monitor
  playing it starts a fresh pass. Wallpaper Engine is closed for the move the
  way its tray's Quit closes it and started again with the arguments it had —
  a few seconds without wallpapers — and it comes back whatever became of the
  rotation. Both files it rewrites are copied to `data/playlist-refresh/`
  first. On by default; a checkbox on the Rotator tab turns it off. See
  [Wallpaper Engine's playlist](docs/rotator.md#wallpaper-engines-playlist).

### Changed

- **The folder on screen moves too.** With Wallpaper Engine closed for the
  rotation, nothing in `myprojects` is held open while the folders move.

## [1.0.0] - 2026-09-18

### Added

- **Versions.** The toolkit has a version — 1.0.0 is the first — shown in
  the window title, by `--version`, and on the exe's Details tab. Every change
  that reaches `main` raises it and gets a section here, and merging it tags
  `vX.Y.Z` and publishes a GitHub release with that section as its notes. See
  [Versions](CONTRIBUTING.md#versions).
- **Hang logs.** If the window or the tray stops answering for five seconds,
  the stack of every thread goes to `data/window-hangs.log` or
  `data/tracker-hangs.log`, written while it is still stuck.
- `--tab NAME` opens the window on a given tab.
- `app/engines/steam_paths.py` — locates Steam, its libraries, and Wallpaper
  Engine within them.
- Documentation covering every tab and how the pieces fit together, under
  [`docs/`](docs/).
- **Tags for built wallpapers.** The Creator writes Wallpaper Engine's genre
  tags into each `project.json`, set for a whole batch and overridable per clip.
  A card follows the batch until it is given tags of its own, and "deliberately
  untagged" is a state distinct from "not decided". The dialog offers the 25
  tags Wallpaper Engine uses and takes free text for anything else.

### Changed

- **The Tracker looks when Wallpaper Engine writes, not every 30 seconds.**
  `playliststate.bin` is rewritten at every wallpaper change, so a change now
  reaches the count within a second or so, together with the countdown ring,
  and wallpapers skipped in a hurry are no longer lost between two looks. A
  look costs about 4 ms instead of 50, and `data/tracker.json` is written only
  when something in it changed — it used to be rewritten ~2900 times a day. A
  safety check every five minutes remains ("Also check every" on the tab); the
  old "Poll every" setting is gone. Without a readable state file the tracker
  falls back to looking every 30 seconds. See
  [When it looks](docs/tracker.md#when-it-looks).
- **A random playlist is counted from Wallpaper Engine's own record of the
  pass**, not from file handles and access times. It sees web wallpapers,
  keeps counting across time the tray was not running, and withdraws credits
  that access times got wrong — which made "Playlist finished" arrive a
  wallpaper early. Sorted playlists are counted as before. See
  [Wallpaper Engine's own record](docs/tracker.md#wallpaper-engines-own-record).
- **The toolkit window is its own program when opened from the tray**, at
  normal priority. Ending a frozen window used to end the tracker with it, since
  they were one process. A second click, or a second launch of the exe, brings
  the open window forward instead of starting another. Its Tracker tab looks
  for itself, on the tray's schedule — about 4 ms a look, taken only when
  Wallpaper Engine writes.
- **Renamed to Wallpaper Engine Toolkit** (from "Wallpaper Suite"). The window
  title, the built executable and the logon task all carry the new name.
  An existing autostart entry registered under the old name is migrated on the
  first run — see [Autostart](docs/tracker.md#starting-with-windows).
- **Folder defaults are detected instead of assumed.** Steam's own registry
  entry and `libraryfolders.vdf` are read to locate Wallpaper Engine, its
  `myprojects` folder and the workshop content folder, so a first run on any
  machine arrives with the right paths already filled in. Folders that cannot
  be derived — where you keep clips, where you keep previews — now start empty
  and ask, rather than pointing somewhere that does not exist.
- **The MongoDB cluster is configuration, not source.** `uri_from_env_file()`
  takes the cluster host from `DB_CLUSTER` in the `.env` it reads, or from the
  `WET_DB_CLUSTER` environment variable.

### Removed

- **The preview-matching Creator.** There were two creator tabs: one that
  required a preview image per clip and skipped the clips without, and one that
  rendered the preview from the video. The second was doing all the work, so the
  first is gone and the second has taken the name **Creator**.

  Output is unchanged — folder naming and the `project.json` shape were already
  shared between them, so wallpapers built before and after are
  indistinguishable. Settings move from the `autocreator` section of
  `data/suite.json` to `creator` on first start.

### Fixed

- **The window froze during a Review** — while counting what was new and
  clicking through authors. Every call from Python into Qt waits for Python's
  global lock whenever another thread is busy in Python, and the gallery's
  animation made hundreds of those calls a second. Frames are now decoded and
  scaled by Qt alone, the animation pauses itself when the window falls behind,
  and the interpreter hands the lock over ten times sooner. On a real page with
  a busy thread beside it the window went from never answering to at most 4 ms
  late. See [Gallery](docs/gallery.md#and-never-at-the-windows-expense).
- **The gallery kept every preview it had ever shown in memory** — 945 MB after
  ninety authors. It keeps the page on screen, and stays at 150–200 MB.
- Turning a page no longer leaves the previous page's downloads queued ahead of
  the one on screen.
- "Count what is new" and a click on an author it had not reached yet no longer
  fetch the same author twice at once.
- Noticing a wallpaper subscribed elsewhere no longer lists Steam's workshop
  folder on the GUI thread every four seconds.
- **No genre is hard-coded any more.** Every wallpaper this tool built came out
  tagged `Girls`, a literal inherited from the tool the engine grew out of. It
  was right for one library and wrong for every other. Nothing is tagged now
  unless you ask for it.

[Unreleased]: https://github.com/Nykolyn/wallpaper-engine-toolkit/compare/v3.9.0...HEAD
[3.9.0]: https://github.com/Nykolyn/wallpaper-engine-toolkit/compare/v3.8.0...v3.9.0
[3.8.0]: https://github.com/Nykolyn/wallpaper-engine-toolkit/compare/v3.7.0...v3.8.0
[3.7.0]: https://github.com/Nykolyn/wallpaper-engine-toolkit/compare/v3.6.0...v3.7.0
[3.6.0]: https://github.com/Nykolyn/wallpaper-engine-toolkit/compare/v3.5.0...v3.6.0
[3.5.0]: https://github.com/Nykolyn/wallpaper-engine-toolkit/compare/v3.4.1...v3.5.0
[3.4.1]: https://github.com/Nykolyn/wallpaper-engine-toolkit/compare/v3.4.0...v3.4.1
[3.4.0]: https://github.com/Nykolyn/wallpaper-engine-toolkit/compare/v3.3.0...v3.4.0
[3.3.0]: https://github.com/Nykolyn/wallpaper-engine-toolkit/compare/v3.2.0...v3.3.0
[3.2.0]: https://github.com/Nykolyn/wallpaper-engine-toolkit/compare/v3.1.0...v3.2.0
[3.1.0]: https://github.com/Nykolyn/wallpaper-engine-toolkit/compare/v3.0.3...v3.1.0
[3.0.3]: https://github.com/Nykolyn/wallpaper-engine-toolkit/compare/v3.0.2...v3.0.3
[3.0.2]: https://github.com/Nykolyn/wallpaper-engine-toolkit/compare/v3.0.1...v3.0.2
[3.0.1]: https://github.com/Nykolyn/wallpaper-engine-toolkit/compare/v3.0.0...v3.0.1
[3.0.0]: https://github.com/Nykolyn/wallpaper-engine-toolkit/compare/v2.2.3...v3.0.0
[2.2.3]: https://github.com/Nykolyn/wallpaper-engine-toolkit/compare/v2.2.2...v2.2.3
[2.2.2]: https://github.com/Nykolyn/wallpaper-engine-toolkit/compare/v2.2.1...v2.2.2
[2.2.1]: https://github.com/Nykolyn/wallpaper-engine-toolkit/compare/v2.2.0...v2.2.1
[2.2.0]: https://github.com/Nykolyn/wallpaper-engine-toolkit/compare/v2.1.1...v2.2.0
[2.1.1]: https://github.com/Nykolyn/wallpaper-engine-toolkit/compare/v2.1.0...v2.1.1
[2.1.0]: https://github.com/Nykolyn/wallpaper-engine-toolkit/compare/v2.0.1...v2.1.0
[2.0.1]: https://github.com/Nykolyn/wallpaper-engine-toolkit/compare/v2.0.0...v2.0.1
[2.0.0]: https://github.com/Nykolyn/wallpaper-engine-toolkit/compare/v1.2.1...v2.0.0
[1.2.1]: https://github.com/Nykolyn/wallpaper-engine-toolkit/compare/v1.2.0...v1.2.1
[1.2.0]: https://github.com/Nykolyn/wallpaper-engine-toolkit/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/Nykolyn/wallpaper-engine-toolkit/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/Nykolyn/wallpaper-engine-toolkit/releases/tag/v1.0.0
