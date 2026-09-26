"""Panels and what goes in them: GlassPanel, Overline, CardTitle, Callout,
MetricStrip, and the three that tell a page's state: EmptyState, StepList and
ActivityLine.

- GlassPanel is the card every page is made of: the 155° glass gradient, a
  hairline edge, the sheen along the top, and elev.2's shadow round it. A
  verdict changes only the edge's colour (`tone="ok"` for a clean run, "warn"
  for one with problems). It is also a surface: the controls on it draw their
  shadows and focus rings on it.
- Overline is a section label ("THE FOUR STEPS").
- CardTitle is a card's title, a quieter subtitle beside it, and actions on
  the right.
- Callout is a tinted note inside a panel: an icon, an optional title, the
  body, and optional actions on the right (Retry / Skip).
- MetricStrip is two to four numbers with their captions, divided by hairlines
  ("1 000 moved in · 998 returned · 3 duplicates · 2 failed").
- EmptyState is what a page says when there is nothing to show yet, or the
  work ended: an icon tile, a title, why, what to do, and when it last
  happened. Its drop-zone variant, dashed, takes folders dragged onto it.
- StepList is a run's steps one under another, joined by a line: pending,
  active (its dot pulses), done, failed; each with a mono caption.
- ActivityLine is the one thing happening now: a spinner (or a small thumb)
  and a mono line ("1234567890 → myprojects").
"""
from __future__ import annotations

import os

from PySide6.QtCore import QMargins, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget,
)

from ... import animations, theme
from . import base, icons
from .base import Caster, Glyph, elevation_margins, follow, grouped, label, set_tone
from .tables import Thumb

_UNSET = object()


# ---- GlassPanel ---------------------------------------------------------------------

PANEL_TONES: dict[str | None, str] = {
    None: "border.hairline",
    "ok": "ok.line",
    "warn": "warn.line",
    "danger": "danger.line",
    "accent": "accent.line",
}


class GlassPanel(Caster, QFrame):
    """A card of glass. Lay its content out in it as in any QFrame; `padding`
    is one of theme.PANEL_PADDING ("none", "sm", "md", "lg")."""

    def __init__(self, parent: QWidget | None = None, *, tone: str | None = None,
                 padding: str = "md", elevation: str = "elev.2"):
        QFrame.__init__(self, parent)
        if elevation not in theme.ELEVATION or theme.ELEVATION[elevation].inset:
            raise KeyError(f"a GlassPanel sits at elev.0, elev.1, elev.2 or elev.3, not {elevation!r}")
        base.declare(self)
        self._elevation = elevation
        self._tone: str | None = None
        self.set_tone(tone)
        self.set_padding(padding)

    def tone(self) -> str | None:
        return self._tone

    def set_tone(self, tone: str | None) -> None:
        tone = None if tone in (None, "none") else tone
        if tone not in PANEL_TONES:
            raise KeyError(f"no panel tone {tone!r}; there are ok, warn, danger, accent and none")
        self._tone = tone
        self.update()

    def set_padding(self, preset: str) -> None:
        try:
            vertical, horizontal = theme.PANEL_PADDING[preset]
        except KeyError:
            raise KeyError(f"no panel padding {preset!r}; there are "
                           f"{', '.join(theme.PANEL_PADDING)}") from None
        self.setContentsMargins(horizontal, vertical, horizontal, vertical)

    def outside_margins(self) -> QMargins:
        return elevation_margins(self._elevation)

    def paint_outside(self, painter: QPainter) -> None:
        if any(theme.shadow_reach(self._elevation)):
            theme.paint_shadow(painter, QRectF(self.rect()), self._elevation, theme.R_LG)

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        follow(self)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        box = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(box, theme.R_LG, theme.R_LG)
        painter.fillPath(path, QBrush(theme.gradient("surface.glass", box)))
        theme.paint_sheen(painter, box, theme.R_LG, self._elevation)
        painter.setPen(QPen(theme.color(PANEL_TONES[self._tone]), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5), theme.R_LG - 0.5, theme.R_LG - 0.5)
        # what the controls on it draw outside themselves
        base.paint(painter, self, event.rect())


# ---- labels at the top of a panel -------------------------------------------------------

