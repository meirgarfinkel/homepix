"""M3b: library folders, background indexing, search and the folder tree, in the core and in the Qt app."""

import os
import shutil

import pytest
from conftest import photo, rows

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


# --- core ------------------------------------------------------------------------------------------

def test_adding_folders_nests_and_refuses_repeats(pf, lib, tmp_path):
    (lib / "2023").mkdir()
    pf.add_folder(lib / "2023")
    with pytest.raises(ValueError, match="already"):
        pf.add_folder(lib / "2023")
    pf.add_folder(lib)  # a folder holding a library folder replaces it
    assert pf.library_folders() == [str(lib)]
    with pytest.raises(ValueError, match="already"):
        pf.add_folder(lib / "2023")  # already in, through its parent
    with pytest.raises(ValueError, match="doesn't exist"):
        pf.add_folder(tmp_path / "nope")
    with pytest.raises(ValueError, match="own files"):
        pf.add_folder(pf.data_dir())


def test_removing_a_folder_forgets_only_its_photos(pf, lib, tmp_path):
    other = tmp_path / "Other"
    photo(lib / "a.jpg")
    photo(other / "b.jpg")
    photo(tmp_path / "Pictures2" / "c.jpg")  # shares the start of the name, but isn't inside
    for f in (lib, other, tmp_path / "Pictures2"):
        pf.add_folder(f)
        pf.index_folder(f, want_faces=False)
    assert pf.remove_folder(str(lib)) == 1
    assert (lib / "a.jpg").exists()  # the file is untouched
    db = pf.open_db()
    left = sorted(os.path.basename(r[0]) for r in db.execute("SELECT path FROM photos"))
    db.close()
    assert left == ["b.jpg", "c.jpg"]
    assert str(lib) not in pf.library_folders()


def test_only_one_indexer_runs_at_a_time(pf, lib):
    from photofind.core.indexer import _IndexLock

    photo(lib / "a.jpg")
    pf.add_folder(lib)
    other_app = _IndexLock()
    assert other_app.acquire()
    ix = pf.Indexer()
    ix._run()
    assert ix.snapshot()["phase"] == "elsewhere" and not rows(pf, lib)
    other_app.release()
    ix._run()
    assert len(rows(pf, lib)) == 1
    assert ix.snapshot()["changes"] == 1
    ix._run()  # nothing new
    assert ix.snapshot()["changes"] == 1


def red(path):
    """A photo unlike the random ones photo() makes, so a search for it has one clear match."""
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 48), (250, 10, 10)).save(path)
    return path


class FakeText:
    """The search model's text side: 'searches' for the photo whose vector it was given."""

    def __init__(self, vec):
        self.vec = vec

    def query(self, q):
        return self.vec


def _clip_of(pf, name):
    db = pf.open_db()
    try:
        r = db.execute("SELECT id, clip FROM photos WHERE path LIKE ?", (f"%{name}",)).fetchone()
        return r["id"], pf.from_blob(r["clip"])
    finally:
        db.close()


def test_search_finds_the_photo_and_keeps_to_a_folder(pf, lib):
    for i in range(12):
        (red if i == 5 else photo)(lib / ("2023" if i % 2 else "2023b") / f"{i}.jpg")
    pf.index_folder(lib, want_faces=False)
    target, vec = _clip_of(pf, "2023/5.jpg")
    index = pf.SearchIndex()
    index._text = FakeText(vec)
    found = index.search("anything")
    assert found["total"] == 12 and found["matches"][0]["id"] == target
    inside = index.search("anything", folder=lib / "2023")
    assert inside["total"] == 6  # not 2023b
    assert all("/2023/" in r["path"] for r in inside["matches"] + inside["related"])


def test_search_sees_new_photos(pf, lib):
    photo(lib / "a.jpg")
    pf.index_folder(lib, want_faces=False)
    index = pf.SearchIndex()
    assert len(index.vectors()[0]) == 1
    photo(lib / "b.jpg")
    pf.index_folder(lib, want_faces=False)
    assert len(index.vectors()[0]) == 2


def test_folder_items_and_counts(pf, lib):
    from photofind.core.timeline import folder_counts, load_items

    photo(lib / "a.jpg")
    photo(lib / "2023" / "b.jpg")
    photo(lib / "2023" / "trip" / "c.jpg")
    photo(lib / "2023b" / "d.jpg")
    pf.index_folder(lib, want_faces=False)
    db = pf.open_db()
    assert len(load_items(db, folder=lib / "2023")) == 2  # and the folder inside it; not 2023b
    counts = folder_counts(db)
    db.close()
    assert counts == {str(lib): 1, str(lib / "2023"): 1, str(lib / "2023" / "trip"): 1, str(lib / "2023b"): 1}


def test_folder_tree_totals():
    from photofind.ui.views.sidebar import folder_tree

    sep = os.sep
    root = f"{sep}lib"
    counts = {root: 1, f"{root}{sep}x": 2, f"{root}{sep}x{sep}y": 3, f"{sep}elsewhere": 5}
    node = folder_tree([root], counts)[root]
    assert node["total"] == 6
    assert node["children"]["x"]["total"] == 5
    assert node["children"]["x"]["children"]["y"]["total"] == 3


