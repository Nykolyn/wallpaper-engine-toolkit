"""A picture of the real window, in a made-up state — for comparing with the design.

A development tool, not part of the app: it is not bundled and nothing in
`app/` imports it.

    .venv\\Scripts\\python.exe tools\\ui_snapshot.py --page rotator --state running --out rotator.png
    .venv\\Scripts\\python.exe tools\\ui_snapshot.py --page settings --size 1040x720 --scale 1.5 --out s.png
    .venv\\Scripts\\python.exe tools\\ui_snapshot.py --list

It builds `MainWindow` offscreen with **fabricated fixtures only**: a data
folder of its own under %TEMP% (nothing is read from or written to this
machine's), no TrackerFeed, services that are never started and a Snapshot
that reads nothing (a fixture's finished job would otherwise have it count
the Rotator's folders, and with no config that is this machine's own
myprojects), made-up folders on a drive `X:` that it reports as present, and
the frame's state from `tests/fixtures/ui/shell.json`. Pages that have a page of their own
(Overview, Rotator, Tracker, Review, Settings) are the real ones, and `load_fixture(state)` puts
them in the state asked for; the old tabs are not built at all — a stand-in says
where each one goes — because building them reads the library, Steam and
Wallpaper Engine.

- `--page`: overview, rotator, tracker, review, creator, copier, settings.
- `--state`: a state of the frame (`--list` shows them: idle, running, clean,
  problems, scanning, building, copying, failed, empty, we-off), or one of
  the page's own (the Tracker's tracking, paused, disconnected, finished, …,
  the Rotator's running, done-clean, confirm, broken, …), which names the
  frame's state it goes with (`Page.frame_fixture`). A page with a fixture of
  that name shows it (Overview's are in `tests/fixtures/ui/overview.json`, the
  Tracker's in `tracker.json`, the Rotator's in `rotator.json`, Review's in
  `review.json`); otherwise its first. A page state that opens a dialog (the
  Rotator's confirm and broken, Review's settings and authors) is drawn with
  the dialog over the window.
- `--size WxH` (default 1280x860) and `--scale 1|1.5` (the user's screen is
  at 150 %; the picture is then 1.5 × the size in pixels).
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _args(argv):
    parser = argparse.ArgumentParser(description="A picture of the window in a made-up state.")
    parser.add_argument("--page", default="overview")
    parser.add_argument("--state", default="idle")
    parser.add_argument("--size", default="1280x860")
    parser.add_argument("--scale", default="1")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--list", action="store_true", help="the frame's states, and stop")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _args(sys.argv[1:] if argv is None else argv)
    # Before Qt and before any app module: offscreen, with Windows' fonts (offscreen
    # Qt has none of its own), at the scale asked for, and a data folder that is
    # nobody's. Relative paths are resolved from an empty folder too.
    scratch = Path(tempfile.mkdtemp(prefix="toolkit_ui_snapshot_"))
    os.environ["WALLPAPER_TOOLKIT_DATA"] = str(scratch / "data")
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ.setdefault("QT_QPA_FONTDIR",
                          str(Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"))
    os.environ["QT_SCALE_FACTOR"] = str(float(args.scale))
    out = args.out.resolve() if args.out else None
    os.chdir(scratch)
    sys.path.insert(0, str(ROOT))

    from app.main_window import load_shell_fixture, page_for
    import json
    states = [k for k in json.loads((ROOT / "tests" / "fixtures" / "ui" / "shell.json")
                                    .read_text(encoding="utf-8")) if k != "//"]
    if args.list:
        from app.pages.overview import OverviewPage
        from app.pages.review import ReviewPage
        from app.pages.rotator import RotatorPage
        from app.pages.settings import SettingsPage
        from app.pages.tracker import TrackerPage
        print("states:", ", ".join(states))
        for page in (OverviewPage, RotatorPage, TrackerPage, ReviewPage, SettingsPage):
            print(f"{page.key}:", ", ".join(page.FIXTURES))
        return 0
    if out is None:
        print("--out is needed")
        return 2
    try:
        width, height = (int(n) for n in args.size.lower().split("x"))
    except ValueError:
        print(f"--size is WxH, not {args.size!r}")
        return 2

    window = build_window(args.page)
    fixture = frame_fixture(window.pages[page_for(args.page)], args.state, states,
                            load_shell_fixture)
    if fixture is None:
        print(f"no state {args.state!r}: not the frame's ({', '.join(states)}), nor the page's")
        return 2
    from PySide6.QtWidgets import QApplication
    window.resize(width, height)
    window.show()
    settle(420)
    window.load_fixture(args.state, fixture)
    settle(420)                     # path checks, the live dot's first frame
    window.fade.stop()
    QApplication.processEvents()
    out.parent.mkdir(parents=True, exist_ok=True)
    picture = window.grab()
    dialog = getattr(window.pages[page_for(args.page)], "fixture_dialog", None)
    if dialog is not None and dialog.isVisible():
        # A dialog is a window of its own over the window, scrim and all: drawn
        # over the window's picture where it stands.
        from PySide6.QtGui import QPainter
        painter = QPainter(picture)
        painter.drawPixmap(dialog.geometry().topLeft() - window.geometry().topLeft(),
                           dialog.grab())
        painter.end()
    picture.save(str(out))
    print(f"saved {out}  ({args.page}, {args.state}, {width}x{height} @ {args.scale})")
    return 0


def frame_fixture(page, state: str, states: list[str], load) -> dict | None:
    """The frame's fixture for a state: the frame's own of that name, or the
    one a page's state names, with what the page changes in it."""
    spec = page.frame_fixture(state) or {}
    frame = spec.get("frame", state)
    if frame not in states:
        return None
    fixture = load(frame)
    if "nav" in spec:
        fixture["nav"] = {**fixture.get("nav", {}), **spec["nav"]}
    if "next" in spec:
        fixture["next"] = spec["next"]
    return fixture


