"""M7: the organizer names files by when they were taken and sorts them into year folders, by the
same rules the library was organized by (the Sep 2026 pipeline), previewed, and undoable."""

import datetime as dt
import os
import time

import numpy as np
import pytest
from conftest import own_trash
from PIL import Image
from test_fileops import contents, index_matches_disk, no_leftovers

_n = 0


def shot(path, taken=None, created=None, mtime=dt.datetime(2025, 11, 30, 9, 52)):
    """A photo with its own content; taken/created: its EXIF dates ("2021:08:01 19:09:04"). Its file
    looks copied onto the computer long after (mtime)."""
    global _n
    _n += 1
    path.parent.mkdir(parents=True, exist_ok=True)
    ex = Image.Exif()
    for tag, value in ((0x9003, taken), (0x9004, created)):
        if value:
            ex.get_ifd(0x8769)[tag] = value
    img = np.random.default_rng(_n).integers(0, 255, (40, 60, 3), dtype=np.uint8)
    Image.fromarray(img).save(path, "JPEG", exif=ex)
    os.utime(path, (mtime.timestamp(), mtime.timestamp()))
    return path


@pytest.fixture
def home(pf, lib, tmp_path, monkeypatch):
    own_trash(monkeypatch, tmp_path)
    from homepix.core import fileops, organize

    pf.add_folder(lib)
    db = pf.open_db()
    yield pf, db, fileops, organize
    db.close()


def plan(home, lib, scope=None):
    pf, db, _, organize = home
    pf.index_folder(lib, want_faces=False)
    return organize.plan(db, [lib], scope)


def apply(home, lib, scope=None):
    pf, db, fo, _ = home
    p = plan(home, lib, scope)
    fo.run(db, "organize", "Organize", p.steps())
    return p


def files(lib):
    return sorted(p for p in contents(lib) if not os.path.basename(p).startswith("."))


# --- the rules -----------------------------------------------------------------------------------

def test_a_folder_of_new_photos_is_named_and_sorted(home, lib):
    shot(lib / "new" / "IMG_1.JPG", "2021:08:01 19:09:04")
    shot(lib / "new" / "IMG_2.jpg", "2019:05:06 07:08:09")
    shot(lib / "new" / "sub" / "DSC_3.jpg", "2021:08:01 19:09:04")  # same second as IMG_1
    p = apply(home, lib, lib / "new")
    assert files(lib) == ["2019/2019_05_06_07_08_09.jpg", "2021/2021_08_01_19_09_04.jpg",
                          "2021/2021_08_01_19_09_04_1.jpg"]
    assert {c.why for c in p.changes} == {"to 2019", "to 2021"}
    index_matches_disk(home[1], lib)
    no_leftovers(lib)
    assert plan(home, lib).changes == []  # organized, it stays so


def test_the_tricky_dates_from_the_old_pipeline(home, lib):
    # the sRGB profile's 1998-02-09 06:49 stamp loses to the photo's real date
    shot(lib / "a.jpg", "1998:02:09 06:49:00", "2017:03:04 05:06:07")
    # a placeholder (1 January, midnight) is all there is: it still tells the year
    shot(lib / "b.jpg", "2016:01:01 00:00:00")
    # a real date in the name beats a placeholder in the photo
    shot(lib / "2015_07_08_09_10_11.jpg", "2015:01:01 00:00:00")
    # only when the file was last changed: no real date, so Unsorted, keeping its name
    shot(lib / "scan.JPG")
    apply(home, lib)
    assert files(lib) == ["2015/2015_07_08_09_10_11.jpg", "2016/2016_01_01_00_00_00.jpg",
                          "2017/2017_03_04_05_06_07.jpg", "Unsorted/scan.jpg"]


def test_only_the_extension_changes_case(home, lib):
    # (on Windows the new name is the old one's: it mustn't count as taken, giving _1)
    shot(lib / "2020" / "2020_01_02_03_04_05.JPG", "2020:01:02 03:04:05")
    shot(lib / "Unsorted" / "scan.PNG")
    apply(home, lib)
    assert files(lib) == ["2020/2020_01_02_03_04_05.jpg", "Unsorted/scan.png"]
    index_matches_disk(home[1], lib)


