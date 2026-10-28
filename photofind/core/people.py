"""Faces to people: recognizing named people, grouping the unnamed, and the changes people make
(naming, "that isn't them", "not a face", renaming, merging, forgetting).

A change is planned as the faces' and people's values before and after it ({"faces": {face id:
[person id, locked, hidden]}, "people": {person id: name, or None when there's no such person}}),
and done as a step of an operation in the journal (core/fileops.py), so Undo puts the values back.
Naming someone recognizes them in more photos too: those faces are part of the same change.
"""


import numpy as np

from .config import ASSIGN_MARGIN, ASSIGN_SIM, CLUSTER_SIM, SUBGROUP_SIM
from .db import from_blob
from .util import normalize


def load_faces(db, where="1=1", params=()):
    rows = db.execute(
        f"SELECT f.id, f.photo_id, f.x, f.y, f.w, f.h, f.score, f.emb, f.cluster, f.person_id, f.locked, p.path, p.qhash "
        f"FROM faces f JOIN photos p ON p.id = f.photo_id WHERE {where}",
        params,
    ).fetchall()
    embs = np.stack([from_blob(r["emb"]) for r in rows]) if rows else np.zeros((0, 128), np.float32)
    return rows, normalize(embs)


def person_centroids(db):
    rows, embs = load_faces(db, "f.person_id IS NOT NULL")
    if not rows:
        return [], np.zeros((0, 128), np.float32)
    ids = np.array([r["person_id"] for r in rows])
    people = sorted(set(ids.tolist()))
    cents = normalize(np.stack([embs[ids == pid].mean(axis=0) for pid in people]))
    return people, cents


def auto_assign(db, write: bool = True):
    """Tag unnamed faces that clearly match someone already named. Returns how many; with
    write=False, the [(person id, face id)] it would tag instead, changing nothing."""
    people, cents = person_centroids(db)
    rows, embs = load_faces(db, "f.person_id IS NULL AND f.locked = 0 AND f.hidden = 0") if people else ([], None)
    if not rows:
        return 0 if write else []
    sims = embs @ cents.T
    order = np.argsort(-sims, axis=1)
    best = sims[np.arange(len(rows)), order[:, 0]]
    second = sims[np.arange(len(rows)), order[:, 1]] if len(people) > 1 else np.full(len(rows), -1.0)
    taken = {
        (r["photo_id"], r["person_id"])
        for r in db.execute("SELECT photo_id, person_id FROM faces WHERE person_id IS NOT NULL")
    }
    updates = []
    for i in sorted(range(len(rows)), key=lambda i: -best[i]):  # most confident first
        pid = people[order[i, 0]]
        key = (rows[i]["photo_id"], pid)
        if best[i] >= ASSIGN_SIM and best[i] - second[i] >= ASSIGN_MARGIN and key not in taken:
            taken.add(key)  # one person can't appear twice in the same photo
            updates.append((pid, rows[i]["id"]))
    if not write:
        return updates
    db.executemany("UPDATE faces SET person_id=?, cluster=NULL WHERE id=?", updates)
    db.commit()
    return len(updates)


def cluster_unnamed(db, save: bool = True):
    """Group unnamed faces that look like the same person. Group 1 is the biggest. save: keep the
    group numbers in the index (the command line names groups by them)."""
    rows, embs = load_faces(db, "f.person_id IS NULL AND f.hidden = 0")
    if not rows:
        return []
    quality = np.array([r["score"] * min(r["w"], r["h"]) for r in rows])
    cents = np.empty_like(embs)  # each group's centroid (there are never more groups than faces)
    sums = np.empty_like(embs)   # running sum of each group's faces: normalize(sum) == normalize(mean)
    members = []
    groups_in = {}               # photo id -> groups that already have a face from that photo
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
                cents[j] = normalize(sums[j])
                continue
        cents[n] = sums[n] = e
        members.append([i])
        groups_in.setdefault(photo, []).append(n)
    members.sort(key=len, reverse=True)
    if save:
        updates = []
        for num, group in enumerate(members, start=1):
            updates += [(num, rows[i]["id"]) for i in group]
        db.executemany("UPDATE faces SET cluster=? WHERE id=?", updates)
        db.commit()
    return [(num, [rows[i] for i in group]) for num, group in enumerate(members, start=1)]


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
    sims = embs @ normalize(embs.mean(axis=0))
    usual = np.array([0.06 <= r["w"] <= 0.4 and r["score"] >= 0.9 for r in rows])
    if usual.any():
        sims = np.where(usual, sims, -2.0)
    face = rows[int(np.argmax(sims))]
    _portraits[key] = (n_faces, face)
    return face


def named_people(db) -> list[dict]:
    """Everyone named, most photographed first: id, name, photos, and a portrait (a face row for
    face_thumb, or None)."""
    people = [dict(r) for r in db.execute(
        "SELECT pe.id, pe.name, COUNT(DISTINCT f.photo_id) AS photos, COUNT(f.id) AS faces FROM people pe "
        "JOIN faces f ON f.person_id = pe.id JOIN photos p ON p.id = f.photo_id "
        "WHERE p.error IS NULL AND p.qhash IS NOT NULL GROUP BY pe.id ORDER BY photos DESC, pe.name"
    )]
    for person in people:
        person["face"] = portrait(db, person["id"], person.pop("faces"))
    return people


# --- changes -----------------------------------------------------------------------------------------

def _faces_now(db, face_ids) -> dict:
    marks = ",".join("?" * len(face_ids))
    return {str(r[0]): [r[1], r[2] or 0, r[3]] for r in db.execute(
        f"SELECT id, person_id, locked, hidden FROM faces WHERE id IN ({marks})", list(face_ids))}


