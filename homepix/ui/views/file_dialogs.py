"""The small dialogs for working with files: renaming one photo or many, and exporting."""

import datetime as dt
import os

from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
)

from ...core.fileops import pattern_names
from ..fileops import bad_name


def _taken(item):
    try:
        return dt.datetime.fromisoformat(item.taken) if not item.taken.startswith("0000") else None
    except ValueError:
        return None


class _Dialog(QDialog):
    def __init__(self, title, parent):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.lay = QVBoxLayout(self)
        self.error = QLabel()
        self.error.setStyleSheet("color: palette(link)")
        self.error.setWordWrap(True)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

    def finish_layout(self):
        self.lay.addWidget(self.error)
        self.lay.addWidget(self.buttons)

    def problem(self, text):
        self.error.setText(text or "")
        self.error.setVisible(bool(text))
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(not text)


class RenameDialog(_Dialog):
    """One photo: its name, with the part before the extension ready to type over."""

    def __init__(self, path: str, parent=None):
        super().__init__("Rename", parent)
        self.path = path
        self.resize(460, 0)
        self.edit = QLineEdit(os.path.basename(path))
        stem = os.path.splitext(os.path.basename(path))[0]
        self.edit.setSelection(0, len(stem))
        self.edit.textChanged.connect(self._check)
        self.lay.addWidget(QLabel("New name:"))
        self.lay.addWidget(self.edit)
        self.finish_layout()
        self._check(self.edit.text())

    def _check(self, text):
        self.problem(bad_name(text))

    def name(self) -> str:
        return self.edit.text().strip()


class BatchRenameDialog(_Dialog):
    """Many photos: a pattern, with a preview of the names it makes."""

    HELP = (
        "<b>{name}</b> the current name &nbsp; <b>{n}</b> a number: 1, 2, 3... &nbsp; "
        "<b>{date}</b> the day it was taken"
    )

    def __init__(self, items, parent=None, pattern="{date} {name}"):
        super().__init__(f"Rename {len(items):,} photos", parent)
        self.items = [(it.path, _taken(it)) for it in items]
        self.resize(520, 0)
        self.edit = QLineEdit(pattern)
        self.edit.textChanged.connect(self._check)
        help_ = QLabel(self.HELP)
        help_.setWordWrap(True)
        self.preview = QLabel()
        self.preview.setWordWrap(True)
        self.lay.addWidget(QLabel("Name them:"))
        self.lay.addWidget(self.edit)
        self.lay.addWidget(help_)
        self.lay.addWidget(self.preview)
        note = QLabel("Each keeps its own extension. A name that's taken gets _1, _2, ...")
        note.setStyleSheet("color: palette(placeholder-text)")
        self.lay.addWidget(note)
        self.finish_layout()
        self._check(pattern)

    def names(self) -> list[str]:
        return pattern_names(self.edit.text(), self.items)

    def _check(self, _):
        try:
            names = self.names()
        except (KeyError, ValueError, IndexError):
            self.preview.setText("")
            self.problem("Use only {name}, {n} and {date} in braces.")
            return
        problem = next((p for p in map(bad_name, names) if p), None)
        ext = [os.path.splitext(p)[1] for p, _ in self.items]
        shown = [f"{os.path.basename(p)}  →  {n}{e}" for (p, _), n, e in zip(self.items[:3], names, ext, strict=False)]
        if len(names) > 3:
            shown.append("...")
        self.preview.setText("<br>".join(s.replace("&", "&amp;").replace("<", "&lt;") for s in shown))
        self.problem(problem)


class ExportDialog(_Dialog):
    """Originals, or smaller JPEGs (for sharing): the choice is remembered."""

    def __init__(self, count: int, settings, parent=None):
        super().__init__(f"Export {count:,} file{'s' if count != 1 else ''}", parent)
        self.settings = settings
        self.original = QRadioButton("The original files, as they are")
        self.smaller = QRadioButton("Smaller JPEGs, for sharing (videos are copied as they are)")
        group = QButtonGroup(self)
        group.addButton(self.original)
        group.addButton(self.smaller)
        self.side = QSpinBox()
        self.side.setRange(320, 12000)
        self.side.setSingleStep(256)
        self.side.setSuffix(" px")
        self.quality = QSpinBox()
        self.quality.setRange(40, 100)
        form = QFormLayout()
        form.addRow("Longest side:", self.side)
        form.addRow("Quality:", self.quality)
        self.lay.addWidget(self.original)
        self.lay.addWidget(self.smaller)
        self.lay.addLayout(form)
        self.finish_layout()
        self.problem(None)
        smaller = settings.value("export/smaller", "false") == "true"
        (self.smaller if smaller else self.original).setChecked(True)
        self.side.setValue(int(settings.value("export/side", 2048)))
        self.quality.setValue(int(settings.value("export/quality", 85)))
        self.smaller.toggled.connect(self._toggled)
        self._toggled(smaller)

    def _toggled(self, on):
        self.side.setEnabled(on)
        self.quality.setEnabled(on)

    def accept(self):
        self.settings.setValue("export/smaller", "true" if self.smaller.isChecked() else "false")
        self.settings.setValue("export/side", self.side.value())
        self.settings.setValue("export/quality", self.quality.value())
        super().accept()

    def choice(self) -> tuple[int | None, int]:
        """(longest side, or None for the originals; JPEG quality)."""
        return (self.side.value() if self.smaller.isChecked() else None), self.quality.value()