def test_what_is_already_in_order_is_left_alone(home, lib):
    shot(lib / "2020" / "2020_01_02_03_04_05.jpg", "2020:01:02 03:04:05")
    shot(lib / "2020" / "2020_01_02_03_04_05_3.jpg", "2020:01:02 03:04:05")  # its number is kept
    shot(lib / "2020" / "Trip" / "2020_06_07_08_09_10.jpg", "2020:06:07 08:09:10")  # a year's own folder
    shot(lib / "Unsorted" / "old" / "scan.jpg")
    before = files(lib)
    p = plan(home, lib)
    assert p.changes == [] and p.looked_at == 4
    apply(home, lib)
    assert files(lib) == before


def test_wrong_names_and_wrong_years_are_put_right(home, lib):
    shot(lib / "2020" / "2022_05_05_05_05_05.jpg", "2021:01:02 03:04:05")  # it's 2021's
    shot(lib / "2020" / "2020_01_02_03_04_06.JPG", "2020:01:02 03:04:01")  # misnamed, and .JPG
    shot(lib / "Unsorted" / "found.jpg", "2019:09:09 09:09:09")  # a date was found after all
    p = apply(home, lib)
    assert files(lib) == ["2019/2019_09_09_09_09_09.jpg", "2020/2020_01_02_03_04_01.jpg",
                          "2021/2021_01_02_03_04_05.jpg"]
    assert sorted(c.why for c in p.changes) == ["renamed", "to 2019", "to 2021"]


def test_a_name_taken_by_a_file_that_stays_gets_a_number(home, lib):
    shot(lib / "2020" / "2020_01_02_03_04_05.jpg", "2020:01:02 03:04:05")
    shot(lib / "2020" / "2020_01_02_03_04_05_1.jpg", "2020:01:02 03:04:05")
    shot(lib / "new" / "IMG_1.jpg", "2020:01:02 03:04:05")
    p = plan(home, lib)
    assert [os.path.basename(c.dst) for c in p.changes] == ["2020_01_02_03_04_05_2.jpg"]


def test_a_chain_each_taking_the_name_the_next_one_leaves(home, lib):
    a = shot(lib / "2020" / "2020_01_01_11_00_00.jpg", "2020:01:01 10:00:00")
    b = shot(lib / "2020" / "2020_01_01_12_00_00.jpg", "2020:01:01 11:00:00")
    was = {p.name: p.read_bytes() for p in (a, b)}
    p = apply(home, lib)
    assert len(p.changes) == 2 and files(lib) == ["2020/2020_01_01_10_00_00.jpg", "2020/2020_01_01_11_00_00.jpg"]
    assert (lib / "2020" / "2020_01_01_10_00_00.jpg").read_bytes() == was["2020_01_01_11_00_00.jpg"]
    assert (lib / "2020" / "2020_01_01_11_00_00.jpg").read_bytes() == was["2020_01_01_12_00_00.jpg"]


# --- what goes with a photo ------------------------------------------------------------------------

def with_clip(home, lib, photo, clip, clip_taken):
    """photo indexed, and clip beside it as its Live Photo's motion (dated as its own clock says)."""
    pf, db, _, _ = home
    pf.index_folder(lib, want_faces=False)
    clip.write_bytes(b"clip")
    db.execute("INSERT INTO photos (path, qhash, kind, taken, taken_source, moment, duration) "
               "SELECT ?, 'q', 'motion', ?, 'video', taken, 2 FROM photos WHERE path=?",
               (str(clip), clip_taken, str(photo)))
    db.commit()


