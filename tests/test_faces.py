"""Naming people, fixing wrong matches, "not a face", renaming, merging and forgetting, all
undoable; the "who is this" groups; and all of it quick at the size of a real library."""

import sqlite3

import numpy as np
import pytest
from conftest import statements

from homepix.core import fileops, people
from homepix.core.db import to_blob


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
        pid = db.execute(
            "INSERT INTO photos (path, qhash, taken) VALUES (?, ?, '2020-01-01 00:00:00')",
            (f"/p/{seed}-{i}.jpg", f"q{seed}-{i}"),
        ).lastrowid
        ids.append(
            db.execute(
                "INSERT INTO faces (photo_id, x, y, w, h, score, emb) VALUES (?, .1, .1, .2, .2, .99, ?)",
                (pid, to_blob(look(n, rng))),
            ).lastrowid
        )
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


def test_a_group_shows_its_most_different_faces_first(db):
    """Two people who look alike end up in one group (siblings, say). The clearest face comes first,
    then the one least like it (the other sibling), and the near-copies after: the first faces show
    the range, which teaches recognizing the most."""
    rng = np.random.default_rng(5)

    def sibling(n):
        v = np.zeros(128, np.float32)
        v[0], v[n] = 1, 1  # alike enough to be grouped, not to be the same person
        return unit(v + rng.normal(0, 0.1 / np.sqrt(128), 128))

    who = [1, 2] * 5
    for i, n in enumerate(who):
        pid = db.execute(
            "INSERT INTO photos (path, qhash, taken) VALUES (?, ?, '2020-01-01 00:00:00')", (f"/s/{i}.jpg", f"s{i}")
        ).lastrowid
        db.execute(
            "INSERT INTO faces (photo_id, x, y, w, h, score, emb) VALUES (?, .1, .1, ?, ?, .99, ?)",
            (pid, 0.3 - i * 0.01, 0.3 - i * 0.01, to_blob(sibling(n))),
        )
    db.commit()
    (group,) = people.who_groups(db)
    first = [who[f["photo_id"] - 1] for f in group]
    assert first[:2] == [1, 2] and sorted(first) == [1] * 5 + [2] * 5
    assert group[0]["photo_id"] == 1  # the clearest

    # Most like ticked: the ticked first, then the ones most like them
    ticked = [f for f in group if who[f["photo_id"] - 1] == 2][:1]
    order = people.by_likeness(group, ticked)
    assert order[0] is ticked[0] and [who[f["photo_id"] - 1] for f in order[1:5]] == [2] * 4


def test_recognizing_compares_with_named_faces_only_and_small_ones_only_when_clear(db):
    """A face recognized by itself is never a reason to recognize another (one wrong match used to
    pull in more, each a bit further off); and a face too small to tell is recognized only when the
    match is clearer than a bigger face needs."""

    def face(v, px=None):
        pid = db.execute("INSERT INTO photos (path, qhash) VALUES (?, ?)", (f"/r/{face.n}.jpg", f"r{face.n}")).lastrowid
        face.n += 1
        return db.execute(
            "INSERT INTO faces (photo_id, x, y, w, h, score, emb, px) VALUES (?, .1, .1, .2, .2, .99, ?, ?)",
            (pid, to_blob(unit(v)), px),
        ).lastrowid

    face.n = 0
    e = np.eye(128, dtype=np.float32)
    named = [face(e[1] + 0.05 * e[k]) for k in (3, 4, 5)]
    near = face(e[1] + 1.2 * e[2], px=120)  # like the named ones (0.64): recognized
    further = face(e[2] + 0.5 * e[1], px=120)  # like `near`, not like the named ones
    small_unsure = face(e[1] + 1.2 * e[2], px=40)  # as alike as `near`, but a blur: not clear enough
    small_clear = face(e[1], px=40)  # a blur, but just like them
    db.commit()
    do(db, people.plan_name(db, named, "Ann"))
    got = names(db)
    assert got[near] == "Ann" and got[further] is None
    assert got[small_unsure] is None and got[small_clear] == "Ann"


