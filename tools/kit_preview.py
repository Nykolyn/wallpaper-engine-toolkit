"""The kit preview: the design system, drawn by the app's own code.

A development tool, not part of the app: it is not bundled and nothing in `app/`
imports it. It lays out every token, type style, icon and loop the way the
design system page does, so a change to the theme can be looked at in one
place, and compared with the design's own pictures.

    .venv\\Scripts\\python.exe tools\\kit_preview.py
    .venv\\Scripts\\python.exe tools\\kit_preview.py --grab icons icons.png

`--grab <section> <out.png>` renders one section offscreen at 100 % and saves
it; `--grab all <folder>` saves every section. The sections are the design
system's: color, type, space, motion, icons, controls (Qt's own widgets under
the stylesheet), then the kit's buttons, inputs, selection, chips and panels,
each state in a row. Each kit step adds its own.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

GRAB = "--grab" in sys.argv[1:]
if GRAB:
    # Offscreen has no fonts of its own; point it at Windows' so a grab uses
    # the faces the app does.
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QT_QPA_FONTDIR",
                          str(Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QEventLoop, QPointF, QRectF, QSize, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPen  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QCheckBox, QComboBox, QFrame, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QProgressBar, QPushButton,
    QScrollArea, QSizePolicy, QSpinBox, QTableWidget, QTableWidgetItem, QTabBar,
    QVBoxLayout, QWidget,
)

from app import animations, theme  # noqa: E402
from app.ui.kit import base as kit_base, icons  # noqa: E402

PAGE_WIDTH = 1440
PAGE_PADDING = (48, 44, 48, 64)      # left, top, right, bottom, as the design page


# ---- small builders -----------------------------------------------------------

def text(content: str, type_token: str = "type.bodySm", color: str = "text.body",
         wrap: bool = False) -> QLabel:
    label = QLabel(content)
    label.setFont(theme.font(type_token))
    label.setStyleSheet(f"color: {theme.css(color)};")
    label.setWordWrap(wrap)
    return label


def overline(content: str) -> QLabel:
    return text(content, "type.overline", "text.lo")


def divider(token: str = "border.hairline") -> QFrame:
    line = QFrame()
    line.setFixedHeight(1)
    line.setStyleSheet(f"background: {theme.css(token)};")
    return line


class Page(QWidget):
    """The design system page's ground: `bg.app`, like the app's window. It is
    the surface the kit's controls draw their shadows and focus rings on."""

    def __init__(self):
        super().__init__()
        kit_base.declare(self)
        self.setFocusPolicy(Qt.ClickFocus)

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        painter = QPainter(self)
        theme.paint_app_background(painter, self.rect())
        kit_base.paint(painter, self, event.rect())


class Section(QWidget):
    """A numbered section: "01  Colour", a line of description, then content."""

    def __init__(self, number: int, title: str, description: str = ""):
        super().__init__()
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(0, 34, 0, 34)
        self.body.setSpacing(18)
        head = QHBoxLayout()
        head.setSpacing(12)
        head.addWidget(text(f"{number:02d}", "type.label", "accent.hover"),
                       0, Qt.AlignBottom)
        head.addWidget(text(title, "type.h1", "text.hi"), 0, Qt.AlignBottom)
        head.addStretch()
        self.body.addLayout(head)
        if description:
            note = text(description, "type.bodySm", "text.mid", wrap=True)
            note.setMaximumWidth(760)
            # In a row with a stretch, so its wrapped height is measured at the
            # 760 px it gets, not at the page's width: measured wide, two lines
            # count as one and every row below is squeezed.
            line = QHBoxLayout()
            line.addWidget(note, 1)
            line.addStretch(0)
            self.body.addLayout(line)

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        painter = QPainter(self)
        painter.fillRect(QRectF(0, 0, self.width(), 1), theme.color("border.hairline"))


# ---- 01 colour -------------------------------------------------------------------

COLOR_GROUPS = (
    ("Background & chrome", ("bg.", "chrome.", "nav.", "scrim")),
    ("Surface", ("surface.",)),
    ("Border", ("border.", "focus.", "sheen")),
    ("Text", ("text.",)),
    ("Accent & status", ("accent", "ok", "warn", "danger", "info")),
    ("Console", ("console.",)),
    ("Tray and kind", ("tray.", "kind.")),
)


class Swatch(QWidget):
    """A token painted as the design shows it: over the page, with a hairline."""

    def __init__(self, token: str):
        super().__init__()
        self.token = token
        self.setFixedSize(38, 38)

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self.token == "bg.app":
            painter.setBrush(theme.app_background(self.rect()))
        elif self.token in ("surface.glass", "nav.gradient"):
            painter.setBrush(QBrush(theme.gradient(self.token, rect)))
        else:
            painter.setBrush(theme.color(self.token))
        painter.setPen(QPen(theme.color("border.hairline"), 1))
        painter.drawRoundedRect(rect, theme.R_ROW, theme.R_ROW)


def _gradient_text(name: str) -> str:
    """A gradient's stops, short: `.115 → .05 → .015 white`, or the colours."""
    stops = [theme.parse(value) for _, value in theme.GRADIENTS[name]]
    if all(c.rgb() == 0xFFFFFFFF for c in stops):
        return " → ".join(f"{c.alphaF():.3f}".rstrip("0").lstrip("0") for c in stops) + " white"
    return " → ".join(c.name().upper() for c in stops)