def test_indexing_status_text():
    from photofind.ui.indexing import describe

    assert describe({"running": False, "phase": "idle"}) == ""
    assert describe({"running": True, "phase": "indexing", "done": 30, "todo": 200, "eta": 150}) \
        == "Adding photos: 30 of 200, about 2 minutes left"
    assert "Another" in describe({"running": False, "phase": "elsewhere"})


# --- the app ---------------------------------------------------------------------------------------

QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def wait_until(app, cond, timeout=20.0):
    import time

    end = time.time() + timeout
    while not cond() and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    assert cond()


@pytest.fixture
def window(pf, app):
    from photofind.ui.main_window import MainWindow

    w = MainWindow()
    w.resize(900, 600)
    w.show()
    yield w
    w.close()


def test_new_library_welcomes_then_adding_a_folder_shows_its_photos(window, lib, app):
    for i in range(3):
        photo(lib / "2023" / f"{i}.jpg")
    for i in range(2):
        photo(lib / "2024" / f"{i}.jpg")
    wait_until(app, lambda: window.stack.currentWidget() is window.welcome)
    window.add_library_folder(str(lib))
    wait_until(app, lambda: len(window.timeline.items) == 5)
    assert window.stack.currentWidget() is window.timeline
    folders = {it.data(0, 256): it.text(1) for it in window.sidebar._all_items()}
    assert folders[str(lib)] == "5" and folders[str(lib / "2023")] == "3"
    window.sidebar.select(str(lib / "2024"))
    window.show_place(str(lib / "2024"))
    wait_until(app, lambda: len(window.timeline.items) == 2)
    assert window.sidebar.current_path() == str(lib / "2024")
    assert "in 2024" in window.count_label.text()


def test_search_shows_sections_and_clearing_it_goes_back(window, pf, lib, app):
    for i in range(10):
        (red if i == 7 else photo)(lib / f"{i}.jpg")
    pf.add_folder(lib)
    pf.index_folder(lib, want_faces=False)
    window.reload()
    wait_until(app, lambda: len(window.timeline.items) == 10)
    target, vec = _clip_of(pf, "/7.jpg")
    window.search_index._text = FakeText(vec)
    window.search_box.setText("the seventh")
    window.search()
    wait_until(app, lambda: "match" in window.count_label.text())
    assert window.timeline.items[0].id == target
    assert window.timeline.months[0].title == "Best matches"
    assert not window.timeline.scrubber.isVisibleTo(window)
    window.search_box.clear()
    wait_until(app, lambda: not window.query and len(window.timeline.items) == 10)
    wait_until(app, lambda: window.timeline.scrubber.isVisibleTo(window))
    assert window.timeline.months[0].title == ""


def test_missing_photos_banner_and_removing_them(window, pf, lib, app, monkeypatch):
    for i in range(60):
        photo(lib / "drive" / f"{i}.jpg")
    for i in range(5):
        photo(lib / "home" / f"{i}.jpg")
    pf.add_folder(lib)
    pf.index_folder(lib, want_faces=False)
    shutil.rmtree(lib / "drive")
    window.watcher.start()
    wait_until(app, lambda: window.banner.isVisibleTo(window) and window.banner.missing == 60)
    assert "60 photos" in window.banner.label.text()
    monkeypatch.setattr(QtWidgets.QMessageBox, "question",
                        staticmethod(lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Yes))
    window.banner.button.click()
    wait_until(app, lambda: not window.banner.isVisibleTo(window))
    wait_until(app, lambda: len(window.timeline.items) == 5)
    assert len(rows(pf, lib)) == 5


def test_removing_a_library_folder_from_the_app(window, pf, lib, app):
    photo(lib / "a.jpg")
    pf.add_folder(lib)
    pf.index_folder(lib, want_faces=False)
    window.reload()
    wait_until(app, lambda: len(window.timeline.items) == 1)
    window.remove_library_folder(str(lib))
    wait_until(app, lambda: window.stack.currentWidget() is window.welcome)
    assert (lib / "a.jpg").exists() and not rows(pf, lib)


def test_toggling_the_scrubber_lays_the_grid_out_again(app):
    from test_ui import NoThumbs

    from photofind.core.timeline import Item, group_by_month
    from photofind.ui.views.timeline import TimelineView

    v = TimelineView(NoThumbs())
    v.resize(800, 600)
    v.show()
    v.set_months(group_by_month([Item(i, f"/x/{i}.jpg", "2024-01-01 00:00:00", f"q{i}") for i in range(30)]))
    app.processEvents()
    before = (v.cols, v._cell)
    v.set_scrubber(False)  # no scrubber: the grid gets its width
    assert (v.cols, v._cell) != before
    v.set_scrubber(True)
    assert (v.cols, v._cell) == before
    v.close()

