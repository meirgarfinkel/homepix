"""The status line at the bottom of the sidebar (as in HomeDocs): what indexing is doing or a message like
"Moved 3 photos to Trash", a thin progress bar under it, and an x to stop indexing. It's never over the
page. Messages still go through the window's QStatusBar (which stays hidden): this shows them."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QToolButton, QVBoxLayout, QWidget

from . import theme
from .indexing import describe


class StatusLine(QWidget):
    """changed(str): the words now shown ("" when there's nothing to say)."""

    changed = Signal(str)

    def __init__(self, watcher, status_bar, parent=None):
        super().__init__(parent)
        self.setObjectName("statusLine")
        self.status_bar = status_bar
        self._state = {"running": False, "phase": "idle"}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 6, 10, 12)
        lay.setSpacing(5)
        row = QHBoxLayout()
        row.setSpacing(4)
        self.label = QLabel()
        self.label.setWordWrap(True)  # the sidebar is narrow: all of it, on two lines if need be
        self.stop = QToolButton()
        self.stop.setObjectName("statusStop")
        self.stop.setAccessibleName("Stop")
        self.stop.clicked.connect(watcher.stop)
        row.addWidget(self.label, 1)
        row.addWidget(self.stop)
        lay.addLayout(row)
        self.bar = QProgressBar()
        self.bar.setFixedHeight(3)
        self.bar.setTextVisible(False)
        lay.addWidget(self.bar)
        self.text = ""
        watcher.state.connect(self.show_state)
        status_bar.hide()
        status_bar.messageChanged.connect(lambda _: self.refresh())
        theme.signals.changed.connect(self._restyle)
        self._restyle()
        self.refresh()

    def _restyle(self):
        self.stop.setIcon(theme.icon("clear", "muted"))

    def show_state(self, s: dict):
        self._state = s
        if s["running"]:
            if s["phase"] == "indexing" and s.get("todo"):
                self.bar.setRange(0, s["todo"])
                self.bar.setValue(s["done"])
            elif s["phase"] == "sharpening" and s.get("sharpen_todo"):
                self.bar.setRange(0, s["sharpen_todo"])
                self.bar.setValue(s["sharpen_done"])
            else:
                self.bar.setRange(0, 0)  # busy, amount unknown
        self.refresh()

    def refresh(self):
        text = self.status_bar.currentMessage() or describe(self._state)
        busy = bool(self._state["running"])
        self.label.setText(text)
        self.bar.setVisible(busy)
        self.stop.setVisible(busy)
        self.setVisible(bool(text) or busy)
        if text != self.text:
            self.text = text
            self.changed.emit(text)
