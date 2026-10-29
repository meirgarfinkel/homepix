"""Search by description: scoring every photo, and turning the scores into clear matches and possibly
related photos."""

import os
import threading
import time

import numpy as np

from .config import MATCH_STRICTNESS, MAX_NEAR, NEAR_STRICTNESS
from .db import open_db
from .settings import excluded_folders, left_out


def split_matches(scores: np.ndarray):
    """Split photos into clear matches and possibly related ones, best first.
    Photos below both bars are left out.

    Every photo gets a score, and there's no fixed number that means "match":
    it varies by search. So instead we look at how this search scored across the
    whole library and keep photos that stand well above the typical one.
    Median-based statistics are used so a handful of strong matches don't skew it.
    """
    order = np.argsort(-scores)
    if len(scores) < 8:  # too few photos to tell what's typical
        return order, order[:0]
    typical = np.median(scores)
    spread = max(1.4826 * np.median(np.abs(scores - typical)), 0.005)
    n_clear = int((scores >= typical + MATCH_STRICTNESS * spread).sum())
    n_near = int((scores >= typical + NEAR_STRICTNESS * spread).sum())
    return order[:n_clear], order[n_clear:n_near][:MAX_NEAR]


class SearchIndex:
    """Search by description. Every photo's search vector is kept in memory (reading them all from
    SQLite took far longer than the search itself) and reloaded only after the database changes.

    settling(): return True while the database is changing constantly (indexing); the vectors are then
    reloaded at most every 10 seconds instead of on every search.
    Safe to use from several threads.
    """

    def __init__(self, settling=None):
        self.lock = threading.Lock()
        self.settling = settling or (lambda: False)
        self.db = None  # a connection of our own, so data_version can tell us about others' writes
        self.version = None
        self.built = 0.0
        self.rows, self.ids, self.mat = [], np.zeros(0, np.int64), np.zeros((0, 512), np.float32)
        self.hidden = np.zeros(0, bool)
        self._hidden_for = None  # the folders excluded that hidden was worked out for
        self._text = None
        self._text_lock = threading.Lock()

    def vectors(self):
        """(rows, ids, matrix): rows are dicts with id, path, taken, qhash, kind."""
        with self.lock:
            if self.db is None:
                self.db = open_db()
            version = self.db.execute("PRAGMA data_version").fetchone()[0]
            if version != self.version and not (self.settling() and time.time() - self.built < 10):
                rows = self.db.execute(
                    "SELECT id, path, taken, qhash, kind, clip FROM photos WHERE clip IS NOT NULL AND error IS NULL"
                ).fetchall()
                self.rows = [{"id": r["id"], "path": r["path"], "taken": r["taken"], "qhash": r["qhash"],
                              "kind": r["kind"] or "photo"} for r in rows]
                self.ids = np.array([r["id"] for r in rows], np.int64)
                self._hidden_for = None
                if rows:
                    blob = b"".join(r["clip"] for r in rows)
                    self.mat = np.frombuffer(blob, np.float16).reshape(len(rows), -1).astype(np.float32)
                else:
                    self.mat = np.zeros((0, 512), np.float32)
                self.version, self.built = version, time.time()
            # the folders excluded aren't searched
            hidden = tuple(excluded_folders())
            if hidden != self._hidden_for:
                self.hidden = np.array([left_out(r["path"], hidden) for r in self.rows], bool)
                self._hidden_for = hidden
            return self.rows, self.ids, self.mat

    def text_model(self):
        with self._text_lock:
            if self._text is None:
                from .ai import ClipText

                self._text = ClipText()
            return self._text

    def warm(self):
        """Load the vectors and the text model ahead of the first search (it takes a second or two)."""
        self.vectors()
        self.text_model()

    def search(self, q: str, person_id=None, folder=None, db=None) -> dict:
        """{"total": photos searched, "matches": [row + "score"], "related": [...]}, best first.
        person_id: only photos with that person in them. folder: only photos in it or below it."""
        rows, ids, mat = self.vectors()
        keep = np.ones(len(rows), bool)
        hidden = self.hidden
        if len(hidden) == len(rows) and (folder is None or not left_out(folder, self._hidden_for)):
            keep &= ~hidden
        sel = np.flatnonzero(keep)
        if person_id is not None or folder is not None:
            own = db is None
            db = db or open_db()
            try:
                if person_id is not None:
                    theirs = [r[0] for r in db.execute(
                        "SELECT DISTINCT photo_id FROM faces WHERE person_id=?", (person_id,))]
                    keep &= np.isin(ids, theirs)
                if folder is not None:
                    prefix = str(folder).rstrip(os.sep) + os.sep
                    keep &= np.array([r["path"].startswith(prefix) for r in rows], bool)
                sel = np.flatnonzero(keep)
            finally:
                if own:
                    db.close()
        if not len(sel):
            return {"total": 0, "matches": [], "related": []}
        scores = mat[sel] @ self.text_model().query(q)
        clear, near = split_matches(scores)

        def out(i):
            return {**rows[sel[i]], "score": float(scores[i])}

        return {"total": len(sel), "matches": [out(i) for i in clear], "related": [out(i) for i in near]}
