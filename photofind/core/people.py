"""Faces to people: recognizing named people and grouping the unnamed."""

import numpy as np

from .config import ASSIGN_MARGIN, ASSIGN_SIM, CLUSTER_SIM
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


def auto_assign(db) -> int:
    """Tag unnamed faces that clearly match someone already named."""
    people, cents = person_centroids(db)
    if not people:
        return 0
    rows, embs = load_faces(db, "f.person_id IS NULL AND f.locked = 0")
    if not rows:
        return 0
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
    db.executemany("UPDATE faces SET person_id=?, cluster=NULL WHERE id=?", updates)
    db.commit()
    return len(updates)


def cluster_unnamed(db):
    """Group unnamed faces that look like the same person. Group 1 is the biggest."""
    rows, embs = load_faces(db, "f.person_id IS NULL")
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
    updates = []
    for num, group in enumerate(members, start=1):
        updates += [(num, rows[i]["id"]) for i in group]
    db.executemany("UPDATE faces SET cluster=? WHERE id=?", updates)
    db.commit()
    return [(num, [rows[i] for i in group]) for num, group in enumerate(members, start=1)]
