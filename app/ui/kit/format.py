"""Numbers, sizes, times and dates, written the one way the design writes them.

Pages never format a number themselves. A count, a size, a duration or a date
goes through here, so `33 421` reads the same on every screen and an estimate
is marked the same way wherever it appears.

- `count(33421)` → `33 421`: thousands apart by a no-break space, so a number
  never breaks across a line. `counted(3, "folder")` → `3 folders`.
- `size(224_395_264)` → `214 MB`; `1.1 GB` and `4.2 TB`, one decimal from GB up.
  Units are binary, as Explorer counts them, and as the engines always have.
- `duration(252)` → `4 min 12 s`; `duration(840)` → `14 min`; `left(372)` →
  `≈6 min left`.
- `approx("21 Sep")` → `≈21 Sep` (estimated); `reconstructed("12:44")` →
  `~12:44` (rebuilt after a restart). `split_qualifier` takes the mark back
  off, for a painter that draws it in `text.lo`.
- Dates: `date_table` → `19 Sep 12:44`; `date_activity` → `13:47` today,
  `Fri 09:10` within the week; `date_long` → `Saturday 19 September, 13:44`;
  `day` → `19 Sep`; `estimate_day` → `≈21 Sep`.
- `ratio(4, 201)` → `4 / 201` in a card, `4/201` in the nav (`style="nav"`),
  `412 of 1 000` in a sentence (`style="prose"`); `percent(412, 1000)` → `41%`.

Month and day names are English and fixed. `strftime("%b")` follows the
Windows locale, and the design's copy is English.
"""
from __future__ import annotations

from datetime import date, datetime

NBSP = chr(0xA0)          # the thousands separator
APPROX = "≈"              # an estimate: from measured history or the live rate
RECONSTRUCTED = "~"       # a time rebuilt after a restart, not observed
QUALIFIERS = (APPROX, RECONSTRUCTED)
DASH = "—"                # a value that is not known

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
MONTHS_LONG = ("January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December")
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
WEEKDAYS_LONG = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
                 "Saturday", "Sunday")


# ---- counts --------------------------------------------------------------------

def count(n: int) -> str:
    """`33 421`, `1 000`, `0`."""
    return f"{int(n):,}".replace(",", NBSP)


def counted(n: int, singular: str, plural: str | None = None) -> str:
    """`1 folder`, `33 421 folders`."""
    word = singular if n == 1 else (plural or singular + "s")
    return f"{count(n)} {word}"


# ---- sizes ---------------------------------------------------------------------

_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")
_DECIMAL_FROM = 3         # GB


def size(num_bytes: float) -> str:
    """`512 B`, `12 KB`, `214 MB`, `1.1 GB`, `4.2 TB`.

    A value that rounds up to the next unit is written in it: 1 023.7 MB is
    `1.0 GB`, not `1 024 MB`.
    """
    value = float(num_bytes)
    unit = 0
    while abs(value) >= 1024 and unit < len(_UNITS) - 1:
        value /= 1024
        unit += 1
    while True:
        decimals = 1 if unit >= _DECIMAL_FROM else 0
        shown = round(value, decimals)
        if abs(shown) >= 1024 and unit < len(_UNITS) - 1:
            value /= 1024
            unit += 1
            continue
        break
    if decimals:
        return f"{shown:.1f} {_UNITS[unit]}"
    return f"{count(shown)} {_UNITS[unit]}"


# ---- durations ------------------------------------------------------------------

def duration(seconds: float, *, exact: bool = True) -> str:
    """`42 s`, `4 min 12 s`, `14 min`, `2 h 5 min`, `3 d 4 h`.

    Seconds are only worth writing while the minutes are few: under ten
    minutes a job's length reads in minutes and seconds, past it in minutes.
    `exact=False` drops them from a minute up, for a figure that is only an
    estimate anyway.
    """
    s = max(0, round(seconds))
    if s < 60:
        return f"{s} s"
    minutes, rest = divmod(s, 60)
    if minutes < 10 and exact:
        return f"{minutes} min {rest} s" if rest else f"{minutes} min"
    minutes = round(s / 60)
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} h {minutes} min" if minutes else f"{hours} h"
    days, hours = divmod(hours, 24)
    return f"{days} d {hours} h" if hours else f"{days} d"


