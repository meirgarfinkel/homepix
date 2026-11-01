"""The people page: first "Who is this?" (while there are faces to name), then everyone named, as
round faces with how many photos they're in. Choosing one shows their photos; right-click to
rename, merge or forget them, or check their faces."""

from PySide6.QtCore import QPoint, QPointF, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QFont, QIcon, QImage, QPainter, QPainterPath, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QListView, QListWidget, QListWidgetItem

from ...core.thumbs import face_thumb
from .. import theme, workers

FACE = 132
CROP = 160  # face crops are made (and kept) at this size
WHO_TILE = -1  # the id of the "Who is this?" circle


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
    who_chosen = Signal()  # the "Who is this?" circle
    menu_requested = Signal(int, QPoint)  # person id, where (global)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("people")
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setIconSize(QSize(FACE, FACE))
        self.setGridSize(QSize(FACE + 72, FACE + 72))
        self.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.setSpacing(8)
        self.setWordWrap(True)
        self.setFrameShape(QListWidget.Shape.NoFrame)
        self.itemClicked.connect(self._choose)
        self._shown = None
        self._people = []
        self._who = None
        self._generation = 0  # faces still loading for an older list are dropped

    def set_people(self, people: list[dict], who=None):
        """who: None if there are no faces to name (no "Who is this?"), else how many groups of
        them to ask about ("" if not known yet)."""
        self._people, self._who = people, who
        key = ([(p["id"], p["name"], p["photos"]) for p in people], who)
        if key == self._shown:
            return
        self._shown = key
        self.clear()
        placeholder = self._placeholder()
        first = 0
        if who is not None:
            item = QListWidgetItem(self._who_icon(), self._who_text(who))
            item.setData(Qt.ItemDataRole.UserRole, WHO_TILE)
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
            item.setForeground(theme.color("accentText"))
            item.setToolTip("Name the people in your photos")
            bold = QFont(self.font())
            bold.setWeight(QFont.Weight.Bold)
            item.setFont(bold)
            self.addItem(item)
            first = 1
        for person in people:
            n = person["photos"]
            item = QListWidgetItem(placeholder, f"{person['name']}\n{n:,} photo{'s' if n != 1 else ''}")
            item.setData(Qt.ItemDataRole.UserRole, person["id"])
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
            self.addItem(item)
        self._generation += 1
        gen = self._generation
        for row, person in enumerate(people, first):  # each face shows as soon as it's ready
            if person["face"] is not None:
                workers.run(
                    face_thumb,
                    lambda f, err, row=row: gen == self._generation and self._face_ready(row, f, err),
                    person["face"],
                    CROP,
                )

    def set_who(self, who):
        """How many groups there are to ask about now (None: none at all)."""
        if (who is None) != (self._who is None):
            self.set_people(self._people, who)
        elif who is not None:
            self._who = who
            self._shown = (self._shown[0], who)
            item = self.item(0)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == WHO_TILE:
                item.setText(self._who_text(who))

    @staticmethod
    def _who_text(who) -> str:
        if isinstance(who, int):
            return f"Who is this?\n{who:,} to name" if who else "Who is this?\nall named"
        return "Who is this?"

    def _who_icon(self) -> QIcon:
        """A circle in the accent's soft colour with a question mark."""
        dpr = self.devicePixelRatioF()
        pm = QPixmap(round(FACE * dpr), round(FACE * dpr))
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.scale(dpr, dpr)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.color("accentSoft"))
        p.drawEllipse(0, 0, FACE, FACE)
        pen = QPen(theme.color("accentText"), FACE * 0.055)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        c, r = FACE / 2, FACE * 0.13
        mark = QPainterPath(QPointF(c - r, c - r * 0.9))
        mark.arcTo(c - r, c - r * 1.9, 2 * r, 2 * r, 180, -270)  # the hook
        mark.quadTo(c, c + r * 0.15, c, c + r * 0.75)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(mark)
        p.setBrush(theme.color("accentText"))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(c, c + r * 1.75), FACE * 0.035, FACE * 0.035)
        p.end()
        pm.setDevicePixelRatio(dpr)
        return QIcon(pm)

    def _choose(self, item):
        pid = item.data(Qt.ItemDataRole.UserRole)
        if pid == WHO_TILE:
            self.who_chosen.emit()
        else:
            self.chosen.emit(pid)

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
            self._choose(item)
        else:
            super().keyPressEvent(e)

    def current_person(self):
        item = self.currentItem()
        if item is None or not item.isSelected() or item.data(Qt.ItemDataRole.UserRole) == WHO_TILE:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def contextMenuEvent(self, e):
        item = self.itemAt(e.pos())
        if item is not None and item.data(Qt.ItemDataRole.UserRole) != WHO_TILE:
            self.setCurrentItem(item)
            self.menu_requested.emit(item.data(Qt.ItemDataRole.UserRole), e.globalPos())

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.count() == 0:
            p = QPainter(self.viewport())
            p.setPen(self.palette().color(QPalette.ColorRole.PlaceholderText))
            p.drawText(
                self.viewport().rect(),
                Qt.AlignmentFlag.AlignCenter,
                "No one named yet.",
            )
            p.end()
