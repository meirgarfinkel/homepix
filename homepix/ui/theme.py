"""The look: colours, the font, the app-wide style sheet, and the toolbar's icons.

HomeDocs's look ("Paper"): cool greys with an apricot accent, in a dark version and a light one, the
toolbar and sidebar sharing one tint around a plain page, with no lines between them. Dark is the default;
Settings can pick light, or follow the system. Widgets that paint themselves read the colours from
here at paint time (`color("accent")`), so they follow a switch on their next repaint.
"""

import atexit
import math
import shutil
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPainterPath, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QAbstractSpinBox, QApplication, QComboBox, QMenu, QProxyStyle, QStyle, QStyledItemDelegate

from ..core.settings import load_settings, save_settings

FONT_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2])) / "homepix" / "ui" / "fonts"
FAMILY = "Plus Jakarta Sans"
WORD_SPACE = 0.14  # extra room between words, as a share of the text size
MODES = {"dark": "Dark", "light": "Light", "system": "Match the system"}

TOKENS = {
    "light": {
        "bg": "#FFFFFF",  # the photos
        "side": "#F5F7FA",  # the toolbar and sidebar, one tint
        "raised": "#FFFFFF",  # menus, plain buttons
        "field": "#EEF1F5",
        "border": "#E3E7ED",
        "text": "#1F2733",
        "muted": "#5D6979",
        "accent": "#966230",  # icons, focus rings, the slider
        "accentText": "#966230",
        "accentFill": "#966230",  # a button's fill, with white text
        "accentInk": "#FFFFFF",
        "accentSoft": "rgba(150,98,48,0.12)",
        "hover": "rgba(31,39,51,0.05)",
        "hoverStrong": "rgba(31,39,51,0.09)",
        "selected": "rgba(31,39,51,0.07)",  # the chosen sidebar row
        "sideText": "rgba(20,20,20,0.80)",  # neutral ink, not blue-grey on the tint
        "sideStrong": "rgba(20,20,20,0.95)",
        "ok": "#2E8A55",  # how many photos and videos
        "danger": "#C0392B",
        "yearNow": "#1F2733",  # the year on screen, in the years beside the photos
    },
    "dark": {
        "bg": "#14171C",
        "side": "#1A1E24",
        "raised": "#20252C",
        "field": "#242930",
        "border": "#2B3139",
        "text": "#E4E8EE",
        "muted": "#98A2AF",
        "accent": "#DDB088",
        "accentText": "#DDB088",
        "accentFill": "#966230",
        "accentInk": "#FFFFFF",
        "accentSoft": "rgba(221,176,136,0.16)",
        "hover": "rgba(255,255,255,0.05)",
        "hoverStrong": "rgba(255,255,255,0.10)",
        "selected": "rgba(255,255,255,0.06)",
        "sideText": "rgba(255,255,255,0.80)",
        "sideStrong": "rgba(255,255,255,0.95)",
        "ok": "#86D9A6",
        "danger": "#F0776A",
        "yearNow": "#FFFFFF",
    },
}

_tokens = TOKENS["dark"]
_dark = True


class _Signals(QObject):
    changed = Signal()  # the colours changed (redraw, rebuild icons)


signals = _Signals()


def _qcolor(value: str) -> QColor:
    if value.startswith("rgba("):
        r, g, b, a = (float(x) for x in value[5:-1].split(","))
        return QColor(int(r), int(g), int(b), round(a * 255))
    return QColor(value)


def color(name: str) -> QColor:
    return _qcolor(_tokens[name])


def font(px: float, weight=QFont.Weight.Normal) -> QFont:
    """The app's font at a size in (96 dpi) pixels, as points so it scales with the screen."""
    f = QFont(FAMILY)
    f.setPointSizeF(px * 0.75)
    f.setWeight(weight)
    f.setWordSpacing(WORD_SPACE * px)  # the font's own spaces are narrow: words ran together
    return f


