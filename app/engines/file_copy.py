"""Verified filesystem copying used by Cleaner."""
from __future__ import annotations

import os
import shutil
from pathlib import Path


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