def test_starting_faces_over_forgets_them_and_finds_them_again(db, pf):
    from homepix.core import config

    ann = add_faces(db, [1] * 4, seed=1)
    do(db, people.plan_name(db, ann[:2], "Ann"))
    do(db, people.plan_hide(db, ann[3:]))
    n, saved = people.reset_faces(db)
    assert n == 4 and saved.exists() and saved.parent == config.data_dir() / "backups"
    assert db.execute("SELECT COUNT(*) FROM faces").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM people").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM photos WHERE faces_done = 1").fetchone()[0] == 0
    assert fileops.can_undo(db) is None  # the naming is gone from the undo history too
    old = sqlite3.connect(saved)  # and the backup has it all
    assert old.execute("SELECT COUNT(*) FROM faces").fetchone()[0] == 4
    old.close()


def test_small_faces_are_read_from_a_bigger_decode(lib):
    """A face 40 pixels across in the 1024-pixel photo the detector looks at is read again from the
    photo decoded big enough for it to be 112 (the recognizer's size), its box and landmarks scaled
    to match; a big face isn't."""
    from PIL import Image

    from homepix.core.ai.faces import Faces

    Image.new("RGB", (4096, 3072), "gray").save(lib / "big.jpg")
    seen = []

    class Det:
        def setInputSize(self, size):
            pass

        def detect(self, img):
            small = [100, 100, 40, 40] + [110, 110, 130, 110, 120, 120, 112, 130, 128, 130] + [0.95]
            big = [500, 300, 200, 200] + [550, 350, 650, 350, 600, 400, 560, 450, 640, 450] + [0.99]
            return 1, np.array([small, big], np.float32)

    class Rec:
        def alignCrop(self, src, row):
            seen.append((src.shape[1], [float(v) for v in row[:4]]))
            return np.zeros((112, 112, 3), np.uint8)

        def feature(self, img):
            return np.ones((1, 128), np.float32)

    f = object.__new__(Faces)
    import cv2

    f.cv2, f.det, f.rec = cv2, Det(), Rec()
    img = Image.new("RGB", (1024, 768))
    found = f.find(img, lib / "big.jpg")
    k = seen[0][0] / 1024
    assert seen[0][0] > 2800 and seen[0][1] == pytest.approx([100 * k, 100 * k, 40 * k, 40 * k])  # the big one
    assert seen[1][0] == 1024 and seen[1][1] == [500, 300, 200, 200]
    assert [round(r[6]) for r in found] == [round(40 * k), 200] and 40 * k >= 112  # the pixels each was read from
    assert found[0][:4] == (100 / 1024, 100 / 768, 40 / 1024, 40 / 768)  # boxes as fractions, as before


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


def test_that_isnt_them_teaches_who_they_arent(db):
    """A look-alike (a sibling, say) is close enough to be recognized as Ann. Saying a couple of
    them aren't her unnames the rest of them too, and stops new ones being recognized as her, while
    Ann's own faces still are."""
    rng = np.random.default_rng(7)
    e = np.eye(128, dtype=np.float32)

    def faces(direction, n):
        out = []
        for _ in range(n):
            pid = db.execute(
                "INSERT INTO photos (path, qhash) VALUES (?, ?)", (f"/n/{faces.n}.jpg", f"n{faces.n}")
            ).lastrowid
            faces.n += 1
            v = unit(direction + rng.normal(0, 0.053, 128))
            out.append(
                db.execute(
                    "INSERT INTO faces (photo_id, x, y, w, h, score, emb) VALUES (?, .1, .1, .2, .2, .99, ?)",
                    (pid, to_blob(v)),
                ).lastrowid
            )
        db.commit()
        return out

    faces.n = 0
    ann_look, sister_look = e[0] + 0.6 * e[1], e[0] + 0.6 * e[2]  # each alike as 0.8, and to each other 0.58
    ann, sister = faces(ann_look, 8), faces(sister_look, 8)
    do(db, people.plan_name(db, ann[:3], "Ann"))
    got = names(db)
    assert all(got[f] == "Ann" for f in ann) and sum(got[f] == "Ann" for f in sister) >= 4  # mixed up
    wrong = [f for f in sister if got[f] == "Ann"]
    pid = people.person_id(db, "Ann")

    do(db, people.plan_not(db, wrong[:2]))
    got = names(db)
    assert all(got[f] is None for f in sister)  # the rest of her went too
    assert all(got[f] == "Ann" for f in ann)
    assert {r[0] for r in db.execute("SELECT id FROM faces WHERE not_person = ?", (pid,))} == set(wrong[:2])
    new_ann, new_sister = faces(ann_look, 3), faces(sister_look, 3)  # new photos come in
    people.auto_assign(db)
    got = names(db)
    assert all(got[f] == "Ann" for f in new_ann) and all(got[f] is None for f in new_sister)

    fileops.undo(db)  # the faces new photos brought aren't part of it: the marks and unnaming are
    assert db.execute("SELECT COUNT(*) FROM faces WHERE not_person IS NOT NULL").fetchone()[0] == 0
    assert [names(db)[f] for f in wrong] == ["Ann"] * len(wrong)


