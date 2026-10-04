"""The tray icon: the app's mark, filled with colour as the next wallpaper nears.

The tray icon is the one part of the toolkit that lives outside its window
(`Tray: fill icon` in the design). It is the Branding page's mark, the grey
frame behind and the dark one in front, without its tile, and the two frames
fill with colour **from left to right**: the scale runs from the left edge of
the back frame to the right edge of the front one. Both frames filled whole is
100 %, anything less is less than 100 %.

What the fill measures is the caller's business: today it is the time to the
next wallpaper change, empty just after a change and full the moment the next
one is due. The playlist's progress is not in the icon; the tooltip and the
window say it.

Four states, each with its colour:

- **running**: the fill in the accent, `#4C8DFF`;
- **paused**: the fill stops where it was and goes cold grey, `#7C879C`, with a
  pause badge;
- **unknown**: no fill, the mark muted, an amber "?" badge, `#E8A33D`;
- **finished**: both frames green, `#3DD68C`, with a tick badge: the playlist
  is done.

The badge, in the bottom right corner, is only for the three states that are
not the usual one. The frames are dark in any Windows theme, so the colours are
the same on a light taskbar as on a dark one.

**Per size, not scaled.** Windows asks for 16, 20, 24, 32, 40 or 48 px
depending on the display scale. Each is drawn on its own, and the edge of the
fill is put on a whole pixel of that size. The mark spans the icon's whole
width, as the other icons in the tray fill theirs, and the fill moves a pixel
at a time across what can be seen filled: 14 steps at 16 px. A step shows once
it is reached, so the frames are filled whole only the moment the next change
is due, never with time still to go. All of them go into one `QIcon`; Windows
picks the one that fits.

**Never animated.** The tray redraws the icon only when a step is crossed
(`FILL_STEPS`, the steps of the largest size), so a once-a-second clock never
makes the shell repaint a notification icon sixty times a minute.

This module is the tray's: it imports Qt's gui and the theme, never the kit
(which would pull the whole window in).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor, QFont, QFontMetricsF, QIcon, QPainter, QPainterPath, QPen, QPixmap)

from . import theme

RUNNING, PAUSED, UNKNOWN, FINISHED = "running", "paused", "unknown", "finished"
STATES = (RUNNING, PAUSED, UNKNOWN, FINISHED)

# Windows asks for 16 at 100 %, 20 at 125 %, 24 at 150 %, 32 at 200 %, 40 at
# 250 % and 48 at 300 %.
SIZES = (16, 20, 24, 32, 40, 48)

# The mark on the Branding page's 64-unit grid (as `BrandMark` draws it), less
# its tile: the back frame (x, y, w, h, radius, stroke) and the front one.
BACK = (13, 15, 25, 20, 4, 4)
FRONT = (24, 27, 27, 22, 4, 3)
# The fill's scale: the left edge of the back frame's stroke to the right end
# of what can be seen filled, the inside of the front frame's edge (the edge
# itself is drawn over the fill, so a scale running on under it would look
# full with a minute and a half of ten still to go).
FILL_FROM = BACK[0] - BACK[5] / 2
FILL_TO = FRONT[0] + FRONT[2] - FRONT[5] / 2
# The mark's extent: left, right, top and bottom, strokes included.
RIGHT = FRONT[0] + FRONT[2] + FRONT[5] / 2
TOP = BACK[1] - BACK[5] / 2
BOTTOM = FRONT[1] + FRONT[3] + FRONT[5] / 2
# The mark fills the icon's width, as the other icons in the tray fill theirs,
# stopping this far short of each side; it is centred top to bottom.
EDGE = 0.5

# The badge: a circle in the bottom right corner, cut out of the mark by a gap.
BADGE_CENTRE = 0.78              # of the size, both ways
BADGE_RADIUS = 0.2
BADGE_GAP = 0.05
_PAUSE = ((-0.42, -0.45, 0.28, 0.9), (0.14, -0.45, 0.28, 0.9))   # of the badge's radius
_TICK = ((-0.48, 0.02), (-0.12, 0.38), (0.5, -0.36))
_GLYPH_EM = 1.5                  # the "?" against the badge's radius
UNKNOWN_OPACITY = 0.55           # a mark that knows nothing is muted


def scale(size: int) -> float:
    """Pixels per grid unit at a size: the mark spans the icon, less `EDGE`."""
    return (size - 2 * EDGE) / (RIGHT - FILL_FROM)


def origin(size: int) -> tuple[float, float]:
    """Where the grid's (0, 0) falls at a size, in pixels: the mark centred."""
    k = scale(size)
    return EDGE - FILL_FROM * k, (size - (BOTTOM - TOP) * k) / 2 - TOP * k


