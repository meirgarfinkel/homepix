"""Dialogs for the details written in photos: editing them (one photo or many), the date and time,
and every tag a file holds."""

import datetime as dt
import os
import re

from PySide6.QtCore import QDateTime, Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QRadioButton,
    QSpinBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from ...core.metadata import Edit


def parse_place(text: str):
    """ "31.778, 35.235" (or with spaces, or a map link holding them) -> (lat, lon); "" -> (); None if
    it can't be read."""
    text = text.strip()
    if not text:
        return ()
    nums = re.findall(r"-?\d+(?:\.\d+)?", text)  # (a map link's first two numbers are the place too)
    if len(nums) < 2:
        return None
    lat, lon = float(nums[0]), float(nums[1])
    return (lat, lon) if -90 <= lat <= 90 and -180 <= lon <= 180 else None


def _common(values):
    """The value all have, or None if they differ."""
    values = list(values)
    return values[0] if values and all(v == values[0] for v in values) else None


class DetailsDialog(QDialog):
    """Description, keywords and location, for one photo or many. Only what's changed here is
    written; with several photos, the keywords shown are those all of them have, and adding or
    removing one does so in each (their other keywords stay)."""

    def __init__(self, details: dict, parent=None):
        super().__init__(parent)
        n = len(details)
        self.setWindowTitle("Details" if n == 1 else f"Details of {n:,} photos")
        self.resize(520, 0)
        ds = list(details.values())
        self._touched = set()
        form = QFormLayout()
        self.description = QPlainTextEdit()
        self.description.setFixedHeight(90)
        desc = _common(d.description for d in ds)
        if desc is None:
            self.description.setPlaceholderText("Different in each. Type to set them all.")
        else:
            self.description.setPlainText(desc)
            self.description.setPlaceholderText("What's happening, who's there...")
        self.description.textChanged.connect(lambda: self._touched.add("description"))
        form.addRow("Description:", self.description)

        self.common_words = [
            w for w in ds[0].keywords if all(w.casefold() in {x.casefold() for x in d.keywords} for d in ds)
        ]
        self.keywords = QLineEdit(", ".join(self.common_words))
        self.keywords.setPlaceholderText("e.g. holiday, beach (separated by commas)")
        if n > 1 and any(d.keywords for d in ds):
            self.keywords.setToolTip("Keywords they all share.")
        self.keywords.textEdited.connect(lambda: self._touched.add("keywords"))
        form.addRow("Keywords:", self.keywords)

        place = _common(d.gps for d in ds)
        self.place = QLineEdit("" if not place else f"{place[0]:.6f}, {place[1]:.6f}")
        self.place.setPlaceholderText("Different in each" if place is None and n > 1 else "e.g. 31.778, 35.235")
        self.place.textEdited.connect(lambda: self._touched.add("place"))
        form.addRow("Location:", self.place)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        self.error = QLabel()
        self.error.setStyleSheet("color: #c0392b")
        lay.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def accept(self):
        if "place" in self._touched and parse_place(self.place.text()) is None:
            self.error.setText("Type a location like 31.778, 35.235.")
            return
        super().accept()

    def edit(self) -> Edit:
        e = Edit()
        if "description" in self._touched:
            e.description = self.description.toPlainText()
        if "keywords" in self._touched:
            new = [w.strip() for w in self.keywords.text().split(",") if w.strip()]
            old = {w.casefold() for w in self.common_words}
            e.add_keywords = tuple(w for w in new if w.casefold() not in old)
            kept = {w.casefold() for w in new}
            e.remove_keywords = tuple(w for w in self.common_words if w.casefold() not in kept)
        if "place" in self._touched:
            e.gps = parse_place(self.place.text())
        return e


