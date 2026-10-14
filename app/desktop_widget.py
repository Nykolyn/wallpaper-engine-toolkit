"""The tray's desktop widget: the same readings, without loading the toolkit UI.

Qt owns movement and DPI conversion. Saved coordinates are fractions of a
screen's available movement area, so resolution, scale and taskbar changes do
not strand the widget outside a screen.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFontMetricsF, QIcon, QKeySequence, QPainter, QPalette, QPen, QShortcut
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QScrollArea, QToolButton, QVBoxLayout, QWidget

from . import theme, tray_words as words
from .tray_icon import FINISHED, PAUSED, RUNNING, UNKNOWN
from .tray_menu import icon_pixmap

WIDTH, HEADER_HEIGHT, ROW_HEIGHT = 360, 42, 136
STATE_COLOUR = {RUNNING: "tray.fill.running", PAUSED: "tray.fill.paused",
                FINISHED: "tray.fill.ok", UNKNOWN: "tray.fill.unknown"}
STATE_TEXT = {RUNNING: "Tracking", PAUSED: "Paused", FINISHED: "Finished",
              UNKNOWN: "Timer unknown"}


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


class MonitorRow(QWidget):
    """One reading, painted like the tray menu and exposed as accessible text."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(ROW_HEIGHT)
        self.setFocusPolicy(Qt.StrongFocus)
        self.progress = self.countdown = None
        self.monitor = ""
        self.state, self.fraction, self.timer_text = UNKNOWN, None, "—:—"
        self.error = ""

    def render(self, monitor: str, progress, countdown, error: str = "") -> None:
        self.monitor, self.progress, self.countdown, self.error = monitor, progress, countdown, error
        self.state, _fill = words.icon_state(progress, countdown)
        self.fraction = countdown.fraction if countdown is not None and progress is not None else None
        self.timer_text = countdown_text(countdown) if progress is not None else "—:—"
        playlist = progress.playlist if progress is not None else "No playlist"
        status = words.header_line(self.state, progress, error)
        timing = countdown.describe() if countdown is not None and progress is not None else "Timer unknown"
        self.setAccessibleName(f"{monitor} · {playlist}")
        self.setAccessibleDescription(f"{status} · {timing}")
        self.setToolTip(f"{monitor}\n{playlist}\n{status}\n{timing}")
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        colour = theme.color(STATE_COLOUR[self.state])
        left_width = max(0, self.width() - 124)

        def text(value, y, token, tone="text.mid"):
            painter.setFont(theme.font(token))
            painter.setPen(theme.color(tone))
            value = QFontMetricsF(painter.font()).elidedText(value, Qt.ElideRight, left_width)
            painter.drawText(QRectF(16, y, left_width, 22), Qt.AlignVCenter, value)

        text(self.monitor, 9, "type.h3", "text.hi")
        text(self.progress.playlist if self.progress else "No playlist", 31, "type.bodySm")
        text(STATE_TEXT[self.state] if self.progress else (self.error or "No playlist found"),
             56, "type.labelStrong", STATE_COLOUR[self.state])
        count = (f"{words.count(self.progress.seen)} / {words.count(self.progress.total)}"
                 f" shown · {self.progress.percent}%" if self.progress else "Progress unavailable")
        text(count, 99, "type.monoSm", "text.lo")
        track = QRectF(16, 87, left_width, 5)
        painter.setPen(Qt.NoPen)
        painter.setBrush(theme.color("tray.body"))
        painter.drawRoundedRect(track, 2.5, 2.5)
        if self.progress and self.progress.total:
            fill = QRectF(track)
            fill.setWidth(track.width() * max(0, min(1, self.progress.seen / self.progress.total)))
            painter.setBrush(colour)
            painter.drawRoundedRect(fill, 2.5, 2.5)

        ring = QRectF(self.width() - 98, 21, 78, 78)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(theme.color("tray.body"), 5))
        painter.drawEllipse(ring)
        if self.fraction is not None and self.fraction > 0:
            painter.setPen(QPen(colour, 5, Qt.SolidLine, Qt.RoundCap))
            painter.drawArc(ring, 90 * 16, -round(360 * 16 * min(1.0, self.fraction)))
        painter.setFont(theme.font("type.metric"))
        painter.setPen(theme.color("text.hi"))
        # Long delays keep every digit inside the ring.
        if QFontMetricsF(painter.font()).horizontalAdvance(self.timer_text) > 68:
            painter.setFont(theme.font("type.ring"))
        painter.drawText(ring, Qt.AlignCenter, self.timer_text)
        painter.setFont(theme.font("type.caption"))
        painter.setPen(theme.color("text.lo"))
        painter.drawText(QRectF(ring.x() - 3, 101, 84, 19), Qt.AlignCenter,
                         "next change" if self.fraction is not None else "timer unknown")
        painter.setPen(QPen(theme.color("border.faint"), 1))
        painter.drawLine(16, self.height() - 1, self.width() - 16, self.height() - 1)
        if self.hasFocus():
            painter.setPen(QPen(theme.color("border.focus"), 1))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(2, 2, -2, -2), 6, 6)


