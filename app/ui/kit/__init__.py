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
)
from .cards import MonitorCard, MonitorView, StatCard
from .chips import Chip, chip_pixmap, chip_size
from .dialogs import (
    CheckGroup, CheckRow, ConfirmDialog, ConfirmResult, FormDialog, OverlayDialog,
)
from .icons import NAMES as ICON_NAMES, icon, pixmap, svg
from .inputs import Dropdown, DropdownPopup, SpinBox, TextInput
from .log import LogLine, LogModel, LogPanel, LogView, ProblemsFilter
from .panels import (
    ActivityLine, Callout, CardTitle, EmptyState, GlassPanel, MetricStrip, Overline, StepList,
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
    Cell, ChipCell, Column, Group, ListRow, ListRowDelegate, RowDelegate, RowList, Table,
    TableBar, TableFooter, TableHeader, TableModel, TableSummary, Thumb, paint_list_row,
    paint_thumb,
)
from .tags import TagPopup, TagSelect
from .thumbs import ThumbLoader
from .toast import Toast, ToastHost

__all__ = [
    "ICON_NAMES", "icon", "pixmap", "svg",
    "Glyph", "Elided", "LiveDot", "declare_surface", "label", "format",
    "AccentButton", "SecondaryButton", "DangerButton", "GhostButton", "IconButton", "LinkButton",
    "TextInput", "SpinBox", "Dropdown", "DropdownPopup",
    "Checkbox", "Toggle", "SegmentedControl", "Pagination", "page_numbers",
    "Chip", "chip_pixmap", "chip_size",
    "GlassPanel", "Overline", "CardTitle", "Callout", "MetricStrip",
    "EmptyState", "StepList", "ActivityLine",
    "PathField", "TagSelect", "TagPopup",
    "ProgressBar", "ProgressRing",
    "StatCard", "MonitorCard", "MonitorView",
    "Table", "TableModel", "TableHeader", "RowDelegate", "Column", "Cell", "ChipCell", "Group",
    "TableBar", "TableSummary", "TableFooter", "ListRow", "ListRowDelegate", "RowList",
    "paint_list_row", "Thumb", "paint_thumb", "ThumbLoader",
    "LogPanel", "LogModel", "LogView", "LogLine", "ProblemsFilter",
    "Toast", "ToastHost", "StatusLine",
    "TitleBar", "CaptionButton", "BrandMark", "Sidebar", "NavSection", "NavItem", "NavState",
    "NextInLoop", "PageHeader",
    "OverlayDialog", "ConfirmDialog", "FormDialog", "CheckGroup", "CheckRow", "ConfirmResult",
]
