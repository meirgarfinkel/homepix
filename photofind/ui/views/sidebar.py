"""The sidebar: all photos, and the library's real folder tree with how many photos each holds."""

import os
from pathlib import Path

import shiboken6
from PySide6.QtCore import QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QHeaderView, QStyledItemDelegate, QTreeWidget, QTreeWidgetItem

from .. import theme

ALL = "all"
PEOPLE = "people"
WHO = "who"  # unnamed faces to name
DUPES = "duplicates"  # the duplicates review


def person_place(person_id: int) -> str:
    return f"person:{person_id}"


def person_of(place: str):
    """The person id in a person_place, or None."""
    return int(place[7:]) if place.startswith("person:") else None


def faces_place(person_id: int) -> str:
    """Someone's faces (rather than their photos)."""
    return f"faces:{person_id}"


def faces_of(place: str):
    return int(place[6:]) if place.startswith("faces:") else None


PATH_ROLE = Qt.ItemDataRole.UserRole


def folder_tree(roots: list[str], counts: dict) -> dict:
    """{root: node}, node = {"path", "name", "total" (photos in it and below), "children": {name: node}}.
    counts: {folder: photos directly in it}. Folders without photos (anywhere below) are left out,
    except the roots themselves."""
    tree = {}
    for root in roots:
        node = {"path": root, "name": Path(root).name or root, "total": 0, "children": {}}
        prefix = root.rstrip(os.sep) + os.sep
        for folder, n in counts.items():
            if folder == root:
                node["total"] += n
                continue
            if not folder.startswith(prefix):
                continue
            node["total"] += n
            cur = node
            for part in folder[len(prefix):].split(os.sep):
                child = cur["children"].get(part)
                if child is None:
                    child = cur["children"][part] = {"path": os.path.join(cur["path"], part), "name": part,
                                                     "total": 0, "children": {}}
                child["total"] += n
                cur = child
        tree[root] = node
    return tree


class CountDelegate(QStyledItemDelegate):
    """The counts column: muted numbers, and the "who is this?" count as an accent badge."""

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        option.text = ""  # drawn below instead, so it isn't drawn twice

    def sizeHint(self, option, index):
        """Wide enough for the number (the drawn one, not the blanked text)."""
        size = super().sizeHint(option, index)
        text = index.data() or ""
        if index.siblingAtColumn(0).data(PATH_ROLE) == WHO:
            width = QFontMetrics(theme.font(11, QFont.Weight.Bold)).horizontalAdvance(text) + 14
        else:
            width = QFontMetrics(option.font).horizontalAdvance(text)
        size.setWidth(max(size.width(), width + 16))
        return size

    def paint(self, p, option, index):
        text = index.data()
        super().paint(p, option, index)  # the row's background
        if not text:
            return
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect.adjusted(0, 0, -12, 0)
        if index.siblingAtColumn(0).data(PATH_ROLE) == WHO:
            p.setFont(theme.font(11, QFont.Weight.Bold))
            w = p.fontMetrics().horizontalAdvance(text) + 14
            badge = QRectF(rect.right() - w, rect.center().y() - 10, w, 20)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.color("accent"))
            p.drawRoundedRect(badge, 10, 10)
            p.setPen(theme.color("accentInk"))
            p.drawText(badge, Qt.AlignmentFlag.AlignCenter, text)
        else:
            p.setPen(theme.color("muted"))
            p.drawText(rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, text)
        p.restore()


