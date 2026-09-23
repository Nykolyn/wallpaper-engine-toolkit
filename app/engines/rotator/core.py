"""Core file-system logic: scanning, rotation, duplicate handling."""
from __future__ import annotations

import os
import random
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from .config import Config, History, RunRecord


# ---- Progress event types -------------------------------------------------

class ProgressEvent:
    def __init__(self, phase: str, message: str, current: int = 0, total: int = 0,
                 level: str = "INFO"):
        self.phase = phase
        self.message = message
        self.current = current
        self.total = total
        self.level = level


ProgressCallback = Callable[[ProgressEvent], None]
CancelCheck = Callable[[], bool]


# ---- Folders that are not set -----------------------------------------------
#
# An empty setting is not "no folder". Path("") is Path("."), the working
# directory, and for the built exe that is usually the install folder: data\
# (the authors database, the Steam key, the tracker's history) and _internal\.
# With the duplicates folder left empty, the Duplicates tab listed those two as
# duplicates and "Delete all" deleted them. A relative path is the
# same hazard with a name on it. So a folder the Rotator lists or acts on is a
# full path, or it is not set, and nothing is listed, moved or deleted under it.

def folder_is_set(path: str | Path | None) -> bool:
    """Whether `path` names a folder of its own: not empty, and not relative."""
    text = "" if path is None else str(path).strip()
    return bool(text) and Path(text).is_absolute()


def folder_problem(label: str, path: str | Path | None) -> str | None:
    """Why `path` cannot be used as the `label` folder, or None when it can."""
    if folder_is_set(path):
        return None
    text = "" if path is None else str(path).strip()
    if not text:
        return f"The {label} folder is not set."
    return f"The {label} folder is not a full path: {text}"


def _child(base: Path, name: str) -> Path | None:
    """`base / name`, or None when `name` is not one folder name.

    A name with a drive or a separator in it would land outside `base` — an
    absolute one replaces it outright — and "", "." or ".." is `base` itself or
    its parent. None of those comes out of a folder listing.
    """
    if not name.strip(" .") or Path(name).name != name:
        return None
    return base / name


def _same_folder(a: str | Path, b: str | Path) -> bool:
    return (os.path.normcase(os.path.realpath(a))
            == os.path.normcase(os.path.realpath(b)))


# ---- Filesystem helpers ---------------------------------------------------

def list_subfolders(path: str | Path) -> list[str]:
    """Return names of immediate subdirectories. Empty list if the path is
    missing or not set — an unset folder is not the working directory."""
    if not folder_is_set(path):
        return []
    p = Path(path)
    if not p.exists():
        return []
    try:
        return sorted(
            (e.name for e in p.iterdir() if e.is_dir()),
            key=str.lower,
        )
    except OSError:
        return []


def folder_size(path: str | Path) -> int:
    """Total size in bytes (best-effort)."""
    total = 0
    try:
        for root, _dirs, files in __import__("os").walk(path):
            for f in files:
                try:
                    total += (Path(root) / f).stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _unique_target(folder: Path, name: str) -> Path:
    """A non-colliding path inside `folder` for `name`."""
    target = folder / name
    if not target.exists():
        return target
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return folder / f"{name}_{stamp}"


def _noop(_e: ProgressEvent) -> None:
    pass


def _never_cancel() -> bool:
    return False


# ---- Protected folders ------------------------------------------------------

# Folders in the destination whose name starts with this prefix are never
# touched by rotation: they are not returned to reserve, not treated as
# duplicates, and simply stay in myprojects across runs.
PROTECTED_PREFIX = "[protected]"


def is_protected(name: str) -> bool:
    return name.lower().startswith(PROTECTED_PREFIX)


# ---- Folders Wallpaper Engine can never show ------------------------------
#
# A wallpaper is identified by its project.json. Without one the folder cannot
# appear in the browser or in a playlist, so rotating it into myprojects only
# burns a slot — and silently: the playlist comes out short and nothing says
# why. Three shapes turn up in a real reserve. Folders holding nothing but
# Wallpaper Engine's own compiled shader cache, left behind when the wallpaper
# itself was removed. Folders left completely empty. And folders whose manifest
# is gone but whose media is still there — those are a wallpaper worth repairing
# rather than deleting, so they are reported apart from the other two and are
# not ticked by default in the confirmation.

MEDIA_SUFFIXES = {
    ".mp4", ".webm", ".mkv", ".avi", ".mov", ".m4v", ".wmv", ".flv",
    ".gif", ".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tga",
    ".pkg", ".html", ".exe", ".mp3", ".ogg", ".wav",
}
# How many entries of a broken folder to keep for the confirmation dialog.
ENTRY_CAP = 60
SHADER_DIR = "shaders"

