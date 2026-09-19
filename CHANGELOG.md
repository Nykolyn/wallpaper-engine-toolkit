# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/Nykolyn/wallpaper-engine-toolkit/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/Nykolyn/wallpaper-engine-toolkit/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/Nykolyn/wallpaper-engine-toolkit/releases/tag/v1.0.0
