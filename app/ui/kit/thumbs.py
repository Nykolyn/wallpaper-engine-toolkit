"""Wallpaper previews: fetched or read once, kept on disk, handed over small.

One loader, two sources:

- **Steam's preview URLs**, for the Review gallery: downloaded once into
  `data/thumbs/<id>.img` and decoded off the GUI thread (`request`, `done`).
  This is the gallery's loader, moved here unchanged; `app/ui/gallery.py`
  imports it from the kit.
- **Local preview files**, for tables and cards: `preview.gif`, `.jpg` or
  `.png` inside a wallpaper folder (`request_local`, `local_done`). Those
  folders live on W:, a 12 TB hard disk, so nothing here runs on the GUI
  thread: the folder is listed, the preview read and one still frame taken on
  a worker. The still is kept as a small JPEG in `data/thumbs/local/`, keyed by
  the preview's path, modification time and size, so an unchanged preview is
  never decoded twice and an edited one is read again.

A view asks only for the rows it shows, once scrolling settles, and calls
`retarget_local()` first so nothing queued for rows scrolled past is read.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from PySide6.QtCore import (
    QBuffer, QByteArray, QIODevice, QObject, QRunnable, QSize, Qt, QThreadPool, Signal,
)
from PySide6.QtGui import QImage, QImageReader, QPainter

from ... import theme
from ...settings import app_data_dir

THUMB_DIR = app_data_dir() / "thumbs"
LOCAL_DIR = THUMB_DIR / "local"

# How hard to look for a frame worth showing, and what counts as one.
MAX_STILL_FRAMES = 24
STILL_MIN_BRIGHTNESS = 0.06

# The still kept on disk fits in this box. It is the largest thumb the screens
# draw (a MonitorCard's 298 × 84 preview) at 150 %, with room to spare, and at
# JPEG quality 85 it is a few tens of KB, which 33 000 folders can afford.
LOCAL_BOX = QSize(480, 270)
LOCAL_QUALITY = 85

# A wallpaper folder's preview, in the order one is preferred when a folder
# has several: the animated one is the one Wallpaper Engine shows.
PREVIEW_NAMES = ("preview.gif", "preview.jpg", "preview.jpeg", "preview.png", "preview.webp")
IMAGE_SUFFIXES = (".gif", ".jpg", ".jpeg", ".png", ".webp", ".bmp")

# Two readers at most: the previews sit on a hard disk, where more readers
# mean more seeking rather than more reading.
LOCAL_THREADS = 2


# ---- Steam previews (the Review gallery) -----------------------------------------------

class _Fetch(QRunnable):
    """One preview, off the GUI thread."""

    def __init__(self, loader: "ThumbLoader", item_id: str, url: str):
        super().__init__()
        self.loader = loader
        self.item_id = item_id
        self.url = url

    def run(self) -> None:
        import urllib.request

        if self.loader.stopped:
            return
        path = self.loader.path_for(self.item_id)
        data = b""
        if path.exists():
            try:
                data = path.read_bytes()
            except OSError:
                data = b""
        if not data:
            try:
                request = urllib.request.Request(
                    self.url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(request, timeout=20) as response:
                    data = response.read()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            except Exception:  # noqa: BLE001 — a missing preview is not an error
                data = b""
        if self.loader.stopped:
            return
        blob = QByteArray(data)
        try:
            self.loader.done.emit(self.item_id, blob, _still_image(blob))
        except RuntimeError:
            # The gallery was closed while this was in flight. A preview
            # nobody is waiting for is not worth a traceback.
            pass


def _still_image(data: QByteArray) -> QImage:
    """One frame to stand for a preview — decoded here, off the GUI thread.

    Nearly half the previews in a real gallery are animated (46 of 90 in the
    first author looked at), and Steam serves those as GIF. Taking frame zero
    fills the grid with black tiles: these wallpapers commonly fade in, and
    measured on this library four in fourteen were still pure black twelve
    frames in. So frames are read in order until one is bright enough to be a
    picture, with a cap so a wallpaper that really is dark still gets a frame.

    A `QImage`, not a `QPixmap`: pixmaps may only be made on the GUI thread,
    and decoding twenty frames there for ninety cards is a stutter.
    """
    if data.isEmpty():
        return QImage()
    buffer = QBuffer()
    buffer.setData(data)
    buffer.open(QIODevice.ReadOnly)
    try:
        reader = QImageReader(buffer)
        reader.setDecideFormatFromContent(True)
        return _still_frame(reader)
    finally:
        buffer.close()


def _still_frame(reader: QImageReader) -> QImage:
    """The first frame bright enough to be a picture, or the brightest of the
    first `MAX_STILL_FRAMES`."""
    best = QImage()
    best_light = -1.0
    for _ in range(MAX_STILL_FRAMES):
        frame = reader.read()
        if frame.isNull():
            break
        light = _brightness(frame)
        if light > best_light:
            best, best_light = frame, light
        if light >= STILL_MIN_BRIGHTNESS:
            break
        if not reader.supportsAnimation():
            break
    return best


def _brightness(frame: QImage) -> float:
    """Mean lightness of a frame, from an 8×8 thumbnail of it."""
    small = frame.scaled(8, 8, Qt.IgnoreAspectRatio, Qt.FastTransformation)
    if small.isNull():
        return 0.0
    total = sum(small.pixelColor(x, y).valueF()
                for x in range(small.width()) for y in range(small.height()))
    return total / max(1, small.width() * small.height())


# ---- local previews (tables and cards) -----------------------------------------------------

def find_preview(path: str) -> tuple[str, int, int] | None:
    """The preview for a wallpaper folder, or an image file itself, as
    `(file, mtime_ns, size)`; None when there is none. Touches the disk: call
    it on a worker.

    A folder is listed once rather than probed name by name. On Windows the
    listing carries each file's time and size, so finding the preview and
    knowing whether it changed is one read of the folder."""
    if os.path.splitext(path)[1].lower() in IMAGE_SUFFIXES:
        st = os.stat(path)
        return path, st.st_mtime_ns, st.st_size
    best = None
    with os.scandir(path) as entries:
        for entry in entries:
            name = entry.name.lower()
            if name in PREVIEW_NAMES and entry.is_file():
                rank = PREVIEW_NAMES.index(name)
                if best is None or rank < best[0]:
                    best = (rank, entry)
    if best is None:
        return None
    st = best[1].stat()
    return best[1].path, st.st_mtime_ns, st.st_size


def cache_file(root: Path, preview: str, mtime_ns: int, size: int) -> Path:
    """Where the still of this preview, as it is now, is kept. The name holds
    the time and size, so a preview that changed misses its old still."""
    digest = _path_digest(preview)
    return Path(root) / digest[:2] / f"{digest}-{mtime_ns:x}-{size:x}.jpg"


def _path_digest(preview: str) -> str:
    return hashlib.sha1(os.path.normcase(preview).encode("utf-8")).hexdigest()[:20]


def _fit(image: QImage, box: QSize) -> QImage:
    if image.width() <= box.width() and image.height() <= box.height():
        return image
    return image.scaled(box, Qt.KeepAspectRatio, Qt.SmoothTransformation)


def read_local(path: str, root: Path, box: QSize = LOCAL_BOX) -> QImage:
    """The still for a wallpaper folder or preview file, from the cache when
    the preview has not changed, else decoded and cached. A null image when
    there is no preview. Worker threads only."""
    found = find_preview(path)
    if found is None:
        return QImage()
    preview, mtime_ns, size = found
    cached = cache_file(root, preview, mtime_ns, size)
    image = QImage()
    try:
        os.stat(cached)
    except OSError:
        pass
    else:
        image = QImage(str(cached))
    if image.isNull():
        image = _decode(preview)
        if image.isNull():
            return image
        _keep(image, cached)
    return _fit(image, box)


def _decode(preview: str) -> QImage:
    reader = QImageReader(preview)
    reader.setDecideFormatFromContent(True)
    source = reader.size()
    if source.isValid() and (source.width() > LOCAL_BOX.width()
                             or source.height() > LOCAL_BOX.height()):
        # JPEG decodes straight to the smaller size, which is most of the cost
        reader.setScaledSize(source.scaled(LOCAL_BOX, Qt.KeepAspectRatio))
    image = _still_frame(reader)
    if image.isNull():
        return image
    image = _fit(image, LOCAL_BOX)
    if image.hasAlphaChannel():
        # A JPEG has no alpha: a transparent preview is laid on the well a
        # thumb is drawn in, so its edges do not turn black or white.
        flat = QImage(image.size(), QImage.Format_RGB32)
        flat.fill(theme.composite("surface.well", "bg.solid"))
        painter = QPainter(flat)
        painter.drawImage(0, 0, image)
        painter.end()
        image = flat
    return image


def _keep(image: QImage, target: Path) -> None:
    """Write the still, then drop the stills of this preview's earlier versions."""
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix(".part")
        if not image.save(str(partial), "JPG", LOCAL_QUALITY):
            return
        os.replace(partial, target)
        prefix = target.name.split("-", 1)[0] + "-"
        with os.scandir(target.parent) as entries:
            stale = [e.path for e in entries
                     if e.name.startswith(prefix) and e.name != target.name]
        for old in stale:
            os.remove(old)
    except OSError:
        pass            # a still not kept is read again next time; nothing more


