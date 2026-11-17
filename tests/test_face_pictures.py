"""Face pictures: made while indexing (the photo is decoded then anyway), cut from the photo's
thumbnail when the face is big enough in it, and made for older faces by a pass after indexing. On a
hard disk, reading each whole photo again to show its face made the faces pages crawl."""

import numpy as np
import pytest
from conftest import photo
from PIL import Image

BIG = (0.4, 0.3, 0.25, 0.35)  # a face filling a quarter of the photo: sharp enough in its thumbnail
TINY = (0.05, 0.05, 0.03, 0.04)  # a face at the back of a group shot: needs the photo


class TwoFaces:
    def find(self, img, path=None):
        if "group" not in str(path):
            return []
        v = np.ones(512, dtype=np.float32) / np.sqrt(512)
        return [(*box, 0.99, v, 100.0) for box in (BIG, TINY)]


@pytest.fixture
def indexed(pf, lib, monkeypatch):
    from homepix.core import library

    monkeypatch.setattr(library, "Faces", TwoFaces)
    p = lib / "group.jpg"
    rng = np.random.default_rng(7)
    Image.fromarray(rng.integers(0, 255, (800, 1200, 3), dtype=np.uint8)).save(p)
    photo(lib / "other.jpg")
    pf.add_folder(lib)
    pf.index_folder(lib)
    db = pf.open_db()
    from homepix.core.people import face_rows

    rows = sorted(face_rows(db, "1"), key=lambda r: -r["w"])  # big first
    db.close()
    assert len(rows) == 2
    return rows


def reads(monkeypatch):
    """The photos read whole from now on."""
    from homepix.core import thumbs

    seen, real = [], thumbs.load_photo
    monkeypatch.setattr(thumbs, "load_photo", lambda path, *a: seen.append(path) or real(path, *a))
    return seen


def test_indexing_makes_the_face_pictures(indexed, monkeypatch):
    from homepix.core.thumbs import face_thumb, face_thumb_file

    assert all(face_thumb_file(r).exists() for r in indexed)
    seen = reads(monkeypatch)
    for r in indexed:
        face_thumb(r)
    assert seen == []  # shown without reading anything but the pictures


def test_a_big_face_comes_from_the_thumbnail_and_a_small_one_from_the_photo(indexed, monkeypatch):
    from homepix.core.thumbs import face_thumb, face_thumb_file

    big, tiny = indexed
    for r in indexed:
        face_thumb_file(r).unlink()
    seen = reads(monkeypatch)
    out = face_thumb(big)
    assert seen == [] and Image.open(out).size == (160, 160)
    face_thumb(tiny)
    assert len(seen) == 1


def test_the_pass_after_indexing_makes_the_missing_ones_reading_each_photo_once(pf, lib, indexed, monkeypatch):
    from homepix.core.thumbs import face_thumb_file
    from homepix.ui.indexing import describe

    for r in indexed:
        face_thumb_file(r).unlink()
    seen, said = reads(monkeypatch), []
    pf.index_folder(lib, on_progress=lambda i: said.append(describe(dict(i, running=True))))
    assert all(face_thumb_file(r).exists() for r in indexed)
    assert len(seen) == 1  # only for the small face
    assert "Making face pictures: 2 of 2" in said
    seen.clear()
    pf.index_folder(lib)  # nothing left to make
    assert seen == []


def test_a_face_from_the_thumbnail_is_the_same_square_as_from_the_photo(pf, lib):
    """The thumbnail is the photo turned upright and shrunk, so the face box (a share of its width
    and height) lands on the same place in it."""
    from homepix.core.media import load_photo
    from homepix.core.thumbs import _thumbnail, face_thumb, photo_thumb

    x, y = np.meshgrid(np.linspace(0, 255, 1200), np.linspace(0, 255, 800))
    pixels = np.stack([x, y, (x + y) / 2], axis=-1).astype(np.uint8)  # (a smooth picture: no aliasing)
    p = lib / "smooth.jpg"
    Image.fromarray(pixels).save(p)
    photo_thumb("smooth", str(p))
    row = dict(zip(("x", "y", "w", "h"), BIG, strict=True), qhash="smooth", path=str(p))
    a = np.asarray(Image.open(face_thumb(row, 160, _thumbnail("smooth"))), dtype=float)
    row["qhash"] = "smooth2"  # (another file)
    b = np.asarray(Image.open(face_thumb(row, 160, load_photo(p, 1600))), dtype=float)
    assert np.abs(a - b).mean() < 3
