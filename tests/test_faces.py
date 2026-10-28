"""M6: naming people, fixing wrong matches, "not a face", renaming, merging and forgetting, all
undoable; the "who is this" groups; and all of it quick at the size of a real library."""

import time

import numpy as np
import pytest

from photofind.core import fileops, people
from photofind.core.db import to_blob


@pytest.fixture
def db(pf):
    db = pf.open_db()
    yield db
    db.close()


def unit(v):
    v = np.asarray(v, np.float32)
    return v / np.linalg.norm(v)


def look(n: int, rng, spread=0.25):
    """A face's embedding: someone's own direction (n) plus a little noise."""
    base = np.zeros(128, np.float32)
    base[n] = 1
    return unit(base + rng.normal(0, spread / np.sqrt(128), 128))


def add_faces(db, who: list[int], seed=0) -> list[int]:
    """One photo per face; who: which person each face looks like. Returns the face ids."""
    rng = np.random.default_rng(seed)
    ids = []
    for i, n in enumerate(who):
        pid = db.execute("INSERT INTO photos (path, qhash, taken) VALUES (?, ?, '2020-01-01 00:00:00')",
                         (f"/p/{seed}-{i}.jpg", f"q{seed}-{i}")).lastrowid
        ids.append(db.execute("INSERT INTO faces (photo_id, x, y, w, h, score, emb) VALUES (?, .1, .1, .2, .2, .99, ?)",
                              (pid, to_blob(look(n, rng)))).lastrowid)
    db.commit()
    return ids


def do(db, step, summary="t"):
    assert step is not None
    return fileops.run(db, "people", summary, [step])


def names(db):
    return dict(db.execute("SELECT f.id, pe.name FROM faces f LEFT JOIN people pe ON pe.id = f.person_id"))


# --- naming --------------------------------------------------------------------------------------

def test_naming_a_group_recognizes_the_rest_and_undo_takes_it_all_back(db):
    ann = add_faces(db, [1] * 6, seed=1)
    bob = add_faces(db, [2] * 3, seed=2)
    groups = people.who_groups(db)
    assert sorted(len(g) for g in groups) == [3, 6]
    do(db, people.plan_name(db, ann[:3], "Ann"))
    got = names(db)
    assert all(got[f] == "Ann" for f in ann)  # the other three were recognized
    assert all(got[f] is None for f in bob)
    assert [len(g) for g in people.who_groups(db)] == [3]
    fileops.undo(db)
    assert all(v is None for v in names(db).values())
    assert db.execute("SELECT COUNT(*) FROM people").fetchone()[0] == 0  # Ann was new: gone again
    fileops.redo(db)
    assert all(names(db)[f] == "Ann" for f in ann)


def test_look_alikes_in_a_group_sit_together(db):
    """Two people who look alike end up in one group (siblings, say). Their faces used to come in
    order of clearness, mixed together; now each one's faces come together, the clearest face first."""
    rng = np.random.default_rng(5)

    def sibling(n):
        v = np.zeros(128, np.float32)
        v[0], v[n] = 1, 1  # alike enough to be grouped, not to be the same person
        return unit(v + rng.normal(0, 0.1 / np.sqrt(128), 128))

    who = [1, 2] * 5
    for i, n in enumerate(who):
        pid = db.execute("INSERT INTO photos (path, qhash, taken) VALUES (?, ?, '2020-01-01 00:00:00')",
                         (f"/s/{i}.jpg", f"s{i}")).lastrowid
        db.execute("INSERT INTO faces (photo_id, x, y, w, h, score, emb) VALUES (?, .1, .1, ?, ?, .99, ?)",
                   (pid, .3 - i * .01, .3 - i * .01, to_blob(sibling(n))))
    db.commit()
    (group,) = people.who_groups(db)
    first = [who[f["photo_id"] - 1] for f in group]
    assert first == [1] * 5 + [2] * 5
    assert group[0]["photo_id"] == 1  # the clearest

    # Most like ticked: the ticked first, then the ones most like them
    ticked = [f for f in group if who[f["photo_id"] - 1] == 2][:1]
    order = people.by_likeness(group, ticked)
    assert order[0] is ticked[0] and [who[f["photo_id"] - 1] for f in order[1:5]] == [2] * 4


def test_naming_someone_known_adds_to_them(db):
    a = add_faces(db, [1, 1], seed=1)
    do(db, people.plan_name(db, a[:1], "Ann"))
    pid = people.person_id(db, "ann")  # names don't care about case
    do(db, people.plan_name(db, a[1:], "ANN"))
    assert {r[0] for r in db.execute("SELECT person_id FROM faces")} == {pid}
    assert db.execute("SELECT COUNT(*) FROM people").fetchone()[0] == 1


def test_that_isnt_them_and_not_a_face(db):
    a = add_faces(db, [1, 1, 1, 1], seed=1)
    do(db, people.plan_name(db, a[:2], "Ann"))
    assert set(names(db).values()) == {"Ann"}
    do(db, people.plan_not(db, a[3:]))  # wrong match
    do(db, people.plan_hide(db, a[2:3]))  # not a face
    got = names(db)
    assert got[a[3]] is None and got[a[2]] is None
    assert people.auto_assign(db, write=False) == []  # neither is recognized again
    assert all(a[2] not in [f["id"] for f in g] for g in people.who_groups(db, min_faces=1))  # never asked about
    assert any(a[3] in [f["id"] for f in g] for g in people.who_groups(db, min_faces=1))  # can still be named
    fileops.undo(db)
    fileops.undo(db)
    assert set(names(db).values()) == {"Ann"}