REASON_EMPTY = "empty folder"
REASON_SHADERS = "only Wallpaper Engine's shader cache"
REASON_ORPHAN = "no project.json, but media inside"
REASON_NO_MANIFEST = "no project.json"


@dataclass
class BrokenFolder:
    """A folder Wallpaper Engine could never show, and why."""
    name: str
    reason: str
    root: str = ""                                     # which library it sits in
    entries: list[str] = field(default_factory=list)   # capped at ENTRY_CAP
    files: int = 0
    size: int = 0
    holds_media: bool = False

    @property
    def safe_to_delete(self) -> bool:
        """True when there is nothing inside that could still be a wallpaper."""
        return not self.holds_media

    @property
    def path(self) -> str:
        return str(Path(self.root) / self.name)

    @property
    def summary(self) -> str:
        return f"{self.files} file(s), {human_size(self.size)}"


def human_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} GB"


def has_manifest(folder: Path) -> bool:
    """Whether the folder holds a project.json.

    Read with scandir rather than a direct exists() on the file: the parent
    listing has just warmed the directory entries, which makes this about
    seventeen times faster over a reserve of tens of thousands of folders
    (measured: 0.9 s against 16.8 s for 33 423).
    """
    try:
        return any(e.name.lower() == "project.json" for e in os.scandir(folder))
    except OSError:
        return False


def inspect_folder(folder: Path, name: str) -> BrokenFolder | None:
    """Describe `folder` if it is unusable, or None when it is a real wallpaper."""
    if has_manifest(folder):
        return None
    entries: list[str] = []
    files = size = 0
    media = False
    only_shaders = True
    for root, _dirs, names in os.walk(folder):
        rel = Path(root).relative_to(folder)
        top = rel.parts[0] if rel.parts else None
        for n in names:
            files += 1
            if top != SHADER_DIR:
                only_shaders = False
            if Path(n).suffix.lower() in MEDIA_SUFFIXES:
                media = True
            try:
                size += (Path(root) / n).stat().st_size
            except OSError:
                pass
            if len(entries) < ENTRY_CAP:
                entries.append(n if top is None else str(rel / n))
    if files == 0:
        reason = REASON_EMPTY
    elif media:
        reason = REASON_ORPHAN
    elif only_shaders:
        reason = REASON_SHADERS
    else:
        reason = REASON_NO_MANIFEST
    return BrokenFolder(name=name, reason=reason, root=str(folder.parent),
                        entries=entries, files=files, size=size, holds_media=media)


def scan_invalid(root: str | Path, progress: ProgressCallback = _noop,
                 cancelled: CancelCheck = _never_cancel) -> list[BrokenFolder]:
    """Every folder under `root` that Wallpaper Engine could never show."""
    base = Path(root)
    names = list_subfolders(base)
    total = len(names)
    progress(ProgressEvent("scan", f"Checking {total} folders for a project.json...",
                           0, total))
    found: list[BrokenFolder] = []
    for i, name in enumerate(names, 1):
        if cancelled():
            progress(ProgressEvent("cancelled", "Cancelled — nothing was deleted.",
                                   i, total, level="WARN"))
            return found
        broken = inspect_folder(base / name, name)
        if broken is not None:
            found.append(broken)
            progress(ProgressEvent("scan", f"{name} — {broken.reason}", i, total,
                                   level="WARN"))
        elif i % 1000 == 0 or i == total:
            progress(ProgressEvent("scan", f"Checked {i} of {total}", i, total))
    progress(ProgressEvent("scan",
        f"{len(found)} of {total} folders cannot be shown by Wallpaper Engine.",
        total, total))
    return found


def delete_broken(paths: list[str], progress: ProgressCallback = _noop) -> list[str]:
    """Permanently delete the confirmed folders. Returns the paths that failed."""
    failed: list[str] = []
    total = len(paths)
    for i, path in enumerate(paths, 1):
        name = Path(path).name
        if not folder_is_set(path):
            # Only a scan of an unset library could produce one, and it would be
            # relative to the working directory.
            progress(ProgressEvent("delete", f"Not deleting {path}: not a full path",
                                   i, total, level="ERROR"))
            failed.append(path)
            continue
        if not Path(path).exists():
            # Already gone: removed by hand since the scan, or swept away with a
            # parent that was on the same list. The folder is not there, which
            # is what was asked for — not a failure to report.
            progress(ProgressEvent("delete", f"{name} was already gone", i, total))
            continue
        try:
            shutil.rmtree(path)
            progress(ProgressEvent("delete", f"Deleted {name}", i, total))
        except OSError as e:
            progress(ProgressEvent("delete", f"Failed to delete {name}: {e}", i, total,
                                   level="ERROR"))
            failed.append(path)
    return failed


