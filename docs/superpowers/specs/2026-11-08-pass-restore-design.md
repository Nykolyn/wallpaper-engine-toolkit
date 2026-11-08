# Restore pass — design

Date: 2026-11-08. Status: approved in chat, awaiting spec review.

## Why

Wallpaper Engine throws a monitor's pass away whenever the playlist is
re-applied to that monitor: a monitor attached after WE started, or the
playlist saved/extended in WE. The pass then starts over from zero, and
everything already shown comes back.

It happened again on 2026-11-08 18:34: Monitor1's `custom` pass (started
2026-11-01 01:46) was thrown away at **168/194**. Monitor0's `subscribed`
pass is reset almost daily at ~1%.

The user wants a tool in the app that puts such a pass back, by button and
automatically, and the Monitor1 pass restored with that tool.

## What the user decided

- A new page **Restore** with a **Restore** button per monitor, *and* automatic
  restore by the tracker.
- Automatic restore is a **per-monitor switch**: Monitor1 on, Monitor0 off by
  default. The button works on every monitor.

## How a pass is put back

The same proven route the Rotator uses (`playlist_refresh`, verified live
2026-09-19):

1. Close WE with the tray Quit command (`engine_control.close`). If it does not
   close, stop: no file is touched.
2. Back up `bin/playliststate.bin`, then rewrite that monitor's entry: keep the
   wallpaper on screen as current, and set the deck (`waiting`) to the live
   playlist items minus everything **shown** and minus the missing ones
   (`StateMonitor` gets the deck directly; `start_over` is the shape of it).
   Other monitors are left byte-for-byte as they were.
3. Start WE with the arguments it was running with (`engine_control.start`).
   WE resumes the deck it finds.
4. Give the tracker its old cycle back: the archived pass's id and start time,
   with every shown wallpaper credited, replacing the cycle the reset made. The
   archive entry is marked `restored`.

**Shown** = the archived pass's shown list **∪** what the current (post-reset)
cycle has shown. The union keeps both: wallpapers shown before the reset and
those shown since, so a restore an hour later still counts the hour.

Rejected:
- Skipping forward with `-control nextWallpaper`: a random deck cannot be
  steered.
- Stopping the reset at its source (start WE only after every monitor is
  attached): it does not cover resets from saving the playlist. A possible
  follow-up, not part of this.

## Components

### 1. Tracker archive keeps the shown list (`app/engines/tracker.py`)

`TrackerState.archive_cycle` today keeps only counts. For a cycle archived
because *Wallpaper Engine started the playlist over* (not a pass that ran to its
end, not a playlist change), the entry also keeps `shown` — the paths in
`cycle.seen` that belong to the playlist. Whether the playlist is still the same
is judged later from its name and these paths. Other archive entries stay as
they are. Size: a 168-item list is ~15 KB; `MAX_ARCHIVE` is 40.

### 2. Restore engine (`app/engines/pass_restore.py`, new)

- `restorable(state, monitor)` → the newest archived pass for that monitor that
  WE threw away, was not restored, and whose playlist is still the one running
  (same name, and at least 90% of its shown wallpapers still in the playlist).
  Returns the shown set and where it came from.
- **Source of the shown list.** The archived `shown` list. For entries archived
  before this change (no list, like today's `270bd142`): the playlist items
  whose file access time lies between the pass's start and its end — this
  reproduced exactly 168 for today's pass. Access times are only used when
  `fsutil` says last-access updates are on (the tracker's existing `last_access_enabled()`).
- `plan(...)` → the new deck, pure and testable: items − shown − missing, the
  current wallpaper kept.
- `restore(monitor)` → steps 1–4 above, reporting progress like
  `PlaylistRefresh`, never leaving WE closed: start is attempted even when the
  rewrite failed.
- Refuses, with a reason the page shows, when: no restorable pass; the playlist
  changed; WE is not running; nothing would be
  left to show (the pass was already complete).

### 3. Page **Restore** (`app/pages/restore.py`, new)

Built from the existing kit (cards, buttons, switch) like the other pages. Per
monitor:

- the current pass: playlist, seen/total;
- the last pass WE threw away: when, seen/total, source of the list
  ("saved by the tracker" / "from file access times");
- **Restore** button (disabled with a reason when not restorable), running the
  restore off the GUI thread through the jobs service;
- **Restore automatically** `Toggle`, saved in `suite.json` under
  `restore` / `auto.<MonitorN>`; default on for a monitor whose playlist came
  out of a rotation, off otherwise (here: Monitor1 on, Monitor0 off).

### 4. Automatic restore (`app/tracker_tray.py`)

Where the tray already announces a restart (`_announce_completions` →
`_say_restarted`, i.e. not a pass that ran to its end):

- if the switch for that monitor is on, wait 60 s (WE allocates heavily for its
  first ~30 s), then run the restore on a background thread;
- balloon + activity entry `restore.clean` ("Pass restored on Monitor1:
  170/194.") or `restore.failed` with the reason;
- **loop guard:** a pass restored automatically is not restored automatically
  again within 10 minutes; a second reset in that window only gets the usual
  "started over" balloon and the button.
- Passes started by the Rotator are never restored: they are playlist changes,
  archived for a different reason, so they are never restorable.

## Error handling

- WE does not close → abort, nothing written, WE untouched.
- Writing the state file fails → the backup stays, WE is started anyway.
- WE does not come back → reported, the backup path named.
- Any failure leaves tracker.json unchanged.

## Testing

- `plan` on today's real numbers: 194 live, 168 shown, 2 shown since → 24 in
  the deck, current kept.
- Union of archived and post-reset shown.
- `archive_cycle` keeps `shown` only for engine resets.
- `restorable` refuses a changed playlist and an already-restored pass.
- Access-time fallback picks items inside the pass window only.
- Loop guard: a second reset within 10 minutes is not auto-restored.
- State-file rewrite touches only the chosen monitor (round-trip through
  `read_state_file`/`write_state_file`).

## Out of scope

- Finding or preventing the cause of WE's resets.
- Restoring passes older than the newest thrown-away one per monitor.

## Afterwards

Once merged and installed, Monitor1's pass `270bd142` is restored with the
page's Restore button (expected about 170/194; the archived entry has no list,
so the access-time fallback supplies the 168). The access-time snapshot taken on
2026-11-08 sits in `.tmp/monitor1-pass-270bd142.json` as a cross-check.
