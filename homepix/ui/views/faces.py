"""Faces as tiles: the "who is this?" page (a group of look-alikes to name at a time) and someone's
faces (to take out the ones that aren't them)."""

import os

from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, QTimer, Signal
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

from ...core.people import KNOWN, MAYBE, NEW, by_likeness
from ...core.thumbs import face_thumb
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
    """Face tiles: a click ticks or unticks one (Ctrl+A ticks them all). opened(face): double-click,
    to see its photo."""

    opened = Signal(dict)
    menu_requested = Signal(QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setIconSize(QSize(TILE, TILE))
        self.setGridSize(QSize(TILE + 14, TILE + 14))
        self.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self.setUniformItemSizes(True)
        self.setFrameShape(QListWidget.Shape.NoFrame)
        self.empty_text = ""
        self.setItemDelegate(_Tile(self))
        self.faces = []
        self._loaded = 0  # faces whose pictures were asked for (the first ones; the rest as they're scrolled to)
        self._generation = 0
        self._items, self._asked = {}, set()  # face id -> its tile; ids whose pictures were asked for
        self.itemDoubleClicked.connect(lambda it: self.opened.emit(self.faces[self.row(it)]))

    def fits(self) -> int:
        """How many tiles fit on the page without scrolling."""
        size, view = self.gridSize(), self.viewport().size()
        return max(1, view.width() // size.width()) * max(1, view.height() // size.height())

    def set_faces(self, faces: list[dict], selected: bool = False):
        self.faces = []
        self.clear()  # (says the selection changed: with no faces left)
        self.faces = faces
        self._loaded = min(len(faces), 600)  # (set first: adding items can repaint)
        self._generation += 1
        self._items, self._asked = {}, set()
        blank = self._placeholder_icon()
        for f in faces:
            item = QListWidgetItem(blank, "")
            item.setData(ID_ROLE, f["id"])
            item.setToolTip(os.path.basename(f["path"]))
            self.addItem(item)
            item.setSelected(selected)
            self._items[f["id"]] = item
        self._request_pictures(faces[:600])  # (more as they're scrolled to: see paintEvent)

    def reorder(self, faces: list[dict]):
        """The same faces in another order, keeping their pictures and what's ticked."""
        assert {f["id"] for f in faces} == {f["id"] for f in self.faces}
        ticked = {f["id"] for f in self.selected_faces()}
        self.blockSignals(True)
        while self.count():
            self.takeItem(0)
        for f in faces:
            self.addItem(self._items[f["id"]])
            self._items[f["id"]].setSelected(f["id"] in ticked)
        self.blockSignals(False)
        self.faces = faces
        self._request_pictures(faces[: self._loaded])
        self.scrollToTop()
        self.itemSelectionChanged.emit()

    def _request_pictures(self, faces):
        """Make the pictures of these faces, if they haven't been asked for."""
        gen = self._generation
        for f in faces:
            if f["id"] not in self._asked:
                self._asked.add(f["id"])
                workers.run(
                    face_thumb,
                    lambda file, err, fid=f["id"]: gen == self._generation and self._picture_ready(fid, file, err),
                    f,
                    CROP,
                )

    def _placeholder_icon(self) -> QIcon:
        pm = QPixmap(TILE, TILE)
        pm.fill(self.palette().color(QPalette.ColorRole.AlternateBase))
        return QIcon(pm)

    def _picture_ready(self, fid, file, err):
        item = self._items.get(fid)
        if err is None and item is not None:
            pm = QPixmap(str(file))
            if not pm.isNull():
                item.setIcon(QIcon(pm))

    def selected_faces(self) -> list[dict]:
        return [self.faces[r] for it in self.selectedItems() if (r := self.row(it)) < len(self.faces)]

    def contextMenuEvent(self, e):
        item = self.itemAt(e.pos())
        if item is not None:
            item.setSelected(True)  # (added to those ticked)
        if self.selectedItems():
            self.menu_requested.emit(e.globalPos())

    def paintEvent(self, e):
        super().paintEvent(e)
        if self._loaded < len(self.faces) and self.count():  # the rest, once they come into view
            last = self.indexAt(self.viewport().rect().bottomRight())
            if not last.isValid() or last.row() >= self._loaded - 50:
                start, self._loaded = self._loaded, min(len(self.faces), self._loaded + 600)
                self._request_pictures(self.faces[start : self._loaded])
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
        self.hint = QLabel(
            "The least like the rest come first. Click the faces that aren't them to tick "
            "them (Ctrl+A ticks them all; Most like ticked brings their look-alikes to the front); "
            "double-click a face to see its photo."
        )
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
        self.sort_button = QPushButton("Most like ticked")
        self.sort_button.setToolTip(
            "Put the faces most like the ones ticked first, then the rest, most "
            "alike to least: tick a wrong one to find the others like it"
        )
        self.sort_button.clicked.connect(
            lambda: self.grid.reorder(by_likeness(self.grid.faces, self.grid.selected_faces()))
        )
        photos = QPushButton("Their photos")
        photos.clicked.connect(self.back)
        row = QHBoxLayout()
        for b in (self.not_button, self.else_button, self.hide_button):
            row.addWidget(b)
        row.addSpacing(12)
        row.addWidget(self.sort_button)
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
        n, faces = len(self.grid.selectedItems()), self.grid.faces
        for b in (self.not_button, self.else_button, self.hide_button):
            b.setEnabled(n > 0)
        self.sort_button.setEnabled(0 < n < len(faces) and "emb" in faces[0])


class WhoView(QWidget):
    """One group of unnamed look-alike faces at a time, none ticked to start with: those ticked are
    named (or hidden), so a face is only named once it's been clicked. Right after a group is named,
    the faces that might be that person too come once (a follow-up: "Is this Ann?"), then the
    groups again, the least like anyone named first."""

    named = Signal(list, str)  # face ids, name
    confirmed = Signal(list, list, str)  # a follow-up's faces that are them, those that aren't, name
    hidden = Signal(list)  # face ids: no one to name
    opened = Signal(dict)  # a face: show its photo

    def __init__(self, parent=None):
        super().__init__(parent)
        self.groups = []
        self.skipped = set()  # the faces of groups skipped (until the app closes)
        self.waiting_for = None  # a name just given: its follow-up is being looked for
        self.follow = None  # the follow-up being shown: {"name", "faces"}
        self._naming = None
        self._give_up = QTimer(self, singleShot=True, interval=10_000)  # (no follow-up came: go on)
        self._give_up.timeout.connect(lambda: self._got_follow_up(None) if self.waiting_for else None)
        self.title = QLabel()
        self.title.setObjectName("pageTitle")
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: palette(placeholder-text)")
        self.grid = FaceGrid()
        self.grid.opened.connect(self.opened)
        self.grid.itemSelectionChanged.connect(self._selection_changed)
        self.name = NameBox()
        self.name.returnPressed.connect(self._name_selected)
        self.name.textChanged.connect(self._selection_changed)
        self.name_button = QPushButton("Name them")
        self.name_button.setDefault(True)
        self.name_button.clicked.connect(self._name_selected)
        self.skip_button = QPushButton("Skip")
        self.skip_button.setToolTip("Ask about another group (this one comes back next time)")
        self.skip_button.clicked.connect(self.skip)
        self.hide_button = QPushButton("No one I know")
        self.hide_button.setToolTip(
            "Strangers, or not faces: don't ask about these again (the ones ticked, or the whole group if none are)"
        )
        self.hide_button.clicked.connect(
            lambda: self.hidden.emit([f["id"] for f in self._selected() or self.grid.faces])
        )
        self.sort_button = QPushButton("Most like ticked")
        self.sort_button.setToolTip("Put the faces most like the ones ticked first, then the rest, most alike to least")
        self.sort_button.clicked.connect(self._sort_by_likeness)
        self.rest_button = QPushButton()
        self.rest_button.setToolTip(
            "Name the ticked faces, and remember the others aren't them (they can still be named someone else)"
        )
        self.rest_button.clicked.connect(self._rest_arent_them)
        self.rest_button.hide()
        row = QHBoxLayout()
        row.addWidget(self.name, 1)
        row.addWidget(self.name_button)
        row.addWidget(self.rest_button)
        row.addSpacing(12)
        row.addWidget(self.sort_button)
        row.addWidget(self.skip_button)
        row.addWidget(self.hide_button)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 18, 24, 12)
        lay.addWidget(self.title)
        lay.addWidget(self.hint)
        lay.addLayout(row)
        lay.addWidget(self.grid, 1)
        self._show_group()

    def set_groups(self, groups, names, follow=None):
        """groups: as from core.people.who_groups; names: everyone named (to offer); follow: the
        follow-up for the name just given (core.people.follow_up), if it was asked for. The group
        being looked at stays (with what's ticked) if it's still the same."""
        shown = {f["id"] for f in self.grid.faces}
        self.groups = groups
        self.name.set_names(names)
        if follow is not None and self.waiting_for:
            self._got_follow_up(follow)
            return
        waiting = self._waiting()
        if self.follow or self.waiting_for:
            self._update_text()
        elif not shown or not waiting or {f["id"] for f in waiting[0]} != shown:
            self._show_group()
        else:
            self._update_text()

    def _got_follow_up(self, faces):
        name, self.waiting_for = self.waiting_for, None
        self._give_up.stop()
        self.follow = {"name": name, "faces": faces} if faces else None
        self._show_group()

    def drop(self, face_ids):
        """These faces have been dealt with: take them out of the groups now, without waiting for
        the groups to be worked out again. A group just named is followed up (see follow_up)."""
        gone = set(face_ids)
        self.groups = [kept for g in self.groups if len(kept := [f for f in g if f["id"] not in gone]) >= 2]
        if self._naming and not self.follow:
            self.waiting_for = self._naming
            self._give_up.start()
        self.follow = None
        self.name.clear()
        self._show_group()

    def skip(self):
        if self.follow:
            self.follow = None
        else:
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
            self.skipped.clear()  # every group was skipped: ask about them again
            waiting = self._waiting()
        if self.waiting_for:
            group = []
            self.grid.empty_text = f"Looking for faces that might be {self.waiting_for} too..."
        elif self.follow:
            group = self.follow["faces"]
            self.name.setText(self.follow["name"])
        else:
            group = waiting[0] if waiting else []
            self.grid.empty_text = "Everyone's been named, or there's no one to ask about yet."
            if group and group[0].get("tier") == KNOWN and group[0].get("like") and not self.name.text():
                self.name.setText(group[0]["like"])  # (a guess: only the faces ticked are named)
        self.grid.set_faces(group)
        for w in (self.name, self.name_button, self.skip_button, self.hide_button):
            w.setEnabled(bool(group))
        self.rest_button.setVisible(bool(self.follow))
        if self.follow:
            self.rest_button.setText(f"The rest aren't {self.follow['name']}")
        self._update_text()
        self._selection_changed()
        if group:
            self.name.setFocus()

    def _update_text(self):
        n, group = self.remaining(), self.grid.faces
        if self.follow:
            who = self.follow["name"]
            self.title.setText(f"Is this {who}?")
            self.hint.setText(
                f"{len(group)} faces that might be {who} too, the most alike first. Tick the ones "
                f"that are {who} and press Enter; The rest aren't {who} also says the others "
                "aren't, so they're not asked about as them again. Skip goes on to the groups."
            )
            return
        self.title.setText("Who is this?" if group or self.waiting_for else "No one to ask about")
        if not group:
            self.hint.setText("")
            return
        after = f"{n - 1:,} more group{'s' if n != 2 else ''} after this" if n > 1 else "the last group"
        tier, like = group[0].get("tier", NEW), group[0].get("like")
        seems = {NEW: "Someone new? ", MAYBE: f"Might be {like}. ", KNOWN: f"Looks like {like}. "}.get(tier, "")
        self.hint.setText(
            f"{seems}{len(group)} faces that look alike; {after}. Click the faces of the same person "
            "to tick them (Ctrl+A ticks them all; Most like ticked brings their look-alikes to the "
            "front); double-click to see its photo."
        )

    def _selected(self):
        return self.grid.selected_faces()

    def _selection_changed(self, *_):
        n = len(self._selected())
        self.name_button.setEnabled(n > 0 and bool(self.name.text().strip()))
        self.name_button.setText(f"Name {n} face{'s' if n != 1 else ''}" if n else "Name them")
        self.hide_button.setEnabled(bool(self.grid.faces))
        self.sort_button.setEnabled(n > 0 and n < len(self.grid.faces) and "emb" in self.grid.faces[0])
        self.rest_button.setEnabled(n < len(self.grid.faces) and bool(self.name.text().strip()))

    def _sort_by_likeness(self):
        self.grid.reorder(by_likeness(self.grid.faces, self._selected()))

    def _name_selected(self):
        faces, name = self._selected(), self.name.text().strip()
        if faces and name:
            self._naming = name  # (drop, if it's done, follows it up)
            try:
                self.named.emit([f["id"] for f in faces], name)
            finally:
                self._naming = None

    def _rest_arent_them(self):
        name = self.name.text().strip() or self.follow["name"]
        yes = {f["id"] for f in self._selected()}
        self.confirmed.emit(sorted(yes), [f["id"] for f in self.grid.faces if f["id"] not in yes], name)
