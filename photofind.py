#!/usr/bin/env python3
"""
photofind - private, local photo search.

Everything runs on your own computer. Photos are never uploaded anywhere;
the only network access is a one-time download of the AI models (~130 MB).

Commands:
    photofind.py index  ~/Pictures            scan a folder (safe to stop and resume)
    photofind.py search dog on the beach       find photos by description
    photofind.py search --person Mom           every photo of a named person
    photofind.py people                        group unknown faces so you can name them
    photofind.py name 3 Mom                    give face group 3 the name "Mom"
    photofind.py status                        what has been indexed so far

Run any command with -h for its options.
"""

import argparse
import datetime as dt
import hashlib
import html
import os
import sqlite3
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

try:  # optional: iPhone .heic photos
    from pillow_heif import register_heif_opener

    register_heif_opener()
    HEIF = True
except ImportError:
    HEIF = False

Image.MAX_IMAGE_PIXELS = 300_000_000  # allow big panoramas

# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif"}
if HEIF:
    IMAGE_EXTS |= {".heic", ".heif"}

WORK_SIZE = 1024          # photos are decoded at most this big (faster, less RAM)
CLIP_SIZE = 256           # what the search model looks at
THUMB_SIZE = 320          # photo thumbnails in the results grid
MIN_FACE_PX = 36          # ignore tiny background faces (in WORK_SIZE pixels)
FACE_SCORE = 0.85         # face detector confidence needed
CLUSTER_SIM = 0.40        # how alike two faces must be to group them
ASSIGN_SIM = 0.42         # how alike a face must be to a named person to auto-tag it
ASSIGN_MARGIN = 0.05      # ...and how much better than the next-closest person
MATCH_STRICTNESS = 2.0    # search: how far above a typical photo's score counts as a match
                          # (raise if unrelated photos show up as matches, lower if real ones are missed)
NEAR_STRICTNESS = 1.0     # search: lower bar for the "possibly related" section
MAX_NEAR = 8              # search: at most this many "possibly related" photos

HF = "https://huggingface.co/Xenova/mobileclip_s0/resolve/main/"
GH = "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/"
MODELS = {
    # local name: (url, size in bytes, sha256 or None)
    "clip_vision.onnx": (HF + "onnx/vision_model.onnx", 45_543_630, None),
    "clip_text.onnx": (HF + "onnx/text_model_int8.onnx", 42_799_230, None),
    "clip_tokenizer.json": (HF + "tokenizer.json", 2_224_081, None),
    "face_detect.onnx": (
        GH + "face_detection_yunet/face_detection_yunet_2023mar.onnx",
        232_589,
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    ),
    "face_embed.onnx": (
        GH + "face_recognition_sface/face_recognition_sface_2021dec.onnx",
        38_696_353,
        "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
    ),
}


def data_dir() -> Path:
    """Where the database, models and thumbnails live."""
    if os.environ.get("PHOTOFIND_HOME"):
        base = Path(os.environ["PHOTOFIND_HOME"]).expanduser()
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "photofind"
    else:
        xdg = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
        base = Path(xdg) / "photofind"
    base.mkdir(parents=True, exist_ok=True)
    return base


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def is_tty() -> bool:
    return sys.stdout is not None and sys.stdout.isatty()


def say(msg=""):
    print(msg, flush=True)


def human_time(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60:02d}m"


def normalize(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, dtype=np.float32)
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.maximum(n, 1e-12)


def be_nice():
    """Run at low priority so the computer stays usable while indexing."""
    try:
        if os.name == "nt":
            import ctypes

            BELOW_NORMAL = 0x4000
            k32 = ctypes.windll.kernel32
            k32.SetPriorityClass(k32.GetCurrentProcess(), BELOW_NORMAL)
        else:
            os.nice(10)
    except Exception:
        pass


def quick_hash(path: Path, size: int) -> str:
    """Fingerprint from file size + first/last 64 KB: spots moved or renamed photos."""
    h = hashlib.sha1(str(size).encode())
    with open(path, "rb") as f:
        h.update(f.read(65536))
        if size > 131072:
            f.seek(-65536, os.SEEK_END)
            h.update(f.read(65536))
    return h.hexdigest()


# --------------------------------------------------------------------------
# Models (downloaded once, then fully offline)
# --------------------------------------------------------------------------

def model_path(name: str) -> Path:
    return data_dir() / "models" / name


