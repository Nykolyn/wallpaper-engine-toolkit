"""Wallpaper Engine Toolkit — single entry point bundling Copier, Creator, Rotator and Tracker.

Run:
    python run_app.py                  # the toolkit window (or bring it forward)
    python run_app.py --tab Review     # ... on a given page: overview, rotator,
                                       #   tracker, review, creator, copier or
                                       #   settings, in any case
    python run_app.py --command rotate:confirm
                                       # ... and carry out a window command once
                                       #   it is up: the tray's "Rotate now…"
                                       #   asks the Rotator's start question
    python run_app.py --tracker        # the background playlist tracker, tray only
    python run_app.py --autostart on   # install / remove / report autostart:
                                       #   on | off | status
    python run_app.py --selfcheck      # report what a built exe can actually
                                       #   import, and exit
    python run_app.py --version        # print the version and exit

What the installer runs (installer/WallpaperEngineToolkit.iss); each takes
``--report <file>`` to write what happened there as well, as a windowed build
has no console:

    python run_app.py --quit           # ask the running window and tray tracker
                                       #   to quit, and wait for them
    python run_app.py --adopt-data <folder>
                                       # copy an earlier copy's data in, when
                                       #   there is no data folder yet
    python run_app.py --backup-data    # copy the data aside before an update
    python run_app.py --autostart release
                                       # remove autostart if it starts this copy
"""
from __future__ import annotations

import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication

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
    elif action == "release":
        message = autostart.release()
    else:
        message = f"autostart: {autostart.method()}"
    log(message)
    _report(message)
    return 0


def _argument(flag: str) -> str:
    """The word after `flag` on the command line, or ""."""
    args = sys.argv[1:]
    if flag in args:
        position = args.index(flag)
        if position + 1 < len(args):
            return args[position + 1]
    return ""


def _report(said: str) -> None:
    """Print what happened, and write it where `--report <file>` says: the
    installer reads it from there, since a windowed build has no console."""
    print(said)
    path = _argument("--report")
    if path:
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(said + "\n")
        except OSError:
            pass


def _quit_running() -> int:
    """Ask the running window and tray tracker to quit, and wait until they
    have (see app/quit_request.py). 0 when nothing runs any more, 1 when
    something would not quit. Reads and writes no data."""
    from PySide6.QtCore import QCoreApplication
    from app import quit_request

    # The sockets want an event dispatcher, which wants an application.
    app = QCoreApplication(sys.argv)  # noqa: F841
    gone, said = quit_request.ask_all()
    _report(said)
    return 0 if gone else 1


def _adopt_data(folder: str) -> int:
    """Copy an earlier copy's data in, if there is no data folder yet (see
    data_location.adopt). 0 when it was copied, 1 when it was not; either way
    the earlier copy is left as it was. Nothing else here may run first: the
    first look at the data folder makes one, and then there is no room for it."""
    from pathlib import Path
    from app import data_location

    if not folder:
        _report("--adopt-data needs the folder of the earlier copy")
        return 1
    copied, said = data_location.adopt(Path(folder))
    if copied:
        from app.tracker_tray import log
        log(f"data: {said}")
    _report(said)
    return 0 if copied else 1


def _backup_data() -> int:
    """Copy the data aside before an update (see app/update_backup.py).
    0 when it was copied and checked, or there was no data to copy."""
    from app import __version__, data_location, update_backup

    if data_location.sandbox_for_data():
        _report("no backup made: this process runs inside another app's sandbox")
        return 1
    folder = data_location.data_dir()
    try:
        _, said = update_backup.take(folder, f"before {__version__}")
    except OSError as err:
        _report(f"the data could not be copied aside: {err}")
        return 1
    from app.tracker_tray import log
    log(said)
    _report(said)
    return 0


def _selfcheck() -> int:
    """Say whether this build has everything the pages need, and exit.

    The checks are in app/selfcheck.py, which the Settings page runs too.
    """
    from app import selfcheck

    ok, report = selfcheck.run()
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

    # The installer's: each before anything else looks at the data folder or
    # the Start menu, which is what they are run to take care of.
    if "--quit" in sys.argv[1:]:
        sys.exit(_quit_running())
    if "--adopt-data" in sys.argv[1:]:
        sys.exit(_adopt_data(_argument("--adopt-data")))
    if "--backup-data" in sys.argv[1:]:
        sys.exit(_backup_data())

    sys.setswitchinterval(SWITCH_INTERVAL)
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    # Where Steam put Wallpaper Engine, worked out on a thread from the start:
    # the window and the tray both need it, and Steam may be on a disk that
    # takes seconds to wake. Until it is known it is not guessed at (see
    # app/engines/steam_paths.py).
    from app.engines import steam_paths
    steam_paths.find_in_background()

    # Before any window: Windows takes the sender of the tray's balloons from
    # this process's AppUserModelID and the Start-menu shortcut that carries it
    # (see app/app_identity.py). Never worth refusing to start over.
    if not {"--autostart", "--selfcheck"} & set(sys.argv[1:]):
        try:
            from app import app_identity
            said = app_identity.claim_for_this_process()
        except Exception as err:  # noqa: BLE001
            said = f"app identity: {type(err).__name__}: {err}"
        if getattr(sys, "frozen", False):
            from app.tracker_tray import log
            log(said)

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
    command = ""
    if "--command" in args:
        position = args.index("--command")
        command = args[position + 1] if position + 1 < len(args) else ""

    # One window. A second launch — the tray's, or a double-click on the exe —
    # hands its request to the first and leaves.
    from app import window_instance
    instance = window_instance.WindowInstance.claim()
    asked = window_instance.parse_command(command) if command else None
    if instance is None:
        window_instance.ask_to_show(tab, command=command if asked else None)
        sys.exit(0)
    window_instance.make_normal_priority()

    from app.hang_watch import HangWatch
    from app.settings import app_data_dir
    watch = HangWatch(app_data_dir() / "window-hangs.log", "the toolkit window").start()

    # Only the window needs the window: imported here, the tray tracker — which
    # runs all day at below-normal priority — does not load the kit and every
    # page to draw a ring.
    from app.main_window import MainWindow

    theme.apply(app)
    # A name that is no page (a typo, an old script) opens on Overview. The
    # window comes up at once, saying it is opening, and makes its pages once
    # that is on screen (MainWindow's `defer`).
    win = MainWindow(initial=tab, defer=True)
    instance.command_received.connect(win.handle_command)
    win.show()
    if asked is not None:
        # Once the window is up, as if it had been asked by the tray.
        QTimer.singleShot(0, lambda: win.handle_command(*asked))
    code = app.exec()
    watch.stop()
    sys.exit(code)


if __name__ == "__main__":
    main()
