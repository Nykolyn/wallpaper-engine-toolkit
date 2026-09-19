"""playlist_refresh.py — after a rotation, Wallpaper Engine plays what it brought.

A rotation swaps the folders in myprojects, but the playlist in Wallpaper
Engine still lists the old ones, and starting it over by hand — open the
playlist, clear it, add everything, save, apply — was the one step the Rotator
left to you. This does it:

1. **Before the move**, Wallpaper Engine is closed the way its tray closes it
   (see engine_control) and its ``config.json`` is read as it left it.
2. **The rotation's playlist is found by what is in it, never by name.** It is
   any playlist — saved, or running on a monitor — most of whose wallpapers are
   folders the rotation is about to take back, and which holds most of those
   folders. Renaming it, or keeping a saved twin of it, changes nothing; a
   playlist of workshop items, or of a few favourites, never qualifies.
3. **After the move** each such playlist is given every wallpaper now in
   myprojects, keeping its name, its settings and whatever it holds from
   elsewhere. A monitor that was playing it starts a fresh pass: its deck in
   ``playliststate.bin`` is rewritten to the whole new list, since Wallpaper
   Engine would otherwise resume the old pass — wallpapers that are gone.
4. Wallpaper Engine is started again, exactly as it was started before.

Both files are copied into ``data/playlist-refresh/`` before they are
rewritten, and replaced whole, never edited in place. Wallpaper Engine is
started again whatever happened in between.
"""
from __future__ import annotations

import json
import os
import random
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import engine_control, steam_paths
from .rotator.core import ProgressEvent, is_protected, list_subfolders
from .wallpaper_timer import SECTION, STATE_FILE, read_state_file, write_state_file

# A playlist belongs to the rotation when at least this share of its wallpapers
# are folders the rotation takes back, and they are at least this share of all
# the folders it takes back.
SHARE = 0.5

Progress = Callable[[ProgressEvent], None]


def _noop(_e: ProgressEvent) -> None:
    pass


def backup_dir() -> Path:
    from ..settings import app_data_dir
    return app_data_dir() / "playlist-refresh"


# ---- Playlist items ------------------------------------------------------------