class _Header(QWidget):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.offset = None
        self.setFixedHeight(HEADER_HEIGHT)
        self.setCursor(Qt.SizeAllCursor)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            handle = self.owner.windowHandle()
            if handle is None or not handle.startSystemMove():
                self.offset = event.globalPosition().toPoint() - self.owner.pos()
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self.offset is not None and event.buttons() & Qt.LeftButton:
            self.owner.move(event.globalPosition().toPoint() - self.offset)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self.offset = None
        self.owner._position_timer.start(150)


class DesktopWidget(QWidget):
    """A compact widget by default; render receives the tray's existing readings."""

    settings_requested = Signal()
    visibility_changed = Signal(bool)

    def __init__(self, settings, parent=None):
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.settings = settings
        self.mode = "compact"
        self.rows: list[MonitorRow] = []
        self._readings = ([], {}, None, "", ())
        self._placing = True
        self._restored = False
        self.setWindowTitle("Wallpaper widget")
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAccessibleName("Wallpaper playlist widget")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        self.header = _Header(self)
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(15, 4, 8, 4)
        header_layout.setSpacing(4)
        title = QLabel("Wallpaper tracker")
        title.setFont(theme.font("type.h3"))
        title.setAttribute(Qt.WA_TransparentForMouseEvents)
        header_layout.addWidget(title)
        header_layout.addStretch()
        self.mode_button = self._button("All", "Show all monitors", self._toggle_mode)
        header_layout.addWidget(self.mode_button)
        header_layout.addWidget(self._button("", "Widget settings", self.settings_requested.emit, "settings"))
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
        layout.addWidget(self.scroll)
        palette = self.palette()
        palette.setColor(QPalette.WindowText, theme.color("text.hi"))
        palette.setColor(QPalette.ButtonText, theme.color("text.mid"))
        palette.setColor(QPalette.Window, theme.color("bg.solid"))
        self.setPalette(palette)
        self.scroll.viewport().setAutoFillBackground(False)
        self.content.setAutoFillBackground(False)
        self._position_timer = QTimer(self)
        self._position_timer.setSingleShot(True)
        self._position_timer.timeout.connect(self._settle_position)
        QShortcut(QKeySequence("Alt+F4"), self, activated=self.request_hide)
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
        button.setCursor(Qt.ArrowCursor)
        button.setFixedSize(34 if text else 28, 28)
        if icon:
            button.setIcon(QIcon(icon_pixmap(icon, theme.color("text.mid"), 16, self.devicePixelRatioF())))
        button.clicked.connect(callback)
        return button

    def sync_settings(self) -> None:
        self.settings.reload_if_changed()
        mode = self.settings.get("widget", "mode", "compact")
        self.mode = mode if mode in ("compact", "expanded") else "compact"
        self.mode_button.setText("One" if self.mode == "expanded" else "All")
        description = "Show selected tray monitor" if self.mode == "expanded" else "Show all monitors"
        self.mode_button.setToolTip(description)
        self.mode_button.setAccessibleName(description)
        self.render(*self._readings)

    def _toggle_mode(self) -> None:
        self.settings.reload_if_changed()
        self.settings.set("widget", "mode", "compact" if self.mode == "expanded" else "expanded")
        self.settings.save()
        self.sync_settings()

    def render(self, results, countdowns, primary, error: str = "", monitors=()) -> None:
        self._readings = (list(results), countdowns, primary, error, tuple(monitors))
        readings = {progress.monitor: progress for progress in self._readings[0]}
        if self.mode == "expanded":
            names = list(dict.fromkeys((*monitors, *readings))) or ["Monitor"]
        else:
            names = [primary.monitor if primary is not None else
                     self.settings.get("tracker", "primary", None) or "Monitor"]
        if len(self.rows) != len(names):
            for row in self.rows:
                self.row_layout.removeWidget(row)
                row.deleteLater()
            self.rows = [MonitorRow(self.content) for _ in names]
            for row in self.rows:
                self.row_layout.addWidget(row)
            self._fit_window()
        for row, name in zip(self.rows, names):
            row.render(name, readings.get(name), countdowns.get(name), error)

    def _watch_screen(self, screen) -> None:
        for signal in (screen.availableGeometryChanged, screen.geometryChanged,
                       screen.logicalDotsPerInchChanged):
            signal.connect(self._screens_changed)

    def _screen_added(self, screen) -> None:
        self._watch_screen(screen)
        self._screens_changed()

    def _screens_changed(self, *_args) -> None:
        QTimer.singleShot(0, self._restore_position)

    def _screen(self):
        app = QApplication.instance()
        return app.screenAt(self.geometry().center()) or self.screen() or app.primaryScreen()

    def _fit_window(self, screen=None, relative=None) -> None:
        screen = screen or self._screen()
        if screen is None:
            return
        area = screen.availableGeometry()
        relative = relative or normalized_position(area, self.size(), self.pos())
        self._placing = True
        self.resize(min(WIDTH, area.width()),
                    min(HEADER_HEIGHT + ROW_HEIGHT * max(1, len(self.rows)) + 2, area.height()))
        self.move(screen_position(area, self.size(), *relative))
        self._placing = False

    def _restore_position(self) -> None:
        saved = self.settings.get("widget", "position", {})
        saved = saved if isinstance(saved, dict) else {}
        app = QApplication.instance()
        screen = next((screen for screen in app.screens() if screen.name() == saved.get("screen")),
                      app.primaryScreen())
        coordinates = []
        for key, default in (("x", 0.97), ("y", 0.1)):
            value = saved.get(key, default)
            coordinates.append(value if isinstance(value, (int, float)) and math.isfinite(value) else default)
        self._fit_window(screen, tuple(coordinates))
        self._restored = True

    def _settle_position(self) -> None:
        if QApplication.mouseButtons() & Qt.LeftButton:
            self._position_timer.start(150)
            return
        self._fit_window()
        self.save_position()

    def save_position(self) -> None:
        if not self._restored:
            return
        screen = self._screen()
        if screen is None:
            return
        x, y = normalized_position(screen.availableGeometry(), self.size(), self.pos())
        self.settings.reload_if_changed()
        self.settings.set("widget", "position", {"screen": screen.name(), "x": x, "y": y})
        self.settings.save()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._restored:
            self._restore_position()
            self.windowHandle().screenChanged.connect(lambda _screen: self._position_timer.start(150))

    def moveEvent(self, event) -> None:  # noqa: N802
        super().moveEvent(event)
        if not self._placing and hasattr(self, "_position_timer"):
            self._position_timer.start(250)

    def request_hide(self) -> None:
        self.save_position()
        self.hide()
        self.visibility_changed.emit(False)

    def closeEvent(self, event) -> None:  # noqa: N802
        # QApplication.quit also closes windows; it must preserve the visibility preference.
        self.save_position()
        event.accept()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(theme.color("surface.overlay"))
        painter.setPen(QPen(theme.color("border.control"), 1))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), theme.R_LG, theme.R_LG)
