"""M8: duplicates. A test library with planted copies (exact, resized, saved again, converted, a video)
and bursts of different shots: every copy is found, no burst is, and setting extras aside (or saying
they aren't duplicates) is undoable. Duplicates/ is adopted: its files are known copies, kept out of
the timeline and search."""

import os
import shutil

import numpy as np
import pytest
from conftest import own_trash, rel
from PIL import Image, ImageDraw

from photofind.core.util import quick_hash


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
    from photofind.core import duplicates, fileops

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
    return duplicates.find(db, [lib])


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
    from photofind.core.duplicates import distance, phash

    a = scene(9, size=(1200, 900))
    assert distance(phash(a), phash(a.resize((300, 225)))) <= 2
    assert distance(phash(a), phash(scene(10, size=(1200, 900)))) > 12


# --- doing something about them ----------------------------------------------------------------------

def test_extras_go_to_duplicates_named_after_their_keeper_and_undo_brings_them_back(home, lib, planted):
    pf, db, fo, duplicates = home
    groups = groups_of(home, lib)
    fo.run(db, "duplicates", "Set aside", duplicates.plan_extras(db, groups, "duplicates", [lib]))
    aside = sorted(os.listdir(lib / "Duplicates"))
    assert aside == ["2020_01_01_10_00_00.jpg", "2021_02_02_10_00_00.jpg", "2022_03_03_10_00_00.jpg",
                     "2023_04_04_10_00_00.png", "v.mp4"]
    assert groups_of(home, lib) == []  # nothing left to review
    known = duplicates.known(db, [lib])
    keepers = {db.execute("SELECT path FROM photos WHERE id=?", (k,)).fetchone()[0] for k, _ in known.values()}
    assert keepers == {str(planted[x]) for x in ("a", "b", "c", "d", "v")}
    fo.undo(db)
    assert not os.listdir(lib / "Duplicates") and len(groups_of(home, lib)) == 5


def test_extras_to_the_trash(home, lib, planted):
    _, db, fo, duplicates = home
    groups = [g for g in groups_of(home, lib) if g.how == "exact"]
    fo.run(db, "trash", "Trash", duplicates.plan_extras(db, groups, "trash", [lib]))
    assert not planted["a exact"].exists() and not planted["v copy"].exists() and planted["a"].exists()


def test_choosing_another_keeper(home, lib, planted):
    _, db, fo, duplicates = home
    g = next(g for g in groups_of(home, lib) if planted_name(g, planted) == "b")
    small = next(c for c in g.copies if c.path == str(planted["b small"]))
    g.keep(small.id)
    fo.run(db, "duplicates", "Set aside", duplicates.plan_extras(db, [g], "duplicates", [lib]))
    assert planted["b small"].exists() and os.listdir(lib / "Duplicates") == ["b.jpg"]


def test_not_duplicates_is_never_offered_again_until_undone(home, lib, planted):
    _, db, fo, duplicates = home
    g = next(g for g in groups_of(home, lib) if planted_name(g, planted) == "d")
    fo.run(db, "apart", "Not duplicates", [duplicates.plan_apart(db, [g])])
    assert ["d", "d png"] not in as_names(groups_of(home, lib), planted)
    assert duplicates.plan_apart(db, [g]) is None  # nothing more to say
    fo.undo(db)
    assert ["d", "d png"] in as_names(groups_of(home, lib), planted)


# --- adopting what the old pipeline set aside --------------------------------------------------------