# ---- The rotation ---------------------------------------------------------

class Rotator:
    def __init__(self, config: Config, history: History):
        self.config = config
        self.history = history
        # Set once a run has gone all the way through and been recorded.
        self.completed = False

    def validate(self) -> Optional[str]:
        # All three, before anything else: an unset reserve or myprojects is the
        # working directory, whose folders would be carried off by the return
        # phase, and an unset duplicates folder is where duplicates would land.
        problem = (folder_problem("reserve", self.config.source)
                   or folder_problem("myprojects", self.config.destination)
                   or folder_problem("duplicates", self.config.duplicates))
        if problem:
            return problem
        if not Path(self.config.source).exists():
            return f"Source folder not found: {self.config.source}"
        if not Path(self.config.destination).exists():
            return f"Destination folder not found: {self.config.destination}"
        return None

    def preview(self) -> dict:
        """Return counts for a confirmation dialog without moving anything."""
        in_dest_all = list_subfolders(self.config.destination)
        protected = [n for n in in_dest_all if is_protected(n)]
        in_dest = [n for n in in_dest_all if not is_protected(n)]
        in_reserve = list_subfolders(self.config.source)
        reserve_names = {n.lower() for n in in_reserve}
        dup = [n for n in in_dest if n.lower() in reserve_names]
        used = self.history.used_set()
        # available after the return phase: reserve + the non-duplicate returns
        returning = [n for n in in_dest if n.lower() not in reserve_names]
        projected_reserve = len(in_reserve) + len(returning)
        available_unique = projected_reserve - len(used)
        return {
            "in_dest": len(in_dest),
            "protected": len(protected),
            "duplicates_expected": len(dup),
            "returning": len(returning),
            "reserve_now": len(in_reserve),
            "projected_reserve": projected_reserve,
            "used": len(used),
            "available_unique": max(available_unique, 0),
            "will_reset": available_unique < self.config.count,
            "count": self.config.count,
        }

    def run(self, progress: ProgressCallback = _noop,
            cancelled: CancelCheck = _never_cancel) -> RunRecord:
        # The worker validates first; this is for any caller that does not.
        problem = self.validate()
        if problem:
            raise ValueError(problem)
        cfg = self.config
        src = Path(cfg.source)
        dst = Path(cfg.destination)
        dup_dir = Path(cfg.duplicates)
        dup_dir.mkdir(parents=True, exist_ok=True)

        self.completed = False
        record = RunRecord(
            id=uuid.uuid4().hex[:8],
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )

        # ---- Phase 1: return myprojects -> reserve (dups -> duplicates) ----
        existing_all = list_subfolders(dst)
        protected = [n for n in existing_all if is_protected(n)]
        existing = [n for n in existing_all if not is_protected(n)]
        reserve_names = {n.lower() for n in list_subfolders(src)}
        if protected:
            progress(ProgressEvent("return",
                f"Skipping {len(protected)} protected folder(s) "
                f"('{PROTECTED_PREFIX}' prefix) — left in myprojects.",
                0, len(existing), level="WARN"))
        progress(ProgressEvent("return", f"Returning {len(existing)} folders from myprojects...",
                               0, len(existing)))
        for i, name in enumerate(existing, 1):
            if cancelled():
                progress(ProgressEvent("cancelled", "Cancelled during return phase.", level="WARN"))
                return record
            srcpath = dst / name
            if name.lower() in reserve_names:
                target = _unique_target(dup_dir, name)
                progress(ProgressEvent("return",
                    f"DUPLICATE: {name} already in reserve -> duplicated_wallpapers",
                    i, len(existing), level="WARN"))
                try:
                    shutil.move(str(srcpath), str(target))
                    record.duplicates.append(name)
                except OSError as e:
                    progress(ProgressEvent("return", f"Failed to move duplicate {name}: {e}",
                                           i, len(existing), level="ERROR"))
                    record.failed.append(name)
            else:
                try:
                    shutil.move(str(srcpath), str(src / name))
                    reserve_names.add(name.lower())
                    record.returned += 1
                except OSError as e:
                    progress(ProgressEvent("return", f"Failed to return {name}: {e}",
                                           i, len(existing), level="ERROR"))
                    record.failed.append(name)
            progress(ProgressEvent("return", f"Returned {name}", i, len(existing)))

        # ---- Phase 2: compute selection -----------------------------------
        all_folders = list_subfolders(src)
        if not all_folders:
            progress(ProgressEvent("error", "No folders in reserve. Aborting.", level="ERROR"))
            return record

        used = self.history.used_set()
        available = [n for n in all_folders if n.lower() not in used]
        progress(ProgressEvent("select",
            f"Reserve: {len(all_folders)} | used: {len(used)} | unique available: {len(available)}"))

        if len(available) < cfg.count:
            progress(ProgressEvent("select",
                f"Only {len(available)} unique left (need {cfg.count}). Resetting history.",
                level="WARN"))
            record.history_reset = True
            available = all_folders

        pick = random.sample(available, min(cfg.count, len(available)))
        progress(ProgressEvent("select", f"Selected {len(pick)} folders.", 0, len(pick)))

        # ---- Phase 3: move reserve -> myprojects --------------------------
        dest_names = {n.lower() for n in list_subfolders(dst)}
        for i, name in enumerate(pick, 1):
            if cancelled():
                progress(ProgressEvent("cancelled", "Cancelled during move phase.", level="WARN"))
                break
            if name.lower() in dest_names:
                progress(ProgressEvent("move", f"SKIP: {name} already in myprojects.",
                                       i, len(pick), level="WARN"))
                record.failed.append(name)
                continue
            try:
                shutil.move(str(src / name), str(dst / name))
                record.moved.append(name)
            except OSError as e:
                progress(ProgressEvent("move", f"Failed to move {name}: {e}",
                                       i, len(pick), level="ERROR"))
                record.failed.append(name)
            progress(ProgressEvent("move", f"Moved {name}", i, len(pick)))

        progress(ProgressEvent("done",
            f"Done. Moved {record.moved_count}, returned {record.returned}, "
            f"duplicates {record.duplicate_count}, failed {len(record.failed)}."))

        self.history.add(record)
        self.completed = True
        return record


