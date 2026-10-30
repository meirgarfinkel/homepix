"""Details written into the photos themselves: round trips in each format, names that survive,
undo that restores the original values, dates shifted, photos turned, and crashes."""

import datetime as dt
import json
import os
import subprocess
import sys
import textwrap

import pytest
from conftest import et_command, open_window, wait_until
from PIL import Image

from homepix.core import exiftool as et_mod
from homepix.core import fileops as fo
from homepix.core import metadata as md

pytestmark = pytest.mark.skipif(not et_mod.available(), reason="needs ExifTool")

FORMATS = ["jpg", "png", "heic", "dng", "tif"]


def make_photo(path, orientation=None, exif=None):
    """A small photo, red on its left half, in the format its name says."""
    import pillow_heif

    pillow_heif.register_heif_opener()
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGB", (80, 40), (0, 0, 255))
    im.paste((255, 0, 0), (0, 0, 40, 40))
    ex = Image.Exif()
    if orientation:
        ex[0x112] = orientation
    for tag, value in (exif or {}).items():
        if tag in (0x9003, 0x9004, 0x9010, 0x9011):  # dates and their offsets live in the Exif IFD
            ex.get_ifd(0x8769)[tag] = value
        else:
            ex[tag] = value
    ext = path.suffix.lower()
    if ext == ".dng":
        im.save(path, "TIFF", tiffinfo={50706: (1, 4, 0, 0), 271: "Test", 272: "Camera", **dict(ex)})
    elif ext == ".heic":
        im.save(path, exif=ex.tobytes())
    elif ext == ".png":
        im.save(path, exif=ex.tobytes()) if exif or orientation else im.save(path)
    else:
        im.save(path, exif=ex.tobytes())
    return path


def index(pf, lib):
    return pf.index_folder(lib, want_faces=False)


def edit_and_run(db, fo, paths, edit):
    steps, cant = fo.plan_details(db, [str(p) for p in paths], edit)
    result = fo.run(db, "details", "test", steps)
    return result, cant


def row(db, path):
    return dict(db.execute("SELECT * FROM photos WHERE path=?", (str(path),)).fetchone())


# --- orientation arithmetic -----------------------------------------------------------------------


def test_turning_four_times_comes_back_and_boxes_follow():
    from homepix.core.metadata import move_box, turned

    for o in range(1, 9):
        a = o
        for _ in range(4):
            a = turned(a, "right")
        assert a == o
        assert turned(turned(o, "left"), "right") == o
        assert turned(turned(o, "flip-h"), "flip-h") == o
    assert turned(1, "right") == 6 and turned(1, "left") == 8 and turned(6, "right") == 3
    box = (0.1, 0.2, 0.3, 0.1)  # left part, near the top
    x, y, w, h = move_box(box, 1, 6)  # turned right: the top edge goes to the right
    assert (round(x, 6), round(y, 6), round(w, 6), round(h, 6)) == (0.7, 0.1, 0.1, 0.3)
    for o in range(1, 9):
        back = move_box(move_box(box, 1, o), o, 1)
        assert all(abs(a - b) < 1e-9 for a, b in zip(back, box, strict=True))


# --- round trips ----------------------------------------------------------------------------------


@pytest.mark.parametrize("ext", FORMATS)
def test_details_round_trip(home, lib, ext):
    pf, db = home
    p = make_photo(lib / f"a.{ext}")
    index(pf, lib)
    mtime = os.stat(p).st_mtime
    when = dt.datetime(2019, 5, 6, 7, 8, 9)
    edit = md.Edit(
        description="Grandma's 90th\nin the garden, ünïcode ✓",
        keywords=["family", "garden, back"],
        date=when,
        gps=(31.778, -35.235),
    )
    result, cant = edit_and_run(db, fo, [p], edit)
    assert cant == [] and result.finished == 1, (cant, result)
    d = md.read([p])[str(p)]
    assert d.description == edit.description
    assert d.keywords == ["family", "garden, back"]
    assert d.taken == when
    assert d.gps == pytest.approx((31.778, -35.235), abs=1e-5)
    assert abs(os.stat(p).st_mtime - mtime) < 1  # its modified time is kept

    from homepix.core import dates
    from homepix.core.util import quick_hash

    assert dates.quick_file_date(p, "photo", 0)[:2] == ("2019-05-06 07:08:09", "exif")  # what indexing reads agrees
    r = row(db, p)
    assert r["qhash"] == quick_hash(p, os.path.getsize(p)) and r["size"] == os.path.getsize(p)
    assert (r["taken"], json.loads(r["keywords"]), r["description"]) == (
        "2019-05-06 07:08:09",
        ["family", "garden, back"],
        edit.description,
    )
    info = index(pf, lib)
    assert (info["done"], info["moved"], info["removed"]) == (0, 0, 0)  # nothing to index again


