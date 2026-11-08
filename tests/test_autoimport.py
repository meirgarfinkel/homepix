"""Auto import: new photos in a watch folder come into the library, once, by the rule's settings;
what's already there isn't imported again; and every batch is undoable."""

import os
import time

from conftest import photo, trashed_files
from test_organize import shot

from homepix.core import autoimport as ai
from homepix.core import fileops as fo

LATER = lambda: time.time() + ai.SETTLE + 5  # noqa: E731  (when everything has settled)


def rule(tmp_path, lib, **kw):
    watch = tmp_path / "Phone"
    watch.mkdir(exist_ok=True)
    kw.setdefault("dest", str(lib / "From phone"))
    if not kw.get("tidy"):
        os.makedirs(kw["dest"], exist_ok=True)
    return ai.Rule(name="Dad's phone", watch=str(watch), **kw)


def go(db, r, lib, now=None):
    batch = ai.check(db, r, [str(lib)], now=now or LATER())
    return batch, ai.run(db, batch)


def names(folder):
    return sorted(
        os.path.relpath(os.path.join(d, f), folder).replace(os.sep, "/")
        for d, _, fs in os.walk(folder)
        for f in fs
        if not f.startswith(".")
    )


def test_moves_new_files_into_the_destination_keeping_names(home, lib, tmp_path):
    pf, db = home
    r = rule(tmp_path, lib)
    photo(tmp_path / "Phone" / "IMG_1.jpg")
    photo(tmp_path / "Phone" / "Camera" / "IMG_2.jpg")
    batch, res = go(db, r, lib)
    assert batch.summary() == "Import 2 photos from Dad's phone"
    assert res.finished == 2
    assert names(lib / "From phone") == ["IMG_1.jpg", "IMG_2.jpg"]
    assert names(tmp_path / "Phone") == []


def test_leave_copies_and_never_takes_the_same_file_again(home, lib, tmp_path):
    pf, db = home
    r = rule(tmp_path, lib, after=ai.LEAVE)
    photo(tmp_path / "Phone" / "IMG_1.jpg")
    go(db, r, lib)
    assert names(tmp_path / "Phone") == ["IMG_1.jpg"]
    assert names(lib / "From phone") == ["IMG_1.jpg"]
    os.remove(lib / "From phone" / "IMG_1.jpg")  # deleted from the library: not brought back
    batch, res = go(db, r, lib)
    assert res is None and not batch.imports
    assert names(lib / "From phone") == []


def test_tidy_names_by_date_into_year_folders(home, lib, tmp_path):
    pf, db = home
    r = rule(tmp_path, lib, tidy=True, dest="")
    shot(tmp_path / "Phone" / "IMG_9.jpg", taken="2021:08:01 19:09:04")
    shot(tmp_path / "Phone" / "IMG_8.jpg", taken="2021:08:01 19:09:04")  # the same second
    photo(tmp_path / "Phone" / "nodate.jpg")
    go(db, r, lib)
    assert names(lib) == ["2021/2021_08_01_19_09_04.jpg", "2021/2021_08_01_19_09_04_1.jpg", "Unsorted/nodate.jpg"]


def test_a_live_photo_pair_keeps_one_name(home, lib, tmp_path):
    pf, db = home
    r = rule(tmp_path, lib)
    (lib / "From phone" / "IMG_5.JPG").write_bytes(b"in the way")
    photo(tmp_path / "Phone" / "IMG_5.JPG")
    (tmp_path / "Phone" / "IMG_5.MOV").write_bytes(b"a short clip")
    (tmp_path / "Phone" / "IMG_5.AAE").write_bytes(b"edits")
    go(db, r, lib)
    assert names(lib / "From phone") == ["IMG_5.JPG", "IMG_5_1.AAE", "IMG_5_1.JPG", "IMG_5_1.MOV"]


def test_already_in_the_library_goes_to_the_trash_when_moving(home, lib, tmp_path):
    pf, db = home
    r = rule(tmp_path, lib)
    old = photo(lib / "2020" / "kept.jpg")
    pf.index_folder(lib, want_faces=False)
    (tmp_path / "Phone" / "same.jpg").write_bytes(old.read_bytes())
    batch, res = go(db, r, lib)
    assert [os.path.basename(f.path) for f in batch.already] == ["same.jpg"]
    assert names(tmp_path / "Phone") == [] and len(trashed_files(tmp_path)) == 1
    assert names(lib / "From phone") == []


def test_already_in_the_library_is_left_when_leaving(home, lib, tmp_path):
    pf, db = home
    r = rule(tmp_path, lib, after=ai.LEAVE)
    old = photo(lib / "2020" / "kept.jpg")
    pf.index_folder(lib, want_faces=False)
    (tmp_path / "Phone" / "same.jpg").write_bytes(old.read_bytes())
    batch, res = go(db, r, lib)
    assert res is None and names(tmp_path / "Phone") == ["same.jpg"]
    assert not ai.check(db, r, [str(lib)], now=LATER()).already  # remembered: not read again


