# Gallery

**The wall of previews the [Review](review.md) tab opens when you click an author.**

## What it is for

Everything one author has made that you are not currently subscribed to, newest
first, as previews. It is where the decision actually gets made: you are looking
at pictures, not at a list of titles.

## What a card knows about a wallpaper

Across the top of the picture, where they read before it does:

| Mark | Meaning |
|---|---|
| **was yours** | This machine has had it before. |
| **new to you** | It never has — you have never subscribed to it or kept a copy. |
| **queued** | It is one of the wallpapers you put in a folder. |
| **subscribed** | Taken. |

**"was yours" is the one that saves the most work.** Wallpapers come back
around, so they are shown rather than hidden — but re-reviewing them blind is
exactly the work this tab removes. For one author, 11 of 29 were in this state.

Two records answer it: the `project.json` of every copy kept across the
Rotator's libraries, where a workshop wallpaper names itself with a
`workshopid`, and **every id Wallpaper Engine's own folders remember**. The
second is far the larger — a wallpaper dropped without ever being copied leaves
nothing in the libraries, and 14 915 ids here are exactly that. The walk behind
the first is 33 882 folders and four minutes cold, so it is cached in
`data/library.json` by folder name and modification time.

That answer arrives a moment after the cards do, on a thread. Until it does, a
card claims neither mark: **"new" used to be painted from "published since your
last visit"**, which is true of every card in the gallery, so a wallpaper
downloaded and deleted twice still called itself new.

**"subscribed" dims the card where it stands** rather than removing it. A tile
disappearing under the cursor loses your place in a wall of four hundred. The
marks stay bright on top of the dimming — dimming the word "subscribed" along
with the picture dims the reason the picture is dim. The badge on the author
card falls by itself, because it counts what is still on offer.

## What it is, and what it costs

Under the title, where the decision is actually made:

| Mark | Meaning |
|---|---|
| a coloured kind | **Scene**, **Video**, **Web**, **Application** or **Preset**, each its own hue and glyph |
| a plain size | the download, as Steam reports it |
| **an amber size** | a gigabyte or more |
| a date | when it was published |

A scene and a video are different decisions — different cost to download,
different behaviour on a monitor — and a 6 MB scene and a 1.4 GB video are more
different still. All three used to be one grey line of prose under the title,
read last if at all. The kind carries its own colour and the gigabyte carries
the warning colour, so both land in the same glance as the picture.

**A selected card is marked by its border, not by its fill.** It used to turn
solid blue while its title and its facts kept the greys they were chosen for,
which left the size and the date all but invisible on the one card you were
looking at.

## Subscribing

Wallpaper Engine's own Subscribe button is not a web request. Taking its install
apart: `bin/steam_api64.dll` is Valve's Steamworks redistributable and exports
`SteamAPI_ISteamUGC_SubscribeItem`; `wallpaperui.exe` — the browser, not the
renderer — loads it and asks for `STEAMUGC_INTERFACE_VERSION020`. A subscription
is a local call into the running Steam client, which performs it for whoever is
signed in.

So the tab offers two routes, switched in its toolbar:

- **Steam directly** — the same call, from this window. One click and the
  wallpaper is on its way. The process declares app id 431960 while the API is
  open, so it is opened for one action and closed again.

  Valve does not sanction using an app id you do not own. This is not a
  licensing or safety problem — the account, the machine and the copy of
  Wallpaper Engine are all yours, and workshop items are free — but it is
  **unsupported**, and a Steam update could close it.

- **Opening Steam's page** — `steam://url/CommunityFilePage/<id>`, where
  Subscribe is one click. Nothing unsupported, one more click.

Either way the tab watches the workshop folder and marks a wallpaper as taken
when it arrives, so subscribing in Wallpaper Engine itself is noticed too.

Steam must be running for either route.

## How it works

### Previews

