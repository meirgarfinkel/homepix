"""What a new library shows: choose the folders where your photos are."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QFileDialog, QLabel, QPushButton, QVBoxLayout, QWidget

from ...core import pictures_folder


class Welcome(QWidget):
    folder_chosen = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setSpacing(14)
        title = QLabel("Welcome to Photo Finder")
        font = QFont(self.font())
        font.setPointSizeF(font.pointSizeF() * 1.8)
        font.setWeight(QFont.Weight.DemiBold)
        title.setFont(font)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        text = QLabel("Choose the folders where you keep your photos. Photo Finder looks through them in "
                      "the background so you can browse and search them. It never changes your files.")
        text.setWordWrap(True)
        text.setMaximumWidth(460)
        text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)
        lay.addWidget(text, 0, Qt.AlignmentFlag.AlignHCenter)
        pics = pictures_folder()
        if pics.is_dir():
            use_pics = QPushButton(f"Use my Pictures folder ({pics})")
            use_pics.setDefault(True)
            use_pics.clicked.connect(lambda: self.folder_chosen.emit(str(pics)))
            lay.addWidget(use_pics, 0, Qt.AlignmentFlag.AlignHCenter)
        other = QPushButton("Choose a folder...")
        other.clicked.connect(self._choose)
        lay.addWidget(other, 0, Qt.AlignmentFlag.AlignHCenter)

    def _choose(self):
        path = QFileDialog.getExistingDirectory(self, "Choose a folder with photos", str(pictures_folder()))
        if path:
            self.folder_chosen.emit(path)
