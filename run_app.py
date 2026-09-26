"""Wallpaper Engine Toolkit — single entry point bundling Copier, Creator, Rotator and Tracker.

Run:
    python run_app.py                  # the toolkit window (or bring it forward)
    python run_app.py --tab Review     # ... on a given tab
    python run_app.py --tracker        # the background playlist tracker, tray only
    python run_app.py --autostart on   # install / remove / report autostart:
                                       #   on | off | status
    python run_app.py --selfcheck      # report what a built exe can actually
                                       #   import, and exit
    python run_app.py --version        # print the version and exit
"""
from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from app.main_window import MainWindow
from app import theme

# How long a Python thread may hold the GIL before it is asked to hand it over.
#
# PySide releases the GIL around every call into Qt and takes it back afterwards,
# so the GUI thread asks for it back hundreds of times per repaint. At the
# default 5 ms, any thread busy in Python — parsing a Steam answer, building an
# item list — makes the GUI thread wait up to 5 ms for each of those, and a
# repaint that took 24 ms took 4.1 s with two such threads beside it (177 times
# slower, measured). At 0.5 ms the same repaint took 94 ms. The cost is more
# switching between busy background threads, which is not what anyone waits on.
SWITCH_INTERVAL = 0.0005


def _autostart(action: str) -> int:
    """Install, remove or report autostart without opening any window.

    A windowed build has no console to print to, so the outcome also goes to
    data/tracker.log — which is where you look when the tracker did not come up.
    """
    from app import autostart
    from app.tracker_tray import log

    if action == "on":
        used = autostart.enable()
        message = f"autostart installed via {used}: {autostart.command()}"
    elif action == "off":
        autostart.disable()
        message = "autostart removed"
    else:
        message = f"autostart: {autostart.method()}"
    log(message)
    print(message)
    return 0


def _selfcheck() -> int:
    """Say whether this build has everything the tabs need, and exit.

    A windowed build has no console, and a module that imports lazily is
    exactly the shape of dependency PyInstaller misses. The failure then looks
    like a tab quietly not working, hours after the build. This makes it a line
    in a file instead.
    """
    from app.settings import app_data_dir

    from app import __version__

    lines = [f"version: {__version__}",
             f"frozen: {bool(getattr(sys, 'frozen', False))}"]
    ok = True
    for module, why in (("PySide6.QtWidgets", "the window"),
                        ("sqlite3", "the authors database and the Steam cache"),
                        ("app.engines.authors_store", "the authors database"),
                        ("app.ui.authors_dialog", "its backups and restoring one"),
                        ("app.engines.review", "the review itself"),
                        ("app.ui.review_tab", "the Review tab"),
                        ("app.engines.steam_ugc", "subscribing from the gallery"),
                        ("app.secrets", "the stored Steam key"),
                        ("PySide6.QtNetwork", "one window, raised from the tray"),
                        ("app.window_instance", "starting the window on its own"),
                        ("app.engines.playlist_refresh",
                         "rebuilding Wallpaper Engine's playlist after a rotation"),
                        ("PySide6.QtSvg", "icons"),
                        ("app.ui.kit", "the redesign's components, and the gallery's previews")):
        try:
            __import__(module)
            lines.append(f"ok      {module}  ({why})")
        except Exception as err:  # noqa: BLE001 — the message is the point
            ok = False
            lines.append(f"MISSING {module}  ({why}): {err}")

    # The stylesheet draws check marks and spin arrows from SVG files, through
    # Qt's svg image plugin. A build without it still starts — it just shows
    # check boxes that never tick, which is exactly what nobody reports.
    try:
        from PySide6.QtGui import QImageReader
        if b"svg" in [bytes(f) for f in QImageReader.supportedImageFormats()]:
            lines.append("ok      svg images  (check marks and arrows)")
        else:
            ok = False
            lines.append("MISSING svg images  (check marks and arrows): no svg image plugin")
    except Exception as err:  # noqa: BLE001 — the message is the point
        ok = False
        lines.append(f"BROKEN  svg images: {type(err).__name__}: {err}")

    # The gallery fetches its previews itself, with urllib on a worker thread,
    # and swallows whatever goes wrong because a missing preview is not worth a
    # traceback. That is fine until every preview fails, which looks exactly
    # like a gallery of empty tiles and says nothing at all. So the fetch is
    # tried once here, where it is allowed to explain itself.
    try:
        import urllib.error
        import urllib.request
        request = urllib.request.Request(
            "https://images.steamusercontent.com/",
            headers={"User-Agent": "Mozilla/5.0"})
        try:
            urllib.request.urlopen(request, timeout=15).close()
        except urllib.error.HTTPError as err:
            # An answer of any kind means the connection itself is sound.
            lines.append(f"ok      preview downloads (HTTP {err.code} from the host)")
        else:
            lines.append("ok      preview downloads")
    except Exception as err:  # noqa: BLE001 — the message is the point
        ok = False
        lines.append(f"BROKEN  preview downloads: {type(err).__name__}: {err}")

    report = "\n".join(lines)
    (app_data_dir() / "selfcheck.txt").write_text(report + "\n", encoding="utf-8")
    print(report)
    return 0 if ok else 1


