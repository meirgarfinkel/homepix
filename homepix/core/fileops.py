"""Changing files: move, rename, copy, Trash and restore, with undo and redo, journaled so that a
crash at any moment loses nothing and leaves the index matching the disk.

How a file is never lost:
- Nothing is ever overwritten (see safefs). A name that's taken gets _1, _2, ... like the library's.
- Every operation is written to the journal (ops, op_steps) before any file is touched, and each
  step's destination is recorded before the step happens. So after a crash, recover() can tell
  from where each file is what was done: a step whose file is at its destination is finished.
  (So the rest is committed in batches, which makes 5,000 files take a second, not a minute.)
- Moves happen in two phases, as in the Sep 2026 pipeline: first every file is renamed to a hidden
  name beside itself, then each goes to its destination. Chains and swaps (A -> B while B -> C)
  are then safe whatever the order.
- The index changes in the same transaction that marks a step finished, and while a move is under
  way its row is at the file's hidden name too, so the index always matches the disk.

Steps (op_steps.action): move, copy, trash, restore, and meta (details written into the file: the
values before and after are in `before`, so undoing writes the old ones back). op_steps.done: 0 planned,
1 under way (a move's file is at its hidden name; a Trash name is reserved), 2 finished, 3 skipped (why: in
`after`).
An operation's state: running, done, interrupted (stopped or crashed partway: what finished stays
finished and can be undone), undone.
"""

import contextlib
import datetime as dt
import filecmp
import json
import os
import shutil
import time
from pathlib import Path

from . import dates, duplicates, metadata, people
from . import trash as trashcan
from .db import columns
from .safefs import copy_file, free_name, move_file, rename_noreplace
from .thumbs import thumbs_dir
from .util import quick_hash

# Steps that change only the index (who's who, what isn't a duplicate): {action: its module}, whose
# apply(db, state) sets a state; the step's before is {"old": state, "new": state}.
INDEX_ONLY = {"people": people, "apart": duplicates}

PLANNED, UNDER_WAY, FINISHED, SKIPPED = 0, 1, 2, 3


def _now() -> str:
    return dt.datetime.now().isoformat(sep=" ", timespec="seconds")


def _hidden(path: str, op_id: int, seq: int, what: str) -> str:
    """HomePix's own name for a file in transit, next to path (hidden, so indexing skips it)."""
    return os.path.join(os.path.dirname(path), f".homepix-{what}-{op_id}-{seq}")


# --- the index side ---------------------------------------------------------------------------------


def snapshot(db, path: str):
    """Everything the index knows about a photo (its row and its faces, names included), to put back
    after restoring it. None if it isn't indexed."""
    row = db.execute("SELECT * FROM photos WHERE path=?", (path,)).fetchone()
    if row is None:
        return None

    def plain(r, cols):
        return {c: (r[c].hex() if isinstance(r[c], bytes) else r[c]) for c in cols}

    face_cols = [c for c in columns(db, "faces") if c not in ("id", "photo_id")]
    faces = db.execute("SELECT * FROM faces WHERE photo_id=?", (row["id"],)).fetchall()
    photo_cols = [c for c in columns(db, "photos") if c != "id"]
    return {
        "photo": plain(row, photo_cols),
        "faces": [plain(f, face_cols) for f in faces],
        "blobs": [c for c in photo_cols + face_cols if c in ("clip", "emb")],
    }


def _insert_snapshot(db, snap, path: str) -> int:
    def unplain(d):
        return {c: (bytes.fromhex(v) if c in snap["blobs"] and v is not None else v) for c, v in d.items()}

    photo = unplain(snap["photo"]) | {"path": path}
    cols = list(photo)
    cur = db.execute(
        f"INSERT INTO photos ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", [photo[c] for c in cols]
    )
    pid = cur.lastrowid
    known = {r[0] for r in db.execute("SELECT id FROM people")} if snap["faces"] else set()
    for face in snap["faces"]:
        f = unplain(face) | {"photo_id": pid}
        if f.get("person_id") not in known:  # (someone deleted meanwhile)
            f["person_id"] = None
        db.execute(f"INSERT INTO faces ({', '.join(f)}) VALUES ({', '.join('?' * len(f))})", list(f.values()))
    return pid


