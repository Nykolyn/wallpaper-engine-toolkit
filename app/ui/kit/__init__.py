"""The redesign's component kit: every screen is built from what is here.

It grows one step at a time; each module lands with its states in the kit
preview (`tools/kit_preview.py`).
"""
from .icons import NAMES as ICON_NAMES, icon, pixmap, svg

__all__ = ["ICON_NAMES", "icon", "pixmap", "svg"]
