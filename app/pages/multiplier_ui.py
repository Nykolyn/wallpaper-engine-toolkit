"""Quota cells and off-thread persistence shared by Tracker and Rotator."""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from ..engines.multipliers import MultiplierStore
from ..engines.rotator.config import read_runs
from ..ui.kit.tables import Cell, SelectCell

VALUES = tuple(range(1, 101)) + (200, 500, 1000)


def multiplier_cell(usage, name: str, *, enabled: bool = True) -> SelectCell | Cell:
    return SelectCell(usage.multiplier(name), VALUES, enabled) if usage is not None else Cell("—", "text.lo")


class MultiplierEdits(QObject):
    changed = Signal(str, int)
    updated = Signal(object)

    def __init__(self, store, offload, say, parent=None):
        super().__init__(parent)
        self.store = store
        self._offload, self._say = offload, say
        self.runs = []
        self.pending: set[str] = set()
        self.ready = False
        self._generation = 0
        self._problem = ""

    def _snapshot(self) -> None:
        try:
            usage = self.store.usage(self.runs) if self.store is not None else None
        except Exception as err:  # noqa: BLE001 — damaged metadata disables edits
            self.ready = False
            self._failed(err)
            usage = None
        self.updated.emit(usage)

    def _failed(self, error) -> None:
        problem = str(error)
        if problem != self._problem:
            self._say("danger", f"Multipliers could not be read: {problem}")
        self._problem = problem

    def refresh(self, runs=None) -> None:
        self._generation += 1
        generation = self._generation
        if runs is not None:
            self.runs = list(runs)
        if self.store is not None and self.ready:
            self._snapshot()

        def read():
            if runs is not None:
                found = list(runs)
            else:
                try:
                    found = read_runs()
                except FileNotFoundError:
                    found = []
            store = self.store or MultiplierStore()
            store.reload()
            return store, found

        def done(result):
            if generation != self._generation:
                return
            if isinstance(result, Exception):
                self.ready = False
                self.updated.emit(None)
                self._failed(result)
                return
            self.store, self.runs = result
            self.ready = True
            self._problem = ""
            self._snapshot()

        self._offload.run(read, done)

    def set(self, name: str, value: int) -> None:
        if name in self.pending or not self.ready:
            return
        self.pending.add(name)
        self._snapshot()

        def done(result):
            self.pending.discard(name)
            self._snapshot()
            if isinstance(result, Exception):
                self._say("danger", f"Multiplier for {name} could not be saved: {result}")
            else:
                self.changed.emit(name, value)

        self._offload.run(lambda: self.store.set_multiplier(name, value), done)


class FixtureQuota:
    def __init__(self, values: dict[str, int], used: dict[str, int]):
        self.values, self.counts = values, used

    def multiplier(self, name: str) -> int:
        return self.values.get(name, 1)

    def remaining(self, name: str) -> int:
        return max(self.multiplier(name) - self.counts.get(name, 0), 0)