def test_a_live_photo_clip_and_sidecars_go_with_their_photo_and_undo_brings_all_back(home, lib):
    _, db, fo, organize = home
    photo = shot(lib / "new" / "IMG_1.jpg", "2022:04:01 07:32:31")
    (lib / "new" / "IMG_1.AAE").write_text("edits")
    (lib / "new" / "IMG_1.jpg.json").write_text("{}")
    (lib / "new" / "IMG_2.AAE").write_text("no photo of its own: stays")
    with_clip(home, lib, photo, lib / "new" / "IMG_1.MOV", "2022-04-01 04:32:32")  # its clock is UTC
    before = contents(lib)
    p = organize.plan(db, [lib])
    fo.run(db, "organize", "Organize", p.steps())
    assert files(lib) == ["2022/2022_04_01_07_32_31.aae", "2022/2022_04_01_07_32_31.jpg",
                          "2022/2022_04_01_07_32_31.jpg.json", "2022/2022_04_01_07_32_31.mov",
                          "new/IMG_2.AAE"]
    assert sorted(c.why for c in p.changes) == ["to 2022"] + ["with its photo"] * 3
    assert organize.plan(db, [lib]).changes == []
    fo.undo(db)
    assert contents(lib) == before
    assert {r[0] for r in db.execute("SELECT path FROM photos")} == {str(photo), str(lib / "new" / "IMG_1.MOV")}


def test_a_live_photo_clip_is_named_like_its_photo_where_it_is(home, lib):
    """Most of the library's clips were named by the day they were copied."""
    _, db, _, organize = home
    photo = shot(lib / "2022" / "2022_04_01_07_32_31_1.jpg", "2022:04:01 07:32:31")
    with_clip(home, lib, photo, lib / "2022" / "2025_11_27_19_34_30.mov", "2025-11-27 19:34:30")
    p = organize.plan(db, [lib])
    assert [(os.path.basename(c.dst), c.why) for c in p.changes] == [("2022_04_01_07_32_31_1.mov", "with its photo")]


def test_a_burst_of_live_photos_keeps_each_clip_with_its_own_photo(home, lib):
    """A photo a second before a Live Photo (with no clip of its own) mustn't take that photo's clip,
    and so on down the burst, each clip named after the photo a second before its own."""
    pf, db, _, organize = home
    a = shot(lib / "2022" / "IMG_1.jpg", "2022:12:14 13:51:41")
    b = shot(lib / "2022" / "IMG_2.jpg", "2022:12:14 13:51:42")
    c = shot(lib / "2022" / "IMG_3.jpg", "2022:12:14 13:51:43")
    with_clip(home, lib, b, lib / "2022" / "2022_12_14_18_56_56.mov", "2022-12-14 18:56:56")
    clip = lib / "2022" / "2022_12_14_18_56_58.mov"  # (with_clip again would index the first one afresh)
    clip.write_bytes(b"clip")
    db.execute("INSERT INTO photos (path, qhash, kind, taken, taken_source, moment, duration) "
               "SELECT ?, 'q2', 'motion', '2022-12-14 18:56:58', 'video', taken, 2 FROM photos WHERE path=?",
               (str(clip), str(c)))
    db.commit()
    p = organize.plan(db, [lib])
    clips = {os.path.basename(ch.src): os.path.basename(ch.dst) for ch in p.changes if ch.why == "with its photo"}
    assert clips == {"2022_12_14_18_56_56.mov": "2022_12_14_13_51_42.mov",
                     "2022_12_14_18_56_58.mov": "2022_12_14_13_51_43.mov"}
    assert a.exists()


def test_a_clip_stays_with_the_copy_left_behind(home, lib):
    """Two copies of one Live Photo: the clip is as much the kept one's, so it doesn't go with the other."""
    _, db, fo, _ = home
    kept = shot(lib / "2023" / "2023_05_22_17_00_08_1.jpg", "2023:05:22 17:00:08")
    extra = shot(lib / "2023" / "2023_05_22_17_00_08.jpg", "2023:05:22 17:00:08")
    clip = lib / "2023" / "2023_05_22_21_00_15.mov"
    with_clip(home, lib, kept, clip, "2023-05-22 21:00:15")
    assert fo.companions(db, [extra]) == {}
    assert fo.with_companions(db, [extra]) == [str(extra)]
    assert fo.companions(db, [kept]) == {}  # either way round: it stays with the other
    assert list(fo.companions(db, [kept, extra]).values()) == [str(clip)]  # both going: it goes too


