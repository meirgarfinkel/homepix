"""Duplicates: exact copies (the same bytes, photos and videos) and near ones (a photo resized or saved
again: it looks the same), never a burst of different shots.

- Exact: the same fingerprint (size, start and end), confirmed by a hash of the whole file.
- Near: photos whose perceptual hash (of their thumbnail) differs in at most NEAR_BITS of 64 bits and
  the search model sees as nearly the same picture (NEAR_LOOK), unless they're two shots: both have
  a real date and they're up to BURST_WINDOW apart, or taken in the same second at the same size.
  A raw file (DNG) is only ever an exact copy: the JPEG beside it is the same shot, kept on purpose.
- The keeper of a group: the most pixels, then the biggest file (then a name already organized).

Extras go to the Trash, never to a folder of their own. Nothing is moved or deleted here:
plan_extras() works out the steps, which fileops.run() makes, undoably.
"""

import datetime as dt
import hashlib
import os
from dataclasses import dataclass, field

import numpy as np
from PIL import Image

from . import dates

NEAR_BITS = 4
NEAR_LOOK = 0.95
BURST_WINDOW = 3600  # seconds
RAW = (".dng",)  # a raw file and its JPEG are one shot kept twice on purpose, not a copy

_N = 32
_DCT = np.cos(np.pi * np.outer(np.arange(_N), 2 * np.arange(_N) + 1) / (2 * _N))


def phash(img: Image.Image) -> int:
    """A 64-bit perceptual hash (the usual pHash: the low frequencies of a 32x32 greyscale DCT, each
    above or below their median), as a signed 64-bit int for SQLite."""
    px = np.asarray(img.convert("L").resize((_N, _N), Image.LANCZOS), np.float64)
    low = (_DCT @ px @ _DCT.T)[:8, :8]
    bits = (low > np.median(low)).flatten()
    return int(np.packbits(bits).view(">u8")[0].astype(np.int64))


def distance(a: int, b: int) -> int:
    return bin((a ^ b) & (2**64 - 1)).count("1")


# --- filled in after indexing ------------------------------------------------------------------------

def fill_looks(db, prefix: str, should_stop=lambda: False) -> int:
    """Work out the perceptual hash (from the thumbnail, so it's quick) and the size in pixels of the
    photos under prefix that don't have them yet. Returns how many."""
    from .thumbs import old_thumb_file, photo_thumb, thumb_file

    rows = db.execute("SELECT id, path, qhash FROM photos WHERE phash IS NULL AND kind='photo' AND error IS NULL "
                      "AND qhash IS NOT NULL AND substr(path, 1, ?) = ?", (len(prefix), prefix)).fetchall()
    done, updates = 0, []
    for r in rows:
        if should_stop():
            break
        try:
            thumb = thumb_file(r["qhash"])
            if not thumb.exists():
                thumb = old_thumb_file(r["qhash"])
            if not thumb.exists():
                thumb = photo_thumb(r["qhash"], r["path"])
            with Image.open(thumb) as t:
                t.thumbnail((320, 320))  # the size hashes were taken at before thumbnails grew
                h = phash(t)
            with Image.open(r["path"]) as img:  # only its header is read
                w, ht = img.size
        except Exception:
            continue  # tried again next time
        updates.append((h, w, ht, r["id"]))
        if len(updates) >= 200:
            db.executemany("UPDATE photos SET phash=?, width=?, height=? WHERE id=?", updates)
            db.commit()
            done, updates = done + len(updates), []
    db.executemany("UPDATE photos SET phash=?, width=?, height=? WHERE id=?", updates)
    db.commit()
    return done + len(updates)


def _sha256(db, rows) -> dict[int, str]:
    """{id: hash of the whole file}, worked out (and kept) for those that don't have one yet."""
    out = {}
    for r in rows:
        h = r["sha256"]
        if h is None:
            try:
                d = hashlib.sha256()
                with open(r["path"], "rb") as f:
                    while chunk := f.read(1 << 20):
                        d.update(chunk)
                h = d.hexdigest()
            except OSError:
                continue
            db.execute("UPDATE photos SET sha256=? WHERE id=?", (h, r["id"]))
        out[r["id"]] = h
    db.commit()
    return out


