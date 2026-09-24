"""The redesign's component kit: every screen is built from what is here.

It grows one step at a time; each module lands with its states in the kit
preview (`tools/kit_preview.py`).

- icons: the design's glyphs.
- base: the state model every control follows, and the surfaces that draw
  shadows and focus rings outside a widget's own rectangle.
- buttons, inputs, selection, chips, panels: the controls.
"""
from .base import Glyph, declare as declare_surface, label
from .buttons import AccentButton, DangerButton, GhostButton, IconButton, SecondaryButton
from .chips import Chip, chip_pixmap, chip_size
from .icons import NAMES as ICON_NAMES, icon, pixmap, svg
from .inputs import Dropdown, DropdownPopup, SpinBox, TextInput
from .panels import Callout, CardTitle, GlassPanel, MetricStrip, Overline
from .selection import Checkbox, Pagination, SegmentedControl, Toggle, page_numbers

__all__ = [
    "ICON_NAMES", "icon", "pixmap", "svg",
    "Glyph", "declare_surface", "label",
    "AccentButton", "SecondaryButton", "DangerButton", "GhostButton", "IconButton",
    "TextInput", "SpinBox", "Dropdown", "DropdownPopup",
    "Checkbox", "Toggle", "SegmentedControl", "Pagination", "page_numbers",
    "Chip", "chip_pixmap", "chip_size",
    "GlassPanel", "Overline", "CardTitle", "Callout", "MetricStrip",
]
