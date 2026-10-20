"""Tray-shaped or circular desktop readings, owned by the tray.

Only existing readings and asynchronously supplied metadata are painted here.
Qt owns native movement and DPI; positions use each screen's logical work area.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QEvent, QPoint, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QActionGroup, QBrush, QColor, QFont, QFontMetricsF, QIcon, QKeySequence,
                          QLinearGradient, QPainter, QPainterPath, QPen, QShortcut)
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QMenu,
                              QScrollArea, QToolButton, QVBoxLayout, QWidget)

from . import theme, tray_words as words
from .tray_icon import FINISHED, PAUSED, RUNNING, UNKNOWN
from .tray_menu import icon_pixmap
from .widget_desktop import apply_layer
from .widget_options import profile, set_option

WIDTH, HEADER_HEIGHT, ROW_HEIGHT = 380, 44, 104
STATE_COLOUR = {RUNNING: "tray.fill.running", PAUSED: "tray.fill.paused",
                FINISHED: "tray.fill.ok", UNKNOWN: "tray.fill.unknown"}
STATE_TEXT = {RUNNING: "Tracking", PAUSED: "Paused", FINISHED: "Finished", UNKNOWN: "Unknown"}
# Measured on the design mock-ups, in rear-lens widths: the front lens's offset
# and size, both corner radii, and the playlist bar's x, top and bottom.
FRONT_AT, FRONT_SIZE = (.41, .52), (1.12, .83)
BACK_RADIUS, FRONT_RADIUS = .24, .23
BAR = (.115, .155, .74)
SHADOW = .045  # of a compact edge, left clear around the lenses for their shadow
DISPLAY = ("Segoe UI Variable", "Segoe UI")


def normalized_position(area, size: QSize, point: QPoint) -> tuple[float, float]:
    return tuple(max(0.0, min(1.0, (position - origin) / max(1, extent - edge)))
                 for position, origin, extent, edge in
                 ((point.x(), area.x(), area.width(), size.width()),
                  (point.y(), area.y(), area.height(), size.height())))


def screen_position(area, size: QSize, x: float, y: float) -> QPoint:
    return QPoint(area.x() + round(max(0, area.width() - size.width()) * max(0.0, min(1.0, x))),
                  area.y() + round(max(0, area.height() - size.height()) * max(0.0, min(1.0, y))))


def countdown_text(countdown) -> str:
    left = countdown.remaining if countdown is not None else None
    if left is None:
        return "—:—"
    minutes, seconds = divmod(math.ceil(left), 60)
    return f"{'≈' if countdown.approximate else ''}{minutes}:{seconds:02d}"


def file_size(size) -> str:
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        return "size unknown"
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f}".rstrip("0").rstrip(".") + " " + unit
        value /= 1024


def _sans(px, weight=QFont.Normal):
    font = QFont()
    font.setFamilies(list(DISPLAY))
    font.setPointSizeF(theme.point_size(px))
    font.setWeight(weight)
    return font


def _numbers(px):
    """The design's bold countdown face; Segoe UI digits are already tabular."""
    return _sans(px, QFont.Bold)


def _text(painter, rect, value, token, colour="text.mid", alignment=Qt.AlignVCenter, shrink=False):
    font = theme.font(token) if isinstance(token, str) else QFont(token)
    if shrink:
        width = QFontMetricsF(font).horizontalAdvance(value)
        if width > rect.width():
            font.setPointSizeF(max(1, font.pointSizeF() * rect.width() / width))
            while font.pointSizeF() > 1 and QFontMetricsF(font).horizontalAdvance(value) > rect.width():
                font.setPointSizeF(max(1, font.pointSizeF() - .5))
    painter.setFont(font)
    value = QFontMetricsF(font).elidedText(value, Qt.ElideRight, rect.width())
    # A soft drop shadow rather than an outline keeps light text clean on bright desktops.
    for dy, alpha in ((1, 95), (2, 40)):
        painter.setPen(QColor(0, 0, 0, alpha))
        painter.drawText(rect.translated(0, dy), alignment, value)
    painter.setPen(theme.color(colour) if isinstance(colour, str) else colour)
    painter.drawText(rect, alignment, value)


