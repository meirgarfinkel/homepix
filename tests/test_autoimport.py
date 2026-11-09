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
    whole = ai.Rule(name="x", watch=str(lib), dest=str(lib))
    assert "whole library folder" in ai.problem(whole, [], folders)
    (lib / "Inbox").mkdir()
    inside = ai.Rule(name="x", watch=str(lib / "Inbox"), dest=str(lib))
    assert ai.problem(inside, [], folders) is None
    outer = tmp_path / "Drive"
    (outer / "Pictures").mkdir(parents=True)
    holds = ai.Rule(name="x", watch=str(outer), dest=str(outer / "Pictures"))
    assert "contain a library folder" in ai.problem(holds, [], [str(outer / "Pictures")])
    twice = ai.Rule(name="y", watch=str(tmp_path / "Phone"), dest=str(lib))
    assert "already imports from" in ai.problem(twice, [ok], folders)
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
    assert ai.waiting(r, [str(lib)]) == 'The "Import from" folder isn\'t connected.'


def test_several_rules_import_as_one_change(home, lib, tmp_path):
    pf, db = home
    dad = rule(tmp_path, lib)
    (tmp_path / "Mom").mkdir()
    mom = ai.Rule(name="Mom's phone", watch=str(tmp_path / "Mom"), dest=str(lib / "From phone"))
    photo(tmp_path / "Phone" / "a.jpg")
    photo(tmp_path / "Mom" / "b.jpg")
    batches = [ai.check(db, r, [str(lib)], now=LATER()) for r in (dad, mom)]
    assert ai.summary(batches) == "Import 2 photos from Dad's phone and Mom's phone"
    res = ai.run(db, batches)
    assert res.finished == 2
    fo.undo(db)
    assert names(lib / "From phone") == []


def test_checking_the_rules_notes_what_each_did(home, lib, tmp_path):
    pf, db = home
    auto = rule(tmp_path, lib)
    (tmp_path / "Review").mkdir()
    manual = ai.Rule(name="Scanner", watch=str(tmp_path / "Review"), dest=str(lib / "From phone"), auto=False)
    gone = ai.Rule(name="USB", watch=str(tmp_path / "unplugged"), dest=str(lib))
    off = ai.Rule(name="Off", watch=str(tmp_path / "Review"), dest=str(lib), enabled=False)
    photo(tmp_path / "Phone" / "a.jpg")
    photo(tmp_path / "Review" / "b.jpg")
    out = ai.check_rules(db, [auto, manual, gone, off], [str(lib)], now=LATER())
    assert [b.rule.name for b in out.batches] == ["Dad's phone", "Scanner"]
    assert names(lib / "From phone") == ["a.jpg"]  # the review one waits
    assert ai.status(auto.id)["count"] == 1
    assert ai.status(manual.id)["pending"] == 1
    assert ai.status(gone.id)["waiting"] == 'The "Import from" folder isn\'t connected.'
    assert ai.status(off.id) == {}


# --- in the app ---------------------------------------------------------------------------------------


def test_the_window_imports_says_so_and_undo_takes_it_back(app, pf, lib, tmp_path, monkeypatch):
    from conftest import open_window, own_trash, wait_until

    own_trash(monkeypatch, tmp_path)
    monkeypatch.setattr(ai, "SETTLE", 0)
    pf.add_folder(lib)
    r = rule(tmp_path, lib)
    ai.save_rules([r])
    photo(tmp_path / "Phone" / "IMG_1.jpg")
    w = open_window(app)
    try:
        w.importer.check()
        wait_until(app, lambda: w.statusBar().currentMessage() == "Imported 1 photo from Dad's phone.")
        assert names(lib / "From phone") == ["IMG_1.jpg"]
        assert ai.status(r.id)["count"] == 1
        wait_until(app, lambda: not w.ops.busy)
        w.ops.undo()  # (Edit > Undo)
        wait_until(app, lambda: names(tmp_path / "Phone") == ["IMG_1.jpg"])
        assert not hasattr(w, "import_bar")  # no bar across the window, just the line at the bottom
    finally:
        w.close()


def test_organizing_imports_to_a_library_folder(app, pf, lib, tmp_path):
    from homepix.ui.views.rule_dialog import RuleDialog

    (tmp_path / "Phone").mkdir()
    d = RuleDialog(None, [], [str(lib)])
    d.name.setText("Phone")
    d.watch.setText(str(tmp_path / "Phone"))
    d.tidy.setChecked(True)
    assert not d.dest_row.isVisibleTo(d) and d.root.isVisibleTo(d)  # a library folder to import to
    d._save()
    assert d.result() == d.DialogCode.Accepted
    assert d.rule.tidy and d.rule.dest == str(lib) and d.rule.after == ai.MOVE and d.rule.auto
    d2 = RuleDialog(None, [d.rule], [str(lib)])
    d2.name.setText("Again")
    d2.watch.setText(str(tmp_path / "Phone"))
    d2.dest.setText(str(lib))
    d2._save()
    assert "already imports from" in d2.problem.text()