def mode() -> str:
    m = load_settings().get("theme", "dark")
    return m if m in MODES else "dark"


def set_mode(m: str):
    s = load_settings()
    s["theme"] = m
    save_settings(s)
    apply(QApplication.instance())


def _fonts_loaded() -> bool:
    if FAMILY in QFontDatabase.families():
        return True
    for f in sorted(FONT_DIR.glob("*.ttf")):
        QFontDatabase.addApplicationFont(str(f))
    return FAMILY in QFontDatabase.families()


def apply(app, m: str | None = None):
    """Style the whole app: Fusion, the palette, the font, the style sheet."""
    global _tokens, _dark
    m = m or mode()
    if m == "system":
        _dark = app.styleHints().colorScheme() != Qt.ColorScheme.Light
        if not getattr(app, "_follows_scheme", False):
            app._follows_scheme = True
            app.styleHints().colorSchemeChanged.connect(lambda _: mode() == "system" and apply(app))
    else:
        _dark = m == "dark"
    _tokens = TOKENS["dark" if _dark else "light"]
    app.setStyle(_FlatStyle("Fusion"))
    app.setAttribute(Qt.ApplicationAttribute.AA_DontShowIconsInMenus)  # e.g. the system's Cut/Copy/Paste ones
    app.setPalette(palette())
    if _fonts_loaded():
        app.setFont(font(13))
    if not hasattr(app, "_popups"):
        app._popups = _Popups(app)
        app.installEventFilter(app._popups)
    app.setStyleSheet(style_sheet(dict(_tokens, arrow=_arrow_file(), arrowUp=_arrow_file("up"))))
    signals.changed.emit()


class _FlatStyle(QProxyStyle):
    """Fusion, with the app's own flat icons in message boxes in place of the system's glossy ones,
    and no icons on dialog buttons (Yes, No, OK, Cancel...)."""

    BOX_ICONS = {
        QStyle.StandardPixmap.SP_MessageBoxQuestion: ("question", "accent"),
        QStyle.StandardPixmap.SP_MessageBoxInformation: ("info", "accent"),
        QStyle.StandardPixmap.SP_MessageBoxWarning: ("warning", "danger"),
        QStyle.StandardPixmap.SP_MessageBoxCritical: ("error", "danger"),
    }

    def standardIcon(self, sp, option=None, widget=None):
        if sp in self.BOX_ICONS:
            name, colour = self.BOX_ICONS[sp]
            ic = QIcon()
            for px in (32, 48):
                ic.addPixmap(_pixmap(name, color(colour), px))
            return ic
        if sp == QStyle.StandardPixmap.SP_LineEditClearButton:  # the x in a search field
            return icon("clear", "muted")
        if sp in (QStyle.StandardPixmap.SP_ArrowLeft, QStyle.StandardPixmap.SP_ArrowBack):  # e.g. a calendar's
            return icon("back")
        if sp in (QStyle.StandardPixmap.SP_ArrowRight, QStyle.StandardPixmap.SP_ArrowForward):
            return icon("forward")
        return super().standardIcon(sp, option, widget)

    def styleHint(self, hint, option=None, widget=None, data=None):
        if hint in (QStyle.StyleHint.SH_DialogButtonBox_ButtonsHaveIcons, QStyle.StyleHint.SH_UnderlineShortcut):
            return 0  # no icons on buttons; no underlined letters (Alt+letter still works)
        return super().styleHint(hint, option, widget, data)


