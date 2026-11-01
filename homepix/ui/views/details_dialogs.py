"""Dialogs for the details written in photos: editing them (one photo or many), the date and time,
and every tag a file holds."""

import datetime as dt
import os
import re

from PySide6.QtCore import QDate, Qt, QTime
from PySide6.QtGui import QTextCharFormat
from PySide6.QtWidgets import (
    QCalendarWidget,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QTimeEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from ...core import dates
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
    """Set the date and the time. With several photos, only what's changed is set: change just the
    date and each keeps its own time (and the other way round)."""

    def __init__(self, items, parent=None):
        super().__init__(parent)
        n = len(items)
        self.setWindowTitle("Date and time" if n == 1 else f"Date and time of {n:,} photos")
        self.setMinimumWidth(360)
        self.items = items
        first = self._taken(items[0]) or dt.datetime.now().replace(second=0, microsecond=0)
        self.date = QDateEdit(QDate(first.year, first.month, first.day))
        self.date.setDisplayFormat("d MMMM yyyy")
        self.date.setCalendarPopup(True)
        cal = self.date.calendarWidget()
        cal.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        plain = QTextCharFormat()
        plain.setForeground(cal.palette().text())
        for day in (Qt.DayOfWeek.Saturday, Qt.DayOfWeek.Sunday):
            cal.setWeekdayTextFormat(day, plain)  # weekends in the same colour as other days
        self.time = QTimeEdit(QTime(first.hour, first.minute))
        self.time.setDisplayFormat("HH:mm")
        self.changed = set()  # "date", "time": what was changed
        self.date.dateChanged.connect(lambda _: self._changed("date"))
        self.time.timeChanged.connect(lambda _: self._changed("time"))
        form = QGridLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)
        for row, (label, field) in enumerate((("Date", self.date), ("Time", self.time))):
            form.addWidget(QLabel(label), row, 0, Qt.AlignmentFlag.AlignVCenter)
            form.addWidget(field, row, 1)
        form.setColumnStretch(1, 1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(14)
        lay.addLayout(form)
        self.note = QLabel()
        self.note.setObjectName("hint")
        self.note.setWordWrap(True)
        lay.addWidget(self.note)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        self._update()

    @staticmethod
    def _taken(item):
        try:
            return None if item.taken.startswith("0000") else dt.datetime.fromisoformat(item.taken)
        except ValueError:
            return None

    def _changed(self, what):
        self.changed.add(what)
        self._update()

    def _update(self):
        n = len(self.items)
        if n > 1:
            self.note.setText("Only what you change is set. The rest stays as each photo has it.")
        elif any(dates.ORGANIZED.match(os.path.splitext(os.path.basename(it.path))[0]) for it in self.items):
            self.note.setText("Its name follows the date, so it's renamed to match.")
        self.note.setVisible(bool(self.note.text()))
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(bool(self.changed))

    def new_dates(self) -> list:
        """[(item, its new date and time)] for the photos this changes."""
        day = self.date.date().toPython()
        clock = self.time.time().toPython()
        out = []
        for it in self.items:
            old = self._taken(it)
            d = day if "date" in self.changed or old is None else old.date()
            keep_time = "time" not in self.changed and old is not None
            t = old.time().replace(microsecond=0) if keep_time else clock.replace(second=0)
            new = dt.datetime.combine(d, t)
            if new != old:
                out.append((it, new))
        return out

    def describe(self) -> str:
        """For Undo: "2024-07-01 09:30", or for several what was set."""
        day, clock = self.date.date().toPython(), self.time.time().toPython()
        if len(self.items) == 1 or self.changed == {"date", "time"}:
            return f"{day:%Y-%m-%d} {clock:%H:%M}"
        return f"{day:%Y-%m-%d}" if "date" in self.changed else f"{clock:%H:%M}"


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
