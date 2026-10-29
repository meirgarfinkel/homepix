"""Exporting: copies of photos for somewhere outside the library (to share, upload or print), either
the original files or JPEGs made smaller. The library itself isn't changed, so this isn't journaled."""

import os

from PIL import Image, ImageOps

from . import media  # noqa: F401  (opens HEIC photos too)
from .safefs import copy_file, free_name, rename_noreplace
from .video import VIDEO_EXTS


def export(paths, folder, resize: int | None = None, quality: int = 85, progress=None, should_stop=None):
    """Copy paths into folder: the originals, or (resize = the longest side in pixels) JPEGs of at most
    that size, keeping the date taken. Videos are always copied as they are. Names that are taken get
    _1, _2, ... Returns (done, [(path, why) not exported])."""
    os.makedirs(folder, exist_ok=True)
    done, failed, taken = 0, [], set()
    for i, src in enumerate(paths):
        if should_stop and should_stop():
            break
        name = os.path.basename(src)
        as_jpeg = resize and os.path.splitext(name)[1].lower() not in VIDEO_EXTS
        if as_jpeg:
            name = os.path.splitext(name)[0] + ".jpg"
        name = free_name(folder, name, taken)
        taken.add(name)
        dst = os.path.join(folder, name)
        partial = os.path.join(folder, f".homepix-export-{os.getpid()}-{i}")
        try:
            if as_jpeg:
                _smaller_jpeg(src, dst, partial, resize, quality)
            else:
                copy_file(src, dst, partial)
            done += 1
        except Exception as e:  # one photo that can't be read doesn't stop the rest
            failed.append((src, str(e)))
        if progress:
            progress(i + 1, len(paths))
    return done, failed


def _smaller_jpeg(src, dst, partial, longest: int, quality: int):
    with Image.open(src) as img:
        exif = img.getexif()
        img = ImageOps.exif_transpose(img).convert("RGB")
    if max(img.size) > longest:
        img.thumbnail((longest, longest), Image.LANCZOS)
    exif.pop(0x0112, None)  # turned upright already
    try:
        img.save(partial, "JPEG", quality=quality, exif=exif.tobytes(), optimize=True)
        st = os.stat(src)
        os.utime(partial, (st.st_atime, st.st_mtime))
        rename_noreplace(partial, dst)
    except BaseException:
        if os.path.exists(partial):
            os.unlink(partial)
        raise
