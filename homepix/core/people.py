"""Faces to people: recognizing named people, grouping the unnamed, and the changes people make
(naming, "that isn't them", "not a face", renaming, merging, forgetting).

A change is planned as the faces' and people's values before and after it ({"faces": {face id:
[person id, locked, hidden]}, "people": {person id: name, or None when there's no such person}}),
and done as a step of an operation in the journal (core/fileops.py), so Undo puts the values back.
Naming someone recognizes them in more photos too: those faces are part of the same change.
"""

import numpy as np

from .config import (
    ASSIGN_MARGIN,
    ASSIGN_MIN_PX,
    ASSIGN_SIM,
    CLUSTER_SIM,
    FOLLOW_UP_FACES,
    MAYBE_SIM,
    SUBGROUP_SIM,
    db_file,
)
from .db import backup, from_blob
from .util import unit_vectors


def load_faces(db, where="1=1", params=()):
    rows = db.execute(
        f"SELECT f.id, f.photo_id, f.x, f.y, f.w, f.h, f.score, f.emb, f.person_id, f.locked, "
        f"f.not_person, p.path, p.qhash "
        f"FROM faces f JOIN photos p ON p.id = f.photo_id WHERE {where}",
        params,
    ).fetchall()
    embs = np.stack([from_blob(r["emb"]) for r in rows]) if rows else np.zeros((0, 128), np.float32)
    return rows, unit_vectors(embs)


RECOGNIZE_K = 3  # a face is compared with the 3 most like it of someone's named faces


def reference_faces(db):
    """The faces someone named (not those recognized by themselves): what recognizing compares
    with, so a wrong match is never a reason for more. Returns their people ids and embeddings."""
    rows, embs = load_faces(db, "f.person_id IS NOT NULL AND f.locked = 1 AND f.hidden = 0")
    return np.array([r["person_id"] for r in rows], dtype=np.int64), embs


def likeness_to(embs, owners, refs, people):
    """How alike each face is to each of people: on average, to the RECOGNIZE_K most like it of the
    faces they were named on (from references). A faces × people array."""
    sims = np.empty((len(embs), len(people)), np.float32)
    cols = [np.flatnonzero(owners == p) for p in people]
    for start in range(0, len(embs), 2048):  # (a block at a time: faces × named faces can be big)
        block = embs[start : start + 2048] @ refs.T
        for j, c in enumerate(cols):
            k = min(RECOGNIZE_K, len(c))
            sims[start : start + 2048, j] = -np.partition(-block[:, c], k - 1, axis=1)[:, :k].mean(axis=1)
    return sims


NOT_THEM_OTHERS = 200  # the other people's named faces most like someone, as more examples of not them
_models = {}  # person id -> (what it learned from, model): learning again only when that changes


def _train_classifier(pos, *negs, l2=1e-3, steps=15):
    """A line between face prints that are someone (pos) and ones that aren't (negs, sets that each
    weigh the same, and all of them as much as pos): logistic regression, (w, b), where e @ w + b > 0
    is the pos side. Newton's method: a few steps, no tuning."""
    negs = [n for n in negs if len(n)]
    neg = np.vstack(negs)
    x = np.hstack([np.vstack([pos, neg]), np.ones((len(pos) + len(neg), 1), np.float32)]).astype(np.float64)
    y = np.r_[np.ones(len(pos)), np.zeros(len(neg))]
    weight = np.r_[np.full(len(pos), 0.5 / len(pos)), *(np.full(len(n), 0.5 / len(negs) / len(n)) for n in negs)]
    ridge = l2 * np.eye(x.shape[1])
    ridge[-1, -1] = 0  # (not the bias)
    w = np.zeros(x.shape[1])
    for _ in range(steps):
        p = 1 / (1 + np.exp(-np.clip(x @ w, -30, 30)))
        grad = x.T @ (weight * (p - y)) + ridge @ w
        hess = (x * (weight * p * (1 - p))[:, None]).T @ x + ridge
        w -= np.linalg.solve(hess, grad)
    return w[:-1].astype(np.float32), np.float32(w[-1])


