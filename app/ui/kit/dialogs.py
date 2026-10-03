"""Dialogs: ConfirmDialog and FormDialog, the two ways the toolkit asks
before it acts.

Nothing writes without showing what it will write (plan §2.6). A confirmation
names the work: the steps a run will take, in order, or the very folders a
delete will remove, grouped by why, each with its size, and a footer that
counts what is ticked — "9 selected · 0 B" — while the button says the same
number ("Delete 9 permanently").

Both draw the same chrome: a `scrim` over the whole window they belong to,
and a frameless panel on `surface.overlay` at elev.3 with `r.xl` corners. The
head is an icon tile, the title (`type.dialog`, 14 px) and the body; the
footer holds a summary on the left and the buttons on the right. They
appear at full opacity with no motion: a dialog is where the eye must land
at once (plan §3.4, what does not move).

**ConfirmDialog** is neutral (an AccentButton goes ahead) or destructive (a
DangerButton does). In a destructive one the default button, and the one
focused when it opens, is Cancel: Enter and a stray Space cancel, and only
a deliberate click deletes. Esc cancels either kind.

    result = ConfirmDialog(
        "Delete 9 unusable folders?",
        "Wallpaper Engine cannot list a folder without a project.json. "
        "Deleting is permanent.",
        window, destructive=True, icon="trash",
        groups=[CheckGroup("Safe to delete", safe_rows, tone="ok"),
                CheckGroup("Hold media", media_rows, tone="warn",
                           initially_checked=False)],
        confirm_text=lambda rows: f"Delete {len(rows)} permanently",
        actions=[("Open folder", open_reserve)],
    ).ask()
    if result:                              # accepted
        delete(row.data for row in result.checked)

- `steps=[...]` lists what will happen, numbered (frame 08): each a title,
  or (title, caption).
- `groups=[CheckGroup(...)]` is a checklist (frame 09). A group's header row
  has a tri-state box that ticks or clears the whole group, and its title in
  overline type in the group's tone with its count and size ("SAFE TO DELETE
  — 9 FOLDERS · 0 B"). A row is a box, a mono name, the reason and a
  right-aligned size. A group longer than `limit` rows (5) shows that many
  and "N more like these", which shows the rest. `initially_checked` says
  whether a group starts ticked.
- `summary(rows) -> str` writes the footer's count from the ticked rows; by
  default "N selected", and the size when the rows have sizes.
- `confirm_text` is words, or a function of the ticked rows. With a
  checklist, the button is off while nothing is ticked.
- `actions=[(text, callback)]` are GhostButtons that do not close it.
- `lines=[...]` lists what a write will do, one mono line each, in a well:
  the first `CONFIRM_LINES` (14) and "… and N more".
- `note=(tone, text)` is a Callout under the rest: what to know before
  saying yes ("3 of these come from lists read without a Steam key…").
- `safe_default=True` makes Cancel the default and the first focus of a
  neutral confirmation too — for one whose consequence cannot be undone
  later, though it deletes nothing.
- `ask()` returns a ConfirmResult: true when confirmed, with `checked`, the
  rows ticked then — and no rows at all when cancelled.

**FormDialog** is a few settings: labelled rows (an overline label, the
field, a note under it), Cancel and Save, and Save is off until the form is
valid. `add_row(label, field, note, check=…, required=…)` adds one;
`check(field)` returns None when the field is right and what is wrong with it
otherwise (False for "not yet", without a message), and says so once the
field has been touched. `set_check(fn)` checks the form as a whole.
`add_widget(w)` puts anything else between the rows (a note with an icon);
`add_action(text, callback)` a GhostButton in the footer that does not close
it ("Authors database…"). `ask()` returns True when saved.

`embedded=True` makes either an ordinary child widget with no scrim, its
shadow drawn by the surface behind: how the kit preview shows them open.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Sequence

from PySide6.QtCore import QEvent, QMargins, QRect, QRectF, Qt, SignalInstance
from PySide6.QtGui import QPainter, QPainterPath, QPen, QRegion
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFrame, QHBoxLayout, QLineEdit, QScrollArea, QSizePolicy, QVBoxLayout,
    QWidget,
)

from ... import theme
from . import base, icons
from . import format as fmt
from .base import Caster, Elided, alive, elevation_margins, follow, label, set_tone
from .buttons import AccentButton, DangerButton, GhostButton, LinkButton, SecondaryButton
from .inputs import TextInput
from .panels import Callout, Overline
from .selection import Checkbox

# ---- what a checklist is made of --------------------------------------------------------


@dataclass(frozen=True)
class CheckRow:
    """One thing a confirmation would act on: a mono `name`, the `reason` it
    is listed, its `size` in bytes (None when not measured) and whatever the
    page needs back (`data`)."""

    name: str
    reason: str = ""
    size: int | None = None
    data: Any = None


@dataclass(frozen=True)
class CheckGroup:
    """Rows listed for one reason, under a header that ticks them all.
    `tone` tints the header's band and colours its `note` and the rows' reasons
    ("ok", "warn", "danger", "info" or "neutral"); `noun` and `plural` name
    what the rows are in its count."""

    title: str
    rows: Sequence[CheckRow] = ()
    tone: str = "neutral"
    initially_checked: bool = True
    noun: str = "folder"
    plural: str | None = None
    limit: int = theme.CHECK_LIMIT
    note: str = ""          # at the header's right, in its tone: "no media inside"


@dataclass(frozen=True)
class ConfirmResult:
    """What a ConfirmDialog was answered with. True when confirmed; `checked`
    holds the rows ticked then, and is empty when it was cancelled."""

    accepted: bool
    checked: tuple[CheckRow, ...] = ()

    def __bool__(self) -> bool:
        return self.accepted


# group tone → the header's label tone
GROUP_TONES = {"neutral": "lo", "ok": "ok", "warn": "warn", "danger": "danger", "info": "info"}
# group tone → its header's band
GROUP_BANDS = {"neutral": "surface.subtle", "ok": "ok.band", "warn": "warn.soft",
               "danger": "danger.soft", "info": "info.soft"}
# tile tone → (ground, edge, icon colour)
TILE_TONES: dict[str, tuple[str, str, str]] = {
    "neutral": ("surface.tile", "border.hairline", "text.mid"),
    "accent": ("accent.soft", "accent.line", "accent.hover"),
    "info": ("info.soft", "info.line", "info"),
    "ok": ("ok.soft", "ok.line", "ok"),
    "warn": ("warn.soft", "warn.line", "warn"),
    "danger": ("danger.soft", "danger.line", "danger"),
}


def sizes_text(rows: Sequence[CheckRow]) -> str:
    """The rows' total size: `1.1 GB`. When some were not measured it is only
    a floor, and says so (`at least 12 KB`); when none were, nothing."""
    known = [row.size for row in rows if row.size is not None]
    if not rows:
        return fmt.size(0)
    if not known:
        return ""
    total = fmt.size(sum(known))
    return total if len(known) == len(rows) else f"at least {total}"


def default_summary(rows: Sequence[CheckRow], sized: bool = True) -> str:
    """"9 selected · 0 B"; without the size when the rows carry none."""
    words = f"{fmt.count(len(rows))} selected"
    size = sizes_text(rows) if sized else ""
    return f"{words} · {size}" if size else words


# ---- the chrome ---------------------------------------------------------------------------

class _Panel(QFrame):
    """The dialog's panel: surface.overlay, the sheen, a hairline edge, r.xl.
    A surface, so its buttons draw their shadows and rings on it."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        base.declare(self)

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        box = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(box, theme.R_XL, theme.R_XL)
        painter.fillPath(path, theme.color("surface.overlay"))
        theme.paint_sheen(painter, box, theme.R_XL, "elev.3")
        painter.setPen(QPen(theme.color("border.control"), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5), theme.R_XL - 0.5, theme.R_XL - 0.5)
        base.paint(painter, self, event.rect())


