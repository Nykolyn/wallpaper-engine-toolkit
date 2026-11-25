"""Subscribed Workshop wallpapers that are in no Wallpaper Engine playlist.

The Cleaner's second tab. Local only: the Workshop folder and `config.json`,
no Steam call. A playlist is any saved one (`general.playlists`) and any
monitor's running copy (`wallpaperconfig.selectedwallpapers.*.playlist`), for
every user section; a monitor's single wallpaper does not count.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .library import find_workshop_content
from .review import FOLDER, find_we_config, we_folders
from .unavailable import Wallpaper, wallpaper_row
from .wallpaper_delete import WORKSHOP_APP
from .wallpaper_meta import author_names


@dataclass
class OutsideScan:
    rows: list[Wallpaper]
    folders: dict[str, list[str]]
    saved: datetime | None


def _config(config_path) -> Path:
    found = config_path or find_we_config()
    if not found:
        raise OSError("Wallpaper Engine's config.json was not found.")
    return Path(found)


def _item_id(item: str) -> str:
    parts = item.replace("\\", "/").split("/")
    if WORKSHOP_APP in parts:
        index = parts.index(WORKSHOP_APP) + 1
        if index < len(parts) and parts[index].isdigit():
            return parts[index]
    return ""


def playlisted_ids(config_path=None) -> set[str]:
    """Every Workshop id in any playlist. Raises OSError rather than guess:
    an unreadable file must stop an unsubscribe, not empty the playlists."""
    path = _config(config_path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        raise OSError(f"Could not read {path.name}: {err}") from err
    playlists = []
    for section in data.values() if isinstance(data, dict) else ():
        general = section.get("general") if isinstance(section, dict) else None
        if not isinstance(general, dict):
            continue
        playlists += general.get("playlists") or []
        selected = (general.get("wallpaperconfig") or {}).get("selectedwallpapers") or {}
        playlists += [cfg.get("playlist") for cfg in selected.values() if isinstance(cfg, dict)]
    ids = set()
    for playlist in playlists:
        if isinstance(playlist, dict):
            ids.update(_item_id(i) for i in playlist.get("items") or [] if isinstance(i, str))
    ids.discard("")
    return ids


def saved_at(config_path=None) -> datetime | None:
    """When Wallpaper Engine last wrote config.json, or None."""
    try:
        return datetime.fromtimestamp(_config(config_path).stat().st_mtime)
    except OSError:
        return None


def scan(*, config_path=None, workshop=None, cache_path=None) -> OutsideScan:
    """Subscribed folders not in any playlist, sorted by id. Off-thread."""
    root = find_workshop_content(workshop)
    if root is None:
        raise OSError("The Wallpaper Engine Workshop folder was not found.")
    kept = playlisted_ids(config_path)
    with os.scandir(root) as entries:
        ids = sorted(e.name for e in entries if e.name.isdigit() and e.name not in kept
                     and e.is_dir() and os.path.isfile(os.path.join(e.path, "project.json")))
    authors = author_names(ids, cache_path)
    rows = [wallpaper_row(root / i, authors[i].name if i in authors else "") for i in ids]
    return OutsideScan(rows, we_folders(config_path), saved_at(config_path))


def default_folder(remembered: str | None, review_scope: str, folders: Iterable[str]) -> str:
    """The remembered folder ("" is All wallpapers) while it exists, else
    Review's folder, else All wallpapers."""
    folders = set(folders)
    if remembered is not None and (remembered == "" or remembered in folders):
        return remembered
    if review_scope.startswith(FOLDER) and review_scope[len(FOLDER):] in folders:
        return review_scope[len(FOLDER):]
    return ""