def test_moving_wrong_faces_to_someone_else(db):
    a = add_faces(db, [1, 1, 1, 2], seed=1)
    do(db, people.plan_name(db, a, "Ann"))  # one of them is really Bob
    order = [f["id"] for f in people.person_faces(db, people.person_id(db, "Ann"))]
    assert order[0] == a[3]  # the odd one out comes first
    do(db, people.plan_name(db, [a[3]], "Bob"))
    got = names(db)
    assert got[a[3]] == "Bob" and [got[f] for f in a[:3]] == ["Ann"] * 3


# --- people -------------------------------------------------------------------------------------

def test_rename_merge_and_forget(db):
    a = add_faces(db, [1, 1], seed=1)
    b = add_faces(db, [2, 2], seed=2)
    do(db, people.plan_name(db, a, "Ann"))
    do(db, people.plan_name(db, b, "Annie"))
    ann, annie = people.person_id(db, "Ann"), people.person_id(db, "Annie")
    do(db, people.plan_rename(db, ann, "Anna"))
    assert people.person_id(db, "Anna") == ann
    assert people.plan_rename(db, ann, "Anna") is None  # nothing to do
    do(db, people.plan_rename(db, annie, "anna"))  # someone else's name: they're one person
    assert set(names(db).values()) == {"Anna"} and db.execute("SELECT COUNT(*) FROM people").fetchone()[0] == 1
    fileops.undo(db)  # apart again, as they were
    assert people.person_id(db, "Annie") == annie and {names(db)[f] for f in b} == {"Annie"}
    do(db, people.plan_merge(db, [ann, annie], annie))
    assert set(names(db).values()) == {"Annie"}
    do(db, people.plan_forget(db, annie))
    assert set(names(db).values()) == {None} and db.execute("SELECT COUNT(*) FROM people").fetchone()[0] == 0
    fileops.undo(db)
    assert set(names(db).values()) == {"Annie"}


def test_a_crash_before_a_change_was_committed_leaves_nothing_half_done(db, pf):
    a = add_faces(db, [1, 1, 1], seed=1)
    step = people.plan_name(db, a, "Ann")
    db.execute("INSERT INTO ops (kind, summary, started, state) VALUES ('people', 't', 'now', 'running')")
    op = db.execute("SELECT MAX(id) FROM ops").fetchone()[0]
    db.execute("INSERT INTO op_steps (op_id, seq, action, before) VALUES (?, 0, 'people', ?)",
               (op, __import__("json").dumps(step["before"])))
    db.commit()  # planned, and then the power went
    fileops.recover(db)
    assert set(names(db).values()) == {None}
    assert fileops.can_undo(db) is None or fileops.can_undo(db)["id"] == op  # nothing it could take back
    assert db.execute("SELECT done FROM op_steps WHERE op_id=?", (op,)).fetchone()[0] == fileops.SKIPPED


# --- at the size of a real library ------------------------------------------------------------

def test_forty_thousand_faces(db):
    rng = np.random.default_rng(7)
    who = list(rng.integers(0, 120, 42_000))
    photo_rows = [(f"/p/{i}.jpg", f"q{i}") for i in range(len(who))]
    db.executemany("INSERT INTO photos (path, qhash, taken) VALUES (?, ?, '2020-01-01 00:00:00')", photo_rows)
    ids = [r[0] for r in db.execute("SELECT id FROM photos ORDER BY id")]
    db.executemany("INSERT INTO faces (photo_id, x, y, w, h, score, emb) VALUES (?, .1, .1, .2, .2, .99, ?)",
                   [(pid, to_blob(look(int(n), rng))) for pid, n in zip(ids, who, strict=True)])
    db.commit()
    faces = [r[0] for r in db.execute("SELECT id FROM faces ORDER BY id")]
    # most of them already named, as in a real library
    for n in range(100):
        mine = [f for f, w in zip(faces, who, strict=True) if w == n][:200]
        db.execute("INSERT INTO people (id, name) VALUES (?, ?)", (n + 1, f"Person {n}"))
        db.executemany("UPDATE faces SET person_id=? WHERE id=?", [(n + 1, f) for f in mine])
    db.commit()

    t = time.perf_counter()
    groups = people.who_groups(db)
    took = time.perf_counter() - t
    assert groups and took < 5, took
    t = time.perf_counter()
    step = people.plan_name(db, [f["id"] for f in groups[0][:5]], "Someone new")
    do(db, step)
    took = time.perf_counter() - t
    assert took < 3, took
    t = time.perf_counter()
    fileops.undo(db)
    assert time.perf_counter() - t < 3


# --- the command line ----------------------------------------------------------------------------

def test_the_command_line_names_through_the_journal(db, pf, capsys):
    from types import SimpleNamespace

    from photofind import cli

    a = add_faces(db, [1, 1, 1], seed=1)
    people.cluster_unnamed(db)  # numbers the groups, as `photofind people` shows them
    group = db.execute("SELECT cluster FROM faces WHERE id=?", (a[0],)).fetchone()[0]
    cli.cmd_name(SimpleNamespace(args=[str(group), "Ann", "Lee"]))
    assert set(names(db).values()) == {"Ann Lee"}
    cli.cmd_rename(SimpleNamespace(old="ann lee", new="Annie"))
    cli.cmd_unname(SimpleNamespace(face_ids=[a[2]]))
    assert names(db)[a[2]] is None and names(db)[a[0]] == "Annie"
    cli.cmd_forget(SimpleNamespace(name="Annie"))
    assert set(names(db).values()) == {None}
    fileops.undo(db)  # each one is an operation the app can take back
    assert [names(db)[f] for f in a] == ["Annie", "Annie", None]
    fileops.undo(db)
    fileops.undo(db)
    assert set(names(db).values()) == {"Ann Lee"}
    assert "Named 3 faces 'Ann Lee'" in capsys.readouterr().out