class Sidebar(QTreeWidget):
    chosen = Signal(str)  # ALL, PEOPLE, WHO, DUPES, a person_place, or a folder's path
    menu_requested = Signal(str, QPoint)  # right-click on a folder or person: its place, and where (global)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setColumnCount(2)
        self.setItemDelegateForColumn(1, CountDelegate(self))
        self.setFrameShape(QTreeWidget.Shape.NoFrame)
        self.setHeaderHidden(True)
        self.header().setStretchLastSection(False)
        self.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.setIndentation(16)
        self.setUniformRowHeights(True)
        self.setMinimumWidth(220)
        self.itemSelectionChanged.connect(self._picked)
        self.currentItemChanged.connect(self._embolden)
        self._quiet = False
        self.set_folders([], {}, 0)

    def set_folders(self, roots: list[str], counts: dict, total: int, people=(), who=None, dupes=None):
        """Rebuild, keeping what was open and what was chosen. people: as from core.named_people;
        who: None if there are no unnamed faces, else how many groups of them to ask about ("" if
        not known yet); dupes: groups of duplicates to review ("" if none or not known yet), None to
        leave the review out."""
        opened = {it.data(0, PATH_ROLE) for it in self._all_items() if it.isExpanded()}
        had_people = any(it.data(0, PATH_ROLE) == PEOPLE for it in self._all_items())
        chosen = self.current_path()
        first = not opened and self.topLevelItemCount() == 0
        self._quiet = True
        self.clear()
        everything = QTreeWidgetItem(["All photos", f"{total:,}"])
        everything.setData(0, PATH_ROLE, ALL)
        self._style_count(everything)
        self.addTopLevelItem(everything)
        if people:
            group = QTreeWidgetItem(["People", f"{len(people):,}"])
            group.setData(0, PATH_ROLE, PEOPLE)
            self._style_count(group)
            self.addTopLevelItem(group)
            for person in people:
                item = QTreeWidgetItem([person["name"], f"{person['photos']:,}"])
                item.setData(0, PATH_ROLE, person_place(person["id"]))
                self._style_count(item)
                group.addChild(item)
            if not had_people:  # new: open, to show who's there
                opened.add(PEOPLE)
        if who is not None:
            ask = QTreeWidgetItem(["Who is this?", f"{who:,}" if isinstance(who, int) else ""])
            ask.setData(0, PATH_ROLE, WHO)
            ask.setToolTip(0, "Name the people in your photos")
            self._style_count(ask)
            self.addTopLevelItem(ask)
        if dupes is not None:
            review = QTreeWidgetItem(["Duplicates", f"{dupes:,}" if isinstance(dupes, int) and dupes else ""])
            review.setData(0, PATH_ROLE, DUPES)
            review.setToolTip(0, "Copies of the same photo or video, to review")
            self._style_count(review)
            self.addTopLevelItem(review)
        heading = QTreeWidgetItem(["FOLDERS", ""])
        heading.setFlags(Qt.ItemFlag.ItemIsEnabled)
        font = theme.font(11, QFont.Weight.Bold)
        font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 108)
        heading.setFont(0, font)
        heading.setForeground(0, theme.color("muted"))
        self.addTopLevelItem(heading)
        heading.setExpanded(True)
        for root, node in folder_tree(roots, counts).items():
            item = self._add(heading, node)
            item.setToolTip(0, root)
            if not Path(root).is_dir():
                item.setText(0, f"{node['name']} (not connected)")
                item.setToolTip(0, f"{root}\nCan't be reached right now: is its drive plugged in? "
                                   "Its photos are still shown.")
                italic = QFont(self.font())
                italic.setItalic(True)
                item.setFont(0, italic)
            if first:
                item.setExpanded(True)
        for it in self._all_items():
            if it.data(0, PATH_ROLE) in opened:
                it.setExpanded(True)
        self.select(chosen or ALL)
        self._quiet = False

    def _add(self, parent, node) -> QTreeWidgetItem:
        item = QTreeWidgetItem([node["name"], f"{node['total']:,}" if node["total"] else ""])
        item.setData(0, PATH_ROLE, node["path"])
        self._style_count(item)
        parent.addChild(item)
        for name in sorted(node["children"], key=str.lower):
            self._add(item, node["children"][name])
        return item

    @staticmethod
    def _style_count(item):
        item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    @staticmethod
    def _embolden(now, before):
        """The chosen place in bold."""
        for item, weight in ((before, QFont.Weight.Normal), (now, QFont.Weight.Bold)):
            if item is not None and shiboken6.isValid(item) and item.flags() & Qt.ItemFlag.ItemIsSelectable:
                f = QFont(item.font(0))
                f.setWeight(weight)
                item.setFont(0, f)

    def _all_items(self):
        stack = [self.topLevelItem(i) for i in range(self.topLevelItemCount())]
        while stack:
            it = stack.pop()
            yield it
            stack.extend(it.child(i) for i in range(it.childCount()))

    def current_path(self):
        items = self.selectedItems()
        return items[0].data(0, PATH_ROLE) if items else None

    def set_count(self, place: str, text: str):
        for it in self._all_items():
            if it.data(0, PATH_ROLE) == place:
                it.setText(1, text)

    def select(self, place: str) -> bool:
        """Choose a place without saying so (no chosen signal). False if it isn't here (All photos
        is chosen instead)."""
        if faces_of(place) is not None:  # their faces: still them
            place = person_place(faces_of(place))
        was, self._quiet = self._quiet, True
        for it in self._all_items():
            if it.data(0, PATH_ROLE) == place:
                self.setCurrentItem(it)
                found = True
                break
        else:
            self.setCurrentItem(self.topLevelItem(0))
            found = place == ALL
        self._quiet = was
        return found

    def contextMenuEvent(self, e):
        item = self.itemAt(self.viewport().mapFromGlobal(e.globalPos()))
        path = item.data(0, PATH_ROLE) if item else None
        if path and (os.path.isabs(path) or person_of(path) is not None):
            self.menu_requested.emit(path, e.globalPos())

    def _picked(self):
        path = self.current_path()
        if path and not self._quiet:
            self.chosen.emit(path)