# ---- Duplicate management (used by the Duplicates tab) --------------------

def delete_folders(duplicates_dir: str, names: list[str],
                   progress: ProgressCallback = _noop) -> list[str]:
    """Permanently delete the given folders from duplicates_dir. Returns failures.

    With duplicates_dir not set, nothing is touched and every name is a failure.
    """
    problem = folder_problem("duplicates", duplicates_dir)
    if problem:
        progress(ProgressEvent("delete", f"{problem} Nothing was deleted.",
                               level="ERROR"))
        return list(names)
    base = Path(duplicates_dir)
    failed = []
    total = len(names)
    for i, name in enumerate(names, 1):
        path = _child(base, name)
        if path is None:
            progress(ProgressEvent("delete", f"Not deleting {name!r}: not a folder name",
                                   i, total, level="ERROR"))
            failed.append(name)
            continue
        try:
            shutil.rmtree(path)
            progress(ProgressEvent("delete", f"Deleted {name}", i, total))
        except OSError as e:
            progress(ProgressEvent("delete", f"Failed to delete {name}: {e}", i, total,
                                   level="ERROR"))
            failed.append(name)
    return failed


def move_replace_to_reserve(duplicates_dir: str, reserve_dir: str, names: list[str],
                            progress: ProgressCallback = _noop) -> list[str]:
    """Move folders from duplicates back to reserve, replacing any existing. Returns failures.

    With either folder not set, or both the same folder — where "replacing"
    would delete the folder about to be moved — nothing is touched and every
    name is a failure.
    """
    problem = (folder_problem("duplicates", duplicates_dir)
               or folder_problem("reserve", reserve_dir))
    if problem is None and _same_folder(duplicates_dir, reserve_dir):
        problem = "The duplicates folder is the reserve itself."
    if problem:
        progress(ProgressEvent("replace", f"{problem} Nothing was moved.",
                               level="ERROR"))
        return list(names)
    dup = Path(duplicates_dir)
    reserve = Path(reserve_dir)
    failed = []
    total = len(names)
    for i, name in enumerate(names, 1):
        srcpath = _child(dup, name)
        target = _child(reserve, name)
        if srcpath is None or target is None:
            progress(ProgressEvent("replace", f"Not moving {name!r}: not a folder name",
                                   i, total, level="ERROR"))
            failed.append(name)
            continue
        try:
            if target.exists():
                shutil.rmtree(target)
            shutil.move(str(srcpath), str(target))
            progress(ProgressEvent("replace", f"Moved & replaced {name} -> reserve", i, total))
        except OSError as e:
            progress(ProgressEvent("replace", f"Failed for {name}: {e}", i, total, level="ERROR"))
            failed.append(name)
    return failed