def _span(size: int) -> tuple[int, int]:
    """The whole pixels the fill's scale covers at a size: first, and last."""
    k, x0 = scale(size), origin(size)[0]
    return math.floor(x0 + FILL_FROM * k), round(x0 + FILL_TO * k)


def fill_steps(size: int) -> int:
    """How many whole pixels the fill's scale spans at a size: its steps."""
    first, last = _span(size)
    return max(1, last - first)


FILL_STEPS = fill_steps(max(SIZES))


def step(fill: float | None, steps: int = FILL_STEPS) -> int | None:
    """How many of `steps` a fill has reached (None stays None).

    Rounded down: a step is shown once it has been reached, never before, so
    the last one, the frames filled whole, is the moment the fill is 1 and
    not a step sooner."""
    if fill is None:
        return None
    if fill >= 1.0:
        return steps
    return min(math.floor(max(fill, 0.0) * steps), steps - 1)


def fill_edge(size: int, fill: float | None) -> float:
    """Where the fill stops at a size, in pixels from the left, on a whole pixel.
    A full one runs to the icon's edge, so nothing short of it is left unfilled."""
    reached = step(fill or 0.0, fill_steps(size))
    if reached == fill_steps(size):
        return float(size)
    return float(_span(size)[0] + reached)


@dataclass(frozen=True)
class Palette:
    """The icon's colours: a fill per state, the empty frames, the badge's glyph."""
    running: QColor
    paused: QColor
    ok: QColor
    unknown: QColor
    frame: QColor
    body: QColor
    edge: QColor
    glyph: QColor
    pause_glyph: QColor

    def fill(self, state: str) -> QColor:
        return {RUNNING: self.running, PAUSED: self.paused, FINISHED: self.ok,
                UNKNOWN: self.unknown}[state]


def palette() -> Palette:
    return Palette(*(theme.color(name) for name in (
        "tray.fill.running", "tray.fill.paused", "tray.fill.ok", "tray.fill.unknown",
        "tray.frame", "tray.body", "brand.edge", "tray.badge.glyph", "tray.badge.pause")))


def _rect(spec, size: int) -> QRectF:
    k, (x0, y0) = scale(size), origin(size)
    x, y, w, h = spec[:4]
    return QRectF(x0 + x * k, y0 + y * k, w * k, h * k)


def _glyph_font(pixels: float) -> QFont:
    font = QFont()
    font.setFamilies(list(theme.MONO))
    font.setPixelSize(max(int(round(pixels)), 1))
    font.setWeight(QFont.Bold)
    return font


def _draw_centred(painter: QPainter, text: str, font: QFont, centre: QPointF) -> None:
    """Text with its ink centred on `centre` (a line box centres the glyph high)."""
    painter.setFont(font)
    ink = QFontMetricsF(font).tightBoundingRect(text)
    painter.drawText(QPointF(centre.x() - ink.center().x(),
                             centre.y() - ink.center().y()), text)


