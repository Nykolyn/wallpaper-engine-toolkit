"""Wallpaper Engine Toolkit — single entry point bundling Copier, Creator, Rotator and Tracker.

Run:
    python run_app.py                  # the toolkit window
    python run_app.py --tracker        # the background playlist tracker, tray only
    python run_app.py --autostart on   # install / remove / report autostart:
                                       #   on | off | status
    python run_app.py --selfcheck      # report what a built exe can actually
                                       #   import, and exit
"""
from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from app.main_window import MainWindow
from app import theme


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

    A windowed build has no console, and the two libraries the Review tab
    reaches the authors database through — pymongo and dnspython — are imported
    lazily, which is exactly the shape of dependency PyInstaller misses. The
    failure then looks like the tab quietly not working, hours after the build.
    This makes it a line in a file instead.
    """
    from app.settings import app_data_dir

    lines = [f"frozen: {bool(getattr(sys, 'frozen', False))}"]
    ok = True
    for module, why in (("PySide6.QtWidgets", "the window"),
                        ("pymongo", "the authors database"),
                        ("bson", "database ids"),
                        ("dns.resolver", "mongodb+srv lookups, the fallback path"),
                        ("app.engines.mongo_srv", "mongodb+srv through Windows"),
                        ("app.engines.review", "the review itself"),
                        ("app.ui.review_tab", "the Review tab"),
                        ("app.engines.steam_ugc", "subscribing from the gallery"),
                        ("app.secrets", "the stored key and connection string")):
        try:
            __import__(module)
            lines.append(f"ok      {module}  ({why})")
        except Exception as err:  # noqa: BLE001 — the message is the point
            ok = False
            lines.append(f"MISSING {module}  ({why}): {err}")

    # Importing the resolver is not the same as being able to use it. On a
    # machine whose nameservers answer Windows but not a library talking to
    # them directly, this line is the difference between the Review tab
    # connecting in a second and timing out for twenty.
    try:
        from app.engines import mongo_srv
        working = mongo_srv.usable()
        lines.append(("ok      " if working else "MISSING ")
                     + "the Windows resolver (dnsapi.dll)")
        ok = ok and working
    except Exception as err:  # noqa: BLE001 — the message is the point
        ok = False
        lines.append(f"MISSING the Windows resolver: {err}")

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
    theme.apply(app)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
