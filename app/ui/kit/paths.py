"""PathField: a folder, shown compactly and checked off the GUI thread.

Folders are set once, on the Settings page; the pages that use them show them
small. So the field has the design's four looks:

- **empty**: a field to type or paste a path into, a folder glyph, and
  "Browse…" at its end;
- **compact**: the folder glyph, the path elided *from the left*
  (`…\\projects\\myprojects`: the end of a path is the part that tells), a ✓
  once the folder is known to be there, and, when the field is editable, an
  IconButton that opens the folder picker;
- **invalid**: the edge and glyph in danger, and "folder not found" under it;
- **disabled**: all of it at 45 %.

It is also a drop target: a folder dragged from Explorer onto it is taken.

**Nothing here touches the disk on the GUI thread.** Whether the folder
exists is asked on a worker (`os.stat` on W:, a hard disk that may be asleep,
can take seconds), and the answer arrives as `validity_changed`. Until it
does the field shows the path without a mark. `path_changed` says the user
chose a folder (typed, browsed or dropped); `set_path` from code does not
emit it.
"""
from __future__ import annotations

import os
import stat

from PySide6.QtCore import (
    QEvent, QMargins, QObject, QPointF, QRectF, QRunnable, QSize, Qt, QThreadPool, Signal,
)
from PySide6.QtGui import QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QAbstractButton, QFileDialog, QLineEdit, QSizePolicy, QWidget

from ... import theme
from . import icons
from .base import Caster, Interactive, follow
from .buttons import IconButton

PATH_STATES = ("empty", "checking", "valid", "invalid")

_pool: QThreadPool | None = None


def _checks() -> QThreadPool:
    """Where folders are checked: two threads, whatever asks."""
    global _pool
    if _pool is None:
        _pool = QThreadPool()
        _pool.setMaxThreadCount(2)
    return _pool


def is_folder(path: str) -> bool:
    """Whether `path` is a folder that is there. Touches the disk: workers only."""
    try:
        return stat.S_ISDIR(os.stat(path).st_mode)
    except (OSError, ValueError):
        return False


class _Answer(QObject):
    """Carries a check's answer back to the GUI thread."""
    done = Signal(int, bool)


class _Check(QRunnable):
    def __init__(self, answer: _Answer, generation: int, path: str):
        super().__init__()
        self.answer, self.generation, self.path = answer, generation, path

    def run(self) -> None:
        ok = is_folder(self.path)
        try:
            self.answer.done.emit(self.generation, ok)
        except RuntimeError:
            pass        # the field went while the disk was answering


class _Link(QAbstractButton):
    """"Browse…": words in the accent, a button to the keyboard and a reader."""

    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setText(text)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.TabFocus)
        self.setAttribute(Qt.WA_Hover)
        width = QFontMetricsF(theme.font("type.caption")).horizontalAdvance(text)
        self.setFixedSize(round(width + 1), theme.CONTROL_HEIGHT - 2)

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        painter = QPainter(self)
        painter.setFont(theme.font("type.caption"))
        tone = ("text.disabled" if not self.isEnabled()
                else "accent" if self.underMouse() or self.isDown() else "accent.hover")
        painter.setPen(theme.color(tone))
        painter.drawText(QRectF(self.rect()), Qt.AlignLeft | Qt.AlignVCenter, self.text())
        if self.hasFocus():
            painter.fillRect(QRectF(0, self.height() - 7, self.width(), 1), theme.color(tone))


