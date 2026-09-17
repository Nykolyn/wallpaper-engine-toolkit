# Rotator

**Moves wallpaper folders between a reserve and `myprojects`, both ways.**

## What it is for

Wallpaper Engine shows what is in `myprojects`. A library of thirty thousand
wallpapers cannot be in `myprojects` — the browser becomes unusable and a
playlist built from it is meaningless.

So the library lives in a **reserve** folder, and a rotation carries a slice of
it into `myprojects`, builds a playlist from that slice, and later carries it
back to make room for the next one. The Rotator is that movement, with the
bookkeeping that makes it repeatable: what went where, what came back, what was
a duplicate, and what should never move at all.

## When to reach for it

- Your library is much larger than one playlist.
- You want a fresh set of wallpapers periodically without choosing them.
- You have just been told by the [Tracker](tracker.md) that the current playlist
  has been shown end to end.

## How to use it

1. **Source** — the reserve. The library. Starts empty; pick it once.
2. **Destination** — `myprojects`. Pre-filled from Steam.
3. **Duplicates** — where folders that turn out to be duplicates are moved.
   Starts empty.
4. **Count** — how many folders to bring across. Default 1000.
5. **Check folders** first if it has been a while — see below.
6. Rotate. The confirmation dialog states exactly what will move before
   anything does.

Afterwards, build a playlist in Wallpaper Engine from what is now in
`myprojects`, and let the [Tracker](tracker.md) tell you when it is done.

## Protected folders

Folders in `myprojects` whose name starts with **`[protected]`**
(case-insensitive) are excluded from rotation entirely. They are not returned to
the reserve, not treated as duplicates, and stay across runs.

Use it for anything that should be in every playlist: wallpapers you actually
like, a set you are working on, or [Copier](copier.md) copies that exist to
weight the current playlist.

The rotation log and the confirmation dialog both report how many were skipped,
so a protected folder is never silently invisible.

## The reserve check

Wallpaper Engine identifies a wallpaper by its `project.json`. A folder without
one can never be listed or shown — so rotating it in quietly costs a slot. A run
of 200 produces a playlist of 198 and nothing says why.

Three shapes turn up in a real reserve:

- folders holding nothing but Wallpaper Engine's own compiled shader cache, left
  behind when the wallpaper was removed;
- folders left empty;
- folders whose manifest is gone but whose **video is still there**.

Measured on one real library: 389 of 33 423.

Every rotation therefore begins with a check of both libraries, and **Check
folders** runs the same thing on its own. It is deliberately not a background
chore. The scan only *reads*; everything it finds goes into a confirmation
dialog listing each folder, where it lives, why it cannot be used and what is
inside it. Expand a row to see the actual files, or open it in Explorer. Only
what is still ticked when the button is pressed is deleted, and **deletion is
permanent**.

The two obviously-dead shapes are ticked by default. A folder that still holds
media is **not**: that is a wallpaper which lost its manifest and may be worth
repairing, so it is shown apart, in the warning colour, and left for you to
decide about.

The whole scan takes about three seconds over 33 000 folders, because it reads
each directory with `scandir` rather than asking whether `project.json` exists —
seventeen times faster on a negative answer (0.9 s against 16.8 s).

## Settings and history

The Rotator keeps its own files, separate from the rest of the app, exactly as
the standalone tool did:

- `app/engines/data/config.json` — the four settings above.
- `app/engines/data/history.json` — one record per run: which folders moved,
  which were duplicates, how many came back, what failed.

History is not only a log. The [Tracker](tracker.md) reads it to date a cycle:
the newest run sharing at least half its folders with a playlist *is* that
playlist's starting moment, which is the difference between an exact cycle start
and a guess. The Tracker never writes to it.

## Watch out for

- **Deletion in the reserve check is permanent.** Nothing goes to the Recycle
  Bin. Read the dialog.
- **Rotation moves, it does not copy.** A folder is in exactly one of the two
  places.
- A playlist outlives its files: after a rotation Wallpaper Engine goes on
  listing folders that are now back in the reserve. That is expected, and the
  Tracker [accounts for it](tracker.md#what-the-probe-cannot-see).
