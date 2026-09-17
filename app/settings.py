"""Persistent settings for the Copier and Creator tabs.

The Rotator tab keeps its own verbatim Config/History (engines/rotator/config.py),
so this module only covers the two tabs whose original settings lived in their
UI layer. Everything is stored in a single ``data/suite.json`` file.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .engines import steam_paths


# ---- Defaults --------------------------------------------------------------
#
# Only one folder in this app can be known in advance: the one Steam created.
# It is looked up rather than assumed, so the first launch on any machine
# arrives with the right target already filled in.
#
# The others — where you keep video clips, where you keep previews — are
# nobody else's business to guess, and an empty field that asks is better than
# a filled one that is wrong. They default to "" and the tab shows a picker.

_MYPROJECTS = steam_paths.as_text(steam_paths.myprojects_dir())

# Copier (was DEFAULT_DEST / DEFAULT_COUNT in wallpaper_copier/ui.py)
DEFAULT_COPIER_DEST = _MYPROJECTS
DEFAULT_COPIER_COUNT = 3

# Creator (was DEFAULT_SOURCE / DEFAULT_TARGET / DEFAULT_PREVIEWS in
# wallpapers_creator/ui.py)
DEFAULT_CREATOR_SOURCE = ""
DEFAULT_CREATOR_TARGET = _MYPROJECTS
DEFAULT_CREATOR_PREVIEWS = ""
DEFAULT_CREATOR_MODE = "Move"

# Auto Creator (builds projects from videos alone, generating the preview)
DEFAULT_AUTO_SOURCE = ""
DEFAULT_AUTO_TARGET = _MYPROJECTS
DEFAULT_AUTO_MODE = "Move"


def app_data_dir() -> Path:
    """Directory where suite settings live. Next to the exe, or project root in dev."""
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).resolve().parent.parent
    d = base / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


SETTINGS_PATH = app_data_dir() / "suite.json"


class Settings:
    """Tiny JSON-backed settings store with section helpers."""

    def __init__(self, data: dict | None = None):
        self._data = data or {}

    @classmethod
    def load(cls) -> "Settings":
        try:
            return cls(json.loads(SETTINGS_PATH.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            return cls({})

    def save(self) -> None:
        try:
            SETTINGS_PATH.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

    # ----- section access ----------------------------------------------------

    def section(self, name: str) -> dict:
        return self._data.setdefault(name, {})

    def get(self, section: str, key: str, default):
        return self.section(section).get(key, default)

    def set(self, section: str, key: str, value) -> None:
        self.section(section)[key] = value
