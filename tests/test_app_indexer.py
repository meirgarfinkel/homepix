"""M0, app side: the background indexer reports photos it kept as missing, and "remove them" forgets them."""

import importlib
import shutil

from conftest import photo, rows


def test_missing_photos_are_reported_then_forgotten_on_request(pf, lib):
    app = importlib.import_module("photofind_app")
    for i in range(60):
        photo(lib / "drive" / f"{i}.jpg")
    for i in range(20):
        photo(lib / "home" / f"{i}.jpg")
    app.save_settings({"folders": [str(lib)]})
    indexer = app.Indexer()
    indexer._run()
    shutil.rmtree(lib / "drive")

    indexer._run()
    assert indexer.snapshot()["missing"] == 60
    assert len(rows(pf, lib)) == 80

    indexer.start(forget_missing=True)  # what the banner's button asks for
    indexer.thread.join(timeout=30)
    assert indexer.snapshot()["missing"] == 0
    assert len(rows(pf, lib)) == 20