def _not_them_model(db, pid, owners, refs):
    """What someone's "that isn't them" marks taught: a line between the faces they were named on and
    the faces they aren't (those marks, and the other people's named faces most like them). None
    until there's a mark (until then the matching rules alone decide)."""
    nots = db.execute("SELECT id, emb FROM faces WHERE not_person = ? ORDER BY id", (pid,)).fetchall()
    pos = refs[owners == pid]
    if not nots or len(pos) < 2:
        return None
    others = refs[owners != pid]
    if len(others):
        likeness = others @ unit_vectors(pos.mean(axis=0))
        others = others[np.argsort(-likeness)[:NOT_THEM_OTHERS]]
    key = (len(pos), pos.sum(axis=0).tobytes(), tuple(r[0] for r in nots), len(others))
    if pid not in _models or _models[pid][0] != key:
        marks = unit_vectors(np.stack([from_blob(r[1]) for r in nots]))
        _models[pid] = (key, _train_classifier(pos, marks, others))
    return _models[pid][1]


def _doubtful(db, pid):
    """The faces recognized as someone (not named by hand) that their "that isn't them" marks say
    aren't them."""
    owners, refs = reference_faces(db)
    model = _not_them_model(db, pid, owners, refs)
    if model is None:
        return []
    rows, embs = load_faces(db, "f.person_id = ? AND f.locked = 0", (pid,))
    return [r["id"] for r, s in zip(rows, embs @ model[0] + model[1], strict=True) if s < 0]


_assigned = {}  # database file -> faces_state() after auto_assign last ran on it (in this run of the app)


def faces_state(db) -> tuple:
    """Changes whenever a face is added or removed, or who it is, its lock, "not a face" or "that isn't them"
    changes: everything recognizing depends on."""
    return tuple(
        db.execute(
            "SELECT COUNT(*), MAX(id), TOTAL(id * COALESCE(person_id, 0)), TOTAL(id * locked), TOTAL(id * hidden), "
            "TOTAL(id * COALESCE(not_person, 0)) FROM faces"
        ).fetchone()
    )


def auto_assign_if_changed(db) -> int:
    """auto_assign, skipped when no face or name changed since it last ran: with many faces it's the
    slowest part of a rescan that found nothing new, and gives the same answer again."""
    key = str(db_file())
    if _assigned.get(key) == faces_state(db):
        return 0
    tagged = auto_assign(db)
    _assigned[key] = faces_state(db)
    return tagged


def auto_assign(db, write: bool = True):
    """Tag unnamed faces that clearly match someone already named: on average as alike as
    ASSIGN_SIM to the RECOGNIZE_K most alike of the faces they were named on, ASSIGN_MARGIN more than
    to anyone else, big enough to tell (ASSIGN_MIN_PX), and not on the wrong side of what their
    "that isn't them" marks taught (_not_them_model). Returns how many; with write=False, the
    [(person id, face id)] it would tag instead, changing nothing."""
    owners, refs = reference_faces(db)
    people = sorted(set(owners.tolist()))
    rows, embs = (
        load_faces(
            db,
            "f.person_id IS NULL AND f.locked = 0 AND f.hidden = 0 AND (f.px IS NULL OR f.px >= ?)",
            (ASSIGN_MIN_PX,),
        )
        if people
        else ([], None)
    )
    if not rows:
        return 0 if write else []
    sims = likeness_to(embs, owners, refs, people)
    order = np.argsort(-sims, axis=1)
    best = sims[np.arange(len(rows)), order[:, 0]]
    second = sims[np.arange(len(rows)), order[:, 1]] if len(people) > 1 else np.full(len(rows), -1.0)
    ok = (best >= ASSIGN_SIM) & (best - second >= ASSIGN_MARGIN)
    ok &= np.array([r["not_person"] != people[j] for r, j in zip(rows, order[:, 0], strict=True)])
    for j in set(order[ok, 0].tolist()):  # what "that isn't them" taught can still say no
        model = _not_them_model(db, people[j], owners, refs)
        if model is not None:
            mine = np.flatnonzero(ok & (order[:, 0] == j))
            ok[mine[embs[mine] @ model[0] + model[1] < 0]] = False
    taken = {
        (r["photo_id"], r["person_id"])
        for r in db.execute("SELECT photo_id, person_id FROM faces WHERE person_id IS NOT NULL")
    }
    updates = []
    for i in sorted(range(len(rows)), key=lambda i: -best[i]):  # most confident first
        pid = people[order[i, 0]]
        key = (rows[i]["photo_id"], pid)
        if ok[i] and key not in taken:
            taken.add(key)  # one person can't appear twice in the same photo
            updates.append((pid, rows[i]["id"]))
    if not write:
        return updates
    db.executemany("UPDATE faces SET person_id=? WHERE id=?", updates)
    db.commit()
    return len(updates)


