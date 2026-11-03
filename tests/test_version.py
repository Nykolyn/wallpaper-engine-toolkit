"""The version rules, and the repository as it stands against them.

Run it directly — there is no test framework in this project:

    .venv\\Scripts\\python.exe tests\\test_version.py

The first half holds tools/release.py to what it promises with changelogs made
up for the purpose; the second checks the real app/__init__.py and CHANGELOG.md,
so a release that forgets to write up its version fails here as well as in
the workflow's own check.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import release       # noqa: E402

results: list[bool] = []


def check(label: str, condition: bool) -> None:
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label)


HEADER = "# Changelog\n\nSome words.\n\n"
GOOD = HEADER + """## [Unreleased]

## [1.1.0] - 2026-10-01

### Added

- A thing.

## [1.0.0] - 2026-09-18

### Fixed

- An older thing.

[Unreleased]: https://example.net/compare/v1.1.0...HEAD
[1.1.0]: https://example.net/tag/v1.1.0
"""

print("-- the rules --")
check("a written-up release that is higher than the last passes",
      release.problems("1.1.0", GOOD, "1.0.0") == [])
PENDING = GOOD.replace("## [Unreleased]\n", "## [Unreleased]\n\n- Waiting.\n")
check("an unreleased change written up under Unreleased passes",
      release.problems("1.1.0", PENDING, "1.1.0") == [])
check("an unreleased change with nothing under Unreleased fails",
      any("Unreleased" in p for p in release.problems("1.1.0", GOOD, "1.1.0")))
check("a lower version fails", bool(release.problems("1.1.0", GOOD, "1.2.0")))
check("versions compare as numbers, not text (1.10.0 > 1.9.0)",
      not any("lower" in p for p in release.problems(
          "1.10.0", GOOD.replace("1.1.0", "1.10.0"), "1.9.0")))
check("a version that is not MAJOR.MINOR.PATCH fails",
      bool(release.problems("1.1", GOOD, None)))
check("a version with no CHANGELOG section fails",
      any("no '## [1.2.0]" in p for p in release.problems("1.2.0", GOOD, "1.1.0")))
check("a section that is not the newest fails",
      any("newest" in p for p in release.problems("1.0.0", GOOD, None)))
check("a section without its date fails",
      any("date" in p for p in release.problems(
          "1.1.0", GOOD.replace("## [1.1.0] - 2026-10-01", "## [1.1.0]"), None)))
check("an empty section fails",
      any("lists no changes" in p for p in release.problems(
          "1.1.0", GOOD.replace("- A thing.\n", ""), None)))
check("entries left under Unreleased fail",
      any("Unreleased" in p for p in release.problems("1.1.0", PENDING, None)))
check("unless they may wait for the next release",
      release.problems("1.1.0", PENDING, None, unreleased_ok=True) == [])
check("a changelog from before versioning has no release to compare with",
      release.released(HEADER + "## [Unreleased]\n\n- Something.\n") is None)
check("the newest release is the first one listed", release.released(GOOD) == "1.1.0")

notes = release.notes("1.1.0", GOOD)
check("release notes are the section's body alone",
      notes == "### Added\n\n- A thing.")
check("and the link references at the foot are not part of the last section",
      "example.net" not in release.notes("1.0.0", GOOD))

print("-- manual release preparation --")
check("a manual release can prepare pending changes", hasattr(release, "prepare"))
if hasattr(release, "prepare"):
    version, prepared = release.prepare("1.1.0", PENDING, "2026-11-03")
    check("pending entries advance the patch version", version == "1.1.1")
    check("the new version is ready for the existing release check",
          release.problems(version, prepared) == [])
    check("pending notes become the dated release, preserving old notes",
          "## [1.1.1] - 2026-11-03" in prepared
          and release.notes(version, prepared) == "- Waiting."
          and release.notes("1.1.0", prepared) == release.notes("1.1.0", GOOD))
    check("preparation updates comparison links",
          "[Unreleased]: https://example.net/compare/v1.1.1...HEAD" in prepared
          and "[1.1.1]: https://example.net/compare/v1.1.0...v1.1.1" in prepared)
    check("retrying an unfinished release keeps the same version and notes",
          release.prepare(version, prepared, "2026-11-04") == (version, prepared))
    check("running without pending changes leaves the release unchanged",
          release.prepare("1.1.0", GOOD, "2026-11-03") == ("1.1.0", GOOD))
    try:
        release.prepare("1.2.0", PENDING, "2026-11-03")
        refused = False
    except ValueError:
        refused = True
    check("invalid existing release metadata is refused before editing", refused)
    try:
        release.prepare("1.1.0", PENDING.replace("compare/v1.1.0...HEAD", "compare/v1.0.0...HEAD"), "2026-11-03")
        refused = False
    except ValueError:
        refused = True
    check("a mismatched comparison link is refused", refused)
    with tempfile.TemporaryDirectory(prefix="release-test-") as folder:
        init, log = Path(folder) / "__init__.py", Path(folder) / "CHANGELOG.md"
        init.write_text('# Keep this comment.\n__version__ = "1.1.0"\n', encoding="utf-8")
        log.write_text(PENDING, encoding="utf-8")
        with patch.object(release, "INIT", init), patch.object(release, "CHANGELOG", log):
            check("the prepare command updates both files", release.main(["prepare"]) == 0
                  and release.current() == "1.1.1"
                  and release.problems(release.current(), log.read_text(encoding="utf-8")) == [])
            before = init.read_bytes(), log.read_bytes()
            check("repeating the command does not rewrite prepared files",
                  release.main(["prepare"]) == 0 and before == (init.read_bytes(), log.read_bytes()))
            check("preparing retains the rest of the version module", "# Keep this comment." in init.read_text())

resource = release.version_file("2.3.4")
check("the exe's version resource carries the numbers",
      "filevers=(2, 3, 4, 0)" in resource and "'ProductVersion', '2.3.4'" in resource)
try:
    compile(resource, "version_info.txt", "eval")
    parses = True
except SyntaxError:
    parses = False
check("and is a Python expression, which is how PyInstaller reads it", parses)

print("-- this repository --")
from app import __version__      # noqa: E402

changelog = release.CHANGELOG.read_text(encoding="utf-8")
# Merged changes may wait under Unreleased for the next release.
found = release.problems(__version__, changelog, None, unreleased_ok=True)
for problem in found:
    print("     " + problem)
check(f"app/__init__.py's {__version__} is written up in CHANGELOG.md", not found)
check("the version tool reads the same version the app does",
      release.current() == __version__)
check(f"the Unreleased link compares from v{__version__}",
      f"compare/v{__version__}...HEAD" in changelog)
check(f"and v{__version__} has a link of its own",
      f"[{__version__}]: https://github.com/" in changelog)

print()
print("PASSED %d/%d" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