class _Local(QRunnable):
    """One local preview, off the GUI thread."""

    def __init__(self, loader: "ThumbLoader", key: str, path: str, box: QSize):
        super().__init__()
        self.loader = loader
        self.key = key
        self.path = path
        self.box = box

    def run(self) -> None:
        if self.loader.stopped:
            return
        try:
            image = read_local(self.path, self.loader.local_root, self.box)
        except Exception:  # noqa: BLE001 — a folder gone or unreadable has no preview
            image = QImage()
        if self.loader.stopped:
            return
        try:
            self.loader.local_done.emit(self.key, image)
        except RuntimeError:
            pass        # the loader went while this was being read


# ---- the loader ---------------------------------------------------------------------------

class ThumbLoader(QObject):
    """Preview images, fetched or read once and kept on disk.

    `request(id, url)` → `done(id, bytes, still)`: a Steam preview.
    `request_local(key, path, box)` → `local_done(key, still)`: the preview in
    a wallpaper folder (or an image file), fitted into `box` device pixels. The
    still is a null QImage when the folder has no preview.
    """

    done = Signal(str, QByteArray, QImage)
    local_done = Signal(str, QImage)

    def __init__(self, parent=None, threads: int = 6, *, local_root: Path | None = None):
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(threads)
        self.local_pool = QThreadPool(self)
        self.local_pool.setMaxThreadCount(LOCAL_THREADS)
        self.local_root = Path(local_root) if local_root is not None else LOCAL_DIR
        self._asked: set[str] = set()
        self._asked_local: set[str] = set()
        # Downloads outlive the widget that wanted them, so they have to be
        # told when nobody is listening any more.
        self.stopped = False

    # -- Steam previews

    def path_for(self, item_id: str) -> Path:
        return THUMB_DIR / f"{item_id}.img"

    def request(self, item_id: str, url: str | None) -> None:
        if not url or item_id in self._asked:
            return
        self._asked.add(item_id)
        self.pool.start(_Fetch(self, item_id, url))

    def forget(self) -> None:
        self._asked.clear()

    def retarget(self) -> None:
        """A different page is on screen: drop what was queued for the last one.

        Nothing used to be dropped. Clicking through ninety authors queued 662
        previews behind six download threads, and the page on screen waited for
        the ones before it. Downloads already running finish and land on disk;
        whatever they were for is simply not in the model any more.
        """
        self.pool.clear()
        self._asked.clear()

    # -- local previews

    def request_local(self, key: str, path: str | os.PathLike | None,
                      box: QSize | None = None) -> bool:
        """Ask for the still of a wallpaper folder's preview. Once per key until
        `retarget_local` or `forget_local`; returns whether it was queued."""
        if not path or self.stopped or key in self._asked_local:
            return False
        self._asked_local.add(key)
        self.local_pool.start(_Local(self, key, os.fspath(path), QSize(box or LOCAL_BOX)))
        return True

    def asked_local(self) -> frozenset[str]:
        """The keys asked for since the last retarget: what is in flight or done."""
        return frozenset(self._asked_local)

    def retarget_local(self) -> None:
        """The rows on screen changed: drop what is queued and not yet started.
        Reads already running finish and land in the cache."""
        self.local_pool.clear()
        self._asked_local.clear()

    def forget_local(self, key: str) -> None:
        self._asked_local.discard(key)

    # -- stopping

    def stop(self) -> None:
        """Abandon everything in flight and wait for the threads to notice."""
        self.stopped = True
        self.pool.clear()
        self.local_pool.clear()
        self.pool.waitForDone(2000)
        self.local_pool.waitForDone(2000)


_shared: ThumbLoader | None = None


def shared() -> ThumbLoader:
    """The loader kit widgets share for local previews, made on first use."""
    global _shared
    if _shared is None:
        from PySide6.QtWidgets import QApplication
        _shared = ThumbLoader(QApplication.instance())
    return _shared
