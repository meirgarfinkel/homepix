"""M3c: videos (dates, lengths, frames, Live Photo motion), the people page, and the app's own startup."""

import datetime as dt
import os
import struct

import pytest
from conftest import et_command, photo, rows
from PIL import Image

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
cv2 = pytest.importorskip("cv2")


# --- reading videos --------------------------------------------------------------------------------


def box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def tiny_mp4(created: int, scale=600, length=600 * 42, apple_date=None) -> bytes:
    """Just enough of an MP4 for video_meta: a movie header, and Apple's creation date if given."""
    mvhd = box(b"mvhd", b"\0\0\0\0" + struct.pack(">IIII", created, created, scale, length) + b"\0" * 80)
    parts = [mvhd]
    if apple_date:
        key = b"mdta" + b"com.apple.quicktime.creationdate"
        keys = box(b"keys", b"\0\0\0\0" + struct.pack(">I", 1) + struct.pack(">I", 8 + len(key) - 4) + key)
        data = box(b"data", struct.pack(">II", 1, 0) + apple_date.encode())
        ilst = box(b"ilst", struct.pack(">I", 8 + len(data)) + struct.pack(">I", 1) + data)
        parts.append(box(b"meta", box(b"hdlr", b"\0" * 24) + keys + ilst))
    return box(b"ftyp", b"qt  \0\0\0\0") + box(b"moov", b"".join(parts))


def since_1904(d: dt.datetime) -> int:
    return int((d - dt.datetime(1904, 1, 1, tzinfo=dt.UTC)).total_seconds())


def test_video_date_and_length_from_the_movie_header(tmp_path):
    from homepix.core.video import video_meta

    utc = dt.datetime(2024, 7, 1, 9, 30, 0, tzinfo=dt.UTC)
    f = tmp_path / "a.mp4"
    f.write_bytes(tiny_mp4(since_1904(utc)))
    meta = video_meta(f)
    assert meta["duration"] == 42
    assert meta["taken"] == utc.astimezone().replace(tzinfo=None)  # shown in local time


def test_apples_local_date_wins(tmp_path):
    from homepix.core.video import video_meta

    f = tmp_path / "a.mov"
    f.write_bytes(
        tiny_mp4(since_1904(dt.datetime(2024, 7, 1, 6, 30, tzinfo=dt.UTC)), apple_date="2024-07-01T09:30:05+0300")
    )
    assert video_meta(f)["taken"] == dt.datetime(2024, 7, 1, 9, 30, 5)


def test_a_broken_or_empty_video_tells_nothing(tmp_path):
    from homepix.core.video import video_meta

    (tmp_path / "junk.mp4").write_bytes(b"\0\0\0\x10ftypisom" + os.urandom(200))
    (tmp_path / "unset.mp4").write_bytes(tiny_mp4(0))  # some cameras leave the date at 0
    assert video_meta(tmp_path / "junk.mp4") == {"clock": None, "taken": None, "duration": None}
    assert video_meta(tmp_path / "unset.mp4")["taken"] is None


def test_dates_in_file_names():
    from homepix.core.dates import in_name, is_placeholder

    assert in_name("2023_05_28_23_07_00_82.jpg") == dt.datetime(2023, 5, 28, 23, 7)
    assert in_name("VID_20230528_230700.mp4") == dt.datetime(2023, 5, 28, 23, 7)
    assert in_name("Screenshot 2026-01-18 at 14-54-55.png") == dt.datetime(2026, 1, 18, 14, 54, 55)
    assert in_name("IMG_2023-05-28.jpg") == dt.datetime(2023, 5, 28)
    assert is_placeholder(in_name("2023_01_01_00_00_00.jpg"))  # a placeholder: only the year's likely right
    assert in_name("IMG_4411.MOV") is None


def make_video(path, seconds: float, fps=10):
    path.parent.mkdir(parents=True, exist_ok=True)
    out = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (64, 48))
    for i in range(int(seconds * fps)):
        frame = cv2.UMat(48, 64, cv2.CV_8UC3, (i * 7 % 255, 120, 30)).get()
        out.write(frame)
    out.release()
    return path