def reset_faces(db):
    """Forget every face and everyone named, so the next indexing finds the faces again from scratch
    (search and everything else stay). The naming in the undo history goes too: the faces it names
    are gone. Not undoable, so the database is backed up first. Returns (faces forgotten, backup)."""
    saved = backup(db, db.execute("PRAGMA user_version").fetchone()[0], "before-faces-reset")
    n = db.execute("SELECT COUNT(*) FROM faces").fetchone()[0]
    db.execute("""WITH RECURSIVE gone(id) AS (SELECT id FROM ops WHERE kind = 'people'
                  UNION SELECT o.id FROM ops o JOIN gone ON o.undoes = gone.id)
                  UPDATE ops SET state = 'forgotten' WHERE id IN (SELECT id FROM gone)""")
    db.execute("DELETE FROM faces")
    db.execute("DELETE FROM people")
    db.execute("UPDATE photos SET faces_done = 0")
    db.commit()
    _portraits.clear()
    _models.clear()
    return n, saved


def cluster_unnamed(db):
    """Group unnamed faces that look like the same person, biggest group first."""
    rows, embs = load_faces(db, "f.person_id IS NULL AND f.hidden = 0")
    if not rows:
        return []
    quality = np.array([r["score"] * min(r["w"], r["h"]) for r in rows])
    cents = np.empty_like(embs)  # each group's centroid (there are never more groups than faces)
    sums = np.empty_like(embs)  # running sum of each group's faces: unit_vectors(sum) == unit_vectors(mean)
    members = []
    groups_in = {}  # photo id -> groups that already have a face from that photo
    for i in np.argsort(-quality):  # clearest faces first, so groups start from good examples
        e, photo, n = embs[i], rows[i]["photo_id"], len(members)
        if n:
            sims = cents[:n] @ e
            sims[groups_in.get(photo, [])] = -1  # one person can't appear twice in the same photo
            j = int(np.argmax(sims))
            if sims[j] >= CLUSTER_SIM:
                members[j].append(i)
                groups_in.setdefault(photo, []).append(j)
                sums[j] += e
                cents[j] = unit_vectors(sums[j])
                continue
        cents[n] = sums[n] = e
        members.append([i])
        groups_in.setdefault(photo, []).append(n)
    members.sort(key=len, reverse=True)
    return [[rows[i] for i in group] for group in members]


_portraits = {}  # (database, person id) -> (their face count, chosen face): chosen again when it changes


def portrait(db, person_id: int, n_faces: int):
    """The face that best shows someone: of their faces at a usual size, the one most like all of them
    (an average face of theirs, rather than an odd angle or a screen-filling video call)."""
    key = (db.execute("PRAGMA database_list").fetchone()[2], person_id)
    hit = _portraits.get(key)
    if hit and hit[0] == n_faces:
        return hit[1]
    rows, embs = load_faces(db, "f.person_id = ? AND p.error IS NULL AND p.qhash IS NOT NULL", (person_id,))
    if not rows:
        return None
    sims = embs @ unit_vectors(embs.mean(axis=0))
    usual = np.array([0.06 <= r["w"] <= 0.4 and r["score"] >= 0.9 for r in rows])
    if usual.any():
        sims = np.where(usual, sims, -2.0)
    face = rows[int(np.argmax(sims))]
    _portraits[key] = (n_faces, face)
    return face


def named_people(db) -> list[dict]:
    """Everyone named, most photographed first: id, name, photos, and a portrait (a face row for
    face_thumb, or None)."""
    people = [
        dict(r)
        for r in db.execute(
            "SELECT pe.id, pe.name, COUNT(DISTINCT f.photo_id) AS photos, COUNT(f.id) AS faces FROM people pe "
            "JOIN faces f ON f.person_id = pe.id JOIN photos p ON p.id = f.photo_id "
            "WHERE p.error IS NULL AND p.qhash IS NOT NULL GROUP BY pe.id ORDER BY photos DESC, pe.name"
        )
    ]
    for person in people:
        person["face"] = portrait(db, person["id"], person.pop("faces"))
    return people


# --- changes -----------------------------------------------------------------------------------------


def _faces_now(db, face_ids) -> dict:
    marks = ",".join("?" * len(face_ids))
    return {
        str(r[0]): [r[1], r[2] or 0, r[3], r[4]]
        for r in db.execute(
            f"SELECT id, person_id, locked, hidden, not_person FROM faces WHERE id IN ({marks})", list(face_ids)
        )
    }


