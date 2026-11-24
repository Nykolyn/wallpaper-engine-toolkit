# CLAUDE.md

## The Obsidian vault is the source of truth

`docs/` is an Obsidian vault and the source of truth for this project: what the
Toolkit does (the feature pages), and why it is built the way it is
([`docs/decisions/`](docs/decisions/README.md)). Code follows the vault; when
they disagree, the vault says what was meant, and the other side is the bug —
or the vault is out of date, which is fixed in the same change.

**Before work:** read [`docs/README.md`](docs/README.md), the pages for the area
you touch, and the decision notes tagged with it. Do not re-open an accepted
decision unless the owner asks; if a request contradicts one, say which note
and ask.

**With every change, in the same pull request:**

- A requirement, behaviour, setting, default or file location that changes →
  update its page in `docs/`.
- A choice settled (by the owner in chat, by measurement, or by a trade-off in
  the code) → add a note `docs/decisions/YYYY-MM-DD-short-name.md` in the
  format in [`docs/decisions/README.md`](docs/decisions/README.md) and a row in
  its table. A decision that changes → mark the old note `superseded` with a
  link to the new one; never delete it.
- A new page → link it from `docs/README.md`.

Write notes with relative Markdown links (`[Rotator](../rotator.md)`), not
`[[wikilinks]]`, so they read the same on GitHub. Images go in `docs/images/`.
Commit only `docs/.obsidian/app.json`; the rest of `.obsidian/` is personal.

**Never in the vault:** credentials, personal paths, account names or anything
else from the owner's machine — the repository is public. Machine-specific
knowledge belongs in private assistant memory, not here. See
[No personal data](docs/decisions/2026-09-17-no-personal-data-in-source.md).

## Working rules

- Every change goes through a pull request from a branch off `origin/main`;
  merging is the owner's call. [CONTRIBUTING.md](CONTRIBUTING.md) has the loop.
- Changes are written up under `## [Unreleased]` in CHANGELOG.md; the version is
  not raised outside a release. See
  [Releases are made by hand](docs/decisions/2026-10-23-manual-releases.md).
- Build only through `build.cmd`; never `pyinstaller` directly. See
  [Data](docs/decisions/2026-09-26-data-outside-program-folder.md).
- Python is the project's `.venv\Scripts\python.exe`; tests are plain scripts in
  `tests\`, and `run_app.py --selfcheck` checks the whole import.
