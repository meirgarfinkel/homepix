"""Small pieces more than one part of the window uses."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QSlider, QStyle, QStyleOptionSlider


class JumpSlider(QSlider):
    """A slider that jumps to where it's clicked (rather than a step toward it), and drags from there."""

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            opt = QStyleOptionSlider()
            self.initStyleOption(opt)
            style = self.style()
            groove = style.subControlRect(QStyle.ComplexControl.CC_Slider, opt, QStyle.SubControl.SC_SliderGroove, self)
            handle = style.subControlRect(QStyle.ComplexControl.CC_Slider, opt, QStyle.SubControl.SC_SliderHandle, self)
            if not handle.contains(e.position().toPoint()):
                span = groove.width() - handle.width()
                x = round(e.position().x()) - groove.x() - handle.width() // 2
                self.setValue(QStyle.sliderValueFromPosition(self.minimum(), self.maximum(), x, span, opt.upsideDown))
        super().mousePressEvent(e)  # (now on the handle: a drag carries on from here)


def toggle_full_screen(window):
    if window.isFullScreen():
        leave_full_screen(window)
    else:
        window.setProperty("wasMaximized", window.isMaximized())
        window.showFullScreen()


def leave_full_screen(window):
    """Back to the window it was: maximized stays maximized."""
    if window.isFullScreen():
        window.showMaximized() if window.property("wasMaximized") else window.showNormal()
