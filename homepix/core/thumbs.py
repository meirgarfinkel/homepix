"""Thumbnails for photos and faces.

They are named by the photo's fingerprint (qhash), not its database id: ids get reused after
deletions, while an edited photo gets a new fingerprint and a moved one keeps it.
"""

import hashlib
import os
import threading
from pathlib import Path

from PIL import Image

from .config import FACE_SIZE, THUMB_SIZE, data_dir
from .media import load_photo
from .video import is_video, video_frame


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


ENOUGH = 0.7  # of a face picture's size: a square this big in the photo's thumbnail is sharp enough
# (at FACE_SIZE, 112 px: what a face tile shows), so the photo needn't be read


def face_thumb_file(row, size=FACE_SIZE) -> Path:
    box = hashlib.sha1(f"{row['x']},{row['y']},{row['w']},{row['h']}".encode()).hexdigest()[:10]
    return thumbs_dir("faces") / f"{row['qhash']}-{box}-v2-{size}.jpg"


def _face_square(row, W, H):
    """The square around the face in a W x H picture of the photo, kept inside it (moved, or made
    smaller, at its edges): (left, top, side) in pixels."""
    cx, cy = (row["x"] + row["w"] / 2) * W, (row["y"] + row["h"] / 2) * H
    half = min(max(row["w"] * W, row["h"] * H) * 0.8, W / 2, H / 2)
    return min(max(cx - half, 0), W - 2 * half), min(max(cy - half, 0), H - 2 * half), 2 * half


def _thumbnail(qhash: str):
    """The photo's thumbnail, decoded (some 50 times less to read than the photo), or None if it
    isn't made yet."""
    try:
        with Image.open(thumb_file(qhash)) as im:
            return im.convert("RGB")
    except OSError:
        return None


def _sharp_enough(row, img, size) -> bool:
    return img is not None and _face_square(row, *img.size)[2] >= size * ENOUGH


def face_thumb(row, size=FACE_SIZE, img: Image.Image = None) -> Path:
    """A square around the face. img: the photo, if it's already decoded (while indexing). Otherwise
    it's cut from the photo's thumbnail when the face is big enough there, and from the photo only
    when it isn't (a small face in a group shot)."""
    out = face_thumb_file(row, size)
    if not out.exists():
        if img is None:
            thumb = _thumbnail(row["qhash"])
            img = thumb if _sharp_enough(row, thumb, size) else load_photo(Path(row["path"]), 1600)
        left, top, side = _face_square(row, *img.size)
        save_thumb(img.crop((int(left), int(top), int(left + side), int(top + side))), out, size, 85)
    return out


def _thumb_name(qhash: str) -> str:
    return f"{qhash}-{THUMB_SIZE}.jpg"


def thumb_file(qhash: str) -> Path:
    """Where a photo's thumbnail is (or goes). Its size is in the name, so a bigger size later
    doesn't pass off the smaller ones as up to date."""
    return thumbs_dir("photos") / _thumb_name(qhash)


def old_thumb_file(qhash: str) -> Path:
    """A thumbnail from before they were sharpened (320 px): shown until its sharper one is made."""
    return thumbs_dir("photos") / f"{qhash}.jpg"


def photo_thumb(qhash: str, path: str, img: Image.Image = None) -> Path:
    """img: the photo, if it's already decoded (saves opening it again). For a video, a frame of it."""
    out = thumb_file(qhash)
    if not out.exists():
        if img is None and is_video(path):
            img = video_frame(path, THUMB_SIZE * 2)
            if img is None:
                raise ValueError("this video can't be decoded")
        elif img is None:
            img = load_photo(Path(path), THUMB_SIZE * 2)
        save_thumb(img, out, THUMB_SIZE, 85)
        old_thumb_file(qhash).unlink(missing_ok=True)
    return out


