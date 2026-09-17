"""The one place the toolkit's look is defined.

Colours used to be typed into each tab by hand — dark log panels inside
otherwise light Fusion windows, three copies of the same card palette, a dozen
one-off font sizes. Everything visual now comes from here: a token table, a
QPalette for the widgets Qt draws itself, and one stylesheet for the rest.

Switching the whole app to a light look is `PALETTES["light"]`; nothing outside
this module names a colour.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QColor, QFont, QIcon, QPalette
from PySide6.QtWidgets import QApplication


# ---- Tokens ---------------------------------------------------------------

DARK = {
    "bg": "#16181D",            # window behind everything
    "surface": "#1E2128",       # panels, group boxes, inputs
    "raised": "#272B34",        # buttons, hovered rows, tab bar
    "border": "#333945",
    "border_strong": "#414957",

    "text": "#E6E9EF",
    "muted": "#98A1B3",         # secondary lines, hints
    "faint": "#6E7789",         # timestamps, footnotes

    "accent": "#4C8DFF",
    "accent_hover": "#6BA1FF",
    "accent_pressed": "#3A79E6",
    "accent_text": "#FFFFFF",

    "ok": "#3DD68C",
    "warn": "#F5A524",
    "danger": "#F04A5C",
    "info": "#4C8DFF",

    "console": "#101319",       # log panel background
    "console_text": "#C3CAD9",
}

LIGHT = {
    "bg": "#F4F5F7",
    "surface": "#FFFFFF",
    "raised": "#ECEEF2",
    "border": "#D9DEE7",
    "border_strong": "#C2C9D6",

    "text": "#1B1F27",
    "muted": "#5A6375",
    "faint": "#8A93A6",

    "accent": "#2F6FE4",
    "accent_hover": "#3F7DEE",
    "accent_pressed": "#265CC0",
    "accent_text": "#FFFFFF",

    "ok": "#129A62",
    "warn": "#B87503",
    "danger": "#D03A4B",
    "info": "#2F6FE4",

    "console": "#1B1F27",       # the log stays dark in both — it reads as output
    "console_text": "#C3CAD9",
}

PALETTES = {"dark": DARK, "light": LIGHT}

# What the app is currently painted with. Read it for a colour, never a literal.
C = DARK

RADIUS = 8
RADIUS_SMALL = 6


# ---- Semantic colours the tabs ask for ------------------------------------

def status_color(kind: str) -> str:
    """Colour for a card border or a status line: ok / bad / done / muted."""
    return {
        "ok": C["ok"],
        "bad": C["danger"],
        "done": C["accent"],
        "muted": C["muted"],
    }.get(kind, C["text"])


def level_color(level: str) -> str:
    """Colour for a log line by its severity."""
    return {
        "INFO": C["console_text"],
        "WARN": C["warn"],
        "ERROR": C["danger"],
    }.get(level, C["console_text"])


# ---- Qt palette -----------------------------------------------------------

def _palette() -> QPalette:
    """Covers what Qt paints without consulting the stylesheet."""
    p = QPalette()
    text = QColor(C["text"])
    muted = QColor(C["muted"])

    p.setColor(QPalette.Window, QColor(C["bg"]))
    p.setColor(QPalette.WindowText, text)
    p.setColor(QPalette.Base, QColor(C["surface"]))
    p.setColor(QPalette.AlternateBase, QColor(C["raised"]))
    p.setColor(QPalette.Text, text)
    p.setColor(QPalette.PlaceholderText, muted)
    p.setColor(QPalette.Button, QColor(C["raised"]))
    p.setColor(QPalette.ButtonText, text)
    p.setColor(QPalette.BrightText, QColor(C["danger"]))
    p.setColor(QPalette.Highlight, QColor(C["accent"]))
    p.setColor(QPalette.HighlightedText, QColor(C["accent_text"]))
    p.setColor(QPalette.Link, QColor(C["accent"]))
    p.setColor(QPalette.ToolTipBase, QColor(C["raised"]))
    p.setColor(QPalette.ToolTipText, text)

    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, QColor(C["faint"]))
    return p


# ---- Stylesheet -----------------------------------------------------------

def stylesheet() -> str:
    return f"""
* {{ outline: none; }}

QWidget {{
    background: transparent;
    color: {C["text"]};
}}
QMainWindow, QDialog {{ background: {C["bg"]}; }}