class _Popups(QObject):
    """Rounds the corners of menus and dropdown lists (their windows need a see-through background
    for that), and lets a dropdown's rows take the style sheet's padding. Hover tips are turned off."""

    def eventFilter(self, obj, e):
        if e.type() == QEvent.Type.ToolTip:
            return True  # no hover tips anywhere
        if e.type() == QEvent.Type.Polish:
            if isinstance(obj, QMenu):
                obj.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
                obj.setWindowFlag(Qt.WindowType.FramelessWindowHint)
                obj.setWindowFlag(Qt.WindowType.NoDropShadowWindowHint)
            elif isinstance(obj, QAbstractSpinBox) and not getattr(obj, "calendarPopup", lambda: False)():
                obj.lineEdit().setTextMargins(10, 0, 0, 0)  # the style sheet's padding is lost beside its arrows
            elif isinstance(obj, QComboBox) and type(obj.itemDelegate()) is not QStyledItemDelegate:
                obj.setItemDelegate(QStyledItemDelegate(obj))
        return False


_arrows = None  # a folder for the dropdown arrows, which a style sheet can only take as files


def _arrow_file(name: str = "down") -> str:
    global _arrows
    if _arrows is None:
        _arrows = Path(tempfile.mkdtemp(prefix="homepix-look-"))
        atexit.register(shutil.rmtree, _arrows, True)
    path = _arrows / f"{name}-{'dark' if _dark else 'light'}.png"
    if not path.exists():
        _pixmap(name, color("muted"), 14, 2.0).save(str(path))
    return path.as_posix()


def palette() -> QPalette:
    c = color
    pal = QPalette()
    R = QPalette.ColorRole
    for role, name in (
        (R.Window, "bg"),
        (R.Base, "bg"),
        (R.AlternateBase, "field"),
        (R.Button, "raised"),
        (R.WindowText, "text"),
        (R.Text, "text"),
        (R.ButtonText, "text"),
        (R.BrightText, "accentInk"),
        (R.PlaceholderText, "muted"),
        (R.Highlight, "accent"),
        (R.HighlightedText, "accentInk"),
        (R.ToolTipBase, "raised"),
        (R.ToolTipText, "text"),
        (R.Link, "accentText"),
        (R.LinkVisited, "accentText"),
        (R.Mid, "border"),
        (R.Dark, "border"),
        (R.Midlight, "field"),
        (R.Light, "raised"),
        (R.Shadow, "bg"),
    ):
        pal.setColor(role, c(name))
    pal.setColor(QPalette.ColorGroup.Disabled, R.Text, c("muted"))
    pal.setColor(QPalette.ColorGroup.Disabled, R.WindowText, c("muted"))
    pal.setColor(QPalette.ColorGroup.Disabled, R.ButtonText, c("muted"))
    return pal


