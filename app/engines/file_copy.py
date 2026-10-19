"""Verified filesystem copying used by Cleaner and Reserve."""
from __future__ import annotations

import os
import json
import shutil
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

COPY_BYTES_PER_SECOND = 16 * 1024 * 1024


@contextmanager
def background_io():
    """Lower this worker's CPU, disk and memory priority; preserve outer scopes."""
    from .we_memory import background_mode
    changed = background_mode(True)
    try:
        yield
    finally:
        if changed:
            background_mode(False)


def _raise(error):
    raise error


def manifest(folder: str) -> tuple[dict[str, int], list[str]]:
    """Do not silently turn an unreadable folder into an empty successful copy."""
    if not os.path.isdir(folder):
        raise FileNotFoundError(f"Source folder not found: {folder}")
    if os.path.islink(folder) or os.path.isjunction(folder):
        raise ValueError(f"Linked source folders are not supported: {folder}")
    files, directories = {}, []
    for root, dirs, names in os.walk(folder, onerror=_raise):
        for name in dirs + names:
            full = os.path.join(root, name)
            if os.path.islink(full) or os.path.isjunction(full):
                raise ValueError(f"Linked files or folders are not supported: {full}")
        directories.extend(os.path.relpath(os.path.join(root, d), folder) for d in dirs)
        for name in names:
            full = os.path.join(root, name)
            files[os.path.relpath(full, folder)] = os.path.getsize(full)
    return files, directories


def destination_info(destination: str) -> dict:
    """Find the actual filesystem behind the nearest existing ancestor, on a worker."""
    if not destination or not destination.strip():
        raise ValueError("Choose a destination folder")
    ancestor = Path(destination).absolute()
    while not ancestor.exists():
        parent = ancestor.parent
        if parent == ancestor:
            raise FileNotFoundError(f"Destination drive not found: {destination}")
        ancestor = parent
    if not ancestor.is_dir():
        raise NotADirectoryError(f"Destination is not a folder: {ancestor}")
    usage = shutil.disk_usage(ancestor)
    return dict(drive=ancestor.anchor or str(ancestor), device=ancestor.stat().st_dev,
                free=usage.free, path=destination)


def discard_output(output, target):
    resolved, parent = os.path.realpath(output), os.path.realpath(target)
    if os.path.dirname(resolved) != parent or os.path.islink(output) or os.path.isjunction(output):
        raise OSError("Partial output changed location; cleanup refused")
    shutil.rmtree(output)


def verify_copy(source, output, expected):
    current, _ = manifest(source)
    actual, _ = manifest(output)
    if current != expected:
        raise OSError("Verification failed: the source changed during copying")
    if actual.keys() != expected.keys():
        raise OSError(f"Verification failed: file list differs ({len(actual)} of {len(expected)} files)")
    for name, size in expected.items():
        if actual[name] != size:
            raise OSError(f"Verification failed: size differs for {name} ({actual[name]} of {size} bytes)")


def wallpaper_project(folder, files):
    """A readable project whose nonempty payload belongs to this folder."""
    project = json.loads((Path(folder) / "project.json").read_text(encoding="utf-8-sig"))
    if not isinstance(project, dict):
        raise ValueError("project.json is not an object.")
    if str(project.get("type", "")).strip().lower() not in ("video", "scene", "web", "application"):
        raise ValueError("This project is not a supported wallpaper type.")
    payload = project.get("file")
    sizes = {os.path.normcase(name): size for name, size in files.items()}
    if (not isinstance(payload, str) or not payload
            or sizes.get(os.path.normcase(os.path.normpath(payload)), 0) <= 0):
        raise ValueError("The wallpaper's content file is missing or empty.")
    return project


@background_io()
def copy_to_reserve(folder, reserve, *, on_progress=None, cancelled=lambda: False):
    """Publish a verified copy without overwriting; leave the source alone."""
    if not reserve or not reserve.strip():
        raise ValueError("Set the reserve folder in Settings first.")
    source, root = Path(folder).resolve(), Path(reserve).resolve()
    if source == root or source in root.parents or root in source.parents:
        raise ValueError("The reserve must be separate from the Workshop folder.")
    files, _ = manifest(folder)
    wallpaper_project(source, files)
    total = sum(files.values())
    if destination_info(str(root))["free"] < total:
        raise OSError("There is not enough free space in the reserve.")
    if cancelled():
        raise InterruptedError("Copy stopped.")
    root.mkdir(parents=True, exist_ok=True)
    target = root / source.name
    if target.exists():
        raise FileExistsError(f"The reserve already contains {target.name}; nothing was replaced.")
    # Outside the reserve, so a concurrent library scan cannot see a partial wallpaper.
    staging = Path(tempfile.mkdtemp(prefix=".toolkit-reserve-", dir=root.parent))
    done, last_report = 0, 0.0

    def copy_file(src, dst):
        nonlocal done, last_report
        with open(src, "rb") as incoming, open(dst, "wb") as outgoing:
            while True:
                if cancelled():
                    raise InterruptedError("Copy stopped.")
                started = time.monotonic()
                chunk = incoming.read(1024 * 1024)
                if not chunk:
                    break
                outgoing.write(chunk)
                done += len(chunk)
                # Keep a large copy from monopolizing the disk used by the game.
                until = started + len(chunk) / COPY_BYTES_PER_SECOND
                while (delay := until - time.monotonic()) > 0:
                    if cancelled():
                        raise InterruptedError("Copy stopped.")
                    time.sleep(min(.05, delay))
                now = time.monotonic()
                if on_progress and now - last_report >= .1:
                    on_progress(done, total)
                    last_report = now
        shutil.copystat(src, dst)
        return dst

    try:
        if on_progress:
            on_progress(0, total)
        shutil.copytree(source, staging, dirs_exist_ok=True, copy_function=copy_file)
        verify_copy(str(source), str(staging), files)
        wallpaper_project(staging, files)
        if cancelled():
            raise InterruptedError("Copy stopped.")
        os.rename(staging, target)       # Windows refuses an existing destination
    except Exception as err:
        try:
            discard_output(str(staging), str(root.parent))
        except OSError as cleanup:
            raise OSError(f"{err}; partial copy remains at {staging}: {cleanup}") from err
        if cancelled():
            raise InterruptedError("Copy stopped.") from err
        raise
    if on_progress:
        on_progress(total, total)
    return str(target)
