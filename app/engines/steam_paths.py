"""Finding Steam, Wallpaper Engine and the workshop on whatever machine this is.

Every folder this app works with hangs off one question: where did Steam put
Wallpaper Engine? Answering it by hard-coding a path works on exactly one
computer, and quietly presents every other computer with an empty folder picker
and no explanation.

So it is asked properly. Steam records its own location in the registry, and
records its *library* locations — the extra drives a user spreads games across
— in ``steamapps/libraryfolders.vdf``. Wallpaper Engine is app **431960**, and
the library that owns it is the one whose ``apps`` block lists that id. From
there the rest is fixed by Steam's own layout:

    <library>/steamapps/common/wallpaper_engine/          the install
    <library>/steamapps/common/wallpaper_engine/config.json
    <library>/steamapps/common/wallpaper_engine/projects/myprojects/
    <library>/steamapps/workshop/content/431960/          subscribed items

Nothing here raises. A machine without Steam, a stripped registry, a Steam that
has never been opened — each of them simply yields ``None``, and the caller
shows an empty field instead of a wrong one. The results are cached because
they cannot change while the app is running without Steam being reinstalled
underneath it.
"""
from __future__ import annotations

import re
import sys
from functools import lru_cache
from pathlib import Path

# Wallpaper Engine on Steam.
WALLPAPER_ENGINE_APPID = "431960"

# Where Steam keeps itself, in the order worth trying.
_REGISTRY_KEYS = (
    ("HKEY_CURRENT_USER", r"Software\Valve\Steam", "SteamPath"),
    ("HKEY_LOCAL_MACHINE", r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
    ("HKEY_LOCAL_MACHINE", r"SOFTWARE\Valve\Steam", "InstallPath"),
)

# Used only when the registry says nothing at all.
_FALLBACK_ROOTS = (
    r"C:\Program Files (x86)\Steam",
    r"C:\Program Files\Steam",
)


def _from_registry() -> str:
    """Steam's own idea of where it lives, or "" off Windows."""
    if sys.platform != "win32":
        return ""
    import winreg

    for hive_name, subkey, value_name in _REGISTRY_KEYS:
        hive = getattr(winreg, hive_name)
        try:
            with winreg.OpenKey(hive, subkey) as key:
                value, _ = winreg.QueryValueEx(key, value_name)
        except OSError:
            continue
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


@lru_cache(maxsize=1)
def steam_root() -> Path | None:
    """The Steam installation folder, or None if it cannot be found.

    Steam writes this path with forward slashes under HKCU and backslashes
    under HKLM; ``Path`` settles that difference by itself.
    """
    candidate = _from_registry()
    if candidate and Path(candidate).is_dir():
        return Path(candidate)
    for fallback in _FALLBACK_ROOTS:
        if Path(fallback).is_dir():
            return Path(fallback)
    return None


# A library entry is `"path"  "D:\SteamLibrary"`, with the backslashes doubled
# the way a VDF escapes them. Reading only this one key is deliberate: the file
# is a full nested format, and a parser for all of it would be a liability for
# the one string that is wanted.
_PATH_LINE = re.compile(r'"path"\s+"([^"]+)"')
_APP_BLOCK = re.compile(r'"apps"\s*\{(.*?)\}', re.DOTALL)


def _library_blocks(text: str) -> list[tuple[Path, str]]:
    """Each library in libraryfolders.vdf, as (path, the apps listed in it)."""
    blocks: list[tuple[Path, str]] = []
    for match in _PATH_LINE.finditer(text):
        raw = match.group(1).replace("\\\\", "\\")
        apps = _APP_BLOCK.search(text, match.end())
        blocks.append((Path(raw), apps.group(1) if apps else ""))
    return blocks


@lru_cache(maxsize=1)
def library_roots() -> tuple[Path, ...]:
    """Every Steam library on this machine, the install folder first."""
    root = steam_root()
    if root is None:
        return ()
    found: list[Path] = [root]
    vdf = root / "steamapps" / "libraryfolders.vdf"
    try:
        text = vdf.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return tuple(found)
    for path, _apps in _library_blocks(text):
        if path not in found and path.is_dir():
            found.append(path)
    return tuple(found)


@lru_cache(maxsize=None)
def library_for_app(appid: str = WALLPAPER_ENGINE_APPID) -> Path | None:
    """The library that holds an app.

    The `apps` block is the authority — it is what Steam itself consults — but
    a library whose manifest is stale still counts if the folder is really
    there, so both are tried before giving up.
    """
    root = steam_root()
    if root is None:
        return None
    vdf = root / "steamapps" / "libraryfolders.vdf"
    try:
        text = vdf.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    for path, apps in _library_blocks(text):
        if f'"{appid}"' in apps and path.is_dir():
            return path
    for path in library_roots():
        if (path / "steamapps" / "common" / "wallpaper_engine").is_dir():
            return path
    return None


@lru_cache(maxsize=1)
def wallpaper_engine_dir() -> Path | None:
    """Wallpaper Engine's install folder, or None."""
    library = library_for_app()
    if library is None:
        return None
    installed = library / "steamapps" / "common" / "wallpaper_engine"
    return installed if installed.is_dir() else None


@lru_cache(maxsize=1)
def workshop_dir() -> Path | None:
    """Where subscribed wallpapers are downloaded, or None.

    This one is *not* required to exist: a fresh install with no subscriptions
    yet has no such folder, and offering the path anyway is more useful than
    offering nothing, because it is where the items will appear.
    """
    library = library_for_app()
    if library is None:
        return None
    return library / "steamapps" / "workshop" / "content" / WALLPAPER_ENGINE_APPID


@lru_cache(maxsize=1)
def myprojects_dir() -> Path | None:
    """Where Wallpaper Engine keeps wallpapers made on this machine, or None."""
    installed = wallpaper_engine_dir()
    return None if installed is None else installed / "projects" / "myprojects"


@lru_cache(maxsize=1)
def we_config() -> Path | None:
    """Wallpaper Engine's own config.json — the playlist and per-monitor state."""
    installed = wallpaper_engine_dir()
    if installed is None:
        return None
    config = installed / "config.json"
    return config if config.is_file() else None


def as_text(path: Path | None) -> str:
    """A path for a settings default: the real one, or "" to leave a field empty.

    An empty field asks a question. A wrong path answers it wrongly, and the
    difference matters in a folder picker that is about to move files.
    """
    return str(path) if path is not None else ""


def forget() -> None:
    """Drop the cache. For tests, and for after Steam moves under a running app."""
    for cached in (steam_root, library_roots, library_for_app, wallpaper_engine_dir,
                   workshop_dir, myprojects_dir, we_config):
        cached.cache_clear()
