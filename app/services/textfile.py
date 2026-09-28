"""Reading a text file from its end, for logs and the activity journal.

Both only ever want the last few lines of a file that grows for weeks, so
they read it backwards a block at a time and stop as soon as they have
enough; a 2 MB journal costs the same to tail as a 2 KB one.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

BLOCK = 64 * 1024


def reverse_lines(path: str | Path, block: int = BLOCK) -> Iterator[str]:
    """Every line of the file, the last first, without its line ending.

    A file that cannot be opened has no lines. Bytes that are not UTF-8 are
    replaced, not raised: one damaged line must not hide the rest. Empty lines
    come through as "" for the caller to skip.
    """
    try:
        f = open(path, "rb")
    except OSError:
        return
    with f:
        pos = f.seek(0, os.SEEK_END)
        rest = b""
        while pos > 0:
            step = min(block, pos)
            pos -= step
            f.seek(pos)
            lines = (f.read(step) + rest).split(b"\n")
            rest = lines.pop(0)         # the start of it may be in the block before
            for line in reversed(lines):
                yield _text(line)
        if rest:
            yield _text(rest)


def tail(path: str | Path, n: int) -> list[str]:
    """The last `n` lines that are not empty, oldest first."""
    found: list[str] = []
    if n <= 0:
        return found
    for line in reverse_lines(path):
        if line.strip():
            found.append(line)
            if len(found) >= n:
                break
    found.reverse()
    return found


def _text(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace").rstrip("\r")
