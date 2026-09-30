"""The log: LogPanel, and the LogModel it shows.

A job reports what it does one line at a time, and the LogPanel is where those
lines are read while it runs: a console well under a header, a line per event
in three columns — time · kind · message — in `type.mono`.

- LogModel keeps the last 5 000 lines in a ring. `append(time, kind, message)`
  adds one (`extend` many at once); past the cap the oldest line goes, so a
  run of 30 000 moves costs the same memory as one of 5 000. The whole run
  is in the log file; the panel is for watching.
- A line's kind decides its colour, as the console does (§3.1): moved,
  returned, deleted and done are ok, skip, dupe and stop warn, fail and error
  err, step, start and info mid. Any other kind is shown as it is written, in
  mid; "step 2" is a step's.
- ProblemsFilter is the "Problems" half of the All / Problems switch: the
  warn and err lines only, over the same model.
- LogView follows the newest line while you are at the bottom, and stops
  following the moment you scroll up to read — lines keep arriving below and
  what you are reading stays where it is, even as the oldest lines leave the
  ring. Scroll back to the bottom and it follows again.
- LogPanel is the card: a header ("Log", the live dot while the job runs,
  "writing to rotator.log", All / Problems, copy, and the chevron), the
  console, and a footer saying how the log folder is kept, with "Open log
  folder". Collapsed, it is the header alone, with the count of problems in
  a badge. The body's height and the chevron move together over
  motion.slow; with Windows' animations off they jump.
- `fill=True` is the Overview's glance at a log: the panel takes the height
  its layout gives it, and collapsed it keeps the console open under the
  header, showing the newest lines, with the badge but without the switch,
  the copy button or the footer. Opening it brings those back; the height
  stays the layout's, so nothing slides.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Callable, Iterable, NamedTuple

from PySide6.QtCore import (
    QAbstractListModel, QModelIndex, QRectF, QSize, QSortFilterProxyModel, Qt, QTimer,
    QVariantAnimation, Signal,
)
from PySide6.QtGui import QFontMetricsF, QKeySequence, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QHBoxLayout, QListView, QSizePolicy, QStyle,
    QStyledItemDelegate, QVBoxLayout, QWidget,
)

from ... import animations, theme
from . import format as fmt
from . import icons
from .base import Elided, LiveDot, follow, label, token
from .buttons import GhostButton, IconButton
from .panels import GlassPanel
from .selection import SegmentedControl
from .tables import TableFooter

# kind → tone, as the console colours it (REDESIGN_PLAN §3.1)
KIND_TONES: dict[str, str] = {
    "moved": "ok", "done": "ok", "returned": "ok", "deleted": "ok",
    "skip": "warn", "dupe": "warn", "stop": "warn",
    "fail": "err", "error": "err",
    "step": "mid", "start": "mid", "info": "mid",
}
# What the kind column is as wide as: every kind above, and a numbered step
# ("step 2", as a rotation's log numbers its steps).
KIND_WIDEST = (*KIND_TONES, "step 9")
# The level names the engines already write, so a page can pass one through.
KIND_ALIASES: dict[str, str] = {"ok": "ok", "warn": "warn", "warning": "warn", "err": "err"}
TONES = ("ok", "warn", "err", "mid")
PROBLEM_TONES = ("warn", "err")
# tone → the colour its kind is written in
TONE_COLOURS = {"ok": "console.ok", "warn": "console.warn", "err": "console.err",
                "mid": "text.mid"}

TIME_ROLE = Qt.UserRole + 80
KIND_ROLE = Qt.UserRole + 81
MESSAGE_ROLE = Qt.UserRole + 82
TONE_ROLE = Qt.UserRole + 83

FOOTER_NOTE = "newest first · kept for 30 days"
# Qt's QWIDGETSIZE_MAX, which PySide does not export: no maximum height.
_UNBOUNDED = (1 << 24) - 1


def kind_tone(kind: str) -> str:
    """The tone a kind is coloured in: "ok", "warn", "err" or "mid"."""
    key = (kind or "").strip().casefold()
    return KIND_TONES.get(key) or KIND_ALIASES.get(key) or "mid"


def _time_text(value) -> str:
    """A line's time as the console writes it: `13:47:02`. None is now; a
    string is taken as written (a line read back from a file)."""
    if value is None:
        value = datetime.now()
    if isinstance(value, str):
        return value
    if isinstance(value, (datetime, date, int, float)):
        return fmt.clock(value, seconds=True)
    raise TypeError(f"a log line's time is a datetime, a timestamp or text, not {value!r}")


class LogLine(NamedTuple):
    time: str
    kind: str
    message: str
    tone: str

    def text(self) -> str:
        """The line as it is copied: `13:47:02  moved  1234567890 → myprojects`."""
        return f"{self.time}  {self.kind}  {self.message}"


# ---- the model --------------------------------------------------------------------

class LogModel(QAbstractListModel):
    """The last `cap` lines of a log, oldest first, in a ring.

    A list whose front is popped moves every line behind it; the ring moves
    none. `problems_changed(count)` follows the warn and err lines as they
    arrive and as they leave off the front.
    """

    problems_changed = Signal(int)

    def __init__(self, parent=None, *, cap: int = theme.LOG_CAP):
        super().__init__(parent)
        if cap < 1:
            raise ValueError(f"a LogModel keeps at least one line, not {cap}")
        self._cap = cap
        self._ring: list[LogLine | None] = [None] * cap
        self._start = 0
        self._size = 0
        self._problems = 0
        self._errors = 0

    # -- reading

    def cap(self) -> int:
        return self._cap

    def rowCount(self, parent=QModelIndex()) -> int:     # noqa: N802 - Qt's name
        return 0 if parent.isValid() else self._size

    def __len__(self) -> int:
        return self._size

    def line(self, row: int) -> LogLine:
        if not 0 <= row < self._size:
            raise IndexError(f"no line {row}; there are {self._size}")
        return self._ring[(self._start + row) % self._cap]

    def lines(self) -> list[LogLine]:
        return [self.line(row) for row in range(self._size)]

    def problem_count(self) -> int:
        """Warn and err lines still in the ring."""
        return self._problems

    def error_count(self) -> int:
        return self._errors

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < self._size:
            return None
        line = self.line(index.row())
        if role == Qt.DisplayRole:
            return line.text()
        if role == Qt.ToolTipRole:
            return line.message
        if role == TIME_ROLE:
            return line.time
        if role == KIND_ROLE:
            return line.kind
        if role == MESSAGE_ROLE:
            return line.message
        if role == TONE_ROLE:
            return line.tone
        return None

    # -- writing

    @staticmethod
    def make_line(time, kind: str, message: str) -> LogLine:
        kind = (kind or "info").strip().casefold()
        return LogLine(_time_text(time), kind, str(message), kind_tone(kind))

    def append(self, time, kind: str, message: str) -> LogLine:
        """Add a line at the end; past the cap, the oldest one goes first.
        `time` is a datetime, a timestamp, text, or None for now."""
        line = self.make_line(time, kind, message)
        self._take([line])
        return line

    def extend(self, lines: Iterable[tuple]) -> int:
        """Add many (time, kind, message) lines in one change, as reading a
        log back does: the view hears one insert, not one per line."""
        made = [self.make_line(*line) for line in lines]
        if made:
            self._take(made)
        return len(made)

    def clear(self) -> None:
        self.beginResetModel()
        self._ring = [None] * self._cap
        self._start = self._size = 0
        self._problems = self._errors = 0
        self.endResetModel()
        self.problems_changed.emit(0)

    def _take(self, made: list[LogLine]) -> None:
        before = self._problems
        made = made[-self._cap:]
        # the ones that must leave to make room, off the front
        leaving = max(0, self._size + len(made) - self._cap)
        if leaving:
            self.beginRemoveRows(QModelIndex(), 0, leaving - 1)
            for i in range(leaving):
                slot = (self._start + i) % self._cap
                self._count(self._ring[slot], -1)
                self._ring[slot] = None
            self._start = (self._start + leaving) % self._cap
            self._size -= leaving
            self.endRemoveRows()
        first = self._size
        self.beginInsertRows(QModelIndex(), first, first + len(made) - 1)
        for line in made:
            self._ring[(self._start + self._size) % self._cap] = line
            self._size += 1
            self._count(line, +1)
        self.endInsertRows()
        if self._problems != before:
            self.problems_changed.emit(self._problems)

    def _count(self, line: LogLine | None, step: int) -> None:
        if line is None:
            return
        if line.tone in PROBLEM_TONES:
            self._problems += step
        if line.tone == "err":
            self._errors += step


class ProblemsFilter(QSortFilterProxyModel):
    """The lines worth a second look: warn and err. Off, it passes everything,
    so one view serves both halves of All / Problems."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._only = False

    def problems_only(self) -> bool:
        return self._only

    def set_problems_only(self, on: bool) -> None:
        if bool(on) != self._only:
            self._only = bool(on)
            self.invalidateRowsFilter()

    def filterAcceptsRow(self, row: int, parent: QModelIndex) -> bool:   # noqa: N802
        if not self._only:
            return True
        return self.sourceModel().index(row, 0, parent).data(TONE_ROLE) in PROBLEM_TONES