def ensure_models(names):
    folder = data_dir() / "models"
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        url, size, sha = MODELS[name]
        dest = folder / name
        if dest.exists() and dest.stat().st_size == size:
            continue
        say(f"Downloading {name} ({size / 1e6:.0f} MB, one time only)...")
        tmp = dest.with_suffix(dest.suffix + ".part")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "photofind/0.1"})
            with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as out:
                got = 0
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    out.write(chunk)
                    got += len(chunk)
                    if is_tty():
                        print(f"\r  {got / 1e6:6.1f} / {size / 1e6:.1f} MB", end="", flush=True)
            if is_tty():
                print()
        except Exception as e:
            tmp.unlink(missing_ok=True)
            sys.exit(
                f"Could not download {name}: {e}\n"
                f"You can download it yourself from\n  {url}\n"
                f"and save it as\n  {dest}"
            )
        if tmp.stat().st_size != size:
            tmp.unlink(missing_ok=True)
            sys.exit(f"Download of {name} was incomplete. Please run the command again.")
        if sha:
            digest = hashlib.sha256(tmp.read_bytes()).hexdigest()
            if digest != sha:
                tmp.unlink(missing_ok=True)
                sys.exit(f"Download of {name} failed its checksum. Please run the command again.")
        tmp.replace(dest)


