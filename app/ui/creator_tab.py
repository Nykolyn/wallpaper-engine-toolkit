"""Creator tab — build Wallpaper Engine projects from videos alone.

Point it at a folder of clips and it writes a working wallpaper per clip: the
preview is rendered from the video itself with ffmpeg, so nothing has to be
prepared first. Cards show the video name and size, turning blue once the
project has been created.
"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox, QLineEdit,
    QPushButton, QLabel, QPlainTextEdit, QComboBox, QFileDialog,
    QMessageBox, QFrame, QScrollArea, QDialog, QDialogButtonBox, QCheckBox,
)

from ..engines.creator import (
    BuildEngine, scan_source, find_ffmpeg, human_size, clean_tags, WE_TAGS,
    GIF_SIZE, GIF_FPS, GIF_DURATION, GIF_SKIP,
)
from ..settings import (
    Settings, DEFAULT_CREATOR_SOURCE, DEFAULT_CREATOR_TARGET, DEFAULT_CREATOR_MODE,
)
from .. import animations, theme
from ..workers import CreatorBridge

SECTION = "creator"




def describe_tags(tags, empty="none"):
    """A tag list as a label: "Anime, Game", or a word when there are none."""
    return ", ".join(tags) if tags else empty


class TagDialog(QDialog):
    """Pick tags for a batch, or for one clip.

    Wallpaper Engine writes its genre tags into project.json and accepts any
    string there, so the known ones are offered as boxes and anything else can
    be typed. The two uses differ in one thing only: a clip may also say
    "whatever the batch says", which is not the same as saying "no tags" — and
    the checkbox at the top is that distinction made visible.
    """

    def __init__(self, tags, parent=None, *, batch=None):
        super().__init__(parent)
        self.setWindowTitle("Tags")
        self.setMinimumWidth(460)
        self._per_item = batch is not None

        layout = QVBoxLayout(self)

        note = QLabel(
            "Written to each project.json. Wallpaper Engine shows them in its "
            "own browser; they are free text, so anything may be added below.")
        note.setWordWrap(True)
        note.setStyleSheet(theme.label_style("faint"))
        layout.addWidget(note)

        self.follow = None
        if self._per_item:
            self.follow = QCheckBox(
                f"Use the tags set for the whole batch  ({describe_tags(batch)})")
            self.follow.setChecked(tags is None)
            self.follow.toggled.connect(self._follow_toggled)
            layout.addWidget(self.follow)

        self.boxes = {}
        grid_host = QWidget()
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(0, 4, 0, 4)
        chosen = list(tags or ())
        for n, name in enumerate(WE_TAGS):
            box = QCheckBox(name)
            box.setChecked(name in chosen)
            self.boxes[name] = box
            grid.addWidget(box, n // 4, n % 4)
        layout.addWidget(grid_host)
        self._grid_host = grid_host

        self.extra = QLineEdit(", ".join(t for t in chosen if t not in self.boxes))
        self.extra.setPlaceholderText("Anything else, comma separated")
        layout.addWidget(self.extra)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        if self._per_item:
            self._follow_toggled(self.follow.isChecked())

    def _follow_toggled(self, following: bool) -> None:
        self._grid_host.setEnabled(not following)
        self.extra.setEnabled(not following)

    def value(self):
        """The chosen tags, or None for "follow the batch"."""
        if self.follow is not None and self.follow.isChecked():
            return None
        picked = [name for name in WE_TAGS if self.boxes[name].isChecked()]
        picked += [t for t in self.extra.text().split(",")]
        return clean_tags(picked)


class VideoCard(QFrame):
    """One video: name + size + its tags, with a coloured border."""

    def __init__(self, item, batch_tags=(), on_tags_changed=None):
        super().__init__()
        self.item = item
        self._batch_tags = list(batch_tags)
        self._on_tags_changed = on_tags_changed
        self.setObjectName("videoCard")
        self._apply_border(theme.status_color("ok" if item.valid else "bad"))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)

        header = QHBoxLayout()
        name = QLabel(item.basename)
        nf = QFont(); nf.setBold(True); nf.setPointSize(11)
        name.setFont(nf)
        name.setStyleSheet(theme.label_style("text", weight=600))
        header.addWidget(name)
        header.addStretch()
        self.tags_btn = QPushButton()
        self.tags_btn.setFlat(True)
        self.tags_btn.setCursor(Qt.PointingHandCursor)
        self.tags_btn.clicked.connect(self._edit_tags)
        header.addWidget(self.tags_btn)
        layout.addLayout(header)
        self._refresh_tags_button()

        if item.valid:
            status = f"✓ {human_size(item.size)} — preview will be generated"
            color = theme.status_color("ok")
        else:
            status = "✗ file unreadable — will be skipped"
            color = theme.status_color("bad")
        self.status = QLabel(status)
        self.status.setStyleSheet(f"color: {color}; background: transparent;")
        layout.addWidget(self.status)

    # ----- tags ---------------------------------------------------------------

    def set_batch_tags(self, tags) -> None:
        """The batch changed; a card that follows it has to say so."""
        self._batch_tags = list(tags)
        self._refresh_tags_button()

    def _refresh_tags_button(self) -> None:
        own = self.item.tags
        if own is None:
            text = f"tags: {describe_tags(self._batch_tags, 'from batch — none')}"
            tip = "Follows the tags set for the whole batch. Click to set this clip's own."
            kind = "faint"
        else:
            text = f"tags: {describe_tags(own, 'none')}"
            tip = "This clip's own tags. Click to change them."
            kind = "text"
        self.tags_btn.setText(text)
        self.tags_btn.setToolTip(tip)
        self.tags_btn.setStyleSheet(theme.label_style(kind) + "text-align: right;")

    def _edit_tags(self) -> None:
        dialog = TagDialog(self.item.tags, self, batch=self._batch_tags)
        if dialog.exec() == QDialog.Accepted:
            self.item.tags = dialog.value()
            self._refresh_tags_button()
            if self._on_tags_changed:
                self._on_tags_changed()

    def _apply_border(self, color: str):
        self.setStyleSheet(theme.card_style("videoCard", color))

    def mark_done(self):
        self._apply_border(theme.status_color("done"))
        self.status.setText("✓ project created")
        self.status.setStyleSheet(f"color: {theme.status_color('done')}; background: transparent;")


class CreatorTab(QWidget):
    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
        self.items = []
        self.cards = {}
        # Tags for everything built in this batch. A clip may override them;
        # see TagDialog.
        self.batch_tags = clean_tags(self.settings.get(SECTION, "tags", []))

        self.bridge = CreatorBridge()
        self.bridge.log.connect(self._log)
        self.bridge.progress.connect(self._on_progress)
        self.bridge.item_done.connect(self._on_item_done)
        self.bridge.finished.connect(self._on_finished)

        self.engine = BuildEngine(
            log=self.bridge.log.emit,
            progress=self.bridge.progress.emit,
            item_done=self.bridge.item_done.emit,
            finished=self.bridge.finished.emit,
        )

        self._build_ui()
        QTimer.singleShot(200, self._scan)

    # ----- UI -----------------------------------------------------------------
    def _build_ui(self):
        layout = QVBoxLayout(self)

        paths_box = QGroupBox("Paths && options")
        grid = QGridLayout(paths_box)
        grid.setColumnStretch(1, 1)

        self.source_edit = QLineEdit(
            self.settings.get(SECTION, "source", DEFAULT_CREATOR_SOURCE))
        self.target_edit = QLineEdit(
            self.settings.get(SECTION, "target", DEFAULT_CREATOR_TARGET))
        self._path_row(grid, 0, "Source (videos):", self.source_edit)
        self._path_row(grid, 1, "Target (WE projects):", self.target_edit)

        opts = QHBoxLayout()
        opts.addWidget(QLabel("Video mode:"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Move", "Copy"])
        self.mode_combo.setCurrentText(
            self.settings.get(SECTION, "mode", DEFAULT_CREATOR_MODE))
        opts.addWidget(self.mode_combo)
        opts.addSpacing(16)
        opts.addWidget(QLabel("Tags:"))
        self.tags_btn = QPushButton()
        self.tags_btn.setToolTip(
            "Written into every project.json this batch builds.\n"
            "A single clip can be given its own tags on its card.")
        self.tags_btn.clicked.connect(self._edit_batch_tags)
        opts.addWidget(self.tags_btn)
        self._refresh_batch_button()
        opts.addSpacing(16)
        opts.addWidget(QLabel(
            f"Preview: {GIF_SIZE}×{GIF_SIZE} (1:1) @ {GIF_FPS}fps · "
            f"{GIF_DURATION:g}s from {GIF_SKIP:g}s"))
        opts.addStretch()
        self.rescan_btn = QPushButton("⟳ Rescan")
        self.rescan_btn.clicked.connect(self._scan)
        opts.addWidget(self.rescan_btn)
        grid.addLayout(opts, 2, 0, 1, 3)
        layout.addWidget(paths_box)

        cards_box = QGroupBox("Videos")
        cards_box_layout = QVBoxLayout(cards_box)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.cards_container = QWidget()
        self.cards_layout = QVBoxLayout(self.cards_container)
        self.cards_layout.setAlignment(Qt.AlignTop)
        self.scroll.setWidget(self.cards_container)
        cards_box_layout.addWidget(self.scroll)
        layout.addWidget(cards_box, 1)

        self.summary = QLabel("")
        layout.addWidget(self.summary)

        prog_row = QHBoxLayout()
        self.progress = animations.SmoothProgressBar()
        self.percent = QLabel("0%")
        self.percent.setMinimumWidth(44)
        self.start_btn = QPushButton("▶  Build")
        self.start_btn.setMinimumHeight(36)
        theme.make_accent(self.start_btn)
        self.cancel_btn = QPushButton("✖  Cancel")
        self.cancel_btn.setMinimumHeight(36)
        self.cancel_btn.setEnabled(False)
        self.start_btn.clicked.connect(self._start)
        self.cancel_btn.clicked.connect(self._cancel)
        prog_row.addWidget(self.progress, 1)
        prog_row.addWidget(self.percent)
        prog_row.addWidget(self.start_btn)
        prog_row.addWidget(self.cancel_btn)
        layout.addLayout(prog_row)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        self.log.setStyleSheet(theme.console_style())
        layout.addWidget(self.log, 1)

        if not find_ffmpeg():
            self._log("[WARN]  ffmpeg not found — previews cannot be generated. "
                      "Install ffmpeg or run: pip install imageio-ffmpeg")

    # ----- tags ---------------------------------------------------------------

    def _refresh_batch_button(self) -> None:
        self.tags_btn.setText(describe_tags(self.batch_tags, "none") + "  …")

    def _edit_batch_tags(self) -> None:
        dialog = TagDialog(self.batch_tags, self)
        if dialog.exec() != QDialog.Accepted:
            return
        self.batch_tags = dialog.value() or []
        self._refresh_batch_button()
        # Cards following the batch have to show the new answer immediately.
        for card in self.cards.values():
            card.set_batch_tags(self.batch_tags)
        self._save_settings()

    def _path_row(self, grid: QGridLayout, row: int, label: str, edit: QLineEdit):
        grid.addWidget(QLabel(label), row, 0)
        grid.addWidget(edit, row, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(lambda: self._browse(edit))
        grid.addWidget(browse, row, 2)

    # ----- actions ------------------------------------------------------------
    def _browse(self, edit: QLineEdit):
        d = QFileDialog.getExistingDirectory(self, "Choose folder", edit.text())
        if d:
            edit.setText(os.path.normpath(d))

    def _clear_cards(self):
        while self.cards_layout.count():
            w = self.cards_layout.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.cards.clear()

    def _scan(self):
        if self.engine.is_running():
            return
        with animations.busy():
            self._scan_now()

    def _scan_now(self):
        source = self.source_edit.text().strip()
        self._clear_cards()

        if not os.path.isdir(source):
            self.summary.setText(f"⚠ Source folder not found: {source}")
            self.summary.setStyleSheet(theme.label_style("danger"))
            self.items = []
            self._update_start_state()
            return

        self.items = scan_source(source)
        valid = sum(1 for it in self.items if it.valid)

        for it in self.items:
            card = VideoCard(it, self.batch_tags)
            self.cards_layout.addWidget(card)
            self.cards[it.basename] = card

        if not self.items:
            self.summary.setText("No video files in the source folder.")
            self.summary.setStyleSheet(theme.label_style("muted"))
        else:
            total = sum(it.size for it in self.items)
            self.summary.setText(
                f"Found {len(self.items)} video(s):  ✓ {valid} to build "
                f"·  {human_size(total)} total")
            self.summary.setStyleSheet(theme.label_style("text"))
        self._update_start_state()

    def _update_start_state(self):
        self.start_btn.setEnabled(any(it.valid for it in self.items))

    def _start(self):
        if self.engine.is_running():
            return
        target = self.target_edit.text().strip()
        if not target:
            QMessageBox.warning(self, "Target", "Specify a target folder.")
            return
        buildable = [it for it in self.items if it.valid]
        if not buildable:
            QMessageBox.warning(self, "No videos", "No videos to build.")
            return
        if not find_ffmpeg():
            QMessageBox.critical(
                self, "ffmpeg missing",
                "ffmpeg was not found, so previews cannot be generated.\n\n"
                "Install ffmpeg and add it to PATH, or run:\n"
                "    pip install imageio-ffmpeg")
            return

        self._save_settings()
        move = self.mode_combo.currentText() == "Move"

        self.log.clear()
        self.progress.setValue(0)
        self.percent.setText("0%")
        self.start_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.rescan_btn.setEnabled(False)
        self.engine.start(buildable, target, move=move, tags=self.batch_tags)

    def _cancel(self):
        self.engine.cancel()
        self._log("[CANCEL] Cancellation requested — finishing current video…")
        self.cancel_btn.setEnabled(False)

    def _save_settings(self):
        self.settings.set(SECTION, "source", self.source_edit.text().strip())
        self.settings.set(SECTION, "target", self.target_edit.text().strip())
        self.settings.set(SECTION, "mode", self.mode_combo.currentText())
        self.settings.set(SECTION, "tags", self.batch_tags)
        self.settings.save()

    # ----- engine signal handlers --------------------------------------------
    def _on_progress(self, done: int, total: int):
        frac = done / total if total else 0
        self.progress.setMaximum(100)
        self.progress.setValue(int(frac * 100))
        self.percent.setText(f"{int(frac * 100)}%")

    def _on_item_done(self, name: str, status: str):
        if status == "ok" and name in self.cards:
            self.cards[name].mark_done()

    def _on_finished(self, report):
        self.cancel_btn.setEnabled(False)
        self.rescan_btn.setEnabled(True)
        ok = sum(1 for e in report if e["status"] == "ok")
        self._log(f"\n[FINISH] Done. Projects created: {ok}.")
        QTimer.singleShot(300, self._scan)  # source may have changed (move mode)

    def _log(self, text: str):
        self.log.appendPlainText(text)
