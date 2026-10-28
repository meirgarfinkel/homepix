"""The duplicates review: each group of copies side by side, the one to keep marked. Click another to
keep it instead; set the extras aside (into Duplicates/, or the Trash) or say they aren't duplicates,
for the groups selected, or all of them. Nothing happens without a preview first."""

from PySide6.QtCore import QAbstractListModel, QModelIndex, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPalette, QPen
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
from .. import theme
from ..thumbs import ThumbLoader
from .changes_dialog import relative

TILE = 240
GAP = 22
PAD = 16
RING = 4  # the keeper's ring sits this far outside its picture
RADIUS = 6
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


def _pill(p: QPainter, x: int, y: int, text: str, bg: QColor, fg: QColor, centre: bool = False) -> QRect:
    fm = p.fontMetrics()
    w, h = fm.horizontalAdvance(text) + 20, fm.height() + 8
    r = QRect(x - w // 2 if centre else x, y - h // 2 if centre else y, w, h)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(bg)
    p.drawRoundedRect(QRectF(r), h / 2, h / 2)
    p.setPen(fg)
    p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)
    return r


class _Row(QStyledItemDelegate):
    """A group: its copies side by side, always in the same order. The one kept has a ring and a
    Keep badge; the extras are dimmed, and say "Keep this one" when the mouse is over them."""

    def __init__(self, view):
        super().__init__(view)
        self.view = view

    def lines(self) -> int:
        return self.view.fontMetrics().height()

    def sizeHint(self, option, index):
        return QSize(0, PAD * 2 + RING + TILE + 10 + self.lines() * 3)

    def tile_rect(self, rect: QRect, i: int) -> QRect:
        return QRect(rect.left() + PAD + RING + i * (TILE + GAP), rect.top() + PAD + RING, TILE, TILE)

    def tile_at(self, rect: QRect, pos, n: int) -> int | None:
        for i in range(n):
            r = self.tile_rect(rect, i)
            if r.left() <= pos.x() <= r.right() and r.top() <= pos.y() <= r.bottom() + self.lines() * 3:
                return i
        return None

    def paint(self, p: QPainter, option, index):
        row = index.row()
        g = self.view.model().groups[row]
        c_ = theme.color
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if option.state & QStyle.StateFlag.State_Selected:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c_("accentSoft"))
            p.drawRoundedRect(QRectF(option.rect).adjusted(4, 4, -4, -4), 14, 14)
        p.setPen(QPen(c_("border")))
        p.drawLine(option.rect.bottomLeft(), option.rect.bottomRight())
        fm, lh = self.view.fontMetrics(), self.lines()
        bold = theme.font(12, QFont.Weight.Bold)
        hover = self.view.hover
        for i, c in enumerate(self.view.shown(g)):
            r = self.tile_rect(option.rect, i)
            if r.left() > option.rect.right():
                break
            keep = c.id == g.keeper.id
            over = hover == (row, i)
            clip = QPainterPath()
            clip.addRoundedRect(QRectF(r), RADIUS, RADIUS)
            img = self.view.loader.get(c.qhash)
            p.save()
            p.setClipPath(clip)
            if img is not None:
                p.drawImage(r, img)
            else:
                p.fillRect(r, c_("field"))
            if not keep and not over:  # the extras, dimmed
                dim = c_("bg")
                dim.setAlpha(110)
                p.fillRect(r, dim)
            p.restore()
            p.setFont(bold)
            if keep:
                p.setPen(QPen(c_("accent"), 3))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(QRectF(r).adjusted(-RING, -RING, RING, RING), RADIUS + RING, RADIUS + RING)
                _pill(p, r.left() + 10, r.top() + 10, "✓ Keep", c_("accent"), c_("accentInk"))
            else:
                _pill(p, r.left() + 10, r.top() + 10, "Same file" if g.how == "exact" else "Extra copy",
                      QColor(0, 0, 0, 140), QColor(255, 255, 255))
                if over:
                    _pill(p, r.center().x(), r.center().y(), "Keep this one", c_("accent"), c_("accentInk"),
                          centre=True)
            y = r.bottom() + 10
            p.setFont(self.view.font())
            p.setPen(c_("text"))
            size = f"{c.width:,} × {c.height:,} · " if c.width else ""
            p.drawText(QRect(r.left(), y, TILE, lh), Qt.AlignmentFlag.AlignLeft,
                       fm.elidedText(size + human_size(c.size), Qt.TextElideMode.ElideRight, TILE))
            p.setPen(c_("muted"))
            p.drawText(QRect(r.left(), y + lh, TILE, lh * 2),
                       Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWrapAnywhere, relative(c.path, self.view.library))
        p.restore()


class _List(QListView):
    keep = Signal(int, int)  # row, copy (as shown)
    open_copy = Signal(int, int)

    def __init__(self, loader, parent=None):
        super().__init__(parent)
        self.loader = loader
        self.library = []
        self.hover = None  # (row, copy) under the mouse
        self._order = {}  # {its copies' ids: the order they're shown in}, so a click doesn't move them
        self.setModel(_Groups(self))
        self.setItemDelegate(_Row(self))
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setUniformItemSizes(True)
        self.setMouseTracking(True)
        self.setFrameShape(QListView.Shape.NoFrame)
        loader.ready.connect(lambda _q: self.viewport().update())

    def shown(self, g) -> list:
        """Its copies in the order first shown (the keeper first, then), whichever is kept now."""
        key = frozenset(c.id for c in g.copies)
        order = self._order.setdefault(key, [c.id for c in g.copies])
        return sorted(g.copies, key=lambda c: order.index(c.id))

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

    def mouseMoveEvent(self, e):
        row, i = self._hit(e.position().toPoint())
        hover = (row, i) if i is not None else None
        if hover != self.hover:
            self.hover = hover
            self.viewport().setCursor(Qt.CursorShape.PointingHandCursor if hover else Qt.CursorShape.ArrowCursor)
            self.viewport().update()
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        if self.hover is not None:
            self.hover = None
            self.viewport().update()
        super().leaveEvent(e)

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
        lay.setContentsMargins(24, 18, 24, 8)
        self.title = QLabel()
        self.title.setObjectName("pageTitle")
        lay.addWidget(self.title)
        self.hint = QLabel()
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
        self.list.open_copy.connect(lambda r, i: self.opened.emit(self.list.shown(self.groups[r])[i].id))
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
        g.keep(self.list.shown(g)[i].id)
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
        hint = ("The copy with the ring is kept (the one with the most pixels, then the biggest file); the "
                "dimmed ones are the extras. Click a copy to keep it instead. Double-click to see it large. "
                "Select groups (Ctrl-click the space beside them) to act on just those.")
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

