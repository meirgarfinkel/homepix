"""A notice across the top of the window: photos that can't be found, or indexing that went wrong."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton


class Banner(QFrame):
    forget_missing = Signal()  # "they were deleted, remove them"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("banner")
        self.setStyleSheet("#banner { background: palette(alternate-base); border-bottom: 1px solid palette(mid); }")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 8, 10, 8)
        self.label = QLabel()
        self.label.setWordWrap(True)
        self.button = QPushButton("They were deleted, remove them")
        self.button.setToolTip("Take them out of Photo Finder. No files are touched.")
        self.button.clicked.connect(self.forget_missing)
        lay.addWidget(self.label, 1)
        lay.addWidget(self.button)
        self.missing = 0
        self.error = None
        self.hide()

    def show_state(self, missing: int, error):
        if missing == self.missing and error == self.error:
            return
        self.missing, self.error = missing, error
        if error:
            self.label.setText(error)
            self.button.hide()
        elif missing:
            self.label.setText(
                f"{missing:,} photo{'s' if missing != 1 else ''} Photo Finder knew about can't be found right "
                "now. They're kept in case a drive is unplugged or a folder is out of reach.")
            self.button.show()
        self.setVisible(bool(error or missing))