def style_sheet(t: dict) -> str:
    return """
QMainWindow, QDialog {{ background: {bg}; }}
QToolTip {{ background: {raised}; color: {text}; border: 1px solid {border}; padding: 5px 8px; }}
QMainWindow::separator {{ background: {side}; width: 0; height: 0; }}

QToolBar#main {{ background: {side}; border: none; padding: 0 8px; spacing: 10px; }}
QToolBar#main QToolButton {{ background: transparent; border: none; border-radius: 14px; min-width: 28px;
                             min-height: 28px; padding: 0; color: {text}; }}
QToolBar#main QToolButton[label="true"] {{ padding: 0 12px; font-weight: 500; }}
QToolBar#main QToolButton:hover {{ background: {hover}; }}
QToolBar#main QToolButton:checked {{ background: {selected}; color: {text}; }}
QToolBar#main QToolButton#sidebarToggle:checked {{ background: transparent; }}
QToolBar#main QToolButton#sidebarToggle:hover {{ background: {hover}; }}
QToolBar#main QToolButton::menu-indicator {{ image: none; width: 0; }}
QLabel#brand {{ font-weight: 600; font-size: 14px; padding-left: 4px; }}
QLineEdit#search {{ background: {field}; border: 1px solid transparent; border-radius: 16px; min-height: 34px;
                    padding: 0 36px 0 36px; font-size: 14px; }}
QLineEdit#search:focus {{ border-color: {accent}; }}

QWidget#pageHeader {{ background: {bg}; }}
QLabel#pageTitle {{ font-size: 22px; font-weight: 600; }}
QLabel#pageCount {{ color: {ok}; }}
QLabel#pageCount[busy="true"] {{ color: {muted}; }}
QLabel#hint {{ color: {muted}; }}
QToolButton#pageBack {{ border: none; border-radius: 14px; padding: 4px 12px 4px 6px; color: {muted};
                        font-weight: 500; }}
QToolButton#pageBack:hover {{ background: {hover}; color: {text}; }}

QTreeWidget#sidebar {{ background: {side}; color: {sideText}; border: none; padding: 10px 8px; outline: 0; }}
QTreeWidget#sidebar::item {{ min-height: 28px; padding: 0 4px; border: none; }}
QTreeWidget#sidebar::item:first {{ border-top-left-radius: 14px; border-bottom-left-radius: 14px;
    padding-left: 8px; }}
QTreeWidget#sidebar::item:last {{ border-top-right-radius: 14px; border-bottom-right-radius: 14px;
    padding-right: 8px; }}
QTreeWidget#sidebar::item:hover {{ background: {hover}; }}
QTreeWidget#sidebar::item:selected {{ background: {selected}; color: {sideStrong}; }}
QTreeWidget#sidebar::branch {{ background: {side}; image: none; border-image: none; }}

QWidget#infoBody, QScrollArea#info {{ background: {side}; border: none; }}
QWidget#sidePanel {{ background: {side}; }}
QWidget#statusLine QLabel {{ color: {muted}; font-size: 12px; background: transparent; }}
QWidget#statusLine QProgressBar {{ background: {border}; border: none; border-radius: 1px; }}
QWidget#statusLine QProgressBar::chunk {{ background: {accent}; border-radius: 1px; }}
QToolButton#statusStop {{ border: none; border-radius: 9px; min-width: 18px; min-height: 18px; padding: 0; }}
QToolButton#statusStop:hover {{ background: {hover}; }}
QFrame#banner {{ background: {accentSoft}; border: none; }}

QPushButton {{ background: {raised}; color: {text}; border: 1px solid {border}; border-radius: 14px;
               padding: 6px 14px; font-weight: 500; }}
QPushButton:hover {{ background: {field}; }}
QPushButton:pressed {{ background: {border}; }}
QPushButton:default {{ background: {accentFill}; color: {accentInk}; border-color: {accentFill}; }}
QPushButton:disabled {{ color: {muted}; background: {side}; }}
QToolButton {{ border-radius: 8px; padding: 4px 8px; }}
QToolButton:hover {{ background: {hover}; }}

QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDateTimeEdit, QComboBox {{
    background: {field}; color: {text}; border: 1px solid transparent; border-radius: 10px; padding: 6px 10px;
    selection-background-color: {accentFill}; selection-color: {accentInk}; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QDateTimeEdit:focus, QComboBox:focus {{
    border-color: {accent}; }}
QComboBox {{ padding: 8px 12px; min-width: 120px; combobox-popup: 0; }}
QDateTimeEdit {{ padding: 8px 12px; }}
QDateEdit::drop-down {{ border: none; width: 32px; subcontrol-position: center right; }}
QDateEdit::down-arrow {{ image: url("{arrow}"); width: 14px; height: 14px; }}
QAbstractSpinBox {{ padding: 8px 4px 8px 2px; }}
QRadioButton {{ spacing: 8px; }}
QRadioButton::indicator {{ width: 14px; height: 14px; border-radius: 9px; border: 2px solid {muted};
    background: transparent; }}
QRadioButton::indicator:hover {{ border-color: {text}; }}
QRadioButton::indicator:checked {{ border: 5px solid {accent}; width: 8px; height: 8px; background: {raised}; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ subcontrol-origin: padding; width: 24px;
    border: none; background: transparent; }}
QAbstractSpinBox::up-button {{ subcontrol-position: top right; margin-top: 3px; }}
QAbstractSpinBox::down-button {{ subcontrol-position: bottom right; margin-bottom: 3px; }}
QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover {{ background: {hover};
    border-radius: 6px; }}
QAbstractSpinBox::up-arrow {{ image: url("{arrowUp}"); width: 12px; height: 12px; }}
QAbstractSpinBox::down-arrow {{ image: url("{arrow}"); width: 12px; height: 12px; }}
QAbstractSpinBox::up-arrow:disabled, QAbstractSpinBox::up-arrow:off,
QAbstractSpinBox::down-arrow:disabled, QAbstractSpinBox::down-arrow:off {{ image: none; }}
QDateEdit {{ padding: 8px 12px; }}
QPushButton:default:disabled {{ background: {side}; color: {muted}; border-color: {border}; }}
QCalendarWidget QWidget {{ alternate-background-color: {raised}; }}
QCalendarWidget {{ background: {raised}; border: 1px solid {border}; border-radius: 12px; }}
QCalendarWidget QWidget#qt_calendar_navigationbar {{ background: {raised}; border-radius: 12px; padding: 4px; }}
QCalendarWidget QToolButton {{ color: {text}; font-weight: 600; padding: 6px 10px; }}
QCalendarWidget QToolButton::menu-indicator {{ image: none; width: 0; }}
QCalendarWidget QToolButton#qt_calendar_prevmonth, QCalendarWidget QToolButton#qt_calendar_nextmonth {{
    qproperty-iconSize: 16px; }}
QCalendarWidget QSpinBox {{ padding: 2px 6px; }}
QCalendarWidget QAbstractItemView {{ background: {raised}; color: {text}; outline: 0; border: none;
    selection-background-color: {accent}; selection-color: {accentInk}; }}
QCalendarWidget QAbstractItemView:disabled {{ color: {muted}; }}
QComboBox:hover {{ border-color: {border}; }}
QComboBox::drop-down {{ border: none; width: 32px; subcontrol-position: center right; }}
QComboBox::down-arrow {{ image: url("{arrow}"); width: 14px; height: 14px; }}
QComboBox QAbstractItemView, QListView#popup {{ background: {raised}; color: {text}; border: 1px solid {border};
    border-radius: 12px; padding: 6px; outline: 0; }}
QComboBox QAbstractItemView::item, QListView#popup::item {{ min-height: 32px; padding: 0 12px; border-radius: 8px; }}
QComboBox QAbstractItemView::item:hover, QListView#popup::item:hover {{ background: {hover}; }}
QComboBox QAbstractItemView::item:selected, QListView#popup::item:selected {{ background: {accentSoft};
    color: {accentText}; }}
QDialog QListWidget, QDialog QTreeView, QDialog QTableView {{ background: {field}; border: none;
    border-radius: 10px; padding: 4px; outline: 0; }}
QDialog QListWidget::item {{ padding: 6px 8px; border-radius: 8px; }}
QDialog QListWidget::item:selected {{ background: {accentSoft}; color: {accentText}; }}

QListWidget#people {{ background: {bg}; border: none; outline: 0; padding: 8px 16px; }}
QListWidget#people::item {{ border: none; border-radius: 14px; padding: 6px; color: {text}; }}
QListWidget#people::item:hover {{ background: {hover}; }}
QListWidget#people::item:selected {{ background: {accentSoft}; color: {accentText}; }}

QMenu {{ background: {raised}; color: {text}; border: 1px solid {border}; border-radius: 14px; padding: 6px; }}
QMenu::item {{ padding: 9px 32px 9px 14px; border-radius: 8px; }}
QMenu::item:selected {{ background: {accentSoft}; color: {accentText}; }}
QMenu::item:disabled {{ color: {muted}; }}
QMenu::separator {{ height: 1px; background: {border}; margin: 5px 8px; }}
QWidget#menuRow {{ border-radius: 8px; background: transparent; }}
QWidget#menuRow[active="true"] {{ background: {accentSoft}; }}
QLabel#menuRowTitle {{ font-weight: 600; color: {text}; background: transparent; }}
QLabel#menuRowPlain {{ color: {text}; background: transparent; }}
QWidget#menuRow[active="true"] QLabel#menuRowTitle, QWidget#menuRow[active="true"] QLabel#menuRowPlain
    {{ color: {accentText}; }}
QLabel#menuRowKeys {{ color: {muted}; font-size: 12px; background: transparent; padding-left: 16px; }}
QWidget#menuRow QLabel:disabled {{ color: {muted}; }}
QLabel#menuRowText {{ font-size: 12px; color: {muted}; background: transparent; }}
QLabel#menuHeading {{ font-size: 11px; font-weight: 600; color: {muted}; padding: 10px 14px 4px;
    background: transparent; }}

QSlider::groove:horizontal {{ height: 4px; background: {border}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {accent}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {accent}; width: 14px; height: 14px; margin: -5px 0;
    border-radius: 7px; }}
QProgressBar {{ background: {field}; border: none; border-radius: 3px; max-height: 6px; }}
QProgressBar::chunk {{ background: {accent}; border-radius: 3px; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle {{ background: {border}; border-radius: 3px; min-height: 30px; min-width: 30px; }}
QScrollBar::handle:hover {{ background: {muted}; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none;
    width: 0; height: 0; }}
""".format(**t)


