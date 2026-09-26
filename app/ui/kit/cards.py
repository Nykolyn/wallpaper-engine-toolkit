"""Cards: StatCard and MonitorCard.

- StatCard is one number with its overline and a caption ("RESERVE · 33 421 ·
  8 204 never used"). Its states: default; hover, for a card that opens a page
  (the glass brightens, the edge strengthens and a ↗ follows the number);
  loading, three shimmering bars where the words will be; empty, "—" and why
  ("no run yet"). The number can take a tone (warn for "89 new").
- MonitorCard is what a monitor is showing and how far through the playlist
  it is. **compact** (Overview): a 72 × 41 thumb, the title, "author · 14 min
  in", and a bar with "4 / 201". **detail** (Tracker): a ring and the count,
  a 298 × 84 preview, the title, the author with a chip, and a footer of SHOWN
  FOR / REMAINING / CYCLE STARTED. Its states are the monitor's: leading,
  summary (a second monitor, following its own order), paused and
  disconnected.

A page never builds a MonitorCard's text itself: it fills a `MonitorView`
from the engine and hands it over, and the card writes every number through
`format`. Tests build MonitorViews directly.
"""
from __future__ import annotations

import html
from dataclasses import dataclass

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from ... import animations, theme
from . import format as fmt
from .base import Glyph, Interactive, follow, label, set_tone
from .buttons import IconButton
from .chips import Chip
from .panels import GlassPanel, Overline
from .progress import ProgressBar, ProgressRing
from .tables import Thumb

_UNSET = object()

VALUE_TONES = {None: "hi", "ok": "ok", "warn": "warn", "danger": "danger", "info": "info",
               "accent": "accent"}


def qualified_html(text: str, tone: str = "text.body") -> str:
    """Rich text for a value that may carry `≈` or `~`: the mark in text.lo on
    the same baseline, as every estimate and reconstruction is written."""
    mark, rest = fmt.split_qualifier(text)
    body = f'<span style="color:{theme.css(tone)}">{html.escape(rest)}</span>'
    if not mark:
        return body
    return f'<span style="color:{theme.css("text.lo")}">{html.escape(mark)}</span>{body}'


# ---- StatCard ------------------------------------------------------------------------

STAT_STATES = ("default", "hover", "loading", "empty")


