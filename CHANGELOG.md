# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

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
