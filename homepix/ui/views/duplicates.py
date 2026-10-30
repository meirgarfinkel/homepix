"""The duplicates review: each group of copies side by side, the one to keep marked. Click another to
keep it instead; move the extras to the Trash, say they aren't duplicates, or skip a group for now
(it's shown last, to decide later). Nothing happens without a preview first."""

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
from ...core.settings import set_skipped_duplicates, skipped_duplicates
from .. import theme
from ..thumbs import ThumbLoader
from .changes_dialog import relative

TILE = 240
GAP = 22
PAD = 16
RING = 4  # the keeper's ring sits this far outside its picture
RADIUS = 6
HEADER = 56  # the "Skipped for now" heading
SKIP_W = 132  # the Skip button
SKIP = "skip"  # (row, SKIP): the mouse is over that row's Skip button
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
            return (
                "The same file" if g.how == "exact" else "They look the same"
            ) + ". Click a copy to keep it instead."
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
        return QSize(
            0, PAD * 2 + RING + TILE + 10 + self.lines() * 3 + (HEADER if index.row() == self.view.first_skipped else 0)
        )

    def body(self, rect: QRect, row: int) -> QRect:
        """The row without the "Skipped for now" heading the first skipped group has above it."""
        return rect.adjusted(0, HEADER, 0, 0) if row == self.view.first_skipped else rect

    def tile_rect(self, rect: QRect, i: int) -> QRect:
        return QRect(rect.left() + PAD + RING + i * (TILE + GAP), rect.top() + PAD + RING, TILE, TILE)

    def tile_at(self, rect: QRect, pos, n: int) -> int | None:
        for i in range(n):
            r = self.tile_rect(rect, i)
            if r.right() > self.tiles_end(rect):
                break
            if r.left() <= pos.x() <= r.right() and r.top() <= pos.y() <= r.bottom() + self.lines() * 3:
                return i
        return None

    def tiles_end(self, rect: QRect) -> int:
        """Where the copies stop: room is left for the Skip button on the right."""
        return rect.right() - SKIP_W - PAD

    def skip_rect(self, rect: QRect) -> QRect:
        h = self.view.fontMetrics().height() + 14
        return QRect(rect.right() - PAD - SKIP_W, rect.top() + PAD + RING, SKIP_W, h)

    def paint(self, p: QPainter, option, index):
        row = index.row()
        g = self.view.model().groups[row]
        c_ = theme.color
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.body(option.rect, row)
        if rect != option.rect:
            p.setFont(theme.font(16, QFont.Weight.Bold))
            p.setPen(c_("text"))
            head = QRect(option.rect.left() + PAD, option.rect.top() + 14, option.rect.width() - 2 * PAD, HEADER - 14)
            p.drawText(
                head,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                f"Skipped for now ({len(self.view.model().groups) - row:,})",
            )
            p.setPen(QPen(c_("border")))
            p.drawLine(option.rect.left(), option.rect.top() + 6, option.rect.right(), option.rect.top() + 6)
        if option.state & QStyle.StateFlag.State_Selected:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c_("accentSoft"))
            p.drawRoundedRect(QRectF(rect).adjusted(4, 4, -4, -4), 14, 14)
        p.setPen(QPen(c_("border")))
        p.drawLine(option.rect.bottomLeft(), option.rect.bottomRight())
        s = self.skip_rect(rect)
        over_skip = self.view.hover == (row, SKIP)
        p.setPen(QPen(c_("border")) if not over_skip else Qt.PenStyle.NoPen)
        p.setBrush(c_("hoverStrong") if over_skip else Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(s), s.height() / 2, s.height() / 2)
        p.setFont(theme.font(12, QFont.Weight.DemiBold))
        p.setPen(c_("text"))
        p.drawText(s, Qt.AlignmentFlag.AlignCenter, "Decide now" if self.view.is_skipped(g) else "Skip for now")
        fm, lh = self.view.fontMetrics(), self.lines()
        bold = theme.font(12, QFont.Weight.Bold)
        hover = self.view.hover
        for i, c in enumerate(self.view.shown(g)):
            r = self.tile_rect(rect, i)
            if r.right() > self.tiles_end(rect):
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
                mark = "Same file" if g.how == "exact" else "Extra copy"
                _pill(p, r.left() + 10, r.top() + 10, mark, QColor(0, 0, 0, 140), QColor(255, 255, 255))
                if over:
                    _pill(
                        p, r.center().x(), r.center().y(), "Keep this one", c_("accent"), c_("accentInk"), centre=True
                    )
            y = r.bottom() + 10
            p.setFont(self.view.font())
            p.setPen(c_("text"))
            size = f"{c.width:,} × {c.height:,} · " if c.width else ""
            p.drawText(
                QRect(r.left(), y, TILE, lh),
                Qt.AlignmentFlag.AlignLeft,
                fm.elidedText(size + human_size(c.size), Qt.TextElideMode.ElideRight, TILE),
            )
            p.setPen(c_("muted"))
            p.drawText(
                QRect(r.left(), y + lh, TILE, lh * 2),
                Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWrapAnywhere,
                relative(c.path, self.view.library),
            )
        p.restore()


