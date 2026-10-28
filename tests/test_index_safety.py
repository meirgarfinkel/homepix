"""M0: the index must never lose photos that are only out of reach (#9), duplicates must not leave ghost
rows (#15), and unreadable photos must be retried when that could help (#12)."""

import os
import shutil
import sqlite3

import pytest
from conftest import copy, photo, rows

from photofind.core import library

# --- #9: deleted vs. out of reach ------------------------------------------------------------------


def test_deleted_photo_is_removed(pf, lib):
    for i in range(10):
        photo(lib / f"{i}.jpg")
    pf.index_folder(lib)
    (lib / "3.jpg").unlink()
    info = pf.index_folder(lib)
    assert info["removed"] == 1 and info["missing"] == 0
    assert "3.jpg" not in rows(pf, lib) and len(rows(pf, lib)) == 9


def test_empty_root_like_an_unmounted_drive_keeps_everything(pf, lib):
    for i in range(5):
        photo(lib / f"{i}.jpg")
    pf.index_folder(lib)
    for f in lib.iterdir():
        f.unlink()
    info = pf.index_folder(lib)
    assert info["removed"] == 0 and info["missing"] == 5
    assert len(rows(pf, lib)) == 5


def test_unmounted_drive_inside_the_library_keeps_its_photos(pf, lib):
    for i in range(200):
        photo(lib / "home" / f"{i}.jpg")
    for year in range(2020, 2023):      # 60 photos, but no single year folder reaches the threshold
        for i in range(20):
            photo(lib / "drive" / str(year) / f"{i}.jpg")
    pf.index_folder(lib)
    for sub in (lib / "drive").iterdir():  # unmounted: only the empty mount point is left
        shutil.rmtree(sub)
    (lib / "home" / "0.jpg").unlink()      # ...while a real deletion elsewhere still counts
    info = pf.index_folder(lib)
    assert info["missing"] == 60 and info["removed"] == 1
    assert len(rows(pf, lib)) == 259


def test_small_folder_deleted_on_purpose_is_removed(pf, lib):
    for i in range(5):
        photo(lib / "trip" / f"{i}.jpg")
    for i in range(20):
        photo(lib / "home" / f"{i}.jpg")
    pf.index_folder(lib)
    shutil.rmtree(lib / "trip")
    info = pf.index_folder(lib)
    assert info["removed"] == 5 and info["missing"] == 0
    assert len(rows(pf, lib)) == 20


def test_folder_emptied_by_moving_its_photos_is_just_a_move(pf, lib):
    for i in range(60):
        photo(lib / "inbox" / f"{i}.jpg")
    pf.index_folder(lib)
    (lib / "2024").mkdir()
    for f in (lib / "inbox").iterdir():
        f.rename(lib / "2024" / f.name)
    info = pf.index_folder(lib)
    assert info["moved"] == 60 and info["missing"] == 0 and info["removed"] == 0


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root can read anything")
@pytest.mark.skipif(os.name == "nt", reason="Windows ignores these permissions")
def test_unreadable_folder_keeps_its_photos(pf, lib):
    for i in range(3):
        photo(lib / "locked" / f"{i}.jpg")
    photo(lib / "open.jpg")
    pf.index_folder(lib)
    os.chmod(lib / "locked", 0)
    try:
        info = pf.index_folder(lib)
    finally:
        os.chmod(lib / "locked", 0o755)
    assert info["unreachable"] == 3 and info["removed"] == 0
    assert len(rows(pf, lib)) == 4


def test_mass_disappearance_is_kept_until_forgotten(pf, lib):
    for i in range(60):
        photo(lib / "x" / f"{i}.jpg")
    for i in range(20):
        photo(lib / "y" / f"{i}.jpg")
    pf.index_folder(lib)
    shutil.rmtree(lib / "x")
    info = pf.index_folder(lib)
    assert info["missing"] == 60 and info["removed"] == 0
    assert len(rows(pf, lib)) == 80
    info = pf.index_folder(lib, forget_missing=True)
    assert info["removed"] == 60 and info["missing"] == 0
    assert len(rows(pf, lib)) == 20


