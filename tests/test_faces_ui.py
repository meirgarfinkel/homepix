"""In the app: the "who is this?" page, someone's faces, renaming people, and face boxes in the
viewer. Every change goes through the journal, so Undo takes it back."""

import os

import numpy as np
import pytest
from conftest import open_window, photo, wait_until
from test_faces import look

from homepix.core.db import to_blob

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6 import QtCore, QtWidgets  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402


@pytest.fixture
def library(pf, lib):
    """Eight photos, one face each: five of one person, three of another."""
    for i in range(8):
        photo(lib / f"{i}.jpg")
    pf.add_folder(lib)
    pf.index_folder(lib, want_faces=False)
    db = pf.open_db()
    rng = np.random.default_rng(3)
    ids = [r[0] for r in db.execute("SELECT id FROM photos ORDER BY path")]
    for i, pid in enumerate(ids):
        db.execute(
            "INSERT INTO faces (photo_id, x, y, w, h, score, emb) VALUES (?, .3, .3, .4, .4, .99, ?)",
            (pid, to_blob(look(1 if i < 5 else 2, rng))),
        )
    db.commit()
    db.close()
    return ids


@pytest.fixture
def window(pf, app, library):
    w = open_window(app)
    yield w
    w.close()


def names(pf):
    db = pf.open_db()
    try:
        return [
            r[0]
            for r in db.execute(
                "SELECT pe.name FROM faces f LEFT JOIN people pe ON pe.id = f.person_id ORDER BY f.photo_id"
            )
        ]
    finally:
        db.close()


def settle(app, window):
    wait_until(app, lambda: not window.ops.busy)
    for _ in range(20):
        app.processEvents()


def test_most_like_ticked_brings_their_look_alikes_to_the_front(app):
    from homepix.ui import workers
    from homepix.ui.views.faces import WhoView

    rng = np.random.default_rng(1)
    who = [1, 2, 1, 2, 1, 2]
    group = [{"id": i, "path": f"/x/{i}.jpg", "emb": look(n, rng)} for i, n in enumerate(who)]
    real, workers.run = workers.run, lambda *a, **k: None  # (no pictures to make)
    try:
        v = WhoView()
        v.set_groups([group], [])
        assert not v.sort_button.isEnabled()  # nothing ticked
        v.grid.item(1).setSelected(True)
        assert v.sort_button.isEnabled()
        v.sort_button.click()
        ids = [f["id"] for f in v.grid.faces]
        assert ids[0] == 1 and set(ids[1:3]) == {3, 5} and set(ids[3:]) == {0, 2, 4}
        assert [f["id"] for f in v.grid.selected_faces()] == [1]  # still ticked
        assert v.grid.item(0).data(QtCore.Qt.ItemDataRole.UserRole) == 1
    finally:
        workers.run = real