# --- at the size of a real library ------------------------------------------------------------

def test_thirty_thousand_files(home, lib):
    _, db, _, organize = home
    start = dt.datetime(2015, 1, 1)
    rows = []
    for i in range(30_000):
        when = start + dt.timedelta(minutes=97 * i)
        name = when.strftime("%Y_%m_%d_%H_%M_%S") if i % 50 else f"IMG_{i}"  # 600 to do
        rows.append((str(lib / str(when.year) / f"{name}.jpg"), f"q{i}", str(when), "exif"))
    db.executemany("INSERT INTO photos (path, qhash, taken, taken_source) VALUES (?, ?, ?, ?)", rows)
    db.commit()
    t = time.perf_counter()
    p = organize.plan(db, [lib])
    took = time.perf_counter() - t
    assert len(p.changes) == 600 and p.looked_at == 30_000
    assert took < 5, took


def test_only_a_folder_in_the_library(home, lib, tmp_path):
    _, db, _, organize = home
    with pytest.raises(ValueError):
        organize.plan(db, [lib], tmp_path / "elsewhere")


def test_the_command_line_previews_then_applies(home, lib, capsys):
    from types import SimpleNamespace

    from homepix import cli

    shot(lib / "new" / "IMG_1.jpg", "2021:08:01 19:09:04")
    home[0].index_folder(lib, want_faces=False)
    args = SimpleNamespace(folder=None, apply=False, all=False, show=20)
    cli.cmd_organize(args)
    assert "1 of 1 files would change" in capsys.readouterr().out and files(lib) == ["new/IMG_1.jpg"]
    cli.cmd_organize(SimpleNamespace(**{**vars(args), "apply": True}))
    assert files(lib) == ["2021/2021_08_01_19_09_04.jpg"]
    home[2].undo(home[1])  # the app's Undo takes it back
    assert files(lib) == ["new/IMG_1.jpg"]


# --- in the app ----------------------------------------------------------------------------------

def test_tidying_from_the_window_previews_then_organizes_and_undo_takes_it_back(pf, lib, monkeypatch):
    from test_faces_ui import app as _app
    from test_faces_ui import wait_until

    from homepix.ui.main_window import MainWindow
    from homepix.ui.views import changes_dialog

    app = _app.__wrapped__()
    shot(lib / "new" / "IMG_1.jpg", "2021:08:01 19:09:04")
    shot(lib / "2019" / "2019_05_06_07_08_09.jpg", "2019:05:06 07:08:09")
    pf.add_folder(lib)
    pf.index_folder(lib, want_faces=False)
    shown = []

    def exec_(dlg):
        model = dlg.table.model()
        shown.append([model.data(model.index(0, c)) for c in range(3)])
        return 1  # Organize

    monkeypatch.setattr(changes_dialog.ChangesDialog, "exec", exec_)
    w = MainWindow(index=False)
    w.show()
    wait_until(app, lambda: w.loaded)
    try:
        w.organizer.tidy()
        # (the files are only read once it's done: Windows won't open one that's being moved)
        wait_until(app, lambda: not w.ops.busy
                   and files(lib) == ["2019/2019_05_06_07_08_09.jpg", "2021/2021_08_01_19_09_04.jpg"])
        assert shown == [[os.path.normpath("new/IMG_1.jpg"), os.path.normpath("2021/2021_08_01_19_09_04.jpg"),
                          "Into 2021/"]]
        w.ops.undo()
        wait_until(app, lambda: not w.ops.busy and files(lib) == ["2019/2019_05_06_07_08_09.jpg", "new/IMG_1.jpg"])
    finally:
        w.close()
