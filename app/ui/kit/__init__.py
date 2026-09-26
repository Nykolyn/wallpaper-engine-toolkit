"""The redesign's component kit: every screen is built from what is here.

It grows one step at a time; each module lands with its states in the kit
preview (`tools/kit_preview.py`).

- icons: the design's glyphs.
- base: the state model every control follows, and the surfaces that draw
  shadows and focus rings outside a widget's own rectangle.
- buttons, inputs, selection, chips, panels: the controls.
- format: how every number, size, duration and date is written.
- paths, tags, progress, cards, tables, thumbs: data display.
"""
from . import format
from .base import Glyph, declare as declare_surface, label
from .buttons import AccentButton, DangerButton, GhostButton, IconButton, SecondaryButton
from .cards import MonitorCard, MonitorView, StatCard
from .chips import Chip, chip_pixmap, chip_size
from .icons import NAMES as ICON_NAMES, icon, pixmap, svg
from .inputs import Dropdown, DropdownPopup, SpinBox, TextInput
from .panels import (
    ActivityLine, Callout, CardTitle, EmptyState, GlassPanel, MetricStrip, Overline, StepList,
)
from .paths import PathField
from .progress import ProgressBar, ProgressRing
from .selection import Checkbox, Pagination, SegmentedControl, Toggle, page_numbers
from .tables import (
    Cell, ChipCell, Column, Group, ListRow, ListRowDelegate, RowDelegate, RowList, Table,
    TableBar, TableFooter, TableHeader, TableModel, TableSummary, Thumb, paint_list_row,
    paint_thumb,
)
from .tags import TagPopup, TagSelect
from .thumbs import ThumbLoader

__all__ = [
    "ICON_NAMES", "icon", "pixmap", "svg",
    "Glyph", "declare_surface", "label", "format",
    "AccentButton", "SecondaryButton", "DangerButton", "GhostButton", "IconButton",
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
]