class DateDialog(QDialog):
    """Set the date and time, or move them by some hours (a camera's clock was wrong, or set to
    another time zone)."""

    def __init__(self, items, parent=None):
        super().__init__(parent)
        n = len(items)
        self.setWindowTitle("Date and time" if n == 1 else f"Date and time of {n:,} photos")
        self.items = items
        first = self._taken(items[0]) or dt.datetime.now().replace(second=0, microsecond=0)
        self.set_to = QRadioButton("Set to:" if n == 1 else "Set all of them to:")
        self.shift = QRadioButton("Move by:")
        group = QButtonGroup(self)
        group.addButton(self.set_to)
        group.addButton(self.shift)
        self.when = QDateTimeEdit(QDateTime(first))
        self.when.setDisplayFormat("yyyy-MM-dd  HH:mm:ss")
        self.when.setCalendarPopup(True)
        self.days, self.hours, self.minutes = QSpinBox(), QSpinBox(), QSpinBox()
        for box, top, unit in ((self.days, 36500, " days"), (self.hours, 23, " h"), (self.minutes, 59, " min")):
            box.setRange(-top, top)
            box.setSuffix(unit)
        shift_row = QHBoxLayout()
        for w in (self.days, self.hours, self.minutes):
            shift_row.addWidget(w)
        shift_row.addStretch()
        form = QFormLayout()
        form.addRow(self.set_to, self.when)
        form.addRow(self.shift, shift_row)
        self.preview = QLabel()
        self.preview.setWordWrap(True)
        self.preview.setStyleSheet("color: palette(placeholder-text)")
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.preview)
        if n > 1:
            note = QLabel("Move keeps their spacing; Set gives all the same time.")
            note.setWordWrap(True)
            lay.addWidget(note)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        (self.set_to if n == 1 else self.shift).setChecked(True)
        for w in (self.days, self.hours, self.minutes):
            w.valueChanged.connect(lambda _: (self.shift.setChecked(True), self._update_preview()))
        self.when.dateTimeChanged.connect(lambda _: (self.set_to.setChecked(True), self._update_preview()))
        group.buttonToggled.connect(lambda *_: self._update_preview())
        self._update_preview()

    @staticmethod
    def _taken(item):
        try:
            return None if item.taken.startswith("0000") else dt.datetime.fromisoformat(item.taken)
        except ValueError:
            return None

    def time_shift(self) -> dt.timedelta:
        return dt.timedelta(days=self.days.value(), hours=self.hours.value(), minutes=self.minutes.value())

    def _update_preview(self):
        lines = []
        for it in self.items[:3]:
            old = self._taken(it)
            new = (
                self.when.dateTime().toPython()
                if self.set_to.isChecked()
                else (old + self.time_shift() if old else None)
            )
            shown = old.strftime("%Y-%m-%d %H:%M") if old else "no date"
            lines.append(
                f"{os.path.basename(it.path)}: {shown} → {new:%Y-%m-%d %H:%M}"
                if new
                else f"{os.path.basename(it.path)}: {shown} (left as it is)"
            )
        if len(self.items) > 3:
            lines.append("...")
        self.preview.setText("\n".join(lines))
        ok = self.set_to.isChecked() or bool(self.time_shift())
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(ok)

    def edit(self) -> Edit:
        if self.set_to.isChecked():
            return Edit(date=self.when.dateTime().toPython().replace(microsecond=0))
        return Edit(shift=self.time_shift())

    def describe(self) -> str:
        """For Undo: "Set the date of 3 photos" or "Move 3 photos by -3 h"."""
        if self.set_to.isChecked():
            return f"{self.when.dateTime().toPython():%Y-%m-%d %H:%M}"
        parts = [
            f"{v:+d}{u}"
            for v, u in ((self.days.value(), " days"), (self.hours.value(), " h"), (self.minutes.value(), " min"))
            if v
        ]
        return " ".join(parts)


class AllDetailsDialog(QDialog):
    """Every tag in the file, by group, with a box to find one."""

    def __init__(self, name: str, tags: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"All details: {name}")
        self.resize(640, 640)
        self.find = QLineEdit()
        self.find.setPlaceholderText("Find a detail...")
        self.find.setClearButtonEnabled(True)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Detail", "Value"])
        self.tree.setColumnWidth(0, 240)
        self.tree.setUniformRowHeights(True)
        groups = {}
        for key, value in tags.items():
            group, _, tag = key.partition(":")
            if not tag:
                group, tag = "", group
            parent = groups.get(group)
            if parent is None:
                parent = groups[group] = QTreeWidgetItem([group or "File", ""])
                self.tree.addTopLevelItem(parent)
            text = ", ".join(map(str, value)) if isinstance(value, list) else str(value)
            item = QTreeWidgetItem([tag, text if len(text) < 500 else text[:500] + "..."])
            item.setToolTip(1, text[:2000])
            parent.addChild(item)
        self.tree.expandAll()
        self.find.textChanged.connect(self._filter)
        lay = QVBoxLayout(self)
        lay.addWidget(self.find)
        lay.addWidget(self.tree, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.tree.setTextElideMode(Qt.TextElideMode.ElideRight)

    def _filter(self, text):
        text = text.casefold()
        for i in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(i)
            shown = 0
            for j in range(group.childCount()):
                it = group.child(j)
                hide = bool(text) and text not in it.text(0).casefold() and text not in it.text(1).casefold()
                it.setHidden(hide)
                shown += not hide
            group.setHidden(shown == 0)