def playlist_item(folder: Path) -> str | None:
    """The path Wallpaper Engine lists for this wallpaper in a playlist, or None.

    It is the file its project.json names, with forward slashes — except a
    scene shipped compiled: its manifest names ``scene.json``, which is packed
    inside ``scene.pkg``, and the playlist lists the ``.pkg``. That reproduces
    every one of 200 items of a playlist Wallpaper Engine built itself.
    """
    try:
        manifest = json.loads((folder / "project.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    name = manifest.get("file") if isinstance(manifest, dict) else None
    if not isinstance(name, str) or not name.strip():
        return None
    target = folder / name
    if not target.is_file():
        packed = target.with_suffix(".pkg")
        if not packed.is_file():
            return None
        target = packed
    return str(target).replace("\\", "/")


def build_items(destination: str) -> list[str]:
    """A playlist item for every wallpaper in `destination`, in folder order."""
    root = Path(destination)
    items = []
    for name in list_subfolders(root):
        item = playlist_item(root / name)
        if item is not None:
            items.append(item)
    return items


def _key(path: str) -> str:
    return os.path.normcase(os.path.normpath(path.replace("/", "\\")))


def folder_in(item: str, destination: str) -> str | None:
    """The folder of `destination` this playlist item lives in, lower-cased."""
    root = _key(destination).rstrip("\\") + "\\"
    path = _key(item)
    if not path.startswith(root):
        return None
    folder = path[len(root):].split("\\")[0]
    return folder or None


# ---- Finding the rotation's playlists -------------------------------------------

@dataclass
class Found:
    """One playlist in config.json that is the rotation's."""
    user: str
    name: str
    monitor: str | None = None          # None: a saved playlist
    index: int = -1                     # position among the saved ones

    @property
    def label(self) -> str:
        return f"'{self.name}' on {self.monitor}" if self.monitor else f"saved '{self.name}'"


def belongs(items: list[str], destination: str, rotating: set[str]) -> bool:
    """Whether a playlist of `items` is the one built from the `rotating` folders."""
    if not items or not rotating:
        return False
    folders = [folder_in(i, destination) for i in items]
    hits = [f for f in folders if f is not None and f in rotating]
    return (len(hits) >= SHARE * len(items)
            and len(set(hits)) >= SHARE * len(rotating))


def _users(config: dict):
    for user, section in config.items():
        general = section.get("general") if isinstance(section, dict) else None
        if isinstance(general, dict):
            yield user, general


def _items(playlist) -> list[str]:
    if not isinstance(playlist, dict):
        return []
    return [i for i in playlist.get("items") or [] if isinstance(i, str)]


def find_playlists(config: dict, destination: str, rotating: set[str]) -> list[Found]:
    """Every playlist, saved or running, that belongs to the rotation."""
    found: list[Found] = []
    for user, general in _users(config):
        for index, playlist in enumerate(general.get("playlists") or []):
            if belongs(_items(playlist), destination, rotating):
                found.append(Found(user, str(playlist.get("name") or "(unnamed)"),
                                   index=index))
        selected = (general.get("wallpaperconfig") or {}).get("selectedwallpapers") or {}
        for monitor, cfg in sorted(selected.items()):
            playlist = cfg.get("playlist") if isinstance(cfg, dict) else None
            if belongs(_items(playlist), destination, rotating):
                found.append(Found(user, str(playlist.get("name") or "(unnamed)"),
                                   monitor=monitor))
    return found


def rotating_folders(destination: str) -> set[str]:
    """The folders a rotation takes back: all of myprojects but the protected."""
    return {n.lower() for n in list_subfolders(destination) if not is_protected(n)}


def preview(destination: str) -> list[str]:
    """What a rotation would rebuild, going by config.json as it is on disk now.

    For the confirmation dialog. The rotation itself looks again once Wallpaper
    Engine has closed and written its last word.
    """
    path = steam_paths.we_config()
    try:
        config = json.loads(Path(path).read_text(encoding="utf-8-sig")) if path else None
    except (OSError, ValueError):
        return []
    if not isinstance(config, dict):
        return []
    found = find_playlists(config, destination, rotating_folders(destination))
    return [f.label for f in found]


# ---- Rewriting ----------------------------------------------------------------

def refill(items: list[str], destination: str, fresh: list[str]) -> list[str]:
    """`items` with everything from `destination` replaced by `fresh`."""
    return [i for i in items if folder_in(i, destination) is None] + fresh


def first_wallpaper(items: list[str], order: str) -> str:
    """What a fresh pass opens with: a random one, unless the playlist is sorted."""
    return random.choice(items) if order == "random" else items[0]


def rewrite_config(config: dict, found: list[Found], destination: str,
                   fresh: list[str]) -> dict[str, list[str]]:
    """Refill every found playlist in place; the new list per monitor restarted."""
    restarted: dict[str, list[str]] = {}
    for f in found:
        general = config[f.user]["general"]
        if f.monitor is None:
            playlist = general["playlists"][f.index]
            playlist["items"] = refill(_items(playlist), destination, fresh)
            continue
        cfg = general["wallpaperconfig"]["selectedwallpapers"][f.monitor]
        playlist = cfg["playlist"]
        playlist["items"] = refill(_items(playlist), destination, fresh)
        order = str((playlist.get("settings") or {}).get("order", "random"))
        cfg["file"] = first_wallpaper(playlist["items"], order)
        restarted[f.monitor] = playlist["items"]
    return restarted


def restart_passes(state: bytes, config: dict, restarted: dict[str, list[str]]) -> bytes:
    """playliststate.bin with each restarted monitor at the start of a fresh pass."""
    sections = read_state_file(state)
    current = {}
    for _user, general in _users(config):
        selected = (general.get("wallpaperconfig") or {}).get("selectedwallpapers") or {}
        for monitor in restarted:
            if monitor in selected:
                current[monitor] = selected[monitor].get("file")
    for section in sections:
        if section.name != SECTION:
            continue
        for monitor in section.monitors:
            items = restarted.get(monitor.name)
            if items:
                monitor.start_over(items, current.get(monitor.name) or items[0])
    return write_state_file(sections)


def dump_config(config: dict) -> bytes:
    """config.json in Wallpaper Engine's own style: tabs, CRLF, raw UTF-8."""
    text = json.dumps(config, indent="\t", ensure_ascii=False, separators=(",", " : "))
    return text.replace("\n", "\r\n").encode("utf-8")


def _replace(path: Path, data: bytes) -> None:
    temp = path.with_name(path.name + ".toolkit-new")
    temp.write_bytes(data)
    os.replace(temp, path)


# ---- Around a rotation ----------------------------------------------------------

@dataclass
class PlaylistRefresh:
    """Closes Wallpaper Engine before a rotation, points it at the new set after.

    prepare() before the files move, finish() after — always, even when the
    rotation failed or was stopped, because finish() is what starts Wallpaper
    Engine again.
    """
    destination: str
    progress: Progress = _noop
    engine: engine_control.Engine | None = None
    config_path: Path | None = None
    found: list[Found] = field(default_factory=list)
    summary: list[str] = field(default_factory=list)
    # config.json as Wallpaper Engine left it on closing; what is rewritten.
    config: dict | None = None

    def _say(self, message: str, level: str = "INFO") -> None:
        self.progress(ProgressEvent("playlist", message, level=level))
        if level != "INFO":
            self.summary.append(message)

    def prepare(self) -> None:
        try:
            self._prepare()
        except Exception as e:  # noqa: BLE001 — the rotation goes ahead without it
            self.found = []
            self._say(f"Wallpaper Engine's playlist could not be looked at: {e}", "ERROR")

    def _prepare(self) -> None:
        self.engine = engine_control.running()
        if self.engine is not None:
            self._say("Closing Wallpaper Engine, so its playlist can be rewritten...")
            if not engine_control.close(self.engine):
                self._say("Wallpaper Engine did not close, so its playlist stays as it "
                          "is — rebuild it there by hand this time.", "WARN")
                self.engine = None          # still running: nothing to start again
                return
            self.config_path = self.engine.install_dir / "config.json"
        else:
            self.config_path = steam_paths.we_config()
            self._say("Wallpaper Engine is not running; its playlist is rewritten for "
                      "the next time it starts.")
        if self.config_path is None or not self.config_path.is_file():
            self._say("Wallpaper Engine's config.json was not found, so no playlist "
                      "can be rewritten.", "WARN")
            self.config_path = None
            return
        try:
            config = json.loads(self.config_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as e:
            self._say(f"Wallpaper Engine's config.json could not be read ({e}).", "WARN")
            self.config_path = None
            return
        self.config = config
        self.found = find_playlists(config, self.destination,
                                    rotating_folders(self.destination))
        if self.found:
            self._say("The rotation's playlist: "
                      + ", ".join(f.label for f in self.found) + ".")
        else:
            self._say("No playlist in Wallpaper Engine is made of what is in myprojects "
                      "now. Build one there once from this rotation, and every rotation "
                      "after keeps it current.", "WARN")

    def finish(self, completed: bool) -> None:
        try:
            if not completed:
                if self.found:
                    self._say("The rotation did not finish, so the playlist was left "
                              "as it was.", "WARN")
            elif self.found and self.config is not None:
                self._rewrite()
        except Exception as e:  # noqa: BLE001 — Wallpaper Engine must come back regardless
            self._say(f"Rewriting the playlist failed: {e}", "ERROR")
        finally:
            if self.engine is not None:
                self._say("Starting Wallpaper Engine again...")
                if engine_control.start(self.engine):
                    self._say("Wallpaper Engine is running again.")
                else:
                    self._say("Wallpaper Engine did not come back — start it by hand.",
                              "ERROR")

    def _rewrite(self) -> None:
        config_path, config = self.config_path, self.config
        state_path = config_path.parent / STATE_FILE
        fresh = build_items(self.destination)
        if not fresh:
            self._say("myprojects holds nothing Wallpaper Engine could play, so the "
                      "playlist was left as it was.", "WARN")
            return
        restarted = rewrite_config(config, self.found, self.destination, fresh)
        state = state_path.read_bytes() if state_path.is_file() else None

        backups = backup_dir()
        backups.mkdir(parents=True, exist_ok=True)
        shutil.copy2(config_path, backups / "config.json")
        if state is not None:
            shutil.copy2(state_path, backups / state_path.name)

        # The state first: a look at config.json in between then finds a deck
        # that already fits the playlist it describes.
        if state is not None and restarted:
            _replace(state_path, restart_passes(state, config, restarted))
        _replace(config_path, dump_config(config))
        self._say(f"Playlist rewritten with {len(fresh)} wallpapers: "
                  + ", ".join(f.label for f in self.found) + ".")
        self.summary.append(f"Playlist rebuilt with {len(fresh)} wallpapers "
                            f"({', '.join(sorted({f.name for f in self.found}))}).")