def _ort_session(path: Path):
    import onnxruntime as ort

    opts = ort.SessionOptions()
    opts.intra_op_num_threads = max(1, (os.cpu_count() or 2) // 2)  # leave half the CPU free
    opts.inter_op_num_threads = 1
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    opts.log_severity_level = 3
    return ort.InferenceSession(str(path), sess_options=opts, providers=["CPUExecutionProvider"])


def _pick_output(session, wanted: str) -> str:
    names = [o.name for o in session.get_outputs()]
    for n in names:
        if wanted in n:
            return n
    return names[0]


class ClipImage:
    """Turns a photo into a 512-number 'meaning' vector."""

    def __init__(self):
        ensure_models(["clip_vision.onnx"])
        self.sess = _ort_session(model_path("clip_vision.onnx"))
        self.inp = self.sess.get_inputs()[0].name
        self.out = _pick_output(self.sess, "image_embeds")

    def embed(self, img: Image.Image) -> np.ndarray:
        w, h = img.size
        scale = CLIP_SIZE / min(w, h)
        img = img.resize((max(CLIP_SIZE, round(w * scale)), max(CLIP_SIZE, round(h * scale))), Image.BILINEAR)
        w, h = img.size
        left, top = (w - CLIP_SIZE) // 2, (h - CLIP_SIZE) // 2
        img = img.crop((left, top, left + CLIP_SIZE, top + CLIP_SIZE))
        x = np.asarray(img, dtype=np.float32) / 255.0  # MobileCLIP: no mean/std normalization
        x = x.transpose(2, 0, 1)[None]
        return normalize(self.sess.run([self.out], {self.inp: x})[0][0])


class ClipText:
    """Turns a search phrase into the same kind of vector."""

    def __init__(self):
        from tokenizers import Tokenizer

        ensure_models(["clip_text.onnx", "clip_tokenizer.json"])
        self.tok = Tokenizer.from_file(str(model_path("clip_tokenizer.json")))
        self.tok.no_padding()
        self.tok.no_truncation()
        self.sess = _ort_session(model_path("clip_text.onnx"))
        self.inputs = self.sess.get_inputs()
        self.out = _pick_output(self.sess, "text_embeds")

    def embed(self, text: str) -> np.ndarray:
        ids = self.tok.encode(text).ids
        if len(ids) > 77:
            ids = ids[:76] + [ids[-1]]  # keep the end-of-text token
        mask = [1] * len(ids) + [0] * (77 - len(ids))
        ids = ids + [0] * (77 - len(ids))
        feed = {}
        for i in self.inputs:
            dtype = np.int32 if "int32" in i.type else np.int64
            if "mask" in i.name:
                feed[i.name] = np.array([mask], dtype=dtype)
            else:
                feed[i.name] = np.array([ids], dtype=dtype)
        return normalize(self.sess.run([self.out], feed)[0][0])

    def query(self, text: str) -> np.ndarray:
        # Blend the plain phrase with "a photo of ..." - CLIP models search better that way.
        return normalize(self.embed(text) + self.embed(f"a photo of {text}"))


class Faces:
    """Finds faces and turns each one into a 128-number 'who is this' vector."""

    def __init__(self):
        import cv2

        try:
            cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
        except Exception:
            pass
        ensure_models(["face_detect.onnx", "face_embed.onnx"])
        self.cv2 = cv2
        self.det = cv2.FaceDetectorYN.create(str(model_path("face_detect.onnx")), "", (320, 320), FACE_SCORE, 0.3, 5000)
        self.rec = cv2.FaceRecognizerSF.create(str(model_path("face_embed.onnx")), "")

    def find(self, img: Image.Image):
        """Returns [(x, y, w, h, score, vector)] with the box as fractions of the photo size."""
        bgr = np.ascontiguousarray(np.asarray(img)[:, :, ::-1])
        h, w = bgr.shape[:2]
        self.det.setInputSize((w, h))
        _, found = self.det.detect(bgr)
        results = []
        if found is None:
            return results
        min_px = max(20, MIN_FACE_PX * max(w, h) / WORK_SIZE)  # scaled for small photos
        for row in found:
            fx, fy, fw, fh = (float(v) for v in row[:4])
            if min(fw, fh) < min_px:
                continue
            aligned = self.rec.alignCrop(bgr, row)
            vec = normalize(self.rec.feature(aligned)[0])
            results.append((fx / w, fy / h, fw / w, fh / h, float(row[-1]), vec))
        return results


# --------------------------------------------------------------------------
# Database (one SQLite file)
# --------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS photos (
    id INTEGER PRIMARY KEY,
    path TEXT UNIQUE NOT NULL,
    size INTEGER, mtime REAL, qhash TEXT,
    taken TEXT,
    clip BLOB,
    faces_done INTEGER DEFAULT 0,
    error TEXT
);
CREATE INDEX IF NOT EXISTS photos_qhash ON photos(qhash);
CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL COLLATE NOCASE
);
CREATE TABLE IF NOT EXISTS faces (
    id INTEGER PRIMARY KEY,
    photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    x REAL, y REAL, w REAL, h REAL, score REAL,
    emb BLOB NOT NULL,
    cluster INTEGER,
    person_id INTEGER REFERENCES people(id) ON DELETE SET NULL,
    locked INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS faces_photo ON faces(photo_id);
CREATE INDEX IF NOT EXISTS faces_person ON faces(person_id);
"""


_db_ready = False


def open_db() -> sqlite3.Connection:
    global _db_ready
    db = sqlite3.connect(data_dir() / "photofind.db", timeout=30, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")  # per connection
    if not _db_ready:  # WAL mode and the tables persist in the file: set them up once per run
        db.execute("PRAGMA journal_mode = WAL")
        db.executescript(SCHEMA)
        _db_ready = True
    return db


def to_blob(v: np.ndarray) -> bytes:
    return np.asarray(v, dtype=np.float16).tobytes()


def from_blob(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype=np.float16).astype(np.float32)


# --------------------------------------------------------------------------
# Reading photos
# --------------------------------------------------------------------------

def load_photo(path: Path, max_side: int = WORK_SIZE) -> Image.Image:
    img = Image.open(path)
    img.draft("RGB", (max_side, max_side))  # JPEGs decode straight to a smaller size: much faster
    img = ImageOps.exif_transpose(img)
    img = img.convert("RGB")
    if max(img.size) > max_side:
        img.thumbnail((max_side, max_side), Image.BILINEAR)
    return img


def date_taken(path: Path, fallback_mtime: float) -> str:
    try:
        with Image.open(path) as img:
            exif = img.getexif()
            value = exif.get_ifd(0x8769).get(36867) or exif.get(306)  # DateTimeOriginal, DateTime
        if value:
            return dt.datetime.strptime(str(value).strip()[:19], "%Y:%m:%d %H:%M:%S").isoformat(sep=" ")
    except Exception:
        pass
    return dt.datetime.fromtimestamp(fallback_mtime).isoformat(sep=" ", timespec="seconds")


IGNORE_MARKER = ".photofind-ignore"  # a folder holding this file is never indexed (e.g. the app's own copies)


def iter_images(root: Path):
    skip = data_dir().resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        if IGNORE_MARKER in filenames:
            dirnames[:] = []
            continue
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and Path(dirpath, d).resolve() != skip)
        for name in sorted(filenames):
            if not name.startswith(".") and Path(name).suffix.lower() in IMAGE_EXTS:
                yield Path(dirpath, name)


# --------------------------------------------------------------------------
# index
# --------------------------------------------------------------------------

def index_folder(root, want_faces=True, on_progress=None, should_stop=None, deadline=None) -> dict:
    """Index (or update) one folder. Safe to stop and resume any time.

    on_progress(info) is called with a dict:
      phase: "scanning" | "loading" | "indexing" | "done"
      found, todo, done, faces, errors, moved, removed, rate (photos/s), eta (seconds)
    should_stop() is polled between photos; return True to stop early.
    """
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Folder not found: {root}")
    info = dict(phase="scanning", root=str(root), found=0, todo=0, done=0, faces=0,
                errors=0, moved=0, removed=0, rate=0.0, eta=None, stopped=False, tagged=0)
    report = on_progress or (lambda i: None)
    stop = should_stop or (lambda: False)
    report(info)

    db = open_db()
    on_disk = {}
    for p in iter_images(root):
        try:
            on_disk[str(p)] = p.stat()
        except OSError:
            pass
    info["found"] = len(on_disk)

    prefix = str(root).rstrip(os.sep) + os.sep
    known = {
        r["path"]: r
        for r in db.execute(
            "SELECT id, path, size, mtime, qhash, clip IS NOT NULL AS has_clip, faces_done, error "
            "FROM photos WHERE substr(path, 1, ?) = ?",
            (len(prefix), prefix),
        )
    }

    todo = []
    for path, st in on_disk.items():
        r = known.get(path)
        unchanged = r is not None and r["size"] == st.st_size and abs(r["mtime"] - st.st_mtime) < 1
        if unchanged and (r["error"] or (r["has_clip"] and (r["faces_done"] or not want_faces))):
            continue
        todo.append((path, st, r if unchanged else None))

    # Photos that disappeared: maybe they were moved/renamed (same content, new path).
    gone = {r["qhash"]: r for p, r in known.items() if p not in on_disk and r["qhash"]}
    if gone:
        still_todo = []
        for path, st, r in todo:
            if path not in known:  # a brand-new path: is it a photo we already know, moved here?
                qh = quick_hash(Path(path), st.st_size)
                old = gone.pop(qh, None)
                if old is not None:
                    db.execute("UPDATE photos SET path=?, mtime=? WHERE id=?", (path, st.st_mtime, old["id"]))
                    info["moved"] += 1
                    if old["has_clip"] and (old["faces_done"] or not want_faces):
                        continue
            still_todo.append((path, st, r))
        todo = still_todo
    removed = [r["id"] for p, r in known.items() if p not in on_disk and r["qhash"] in gone]
    removed += [r["id"] for p, r in known.items() if p not in on_disk and not r["qhash"]]
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
                p = Path(path)
                try:
                    img = load_photo(p)
                    need_clip = prev is None or not prev["has_clip"]
                    vec = clip.embed(img) if need_clip else None
                    found = faces.find(img) if faces else []
                    taken = date_taken(p, st.st_mtime)
                    qh = quick_hash(p, st.st_size)
                    if need_clip:
                        db.execute(
                            "INSERT INTO photos (path, size, mtime, qhash, taken, clip, faces_done, error) "
                            "VALUES (?,?,?,?,?,?,?,NULL) ON CONFLICT(path) DO UPDATE SET size=excluded.size, "
                            "mtime=excluded.mtime, qhash=excluded.qhash, taken=excluded.taken, clip=excluded.clip, "
                            "faces_done=excluded.faces_done, error=NULL",
                            (path, st.st_size, st.st_mtime, qh, taken, to_blob(vec), int(bool(faces))),
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
                        info["faces"] += len(found)
                    try:  # the photo is already decoded, so its thumbnail is nearly free now
                        photo_thumb(qh, path, img)
                    except OSError:
                        pass  # not worth failing the photo over; it's made on demand later
                except Exception as e:
                    info["errors"] += 1
                    db.execute(
                        "INSERT INTO photos (path, size, mtime, error) VALUES (?,?,?,?) "
                        "ON CONFLICT(path) DO UPDATE SET size=excluded.size, mtime=excluded.mtime, error=excluded.error",
                        (path, st.st_size, st.st_mtime, f"{type(e).__name__}: {e}"[:300]),
                    )
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


def cmd_index(args):
    be_nice()
    tty = is_tty()
    last = {"t": 0.0}

    def progress(i):
        if i["phase"] == "scanning":
            say(f"Scanning {i['root']} ...")
        elif i["phase"] == "loading":
            say(f"{i['found']} photos found, {i['found'] - i['todo']} already indexed"
                + (f", {i['moved']} moved" if i["moved"] else "")
                + (f", {i['removed']} removed" if i["removed"] else "") + f", {i['todo']} to do.")
            say("Loading models...")
        elif i["phase"] == "indexing":
            if i["done"] == 0:
                say("Indexing. You can stop any time with Ctrl+C; run the same command later to continue.")
                return
            now = time.time()
            if tty and (now - last["t"] > 0.2 or i["done"] == i["todo"]):
                last["t"] = now
                line = (f"  {i['done']}/{i['todo']} photos | {i['rate']:.1f}/s | "
                        f"about {human_time(i['eta'] or 0)} left | {i['faces']} faces")
                print("\r" + line.ljust(78), end="", flush=True)
            elif not tty and (i["done"] % 200 == 0 or i["done"] == i["todo"]):
                say(f"  {i['done']}/{i['todo']} photos")

    deadline = time.time() + args.max_minutes * 60 if args.max_minutes else None
    try:
        info = index_folder(args.folder, not args.no_faces, progress, deadline=deadline)
    except FileNotFoundError as e:
        sys.exit(str(e))
    except KeyboardInterrupt:
        say("\nStopped. Progress is saved; run the same command to continue.")
        return
    if tty and info["todo"]:
        say()
    if not info["todo"]:
        say(f"{info['found']} photos found, all already indexed"
            + (f" ({info['moved']} moved, {info['removed']} removed)" if info["moved"] or info["removed"] else "") + ".")
        return
    if info["stopped"]:
        say(f"Reached the {args.max_minutes}-minute limit; run again to continue.")
    say(f"Indexed {info['done']} photos"
        + (f" ({info['errors']} could not be read; see `status`)" if info["errors"] else "") + ".")
    if not args.no_faces:
        if info["tagged"]:
            say(f"Recognized {info['tagged']} faces of people you've already named.")
        db = open_db()
        unnamed = db.execute("SELECT COUNT(*) FROM faces WHERE person_id IS NULL").fetchone()[0]
        if unnamed:
            say(f"{unnamed} faces don't have a name yet. Run `photofind.py people` to name them.")


# --------------------------------------------------------------------------
# Faces: grouping and naming
# --------------------------------------------------------------------------

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


def thumbs_dir(kind: str) -> Path:
    folder = data_dir() / "thumbs" / kind
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def save_thumb(img: Image.Image, out: Path, size: int, quality: int):
    """Shrink a copy of img and write it atomically, so a half-written file is never served."""
    img = img.copy()
    img.thumbnail((size, size))
    tmp = out.with_name(f"{out.stem}.{threading.get_ident()}.tmp")
    img.save(tmp, "JPEG", quality=quality)
    tmp.replace(out)


# Thumbnails are named by the photo's fingerprint (qhash), not its database id: ids get
# reused after deletions, while an edited photo gets a new fingerprint and a moved one keeps it.

def face_thumb(row, size=128) -> Path:
    box = hashlib.sha1(f"{row['x']},{row['y']},{row['w']},{row['h']}".encode()).hexdigest()[:10]
    out = thumbs_dir("faces") / f"{row['qhash']}-{box}-{size}.jpg"
    if not out.exists():
        img = load_photo(Path(row["path"]), 1600)
        W, H = img.size
        cx, cy = (row["x"] + row["w"] / 2) * W, (row["y"] + row["h"] / 2) * H
        half = max(row["w"] * W, row["h"] * H) * 0.8
        save_thumb(img.crop((int(cx - half), int(cy - half), int(cx + half), int(cy + half))), out, size, 85)
    return out


def photo_thumb(qhash: str, path: str, img: Image.Image = None) -> Path:
    """img: the photo, if it's already decoded (saves opening it again)."""
    out = thumbs_dir("photos") / f"{qhash}.jpg"
    if not out.exists():
        if img is None:
            img = load_photo(Path(path), THUMB_SIZE * 2)
        save_thumb(img, out, THUMB_SIZE, 82)
    return out


def prune_thumbs(db):
    """Delete thumbnails of photos that are gone or have changed."""
    keep = {r[0] for r in db.execute("SELECT qhash FROM photos WHERE qhash IS NOT NULL")}
    for kind in ("photos", "faces"):
        for f in thumbs_dir(kind).glob("*.jpg"):
            if f.stem.split("-")[0] not in keep:
                f.unlink(missing_ok=True)


def cmd_people(args):
    db = open_db()
    if db.execute("SELECT COUNT(*) FROM faces").fetchone()[0] == 0:
        sys.exit("No faces found yet. Run `photofind.py index <folder>` first.")
    auto_assign(db)
    groups = cluster_unnamed(db)
    named = db.execute(
        "SELECT pe.id, pe.name, COUNT(f.id) AS n FROM people pe LEFT JOIN faces f ON f.person_id = pe.id "
        "GROUP BY pe.id ORDER BY n DESC"
    ).fetchall()

    shown = [(num, g) for num, g in groups if len(g) >= args.min_faces][: args.limit]
    singles = sum(1 for _, g in groups if len(g) == 1)

    if named:
        say("Named people:")
        for p in named:
            say(f"  {p['name']}: {p['n']} faces")
        say()
    if shown:
        say("Unnamed face groups (name one with: photofind.py name <group> <name>):")
        for num, g in shown:
            say(f"  group {num}: {len(g)} faces")
    else:
        say("No unnamed face groups left.")
    if singles:
        say(f"  (+{singles} faces seen only once)")

    # Visual page
    parts = [page_head("People")]
    for p in named:
        rows, _ = load_faces(db, "f.person_id = ?", (p["id"],))
        rows = sorted(rows, key=lambda r: -r["score"] * r["w"])[: args.per_group]
        parts.append(f"<h2>{html.escape(p['name'])} <small>{p['n']} faces</small></h2><div class=faces>")
        parts += [face_html(r) for r in rows]
        parts.append("</div>")
    for num, g in shown:
        parts.append(
            f"<h2>Group {num} <small>{len(g)} faces &middot; "
            f"<code>photofind.py name {num} &lt;name&gt;</code></small></h2><div class=faces>"
        )
        parts += [face_html(r) for r in g[: args.per_group]]
        parts.append("</div>")
    parts.append(
        "<p class=hint>Number under each face = face id. If a face is in the wrong place, run "
        "<code>photofind.py unname &lt;face id&gt;</code>.</p></body></html>"
    )
    out = data_dir() / "people.html"
    out.write_text("".join(parts), encoding="utf-8")
    say(f"\nFaces page: {out}")
    if not args.no_open:
        webbrowser.open(out.as_uri())


def cmd_name(args):
    clusters, name_words = [], []
    for token in args.args:
        if not name_words and token.isdigit():
            clusters.append(int(token))
        else:
            name_words.append(token)
    name = " ".join(name_words).strip()
    if not clusters or not name:
        sys.exit("Usage: photofind.py name <group> [<group> ...] <name>    e.g.  photofind.py name 3 7 Grandma")
    db = open_db()
    db.execute("INSERT OR IGNORE INTO people (name) VALUES (?)", (name,))
    pid = db.execute("SELECT id FROM people WHERE name = ?", (name,)).fetchone()[0]
    marks = ",".join("?" * len(clusters))
    n = db.execute(
        f"UPDATE faces SET person_id=?, cluster=NULL, locked=0 WHERE person_id IS NULL AND cluster IN ({marks})",
        (pid, *clusters),
    ).rowcount
    db.commit()
    if n == 0:
        sys.exit("Those group numbers weren't found. Run `photofind.py people` to see current groups.")
    more = auto_assign(db)
    say(f"Named {n} faces '{name}'" + (f", and recognized {more} more" if more else "") + ".")
    say(f"Try: photofind.py search --person \"{name}\"")


def cmd_unname(args):
    db = open_db()
    marks = ",".join("?" * len(args.face_ids))
    n = db.execute(
        f"UPDATE faces SET person_id=NULL, cluster=NULL, locked=1 WHERE id IN ({marks})", args.face_ids
    ).rowcount
    db.commit()
    say(f"Removed the name from {n} face(s). They won't be auto-tagged again, but you can still name them.")


def cmd_rename(args):
    db = open_db()
    n = db.execute("UPDATE people SET name=? WHERE name=?", (args.new, args.old)).rowcount
    db.commit()
    say("Renamed." if n else f"No person called '{args.old}'.")


def cmd_forget(args):
    db = open_db()
    row = db.execute("SELECT id FROM people WHERE name=?", (args.name,)).fetchone()
    if not row:
        sys.exit(f"No person called '{args.name}'.")
    db.execute("UPDATE faces SET person_id=NULL, cluster=NULL WHERE person_id=?", (row[0],))
    db.execute("DELETE FROM people WHERE id=?", (row[0],))
    db.commit()
    say(f"Forgot '{args.name}'. Their faces are unnamed again.")


# --------------------------------------------------------------------------
# search
# --------------------------------------------------------------------------

def page_head(title: str) -> str:
    return (
        "<!doctype html><html><head><meta charset=utf-8>"
        f"<title>{html.escape(title)}</title><style>"
        "body{font:15px system-ui,sans-serif;margin:24px;background:#f6f5f2;color:#222}"
        "h1{font-size:20px} h2{font-size:16px;margin:28px 0 8px} small{color:#777;font-weight:normal}"
        ".grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px}"
        ".grid a{background:#fff;border-radius:8px;overflow:hidden;text-decoration:none;color:#444;"
        "box-shadow:0 1px 3px #0002}"
        ".grid img{width:100%;height:200px;object-fit:cover;display:block}"
        ".grid div{padding:6px 8px;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}"
        ".faces{display:flex;flex-wrap:wrap;gap:8px}"
        ".faces figure{margin:0;text-align:center;font-size:11px;color:#888}"
        ".faces img{width:96px;height:96px;border-radius:50%;object-fit:cover;display:block}"
        "details{margin-top:28px} summary{cursor:pointer;color:#777;margin-bottom:12px}"
        "code{background:#e9e6df;padding:1px 5px;border-radius:4px} .hint{color:#666;margin-top:32px}"
        "@media(prefers-color-scheme:dark){body{background:#1c1c1e;color:#eee}"
        ".grid a{background:#2c2c2e;color:#ccc} code{background:#3a3a3c}}"
        "</style></head><body>"
    )


def face_html(r) -> str:
    try:
        src = face_thumb(r).as_uri()
    except Exception:
        return ""
    return (
        f"<figure><a href='{Path(r['path']).as_uri()}' title='{html.escape(r['path'])}'>"
        f"<img src='{src}' loading=lazy></a>{r['id']}</figure>"
    )


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


def grid_html(items) -> str:
    cells = []
    for r, s in items:
        try:
            thumb = photo_thumb(r["qhash"], r["path"]).as_uri()
        except Exception:
            continue
        label = (r["taken"] or "")[:10] + ("" if s is None else f" &middot; {s:.2f}")
        cells.append(
            f"<a href='{Path(r['path']).as_uri()}' title='{html.escape(r['path'])}'>"
            f"<img src='{thumb}' loading=lazy><div>{label}<br>{html.escape(Path(r['path']).name)}</div></a>"
        )
    return "<div class=grid>" + "".join(cells) + "</div>"


def cmd_search(args):
    db = open_db()
    query = " ".join(args.query).strip()
    if not query and not args.person:
        sys.exit('Tell me what to look for, e.g.  photofind.py search "dog on the beach"  or  --person Mom')

    where, params = ["p.clip IS NOT NULL"], []
    names = []
    for name in args.person or []:
        row = db.execute("SELECT id, name FROM people WHERE name=?", (name,)).fetchone()
        if not row:
            known = ", ".join(r[0] for r in db.execute("SELECT name FROM people ORDER BY name")) or "none yet"
            sys.exit(f"No person called '{name}'. Named people: {known}")
        where.append("EXISTS (SELECT 1 FROM faces f WHERE f.photo_id = p.id AND f.person_id = ?)")
        params.append(row[0])
        names.append(row[1])
    if args.since:
        where.append("p.taken >= ?")
        params.append(args.since)
    if args.until:
        where.append("p.taken < ?")
        params.append(args.until + "~")  # include the whole 'until' day/year
    rows = db.execute(f"SELECT p.id, p.path, p.taken, p.qhash, p.clip FROM photos p WHERE {' AND '.join(where)}", params).fetchall()
    if not rows:
        sys.exit("No indexed photos match those filters." if len(where) > 1 else "Nothing indexed yet. Run `photofind.py index <folder>` first.")

    title = query or "Photos"
    if names:
        title += " with " + " & ".join(names)

    if query:
        t0 = time.time()
        qv = ClipText().query(query)
        mat = np.stack([from_blob(r["clip"]) for r in rows])
        scores = mat @ qv
        took = time.time() - t0
        if args.all:
            order = np.argsort(-scores)
            matches, weaker = order[: args.limit], order[:0]
        else:
            matches, weaker = split_matches(scores)
            matches = matches[: args.limit]
        matches = [(rows[i], float(scores[i])) for i in matches]
        weaker = [(rows[i], float(scores[i])) for i in weaker]
    else:
        matches = [(r, None) for r in sorted(rows, key=lambda r: r["taken"] or "", reverse=True)[: args.limit]]
        weaker, took = [], 0.0

    def show(items, n):
        for r, s in items[:n]:
            say(f"  {'' if s is None else f'{s:.3f}  '}{(r['taken'] or '')[:10]}  {r['path']}")

    timing = f" ({took:.2f}s)" if query else ""
    if not query:
        say(f"{title}: {len(rows)} photos" + (f" (showing the newest {len(matches)}; use -n for more)" if len(rows) > len(matches) else ""))
        show(matches, 10)
    elif matches:
        label = "top" if args.all else "clear match" + ("es" if len(matches) != 1 else "")
        say(f"{title}: {len(matches)} {label} out of {len(rows)} photos{timing}" if not args.all
            else f"{title}: {label} {len(matches)} of {len(rows)} photos{timing}")
        show(matches, 10)
        if len(matches) > 10:
            say(f"  ... and {len(matches) - 10} more on the results page")
        if weaker:
            say(f"  (+{len(weaker)} possibly related on the results page)")
    elif weaker:
        say(f"No clear matches for '{title}' among {len(rows)} photos{timing}. Possibly related:")
        show(weaker, MAX_NEAR)
    else:
        say(f"No matches for '{title}' among {len(rows)} photos{timing}.")
        return

    count = f"{len(rows)} photos" if not query else f"{len(matches)} {'shown' if args.all else 'matches'}"
    parts = [page_head(title), f"<h1>{html.escape(title)} <small>{count}</small></h1>"]
    if matches:
        parts.append(grid_html(matches))
    if weaker:
        if matches:
            parts.append(f"<details><summary>Possibly related ({len(weaker)})</summary>{grid_html(weaker)}</details>")
        else:
            parts.append(f"<p>No clear matches. Possibly related:</p>{grid_html(weaker)}")
    parts.append("</body></html>")
    out = data_dir() / "results.html"
    out.write_text("".join(parts), encoding="utf-8")
    say(f"Results page: {out}")
    if not args.no_open:
        webbrowser.open(out.as_uri())


# --------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------

def cmd_status(args):
    db = open_db()
    q = lambda sql: db.execute(sql).fetchone()[0]
    say(f"Data folder:  {data_dir()}")
    say(f"Photos:       {q('SELECT COUNT(*) FROM photos WHERE clip IS NOT NULL')} searchable")
    say(f"Faces:        {q('SELECT COUNT(*) FROM faces')} found, "
        f"{q('SELECT COUNT(*) FROM faces WHERE person_id IS NOT NULL')} named")
    say(f"People:       {q('SELECT COUNT(*) FROM people')}")
    dbfile = data_dir() / "photofind.db"
    say(f"Database:     {dbfile.stat().st_size / 1e6:.1f} MB")
    present = [n for n in MODELS if model_path(n).exists()]
    say(f"Models:       {len(present)}/{len(MODELS)} downloaded")
    say(f"HEIC support: {'yes' if HEIF else 'no (pip install pillow-heif to add it)'}")
    errs = db.execute("SELECT path, error FROM photos WHERE error IS NOT NULL LIMIT 20").fetchall()
    if errs:
        say(f"\nPhotos that couldn't be read ({q('SELECT COUNT(*) FROM photos WHERE error IS NOT NULL')}):")
        for r in errs:
            say(f"  {r['path']}\n    {r['error']}")


# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        prog="photofind.py",
        description="Private photo search that runs entirely on your computer.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Commands:")[1],
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("index", help="scan a folder of photos (stop and resume any time)")
    p.add_argument("folder")
    p.add_argument("--no-faces", action="store_true", help="skip face recognition (faster)")
    p.add_argument("--max-minutes", type=float, help="stop after this many minutes (handy for overnight runs)")
    p.set_defaults(func=cmd_index)

    p = sub.add_parser("search", help="find photos by description and/or person")
    p.add_argument("query", nargs="*", help='what to look for, e.g. "birthday cake"')
    p.add_argument("--person", action="append", help="only photos with this person (repeat for several)")
    p.add_argument("--since", help="only photos taken on/after this date, e.g. 2023 or 2023-06-01")
    p.add_argument("--until", help="only photos taken on/before this date, e.g. 2024 or 2024-12-31")
    p.add_argument("-n", "--limit", type=int, default=40, help="most matches to show (default 40)")
    p.add_argument("--all", action="store_true", help="rank every photo instead of only clear matches")
    p.add_argument("--no-open", action="store_true", help="don't open the results page in the browser")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("people", help="group unnamed faces and show them so you can name them")
    p.add_argument("--min-faces", type=int, default=2, help="smallest group to show (default 2)")
    p.add_argument("--limit", type=int, default=30, help="how many groups to show (default 30)")
    p.add_argument("--per-group", type=int, default=24, help="faces shown per group (default 24)")
    p.add_argument("--no-open", action="store_true")
    p.set_defaults(func=cmd_people)

    p = sub.add_parser("name", help="name a face group:  name 3 Mom   (several groups: name 3 7 Mom)")
    p.add_argument("args", nargs="+")
    p.set_defaults(func=cmd_name)

    p = sub.add_parser("unname", help="remove a wrong name from specific faces (by face id)")
    p.add_argument("face_ids", nargs="+", type=int)
    p.set_defaults(func=cmd_unname)

    p = sub.add_parser("rename", help="rename a person")
    p.add_argument("old")
    p.add_argument("new")
    p.set_defaults(func=cmd_rename)

    p = sub.add_parser("forget", help="delete a person's name (their faces become unnamed)")
    p.add_argument("name")
    p.set_defaults(func=cmd_forget)

    p = sub.add_parser("status", help="show what has been indexed")
    p.set_defaults(func=cmd_status)

    args = ap.parse_args()
    try:
        args.func(args)
    except BrokenPipeError:  # output piped into e.g. `head`
        sys.stderr.close()
    except KeyboardInterrupt:
        sys.exit("\nStopped.")


if __name__ == "__main__":
    main()
