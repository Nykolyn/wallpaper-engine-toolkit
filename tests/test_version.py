"""The version rules, and the repository as it stands against them.

Run it directly — there is no test framework in this project:

    .venv\\Scripts\\python.exe tests\\test_version.py

The first half holds tools/release.py to what it promises with changelogs made
up for the purpose; the second checks the real app/__init__.py and CHANGELOG.md,
so a pull request that forgets to write up its version fails here as well as
in the workflow's own check.
"""
from __future__ import annotations

import sys
from pathlib import Path

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
check("the same version as before fails",
      any("raise it" in p for p in release.problems("1.1.0", GOOD, "1.1.0")))
check("a lower version fails", bool(release.problems("1.1.0", GOOD, "1.2.0")))
check("versions compare as numbers, not text (1.10.0 > 1.9.0)",
      not any("raise it" in p for p in release.problems(
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
      any("Unreleased" in p for p in release.problems(
          "1.1.0", GOOD.replace("## [Unreleased]\n", "## [Unreleased]\n\n- Forgotten.\n"),
          None)))
check("a changelog from before versioning has no release to compare with",
      release.released(HEADER + "## [Unreleased]\n\n- Something.\n") is None)
check("the newest release is the first one listed", release.released(GOOD) == "1.1.0")

notes = release.notes("1.1.0", GOOD)
check("release notes are the section's body alone",
      notes == "### Added\n\n- A thing.")
check("and the link references at the foot are not part of the last section",
      "example.net" not in release.notes("1.0.0", GOOD))

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
found = release.problems(__version__, changelog, None)
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
