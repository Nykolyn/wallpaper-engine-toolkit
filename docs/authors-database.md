# Authors database

**The file behind the [Review](review.md) tab: one row per author, and when you
last looked at their work.**

Only the Review tab uses it. Every other tab in this app works without it.

## What it is for

Review's whole premise is that the interesting unit is the *author*, not the
wallpaper — and that means remembering, per author, **when you last looked at
their work**. That date is what turns "here are 1 271 wallpapers" into "here are
the nine published since you were last here".

## Where it is

`data/authors.sqlite`, in the [data folder](configuration.md#where-things-live)
with the other things the toolkit keeps — `%LOCALAPPDATA%\WallpaperEngineToolkit`
in a built copy, the project folder's `data\` when run from source.

There is nothing to set up. The file is made the first time the Review tab
needs it, and **Authors database…** in the tab says how many authors it holds,
where the file is, and what backups there are.

It is a SQLite database: one file, no server, and read by the Python standard
library, so the toolkit ships nothing extra for it. SQLite rather than a JSON
file because it already does the three things a JSON file would need written
by hand — survive a crash in the middle of a write, change one row without
rewriting forty thousand, and find `O0P` when asked for `o0p` through an index.

## What is in it

One table, `authors`, with only what the Review uses:

| Column | Holds |
|---|---|
| `key` | the author's steamID64 — or, for the few nothing could identify, the vanity name they were filed under. Compared without regard to case. |
| `name` | what they were called when last seen |
| `added` | when they were first found, in UTC: `2026-09-21T10:15:30Z` |
| `visited` | how far through their work you are, in UTC; empty means never |

Times carry their zone. UTC without the marker is one misreading away from a
silent three-hour error in the one date that decides what is shown.

A vanity name is case-blind on Steam and may only contain ASCII letters,
digits, `_` and `-`, which is exactly what SQLite's `NOCASE` folds — so `O0P`
and `o0p` are one key, and one author.

## Nothing is written without saying what it would write

Every change is **planned first** and shown as a line of English —
`create New Person (76561199999999999)` — before anything is written. You
approve the plan, not the intention.

Then it is written **in one transaction**: all of it, or — if any part fails —
none of it. Every update and delete must reach exactly the row it was planned
against; one that reaches nothing stops the whole write instead of reporting a
success that changed nothing.

## Backups

After every change, two things are written to `data/authors_backup/`:

- **A snapshot** — the whole table as gzipped JSON, about 1 MB for 39 000
  authors, named for when it was taken and how many authors it holds:
  `authors-20260921-210911-38989.json.gz`. It is written to a temporary name,
  flushed to disk, renamed into place, and read back and counted before the
  write counts as backed up. Unzipped, it is plain JSON with one author per
  line — readable without this app.
- **A journal line** in `journal.jsonl` — every row the change touched, as it
  was and as it became, so one mistaken change can be undone without undoing
  the ones after it.

Snapshots are pruned so the folder stays a few dozen files: **the last ten**,
then **one a day for a week**, **one a week for a month** and **one a month for
a year**. Only files named like a snapshot are ever touched.

### A second copy somewhere else

A backup on the same disk as the database survives mistakes, not the disk
failing. In **Authors database…**, **Second copy in** picks a folder — another
drive, or one OneDrive or Dropbox syncs — and every snapshot is copied there as
well, verified, and pruned by the same rules.

It is off until you choose one, and the dialog says what that risks. If the
folder cannot be reached when a change is written — an unplugged drive — the
change still happens and is still backed up in `data/`; the tab says the copy
was not made, and the next change makes it.

### Restoring

**Authors database…** lists every snapshot in both places, newest first. Pick
one and press **Restore selected…**: the dialog says how many authors are there
now and how many the backup holds, and asks. The current state is snapshotted
first, so a restore is undone the same way it is done.

If the database file is ever damaged, the tab says so when it opens it and
offers the backups straight away. The damaged file is set aside as
`authors.sqlite.damaged1`, never deleted.

## Why an author can have two keys

Steam offers two forms of profile URL: an **account number**
(`/profiles/7656119…`) and a **vanity name** (`/id/someone`). The account number
is the identity — it never changes. A vanity name can be changed, and the old
one is then *released* for somebody else to claim.

So authors are filed under their account number. The exceptions are authors
added long ago under a vanity name that nothing can tie to an account any more
— 821 of 38 989 in the database this was built against. They stay filed under
that name.

That is why a lookup asks for both of an author's keys, the account number and
the vanity name Steam reports today, and why a card that finds two records says
**duplicated** rather than picking one.