# ---- the view ------------------------------------------------------------------------

class _Columns:
    """Where time, kind and message start, measured once for the console font."""

    def __init__(self):
        self.font = theme.font("type.mono")
        self.metrics = QFontMetricsF(self.font)
        self.time = self.metrics.horizontalAdvance("00:00:00")
        widest = max(self.metrics.horizontalAdvance(kind) for kind in KIND_WIDEST)
        self.kind = widest
        self.row = round(theme.line_height("type.mono"))


class _LineDelegate(QStyledItemDelegate):
    """A console line: its ground (zebra, selection), then the three columns."""

    def __init__(self, view: LogView):
        super().__init__(view)
        self._view = view
        self.columns = _Columns()

    def sizeHint(self, option, index) -> QSize:     # noqa: N802 - Qt's name
        return QSize(200, self.columns.row)

    def paint(self, painter: QPainter, option, index: QModelIndex) -> None:
        c = self.columns
        rect = QRectF(option.rect)
        if option.state & QStyle.State_Selected:
            painter.fillRect(rect, theme.color("console.selection"))
        elif index.row() % 2:
            painter.fillRect(rect, theme.color("console.rowAlt"))
        painter.save()
        painter.setFont(c.font)
        x = rect.left() + theme.LOG_ROW_PAD
        right = rect.right() - theme.LOG_ROW_PAD
        tone = index.data(TONE_ROLE) or "mid"
        painter.setPen(theme.color("console.dim"))
        painter.drawText(QRectF(x, rect.top(), c.time, rect.height()),
                         Qt.AlignLeft | Qt.AlignVCenter, index.data(TIME_ROLE) or "")
        x += c.time + theme.LOG_COLUMN_GAP
        kind = c.metrics.elidedText(index.data(KIND_ROLE) or "", Qt.ElideRight, c.kind)
        painter.setPen(theme.color(TONE_COLOURS[tone]))
        painter.drawText(QRectF(x, rect.top(), c.kind, rect.height()),
                         Qt.AlignLeft | Qt.AlignVCenter, kind)
        x += c.kind + theme.LOG_COLUMN_GAP
        # one line per event: a message that spans lines is drawn on one, and
        # its tool tip holds it as it was written
        message = " ".join((index.data(MESSAGE_ROLE) or "").split())
        width = max(0.0, right - x)
        painter.setPen(theme.color("console.text"))
        painter.drawText(QRectF(x, rect.top(), width, rect.height()),
                         Qt.AlignLeft | Qt.AlignVCenter,
                         c.metrics.elidedText(message, Qt.ElideRight, width))
        painter.restore()


