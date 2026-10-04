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
    """(state, fill) for the icon: the fill is the time to the next wallpaper
    change, 0 just after one and 1 the moment the next is due.

    Finished wins over everything: the playlist is done whatever the timer
    says. Without a timer reading the fill cannot say anything, and without a
    playlist there is nothing to say at all.
    """
    if progress is None:
        return UNKNOWN, None
    if is_finished(progress):
        return FINISHED, 1.0
    left = countdown.fraction if countdown is not None else None
    if left is None:
        return UNKNOWN, None
    return (PAUSED if countdown.paused else RUNNING), 1.0 - left


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
    """One monitor, the playlist's share first, since the icon no longer shows
    it: `▸ 55% · Monitor1 · 81 of 192 shown · next in 4:29`, or
    `100% · Monitor1 · 192 of 192 shown · finished`."""
    line = (f"{'▸' if lead else ' '} {progress.percent}% · {progress.monitor} · "
            f"{count(progress.seen)} of {count(progress.total)} shown")
    if is_finished(progress):
        return line + " · finished"
    timing = countdown.describe() if countdown is not None else ""
    if timing:
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


def finished_balloon(monitor: str, total: int, batch: int | None = None,
                     reserve: dict | None = None) -> tuple[str, str]:
    """"Playlist finished": the numbers are the playlist's own.

    The design adds that the folders swapped in "have never been used". How
    many the reserve still has is the window's to count (it lists the reserve
    on the wallpaper disk), so it is said only from a fresh count the window
    left (`reserve`, from `never_used()`); without one the clause says what a
    run does with the batch size it was given and no more.
    """
    body = f"All {count(total)} wallpapers on {monitor} have been shown."
    if reserve is not None and batch and reserve["batch"] == batch:
        # Counted for this batch size: with another, whether it resets differs.
        never = reserve["never_used"]
        if reserve["will_reset"]:
            body += (f" Rotate to swap in {count(batch)}: only {count(never)} folders were "
                     f"never used, so the run draws from the whole reserve again.")
        else:
            body += (f" Rotate to swap in {count(batch)} of the {count(never)} folders "
                     f"that have never been used.")
    elif batch:
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


# A count of the reserve older than this is not said: folders are added by hand.
RESERVE_FRESH_SECONDS = 24 * 3600


def never_used(now: float | None = None) -> dict | None:
    """The reserve's last count, as the window left it (snapshot.remember_reserve),
    when it is fresh enough to say: taken within RESERVE_FRESH_SECONDS, and no
    rotation recorded since, since a run uses up a batch of them. None
    otherwise, or when the file is missing or damaged; the balloon then keeps
    its words without the number."""
    import time

    from .engines.rotator import config as rconfig
    from .services.snapshot import RESERVE_COUNT_FILE
    from .settings import app_data_dir
    try:
        data = json.loads((app_data_dir() / RESERVE_COUNT_FILE).read_text(encoding="utf-8"))
        counted = float(data["counted_at"])
        found = {"never_used": int(data["never_used"]), "batch": int(data["batch"]),
                 "will_reset": bool(data["will_reset"])}
    except (OSError, ValueError, TypeError, KeyError):
        return None
    if found["never_used"] < 0 or not 0 <= (time.time() if now is None else now) - counted \
            <= RESERVE_FRESH_SECONDS:
        return None
    try:
        if rconfig.HISTORY_PATH.stat().st_mtime > counted:
            return None                 # a run since: a batch of them was used
    except OSError:
        pass                            # no history: no run since, either
    return found
