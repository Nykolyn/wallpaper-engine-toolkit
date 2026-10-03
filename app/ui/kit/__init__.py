"""The redesign's component kit: every screen is built from what is here.

It grows one step at a time; each module lands with its states in the kit
preview (`tools/kit_preview.py`).

- icons: the design's glyphs.
- base: the state model every control follows, and the surfaces that draw
  shadows and focus rings outside a widget's own rectangle.
- buttons, inputs, selection, chips, panels: the controls.
- format: how every number, size, duration and date is written.
- paths, tags, progress, cards, tables, thumbs: data display.
- log, toast, statusline, dialogs: feedback — what reports work, and what
  asks before acting.
- shell: the frame — the title bar, the sidebar and its items, the page header.
"""
from . import format
from .base import Elided, Glyph, LiveDot, declare as declare_surface, label
from .buttons import (
    AccentButton, DangerButton, GhostButton, IconButton, LinkButton, SecondaryButton,
    button_pixmap, button_size, icon_button_pixmap,
)
from .cards import MonitorCard, MonitorView, StatCard, ToolTile
from .chips import Chip, chip_pixmap, chip_size
from .dialogs import (
    CheckGroup, CheckRow, ConfirmDialog, ConfirmResult, FormDialog, OverlayDialog,
)
from .icons import NAMES as ICON_NAMES, icon, pixmap, svg
from .inputs import Dropdown, DropdownPopup, SpinBox, TextInput
from .log import ConsoleExcerpt, LogLine, LogModel, LogPanel, LogView, ProblemsFilter
from .panels import (
    ActivityLine, Callout, CardTitle, EmptyState, GlassPanel, IconDisc, MetricStrip, Overline,
    Rule, Spinner, StepList,
)
from .paths import PathField
from .progress import ProgressBar, ProgressRing
from .selection import Checkbox, Pagination, SegmentedControl, Toggle, page_numbers
from .shell import (
    BrandMark, CaptionButton, NavItem, NavSection, NavState, NextInLoop, PageHeader, Sidebar,
    TitleBar,
)
from .statusline import StatusLine
from .tables import (
    BusyCell, ButtonCell, ButtonsCell, Cell, CellButton, ChipCell, Column, DiscCell, Group,
    ListRow, ListRowDelegate, RowDelegate, RowList, SkeletonRows, Table, TableBar, TableFooter,
    TableHeader, TableModel, TableSummary, Thumb, buttons_width, disc_pixmap, paint_list_row,
    paint_spinner, paint_thumb,
    CheckCell, TagsCell, ProgressCell, SpinCell,
)
from .tags import TagPopup, TagSelect
from .thumbs import ThumbLoader
from .toast import Toast, ToastHost

__all__ = [
    "ICON_NAMES", "icon", "pixmap", "svg",
    "Glyph", "Elided", "LiveDot", "declare_surface", "label", "format",
    "AccentButton", "SecondaryButton", "DangerButton", "GhostButton", "IconButton", "LinkButton",
    "button_pixmap", "button_size", "icon_button_pixmap",
    "TextInput", "SpinBox", "Dropdown", "DropdownPopup",
    "Checkbox", "Toggle", "SegmentedControl", "Pagination", "page_numbers",
    "Chip", "chip_pixmap", "chip_size",
    "GlassPanel", "Overline", "Rule", "CardTitle", "Callout", "MetricStrip", "IconDisc",
    "EmptyState", "StepList", "ActivityLine", "Spinner",
    "PathField", "TagSelect", "TagPopup",
    "ProgressBar", "ProgressRing",
    "StatCard", "MonitorCard", "MonitorView", "ToolTile",
    "Table", "TableModel", "TableHeader", "RowDelegate", "Column", "Cell", "ChipCell", "Group",
    "ButtonCell", "ButtonsCell", "CellButton", "buttons_width", "BusyCell", "DiscCell",
    "disc_pixmap", "paint_spinner",
    "TagsCell", "CheckCell", "ProgressCell", "SpinCell",
    "TableBar", "TableSummary", "TableFooter", "ListRow", "ListRowDelegate", "RowList",
    "SkeletonRows",
    "paint_list_row", "Thumb", "paint_thumb", "ThumbLoader",
    "LogPanel", "LogModel", "LogView", "LogLine", "ProblemsFilter", "ConsoleExcerpt",
    "Toast", "ToastHost", "StatusLine",
    "TitleBar", "CaptionButton", "BrandMark", "Sidebar", "NavSection", "NavItem", "NavState",
    "NextInLoop", "PageHeader",
    "OverlayDialog", "ConfirmDialog", "FormDialog", "CheckGroup", "CheckRow", "ConfirmResult",
]
