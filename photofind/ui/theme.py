"""The look: colours, the font, the app-wide style sheet, and the toolbar's icons.

One design (Gallery, with the Ocean accent) in a dark and a light version. Dark is the default;
Settings can pick light, or follow the system. Widgets that paint themselves read the colours from
here at paint time (`color("accent")`), so they follow a switch on their next repaint.
"""

import sys
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPainterPath, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QApplication

from ..core import load_settings, save_settings

FONT_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2])) / "photofind" / "ui" / "fonts"
FAMILY = "Plus Jakarta Sans"
MODES = {"dark": "Dark", "light": "Light", "system": "Match the system"}

TOKENS = {
    "light": {
        "bg": "#FFFFFF", "side": "#F5F6F8", "raised": "#FFFFFF", "field": "#EEF0F3", "border": "#E4E7EB",
        "text": "#161A1F", "muted": "#5B6470", "accent": "#0A7DAD", "accentInk": "#FFFFFF",
        "accentSoft": "rgba(10,125,173,0.11)", "accentText": "#086A93", "hover": "rgba(22,26,31,0.06)",
        "danger": "#C0392B",
    },
    "dark": {
        "bg": "#0E1014", "side": "#15181D", "raised": "#1D2127", "field": "#1D2127", "border": "#262B32",
        "text": "#E7EAEE", "muted": "#9AA3AE", "accent": "#3AA9DB", "accentInk": "#04202C",
        "accentSoft": "rgba(58,169,219,0.16)", "accentText": "#7FCBEA", "hover": "rgba(231,234,238,0.06)",
        "danger": "#F0776A",
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


def is_dark() -> bool:
    return _dark


def font(px: float, weight=QFont.Weight.Normal) -> QFont:
    """The app's font at a size in (96 dpi) pixels, as points so it scales with the screen."""
    f = QFont(FAMILY)
    f.setPointSizeF(px * 0.75)
    f.setWeight(weight)
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
    app.setStyle("Fusion")
    app.setPalette(palette())
    if _fonts_loaded():
        app.setFont(font(13))
    app.setStyleSheet(style_sheet(_tokens))
    signals.changed.emit()


def palette() -> QPalette:
    c = color
    pal = QPalette()
    R = QPalette.ColorRole
    for role, name in ((R.Window, "bg"), (R.Base, "bg"), (R.AlternateBase, "field"), (R.Button, "raised"),
                       (R.WindowText, "text"), (R.Text, "text"), (R.ButtonText, "text"), (R.BrightText, "accentInk"),
                       (R.PlaceholderText, "muted"), (R.Highlight, "accent"), (R.HighlightedText, "accentInk"),
                       (R.ToolTipBase, "raised"), (R.ToolTipText, "text"), (R.Link, "accentText"),
                       (R.LinkVisited, "accentText"), (R.Mid, "border"), (R.Dark, "border"), (R.Midlight, "field"),
                       (R.Light, "raised"), (R.Shadow, "bg")):
        pal.setColor(role, c(name))
    pal.setColor(QPalette.ColorGroup.Disabled, R.Text, c("muted"))
    pal.setColor(QPalette.ColorGroup.Disabled, R.WindowText, c("muted"))
    pal.setColor(QPalette.ColorGroup.Disabled, R.ButtonText, c("muted"))
    return pal


def style_sheet(t: dict) -> str:
    return """
QMainWindow, QDialog {{ background: {bg}; }}
QToolTip {{ background: {raised}; color: {text}; border: 1px solid {border}; padding: 5px 8px; }}
QMainWindow::separator {{ background: {border}; width: 1px; height: 1px; }}

QToolBar#main {{ background: {bg}; border: none; border-bottom: 1px solid {border}; padding: 0 14px;
                 spacing: 6px; }}
QToolBar#main QToolButton {{ background: transparent; border: none; border-radius: 19px; min-width: 38px;
                             min-height: 38px; padding: 0; color: {text}; }}
QToolBar#main QToolButton[label="true"] {{ padding: 0 14px; font-weight: 600; }}
QToolBar#main QToolButton:hover {{ background: {hover}; }}
QToolBar#main QToolButton:checked {{ background: {accentSoft}; color: {accentText}; }}
QToolBar#main QToolButton::menu-indicator {{ image: none; width: 0; }}
QLabel#brand {{ font-weight: 700; font-size: 15px; padding-left: 4px; }}
QLineEdit#search {{ background: {field}; border: 1px solid transparent; border-radius: 20px; min-height: 38px;
                    padding: 0 40px 0 40px; font-size: 14px; }}
QLineEdit#search:focus {{ border-color: {accent}; background: {bg}; }}

QWidget#pageHeader {{ background: {bg}; }}
QLabel#pageTitle {{ font-size: 26px; font-weight: 700; }}
QLabel#pageCount {{ color: {muted}; }}

QTreeWidget#sidebar {{ background: {side}; border: none; padding: 10px 10px; outline: 0; }}
QTreeWidget#sidebar::item {{ min-height: 36px; padding: 0 4px; border: none; }}
QTreeWidget#sidebar::item:first {{ border-top-left-radius: 18px; border-bottom-left-radius: 18px; padding-left: 10px; }}
QTreeWidget#sidebar::item:last {{ border-top-right-radius: 18px; border-bottom-right-radius: 18px; padding-right: 12px; }}
QTreeWidget#sidebar::item:hover {{ background: {hover}; }}
QTreeWidget#sidebar::item:selected {{ background: {accentSoft}; color: {accentText}; }}
QTreeWidget#sidebar::branch {{ background: {side}; image: none; border-image: none; }}

QWidget#infoBody, QScrollArea#info {{ background: {side}; border: none; }}
QStatusBar {{ background: {side}; color: {muted}; border-top: 1px solid {border}; }}
QStatusBar::item {{ border: none; }}
QStatusBar QLabel {{ color: {muted}; }}
QFrame#banner {{ background: {accentSoft}; border: none; border-bottom: 1px solid {border}; }}

QPushButton {{ background: {raised}; color: {text}; border: 1px solid {border}; border-radius: 10px;
               padding: 7px 16px; font-weight: 600; }}
QPushButton:hover {{ background: {field}; }}
QPushButton:pressed {{ background: {border}; }}
QPushButton:default {{ background: {accent}; color: {accentInk}; border-color: {accent}; border-radius: 17px; }}
QPushButton:disabled {{ color: {muted}; background: {side}; }}
QToolButton {{ border-radius: 8px; padding: 4px 8px; }}
QToolButton:hover {{ background: {hover}; }}

QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDateTimeEdit, QComboBox {{
    background: {field}; color: {text}; border: 1px solid transparent; border-radius: 10px; padding: 6px 10px;
    selection-background-color: {accent}; selection-color: {accentInk}; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QDateTimeEdit:focus, QComboBox:focus {{
    border-color: {accent}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{ background: {raised}; border: 1px solid {border}; padding: 4px;
                               selection-background-color: {accentSoft}; selection-color: {accentText}; }}
QDialog QListWidget, QDialog QTreeView, QDialog QTableView {{ background: {field}; border: none; border-radius: 10px; padding: 4px;
                                            outline: 0; }}
QDialog QListWidget::item {{ padding: 6px 8px; border-radius: 8px; }}
QDialog QListWidget::item:selected {{ background: {accentSoft}; color: {accentText}; }}

QListWidget#people {{ background: {bg}; border: none; outline: 0; padding: 8px 16px; }}
QListWidget#people::item {{ border: none; border-radius: 14px; padding: 6px; color: {text}; }}
QListWidget#people::item:hover {{ background: {hover}; }}
QListWidget#people::item:selected {{ background: {accentSoft}; color: {accentText}; }}

QMenu {{ background: {raised}; color: {text}; border: 1px solid {border}; padding: 6px; }}
QMenu::item {{ padding: 7px 28px 7px 14px; border-radius: 6px; }}
QMenu::item:selected {{ background: {accentSoft}; color: {accentText}; }}
QMenu::item:disabled {{ color: {muted}; }}
QMenu::separator {{ height: 1px; background: {border}; margin: 5px 8px; }}

QSlider::groove:horizontal {{ height: 4px; background: {border}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {accent}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {accent}; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}
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

def _draw(name: str, p: QPainter):
    """On a 24 × 24 grid, in the pen already set."""
    if name == "sidebar":
        p.drawRoundedRect(QRectF(3.5, 5, 17, 14), 3, 3)
        p.drawLine(QPointF(9.5, 5), QPointF(9.5, 19))
    elif name == "info":
        p.drawEllipse(QPointF(12, 12), 8.5, 8.5)
        p.drawLine(QPointF(12, 11), QPointF(12, 16.5))
        p.drawPoint(QPointF(12, 7.8))
    elif name == "settings":  # two sliders
        for y, x in ((8.5, 15), (15.5, 9)):
            p.drawLine(QPointF(4, y), QPointF(20, y))
            p.save()
            p.setBrush(p.pen().color())
            p.drawEllipse(QPointF(x, y), 2.4, 2.4)
            p.restore()
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
    elif name == "search":
        p.drawEllipse(QPointF(10.5, 10.5), 6, 6)
        p.drawLine(QPointF(15, 15), QPointF(19.5, 19.5))
    elif name == "clear":
        p.drawLine(QPointF(7, 7), QPointF(17, 17))
        p.drawLine(QPointF(17, 7), QPointF(7, 17))
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
    _draw(name, p)
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def icon(name: str, colour: str = "text") -> QIcon:
    """In the text colour (or another), and the accent's when checked."""
    ic = QIcon()
    ic.addPixmap(_pixmap(name, color(colour)), QIcon.Mode.Normal, QIcon.State.Off)
    ic.addPixmap(_pixmap(name, color("accentText")), QIcon.Mode.Normal, QIcon.State.On)
    ic.addPixmap(_pixmap(name, color("muted")), QIcon.Mode.Disabled, QIcon.State.Off)
    return ic


def brand_mark(px: int = 28, dpr: float = 2.0) -> QPixmap:
    """A rounded square in the accent with a sun over hills."""
    pm = QPixmap(round(px * dpr), round(px * dpr))
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(dpr * px / 28, dpr * px / 28)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color("accent"))
    p.drawRoundedRect(QRectF(0, 0, 28, 28), 8, 8)
    p.setBrush(color("accentInk"))
    p.drawEllipse(QPointF(18.5, 9.5), 2.6, 2.6)
    hills = QPainterPath(QPointF(5, 21))
    hills.lineTo(11, 13)
    hills.lineTo(15, 18)
    hills.lineTo(17.5, 15.5)
    hills.lineTo(23, 21)
    hills.closeSubpath()
    p.drawPath(hills)
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm
