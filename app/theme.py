"""The one place the toolkit's look is defined.

Everything visual comes from the design system's token table: colours by their
dotted design names (`text.lo`, `surface.well`, `accent.hover`), a type scale
(`type.h3`), spacing, radii and elevations. Qt draws the standard controls from
one stylesheet generated here out of those tokens; everything the kit paints
itself asks `color()`, `font()` and `paint_shadow()` for the same values.

The design is dark only. The token *names* say what a colour is for, never what
it looks like, so a light palette can come back later without touching the UI;
it is not kept half-built in the meantime.
"""
from __future__ import annotations

import hashlib
import math
import re
import sys
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush, QColor, QFont, QIcon, QImage, QLinearGradient, QPainter,
    QPainterPath, QPalette, QPixmap, QRadialGradient, QTransform,
)
from PySide6.QtWidgets import QApplication


# ---- Colour ---------------------------------------------------------------
#
# Values are exactly the design's: a hex colour, or `rgba()` with a 0–1 alpha.
# Glass is translucent white over a dark gradient, so most surfaces are white
# at a few percent rather than a grey — a grey would not pick up the gradient.

TOKENS: dict[str, str] = {
    # background and chrome
    "bg.solid": "#1B2130",                      # dialogs, and behind the gradient
    "chrome.titlebar": "rgba(255,255,255,.04)",
    "chrome.statusbar": "rgba(255,255,255,.05)",
    "chrome.divider": "rgba(255,255,255,.07)",
    "nav.edge": "rgba(255,255,255,.09)",
    "nav.edgeInner": "rgba(255,255,255,.05)",
    "nav.selected": "rgba(255,255,255,.13)",
    "nav.selectedSheen": "rgba(255,255,255,.16)",
    "nav.overline": "#8B94A6",                  # the one text below text.lo
    "scrim": "rgba(8,10,14,.62)",

    # surfaces
    "surface.raised": "rgba(255,255,255,.12)",  # hover, selected, chip ground
    "surface.raisedHi": "rgba(255,255,255,.16)",
    "surface.wash": "rgba(255,255,255,.06)",    # secondary button
    "surface.washHover": "rgba(255,255,255,.13)",
    "surface.washPress": "rgba(255,255,255,.05)",
    "surface.washDisabled": "rgba(255,255,255,.03)",
    "surface.press": "rgba(0,0,0,.25)",         # ghost and icon buttons, pressed
    "surface.subtle": "rgba(255,255,255,.035)",
    "surface.header": "rgba(255,255,255,.04)",  # table header
    "surface.footer": "rgba(255,255,255,.03)",  # table footer
    "surface.zebra": "rgba(255,255,255,.025)",
    "surface.well": "rgba(8,10,14,.5)",         # inputs, tracks, thumbs
    "surface.console": "rgba(6,8,12,.72)",
    "surface.overlay": "rgba(18,22,30,.94)",    # dialog, toast
    "surface.popup": "rgba(18,22,30,.97)",      # menu, dropdown, tool tip
    "surface.note": "rgba(255,255,255,.04)",    # a neutral Callout

    # borders
    "border.hairline": "rgba(255,255,255,.10)",
    "border.control": "rgba(255,255,255,.14)",
    "border.strong": "rgba(255,255,255,.22)",
    "border.focus": "#6FA5FF",
    "focus.ring": "rgba(76,141,255,.45)",
    "sheen": "rgba(255,255,255,.12)",           # inset top light
    "sheen.hi": "rgba(255,255,255,.14)",

    # text
    "text.hi": "#F0F3F8",                       # titles, numerals
    "text.body": "#E6E9EF",
    "text.mid": "#B7C0D0",                      # secondary
    "text.lo": "#98A1B3",                       # captions; the floor on glass
    "text.disabled": "rgba(230,233,239,.38)",
    "text.onAccent": "#0E1116",                 # on accent, ok and warn fills
    "text.onDanger": "#FFFFFF",

    # accent and status
    "accent": "#4C8DFF",
    "accent.hover": "#6FA5FF",
    "accent.press": "#3B76DE",
    "accent.soft": "rgba(76,141,255,.16)",
    "ok": "#3DD68C",
    "ok.soft": "rgba(61,214,140,.15)",
    "warn": "#F5A524",
    "warn.soft": "rgba(245,165,36,.16)",
    "danger": "#FF6B6B",                        # text and icons on dark
    "danger.soft": "rgba(255,107,107,.15)",
    "danger.solid": "#D64545",                  # DangerButton
    "danger.solidHover": "#E25555",
    "danger.solidPress": "#B93A3A",
    "info": "#7FB2FF",
    "info.soft": "rgba(127,178,255,.14)",

    # The edge of a tinted box — a chip, a callout, a panel with a verdict —
    # in its own hue, stronger than its fill so the box reads on glass.
    "accent.line": "rgba(76,141,255,.55)",
    "ok.line": "rgba(61,214,140,.40)",
    "warn.line": "rgba(245,165,36,.45)",
    "danger.line": "rgba(255,107,107,.45)",
    "info.line": "rgba(127,178,255,.40)",

    # the log console
    "console.text": "#D3D9E3",
    "console.dim": "#98A1B3",
    "console.ok": "#3DD68C",
    "console.warn": "#F5A524",
    "console.err": "#FF6B6B",
    "console.rowAlt": "rgba(255,255,255,.035)",
    "console.selection": "rgba(76,141,255,.28)",

    # the tray icon on a light taskbar
    "tray.light.ring": "#2C6BD8",
    "tray.light.glyph": "#1A1A1A",

    # One hue per kind of wallpaper, which the gallery's cards still use. The
    # design shows the kind as plain text instead; these go with the gallery
    # rewrite.
    "kind.scene": "#5BC8E8",
    "kind.video": "#C58BFF",
    "kind.web": "#3DD68C",
    "kind.application": "#F5A524",
    "kind.preset": "#98A1B3",
}

# Three surfaces are gradients rather than colours. Stops are (position, value).
GRADIENTS: dict[str, tuple[tuple[float, str], ...]] = {
    # radial-gradient(1100px 620px at 16% -12%, ...), painted once per window
    "bg.app": ((0.0, "#2C3752"), (0.46, "#1B2130"), (1.0, "#141821")),
    # linear-gradient(155deg, ...), every panel and card
    "surface.glass": ((0.0, "rgba(255,255,255,.085)"), (1.0, "rgba(255,255,255,.022)")),
    # linear-gradient(180deg, ...), the sidebar
    "nav.gradient": ((0.0, "rgba(255,255,255,.115)"), (0.48, "rgba(255,255,255,.05)"),
                     (1.0, "rgba(255,255,255,.015)")),
}