class Overline(QLabel):
    """A section label: mono, upper case, tracked, text.lo."""

    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setFont(theme.font("type.overline"))
        self.setProperty("tone", "lo")


class CardTitle(QWidget):
    """A card's title (type.h3), a subtitle beside it in text.mid, and a slot
    for actions on the right (`add_action`), usually a GhostButton of size sm."""

    def __init__(self, title: str = "", subtitle: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.SP_10)
        words = QHBoxLayout()
        words.setSpacing(theme.SP_10)
        self._title = label(title, "type.h3", "hi")
        self._subtitle = label(subtitle, "type.label", "mid")
        self._subtitle.setVisible(bool(subtitle))
        # the two sit on one line, bottoms together, which is near enough their baselines
        words.addWidget(self._title, 0, Qt.AlignBottom)
        words.addWidget(self._subtitle, 0, Qt.AlignBottom)
        row.addLayout(words)
        row.addStretch(1)
        self._actions = QHBoxLayout()
        self._actions.setSpacing(theme.SP_8)
        row.addLayout(self._actions)

    def title(self) -> str:
        return self._title.text()

    def set_title(self, text: str) -> None:
        self._title.setText(text)

    def subtitle(self) -> str:
        return self._subtitle.text()

    def set_subtitle(self, text: str) -> None:
        self._subtitle.setText(text)
        self._subtitle.setVisible(bool(text))

    def add_action(self, widget: QWidget) -> QWidget:
        self._actions.addWidget(widget, 0, Qt.AlignVCenter)
        return widget


# ---- Callout ----------------------------------------------------------------------------

# tone → (ground, edge, icon colour, body colour, icon)
CALLOUT_TONES: dict[str, tuple[str, str, str, str, str]] = {
    "neutral": ("surface.note", "border.hairline", "text.mid", "mid", "info"),
    "info": ("info.soft", "info.line", "info", "body", "info"),
    "warn": ("warn.soft", "warn.line", "warn", "body", "warn"),
    "danger": ("danger.soft", "danger.line", "danger", "body", "warn"),
    "ok": ("ok.soft", "ok.line", "ok", "body", "check"),
}


class Callout(QFrame):
    """A note inside a panel, tinted by its tone: neutral, info, warn, danger
    or ok. An icon, an optional title, the body, and actions on the right
    (`add_action`) when the note offers a way out."""

    def __init__(self, body: str = "", parent: QWidget | None = None, *,
                 tone: str = "neutral", title: str = "", icon: str | None = None):
        super().__init__(parent)
        if tone not in CALLOUT_TONES:
            raise KeyError(f"no Callout tone {tone!r}; there are {', '.join(CALLOUT_TONES)}")
        self._tone = tone
        row = QHBoxLayout(self)
        vertical, horizontal = theme.CALLOUT_PAD
        row.setContentsMargins(horizontal, vertical, horizontal, vertical)
        row.setSpacing(theme.CALLOUT_GAP)
        _, _, glyph_colour, body_tone, default_icon = CALLOUT_TONES[tone]
        self._glyph = Glyph(icon or default_icon, glyph_colour, theme.CALLOUT_ICON)
        row.addWidget(self._glyph, 0, Qt.AlignTop)
        words = QVBoxLayout()
        words.setSpacing(theme.SP_2)
        self._title = label(title, "type.labelStrong", "hi")
        self._title.setWordWrap(True)
        self._title.setVisible(bool(title))
        self._body = label(body, "type.caption", body_tone)
        self._body.setWordWrap(True)
        self._body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        words.addWidget(self._title)
        words.addWidget(self._body)
        row.addLayout(words, 1)
        self._actions = QHBoxLayout()
        self._actions.setSpacing(theme.SP_6)
        row.addLayout(self._actions)
        row.setAlignment(self._actions, Qt.AlignTop)

    def tone(self) -> str:
        return self._tone

    def set_body(self, text: str) -> None:
        self._body.setText(text)

    def set_title(self, text: str) -> None:
        self._title.setText(text)
        self._title.setVisible(bool(text))

    def add_action(self, widget: QWidget) -> QWidget:
        self._actions.addWidget(widget)
        return widget

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        ground, edge = CALLOUT_TONES[self._tone][:2]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        box = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(box, theme.R_ROW, theme.R_ROW)
        painter.fillPath(path, theme.color(ground))
        painter.setPen(QPen(theme.color(edge), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5), theme.R_ROW - 0.5, theme.R_ROW - 0.5)