def test_naming_a_face_someone_else_and_forgetting_keep_the_marks_straight(db):
    a = add_faces(db, [1, 1, 1, 2], seed=1)
    do(db, people.plan_name(db, a, "Ann"))
    ann = people.person_id(db, "Ann")
    do(db, people.plan_name(db, [a[3]], "Bob"))  # it's Bob: so not Ann
    assert db.execute("SELECT not_person FROM faces WHERE id=?", (a[3],)).fetchone()[0] == ann
    do(db, people.plan_forget(db, ann))
    assert db.execute("SELECT not_person FROM faces WHERE id=?", (a[3],)).fetchone()[0] is None
    fileops.undo(db)  # Ann is back, and so is what was said about her
    assert db.execute("SELECT not_person FROM faces WHERE id=?", (a[3],)).fetchone()[0] == ann


def _face(db, v, px=None):
    """A face with this embedding, in a photo of its own."""
    n = db.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
    pid = db.execute("INSERT INTO photos (path, qhash) VALUES (?, ?)", (f"/w/{n}.jpg", f"w{n}")).lastrowid
    return db.execute(
        "INSERT INTO faces (photo_id, x, y, w, h, score, emb, px) VALUES (?, .1, .1, .2, .2, .99, ?, ?)",
        (pid, to_blob(unit(v)), px),
    ).lastrowid


def _people_and_others(db):
    """Ann and Bob named; unnamed: someone new (4 faces), 6 that might be Bob, and 3 small ones that
    are like Ann, but not clearly enough to be recognized (the background: never asked about)."""
    rng = np.random.default_rng(11)
    e = np.eye(128, dtype=np.float32)

    def some(v, n, px=None):
        return [_face(db, v + rng.normal(0, 0.02, 128), px) for _ in range(n)]

    ann, bob = some(e[1], 3), some(e[2], 3)
    new, maybe_bob, small_ann = some(e[3], 4), some(e[2] + 1.6 * e[5], 6), some(e[1] + 1.2 * e[6], 3, px=40)
    db.commit()
    do(db, people.plan_name(db, ann, "Ann"))
    do(db, people.plan_name(db, bob, "Bob"))
    return new, maybe_bob, small_ann


def test_who_is_this_asks_about_the_people_in_the_most_photos_first_and_not_the_background(db):
    new, maybe_bob, small_ann = _people_and_others(db)
    groups = people.who_groups(db)
    assert [{f["id"] for f in g} for g in groups] == [set(maybe_bob), set(new)]  # (no small ones)
    assert [(g[0]["tier"], g[0]["like"]) for g in groups] == [(people.MAYBE, "Bob"), (people.NEW, None)]
    assert all(names(db)[f] is None for f in small_ann)