def _draw_mark(painter: QPainter, size: int, colours: Palette, colour: QColor | None,
               edge: float) -> None:
    """The two frames, filled with `colour` left of `edge` (None: no fill)."""
    k = scale(size)
    filled = QRectF(0, 0, edge, size)
    # The back frame: an outline, grey where the fill has not reached.
    back = _rect(BACK, size)
    radius, stroke = BACK[4] * k, BACK[5] * k
    painter.setBrush(Qt.NoBrush)
    painter.setPen(QPen(colours.frame, stroke))
    painter.drawRoundedRect(back, radius, radius)
    if colour is not None and edge > 0:
        painter.save()
        painter.setClipRect(filled)
        painter.setPen(QPen(colour, stroke))
        painter.drawRoundedRect(back, radius, radius)
        painter.restore()
    # The front frame: a dark body over the back one, cut out of it by its edge.
    front = _rect(FRONT, size)
    radius, stroke = FRONT[4] * k, FRONT[5] * k
    body = QPainterPath()
    body.addRoundedRect(front, radius, radius)
    painter.setPen(Qt.NoPen)
    painter.setBrush(colours.body)
    painter.drawPath(body)
    if colour is not None and edge > 0:
        painter.save()
        painter.setClipRect(filled)
        painter.setBrush(colour)
        painter.drawPath(body)
        painter.restore()
    painter.setBrush(Qt.NoBrush)
    painter.setPen(QPen(colours.edge, stroke))
    painter.drawPath(body)


def _draw_badge(painter: QPainter, state: str, size: int, colours: Palette) -> None:
    centre = QPointF(BADGE_CENTRE * size, BADGE_CENTRE * size)
    radius = BADGE_RADIUS * size
    gap = max(BADGE_GAP * size, 1.0)
    # Cut the mark away round the badge, so it reads at 16 px.
    painter.save()
    painter.setCompositionMode(QPainter.CompositionMode_Clear)
    painter.setPen(Qt.NoPen)
    painter.setBrush(Qt.black)
    painter.drawEllipse(centre, radius + gap, radius + gap)
    painter.restore()
    painter.setPen(Qt.NoPen)
    painter.setBrush(colours.fill(state))
    painter.drawEllipse(centre, radius, radius)
    if state == PAUSED:
        painter.setBrush(colours.pause_glyph)
        for x, y, w, h in _PAUSE:
            painter.drawRect(QRectF(centre.x() + x * radius, centre.y() + y * radius,
                                    w * radius, h * radius))
    elif state == UNKNOWN:
        painter.setPen(colours.glyph)
        _draw_centred(painter, "?", _glyph_font(radius * _GLYPH_EM), centre)
    else:                                                   # FINISHED: the tick
        painter.setPen(QPen(colours.glyph, max(radius * 0.36, 1.0), Qt.SolidLine,
                            Qt.RoundCap, Qt.RoundJoin))
        painter.drawPolyline([QPointF(centre.x() + x * radius, centre.y() + y * radius)
                              for x, y in _TICK])


def mark_pixmap(state: str, size: int, *, fill: float | None = None) -> QPixmap:
    """One state of the mark at one size, `size` × `size` pixels.

    `fill` (0..1) is how much of the two frames is filled, from the left; it
    matters to running and paused. Finished is always full, unknown empty.
    """
    if state not in STATES:
        raise ValueError(f"no tray state {state!r}; there are {', '.join(STATES)}")
    colours = palette()
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.TextAntialiasing)

    if state == UNKNOWN:
        painter.setOpacity(UNKNOWN_OPACITY)
        _draw_mark(painter, size, colours, None, 0.0)
        painter.setOpacity(1.0)
    else:
        share = 1.0 if state == FINISHED else fill
        _draw_mark(painter, size, colours, colours.fill(state), fill_edge(size, share))
    if state != RUNNING:
        _draw_badge(painter, state, size, colours)
    painter.end()
    return pixmap


def tray_icon(state: str, fill: float | None = None) -> QIcon:
    """The mark as one icon holding every size, so Windows can pick its own."""
    icon = QIcon()
    for size in SIZES:
        icon.addPixmap(mark_pixmap(state, size, fill=fill))
    return icon