def _rounded(rect, radius):
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


def _shadow(painter, path, depth, keep_out=None):
    """A soft shadow outside a lens only: translucent glass must not darken itself."""
    area = QPainterPath()
    area.addRect(path.boundingRect().adjusted(-2 * depth, -2 * depth, 2 * depth, 2 * depth))
    area = area.subtracted(path if keep_out is None else path.united(keep_out))
    painter.save()
    painter.setClipPath(area, Qt.IntersectClip)
    painter.setBrush(Qt.NoBrush)
    cast = path.translated(0, depth * .35)
    # Nested strokes add up to a shadow that fades linearly over `depth`.
    for step in range(6, 0, -1):
        painter.setPen(QPen(QColor(0, 0, 0, 11), 2 * depth * step / 6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.drawPath(cast)
    painter.restore()


def _glass(painter, rect, radius, material="widget.lens"):
    """Smoke glass lit from above; `_rim` goes on last, over whatever fills it."""
    path = _rounded(rect, radius)
    painter.fillPath(path, theme.color(material))
    sheen = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    sheen.setColorAt(0, QColor(255, 255, 255, 30))
    sheen.setColorAt(.5, QColor(255, 255, 255, 6))
    sheen.setColorAt(1, QColor(255, 255, 255, 0))
    painter.fillPath(path, QBrush(sheen))
    return path


def _rim(painter, rect, radius):
    edge = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    edge.setColorAt(0, QColor(255, 255, 255, 110))
    edge.setColorAt(.5, QColor(255, 255, 255, 76))
    edge.setColorAt(1, QColor(255, 255, 255, 44))
    painter.setBrush(Qt.NoBrush)
    painter.setPen(QPen(QBrush(edge), 1))
    painter.drawRoundedRect(rect.adjusted(.5, .5, -.5, -.5), radius - .5, radius - .5)
    painter.setPen(QPen(QColor(255, 255, 255, 14), 2))
    painter.drawRoundedRect(rect.adjusted(2, 2, -2, -2), max(0.0, radius - 2), max(0.0, radius - 2))


def _gloss(painter, rect):
    """The soft highlight along the front lens's upper edge."""
    pill = QRectF(rect.left() + rect.width() * .15, rect.top() + rect.height() * .065,
                  rect.width() * .72, rect.height() * .13)
    light = QLinearGradient(pill.topLeft(), pill.bottomLeft())
    light.setColorAt(0, QColor(255, 255, 255, 58))
    light.setColorAt(1, QColor(255, 255, 255, 6))
    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(light))
    painter.drawRoundedRect(pill, pill.height() / 2, pill.height() / 2)


def _ring(painter, rect, fraction, colour, width):
    painter.setBrush(Qt.NoBrush)
    painter.setPen(QPen(theme.color("widget.track"), width))
    painter.drawEllipse(rect)
    if fraction is not None and fraction > 0:
        painter.setPen(QPen(colour, width, Qt.SolidLine, Qt.RoundCap))
        painter.drawArc(rect, 90 * 16, -round(360 * 16 * min(1.0, fraction)))


def _ring_lens(painter, lens, fraction, playlist_fraction, outer, inner, depth):
    """A round lens: the outer arc is time left, the inner one the playlist left."""
    d = lens.width()
    _shadow(painter, _rounded(lens, d / 2), depth)
    _glass(painter, lens, d / 2)
    painter.setBrush(Qt.NoBrush)
    for inset, alpha in ((.035, 22), (.17, 14)):  # the bezel and the face's edge
        painter.setPen(QPen(QColor(255, 255, 255, alpha), 1))
        painter.drawEllipse(lens.adjusted(d * inset, d * inset, -d * inset, -d * inset))
    _ring(painter, lens.adjusted(d * .066, d * .066, -d * .066, -d * .066), fraction, outer, max(2.5, d * .02))
    _ring(painter, lens.adjusted(d * .133, d * .133, -d * .133, -d * .133), playlist_fraction, inner,
          max(2.2, d * .015))
    _rim(painter, lens, d / 2)


def _fill_edge(rect, fraction):
    if fraction is None or fraction <= 0:
        return 0.0
    if fraction >= 1:
        return rect.right() + 1
    return rect.left() + rect.width() * fraction


def _rectangle_rects(size):
    margin = size * SHADOW
    unit = (size - 2 * margin) / (FRONT_AT[0] + FRONT_SIZE[0])
    top = (size - (FRONT_AT[1] + FRONT_SIZE[1]) * unit) / 2
    back = QRectF(margin, top, unit, unit)
    front = QRectF(margin + FRONT_AT[0] * unit, top + FRONT_AT[1] * unit,
                   FRONT_SIZE[0] * unit, FRONT_SIZE[1] * unit)
    return back, front


def _rectangle_paths(size):
    back, front = _rectangle_rects(size)
    return _rounded(back, back.width() * BACK_RADIUS), _rounded(front, back.width() * FRONT_RADIUS)


def _bar_rect(back):
    unit = back.width()
    width = max(2.0, unit * .026)
    return QRectF(back.left() + unit * BAR[0] - width / 2, back.top() + unit * BAR[1],
                  width, unit * (BAR[2] - BAR[1]))


def _bar(painter, back, fraction, colour):
    """The rear lens's playlist bar, filling from the top as wallpapers are seen."""
    bar = _bar_rect(back)
    path = _rounded(bar, bar.width() / 2)
    track = QLinearGradient(bar.topLeft(), bar.bottomLeft())
    track.setColorAt(0, QColor(255, 255, 255, 110))
    track.setColorAt(1, QColor(255, 255, 255, 20))
    painter.fillPath(path, QBrush(track))
    if fraction is None or fraction <= 0:
        return
    # A first wallpaper always shows; only a complete playlist fills the whole bar.
    length = bar.height() if fraction >= 1 else min(max(1.0, bar.height() * fraction), bar.height() - 1)
    fill = QLinearGradient(bar.topLeft(), bar.bottomLeft())
    dim = QColor(colour)
    dim.setAlpha(170)
    fill.setColorAt(0, colour)
    fill.setColorAt(1, dim)
    painter.save()
    painter.setClipRect(QRectF(bar.left() - 1, bar.top(), bar.width() + 2, length), Qt.IntersectClip)
    painter.fillPath(path, QBrush(fill))
    painter.restore()


def _rectangles(painter, size, playlist, wallpaper, percent, timer, outer, inner):
    back, front = _rectangle_rects(size)
    back_path, front_path = _rectangle_paths(size)
    unit, depth = back.width(), size * SHADOW * .85
    back_radius, front_radius = unit * BACK_RADIUS, unit * FRONT_RADIUS
    _shadow(painter, back_path, depth, keep_out=front_path)
    _glass(painter, back, back_radius)
    painter.save()
    outside = QPainterPath()
    outside.addRect(QRectF(0, 0, size, size))
    painter.setClipPath(outside.subtracted(front_path), Qt.IntersectClip)
    _rim(painter, back, back_radius)
    painter.restore()
    _bar(painter, back, playlist, outer)
    _text(painter, QRectF(back.left() + unit * .24, back.top() + unit * .17, unit * .64, unit * .24),
          percent, _sans(max(10.0, unit * .17)), "text.hi", Qt.AlignLeft | Qt.AlignVCenter, True)
    _shadow(painter, front_path, depth)
    _glass(painter, front, front_radius)
    fill_rect = front.adjusted(1, 1, -1, -1)
    edge = _fill_edge(fill_rect, wallpaper)
    if edge:
        fill = QLinearGradient(front.topLeft(), front.bottomLeft())
        top, bottom = QColor(inner), QColor(inner)
        top.setAlpha(190)
        bottom.setAlpha(105)
        fill.setColorAt(0, top)
        fill.setColorAt(1, bottom)
        painter.save()
        painter.setClipRect(QRectF(0, 0, edge, size), Qt.IntersectClip)
        painter.fillPath(_rounded(fill_rect, front_radius - 1), QBrush(fill))
        painter.restore()
    _gloss(painter, front)
    _rim(painter, front, front_radius)
    _text(painter, QRectF(front.left() + front.width() * .06, front.top() + front.height() * .28,
                          front.width() * .88, front.height() * .5),
          timer, _numbers(front.height() * .34), QColor(Qt.white), Qt.AlignCenter, True)


class MonitorRow(QWidget):
    """A dual countdown: remaining wallpaper time and remaining playlist items."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.StrongFocus)
        self.progress = self.countdown = None
        self.monitor = self.wallpaper_title = ""
        self.state, self.fraction, self.playlist_fraction = UNKNOWN, None, None
        self.timer_text, self.remaining, self.size_text = "—:—", None, "size unknown"
        self.compact = True
        self.options = {}

    def render(self, monitor, progress, countdown, error="", *, metadata=None, options=None, compact=True):
        self.monitor, self.progress, self.countdown = monitor, progress, countdown
        self.options, self.compact = options or {}, compact
        self.state, _fill = words.icon_state(progress, countdown)
        self.fraction = countdown.fraction if countdown is not None and progress is not None else None
        self.timer_text = countdown_text(countdown) if progress is not None else "—:—"
        self.remaining = max(progress.total - progress.seen, 0) if progress is not None else None
        self.playlist_fraction = self.remaining / progress.total if progress is not None and progress.total else None
        playlist = progress.playlist if progress is not None else "No playlist"
        item = metadata or {}
        self.wallpaper_title = item.get("title") or getattr(progress, "current_title", "") or "Wallpaper unknown"
        self.size_text = file_size(item.get("size"))
        status = words.header_line(self.state, progress, error)
        timing = countdown.describe() if countdown is not None and progress is not None else "Timer unknown"
        remaining = f"{words.count(self.remaining)} of {words.count(progress.total)} wallpapers left" if progress else "Playlist unknown"
        self.setAccessibleName(f"{monitor} · {playlist}")
        self.setAccessibleDescription(f"{status} · {timing} · {remaining} · {self.wallpaper_title} · {self.size_text}")
        diagram = ("Rear rectangle: playlist progress · front rectangle: wallpaper elapsed"
                   if self.options.get("design", "rectangles") == "rectangles"
                   else "Outer ring: time left · inner ring: playlist left")
        self.setToolTip(f"{monitor}\n{playlist}\n{status}\n{timing}\n{remaining}\n"
                        f"{self.wallpaper_title} · {self.size_text}\n{diagram}\n"
                        "Right-click for options · double-click to change view")
        self.setFixedHeight(self.options.get("size", 144) if compact else ROW_HEIGHT)
        self.update()

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rectangles = self.options.get("design", "rectangles") == "rectangles"
        outer = theme.color("widget.outer")
        inner = theme.color("widget.inner")
        status_colour = theme.color(STATE_COLOUR[self.state])
        if rectangles and self.state == FINISHED:
            outer = inner = status_colour
        elif self.state == PAUSED:
            outer = inner = status_colour
        elif not rectangles and self.state == UNKNOWN:
            outer = status_colour
        edge = min(self.width(), self.height()) if self.compact else 80
        margin = edge * SHADOW
        lens = QRectF(margin, margin * .6, edge - 2 * margin, edge - 2 * margin)
        painter.save()
        if not self.compact:
            painter.translate(12, 10)
        if rectangles:
            playlist = (self.progress.seen / self.progress.total
                        if self.progress is not None and self.progress.total else None)
            percent = f"{self.progress.percent}%" if playlist is not None else "—%"
            _state, wallpaper = words.icon_state(self.progress, self.countdown)
            _rectangles(painter, edge, playlist, wallpaper, percent, self.timer_text, outer, inner)
        else:
            d = lens.width()
            _ring_lens(painter, lens, self.fraction, self.playlist_fraction, outer, inner, margin * .85)
            count = f"{words.count(self.remaining)} left" if self.remaining is not None else "— left"
            if self.compact:
                _text(painter, QRectF(lens.left() + d * .22, lens.top() + d * .235, d * .56, d * .11),
                      self.monitor, _sans(max(9.0, d * .085), QFont.DemiBold), "text.body", Qt.AlignCenter, True)
                timer = QRectF(lens.left() + d * .16, lens.top() + d * .39, d * .68, d * .24)
                count_rect = QRectF(lens.left() + d * .22, lens.top() + d * .665, d * .56, d * .11)
            else:
                timer = QRectF(lens.left() + d * .16, lens.top() + d * .30, d * .68, d * .26)
                count_rect = QRectF(lens.left() + d * .2, lens.top() + d * .58, d * .6, d * .14)
            _text(painter, timer, self.timer_text, _numbers(d * .19), QColor(Qt.white), Qt.AlignCenter, True)
            _text(painter, count_rect, count, _sans(max(9.0, d * .072)), "text.body", Qt.AlignCenter, True)
        painter.restore()
        if not self.compact:
            start, width = 106, max(0, self.width() - 122)
            _text(painter, QRectF(start, 11, max(0, width - 76), 22), self.monitor, "type.h3", "text.hi")
            state_width = QFontMetricsF(theme.font("type.caption")).horizontalAdvance(STATE_TEXT[self.state])
            painter.setPen(Qt.NoPen)
            painter.setBrush(inner if self.state == RUNNING else status_colour)
            painter.drawEllipse(QRectF(self.width() - 16 - state_width - 9, 21, 4, 4))
            _text(painter, QRectF(self.width() - 72, 12, 56, 20), STATE_TEXT[self.state],
                  "type.caption", "text.mid", Qt.AlignRight | Qt.AlignVCenter)
            _text(painter, QRectF(start, 38, width, 20), self.progress.playlist if self.progress else "No playlist",
                  "type.bodySm", "text.body")
            size_width = QFontMetricsF(theme.font("type.caption")).horizontalAdvance(self.size_text)
            _text(painter, QRectF(start, 67, max(0, width - size_width - 9), 20),
                  self.wallpaper_title, "type.bodySm", "text.mid")
            _text(painter, QRectF(self.width() - 16 - size_width, 67, size_width, 20),
                  self.size_text, "type.caption", "text.mid", Qt.AlignRight | Qt.AlignVCenter)
            if self.y() > 0:
                divider = QLinearGradient(16, 0, self.width() - 16, 0)
                divider.setColorAt(0, QColor(255, 255, 255, 0))
                divider.setColorAt(.5, theme.color("border.control"))
                divider.setColorAt(1, QColor(255, 255, 255, 0))
                painter.fillRect(QRectF(16, 0, self.width() - 32, 1), QBrush(divider))
        if self.hasFocus():
            # The focus ring follows the glass, so focus never draws a box on the desktop.
            painter.setPen(QPen(theme.color("border.focus"), 1.5))
            painter.setBrush(Qt.NoBrush)
            if self.compact and rectangles:
                back_path, front_path = _rectangle_paths(edge)
                painter.drawPath(back_path.united(front_path))
            elif self.compact:
                painter.drawEllipse(lens)
            else:
                painter.drawRoundedRect(QRectF(self.rect()).adjusted(2, 2, -2, -2), 6, 6)


class DesktopWidget(QWidget):
    settings_requested = Signal()
    visibility_changed = Signal(bool)

    def __init__(self, settings, parent=None, *, profile_key="default", screen=None):
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint)
        self.settings, self.profile_key, self.initial_screen = settings, profile_key, screen
        self.mode, self.options = "compact", {}
        self.rows = []
        self._readings = ([], {}, None, "", (), {})
        self._layout_key = None
        self._placing, self._restored, self._offset = True, False, None
        self.setWindowTitle("Wallpaper widget")
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.StrongFocus)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.header = QWidget(self)
        self.header.setFixedHeight(HEADER_HEIGHT)
        self.header.installEventFilter(self)
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(16, 4, 11, 2)
        header_layout.setSpacing(4)
        title = QLabel("Playlists")
        title.setFont(theme.font("type.labelStrong"))
        title.setAttribute(Qt.WA_TransparentForMouseEvents)
        header_layout.addWidget(title)
        header_layout.addStretch()
        self.mode_button = self._button("Compact", "Compact view", self._toggle_mode)
        header_layout.addWidget(self.mode_button)
        header_layout.addWidget(self._button("", "Widget options", self.show_options, "settings"))
        header_layout.addWidget(self._button("", "Hide widget", self.request_hide, "close"))
        layout.addWidget(self.header)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.content = QWidget()
        self.row_layout = QVBoxLayout(self.content)
        self.row_layout.setContentsMargins(0, 0, 0, 0)
        self.row_layout.setSpacing(0)
        self.scroll.setWidget(self.content)
        self.scroll.viewport().setAutoFillBackground(False)
        self.content.setAutoFillBackground(False)
        layout.addWidget(self.scroll)
        self._position_timer = QTimer(self)
        self._position_timer.setSingleShot(True)
        self._position_timer.timeout.connect(self._settle_position)
        for shortcut, callback in (("Alt+F4", self.request_hide), ("Shift+F10", self.show_options),
                                   ("Menu", self.show_options), ("Return", self.show_options),
                                   ("F2", self.settings_requested.emit), ("Ctrl+M", self._toggle_mode),
                                   ("Ctrl+L", lambda: self._set_option("locked", not self.options["locked"]))):
            QShortcut(QKeySequence(shortcut), self, activated=callback)
        for key, dx, dy in (("Left", -10, 0), ("Right", 10, 0), ("Up", 0, -10), ("Down", 0, 10)):
            QShortcut(QKeySequence(f"Alt+{key}"), self, activated=lambda x=dx, y=dy: self._nudge(x, y))
        app = QApplication.instance()
        app.screenAdded.connect(self._screen_added)
        app.screenRemoved.connect(self._screens_changed)
        for screen in app.screens():
            self._watch_screen(screen)
        self.sync_settings()
        self._placing = False

    def _button(self, text, description, callback, icon=""):
        button = QToolButton(self.header)
        button.setText(text)
        button.setFont(theme.font("type.bodySm"))
        button.setToolTip(description)
        button.setAccessibleName(description)
        button.setAutoRaise(True)
        button.setFixedSize(65 if text else 28, 28)
        if icon:
            button.setIcon(QIcon(icon_pixmap(icon, theme.color("text.mid"), 16, self.devicePixelRatioF())))
        button.clicked.connect(callback)
        return button

    def sync_settings(self):
        self.settings.reload_if_changed()
        previous = self.options
        self.options = profile(self.settings, self.profile_key)
        self.mode = self.options["mode"]
        self.header.setVisible(self.mode == "expanded")
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff if self.mode == "compact" else Qt.ScrollBarAsNeeded)
        self.setWindowOpacity(1)
        self.setCursor(Qt.ArrowCursor if self.options["locked"] else Qt.SizeAllCursor)
        self.render(*self._readings)
        if self.isVisible() and previous.get("position") != self.options.get("position"):
            self._restore_position()
        if self.isVisible() and previous.get("layer") != self.options["layer"]:
            apply_layer(self, self.options["layer"])

    def _set_option(self, name, value):
        set_option(self.settings, self.profile_key, name, value)
        self.sync_settings()

    def _toggle_mode(self):
        self._set_option("mode", "compact" if self.mode == "expanded" else "expanded")

    def render(self, results, countdowns, primary, error="", monitors=(), metadata=None):
        results = list(results)
        self._readings = (results, countdowns, primary, error, tuple(monitors), metadata or {})
        readings = {progress.monitor: progress for progress in results}
        binding = self.options.get("monitor", "tray")
        if binding != "tray":
            names = [binding]
        elif self.mode == "expanded":
            names = list(dict.fromkeys((*monitors, *readings))) or ["Monitor"]
        else:
            names = [primary.monitor if primary is not None else self.settings.get("tracker", "primary", None) or "Monitor"]
        if len(self.rows) != len(names):
            for row in self.rows:
                self.row_layout.removeWidget(row)
                row.hide()
                row.deleteLater()
            self.rows = [MonitorRow(self.content) for _ in names]
            for row in self.rows:
                row.installEventFilter(self)
                self.row_layout.addWidget(row)
        for row, name in zip(self.rows, names):
            progress = readings.get(name)
            current = getattr(progress, "current", None)
            item = (metadata or {}).get(current, {})
            row.render(name, progress, countdowns.get(name), error, metadata=item,
                       options=self.options, compact=self.mode == "compact")
        layout_key = (self.mode, self.options["size"], len(names))
        if layout_key != self._layout_key:
            self._fit_window()
            self._layout_key = layout_key
        self.setAccessibleName(self.rows[0].accessibleName() if self.mode == "compact" else "Wallpaper playlists")
        self.setAccessibleDescription(self.rows[0].accessibleDescription())
        self.setToolTip(self.rows[0].toolTip())
        self.update()

    def build_menu(self):
        menu = QMenu(self)

        def choices(label, key, values):
            submenu = menu.addMenu(label)
            group = QActionGroup(submenu)
            group.setExclusive(True)
            for title, value in values:
                action = submenu.addAction(title)
                action.setData((key, value))
                action.setCheckable(True)
                action.setChecked(self.options[key] == value)
                action.triggered.connect(lambda _checked=False, k=key, v=value: self._set_option(k, v))
                group.addAction(action)

        choices("View", "mode", (("Compact", "compact"), ("Expanded", "expanded")))
        choices("Design", "design", (("Rectangles", "rectangles"), ("Rings", "rings")))
        monitors = dict.fromkeys((*self._readings[4], *(p.monitor for p in self._readings[0])))
        if self.profile_key == "default":
            choices("Monitor", "monitor", (("Selected tray monitor", "tray"), *((name, name) for name in monitors)))
        else:
            menu.addAction(f"Monitor: {self.profile_key}").setEnabled(False)
        choices("Layer", "layer", (("On desktop", "desktop"), ("Normal", "normal"), ("Always on top", "top")))
        choices("Size", "size", (("Small · 112 px", 112), ("Medium · 144 px", 144), ("Large · 176 px", 176)))
        menu.addSeparator()
        lock = menu.addAction("Lock position")
        lock.setCheckable(True)
        lock.setChecked(self.options["locked"])
        lock.setShortcut(QKeySequence("Ctrl+L"))
        lock.triggered.connect(lambda checked: self._set_option("locked", checked))
        menu.addAction("Move: Alt + arrow keys").setEnabled(False)
        menu.addAction("Reset position", self.reset_position)
        menu.addSeparator()
        settings = menu.addAction("Widget settings", self.settings_requested.emit)
        settings.setShortcut(QKeySequence("F2"))
        menu.addAction("Hide widget", self.request_hide)
        return menu

    def show_options(self, position=None):
        self._menu = self.build_menu()
        self._menu.aboutToHide.connect(self._menu.deleteLater)
        self._menu.popup(position if isinstance(position, QPoint) else self.mapToGlobal(self.rect().center()))

    def contextMenuEvent(self, event):  # noqa: N802
        self.show_options(event.globalPos())
        event.accept()

    def eventFilter(self, watched, event):  # noqa: N802
        if event.type() == QEvent.MouseButtonDblClick and event.button() == Qt.LeftButton:
            self._toggle_mode()
            return True
        if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            return self._start_move(event)
        if event.type() == QEvent.MouseMove and self._offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._offset)
            return True
        if event.type() == QEvent.MouseButtonRelease:
            self._offset = None
            self._position_timer.start(150)
        return super().eventFilter(watched, event)

    def _start_move(self, event):
        if self.options["locked"]:
            return False
        handle = self.windowHandle()
        if handle is None or not handle.startSystemMove():
            self._offset = event.globalPosition().toPoint() - self.pos()
        return True

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._start_move(event)

    def mouseDoubleClickEvent(self, event):  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._toggle_mode()

    def mouseMoveEvent(self, event):  # noqa: N802
        if self._offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._offset)

    def mouseReleaseEvent(self, event):  # noqa: N802
        self._offset = None
        self._position_timer.start(150)

    def _nudge(self, dx, dy):
        if not self.options["locked"]:
            self.move(self.pos() + QPoint(dx, dy))
            self._settle_position()

    def _watch_screen(self, screen):
        for signal in (screen.availableGeometryChanged, screen.geometryChanged, screen.logicalDotsPerInchChanged):
            signal.connect(self._screens_changed)

    def _screen_added(self, screen):
        self._watch_screen(screen)
        self._screens_changed()

    def _screens_changed(self, *_args):
        QTimer.singleShot(0, self._restore_position)

    def _screen(self):
        app = QApplication.instance()
        return app.screenAt(self.geometry().center()) or self.screen() or app.primaryScreen()

    def _fit_window(self, screen=None, relative=None):
        screen = screen or self._screen()
        if screen is None:
            return
        area = screen.availableGeometry()
        relative = relative or normalized_position(area, self.size(), self.pos())
        self._placing = True
        # Drop the expanded header's minimum width before resizing back to a circle.
        self.layout().activate()
        if self.mode == "compact":
            edge = min(self.options["size"], area.width(), area.height())
            self.resize(edge, edge)
            for row in self.rows:
                row.setFixedHeight(edge)
        else:
            self.resize(min(WIDTH, area.width()), min(HEADER_HEIGHT + ROW_HEIGHT * max(1, len(self.rows)), area.height()))
        self.move(screen_position(area, self.size(), *relative))
        self._placing = False

    def _restore_position(self):
        saved = self.options.get("position", {})
        saved = saved if isinstance(saved, dict) else {}
        app = QApplication.instance()
        screens = app.screens()
        fallback = self.initial_screen if self.initial_screen in screens else app.primaryScreen()
        screen = next((screen for screen in screens if screen.name() == saved.get("screen")), fallback)
        coordinates = []
        for key, default in (("x", .95), ("y", .1)):
            value = saved.get(key, default)
            coordinates.append(value if isinstance(value, (int, float)) and math.isfinite(value) else default)
        self._fit_window(screen, tuple(coordinates))
        self._restored = True

    def reset_position(self):
        screen = self.initial_screen if self.initial_screen in QApplication.screens() else self._screen()
        if screen is None:
            return
        self._set_option("position", {"screen": screen.name(), "x": .95, "y": .1})
        self._restore_position()

    def _settle_position(self):
        if QApplication.mouseButtons() & Qt.LeftButton:
            self._position_timer.start(150)
            return
        self._fit_window()
        self.save_position()

    def save_position(self):
        if not self._restored:
            return
        screen = self._screen()
        if screen is None:
            return
        x, y = normalized_position(screen.availableGeometry(), self.size(), self.pos())
        position = {"screen": screen.name(), "x": x, "y": y}
        set_option(self.settings, self.profile_key, "position", position)
        self.options["position"] = position

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        if not self._restored:
            self._restore_position()
            self.windowHandle().screenChanged.connect(lambda _screen: self._position_timer.start(150))
        QTimer.singleShot(0, lambda: apply_layer(self, self.options["layer"]) if self.isVisible() else None)

    def moveEvent(self, event):  # noqa: N802
        super().moveEvent(event)
        if not self._placing and hasattr(self, "_position_timer"):
            self._position_timer.start(250)

    def request_hide(self):
        self.save_position()
        self.hide()
        self.visibility_changed.emit(False)

    def closeEvent(self, event):  # noqa: N802
        # QApplication.quit also closes windows; preserve the visibility preference.
        self.save_position()
        event.accept()

    def paintEvent(self, event):  # noqa: N802
        if self.mode == "compact":
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        panel = QRectF(self.rect())
        _glass(painter, panel, 27, "widget.glass")
        _rim(painter, panel, 27)
