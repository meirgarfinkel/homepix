"""Duplicates. A test library with planted copies (exact, resized, saved again, converted, a video)
and bursts of different shots: every copy is found, no burst is, and moving extras to the Trash (or
saying they aren't duplicates) is undoable."""

import os
import shutil
import time

import numpy as np
import pytest
from conftest import own_trash
from PIL import Image, ImageDraw

from homepix.core.util import quick_hash


def scene(seed, shift=0, size=(640, 480)):
    """A picture of its own (shapes on a gradient); shift moves the shapes a little, like the next
    shot of a burst."""
    rng = np.random.default_rng(seed)
    w, h = size
    base = rng.integers(40, 200, 3)
    grad = np.linspace(0, 1, w)[None, :, None] * rng.integers(-60, 60, 3)
    img = Image.fromarray(np.clip(base + grad + np.zeros((h, w, 3)), 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(img)
    for _ in range(6):
        x, y = rng.integers(0, w - 120), rng.integers(0, h - 120)
        r = rng.integers(40, 120)
        d.ellipse([x + shift, y, x + r + shift, y + r], fill=tuple(int(v) for v in rng.integers(0, 255, 3)))
    return img


def save(img, path, taken=None, quality=92):
    path.parent.mkdir(parents=True, exist_ok=True)
    ex = Image.Exif()
    if taken:
        ex.get_ifd(0x8769)[0x9003] = taken
    if path.suffix.lower() == ".png":
        img.save(path)
    else:
        img.save(path, "JPEG", quality=quality, exif=ex)
    return path


@pytest.fixture
def home(pf, lib, tmp_path, monkeypatch):
    own_trash(monkeypatch, tmp_path)
    from homepix.core import duplicates, fileops

    pf.add_folder(lib)
    db = pf.open_db()
    yield pf, db, fileops, duplicates
    db.close()


@pytest.fixture
def planted(home, lib):
    """{what: path}: originals, their copies, bursts, and others."""
    pf, db, _, _ = home
    p = {}
    p["a"] = save(scene(1), lib / "2020" / "2020_01_01_10_00_00.jpg", "2020:01:01 10:00:00")
    p["a exact"] = lib / "backup" / "IMG_0001.jpg"
    p["a exact"].parent.mkdir()
    shutil.copy2(p["a"], p["a exact"])
    p["b"] = save(scene(2, size=(1200, 900)), lib / "2021" / "2021_02_02_10_00_00.jpg", "2021:02:02 10:00:00")
    p["b small"] = save(scene(2, size=(1200, 900)).resize((400, 300)), lib / "shared" / "b.jpg")  # no date left
    p["c"] = save(scene(3), lib / "2022" / "2022_03_03_10_00_00.jpg", "2022:03:03 10:00:00")
    p["c again"] = save(scene(3), lib / "2022" / "2022_03_03_10_00_00_1.jpg", "2022:03:03 10:00:00", quality=35)
    p["d"] = save(scene(4), lib / "2023" / "2023_04_04_10_00_00.jpg", "2023:04:04 10:00:00")
    p["d png"] = save(scene(4), lib / "2023" / "2023_04_04_10_00_00.png")
    for i in range(3):  # a burst: a second apart
        p[f"burst {i}"] = save(scene(5, shift=3 * i), lib / "2024" / f"2024_05_05_10_00_0{i}.jpg",
                               f"2024:05:05 10:00:0{i}")
    for i in range(2):  # a burst within one second
        p[f"fast {i}"] = save(scene(6, shift=2 * i), lib / "2024" / f"2024_06_06_10_00_00{'_1' * i}.jpg",
                              "2024:06:06 10:00:00")
    for i in range(6):
        p[f"other {i}"] = save(scene(20 + i), lib / "2019" / f"2019_07_0{i + 1}_10_00_00.jpg",
                               f"2019:07:0{i + 1} 10:00:00")
    pf.index_folder(lib, want_faces=False)
    for name in ("v", "v copy"):  # a video and its copy (videos are only ever exact copies)
        path = lib / ("2020" if name == "v" else "backup") / f"{name}.mp4"
        path.write_bytes(b"\0\0\0\x18ftypmp42" + b"movie" * 5000)
        p[name] = path
        db.execute("INSERT INTO photos (path, size, mtime, qhash, kind, taken, taken_source) "
                   "VALUES (?, ?, 0, ?, 'video', '2020-01-01 12:00:00', 'video')",
                   (str(path), path.stat().st_size, quick_hash(path, path.stat().st_size)))
    db.commit()
    return p


def groups_of(home, lib):
    _, db, _, duplicates = home
    return duplicates.find(db)


def as_names(groups, planted):
    back = {str(v): k for k, v in planted.items()}
    return sorted(sorted(back[c.path] for c in g.copies) for g in groups)


# --- finding them ----------------------------------------------------------------------------------

def test_every_planted_copy_is_found_and_no_burst(home, lib, planted):
    groups = groups_of(home, lib)
    assert as_names(groups, planted) == [["a", "a exact"], ["b", "b small"], ["c", "c again"], ["d", "d png"],
                                         ["v", "v copy"]]
    how = {planted_name(g, planted): g.how for g in groups}
    assert how["a"] == "exact" and how["v"] == "exact" and how["b"] == "near"


def planted_name(group, planted):
    back = {str(v): k for k, v in planted.items()}
    return min(back[c.path] for c in group.copies)


def test_the_keeper_has_the_most_pixels_then_the_biggest_file(home, lib, planted):
    keepers = {planted_name(g, planted): g.keeper.path for g in groups_of(home, lib)}
    assert keepers["b"] == str(planted["b"])  # 1200x900 beats 400x300
    assert keepers["c"] == str(planted["c"])  # the same size: the bigger file (better quality)
    assert keepers["a"] == str(planted["a"])  # exact copies: the one named by its date


def test_the_perceptual_hash_sees_through_resizing_and_saving_again():
    from homepix.core.duplicates import phash

    def distance(a, b):  # bits that differ
        return ((a ^ b) & (2**64 - 1)).bit_count()

    a = scene(9, size=(1200, 900))
    assert distance(phash(a), phash(a.resize((300, 225)))) <= 2
    assert distance(phash(a), phash(scene(10, size=(1200, 900)))) > 12


# --- doing something about them ----------------------------------------------------------------------

def test_extras_go_to_the_trash_and_undo_brings_them_back(home, lib, planted):
    _, db, fo, duplicates = home
    fo.run(db, "trash", "Trash", duplicates.plan_extras(db, groups_of(home, lib)))
    assert groups_of(home, lib) == []  # nothing left to review
    assert all(planted[x].exists() for x in ("a", "b", "c", "d", "v"))
    assert not any(planted[x].exists() for x in ("a exact", "b small", "c again", "d png", "v copy"))
    assert not (lib / "Duplicates").exists()  # never a folder of their own
    fo.undo(db)
    assert len(groups_of(home, lib)) == 5


def test_choosing_another_keeper(home, lib, planted):
    _, db, fo, duplicates = home
    g = next(g for g in groups_of(home, lib) if planted_name(g, planted) == "b")
    small = next(c for c in g.copies if c.path == str(planted["b small"]))
    g.keep(small.id)
    fo.run(db, "trash", "Trash", duplicates.plan_extras(db, [g]))
    assert planted["b small"].exists() and not planted["b"].exists()


def test_not_duplicates_is_never_offered_again_until_undone(home, lib, planted):
    _, db, fo, duplicates = home
    g = next(g for g in groups_of(home, lib) if planted_name(g, planted) == "d")
    fo.run(db, "apart", "Not duplicates", [duplicates.plan_apart(db, [g])])
    assert ["d", "d png"] not in as_names(groups_of(home, lib), planted)
    assert duplicates.plan_apart(db, [g]) is None  # nothing more to say
    fo.undo(db)
    assert ["d", "d png"] in as_names(groups_of(home, lib), planted)


# --- in the app ----------------------------------------------------------------------------------

def test_reviewing_in_the_window(home, lib, planted, monkeypatch):
    from test_faces_ui import app as _app
    from test_faces_ui import wait_until

    from homepix.ui.main_window import MainWindow
    from homepix.ui.views import changes_dialog
    from homepix.ui.views.sidebar import DUPES

    app = _app.__wrapped__()
    shown = []

    def exec_(dlg):
        model = dlg.table.model()
        shown.append([model.data(model.index(r, 1)) for r in range(model.rowCount())])
        return 1

    monkeypatch.setattr(changes_dialog.ChangesDialog, "exec", exec_)
    w = MainWindow(index=False)
    w.show()
    wait_until(app, lambda: w.loaded)
    try:
        w.show_place(DUPES)
        wait_until(app, lambda: len(w.dupes.groups) == 5)
        assert w.stack.currentWidget() is w.dupes and w.dedupe.count == 5
        # keep the small copy of b instead, and say d and its PNG aren't duplicates
        row = next(i for i, g in enumerate(w.dupes.groups) if planted_name(g, planted) == "b")
        w.dupes._keep(row, 1)
        assert w.dupes.groups[row].keeper.path == str(planted["b small"])
        d = next(i for i, g in enumerate(w.dupes.groups) if planted_name(g, planted) == "d")
        w.dupes.list.selectionModel().select(w.dupes.list.model().index(d, 0),
                                             w.dupes.list.selectionModel().SelectionFlag.Select)
        w.dupes.apart_button.click()
        wait_until(app, lambda: len(w.dupes.groups) == 4 and not w.ops.busy)
        assert w.dupes.groups[[planted_name(g, planted) for g in w.dupes.groups].index("b")].keeper.path \
            == str(planted["b small"])  # still the one chosen
        # not sure about c yet: skipped, it goes to the end and is left alone
        c = [planted_name(g, planted) for g in w.dupes.groups].index("c")
        w.dupes.skip(c)
        assert [planted_name(g, planted) for g in w.dupes.groups][-1] == "c"
        assert w.dupes.list.first_skipped == 3 and w.dedupe.count == 3
        assert w.dupes.title.text() == "3 groups of duplicates, 3 extra copies, 1 skipped"
        w.dupes.trash_button.click()
        wait_until(app, lambda: not w.ops.busy and not planted["b"].exists())
        assert set(shown[0]) == {"Trash"} and planted["b small"].exists() and planted["c again"].exists()
        assert not (lib / "Duplicates").exists()
        wait_until(app, lambda: [planted_name(g, planted) for g in w.dupes.groups] == ["c"])
        assert w.dupes.is_skipped(w.dupes.groups[0])  # still skipped, after the reload
        w.dupes.skip(0)  # decide it now after all
        assert not w.dupes.is_skipped(w.dupes.groups[0]) and w.dedupe.count == 1
        w.ops.undo()
        wait_until(app, lambda: len(w.dupes.groups) == 4 and not w.ops.busy)
        assert planted["b"].exists()
    finally:
        w.close()


def test_skipped_groups_are_remembered(home, lib, planted):
    from test_faces_ui import app as _app

    from homepix.ui.views.duplicates import DuplicatesView

    _app.__wrapped__()
    groups = groups_of(home, lib)
    v = DuplicatesView()
    v.set_groups(groups, [lib])
    v.skip(0)
    first = v.groups[-1]
    again = DuplicatesView()  # as after closing the app
    again.set_groups(groups_of(home, lib), [lib])
    assert again.is_skipped(again.groups[-1]) and {c.id for c in again.groups[-1].copies} == {c.id for c in first.copies}
    assert again.to_decide() == 4


def test_comparing_a_groups_copies_large(home, lib, planted):
    """Double-click a copy: the viewer shows that group's copies, the arrows go round them, K keeps
    the one shown, and the copies stay where they were on the page."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent
    from test_faces_ui import app as _app
    from test_faces_ui import wait_until

    from homepix.ui.main_window import MainWindow
    from homepix.ui.views.sidebar import DUPES

    app = _app.__wrapped__()
    w = MainWindow(index=False)
    w.show()
    wait_until(app, lambda: w.loaded)
    try:
        w.show_place(DUPES)
        wait_until(app, lambda: len(w.dupes.groups) == 5)
        row = next(i for i, g in enumerate(w.dupes.groups) if planted_name(g, planted) == "b")
        g = w.dupes.groups[row]
        order = [c.id for c in w.dupes.list.shown(g)]
        w.dupes.list.open_copy.emit(row, 0)
        v = w.viewer
        wait_until(app, lambda: w.stack.currentWidget() is v and len(v.items) == 2)
        assert [it.id for it in v.items] == order and v.index == 0
        assert v.compare[0](0)[0] == "Copy 1 of 2 · ✓ Keep"
        assert v.compare[0](0)[1].startswith(f"Duplicates {row + 1} of 5 · ")

        def key(k):
            app.sendEvent(v, QKeyEvent(QKeyEvent.Type.KeyPress, k, Qt.KeyboardModifier.NoModifier))

        key(Qt.Key.Key_Right)
        assert v.index == 1 and v.compare[0](1)[0].startswith("Copy 2 of 2 · ")
        key(Qt.Key.Key_Right)  # round again
        assert v.index == 0
        key(Qt.Key.Key_Left)
        assert v.index == 1
        key(Qt.Key.Key_K)
        assert g.keeper.id == order[1] and v.compare[0](1)[0] == "Copy 2 of 2 · ✓ Keep"
        assert [c.id for c in w.dupes.list.shown(g)] == order  # nothing moved on the page
        key(Qt.Key.Key_Down)  # the next group, still large
        assert row + 1 < len(w.dupes.groups)  # (b isn't the last group)
        nxt = w.dupes.groups[row + 1]
        wait_until(app, lambda: [it.id for it in v.items] == [c.id for c in w.dupes.list.shown(nxt)])
        assert w.stack.currentWidget() is v and v.index == 0
        assert w.dupes.list.currentIndex().row() == row + 1
        key(Qt.Key.Key_Up)
        wait_until(app, lambda: [it.id for it in v.items] == order)
        # Down held: the presses come faster than the groups load. Each goes on from the one before,
        # it stops at the last group, and only that one is shown (not every group on the way, late).
        shown = []
        real = v.show_items
        v.show_items = lambda items, i: (shown.append([it.id for it in items]), real(items, i))
        for _ in range(10):
            key(Qt.Key.Key_Down)
        last = [c.id for c in w.dupes.list.shown(w.dupes.groups[-1])]
        wait_until(app, lambda: [it.id for it in v.items] == last)
        end = time.time() + 0.5
        while time.time() < end:
            app.processEvents()
        assert shown == [last] and w.dupes.list.currentIndex().row() == len(w.dupes.groups) - 1
        v.show_items = real
        key(Qt.Key.Key_Escape)
        wait_until(app, lambda: w.stack.currentWidget() is w.dupes)
        assert v.compare is None
    finally:
        w.close()


def test_a_raw_file_and_its_jpeg_are_not_duplicates(home, lib):
    pf, db, _, duplicates = home
    save(scene(40), lib / "2023" / "2023_01_02_03_04_05.jpg", "2023:01:02 03:04:05")
    dng = save(scene(40).resize((320, 240)), lib / "2023" / "x.jpg", "2023:01:02 03:04:05")
    os.rename(dng, lib / "2023" / "2023_01_02_03_04_05.dng")  # (its preview is what's read)
    pf.index_folder(lib, want_faces=False)
    assert db.execute("SELECT COUNT(*) FROM photos WHERE phash IS NOT NULL").fetchone()[0] == 2
    assert duplicates.find(db) == []


def test_a_burst_in_one_second_with_smaller_copies_of_each_shot(home, lib):
    """A phone's burst (three frames in one second, each also saved smaller): each small copy goes
    with its own frame, and the frames stay apart."""
    pf, db, _, duplicates = home
    for i in range(3):
        big = scene(50, shift=12 * i, size=(1200, 900))
        save(big, lib / "2023" / f"2023_02_26_15_35_26{'_' + str(i) if i else ''}.jpg", "2023:02:26 15:35:26",
             quality=95 - 3 * i)
        save(big.resize((400, 300)), lib / "2023" / f"small_{i}.jpg", "2023:02:26 15:35:26")
    pf.index_folder(lib, want_faces=False)
    groups = duplicates.find(db)
    pairs = sorted((os.path.basename(g.keeper.path), [os.path.basename(c.path) for c in g.extras]) for g in groups)
    assert all(len(extras) == 1 for _, extras in pairs)
    for keeper, (extra,) in pairs:
        n = keeper.removesuffix(".jpg").rpartition("_")[2]
        assert extra == f"small_{n if len(n) == 1 else 0}.jpg", pairs


def test_a_series_without_dates_is_not_duplicates(home, lib):
    """A shoot whose dates were lost: shots alike, the same size, nearly the same file size."""
    pf, db, _, duplicates = home
    for i in range(5):
        save(scene(60, shift=i), lib / "2024" / f"2024_01_01_00_00_00_{i}.jpg")
    pf.index_folder(lib, want_faces=False)
    assert duplicates.find(db) == []