def _apply(db, step) -> dict:
    """Bring the index in line with a finished step. Returns what to remember about it."""
    action, src, dst = step["action"], step["src"], step["dst"]
    if action == "move":
        # The row is at the file's hidden name once it's staged (see run); at src only if a crash came
        # first, before any other file could have arrived at src.
        temp = _hidden(src, step["op_id"], step["seq"], "moving")
        if db.execute("UPDATE photos SET path=? WHERE path=?", (dst, temp)).rowcount == 0:
            db.execute("UPDATE photos SET path=? WHERE path=?", (dst, src))
        _renamed(db, dst, os.path.basename(src))
        return {}
    if action == "copy":
        snap = snapshot(db, src)
        return {"new_id": _insert_snapshot(db, snap, dst)} if snap else {}
    if action == "trash":
        db.execute("DELETE FROM photos WHERE path=?", (src,))
        return {}
    if action == "restore":
        before = json.loads(step["before"] or "{}")
        if before.get("snapshot") and db.execute("SELECT 1 FROM photos WHERE path=?", (dst,)).fetchone() is None:
            return {"new_id": _insert_snapshot(db, before["snapshot"], dst)}
        return {}
    if action == "meta":
        return _apply_meta(db, src, json.loads(step["before"]))
    if action in INDEX_ONLY:  # done in the index itself (see run)
        return {}
    raise ValueError(f"unknown step {action}")


def _renamed(db, path, old_name: str):
    """A file's name counts towards its date (dates.py): after a rename, work it out again if it may
    have changed (not when a real date in the file itself is still the earliest)."""
    new, old = dates.in_name(os.path.basename(path)), dates.in_name(old_name)
    if new == old:
        return
    row = db.execute("SELECT id, kind, taken, taken_source, mtime FROM photos WHERE path=?", (path,)).fetchone()
    if row is None or row["taken"] is None:
        return
    if row["taken_source"] in ("exif", "video") and (
        new is None or dates.is_placeholder(new) or new.isoformat(sep=" ") >= row["taken"]
    ):
        return
    taken, source, moment = dates.of_file(path, row["kind"] or "photo", row["mtime"] or 0)
    db.execute("UPDATE photos SET taken=?, taken_source=?, moment=? WHERE id=?", (taken, source, moment, row["id"]))


def _apply_meta(db, path, change) -> dict:
    """After writing details into a file: its new fingerprint (so it isn't indexed again from scratch,
    which would lose who is in it), date, keywords and description; the face boxes moved if
    it was turned; its thumbnails kept unless it was."""
    row = db.execute("SELECT id, qhash, kind, taken FROM photos WHERE path=?", (path,)).fetchone()
    if row is None:
        return {}
    st = os.stat(path)
    qh = quick_hash(Path(path), st.st_size)
    d = metadata.read([path]).get(path)
    fields = {"size": st.st_size, "mtime": st.st_mtime, "qhash": qh, "sha256": None}  # (its bytes changed)
    if d is not None:
        taken, source, moment = dates.from_tags(path, d.tags, st.st_mtime)
        fields.update(
            taken=taken,
            taken_source=source,
            moment=moment,
            details_done=1,
            keywords=json.dumps(d.keywords) if d.keywords else None,
            description=d.description or None,
        )
    db.execute(f"UPDATE photos SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?", (*fields.values(), row["id"]))
    turn = change.get("orientation")
    if turn and turn[0] != turn[1]:
        db.execute("UPDATE photos SET phash=NULL WHERE id=?", (row["id"],))  # it looks different now
        for f in db.execute("SELECT id, x, y, w, h FROM faces WHERE photo_id=?", (row["id"],)).fetchall():
            db.execute(
                "UPDATE faces SET x=?, y=?, w=?, h=? WHERE id=?",
                (*metadata.move_box((f["x"], f["y"], f["w"], f["h"]), *turn), f["id"]),
            )
    elif row["qhash"] and row["qhash"] != qh:  # the picture is the same: so are its thumbnails
        for kind in ("photos", "faces"):
            for old in thumbs_dir(kind).glob(f"{row['qhash']}*.jpg"):
                new = old.with_name(qh + old.name[len(row["qhash"]) :])
                if not new.exists():
                    with contextlib.suppress(OSError):
                        shutil.copyfile(old, new)  # (a copy, not a rename: an exact copy elsewhere shares them)
    return {}


