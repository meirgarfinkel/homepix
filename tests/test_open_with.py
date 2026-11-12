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
    url = a.as_uri()
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
    assert "TryExec=/apps/HomePix.AppImage\n" in text  # (the AppImage deleted: the menu leaves it out)
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


def test_a_photo_outside_the_library_is_shown_without_adding_anything(window, pf, lib, app, tmp_path):
    photo(lib / "home.jpg")
    pf.add_folder(lib)
    pf.index_folder(lib, want_faces=False)
    window.reload()
    wait_until(app, lambda: len(window.timeline.items) == 1)
    downloads = tmp_path / "Downloads"
    for name in ("b.jpg", "A.jpg", "c.png"):
        photo(downloads / name)
    (downloads / "notes.txt").write_text("not a photo")
    window.opener.open([str(downloads / "b.jpg")])
    wait_until(app, lambda: window.stack.currentWidget() is window.viewer)
    v = window.viewer
    assert [os.path.basename(it.path) for it in v.items] == ["A.jpg", "b.jpg", "c.png"]  # by name
    assert v.items[v.index].path == str(downloads / "b.jpg")
    assert "isn't in your library" in window.statusBar().currentMessage()
    assert pf.library_folders() == [str(lib)]  # nothing added
    assert window.files.targets() == []  # only looked at: no renaming, moving or deleting it
    v.go(v.index + 1)
    assert v.items[v.index].path == str(downloads / "c.png")
    window.reload()  # the library changing meanwhile leaves it be
    wait_until(app, lambda: len(window.timeline.items) == 1)
    assert window.stack.currentWidget() is window.viewer and len(v.items) == 3
    window.close_viewer()
    assert window.stack.currentWidget() is window.timeline


def test_a_second_launch_hands_its_photo_to_the_open_window(window, pf, lib, app):
    import subprocess
    import sys

    from homepix.ui import app as app_module

    photo(lib / "a.jpg")
    pf.add_folder(lib)
    pf.index_folder(lib, want_faces=False)
    server = app_module.single_instance()
    assert server is not None
    server.newConnection.connect(lambda: app_module._listen(server.nextPendingConnection(), window))
    launch = (  # another HomePix starting, with a photo to open (as "Open With" starts it)
        "import sys; from PySide6.QtCore import QCoreApplication; a = QCoreApplication([]); "
        "from homepix.ui.app import single_instance; sys.exit(0 if single_instance([sys.argv[1]]) is None else 1)"
    )
    second = subprocess.Popen([sys.executable, "-c", launch, str(lib / "a.jpg")])
    wait_until(app, lambda: second.poll() is not None and window.stack.currentWidget() is window.viewer)
    assert second.returncode == 0  # it found this one open, handed the photo over and stopped
    assert window.viewer.items[0].path == str(lib / "a.jpg")
    server.close()


def test_a_launch_waits_for_a_copy_that_is_closing_then_opens(app):
    import subprocess
    import sys
    import time

    from homepix.ui import app as app_module

    closing = app_module.single_instance()  # a copy still finishing up: it never answers
    assert closing is not None
    launch = (
        "import sys; from PySide6.QtCore import QCoreApplication; a = QCoreApplication([]); "
        "from homepix.ui.app import single_instance; sys.exit(0 if single_instance() is None else 1)"
    )
    knocked = []
    closing.newConnection.connect(lambda: knocked.append(True))
    second = subprocess.Popen([sys.executable, "-c", launch])
    try:
        wait_until(app, lambda: knocked, timeout=30)  # it's started and found the closing copy
        end = time.time() + 1
        while time.time() < end:
            app.processEvents()
            time.sleep(0.05)
        assert second.poll() is None  # not given up: it waits for the closing copy to go
        closing.close()
        wait_until(app, lambda: second.poll() is not None, timeout=10)
    finally:
        closing.close()
        if second.poll() is None:
            second.kill()
            second.wait()
    assert second.returncode == 1  # then it opens itself


def test_closing_the_window_lets_a_new_launch_open_at_once(app):
    from homepix.ui import app as app_module

    server = app_module.single_instance()
    win = open_window(app)
    win.closing.connect(server.close)
    win.close()
    assert not server.isListening()


def test_the_windows_installer_offers_homepix_for_every_kind_it_reads():
    from pathlib import Path

    from homepix.core.library import MEDIA_EXTS
    from homepix.core.media import IMAGE_EXTS

    iss = (Path(__file__).parents[1] / "packaging" / "homepix.iss").read_text()
    for ext in MEDIA_EXTS | IMAGE_EXTS | {".heic", ".heif"}:
        assert f"Software\\Classes\\{ext}\\OpenWithProgids" in iss, ext
        assert f'SupportedTypes"; ValueType: string; ValueName: "{ext}"' in iss, ext


def test_quitting_waits_for_a_file_change_but_not_long_for_other_work():
    import threading
    import time

    from homepix.ui import workers

    slow, change = threading.Event(), threading.Event()
    workers.run(lambda: slow.wait(30), lambda res, err: None)  # e.g. a long background job
    workers.run(lambda: (time.sleep(0.5), change.set()), lambda res, err: None, must_finish=True)
    start = time.time()
    assert workers.wait_idle(0.2) is False  # the background job is left behind...
    assert change.is_set()  # ...but the file change finished first
    assert time.time() - start < 5
    slow.set()
    assert workers.wait_idle(5)
