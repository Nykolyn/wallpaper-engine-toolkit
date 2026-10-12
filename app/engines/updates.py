"""Published GitHub installers, downloaded and checked before Inno Setup runs."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .. import __version__, external, install_info

REPOSITORY = "Nykolyn/wallpaper-engine-toolkit"
RELEASES = f"https://github.com/{REPOSITORY}/releases"
LATEST = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
CHUNK = 256 * 1024
MAX_METADATA = 1024 * 1024
TIMEOUT = 20


@dataclass(frozen=True)
class Release:
    version: str
    notes: str
    url: str
    size: int
    sha256: str

    @property
    def filename(self) -> str:
        return f"WallpaperEngineToolkit-Setup-{self.version}.exe"


class Cancelled(Exception):
    """The download was cancelled; nothing is ready to install."""


def version(text: str) -> tuple[int, int, int]:
    if not isinstance(text, str) or not re.fullmatch(r"v?\d{1,6}\.\d{1,6}\.\d{1,6}", text):
        raise ValueError("The release has an unsupported version number.")
    return tuple(int(part) for part in text.removeprefix("v").split("."))


def parse(data: dict, current: str = __version__) -> Release | None:
    """Accept only a newer stable release and its exact installer with SHA-256."""
    if not isinstance(data, dict):
        raise ValueError("GitHub returned an invalid release.")
    if data.get("draft") or data.get("prerelease"):
        return None
    tag = data.get("tag_name", "")
    if version(tag) <= version(current):
        return None
    number = tag.removeprefix("v")
    filename = f"WallpaperEngineToolkit-Setup-{number}.exe"
    assets = [a for a in data.get("assets", []) if isinstance(a, dict) and a.get("name") == filename]
    if len(assets) != 1:
        raise ValueError("The release's installer is not ready yet. Try again later.")
    asset = assets[0]
    expected = f"{RELEASES}/download/{tag}/{filename}"
    digest, size = asset.get("digest", ""), asset.get("size")
    if (asset.get("browser_download_url") != expected
            or not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
            or type(size) is not int or size <= 0):
        raise ValueError("The release's installer cannot be verified.")
    return Release(number, str(data.get("body") or ""), expected, size, digest[7:])


def _open(url: str):
    request = urllib.request.Request(url, headers={"User-Agent": "WallpaperEngineToolkit/" + __version__,
                                                "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(request, timeout=TIMEOUT)


def check() -> Release | None:
    with _open(LATEST) as response:
        raw = response.read(MAX_METADATA + 1)
    if len(raw) > MAX_METADATA:
        raise ValueError("GitHub returned too much release information.")
    return parse(json.loads(raw))


def verify(path: Path, release: Release) -> None:
    """Recheck the file before running it, including a previously downloaded copy."""
    if path.stat().st_size != release.size:
        raise ValueError("The downloaded installer has the wrong size. Download it again.")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(CHUNK), b""):
            digest.update(block)
    if digest.hexdigest() != release.sha256:
        raise ValueError("The downloaded installer failed its SHA-256 check. Download it again.")


def download(release: Release, folder: Path, cancel: threading.Event, progress=lambda done, total: None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / release.filename
    partial = target.with_suffix(".partial")
    try:
        if cancel.is_set():
            raise Cancelled()
        if shutil.disk_usage(folder).free < release.size:
            raise OSError("There is not enough disk space to download the update.")
        with _open(release.url) as response:
            # GitHub redirects assets to its HTTPS content hosts, never to an arbitrary server.
            destination = urlsplit(response.geturl())
            if (destination.scheme != "https" or destination.hostname not in
                    ("github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com")):
                raise ValueError("GitHub redirected the installer to an unexpected address.")
            done = 0
            with partial.open("wb") as stream:
                while True:
                    if cancel.is_set():
                        raise Cancelled()
                    block = response.read(CHUNK)
                    if not block:
                        break
                    done += len(block)
                    if done > release.size:
                        raise ValueError("The downloaded installer is larger than expected.")
                    stream.write(block)
                    progress(done, release.size)
        verify(partial, release)
        if cancel.is_set():
            raise Cancelled()
        partial.replace(target)
        for old in folder.glob("WallpaperEngineToolkit-Setup-*.exe"):
            if old != target:
                try:
                    old.unlink()
                except OSError:
                    pass  # a previous setup may still be exiting after reopening the app
        return target
    finally:
        partial.unlink(missing_ok=True)


def install(path: Path, release: Release) -> int:
    """The installer outlives this window and asks it and the tracker to quit."""
    if not install_info.is_installed_copy():
        raise ValueError("Install the release's setup first to enable in-app installation.")
    verify(path, release)
    process = external.popen([str(path), "/VERYSILENT", "/SP-", "/NORESTART", "/UpdateFromApp=1",
                              f"/LOG={path.parent / 'install.log'}"], cwd=str(path.parent))
    return process.wait()