# Committing after every file would make a big operation slow (each commit waits for the disk), and
# isn't needed: recover() tells from where the files are what was done. Only what it can't tell is
# committed before going on: the plan, the end of the first phase of moves, the names chosen in the
# Trash, and a destination changed because its name was taken meanwhile.


def _finish(db, step, dst=None):
    """The step's file is where it should be: update the index and mark it finished, together."""
    step = dict(step)
    if dst is not None:
        step["dst"] = dst
    after = _apply(db, step)
    db.execute(
        "UPDATE op_steps SET done=?, dst=?, after=? WHERE id=?", (FINISHED, step["dst"], json.dumps(after), step["id"])
    )


def _skip(db, step, why: str):
    db.execute("UPDATE op_steps SET done=?, after=? WHERE id=?", (SKIPPED, json.dumps({"why": why}), step["id"]))


def _set(db, step, **fields):
    db.execute(f"UPDATE op_steps SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?", (*fields.values(), step["id"]))


# --- planning ---------------------------------------------------------------------------------------


def plan_moves(pairs, freed=()) -> list[dict]:
    """[(src, wanted dst)] -> move steps. A wanted name that's taken (and not being vacated by another
    file in the same operation, or going to the Trash first: freed) gets _1, _2, ...; files already
    where they're wanted are left out."""
    # (names compared as the system does: on Windows IMG.JPG and img.jpg are one name)
    srcs = {os.path.normcase(os.path.abspath(s)) for s, d in pairs if os.path.abspath(s) != os.path.abspath(d)}
    srcs |= {os.path.normcase(os.path.abspath(p)) for p in freed}
    planned, steps = set(), []
    for src, dst in pairs:
        src, dst = os.path.abspath(src), os.path.abspath(dst)
        if src == dst:
            continue
        folder, name = os.path.split(dst)
        stem, ext = os.path.splitext(name)
        n, candidate = 0, dst
        while (key := os.path.normcase(candidate)) in planned or (os.path.lexists(candidate) and key not in srcs):
            n += 1
            candidate = os.path.join(folder, f"{stem}_{n}{ext}")
        planned.add(key)
        steps.append({"action": "move", "src": src, "dst": candidate})
    return steps


def plan_into(paths, folder, action: str) -> list[dict]:
    """Move or copy files into folder, keeping their names (made free if taken)."""
    folder = os.path.abspath(folder)
    if action == "move":
        return plan_moves([(p, os.path.join(folder, os.path.basename(p))) for p in paths])
    taken, steps = set(), []
    for p in paths:
        name = free_name(folder, os.path.basename(p), taken)
        taken.add(name)
        steps.append({"action": "copy", "src": os.path.abspath(p), "dst": os.path.join(folder, name)})
    return steps


def plan_trash(paths) -> list[dict]:
    return [{"action": "trash", "src": os.path.abspath(p), "dst": None} for p in paths]


def companions(db, paths) -> dict[str, str]:
    """{photo path: its Live Photo clip's path} for those of paths that have one.

    Each motion clip belongs to one photo in its folder taken within 2 seconds of it: the one with the
    same name, else the closest. That's settled among all the folder's photos, not only paths, so a
    clip never goes with a photo a second off when its own photo sits right beside it. Where a photo
    not in paths is as close as one in it (an exact copy kept while its duplicate goes), the clip
    stays with the one left behind."""
    wanted = {os.path.abspath(p) for p in paths}
    folders = {os.path.dirname(p) for p in wanted}
    photos, clips = {}, {}
    for path, kind, when in db.execute(  # a clip's local time, as its photo's date is
        "SELECT path, kind, COALESCE(moment, taken) FROM photos WHERE kind IN ('photo', 'motion') AND taken IS NOT NULL"
    ):
        folder = os.path.dirname(path)
        if folder in folders:
            (photos if kind == "photo" else clips).setdefault(folder, []).append(
                (path, dt.datetime.fromisoformat(when))
            )

    def stem(path):
        return os.path.splitext(os.path.basename(path))[0].lower()

    out = {}
    for folder, its_clips in clips.items():
        pairs = sorted(
            (stem(p) != stem(c), abs((pt - ct).total_seconds()), p in wanted, p, c)
            for c, ct in its_clips
            for p, pt in photos.get(folder, [])
            if abs((pt - ct).total_seconds()) <= 2
        )
        has, taken = set(), set()
        for _, _, asked, p, c in pairs:  # the best match first, all through the folder
            if p in has or c in taken:
                continue
            has.add(p)
            taken.add(c)
            if asked:
                out[p] = c
    return out


