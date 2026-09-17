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

4. **Add a line to [CHANGELOG.md](CHANGELOG.md)** under `## [Unreleased]` for
   anything a user would notice.

## What must never be committed

`.gitignore` covers these, but they are worth knowing by name:

| Never | Why |
|---|---|
| `data/` | Your Steam API key and database URI (DPAPI-encrypted, still private), your tracker history, 150 MB of cached thumbnails. |
| `app/engines/data/` | The Rotator's config and history — real folder paths from your machine. |
| `dist/`, `build/`, `.venv/` | Build output and the virtualenv. |

**No credential, hostname or personal path belongs in source.** The MongoDB
cluster comes from `DB_CLUSTER` or `WET_DB_CLUSTER`; folder defaults are
detected from Steam's own registry entry; and the live tests skip themselves
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
