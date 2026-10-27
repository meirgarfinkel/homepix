"""The people page: everyone named, as round faces with how many photos they're in. Choosing one
shows their photos."""

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QBrush, QIcon, QImage, QPainter, QPainterPath, QPalette, QPixmap
from PySide6.QtWidgets import QListView, QListWidget, QListWidgetItem

from ...core import face_thumb
from .. import workers

FACE = 132
CROP = 160  # face crops are made (and kept) at this size


def round_face(path: str, size: int, dpr: float) -> QPixmap:
    px = round(size * dpr)
    img = QImage(path)
    out = QPixmap(px, px)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    clip = QPainterPath()
    clip.addEllipse(0, 0, px, px)
    p.setClipPath(clip)
    if not img.isNull():
        side = min(img.width(), img.height())
        src = img.copy((img.width() - side) // 2, (img.height() - side) // 2, side, side)
        p.drawImage(out.rect(), src)
    p.end()
    out.setDevicePixelRatio(dpr)
    return out


class PeopleView(QListWidget):
    chosen = Signal(int)  # person id

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setIconSize(QSize(FACE, FACE))
        self.setGridSize(QSize(FACE + 72, FACE + 72))
        self.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.setSpacing(8)
        self.setWordWrap(True)
        self.setFrameShape(QListWidget.Shape.NoFrame)
        self.itemClicked.connect(lambda it: self.chosen.emit(it.data(Qt.ItemDataRole.UserRole)))
        self._shown = []
        self._generation = 0  # faces still loading for an older list are dropped

    def set_people(self, people: list[dict]):
        key = [(p["id"], p["name"], p["photos"]) for p in people]
        if key == self._shown:
            return
        self._shown = key
        self.clear()
        placeholder = self._placeholder()
        for person in people:
            n = person["photos"]
            item = QListWidgetItem(placeholder, f"{person['name']}\n{n:,} photo{'s' if n != 1 else ''}")
            item.setData(Qt.ItemDataRole.UserRole, person["id"])
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
            self.addItem(item)
        self._generation += 1
        gen = self._generation
        for row, person in enumerate(people):  # each face shows as soon as it's ready
            if person["face"] is not None:
                workers.run(face_thumb, lambda f, err, row=row: gen == self._generation and self._face_ready(row, f, err),
                            person["face"], CROP)

    def _placeholder(self) -> QIcon:
        dpr = self.devicePixelRatioF()
        pm = QPixmap(round(FACE * dpr), round(FACE * dpr))
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(self.palette().color(QPalette.ColorRole.AlternateBase)))
        p.drawEllipse(pm.rect())
        p.end()
        pm.setDevicePixelRatio(dpr)
        return QIcon(pm)

    def _face_ready(self, row: int, file, err):
        item = self.item(row)
        if err is None and item is not None:  # err: its photo can't be read right now (e.g. the drive is out)
            item.setIcon(QIcon(round_face(str(file), FACE, self.devicePixelRatioF())))

    def keyPressEvent(self, e):
        item = self.currentItem()
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and item is not None:
            self.chosen.emit(item.data(Qt.ItemDataRole.UserRole))
        else:
            super().keyPressEvent(e)

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.count() == 0:
            p = QPainter(self.viewport())
            p.setPen(self.palette().color(QPalette.ColorRole.PlaceholderText))
            p.drawText(self.viewport().rect(), Qt.AlignmentFlag.AlignCenter,
                       "No one has been named yet.\nNaming people comes in a later version.")
            p.end()