# The app background's ellipse, as the design writes it.
BG_APP_CENTRE = (0.16, -0.12)        # fraction of the width and of the height
BG_APP_RADII = (1100.0, 620.0)       # px
GLASS_ANGLE = 155.0                  # CSS degrees: 0 points up, 90 right

_RGBA = re.compile(r"rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)")


def parse(value: str) -> QColor:
    """A colour as the design writes it — `#RRGGBB` or `rgba(r,g,b,.5)` — as a QColor.

    QColor reads the hex forms itself but not CSS's `rgba()` with a fractional
    alpha, which is how every translucent value in the design is written.
    """
    value = value.strip()
    match = _RGBA.fullmatch(value)
    if match:
        r, g, b, a = match.groups()
        colour = QColor(int(r), int(g), int(b))
        colour.setAlphaF(float(a) if a is not None else 1.0)
        return colour
    colour = QColor(value)
    if not colour.isValid():
        raise ValueError(f"not a colour: {value!r}")
    return colour


@lru_cache(maxsize=None)
def _parsed(name: str) -> QColor:
    try:
        return parse(TOKENS[name])
    except KeyError:
        raise KeyError(f"no colour token {name!r}") from None


def color(name: str, alpha: float | None = None) -> QColor:
    """A colour token as a QColor. A copy, so the caller may change it freely.

    `alpha` scales the token's own opacity, as `css()` does: the design's
    disabled AccentButton is "the accent at 30 %".
    """
    c = QColor(_parsed(name))
    if alpha is not None:
        c.setAlphaF(c.alphaF() * alpha)
    return c


def css(name: str, alpha: float | None = None) -> str:
    """A colour token as text for a stylesheet, rich text or QColor().

    `#RRGGBB` when opaque, otherwise Qt's `#AARRGGBB` — the one spelling that
    QSS, QColor and Qt's rich text all read the same way. `alpha` scales the
    token's own opacity, for the few states the design writes as "the accent
    at 30%".
    """
    c = color(name, alpha)
    return c.name(QColor.HexArgb if c.alpha() < 255 else QColor.HexRgb).upper()


def composite(top: str | QColor, bottom: str | QColor) -> QColor:
    """`top` laid over an opaque `bottom`: what a translucent token actually shows.

    Contrast has to be measured on this, not on the token — `text.lo` over
    `surface.glass` means text over white-at-5% over the app background.
    """
    t = color(top) if isinstance(top, str) else QColor(top)
    b = color(bottom) if isinstance(bottom, str) else QColor(bottom)
    a = t.alphaF()
    return QColor(round(t.red() * a + b.red() * (1 - a)),
                  round(t.green() * a + b.green() * (1 - a)),
                  round(t.blue() * a + b.blue() * (1 - a)))


def panel_ground() -> QColor:
    """What text on a panel actually sits on: `surface.glass` at its middle, over
    `bg.solid`. The design measures it at ≈#262C3A and holds `text.lo` to it."""
    stops = [parse(value) for _, value in GRADIENTS["surface.glass"]]
    middle = QColor(stops[0])
    middle.setAlphaF((stops[0].alphaF() + stops[-1].alphaF()) / 2)
    return composite(middle, "bg.solid")