def with_companions(db, paths) -> list[str]:
    """paths, each photo followed by its Live Photo clip (if it has one and it isn't already there)."""
    paths = [os.path.abspath(p) for p in paths]
    have, clips = set(paths), companions(db, paths)
    out = []
    for p in paths:
        out.append(p)
        c = clips.get(p)
        if c and c not in have:
            out.append(c)
            have.add(c)
    return out


def plan_rename(db, renames) -> list[dict]:
    """[(path, new name without folder)] -> move steps. A name without an extension keeps the file's
    own; a Live Photo's clip is renamed with its photo."""
    pairs, clips = [], companions(db, [p for p, _ in renames])
    for path, name in renames:
        path = os.path.abspath(path)
        stem, ext = os.path.splitext(name)
        if ext.lower() != os.path.splitext(path)[1].lower():
            stem, ext = name, os.path.splitext(path)[1]
        pairs.append((path, os.path.join(os.path.dirname(path), stem + ext)))
        if path in clips:
            clip = clips[path]
            pairs.append((clip, os.path.join(os.path.dirname(clip), stem + os.path.splitext(clip)[1])))
    return plan_moves(pairs)


def plan_details(db, paths, edit: "metadata.Edit"):
    """Steps that write edit into each file, and [(path, why)] for those it can't be written to.
    Changing the date changes a Live Photo's clip's date too, so they stay together; and a file named
    by its date (2022_04_01_07_32_31.jpg) is renamed to its new one, so the two never disagree."""
    paths = with_companions(db, paths) if edit.touches_date() else [os.path.abspath(p) for p in paths]
    found, steps, cant = {}, [], []
    for i in range(0, len(paths), 200):
        found.update(metadata.read(paths[i : i + 200]))
    for p in paths:
        d = found.get(p)
        if d is None:
            cant.append((p, "its details can't be read"))
            continue
        row = db.execute("SELECT taken, taken_source FROM photos WHERE path=?", (p,)).fetchone()
        shown = None  # a date from its name counts; a guess from when the file changed doesn't
        if row and row["taken"] and row["taken_source"] not in (None, "file-modified"):
            shown = dt.datetime.fromisoformat(row["taken"])
        try:
            new = metadata.changes(p, d, edit, shown)
        except metadata.CantChange as e:
            cant.append((p, str(e)))
            continue
        if not new:
            continue  # it already is that way
        change = {"old": metadata.before(d, new), "new": new}
        if edit.turn:
            change["orientation"] = [d.orientation, metadata.turned(d.orientation, edit.turn)]
        steps.append({"action": "meta", "src": p, "dst": p, "before": change})
    if edit.touches_date():
        renames = []
        for s in steps:
            stem, ext = os.path.splitext(os.path.basename(s["src"]))
            new = s["before"]["new"]
            when = dates.parse(new.get("ExifIFD:DateTimeOriginal") or new.get("QuickTime:CreateDate"))
            if when is not None and dates.ORGANIZED.match(stem):
                renames.append((s["src"], os.path.join(os.path.dirname(s["src"]), when.strftime(dates.FORMAT) + ext)))
        steps = plan_moves(renames) + steps  # moves go first anyway; each write follows its file (see run)
    return steps, cant


def pattern_names(pattern: str, items) -> list[str]:
    """Names for a batch rename. items: [(path, taken datetime or None)]; the pattern may use {name}
    (the current name), {n} (1, 2, ... padded to the same width) and {date} (2024-07-01)."""
    width = len(str(len(items)))
    out = []
    for i, (path, taken) in enumerate(items, 1):
        out.append(
            pattern.format_map(
                {
                    "name": os.path.splitext(os.path.basename(path))[0],
                    "n": str(i).zfill(width),
                    "date": taken.strftime("%Y-%m-%d") if taken else "undated",
                }
            ).strip()
        )
    return out


# --- running ----------------------------------------------------------------------------------------


class Result:
    def __init__(self, op_id, finished=0, skipped=None, stopped=False):
        self.op_id, self.finished, self.skipped, self.stopped = op_id, finished, skipped or [], stopped

    def __repr__(self):
        return f"Result(op={self.op_id}, finished={self.finished}, skipped={self.skipped}, stopped={self.stopped})"


