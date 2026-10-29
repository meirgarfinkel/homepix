"""The database: a new one gets the current schema, 1.0's is brought up to date keeping everything,
and one too new or too old is left alone."""

import os
import shutil
import sqlite3

import pytest
from conftest import photo, rows

from homepix.core import db as dbmod

# The schema as 1.0 left it (database version 11), with a little in it
V11 = """
CREATE TABLE photos (id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL, size INTEGER, mtime REAL, qhash TEXT,
    taken TEXT, clip BLOB, faces_done INTEGER DEFAULT 0, error TEXT, error_reader TEXT,
    kind TEXT NOT NULL DEFAULT 'photo', taken_source TEXT, sha256 TEXT, duration REAL,
    rating INTEGER NOT NULL DEFAULT 0, keywords TEXT, description TEXT, details_done INTEGER NOT NULL DEFAULT 0,
    moment TEXT, phash INTEGER, width INTEGER, height INTEGER);
CREATE INDEX photos_qhash ON photos(qhash);
CREATE TABLE people (id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL COLLATE NOCASE);
CREATE TABLE faces (id INTEGER PRIMARY KEY, photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    x REAL, y REAL, w REAL, h REAL, score REAL, emb BLOB NOT NULL, cluster INTEGER,
    person_id INTEGER REFERENCES people(id) ON DELETE SET NULL, locked INTEGER DEFAULT 0,
    hidden INTEGER NOT NULL DEFAULT 0, px REAL, not_person INTEGER REFERENCES people(id) ON DELETE SET NULL);
CREATE INDEX faces_photo ON faces(photo_id);
CREATE INDEX faces_person ON faces(person_id);
CREATE INDEX photos_sha256 ON photos(sha256);
CREATE TABLE sidecars (id INTEGER PRIMARY KEY, photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    path TEXT UNIQUE NOT NULL, kind TEXT NOT NULL);
CREATE INDEX sidecars_photo ON sidecars(photo_id);
CREATE TABLE ops (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, summary TEXT, started TEXT NOT NULL, finished TEXT,
    state TEXT NOT NULL DEFAULT 'running', undoes INTEGER REFERENCES ops(id));
CREATE TABLE op_steps (id INTEGER PRIMARY KEY, op_id INTEGER NOT NULL REFERENCES ops(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL, action TEXT NOT NULL, photo_id INTEGER, src TEXT, dst TEXT, before TEXT, after TEXT,
    done INTEGER NOT NULL DEFAULT 0, UNIQUE (op_id, seq));
CREATE INDEX photos_kind ON photos(kind);
CREATE TABLE apart (a INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    b INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE, PRIMARY KEY (a, b));
INSERT INTO photos (id, path, size, taken, clip, rating, keywords) VALUES
    (1, '/p/a.jpg', 10, '2020-01-01 00:00:00', x'00', 3, '["sea"]'), (2, '/p/b.jpg', 20, NULL, NULL, 0, NULL);
INSERT INTO people (id, name) VALUES (1, 'Mom'), (2, 'Dad');
INSERT INTO faces (id, photo_id, emb, cluster, person_id, locked, not_person) VALUES (1, 1, x'00', 4, 1, 1, 2);
INSERT INTO ops (id, kind, summary, started, state) VALUES (1, 'move', 'Moved a.jpg', '2026-10-01 10:00:00', 'done');
INSERT INTO op_steps (op_id, seq, action, photo_id, src, dst, done) VALUES (1, 0, 'move', 1, '/q/a.jpg', '/p/a.jpg', 2);
INSERT INTO apart (a, b) VALUES (1, 2);
PRAGMA user_version = 11;
"""


def make_db(pf, script=V11):
    path = pf.data_dir() / "homepix.db"
    with sqlite3.connect(path) as db:
        db.executescript(script)
    return path


def columns(db, table):
    return set(dbmod.columns(db, table))


def tables(db):
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def backups(pf):
    return sorted((pf.data_dir() / "backups").glob("*.db")) if (pf.data_dir() / "backups").exists() else []


@pytest.fixture(autouse=True)
def fresh_open(monkeypatch):
    monkeypatch.setattr(dbmod, "_ready_dbs", set())  # every test opens its database for the first time


def test_new_database_gets_the_schema_and_no_backup(pf):
    db = pf.open_db()
    assert db.execute("PRAGMA user_version").fetchone()[0] == dbmod.VERSION
    assert tables(db) == set(dbmod.TABLES)
    assert {"error_reader", "kind", "taken_source", "sha256", "phash"} <= columns(db, "photos")
    assert backups(pf) == []