def contrast(fg: QColor, bg: QColor) -> float:
    """WCAG contrast ratio between two opaque colours."""
    def luminance(c: QColor) -> float:
        def channel(v: int) -> float:
            v /= 255
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        return 0.2126 * channel(c.red()) + 0.7152 * channel(c.green()) + 0.0722 * channel(c.blue())
    hi, lo = sorted((luminance(fg), luminance(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def gradient(name: str, rect: QRectF) -> QLinearGradient:
    """`surface.glass` or `nav.gradient` laid across `rect`, as the design draws it."""
    if name == "surface.glass":
        # CSS spreads an angled gradient so that the two far corners land
        # exactly on the first and last stop; the half-length is the rectangle
        # projected onto the gradient's direction.
        rad = math.radians(GLASS_ANGLE)
        dx, dy = math.sin(rad), -math.cos(rad)
        half = (abs(rect.width() * dx) + abs(rect.height() * dy)) / 2
        c = rect.center()
        grad = QLinearGradient(c - QPointF(dx, dy) * half, c + QPointF(dx, dy) * half)
    elif name == "nav.gradient":
        grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    else:
        raise KeyError(f"no linear gradient {name!r}")
    for position, value in GRADIENTS[name]:
        grad.setColorAt(position, parse(value))
    return grad


def app_background(rect) -> QBrush:
    """`bg.app` for a window of this size, as a brush to keep until it resizes.

    CSS draws it as an ellipse 1100 × 620 px centred above the top-left of the
    window. Qt's radial gradient is a circle, so it is drawn with the vertical
    radius and the brush is stretched sideways into the ellipse.
    """
    rect = QRectF(rect)
    rx, ry = BG_APP_RADII
    grad = QRadialGradient(QPointF(0, 0), ry)
    for position, value in GRADIENTS["bg.app"]:
        grad.setColorAt(position, parse(value))
    brush = QBrush(grad)
    t = QTransform()
    t.translate(rect.left() + rect.width() * BG_APP_CENTRE[0],
                rect.top() + rect.height() * BG_APP_CENTRE[1])
    t.scale(rx / ry, 1.0)
    brush.setTransform(t)
    return brush


def paint_app_background(painter: QPainter, rect) -> None:
    """Fill `rect` with `bg.app`, the radial gradient everything else sits on."""
    painter.fillRect(QRectF(rect), app_background(rect))


# ---- Type -----------------------------------------------------------------

SANS = ("Segoe UI Variable Text", "Segoe UI Variable", "Segoe UI")
# Consolas is monospaced, so every numeral is tabular and a count does not
# jitter sideways while it climbs.
MONO = ("Consolas", "Cascadia Mono")


@dataclass(frozen=True)
class TypeSpec:
    px: float                 # size in CSS px = Qt logical px
    line: float               # line height, as a multiple of the size
    weight: int = 400
    mono: bool = False
    upper: bool = False
    tracking: float = 0.0     # letter spacing in em


TYPE: dict[str, TypeSpec] = {
    "type.display": TypeSpec(38, 1.0, 600, mono=True),     # big counters
    "type.numeric": TypeSpec(30, 1.1, 600, mono=True),     # StatCard value
    "type.count": TypeSpec(26, 1.0, 600, mono=True),       # MonitorCard count
    "type.metricLg": TypeSpec(22, 1.0, 600, mono=True),    # Pace value
    "type.metric": TypeSpec(17, 1.0, 600, mono=True),      # MetricStrip numbers
    "type.h1": TypeSpec(24, 1.2, 600),
    "type.hero": TypeSpec(17, 1.3, 600),                   # EmptyState title
    "type.h2": TypeSpec(15, 1.3, 600),                     # page title
    "type.h3": TypeSpec(12.5, 1.35, 600),                  # card title
    "type.body": TypeSpec(12.5, 1.45),
    "type.bodySm": TypeSpec(11.5, 1.4),                    # dense rows, buttons
    "type.label": TypeSpec(11, 1.3),
    "type.labelStrong": TypeSpec(11, 1.3, 600),            # a Callout's title
    "type.caption": TypeSpec(10.5, 1.4),                   # text.lo only
    "type.overline": TypeSpec(10, 1.3, 600, mono=True, upper=True, tracking=0.11),
    "type.chip": TypeSpec(10, 1.2, 600, mono=True, upper=True, tracking=0.04),  # Chip label
    "type.mono": TypeSpec(11, 1.85, mono=True),            # paths, ids, log
    "type.monoSm": TypeSpec(10.5, 1.4, mono=True),         # meta, counts
}


def _spec(token: str) -> TypeSpec:
    try:
        return TYPE[token]
    except KeyError:
        raise KeyError(f"no type token {token!r}") from None


def point_size(px: float) -> float:
    """CSS px to Qt points. 11.5 px cannot go through setPixelSize(int)."""
    return px * 0.75


def font(token: str) -> QFont:
    """A type token as a QFont: family, size (fractional px survive), weight."""
    spec = _spec(token)
    f = QFont()
    f.setFamilies(list(MONO if spec.mono else SANS))
    f.setStyleHint(QFont.Monospace if spec.mono else QFont.SansSerif)
    f.setPointSizeF(point_size(spec.px))
    f.setWeight(QFont.Weight(spec.weight))
    if spec.upper:
        f.setCapitalization(QFont.AllUppercase)
    if spec.tracking:
        f.setLetterSpacing(QFont.AbsoluteSpacing, spec.tracking * spec.px)
    return f


def line_height(token: str) -> float:
    """The token's line height in px, for layouts that stack lines by hand."""
    spec = _spec(token)
    return spec.px * spec.line


def _families(families) -> str:
    return ", ".join(f'"{name}"' for name in families)


def qss_font(token: str) -> str:
    """A type token as stylesheet declarations. Sizes in pt, as fractions need."""
    spec = _spec(token)
    return (f"font-family: {_families(MONO if spec.mono else SANS)}; "
            f"font-size: {point_size(spec.px):g}pt; font-weight: {spec.weight};")


# ---- Space, radius, elevation ---------------------------------------------

SPACING: dict[str, int] = {f"sp.{n}": n for n in (2, 4, 6, 8, 10, 12, 14, 16, 20, 24, 32)}
SP_2, SP_4, SP_6, SP_8, SP_10, SP_12, SP_14, SP_16, SP_20, SP_24, SP_32 = SPACING.values()

RADIUS: dict[str, int] = {
    "r.sm": 4,      # chips, thumbs, badges
    "r.md": 6,      # buttons, inputs
    "r.row": 7,     # nav items, list rows, callouts
    "r.lg": 10,     # cards, panels
    "r.xl": 12,     # dialogs, drop zones
    "r.pill": 999,  # toggles, pills
}
R_SM, R_MD, R_ROW, R_LG, R_XL, R_PILL = RADIUS.values()


@dataclass(frozen=True)
class Elevation:
    dy: float = 0.0       # the shadow's offset downwards, px
    blur: float = 0.0     # CSS blur radius, px (a Gaussian of sigma blur/2)
    alpha: float = 0.0    # black at this opacity
    sheen: float = 0.0    # the inset top light, white at this opacity
    inset: bool = False


ELEVATION: dict[str, Elevation] = {
    "elev.0": Elevation(),                                  # flush, inside a card
    "elev.1": Elevation(2, 6, 0.28, sheen=0.12),            # controls
    "elev.2": Elevation(14, 34, 0.42, sheen=0.12),          # cards, panels
    "elev.3": Elevation(28, 70, 0.60, sheen=0.14),          # dialog, menu, toast
    "elev.inset": Elevation(2, 5, 0.45, inset=True),        # wells, pressed
}


def _elevation(elev: str) -> Elevation:
    try:
        return ELEVATION[elev]
    except KeyError:
        raise KeyError(f"no elevation {elev!r}") from None


def _phi(x: float) -> float:
    """The standard normal CDF: how much of a blurred edge has faded at x sigmas."""
    return 0.5 * math.erfc(-x / math.sqrt(2))


def _margin(e: Elevation) -> int:
    """How far a shadow reaches past its box: three sigmas, where it is gone."""
    return math.ceil(3 * e.blur / 2)


def shadow_reach(elev: str) -> tuple[int, int, int, int]:
    """How far an elevation's outer shadow reaches past its box: left, top,
    right, bottom. Nothing for elev.0 and the inset."""
    e = _elevation(elev)
    if e.inset or e.blur <= 0:
        return (0, 0, 0, 0)
    m, dy = _margin(e), math.ceil(e.dy)
    return (m, max(0, m - dy), m, m + dy)


_shadows: dict[tuple[str, float], QPixmap] = {}


def shadow(elev: str, dpr: float = 1.0) -> QPixmap:
    """The pre-blurred shadow of an elevation, as a nine-slice source. Cached.

    It is a box of 2m+1 px with the shadow's full reach m around it, so every
    corner piece is 2m square — m of falloff outside the box and m inside, where
    the other edge's falloff ends. Blurring a box by a Gaussian separates into
    one profile across and one down, so the image is two one-pixel profiles
    multiplied together by the painter, not a per-pixel loop in Python.

    Corners are the square box's; at these blurs (sigma 3 to 35 px against
    radii of 6 to 12) the rounding does not show in the shadow.
    """
    key = (elev, round(dpr, 3))
    cached = _shadows.get(key)
    if cached is not None:
        return cached
    e = _elevation(elev)
    if e.inset or e.blur <= 0:
        raise ValueError(f"{elev} has no outer shadow")
    sigma, m = e.blur / 2, _margin(e)
    logical = 4 * m + 1
    size = math.ceil(logical * dpr)

    def profile() -> bytes:
        out = bytearray(4 * size)
        for i in range(size):
            x = (i + 0.5) / dpr
            cover = _phi((x - m) / sigma) - _phi((x - (3 * m + 1)) / sigma)
            out[4 * i + 3] = max(0, min(255, round(255 * cover)))
        return bytes(out)

    line = profile()
    across = QImage(line, size, 1, 4 * size, QImage.Format_ARGB32_Premultiplied).copy()
    down = QImage(line, 1, size, 4, QImage.Format_ARGB32_Premultiplied).copy()

    image = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    p = QPainter(image)
    p.setOpacity(e.alpha)
    p.drawImage(QRectF(0, 0, size, size), across)
    p.setOpacity(1.0)
    p.setCompositionMode(QPainter.CompositionMode_DestinationIn)
    p.drawImage(QRectF(0, 0, size, size), down)
    p.end()

    pix = QPixmap.fromImage(image)
    pix.setDevicePixelRatio(dpr)
    _shadows[key] = pix
    return pix


def paint_shadow(painter: QPainter, rect, elev: str, radius: float = R_LG) -> None:
    """Paint an elevation's shadow for a box at `rect` with corner `radius`.

    Outer shadows are drawn only outside the box, as CSS draws them: glass is
    translucent, and a shadow under it would read as a darker panel. The inset
    shadow of wells is the top edge darkening inside the box.
    """
    e = _elevation(elev)
    rect = QRectF(rect)
    if e.blur <= 0 or rect.isEmpty():
        return
    box = QPainterPath()
    box.addRoundedRect(rect, radius, radius)
    painter.save()
    if e.inset:
        _paint_inset(painter, rect, box, e)
        painter.restore()
        return

    dpr = painter.device().devicePixelRatioF() if painter.device() else 1.0
    pix = shadow(elev, dpr)
    m = _margin(e)
    outer = rect.adjusted(-m, -m + e.dy, m, m + e.dy)
    clip = QPainterPath()
    clip.addRect(outer)
    painter.setClipPath(clip.subtracted(box))

    # Nine pieces. A box narrower than the two corners shares what it has.
    src_c = 2 * m * dpr
    src_mid = pix.width() - 2 * src_c
    cx = min(2 * m, outer.width() / 2)
    cy = min(2 * m, outer.height() / 2)
    xs = (outer.left(), outer.left() + cx, outer.right() - cx, outer.right())
    ys = (outer.top(), outer.top() + cy, outer.bottom() - cy, outer.bottom())
    sx = (0.0, src_c, src_c + src_mid, float(pix.width()))
    sy = (0.0, src_c, src_c + src_mid, float(pix.height()))
    for row in range(3):
        for col in range(3):
            target = QRectF(QPointF(xs[col], ys[row]), QPointF(xs[col + 1], ys[row + 1]))
            if target.width() <= 0 or target.height() <= 0:
                continue
            source = QRectF(QPointF(sx[col], sy[row]), QPointF(sx[col + 1], sy[row + 1]))
            painter.drawPixmap(target, pix, source)
    painter.restore()


def _paint_inset(painter: QPainter, rect: QRectF, box: QPainterPath, e: Elevation) -> None:
    """`inset 0 2px 5px`: the top of a well in shade, fading within a few px."""
    sigma = e.blur / 2
    depth = e.dy + 3 * sigma
    grad = QLinearGradient(rect.topLeft(), rect.topLeft() + QPointF(0, depth))
    shade = QColor(0, 0, 0)
    for step in range(6):
        y = depth * step / 5
        shade.setAlphaF(e.alpha * _phi((e.dy - y) / sigma))
        grad.setColorAt(step / 5, shade)
    painter.setClipPath(box)
    painter.fillRect(QRectF(rect.left(), rect.top(), rect.width(), min(depth, rect.height())),
                     QBrush(grad))


def paint_sheen(painter: QPainter, rect, radius: float, elev: str = "elev.1") -> None:
    """The inset top light of a raised box: a 1 px line just inside its top edge."""
    e = _elevation(elev)
    if not e.sheen:
        return
    rect = QRectF(rect)
    radius = min(radius, rect.height() / 2)
    box = QPainterPath()
    box.addRoundedRect(rect, radius, radius)
    painter.save()
    painter.setClipPath(box)
    painter.fillRect(QRectF(rect.left(), rect.top(), rect.width(), 1.0),
                     QColor(255, 255, 255, round(255 * e.sheen)))
    painter.restore()


# The knob of a Toggle: `0 1px 2px rgba(0,0,0,.5)` under a 12 px disc.
KNOB_SHADOW = Elevation(1, 2, 0.5)


def paint_disc_shadow(painter: QPainter, centre: QPointF, radius: float,
                      e: Elevation = KNOB_SHADOW) -> None:
    """A soft shadow under a disc. The nine-slice box shadow would show its
    square corners round something this small, so this one is radial."""
    sigma = e.blur / 2
    reach = radius + 3 * sigma
    c = QPointF(centre.x(), centre.y() + e.dy)
    grad = QRadialGradient(c, reach)
    shade = QColor(0, 0, 0)
    for step in range(9):
        r = reach * step / 8
        shade.setAlphaF(e.alpha * _phi((radius - r) / sigma))
        grad.setColorAt(step / 8, shade)
    painter.save()
    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(grad))
    painter.drawEllipse(c, reach, reach)
    painter.restore()


# The focus ring: `0 0 0 2px focus.ring` round the box, keyboard focus only.
FOCUS_RING = 2


def paint_focus_ring(painter: QPainter, rect, radius: float) -> None:
    """The ring round a box with keyboard focus, outside it, following its corners."""
    rect = QRectF(rect)
    radius = min(radius, rect.height() / 2)
    inner = QPainterPath()
    inner.addRoundedRect(rect, radius, radius)
    grown = rect.adjusted(-FOCUS_RING, -FOCUS_RING, FOCUS_RING, FOCUS_RING)
    outer = QPainterPath()
    outer.addRoundedRect(grown, radius + FOCUS_RING, radius + FOCUS_RING)
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)
    painter.fillPath(outer.subtracted(inner), color("focus.ring"))
    painter.restore()


# A disabled control is drawn at this opacity, sheen and shadow dropped. Fields
# are a shade stronger, so the text in them stays legible.
DISABLED_OPACITY = 0.4
DISABLED_FIELD_OPACITY = 0.45


# ---- Component metrics -------------------------------------------------------
#
# The design system's component sizes, in CSS px = Qt logical px, so no kit
# module carries a number of its own. The design draws most controls with CSS
# `content-box` sizing, so a bordered box is 2 px bigger than its padding says;
# these are the outer sizes, border included.

# A single-line control — a medium button, a field, a Dropdown — is this tall,
# so a toolbar of them lines up.
CONTROL_HEIGHT = 30


@dataclass(frozen=True)
class ButtonSize:
    height: int          # outer, border included
    pad: int             # left and right, inside the 1 px border
    font: str            # type token
    icon: int            # leading icon, px
    gap: int             # between icon, text and key cap


BUTTON: dict[str, ButtonSize] = {
    # inline actions in a card header ("Open log"): padding 6/10 around 11 px
    "sm": ButtonSize(26, 9, "type.label", 13, 6),
    # the default: padding 7/13 around 11.5 px
    "md": ButtonSize(CONTROL_HEIGHT, 12, "type.bodySm", 14, 7),
    # an empty state's one action ("Choose folders"): padding 9/18 around 12 px
    "lg": ButtonSize(34, 17, "type.body", 16, 7),
}

# IconButton: (side, glyph) — 30 px in toolbars, 22 px in a Pagination
ICON_BUTTON: dict[str, tuple[int, int]] = {"md": (30, 16), "sm": (22, 13)}

KEY_CAP_PAD = 4          # the "Ctrl+V" cap inside a button: padding 0 4px, r.sm

CHECK_BOX = 17           # 15 px inside a 1 px border
CHECK_GAP = 8            # box to label
TOGGLE_TRACK = (36, 20)  # 34 × 18 inside a 1 px border
TOGGLE_KNOB = 12
TOGGLE_INSET = 3         # knob to track, inside the border
TOGGLE_GAP = 9           # track to label
SEGMENT_HEIGHT = 26      # padding 5/11 around 11 px, and the border
SEGMENT_PAD = 11
PAGE_GAP = 3             # between a Pagination's cells
PAGE_PAD = 5             # a page number's padding, in a 22 px cell

CHIP_HEIGHT = 18         # padding 2 around 10 px mono, and the border
CHIP_PAD = 8
CHIP_GAP = 5             # leading dot or glyph to the label
CHIP_DOT = 5
CHIP_GLYPH = 10
CHIP_DASH = (3.0, 2.0)   # the Unidentified chip's dashed edge, in pen widths

FIELD_PAD = 10           # TextInput and Dropdown: padding 7/10
FIELD_ICON = 13          # a field's leading search icon, and the chevron
FIELD_ICON_GAP = 7
FIELD_ERROR_GAP = 5      # the box to its error line
FIELD_ERROR_HEIGHT = FIELD_ERROR_GAP + math.ceil(line_height("type.label"))
POPUP_GAP = 4            # a Dropdown's box to its popup
POPUP_PAD = 4
POPUP_ROW = (6, 8)       # a row's padding, vertical and horizontal
POPUP_CHECK = 12         # the selected row's check

PANEL_PADDING: dict[str, tuple[int, int]] = {   # (vertical, horizontal)
    "none": (0, 0),
    "sm": (12, 13),
    "md": (13, 14),
    "lg": (14, 15),
}
CALLOUT_PAD = (9, 11)
CALLOUT_ICON = 14
CALLOUT_GAP = 9
METRIC_PAD = 11          # each value's side padding, either side of a divider
METRIC_RULE_PAD = 10     # above and below the values, inside the rules
METRIC_CAPTION_GAP = 4


# ---- Semantic colours the old tabs ask for ---------------------------------

def status_color(kind: str) -> str:
    """Colour for a card border or a status line: ok / bad / done / muted."""
    return css({"ok": "ok", "bad": "danger", "done": "accent",
                "muted": "text.mid"}.get(kind, "text.body"))


def kind_color(kind: str) -> str:
    """Colour for a wallpaper's kind — Scene, Video, Web, Application, Preset.

    Anything else falls back to the neutral chip colour. A card draws no chip
    at all for an item Steam never tagged: an empty space says "unknown" as
    well as the word would, and more quietly.
    """
    token = f"kind.{(kind or '').casefold()}"
    return css(token if token in TOKENS else "surface.raised")


def level_color(level: str) -> str:
    """Colour for a log line by its severity."""
    return css({"INFO": "console.text", "WARN": "console.warn",
                "ERROR": "console.err"}.get(level, "console.text"))


# ---- Qt palette -----------------------------------------------------------

def _palette() -> QPalette:
    """What Qt paints without consulting the stylesheet.

    Palette colours end up on opaque grounds Qt fills itself, so the
    translucent tokens are composited over `bg.solid` first.
    """
    def solid(token: str) -> QColor:
        return composite(token, "bg.solid")

    p = QPalette()
    p.setColor(QPalette.Window, color("bg.solid"))
    p.setColor(QPalette.WindowText, color("text.body"))
    p.setColor(QPalette.Base, solid("surface.well"))
    p.setColor(QPalette.AlternateBase, solid("surface.zebra"))
    p.setColor(QPalette.Text, color("text.body"))
    p.setColor(QPalette.PlaceholderText, color("text.lo"))
    p.setColor(QPalette.Button, solid("surface.wash"))
    p.setColor(QPalette.ButtonText, color("text.body"))
    p.setColor(QPalette.BrightText, color("danger"))
    p.setColor(QPalette.Highlight, color("accent"))
    p.setColor(QPalette.HighlightedText, color("text.onAccent"))
    p.setColor(QPalette.Link, color("accent.hover"))
    p.setColor(QPalette.LinkVisited, color("accent.hover"))
    p.setColor(QPalette.ToolTipBase, solid("surface.popup"))
    p.setColor(QPalette.ToolTipText, color("text.body"))
    p.setColor(QPalette.Light, solid("border.strong"))
    p.setColor(QPalette.Midlight, solid("border.control"))
    p.setColor(QPalette.Mid, solid("border.hairline"))
    p.setColor(QPalette.Dark, solid("surface.well"))
    p.setColor(QPalette.Shadow, QColor(0, 0, 0))

    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, solid("text.disabled"))
    return p


