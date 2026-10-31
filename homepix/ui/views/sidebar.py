"""The sidebar: all photos, and the library's real folder tree with how many photos each folder
inside it holds."""

import os
from pathlib import Path

import shiboken6
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QHeaderView, QStyledItemDelegate, QTreeWidget, QTreeWidgetItem

from ...core.util import folder_prefix
from .. import theme

ALL = "all"
PEOPLE = "people"
WHO = "who"  # unnamed faces to name
DUPES = "duplicates"  # the duplicates review
JUNK = "junk"  # photos that may be junk


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
        prefix = folder_prefix(root)
        for folder, n in counts.items():
            if folder == root:
                node["total"] += n
                continue
            if not folder.startswith(prefix):
                continue
            node["total"] += n
            cur = node
            for part in folder[len(prefix) :].split(os.sep):
                child = cur["children"].get(part)
                if child is None:
                    child = cur["children"][part] = {
                        "path": os.path.join(cur["path"], part),
                        "name": part,
                        "total": 0,
                        "children": {},
                    }
                child["total"] += n
                cur = child
        tree[root] = node
    return tree


CHEVRON = 22  # room at the right of every row for the open/close arrow (so counts line up)
RIGHT = 10  # space after it


class CountDelegate(QStyledItemDelegate):
    """The counts column: the muted number, then, for a row with folders inside, the arrow that
    opens and closes it."""

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        option.text = ""  # drawn below instead, so it isn't drawn twice

    def sizeHint(self, option, index):
        """Wide enough for the number (the drawn one, not the blanked text) and the arrow."""
        size = super().sizeHint(option, index)
        width = QFontMetrics(option.font).horizontalAdvance(index.data() or "")
        size.setWidth(max(size.width(), width + 8 + CHEVRON + RIGHT))
        return size

    def paint(self, p, option, index):
        text = index.data()
        super().paint(p, option, index)  # the row's background
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        muted = theme.color("muted")
        arrow = QRectF(option.rect.right() - RIGHT - CHEVRON, option.rect.top(), CHEVRON, option.rect.height())
        if text:
            p.setPen(muted)
            p.drawText(
                option.rect.adjusted(0, 0, -RIGHT - CHEVRON, 0),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                text,
            )
        first = index.siblingAtColumn(0)
        if index.model().hasChildren(first):
            tree = self.parent()
            if tree.hover_arrow is not None and tree.indexFromItem(tree.hover_arrow) == first:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(theme.color("hoverStrong"))
                p.drawEllipse(arrow.center(), 12, 12)
            pen = QPen(theme.color("text"), 1.6)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            c, w = arrow.center(), 4.0
            h = -2.0 if tree.isExpanded(first) else 2.0  # ▴ open, ▾ closed
            path = QPainterPath(QPointF(c.x() - w, c.y() - h))
            path.lineTo(c.x(), c.y() + h)
            path.lineTo(c.x() + w, c.y() - h)
            p.drawPath(path)
        p.restore()


