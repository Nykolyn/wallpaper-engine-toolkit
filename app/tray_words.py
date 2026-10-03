"""What the tray says: the icon's state, the menu's header, the tooltip, the two
balloons, and the three numbers the menu reads off the disk.

Plain functions of a `Progress` (the tracker's reading of one monitor's
playlist) and a `Countdown` (where its wallpaper is on its timer), so they are
tested without a tray. The copy is the design's (`Tray and Notifications`),
with the app's real numbers in it; a number the tray cannot know leaves its
clause out, it is never guessed.

The three readers at the end are for the menu and the balloons only. They read
small files in the data folder (never anything on the wallpaper disks), they
read nothing until asked, and they write nothing: a counter that happens to
look first must not put a damaged history right.
"""
from __future__ import annotations

import json

from .tray_icon import FINISHED, PAUSED, RUNNING, UNKNOWN

NBSP = chr(0xA0)

# Windows cuts a tray tooltip at 128 characters.
TOOLTIP_LIMIT = 127


def count(n: int) -> str:
    """`1 000`, with a no-break space so a number never splits across a line."""
    return f"{n:,}".replace(",", NBSP)


def is_finished(progress) -> bool:
    """Every wallpaper of the playlist has been shown. An empty playlist has not."""
    return bool(progress.total) and progress.seen >= progress.total


def icon_state(progress, countdown) -> tuple[str, float | None]:
    """(state, ring fraction) for the icon.

    Finished wins over everything: the playlist is done whatever the timer
    says. Without a timer reading the ring cannot say anything, and without a
    playlist there is nothing to say at all.
    """
    if progress is None:
        return UNKNOWN, None
    if is_finished(progress):
        return FINISHED, 1.0
    fraction = countdown.fraction if countdown is not None else None
    if fraction is None:
        return UNKNOWN, None
    return (PAUSED if countdown.paused else RUNNING), fraction


def shown(progress) -> str:
    """`4 of 201 shown`, or `all 201 shown`."""
    if is_finished(progress):
        return f"all {count(progress.total)} shown"
    return f"{count(progress.seen)} of {count(progress.total)} shown"


def header_line(state: str, progress, error: str = "") -> str:
    """The menu header's second line: the icon, in words."""
    if progress is None:
        return error or "no playlist found"
    if state == FINISHED:
        return f"finished · {shown(progress)}"
    if state == PAUSED:
        return f"paused · {shown(progress)}"
    if state == UNKNOWN:
        return f"timer unknown · {shown(progress)}"
    return f"tracking · {shown(progress)}"


def tooltip_line(progress, countdown, *, lead: bool) -> str:
    """One monitor: `▸ Monitor1 · 4 of 201 shown · next in 3:45`."""
    line = f"{'▸' if lead else ' '} {progress.monitor} · {shown(progress)}"
    timing = countdown.describe() if countdown is not None else ""
    if timing and not is_finished(progress):
        line += f" · {timing}"
    return line


def tooltip(results, primary, countdowns, error: str = "") -> str:
    """One line per monitor, whole lines only, within what Windows will show."""
    if not results:
        return (error or "No playlist found")[:TOOLTIP_LIMIT]
    lines: list[str] = []
    for p in results:
        line = tooltip_line(p, countdowns.get(p.monitor), lead=p is primary)
        if len("\n".join(lines + [line])) > TOOLTIP_LIMIT:
            if not lines:
                lines.append(line[:TOOLTIP_LIMIT])
            break
        lines.append(line)
    return "\n".join(lines)


# ---- the menu's right-hand hints ---------------------------------------------------

def run_hint(next_run: int | None) -> str:
    """`run 39`: the run "Rotate now…" would start."""
    return f"run {next_run}" if next_run else ""


def review_hint(waiting: int | None) -> str:
    """`12 waiting`: authors of the last scan not gone through yet. Nothing
    waiting, or nothing known, has no hint."""
    return f"{count(waiting)} waiting" if waiting else ""


# ---- the two balloons --------------------------------------------------------------

FINISHED_TITLE = "Playlist finished"
RESTARTED_TITLE = "Playlist started over"


def finished_balloon(monitor: str, total: int, batch: int | None = None) -> tuple[str, str]:
    """"Playlist finished": the numbers are the playlist's own.

    The design adds that the folders swapped in "have never been used". Whether
    the reserve still holds that many unused ones is the window's to count (it
    lists the reserve on the wallpaper disk), not the tray's, so the clause
    says what a run does with the batch size it was given and no more.
    """
    body = f"All {count(total)} wallpapers on {monitor} have been shown."
    if batch:
        body += f" Rotate to swap in {count(batch)} folders from the reserve."
    return FINISHED_TITLE, body


def restarted_balloon(monitor: str, seen: int, total: int) -> tuple[str, str]:
    """"Playlist started over": back at the start without a rotation."""
    where = (f"is back at wallpaper {count(seen)} of {count(total)}" if seen >= 1
             else f"is back at the start of its {count(total)} wallpapers")
    return RESTARTED_TITLE, (
        f"{monitor} {where} without a rotation — Wallpaper Engine restarted or the "
        f"playlist was rebuilt. The count starts again.")


# ---- what the menu reads off the disk ----------------------------------------------

def next_run_number() -> int | None:
    """The number the next rotation would have (the history's runs + 1), or None
    when the history cannot be read."""
    try:
        from .engines.rotator import config as rconfig
        return len(rconfig.read_runs()) + 1
    except FileNotFoundError:
        return 1
    except Exception:                            # noqa: BLE001 — unknown, not an error
        return None


def rotation_batch() -> int | None:
    """Folders a rotation moves in, as the Rotator's settings file says. None
    when there is no file: its defaults are not something the user chose."""
    try:
        from .engines.rotator import config as rconfig
        data = json.loads(rconfig.CONFIG_PATH.read_text(encoding="utf-8"))
        value = data.get("count") if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def review_waiting() -> int | None:
    """Authors of the last scan not gone through yet, from `review_last.json`,
    as the sidebar's badge counts them: none once the review was finished. None
    when there is no scan or the file cannot be read."""
    try:
        from .services import snapshot
        from .settings import app_data_dir
        data = snapshot.review_summary(app_data_dir())
    except Exception:                            # noqa: BLE001 — unknown, not an error
        return None
    if data is None:
        return None
    state = snapshot.ReviewState.from_json(data)
    return None if state.finished is not None else state.waiting
