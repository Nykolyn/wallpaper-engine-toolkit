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
5. **Wallpaper Engine** — rebuild the playlist from the new set and start it
   over. On by default; see [below](#wallpaper-engines-playlist).
6. **Check folders** first if it has been a while — see below.
7. Rotate. The confirmation dialog states exactly what will move, and which
   playlist will be rebuilt, before anything does.

All three folders are needed, as full paths — `D:\Wallpapers\reserve`, not
`reserve`. One left empty or relative counts as **not set**: its list on the
Reserve, Transferred or Duplicates tab says `(not set)` and shows nothing, and
neither a rotation nor anything on the Duplicates tab will act on it. An empty
path would otherwise mean the folder the app was started from, which for the
built exe is the install folder — the program itself, and before 3.0.0 your
`data\` too.

The first time, build a playlist in Wallpaper Engine from what is in
`myprojects`. From then on every rotation rebuilds it and starts it over by
itself, and the [Tracker](tracker.md) tells you when it is done.

## Wallpaper Engine's playlist

A rotation used to end with a chore in Wallpaper Engine: open the playlist,
clear out wallpapers that had just gone back to the reserve, add everything
now in `myprojects`, save, apply. The rotation does that part too now.

**Which playlist.** The one made of what the rotation takes back, found by its
contents and never by its name: any playlist — saved, or running on a monitor —
at least half of whose wallpapers are folders about to return to the reserve,
and which holds at least half of those folders. Renaming it changes nothing,
and a saved twin of it is refilled as well. A playlist of workshop
subscriptions never qualifies, nor a few favourites that happen to live in
`myprojects`. If nothing qualifies — before the first playlist is built, say —
the rotation says so and carries on.

**What it becomes.** Every wallpaper now in `myprojects`, `[protected]` ones
included, plus whatever the playlist held from elsewhere (a default wallpaper,
say). Its name and settings — delay, order, transitions — stay as they were. A
monitor that was playing it starts a fresh pass on the new set, as it would
after applying the playlist by hand.

**What happens to Wallpaper Engine.** It is closed before the first folder
moves and started again after the last, a few seconds without wallpapers.
There is no other way in: it reads its playlists from `config.json` as it
starts and writes its own copy back as it exits, and its command line can only
switch to a playlist it already has. So:

1. It is closed the way its tray menu's **Quit** closes it — never killed, so
   it saves what it saves on the way out. That took 0.2 s here.
2. The folders move. Nothing in `myprojects` is held open while they do.
3. The playlist in `config.json` is refilled, and the monitor's pass in
   `bin/playliststate.bin` is reset to the whole new list — otherwise Wallpaper
   Engine resumes the old pass, made of wallpapers that are gone. Other
   monitors' passes are left byte for byte as they were.
4. It is started again with the program and arguments it was running with.

Both files are copied to `data/playlist-refresh/` before they are rewritten,
and replaced whole rather than edited in place. Wallpaper Engine is started
again whatever happens in between — a rotation that fails or is stopped leaves
the playlist as it was, and still brings it back. If it will not close, the
rotation goes ahead without touching it, and the summary says to rebuild the
playlist by hand that once.

Untick the box to leave Wallpaper Engine alone entirely; the rotation then only
moves folders, as it always did.

## Protected folders

Folders in `myprojects` whose name starts with **`[protected]`**
(case-insensitive) are excluded from rotation entirely. They are not returned to
the reserve, not treated as duplicates, and stay across runs.

Use it for anything that should be in every playlist: wallpapers you actually
like, a set you are working on, or [Copier](copier.md) copies that exist to
weight the current playlist.

The rotation log and the confirmation dialog both report how many were skipped,
so a protected folder is never silently invisible.

## Duplicates

A folder coming back from `myprojects` whose name is already in the reserve is
not merged into it. It goes to the duplicates folder instead, and the run's
history lists it. The **Duplicates** tab lists what is in that folder and offers
two things to do with it:

- **Delete** the selected folders, or all of them. Permanently: nothing goes to
  the Recycle Bin.
- **Move & replace → reserve**: each folder replaces the reserve's folder of the
  same name, which is deleted first.

The question before either names the folders involved. The delete buttons stay
off until the duplicates folder is set, and the move buttons until the reserve
is set too. Moving back refuses outright when the duplicates folder *is* the
reserve, since replacing would delete the very folder being moved.

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

Both are in the [data folder](configuration.md#where-things-live) with
everything else. (Until 3.0.0 a source run kept them apart, in
`app/engines/data/`, the way the standalone tool did.)

- `data/config.json` — the five settings above.
- `data/history.json` — one record per run: which folders moved,
  which were duplicates, how many came back, what failed.

History is not only a log. It is what keeps a rotation from picking what the
last ones already showed, and the [Tracker](tracker.md) reads it to date a cycle:
the newest run sharing at least half its folders with a playlist *is* that
playlist's starting moment, which is the difference between an exact cycle start
and a guess. The Tracker never writes to it.

Wallpaper Engine's `config.json` and `bin/playliststate.bin`, as they were
before the last rotation rewrote them, are in `data/playlist-refresh/`.

### The history is not lost quietly

It was, once: a build emptied the folder it was in, nothing said so, and the
next rotation began a history of one run. So now:

- Every save writes the whole file under a temporary name that then replaces
  the old one — a save cut short leaves the previous version — and a snapshot
  to `data/history_backup/`. The newest 30 are kept.
- A `history.json` that is **missing** at start-up is put back from the newest
  snapshot that reads.
- One that **cannot be read** is renamed `history.unreadable-<time>.json`,
  never written over, and put back from a snapshot the same way.
- A run with a key this version does not know is read anyway, and the key is
  written back as it was. (An unknown key used to make the whole file
  unreadable, and the next rotation saved an empty history over it.)
- Whichever happened, the History tab says so above the list, and so does the
  question before the next rotation.
- If the file can be neither read nor renamed, **no rotation starts** — its
  record would replace the file — until it reads or is moved away.

`config.json` is kept the same way: one that cannot be read is renamed
`config.unreadable-<time>.json` before the defaults are written.

To start the history over on purpose, delete `history.json` *and*
`history_backup/`; with a snapshot left, the history comes back.

## Watch out for

- **Deletion in the reserve check is permanent.** Nothing goes to the Recycle
  Bin. Read the dialog. The same goes for deleting on the Duplicates tab.
- **Rotation moves, it does not copy.** A folder is in exactly one of the two
  places.
- **Wallpaper Engine goes away for a few seconds** during a rotation that
  rebuilds its playlist, and the monitor playing that playlist starts a fresh
  pass. Rotate when the pass is done, which is when the Tracker says to
  anyway.
- A playlist the rotation did not rebuild outlives its files: Wallpaper Engine
  goes on listing folders that are now back in the reserve. That is expected,
  and the Tracker [accounts for it](tracker.md#what-neither-can-see).