class StatCard(Interactive, GlassPanel):
    """One figure on glass. `clickable=True` gives it the hover state and a
    `clicked` signal (it opens the page the number comes from).

        StatCard("Reserve", 33421, "8 204 never used")
        StatCard("New since last review", 89, "from 12 authors", tone="warn")
        StatCard("Duplicates set aside")            # empty: "—", no run yet
    """

    clicked = Signal()

    def __init__(self, overline: str, value=None, caption: str = "",
                 parent: QWidget | None = None, *, tone: str | None = None,
                 clickable: bool = False, empty_caption: str = "no run yet"):
        GlassPanel.__init__(self, parent)
        vertical, horizontal = theme.STAT_PAD
        self.setContentsMargins(horizontal, vertical, horizontal, vertical)
        self.setMinimumWidth(theme.STAT_MIN_WIDTH)
        self._clickable = clickable
        self._loading = False
        self._empty = True
        self._tone = tone
        self._empty_caption = empty_caption
        if clickable:
            self.setCursor(Qt.PointingHandCursor)
            self.setFocusPolicy(Qt.StrongFocus)

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        self._overline = Overline(overline)
        self._value = label("", "type.numeric", "hi")
        self._ext = Glyph("ext", "accent.hover", theme.STAT_EXT)
        self._ext.hide()
        self._caption = label("", "type.label", "mid")
        for widget in (self._overline, self._value, self._caption):
            policy = widget.sizePolicy()
            policy.setRetainSizeWhenHidden(True)
            widget.setSizePolicy(policy)
        numbers = QHBoxLayout()
        numbers.setSpacing(theme.SP_8)
        numbers.addWidget(self._value, 0, Qt.AlignBaseline)
        numbers.addWidget(self._ext, 0, Qt.AlignVCenter)
        numbers.addStretch(1)
        column.addWidget(self._overline)
        column.addSpacing(theme.STAT_VALUE_GAP)
        column.addLayout(numbers)
        column.addSpacing(theme.STAT_CAPTION_GAP)
        column.addWidget(self._caption)
        column.addStretch(1)
        self.setAccessibleName(overline)
        self.set_value(value, tone)
        if value is not None or caption:
            self.set_caption(caption)

    # -- what it says

    def overline(self) -> str:
        return self._overline.text()

    def value(self) -> str:
        return self._value.text()

    def caption(self) -> str:
        return self._caption.text()

    def set_value(self, value, tone=_UNSET) -> None:
        """A number (grouped), words, or None for "not known yet"."""
        if tone is not _UNSET:
            if tone not in VALUE_TONES:
                raise KeyError(f"no StatCard tone {tone!r}; there are "
                               f"{', '.join(str(t) for t in VALUE_TONES)}")
            self._tone = tone
        self._empty = value is None
        if self._empty:
            self._value.setText(fmt.DASH)
            set_tone(self._value, "lo")
            self._caption.setText(self._empty_caption)
            set_tone(self._caption, "lo")
        else:
            text = fmt.count(value) if isinstance(value, int) and not isinstance(value, bool) \
                else str(value)
            self._value.setText(text)
            set_tone(self._value, VALUE_TONES[self._tone])
            set_tone(self._caption, "mid")
        self.setAccessibleDescription(f"{self._value.text()} {self._caption.text()}".strip())
        self.update()

    def set_caption(self, text: str) -> None:
        self._caption.setText(text)
        self.setAccessibleDescription(f"{self._value.text()} {text}".strip())

    def set_empty(self, caption: str | None = None) -> None:
        if caption is not None:
            self._empty_caption = caption
        self.set_value(None)

    def set_loading(self, on: bool = True) -> None:
        """Three shimmering bars until the numbers arrive."""
        self._loading = on
        for widget in (self._overline, self._value, self._caption):
            widget.setVisible(not on)
        driver = animations.loop("shimmer")
        if on:
            driver.subscribe(self)
        else:
            driver.unsubscribe(self)
        self.state_changed()

    def card_state(self) -> str:
        if self._loading:
            return "loading"
        if self._empty:
            return "empty"
        return "hover" if self._hovered() else "default"

    def _hovered(self) -> bool:
        if not self.isEnabled():
            return False
        if self._kit_force == "hover":
            return True
        return self._clickable and self._kit_force is None and self.underMouse()

    def state_changed(self) -> None:
        self._ext.setVisible(self.card_state() == "hover")
        super().state_changed()

    # -- clicks

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt's name
        if (self._clickable and event.button() == Qt.LeftButton
                and self.rect().contains(event.position().toPoint())):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:     # noqa: N802 - Qt's name
        if self._clickable and event.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            self.clicked.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    # -- painting

    def paint_outside(self, painter: QPainter) -> None:
        super().paint_outside(painter)
        if self.focus_visible() and self._clickable:
            theme.paint_focus_ring(painter, QRectF(self.rect()), theme.R_LG)

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        follow(self)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        box = QRectF(self.rect())
        hover = self.card_state() == "hover"
        path = QPainterPath()
        path.addRoundedRect(box, theme.R_LG, theme.R_LG)
        glass = "surface.glassHover" if hover else "surface.glass"
        painter.fillPath(path, QBrush(theme.gradient(glass, box)))
        theme.paint_sheen(painter, box, theme.R_LG, "elev.2")
        edge = "border.strong" if hover else "border.hairline"
        painter.setPen(QPen(theme.color(edge), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5), theme.R_LG - 0.5, theme.R_LG - 0.5)
        if self._loading:
            self._paint_skeleton(painter)

    def _paint_skeleton(self, painter: QPainter) -> None:
        m = self.contentsMargins()
        y = float(m.top())
        driver = animations.loop("shimmer")
        painter.setPen(Qt.NoPen)
        for i, (width, height, radius, gap) in enumerate(theme.STAT_SKELETON):
            y += gap
            bar = theme.color("surface.raised",
                              driver.value(delay=i * theme.SKELETON_STAGGER))
            painter.setBrush(bar)
            painter.drawRoundedRect(QRectF(m.left(), y, width, height), radius, radius)
            y += height


# ---- MonitorView --------------------------------------------------------------------------

MONITOR_STATES = ("leading", "summary", "paused", "disconnected")
# state → (badge text, badge fill); a summary monitor wears no badge
BADGES = {"leading": ("Leading", "accent"), "paused": ("Paused", "warn"),
          "disconnected": ("Disconnected", "danger")}
BAR_TONES = {"leading": "accent", "summary": "muted", "paused": "warn",
             "disconnected": "danger"}