# --- finding them ------------------------------------------------------------------------------------

@dataclass
class Copy:
    id: int
    path: str
    kind: str
    qhash: str
    size: int
    width: int | None
    height: int | None
    taken: str | None

    @property
    def pixels(self) -> int:
        return (self.width or 0) * (self.height or 0)


@dataclass
class Group:
    copies: list[Copy]  # the keeper first
    how: str  # "exact" (the same bytes) or "near" (looks the same)
    pairs: list = field(default_factory=list, repr=False)  # [(id, id)] that matched

    @property
    def keeper(self) -> Copy:
        return self.copies[0]

    @property
    def extras(self) -> list[Copy]:
        return self.copies[1:]

    def keep(self, photo_id: int):
        """Make another of them the keeper."""
        self.copies.sort(key=lambda c: c.id != photo_id)


def _keeper_order(c: Copy):
    organized = bool(dates.ORGANIZED.match(os.path.splitext(os.path.basename(c.path))[0]))
    return (-c.pixels, -(c.size or 0), not organized, len(c.path), c.path)


_COLS = "id, path, kind, qhash, size, width, height, taken, taken_source, sha256, phash, clip"


def _rows(db):
    return db.execute(f"SELECT {_COLS} FROM photos WHERE error IS NULL AND qhash IS NOT NULL "
                      "AND kind != 'motion'").fetchall()


def _copy(r) -> Copy:
    return Copy(r["id"], r["path"], r["kind"] or "photo", r["qhash"], r["size"] or 0, r["width"], r["height"],
                r["taken"])


def _exact_pairs(db, rows) -> list[tuple[int, int]]:
    by_q = {}
    for r in rows:
        by_q.setdefault(r["qhash"], []).append(r)
    pairs = []
    for same in by_q.values():
        if len(same) < 2:
            continue
        sha = _sha256(db, same)
        by_sha = {}
        for r in same:
            if r["id"] in sha:
                by_sha.setdefault(sha[r["id"]], []).append(r["id"])
        for ids in by_sha.values():
            pairs += [(ids[0], i) for i in ids[1:]]
    return pairs


def _when(r):
    if r["taken"] is None or r["taken_source"] in (None, "file-modified", "placeholder"):
        return None  # no real date to tell two shots apart by
    return dt.datetime.fromisoformat(r["taken"])


