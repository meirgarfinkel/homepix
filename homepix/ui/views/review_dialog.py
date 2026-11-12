"""Reviewing what a rule found before it's imported (for rules that don't import by themselves): the
new files as thumbnails, each ticked; untick any to leave it for next time. Hovering one shows where
it will go."""

import os

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from ...core import autoimport
from ...core.media import load_photo
from ...core.video import VIDEO_EXTS, video_frame
from .. import theme, workers
from ..fileops import counted, plural
from ..thumbs import to_qimage

THUMB = 140
PIXMAP_ROLE = Qt.ItemDataRole.UserRole + 1


def _faded(pm: QPixmap) -> QPixmap:
    out = QPixmap(pm.size())
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setOpacity(0.3)
    p.drawPixmap(0, 0, pm)
    p.end()
    return out


def _thumb(path: str):
    try:
        video = os.path.splitext(path)[1].lower() in VIDEO_EXTS
        img = video_frame(path, THUMB) if video else load_photo(path, THUMB)
        return None if img is None else to_qimage(img)
    except Exception:
        return None


def goes_to(dst: str, rule) -> str:
    """Where one file will go, under its thumbnail: "→ 2024/2024_05_06_07_08_09.jpg" (from the rule's
    destination)."""
    rel = os.path.relpath(dst, rule.dest)
    return "→ " + (dst if rel.startswith("..") else rel.replace(os.sep, "/"))


def where_text(batch: autoimport.Batch) -> str:
    """Where they go: "into Pictures/From phone", "into the year folders in Pictures"."""
    r = batch.rule
    if r.tidy:
        return f"into the year folders in {os.path.basename(r.dest) or r.dest}"
    return f"into {os.path.basename(r.dest) or r.dest}"


class ReviewDialog(QDialog):
    def __init__(self, batch: autoimport.Batch, parent=None):
        super().__init__(parent)
        self.batch = batch
        self.setWindowTitle(f"New from {batch.rule.name}")
        self.resize(900, 600)
        lay = QVBoxLayout(self)
        self.head = QLabel()
        self.head.setWordWrap(True)
        lay.addWidget(self.head)
        self.grid = QListWidget()
        self.grid.setViewMode(QListView.ViewMode.IconMode)
        self.grid.setIconSize(QSize(THUMB, THUMB))
        fm = self.fontMetrics()
        wide = max(THUMB + 24, fm.horizontalAdvance("→ 2024/2024_05_06_07_08_09_1.jpg") + 16)  # (a whole tidy name)
        self.grid.setGridSize(QSize(wide, THUMB + 4 + 2 * (fm.height() + 4)))
        self.grid.setResizeMode(QListView.ResizeMode.Adjust)
        self.grid.setMovement(QListView.Movement.Static)
        self.grid.setUniformItemSizes(True)
        self.grid.setWordWrap(False)  # (two lines: its name, and where it goes; long ones shortened)
        self.grid.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.grid.itemChanged.connect(self._changed)
        self.grid.itemDoubleClicked.connect(
            lambda it: it.setCheckState(
                Qt.CheckState.Unchecked if it.checkState() == Qt.CheckState.Checked else Qt.CheckState.Checked
            )
        )
        lay.addWidget(self.grid, 1)
        blank = QPixmap(THUMB, THUMB)
        blank.fill(theme.color("field"))
        placeholder = QIcon(blank)
        for f, dst in batch.imports:
            item = QListWidgetItem(placeholder, f"{os.path.basename(f.path)}\n{goes_to(dst, batch.rule)}")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(Qt.ItemDataRole.UserRole, f.path)
            self.grid.addItem(item)
            workers.run(_thumb, lambda img, err, it=item: self._got_thumb(it, img), f.path)
        self.note = QLabel()
        self.note.setObjectName("hint")
        self.note.setWordWrap(True)
        lay.addWidget(self.note)
        buttons = QDialogButtonBox()
        self.go = QPushButton("Import")
        self.go.setDefault(True)
        buttons.addButton(self.go, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self._count()

    def _got_thumb(self, item, img):
        if img is not None and self.isVisible():
            item.setData(PIXMAP_ROLE, QPixmap.fromImage(img))
            self._show_icon(item)

    def _show_icon(self, item):
        pm = item.data(PIXMAP_ROLE)
        if pm is None:
            return
        ticked = item.checkState() == Qt.CheckState.Checked
        self.grid.blockSignals(True)  # (a new icon counts as a change too)
        item.setIcon(QIcon(pm if ticked else _faded(pm)))
        self.grid.blockSignals(False)

    def _changed(self, item):
        self._show_icon(item)
        self._count()

    def left_out(self) -> list[str]:
        return [
            self.grid.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.grid.count())
            if self.grid.item(i).checkState() != Qt.CheckState.Checked
        ]

    def chosen(self) -> autoimport.Batch:
        return self.batch.without(self.left_out())

    def _count(self):
        b = self.chosen()
        p, v = b.counts()
        verb = "Move" if b.rule.after == autoimport.MOVE else "Copy"
        self.head.setText(
            f"<b>{verb} {counted(p, v)} {where_text(b)}.</b> "
            "Untick any you don't want now: they'll be offered again next time."
        )
        notes = []
        if b.already:
            gone = " They'll go to the Trash." if b.rule.after == autoimport.MOVE else ""
            notes.append(f"{plural(len(b.already), 'file')} already in your library won't be imported again.{gone}")
        if b.cant:
            notes.append(f"{plural(len(b.cant), 'file')} couldn't be read and will be tried again next time.")
        self.note.setText(" ".join(notes))
        self.note.setVisible(bool(notes))
        self.go.setText(f"Import {p + v:,}" if p + v else "Import")
        self.go.setEnabled(bool(b.steps))
