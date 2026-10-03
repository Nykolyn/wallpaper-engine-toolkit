"""The tray icon: one ring, four states, drawn for each size Windows may ask for.

The tray icon is the one part of the toolkit that lives outside its window, so
it follows Windows rather than the glass (`Tray and Notifications` in the
design). It is a ring with a state:

- **running**: the accent ring, filled to `fraction`, and the percentage of the
  playlist shown in the middle from 24 px up (a 2-ish px dot below that);
- **paused**: the same ring in grey, with a pause glyph;
- **unknown**: a dotted empty track and a "?": nothing to say yet;
- **finished**: a full green ring and a tick: the playlist is done.

What the ring *measures* is the caller's business (gate G5): today it is the
time left on the current wallpaper, so `fraction` runs from 1 just after a
change down to 0, and the number is how much of the playlist has been shown.

**Per size, not scaled.** Windows asks for 16, 20, 24, 32, 40 or 48 px
depending on the display scale. Each is drawn on its own, so the stroke is
2.5 px at 16, 3 at 24, 3.5 at 32 (the design's table, a half pixel per eight)
and the middle is the number or the dot as the room allows, instead of one
picture shrunk until its text is mush. All of them go into one `QIcon`; Windows
picks the one that fits at 150 %.

**Never animated.** The tray redraws the icon when what it shows changes. The
ring moves in 2° steps (`RING_STEPS` = 180): on a 10-minute delay that is a new
icon every few seconds rather than every second, which the eye cannot tell
apart, and it keeps a once-a-second clock from making the shell repaint a
notification icon sixty times a minute.

**Light taskbars.** With `SystemUsesLightTheme` set the ring goes `#2C6BD8`
and the glyphs `#1A1A1A`; `taskbar_is_light()` reads the switch and
`TaskbarTheme` hears Windows say it changed.

This module is the tray's: it imports Qt's core and gui and the theme, never
the kit (which would pull the whole window in).
"""
from __future__ import annotations

import ctypes
import math
import os
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import (
    QAbstractNativeEventFilter, QObject, QPointF, QRectF, Qt, QTimer, Signal)
from PySide6.QtGui import (
    QColor, QFont, QFontMetricsF, QIcon, QPainter, QPen, QPixmap, QWindow)

from . import theme

RUNNING, PAUSED, UNKNOWN, FINISHED = "running", "paused", "unknown", "finished"
STATES = (RUNNING, PAUSED, UNKNOWN, FINISHED)

# Windows asks for 16 at 100 %, 20 at 125 %, 24 at 150 %, 32 at 200 %, 40 at
# 250 % and 48 at 300 %.
SIZES = (16, 20, 24, 32, 40, 48)
NUMBER_FROM = 24                 # the percentage fits from here; below it a dot
RING_STEPS = 180                 # the ring moves in 2° steps

# The design's numbers, as fractions of the icon's size, so every size shares
# them: the pause bars, the tick and the dot.
_PAUSE = ((0.40, 0.36, 0.07, 0.28), (0.53, 0.36, 0.07, 0.28))   # x, y, w, h
_PAUSE_ROUND = 0.02
_TICK = ((0.31, 0.52), (0.44, 0.66), (0.70, 0.36))
_DOT = 0.11                      # 1.76 px radius at 16
_EDGE = 0.5                      # the ring stops half a pixel short of the edge
_DASHES = 8                      # an unknown ring's track: eight dashes, eight gaps
_NUMBER_EM = 0.41                # the number's size, 10 px at 24 and 13 at 32
_GLYPH_EM = 0.56                 # the "?": 9 px at 16, 13 at 24, 18 at 32
_TICK_STROKES = {16: 1.9, 24: 2.0, 32: 2.4}


def stroke_width(size: int) -> float:
    """The ring's stroke: 2.5 px at 16, 3 at 24, 3.5 at 32; half a pixel more
    for every eight, so 20 is 2.75 and 48 is 4.5."""
    return 2.5 + (size - 16) / 16


def ring_radius(size: int) -> float:
    """The radius of the ring's centre line: its outer edge sits half a pixel in."""
    return size / 2 - _EDGE - stroke_width(size) / 2


def tick_width(size: int) -> float:
    known = sorted(_TICK_STROKES)
    if size <= known[0]:
        return _TICK_STROKES[known[0]] * size / known[0]
    for low, high in zip(known, known[1:]):
        if size <= high:
            t = (size - low) / (high - low)
            return _TICK_STROKES[low] + (_TICK_STROKES[high] - _TICK_STROKES[low]) * t
    return _TICK_STROKES[known[-1]] * size / known[-1]


