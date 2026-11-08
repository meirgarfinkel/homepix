"""Setting up an import automation: its name, the folder it imports from, where it imports to (a
folder in the library, or the library's year folders when organizing), and how it behaves."""

import os
from dataclasses import replace

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
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
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ...core import autoimport
from ...core.settings import pictures_folder

EMPTY_WARNING = (
    "Imported files are moved out of this folder. If your sync app copies deletions back to the phone or to "
    "iCloud, they'll be deleted there too. With Syncthing, set this folder to Receive Only on this computer. "
    "With iCloud for Windows or icloudpd, leave this off."
)
KEEP_NOTE = "Imported files are copied, and the originals stay in this folder."


def _folder_row(edit: QLineEdit, choose) -> QWidget:
    w = QWidget()
    row = QHBoxLayout(w)
    row.setContentsMargins(0, 0, 0, 0)
    row.addWidget(edit, 1)
    b = QPushButton("Choose...")
    b.setAutoDefault(False)
    b.clicked.connect(choose)
    row.addWidget(b)
    return w


def _hint(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setObjectName("hint")
    label.setWordWrap(True)
    return label


def _option(box: QCheckBox, hint: QLabel) -> QWidget:
    """A checkbox with its explanation right under it."""
    w = QWidget()
    col = QVBoxLayout(w)
    col.setContentsMargins(0, 0, 0, 4)
    col.setSpacing(2)
    col.addWidget(box)
    col.addWidget(hint)
    hint.setIndent(box.style().pixelMetric(box.style().PixelMetric.PM_IndicatorWidth) + 6)
    return w


class RuleDialog(QDialog):
    def __init__(self, rule: autoimport.Rule | None, rules: list, folders: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import automation" if rule else "New import automation")
        self.rules, self.folders = rules, folders
        self.rule = rule or autoimport.Rule(name="", watch="", dest=folders[0] if len(folders) == 1 else "")
        r = self.rule
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(r.name)
        self.name.setPlaceholderText("e.g. Dad's phone")
        form.addRow("Name:", self.name)
        self.tidy = QCheckBox("Rename and organize into year folders")  # (it decides what Import to offers)
        self.tidy.setChecked(r.tidy)
        self.tidy.toggled.connect(self._tidy_toggled)
        form.addRow("", self.tidy)
        self.watch = QLineEdit(r.watch)
        self.watch.setPlaceholderText("The folder your sync app puts new photos in")
        form.addRow("Import from:", _folder_row(self.watch, self._choose_watch))

        # Import to: a folder in the library, or (organizing) which library folder's year folders
        self.dest = QLineEdit(r.dest if not r.tidy else "")
        self.dest.setPlaceholderText("A folder in your library")
        self.root = QComboBox()
        self.root.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.root.setMinimumContentsLength(20)  # (a long path doesn't widen the window)
        self.root.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)  # (never taller than one line)
        for f in folders:
            self.root.addItem(f, f)
        if r.tidy and r.dest in folders:
            self.root.setCurrentIndex(folders.index(r.dest))
        # only one shows: a folder to choose, or (organizing) a library folder
        self.to = QWidget()
        to_row = QHBoxLayout(self.to)
        to_row.setContentsMargins(0, 0, 0, 0)
        self.dest_row = _folder_row(self.dest, self._choose_dest)
        to_row.addWidget(self.dest_row)
        to_row.addWidget(self.root)
        self.to.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        form.addRow("Import to:", self.to)

        options = QVBoxLayout()  # (a plain column: a form layout leaves gaps around wrapped text)
        options.setSpacing(8)
        self.empty = QCheckBox('Empty the "Import from" folder after importing')
        self.empty.setChecked(r.after == autoimport.MOVE)
        self.empty.toggled.connect(self._empty_toggled)
        self.empty_note = _hint()
        options.addWidget(_option(self.empty, self.empty_note))
        self.ask = QCheckBox("Ask me before importing")
        self.ask.setChecked(not r.auto)
        options.addWidget(_option(self.ask, _hint("You'll see the new photos first and choose which ones to import.")))
        self.paused = QCheckBox("Pause automation")
        self.paused.setChecked(not r.enabled)
        options.addWidget(self.paused)
        lay.addLayout(form)
        label_width = max(form.labelForField(w).sizeHint().width() for w in (self.name, self.watch.parent(), self.to))
        gap = form.horizontalSpacing()
        if gap < 0:
            gap = self.style().pixelMetric(self.style().PixelMetric.PM_LayoutHorizontalSpacing)
        options.setContentsMargins(label_width + gap, 6, 0, 0)
        lay.addLayout(options)

        self.problem = QLabel()
        self.problem.setObjectName("problem")
        self.problem.setWordWrap(True)
        self.problem.hide()
        lay.addWidget(self.problem)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self._tidy_toggled(self.tidy.isChecked())
        self._empty_toggled(self.empty.isChecked())
        self.setMinimumWidth(600)
        self.adjustSize()

    def _tidy_toggled(self, on: bool):
        self.dest_row.setVisible(not on)
        self.root.setVisible(on)
        QTimer.singleShot(0, self._fit_notes)  # (once the layout has caught up: no gap left behind)

    def _empty_toggled(self, on: bool):
        self.empty_note.setText(EMPTY_WARNING if on else KEEP_NOTE)
        self._fit_notes()

    def _fit_notes(self):
        """Give each wrapped note the height its lines need at its width (layouts don't on their own)."""
        for label in self.findChildren(QLabel):
            if label.objectName() == "hint" and label.width() > 50:
                label.setFixedHeight(label.heightForWidth(label.width()))  # (its own guess is too tall)
        self.adjustSize()

    def showEvent(self, e):
        super().showEvent(e)
        self._fit_notes()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if e.oldSize().width() != e.size().width():
            self._fit_notes()

    def _choose_watch(self):
        start = self.watch.text() or str(pictures_folder())
        path = QFileDialog.getExistingDirectory(self, "Import from", start)
        if path:
            self.watch.setText(path)
            if not self.name.text().strip():
                self.name.setText(os.path.basename(path))

    def _choose_dest(self):
        start = self.dest.text() or (self.folders[0] if self.folders else str(pictures_folder()))
        path = QFileDialog.getExistingDirectory(self, "Import to", start)
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
            after=autoimport.MOVE if self.empty.isChecked() else autoimport.LEAVE,
            auto=not self.ask.isChecked(),
            enabled=not self.paused.isChecked(),
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