# --- icons -------------------------------------------------------------------------------------------


def _draw_icon(name: str, p: QPainter):  # noqa: C901 (one plain branch per icon)
    """On a 24 × 24 grid, in the pen already set."""
    if name == "sidebar":
        p.drawRoundedRect(QRectF(3.5, 5, 17, 14), 3, 3)
        p.drawLine(QPointF(9.5, 5), QPointF(9.5, 19))
    elif name == "info":
        p.drawEllipse(QPointF(12, 12), 8.5, 8.5)
        p.drawLine(QPointF(12, 11), QPointF(12, 16.5))
        p.drawPoint(QPointF(12, 7.8))
    elif name == "settings":  # a gear
        path = QPainterPath()
        for k in range(8):
            for da, r in ((-15, 6.6), (-8, 9), (8, 9), (15, 6.6)):
                a = math.radians(k * 45 + da)
                pt = QPointF(12 + r * math.cos(a), 12 + r * math.sin(a))
                if path.elementCount():
                    path.lineTo(pt)
                else:
                    path.moveTo(pt)
        path.closeSubpath()
        p.drawPath(path)
        p.drawEllipse(QPointF(12, 12), 2.8, 2.8)
    elif name == "check":
        path = QPainterPath(QPointF(5.5, 12.5))
        path.lineTo(10, 17)
        path.lineTo(18.5, 7.5)
        p.drawPath(path)
    elif name in ("undo", "redo"):
        p.save()
        if name == "redo":  # undo's mirror image
            p.translate(24, 0)
            p.scale(-1, 1)
        path = QPainterPath(QPointF(5, 10))
        path.lineTo(15, 10)
        path.cubicTo(21, 10, 21, 19, 15, 19)
        path.lineTo(10, 19)
        p.drawPath(path)
        path = QPainterPath(QPointF(9, 6))
        path.lineTo(5, 10)
        path.lineTo(9, 14)
        p.drawPath(path)
        p.restore()
    elif name == "restart":  # a circling arrow
        path = QPainterPath()
        path.arcMoveTo(QRectF(4.5, 4.5, 15, 15), 60)
        path.arcTo(QRectF(4.5, 4.5, 15, 15), 60, 300)
        p.drawPath(path)
        tip = QPainterPath(QPointF(15.8, 3.5))
        tip.lineTo(15.8, 7.6)
        tip.lineTo(11.7, 7.6)
        p.drawPath(tip)
    elif name == "fullscreen":  # four corners
        for x, y, dx, dy in ((4, 4, 1, 1), (20, 4, -1, 1), (4, 20, 1, -1), (20, 20, -1, -1)):
            path = QPainterPath(QPointF(x, y + 5 * dy))
            path.lineTo(x, y)
            path.lineTo(x + 5 * dx, y)
            p.drawPath(path)
    elif name == "update":  # an arrow down into a tray
        p.drawLine(QPointF(12, 4), QPointF(12, 15))
        path = QPainterPath(QPointF(7.5, 10.5))
        path.lineTo(12, 15)
        path.lineTo(16.5, 10.5)
        p.drawPath(path)
        path = QPainterPath(QPointF(4.5, 15))
        path.lineTo(4.5, 19.5)
        path.lineTo(19.5, 19.5)
        path.lineTo(19.5, 15)
        p.drawPath(path)
    elif name == "quit":  # a power symbol: a circle open at the top, a line down through the gap
        path = QPainterPath()
        ring = QRectF(3.5, 4.5, 17, 17)
        path.arcMoveTo(ring, 125)
        path.arcTo(ring, 125, 290)
        p.drawPath(path)
        p.drawLine(QPointF(12, 2.5), QPointF(12, 12.5))
    elif name == "calendar":
        p.drawRoundedRect(QRectF(4, 5.5, 16, 14.5), 2.5, 2.5)
        p.drawLine(QPointF(4, 10), QPointF(20, 10))
        p.drawLine(QPointF(8.5, 3.5), QPointF(8.5, 7))
        p.drawLine(QPointF(15.5, 3.5), QPointF(15.5, 7))
    elif name == "copies":
        p.drawRoundedRect(QRectF(8.5, 8.5, 11.5, 11.5), 2, 2)
        path = QPainterPath(QPointF(15.5, 8.5))
        path.lineTo(15.5, 6)
        path.quadTo(15.5, 4, 13.5, 4)
        path.lineTo(6, 4)
        path.quadTo(4, 4, 4, 6)
        path.lineTo(4, 13.5)
        path.quadTo(4, 15.5, 6, 15.5)
        path.lineTo(8.5, 15.5)
        p.drawPath(path)
    elif name == "trash":
        p.drawLine(QPointF(4.5, 7), QPointF(19.5, 7))
        path = QPainterPath(QPointF(6.5, 7))
        path.lineTo(7.5, 19.5)
        path.lineTo(16.5, 19.5)
        path.lineTo(17.5, 7)
        p.drawPath(path)
        path = QPainterPath(QPointF(9.5, 7))
        path.lineTo(9.5, 4.5)
        path.lineTo(14.5, 4.5)
        path.lineTo(14.5, 7)
        p.drawPath(path)
        p.drawLine(QPointF(10.5, 10.5), QPointF(10.5, 16))
        p.drawLine(QPointF(13.5, 10.5), QPointF(13.5, 16))
    elif name == "more":
        p.save()
        p.setBrush(p.pen().color())
        for x in (6, 12, 18):
            p.drawEllipse(QPointF(x, 12), 1.4, 1.4)
        p.restore()
    elif name == "back":
        path = QPainterPath(QPointF(14.5, 6))
        path.lineTo(8.5, 12)
        path.lineTo(14.5, 18)
        p.drawPath(path)
    elif name == "up":
        path = QPainterPath(QPointF(7, 14))
        path.lineTo(12, 9)
        path.lineTo(17, 14)
        p.drawPath(path)
    elif name == "forward":
        path = QPainterPath(QPointF(9.5, 6))
        path.lineTo(15.5, 12)
        path.lineTo(9.5, 18)
        p.drawPath(path)
    elif name == "question":
        p.drawEllipse(QPointF(12, 12), 8.5, 8.5)
        path = QPainterPath(QPointF(9.6, 9.7))
        path.cubicTo(9.6, 6.6, 14.6, 6.6, 14.6, 9.6)
        path.cubicTo(14.6, 11.6, 12, 11.7, 12, 13.6)
        p.drawPath(path)
        p.drawPoint(QPointF(12, 16.6))
    elif name == "warning":
        path = QPainterPath(QPointF(12, 4.5))
        path.lineTo(20.5, 19)
        path.lineTo(3.5, 19)
        path.closeSubpath()
        p.drawPath(path)
        p.drawLine(QPointF(12, 10), QPointF(12, 13.5))
        p.drawPoint(QPointF(12, 16.3))
    elif name == "error":
        p.drawEllipse(QPointF(12, 12), 8.5, 8.5)
        p.drawLine(QPointF(12, 7.5), QPointF(12, 12.8))
        p.drawPoint(QPointF(12, 16.2))
    elif name == "search":
        p.drawEllipse(QPointF(10.5, 10.5), 6, 6)
        p.drawLine(QPointF(15, 15), QPointF(19.5, 19.5))
    elif name == "clear":
        p.drawLine(QPointF(7, 7), QPointF(17, 17))
        p.drawLine(QPointF(17, 7), QPointF(7, 17))
    elif name == "down":
        path = QPainterPath(QPointF(7, 10))
        path.lineTo(12, 15)
        path.lineTo(17, 10)
        p.drawPath(path)
    elif name == "faces":
        p.drawEllipse(QPointF(12, 9), 3.6, 3.6)
        path = QPainterPath(QPointF(5, 19.5))
        path.cubicTo(6, 14.5, 18, 14.5, 19, 19.5)
        p.drawPath(path)