class LogView(QListView):
    """The console's lines. It follows the newest one while you are at the
    bottom; scrolled up, it stays on what you are reading."""

    copy_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._follow = True
        self._shift = 0
        self._refiltering = False
        self._delegate = _LineDelegate(self)
        self.setItemDelegate(self._delegate)
        self.setUniformItemSizes(True)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setFrameShape(QListView.NoFrame)
        self.setMouseTracking(False)
        self.setAccessibleName("Log lines")
        bar = self.verticalScrollBar()
        bar.setSingleStep(self._delegate.columns.row)
        bar.valueChanged.connect(self._scrolled)
        bar.rangeChanged.connect(self._range)
        self._empty_text = "Nothing written yet"

    def setModel(self, model) -> None:          # noqa: N802 - Qt's name
        old = self.model()
        if old is not None:
            old.rowsAboutToBeRemoved.disconnect(self._leaving)
            old.rowsRemoved.disconnect(self._left)
        super().setModel(model)
        if model is not None:
            model.rowsAboutToBeRemoved.connect(self._leaving)
            model.rowsRemoved.connect(self._left)

    # -- following the newest line

    def following(self) -> bool:
        """True while the view sits at the bottom and moves with new lines."""
        return self._follow

    def scroll_to_end(self) -> None:
        self._follow = True
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _scrolled(self, value: int) -> None:
        # Whatever moved the bar — a wheel, a drag, a key, the range closing
        # in — the view follows exactly when it ends up at the bottom.
        self._follow = value >= self.verticalScrollBar().maximum()

    def _range(self, _low: int, high: int) -> None:
        if self._follow:
            self.verticalScrollBar().setValue(high)

    def refilter(self, change: Callable[[], None]) -> None:
        """Run a change of filter. The rows it takes away are not lines
        leaving the ring, and the reading position does not follow them."""
        self._refiltering = True
        try:
            change()
        finally:
            self._refiltering = False
            self._shift = 0

    def _leaving(self, parent, first: int, last: int) -> None:
        # Lines leaving the front of the ring move everything below them up;
        # a reader who scrolled up keeps the lines they were reading.
        if not self._follow and first == 0 and not self._refiltering:
            self._shift += (last - first + 1) * self._delegate.columns.row

    def _left(self, *_args) -> None:
        if self._shift:
            bar = self.verticalScrollBar()
            bar.setValue(max(0, bar.value() - self._shift))
            self._shift = 0
            self._follow = False

    # -- the rest

    def set_empty_text(self, text: str) -> None:
        self._empty_text = text
        self.viewport().update()

    def keyPressEvent(self, event) -> None:     # noqa: N802 - Qt's name
        if event.matches(QKeySequence.Copy):
            self.copy_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        super().paintEvent(event)
        model = self.model()
        if model is None or model.rowCount():
            return
        painter = QPainter(self.viewport())
        painter.setFont(theme.font("type.monoSm"))
        painter.setPen(theme.color("text.lo"))
        painter.drawText(QRectF(self.viewport().rect()), Qt.AlignCenter, self._empty_text)


