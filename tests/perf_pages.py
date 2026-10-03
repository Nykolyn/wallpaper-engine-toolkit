"""How the long lists hold up at the sizes they reach for real.

Not a test (the runner takes `test_*.py` only): a measurement to run by hand
before and after a change to a page's list, with its numbers in the pull
request. tests/perf_gallery.py measures the Review gallery the same way.

    .venv\\Scripts\\python.exe tests\\perf_pages.py [--busy 2] [--only reserve,tracker,authors,log]

The window is the one tools/ui_snapshot.py builds (made-up data on a drive X:,
nothing read from this machine), at 1 280 × 860, offscreen. `--busy` Python
threads spin while the lists scroll and the log streams, with the switch
interval the app sets (0.5 ms): PySide gives the GIL up on every Qt call, so
a paint's cost under contention is about how many calls it makes.

- **reserve**: the Rotator's reserve table with the fixture's 33 421 folders:
  the model rebuilt, a filter typed and cleared, a sort by size, and 150
  scroll steps of three rows, each repainted at once (`viewport().repaint()`).
- **tracker**: the Tracker's playlist with 1 400 rows (200 shown, 1 200 in
  the queue): the rows set, then 150 scroll steps.
- **authors**: Review's author list with 450 authors: the rows set, then
  150 scroll steps.
- **log**: a LogPanel at its cap (5 000 lines) while a job streams 200 lines a
  second for 10 s: what each line costs, the console's repaints, and a 50 ms
  clock beside it (its ticks and worst gap say whether the window kept up).
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--busy", type=int, default=2)
    parser.add_argument("--switch", type=float, default=0.0005)
    parser.add_argument("--only", default="reserve,tracker,authors,log")
    parser.add_argument("--stream", type=float, default=10.0, help="seconds the log streams")
    parser.add_argument("--root", type=Path, default=ROOT,
                        help="a checkout to measure instead of this one (before a change)")
    return parser.parse_args()


class Busy:
    """`n` threads spinning in Python while the block runs."""

    def __init__(self, n: int):
        self.n, self.stop = n, threading.Event()

    def __enter__(self):
        def spin():
            total = 0
            while not self.stop.is_set():
                total += sum(range(2000))
        self.threads = [threading.Thread(target=spin, daemon=True) for _ in range(self.n)]
        for thread in self.threads:
            thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        for thread in self.threads:
            thread.join()


def ms(seconds: float) -> str:
    return f"{seconds * 1000:.1f} ms"


def spread(samples: list[float]) -> str:
    values = sorted(s * 1000 for s in samples)
    if not values:
        return "no samples"
    p95 = values[min(len(values) - 1, int(0.95 * len(values)))]
    return f"p50 {statistics.median(values):.2f} ms, p95 {p95:.2f} ms, worst {values[-1]:.2f} ms ({len(values)})"


def timed(action) -> float:
    started = time.perf_counter()
    action()
    return time.perf_counter() - started


def scroll(view, steps: int = 150, rows: int = 3) -> list[float]:
    """Scroll down `rows` rows at a time (round to the top at the end),
    repainting each step at once."""
    bar = view.verticalScrollBar()
    row = (view.verticalHeader().defaultSectionSize() if hasattr(view, "verticalHeader")
           else view.sizeHintForRow(0))
    step = max(1, row) * rows
    samples = []
    for i in range(steps):
        bar.setValue(((i + 1) * step) % (bar.maximum() + 1))
        samples.append(timed(view.viewport().repaint))
    return samples


def main() -> int:
    args = _args()
    scratch = Path(tempfile.mkdtemp(prefix="toolkit_perf_pages_"))
    os.environ["WALLPAPER_TOOLKIT_DATA"] = str(scratch / "data")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QT_QPA_FONTDIR", str(Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"))
    sys.path.insert(0, str(args.root))
    sys.path.insert(0, str(args.root / "tools"))
    sys.stdout.reconfigure(errors="replace")
    sys.setswitchinterval(args.switch)

    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    import ui_snapshot
    from app.main_window import load_shell_fixture

    states = [k for k in json.loads((args.root / "tests/fixtures/ui/shell.json").read_text(encoding="utf-8"))
              if k != "//"]
    window = ui_snapshot.build_window("overview")
    window.resize(1280, 860)
    window.show()
    ui_snapshot.settle(200)
    wanted = args.only.split(",")
    print(f"{args.busy} busy thread(s) while scrolling and streaming, switch interval "
          f"{args.switch * 1000:g} ms, window 1280 x 860, offscreen")

    def show(key: str, state: str):
        page = window.pages[key]
        window.show_page(key, animate=False)
        window.load_fixture(state, ui_snapshot.frame_fixture(page, state, states, load_shell_fixture))
        ui_snapshot.settle(300)
        return page

    if "reserve" in wanted:
        page = show("rotator", "idle")
        model, table = page.models["reserve"], page.tables["reserve"]
        names = model.names()
        build = timed(lambda: model.set_names(names))
        page.filter.setText("mist 1")
        filtering = timed(page._apply_filter)
        shown = model.rowCount()
        page.filter.setText("")
        clearing = timed(page._apply_filter)
        size_column = [c.title for c in model.columns].index("Size")
        sorting = timed(lambda: table.sort_by(size_column, Qt.DescendingOrder))
        app.processEvents()
        with Busy(args.busy):
            steps = scroll(table)
        print(f"\nreserve   {len(names)} folders: model built {ms(build)}; filter 'mist 1' "
              f"{ms(filtering)} ({shown} rows), cleared {ms(clearing)}; sort by size {ms(sorting)}")
        print(f"          scroll steps: {spread(steps)}")

    if "tracker" in wanted:
        from app.pages.tracker import QUEUE, SHOWN, PlaylistRow
        page = show("tracker", "tracking")
        now = datetime.now()
        rows = []
        for n in range(1, 1401):
            shown = n <= 200
            rows.append(PlaylistRow(
                item=fr"X:\Library\myprojects\made_up_{n:04d}\scene.json", group=SHOWN if shown else QUEUE,
                number=n, queue=None if shown else n - 200,
                when=now - timedelta(minutes=42 * (201 - n)) if shown else None,
                shown_for=42 * 60 if shown else None, on_screen=n == 200,
                title=f"Made-up wallpaper {n}", author=f"Author {n % 37}", kind="scene",
                folder=fr"X:\Library\myprojects\made_up_{n:04d}"))
        build = timed(lambda: page.model.set_playlist(rows, False, now))
        app.processEvents()
        with Busy(args.busy):
            steps = scroll(page.table)
        print(f"\ntracker   1 400 rows: set {ms(build)}")
        print(f"          scroll steps: {spread(steps)}")

    if "authors" in wanted:
        from app.engines.review_flow import SessionAuthor
        from app.pages.review import AuthorList, author_row
        authors = AuthorList()
        authors.resize(262, 640)
        authors.show()
        people = [SessionAuthor(id=str(76561198000000000 + i), name=f"Made-up author {i}",
                                new=1 + i % 23, done=i % 5 == 0) for i in range(450)]
        build = timed(lambda: authors.model_.set_rows([(a.id, author_row(a)) for a in people]))
        app.processEvents()
        with Busy(args.busy):
            steps = scroll(authors, rows=3)
        print(f"\nauthors   450: rows made and set {ms(build)}")
        print(f"          scroll steps: {spread(steps)}")
        authors.close()

    if "log" in wanted:
        from app.ui.kit import LogPanel
        panel = LogPanel("Log", file="rotator/run-made-up.log")
        panel.resize(640, 420)
        panel.show()
        lines = [(None, "moved" if i % 9 else "fail", f"made_up_{i:05d} → myprojects")
                 for i in range(5000)]
        fill = timed(lambda: panel.extend(lines))
        app.processEvents()
        appends: list[float] = []
        paints: list[float] = []
        ticks: list[float] = []
        count = [0]

        def stream():
            count[0] += 1
            appends.append(timed(lambda: panel.append(None, "moved", f"streamed_{count[0]:05d} → myprojects")))

        view = panel.view
        real_paint = view.paintEvent

        def paint(event):
            started = time.perf_counter()
            real_paint(event)
            paints.append(time.perf_counter() - started)

        view.paintEvent = paint
        feeder, clock = QTimer(), QTimer()
        feeder.setInterval(5)
        feeder.timeout.connect(stream)
        clock.setInterval(50)
        clock.timeout.connect(lambda: ticks.append(time.perf_counter()))
        with Busy(args.busy):
            started = time.perf_counter()
            feeder.start()
            clock.start()
            end = started + args.stream
            while time.perf_counter() < end:
                app.processEvents()
                time.sleep(0.001)
            feeder.stop()
            clock.stop()
        gaps = [b - a for a, b in zip([started] + ticks, ticks)]
        expected = int(args.stream * 1000 / 50)
        print(f"\nlog       filled to its cap ({len(panel.model())} lines) in {ms(fill)}; "
              f"{count[0]} lines streamed in {args.stream:g} s")
        print(f"          each line: {spread(appends)}")
        print(f"          console repaints: {spread(paints)}")
        print(f"          50 ms clock: {len(ticks)} of {expected} ticks, worst gap {ms(max(gaps) if gaps else 0)}")
        panel.close()

    window.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
