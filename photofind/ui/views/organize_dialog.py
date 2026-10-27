"""The organizer's preview: every file it would rename or move, before anything is done."""

import os
from collections import Counter

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QHeaderView, QLabel, QTableView, QVBoxLayout

from ...core.organize import Plan, root_of


def _why(why: str) -> str:
    if why == "renamed":
        return "Named by its date"
    if why == "with its photo":
        return "Goes with its photo"
    place = why.removeprefix("to ")
    return "No date: into Unsorted/" if place == "Unsorted" else f"Into {place}/"


_ROOT = QModelIndex()


class _Changes(QAbstractTableModel):
    HEADERS = ("Now", "Will be", "Why")

    def __init__(self, plan: Plan, folders, parent=None):
        super().__init__(parent)
        self.rows = []
        for c in plan.changes:
            root = root_of(c.src, folders) or os.path.dirname(c.src)
            self.rows.append((os.path.relpath(c.src, root), os.path.relpath(c.dst, root), _why(c.why), c.src))

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


def summary(plan: Plan) -> str:
    """"940 go with their photo, 3 into Unsorted/ ..." for the files that change."""
    counts = Counter("year" if c.why.startswith("to ") and c.why != "to Unsorted" else c.why for c in plan.changes)
    parts = [(counts["renamed"], "named by their date"), (counts["year"], "into their year's folder"),
             (counts["to Unsorted"], "into Unsorted/ (no date)"), (counts["with its photo"], "go with their photo")]
    return ", ".join(f"{n:,} {text}" for n, text in parts if n)


class OrganizeDialog(QDialog):
    def __init__(self, plan: Plan, folders, title: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(900, 560)
        lay = QVBoxLayout(self)
        n = len(plan.changes)
        head = QLabel(f"<b>{n:,} of {plan.looked_at:,} files</b> will be renamed or moved: {summary(plan)}.")
        head.setWordWrap(True)
        lay.addWidget(head)
        note = ("Each is named by when it was taken, in its year's folder. Nothing changes until you "
                "choose Organize, and Undo takes it all back.")
        if plan.not_ready:
            note += f" {plan.not_ready:,} files are still being indexed and are left for now."
        sub = QLabel(note)
        sub.setWordWrap(True)
        sub.setStyleSheet("color: palette(placeholder-text)")
        lay.addWidget(sub)
        self.table = QTableView()
        self.table.setModel(_Changes(plan, folders, self.table))
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
        go = self.buttons.addButton(f"Organize {n:,} files", QDialogButtonBox.ButtonRole.AcceptRole)
        go.setDefault(True)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