def test_names_survive_editing(home, lib):
    pf, db = home
    p = make_photo(lib / "a.jpg")
    index(pf, lib)
    pid = row(db, p)["id"]
    db.execute("INSERT INTO people (name) VALUES ('Ann')")
    db.execute(
        "INSERT INTO faces (photo_id, x, y, w, h, score, emb, person_id) VALUES (?,.1,.2,.3,.1,1,?,1)",
        (pid, b"\x01\x02"),
    )
    db.execute("UPDATE photos SET faces_done=1")  # as if its faces had been found when indexed
    db.commit()
    edit_and_run(db, fo, [p], md.Edit(keywords=["party"], description="Ann"))
    pf.index_folder(lib, want_faces=True)  # a full pass, faces and all, afterwards
    assert db.execute("SELECT p.name FROM faces f JOIN people p ON p.id=f.person_id").fetchall()[0][0] == "Ann"
    assert row(db, p)["id"] == pid


def test_undo_puts_back_exactly_what_was_there(home, lib):
    pf, db = home
    p = make_photo(lib / "a.jpg", exif={270: "OLYMPUS DIGITAL CAMERA", 0x9003: "2010:01:02 03:04:05"})
    subprocess.run(
        [*et_command(), "-q", "-overwrite_original", "-IPTC:Keywords=old one", "-IPTC:Keywords=old two", str(p)],
        check=True,
    )
    index(pf, lib)
    original = md.read([p])[str(p)].tags
    assert md.read([p])[str(p)].description == ""  # the camera's words aren't a description
    edit_and_run(db, fo, [p], md.Edit(description="New", keywords=["x"], date=dt.datetime(2020, 1, 1), gps=(1.5, 2.5)))
    changed = md.read([p])[str(p)]
    assert changed.tags["IPTC:Keywords"] == "x"  # the older tags are kept in step
    assert changed.tags["IFD0:ImageDescription"] == "New"
    fo.undo(db)
    assert md.read([p])[str(p)].tags == original
    r = row(db, p)
    assert (r["taken"], json.loads(r["keywords"])) == ("2010-01-02 03:04:05", ["old one", "old two"])
    fo.redo(db)
    assert md.read([p])[str(p)].description == "New"


def test_shift_a_batch_and_the_live_photo_clip_moves_with_it(home, lib):
    cv2 = pytest.importorskip("cv2")
    import numpy as np

    pf, db = home
    a = make_photo(lib / "IMG_1.jpg", exif={0x9003: "2021:03:04 10:00:00"})
    b = make_photo(lib / "IMG_2.jpg", exif={0x9003: "2021:03:05 18:30:00"})
    clip = lib / "IMG_1.mov"
    w = cv2.VideoWriter(str(clip), cv2.VideoWriter_fourcc(*"mp4v"), 10, (32, 32))
    for _ in range(10):
        w.write(np.zeros((32, 32, 3), np.uint8))
    w.release()
    local = dt.datetime(2021, 3, 4, 10, 0, 1)
    utc = local.astimezone(dt.UTC).strftime("%Y:%m:%d %H:%M:%S")
    subprocess.run([*et_command(), "-q", "-overwrite_original", f"-QuickTime:CreateDate={utc}", str(clip)], check=True)
    index(pf, lib)
    assert row(db, clip)["kind"] == "motion"
    result, cant = edit_and_run(db, fo, [a, b], md.Edit(shift=dt.timedelta(hours=-3, minutes=-15)))
    assert result.finished == 3 and not cant
    assert row(db, a)["taken"] == "2021-03-04 06:45:00" and row(db, b)["taken"] == "2021-03-05 15:15:00"
    clock = dt.datetime.strptime(utc, "%Y:%m:%d %H:%M:%S") - dt.timedelta(hours=3, minutes=15)
    assert row(db, clip)["taken"] == str(clock)  # a video's date is its clock, as stored (like its name)
    assert row(db, clip)["moment"] == "2021-03-04 06:45:01"  # its local time, to stay with its photo
    index(pf, lib)
    assert row(db, clip)["kind"] == "motion"  # still its photo's clip


def test_setting_a_date_on_a_photo_with_none(home, lib):
    pf, db = home
    p = make_photo(lib / "a.png")
    index(pf, lib)
    assert row(db, p)["taken_source"] == "file-modified"
    result, cant = edit_and_run(db, fo, [p], md.Edit(shift=dt.timedelta(hours=1)))
    assert cant and "no date" in cant[0][1]
    edit_and_run(db, fo, [p], md.Edit(date=dt.datetime(2001, 2, 3, 4, 5, 6)))
    assert row(db, p)["taken"] == "2001-02-03 04:05:06"


