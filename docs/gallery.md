# Gallery

**The wall of previews the [Review](review.md) tab opens when you click an author.**

## What it is for

Everything one author has made that you are not currently subscribed to, newest
first, as previews. It is where the decision actually gets made: you are looking
at pictures, not at a list of titles.

## What a card knows about a wallpaper

| Mark | Meaning |
|---|---|
| **new** | Published since your last visit. This is what the badge on the author card counts. |
| **queued** | It is one of the wallpapers you put in the `new` folder. |
| **was yours** | You had it once and deleted it. |
| **subscribed** | Taken. |

**"was yours" is the one that saves the most work.** Wallpapers come back
around, so they are shown rather than hidden — but re-reviewing them blind is
exactly the work this tab removes. For one author, 327 of 1 155 were in this
state.

The mark comes from `project.json` files across the Rotator's libraries, where a
workshop wallpaper names itself with a `workshopid`. That walk is 33 882 folders
and takes four minutes cold, so it is cached in `data/library.json` by folder
name and modification time; afterwards a refresh reads only what changed.

**"subscribed" dims the card where it stands** rather than removing it. A tile
disappearing under the cursor loses your place in a wall of four hundred. The
badge on the author card falls by itself, because it counts what is still on
offer.

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
