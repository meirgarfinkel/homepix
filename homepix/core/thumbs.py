"""Thumbnails for photos and faces.

They are named by the photo's fingerprint (qhash), not its database id: ids get reused after
deletions, while an edited photo gets a new fingerprint and a moved one keeps it.
"""

import hashlib
import os
import threading
from pathlib import Path

from PIL import Image

from .config import THUMB_SIZE, data_dir
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


def face_thumb(row, size=128) -> Path:
    """A square around the face, kept inside the photo (moved, or made smaller, at its edges)."""
    box = hashlib.sha1(f"{row['x']},{row['y']},{row['w']},{row['h']}".encode()).hexdigest()[:10]
    out = thumbs_dir("faces") / f"{row['qhash']}-{box}-v2-{size}.jpg"
    if not out.exists():
        img = load_photo(Path(row["path"]), 1600)
        W, H = img.size
        cx, cy = (row["x"] + row["w"] / 2) * W, (row["y"] + row["h"] / 2) * H
        half = min(max(row["w"] * W, row["h"] * H) * 0.8, W / 2, H / 2)
        left = min(max(cx - half, 0), W - 2 * half)
        top = min(max(cy - half, 0), H - 2 * half)
        save_thumb(img.crop((int(left), int(top), int(left + 2 * half), int(top + 2 * half))), out, size, 85)
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


def prune_thumbs(db):
    """Delete thumbnails of photos that are gone or have changed."""
    keep = {r[0] for r in db.execute("SELECT qhash FROM photos WHERE qhash IS NOT NULL")}
    for kind in ("photos", "faces"):
        for f in thumbs_dir(kind).glob("*.jpg"):
            if f.stem.split("-")[0] not in keep:
                f.unlink(missing_ok=True)
