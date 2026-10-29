"""The portable copy: everything HomePix knows about a library folder's photos, kept inside that
folder (.homepix/library.db), so copying the folder to another computer brings it all along.

The app works on its own database in the data folder, and writes each folder's copy after changes
(write_copy). On another computer, adding the folder offers to bring its copy in (read_copy). Each
photo is found by its content, not by where it was, so the folder can be anywhere, on any system.

In the copy: the photos' search vectors, dates and details, their faces and who they are, the people
named, the pairs said not to be duplicates, and the folders left out of the library. Paths are
relative to the folder, with / between folders. Not in it: the undo history (it names files on the
computer it was made on) and thumbnails (made again as needed).

Each copy carries a library id. A copy written by another library (one that was never brought in
here) is never overwritten: it's kept beside the new one as library-<date>.db.
"""

import datetime as dt
import json
import os
import shutil
import sqlite3
import threading
import uuid
from pathlib import Path

from .. import __version__
from .config import data_dir, db_file
from .db import LATEST, NewerDatabaseError, open_db, upgrade
from .settings import load_settings, save_settings, set_excluded
from .util import quick_hash

FOLDER = ".homepix"
OLD_FOLDER = ".photofind"  # (Photo Finder's name for it: still read, and renamed when next written)
NAME = "library.db"
NOTE = "About this folder.txt"
NOTE_TEXT = """\
HomePix keeps a copy of what it knows about the photos in this folder here: the people you
named and their faces, the search index, your duplicate choices and the folders you left out.

Copy it along with your photos (it comes along when you copy the whole folder). HomePix on
another computer then offers to pick up where you left off, without looking through every photo again.

It's updated by itself a few seconds after each change, and whenever HomePix closes. Deleting it
loses nothing on this computer, but then another computer would start from scratch.
"""


def _folder(root, moving: bool = False) -> Path:
    """The folder root's copy is in: Photo Finder's, if that's the one there (moving: renamed first)."""
    new, old = Path(root) / FOLDER, Path(root) / OLD_FOLDER
    if not new.exists() and old.is_dir():
        if not moving:
            return old
        try:
            old.rename(new)
        except OSError:
            return old  # (a read-only drive, say: it stays as it is)
    return new


def copy_path(root) -> Path:
    return _folder(root) / NAME


def _prefix(root) -> str:
    return str(root).rstrip(os.sep) + os.sep


def stamp() -> str:
    """When the database last changed (its file and its write-ahead log), to tell if a copy is current."""
    def st(p):
        try:
            s = p.stat()
            return [s.st_mtime_ns, s.st_size]
        except OSError:
            return [0, 0]

    path = db_file()
    wal = st(path.with_name(path.name + "-wal"))
    return json.dumps([st(path)[0], wal if wal[1] else [0, 0]])


def _owned(root) -> str | None:
    return load_settings().get("copies", {}).get(str(root))


def _own(root, library: str):
    s = load_settings()
    s.setdefault("copies", {})[str(root)] = library
    save_settings(s)


def info(root) -> dict | None:
    """What root's copy says about itself ({library, written, photos, people, version, source}), or
    None if there's none (or it can't be read)."""
    path = copy_path(root)
    if not path.is_file():
        return None
    try:
        con = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
        try:
            meta = dict(con.execute("SELECT key, value FROM portable"))
            meta["version"] = con.execute("PRAGMA user_version").fetchone()[0]
        finally:
            con.close()
    except sqlite3.Error:
        return None
    for k in ("photos", "people"):
        meta[k] = int(meta.get(k) or 0)
    return meta


def photos_in(db, root) -> int:
    prefix = _prefix(root)
    return db.execute("SELECT COUNT(*) FROM photos WHERE substr(path, 1, ?) = ?", (len(prefix), prefix)).fetchone()[0]


def offered(root) -> dict | None:
    """root's copy, if it has one to bring in: there's a copy and none of the folder's photos are in
    the database yet (a new computer, or the folder was taken out of the library and added again)."""
    meta = info(root)
    if meta is None or not meta["photos"]:
        return None
    db = open_db()
    try:
        return None if photos_in(db, root) else meta
    finally:
        db.close()


def _hide(folder: Path):
    if os.name == "nt":  # a leading dot hides it everywhere else
        try:
            import ctypes

            ctypes.windll.kernel32.SetFileAttributesW(str(folder), 0x2)  # FILE_ATTRIBUTE_HIDDEN
        except Exception:
            pass


def _set_aside(path: Path, meta: dict | None):
    """Keep another library's copy (or one that can't be read) beside the new one."""
    when = (meta or {}).get("written", "")[:10] or dt.date.today().isoformat()
    n, dst = 0, path.with_name(f"library-{when}.db")
    while dst.exists():
        n += 1
        dst = path.with_name(f"library-{when}_{n}.db")
    os.replace(path, dst)