def _migrate_autostart() -> None:
    """Move an autostart entry left over from this app's old name. Once.

    The app used to be called Wallpaper Suite, and registered its logon task
    under that name. After the rename that entry still starts the tracker every
    morning, but nothing in the app recognises it any more — so the Tracker tab
    would show autostart as off while it demonstrably runs, and switching it on
    would install a second one.

    Checking costs a `schtasks` call, which is not worth paying at every start
    forever, so the result is remembered. Any failure here is swallowed: an
    autostart entry is not worth refusing to open the window over.
    """
    from app.settings import Settings

    settings = Settings.load()
    if settings.get("app", "autostart_migrated", False):
        return
    try:
        from app import autostart
        said = autostart.migrate_legacy()
    except Exception:  # noqa: BLE001 — never block start-up
        return
    if said:
        from app.tracker_tray import log
        log(said)
    settings.set("app", "autostart_migrated", True)
    settings.save()


def main():
    # Before anything with side effects: asking the version changes nothing.
    # A windowed build has no console, so read it redirected:
    # `WallpaperEngineToolkit.exe --version | more`.
    if "--version" in sys.argv[1:]:
        from app import __version__
        print(__version__)
        sys.exit(0)

    sys.setswitchinterval(SWITCH_INTERVAL)
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    _migrate_autostart()

    args = sys.argv[1:]
    if "--autostart" in args:
        position = args.index("--autostart")
        action = args[position + 1] if position + 1 < len(args) else "status"
        sys.exit(_autostart(action))

    if "--selfcheck" in args:
        sys.exit(_selfcheck())

    if "--tracker" in args:
        from app.tracker_tray import run_tray
        sys.exit(run_tray())

    app = QApplication(sys.argv)
    app.setApplicationName("Wallpaper Engine Toolkit")
    from app import __version__
    app.setApplicationVersion(__version__)

    tab = ""
    if "--tab" in args:
        position = args.index("--tab")
        tab = args[position + 1] if position + 1 < len(args) else ""

    # One window. A second launch — the tray's, or a double-click on the exe —
    # hands its request to the first and leaves.
    from app import window_instance
    instance = window_instance.WindowInstance.claim()
    if instance is None:
        window_instance.ask_to_show(tab)
        sys.exit(0)
    window_instance.make_normal_priority()

    from app.hang_watch import HangWatch
    from app.settings import app_data_dir
    watch = HangWatch(app_data_dir() / "window-hangs.log", "the toolkit window").start()

    theme.apply(app)
    win = MainWindow()
    if tab:
        win.show_tab(tab)
    instance.show_requested.connect(win.bring_forward)
    win.show()
    code = app.exec()
    watch.stop()
    sys.exit(code)


if __name__ == "__main__":
    main()
