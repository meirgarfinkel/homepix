"""An icon in the system tray (the top bar's status area) while HomePix is kept running in the
background, so it can be seen running with the window closed: clicking it opens the window; its menu
has Open HomePix and Quit. Not shown when the desktop has no tray (plain GNOME without its extension)."""

from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from ..core import background


class Tray:
    def __init__(self, window):
        self.w = window
        self.icon = None

    def update(self):
        """Show the icon when HomePix is set to keep running (and there's a tray), else take it away."""
        want = background.on() and QSystemTrayIcon.isSystemTrayAvailable()
        if want and self.icon is None:
            icon = QSystemTrayIcon(QApplication.windowIcon(), self.w)
            menu = QMenu(self.w)
            menu.addAction("Open HomePix", self.w.bring_forward)
            menu.addSeparator()
            menu.addAction("Quit", self.w.quit_app)
            icon.setContextMenu(menu)
            icon.activated.connect(self._clicked)
            icon.show()
            self.icon, self._menu = icon, menu
        elif not want and self.icon is not None:
            self.icon.hide()
            self.icon.deleteLater()
            self.icon = None

    def _clicked(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.w.bring_forward()