def sharpen_thumbs(db, prefix: str, should_stop=lambda: False, progress=None, workers: int = 3) -> int:
    """Make the sharper thumbnail of every photo and video under prefix that only has an old one
    (or none), newest first, the way the timeline shows them. progress(done, todo) is called as it
    goes. A photo that can't be read now is tried again next time. Returns how many were made."""
    from concurrent.futures import ThreadPoolExecutor

    rows = db.execute(
        "SELECT qhash, path FROM photos WHERE qhash IS NOT NULL AND error IS NULL "
        "AND kind IN ('photo', 'video') AND substr(path, 1, ?) = ? ORDER BY taken DESC",
        (len(prefix), prefix),
    ).fetchall()
    made_already = set(os.listdir(thumbs_dir("photos")))  # (one listing: much faster than a check per photo)
    seen, todo = set(), []
    for q, path in rows:
        if q not in seen and _thumb_name(q) not in made_already:
            seen.add(q)
            todo.append((q, path))
    if not todo:
        return 0

    def make(item):
        try:
            photo_thumb(*item)
            return True
        except Exception:
            return False

    made = done = 0
    report = progress or (lambda done, todo: None)
    report(0, len(todo))
    with ThreadPoolExecutor(workers, thread_name_prefix="sharpen") as pool:
        for start in range(0, len(todo), 24):  # a batch at a time, so stopping is quick
            if should_stop():
                break
            batch = todo[start : start + 24]
            made += sum(pool.map(make, batch))
            done += len(batch)
            report(done, len(todo))
    return made


def make_face_thumbs(db, prefix: str, should_stop=lambda: False, progress=None, workers: int = 2) -> int:
    """Make the face pictures not made yet (faces found before they were made while indexing, or
    a library copied without them) for the photos under prefix, newest first. Each photo is read
    once for all its faces, and only when its thumbnail isn't sharp enough for one of them.
    progress(done, todo) is called as it goes. Returns how many were made."""
    from concurrent.futures import ThreadPoolExecutor

    rows = db.execute(
        "SELECT f.x, f.y, f.w, f.h, p.path, p.qhash FROM faces f JOIN photos p ON p.id = f.photo_id "
        "WHERE f.hidden = 0 AND p.error IS NULL AND p.qhash IS NOT NULL AND substr(p.path, 1, ?) = ? "
        "ORDER BY p.taken DESC, p.id",
        (len(prefix), prefix),
    ).fetchall()
    made_already = set(os.listdir(thumbs_dir("faces")))  # (one listing: much faster than a check per face)
    by_photo = {}
    for r in rows:
        face = dict(zip(("x", "y", "w", "h", "path", "qhash"), r, strict=True))
        if face_thumb_file(face).name not in made_already:
            by_photo.setdefault(face["qhash"], []).append(face)
    todo = list(by_photo.values())
    if not todo:
        return 0

    def make(faces):
        """One photo's faces: its thumbnail and (only if one of them needs it) the photo, read once."""
        made, thumb, photo = 0, _thumbnail(faces[0]["qhash"]), None
        for face in faces:
            try:
                if not _sharp_enough(face, thumb, FACE_SIZE) and photo is None:
                    photo = load_photo(Path(face["path"]), 1600)
                face_thumb(face, FACE_SIZE, thumb if _sharp_enough(face, thumb, FACE_SIZE) else photo)
                made += 1
            except Exception:  # can't be read now (its drive is out): made when it's shown, or next time
                return made
        return made

    made = done = 0
    report = progress or (lambda done, todo: None)
    total = sum(len(f) for f in todo)
    report(0, total)
    with ThreadPoolExecutor(workers, thread_name_prefix="face-pictures") as pool:
        for start in range(0, len(todo), 24):  # a batch of photos at a time, so stopping is quick
            if should_stop():
                break
            batch = todo[start : start + 24]
            made += sum(pool.map(make, batch))
            done += sum(len(f) for f in batch)
            report(done, total)
    return made


def prune_thumbs(db):
    """Delete thumbnails of photos that are gone or have changed."""
    keep = {r[0] for r in db.execute("SELECT qhash FROM photos WHERE qhash IS NOT NULL")}
    for kind in ("photos", "faces"):
        for f in thumbs_dir(kind).glob("*.jpg"):
            if f.stem.split("-")[0] not in keep:
                f.unlink(missing_ok=True)
