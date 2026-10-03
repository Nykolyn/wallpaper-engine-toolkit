# Creator redesign verification

All screenshots use fictional names and paths from `tests/fixtures/ui/creator.json`.
They were taken from the real page with `tools/ui_snapshot.py`.

| State | Snapshot | Compared with the handoff |
|---|---|---|
| Empty, tag popup open | [empty](empty.png) | frame 17 |
| Reading | [reading](reading.png) | frame 18 |
| Scanned | [scanned](scanned.png) | frame 19 |
| Building | [building](building.png) | frame 20 |
| Done | [done](done.png) | frame 21 |
| ffmpeg missing | [ffmpeg missing](ffmpeg-missing.png) | error-state brief |

Layout, spacing, hierarchy, colors and state changes were checked against
frames 17–21. The Done list is one of the prompt's allowed presentations.
Missing fictional previews use the kit's placeholders; a separate real-ffmpeg
check loaded an actual generated GIF through Qt's image reader.

Additional checks: [1040×720](scanned-small.png), [2560×1440](done-large.png),
[1280×860 at 150%](scanned-150.png). Standard state screenshots are 1280×860.
The shared kit's [fields](fields.png) and [tables](tables.png) include custom
tags, checkbox/tag cells and indeterminate per-item progress.

Validation on Windows with Python 3.12 and Qt offscreen:

- All 30 `tests/test_*.py` scripts passed; the final keyboard change also passed
  the Creator page's 34 checks.
- `run_app.py --selfcheck`, `tools/release.py check --base origin/main`, and
  `git diff --check` passed.
- A generated two-second H.264 clip was scanned and built with real ffmpeg
  in a temporary directory. Dimensions/duration, JSON tags, copied video size,
  a readable 480×480 GIF, and removal of the verified Move source were checked.
- Staged added text was audited for secrets, real user paths and fixture names;
  screenshots contain only the fictional fixture data. Local diagnostic logs
  are ignored.

Decisions: G8 A (no automatic folder-name tags), G11 A (omit Open in Wallpaper
Engine). Formats and preview copy derive from engine constants. Build size
states the exact video payload plus previews, whose size is known after render.
Per-item progress is indeterminate because ffmpeg's percentage is not measured.