def snap(fraction: float | None) -> float | None:
    """A fraction on the 2° grid (None stays None, the rest is clamped to 0..1)."""
    if fraction is None:
        return None
    return round(min(max(fraction, 0.0), 1.0) * RING_STEPS) / RING_STEPS


@dataclass(frozen=True)
class Palette:
    """The icon's colours on one kind of taskbar."""
    ring: QColor
    paused: QColor
    ok: QColor
    glyph: QColor
    track: QColor


def palette(light: bool = False) -> Palette:
    side = "light" if light else "dark"
    return Palette(*(theme.color(f"tray.{side}.{name}")
                     for name in ("ring", "paused", "ok", "glyph", "track")))


def _mono(pixels: float, weight=QFont.Bold) -> QFont:
    font = QFont()
    font.setFamilies(list(theme.MONO))
    font.setPixelSize(max(int(round(pixels)), 1))
    font.setWeight(weight)
    font.setLetterSpacing(QFont.AbsoluteSpacing, -0.04 * pixels)
    return font


def _draw_centred(painter: QPainter, text: str, font: QFont, centre: QPointF) -> None:
    """Text with its ink centred on `centre` (a line box centres the digits high)."""
    painter.setFont(font)
    ink = QFontMetricsF(font).tightBoundingRect(text)
    painter.drawText(QPointF(centre.x() - ink.center().x(),
                             centre.y() - ink.center().y()), text)


def ring_pixmap(state: str, size: int, *, fraction: float | None = None,
                number: int | None = None, light: bool = False) -> QPixmap:
    """One state of the ring at one size, `size` × `size` pixels.

    `fraction` (0..1) is how much of the ring is filled, clockwise from twelve;
    it matters to running and paused. `number` is the percentage for the middle
    of a running ring from 24 px up. `light` is a light taskbar.
    """
    if state not in STATES:
        raise ValueError(f"no tray state {state!r}; there are {', '.join(STATES)}")
    colours = palette(light)
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.TextAntialiasing)

    width = stroke_width(size)
    radius = ring_radius(size)
    centre = QPointF(size / 2, size / 2)
    box = QRectF(centre.x() - radius, centre.y() - radius, 2 * radius, 2 * radius)

    # The track: the whole circle, or eight dashes of it when nothing is known.
    track = QPen(colours.track, width, Qt.SolidLine, Qt.FlatCap)
    if state == UNKNOWN:
        dash = math.pi * radius / _DASHES / width       # in pen widths
        track.setDashPattern([dash, dash])
    painter.setPen(track)
    painter.drawEllipse(box)

    if state in (RUNNING, PAUSED, FINISHED):
        share = 1.0 if state == FINISHED else snap(fraction)
        fill = {RUNNING: colours.ring, PAUSED: colours.paused, FINISHED: colours.ok}[state]
        if share:
            painter.setPen(QPen(fill, width, Qt.SolidLine, Qt.RoundCap))
            if share >= 1.0:
                painter.drawEllipse(box)
            else:
                # Qt's angles are 1/16 degree, counter-clockwise from three
                # o'clock: twelve is 90°, and a negative span runs clockwise.
                painter.drawArc(box, 90 * 16, -int(round(360 * 16 * share)))

    painter.setPen(Qt.NoPen)
    painter.setBrush(colours.glyph)
    if state == RUNNING or (state == UNKNOWN and number is not None):
        # The percentage of the playlist shown, or the dot where it does not fit.
        # An unknown ring that still has a count keeps saying it: the ring is
        # what is unknown, not the playlist.
        if number is not None and size >= NUMBER_FROM:
            painter.setPen(colours.glyph)
            text = str(max(0, int(number)))
            _draw_centred(painter, text, _mono(size * _NUMBER_EM * (0.8 if len(text) > 2 else 1)),
                          centre)
        else:
            painter.drawEllipse(centre, size * _DOT, size * _DOT)
    elif state == PAUSED:
        for x, y, w, h in _PAUSE:
            painter.drawRoundedRect(QRectF(x * size, y * size, w * size, h * size),
                                    _PAUSE_ROUND * size, _PAUSE_ROUND * size)
    elif state == UNKNOWN:                                 # no count either
        painter.setPen(colours.glyph)
        _draw_centred(painter, "?", _mono(size * _GLYPH_EM), centre)
    else:                                                  # FINISHED: the tick
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(colours.glyph, tick_width(size), Qt.SolidLine,
                            Qt.RoundCap, Qt.RoundJoin))
        points = [QPointF(x * size, y * size) for x, y in _TICK]
        painter.drawPolyline(points)
    painter.end()
    return pixmap


