"""The tray's menu: a header that says what the icon says, and five rows.

The design (`Tray and Notifications`) draws a popup on `surface.overlay`: the
icon (the mark, filled as it is in the tray) and "Toolkit" with one line in
words ("tracking · 4 of 201 shown", which the icon does not say), then

    Open Toolkit                  Enter
    ───────────────────────────────────
    Rotate now…                  run 39
    Review                   12 waiting
    ───────────────────────────────────
    Settings
    Quit

Only "Rotate now…" leads to anything being changed, and the window still asks
before it does (see `MainWindow.handle_command`). The words are `tray_words`'s;
this module is the shape.

**A QMenu that paints itself.** A native or QSS-styled menu cannot put a mono
hint at the right of a row, or the tray's icon in its header. The menu is a `QMenu`
still, so Windows' ways with a tray menu (it closes when the shell is clicked,
the arrow keys, Enter, Escape) are Qt's own; it only draws its own rows, in
`paintEvent`, and tells Qt how tall they are through `_MenuStyle`. The tray
process runs without the stylesheet (`theme.apply(app, styled=False)`), so
nothing competes with it.

The icons are the kit's, embedded here as SVG strings: importing
`app.ui.kit.icons` would load the whole kit, and the tray is a process that
sits at below-normal priority all day. `tests/test_tray_icon.py` fails if one
of them drifts from the kit's drawing.
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetricsF, QPainter, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QMenu, QProxyStyle, QStyle, QStyleOptionMenuItem

from . import theme
from .branding import DISPLAY_NAME
from .tray_icon import mark_pixmap

# ---- what the menu says ------------------------------------------------------------

OPEN, ROTATE, REVIEW, SETTINGS, QUIT = "open", "rotate", "review", "settings", "quit"
HEADER = "header"           # the data of the header's action: it is not a row


@dataclass(frozen=True)
class Row:
    key: str
    label: str
    hint: str = ""
    icon: str = ""          # a name in ICON_BODIES


@dataclass(frozen=True)
class MenuModel:
    """What a menu shows: the icon's state and words, and the rows in groups."""
    state: str
    fraction: float | None      # the icon's fill
    number: int | None
    title: str
    line: str
    groups: tuple[tuple[Row, ...], ...]

    def rows(self) -> list[Row]:
        return [row for group in self.groups for row in group]

    def keys(self) -> list[str]:
        return [row.key for row in self.rows()]


def build_model(state: str, fraction: float | None, number: int | None, line: str, *,
                run_hint: str = "", review_hint: str = "") -> MenuModel:
    """The menu for one state of the icon. The rows are the same in every state;
    what changes is the header (its icon and its line) and the two hints."""
    return MenuModel(
        state, fraction, number, DISPLAY_NAME, line,
        ((Row(OPEN, f"Open {DISPLAY_NAME}", "Enter", "review"),),
         (Row(ROTATE, "Rotate now…", run_hint, "rotator"),
          Row(REVIEW, "Review", review_hint, "review")),
         (Row(SETTINGS, "Settings", "", "settings"),
          Row(QUIT, "Quit", "", "close"))))


# ---- the icons ---------------------------------------------------------------------

_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16" '
        'fill="none" stroke="currentColor" stroke-width="1.3">{}</svg>')

# The kit's drawings of these four (app/ui/kit/icons.py), word for word.
ICON_BODIES: dict[str, str] = {
    "rotator": '<circle cx="8" cy="8" r="6"></circle><circle cx="8" cy="2" r="1.4" fill="currentColor" stroke="none"></circle>',
    "review": '<rect x="1.5" y="2.5" width="13" height="11"></rect><line x1="1.5" y1="6" x2="14.5" y2="6"></line>',
    "settings": '<circle cx="8" cy="8" r="6"></circle><circle cx="8" cy="8" r="2"></circle>',
    "close": '<line x1="3.5" y1="3.5" x2="12.5" y2="12.5"></line><line x1="12.5" y1="3.5" x2="3.5" y2="12.5"></line>',
}

_pixmaps: dict[tuple, QPixmap] = {}


