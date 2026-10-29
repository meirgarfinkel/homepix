"""M3a: the Qt timeline, viewer and info panel (run offscreen, no display needed)."""

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from conftest import photo  # noqa: E402
from PIL import Image  # noqa: E402
from PySide6.QtCore import QObject, QPoint, QRect, Qt, Signal  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from homepix.core.timeline import Item, group_by_month  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def wait_until(app, cond, timeout=10.0):
    import time

    end = time.time() + timeout
    while not cond() and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    assert cond()


def test_qpainter_keeps_reference_counts(app):
    """PySide6 6.12.0 releases None once too often on every fillRect/drawImage, which crashes the
    app after a few thousand tiles. This fails on such a version."""
    target = QImage(64, 64, QImage.Format.Format_RGB32)
    img = QImage(8, 8, QImage.Format.Format_RGB32)
    p = QPainter(target)
    before = sys.getrefcount(None)
    for _ in range(500):
        p.fillRect(QRect(0, 0, 4, 4), QColor(1, 2, 3))
        p.drawImage(QRect(0, 0, 4, 4), img)
        p.drawRect(QRect(0, 0, 4, 4))
    after = sys.getrefcount(None)
    p.end()
    assert after - before > -100, "this PySide6 version has the QPainter reference bug: use the pinned one"


def fake_items(per_month):
    """per_month: [("2024-03", n), ...] newest first."""
    out, n = [], 0
    for key, count in per_month:
        for k in range(count):
            n += 1
            out.append(Item(n, f"/nowhere/{n}.jpg", f"{key}-{28 - k % 27:02d} 12:00:00", f"q{n}"))
    return out


class NoThumbs(QObject):
    """Stands in for the thumbnail loader: remembers what was asked for, loads nothing."""

    ready = Signal(str)

    def __init__(self):
        super().__init__()
        self.wanted = []

    def set_size(self, px):
        self.size = px

    def get(self, q):
        return None

    def failed(self, q):
        return False

    def want(self, items):
        self.wanted = items


@pytest.fixture
def view(app):
    from homepix.ui.views.timeline import TimelineView

    v = TimelineView(NoThumbs())
    v.resize(800, 600)
    v.show()
    v.set_months(group_by_month(fake_items([("2024-03", 10), ("2024-01", 3), ("2023-12", 7)])))
    app.processEvents()
    yield v
    v.close()


def test_layout_has_a_header_per_month_and_every_item_once(view):
    headers = [r for r in view.rows if r.start == -1]
    assert [view.months[r.month].key for r in headers] == ["2024-03", "2024-01", "2023-12"]
    covered = [i for r in view.rows if r.start != -1 for i in range(r.start, r.end)]
    assert covered == list(range(20))
    assert all(r.end - r.start <= view.cols for r in view.rows if r.start != -1)


def test_click_ctrl_and_shift_select(view):
    def click(i, mods=Qt.KeyboardModifier.NoModifier):
        r = view.item_rect(i).translated(0, -view.verticalScrollBar().value())
        QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, mods, r.center())

    click(2)
    assert view.selected == {2} and view.current == 2
    click(5, Qt.KeyboardModifier.ShiftModifier)
    assert view.selected == {2, 3, 4, 5}
    click(3, Qt.KeyboardModifier.ControlModifier)
    assert view.selected == {2, 4, 5}
    QTest.keyClick(view, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    assert view.selected == set(range(20))


def test_item_at_matches_item_rect(view):
    for i in range(20):
        r = view.item_rect(i).translated(0, -view.verticalScrollBar().value())
        if view.viewport().rect().contains(r.center()):
            assert view.item_at(r.center()) == i
    assert view.item_at(QPoint(2, 2)) == -1  # the margin


def test_arrow_keys_cross_months_in_the_same_column(view):
    last_march_row = [r for r in view.rows if r.start != -1 and view.months[r.month].key == "2024-03"][-1]
    view.set_current(last_march_row.start)
    QTest.keyClick(view, Qt.Key.Key_Down)
    jan = [r for r in view.rows if r.start != -1 and view.months[r.month].key == "2024-01"][0]
    assert view.current == jan.start  # first column of January's first row
    QTest.keyClick(view, Qt.Key.Key_Up)
    assert view.current == last_march_row.start
    QTest.keyClick(view, Qt.Key.Key_End)
    assert view.current == 19
    QTest.keyClick(view, Qt.Key.Key_Home)
    assert view.current == 0


def test_enter_and_double_click_open(view):
    opened = []
    view.activated.connect(opened.append)
    view.set_current(4)
    QTest.keyClick(view, Qt.Key.Key_Return)
    r = view.item_rect(1).translated(0, -view.verticalScrollBar().value())
    QTest.mouseDClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, r.center())
    assert opened == [4, 1]


def test_visible_thumbnails_are_requested_first(view):
    view.verticalScrollBar().setValue(0)
    view.viewport().repaint()
    wanted = [q for q, _ in view.loader.wanted]
    assert wanted[0] == "q1" and len(set(wanted)) == len(wanted)


def test_selection_survives_reload_by_photo_not_position(view):
    view.set_current(5)
    keep = view.items[5].id
    items = fake_items([("2024-05", 4), ("2024-03", 10), ("2024-01", 3), ("2023-12", 7)])
    # new photos arrived on top: ids shift in position but not identity
    for it in items[:4]:
        it.id += 1000
    view.set_months(group_by_month(items))
    assert [view.items[i].id for i in view.selected] == [keep]


def test_tile_size_is_clamped_and_relayouts(view):
    from homepix.ui.views.timeline import MAX_TILE, MIN_TILE

    view.set_tile_size(10_000)
    assert view.tile == MAX_TILE
    wide = view.cols
    view.set_tile_size(1)
    assert view.tile == MIN_TILE and view.cols > wide


