"""The toolbar's menu: a hamburger button that turns into an X while its menu is open, and the menu's
rows that say what they do (a name, and a line under it)."""

from PySide6.QtCore import QEasingCurve, QPointF, Qt, QVariantAnimation
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QStyle,
    QStyleOptionToolButton,
    QStylePainter,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from . import theme


class MenuButton(QToolButton):
    """Three lines that turn into an X while the menu is open (and back when it closes)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.progress = 0.0  # 0: three lines, 1: an X
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(240)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._anim.valueChanged.connect(self._step)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)

    def setMenu(self, menu):  # noqa: N802 (Qt's name)
        super().setMenu(menu)
        menu.aboutToShow.connect(lambda: self._go(1.0))
        menu.aboutToHide.connect(lambda: self._go(0.0))

    def _go(self, to):
        self._anim.stop()
        self._anim.setStartValue(self.progress)
        self._anim.setEndValue(to)
        self._anim.start()

    def _step(self, value):
        self.progress = float(value)
        self.update()

    def sizeHint(self):  # noqa: N802 (Qt's name)
        return self.iconSize().grownBy(self.contentsMargins()).expandedTo(super().sizeHint())

    def paintEvent(self, e):  # noqa: N802 (Qt's name)
        p = QStylePainter(self)
        opt = QStyleOptionToolButton()
        self.initStyleOption(opt)
        opt.icon, opt.text = type(opt.icon)(), ""
        opt.features &= ~QStyleOptionToolButton.ToolButtonFeature.HasMenu  # (no arrow)
        p.drawComplexControl(QStyle.ComplexControl.CC_ToolButton, opt)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = self.iconSize().width()
        p.translate((self.width() - side) / 2, (self.height() - side) / 2)
        p.scale(side / 24, side / 24)
        pen = QPen(theme.color("text"), 1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        # first the top and bottom lines slide to the middle (the middle one fading), then they turn
        slide = min(self.progress * 2, 1.0)
        turn = max(self.progress * 2 - 1, 0.0)
        half = 7.5 - 0.9 * turn  # the X's arms a little shorter than the lines (they're diagonal)
        for y, angle in ((6.5, 45), (17.5, -45)):
            p.save()
            p.translate(12, y + (12 - y) * slide)
            p.rotate(angle * turn)
            p.drawLine(QPointF(-half, 0), QPointF(half, 0))
            p.restore()
        if slide < 1:
            p.setOpacity(1 - slide)
            p.drawLine(QPointF(4.5, 12), QPointF(19.5, 12))


class _Row(QWidget):
    """A menu row with a line under its name saying what it does."""

    def __init__(self, action, description, icon, menu):
        super().__init__(menu)
        self.action, self.menu = action, menu
        self.item = None  # its place in the menu (set by add_row)
        self.setObjectName("menuRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setProperty("active", False)
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 8, 24, 8)
        row.setSpacing(12)
        self.icon = QLabel()
        self.icon.setFixedSize(20, 20)
        self.icon_name = icon
        row.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)
        words = QVBoxLayout()
        words.setSpacing(2)
        self.title = QLabel(action.text())
        self.title.setObjectName("menuRowTitle")
        self.description = QLabel(description)
        self.description.setObjectName("menuRowText")
        words.addWidget(self.title)
        words.addWidget(self.description)
        row.addLayout(words, 1)
        self._paint_icon()

    def _paint_icon(self):
        colour = "accentText" if self.property("active") else "text"
        self.icon.setPixmap(theme.icon(self.icon_name, colour).pixmap(20, 20))

    def _set_active(self, on):
        if self.property("active") != on:
            self.setProperty("active", on)
            for w in (self, self.title, self.description):
                w.style().unpolish(w)
                w.style().polish(w)
            self._paint_icon()

    def enterEvent(self, e):  # noqa: N802 (Qt's name)
        self.menu.setActiveAction(self.item)
        self.setFocus()
        self._set_active(True)

    def leaveEvent(self, e):  # noqa: N802 (Qt's name)
        if not self.hasFocus():
            self._set_active(False)

    def focusInEvent(self, e):  # noqa: N802 (Qt's name)
        self._set_active(True)

    def focusOutEvent(self, e):  # noqa: N802 (Qt's name)
        self._set_active(False)

    def mouseReleaseEvent(self, e):  # noqa: N802 (Qt's name)
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.choose()

    def keyPressEvent(self, e):  # noqa: N802 (Qt's name)
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.choose()
        else:
            e.ignore()  # (the arrows and Esc are the menu's)

    def choose(self):
        self.menu.close()
        self.action.trigger()


def add_heading(menu: QMenu, text: str):
    label = QLabel(text.upper())
    label.setObjectName("menuHeading")
    item = QWidgetAction(menu)
    item.setDefaultWidget(label)
    item.setEnabled(False)  # (a label: the arrows pass over it)
    menu.addAction(item)


def add_row(menu: QMenu, action, description: str, icon: str):
    """action, as a row with its description under it (choosing it triggers action)."""
    item = QWidgetAction(menu)
    row = _Row(action, description, icon, menu)
    row.item = item
    item.setDefaultWidget(row)
    menu.addAction(item)
    return item