def _change(db, faces: dict, people: dict | None = None, recognize: bool = False) -> dict | None:
    """The step that sets these faces ({face id: [person id, locked, hidden]}) and people ({person id:
    name or None}); recognize: and tags the faces that naming makes recognizable. None if it changes
    nothing."""
    people = {str(k): v for k, v in (people or {}).items()}
    faces = {str(k): v for k, v in faces.items()}
    if recognize:  # see what auto-tagging would do once this is done, without doing either
        db.execute("SAVEPOINT plan")
        try:
            apply(db, {"faces": faces, "people": people})
            faces.update({str(fid): [pid, 0, 0] for pid, fid in auto_assign(db, write=False)})
        finally:
            db.execute("ROLLBACK TO plan")
            db.execute("RELEASE plan")
    old_faces = _faces_now(db, faces) if faces else {}
    faces = {k: v for k, v in faces.items() if k in old_faces and old_faces[k] != v}
    old_people = {k: (r[0] if (r := db.execute("SELECT name FROM people WHERE id=?", (int(k),)).fetchone()) else None)
                  for k in people}
    people = {k: v for k, v in people.items() if old_people[k] != v}
    if not faces and not people:
        return None
    old = {"faces": {k: old_faces[k] for k in faces}, "people": {k: old_people[k] for k in people}}
    return {"action": "people", "src": None, "dst": None, "before": {"old": old, "new": {"faces": faces, "people": people}}}


def apply(db, state: dict):
    """Set the values a change (or its undoing) leaves: people first, so faces can point at them, and
    those that go last, once no face does."""
    for pid, name in state.get("people", {}).items():
        if name is not None:
            db.execute("INSERT INTO people (id, name) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET name=excluded.name",
                       (int(pid), name))
    db.executemany("UPDATE faces SET person_id=?, locked=?, hidden=?, cluster=NULL WHERE id=?",
                   [(pid, locked, hidden, int(fid)) for fid, (pid, locked, hidden) in state.get("faces", {}).items()])
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
    return _change(db, {f: [pid, 1, 0] for f in face_ids}, people, recognize=True)


def plan_not(db, face_ids):
    """"That isn't them": unnamed, and never recognized again (it can still be named)."""
    return _change(db, {f: [None, 1, 0] for f in face_ids})


def plan_hide(db, face_ids):
    """Not a face, or no one to name: never asked about or recognized again."""
    return _change(db, {f: [None, 1, 1] for f in face_ids})


def plan_rename(db, pid: int, name: str):
    """A new name; the name of someone else merges the two."""
    other = person_id(db, name)
    if other is not None and other != pid:
        return plan_merge(db, [pid], other)
    return _change(db, {}, {pid: name.strip()})


def plan_merge(db, pids, into: int):
    """Everyone in pids is the person into."""
    pids = [p for p in pids if p != into]
    marks = ",".join("?" * len(pids))
    faces = {r[0]: [into, r[1] or 0, 0] for r in db.execute(
        f"SELECT id, locked FROM faces WHERE person_id IN ({marks})", pids)} if pids else {}
    return _change(db, faces, {p: None for p in pids}, recognize=True)


def plan_forget(db, pid: int):
    """No such person: their faces are unnamed again (and may be recognized as someone else)."""
    faces = {r[0]: [None, 0, 0] for r in db.execute("SELECT id FROM faces WHERE person_id=?", (pid,))}
    return _change(db, faces, {pid: None})


# --- for the app -----------------------------------------------------------------------------------

def face_rows(db, where: str, params=()) -> list[dict]:
    """Faces to show, with what face_thumb needs."""
    return [dict(r) for r in db.execute(
        "SELECT f.id, f.photo_id, f.x, f.y, f.w, f.h, f.score, f.person_id, pe.name, p.path, p.qhash FROM faces f "
        f"JOIN photos p ON p.id = f.photo_id LEFT JOIN people pe ON pe.id = f.person_id WHERE ({where}) "
        "AND p.error IS NULL AND p.qhash IS NOT NULL", params)]


def who_groups(db, min_faces: int = 2) -> list[list[dict]]:
    """Unnamed faces in groups of look-alikes, biggest first: who to ask about. In each group the
    clearest face comes first and the closest look-alikes sit together (see likeness_order); each
    face has its "emb" (normalized), to sort by. Changes nothing (about a second for 40,000 faces)."""
    groups = [g for _, g in cluster_unnamed(db, save=False) if len(g) >= min_faces]
    keep = ("id", "photo_id", "x", "y", "w", "h", "score", "path", "qhash")
    out = []
    for g in groups:
        embs = normalize(np.stack([from_blob(r["emb"]) for r in g]))
        quality = np.array([r["score"] * min(r["w"], r["h"]) for r in g])
        out.append([{**{k: g[i][k] for k in keep}, "emb": embs[i]} for i in likeness_order(embs, quality)])
    return out


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
                cents[j] = normalize(sums[j])
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
    cent = normalize(np.mean([f["emb"] for f in to], axis=0))
    rest = sorted((f for f in faces if f["id"] not in chosen), key=lambda f: -float(f["emb"] @ cent))
    return [f for f in faces if f["id"] in chosen] + rest


def person_faces(db, pid: int) -> list[dict]:
    """Someone's faces, least like the rest of theirs first: where wrong matches are."""
    rows = face_rows(db, "f.person_id = ?", (pid,))
    if len(rows) < 2:
        return rows
    embs = {r["id"]: r for r in rows}
    found, vecs = load_faces(db, "f.person_id = ?", (pid,))
    sims = vecs @ normalize(vecs.mean(axis=0))
    order = [found[i]["id"] for i in np.argsort(sims)]
    return [embs[i] for i in order if i in embs]
