---
date: 2026-09-21
status: accepted
source: "#10, #12"
tags: [review, data]
---

# Authors live in SQLite beside the app; MongoDB is gone

**Decision.** Review's authors are in `data/authors.sqlite`
(`app/engines/authors_store.py`), not a MongoDB server and not JSON. Columns:
`key` (steamID64 or vanity name, case-insensitive), `name`, `added`, `visited`
(UTC text ending in `Z`). The old fields `referenceLink`, `favorite`,
`reviewedFavorites` were dropped on purpose. Every write is followed by a
verified gzip JSON snapshot in `data/authors_backup/` plus a journal; retention
is 10 recent / 7 daily / 4 weekly / 12 monthly. A second backup folder is used
**only if one is set** (`review.backup_mirror`). Every MongoDB module, tool,
test and dependency was removed in 2.1.0.

**Why.** A new user should have nothing to set up, and the owner no longer uses
the old database.

**Consequences.** The last MongoDB-capable code is at tag `v2.0.1`. See
[Authors database](../authors-database.md) and
[No leftovers](2026-09-21-no-leftovers.md).