class _Footer(QWidget):
    """The panel's foot: the summary on the left and the buttons on the right,
    on the panel's own ground (the design draws no strip or line here)."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        base.declare(self)              # the buttons' shadows and rings go over the panel
        self.row = QHBoxLayout(self)
        above, below = theme.DIALOG_FOOTER_PAD
        side = theme.DIALOG_PAD[1]
        self.row.setContentsMargins(side, above, side, below)
        self.row.setSpacing(theme.SP_8)
        self.summary = Elided("", "type.monoSm", "lo")
        self.summary.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.row.addWidget(self.summary, 1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(theme.SP_6)
        self.row.addLayout(self.actions)
        self.buttons = QHBoxLayout()
        self.buttons.setSpacing(theme.SP_8)
        self.row.addLayout(self.buttons)

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        painter = QPainter(self)
        base.paint(painter, self, event.rect())


class _Tile(QWidget):
    """The rounded square the dialog's icon sits in, tinted by its tone."""

    def __init__(self, icon: str, tone: str, parent: QWidget | None = None):
        super().__init__(parent)
        if tone not in TILE_TONES:
            raise KeyError(f"no tile tone {tone!r}; there are {', '.join(TILE_TONES)}")
        icons.svg(icon)
        self.icon, self.tone = icon, tone
        self.setFixedSize(theme.DIALOG_TILE, theme.DIALOG_TILE)

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        ground, edge, colour = TILE_TONES[self.tone]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        box = QRectF(self.rect())
        radius = theme.DIALOG_TILE_RADIUS
        path = QPainterPath()
        path.addRoundedRect(box, radius, radius)
        painter.fillPath(path, theme.color(ground))
        painter.setPen(QPen(theme.color(edge), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5), radius - 0.5, radius - 0.5)
        size = theme.DIALOG_ICON
        painter.drawPixmap(round((self.width() - size) / 2), round((self.height() - size) / 2),
                           icons.pixmap(self.icon, colour, size, self.devicePixelRatioF()))


class OverlayDialog(Caster, QDialog):
    """The chrome both dialogs share: the scrim over the window, the panel in
    its middle, the head, a column for the body, and the footer.

    It is a frameless window as large as the window it belongs to, which it
    covers and follows; without a parent it is the panel alone, centred on
    the screen. `embedded=True` makes it an ordinary child widget instead.
    """

    def __init__(self, parent: QWidget | None = None, *, width: int = theme.DIALOG_WIDTH,
                 embedded: bool = False):
        QDialog.__init__(self, parent)
        self._embedded = embedded
        self._owner: QWidget | None = None
        self._initial: QWidget | None = None
        if embedded:
            self.setWindowFlags(Qt.Widget)
        else:
            self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
            self.setAttribute(Qt.WA_TranslucentBackground)
            self.setModal(True)
        self.panel = _Panel(self)
        self.panel.setFixedWidth(width)
        outer = QVBoxLayout(self.panel)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._content = QWidget()
        self.body_column = QVBoxLayout(self._content)
        pad_v, pad_h = theme.DIALOG_PAD
        self.body_column.setContentsMargins(pad_h, pad_v, pad_h, 0)
        self.body_column.setSpacing(theme.DIALOG_GAP)
        outer.addWidget(self._content, 1)
        self.footer = _Footer()
        outer.addWidget(self.footer)
        self._title: QWidget | None = None
        self._body: QWidget | None = None
        self._tile: _Tile | None = None

    # -- the head

    def _set_head(self, title: str, body: str, icon: str | None, tone: str) -> None:
        head = QWidget()
        row = QHBoxLayout(head)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.DIALOG_HEAD_GAP)
        if icon:
            self._tile = _Tile(icon, tone)
            row.addWidget(self._tile, 0, Qt.AlignTop)
        words = QVBoxLayout()
        words.setSpacing(theme.SP_6)
        self._title = label(title, "type.dialog", "hi")
        self._title.setWordWrap(True)
        self._body = label(body, "type.bodySm", "mid")
        self._body.setWordWrap(True)
        self._body.setVisible(bool(body))
        words.addWidget(self._title)
        words.addWidget(self._body)
        row.addLayout(words, 1)
        self.body_column.addWidget(head)
        self.setWindowTitle(title)
        self.setAccessibleName(title)

    def title(self) -> str:
        return self._title.text() if self._title is not None else ""

    def body(self) -> str:
        return self._body.text() if self._body is not None else ""

    def tile(self) -> tuple[str, str] | None:
        """The head's icon and tone, or None when it has no tile."""
        return (self._tile.icon, self._tile.tone) if self._tile is not None else None

    def is_embedded(self) -> bool:
        return self._embedded

    # -- where it goes

    def _panel_height(self, room: int) -> int:
        layout = self.panel.layout()
        width = self.panel.width()
        wanted = (layout.totalHeightForWidth(width) if layout.hasHeightForWidth()
                  else layout.totalSizeHint().height())
        return max(layout.totalMinimumSize().height(), min(wanted, room))

    def place(self) -> None:
        """Cover the window and centre the panel in it; again whenever what
        the panel holds changes size."""
        width = self.panel.width()
        if self._embedded:
            height = self._panel_height(10_000)
            self.panel.setGeometry(0, 0, width, height)
            self.setFixedSize(width, height)
            return
        owner = self._owner_window()
        if owner is not None:
            area = QRect(owner.mapToGlobal(owner.rect().topLeft()), owner.size())
            room = area.height() - 2 * theme.DIALOG_SCREEN_MARGIN
            height = self._panel_height(room)
            self.setGeometry(area)
            self.panel.setGeometry((area.width() - width) // 2, (area.height() - height) // 2,
                                   width, height)
        else:
            left, top, right, bottom = theme.shadow_reach("elev.3")
            screen = self.screen().availableGeometry()
            height = self._panel_height(screen.height() - 2 * theme.DIALOG_SCREEN_MARGIN)
            size = (width + left + right, height + top + bottom)
            self.setGeometry(screen.left() + (screen.width() - size[0]) // 2,
                             screen.top() + (screen.height() - size[1]) // 2, *size)
            self.panel.setGeometry(left, top, width, height)
        self.update()

    def _owner_window(self) -> QWidget | None:
        parent = self.parentWidget()
        return parent.window() if parent is not None else None

    def focusNextPrevChild(self, next: bool) -> bool:     # noqa: N802 - Qt's name
        # rows built since it opened (a checklist's "N more") take their place
        base.chain_tabs(base.tab_stops(self.panel))
        return super().focusNextPrevChild(next)

    def setVisible(self, visible: bool) -> None:    # noqa: N802 - Qt's name
        if visible:
            # placed before it is mapped, so it never shows where Qt would have put it
            self.place()
            # Tab goes down the dialog as it reads: its body, then the footer's
            # buttons; the footer was made first
            base.chain_tabs(base.tab_stops(self.panel))
            owner = None if self._embedded else self._owner_window()
            if owner is not None and self._owner is None:
                owner.installEventFilter(self)
                self._owner = owner
        elif self._owner is not None:
            if alive(self._owner):
                self._owner.removeEventFilter(self)
            self._owner = None
        super().setVisible(visible)
        if visible and self._initial is not None:
            self._initial.setFocus(Qt.OtherFocusReason)

    def eventFilter(self, watched, event) -> bool:      # noqa: N802 - Qt's name
        if watched is self._owner and event.type() in (QEvent.Move, QEvent.Resize):
            self.place()
        return False

    def initial_focus(self) -> QWidget | None:
        """The control that has focus when the dialog opens."""
        return self._initial

    # -- answering

    def ask(self):
        """Show it, wait for the answer, and return it."""
        self.exec()
        return self.result_value()

    def result_value(self):
        raise NotImplementedError

    def done(self, code: int) -> None:
        self._answer(code == QDialog.Accepted)
        if self._embedded:
            self.setResult(code)            # a specimen on a page stays where it is
            return
        super().done(code)

    def _answer(self, accepted: bool) -> None:
        pass

    # -- painting

    def outside_margins(self) -> QMargins:
        return elevation_margins("elev.3") if self._embedded else QMargins()

    def paint_outside(self, painter: QPainter) -> None:
        theme.paint_shadow(painter, QRectF(self.rect()), "elev.3", theme.R_XL)

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        follow(self)
        if self._embedded:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if self._owner_window() is not None:
            painter.fillRect(QRectF(self.rect()), theme.color("scrim"))
        theme.paint_shadow(painter, QRectF(self.panel.geometry()), "elev.3", theme.R_XL)


# ---- numbered steps (frame 08) ---------------------------------------------------------------

def paint_well(painter: QPainter, widget: QWidget) -> None:
    """A dialog's well: surface.well, a hairline, and the inset shade at its top."""
    painter.setRenderHint(QPainter.Antialiasing)
    box = QRectF(widget.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
    radius = theme.DIALOG_WELL_RADIUS
    path = QPainterPath()
    path.addRoundedRect(box, radius, radius)
    painter.fillPath(path, theme.color("surface.well"))
    theme.paint_shadow(painter, box, "elev.inset", radius)
    painter.setPen(QPen(theme.color("border.hairline"), 1))
    painter.setBrush(Qt.NoBrush)
    painter.drawRoundedRect(box, radius, radius)


class _Steps(QWidget):
    """What a confirmed run will do, in order, in a well: 1, 2, 3…"""

    def __init__(self, steps, parent: QWidget | None = None):
        super().__init__(parent)
        column = QVBoxLayout(self)
        pad_v, pad_h = theme.DIALOG_WELL_PAD
        column.setContentsMargins(pad_h, pad_v, pad_h, pad_v)
        column.setSpacing(theme.DIALOG_STEP_GAP)
        self.titles: list[str] = []
        for n, step in enumerate(steps, start=1):
            title, caption = (step, "") if isinstance(step, str) else (step[0], step[1] if len(step) > 1 else "")
            row = QHBoxLayout()
            row.setSpacing(theme.DIALOG_STEP_NUMBER_GAP)
            number = label(str(n), "type.monoXs", "lo")
            number.setFixedWidth(theme.DIALOG_STEP_NUMBER)
            row.addWidget(number, 0, Qt.AlignTop)
            words = QVBoxLayout()
            words.setSpacing(theme.SP_2)
            name = label(title, "type.label", "body")
            name.setWordWrap(True)
            words.addWidget(name)
            if caption:
                note = label(caption, "type.monoXs", "lo")
                note.setWordWrap(True)
                words.addWidget(note)
            row.addLayout(words, 1)
            column.addLayout(row)
            self.titles.append(title)

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        paint_well(QPainter(self), self)


# ---- the checklist (frame 09) -----------------------------------------------------------------

class _GroupCheck(Checkbox):
    """A group's box. Its third state is only ever shown: a click on a group
    that is partly ticked ticks all of it, as a click on a clear one does."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__("", parent, tristate=True)

    def nextCheckState(self) -> None:           # noqa: N802 - Qt's name
        self.setCheckState(Qt.Unchecked if self.checkState() == Qt.Checked else Qt.Checked)


class _Row(QWidget):
    """A checklist row: the box, the name in mono over why it is listed, and
    its size. A click anywhere on it ticks it."""

    def __init__(self, row: CheckRow, tone: str, sized: bool, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_Hover)
        line = QHBoxLayout(self)
        vertical, horizontal = theme.CHECK_ROW_PAD
        line.setContentsMargins(horizontal, vertical, horizontal, vertical)
        line.setSpacing(theme.CHECK_ROW_GAP)
        self.box = Checkbox("")
        self.box.setAccessibleName(row.name)
        self.box.setAccessibleDescription(row.reason)
        words = QVBoxLayout()
        words.setSpacing(theme.CHECK_LINE_GAP)
        name = Elided(row.name, "type.mono", "body", mode=Qt.ElideMiddle)
        name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        words.addWidget(name)
        if row.reason:
            # the reason in the group's hue where that is a warning, else quiet
            reason = Elided(row.reason, "type.caption", tone if tone in ("warn", "danger") else "lo")
            reason.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            words.addWidget(reason)
        line.addWidget(self.box)
        line.addLayout(words, 1)
        if sized:
            size = Elided("" if row.size is None else fmt.size(row.size), "type.monoSm",
                          tone if tone in ("warn", "danger") else "lo", align=Qt.AlignRight)
            size.setFixedWidth(theme.CHECK_SIZE_WIDTH)
            line.addWidget(size)
        self._hot = False

    def mouseReleaseEvent(self, event) -> None:     # noqa: N802 - Qt's name
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.box.click()
        super().mouseReleaseEvent(event)

    def event(self, event) -> bool:
        if event.type() in (QEvent.HoverEnter, QEvent.HoverLeave):
            self._hot = event.type() == QEvent.HoverEnter
            self.update()
        return super().event(event)

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        painter = QPainter(self)
        if self._hot:
            painter.fillRect(QRectF(self.rect()), theme.color("surface.rowHover"))
        painter.fillRect(QRectF(0, self.height() - 1, self.width(), 1), theme.color("border.faint"))


class _Band(QWidget):
    """A group's header: its tone's band, a hairline under it."""

    def __init__(self, tone: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.tone = tone

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        painter = QPainter(self)
        painter.fillRect(QRectF(self.rect()), theme.color(GROUP_BANDS[self.tone]))
        painter.fillRect(QRectF(0, self.height() - 1, self.width(), 1), theme.color("border.hairline"))


class _Checklist(QFrame):
    """The groups, in a well that scrolls past `CHECKLIST_MAX`. It holds the
    ticks for every row, built or not: a group of 3 000 builds its first
    five rows, and ticking the group ticks all 3 000 without building one."""

    def __init__(self, groups: Sequence[CheckGroup], changed: Callable[[], None],
                 resized: Callable[[], None], parent: QWidget | None = None):
        super().__init__(parent)
        self.groups = list(groups)
        for group in self.groups:
            if group.tone not in GROUP_TONES:
                raise KeyError(f"no group tone {group.tone!r}; there are {', '.join(GROUP_TONES)}")
        self._changed, self._resized = changed, resized
        self.ticks = [[bool(g.initially_checked)] * len(g.rows) for g in self.groups]
        self.sized = any(row.size is not None for g in self.groups for row in g.rows)
        self.rows: list[list[_Row]] = [[] for _ in self.groups]
        self.heads: list[_GroupCheck] = []
        self.more: list[LinkButton] = []
        self._boxes: list[QVBoxLayout] = []

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setFocusPolicy(Qt.NoFocus)
        self._scroll.viewport().installEventFilter(self)
        inner = QWidget()
        column = QVBoxLayout(inner)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        for gi, group in enumerate(self.groups):
            head = _Band(group.tone)
            line = QHBoxLayout(head)
            vertical, horizontal = theme.CHECK_HEAD_PAD
            line.setContentsMargins(horizontal, vertical, horizontal, vertical + 1)
            line.setSpacing(theme.CHECK_ROW_GAP)
            box = _GroupCheck()
            box.setAccessibleName(group.title)
            box.setAccessibleDescription(group.note)
            box.clicked.connect(lambda _=False, i=gi: self.set_group(i, self.heads[i].checkState() == Qt.Checked))
            words = Elided(self.head_text(gi), "type.overline", "lo")
            words.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            line.addWidget(box)
            line.addWidget(words, 1)
            if group.note:
                line.addWidget(label(group.note, "type.monoXs", GROUP_TONES[group.tone]))
            column.addWidget(head)
            self.heads.append(box)
            rows = QVBoxLayout()
            rows.setSpacing(0)
            column.addLayout(rows)
            self._boxes.append(rows)
            more = LinkButton("", font="type.monoXs")
            more.clicked.connect(lambda _=False, i=gi: self.expand(i))
            holder = QHBoxLayout()
            holder.setContentsMargins(horizontal, theme.SP_6, 0, theme.SP_6)
            holder.addWidget(more)
            holder.addStretch(1)
            column.addLayout(holder)
            self.more.append(more)
            self._build(gi, min(len(group.rows), max(0, group.limit)))
            self.sync(gi)
        column.addStretch(1)
        self._scroll.setWidget(inner)
        self._inner = inner
        outer = QVBoxLayout(self)
        outer.setContentsMargins(1, 1, 1, 1)
        outer.addWidget(self._scroll)
        self.fit()

    # -- the rows

    def head_text(self, gi: int) -> str:
        group = self.groups[gi]
        words = f"{group.title} — {fmt.counted(len(group.rows), group.noun, group.plural)}"
        size = sizes_text(group.rows) if self.sized else ""
        return f"{words} · {size}" if size else words

    def _build(self, gi: int, upto: int) -> None:
        group, built = self.groups[gi], self.rows[gi]
        for ri in range(len(built), upto):
            row = _Row(group.rows[ri], group.tone, self.sized)
            row.box.setChecked(self.ticks[gi][ri])
            row.box.clicked.connect(lambda on, g=gi, r=ri: self.set_row(g, r, on))
            self._boxes[gi].addWidget(row)
            built.append(row)
        left = len(group.rows) - len(built)
        self.more[gi].setText(f"{fmt.count(left)} more like these" if left else "")
        self.more[gi].setVisible(left > 0)

    def expand(self, gi: int) -> None:
        self._build(gi, len(self.groups[gi].rows))
        self.fit()
        self._resized()

    def fit(self) -> None:
        self._inner.adjustSize()
        height = self._inner.sizeHint().height() + 2
        self.setFixedHeight(min(height, theme.CHECKLIST_MAX))

    # -- the ticks

    def set_row(self, gi: int, ri: int, on: bool) -> None:
        self.ticks[gi][ri] = bool(on)
        if ri < len(self.rows[gi]):
            box = self.rows[gi][ri].box
            if box.isChecked() != bool(on):
                box.setChecked(bool(on))
        self.sync(gi)
        self._changed()

    def set_group(self, gi: int, on: bool) -> None:
        self.ticks[gi] = [bool(on)] * len(self.groups[gi].rows)
        for row in self.rows[gi]:
            row.box.setChecked(bool(on))
        self.sync(gi)
        self._changed()

    def state(self, gi: int) -> Qt.CheckState:
        ticks = self.ticks[gi]
        if ticks and all(ticks):
            return Qt.Checked
        return Qt.PartiallyChecked if any(ticks) else Qt.Unchecked

    def sync(self, gi: int) -> None:
        self.heads[gi].setCheckState(self.state(gi))

    def checked(self) -> list[CheckRow]:
        return [row for g, group in enumerate(self.groups)
                for row, on in zip(group.rows, self.ticks[g]) if on]

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        box = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = theme.DIALOG_WELL_RADIUS
        painter.setPen(QPen(theme.color("border.hairline"), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(box, radius, radius)

    def eventFilter(self, watched, event) -> bool:      # noqa: N802 - Qt's name
        # The bands and rows are square; the box's rounded corners clip them.
        if watched is self._scroll.viewport() and event.type() == QEvent.Resize:
            radius = theme.DIALOG_WELL_RADIUS - 1
            path = QPainterPath()
            path.addRoundedRect(QRectF(watched.rect()), radius, radius)
            watched.setMask(QRegion(path.toFillPolygon().toPolygon()))
        return False


# ---- a plain list of what will be written ----------------------------------------------------

class _Lines(QFrame):
    """What a confirmation will write, a line each, in a well: the first
    `limit` and "… and N more"."""

    def __init__(self, lines: Sequence[str], limit: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.lines = [str(line) for line in lines]
        column = QVBoxLayout(self)
        pad_v, pad_h = theme.CONFIRM_LINES_PAD
        column.setContentsMargins(pad_h, pad_v, pad_h, pad_v)
        column.setSpacing(theme.SP_2)
        self.shown: list[str] = self.lines[:max(0, limit)]
        for line in self.shown:
            words = Elided(line, "type.monoSm", "body")
            words.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            words.setToolTip(line)
            column.addWidget(words)
        left = len(self.lines) - len(self.shown)
        self.more = f"… and {fmt.count(left)} more" if left > 0 else ""
        if self.more:
            column.addWidget(label(self.more, "type.monoSm", "lo"))

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        box = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(box, theme.R_ROW, theme.R_ROW)
        painter.fillPath(path, theme.color("surface.subtle"))
        painter.setPen(QPen(theme.color("border.hairline"), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5), theme.R_ROW - 0.5, theme.R_ROW - 0.5)


# ---- ConfirmDialog ----------------------------------------------------------------------------

class ConfirmDialog(OverlayDialog):
    """Ask before acting: what will happen, and the button that does it.
    See the module's docstring for the whole API."""

    def __init__(self, title: str, body: str = "", parent: QWidget | None = None, *,
                 destructive: bool = False, icon: str | None = None, tone: str | None = None,
                 steps: Sequence = (), groups: Sequence[CheckGroup] = (),
                 summary: Callable[[list[CheckRow]], str] | None = None,
                 confirm_text: str | Callable[[list[CheckRow]], str] | None = None,
                 cancel_text: str = "Cancel",
                 actions: Sequence[tuple[str, Callable[[], None]]] = (),
                 lines: Sequence[str] = (), note: tuple[str, str] | None = None,
                 safe_default: bool = False, embedded: bool = False):
        super().__init__(parent, width=theme.DIALOG_WIDE if groups or lines else theme.DIALOG_WIDTH,
                         embedded=embedded)
        self._destructive = destructive
        self._result = ConfirmResult(False)
        self._set_head(title, body, icon or ("warn" if destructive else "info"),
                       tone or ("warn" if destructive else "accent"))
        self._steps: _Steps | None = None
        if steps:
            self._steps = _Steps(steps)
            self.body_column.addWidget(self._steps)
        self._checklist: _Checklist | None = None
        if groups:
            self._checklist = _Checklist(groups, self._refresh, self.place)
            self.body_column.addWidget(self._checklist)
        self._lines: _Lines | None = None
        if lines:
            self._lines = _Lines(lines, theme.CONFIRM_LINES)
            self.body_column.addWidget(self._lines)
        self._note: Callout | None = None
        if note is not None:
            tone, text = note
            self._note = Callout(text, tone=tone)
            self.body_column.addWidget(self._note)
        self._summary = summary
        self._confirm_text = confirm_text or ("Delete" if destructive else "Continue")

        self._actions: list[GhostButton] = []
        for text, callback in actions:
            button = GhostButton(text)
            button.setAutoDefault(False)
            button.clicked.connect(lambda _=False, fn=callback: fn())
            self.footer.actions.addWidget(button)
            self._actions.append(button)
        self._cancel = SecondaryButton(cancel_text)
        self._cancel.clicked.connect(self.reject)
        self._confirm = (DangerButton if destructive else AccentButton)("")
        self._confirm.clicked.connect(self.accept)
        self.footer.buttons.addWidget(self._cancel)
        self.footer.buttons.addWidget(self._confirm)
        self._safe = destructive or safe_default
        if self._safe:
            # Enter, and the focus the dialog opens with, go to Cancel
            self._cancel.setDefault(True)
            self._initial = self._cancel
        else:
            self._confirm.setDefault(True)
            self._initial = self._confirm
        self._refresh()

    # -- reading it

    def is_destructive(self) -> bool:
        return self._destructive

    def cancel_is_default(self) -> bool:
        """Whether Enter, and the first focus, go to Cancel."""
        return self._safe

    def listed_lines(self) -> list[str]:
        """The lines shown, and "… and N more" when there are more."""
        if self._lines is None:
            return []
        return self._lines.shown + ([self._lines.more] if self._lines.more else [])

    def note_text(self) -> str:
        return self._note.body() if self._note is not None else ""

    def confirm_button(self):
        return self._confirm

    def cancel_button(self) -> SecondaryButton:
        return self._cancel

    def action_buttons(self) -> list[GhostButton]:
        return list(self._actions)

    def step_titles(self) -> list[str]:
        return list(self._steps.titles) if self._steps is not None else []

    def summary_text(self) -> str:
        return self.footer.summary.text()

    def checked_rows(self) -> list[CheckRow]:
        return self._checklist.checked() if self._checklist is not None else []

    def result_value(self) -> ConfirmResult:
        return self._result

    # -- the checklist, for a page and a test

    def group_count(self) -> int:
        return len(self._checklist.groups) if self._checklist is not None else 0

    def group_state(self, gi: int) -> Qt.CheckState:
        return self._checklist.state(gi)

    def group_header(self, gi: int) -> str:
        return self._checklist.head_text(gi)

    def group_checkbox(self, gi: int) -> Checkbox:
        return self._checklist.heads[gi]

    def set_group_checked(self, gi: int, on: bool) -> None:
        self._checklist.set_group(gi, on)

    def row_checkbox(self, gi: int, ri: int) -> Checkbox | None:
        """A row's box, or None while the row is folded under "N more"."""
        rows = self._checklist.rows[gi]
        return rows[ri].box if ri < len(rows) else None

    def set_row_checked(self, gi: int, ri: int, on: bool) -> None:
        self._checklist.set_row(gi, ri, on)

    def built_rows(self, gi: int) -> int:
        return len(self._checklist.rows[gi])

    def more_text(self, gi: int) -> str:
        more = self._checklist.more[gi]
        return more.text() if not more.isHidden() else ""

    def expand_group(self, gi: int) -> None:
        self._checklist.expand(gi)

    # -- keeping the words true

    def _refresh(self) -> None:
        rows = self.checked_rows()
        if self._checklist is not None:
            if self._summary is not None:
                self.footer.summary.set_text(self._summary(rows))
            else:
                self.footer.summary.set_text(default_summary(rows, self._checklist.sized))
        elif self._summary is not None:
            self.footer.summary.set_text(self._summary(rows))
        text = self._confirm_text(rows) if callable(self._confirm_text) else self._confirm_text
        self._confirm.setText(text)
        self._confirm.setEnabled(self._checklist is None or bool(rows))

    def accept(self) -> None:
        if self._confirm.isEnabled():
            super().accept()

    def _answer(self, accepted: bool) -> None:
        self._result = ConfirmResult(accepted, tuple(self.checked_rows()) if accepted else ())


# ---- FormDialog ----------------------------------------------------------------------------------

# The signals a field says it changed with, whichever kind it is.
_CHANGE_SIGNALS = ("textChanged", "valueChanged", "currentIndexChanged", "toggled",
                   "path_changed", "validity_changed", "changed")


def _empty(field: QWidget) -> bool:
    if isinstance(field, QLineEdit):
        return not field.text().strip()
    if isinstance(field, QComboBox):
        return field.currentIndex() < 0
    path = getattr(field, "path", None)
    if callable(path):
        return not path()
    return False


class _FormRow(QWidget):
    """An overline label, the field, and a note under it that turns into the
    field's problem once the field has been touched."""

    def __init__(self, title: str, field: QWidget, note: str, check, required: bool,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.field, self.check, self.required = field, check, required
        self.note_text = note
        self.touched = False
        self.problem: str | None = None
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(theme.SP_6)
        self.label = Overline(title)
        self.label.setBuddy(field)
        column.addWidget(self.label)
        column.addWidget(field)
        self.note = label(note, "type.caption", "lo")
        self.note.setWordWrap(True)
        self.note.setVisible(bool(note))
        column.addWidget(self.note)
        if not field.accessibleName():
            field.setAccessibleName(title)

    def evaluate(self) -> bool:
        """Whether the field is right; what is wrong is kept in `problem`."""
        problem: str | None = None
        valid = True
        if self.required and _empty(self.field):
            valid = False
        elif self.check is not None:
            verdict = self.check(self.field)
            if verdict is False:
                valid = False
            elif isinstance(verdict, str) and verdict:
                valid, problem = False, verdict
        self.problem = problem
        self._show(problem if self.touched else None)
        return valid

    def _show(self, problem: str | None) -> None:
        if isinstance(self.field, TextInput):
            if self.field.error() != problem:
                self.field.set_error(problem)
            return
        self.note.setText(problem or self.note_text)
        set_tone(self.note, "danger" if problem else "lo")
        self.note.setVisible(bool(problem or self.note_text))


class FormDialog(OverlayDialog):
    """A few settings, and Save once they are right.

        form = FormDialog("Review settings", window)
        form.add_row("Scan every", every, note="Steam is asked once per author.")
        form.add_row("Steam Web API key", key, check=lambda f: None if len(f.text()) == 32
                     else "a key is 32 characters", required=True)
        if form.ask():
            save(every.current_data(), key.text())
    """

    def __init__(self, title: str, parent: QWidget | None = None, *, body: str = "",
                 icon: str | None = None, tone: str = "accent", save_text: str = "Save",
                 cancel_text: str = "Cancel", embedded: bool = False):
        super().__init__(parent, width=theme.DIALOG_WIDTH, embedded=embedded)
        self._set_head(title, body, icon, tone)
        self._rows: list[_FormRow] = []
        self._check: Callable[[FormDialog], Any] | None = None
        self._saved = False
        self._fields = QVBoxLayout()
        self._fields.setSpacing(theme.DIALOG_ROW_GAP)
        self.body_column.addLayout(self._fields)
        self._cancel = SecondaryButton(cancel_text)
        self._cancel.clicked.connect(self.reject)
        self._save = AccentButton(save_text)
        self._save.setDefault(True)
        self._save.clicked.connect(self.accept)
        self.footer.buttons.addWidget(self._cancel)
        self.footer.buttons.addWidget(self._save)
        self._initial = self._save
        self.revalidate()

    def add_row(self, title: str, field: QWidget, note: str = "", *,
                check: Callable[[QWidget], Any] | None = None, required: bool = False) -> QWidget:
        """Add a labelled field; it is returned, to keep. See the module's
        docstring for `check`."""
        row = _FormRow(title, field, note, check, required)
        self._fields.addWidget(row)
        self._rows.append(row)
        for name in _CHANGE_SIGNALS:
            signal = getattr(field, name, None)
            if isinstance(signal, SignalInstance):
                signal.connect(lambda *_args, r=row: self._touched(r))
        if len(self._rows) == 1:
            self._initial = field
        self.revalidate()
        return field

    def add_widget(self, widget: QWidget) -> QWidget:
        """Something that is not a labelled field — a note with an icon —
        after the rows so far."""
        self._fields.addWidget(widget)
        return widget

    def add_action(self, text: str, callback: Callable[[], None]) -> GhostButton:
        """A GhostButton in the footer, on the left of Cancel; it does not
        close the dialog."""
        button = GhostButton(text)
        button.setAutoDefault(False)
        button.clicked.connect(lambda _=False, fn=callback: fn())
        self.footer.actions.addWidget(button)
        return button

    def set_check(self, check: Callable[[FormDialog], Any] | None) -> None:
        """A check of the form as a whole, for what no one field can say
        ("the reserve cannot be the destination"). Same answers as a field's."""
        self._check = check
        self.revalidate()

    def fields(self) -> list[QWidget]:
        return [row.field for row in self._rows]

    def problem(self, field: QWidget) -> str | None:
        """What is wrong with a field, shown or not yet."""
        return next((row.problem for row in self._rows if row.field is field), None)

    def save_button(self) -> AccentButton:
        return self._save

    def cancel_button(self) -> SecondaryButton:
        return self._cancel

    def is_valid(self) -> bool:
        return self._save.isEnabled()

    def revalidate(self) -> bool:
        """Check every row and the form, and let Save follow."""
        valid = all([row.evaluate() for row in self._rows])
        message = ""
        if valid and self._check is not None:
            verdict = self._check(self)
            if verdict is False:
                valid = False
            elif isinstance(verdict, str) and verdict:
                valid, message = False, verdict
        self.footer.summary.set_text(message)
        self.footer.summary.set_tone("danger" if message else "lo")
        self._save.setEnabled(valid)
        return valid

    def _touched(self, row: _FormRow) -> None:
        row.touched = True
        self.revalidate()
        if self.isVisible():
            self.place()            # a problem shown or cleared changes the row's height

    def accept(self) -> None:
        if self.revalidate():
            super().accept()

    def _answer(self, accepted: bool) -> None:
        self._saved = accepted

    def result_value(self) -> bool:
        return self._saved
