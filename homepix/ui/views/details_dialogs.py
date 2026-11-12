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

_NUM = r"[-+]?\d+(?:\.\d+)?"
# one coordinate: degrees (decimal, or with minutes and seconds), and N/S/E/W before or after
_COORD = r"([NSEW])?\s*(" + _NUM + r")\s*°?\s*(?:(\d+(?:\.\d+)?)\s*['′]\s*)?(?:(\d+(?:\.\d+)?)\s*[\"″]\s*)?([NSEW])?"
_PAIR = re.compile(_COORD + r"\s*[,;]?\s*" + _COORD, re.IGNORECASE)
_LINK = [  # where a map link names its place (only there: a house number or a zoom isn't a place)
    re.compile(r"[?&#]mlat=(" + _NUM + r").*?[?&#]mlon=(" + _NUM + r")"),
    re.compile(r"@(" + _NUM + r"),(" + _NUM + r")"),
    re.compile(r"[?&](?:ll|q|query|center|destination|daddr|sll)=(" + _NUM + r")(?:,|%2C)\s*(" + _NUM + r")", re.I),
    re.compile(r"[#&?]map=\d+(?:\.\d+)?/(" + _NUM + r")/(" + _NUM + r")"),
]


def _degrees(sign_before, deg, minutes, seconds, sign_after, letters: str):
    """One coordinate's value, or None if its N/S/E/W is the wrong kind (or given twice)."""
    if sign_before and sign_after:
        return None
    letter = (sign_before or sign_after or "").upper()
    if letter and letter not in letters:
        return None
    value = abs(float(deg)) + float(minutes or 0) / 60 + float(seconds or 0) / 3600
    if (minutes and float(minutes) >= 60) or (seconds and float(seconds) >= 60):
        return None
    negative = deg.startswith("-") or letter in ("S", "W")
    return -value if negative else value


def parse_place(text: str):
    """ "31.778, 35.235", "40.7128° N, 74.0060° W", 31°46'41"N 35°14'06"E, or a map link that names
    the place -> (lat, lon); "" -> (); None if it can't be read for sure (nothing is guessed: the
    place is written into the files)."""
    text = text.strip()
    if not text:
        return ()
    if text.lower().startswith("geo:"):
        text = text[4:].split("?")[0]
    if "/" in text or "=" in text:  # a link
        for pat in _LINK:
            m = pat.search(text)
            if m:
                lat, lon = float(m.group(1)), float(m.group(2))
                break
        else:
            return None
    else:
        m = _PAIR.fullmatch(text)
        if m is None:
            return None
        lat, lon = _degrees(*m.groups()[:5], "NS"), _degrees(*m.groups()[5:], "EW")
        if lat is None or lon is None:
            return None
    return (lat, lon) if -90 <= lat <= 90 and -180 <= lon <= 180 else None


def place_text(place) -> str:
    """How a parsed place reads: "31.77806° N, 35.23500° E"."""
    if place is None:
        return "Can't read a place in that."
    if not place:
        return ""
    lat, lon = place
    return f"{abs(lat):.5f}° {'N' if lat >= 0 else 'S'}, {abs(lon):.5f}° {'E' if lon >= 0 else 'W'}"


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
        self.keywords.textEdited.connect(lambda: self._touched.add("keywords"))
        form.addRow("Keywords:", self.keywords)

        place = _common(d.gps for d in ds)
        self.place = QLineEdit("" if not place else f"{place[0]:.6f}, {place[1]:.6f}")
        self.place.setPlaceholderText("Different in each" if place is None and n > 1 else "e.g. 31.778, 35.235")
        self.place.textEdited.connect(lambda: self._touched.add("place"))
        self.place.textEdited.connect(self._show_place)
        form.addRow("Location:", self.place)
        self.place_reads = QLabel()  # how what's typed is read, as it's typed (it's written into the files)
        self.place_reads.setObjectName("hint")
        form.addRow("", self.place_reads)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        self.error = QLabel()
        self.error.setObjectName("problem")
        lay.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _show_place(self):
        self.place_reads.setText(place_text(parse_place(self.place.text())))

    def accept(self):
        if "place" in self._touched and parse_place(self.place.text()) is None:
            self.error.setText("Type a location like 31.778, 35.235, or paste a map link to the place.")
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
        self.setWindowTitle(f"File info: {name}")
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
