"""What a wallpaper folder's row can do, on the Tracker's and the Rotator's
tables alike: open it in Explorer, and Delete it after asking.

Delete itself is `engines/wallpaper_delete` (the Recycle Bin, a Workshop item
unsubscribed first); this is the question asked before it and the words said
after, so the two pages ask and answer the same way.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Callable

from PySide6.QtWidgets import QWidget

from .. import external
from ..engines.wallpaper_delete import DeleteError, Deleted, workshop_id
from ..ui.kit import ConfirmDialog

SEND, DELETE = "copier", "delete"           # the buttons' keys
SEND_TIP = "Send to Copier"
DELETE_TIP = "Delete…"
WORKSHOP_DELETE_TIP = "Unsubscribe and delete…"


def delete_tip(folder: str) -> str:
    return WORKSHOP_DELETE_TIP if workshop_id(folder) else DELETE_TIP


def delete_dialog(name: str, folder: str, parent: QWidget | None, *,
                  on_screen: bool = False) -> ConfirmDialog:
    """Asking before Delete: which folder, where it goes, and for a Workshop
    item that its subscription goes first."""
    item = workshop_id(folder)
    if item:
        title = f"Unsubscribe from “{name}” and delete it?"
        body = (f"Steam is asked to unsubscribe from Workshop item {item}, so it is not "
                f"downloaded again, and then its folder goes to the Recycle Bin.")
        confirm = "Unsubscribe and delete"
    else:
        title = f"Delete “{name}”?"
        body = "The folder goes to the Recycle Bin, where it can be restored from."
        confirm = "Delete"
    note = None
    if on_screen:
        note = ("warn", "Wallpaper Engine is showing it now. Windows may refuse to move a "
                        "folder in use; if it does, nothing is deleted.")
    else:
        note = ("neutral", "Wallpaper Engine's playlist keeps its entry for it until the next "
                           "rotation rebuilds the playlist; that entry is skipped meanwhile.")
    return ConfirmDialog(title, body, parent, destructive=True, icon="trash",
                         lines=[folder], note=note, confirm_text=confirm)


def deleted_words(name: str, result) -> tuple[str, str]:
    """(tone, words) for the toast once Delete has run: `result` is what
    `delete_wallpaper` returned, or the exception it raised."""
    if isinstance(result, Deleted):
        if result.workshop_id:
            return "ok", f"Unsubscribed from “{name}” and moved its folder to the Recycle Bin."
        return "ok", f"“{name}” is in the Recycle Bin."
    reason = str(result) if isinstance(result, DeleteError) else f"{result}."
    return "danger", f"Could not delete “{name}”: {reason}"


def open_in_explorer(path: str, failed: Callable[[str], None]) -> None:
    """Open Explorer in a wallpaper folder, on a thread (the disk may be
    asleep). `failed` gets what went wrong, from that thread."""
    def run() -> None:
        target = Path(path)
        try:
            if target.is_dir():
                external.popen(["explorer", os.path.normpath(str(target))])
                return
            message = f"The folder is not there any more: {target}"
        except OSError as err:
            message = f"Could not open Explorer: {err}"
        try:
            failed(message)
        except RuntimeError:
            pass                # the page went while the disk was answering
    threading.Thread(target=run, daemon=True, name="open-folder").start()
