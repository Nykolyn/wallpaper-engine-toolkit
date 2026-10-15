"""Current wallpaper titles and file sizes, read once per session off the GUI thread."""
from __future__ import annotations

import stat
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from .engines.wallpaper_meta import read_meta


def read_current(item: str, title: str = "") -> dict:
    meta = read_meta(item)
    size = None
    try:
        info = Path(item).stat()
        if stat.S_ISREG(info.st_mode):
            size = info.st_size
    except OSError:
        pass
    return {"title": (meta.title if meta.readable else title or meta.title), "size": size}


class WidgetMetadata(QObject):
    updated = Signal()
    _arrived = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.data: dict[str, dict] = {}
        self._wanted: dict[str, str] = {}
        self._busy = False
        self._stopped = threading.Event()
        self.destroyed.connect(lambda: self._stopped.set())
        self._arrived.connect(self._receive)

    def request(self, results) -> None:
        self._wanted = {p.current: getattr(p, "current_title", "") for p in results
                        if getattr(p, "current", None)}
        self._start()

    def _start(self) -> None:
        if self._busy or self._stopped.is_set():
            return
        todo = {item: title for item, title in self._wanted.items() if item not in self.data}
        if not todo:
            return
        self._busy = True
        threading.Thread(target=self._read, args=(todo,), daemon=True,
                         name="widget metadata").start()

    def _read(self, todo: dict[str, str]) -> None:
        values = {}
        for item, title in todo.items():
            if self._stopped.is_set():
                return
            values[item] = read_current(item, title)
        if not self._stopped.is_set():
            try:
                self._arrived.emit(values)
            except RuntimeError:  # the application closed during a disk read
                pass

    def _receive(self, values: dict) -> None:
        self._busy = False
        if self._stopped.is_set():
            return
        self.data.update(values)
        self.updated.emit()
        # A wallpaper may have changed while the sleeping disk was waking.
        self._start()

    def stop(self) -> None:
        self._stopped.set()
