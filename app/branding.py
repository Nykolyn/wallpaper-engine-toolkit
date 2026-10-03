"""The product's names, in one place (gate G10).

"Toolkit" is the short name: the window's title, the tray's tool tip and menu,
and the notifications. "Toolkit for Wallpaper Engine" is the full one, for the
About box. The executable, the logon task and the data folder keep the names
they were installed under (`WallpaperEngineToolkit…`): renaming those would
orphan an installed copy.

Light on purpose: the tray tracker imports this, and must not import a window
to learn what it is called.
"""
from __future__ import annotations

DISPLAY_NAME = "Toolkit"
FULL_NAME = "Toolkit for Wallpaper Engine"
