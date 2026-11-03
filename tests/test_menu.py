"""The toolbar's menu: one list (no submenus), rows that say what they do, and the button's lines
turning into an X while it's open."""

from conftest import open_window, wait_until
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QToolBar, QWidgetAction


def _rows(menu):
    return {
        a.defaultWidget().title.text(): a.defaultWidget()
        for a in menu.actions()
        if isinstance(a, QWidgetAction) and hasattr(a.defaultWidget(), "title")
    }


def test_one_list_with_described_rows(pf, app, monkeypatch):
    w = open_window(app)
    try:
        m = w.more_menu
        assert not any(a.menu() for a in m.actions())  # no submenus
        rows = _rows(m)
        assert list(rows) == [
            "Organize by date...",
            "Duplicates",
            "Declutter",
            "Name faces",
            "Show names on faces",
            "Start faces over...",
            "Undo",
            "Redo",
            "Full screen",
            "Settings...",
            "Check for updates",
            "Quit",
        ]
        described = [t for t, r in rows.items() if r.description.text()]
        assert described == ["Organize by date...", "Duplicates", "Declutter", "Name faces"]
        unticked = {t: r for t, r in rows.items() if t != "Show names on faces"}
        assert all(not r.icon.pixmap().isNull() for r in unticked.values())  # an icon on every other row
        assert rows["Settings..."].right.text()  # its shortcut
        organized = []
        monkeypatch.setattr(w.organizer, "preview", organized.append)
        rows["Organize by date..."].choose()
        assert organized == [None]  # the whole library
    finally:
        w.close()


def test_settings_only_in_the_menu(pf, app):
    w = open_window(app)
    try:
        toolbar = w.findChild(QToolBar, "main")
        assert w.settings_action not in toolbar.actions()
        assert w.settings_action in w.actions()  # Ctrl+, still works
    finally:
        w.close()


def test_a_tick_while_names_are_shown(pf, app):
    w = open_window(app)
    try:
        row = _rows(w.more_menu)["Show names on faces"]
        assert row.icon.pixmap().isNull()  # off: nothing
        w.viewer.toggle_faces()  # (as N in the viewer does)
        assert not row.icon.pixmap().isNull()  # on: a tick
        row.choose()
        assert not w.viewer.show_faces and row.icon.pixmap().isNull()
    finally:
        w.close()


def test_nothing_lit_until_pointed_at(pf, app):
    w = open_window(app)
    try:
        m = w.more_menu
        rows = _rows(m)
        QTimer.singleShot(50, m.close)
        w.more_button.showMenu()
        assert not any(r.property("active") for r in rows.values())
        m.setActiveAction(rows["Duplicates"].item)  # (what pointing at it, or the arrow keys, does)
        assert [t for t, r in rows.items() if r.property("active")] == ["Duplicates"]
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
