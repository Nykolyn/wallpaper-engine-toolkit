---
date: 2026-11-24
status: accepted
source: "owner, in chat"
tags: [process]
---

# docs/ is the Obsidian vault and the source of truth

**Decision.** `docs/` is an Obsidian vault and the project's source of truth
for requirements, behaviour and decisions. Feature pages describe what the
Toolkit does; [decisions](README.md) record why. Any change to a requirement or
behaviour updates its page — and adds or updates a decision note when it
settles a choice — in the same pull request. Notes use relative Markdown links
(set in `docs/.obsidian/app.json`) so the vault reads the same on GitHub.

**Why.** Decisions were scattered across pull request bodies, a private design
folder and assistant memory, where nobody else can read them.

**Consequences.** `CLAUDE.md` tells assistants to read the vault before work and
to keep it current. Machine-specific notes (paths, installs on the owner's
computer) stay out of the vault, per
[No personal data](2026-09-17-no-personal-data-in-source.md). Only
`docs/.obsidian/app.json` is committed; the rest of `.obsidian/` is personal
workspace state.
