"""How the gallery holds up while it animates and other threads are busy.

Not a test (the runner takes `test_*.py` only): a measurement to run by hand
before and after a change to `app/ui/gallery.py`, with its numbers in the
pull request.

    .venv\\Scripts\\python.exe tests\\perf_gallery.py [--busy 2] [--seconds 20] [--size 700x640]

A page of thirty animated previews is made up here (Pillow draws them: 400 ×
400, 24 frames at 25 a second, moving shapes over noise, so decoding costs
what a real one does), handed to a `GalleryView` as if downloaded, and left to
play while `--busy` threads spin in Python, with the switch interval the app
sets (0.5 ms). Offscreen, so nothing shows; the platform paints into memory
the way a window does.

What it reports:

- **the clock**: a 50 ms timer beside the gallery, as the old measurement had
  it (docs/gallery.md): how many of its ticks arrived, and the worst delay;
- **frame time**: how long each repaint of the gallery's viewport took
  (p50 / p95 / worst) and how many there were;
- how many previews played, and for how long the animation rested because
  the window was behind.

It drives the view through what every version of it has had — `show_items`,
`_image_arrived`, `_sync_players`, `_players`, `resting` — so the same file
measures the code before a change (point `--root` at a checkout of it).
"""
from __future__ import annotations

import argparse
import io
import os
import random
import statistics
import sys
import threading
import time
from pathlib import Path


def _args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--busy", type=int, default=2)
    parser.add_argument("--seconds", type=float, default=20.0)
    parser.add_argument("--size", default="700x640")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--switch", type=float, default=0.0005)
    return parser.parse_args()


def make_gif(seed: int, side: int = 400, frames: int = 24) -> bytes:
    from PIL import Image, ImageDraw
    rng = random.Random(seed)
    base = Image.effect_noise((side, side), 40 + seed % 30).convert("RGB")
    tint = Image.new("RGB", (side, side), (rng.randrange(40, 200), rng.randrange(40, 200),
                                           rng.randrange(40, 200)))
    ground = Image.blend(base, tint, 0.6)
    shots = []
    for f in range(frames):
        frame = ground.copy()
        draw = ImageDraw.Draw(frame)
        for k in range(6):
            x = (rng.randrange(side) + f * (5 + k * 3)) % side
            y = (rng.randrange(side) + f * (3 + k)) % side
            r = 20 + k * 9
            draw.ellipse((x - r, y - r, x + r, y + r),
                         fill=(rng.randrange(256), rng.randrange(256), rng.randrange(256)))
        shots.append(frame.convert("P", palette=Image.ADAPTIVE, colors=128))
    out = io.BytesIO()
    shots[0].save(out, "GIF", save_all=True, append_images=shots[1:], duration=40, loop=0)
    return out.getvalue()


def main() -> int:
    args = _args()
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
    sys.path.insert(0, str(args.root))
    import tempfile
    os.environ.setdefault("WALLPAPER_TOOLKIT_DATA", tempfile.mkdtemp(prefix="gallery-bench-"))
    sys.setswitchinterval(args.switch)

    from PySide6.QtCore import QByteArray, QTimer
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

    app = QApplication.instance() or QApplication([])
    from app import theme
    theme.apply(app)
    import app.ui.gallery as gal
    from app.engines.review import Wallpaper
    from app.engines.steam_api import ItemDetails

    paints: list[float] = []

    class Timed(gal.GalleryView):
        def paintEvent(self, event):     # noqa: N802 - Qt's name
            started = time.perf_counter()
            super().paintEvent(event)
            paints.append(time.perf_counter() - started)

    width, height = (int(v) for v in args.size.split("x"))
    window = QWidget()
    window.resize(width, height)
    column = QVBoxLayout(window)
    column.setContentsMargins(0, 0, 0, 0)
    view = Timed()
    column.addWidget(view)
    window.show()

    wallpapers = []
    for i in range(30):
        item = ItemDetails(id=str(9_000_000_100 + i), ok=True, creator="76561198000000001",
                           title=f"Made-up preview {i + 1}", created=1750000000 + i,
                           updated=1750000000 + i, preview="", kind="Scene",
                           file_size=(30 + i * 7) * 1024 ** 2)
        wallpapers.append(Wallpaper(item=item, owned_checked=True))
    view.show_items(wallpapers)
    app.processEvents()
    gifs = [make_gif(i) for i in range(30)]
    for wallpaper, data in zip(wallpapers, gifs):
        frame = QImage.fromData(data)
        view._image_arrived(wallpaper.id, QByteArray(data), frame)
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    view._sync_players()

    stop = threading.Event()

    def spin():
        total = 0
        while not stop.is_set():
            total += sum(range(2000))

    threads = [threading.Thread(target=spin, daemon=True) for _ in range(args.busy)]
    for thread in threads:
        thread.start()

    ticks: list[float] = []
    probe = QTimer()
    probe.setInterval(50)
    probe.timeout.connect(lambda: ticks.append(time.perf_counter()))
    resting = [0.0]
    last = [time.perf_counter()]

    def watch():
        now = time.perf_counter()
        if getattr(view, "resting", False):
            resting[0] += now - last[0]
        last[0] = now

    probe.timeout.connect(watch)
    paints.clear()
    started = time.perf_counter()
    probe.start()
    end = started + args.seconds
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.001)
    probe.stop()
    stop.set()

    expected = int(args.seconds * 1000 / 50)
    gaps = [b - a for a, b in zip([started] + ticks, ticks)]
    worst = max(gaps) if gaps else args.seconds
    ms = sorted(p * 1000 for p in paints)

    def pct(q):
        return ms[min(len(ms) - 1, int(q * len(ms)))] if ms else 0.0

    print(f"root      {args.root}")
    print(f"viewport  {view.viewport().width()} x {view.viewport().height()}, "
          f"{args.busy} busy thread(s), switch interval {args.switch * 1000:g} ms")
    print(f"players   {len(view._players)}")
    print(f"clock     {len(ticks)} of {expected} ticks, worst gap {worst * 1000:.0f} ms")
    print(f"paints    {len(ms)} in {args.seconds:g} s; p50 {statistics.median(ms) if ms else 0:.2f} ms, "
          f"p95 {pct(0.95):.2f} ms, worst {ms[-1] if ms else 0:.2f} ms")
    print(f"resting   {resting[0]:.1f} s of {args.seconds:g}")
    view._stop_all()
    view.close_loader()
    return 0


if __name__ == "__main__":
    sys.exit(main())