def run(db, kind: str, summary: str, steps: list[dict], progress=None, should_stop=None, undoes=None) -> Result:
    """Do an operation. Its steps are journaled first; then moves (in two phases), copies, Trash and
    restores. progress(done, total) is called as it goes; should_stop() is checked between files (a
    move already under way always finishes). Returns a Result; nothing raises for a single file that
    can't be done: it's skipped, and the reason is in Result.skipped as (path, reason)."""
    op = _Op(db, _journal(db, kind, summary, steps, undoes), progress, should_stop)
    early = op.trash_in_the_way()
    moved_to = op.move()
    trashing = op.reserve_trash(early)
    op.do_the_rest(trashing, moved_to)
    return op.close(trashing)


def _journal(db, kind, summary, steps, undoes) -> int:
    """The whole plan, on disk before any file is touched. Returns the operation's id."""
    cur = db.execute(
        "INSERT INTO ops (kind, summary, started, state, undoes) VALUES (?,?,?,?,?)",
        (kind, summary, _now(), "running", undoes),
    )
    op_id = cur.lastrowid
    for seq, s in enumerate(steps):
        before = s.get("before")
        if s["action"] == "trash" and before is None:
            before = {"snapshot": snapshot(db, s["src"])}
        db.execute(
            "INSERT INTO op_steps (op_id, seq, action, src, dst, before) VALUES (?,?,?,?,?,?)",
            (op_id, seq, s["action"], s["src"], s["dst"], json.dumps(before) if before else None),
        )
    db.commit()
    return op_id


