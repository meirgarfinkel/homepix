"""A row of buttons that wraps onto a second line when the window is narrow, instead of holding the
window wide (so it fits half the screen)."""

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QStyle


class FlowLayout(QLayout):
    def __init__(self, parent=None, spacing: int = -1):
        super().__init__(parent)
        self._items = []
        self._gap = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._place(QRect(0, 0, width, 0), move=False)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._place(rect, move=True)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _spacing(self, horizontal: bool) -> int:
        if self._gap >= 0:
            return self._gap
        w = self.parentWidget()
        if w is None:
            return 6
        metric = (
            QStyle.PixelMetric.PM_LayoutHorizontalSpacing if horizontal else QStyle.PixelMetric.PM_LayoutVerticalSpacing
        )
        gap = w.style().pixelMetric(metric, None, w)
        return gap if gap >= 0 else 6

    def _place(self, rect: QRect, move: bool) -> int:
        """Lay the items out left to right, wrapping; returns the height that takes."""
        m = self.contentsMargins()
        area = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line = area.x(), area.y(), 0
        across, down = self._spacing(True), self._spacing(False)
        for item in self._items:
            w = item.widget()
            if w is not None and w.isHidden():
                continue
            size = item.sizeHint().expandedTo(item.minimumSize())  # (a widget's own minimum width counts)
            if x > area.x() and x + size.width() > area.right() + 1:
                x, y, line = area.x(), y + line + down, 0
            if move:
                item.setGeometry(QRect(QPoint(x, y), size))
            x += size.width() + across
            line = max(line, size.height())
        return y + line - rect.y() + m.bottom()