def test_naming_a_group_asks_once_about_faces_that_might_be_them(app):
    from homepix.core.people import KNOWN, NEW
    from homepix.ui import workers
    from homepix.ui.views.faces import WhoView

    rng = np.random.default_rng(2)

    def faces(start, n, tier=NEW, like=None):
        return [
            {"id": i, "path": f"/x/{i}.jpg", "emb": look(1, rng), "tier": tier, "like": like}
            for i in range(start, start + n)
        ]

    new, known = faces(0, 3), faces(10, 3, KNOWN, "Ann")
    real, workers.run = workers.run, lambda *a, **k: None  # (no pictures to make)
    try:
        v = WhoView()
        named, confirmed = [], []
        v.named.connect(lambda ids, name: (named.append((ids, name)), v.drop(ids)))  # (as the window does)
        v.confirmed.connect(lambda yes, no, name: (confirmed.append((yes, no, name)), v.drop(yes + no)))
        v.set_groups([new, known], ["Ann"])
        assert "Someone new?" in v.hint.text() and v.rest_button.isHidden()
        for i in range(2):
            v.grid.item(i).setSelected(True)
        v.name.setText("Cy")
        v.name_button.click()
        assert named == [([0, 1], "Cy")] and v.waiting_for == "Cy" and v.grid.count() == 0

        v.set_groups([known], ["Ann", "Cy"])  # (worked out before the naming was done: still waiting)
        assert v.waiting_for == "Cy"
        maybe = faces(20, 4)
        v.set_groups([known], ["Ann", "Cy"], maybe)
        assert v.title.text() == "Is this Cy?" and v.name.text() == "Cy" and v.grid.count() == 4
        assert not v.rest_button.isHidden() and v.rest_button.text() == "The rest aren't Cy"
        v.grid.item(0).setSelected(True)
        v.rest_button.click()
        assert confirmed == [([20], [21, 22, 23], "Cy")]
        assert v.follow is None and v.waiting_for is None  # just the once: on to the groups
        assert v.title.text() == "Who is this?" and "Looks like Ann." in v.hint.text()
        assert v.name.text() == "Ann"  # a guess, already typed
        for i in range(3):
            v.grid.item(i).setSelected(True)
        v.name_button.click()
        assert v.waiting_for == "Ann"
        v.set_groups([], ["Ann", "Cy"], [])  # nothing more like her: no follow-up
        assert v.follow is None and v.title.text() == "No one to ask about"
    finally:
        workers.run = real


def test_who_is_this_names_a_group_and_undo_takes_it_back(window, pf, app):
    from homepix.ui.views.people import WHO_TILE
    from homepix.ui.views.sidebar import PEOPLE

    window.show_place(PEOPLE)  # it's the first circle on the People page
    wait_until(app, lambda: window.people.count() and window.people.item(0).data(256) == WHO_TILE)
    wait_until(app, lambda: window.people.item(0).text() == "Who is this?\n2 to name")
    window.people.itemClicked.emit(window.people.item(0))
    wait_until(app, lambda: window.stack.currentWidget() is window.who and window.who.grid.count() == 5)
    assert window.who.remaining() == 2
    assert window.who.grid.selected_faces() == []  # none ticked to start with
    window.who.name.setText("Ann")
    assert not window.who.name_button.isEnabled()  # nothing ticked: no one to name yet
    for i in range(4):  # one isn't them (they're named once it's recognized)
        window.who.grid.item(i).setSelected(True)
    assert window.who.name_button.text() == "Name 4 faces"
    window.who.name_button.click()
    assert window.who.waiting_for == "Ann"  # looking for faces that might be Ann too...
    settle(app, window)
    wait_until(app, lambda: window.who.grid.count() == 3)  # none (Bob's are nothing like her): on to the next group
    assert window.who.follow is None
    assert names(pf) == ["Ann"] * 5 + [None] * 3  # the one left out was recognized, being so alike
    wait_until(app, lambda: window.who.remaining() == 1)
    assert "Ann" in window.people_names.values()

    assert window.who.grid.selected_faces() == [] and window.who.hide_button.isEnabled()
    window.who.hide_button.click()  # strangers: none ticked, so the whole group; don't ask again
    settle(app, window)
    wait_until(app, lambda: window.who.remaining() == 0)
    assert window.who.title.text() == "No one to ask about"

    window.ops.undo()
    settle(app, window)
    wait_until(app, lambda: window.who.remaining() == 1)
    window.ops.undo()
    settle(app, window)
    assert names(pf) == [None] * 8
    wait_until(app, lambda: window.who.remaining() == 2)


