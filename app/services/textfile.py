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


def last_lines(f, end: int, n: int, block: int = BLOCK) -> tuple[list[str], int]:
    """The last `n` whole lines of an open binary file before byte `end`, oldest
    first and empty ones left out, and the offset just past the last of them.

    A line not ended by a newline yet is still being written: it is left for
    the next read, which starts at the offset returned.
    """
    pos, data = end, b""
    while pos > 0 and data.count(b"\n") <= n:
        step = min(block, pos)
        pos -= step
        f.seek(pos)
        data = f.read(step) + data
    cut = data.rfind(b"\n")
    if cut < 0:
        return [], pos
    lines = [_text(line) for line in data[:cut].split(b"\n")]
    if pos > 0:
        lines = lines[1:]               # the first may have begun before `pos`
    lines = [line for line in lines if line.strip()]
    return lines[-n:] if n > 0 else [], pos + cut + 1


def text(raw: bytes) -> str:
    """A line's bytes as text: not UTF-8 is replaced, and a CR at the end dropped."""
    return _text(raw)


def _text(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace").rstrip("\r")
