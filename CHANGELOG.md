# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/Nykolyn/wallpaper-engine-toolkit/compare/v2.2.2...HEAD
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