def color_section() -> Section:
    section = Section(1, "Colour",
                      "Glass panels are translucent: a colour is only legible if it "
                      "survives the panel wash over the app gradient. Panel ground "
                      f"measures ≈{theme.panel_ground().name().upper()} — text.lo is the "
                      f"floor at {theme.contrast(theme.color('text.lo'), theme.panel_ground()):.1f}:1.")
    entries = [(name, _gradient_text(name)) for name in theme.GRADIENTS]
    entries += list(theme.TOKENS.items())
    for title, prefixes in COLOR_GROUPS:
        group = [(n, v) for n, v in entries if n.startswith(prefixes)]
        entries = [(n, v) for n, v in entries if not n.startswith(prefixes)]
        box = QVBoxLayout()
        box.setSpacing(12)
        box.addWidget(overline(title))
        grid = QGridLayout()
        grid.setHorizontalSpacing(22)
        grid.setVerticalSpacing(14)
        for i, (name, value) in enumerate(group):
            cell = QHBoxLayout()
            cell.setSpacing(10)
            cell.addWidget(Swatch(name))
            words = QVBoxLayout()
            words.setSpacing(1)
            words.addWidget(text(name, "type.bodySm", "text.body"))
            value_label = text(value, "type.monoSm", "text.lo")
            # a long value is cut at the column, as the design's ellipsis does
            value_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            words.addWidget(value_label)
            cell.addLayout(words, 1)
            grid.addLayout(cell, i // 3, i % 3)
        for column in range(3):
            grid.setColumnStretch(column, 1)
        box.addLayout(grid)
        section.body.addLayout(box)
        section.body.addSpacing(8)
    assert not entries, f"tokens in no group: {entries}"
    return section


# ---- 02 type ----------------------------------------------------------------------

TYPE_SAMPLES = {
    "type.display": "412/1000",
    "type.numeric": "33 421",
    "type.count": "4 / 201",
    "type.metricLg": "≈6 min",
    "type.metric": "1 000",
    "type.h1": "Toolkit for Wallpaper Engine",
    "type.hero": "Nothing to review yet",
    "type.h2": "Tracker",
    "type.h3": "The four steps",
    "type.body": "Move 1 000 new folders in",
    "type.bodySm": "Drawn at random from 8 204 never used",
    "type.label": "Verify after move",
    "type.labelStrong": "Move empties the source folder",
    "type.caption": "kept for 30 days",
    "type.overline": "The loop",
    "type.chip": "New author",
    "type.mono": r"D:\reserve\1234567890",
    "type.monoSm": "412 / 1 000 · 41%",
}


def _type_spec(token: str) -> str:
    spec = theme.TYPE[token]
    parts = [f"{spec.px:g}px/{spec.line:g}", str(spec.weight), "mono" if spec.mono else "sans"]
    if spec.tracking:
        parts.append(f".{round(spec.tracking * 100):02d}em")
    if spec.upper:
        parts.append("UPPER")
    return " · ".join(parts)


def type_section() -> Section:
    section = Section(2, "Typography",
                      "Segoe UI Variable Text for prose and UI; Consolas for anything "
                      "counted, pathed or logged. Numerals in mono are tabular so "
                      "columns do not jitter while counting.")
    for token in theme.TYPE:
        row = QHBoxLayout()
        row.setSpacing(14)
        name = QVBoxLayout()
        name.setContentsMargins(0, 0, 0, 0)
        name.setSpacing(1)
        name.addWidget(text(token, "type.bodySm", "text.body"))
        name.addWidget(text(_type_spec(token), "type.monoSm", "text.lo"))
        holder = QWidget()
        holder.setLayout(name)
        holder.setFixedWidth(216)
        row.addWidget(holder)
        sample = text(TYPE_SAMPLES.get(token, token), token,
                      "text.lo" if token in ("type.caption", "type.overline") else "text.hi")
        row.addWidget(sample, 1)
        section.body.addLayout(row)
        section.body.addWidget(divider())
    return section


# ---- 03 space, radius, elevation ------------------------------------------------------

class Bar(QWidget):
    """A spacing step: the width it names, in accent."""

    def __init__(self, width: int):
        super().__init__()
        self.setFixedSize(width + 2, 36)

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(theme.color("accent.soft"))
        painter.setPen(QPen(QColor(theme.css("accent", 0.55)), 1))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 2, 2)


def paint_tile(painter: QPainter, rect: QRectF, radius: float, elev: str = "elev.0",
               border: str = "border.hairline") -> None:
    """A glass tile with a radius and an elevation, painted the way GlassPanel will be."""
    radius = min(radius, rect.height() / 2)
    e = theme.ELEVATION[elev]
    if not e.inset:
        theme.paint_shadow(painter, rect, elev, radius)
    painter.setBrush(QBrush(theme.gradient("surface.glass", rect)))
    painter.setPen(QPen(theme.color(border), 1))
    painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
    if e.inset:
        theme.paint_shadow(painter, rect, elev, radius)
    if e.sheen:
        # the inset top light: a line one px inside the top edge
        sheen = QColor(255, 255, 255)
        sheen.setAlphaF(e.sheen)
        painter.setPen(QPen(sheen, 1))
        y = rect.top() + 1.5
        painter.drawLine(QPointF(rect.left() + radius, y), QPointF(rect.right() - radius, y))


class Tile(QWidget):
    """One radius specimen."""

    def __init__(self, width: int, height: int, radius: float):
        super().__init__()
        self.radius = radius
        self.setFixedSize(width, height)

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        paint_tile(painter, QRectF(self.rect()), self.radius, border="border.control")


