"""The selfcheck: what this build can actually do, written down.

A windowed build has no console, and a module that imports lazily is exactly
the shape of dependency PyInstaller misses. The failure then looks like a
page quietly not working, hours after the build. This makes it a line in a
file instead: `data/selfcheck.txt`, written by `--selfcheck` and by the
Settings page's "Run selfcheck".
"""
from __future__ import annotations

import sys
from pathlib import Path

REPORT_NAME = "selfcheck.txt"


def run() -> tuple[bool, str]:
    """Check everything, write the report, and return (all well, the report).

    Touches the network (one preview download) and imports modules: run it
    off the GUI thread.
    """
    from . import __version__
    from .data_location import resolve

    lines = [f"version: {__version__}",
             f"frozen: {bool(getattr(sys, 'frozen', False))}",
             # Where the data is, and whether it was just moved there.
             f"data: {resolve().report}"]
    ok = True
    for module, why in (("PySide6.QtWidgets", "the window"),
                        ("sqlite3", "the authors database and the Steam cache"),
                        ("app.engines.authors_store", "the authors database"),
                        ("app.engines.review", "the review itself"),
                        ("app.engines.review_flow",
                         "a scan as one flow, carrying on, review_last.json"),
                        ("app.engines.steam_ugc", "subscribing from the gallery"),
                        ("app.secrets", "the stored Steam key"),
                        ("PySide6.QtNetwork", "one window, raised from the tray"),
                        ("app.window_instance", "starting the window on its own"),
                        ("app.engines.playlist_refresh",
                         "rebuilding Wallpaper Engine's playlist after a rotation"),
                        ("app.engines.rotator.runner",
                         "a rotation's steps, stop, retry and run_meta.json"),
                        ("app.engines.library_index",
                         "titles, sizes and authors of the reserve's folders"),
                        ("PySide6.QtSvg", "icons"),
                        ("app.ui.kit", "the redesign's components, and the gallery's previews"),
                        ("app.services",
                         "the running jobs, the activity journal and the log files"),
                        ("app.pages", "the window's pages"),
                        ("app.pages.rotator",
                         "the Rotator page: the next run, a run under way, the history"),
                        ("app.pages.tracker",
                         "the Tracker page, its titles and authors read off the window's thread"),
                        ("app.pages.review",
                         "the Review page: the scan, the authors, finishing a review"),
                        ("app.pages.copier", "the Copier queue, verification and retry"),
                        ("app.pages.creator", "the Creator page: reading, building and verified Move"),
                        ("app.pages.review_settings",
                         "Review settings, the authors database and its backups"),
                        ("app.window_frame", "the window's own title bar"),
                        ("app.tracker_tray", "the tray tracker, its ring and its balloons"),
                        ("app.tray_menu", "the tray's menu"),
                        ("app.tray_words", "the tray's tooltip, menu hints and balloon copy"),
                        ("app.app_identity", "the balloons' sender: the Start-menu shortcut")):
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

    # A build hands the programs it starts its own DLL folder unless it clears
    # it first. Wallpaper Engine, restarted after a rotation, ran on the build's
    # VCRUNTIME140.dll because of that. PySide6 is imported by now, so PATH is
    # as polluted here as it is in the window and the tray.
    try:
        from . import external
        clean, said = external.report()
        lines.append(f"{'ok     ' if clean else 'BROKEN '} other programs  ({said})")
        ok = ok and clean
    except Exception as err:  # noqa: BLE001 — the message is the point
        ok = False
        lines.append(f"BROKEN  other programs: {type(err).__name__}: {err}")

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
    report_path().write_text(report + "\n", encoding="utf-8")
    return ok, report


def report_path() -> Path:
    from .settings import app_data_dir
    return app_data_dir() / REPORT_NAME


def problems(report: str) -> list[str]:
    """The report's lines that say something is wrong."""
    return [line for line in report.splitlines()
            if line.startswith(("MISSING", "BROKEN"))]
