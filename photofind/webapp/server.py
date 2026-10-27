"""
Photo Finder - the desktop app for photofind.

Starts a small web server that only this computer can reach, then shows the
interface in its own app window (pywebview: Qt on Linux, the built-in Edge
engine on Windows). If that window can't be created, it falls back to a
browser window. Closing the window quits the app.
"""

import itertools
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path

import numpy as np

from photofind import core as pf
from photofind.core.ai.models import _ort_session

UI_FILE = Path(__file__).with_name("ui.html")
# When bundled into a single app (PyInstaller), extra files live in a temporary folder.
ICON = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2])) / "photofind-icon.png"

RESCAN_EVERY = 30 * 60  # look for new photos every 30 minutes while the app is open
MAX_RESULTS = 200


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------

def settings_path() -> Path:
    return pf.data_dir() / "settings.json"


def load_settings() -> dict:
    try:
        return json.loads(settings_path().read_text(encoding="utf-8"))
    except Exception:
        return {"folders": []}


def save_settings(s: dict):
    tmp = settings_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=2), encoding="utf-8")
    tmp.replace(settings_path())


def pictures_folder() -> Path:
    """The user's Pictures folder, wherever their system keeps it."""
    if os.name != "nt" and shutil.which("xdg-user-dir"):
        try:
            out = subprocess.run(["xdg-user-dir", "PICTURES"], capture_output=True, text=True, timeout=3).stdout.strip()
            if out and Path(out).is_dir() and Path(out) != Path.home():
                return Path(out)
        except Exception:
            pass
    return Path.home() / "Pictures"


# --------------------------------------------------------------------------
# Background indexing
# --------------------------------------------------------------------------

class Indexer:
    def __init__(self):
        self.lock = threading.Lock()
        self.thread = None
        self.stop_flag = False
        self.again = False
        self.forget_missing = False  # next pass: also remove photos that can't be found
        self.state = {"running": False, "phase": "idle", "done": 0, "todo": 0, "eta": None, "error": None,
                      "folder": None, "last_run": None, "missing": 0}

    def start(self, forget_missing=False):
        with self.lock:
            self.forget_missing |= forget_missing
            if self.thread and self.thread.is_alive():
                self.again = True  # finish this pass, then go again (e.g. a folder was added)
                return
            self.stop_flag = False
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()

    def stop(self):
        self.stop_flag = True

    def _progress(self, info):
        with self.lock:
            self.state.update(phase=info["phase"], done=info["done"], todo=info["todo"],
                              eta=info["eta"], folder=info["root"], running=True)

    def _run(self):
        pf.be_nice()
        while True:
            self.again = False
            with self.lock:
                self.state.update(running=True, error=None, phase="scanning", done=0, todo=0, eta=None)
                forget, self.forget_missing = self.forget_missing, False
            missing = 0
            for folder in load_settings().get("folders", []):
                if self.stop_flag:
                    break
                if not Path(folder).is_dir():
                    continue  # e.g. an unplugged drive; its photos stay searchable
                try:
                    info = pf.index_folder(folder, True, self._progress, lambda: self.stop_flag or self.again,
                                           forget_missing=forget)
                    missing += info["missing"]
                except pf.ModelDownloadError as e:
                    with self.lock:
                        self.state["error"] = (
                            "Couldn't download what Photo Finder needs to recognize photos. "
                            "Check your internet connection, then choose Check for new photos in Settings."
                        )
                    print(e, file=sys.stderr)
                    break
                except Exception as e:
                    with self.lock:
                        self.state["error"] = f"Something went wrong while looking through {folder}: {e}"
                    print(f"index error: {e!r}", file=sys.stderr)
            with self.lock:
                self.state["missing"] = missing
            if not self.again or self.stop_flag:
                break
        with self.lock:
            self.state.update(running=False, phase="idle", last_run=time.time())
        FACES.invalidate()

    def snapshot(self):
        with self.lock:
            return dict(self.state)