def _plan_change(
    db, faces: dict, people: dict | None = None, recognize: bool = False, learn: bool = False, teach=()
) -> dict | None:
    """The step that sets these faces ({face id: [person id, locked, hidden, not person]}, the last
    left out to keep it) and people ({person id: name or None}); recognize: and tags the faces that
    naming makes recognizable; learn: a face taken from someone is remembered as not them, and the
    faces recognized as them that it then says aren't are unnamed. None if it changes nothing."""
    people = {str(k): v for k, v in (people or {}).items()}
    faces = {str(k): list(v) for k, v in faces.items()}
    taught = set(teach)  # people that faces were just marked as not being
    if learn and faces:
        taught |= _remember_who_it_isnt(db, faces)
    if recognize or taught:
        _add_knock_on_changes(db, faces, people, taught, recognize)
    return _as_step(db, faces, people)


def _remember_who_it_isnt(db, faces: dict) -> set:
    """Faces taken from someone are remembered as not them (added to faces). Returns who they were."""
    taught = set()
    for k, (was, *_rest) in _faces_now(db, faces).items():
        v = faces[k]
        if len(v) == 3 and was is not None and was != v[0]:
            v.append(was)
            taught.add(was)
    return taught


def _add_knock_on_changes(db, faces: dict, people: dict, taught, recognize: bool):
    """Add to faces what follows from the change: faces recognized as the taught people that may not be
    them are unnamed; recognize: faces the naming makes recognizable are named. Works it out by
    making the change and taking it back."""
    db.execute("SAVEPOINT plan")
    try:
        apply(db, {"faces": faces, "people": people})
        for pid in taught:
            faces.update({str(fid): [None, 0, 0] for fid in _doubtful(db, pid)})
        if recognize:
            faces.update({str(fid): [pid, 0, 0] for pid, fid in auto_assign(db, write=False)})
    finally:
        db.execute("ROLLBACK TO plan")
        db.execute("RELEASE plan")


def _as_step(db, faces: dict, people: dict) -> dict | None:
    """The step for what actually changes, with what it was before (to undo it). None if nothing does."""
    old_faces = _faces_now(db, faces) if faces else {}
    for k, v in faces.items():
        if len(v) == 3 and k in old_faces:
            v.append(old_faces[k][3])
    faces = {k: v for k, v in faces.items() if k in old_faces and old_faces[k] != v}
    old_people = {
        k: (r[0] if (r := db.execute("SELECT name FROM people WHERE id=?", (int(k),)).fetchone()) else None)
        for k in people
    }
    people = {k: v for k, v in people.items() if old_people[k] != v}
    if not faces and not people:
        return None
    old = {"faces": {k: old_faces[k] for k in faces}, "people": {k: old_people[k] for k in people}}
    return {
        "action": "people",
        "src": None,
        "dst": None,
        "before": {"old": old, "new": {"faces": faces, "people": people}},
    }


def apply(db, state: dict):
    """Set the values a change (or its undoing) leaves: people first, so faces can point at them, and
    those that go last, once no face does."""
    for pid, name in state.get("people", {}).items():
        if name is not None:
            db.execute(
                "INSERT INTO people (id, name) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET name=excluded.name",
                (int(pid), name),
            )
    faces = state.get("faces", {}).items()
    db.executemany(
        "UPDATE faces SET person_id=?, locked=?, hidden=? WHERE id=?",
        [(v[0], v[1], v[2], int(fid)) for fid, v in faces],
    )
    db.executemany(
        "UPDATE faces SET not_person=? WHERE id=?",  # (steps from before it was kept have none)
        [(v[3], int(fid)) for fid, v in faces if len(v) > 3],
    )
    for pid, name in state.get("people", {}).items():
        if name is None:
            db.execute("DELETE FROM people WHERE id=?", (int(pid),))


def person_id(db, name: str):
    r = db.execute("SELECT id FROM people WHERE name=?", (name.strip(),)).fetchone()
    return r[0] if r else None


def _new_person(db, name: str) -> tuple[int, dict]:
    """(their id, the people part of a change): an existing person's, or one made for this name."""
    pid = person_id(db, name)
    if pid is not None:
        return pid, {}
    pid = (db.execute("SELECT MAX(id) FROM people").fetchone()[0] or 0) + 1
    return pid, {pid: name.strip()}


def plan_name(db, face_ids, name: str):
    """These faces are name (someone known, or someone new). Faces it then recognizes are named too."""
    pid, people = _new_person(db, name)
    return _plan_change(db, {f: [pid, 1, 0] for f in face_ids}, people, recognize=True, learn=True)