class _Op:
    """An operation being done: its steps, in phases, and how far it has got."""

    def __init__(self, db, op_id, progress, should_stop):
        self.db, self.op_id = db, op_id
        self.report = progress or (lambda done, total: None)
        self.should_stop = should_stop or (lambda: False)
        self.rows = db.execute("SELECT * FROM op_steps WHERE op_id=? ORDER BY seq", (op_id,)).fetchall()
        self.result = Result(op_id)
        self.done = 0
        self.last_commit = time.monotonic()

    def stopping(self) -> bool:
        if self.result.stopped or self.should_stop():
            self.result.stopped = True
        return self.result.stopped

    def skipped(self, step, why):
        _skip(self.db, step, why)
        self.result.skipped.append((step["src"], why))

    def finished(self, step, dst):
        _finish(self.db, step, dst)
        self.result.finished += 1
        self.done += 1
        self.report(self.done, len(self.rows))
        if time.monotonic() - self.last_commit > 0.5:  # so the window can show the progress
            self.db.commit()
            self.last_commit = time.monotonic()

    def _hidden(self, r, path, what):
        return _hidden(path, self.op_id, r["seq"], what)

    def _reserve(self, r) -> str | None:
        """A name in the Trash for this step's file, recorded as under way. None: skipped."""
        if not os.path.lexists(r["src"]):
            self.skipped(r, "it isn't there any more")
            return None
        try:
            dst = trashcan.reserve(r["src"])
        except OSError as e:
            self.skipped(r, e.strerror or str(e))
            return None
        _set(self.db, r, dst=dst, done=UNDER_WAY)
        return dst

    def trash_in_the_way(self) -> set:
        """Trash that makes room for a move (a copy kept instead takes its original's place): first.
        Returns those steps' ids."""
        room = {os.path.normcase(r["dst"]) for r in self.rows if r["action"] == "move" and r["dst"]}
        early = set()
        for r in self.rows:
            if r["action"] != "trash" or os.path.normcase(r["src"]) not in room:
                continue
            early.add(r["id"])
            dst = self._reserve(r)
            if dst is None:
                continue
            self.db.commit()
            try:
                rename_noreplace(r["src"], dst)
            except OSError as e:
                trashcan.unreserve(dst)
                self.skipped(r, e.strerror or str(e))
                continue
            self.finished(r, dst)
        return early

    def move(self) -> dict:
        """The moves, in two phases. Returns {source: where it went}."""
        db = self.db
        # phase 1: each file to its hidden name beside itself
        staged = []
        for r in self.rows:
            if r["action"] != "move":
                continue
            if self.stopping():
                break
            temp = self._hidden(r, r["src"], "moving")
            try:
                rename_noreplace(r["src"], temp)
            except FileNotFoundError:
                self.skipped(r, "it isn't there any more")
                continue
            except OSError as e:
                self.skipped(r, e.strerror or str(e))
                continue
            # the row follows (paths are unique in the index, so it can't go straight to a name being vacated)
            db.execute("UPDATE photos SET path=? WHERE path=?", (temp, r["src"]))
            _set(db, r, done=UNDER_WAY)
            staged.append(r)
        db.commit()
        # phase 2: each staged file to its destination (always finished once started)
        moved_to = {}
        for r in staged:
            temp = self._hidden(r, r["src"], "moving")
            try:
                dst = _free(db, r, r["dst"])
                move_file(temp, dst, self._hidden(r, dst, "partial"))
            except OSError as e:
                try:
                    rename_noreplace(temp, r["src"])  # put it back where it was
                except OSError:
                    continue  # left under way: recover() will finish it
                db.execute("UPDATE photos SET path=? WHERE path=?", (r["src"], temp))
                self.skipped(r, e.strerror or str(e))
                continue
            self.finished(r, dst)
            moved_to[r["src"]] = dst
        return moved_to

    def reserve_trash(self, early) -> dict:
        """Trash: every name in it is chosen (and reserved) first. Returns {step id: step, with its dst}."""
        trashing = {}
        for r in self.rows:
            if r["action"] != "trash" or self.result.stopped or r["id"] in early:
                continue
            dst = self._reserve(r)
            if dst is not None:
                trashing[r["id"]] = dict(r) | {"dst": dst}
        self.db.commit()
        return trashing

    def do_the_rest(self, trashing: dict, moved_to: dict):
        """Copies, Trash, restores, details and the index-only steps. Trash done is taken out of trashing."""
        for r in self.rows:
            if r["action"] == "move" or (r["action"] == "trash" and r["id"] not in trashing):
                continue
            if self.stopping():
                break
            try:
                if r["action"] == "meta" and r["src"] in moved_to and not os.path.lexists(r["src"]):
                    r = self._follow(r, moved_to[r["src"]])  # renamed just now
                dst = self._do(r, trashing)
            except FileNotFoundError:
                self.skipped(r, "it isn't there any more")
                continue
            except OSError as e:
                self.skipped(r, e.strerror or str(e))
                continue
            self.finished(r, dst)

    def _follow(self, r, path):
        """A details step whose file was just renamed: it goes to the new name."""
        r = dict(r) | {"src": path, "dst": path}
        _set(self.db, r, src=path, dst=path)
        self.db.commit()  # (recovery and undo need to know where it went)
        return r

    def _do(self, r, trashing):
        """Do one step. Returns where its file went."""
        db, action = self.db, r["action"]
        if action == "copy":
            dst = _free(db, r, r["dst"])
            copy_file(r["src"], dst, self._hidden(r, dst, "partial"))
        elif action == "trash":
            dst = trashing.pop(r["id"])["dst"]
            try:
                rename_noreplace(r["src"], dst)
            except BaseException:
                trashcan.unreserve(dst)
                raise
        elif action == "restore":
            dst = _free(db, r, r["dst"])
            trashcan.restore(r["src"], dst)
        elif action == "meta":
            if not os.path.lexists(r["src"]):
                raise FileNotFoundError
            metadata.write(r["src"], json.loads(r["before"])["new"])
            dst = r["src"]
        elif action in INDEX_ONLY:  # only the index changes: committed with being marked done
            INDEX_ONLY[action].apply(db, json.loads(r["before"])["new"])
            dst = None
        return dst

    def close(self, trashing: dict) -> Result:
        """What wasn't done is marked so (a reserved Trash name is let go), and the operation ends."""
        db = self.db
        for r in trashing.values():  # stopped before they went
            trashcan.unreserve(r["dst"])
            _skip(db, r, "stopped before it was done")
        for r in db.execute("SELECT * FROM op_steps WHERE op_id=? AND done=?", (self.op_id, PLANNED)).fetchall():
            _skip(db, r, "stopped before it was done")
        state = "interrupted" if self.result.stopped else "done"
        db.execute("UPDATE ops SET state=?, finished=? WHERE id=?", (state, _now(), self.op_id))
        db.commit()
        return self.result