def test_ignore_marker_drops_its_photos_even_many(pf, lib):
    for i in range(60):
        photo(lib / "project" / "build" / f"{i}.png", "PNG")
    photo(lib / "keep.jpg")
    pf.index_folder(lib)
    (lib / "project" / pf.IGNORE_MARKER).touch()
    info = pf.index_folder(lib)
    assert info["removed"] == 60 and info["missing"] == 0
    assert list(rows(pf, lib)) == ["keep.jpg"]


# --- #15: moves and duplicates ---------------------------------------------------------------------


def test_moved_photo_keeps_its_row(pf, lib):
    photo(lib / "a" / "p.jpg")
    pf.index_folder(lib)
    before = rows(pf, lib)["a/p.jpg"]["id"]
    (lib / "b").mkdir()
    (lib / "a" / "p.jpg").rename(lib / "b" / "p.jpg")
    info = pf.index_folder(lib)
    assert info["moved"] == 1 and info["removed"] == 0
    assert rows(pf, lib)["b/p.jpg"]["id"] == before


def test_moved_exact_copies_each_keep_their_row(pf, lib):
    copy(photo(lib / "a" / "1.jpg"), lib / "a" / "2.jpg")
    pf.index_folder(lib)
    ids = {r["id"] for r in rows(pf, lib).values()}
    shutil.move(lib / "a", lib / "b")
    info = pf.index_folder(lib)
    assert info["moved"] == 2 and info["removed"] == 0
    assert {r["id"] for r in rows(pf, lib).values()} == ids


def test_vanished_copy_leaves_no_ghost_row(pf, lib):
    original = photo(lib / "a" / "1.jpg")
    copy(original, lib / "b" / "1.jpg")
    pf.index_folder(lib)
    (lib / "c").mkdir()
    (lib / "a" / "1.jpg").rename(lib / "c" / "1.jpg")  # one copy moved...
    (lib / "b" / "1.jpg").unlink()                     # ...the other deleted
    pf.index_folder(lib)
    assert sorted(rows(pf, lib)) == ["c/1.jpg"]


# --- #12: unreadable photos ------------------------------------------------------------------------


def test_unreadable_photo_is_retried_only_when_reading_improves(pf, lib, monkeypatch):
    (lib / "broken.jpg").write_bytes(b"this is not a photo")
    info = pf.index_folder(lib)
    assert info["errors"] == 1 and rows(pf, lib)["broken.jpg"]["error"]
    assert pf.index_folder(lib)["todo"] == 0      # nothing changed: not tried again
    monkeypatch.setattr(library, "READER", "99")
    assert pf.index_folder(lib)["todo"] == 1      # better reader: tried again
    assert rows(pf, lib)["broken.jpg"]["error_reader"] == "99"


def test_truncated_jpeg_is_indexed(pf, lib):
    p = photo(lib / "cut.jpg")
    p.write_bytes(p.read_bytes()[:-200])
    info = pf.index_folder(lib)
    r = rows(pf, lib)["cut.jpg"]
    assert info["errors"] == 0 and r["error"] is None and r["clip"] is not None


def test_heic_is_indexed(pf, lib):
    if not pf.HEIF:
        pytest.skip("pillow-heif not installed")
    photo(lib / "iphone.heic", "HEIF")
    pf.index_folder(lib)
    assert rows(pf, lib)["iphone.heic"]["clip"] is not None


def test_model_hiccup_is_not_blamed_on_the_photo(pf, lib, monkeypatch):
    photo(lib / "p.jpg")
    working = library.ClipImage

    class Hiccup(working):
        def embed(self, img):
            raise RuntimeError("model hiccup")

    monkeypatch.setattr(library, "ClipImage", Hiccup)
    info = pf.index_folder(lib)
    assert info["errors"] == 1 and "p.jpg" not in rows(pf, lib)  # nothing recorded against it
    monkeypatch.setattr(library, "ClipImage", working)
    pf.index_folder(lib)
    assert rows(pf, lib)["p.jpg"]["clip"] is not None


def test_database_from_before_m0_gets_the_new_column(pf, tmp_path):
    path = tmp_path / "data" / "photofind.db"
    path.parent.mkdir(parents=True)
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE photos (id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL, size INTEGER, "
                "mtime REAL, qhash TEXT, taken TEXT, clip BLOB, faces_done INTEGER DEFAULT 0, error TEXT)")
    old.commit()
    old.close()
    db = pf.open_db()
    assert "error_reader" in {r["name"] for r in db.execute("PRAGMA table_info(photos)")}
