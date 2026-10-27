"""M1: databases upgrade safely from any earlier version, and one from a newer version is left alone."""

import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import photo, rows

from photofind.core import db as dbmod

# The schema as it was before versioning; pre-M0 databases lack error_reader.
OLD_SCHEMA = """
CREATE TABLE photos (id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL, size INTEGER, mtime REAL, qhash TEXT,
    taken TEXT, clip BLOB, faces_done INTEGER DEFAULT 0, error TEXT {extra});
CREATE INDEX photos_qhash ON photos(qhash);
CREATE TABLE people (id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL COLLATE NOCASE);
CREATE TABLE faces (id INTEGER PRIMARY KEY, photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    x REAL, y REAL, w REAL, h REAL, score REAL, emb BLOB NOT NULL, cluster INTEGER,
    person_id INTEGER REFERENCES people(id) ON DELETE SET NULL, locked INTEGER DEFAULT 0);
CREATE INDEX faces_photo ON faces(photo_id);
CREATE INDEX faces_person ON faces(person_id);
INSERT INTO photos (id, path, size, taken, clip) VALUES (1, '/p/a.jpg', 10, '2020-01-01 00:00:00', x'00');
INSERT INTO people (id, name) VALUES (1, 'Mom');
INSERT INTO faces (photo_id, emb, person_id) VALUES (1, x'00', 1);
"""


def make_old_db(pf, with_error_reader):
    path = pf.data_dir() / "photofind.db"
    with sqlite3.connect(path) as db:
        db.executescript(OLD_SCHEMA.format(extra=", error_reader TEXT" if with_error_reader else ""))
    return path


def columns(db, table):
    return {r[1] for r in db.execute(f"PRAGMA table_info({table})")}


def tables(db):
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def backups(pf):
    return sorted((pf.data_dir() / "backups").glob("*.db")) if (pf.data_dir() / "backups").exists() else []


@pytest.fixture(autouse=True)
def fresh_open(monkeypatch):
    monkeypatch.setattr(dbmod, "_ready_dbs", set())  # every test opens its database for the first time


def test_new_database_gets_latest_schema_and_no_backup(pf):
    db = pf.open_db()
    assert db.execute("PRAGMA user_version").fetchone()[0] == dbmod.LATEST
    assert {"photos", "people", "faces", "sidecars", "ops", "op_steps"} <= tables(db)
    assert {"error_reader", "kind", "taken_source", "sha256"} <= columns(db, "photos")
    assert backups(pf) == []


@pytest.mark.parametrize("with_error_reader", [False, True], ids=["pre-M0", "M0"])
def test_old_database_upgrades_keeping_everything(pf, with_error_reader):
    make_old_db(pf, with_error_reader)
    db = pf.open_db()
    assert db.execute("PRAGMA user_version").fetchone()[0] == dbmod.LATEST
    assert {"error_reader", "kind", "taken_source", "sha256"} <= columns(db, "photos")
    photo_row = dict(db.execute("SELECT * FROM photos").fetchone())
    assert photo_row["path"] == "/p/a.jpg" and photo_row["kind"] == "photo" and photo_row["taken_source"] is None
    assert db.execute("SELECT name FROM people JOIN faces ON faces.person_id = people.id").fetchone()[0] == "Mom"

    [saved] = backups(pf)  # the untouched original, in case the upgrade ever needs undoing
    with sqlite3.connect(saved) as old:
        assert old.execute("PRAGMA user_version").fetchone()[0] == 0
        assert "sidecars" not in tables(old)
        assert old.execute("SELECT COUNT(*) FROM faces").fetchone()[0] == 1


def test_upgrade_happens_once(pf):
    make_old_db(pf, True)
    pf.open_db().close()
    dbmod._ready_dbs.clear()  # as if the app started again
    pf.open_db().close()
    assert len(backups(pf)) == 1


def test_failed_upgrade_leaves_database_untouched(pf, monkeypatch):
    path = make_old_db(pf, True)

    def broken(db):
        db.execute("CREATE TABLE half_done (x)")
        raise RuntimeError("boom")

    monkeypatch.setattr(dbmod, "MIGRATIONS", [dbmod._m1_base, broken])
    monkeypatch.setattr(dbmod, "LATEST", 2)
    with pytest.raises(RuntimeError, match="boom"):
        pf.open_db()
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 0
        assert "half_done" not in tables(db)


def test_newer_database_is_refused_and_not_touched(pf):
    path = pf.data_dir() / "photofind.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE future (x)")
        db.execute(f"PRAGMA user_version = {dbmod.LATEST + 1}")
    with pytest.raises(pf.NewerDatabaseError, match="newer Photo Finder"):
        pf.open_db()
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == dbmod.LATEST + 1
        assert tables(db) == {"future"}
    assert backups(pf) == []


def test_index_records_where_the_date_came_from(pf, lib):
    photo(lib / "plain.jpg")  # no EXIF: the date is a guess from the file
    pf.index_folder(lib, want_faces=False)
    assert rows(pf, lib)["plain.jpg"]["taken_source"] == "file-modified"


def test_cli_runs_as_a_module(tmp_path):
    env = {**os.environ, "PHOTOFIND_HOME": str(tmp_path / "home")}
    out = subprocess.run([sys.executable, "-m", "photofind", "status"], capture_output=True, text=True, env=env,
                         cwd=Path(__file__).resolve().parents[1], timeout=60)
    assert out.returncode == 0, out.stderr
    assert "Photos:" in out.stdout


@pytest.mark.skipif(not os.environ.get("PHOTOFIND_REAL_DB"), reason="set PHOTOFIND_REAL_DB to a copy of a real database")
def test_real_database_upgrades_without_loss(pf):
    path = pf.data_dir() / "photofind.db"
    shutil.copy(os.environ["PHOTOFIND_REAL_DB"], path)
    counts = "SELECT (SELECT COUNT(*) FROM photos), (SELECT COUNT(*) FROM faces), " \
             "(SELECT COUNT(*) FROM faces WHERE person_id IS NOT NULL), (SELECT COUNT(*) FROM people)"
    with sqlite3.connect(path) as db:
        before = db.execute(counts).fetchone()
    db = pf.open_db()
    assert tuple(db.execute(counts).fetchone()) == tuple(before)
    assert db.execute("PRAGMA user_version").fetchone()[0] == dbmod.LATEST
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    print("photos, faces, named faces, people:", tuple(before))


def test_ratings_from_0_5_are_cleared(pf, monkeypatch):
    """0.5.0 had star ratings (database version 5); they were dropped in the next one."""
    every, latest = dbmod.MIGRATIONS, dbmod.LATEST
    monkeypatch.setattr(dbmod, "MIGRATIONS", every[:5])
    monkeypatch.setattr(dbmod, "LATEST", 5)
    db = pf.open_db()
    db.execute("INSERT INTO photos (path, rating) VALUES ('/p/a.jpg', 5)")
    db.commit()
    db.close()
    monkeypatch.setattr(dbmod, "MIGRATIONS", every)
    monkeypatch.setattr(dbmod, "LATEST", latest)
    monkeypatch.setattr(dbmod, "_ready_dbs", set())
    db = pf.open_db()
    assert db.execute("PRAGMA user_version").fetchone()[0] == dbmod.LATEST
    assert db.execute("SELECT rating FROM photos").fetchone()[0] == 0
    assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='photos_rating'").fetchone()
