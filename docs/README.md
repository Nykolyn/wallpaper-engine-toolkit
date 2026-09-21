# Wallpaper Engine Toolkit — documentation

Five tools in one window, each solving a different part of the same problem:
keeping a large Wallpaper Engine library moving without doing it by hand.

Start with [Getting started](getting-started.md) if this is a fresh install.

## The tabs

| Page | What the tab is for | Reach for it when |
|---|---|---|
| [Copier](copier.md) | Duplicates existing wallpaper folders N times each. | A playlist needs a wallpaper weighted more heavily, or you want copies to edit independently. |
| [Creator](creator.md) | Turns video clips into Wallpaper Engine projects, **no preview needed** — it renders one from the video. | You have a folder of clips and want them usable as wallpapers now. |
| [Rotator](rotator.md) | Moves folders between a reserve and `myprojects`, with duplicate and history handling, and rebuilds the playlist. | Your library is far larger than one playlist, and you cycle through it. |
| [Tracker](tracker.md) | Reports how far Wallpaper Engine has got through the active playlist. | You want to know when the playlist is finished, so the next rotation is due. |
| [Review](review.md) | Groups the week's new wallpapers by author, and says what each has published since you last looked. | You triage new wallpapers weekly and care who made them. |

## Everything else

| Page | Covers |
|---|---|
| [Getting started](getting-started.md) | Installing, first run, what needs setting up before which tab works. |
| [Configuration](configuration.md) | Every setting, every file written, where each one lives, and what to back up. |
| [Gallery](gallery.md) | The wall of previews the Review tab opens: what the badges mean, and subscribing. |
| [Authors database](authors-database.md) | The file behind Review — what is in it, its backups, restoring one, and coming from MongoDB. |
| [Building](building.md) | Producing a standalone `.exe`. |
| [Development](development.md) | Architecture, the test suite, the theme and the motion rules. |
| [Troubleshooting](troubleshooting.md) | What each failure looks like and which file answers it. |

## What this does not do

One thing writes to Wallpaper Engine: a rotation rebuilding its playlist — see
[the Rotator](rotator.md#wallpaper-engines-playlist). The Tracker reads
`config.json` and the files in `bin/`; it never changes them. Rotation moves
*your* folders between *your* directories. The one action that reaches outside the machine is
subscribing to a workshop item, which is a deliberate click in the
[gallery](gallery.md#subscribing).