def test_thumbnail_loader_decodes_square_tiles(pf, lib, app):
    from homepix.ui.thumbs import ThumbLoader

    p = photo(lib / "a.jpg")
    q = "abc123"
    loader = ThumbLoader(workers=1)
    loader.set_size(50)
    got = []
    loader.ready.connect(got.append)
    loader.want([(q, str(p)), ("missing", str(lib / "nope.jpg"))])
    wait_until(app, lambda: len(got) == 2)
    img = loader.get(q)
    assert img is not None and (img.width(), img.height()) == (50, 50)
    assert loader.get("missing") is None and loader.failed("missing")
    loader.stop()


def test_tiles_keep_the_thumbnails_fine_detail(pf, lib, app):
    """Tiles used to be shrunk while decoding (Qt's quick way), which smeared fine detail: 3 px
    stripes shrunk to a third should be 1 px stripes, and came out up to a quarter wrong."""
    from PIL import Image

    from homepix.core.thumbs import thumbs_dir
    from homepix.ui.thumbs import ThumbLoader

    stripes = Image.new("L", (320, 240))
    stripes.putdata([255 if (x // 3) % 2 else 0 for _ in range(240) for x in range(320)])
    stripes.convert("RGB").save(thumbs_dir("photos") / "stripes.jpg", quality=100, subsampling=0)
    tile = ThumbLoader._decode("stripes", "", 80)  # the middle 240 px square -> 80
    assert (tile.width(), tile.height()) == (80, 80)
    src = [255 if (x // 3) % 2 else 0 for x in range(320)]
    want = [sum(src[40 + 3 * x:43 + 3 * x]) / 3 for x in range(80)]  # (the square starts at x=40)
    off = [abs(tile.pixelColor(x, 40).red() - want[x]) for x in range(4, 76)]
    assert sum(off) / len(off) < 8


def test_exact_copies_share_one_tile_and_the_budget_stays_true(pf, lib, app):
    """Exact copies have the same qhash (a duplicates group shows them side by side). Asked for
    together, they used to be loaded twice and counted twice, so the budget filled with tiles that
    weren't there and the cache emptied itself over and over: the Duplicates page blinked."""
    import shutil

    from homepix.ui.thumbs import ThumbLoader

    loader = ThumbLoader(workers=2, budget=10 * 50 * 50 * 4)  # room for 10 tiles
    loader.set_size(50)
    got = []
    loader.ready.connect(got.append)
    for n in range(30):
        p = photo(lib / f"{n}.jpg")
        shutil.copy2(p, lib / f"{n} copy.jpg")
        loader.want([(f"q{n}", str(p)), (f"q{n}", str(lib / f"{n} copy.jpg"))])
        wait_until(app, lambda n=n: f"q{n}" in got)
    real = sum(img.sizeInBytes() for img in loader._cache.values())
    assert loader._bytes == real and len(loader._cache) == 10
    assert got.count("q0") == 1  # loaded once
    loader.stop()


def test_viewer_shows_photo_and_steps(pf, lib, app):
    from homepix.ui.views.viewer import Viewer

    paths = [photo(lib / f"{i}.jpg") for i in range(3)]
    items = [Item(i, str(p), "2024-01-01 00:00:00", f"q{i}") for i, p in enumerate(paths)]
    v = Viewer()
    v.resize(400, 300)
    v.show()
    v.show_items(items, 1)
    wait_until(app, lambda: v._image() is not None)
    QTest.keyClick(v, Qt.Key.Key_Right)
    assert v.index == 2
    QTest.keyClick(v, Qt.Key.Key_Right)
    assert v.index == 2  # stays on the last one
    closed = []
    v.closed.connect(lambda: closed.append(True))
    QTest.keyClick(v, Qt.Key.Key_Escape)
    assert closed
    v.close()


def test_photo_info_reads_exif_and_index(pf, lib):
    from homepix.core.info import photo_info

    p = lib / "cam.jpg"
    img = Image.new("RGB", (40, 30), "red")
    exif = Image.Exif()
    exif[0x010F], exif[0x0110], exif[0x0112] = "Canon", "Canon EOS R6", 6  # make, model, rotated
    img.save(p, exif=exif)
    pf.index_folder(lib, want_faces=False)
    db = pf.open_db()
    pid = db.execute("SELECT id FROM photos").fetchone()[0]
    info = photo_info(db, pid)
    assert info["camera"] == "Canon EOS R6"
    assert info["dimensions"] == (30, 40)  # shown turned a quarter
    assert info["date_source"] == "guessed from when the file was last changed"
    p.unlink()
    assert photo_info(db, pid)["offline"] is True
    assert photo_info(db, 999) is None
    db.close()


def test_the_info_panel_says_when_it_was_taken(pf, lib, app):
    from homepix.ui.views.info_panel import InfoPanel

    img = Image.new("RGB", (40, 30), "red")
    exif = Image.Exif()
    exif[0x8769] = {0x9003: "2022:04:01 07:32:31"}  # DateTimeOriginal
    img.save(lib / "cam.jpg", exif=exif)
    pf.index_folder(lib, want_faces=False)
    db = pf.open_db()
    pid = db.execute("SELECT id FROM photos").fetchone()[0]
    db.close()
    panel = InfoPanel()
    panel.show_photo(pid)
    wait_until(app, lambda: "Taken" in panel.label.text())
    text = panel.label.text()
    assert "Friday, April 1, 2022<br>07:32:31" in text and "written in the photo (EXIF)" in text
    panel.close()