# ---- MetricStrip -------------------------------------------------------------------------

METRIC_TONES: dict[str | None, str] = {
    None: "hi", "ok": "ok", "warn": "warn", "danger": "danger", "info": "info",
}


def _number(value) -> str:
    if isinstance(value, int) and not isinstance(value, bool):
        return grouped(value)
    return str(value)


class MetricStrip(QWidget):
    """Two to four numbers side by side, each over its caption, with hairlines
    between them and, when `ruled`, above and below. A value's tone colours it:
    None (text.hi), "ok", "warn", "danger" or "info".

        MetricStrip([(1000, "moved in"), (998, "returned"), (2, "failed", "danger")])
    """

    def __init__(self, items, parent: QWidget | None = None, *, ruled: bool = True):
        items = [tuple(item) for item in items]
        if not 2 <= len(items) <= 4:
            raise ValueError(f"a MetricStrip holds two to four numbers, not {len(items)}")
        super().__init__(parent)
        self._ruled = ruled
        row = QHBoxLayout(self)
        pad = theme.METRIC_RULE_PAD if ruled else 0
        row.setContentsMargins(0, pad, 0, pad)
        row.setSpacing(0)
        self._values: list[QLabel] = []
        self._captions: list[QLabel] = []
        self._cells: list[QWidget] = []
        last = len(items) - 1
        for i, item in enumerate(items):
            value, caption = item[0], item[1]
            tone = item[2] if len(item) > 2 else None
            cell = QWidget()
            column = QVBoxLayout(cell)
            column.setContentsMargins(theme.METRIC_PAD if i else 0, 0,
                                      theme.METRIC_PAD if i < last else 0, 0)
            column.setSpacing(theme.METRIC_CAPTION_GAP)
            number = label(_number(value), "type.metric", self._tone(tone))
            words = label(caption, "type.caption", "lo")
            column.addWidget(number)
            column.addWidget(words)
            row.addWidget(cell, 1)
            self._values.append(number)
            self._captions.append(words)
            self._cells.append(cell)

    @staticmethod
    def _tone(tone: str | None) -> str:
        if tone not in METRIC_TONES:
            raise KeyError(f"no metric tone {tone!r}; there are ok, warn, danger, info and None")
        return METRIC_TONES[tone]

    def __len__(self) -> int:
        return len(self._values)

    def value(self, index: int) -> str:
        return self._values[index].text()

    def set_value(self, index: int, value, tone=_UNSET) -> None:
        self._values[index].setText(_number(value))
        if tone is not _UNSET:
            set_tone(self._values[index], self._tone(tone))

    def set_caption(self, index: int, text: str) -> None:
        self._captions[index].setText(text)

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        painter = QPainter(self)
        line = theme.color("border.hairline")
        if self._ruled:
            painter.fillRect(QRectF(0, 0, self.width(), 1), line)
            painter.fillRect(QRectF(0, self.height() - 1, self.width(), 1), line)
        for cell in self._cells[1:]:
            geometry = cell.geometry()
            painter.fillRect(QRectF(geometry.left(), geometry.top(), 1, geometry.height()), line)


# ---- EmptyState ---------------------------------------------------------------------------------

# tone → (tile ground, tile edge, icon colour)
EMPTY_TONES: dict[str, tuple[str, str, str]] = {
    "neutral": ("surface.tile", "border.hairline", "text.mid"),
    "ok": ("ok.soft", "ok.line", "ok"),
    "danger": ("danger.soft", "danger.line", "danger"),
}


