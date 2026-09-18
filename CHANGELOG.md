# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
- **The toolkit window opened from the tray shows the tray's count** instead
  of polling on a timer of its own beside it, which it went on doing after the
  window was closed.

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

### Added

- `app/engines/steam_paths.py` — locates Steam, its libraries, and Wallpaper
  Engine within them.
- Documentation covering every tab and how the pieces fit together, under
  [`docs/`](docs/).

### Removed

- **The preview-matching Creator.** There were two creator tabs: one that
  required a preview image per clip and skipped the clips without, and one that
  rendered the preview from the video. The second was doing all the work, so the
  first is gone and the second has taken the name **Creator**.

  Output is unchanged — folder naming and the `project.json` shape were already
  shared between them, so wallpapers built before and after are
  indistinguishable. Settings move from the `autocreator` section of
  `data/suite.json` to `creator` on first start.

### Added

- **Tags for built wallpapers.** The Creator writes Wallpaper Engine's genre
  tags into each `project.json`, set for a whole batch and overridable per clip.
  A card follows the batch until it is given tags of its own, and "deliberately
  untagged" is a state distinct from "not decided". The dialog offers the 25
  tags Wallpaper Engine uses and takes free text for anything else.

### Fixed

- **No genre is hard-coded any more.** Every wallpaper this tool built came out
  tagged `Girls`, a literal inherited from the tool the engine grew out of. It
  was right for one library and wrong for every other. Nothing is tagged now
  unless you ask for it.
