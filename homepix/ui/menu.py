"""The toolbar's menu: a hamburger button that turns into an X while its menu is open, and the menu's
rows: an icon, a name (with a line under it saying what it does, for the main ones), and on the right
the shortcut, or a tick for a setting that's on."""

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPointF, Qt, QVariantAnimation
from PySide6.QtGui import QKeySequence, QPainter, QPen, QPixmap
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
    def __init__(self, action, description, icon, menu):
        super().__init__(menu)
        self.action, self.menu, self.icon_name = action, menu, icon
        self.item = None  # its place in the menu (set by add_row)
        self.setObjectName("menuRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setProperty("active", False)
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 8, 14, 8)
        row.setSpacing(12)
        self.icon = QLabel()
        self.icon.setFixedSize(20, 20)
        row.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop if description else Qt.AlignmentFlag.AlignVCenter)
        words = QVBoxLayout()
        words.setSpacing(2)
        self.title = QLabel(action.text())
        self.title.setObjectName("menuRowTitle" if description else "menuRowPlain")
        words.addWidget(self.title)
        self.description = QLabel(description)
        self.description.setObjectName("menuRowText")
        self.description.setVisible(bool(description))
        words.addWidget(self.description)
        row.addLayout(words, 1)
        self.right = QLabel()  # the shortcut, or a tick
        self.right.setObjectName("menuRowKeys")
        row.addWidget(self.right, 0, Qt.AlignmentFlag.AlignVCenter)
        action.changed.connect(self.refresh)
        self.refresh()

    def refresh(self):
        """After the action changed (on or off, enabled or not), or the row became active or not."""
        on = self.action.isEnabled()
        self.setEnabled(on)
        if self.item is not None:
            self.item.setEnabled(on)  # (the arrows pass over it while it's off)
        colour = "muted" if not on else "accentText" if self.property("active") else "text"
        self.icon.setPixmap(theme.icon(self.icon_name, colour).pixmap(20, 20))
        if self.action.isCheckable():
            self.right.setText("")
            self.right.setPixmap(theme.icon("check", colour).pixmap(18, 18) if self.action.isChecked() else QPixmap())
        else:
            self.right.setText(self.action.shortcut().toString(QKeySequence.SequenceFormat.NativeText))
        for w in (self, self.title, self.description, self.right):
            w.style().unpolish(w)
            w.style().polish(w)

    def set_active(self, on):
        if self.property("active") != on:
            self.setProperty("active", on)
            self.refresh()

    def enterEvent(self, e):  # noqa: N802 (Qt's name)
        if self.action.isEnabled():
            self.menu.setActiveAction(self.item)  # (the menu then lights this row: _RowMenu)

    def leaveEvent(self, e):  # noqa: N802 (Qt's name)
        self.set_active(False)

    def mouseReleaseEvent(self, e):  # noqa: N802 (Qt's name)
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.choose()

    def choose(self):
        if self.action.isEnabled():
            self.menu.close()
            self.action.trigger()


class _RowMenu(QObject):
    """Keeps the menu's rows lit as the menu's own items would be: the one under the mouse, or reached
    with the arrow keys (and Enter chooses it)."""

    def __init__(self, menu: QMenu):
        super().__init__(menu)
        self.menu = menu
        menu.hovered.connect(self._light)
        menu.aboutToShow.connect(lambda: self._light(None))
        menu.aboutToHide.connect(lambda: self._light(None))
        menu.installEventFilter(self)

    def rows(self):
        widgets = (a.defaultWidget() for a in self.menu.actions() if isinstance(a, QWidgetAction))
        return [w for w in widgets if isinstance(w, _Row)]

    def _light(self, action):
        for row in self.rows():
            row.set_active(action is not None and row.item is action)

    def eventFilter(self, obj, e):  # noqa: N802 (Qt's name)
        if e.type() == QEvent.Type.KeyPress and e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            active = self.menu.activeAction()
            for row in self.rows():
                if row.item is active:
                    row.choose()
                    return True
        return False


def add_heading(menu: QMenu, text: str):
    label = QLabel(text.upper())
    label.setObjectName("menuHeading")
    item = QWidgetAction(menu)
    item.setDefaultWidget(label)
    item.setEnabled(False)  # (a label: the arrows pass over it)
    menu.addAction(item)


def add_row(menu: QMenu, action, icon: str, description: str = ""):
    """action, as a row (choosing it triggers action)."""
    if not hasattr(menu, "_rows"):
        menu._rows = _RowMenu(menu)
    item = QWidgetAction(menu)
    row = _Row(action, description, icon, menu)
    row.item = item
    item.setDefaultWidget(row)
    row.refresh()
    menu.addAction(item)
    return item
