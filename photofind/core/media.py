"""Reading photos: which files count, decoding, and when they were taken."""

import datetime as dt
import re
from pathlib import Path

from PIL import Image, ImageFile, ImageOps

from .config import WORK_SIZE

try:  # optional: iPhone .heic photos
    from pillow_heif import register_heif_opener

    register_heif_opener()
    HEIF = True
except ImportError:
    HEIF = False

Image.MAX_IMAGE_PIXELS = 300_000_000  # allow big panoramas
ImageFile.LOAD_TRUNCATED_IMAGES = True  # a photo cut short (e.g. by an interrupted copy) still shows what's there

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif", ".dng"}  # DNG: its preview
if HEIF:
    IMAGE_EXTS |= {".heic", ".heif"}

# Photos that couldn't be read are tried again once the photo changes, or once we can read more than we
# could when it failed. Bump READER_VERSION whenever reading improves (a new format, a decoder fix).
READER_VERSION = 2
READER = f"{READER_VERSION}{'+heif' if HEIF else ''}"


def load_photo(path: Path, max_side: int = WORK_SIZE) -> Image.Image:
    img = Image.open(path)
    img.draft("RGB", (max_side, max_side))  # JPEGs decode straight to a smaller size: much faster
    img = ImageOps.exif_transpose(img)
    img = img.convert("RGB")
    if max(img.size) > max_side:
        img.thumbnail((max_side, max_side), Image.BILINEAR)
    return img


def date_taken(path: Path, fallback_mtime: float) -> tuple[str, str]:
    """When the photo was taken, and where that came from: 'exif', or 'file-modified' when the photo
    doesn't say (then the file's modified time is the best guess)."""
    try:
        with Image.open(path) as img:
            exif = img.getexif()
            value = exif.get_ifd(0x8769).get(36867) or exif.get(306)  # DateTimeOriginal, DateTime
        if value:
            taken = dt.datetime.strptime(str(value).strip()[:19], "%Y:%m:%d %H:%M:%S")
            return taken.isoformat(sep=" "), "exif"
    except Exception:
        pass
    return dt.datetime.fromtimestamp(fallback_mtime).isoformat(sep=" ", timespec="seconds"), "file-modified"


# Dates in file names, most precise first (from the Sep 2026 rename pipeline).
_NAME_DATES = [
    (re.compile(r"(19|20)(\d{2})-(\d{2})-(\d{2}) at (\d{2})[-.](\d{2})[-.](\d{2})"), True),  # macOS screenshots
    (re.compile(r"(19|20)(\d{2})[-_.]?(\d{2})[-_.]?(\d{2})[-_.\s]+(\d{2})[-_.:]?(\d{2})[-_.:]?(\d{2})"), True),
    (re.compile(r"(19|20)(\d{2})[-_.]?(\d{2})[-_.]?(\d{2})(?!\d)"), False),
]
PLACEHOLDER_DATES = {dt.datetime(1998, 2, 9, 6, 49)}  # the sRGB colour profile's own date


def is_placeholder(d: dt.datetime) -> bool:
    """A date tools stamp when they have none: never a real one."""
    return d in PLACEHOLDER_DATES or (d.month == 1 and d.day == 1 and d.time() == dt.time())


def filename_date(name: str):
    """The date (and time, if there is one) in a file's name, or None."""
    for pattern, has_time in _NAME_DATES:
        for m in pattern.finditer(name):
            g = [int(x) for x in m.groups()]
            try:
                d = dt.datetime(g[0] * 100 + g[1], g[2], g[3], *(g[4:7] if has_time else ()))
            except ValueError:
                continue
            if 1990 <= d.year <= dt.date.today().year + 1 and not is_placeholder(d):
                return d
    return None