class _Tile(QWidget):
    """The rounded square an EmptyState's icon sits in."""

    def __init__(self, icon: str, tone: str, parent: QWidget | None = None):
        super().__init__(parent)
        icons.svg(icon)
        self._icon, self._tone = icon, tone
        self.setFixedSize(theme.EMPTY_TILE, theme.EMPTY_TILE)

    def set(self, icon: str | None = None, tone: str | None = None) -> None:
        if icon is not None:
            icons.svg(icon)
            self._icon = icon
        if tone is not None:
            self._tone = tone
        self.update()

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        ground, edge, colour = EMPTY_TONES[self._tone]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        box = QRectF(self.rect())
        radius = theme.EMPTY_TILE_RADIUS
        path = QPainterPath()
        path.addRoundedRect(box, radius, radius)
        painter.fillPath(path, theme.color(ground))
        painter.setPen(QPen(theme.color(edge), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5), radius - 0.5, radius - 0.5)
        size = theme.EMPTY_ICON
        painter.drawPixmap(QPointF((self.width() - size) / 2, (self.height() - size) / 2),
                           icons.pixmap(self._icon, colour, size, self.devicePixelRatioF()))


class _Zone(QWidget):
    """The EmptyState's column; dashed round the edge when it is a drop zone."""

    def __init__(self, dashed: bool, parent: QWidget | None = None):
        super().__init__(parent)
        self.dashed = dashed
        self.hot = False

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        if not self.dashed:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(theme.color("border.focus" if self.hot else "border.strong"), 1)
        pen.setDashPattern(list(theme.DROP_DASH))
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5),
                                theme.R_XL - 0.5, theme.R_XL - 0.5)


class EmptyState(QWidget):
    """What a page says when it has nothing to show, centred where it stands.

        empty = EmptyState("Nothing scanned since the last review",
                           "A scan checks all 118 authors you follow…",
                           icon="review", meta="last scan Friday 09:10")
        empty.add_action(AccentButton("Scan for new items", size="lg"))

    `tone` is "neutral", "ok" (the work ended well) or "danger" (it stopped);
    it colours only the icon tile. `drop_zone=True` draws the dashed edge
    round it and emits `dropped(paths)` for folders dragged onto it.
    """

    dropped = Signal(list)

    def __init__(self, title: str = "", body: str = "", parent: QWidget | None = None, *,
                 icon: str = "info", tone: str = "neutral", drop_zone: bool = False,
                 meta: str = "", meta_icon: str = "clock"):
        super().__init__(parent)
        if tone not in EMPTY_TONES:
            raise KeyError(f"no EmptyState tone {tone!r}; there are {', '.join(EMPTY_TONES)}")
        self._tone = tone
        self._drop = drop_zone
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addStretch(1)
        middle = QHBoxLayout()
        middle.addStretch(1)
        self._zone = _Zone(drop_zone)
        middle.addWidget(self._zone)
        middle.addStretch(1)
        outer.addLayout(middle)
        outer.addStretch(1)

        # The column has the design's measure as a fixed width, so its wrapped
        # lines are measured at the width they are drawn at.
        pad_v, pad_h = theme.DROP_PAD if drop_zone else (0, 0)
        measure = theme.DROP_BODY_WIDTH if drop_zone else theme.EMPTY_BODY_WIDTH
        self._zone.setFixedWidth(measure + 2 * pad_h)
        column = QVBoxLayout(self._zone)
        column.setContentsMargins(pad_h, pad_v, pad_h, pad_v)
        column.setSpacing(theme.EMPTY_GAP)
        self._tile = _Tile(icon, tone)
        column.addWidget(self._tile, 0, Qt.AlignHCenter)
        self._title = label(title, "type.hero", "hi")
        self._body = label(body, "type.lead", "mid")
        for words in (self._title, self._body):
            words.setWordWrap(True)
            words.setAlignment(Qt.AlignHCenter)
            column.addWidget(words)
        self._body.setVisible(bool(body))
        self._actions_row = QWidget()
        self._actions = QHBoxLayout(self._actions_row)
        self._actions.setContentsMargins(0, theme.SP_4, 0, 0)
        self._actions.setSpacing(theme.SP_8 + 1)
        self._actions.addStretch(1)
        self._actions.addStretch(1)
        self._actions_row.hide()
        column.addWidget(self._actions_row)
        self._meta_row = QWidget()
        line = QHBoxLayout(self._meta_row)
        line.setContentsMargins(0, theme.SP_6, 0, 0)
        line.setSpacing(theme.SP_6 + 1)
        line.addStretch(1)
        self._meta_glyph = Glyph(meta_icon, "text.lo", theme.EMPTY_META_ICON)
        self._meta = label(meta, "type.monoSm", "lo")
        line.addWidget(self._meta_glyph)
        line.addWidget(self._meta)
        line.addStretch(1)
        self._meta_row.setVisible(bool(meta))
        column.addWidget(self._meta_row)
        if drop_zone:
            self.setAcceptDrops(True)

    # -- what it says

    def title(self) -> str:
        return self._title.text()

    def set_title(self, text: str) -> None:
        self._title.setText(text)

    def body(self) -> str:
        return self._body.text()

    def set_body(self, text: str) -> None:
        self._body.setText(text)
        self._body.setVisible(bool(text))

    def meta(self) -> str:
        return self._meta.text()

    def set_meta(self, text: str) -> None:
        self._meta.setText(text)
        self._meta_row.setVisible(bool(text))

    def tone(self) -> str:
        return self._tone

    def set_tone(self, tone: str) -> None:
        if tone not in EMPTY_TONES:
            raise KeyError(f"no EmptyState tone {tone!r}; there are {', '.join(EMPTY_TONES)}")
        self._tone = tone
        self._tile.set(tone=tone)

    def set_icon(self, name: str) -> None:
        self._tile.set(icon=name)

    def is_drop_zone(self) -> bool:
        return self._drop

    def add_action(self, widget: QWidget) -> QWidget:
        """A button under the words: the AccentButton that starts the work
        first, then quieter ones."""
        self._actions.insertWidget(self._actions.count() - 1, widget)
        self._actions_row.show()
        return widget

    # -- drops

    @staticmethod
    def _paths(event) -> list[str]:
        mime = event.mimeData()
        if not mime.hasUrls():
            return []
        return [os.path.normpath(url.toLocalFile()) for url in mime.urls() if url.isLocalFile()]

    def dragEnterEvent(self, event) -> None:    # noqa: N802 - Qt's name
        if self._drop and self._paths(event):
            event.acceptProposedAction()
            self._zone.hot = True
            self._zone.update()
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:    # noqa: N802 - Qt's name
        self._zone.hot = False
        self._zone.update()

    def dropEvent(self, event) -> None:         # noqa: N802 - Qt's name
        self._zone.hot = False
        self._zone.update()
        paths = self._paths(event)
        if paths:
            event.acceptProposedAction()
            self.dropped.emit(paths)


