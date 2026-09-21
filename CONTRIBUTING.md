# Contributing

**GitHub is the source of truth for this project.** The `main` branch is what
the project *is*; a change that only exists on someone's disk does not exist.
Every change — code, documentation, a one-line fix — arrives through a pull
request.

## The loop

```
git switch main
git pull                                  # start from what is on GitHub
git switch -c short-branch-name           # never commit to main directly

# ...make the change...

.venv\Scripts\python.exe tests\test_whatever.py     # the tests it touches
git add -A
git commit -m "Say what changed and why"
git push -u origin short-branch-name
gh pr create --fill                       # or open it on github.com
```

Then merge the pull request on GitHub, and:

```
git switch main
git pull
git branch -d short-branch-name
```

### Why not commit straight to main

Because a pull request is a place to read the change before it becomes the
truth — including your own, a day later. It also gives every change a URL, which
is what makes "when did this break?" answerable.

## Branch names

Short, lowercase, hyphenated, and about the change rather than the file:

```
tracker-web-wallpaper-credit
fix-gallery-freeze
docs-rotator-reserve-check
```

## Commit messages

A summary line in the imperative, under about 70 characters, then a blank line
and as much prose as the change deserves.

**The body is for the decision, not the diff.** The diff already says what
changed; the message is where "why this and not the obvious thing" lives.

```
Credit web wallpapers from access times only

An .html wallpaper is read once by a browser process and never held
open, so the file-handle probe can never catch one. Two of them on a
208-item playlist meant the count stalled at 206 for ever and the
"time to rotate" cue never fired.

The ten-minute access-time sweep already credits them; it just was not
running often enough to matter on a short playlist.
```

## Before you open a pull request

1. **Run the tests the change touches.** They are plain scripts:

   ```
   .venv\Scripts\python.exe tests\test_tracker.py
   ```

   Run all of them for anything that is not obviously local:

   ```
   for %f in (tests\test_*.py) do .venv\Scripts\python.exe %f
   ```

2. **Check it still imports as a whole**, since nothing here is lazy-loaded at
   test time but plenty is at runtime:

   ```
   .venv\Scripts\python.exe run_app.py --selfcheck
   ```

3. **Update the documentation in the same pull request.** A behaviour change
   with stale docs is a half-finished change. The pages are in [`docs/`](docs/).

4. **Raise the version and write it up** — see [Versions](#versions). The
   pull request cannot go green without it.

## Versions

Every pull request is a release. The version lives in one place —
`__version__` in [`app/__init__.py`](app/__init__.py) — and follows
[Semantic Versioning](https://semver.org/):

| Raise | When the change… | Example |
|---|---|---|
| **patch** `1.0.0 → 1.0.1` | fixes something, or only touches docs, tests or the build | a freeze fixed, a doc page corrected |
| **minor** `1.0.1 → 1.1.0` | adds something you can see or use | a new tab, a new setting, a new flag |
| **major** `1.1.0 → 2.0.0` | breaks what was there | a setting that no longer carries over, `data/` in a shape an older build cannot read |

In the same pull request, give [CHANGELOG.md](CHANGELOG.md) a section for the
new version, newest first, under an empty `## [Unreleased]`:

```
## [Unreleased]

## [1.0.1] - 2026-09-19

### Fixed

- **What a user would notice**, then why, in a sentence or two.
```

Use the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) headings —
Added, Changed, Deprecated, Removed, Fixed, Security — and update the link
references at the foot of the file (`[Unreleased]` compares from the new tag;
the new version gets a line of its own).

The `tests` workflow runs `tools/release.py check` on every pull request and
fails it when the version is not higher than the last release on `main`, when
it has no dated section, or when entries are left under `[Unreleased]`. Run
the same check before pushing:

```
.venv\Scripts\python.exe tools\release.py check --base origin/main
```

Once the merge is green on `main`, the workflow tags it `vX.Y.Z` and publishes a
GitHub release with that CHANGELOG section as its notes. The version also shows
in the window title, in `run_app.py --version`, at the top of
`data/selfcheck.txt`, and on the built exe's Details tab.

**Two pull requests open at once** will often pick the same number. The second
to merge rebases, takes the next number, and moves its section above the
first's.

## What must never be committed

`.gitignore` covers these, but they are worth knowing by name:

| Never | Why |
|---|---|
| `data/` | Your Steam API key (DPAPI-encrypted, still private), your authors database and its backups, your tracker history, 150 MB of cached thumbnails. |
| `app/engines/data/` | The Rotator's config and history — real folder paths from your machine. |
| `dist/`, `build/`, `.venv/` | Build output and the virtualenv. |

**No credential, hostname or personal path belongs in source.** Folder
defaults are detected from Steam's own registry entry; and the live tests skip themselves
unless an environment variable names what to talk to. If a change needs a new
one of these, add an environment variable — do not type the value into a file.

## Style

The codebase has a voice, and matching it matters more than any rule:

- **Comments explain the decision, not the syntax.** If a piece of code is
  shaped oddly, the comment says which simpler version was tried and what it
  cost.
- **Numbers are measured, not estimated.** If you write a figure in a comment or
  a doc page, it came from a run you did.
- **Nothing writes without saying what it would write.** The database plans
  first; the reserve check confirms first; a rotation states the move first.
  Keep that.
- Four-space indent, `from __future__ import annotations`, type hints on
  anything public.
- Line endings are normalised by `.gitattributes` — LF everywhere except `.cmd`
  files. Do not fight it.

## Reporting a bug

Open an issue with:

- What you expected and what happened.
- The relevant lines from `data/tracker.log` (tray and autostart problems) or
  `data/selfcheck.txt` (anything that looks like a missing dependency).
- Whether you are on a source run or a build, and the Wallpaper Engine version
  if it is a Tracker issue — the countdown reads offsets that an update can
  move.

**Redact before pasting.** Those files carry real folder paths. They never carry
credentials, but they do describe your disk.