def _raw(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in RAW


def two_shots(a, b) -> bool:
    """Are these two different shots (a burst, or a series), however alike they look? Two with real
    dates up to BURST_WINDOW apart; or two of the same size in pixels and nearly the same file size
    (a copy that size is the same bytes, found as an exact one; a series of shots is like this)."""
    ta, tb = _when(a), _when(b)
    if ta is not None and tb is not None and 0 < abs((ta - tb).total_seconds()) <= BURST_WINDOW:
        return True
    same_size = a["width"] and (a["width"], a["height"]) == (b["width"], b["height"])
    sizes = sorted([a["size"] or 1, b["size"] or 1])
    return bool(same_size) and sizes[1] / sizes[0] < 1.5


def _near_pairs(rows) -> list[tuple[int, int, int, float]]:
    """[(id, id, bits apart, how alike to the search model)] of photos that look the same."""
    photos = [r for r in rows if r["phash"] is not None and r["clip"] is not None and not _raw(r["path"])]
    if len(photos) < 2:
        return []
    h = np.array([r["phash"] for r in photos], np.int64).view(np.uint64)
    clip = np.frombuffer(b"".join(r["clip"] for r in photos), np.float16).reshape(len(photos), -1).astype(np.float32)
    pairs = []
    for i in range(0, len(photos), 1024):
        d = np.bitwise_count(h[i:i + 1024, None] ^ h[None, :])
        a, b = np.nonzero(d <= NEAR_BITS)
        a += i
        keep = b > a
        a, b = a[keep], b[keep]
        if not len(a):
            continue
        look = np.einsum("ij,ij->i", clip[a], clip[b])
        ok = look >= NEAR_LOOK
        for x, y, dist, sim in zip(a[ok], b[ok], d[a[ok] - i, b[ok]], look[ok], strict=True):
            if not two_shots(photos[x], photos[y]):
                pairs.append((photos[x]["id"], photos[y]["id"], int(dist), float(sim)))
    return pairs


def _apart(db) -> set[tuple[int, int]]:
    return {(a, b) for a, b in db.execute("SELECT a, b FROM apart")}


def find(db) -> list[Group]:
    """The groups of duplicates in the library, newest first. Each holds files that are all copies of
    the first, its keeper."""
    rows = _rows(db)
    by_id = {r["id"]: r for r in rows}
    apart = _apart(db)
    exact = [(a, b, -1, 1.0) for a, b in _exact_pairs(db, rows) if tuple(sorted((a, b))) not in apart]
    near = [p for p in _near_pairs(rows) if tuple(sorted(p[:2])) not in apart]
    # Each file is a copy of the best of the files ranked above it that it matches (the same bytes
    # first, then the closest look), or else a keeper. No chains: two shots of a burst that each
    # match the other's smaller copy stay apart, each with its own.
    rank = {i: n for n, i in enumerate(sorted({x for p in exact + near for x in p[:2]},
                                               key=lambda i: _keeper_order(_copy(by_id[i]))))}
    best = {}
    for a, b, dist, sim in exact + near:
        lo, hi = (a, b) if rank[a] > rank[b] else (b, a)  # lo is the copy: ranked below hi
        key = (dist, -sim, rank[hi])
        if lo not in best or key < best[lo][0]:
            best[lo] = (key, hi)
    keeper_of = {}
    for i in sorted(rank, key=rank.get):  # best first, so a file's keeper is settled before it
        if i in best:
            up = best[i][1]
            keeper_of[i] = keeper_of.get(up, up)
    members = {}
    for i, k in keeper_of.items():
        members.setdefault(k, [k]).append(i)
    groups = []
    for ids in members.values():
        copies = [_copy(by_id[i]) for i in ids]
        how = "exact" if all(best[i][0][0] == -1 for i in ids[1:]) else "near"
        groups.append(Group(copies, how, [(best[i][1], i) for i in ids[1:]]))
    groups.sort(key=lambda g: g.keeper.taken or "", reverse=True)
    return groups


def pending(db) -> int:
    """Photos whose look isn't worked out yet (they're done after indexing): duplicates among them
    aren't found until then."""
    return db.execute("SELECT COUNT(*) FROM photos WHERE phash IS NULL AND kind='photo' AND error IS NULL "
                      "AND qhash IS NOT NULL").fetchone()[0]


# --- doing something about them ----------------------------------------------------------------------

def plan_extras(db, groups) -> list[dict]:
    """Steps that move every group's extras to the Trash (a Live Photo's clip goes with its photo,
    unless the copy kept is as much its photo)."""
    from . import fileops

    return fileops.plan_trash(fileops.with_companions(db, [c.path for g in groups for c in g.extras]))


def plan_apart(db, groups) -> dict | None:
    """A step saying each group's files aren't duplicates of each other (never offered again)."""
    have = _apart(db)
    pairs = sorted({tuple(sorted((a.id, b.id))) for g in groups for a in g.copies for b in g.copies
                    if a.id != b.id} - have)
    if not pairs:
        return None
    return {"action": "apart", "src": None, "dst": None,
            "before": {"old": {"pairs": pairs, "apart": False}, "new": {"pairs": pairs, "apart": True}}}


def apply(db, state: dict):
    """Set the state a "not duplicates" change (or its undoing) leaves."""
    pairs = [tuple(p) for p in state["pairs"]]
    if state["apart"]:
        db.executemany("INSERT OR IGNORE INTO apart (a, b) SELECT ?, ? WHERE EXISTS (SELECT 1 FROM photos WHERE id=?) "
                       "AND EXISTS (SELECT 1 FROM photos WHERE id=?)", [(a, b, a, b) for a, b in pairs])
    else:
        db.executemany("DELETE FROM apart WHERE a=? AND b=?", pairs)


def summary(groups) -> str:
    extras = sum(len(g.extras) for g in groups)
    return f"{len(groups):,} group{'s' if len(groups) != 1 else ''}, {extras:,} extra cop{'ies' if extras != 1 else 'y'}"

