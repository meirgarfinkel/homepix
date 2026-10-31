"""Opening a photo from the file manager ("Open With > HomePix")."""

import os

import pytest
from conftest import open_window, photo, wait_until

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")


def test_only_photos_and_videos_are_taken_from_the_arguments(tmp_path):
    from homepix.ui.open_files import given_files

    a = photo(tmp_path / "my photo.jpg")
    (tmp_path / "notes.txt").write_text("hi")
    url = "file://" + str(a).replace(" ", "%20")
    assert given_files([str(a), str(tmp_path / "notes.txt"), "--flag", str(tmp_path / "gone.jpg"), url]) == [
        str(a),
        str(a),
    ]


def test_the_menu_entry_says_which_files_it_opens(pf, tmp_path, monkeypatch):
    from homepix.ui import app

    monkeypatch.setenv("APPIMAGE", "/apps/HomePix.AppImage")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    monkeypatch.setattr("shutil.which", lambda name: None)
    app.add_menu_entry()
    text = (tmp_path / "share" / "applications" / "homepix.desktop").read_text()
    assert 'Exec="/apps/HomePix.AppImage" %F' in text
    assert "MimeType=image/jpeg;" in text and "image/heic;" in text and "video/mp4;" in text


@pytest.fixture
def window(pf, app):
    w = open_window(app, ready=None, index=True, size=(900, 600))
    yield w
    w.close()


def test_a_photo_in_the_library_opens_large_with_its_folder(window, pf, lib, app):
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        photo(lib / "trip" / name)
    photo(lib / "trip" / "inside" / "d.jpg")  # a folder below: not stepped through
    photo(lib / "other.jpg")
    pf.add_folder(lib)
    pf.index_folder(lib, want_faces=False)
    window.reload()
    wait_until(app, lambda: len(window.timeline.items) == 5)
    window.opener.open([str(lib / "trip" / "b.jpg")])
    wait_until(app, lambda: window.stack.currentWidget() is window.viewer)
    v = window.viewer
    assert sorted(os.path.basename(it.path) for it in v.items) == ["a.jpg", "b.jpg", "c.jpg"]
    assert v.items[v.index].path == str(lib / "trip" / "b.jpg")
    window.close_viewer()
    assert window.stack.currentWidget() is window.timeline


def test_a_photo_outside_the_library_offers_to_add_its_folder(window, pf, lib, app, tmp_path, monkeypatch):
    photo(lib / "home.jpg")
    pf.add_folder(lib)
    pf.index_folder(lib, want_faces=False)
    outside = photo(tmp_path / "Downloads" / "new.jpg")
    asked = []

    def yes(*a, **k):
        asked.append(a[2])
        return QtWidgets.QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QtWidgets.QMessageBox, "question", staticmethod(yes))
    window.opener.open([str(outside)])
    wait_until(app, lambda: window.stack.currentWidget() is window.viewer, timeout=30)
    assert "isn't in your library" in asked[0]
    assert str(tmp_path / "Downloads") in pf.library_folders()
    assert window.viewer.items[window.viewer.index].path == str(outside)


def test_a_second_launch_hands_its_photo_to_the_open_window(window, pf, lib, app, monkeypatch):
    from homepix.ui import app as app_module

    photo(lib / "a.jpg")
    pf.add_folder(lib)
    pf.index_folder(lib, want_faces=False)
    monkeypatch.setattr(app_module, "_server_name", lambda: f"homepix-test-{os.getpid()}")
    server = app_module.single_instance()
    assert server is not None
    server.newConnection.connect(lambda: app_module._listen(server.nextPendingConnection(), window))
    assert app_module.single_instance([str(lib / "a.jpg")]) is None  # already open: handed over
    wait_until(app, lambda: window.stack.currentWidget() is window.viewer)
    assert window.viewer.items[0].path == str(lib / "a.jpg")
    server.close()