class Sidebar(QTreeWidget):
    chosen = Signal(str)  # ALL, PEOPLE, WHO, DUPES, JUNK, a person_place, or a folder's path
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
        self.setRootIsDecorated(False)  # no arrows at the left: they're at the right (CountDelegate)
        self.setExpandsOnDoubleClick(False)  # (double-click chooses it, like a click)
        self.setMouseTracking(True)  # (the hand over an arrow)
        self.hover_arrow = None  # the row whose arrow the mouse is over
        self.setUniformRowHeights(True)
        self.setMinimumWidth(220)
        self.itemSelectionChanged.connect(self._on_pick)
        self.currentItemChanged.connect(self._embolden)
        self._quiet = False
        self.set_folders([], {}, 0)

    def set_folders(self, roots: list[str], counts: dict, total: int, people=None, dupes=None, excluded=()):
        """Rebuild, keeping what was open and what was chosen; everything starts closed. people: how
        many are named, or None to leave People out (no one named and no faces to ask about);
        dupes: groups of duplicates to review ("" if none or not known yet), None to leave the
        review out. excluded: folders left out of the library's views (shown dimmed)."""
        opened = {it.data(0, PATH_ROLE) for it in self._all_items() if it.isExpanded()}
        chosen = self.current_path()
        self._quiet = True
        self.hover_arrow = None
        self.clear()
        everything = QTreeWidgetItem(["All photos", f"{total:,}"])
        everything.setData(0, PATH_ROLE, ALL)
        self._style_count(everything)
        self.addTopLevelItem(everything)
        if people is not None:
            group = QTreeWidgetItem(["People", f"{people:,}" if people else ""])
            group.setData(0, PATH_ROLE, PEOPLE)
            group.setToolTip(0, "Everyone you've named, and Who is this? to name more")
            self._style_count(group)
            self.addTopLevelItem(group)
        if dupes is not None:
            review = QTreeWidgetItem(["Duplicates", f"{dupes:,}" if isinstance(dupes, int) and dupes else ""])
            review.setData(0, PATH_ROLE, DUPES)
            review.setToolTip(0, "Copies of the same photo or video, to review")
            self._style_count(review)
            self.addTopLevelItem(review)
            junk = QTreeWidgetItem(["Possibly junk", ""])
            junk.setData(0, PATH_ROLE, JUNK)
            junk.setToolTip(0, "Ads, screenshots, documents and accidental shots, to clear out")
            self._style_count(junk)
            self.addTopLevelItem(junk)
        heading = QTreeWidgetItem(["FOLDERS", ""])
        heading.setFlags(Qt.ItemFlag.NoItemFlags)  # a label, not a place
        font = theme.font(11, QFont.Weight.Bold)
        font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 108)
        heading.setFont(0, font)
        heading.setForeground(0, theme.color("muted"))
        self.addTopLevelItem(heading)
        for root, node in folder_tree(roots, counts).items():
            item = self._add_node(None, node)
            item.setText(1, "")  # its count is All photos' again: just the name and the arrow
            item.setToolTip(0, root)
            if not Path(root).is_dir():
                item.setText(0, f"{node['name']} (not connected)")
                item.setToolTip(
                    0, f"{root}\nCan't be reached right now: is its drive plugged in? Its photos are still shown."
                )
                italic = QFont(self.font())
                italic.setItalic(True)
                item.setFont(0, italic)
        for it in self._all_items():
            path = it.data(0, PATH_ROLE)
            if path in opened:
                it.setExpanded(True)
            if (
                path
                and os.path.isabs(path)
                and any(path == f or path.startswith(os.path.join(f, "")) for f in excluded)
            ):
                it.setForeground(0, theme.color("muted"))  # (and the folders inside it)
            if path in excluded:
                it.setText(0, f"{it.text(0)} (excluded)")
                it.setToolTip(0, f"{path}\nLeft out of All photos, people and search. Right-click to include it again.")
        self.select(chosen or ALL)
        self._quiet = False

    def _add_node(self, parent, node) -> QTreeWidgetItem:
        item = QTreeWidgetItem([node["name"], f"{node['total']:,}" if node["total"] else ""])
        item.setData(0, PATH_ROLE, node["path"])
        self._style_count(item)
        if parent is None:
            self.addTopLevelItem(item)
        else:
            parent.addChild(item)
        for name in sorted(node["children"], key=str.lower):
            self._add_node(item, node["children"][name])
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
        if place == WHO or person_of(place) is not None or faces_of(place) is not None:
            place = PEOPLE  # (people aren't listed one by one)
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

    def _arrow_hit(self, pos) -> QTreeWidgetItem | None:
        """The row whose open/close arrow is at pos, if any."""
        item = self.itemAt(pos)
        if item is None or item.childCount() == 0:
            return None
        return item if pos.x() >= self.viewport().width() - RIGHT - CHEVRON - 6 else None

    def mousePressEvent(self, e):
        pos = e.position().toPoint()
        item = self._arrow_hit(pos)
        if item is not None and e.button() == Qt.MouseButton.LeftButton:
            item.setExpanded(not item.isExpanded())
            return
        under = self.itemAt(pos)
        again = under is not None and under.isSelected() and e.button() == Qt.MouseButton.LeftButton
        super().mousePressEvent(e)
        if again and not self._quiet:  # already chosen: go back to it (e.g. People, from someone's photos)
            self.chosen.emit(under.data(0, PATH_ROLE))

    def mouseMoveEvent(self, e):
        hit = self._arrow_hit(e.position().toPoint())
        self.viewport().setCursor(Qt.CursorShape.PointingHandCursor if hit else Qt.CursorShape.ArrowCursor)
        if hit is not self.hover_arrow:
            self.hover_arrow = hit
            self.viewport().update()
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        if self.hover_arrow is not None:
            self.hover_arrow = None
            self.viewport().update()
        super().leaveEvent(e)

    def contextMenuEvent(self, e):
        item = self.itemAt(self.viewport().mapFromGlobal(e.globalPos()))
        path = item.data(0, PATH_ROLE) if item else None
        if path and (os.path.isabs(path) or person_of(path) is not None):
            self.menu_requested.emit(path, e.globalPos())

    def _on_pick(self):
        path = self.current_path()
        if path and not self._quiet:
            self.chosen.emit(path)