def icon_pixmap(name: str, colour: QColor, size: int, dpr: float = 1.0) -> QPixmap:
    """A menu icon in a colour, `size` logical pixels square, cached."""
    key = (name, colour.name(), size, dpr)
    pixmap = _pixmaps.get(key)
    if pixmap is None:
        svg = _SVG.format(ICON_BODIES[name]).replace("currentColor", colour.name())
        edge = max(1, round(size * dpr))
        pixmap = QPixmap(edge, edge)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        QSvgRenderer(QByteArray(svg.encode("utf-8"))).render(painter, QRectF(0, 0, edge, edge))
        painter.end()
        pixmap.setDevicePixelRatio(dpr)
        _pixmaps[key] = pixmap
    return pixmap


# ---- the shape ----------------------------------------------------------------------

class _MenuStyle(QProxyStyle):
    """Fusion, except that the menu's rows have the heights of the design's.

    A QMenu asks its style how big each item is and how wide its frame is.
    The header is told apart from a row by being checkable (nothing else in
    this menu is): the option carries no more than that.
    """

    def __init__(self):
        super().__init__("Fusion")

    def pixelMetric(self, metric, option=None, widget=None):      # noqa: N802 - Qt's name
        if metric == QStyle.PM_MenuPanelWidth:
            return 1
        if metric in (QStyle.PM_MenuHMargin, QStyle.PM_MenuVMargin):
            return theme.TRAY_MENU_PAD - 1
        if metric in (QStyle.PM_MenuDesktopFrameWidth, QStyle.PM_MenuTearoffHeight):
            return 0
        return super().pixelMetric(metric, option, widget)

    def sizeFromContents(self, contents, option, size, widget=None):   # noqa: N802
        if contents == QStyle.CT_MenuItem:
            width = theme.TRAY_MENU_WIDTH - 2 * theme.TRAY_MENU_PAD
            if option.menuItemType == QStyleOptionMenuItem.MenuItemType.Separator:
                return QSize(width, theme.TRAY_DIVIDER_HEIGHT)
            if option.checkType != QStyleOptionMenuItem.CheckType.NotCheckable:
                return QSize(width, theme.TRAY_HEADER_HEIGHT)
            return QSize(width, theme.TRAY_ROW_HEIGHT)
        return super().sizeFromContents(contents, option, size, widget)


