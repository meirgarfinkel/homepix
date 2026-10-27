"""Settings: the folders that make up the library."""

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ...core import library_folders, pictures_folder


class SettingsDialog(QDialog):
    add_requested = Signal(str)
    remove_requested = Signal(str)
    rescan_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(560, 380)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("<b>Library folders</b>"))
        note = QLabel("Photo Finder shows and searches the photos in these folders and the folders inside "
                      "them. It only reads them: nothing is changed on its own.")
        note.setWordWrap(True)
        lay.addWidget(note)
        self.list = QListWidget()
        self.list.itemSelectionChanged.connect(self._selection)
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        add = QPushButton("Add a folder...")
        add.clicked.connect(self._add)
        self.remove = QPushButton("Remove")
        self.remove.clicked.connect(self._remove)
        rescan = QPushButton("Check for new photos now")
        rescan.clicked.connect(self.rescan_requested)
        row.addWidget(add)
        row.addWidget(self.remove)
        row.addStretch()
        row.addWidget(rescan)
        lay.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.refresh()

    def refresh(self):
        self.list.clear()
        for f in library_folders():
            item = QListWidgetItem(f if Path(f).is_dir() else f"{f}   (not connected right now)")
            item.setData(Qt.ItemDataRole.UserRole, f)
            self.list.addItem(item)
        self._selection()

    def _selection(self):
        self.remove.setEnabled(bool(self.list.selectedItems()))

    def _add(self):
        path = QFileDialog.getExistingDirectory(self, "Choose a folder with photos", str(pictures_folder()))
        if path:
            self.add_requested.emit(path)

    def _remove(self):
        items = self.list.selectedItems()
        if not items:
            return
        folder = items[0].data(Qt.ItemDataRole.UserRole)
        answer = QMessageBox.question(
            self, "Remove folder",
            f"Take {folder} out of the library?\n\nIts photos will no longer show in Photo Finder, and what it "
            "learned about them (for search, and who is in them) is forgotten. The files themselves "
            "aren't touched.",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.remove_requested.emit(folder)