def _free(db, step, dst: str) -> str:
    """dst, or a free name beside it if something has taken it since the plan (recorded first)."""
    folder = os.path.dirname(dst)
    os.makedirs(folder, exist_ok=True)
    if os.path.lexists(dst):
        dst = os.path.join(folder, free_name(folder, os.path.basename(dst)))
        _set(db, step, dst=dst)
        db.commit()
    return dst


# --- after a crash ----------------------------------------------------------------------------------


def recover(db) -> list[str]:
    """Finish what a crash interrupted: every step whose file reached its destination is marked
    finished (and the index updated); every move caught at its hidden name is completed; leftover
    partial copies are removed. Returns a line for each operation looked at."""
    notes = []
    for op in db.execute("SELECT * FROM ops WHERE state='running' ORDER BY id").fetchall():
        fixed, started = 0, _second_phase_started(db, op["id"])
        for r in db.execute(
            "SELECT * FROM op_steps WHERE op_id=? AND done IN (?, ?) ORDER BY seq", (op["id"], PLANNED, UNDER_WAY)
        ).fetchall():
            src, dst, action = r["src"], r["dst"], r["action"]
            if dst:
                Path(_hidden(dst, op["id"], r["seq"], "partial")).unlink(missing_ok=True)
            if action == "move":
                fixed += _recover_move(db, op["id"], r, started)
            elif action == "copy":
                if os.path.lexists(dst) and os.path.lexists(src) and os.path.getsize(dst) == os.path.getsize(src):
                    _finish(db, r)
                    fixed += 1
                else:
                    _skip(db, r, "interrupted before it was done")
            elif action == "trash":
                if dst and os.path.lexists(dst) and not os.path.lexists(src):
                    _finish(db, r)
                    fixed += 1
                else:
                    if dst:
                        trashcan.unreserve(dst)
                    _skip(db, r, "interrupted before it was done")
            elif action == "restore":
                if os.path.lexists(dst) and not os.path.lexists(src):
                    trashcan.unreserve(src)
                    _finish(db, r)
                    fixed += 1
                else:
                    _skip(db, r, "interrupted before it was done")
            elif action in INDEX_ONLY:  # it and its being done are committed together: it wasn't
                _skip(db, r, "interrupted before it was done")
            elif action == "meta":  # ExifTool replaces a file in one step: it holds either all or none
                Path(metadata.leftover(src)).unlink(missing_ok=True)
                if os.path.lexists(src) and metadata.written(src, json.loads(r["before"])["new"]):
                    _finish(db, r)
                    fixed += 1
                else:
                    _skip(db, r, "interrupted before it was done")
        db.execute("UPDATE ops SET state='interrupted', finished=? WHERE id=?", (_now(), op["id"]))
        db.commit()
        notes.append(f"{op['summary']}: interrupted; {fixed} file(s) finished on recovery")
    return notes


def _second_phase_started(db, op_id) -> bool:
    """Had any of the operation's moved files reached its destination? (Then every file was already
    at its hidden name, and the moves are finished; if not, they're taken back, since files not yet
    moved may still hold the names wanted.) Also puts each staged file's row at its hidden name, for
    a crash that came between the two."""
    started = False
    for r in db.execute("SELECT * FROM op_steps WHERE op_id=? AND action='move'", (op_id,)).fetchall():
        temp = _hidden(r["src"], op_id, r["seq"], "moving")
        if r["done"] == FINISHED or (r["done"] == UNDER_WAY and not os.path.lexists(temp)):
            started = True
        elif r["done"] == PLANNED and os.path.lexists(temp):
            db.execute("UPDATE photos SET path=? WHERE path=?", (temp, r["src"]))
            _set(db, r, done=UNDER_WAY)
    return started


def _recover_move(db, op_id, r, started: bool) -> int:
    """Finish (or take back) one move a crash interrupted. Returns 1 if it ends up done."""
    src, dst = r["src"], r["dst"]
    temp = _hidden(src, op_id, r["seq"], "moving")
    if not os.path.lexists(temp):
        if r["done"] == UNDER_WAY and os.path.lexists(dst):  # it arrived; only the bookkeeping was left
            _finish(db, r)
            return 1
        db.execute("UPDATE photos SET path=? WHERE path=?", (src, temp))  # it was put back
        _skip(db, r, "interrupted before it started")
        return 0
    if os.path.lexists(dst) and filecmp.cmp(temp, dst, shallow=False):  # copied across drives, not yet removed
        os.unlink(temp)
        _finish(db, r)
        return 1
    if not started and not os.path.lexists(src):  # take it back
        rename_noreplace(temp, src)
        db.execute("UPDATE photos SET path=? WHERE path=?", (src, temp))
        _skip(db, r, "interrupted before it started")
        return 0
    dst = _free(db, r, dst)
    move_file(temp, dst, _hidden(dst, op_id, r["seq"], "partial"))
    _finish(db, r, dst)
    return 1