/* ---- tabs ---- */
QTabWidget::pane {{
    border: 1px solid {C["border"]};
    border-radius: {RADIUS}px;
    background: {C["surface"]};
    top: -1px;
}}
QTabBar {{ qproperty-drawBase: 0; }}
QTabBar::tab {{
    background: transparent;
    color: {C["muted"]};
    border: 1px solid transparent;
    border-bottom: none;
    padding: 8px 18px;
    margin-right: 2px;
    border-top-left-radius: {RADIUS}px;
    border-top-right-radius: {RADIUS}px;
    font-weight: 600;
}}
QTabBar::tab:hover {{ color: {C["text"]}; background: {C["raised"]}; }}
QTabBar::tab:selected {{
    background: {C["surface"]};
    color: {C["text"]};
    border-color: {C["border"]};
}}

/* ---- grouping ---- */
QGroupBox {{
    background: {C["surface"]};
    border: 1px solid {C["border"]};
    border-radius: {RADIUS}px;
    margin-top: 14px;
    padding: 14px 12px 12px 12px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 6px;
    color: {C["muted"]};
}}

/* ---- buttons ---- */
QPushButton {{
    background: {C["raised"]};
    border: 1px solid {C["border"]};
    border-radius: {RADIUS_SMALL}px;
    padding: 7px 14px;
    color: {C["text"]};
    font-weight: 600;
}}
QPushButton:hover {{ background: {C["border"]}; border-color: {C["border_strong"]}; }}
/* A pixel of travel on press: the cheapest thing that makes a button feel
   pressed rather than merely recoloured. */
QPushButton:pressed {{
    background: {C["border_strong"]};
    padding-top: 8px;
    padding-bottom: 6px;
}}
QPushButton:disabled {{ color: {C["faint"]}; background: {C["surface"]}; }}
QPushButton[accent="true"] {{
    background: {C["accent"]};
    border-color: {C["accent"]};
    color: {C["accent_text"]};
}}
QPushButton[accent="true"]:hover {{
    background: {C["accent_hover"]}; border-color: {C["accent_hover"]};
}}
QPushButton[accent="true"]:pressed {{ background: {C["accent_pressed"]}; }}
QPushButton[accent="true"]:disabled {{
    background: {C["raised"]}; border-color: {C["border"]}; color: {C["faint"]};
}}

/* ---- inputs ---- */
QLineEdit, QSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    background: {C["bg"]};
    border: 1px solid {C["border"]};
    border-radius: {RADIUS_SMALL}px;
    padding: 6px 8px;
    selection-background-color: {C["accent"]};
    selection-color: {C["accent_text"]};
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{
    border-color: {C["accent"]};
}}
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{ color: {C["faint"]}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {C["surface"]};
    border: 1px solid {C["border"]};
    selection-background-color: {C["accent"]};
    selection-color: {C["accent_text"]};
}}

/* Spin arrows, checkmarks and radio dots are left to Fusion, which draws them
   from the palette. Restyling the boxes without supplying the glyphs is what
   makes a themed app lose its tick marks and its up/down arrows. */
QCheckBox, QRadioButton {{ spacing: 8px; }}

/* ---- lists and trees ---- */
QListView, QTreeWidget, QTreeView, QTableView {{
    background: {C["bg"]};
    border: 1px solid {C["border"]};
    border-radius: {RADIUS_SMALL}px;
    alternate-background-color: {C["surface"]};
}}
QListView::item, QTreeView::item {{ padding: 4px 6px; border-radius: 4px; }}
QListView::item:hover, QTreeView::item:hover {{ background: {C["raised"]}; }}
QListView::item:selected, QTreeView::item:selected {{
    background: {C["accent"]}; color: {C["accent_text"]};
}}
QHeaderView::section {{
    background: {C["raised"]};
    color: {C["muted"]};
    border: none;
    border-bottom: 1px solid {C["border"]};
    padding: 6px 8px;
    font-weight: 600;
}}

/* ---- progress ---- */
QProgressBar {{
    background: {C["bg"]};
    border: 1px solid {C["border"]};
    border-radius: {RADIUS_SMALL}px;
    text-align: center;
    color: {C["text"]};
    min-height: 16px;
}}
QProgressBar::chunk {{
    background: {C["accent"]};
    border-radius: {RADIUS_SMALL - 1}px;
}}

