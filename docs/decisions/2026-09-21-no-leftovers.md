---
date: 2026-09-21
status: accepted
source: "owner, in chat (#12)"
tags: [process]
---

# No leftovers: what is replaced is removed

**Decision.** When something is replaced, the old code, docs, dependencies,
stored secrets and stale files go in the same change — not "in a later
release". Data on disk goes to the Recycle Bin, never a hard delete.

**Why.** The owner's rule, stated when MongoDB was removed: keep the code clean
and leave no remnants.

**Consequences.** A pull request that replaces a feature lists what it removed.
Superseded decision notes are the exception: they stay, marked `superseded`
(see [Decisions](README.md#writing-a-note)).