@dataclass(frozen=True)
class MonitorView:
    """What a MonitorCard shows, in plain values a page fills from the engine.

    Times are seconds; `cycle_started` and `last_seen` are datetimes (or
    timestamps). `reconstructed` says the cycle's start was rebuilt after a
    restart rather than seen, which the card marks with `~`. `preview` is the
    wallpaper's folder (or preview file), read off the GUI thread."""
    name: str
    state: str = "summary"
    resolution: str = ""
    title: str = ""
    author: str = ""
    author_chip: str | None = None       # a Chip variant: "Known", "NewAuthor"…
    position: int = 0                    # 4 …
    total: int = 0                       # … of 201
    shown_for: float | None = None
    remaining: float | None = None
    cycle_started: object = None
    reconstructed: bool = False
    last_seen: object = None
    preview: str | None = None
    note: str = ""                       # "follows its own order · not counted for rotation"

    def __post_init__(self) -> None:
        if self.state not in MONITOR_STATES:
            raise ValueError(f"no monitor state {self.state!r}; there are "
                             f"{', '.join(MONITOR_STATES)}")

    # -- the words, as the card writes them

    def badge(self) -> tuple[str, str] | None:
        """(text, fill token) for the badge after the name, or None."""
        return BADGES.get(self.state)

    def icon_tone(self) -> str:
        return "danger" if self.state == "disconnected" else "text.mid"

    def title_text(self) -> str:
        if self.state == "disconnected":
            return "no signal"
        return self.title or fmt.DASH

    def meta_text(self, now=None) -> str:
        """"Marlow · 14 min in", or "last seen 11:02" for a monitor that is gone."""
        if self.state == "disconnected":
            if self.last_seen is None:
                return "last seen: never"
            return f"last seen {fmt.date_activity(self.last_seen, now)}"
        parts = [self.author] if self.author else []
        if self.shown_for is not None:
            parts.append(f"{fmt.duration(self.shown_for, exact=False)} in")
        return " · ".join(parts)

    def count_text(self) -> str:
        return fmt.ratio(self.position, self.total) if self.total else fmt.DASH

    def fraction(self) -> float:
        if self.total <= 0:
            return 0.0
        return max(0.0, min(1.0, self.position / self.total))

    def bar_tone(self) -> str:
        return BAR_TONES[self.state]

    def remaining_parts(self) -> tuple[str, str, str]:
        """(value, the word after it, tone) for REMAINING: "26 min", "26 min
        paused" in warn, or "— disconnected"."""
        if self.state == "disconnected":
            return fmt.DASH, "disconnected", "danger"
        value = fmt.DASH if self.remaining is None else fmt.duration(self.remaining, exact=False)
        if self.state == "paused":
            return value, "paused", "warn"
        return value, "", "text.body"

    def cycle_text(self, now=None) -> str:
        if self.cycle_started is None:
            return fmt.DASH
        text = fmt.date_table(self.cycle_started, now)
        return fmt.reconstructed(text) if self.reconstructed else text

    def facts(self, now=None) -> list[tuple[str, str]]:
        """The detail footer, as (label, value)."""
        shown = fmt.DASH if self.shown_for is None else fmt.duration(self.shown_for, exact=False)
        value, word, _ = self.remaining_parts()
        return [("Shown for", shown),
                ("Remaining", f"{value} {word}".strip()),
                ("Cycle started", self.cycle_text(now))]


# ---- MonitorCard ------------------------------------------------------------------------------

