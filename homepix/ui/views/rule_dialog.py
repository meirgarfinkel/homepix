"""Adding or changing an auto import rule: its name, the watch folder, where files go, and what
happens to them after."""

import os
from dataclasses import replace

from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from ...core import autoimport
from ...core.settings import pictures_folder

MOVE_WARNING = (
    "Moving files out deletes them from the watch folder. With some sync apps that deletes them from the "
    "phone or from iCloud too: with Syncthing, set this folder to Receive Only on this computer; with "
    "iCloud for Windows or icloudpd, choose Leave them there."
)


def _folder_row(edit: QLineEdit, choose) -> QWidget:
    w = QWidget()
    row = QHBoxLayout(w)
    row.setContentsMargins(0, 0, 0, 0)
    row.addWidget(edit, 1)
    b = QPushButton("Choose...")
    b.setAutoDefault(False)
    b.clicked.connect(choose)
    row.addWidget(b)
    w.button = b
    return w


class RuleDialog(QDialog):
    def __init__(self, rule: autoimport.Rule | None, rules: list, folders: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Auto import rule" if rule else "New auto import rule")
        self.rules, self.folders = rules, folders
        self.rule = rule or autoimport.Rule(name="", watch="", dest=folders[0] if len(folders) == 1 else "")
        r = self.rule
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(r.name)
        self.name.setPlaceholderText("e.g. Dad's phone")
        form.addRow("Name:", self.name)
        self.watch = QLineEdit(r.watch)
        self.watch.setPlaceholderText("Where the sync app puts new photos")
        form.addRow("Watch folder:", _folder_row(self.watch, self._choose_watch))

        self.tidy = QCheckBox("Tidy up: name by date and file into year folders")
        self.tidy.setChecked(r.tidy)
        self.tidy.toggled.connect(self._tidy_toggled)
        form.addRow("", self.tidy)
        self.dest = QLineEdit(r.dest if not r.tidy else "")
        self.dest.setPlaceholderText("A folder in your library")
        self.dest_row = _folder_row(self.dest, self._choose_dest)
        form.addRow("Destination:", self.dest_row)
        self.root = QComboBox()
        for f in folders:
            self.root.addItem(f, f)
        if r.tidy and r.dest in folders:
            self.root.setCurrentIndex(folders.index(r.dest))
        self.root_label = QLabel("Library folder:")
        form.addRow(self.root_label, self.root)

        after = QWidget()
        after_lay = QVBoxLayout(after)
        after_lay.setContentsMargins(0, 0, 0, 0)
        self.move = QRadioButton("Move them out of the watch folder")
        self.leave = QRadioButton("Leave them there")
        group = QButtonGroup(self)
        for b in (self.move, self.leave):
            group.addButton(b)
            after_lay.addWidget(b)
        (self.move if r.after == autoimport.MOVE else self.leave).setChecked(True)
        self.warning = QLabel(MOVE_WARNING)
        self.warning.setObjectName("hint")
        self.warning.setWordWrap(True)
        after_lay.addWidget(self.warning)
        self.move.toggled.connect(self.warning.setVisible)
        form.addRow("After importing:", after)

        self.auto = QCheckBox("Import new files by themselves (off: they wait for you to review them)")
        self.auto.setChecked(r.auto)
        form.addRow("", self.auto)
        self.enabled = QCheckBox("This rule is on")
        self.enabled.setChecked(r.enabled)
        form.addRow("", self.enabled)
        lay.addLayout(form)

        self.problem = QLabel()
        self.problem.setObjectName("problem")
        self.problem.setWordWrap(True)
        self.problem.hide()
        lay.addWidget(self.problem)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.warning.setVisible(self.move.isChecked())
        self._tidy_toggled(self.tidy.isChecked())
        self.resize(560, self.sizeHint().height())

    def _tidy_toggled(self, on: bool):
        self.dest_row.setEnabled(not on)
        self.dest.setEnabled(not on)
        self.dest.setPlaceholderText("Year folders in your library" if on else "A folder in your library")
        many = len(self.folders) > 1
        self.root.setVisible(on and many)
        self.root_label.setVisible(on and many)

    def _choose_watch(self):
        start = self.watch.text() or str(pictures_folder())
        path = QFileDialog.getExistingDirectory(self, "Choose the watch folder", start)
        if path:
            self.watch.setText(path)
            if not self.name.text().strip():
                self.name.setText(os.path.basename(path))

    def _choose_dest(self):
        start = self.dest.text() or (self.folders[0] if self.folders else str(pictures_folder()))
        path = QFileDialog.getExistingDirectory(self, "Choose where imported files go", start)
        if path:
            self.dest.setText(path)

    def result_rule(self) -> autoimport.Rule:
        tidy = self.tidy.isChecked()
        if tidy:
            dest = self.root.currentData() or ""
        else:
            dest = os.path.abspath(self.dest.text().strip()) if self.dest.text().strip() else ""
        return replace(
            self.rule,
            name=self.name.text().strip(),
            watch=os.path.abspath(self.watch.text().strip()) if self.watch.text().strip() else "",
            dest=dest,
            tidy=tidy,
            after=autoimport.MOVE if self.move.isChecked() else autoimport.LEAVE,
            auto=self.auto.isChecked(),
            enabled=self.enabled.isChecked(),
        )

    def _save(self):
        rule = self.result_rule()
        why = autoimport.problem(rule, self.rules, self.folders)
        if why:
            self.problem.setText(why)
            self.problem.show()
            return
        self.rule = rule
        self.accept()
