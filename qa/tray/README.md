# Tray and branding verification

Everything here is drawn by `tools/tray_preview.py` from made-up values (a
4 of 201 playlist on "Monitor1"); nothing is read from the data folder.
The reference is the design's `Tray and Notifications` and `Branding` pages.

| What | Picture | Compared with |
|---|---|---|
| The four states at 16, 20, 24, 32, 40 and 48 px, on a dark (`#202020`) and a light (`#F3F3F3`) taskbar, each drawn at its true size and magnified 4× without smoothing | [icons](icons.png) | the design's "four states" and "ring geometry" |
| The menu, running | [menu-running](menu-running.png) | the design's tray menu |
| The menu, paused / unknown / finished | [paused](menu-paused.png), [unknown](menu-unknown.png), [finished](menu-finished.png) | the same menu, header per state |
| The balloons' copy: finished with the batch known and unknown, started over | [notes](notes.png) | the design's two Windows toasts |
| The app icon at 16 to 256 px | [`assets/icon_preview.png`](../../assets/icon_preview.png) | `Branding` |

What was checked:

- The ring: stroke 2.5 / 3 / 3.5 px at 16 / 24 / 32 (interpolated for 20, 40, 48),
  clockwise from twelve, the percentage from 24 px up and a dot below, the
  pause bars and the tick at the design's proportions, the unknown track as eight
  dashes. The light taskbar's `#2C6BD8` and `#1A1A1A`.
- The menu, also on the real Windows platform at 150 % scale (a frameless,
  translucent `QMenu` with its own painting): rounded, rows 32 px, the hint
  column in mono, Open Toolkit highlighted for Enter.
- `TaskbarTheme` end to end on the real platform: a settings message sent to
  the process's own windows flips the icon's colours. (A tray-only process has no
  window of Qt's own, so the Windows broadcast would never reach the filter; it
  listens through a native window that is created and never shown.)

Deviations from the design, and why, are in the pull request. In short: the
menu has no drop shadow (a translucent rounded popup cannot carry Windows' own);
the menu and rows use `r.lg` / `r.md` (the design draws 9 and 6); the
"Playlist finished" balloon says "from the reserve", not "that have never been
used" (the tray cannot count those without listing the wallpaper disk); and
the balloons have no buttons (gate G6 A).
