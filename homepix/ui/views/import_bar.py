"""A bar across the top of the window after auto import brought photos in: see them, or undo it."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton


class ImportBar(QFrame):
    see = Signal(str)  # the place to show: a folder, or "" for all photos
    undo = Signal(int)  # the import's operation id

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("banner")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 8, 10, 8)
        self.label = QLabel()
        self.label.setWordWrap(True)
        self.see_button = QPushButton("See them")
        self.see_button.clicked.connect(lambda: self.see.emit(self.place))
        self.undo_button = QPushButton("Undo")
        self.undo_button.clicked.connect(lambda: self.undo.emit(self.op_id))
        close = QPushButton("Close")
        close.clicked.connect(self.hide)
        lay.addWidget(self.label, 1)
        lay.addWidget(self.see_button)
        lay.addWidget(self.undo_button)
        lay.addWidget(close)
        self.op_id = None
        self.place = ""
        self.hide()

    def show_import(self, text: str, op_id: int | None, place: str, imported: bool):
        """text: what happened; op_id: the operation to undo (None: nothing to undo); place: where
        "See them" goes; imported: anything came in (else there's nothing to see)."""
        self.label.setText(text)
        self.op_id, self.place = op_id, place
        self.see_button.setVisible(imported)
        self.undo_button.setVisible(op_id is not None)
        self.show()