def test_a_face_far_smaller_than_the_main_one_in_its_photo_is_background(db):
    """A stranger behind the people photographed, sharp enough to read: never asked about, but
    recognized if it clearly is someone."""
    e = np.eye(128, dtype=np.float32)
    ann = [_face(db, e[1] + 0.05 * e[k]) for k in (3, 4, 5)]
    do(db, people.plan_name(db, ann, "Ann"))
    photos = []
    for n in range(3):
        photos.append(db.execute("INSERT INTO photos (path, qhash) VALUES (?, ?)", (f"/b/{n}.jpg", f"b{n}")).lastrowid)

    def face(photo, v, w):
        return db.execute(
            "INSERT INTO faces (photo_id, x, y, w, h, score, emb, px) VALUES (?, .1, .1, ?, ?, .99, ?, 200)",
            (photo, w, w, to_blob(unit(v))),
        ).lastrowid

    front = [face(p, e[7] + 0.05 * e[8 + n], 0.4) for n, p in enumerate(photos)]
    behind = [face(p, e[9] + 0.05 * e[20 + n], 0.1) for n, p in enumerate(photos)]  # someone, three times
    behind_ann = face(photos[0], e[1], 0.1)
    db.commit()
    groups = [{f["id"] for f in g} for g in people.who_groups(db)]
    assert set(front) in groups and not any(set(behind) & g for g in groups)
    assert people.auto_assign(db) == 1 and names(db)[behind_ann] == "Ann"


def test_the_faces_that_might_be_someone_just_named(db):
    new, maybe_bob, small_ann = _people_and_others(db)
    assert people.follow_up(db, "Ann") == []  # (the small ones are background: never asked about)
    got = [f["id"] for f in people.follow_up(db, "Bob")]
    assert set(got) == set(maybe_bob)
    assert len(people.follow_up(db, "Bob", limit=4)) == 4
    assert people.follow_up(db, "Nobody") == []

    # Is this Bob? two are; the rest aren't (still unnamed, free to be anyone else)
    do(db, people.plan_confirm(db, maybe_bob[:2], maybe_bob[2:], "Bob"))
    got = names(db)
    assert [got[f] for f in maybe_bob] == ["Bob"] * 2 + [None] * 4
    bob = people.person_id(db, "Bob")
    rows = db.execute(f"SELECT locked, not_person FROM faces WHERE id IN ({','.join(map(str, maybe_bob[2:]))})")
    assert {tuple(r) for r in rows} == {(0, bob)}
    assert people.follow_up(db, "Bob") == []  # not asked about as Bob again
    people.auto_assign(db)
    assert all(names(db)[f] is None for f in maybe_bob[2:])  # nor recognized as him
    fileops.undo(db)
    assert all(names(db)[f] is None for f in maybe_bob)
    assert db.execute("SELECT COUNT(*) FROM faces WHERE not_person IS NOT NULL").fetchone()[0] == 0


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
    db.execute(
        "INSERT INTO op_steps (op_id, seq, action, before) VALUES (?, 0, 'people', ?)",
        (op, __import__("json").dumps(step["before"])),
    )
    db.commit()  # planned, and then the power went
    fileops.recover(db)
    assert set(names(db).values()) == {None}
    assert fileops.can_undo(db) is None  # nothing it could take back
    assert db.execute("SELECT done FROM op_steps WHERE op_id=?", (op,)).fetchone()[0] == fileops.SKIPPED


# --- at the size of a real library ------------------------------------------------------------


def test_forty_thousand_faces(db):
    rng = np.random.default_rng(7)
    who = list(rng.integers(0, 120, 42_000))
    photo_rows = [(f"/p/{i}.jpg", f"q{i}") for i in range(len(who))]
    db.executemany("INSERT INTO photos (path, qhash, taken) VALUES (?, ?, '2020-01-01 00:00:00')", photo_rows)
    ids = [r[0] for r in db.execute("SELECT id FROM photos ORDER BY id")]
    db.executemany(
        "INSERT INTO faces (photo_id, x, y, w, h, score, emb) VALUES (?, .1, .1, .2, .2, .99, ?)",
        [(pid, to_blob(look(int(n), rng))) for pid, n in zip(ids, who, strict=True)],
    )
    db.commit()
    faces = [r[0] for r in db.execute("SELECT id FROM faces ORDER BY id")]
    # most of them already named, as in a real library
    for n in range(100):
        mine = [f for f, w in zip(faces, who, strict=True) if w == n][:200]
        db.execute("INSERT INTO people (id, name) VALUES (?, ?)", (n + 1, f"Person {n}"))
        db.executemany("UPDATE faces SET person_id=? WHERE id=?", [(n + 1, f) for f in mine])
    db.commit()

    with statements(db) as grouping:
        groups = people.who_groups(db)
    with statements(db) as naming:
        step = people.plan_name(db, [f["id"] for f in groups[0][:5]], "Someone new")
        do(db, step)
    with statements(db) as undoing:
        fileops.undo(db)
    # (counted, not timed: the same on any machine. Naming and undoing touch the faces named, one row
    # each, never all 40,000; grouping reads them in one go.)
    assert groups and grouping.count <= 10
    assert naming.count < 2000 and undoing.count < 2000


