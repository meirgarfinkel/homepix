"""Settings: the folders that make up the library, the folders excluded from it (exclude a folder,
take one back in), import automations, the app to edit photos in, and the theme."""

import os
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
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

from ...core import autoimport, background
from ...core.settings import (
    excluded_folders,
    library_folders,
    load_settings,
    pictures_folder,
    save_settings,
    set_excluded,
)
from ...core.util import within
from .. import theme
from ..autoimport import describe
from .rule_dialog import RuleDialog


class SettingsDialog(QDialog):
    add_requested = Signal(str)
    remove_requested = Signal(str)
    rescan_requested = Signal()
    left_out_changed = Signal()  # a folder excluded, or taken back in
    import_check_requested = Signal()  # Check now, for import automations
    background_changed = Signal()  # Keep running in the background, turned on or off

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(640, 800)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("<b>Library folders</b>"))
        note = QLabel("The folders HomePix shows and searches.")
        note.setObjectName("hint")
        note.setWordWrap(True)
        lay.addWidget(note)
        self.list = QListWidget()
        self.list.itemSelectionChanged.connect(self._selected_folder)
        lay.addWidget(self.list, 3)
        row = QHBoxLayout()
        add = QPushButton("Add a folder...")
        add.clicked.connect(self._add_folder)
        self.remove = QPushButton("Remove")
        self.remove.clicked.connect(self._remove_folder)
        rescan = QPushButton("Check for new photos now")
        rescan.setObjectName("checkButton")
        rescan.clicked.connect(self.rescan_requested)
        row.addWidget(add)
        row.addWidget(self.remove)
        row.addStretch()
        row.addWidget(rescan)
        lay.addLayout(row)

        lay.addSpacing(10)
        lay.addWidget(QLabel("<b>Excluded folders</b>"))
        note = QLabel(
            "Folders whose photos don't show in All photos, people or search. "
            "They still show when you open the folder itself."
        )
        note.setObjectName("hint")
        note.setWordWrap(True)
        lay.addWidget(note)
        self.left_list = QListWidget()
        self.left_list.setMinimumHeight(80)  # (room for a few rows)
        self.left_list.itemSelectionChanged.connect(self._selected_folder)
        lay.addWidget(self.left_list, 2)
        row = QHBoxLayout()
        self.leave_button = QPushButton("Exclude a folder...")
        self.leave_button.clicked.connect(self._leave_out)
        self.back_button = QPushButton("Take back in")
        self.back_button.clicked.connect(self._take_back)
        row.addWidget(self.leave_button)
        row.addWidget(self.back_button)
        row.addStretch()
        lay.addLayout(row)

        lay.addSpacing(10)
        lay.addWidget(QLabel("<b>Import automations</b>"))
        note = QLabel(
            "Import new photos by themselves from a folder your sync app fills (from a phone, say). "
            "Checked every minute while HomePix is running."
        )
        note.setObjectName("hint")
        note.setWordWrap(True)
        lay.addWidget(note)
        self.rule_list = QListWidget()
        self.rule_list.setMinimumHeight(90)  # (room for a few)
        self.rule_list.itemSelectionChanged.connect(self._selected_folder)
        self.rule_list.itemDoubleClicked.connect(lambda _: self._edit_rule())
        lay.addWidget(self.rule_list, 2)
        row = QHBoxLayout()
        self.add_rule_button = QPushButton("New automation...")
        self.add_rule_button.clicked.connect(lambda: self._edit_rule(new=True))
        self.edit_rule_button = QPushButton("Edit...")
        self.edit_rule_button.clicked.connect(lambda: self._edit_rule())
        self.remove_rule_button = QPushButton("Remove")
        self.remove_rule_button.clicked.connect(self._remove_rule)
        self.check_now = QPushButton("Check now")
        self.check_now.setObjectName("checkButton")
        self.check_now.clicked.connect(self.import_check_requested)
        for b in (self.add_rule_button, self.edit_rule_button, self.remove_rule_button):
            row.addWidget(b)
        row.addStretch()
        row.addWidget(self.check_now)
        lay.addLayout(row)
        self.keep_running = QCheckBox("Keep running in the background")
        self.keep_running.setChecked(background.on())
        self.keep_running.setToolTip(
            "HomePix starts hidden when you log in, and closing the window leaves it running, so new photos "
            "come in with the window closed. File > Quit stops it until you next log in or open it."
        )
        self.keep_running.toggled.connect(self._keep_running)
        lay.addWidget(self.keep_running)
        note = QLabel("Starts hidden when you log in, so new photos come in with the window closed.")
        note.setObjectName("hint")
        note.setWordWrap(True)
        lay.addWidget(note)

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
        lay.addWidget(QLabel("<b>Theme</b>"))
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
        for b in self.findChildren(QPushButton):
            b.setAutoDefault(False)  # (none of them is the main one)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.refresh()

    def _keep_running(self, want: bool):
        why = background.set_on(want)
        self.background_changed.emit()
        if why:
            QMessageBox.warning(
                self, "Keep running in the background", f"HomePix couldn't be set to start when you log in:\n{why}"
            )

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
            self._add_item(self.list, f if Path(f).is_dir() else f"{f}   (not connected right now)", f)
        keep = self._chosen(self.left_list)
        self.left_list.clear()
        for f in excluded_folders():
            self._add_item(self.left_list, f, f, keep)
        self.refresh_rules()

    def refresh_rules(self):
        keep = self._chosen(self.rule_list)
        self.rule_list.clear()
        for r in autoimport.load_rules():
            item = QListWidgetItem(theme.icon("folder", "muted"), f"{r.name}  ·  {describe(r)}")
            item.setData(Qt.ItemDataRole.UserRole, r.id)
            item.setToolTip(f"Import from {r.watch}\nImport to {'the year folders in ' if r.tidy else ''}{r.dest}")
            self.rule_list.addItem(item)
            if r.id == keep:
                item.setSelected(True)
        self._selected_folder()

    def _edit_rule(self, new: bool = False):
        folders = library_folders()
        if not folders:
            QMessageBox.information(
                self, "Import automations", "Add a library folder first: imported photos go into it."
            )
            return
        rules = autoimport.load_rules()
        chosen = None if new else self._chosen(self.rule_list)
        rule = next((r for r in rules if r.id == chosen), None)
        if not new and rule is None:
            return
        dlg = RuleDialog(rule, rules, folders, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        rules = [dlg.rule if r.id == dlg.rule.id else r for r in rules]
        if new:
            rules.append(dlg.rule)
        autoimport.save_rules(rules)
        if dlg.excluded_now:
            self.refresh()  # (the folder is in Excluded folders now too)
            self.left_out_changed.emit()
        else:
            self.refresh_rules()

    def _remove_rule(self):
        chosen = self._chosen(self.rule_list)
        rules = autoimport.load_rules()
        rule = next((r for r in rules if r.id == chosen), None)
        if rule is None:
            return
        answer = QMessageBox.question(
            self,
            "Remove automation",
            f'Remove "{rule.name}"?\n\nNo photos are touched.',
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            autoimport.save_rules([r for r in rules if r.id != rule.id])
            self.refresh_rules()

    @staticmethod
    def _add_item(lst, text, path, keep=None):
        item = QListWidgetItem(theme.icon("folder", "muted"), text)
        item.setData(Qt.ItemDataRole.UserRole, path)
        lst.addItem(item)
        if path == keep:
            item.setSelected(True)

    @staticmethod
    def _chosen(lst) -> str | None:
        items = lst.selectedItems()
        return items[0].data(Qt.ItemDataRole.UserRole) if items else None

    def _selected_folder(self):
        self.remove.setEnabled(bool(self.list.selectedItems()))
        self.back_button.setEnabled(self._chosen(self.left_list) is not None)
        self.leave_button.setEnabled(bool(library_folders()))
        if hasattr(self, "rule_list"):
            chosen = self._chosen(self.rule_list) is not None
            self.edit_rule_button.setEnabled(chosen)
            self.remove_rule_button.setEnabled(chosen)

    def _leave_out(self):
        roots = library_folders()
        start = self._chosen(self.list) or (roots[0] if roots else str(pictures_folder()))
        chosen = QFileDialog.getExistingDirectory(self, "Choose a folder to exclude", start)
        if not chosen:
            return
        chosen = os.path.abspath(chosen)
        if chosen in map(os.path.abspath, roots):
            why = "That's a whole library folder. To stop showing it, remove it from Library folders above."
        elif not within(chosen, roots):
            why = "That folder isn't in the library, so there's nothing to exclude."
        elif within(chosen, excluded_folders()):
            why = "That folder is already excluded."
        else:
            why = None
        if why:
            QMessageBox.information(self, "Exclude a folder", why)
            return
        set_excluded(chosen, True)
        self.refresh()
        self.left_out_changed.emit()

    def _take_back(self):
        path = self._chosen(self.left_list)
        if path is not None:
            set_excluded(path, False)
            self.refresh()
            self.left_out_changed.emit()

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