def plan_not(db, face_ids):
    """ "That isn't them": unnamed, never recognized again (it can still be named), and remembered as
    not them, so faces like it are less likely to be recognized as them."""
    return _plan_change(db, {f: [None, 1, 0] for f in face_ids}, learn=True)


def plan_confirm(db, yes, no, name: str):
    """After naming someone, of the faces that might be them: yes are them, no aren't (still unnamed
    and free to be anyone else, but remembered as not them, to learn from). One change."""
    pid, people = _new_person(db, name)
    now = _faces_now(db, no) if no else {}
    faces = {**{f: [*now[str(f)][:3], pid] for f in no if str(f) in now}, **{f: [pid, 1, 0] for f in yes}}
    return _plan_change(db, faces, people, recognize=True, learn=True, teach={pid} if no else ())


def plan_hide(db, face_ids):
    """Not a face, or no one to name: never asked about or recognized again."""
    return _plan_change(db, {f: [None, 1, 1] for f in face_ids})


def plan_rename(db, pid: int, name: str):
    """A new name; the name of someone else merges the two."""
    other = person_id(db, name)
    if other is not None and other != pid:
        return plan_merge(db, [pid], other)
    return _plan_change(db, {}, {pid: name.strip()})


def plan_merge(db, pids, into: int):
    """Everyone in pids is the person into."""
    pids = [p for p in pids if p != into]
    marks = ",".join("?" * len(pids))
    faces = (
        {
            r[0]: [into, r[1] or 0, 0]
            for r in db.execute(f"SELECT id, locked FROM faces WHERE person_id IN ({marks})", pids)
        }
        if pids
        else {}
    )
    for r in db.execute(f"SELECT id, person_id, locked, hidden FROM faces WHERE not_person IN ({marks})", pids):
        person, locked, hidden = faces.get(r[0], [r[1], r[2] or 0, r[3]])[:3]
        faces[r[0]] = [person, locked, hidden, None if person == into else into]  # not them: not into either
    return _plan_change(db, faces, dict.fromkeys(pids), recognize=True)


def plan_forget(db, pid: int):
    """No such person: their faces are unnamed again (and may be recognized as someone else)."""
    faces = {r[0]: [None, 0, 0] for r in db.execute("SELECT id FROM faces WHERE person_id=?", (pid,))}
    for r in db.execute("SELECT id, person_id, locked, hidden FROM faces WHERE not_person=?", (pid,)):
        faces[r[0]] = [*faces.get(r[0], [r[1], r[2] or 0, r[3]])[:3], None]  # (so Undo brings the marks back)
    return _plan_change(db, faces, {pid: None})


# --- for the app -----------------------------------------------------------------------------------


def face_rows(db, where: str, params=()) -> list[dict]:
    """Faces to show, with what face_thumb needs."""
    return [
        dict(r)
        for r in db.execute(
            "SELECT f.id, f.photo_id, f.x, f.y, f.w, f.h, f.score, f.person_id, pe.name, p.path, p.qhash FROM faces f "
            f"JOIN photos p ON p.id = f.photo_id LEFT JOIN people pe ON pe.id = f.person_id WHERE ({where}) "
            "AND p.error IS NULL AND p.qhash IS NOT NULL",
            params,
        )
    ]


NEW, MAYBE, KNOWN = 0, 1, 2  # how much a group looks like someone named (its faces' "tier")
_KEEP = ("id", "photo_id", "x", "y", "w", "h", "score", "path", "qhash")


