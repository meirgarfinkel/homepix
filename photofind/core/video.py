"""Videos (MP4, MOV): when they were recorded, how long they are, and a frame to show.

No AI looks at videos; they're browsed, opened in the system's player, and organized like photos.
The date comes from the file itself: Apple's creation date (local time, as recorded) when it's there,
else the movie header's creation time (UTC, turned into local time).
"""

import datetime as dt
import struct
from pathlib import Path

VIDEO_EXTS = {".mp4", ".mov", ".m4v"}
_EPOCH_1904 = dt.datetime(1904, 1, 1, tzinfo=dt.UTC)


def is_video(path) -> bool:
    return Path(path).suffix.lower() in VIDEO_EXTS


def _boxes(f, start: int, end: int):
    """(type, payload start, payload end) for each box between start and end."""
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        head = f.read(8)
        if len(head) < 8:
            return
        size, kind = struct.unpack(">I4s", head)
        body = pos + 8
        if size == 1:  # 64-bit size follows
            size = struct.unpack(">Q", f.read(8))[0]
            body += 8
        elif size == 0:  # to the end of the file
            size = end - pos
        if size < body - pos:
            return
        yield kind, body, min(pos + size, end)
        pos += size


def _find(f, start, end, kind):
    for k, a, b in _boxes(f, start, end):
        if k == kind:
            return a, b
    return None


def _apple_date(f, meta_start, meta_end):
    """com.apple.quicktime.creationdate from a QuickTime meta box: keys name the items in ilst."""
    keys = _find(f, meta_start, meta_end, b"keys")
    ilst = _find(f, meta_start, meta_end, b"ilst")
    if not keys or not ilst:
        return None
    f.seek(keys[0] + 4)
    names = {}
    count = struct.unpack(">I", f.read(4))[0]
    pos = keys[0] + 8
    for i in range(1, min(count, 200) + 1):
        f.seek(pos)
        size = struct.unpack(">I", f.read(4))[0]
        if size < 8:
            return None
        names[i] = f.read(size - 4)[4:]  # skip the namespace
        pos += size
    want = next((i for i, n in names.items() if n == b"com.apple.quicktime.creationdate"), None)
    if want is None:
        return None
    for kind, a, b in _boxes(f, *ilst):
        if struct.unpack(">I", kind)[0] == want:
            data = _find(f, a, b, b"data")
            if data:
                f.seek(data[0] + 8)  # type and locale
                return f.read(min(data[1] - data[0] - 8, 64)).decode("utf-8", "replace").strip()
    return None


def video_meta(path) -> dict:
    """{"taken": datetime (local, naive) or None, "duration": seconds or None}. Never raises on a bad
    file; it just knows less."""
    out = {"taken": None, "duration": None}
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            moov = _find(f, 0, f.tell(), b"moov")
            if not moov:
                return out
            mvhd = _find(f, *moov, b"mvhd")
            if mvhd:
                f.seek(mvhd[0])
                version = f.read(1)[0]
                f.read(3)
                if version == 1:
                    created, _, scale, length = struct.unpack(">QQIQ", f.read(28))
                else:
                    created, _, scale, length = struct.unpack(">IIII", f.read(16))
                if scale:
                    out["duration"] = length / scale
                if created > 86400 * 365 * 86:  # after 1990: 0 or tiny means "not set"
                    utc = _EPOCH_1904 + dt.timedelta(seconds=created)
                    out["taken"] = utc.astimezone().replace(tzinfo=None)
            meta = _find(f, *moov, b"meta")
            if meta:
                apple = _apple_date(f, *meta)
                if apple:
                    try:  # e.g. 2023-05-28T23:07:00+0300: the wall time where it was recorded
                        out["taken"] = dt.datetime.fromisoformat(apple[:19])
                    except ValueError:
                        pass
    except (OSError, struct.error, IndexError):
        pass
    return out


def video_frame(path, max_side: int):
    """A frame from a second in (or a tenth of the way, for short ones), as a PIL image, or None if
    the video can't be decoded."""
    import cv2
    from PIL import Image

    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            return None
        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        target = min(fps, frames / 10) if frames else 0
        if target >= 1:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(target))
        ok, frame = cap.read()
        if not ok and target:
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = cap.read()
        if not ok:
            return None
        img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        img.thumbnail((max_side, max_side))
        return img
    finally:
        cap.release()


def video_size(path):
    """(width, height) as shown, or None."""
    import cv2

    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            return None
        w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        rotation = int(cap.get(cv2.CAP_PROP_ORIENTATION_META) or 0)
        return (h, w) if rotation % 180 == 90 else (w, h)
    finally:
        cap.release()