class ElevationRow(QWidget):
    """The five elevations side by side, in one widget so a shadow can spread
    past its own tile the way it does in the app."""

    TILE = QSize(96, 52)
    GAP = 28
    TOP = 40          # room for elev.3 to fade out above its tile, not be cut

    def __init__(self):
        super().__init__()
        reach = max(e.dy + 3 * e.blur / 2 for e in theme.ELEVATION.values())
        self.setFixedSize(len(theme.ELEVATION) * (self.TILE.width() + self.GAP),
                          int(self.TOP + self.TILE.height() + reach))

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        tiles = []
        for i, name in enumerate(theme.ELEVATION):
            rect = QRectF(i * (self.TILE.width() + self.GAP) + 6, self.TOP,
                          self.TILE.width(), self.TILE.height())
            paint_tile(painter, rect, theme.R_LG, name)
            tiles.append((name, rect))
        painter.setFont(theme.font("type.monoSm"))
        for name, rect in tiles:
            below = QRectF(rect.left() - 20, rect.bottom() + 8, rect.width() + 40, 16)
            painter.setPen(theme.color("text.body"))
            painter.drawText(below, Qt.AlignHCenter, name)
            painter.setPen(theme.color("text.lo"))
            painter.drawText(below.translated(0, 18), Qt.AlignHCenter, ELEVATION_NOTES[name])


def _captioned(widget: QWidget, name: str, note: str) -> QVBoxLayout:
    column = QVBoxLayout()
    column.setSpacing(7)
    column.addWidget(widget, 0, Qt.AlignHCenter)
    column.addWidget(text(name, "type.monoSm", "text.body"), 0, Qt.AlignHCenter)
    column.addWidget(text(note, "type.monoSm", "text.lo"), 0, Qt.AlignHCenter)
    return column


RADIUS_NOTES = {"r.sm": "chips, thumbs", "r.md": "buttons, inputs", "r.row": "rows, nav items",
                "r.lg": "cards, panels", "r.xl": "dialogs", "r.pill": "badges, toggles"}
ELEVATION_NOTES = {"elev.0": "flush, inside a card", "elev.1": "controls",
                   "elev.2": "cards, panels", "elev.3": "dialog, menu, toast",
                   "elev.inset": "wells, pressed"}


def space_section() -> Section:
    section = Section(3, "Spacing, radius, elevation")

    section.body.addWidget(overline("Spacing — 2px base"))
    bars = QHBoxLayout()
    bars.setSpacing(16)
    for name, value in theme.SPACING.items():
        column = QVBoxLayout()
        column.setSpacing(6)
        column.addWidget(Bar(value), 0, Qt.AlignHCenter | Qt.AlignBottom)
        column.addWidget(text(name, "type.monoSm", "text.lo"), 0, Qt.AlignHCenter)
        bars.addLayout(column)
    bars.addStretch()
    section.body.addLayout(bars)
    section.body.addWidget(divider())

    section.body.addWidget(overline("Radius"))
    radii = QHBoxLayout()
    radii.setSpacing(24)
    for name, value in theme.RADIUS.items():
        radii.addLayout(_captioned(Tile(58, 42, value),
                                   name, f"{value}px · {RADIUS_NOTES[name]}"))
    radii.addStretch()
    section.body.addLayout(radii)
    section.body.addWidget(divider())

    section.body.addWidget(overline("Elevation"))
    section.body.addWidget(ElevationRow())
    return section


# ---- 05 icons ---------------------------------------------------------------------

class IconTile(QWidget):
    def __init__(self, name: str):
        super().__init__()
        self.name = name
        self.setFixedSize(138, 60)

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setBrush(QBrush(theme.gradient("surface.glass", rect)))
        painter.setPen(QPen(theme.color("border.hairline"), 1))
        painter.drawRoundedRect(rect, theme.R_LG, theme.R_LG)
        glyph = icons.pixmap(self.name, "text.body", 16, self.devicePixelRatioF())
        painter.drawPixmap(QPointF((self.width() - 16) / 2, 13), glyph)
        painter.setFont(theme.font("type.monoSm"))
        painter.setPen(theme.color("text.lo"))
        painter.drawText(QRectF(0, 36, self.width(), 16), Qt.AlignHCenter, self.name)


DESIGN_SET = 26


