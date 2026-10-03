"""Persistent settings: ``data/suite.json``.

The Rotator keeps its own Config/History (engines/rotator/config.py); this is
everything else — the Copier's and the Creator's folders, the tracker's, the
Review's. The Settings page writes both stores, each through its own API.

Two processes use this file: the window and the tray. Each holds it in memory
and writes it whole, so each reads it again when the other has written it
(`reload_if_changed`) before it relies on or changes what the other may have
set.
"""
from __future__ import annotations

import json
from pathlib import Path

from .engines import steam_paths


# ---- Defaults --------------------------------------------------------------
#
# Only one folder in this app can be known in advance: the one Steam created.
# It is looked up rather than assumed, so the first launch on any machine
# arrives with the right target already filled in.
#
# Looked up in the background, though (steam_paths.find_in_background): Steam
# may be on the wallpaper disk, and this module is imported by the window and
# the tray before either has drawn anything. Until the answer is in, the default
# is "", as it is with no Steam at all; a page shows it when it arrives
# (steam_paths.when_found), and nothing writes to an empty folder.
#
# The others — where you keep video clips, where you keep previews — are
# nobody else's business to guess, and an empty field that asks is better than
# a filled one that is wrong. They default to "" and the tab shows a picker.

def default_myprojects() -> str:
    """Wallpaper Engine's myprojects as Steam has it, once known; "" before."""
    return steam_paths.as_text(steam_paths.known(steam_paths.myprojects_dir))


# Copier (was DEFAULT_DEST / DEFAULT_COUNT in wallpaper_copier/ui.py)
default_copier_dest = default_myprojects
DEFAULT_COPIER_COUNT = 3

# Creator (builds projects from videos alone, generating the preview)
DEFAULT_CREATOR_SOURCE = ""
default_creator_target = default_myprojects
DEFAULT_CREATOR_MODE = "Move"

# The Creator tab was once two tabs, and the surviving one stored its settings
# under "autocreator". Nothing else remembers that, so the rename is done here,
# once, on the way in.
_RENAMED_SECTIONS = {"autocreator": "creator"}


def app_data_dir() -> Path:
    """The one folder every data file lives in — see app/data_location.py."""
    from .data_location import data_dir
    return data_dir()


SETTINGS_PATH = app_data_dir() / "suite.json"


def _stamp() -> tuple[int, int] | None:
    """What the file looks like on disk: enough to see that it was written."""
    try:
        st = SETTINGS_PATH.stat()
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


class Settings:
    """Tiny JSON-backed settings store with section helpers."""

    def __init__(self, data: dict | None = None):
        self._data = data or {}
        self._stamp: tuple[int, int] | None = None

    @classmethod
    def load(cls) -> "Settings":
        stamp = _stamp()
        try:
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls({})
        settings = cls(cls._rename_sections(data))
        settings._stamp = stamp
        return settings

    def reload_if_changed(self) -> bool:
        """Read the file again if it was written since this was loaded or saved
        — by the other process. In place, so everyone holding this object sees
        it. True if it was read again."""
        stamp = _stamp()
        if stamp is None or stamp == self._stamp:
            return False
        fresh = Settings.load()
        self._data = fresh._data
        self._stamp = fresh._stamp
        return True

    @staticmethod
    def _rename_sections(data: dict) -> dict:
        """Carry a renamed tab's settings over to its new section name.

        The old name is dropped only once its values are somewhere else, and a
        section that already exists under the new name wins — it is the one the
        app has been writing to.
        """
        for old_name, new_name in _RENAMED_SECTIONS.items():
            stale = data.pop(old_name, None)
            if isinstance(stale, dict) and stale and not data.get(new_name):
                data[new_name] = stale
        return data

    def save(self) -> None:
        try:
            SETTINGS_PATH.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            return
        self._stamp = _stamp()

    # ----- section access ----------------------------------------------------

    def section(self, name: str) -> dict:
        return self._data.setdefault(name, {})

    def get(self, section: str, key: str, default):
        return self.section(section).get(key, default)

    def set(self, section: str, key: str, value) -> None:
        self.section(section)[key] = value