def test_someones_faces_and_taking_out_one_that_isnt_them(window, pf, app):
    from homepix.core import people
    from homepix.ui.views.sidebar import faces_place, person_place

    db = pf.open_db()
    ann = [r[0] for r in db.execute("SELECT id FROM faces ORDER BY photo_id")]
    db.close()
    window.naming.name(ann[:6], "Ann")  # one of them is really someone else
    settle(app, window)
    db = pf.open_db()
    pid = people.person_id(db, "Ann")
    db.close()
    window.show_place(person_place(pid))
    wait_until(app, lambda: len(window.timeline.items) == 6)
    assert window.faces_action.isVisible()
    window.faces_action.trigger()
    wait_until(app, lambda: window.place == faces_place(pid) and window.person_faces.grid.count() == 6)
    assert window.stack.currentWidget() is window.person_faces
    first = window.person_faces.grid.faces[0]
    assert first["id"] == ann[5]  # the odd one out comes first
    grid = window.person_faces.grid
    assert grid.selected_faces() == [] and not window.person_faces.not_button.isEnabled()

    def click(row):  # a click ticks or unticks, as on Who is this?
        QTest.mouseClick(
            grid.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=grid.visualItemRect(grid.item(row)).center()
        )

    click(0)
    click(1)
    assert {f["id"] for f in grid.selected_faces()} == {grid.faces[0]["id"], grid.faces[1]["id"]}
    click(1)
    assert [f["id"] for f in grid.selected_faces()] == [first["id"]]
    window.person_faces.sort_button.click()  # Most like ticked: the ticked one first, the rest after
    assert grid.faces[0]["id"] == first["id"] and len(grid.faces) == 6
    assert [f["id"] for f in grid.selected_faces()] == [first["id"]]
    window.person_faces.not_button.click()
    settle(app, window)
    wait_until(app, lambda: window.person_faces.grid.count() == 5)
    assert names(pf)[5] is None
    window.person_faces.back.emit()
    wait_until(app, lambda: len(window.timeline.items) == 5)


def test_rename_and_forget_from_the_people_page(window, pf, app, monkeypatch):
    from homepix.ui import people_actions
    from homepix.ui.views.people import WHO_TILE
    from homepix.ui.views.sidebar import PEOPLE

    def named():  # the people's circles (not "Who is this?")
        items = [window.people.item(i) for i in range(window.people.count())]
        return [it for it in items if it.data(256) != WHO_TILE]

    db = pf.open_db()
    faces = [r[0] for r in db.execute("SELECT id FROM faces ORDER BY photo_id")]
    db.close()
    window.naming.name(faces[:5], "Ann")
    settle(app, window)
    window.show_place(PEOPLE)
    wait_until(app, lambda: len(named()) == 1)
    pid = named()[0].data(256)
    monkeypatch.setattr(people_actions, "ask_name", lambda *a, **k: "Anna")
    window.naming.rename(pid)
    settle(app, window)
    wait_until(app, lambda: len(named()) == 1 and named()[0].text().startswith("Anna"))
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "question", staticmethod(lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Yes)
    )
    window.naming.forget(pid)
    settle(app, window)
    assert names(pf) == [None] * 8
    wait_until(app, lambda: named() == [])
    window.ops.undo()
    settle(app, window)
    assert names(pf)[:5] == ["Anna"] * 5


def test_face_boxes_in_the_viewer(window, pf, app):
    from PySide6.QtCore import QPointF

    window.open_viewer(0)
    v = window.viewer
    wait_until(app, lambda: v._image() is not None)
    v.show_faces = False
    v.toggle_faces()
    wait_until(app, lambda: v._current_faces())
    rect = v._target_rect(v._image())
    face = v._face_at(QPointF(rect.center()))  # the face is in the middle of the photo
    assert face is not None and face["name"] is None
    assert v._face_at(QPointF(rect.x() + 2, rect.y() + 2)) is None
    v.grab()  # the boxes draw
    window.naming.name([face["id"]], "Bo")
    settle(app, window)
    wait_until(app, lambda: v._current_faces() and v._current_faces()[0]["name"] == "Bo")
    assert window.stack.currentWidget() is v  # still looking at it
    v.toggle_faces()
    assert not v._current_faces()
    assert window.settings.value("viewer/faces") == "false"


def test_a_face_grid_can_be_drawn_before_it_has_faces(app, monkeypatch):
    import sys

    from homepix.ui.views.faces import FaceGrid

    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *e: errors.append(e[1]))
    grid = FaceGrid()
    grid.resize(400, 300)
    grid.grab()  # (paints it)
    assert errors == []
