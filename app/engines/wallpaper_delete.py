"""wallpaper_delete.py — the Tracker's and the Rotator's Delete.

A wallpaper folder goes to the Recycle Bin, never straight off the disk: a
click in a long table is easy to land on the wrong row, and the Recycle Bin is
where Windows' own Delete would have put it.

A **Workshop item** (a folder under `steamapps/workshop/content/431960/<id>`)
is unsubscribed first. Deleting its folder alone does nothing that lasts:
Steam sees a subscribed item missing and downloads it again. So the
subscription goes first, through the running Steam client
(`steam_ugc.SteamUgc`, opened for this one call), and if Steam will not drop
it the folder is left alone and the reason said. A folder in myprojects or the
reserve is the user's own copy, whatever its project.json says: deleting it
unsubscribes nothing.

Nothing here touches Qt; the pages run `delete_wallpaper` on a thread of
their own (the library is on a hard disk, and Steam can take seconds).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

WORKSHOP_APP = "431960"
# How long Steam is given to agree the item is no longer subscribed.
UNSUBSCRIBE_WAIT = 5.0


class DeleteError(Exception):
    """Why a folder was not deleted, in words for a toast. Nothing was changed."""


@dataclass(frozen=True)
class Deleted:
    folder: str
    workshop_id: str = ""           # set for a Workshop item, which was unsubscribed


def workshop_id(folder: str | os.PathLike) -> str:
    """The Workshop item a folder is — `…/431960/<id>`, Steam's own place
    for Wallpaper Engine's subscriptions — or "". The path decides, nothing is
    read: a copy in myprojects is not a subscription."""
    text = os.fspath(folder).replace("\\", "/").rstrip("/")
    parts = text.split("/")
    if len(parts) >= 2 and parts[-1].isdigit() and parts[-2] == WORKSHOP_APP:
        return parts[-1]
    return ""


def _unsubscribe(item_id: str) -> bool:
    """Ask the running Steam to drop the subscription; True once it agrees."""
    from .steam_ugc import SteamUgc

    with SteamUgc() as ugc:
        return not ugc.unsubscribe(item_id, wait=UNSUBSCRIBE_WAIT).subscribed


def _recycle(path: Path) -> bool:
    from ..data_location import to_recycle_bin

    return to_recycle_bin(path)


def delete_wallpaper(folder: str | os.PathLike, *,
                     unsubscribe: Callable[[str], bool] = _unsubscribe,
                     recycle: Callable[[Path], bool] = _recycle) -> Deleted:
    """Send a wallpaper folder to the Recycle Bin, unsubscribing it first when
    it is a Workshop item. Raises DeleteError, with nothing changed, when it
    cannot. Touches the disk and Steam: a worker's job."""
    path = Path(folder)
    if not path.is_dir():
        raise DeleteError("it is not there any more.")
    item = workshop_id(path)
    if item:
        try:
            dropped = unsubscribe(item)
        except Exception as err:  # noqa: BLE001 — UgcError, or the DLL itself failing
            raise DeleteError(f"Steam would not unsubscribe from it ({err}). The folder "
                              f"was left alone, or Steam would download it again.") from None
        if not dropped:
            raise DeleteError("Steam still lists it as subscribed. The folder was left "
                              "alone, or Steam would download it again.")
    if not recycle(path) or path.exists():
        what = "It is unsubscribed, but its" if item else "Its"
        raise DeleteError(f"Windows would not move it to the Recycle Bin. {what} folder may "
                          f"be in use: Wallpaper Engine may be showing it.")
    return Deleted(str(path), item)
