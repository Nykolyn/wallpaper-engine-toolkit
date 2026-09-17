"""steam_ugc.py — subscribing to a wallpaper without leaving the window.

Wallpaper Engine's own Subscribe button is not a web request. Taking its
install apart: `bin/steam_api64.dll` is Valve's Steamworks redistributable and
exports `SteamAPI_ISteamUGC_SubscribeItem`; `wallpaperui.exe` — the wallpaper
browser, not the renderer — loads it and asks for
`STEAMUGC_INTERFACE_VERSION020`. So a subscription is a local call into the
running Steam client, which performs it for whoever is logged in. Nothing
identifies *Wallpaper Engine* to Steam except the app id its process declares.

That is the whole trick, and it is reproducible here — verified before this
module was written, with a read-only call:

    SteamAPI_InitFlat -> 0
    ISteamUGC pointer -> 0x15a81c20000
    GetNumSubscribedItems -> 1283          (1 288 folders on disk)

**What this costs.** The process has to declare app id 431960 while the API is
open, so Steam may show Wallpaper Engine as running. The connection is
therefore opened for the length of one action and closed again, rather than
held for the session. Valve does not sanction using an app id you do not own:
this is not a safety or licensing problem — the account, the machine and the
copy of Wallpaper Engine are all the user's, and workshop items are free — but
it is unsupported, and a Steam update could stop it working without warning.
Everything here is therefore optional: :meth:`SteamUgc.available` says whether
it can work at all, and the review falls back to opening Steam's own page.

The DLL is never shipped. It is loaded from the Wallpaper Engine install that
is already on the machine.
"""
from __future__ import annotations

import ctypes
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

from .tracker import find_we_config

APP_ID = 431960
UGC_ACCESSOR = "SteamAPI_SteamUGC_v020"

# ISteamUGC::GetItemState, as a bitmask.
STATE_NONE = 0
STATE_SUBSCRIBED = 1
STATE_LEGACY = 2
STATE_INSTALLED = 4
STATE_NEEDS_UPDATE = 8
STATE_DOWNLOADING = 16
STATE_DOWNLOAD_PENDING = 32


class UgcError(RuntimeError):
    """Steam is not there, or would not do what was asked."""


@dataclass
class ItemState:
    """What Steam currently thinks about one workshop item."""

    id: str
    flags: int = 0

    @property
    def subscribed(self) -> bool:
        return bool(self.flags & STATE_SUBSCRIBED)

    @property
    def installed(self) -> bool:
        return bool(self.flags & STATE_INSTALLED)

    @property
    def downloading(self) -> bool:
        return bool(self.flags & (STATE_DOWNLOADING | STATE_DOWNLOAD_PENDING))

    @property
    def ready(self) -> bool:
        """Subscribed *and* on disk — the point at which a card can go green."""
        return self.subscribed and self.installed and not self.downloading

    def describe(self) -> str:
        if not self.flags:
            return "not subscribed"
        parts = []
        if self.subscribed:
            parts.append("subscribed")
        if self.downloading:
            parts.append("downloading")
        elif self.installed:
            parts.append("installed")
        if self.flags & STATE_NEEDS_UPDATE:
            parts.append("needs update")
        return ", ".join(parts) or f"state {self.flags}"


def find_steam_api(hint: str | Path | None = None) -> Path | None:
    """Wallpaper Engine's copy of the Steamworks DLL, if it is installed."""
    candidates: list[Path] = []
    if hint:
        candidates.append(Path(hint))
    config = find_we_config()
    if config:
        candidates.append(Path(config).parent / "bin" / "steam_api64.dll")
    for path in candidates:
        if path.is_file():
            return path
    return None