class _List(QListView):
    keep = Signal(int, int)  # row, copy (as shown)
    open_copy = Signal(int, int)
    skip = Signal(int)  # row: skip it for now (or decide it now, if skipped)

    def __init__(self, loader, parent=None):
        super().__init__(parent)
        self.loader = loader
        self.library = []
        self.hover = None  # (row, copy) or (row, SKIP) under the mouse
        self.first_skipped = None  # the row the skipped groups start at (the heading sits above it)
        self.is_skipped = lambda g: False
        self._order = {}  # {its copies' ids: the order they're shown in}, so a click doesn't move them
        self.setModel(_Groups(self))
        self.setItemDelegate(_Row(self))
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setMouseTracking(True)
        self.setFrameShape(QListView.Shape.NoFrame)
        loader.ready.connect(lambda _q: self.viewport().update())

    def shown(self, g) -> list:
        """Its copies in the order first shown (the keeper first, then), whichever is kept now."""
        key = frozenset(c.id for c in g.copies)
        order = self._order.setdefault(key, [c.id for c in g.copies])
        return sorted(g.copies, key=lambda c: order.index(c.id))

    def _hit(self, pos):
        """(row, copy as shown), (row, SKIP), or (row or None, None)."""
        index = self.indexAt(pos)
        if not index.isValid():
            return None, None
        row, d = index.row(), self.itemDelegate()
        rect = d.body(self.visualRect(index), row)
        if d.skip_rect(rect).contains(pos):
            return row, SKIP
        return row, d.tile_at(rect, pos, len(self.model().groups[row].copies))

    def mousePressEvent(self, e):
        row, i = self._hit(e.position().toPoint())
        plain = e.button() == Qt.MouseButton.LeftButton and not e.modifiers() & (
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
        )
        if i == SKIP:
            if plain:
                self.skip.emit(row)
            return
        if row is not None and i is not None and plain:
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
        if row is not None and i is not None and i != SKIP:
            self.open_copy.emit(row, i)

    def paintEvent(self, e):
        super().paintEvent(e)
        groups = self.model().groups
        first = self.indexAt(self.viewport().rect().topLeft())
        start = first.row() if first.isValid() else 0
        last = self.indexAt(self.viewport().rect().bottomLeft())
        end = last.row() + 2 if last.isValid() else len(groups)
        rows = range(start, min(len(groups), end))
        self.loader.want([(c.qhash, c.path) for r in rows for c in groups[r].copies])
        if not groups:
            p = QPainter(self.viewport())
            p.setPen(self.palette().color(QPalette.ColorRole.PlaceholderText))
            p.drawText(self.viewport().rect(), Qt.AlignmentFlag.AlignCenter, self.empty_text)

    empty_text = "Looking for duplicates..."


