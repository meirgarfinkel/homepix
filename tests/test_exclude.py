"""Excluding a folder: its photos leave All photos and search, but still show when it's opened."""

import os

import numpy as np
from conftest import photo


def _library(pf, lib):
    for name in ("2020/a.jpg", "2020/b.jpg", "Screens/c.jpg", "Screens/old/d.jpg"):
        photo(lib / name)
    pf.add_folder(lib)
    pf.index_folder(lib, want_faces=False)


def test_an_excluded_folder_is_left_out_but_can_be_opened(pf, lib):
    from homepix.core.timeline import load_items

    _library(pf, lib)
    screens = str(lib / "Screens")
    pf.set_excluded(screens, True)
    assert pf.excluded_folders() == [screens]
    db = pf.open_db()
    excluded = pf.excluded_folders()
    shown = {os.path.basename(it.path) for it in load_items(db, excluded=excluded)}
    assert shown == {"a.jpg", "b.jpg"}
    opened = load_items(db, folder=screens, excluded=excluded)
    assert {os.path.basename(it.path) for it in opened} == {"c.jpg", "d.jpg"}
    assert len(load_items(db, folder=lib / "Screens" / "old", excluded=excluded)) == 1

    from homepix.core.search import SearchIndex

    index = SearchIndex()
    index.text_model = lambda: type("T", (), {"query": staticmethod(lambda q: np.ones(512, np.float32))})()
    assert index.search("anything")["total"] == 2
    assert index.search("anything", folder=screens)["total"] == 2

    pf.set_excluded(screens, False)  # back in: search sees it again without re-indexing
    assert pf.excluded_folders() == []
    assert index.search("anything")["total"] == 4
    assert len(load_items(db, excluded=pf.excluded_folders())) == 4
    db.close()


def test_excluding_a_folder_that_holds_others_replaces_them(pf, lib):
    _library(pf, lib)
    pf.set_excluded(str(lib / "Screens" / "old"), True)
    pf.set_excluded(str(lib / "Screens"), True)
    assert pf.excluded_folders() == [str(lib / "Screens")]
    pf.set_excluded(str(lib / "Screens" / "old"), True)  # inside one left out already: nothing to add
    assert pf.excluded_folders() == [str(lib / "Screens")]
