"""Wallpaper quotas, keyed by folder identity across reserve/protection moves."""
from __future__ import annotations

import json
import threading
from collections import Counter
from pathlib import Path

_LOCK = threading.RLock()
_PREFIX = "[protected]"


def identity(name: str) -> str:
    if not isinstance(name, str):
        raise ValueError("A wallpaper identity must be a folder name.")
    key = name.lower()
    if key.startswith(_PREFIX):
        key = key[len(_PREFIX):].lstrip()
    if not key.strip(" .") or "/" in key or "\\" in key or ":" in key:
        raise ValueError(f"Not a wallpaper folder name: {name!r}")
    return key


class QuotaUsage:
    """An immutable view: a running selection keeps the settings it started with."""

    def __init__(self, multipliers: dict[str, int], used: Counter, cycle: str,
                 counted_runs: set[str]):
        self._multipliers = dict(multipliers)
        self._used = Counter(used)
        self.cycle = cycle
        self.counted_runs = frozenset(counted_runs)

    def multiplier(self, name: str) -> int:
        return self._multipliers.get(identity(name), 1)

    def used(self, name: str) -> int:
        return self._used[identity(name)]

    def remaining(self, name: str) -> int:
        return max(self.multiplier(name) - self.used(name), 0)

    @property
    def used_count(self) -> int:
        return len(self._used)


class MultiplierStore:
    def __init__(self, path: Path | None = None):
        from .rotator import config as rc
        self.path = Path(path) if path is not None else rc.HISTORY_PATH.parent / "multipliers.json"
        self._data = self._empty()
        self.problem = ""
        try:
            self.reload()
        except (OSError, ValueError):
            pass                         # the window opens; usage and writes still refuse

    @staticmethod
    def _empty() -> dict:
        return {"version": 1, "multipliers": {}, "baseline": None, "retries": []}

    def reload(self) -> None:
        """Read strictly: an unreadable quota file must never become default quotas."""
        try:
            self._load()
        except (OSError, ValueError) as err:
            self.problem = str(err)
            raise
        self.problem = ""

    def _load(self) -> None:
        with _LOCK:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                data = self._empty()
            try:
                if not isinstance(data, dict) or type(data.get("version")) is not int \
                        or data["version"] != 1:
                    raise ValueError("unsupported multiplier data")
                values = data["multipliers"]
                if not isinstance(values, dict):
                    raise ValueError("multipliers must be an object")
                for name, value in values.items():
                    if identity(name) != name or type(value) is not int or value < 1:
                        raise ValueError(f"invalid multiplier for {name!r}")
                baseline = data["baseline"]
                if baseline is not None:
                    if not isinstance(baseline, dict) or not isinstance(baseline["run_id"], str):
                        raise ValueError("invalid cycle baseline")
                    if type(baseline["moved_count"]) is not int or baseline["moved_count"] < 0:
                        raise ValueError("invalid baseline move count")
                    if not isinstance(baseline["active"], list):
                        raise ValueError("invalid initial active wallpapers")
                    for name in baseline["active"]:
                        if identity(name) != name:
                            raise ValueError("invalid initial wallpaper identity")
                    if len(set(baseline["active"])) != len(baseline["active"]):
                        raise ValueError("duplicate initial wallpaper identity")
                if not isinstance(data["retries"], list):
                    raise ValueError("invalid retry quota ledger")
                for entry in data["retries"]:
                    if not isinstance(entry, dict) or not isinstance(entry["cycle"], str) \
                            or not isinstance(entry["run_id"], str) \
                            or type(entry["index"]) is not int or entry["index"] < 0:
                        raise ValueError("invalid retry quota entry")
                    if identity(entry["name"]) != entry["name"]:
                        raise ValueError("invalid retry wallpaper identity")
            except (KeyError, TypeError, ValueError) as err:
                raise ValueError(f"{self.path.name} could not be read: {err}") from err
            self._data = data

    def _save(self) -> None:
        from .rotator.config import _write_atomically
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            _write_atomically(self.path, json.dumps(self._data, indent=2, ensure_ascii=False))
        except OSError:
            try:
                self.reload()
            except (OSError, ValueError):
                pass
            raise

    def multiplier(self, name: str) -> int:
        if self.problem:
            raise ValueError(self.problem)
        return self._data["multipliers"].get(identity(name), 1)

    def set_multiplier(self, name: str, value: int) -> None:
        key = identity(name)
        if type(value) is not int or value < 1:
            raise ValueError("Multiplier must be a positive whole number.")
        with _LOCK:
            self.reload()
            self._data["multipliers"][key] = value
            self._save()

    def usage(self, runs) -> QuotaUsage:
        if self.problem:
            raise ValueError(self.problem)
        used, counted = Counter(), set()
        baseline = self._data["baseline"]
        cycle = f"baseline:{baseline['run_id']}" if baseline is not None else "initial"
        reached_baseline = baseline is None
        for record in runs:                 # newest first, including the reset's own draw
            if baseline is not None and record.id == baseline["run_id"]:
                reached_baseline = True
                used.update(identity(name) for name in record.moved[baseline["moved_count"]:])
                break
            counted.add(record.id)
            used.update(identity(name) for name in record.moved)
            if record.history_reset:
                cycle = f"reset:{record.id}"
                reached_baseline = False
                break
        if baseline is not None and cycle.startswith("baseline:"):
            if baseline["run_id"] and not reached_baseline:
                raise ValueError("The multiplier cycle baseline is missing from rotation history.")
            used.update(baseline["active"])
        records = {record.id: record for record in runs}
        for entry in self._data["retries"]:
            record = records.get(entry["run_id"])
            if entry["cycle"] == cycle and record is not None \
                    and entry["index"] < len(record.moved) \
                    and identity(record.moved[entry["index"]]) == entry["name"]:
                used[entry["name"]] += 1
        return QuotaUsage(self._data["multipliers"], used, cycle, counted)

    def record_retry(self, runs, record, name: str) -> None:
        """Write an older retry's intent before moving; only history confirms its debit."""
        with _LOCK:
            self.reload()
            usage = self.usage(runs)
            baseline = self._data["baseline"]
            if record.id in usage.counted_runs or (baseline is not None
                    and usage.cycle.startswith("baseline:") and record.id == baseline["run_id"]):
                return                    # the normal history-derived count includes this move
            entry = {"cycle": usage.cycle, "run_id": record.id,
                     "index": len(record.moved), "name": identity(name)}
            if entry not in self._data["retries"]:
                self._data["retries"] = [e for e in self._data["retries"]
                                         if e["cycle"] == usage.cycle] + [entry]
                self._save()