# ---- the card ---------------------------------------------------------------------------

class _Console(QWidget):
    """The well the lines sit in: surface.console, a hairline above it."""

    def __init__(self, view: LogView, parent: QWidget | None = None):
        super().__init__(parent)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 1, 0, 0)
        column.addWidget(view)

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        painter = QPainter(self)
        painter.fillRect(QRectF(self.rect()), theme.color("surface.console"))
        painter.fillRect(QRectF(0, 0, self.width(), 1), theme.color("border.hairline"))


class _Badge(QWidget):
    """The count of problems on a collapsed panel: warn, or danger once an
    error is among them."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._text, self._tone = "", "warn"
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def text(self) -> str:
        return self._text

    def tone(self) -> str:
        return self._tone

    def set_count(self, problems: int, errors: int) -> None:
        self._text = fmt.counted(problems, "problem")
        self._tone = "danger" if errors else "warn"
        self.setAccessibleName(self._text)
        metrics = QFontMetricsF(theme.font("type.monoSm"))
        vertical, horizontal = theme.LOG_BADGE_PAD
        self.setFixedSize(round(metrics.horizontalAdvance(self._text) + 2 * horizontal + 0.5),
                          round(metrics.height() + 2 * vertical))
        self.update()

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        box = QRectF(self.rect())
        radius = box.height() / 2
        path = QPainterPath()
        path.addRoundedRect(box, radius, radius)
        painter.fillPath(path, theme.color(f"{self._tone}.soft"))
        painter.setPen(QPen(theme.color(f"{self._tone}.line"), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5), radius - 0.5, radius - 0.5)
        painter.setFont(theme.font("type.monoSm"))
        painter.setPen(theme.color(self._tone))
        painter.drawText(box, Qt.AlignCenter, self._text)


class _Chevron(IconButton):
    """The chevron that opens and closes the panel: down while closed, and
    turning to point up as the body opens."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__("chevD", "Show the log", parent)
        self.angle = 0.0

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        follow(self)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        colour = token(self._paint_box(painter))
        glyph = icons.pixmap("chevD", colour, self._glyph, self.devicePixelRatioF())
        painter.translate(self._side / 2, self._side / 2)
        painter.rotate(self.angle)
        painter.drawPixmap(QRectF(-self._glyph / 2, -self._glyph / 2, self._glyph, self._glyph),
                           glyph, QRectF(glyph.rect()))


