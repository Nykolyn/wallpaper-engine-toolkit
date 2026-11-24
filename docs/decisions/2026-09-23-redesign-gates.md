---
date: 2026-09-23
status: accepted
source: "16 step pull requests, last #40"
tags: [ui]
---

# The redesign and its gate answers

**Decision.** The UI was rebuilt in PySide6 from a Claude Design handoff in 16
steps, one pull request each, ending with 3.12.0 (#40). Glass is translucent
fills over a static gradient; no blur. The design's CSS is content-box, so
borders and padding sit outside a named size. The owner answered each gate:

| Gate | Answer |
|---|---|
| G1 window chrome | **B** — frameless, the design's 32 px title bar; snap and resize kept through `WM_NCHITTEST` (`app/window_frame.py`). Chosen over the recommended native bar. |
| G2 Copier purpose | **A** — keep N copies for playlist weighting. Later replaced by [multiplier quotas](2026-10-12-multiplier-quotas.md). |
| G3 gallery bar actions | **A** — "Subscribe selected" + "Subscribe page". |
| G4 scheduled scan | **A** — no scheduler; Done shows the last scan. |
| G5 tray ring and menu | **A** — the ring is the time left on the current wallpaper; menu as designed. |
| G6 toast buttons | **A** — balloons that click through to the page; no buttons, no new dependency. |
| G7 Rotator pause | **A** — no Pause; "Stop after this step" only. |
| G8 Creator auto-tags | **A** — no tags from folder names for now. |
| G9 selection strategies | **A** — one strategy, shown as a line. |
| G10 display name | **A** — "Toolkit" in the title, tray and notifications; "Toolkit for Wallpaper Engine" in About; exe, task and data names unchanged. |
| G11 Creator "Open in Wallpaper Engine" | **A** — omitted. |

**Why.** The old window was a port of three tools: forms, dominant path fields,
empty log panels, nested tabs, no overview.

**Consequences.** Do not assume the owner takes the recommended option — ask at
each gate. Page snapshots come from `tools/ui_snapshot.py` with fabricated
fixtures. The look and feel is documented in
[Development](../development.md#look-and-feel).
