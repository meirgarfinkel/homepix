"""HomePix was called Photo Finder: its data folder, database and the markers it left in photo
folders are found under their old names, and moved to the new ones."""

import sqlite3

from conftest import photo


def test_photo_finders_data_folder_is_moved_not_copied(tmp_path, monkeypatch):
    from homepix.core import config

    monkeypatch.delenv("HOMEPIX_HOME")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    old = tmp_path / "photofind"
    (old / "models").mkdir(parents=True)
    con = sqlite3.connect(old / "photofind.db")
    con.execute("CREATE TABLE t (x)")
    con.execute("INSERT INTO t VALUES (7)")
    con.commit()
    con.close()
    assert config.data_dir() == old and config.db_file() == old / "photofind.db"  # used where it is, until moved
    inode = (old / "photofind.db").stat().st_ino

    assert config.move_old_data()
    new = tmp_path / "homepix"
    assert not old.exists() and (new / "models").is_dir()
    assert config.data_dir() == new and config.db_file() == new / "homepix.db"
    assert (new / "homepix.db").stat().st_ino == inode  # (renamed: nothing copied)
    assert sqlite3.connect(new / "homepix.db").execute("SELECT x FROM t").fetchall() == [(7,)]
    assert not config.move_old_data()  # once


def test_the_old_name_for_home_still_works(tmp_path, monkeypatch):
    from homepix.core import config

    monkeypatch.delenv("HOMEPIX_HOME")
    monkeypatch.setenv("PHOTOFIND_HOME", str(tmp_path / "mine"))
    assert config.data_dir() == tmp_path / "mine" and config.old_data_dir() is None


def test_photo_finders_marks_in_photo_folders(pf, lib):
    from homepix.core import portable

    photo(lib / "a.jpg")
    (lib / "skip").mkdir()
    photo(lib / "skip" / "b.jpg")
    (lib / "skip" / ".photofind-ignore").touch()  # left out, as before
    pf.add_folder(lib)
    pf.index_folder(lib, want_faces=False)
    db = pf.open_db()
    assert [r[0].rsplit("/", 1)[-1] for r in db.execute("SELECT path FROM photos")] == ["a.jpg"]
    db.close()

    assert portable.write_copy(lib) == 1  # (a folder's copy, as Photo Finder kept it)
    (lib / ".homepix").rename(lib / ".photofind")
    assert portable.copy_path(lib) == lib / ".photofind" / "library.db"  # found under its old name
    assert portable.info(lib)["photos"] == 1
    portable.write_copy(lib)
    assert not (lib / ".photofind").exists() and portable.copy_path(lib) == lib / ".homepix" / "library.db"