/* ---- scrollbars ---- */
QScrollBar:vertical, QScrollBar:horizontal {{
    background: transparent; border: none; margin: 0;
}}
QScrollBar:vertical {{ width: 11px; }}
QScrollBar:horizontal {{ height: 11px; }}
QScrollBar::handle {{ background: {C["border_strong"]}; border-radius: 5px; }}
QScrollBar::handle:hover {{ background: {C["muted"]}; }}
QScrollBar::handle:vertical {{ min-height: 30px; }}
QScrollBar::handle:horizontal {{ min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ---- misc chrome ---- */
QSplitter::handle {{ background: transparent; }}
QSplitter::handle:horizontal {{ width: 10px; }}
QSplitter::handle:vertical {{ height: 10px; }}
QScrollArea {{ border: none; }}
QToolTip {{
    background: {C["raised"]};
    color: {C["text"]};
    border: 1px solid {C["border_strong"]};
    border-radius: {RADIUS_SMALL}px;
    padding: 6px 8px;
}}
QMenu {{
    background: {C["surface"]};
    border: 1px solid {C["border"]};
    border-radius: {RADIUS_SMALL}px;
    padding: 6px;
}}
QMenu::item {{ padding: 6px 22px 6px 12px; border-radius: 4px; }}
QMenu::item:selected {{ background: {C["accent"]}; color: {C["accent_text"]}; }}
QMenu::item:disabled {{ color: {C["faint"]}; }}
QMenu::separator {{ height: 1px; background: {C["border"]}; margin: 5px 8px; }}
QMessageBox {{ background: {C["bg"]}; }}
"""


# ---- Reusable fragments for widgets that stay hand-styled -----------------

def console_style() -> str:
    """The log panels, which are deliberately a terminal rather than a page."""
    return (f"background: {C['console']}; color: {C['console_text']}; "
            f"border: 1px solid {C['border']}; border-radius: {RADIUS_SMALL}px; "
            f"padding: 8px; font-family: 'Cascadia Mono', Consolas, monospace;")


def card_style(object_name: str, color: str) -> str:
    """A preview card, tinted by whether its item is ready, skipped or built.

    The hover rule is what makes a wall of cards feel alive under the cursor;
    Qt applies it instantly, which is the right speed for pointer feedback.
    """
    return (f"#{object_name} {{ background: {C['surface']}; "
            f"border: 1px solid {C['border']}; border-left: 3px solid {color}; "
            f"border-radius: {RADIUS}px; }}"
            f"#{object_name}:hover {{ background: {C['raised']}; "
            f"border-color: {C['border_strong']}; border-left-color: {color}; }}")


def make_accent(button) -> None:
    """Mark the one button that starts the work, so it stands out from Cancel."""
    button.setProperty("accent", True)
    # A property set after the widget exists needs the style re-evaluated.
    button.style().unpolish(button)
    button.style().polish(button)


def label_style(kind: str = "muted", size: int | None = None,
                weight: int | None = None) -> str:
    """A secondary text line: muted / faint / ok / warn / danger / accent."""
    color = {
        "muted": C["muted"], "faint": C["faint"], "text": C["text"],
        "ok": C["ok"], "warn": C["warn"], "danger": C["danger"],
        "accent": C["accent"],
    }.get(kind, C["muted"])
    parts = [f"color: {color};", "background: transparent;"]
    if size:
        parts.append(f"font-size: {size}px;")
    if weight:
        parts.append(f"font-weight: {weight};")
    return " ".join(parts)


# ---- Applying it ----------------------------------------------------------

def icon_path() -> Path:
    """assets/icon.ico, both from source and from inside the bundled exe."""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        base = Path(__file__).resolve().parent.parent
    return base / "assets" / "icon.ico"


def app_icon() -> QIcon:
    path = icon_path()
    return QIcon(str(path)) if path.exists() else QIcon()


def apply(app: QApplication, name: str = "dark") -> None:
    """Paint the whole application. Call once, before building any window."""
    global C
    C = PALETTES.get(name, DARK)

    app.setStyle("Fusion")
    app.setPalette(_palette())

    # Segoe UI Variable is the Windows 11 face; Segoe UI everywhere else.
    font = QFont("Segoe UI Variable Text", 10)
    if not font.exactMatch():
        font = QFont("Segoe UI", 10)
    app.setFont(font)

    app.setStyleSheet(stylesheet())
    app.setWindowIcon(app_icon())
