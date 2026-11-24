---
date: 2026-09-18
status: accepted
source: "#5, #29, #43"
tags: [ui]
---

# Keep Python off per-frame and per-item GUI paths

**Decision.** Nothing runs Python per frame or per painted item: animation is
left to Qt, whole item tiles are cached and items outside the repaint region
are skipped. The switch interval is lowered (`sys.setswitchinterval(0.0005)`).
No disk access on the GUI thread — wallpaper libraries sit on slow hard disks.
The window does not run inside the tray tracker's process. Every kit widget has
a parent before it is shown.

**Why.** PySide6 releases the GIL around every Qt call, so a paint costs about
its number of Qt calls under contention: one busy thread made 200 card paints
go from 24 ms to 2.5 s, and a per-frame Python animation froze outright. A
parentless kit button collected while another thread ran crashed the process,
and a parentless widget flashes as a window and steals focus. The tracker's
process runs at low CPU, I/O and memory priority, which a window inside it
inherited.

**Consequences.** When the window freezes, read `window-hangs.log` /
`tracker-hangs.log` before theorising. `tests/perf_gallery.py` measures the
gallery under contention.