def left(seconds: float) -> str:
    """`≈6 min left`: time still to go is always an estimate, so it is never
    written to the second."""
    return approx(duration(seconds, exact=False)) + " left"


# ---- qualifiers ------------------------------------------------------------------

def approx(text: str) -> str:
    """`≈text`: estimated, from measured history or the live rate."""
    text = str(text)
    return text if text.startswith(APPROX) else APPROX + text


def reconstructed(value) -> str:
    """`~12:44`: a time rebuilt after a restart rather than seen happening.
    A datetime is written as a clock time first."""
    text = value if isinstance(value, str) else clock(value)
    return text if text.startswith(RECONSTRUCTED) else RECONSTRUCTED + text


def split_qualifier(text: str) -> tuple[str, str]:
    """`("≈", "6 min")` from `≈6 min`, `("", "6 min")` from `6 min`, so the mark
    can be drawn in its own colour on the same baseline."""
    if text and text[0] in QUALIFIERS:
        return text[0], text[1:]
    return "", text


# ---- dates ------------------------------------------------------------------------

def _local(value) -> datetime:
    """A timestamp, a date or a datetime, as a naive local datetime."""
    if isinstance(value, datetime):
        return value.astimezone().replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    return datetime.fromtimestamp(float(value))


def _now(now) -> datetime:
    return datetime.now() if now is None else _local(now)


def clock(value) -> str:
    """`13:47`."""
    dt = _local(value)
    return f"{dt.hour:02d}:{dt.minute:02d}"


def day(value, now=None) -> str:
    """`19 Sep`, and the year too when it is not this one: `3 Jul 2025`."""
    dt, today = _local(value), _now(now)
    text = f"{dt.day} {MONTHS[dt.month - 1]}"
    return text if dt.year == today.year else f"{text} {dt.year}"


def date_table(value, now=None) -> str:
    """`19 Sep 12:44`, the way a table column writes a moment."""
    return f"{day(value, now)} {clock(value)}"


def date_activity(value, now=None) -> str:
    """How a list of recent events writes when each happened: `13:47` today,
    `Fri 09:10` within the six days before it, `19 Sep 12:44` further back."""
    dt, today = _local(value), _now(now)
    days = (today.date() - dt.date()).days
    if days == 0:
        return clock(dt)
    if 1 <= days <= 6:
        return f"{WEEKDAYS[dt.weekday()]} {clock(dt)}"
    return date_table(dt, today)


def date_long(value) -> str:
    """`Saturday 19 September, 13:44`, for a page header."""
    dt = _local(value)
    return (f"{WEEKDAYS_LONG[dt.weekday()]} {dt.day} {MONTHS_LONG[dt.month - 1]}, "
            f"{clock(dt)}")


def estimate_day(value, now=None) -> str:
    """`≈21 Sep`: the day something is expected, never promised."""
    return approx(day(value, now))


# ---- ratios --------------------------------------------------------------------------

RATIO_STYLES = ("card", "nav", "prose")


def ratio(done: int, total: int, style: str = "card") -> str:
    """`4 / 201` in a card, `4/201` in the nav, `412 of 1 000` in a sentence."""
    a, b = count(done), count(total)
    if style == "card":
        return f"{a} / {b}"
    if style == "nav":
        return f"{a}/{b}"
    if style == "prose":
        return f"{a} of {b}"
    raise ValueError(f"no ratio style {style!r}; there are {', '.join(RATIO_STYLES)}")


def percent(done: float, total: float) -> str:
    """`41%`. Never `100%` before the last item, and never `0%` after the first:
    a ring that reads 100 while work remains, or 0 while it moves, is lying."""
    if total <= 0:
        return "0%"
    value = round(100 * done / total)
    if done < total:
        value = min(value, 99)
    if done > 0:
        value = max(value, 1)
    return f"{max(0, min(100, value))}%"
