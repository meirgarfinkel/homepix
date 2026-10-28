"""Faces as tiles: the "who is this?" page (a group of look-alikes to name at a time) and someone's
faces (to take out the ones that aren't them)."""

import os

from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPalette, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCompleter,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from ...core import face_thumb
from .. import workers
from .people import CROP

TILE = 112
ID_ROLE = Qt.ItemDataRole.UserRole


class _Tile(QStyledItemDelegate):
    """A face, with a thin border and a tick when it's chosen."""

    def __init__(self, grid):
        super().__init__(grid)
        self.grid = grid

    def sizeHint(self, option, index):
        return QSize(TILE + 14, TILE + 14)

    def paint(self, p, option, index):
        chosen = bool(option.state & QStyle.StateFlag.State_Selected)
        box = QRectF(option.rect).adjusted(7, 7, -7, -7)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        clip = QPainterPath()
        clip.addRoundedRect(box, 8, 8)
        p.setClipPath(clip)
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        if isinstance(icon, QIcon):
            p.drawPixmap(box.toRect(), icon.pixmap(QSize(TILE, TILE)))
        p.setClipping(False)
        highlight = self.grid.palette().color(QPalette.ColorRole.Highlight)
        if chosen:
            p.setPen(QPen(highlight, 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(box.adjusted(1, 1, -1, -1), 7, 7)
            r = 8.5
            c = QPointF(box.right() - r - 5, box.top() + r + 5)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(highlight)
            p.drawEllipse(c, r, r)
            tick = QPen(QColor(255, 255, 255), 1.6)
            tick.setCapStyle(Qt.PenCapStyle.RoundCap)
            tick.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(tick)
            p.drawPolyline([c + QPointF(-3.6, 0.2), c + QPointF(-1.1, 2.8), c + QPointF(3.8, -2.6)])
        if option.state & QStyle.StateFlag.State_HasFocus and self.grid.hasFocus():
            p.setPen(QPen(highlight, 1, Qt.PenStyle.DotLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(box.adjusted(-4, -4, 4, 4), 10, 10)
        p.restore()


class FaceGrid(QListWidget):
    """Face tiles, any number selected. opened(face): double-click, to see its photo."""

    opened = Signal(dict)
    menu_requested = Signal(QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setIconSize(QSize(TILE, TILE))
        self.setGridSize(QSize(TILE + 14, TILE + 14))
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setUniformItemSizes(True)
        self.setFrameShape(QListWidget.Shape.NoFrame)
        self.empty_text = ""
        self.setItemDelegate(_Tile(self))
        self.faces = []
        self._loaded = 0  # faces whose pictures were asked for (the first ones; the rest as they're scrolled to)
        self._generation = 0
        self.itemDoubleClicked.connect(lambda it: self.opened.emit(self.faces[self.row(it)]))

    def set_faces(self, faces: list[dict], selected: bool = False):
        self.faces = []
        self.clear()  # (says the selection changed: with no faces left)
        self.faces = faces
        self._loaded = min(len(faces), 600)  # (set first: adding items can repaint)
        self._generation += 1
        gen = self._generation
        blank = self._blank()
        for f in faces:
            item = QListWidgetItem(blank, "")
            item.setData(ID_ROLE, f["id"])
            item.setToolTip(os.path.basename(f["path"]))
            self.addItem(item)
            item.setSelected(selected)
        for row, f in enumerate(faces[:600]):  # (more load as they're scrolled to: see paintEvent)
            workers.run(face_thumb, lambda file, err, row=row: gen == self._generation and self._ready(row, file, err),
                        f, CROP)

    def _blank(self) -> QIcon:
        pm = QPixmap(TILE, TILE)
        pm.fill(self.palette().color(QPalette.ColorRole.AlternateBase))
        return QIcon(pm)

    def _ready(self, row, file, err):
        item = self.item(row)
        if err is None and item is not None:
            pm = QPixmap(str(file))
            if not pm.isNull():
                item.setIcon(QIcon(pm))

    def selected_faces(self) -> list[dict]:
        return [self.faces[r] for it in self.selectedItems() if (r := self.row(it)) < len(self.faces)]

    def contextMenuEvent(self, e):
        item = self.itemAt(e.pos())
        if item is not None and not item.isSelected():
            self.clearSelection()
            item.setSelected(True)
        if self.selectedItems():
            self.menu_requested.emit(e.globalPos())

    def paintEvent(self, e):
        super().paintEvent(e)
        if self._loaded < len(self.faces) and self.count():  # the rest, once they come into view
            last = self.indexAt(self.viewport().rect().bottomRight())
            if not last.isValid() or last.row() >= self._loaded - 50:
                gen, start, self._loaded = self._generation, self._loaded, min(len(self.faces), self._loaded + 600)
                for row in range(start, self._loaded):
                    workers.run(face_thumb, lambda file, err, row=row: gen == self._generation
                                and self._ready(row, file, err), self.faces[row], CROP)
        if self.count() == 0 and self.empty_text:
            p = QPainter(self.viewport())
            p.setPen(self.palette().color(QPalette.ColorRole.PlaceholderText))
            p.drawText(self.viewport().rect(), Qt.AlignmentFlag.AlignCenter, self.empty_text)
            p.end()


class NameBox(QLineEdit):
    """A name to type, with the names already given offered as you type."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPlaceholderText("Their name")
        self.completer_ = QCompleter([], self)
        self.completer_.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer_.setFilterMode(Qt.MatchFlag.MatchContains)
        self.completer_.popup().setObjectName("popup")  # (styled like the other dropdowns)
        self.setCompleter(self.completer_)

    def set_names(self, names):
        self.completer_.model().setStringList(sorted(names, key=str.casefold))


def ask_name(parent, title: str, text: str, names, name: str = ""):
    """A name typed (with the names given offered), or None."""
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    box = NameBox(dlg)
    box.set_names(names)
    box.setText(name)
    box.selectAll()
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)
    lay = QVBoxLayout(dlg)
    label = QLabel(text)
    label.setWordWrap(True)
    lay.addWidget(label)
    lay.addWidget(box)
    lay.addWidget(buttons)
    dlg.resize(380, dlg.sizeHint().height())
    if not dlg.exec():
        return None
    return box.text().strip() or None


class PersonFaces(QWidget):
    """Everyone's faces that were named or recognized as someone, the least like the rest first:
    where the wrong ones are."""

    not_them = Signal(list)  # face ids
    someone_else = Signal(list)
    hidden = Signal(list)
    opened = Signal(dict)
    back = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.person = None
        self.title = QLabel()
        self.title.setObjectName("pageTitle")
        self.hint = QLabel("The least like the rest come first. Select any that aren't them. "
                           "Double-click a face to see its photo.")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: palette(placeholder-text)")
        self.grid = FaceGrid()
        self.grid.empty_text = "No faces."
        self.grid.opened.connect(self.opened)
        self.grid.itemSelectionChanged.connect(self._selection_changed)
        self.grid.menu_requested.connect(lambda pos: self.menu().exec(pos))
        self.not_button = QPushButton()
        self.not_button.clicked.connect(lambda: self.not_them.emit(self._ids()))
        self.else_button = QPushButton("Someone else...")
        self.else_button.clicked.connect(lambda: self.someone_else.emit(self._ids()))
        self.hide_button = QPushButton("Not a face")
        self.hide_button.clicked.connect(lambda: self.hidden.emit(self._ids()))
        photos = QPushButton("Their photos")
        photos.clicked.connect(self.back)
        row = QHBoxLayout()
        for b in (self.not_button, self.else_button, self.hide_button):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(photos)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 18, 24, 12)
        lay.addWidget(self.title)
        lay.addWidget(self.hint)
        lay.addLayout(row)
        lay.addWidget(self.grid, 1)

    def set_faces(self, person: dict, faces: list[dict]):
        """person: {"id", "name"}."""
        same = self.person is not None and self.person["id"] == person["id"]
        keep = {f["id"] for f in self.grid.selected_faces()} if same else set()
        scroll = self.grid.verticalScrollBar().value() if same else 0
        self.person = person
        n = len(faces)
        self.title.setText(f"{person['name']}'s faces ({n:,})")
        self.not_button.setText(f"Not {person['name']}")
        self.grid.set_faces(faces)
        for row, f in enumerate(faces):
            if f["id"] in keep:
                self.grid.item(row).setSelected(True)
        self.grid.verticalScrollBar().setValue(scroll)
        self._selection_changed()

    def _ids(self):
        return [f["id"] for f in self.grid.selected_faces()]

    def menu(self) -> QMenu:
        m = QMenu(self)
        for b in (self.not_button, self.else_button, self.hide_button):
            m.addAction(b.text(), b.click)
        return m

    def _selection_changed(self):
        on = bool(self.grid.selectedItems())
        for b in (self.not_button, self.else_button, self.hide_button):
            b.setEnabled(on)


class WhoView(QWidget):
    """One group of unnamed look-alike faces at a time, none ticked to start with: those ticked are
    named (or hidden), so a face is only named once it's been clicked."""

    named = Signal(list, str)  # face ids, name
    hidden = Signal(list)  # face ids: no one to name
    opened = Signal(dict)  # a face: show its photo

    def __init__(self, parent=None):
        super().__init__(parent)
        self.groups = []
        self.skipped = set()  # the faces of groups skipped (until the app closes)
        self.title = QLabel()
        self.title.setObjectName("pageTitle")
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: palette(placeholder-text)")
        self.grid = FaceGrid()
        self.grid.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)  # a click ticks or unticks
        self.grid.opened.connect(self.opened)
        self.grid.itemSelectionChanged.connect(self._selection_changed)
        self.name = NameBox()
        self.name.returnPressed.connect(self._name)
        self.name.textChanged.connect(self._selection_changed)
        self.name_button = QPushButton("Name them")
        self.name_button.setDefault(True)
        self.name_button.clicked.connect(self._name)
        self.skip_button = QPushButton("Skip")
        self.skip_button.setToolTip("Ask about another group (this one comes back next time)")
        self.skip_button.clicked.connect(self.skip)
        self.hide_button = QPushButton("No one I know")
        self.hide_button.setToolTip("Strangers, or not faces: don't ask about these again (the ones ticked, or "
                                    "the whole group if none are)")
        self.hide_button.clicked.connect(
            lambda: self.hidden.emit([f["id"] for f in self._chosen() or self.grid.faces]))
        row = QHBoxLayout()
        row.addWidget(self.name, 1)
        row.addWidget(self.name_button)
        row.addSpacing(12)
        row.addWidget(self.skip_button)
        row.addWidget(self.hide_button)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 18, 24, 12)
        lay.addWidget(self.title)
        lay.addWidget(self.hint)
        lay.addLayout(row)
        lay.addWidget(self.grid, 1)
        self._show_group()

    def set_groups(self, groups, names):
        """groups: as from core.people.who_groups; names: everyone named (to offer). The group being
        looked at stays (with what's ticked) if it's still the same."""
        shown = {f["id"] for f in self.grid.faces}
        self.groups = groups
        self.name.set_names(names)
        waiting = self._waiting()
        if not shown or not waiting or {f["id"] for f in waiting[0]} != shown:
            self._show_group()
        else:
            self._update_text()

    def drop(self, face_ids):
        """These faces have been dealt with: take them out of the groups now, without waiting for
        the groups to be worked out again."""
        gone = set(face_ids)
        self.groups = [kept for g in self.groups if len(kept := [f for f in g if f["id"] not in gone]) >= 2]
        self.name.clear()
        self._show_group()

    def skip(self):
        self.skipped.update(f["id"] for f in self.grid.faces)
        self.name.clear()
        self._show_group()

    def _waiting(self):
        """The groups still to ask about, in order. (A group is skipped if its clearest face was in
        one skipped, so it stays skipped when the groups are worked out again.)"""
        return [g for g in self.groups if g[0]["id"] not in self.skipped]

    def remaining(self) -> int:
        return len(self._waiting())

    def _show_group(self):
        waiting = self._waiting()
        if not waiting and self.skipped and self.groups:
            self.skipped.clear()  # round again
            waiting = self._waiting()
        group = waiting[0] if waiting else []
        self.grid.empty_text = "Everyone's been named, or there's no one to ask about yet."
        self.grid.set_faces(group)
        for w in (self.name, self.name_button, self.skip_button, self.hide_button):
            w.setEnabled(bool(group))
        self._update_text()
        self._selection_changed()
        if group:
            self.name.setFocus()

    def _update_text(self):
        n, group = self.remaining(), self.grid.faces
        self.title.setText("Who is this?" if group else "No one to ask about")
        after = f"{n - 1:,} more group{'s' if n != 2 else ''} after this" if n > 1 else "the last group"
        self.hint.setText(f"{len(group)} faces that look alike; {after}. Click the faces of the same person to "
                          "tick them (Ctrl+A ticks them all); double-click to see its photo."
                          if group else "")

    def _chosen(self):
        return self.grid.selected_faces()

    def _selection_changed(self, *_):
        n = len(self._chosen())
        self.name_button.setEnabled(n > 0 and bool(self.name.text().strip()))
        self.name_button.setText(f"Name {n} face{'s' if n != 1 else ''}" if n else "Name them")
        self.hide_button.setEnabled(bool(self.grid.faces))

    def _name(self):
        faces, name = self._chosen(), self.name.text().strip()
        if faces and name:
            self.named.emit([f["id"] for f in faces], name)
