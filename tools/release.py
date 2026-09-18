"""The version: where it lives, what CI demands of it, and what a release says.

The one place the version is written is `__version__` in `app/__init__.py`.
Everything else reads it from there — the window title, `--version`, the
exe's file properties, the git tag and the GitHub release — so they cannot
disagree.

Every pull request is a release: it raises the version and gives CHANGELOG.md
a section `## [X.Y.Z] - YYYY-MM-DD` for it. `check` is what makes that
mandatory rather than a good intention; the tests workflow runs it on every
pull request.

    python tools/release.py check [--base REF]   # the PR raised the version and wrote it up
    python tools/release.py version              # print the version
    python tools/release.py notes [VERSION]      # the CHANGELOG section, for a release body
    python tools/release.py version-file OUT     # PyInstaller's Windows version resource
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INIT = ROOT / "app" / "__init__.py"
CHANGELOG = ROOT / "CHANGELOG.md"

SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
VERSION_LINE = re.compile(r'^__version__\s*=\s*"([^"]*)"', re.M)
SECTION = re.compile(r"^## \[([^\]]+)\](.*)$", re.M)
DATED = re.compile(r"^ - \d{4}-\d{2}-\d{2}$")


def version_in(text: str) -> str:
    match = VERSION_LINE.search(text)
    if not match:
        raise ValueError("no __version__ line in app/__init__.py")
    return match.group(1)


def parse(version: str) -> tuple[int, int, int]:
    match = SEMVER.match(version)
    if not match:
        raise ValueError(f"{version!r} is not MAJOR.MINOR.PATCH")
    return tuple(int(part) for part in match.groups())      # type: ignore[return-value]


def current() -> str:
    return version_in(INIT.read_text(encoding="utf-8"))


def sections(changelog: str) -> list[tuple[str, str, str]]:
    """(name, the rest of the heading line, body) for every `## [...]` section."""
    found = list(SECTION.finditer(changelog))
    out = []
    for i, match in enumerate(found):
        end = found[i + 1].start() if i + 1 < len(found) else len(changelog)
        body = changelog[match.end():end]
        # Link references at the foot of the file belong to no section.
        body = re.split(r"^\[[^\]]+\]: ", body, maxsplit=1, flags=re.M)[0]
        out.append((match.group(1), match.group(2), body.strip()))
    return out


def notes(version: str, changelog: str | None = None) -> str:
    changelog = CHANGELOG.read_text(encoding="utf-8") if changelog is None else changelog
    for name, _, body in sections(changelog):
        if name == version:
            return body
    raise ValueError(f"CHANGELOG.md has no section for {version}")


def problems(version: str, changelog: str, base_version: str | None = None) -> list[str]:
    """Everything wrong with this version and its write-up; empty when it is releasable."""
    found = []
    try:
        mine = parse(version)
    except ValueError as err:
        return [str(err)]
    if base_version is not None:
        try:
            if mine <= parse(base_version):
                found.append(f"the version is still {version}; it was {base_version} before "
                             "this change — raise it in app/__init__.py")
        except ValueError:
            pass            # the base predates versioning; any valid version will do
    listed = sections(changelog)
    names = [name for name, _, _ in listed]
    released = [entry for entry in listed if entry[0] != "Unreleased"]
    if version not in names:
        found.append(f"CHANGELOG.md has no '## [{version}] - YYYY-MM-DD' section")
    elif released[0][0] != version:
        found.append(f"'## [{version}]' must be the newest release in CHANGELOG.md, "
                     f"above {released[0][0]}")
    else:
        heading, body = released[0][1], released[0][2]
        if not DATED.match(heading):
            found.append(f"'## [{version}]' needs its date: '## [{version}] - YYYY-MM-DD'")
        if not re.search(r"^- ", body, re.M):
            found.append(f"'## [{version}]' in CHANGELOG.md lists no changes")
    for name, _, body in listed:
        if name == "Unreleased" and re.search(r"^- ", body, re.M):
            found.append("entries are still under '## [Unreleased]' — move them into "
                         f"'## [{version}]'")
    return found


def released(changelog: str) -> str | None:
    """The newest version CHANGELOG.md records as released, if any."""
    for name, _, _ in sections(changelog):
        if name != "Unreleased":
            return name
    return None


def base_version(ref: str) -> str | None:
    """The newest release on `ref`, by its CHANGELOG.

    Read from the changelog rather than `__version__`, because a version only
    counts once it has been written up: app/__init__.py said 1.0.0 for months
    before anything was released as 1.0.0.
    """
    try:
        text = subprocess.run(["git", "show", f"{ref}:CHANGELOG.md"], cwd=ROOT,
                              capture_output=True, text=True, encoding="utf-8",
                              check=True).stdout
    except subprocess.CalledProcessError:
        return None
    return released(text)


def version_file(version: str) -> str:
    """The VSVersionInfo PyInstaller turns into the exe's Details tab."""
    major, minor, patch = parse(version)
    numbers = f"({major}, {minor}, {patch}, 0)"
    strings = {
        "CompanyName": "Andrii Nykolyn",
        "FileDescription": "Wallpaper Engine Toolkit",
        "FileVersion": version,
        "InternalName": "WallpaperEngineToolkit",
        "LegalCopyright": "MIT License",
        "OriginalFilename": "WallpaperEngineToolkit.exe",
        "ProductName": "Wallpaper Engine Toolkit",
        "ProductVersion": version,
    }
    entries = ",\n".join(f"          StringStruct({k!r}, {v!r})" for k, v in strings.items())
    return f"""# Generated by tools/release.py from app/__init__.py — do not edit.
VSVersionInfo(
  ffi=FixedFileInfo(filevers={numbers}, prodvers={numbers},
                    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1,
                    subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
{entries}
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="fail unless the version is releasable")
    check.add_argument("--base", help="git ref the version must be higher than")
    sub.add_parser("version", help="print the version")
    notes_cmd = sub.add_parser("notes", help="print a version's CHANGELOG section")
    notes_cmd.add_argument("version", nargs="?")
    out = sub.add_parser("version-file", help="write PyInstaller's version resource")
    out.add_argument("path")
    args = parser.parse_args(argv)

    # Console code pages on Windows cannot always encode the changelog's dashes.
    sys.stdout.reconfigure(encoding="utf-8")
    version = current()
    if args.command == "version":
        print(version)
    elif args.command == "notes":
        print(notes(args.version or version))
    elif args.command == "version-file":
        path = Path(args.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(version_file(version), encoding="utf-8")
    else:
        base = base_version(args.base) if args.base else None
        found = problems(version, CHANGELOG.read_text(encoding="utf-8"), base)
        for problem in found:
            print(f"::error::{problem}" if "GITHUB_ACTIONS" in os.environ
                  else f"error: {problem}")
        if found:
            print("Every pull request raises the version and writes it up; "
                  "see CONTRIBUTING.md#versions.")
            return 1
        print(f"version {version}" + (f", up from {base}" if base else "") + ": ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
