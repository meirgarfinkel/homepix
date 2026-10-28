"""The look: dark by default, light or the system's when chosen, and the bundled font."""

import pytest
from PySide6 import QtWidgets
from PySide6.QtGui import QPalette

from photofind.core import load_settings
from photofind.ui import theme


@pytest.fixture
def app(pf):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app
    app.setStyleSheet("")


def test_dark_by_default_and_switches(app):
    theme.apply(app)
    assert theme.mode() == "dark" and theme.is_dark()
    assert app.palette().color(QPalette.ColorRole.Window).name() == "#0e1014"
    theme.set_mode("light")
    assert load_settings()["theme"] == "light"
    assert not theme.is_dark()
    assert app.palette().color(QPalette.ColorRole.Window).name() == "#ffffff"
    assert theme.color("accent").name() == "#0a7dad"
    theme.set_mode("dark")


def test_follows_the_system(app):
    theme.apply(app, "system")  # offscreen reports no preference: dark unless it says light
    assert theme.is_dark() == (app.styleHints().colorScheme().name != "Light")
    theme.apply(app, "dark")


def test_the_font_is_bundled(app):
    theme.apply(app)
    assert app.font().family() == theme.FAMILY


def test_soft_colours_keep_their_transparency():
    assert theme._qcolor("rgba(10,125,173,0.11)").alpha() == 28