# ---- StepList -----------------------------------------------------------------------------------

STEP_STATES = ("pending", "active", "done", "failed")
# the caption's tone when none is given, and the title's
STEP_CAPTION_TONES = {"pending": "lo", "active": "accent", "done": "lo", "failed": "danger"}
STEP_TITLE_TONES = {"pending": "mid", "active": "body", "done": "body", "failed": "body"}


class _StepMarker(QWidget):
    """A step's dot, and the line down to the next one."""

    def __init__(self, state: str, last: bool, parent: QWidget | None = None):
        super().__init__(parent)
        self._state = "pending"
        self.last = last
        self.setFixedWidth(theme.STEP_DOT)
        self.setMinimumHeight(theme.STEP_DOT)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.set_state(state)

    def state(self) -> str:
        return self._state

    def set_state(self, state: str) -> None:
        self._state = state
        driver = animations.loop("pulse")
        if state == "active":
            driver.subscribe(self)
        else:
            driver.unsubscribe(self)
        self.update()

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        side = theme.STEP_DOT
        dot = QRectF(0, 0, side, side)
        if not self.last:
            top = side + theme.STEP_LINK_GAP
            painter.fillRect(QRectF(side / 2 - 0.5, top, 1, max(0, self.height() - top)),
                             theme.color("border.hairline"))
        dpr = self.devicePixelRatioF()
        if self._state in ("done", "failed"):
            fill, glyph, size = (("ok", "check", theme.STEP_TICK[0]) if self._state == "done"
                                 else ("danger", "close", theme.STEP_TICK[1]))
            painter.setPen(Qt.NoPen)
            painter.setBrush(theme.color(fill))
            painter.drawEllipse(dot)
            painter.drawPixmap(QPointF(side / 2 - size / 2, side / 2 - size / 2),
                               icons.pixmap(glyph, "text.onAccent", size, dpr))
        elif self._state == "active":
            painter.setOpacity(animations.loop("pulse").value())
            painter.setPen(QPen(theme.color("accent"), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(dot.adjusted(1, 1, -1, -1))
        else:
            painter.setPen(QPen(theme.color("border.control"), 1))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(dot.adjusted(0.5, 0.5, -0.5, -0.5))


class StepList(QWidget):
    """A run's steps, top to bottom. Each is (title, caption) or (title,
    caption, state) or (title, caption, state, caption tone); states are
    "pending", "active", "done" and "failed".

        steps = StepList([("Return the previous batch", "998 returned · 13:46", "done"),
                          ("Move 1 000 new folders in", "412 / 1 000 · ≈6 min left", "active"),
                          ("Check for duplicates", "waiting")])
    """

    def __init__(self, steps=(), parent: QWidget | None = None):
        super().__init__(parent)
        self._column = QVBoxLayout(self)
        self._column.setContentsMargins(0, 0, 0, 0)
        self._column.setSpacing(0)
        # room to spare goes below the last step, not into the rows: a row
        # stretched past its height pushes its caption away from its title
        self._column.addStretch(1)
        self._rows: list[tuple[QWidget, _StepMarker, QLabel, QLabel]] = []
        self._tones: list[str | None] = []
        self.set_steps(steps)

    def set_steps(self, steps) -> None:
        for row, *_ in self._rows:
            self._column.removeWidget(row)
            row.deleteLater()
        self._rows, self._tones = [], []
        steps = [tuple(step) for step in steps]
        for i, step in enumerate(steps):
            title, caption = step[0], step[1] if len(step) > 1 else ""
            state = step[2] if len(step) > 2 else "pending"
            self._check(state)
            last = i == len(steps) - 1
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            line.setSpacing(theme.STEP_GAP)
            marker = _StepMarker(state, last)
            words = QVBoxLayout()
            words.setContentsMargins(0, 0, 0, 0 if last else theme.STEP_PAD)
            words.setSpacing(theme.SP_2)
            name = label(title, "type.step", STEP_TITLE_TONES[state])
            name.setWordWrap(True)
            note = label(caption, "type.monoXs", "lo")
            note.setWordWrap(True)
            words.addWidget(name)
            words.addWidget(note)
            line.addWidget(marker)
            line.addLayout(words, 1)
            self._column.insertWidget(i, row)
            self._rows.append((row, marker, name, note))
            self._tones.append(step[3] if len(step) > 3 else None)
            self._paint_tone(i)

    @staticmethod
    def _check(state: str) -> None:
        if state not in STEP_STATES:
            raise ValueError(f"no step state {state!r}; there are {', '.join(STEP_STATES)}")

    def _paint_tone(self, i: int) -> None:
        _, marker, name, note = self._rows[i]
        state = marker.state()
        set_tone(name, STEP_TITLE_TONES[state])
        set_tone(note, self._tones[i] or STEP_CAPTION_TONES[state])

    def __len__(self) -> int:
        return len(self._rows)

    def step_state(self, i: int) -> str:
        return self._rows[i][1].state()

    def title(self, i: int) -> str:
        return self._rows[i][2].text()

    def caption(self, i: int) -> str:
        return self._rows[i][3].text()

    def caption_tone(self, i: int) -> str:
        return self._rows[i][3].property("tone")

    def set_step(self, i: int, *, state: str | None = None, caption: str | None = None,
                 tone=None, title: str | None = None) -> None:
        """Change one step; `tone` sets the caption's ("ok", "warn"…), "" goes
        back to the state's own."""
        _, marker, name, note = self._rows[i]
        if state is not None:
            self._check(state)
            marker.set_state(state)
        if caption is not None:
            note.setText(caption)
        if title is not None:
            name.setText(title)
        if tone is not None:
            self._tones[i] = tone or None
        self._paint_tone(i)


# ---- ActivityLine ---------------------------------------------------------------------------------

class _Spinner(QWidget):
    """Half a ring turning on the shared "spin" clock, over the rest of it."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        side = theme.ACTIVITY_SPINNER
        self.setFixedSize(side, side)
        animations.loop("spin").subscribe(self)

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        stroke = theme.ACTIVITY_SPINNER_STROKE
        box = QRectF(self.rect()).adjusted(stroke / 2, stroke / 2, -stroke / 2, -stroke / 2)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(theme.color("surface.raised"), stroke))
        painter.drawEllipse(box)
        painter.setPen(QPen(theme.color("accent"), stroke))
        # the CSS colours the ring's top and right sides: ten-thirty round to four-thirty
        start = 135 - animations.loop("spin").value()
        painter.drawArc(box, round(start * 16), -round(360 * theme.ACTIVITY_SPIN_ARC * 16))


class ActivityLine(QWidget):
    """What is being worked on right now: a spinner, or a small thumb of the
    wallpaper, and one mono line. With Windows' animations off the spinner
    stands still and the line says "working" first, so it still reads as
    something happening."""

    def __init__(self, text: str = "", parent: QWidget | None = None, *, spinning: bool = True):
        super().__init__(parent)
        self._text = text
        row = QHBoxLayout(self)
        pad_v, pad_h = theme.ACTIVITY_PAD
        row.setContentsMargins(pad_h, pad_v, pad_h, pad_v)
        row.setSpacing(theme.ACTIVITY_GAP)
        self._spinner = _Spinner()
        self._thumb = Thumb("xs")
        self._thumb.hide()
        row.addWidget(self._spinner)
        row.addWidget(self._thumb)
        row.addStretch(1)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAccessibleName("Working on")
        self.set_text(text)
        self.set_spinning(spinning)

    def text(self) -> str:
        return self._text

    def shown_text(self) -> str:
        """The line as drawn: "working · …" first when the spinner cannot turn."""
        if self._spinner.isVisibleTo(self) and not animations.ENABLED:
            return f"working · {self._text}" if self._text else "working"
        return self._text

    def set_text(self, text: str) -> None:
        self._text = text
        self.setAccessibleDescription(text)
        self.update()

    def set_spinning(self, on: bool = True) -> None:
        self._spinner.setVisible(on)
        if on:
            self._thumb.hide()
        self._fit()

    def set_thumb(self, source: str | None = None, pixmap=None) -> None:
        """Lead with the wallpaper's thumb instead of the spinner."""
        self._spinner.hide()
        self._thumb.show()
        if pixmap is not None:
            self._thumb.set_pixmap(pixmap)
        else:
            self._thumb.set_source(source)
        self._fit()

    def lead(self) -> str:
        """"spinner", "thumb" or "": what the line starts with."""
        if self._spinner.isVisibleTo(self):
            return "spinner"
        return "thumb" if self._thumb.isVisibleTo(self) else ""

    def _fit(self) -> None:
        pad_v = theme.ACTIVITY_PAD[0]
        lead = theme.THUMB["xs"][1] if self._thumb.isVisibleTo(self) else theme.ACTIVITY_SPINNER
        line = QFontMetricsF(theme.font("type.monoSm")).height()
        self.setFixedHeight(round(max(lead, line) + 2 * pad_v))
        self.update()

    def sizeHint(self) -> QSize:                # noqa: N802 - Qt's name
        return QSize(240, self.height())

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        box = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(box, theme.R_ROW, theme.R_ROW)
        painter.fillPath(path, theme.color("surface.subtle"))
        pad_h = theme.ACTIVITY_PAD[1]
        lead = self._thumb if self._thumb.isVisibleTo(self) else self._spinner
        left = (lead.geometry().right() + 1 + theme.ACTIVITY_GAP if lead.isVisibleTo(self)
                else pad_h)
        font = theme.font("type.monoSm")
        width = max(0.0, box.width() - pad_h - left)
        text = QFontMetricsF(font).elidedText(self.shown_text(), Qt.ElideRight, width)
        painter.setFont(font)
        painter.setPen(theme.color("text.mid"))
        painter.drawText(QRectF(left, 0, width, box.height()), Qt.AlignLeft | Qt.AlignVCenter, text)
