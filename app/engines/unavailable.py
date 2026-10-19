"""Find local Workshop wallpapers Steam cannot describe; preserve one in the reserve."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .file_copy import copy_to_reserve, manifest
from .library import find_workshop_content
from .review import we_folders
from .steam_api import SteamClient, SteamError
from .wallpaper_delete import delete_wallpaper, workshop_id
from .wallpaper_meta import author_names, read_meta

CHECK_AGE = 24 * 3600
# Steam EResult: FileNotFound, AccessDenied, ItemDeleted. Other failures are inconclusive.
UNAVAILABLE = (9, 15, 86)


@dataclass
class Wallpaper:
    folder: str
    title: str
    author: str = ""
    kind: str = ""
    size: int | None = None
    created: datetime | None = None
    note: str = ""
    tags: tuple[str, ...] = ()
    age: str = ""


@dataclass
class Scan:
    rows: list[Wallpaper]
    folders: dict[str, list[str]]
    checked: int
    warning: str = ""


def scan(*, config_path=None, workshop=None, cache_path=None, refresh=False,
         on_progress=None, client=None) -> Scan:
    """One shallow listing, batched cached API calls, metadata only for matches. Off-thread."""
    root = find_workshop_content(workshop)
    if root is None:
        raise OSError("The Wallpaper Engine Workshop folder was not found.")
    with os.scandir(root) as entries:
        ids = sorted(e.name for e in entries if e.name.isdigit() and e.is_dir()
                     and os.path.isfile(os.path.join(e.path, "project.json")))
    folders = we_folders(config_path)
    # Capture previously known names before an unavailable answer replaces the cached item.
    authors = author_names(ids, cache_path)
    own_client = client is None
    if own_client:
        options = {"cache_path": cache_path} if cache_path is not None else {}
        client = SteamClient(**options)
    try:
        details = client.details(ids, refresh=refresh, max_age=CHECK_AGE,
                                 on_progress=on_progress)
        legacy = [i for i, item in details.items() if not item.ok and not item.result]
        if legacy:
            details.update(client.details(legacy, refresh=True))
    finally:
        if own_client:
            client.close()
    uncertain = [i for i in ids if i not in details
                 or (not details[i].ok and details[i].result not in UNAVAILABLE)]
    if ids and len(uncertain) == len(ids):
        raise SteamError("Steam returned no conclusive item details. Try checking again later.")
    rows = []
    for item_id in ids:
        item = details.get(item_id)
        if item is None or item.ok or item.result not in UNAVAILABLE:
            continue
        folder = root / item_id
        meta = read_meta(str(folder / "project.json"))
        row = Wallpaper(str(folder), meta.title, authors[item_id].name if item_id in authors else "",
                        meta.kind, tags=meta.tags, age=meta.age)
        try:
            row.created = datetime.fromtimestamp(folder.stat().st_ctime)
            files, _ = manifest(str(folder))
            row.size = sum(files.values())
        except (OSError, ValueError) as err:
            row.note = f"Could not read all folder metadata: {err}"
        rows.append(row)
    warning = (f"Steam could not check {len(uncertain)} wallpapers; they are not listed here."
               if uncertain else "")
    return Scan(rows, folders, len(ids) - len(uncertain), warning)


def move_to_reserve(folder: str, reserve: str, *, remove=delete_wallpaper) -> str:
    """Verified copy, publish without overwrite, then unsubscribe/recycle the source."""
    source = Path(folder).resolve()
    if not workshop_id(source):
        raise ValueError("This is not a Workshop wallpaper folder.")
    target = copy_to_reserve(folder, reserve)
    try:
        remove(str(source))
    except Exception as err:
        raise OSError(f"The verified copy is kept at {target}, but removing the Workshop "
                      f"source failed: {err}") from err
    return str(target)
