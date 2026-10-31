"""Declutter: photos the search model sees as ads, screenshots, documents or accidental shots,
listed by kind; never photos with someone named in them."""

import os

import numpy as np
from conftest import open_window, own_trash, trashed_files, wait_until
from PIL import Image

from homepix.core import junk
from homepix.core.db import to_blob

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class FakeText:
    """Descriptions as vectors: the flyer phrase is the flyer photo's own, the photos-worth-keeping
    phrases are the other photos', and every other phrase is unlike anything."""

    def __init__(self, flyer, others, receipt=None):
        self.flyer, self.others, self.receipt = flyer, others, receipt
        self.rng = np.random.default_rng(3)

    def query(self, phrase):
        if phrase == "a flyer":
            return self.flyer
        if phrase == "a receipt" and self.receipt is not None:
            return self.receipt
        if phrase in junk.KEEP:
            return self.others[junk.KEEP.index(phrase) % len(self.others)]
        v = self.rng.normal(size=512).astype(np.float32)
        return v / np.linalg.norm(v)


COLORS = [(250, 5, 5), (5, 250, 5), (5, 5, 250), (5, 250, 250), (250, 5, 250), (250, 250, 5), (200, 200, 200)]


def _library(pf, lib):
    """A red photo (the flyer) and six of other colours (their search vectors point elsewhere)."""
    for i, color in enumerate(COLORS):
        Image.new("RGB", (64, 48), color).save(lib / f"{i}.jpg")
    pf.add_folder(lib)
    pf.index_folder(lib, want_faces=False)
    db = pf.open_db()
    rows = {os.path.basename(p): (i, pf.from_blob(c)) for i, p, c in db.execute("SELECT id, path, clip FROM photos")}
    db.close()
    flyer_id, flyer = rows.pop("0.jpg")
    return flyer_id, flyer, [v for _, v in rows.values()]


def test_an_ad_is_listed_as_one_and_ordinary_photos_arent(pf, lib):
    flyer_id, flyer, others = _library(pf, lib)
    index = pf.SearchIndex()
    index._text = FakeText(flyer, others)
    db = pf.open_db()
    try:
        assert [(kind, [r["id"] for r in rows]) for kind, rows in junk.find(index, db)] == [
            ("Ads and flyers", [flyer_id])
        ]

        # someone named is in it: not junk
        db.execute("INSERT INTO people (id, name) VALUES (1, 'Ann')")
        db.execute(
            "INSERT INTO faces (photo_id, x, y, w, h, score, emb, person_id) VALUES (?, .1, .1, .2, .2, .9, ?, 1)",
            (flyer_id, to_blob(np.ones(128, np.float32))),
        )
        db.commit()
        assert junk.find(index, db) == []
    finally:
        db.close()


def test_the_possibly_junk_page(pf, lib, app):
    from homepix.ui.views.sidebar import JUNK

    flyer_id, flyer, others = _library(pf, lib)
    window = open_window(app)
    try:
        window.search_index._text = FakeText(flyer, others)
        window.show_place(JUNK)
        wait_until(app, lambda: [it.id for it in window.timeline.items] == [flyer_id])
        assert window.timeline.months[0].title == "Ads and flyers"
        assert window.page_title.text() == "Declutter"
        assert not window.timeline.scrubber.isVisibleTo(window)
        sidebar = {window.sidebar.topLevelItem(i).text(0): i for i in range(window.sidebar.topLevelItemCount())}
        assert sidebar["Declutter"] == sidebar["Duplicates"] + 1  # right under Duplicates
        assert window.sidebar.topLevelItem(sidebar["Declutter"]).text(1) == "1"
    finally:
        window.close()


def test_on_possibly_junk_a_click_ticks_a_photo(pf, lib, app, tmp_path, monkeypatch):
    """As on Who is this?: a click ticks or unticks; Delete moves only what's ticked to the Trash."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from homepix.ui.views.sidebar import ALL, JUNK

    own_trash(monkeypatch, tmp_path)
    flyer_id, flyer, others = _library(pf, lib)
    receipt = others.pop(0)  # the green one is a receipt
    window = open_window(app)
    try:
        window.search_index._text = FakeText(flyer, others, receipt)
        window.show_place(JUNK)
        view = window.timeline
        wait_until(app, lambda: len(view.items) == 2)

        def click(i):
            r = view.item_rect(i).translated(0, -view.verticalScrollBar().value())
            QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, r.center())

        assert view.ticking and view.selected == set() and "click photos to tick them" in window.count_label.text()
        click(0)
        click(1)
        assert view.selected == {0, 1} and "2 ticked" in window.count_label.text()
        click(0)
        click(1)
        assert view.selected == set() and window.files.targets() == []  # nothing ticked: nothing to delete

        click(0)
        gone = view.items[0].id
        window.files.trash_them()
        wait_until(app, lambda: len(view.items) == 1 and not window.ops.busy)
        assert len(trashed_files(tmp_path)) == 1 and view.items[0].id != gone
        assert view.selected == set()  # the one left isn't ticked by itself

        window.show_place(ALL)  # elsewhere a click chooses a photo, as always
        wait_until(app, lambda: len(view.items) == 6)
        assert not view.ticking
    finally:
        window.close()