# --- undo and redo ----------------------------------------------------------------------------------


def inverse(db, op_id: int) -> list[dict]:
    """The steps that take an operation's finished steps back, last first."""
    out = []
    for r in db.execute("SELECT * FROM op_steps WHERE op_id=? AND done=? ORDER BY seq DESC", (op_id, FINISHED)):
        if r["action"] == "move":
            out.append(("move", r["dst"], r["src"], None))
        elif r["action"] in ("copy", "restore"):
            out.append(("trash", r["dst"], None, None))
        elif r["action"] == "trash":
            before = json.loads(r["before"] or "{}")
            out.append(("restore", r["dst"], r["src"], {"snapshot": before.get("snapshot")}))
        elif r["action"] == "meta":
            c = json.loads(r["before"])
            back = {"old": c["new"], "new": c["old"]}
            if c.get("orientation"):
                back["orientation"] = c["orientation"][::-1]
            out.append(("meta", r["src"], r["src"], back))
        elif r["action"] in INDEX_ONLY:
            c = json.loads(r["before"])
            out.append((r["action"], None, None, {"old": c["new"], "new": c["old"]}))
    moves = plan_moves(
        [(s, d) for a, s, d, _ in out if a == "move"],  # (trash making room goes first)
        freed=[s for a, s, _, _ in out if a == "trash"],
    )
    # a place a move empties first (restores come after moves) is free for a restore
    vacated = {os.path.normcase(m["src"]) for m in moves} - {os.path.normcase(m["dst"]) for m in moves}
    others = []
    taken = {}
    for a, s, d, before in out:
        if a == "trash":
            others.append({"action": "trash", "src": s, "dst": None})
        elif a == "restore":
            folder = os.path.dirname(d)
            mine = taken.setdefault(folder, set())
            free = os.path.normcase(d) in vacated and os.path.basename(d) not in mine
            name = os.path.basename(d) if free else free_name(folder, os.path.basename(d), mine)
            taken[folder].add(name)
            others.append({"action": "restore", "src": s, "dst": os.path.join(folder, name), "before": before})
        elif a == "meta":
            others.append({"action": "meta", "src": s, "dst": s, "before": before})
        elif a in INDEX_ONLY:
            others.append({"action": a, "src": None, "dst": None, "before": before})
    return moves + others


def can_undo(db):
    """The operation Ctrl+Z would take back, or None."""
    return db.execute(
        "SELECT * FROM ops WHERE state IN ('done', 'interrupted') AND kind != 'undo' ORDER BY id DESC LIMIT 1"
    ).fetchone()


def can_redo(db):
    """The undo that Ctrl+Shift+Z would take back, or None. Doing anything new clears it."""
    newest = db.execute("SELECT MAX(id) FROM ops WHERE kind NOT IN ('undo', 'redo')").fetchone()[0] or 0
    return db.execute(
        "SELECT * FROM ops WHERE kind='undo' AND state IN ('done', 'interrupted') AND id > ? ORDER BY id DESC LIMIT 1",
        (newest,),
    ).fetchone()


def undo(db, progress=None) -> Result | None:
    op = can_undo(db)
    if op is None:
        return None
    result = run(db, "undo", f"Undo: {op['summary']}", inverse(db, op["id"]), progress, undoes=op["id"])
    db.execute("UPDATE ops SET state='undone' WHERE id=?", (op["id"],))
    db.commit()
    return result


def redo(db, progress=None) -> Result | None:
    u = can_redo(db)
    if u is None:
        return None
    original = db.execute("SELECT * FROM ops WHERE id=?", (u["undoes"],)).fetchone()
    result = run(
        db, "redo", original["summary"] if original else "Redo", inverse(db, u["id"]), progress, undoes=u["id"]
    )
    db.execute("UPDATE ops SET state='undone' WHERE id=?", (u["id"],))
    db.commit()
    return result
