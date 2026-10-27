"""Scanning folders and keeping the index in step with them."""

import os
import sqlite3
import sys
import time
from pathlib import Path

from .ai import ClipImage, Faces
from .config import MISSING_MIN, MISSING_SHARE, data_dir
from .db import open_db, to_blob
from .media import IMAGE_EXTS, READER, date_taken, load_photo
from .people import auto_assign
from .thumbs import photo_thumb, prune_thumbs
from .util import quick_hash

IGNORE_MARKER = ".photofind-ignore"  # a folder holding this file is never indexed (e.g. the app's own copies)


def scan_folder(root: Path):
    """Every photo under root with its stat, plus what index_folder needs to tell a deleted photo from
    one that's out of reach: paths that couldn't be read (folders or files), the folders that were looked
    through, the empty ones among them (an unmounted drive's mount point looks just like one) and folders
    marked to be ignored."""
    skip = data_dir().resolve()
    files, unreadable, seen, empty, ignored = {}, set(), set(), set(), set()
    for dirpath, dirnames, filenames in os.walk(root, onerror=lambda e: unreadable.add(os.path.normpath(e.filename))):
        if IGNORE_MARKER in filenames:
            ignored.add(dirpath)
            dirnames[:] = []
            continue
        seen.add(dirpath)
        if not dirnames and not filenames:
            empty.add(dirpath)
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and Path(dirpath, d).resolve() != skip)
        for name in sorted(filenames):
            if not name.startswith(".") and Path(name).suffix.lower() in IMAGE_EXTS:
                p = os.path.join(dirpath, name)
                try:
                    files[p] = os.stat(p)
                except OSError:
                    unreadable.add(p)
    return files, unreadable, seen, empty, ignored


def _inside(path: str, folders: set) -> bool:
    """Is path one of folders, or anywhere inside one?"""
    while folders:
        if path in folders:
            return True
        parent = os.path.dirname(path)
        if parent == path:
            return False
        path = parent
    return False