def _pixmap(name: str, col: QColor, px: int = 20, dpr: float = 2.0) -> QPixmap:
    pm = QPixmap(round(px * dpr), round(px * dpr))
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(px * dpr / 24, px * dpr / 24)
    pen = QPen(col, 1.8)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    _draw_icon(name, p)
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def icon(name: str, colour: str = "text", checked: str = "accentText") -> QIcon:
    """In the text colour (or another), and the accent's (or another) when checked."""
    ic = QIcon()
    ic.addPixmap(_pixmap(name, color(colour)), QIcon.Mode.Normal, QIcon.State.Off)
    ic.addPixmap(_pixmap(name, color(checked)), QIcon.Mode.Normal, QIcon.State.On)
    ic.addPixmap(_pixmap(name, color("muted")), QIcon.Mode.Disabled, QIcon.State.Off)
    return ic


def brand_mark(px: int = 28, dpr: float = 2.0) -> QPixmap:
    """The app icon (homepix-icon.svg): a roof with a chimney over a camera lens, on a dark rounded
    square. Drawn on a 64-unit grid, the same as the SVG."""
    pm = QPixmap(round(px * dpr), round(px * dpr))
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(dpr * px / 64, dpr * px / 64)
    roof_colour, lens_colour = QColor("#E4E7EA"), QColor("#3AA9DB")
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#13212C"))
    p.drawRoundedRect(QRectF(0, 0, 64, 64), 15, 15)
    p.setBrush(roof_colour)
    p.drawRoundedRect(QRectF(40, 16.5, 4, 7.5), 0.8, 0.8)  # chimney

    def pen(col, width):
        pn = QPen(col, width)
        pn.setCapStyle(Qt.PenCapStyle.RoundCap)
        pn.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        return pn

    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(pen(roof_colour, 4))
    roof = QPainterPath(QPointF(12, 30.9))
    roof.lineTo(32, 15.3)
    roof.lineTo(52, 30.9)
    p.drawPath(roof)
    p.setPen(pen(lens_colour, 3.4))
    p.drawEllipse(QPointF(32, 39), 11, 11)
    p.setPen(pen(QColor("#FFFFFF"), 2.2))
    p.drawArc(QRectF(25.5, 32.5, 13, 13), 110 * 16, 50 * 16)  # the glare, top left inside the lens
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm
