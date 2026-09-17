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

## What you need first

Two credentials, set in the tab's own dialog and stored DPAPI-encrypted:

1. A **Steam Web API key** — free from
   [steamcommunity.com/dev/apikey](https://steamcommunity.com/dev/apikey).
   See [why it is not optional](#why-it-needs-a-steam-web-api-key).
2. A **MongoDB connection string** for the authors database. See
   [Authors database](authors-database.md).

Both have a **Test** button, and those matter more than they look: a wrong key
and an unreachable database fail identically from the tab's point of view —
"nothing happened" — and each takes a slow round trip to find out. Testing them
one at a time, with the answer in a sentence, turns a mystery into a sentence
about which of the two is wrong.

## How to use it

1. Put wallpapers in Wallpaper Engine's `new` folder during the week.
2. Open the tab. The left list fills with one card per author: their name,
   whether the database has heard of them, and a badge counting what they have
   published since your last visit that you are **not** subscribed to.
3. Click a card to open [their gallery](gallery.md) — everything of theirs not
   currently subscribed, newest first, as previews.
4. Subscribe to what you want.
5. Press **Update the database**. Authors that were new are created; the rest
   have their visit date moved on.

### What is in the folder is not what the folder remembers

Unsubscribing from a wallpaper does not take it out of a folder — its id stays
in `config.json` for ever. A folder used as a queue for years accumulates
entries with nothing behind them. On one real folder: **2 155 ids remembered,
39 still on disk**.

Wallpaper Engine shows the 39, because those are the ones it has files for, and
so does this tab. The difference is stated rather than hidden. Reading the
folder literally would mean re-reviewing every wallpaper ever put aside and
since deleted, turning a week's 26 authors into 453.

### The visit date moves to the newest wallpaper the review covered

Not to "now". An author who publishes something while the window is open would
otherwise be skipped for good, and the entire purpose of the date is that
nothing is skipped.

## How it works

### Two phases, because they cost differently

**Naming the authors takes seconds.** The wallpapers are described in batches of
200, a hundred profiles come back per request, and every database record is
fetched in one query — 616 keys in 0.7 s.

**Counting what each has published since is a request apiece.** Opening one
author's whole back catalogue is a dozen. So the list appears first and fills in
behind itself, and a gallery is fetched only when its card is clicked.

### Why it needs a Steam Web API key

The obvious way to list an author's work is their public workshop page, and it
is a trap. Measured against one real library, a signed-out listing does not show
an author's work — it shows a fraction of it:

| author | public listing | owned here | of those, unlisted |
|---|---|---|---|
| A | **0** | 174 | 174 |
| B | 191 | 58 | 24 |
| C | 278 | 49 | 47 |

Mature and questionable content is invisible to a signed-out client, and it was
**43%** of that library. The Web API has no such gate, so with a key everything
is read through it and the lists are whole.

### Where the time goes, measured rather than guessed

Identifying the authors is 3.1 s for a folder of 80 wallpapers, of which 1.3 s
is opening the database.

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

### Reaching MongoDB through a VPN

A `mongodb+srv://` string is not a host. It tells the driver to look up an SRV
record for the cluster's servers and a TXT record for its default options, and
pymongo does that with **dnspython**, which reads the configured nameservers and
queries them itself over UDP.

Behind a VPN that fails. The nameservers a VPN installs answer the Windows DNS
Client but not a program asking them directly, so every lookup burns its full
20-second timeout and the tab reports a database it cannot reach while the
database is reachable the whole time. `Resolve-DnsName` and
`socket.getaddrinfo` answer the same names in 0.06 s.

So `app/engines/mongo_srv.py` does the lookup with `DnsQuery_W` from
`dnsapi.dll` — the resolver Windows uses itself — and folds the result into an
ordinary `mongodb://` string with the servers written into it, which pymongo
then resolves with `getaddrinfo`: the same path the Steam client and everything
else already take.

Credentials are copied verbatim (they are percent-encoded and must not be
touched), an option written by hand beats the same option from TXT, TLS is
stated explicitly because `+srv` implies it and `mongodb://` does not, and a
server outside the cluster's own domain is refused. If any of that cannot be
done — another platform, no SRV record — the original string is handed over
untouched and pymongo does it its own way.

Connecting went from failing after 20.4 s to succeeding in 1.6 s.

## Files

| File | What it holds |
|---|---|
| `data/secrets.json` | the API key and the connection string, DPAPI-encrypted |
| `data/library.json` | workshop ids found in the local libraries, so the four-minute walk is paid once |
| `data/thumbs/` | preview images |
| `data/steam_cache.sqlite` | what Steam has already been asked |
| `data/authors_backup/` | plans and per-change backups before the database is written |

## See also

- [Gallery](gallery.md) — the wall of previews, what each badge means, and
  subscribing.
- [Authors database](authors-database.md) — the collection itself.
