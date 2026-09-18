# Tracker

**How far Wallpaper Engine has got through the active playlist — `112/208`.**

## What it is for

After a [rotation](rotator.md) you build a playlist and Wallpaper Engine walks
it at one wallpaper per `delay` minutes. The only question that matters
afterwards is *how much of it has already been shown*, because that is when the
next rotation is due. Without an answer you either rotate early and throw away
wallpapers nobody saw, or rotate late and watch the same set for another week.

Wallpaper Engine does not report this. It stores neither a position nor a
shuffle, and its playlists are ordered `random`. The Tracker reconstructs it.

## When to reach for it

- Continuously, in the background, as a tray icon — that is its normal mode.
- Open the tab when you want the detail: what has been shown, what has not,
  when the cycle started, and the projected finish.

## How to use it

Open the tab, or run the tray-only tracker:

```
python run_app.py --tracker
```

…or `run_tracker.cmd`. The tray icon is a progress ring with the percent in the
middle; the tooltip carries `seen/total` per monitor. A balloon fires once a
playlist has been shown end to end — that is the cue to rotate.

Clicking the icon opens the main window on this tab — as a **program of its
own**, at normal priority, and clicking again brings the same window forward
rather than opening another. The window used to be built inside the tray; when
it froze during a Review, ending it ended the count as well, and it ran at the
below-normal priority the logon task gives the tray. Now either can stop
without the other. The tab in it looks for itself, on the same schedule as the
tray — a look is about 4 ms, taken only when Wallpaper Engine writes — and the
two write the same state file and merge on save, so running both is fine. A
named mutex keeps autostart and a manual `run_tracker.cmd` from becoming two
icons both looking, and another keeps the window to one.

