"""Settings: the folders that make up the library, the app to edit photos in, and the look."""

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ...core.settings import library_folders, load_settings, pictures_folder, save_settings
from .. import theme


class SettingsDialog(QDialog):
    add_requested = Signal(str)
    remove_requested = Signal(str)
    rescan_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(600, 460)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("<b>Library folders</b>"))
        note = QLabel("The folders HomePix shows and searches.")
        note.setWordWrap(True)
        lay.addWidget(note)
        self.list = QListWidget()
        self.list.itemSelectionChanged.connect(self._selected_folder)
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        add = QPushButton("Add a folder...")
        add.clicked.connect(self._add_folder)
        self.remove = QPushButton("Remove")
        self.remove.clicked.connect(self._remove_folder)
        rescan = QPushButton("Check for new photos now")
        rescan.clicked.connect(self.rescan_requested)
        row.addWidget(add)
        row.addWidget(self.remove)
        row.addStretch()
        row.addWidget(rescan)
        lay.addLayout(row)
        lay.addSpacing(10)
        lay.addWidget(QLabel("<b>Editing photos</b>"))
        self.editor = QLineEdit(load_settings().get("editor", ""))
        self.editor.setPlaceholderText("The usual app for each kind of file")
        self.editor.setToolTip(
            "The app that Open in... uses, e.g. gimp or darktable (the photo is added at "
            "the end, or where %f is). Leave it empty for the usual app."
        )
        self.editor.editingFinished.connect(self._save_editor)
        form = QHBoxLayout()
        form.addWidget(QLabel("Open photos in:"))
        form.addWidget(self.editor, 1)
        lay.addLayout(form)
        lay.addSpacing(10)
        lay.addWidget(QLabel("<b>Look</b>"))
        self.look = QComboBox()
        for key, name in theme.MODES.items():
            self.look.addItem(name, key)
        self.look.setCurrentIndex(self.look.findData(theme.mode()))
        self.look.currentIndexChanged.connect(lambda _: theme.set_mode(self.look.currentData()))
        look = QHBoxLayout()
        look.addWidget(QLabel("Colours:"))
        look.addWidget(self.look)
        look.addStretch()
        lay.addLayout(look)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.refresh()

    def _save_editor(self):
        s = load_settings()
        s["editor"] = self.editor.text().strip()
        save_settings(s)

    def done(self, r):
        self._save_editor()
        super().done(r)

    def refresh(self):
        self.list.clear()
        for f in library_folders():
            item = QListWidgetItem(f if Path(f).is_dir() else f"{f}   (not connected right now)")
            item.setData(Qt.ItemDataRole.UserRole, f)
            self.list.addItem(item)
        self._selected_folder()

    def _selected_folder(self):
        self.remove.setEnabled(bool(self.list.selectedItems()))

    def _add_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Choose a folder with photos", str(pictures_folder()))
        if path:
            self.add_requested.emit(path)

    def _remove_folder(self):
        items = self.list.selectedItems()
        if not items:
            return
        folder = items[0].data(Qt.ItemDataRole.UserRole)
        answer = QMessageBox.question(
            self,
            "Remove folder",
            f"Remove {folder} from the library?\n\nYour files aren't touched.",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.remove_requested.emit(folder)
