"""A bar across the top of the window when new files wait for review (automations that ask first)."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton


class ReviewBar(QFrame):
    """New files are waiting (automations that ask first): review them, or later."""

    review = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("banner")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 8, 10, 8)
        self.label = QLabel()
        self.label.setWordWrap(True)
        self.review_button = QPushButton("Review")
        self.review_button.clicked.connect(self.review)
        later = QPushButton("Later")
        later.clicked.connect(self.hide)
        lay.addWidget(self.label, 1)
        lay.addWidget(self.review_button)
        lay.addWidget(later)
        self.hide()

    def show_waiting(self, text: str):
        self.label.setText(text)
        self.show()