# --- turning ----------------------------------------------------------------------------------------


def red_side(path):
    import pillow_heif
    from PIL import ImageOps

    pillow_heif.register_heif_opener()
    img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    w, h = img.size
    strips = {
        "left": (0, 0, w // 4, h),
        "right": (w - w // 4, 0, w, h),
        "top": (0, 0, w, h // 4),
        "bottom": (0, h - h // 4, w, h),
    }

    def red(box):  # every pixel of the strip red
        r, g, b = (img.crop(box).getextrema()[c] for c in range(3))
        return r[0] > 200 and b[1] < 80

    return [k for k, box in strips.items() if red(box)]


@pytest.mark.parametrize("ext", ["jpg", "heic", "dng"])
def test_turning_right_and_back(home, lib, ext):
    pf, db = home
    p = make_photo(lib / f"a.{ext}", orientation=1 if ext != "heic" else 6)
    index(pf, lib)
    start = red_side(p)
    pid = row(db, p)["id"]
    db.execute("INSERT INTO faces (photo_id, x, y, w, h, score, emb) VALUES (?,.1,.2,.3,.1,1,?)", (pid, b"\x01"))
    db.commit()
    result, cant = edit_and_run(db, fo, [p], md.Edit(turn="right"))
    assert result.finished == 1, cant
    after = {"left": "top", "top": "right", "right": "bottom", "bottom": "left"}
    assert red_side(p) == [after[s] for s in start]
    box = db.execute("SELECT x, y, w, h FROM faces").fetchone()
    assert tuple(round(v, 6) for v in box) != (0.1, 0.2, 0.3, 0.1)
    fo.undo(db)
    assert red_side(p) == start
    assert tuple(round(v, 6) for v in db.execute("SELECT x, y, w, h FROM faces").fetchone()) == (0.1, 0.2, 0.3, 0.1)


def test_a_heic_cant_be_flipped_and_says_why(home, lib):
    pf, db = home
    p = make_photo(lib / "a.heic", orientation=6)
    index(pf, lib)
    steps, cant = fo.plan_details(db, [str(p)], md.Edit(turn="flip-h"))
    assert steps == [] and "flipped" in cant[0][1]


def test_a_format_exiftool_cant_write_is_reported(home, lib):
    pf, db = home
    p = lib / "a.gif"
    Image.new("RGB", (8, 8)).save(p)
    index(pf, lib)
    steps, cant = fo.plan_details(db, [str(p)], md.Edit(description="x"))
    result = fo.run(db, "details", "t", steps)
    assert result.finished == 1 or result.skipped  # either it can, or it says why


# --- reading what other apps wrote ------------------------------------------------------------------


def test_indexing_reads_keywords_and_descriptions_written_elsewhere(home, lib):
    pf, db = home
    p = make_photo(lib / "a.jpg")
    subprocess.run(
        [
            *et_command(),
            "-q",
            "-overwrite_original",
            "-XMP-dc:Subject=trip",
            "-XMP-dc:Description=From digiKam",
            str(p),
        ],
        check=True,
    )
    index(pf, lib)
    r = row(db, p)
    assert (json.loads(r["keywords"]), r["description"], r["details_done"]) == (["trip"], "From digiKam", 1)
    subprocess.run([*et_command(), "-q", "-overwrite_original", "-XMP-dc:Subject=home", str(p)], check=True)
    os.utime(p, (os.stat(p).st_atime, os.stat(p).st_mtime + 5))  # (as a real edit later on would)
    index(pf, lib)  # changed outside: read again
    assert json.loads(row(db, p)["keywords"]) == ["home"]


# --- killed partway ---------------------------------------------------------------------------------

KILLER = textwrap.dedent("""
    import os, sys
    from homepix.core import fileops, metadata
    from homepix.core.db import open_db
    real = metadata.write
    count = 0
    def write(*a, **k):
        global count
        real(*a, **k)
        count += 1
        if count >= int(sys.argv[2]):
            os._exit(9)
    metadata.write = write
    db = open_db()
    paths = sorted(os.path.join(sys.argv[1], n) for n in os.listdir(sys.argv[1]) if n.endswith(".jpg"))
    steps, _ = fileops.plan_details(db, paths, metadata.Edit(description="crash"))
    fileops.run(db, "details", "test", steps)
    os._exit(0)
""")


@pytest.mark.parametrize("after", [1, 2])
def test_killed_while_writing_details(home, lib, tmp_path, after):
    pf, db = home
    for i in range(3):
        make_photo(lib / f"{i}.jpg")
    index(pf, lib)
    script = tmp_path / "killer.py"
    script.write_text(KILLER)
    r = subprocess.run(
        [sys.executable, str(script), str(lib), str(after)], env=dict(os.environ, PYTHONPATH=os.getcwd())
    )
    assert r.returncode == 9
    fo.recover(db)
    from homepix.core.util import quick_hash

    changed = 0
    for i in range(3):
        p = lib / f"{i}.jpg"
        rw = row(db, p)
        assert rw["qhash"] == quick_hash(p, os.path.getsize(p))  # the index matches every file
        assert (rw["description"] or "") == md.read([p])[str(p)].description
        changed += rw["description"] == "crash"
    assert changed == after
    assert not [n for n in os.listdir(lib) if "exiftool_tmp" in n]
    fo.undo(db)
    assert all(md.read([lib / f"{i}.jpg"])[str(lib / f"{i}.jpg")].description == "" for i in range(3))


# --- in the app ----------------------------------------------------------------------------------------


@pytest.fixture
def window(home, lib, app):
    pf, db = home
    for i in range(3):
        make_photo(lib / f"IMG_{i}.jpg", exif={0x9003: f"2022:0{i + 1}:01 12:00:00"})
    index(pf, lib)
    w = open_window(app, lambda w: len(w.timeline.items) == 3, size=(900, 600))
    yield w
    w.close()


def test_editing_details_of_several_from_the_dialog(window, lib, app, monkeypatch):
    from homepix.core import metadata
    from homepix.ui.views import details_dialogs

    w = window
    w.timeline.select_all()

    def fill(dlg):
        dlg.keywords.setText("trip")
        dlg._touched.add("keywords")
        return True

    monkeypatch.setattr(details_dialogs.DetailsDialog, "exec", fill)
    w.photo.details.trigger()
    wait_until(app, lambda: not w.ops.busy and w.files.undo.text() == "Undo Change the details of 3 photos")
    found = metadata.read([it.path for it in w.timeline.items])
    assert all(d.keywords == ["trip"] for d in found.values())


def test_moving_the_date_of_several(window, app, monkeypatch):
    from homepix.ui.views import details_dialogs

    w = window
    w.timeline.select_all()
    before = sorted(it.taken for it in w.timeline.items)

    def shift(dlg):
        dlg.hours.setValue(-2)
        return True

    monkeypatch.setattr(details_dialogs.DateDialog, "exec", shift)
    w.photo.date.trigger()
    wait_until(app, lambda: not w.ops.busy and sorted(it.taken for it in w.timeline.items) != before)
    after = sorted(it.taken for it in w.timeline.items)
    assert after == [(dt.datetime.fromisoformat(t) - dt.timedelta(hours=2)).isoformat(sep=" ") for t in before]


def test_rotating_in_the_viewer_shows_it_turned(window, app):
    w = window
    w.open_viewer(0)
    wait_until(app, lambda: w.viewer._best_image() is not None)
    before = w.viewer._best_image()[0].size()
    w.photo.turns["right"].trigger()
    wait_until(
        app,
        lambda: not w.ops.busy and w.viewer._best_image() is not None and w.viewer._best_image()[0].size() != before,
    )
    assert w.viewer._best_image()[0].size().width() == before.height()


def test_all_details_lists_the_tags(window, app, monkeypatch):
    from homepix.ui.views import details_dialogs

    seen = {}
    monkeypatch.setattr(details_dialogs.AllDetailsDialog, "exec", lambda dlg: seen.setdefault("dlg", dlg))
    window.timeline.set_current(0)
    window.photo.all_details.trigger()
    wait_until(app, lambda: "dlg" in seen)
    tree = seen["dlg"].tree
    groups = {tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())}
    assert "ExifIFD" in groups
    seen["dlg"].find.setText("DateTimeOriginal")
    visible = [
        tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount()) if not tree.topLevelItem(i).isHidden()
    ]
    assert visible == ["ExifIFD"]


def test_place_parsing():
    from homepix.ui.views.details_dialogs import parse_place

    assert parse_place("31.778, 35.235") == (31.778, 35.235)
    assert parse_place("-33.86 151.21") == (-33.86, 151.21)
    assert parse_place("https://www.openstreetmap.org/?mlat=31.7&mlon=35.2#map=16/31.7/35.2") == (31.7, 35.2)
    assert parse_place("") == ()
    assert parse_place("north") is None and parse_place("95, 10") is None
