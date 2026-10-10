"""Find local Workshop wallpapers Steam cannot describe; preserve one in the reserve."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .copier import CopyEngine, destination_info, manifest
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
                        meta.kind)
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
    if not reserve or not reserve.strip():
        raise ValueError("Set the reserve folder in Settings first.")
    source, root = Path(folder).resolve(), Path(reserve).resolve()
    if not workshop_id(source):
        raise ValueError("This is not a Workshop wallpaper folder.")
    if source == root or source in root.parents or root in source.parents:
        raise ValueError("The reserve must be separate from the Workshop folder.")
    files, _ = manifest(folder)
    project = json.loads((source / "project.json").read_text(encoding="utf-8-sig"))
    if not isinstance(project, dict):
        raise ValueError("project.json is not an object.")
    payload = project.get("file")
    sizes = {os.path.normcase(name): size for name, size in files.items()}
    if (not isinstance(payload, str) or not payload
            or sizes.get(os.path.normcase(os.path.normpath(payload)), 0) <= 0):
        raise ValueError("The wallpaper's content file is missing or empty.")
    if destination_info(str(root))["free"] < sum(files.values()):
        raise OSError("There is not enough free space in the reserve.")
    root.mkdir(parents=True, exist_ok=True)
    target = root / source.name
    if target.exists():
        raise FileExistsError(f"The reserve already contains {target.name}; nothing was replaced.")
    # Outside the reserve, so a concurrent rotation cannot take a half-written wallpaper.
    staging = Path(tempfile.mkdtemp(prefix=".toolkit-reserve-", dir=root.parent))
    try:
        shutil.copytree(source, staging, dirs_exist_ok=True)
        CopyEngine._verify(str(source), str(staging), files)
        json.loads((staging / "project.json").read_text(encoding="utf-8-sig"))
        os.rename(staging, target)       # Windows refuses an existing destination
    except Exception as err:
        try:
            CopyEngine._discard(str(staging), str(root.parent))
        except OSError as cleanup:
            raise OSError(f"{err}; partial copy remains at {staging}: {cleanup}") from err
        raise
    try:
        remove(str(source))
    except Exception as err:
        raise OSError(f"The verified copy is kept at {target}, but removing the Workshop "
                      f"source failed: {err}") from err
    return str(target)
