"""The sidebar: all photos, and the library's real folder tree with how many photos each holds."""

import os
from pathlib import Path

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QFont, QPalette
from PySide6.QtWidgets import QHeaderView, QTreeWidget, QTreeWidgetItem

ALL = "all"
PEOPLE = "people"
WHO = "who"  # unnamed faces to name


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


class Sidebar(QTreeWidget):
    chosen = Signal(str)  # ALL, PEOPLE, WHO, a person_place, or a folder's path
    menu_requested = Signal(str, QPoint)  # right-click on a folder or person: its place, and where (global)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(2)
        self.setHeaderHidden(True)
        self.header().setStretchLastSection(False)
        self.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.setIndentation(14)
        self.setUniformRowHeights(True)
        self.setMinimumWidth(200)
        self.itemSelectionChanged.connect(self._picked)
        self._quiet = False
        self.set_folders([], {}, 0)

    def set_folders(self, roots: list[str], counts: dict, total: int, people=(), who=None):
        """Rebuild, keeping what was open and what was chosen. people: as from core.named_people;
        who: None if there are no unnamed faces, else how many groups of them to ask about ("" if
        not known yet)."""
        opened = {it.data(0, PATH_ROLE) for it in self._all_items() if it.isExpanded()}
        had_people = any(it.data(0, PATH_ROLE) == PEOPLE for it in self._all_items())
        chosen = self.current_path()
        first = not opened and self.topLevelItemCount() == 0
        self._quiet = True
        self.clear()
        everything = QTreeWidgetItem([" All photos", f"{total:,}"])
        everything.setData(0, PATH_ROLE, ALL)
        self._style_count(everything)
        self.addTopLevelItem(everything)
        if people:
            group = QTreeWidgetItem([" People", f"{len(people):,}"])
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
            ask = QTreeWidgetItem([" Who is this?", f"{who:,}" if isinstance(who, int) else ""])
            ask.setData(0, PATH_ROLE, WHO)
            ask.setToolTip(0, "Name the people in your photos")
            self._style_count(ask)
            self.addTopLevelItem(ask)
        heading = QTreeWidgetItem(["Folders", ""])
        heading.setFlags(Qt.ItemFlag.ItemIsEnabled)
        font = QFont(self.font())
        font.setWeight(QFont.Weight.DemiBold)
        heading.setFont(0, font)
        heading.setForeground(0, self.palette().color(QPalette.ColorRole.PlaceholderText))
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

    def _style_count(self, item):
        item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        item.setForeground(1, self.palette().color(QPalette.ColorRole.PlaceholderText))

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
