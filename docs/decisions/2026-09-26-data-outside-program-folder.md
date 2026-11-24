---
date: 2026-09-26
status: accepted
source: "#10, #18"
tags: [data]
---

# Data lives outside the program folder and is never lost quietly

**Decision.** A build keeps its data in `%LOCALAPPDATA%\WallpaperEngineToolkit`;
a source run keeps `data\` beside `run_app.py`; `WALLPAPER_TOOLKIT_DATA`
overrides both. Moving it is copy → SHA-256 verify → rename into place → old
folder to the Recycle Bin, and nothing is deleted if any step fails. The
Rotator's history is written atomically and snapshotted; a missing or
unreadable file is restored from a snapshot and said so, never silently
replaced by an empty one. Builds go **only** through `build.cmd`, which stages
and mirrors into `dist\` with `data\` excluded.

**Why.** On 2026-09-20 `pyinstaller` run directly emptied the program's
`data\` folder. The Steam key was noticed and put back; the Rotator's history
was not, and the next rotation started a new one. The owner: no data is to be
lost through a failed build — or at least they must be told.

**Consequences.** Any loss is reported at once, unprompted, with exactly which
files and what cannot be recovered, established from a full listing, not from
the one symptom noticed. See [Configuration](../configuration.md) and
[Building](../building.md).
