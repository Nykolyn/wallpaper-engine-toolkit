# Overview

**Where the loop stands, at a glance: the first entry in the sidebar, and the
page the window opens on (Ctrl+1).**

The loop is rotate → watch the playlist run down → review what is new. The
Overview reads each part from what the window already knows, so opening it
costs nothing: it never lists the reserve on the page itself — that is counted
on a worker, and the page shows the result when it arrives. Nothing here is
made up: a number that is not known yet says why, instead of showing 0.

**Refresh now**, at the top right, counts the folders again, has the Tracker
look at Wallpaper Engine, and reads the journal and the log again. The date
beside the title moves on with the minute.

## The four numbers

| Card | What it is | When it has none |
|---|---|---|
| **Reserve** | Folders in the reserve, and how many were never used — what the next run draws from. | *set the reserve folder in Settings* (a link), or *reserve folder not found* |
| **In rotation** | Folders in `myprojects` now, `[protected]` ones not counted; and how many today's runs swapped in, once one has. | *set the myprojects folder in Settings* |
| **Playlist** | The leading monitor's count, `4 / 201`, and how many are still to go. It turns green when every wallpaper has been shown. | *no playlist counted yet* |
| **New since last review** | What the last Review scan found, in amber while it waits to be reviewed, and from how many authors. | *no scan yet*; once a review is finished, *reviewed … · no scan since*, because what is new is not known again until the next scan |

While a count is on its way the card shimmers. A number that was read before
but cannot be read now — the Playlist while Wallpaper Engine is not running,
say — stays, greyed, and says **last known**.

Each card opens the page its number comes from: the Rotator, the Tracker or
Review — or Settings, when what is missing is a folder to set.

## The loop

A tile for each part:

- **Rotator** — while a run goes: its step (`Step 2 of 4 — moving 1 000
  folders into myprojects`), a bar, `412 / 1 000` and, once the run's own pace
  gives one, `≈6 min left`. Otherwise how the last run went: *Ready for run
  39*, *Run 38 had 2 problems* in amber, *Run 38 failed* in red.
- **Tracker** — *Counting the playlist down*, how many of how many have been
  shown, and the day it should run out (`≈21 Sep`, an estimate from the
  playlist's own pace). *Playlist finished — time to rotate* when it has.
  With Wallpaper Engine not running: the last known count, said so.
- **Review** — the authors still waiting (*12 authors waiting*) and how many
  new items since the last visit; while a scan runs, how far it has got.

The tile of whatever is running is outlined in blue with a pulsing dot. A tile
opens its page, as does **Open Rotator →**.

Under the tiles, one sentence says what the next run will do: *The next run
draws 1 000 at random from the 8 204 never used.* When fewer folders were
never used than a run moves, it says instead that the history resets on the
next run. A rotation is always started by hand, on the [Rotator](rotator.md)
page; nothing here starts one.

## Recent activity

The eight newest entries of the activity journal (`data/activity.jsonl`): a
run starting and ending, duplicates set aside, the playlist advancing or
finishing, a Review count, a Creator build, a Copier run. Each has the time
(`13:47` today, `Fri 09:10` within the week), the tool's icon — amber or red
when it ended with problems or failed — what happened, and a chip where the
entry has one. Clicking a row opens its tool's page. **Open log folder** opens
`data/logs/`.

The journal is written by the window, so the playlist's events are the ones
seen while it was open. On a fresh install it says *Nothing has happened yet*.

## Monitors and the log

On the right, a card for each monitor the [Tracker](tracker.md) counts, the
leading one first and marked **LEADING** (the **Lead monitor** in
[Settings](settings.md#tracker-and-tray) chooses it; Automatic picks the
playlist a rotation built): what it is showing, for how long, and its count.
With Wallpaper Engine not running they show the last known wallpaper and
count, and say so.

Under them, the log: while a job runs, the newest lines of that job's own log
file, followed as they are written; otherwise the log file written last, named
beside the title. The badge counts its warnings and errors. Open it (the
chevron) for **All / Problems**, copying lines, and **Open log folder**. The
panel holds the last few hundred lines; the files themselves are in
`data/logs/`, kept for 30 days — see
[Where the logs are](troubleshooting.md#where-the-logs-are).