def icons_section() -> Section:
    section = Section(5, "Icons",
                      "16px grid, 1.3px stroke, no fill, drawn in any token. 18px in "
                      "empty states, 13px inline with 11px text. The first 26 are the "
                      "design system's set; the rest are glyphs the screens use.")
    for title, names in (("The set", icons.NAMES[:DESIGN_SET]),
                         ("From the screens", icons.NAMES[DESIGN_SET:])):
        section.body.addWidget(overline(title))
        grid = QGridLayout()
        grid.setSpacing(12)
        for i, name in enumerate(names):
            grid.addWidget(IconTile(name), i // 9, i % 9)
        grid.setColumnStretch(9, 1)
        section.body.addLayout(grid)
    return section


# ---- 04 motion ----------------------------------------------------------------------

class Specimen(QWidget):
    """A live loop, painted from its shared driver the way kit widgets will."""

    def __init__(self, kind: str, size: QSize):
        super().__init__()
        self.kind = kind
        self.driver = animations.loop(kind)
        self.setFixedSize(size)
        self.driver.subscribe(self)

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        getattr(self, f"_paint_{self.kind}")(painter)

    def _paint_spin(self, painter: QPainter) -> None:
        rect = QRectF(4, 4, 34, 34).adjusted(2, 2, -2, -2)
        painter.setPen(QPen(theme.color("surface.well"), 4))
        painter.drawEllipse(rect)
        painter.setPen(QPen(theme.color("accent"), 4, Qt.SolidLine, Qt.RoundCap))
        start = 90 - self.driver.value()
        painter.drawArc(rect, int(start * 16), int(-90 * 16))
        if not animations.ENABLED:
            painter.setFont(theme.font("type.bodySm"))
            painter.setPen(theme.color("text.mid"))
            painter.drawText(QRectF(48, 0, 120, 42), Qt.AlignVCenter, "working")

    def _paint_pulse(self, painter: QPainter) -> None:
        dot = theme.color("accent")
        dot.setAlphaF(self.driver.value())
        painter.setPen(Qt.NoPen)
        painter.setBrush(dot)
        painter.drawEllipse(QRectF(4, 17, 8, 8))
        painter.setFont(theme.font("type.bodySm"))
        painter.setPen(theme.color("text.body"))
        painter.drawText(QRectF(20, 0, 200, 42), Qt.AlignVCenter, "step 2 of 4")

    def _paint_shimmer(self, painter: QPainter) -> None:
        painter.setPen(Qt.NoPen)
        for i, width in enumerate((0.8, 0.55, 0.65)):
            bar = theme.color("surface.raised")
            bar.setAlphaF(bar.alphaF() * self.driver.value(delay=200 * i))
            painter.setBrush(bar)
            painter.drawRoundedRect(QRectF(0, 6 + 14 * i, self.width() * width, 8), 4, 4)

    def _paint_indeterminate(self, painter: QPainter) -> None:
        track = QRectF(0, (self.height() - 8) / 2, self.width(), 8)
        painter.setPen(Qt.NoPen)
        painter.setBrush(theme.color("surface.well"))
        painter.drawRoundedRect(track, 4, 4)
        sweep = track.width() * 0.38
        # translateX(-100%) → translateX(265%) of the sweep's own width
        x = track.left() + sweep * (-1.0 + 3.65 * self.driver.value())
        grad = QLinearGradient(QPointF(x, 0), QPointF(x + sweep, 0))
        clear = theme.color("accent")
        clear.setAlpha(0)
        grad.setColorAt(0, clear)
        grad.setColorAt(0.5, theme.color("accent"))
        grad.setColorAt(1, clear)
        painter.save()
        path_rect = track
        painter.setClipRect(path_rect)
        painter.setBrush(QBrush(grad))
        painter.drawRoundedRect(QRectF(x, track.top(), sweep, track.height()), 4, 4)
        painter.restore()


MOTION_ROWS = (
    ("motion.fast", f"{animations.FAST}ms · ease.standard", "hover tint, icon colour"),
    ("motion.base", f"{animations.BASE}ms · ease.standard", "button fill, toggle knob, cross-fade"),
    ("motion.slow", f"{animations.SLOW}ms · ease.standard", "progress width, panel expand/collapse"),
    ("ease.standard", "cubic-bezier(.2,.7,.3,1)", "everything except spinners"),
    ("flash", f"{animations.FLASH}ms · ease.standard", "a count that changed by itself"),
)


def motion_section() -> Section:
    state = "on" if animations.ENABLED else "off (Windows animations are off)"
    section = Section(4, "Motion",
                      "Three durations and one curve carry the whole app; the loops "
                      "share one clock per kind and stop when nothing shows them. "
                      f"Motion is {state}.")
    for name, value, use in MOTION_ROWS:
        row = QHBoxLayout()
        label = text(name, "type.bodySm", "text.body")
        label.setFixedWidth(196)
        row.addWidget(label)
        timing = text(value, "type.monoSm", "accent.hover")
        timing.setFixedWidth(266)
        row.addWidget(timing)
        row.addWidget(text(use, "type.bodySm", "text.mid"), 1)
        section.body.addLayout(row)
        section.body.addWidget(divider())

    section.body.addWidget(overline("Loops, live"))
    specimens = QHBoxLayout()
    specimens.setSpacing(28)
    for kind, size in (("spin", QSize(170, 42)), ("pulse", QSize(170, 42)),
                       ("shimmer", QSize(220, 42)), ("indeterminate", QSize(220, 42))):
        period = animations.LOOPS[kind][0]
        specimens.addLayout(_captioned(Specimen(kind, size), f"anim.{kind}", f"{period}ms"))
    specimens.addStretch()
    section.body.addLayout(specimens)
    return section


# ---- standard controls ------------------------------------------------------------

def _row(label: str, *widgets: QWidget) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(14)
    name = text(label, "type.bodySm", "text.body")
    name.setFixedWidth(196)
    row.addWidget(name)
    for widget in widgets:
        row.addWidget(widget)
    row.addStretch()
    return row


def _disabled(widget: QWidget) -> QWidget:
    widget.setEnabled(False)
    return widget


def controls_section() -> Section:
    """What the generated stylesheet does to Qt's own widgets. The old tabs are
    made of these until their pages are rebuilt from the kit."""
    section = Section(6, "Standard controls",
                      "Qt's own widgets, drawn by the stylesheet theme.py generates: "
                      "what the tabs not yet rebuilt are made of. Every state that "
                      "can be set without a pointer is shown.")

    start = QPushButton(icons.icon("play", "text.onAccent"), "Start run")
    theme.make_accent(start)
    stopped = QPushButton(icons.icon("play", "text.onAccent"), "Start run")
    theme.make_accent(stopped)
    section.body.addLayout(_row("QPushButton", start, _disabled(stopped),
                                QPushButton(icons.icon("refresh"), "Rescan"),
                                QPushButton("Playlist settings"),
                                _disabled(QPushButton(icons.icon("stop"), "Stop"))))

    filled = QLineEdit("sunset")
    empty = QLineEdit()
    empty.setPlaceholderText("Filter by author…")
    off = QLineEdit()
    off.setPlaceholderText("Filter by author…")
    for field in (filled, empty, off):
        field.setFixedWidth(190)
    section.body.addLayout(_row("QLineEdit", empty, filled, _disabled(off)))

    spin = QSpinBox()
    spin.setRange(1, 100000)
    spin.setValue(1000)
    top = QSpinBox()
    top.setRange(1, 1000)
    top.setValue(1000)
    minutes = QSpinBox()
    minutes.setSuffix(" min")
    minutes.setValue(5)
    section.body.addLayout(_row("QSpinBox", spin, top, minutes, _disabled(QSpinBox())))

    combo = QComboBox()
    combo.addItems(["Random from unused", "Random from all", "Oldest first"])
    combo.setFixedWidth(200)
    still = QComboBox()
    still.addItems(["Random from unused"])
    still.setFixedWidth(200)
    section.body.addLayout(_row("QComboBox", combo, _disabled(still)))

    on = QCheckBox("Verify after move")
    on.setChecked(True)
    partly = QCheckBox("Some selected")
    partly.setTristate(True)
    partly.setCheckState(Qt.PartiallyChecked)
    locked = QCheckBox("Verify after move")
    locked.setChecked(True)
    section.body.addLayout(_row("QCheckBox", QCheckBox("Verify after move"), on, partly,
                                _disabled(QCheckBox("Verify after move")), _disabled(locked)))

    bars = []
    for value in (0, 41, 100):
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(value)
        bar.setTextVisible(value == 41)
        bar.setFixedWidth(200)
        bars.append(bar)
    section.body.addLayout(_row("QProgressBar", *bars))

    tabs = QTabBar()
    for name in ("Rotate", "Reserve", "History"):
        tabs.addTab(name)
    tabs.setCurrentIndex(1)
    section.body.addLayout(_row("QTabBar", tabs))

    items = QListWidget()
    items.addItems([f"folder {n:04d}" for n in range(1, 30)])
    items.setCurrentRow(2)
    items.setFixedSize(240, 150)
    table = QTableWidget(12, 2)
    table.setHorizontalHeaderLabels(["Folder path", "Copies"])
    table.setAlternatingRowColors(True)
    for row in range(12):
        table.setItem(row, 0, QTableWidgetItem(f"folder {row + 1:04d}"))
        table.setItem(row, 1, QTableWidgetItem("2"))
    table.selectRow(1)
    table.setFixedSize(360, 150)
    table.setSortingEnabled(True)
    table.sortByColumn(0, Qt.AscendingOrder)
    section.body.addLayout(_row("Lists and tables", items, table))

    box = QGroupBox("QGroupBox")
    inside = QVBoxLayout(box)
    inside.addWidget(text("A panel of the old tabs: glass, with its title above.",
                          "type.bodySm", "text.mid"))
    box.setFixedWidth(420)
    section.body.addLayout(_row("QGroupBox", box))
    return section


# ---- kit controls: a state table like the design system's ------------------------------

NAME_COLUMN = 210


class StateTable:
    """Rows of one component, a column per state, as the design system lays
    out its Buttons, Inputs and Selection sections."""

    def __init__(self, section: Section, columns: tuple[str, ...]):
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(14)
        self.grid.setVerticalSpacing(13)
        self.grid.setColumnMinimumWidth(0, NAME_COLUMN)
        for i, name in enumerate(columns, start=1):
            self.grid.addWidget(overline(name), 0, i)
            self.grid.setColumnStretch(i, 1)
        self.grid.addWidget(divider(), 1, 0, 1, len(columns) + 1)
        self.width = len(columns) + 1
        self.row = 2
        section.body.addLayout(self.grid)

    def add(self, name: str, note: str, *widgets: QWidget) -> None:
        words = QVBoxLayout()
        words.setSpacing(2)
        words.addWidget(text(name, "type.bodySm", "text.body"))
        if note:
            words.addWidget(text(note, "type.monoSm", "text.lo"))
        self.grid.addLayout(words, self.row, 0)
        for i, widget in enumerate(widgets, start=1):
            self.grid.addWidget(widget, self.row, i, Qt.AlignLeft | Qt.AlignVCenter)
        self.grid.addWidget(divider("chrome.divider"), self.row + 1, 0, 1, self.width)
        self.row += 2


def in_state(widget: QWidget, state: str | None) -> QWidget:
    """A kit control shown in one state: None, hover, pressed, focus or disabled."""
    if state == "disabled":
        widget.setEnabled(False)
    elif state:
        widget.force_state = state
    return widget


BUTTON_STATES = ("DEFAULT", "HOVER", "PRESSED", "DISABLED", "FOCUS")
_BUTTON_ORDER = (None, "hover", "pressed", "disabled", "focus")


def buttons_section() -> Section:
    from app.ui.kit import (AccentButton, DangerButton, GhostButton, IconButton,
                            SecondaryButton)

    section = Section(7, "Buttons",
                      "Every interactive widget has these five states. Hover is the "
                      "pointer only; the focus ring shows on keyboard focus only. The "
                      "fill eases over motion.base; the box never moves.")
    table = StateTable(section, BUTTON_STATES)
    rows = (
        ("AccentButton", "one per screen", lambda: AccentButton("Start run")),
        ("SecondaryButton", "default weight", lambda: SecondaryButton("Playlist settings")),
        ("DangerButton", "destructive only", lambda: DangerButton("Delete")),
        ("GhostButton", "inline, low weight", lambda: GhostButton("Open folder")),
        ("GhostButton outlined", "page header", lambda: GhostButton("Skip for now", outlined=True)),
        ("IconButton", "30×30 · 16px glyph", lambda: IconButton("refresh", "Refresh")),
        ("IconButton sm", "22×22 · 13px glyph", lambda: IconButton("chevR", "Next page", size="sm")),
    )
    for name, note, make in rows:
        table.add(name, note, *(in_state(make(), state) for state in _BUTTON_ORDER))

    section.body.addWidget(overline("Content and sizes"))
    row = QHBoxLayout()
    row.setSpacing(16)
    for widget in (AccentButton("Start run", icon="play"),
                   SecondaryButton("Open destination", icon="folder"),
                   GhostButton("Paste path", icon="clipboard", key="Ctrl+V"),
                   GhostButton("Open log", size="sm"),
                   SecondaryButton("Retry", size="sm"),
                   AccentButton("Choose folders", size="lg"),
                   SecondaryButton("Paste path", icon="clipboard", key="Ctrl+V", size="lg")):
        row.addWidget(widget)
    row.addStretch()
    section.body.addLayout(row)
    section.body.addWidget(text("sm: inline actions in a card header · md: the default · lg: an "
                                "empty state's one action. Every variant of a size has the "
                                "same box, so a row of them lines up.",
                                "type.bodySm", "text.mid", wrap=True))
    return section


def inputs_section() -> Section:
    from app.ui.kit import Dropdown, DropdownPopup, SpinBox, TextInput

    section = Section(8, "Text, number and choice inputs",
                      "Qt's own fields under the generated stylesheet, with the inset "
                      "shade and the focus ring the kit adds. A field you type into shows "
                      "its ring whenever it has focus; a Dropdown only from the keyboard.")
    table = StateTable(section, ("DEFAULT", "HOVER", "FOCUS", "DISABLED", "ERROR / AT MAX"))

    def field(value: str = "", *, search: bool = False, placeholder="Filter by author…"):
        widget = TextInput(value, placeholder=placeholder, search=search)
        widget.setFixedWidth(190)
        return widget

    wrong = field("rain?")
    wrong.set_error("no author by that name")
    table.add("TextInput", "placeholder = text.lo",
              field(), in_state(field(), "hover"), in_state(field("rain"), "focus"),
              in_state(field(), "disabled"), wrong)
    missing = field("zzz", search=True, placeholder="Filter…")
    missing.set_error("nothing matches")
    table.add("TextInput search", "leading glyph",
              field(search=True, placeholder="Filter…"),
              in_state(field(search=True, placeholder="Filter…"), "hover"),
              in_state(field("dune", search=True, placeholder="Filter…"), "focus"),
              in_state(field(search=True, placeholder="Filter…"), "disabled"), missing)

    def spin(maximum: int = 100_000):
        return SpinBox(minimum=1, maximum=maximum, value=1000)

    table.add("SpinBox", "tabular numerals, 1 000",
              spin(), in_state(spin(), "hover"), in_state(spin(), "focus"),
              in_state(spin(), "disabled"), spin(1000))

    def choice(prefix: str = ""):
        widget = Dropdown(prefix=prefix)
        for option in ("Random from unused", "Random from all", "Oldest first", "By author"):
            widget.add_item(option)
        widget.setFixedWidth(190)
        return widget

    table.add("Dropdown", "closed / open below",
              choice(), in_state(choice(), "hover"), in_state(choice(), "focus"),
              in_state(choice(), "disabled"),
              text("see open state below", "type.monoSm", "text.lo"))

    def sources():
        widget = Dropdown()
        widget.add_section("Your folders")
        widget.add_item("all", count=12547)
        widget.add_item("filter", count=836)
        widget.add_item("new", count=3366)
        widget.add_separator()
        for option in ("All folders", "Not in any folder", "Everything you have"):
            widget.add_item(option)
        widget.setCurrentIndex(3)
        widget.setFixedWidth(190)
        return widget

    sort = choice("Sort")
    sort.clear()
    for option in ("Known first", "Most new", "A to Z"):
        sort.add_item(option)
    table.add("Dropdown extras", "prefix, section, counts",
              sort, sources(), in_state(sources(), "focus"))

    section.body.addSpacing(10)
    row = QHBoxLayout()
    row.setSpacing(40)
    for build, hot, note in ((choice, 1, "Dropdown open: the popup on surface.popup at elev.3, 4px "
                                 "padding, rows at r.sm. The chosen row carries accent.soft "
                                 "and a check; the hovered row surface.raised."),
                        (sources, 2, "Section rows (YOUR FOLDERS) and separators are never "
                                  "chosen; counts sit right in mono, and the closed box "
                                  "shows the chosen row's count.")):
        column = QVBoxLayout()
        column.setSpacing(theme.POPUP_GAP)
        box = in_state(build(), "focus")
        column.addWidget(box)
        popup = DropdownPopup(box, embedded=True)
        popup.set_hot_row(hot)
        column.addWidget(popup)
        column.addStretch()
        row.addLayout(column)
        words = text(note, "type.bodySm", "text.mid", wrap=True)
        words.setFixedWidth(250)
        row.addWidget(words, 0, Qt.AlignTop)
    row.addStretch()
    section.body.addLayout(row)
    return section


def selection_section() -> Section:
    from app.ui.kit import Checkbox, Pagination, SegmentedControl, Toggle

    section = Section(9, "Selection controls",
                      "The tick is instant; the Toggle's knob and track move together "
                      "over motion.base, and jump when motion is off.")
    table = StateTable(section, ("OFF", "HOVER", "ON", "INDETERMINATE / ON HOVER",
                                 "DISABLED", "FOCUS"))

    def check(state=Qt.Unchecked):
        widget = Checkbox("Verify after move", tristate=True)
        widget.setCheckState(state)
        return widget

    table.add("Checkbox", "",
              check(), in_state(check(), "hover"), check(Qt.Checked),
              check(Qt.PartiallyChecked), in_state(check(), "disabled"),
              in_state(check(), "focus"))

    def switch(on: bool = False):
        return Toggle("Restart Wallpaper Engine", checked=on)

    table.add("Toggle", "34×18 · knob 12px",
              switch(), in_state(switch(), "hover"), switch(True),
              in_state(switch(True), "hover"), in_state(switch(), "disabled"),
              in_state(switch(), "focus"))

    section.body.addSpacing(12)
    segments = StateTable(section, BUTTON_STATES)

    def segmented(labels=("Queue", "Shown")):
        return SegmentedControl(labels)

    segments.add("SegmentedControl", "2–3 segments · exclusive",
                 *(in_state(segmented(), state) for state in _BUTTON_ORDER))
    segments.add("SegmentedControl 3", "the most it takes",
                 *(in_state(segmented(("All", "Problems", "Shown")), state)
                   for state in _BUTTON_ORDER))
    section.body.addWidget(text(
        "Selected segment is surface.raised + text.hi; the rest sit on transparent with "
        "text.mid. Never more than three segments and never a lone one — for a binary "
        "setting use Toggle.", "type.bodySm", "text.mid", wrap=True))

    section.body.addSpacing(12)
    pages = StateTable(section, ("DEFAULT", "HOVER", "IN THE MIDDLE", "AT LAST PAGE"))
    hovered = Pagination(pages=6, current=1)
    hovered.force_state = "hover"
    pages.add("Pagination", "gallery and table footers",
              Pagination(pages=6, current=1), hovered, Pagination(pages=6, current=3),
              Pagination(pages=6, current=6))
    section.body.addWidget(text(
        "Current page is an accent fill; the rest are ghost. At most four numbers — first, "
        "current, current+1, last — and an ellipsis for each gap. Both chevrons are "
        "IconButtons at 22px and go disabled at the ends. Below two pages the whole "
        "control is hidden, not disabled.", "type.bodySm", "text.mid", wrap=True))
    return section


def chips_section() -> Section:
    from app.ui.kit import Chip
    from app.ui.kit.chips import VARIANTS

    section = Section(10, "Chips and badges",
                      "Chip variants are semantic, not decorative — the colour is the "
                      "meaning. Fourteen in total and no more: a screen that needs a new "
                      "word reuses the nearest variant with its own text.")
    names = list(VARIANTS)
    library, jobs = names[:8], names[8:]

    def strip(variants) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)
        for name in variants:
            row.addWidget(Chip(name))
        row.addStretch()
        return row

    def named(variants, columns: int, notes: dict[str, str] | None = None) -> QGridLayout:
        grid = QGridLayout()
        grid.setHorizontalSpacing(22)
        grid.setVerticalSpacing(12)
        for i, name in enumerate(variants):
            cell = QHBoxLayout()
            cell.setSpacing(9)
            cell.addWidget(Chip(name), 0, Qt.AlignVCenter)
            words = QVBoxLayout()
            words.setSpacing(1)
            words.addWidget(text(f"Chip.{name}", "type.monoSm", "text.lo"))
            if notes:
                words.addWidget(text(notes[name], "type.caption", "text.mid"))
            cell.addLayout(words, 1)
            grid.addLayout(cell, i // columns, i % columns)
        for column in range(columns):
            grid.setColumnStretch(column, 1)
        return grid

    section.body.addLayout(strip(library))
    section.body.addLayout(named(library, 4))
    section.body.addWidget(divider())

    states = QHBoxLayout()
    states.setSpacing(26)
    for state in ("default", "hover", "selected", "disabled"):
        chip = Chip("Duplicated")
        if state == "hover":
            chip.force_state = "hover"
        elif state == "selected":
            chip.set_selected(True)
        elif state == "disabled":
            chip.setEnabled(False)
        pair = QHBoxLayout()
        pair.setSpacing(9)
        pair.addWidget(chip)
        pair.addWidget(text(state, "type.monoSm", "text.lo"))
        states.addLayout(pair)
    own = QHBoxLayout()
    own.setSpacing(9)
    own.addWidget(Chip("Duplicated", "Already have"))
    own.addWidget(text('Chip("Duplicated", "Already have") — text apart from variant',
                       "type.monoSm", "text.lo"))
    states.addLayout(own)
    states.addStretch()
    section.body.addLayout(states)

    section.body.addSpacing(10)
    section.body.addWidget(overline("Job and file state — Creator and Copier"))
    section.body.addLayout(strip(jobs))
    section.body.addLayout(named(jobs, 3, {
        "Tagged": "Creator — file already carries tags",
        "NeedsTags": "Creator — blocks the build unless skipped",
        "AutoTagged": "Creator — tags guessed, not confirmed",
        "Copying": "Copier — job in flight",
        "Done": "Copier and Creator — finished item",
        "Failed": "Copier — job stopped, retry offered",
    }))
    return section


def panels_section() -> Section:
    from app.ui.kit import (Callout, CardTitle, GhostButton, GlassPanel, MetricStrip,
                            Overline, SecondaryButton)

    section = Section(11, "Panels",
                      "GlassPanel is the card: glass, a hairline edge, the sheen, elev.2. "
                      "A verdict changes only its edge. Overline, CardTitle, Callout and "
                      "MetricStrip are what goes inside.")
    tones = QHBoxLayout()
    tones.setSpacing(14)
    for tone, words in ((None, "default"), ("ok", "finished cleanly"),
                        ("warn", "finished with problems"), ("danger", "failed"),
                        ("accent", "the active tool")):
        panel = GlassPanel(tone=tone)
        inside = QVBoxLayout(panel)
        inside.setSpacing(6)
        inside.addWidget(Overline(f"tone={tone or 'none'}"))
        inside.addWidget(text(words, "type.bodySm", "text.mid"))
        panel.setFixedHeight(84)
        tones.addWidget(panel, 1)
    section.body.addLayout(tones)
    section.body.addSpacing(8)

    row = QHBoxLayout()
    row.setSpacing(14)

    left = GlassPanel(tone="warn", padding="lg")
    column = QVBoxLayout(left)
    column.setSpacing(12)
    title = CardTitle("Run 38 finished with 2 problems")
    column.addWidget(title)
    column.addWidget(text("1 000 folders moved in, 998 returned. Two folders were in use "
                          "and stayed where they were.", "type.bodySm", "text.mid", wrap=True))
    column.addWidget(MetricStrip([(1000, "moved in"), (998, "returned"), (3, "duplicates"),
                                  (2, "failed", "danger")]))
    column.addWidget(Callout("Wallpaper Engine is still showing the old playlist. Rebuild it "
                             "from here or restart the app.", tone="danger"))
    column.addStretch()
    row.addWidget(left, 1)

    right = GlassPanel(padding="md")
    column = QVBoxLayout(right)
    column.setSpacing(11)
    head = CardTitle("The loop", "run 38 · started 13:41")
    head.add_action(GhostButton("Open Rotator →", size="sm"))
    column.addWidget(head)
    column.addWidget(Overline("Callouts"))
    column.addWidget(Callout("The Tracker is counting again — 201 on the playlist."))
    column.addWidget(Callout("Timestamps marked ~ were reconstructed after a restart.",
                             tone="info"))
    column.addWidget(Callout("Files are moved out of the source folder, not copied.",
                             tone="warn", title="Move empties the source folder"))
    stopped = Callout("Access denied writing to the destination. The folder is open in "
                      "Wallpaper Engine.", tone="danger", title="A job stopped after 1 item")
    stopped.add_action(SecondaryButton("Retry"))
    stopped.add_action(GhostButton("Skip", size="sm"))
    column.addWidget(stopped)
    column.addWidget(Callout("37 wallpapers created.", tone="ok"))
    column.addWidget(Overline("MetricStrip, unruled"))
    column.addWidget(MetricStrip([(89, "new items seen"), (12, "subscribed", "info"),
                                  (4, "already had", "warn"), (2, "were yours", "ok")],
                                 ruled=False))
    row.addWidget(right, 1)
    section.body.addLayout(row)
    return section


# ---- the page -------------------------------------------------------------------------

# Every section, in the design system's order. Kit steps add theirs here.
SECTIONS = {
    "color": color_section,
    "type": type_section,
    "space": space_section,
    "motion": motion_section,
    "icons": icons_section,
    "controls": controls_section,
    "buttons": buttons_section,
    "inputs": inputs_section,
    "selection": selection_section,
    "chips": chips_section,
    "panels": panels_section,
}


def build_page(names) -> tuple[Page, dict[str, Section]]:
    page = Page()
    page.setFixedWidth(PAGE_WIDTH)
    column = QVBoxLayout(page)
    column.setContentsMargins(*PAGE_PADDING)
    column.setSpacing(0)
    if len(names) > 1:
        column.addWidget(text("Toolkit Design System", "type.h1", "text.hi"))
        column.addWidget(text("drawn by app/theme.py and app/ui/kit", "type.monoSm", "text.lo"))
    built = {}
    for name in names:
        section = SECTIONS[name]()
        built[name] = section
        column.addWidget(section)
    column.addStretch()
    page.adjustSize()
    return page, built


def settle(page: Page) -> None:
    """Size the page again once it is on screen. Widgets take the stylesheet
    when they are first shown, which can change their size; measured before
    that, the page comes out short and Qt squeezes the rows to fit."""
    QApplication.processEvents()
    page.resize(PAGE_WIDTH, page.layout().totalHeightForWidth(PAGE_WIDTH))
    QApplication.processEvents()


def wait(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def grab(which: str, out: Path) -> list[Path]:
    """Save one section (or all of them, into a folder) as the design crops them."""
    names = list(SECTIONS) if which == "all" else [which]
    page, built = build_page(names)
    page.show()
    page.setFocus()               # no control starts with focus it was not given
    settle(page)
    wait(300)                     # let the loops reach a frame worth seeing
    saved = []
    for name, section in built.items():
        target = out / f"{name}.png" if which == "all" else out
        target.parent.mkdir(parents=True, exist_ok=True)
        page.grab(section.geometry()).save(str(target))
        saved.append(target)
    return saved


def main() -> int:
    app = QApplication(sys.argv)
    theme.apply(app)

    args = sys.argv[1:]
    if GRAB:
        i = args.index("--grab")
        try:
            which, out = args[i + 1], Path(args[i + 2])
        except IndexError:
            print("usage: kit_preview.py --grab <section|all> <out.png|folder>")
            return 2
        if which != "all" and which not in SECTIONS:
            print(f"no section {which!r}; there are: {', '.join(SECTIONS)}")
            return 2
        for path in grab(which, out):
            print(f"saved {path}")
        return 0

    page, _ = build_page(list(SECTIONS))
    scroll = QScrollArea()
    scroll.setWindowTitle("Kit preview")
    scroll.setWidget(page)
    scroll.setWidgetResizable(False)
    scroll.setAlignment(Qt.AlignHCenter)
    scroll.setStyleSheet(f"QScrollArea {{ background: {theme.css('bg.solid')}; }}")
    scroll.resize(PAGE_WIDTH + 24, 900)
    scroll.show()
    page.setFocus()
    settle(page)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
