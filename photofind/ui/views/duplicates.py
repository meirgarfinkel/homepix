"""The duplicates review: each group of copies side by side, the one to keep marked. Click another to
keep it instead; set the extras aside (into Duplicates/, or the Trash) or say they aren't duplicates,
for the groups selected, or all of them. Nothing happens without a preview first."""

from PySide6.QtCore import QAbstractListModel, QModelIndex, QRect, QSize, Qt, Signal
from PySide6.QtGui import QFont, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QListView,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from ...core.info import human_size
from ..thumbs import ThumbLoader
from .changes_dialog import relative

TILE = 150
GAP = 14
PAD = 12
_ROOT = QModelIndex()


class _Groups(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.groups = []

    def rowCount(self, parent=_ROOT):
        return 0 if parent.isValid() else len(self.groups)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.ToolTipRole:
            g = self.groups[index.row()]
            return ("The same file" if g.how == "exact" else "They look the same") + ". Click a copy to keep it instead."
        return None

    def set_groups(self, groups):
        self.beginResetModel()
        self.groups = groups
        self.endResetModel()


class _Row(QStyledItemDelegate):
    """A group: its copies' thumbnails in a row, each with its size and where it is."""

    def __init__(self, view):
        super().__init__(view)
        self.view = view

    def lines(self) -> int:
        return self.view.fontMetrics().height()

    def sizeHint(self, option, index):
        return QSize(0, PAD * 2 + TILE + 6 + self.lines() * 3)

    def tile_rect(self, rect: QRect, i: int) -> QRect:
        return QRect(rect.left() + PAD + i * (TILE + GAP), rect.top() + PAD, TILE, TILE)

    def tile_at(self, rect: QRect, pos, n: int) -> int | None:
        for i in range(n):
            r = self.tile_rect(rect, i)
            if r.left() <= pos.x() <= r.right() and r.top() <= pos.y() <= r.bottom() + self.lines() * 3:
                return i
        return None

    def paint(self, p: QPainter, option, index):
        g = self.view.model().groups[index.row()]
        pal = option.palette
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if option.state & QStyle.StateFlag.State_Selected:
            p.fillRect(option.rect, pal.color(QPalette.ColorRole.Highlight).lighter(185)
                       if pal.color(QPalette.ColorRole.Window).lightness() > 128
                       else pal.color(QPalette.ColorRole.Highlight).darker(250))
        p.setPen(QPen(pal.color(QPalette.ColorRole.Mid)))
        p.drawLine(option.rect.bottomLeft(), option.rect.bottomRight())
        accent = pal.color(QPalette.ColorRole.Highlight)
        fm, lh = self.view.fontMetrics(), self.lines()
        bold = QFont(self.view.font())
        bold.setWeight(QFont.Weight.DemiBold)
        for i, c in enumerate(g.copies):
            r = self.tile_rect(option.rect, i)
            if r.left() > option.rect.right():
                break
            img = self.view.loader.get(c.qhash)
            if img is not None:
                p.drawImage(r, img)
            else:
                p.fillRect(r, pal.color(QPalette.ColorRole.AlternateBase))
            keep = i == 0
            if keep:
                p.setPen(QPen(accent, 3))
                p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 4, 4)
            y = r.bottom() + 6
            text = QRect(r.left(), y, TILE, lh)
            p.setPen(accent if keep else pal.color(QPalette.ColorRole.PlaceholderText))
            p.setFont(bold)
            p.drawText(text, Qt.AlignmentFlag.AlignLeft, "Keep" if keep else
                       ("Same file" if g.how == "exact" else "Copy"))
            p.setFont(self.view.font())
            p.setPen(pal.color(QPalette.ColorRole.Text))
            size = f"{c.width} × {c.height} · " if c.width else ""
            p.drawText(text.translated(0, lh), Qt.AlignmentFlag.AlignLeft,
                       fm.elidedText(size + human_size(c.size), Qt.TextElideMode.ElideRight, TILE))
            p.setPen(pal.color(QPalette.ColorRole.PlaceholderText))
            p.drawText(text.translated(0, 2 * lh), Qt.AlignmentFlag.AlignLeft,
                       fm.elidedText(relative(c.path, self.view.library), Qt.TextElideMode.ElideLeft, TILE))
        p.restore()