_lock = threading.RLock()  # one copy written at a time (the app's background one, and on closing)


def write_copy(root) -> int | None:
    """Write root's copy from the database now. Returns the number of photos in it, or None if it
    wasn't written: the folder isn't there (an unplugged drive), or the database has none of its photos
    (a copy is never replaced by an empty one). Raises OSError if the folder can't be written to."""
    with _lock:
        return _write_copy(Path(root))


def _write_copy(root: Path) -> int | None:
    if not root.is_dir():
        return None
    prefix = _prefix(root)
    source = stamp()  # taken first: a change made while copying leaves the copy older than the database
    work = data_dir() / "portable-out.tmp.db"
    work.unlink(missing_ok=True)
    db = open_db()
    try:
        if not photos_in(db, root):
            return None
        out = sqlite3.connect(work)
        db.backup(out)
    finally:
        db.close()
    folder = _folder(root, moving=True)
    tmp = folder / (NAME + ".tmp")
    try:
        out.execute("PRAGMA foreign_keys = ON")
        out.execute("PRAGMA journal_mode = OFF")  # a scratch copy: nothing to protect
        out.execute("DELETE FROM op_steps")
        out.execute("DELETE FROM ops")
        n = len(prefix)
        out.execute("DELETE FROM photos WHERE substr(path, 1, ?) != ?", (n, prefix))
        out.execute("DELETE FROM sidecars WHERE substr(path, 1, ?) != ?", (n, prefix))
        for table in ("photos", "sidecars"):
            out.execute(f"UPDATE {table} SET path = replace(substr(path, ?), ?, '/')", (n + 1, os.sep))
        out.execute("DELETE FROM people WHERE id NOT IN (SELECT person_id FROM faces WHERE person_id IS NOT NULL)")
        library = _owned(root)
        if library is None:
            library = uuid.uuid4().hex
            _own(root, library)
        excluded = [os.path.relpath(f, root).replace(os.sep, "/") for f in load_settings().get("excluded", [])
                    if f.startswith(prefix)]
        meta = {
            "library": library,
            "written": dt.datetime.now().isoformat(timespec="seconds"),
            "app": __version__,
            "photos": out.execute("SELECT COUNT(*) FROM photos").fetchone()[0],
            "people": out.execute("SELECT COUNT(*) FROM people").fetchone()[0],
            "excluded": json.dumps(excluded),
            "source": source,
        }
        out.execute("CREATE TABLE portable (key TEXT PRIMARY KEY, value TEXT)")
        out.executemany("INSERT INTO portable VALUES (?, ?)", [(k, str(v)) for k, v in meta.items()])
        out.commit()
        folder.mkdir(exist_ok=True)
        _hide(folder)
        if not (folder / NOTE).exists():
            (folder / NOTE).write_text(NOTE_TEXT, encoding="utf-8")
        tmp.unlink(missing_ok=True)
        out.execute("VACUUM INTO ?", (str(tmp),))  # compact, and written to the folder only once
    finally:
        out.close()
        work.unlink(missing_ok=True)
    done = sqlite3.connect(tmp)
    try:  # an ordinary file, readable from a read-only drive (a WAL database there can't be opened)
        done.execute("PRAGMA journal_mode = DELETE")
    finally:
        done.close()
    dst = folder / NAME
    if dst.exists():
        there = info(root)
        if there is None or there.get("library") != library:
            _set_aside(dst, there)
    os.replace(tmp, dst)  # all at once: a copy is never half written
    return meta["photos"]


def current(root) -> bool:
    """Is root's copy up to date: written here, since the database last changed?"""
    meta = info(root)
    return meta is not None and meta.get("library") == _owned(root) and meta.get("source") == stamp()


def refresh_copies(roots=None) -> list[str]:
    """Write the copy of every library folder (or of roots) whose copy isn't current. Returns the
    folders written. A folder that can't be written to (read-only, full) is skipped and reported."""
    from .settings import library_folders

    written = []
    with _lock:
        for root in library_folders() if roots is None else roots:
            if current(root):
                continue
            try:
                if write_copy(root) is not None:
                    written.append(root)
            except (OSError, sqlite3.Error) as e:
                print(f"Couldn't save the library's copy in {root}: {e!r}", flush=True)
    return written