INDEXER = Indexer()


def rescan_loop():
    while True:
        time.sleep(60)
        last = INDEXER.snapshot()["last_run"] or 0
        if not INDEXER.snapshot()["running"] and time.time() - last > RESCAN_EVERY:
            INDEXER.start()


# --------------------------------------------------------------------------
# Search and faces
# --------------------------------------------------------------------------

_clip_text = None
_clip_lock = threading.Lock()


def clip_text():
    global _clip_text
    with _clip_lock:
        if _clip_text is None:
            _clip_text = pf.ClipText()
        return _clip_text


def photo_item(r, score=None):
    return {
        "id": r["id"],
        "date": (r["taken"] or "")[:10],
        "name": Path(r["path"]).name,
        "v": r["qhash"],  # changes with the photo's content, so the browser never shows a stale copy
        "score": None if score is None else round(float(score), 3),
    }


class Embeddings:
    """Every photo's search vector, kept in memory: reading them all from SQLite took
    far longer than the search itself. Reloaded only after the database changes."""

    def __init__(self):
        self.lock = threading.Lock()
        self.db = None      # a connection of our own, so data_version can tell us about others' writes
        self.version = None
        self.built = 0.0
        self.rows, self.ids, self.mat = [], np.zeros(0, np.int64), np.zeros((0, 512), np.float32)

    def get(self):
        with self.lock:
            if self.db is None:
                self.db = pf.open_db()
            version = self.db.execute("PRAGMA data_version").fetchone()[0]
            # while indexing the database changes constantly: reload at most every 10 seconds
            settling = INDEXER.snapshot()["running"] and time.time() - self.built < 10
            if version != self.version and not settling:
                rows = self.db.execute("SELECT id, path, taken, qhash, clip FROM photos WHERE clip IS NOT NULL").fetchall()
                self.rows = [{"id": r["id"], "path": r["path"], "taken": r["taken"], "qhash": r["qhash"]} for r in rows]
                self.ids = np.array([r["id"] for r in rows], np.int64)
                if rows:
                    blob = b"".join(r["clip"] for r in rows)
                    self.mat = np.frombuffer(blob, np.float16).reshape(len(rows), -1).astype(np.float32)
                else:
                    self.mat = np.zeros((0, 512), np.float32)
                self.version, self.built = version, time.time()
            return self.rows, self.ids, self.mat


EMBEDDINGS = Embeddings()


def search(q: str, person_id=None) -> dict:
    nothing = {"total": 0, "matches": [], "related": []}
    if not q:  # newest first: let SQLite sort, no vectors needed
        where, params = "p.clip IS NOT NULL", []
        if person_id:
            where += " AND EXISTS (SELECT 1 FROM faces f WHERE f.photo_id = p.id AND f.person_id = ?)"
            params.append(person_id)
        with closing(pf.open_db()) as db:
            total = db.execute(f"SELECT COUNT(*) FROM photos p WHERE {where}", params).fetchone()[0]
            rows = db.execute(
                f"SELECT p.id, p.path, p.taken, p.qhash FROM photos p WHERE {where} "
                f"ORDER BY p.taken DESC LIMIT {MAX_RESULTS}", params
            ).fetchall()
        if not total:
            return nothing
        return {"total": total, "matches": [photo_item(r) for r in rows], "related": [], "recent": True}

    rows, ids, mat = EMBEDDINGS.get()
    sel = np.arange(len(rows))
    if person_id:
        with closing(pf.open_db()) as db:
            theirs = [r[0] for r in db.execute("SELECT DISTINCT photo_id FROM faces WHERE person_id=?", (person_id,))]
        sel = np.flatnonzero(np.isin(ids, theirs))
        mat = mat[sel]
    if not len(sel):
        return nothing
    scores = mat @ clip_text().query(q)
    clear, near = pf.split_matches(scores)
    return {
        "total": len(sel),
        "matches": [photo_item(rows[sel[i]], scores[i]) for i in clear[:MAX_RESULTS]],
        "related": [photo_item(rows[sel[i]], scores[i]) for i in near],
    }


