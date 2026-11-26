"""Find local Workshop wallpapers Steam cannot describe; preserve one in the reserve."""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .file_copy import copy_to_reserve, destination_info, manifest, reserve_check, reserve_target
from .library import find_workshop_content
from .review import we_folders
from .steam_api import SteamClient, SteamError
from .wallpaper_delete import (_recycle, _unsubscribe_many, agreed, delete_wallpaper,
                               drop_subscriptions, workshop_id)
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


def wallpaper_row(folder: Path, author: str) -> Wallpaper:
    """A table row from the folder's project.json, size and creation time. Reads the disk."""
    meta = read_meta(str(folder / "project.json"))
    row = Wallpaper(str(folder), meta.title, author, meta.kind, tags=meta.tags, age=meta.age)
    try:
        row.created = datetime.fromtimestamp(folder.stat().st_ctime)
        files, _ = manifest(str(folder))
        row.size = sum(files.values())
    except (OSError, ValueError) as err:
        row.note = f"Could not read all folder metadata: {err}"
    return row


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
        rows.append(wallpaper_row(root / item_id,
                                  authors[item_id].name if item_id in authors else ""))
    warning = (f"Steam could not check {len(uncertain)} wallpapers; they are not listed here."
               if uncertain else "")
    return Scan(rows, folders, len(ids) - len(uncertain), warning)


@dataclass(frozen=True)
class Staged:
    """A wallpaper ready for the reserve, waiting for Steam to drop it."""
    source: Path
    target: Path
    held: Path | None = None    # renamed out of the Workshop folder; None: copied


def _volume(path) -> int | None:
    try:
        return destination_info(str(path))["device"]
    except (OSError, ValueError):
        return None


def _stage(folder, reserve) -> Staged:
    """Take one wallpaper out of the Workshop folder, ready for the reserve.

    On the reserve's own volume the folder is renamed beside the reserve: no
    bytes are copied, and a folder Wallpaper Engine is showing refuses at once,
    before Steam is asked anything. On another volume it is copied and verified.
    """
    source = Path(folder).resolve()
    if not workshop_id(source):
        raise ValueError("This is not a Workshop wallpaper folder.")
    here = _volume(source)
    if here is None or not reserve or not reserve.strip() or _volume(Path(reserve).resolve()) != here:
        return Staged(source, Path(copy_to_reserve(folder, reserve)))
    source, root, _ = reserve_check(folder, reserve)
    target = reserve_target(source, root)
    # Outside the reserve, so a library scan never sees it before Steam lets go.
    holder = Path(tempfile.mkdtemp(prefix=".toolkit-reserve-", dir=root.parent))
    try:
        os.rename(source, holder / source.name)
    except OSError as err:
        holder.rmdir()
        raise OSError("Windows would not move the folder; Wallpaper Engine may be showing it. "
                      f"Nothing was changed. ({err})") from None
    return Staged(source, target, holder / source.name)


def _finish(staged: Staged, dropped, recycle) -> str | Exception:
    """Publish what Steam let go of; put back, or keep, what it did not."""
    if staged.held is None:
        try:
            delete_wallpaper(staged.source, unsubscribe=agreed(dropped), recycle=recycle)
        except Exception as err:  # noqa: BLE001 — reported on the row
            return OSError(f"The verified copy is kept at {staged.target}, but removing the "
                           f"Workshop source failed: {err}")
        return str(staged.target)
    try:
        let_go = agreed(dropped)(workshop_id(staged.source))
        why = "Steam still lists it as subscribed"
    except Exception as err:  # noqa: BLE001 — UgcError, or the DLL itself failing
        let_go, why = False, f"Steam would not unsubscribe from it ({err})"
    destination = staged.target if let_go else staged.source
    try:
        os.rename(staged.held, destination)
    except OSError as err:
        what = "It is unsubscribed, but moving it into the reserve" if let_go else f"{why}, and putting it back"
        return OSError(f"{what} failed ({err}). The wallpaper is kept at {staged.held}.")
    try:
        staged.held.parent.rmdir()
    except OSError:
        pass
    return str(staged.target) if let_go else OSError(f"{why}; the folder was put back.")


def move_many(folders, reserve, *, unsubscribe_many=_unsubscribe_many, recycle=_recycle,
              on_progress=None) -> list[str | Exception]:
    """Move a selection to the reserve: stage every folder, ask Steam to drop
    them all in one session, then publish. One reserve path or exception per
    folder, in order."""
    staged: list[Staged | Exception] = []
    for folder in folders:
        try:
            staged.append(_stage(folder, reserve))
        except Exception as err:  # noqa: BLE001 — reported on the row
            staged.append(err)
        if on_progress:
            on_progress(len(staged), len(folders))
    dropped = drop_subscriptions((workshop_id(s.source) for s in staged if isinstance(s, Staged)),
                                 unsubscribe_many)
    return [s if isinstance(s, Exception) else _finish(s, dropped, recycle) for s in staged]


def move_to_reserve(folder: str, reserve: str, **options) -> str:
    """`move_many` for one wallpaper; raises what it would report."""
    result = move_many([folder], reserve, **options)[0]
    if isinstance(result, Exception):
        raise result
    return result
