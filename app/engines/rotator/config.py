"""Configuration and persistent state management."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path

from .. import steam_paths


def app_data_dir() -> Path:
    """Directory where config/history live. Next to the exe, or project root in dev."""
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).resolve().parent.parent
    d = base / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


CONFIG_PATH = app_data_dir() / "config.json"
HISTORY_PATH = app_data_dir() / "history.json"


@dataclass
class Config:
    """The three folders a rotation moves between, and how many to keep.

    Only ``destination`` can be worked out in advance — it is Wallpaper
    Engine's own myprojects folder, which Steam put somewhere specific. The
    reserve and the duplicates bin are folders you choose, so they start
    empty and the Rotator tab asks for them rather than inventing them.
    """

    source: str = ""
    destination: str = field(
        default_factory=lambda: steam_paths.as_text(steam_paths.myprojects_dir()))
    duplicates: str = ""
    count: int = 1000

    @classmethod
    def load(cls) -> "Config":
        if CONFIG_PATH.exists():
            try:
                data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                return cls(**{k: data.get(k, getattr(cls(), k)) for k in cls().__dict__})
            except Exception:
                pass
        c = cls()
        c.save()
        return c

    def save(self) -> None:
        CONFIG_PATH.write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8"
        )


@dataclass
class RunRecord:
    """A single rotation run, stored in history."""
    id: str
    timestamp: str
    moved: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    returned: int = 0
    failed: list[str] = field(default_factory=list)
    history_reset: bool = False

    @property
    def moved_count(self) -> int:
        return len(self.moved)

    @property
    def duplicate_count(self) -> int:
        return len(self.duplicates)


class History:
    """Persistent history of runs + derived 'used' set for uniqueness."""

    def __init__(self, runs: list[RunRecord]):
        self.runs = runs

    @classmethod
    def load(cls) -> "History":
        if HISTORY_PATH.exists():
            try:
                data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
                runs = [RunRecord(**r) for r in data.get("runs", [])]
                return cls(runs)
            except Exception:
                pass
        return cls([])

    def save(self) -> None:
        data = {"runs": [asdict(r) for r in self.runs]}
        HISTORY_PATH.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def add(self, record: RunRecord) -> None:
        self.runs.insert(0, record)  # newest first
        self.save()

    def used_set(self) -> set[str]:
        """All folder names ever moved, lower-cased, since the last history reset."""
        used: set[str] = set()
        for r in self.runs:  # newest first
            if r.history_reset:
                # everything from this run onward (older) was before a reset boundary
                for name in r.moved:
                    used.add(name.lower())
                break
            for name in r.moved:
                used.add(name.lower())
        return used
