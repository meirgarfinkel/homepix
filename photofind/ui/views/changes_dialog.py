"""A preview of changes to files: every one (now, what it will be, why) before anything is done.
The organizer and the duplicates review both show one."""

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QHeaderView, QLabel, QTableView, QVBoxLayout

_ROOT = QModelIndex()


class _Changes(QAbstractTableModel):
    HEADERS = ("Now", "Will be", "Why")

    def __init__(self, rows, parent=None):
        super().__init__(parent)
        self.rows = rows  # [(now, will be, why, tooltip)]

    def rowCount(self, parent=_ROOT):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=_ROOT):
        return 0 if parent.isValid() else 3

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole:
            return self.rows[index.row()][index.column()]
        if role == Qt.ItemDataRole.ToolTipRole:
            return self.rows[index.row()][3]
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.HEADERS[section]
        return None


class ChangesDialog(QDialog):
    """head: what will happen (rich text); note: more about it, in grey; go: the button that does it."""

    def __init__(self, title: str, head: str, note: str, rows, go: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(900, 560)
        lay = QVBoxLayout(self)
        for text, grey in ((head, False), (note, True)):
            label = QLabel(text)
            label.setWordWrap(True)
            if grey:
                label.setStyleSheet("color: palette(placeholder-text)")
            lay.addWidget(label)
        self.table = QTableView()
        self.table.setModel(_Changes(rows, self.table))
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(self.fontMetrics().height() + 8)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        lay.addWidget(self.table, 1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.buttons.addButton(go, QDialogButtonBox.ButtonRole.AcceptRole).setDefault(True)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)


def relative(path: str, library) -> str:
    """path as shown: from its library folder."""
    import os

    from ...core.organize import root_of

    root = root_of(path, library)
    return os.path.relpath(path, root) if root else path
