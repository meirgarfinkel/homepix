"""The date each photo and video is sorted, shown and named by: the rule the library's files were
renamed by (core/dates.py), and changing a date renames a file named by it."""

import datetime as dt
import os
import subprocess

import pytest
from test_metadata import edit_and_run, index, make_photo, row

from photofind.core import dates
from photofind.core import exiftool as et_mod

needs_exiftool = pytest.mark.skipif(not et_mod.available(), reason="needs ExifTool")


@pytest.fixture
def home(pf, lib, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    from photofind.core import fileops, metadata

    pf.add_folder(lib)
    db = pf.open_db()
    yield pf, db, fileops, metadata
    db.close()


def old_mtime(p):
    """As if copied onto this computer long after it was taken."""
    t = dt.datetime(2025, 11, 30, 9, 52).timestamp()
    os.utime(p, (t, t))
    return p


# --- the rule --------------------------------------------------------------------------------------

def test_the_rule():
    pick = dates.pick
    mtime = dt.datetime(2025, 11, 30, 9, 52).timestamp()
    exif = dt.datetime(2022, 12, 13, 15, 11, 16)
    # the name, when the photo holds no date (not when the file was copied)
    assert pick("/p/2022_04_01_07_32_31.jpg", [], mtime) == ("2022-04-01 07:32:31", "filename")
    # the earliest real one
    assert pick("/p/2022_12_13_15_11_18.heic", [(exif, "exif")], mtime) == ("2022-12-13 15:11:16", "exif")
    assert pick("/p/2019_01_02_03_04_05.jpg", [(exif, "exif")], mtime) == ("2019-01-02 03:04:05", "filename")
    # the same moment: the photo's own wins
    assert pick("/p/2022_12_13_15_11_16.jpg", [(exif, "exif")], mtime)[1] == "exif"
    # a placeholder never beats a real date, even an earlier one
    stamp = dt.datetime(2023, 1, 1)
    assert pick("/p/2023_05_28_23_07_00_38.jpg", [(stamp, "exif")], mtime) == ("2023-05-28 23:07:00", "filename")
    assert pick("/p/IMG_1.jpg", [(dt.datetime(1998, 2, 9, 6, 49), "exif"), (exif, "exif")], mtime)[0] == str(exif)
    # but it's better than nothing
    assert pick("/p/2015_01_01_00_00_00_3.jpg", [], mtime) == ("2015-01-01 00:00:00", "placeholder")
    assert pick("/p/IMG_1.jpg", [], mtime) == ("2025-11-30 09:52:00", "file-modified")


def test_dates_out_of_range_dont_count():
    assert dates.parse("0000:00:00 00:00:00") is None
    assert dates.parse("1970:01:01 00:00:00") is None
    assert dates.parse("2024:07:01 09:30:05+02:00") == dt.datetime(2024, 7, 1, 9, 30, 5)
    assert dates.in_name("IMG_4411.jpg") is None


def test_indexing_and_the_timeline_go_by_it(pf, lib):
    named = old_mtime(make_photo(lib / "2022_04_01_07_32_31.jpg"))  # no date inside: the name says
    exif = old_mtime(make_photo(lib / "IMG_1.jpg", exif={0x9003: "2023:06:01 10:00:00"}))
    stamped = old_mtime(make_photo(lib / "2023_05_28_23_07_00.jpg", exif={0x9003: "2023:01:01 00:00:00"}))
    nothing = make_photo(lib / "IMG_2.jpg")
    os.utime(nothing, (dt.datetime(2020, 1, 5).timestamp(),) * 2)
    pf.index_folder(lib, want_faces=False)
    from photofind.core.timeline import load_items

    db = pf.open_db()
    got = [(os.path.basename(it.path), it.taken) for it in load_items(db)]
    assert got == [("IMG_1.jpg", "2023-06-01 10:00:00"), ("2023_05_28_23_07_00.jpg", "2023-05-28 23:07:00"),
                   ("2022_04_01_07_32_31.jpg", "2022-04-01 07:32:31"), ("IMG_2.jpg", "2020-01-05 00:00:00")]
    sources = dict(db.execute("SELECT path, taken_source FROM photos"))
    assert sources[str(named)] == "filename" and sources[str(stamped)] == "filename"
    assert sources[str(exif)] == "exif" and sources[str(nothing)] == "file-modified"


@needs_exiftool
@pytest.mark.parametrize("name", ["2021_08_01_19_09_00.jpg", "IMG_1.heic", "a.png"])
def test_both_ways_of_reading_agree(lib, name):
    from photofind.core import metadata

    p = make_photo(lib / name, exif={0x9003: "2021:08:01 19:09:04", 0x9004: "2021:08:01 19:09:03"})
    d = metadata.read([p])[str(p)]
    assert dates.of_file(p, "photo", 0) == dates.from_tags(p, d.tags, 0)


# --- videos ----------------------------------------------------------------------------------------

@needs_exiftool
def test_a_video_goes_by_its_clock_and_a_live_photo_clip_stays_with_its_photo(home, lib):
    cv2 = pytest.importorskip("cv2")
    import numpy as np

    pf, db, fo, md = home
    photo = make_photo(lib / "IMG_7.jpg", exif={0x9003: "2021:03:04 10:00:00"})
    clip = lib / "IMG_7.mov"
    w = cv2.VideoWriter(str(clip), cv2.VideoWriter_fourcc(*"mp4v"), 10, (32, 32))
    for _ in range(10):
        w.write(np.zeros((32, 32, 3), np.uint8))
    w.release()
    local = dt.datetime(2021, 3, 4, 10, 0, 1)
    utc = local.astimezone(dt.UTC).replace(tzinfo=None)
    subprocess.run(["exiftool", "-q", "-overwrite_original", f"-QuickTime:CreateDate={utc:%Y:%m:%d %H:%M:%S}",
                    str(clip)], check=True)
    index(pf, lib)
    r = row(db, clip)
    assert r["taken"] == str(utc) and r["taken_source"] == "video"  # its clock, as its name would be
    assert r["moment"] == str(local) and r["kind"] == "motion"  # still its photo's clip
    assert fo.companions(db, [str(photo)]) == {str(photo): str(clip)}


# --- changing a date ------------------------------------------------------------------------------

@needs_exiftool
def test_moving_a_date_later_renames_a_file_named_by_it_and_undo_puts_both_back(home, lib):
    pf, db, fo, md = home
    p = old_mtime(make_photo(lib / "2022_04_01_07_32_31_2.jpg"))  # its name is all it has
    other = make_photo(lib / "2022_04_01_10_32_31.jpg")  # already has the name it'll want
    index(pf, lib)
    pid = row(db, p)["id"]
    result, cant = edit_and_run(db, fo, [p], md.Edit(shift=dt.timedelta(hours=3)))
    assert result.finished == 2 and not cant, (result, cant)
    moved = lib / "2022_04_01_10_32_31_1.jpg"
    assert moved.exists() and not p.exists() and other.exists()
    r = row(db, moved)
    assert (r["id"], r["taken"], r["taken_source"]) == (pid, "2022-04-01 10:32:31", "exif")
    assert md.read([moved])[str(moved)].taken == dt.datetime(2022, 4, 1, 10, 32, 31)

    fo.undo(db)
    assert p.exists() and not moved.exists()
    r = row(db, p)
    assert (r["id"], r["taken"]) == (pid, "2022-04-01 07:32:31")
    assert md.read([p])[str(p)].taken is None  # the date written is taken out again
    fo.redo(db)
    assert row(db, moved)["taken"] == "2022-04-01 10:32:31"


@needs_exiftool
def test_setting_a_date_renames_too_but_other_names_stay(home, lib):
    pf, db, fo, md = home
    a = make_photo(lib / "2019_05_06_07_08_09.jpg", exif={0x9003: "2019:05:06 07:08:09"})
    b = make_photo(lib / "Holiday.jpg", exif={0x9003: "2019:05:06 07:08:09"})
    index(pf, lib)
    when = dt.datetime(2020, 1, 2, 3, 4, 5)
    result, cant = edit_and_run(db, fo, [a, b], md.Edit(date=when))
    assert not cant and result.finished == 3
    assert (lib / "2020_01_02_03_04_05.jpg").exists() and b.exists()
    assert row(db, lib / "2020_01_02_03_04_05.jpg")["taken"] == row(db, b)["taken"] == str(when)


@needs_exiftool
def test_a_photo_dated_by_its_name_only_can_be_moved_but_a_guess_cant(home, lib):
    pf, db, fo, md = home
    named = old_mtime(make_photo(lib / "IMG_20220401_073231.jpg"))
    guessed = old_mtime(make_photo(lib / "IMG_9.jpg"))
    index(pf, lib)
    result, cant = edit_and_run(db, fo, [named, guessed], md.Edit(shift=dt.timedelta(minutes=-1)))
    assert [os.path.basename(p) for p, _ in cant] == ["IMG_9.jpg"] and "no date" in cant[0][1]
    assert row(db, named)["taken"] == "2022-04-01 07:31:31"  # (a name not made by its date stays)


def test_renaming_a_file_away_from_its_date(pf, lib):
    from photofind.core import fileops

    p = old_mtime(make_photo(lib / "2022_04_01_07_32_31.jpg"))
    exif = make_photo(lib / "2010_01_01_10_00_00.jpg", exif={0x9003: "2011:05:05 05:05:05"})
    pf.index_folder(lib, want_faces=False)
    db = pf.open_db()
    assert dict(db.execute("SELECT path, taken FROM photos"))[str(exif)] == "2010-01-01 10:00:00"
    fileops.run(db, "rename", "t", fileops.plan_rename(db, [(str(p), "Trip"), (str(exif), "Beach")]))
    got = dict(db.execute("SELECT path, taken FROM photos"))
    assert got[str(lib / "Trip.jpg")] == "2025-11-30 09:52:00"  # nothing else says when: a guess
    assert got[str(lib / "Beach.jpg")] == "2011-05-05 05:05:05"
    fileops.undo(db)
    assert dict(db.execute("SELECT path, taken FROM photos"))[str(p)] == "2022-04-01 07:32:31"


# --- an index from before ---------------------------------------------------------------------------

@needs_exiftool
def test_an_older_index_gets_the_new_dates_on_the_next_pass(pf, lib):
    p = old_mtime(make_photo(lib / "2022_04_01_07_32_31.jpg"))
    pf.index_folder(lib, want_faces=False)
    db = pf.open_db()
    # as 0.5 left it: the date from when the file changed, everything read
    db.execute("UPDATE photos SET taken='2025-11-30 09:52:00', taken_source='file-modified', details_done=1")
    db.commit()
    from photofind.core.db import _m7_dates

    db.execute("ALTER TABLE photos DROP COLUMN moment")
    _m7_dates(db)
    db.commit()
    info = pf.index_folder(lib, want_faces=False)
    assert info["done"] == 0  # nothing decoded again
    assert dict(db.execute("SELECT path, taken FROM photos"))[str(p)] == "2022-04-01 07:32:31"


@needs_exiftool
def test_a_phone_that_names_photos_in_utc_keeps_its_motion_clip(home, lib):
    """Pixel phones: PXL_20231001_060918.jpg (UTC) holding 09:09:18 (local), with a short .mp4 clip."""
    cv2 = pytest.importorskip("cv2")
    import numpy as np

    pf, db, fo, md = home
    photo = make_photo(lib / "2023_10_01_06_09_18.jpg", exif={0x9003: "2023:10:01 09:09:18"})
    clip = lib / "2023_10_01_06_09_20.mp4"
    w = cv2.VideoWriter(str(clip), cv2.VideoWriter_fourcc(*"mp4v"), 10, (32, 32))
    for _ in range(10):
        w.write(np.zeros((32, 32, 3), np.uint8))
    w.release()
    subprocess.run(["exiftool", "-q", "-overwrite_original", "-QuickTime:CreateDate=2023:10:01 06:09:20",
                    "-Keys:CreationDate=2023:10:01 09:09:20+03:00", str(clip)], check=True)
    index(pf, lib)
    assert row(db, photo)["taken"] == "2023-10-01 06:09:18"  # as its name says
    assert row(db, photo)["moment"] == "2023-10-01 09:09:18"
    assert row(db, clip)["kind"] == "motion"
    assert fo.companions(db, [str(photo)]) == {str(photo): str(clip)}


def test_a_short_video_dated_only_by_its_name_isnt_taken_for_a_live_photo(pf, lib):
    """An import batch named by one time (2023_05_28_23_07_00_1, _2, ...) holds photos and clips alike."""
    cv2 = pytest.importorskip("cv2")
    import numpy as np

    make_photo(lib / "2023_05_28_23_07_00_1.jpg")
    clip = lib / "2023_05_28_23_07_00_2.mp4"
    w = cv2.VideoWriter(str(clip), cv2.VideoWriter_fourcc(*"mp4v"), 10, (32, 32))
    for _ in range(10):
        w.write(np.zeros((32, 32, 3), np.uint8))
    w.release()
    pf.index_folder(lib, want_faces=False)
    db = pf.open_db()
    r = db.execute("SELECT kind, taken, taken_source FROM photos WHERE path=?", (str(clip),)).fetchone()
    if r["taken_source"] == "filename":  # (OpenCV leaves the video's clock unset)
        assert r["kind"] == "video"
