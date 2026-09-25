"""Panels and what goes at the top of them: GlassPanel, Overline, CardTitle,
Callout, MetricStrip.

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
"""
from __future__ import annotations

from PySide6.QtCore import QMargins, QRectF, Qt
from PySide6.QtGui import QBrush, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ... import theme
from . import base
from .base import Caster, Glyph, elevation_margins, follow, grouped, label, set_tone

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