class SteamUgc:
    """The Steamworks user-content interface, opened only while it is needed.

    Used as a context manager, so the app id is declared for the length of one
    subscription rather than for the whole time the toolkit is running::

        with SteamUgc() as ugc:
            ugc.subscribe("3796616409")
    """

    def __init__(self, dll: str | Path | None = None,
                 on_log: Callable[[str], None] | None = None):
        self.path = find_steam_api(dll)
        self._lib = None
        self._ugc = None
        self._log = on_log or (lambda _message: None)

    # -- availability ------------------------------------------------------

    @property
    def available(self) -> bool:
        """Whether the DLL is even there. Says nothing about Steam running."""
        return self.path is not None

    @property
    def open(self) -> bool:
        return self._ugc is not None

    def check(self) -> str:
        """A sentence for the settings panel: can this work, and if not why."""
        if not self.available:
            return "Wallpaper Engine's Steamworks library was not found"
        try:
            with self:
                return f"ready — Steam reports {self.subscribed_count()} subscriptions"
        except UgcError as err:
            return str(err)

    # -- opening and closing ----------------------------------------------

    def connect(self) -> "SteamUgc":
        if self._ugc is not None:
            return self
        if not self.available:
            raise UgcError("Wallpaper Engine's steam_api64.dll was not found")
        # Steam reads the app id from the environment when the process was not
        # launched by Steam itself.
        os.environ["SteamAppId"] = str(APP_ID)
        os.environ["SteamGameId"] = str(APP_ID)
        try:
            cookie = os.add_dll_directory(str(self.path.parent))
        except (AttributeError, OSError):
            cookie = None
        try:
            lib = ctypes.CDLL(str(self.path))
        except OSError as err:
            raise UgcError(f"could not load {self.path.name}: {err}") from err
        finally:
            if cookie is not None:
                cookie.close()

        message = ctypes.create_string_buffer(1024)
        lib.SteamAPI_InitFlat.restype = ctypes.c_int
        lib.SteamAPI_InitFlat.argtypes = [ctypes.c_char_p]
        if lib.SteamAPI_InitFlat(message) != 0:
            raise UgcError("Steam is not running, or is not signed in "
                           f"({message.value.decode(errors='replace') or 'no detail'})")

        accessor = getattr(lib, UGC_ACCESSOR, None)
        if accessor is None:
            lib.SteamAPI_Shutdown()
            raise UgcError(f"{self.path.name} has no {UGC_ACCESSOR}; "
                           "Wallpaper Engine's SDK version has moved on")
        accessor.restype = ctypes.c_void_p
        pointer = accessor()
        if not pointer:
            lib.SteamAPI_Shutdown()
            raise UgcError("Steam refused the user-content interface")

        self._lib, self._ugc = lib, pointer
        self._bind()
        return self

    def _bind(self) -> None:
        lib = self._lib
        lib.SteamAPI_ISteamUGC_SubscribeItem.restype = ctypes.c_uint64
        lib.SteamAPI_ISteamUGC_SubscribeItem.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
        lib.SteamAPI_ISteamUGC_UnsubscribeItem.restype = ctypes.c_uint64
        lib.SteamAPI_ISteamUGC_UnsubscribeItem.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
        lib.SteamAPI_ISteamUGC_GetItemState.restype = ctypes.c_uint32
        lib.SteamAPI_ISteamUGC_GetItemState.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
        lib.SteamAPI_RunCallbacks.restype = None
        lib.SteamAPI_RunCallbacks.argtypes = []

    def close(self) -> None:
        if self._lib is not None:
            try:
                self._lib.SteamAPI_Shutdown()
            except Exception:  # noqa: BLE001 — closing must not raise
                pass
        self._lib = self._ugc = None

    def __enter__(self) -> "SteamUgc":
        return self.connect()

    def __exit__(self, *_exc) -> None:
        self.close()

    def _require(self):
        if self._ugc is None:
            raise UgcError("not connected to Steam")
        return self._ugc

    # -- reading -----------------------------------------------------------

    def subscribed_count(self) -> int:
        ugc = self._require()
        call = self._lib.SteamAPI_ISteamUGC_GetNumSubscribedItems
        call.restype = ctypes.c_uint32
        # The flag arrived in a later SDK; the older signature takes none.
        for argtypes, args in (([ctypes.c_void_p, ctypes.c_bool], (ugc, True)),
                               ([ctypes.c_void_p], (ugc,))):
            try:
                call.argtypes = argtypes
                return int(call(*args))
            except (ctypes.ArgumentError, TypeError):
                continue
        raise UgcError("GetNumSubscribedItems would not accept either signature")

    def state(self, item_id: str | int) -> ItemState:
        """What Steam thinks of one item — the truth behind a card's colour."""
        ugc = self._require()
        flags = self._lib.SteamAPI_ISteamUGC_GetItemState(ugc, int(item_id))
        return ItemState(id=str(item_id), flags=int(flags))

    def states(self, item_ids: Iterable[str | int]) -> dict[str, ItemState]:
        return {str(i): self.state(i) for i in item_ids}

    # -- writing -----------------------------------------------------------

    def subscribe(self, item_id: str | int, wait: float = 0.0) -> ItemState:
        """Subscribe, the way the Subscribe button does.

        The call is asynchronous and its result arrives on a callback nobody
        here is listening for, so success is read back from the item's own
        state instead — which is what the gallery needs anyway. ``wait``
        seconds of polling turns "asked for" into "Steam agrees it is
        subscribed"; the download itself takes longer and is watched by the
        library scan.
        """
        ugc = self._require()
        self._lib.SteamAPI_ISteamUGC_SubscribeItem(ugc, int(item_id))
        self._log(f"asked Steam to subscribe to {item_id}")
        return self._settle(item_id, wait, want=True)

    def unsubscribe(self, item_id: str | int, wait: float = 0.0) -> ItemState:
        ugc = self._require()
        self._lib.SteamAPI_ISteamUGC_UnsubscribeItem(ugc, int(item_id))
        self._log(f"asked Steam to unsubscribe from {item_id}")
        return self._settle(item_id, wait, want=False)

    def subscribe_many(self, item_ids: Sequence[str | int], wait: float = 0.0,
                       on_progress: Callable[[int, int], None] | None = None,
                       ) -> dict[str, ItemState]:
        out: dict[str, ItemState] = {}
        for done, item_id in enumerate(item_ids, 1):
            out[str(item_id)] = self.subscribe(item_id, wait=wait)
            if on_progress:
                on_progress(done, len(item_ids))
        return out

    def _settle(self, item_id: str | int, wait: float, want: bool) -> ItemState:
        deadline = time.monotonic() + max(0.0, wait)
        state = self.state(item_id)
        while state.subscribed != want and time.monotonic() < deadline:
            self._lib.SteamAPI_RunCallbacks()
            time.sleep(0.1)
            state = self.state(item_id)
        return state