def test_the_duplicates_folder_is_known_and_out_of_the_timeline_and_search(home, lib):
    pf, db, _, duplicates = home
    keeper = save(scene(1), lib / "2020" / "2020_01_01_10_00_00.jpg", "2020:01:01 10:00:00")
    shutil.copy2(keeper, lib / "2020" / "x.tmp")
    save(scene(2), lib / "2021" / "2021_02_02_10_00_00.jpg", "2021:02:02 10:00:00")
    save(scene(3), lib / "2022" / "2022_03_03_10_00_00.png")
    (lib / "Duplicates").mkdir()
    os.rename(lib / "2020" / "x.tmp", lib / "Duplicates" / "2020_01_01_10_00_00.jpg")  # an exact copy
    save(scene(2).resize((320, 240)), lib / "Duplicates" / "IMG_4.jpg")  # a smaller one
    save(scene(32), lib / "Duplicates" / "2022_03_03_10_00_00_1.jpg")  # named after its keeper (edited since)
    save(scene(30), lib / "Duplicates" / "BSEA6346.jpg")  # the csv knows its keeper (renamed since)
    save(scene(31), lib / "2016" / "2016_01_01_00_00_00.jpg")
    (lib / "duplicate_mapping.csv").write_text(
        "kept_original_path,duplicate_original_path,duplicate_current_path_in_Duplicates\n"
        "2016/2016_01_01_00_00_00.jpg,Unsorted/BSEA6346.jpg,Duplicates/BSEA6346.jpg\n")
    pf.index_folder(lib, want_faces=False)
    known = duplicates.known(db, [lib])
    path = {r[0]: r[1] for r in db.execute("SELECT id, path FROM photos")}
    got = {os.path.basename(path[d]): (rel(path[k], lib), how) for d, (k, how) in known.items()}
    assert got == {"2020_01_01_10_00_00.jpg": ("2020/2020_01_01_10_00_00.jpg", "exact"),
                   "IMG_4.jpg": ("2021/2021_02_02_10_00_00.jpg", "near"),
                   "2022_03_03_10_00_00_1.jpg": ("2022/2022_03_03_10_00_00.png", "name"),
                   "BSEA6346.jpg": ("2016/2016_01_01_00_00_00.jpg", "mapping")}
    assert duplicates.find(db, [lib]) == []  # set aside already: nothing to review

    from photofind.core.timeline import load_items

    shown = {os.path.relpath(it.path, lib) for it in load_items(db, library=[lib])}
    assert not any(p.startswith("Duplicates") for p in shown) and len(shown) == 4
    assert len(load_items(db, folder=lib / "Duplicates", library=[lib])) == 4  # it can still be opened
    assert len(load_items(db, folder=lib, library=[lib])) == 4

    from photofind.core.search import SearchIndex

    index = SearchIndex()
    index.text_model = lambda: type("T", (), {"query": staticmethod(lambda q: np.ones(512, np.float32))})()
    assert index.search("anything")["total"] == 4
    assert index.search("anything", folder=str(lib / "Duplicates"))["total"] == 4


def test_the_command_line_reports(home, lib, planted, capsys):
    from types import SimpleNamespace

    from photofind import cli

    cli.cmd_duplicates(SimpleNamespace(show=10))
    out = capsys.readouterr().out
    assert "Set aside in Duplicates/: 0" in out and "5 groups, 5 extra copies" in out


# --- in the app ----------------------------------------------------------------------------------

def test_reviewing_in_the_window(home, lib, planted, monkeypatch):
    from test_faces_ui import app as _app
    from test_faces_ui import wait_until

    from photofind.ui.main_window import MainWindow
    from photofind.ui.views import changes_dialog
    from photofind.ui.views.sidebar import DUPES

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
        w.dupes.aside_button.click()
        wait_until(app, lambda: (lib / "Duplicates").is_dir() and len(os.listdir(lib / "Duplicates")) == 4
                   and not w.ops.busy)
        assert os.path.join("Duplicates", "b.jpg") in shown[0] and planted["b small"].exists() and not planted["b"].exists()
        wait_until(app, lambda: w.dupes.groups == [])
        w.ops.undo()
        wait_until(app, lambda: len(w.dupes.groups) == 4 and not w.ops.busy)
        assert planted["b"].exists()
    finally:
        w.close()


def test_comparing_a_groups_copies_large(home, lib, planted):
    """Double-click a copy: the viewer shows that group's copies, the arrows go round them, K keeps
    the one shown, and the copies stay where they were on the page."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent
    from test_faces_ui import app as _app
    from test_faces_ui import wait_until

    from photofind.ui.main_window import MainWindow
    from photofind.ui.views.sidebar import DUPES

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
    assert duplicates.find(db, [lib]) == []


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
    groups = duplicates.find(db, [lib])
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
    assert duplicates.find(db, [lib]) == []