class DuplicatesView(QWidget):
    """The duplicates in the library (a group can be skipped for now: it's shown last, to decide
    later). trash_extras(groups); apart(groups); compared(its copies' ids as shown, the one
    double-clicked): to look at them large, one after another."""

    trash_extras = Signal(list)
    deciding = Signal(int)  # a group was skipped (or taken back): how many are left to decide
    apart = Signal(list)
    compared = Signal(list, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.loader = ThumbLoader(workers=2, budget=48 * 1024 * 1024)
        self.groups = []
        self.pending = 0  # photos not looked at yet
        self._kept = {}  # {its copies' ids: the one chosen to keep}
        self._skipped = skipped_duplicates()  # {its copies' ids} of the groups skipped for now
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
        self.trash_button = QPushButton("Move extras to Trash...")
        self.trash_button.setToolTip("Each group keeps the copy marked Keep. Skipped groups are left as they are.")
        self.apart_button = QPushButton("Not duplicates")
        self.apart_button.setToolTip("Never offer these as duplicates again")
        for b in (self.trash_button, self.apart_button):
            row.addWidget(b)
        row.addStretch()
        lay.addLayout(row)
        self.list = _List(self.loader)
        self.list.is_skipped = self.is_skipped
        lay.addWidget(self.list, 1)
        self.list.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.loader.set_size(round(TILE * self.devicePixelRatioF()))
        self.trash_button.clicked.connect(self._trash)
        self.apart_button.clicked.connect(lambda: self._act(self.apart.emit, selected_only=True))
        self.list.keep.connect(self._keep)
        self.list.skip.connect(self.skip)
        self.list.open_copy.connect(lambda r, i: self.compared.emit([c.id for c in self.list.shown(self.groups[r])], i))
        self.list.selectionModel().selectionChanged.connect(lambda *_: self._update_text())
        self._show()

    def set_groups(self, groups, library, pending=0):
        """The duplicates in the library (those skipped for now last)."""
        self.list.library = library
        self.pending = pending
        for g in groups:
            chosen = self._kept.get(self._key(g))
            if chosen is not None:
                g.keep(chosen)
        self.groups = self._skipped_last(groups)
        self._show()

    def _skipped_last(self, groups):
        return [g for g in groups if not self.is_skipped(g)] + [g for g in groups if self.is_skipped(g)]

    def _show(self):
        self.list.empty_text = (
            "No duplicates found yet."
            if self.pending
            else "No duplicates. Every photo and video here is the only one of its kind."
        )
        self.list.first_skipped = next((i for i, g in enumerate(self.groups) if self.is_skipped(g)), None)
        self.list.model().set_groups(self.groups)
        self.list.viewport().update()
        self._update_text()

    # --- skipping a group for now ---

    @staticmethod
    def _key(g) -> frozenset:
        return frozenset(c.id for c in g.copies)

    def is_skipped(self, g) -> bool:
        return self._key(g) in self._skipped

    def to_decide(self) -> int:
        """The groups not skipped."""
        return sum(not self.is_skipped(g) for g in self.groups)

    def skip(self, row: int):
        """Skip that group for now (to the end, under Skipped for now), or decide it now if it was skipped."""
        key = self._key(self.groups[row])
        self._skipped ^= {key}
        self._skipped &= {self._key(g) for g in self.groups}  # (groups decided since are forgotten)
        set_skipped_duplicates(self._skipped)
        self.groups = self._skipped_last(self.groups)
        bar = self.list.verticalScrollBar()
        at = bar.value()
        self._show()
        bar.setValue(at)  # the next group takes its place
        self.deciding.emit(self.to_decide())

    def selected(self) -> list:
        rows = sorted({i.row() for i in self.list.selectionModel().selectedRows()})
        return [self.groups[r] for r in rows]

    def _act(self, do, selected_only=False):
        groups = self.selected() or ([] if selected_only else list(self.groups))
        if groups:
            do(groups)

    def _trash(self):
        groups = self.selected() or [g for g in self.groups if not self.is_skipped(g)]
        if groups:
            self.trash_extras.emit(groups)

    def _keep(self, row, i):
        g = self.groups[row]
        g.keep(self.list.shown(g)[i].id)
        self._kept[self._key(g)] = g.keeper.id
        self.list.viewport().update()

    def _group_of(self, photo_id):
        return next((g for g in self.groups if any(c.id == photo_id for c in g.copies)), None)

    def copy_info(self, photo_id) -> tuple[str, str]:
        """For the viewer: ("Copy 1 of 2 · ✓ Keep", "4,032 × 3,024 · 3.2 MB · 2024/IMG_1.jpg")."""
        g = self._group_of(photo_id)
        if g is None:
            return "Not a duplicate any more", ""
        shown = self.list.shown(g)
        i = next(i for i, c in enumerate(shown) if c.id == photo_id)
        c = shown[i]
        if c.id == g.keeper.id:
            kept = "✓ Keep"
        else:
            kept = "Same file" if g.how == "exact" else "Extra copy"
        size = f"{c.width:,} × {c.height:,} · " if c.width else ""
        n = self.groups.index(g)
        return (
            f"Copy {i + 1} of {len(shown)} · {kept}",
            f"Duplicates {n + 1} of {len(self.groups)} · {size}{human_size(c.size)} · "
            f"{relative(c.path, self.list.library)}",
        )

    def other_group(self, photo_id, step: int):
        """The group step after (or before) this photo's: its copies' ids as shown, or None at either end.
        It's made the current row on the page, so going back lands on it."""
        g = self._group_of(photo_id)
        row = (self.groups.index(g) if g is not None else -1) + step
        if not 0 <= row < len(self.groups):
            return None
        index = self.list.model().index(row, 0)
        self.list.selectionModel().clearSelection()
        self.list.setCurrentIndex(index)
        self.list.scrollTo(index)
        return [c.id for c in self.list.shown(self.groups[row])]

    def keep_photo(self, photo_id):
        """Keep this copy of its group (from the viewer)."""
        g = self._group_of(photo_id)
        if g is not None:
            self._keep(self.groups.index(g), [c.id for c in self.list.shown(g)].index(photo_id))

    def _update_text(self):
        deciding = [g for g in self.groups if not self.is_skipped(g)]
        skipped = len(self.groups) - len(deciding)
        extras = sum(len(g.extras) for g in deciding)
        chosen = self.selected()
        if not self.groups:
            self.title.setText("Duplicates")
        else:
            self.title.setText(
                f"{len(deciding):,} group{'s' if len(deciding) != 1 else ''} of duplicates, "
                f"{extras:,} extra cop{'ies' if extras != 1 else 'y'}" + (f", {skipped:,} skipped" if skipped else "")
            )
        hint = (
            "The copy with the ring is kept (the one with the most pixels, then the biggest file); the "
            "dimmed ones are the extras. Click a copy to keep it instead. Not sure yet? Skip for now puts "
            "the group at the end, to decide later. Double-click to compare them large "
            "(← → to switch, ↑ ↓ for other duplicates, K to keep). "
            "Select groups (Ctrl-click the space beside them) to act on just those."
        )
        if self.pending:
            hint = f"Still looking at {self.pending:,} photos (after indexing): more may turn up. " + hint
        self.hint.setText(hint)
        which = f" ({len(chosen):,} selected)" if chosen else ""
        self.trash_button.setText(f"Move extras to Trash{which}...")
        self.trash_button.setEnabled(bool(chosen or deciding))
        self.apart_button.setEnabled(bool(chosen))
        self.apart_button.setToolTip(
            "Never offer the selected groups as duplicates again"
            if chosen
            else "Select the groups that aren't duplicates first"
        )

    def stop(self):
        self.loader.stop()
