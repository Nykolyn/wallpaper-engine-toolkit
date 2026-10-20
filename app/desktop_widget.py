"""Tray-shaped or circular desktop readings, owned by the tray.

Only existing readings and asynchronously supplied metadata are painted here.
Qt owns native movement and DPI; positions use each screen's logical work area.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QEvent, QPoint, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QActionGroup, QBrush, QColor, QFontMetricsF, QIcon, QKeySequence,
                          QLinearGradient, QPainter, QPainterPath, QPen, QRegion, QShortcut)
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QMenu,
                              QScrollArea, QToolButton, QVBoxLayout, QWidget)

from . import theme, tray_icon, tray_words as words
from .tray_icon import FINISHED, PAUSED, RUNNING, UNKNOWN
from .tray_menu import icon_pixmap
from .widget_desktop import apply_layer
from .widget_backdrop import apply_backdrop
from .widget_options import profile, set_option

WIDTH, HEADER_HEIGHT, ROW_HEIGHT = 380, 44, 104
STATE_COLOUR = {RUNNING: "tray.fill.running", PAUSED: "tray.fill.paused",
                FINISHED: "tray.fill.ok", UNKNOWN: "tray.fill.unknown"}
STATE_TEXT = {RUNNING: "Tracking", PAUSED: "Paused", FINISHED: "Finished", UNKNOWN: "Unknown"}


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


def _text(painter, rect, value, token, colour="text.mid", alignment=Qt.AlignVCenter, shrink=False):
    font = theme.font(token)
    if shrink:
        width = QFontMetricsF(font).horizontalAdvance(value)
        if width > rect.width():
            font.setPointSizeF(max(1, font.pointSizeF() * rect.width() / width))
            while font.pointSizeF() > 1 and QFontMetricsF(font).horizontalAdvance(value) > rect.width():
                font.setPointSizeF(max(1, font.pointSizeF() - .5))
    painter.setFont(font)
    value = QFontMetricsF(font).elidedText(value, Qt.ElideRight, rect.width())
    painter.setPen(QColor(0, 0, 0, 170))
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        painter.drawText(rect.translated(dx, dy), alignment, value)
    painter.setPen(theme.color(colour) if isinstance(colour, str) else colour)
    painter.drawText(rect, alignment, value)


def _glass(painter, rect, radius, native=False):
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    painter.fillPath(path, theme.color("widget.clear" if native else "widget.glass"))
    sheen = QLinearGradient(rect.topLeft(), rect.bottomRight())
    sheen.setColorAt(0, QColor(255, 255, 255, 54))
    sheen.setColorAt(.44, QColor(255, 255, 255, 4))
    sheen.setColorAt(1, QColor(187, 179, 219, 26))
    painter.fillPath(path, QBrush(sheen))
    edge = QLinearGradient(rect.topLeft(), rect.bottomRight())
    edge.setColorAt(0, QColor(255, 255, 255, 200))
    edge.setColorAt(.45, QColor(255, 255, 255, 24))
    edge.setColorAt(1, theme.color("widget.edge"))
    painter.setPen(QPen(QBrush(edge), 1.2))
    painter.setBrush(Qt.NoBrush)
    painter.drawPath(path)
    inset = rect.adjusted(1.5, 1.5, -1.5, -1.5)
    painter.setPen(QPen(QColor(255, 255, 255, 42), 1.5))
    painter.drawRoundedRect(inset, max(0, radius - 1.5), max(0, radius - 1.5))
    painter.save()
    painter.setClipPath(path)
    gloss = QRectF(rect.left() + radius * .55, rect.top() + 4,
                   rect.width() - radius * 1.1, max(4, min(13, rect.height() * .14)))
    light = QLinearGradient(gloss.topLeft(), gloss.bottomLeft())
    light.setColorAt(0, QColor(255, 255, 255, 75))
    light.setColorAt(1, QColor(255, 255, 255, 0))
    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(light))
    painter.drawEllipse(gloss)
    painter.restore()
    return path


def _ring(painter, rect, fraction, colour, width):
    painter.setBrush(Qt.NoBrush)
    painter.setPen(QPen(theme.color("widget.track"), width))
    painter.drawEllipse(rect)
    if fraction is not None and fraction > 0:
        painter.setPen(QPen(colour, width, Qt.SolidLine, Qt.RoundCap))
        painter.drawArc(rect, 90 * 16, -round(360 * 16 * min(1.0, fraction)))


def _fill_edge(rect, fraction, stroke=0):
    if fraction is None or fraction <= 0:
        return 0.0
    if fraction >= 1:
        return rect.right() + stroke / 2 + 1
    return rect.left() - stroke / 2 + (rect.width() + stroke) * fraction


def _rectangle_rects(size):
    back = tray_icon._rect(tray_icon.BACK, size)
    front = tray_icon._rect(tray_icon.FRONT, size)
    back.setHeight(back.width())
    return back, front


def _rectangles(painter, size, playlist, wallpaper, percent, timer, outer, inner, native=False):
    back, front = _rectangle_rects(size)
    scale = tray_icon.scale(size)
    back_path = _glass(painter, back, back.width() * .26, native)
    # The tray clips include thick strokes; glass follows its own painted edges.
    back_stroke = max(1.5, size / 70)
    front_fill = front.adjusted(1.5, 1.5, -1.5, -1.5)
    back_edge = _fill_edge(back, playlist, back_stroke)
    front_edge = _fill_edge(front_fill, wallpaper)
    painter.save()
    painter.setClipRect(QRectF(0, 0, back_edge, size))
    painter.setPen(QPen(outer, back_stroke))
    painter.drawPath(back_path)
    painter.restore()
    inset = tray_icon.BACK[5] * scale / 2
    label = QRectF(back.left() + inset, back.top() + inset, back.width() - 2 * inset,
                   front.top() - tray_icon.FRONT[5] * scale / 2 - back.top() - inset)
    _text(painter, label, percent, "type.h3" if size >= 112 else "type.monoXs",
          "text.hi", Qt.AlignCenter, True)
    _glass(painter, front, front.height() * .30, native)
    fill_path = QPainterPath()
    fill_path.addRoundedRect(front_fill, front.height() * .28, front.height() * .28)
    tint = inner.darker(155)
    tint.setAlpha(85 if native else 170)
    fill = QLinearGradient(front.topLeft(), front.bottomLeft())
    top = QColor(tint)
    top.setAlpha(35 if native else 115)
    fill.setColorAt(0, top)
    fill.setColorAt(1, tint)
    painter.save()
    painter.setClipRect(QRectF(0, 0, front_edge, size))
    painter.fillPath(fill_path, QBrush(fill))
    painter.restore()
    inset = tray_icon.FRONT[5] * scale / 2 + 2
    label = front.adjusted(inset, inset, -inset, -inset)
    # Contrast against the tinted glass, including a white desktop behind it.
    background = theme.composite(tint, theme.composite("widget.glass", QColor(Qt.white)))
    on_fill = max((QColor(Qt.black), QColor(Qt.white)), key=lambda colour: theme.contrast(colour, background))
    if native:
        on_fill = QColor(Qt.white)
    for clip, colour in ((QRectF(front_edge, 0, size - front_edge, size), QColor(Qt.white)),
                         (QRectF(0, 0, front_edge, size), on_fill)):
        painter.save()
        painter.setClipRect(clip, Qt.IntersectClip)
        _text(painter, label, timer, "type.metricLg" if size >= 112 else "type.ring",
              colour, Qt.AlignCenter, True)
        painter.restore()


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
        self.native_glass = False
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
        if rectangles:
            size = min(self.width(), self.height()) if self.compact else 80
            painter.save()
            if not self.compact:
                painter.translate(12, 10)
            playlist = (self.progress.seen / self.progress.total
                        if self.progress is not None and self.progress.total else None)
            percent = f"{self.progress.percent}%" if playlist is not None else "—%"
            _state, wallpaper = words.icon_state(self.progress, self.countdown)
            _rectangles(painter, size, playlist, wallpaper, percent, self.timer_text, outer, inner, self.native_glass)
            painter.restore()
        elif self.compact:
            edge = min(self.width(), self.height())
            _glass(painter, QRectF(1, 1, edge - 2, edge - 2), (edge - 2) / 2, self.native_glass)
            stroke = max(2.5, edge / 48)
            margin = edge * .075
            _ring(painter, QRectF(margin, margin, edge - margin * 2, edge - margin * 2), self.fraction, outer, stroke)
            inset = margin + edge * .07
            _ring(painter, QRectF(inset, inset, edge - inset * 2, edge - inset * 2),
                  self.playlist_fraction, inner, max(2.2, stroke - .3))
            text_width = edge * .61
            x = (edge - text_width) / 2
            _text(painter, QRectF(x, edge * .235, text_width, edge * .13), self.monitor,
                  "type.labelStrong", alignment=Qt.AlignCenter, shrink=True)
            token = "type.metricLg" if edge < 130 or self.fraction is None else "type.count"
            _text(painter, QRectF(x - 3, edge * .355, text_width + 6, edge * .235),
                  self.timer_text, token, "text.hi", Qt.AlignCenter, True)
            count = f"{words.count(self.remaining)} left" if self.remaining is not None else "playlist —"
            _text(painter, QRectF(x, edge * .595, text_width, edge * .13), count,
                  "type.monoSm", "text.body", Qt.AlignCenter, True)
        if not self.compact:
            ring = QRectF(12, 10, 80, 80)
            if not rectangles:
                _glass(painter, ring, 40, self.native_glass)
                _ring(painter, ring.adjusted(6, 6, -6, -6), self.fraction, outer, 2.5)
                _ring(painter, ring.adjusted(12, 12, -12, -12), self.playlist_fraction, inner, 2.2)
                _text(painter, QRectF(23, 31, 58, 20), self.timer_text, "type.ring", "text.hi", Qt.AlignCenter, True)
                count = f"{words.count(self.remaining)} left" if self.remaining is not None else "— left"
                _text(painter, QRectF(25, 53, 54, 15), count, "type.monoXs", "text.mid", Qt.AlignCenter, True)
            start, width = 106, max(0, self.width() - 122)
            _text(painter, QRectF(start, 11, max(0, width - 76), 22), self.monitor, "type.h3", "text.hi")
            painter.setPen(Qt.NoPen)
            painter.setBrush(inner if self.state == RUNNING else status_colour)
            painter.drawEllipse(QRectF(self.width() - 81, 21, 4, 4))
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
            painter.setPen(QPen(theme.color("border.focus"), 1))
            painter.setBrush(Qt.NoBrush)
            if self.compact and not rectangles:
                painter.drawEllipse(QRectF(self.rect()).adjusted(1, 1, -1, -1))
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
            row.native_glass = getattr(self, "native_glass", False)
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
        self._update_backdrop()
        self.update()

    def _update_backdrop(self, force=False):
        if not self.isVisible():
            return
        key = (int(self.winId()), self.mode, self.options["design"], self.size(), self.devicePixelRatioF())
        if not force and key == getattr(self, "_backdrop_key", None):
            return
        self._backdrop_key = key
        self.native_glass = apply_backdrop(self)
        path = QPainterPath()
        if self.mode == "expanded":
            path.addRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), 27, 27)
        elif self.options["design"] == "rings":
            path.addEllipse(QRectF(self.rect()).adjusted(1, 1, -1, -1))
        else:
            back, front = _rectangle_rects(min(self.width(), self.height()))
            path.addRoundedRect(back, back.width() * .26, back.width() * .26)
            path.addRoundedRect(front, front.height() * .30, front.height() * .30)
            path.setFillRule(Qt.WindingFill)
        if self.native_glass:
            self.setMask(QRegion(path.toFillPolygon().toPolygon(), Qt.WindingFill))
        else:
            self.clearMask()
        for row in self.rows:
            row.native_glass = self.native_glass
            row.update()
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
        QTimer.singleShot(0, lambda: self._update_backdrop(force=True))

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self, "options"):
            self._update_backdrop()

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
        native = getattr(self, "native_glass", False)
        path = _glass(painter, QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), 27, native)
        if native:
            # Standard header controls need a contrast backing over bright desktops.
            shade = QLinearGradient(0, 0, 0, HEADER_HEIGHT + 8)
            shade.setColorAt(0, QColor(15, 20, 29, 185))
            shade.setColorAt(.8, QColor(15, 20, 29, 185))
            shade.setColorAt(1, QColor(15, 20, 29, 0))
            painter.fillPath(path, QBrush(shade))
