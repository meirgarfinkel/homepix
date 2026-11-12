"""M3a: the Qt timeline, viewer and info panel (run offscreen, no display needed)."""

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from conftest import photo, wait_until  # noqa: E402
from PIL import Image  # noqa: E402
from PySide6.QtCore import QObject, QPoint, QRect, Qt, Signal  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from homepix.core.timeline import Item, group_by_month  # noqa: E402


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


def test_ticking_by_dragging_across_photos(view):
    """While ticking (as on Declutter), as on Who is this?: press and drag (on a photo or beside them),
    and every photo in the rectangle flips, ticked or unticked; the rectangle shows while dragging."""
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QMouseEvent

    view.ticking = True
    left, none = Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier

    def at(i):
        return view.item_rect(i).translated(0, -view.verticalScrollBar().value()).center()

    def move(p):
        p = QPointF(p)
        e = QMouseEvent(QEvent.Type.MouseMove, p, view.viewport().mapToGlobal(p), Qt.MouseButton.NoButton, left, none)
        QApplication.sendEvent(view.viewport(), e)

    def drag(start, *through):
        QTest.mousePress(view.viewport(), left, none, start)
        for p in through:
            move(p)
        shown = view._band.isVisible()
        QTest.mouseRelease(view.viewport(), left, none, through[-1])
        assert shown and not view._band.isVisible()  # the rectangle shows while dragging, not after

    assert view.cols == 3
    drag(at(0), at(4))  # the first two of the first row, and of the second
    assert view.selected == {0, 1, 3, 4}
    drag(at(1), at(2))  # each flips: 1 unticked, 2 ticked
    assert view.selected == {0, 2, 3, 4}
    drag(at(4), at(8), at(4))  # dragging back puts them back: only 4 flips
    assert view.selected == {0, 2, 3}
    beside = QPoint(view.viewport().width() - 5, at(6).y())  # right of the last column
    drag(beside, at(7))
    assert view.selected == {0, 2, 3, 7, 8}

    view.ticking = False
    QTest.mousePress(view.viewport(), left, none, at(5))
    move(at(7))
    QTest.mouseRelease(view.viewport(), left, none, at(7))
    assert view.selected == {5} and not view._band.isVisible()  # not ticking: a drag doesn't tick


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

    from homepix.core.thumbs import thumb_file
    from homepix.ui.thumbs import ThumbLoader

    stripes = Image.new("L", (320, 240))
    stripes.putdata([255 if (x // 3) % 2 else 0 for _ in range(240) for x in range(320)])
    stripes.convert("RGB").save(thumb_file("stripes"), quality=100, subsampling=0)
    tile, soft = ThumbLoader._decode("stripes", "", 80)  # the middle 240 px square -> 80
    assert (tile.width(), tile.height()) == (80, 80) and not soft
    src = [255 if (x // 3) % 2 else 0 for x in range(320)]
    want = [sum(src[40 + 3 * x : 43 + 3 * x]) / 3 for x in range(80)]  # (the square starts at x=40)
    off = [abs(tile.pixelColor(x, 40).red() - want[x]) for x in range(4, 76)]
    assert sum(off) / len(off) < 8


def test_old_thumbnails_show_until_sharpened_then_swap_in(pf, lib, app):
    """Thumbnails grew from 320 to 640 px. Libraries indexed before keep showing their old ones
    while the sharper ones are made in the background (newest first), and tiles swap over."""
    from homepix.core.db import open_db
    from homepix.core.thumbs import THUMB_SIZE, old_thumb_file, sharpen_thumbs, thumb_file
    from homepix.ui.thumbs import ThumbLoader

    for n in range(3):
        photo(lib / f"2024_01_0{n + 1}_12_00_00.jpg")
    pf.index_folder(lib)
    db = open_db()
    rows = db.execute("SELECT qhash, path FROM photos ORDER BY taken").fetchall()
    for q, _ in rows:  # as a library indexed before: only old, small thumbnails
        thumb_file(q).rename(old_thumb_file(q))
        with Image.open(old_thumb_file(q)) as im:
            im.thumbnail((320, 320))
            im.save(old_thumb_file(q))
    q, p = rows[0]

    loader = ThumbLoader(workers=1)
    loader.set_size(60)
    got = []
    loader.ready.connect(got.append)
    loader.want([(q, p)])
    wait_until(app, lambda: q in got)
    old_tile = loader.get(q)
    assert old_tile is not None and q in loader._soft
    assert not thumb_file(q).exists()  # shown from the old one, not made on the spot

    progress = []
    assert sharpen_thumbs(db, str(lib) + os.sep, progress=lambda d, t: progress.append((d, t))) == 3
    assert progress[0] == (0, 3) and progress[-1] == (3, 3)
    for q2, _ in rows:
        assert thumb_file(q2).exists() and not old_thumb_file(q2).exists()
        with Image.open(thumb_file(q2)) as im:
            assert max(im.size) == min(THUMB_SIZE, max(Image.open(rows[0][1]).size))
    assert sharpen_thumbs(db, str(lib) + os.sep) == 0  # nothing left to do

    got.clear()
    loader.want([(q, p)])  # the next repaint asks again: the sharper one is loaded over the old
    assert loader.get(q) is old_tile  # (still shown meanwhile)
    wait_until(app, lambda: q in got)
    assert loader.get(q) is not old_tile and q not in loader._soft
    loader.stop()
    db.close()


def test_indexing_sharpens_old_thumbnails_and_says_so(pf, lib, app):
    from homepix.core.db import open_db
    from homepix.core.thumbs import old_thumb_file, thumb_file
    from homepix.ui.indexing import describe

    for n in range(2):
        photo(lib / f"{n}.jpg")
    pf.index_folder(lib)
    db = open_db()
    qs = [r[0] for r in db.execute("SELECT qhash FROM photos")]
    db.close()
    for q in qs:
        thumb_file(q).rename(old_thumb_file(q))
    seen = []
    info = pf.index_folder(lib, on_progress=lambda i: seen.append(describe(dict(i, running=True))))
    assert info["todo"] == 0 and all(thumb_file(q).exists() for q in qs)
    assert "Sharpening thumbnails: 2 of 2" in seen


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
    wait_until(app, lambda: v._best_image() is not None)
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
    text = panel.label.text().replace("&nbsp;", " ")
    assert "Friday, April 1, 2022<br>07:32:31" in text and "written in the photo (EXIF)" in text
    panel.close()


def test_size_slider_jumps_to_a_click_and_drags_from_there(app):
    from PySide6.QtCore import Qt as _Qt

    from homepix.ui.main_window import JumpSlider

    s = JumpSlider(_Qt.Orientation.Horizontal)
    s.setRange(90, 360)
    s.setFixedWidth(200)
    s.show()
    QTest.qWaitForWindowExposed(s)
    s.setValue(90)
    QTest.mouseClick(s, _Qt.MouseButton.LeftButton, pos=QPoint(170, s.height() // 2))
    assert s.value() > 300  # straight there, not one page step (+27) toward it
    QTest.mouseClick(s, _Qt.MouseButton.LeftButton, pos=QPoint(100, s.height() // 2))
    assert 200 < s.value() < 250  # (the middle)
    QTest.mousePress(s, _Qt.MouseButton.LeftButton, pos=QPoint(30, s.height() // 2))
    low = s.value()
    QTest.mouseMove(s, QPoint(120, s.height() // 2))
    assert s.value() > low  # the click goes on as a drag
    QTest.mouseRelease(s, _Qt.MouseButton.LeftButton, pos=QPoint(120, s.height() // 2))
    s.close()


def test_message_boxes_and_buttons_use_the_apps_flat_icons(app):
    """No glossy system icons: message boxes get the app's own line icons, buttons and menus none."""
    from PySide6.QtWidgets import QStyle

    from homepix.ui import theme

    theme.apply(app, "dark")
    style = app.style()
    assert style.styleHint(QStyle.StyleHint.SH_DialogButtonBox_ButtonsHaveIcons) == 0
    assert app.testAttribute(Qt.ApplicationAttribute.AA_DontShowIconsInMenus)
    accent = theme.color("accent").rgb()
    img = style.standardIcon(QStyle.StandardPixmap.SP_MessageBoxQuestion).pixmap(32, 32).toImage()
    colours = {
        img.pixelColor(x, y).rgb()
        for x in range(img.width())
        for y in range(img.height())
        if img.pixelColor(x, y).alpha() == 255
    }
    assert colours == {accent}  # one flat colour, the app's accent


def test_long_paths_wrap_in_the_info_panel():
    from homepix.ui.views.info_panel import breakable

    assert breakable(r"C:\Users\me\Pictures").replace("\u200b", "") == r"C:\Users\me\Pictures"
    assert r"\​Users\​" in breakable(r"C:\Users\me")  # a place to wrap after each \


def test_settings_lists_and_changes_the_folders_left_out(pf, app, tmp_path, monkeypatch):
    """Settings' Left out: subfolders of the library left out, one left out more, one taken back in."""
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    from homepix.core.settings import excluded_folders, load_settings, save_settings, set_excluded
    from homepix.ui.views.settings_dialog import SettingsDialog

    lib = tmp_path / "lib"
    for sub in ("a", "b"):
        (lib / sub).mkdir(parents=True)
    s = load_settings()
    s["folders"] = [str(lib)]
    save_settings(s)
    set_excluded(str(lib / "a"), True)
    dlg = SettingsDialog()
    changed = []
    dlg.left_out_changed.connect(lambda: changed.append(1))
    assert [dlg.left_list.item(i).data(0x0100) for i in range(dlg.left_list.count())] == [str(lib / "a")]
    assert not dlg.back_button.isEnabled()

    told = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a: told.append(a[2]))
    for folder in (lib, tmp_path):  # the library folder itself, and one outside it: not left out
        monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, f=folder: str(f))
        dlg._leave_out()
    assert len(told) == 2 and excluded_folders() == [str(lib / "a")] and not changed

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a: str(lib / "b"))
    dlg._leave_out()
    assert excluded_folders() == [str(lib / "a"), str(lib / "b")] and dlg.left_list.count() == 2

    dlg.left_list.item(0).setSelected(True)
    assert dlg.back_button.isEnabled()
    dlg._take_back()
    assert excluded_folders() == [str(lib / "b")] and changed == [1, 1]


def test_the_window_fits_half_a_screen(app, pf, lib):
    """Snapped to half a 1920-wide screen (960), the window fits with room to spare: rows of buttons
    wrap, and the side panels can be narrower."""
    from conftest import open_window
    from PySide6.QtWidgets import QPushButton, QWidget

    from homepix.ui.views.flow import FlowLayout

    w = open_window(app)
    try:
        assert w.minimumSizeHint().width() <= 820
    finally:
        w.close()
    box = QWidget()
    row = FlowLayout(box)
    buttons = [QPushButton(t) for t in ("Someone else...", "Not a face", "Most like ticked", "Their photos")]
    for b in buttons:
        row.addWidget(b)
    wide = row.heightForWidth(2000)
    narrow = row.heightForWidth(buttons[0].sizeHint().width() + 10)
    assert narrow > wide * 3  # one per line
    box.resize(2000, wide)
    box.show()
    app.processEvents()
    assert len({b.y() for b in buttons}) == 1  # all on one line when there's room
    box.close()
    box.deleteLater()
    app.processEvents()


def test_settings_shows_folders_in_the_home_folder_the_short_way(app, pf, tmp_path, monkeypatch):
    from pathlib import Path

    from homepix.core.settings import load_settings, save_settings
    from homepix.ui.views.settings_dialog import SettingsDialog

    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    (tmp_path / "Pictures").mkdir()
    s = load_settings()
    s["folders"] = [str(tmp_path / "Pictures")]
    save_settings(s)
    dlg = SettingsDialog()
    try:
        item = dlg.list.item(0)
        assert item.text() == os.path.join("~", "Pictures")
        assert item.data(Qt.ItemDataRole.UserRole) == str(tmp_path / "Pictures") and item.toolTip() == ""
        assert dlg.list.height() == 3 * SettingsDialog.ROW + 10  # (room for a few, not stretched)
    finally:
        dlg.close()


def test_no_hover_tips_in_the_app():
    """The owner turned hover tips off (they got in the way): what's needed is visible text."""
    from pathlib import Path

    root = Path(__file__).parent.parent / "homepix"
    found = [
        f"{p.relative_to(root)}:{n}"
        for p in root.rglob("*.py")
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if "setToolTip(" in line or "ToolTipRole" in line or "setToolTipsVisible" in line
    ]
    assert found == []