# ---- Stylesheet -----------------------------------------------------------
#
# Written with $(token) placeholders and filled in from the tables above, so a
# value appears once, under its design name. Understood: a colour $(text.lo),
# a colour at part of its opacity $(accent/.3), a radius $(r.md) or space
# $(sp.8), a type style $(font:type.bodySm), a gradient $(grad:surface.glass),
# a component size $(px:field.error), and a drawing in a colour
# $(img:check/text.onAccent), with an optional stroke width after a third
# slash. Anything left unfilled is a typo, and test_theme.py fails on it.

_TEMPLATE = """
* { outline: none; }

QWidget {
    background: transparent;
    color: $(text.body);
}
QMainWindow, QDialog, QMessageBox { background: $(bg.solid); }

/* ---- the old tab strip, until the sidebar replaces it ---- */
QTabWidget::pane {
    border: none;
    border-top: 1px solid $(border.hairline);
    background: transparent;
    top: -1px;
}
QTabBar { qproperty-drawBase: 0; }
QTabBar::tab {
    background: transparent;
    color: $(text.mid);
    border: 1px solid transparent;
    border-radius: $(r.row)px;
    padding: 6px 14px;
    margin: 6px 4px 6px 0;
    $(font:type.bodySm)
    font-weight: 600;
}
QTabBar::tab:hover { background: $(surface.wash); color: $(text.body); }
QTabBar::tab:selected {
    background: $(nav.selected);
    border-color: $(nav.selectedSheen);
    color: $(text.hi);
}

/* ---- panels ---- */
QGroupBox {
    background: $(grad:surface.glass);
    border: 1px solid $(border.hairline);
    border-radius: $(r.lg)px;
    margin-top: 24px;
    padding: 14px 12px 12px 12px;
    $(font:type.h3)
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 2px;
    padding: 0 2px 6px 2px;
    color: $(text.mid);
}

/* ---- buttons: the design's SecondaryButton, and AccentButton ---- */
QPushButton {
    background: $(surface.wash);
    border: 1px solid $(border.control);
    border-radius: $(r.md)px;
    padding: 6px 13px;
    color: $(text.body);
    $(font:type.bodySm)
}
QPushButton:hover { background: $(surface.washHover); border-color: $(border.strong); }
QPushButton:pressed { background: $(surface.washPress); border-color: $(border.strong); }
QPushButton:disabled {
    background: $(surface.washDisabled);
    border-color: $(border.control);
    color: $(text.disabled);
}
QPushButton[accent="true"] {
    background: $(accent);
    border-color: $(accent);
    color: $(text.onAccent);
}
QPushButton[accent="true"]:hover {
    background: $(accent.hover); border-color: $(accent.hover);
}
QPushButton[accent="true"]:pressed {
    background: $(accent.press); border-color: $(accent.press);
}
QPushButton[accent="true"]:disabled {
    background: $(accent/.3);
    border-color: transparent;
    color: $(text.onAccent/.5);
}
QToolButton {
    background: transparent;
    border: 1px solid transparent;
    border-radius: $(r.md)px;
    padding: 4px;
    color: $(text.mid);
}
QToolButton:hover { background: $(surface.raised); color: $(text.hi); }
QToolButton:pressed { background: $(surface.press); }

/* ---- text, number and choice inputs ---- */
QLineEdit, QAbstractSpinBox, QComboBox, QPlainTextEdit, QTextEdit {
    background: $(surface.well);
    border: 1px solid $(border.control);
    border-radius: $(r.md)px;
    padding: 6px 10px;
    color: $(text.body);
    selection-background-color: $(console.selection);
    selection-color: $(text.hi);
    $(font:type.bodySm)
}
QLineEdit:hover, QAbstractSpinBox:hover, QComboBox:hover {
    border-color: $(border.strong);
}
QLineEdit:focus, QAbstractSpinBox:focus, QComboBox:focus, QComboBox:on,
QPlainTextEdit:focus, QTextEdit:focus {
    border-color: $(border.focus);
}
QLineEdit:disabled, QAbstractSpinBox:disabled, QComboBox:disabled {
    background: $(surface.subtle);
    border-color: $(border.hairline);
    color: $(text.disabled);
}

/* Numbers are mono, so they are tabular; the arrows are a column of their own. */
QAbstractSpinBox {
    padding: 5px 30px 5px 10px;
    $(font:type.mono)
    font-size: 9pt;
}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {
    subcontrol-origin: border;
    width: 20px;
    background: transparent;
    border: none;
    border-left: 1px solid $(border.hairline);
}
QAbstractSpinBox::up-button {
    subcontrol-position: top right;
    border-bottom: 1px solid $(border.hairline);
    border-top-right-radius: $(r.md)px;
}
QAbstractSpinBox::down-button {
    subcontrol-position: bottom right;
    border-bottom-right-radius: $(r.md)px;
}
QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover {
    background: $(surface.raised);
}
QAbstractSpinBox::up-button:pressed, QAbstractSpinBox::down-button:pressed {
    background: $(surface.press);
}
QAbstractSpinBox::up-arrow {
    image: url("$(img:chevU/text.mid/2)");
    width: 9px; height: 9px;
}
QAbstractSpinBox::down-arrow {
    image: url("$(img:chevD/text.mid/2)");
    width: 9px; height: 9px;
}
QAbstractSpinBox::up-arrow:disabled, QAbstractSpinBox::up-arrow:off {
    image: url("$(img:chevU/text.disabled/2)");
}
QAbstractSpinBox::down-arrow:disabled, QAbstractSpinBox::down-arrow:off {
    image: url("$(img:chevD/text.disabled/2)");
}

/* The popup opens below the box as a list, as the design's Dropdown does,
   rather than over it as a menu: a list's rows take the rules below, where a
   menu's would be drawn by Fusion with its own outline. */
QComboBox { padding-right: 28px; combobox-popup: 0; }
QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 26px;
    border: none;
}
QComboBox::down-arrow {
    image: url("$(img:chevD/text.mid)");
    width: 13px; height: 13px;
}
QComboBox::down-arrow:disabled { image: url("$(img:chevD/text.disabled)"); }
QComboBox QAbstractItemView {
    background: $(surface.popup);
    border: 1px solid $(border.control);
    padding: 4px;
    outline: none;
    color: $(text.body);
    selection-background-color: $(accent.soft);
    selection-color: $(text.hi);
}
QComboBox QAbstractItemView::item {
    padding: 6px 8px;
    border: none;
    border-radius: 5px;
    min-height: 16px;
}
QComboBox QAbstractItemView::item:hover { background: $(surface.raised); }
QComboBox QAbstractItemView::item:selected { background: $(accent.soft); color: $(text.hi); }

/* ---- check boxes: the tick is drawn, and instant ----
   The same box in a list or tree row (the cleanup dialog ticks its folders
   there): left to Fusion, an unticked row draws no box at all on this ground. */
QCheckBox { spacing: 8px; }
QCheckBox:disabled { color: $(text.disabled); }
QCheckBox::indicator, QAbstractItemView::indicator {
    /* 15 px inside the border, as the design draws it: a 9 px tick and 3 px
       of padding round it (QSS counts width without the padding) */
    width: 9px; height: 9px;
    padding: 3px;
    border: 1px solid $(border.control);
    border-radius: $(r.sm)px;
    background: transparent;
}
QCheckBox::indicator:hover, QAbstractItemView::indicator:hover {
    background: $(surface.raised); border-color: $(border.strong);
}
QCheckBox::indicator:checked, QCheckBox::indicator:indeterminate,
QAbstractItemView::indicator:checked, QAbstractItemView::indicator:indeterminate {
    background: $(accent);
    border-color: $(accent);
}
QCheckBox::indicator:checked, QAbstractItemView::indicator:checked {
    image: url("$(img:check/text.onAccent/2.4)");
}
QCheckBox::indicator:indeterminate, QAbstractItemView::indicator:indeterminate {
    image: url("$(img:dash/text.onAccent)");
}
QCheckBox::indicator:checked:hover, QCheckBox::indicator:indeterminate:hover,
QAbstractItemView::indicator:checked:hover, QAbstractItemView::indicator:indeterminate:hover {
    background: $(accent.hover); border-color: $(accent.hover);
}
QCheckBox::indicator:disabled { border-color: $(border.hairline); background: transparent; }
QCheckBox::indicator:checked:disabled, QCheckBox::indicator:indeterminate:disabled {
    background: $(accent/.4); border-color: transparent;
}

/* ---- lists, trees and tables ---- */
QAbstractItemView {
    background: $(surface.subtle);
    alternate-background-color: $(surface.zebra);
    border: 1px solid $(border.hairline);
    border-radius: $(r.md)px;
    selection-background-color: $(accent.soft);
    selection-color: $(text.hi);
}
/* Rows are painted here rather than from the palette, which holds opaque
   colours for what Qt fills itself: a table with alternating rows would
   otherwise lay solid bands over the glass. */
QAbstractItemView::item { padding: 3px 8px; border: none; background: transparent; }
QAbstractItemView::item:alternate { background: $(surface.zebra); }
QAbstractItemView::item:hover { background: $(surface.raised); }
QAbstractItemView::item:selected { background: $(accent.soft); color: $(text.hi); }
QTableView { gridline-color: $(border.hairline); }
QHeaderView { background: transparent; border: none; }
QHeaderView::section {
    background: $(surface.header);
    color: $(text.lo);
    border: none;
    border-bottom: 1px solid $(border.hairline);
    padding: 7px 12px;
    $(font:type.overline)
}
QHeaderView::section:hover { color: $(text.body); }
QHeaderView::up-arrow { image: url("$(img:sortUp/accent)"); width: 11px; height: 11px; }
QHeaderView::down-arrow { image: url("$(img:chevD/accent)"); width: 11px; height: 11px; }
QTableCornerButton::section { background: $(surface.header); border: none; }

/* ---- progress ---- */
QProgressBar {
    background: $(surface.well);
    border: none;
    border-radius: $(r.sm)px;
    min-height: 8px;
    text-align: center;
    color: $(text.hi);
    $(font:type.monoSm)
}
QProgressBar::chunk { background: $(accent); border-radius: $(r.sm)px; }

/* ---- scroll bars: a thin handle, no arrows ---- */
QScrollBar:vertical, QScrollBar:horizontal {
    background: transparent; border: none; margin: 0;
}
QScrollBar:vertical { width: 10px; }
QScrollBar:horizontal { height: 10px; }
QScrollBar::handle {
    background: $(surface.raisedHi);
    border: 2px solid transparent;
    border-radius: 5px;
    background-clip: padding;
}
QScrollBar::handle:hover { background: $(border.strong); }
QScrollBar::handle:pressed { background: $(text.lo); }
QScrollBar::handle:vertical { min-height: 30px; }
QScrollBar::handle:horizontal { min-width: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; border: none; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

/* ---- menus and tool tips: the popup surface ---- */
QMenu {
    background: $(surface.popup);
    border: 1px solid $(border.control);
    padding: 4px;
    color: $(text.body);
    $(font:type.bodySm)
}
QMenu::item { padding: 6px 22px 6px 10px; border-radius: 5px; }
QMenu::item:selected { background: $(surface.raised); color: $(text.hi); }
QMenu::item:disabled { color: $(text.disabled); }
QMenu::separator { height: 1px; background: $(border.hairline); margin: 4px 6px; }
QToolTip {
    background: $(surface.popup);
    color: $(text.body);
    border: 1px solid $(border.control);
    padding: 5px 8px;
    $(font:type.label)
}

/* ---- misc chrome ---- */
QSplitter::handle { background: transparent; }
QSplitter::handle:horizontal { width: 10px; }
QSplitter::handle:vertical { height: 10px; }
QScrollArea { border: none; background: transparent; }

/* ---- the kit's fields and check box (app/ui/kit) ----
   They are Qt's own controls under the rules above; these add what the kit
   classes know and Qt does not. [forceState] draws a state without a pointer,
   for the kit preview. A field in error keeps its red edge under the pointer
   and in focus, and makes room below itself for the message. */
TextInput[forceState="hover"], SpinBox[forceState="hover"], Dropdown[forceState="hover"] {
    border-color: $(border.strong);
}
TextInput[forceState="focus"], SpinBox[forceState="focus"] { border-color: $(border.focus); }
TextInput[error="true"], TextInput[error="true"]:hover, TextInput[error="true"]:focus {
    border-color: $(danger);
}
TextInput[error="true"] { margin-bottom: $(px:field.error)px; }
/* A Dropdown shows focus when the keyboard brought it there, as a button does,
   and while its list is open; the class sets focusVisible for both. */
Dropdown:focus { border-color: $(border.control); }
Dropdown:focus:hover { border-color: $(border.strong); }
Dropdown[focusVisible="true"], Dropdown[forceState="focus"] { border-color: $(border.focus); }
/* its list paints its own rows, on its own popup ground */
DropdownPopup QListView { background: transparent; border: none; padding: 0; }

/* Kit labels name their colour, so no label carries a stylesheet of its own. */
QLabel[tone="hi"] { color: $(text.hi); }
QLabel[tone="body"] { color: $(text.body); }
QLabel[tone="mid"] { color: $(text.mid); }
QLabel[tone="lo"] { color: $(text.lo); }
QLabel[tone="ok"] { color: $(ok); }
QLabel[tone="warn"] { color: $(warn); }
QLabel[tone="danger"] { color: $(danger); }
QLabel[tone="info"] { color: $(info); }
QLabel[tone="accent"] { color: $(accent.hover); }
QLabel[tone]:disabled { color: $(text.disabled); }

Checkbox { $(font:type.bodySm) spacing: $(px:check.gap)px; }
Checkbox[forceState="hover"]::indicator {
    background: $(surface.raised); border-color: $(border.strong);
}
Checkbox[forceState="hover"]::indicator:checked,
Checkbox[forceState="hover"]::indicator:indeterminate {
    background: $(accent.hover); border-color: $(accent.hover);
}
"""