class _Badge(QWidget):
    """LEADING, PAUSED, DISCONNECTED: mono capitals on a solid fill."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._text, self._fill = "", "accent"
        self.hide()

    def set_badge(self, badge: tuple[str, str] | None) -> None:
        if badge is None:
            self.hide()
            return
        self._text, self._fill = badge[0].upper(), badge[1]
        metrics = QFontMetricsF(theme.font("type.chip"))
        vertical, horizontal = theme.BADGE_PAD
        self.setFixedSize(round(metrics.horizontalAdvance(self._text) + 2 * horizontal + 0.5),
                          round(metrics.height() + 2 * vertical))
        self.setAccessibleName(badge[0])
        self.show()
        self.update()

    def text(self) -> str:
        return self._text

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), theme.R_SM, theme.R_SM)
        painter.fillPath(path, theme.color(self._fill))
        painter.setFont(theme.font("type.chip"))
        painter.setPen(theme.color("text.onAccent"))
        painter.drawText(QRectF(self.rect()), Qt.AlignCenter, self._text)


class _Facts(QWidget):
    """SHOWN FOR | REMAINING | CYCLE STARTED, over a hairline, divided by hairlines."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, theme.SP_10, 0, 0)
        row.setSpacing(0)
        self._cells: list[QWidget] = []
        self._labels: list[QLabel] = []
        self._values: list[QLabel] = []
        self._words: list[QLabel] = []
        for i in range(3):
            cell = QWidget()
            column = QVBoxLayout(cell)
            column.setContentsMargins(theme.SP_12 if i else 0, 0, 0, 0)
            column.setSpacing(theme.SP_2 + 1)
            caption = label("", "type.monoXs", "lo")
            line = QHBoxLayout()
            line.setSpacing(theme.SP_6)
            value = label("", "type.value", "body")
            value.setTextFormat(Qt.RichText)
            word = label("", "type.monoXs", "lo")
            word.hide()
            line.addWidget(value, 0, Qt.AlignBaseline)
            line.addWidget(word, 0, Qt.AlignBaseline)
            line.addStretch(1)
            column.addWidget(caption)
            column.addLayout(line)
            row.addWidget(cell, 1)
            self._cells.append(cell)
            self._labels.append(caption)
            self._values.append(value)
            self._words.append(word)

    def set_view(self, view: MonitorView, now=None) -> None:
        facts = view.facts(now)
        remaining, word, tone = view.remaining_parts()
        for i, (caption, value) in enumerate(facts):
            self._labels[i].setText(caption.upper())
            if i == 1:
                value_tone = "text.lo" if view.state == "disconnected" else tone
                self._values[i].setText(qualified_html(remaining, value_tone))
                self._words[i].setText(word)
                self._words[i].setVisible(bool(word))
                set_tone(self._words[i], {"warn": "warn", "danger": "danger"}.get(tone, "lo"))
            else:
                self._values[i].setText(qualified_html(value))

    def value_texts(self) -> list[str]:
        """The three values as a reader sees them, without markup."""
        out = []
        for value, word in zip(self._values, self._words):
            plain = _plain(value.text())
            out.append(f"{plain} {word.text()}".strip() if word.isVisibleTo(self) else plain)
        return out

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        painter = QPainter(self)
        line = theme.color("border.hairline")
        painter.fillRect(QRectF(0, 0, self.width(), 1), line)
        for cell in self._cells[1:]:
            g = cell.geometry()
            painter.fillRect(QRectF(g.left(), g.top(), 1, g.height()), line)


def _plain(rich: str) -> str:
    from PySide6.QtGui import QTextDocumentFragment
    return QTextDocumentFragment.fromHtml(rich).toPlainText()


