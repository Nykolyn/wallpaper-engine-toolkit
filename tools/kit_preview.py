"""The kit preview: the design system, drawn by the app's own code.

A development tool, not part of the app: it is not bundled and nothing in `app/`
imports it. It lays out every token, type style, icon and loop the way the
design system page does, so a change to the theme can be looked at in one
place, and compared with the design's own pictures.

    .venv\\Scripts\\python.exe tools\\kit_preview.py
    .venv\\Scripts\\python.exe tools\\kit_preview.py --grab icons icons.png

`--grab <section> <out.png>` renders one section offscreen at 100 % and saves
it; `--grab all <folder>` saves every section. The sections are the design
system's: color, type, space, icons, motion. Each kit step adds its own.
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
from app.ui.kit import icons  # noqa: E402

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


def divider() -> QFrame:
    line = QFrame()
    line.setFixedHeight(1)
    line.setStyleSheet(f"background: {theme.css('border.hairline')};")
    return line


class Page(QWidget):
    """The design system page's ground: `bg.app`, like the app's window."""

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt's name
        painter = QPainter(self)
        theme.paint_app_background(painter, self.rect())


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
            self.body.addWidget(note)

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
    "type.caption": "kept for 30 days",
    "type.overline": "The loop",
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


# ---- the page -------------------------------------------------------------------------

# Every section, in the design system's order. Kit steps add theirs here.
SECTIONS = {
    "color": color_section,
    "type": type_section,
    "space": space_section,
    "motion": motion_section,
    "icons": icons_section,
    "controls": controls_section,
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


def wait(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def grab(which: str, out: Path) -> list[Path]:
    """Save one section (or all of them, into a folder) as the design crops them."""
    names = list(SECTIONS) if which == "all" else [which]
    page, built = build_page(names)
    page.show()
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
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