# Sizes the stylesheet takes from the component metrics, as $(px:name).
_QSS_PX = {
    "field.error": FIELD_ERROR_HEIGHT,
    "check.gap": CHECK_GAP,
}

_PLACEHOLDER = re.compile(r"\$\(([^()]*)\)")

# Drawings the stylesheet needs that are not icons: the indeterminate check.
_QSS_SHAPES = {
    "dash": '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" '
            'viewBox="0 0 16 16"><rect x="2" y="6.2" width="12" height="3.6" '
            'rx="1.8" fill="currentColor"/></svg>',
}


def _asset_dir() -> Path:
    """Where the stylesheet's images go. The drawings live in Python (nothing
    to bundle), but a stylesheet only takes a file; the temp folder is fine for
    files rewritten at every start."""
    return Path(tempfile.gettempdir()) / "WallpaperEngineToolkit" / "qss"


def _asset(spec: str, folder: Path) -> str:
    """`check/text.onAccent/2.4` → a file holding that drawing, in that colour.

    Each file is named after its contents, so the window and a tray of another
    version, running side by side, never overwrite each other's drawings.
    """
    from .ui.kit import icons

    parts = spec.split("/")
    name, token = parts[0], parts[1]
    stroke = float(parts[2]) if len(parts) > 2 else None
    c = color(token)
    if name in _QSS_SHAPES:
        text = _QSS_SHAPES[name].replace("currentColor", c.name(QColor.HexRgb))
        if c.alpha() < 255:
            text = text.replace("<rect ", f'<rect fill-opacity="{c.alphaF():.3f}" ')
    else:
        text = icons.svg(name, c, stroke=stroke)
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]
    path = folder / f"{name}-{digest}.svg"
    try:
        if not path.exists():
            folder.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    except OSError:
        pass            # a missing tick is not worth refusing to start over
    return path.as_posix()


