"""The design's icons, kept as SVG text so there is nothing to bundle or read.

Twenty-six are the design system's set: a 16 px grid, a 1.3 px stroke,
`currentColor`. The rest are the few glyphs the screens draw outside that set
(plus, clipboard, tag, video, folderPlus, sortUp, and the up and left
chevrons), taken from the screens' own markup.

Icons are rendered with QSvgRenderer after `currentColor` is replaced, and
cached by name, colour, size and device pixel ratio: painting an icon is a
pixmap copy, never an SVG parse.
"""
from __future__ import annotations

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from ... import theme

_HEAD = ('<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" '
         'viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3">')

# The inner drawing of each icon; every one shares the head above.
_BODIES: dict[str, str] = {
    # the design system's 26
    "overview": '<rect x="1.5" y="1.5" width="5.5" height="5.5"></rect><rect x="9" y="1.5" width="5.5" height="5.5"></rect><rect x="1.5" y="9" width="5.5" height="5.5"></rect><rect x="9" y="9" width="5.5" height="5.5"></rect>',
    "rotator": '<circle cx="8" cy="8" r="6"></circle><circle cx="8" cy="2" r="1.4" fill="currentColor" stroke="none"></circle>',
    "tracker": '<circle cx="8" cy="8" r="6"></circle><line x1="8" y1="8" x2="8" y2="4.2"></line><line x1="8" y1="8" x2="10.6" y2="9.6"></line>',
    "review": '<rect x="1.5" y="2.5" width="13" height="11"></rect><line x1="1.5" y1="6" x2="14.5" y2="6"></line>',
    "creator": '<polygon points="4,2.5 13,8 4,13.5"></polygon>',
    "copier": '<rect x="1.8" y="1.8" width="8.4" height="8.4"></rect><rect x="5.8" y="5.8" width="8.4" height="8.4"></rect>',
    "settings": '<circle cx="8" cy="8" r="6"></circle><circle cx="8" cy="8" r="2"></circle>',
    "folder": '<path d="M1.5 4.2h4.2l1.3 1.6h7.5v7.7h-13z"></path>',
    "search": '<circle cx="7" cy="7" r="4.6"></circle><line x1="10.4" y1="10.4" x2="14" y2="14"></line>',
    "refresh": '<path d="M13.5 8a5.5 5.5 0 1 1-1.9-4.2"></path><polyline points="13.6,1.6 13.6,4.4 10.8,4.4"></polyline>',
    "play": '<polygon points="4,2.5 13,8 4,13.5"></polygon>',
    "pause": '<line x1="5.5" y1="3" x2="5.5" y2="13"></line><line x1="10.5" y1="3" x2="10.5" y2="13"></line>',
    "stop": '<rect x="4" y="4" width="8" height="8"></rect>',
    "check": '<polyline points="2.6,8.4 6.2,12 13.4,3.6"></polyline>',
    "close": '<line x1="3.5" y1="3.5" x2="12.5" y2="12.5"></line><line x1="12.5" y1="3.5" x2="3.5" y2="12.5"></line>',
    "chevD": '<polyline points="3.6,6 8,10.2 12.4,6"></polyline>',
    "chevR": '<polyline points="6,3.6 10.2,8 6,12.4"></polyline>',
    "warn": '<path d="M8 1.8 15 13.8h-14z"></path><line x1="8" y1="6" x2="8" y2="9.4"></line><circle cx="8" cy="11.6" r=".7" fill="currentColor" stroke="none"></circle>',
    "info": '<circle cx="8" cy="8" r="6.3"></circle><line x1="8" y1="7.2" x2="8" y2="11.4"></line><circle cx="8" cy="4.9" r=".75" fill="currentColor" stroke="none"></circle>',
    "ext": '<polyline points="9.4,2.2 13.8,2.2 13.8,6.6"></polyline><line x1="13.8" y1="2.2" x2="7.6" y2="8.4"></line><path d="M11.6 9.6v4.2h-9.4v-9.4h4.2"></path>',
    "trash": '<polyline points="2.6,4 13.4,4"></polyline><path d="M4.2 4v9.4h7.6v-9.4"></path><path d="M6.2 4v-1.8h3.6v1.8"></path>',
    "lock": '<rect x="3.2" y="7" width="9.6" height="7"></rect><path d="M5.6 7v-2.2a2.4 2.4 0 0 1 4.8 0v2.2"></path>',
    "monitor": '<rect x="1.5" y="2.5" width="13" height="9"></rect><line x1="5.5" y1="14" x2="10.5" y2="14"></line>',
    "clock": '<circle cx="8" cy="8" r="6.2"></circle><polyline points="8,4.2 8,8 10.6,9.6"></polyline>',
    "dots": '<circle cx="3" cy="8" r="1.1" fill="currentColor" stroke="none"></circle><circle cx="8" cy="8" r="1.1" fill="currentColor" stroke="none"></circle><circle cx="13" cy="8" r="1.1" fill="currentColor" stroke="none"></circle>',
    "drag": '<line x1="5" y1="4" x2="11" y2="4"></line><line x1="5" y1="8" x2="11" y2="8"></line><line x1="5" y1="12" x2="11" y2="12"></line>',

    # glyphs the screens use outside the set
    "plus": '<line x1="8" y1="3" x2="8" y2="13"></line><line x1="3" y1="8" x2="13" y2="8"></line>',               # Add folders
    "clipboard": '<rect x="3.5" y="3" width="9" height="11.5" rx="1"></rect><rect x="6" y="1.5" width="4" height="3" rx=".6"></rect>',  # Paste path
    "tag": '<path d="M2 2h5.6l6.4 6.4-5.6 5.6-6.4-6.4z"></path><circle cx="5" cy="5" r="1" fill="currentColor" stroke="none"></circle>',  # Add tags
    "video": '<rect x="1.5" y="3.5" width="9.5" height="9"></rect><polygon points="11,6.5 14.5,4.5 14.5,11.5 11,9.5"></polygon>',  # Creator rows
    "folderPlus": '<path d="M1.5 4.2h4.2l1.3 1.6h7.5v7.7h-13z"></path><line x1="8" y1="8" x2="8" y2="12"></line><line x1="6" y1="10" x2="10" y2="10"></line>',  # Creator empty state
    "sortUp": '<polyline points="4.4,9.4 8,5.6 11.6,9.4"></polyline>',       # sorted table header
    "chevU": '<polyline points="3.6,10 8,5.8 12.4,10"></polyline>',          # SpinBox up arrow
    "chevL": '<polyline points="10,3.6 5.8,8 10,12.4"></polyline>',          # Pagination back
}

