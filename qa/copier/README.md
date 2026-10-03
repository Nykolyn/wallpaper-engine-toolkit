# Copier redesign verification

All screenshots use fictional paths and names from `tests/fixtures/ui/copier.json`.
The real page is captured with `tools/ui_snapshot.py --page copier`.

| State | Snapshot | Compared with handoff |
|---|---|---|
| Empty | [empty](empty.png) | frame 22 |
| Queue | [queue](queue.png) | frame 23 |
| Running | [running](running.png) | frame 24 |
| Done with failure | [done](done.png) | frame 25 |
| Insufficient space | [no-space](no-space.png) | free-space requirement |

Checked layout, spacing, hierarchy, colors and state transitions at 1280×860.
Additional checks: [queue 1040×720](queue-small.png),
[running 1040×720](running-small.png), [done 2560×1440](done-large.png),
[queue at 150%](queue-150.png). [Kit tables](tables.png) show editable copies
cells and captioned progress.

G2 A keeps N duplicates for playlist weighting (default 3), with optional
per-job destinations. G3 A excludes Send from Review; Tracker remains wired.
Header metrics occupy a second row for space. Failures use a warning header
and the actual filesystem reason. Counts include files times copies, rather
than the design's sample totals. Logs use the shared LogPanel.

Validation on Windows with Python 3.12.14 and Qt offscreen:

- All 32 test scripts passed, including 11 Copier engine tests and 31 page checks.
- Engine tests use temporary folders: suffix numbering/no overwrite, queue
  ordering, pause/resume, cancellation cleanup, verification mismatch,
  failure isolation, active and idle retry, skip, parsing and free-space budgets.
- Page checks cover fixtures, editable copies, totals, worker measurements,
  stale scans, service/journal/toast integration and retry without duplicate output.
- Selfcheck, release check (3.10.0 over 3.9.0) and diff check passed.
- The local venv launcher references a missing interpreter; checks used the
  bundled Python 3.12 with existing site-packages, matching CI.
- Screenshots contain fabricated data. Logs and local runtime helpers are ignored.
- No installer was built and no live wallpaper library was used.
