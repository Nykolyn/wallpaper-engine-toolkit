# Creator

**Turns video clips into Wallpaper Engine projects. No preview needed.**

## What it is for

A Wallpaper Engine video wallpaper is a folder holding three things: the video,
a preview image, and a `project.json` describing them. Making one by hand is
three steps of clerical work; making forty is an afternoon.

The Creator does it in bulk, and it renders the preview from the video itself —
so a folder of clips becomes a folder of working wallpapers in one pass, with
nothing prepared beforehand.

## When to reach for it

- You have clips and want them usable as wallpapers.
- You are bulk-importing: a capture session, a downloaded pack, a folder that
  accumulated over months.
- You just recorded something and want to see it on the desktop.

## How to use it

1. **Source** — the folder of videos. Starts empty; pick it once and it is
   remembered.
2. **Target** — where projects are written. Pre-filled with Wallpaper Engine's
   `myprojects` folder, [detected from Steam](configuration.md#how-the-detected-ones-are-found).
3. **Mode** — `Move` takes each clip out of the source folder; `Copy` leaves it.
4. Press **Scan**. Each video becomes a card showing its name and size.
5. Build. Cards turn blue as their projects are created.

## What it produces

```
myprojects/<filename>-<random suffix>/
├── <filename>.mp4     # moved or copied from the source
├── preview.gif        # rendered from the video
└── project.json       # Wallpaper Engine format
```

The random suffix keeps two clips of the same name from colliding, and matches
what Wallpaper Engine's own editor does. It is epoch, process id, six random
digits and a counter — the counter because the first three collide often enough
to matter when forty projects are built inside one second.

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
is left untouched in the source folder — **a failure never consumes the input.**

ffmpeg is taken from `PATH` if present, otherwise from the bundled
`imageio-ffmpeg` package, so there is nothing to install.

## Tags

Wallpaper Engine writes genre tags into each `project.json` and shows them in
its own browser. This tab lets you set them at two levels.

**For the whole batch** — the `Tags` button next to the video mode. Whatever is
chosen there goes into every project built in that run, and is remembered
between sessions.

**For one clip** — the `tags:` button on its card. A card normally *follows* the
batch and says so, updating live if the batch changes. Once you give a clip its
own tags it keeps them, and the dialog grows a checkbox to hand it back to the
batch.

Those two states are deliberately different:

| The card says | Meaning |
|---|---|
| `tags: Anime, Game` while the batch is `Anime, Game` | following the batch — change the batch and this changes with it |
| `tags: Nature` | this clip's own, whatever the batch says |
| `tags: none` | this clip was deliberately left untagged, and stays untagged even if the batch is given tags |

The dialog offers the 25 tags Wallpaper Engine itself uses, and a free-text
field for anything else — `project.json` accepts any string, so the list is an
offer rather than a rule.

Nothing is tagged by default. An earlier version of this tool wrote a fixed
genre into every wallpaper it built, which was right for the one library it was
written in and wrong everywhere else.

> Where the list came from: 21 distinct tags were counted across 1 529 real
> `project.json` files in a workshop library, and 23 of the 25 appear verbatim
> in Wallpaper Engine's own UI bundle. `Sci-Fi` and `Television` are in real
> projects but not in the bundle's strings, so the list is the union of both.

## Settings it keeps

Source, target, mode and the batch tags live in `data/suite.json` under
`creator`. A clip's own tags belong to the scan, not to the settings — rescanning
starts everything following the batch again.

## Watch out for

- **`Move` is the default**, and it empties the source folder as it builds. That
  is usually what you want for a staging folder, and not at all what you want if
  the source is your clip archive.
- Rendering is the slow part — a real encode per clip, not a frame grab. A large
  batch is minutes, not seconds.
- A clip whose first six seconds are unrepresentative gets an unrepresentative
  preview. Replace `preview.gif` in that project folder by hand; nothing else
  about the project depends on how it was made.
- The scheme colour in `project.json` is still a fixed value inherited from
  the tool this engine grew out of. Wallpaper Engine ignores it for a local
  project; edit the file if you care.

## A note on history

This tab used to be two. The original Creator required a matching preview image
for every clip — `sunset.mp4` took `sunset.jpg` — and skipped any clip without
one. An Auto Creator was added beside it that rendered the preview instead.

The second one turned out to be the whole of the work, because nobody has forty
previews lying around, so the first was removed and the second took its name.
The folder naming and the `project.json` shape are carried over unchanged, so
wallpapers built by either version are indistinguishable.

If you have settings from before the merge, they are moved from the
`autocreator` section of `data/suite.json` to `creator` the first time the app
starts.