NAMES: tuple[str, ...] = tuple(_BODIES)


def _resolve(colour) -> QColor:
    return theme.color(colour) if isinstance(colour, str) else QColor(colour)


def svg(name: str, colour="text.body", stroke: float | None = None) -> str:
    """The icon as SVG text, drawn in `colour` (a token name or a QColor).

    `stroke` replaces the 1.3 px stroke, for the few places that draw a glyph
    much smaller than 16 px and need it heavier to survive — the check box's
    tick is the same check at 9 px and 2.4.
    """
    try:
        body = _BODIES[name]
    except KeyError:
        raise KeyError(f"no icon {name!r}") from None
    c = _resolve(colour)
    head = _HEAD
    if stroke is not None:
        head = head.replace('stroke-width="1.3"', f'stroke-width="{stroke:g}"')
    if c.alpha() < 255:
        head = head.replace("<svg ", f'<svg opacity="{c.alphaF():.3f}" ')
    return (head + body + "</svg>").replace("currentColor", c.name(QColor.HexRgb))


_pixmaps: dict[tuple, QPixmap] = {}
_icons: dict[tuple, QIcon] = {}


def pixmap(name: str, colour="text.body", size: int = 16,
           dpr: float | None = None) -> QPixmap:
    """The icon as a `size` × `size` px pixmap at device pixel ratio `dpr`."""
    c = _resolve(colour)
    if dpr is None:
        app = QGuiApplication.instance()
        dpr = app.devicePixelRatio() if app is not None else 1.0
    key = (name, c.rgba(), size, round(dpr, 3))
    cached = _pixmaps.get(key)
    if cached is not None:
        return cached

    renderer = QSvgRenderer(QByteArray(svg(name, c).encode("utf-8")))
    device = max(1, round(size * dpr))
    pix = QPixmap(device, device)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)
    renderer.render(painter, QRectF(0, 0, device, device))
    painter.end()
    pix.setDevicePixelRatio(dpr)
    _pixmaps[key] = pix
    return pix


def icon(name: str, colour="text.body", size: int = 16) -> QIcon:
    """The icon as a QIcon, for buttons and actions.

    It carries a pixmap for every screen's scale, so a window dragged to the
    other monitor stays sharp, and a disabled version at 40 % opacity — the
    design's disabled state — so a disabled button greys its glyph with its
    text.
    """
    c = _resolve(colour)
    key = (name, c.rgba(), size)
    cached = _icons.get(key)
    if cached is not None:
        return cached
    faded = QColor(c)
    faded.setAlphaF(c.alphaF() * 0.4)
    app = QGuiApplication.instance()
    ratios = sorted({round(s.devicePixelRatio(), 3) for s in app.screens()} if app else {1.0})
    result = QIcon()
    for dpr in ratios:
        result.addPixmap(pixmap(name, c, size, dpr), QIcon.Normal)
        result.addPixmap(pixmap(name, faded, size, dpr), QIcon.Disabled)
    _icons[key] = result
    return result
