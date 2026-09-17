"""Creator tab — Qt front-end for the verbatim BuildEngine.

Mirrors the standalone Wallpaper Creator:
  * source / target / previews paths (remembered between runs)
  * Move vs Copy mode
  * a scrollable gallery of clip cards with preview thumbnails:
        green border  — preview found, clip will be built
        red border    — no preview, clip will be skipped
        blue border   — project created
  * background build with live log, progress bar and percentage
"""
from __future__ import annotations

import os
import threading

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QImage, QPixmap
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox, QLineEdit,
    QPushButton, QLabel, QPlainTextEdit, QComboBox, QFileDialog,
    QMessageBox, QFrame, QScrollArea,
)

from ..engines.creator import BuildEngine, scan_source
from ..settings import (
    Settings, DEFAULT_CREATOR_SOURCE, DEFAULT_CREATOR_TARGET,
    DEFAULT_CREATOR_PREVIEWS, DEFAULT_CREATOR_MODE,
)
from .. import animations, theme
from ..workers import CreatorBridge

SECTION = "creator"


THUMB_W = 168             # preview thumbnail width
try:
    from PIL import Image  # noqa: F401
    PIL_AVAILABLE = True
except ImportError:  # pragma: no cover
    PIL_AVAILABLE = False


class ClipCard(QFrame):
    """One clip: thumbnail + name + status, with a coloured border."""

    def __init__(self, item):
        super().__init__()
        self.item = item
        self._border = theme.status_color("ok" if item.valid else "bad")
        self.setObjectName("clipCard")
        self._apply_border(self._border)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        self.thumb = QLabel()
        self.thumb.setFixedSize(THUMB_W, THUMB_W * 9 // 16)
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setStyleSheet(theme.label_style("faint"))
        self.thumb.setText("…")
        layout.addWidget(self.thumb)

        text_col = QVBoxLayout()
        name = QLabel(item.basename)
        nf = QFont(); nf.setBold(True); nf.setPointSize(11)
        name.setFont(nf)
        name.setStyleSheet(theme.label_style("text", weight=600))
        text_col.addWidget(name)

        if item.valid:
            status = f"✓ preview: {os.path.basename(item.preview_path)}"
            color = theme.status_color("ok")
        else:
            status = "✗ no preview found — will be skipped"
            color = theme.status_color("bad")
        self.status = QLabel(status)
        self.status.setStyleSheet(f"color: {color}; background: transparent;")
        text_col.addWidget(self.status)
        text_col.addStretch()
        layout.addLayout(text_col, 1)

    def _apply_border(self, color: str):
        self.setStyleSheet(theme.card_style("clipCard", color))

    def set_thumbnail(self, image: QImage):
        self.thumb.setPixmap(QPixmap.fromImage(image))
        self.thumb.setText("")

    def mark_done(self):
        self._apply_border(theme.status_color("done"))
        self.status.setText("✓ project created")
        self.status.setStyleSheet(f"color: {theme.status_color('done')}; background: transparent;")


class CreatorTab(QWidget):
    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
        self.items = []
        self.cards = {}  # basename -> ClipCard

        self.bridge = CreatorBridge()
        self.bridge.log.connect(self._log)
        self.bridge.progress.connect(self._on_progress)
        self.bridge.item_done.connect(self._on_item_done)
        self.bridge.thumb.connect(self._on_thumb)
        self.bridge.finished.connect(self._on_finished)

        self.engine = BuildEngine(
            log=self.bridge.log.emit,
            progress=self.bridge.progress.emit,
            item_done=self.bridge.item_done.emit,
            finished=self.bridge.finished.emit,
        )

        self._build_ui()
        # Auto-scan shortly after construction (like the original).
        from PySide6.QtCore import QTimer
        QTimer.singleShot(200, self._scan)

    # ----- UI -----------------------------------------------------------------
    def _build_ui(self):
        layout = QVBoxLayout(self)

        # --- Paths + options ---
        paths_box = QGroupBox("Paths && options")
        grid = QGridLayout(paths_box)
        grid.setColumnStretch(1, 1)

        self.source_edit = QLineEdit(
            self.settings.get(SECTION, "source", DEFAULT_CREATOR_SOURCE))
        self.target_edit = QLineEdit(
            self.settings.get(SECTION, "target", DEFAULT_CREATOR_TARGET))
        self.previews_edit = QLineEdit(
            self.settings.get(SECTION, "previews", DEFAULT_CREATOR_PREVIEWS))

        self._path_row(grid, 0, "Source (videos):", self.source_edit)
        self._path_row(grid, 1, "Target (WE projects):", self.target_edit)
        self._path_row(grid, 2, "Previews folder:", self.previews_edit)

        opts = QHBoxLayout()
        opts.addWidget(QLabel("Video mode:"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Move", "Copy"])
        self.mode_combo.setCurrentText(
            self.settings.get(SECTION, "mode", DEFAULT_CREATOR_MODE))
        opts.addWidget(self.mode_combo)
        opts.addStretch()
        self.rescan_btn = QPushButton("⟳ Rescan")
        self.rescan_btn.clicked.connect(self._scan)
        opts.addWidget(self.rescan_btn)
        grid.addLayout(opts, 3, 0, 1, 3)
        layout.addWidget(paths_box)

        # --- Clip cards (scrollable) ---
        cards_box = QGroupBox("Clips")
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

        # --- Progress + actions ---
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

        # --- Log ---
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        self.log.setStyleSheet(theme.console_style())
        layout.addWidget(self.log, 1)

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
            item = self.cards_layout.takeAt(0)
            w = item.widget()
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
        previews = self.previews_edit.text().strip()

        self._clear_cards()

        if not os.path.isdir(source):
            self.summary.setText(f"⚠ Source folder not found: {source}")
            self.summary.setStyleSheet(theme.label_style("danger"))
            self.items = []
            self._update_start_state()
            return

        self.items = scan_source(source, previews)
        valid = sum(1 for it in self.items if it.valid)
        skipped = len(self.items) - valid

        for it in self.items:
            card = ClipCard(it)
            self.cards_layout.addWidget(card)
            self.cards[it.basename] = card

        if not self.items:
            self.summary.setText("No .mp4 files in the source folder.")
            self.summary.setStyleSheet(theme.label_style("muted"))
        else:
            self.summary.setText(
                f"Found {len(self.items)}:  ✓ {valid} to build   ✗ {skipped} skipped")
            self.summary.setStyleSheet(theme.label_style("text"))
        self._update_start_state()

        if PIL_AVAILABLE:
            threading.Thread(
                target=self._load_thumbnails, args=(list(self.items),),
                daemon=True).start()

    def _load_thumbnails(self, items):
        from PIL import Image
        for it in items:
            if not it.preview_path:
                continue
            try:
                img = Image.open(it.preview_path)
                img.seek(0)  # first frame of a gif
                img = img.convert("RGB")
                w, h = img.size
                th = max(1, int(THUMB_W * h / w))
                img = img.resize((THUMB_W, th), Image.LANCZOS)
                # Build a QImage straight from raw RGB bytes; .copy() detaches it
                # from the Python buffer so it survives back on the UI thread.
                data = img.tobytes("raw", "RGB")
                qimg = QImage(data, img.width, img.height,
                              img.width * 3, QImage.Format_RGB888).copy()
                self.bridge.thumb.emit(it.basename, qimg)
            except Exception:  # noqa: BLE001 — a thumbnail is not critical
                pass

    def _update_start_state(self):
        self.start_btn.setEnabled(any(it.valid for it in self.items))

    def _start(self):
        if self.engine.is_running():
            return
        target = self.target_edit.text().strip()
        if not target:
            QMessageBox.warning(self, "Target", "Specify a target folder.")
            return
        if not any(it.valid for it in self.items):
            QMessageBox.warning(self, "No clips", "No valid clips to build.")
            return

        self._save_settings()
        move = self.mode_combo.currentText() == "Move"

        self.log.clear()
        self.progress.setValue(0)
        self.percent.setText("0%")
        self.start_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.rescan_btn.setEnabled(False)
        self.engine.start(list(self.items), target, move=move)

    def _cancel(self):
        self.engine.cancel()
        self._log("[CANCEL] Cancellation requested — finishing current clip…")
        self.cancel_btn.setEnabled(False)

    def _save_settings(self):
        self.settings.set(SECTION, "source", self.source_edit.text().strip())
        self.settings.set(SECTION, "target", self.target_edit.text().strip())
        self.settings.set(SECTION, "previews", self.previews_edit.text().strip())
        self.settings.set(SECTION, "mode", self.mode_combo.currentText())
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

    def _on_thumb(self, name: str, image: QImage):
        if name in self.cards:
            self.cards[name].set_thumbnail(image)

    def _on_finished(self, report):
        self.cancel_btn.setEnabled(False)
        self.rescan_btn.setEnabled(True)
        ok = sum(1 for e in report if e["status"] == "ok")
        self._log(f"\n[FINISH] Done. Projects created: {ok}.")
        from PySide6.QtCore import QTimer
        QTimer.singleShot(300, self._scan)  # source may have changed (move mode)

    def _log(self, text: str):
        self.log.appendPlainText(text)
