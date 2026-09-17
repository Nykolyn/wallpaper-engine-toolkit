# Creator

**Turns video clips plus previews you already have into Wallpaper Engine projects.**

## What it is for

A Wallpaper Engine video wallpaper is a folder holding three things: the video,
a preview image, and a `project.json` describing them. Making one by hand is
three steps of clerical work; making forty is an afternoon.

The Creator does it in bulk — but it takes **your** preview for each clip.
That is the difference between this tab and
[Auto Creator](auto-creator.md), which renders a preview from the video itself.

## When to reach for it

- You render previews yourself and want exactly those frames.
- A clip's best frame is nowhere near the start, so an automatic preview would
  pick the wrong moment.
- You want a still, designed preview — text, a logo, a composite — rather than
  a loop of the video.

Use [Auto Creator](auto-creator.md) instead when you have clips and no previews
and simply want them usable.

## How to use it

1. **Source** — the folder holding the `.mp4` clips.
2. **Previews** — the folder holding preview images. A preview is matched to a
   clip **by filename**: `sunset.mp4` takes `sunset.jpg` (or `.png`, `.gif`).
3. **Target** — where projects are written. Pre-filled with `myprojects`.
4. **Mode** — `Move` takes the clip out of the source folder; `Copy` leaves it.
5. Press **Scan**. Each clip becomes a card showing its name, whether a preview
   was found, and its status.
6. Build.

A clip with no matching preview is shown as such and skipped rather than built
half-formed.

## What it produces

```
myprojects/<filename>-<random suffix>/
├── <filename>.mp4     # moved or copied from the source
├── <preview>          # your image, copied in
└── project.json       # Wallpaper Engine format
```

The random suffix keeps two clips of the same name from colliding, and matches
what Wallpaper Engine's own editor does.

## How it works

The engine (`app/engines/creator.py`) is the original `wallpapers_creator` core,
reused unchanged: `scan_source` finds the clips and pairs them with previews,
`BuildEngine` writes each project. The same `generate_suffix` and
`build_project_json` are imported by [Auto Creator](auto-creator.md), so both
tabs emit identically shaped output.

Card thumbnails need **Pillow**. Without it the cards still work and still
build — they just have no image on them.

## Settings it keeps

Source, previews, target and mode are remembered in `data/suite.json` under
`creator`. Source and previews start empty on a fresh install; target is
pre-filled from Steam.

## Watch out for

- **`Move` is the default.** It empties the source folder as it builds. That is
  usually what you want for a staging folder, and not at all what you want if
  the source is your clip archive.
- Matching is by **stem**, not by fuzzy search. `sunset final.mp4` will not find
  `sunset.jpg`.
