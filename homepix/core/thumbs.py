"""Thumbnails for photos and faces.

They are named by the photo's fingerprint (qhash), not its database id: ids get reused after
deletions, while an edited photo gets a new fingerprint and a moved one keeps it.
"""

import hashlib
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


def photo_thumb(qhash: str, path: str, img: Image.Image = None) -> Path:
    """img: the photo, if it's already decoded (saves opening it again). For a video, a frame of it."""
    out = thumbs_dir("photos") / f"{qhash}.jpg"
    if not out.exists():
        if img is None and is_video(path):
            img = video_frame(path, THUMB_SIZE * 2)
            if img is None:
                raise ValueError("this video can't be decoded")
        elif img is None:
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
