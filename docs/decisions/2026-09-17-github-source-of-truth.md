---
date: 2026-09-17
status: accepted
source: "owner, in chat"
tags: [process]
---

# GitHub is where the project lives; every change is a pull request

**Decision.** The public repository's `main` is what the project is. Every
change — code, docs, a one-line fix — arrives through a pull request that is
squash-merged and its branch deleted. Nothing is committed to `main` directly
(the Release workflow's version commit is the one exception). Work starts from
`origin/main` after a fetch, never from a local branch of the same name.
Merging is the owner's call.

**Why.** A change that exists only on a disk is not done. A pull request is a
place to read the change before it becomes the truth and gives it a URL, which
is what makes "when did this break?" answerable. A stale local `main` once
produced a pull request that conflicted with four merged ones.

**Consequences.** Parallel sessions work in their own worktrees, never by
switching the branch of the shared checkout. The full loop is in
[CONTRIBUTING.md](../../CONTRIBUTING.md).
