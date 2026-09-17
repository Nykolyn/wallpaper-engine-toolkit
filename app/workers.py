"""Qt signal bridges for the callback-based Copier and Creator engines.

The original ``CopyEngine`` and ``BuildEngine`` already manage their own worker
threads and report progress through plain callables. We don't touch them — we
just hand them bound signal emitters. Qt delivers cross-thread signals via a
queued connection, so the UI is updated safely on the main thread.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class CopierBridge(QObject):
    """Signals mirroring CopyEngine's callbacks (log / progress / speed / finished)."""

    log = Signal(str)
    progress = Signal(int, int)      # done, total
    speed = Signal(float)            # MB/s
    finished = Signal(object)        # report: list[dict]


class CreatorBridge(QObject):
    """Signals mirroring BuildEngine's callbacks (log / progress / item_done / finished)."""

    log = Signal(str)
    progress = Signal(int, int)      # done, total
    item_done = Signal(str, str)     # name, status
    finished = Signal(object)        # report: list[dict]
    # Extra channel for thumbnails loaded off-thread by the Creator tab.
    thumb = Signal(str, object)      # basename, QImage
