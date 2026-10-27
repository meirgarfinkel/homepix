"""Reading photos: which files count, and decoding them. When they were taken is in dates.py."""

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