def photo_taken(path, when: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    exif = Image.Exif()
    exif.get_ifd(0x8769)[36867] = when
    Image.new("RGB", (64, 48), "blue").save(path, exif=exif)
    return path


# --- indexing videos -------------------------------------------------------------------------------


def test_videos_are_indexed_without_ai_and_get_a_thumbnail(pf, lib, monkeypatch):
    import homepix.core.library as library

    def no_ai():
        raise AssertionError("videos don't need the AI models")

    monkeypatch.setattr(library, "ClipImage", no_ai)
    make_video(lib / "2024_05_01_12_00_00.mp4", 6)
    info = pf.index_folder(lib)
    assert info["done"] == 1 and info["errors"] == 0
    r = rows(pf, lib)["2024_05_01_12_00_00.mp4"]
    assert r["kind"] == "video" and r["clip"] is None and r["faces_done"] == 1
    assert r["duration"] == pytest.approx(6, abs=0.2)
    assert r["taken"] == "2024-05-01 12:00:00" and r["taken_source"] == "filename"
    from homepix.core.thumbs import thumb_file

    assert thumb_file(r["qhash"]).exists()
    again = pf.index_folder(lib)  # nothing to redo
    assert again["todo"] == 0
    assert pf.SearchIndex().vectors()[0] == []  # no search for videos


def recorded(path, local: str):
    """Give a video the clock a camera would: when it was recorded, in UTC."""
    import subprocess

    from homepix.core.exiftool import available

    if not available():
        pytest.skip("needs ExifTool")
    utc = dt.datetime.fromisoformat(local).astimezone(dt.UTC)
    subprocess.run(
        [*et_command(), "-q", "-overwrite_original", f"-QuickTime:CreateDate={utc:%Y:%m:%d %H:%M:%S}", str(path)],
        check=True,
    )
    return path


def test_a_short_clip_taken_with_a_photo_is_its_motion(pf, lib):
    from homepix.core.timeline import folder_counts, load_items

    photo_taken(lib / "live.jpg", "2024:05:01 12:00:01")
    recorded(make_video(lib / "2024_05_01_12_00_00.mp4", 2.5), "2024-05-01 12:00:00")  # the Live Photo's motion
    recorded(make_video(lib / "2024_05_01_12_00_02.mp4", 8), "2024-05-01 12:00:02")  # a real video, taken then
    recorded(make_video(lib / "2024_06_01_09_00_00.mp4", 2), "2024-06-01 09:00:00")  # short, but no photo with it
    pf.index_folder(lib, want_faces=False)
    kinds = {name: r["kind"] for name, r in rows(pf, lib).items()}
    assert kinds == {
        "live.jpg": "photo",
        "2024_05_01_12_00_00.mp4": "motion",
        "2024_05_01_12_00_02.mp4": "video",
        "2024_06_01_09_00_00.mp4": "video",
    }
    db = pf.open_db()
    shown = sorted(os.path.basename(it.path) for it in load_items(db))
    assert shown == ["2024_05_01_12_00_02.mp4", "2024_06_01_09_00_00.mp4", "live.jpg"]
    assert sum(folder_counts(db).values()) == 3
    db.close()
    (lib / "live.jpg").unlink()  # the photo is gone: its clip is a video of its own again
    pf.index_folder(lib, want_faces=False)
    assert rows(pf, lib)["2024_05_01_12_00_00.mp4"]["kind"] == "video"


def test_video_info(pf, lib):
    from homepix.core.info import photo_info

    make_video(lib / "clip.mp4", 3)
    pf.index_folder(lib, want_faces=False)
    db = pf.open_db()
    info = photo_info(db, db.execute("SELECT id FROM photos").fetchone()[0])
    db.close()
    assert info["kind"] == "video" and info["dimensions"] == (64, 48)
    assert info["duration"] == pytest.approx(3, abs=0.2) and "error" not in info


# --- people ----------------------------------------------------------------------------------------


def add_person(pf, name, photo_ids, w=0.2):
    db = pf.open_db()
    db.execute("INSERT INTO people (name) VALUES (?)", (name,))
    pid = db.execute("SELECT id FROM people WHERE name=?", (name,)).fetchone()[0]
    for i, photo_id in enumerate(photo_ids):
        db.execute(
            "INSERT INTO faces (photo_id, x, y, w, h, score, emb, person_id) VALUES (?,?,?,?,?,?,?,?)",
            (photo_id, 0.3, 0.3, w, w, 0.99, pf.to_blob([1.0] + [0.01 * i] * 127), pid),
        )
    db.commit()
    db.close()
    return pid


def photo_ids(pf):
    db = pf.open_db()
    try:
        return [r[0] for r in db.execute("SELECT id FROM photos ORDER BY id")]
    finally:
        db.close()


def test_named_people_most_photographed_first(pf, lib):
    for i in range(5):
        photo(lib / f"{i}.jpg")
    pf.index_folder(lib, want_faces=False)
    ids = photo_ids(pf)
    add_person(pf, "Ada", ids[:2])
    add_person(pf, "Bea", ids[:4])
    db = pf.open_db()
    people = pf.named_people(db)
    db.close()
    assert [(p["name"], p["photos"]) for p in people] == [("Bea", 4), ("Ada", 2)]
    assert all(p["face"] is not None for p in people)
    pf.face_thumb(people[0]["face"], 160)  # the portrait crops


def test_face_crops_stay_inside_the_photo(pf, lib):
    p = lib / "edge.jpg"
    Image.new("RGB", (200, 100), (200, 30, 30)).save(p)
    row = {"x": 0.9, "y": 0.0, "w": 0.1, "h": 0.2, "path": str(p), "qhash": "edge"}
    crop = Image.open(pf.face_thumb(row, 64)).convert("RGB")
    e = crop.width - 1
    corners = [crop.getpixel(xy) for xy in ((0, 0), (e, 0), (0, e), (e, e))]
    assert all(c[0] > 150 for c in corners)  # all photo, no black padding


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
    from homepix.ui.main_window import MainWindow

    w = MainWindow(index=False)
    w.resize(900, 600)
    w.show()
    yield w
    w.close()


def test_people_page_and_a_persons_photos(window, pf, lib, app):
    from homepix.ui.views.sidebar import PEOPLE, person_place

    for i in range(4):
        photo(lib / f"{i}.jpg")
    pf.add_folder(lib)
    pf.index_folder(lib, want_faces=False)
    pid = add_person(pf, "Ada", photo_ids(pf)[:3])
    window.reload()
    wait_until(app, lambda: len(window.timeline.items) == 4)
    window.show_place(PEOPLE)
    wait_until(app, lambda: window.stack.currentWidget() is window.people and window.people.count() == 1)
    assert window.people.item(0).text() == "Ada\n3 photos"
    window.people.itemClicked.emit(window.people.item(0))
    wait_until(app, lambda: window.place == person_place(pid) and len(window.timeline.items) == 3)
    assert window.stack.currentWidget() is window.timeline
    assert window.count_label.text().strip() == "3 photos of Ada"
    assert window.sidebar.current_path() == PEOPLE  # (people aren't listed one by one)


def test_videos_in_the_grid_and_the_viewer(window, pf, lib, app, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtTest import QTest

    photo(lib / "a.jpg")
    make_video(lib / "clip.mp4", 6)
    pf.add_folder(lib)
    pf.index_folder(lib, want_faces=False)
    window.reload()
    wait_until(app, lambda: len(window.timeline.items) == 2)
    assert window.count_label.text().strip() == "1 photo and 1 video"
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", staticmethod(lambda url: opened.append(url.toLocalFile())))
    vi = next(i for i, it in enumerate(window.timeline.items) if it.kind == "video")
    window.open_viewer(vi)
    wait_until(app, lambda: window.viewer._image() is not None)  # its frame shows
    assert opened == []  # stepping onto a video doesn't start it
    QTest.keyClick(window.viewer, Qt.Key.Key_Return)
    assert [os.path.normpath(p) for p in opened] == [str(lib / "clip.mp4")]
    window.viewer.go(1 - vi)  # to the photo: Enter there doesn't play anything
    QTest.keyClick(window.viewer, Qt.Key.Key_Return)
    assert len(opened) == 1


def test_counting_photos_and_videos():
    from homepix.ui.fileops import counted

    assert counted(1, 0) == "1 photo"
    assert counted(1234, 2) == "1,234 photos and 2 videos"
    assert counted(0, 1) == "1 video"
    assert counted(0, 0) == "0 photos"