def test_a_review_can_leave_some_out_for_next_time(home, lib, tmp_path):
    pf, db = home
    r = rule(tmp_path, lib, auto=False)
    photo(tmp_path / "Phone" / "a.jpg")
    photo(tmp_path / "Phone" / "b.jpg")
    batch = ai.check(db, r, [str(lib)], now=LATER())
    ai.run(db, batch.without([str(tmp_path / "Phone" / "b.jpg")]))
    assert names(lib / "From phone") == ["a.jpg"] and names(tmp_path / "Phone") == ["b.jpg"]
    again = ai.check(db, r, [str(lib)], now=LATER())
    assert [os.path.basename(f.path) for f, _ in again.imports] == ["b.jpg"]


def test_a_rule_that_waits_offers_a_review(app, pf, lib, tmp_path, monkeypatch):
    from conftest import open_window, own_trash, wait_until

    from homepix.ui.views import review_dialog

    own_trash(monkeypatch, tmp_path)
    monkeypatch.setattr(ai, "SETTLE", 0)
    pf.add_folder(lib)
    r = rule(tmp_path, lib, auto=False)
    ai.save_rules([r])
    photo(tmp_path / "Phone" / "a.jpg")
    photo(tmp_path / "Phone" / "b.jpg")
    w = open_window(app)
    try:
        w.importer.check()
        wait_until(app, lambda: w.review_bar.isVisible())
        assert "2 new files from Dad's phone" in w.review_bar.label.text()
        assert names(tmp_path / "Phone") == ["a.jpg", "b.jpg"]  # nothing done yet

        def review(dlg):  # untick b.jpg, then Import
            dlg.grid.item(1).setCheckState(review_dialog.Qt.CheckState.Unchecked)
            assert dlg.go.text() == "Import 1"
            return review_dialog.ReviewDialog.DialogCode.Accepted

        def fake_exec(self):
            self.setResult(review(self))
            return self.result()

        monkeypatch.setattr(review_dialog.ReviewDialog, "exec", fake_exec)
        w.review_bar.review_button.click()
        wait_until(app, lambda: w.statusBar().currentMessage().startswith("Imported"))
        assert names(lib / "From phone") == ["a.jpg"] and names(tmp_path / "Phone") == ["b.jpg"]
        assert not w.review_bar.isVisible()
    finally:
        w.close()


def test_an_import_from_folder_inside_the_library(home, lib, tmp_path):
    """Its photos are indexed (it's in the library, though excluded), but they're on their way in:
    not already in the library, and not duplicates of their copies."""
    from homepix.core import duplicates

    pf, db = home
    (lib / "From phone").mkdir()
    r = ai.Rule(name="Inbox", watch=str(lib / "Inbox"), dest=str(lib / "From phone"), after=ai.LEAVE)
    ai.save_rules([r])
    pf.set_excluded(str(lib / "Inbox"), True)
    photo(lib / "Inbox" / "a.jpg")
    pf.index_folder(lib, want_faces=False)
    batch, res = go(db, r, lib)
    assert res.finished == 1 and names(lib / "From phone") == ["a.jpg"]
    pf.index_folder(lib, want_faces=False)
    assert duplicates.find(db) == []


def test_saving_an_import_from_folder_in_the_library_excludes_it(app, pf, lib, monkeypatch):
    from homepix.ui.views import rule_dialog

    pf.add_folder(lib)
    (lib / "Inbox").mkdir()
    d = rule_dialog.RuleDialog(None, [], [str(lib)])
    d.name.setText("Inbox")
    d.watch.setText(str(lib / "Inbox"))
    d.dest.setText(str(lib))
    asked = []
    monkeypatch.setattr(
        rule_dialog.QMessageBox,
        "question",
        lambda *a, **k: asked.append(a[2]) or rule_dialog.QMessageBox.StandardButton.Cancel,
    )
    d._save()
    assert asked and "Excluded folders" in asked[0]
    assert d.result() != d.DialogCode.Accepted and pf.excluded_folders() == []  # said no: nothing saved
    monkeypatch.setattr(rule_dialog.QMessageBox, "question", lambda *a, **k: rule_dialog.QMessageBox.StandardButton.Yes)
    d._save()
    assert d.result() == d.DialogCode.Accepted and d.excluded_now
    assert pf.excluded_folders() == [str(lib / "Inbox")]
