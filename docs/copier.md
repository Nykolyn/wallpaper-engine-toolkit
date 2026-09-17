# Copier

**Mass-duplicates Wallpaper Engine folders, N copies each.**

## What it is for

Wallpaper Engine plays a playlist by picking from its entries. Every entry is
equally likely, which means a wallpaper you would rather see often appears
exactly as often as one you are indifferent to. There is no weight setting.

Duplicating a folder is the weight setting. Three copies of a wallpaper in a
playlist of two hundred is three chances instead of one.

The other use is editorial: a copy is a separate project, so you can change its
`project.json`, crop it differently, or set different properties without
touching the original.

## When to reach for it

- A handful of wallpapers should come up more often than the rest.
- You want to fork a wallpaper and edit the copy.
- You need a bulk test set — twenty copies of one folder to see how a playlist
  of that size behaves.

## How to use it

1. Set **Destination** — where the copies are written. It arrives pre-filled
   with Wallpaper Engine's `myprojects` folder.
2. Add source folders. Drop them onto the list, or use the button. Each row is
   one folder plus a count.
3. Set the count per row. The default is 3.
4. Press the accented button to start.

Copies are named after the original with a `_copy1`, `_copy2`, … suffix, so
they sort next to it and are obvious to remove later.

The log panel reports each copy as it lands, and the progress bar tracks the
whole job rather than the current folder.

## How it works

The engine (`app/engines/copier.py`) is the original `wallpaper_copier` code,
reused unchanged. It runs on a background thread and reports through callbacks;
`app/workers.py` bridges those callbacks onto Qt signals so the window updates
safely.

A copy is a plain recursive directory copy. Nothing rewrites `project.json`, so
a copy carries the original's title — Wallpaper Engine shows several entries
with the same name, which is what you want when the point is weighting, and
what you edit when the point is forking.

## Settings it keeps

The destination is remembered in `data/suite.json` under `copier`. The per-row
counts are not remembered — they belong to the job, not the tab.

## Watch out for

- **Copies are real folders.** Twenty copies of a 400 MB scene wallpaper is
  8 GB. The Rotator's [reserve check](rotator.md#the-reserve-check) will not
  flag them, because they are valid wallpapers.
- **A rotation treats copies as ordinary folders**, so they can be carried back
  to the reserve like anything else. If copies exist to weight a *current*
  playlist, prefix them with `[protected]` — see
  [protected folders](rotator.md#protected-folders).
