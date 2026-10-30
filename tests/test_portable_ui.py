"""The portable copy from the app: offered when a folder that has one is added, and kept up to date."""

import shutil

import pytest
from conftest import open_window, wait_until
from test_portable import named, new_computer, setup_library

from homepix.core import portable


@pytest.mark.parametrize("use", [True, False])
def test_adding_a_folder_with_a_library_offers_it(pf, lib, tmp_path, monkeypatch, app, use):
    from homepix.ui import portable as portable_ui

    setup_library(pf, lib)
    portable.write_copy(lib)
    before = named(pf, lib)
    there = tmp_path / "usb" / "Pictures"
    shutil.copytree(lib, there)
    new_computer(monkeypatch, tmp_path)
    asked = []
    monkeypatch.setattr(portable_ui, "ask_use_copy", lambda parent, root, meta: asked.append(meta["photos"]) or use)
    w = open_window(app)
    try:
        w.add_library_folder(str(there))
        wait_until(app, lambda: asked)
        assert asked == [5]
        if use:
            wait_until(app, lambda: named(pf, there) == before)
            wait_until(app, lambda: "Brought in 5 photos and 1 person" in w.statusBar().currentMessage())
            wait_until(app, lambda: "Ann" in w.people_names.values())
        else:  # looked through from the start instead: every photo (Old/ isn't left out), no one named
            wait_until(app, lambda: len(w.timeline.items) == 5 and not w.watcher.indexer.snapshot()["running"])
            assert named(pf, there) == {}
    finally:
        w.close()


def test_the_copy_is_kept_up_to_date(pf, lib, app, monkeypatch):
    from homepix.ui import portable as portable_ui

    setup_library(pf, lib)
    w = open_window(app)
    try:
        copies = portable_ui.PortableCopies(w)
        monkeypatch.setattr(portable_ui, "QUIET", 0)
        monkeypatch.setattr(portable_ui, "GAP", 0)
        copies._tick()
        wait_until(app, lambda: portable.current(lib))
        assert portable.info(lib)["people"] == 1
        db = pf.open_db()
        db.execute("UPDATE people SET name = 'Anna'")
        db.commit()
        db.close()
        assert not portable.current(lib)
        copies.flush()  # as on closing
        assert portable.current(lib)
    finally:
        w.close()