class MonitorCard(GlassPanel):
    """A monitor and what it is showing. `detail=False` is the compact card
    (Overview), `detail=True` the Tracker's. Fill it with `set_view`;
    `set_menu(QMenu)` puts an overflow button in its header."""

    def __init__(self, view: MonitorView | None = None, parent: QWidget | None = None, *,
                 detail: bool = False):
        GlassPanel.__init__(self, parent)
        self._detail = detail
        variant = self.variant()
        vertical, horizontal = theme.MONITOR_PAD[variant]
        self.setContentsMargins(horizontal, vertical, horizontal, vertical)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(theme.MONITOR_GAP[variant])
        self._view: MonitorView | None = None

        # the header: glyph, name, badge, resolution, overflow
        head = QHBoxLayout()
        head.setSpacing(theme.SP_8)
        self._glyph = Glyph("monitor", "text.mid", theme.MONITOR_ICON)
        self._name = label("", "type.h3" if detail else "type.body", "hi")
        self._badge = _Badge()
        self._resolution = label("", "type.monoXs", "lo")
        self._menu = IconButton("dots", "More for this monitor", size="sm")
        self._menu.hide()
        head.addWidget(self._glyph)
        head.addWidget(self._name)
        head.addWidget(self._badge)
        head.addStretch(1)
        head.addWidget(self._resolution)
        head.addWidget(self._menu)
        column.addLayout(head)

        self._title = label("", "type.body" if detail else "type.bodySm",
                            "hi" if detail else "body")
        self._title.setMinimumWidth(1)
        self._title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._meta = label("", "type.monoXs" if not detail else "type.label",
                           "lo" if not detail else "mid")
        if not detail:
            # the compact card's words share the width left of nothing; let
            # them be cut rather than widen the card
            self._meta.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._count = label("", "type.monoSm", "mid")
        self._note = label("", "type.monoXs", "lo")
        self._note.hide()
        if detail:
            self._build_detail(column)
        else:
            self._build_compact(column)
        if view is not None:
            self.set_view(view)

    def variant(self) -> str:
        return "detail" if self._detail else "compact"

    def _build_compact(self, column: QVBoxLayout) -> None:
        body = QHBoxLayout()
        body.setSpacing(theme.SP_10)
        self._thumb = Thumb("card")
        words = QVBoxLayout()
        words.setSpacing(theme.SP_2 + 1)
        words.addStretch(1)
        words.addWidget(self._title)
        words.addWidget(self._meta)
        words.addStretch(1)
        body.addWidget(self._thumb)
        body.addLayout(words, 1)
        column.addLayout(body)
        progress = QHBoxLayout()
        progress.setSpacing(theme.SP_8 + 1)
        self._bar = ProgressBar(height=6)
        progress.addWidget(self._bar, 1, Qt.AlignVCenter)
        progress.addWidget(self._count)
        column.addLayout(progress)
        column.addWidget(self._note)

    def _build_detail(self, column: QVBoxLayout) -> None:
        top = QHBoxLayout()
        top.setSpacing(theme.SP_12 + 1)
        self._ring = ProgressRing(58)
        numbers = QVBoxLayout()
        numbers.setSpacing(theme.SP_6)
        count = QHBoxLayout()
        count.setSpacing(theme.SP_6)
        self._position = label("", "type.count", "hi")
        self._of = label("", "type.metric", "lo")
        count.addWidget(self._position, 0, Qt.AlignBaseline)
        count.addWidget(self._of, 0, Qt.AlignBaseline)
        count.addStretch(1)
        self._cycle = label("shown this cycle", "type.label", "mid")
        numbers.addStretch(1)
        numbers.addLayout(count)
        numbers.addWidget(self._cycle)
        numbers.addStretch(1)
        top.addWidget(self._ring)
        top.addLayout(numbers, 1)
        column.addLayout(top)
        self._thumb = Thumb("wide")
        column.addWidget(self._thumb)
        words = QVBoxLayout()
        words.setSpacing(theme.SP_4)
        words.addWidget(self._title)
        who = QHBoxLayout()
        who.setSpacing(theme.SP_6 + 1)
        who.addWidget(self._meta)
        self._chip = Chip("Known")
        self._chip.hide()
        who.addWidget(self._chip)
        who.addStretch(1)
        words.addLayout(who)
        column.addLayout(words)
        self._facts = _Facts()
        column.addWidget(self._facts)
        column.addWidget(self._note)

    # -- filling it

    def view(self) -> MonitorView | None:
        return self._view

    def set_view(self, view: MonitorView, now=None) -> None:
        self._view = view
        self._glyph.set_icon("monitor", view.icon_tone())
        self._name.setText(view.name)
        self._badge.set_badge(view.badge())
        self._resolution.setText(view.resolution)
        self._title.setText(view.title_text())
        self._title.setToolTip(view.title if view.title else "")
        self._note.setText(view.note)
        self._note.setVisible(bool(view.note))
        self._thumb.set_source(view.preview if view.state != "disconnected" else None)
        if self._detail:
            self._meta.setText(view.author or fmt.DASH)
            self._chip.setVisible(bool(view.author_chip) and view.state != "disconnected")
            if view.author_chip:
                self._chip.set_variant(view.author_chip)
            if view.state == "disconnected":
                self._meta.setText(view.meta_text(now))
            if view.total:
                self._position.setText(fmt.count(view.position))
                self._of.setText(f"/ {fmt.count(view.total)}")
            else:
                self._position.setText(fmt.DASH)
                self._of.setText("")
            self._ring.set_value(view.position, view.total)
            self._facts.set_view(view, now)
        else:
            self._meta.setText(view.meta_text(now))
            self._count.setText(view.count_text())
            self._bar.set_tone(view.bar_tone())
            self._bar.set_fraction(view.fraction())
        self.setAccessibleName(view.name)
        self.setAccessibleDescription(f"{view.title_text()}, {view.count_text()}")

    def set_menu(self, menu) -> None:
        """The overflow button, with this menu; None takes it away."""
        self._menu.setMenu(menu)
        self._menu.setPopupMode(IconButton.InstantPopup)
        self._menu.setVisible(menu is not None)

    # -- for tests and snapshots

    def texts(self) -> dict[str, str]:
        """What the card says, by part."""
        out = {"name": self._name.text(),
               "badge": self._badge.text() if self._badge.isVisibleTo(self) else "",
               "resolution": self._resolution.text(), "title": self._title.text(),
               "meta": self._meta.text(),
               "note": self._note.text() if self._note.isVisibleTo(self) else ""}
        if self._detail:
            out["count"] = f"{self._position.text()} {self._of.text()}".strip()
            out["ring"] = self._ring.label()
            out["facts"] = " | ".join(self._facts.value_texts())
            out["chip"] = self._chip.text() if self._chip.isVisibleTo(self) else ""
        else:
            out["count"] = self._count.text()
            out["bar"] = self._bar.colour_token()
        return out
