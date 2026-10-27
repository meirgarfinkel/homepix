"""Starting the desktop app."""

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from .. import __version__
from ..core.media import HEIF  # noqa: F401  (registers the HEIC reader before anything decodes)


def main(argv=None):
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("Photo Finder")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("photofind")
    app.setDesktopFileName("photofind")
    from importlib.resources import files

    icon = files("photofind").parent / "photofind-icon.png"
    if icon.is_file():
        app.setWindowIcon(QIcon(str(icon)))

    from .main_window import MainWindow

    win = MainWindow()
    win.show()
    return app.exec()