def test_two_copies_in_the_watch_folder_import_once(home, lib, tmp_path):
    pf, db = home
    r = rule(tmp_path, lib)
    a = photo(tmp_path / "Phone" / "a.jpg")
    (tmp_path / "Phone" / "b.jpg").write_bytes(a.read_bytes())
    go(db, r, lib)
    assert len(names(lib / "From phone")) == 1 and names(tmp_path / "Phone") == []


def test_files_still_arriving_wait(home, lib, tmp_path):
    pf, db = home
    r = rule(tmp_path, lib)
    photo(tmp_path / "Phone" / "fresh.jpg")
    photo(tmp_path / "Phone" / ".syncthing.IMG_3.jpg.tmp")
    photo(tmp_path / "Phone" / "IMG_4.jpg.part")
    batch = ai.check(db, r, [str(lib)], now=time.time())
    assert not batch.imports  # fresh.jpg has only just arrived
    batch = ai.check(db, r, [str(lib)], now=LATER())
    assert [os.path.basename(f.path) for f, _ in batch.imports] == ["fresh.jpg"]


def test_undo_puts_them_back_and_they_are_left_alone_after(home, lib, tmp_path):
    pf, db = home
    r = rule(tmp_path, lib)
    photo(tmp_path / "Phone" / "IMG_1.jpg")
    go(db, r, lib)
    fo.undo(db)
    assert names(tmp_path / "Phone") == ["IMG_1.jpg"] and names(lib / "From phone") == []
    batch, res = go(db, r, lib)  # not wanted: not imported again, nor sent to the Trash
    assert res is None and names(tmp_path / "Phone") == ["IMG_1.jpg"]
    assert not trashed_files(tmp_path)


def test_a_failed_file_is_tried_again(home, lib, tmp_path, monkeypatch):
    pf, db = home
    r = rule(tmp_path, lib)
    photo(tmp_path / "Phone" / "IMG_1.jpg")
    batch = ai.check(db, r, [str(lib)], now=LATER())
    os.rename(tmp_path / "Phone" / "IMG_1.jpg", tmp_path / "IMG_1.jpg")  # gone before it could be moved
    res = ai.run(db, batch)
    assert res.finished == 0
    os.rename(tmp_path / "IMG_1.jpg", tmp_path / "Phone" / "IMG_1.jpg")
    batch, res = go(db, r, lib)
    assert res.finished == 1


def test_rules_that_cant_be_saved(pf, lib, tmp_path):
    folders = [str(lib)]
    (tmp_path / "Phone").mkdir()
    ok = ai.Rule(name="Phone", watch=str(tmp_path / "Phone"), dest=str(lib))
    assert ai.problem(ok, [], folders) is None
    inside = ai.Rule(name="x", watch=str(lib), dest=str(lib))
    assert "inside your library" in ai.problem(inside, [], folders)
    outer = tmp_path / "Drive"
    (outer / "Pictures").mkdir(parents=True)
    holds = ai.Rule(name="x", watch=str(outer), dest=str(outer / "Pictures"))
    assert "hold a library folder" in ai.problem(holds, [], [str(outer / "Pictures")])
    twice = ai.Rule(name="y", watch=str(tmp_path / "Phone"), dest=str(lib))
    assert "already watches" in ai.problem(twice, [ok], folders)
    outside = ai.Rule(name="z", watch=str(tmp_path / "Phone"), dest=str(tmp_path))
    assert "inside your library" in ai.problem(outside, [], folders)
    tidy = ai.Rule(name="t", watch=str(tmp_path / "Phone"), tidy=True)
    assert ai.problem(tidy, [], folders) is None  # one library folder: that's the one
    assert "which library folder" in ai.problem(tidy, [], folders + [str(tmp_path / "Other")])


def test_rules_are_kept_in_settings(pf, lib, tmp_path):
    r = ai.Rule(name="Phone", watch=str(tmp_path), dest=str(lib), after=ai.LEAVE)
    ai.save_rules([r])
    assert ai.load_rules() == [r]
    ai.set_status(r.id, checked="now")
    assert ai.status(r.id) == {"checked": "now"}
    ai.save_rules([])
    assert ai.load_rules() == [] and ai.status(r.id) == {}


def test_a_missing_watch_folder_just_waits(home, lib, tmp_path):
    pf, db = home
    r = ai.Rule(name="USB", watch=str(tmp_path / "unplugged"), dest=str(lib))
    assert ai.waiting(r, [str(lib)]) == "The watch folder isn't there."
