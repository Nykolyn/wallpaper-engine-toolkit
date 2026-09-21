# Review

**The week's new wallpapers, grouped by who made them.**

## What it is for

Every day some wallpapers get put into Wallpaper Engine's **`new`** folder. At
the end of the week they need going through — and the thing worth going through
is not the wallpapers but their **authors**. Someone who made one wallpaper you
liked has probably made others.

By hand that means: click each wallpaper, note who made it, search a database of
39 000 authors, create them or read off when you last visited, open their
workshop, find the first wallpaper published after that date, and browse
forward. Measured on one real week: 39 wallpapers, **26 distinct authors**,
seven of whom had never been added.

This tab is that, done for you.

## When to reach for it

- Weekly, on whatever you have put in the `new` folder.
- When you want to know which authors are worth following.
- When you want to catch up on one author's back catalogue without scrolling
  past what you already have.
- When you want to go through more than one folder, or the whole library at
  once. See [What gets reviewed](#what-gets-reviewed).

## What you need first

Nothing. Press **Scan**.

- The **authors database** is a file, `data/authors.sqlite`, made the first
  time the tab needs it and backed up after every change. See
  [Authors database](authors-database.md).
- A **Steam Web API key** is optional, and worth having. Without one the tab
  works, with less — see [what the key is for](#what-a-steam-web-api-key-is-for).
  It is free from
  [steamcommunity.com/dev/apikey](https://steamcommunity.com/dev/apikey) and
  goes in **Steam key…**, which has a **Test** button and stores the key
  DPAPI-encrypted.

## How to use it

1. Put wallpapers in Wallpaper Engine's `new` folder during the week.
2. Open the tab, pick what to **look at**, and press **Scan**. The left list
   fills with one card per author: their name, whether the database has heard
   of them, and a badge counting what they have published since your last
   visit that you are **not** subscribed to.
3. Click a card to open [their gallery](gallery.md) — everything of theirs not
   currently subscribed, newest first, as previews.
4. Subscribe to what you want.
5. Press **Update the database**. Authors that were new are created; the rest
   have their visit date moved on. The change is listed before it is written,
   written in one go — all of it or none — and backed up straight after.

### What gets reviewed

The **Look at** box holds Wallpaper Engine's own folders, read out of its
`config.json` every time the tab opens, so a folder you made this morning is
in the list this afternoon. Under them are three that cross folders:

| Choice | What it reviews |
|---|---|
| a folder by name | what that folder holds — `new`, the weekly habit |
| **All folders** | everything in any of them, each wallpaper once |
| **Not in any folder** | what you have that was never filed anywhere |
| **Everything you have** | both together — the whole library |

The last two exist because a folder is not the library. On the machine this
was built against the three folders remember 16 629 ids between them; a
wallpaper subscribed to last night and not yet filed is in none of them, and
before this it could not be reached at all.

Whatever you choose, only wallpapers Wallpaper Engine can actually show are
reviewed — see [below](#what-is-in-the-folder-is-not-what-the-folder-remembers).

### What is in the folder is not what the folder remembers

Unsubscribing from a wallpaper does not take it out of a folder — its id stays
in `config.json` for ever. A folder used as a queue for years accumulates
entries with nothing behind them. On one real folder: **2 155 ids remembered,
39 still on disk**.

Wallpaper Engine shows the 39, because those are the ones it has files for, and
so does this tab. The difference is stated rather than hidden. Reading the
folder literally would mean re-reviewing every wallpaper ever put aside and
since deleted, turning a week's 26 authors into 453.

### Whether you have had a wallpaper before is asked separately

A card in a gallery says **was yours** when this machine has had that wallpaper
at some point, and **new to you** when it never has. Two records answer that,
and one of them used to be missing:

| Record | What it knows | Ids here |
|---|---|---|
| `project.json` in the local libraries | every workshop wallpaper you kept a copy of | 4 412 |
| Wallpaper Engine's own folders | every id ever put in one, for ever | 16 629 |

The second is the larger by far. A wallpaper subscribed to and later dropped
without ever being copied leaves nothing in the libraries — but its id stays in
the folder. **14 915** ids on this machine are in that state, and only 425 of
them were in the copies index, so before this they came back around looking as
though they had never been seen.

The answer costs a parse of a 2.35 MB `config.json`, so it is worked out **when
you open an author**, on a thread, and shared by every gallery afterwards —
0.02 s for the first, nothing at all for the rest. Pressing **Count what is
new** does not ask it: that is four hundred authors and nobody is looking at a
gallery yet. Until it has been asked, a card claims neither answer.

### The visit date moves to the newest wallpaper the review covered

Not to "now". An author who publishes something while the window is open would
otherwise be skipped for good, and the entire purpose of the date is that
nothing is skipped.

## How it works

### Two phases, because they cost differently

**Naming the authors takes seconds.** The wallpapers are described in batches of
200, a hundred profiles come back per request, and the authors database is a
local file — 616 authors looked up by both of their keys in 11 ms.

**Counting what each has published since is a request apiece.** Opening one
author's whole back catalogue is a dozen. So the list appears first and fills in
behind itself, and a gallery is fetched only when its card is clicked.

Those requests go out through the Steam client's own pool rather than one after
another — measured at **17.9 s for the 85 authors** of an ordinary week. The
workshop folder is read once for the whole count instead of once per author,
and what you used to own is not asked at all.

### What a Steam Web API key is for

Without a key the toolkit sees Steam the way a signed-out visitor does. The
obvious way to list an author's work is their public workshop page, and it is a
trap. Measured against one real library, a signed-out listing does not show an
author's work — it shows a fraction of it:

| author | public listing | owned here | of those, unlisted |
|---|---|---|---|
| A | **0** | 174 | 174 |
| B | 191 | 58 | 24 |
| C | 278 | 49 | 47 |

Mature and questionable content is invisible to a signed-out client, and it was
**43%** of that library. The Web API has no such gate, so with a key everything
is read through it and the lists are whole.

The key is still optional, because the tab is useful without it — but going
without is said, not discovered:

- A **banner** under the toolbar says what is missing, with **Add a key…** next
  to it. **Hide** puts it away; the two warnings below stay.
- An author counted without a key says so under their name: *list incomplete:
  read without a Steam key*.
- **Update the database** warns, and defaults to **Cancel**, when a visit date
  would be written from such a list. That is the one consequence adding a key
  later does not undo: the date moves past the mature wallpapers that were left
  out, and they are not offered again.

Names are the other difference. With a key a hundred profiles are one request,
so every scan fetches them fresh. Without one each is a page of its own, 0.4 s
apart — three minutes for an ordinary week — so a keyless scan takes names from
the cache, which is at most two weeks old, and fetches only the ones it lacks.

A key Steam refuses stops the scan with a sentence saying so, rather than
quietly counting less.

### Where the time goes, measured rather than guessed

Identifying the authors is under two seconds for a folder of 80 wallpapers.
Opening the database used to be 1.3 s of that, a round trip to a server; the
local file opens in 5 ms.

Counting what each has published since is the phase with the waiting in it, and
it is latency and nothing else: one request per author, 641 ms median, all of it
the round trip. Done one author at a time that was **44.2 s for fifty authors**
with the machine idle throughout. They go through the Steam client's own pool
instead, where the per-host throttle still paces the requests — so Steam is
asked no harder, the waiting simply overlaps:

| workers | time for 52 requests |
|---|---|
| 1 | 44.2 s |
| 4 | 16.4 s |
| **8** | **11.3 s** |
| 12 | 11.1 s |

Eight is where the gain stops, and that is the default. At that point the
throttle is the whole of what is left (52 requests x 0.2 s = 10.4 s).

Opening one author's gallery fetches their whole catalogue, which for a prolific
author is thirteen pages. Those used to be read one after another, because a
*shallow* fetch reads them that way on purpose — each page decides whether the
next is needed at all, which is what turns thirteen requests into one. A full
fetch has no such decision, and the first response already says how many pages
there are, so the rest go out together: **17.1 s to 4.3 s** for an author with
1 271 wallpapers, 10.6 s to 2.2 s for one with 380.

`IPublishedFileService/GetUserFiles` returns items ordered by `time_updated`,
newest first — measured, not documented, and re-checked by the test script — and
an item is never updated before it was created. So paging can stop at the first
page whose contents were all last touched before the visit date: for an author
with 1 204 wallpapers, one request instead of thirteen.

## Files

| File | What it holds |
|---|---|
| `data/authors.sqlite` | the authors database: one row per author, with the visit date |
| `data/secrets.json` | the Steam API key, DPAPI-encrypted |
| `data/library.json` | workshop ids found in the local libraries, so the four-minute walk is paid once |
| Wallpaper Engine's `config.json` | read, never written: its folders are both the review queue and the record of what you have had |
| `data/thumbs/` | preview images |
| `data/steam_cache.sqlite` | what Steam has already been asked |
| `data/authors_backup/` | a snapshot after every change, and `journal.jsonl` of every change — see [Backups](authors-database.md#backups) |

## See also

- [Gallery](gallery.md) — the wall of previews, what each badge means, and
  subscribing.
- [Authors database](authors-database.md) — the file, its backups, and
  restoring one.
