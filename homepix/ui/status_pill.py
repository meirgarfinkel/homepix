"""The status pill: a small box floating at the bottom right of the page, over the photos, while
there's something to say (a message, or what indexing is doing). It's never part of the layout, so
nothing moves when it comes and goes. Messages still go through the window's QStatusBar (which
stays hidden): the pill shows what it says."""

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel

MARGIN = 14  # from the page's bottom and right edges


class StatusPill(QFrame):
    def __init__(self, page, status_bar, index_status, right_inset: int = 0):
        """page: the widget it floats over; status_bar: whose messages it shows; index_status: the
        indexing widget it holds; right_inset: room left free on the right (the date scrubber)."""
        super().__init__(page)
        self.setObjectName("statusPill")
        self.page, self.status_bar, self.index_status, self.right_inset = page, status_bar, index_status, right_inset
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 4, 6, 4)
        lay.setSpacing(10)
        self.message = QLabel()
        lay.addWidget(self.message)
        lay.addWidget(index_status)
        status_bar.hide()
        status_bar.messageChanged.connect(lambda _: self.refresh())
        self._follow = _Resized(self.refresh)
        page.installEventFilter(self._follow)
        self.refresh()

    def refresh(self, *_):
        text = self.status_bar.currentMessage()
        room = max(160, self.page.width() // 2)  # one line: a long message ends in "..." (all of it on hover)
        self.message.setText(self.message.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, room))
        self.message.setToolTip(text)
        self.message.setVisible(bool(text))
        s = self.index_status
        busy = not s.label.isHidden() or not s.bar.isHidden()
        s.setVisible(busy)
        self.setVisible(bool(text) or busy)
        if self.isVisible():
            self.place()
            self.raise_()

    def place(self):
        self.adjustSize()
        x = self.page.width() - self.width() - MARGIN - self.right_inset
        self.move(max(MARGIN, x), self.page.height() - self.height() - MARGIN)


class _Resized(QObject):
    def __init__(self, then):
        super().__init__()
        self.then = then

    def eventFilter(self, obj, e):
        if e.type() == QEvent.Type.Resize:
            self.then()
        return False
