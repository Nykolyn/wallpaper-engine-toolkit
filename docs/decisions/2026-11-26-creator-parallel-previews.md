---
date: 2026-11-26
status: accepted
source: "owner, in chat"
tags: [engine]
---

# Creator renders four previews at once, in one ffmpeg pass each

**Decision.** Creator builds up to four wallpapers at the same time (fewer on
a machine with under eight logical processors). Each GIF preview is one
ffmpeg run: `split` feeds `palettegen` and `paletteuse` from a single decode,
instead of two runs with a palette file between them. Videos are still
copied onto the destination one at a time. **Pause** lets the wallpapers in
progress finish, then waits.

**Why.** A build spent ~1.6 s per clip (360 clips in 9 min 39 s), nearly all
of it in ffmpeg on one core. Measured on a 28-thread machine with 12 real
clips: 1 at a time 1.38 s a clip, 2 → 0.77 s, 4 → 0.50 s, 6 → 0.40 s,
8 → 0.42 s. Four gets most of the gain and leaves the processor for Wallpaper
Engine, which keeps playing during a build. The one-pass GIF is byte-identical
to the two-pass one and ~13% faster. Copying videos side by side onto a hard
disk would only make its head seek between them.

**Consequences.** Per-file results arrive in the order clips finish; the final
report keeps the selection's order. Several rows show as working at once.
Folder-name suffixes come from an atomic counter, so two clips started in the
same second still get different folders. See [Creator](../creator.md#building-safely).