def test_1_0_database_drops_what_was_never_used_and_keeps_everything_else(pf):
    make_db(pf)
    db = pf.open_db()
    assert db.execute("PRAGMA user_version").fetchone()[0] == dbmod.VERSION
    assert tables(db) == set(dbmod.TABLES)  # (no sidecars)
    assert "rating" not in columns(db, "photos") and "cluster" not in columns(db, "faces")
    assert "photo_id" not in columns(db, "op_steps")
    a = dict(db.execute("SELECT * FROM photos WHERE id=1").fetchone())
    assert (a["path"], a["size"], a["keywords"], a["kind"]) == ("/p/a.jpg", 10, '["sea"]', "photo")
    face = dict(db.execute("SELECT * FROM faces").fetchone())
    assert (face["person_id"], face["locked"], face["not_person"]) == (1, 1, 2)
    assert db.execute("SELECT src, dst, done FROM op_steps").fetchone()[:] == ("/q/a.jpg", "/p/a.jpg", 2)
    assert db.execute("SELECT a, b FROM apart").fetchone()[:] == (1, 2)
    indexes = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL")}
    assert indexes == {"photos_qhash", "photos_kind", "photos_sha256", "faces_photo", "faces_person"}
    # its foreign keys still hold: deleting a photo takes its faces and duplicate choices with it
    db.execute("DELETE FROM photos WHERE id=1")
    assert db.execute("SELECT COUNT(*) FROM faces").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM apart").fetchone()[0] == 0
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1

    [saved] = backups(pf)  # the untouched original, in case the upgrade ever needs undoing
    with sqlite3.connect(saved) as old:
        assert old.execute("PRAGMA user_version").fetchone()[0] == 11
        assert "sidecars" in tables(old)


def test_upgrade_happens_once(pf):
    make_db(pf)
    pf.open_db().close()
    dbmod._ready_dbs.clear()  # as if the app started again
    pf.open_db().close()
    assert len(backups(pf)) == 1


def test_failed_upgrade_leaves_database_untouched(pf, monkeypatch):
    path = make_db(pf)

    def broken(db):
        db.execute("DROP TABLE sidecars")
        raise RuntimeError("boom")

    monkeypatch.setitem(dbmod.UPGRADES, 11, broken)
    with pytest.raises(RuntimeError, match="boom"):
        pf.open_db()
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 11
        assert "sidecars" in tables(db) and "rating" in columns(db, "photos")


def test_newer_database_is_refused_and_not_touched(pf):
    path = make_db(pf, f"CREATE TABLE future (x); PRAGMA user_version = {dbmod.VERSION + 1};")
    with pytest.raises(pf.DatabaseVersionError, match="newer HomePix"):
        pf.open_db()
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == dbmod.VERSION + 1
        assert tables(db) == {"future"}
    assert backups(pf) == []


def test_database_from_before_1_0_is_refused_and_not_touched(pf):
    path = make_db(pf, "CREATE TABLE photos (id INTEGER PRIMARY KEY, path TEXT); PRAGMA user_version = 5;")
    with pytest.raises(pf.DatabaseVersionError, match="before 1.0"):
        pf.open_db()
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 5
        assert tables(db) == {"photos"}


def test_index_records_where_the_date_came_from(pf, lib):
    photo(lib / "plain.jpg")  # no EXIF: the date is a guess from the file
    pf.index_folder(lib, want_faces=False)
    assert rows(pf, lib)["plain.jpg"]["taken_source"] == "file-modified"


@pytest.mark.skipif(not os.environ.get("HOMEPIX_REAL_DB"), reason="set HOMEPIX_REAL_DB to a copy of a real database")
def test_real_database_upgrades_without_loss(pf):
    path = pf.data_dir() / "homepix.db"
    shutil.copy(os.environ["HOMEPIX_REAL_DB"], path)
    counts = "SELECT (SELECT COUNT(*) FROM photos), (SELECT COUNT(*) FROM faces), " \
             "(SELECT COUNT(*) FROM faces WHERE person_id IS NOT NULL), (SELECT COUNT(*) FROM people), " \
             "(SELECT COUNT(*) FROM ops), (SELECT COUNT(*) FROM op_steps), (SELECT COUNT(*) FROM apart)"
    with sqlite3.connect(path) as db:
        before = db.execute(counts).fetchone()
    db = pf.open_db()
    assert tuple(db.execute(counts).fetchone()) == tuple(before)
    assert db.execute("PRAGMA user_version").fetchone()[0] == dbmod.VERSION
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    print("photos, faces, named faces, people, operations, steps, apart:", tuple(before))