Fetched on a thread pool, cached in `data/thumbs`, and drawn into a virtualised
grid — 1 200 cards is a `QListView` with uniform item sizes, not 1 200 widgets.

Nearly half are **animated**: Steam serves an animated wallpaper's preview as a
real GIF, so `QMovie` plays it.

Their still frame is deliberately not frame zero. These previews commonly fade
in from black, and a quarter of them were still pure black twelve frames in — so
frames are read until one is bright enough to be a picture, on the worker thread
rather than in front of the window.

### It animates, but not thirty at once

Steam's own browser animates its whole grid and so does Wallpaper Engine, so a
wall that only moves under the cursor reads as broken.

Building a decoder for all thirty cards of a page does not work, though, and the
way it failed was the worst kind. The previews arrive in bursts of six from the
download threads; each arrival was handled on the GUI thread by sweeping the
whole page and starting a player on the spot; and on one author's gallery the
window simply stopped answering — no previews, no repaints, nothing. Short of
that it was merely bad: twenty-one players came to 178 frames a second, each
frame scaled and each asking for its row to be repainted, leaving the GUI thread
334 ms behind.

Three things fixed it:

- An arrival now only **notes** that the players are worth reconciling, and a
  timer does that once per burst rather than once per preview.
- At most **eight** animate at a time — the ones nearest the top of the view,
  where the eye is. The rest keep their still frame.
- Frames are collected and repainted on one **50 ms clock**, so repainting costs
  what the page costs rather than the sum of every player's frame rate.

The same gallery that froze now opens and stays open, with the GUI thread never
more than **76 ms** behind.

### And never at the window's expense

That was not the end of it. On 18 September the window froze again, during
**Count what is new**, a few authors into clicking through the results — and
the cause turned out to be one level down, in how Python and Qt share a process.

PySide gives up Python's global lock (the GIL) around every call into Qt and
takes it back afterwards. Taking it back is free while nothing else wants it.
While another thread is busy in Python — parsing a page of Steam's answer,
building an author's item list — it is a wait of up to the interpreter's switch
interval, 5 ms by default, *per call*. A repaint makes hundreds of calls. And the
animation made a few hundred more every second: each frame of each player was
scaled and turned into a pixmap in Python, eight players at 25 frames a second.

Measured on one real page of thirty GIF previews, eight of them playing, with
threads busy in Python beside it:

| | busy threads | timer ticks out of 400 | worst delay |
|---|---|---|---|
| before, 5 ms switch interval | 1 | 2 | 4.1 s |
| before, 5 ms switch interval | 2 | **0** | the window never answered |
| now, 5 ms switch interval | 1 | 257 | 0.4 s, and the animation rested |
| now, 0.5 ms switch interval | 1 | 399 | 4 ms |
| now, 0.5 ms switch interval | 4 | 398 | 23 ms |

What changed:

- **No Python runs per frame.** `QMovie` is given the size the still was fitted
  to and scales its own frames; the delegate paints the movie's current frame;
  and one 50 ms clock repaints only the cards whose frame number moved on.
- **The animation rests when the window is behind.** The same clock notices
  when it runs late — twice running by more than a quarter of a second — pauses
  every player, and resumes them after two seconds on time. Whatever else is
  holding the window up, the animation is never what tips it over.
- **The switch interval is 0.5 ms**, set once at start-up, so a busy thread
  hands the GIL back ten times sooner.
- **Only the page on screen is kept in memory.** Every preview of every page
  ever shown used to stay: 662 previews and 945 MB after clicking through ninety
  authors. Now that is 150–200 MB, flat. The bytes are in `data/thumbs`, and a
  page revisited is read back from disk.
- **Turning the page drops the downloads queued for the last one**, instead of
  making the page on screen wait behind them.

If the window ever stops answering anyway, it leaves the stacks of every thread
in `data/window-hangs.log` — see [Troubleshooting](troubleshooting.md#the-window-stopped-answering).