def tray_icon(state: str, fraction: float | None = None, number: int | None = None, *,
              light: bool = False) -> QIcon:
    """The ring as one icon holding every size, so Windows can pick its own."""
    icon = QIcon()
    for size in SIZES:
        icon.addPixmap(ring_pixmap(state, size, fraction=fraction, number=number,
                                   light=light))
    return icon


# ---- the taskbar's colour ----------------------------------------------------------

THEME_KEY = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
THEME_VALUE = "SystemUsesLightTheme"          # the taskbar's; AppsUseLightTheme is the apps'


def _read_light_switch() -> int | None:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, THEME_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, THEME_VALUE)
        return int(value)
    except (ImportError, OSError, ValueError, TypeError):
        return None


def taskbar_is_light(read: Callable[[], int | None] = _read_light_switch) -> bool:
    """Whether Windows draws its taskbar light. A build without the switch, or a
    machine that cannot be asked, is taken to be dark, which is Windows' default."""
    return read() == 1


# What Windows sends when the colours change: a broadcast WM_SETTINGCHANGE whose
# text is "ImmersiveColorSet", and WM_THEMECHANGED.
WM_SETTINGCHANGE = 0x001A
WM_THEMECHANGED = 0x031A
WM_DWMCOLORIZATIONCOLORCHANGED = 0x0320
_COLOUR_TEXTS = ("ImmersiveColorSet", "WindowsThemeElement")
RECHECK_SECONDS = 60             # and the switch is read again this often anyway


def is_colour_message(message: int, lparam: int) -> bool:
    """Whether a window message is Windows saying its colours changed."""
    if message in (WM_THEMECHANGED, WM_DWMCOLORIZATIONCOLORCHANGED):
        return True
    if message != WM_SETTINGCHANGE or not lparam:
        return False
    try:
        return ctypes.wstring_at(lparam, 32).split("\0")[0] in _COLOUR_TEXTS
    except (OSError, ValueError):
        return False


class TaskbarTheme(QObject, QAbstractNativeEventFilter):
    """Whether the taskbar is light, kept up to date.

    `changed(bool)` fires when it flips. Windows tells a process by a message
    broadcast to its top-level windows, and `listen()` sets up the means of
    hearing it: Qt's native event filter only sees messages that reach one of
    *Qt's own windows*, and a tray-only process has none (measured: the tray
    icon's hidden window never shows its messages to the filter), so a native
    window is created that is never shown. The switch is also read again every
    `RECHECK_SECONDS` through `poll()`, because a message that never arrives is
    a wrong icon until the next restart.
    """

    changed = Signal(bool)

    def __init__(self, read: Callable[[], int | None] = _read_light_switch,
                 parent: QObject | None = None):
        QObject.__init__(self, parent)
        QAbstractNativeEventFilter.__init__(self)
        self._read = read
        self._light = taskbar_is_light(read)
        self._since = 0
        self._pending = False
        self._window: QWindow | None = None

    def listen(self, app) -> None:
        """Start hearing Windows: the filter, and a window to hear it through.
        The window is created, not shown: no taskbar button, no Alt+Tab entry."""
        app.installNativeEventFilter(self)
        if self._window is None:
            self._window = QWindow()
            self._window.create()

    @property
    def light(self) -> bool:
        return self._light

    def refresh(self) -> bool:
        """Read the switch now. True if it changed (and `changed` is emitted)."""
        light = taskbar_is_light(self._read)
        if light == self._light:
            return False
        self._light = light
        self.changed.emit(light)
        return True

    def poll(self, seconds: int = 1) -> None:
        """Called by the tray's clock: read the switch once in `RECHECK_SECONDS`."""
        self._since += seconds
        if self._since >= RECHECK_SECONDS:
            self._since = 0
            self.refresh()

    def hear(self, message: int, lparam: int) -> None:
        """A window message of the tray's: refresh once if it is about colours."""
        if is_colour_message(message, lparam) and not self._pending:
            self._pending = True
            QTimer.singleShot(0, self._heard)       # out of the message's way

    def _heard(self) -> None:
        self._pending = False
        self.refresh()

    def nativeEventFilter(self, event_type, message):          # noqa: N802 - Qt's name
        if os.name == "nt" and bytes(event_type) == b"windows_generic_MSG":
            from ctypes import wintypes
            msg = wintypes.MSG.from_address(int(message))
            self.hear(msg.message, msg.lParam)
        return False, 0
