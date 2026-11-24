# Decisions

Every decision that shapes the project — what it does, how it is built, how it
ships — has a note here. The notes are the source of truth: when code, a doc
page and a note disagree, the note says what was meant, and one of the other
two is the bug. A note is written or updated in the same pull request as the
change it describes.

This folder is part of the Obsidian vault that is `docs/` (open `docs/` as a
vault). Notes use ordinary relative Markdown links, so they read the same on
GitHub.

## Writing a note

One decision per file, named `YYYY-MM-DD-short-name.md` after the day it was
made:

```markdown
---
date: 2026-11-24
status: accepted          # accepted | superseded
source: "#99"             # pull request(s) or "owner, in chat"
tags: [process]           # area: process, data, rotator, tracker, review, ui, engine, release, steam
---

# The decision, as a sentence

**Decision.** What was chosen, stated so it can be checked.

**Why.** What forced it, and what the obvious alternative would have cost.

**Consequences.** What follows from it in code, docs or daily work.
```

A decision that is replaced is not deleted: set `status: superseded`, add
`superseded_by:` with a link to the new note, and leave the text as it was.
Requirements that change without a new decision (a number, a default) are
edited in place, with the pull request added to `source`.

## The notes

| Date | Decision | Area |
|---|---|---|
| 2026-09-07 | [Steam is read without a Web API key](2026-09-07-steam-without-key.md) | steam |
| 2026-09-17 | [GitHub is where the project lives; every change is a pull request](2026-09-17-github-source-of-truth.md) | process |
| 2026-09-17 | [No personal data in source, tests or previews](2026-09-17-no-personal-data-in-source.md) | process |
| 2026-09-18 | [The Tracker follows Wallpaper Engine's own files](2026-09-18-tracker-follows-engine-files.md) | tracker |
| 2026-09-18 | [Keep Python off per-frame and per-item GUI paths](2026-09-18-python-off-gui-paths.md) | ui |
| 2026-09-19 | [Playlists are rewritten while Wallpaper Engine is closed](2026-09-19-rewrite-playlist-while-closed.md) | engine |
| 2026-09-21 | [Authors live in SQLite beside the app; MongoDB is gone](2026-09-21-authors-in-sqlite.md) | review, data |
| 2026-09-21 | [No leftovers: what is replaced is removed](2026-09-21-no-leftovers.md) | process |
| 2026-09-23 | [The redesign and its gate answers](2026-09-23-redesign-gates.md) | ui |
| 2026-09-26 | [Data lives outside the program folder and is never lost quietly](2026-09-26-data-outside-program-folder.md) | data |
| 2026-10-10 | [One installer, and it keeps the data](2026-10-10-installer.md) | release, data |
| 2026-10-12 | [Multiplier quotas replace physical copies](2026-10-12-multiplier-quotas.md) | rotator |
| 2026-10-23 | [Releases are made by hand](2026-10-23-manual-releases.md) | release |
| 2026-11-08 | [Restore a pass Wallpaper Engine threw away](2026-11-08-pass-restore.md) | engine, tracker |
| 2026-11-20 | [Player controls act on the click](2026-11-20-player-controls.md) | tracker |
| 2026-11-24 | [docs/ is the Obsidian vault and the source of truth](2026-11-24-obsidian-vault.md) | process |

Longer design write-ups that led to some of these are in
[`superpowers/specs`](../superpowers/specs/) and their build plans in
[`superpowers/plans`](../superpowers/plans/).
