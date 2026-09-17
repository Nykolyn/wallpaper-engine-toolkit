"""Reusable UI widgets."""
from __future__ import annotations

from PySide6.QtCore import Qt, QAbstractListModel, QModelIndex, QSortFilterProxyModel
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QLabel, QListView,
    QPushButton, QAbstractItemView,
)

from .. import theme


class FolderListModel(QAbstractListModel):
    """A virtualized model backing huge folder-name lists (25k+ rows)."""

    def __init__(self, names: list[str] | None = None):
        super().__init__()
        self._names: list[str] = names or []

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self._names)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        if role in (Qt.DisplayRole, Qt.ToolTipRole):
            return self._names[index.row()]
        return None

    def set_names(self, names: list[str]):
        self.beginResetModel()
        self._names = names
        self.endResetModel()

    def name_at(self, row: int) -> str:
        return self._names[row]


class FolderListPanel(QWidget):
    """A searchable folder list with a count label and an optional refresh button."""

    def __init__(self, title: str, selectable: bool = False, parent=None):
        super().__init__(parent)
        self.model = FolderListModel()
        self.proxy = QSortFilterProxyModel()
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        header = QHBoxLayout()
        self.count_label = QLabel(f"{title}: 0")
        self.count_label.setStyleSheet(theme.label_style("text", weight=600))
        header.addWidget(self.count_label)
        header.addStretch()
        self.refresh_btn = QPushButton("Refresh")
        header.addWidget(self.refresh_btn)
        layout.addLayout(header)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter by name...")
        self.search.textChanged.connect(self.proxy.setFilterFixedString)
        layout.addWidget(self.search)

        self.view = QListView()
        self.view.setModel(self.proxy)
        self.view.setUniformItemSizes(True)  # huge perf win for big lists
        self.view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        if selectable:
            self.view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        else:
            self.view.setSelectionMode(QAbstractItemView.NoSelection)
        layout.addWidget(self.view)

        self._title = title

    def set_title(self, title: str):
        """Rename the list; the count after the title is kept up to date."""
        self._title = title
        self.count_label.setText(f"{title}: {self.model.rowCount()}")

    def set_names(self, names: list[str]):
        self.model.set_names(names)
        self.count_label.setText(f"{self._title}: {len(names)}")

    def selected_names(self) -> list[str]:
        rows = self.view.selectionModel().selectedRows() if self.view.selectionModel() else []
        out = []
        for idx in rows:
            src = self.proxy.mapToSource(idx)
            out.append(self.model.name_at(src.row()))
        return out

    def select_all(self):
        self.view.selectAll()
