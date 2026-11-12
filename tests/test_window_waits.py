"""Work that arrives while the library is busy waits its turn: nothing the user confirmed is dropped,
and quitting doesn't cut a change off. (An update waiting for a change: test_updates.py.)"""

import threading
import types

from conftest import open_window, wait_until
from test_portable import setup_library

from homepix.core import portable


def test_a_change_asked_for_meanwhile_starts_when_the_first_ends(app, pf):
    w = open_window(app, ready=None)
    try:
        gate, ran = threading.Event(), []
        w.ops._in_background("first", lambda p, s: gate.wait(10), lambda r, e: ran.append("first"), exclusive=False)
        assert w.ops.busy
        second = w.ops._in_background("second", lambda p, s: None, lambda r, e: ran.append("second"), exclusive=False)
        third = w.ops._in_background("third", lambda p, s: None, lambda r, e: ran.append("third"), exclusive=False)
        assert second and not third  # one waits; a second one waiting is refused, and says so
        assert "Still busy" in w.statusBar().currentMessage()
        gate.set()
        wait_until(app, lambda: ran == ["first", "second"] and not w.ops.busy)
    finally:
        w.close()


def test_an_import_check_that_found_the_library_busy_runs_when_it_is_free(app, pf, monkeypatch):
    from homepix.core import autoimport

    w = open_window(app, ready=None)
    try:
        monkeypatch.setattr(autoimport, "load_rules", lambda: [types.SimpleNamespace(enabled=True)])
        w.ops.busy = True
        w.importer.check(asked=True)
        assert w.importer._wanted is True  # (not a timer two minutes on)
        checks = []
        monkeypatch.setattr(w.importer, "check", lambda asked=False: checks.append(asked))
        w.ops.history_changed.emit()  # still busy (the next change started): not yet
        assert checks == []
        w.ops.busy = False
        w.ops.history_changed.emit()
        assert checks == [True] and w.importer._wanted is None
    finally:
        w.ops.busy = False
        w.close()


def test_closing_to_the_background_keeps_the_copies_up_to_date(app, pf, lib, monkeypatch):
    from homepix.core import background
    from homepix.ui import portable as portable_ui

    setup_library(pf, lib)
    monkeypatch.setattr(portable_ui, "QUIET", 0)
    monkeypatch.setattr(portable_ui, "GAP", 0)
    monkeypatch.setattr(background, "on", lambda: True)
    w = open_window(app, index=True)
    try:
        w.close()  # kept running: the window only hides
        assert w.isHidden() and w.copies._timer.isActive()
        wait_until(app, lambda: portable.is_current(lib))
        db = pf.open_db()
        db.execute("UPDATE people SET name = 'Anna'")
        db.commit()
        db.close()
        w.copies._tick()
        wait_until(app, lambda: portable.is_current(lib))  # written again, the window still hidden
    finally:
        w.quit_app()


def test_quitting_waits_for_changes_before_closing_exiftool(monkeypatch):
    import atexit
    import os

    from homepix.core import exiftool
    from homepix.ui import app as app_mod
    from homepix.ui import workers

    order = []
    monkeypatch.setattr(workers, "wait_idle", lambda timeout: order.append("waited"))
    monkeypatch.setattr(exiftool, "close_shared", lambda: order.append("closed ExifTool"))
    monkeypatch.setattr(atexit, "_run_exitfuncs", lambda: None)
    monkeypatch.setattr(os, "_exit", lambda code: order.append(code))
    app_mod._exit(0)
    assert order == ["waited", "closed ExifTool", 0]
