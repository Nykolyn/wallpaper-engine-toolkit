---
date: 2026-10-12
status: accepted
source: "#62"
tags: [rotator]
---

# Multiplier quotas replace physical copies

**Decision.** A wallpaper is weighted by a persistent **Multiplier** and a
separate **Remaining** quota, not by physical copies in the reserve. A
successful move into rotation spends one appearance; selection is weighted by
Remaining, never repeats a wallpaper inside a batch, drains smaller tail
batches, and starts a new cycle only when eligible quotas are exhausted. Raising
an active wallpaper from 1 to 20 leaves 19 future appearances. The Copier was
removed (version 4.0.0).

**Why.** Weighting by physical copies cost disk space and meant tracking which
folder was a copy of which; one wallpaper with a number does the same job.

**Consequences.** Supersedes G2's "keep N copies" in the
[redesign gates](2026-09-23-redesign-gates.md). The one-off migration of copies
was later removed from the app (#63). See [Rotator](../rotator.md) and
[Development](../development.md#multiplier-state).