def index_folder(root, want_faces=True, on_progress=None, should_stop=None, deadline=None,
                 forget_missing=False) -> dict:
    """Index (or update) one folder. Safe to stop and resume any time.

    on_progress(info) is called with a dict:
      phase: "scanning" | "loading" | "indexing" | "done"
      found, todo, done, faces, errors, moved, removed, rate (photos/s), eta (seconds),
      unreachable (kept: in folders that couldn't be read), missing (kept: see forget_missing)
    should_stop() is polled between photos; return True to stop early.
    forget_missing: remove photos that can't be found even when so many vanished at once that it
      looks like an unplugged drive (normally they're kept and reported as "missing").
    """
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Folder not found: {root}")
    info = dict(phase="scanning", root=str(root), found=0, todo=0, done=0, faces=0, errors=0, moved=0,
                removed=0, unreachable=0, missing=0, rate=0.0, eta=None, stopped=False, tagged=0)
    report = on_progress or (lambda i: None)
    stop = should_stop or (lambda: False)
    report(info)

    db = open_db()
    on_disk, unreadable, seen, empty, ignored = scan_folder(root)
    info["found"] = len(on_disk)

    prefix = str(root).rstrip(os.sep) + os.sep
    known = {
        r["path"]: r
        for r in db.execute(
            "SELECT id, path, size, mtime, qhash, clip IS NOT NULL AS has_clip, faces_done, error, error_reader "
            "FROM photos WHERE substr(path, 1, ?) = ?",
            (len(prefix), prefix),
        )
    }

    todo = []
    for path, st in on_disk.items():
        r = known.get(path)
        unchanged = r is not None and r["size"] == st.st_size and abs(r["mtime"] - st.st_mtime) < 1
        if unchanged and r["error"] and r["error_reader"] == READER:
            continue  # couldn't be read, and nothing has changed since
        if unchanged and not r["error"] and r["has_clip"] and (r["faces_done"] or not want_faces):
            continue
        todo.append((path, st, r if unchanged else None))

    # Photos no longer where we last saw them. Sort out which really are gone:
    excluded, vanished = [], []
    for p, r in known.items():
        if p in on_disk:
            continue
        if _inside(p, ignored):
            excluded.append(r)          # its folder is now marked to be ignored: drop it
        elif _inside(p, unreadable):
            info["unreachable"] += 1    # its folder couldn't be read right now: keep it
        else:
            vanished.append(r)

    # Vanished photos may have been moved or renamed: same content at a path we haven't seen before.
    # Several can share a fingerprint (exact copies), so match them one by one.
    gone = {}
    for r in vanished:
        if r["qhash"]:
            gone.setdefault(r["qhash"], []).append(r)
    found_again = set()
    if gone:
        still_todo = []
        for path, st, r in todo:
            if path not in known:
                same = gone.get(quick_hash(Path(path), st.st_size))
                if same:
                    old = same.pop()
                    found_again.add(old["id"])
                    db.execute("UPDATE photos SET path=?, mtime=? WHERE id=?", (path, st.st_mtime, old["id"]))
                    info["moved"] += 1
                    if old["has_clip"] and not old["error"] and (old["faces_done"] or not want_faces):
                        continue
            still_todo.append((path, st, r))
        todo = still_todo
    vanished = [r for r in vanished if r["id"] not in found_again]

    # A lot vanishing at once looks more like an unplugged drive or a glitch than a deletion, so those
    # are kept (and reported as missing) until the user says to forget them: a whole folder of
    # MISSING_MIN or more that emptied or disappeared, more than MISSING_SHARE of everything, or every
    # photo in the folder. Smaller deletions, even of a whole small folder, are simply removed.
    if vanished and not forget_missing:
        def gone_folder(path):  # the outermost folder (below root) that has emptied or disappeared
            top, d = None, os.path.dirname(path)
            while len(d) > len(prefix) - 1 and (d not in seen or d in empty):
                top, d = d, os.path.dirname(d)
            return top

        by_folder = {}
        for r in vanished:
            by_folder.setdefault(gone_folder(r["path"]), []).append(r)
        keep = [r for top, rs in by_folder.items() if top and len(rs) >= MISSING_MIN for r in rs]
        if (len(vanished) >= MISSING_MIN and len(vanished) > MISSING_SHARE * len(known)) or not on_disk:
            keep = vanished
        info["missing"] = len(keep)
        kept = {r["id"] for r in keep}
        vanished = [r for r in vanished if r["id"] not in kept]
    removed = [r["id"] for r in excluded + vanished]
    db.executemany("DELETE FROM photos WHERE id=?", [(i,) for i in removed])
    db.commit()
    info.update(removed=len(removed), todo=len(todo))

    if todo:
        info["phase"] = "loading"
        report(info)
        clip = ClipImage()
        faces = Faces() if want_faces else None
        info["phase"] = "indexing"
        report(info)
        start = time.time()
        try:
            for path, st, prev in todo:
                if stop() or (deadline and time.time() > deadline):
                    info["stopped"] = True
                    break
                try:
                    img = load_photo(Path(path))
                except Exception as e:  # the file itself can't be read: note why, and with which reader
                    info["errors"] += 1
                    db.execute(
                        "INSERT INTO photos (path, size, mtime, error, error_reader) VALUES (?,?,?,?,?) "
                        "ON CONFLICT(path) DO UPDATE SET size=excluded.size, mtime=excluded.mtime, "
                        "error=excluded.error, error_reader=excluded.error_reader",
                        (path, st.st_size, st.st_mtime, f"{type(e).__name__}: {e}"[:300], READER),
                    )
                else:
                    try:
                        info["faces"] += _index_photo(db, clip, faces, path, st, prev, img)
                    except sqlite3.Error:
                        raise  # the database is in trouble: stop, rather than blame every photo
                    except Exception as e:  # our side hiccuped, not the photo: not recorded, so it's retried next time
                        info["errors"] += 1
                        print(f"index: {path}: {e!r}", file=sys.stderr)
                info["done"] += 1
                if info["done"] % 25 == 0:
                    db.commit()
                    if want_faces and info["done"] % 200 == 0:
                        info["tagged"] += auto_assign(db)  # recognize named people as we go
                rate = info["done"] / max(time.time() - start, 1e-6)
                info.update(rate=rate, eta=(len(todo) - info["done"]) / max(rate, 1e-6))
                report(info)
        finally:
            db.commit()

    if want_faces:
        info["tagged"] += auto_assign(db)
    if todo or removed:
        prune_thumbs(db)
    db.close()
    info["phase"] = "done"
    report(info)
    return info


def _index_photo(db, clip, faces, path, st, prev, img) -> int:
    """Store one decoded photo's search vector, date, fingerprint and faces. Returns the number of faces."""
    p = Path(path)
    need_clip = prev is None or not prev["has_clip"] or bool(prev["error"])
    vec = clip.embed(img) if need_clip else None
    found = faces.find(img) if faces else []
    taken, source = date_taken(p, st.st_mtime)
    qh = quick_hash(p, st.st_size)
    if need_clip:
        db.execute(
            "INSERT INTO photos (path, size, mtime, qhash, taken, taken_source, clip, faces_done, error, error_reader) "
            "VALUES (?,?,?,?,?,?,?,?,NULL,NULL) ON CONFLICT(path) DO UPDATE SET size=excluded.size, "
            "mtime=excluded.mtime, qhash=excluded.qhash, taken=excluded.taken, "
            "taken_source=excluded.taken_source, clip=excluded.clip, "
            "faces_done=excluded.faces_done, error=NULL, error_reader=NULL",
            (path, st.st_size, st.st_mtime, qh, taken, source, to_blob(vec), int(bool(faces))),
        )
    else:
        db.execute("UPDATE photos SET faces_done=1 WHERE path=?", (path,))
    pid = db.execute("SELECT id FROM photos WHERE path=?", (path,)).fetchone()[0]
    if faces:
        db.execute("DELETE FROM faces WHERE photo_id=?", (pid,))
        db.executemany(
            "INSERT INTO faces (photo_id, x, y, w, h, score, emb) VALUES (?,?,?,?,?,?,?)",
            [(pid, x, y, w, h, s, to_blob(v)) for x, y, w, h, s, v in found],
        )
    try:  # the photo is already decoded, so its thumbnail is nearly free now
        photo_thumb(qh, path, img)
    except OSError:
        pass  # not worth failing the photo over; it's made on demand later
    return len(found)