class TrayMenu(QMenu):
    """The menu. `set_model` fills it; `chosen(key)` says which row was taken."""

    chosen = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._style = _MenuStyle()                  # a widget does not own its style
        self.setStyle(self._style)
        self.setWindowFlags(self.windowFlags() | Qt.FramelessWindowHint
                            | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMinimumWidth(theme.TRAY_MENU_WIDTH)
        self._model: MenuModel | None = None
        self._rows: dict[str, Row] = {}
        self._open = None
        self.triggered.connect(self._triggered)

    def model(self) -> MenuModel | None:
        return self._model

    def set_model(self, model: MenuModel) -> None:
        self.clear()
        self._model = model
        self._open = None
        self._rows = {row.key: row for row in model.rows()}
        header = self.addAction(model.title)
        header.setCheckable(True)                   # how the style knows it is the header
        header.setEnabled(False)
        header.setData(HEADER)
        for group in model.groups:
            self.addSeparator()
            for row in group:
                action = self.addAction(row.label)
                action.setData(row.key)
                if row.key == OPEN:
                    self._open = action

    def texts(self) -> list[str]:
        """Every row as the menu draws it, for a test: `Rotate now…  ·  run 39`."""
        found = []
        for action in self.actions():
            if action.isSeparator():
                found.append("-")
            elif action.data() == HEADER:
                found.append(f"{self._model.title}  ·  {self._model.line}")
            else:
                row = self._rows[action.data()]
                found.append(f"{row.label}  ·  {row.hint}" if row.hint else row.label)
        return found

    def _triggered(self, action) -> None:
        key = action.data()
        if key in self._rows:
            self.chosen.emit(key)

    def showEvent(self, event) -> None:             # noqa: N802 - Qt's name
        super().showEvent(event)
        # "Open Toolkit" is the row Enter takes: it starts highlighted, as the
        # design draws it. Hovering another row moves the highlight as usual.
        QTimer.singleShot(0, self._highlight_open)

    def _highlight_open(self) -> None:
        if self.isVisible() and self._open is not None and self.activeAction() is None:
            self.setActiveAction(self._open)

    # -- painting

    def paintEvent(self, event) -> None:            # noqa: N802 - Qt's name
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        panel = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = theme.TRAY_MENU_RADIUS
        painter.setPen(QPen(theme.color("border.control"), 1))
        painter.setBrush(theme.color("surface.overlay"))
        painter.drawRoundedRect(panel, radius, radius)
        theme.paint_sheen(painter, panel, radius, "elev.3")
        painter.setPen(Qt.NoPen)
        dpr = self.devicePixelRatioF()
        active = self.activeAction()
        for action in self.actions():
            rect = QRectF(self.actionGeometry(action))
            if action.isSeparator():
                self._paint_divider(painter, rect)
            elif action.data() == HEADER:
                self._paint_header(painter, rect, dpr)
            else:
                self._paint_row(painter, rect, self._rows[action.data()], dpr,
                                action is active and action.isEnabled())

    def _paint_divider(self, painter: QPainter, rect: QRectF) -> None:
        inset = theme.TRAY_DIVIDER_INSET - theme.TRAY_MENU_PAD
        y = rect.center().y() - 0.5
        painter.fillRect(QRectF(rect.left() + inset, y, rect.width() - 2 * inset, 1.0),
                         theme.color("border.hairline"))

    def _paint_header(self, painter: QPainter, rect: QRectF, dpr: float) -> None:
        model = self._model
        left, top = theme.TRAY_HEADER_PAD
        ring = theme.TRAY_HEADER_RING
        title, line = theme.font("type.h3"), theme.font("type.monoSm")
        title_height = QFontMetricsF(title).height()
        line_height = QFontMetricsF(line).height()
        block = title_height + theme.TRAY_HEADER_LINE_GAP + line_height
        y = rect.top() + top
        pixmap = mark_pixmap(model.state, max(1, round(ring * dpr)), fill=model.fraction)
        pixmap.setDevicePixelRatio(dpr)
        painter.drawPixmap(int(rect.left() + left), int(y + (block - ring) / 2), pixmap)
        x = rect.left() + left + ring + theme.TRAY_HEADER_GAP
        width = rect.right() - x - left
        painter.setFont(title)
        painter.setPen(theme.color("text.hi"))
        painter.drawText(QRectF(x, y, width, title_height), Qt.AlignLeft | Qt.AlignVCenter,
                         model.title)
        painter.setFont(line)
        painter.setPen(theme.color("text.lo"))
        elided = QFontMetricsF(line).elidedText(model.line, Qt.ElideRight, width)
        painter.drawText(QRectF(x, y + title_height + theme.TRAY_HEADER_LINE_GAP, width,
                                line_height), Qt.AlignLeft | Qt.AlignVCenter, elided)

    def _paint_row(self, painter: QPainter, rect: QRectF, row: Row, dpr: float,
                   hot: bool) -> None:
        if hot:
            painter.setPen(Qt.NoPen)
            painter.setBrush(theme.color("surface.raised"))
            painter.drawRoundedRect(rect, theme.R_MD, theme.R_MD)
            painter.setBrush(Qt.NoBrush)
        x = rect.left() + theme.TRAY_ROW_PAD
        box = theme.TRAY_ICON_BOX
        if row.icon:
            painter.drawPixmap(int(x), int(rect.center().y() - box / 2),
                               icon_pixmap(row.icon, theme.color("text.mid"), box, dpr))
        x += box + theme.TRAY_ROW_GAP
        right = rect.right() - theme.TRAY_ROW_PAD
        label, hint = theme.font("type.body"), theme.font("type.monoSm")
        hint_width = 0.0
        if row.hint:
            painter.setFont(hint)
            painter.setPen(theme.color("text.lo"))
            hint_width = QFontMetricsF(hint).horizontalAdvance(row.hint)
            painter.drawText(QRectF(right - hint_width, rect.top(), hint_width, rect.height()),
                             Qt.AlignRight | Qt.AlignVCenter, row.hint)
        painter.setFont(label)
        painter.setPen(theme.color("text.body"))
        painter.drawText(QRectF(x, rect.top(), right - hint_width - x, rect.height()),
                         Qt.AlignLeft | Qt.AlignVCenter, row.label)