def test_recognizing_again_only_when_faces_or_names_changed(db, monkeypatch):
    """A rescan that found nothing new skips recognizing (slow with many faces); any change to the
    faces or who they are makes it run again."""
    ran = []
    real = people.auto_assign
    monkeypatch.setattr(
        people, "auto_assign", lambda d, write=True, **k: (write and ran.append(1)) or real(d, write, **k)
    )
    monkeypatch.setattr(people, "_assigned", {})
    a = add_faces(db, [1, 1, 1, 2, 2], seed=1)
    do(db, people.plan_name(db, a[:2], "Ann"))

    people.auto_assign_if_changed(db)  # the first time in a run of the app, it always looks
    assert len(ran) == 1
    people.auto_assign_if_changed(db)  # nothing changed: skipped
    assert len(ran) == 1

    new = add_faces(db, [1], seed=2)  # a new photo of Ann
    assert people.auto_assign_if_changed(db) == 1 and len(ran) == 2
    assert names(db)[new[0]] == "Ann"

    do(db, people.plan_not(db, new))  # "that isn't her": the marks changed
    people.auto_assign_if_changed(db)
    assert len(ran) == 3 and names(db)[new[0]] is None
    people.auto_assign_if_changed(db)
    assert len(ran) == 3


def test_recognizing_stopped_partway_tags_nothing_and_runs_again(db, monkeypatch):
    """An action wants the library while recognizing is under way: it stops at once, tags no one, and
    isn't counted as done (so the next rescan does it)."""
    monkeypatch.setattr(people, "_assigned", {})
    a = add_faces(db, [1, 1, 1], seed=1)
    do(db, people.plan_name(db, a[:2], "Ann"))
    db.execute("UPDATE faces SET person_id = NULL WHERE id = ?", (a[2],))  # (naming recognized it: undo that)
    db.commit()
    assert people.auto_assign_if_changed(db, should_stop=lambda: True) == 0
    assert names(db)[a[2]] is None and people._assigned == {}
    assert people.auto_assign_if_changed(db) == 1 and names(db)[a[2]] == "Ann"


def test_the_follow_up_ends_with_the_least_sure_matches_and_the_rest_arent_unnames_them(db):
    """After the faces that might be Ann, the ones just recognized as her that are least like her:
    Enter leaves those not ticked as they are; "the rest aren't Ann" unnames them, and they're never
    recognized as her again."""
    rng = np.random.default_rng(4)
    e = np.eye(128, dtype=np.float32)
    ann = [_face(db, e[1] + rng.normal(0, 0.02, 128)) for _ in range(3)]
    sure = [_face(db, e[1] + rng.normal(0, 0.02, 128)) for _ in range(3)]
    unsure = _face(db, e[1] + 0.9 * e[4])  # recognized, but the least like her
    maybe = _face(db, e[1] + 1.6 * e[5])  # might be her
    db.commit()
    do(db, people.plan_name(db, ann, "Ann"))
    got = names(db)
    assert all(got[f] == "Ann" for f in [*sure, unsure]) and got[maybe] is None
    shown = [f["id"] for f in people.follow_up(db, "Ann", unsure=2)]
    assert shown[0] == maybe and shown[1] == unsure and len(shown) == 3  # (the maybes, then the 2 least sure)
    do(db, people.plan_confirm(db, [maybe], [unsure], "Ann"))
    got = names(db)
    assert got[maybe] == "Ann" and got[unsure] is None and all(got[f] == "Ann" for f in sure)
    people.auto_assign(db)
    assert names(db)[unsure] is None  # not recognized as her again
    fileops.undo(db)
    assert names(db)[unsure] == "Ann" and names(db)[maybe] is None