# ---- the window, made up ---------------------------------------------------------------

FAKE_DRIVE = "X:\\"


def build_window(page_key: str):
    from PySide6.QtCore import QObject, Qt, Signal
    from PySide6.QtWidgets import QApplication, QVBoxLayout

    app = QApplication.instance() or QApplication(sys.argv)
    from app import services, theme
    from app.engines.rotator.config import Config
    from app.services import snapshot as snapshot_module
    from app.main_window import MainWindow, page_for
    from app.pages.base import Page
    from app.pages.overview import OverviewPage
    from app.pages.review import ReviewPage
    from app.pages.rotator import RotatorPage
    from app.pages.settings import SettingsPage
    from app.pages.tracker import TrackerPage
    from app.settings import Settings
    from app.ui.kit import label, paths

    theme.apply(app)
    # The Snapshot's worker reads nothing here: only a fixture puts readings.
    snapshot_module.compute = lambda keys, data_dir: {}
    # The fixtures' folders are on a drive this machine does not have; the
    # fields are told they are there, so they draw as a set-up machine's would.
    paths.is_folder = lambda path: str(path).startswith(FAKE_DRIVE)
    paths.is_file = lambda path: str(path).startswith(FAKE_DRIVE)

    class StandInFeed(QObject):
        """No tracker: nothing reads Wallpaper Engine."""
        updated = Signal()
        config_changed = Signal()
        results: list = []

        class tracker:                  # noqa: N801 — stands in for an attribute
            config_path = ""

        def refresh(self):
            pass

        def use_config(self, path):
            pass

        def set_heartbeat(self, seconds):
            pass

    class StandIn(Page):
        """Where an old tab goes; the tab itself is not built here."""

        def __init__(self, key: str, title: str, icon: str):
            self.key, self.title, self.icon = key, title, icon
            super().__init__()
            column = QVBoxLayout(self)
            note = label(f"The {title} tab is not drawn in snapshots: it reads this "
                         f"machine's library. Its page comes in its own step.", "type.body", "lo")
            note.setAlignment(Qt.AlignCenter)
            note.setWordWrap(True)
            column.addWidget(note)

    settings = Settings({})
    feed = StandInFeed()
    svc = services.Services(data_dir=Path(os.environ["WALLPAPER_TOOLKIT_DATA"]),
                            settings=settings)
    config = Config(source="", destination="", duplicates="", count=1000)
    pages = [OverviewPage(svc, feed, settings=settings),
             RotatorPage(config, svc),
             TrackerPage(feed, svc, settings=settings),
             ReviewPage(settings, svc),
             StandIn("creator", "Creator", "creator"),
             StandIn("copier", "Copier", "copier"),
             SettingsPage(settings, config, feed=feed, services=svc)]
    key = page_for(page_key)
    if key is None:
        raise SystemExit(f"no page {page_key!r}")
    return MainWindow(settings=settings, feed=feed, services_=svc, pages=pages,
                      start=False, initial=key)


def settle(ms: int) -> None:
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


if __name__ == "__main__":
    sys.exit(main())
