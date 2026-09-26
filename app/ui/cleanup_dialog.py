"""The confirmation put in front of deleting anything from the reserve.

Nothing here decides on its own. The scan is read-only; this dialog shows what
it found — folder by folder, with the actual contents one click away and
Explorer one more — and only what is still ticked when the button is pressed is
deleted. Folders that still hold media start unticked, because those are a
wallpaper that lost its manifest rather than leftover rubbish.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTreeWidget,
    QTreeWidgetItem, QAbstractItemView, QDialogButtonBox,
)

from .. import external, theme
from ..engines.rotator.core import BrokenFolder, human_size

PATH_ROLE = Qt.UserRole + 1
LOADED_ROLE = Qt.UserRole + 2


class CleanupDialog(QDialog):
    """Pick which unusable reserve folders to delete. `names()` holds the answer."""

    def __init__(self, broken: list[BrokenFolder], scanned: int = 0, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Unusable folders")
        self.setWindowIcon(theme.app_icon())
        self.resize(980, 640)
        self.broken = {b.path: b for b in broken}

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        headline = QLabel(
            f"{len(broken)} of {scanned or len(broken)} folders have no project.json")
        headline.setStyleSheet(theme.label_style("text", size=15, weight=600))
        layout.addWidget(headline)

        explain = QLabel(
            "Wallpaper Engine identifies a wallpaper by its project.json, so it can "
            "never list or show these. Rotating one into myprojects only loses a slot "
            "in the playlist. Expand a folder to see exactly what is inside, or open "
            "it in Explorer. Deleting is permanent — only ticked folders are removed.")
        explain.setWordWrap(True)
        explain.setStyleSheet(theme.label_style("muted", size=12))
        layout.addWidget(explain)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Folder", "Where", "Why it cannot be used",
                                   "Contents"])
        self.tree.setColumnWidth(0, 300)
        self.tree.setColumnWidth(1, 150)
        self.tree.setColumnWidth(2, 280)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setUniformRowHeights(True)
        self.tree.itemExpanded.connect(self._fill_children)
        self.tree.itemDoubleClicked.connect(self._reveal_item)
        layout.addWidget(self.tree, 1)

        for b in sorted(broken, key=lambda x: (not x.safe_to_delete, x.name.lower())):
            self._add_row(b)

        self.counts = QLabel()
        self.counts.setStyleSheet(theme.label_style("muted", size=12))
        layout.addWidget(self.counts)
        # Connected only now: filling the rows above ticks boxes, and the
        # running total cannot be updated before the label it writes into
        # exists.
        self.tree.itemChanged.connect(lambda *_: self._update_counts())

        picks = QHBoxLayout()
        for label, handler in (("Select all", lambda: self._set_all(True)),
                               ("Select none", lambda: self._set_all(False)),
                               ("Only the safe ones", self._select_safe)):
            btn = QPushButton(label)
            btn.clicked.connect(handler)
            picks.addWidget(btn)
        self.reveal_btn = QPushButton("Open in Explorer")
        self.reveal_btn.clicked.connect(self._reveal_selected)
        picks.addWidget(self.reveal_btn)
        picks.addStretch()
        layout.addLayout(picks)

        buttons = QDialogButtonBox()
        self.skip_btn = buttons.addButton("Skip cleanup", QDialogButtonBox.RejectRole)
        self.delete_btn = buttons.addButton("Delete ticked folders",
                                            QDialogButtonBox.AcceptRole)
        theme.make_accent(self.delete_btn)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._select_safe()

    # ------------------------------------------------------------------ rows
    def _add_row(self, b: BrokenFolder) -> None:
        item = QTreeWidgetItem([b.name, Path(b.root).name, b.reason, b.summary])
        item.setData(0, PATH_ROLE, b.path)
        item.setData(0, LOADED_ROLE, False)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(0, Qt.Unchecked)
        if not b.safe_to_delete:
            item.setToolTip(0, "Holds media — this may be a wallpaper whose "
                               "project.json went missing, not rubbish.")
        if b.entries:
            # A placeholder keeps the arrow without building thousands of rows
            # up front; the real entries arrive when the folder is expanded.
            item.addChild(QTreeWidgetItem(["…"]))
        self.tree.addTopLevelItem(item)
        if not b.safe_to_delete:
            self._tint(item)

    def _tint(self, item: QTreeWidgetItem) -> None:
        colour = theme.color("warn")
        for column in range(self.tree.columnCount()):
            item.setForeground(column, colour)

    def _fill_children(self, item: QTreeWidgetItem) -> None:
        if item.data(0, LOADED_ROLE) or item.parent() is not None:
            return
        item.setData(0, LOADED_ROLE, True)
        b = self.broken.get(item.data(0, PATH_ROLE))
        item.takeChildren()
        if b is None:
            return
        for entry in b.entries:
            item.addChild(QTreeWidgetItem([entry]))
        if b.files > len(b.entries):
            item.addChild(QTreeWidgetItem(
                [f"… and {b.files - len(b.entries)} more file(s)"]))

    # --------------------------------------------------------------- ticking
    def _rows(self):
        for i in range(self.tree.topLevelItemCount()):
            yield self.tree.topLevelItem(i)

    def _set_all(self, ticked: bool) -> None:
        state = Qt.Checked if ticked else Qt.Unchecked
        for item in self._rows():
            item.setCheckState(0, state)

    def _select_safe(self) -> None:
        """Everything except the folders that still hold media."""
        for item in self._rows():
            b = self.broken.get(item.data(0, PATH_ROLE))
            item.setCheckState(0, Qt.Checked if b and b.safe_to_delete else Qt.Unchecked)

    def _update_counts(self) -> None:
        picked = self.paths()
        size = sum(self.broken[p].size for p in picked if p in self.broken)
        held = sum(1 for p in picked if not self.broken[p].safe_to_delete)
        text = f"{len(picked)} folder(s) ticked · {human_size(size)} will be freed"
        if held:
            text += f" · {held} of them still hold media"
        self.counts.setText(text)
        self.counts.setStyleSheet(
            theme.label_style("warn" if held else "muted", size=12))
        self.delete_btn.setEnabled(bool(picked))
        self.delete_btn.setText(
            f"Delete {len(picked)} folder(s)" if picked else "Delete ticked folders")

    def paths(self) -> list[str]:
        """The folders the user confirmed for deletion."""
        return [item.data(0, PATH_ROLE) for item in self._rows()
                if item.checkState(0) == Qt.Checked]

    # -------------------------------------------------------------- Explorer
    def _reveal_item(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        top = item if item.parent() is None else item.parent()
        path = top.data(0, PATH_ROLE)
        if path:
            self._open(Path(path))

    def _reveal_selected(self) -> None:
        chosen = self.tree.selectedItems()
        if chosen:
            self._reveal_item(chosen[0])

    @staticmethod
    def _open(path: Path) -> None:
        try:
            external.popen(["explorer", str(path)])
        except OSError:
            pass