**New cycle** resets the count by hand. You should rarely need it — see
[cycles](#cycles).

## Starting with Windows

The tab's checkbox, or without a window:

```
python run_app.py --autostart on
python run_app.py --autostart off
python run_app.py --autostart status
```

It installs a **scheduled task** triggered at logon with a 30-second delay, and
falls back to the `HKCU\...\CurrentVersion\Run` key if Task Scheduler refuses.
Only one of the two is ever installed, and the tab shows which.

The task is preferred because a Run entry is launched by Explorer during shell
startup, when the notification area may not exist yet and a tray icon has
nowhere to go. A delayed task starts once the desktop has settled and does not
depend on Explorer processing the Run key at all.

It is defined from XML rather than through `schtasks /tr`, which cannot be
handed a quoted path plus arguments unambiguously. That also makes room for the
settings a long-lived process needs — chiefly no execution time limit, since the
default stops a running task after three days.

> **Upgrading from "Wallpaper Suite".** The app used to register its task under
> the old name. On first run the entry is renamed in place, carrying its action,
> trigger and delay over unchanged, and the old one is removed. If the entry
> pointed at an executable that no longer exists — because the build was
> replaced by one under the new name — it is rebuilt from scratch instead. This
> happens once; the result is recorded in `data/suite.json`.

The tracker does not treat a missing notification area as fatal: it waits up to
two minutes for one, and if none appears it keeps counting without an icon
rather than quitting or putting a modal dialog on screen at logon. Every launch
appends to `data/tracker.log` — start, whether the tray was there, and any
crash. A windowed build has no console, so that file is what answers "why was
there no icon after I logged in?".

## How it works

### Where the numbers come from

Playlists, their item lists and the timer delay are read from Wallpaper Engine's
own `config.json`, one active playlist per monitor, under
`<user>.general.wallpaperconfig.selectedwallpapers`.

That file is **not** a live source for what is on screen. Wallpaper Engine
flushes it when it starts and when it exits, so the current-wallpaper field
stays frozen at whatever was showing at launch.

What *is* live is `bin/playliststate.bin`, rewritten at every wallpaper change
(see [the countdown ring](#the-countdown-ring)). Per monitor it names the
wallpaper on screen and every entry the current pass has **not drawn yet**. For
a random playlist that is the count itself: what the pass has drawn has been
shown, what is waiting has not.

### When it looks

The tracker looks whenever Wallpaper Engine rewrites `playliststate.bin` or
`config.json` — both are checked with a `stat` once a second, about 20 µs — so
a change reaches the count within a second or so of the engine writing it down,
together with the ring. Every change is seen, including wallpapers skipped in a
hurry.

It used to look every 30 seconds whether anything had happened or not. On a
10-minute playlist nineteen looks in twenty found nothing, and each still cost
about 50 ms on the tray's GUI thread plus a rewrite of the 200 KB
`data/tracker.json` — some 2900 looks and 570 MB of rewrites a day, for about
290 changes on two monitors. A look now costs about 4 ms, and the file is
written only when something in it changed.

A slower **safety check** stays, every five minutes by default ("Also check
every" on the tab), for what no write announces — chiefly a wallpaper deleted
from disk. Where there is no readable `playliststate.bin` at all, the tracker
goes back to looking every 30 seconds, because then nothing else will tell it;
`data/tracker.log` says which of the two it is doing.

### Wallpaper Engine's own record

Where the state file describes the playlist — random order, the wallpaper on
screen in the playlist and no longer waiting, a deck of this playlist rather
than another — the count follows it. Credits it contradicts are withdrawn. A
change looked at as it is written is dated to the second from the write;
anything drawn between two looks, or while nothing was running, is counted too
and marked `~`, dated from its file's access time. The card says "following
Wallpaper Engine's own record of the pass".

Checked against the live tracker before the switch, the deck named the same
wallpaper on screen as the handle probe on both monitors, and disagreed with the
count only where access times had credited something the engine had not drawn:
181 counted on a playlist it had drawn 180 of, 6 on one it had drawn 4 of. Each
such credit made "playlist finished" arrive a wallpaper early.

### Where the record does not reach

A **sorted** playlist, whose deck has not been checked against a real one, and a
state file that does not fit the playlist, are counted the way everything was
before the state file was understood — by the **file handle**. The renderer
keeps the wallpaper it is showing open, so opening a path with no sharing flags
gets `ERROR_SHARING_VIOLATION` for exactly that one file and succeeds for every
other — for `.mp4` and `scene.pkg` alike. The whole playlist is swept every
look, about 17 ms for 1300 items.

The sweep deliberately does **not** stop at the item found last time. Wallpaper
Engine does not always release a wallpaper it has moved on from; two files were
caught held at once, one playing and one abandoned half an hour earlier. When
several are held, the one whose file was **read most recently** is the one on
screen.

### What neither can see

- **Deleted wallpapers.** Wallpaper Engine goes on listing a wallpaper after its
  folder is gone — in its deck too — and it can never come up again. These are
  found by checking existence every five minutes and whenever `config.json` or
  the cycle changes (about 12 ms for 1600 items), and held out of the total. On
  the playlist that prompted this: 197 of 208 shown, all 11 stragglers deleted
  mid-cycle — the tracker would have read 197/208 for ever. A file missing from
  a folder that has *itself* vanished is left alone instead: that is an
  unmounted drive, not a deletion.
- **Web wallpapers**, to the handle probe. An `.html` wallpaper is read once by
  a browser process and never held. The engine's record counts them like any
  other; where the probe is in use, its access time is the only thing that
  credits one — see reconciliation below.

### Cycles

Everything shown since a playlist was first seen is one *cycle*, kept in
`data/tracker.json` as `item -> first shown`.

A rotation replaces the playlist wholesale, so when the item list overlaps the
tracked one by less than half it is treated as a new playlist: the old cycle is
archived and the count restarts by itself. Smaller edits — a handful of items
added or removed — keep the cycle and adjust the total.

### Time nobody was watching

A pass followed through Wallpaper Engine's record needs none of this section:
the deck says what was drawn while nothing ran. What follows is for the
playlists counted by the handle probe.

A tally that only advances while the app happens to be running drifts behind
silently. NTFS fills the gap: with last-access updates on (the Windows default,
`fsutil behavior query disablelastaccess`), showing a wallpaper stamps its
file's access time, at one-hour granularity — far finer than the delay between
wallpapers.

So the tracker is a **reconciler**, not a counter. It re-reads those stamps when
it adopts a playlist it has not been watching, when a look finds more than five
minutes passed since the last, and in any case every ten minutes. Recovered
entries are marked `~` and counted separately, so observation and reconstruction
are never confused. Where last-access updates are off, the tab says so and falls
back to counting only what it sees.

An access time is not proof of a display, though. Plenty of things read the
whole library at once — Wallpaper Engine refreshing its browser, a rotation
moving 200 folders, a backup walking the tree. On one real rotation 178 files
shared a single access time, and a playlist that had shown exactly one wallpaper
reported 180 of 199 done.

**The thing that separates them is the rate, and only the rate.** A machine
reads at 160 files a second; a person pressing "next" twice does it 22 seconds
apart; the playlist moves once per `delay`. So access times are grouped by the
quiet between them — five seconds of nothing ends a group — and a group is
discarded only when it holds at least five files *and* they arrived faster than
two a second. Everything else counts.

An earlier attempt tested against the playlist's cadence instead, reasoning that
displays cannot arrive faster than one per `delay`. That is false the moment
anyone skips by hand, and it cost what you would expect: two wallpapers skipped
22 seconds apart were discarded as a scan and sat in "not yet shown" for ever.
There is also deliberately no cap on how much one sweep may credit —
reconstructing a cycle that has run for days *should* credit most of it at once,
which is the very case such a cap refuses.

### When the cycle began

For a playlist the [Rotator](rotator.md) built, this is exact: `history.json`
records the folders each run moved, so the newest run sharing at least half its
folders with the playlist *is* its starting moment. Otherwise the start is taken
from the break in the access times — inside a running cycle wallpapers are shown
day after day, so only a gap of several days reads as "the previous playlist
ended here". If neither is conclusive the tracker refuses to guess and starts
counting from now. The card always says which of the three applied.

### What comes next

Wallpaper Engine offers two orders, `random` and `sorted`, and only sorted has a
queue: it walks the items in order and wraps, so the list reads from just after
the wallpaper on screen and its top row is genuinely next.

Random has no queue anywhere a program can read. `config.json` stores the order
setting and nothing else; the log says nothing; and the order observed here has
no relation to the list — 36 displays watched live, not one step to the next
position, a rank correlation of +0.07. Each pass is a shuffle rather than a dice
roll (196 changes on a 189-wallpaper playlist repeated one wallpaper, where
drawing with replacement repeats about seventy), so every wallpaper still
waiting is exactly as likely to be next as any other.

The list therefore says which of the two it is showing — "Up next, in playing
order" or "random order, any of these can be next" — rather than inventing an
order. To get a real queue, set the playlist to Sorted in Wallpaper Engine.

### Which monitor leads

With two monitors running two playlists, the card, the list and the tray number
all follow the playlist a **rotation built** — that is the one whose end is the
cue to rotate again. A subscribed or hand-made playlist runs forever and says
nothing about timing. *Show on the icon* in the tray menu overrides this, and
the choice is remembered.

### When Wallpaper Engine starts a playlist over

Wallpaper Engine can begin a playlist again on its own, and then the count
describes a pass that no longer exists. It happened for real: the machine was
started with one monitor, Wallpaper Engine launched, and the second monitor
plugged in afterwards. That monitor's pass was thrown away and a fresh one
begun, while the tracker went on at 81/195 — counting as shown the very
wallpapers about to be shown again.

`bin/playliststate.bin` says so plainly: it keeps, per monitor, the entries the
current pass has not drawn yet. Everything counted as shown should be missing
from that list, and before the restart it was. After it, 77 of 83 were waiting
again; on the other monitor, 196 of 200.

So on every look, when at least half of what the cycle counts as shown (and no
fewer than three) is back in the engine's deck, the cycle is archived with the
reason and a new one begins from what the engine has drawn — credited from the
engine's record and marked `~`. A deck that does not fit the playlist is
ignored. The tray says when it happens ("Playlist started over") and the card
says what the old count reached.

A pass that ran to its **end** begins again the same way, and is told apart by
what was left: nothing, or only the wallpaper now on screen. That covers both
things Wallpaper Engine might do at the end — write out an empty deck first, or
shuffle the next pass as it draws the last wallpaper, in which case the finished
cycle is never seen whole. Either way the tray says "Playlist finished" once,
and the card says the next pass began after all of the last one was shown.

**New cycle** on a followed pass sets aside what the pass has drawn so far,
since the engine's record would otherwise restore the whole count at the next
look. The set-aside wallpapers count again once Wallpaper Engine deals them
again.

**What causes it, and how to avoid it.** Wallpaper Engine keeps a pass with the
playlist *as applied to a monitor*, and discards it whenever the playlist is
applied to that monitor again:

- **A monitor that appears after Wallpaper Engine has started.** Started with
  one screen and given the second two minutes later, the second screen's pass
  was replaced (deck 118 to 199) while the screen that had been there carried on
  untouched (deck 1385 to 1383). So connect and switch on every monitor *before*
  starting Wallpaper Engine.
- **Saving the playlist.** A subscribed playlist extended from 1 361 to 1 386
  wallpapers began a fresh pass seven seconds later. Add subscriptions in
  batches, when the pass is done anyway.

Skipping ahead does *not*: a burst of changes took the deck from 195 to 192 one
draw at a time, in the same pass.

### The countdown ring

The number in the middle of the tray icon is how much of the playlist has been
shown. The ring around it is how long the current wallpaper has left — full just
after a change, draining clockwise, grey while paused, an empty track while the
time is not yet known.

Wallpaper Engine does not publish its timer. Its `-control` commands report no
time and it keeps no pipe open to ask. It does keep two files in `bin/` in a
small length-prefixed format of its own (magic `PLPV0005`), both decoded here
exactly:

- `playliststate.bin` — per monitor, the wallpaper on screen and the entries
  this pass has not reached. **Rewritten at every change, to the second.** Its
  modification time is the one exact clock available.
- `playliststatetime.bin` — the seconds each timer had run, but written only as
  Wallpaper Engine exits, so it cannot follow a running timer and is unused.

**The timer is the playlist's `delay` of *playing* time**, measured rather than
assumed: changes on a 10-minute playlist at 14:53:46, 15:03:46, 15:13:45,
15:23:45. Unless "update on pause" is on, it stops while the wallpaper is
paused — and pausing is **per monitor**, decided by Wallpaper Engine's own
`playbackfocus` / `playbackmaximized` / `playbackfullscreen`: an application
focused, maximized or covering *that* monitor pauses that monitor and no other.
Cross-checked by reading the position of each video file Wallpaper Engine had
open: the file behind the monitor judged paused stood still, the other advanced
about 12 MB a second.

Window overlays that sit over a screen without being an application — game and
capture overlays, tool windows, anything that never takes focus — are not
counted, and neither are Wallpaper Engine's own windows. This app's window *is*,
because to Wallpaper Engine it is an application like any other. Displays are
matched to `Monitor0` / `Monitor1` through the device paths Wallpaper Engine
records in `monitormap`. A tick costs 0.3 ms.

**Reading the timer itself.** The estimate above was off by up to seven seconds,
so wherever it can the tray reads the real value. Scanning `wallpaper64.exe` for
float32 values that grow in step with real time turns up one per monitor, each
dropping to 0.000 the instant its monitor changes. Each lives in a playlist
object that describes itself: the monitor's name as a short `std::string`, then
at fixed offsets the delay in minutes (+88), the playlist instance number
(+104), the timer in seconds (+108) and the transition in milliseconds (+116).
Nothing else in memory matches all five, so the object is found by searching for
the name and checking the rest against `config.json` and the state file. Access
is read-only (`PROCESS_QUERY_INFORMATION | PROCESS_VM_READ`); nothing is ever
written.

**Reading a process without stopping it.** The first version searched 1.4 GB in
16 MB pieces and made Wallpaper Engine stutter while it started. What the other
process feels is the length of one read, not their number:

```
piece      64 KB    256 KB      1 MB      4 MB     16 MB
median   0.33 ms   1.32 ms   5.26 ms   55.3 ms    ~220 ms
max      3.42 ms  12.45 ms  48.16 ms  154.6 ms
```

So the search reads 64 KB at a time, rests after each piece for as long as the
read took, runs at background thread priority, and does not start until
Wallpaper Engine has been up for 30 seconds. It starts with the smallest
regions, where a heap keeps small objects, and once one monitor's object is
found it looks beside it for the others — 24 MB instead of 861 MB. Where each
was found is written down, so the next tray to start checks two addresses
instead of searching at all.

```
cold, nothing remembered      25 MB     0.3 s   longest hold 0.60 ms
the place remembered           2 reads  0.0 s   longest hold 0.04 ms
the place has moved          141 MB     2.1 s   longest hold 0.80 ms
nothing matches at all      1374 MB    38.6 s   longest hold 3.96 ms
```

The last line is what a Wallpaper Engine update that moved the fields would
cost, after which the tray waits five times longer before each attempt, up to
half an hour.

Measured on two real changes, the countdown read 0.00 s and 0.97 s a quarter of
a second before the timer reset. The remaining second is Wallpaper Engine's own:
it checks the timer about once a second, so it changes at anywhere from 599 to
601 s. If the object cannot be found — a 32-bit build, or an update that moved
the fields — the tray says so in `data/tracker.log` and uses the estimate.

What cannot be known is said, not guessed. Until the tray has seen a change the
ring is empty, because the state file does not say when the wallpaper on screen
came up. The first change after Wallpaper Engine starts is marked approximate
("≈") as a precaution. The count is saved to `data/wallpaper_timer.json` every
15 seconds and picked up again by a restarted tray, as long as Wallpaper Engine
and the wallpaper are the same.

### The estimate

With the order set to `random` the same wallpaper can come up twice, so `seen`
and `changes` are counted separately. Time left is `remaining x delay`,
stretched by what a newly seen wallpaper has actually cost so far.

It deliberately does not model the order. An earlier version switched to the
coupon-collector expectation as soon as a single repeat appeared, turning a
2h40m estimate into 4d21h on one stray event. The evidence was against it
anyway: 192 distinct wallpapers out of 193 displays is a shuffled playthrough,
where drawing with replacement from 208 would yield about 125. Repeats are also
only visible in the displays actually watched, never in the ones rebuilt from
file times, so they are far too weak a signal to pivot a model on.

On a pass followed through Wallpaper Engine's record there is no stretching at
all: nothing is drawn twice within a pass, so what is left is exactly
`remaining x delay`. A repeat rate carried over from the probe's count — 22
"repeats" on a 189-wallpaper playlist here, most of them the probe losing track
— has nothing to say about it.

Wallpaper time is not wall-clock time — it only passes while Wallpaper Engine is
running and unpaused, and `playbackfullscreen` / `playbackmaximized` stop the
timer behind a game. So the card also projects a finish from the pace the cycle
has actually kept — elapsed time divided by wallpapers seen — which is the
number that answers "when can I rotate?".

## Files

| File | What it holds |
|---|---|
| `data/tracker.json` | the live cycle per monitor, plus the last 40 finished cycles |
| `data/wallpaper_timer.json` | the countdown, saved every 15 seconds |
| `data/tracker.log` | every launch, whether the tray appeared, any crash |
| `data/suite.json` under `tracker` | safety-check interval (`heartbeat`, seconds), `config.json` path, which monitor the icon shows |

It **reads** the Rotator's `history.json` to date a cycle and never writes to
it. Nothing is ever written back to Wallpaper Engine.

## Watch out for

- **Clicking a row** opens Explorer on that wallpaper's folder with the file
  selected. A playlist outlives its files, so a row whose file is gone opens the
  nearest folder that still exists instead.
- Playlists that change on a schedule or when a video ends have no delay to
  count, and show no ring.
- Per-application rules (`apprules`) and the on-battery setting are not
  modelled. Settings changed in Wallpaper Engine reach the tracker when it next
  writes `config.json`, which it does on start and exit.