class FaceGroups:
    """Unnamed face groups for the "Who's this?" flow (cached; grouping isn't free)."""

    def __init__(self):
        self.lock = threading.Lock()        # guards the fields below
        self.build_lock = threading.Lock()  # one grouping at a time
        self.cache = []
        self.stale = True
        self.gen = 0                        # bumped by every change, so a grouping that raced one is redone
        self.skipped = set()                # face ids the user skipped this session

    def invalidate(self):
        with self.lock:
            self.stale = True
            self.gen += 1

    def _build(self):
        with self.build_lock:
            with self.lock:
                if not self.stale:
                    return
                gen = self.gen
            with closing(pf.open_db()) as db:
                pf.auto_assign(db)
                groups = pf.cluster_unnamed(db)
            groups = [
                [dict(id=r["id"], photo_id=r["photo_id"]) for r in sorted(g, key=lambda r: -r["score"] * r["w"])]
                for _, g in groups if len(g) >= 2
            ]
            with self.lock:
                self.cache = groups
                self.stale = gen != self.gen

    def _visible(self):
        with self.lock:
            return [g for g in self.cache if sum(f["id"] in self.skipped for f in g) * 2 < len(g)]

    def groups(self):
        """Up-to-date groups (waits for a regroup if needed)."""
        self._build()
        return self._visible()

    def count(self):
        """For the status poll: never waits. If out of date, regroups in the background
        and reports the last count meanwhile."""
        if self.stale and not self.build_lock.locked():
            threading.Thread(target=self._build, daemon=True).start()
        return len(self._visible())

    def drop_named(self, db):
        """After naming faces: drop them from the groups instead of regrouping everything."""
        unnamed = {r[0] for r in db.execute("SELECT id FROM faces WHERE person_id IS NULL")}
        with self.lock:
            self.gen += 1  # a regroup already under way read the database before this naming
            self.cache = [kept for g in self.cache if len(kept := [f for f in g if f["id"] in unnamed]) >= 2]

    def skip(self, face_ids):
        with self.lock:
            self.skipped.update(face_ids)


FACES = FaceGroups()


def people_list():
    with closing(pf.open_db()) as db:
        return [dict(r) for r in db.execute(
            "SELECT pe.id, pe.name, COUNT(DISTINCT f.photo_id) AS photos, "
            "(SELECT b.id FROM faces b WHERE b.person_id = pe.id ORDER BY b.score * b.w DESC LIMIT 1) AS face "
            "FROM people pe JOIN faces f ON f.person_id = pe.id GROUP BY pe.id ORDER BY photos DESC, pe.name"
        )]


def counts():
    with closing(pf.open_db()) as db:
        return {
            "photos": db.execute("SELECT COUNT(*) FROM photos WHERE clip IS NOT NULL").fetchone()[0],
            "faces": db.execute("SELECT COUNT(*) FROM faces").fetchone()[0],
        }


def get_row(sql, params):
    with closing(pf.open_db()) as db:
        return db.execute(sql, params).fetchone()