class _List(QListView):
    keep = Signal(int, int)  # row, copy
    open_copy = Signal(int, int)

    def __init__(self, loader, parent=None):
        super().__init__(parent)
        self.loader = loader
        self.library = []
        self.setModel(_Groups(self))
        self.setItemDelegate(_Row(self))
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setUniformItemSizes(True)
        loader.ready.connect(lambda _q: self.viewport().update())

    def _hit(self, pos):
        index = self.indexAt(pos)
        if not index.isValid():
            return None, None
        n = len(self.model().groups[index.row()].copies)
        return index.row(), self.itemDelegate().tile_at(self.visualRect(index), pos, n)

    def mousePressEvent(self, e):
        row, i = self._hit(e.position().toPoint())
        if row is not None and i is not None and e.button() == Qt.MouseButton.LeftButton \
                and not e.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier):
            self.keep.emit(row, i)
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e):
        row, i = self._hit(e.position().toPoint())
        if row is not None and i is not None:
            self.open_copy.emit(row, i)

    def paintEvent(self, e):
        super().paintEvent(e)
        groups = self.model().groups
        first = self.indexAt(self.viewport().rect().topLeft())
        start = first.row() if first.isValid() else 0
        rows = range(start, min(len(groups), start + self.viewport().height() // max(1, self.sizeHintForRow(0)) + 2))
        self.loader.want([(c.qhash, c.path) for r in rows for c in groups[r].copies])
        if not groups:
            p = QPainter(self.viewport())
            p.setPen(self.palette().color(QPalette.ColorRole.PlaceholderText))
            p.drawText(self.viewport().rect(), Qt.AlignmentFlag.AlignCenter, self.empty_text)

    empty_text = "Looking for duplicates..."


class DuplicatesView(QWidget):
    """set_aside(groups, to): to is "duplicates" or "trash"; apart(groups); opened(photo id)."""

    set_aside = Signal(list, str)
    apart = Signal(list)
    opened = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.loader = ThumbLoader(workers=2, budget=48 * 1024 * 1024)
        self.groups = []
        self.pending = 0  # photos not looked at yet
        self._kept = {}  # {its copies' ids: the one chosen to keep}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 8)
        self.title = QLabel()
        font = QFont(self.font())
        font.setPointSizeF(font.pointSizeF() * 1.25)
        font.setWeight(QFont.Weight.DemiBold)
        self.title.setFont(font)
        lay.addWidget(self.title)
        self.hint = QLabel("The copy marked Keep has the most pixels, then the biggest file. Click another to "
                           "keep it instead. Select groups to act on just those.")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: palette(placeholder-text)")
        lay.addWidget(self.hint)
        row = QHBoxLayout()
        self.aside_button = QPushButton("Move extras to Duplicates/...")
        self.aside_button.setToolTip("Into your library's Duplicates folder, named after the copy kept")
        self.trash_button = QPushButton("Move extras to Trash...")
        self.apart_button = QPushButton("Not duplicates")
        self.apart_button.setToolTip("Never offer these as duplicates again")
        for b in (self.aside_button, self.trash_button, self.apart_button):
            row.addWidget(b)
        row.addStretch()
        lay.addLayout(row)
        self.list = _List(self.loader)
        lay.addWidget(self.list, 1)
        self.list.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.loader.set_size(round(TILE * self.devicePixelRatioF()))
        self.aside_button.clicked.connect(lambda: self._act(lambda gs: self.set_aside.emit(gs, "duplicates")))
        self.trash_button.clicked.connect(lambda: self._act(lambda gs: self.set_aside.emit(gs, "trash")))
        self.apart_button.clicked.connect(lambda: self._act(self.apart.emit, selected_only=True))
        self.list.keep.connect(self._keep)
        self.list.open_copy.connect(lambda r, i: self.opened.emit(self.groups[r].copies[i].id))
        self.list.selectionModel().selectionChanged.connect(lambda *_: self._update_text())
        self._update_text()

    def set_groups(self, groups, library, pending=0):
        for g in groups:
            chosen = self._kept.get(frozenset(c.id for c in g.copies))
            if chosen is not None:
                g.keep(chosen)
        self.groups = groups
        self.list.library = library
        self.pending = pending
        self.list.empty_text = ("No duplicates found yet." if pending else
                                "No duplicates. Every photo and video here is the only one of its kind.")
        self.list.model().set_groups(groups)
        self._update_text()

    def selected(self) -> list:
        rows = sorted({i.row() for i in self.list.selectionModel().selectedRows()})
        return [self.groups[r] for r in rows]

    def _act(self, do, selected_only=False):
        groups = self.selected() or ([] if selected_only else list(self.groups))
        if groups:
            do(groups)

    def _keep(self, row, i):
        g = self.groups[row]
        g.keep(g.copies[i].id)
        self._kept[frozenset(c.id for c in g.copies)] = g.keeper.id
        self.list.viewport().update()

    def _update_text(self):
        extras = sum(len(g.extras) for g in self.groups)
        chosen = self.selected()
        if not self.groups:
            self.title.setText("Duplicates")
        else:
            self.title.setText(f"{len(self.groups):,} group{'s' if len(self.groups) != 1 else ''} of duplicates, "
                               f"{extras:,} extra cop{'ies' if extras != 1 else 'y'}")
        hint = ("The copy marked Keep has the most pixels, then the biggest file. Click another to keep it "
                "instead. Select groups to act on just those.")
        if self.pending:
            hint = (f"Still looking at {self.pending:,} photos (after indexing): more may turn up. " + hint)
        self.hint.setText(hint)
        which = f" ({len(chosen):,} selected)" if chosen else ""
        self.aside_button.setText(f"Move extras to Duplicates/{which}...")
        self.trash_button.setText(f"Move extras to Trash{which}...")
        for b in (self.aside_button, self.trash_button):
            b.setEnabled(bool(self.groups))
        self.apart_button.setEnabled(bool(chosen))
        self.apart_button.setToolTip("Never offer the selected groups as duplicates again" if chosen
                                     else "Select the groups that aren't duplicates first")

    def stop(self):
        self.loader.stop()