def _qss_gradient(name: str) -> str:
    stops = GRADIENTS[name]
    if name == "surface.glass":
        rad = math.radians(GLASS_ANGLE)
        dx, dy = math.sin(rad) / 2, -math.cos(rad) / 2
        coords = f"x1:{0.5 - dx:.3f}, y1:{0.5 - dy:.3f}, x2:{0.5 + dx:.3f}, y2:{0.5 + dy:.3f}"
    else:
        coords = "x1:0, y1:0, x2:0, y2:1"
    parts = ", ".join(f"stop:{pos:g} {parse(value).name(QColor.HexArgb).upper()}"
                      for pos, value in stops)
    return f"qlineargradient({coords}, {parts})"


def stylesheet(asset_dir: Path | None = None) -> str:
    """The stylesheet for every standard control, filled in from the tokens."""
    folder = asset_dir or _asset_dir()

    def fill(match: re.Match) -> str:
        key = match.group(1)
        try:
            if key.startswith("font:"):
                return qss_font(key[5:])
            if key.startswith("img:"):
                return _asset(key[4:], folder)
            if key.startswith("grad:"):
                return _qss_gradient(key[5:])
            if key.startswith("px:"):
                return str(_QSS_PX[key[3:]])
            if key in RADIUS:
                return str(RADIUS[key])
            if key in SPACING:
                return str(SPACING[key])
            if "/" in key:
                token, alpha = key.split("/")
                return css(token, float(alpha))
            return css(key)
        except (KeyError, ValueError):
            return match.group(0)      # left in place for the test to find

    return _PLACEHOLDER.sub(fill, _TEMPLATE)


