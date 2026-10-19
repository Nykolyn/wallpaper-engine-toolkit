"""Copy active, downloaded Workshop subscriptions missing from the reserve."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .. import secrets
from ..settings import app_data_dir
from . import file_copy
from .library import find_workshop_content
from .library_index import LibraryIndex
from .steam_api import DETAILS_BATCH, SUMMARY_BATCH, SteamClient, SteamError
from .steam_ugc import SteamUgc
from .unavailable import Wallpaper
from .wallpaper_delete import workshop_id
from .wallpaper_meta import author_names, read_meta


@dataclass
class Scan:
    rows: list[Wallpaper]
    checked: int
    skipped: int = 0
    warning: str = ""


def reserve_ids(reserve, *, index_path=None, on_progress=None, cancelled=lambda: False):
    if not reserve or not str(reserve).strip():
        raise ValueError("Set the reserve folder in Settings first.")
    with os.scandir(reserve) as entries:
        names = {e.name for e in entries if e.is_dir()}
    # A separate cache avoids concurrent saves over the Rotator's metadata index.
    index = LibraryIndex(index_path or app_data_dir() / "reserve_index.json")
    index.load()
    report = index.refresh([reserve], progress=on_progress, cancelled=cancelled)
    if cancelled():
        raise InterruptedError("Scan stopped.")
    if report.missing or not report.complete:
        raise OSError("The reserve could not be read completely. Try scanning again.")
    return index.workshop_ids(reserve) | {n for n in names if n.isdigit()}


def scan(reserve, *, workshop=None, cache_path=None, index_path=None, ugc=None, client=None,
         on_progress=None, cancelled=lambda: False, api_key=None):
    def progress(phase, done, total):
        if cancelled():
            raise InterruptedError("Scan stopped.")
        if on_progress:
            on_progress(phase, done, total)

    kept = reserve_ids(reserve, index_path=index_path, cancelled=cancelled,
                       on_progress=lambda _root, d, t: progress("Reading reserve", d, t))
    root = find_workshop_content(workshop)
    if root is None:
        raise OSError("The Wallpaper Engine Workshop folder was not found.")
    with os.scandir(root) as entries:
        ids = sorted(e.name for e in entries if e.name.isdigit() and e.is_dir())
    rows, ready, skipped = [], [], 0
    with ugc if ugc is not None else SteamUgc(cancelled=cancelled) as steam:
        for done, item in enumerate(ids, 1):
            progress("Checking subscriptions", done, len(ids))
            if item not in kept and steam.state(item).ready:
                ready.append(item)
    for done, item in enumerate(ready, 1):
        progress("Reading wallpapers and sizes", done, len(ready))
        folder = str(root / item)
        try:
            files, _ = file_copy.manifest(folder)
            file_copy.wallpaper_project(folder, files)
            meta = read_meta(str(root / item / "project.json"))
            rows.append(Wallpaper(folder, meta.title, kind=meta.kind, size=sum(files.values())))
        except (OSError, ValueError):
            skipped += 1
    authors = author_names([Path(r.folder).name for r in rows], cache_path)
    missing = [Path(r.folder).name for r in rows if Path(r.folder).name not in authors]
    warning = ""
    own_client = client is None
    try:
        if missing:
            client = client or SteamClient(api_key=api_key or secrets.get(secrets.STEAM_API_KEY) or None,
                                            timeout=10, retries=1,
                                            **({"cache_path": cache_path} if cache_path else {}))
            details, profiles = {}, {}
            for start in range(0, len(missing), DETAILS_BATCH):
                progress("Reading wallpaper details", start, len(missing))
                details.update(client.details(missing[start:start + DETAILS_BATCH]))
            creators = sorted({d.creator for d in details.values() if d.creator})
            batch_size = SUMMARY_BATCH if getattr(client, "has_key", False) else getattr(client, "workers", 4)
            for start in range(0, len(creators), batch_size):
                progress("Reading authors", start, len(creators))
                profiles.update(client.profiles(creators[start:start + batch_size]))
            for row in rows:
                detail = details.get(Path(row.folder).name)
                profile = profiles.get(detail.creator) if detail else None
                row.author = profile.name if profile else ""
    except SteamError:
        warning = "Some author names could not be loaded. You can still copy the wallpapers."
    finally:
        if own_client and client is not None:
            client.close()
    for row in rows:
        known = authors.get(Path(row.folder).name)
        if known:
            row.author = known.name
    if cancelled():
        raise InterruptedError("Scan stopped.")
    return Scan(rows, len(ids), skipped, warning)


def copy_to_reserve(folder, reserve, *, ugc=None, on_progress=None, cancelled=lambda: False,
                    kept=None):
    item = workshop_id(folder)
    if not item:
        raise ValueError("This is not a Workshop wallpaper folder.")
    with ugc if ugc is not None else SteamUgc(cancelled=cancelled) as steam:
        if not steam.state(item).ready:
            raise ValueError("This wallpaper is no longer subscribed and fully downloaded. Scan again.")
    known = reserve_ids(reserve, cancelled=cancelled) if kept is None else kept
    if item in known:
        raise FileExistsError("This wallpaper is already in the reserve. Scan again.")
    target = file_copy.copy_to_reserve(folder, reserve, on_progress=on_progress, cancelled=cancelled)
    known.add(item)
    return target
