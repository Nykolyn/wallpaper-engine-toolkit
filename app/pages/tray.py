"""Tray colours and progress, using the widget settings' controls."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QColorDialog, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from .. import theme, tray_options
from ..tray_icon import RUNNING, PAUSED, UNKNOWN, FINISHED, mark_pixmap
from ..ui.kit import CardTitle, Dropdown, GlassPanel, Overline, SecondaryButton, TextInput, label
from .base import Page


class TrayPage(Page):
    key = "tray"
    title = "Tray"
    icon = "tracker"
    FIXTURES = ("idle", "split")

    def __init__(self, settings, *, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.set_subtitle("Customize the two rectangles · saved as you change them")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        column = QVBoxLayout(body)
        pad_v, pad_h = theme.BODY_PAD
        column.setContentsMargins(pad_h, pad_v, pad_h, pad_v)
        column.setSpacing(theme.PANEL_GAP)
        scroll.setWidget(body)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        panel = GlassPanel(padding="lg")
        fields = QVBoxLayout(panel)
        fields.setContentsMargins(0, 0, 0, 0)
        fields.setSpacing(theme.DIALOG_ROW_GAP)
        fields.addWidget(CardTitle("Appearance", "Main rectangle in front · additional rectangle behind"))
        self.colors = {}
        for name, title in (("front_color", "Main rectangle color"), ("back_color", "Additional rectangle color")):
            field = TextInput(placeholder="#RRGGBB")
            field.setMaxLength(7)
            field.setAccessibleName(title)
            field.editingFinished.connect(lambda n=name: self._color(n))
            heading = Overline(title)
            heading.setBuddy(field)
            fields.addWidget(heading)
            row = QHBoxLayout()
            row.addWidget(field, 1)
            choose = SecondaryButton("Choose…")
            choose.setAccessibleName("Choose " + title.lower())
            choose.clicked.connect(lambda checked=False, n=name: self._choose(n))
            row.addWidget(choose)
            fields.addLayout(row)
            self.colors[name] = field
        self.mode = Dropdown()
        self.mode.setAccessibleName("Progress mode")
        for title, value in (("Both rectangles: current wallpaper (default)", "combined"),
                             ("Main: wallpaper · additional: playlist", "front_wallpaper"),
                             ("Main: playlist · additional: wallpaper", "back_wallpaper")):
            self.mode.add_item(title, value)
        heading = Overline("Progress")
        heading.setBuddy(self.mode)
        fields.addWidget(heading)
        fields.addWidget(self.mode)
        self.mode.currentIndexChanged.connect(self._mode)
        note = label("Wallpaper progress fills until the next change. Playlist progress counts wallpapers shown. "
                     "Pause, unknown timer and finished states keep their status colors and badges.", "type.caption", "lo")
        note.setWordWrap(True)
        fields.addWidget(note)
        reset = SecondaryButton("Reset to defaults")
        reset.clicked.connect(self._reset)
        fields.addWidget(reset, 0, Qt.AlignLeft)
        column.addWidget(panel)
        preview = GlassPanel(padding="lg")
        previews = QVBoxLayout(preview)
        previews.setContentsMargins(0, 0, 0, 0)
        previews.addWidget(CardTitle("Preview", "Example: wallpaper 35% · playlist 70%"))
        row = QHBoxLayout()
        self.previews = []
        for state, title in ((RUNNING, "Running"), (PAUSED, "Paused"), (UNKNOWN, "Timer unknown"), (FINISHED, "Finished")):
            stack = QVBoxLayout()
            image = label("")
            image.setAlignment(Qt.AlignCenter)
            image.setAccessibleName(title + " icon preview")
            stack.addWidget(image)
            caption = label(title, "type.caption", "mid")
            caption.setAlignment(Qt.AlignCenter)
            stack.addWidget(caption)
            row.addLayout(stack, 1)
            self.previews.append((image, state))
        previews.addLayout(row)
        column.addWidget(preview)
        column.addStretch(1)
        self._sync()

    def _sync(self):
        options = tray_options.read(self.settings)
        for name, field in self.colors.items():
            field.setText(options[name])
            field.set_error(None)
        self.mode.blockSignals(True)
        self.mode.setCurrentIndex(self.mode.findData(options["progress"]))
        self.mode.blockSignals(False)
        for image, state in self.previews:
            image.setPixmap(mark_pixmap(state, 48, fill=.35, playlist_fill=.70, options=options))

    def _color(self, name):
        try:
            tray_options.set_option(self.settings, name, self.colors[name].text())
        except ValueError:
            self.colors[name].set_error("Use #RRGGBB")
            return
        self._sync()

    def _choose(self, name):
        color = QColorDialog.getColor(QColor(tray_options.read(self.settings)[name]),
                                      self.window(), self.colors[name].accessibleName())
        if color.isValid():
            self.colors[name].setText(color.name())
            self._color(name)

    def _mode(self, _index):
        tray_options.set_option(self.settings, "progress", self.mode.currentData())
        self._sync()

    def _reset(self):
        self.settings.reload_if_changed()
        for name, value in tray_options.DEFAULTS.items():
            self.settings.set("tray", name, value)
        self.settings.save()
        self._sync()

    def on_shown(self):
        self.settings.reload_if_changed()
        self._sync()

    def frame_fixture(self, state):
        return {"frame": "idle"}

    def load_fixture(self, state):
        if state not in self.FIXTURES:
            return super().load_fixture(state)
        from ..settings import Settings
        self.settings = Settings({"tray": {"progress": "front_wallpaper", "back_color": "#22C3B0"}
                                  if state == "split" else {}})
        self._sync()
