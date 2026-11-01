"""A bar across the top of the window when a newer HomePix has been downloaded: restart to update."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton


class UpdateBar(QFrame):
    restart = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("banner")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 8, 10, 8)
        self.label = QLabel()
        self.button = QPushButton("Restart to update")
        self.button.setDefault(False)
        self.button.clicked.connect(self.restart)
        later = QPushButton("Later")
        later.clicked.connect(self.hide)
        lay.addWidget(self.label, 1)
        lay.addWidget(self.button)
        lay.addWidget(later)
        self.hide()

    def offer(self, version: str):
        self.label.setText(f"HomePix {version} is ready.")
        self.show()
