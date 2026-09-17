# Auto Creator

**Turns video clips into Wallpaper Engine projects with no preview needed.**

## What it is for

[Creator](creator.md) needs a preview image per clip. Most of the time you do
not have one and do not want to make forty. This tab renders the preview from
the video itself, so a folder of clips becomes a folder of working wallpapers
in one pass.

## When to reach for it

- You have clips and no previews.
- You are bulk-importing — a capture session, a downloaded pack, a folder that
  accumulated over months.
- The first few seconds of each clip are representative, which for looping
  footage they usually are.

Use [Creator](creator.md) instead when the preview matters and you have made
one.

## How to use it

1. **Source** — the folder of videos. Starts empty; pick it once and it is
   remembered.
2. **Target** — where projects are written. Pre-filled with `myprojects`.
3. **Mode** — `Move` or `Copy`.
4. Scan, then build.

## What it produces

```
myprojects/<filename>-<random suffix>/
├── <filename>.mp4     # moved or copied from the source
├── preview.gif        # rendered from the video
└── project.json       # Wallpaper Engine format
```

Folder naming and `project.json` come from the [Creator](creator.md) engine, so
the output is identical in shape to what that tab produces.

## How the preview is rendered

**480×480 square (1:1), 15 fps, 5 seconds starting 1 second in.**

Each of those is a decision:

- **Square**, because Wallpaper Engine's own grid is square and a 16:9 preview
  is letterboxed into it. The square comes from centre-cropping the larger
  dimension and *then* scaling, so the image is never stretched and never
  padded.
- **One second in**, because clips commonly open on black or fade in, and a
  preview of black is no preview. Clips shorter than about two seconds are
  captured from the start instead.
- **Two-pass ffmpeg** (`palettegen` then `paletteuse`). A single-pass GIF encode
  quantises to a generic palette and bands heavily on gradients — which is most
  footage worth using as a wallpaper.

If the GIF fails, it falls back to a single still frame (`preview.jpg`). If that
also fails the item is skipped, its half-built folder is removed, and the video
is left untouched in the source folder — a failure never consumes the input.

ffmpeg is taken from `PATH` if present, otherwise from the bundled
`imageio-ffmpeg` package, so there is nothing to install.

## Settings it keeps

Source, target and mode live in `data/suite.json` under `autocreator`.

## Watch out for

- **`Move` is the default**, and it empties the source folder as it builds.
- Rendering is the slow part — it is a real encode per clip, not a frame grab.
  A large batch is minutes, not seconds.
- A clip whose first six seconds are unrepresentative gets an unrepresentative
  preview. That is the trade for not making previews by hand; rebuild those few
  through [Creator](creator.md).