class _Head(QWidget):
    """The header row; a click anywhere on it that no control takes opens or
    closes the panel."""

    clicked = Signal()

    def mousePressEvent(self, event) -> None:       # noqa: N802 - Qt's name
        event.setAccepted(event.button() == Qt.LeftButton)

    def mouseReleaseEvent(self, event) -> None:     # noqa: N802 - Qt's name
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class LogPanel(GlassPanel):
    """A job's log, as a card. Feed it with `append(time, kind, message)`.

        log = LogPanel("Log", file="rotator.log", on_open_folder=open_logs)
        log.set_live(True)                     # the dot pulses while the job runs
        log.append(datetime.now(), "moved", "1234567890 → myprojects")

    `set_expanded(False)` closes it to its header, where a badge counts the
    problems. `copy()` puts the selected lines on the clipboard, or every line
    shown when none is selected. `cap` is how many lines it keeps.
    """

    expanded_changed = Signal(bool)

    def __init__(self, title: str = "Log", parent: QWidget | None = None, *,
                 file: str = "", expanded: bool = True, cap: int = theme.LOG_CAP,
                 on_open_folder: Callable[[], None] | None = None,
                 note: str = FOOTER_NOTE, fill: bool = False):
        super().__init__(parent, padding="none")
        self._fill = False
        self._model = LogModel(self, cap=cap)
        self._filter = ProblemsFilter(self)
        self._filter.setSourceModel(self._model)
        self._on_open: Callable[[], None] | None = None
        self._console_height = theme.LOG_BODY
        self._t = 1.0 if expanded else 0.0
        self._expanded = expanded

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)

        self._head = _Head()
        self._head.setCursor(Qt.PointingHandCursor)
        self._head.clicked.connect(self.toggle)
        row = QHBoxLayout(self._head)
        vertical, horizontal = theme.LOG_HEAD_PAD
        row.setContentsMargins(horizontal, vertical, horizontal - theme.SP_4, vertical)
        row.setSpacing(theme.SP_10)
        self._title = label(title, "type.h3", "hi")
        self._dot = LiveDot("accent", live=False)
        self._dot.hide()
        self._file = Elided("", "type.monoSm", "lo")
        self._badge = _Badge()
        self._badge.hide()
        self._switch = SegmentedControl(("All", "Problems"))
        self._switch.changed.connect(lambda i: self.set_problems_only(i == 1))
        self._copy = IconButton("clipboard", "Copy the selected lines, or all of them")
        self._copy.clicked.connect(self.copy)
        self._chevron = _Chevron()
        self._chevron.clicked.connect(self.toggle)
        row.addWidget(self._title)
        row.addWidget(self._dot)
        row.addWidget(self._file, 1)
        row.addWidget(self._badge)
        row.addWidget(self._switch)
        row.addWidget(self._copy)
        row.addWidget(self._chevron)
        column.addWidget(self._head)

        self._body = QWidget()
        inside = QVBoxLayout(self._body)
        inside.setContentsMargins(0, 0, 0, 0)
        inside.setSpacing(0)
        self.view = LogView()
        self.view.setModel(self._filter)
        self.view.copy_requested.connect(self.copy)
        self._console = _Console(self.view)
        inside.addWidget(self._console, 1)
        self._footer = TableFooter(note)
        self._open = GhostButton("Open log folder", icon="folder", size="sm")
        self._open.clicked.connect(self._open_folder)
        self._footer.add_action(self._open)
        inside.addWidget(self._footer)
        column.addWidget(self._body)

        self._animation = QVariantAnimation(self)
        self._animation.setDuration(animations.SLOW)
        self._animation.setEasingCurve(animations.ease())
        self._animation.valueChanged.connect(self._step)
        self._animation.finished.connect(self._settled)
        self._model.problems_changed.connect(self._problems)
        self._copied = QTimer(self)
        self._copied.setSingleShot(True)
        self._copied.setInterval(animations.FLASH)
        self._copied.timeout.connect(lambda: self._copy.set_icon("clipboard"))

        self.set_file(file)
        self.set_open_folder(on_open_folder)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._apply(self._t)
        self._settled()
        if fill:
            self.set_fill(True)

    # -- lines

    def model(self) -> LogModel:
        return self._model

    def append(self, time, kind: str, message: str) -> LogLine:
        return self._model.append(time, kind, message)

    def extend(self, lines) -> int:
        return self._model.extend(lines)

    def clear(self) -> None:
        self._model.clear()

    def problem_count(self) -> int:
        return self._model.problem_count()

    def shown_lines(self) -> list[LogLine]:
        """The lines the console shows now: all of them, or the problems."""
        return [self._model.line(self._filter.mapToSource(self._filter.index(r, 0)).row())
                for r in range(self._filter.rowCount())]

    def problems_only(self) -> bool:
        return self._filter.problems_only()

    def set_problems_only(self, on: bool) -> None:
        self.view.refilter(lambda: self._filter.set_problems_only(on))
        if self._switch.current_index() != int(bool(on)):
            self._switch.set_current_index(int(bool(on)))
        self.view.set_empty_text("No warnings or errors" if on else "Nothing written yet")
        if self.view.following():
            self.view.scroll_to_end()

    def copy(self) -> str:
        """Put the selected lines on the clipboard, oldest first — or every
        line shown when none is selected. Returns what was copied."""
        rows = sorted(index.row() for index in self.view.selectionModel().selectedRows())
        if not rows:
            rows = range(self._filter.rowCount())
        lines = [self._model.line(self._filter.mapToSource(self._filter.index(r, 0)).row())
                 for r in rows]
        text = "\n".join(line.text() for line in lines)
        QApplication.clipboard().setText(text)
        if lines:
            self._copy.set_icon("check")
            self._copied.start()
        return text

    # -- the header

    def title(self) -> str:
        return self._title.text()

    def set_title(self, text: str) -> None:
        self._title.setText(text)

    def file_text(self) -> str:
        return self._file.text()

    def set_file(self, name: str | None, *, writing: bool = True) -> None:
        """"writing to rotator.log"; nothing when the job writes no file.
        `writing=False` names a file that is only being read back."""
        if not name:
            self._file.set_text("")
        else:
            self._file.set_text(f"writing to {name}" if writing else name)

    def live(self) -> bool:
        return self._dot.live()

    def set_live(self, on: bool = True) -> None:
        """The dot beside the title pulses while the panel's job runs."""
        self._dot.set_live(on)
        self._dot.setVisible(bool(on))

    def badge_text(self) -> str:
        """The collapsed header's count, "" when it shows none."""
        return self._badge.text() if self._badge.isVisibleTo(self) else ""

    def set_open_folder(self, callback: Callable[[], None] | None) -> None:
        """What "Open log folder" does; without it the button is not shown."""
        self._on_open = callback
        self._open.setVisible(callback is not None)

    def set_note(self, text: str) -> None:
        self._footer.set_text(text)

    def _open_folder(self) -> None:
        if self._on_open is not None:
            self._on_open()

    def _problems(self, _count: int = 0) -> None:
        self._badge.set_count(self._model.problem_count(), self._model.error_count())
        self._badge.setVisible(not self._expanded and self._model.problem_count() > 0)

    # -- open and closed

    def expanded(self) -> bool:
        return self._expanded

    def expansion(self) -> float:
        """0 closed, 1 open; in between while it moves."""
        return self._t

    def chevron_angle(self) -> float:
        return self._chevron.angle

    def toggle(self) -> None:
        self.set_expanded(not self._expanded)

    def set_expanded(self, on: bool) -> None:
        on = bool(on)
        if on == self._expanded:
            return
        self._expanded = on
        self._chevron.setToolTip("Hide the log" if on else "Show the log")
        self._switch.setVisible(on)
        self._copy.setVisible(on)
        self._body.show()
        self._problems()
        self._animation.stop()
        target = 1.0 if on else 0.0
        if animations.ENABLED and self.isVisible() and not self._fill:
            self._animation.setStartValue(self._t)
            self._animation.setEndValue(target)
            self._animation.start()
        else:
            self._apply(target)
            self._settled()
        self.expanded_changed.emit(on)

    def set_console_height(self, px: int) -> None:
        """The console's height when open, 232 px unless a page says otherwise."""
        self._console_height = max(0, int(px))
        self._apply(self._t)

    def fills(self) -> bool:
        return self._fill

    def set_fill(self, on: bool) -> None:
        """Take the height the layout gives, and keep the console open while
        collapsed (see the module doc). Off, the panel is as tall as its
        parts, and collapsed it is its header."""
        on = bool(on)
        if on == self._fill:
            return
        self._fill = on
        self._animation.stop()
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding if on else QSizePolicy.Fixed)
        self._apply(1.0 if self._expanded else 0.0)
        self._settled()
        self.updateGeometry()

    def footer_shown(self) -> bool:
        return self._footer.isVisibleTo(self)

    def body_height(self) -> int:
        """The body's full height, console and footer."""
        return self._console_height + self._footer.sizeHint().height()

    def _step(self, value) -> None:
        self._apply(float(value))

    def _apply(self, t: float) -> None:
        self._t = t
        if self._fill:
            # the layout decides the height; open or closed, the console takes
            # what the header (and, open, the footer) leave
            for part in (self._body, self._console):
                part.setMinimumHeight(0)
                part.setMaximumHeight(_UNBOUNDED)
            self._footer.setVisible(self._expanded)
        else:
            self._footer.setVisible(True)
            self._body.setFixedHeight(round(self.body_height() * t))
            self._console.setFixedHeight(self._console_height)
        self._chevron.angle = 180.0 * t
        self._chevron.update()

    def _settled(self) -> None:
        self._body.setVisible(self._t > 0 or self._fill)
        self._switch.setVisible(self._expanded)
        self._copy.setVisible(self._expanded)
        self._chevron.setToolTip("Hide the log" if self._expanded else "Show the log")
        self._problems()
