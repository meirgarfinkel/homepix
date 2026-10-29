"""The portable copy: a library folder carries what HomePix knows about it (faces, names, search,
duplicate choices, folders left out), so copying the folder to another computer brings it along."""

import os
import shutil
import sqlite3
import time

import numpy as np
from conftest import photo

from homepix.core import portable
from homepix.core.db import to_blob


def setup_library(pf, lib):
    """Five photos (two in 2024/, one in Old/), three of them of Ann, a pair said not to be duplicates,
    and Old/ left out of the library."""
    for name in ("a.jpg", "b.jpg", "2024/c.jpg", "2024/d.jpg", "Old/e.jpg"):
        photo(lib / name)
    pf.add_folder(lib)
    pf.index_folder(lib)
    db = pf.open_db()
    ids = {os.path.relpath(p, lib).replace(os.sep, "/"): i for i, p in db.execute("SELECT id, path FROM photos")}
    ann = db.execute("INSERT INTO people (name) VALUES ('Ann')").lastrowid
    rng = np.random.default_rng(1)
    for name in ("a.jpg", "2024/c.jpg", "Old/e.jpg"):
        db.execute("INSERT INTO faces (photo_id, x, y, w, h, score, emb, person_id) VALUES (?, .3, .3, .4, .4, .99, ?, ?)",
                   (ids[name], to_blob(rng.normal(size=128)), ann))
    db.execute("INSERT INTO apart (a, b) VALUES (?, ?)", sorted((ids["a.jpg"], ids["b.jpg"])))
    db.commit()
    db.close()
    pf.set_excluded(str(lib / "Old"), True)


def named(pf, root):
    """{photo path relative to root: the names of the people in it}."""
    db = pf.open_db()
    try:
        out = {}
        for path, name in db.execute("SELECT p.path, pe.name FROM faces f JOIN photos p ON p.id = f.photo_id "
                                     "LEFT JOIN people pe ON pe.id = f.person_id"):
            out.setdefault(os.path.relpath(path, root).replace(os.sep, "/"), []).append(name)
        return out
    finally:
        db.close()


def apart_pairs(pf, root):
    db = pf.open_db()
    try:
        return {tuple(sorted(os.path.relpath(p, root).replace(os.sep, "/") for p in (pa, pb)))
                for pa, pb in db.execute("SELECT x.path, y.path FROM apart JOIN photos x ON x.id = a "
                                         "JOIN photos y ON y.id = b")}
    finally:
        db.close()


def new_computer(monkeypatch, tmp_path, name="data2"):
    monkeypatch.setenv("HOMEPIX_HOME", str(tmp_path / name))


def test_the_copy_holds_the_folders_photos_with_relative_paths(pf, lib):
    setup_library(pf, lib)
    assert portable.write_copy(lib) == 5
    path = lib / ".homepix" / "library.db"
    assert path.is_file() and (lib / ".homepix" / portable.NOTE).is_file()
    con = sqlite3.connect(path)
    try:
        assert sorted(r[0] for r in con.execute("SELECT path FROM photos")) == [
            "2024/c.jpg", "2024/d.jpg", "Old/e.jpg", "a.jpg", "b.jpg"]
        assert con.execute("SELECT COUNT(*) FROM ops").fetchone()[0] == 0  # no undo history
        assert con.execute("PRAGMA journal_mode").fetchone()[0] == "delete"  # readable from a read-only drive
    finally:
        con.close()
    meta = portable.info(lib)
    assert (meta["photos"], meta["people"]) == (5, 1)
    assert pf.scan_folder(lib)[0].keys() == {str(lib / n) for n in ("a.jpg", "b.jpg", "2024/c.jpg", "2024/d.jpg",
                                                                    "Old/e.jpg")}  # the copy isn't a photo


