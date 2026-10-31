"""Possibly junk: photos the search model sees as ads, screenshots, documents or accidental shots,
listed by kind; never photos with someone named in them."""

import os

import numpy as np
from conftest import open_window, wait_until
from PIL import Image

from homepix.core import junk
from homepix.core.db import to_blob

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class FakeText:
    """Descriptions as vectors: the flyer phrase is the flyer photo's own, the photos-worth-keeping
    phrases are the other photos', and every other phrase is unlike anything."""

    def __init__(self, flyer, others):
        self.flyer, self.others = flyer, others
        self.rng = np.random.default_rng(3)

    def query(self, phrase):
        if phrase == "a flyer":
            return self.flyer
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
        assert window.page_title.text() == "Possibly junk"
        assert not window.timeline.scrubber.isVisibleTo(window)
        sidebar = {window.sidebar.topLevelItem(i).text(0): i for i in range(window.sidebar.topLevelItemCount())}
        assert sidebar["Possibly junk"] == sidebar["Duplicates"] + 1  # right under Duplicates
        assert window.sidebar.topLevelItem(sidebar["Possibly junk"]).text(1) == "1"
    finally:
        window.close()