def open_on_computer(path: Path, reveal=False):
    target = path.parent if reveal else path
    if os.name == "nt":
        if reveal:
            subprocess.Popen(["explorer", "/select,", str(path)])
        else:
            os.startfile(str(path))  # noqa
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)] if reveal else ["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(target)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def copies_folder() -> Path:
    """Where "Copy all" puts its folders. Marked so Photo Finder doesn't index its own copies."""
    root = pictures_folder() / "Photo Finder"
    root.mkdir(parents=True, exist_ok=True)
    (root / pf.IGNORE_MARKER).touch()
    return root


def unused(path: Path, is_dir=False) -> Path:
    """path, or "name (2)", "name (3)"... if that's taken."""
    if not path.exists():
        return path
    stem, ext = (path.name, "") if is_dir else (path.stem, path.suffix)
    for n in itertools.count(2):
        p = path.with_name(f"{stem} ({n}){ext}")
        if not p.exists():
            return p


def copy_photos(name: str, ids: list) -> dict:
    """Copy photos (in the given order) into a new folder named after the search."""
    name = " ".join(re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", name).split()).strip(" .")[:80] or "Photos"
    marks = ",".join("?" * len(ids))
    with closing(pf.open_db()) as db:
        paths = dict(db.execute(f"SELECT id, path FROM photos WHERE id IN ({marks})", ids).fetchall())
    found = [Path(paths[i]) for i in ids if i in paths and Path(paths[i]).is_file()]
    if not found:
        raise FileNotFoundError("Those photos couldn't be found. Is their drive plugged in?")
    dest = unused(copies_folder() / name, is_dir=True)
    dest.mkdir()
    for src in found:
        shutil.copy2(src, unused(dest / src.name))  # copy2 keeps the photo's dates
    return {"folder": str(dest), "copied": len(found), "total": len(ids)}


def browse(path) -> dict:
    home = Path.home()
    shortcuts = [{"label": "Home", "path": str(home)}]
    pics = pictures_folder()
    if pics.is_dir():
        shortcuts.insert(0, {"label": "Pictures", "path": str(pics)})
    for name in ("Desktop", "Documents", "Downloads"):
        if (home / name).is_dir():
            shortcuts.append({"label": name, "path": str(home / name)})
    if os.name == "nt":
        import string

        for letter in string.ascii_uppercase:
            if Path(f"{letter}:\\").exists():
                shortcuts.append({"label": f"Drive {letter}:", "path": f"{letter}:\\"})
    else:
        for base in (Path("/media") / home.name, Path("/run/media") / home.name, Path("/mnt")):
            if base.is_dir():
                for d in sorted(base.iterdir()):
                    if d.is_dir():
                        shortcuts.append({"label": d.name, "path": str(d)})
    cur = Path(path).expanduser() if path else (pics if pics.is_dir() else home)
    try:
        cur = cur.resolve()
        dirs = sorted(
            (d for d in cur.iterdir() if d.is_dir() and not d.name.startswith(".")), key=lambda d: d.name.lower()
        )
        dirs = [{"name": d.name, "path": str(d)} for d in dirs[:500]]
        error = None
    except Exception:
        dirs, error = [], "This folder can't be opened."
    parent = str(cur.parent) if cur.parent != cur else None
    return {"path": str(cur), "parent": parent, "dirs": dirs, "shortcuts": shortcuts, "error": error}


# --------------------------------------------------------------------------
# Web server
# --------------------------------------------------------------------------

TOKEN = secrets.token_urlsafe(24)
LAST_SEEN = {"t": time.time()}


class Handler(BaseHTTPRequestHandler):
    server_version = "PhotoFinder"

    def log_message(self, *a):
        pass

    # --- plumbing
    def _send(self, code, body: bytes, ctype="application/json", cache=False):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "private, max-age=86400" if cache else "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj).encode())

    def _jpeg(self, path: Path):
        self._send(200, path.read_bytes(), "image/jpeg", cache=True)

    def _route(self):
        host = self.headers.get("Host", "")
        if host != f"127.0.0.1:{self.server.server_port}":  # blocks DNS-rebinding tricks
            return None, None
        url = urllib.parse.urlsplit(self.path)
        parts = url.path.split("/")
        if len(parts) < 3 or parts[1] != "t" or not secrets.compare_digest(parts[2], TOKEN):
            return None, None
        LAST_SEEN["t"] = time.time()
        return "/" + "/".join(parts[3:]), urllib.parse.parse_qs(url.query)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    # --- GET
    def do_GET(self):
        route, qs = self._route()
        if route is None:
            return self._send(404, b"Not found", "text/plain")
        q = lambda k, d=None: (qs.get(k) or [d])[0]
        try:
            if route in ("/", ""):
                return self._send(200, UI_FILE.read_bytes(), "text/html; charset=utf-8")
            if route == "/api/state":
                s = load_settings()
                return self._json({
                    "folders": s.get("folders", []),
                    "pictures": str(pictures_folder()) if pictures_folder().is_dir() else None,
                    "index": INDEXER.snapshot(),
                    "counts": counts(),
                    "people": people_list(),
                    "unnamed_groups": FACES.count(),
                })
            if route == "/api/who":
                groups = FACES.groups()
                return self._json({"groups": groups[:1], "remaining": len(groups),
                                   "preview": [g[0]["id"] for g in groups[:3]]})
            if route == "/api/search":
                pid = q("person")
                return self._json(search((q("q") or "").strip(), int(pid) if pid else None))
            if route == "/api/browse":
                return self._json(browse(q("path")))
            if route.startswith("/thumb/"):
                r = get_row("SELECT qhash, path FROM photos WHERE id=? AND qhash IS NOT NULL", (int(route.split("/")[2]),))
                return self._jpeg(pf.photo_thumb(r["qhash"], r["path"])) if r else self._send(404, b"", "text/plain")
            if route.startswith("/face/"):
                r = get_row(
                    "SELECT f.id, f.x, f.y, f.w, f.h, p.path, p.qhash FROM faces f JOIN photos p ON p.id=f.photo_id WHERE f.id=?",
                    (int(route.split("/")[2]),),
                )
                return self._jpeg(pf.face_thumb(r, 160)) if r else self._send(404, b"", "text/plain")
            if route.startswith("/photo/"):
                r = get_row("SELECT path FROM photos WHERE id=?", (int(route.split("/")[2]),))
                if not r:
                    return self._send(404, b"", "text/plain")
                img = pf.load_photo(Path(r["path"]), 2400)
                buf = BytesIO()
                img.save(buf, "JPEG", quality=88)
                return self._send(200, buf.getvalue(), "image/jpeg", cache=True)
            if route == "/icon.png":
                return self._send(200, ICON.read_bytes(), "image/png", cache=True)
            if route == "/api/show":  # someone opened the app again: bring the window forward
                show_window()
                return self._json({"ok": True})
            if route == "/api/ping":
                return self._json({"ok": True})
            return self._send(404, b"Not found", "text/plain")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            print(f"GET {route}: {e!r}", file=sys.stderr)
            self._json({"error": str(e)}, 500)

    # --- POST
    def do_POST(self):
        route, _ = self._route()
        if route is None:
            return self._send(404, b"Not found", "text/plain")
        if route == "/api/show":
            return self._json({"ok": show_window()})
        try:
            b = self._body()
            if route == "/api/folders/add":
                p = Path(b["path"]).expanduser().resolve()
                if not p.is_dir():
                    return self._json({"error": "That folder doesn't exist."}, 400)
                s = load_settings()
                folders = s.setdefault("folders", [])
                if any(p == Path(f) or Path(f) in p.parents for f in folders):
                    return self._json({"error": "That folder is already included."}, 400)
                # adding a parent folder replaces the folders inside it
                s["folders"] = [f for f in folders if p not in Path(f).parents] + [str(p)]
                save_settings(s)
                INDEXER.start()
                return self._json({"ok": True})
            if route == "/api/folders/remove":
                s = load_settings()
                s["folders"] = [f for f in s.get("folders", []) if f != b["path"]]
                save_settings(s)
                prefix = b["path"].rstrip(os.sep) + os.sep
                with closing(pf.open_db()) as db:
                    db.execute("DELETE FROM photos WHERE substr(path, 1, ?) = ?", (len(prefix), prefix))
                    db.commit()
                FACES.invalidate()
                return self._json({"ok": True})
            if route == "/api/rescan":
                INDEXER.start()
                return self._json({"ok": True})
            if route == "/api/forget-missing":  # "those photos were deleted on purpose"
                INDEXER.start(forget_missing=True)
                return self._json({"ok": True})
            if route == "/api/name":
                name = (b.get("name") or "").strip()
                ids = [int(i) for i in b.get("face_ids", [])]
                if not name or not ids:
                    return self._json({"error": "Type a name first."}, 400)
                with closing(pf.open_db()) as db:
                    db.execute("INSERT OR IGNORE INTO people (name) VALUES (?)", (name,))
                    pid = db.execute("SELECT id FROM people WHERE name=?", (name,)).fetchone()[0]
                    marks = ",".join("?" * len(ids))
                    db.execute(f"UPDATE faces SET person_id=?, cluster=NULL, locked=0 WHERE person_id IS NULL AND id IN ({marks})",
                               (pid, *ids))
                    db.commit()
                    pf.auto_assign(db)
                    FACES.drop_named(db)
                return self._json({"ok": True, "person": pid})
            if route == "/api/skip":
                FACES.skip(int(i) for i in b.get("face_ids", []))
                return self._json({"ok": True})
            if route == "/api/not":  # "this photo isn't Mom"
                with closing(pf.open_db()) as db:
                    db.execute("UPDATE faces SET person_id=NULL, cluster=NULL, locked=1 WHERE photo_id=? AND person_id=?",
                               (int(b["photo"]), int(b["person"])))
                    db.commit()
                FACES.invalidate()
                return self._json({"ok": True})
            if route == "/api/person/rename":
                name = (b.get("name") or "").strip()
                if not name:
                    return self._json({"error": "Type a name first."}, 400)
                with closing(pf.open_db()) as db:
                    clash = db.execute("SELECT id FROM people WHERE name=? AND id<>?", (name, int(b["id"]))).fetchone()
                    if clash:  # renaming to an existing person merges them
                        db.execute("UPDATE faces SET person_id=? WHERE person_id=?", (clash[0], int(b["id"])))
                        db.execute("DELETE FROM people WHERE id=?", (int(b["id"]),))
                    else:
                        db.execute("UPDATE people SET name=? WHERE id=?", (name, int(b["id"])))
                    db.commit()
                return self._json({"ok": True, "person": clash[0] if clash else int(b["id"])})
            if route == "/api/person/forget":
                with closing(pf.open_db()) as db:
                    db.execute("UPDATE faces SET person_id=NULL, cluster=NULL WHERE person_id=?", (int(b["id"]),))
                    db.execute("DELETE FROM people WHERE id=?", (int(b["id"]),))
                    db.commit()
                FACES.invalidate()
                return self._json({"ok": True})
            if route == "/api/copy":
                ids = [int(i) for i in b.get("ids", [])]
                if not ids:
                    return self._json({"error": "There are no photos to copy."}, 400)
                try:
                    return self._json(copy_photos(b.get("name") or "", ids))
                except FileNotFoundError as e:
                    return self._json({"error": str(e)}, 400)
            if route == "/api/open-folder":  # only folders "Copy all" made
                p = Path(b["path"]).resolve()
                if copies_folder().resolve() not in p.parents or not p.is_dir():
                    return self._json({"error": "That folder can't be opened."}, 400)
                open_on_computer(p)
                return self._json({"ok": True})
            if route == "/api/open":
                r = get_row("SELECT path FROM photos WHERE id=?", (int(b["id"]),))
                if r:
                    open_on_computer(Path(r["path"]), reveal=bool(b.get("reveal")))
                return self._json({"ok": bool(r)})
            return self._send(404, b"Not found", "text/plain")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            print(f"POST {route}: {e!r}", file=sys.stderr)
            self._json({"error": str(e)}, 500)


# --------------------------------------------------------------------------
# Window and lifetime
# --------------------------------------------------------------------------

def find_app_browser():
    """A Chromium-family browser can show the page as a plain app window."""
    names = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
             "brave-browser", "microsoft-edge", "microsoft-edge-stable", "vivaldi"]
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    if os.name == "nt":
        for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
            if not base:
                continue
            for rel in (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"):
                p = Path(base) / rel
                if p.exists():
                    return str(p)
    return None


def open_window(url: str):
    """Returns the window's process if we can tell when it closes, else None."""
    exe = find_app_browser()
    if exe:
        profile = pf.data_dir() / "window"
        try:
            proc = subprocess.Popen(
                [exe, f"--app={url}", f"--user-data-dir={profile}", "--window-size=1180,860",
                 "--no-first-run", "--no-default-browser-check", "--disable-sync"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            time.sleep(3)
            if proc.poll() is None:
                return proc
        except Exception:
            pass
    webbrowser.open(url)
    return None


WINDOW = None  # the pywebview window, when there is one


def show_window():
    if WINDOW is None:
        return False
    try:
        WINDOW.restore()
        WINDOW.show()
        WINDOW.on_top = True  # nudge it in front of other windows
        time.sleep(0.3)
        WINDOW.on_top = False
        return True
    except Exception:
        return False


def prepare_linux_window():
    if not sys.platform.startswith("linux"):
        return
    # Newer Ubuntu-based systems block the user namespaces Qt's web engine uses for its
    # sandbox, which leaves a blank window. The window only shows Photo Finder's own
    # local page, so turn that sandbox off only where the system blocks it.
    try:
        if Path("/proc/sys/kernel/apparmor_restrict_unprivileged_userns").read_text().strip() == "1":
            os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
    except Exception:
        pass
    # Running as an AppImage: add Photo Finder to the applications menu (once, or if it moved).
    appimage = os.environ.get("APPIMAGE")
    if appimage:
        try:
            share = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
            icon = pf.data_dir() / "photofind-icon.png"
            icon.write_bytes(ICON.read_bytes())
            entry = share / "applications" / "photofind.desktop"
            text = (
                "[Desktop Entry]\nType=Application\nName=Photo Finder\n"
                "Comment=Find your photos by describing them\n"
                f'Exec="{appimage}"\nIcon={icon}\nTerminal=false\n'
                "Categories=Graphics;Photography;\nKeywords=photos;pictures;search;faces;\n"
            )
            if not entry.exists() or entry.read_text() != text:
                entry.parent.mkdir(parents=True, exist_ok=True)
                entry.write_text(text)
        except Exception as e:
            print(f"Couldn't add a menu entry: {e!r}", flush=True)


def run_app_window(url: str) -> bool:
    """Show the app in its own window and wait until it's closed.
    Returns False if no app window could be created (then we use a browser)."""
    global WINDOW
    try:
        import webview
    except Exception as e:
        print(f"pywebview not available: {e!r}", flush=True)
        return False
    prepare_linux_window()
    try:
        WINDOW = webview.create_window(
            "Photo Finder", url, width=1180, height=860, min_size=(420, 520), background_color="#F2F4F6"
        )
        gui = "qt" if sys.platform.startswith("linux") else None  # Windows: built-in Edge WebView2
        func = self_test if "--self-test" in sys.argv else None
        webview.start(func, gui=gui, icon=str(ICON))
        return True
    except Exception as e:
        print(f"Couldn't open the app window, using a browser instead: {e!r}", flush=True)
        WINDOW = None
        return False


SELF_TEST = {"ok": False}


def self_test():
    """`--self-test`: check the window opens, the interface loads and the AI parts work, then quit."""
    try:
        import cv2  # noqa: F401
        import onnxruntime  # noqa: F401
        import tokenizers  # noqa: F401
        print("SELF-TEST: AI libraries load.", flush=True)
        if not pf.HEIF:
            raise RuntimeError("HEIC support (pillow-heif) is missing from this build")
        print("SELF-TEST: HEIC photos can be read.", flush=True)
        if pf.model_path("face_detect.onnx").exists() and pf.model_path("face_embed.onnx").exists():
            from PIL import Image as _I
            _ort_session(pf.model_path("face_detect.onnx"))
            pf.Faces().find(_I.new("RGB", (640, 480), "white"))
            print("SELF-TEST: face recognition runs.", flush=True)
        if all(pf.model_path(n).exists() for n in ("clip_vision.onnx", "clip_text.onnx", "clip_tokenizer.json")):
            from PIL import Image as _I
            pf.ClipImage().embed(_I.new("RGB", (640, 480), "white"))
            pf.ClipText().embed("a dog on the beach")
            print("SELF-TEST: photo search runs.", flush=True)
    except Exception as e:
        print(f"SELF-TEST FAILED: {e!r}", flush=True)
        WINDOW.destroy()
        return
    for _ in range(60):
        time.sleep(0.5)
        try:
            title = WINDOW.evaluate_js("document.title")
            ready = WINDOW.evaluate_js("typeof state !== 'undefined' && !!state && !!document.getElementById('status')")
            if title == "Photo Finder" and ready:
                SELF_TEST["ok"] = True
                break
        except Exception:
            pass
    print("SELF-TEST " + ("OK: the window opened and the interface loaded." if SELF_TEST["ok"]
                          else "FAILED: the interface didn't load in the window."), flush=True)
    WINDOW.destroy()


def already_running():
    """If Photo Finder is already open, just show it again."""
    try:
        info = json.loads((pf.data_dir() / "server.json").read_text())
        url = f"http://127.0.0.1:{info['port']}/t/{info['token']}/"
        with urllib.request.urlopen(url + "api/ping", timeout=2) as r:
            if r.status == 200:
                return url
    except Exception:
        pass
    return None


def main():
    if sys.stdout is None or (not sys.stdout.isatty() and "--self-test" not in sys.argv):  # from the app menu: keep a log
        log = open(pf.data_dir() / "app.log", "a", buffering=1, encoding="utf-8")
        sys.stdout = sys.stderr = log
        print(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')}")
    url = None if "--self-test" in sys.argv else already_running()
    if url:
        shown = False
        try:  # bring the open window forward
            req = urllib.request.Request(url + "api/show", data=b"{}", headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=3) as r:
                shown = json.loads(r.read()).get("ok", False)
        except Exception:
            pass
        if not shown:  # it's running in a browser tab instead
            open_window(url)
        return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    port = server.server_port
    url = f"http://127.0.0.1:{port}/t/{TOKEN}/"
    info_file = pf.data_dir() / "server.json"
    info_file.write_text(json.dumps({"port": port, "token": TOKEN, "pid": os.getpid()}))
    try:
        os.chmod(info_file, 0o600)
    except Exception:
        pass
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"Photo Finder is running at {url}", flush=True)

    if load_settings().get("folders") and "--self-test" not in sys.argv:
        INDEXER.start()
    threading.Thread(target=rescan_loop, daemon=True).start()

    window = None
    if "--no-window" not in sys.argv and "--browser" not in sys.argv:
        if run_app_window(url):  # blocks until the window is closed
            shutdown(server, info_file)
            if "--self-test" in sys.argv:
                sys.exit(0 if SELF_TEST["ok"] else 1)
            return
        if "--self-test" in sys.argv:
            print("SELF-TEST FAILED: the app window couldn't be created.", flush=True)
            shutdown(server, info_file)
            sys.exit(1)
    if "--no-window" not in sys.argv:
        window = open_window(url)
    try:
        while True:
            time.sleep(2)
            if window is not None:
                if window.poll() is not None:  # window closed
                    break
            elif "--no-window" not in sys.argv and time.time() - LAST_SEEN["t"] > 150:
                break  # browser tab closed (the page checks in every few seconds; background tabs less often)
    except KeyboardInterrupt:
        pass
    shutdown(server, info_file)


def shutdown(server, info_file):
    INDEXER.stop()
    server.shutdown()
    try:
        info_file.unlink()
    except Exception:
        pass


if __name__ == "__main__":
    main()