def test_another_computer_picks_up_where_it_left_off(pf, lib, tmp_path, monkeypatch):
    setup_library(pf, lib)
    portable.write_copy(lib)
    before = named(pf, lib)

    # copied somewhere else; one file's modified time changed on the way, and one was renamed since
    there = tmp_path / "elsewhere" / "My Photos"
    shutil.copytree(lib, there)
    later = time.time() + 3600
    os.utime(there / "a.jpg", (later, later))
    (there / "2024" / "c.jpg").rename(there / "2024" / "renamed.jpg")
    new_computer(monkeypatch, tmp_path)
    pf.add_folder(there)

    assert portable.offered(there)["photos"] == 5
    got = portable.read_copy(there)
    assert got == {"photos": 5, "missing": 0, "people": 1}
    assert portable.offered(there) is None  # brought in: not offered again
    after = named(pf, there)
    assert after == {("2024/renamed.jpg" if k == "2024/c.jpg" else k): v for k, v in before.items()}
    assert apart_pairs(pf, there) == {("a.jpg", "b.jpg")}
    assert pf.excluded_folders() == [str(there / "Old")]

    info = pf.index_folder(there)  # nothing needs looking at again
    assert (info["todo"], info["removed"]) == (0, 0)
    assert named(pf, there) == after

    # this computer now owns the copy: writing it again replaces it, nothing set aside
    portable.write_copy(there)
    assert sorted(p.name for p in (there / ".homepix").iterdir()) == [portable.NOTE, "library.db"]


def test_a_photo_gone_from_the_folder_is_left_out(pf, lib, tmp_path, monkeypatch):
    setup_library(pf, lib)
    portable.write_copy(lib)
    (lib / "b.jpg").unlink()
    new_computer(monkeypatch, tmp_path)
    pf.add_folder(lib)
    assert portable.read_copy(lib) == {"photos": 4, "missing": 1, "people": 1}
    assert apart_pairs(pf, lib) == set()  # its pair went with it


def test_people_are_matched_by_name(pf, lib, tmp_path, monkeypatch):
    setup_library(pf, lib)
    portable.write_copy(lib)
    new_computer(monkeypatch, tmp_path)
    db = pf.open_db()
    db.execute("INSERT INTO people (name) VALUES ('ann')")  # already named on this computer
    db.commit()
    db.close()
    pf.add_folder(lib)
    portable.read_copy(lib)
    db = pf.open_db()
    try:
        assert db.execute("SELECT COUNT(*) FROM people").fetchone()[0] == 1
    finally:
        db.close()


def test_a_copy_is_never_replaced_by_an_empty_one_or_another_librarys(pf, lib, tmp_path, monkeypatch):
    setup_library(pf, lib)
    portable.write_copy(lib)
    first = portable.info(lib)
    new_computer(monkeypatch, tmp_path)
    pf.add_folder(lib)
    assert portable.write_copy(lib) is None  # none of its photos here yet: the copy stays as it was
    assert portable.info(lib) == first

    pf.index_folder(lib)  # started afresh instead of bringing the copy in
    portable.write_copy(lib)
    files = sorted(p.name for p in (lib / ".homepix").iterdir())
    assert files == [portable.NOTE, f"library-{first['written'][:10]}.db", "library.db"]  # the old one kept
    assert portable.info(lib)["library"] != first["library"]


def test_a_folder_that_isnt_there_is_skipped(pf, tmp_path):
    assert portable.write_copy(tmp_path / "unplugged") is None


def test_who_a_face_isnt_comes_along_too(pf, lib, tmp_path, monkeypatch):
    """A "that isn't them" mark names a person by their id, which is another one on another computer."""
    setup_library(pf, lib)
    db = pf.open_db()
    bob = db.execute("INSERT INTO people (name) VALUES ('Bob')").lastrowid
    b = db.execute("SELECT id FROM photos WHERE path = ?", (str(lib / "b.jpg"),)).fetchone()[0]
    db.execute("INSERT INTO faces (photo_id, x, y, w, h, score, emb, person_id) VALUES (?, .3, .3, .4, .4, .99, ?, ?)",
               (b, to_blob(np.ones(128)), bob))
    db.execute("UPDATE faces SET not_person = ? WHERE person_id != ?", (bob, bob))  # Ann's faces aren't Bob
    db.commit()
    db.close()
    portable.write_copy(lib)
    new_computer(monkeypatch, tmp_path)
    db = pf.open_db()
    db.executemany("INSERT INTO people (name) VALUES (?)", [("Zed",), ("Yan",), ("Xia",)])  # other ids here
    db.commit()
    db.close()
    pf.add_folder(lib)
    portable.read_copy(lib)
    db = pf.open_db()
    try:
        marks = {n for (n,) in db.execute("SELECT pe.name FROM faces f JOIN people pe ON pe.id = f.not_person")}
        assert marks == {"Bob"}
    finally:
        db.close()
