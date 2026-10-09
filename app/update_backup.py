"""A copy of the data taken before an update, so a new version cannot lose it.

The installer replaces the program and never touches the data folder (see
``data_location``). But the new version is what reads that data next, and a
mistake in it could still damage what it reads. So before the new version does
anything else, the installer has it copy the data aside, into
``update_backup\\<date time> before <version>\\``: every file compared with its
original (size and SHA-256), and a ``manifest.json`` listing what is there.

What is left out can be had again or is a backup already: ``thumbs\\`` (the
previews, downloaded or read again when they are shown — a gigabyte on a big
library), the logs, and the backups the app keeps of its own
(``authors_backup\\``, ``history_backup\\``, and these). What is copied —
settings, the Steam key, the authors database, the Rotator's history, the
Tracker's state, the caches that take minutes to build — comes to a few MB.

The newest ``KEEP`` are kept; older ones go to the Recycle Bin, never deleted
outright.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

FOLDER = "update_backup"
KEEP = 5
LEFT_OUT = ("thumbs", "logs", "authors_backup", "history_backup", FOLDER)
PARTIAL = ".partial"
MANIFEST = "manifest.json"


def wanted(relative: Path) -> bool:
    """Whether a file of the data folder (by its path inside it) is copied."""
    return relative.parts[0] not in LEFT_OUT and relative.suffix.lower() != ".log"


def take(data: Path, label: str, *, now: datetime | None = None,
         recycle=None) -> tuple[Path, str]:
    """Copy `data` aside under `label`; return (the copy, what happened in words).

    Raises OSError when the copy cannot be made or does not match its
    original; whatever was half made is removed again, and `data` itself is
    only ever read.
    """
    from . import __version__
    from .data_location import _sha256, to_recycle_bin

    recycle = recycle or to_recycle_bin
    now = now or datetime.now()
    root = data / FOLDER
    name = f"{now:%Y-%m-%d %H%M%S} {label}"
    dest = root / name
    number = 2
    while dest.exists():
        dest = root / f"{name} ({number})"
        number += 1
    staging = dest.with_name(dest.name + PARTIAL)

    root.mkdir(parents=True, exist_ok=True)
    for left in root.glob("*" + PARTIAL):
        shutil.rmtree(left, ignore_errors=True)     # a copy that died half way; ours
    listed = []
    size = 0
    try:
        for original in sorted(data.rglob("*")):
            relative = original.relative_to(data)
            if not original.is_file() or not wanted(relative):
                continue
            copy = staging / relative
            copy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, copy)
            digest = _sha256(original)
            if copy.stat().st_size != original.stat().st_size or _sha256(copy) != digest:
                raise OSError(f"{copy} does not match {original}")
            listed.append({"file": relative.as_posix(), "bytes": copy.stat().st_size,
                           "sha256": digest})
            size += copy.stat().st_size
        (staging / MANIFEST).write_text(json.dumps({
            "what": "The data of Wallpaper Engine Toolkit, copied aside before an update. "
                    "To go back to it, quit the toolkit and copy these files over the "
                    "ones in the data folder.",
            "taken": now.strftime("%Y-%m-%d %H:%M:%S"),
            "label": label,
            "by_version": __version__,
            "from": str(data),
            "left_out": [*LEFT_OUT, "*.log"],
            "files": listed,
        }, indent=2), encoding="utf-8")
        staging.rename(dest)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    said = (f"data copied aside to {dest}: {len(listed)} files, "
            f"{size / 1_048_576:.1f} MB, each one verified")
    pruned = prune(root, recycle)
    if pruned:
        said += f"; {pruned}"
    return dest, said


def prune(root: Path, recycle) -> str:
    """Send all but the newest KEEP copies to the Recycle Bin. Says what it did."""
    copies = sorted(p for p in root.iterdir()
                    if p.is_dir() and not p.name.endswith(PARTIAL))
    old = copies[:-KEEP] if len(copies) > KEEP else []
    stuck = [p.name for p in old if not recycle(p)]
    if not old:
        return ""
    if stuck:
        return (f"{len(old) - len(stuck)} older copies went to the Recycle Bin, "
                f"{len(stuck)} could not and are still there")
    return f"{len(old)} older {'copy' if len(old) == 1 else 'copies'} went to the Recycle Bin"