def read_copy(root) -> dict:
    """Bring root's copy into the database. Each photo in it that's in the folder now is found where it
    was (if that file hasn't changed) or else by its content, anywhere in the folder; it comes in with its
    faces and who they are, and the pairs said not to be duplicates. People are matched by name. Photos
    already in the database are left as they are; photos no longer there are left out (and anything new is
    found by the next indexing). Returns {"photos": brought in, "missing": not found, "people": named}."""
    from .library import scan_folder

    root = Path(root)
    prefix = _prefix(root)
    work = data_dir() / "portable-in.tmp.db"
    shutil.copyfile(copy_path(root), work)  # never change the copy itself: it may be on a read-only drive
    cp = sqlite3.connect(work)
    cp.row_factory = sqlite3.Row
    try:
        version = cp.execute("PRAGMA user_version").fetchone()[0]
        if version > LATEST:
            raise NewerDatabaseError(f"This folder's library was saved by a newer HomePix (database "
                                     f"version {version}; this one knows up to {LATEST}). Update HomePix first.")
        upgrade(cp)
        meta = dict(cp.execute("SELECT key, value FROM portable").fetchall())
        rows = cp.execute("SELECT * FROM photos").fetchall()
        on_disk = scan_folder(root)[0]
        db = open_db()
        try:
            claimed = {r[0] for r in db.execute("SELECT path FROM photos WHERE substr(path, 1, ?) = ?",
                                                (len(prefix), prefix))}
            found = {}  # the copy's photo id: (its path now, its modified time now)
            waiting = {}  # quick hash: the copy's photos not where they were
            for r in rows:
                p = prefix + r["path"].replace("/", os.sep)
                st = on_disk.get(p)
                if p not in claimed and st is not None and st.st_size == r["size"] \
                        and r["mtime"] is not None and abs(st.st_mtime - r["mtime"]) < 1:
                    found[r["id"]] = (p, st.st_mtime)
                    claimed.add(p)
                elif r["qhash"]:
                    waiting.setdefault(r["qhash"], []).append(r)
            sizes = {r["size"] for rs in waiting.values() for r in rs}
            for p, st in on_disk.items():  # moved, renamed or copied with a new modified time
                if not waiting:
                    break
                if p in claimed or st.st_size not in sizes:
                    continue
                try:
                    qh = quick_hash(Path(p), st.st_size)
                except OSError:
                    continue
                same = waiting.get(qh)
                if same:
                    found[same.pop(0)["id"]] = (p, st.st_mtime)
                    claimed.add(p)
                    if not same:
                        del waiting[qh]
            named = _bring_in(db, cp, found, prefix)
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()
    finally:
        cp.close()
        work.unlink(missing_ok=True)
    for f in json.loads(meta.get("excluded") or "[]"):
        set_excluded(prefix + f.replace("/", os.sep), True)
    if meta.get("library"):
        _own(root, meta["library"])
    return {"photos": len(found), "missing": len(rows) - len(found), "people": named}


def _columns(con, table) -> list[str]:
    return [r[1] for r in con.execute(f"PRAGMA table_info({table})")]


def _bring_in(db, cp, found: dict, prefix: str) -> int:
    """Copy the found photos' rows (and what hangs off them) from the copy into the database. Returns
    how many people they're of."""
    cols = [c for c in _columns(cp, "photos") if c != "id" and c in _columns(db, "photos")]
    new = {}
    for r in cp.execute(f"SELECT id, {', '.join(cols)} FROM photos"):
        if r["id"] not in found:
            continue
        path, mtime = found[r["id"]]
        values = [path if c == "path" else mtime if c == "mtime" else r[c] for c in cols]
        cur = db.execute(f"INSERT INTO photos ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", values)
        new[r["id"]] = cur.lastrowid
    people = {}
    for pid, name in cp.execute("SELECT id, name FROM people"):
        row = db.execute("SELECT id FROM people WHERE name = ?", (name,)).fetchone()
        people[pid] = row[0] if row else db.execute("INSERT INTO people (name) VALUES (?)", (name,)).lastrowid
    shift = (db.execute("SELECT MAX(cluster) FROM faces").fetchone()[0] or 0) + 1  # its groups stay its own
    fcols = [c for c in _columns(cp, "faces") if c != "id" and c in _columns(db, "faces")]
    named = set()
    for f in cp.execute(f"SELECT {', '.join(fcols)} FROM faces"):
        if f["photo_id"] not in new:
            continue
        values = []
        for c in fcols:
            v = f[c]
            if c == "photo_id":
                v = new[v]
            elif c == "person_id" and v is not None:
                v = people.get(v)
                named.add(v)
            elif c == "cluster" and v is not None:
                v += shift
            values.append(v)
        db.execute(f"INSERT INTO faces ({', '.join(fcols)}) VALUES ({', '.join('?' * len(fcols))})", values)
    for photo_id, path, kind in cp.execute("SELECT photo_id, path, kind FROM sidecars"):
        if photo_id in new:
            db.execute("INSERT OR IGNORE INTO sidecars (photo_id, path, kind) VALUES (?, ?, ?)",
                       (new[photo_id], prefix + path.replace("/", os.sep), kind))
    for a, b in cp.execute("SELECT a, b FROM apart"):
        if a in new and b in new:
            db.execute("INSERT OR IGNORE INTO apart (a, b) VALUES (?, ?)", sorted((new[a], new[b])))
    named.discard(None)
    return len(named)
