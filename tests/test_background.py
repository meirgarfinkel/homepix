"""Keeping HomePix running in the background: the login entry, and the window hiding instead of quitting."""

import sys

import pytest

from homepix.core import background


@pytest.fixture
def autostart(pf, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("APPIMAGE", "/home/me/Apps/HomePix x86_64.AppImage")
    return tmp_path / "config" / "autostart" / "homepix.desktop"


def test_turning_it_on_starts_homepix_hidden_at_login_and_off_takes_that_away(autostart):
    assert not background.on()
    assert background.set_on(True) == ""
    assert background.on()
    text = autostart.read_text()
    assert 'Exec="/home/me/Apps/HomePix x86_64.AppImage" --background\n' in text
    assert "TryExec=/home/me/Apps/HomePix x86_64.AppImage\n" in text  # (deleted: skipped at login, no error)
    assert background.set_on(False) == ""
    assert not background.on()
    assert not autostart.exists()


def test_the_login_entry_follows_homepix_when_it_moves(autostart, monkeypatch):
    background.set_on(True)
    monkeypatch.setenv("APPIMAGE", "/opt/HomePix.AppImage")
    background.refresh()  # (each start)
    assert "Exec=/opt/HomePix.AppImage --background\n" in autostart.read_text()


def test_on_windows_a_hidden_script_in_the_startup_folder(pf, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.delenv("APPIMAGE", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Program Files\HomePix\HomePix.exe")
    background.set_on(True)
    script = tmp_path / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "HomePix.vbs"
    assert script.read_bytes() == (
        b'CreateObject("WScript.Shell").Run '
        b'"""C:\\Program Files\\HomePix\\HomePix.exe"" ""--background""", 0, False\r\n'
    )


def test_closing_the_window_hides_it_when_kept_running_and_quit_quits(app, pf, autostart):
    from conftest import open_window

    background.set_on(True)
    w = open_window(app, ready=None)
    closed = []
    w.closing.connect(lambda: closed.append(True))
    w.close()
    assert not w.isVisible() and not closed  # still running, just hidden
    w.bring_forward()  # (opening HomePix from the menu)
    assert w.isVisible()
    w.quit_app()  # File > Quit
    assert closed and not w.isVisible()


def test_closing_the_window_quits_when_not_kept_running(app, pf, autostart):
    from conftest import open_window

    w = open_window(app, ready=None)
    closed = []
    w.closing.connect(lambda: closed.append(True))
    w.close()
    assert closed


def test_a_tray_icon_shows_while_kept_running(app, pf, autostart, monkeypatch):
    from conftest import open_window
    from PySide6.QtWidgets import QSystemTrayIcon

    from homepix.ui.views.settings_dialog import SettingsDialog

    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: True))
    w = open_window(app, ready=None)
    try:
        assert w.tray.icon is None  # (off by default)
        dlg = SettingsDialog(w)
        dlg.background_changed.connect(w.tray.update)
        dlg.keep_running.setChecked(True)
        assert w.tray.icon is not None
        assert [a.text() for a in w.tray.icon.contextMenu().actions() if a.text()] == ["Open HomePix", "Quit"]
        w.hide()
        w.tray._clicked(QSystemTrayIcon.ActivationReason.Trigger)  # a click on it opens the window
        assert w.isVisible()
        dlg.keep_running.setChecked(False)
        assert w.tray.icon is None
        dlg.close()
    finally:
        w.quit_app()


def test_no_tray_icon_where_the_desktop_has_no_tray(app, pf, autostart, monkeypatch):
    from conftest import open_window
    from PySide6.QtWidgets import QSystemTrayIcon

    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: False))
    background.set_on(True)
    w = open_window(app, ready=None)
    try:
        assert w.tray.icon is None
    finally:
        w.quit_app()