def unresolved(qss: str) -> list[str]:
    """Placeholders a stylesheet still carries — each one a misspelt token."""
    return _PLACEHOLDER.findall(qss)


# ---- Fragments for widgets that stay hand-styled -----------------------------

def console_style() -> str:
    """The log panels, which are deliberately a terminal rather than a page."""
    return (f"background: {css('surface.console')}; color: {css('console.text')}; "
            f"border: 1px solid {css('border.hairline')}; border-radius: {R_MD}px; "
            f"padding: 8px; {qss_font('type.mono')}")


def card_style(object_name: str, color_: str) -> str:
    """A preview card, tinted by whether its item is ready, skipped or built.

    The hover rule is what makes a wall of cards feel alive under the cursor;
    Qt applies it instantly, which is the right speed for pointer feedback.
    """
    return (f"#{object_name} {{ background: {css('surface.wash')}; "
            f"border: 1px solid {css('border.hairline')}; border-left: 3px solid {color_}; "
            f"border-radius: {R_LG}px; }}"
            f"#{object_name}:hover {{ background: {css('surface.raised')}; "
            f"border-color: {css('border.strong')}; border-left-color: {color_}; }}")


def make_accent(button) -> None:
    """Mark the one button that starts the work, so it stands out from Cancel."""
    button.setProperty("accent", True)
    # A property set after the widget exists needs the style re-evaluated.
    button.style().unpolish(button)
    button.style().polish(button)