class PathField(Interactive, Caster, QWidget):
    """A folder. `set_path(path)` shows it and checks it; `path()`, `valid()`
    (True, False, or None while unknown) and `variant()` read it back.

        field = PathField(placeholder="Choose a folder of videos")
        field.path_changed.connect(settings.save_source)
    """

    path_changed = Signal(str)
    validity_changed = Signal(object)

    def __init__(self, path: str = "", parent: QWidget | None = None, *,
                 placeholder: str = "Choose a folder", editable: bool = True,
                 dialog_title: str = "Choose a folder"):
        QWidget.__init__(self, parent)
        self._path = ""
        self._valid: bool | None = None
        self._generation = 0
        self._editable = editable
        self._dialog_title = dialog_title
        self._dragging = False
        self._answer = _Answer(self)
        self._answer.done.connect(self._checked)
        self.setAcceptDrops(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAccessibleName(placeholder)

        self._edit = QLineEdit(self)
        self._edit.setFrame(False)
        self._edit.setPlaceholderText(placeholder)
        self._edit.setFont(theme.font("type.monoSm"))
        self._edit.editingFinished.connect(self._typed)
        self._edit.installEventFilter(self)
        self._browse = _Link("Browse…", self)
        self._browse.clicked.connect(self.browse)
        self._change = IconButton("folder", "Choose another folder", self, size="sm")
        self._change.clicked.connect(self.browse)
        self._layout()
        self.set_path(path)

    # -- the path and what is known about it

    def path(self) -> str:
        return self._path

    def valid(self) -> bool | None:
        return self._valid

    def path_state(self) -> str:
        """"empty", "checking", "valid" or "invalid"."""
        if not self._path:
            return "empty"
        return {None: "checking", True: "valid", False: "invalid"}[self._valid]

    def variant(self) -> str:
        """The design's name for what it looks like: "empty", "compact" or "invalid"."""
        state = self.path_state()
        return state if state in ("empty", "invalid") else "compact"

    def set_path(self, path: str | os.PathLike | None) -> None:
        """Show this folder and check, on a worker, that it is there."""
        path = os.fspath(path) if path else ""
        self._path = path
        self._generation += 1
        self._edit.clear()
        self._set_valid(None, announce=bool(path))
        if path:
            _checks().start(_Check(self._answer, self._generation, path))
        self.setToolTip(path)
        self._layout()

    def _checked(self, generation: int, ok: bool) -> None:
        if generation == self._generation and self._path:
            self._set_valid(ok)

    def _set_valid(self, valid: bool | None, announce: bool = True) -> None:
        changed = valid != self._valid
        self._valid = valid
        height = theme.CONTROL_HEIGHT + (theme.FIELD_ERROR_HEIGHT if valid is False else 0)
        self.setFixedHeight(height)
        self.setAccessibleDescription({None: "", True: "folder found",
                                       False: "folder not found"}[valid])
        self._layout()
        self.update()
        if changed and announce:
            self.validity_changed.emit(valid)

    # -- choosing a folder

    def browse(self) -> None:
        """Open the folder picker (the user asked; the dialog is Windows')."""
        if not self._editable or not self.isEnabled():
            return
        chosen = QFileDialog.getExistingDirectory(self.window(), self._dialog_title, self._path)
        if chosen:
            self._chosen(os.path.normpath(chosen))

    def _typed(self) -> None:
        text = self._edit.text().strip().strip('"')
        if text and text != self._path:
            self._chosen(text)

    def _chosen(self, path: str) -> None:
        self.set_path(path)
        self.path_changed.emit(path)

    # drops: a folder from Explorer

    @staticmethod
    def _dropped_path(event) -> str:
        mime = event.mimeData()
        if not mime.hasUrls():
            return ""
        for url in mime.urls():
            if url.isLocalFile():
                return os.path.normpath(url.toLocalFile())
        return ""

    def dragEnterEvent(self, event) -> None:    # noqa: N802 - Qt's name
        if self._editable and self.isEnabled() and self._dropped_path(event):
            event.acceptProposedAction()
            self._dragging = True
            self.state_changed()
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:    # noqa: N802 - Qt's name
        self._dragging = False
        self.state_changed()

    def dropEvent(self, event) -> None:         # noqa: N802 - Qt's name
        self._dragging = False
        path = self._dropped_path(event)
        if path:
            event.acceptProposedAction()
            self._chosen(path)
        self.state_changed()

    # -- keys and focus

    def keyPressEvent(self, event) -> None:     # noqa: N802 - Qt's name
        if self._path and event.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            self.browse()
            event.accept()
            return
        super().keyPressEvent(event)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt's name
        if watched is self._edit and event.type() in (QEvent.FocusIn, QEvent.FocusOut):
            self.state_changed()
        return False

    def focus_visible(self) -> bool:
        if not self.isEnabled():
            return False
        if self._kit_force is not None:
            return self._kit_force == "focus"
        return self._edit.hasFocus() or (self.hasFocus() and self._kit_keyboard) or self._dragging

    # -- geometry

    def sizeHint(self) -> QSize:                # noqa: N802 - Qt's name
        return QSize(240, self.height() or theme.CONTROL_HEIGHT)

    def minimumSizeHint(self) -> QSize:         # noqa: N802 - Qt's name
        return QSize(120, self.height() or theme.CONTROL_HEIGHT)

    def _box(self) -> QRectF:
        return QRectF(0, 0, self.width(), theme.CONTROL_HEIGHT)

    def _text_left(self) -> float:
        return 1 + theme.PATH_PAD[1] + theme.PATH_ICON + theme.PATH_GAP

    def _text_right(self) -> float:
        """Where the path's words must end: before the ✓ and the button."""
        right = self.width() - 1 - theme.PATH_PAD[1]
        if self.variant() == "empty":
            return right - self._browse.width() - theme.PATH_GAP
        if self._editable:
            right = self._change.geometry().left() - theme.SP_4
        if self._valid:
            right -= theme.PATH_MARK + theme.PATH_GAP
        return right

    def _layout(self) -> None:
        empty = not self._path
        self.setFocusPolicy(Qt.NoFocus if empty else (Qt.StrongFocus if self._editable
                                                      else Qt.NoFocus))
        self._edit.setVisible(empty)
        self._browse.setVisible(empty and self._editable)
        self._edit.setReadOnly(not self._editable)
        self._change.setVisible(not empty and self._editable)
        height = theme.CONTROL_HEIGHT
        right = self.width() - 1 - theme.PATH_PAD[1]
        self._browse.move(round(right - self._browse.width()), (height - self._browse.height()) // 2)
        side = self._change.width()
        inset = (height - side) // 2
        self._change.move(self.width() - inset - side, inset)
        left = round(self._text_left())
        edit_right = (right - self._browse.width() - theme.PATH_GAP) if self._editable else right
        self._edit.setGeometry(left, 1, max(0, round(edit_right - left)), height - 2)

    def resizeEvent(self, event) -> None:       # noqa: N802 - Qt's name
        super().resizeEvent(event)
        self._layout()

    # -- painting

    def outside_margins(self) -> QMargins:
        ring = theme.FOCUS_RING
        return QMargins(ring, ring, ring, ring)

    def paint_outside(self, painter: QPainter) -> None:
        if self.focus_visible():
            theme.paint_focus_ring(painter, self._box(), theme.R_MD)

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        follow(self)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if not self.isEnabled():
            painter.setOpacity(theme.DISABLED_FIELD_OPACITY)
        box = self._box()
        invalid = self._valid is False and bool(self._path)
        path = QPainterPath()
        path.addRoundedRect(box, theme.R_MD, theme.R_MD)
        painter.fillPath(path, theme.color("surface.well"))
        theme.paint_shadow(painter, box, "elev.inset", theme.R_MD)
        if invalid:
            edge = "danger"
        elif self.focus_visible():
            edge = "border.focus"
        elif self.visual_state() == "hover":
            edge = "border.strong"
        else:
            edge = "border.control"
        painter.setPen(QPen(theme.color(edge), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5), theme.R_MD - 0.5, theme.R_MD - 0.5)

        dpr = self.devicePixelRatioF()
        middle = box.center().y()
        size = theme.PATH_ICON
        glyph_x = 1 + theme.PATH_PAD[1]
        painter.drawPixmap(QPointF(glyph_x, middle - size / 2),
                           icons.pixmap("folder", "danger" if invalid else "text.lo", size, dpr))
        if self._path:
            left, right = self._text_left(), self._text_right()
            font = theme.font("type.monoSm")
            text = QFontMetricsF(font).elidedText(self._path, Qt.ElideLeft, max(0.0, right - left))
            painter.setFont(font)
            painter.setPen(theme.color("text.body"))
            painter.drawText(QRectF(left, box.top(), right - left, box.height()),
                             Qt.AlignLeft | Qt.AlignVCenter, text)
            if self._valid:
                mark = theme.PATH_MARK
                painter.drawPixmap(QPointF(right + theme.PATH_GAP, middle - mark / 2),
                                   icons.pixmap("check", "ok", mark, dpr))
        if invalid:
            self._paint_error(painter, box)

    def _paint_error(self, painter: QPainter, box: QRectF) -> None:
        """⚠ folder not found, in danger, under the field."""
        size = theme.PATH_MARK
        top = box.bottom() + theme.FIELD_ERROR_GAP
        line = self.height() - top
        painter.drawPixmap(QPointF(1, top + (line - size) / 2),
                           icons.pixmap("warn", "danger", size, self.devicePixelRatioF()))
        painter.setFont(theme.font("type.caption"))
        painter.setPen(theme.color("danger"))
        left = 1 + size + theme.SP_4 + 1
        painter.drawText(QRectF(left, top, self.width() - left, line),
                         Qt.AlignLeft | Qt.AlignVCenter, "folder not found")
