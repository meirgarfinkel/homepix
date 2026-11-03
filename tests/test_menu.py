"""The toolbar's menu: one list (no submenus), rows that say what they do, and the button's lines
turning into an X while it's open."""

from conftest import open_window, wait_until
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QWidgetAction


def test_one_list_with_described_rows(pf, app, monkeypatch):
    w = open_window(app)
    try:
        m = w.more_menu
        assert not any(a.menu() for a in m.actions())  # no submenus
        rows = {
            a.defaultWidget().title.text(): a.defaultWidget()
            for a in m.actions()
            if isinstance(a, QWidgetAction) and hasattr(a.defaultWidget(), "title")
        }
        assert list(rows) == ["Sort by date...", "Duplicates", "Declutter", "Name faces"]
        assert all(r.description.text() for r in rows.values())
        sorted_ = []
        monkeypatch.setattr(w.organizer, "preview", sorted_.append)
        rows["Sort by date..."].choose()
        assert sorted_ == [None]  # the whole library
    finally:
        w.close()


def test_lines_turn_into_an_x(pf, app):
    w = open_window(app)
    try:
        button, seen = w.more_button, []

        def look():
            seen.append(button.progress)
            if button.progress < 1:
                QTimer.singleShot(20, look)
            else:
                w.more_menu.close()

        QTimer.singleShot(0, look)
        button.showMenu()  # (returns once the menu closes)
        assert seen[-1] == 1.0
        wait_until(app, lambda: button.progress == 0.0)
    finally:
        w.close()