def label_style(kind: str = "muted", size: int | None = None,
                weight: int | None = None) -> str:
    """A secondary text line: muted / faint / ok / warn / danger / accent.

    `size` is in px, as the callers have always written it.
    """
    token = {
        "muted": "text.mid", "faint": "text.lo", "text": "text.body",
        "ok": "ok", "warn": "warn", "danger": "danger", "accent": "accent",
    }.get(kind, "text.mid")
    parts = [f"color: {css(token)};", "background: transparent;"]
    if size:
        parts.append(f"font-size: {point_size(size):g}pt;")
    if weight:
        parts.append(f"font-weight: {weight};")
    return " ".join(parts)


# ---- Applying it ----------------------------------------------------------

def icon_path() -> Path:
    """assets/icon.ico, both from source and from inside the bundled exe."""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        base = Path(__file__).resolve().parent.parent
    return base / "assets" / "icon.ico"


def app_icon() -> QIcon:
    path = icon_path()
    return QIcon(str(path)) if path.exists() else QIcon()


def apply(app: QApplication) -> None:
    """Paint the whole application. Call once, before building any window.

    Also reads Windows' own animation switch: with animations off there, the
    app's motion is off too.
    """
    from . import animations

    animations.ENABLED = not animations.reduced_motion()

    app.setStyle("Fusion")
    app.setPalette(_palette())
    app.setFont(font("type.body"))
    app.setStyleSheet(stylesheet())
    app.setWindowIcon(app_icon())