def who_groups(db, min_faces: int = 2) -> list[list[dict]]:
    """Unnamed faces in groups of look-alikes: who to ask about. Those least like anyone named come
    first (NEW: someone not named yet), then those that might be someone named (MAYBE), then those
    that look like them (KNOWN); within each, the biggest and clearest first. In each group the
    clearest face comes first and the closest look-alikes sit together (see likeness_order). Each
    face has its "emb" (normalized), to sort by, and its group's "tier" and "like": the name of who
    it looks like (None for NEW). Changes nothing (about a second for 40,000 faces)."""
    groups = [g for g in cluster_unnamed(db) if len(g) >= min_faces]
    owners, refs = reference_faces(db)
    people = sorted(set(owners.tolist()))
    names = dict(db.execute("SELECT id, name FROM people"))
    embs_of = [unit_vectors(np.stack([from_blob(r["emb"]) for r in g])) for g in groups]
    if people and groups:  # (all at once: quicker than group by group)
        every = likeness_to(np.vstack(embs_of), owners, refs, people)
        ends = np.cumsum([len(g) for g in groups])
    out = []
    for n, (g, embs) in enumerate(zip(groups, embs_of, strict=True)):
        quality = np.array([r["score"] * min(r["w"], r["h"]) for r in g])
        tier, like = NEW, None
        if people:
            alike = every[ends[n] - len(g) : ends[n]].mean(axis=0)  # the group, to each person
            j = int(np.argmax(alike))
            if alike[j] >= MAYBE_SIM:
                tier, like = (KNOWN if alike[j] >= ASSIGN_SIM else MAYBE), names.get(people[j])
        faces = [
            {**{k: g[i][k] for k in _KEEP}, "emb": embs[i], "tier": tier, "like": like}
            for i in likeness_order(embs, quality)
        ]
        out.append((tier, -len(g), -float(quality.mean()), faces))
    out.sort(key=lambda t: t[:3])
    return [faces for *_, faces in out]


def follow_up(db, name: str, limit: int = FOLLOW_UP_FACES) -> list[dict]:
    """Right after naming someone: the unnamed faces that might be them (at least MAYBE_SIM alike,
    not yet enough to be recognized, or held back), from all the groups, most alike first. None
    that were said not to be them. Each with its "emb", as in who_groups."""
    pid = person_id(db, name)
    owners, refs = reference_faces(db)
    if pid is None or not (owners == pid).any():
        return []
    rows, embs = load_faces(db, "f.person_id IS NULL AND f.hidden = 0 AND f.not_person IS NOT ?", (pid,))
    if not rows:
        return []
    alike = likeness_to(embs, owners, refs, [pid])[:, 0]
    best = [i for i in np.argsort(-alike, kind="stable")[:limit] if alike[i] >= MAYBE_SIM]
    return [{**{k: rows[i][k] for k in _KEEP}, "emb": embs[i]} for i in best]


def likeness_order(embs, quality) -> list[int]:
    """An order for one group's faces (normalized embeddings) in which look-alikes sit together: the
    faces split into tighter sets (each started by its clearest face, which leads it), the set of the
    clearest face first, then each time the set most like the one before; in a set, after its first
    face, the ones most like the set first."""
    n = len(embs)
    cents, sums, members = np.empty_like(embs), np.empty_like(embs), []
    for i in np.argsort(-quality, kind="stable"):
        k = len(members)
        if k:
            sims = cents[:k] @ embs[i]
            j = int(np.argmax(sims))
            if sims[j] >= SUBGROUP_SIM:
                members[j].append(i)
                sums[j] += embs[i]
                cents[j] = unit_vectors(sums[j])
                continue
        cents[k] = sums[k] = embs[i]
        members.append([i])
    k = len(members)
    cents = cents[:k]
    order, left, last = [], np.ones(k, bool), 0
    for _ in range(k):
        left[last] = False
        head, rest = members[last][0], members[last][1:]
        order += [head] + sorted(rest, key=lambda i: -float(embs[i] @ cents[last]))
        if not left.any():
            break
        sims = np.where(left, cents @ cents[last], -2.0)
        last = int(np.argmax(sims))
    assert len(order) == n
    return [int(i) for i in order]


def by_likeness(faces: list[dict], to: list[dict]) -> list[dict]:
    """faces (with "emb"s) reordered: those in `to` first, as they were, then the rest most like
    them first."""
    chosen = {f["id"] for f in to}
    if not to:
        return list(faces)
    cent = unit_vectors(np.mean([f["emb"] for f in to], axis=0))
    rest = sorted((f for f in faces if f["id"] not in chosen), key=lambda f: -float(f["emb"] @ cent))
    return [f for f in faces if f["id"] in chosen] + rest


def person_faces(db, pid: int) -> list[dict]:
    """Someone's faces, least like the rest of theirs first: where wrong matches are. Each has its
    "emb" (normalized), to sort by."""
    rows = {r["id"]: r for r in face_rows(db, "f.person_id = ?", (pid,))}
    found, vecs = load_faces(db, "f.person_id = ?", (pid,))
    sims = vecs @ unit_vectors(vecs.mean(axis=0)) if len(found) else np.zeros(0)
    order = np.argsort(sims, kind="stable")
    return [{**rows[found[i]["id"]], "emb": vecs[i]} for i in order if found[i]["id"] in rows]
